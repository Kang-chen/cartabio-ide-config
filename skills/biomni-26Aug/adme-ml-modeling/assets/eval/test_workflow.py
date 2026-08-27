from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from adme_skill.artifacts import load_bundle
from adme_skill.data import aggregate_replicates, prepare_dataset
from adme_skill.modeling import make_feature_transformer, select_candidate, transform_smiles
from adme_skill.schema import DatasetSpec, EndpointSpec, RunConfig
from adme_skill.validation import assign_outer_split
from adme_skill.workflow import inspect_dataset, predict_bundle, train_model
from make_example_data import make_dataset


def _write_regression_data(path: Path, n: int = 90) -> pd.DataFrame:
    frame = make_dataset(n=n, seed=21, noise=0.25).sort_values("date").reset_index(drop=True)
    frame["assay"] = "Caco2-A"
    frame["unit"] = "log10 cm/s"
    frame.to_csv(path, index=False)
    return frame


def _regression_spec(path: Path) -> DatasetSpec:
    return DatasetSpec(
        data_path=str(path),
        smiles_column="smiles",
        date_column="date",
        assay_context_columns=["assay"],
        endpoint=EndpointSpec(
            label_column="logD",
            task="regression",
            scale="log10",
            unit="log10 cm/s",
            unit_column="unit",
        ),
    )


class WorkflowTests(unittest.TestCase):
    def test_locked_test_labels_do_not_change_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.csv"
            _write_regression_data(path)
            spec = _regression_spec(path)
            config = RunConfig(
                split="time",
                feature_sets=["ecfp"],
                models=["dummy", "knn", "ridge"],
                inner_splits=3,
                n_bootstrap=0,
            )
            rows, audit = prepare_dataset(spec)
            self.assertEqual(audit["status"], "ready")
            assigned, _ = assign_outer_split(rows, spec, config)
            train, _ = aggregate_replicates(assigned[assigned["outer_split"] == "train"], "regression")
            train = train.sort_values("measurement_date").reset_index(drop=True)
            transformer = make_feature_transformer("ecfp")
            X = transform_smiles(transformer, train["smiles"].tolist())
            first, _, _ = select_candidate(train, "regression", "log10", "time", config, {"ecfp": X})

            assigned.loc[assigned["outer_split"] == "test", "label"] = np.linspace(1e4, 2e4, (assigned["outer_split"] == "test").sum())
            train_again, _ = aggregate_replicates(
                assigned[assigned["outer_split"] == "train"], "regression"
            )
            train_again = train_again.sort_values("measurement_date").reset_index(drop=True)
            second, _, _ = select_candidate(
                train_again, "regression", "log10", "time", config, {"ecfp": X}
            )
            self.assertEqual(first, second)

    def test_regression_end_to_end_and_bundle_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data.csv"
            prediction_input = root / "new.csv"
            frame = _write_regression_data(data, n=95)
            frame.iloc[:8][["smiles"]].to_csv(prediction_input, index=False)
            result = train_model(
                _regression_spec(data),
                RunConfig(
                    split="time",
                    feature_sets=["ecfp"],
                    models=["dummy", "knn", "ridge"],
                    inner_splits=3,
                    n_bootstrap=30,
                    seed=4,
                ),
                str(root / "run"),
            )
            self.assertEqual(result["status"], "completed", result)
            bundle = load_bundle(result["model_bundle_path"])
            first = bundle.predict(frame.iloc[:5]["smiles"].tolist())["prediction"]
            reloaded = load_bundle(result["model_bundle_path"])
            second = reloaded.predict(frame.iloc[:5]["smiles"].tolist())["prediction"]
            np.testing.assert_allclose(first, second, rtol=0, atol=1e-12)
            prediction_result = predict_bundle(
                result["model_bundle_path"], str(prediction_input), "smiles", str(root / "scored")
            )
            self.assertEqual(prediction_result["status"], "completed")
            scored = pd.read_csv(prediction_result["predictions_path"])
            self.assertIn("nearest_neighbor_similarity", scored.columns)
            self.assertIn("in_applicability_domain", scored.columns)
            manifest = json.loads(Path(result["manifest_path"]).read_text())
            self.assertIn("model_bundle.joblib", manifest["artifacts"])

    def test_classification_end_to_end_with_prediction_sets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "classification.csv"
            frame = make_dataset(n=120, seed=31, noise=0.3).sort_values("date").reset_index(drop=True)
            frame["active"] = np.where(frame["logD"] >= frame["logD"].median(), "yes", "no")
            frame["assay"] = "PAMPA"
            frame["unit"] = "class"
            frame.to_csv(data, index=False)
            spec = DatasetSpec(
                data_path=str(data),
                date_column="date",
                assay_context_columns=["assay"],
                endpoint=EndpointSpec(
                    label_column="active",
                    task="classification",
                    unit="class",
                    unit_column="unit",
                    class_mapping={"no": 0, "yes": 1},
                ),
            )
            result = train_model(
                spec,
                RunConfig(
                    split="time",
                    feature_sets=["ecfp"],
                    models=["dummy", "knn", "logistic"],
                    inner_splits=3,
                    n_bootstrap=20,
                    seed=9,
                ),
                str(root / "run"),
            )
            self.assertEqual(result["status"], "completed", result)
            output = pd.read_csv(result["predictions_path"])
            self.assertIn("prediction_set_0", output.columns)
            self.assertIn("prediction_set_1", output.columns)
            evaluation = json.loads(Path(result["evaluation_path"]).read_text())
            self.assertEqual(
                evaluation["selection_scope"], "inner validation on outer-training data only"
            )

    def test_ad_reference_sets_are_derived_and_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data.csv"
            _write_regression_data(data, n=120)
            result = train_model(
                _regression_spec(data),
                RunConfig(
                    split="time",
                    feature_sets=["ecfp"],
                    models=["dummy", "knn", "ridge"],
                    inner_splits=3,
                    n_bootstrap=0,
                    seed=3,
                ),
                str(root / "run"),
            )
            self.assertEqual(result["status"], "completed", result)
            evaluation = json.loads(Path(result["evaluation_path"]).read_text())
            bundle = load_bundle(result["model_bundle_path"])
            # The single hand-written field is gone; two derived descriptors replace it.
            self.assertNotIn("ad_reference_set", evaluation)
            eval_ref = evaluation["ad_reference_set_evaluation"]
            deploy_ref = evaluation["ad_reference_set_deployment"]
            # Evaluation reference = outer-training only, count derived from that array.
            self.assertEqual(eval_ref["scope"], "outer_training")
            self.assertEqual(eval_ref["n"], evaluation["n_train"])
            # Deployment reference = all audited, count derived from the bundle's own array.
            self.assertEqual(deploy_ref["scope"], "all_audited")
            self.assertEqual(deploy_ref["n"], evaluation["n_train"] + evaluation["n_test"])
            self.assertEqual(deploy_ref["n"], len(bundle.reference_smiles))
            self.assertNotEqual(eval_ref["n"], deploy_ref["n"])
            card = Path(result["model_card_path"]).read_text()
            self.assertIn(eval_ref["label"], card)
            self.assertIn(deploy_ref["label"], card)

    def test_fetch_benchmark_returns_real_labels(self) -> None:
        import urllib.error

        from fetch_benchmark import dataset_spec_for, fetch_benchmark

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "lip.csv"
            try:
                meta = fetch_benchmark("lipophilicity_astrazeneca", str(out))
            except (urllib.error.URLError, TimeoutError) as exc:
                self.skipTest(f"benchmark fetch unavailable offline: {exc}")
            frame = pd.read_csv(out)
            # Real single-endpoint measured values, not synthetic.
            self.assertEqual(list(frame.columns), ["compound_id", "smiles", "logD", "assay", "unit"])
            self.assertEqual(meta["license"], "CC BY 4.0")
            self.assertGreater(len(frame), 4000)
            # logD is a real log-ratio: it spans negative and positive values.
            self.assertLess(float(frame["logD"].min()), 0.0)
            self.assertGreater(float(frame["logD"].max()), 0.0)
            # Framed as a linear regression with a scaffold split (no date column).
            spec = dataset_spec_for("lipophilicity_astrazeneca", str(out))
            self.assertEqual(spec["endpoint"]["task"], "regression")
            self.assertEqual(spec["endpoint"]["scale"], "linear")
            self.assertNotIn("date_column", spec)

    def test_demo_set_spans_both_regimes_real_data(self) -> None:
        import urllib.error

        from build_sample_test_set import build_demo_set

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "demo.csv"
            light = RunConfig(
                split="scaffold",
                feature_sets=["ecfp"],
                models=["dummy", "knn", "ridge"],
                inner_splits=3,
                n_bootstrap=0,
                seed=0,
            )
            try:
                summary = build_demo_set(
                    out_path=out,
                    n_in=12,
                    n_out=12,
                    train_cap=600,
                    run_config=light,
                    workdir=root / "work",
                    verbose=False,
                )
            except (urllib.error.URLError, TimeoutError) as exc:
                self.skipTest(f"benchmark fetch unavailable offline: {exc}")
            table = pd.read_csv(out)
            # Real molecules carrying design provenance from two distinct sources.
            self.assertEqual(
                sorted(table["design_source"].unique().tolist()),
                ["aqsoldb_external", "lipophilicity_holdout"],
            )
            self.assertEqual(
                sorted(table["design_regime"].unique().tolist()),
                ["expected_in_domain", "expected_out_of_domain"],
            )
            in_block = table[table["design_regime"] == "expected_in_domain"]
            out_block = table[table["design_regime"] == "expected_out_of_domain"]
            # Real measured logD flows through for the in-domain block; the OOD block is a
            # different endpoint and carries none.
            self.assertTrue(in_block["measured_logD"].notna().all())
            self.assertTrue(out_block["measured_logD"].isna().all())
            # Both regimes are actually exercised -- measured against the real reference, not assumed.
            self.assertGreaterEqual(summary["per_block"]["expected_in_domain"]["n_in_domain"], 1)
            self.assertGreaterEqual(summary["per_block"]["expected_out_of_domain"]["n_out_of_domain"], 1)
            self.assertGreater(summary["overall_fraction_in_domain"], 0.0)
            self.assertLess(summary["overall_fraction_in_domain"], 1.0)
            # In-domain holdout molecules sit closer to the training space than external OOD ones.
            self.assertGreater(
                summary["per_block"]["expected_in_domain"]["nn_sim_median"],
                summary["per_block"]["expected_out_of_domain"]["nn_sim_median"],
            )
            # The AD error-monotonicity verdict is computed and surfaced on the real dataset.
            self.assertIn(
                summary["error_monotonicity"]["verdict"],
                {"supported", "not_evidenced", "inverted", "insufficient_data"},
            )

    def test_inspection_returns_blockers_without_training(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "bad.csv"
            pd.DataFrame({"smiles": ["CCO"] * 20, "label": ["maybe"] * 20}).to_csv(
                data, index=False
            )
            result = inspect_dataset(
                DatasetSpec(
                    data_path=str(data),
                    endpoint=EndpointSpec(label_column="label", task="classification"),
                ),
                str(root / "audit"),
            )
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(Path(result["audit_path"]).exists())

    def test_censored_regression_end_to_end_uses_only_aft_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "censored.csv"
            frame = make_dataset(n=95, seed=52, noise=0.25).sort_values("date").reset_index(drop=True)
            low, high = frame["logD"].quantile([0.15, 0.8])
            frame["observed"] = [
                f"<{low:.4f}" if value < low else (f">{high:.4f}" if value > high else f"{value:.4f}")
                for value in frame["logD"]
            ]
            frame["assay"] = "solubility"
            frame["unit"] = "log10 uM"
            frame.to_csv(data, index=False)
            spec = DatasetSpec(
                data_path=str(data),
                date_column="date",
                assay_context_columns=["assay"],
                endpoint=EndpointSpec(
                    label_column="observed",
                    task="regression",
                    scale="log10",
                    unit="log10 uM",
                    unit_column="unit",
                ),
            )
            result = train_model(
                spec,
                RunConfig(
                    split="time",
                    feature_sets=["ecfp"],
                    models=["aft_constant", "aft_xgb"],
                    inner_splits=3,
                    n_bootstrap=20,
                    seed=12,
                ),
                str(root / "run"),
            )
            self.assertEqual(result["status"], "completed", result)
            self.assertTrue(result["selected_candidate"].startswith("aft_"))
            evaluation = json.loads(Path(result["evaluation_path"]).read_text())
            self.assertIn("interval_c_index", evaluation["outer_assessment"]["metrics"])
            self.assertIsNone(evaluation["outer_assessment"]["uncertainty"])


if __name__ == "__main__":
    unittest.main()
