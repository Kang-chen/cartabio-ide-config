#!/usr/bin/env python3
"""
enrichment.py -- LABELED-branch benchmarking metrics for a virtual screen.

Runs ONLY when the library has activity labels (library_meta.json: labeled=true).
For unlabeled runs the orchestrator skips this and the report notes that enrichment
was omitted (no ground-truth actives/decoys).

Convention (matches the reference EGFR/DUD-E run):
  * actives = 1, decoys = 0
  * Vina affinity: MORE NEGATIVE = better -> rank ASCENDING by vina_affinity
  * ROC uses score = -vina_affinity (so higher = better, standard orientation)
  * EF / BEDROC use RDKit's validated implementations (rdkit.ML.Scoring.Scoring):
      CalcEnrichment / CalcBEDROC expect rows sorted BEST->WORST, each row a list
      whose col --col holds the label. We pass [[label], ...] and col=0.
  * Mann-Whitney U on affinities, alternative="less" (actives more negative);
    rank-biserial effect size  rbc = 1 - 2U/(nA*nD).

Inputs:
  --scores all_scores_merged.csv  (mol_id,...,activity_label,vina_affinity,status)

Outputs (into --outdir):
  enrichment_metrics.json   all scalar metrics + counts
  roc_curve.csv             fpr,tpr,threshold
  actives_scored.csv        scored actives with rank (for triage/QC)
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import sys


try:
    from common import label_from_row as _label_from_row
except Exception:  # standalone fallback if common.py is not importable
    def _label_from_row(r):
        lab = r.get("activity_label", "")
        if lab not in ("", None):
            try:
                return int(float(lab))
            except (ValueError, TypeError):
                pass
        src = (r.get("source") or "").strip().lower()
        if src in ("active", "actives"):
            return 1
        if src in ("decoy", "decoys", "inactive", "inactives"):
            return 0
        return None


def _load(scores_csv):
    rows = []
    with open(scores_csv, newline="") as fh:
        for r in csv.DictReader(fh):
            aff = r.get("vina_affinity", "")
            if r.get("status") != "ok" or aff in ("", None):
                continue
            lab = _label_from_row(r)
            if lab is None:
                continue
            try:
                rows.append((r["mol_id"], int(lab), float(aff)))
            except ValueError:
                continue
    return rows


def _ranked_rows(labels_by_rank):
    """RDKit Scoring wants rows sorted best->worst; each row's col0 = label."""
    return [[int(x)] for x in labels_by_rank]


def run(args) -> int:
    rows = _load(args.scores)
    n_act = sum(1 for _, l, _ in rows if l == 1)
    n_dec = sum(1 for _, l, _ in rows if l == 0)
    if n_act == 0 or n_dec == 0:
        print(f"[ERROR] need both classes; got actives={n_act} decoys={n_dec}. "
              "This looks like an UNLABELED run -- enrichment should be skipped.",
              file=sys.stderr)
        return 3

    # rank best->worst = ascending affinity (most negative first)
    rows_sorted = sorted(rows, key=lambda t: t[2])
    labels_by_rank = [l for _, l, _ in rows_sorted]

    import numpy as np
    from rdkit.ML.Scoring.Scoring import CalcBEDROC, CalcEnrichment, CalcAUC

    ranked = _ranked_rows(labels_by_rank)
    roc_auc = float(CalcAUC(ranked, 0))
    bedroc = float(CalcBEDROC(ranked, 0, args.bedroc_alpha))
    efs = {}
    for frac in args.ef_fracs:
        try:
            efs[f"EF_{frac}"] = float(CalcEnrichment(ranked, 0, [frac])[0])
        except Exception:
            efs[f"EF_{frac}"] = None

    # sklearn ROC curve (for plotting) using score = -affinity
    from sklearn.metrics import roc_curve, roc_auc_score
    y = np.array([l for _, l, _ in rows])
    s = np.array([-a for _, _, a in rows])
    fpr, tpr, thr = roc_curve(y, s)
    roc_auc_sk = float(roc_auc_score(y, s))

    # Mann-Whitney + rank-biserial
    from scipy.stats import mannwhitneyu
    act = [a for _, l, a in rows if l == 1]
    dec = [a for _, l, a in rows if l == 0]
    U, p = mannwhitneyu(act, dec, alternative="less")
    rbc = 1 - (2 * U) / (len(act) * len(dec))

    metrics = {
        "n_scored": len(rows),
        "n_actives_scored": n_act,
        "n_decoys_scored": n_dec,
        "roc_auc": round(roc_auc, 4),
        "roc_auc_sklearn": round(roc_auc_sk, 4),
        f"bedroc_alpha{int(args.bedroc_alpha)}": round(bedroc, 4),
        "active_affinity_median": round(float(np.median(act)), 3),
        "decoy_affinity_median": round(float(np.median(dec)), 3),
        "active_affinity_mean": round(float(np.mean(act)), 3),
        "decoy_affinity_mean": round(float(np.mean(dec)), 3),
        "mannwhitney_U": float(U),
        "mannwhitney_p": float(p),
        "rank_biserial_effect": round(float(rbc), 4),
    }
    metrics.update({k: (round(v, 4) if v is not None else None) for k, v in efs.items()})

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "enrichment_metrics.json"), "w") as fh:
        json.dump(metrics, fh, indent=2)
    with open(os.path.join(args.outdir, "roc_curve.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["fpr", "tpr", "threshold"])
        for a, b, c in zip(fpr, tpr, thr):
            w.writerow([round(float(a), 5), round(float(b), 5), round(float(c), 4)])
    # scored actives with global rank
    rank_of = {mid: i + 1 for i, (mid, _, _) in enumerate(rows_sorted)}
    with open(os.path.join(args.outdir, "actives_scored.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["mol_id", "vina_affinity", "rank"])
        for mid, l, a in sorted([r for r in rows if r[1] == 1], key=lambda t: t[2]):
            w.writerow([mid, a, rank_of[mid]])

    print(f"[OK] ROC-AUC={metrics['roc_auc']} "
          f"BEDROC(a={int(args.bedroc_alpha)})={metrics[f'bedroc_alpha{int(args.bedroc_alpha)}']} "
          f"EF={ {k: v for k, v in metrics.items() if k.startswith('EF_')} }")
    print(f"[OK] MWU p={metrics['mannwhitney_p']:.2e} rank-biserial={metrics['rank_biserial_effect']} "
          f"(actives med {metrics['active_affinity_median']} vs decoys {metrics['decoy_affinity_median']})")
    return 0


def self_check() -> int:
    """Perfect ranking must give AUC≈1, EF1%≈max, BEDROC≈1; random ~0.5."""
    ok = True
    try:
        from rdkit.ML.Scoring.Scoring import CalcBEDROC, CalcEnrichment, CalcAUC
    except ImportError as e:
        print(f"[FAIL] rdkit Scoring import: {e}", file=sys.stderr); return 1
    # 10 actives ranked first, then 90 decoys
    perfect = _ranked_rows([1] * 10 + [0] * 90)
    auc = CalcAUC(perfect, 0)
    bed = CalcBEDROC(perfect, 0, 20.0)
    ef1 = CalcEnrichment(perfect, 0, [0.01])[0]
    if not (auc > 0.99 and bed > 0.99):
        print(f"[FAIL] perfect AUC={auc} BEDROC={bed}", file=sys.stderr); ok = False
    # rank-biserial sign check
    from scipy.stats import mannwhitneyu
    U, p = mannwhitneyu([-9, -8.5, -8.2], [-7, -6.5, -6], alternative="less")
    rbc = 1 - (2 * U) / (3 * 3)
    if not (rbc > 0):
        print(f"[FAIL] rank-biserial sign rbc={rbc}", file=sys.stderr); ok = False
    print(f"enrichment.py self-check: {'PASS' if ok else 'FAIL'} "
          f"(perfect AUC={auc:.3f} BEDROC={bed:.3f} EF1%={ef1:.1f}, rbc>0)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Labeled-branch enrichment metrics (RDKit)")
    ap.add_argument("--scores", help="all_scores_merged.csv")
    ap.add_argument("--outdir", default="/mnt/results/sbvs_run")
    ap.add_argument("--bedroc-alpha", type=float, default=20.0)
    ap.add_argument("--ef-fracs", type=float, nargs="+", default=[0.01, 0.05, 0.10])
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    if not args.scores:
        ap.error("--scores is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
