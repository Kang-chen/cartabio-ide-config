---
id: "skill_9220a3a4f3fd3052b91800e13683d7cf"
name: deconvolution-bulk-rnaseq
description: "Use to estimate cell-type proportions in bulk RNA-seq from an annotated single-cell reference and test composition differences between groups or time points. Supports BayesPrism, DWLS, and optional MuSiC/Bisque with cross-method concordance."
category: "transcriptomics"
visibility: "public"
starting-prompt: "Deconvolve my bulk RNA-seq into cell-type proportions using a single-cell reference and test which cell types differ between my groups. Generate a PDF report with intro, methods, results, conclusions and figures."
---

# Bulk RNA-seq Cell-Type Deconvolution (single-cell reference)

## When to Use This Skill

**Use when:**
- You have **bulk RNA-seq** (genes x samples) and want to estimate **cell-type proportions** within each sample
- You have (or can build) an **annotated single-cell reference** covering the cell types in the bulk
- You want to test **which cell types differ between groups** (e.g., responders vs non-responders), optionally over **timepoints**
- You need a **commercially-licensed** workflow on Biomni — CIBERSORTx-style estimation without the CIBERSORTx license

**Don't use when:**
- You have single-cell data already (no deconvolution needed — use `scrnaseq-scanpy-core-analysis`)
- You want **spatial spot** deconvolution (use `spatial-transcriptomics`)
- You want differential expression, not composition (use `bulk-rnaseq-counts-to-de-deseq2`)
- You have no reference and cannot build one (reference-free methods are out of scope)

## Installation

**The license is the design constraint — every method below permits commercial use.** See [references/license-notes.md](references/license-notes.md).

| Package | Source | License | Commercial Use | Installation |
|---------|--------|---------|----------------|--------------|
| BayesPrism | GitHub | GPL-3 | Permitted | `remotes::install_github("Danko-Lab/BayesPrism/BayesPrism")` |
| DWLS | CRAN | GPL-2 | Permitted | `install.packages("DWLS")` |
| MuSiC *(optional)* | GitHub | GPL-3 | Permitted | `remotes::install_github("xuranw/MuSiC")` |
| BisqueRNA *(optional)* | CRAN | GPL-3 | Permitted | `install.packages("BisqueRNA")` |
| SingleCellExperiment | Bioc | GPL-3 | Permitted | `BiocManager::install("SingleCellExperiment")` |
| zellkonverter *(.h5ad input)* | Bioc | MIT | Permitted | `BiocManager::install("zellkonverter")` |
| Seurat *(.rds input)* | CRAN | MIT | Permitted | `install.packages("Seurat")` |
| ggplot2 | CRAN | MIT | Permitted | `install.packages("ggplot2")` |
| ggprism | CRAN | GPL-3 | Permitted | `install.packages("ggprism")` |
| lmerTest *(optional, longitudinal)* | CRAN | GPL | Permitted | `install.packages("lmerTest")` |
| SimBu *(eval only)* | Bioc | GPL-3 | Permitted | `BiocManager::install("SimBu")` |
| **CIBERSORTx** | --- | **non-commercial** | **DO NOT USE** | Stanford license; off-limits on Biomni |
| **EPIC** | --- | **academic-only** | **DO NOT USE** | for-profit needs separate license |
| **BSeq-sc** | --- | **wraps CIBERSORT** | **DO NOT USE** | Inherits CIBERSORT's restriction |

**Optional reference builder (Python):** `cellxgene-census`, `scanpy`, `anndata` — only needed for `scripts/census_reference.py`.

**Why no `omnideconv`:** Earlier versions of this skill routed methods through the `omnideconv` framework. In practice the direct-call paths (BayesPrism, DWLS, MuSiC, Bisque) do the real work and `omnideconv`'s changing kwarg signatures added install bulk and noisy fallback messages. The rebuild calls the methods directly.

**NEVER use CIBERSORTx, EPIC, or BSeq-sc.** `run_deconvolution()` hard-errors if requested — this is intentional, not a bug. See [references/license-notes.md](references/license-notes.md).

## Inputs

**Required:**
- **Bulk matrix** (genes x samples) — `.csv` / `.tsv` / `.rds`; **gene symbols as row names** (must match the reference's gene IDs). Linear or log scale (auto-detected; converted to linear).
- **Annotated single-cell reference** — `.h5ad` (CELLxGENE/AnnData), Seurat `.rds`, or SingleCellExperiment `.rds`, with a **`cell_type`** annotation column (and ideally **`donor_id`** for MuSiC/Bisque).

**Optional (for group testing):**
- **Sample metadata** — `sample_id`, `group`, and optionally `timepoint`, `subject_id`.

**Accepted sources for the reference:**
- One-step Census fallback: `scripts/census_reference.py` (pulls a CELLxGENE Census slice → `.h5ad`)
- An annotated `.h5ad` / Seurat `.rds` / SCE `.rds` you already have
- Example data (auto-generated synthetic immune cohort with known proportions)

## Outputs

**All user-facing outputs land in `/mnt/results/deconvolution/` so the Biomni results panel surfaces them automatically. Use a custom `output_dir` only if you have a reason to.**

**Proportion tables (CSV):**
- `proportions_<method>.csv` — per-method cell-type fractions (samples x cell type)
- `consensus_proportions.csv` — cross-method mean fractions
- `method_concordance.csv` — pairwise Pearson r (overall + per cell type); flags method-fragile types
- `composition_summary.csv` — mean +/- SD fraction per group (x timepoint)
- `proportion_contrasts.csv` — group contrasts with BH-FDR (+ `mixed_model_anova.csv` if longitudinal)
- `ground_truth_recovery.csv` — per-cell-type r + RMSE vs known truth (example data only)

**Figures (PNG + SVG):**
- `composition_stacked` — mean composition per group x timepoint
- `celltype_boxplots` — per-cell-type fractions by group (top differential types)
- `composition_trajectories` — mean fraction over timepoints by group (if timepoints present)
- `method_concordance_scatter` — first method vs second method, points = (sample, cell type)
- `ground_truth_recovery` — estimated vs true proportions (example data only)

**Analysis object (RDS):**
- `cell_type_deconvolution.rds` — complete results (all proportions, consensus, concordance, contrasts, metadata)
  - Load with: `res <- readRDS('/mnt/results/deconvolution/cell_type_deconvolution.rds')`
  - Required for: downstream functional enrichment / target prioritisation, re-plotting, re-testing

**Reports:**
- `analysis_report.md` — markdown summary (always generated by `export_all()`)
- `analysis_report.pdf` — **you (the agent) generate this** comprehensive PDF (Intro, Methods, Results with embedded figures, Conclusions, References)

**PDF style rules:**
- **US Letter page size (8.5 x 11 in)** — always set page dimensions explicitly; do not rely on library defaults
- **No Unicode superscripts** — use `3.36e-06` or `3.36 x 10^(-6)`, not Unicode superscript chars (they render as boxes in PDF fonts)
- **No half-empty pages** — group headings with their content; only page-break before major sections (Results, Conclusions)
- **Figures >=80% page width** — multi-panel figures must be large enough to read; never embed below 50% width

## Clarification Questions

**ALWAYS ask Question 1 FIRST. Do not proceed before the user answers.**

### 1. Input Files (ASK THIS FIRST):
- **Do you have a bulk RNA-seq matrix (genes x samples)?** Provide the path (`.csv`/`.tsv`/`.rds`) and the **scale** (raw counts / TPM / log2).
- **Do you have an annotated single-cell reference?** Provide the path (`.h5ad` / Seurat `.rds` / SCE `.rds`), or:
  - **Build one** from the CELLxGENE Census (`scripts/census_reference.py`), **or**
  - **Use example data?** — a synthetic immune cohort (6 cell types; 24 bulk samples = responder vs non-responder x baseline/week12) with **known ground-truth proportions** for recovery checks.

> **IF EXAMPLE DATA SELECTED:** All parameters are pre-defined (human immune cell types, two groups, two timepoints, known truth). Skip to Question 4, then proceed to Step 1. Do NOT ask Questions 2-3.

### 2. Reference & scale (own data only):
- Which metadata column holds the cell-type labels? (default `cell_type`)
- Is the bulk on raw/TPM (linear) or log2 scale? (auto-detected, but confirm if unsure)

### 3. Grouping (own data only):
- Which metadata column defines the groups to contrast (e.g., `response`)?
- Is there a `timepoint` and a `subject_id` (enables the longitudinal mixed model)?

### 4. Methods (structured — works for demo and own data):
- a) **BayesPrism + DWLS** (recommended default — robust + collinear-aware, with concordance)
- b) Add **MuSiC + Bisque** for a 4-method concordance panel (only when reference has >=3 donors)
- c) BayesPrism only (fastest single method)
- > CIBERSORTx and EPIC are **excluded by license** and never offered.

## Standard Workflow (R)

**MANDATORY: USE SCRIPTS EXACTLY AS SHOWN — DO NOT WRITE INLINE CODE**

**Step 1 — Load data:** (`~5 s`)
```r
source("scripts/load_data.R")
ex <- load_example_data()                       # synthetic cohort with known truth
reference <- ex$reference; bulk <- ex$bulk; metadata <- ex$metadata
# --- OR your own data: ---
# reference <- load_reference("reference.h5ad", cell_type_col = "cell_type")
# bulk      <- load_bulk("bulk_counts.csv")
# metadata  <- read.csv("metadata.csv")         # sample_id, group, [timepoint, subject_id]
```

**Step 2 — Deconvolve + test composition:** (`~3-8 min on 8 cores; BayesPrism dominates`)
```r
source("scripts/run_deconvolution.R")
deconv <- run_deconvolution(bulk, reference, methods = c("bayesprism", "dwls"))
# n_cores auto-detects (capped at 8); pass n_cores = N to override.

source("scripts/deconv_stats.R")
contrasts <- proportion_contrasts(deconv$consensus, metadata, group_col = "group",
                                  timepoint_col = "timepoint", subject_col = "subject_id")
```
**DO NOT write inline deconvolution or statistics code. Just source the scripts and call the functions.**

**Step 3 — Generate visualizations:** (`~10-30 s`)
```r
source("scripts/deconv_plots.R")
generate_all_plots(deconv, metadata = metadata, contrasts = contrasts,
                   ground_truth = ex$ground_truth,
                   output_dir = "/mnt/results/deconvolution")
```
**DO NOT write inline plotting code (ggplot, ggsave). Just use the script.**
*(Omit `ground_truth =` for your own data — it's for the example's recovery plot.)*

**Step 4 — Export results:** (`~5 s`)
```r
source("scripts/export_results.R")
export_all(deconv, metadata = metadata, contrasts = contrasts,
           ground_truth = ex$ground_truth,
           output_dir = "/mnt/results/deconvolution")
```
**DO NOT write custom export code. Use `export_all()`.** Then **write the comprehensive `analysis_report.pdf`** (see PDF style rules above).

**VERIFICATION — You should see:**
- After Step 1: `"[OK] Example data loaded successfully!"` (or `"[OK] Reference loaded successfully!"` + `"[OK] Bulk loaded successfully!"`)
- After Step 2: `"[OK] Deconvolution completed!"` then `"[OK] Proportion contrasts completed!"`
- After Step 3: `"[OK] All plots generated successfully!"`
- After Step 4: `"=== Export Complete ==="`

**IF YOU DON'T SEE THESE:** You wrote inline code. Stop and use `source()`.

**CRITICAL — DO NOT:**
- **Write inline deconvolution code** -> **STOP: Use `run_deconvolution()`**
- **Write inline statistics/plotting code** -> **STOP: Use `proportion_contrasts()` / `generate_all_plots()`**
- **Write custom export code** -> **STOP: Use `export_all()`**
- **Use CIBERSORTx or EPIC** -> non-commercial licenses; `run_deconvolution()` rejects them
- **Try to install svglite** -> scripts handle SVG fallback automatically (writes to /workspace then copies to /mnt/results when the output dir is S3-FUSE-backed)

**IF SCRIPTS FAIL — Script Failure Hierarchy:**
1. **Fix and Retry (90%)** — Install the missing package, re-run the script
2. **Modify Script (5%)** — Edit the script file itself, document changes
3. **Use as Reference (4%)** — Read the script, adapt the approach, cite the source
4. **Write from Scratch (1%)** — Only if genuinely impossible, explain why

**NEVER skip directly to writing inline code without trying the script first.**

## Python entry point (optional)

Python-first agents can run the same workflow via a single subprocess wrapper that returns parsed CSVs as pandas DataFrames. No `rpy2` dependency.

```python
import sys; sys.path.insert(0, "scripts")
from deconvolution import run_deconvolution, load_example

# load_example() returns kwargs ready to splat into run_deconvolution().
paths = load_example(output_dir="/mnt/results/deconvolution")     # writes example inputs + returns paths

res = run_deconvolution(
    **paths,                                                       # bulk_path, reference_path, metadata_path, ground_truth_path
    output_dir="/mnt/results/deconvolution",
    methods=["bayesprism", "dwls"],
    group_col="group",
    timepoint_col="timepoint",
    subject_col="subject_id",
)
print(res["consensus"].head())                                     # pd.DataFrame
print(res["contrasts"].query("significant").head())
```

Under the hood: `subprocess.run(["Rscript", "scripts/run_full_workflow.R", ...])`. The R workflow writes the same CSVs/PNGs/RDS as the R API; the Python wrapper parses them into `dict[str, pd.DataFrame]`. Logs from R stream through unchanged.

## Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| **A bulk cell type is missing from the reference** | Reference doesn't cover all populations in the bulk | **Most important failure mode** — biases *all* methods. Rebuild the reference to include every expected cell type (see [references/parameter-tuning.md](references/parameter-tuning.md)) |
| **Proportions look random / all methods disagree** | Log-scale bulk fed as linear, or gene-ID mismatch | Confirm scale (script auto-detects & exponentiates log2); ensure bulk and reference use the **same gene IDs** (symbols vs Ensembl) |
| **"only N shared genes" warning** | Gene-ID type mismatch bulk ↔ reference | Convert one side so both use gene **symbols** (the reference builder sets symbols as `var_names`) |
| **BayesPrism slow** | Single-core or many reference cells | `run_deconvolution(..., n_cores = 8)` (default auto-detects), `max_cells_per_type = 200` to downsample further |
| **A cell type flips across methods** | Closely-related / collinear types | Reported as **method-fragile** in `method_concordance.csv` — interpret with caution (DWLS handles collinearity best) |
| **CIBERSORTx/EPIC requested** | Non-commercial license | Excluded by design; `run_deconvolution()` errors. Use BayesPrism/DWLS/MuSiC/Bisque |
| **Metadata column missing (`group`)** | Default `group_col = "group"` doesn't match user's metadata | Pass `group_col = "<your_column>"` explicitly to `proportion_contrasts()` and `generate_all_plots()` |
| **Auto-picked group_levels surprise** | `group_col` has >2 levels; defaults to two most frequent | Pass `group_levels = c("A", "B")` to `proportion_contrasts()` to lock the contrast |
| **SVG export error "svglite required"** | Missing optional dependency | `generate_all_plots()` handles the fallback to base-R `svg()` automatically. DO NOT install svglite manually |
| **Mixed model skipped** | `lmerTest` missing or no `subject_id`/`timepoint` | Optional — Wilcoxon + BH-FDR still run. `install.packages("lmerTest")` to enable |
| **BisqueRNA returns flat 1/K proportions** | Reference has near-equal cell-type counts across *all* batch donors (no inter-donor variance for `ReferenceBasedDecomposition` to regress on) | Expected on very balanced reference designs (including the bundled synthetic example). On real scRNA-seq references with natural inter-donor variation, Bisque works as designed. If you see this on real data, check `batch_col` actually varies — e.g. set `batch_col = "donor_id"` only when donors differ in composition |

## Suggested Next Steps

After deconvolution, consider:

1. **Functional enrichment** — feed the cell types that shift between groups into `functional-enrichment-from-degs`
2. **Target prioritisation** — connect composition changes to candidate targets / pathways
3. **Pseudobulk DE within a cell type** — combine with `bulk-rnaseq-counts-to-de-deseq2` to ask whether a population also changes *expression*, not just abundance
4. **Cell-cell communication** — reuse the same single-cell reference in `cell-cell-communication`
5. **Longitudinal modelling** — re-run `proportion_contrasts(..., mixed_model = TRUE)` for `prop ~ group*timepoint + (1|subject)`

## Related Skills

| Skill | Relationship |
|-------|-------------|
| `scrnaseq-scanpy-core-analysis` / `scrnaseq-seurat-core-analysis` | **Upstream** — QC + annotate the reference (`cell_type` labels) |
| `omics-dataset-retrieval` / `bulk-rnaseq-counts-to-de-deseq2` | **Upstream** — source the bulk matrix |
| `cell-cell-communication` | Complementary — same reference, intercellular signalling |
| `functional-enrichment-from-degs` | **Downstream** — interpret the cell types that change |
| `spatial-transcriptomics` | Alternative — spatial spot deconvolution |
| `pdf-report-generation` | Used by the agent to assemble `analysis_report.pdf` |

## Validation Scope

**Bundled tests are self-consistency checks, not real-world accuracy validation.**

The skill ships with `assets/eval/simple_test.R` and `assets/eval/simbu_ground_truth.R`. Both run against the bundled synthetic immune cohort (`.simulate_immune_data()`), and SimBu builds its pseudobulk from the same synthetic reference. Reported Pearson r values (typically > 0.99) reflect the pipeline correctly recovering proportions it itself generated -- they do not certify accuracy on real bulk RNA-seq cohorts.

What the bundled tests **do** prove:
- Pipeline integration is intact (load -> deconvolve -> contrasts -> figures -> export).
- The bayesprism+dwls default panel agrees on the engineered biology (Monocyte up, CD8_T down in non-responders at week12).
- Cross-method concordance (r > 0.95 on the example) holds across method changes -- a real regression signal.
- The SimBu acceptance threshold (`r > 0.8`) protects against pipeline regressions but not modelling assumptions.

What they do **not** prove:
- Absolute accuracy on real cohorts where ground truth comes from flow cytometry, mass cytometry, or an orthogonal reference.
- Robustness when the bulk contains cell types absent from the reference (the single largest failure mode -- see Common Issues).
- Cross-dataset generalisation when the single-cell reference is built from a different tissue, donor, or platform than the bulk.

### Per-method coverage in the validated build

All four optional methods were exercised end-to-end against the bundled synthetic cohort. Behaviour on this example:

| Method | Wallclock | Concordance vs BayesPrism | Per-sample rowsum | Notes |
|--------|-----------|---------------------------|-------------------|-------|
| BayesPrism | ~113 s | -- (anchor) | 1.000 | Default panel member |
| DWLS | ~23 s | r = 0.993 | 1.000 | Default panel member |
| MuSiC | ~1 s | r = 0.993 | 1.000 | Tracks DWLS r=1.000 on this example |
| BisqueRNA | < 1 s | r = 0.607 | 1.000 | Returns ~uniform 1/K on this very balanced reference -- see Common Issues |

The default panel (`bayesprism + dwls`) is the recommendation. MuSiC and BisqueRNA stay in the optional panel; both install and run, but treat BisqueRNA output cautiously when the single-cell reference has near-equal cell counts across every donor.

**For publication-grade use**, validate against (a) an external cohort with experimental cell-type proportions (e.g. PBMC fractions from flow), or (b) pseudobulk built from an *independent* scRNA-seq dataset of the same tissue (not the same dataset used as the reference). Pass your own truth table as the `ground_truth` argument to `generate_all_plots()` and `export_all()` (same shape as `ex$ground_truth`: `sample_id` column + one column per cell type, fractions summing to ~1) -- the existing recovery scatter and `ground_truth_recovery.csv` will be computed against it. The Avila Cobos et al. 2020 benchmark paper is the canonical reference.

## References

- Avila Cobos F, et al. **Benchmarking of cell type deconvolution pipelines for transcriptomics data.** *Nat Commun.* 2020;11:5650.
- Chu T, et al. **Cell type and gene expression deconvolution with BayesPrism.** *Nat Cancer.* 2022;3:505-517.
- Tsoucas D, et al. **Accurate estimation of cell-type composition from gene expression data (DWLS).** *Nat Commun.* 2019;10:2975.
- Wang X, et al. **Bulk tissue cell type deconvolution with multi-subject single-cell expression reference (MuSiC).** *Nat Commun.* 2019;10:380.
- Jew B, et al. **Accurate estimation of cell composition in bulk expression through robust integration of single-cell information (Bisque).** *Nat Commun.* 2020;11:1971.
- Method selection: [references/method-selection-guide.md](references/method-selection-guide.md)
- License rationale: [references/license-notes.md](references/license-notes.md)
- Reference & preprocessing tuning: [references/parameter-tuning.md](references/parameter-tuning.md)
