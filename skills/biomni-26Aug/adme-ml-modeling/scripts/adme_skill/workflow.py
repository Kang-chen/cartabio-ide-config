from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .artifacts import (
    ModelBundle,
    dependency_versions,
    load_bundle,
    save_bundle,
    sha256_file,
    write_json,
    write_model_card,
)
from .data import aggregate_replicates, prepare_dataset, read_table, standardize_structure
from .modeling import (
    domain_summary,
    feature_state,
    fit_deployment_models,
    fit_outer_assessment,
    grouped_bootstrap_ci,
    make_feature_transformer,
    reference_set_descriptor,
    select_candidate,
    transform_smiles,
)
from .schema import DatasetSpec, RunConfig
from .validation import assign_outer_split, resolve_split


def _as_dataset_spec(value: DatasetSpec | dict[str, Any]) -> DatasetSpec:
    return value if isinstance(value, DatasetSpec) else DatasetSpec.model_validate(value)


def _as_run_config(value: RunConfig | dict[str, Any] | None) -> RunConfig:
    if isinstance(value, RunConfig):
        return value
    return RunConfig.model_validate(value or {})


def _inner_validation_description(strategy: str, n_splits: int) -> dict[str, Any]:
    """Describe the inner CV scheme actually used for the given outer-split strategy.

    validation.py routes ``time`` to chronological ``TimeSeriesSplit`` and every other
    strategy to scaffold-grouped ``GroupKFold``.  The model card must state which one
    was used rather than restating a constant.
    """
    if strategy == "time":
        return {
            "method": "TimeSeriesSplit",
            "n_splits": n_splits,
            "description": (
                f"{n_splits}-fold chronological TimeSeriesSplit (forward-chaining folds "
                f"ordered by measurement date)"
            ),
        }
    return {
        "method": "GroupKFold",
        "n_splits": n_splits,
        "description": (
            f"{n_splits}-fold scaffold-grouped GroupKFold (Murcko scaffold groups, "
            f"fingerprint-cluster fallback when scaffolds are uninformative)"
        ),
    }


def inspect_dataset(
    dataset_spec: DatasetSpec | dict[str, Any], output_dir: str
) -> dict[str, Any]:
    spec = _as_dataset_spec(dataset_spec)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    rows, audit = prepare_dataset(spec, require_labels=True)
    audit.update(
        dataset_spec=spec.model_dump(mode="json"),
        input_sha256=sha256_file(spec.data_path),
        dependency_versions=dependency_versions(),
    )
    audit_path = write_json(target / "dataset_audit.json", audit)
    row_columns = [
        column
        for column in [
            "source_row",
            "raw_smiles",
            "smiles",
            "molecule_key",
            "assay_signature",
            "label",
            "lower_bound",
            "upper_bound",
            "qualifier",
            "measurement_date",
            "structure_messages",
        ]
        if column in rows.columns
    ]
    rows_path = target / "audited_rows.csv"
    rows[row_columns].to_csv(rows_path, index=False)
    return {
        "status": audit["status"],
        "audit_path": str(audit_path),
        "audited_rows_path": str(rows_path),
        "summary": audit["summary"],
        "blockers": audit["blockers"],
        "warnings": audit["warnings"],
    }


def _check_artifact_consistency(
    evaluation: dict[str, Any],
    prediction_table: pd.DataFrame,
    n_train: int,
    n_test: int,
    split_rows: int,
) -> list[str]:
    """Re-read what is about to ship and record mismatches before the model card is written.

    Interval checks are guarded on prediction_lower/prediction_upper being present: on the
    censored path uncertainty is None and those columns are absent. Only the n_rows checks
    raise; everything else is recorded as a consistency_warning.
    """
    warnings: list[str] = []
    uncertainty = evaluation.get("outer_assessment", {}).get("uncertainty")
    has_intervals = "prediction_lower" in prediction_table.columns and "prediction_upper" in prediction_table.columns

    if has_intervals and uncertainty is not None:
        width = prediction_table["prediction_upper"].to_numpy(float) - prediction_table["prediction_lower"].to_numpy(float)
        mean_width = float(np.mean(width))
        if "mean_interval_width" in uncertainty:
            if abs(uncertainty["mean_interval_width"] - mean_width) > 1e-9:
                warnings.append(
                    f"uncertainty.mean_interval_width ({uncertainty['mean_interval_width']}) "
                    f"!= mean(prediction_table upper-lower) ({mean_width})"
                )
        rounded = np.round(width, 9)
        n_distinct = int(np.unique(rounded).size)
        is_constant = uncertainty.get("interval_width_is_constant", False)
        if is_constant and n_distinct != 1:
            warnings.append(
                f"uncertainty.interval_width_is_constant is True but prediction_table has "
                f"{n_distinct} distinct widths (rounded 9dp)"
            )
        if not is_constant and n_distinct == 1:
            warnings.append(
                "uncertainty.interval_width_is_constant is False but prediction_table has "
                "exactly one width (rounded 9dp)"
            )

    domain = evaluation.get("applicability_domain", {})
    if "in_applicability_domain" in prediction_table.columns and "fraction_in_domain" in domain:
        table_fraction = float(prediction_table["in_applicability_domain"].astype(bool).mean())
        if abs(domain["fraction_in_domain"] - table_fraction) > 1e-9:
            warnings.append(
                f"applicability_domain.fraction_in_domain ({domain['fraction_in_domain']}) "
                f"!= mean(prediction_table.in_applicability_domain) ({table_fraction})"
            )

    if n_test != len(prediction_table):
        raise ValueError(
            f"n_test ({n_test}) != len(prediction_table) ({len(prediction_table)})"
        )
    if n_train + n_test != split_rows:
        raise ValueError(
            f"n_train + n_test ({n_train + n_test}) != split rows ({split_rows})"
        )
    return warnings


def train_model(
    dataset_spec: DatasetSpec | dict[str, Any],
    run_config: RunConfig | dict[str, Any] | None,
    output_dir: str,
) -> dict[str, Any]:
    spec = _as_dataset_spec(dataset_spec)
    config = _as_run_config(run_config)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    rows, audit = prepare_dataset(spec, require_labels=True)
    audit.update(
        dataset_spec=spec.model_dump(mode="json"),
        input_sha256=sha256_file(spec.data_path),
        dependency_versions=dependency_versions(),
    )
    write_json(target / "dataset_audit.json", audit)
    if audit["status"] != "ready":
        return {
            "status": "blocked",
            "message": "Dataset audit must be resolved before training.",
            "audit_path": str(target / "dataset_audit.json"),
            "blockers": audit["blockers"],
        }

    assigned, split_metadata = assign_outer_split(rows, spec, config)
    train_rows = assigned[assigned["outer_split"] == "train"].copy()
    test_rows = assigned[assigned["outer_split"] == "test"].copy()
    train, replicate_noise = aggregate_replicates(train_rows, spec.endpoint.task)
    test, _ = aggregate_replicates(test_rows, spec.endpoint.task)
    all_data, all_noise = aggregate_replicates(rows, spec.endpoint.task)
    strategy = resolve_split(spec, config)
    if strategy == "time":
        train = train.sort_values("measurement_date").reset_index(drop=True)
        test = test.sort_values("measurement_date").reset_index(drop=True)
    else:
        train = train.reset_index(drop=True)
        test = test.reset_index(drop=True)

    transformers = {name: make_feature_transformer(name) for name in config.feature_sets}
    train_features = {
        name: transform_smiles(transformer, train["smiles"].tolist())
        for name, transformer in transformers.items()
    }
    test_features = {
        name: transform_smiles(transformer, test["smiles"].tolist())
        for name, transformer in transformers.items()
    }
    all_features = {
        name: transform_smiles(transformer, all_data["smiles"].tolist())
        for name, transformer in transformers.items()
    }

    candidate, comparison, oof_prediction = select_candidate(
        train,
        spec.endpoint.task,
        spec.endpoint.scale,
        strategy,
        config,
        train_features,
    )
    outer = fit_outer_assessment(
        train,
        test,
        candidate,
        spec.endpoint.task,
        spec.endpoint.scale,
        strategy,
        config,
        train_features[candidate.features],
        test_features[candidate.features],
    )
    domain, similarity, in_domain = domain_summary(
        train, test, oof_prediction, query_label="outer_test", reference_scope="outer_training"
    )
    bootstrap = grouped_bootstrap_ci(
        test,
        outer["prediction"],
        spec.endpoint.task,
        spec.endpoint.scale,
        config.probability_threshold,
        config.n_bootstrap,
        config.seed,
    )
    point_model, conformal_model, deployment_uncertainty = fit_deployment_models(
        all_data,
        candidate,
        spec.endpoint.task,
        spec.endpoint.scale,
        config,
        all_features[candidate.features],
    )

    prediction_table = pd.DataFrame(
        {
            "identity_key": test["identity_key"],
            "smiles": test["smiles"],
            "observed": test["label"],
            "qualifier": test["qualifier"],
            "lower_bound": test["lower_bound"],
            "upper_bound": test["upper_bound"],
            "prediction": outer["prediction"],
            "nearest_neighbor_similarity": similarity,
            "in_applicability_domain": in_domain,
        }
    )
    if "prediction_lower" in outer:
        prediction_table["prediction_lower"] = outer["prediction_lower"]
        prediction_table["prediction_upper"] = outer["prediction_upper"]
    if "prediction_sets" in outer:
        prediction_table["prediction_set_0"] = outer["prediction_sets"][:, 0]
        prediction_table["prediction_set_1"] = outer["prediction_sets"][:, 1]
        prediction_table["predicted_class"] = (
            prediction_table["prediction"] >= config.probability_threshold
        ).astype(int)
    prediction_path = target / "outer_test_predictions.csv"
    prediction_table.to_csv(prediction_path, index=False)

    splits_path = target / "split_assignments.csv"
    assigned[
        ["source_row", "identity_key", "smiles", "measurement_date", "outer_split"]
    ].to_csv(splits_path, index=False)
    comparison_path = target / "model_comparison.csv"
    pd.DataFrame(comparison).sort_values("candidate").to_csv(comparison_path, index=False)

    inner_validation = _inner_validation_description(strategy, config.inner_splits)
    # The single list used as the deployment-bundle AD reference; reused verbatim below so the
    # recorded deployment descriptor and the bundle's reference set can never diverge.
    deployment_reference_smiles = all_data["smiles"].tolist()
    evaluation = {
        "task": spec.endpoint.task,
        "scale": spec.endpoint.scale,
        "selected_candidate": candidate.key,
        "selection_scope": "inner validation on outer-training data only",
        "inner_validation": inner_validation,
        "n_train": len(train),
        "n_test": len(test),
        "replicate_noise_std_train": replicate_noise,
        "replicate_noise_std_all": all_noise,
        "split": split_metadata,
        "outer_assessment": {
            "metrics": outer["metrics"],
            "bootstrap_95_ci": bootstrap,
            "uncertainty": outer.get("uncertainty"),
            "warning": outer.get("warning"),
        },
        "applicability_domain": domain,
        "deployment_uncertainty": deployment_uncertainty,
        "ad_threshold_basis": "outer-training leave-one-out 5th percentile",
        "ad_reference_set_evaluation": domain["reference_set"],
        "ad_reference_set_deployment": reference_set_descriptor(
            deployment_reference_smiles, "all_audited"
        ),
    }
    evaluation_path = write_json(target / "evaluation.json", evaluation)

    bundle = ModelBundle(
        bundle_version="0.2",
        task=spec.endpoint.task,
        scale=spec.endpoint.scale,
        candidate={"model": candidate.model, "features": candidate.features},
        feature_state=feature_state(transformers[candidate.features]),
        point_model=point_model,
        conformal_model=conformal_model,
        reference_smiles=deployment_reference_smiles,
        ad_threshold=float(domain["threshold"]),
        ad_threshold_kind=domain["threshold_kind"],
        confidence_level=config.confidence_level,
        probability_threshold=config.probability_threshold,
        censored=bool((all_data["qualifier"] != "exact").any()),
        metadata={
            "dataset_spec": spec.model_dump(mode="json"),
            "run_config": config.model_dump(mode="json"),
            "input_sha256": sha256_file(spec.data_path),
            "dependency_versions": dependency_versions(),
            "evaluation": evaluation,
        },
    )
    bundle_path = save_bundle(target / "model_bundle.joblib", bundle)
    consistency_warnings = _check_artifact_consistency(
        evaluation,
        prediction_table,
        n_train=len(train),
        n_test=len(test),
        split_rows=len(assigned[assigned["outer_split"].isin(["train", "test"])]),
    )
    if consistency_warnings:
        evaluation["consistency_warnings"] = consistency_warnings
        evaluation_path = write_json(target / "evaluation.json", evaluation)
    card_path = write_model_card(target / "model_card.md", evaluation, audit)

    artifact_paths = [
        target / "dataset_audit.json",
        splits_path,
        comparison_path,
        evaluation_path,
        prediction_path,
        bundle_path,
        card_path,
    ]
    manifest = {
        "schema_version": "0.2",
        "input": {"path": str(Path(spec.data_path).resolve()), "sha256": sha256_file(spec.data_path)},
        "dataset_spec": spec.model_dump(mode="json"),
        "run_config": config.model_dump(mode="json"),
        "dependencies": dependency_versions(),
        "artifacts": {
            path.name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in artifact_paths
        },
    }
    manifest_path = write_json(target / "run_manifest.json", manifest)
    return {
        "status": "completed",
        "selected_candidate": candidate.key,
        "outer_metrics": outer["metrics"],
        "audit_path": str(target / "dataset_audit.json"),
        "evaluation_path": str(evaluation_path),
        "predictions_path": str(prediction_path),
        "model_bundle_path": str(bundle_path),
        "model_card_path": str(card_path),
        "manifest_path": str(manifest_path),
        "consistency_warnings": consistency_warnings,
    }


def predict_bundle(
    model_bundle_path: str,
    data_path: str,
    smiles_column: str,
    output_dir: str,
) -> dict[str, Any]:
    bundle = load_bundle(model_bundle_path)
    raw = read_table(data_path).copy()
    if smiles_column not in raw.columns:
        raise ValueError(f"SMILES column {smiles_column!r} is missing")
    raw.insert(0, "source_row", np.arange(len(raw), dtype=int))
    standardized = [standardize_structure(value) for value in raw[smiles_column]]
    raw["standardized_smiles"] = [item[0] for item in standardized]
    raw["prediction_status"] = ["ready" if item[0] else "invalid_structure" for item in standardized]
    valid = raw["standardized_smiles"].notna()
    if valid.any():
        results = bundle.predict(raw.loc[valid, "standardized_smiles"].tolist())
        for key, values in results.items():
            raw.loc[valid, key] = np.asarray(values)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    prediction_path = target / "predictions.csv"
    raw.to_csv(prediction_path, index=False)

    manifest: dict[str, Any] = {
        "schema_version": "0.2",
        "model_bundle": {
            "path": str(Path(model_bundle_path).resolve()),
            "sha256": sha256_file(model_bundle_path),
        },
        "input": {"path": str(Path(data_path).resolve()), "sha256": sha256_file(data_path)},
        "n_rows": len(raw),
        "n_predicted": int(valid.sum()),
        "n_invalid": int((~valid).sum()),
    }

    fraction_in_domain: float | None = None
    n_out_of_domain: int | None = None
    domain_warning: str | None = None
    if "in_applicability_domain" in raw.columns and valid.any():
        scored = raw.loc[valid]
        in_domain = scored["in_applicability_domain"].astype(bool)
        n_in_domain = int(in_domain.sum())
        n_out_of_domain = int((~in_domain).sum())
        fraction_in_domain = float(in_domain.mean())
        nn_sim = scored["nearest_neighbor_similarity"].to_numpy(float)
        manifest["applicability_domain"] = {
            "n_in_domain": n_in_domain,
            "n_out_of_domain": n_out_of_domain,
            "fraction_in_domain": fraction_in_domain,
            "threshold": bundle.ad_threshold,
            "threshold_kind": bundle.ad_threshold_kind,
            "nn_similarity_min": float(np.min(nn_sim)),
            "nn_similarity_median": float(np.median(nn_sim)),
            "nn_similarity_max": float(np.max(nn_sim)),
        }
        if fraction_in_domain < 0.5:
            domain_warning = (
                f"{n_out_of_domain} of {len(scored)} scored molecules fall outside the "
                f"applicability domain (NN Tanimoto < {bundle.ad_threshold:.3g}); "
                f"these are extrapolations, not validated predictions."
            )
            manifest["domain_warning"] = domain_warning

    if "prediction_lower" in raw.columns and "prediction_upper" in raw.columns and valid.any():
        scored = raw.loc[valid]
        lower = scored["prediction_lower"].to_numpy(float)
        upper = scored["prediction_upper"].to_numpy(float)
        point = scored["prediction"].to_numpy(float)
        width = upper - lower
        asym = (upper - point) - (point - lower)
        manifest["uncertainty"] = {
            "width_min": float(np.min(width)),
            "width_max": float(np.max(width)),
            "width_mean": float(np.mean(width)),
            "n_distinct_widths": int(np.unique(np.round(width, 9)).size),
            "max_asymmetry": float(np.max(np.abs(asym))),
        }

    manifest_path = write_json(target / "prediction_manifest.json", manifest)
    result: dict[str, Any] = {
        "status": "completed",
        "predictions_path": str(prediction_path),
        "manifest_path": str(manifest_path),
        "n_predicted": int(valid.sum()),
        "n_invalid": int((~valid).sum()),
    }
    if fraction_in_domain is not None:
        result["fraction_in_domain"] = fraction_in_domain
    if n_out_of_domain is not None:
        result["n_out_of_domain"] = n_out_of_domain
    if domain_warning is not None:
        result["domain_warning"] = domain_warning
    return result

