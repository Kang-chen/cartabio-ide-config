---
id: "skill_ba99051fddd1d71feb9c6b74443592be"
name: consensus-disease-signature
description: "Use to derive a cross-study consensus disease expression signature from multiple bulk RNA-seq or microarray cohorts. Combines cohort-level differential expression with random-effects meta-analysis, direction consistency, control-group sensitivity checks, pathway enrichment, and core up/down gene sets."
category: "transcriptomics"
visibility: "public"
starting-prompt: "Build a consensus disease signature by meta-analyzing differential expression across several bulk-transcriptome cohorts."
---

# Consensus Expression Meta-Signature

## What this skill does

Given **two or more bulk-transcriptome cohorts** comparing a condition to a control, this
skill derives the **consensus transcriptional signature**: the genes that change in the
**same direction across independent cohorts and platforms**, quantified by a formal
**random-effects effect-size meta-analysis**. It then characterizes the signature
(GO + Reactome over-representation, Hallmark GSEA), validates it against the literature, and
assembles a self-contained **PDF report** with a visual-abstract infographic.

When cohorts use **different control groups** (e.g. histologically normal vs osteoarthritis vs
trauma), the skill does **not** silently pool them into one contrast: each cohort's control type
is recorded, the heterogeneity is flagged, and a **sensitivity meta-analysis** restricted to
non-inflammatory-control cohorts quantifies how much of the consensus signature is preserved.

The method is condition-agnostic. Only the *inputs* (which cohorts, which contrast) and
the *literature validation query* change between diseases. Nothing in the pipeline is
hard-coded to any specific disease.

## Scope

**In scope**
- Any human disease/condition with >= 2 bulk-transcriptome cohorts.
- Cohorts from **GEO** (retrieved automatically) and/or **user-supplied expression
  matrices** (proprietary cohorts, or ArrayExpress/BioStudies `E-MTAB-*` flat files fed
  through the matrix path).
- **Microarray** (limma on log2 intensities) and **RNA-seq** (limma-voom on counts).
- A **two-group contrast** (disease vs control is the default instantiation; any two
  metadata-defined groups work).

**Explicitly NOT in scope** (keep the skill focused; do not silently expand it)
- Single-study differential expression with no meta-analysis (use limma directly).
- Meta-analysis by p-value combination or vote-counting (this skill uses effect sizes only).
- Cell-type deconvolution, single-cell integration, eQTL, or network inference.
- More than two contrast groups modeled jointly (pick the two levels via config instead).
- Non-human organisms out of the box (documented extension: swap the annotation `OrgDb`
  and platform packages; not implemented by default).

## Inputs

A single **YAML config** drives everything (see `references/example_config.yaml`):
- `disease`, `output_dir`, global `contrast` (case/control values + column), `fdr`, `core_lfc`.
- `cohorts[]`: for each cohort an `id`, `source` (`geo` or `matrix`), `platform`, `type`
  (`microarray`/`rnaseq`), and how to assign the two groups (`group_column`,
  `case_values`, `control_values`, optional `filters` for tissue/timepoint).

Full field reference: `references/parameters.md`.

## Outputs (under `output_dir`)

- **`tables/summary.json`** — headline numbers + top genes (consumed by the report builder).
  Reports `n_fdr_sig` (FDR-significant, either direction) and `n_consensus` (that AND sign-consistent)
  as **distinct** keys, plus per-cohort `control_type`, `data_types`, `heterogeneous_controls`, and a
  `sensitivity` block.
- **`tables/meta_analysis_full.csv`** — every gene: pooled estimate, SE, z, p, FDR, I2,
  tau2, k, per-cohort log2FC, `direction`, `consensus`, `core`.
- **`tables/consensus_UP_genes.csv`**, **`consensus_DOWN_genes.csv`**.
- **`tables/sensitivity_noninflammatory_meta.csv`** — the meta-analysis restricted to
  non-inflammatory-control cohorts (only when >= 2 such cohorts exist; see step 7).
- **`tables/enrichment_GO_BP_{UP,DOWN}.csv`**, **`enrichment_Reactome_{UP,DOWN}.csv`**, **`GSEA_hallmark.csv`**.
  (KEGG ORA was removed — not licensed for commercial use; Reactome replaces it. See `DATA_SOURCES.md`.)
- **`figures/`** (PNG @150dpi + editable SVG): QC distributions, per-study volcano,
  cross-cohort concordance scatter, consensus heatmap, forest plot, ORA dotplots (GO + Reactome),
  Hallmark GSEA barplot, and (when a sensitivity meta ran) `sensitivity_preservation`.
- **A schematic infographic** (via `GenerateImage`) and the final **`<disease>_consensus_signature_report.pdf`**.

## Workflow

Run the R engine, generate the infographic, then build the PDF. Each step below notes its
*biological/statistical rationale* so the reasoning is transferable.

1. **Ingest each cohort.** GEO series via `GEOquery`, or a user matrix + metadata table.
   *Why:* multiple independent cohorts are the whole point — one study is underpowered and
   confounded by cohort-specific technical variation.

2. **Select samples for the contrast.** Apply `filters` (e.g. tissue == colon, visit ==
   baseline) and keep only the two contrast groups. *Why:* mixing tissues, timepoints, or
   on-treatment samples injects variance unrelated to the disease contrast. (In the UC
   reference run this meant colon-only, baseline-visit-only, and excluding dysplasia.)
   **Also set each cohort's `control_type`** (e.g. `normal`, `osteoarthritis`, `trauma`) so
   heterogeneous baselines are handled explicitly downstream (step 7b) rather than silently pooled.

3. **QC + cross-cohort duplicate check.** Inspect per-sample distributions; cross-correlate
   mean expression between every cohort pair and flag r > 0.999. *Why:* GEO frequently
   hosts **re-deposits** of the same data under different accessions; counting them twice
   fakes replication and biases the pooled estimate. (This is exactly how a duplicate of one
   UC cohort was caught and dropped.)

4. **Annotate probes -> gene symbols and collapse.** Map with the platform's Bioconductor
   `.db` package (strip the `_PM` infix for GPL13158/GPL16311); collapse multi-probe genes to
   the probe with the highest mean expression. *Why:* meta-analysis needs a shared feature
   space; SYMBOL is the common denominator; max-mean picks the most reliably measured probe.

5. **Per-cohort differential expression.** The engine dispatches on each cohort's declared
   `type`: **limma moderated t-test for `microarray`** (log2 intensities) or **limma-voom for
   `rnaseq`** (filterByExpr + TMM-normalized counts). **Retain log2FC and its standard error**
   (`stdev.unscaled * sqrt(s2.post)`). *Why:* the (log2FC, SE) pair is the effect size +
   precision the meta-analysis combines — not p-values, which are not comparable across
   platforms/sample sizes. The **report Methods text is generated from the cohorts' actual
   `type`s** (via `summary.json$data_types`) so it never mis-describes an all-RNA-seq run as
   microarray, or vice versa.

6. **Random-effects effect-size meta-analysis.** For each gene in >= 2 cohorts,
   `metafor::rma(yi=log2FC, sei=SE, method="REML")` (DerSimonian-Laird fallback);
   BH-FDR across all tested genes; record I2 heterogeneity. *Why:* random effects allow the
   true effect to vary across cohorts (biological + technical heterogeneity) and yield a
   pooled estimate with calibrated uncertainty; I2 quantifies cross-cohort consistency.

7. **Define the consensus and core sets.** `consensus = FDR < fdr AND same sign in all
   contributing cohorts`; `core = consensus AND |pooled log2FC| >= core_lfc`. *Why:*
   statistical significance alone can be driven by one cohort; requiring **direction
   consistency** is what makes the signature reproducible. Core adds an effect-size floor
   for the highest-confidence genes. **Count discipline:** `n_fdr_sig` (FDR-significant, either
   direction) and `n_consensus` (that AND sign-consistent) are **different quantities** —
   `n_consensus <= n_fdr_sig` by construction. The engine asserts this and `summary.json`/the
   report keep them as distinct, separately-labelled numbers (never substitute one for the other,
   as an earlier version's report text did).

7b. **Handle heterogeneous control groups.** If cohorts declare more than one distinct
   `control_type`, set `heterogeneous_controls = true`, record every cohort's control type, and —
   when >= 2 cohorts share a **non-inflammatory** control (`noninflammatory_control_types`, default
   `normal`/`trauma`/`healthy`/`control`) — re-run the **same** effect-size meta-analysis on that
   subset and report the fraction of primary consensus genes preserved (direction-consistent &
   FDR < fdr) in the subset. *Why:* different controls are not equivalent baselines (e.g. OA synovium
   carries its own inflammation), so pooling them silently can conflate case-vs-normal with
   case-vs-other-disease. Random effects absorb control-type differences as heterogeneity, and the
   sensitivity subset checks that the signature is disease-driven, not comparator-driven. If only one
   control type is present this step is a no-op; if heterogeneity exists but < 2 non-inflammatory
   cohorts, the heterogeneity is still flagged and caveated but the subset meta is skipped with a reason.

8. **Functional enrichment.** ORA (`enrichGO` BP simplified at 0.7 + `ReactomePA::enrichPathway`) on core
   up/down **separately**, with the **universe = all meta-tested genes**; Hallmark **GSEA**
   (`fgsea`) on the z-score-ranked list. *Why:* separating directions keeps up/down biology
   distinct; the correct background is the tested-gene universe, not the whole genome; GSEA
   captures coordinated shifts ORA misses. **KEGG ORA was removed** because the KEGG API is not
   licensed for commercial use; **Reactome** (open, commercially usable) replaces it — see
   `DATA_SOURCES.md`. **Load only Hallmark / small MSigDB
   subcollections** — loading full C2 (~7000 sets) caused OOM in the reference run.

9. **Literature validation (`LiteratureSearch`).** Search e.g. *"<disease> transcriptome
   meta-analysis marker genes"*; extract explicitly named up/down markers from returned
   records; report the fraction recovered **with correct direction**. Save records to
   `execution_trace/references.jsonl` for the report. *Why:* independent confirmation that
   the signature recapitulates known biology. **If few named markers are recoverable, say so
   — never invent genes or citations** (fabrication guard).

10. **Report.** Generate a **schematic infographic** with `GenerateImage` (the workflow +
    headline numbers as a visual abstract), then build the **PDF** with `scripts/build_report.py`
    following the `pdf-report-generation` skill: executive summary, introduction, methods,
    results (all figures + top-gene tables), conclusions, suggested next steps, and references.

## How to run

```bash
# 0. One-time deps (see Environment). metafor + any missing platform .db packages.
# 1. Core analysis  (writes tables/ + figures/ + summary.json)
Rscript scripts/run_meta_signature.R  my_config.yaml

# 2. Literature validation + infographic are agent-driven:
#    - call LiteratureSearch for "<disease> ... marker genes", check top genes' concordance
#    - call GenerateImage to render a schematic visual abstract PNG (workflow + headline numbers)

# 3. Assemble the PDF
python scripts/build_report.py \
   --results /mnt/results \
   --infographic /mnt/results/figures/infographic.png \
   --out /mnt/results/<disease>_consensus_signature_report.pdf
```

Then **verify every figure and the final PDF** with a `Read` media output check (no
black-box glyphs, figures embedded, nothing clipped or blank) before delivering.

## Scientific caveats

- **Human default.** Uses `org.Hs.eg.db` and human platform packages. Other species require
  swapping the annotation packages (documented, not automated).
- **Two-group contrast only.** For designs with >2 groups, choose the two comparison levels
  in the config; this skill does not fit multi-level or interaction models.
- **Heterogeneous control groups.** When cohorts use different controls (e.g. normal vs OA vs
  trauma), the pooled estimate is an average over non-equivalent baselines; genes separating the
  case specifically from an inflammatory comparator can be attenuated. Always set `control_type`
  per cohort, read the `heterogeneous_controls` flag, and interpret the non-inflammatory-control
  sensitivity result. Prefer a consistent control where the data allow it.
- **Effect-size meta-analysis only.** No p-value combination, vote-counting, or single-study mode.
- **Independence matters.** Always run the duplicate check; drop re-deposited cohorts. Cohorts
  from the same lab/platform may still share batch structure — interpret low I2 accordingly.
- **Batch/covariate adjustment** is per-cohort via the design; cross-cohort batch is handled by
  the meta-analysis (random effects), not by merging raw data (avoid naive matrix concatenation).
- **MSigDB memory.** Load only Hallmark or small subcollections; full C2 can OOM.
- **No fabrication.** Literature validation reports exactly what was found; if markers or
  citations are not recoverable, state that rather than inventing them.

## Environment & dependencies

- **Pre-installed (verified):** R — `limma`, `clusterProfiler`, `org.Hs.eg.db`, `fgsea`,
  `msigdbr`, `GEOquery`, `ComplexHeatmap`, `ggplot2`. Python — standard scientific stack.
  Skills — `pdf-report-generation`.
- **Install if missing:** R — `metafor`, `edgeR` (RNA-seq), `ReactomePA` + `reactome.db`
  (Reactome ORA; replaces KEGG), `patchwork`, `svglite`, `reshape2`,
  `circlize`, and the platform annotation packages you need (`hgu133plus2.db`,
  `hugene10sttranscriptcluster.db`, `illuminaHumanv4.db`, ...). Python — `reportlab`, `pypdf`, `pillow`.
  Install R packages to a persistent lib: `.libPaths(c("/workspace/.Rlib", .libPaths()))`.
  (`reactome.db` is a ~1 GB annotation download — budget a few minutes on first install.)
- **Biomni tools used:** `LiteratureSearch` (validation + references), `GenerateImage`
  (infographic), `Read` media output check (figure/PDF QC).
- **Optional datalake reference:** `LINCS1000/RNAseq_transcriptomics_genesets/*.gmt`
  (`disease_signatures-v1.0.gmt`, `human_GEO.gmt`) as a secondary enrichment reference.

### Commercial-use restrictions (needs_commercial_review)

The following runtime dependencies have commercial-use restrictions that must be
reviewed before any non-academic deployment. They are **not** cleared for commercial
use by this skill and require a separate license or a substitute pathway.

- **KEGG — REMOVED (was not licensed for commercial use).** Earlier versions ran
  `clusterProfiler::enrichKEGG()`, which downloads KEGG pathway annotations from
  `rest.kegg.jp` at runtime; the KEGG API is "made available only for academic use by
  academic users" and "non-academic use of KEGG requires a commercial license" (KEGG
  copyright page, updated Oct 1, 2024; license via Pathway Solutions). **KEGG ORA has been
  removed** from this skill and replaced with **Reactome** (`ReactomePA::enrichPathway`),
  which is open and commercially usable (see `DATA_SOURCES.md`). No KEGG API call remains and
  no `enrichment_KEGG_*` table is produced; the outputs are now `enrichment_Reactome_{UP,DOWN}.csv`.
- **MSigDB Hallmark gene sets (via `msigdbr`).** The Hallmark (H) collection used for
  GSEA appears to be CC-BY-4.0 on individual gene-set pages, but the broader MSigDB
  has mixed licensing — the Broad Institute notes that "a significant portion of the
  data requires a commercial license for any commercial application." This skill loads
  only `msigdbr(species='Homo sapiens', category='H')`. **For commercial deployments:**
  confirm that the Hallmark collection specifically carries no additional restriction
  beyond CC-BY-4.0, or obtain a MSigDB commercial license. Do not assume the full
  MSigDB is cleared because Hallmark appears to be.

No other material dependency in this skill has a known commercial-use prohibition
(metafor: GPL >=2; limma, clusterProfiler, GEOquery, fgsea, ReactomePA, Bioconductor
annotation packages incl. reactome.db: Artistic-2.0 / MIT; Reactome pathway data: CC0
(the Reactome knowledgebase is released to the public domain, CC-BY for attribution of
figures); GO: CC-BY 4.0; reportlab: BSD; GEO data: no restrictions per NCBI).
However, "no prohibition found" is not the same as explicit commercial clearance —
verify each dependency independently for your deployment context. See `DATA_SOURCES.md`
for the full data-source + license inventory.

## Compute

Bulk meta-analysis is light: the reference UC run (3 cohorts, ~490 samples, meta over
21,358 genes) completed in ~100 s. The **default sandbox handles <= ~5 microarray cohorts**;
for many cohorts or RNA-seq counts use a **16 GB worker** (`ManageMachine`). No HPC needed.
**Write tables/figures to `/mnt/results` incrementally** — the reference run survived two
container restarts because outputs were saved as they were produced.

## Trigger examples

- "Build a consensus RNA expression signature for <disease> from GEO series GSE_A, GSE_B, GSE_C."
- "Meta-analyze differential expression across these psoriasis cohorts and give me the consensus up/down genes."
- "I have three expression matrices for <condition>; make a cross-cohort meta-analysis signature and a PDF report."
- "Integrate these datasets into one disease signature with enrichment and literature validation."
