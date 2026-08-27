#!/usr/bin/env python3
"""
check_enrichment.py
===================
Face-validity self-check for a knowledge-graph target ranking. When there is no
held-out ground truth (the usual case in target discovery), the strongest
available sanity check is: do KNOWN drug targets for the disease concentrate at
the TOP of the ranking? A credible ranking should (a) enrich known targets far
above the genome-wide background in the top bin, and (b) show fold-enrichment that
DECREASES down the list.

If the top bin is NOT enriched above background, the ranking has effectively
failed this check -- usually because the disease anchors were wrong, had too few
seed genes, or the disease is poorly covered in PrimeKG. The script prints a clear
WARNING in that case.

Output
------
- Human-readable table of per-bin known-target rate and fold-enrichment.
- JSON (enrichment_check.json) with the numbers + a boolean `face_valid` flag and
  `monotonic_decreasing` flag, for the PDF report to consume.

Usage
-----
    python check_enrichment.py --ranked ./out/ranked_targets.csv \
        --out ./out/enrichment_check.json
    # custom bins:
    python check_enrichment.py --ranked ./out/ranked_targets.csv \
        --bins 25 50 100 500 2000
"""
import argparse, json, os
import numpy as np
import pandas as pd


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ranked', required=True)
    ap.add_argument('--label-col', default='known_drug_target',
                    help='boolean column marking known drug targets (default: known_drug_target)')
    ap.add_argument('--bins', type=int, nargs='+', default=[50, 100, 500, 2000],
                    help='upper edges of rank bins; a final "rest" bin is added')
    ap.add_argument('--top-warn-fold', type=float, default=1.0,
                    help='WARN if top-bin fold-enrichment is <= this')
    ap.add_argument('--fallback-seed-recovery', action='store_true',
                    help="if --label-col is absent, fall back to a self-contained seed-recovery "
                         "check (do the disease seed genes, from the is_seed column, concentrate at "
                         "the top?). Used in commercial mode when the Open Targets label is "
                         "unavailable offline. The label semantics change to 'is_seed', which is a "
                         "weaker but source-free face-validity signal.")
    ap.add_argument('--out', default='./enrichment_check.json')
    args = ap.parse_args()

    df = pd.read_csv(args.ranked)
    label_col = args.label_col
    label_kind = 'known_drug_target'
    if label_col not in df.columns:
        if args.fallback_seed_recovery and 'is_seed' in df.columns:
            print(f"NOTE: label column '{label_col}' absent; falling back to SEED-RECOVERY check "
                  "on 'is_seed' (source-free face validity: do disease seeds rank at the top?).")
            label_col = 'is_seed'
            label_kind = 'seed_recovery'
        else:
            raise SystemExit(f"label column '{args.label_col}' not in {args.ranked}; "
                             f"columns: {list(df.columns)}")
    df = df.sort_values('rank') if 'rank' in df.columns else df.reset_index(drop=True)
    known = df[label_col].astype(bool).to_numpy()
    N = len(known)
    background = known.mean()
    n_known_total = int(known.sum())

    edges = [0] + sorted(set(args.bins)) + [N]
    edges = [e for e in edges if e <= N]
    if edges[-1] != N:
        edges.append(N)

    bins = []
    prev = 0
    for hi in edges[1:]:
        lo = prev
        seg = known[lo:hi]
        if len(seg) == 0:
            prev = hi
            continue
        rate = seg.mean()
        fold = rate / background if background > 0 else float('nan')
        bins.append(dict(rank_lo=lo + 1, rank_hi=hi, n=len(seg),
                         n_known=int(seg.sum()), rate=float(rate),
                         fold_enrichment=float(fold)))
        prev = hi

    folds = [b['fold_enrichment'] for b in bins]
    monotonic = all(folds[i] >= folds[i + 1] - 1e-9 for i in range(len(folds) - 1))
    top_fold = folds[0] if folds else float('nan')
    face_valid = bool(top_fold > args.top_warn_fold)

    result = dict(n_total=N, background_rate=float(background),
                  n_known_total=n_known_total, bins=bins,
                  top_bin_fold=float(top_fold), face_valid=face_valid,
                  monotonic_decreasing=bool(monotonic),
                  label_col=label_col, label_kind=label_kind)

    # ---- report ----
    print(f"Ranking: {N} genes; background known-target rate = {background:.5f} "
          f"({n_known_total} known targets total)\n")
    print(f"{'rank bin':<16}{'n':>7}{'known':>7}{'rate':>9}{'fold':>9}")
    print('-' * 48)
    for b in bins:
        print(f"{b['rank_lo']:>6}-{b['rank_hi']:<9}{b['n']:>7}{b['n_known']:>7}"
              f"{b['rate']:>9.4f}{b['fold_enrichment']:>8.1f}x")
    print()
    if not face_valid:
        print("*** WARNING: FACE-VALIDITY CHECK FAILED ***")
        print(f"    Top bin fold-enrichment ({top_fold:.2f}x) is not above background.")
        print("    The ranking does NOT recover known drug targets at the top. Likely causes:")
        print("    wrong/insufficient disease anchors, too few seed genes, or poor PrimeKG")
        print("    coverage for this disease. Re-check anchors with find_disease_anchors.py")
        print("    before trusting or reporting these results.")
    else:
        print(f"OK: known targets are {top_fold:.1f}x enriched in the top bin "
              f"(> background).")
        if monotonic:
            print("OK: fold-enrichment decreases monotonically across bins "
                  "(expected for a calibrated ranking).")
        else:
            print("NOTE: fold-enrichment is not strictly monotonic across bins; "
                  "inspect the per-bin table above.")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == '__main__':
    main()
