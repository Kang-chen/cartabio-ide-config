---
id: "skill_40063c54fad9439a9acd36c76a25983f"
name: "experimental-design-statistics"
description: "Use to design genomics or omics experiments with power and sample-size calculations, batch and covariate planning, replication, and multiple-testing correction."
category: "experimental_design"
visibility: "public"
starting-prompt: "Help me design a bulk RNA-seq experiment with power analysis for sample size estimation and a batch-balanced layout. Generate a PDF report with an intro, methods, results, conclusions and figures from all of the analyses you perform."
---

# Experimental Design and Statistical Planning

Comprehensive workflow for statistical experimental design in genomics, from power analysis and sample size determination to batch-balanced experimental layouts and multiple testing strategy.

## Scope

**What this skill does:** Two-group unpaired negative-binomial power analysis and sample-size estimation for bulk RNA-seq (and ATAC-seq / proteomics / methylation via the same count-based framework), with batch-balanced sample assignment and multiple-testing correction guidance.

**What this skill does NOT do:**
- Multi-factor, interaction, or factorial designs (only two-group comparisons)
- Repeated-measures, time-course, or paired designs
- Survival or time-to-event power
- Degrees-of-freedom cost of covariate adjustment (covariates are balanced but the power model does not penalize for them)
- Unequal group allocation (1:1 only)
- Simulation-based power (analytic NB approximation only; `calc_power_proper` is a stub that stops)

For any of the above, use simulation-based tools (e.g., PROPER, powsimR) or consult a statistician.

## Installation

| Software | Version | License | Installation |
|----------|---------|---------|--------------|
| DESeq2 | ≥1.30.0 | LGPL (≥3) | `BiocManager::install('DESeq2')` |
| RNASeqPower | ≥1.30.0 | LGPL (>=2) | `BiocManager::install('RNASeqPower')` |
| RnaSeqSampleSize | ≥1.30.0 | GPL (≥2) | `BiocManager::install('RnaSeqSampleSize')` |
| pwr | ≥1.3.0 | GPL (≥3) | `install.packages('pwr')` |
| IHW | ≥1.18.0 | Artistic-2.0 | `BiocManager::install('IHW')` |
| anticlust | ≥0.8.0 | MIT | `install.packages('anticlust')` |
| ggplot2 | ≥3.3.0 | MIT | `install.packages('ggplot2')` |
| ggprism | ≥1.0.3 | GPL-3 | `install.packages('ggprism')` |
| jsonlite | ≥1.7.0 | MIT | `install.packages('jsonlite')` |
| pasilla | ≥1.18.0 | Artistic-2.0 | `BiocManager::install('pasilla')` |

**Quick install:**
```r
if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
BiocManager::install(c("DESeq2", "RNASeqPower", "RnaSeqSampleSize", "IHW", "pasilla"))
install.packages(c("anticlust", "ggplot2", "ggprism", "jsonlite", "pwr"))
```

**Full installation and license details:** [references/software_requirements.md](references/software_requirements.md)

## Inputs

**Required:**
- **Experimental design info**: Assay type, n conditions, sample relationship, planned n
- **Effect size expectations**: Target fold change, variability (CV or pilot data)
- **Statistical requirements**: Target power (0.80/0.90), α (0.05), multiple testing preference

**Optional:**
- **Practical constraints**: Budget, sample availability, batch structure, sequencing depth, covariates

**Detailed input requirements:** [references/experimental_design_best_practices.md#input-requirements](references/experimental_design_best_practices.md#input-requirements)

## Outputs

**Power and sample size:**
- `power_analysis_results.csv` - Power calculations for scenarios
- `sample_size_recommendation.txt` - Required n with justification
- `cv_sensitivity.csv` - Required n across CV values (package-emitted from recommendation)
- `de_proportion_sensitivity.csv` - Required n across DE-proportion assumptions (package-emitted)
- `mean_count_sensitivity.csv` - Required n across per-gene mean count values (package-emitted)
- `power_vs_n_curve.png` + `.svg` - Power relationship visualizations
- `cv_by_tissue.png` + `.svg` - Bulk RNA-seq CV by tissue (reference for the CV assumption)

**Batch design:**
- `batch_layout_for_lab.csv` - Batch assignment template (replace sample IDs with your own when using example data)
- `batch_design_validation.txt` - Confounding check results
- `batch_design_plot.png` + `.svg` - Visual layout

**Documentation:**
- `statistical_analysis_plan.md` - Complete pre-registration plan
- `lab_protocol_checklist.md` - Step-by-step processing guide
- `design_parameters.json` - All parameters (human-readable)

**Analysis objects (RDS) - For downstream use:**
- `batch_design.rds` - Load with: `readRDS('batch_design.rds')` (batch effect correction)
- `design_parameters.rds` - Load with: `readRDS('design_parameters.rds')` (validation, replication)

**PDF report:**
- `analysis_report.pdf` - Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

## Clarification Questions

🚨 **ALWAYS ask Question 1 FIRST. Do not ask about assay type, experimental structure, or design parameters before the user has answered Question 1.**

### 1. **Input Files** (ASK THIS FIRST):
   - **Do you have pilot data or existing results files to inform the experimental design?**
     - If uploaded: Are these pilot data files (DESeq2 objects, count matrices) for power calculations?
     - Expected formats: RDS (DESeqDataSet), CSV/TSV (count matrices)
   - **Or use literature-based estimates?**
     - Tissue-specific variability values from published data — all defaults pre-defined

> 🚨 **IF LITERATURE-BASED ESTIMATES SELECTED (no pilot data):** Use defaults (bulk RNA-seq, 2-group case-control, moderate fold change 1.5x, power 0.90 for grants, BH-FDR). **DO NOT ask questions 2-7, EXCEPT:** ask the user to select their approximate sample type for CV estimation:
>
> **Sample type (determines biological variability CV):**
> - a) Cell lines (CV ≈ 0.2 — low variability)
> - b) Sorted cells / PBMCs (CV ≈ 0.4 — moderate) **(default)**
> - c) Whole tissue biopsies (CV ≈ 0.5 — moderate-high)
> - d) Heterogeneous clinical samples (CV ≈ 0.6 — high variability)
> - e) Not sure — use default CV = 0.4 with sensitivity analysis
>
> Pass the selected CV and tissue label to `generate_design_recommendation(cv = X, tissue_type = "label")`. Then proceed directly to Step 1.

**Questions 2-7 are ONLY for users providing their own pilot data or specifying custom parameters:**

### 2. **Assay Type**: Bulk RNA-seq, scRNA-seq, ATAC-seq, ChIP-seq, methylation, proteomics, or other?
### 3. **Experimental Structure**: Number of conditions (2 case-control, 3+ multi-group, factorial)? Planned n? Sample type (independent/paired/repeated)? Covariates (sex, age, batch, site)?
### 4. **Effect Size & Variability**: Target fold change (large ≥2x, moderate 1.5-2x, small 1.2-1.5x)? Pilot data available?
### 5. **Statistical Requirements**: Power (0.80 standard, 0.90 grants)? Alpha (0.05 standard, 0.01 stringent)? Multiple testing (BH-FDR standard, IHW, Bonferroni)?
### 6. **Practical Constraints**: Budget/max samples? Sample availability? Batch structure? Sequencing depth target?
### 7. **Primary Objective**: Power analysis, sample size, batch design, multiple testing guidance, complete design, or budget optimization?

**Comprehensive clarification guide:** [references/experimental_design_best_practices.md#clarification-questions](references/experimental_design_best_practices.md#clarification-questions)

## Standard Workflow

🚨 **MANDATORY: USE SCRIPTS EXACTLY AS SHOWN - DO NOT WRITE INLINE CODE** 🚨

The experimental design workflow follows 5 steps: **Load** → **Calculate** → **Visualize** → **Export** → **Report**

### **Step 1 - Load Parameters**

*Biological rationale: Pilot data gives measured biological CV (between-animal/individual variability), which is what determines power — not technical CV, which is near-Poisson and irrelevant at typical sequencing depths.*

```r
source("scripts/load_example_data.R")
pilot_data <- load_example_data()
```

**With pilot data (preferred):**
- Uses `pilot_data$dds` for power calculations
- Uses `pilot_data$cv$median` for sample size estimation
- Provides realistic variability estimates

**Without pilot data (alternative):**
```r
source("scripts/load_example_data.R")
cv_db <- load_cv_database()
# Select appropriate tissue type from cv_db
```

**✅ VERIFICATION:** You MUST see: `"✓ Example pilot data loaded successfully!"`

**Decision:** Pilot data provides more accurate estimates. See [power_analysis_guidelines.md#pilot-vs-literature](references/power_analysis_guidelines.md#pilot-vs-literature)

---

### **Step 2 - Calculate Design**

🚨 **DO NOT write inline calculation code. Use the provided scripts.**

**A. Power Analysis** - Calculate power for your proposed design
```r
source("scripts/power_rnaseq.R")
power_result <- calc_power_rnaseq(
  mean_count = 20,
  n_per_group = 6,
  cv = pilot_data$cv$median,
  fold_change = 1.5,
  alpha = 0.05
)
```

**B. Sample Size Determination** - Calculate required n

*Biological rationale: FDR-aware n (RnaSeqSampleSize) differs from per-gene n (RNASeqPower) because it accounts for the multiplicity of testing thousands of genes. Per-gene power answers "can I detect one gene?"; FDR-aware power answers "can I find a meaningful fraction of true DE genes while controlling false discoveries?" The FDR-aware n is always larger and is the one to use for the experimental plan.*

**With pilot data:**
```r
source("scripts/sample_size_de.R")
required_n <- samplesize_from_pilot(
  pilot_dds = pilot_data$dds,
  fold_change = 1.5,
  power = 0.9,
  fdr = 0.05
)
```

**Without pilot data (literature-based CV):**
```r
source("scripts/sample_size_de.R")
required_n <- calc_samplesize_de(cv = 0.40, fold_change = 1.5, power = 0.9, fdr = 0.05)
```

**C. Batch Assignment** - Generate balanced batch layout for planned experiment

*Biological rationale: Batch must cross condition so that batch effects are estimable separately from the biological effect. If batch is confounded with condition, no statistical method can recover the biological signal. Anticlustering optimizes balance, but balance is not randomization — it cannot fix a covariate that is already confounded with condition in the study design.*

```r
source("scripts/batch_assignment.R")
batch_design <- assign_samples_to_batches(
  metadata = planned,
  batch_size = 10,
  balance_vars = c("condition", "sex")
)
```

**D. Design Recommendation** (ALWAYS run this)
```r
source("scripts/power_rnaseq.R")
recommendation <- generate_design_recommendation(
  cv = pilot_data$cv$median, target_fc = 1.5, target_power = 0.90
)
```
🚨 **This produces the complete, honest sample size recommendation with per-gene AND FDR-aware power, plus sensitivity sweeps for CV, DE proportion, and mean count. DO NOT make your own sample size recommendation — use this output directly.**

**E. qPCR / ΔCt Power** — see [references/qpcr_power.md](references/qpcr_power.md) for qPCR/RT-qPCR/ddPCR power analysis (continuous readout, `pwr` package, biological ΔCt SD enforced).

**⚠️ IF SCRIPTS FAIL - Script Failure Hierarchy:**
1. **Fix and Retry (90%)** - Install missing package, re-run script
2. **Modify Script (5%)** - Edit the script file itself, document changes
3. **Use as Reference (4%)** - Read script, adapt approach, cite source
4. **Write from Scratch (1%)** - Only if genuinely impossible, explain why

**✅ VERIFICATION:** You should see:
- After power analysis: `"✓ Power analysis completed successfully!"`
- After sample size: `"✓ FDR-aware sample size estimation completed successfully!"`
- After batch design: `"✓ Batch design generated successfully!"`
- After recommendation: `"✓ Design recommendation generated successfully!"`

**CRITICAL RULE:** Batch must NEVER confound with condition. See [batch_effect_mitigation.md#cardinal-rule](references/batch_effect_mitigation.md#cardinal-rule)

---

### **Step 3 - Visualize Design**

```r
source("scripts/plot_power_curves.R")
plot_power_vs_samplesize(
  cv = pilot_data$cv$median,
  fold_changes = c(1.5, 2, 3),
  mean_count = 20,
  # x-range comes from the recommendation grid so the curve spans (and passes)
  # the headline n instead of stopping short of it
  max_n = max(recommendation$power_table$n),
  output_file = "design_results/power_vs_n_curve"
)

# CV-by-tissue reference for the bulk RNA-seq CV assumption the design rests on
plot_cv_by_tissue(assay = "Bulk RNA-seq", output_file = "design_results/cv_by_tissue")
```

```r
source("scripts/batch_validation.R")
confounding_check <- check_confounding(batch_design, "condition")
check_covariate_condition_balance(batch_design, "condition", c("sex", "age_group"))
visualize_batch_design(
  batch_design,
  condition_var = "condition",
  output_file = "design_results/batch_design_plot"
)
```

**✅ VERIFICATION:** You should see:
- `"Saving power curve plots:"` followed by PNG + SVG file paths
- `"Saving CV reference plots:"` followed by PNG + SVG file paths
- `"PASS: No confounding detected"` or `"WARNING: Batch is CONFOUNDED"`
- `"Saving batch design plots:"` followed by PNG + SVG file paths

---

### **Step 4 - Export All Results**

*Biological rationale: The RDS objects preserve the exact design and parameter state for downstream DE analysis and validation. The consistency gate ensures the exported plan describes the design that was actually computed — if design_params disagrees with the recommendation, export halts before any file is written.*

```r
source("scripts/export_design.R")

# Derive design_params from the recommendation — do NOT hand-type them
design_params <- build_design_params(recommendation, batch_design,
                                     pilot_result = required_n)

export_complete_design(batch_design, design_params, output_dir = "design_results",
                       recommendation = recommendation, pilot_result = required_n,
                       condition_var = "condition", covariates = c("sex", "age_group"))
```

**DO NOT write custom export code. Use export_complete_design().**

**✅ VERIFICATION:** You MUST see: `"=== Export Complete ==="`

This will generate:
1. `batch_layout_for_lab.csv` - Batch assignment template
2. `statistical_analysis_plan.md` - Pre-registration analysis plan
3. `lab_protocol_checklist.md` - Lab processing checklist
4. `batch_design.rds` - Batch design object (for downstream use)
5. `design_parameters.rds` - Design parameters (for downstream use)
6. `design_parameters.json` - Design parameters (human-readable)
7. `power_analysis_results.csv` - Power analysis results
8. `sample_size_recommendation.txt` - Sample size recommendation
9. `cv_sensitivity.csv` - CV sensitivity table
10. `de_proportion_sensitivity.csv` - DE proportion sensitivity table
11. `mean_count_sensitivity.csv` - Mean count sensitivity table
12. `batch_design_validation.txt` - Batch confounding/balance validation report

**RDS objects are CRITICAL** for downstream workflows and validation studies.

---

### **Step 5 - Generate Final Report (MANDATORY)**

🚨 **This step is required, not optional. The workflow is not complete until a report has been produced.**

Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

```
Skill(action="load", name="pdf-report-generation")
```

---

### **Complete Workflow Example**

```r
# Step 1: Load pilot data
source("scripts/load_example_data.R")
pilot_data <- load_example_data()

# Step 2: Calculate design
source("scripts/power_rnaseq.R")
source("scripts/sample_size_de.R")
source("scripts/batch_assignment.R")

power_result <- calc_power_rnaseq(mean_count = 20, n_per_group = 6,
                                  cv = pilot_data$cv$median, fold_change = 1.5)
required_n <- samplesize_from_pilot(pilot_data$dds, fold_change = 1.5, power = 0.9)
recommendation <- generate_design_recommendation(cv = pilot_data$cv$median,
                                                  target_fc = 1.5, target_power = 0.90)

# Derive rec_n the same way build_design_params does
rec_n <- recommendation$fdr_required_n$power_90
if (is.na(rec_n)) rec_n <- recommendation$per_gene_required_n$power_90

# Build batch layout AFTER the recommendation, using rec_n (not the demo 10)
planned <- make_planned_metadata(n_per_group = rec_n, conditions = c("untreated", "treated"))
batch_design <- assign_samples_to_batches(planned, batch_size = 10,
                                          balance_vars = c("condition", "sex"))

# Step 3: Visualize and validate
source("scripts/plot_power_curves.R")
source("scripts/batch_validation.R")

plot_power_vs_samplesize(cv = pilot_data$cv$median, fold_changes = c(1.5, 2, 3),
                         mean_count = 20, max_n = max(recommendation$power_table$n),
                         output_file = "design_results/power_vs_n_curve")
plot_cv_by_tissue(assay = "Bulk RNA-seq", output_file = "design_results/cv_by_tissue")
check_confounding(batch_design, "condition")
visualize_batch_design(batch_design, "condition", output_file = "design_results/batch_design_plot")

# Step 4: Export — derive design_params from the recommendation, do NOT hand-type
source("scripts/export_design.R")
design_params <- build_design_params(recommendation, batch_design, pilot_result = required_n)
export_complete_design(batch_design, design_params, output_dir = "design_results",
                       recommendation = recommendation, pilot_result = required_n,
                       condition_var = "condition", covariates = c("sex", "age_group"))
```

**Note:** `pilot_data$planned_metadata` is a 20-sample quick demo (n_per_group=10). For a real design, use `make_planned_metadata(rec_n)` as shown above so the batch layout matches the recommended sample size.

## Scientific Caveats

- **CV dominates the answer and is normally assumed, not measured.** The biological coefficient of variation is the single most influential parameter. When no pilot data is available, CV comes from `references/cv_tissue_database.csv` — literature point estimates, eight of which are sourced "Literature avg" (unsourced consensus). Always run the CV sensitivity sweep and report which CV was assumed.
- **lambda0 is a per-gene expected count, not library size.** The variance decomposition is CV_total^2 = 1/lambda0 + BCV^2, where lambda0 is the expected read count for a single gene (typically 5-300). Library depth in reads is NOT a direct input to RNASeqPower or RnaSeqSampleSize. The single source of truth for this identity is `scripts/variance_model.R`.
- **n is nearly flat in lambda0 at fixed CV — this is a parameterization artifact, not robustness.** At fixed CV, dispersion = CV^2 - 1/lambda0 rises as lambda0 rises, cancelling the gain from the shrinking Poisson term. At fixed dispersion (the biologically meaningful comparison), n does decrease with lambda0. Do not interpret the flat-at-fixed-CV response as evidence that sequencing depth does not matter.
- **The assumed DE proportion moves n from ~43 to ~27 across 1-20% DE.** This is a larger swing than any other parameter. The default pi0 = 0.9 (10% DE) is a skill assumption, not a measured quantity. The pi0 sensitivity sweep is always emitted; read it before quoting a headline n.
- **Per-gene vs experiment-wide power.** RNASeqPower gives per-gene power (probability of detecting one specific DE gene). RnaSeqSampleSize gives FDR-aware power (probability of detecting a meaningful fraction of true DE genes while controlling false discoveries). The FDR-aware n is always larger and is the one to use for the experimental plan.
- **RnaSeqSampleSize assumes one dispersion and one lambda0 across all genes.** Real data has a mean-dispersion trend. The power estimate is an approximation that tends to be optimistic for low-expression genes and conservative for high-expression genes.
- **Anticlust balance is not randomization.** Batch balancing distributes known covariates evenly across batches but cannot fix a covariate that is confounded with condition in the study design. If sex is confounded with treatment group, no batch design can recover the treatment effect.
- **The CV database is literature point estimates.** Eight of 29 rows are sourced "Literature avg" (unsourced consensus). The Human PBMC bulk RNA-seq CV = 0.40 is a consensus estimate, not a direct measurement from any single study. Verify against pilot data when possible.

## Decision Guide

- **Pilot vs Literature:** Use pilot data if available (more accurate). Literature CV acceptable as fallback.
- **Sample Size vs Depth:** Prioritize more samples over deeper sequencing for DE. 15-20M reads sufficient.
- **Multiple Testing:** BH-FDR (standard), IHW (more power), Bonferroni (stringent).

**See:** [experimental_design_best_practices.md#decision-guide](references/experimental_design_best_practices.md#decision-guide) for comprehensive guidance.

## Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Power <0.80 with max budget | Effect size too small or CV too high | Increase n, increase depth, or revise effect size expectations. See [references/power_analysis_guidelines.md#low-power](references/power_analysis_guidelines.md#low-power) |
| Batch confounding detected | Unequal condition distribution across batches | Regenerate with stricter balance constraints or adjust batch size. See [references/batch_effect_mitigation.md#troubleshooting](references/batch_effect_mitigation.md#troubleshooting) |
| Required n exceeds sample availability | Pilot data shows high variability or small effect | Consider paired design, blocking by major covariates, or revise target fold-change. See [references/experimental_design_best_practices.md#budget-optimization](references/experimental_design_best_practices.md#budget-optimization) |
| Can't balance all covariates | Too many variables for batch size | Prioritize key covariates (condition > sex > age > others). See [references/batch_effect_mitigation.md#covariate-priority](references/batch_effect_mitigation.md#covariate-priority) |
| CV estimate varies widely | Pilot data has outliers or low counts | Filter low-count genes (mean <10) before CV calculation. Use median, not mean CV. See [references/power_analysis_guidelines.md#cv-estimation](references/power_analysis_guidelines.md#cv-estimation) |
| Power calculations give n<3 | Very large effect size or low variability | Warning: n<3 too low for valid inference. Plan for minimum n=3-4 even if calculations suggest n=2 |
| Power seems high but few DE genes found | Per-gene vs. experiment-wide power | Use `generate_design_recommendation()` which reconciles both. |
| Multiple testing correction too stringent | Many tests, low discovery rate | Consider IHW (more powerful than BH-FDR) or independent filtering. See [references/multiple_testing_guide.md#choosing](references/multiple_testing_guide.md#choosing) |
| SVG export error | Missing optional dependency | Normal - scripts fall back automatically. Both PNG and SVG will be created in most environments. |
| "cannot open file 'Rplots.pdf'" | Default PDF device in container | Re-run the plotting function — scripts suppress this automatically. |
| FDR column blank in power table | RnaSeqSampleSize not installed | Install with `BiocManager::install('RnaSeqSampleSize')`. Per-gene power still valid but underestimates required n. |
| Covariate confounded with condition | Unequal covariate distribution across conditions | Batch balancing cannot fix this. Either balance covariates within conditions, or include in DE model (`~ covariate + condition`). |

**Detailed troubleshooting:** [references/troubleshooting_guide.md](references/troubleshooting_guide.md)

## Suggested Next Steps

1. **Execute Experiment** - Use batch assignment file to guide sample processing
2. **Perform DE Analysis** - Use bulk-rnaseq-counts-to-de-deseq2 or appropriate skill
3. **Apply Multiple Testing** - Use `source("scripts/multiple_testing.R"); recommend_method(...)` to compare IHW vs BH-FDR
4. **Validate Results** - Check batch effects were controlled, verify power calculations

## Related Skills

**Upstream:** None - this is typically the first step in a project

**Downstream (after data collection):**
- **bulk-rnaseq-counts-to-de-deseq2** - Differential expression analysis
- **functional-enrichment-from-degs** - Pathway analysis

**Report generation:**
- **pdf-report-generation** - Generates the final PDF report (see Step 5).

**Alternative/complementary:**
- **bulk-omics-clustering** - Discover natural groupings post-hoc

## Biomni Resource Awareness

- Check required packages directly in Python/R and import documented `biomni.tool` functions before use; use the bundled CV database for the core workflow.
- Use `LiteratureSearch` to find published power analyses or CV estimates for specific tissues/assays when the bundled `references/cv_tissue_database.csv` lacks your sample type.
- The bundled references are self-contained and sufficient for the core workflow; external lookups are optional extensions.

## References

**Detailed documentation:**
- [references/experimental_design_best_practices.md](references/experimental_design_best_practices.md) - General design principles, decision guide, common patterns
- [references/power_analysis_guidelines.md](references/power_analysis_guidelines.md) - Detailed power calculation methods, pilot vs literature
- [references/batch_effect_mitigation.md](references/batch_effect_mitigation.md) - Preventing/controlling batch effects, cardinal rule, troubleshooting
- [references/multiple_testing_guide.md](references/multiple_testing_guide.md) - Choosing correction methods
- [references/qc_guidelines.md](references/qc_guidelines.md) - Quality control checkpoints
- [references/troubleshooting_guide.md](references/troubleshooting_guide.md) - Common problems and solutions
- [references/software_requirements.md](references/software_requirements.md) - Installation and licenses
- [references/qpcr_power.md](references/qpcr_power.md) - qPCR / ΔCt power analysis
- [references/cv_tissue_database.csv](references/cv_tissue_database.csv) - Tissue-specific variability estimates

**Scripts:** See scripts/ directory for all analysis functions:
- Data loading: [load_example_data.R](scripts/load_example_data.R)
- Power/sample size: [power_rnaseq.R](scripts/power_rnaseq.R), [power_atacseq.R](scripts/power_atacseq.R), [sample_size_de.R](scripts/sample_size_de.R), [sample_size_scrna.R](scripts/sample_size_scrna.R)
- qPCR / ΔCt power: [power_qpcr.R](scripts/power_qpcr.R) — see [references/qpcr_power.md](references/qpcr_power.md)
- Batch design: [batch_assignment.R](scripts/batch_assignment.R), [batch_validation.R](scripts/batch_validation.R)
- Visualization: [plot_power_curves.R](scripts/plot_power_curves.R)
- Export: [export_design.R](scripts/export_design.R) (includes `build_design_params`, `validate_design_consistency`, RDS saving)
- Variance model: [variance_model.R](scripts/variance_model.R) (single source of truth for CV^2 = 1/lambda0 + BCV^2)

**Key Papers:**
- Hart SN et al. (2013) *J Comput Biol* 20(12):970-978 - RNA-seq sample size
- Li CI et al. (2018) *BMC Bioinformatics* 19:191 - RnaSeqSampleSize (FDR-aware power)
- Papenberg M & Klau GH (2021) *Psychol Methods* 26(2):161-174 - Anticlustering (batch balance)
- Schurch NJ et al. (2016) *RNA* 22(6):839-851 - Biological replicates needed
- Leek JT et al. (2010) *Nat Rev Genet* 11(10):733-739 - Batch effects impact
- Benjamini & Hochberg (1995) *J R Stat Soc Series B* 57(1):289-300 - FDR control
- Love MI et al. (2014) *Genome Biol* 15(12):550 - DESeq2 methods
