# Worked example — residual TYK2 signaling → adalimumab non-response in psoriasis

The concrete run this skill generalizes. Use it as a template for what "good" looks like
end to end. All numbers below were verified against the original run record; treat them as
an illustration of the workflow, not as inputs to a new analysis.

## Inputs
- **drug:** adalimumab (anti-TNF-α)
- **disease:** psoriasis (target tissue: lesional skin)
- **gene_signatures:** two named TYK2-pathway sets
  - `TYK2_JAK1_IFNab` — JAK1-dependent type-I interferon arm — **21 genes**
  - `TYK2_JAK2_IL12_23` — JAK2-dependent IL-12/23 arm — **20 genes**
  - ambiguous tokens resolved: `IFNA → IFNA1 + IFNA2`, `IL18R → IL18R1`
- context panel: 50 MSigDB Hallmark sets + keyword-filtered Reactome immune sets
- response: PASI75 at last on-treatment timepoint (threshold 0.75, robust `−1e-9`)

Motivation: replicate a ulcerative-colitis finding (UC poster MP256, Paraskevopoulou
et al., UEGW 2025; VARSITY cohort n≈770) that residual TYK2 signaling marks non-response,
now in psoriasis skin.

## Stage 1-2 — discovery & curation
Searched GEO (eutils) **and** ArrayExpress/BioStudies; catalogued **20 human series** with
explicit include/exclude reasons (the discovery catalog = Table 1). Four included:

| Accession | Role | Why |
|---|---|---|
| **E-MTAB-14509** (PSORT; ArrayExpress, *not* in GEO) | primary + internal validation | RNA-seq, adalimumab arm, per-sample numeric PASI (197/198 ADA lesional), built-in discovery/refinement split |
| **GSE85034** | cross-platform confirmation | Illumina microarray, adalimumab + MTX, per-sample numeric PASI, WK0/1/2/4/16 |
| **GSE74697** | pharmacodynamic (paired) | RNA-seq, 18 paired pre/post adalimumab |
| **GSE252029** | pharmacodynamic (ADA arm) | microarray, VOYAGE-1 substudy, guselkumab vs **adalimumab** comparator |

Excluded (named, with reasons): GSE11903 (etanercept), GSE117239 & GSE106992
(ustekinumab+etanercept), GSE201827 (secukinumab), GSE31652 (anti-IL-17A), GSE290870
(guselkumab), GSE151278 (blood + methylation, not skin) + 9 in-vitro/untreated/RA-blood
series summarized in a footnote row.

**Lesson:** always query ArrayExpress/BioStudies — the single best cohort (PSORT) is not
in GEO.

## Stage 3 — response splits
- PSORT discovery: n=33 → **18R / 15NR**
- PSORT refinement: n=25 → **16R / 9NR** (the `−1e-9` robust threshold corrected one
  boundary patient from an earlier 15R/10NR; this shift changed downstream headline
  numbers, e.g. CAMERA TYK2-JAK2 0.16 → 0.019 — a concrete reason the robust threshold
  matters)
- GSE85034: n=14 → **9R / 5NR**

## Stage 4 — GSVA coverage
GSVA v1.50.5, Gaussian kcdf, minSize=3. Coverage near-complete: TYK2-JAK1 **21/21** and
TYK2-JAK2 **20/20** in PSORT + both PD cohorts; **21/21** and **19/20** in GSE85034
(missing only IL36A).

## Stage 5 — statistics (headline results)
- **Primary ΔGSVA (change from baseline), endpoint NR vs R:** each patient's within-patient
  change is formed first (the paired step), then NR patients' ΔGSVA is compared to R
  patients' ΔGSVA with a **two-sample (unpaired) Wilcoxon rank-sum test** (groups are
  unequal, e.g. 9NR/16R). Direction concordant (NR>R) in **100% of cohort × signature
  tests**. Refinement endpoint ΔGSVA **+0.312** for TYK2-JAK1 (9NR/16R, rank-sum p=0.207)
  — nominal, not FDR-significant in any single cohort.
- **Cross-cohort concordance (Fisher's method, one-sided NR>R):** all-splits combined
  **p=0.048 (TYK2-JAK1)** and **p=0.049 (TYK2-JAK2)**. **Important caveat — these are
  pseudo-replicated:** the PSORT discovery and refinement cohorts are splits of the *same*
  parent dataset (`E-MTAB-14509`), so combining all three p-values via Fisher (which
  assumes independence) is optimistic. The per-cohort one-sided p-values for TYK2-JAK1 are
  ≈ **0.408 (discovery, near-null, Δ=+0.022), 0.104 (refinement, Δ=+0.312), 0.041
  (GSE85034, Δ=+0.377)** — i.e. the combined value leans heavily on the single *independent*
  cross-platform cohort (GSE85034) plus the consistent direction, not on a broad
  independent signal. Report it as a directional/descriptive summary, not a formal
  meta-analytic p; the independence-aware `--cohort-parent` option collapses the two PSORT
  splits to one per parent.
- **CAMERA (competitive, absolute endpoint):** **TYK2-JAK2 FDR-significant in 3/3 cohorts;
  TYK2-JAK1 in 2/3** (all enriched in non-responders). GSE85034 TYK2-JAK1 CAMERA FDR
  ≈ 0.058. Flagged as confounded by residual disease activity.
- **Per-gene DE (limma-voom):** **no single gene survives FDR** in any contrast (honest
  null; thousands of nominal hits at wk12).
- **Pharmacodynamic:** both TYK2 arms fall with treatment (signatures are drug-responsive),
  supporting the residual-signal interpretation. GSE74697 is a genuine paired pre/post
  design, so a **paired** test (`wilcox.test(pre, post, paired=TRUE)`) is appropriate here
  — the only place a paired test applies.

**Net framing (important):** hypothesis-strengthening directional + pathway-level
concordance with UC, **limited** by per-cohort sample sizes ~10-20× smaller than UC
VARSITY, sensitivity to a single boundary patient, non-independence of the two PSORT splits
(so the combined Fisher p is optimistic and rests largely on one independent cohort), and a
headline that also leans partly on a confounded absolute endpoint. The report presents
ΔGSVA concordance and FDR-adjusted results **side by side** and states these caveats
explicitly rather than overclaiming.

## Stage 6-7 — deliverables & validation
- PDF (15 pages): Table 1 discovery catalog → Summary + headline → Methods (incl. "which
  test produced which result") → Results (analysed-cohort Table 2, ΔGSVA Table 3, CAMERA
  Table 4, gene-DE Table 5, pharmacodynamic) → Limitations → Next steps → References.
- Four figures (PNG+SVG): NR-vs-R ΔGSVA heatmap, per-gene volcanoes, pharmacodynamic
  pre/post, ΔGSVA trajectories — each media-checked, regenerated on failure.
- Discovery-catalog CSV + rerunnable bundle (GSVA matrices, per-patient response tables,
  per-gene DE tables, stat CSVs).
- Verify-before-trust: every headline number reconciled against source CSVs; table
  numbering contiguous; all figures media-checked; PMIDs/DOIs confirmed against source
  (e.g. GSE74697 → doi:10.1186/s12864-016-3188-y; GSE252029 → PMID 39114670,
  doi:10.1016/j.xjidi.2024.100287).

## Verified reference IDs (confirmed against the run record)
- **GSE74697** — Network analysis of psoriasis; BMC Genomics 2016;17:841;
  doi:10.1186/s12864-016-3188-y.
- **GSE252029** — Guselkumab reduces ... VOYAGE-1 substudy; JID Innovations 2024;4:100287;
  PMID 39114670; NCT02207231.
- **PSORT / E-MTAB-14509** — bioRxiv 2025.07.29.666780; JID 2024
  doi:10.1016/j.xjidi.2024.100333.

## Example `run_config.json` skeleton (for `build_report.py`)
```json
{
  "meta": {
    "title": "Residual TYK2 Signaling and Adalimumab Non-Response in Psoriasis",
    "subtitle": "GSVA replication of a ulcerative-colitis TYK2 analysis in lesional skin",
    "attribution": "Generated by Biomni"
  },
  "catalog_csv": "data/psoriasis_adalimumab_catalog.csv",
  "catalog_lead": "GEO and ArrayExpress/BioStudies were searched ...",
  "catalog_caption": "Table 1. Datasets screened during discovery ...",
  "summary_paragraphs": ["..."],
  "headline": {"head": "Headline: ...", "body": "(1) ... (2) ... (3) honest caveats ..."},
  "figures": {
    "heatmap": {"path": "figures/Fig1_heatmap.png", "w": 500, "h": 240,
                 "caption": "Figure 1. NR vs R enrichment (GSVA change-from-baseline dGSVA) ..."},
    "volcano": {"path": "figures/Fig2_volcano.png", "w": 500, "h": 300,
                 "caption": "Figure 2. Per-gene DE (limma-voom) ..."},
    "pd":      {"path": "figures/Fig3_pd.png", "w": 500, "h": 300,
                 "caption": "Figure 3. Pharmacodynamic change (GSVA) ..."},
    "trajectory": {"path": "figures/Fig4_traj.png", "w": 500, "h": 300,
                 "caption": "Figure 4. Change-from-baseline dGSVA trajectories ..."}
  },
  "methods_subsections": [
    {"head": "Datasets", "paragraphs": ["..."]},
    {"head": "Gene signatures, context panel, and response", "paragraphs": ["..."]},
    {"head": "GSVA scoring and statistical testing", "paragraphs": ["..."]}
  ],
  "method_mapping": [
    "GSVA (per-sample enrichment) + Wilcoxon rank-sum on change-from-baseline dGSVA (unpaired NR-vs-R) - primary endpoint test -> Table 3, Figure 1, Figure 4; pharmacodynamic pre/post (paired Wilcoxon) -> Figure 3.",
    "Fisher's method on one-sided p-values - cross-cohort directional concordance (the p=0.048 / p=0.049 headline).",
    "CAMERA (competitive gene-set test, limma) - absolute on-treatment endpoint NR-vs-R contrast -> Table 4 (partly confounded by residual disease activity).",
    "limma-voom (per-gene DE) - Figure 2, Table 5."
  ],
  "results_blocks": [
    {"head": "Analysed adalimumab lesional-skin cohorts", "table_csv": "tables/analysed_cohorts.csv",
     "table_caption": "Table 2. Analysed cohorts (the four datasets included from Table 1).", "page_break": false},
    {"head": "The direction reproduces across all response cohorts (GSVA dGSVA)",
     "table_csv": "tables/dgsva_endpoint.csv", "table_caption": "Table 3. ... (GSVA change-from-baseline dGSVA, Wilcoxon rank-sum NR vs R).",
     "figures": [], "page_break": false},
    {"head": "Pathway-level test (CAMERA) ", "table_csv": "tables/camera_endpoint.csv",
     "table_caption": "Table 4. ... (CAMERA competitive gene-set test; absolute endpoint contrast).", "page_break": false},
    {"head": "Gene-level differential expression: the honest null (limma-voom)",
     "table_csv": "tables/gene_de_summary.csv", "table_caption": "Table 5. ... (limma-voom per-gene DE).",
     "figures": ["volcano"], "page_break": true},
    {"head": "Pharmacodynamic validation (GSVA)", "figures": ["pd"], "page_break": false},
    {"head": "Change-from-baseline trajectories (dGSVA)", "figures": ["trajectory"], "page_break": false}
  ],
  "limitations": ["Per-cohort sample sizes ~10-20x smaller than UC VARSITY ...",
                   "Headline sensitive to one boundary patient ...",
                   "CAMERA absolute endpoint partly confounded by residual disease activity ...",
                   "No single gene survives FDR ..."],
  "next_steps": ["..."],
  "references": ["[1] ...", "[2] ..."]
}
```
