#!/usr/bin/env python3
"""
build_response_table.py — Stage 3 of signature-response-enrichment.

Turn a per-sample severity table into a per-patient responder / non-responder table.

Response rule (generalized from the worked example's PASI75):
    pct_improve = (baseline - endpoint) / baseline          # on the severity metric
    responder   = pct_improve >= threshold - 1e-9           # default threshold 0.75

The `- 1e-9` is deliberate: it prevents floating-point boundary flips. In the worked
example this corrected exactly one PSORT-refinement boundary patient (15R/10NR ->
16R/9NR), which changed downstream results — so keep it.

Fallback: if no numeric severity metric is available but a categorical responder label
is, use that and mark the cohort `label_based=True`.

Input CSV (long format) must contain at least:
    patient_id, timepoint, <severity_col>            # numeric mode
  or
    patient_id, <response_label_col>                 # categorical fallback

Baseline / endpoint timepoints are chosen automatically (min / max of a numeric parse
of the timepoint field) unless given explicitly.

Usage (numeric):
  python build_response_table.py --in samples.csv \
      --patient-col patient_id --time-col week --severity-col pasi \
      --threshold 0.75 --out response.csv

Usage (categorical fallback):
  python build_response_table.py --in samples.csv \
      --patient-col patient_id --label-col response --responder-value R \
      --out response.csv
"""
import argparse
import re
import sys

import numpy as np
import pandas as pd


def _num(x):
    """Extract a numeric value from a timepoint like 'WK12' / 'week 4' / '0'."""
    m = re.search(r"-?\d+\.?\d*", str(x))
    return float(m.group()) if m else np.nan


def numeric_mode(df, pcol, tcol, scol, threshold, baseline_tp, endpoint_tp):
    df = df.copy()
    df["_t"] = df[tcol].map(_num)
    df[scol] = pd.to_numeric(df[scol], errors="coerce")
    df = df.dropna(subset=["_t", scol])
    if baseline_tp is not None:
        base_mask = df[tcol].astype(str) == str(baseline_tp)
    else:
        base_mask = df["_t"] == df["_t"].min()
    if endpoint_tp is not None:
        end_mask = df[tcol].astype(str) == str(endpoint_tp)
    else:
        end_mask = df["_t"] == df["_t"].max()

    base = df[base_mask].groupby(pcol)[scol].mean().rename("baseline")
    end = df[end_mask].groupby(pcol)[scol].mean().rename("endpoint")
    out = pd.concat([base, end], axis=1).dropna()
    # guard against baseline==0 (undefined % improvement)
    out = out[out["baseline"] != 0]
    out["pct_improve"] = (out["baseline"] - out["endpoint"]) / out["baseline"]
    out["responder"] = out["pct_improve"] >= (threshold - 1e-9)
    out["response_group"] = np.where(out["responder"], "R", "NR")
    out["label_based"] = False
    return out.reset_index()


def categorical_mode(df, pcol, lcol, responder_value):
    df = df.copy()
    lab = df.groupby(pcol)[lcol].first()
    out = lab.to_frame("response_label")
    out["responder"] = out["response_label"].astype(str) == str(responder_value)
    out["response_group"] = np.where(out["responder"], "R", "NR")
    out["baseline"] = np.nan
    out["endpoint"] = np.nan
    out["pct_improve"] = np.nan
    out["label_based"] = True
    return out.reset_index()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--patient-col", required=True)
    ap.add_argument("--time-col")
    ap.add_argument("--severity-col")
    ap.add_argument("--label-col")
    ap.add_argument("--responder-value", default="R")
    ap.add_argument("--threshold", type=float, default=0.75)
    ap.add_argument("--baseline-tp", default=None)
    ap.add_argument("--endpoint-tp", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    df = pd.read_csv(args.inp)

    if args.severity_col and args.time_col and args.severity_col in df.columns:
        out = numeric_mode(df, args.patient_col, args.time_col, args.severity_col,
                           args.threshold, args.baseline_tp, args.endpoint_tp)
        mode = f"numeric (>= {args.threshold:.0%} improvement, robust -1e-9)"
    elif args.label_col and args.label_col in df.columns:
        out = categorical_mode(df, args.patient_col, args.label_col,
                               args.responder_value)
        mode = f"categorical label ({args.label_col}=={args.responder_value} -> R)"
    else:
        sys.exit("ERROR: provide --time-col + --severity-col (numeric) OR "
                 "--label-col (categorical fallback).")

    n_r = int((out["response_group"] == "R").sum())
    n_nr = int((out["response_group"] == "NR").sum())
    out.to_csv(args.out, index=False)
    sys.stderr.write(
        f"[response] mode: {mode}\n"
        f"[response] split: {n_r} R / {n_nr} NR  (n={len(out)})\n"
        f"[response] wrote -> {args.out}\n")
    if out["label_based"].any():
        sys.stderr.write("[response] NOTE: cohort is LABEL-BASED (no numeric severity); "
                         "flag this in the report.\n")


if __name__ == "__main__":
    main()
