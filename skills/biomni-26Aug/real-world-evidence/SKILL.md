---
id: "skill_ea1afce75ae2b4fc030bbe787188145e"
name: "real-world-evidence"
description: "Use to run a configurable real-world evidence cohort study on structured EHR or claims data. Covers diagnosis-code cohort construction, Table 1, treatment patterns, Kaplan-Meier/landmark survival, EPV-gated Cox models, and adaptation from MIMIC-IV to other diseases or datasets."
category: "data_analysis"
visibility: "public"
starting-prompt: "Run a real-world-evidence cohort study on clinical EHR data: define a disease cohort by diagnosis codes, characterize baseline, profile treatment patterns, analyze survival vs a comparator, and generate a branded PDF report with an infographic, methods, results, conclusions, figures, and references."
---

# Real-World Evidence (RWE) Clinical Cohort Study

A config-driven pipeline for retrospective cohort studies on structured EHR /
claims-style data. You define one study configuration (disease codes, treatment
classes, comparator, endpoint); the pipeline builds the cohort, a baseline
Table 1, treatment-pattern summaries, Kaplan-Meier / landmark survival with an
EPV-gated Cox model, and a Phylo-branded PDF report with a **data-faithful**
infographic. **Nothing disease-, drug-, or dataset-specific is hardcoded** — the
same scripts run for sepsis, oncology, cardiology, or any code-defined cohort.

## When to Use This Skill

Use this when you need to:
- **Define a patient cohort by diagnosis codes** (ICD-9/10 or other) and compare
  it to a contemporaneous comparator group
- **Build an auto-typed Table 1** (median [IQR] + Wilcoxon; n (%) + Fisher)
- **Profile treatment patterns** — drug-class classification, combination
  therapy, time-to-first-exposure — with a **swappable** treatment map
- **Analyze time-to-event outcomes** with Kaplan-Meier, landmark survival, and
  log-rank, plus multivariable Cox **only when statistically defensible** (EPV gate)
- **Produce a shareable branded PDF** with an infographic, methods, results,
  discussion, conclusions, literature-grounded references, and next steps
- **Reproduce a worked example** end-to-end on the open-access MIMIC-IV demo

**Don't use this skill for:**
- ❌ Single-cohort survival with pre-cleaned time/event columns and no cohort
  building → use `survival-analysis-clinical`
- ❌ Omics-based biomarker panel selection → use `elastic-net-biomarker-panel`
- ❌ Differential expression → use `bulk-rnaseq-counts-to-de-deseq2`
- ❌ Formal causal effect estimation / target-trial emulation with propensity
  weighting → this pipeline is descriptive; see `references/rwe-methodology.md`
  for why and what to do next

## Design Philosophy (read this first)

1. **One config, no hardcoding.** Everything study-specific lives in a single
   `CFG` list (`scripts/00_config_template.R`). Analysis scripts read from `CFG`
   and never contain disease/drug/dataset values. To adapt to a new study you
   edit **only** the config.
2. **Honest defaults.** Descriptive stats + KM + landmark + log-rank always run.
   Multivariable Cox runs **only** if events-per-variable ≥ `CFG$epv_min`
   (default 10); otherwise it is suppressed with a note. All p-values are labeled
   **exploratory**; no multiple-testing correction by default.
3. **The infographic never lies.** It is assembled from the **real computed
   numbers** with ggplot2 (`infographic_mode = "composed_panel"`). An
   image-generation model must **NEVER** render actual numbers or proportional
   bars — at most it may draw an empty design shell with values overlaid
   programmatically (`"generated_shell"`). See "Infographic rule" below.
4. **Arms are defined by exposure, not by code vintage.** For a comparative
   **drug study** (drug A vs drug B), the two arms must be defined by *what
   patients received*. Use `comparator = "active_comparator"`: `cohort_codes`
   become **eligibility** (the disease pool), and the arms come from
   `exposure_cohort_map` / `exposure_comparator_map`. **Never** split arms by
   ICD-code vintage (e.g. ICD-10 `E11` vs ICD-9 `250` for the same disease) —
   coding version is an era/site artifact, not a treatment distinction, and the
   `comparator` setting must match the arms you actually analyze (don't narrate
   an active comparator while configuring "rest of population").

## Installation

```r
options(repos = c(CRAN = "https://cloud.r-project.org"))
install.packages(c("data.table", "survival", "ggplot2", "patchwork",
                   "scales", "jsonlite", "dplyr"))
# optional: parquet input support
install.packages("arrow")
```
```bash
# PDF builder (Python)
pip install reportlab pypdf pandas pillow
```

| Software | Version | License | Commercial Use |
|----------|---------|---------|----------------|
| data.table | >=1.14 | MPL-2.0 | ✅ |
| survival | >=3.5 | LGPL (>=2) | ✅ |
| ggplot2 | >=3.4 | MIT | ✅ |
| patchwork | >=1.1 | MIT | ✅ |
| jsonlite | >=1.8 | MIT | ✅ |
| dplyr | >=1.1 | MIT | ✅ |
| reportlab (py) | >=4.0 | BSD | ✅ |
| pypdf (py) | >=4.0 | BSD | ✅ |

## Inputs

Structured, patient-level tables (CSV or parquet), mapped to a canonical schema
via `CFG$cols` (edit the right-hand side to match YOUR column names):

**Required:**
- **patients** — one row per patient: `subject_id`, sex, age or date-of-birth,
  date-of-death (optional)
- **encounters** — one row per admission: `subject_id`, `hadm_id`, admit time,
  discharge time, in-hospital death time / expiry flag
- **diagnoses** — long: `subject_id`, `hadm_id`, `icd_code`, `icd_version`
- **drugs** — long: `subject_id`, `hadm_id`, `drug`, `route`, start time

**Optional:**
- **icu_stays** — `subject_id`, `hadm_id`, `stay_id`, intime, outtime, LOS
  (needed for ICU-anchored time origin or "rest of ICU" comparator)

**Formats:** CSV/TSV or parquet. Minimum ~50 patients recommended; Cox needs
≥ `epv_min` events per candidate covariate or it is suppressed.

## Outputs (written to `CFG$paths$out_dir`)

**Tables (`tables/`):** `cohort_flow.csv`, `table1_cohort.csv`,
`treatment_class_summary.csv`, `treatment_top_agents.csv`,
`treatment_summary.csv`, `survival_landmark_cohort.csv`,
`survival_logrank.csv`, `cohort_vs_comparator.csv`, and `cox_results.csv`
(only if EPV gate passes).

**Figures (`figures/`):** `infographic_summary.png/.svg` (data-faithful
composed panel) plus any KM/treatment figures you add.

**Report:** `report_<slug>.pdf` — Phylo-branded, with infographic summary page,
executive summary, methods (+ parameter table), results (tables + figures),
discussion, limitations, conclusions, next steps, and a References section
populated from `LiteratureSearch`.

**Intermediates (`/workspace/rwe/*.RData`):** analysis objects for reuse.

## Clarification Questions

🚨 **ALWAYS ask Question 1 FIRST.**

### 1. Example or own data? (ASK FIRST)
   - **a) MIMIC-IV demo — sepsis worked example** (recommended; no credentialing).
     Downloads the open-access demo (100 ICU patients) and reproduces the sepsis
     analysis. Runs in minutes. → use `examples/mimic_sepsis_config.R`
     **Licensing note:** only the 100-patient **demo** is open-access (ODbL
     v1.0). The **full MIMIC-IV** is restricted-access (PhysioNet credentialing
     + signed DUA required; redistribution prohibited). Never use the full
     MIMIC-IV from an unauthorized mirror.
   - **b) I have my own clinical data.** → continue to Questions 2–3

### 2. Study definition (own data — free text OK)
   - Cohort: which diagnosis codes define it (ICD version + prefixes/exact codes)?
   - Comparator: rest of population, or rest of ICU?
   - Treatment class of interest and the drug substrings that define each class?
   - Primary endpoint and time origin (admission vs first ICU stay)?

### 3. Column mapping (own data)
   - Confirm the column names for each input table so `CFG$cols` can be filled.

## Standard Workflow

🚨 **Adapt the CONFIG, then run the scripts as-is. Do not rewrite the analysis
scripts inline.** The scripts are config-driven; the correct way to change
behavior is to edit the config, not the scripts.

**Step 1 — Copy and edit the config.**
```bash
cp scripts/00_config_template.R my_study_config.R
```
Edit `my_study_config.R`: set study identity, `paths` (input files + `out_dir`),
`cols` (map your column names), `cohort_codes`, optional `severity_tiers`,
`comparator`, `treatment_label` + `treatment_map`, `primary_endpoint`,
`landmark_times`, `epv_min`, and `literature_queries`. Every field is documented
in `references/config-reference.md`.

**Two ways to define the groups (pick the one that fits your question):**
- **Disease cohort vs everyone else** (`comparator = "rest_of_population"` or
  `"rest_of_icu"`): `cohort_codes` define the cohort; the comparator is the rest
  of the population / ICU. This is the sepsis worked example.
- **Drug A vs drug B — active comparator** (`comparator = "active_comparator"`):
  `cohort_codes` define **eligibility** (the disease pool, e.g. all type-2
  diabetes = `E11` + `250`); the two arms are defined by **exposure** via
  `exposure_cohort_map` / `exposure_comparator_map`, with new-user time zero at
  the first qualifying fill and `exposure_overlap_rule` for patients exposed to
  both. Use this for any comparative drug-effectiveness question — never encode
  the arms as different ICD-code versions. See `config-reference.md` §6b.

**For the worked example, skip copying** and use `examples/mimic_sepsis_config.R`
directly (first fetch the demo data — see Step 2).

**Step 2 — (worked example only) download the open MIMIC-IV demo.**
```r
source("scripts/load_mimic_demo.R")
download_mimic_demo("/workspace/mimic")   # open access, no credentialing
```
This writes the hosp/ and icu/ tables the sepsis config points to.

> ⚠️ **Data-source guardrail (HARD CONSTRAINT).** The MIMIC-IV **demo** (100
> patients) is open-access under the PhysioNet Open Data Commons ODbL v1.0
> license — no credentialing required. The **full MIMIC-IV** is a
> **credentialed, restricted-access** dataset under the PhysioNet Credentialed
> Health Data Use Agreement 1.5.0; access requires PhysioNet credentialing, a
> signed DUA, and CITI training, and the DUA **prohibits redistribution and
> sharing with third parties** (including via APIs or online platforms).
>
> **If `physionet.org` is unreachable from the sandbox, do NOT substitute the
> full MIMIC-IV or any other restricted-access dataset from an unauthorized
> mirror** (e.g. a HuggingFace dataset mirror). Using restricted data from an
> unauthorized mirror does not constitute lawful access under the DUA and is a
> commercial-use violation. Instead, either (a) report the network failure
> clearly and skip the run, explaining the blocker, or (b) run the pipeline on
> a small synthetic fixture that matches the canonical schema (see
> `references/config-reference.md` § 1). Never let a network outage pressure
> you into using data you are not licensed to use.

**Step 3 — Ground the report in literature (agent step).**
Call Biomni **`LiteratureSearch`** with the terms in `CFG$literature_queries`
(disease + treatment + outcome). Write the formatted citation strings, one per
line, to `<out_dir>/tables/references.txt`. `06_manifest.R` picks them up and the
PDF gets a real References section. (Skip only if the user does not want a
literature-grounded report.)

**Step 4 — Run the full pipeline.**
```bash
Rscript scripts/run_all.R my_study_config.R
# worked example:
# Rscript scripts/run_all.R examples/mimic_sepsis_config.R
```
This runs cohort → treatment → Table 1 → survival → comparison → infographic →
manifest, then shells out to `scripts/build_report.py` to build the PDF.
Treatment patterns run before Table 1 so that the treatment-use row is
available when Table 1 is built.

**✅ VERIFICATION:** you should see `== pipeline complete ==` and
`[build_report] OK -> .../report_<slug>.pdf` with `pages>=2`.

**Step 5 — Validate the PDF (MANDATORY).**
Run the visual media check on the generated PDF (`Read` with
`mode="media_output_check"`). Confirm: infographic present and non-blank; all
tables within margins; prose sections flow normally (not one char per line); no
blank/filler pages. Regenerate and re-check if any issue is reported.

### Running individual steps (optional)
Each script is standalone and takes the config as its first argument, e.g.:
```bash
Rscript scripts/02_table1.R my_study_config.R
python scripts/build_report.py /path/to/out_dir
```

## Infographic rule (HARD CONSTRAINT)

Image-generation models must **NEVER** render actual numbers or proportional bar
heights — those values are unverifiable and non-reproducible. Two allowed modes:
- **`composed_panel`** (DEFAULT): `make_infographic.R` builds the summary figure
  from the computed CSVs with ggplot2. Data-faithful by construction.
- **`generated_shell`** (opt-in): `GenerateImage` may draw ONLY an empty layout
  (boxes, arrows, labels, no numbers); every value is then overlaid
  programmatically from the same outputs.

If you are ever tempted to "just have the image model add the numbers" — don't.
That is the one thing this skill forbids.

## Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| **Two drug arms coded as different ICD versions** (e.g. arm A = ICD-10 `E11`, arm B = ICD-9 `250` for the same disease) | Trying to force an exposure contrast through the code-based cohort builder; coding vintage is an era/site artifact, not a treatment | Use `comparator = "active_comparator"`: put all disease codes in `cohort_codes` (eligibility) and define arms with `exposure_cohort_map` / `exposure_comparator_map`. See Design Philosophy #4 and `config-reference.md` §6b. |
| **`comparator` doesn't match the narrative** (config says `rest_of_population` but the report describes an active drug comparator) | Group definition and write-up drifted apart | Set `comparator` to the design you actually analyze; for drug-vs-drug use `active_comparator` so the comparator arm is the specific comparator drug class. |
| **`active_comparator` errors: "requires exposure_*_map / a drugs table"** | Mode selected without the exposure maps or a drugs table | Provide `exposure_cohort_map`, `exposure_comparator_map`, and `CFG$paths$drugs`. Keep the two maps mutually exclusive at the drug level. |
| **0-byte PNG in `figures/`** | R `file.copy()` to S3-backed `/mnt/results` yields 0 bytes | Already handled: `make_infographic.R` stages the PNG on `/workspace` and moves it with a shell `cp`. If you add figures, follow the same pattern. |
| **Conclusions/section text prints one character per line** | A single-element text field was auto-unboxed to a JSON string and iterated char-by-char | Already handled on both sides (`as.list()` in `06_manifest.R`; `mget()` in `build_report.py`). Keep those guards if you edit manifest fields. |
| **`build_report.py` "Style 'X' already defined"** | Custom ParagraphStyle name collides with a ReportLab built-in (e.g. `Bullet`, `Title`) | Use a distinct prefix (the builder uses `RWEBullet`, `RTitle`, etc.). |
| **DataFrame truthiness ValueError** | `csv(a) or csv(b)` on pandas DataFrames | Use the provided `csv_first(...)` helper (None-safe). |
| **Cox suppressed / no `cox_results.csv`** | EPV below threshold, or `cox_covariates` empty | Expected and correct. Reduce covariates, get more events, or raise `epv_min` deliberately. See `references/survival-guardrails.md`. |
| **Comparator in-hospital deaths look too low** | Non-ICU first admission chosen for an ICU-anchored study | Set `time_origin="index_icu_in"` or `comparator="rest_of_icu"`; the cohort builder then selects each comparator's first ICU admission. |
| **Antibiotic/exposure count differs from expectation** | `treatment_exposure_scope` mismatch | `index_encounter` (default, rigorous) vs `any_encounter` (ever-exposed) answer different questions — pick deliberately. |
| **Median survival is `NA`** | KM never crosses 50% (small cohort) | Not a bug — report landmark survival (the pipeline already does). |
| **PhysioNet unreachable / `load_mimic_demo()` fails (connection refused)** | `physionet.org` blocked by sandbox network policy | Do **NOT** substitute the full MIMIC-IV or any restricted-access dataset from an unauthorized mirror (e.g. HuggingFace) — the full MIMIC-IV requires PhysioNet credentialing + a signed DUA that prohibits redistribution. Instead, report the network failure clearly and either skip the run or use a small synthetic fixture matching the canonical schema. See the data-source guardrail in Step 2. |

## Agent Summary Guidelines

When presenting results, the agent MUST:
1. **Copy every number from the CSV outputs or console** — never estimate,
   round from memory, or recalculate group sizes. If a value is not in the
   output, re-run the step or read the file.
2. **Report the cohort flow** (screened → cohort → with-index-encounter) and the
   patient-level denominators used for Table 1 and survival.
3. **Report landmark survival with 95% CI and N-at-risk**, not fragile medians.
   If a median's upper CI is `NA`, say "not reached" and use landmark rates.
4. **State whether Cox ran or was suppressed**, with the EPV value. If it ran,
   report the PH check; if EPV < 10 was overridden, caveat the estimates.
5. **Label all p-values exploratory** and state that no multiple-testing
   correction was applied.
6. **Distinguish a single-timepoint proportion test (Fisher) from the log-rank**
   — they can disagree without contradiction (see `references/survival-guardrails.md`).
7. **State the mortality denominator** (patient- vs admission-level) explicitly
   whenever quoting a mortality figure.
8. **Report the phenotype limitation** — the cohort is code-defined and should be
   validated; recommend it as a next step.
9. **Never describe the infographic or figures from memory** — reference the
   actual generated files.
10. **If the intended data source is unreachable, report the failure clearly.**
    Do not substitute restricted-access or unauthorized-mirror datasets (e.g.
    the full MIMIC-IV from a HuggingFace mirror). Use a synthetic fixture
    matching the canonical schema, or skip the run and explain the blocker.
    The full MIMIC-IV is credentialed data under the PhysioNet DUA, which
    prohibits redistribution and third-party sharing.

## Interpretation Guidelines

- **EPV < 10:** any Cox output is potentially overfitted; describe it as such,
  never as "good discrimination". Prefer descriptive + KM.
- **Log-rank p vs Fisher p:** different estimands (survival curve over time vs
  proportion at one timepoint); report both for what they are.
- **In-hospital vs all-cause survival curves diverge:** expected when deaths
  occur post-discharge (captured by linked date-of-death). Not a bug.
- **Wide late-time CIs:** small at-risk set; interpret tails cautiously.
- **Confounding by indication is intrinsic:** all cohort-vs-comparator contrasts
  are descriptive, not causal.

## Suggested Next Steps

1. **Validate the cohort definition** against chart review or a published,
   validated phenotype; run a sensitivity analysis with an alternative code set.
2. **Scale to the full data source** (e.g. credentialed MIMIC-IV) to increase
   power and EPV, enabling adjusted models. Note: the full MIMIC-IV requires
   PhysioNet credentialing and a signed DUA; obtain access lawfully through
   PhysioNet, never through unauthorized mirrors.
3. **Move toward causal inference** — target-trial emulation with pre-specified
   eligibility, treatment strategies, time zero, and propensity/regression
   adjustment. See `references/rwe-methodology.md`.
4. **Search the trial landscape** for related interventional evidence →
   `clinicaltrials-landscape`.
5. **Detailed time-to-event modeling** on the cleaned cohort →
   `survival-analysis-clinical`.

## Related Skills

| Skill | Relationship |
|-------|-------------|
| `survival-analysis-clinical` | **Downstream/complementary** — deeper Cox/risk-stratification on the cohort this skill builds |
| `clinicaltrials-landscape` | **Complementary** — interventional evidence for the same disease/treatment |
| `elastic-net-biomarker-panel` | **Downstream** — if omics are available for the cohort |
| `pdf-report-generation` | **Used by** — this skill's PDF builder follows its Phylo brand system |

## References

See `references/` for the full methodology:
- `config-reference.md` — every `CFG` field, with examples
- `rwe-methodology.md` — study design, code-based phenotyping, confounding by
  indication, comparator/time-zero alignment, RECORD reporting
- `survival-guardrails.md` — EPV gate, PH assumption, KM/landmark, the
  Fisher-vs-log-rank distinction, mortality denominators, censoring

Key methodological citations (refresh with `LiteratureSearch` per study):
- Peduzzi P, Concato J, et al. Importance of events per independent variable in
  proportional hazards regression. *J Clin Epidemiol.* 1995.
- Stürmer T, et al. Methodological considerations when analysing and
  interpreting real-world data. *Rheumatology.* 2020.
- Zhao R, et al. Reporting quality of cohort studies using real-world data based
  on RECORD: a systematic review. *BMC Med Res Methodol.* 2023.
