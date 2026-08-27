"""Regenerate ``assets/sample-test-set.csv`` from REAL public molecules spanning both regimes.

This is the reproducible provenance of the bundled demonstration prediction set. It is a
developer/maintainer utility, not one of the three registered Biomni tools. Run it with the
pinned runtime from an isolated environment (paths are resolved relative to this file, never via
``setwd``/``os.chdir``):

    python scripts/build_sample_test_set.py

The set is composed **by construction** to exercise both applicability-domain (AD) regimes with
REAL molecules, and the achieved coverage is **measured, never assumed and never tuned**:

1. **In-domain block** (``design_source=lipophilicity_holdout`` / ``design_regime=expected_in_domain``):
   ``N_IN`` real molecules randomly held out of the Lipophilicity_AstraZeneca training set. They
   share the training endpoint and chemical space and carry their real measured logD, but are
   removed from the deployment reference, so they are genuine near-domain molecules rather than
   verbatim reference members (nearest-neighbour Tanimoto high but < 1.0).
2. **Out-of-domain block** (``design_source=aqsoldb_external`` / ``design_regime=expected_out_of_domain``):
   ``N_OUT`` real molecules from a **distinct assay/database** (AqSolDB aqueous solubility),
   restricted to those whose molecular weight is **outside the training set's own drug-like MW
   window** (data-derived 5th-95th percentile). The MW window is a chemical-property criterion; it
   does **not** use the AD fingerprint metric, so the block is not gamed via the domain definition.
   These carry no logD (a different endpoint), so ``measured_logD`` is blank for them.

The AD threshold, the Morgan-r2 Tanimoto metric, and the molecule-selection rule are all fixed by
construction. The realised in-domain fraction is whatever ``predict_bundle`` measures against the
real deployment reference; where achieved diverges from expected (real small-molecule spaces
overlap, so some expected-OOD molecules land in-domain) the run **says so**. See
``references/example_data.md`` for the rationale, sources, licences, and the recorded numbers.

Data sources / licences (re-verify before redistributing; see ``fetch_benchmark.py``):
- Lipophilicity_AstraZeneca -- real logD7.4, AstraZeneca (2016) via MoleculeNet (Wu et al. 2018),
  obtained through TDC. TDC lists it as "Not Specified. CC BY 4.0".
- AqSolDB -- real aqueous solubility, Sorkun et al. (Sci. Data 2019), obtained through TDC. CC BY 4.0.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from adme_skill.artifacts import load_bundle  # noqa: E402
from adme_skill.schema import RunConfig  # noqa: E402
from adme_skill.workflow import predict_bundle, train_model  # noqa: E402
from fetch_benchmark import dataset_spec_for, fetch_benchmark  # noqa: E402

IN_DOMAIN_SOURCE = "lipophilicity_astrazeneca"  # real logD; same endpoint/space -> in-domain block
OOD_SOURCE = "solubility_aqsoldb"  # distinct assay/database -> out-of-domain pool
IN_ENDPOINT = "logD"
N_IN = 50
N_OUT = 50
# Fixed selection seed. Selection is NOT iterated against any AD outcome; this only makes the
# random holdout / OOD sample reproducible.
BUILD_SEED = 20240517


def _canonical(smiles: str) -> str | None:
    mol = Chem.MolFromSmiles(str(smiles))
    return Chem.MolToSmiles(mol) if mol is not None else None


def _mol_weights(smiles: list[str]) -> np.ndarray:
    weights = []
    for smi in smiles:
        mol = Chem.MolFromSmiles(str(smi))
        weights.append(float(Descriptors.MolWt(mol)) if mol is not None else np.nan)
    return np.asarray(weights, dtype=float)


def build_demo_set(
    out_path: Path | str | None = None,
    n_in: int = N_IN,
    n_out: int = N_OUT,
    train_cap: int | None = None,
    run_config: RunConfig | None = None,
    seed: int = BUILD_SEED,
    workdir: Path | str | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Fetch real data, compose the demo set, train the reference model, and MEASURE coverage.

    ``train_cap`` (used by the offline-skipping test) subsamples the Lipophilicity training set to
    keep a light run fast; ``None`` uses the full benchmark for the shipped asset. Returns a summary
    dict of the measured (not assumed) achieved fractions and headline metrics.
    """
    rng = np.random.default_rng(seed)
    config = run_config or RunConfig(
        split="scaffold", feature_sets=["ecfp", "desc2d"], inner_splits=3, n_bootstrap=200, seed=0
    )

    # 1) Real in-domain benchmark (Lipophilicity, real logD7.4). Keep provenance columns.
    lip_meta = fetch_benchmark(IN_DOMAIN_SOURCE)
    lip = (
        lip_meta["frame"]
        .dropna(subset=["smiles", IN_ENDPOINT])
        .drop_duplicates(subset=["smiles"])
        .reset_index(drop=True)
    )

    # 2) Hold out n_in real molecules for the in-domain block; they are removed from training so
    #    they are NOT in the deployment reference (same space, real logD, not verbatim members).
    holdout_idx = rng.choice(len(lip), size=n_in, replace=False)
    holdout_mask = np.zeros(len(lip), dtype=bool)
    holdout_mask[holdout_idx] = True
    in_block = lip.loc[holdout_mask, ["smiles", IN_ENDPOINT]].reset_index(drop=True)
    train_frame = lip.loc[~holdout_mask].reset_index(drop=True)
    if train_cap is not None and len(train_frame) > train_cap:
        keep = rng.choice(len(train_frame), size=train_cap, replace=False)
        train_frame = train_frame.iloc[np.sort(keep)].reset_index(drop=True)

    # 3) Data-derived drug-like MW window of the TRAINING molecules (p5-p95). Chemical-property
    #    criterion only; it does NOT touch the AD fingerprint metric.
    train_mw = _mol_weights(train_frame["smiles"].tolist())
    mw_lo, mw_hi = (float(x) for x in np.nanpercentile(train_mw, [5, 95]))

    # 4) OOD pool from a distinct assay/database (AqSolDB), restricted to molecules OUTSIDE the
    #    training MW window and not overlapping the Lipophilicity structures.
    aq = (
        fetch_benchmark(OOD_SOURCE)["frame"]
        .dropna(subset=["smiles"])
        .drop_duplicates(subset=["smiles"])
        .reset_index(drop=True)
    )
    aq["_mw"] = _mol_weights(aq["smiles"].tolist())
    aq["_canon"] = [_canonical(s) for s in aq["smiles"]]
    lip_canon = set(filter(None, (_canonical(s) for s in lip["smiles"])))
    outside = aq[
        aq["_mw"].notna()
        & ((aq["_mw"] < mw_lo) | (aq["_mw"] > mw_hi))
        & aq["_canon"].notna()
        & ~aq["_canon"].isin(lip_canon)
    ].reset_index(drop=True)
    if len(outside) < n_out:
        raise RuntimeError(
            f"only {len(outside)} eligible external OOD molecules (need {n_out}); "
            f"MW window was [{mw_lo:.1f}, {mw_hi:.1f}]"
        )
    take = np.sort(rng.choice(len(outside), size=n_out, replace=False))
    out_block = outside.iloc[take].reset_index(drop=True)

    # 5) Compose the demonstration set with design provenance (intended regime only).
    demo = pd.concat(
        [
            pd.DataFrame(
                {
                    "smiles": in_block["smiles"].to_numpy(),
                    "design_source": "lipophilicity_holdout",
                    "design_regime": "expected_in_domain",
                    "measured_logD": in_block[IN_ENDPOINT].round(3).to_numpy(),
                }
            ),
            pd.DataFrame(
                {
                    "smiles": out_block["smiles"].to_numpy(),
                    "design_source": "aqsoldb_external",
                    "design_regime": "expected_out_of_domain",
                    "measured_logD": np.nan,  # AqSolDB reports logS, not logD
                }
            ),
        ],
        ignore_index=True,
    )

    tmp_ctx = tempfile.TemporaryDirectory() if workdir is None else None
    work = Path(workdir) if workdir is not None else Path(tmp_ctx.name)
    work.mkdir(parents=True, exist_ok=True)
    try:
        # 6) Train the real reference model on the (held-out-excluded) Lipophilicity set.
        train_path = work / "lipophilicity_train.csv"
        train_frame.to_csv(train_path, index=False)
        result = train_model(dataset_spec_for(IN_DOMAIN_SOURCE, str(train_path)), config, str(work / "run"))
        if result["status"] != "completed":
            raise RuntimeError(f"training did not complete: {result}")
        bundle = load_bundle(result["model_bundle_path"])
        evaluation = json.loads(Path(result["evaluation_path"]).read_text())

        # Write the asset, then MEASURE achieved coverage via the real prediction path.
        target = Path(out_path) if out_path is not None else ROOT / "assets" / "sample-test-set.csv"
        demo.to_csv(target, index=False)
        scored = predict_bundle(result["model_bundle_path"], str(target), "smiles", str(work / "scored"))
        table = pd.read_csv(scored["predictions_path"])
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    # Measure only validly-scored molecules; invalid structures (e.g. inorganic AqSolDB salts) have
    # no similarity and must not be counted as in-domain. Recompute the flag from the similarity and
    # the bundle threshold rather than trusting a CSV bool round-trip.
    threshold = float(bundle.ad_threshold)
    per_block: dict[str, Any] = {}
    for regime in ("expected_in_domain", "expected_out_of_domain"):
        block = table[table["design_regime"] == regime]
        valid = block[block["nearest_neighbor_similarity"].notna()]
        sim = valid["nearest_neighbor_similarity"].astype(float)
        ind = sim >= threshold
        per_block[regime] = {
            "n": int(len(block)),
            "n_scored": int(len(valid)),
            "n_invalid_structure": int(len(block) - len(valid)),
            "achieved_in_domain": float(ind.mean()) if len(valid) else None,
            "n_in_domain": int(ind.sum()),
            "n_out_of_domain": int((~ind).sum()),
            "nn_sim_min": float(sim.min()) if len(valid) else None,
            "nn_sim_median": float(sim.median()) if len(valid) else None,
            "nn_sim_max": float(sim.max()) if len(valid) else None,
        }

    summary = {
        "out_path": str(target),
        "n_in": int(len(in_block)),
        "n_out": int(len(out_block)),
        "reference_n": int(len(bundle.reference_smiles)),
        "ad_threshold": float(bundle.ad_threshold),
        "ad_threshold_kind": bundle.ad_threshold_kind,
        "mw_window": [mw_lo, mw_hi],
        "overall_fraction_in_domain": scored.get("fraction_in_domain"),
        "domain_warning": scored.get("domain_warning"),
        "selected_candidate": result["selected_candidate"],
        "headline_metrics": evaluation["outer_assessment"]["metrics"],
        "error_monotonicity": evaluation["applicability_domain"]["error_monotonicity"],
        "per_block": per_block,
    }

    if verbose:
        print(
            f"\nreference model: {summary['selected_candidate']} on n={summary['reference_n']} "
            f"Lipophilicity molecules; AD threshold={summary['ad_threshold']:.3f} "
            f"({summary['ad_threshold_kind']})"
        )
        print(f"training MW window (p5-p95): [{mw_lo:.1f}, {mw_hi:.1f}]")
        print(
            "HEADLINE outer-test metrics (REAL logD): "
            + ", ".join(f"{k}={v:.3f}" for k, v in summary["headline_metrics"].items() if isinstance(v, (int, float)))
        )
        mono = summary["error_monotonicity"]
        print(
            f"AD error-monotonicity on this dataset: verdict={mono['verdict']} "
            f"(Spearman rho={mono.get('spearman_rho')}, n_strata={mono.get('n_strata')})"
        )
        for regime, stats in per_block.items():
            print(
                f"  {regime}: n={stats['n']} scored={stats['n_scored']} "
                f"(invalid_structure={stats['n_invalid_structure']}) "
                f"achieved_in_domain={stats['achieved_in_domain']:.3f} "
                f"NN-sim min/median/max={stats['nn_sim_min']:.3f}/{stats['nn_sim_median']:.3f}/"
                f"{stats['nn_sim_max']:.3f}"
            )
        print(
            f"OVERALL achieved in-domain fraction = {summary['overall_fraction_in_domain']:.3f} "
            f"(reference n={summary['reference_n']}, threshold={summary['ad_threshold']:.3f})"
        )
        if summary["domain_warning"]:
            print(f"domain_warning: {summary['domain_warning']}")
        print(f"wrote {len(demo)} rows -> {target}")
    return summary


def main() -> int:
    build_demo_set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
