---
id: "skill_827c499e76084524a2e098d80383a3a4"
name: "survival-analysis-clinical"
description: "Use for clinical time-to-event analysis with Kaplan-Meier curves, log-rank tests, Cox proportional-hazards regression, covariates, and risk stratification."
category: "multi_omics"
visibility: "public"
starting-prompt: "Run a survival analysis on breast cancer clinical data to identify prognostic factors and stratify patients by risk. Generate a PDF report with an intro, methods, results, conclusions and figures from all of the analyses you perform."
---

# Clinical Survival & Outcome Analysis

Kaplan-Meier survival estimation, Cox proportional hazards regression, and risk stratification for clinical and real-world evidence (RWE) datasets.

## Scope

This skill performs standard right-censored survival analysis: Kaplan-Meier estimation, Cox proportional hazards regression, Schoenfeld residual PH testing, and risk stratification via the Cox linear predictor.

**What this skill does NOT do:**
- **Competing-risks / Fine-Gray models** — standard Cox treats competing events as censored, which can overestimate cause-specific event probabilities
- **Interval censoring** — only right-censored data is supported
- **Multi-state / illness-death models** — only a single absorbing event is modeled
- **Multiple imputation** — the Cox model is complete-case; patients with missing covariates are excluded
- **External validation cohort** — the C-index is computed on the same data used to fit the model (optimism-corrected via bootstrap, but no external validation is performed)
- **RMST (restricted mean survival time)** — not implemented; use landmark rates when medians are unreliable
- **Time-dependent covariates as a primary model** — the PH sensitivity analysis (`survSplit` / stratified Cox) is a diagnostic, not a replacement for the primary model

## Scientific Caveats

- **Risk score is discovery-only.** The risk score and risk groups are fitted and evaluated on the same patients. The optimism-corrected C-index is the headline discrimination metric; the apparent C-index is optimistically biased.
- **Breast-cancer OS with non-cancer deaths.** When the endpoint is overall survival, non-cancer deaths are treated as events. This is formally a competing-risks setting; standard Cox treats them as the event of interest. Interpret OS HRs accordingly.
- **Cox model is complete-case.** Patients with missing covariate values are excluded from the Cox model. If >20% are excluded, a warning banner is emitted. No imputation is performed.

## When to Use This Skill

Use this skill when you need to:
- **Estimate survival curves** (Kaplan-Meier) with confidence intervals and risk tables
- **Identify prognostic factors** via Cox proportional hazards regression
- **Stratify patients by risk** using Cox model linear predictor
- **Test proportional hazards assumption** with Schoenfeld residuals
- **Compare survival between groups** (molecular subtypes, treatment arms, biomarker levels)
- **Generate forest plots** of hazard ratios for multi-covariate models

**Don't use this skill for:**
- ❌ Biomarker panel selection from omics → use `elastic-net-biomarker-panel`
- ❌ Differential expression analysis → use `bulk-rnaseq-counts-to-de-deseq2`
- ❌ Disease trajectory / longitudinal modeling → use `disease-progression-longitudinal`
- ❌ Genetic association / Mendelian randomization → use `mendelian-randomization-twosamplemr`

## Installation

Before selecting tools or packages, review
`references/biomni_supported_resources.md` for preinstalled packages and
Biomni-native tools. Check package availability with `requireNamespace()`.
Use Biomni `LiteratureSearch` when literature context or citations are needed.

The example cohort for this workflow comes from `load_example_data()` only.
Do not substitute a cohort from cBioPortal, GEO, a TCGA API, or any other
database. If no packaged dataset is usable, stop and ask the user.

```r
options(repos = c(CRAN = "https://cloud.r-project.org"))
if (!require('BiocManager', quietly = TRUE)) install.packages('BiocManager')

# Core (required)
install.packages(c('survival', 'ggplot2', 'ggprism', 'scales'))

# Enhanced KM curves with risk tables (recommended)
install.packages('survminer')

# Example data: TCGA BRCA (optional, needed for tcga_brca demo)
BiocManager::install('RTCGA.clinical')

```

| Software | Version | License | Commercial Use | Installation |
|----------|---------|---------|----------------|-------------|
| survival | >=3.5 | LGPL (>=2) | ✅ Permitted | `install.packages('survival')` |
| ggplot2 | >=3.4 | MIT | ✅ Permitted | `install.packages('ggplot2')` |
| ggprism | >=1.0.3 | GPL (>=3) | ✅ Permitted | `install.packages('ggprism')` |
| scales | >=1.2 | MIT | ✅ Permitted | `install.packages('scales')` |
| survminer | >=0.4.9 | GPL (>=2) | ✅ Permitted | `install.packages('survminer')` |

## Inputs

**Required:**
- **Clinical data** with columns for:
  - **Time-to-event** (numeric: days, months, or years)
  - **Event indicator** (binary: 0 = censored, 1 = event)
- Minimum 50 patients recommended (20+ events for reliable Cox estimates)

**Optional:**
- **Stratification variable** (e.g., molecular subtype, treatment arm, biomarker group)
- **Covariates** for Cox model (age, stage, receptor status, etc.)
- **Pre-computed risk scores** from upstream skills (e.g., `elastic-net-biomarker-panel`)

**Formats:** CSV/TSV with headers, or R data frame

## Outputs

Each analysed endpoint gets its own `results/<endpoint>/` subdirectory (e.g. `results/OS/`, `results/RFS/`) containing the full set of artifacts below; the final PDF (see Reports) combines all endpoints. Paths below are shown relative to one endpoint's subdirectory.

**Primary results:**
- `cox_coefficients.csv` — Hazard ratios with 95% CI and p-values for all covariates
- `risk_scores.csv` — Patient-level risk scores and risk group assignments
- `clinical_annotated.csv` — Full clinical data with added risk group column
- `survival_summary.csv` — Summary statistics per risk group (N, events, event rate, median survival)
- `ph_assumption_test.csv` — Schoenfeld residual test results (chi-sq, p-value per covariate)
- `key_metrics.csv` — Single-row headline metrics table (all N, HR, C-index, p-values, reference levels in one row — the canonical source for report numbers)
- `reference_levels.csv` — Covariate reference levels and the selection rule that chose them
- `missingness_assessment.csv` — Per-covariate missingness counts and informative-missingness Fisher test
- `ph_sensitivity.csv` — Time-varying coefficient sensitivity analysis (generated only when global PH test p < 0.05)

**Analysis objects (RDS):**
- `survival_model.rds` — Complete analysis object for downstream use
  - Load with: `model <- readRDS('results/<endpoint>/survival_model.rds')` (e.g. `results/OS/survival_model.rds`)
  - Contains: KM fits, Cox model, PH test, risk groups, clinical data, metadata
  - Access risk scores: `model$cox$risk_scores`
  - Access Cox model: `model$cox$model`
  - Required for: `elastic-net-biomarker-panel` (risk scores as features), downstream integration

**Plots (PNG + SVG):**
- `km_overall.png/.svg` — Overall Kaplan-Meier curve with confidence interval
- `km_stratified.png/.svg` — Stratified survival curves with log-rank p-value
- `forest_plot.png/.svg` — Forest plot of hazard ratios with significance markers
- `km_risk_groups.png/.svg` — Risk group survival curves with log-rank test
- `schoenfeld_diagnostics.png/.svg` — PH assumption diagnostic plots
- `cumulative_hazard.png/.svg` — Cumulative hazard function

**Reports:**
- `survival_report.md` — Markdown summary of one endpoint's results, written into that endpoint's `results/<endpoint>/` subdirectory; the machine-readable source the PDF is assembled from (one per analysed endpoint).
- `survival_report.pdf` — Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

## Clarification Questions

🚨 **ALWAYS ask Question 1 FIRST.**

### 1. **Example or Own Data?** (ASK THIS FIRST):
   - **a) Rotterdam Breast Cancer** (recommended)
     - 2,982 patients with overall survival (OS) and recurrence-free survival (RFS) endpoints, grade, tumor size, nodal status, hormone/chemotherapy, ER/PGR
     - Ships with the `survival` R package — no download, runs instantly
   - **b) TCGA Breast Cancer** (requires RTCGA.clinical)
     - 1,100+ patients with overall survival, molecular subtypes, stage, age, ER/PR/HER2 status
     - **Requires download** (~50MB via RTCGA.clinical, frequently unavailable). If the package is missing, the loader errors with a named fallback to `load_example_data(dataset = "rotterdam")`
   - **c) NCCTG Lung Cancer** (quick demo, no download)
     - 228 advanced lung cancer patients, sex stratification, ECOG performance status
     - Built-in R dataset — runs instantly
   - **d) I have my own clinical data to analyze**
     - Continue to Questions 2-3 below

> **IF EXAMPLE SELECTED (option a, b, or c):** Proceed to Question 2 for analysis options. Skip Question 3.

### 2. **Analysis Options** *(structured — for all datasets)*:
   - **Endpoint(s)?** *(analysed automatically — not a single choice)*
     - Rotterdam carries **two** endpoints (OS and RFS); **both are analysed and presented together** in one report. The workflow never analyses just one endpoint when the cohort carries more than one. OS = death from any cause; RFS = recurrence or death, whichever comes first. (TCGA BRCA and Lung carry overall survival only.)
   - **Stratification variable?**
     - a) Default for dataset (grade for Rotterdam, mol_subtype for TCGA BRCA, sex for Lung)
     - b) Stage
     - c) Age group
   - **Risk stratification method?**
     - a) Median split — 2 groups (recommended)
     - b) Tertiles — 3 groups
     - c) Quartiles — 4 groups

### 3. **Data Details** *(own data only — free-text OK)*:
   - What is the time column name? Units (days/months/years)?
   - What is the event column name? What does 1 represent (death/relapse/progression)?
   - What stratification variable? What covariates for the Cox model?

## Standard Workflow

🚨 **MANDATORY: USE SCRIPTS EXACTLY AS SHOWN - DO NOT WRITE INLINE CODE** 🚨

**Analyse every endpoint the cohort carries.** A cohort may define more than one time-to-event endpoint (Rotterdam carries both **OS** and **RFS**). Derive the set from the data with `available_endpoints(dataset)` and analyse **each** endpoint — never silently pick one. Each endpoint's outputs are written to its own `results/<endpoint>/` subdirectory, and the final report (Step 5) presents all endpoints together. If an endpoint is ever skipped (e.g. genuinely too expensive), state which was run, which was skipped, and why — these example cohorts are small, so run them all. All `results/...` paths in the sections below refer to each endpoint's `results/<endpoint>/` subdirectory.

**Step 1 - Enumerate endpoints and identify the cohort:**
The endpoint definition determines what each hazard ratio means — an OS HR estimates the risk of death from any cause, while an RFS HR estimates the risk of recurrence or death. Analysing both when both exist answers the full clinical question instead of half of it.
```r
source("scripts/load_example_data.R")

dataset   <- "rotterdam"                    # or "tcga_brca" / "lung"
endpoints <- available_endpoints(dataset)   # derived from the data, e.g. c("OS", "RFS")
# Own data: set endpoints to the time/event pair(s) you have and load with
#   load_user_data("path/to/clinical.csv", time_col = "time", event_col = "status")
# inside the loop below (one iteration per endpoint).
```
**DO NOT write custom data loading code. Use the loader functions.**

**✅ VERIFICATION:** `endpoints` lists every endpoint the cohort carries (Rotterdam: `OS`, `RFS`).

**Steps 2-4 - Analyse, visualize, and export EACH endpoint:**
Loop over the endpoints; for each, run the survival analysis, generate all plots, and export all artifacts into that endpoint's `results/<endpoint>/` subdirectory. The Cox model assumes proportional hazards — that the hazard ratio between any two patients is constant over time; the workflow tests this automatically and, when violated, runs a time-split and stratified sensitivity analysis. Reference levels for categorical covariates are selected by clinical convention (not largest group), so HRs are interpretable relative to the clinically standard baseline (e.g., low grade, no treatment). Exporting produces the canonical CSVs (key_metrics, reference_levels, missingness_assessment) the report must cite — every N, HR, CI, p-value, and C-index is copied from these files, never recomputed from memory. The consistency gate verifies row-count invariants before the export token is printed.
```r
source("scripts/basic_workflow.R")
source("scripts/survival_plots.R")
source("scripts/export_results.R")

results <- list()
for (ep in endpoints) {
    data   <- load_example_data(dataset = dataset, endpoint = ep)
    result <- run_survival_analysis(data)
    # Optional: run_survival_analysis(data, risk_strata_method = "tertiles")
    # Optional: run_survival_analysis(data, covariates = c("age", "stage"))

    outdir <- file.path("results", ep)      # e.g. results/OS, results/RFS
    generate_all_plots(result, output_dir = outdir)
    export_all(result, output_dir = outdir)
    results[[ep]] <- result
}
```
**DO NOT write inline Cox/KM code (coxph, survfit), inline plotting code (ggsave, ggplot, ggsurvplot), or custom export code. Source the scripts and call the functions.**

**The plotting script handles PNG + SVG export with graceful fallback for SVG dependencies.**

**✅ VERIFICATION (per endpoint):** For each endpoint you MUST see:
- `"✓ Survival analysis completed successfully!"`
- `"✓ All survival plots generated successfully!"`
- `"=== Export Complete ==="`

**❌ IF YOU DON'T SEE THESE:** You wrote inline code, or skipped an endpoint. Stop, use `source()`, and analyse every endpoint in `endpoints`.

⚠️ **CRITICAL - DO NOT:**
- ❌ **Analyse only one endpoint when the cohort carries several** → **STOP: loop over `available_endpoints(dataset)`**
- ❌ **Write inline Cox/KM code (coxph, survfit)** → **STOP: Use `source("scripts/basic_workflow.R")`**
- ❌ **Write inline plotting code (ggsave, ggplot, ggsurvplot)** → **STOP: Use `generate_all_plots()`**
- ❌ **Write custom export code** → **STOP: Use `export_all()`**
- ❌ **Try to install svglite** → script handles SVG fallback automatically

**⚠️ IF SCRIPTS FAIL - Script Failure Hierarchy:**
1. **Fix and Retry (90%)** - Install missing package, re-run script
2. **Modify Script (5%)** - Edit the script file itself, document changes
3. **Use as Reference (4%)** - Read script, adapt approach, cite source
4. **Write from Scratch (1%)** - Only if genuinely impossible, explain why

**NEVER skip directly to writing inline code without trying the script first.**

**Step 5 - Generate final report (MANDATORY terminal step — the run is not complete until this has happened):**
Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

Every number in the report MUST be copied from each endpoint's `results/<endpoint>/key_metrics.csv`, `results/<endpoint>/reference_levels.csv`, or the other exported CSVs — never recomputed from memory.

**✅ VERIFICATION:** A PDF report exists in the output directory.

## Common Issues

| Error | Cause | Fix |
|-------|-------|-----|
| **"No valid covariates found"** | All columns have >20% missing or single value | Provide covariates explicitly: `run_survival_analysis(data, covariates = c("age", "stage"))` |
| **"Cox model failed with all covariates"** | Collinear or non-convergent covariates | Script auto-falls back to stepwise. Inspect individual p-values. |
| **PH assumption violated (global p < 0.05)** | Time-varying effects | Note in report. Consider stratified analysis. See `references/cox-regression-guide.md`. |
| **"Event column must be binary (0/1)"** | Non-standard event coding | Recode: e.g., `survival::lung` uses 1=censored, 2=dead → script handles this. |
| **RTCGA.clinical unavailable** | Package not installed / download blocked | Use `dataset = "rotterdam"` (breast cancer, 2,982 patients, no download) or `dataset = "lung"` as fallback. |
| **SVG export failed** | Missing optional dependency | Normal — `generate_all_plots()` falls back automatically. PNG always generated. |
| **KM curve drops steeply despite low event rate** | **Heavy censoring (correct behavior)** | **NOT A BUG.** With heavy censoring (e.g., 90% censored), the at-risk set shrinks so each late event causes a large survival drop. The KM tail (N at risk < 30) is unreliable. Report **landmark survival rates** instead. |
| **Subtype medians have upper CI = NA** | **KM never crosses 50% for that group** | The median is an unreliable extrapolation. The script flags this — use landmark rates instead. Do NOT report these medians as reliable point estimates. |

## Agent Summary Guidelines

When presenting final results to the user, the agent MUST:
1. **Read `results/key_metrics.csv` and `results/reference_levels.csv`.** Every N, HR, CI, p-value, C-index, and reference level in the report MUST be copied from those files or from the other exported CSVs (`cox_coefficients.csv`, `survival_summary.csv`, `ph_assumption_test.csv`, `missingness_assessment.csv`, `ph_sensitivity.csv`). If a number is not in a file, re-run the relevant step. Do NOT estimate, round from memory, or recalculate.
2. **Never fabricate survival curve descriptions** — reference the actual generated plots
3. **Never report unreliable medians as if they are reliable** — when `key_metrics.csv` shows `median_reliable = FALSE`, report "Median survival: Not reached" and use landmark survival rates instead
4. **Methods section MUST match actual model** — list only covariates from `cox_coefficients.csv`. Check `key_metrics.csv` for `n_dropped_covariates` and report what was excluded and why. NEVER list covariates from memory.
5. **Report informative missingness** — if `missingness_assessment.csv` has any row with `informative = TRUE`, report the event rate comparison prominently and note selection bias risk
6. **Report follow-up anomalies** — if `result$diagnostics$followup_anomaly` is TRUE, investigate and explain prominently. Do NOT dismiss as "expected" without evidence.

⚠️ **CRITICAL REPORTING RULES:**
- **EPV < 10 + C-index:** If `key_metrics.csv` shows `epv < 10`, you MUST describe the C-index as "potentially overfitted" or "unreliable". NEVER use "good" or "moderate discrimination" without this caveat. The C-index is optimistically biased when EPV is low. Report the optimism-corrected C-index as the headline.
- **PH violation + forest plot/Cox table:** If `key_metrics.csv` shows `ph_violated = TRUE`, you MUST include a prominent warning on the forest plot caption AND any Cox results table: "PH assumption violated (p=X) — HRs represent time-averaged effects and may be misleading." Do NOT present HRs as primary findings without this warning.
- **Small reference groups:** If `reference_levels.csv` shows a reference group with N < 50, flag the estimate as unstable. State the reference group N explicitly.
- **Complete-case warning:** If `key_metrics.csv` shows `complete_case_warning = TRUE`, report that >20% of patients were excluded due to missing covariates and note the selection bias risk.
- **Never fabricate group sizes or statistics.** All Ns, HRs, CIs, and p-values in the report text MUST be copied from the exported CSV files. If a number is not in a file, re-run the relevant step.

## Interpretation Guidelines

- **C-index > 0.7:** Good model discrimination — **ONLY if EPV >= 10**. If EPV < 10, say "potentially overfitted (EPV = X)"
- **C-index 0.6-0.7:** Moderate — useful combined with clinical factors
- **C-index ~ 0.5:** No better than chance
- **HR > 1:** Higher hazard (worse prognosis) per unit increase
- **HR < 1:** Lower hazard (protective effect)
- **HR 95% CI includes 1.0:** Not statistically significant
- **PH global p < 0.05:** Proportional hazards assumption violated — HRs are time-averaged and may be misleading. Must be stated prominently on forest plots and Cox tables, not buried in a later section.
- **EPV < 10:** Model underpowered — C-index likely optimistically biased; consider fewer covariates. NEVER call the C-index "good" when EPV < 10.
- **Median survival "Not reached":** KM curve never crosses 50% — use landmark survival rates instead
- **Low event rate (<15%):** KM curves may drop steeply in the tail due to small at-risk set (heavy censoring), not because most patients die. Always check N at risk at each timepoint.
- **Median follow-up < 2 yr with max obs > 5 yr:** Likely a data quality artifact — investigate completeness of follow-up times for censored patients before interpreting results.

## Suggested Next Steps

1. **Biomarker panel discovery** — Use risk scores as features → `elastic-net-biomarker-panel`
2. **Pathway enrichment** — If molecular subtypes differ → `functional-enrichment-from-degs`
3. **Multi-omics integration** — Combine clinical + omics → `multi-omics-integration-mofa`
4. **Disease trajectory** — Map temporal progression → `disease-progression-longitudinal`
5. **Clinical trial landscape** — Search related interventional trials → `clinicaltrials-landscape`

## Related Skills

| Skill | Relationship |
|-------|-------------|
| `elastic-net-biomarker-panel` | **Downstream** — Use risk scores as features for biomarker selection |
| `disease-progression-longitudinal` | **Complementary** — Trajectory analysis on same clinical data |
| `multi-omics-integration-mofa` | **Upstream** — Factor scores as Cox covariates |
| `bulk-rnaseq-counts-to-de-deseq2` | **Upstream** — DE results inform covariate selection |
| `coexpression-network` | **Upstream** — Module eigengenes as survival predictors |

## References

- Cox DR. Regression Models and Life-Tables. J R Stat Soc B. 1972;34(2):187-220.
- Kaplan EL, Meier P. Nonparametric Estimation from Incomplete Observations. JASA. 1958;53(282):457-481.
- Cancer Genome Atlas Network. Comprehensive molecular portraits of human breast tumours. Nature. 2012;490:61-70.
- Loprinzi CL, et al. Prospective evaluation of prognostic variables from patient-completed questionnaires. J Clin Oncol. 1994;12:601-607.
- Therneau TM. A Package for Survival Analysis in R. R package survival.
- See [references/cox-regression-guide.md](references/cox-regression-guide.md) for detailed Cox PH interpretation
- See [references/risk-stratification-guide.md](references/risk-stratification-guide.md) for risk group methodology
- See [references/biomni_supported_resources.md](references/biomni_supported_resources.md) for Biomni-native tools, queryable databases, data-lake datasets, and preinstalled packages
