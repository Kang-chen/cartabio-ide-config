#!/usr/bin/env python3
"""
Inferred microbial metabolite module scoring from PICRUSt2 enzyme (EC) predictions.
ADAPTABLE TEMPLATE - edit the CONFIG block, then run:
    micromamba run -n base python metabolite_modules.py

Modules are keyed on EC numbers (IUBMB Enzyme Commission), which PICRUSt2 outputs by
DEFAULT (EC_metagenome_out/pred_metagenome_unstrat.tsv.gz). KEGG Orthology (KO) is NOT
used by default because KEGG is not licensed for commercial use; see references/DATA_SOURCES.md.

Encodes the hard-won correctness rules (see references/metabolite_modules.md):
  * Butyrate reported as SEPARATE but/buk routes (aggregate masks the switch)
  * EC:1.3.1.114 EXCLUDED from the bai module (promiscuous enzyme -> spurious depletion)
  * bai secondary-bile-acid module GATED OFF by default (16S cannot resolve it)
  * Single-enzyme domination audit printed for every multi-enzyme module
  * Subject-mean Wilcoxon for repeated measures + BH correction
Requires: pandas, numpy, scipy, statsmodels

Optional academic KO mode: set USE_KO=True and point KO_UNSTRAT at your own KO table and
KO_GENESETS_TSV at your own KO->module map. This ships NO KEGG data; it only lets an
academic user (covered by KEGG's academic terms) reuse the same statistics on KO features.
A license warning is printed when enabled.
"""
import pandas as pd, numpy as np
from scipy.stats import mannwhitneyu
from statsmodels.stats.multitest import multipletests

# ------------------------------ CONFIG ---------------------------------------
# Default (license-clean): PICRUSt2 EC predictions.
EC_UNSTRAT  = "picrust2_out/EC_metagenome_out/pred_metagenome_unstrat.tsv.gz"
METADATA    = "metadata.tsv"          # first col = sample ID
GROUP_COL   = "group"                 # 2-level grouping column
CASE_LEVEL  = "IBD"                   # non-reference (case) level
CTRL_LEVEL  = "HC"                    # reference (control) level
SUBJECT_COL = "host_subject_id"       # subject ID for repeated measures; None if 1 sample/subject
OUTDIR      = "results/tables"
INCLUDE_BAI = False                   # bai secondary-bile-acid module is GATED OFF by default

# --- optional academic-only KO mode (ships NO KEGG data; off by default) ---
USE_KO          = False
KO_UNSTRAT      = "picrust2_out/KO_metagenome_out/pred_metagenome_unstrat.tsv.gz"
KO_GENESETS_TSV = None                # user-supplied TSV: columns module<TAB>ko ; required if USE_KO
# -----------------------------------------------------------------------------
import os; os.makedirs(OUTDIR, exist_ok=True)

# EC-keyed module sets (mirror of references/metabolite_modules_ec.csv).
# All EC values below are verified present in PICRUSt2 EC output on the reference dataset.
# EC:1.3.1.114 is deliberately ABSENT from bai_specific (promiscuous-enzyme artifact).
EC_GENESETS = {
    "Butyrate_but_route":  ["EC:2.8.3.8", "EC:2.8.3.9"],                          # health CoA-transferase
    "Butyrate_buk_route":  ["EC:2.7.2.7", "EC:2.3.1.19"],                         # dysbiosis kinase
    "Butyrate_total":      ["EC:2.8.3.8", "EC:2.8.3.9", "EC:2.7.2.7",
                            "EC:2.3.1.19", "EC:1.3.8.1"],
    "Propionate":          ["EC:2.8.3.18", "EC:2.7.2.1"],                         # 2.7.2.1 shared w/ acetate
    "Acetate":             ["EC:2.7.2.1", "EC:2.3.1.8", "EC:6.2.1.1"],
    "BSH":                 ["EC:3.5.1.24"],
    "bai_specific":        ["EC:1.3.1.116"],                                      # EC:1.3.1.114 EXCLUDED
    "Indole":              ["EC:4.1.99.1"],
}
# Butyrate routes get FDR-corrected within their own family; the rest jointly.
ROUTE_FAMILY = {"Butyrate_but_route", "Butyrate_buk_route"}
MAIN_MODULES = ["Propionate", "Acetate", "BSH", "Indole", "Butyrate_total"]
if INCLUDE_BAI:
    MAIN_MODULES.append("bai_specific")

## ---- select feature space: EC (default) or KO (academic opt-in) ----
if USE_KO:
    print("!" * 78)
    print("USE_KO=True: KO mode is for ACADEMIC use only. KEGG is NOT licensed for")
    print("commercial use. You are responsible for your KEGG license terms. No KEGG")
    print("data is shipped with this skill; you must supply KO_UNSTRAT and KO_GENESETS_TSV.")
    print("!" * 78)
    if not KO_GENESETS_TSV or not os.path.exists(str(KO_GENESETS_TSV)):
        raise SystemExit("USE_KO=True requires KO_GENESETS_TSV (columns: module<TAB>ko).")
    feat = pd.read_csv(KO_UNSTRAT, sep="\t", index_col=0)
    kmap = pd.read_csv(KO_GENESETS_TSV, sep="\t", dtype=str)
    GENESETS = {m: sorted(set(g["ko"])) for m, g in kmap.groupby("module")}
    FEATURE_KIND = "KO"
    OUT_STATS = f"{OUTDIR}/metabolite_module_stats.csv"
else:
    feat = pd.read_csv(EC_UNSTRAT, sep="\t", index_col=0)
    # PICRUSt2 EC feature IDs are typically "EC:1.1.1.1"; tolerate a bare "1.1.1.1" table too.
    if not any(str(i).startswith("EC:") for i in feat.index[:20]):
        feat.index = ["EC:" + str(i) if not str(i).startswith("EC:") else str(i) for i in feat.index]
    GENESETS = {m: v for m, v in EC_GENESETS.items() if (INCLUDE_BAI or m != "bai_specific")}
    FEATURE_KIND = "EC"
    OUT_STATS = f"{OUTDIR}/metabolite_module_stats.csv"

## ---- load & align ----
md = pd.read_csv(METADATA, sep="\t")
md = md.set_index(md.columns[0])
common = [s for s in feat.columns if s in md.index]
feat = feat[common]; md = md.loc[common]
grp = md[GROUP_COL].astype(str)
print(f"{FEATURE_KIND} table {feat.shape[0]} features x {feat.shape[1]} samples | "
      f"{CTRL_LEVEL}={int((grp==CTRL_LEVEL).sum())} {CASE_LEVEL}={int((grp==CASE_LEVEL).sum())}")
if not INCLUDE_BAI and not USE_KO:
    print("  [note] bai secondary-bile-acid module is GATED OFF (set INCLUDE_BAI=True to enable; low confidence).")

feat_rel = feat / feat.sum(axis=0)   # per-sample relative abundance

## ---- score modules + domination audit ----
scores = pd.DataFrame(index=feat.columns)
for name, members in GENESETS.items():
    present = [k for k in members if k in feat_rel.index]
    if not present:
        print(f"  [skip] {name}: no {FEATURE_KIND} features found in prediction"); continue
    sub = feat_rel.loc[present]
    scores[name] = sub.sum(axis=0)
    if len(present) > 1:
        contrib = sub.mean(axis=1); top = contrib.idxmax()
        frac = contrib.max() / contrib.sum()
        print(f"  {name}: {len(present)}/{len(members)} {FEATURE_KIND}; top {top} carries {frac:.0%}")
scores[GROUP_COL] = grp
if SUBJECT_COL:
    scores["subject"] = md[SUBJECT_COL].values
scores.to_csv(f"{OUTDIR}/metabolite_module_scores.csv")

## ---- test: subject-mean Wilcoxon (or per-sample if no subjects) ----
def test_module(name):
    df = scores[[name, GROUP_COL]].copy()
    if SUBJECT_COL:
        df["subject"] = scores["subject"]
        agg = df.groupby(["subject", GROUP_COL])[name].mean().reset_index()
        a = agg.loc[agg[GROUP_COL] == CASE_LEVEL, name]
        b = agg.loc[agg[GROUP_COL] == CTRL_LEVEL, name]
    else:
        a = df.loc[df[GROUP_COL] == CASE_LEVEL, name]
        b = df.loc[df[GROUP_COL] == CTRL_LEVEL, name]
    if len(a) < 2 or len(b) < 2: return None
    p = mannwhitneyu(a, b, alternative="two-sided").pvalue
    fold = a.mean() / b.mean() if b.mean() else np.nan
    n_present = int(sum(1 for k in GENESETS[name] if k in feat_rel.index))
    return dict(pathway=name, n_feat_present=n_present, n_feat_total=len(GENESETS[name]),
                ctrl_mean=b.mean(), case_mean=a.mean(), fold_case_ctrl=fold,
                direction="higher in case" if fold > 1 else "lower in case", p_subject=p)

rows = [r for r in (test_module(n) for n in GENESETS if n in scores.columns) if r]
res = pd.DataFrame(rows)
# FDR: main modules jointly; butyrate routes within their own family
res["q_subject"] = np.nan
main_mask = res.pathway.isin(MAIN_MODULES)
if main_mask.any():
    res.loc[main_mask, "q_subject"] = multipletests(res.loc[main_mask, "p_subject"], method="fdr_bh")[1]
route_mask = res.pathway.isin(ROUTE_FAMILY)
if route_mask.any():
    res.loc[route_mask, "q_subject"] = multipletests(res.loc[route_mask, "p_subject"], method="fdr_bh")[1]
res = res.sort_values("p_subject")
res.to_csv(OUT_STATS, index=False)
print("\nMetabolite module stats:")
print(res[["pathway", "fold_case_ctrl", "direction", "p_subject", "q_subject"]].to_string(index=False))
print("\nReminder: predicted genomic potential, NOT measured metabolites. Hypothesis-generating.")
print("Do NOT report secondary bile-acid (bai) depletion from 16S prediction (promiscuous EC:1.3.1.114 artifact).")
