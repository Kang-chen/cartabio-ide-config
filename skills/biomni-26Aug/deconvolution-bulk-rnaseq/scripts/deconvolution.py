#!/usr/bin/env python3
"""deconvolution.py -- Python entry point for the bulk RNA-seq deconvolution skill.

Thin subprocess wrapper around ``scripts/run_full_workflow.R`` (no ``rpy2``
dependency). Python-first agents call:

    from deconvolution import run_deconvolution, load_example
    paths = load_example("/mnt/results/deconvolution")
    res = run_deconvolution(**paths, output_dir="/mnt/results/deconvolution",
                            group_col="group", timepoint_col="timepoint",
                            subject_col="subject_id")
    res["consensus"].head()

``res`` is a ``dict[str, pandas.DataFrame]`` with keys:
  - ``proportions`` : per-method long-form table (sample_id, method, cell_type, fraction)
  - ``consensus``   : sample_id + one column per cell type (cross-method mean)
  - ``concordance`` : pairwise Pearson r (overall + per cell type)
  - ``contrasts``   : group contrasts (Wilcoxon + BH-FDR)
  - ``recovery``    : per-cell-type Pearson r vs ground truth (if provided)

Everything below the entry points is implementation detail.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


def _scripts_dir() -> Path:
    """scripts/ directory holding this file + run_full_workflow.R."""
    return Path(__file__).resolve().parent


def _skill_root() -> Path:
    """Skill root (parent of scripts/)."""
    return _scripts_dir().parent


def _rscript_bin() -> str:
    """Locate Rscript on PATH; raise a clear error if R is not installed."""
    rscript = shutil.which("Rscript")
    if rscript is None:
        raise RuntimeError(
            "Rscript not found on PATH. The deconvolution-bulk-rnaseq skill "
            "is R-native; install R + the required Bioc/CRAN packages first "
            "(see SKILL.md prerequisites)."
        )
    return rscript


def _run_rscript(cli_args: List[str]) -> None:
    """Run scripts/run_full_workflow.R with the given CLI args, streaming logs."""
    workflow = _scripts_dir() / "run_full_workflow.R"
    if not workflow.exists():
        raise FileNotFoundError(
            f"Missing orchestrator: {workflow}. Check the skill installation."
        )
    cmd = [_rscript_bin(), str(workflow), *cli_args]
    print("[deconvolution.py] $ " + " ".join(cmd), flush=True)
    # Run from the skill root so relative paths inside Rscripts resolve.
    proc = subprocess.run(cmd, cwd=str(_skill_root()), check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"R workflow failed (exit {proc.returncode}). See log above."
        )


def load_example(output_dir: str) -> Dict[str, str]:
    """Write the bundled synthetic immune cohort into *output_dir* and return
    paths for ``run_deconvolution(**load_example(...))``.

    Files written:
      example_bulk.csv          -- gene x sample matrix (first column = gene)
      example_reference.rds     -- SingleCellExperiment with cell_type + donor_id
      example_metadata.csv      -- sample_id, group, timepoint, subject_id
      example_ground_truth.csv  -- sample_id + one column per cell type

    The synthetic cohort is deterministic (seed = 42), so SimBu-style
    ground-truth recovery is reproducible.
    """
    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    _run_rscript(["--output-dir", str(out), "--example"])
    paths = {
        "bulk_path":         str(out / "example_bulk.csv"),
        "reference_path":    str(out / "example_reference.rds"),
        "metadata_path":     str(out / "example_metadata.csv"),
        "ground_truth_path": str(out / "example_ground_truth.csv"),
    }
    missing = [p for p in paths.values() if not Path(p).exists()]
    if missing:
        raise RuntimeError(
            "load_example() did not produce expected files: " + ", ".join(missing)
        )
    return paths


def run_deconvolution(
    bulk_path: str,
    reference_path: str,
    metadata_path: str,
    output_dir: str,
    methods: Optional[List[str]] = None,
    group_col: str = "group",
    group_levels: Optional[List[str]] = None,
    timepoint_col: Optional[str] = None,
    subject_col: Optional[str] = None,
    ground_truth_path: Optional[str] = None,
    max_cells_per_type: int = 300,
    n_cores: Optional[int] = None,
    cell_type_col: str = "cell_type",
    batch_col: str = "donor_id",
) -> Dict[str, pd.DataFrame]:
    """Run the full deconvolution workflow via Rscript and parse outputs.

    Parameters
    ----------
    bulk_path, reference_path, metadata_path
        CSV / RDS / H5AD inputs. See SKILL.md for required formats.
    output_dir
        Where all CSV / PNG / SVG / RDS outputs go (e.g. /mnt/results/deconvolution).
    methods
        License-clean panel members (default ``["bayesprism", "dwls"]``).
        Allowed: bayesprism, dwls, music, bisque. CIBERSORTx / EPIC / BSeq-sc
        are hard-rejected by the R workflow.
    group_col, timepoint_col, subject_col
        Metadata columns used for contrasts. timepoint + subject enable a
        longitudinal mixed model (``prop ~ group * timepoint + (1|subject)``).
    ground_truth_path
        Optional CSV with sample_id + one column per cell type. When provided,
        the workflow writes ``ground_truth_recovery.csv`` and the recovery scatter.
    max_cells_per_type
        Reference downsample cap per cell type (default 300).
    n_cores
        BayesPrism core count. None -> auto-detect (min(detectCores()-1, 8)).
    cell_type_col, batch_col
        Reference colData columns to rename to 'cell_type' / 'donor_id'
        if not already named that way.

    Returns
    -------
    dict[str, pd.DataFrame]
        See module docstring.
    """
    methods = methods or ["bayesprism", "dwls"]
    banned = sorted(set(m.lower() for m in methods) & {"cibersortx", "cibersort", "epic", "bseqsc"})
    if banned:
        raise ValueError(
            f"Refusing to run non-commercial methods: {banned}. "
            "Use bayesprism / dwls / music / bisque (see references/license-notes.md)."
        )

    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    cli = [
        "--bulk", str(Path(bulk_path).expanduser()),
        "--reference", str(Path(reference_path).expanduser()),
        "--metadata", str(Path(metadata_path).expanduser()),
        "--output-dir", str(out),
        "--methods", ",".join(methods),
        "--group-col", group_col,
        "--max-cells-per-type", str(int(max_cells_per_type)),
        "--n-cores", "AUTO" if n_cores is None else str(int(n_cores)),
        "--cell-type-col", cell_type_col,
        "--batch-col", batch_col,
    ]
    if group_levels:
        cli += ["--group-levels", ",".join(group_levels)]
    if timepoint_col:
        cli += ["--timepoint-col", timepoint_col]
    if subject_col:
        cli += ["--subject-col", subject_col]
    if ground_truth_path:
        cli += ["--ground-truth", str(Path(ground_truth_path).expanduser())]

    _run_rscript(cli)

    return _parse_outputs(out, methods)


def _parse_outputs(out: Path, methods: List[str]) -> Dict[str, pd.DataFrame]:
    """Read the workflow's CSV outputs back into a dict of DataFrames."""
    def _read(name: str) -> Optional[pd.DataFrame]:
        p = out / name
        if not p.exists() or p.stat().st_size == 0:
            return None
        try:
            return pd.read_csv(p)
        except Exception as e:                              # noqa: BLE001
            print(f"[deconvolution.py] failed to parse {p}: {e}", flush=True)
            return None

    # Per-method proportions -> long form
    long_rows = []
    for m in methods:
        df = _read(f"proportions_{m}.csv")
        if df is None:
            continue
        ct_cols = [c for c in df.columns if c != "sample_id"]
        long = df.melt(id_vars="sample_id", value_vars=ct_cols,
                       var_name="cell_type", value_name="fraction")
        long.insert(1, "method", m)
        long_rows.append(long)
    proportions = (pd.concat(long_rows, ignore_index=True)
                   if long_rows else pd.DataFrame(
                       columns=["sample_id", "method", "cell_type", "fraction"]))

    # NB: `_read(...) or pd.DataFrame()` is unsafe -- non-empty DataFrames raise
    # truth-value-ambiguous ValueError. Explicit None check is required.
    def _read_or_empty(name: str) -> pd.DataFrame:
        df = _read(name)
        return df if df is not None else pd.DataFrame()

    consensus   = _read_or_empty("consensus_proportions.csv")
    concordance = _read_or_empty("method_concordance.csv")
    contrasts   = _read_or_empty("proportion_contrasts.csv")
    recovery    = _read_or_empty("ground_truth_recovery.csv")
    composition = _read_or_empty("composition_summary.csv")

    return {
        "proportions": proportions,
        "consensus":   consensus,
        "concordance": concordance,
        "contrasts":   contrasts,
        "recovery":    recovery,
        "composition": composition,
        "output_dir":  pd.DataFrame({"path": [str(out)]}),  # convenience
    }


# --- CLI shim ---------------------------------------------------------------
def _main(argv: Optional[List[str]] = None) -> int:
    """CLI passthrough: forwards args straight to run_full_workflow.R."""
    if argv is None:
        argv = sys.argv[1:]
    _run_rscript(argv)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
