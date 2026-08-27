"""
run_constraint_gating.py — end-to-end entrypoint for the genetic-constraint-gating skill.

Usage (from the skill's scripts/ dir so imports resolve):
    python run_constraint_gating.py --genes SCN1A MECP2 NF1 --outdir /mnt/results
    python run_constraint_gating.py --file /mnt/user-uploads/candidates.csv --outdir /mnt/results
    python run_constraint_gating.py --genes TP53 PTEN --no-pdf     # table + figures only
    python run_constraint_gating.py --genes TP53 PTEN --csv-only   # leanest

Outputs (default = full deliverables) written to --outdir:
    gnomad_constraint_flags.csv
    fig1_ranked_loeuf.(png|svg), fig2_pli_vs_loeuf.(png|svg), fig3_loeuf_v2_vs_v4.(png|svg)
    report_gnomad_LoF_constraint.pdf

After generating figures/PDF, ALWAYS run a media-output-check (Read mode) on each
PNG and on the PDF, and regenerate if a figure is blank/clipped/unreadable.
"""

import argparse
import os
import sys
import pandas as pd

# make sibling modules importable regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from constraint_analysis import analyze_genes
from constraint_figures import make_all_figures
from constraint_report import build_report


def _read_gene_file(path):
    """Read a CSV/TXT and return a list of gene tokens from a gene-like column."""
    if path.lower().endswith((".csv", ".tsv")):
        sep = "\t" if path.lower().endswith(".tsv") else ","
        df = pd.read_csv(path, sep=sep)
        cols = {c.lower(): c for c in df.columns}
        for key in ("gene", "symbol", "gene_symbol", "genes", "hgnc_symbol"):
            if key in cols:
                return df[cols[key]].dropna().astype(str).str.strip().tolist()
        return df.iloc[:, 0].dropna().astype(str).str.strip().tolist()
    # plain text: one token per line (or whitespace/comma separated)
    with open(path) as fh:
        text = fh.read()
    toks = [t.strip() for line in text.splitlines() for t in line.replace(",", " ").split()]
    return [t for t in toks if t]


def main(argv=None):
    ap = argparse.ArgumentParser(description="gnomAD LoF constraint gating for a gene set.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--genes", nargs="+", help="gene symbols / aliases / ENSG ids")
    src.add_argument("--file", help="CSV/TXT file with a gene column")
    ap.add_argument("--outdir", default="/mnt/results", help="output directory")
    ap.add_argument("--loeuf-cut", type=float, default=0.35)
    ap.add_argument("--pli-cut", type=float, default=0.90)
    ap.add_argument("--no-pdf", action="store_true", help="skip the PDF (table + figures only)")
    ap.add_argument("--csv-only", action="store_true", help="only the CSV (no figures, no PDF)")
    args = ap.parse_args(argv)

    genes = args.genes if args.genes else _read_gene_file(args.file)
    genes = [g for g in dict.fromkeys(genes)]  # dedupe, keep order
    if not genes:
        print("No genes to process."); return 1
    os.makedirs(args.outdir, exist_ok=True)

    print(f"Analyzing {len(genes)} gene(s)...")
    df = analyze_genes(genes)

    # inject custom thresholds into the analysis if the user changed them
    if (args.loeuf_cut, args.pli_cut) != (0.35, 0.90):
        def recut(r):
            drivers = []
            if pd.notna(r.get("LOEUF_v2")) and r["LOEUF_v2"] < args.loeuf_cut:
                drivers.append("LOEUF")
            if pd.notna(r.get("pLI_v2")) and r["pLI_v2"] >= args.pli_cut:
                drivers.append("pLI")
            if r["LoF_intolerant"] in ("Yes", "No"):
                r["LoF_intolerant"] = "Yes" if drivers else "No"
                r["flag_driver"] = "+".join(drivers) if drivers else "-"
            return r
        df = df.apply(recut, axis=1)
        # thresholds changed -> refresh the drug-target interpretation to match
        from constraint_druggability import add_druggability_columns
        drop = [c for c in ("ko_tolerance_tier", "ko_tolerance_rationale", "systemic_target_risk",
                            "systemic_target_note", "target_strategy", "actionability",
                            "druggability_verdict") if c in df.columns]
        if drop:
            df = df.drop(columns=drop)
        df = add_druggability_columns(df)

    csv_path = os.path.join(args.outdir, "gnomad_constraint_flags.csv")
    df.to_csv(csv_path, index=False)
    print(f"  -> {csv_path}")

    n_res = int(df["LoF_intolerant"].isin(["Yes", "No"]).sum())
    n_flag = int((df["LoF_intolerant"] == "Yes").sum())
    print(f"  resolved {n_res}/{len(df)} genes; {n_flag} flagged LoF-intolerant: "
          f"{', '.join(df.loc[df.LoF_intolerant=='Yes','gene'])}")

    if args.csv_only:
        return 0

    figs = make_all_figures(df, args.outdir)
    print("  figures:", {k: os.path.basename(v) if v else None for k, v in figs.items()})

    if not args.no_pdf:
        pdf_path = os.path.join(args.outdir, "report_gnomad_LoF_constraint.pdf")
        build_report(df, figs, pdf_path, thresholds=(args.loeuf_cut, args.pli_cut))
        print(f"  -> {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
