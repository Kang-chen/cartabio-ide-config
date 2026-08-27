from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from .modeling import (
    _from_aft_prediction,
    nearest_similarity,
    transform_smiles,
    transformer_from_state,
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dependency_versions() -> dict[str, str]:
    names = [
        "chembl-structure-pipeline",
        "joblib",
        "mapie",
        "molfeat",
        "numpy",
        "pandas",
        "pydantic",
        "rdkit",
        "scikit-learn",
        "scipy",
        "splito",
        "xgboost",
    ]
    versions = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "missing"
    return versions


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def write_json(path: str | Path, value: Any) -> Path:
    target = Path(path)
    target.write_text(json.dumps(_json_safe(value), indent=2, sort_keys=True, allow_nan=False) + "\n")
    return target


@dataclass
class ModelBundle:
    bundle_version: str
    task: str
    scale: str
    candidate: dict[str, str]
    feature_state: dict[str, Any]
    point_model: Any
    conformal_model: Any | None
    reference_smiles: list[str]
    ad_threshold: float
    ad_threshold_kind: str
    confidence_level: float
    probability_threshold: float
    censored: bool
    metadata: dict[str, Any]

    def predict(self, standardized_smiles: list[str]) -> dict[str, Any]:
        transformer = transformer_from_state(self.feature_state)
        X = transform_smiles(transformer, standardized_smiles)
        result: dict[str, Any] = {}
        if self.censored:
            point = _from_aft_prediction(self.point_model.predict(X), self.scale)
            result["prediction"] = point
        elif self.task == "regression":
            if self.conformal_model is not None:
                point, intervals = self.conformal_model.predict_interval(X)
                result["prediction"] = point
                result["prediction_lower"] = intervals[:, 0, 0]
                result["prediction_upper"] = intervals[:, 1, 0]
            else:
                result["prediction"] = self.point_model.predict(X)
        else:
            probability = self.point_model.predict_proba(X)[:, 1]
            result["prediction_probability"] = probability
            result["prediction_label"] = (probability >= self.probability_threshold).astype(int)
            if self.conformal_model is not None:
                _, prediction_sets = self.conformal_model.predict_set(X)
                result["prediction_set_0"] = prediction_sets[:, 0, 0].astype(bool)
                result["prediction_set_1"] = prediction_sets[:, 1, 0].astype(bool)
        similarity = nearest_similarity(self.reference_smiles, standardized_smiles)
        result["nearest_neighbor_similarity"] = similarity
        result["in_applicability_domain"] = similarity >= self.ad_threshold
        return result


def save_bundle(path: str | Path, bundle: ModelBundle) -> Path:
    target = Path(path)
    joblib.dump(bundle, target, compress=3)
    return target


def load_bundle(path: str | Path) -> ModelBundle:
    value = joblib.load(path)
    if not isinstance(value, ModelBundle):
        raise TypeError("artifact is not an ADME ModelBundle")
    if value.bundle_version != "0.2":
        raise ValueError(f"unsupported model bundle version {value.bundle_version!r}")
    return value


def write_model_card(path: str | Path, evaluation: dict[str, Any], audit: dict[str, Any]) -> Path:
    metrics = evaluation["outer_assessment"]["metrics"]
    ci = evaluation["outer_assessment"].get("bootstrap_95_ci", {})
    inner = evaluation.get("inner_validation", {})
    inner_desc = inner.get("description", "inner validation on outer-training data only")
    lines = [
        "# ADME model assessment",
        "",
        f"- Endpoint task: `{evaluation['task']}`",
        f"- Locked outer split: `{evaluation['split']['strategy']}`",
        f"- Inner validation: {inner_desc}",
        f"- Selected only by inner validation: `{evaluation['selected_candidate']}`",
        f"- Training/test molecules: {evaluation['n_train']} / {evaluation['n_test']}",
        f"- Censored observations: {audit['summary']['n_censored']}",
        "",
        "## Locked outer assessment",
        "",
    ]
    for key, value in metrics.items():
        suffix = f" (95% grouped bootstrap CI {ci[key][0]:.3g}–{ci[key][1]:.3g})" if key in ci else ""
        if isinstance(value, float):
            lines.append(f"- {key}: {value:.4g}{suffix}")
        else:
            lines.append(f"- {key}: {value}{suffix}")
    uncertainty = evaluation["outer_assessment"].get("uncertainty")
    lines.extend(["", "## Reliability", ""])
    if uncertainty:
        lines.append(f"- Uncertainty: {uncertainty['method']}")
        if "empirical_coverage" in uncertainty:
            lines.append(
                f"- Empirical outer coverage: {uncertainty['empirical_coverage']:.3f} "
                f"at nominal {uncertainty['confidence_level']:.3f}"
            )
        if "mean_interval_width" in uncertainty:
            lines.append(f"- Mean interval width: {uncertainty['mean_interval_width']:.4g}")
            if uncertainty.get("interval_width_is_constant"):
                width = uncertainty["mean_interval_width"]
                lines.append(
                    f"- On the locked outer test set every interval has the same width "
                    f"({width:.4g}; half-width {width / 2:.4g}), because split-conformal "
                    f"absolute-residual scoring uses one calibration quantile for all molecules. "
                    f"Outer interval width therefore does not signal extrapolation; only the "
                    f"`in_applicability_domain` flag does."
                )
            else:
                lines.append(
                    f"- Interval width varies across the locked outer test set: "
                    f"{uncertainty.get('interval_width_min', float('nan')):.4g}–"
                    f"{uncertainty.get('interval_width_max', float('nan')):.4g} "
                    f"({uncertainty.get('interval_width_n_distinct', 'unknown')} distinct widths). "
                    f"Only the `in_applicability_domain` flag signals extrapolation."
                )
    else:
        lines.append("- No conformal interval/set is claimed for this censored-label model.")
    domain = evaluation["applicability_domain"]
    lines.extend(
        [
            f"- Test fraction in domain: {domain['fraction_in_domain']:.3f}",
            f"- Domain threshold: {domain['threshold']:.3f} ({domain['threshold_kind']})",
        ]
    )
    if "ad_threshold_basis" in evaluation:
        lines.append(f"- AD threshold basis: {evaluation['ad_threshold_basis']}")
    eval_ref = evaluation.get("ad_reference_set_evaluation")
    if eval_ref:
        lines.append(
            f"- AD reference set (locked-test evaluation): {eval_ref['label']} — the test "
            f"fraction-in-domain and threshold above are computed against this reference."
        )
    deploy_ref = evaluation.get("ad_reference_set_deployment")
    if deploy_ref:
        lines.append(
            f"- AD reference set (deployment bundle / `predictions.csv`): {deploy_ref['label']} "
            f"— new-molecule in/out-of-domain flags use this reference."
        )
    monotonicity = domain.get("error_monotonicity")
    if monotonicity:
        rho = monotonicity.get("spearman_rho")
        rho_str = f"{rho:.2f}" if isinstance(rho, (int, float)) else "n/a"
        lines.append(
            f"- Domain flag vs error on this dataset: **{monotonicity.get('verdict', 'unknown')}** "
            f"(Spearman rho={rho_str} across {monotonicity.get('n_strata', '?')} similarity strata). "
            f"{monotonicity.get('explanation', '')}"
        )
    deployment = evaluation.get("deployment_uncertainty")
    if deployment:
        lines.extend(
            [
                "",
                "## Deployment predictions",
                "",
                f"- Deployment estimator: `{deployment.get('estimator_class', 'unknown')}` "
                f"({deployment.get('aggregation', 'unknown')}).",
                f"- `predictions.csv` intervals come from a different estimator than the locked "
                f"outer assessment above, are per-molecule, and are not symmetric about the point "
                f"estimate. Do not carry a width statement from this card over to "
                f"`predictions.csv`.",
                f"- Branch taken: {deployment.get('branch', 'unknown')}.",
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation constraints",
            "",
            "- Treat the locked outer result—not inner CV or a random diagnostic—as the prospective estimate.",
            "- The model applies only to the declared endpoint units, transformation, and assay context.",
            "- Conformal guarantees weaken under temporal or chemical distribution shift.",
            "- Treat out-of-domain predictions as hypotheses requiring experimental confirmation.",
            "- Censored limits were modeled as intervals and were not treated as exact measurements.",
        ]
    )
    target = Path(path)
    target.write_text("\n".join(lines) + "\n")
    return target

