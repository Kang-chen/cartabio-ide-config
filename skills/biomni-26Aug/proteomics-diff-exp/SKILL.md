---
id: "skill_08c0e01956d643148a85f5334cfd7abd"
name: "proteomics-diff-exp"
description: "Use to test differential protein abundance in mass-spectrometry proteomics data with limma and DEqMS, including PSM-aware variance estimation."
category: "proteomics_metabolomics"
visibility: "public"
starting-prompt: "Perform differential protein expression analysis on my proteomics mass spectrometry data."
---

# Proteomics Differential Expression (limma + DEqMS)

Differential protein expression analysis for TMT/LFQ mass spectrometry proteomics data using limma linear models with DEqMS PSM-count-aware variance correction.

## When to Use This Skill

Use this skill when you have:
- ✅ **Protein quantification data** from TMT or LFQ mass spectrometry
- ✅ **PSM/peptide counts per protein** (for DEqMS variance correction)
- ✅ **Biological replicates** (≥2 per condition, ≥3 recommended)
- ✅ Need for **PSM-aware statistical testing** (improved power over standard limma)

**Don't use this skill for:**
- ❌ RNA-seq data → use bulk-rnaseq-counts-to-de-deseq2
- ❌ Metabolomics data → different normalization/statistics needed
- ❌ Pre-computed fold changes without raw intensities

## Quick Start (Example Data)

**Test this skill with real TMT proteomics data in ~2 minutes:**

```r
source("scripts/load_example_data.R")
data <- load_example_data()    # Auto-downloads A431 TMT 10-plex data (~30s)
psm_data <- data$psm_data      # 316,726 PSMs × 10 TMT channels
metadata <- data$metadata       # 4 conditions: ctrl, miR191, miR372, miR519

# Run complete workflow
source("scripts/basic_workflow.R")  # Creates fit_deqms, deqms_results + prints summary
```

**What you get:**
- **Dataset:** A431 human epidermoid carcinoma cells treated with miRNAs (TMT 10-plex)
- **Comparison:** miR372 vs ctrl (3 vs 3 replicates)
- **Expected results:** ~9,000 proteins quantified, significant DE proteins at adj.p < 0.05

**For your own data:** Replace data loading with your protein intensity matrix and metadata (see [Inputs](#inputs) section).

## Installation

**Core packages (required):**
```r
options(repos = c(CRAN = "https://cloud.r-project.org"))
if (!require('BiocManager', quietly = TRUE)) install.packages('BiocManager')
BiocManager::install(c('limma', 'DEqMS', 'ExperimentHub'))
```

**Visualization packages (required):**
```r
install.packages(c('ggplot2', 'ggprism', 'ggrepel', 'circlize', 'matrixStats'))
BiocManager::install('ComplexHeatmap')
```

**Optional packages:**
```r
install.packages(c('rmarkdown', 'knitr'))        # fallback script PDF report
BiocManager::install(c('impute', 'vsn'))          # kNN imputation, VSN normalization
```

| Software | Version | License | Commercial Use | Installation |
|----------|---------|---------|----------------|--------------|
| limma | ≥3.50.0 | GPL (≥2) | ✅ Permitted | `BiocManager::install('limma')` |
| DEqMS | ≥1.12.0 | LGPL | ✅ Permitted | `BiocManager::install('DEqMS')` |
| ExperimentHub | ≥2.0.0 | Artistic-2.0 | ✅ Permitted | `BiocManager::install('ExperimentHub')` |
| ggplot2 | ≥3.4.0 | MIT | ✅ Permitted | `install.packages('ggplot2')` |
| ggprism | ≥1.0.3 | GPL (≥3) | ✅ Permitted | `install.packages('ggprism')` |
| ggrepel | ≥0.9.0 | GPL-3 | ✅ Permitted | `install.packages('ggrepel')` |
| ComplexHeatmap | ≥2.10.0 | MIT | ✅ Permitted | `BiocManager::install('ComplexHeatmap')` |
| circlize | ≥0.4.15 | MIT | ✅ Permitted | `install.packages('circlize')` |
| matrixStats | ≥0.60.0 | Artistic-2.0 | ✅ Permitted | `install.packages('matrixStats')` |
| rmarkdown | ≥2.20 | GPL-3 | ✅ Permitted | Optional |

**Note:** Scripts automatically generate both PNG and SVG formats. SVG export uses base R svg() device (always available) or svglite if installed. No additional setup needed.

## Inputs

**Required:**
- **Protein intensity matrix**: Rows = proteins, Columns = samples
  - PSM-level table with gene/protein column (recommended — enables medianSweeping aggregation)
  - OR protein-level intensities (log2 or raw)
- **Sample metadata**: data.frame with `condition` column

**Optional but recommended:**
- **PSM/peptide counts per protein** (critical for DEqMS variance correction)

**Supported formats:** MaxQuant proteinGroups.txt, Proteome Discoverer export, generic CSV/TSV

## Outputs

**Result tables (CSV):**
- `all_results.csv` — Full DEqMS results (logFC, sca.P.Value, sca.adj.pval, count)
- `significant_results.csv` — Filtered by adjusted p-value and fold change
- `normalized_protein_matrix.csv` — Log2 normalized protein intensities
- `psm_counts.csv` — PSM counts per protein
- `top100_proteins.csv` — Top 100 by DEqMS adjusted p-value

**Analysis objects (RDS):**
- `analysis_object.rds` — Complete analysis object for downstream skills
  - Load with: `obj <- readRDS('results/analysis_object.rds')`
  - Contains: fit_deqms, deqms_results, protein_matrix, metadata, psm_counts

**Plots (PNG + SVG):**
- `intensity_distribution` — Before/after normalization boxplots
- `missing_values_heatmap` — Missing value pattern across samples
- `pca_plot` — PCA colored by condition
- `sample_correlation_heatmap` — Pearson correlation between samples
- `volcano_plot` — Differential expression with labeled top hits
- `ma_plot` — Log2 fold change vs average expression
- `variance_psm_plot` — DEqMS variance vs PSM count relationship

**Reports:**
- `analysis_report.pdf` — final PDF report. When the user requests a PDF report, use the `pdf-report-generation` skill to create the final report from `analysis_report.md`, exported CSVs, and generated figures. The RMarkdown PDF path in `scripts/generate_report.R` is a fallback when `pdf-report-generation` is unavailable.
- `analysis_report.md` — Markdown report (always generated)

## Report Packaging

When the user requests a PDF report, run the analysis first, then load and use
the Biomni `pdf-report-generation` skill for the final PDF deliverable. Build
the PDF from this skill's markdown summary, result tables, and generated figures,
and save it using the PDF filename listed in Outputs, or a stable descriptive filename when Outputs does not define one.

Keep this skill focused on scientific workflow and artifact content. Do not add
custom figure appearance or report layout instructions here; those are handled
by the platform prompt and dedicated reporting skills. If `pdf-report-generation`
is unavailable, use the packaged markdown/HTML/script fallback when present and
clearly disclose the fallback.

## Clarification Questions

### 1. **Input Files** (ASK THIS FIRST):
- Do you have proteomics data files to analyze?
  - If uploaded: What format? (MaxQuant proteinGroups.txt / Proteome Discoverer / CSV)
  - Expected: protein intensity matrix + sample metadata
  - **Or use example data?** TMT 10-plex A431 cancer cell line dataset (auto-downloads ~30s)

### 2. **Analysis Options** (structured):
- *(If using example data)* The demo dataset contains A431 human cancer cells treated with miRNAs (3 ctrl + 3 miR372 replicates). Choose analysis mode:
  - a) Standard analysis with default comparison miR372 vs ctrl (recommended)
  - b) Custom comparison (miR191 vs ctrl or miR519 vs ctrl)
- *(If using your own data)* Which conditions to compare? (e.g., Treatment-Control)

### 3. **Thresholds:**
- a) Standard: adjusted p-value < 0.05, |log2FC| > 0.58 / 1.5-fold change (recommended)
- b) Relaxed: adjusted p-value < 0.1, |log2FC| > 0 (any fold change)
- c) Stringent: adjusted p-value < 0.01, |log2FC| > 1 (2-fold change)

## Standard Workflow

🚨 **MANDATORY: USE SCRIPTS EXACTLY AS SHOWN - DO NOT WRITE INLINE CODE** 🚨

**Step 1 - Load data:**
```r
source("scripts/load_example_data.R")
data <- load_example_data()
psm_data <- data$psm_data
metadata <- data$metadata
```
**For your own data:** Replace with your loading code, then call `validate_input_data()`.

**Step 2 - Run DE analysis:**
```r
source("scripts/basic_workflow.R")
```
**DO NOT expand this into inline code. DO NOT write limma/DEqMS steps manually. Just source the script.**

**Step 3 - Generate plots:**
```r
source("scripts/qc_plots.R")
generate_all_plots(fit_deqms, deqms_results, protein_matrix,
                    metadata, output_dir = "results", raw_matrix = raw_matrix)
```
🚨 **DO NOT write inline plotting code (ggsave, ggplot, Heatmap, etc.). Just use the script.** 🚨

**Step 4 - Export results:**
```r
source("scripts/export_results.R")
export_all(fit_deqms, deqms_results, protein_matrix, metadata,
            output_dir = "results")
```
**DO NOT write custom export code. Use export_all() to save all outputs including RDS.**

**Step 5 - Generate the final PDF report when requested:**
- If the user asks for a PDF report, use the `pdf-report-generation` skill after `export_all()` completes.
- Build the report from `results/analysis_report.md`, `results/all_results.csv`, `results/significant_results.csv`, `results/top100_proteins.csv`, and the generated PNG figures.
- The final PDF must include an infographic or summary figure plus introduction, methods, results, conclusions, figures, references, and next steps.
- Save or rename the final report as `results/analysis_report.pdf` so the expected artifact name stays stable.
- If `pdf-report-generation` is unavailable, use the fallback PDF created by `scripts/generate_report.R` and state that fallback clearly.

**✅ VERIFICATION - You should see:**
- After Step 1: `"✓ Example data loaded successfully"` with PSM/protein counts
- After Step 2: `"✓ Proteomics DE analysis completed successfully!"` with summary
- After Step 3: `"✓ All plots generated successfully!"`
- After Step 4: `"=== Export Complete ==="` with file list
- If PDF was requested: `analysis_report.pdf` exists and was created with `pdf-report-generation`, or the fallback path is clearly disclosed

**❌ IF YOU DON'T SEE THESE MESSAGES:** You wrote inline code. Stop and use source().

⚠️ **CRITICAL - DO NOT:**
- ❌ **Write inline limma/DEqMS code** → **STOP: Use `source("scripts/basic_workflow.R")`**
- ❌ **Write inline plotting code** → **STOP: Use `generate_all_plots()`**
- ❌ **Write custom export code** → **STOP: Use `export_all()`**
- ❌ **Try to install svglite** → scripts handle SVG fallback automatically
- ❌ **Skip `pdf-report-generation` when the user requested a PDF report** → use it for the final PDF unless unavailable, then disclose fallback
- ❌ **Use absolute paths** → Always use `scripts/file.R`

**⚠️ IF SCRIPTS FAIL - Script Failure Hierarchy:**
1. **Fix and Retry (90%)** - Install missing package, re-run script
2. **Modify Script (5%)** - Edit the script file itself, document changes
3. **Use as Reference (4%)** - Read script, adapt approach, cite source
4. **Write from Scratch (1%)** - Only if genuinely impossible, explain why

**NEVER skip directly to writing inline code without trying the script first.**

**What the scripts provide:**
- [scripts/load_example_data.R](scripts/load_example_data.R) — `load_example_data()`, `validate_input_data()`
- [scripts/basic_workflow.R](scripts/basic_workflow.R) — Complete limma+DEqMS pipeline with PSM aggregation, imputation, normalization
- [scripts/qc_plots.R](scripts/qc_plots.R) — Analysis-ready plots with ggprism/ComplexHeatmap (PNG + SVG with automatic fallback)
- [scripts/export_results.R](scripts/export_results.R) — `export_all()` saves core outputs (CSV, RDS, markdown report, and fallback PDF when available)

## Customizing the Analysis

**To change the comparison** (before sourcing basic_workflow.R):
```r
comparison_name <- "miR519-ctrl"  # or any valid contrast
source("scripts/basic_workflow.R")
```

**To change imputation/normalization:**
```r
imputation_method <- "kNN"         # "MinProb" (default) or "kNN"
normalization_method <- "quantile" # "median" (default), "quantile", or "none"
source("scripts/basic_workflow.R")
```

**For detailed method documentation:** See [references/proteomics-reference.md](references/proteomics-reference.md)
**For normalization guidance:** See [references/normalization-guide.md](references/normalization-guide.md)

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| **Not seeing verification messages** | Wrote inline code instead of source() | Stop and use Standard Workflow commands exactly |
| **"cannot open file" error** | Using absolute paths | Use relative paths: `source("scripts/file.R")` |
| **ExperimentHub download fails** | Network timeout | Set `options(timeout = 300)` and retry |
| **Missing package errors** | Package not installed | `BiocManager::install('package')` or `install.packages('package')` |
| **SVG export error "svglite required"** | Missing optional dependency | Use `generate_all_plots()` — it handles fallback automatically. DO NOT try to install svglite manually |
| **svglite dependency conflict** | System library version mismatch | Normal — `generate_all_plots()` falls back to base R svg() device automatically. Both PNG and SVG will be created |
| **All proteins filtered out** | Too stringent missing value filter | Adjust filter threshold in basic_workflow.R |
| **No significant proteins** | Weak effect or wrong comparison | Check PCA for condition separation; try relaxed thresholds |

## Suggested Next Steps

After running this skill:
1. **Pathway enrichment** → functional-enrichment skill with significant proteins
2. **Biomarker panel** → elastic-net-biomarker-panel with DE proteins as features
3. **Network analysis** → coexpression-network with protein matrix
4. **Gene list processing** → de-results-to-gene-lists for annotation

## Related Skills

| Skill | Relationship | When to Use |
|-------|-------------|-------------|
| bulk-rnaseq-counts-to-de-deseq2 | Alternative | RNA-seq count data (not proteomics) |
| elastic-net-biomarker-panel | Downstream | Build biomarker panel from DE proteins |
| coexpression-network | Downstream | Protein co-expression modules |

## References

- **DEqMS:** Zhu Y, et al. *Molecular & Cellular Proteomics*. 2020;19(6):1047-1057
- **limma:** Ritchie ME, et al. *Nucleic Acids Research*. 2015;43(7):e47
- [Detailed method reference](references/proteomics-reference.md)
- [Normalization guide](references/normalization-guide.md)
