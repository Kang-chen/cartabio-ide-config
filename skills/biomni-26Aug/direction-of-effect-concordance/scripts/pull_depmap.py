#!/usr/bin/env python3
"""
pull_depmap.py — summarize DepMap CRISPR gene-effect / gene-dependency for the target genes,
for the Functional/CRISPR axis of direction-of-effect concordance.

MEMORY-SAFE: the DepMap matrices are ~400 MB each with cell lines as ROWS and genes as
COLUMNS. We read only the target gene COLUMNS (usecols), never the whole matrix.

DepMap score convention (CRITICAL — see references/direction_rules.md + `gene-essentiality`):
  CRISPRGeneEffect.csv     : NEGATIVE = essential (knockout kills cells); ~0 = non-essential.
  CRISPRGeneDependency.csv : probability of dependency in [0,1] (higher = more dependent).

Direction mapping produced here (functional axis):
  - broad pan-essentiality (very negative mean effect, high frac dependent across lines)
        -> "INHIBIT (broad essentiality -> toxicity caveat)"
  - selective dependency (some lines strongly dependent, many not)
        -> "INHIBIT (selective dependency)"
  - not essential anywhere -> "not_informative (no dependency signal)"
The agent refines this with disease-lineage context and literature (Step 3/4).

Usage:
  python pull_depmap.py --targets "PCSK9,SOST,PNPLA3" --out RUN/data
  python pull_depmap.py --targets "MDM2,EGFR" --out RUN/data \
      --effect "${DEPMAP_GENE_EFFECT_CSV}"
"""
import argparse, os, re, sys
import pandas as pd

DEF_EFFECT = "/mnt/datalake/depmap/crispr_screen/CRISPRGeneEffect.csv"
DEF_DEP = "/mnt/datalake/depmap/crispr_screen/CRISPRGeneDependency.csv"

# DepMap gene columns are typically "SYMBOL (ENTREZID)", e.g. "PCSK9 (255738)".
def find_columns(header, symbols):
    """Map each requested symbol to its actual column name in the DepMap header."""
    colmap = {}
    # build symbol -> column by stripping the trailing "(entrez)"
    base = {}
    for col in header:
        m = re.match(r"^([A-Za-z0-9\-\.]+)\s*\(\d+\)$", col)
        key = (m.group(1) if m else col).upper()
        base.setdefault(key, col)
    for s in symbols:
        colmap[s] = base.get(s.upper())
    return colmap


def summarize(effect_path, dep_path, symbols):
    header = pd.read_csv(effect_path, nrows=0).columns.tolist()
    id_col = header[0]  # first column = model/cell-line ID
    colmap = find_columns(header, symbols)
    present = {s: c for s, c in colmap.items() if c}
    missing = [s for s, c in colmap.items() if not c]

    rows = []
    if present:
        eff = pd.read_csv(effect_path, usecols=[id_col] + list(present.values()))
        dep = None
        if dep_path and os.path.exists(dep_path):
            dep_header = pd.read_csv(dep_path, nrows=0).columns.tolist()
            dep_id = dep_header[0]
            dep_colmap = find_columns(dep_header, symbols)
            dep_present = {s: c for s, c in dep_colmap.items() if c}
            if dep_present:
                dep = pd.read_csv(dep_path, usecols=[dep_id] + list(dep_present.values()))
        for s, col in present.items():
            vals = eff[col].dropna()
            n = len(vals)
            mean_eff = float(vals.mean()) if n else None
            frac_dep = None
            if dep is not None and s in dep_colmap and dep_colmap[s] in dep.columns:
                dvals = dep[dep_colmap[s]].dropna()
                frac_dep = float((dvals > 0.5).mean()) if len(dvals) else None
            # heuristics
            pan_ess = bool(mean_eff is not None and mean_eff < -0.5
                           and (frac_dep is None or frac_dep > 0.7))
            selective = bool(mean_eff is not None and not pan_ess
                             and float(vals.min()) < -0.5 and mean_eff > -0.4)
            if pan_ess:
                interp = "INHIBIT (broad essentiality -> on-target toxicity caveat)"
            elif selective:
                interp = "INHIBIT (selective dependency in a subset of lines)"
            elif mean_eff is not None and mean_eff < -0.2:
                interp = "INHIBIT (modest dependency)"
            else:
                interp = "not_informative (no clear dependency signal)"
            rows.append({"target": s, "depmap_column": col, "n_lines": n,
                         "mean_gene_effect": round(mean_eff, 4) if mean_eff is not None else None,
                         "min_gene_effect": round(float(vals.min()), 4) if n else None,
                         "frac_dependent": round(frac_dep, 4) if frac_dep is not None else None,
                         "pan_essential": pan_ess, "selective": selective,
                         "interpretation": interp})
    for s in missing:
        rows.append({"target": s, "depmap_column": None, "n_lines": 0,
                     "mean_gene_effect": None, "min_gene_effect": None,
                     "frac_dependent": None, "pan_essential": False,
                     "selective": False,
                     "interpretation": "not_informative (gene not in DepMap CRISPR matrix)"})
    return pd.DataFrame(rows), missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--effect", default=DEF_EFFECT)
    ap.add_argument("--dependency", default=DEF_DEP)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    symbols = [t.strip() for t in args.targets.split(",") if t.strip()]

    if not os.path.exists(args.effect):
        sys.exit(f"ERROR: DepMap effect file not found: {args.effect}\n"
                 f"Supply the DepMap effect file with --effect.")

    df, missing = summarize(args.effect, args.dependency, symbols)
    out_csv = os.path.join(args.out, "depmap_summary.csv")
    df.to_csv(out_csv, index=False)
    # sanity note about score convention
    print("DepMap score convention: NEGATIVE gene-effect = essential (knockout lethal).")
    for _, r in df.iterrows():
        print(f"  {r['target']}: mean_effect={r['mean_gene_effect']} "
              f"frac_dep={r['frac_dependent']} -> {r['interpretation']}")
    if missing:
        print("Not found in DepMap CRISPR matrix:", ", ".join(missing))
    print(f"-> {out_csv}")


if __name__ == "__main__":
    main()
