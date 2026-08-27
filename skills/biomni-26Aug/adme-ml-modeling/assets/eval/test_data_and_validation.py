from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from adme_skill.data import aggregate_replicates, parse_interval, prepare_dataset
from adme_skill.modeling import (
    Tanimoto1NNRegressor,
    XGBAFTRegressor,
    _error_monotonicity,
    domain_summary,
    interval_concordance,
    reference_set_descriptor,
)
from adme_skill.schema import DatasetSpec, EndpointSpec, RunConfig
from adme_skill.validation import assign_outer_split
from make_example_data import make_dataset


class DataAndValidationTests(unittest.TestCase):
    def test_interval_parser_preserves_bounds(self) -> None:
        self.assertEqual(parse_interval(">3")[:3], (3.0, 3.0, np.inf))
        self.assertEqual(parse_interval("2", "<")[:3], (2.0, -np.inf, 2.0))
        self.assertEqual(parse_interval("1.5")[:3], (1.5, 1.5, 1.5))
        with self.assertRaises(ValueError):
            parse_interval(">3", "<")

    def test_mixed_assays_block_training_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mixed.csv"
            pd.DataFrame(
                {
                    "smiles": ["CCO", "CCN"] * 10,
                    "value": np.arange(20, dtype=float),
                    "assay": ["A"] * 10 + ["B"] * 10,
                    "unit": ["uM"] * 20,
                }
            ).to_csv(path, index=False)
            spec = DatasetSpec(
                data_path=str(path),
                endpoint=EndpointSpec(
                    label_column="value", task="regression", unit="uM", unit_column="unit"
                ),
                assay_context_columns=["assay"],
            )
            _, audit = prepare_dataset(spec)
            self.assertEqual(audit["status"], "blocked")
            self.assertIn("mixed_assay_context", {item["code"] for item in audit["blockers"]})

    def test_replicate_noise_uses_nonidentical_exact_replicates(self) -> None:
        frame = pd.DataFrame(
            {
                "identity_key": ["a", "a", "b"],
                "molecule_key": ["a", "a", "b"],
                "smiles": ["CCO", "CCO", "CCN"],
                "assay_signature": ["x", "x", "x"],
                "source_row": [0, 1, 2],
                "measurement_date": pd.to_datetime(["2024-01-01"] * 3, utc=True),
                "label": [1.0, 3.0, 2.0],
                "lower_bound": [1.0, 3.0, 2.0],
                "upper_bound": [1.0, 3.0, 2.0],
                "qualifier": ["exact", "exact", "exact"],
            }
        )
        aggregated, noise = aggregate_replicates(frame, "regression")
        self.assertAlmostEqual(aggregated.loc[aggregated["identity_key"] == "a", "label"].item(), 2.0)
        self.assertAlmostEqual(noise or 0.0, np.sqrt(2.0))

    def test_time_split_purges_future_retests(self) -> None:
        rows = []
        for index in range(30):
            rows.append(
                {
                    "source_row": index,
                    "identity_key": f"mol-{index}",
                "smiles": "CCO" if index % 2 else "CCN",
                "label": float(index),
                "measurement_date": pd.Timestamp("2024-01-01", tz="UTC")
                    + pd.to_timedelta(index, unit="D"),
                }
            )
        rows.append(
            {
                "source_row": 30,
                "identity_key": "mol-0",
                "smiles": "CCN",
                "label": 999.0,
                "measurement_date": pd.Timestamp("2024-02-15", tz="UTC"),
            }
        )
        frame = pd.DataFrame(rows)
        spec = DatasetSpec(
            data_path="unused.csv",
            date_column="date",
            endpoint=EndpointSpec(label_column="label", task="regression"),
        )
        assigned, metadata = assign_outer_split(frame, spec, RunConfig(split="time", test_fraction=0.2))
        self.assertEqual(assigned.loc[assigned["source_row"] == 30, "outer_split"].item(), "purged_retest")
        train = assigned[assigned["outer_split"] == "train"]
        test = assigned[assigned["outer_split"] == "test"]
        self.assertFalse(set(train["identity_key"]) & set(test["identity_key"]))
        self.assertGreater(metadata["n_purged_retests"], 0)

    def test_knn_is_binary_tanimoto_not_descriptor_sign_binarization(self) -> None:
        X = np.asarray([[1, 0, 0, 1], [0, 1, 1, 0]], dtype=float)
        model = Tanimoto1NNRegressor().fit(X, np.asarray([10.0, -2.0]))
        np.testing.assert_array_equal(model.predict(X), np.asarray([10.0, -2.0]))

    def test_xgboost_aft_consumes_censor_bounds(self) -> None:
        rng = np.random.default_rng(7)
        X = rng.normal(size=(100, 5))
        truth = np.exp(0.5 * X[:, 0] - 0.35 * X[:, 1] + rng.normal(scale=0.15, size=100))
        cutoff = float(np.quantile(truth, 0.75))
        lower = np.minimum(truth, cutoff)
        upper = np.where(truth > cutoff, np.inf, truth)
        model = XGBAFTRegressor(num_boost_round=80, seed=3).fit_interval(X, lower, upper)
        prediction = model.predict(X)
        self.assertGreater(interval_concordance(lower, upper, prediction), 0.7)

    def test_reference_set_descriptor_derives_count_from_array(self) -> None:
        outer = reference_set_descriptor(["CCO", "CCN", "c1ccccc1"], "outer_training")
        self.assertEqual(outer, {"scope": "outer_training", "n": 3, "label": "outer-training molecules only, n=3"})
        deploy = reference_set_descriptor(["CCO", "CCN", "c1ccccc1", "CCC"], "all_audited")
        self.assertEqual(deploy["n"], 4)
        self.assertEqual(deploy["label"], "all audited molecules (train+test), n=4")
        # An unknown scope must still derive its count from the array rather than fabricate one.
        self.assertEqual(reference_set_descriptor(["CCO"], "other")["n"], 1)

    def test_domain_summary_reference_matches_similarity_call(self) -> None:
        train = pd.DataFrame({"smiles": ["CCO", "CCN", "c1ccccc1", "CCCO", "c1ccncc1", "CC(=O)O"]})
        query = pd.DataFrame({"smiles": ["CCO", "c1ccccc1C"]})
        result, similarity, in_domain = domain_summary(train, query)
        # The recorded reference set is the outer-training frame passed to nearest_similarity.
        self.assertEqual(result["reference_set"]["scope"], "outer_training")
        self.assertEqual(result["reference_set"]["n"], len(train))
        self.assertIn(f"n={len(train)}", result["reference_set"]["label"])
        self.assertEqual(len(similarity), len(query))
        self.assertEqual(len(in_domain), len(query))

    def test_splito_deployment_split_records_mood_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_path = root / "train.csv"
            deployment_path = root / "deployment.csv"
            frame = make_dataset(n=70, seed=44)
            frame["assay"] = "A"
            frame["unit"] = "uM"
            frame.to_csv(data_path, index=False)
            make_dataset(n=12, seed=90)[["smiles"]].to_csv(deployment_path, index=False)
            spec = DatasetSpec(
                data_path=str(data_path),
                assay_context_columns=["assay"],
                endpoint=EndpointSpec(
                    label_column="logD", task="regression", unit="uM", unit_column="unit"
                ),
            )
            rows, audit = prepare_dataset(spec)
            self.assertEqual(audit["status"], "ready")
            assigned, metadata = assign_outer_split(
                rows,
                spec,
                RunConfig(
                    split="deployment",
                    deployment_path=str(deployment_path),
                    feature_sets=["ecfp"],
                    n_bootstrap=0,
                ),
            )
            self.assertTrue(metadata["mood_ranking"])
            train = set(assigned.loc[assigned["outer_split"] == "train", "identity_key"])
            test = set(assigned.loc[assigned["outer_split"] == "test", "identity_key"])
            self.assertFalse(train & test)

    def test_error_monotonicity_verdicts_from_strata(self) -> None:
        # Strata are ordered by ascending nearest-neighbour similarity. Error falling as
        # similarity rises -> the flag tracks error -> supported.
        supported = _error_monotonicity(np.array([0.9, 0.7, 0.5, 0.3]))
        self.assertEqual(supported["verdict"], "supported")
        self.assertLessEqual(supported["spearman_rho"], -0.5)
        self.assertEqual(supported["n_strata"], 4)
        # Error rising with similarity -> inverted.
        inverted = _error_monotonicity(np.array([0.3, 0.5, 0.7, 0.9]))
        self.assertEqual(inverted["verdict"], "inverted")
        self.assertGreaterEqual(inverted["spearman_rho"], 0.5)
        # No monotonic relationship (Spearman rho ~ 0) -> not_evidenced.
        not_evidenced = _error_monotonicity(np.array([0.5, 0.6, 0.4, 0.55]))
        self.assertEqual(not_evidenced["verdict"], "not_evidenced")
        self.assertLess(abs(not_evidenced["spearman_rho"]), 0.5)
        # Fewer than three finite strata -> cannot assess.
        insufficient = _error_monotonicity(np.array([0.5, np.nan]))
        self.assertEqual(insufficient["verdict"], "insufficient_data")
        self.assertIsNone(insufficient["spearman_rho"])

    def test_preflight_refuses_live_session_environment(self) -> None:
        import biomni_tools

        ack = biomni_tools.SESSION_OVERRIDE_ENV
        # A virtual-env marker inside the session workspace -> refuse (unsafe_environment).
        with mock.patch.dict(os.environ, {"VIRTUAL_ENV": "/workspace/.venv"}, clear=False):
            os.environ.pop("UV_PROJECT_ENVIRONMENT", None)
            os.environ.pop(ack, None)
            status = biomni_tools.dependency_status()
            self.assertEqual(status["status"], "unsafe_environment")
            self.assertIn("VIRTUAL_ENV", status["active_session_markers"])
        # Explicit acknowledgement overrides the guard.
        with mock.patch.dict(os.environ, {"VIRTUAL_ENV": "/workspace/.venv", ack: "1"}, clear=False):
            self.assertNotEqual(biomni_tools.dependency_status()["status"], "unsafe_environment")
        # A marker OUTSIDE the session workspace is a legitimate isolated env, not flagged.
        with mock.patch.dict(os.environ, {"VIRTUAL_ENV": "/opt/pinned-env"}, clear=False):
            os.environ.pop("UV_PROJECT_ENVIRONMENT", None)
            os.environ.pop(ack, None)
            self.assertNotEqual(biomni_tools.dependency_status()["status"], "unsafe_environment")
        # No markers at all -> normal presence check.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VIRTUAL_ENV", None)
            os.environ.pop("UV_PROJECT_ENVIRONMENT", None)
            os.environ.pop(ack, None)
            self.assertIn(biomni_tools.dependency_status()["status"], {"ready", "missing_dependencies"})


if __name__ == "__main__":
    unittest.main()
