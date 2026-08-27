#!/usr/bin/env python3
"""
DepMap cross-check for CRISPR-screen hits.

Purpose: after MAGeCK nominates hits, distinguish genes that are simply
BROADLY ESSENTIAL (would deplete in almost any proliferating cell) from genes
that are context/T-cell-specific regulators. A screen "hit" that is pan-essential
in DepMap is a weaker CAR-T engineering target than one that is not.

Data: DepMap CRISPR gene-effect matrix (Chronos scores).
  /mnt/datalake/depmap/crispr_screen/CRISPRGeneEffect.csv
  Orientation: rows = cell-line models (ModelID, ACH-######; first unnamed column),
               columns = genes named "SYMBOL (ENTREZ)".
  Interpretation: gene-effect ~ 0 = no effect; more NEGATIVE = more depleting /
  more essential. A common essentiality threshold is < -0.5 (dependent line).

Memory-safety: the full matrix is ~430 MB / ~18k gene columns. This script reads
ONLY the queried gene columns (+ the ModelID index) via pandas `usecols`, so it
never loads the whole matrix.

Usage:
    python depmap_crosscheck.py --genes CD3D CBLB CD5 PTEN LCP2 ITK \
        --out /mnt/results/screen_analysis/tables/depmap_crosscheck.csv
    # or read genes from a MAGeCK gene_summary (top N by pos and neg rank):
    python depmap_crosscheck.py --gene-summary pilot_div_vs_nondiv.gene_summary.txt \
        --top 15 --out .../depmap_crosscheck.csv

Optional alternative reference: pass --matrix to point at a different
gene-effect/essentiality CSV with the same orientation (rows=models,
cols=SYMBOL...), e.g. a user-supplied or lineage-filtered matrix.
"""
import argparse, csv, os, re, sys

DEFAULT_MATRIX = "/mnt/datalake/depmap/crispr_screen/CRISPRGeneEffect.csv"
DEP_THRESHOLD = -0.5   # Chronos gene-effect below this ~ "dependent" line

def read_header(path):
    with open(path) as f:
        return next(csv.reader(f))

def build_symbol_map(header):
    """Map bare gene SYMBOL -> exact column name 'SYMBOL (ENTREZ)'."""
    m = {}
    for col in header:
        mt = re.match(r"^(.*?)\s*\(\d+\)\s*$", col)
        if mt:
            m[mt.group(1).upper()] = col
    return m

def genes_from_gene_summary(path, top):
    import pandas as pd
    gs = pd.read_csv(path, sep="\t")
    pos = gs.sort_values("pos|rank").head(top)["id"].tolist()
    neg = gs.sort_values("neg|rank").head(top)["id"].tolist()
    # preserve order, dedupe
    seen, out = set(), []
    for g in pos + neg:
        if g not in seen and str(g) != "Non_Targeting_Control":
            seen.add(g); out.append(g)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--genes", nargs="+", help="gene symbols to check")
    ap.add_argument("--gene-summary", help="MAGeCK gene_summary.txt (auto-pick top hits)")
    ap.add_argument("--top", type=int, default=15, help="top N per direction from gene_summary")
    ap.add_argument("--matrix", default=DEFAULT_MATRIX, help="gene-effect CSV (rows=models, cols='SYMBOL (ENTREZ)')")
    ap.add_argument("--threshold", type=float, default=DEP_THRESHOLD)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import pandas as pd
    genes = list(args.genes or [])
    if args.gene_summary:
        genes += genes_from_gene_summary(args.gene_summary, args.top)
    if not genes:
        sys.exit("Provide --genes and/or --gene-summary")
    genes = [g for g in dict.fromkeys(genes)]  # dedupe, keep order

    header = read_header(args.matrix)
    id_col = header[0]  # first column = ModelID (header may be empty string '')
    sym2col = build_symbol_map(header)

    matched = {g: sym2col[g.upper()] for g in genes if g.upper() in sym2col}
    missing = [g for g in genes if g.upper() not in sym2col]
    if not matched:
        sys.exit(f"No query genes found in matrix columns. Missing: {missing}")

    # Memory-safe: read ONLY the matched gene columns. All statistics below are
    # column-wise aggregates over cell lines, so we don't need the ModelID index.
    # (Note: pandas auto-renames the empty first header to 'Unnamed: 0', so it is
    # NOT selected by this callable -- which is fine, we don't need it.)
    want = set(matched.values())
    df = pd.read_csv(args.matrix, usecols=lambda c: c in want)
    n_lines = df.shape[0]

    rows = []
    for g, col in matched.items():
        vals = df[col].dropna()
        mean_eff = float(vals.mean())
        median_eff = float(vals.median())
        frac_dep = float((vals < args.threshold).mean())
        # pan-essential heuristic: dependent in the vast majority of lines
        pan_essential = frac_dep >= 0.90
        rows.append({
            "gene": g,
            "depmap_mean_gene_effect": round(mean_eff, 4),
            "depmap_median_gene_effect": round(median_eff, 4),
            f"frac_lines_dependent(<{args.threshold})": round(frac_dep, 4),
            "n_lines": n_lines,
            "pan_essential_flag": pan_essential,
            "interpretation": ("broadly essential (weak CAR-T target)" if pan_essential
                               else "not pan-essential (context-specific candidate)"),
        })
    out_df = pd.DataFrame(rows).sort_values("depmap_mean_gene_effect")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    out_df.to_csv(args.out, index=False)
    print(out_df.to_string(index=False))
    if missing:
        print(f"\n[note] {len(missing)} gene(s) not in matrix: {missing}")
    print(f"\nWrote {args.out}")
    print("Reminder: DepMap lines are cancer cell lines, NOT primary T cells; use this "
          "only to flag broad essentiality, not to confirm T-cell-specific biology.")

if __name__ == "__main__":
    main()
