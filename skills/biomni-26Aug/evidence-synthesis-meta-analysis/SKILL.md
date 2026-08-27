---
id: "skill_c8c810255403eb169f5a580f37c919fb"
name: "evidence-synthesis-meta-analysis"
description: "Use to systematically synthesize and pool effect estimates across studies or trials. Supports continuous, binary, and time-to-event outcomes with random-effects meta-analysis, forest plots, heterogeneity/I-squared, PRISMA-style evidence review, and uploaded effect-size tables."
category: "data_analysis"
visibility: "public"
starting-prompt: "Meta-analyze the evidence for ..."
---

# Evidence Synthesis & Meta-Analysis

Pool effect estimates across studies into a rigorous random-effects meta-analysis with a full
robustness suite and a publication-grade PDF report (with a visual infographic). Works for any
intervention or exposure question — the source workflow was GLP-1 receptor agonists for weight loss,
but nothing here is drug- or disease-specific.

## Scope
- **Does**: study discovery (optional), PRISMA-style screening, structured effect-size extraction,
  generic-inverse-variance random-effects pooling, heterogeneity + prediction interval, subgroups /
  meta-regression, leave-one-out + influence + small-study diagnostics, 4 media-checked figures, and a
  Phylo-branded PDF (infographic, intro, methods, results, discussion, limitations, next steps,
  references, appendix).
- **Effect measures**: continuous (`MD`, `SMD`) and binary/time-to-event (`OR`, `RR`, `HR`), one
  measure per analysis, pooled on the correct scale (ratios logged then back-transformed).
- **Does NOT** (v1): single-arm proportion/prevalence meta-analysis, network/multivariate
  meta-analysis, individual-participant-data meta-analysis, or diagnostic-test-accuracy (bivariate)
  models. Flag these and stop rather than approximating them.

## Inputs (two modes)
1. **User-supplied extraction table** — a CSV matching `references/extraction_table_template.csv`
   (one row per comparison: `study, year, measure, effect, ci_lo, ci_hi, se, n_trt, n_ctrl, subgroup,
   design, source_id, notes`). Go straight to pooling.
2. **Literature-driven** — start from a research question, discover studies with the Biomni
   **`LiteratureSearch`** tool, screen them, and extract effects from the papers into that same table.

Either way the analysis engine consumes `data/extraction_table.csv`.

## Outputs (saved under a run folder in `/mnt/results/`)
- `data/`: `extraction_table.csv`, `screening_log.csv`, `meta_results.csv`, `meta_model.rds`,
  `leaveoneout.csv`, `influence_diagnostics.csv`, `smallstudy_test.txt`, `risk_of_bias.csv`,
  `references_used.csv`.
- `figures/` (PNG + SVG, each media-checked): `forest_main`, `funnel`, `leaveoneout`,
  `heterogeneity_panel`, plus `infographic.png` (from `GenerateImage`).
- `report_<topic>_meta.pdf` — the final report.

## Workflow

### 0. Confirm the analysis is in scope
Confirm the effect measure and that one measure will be pooled (never mix MD with OR). If the request
is a proportion/network/IPD/diagnostic meta-analysis, say it's out of scope for this skill.

### 1. Get the data
- **Mode 1 (table given)**: validate it against `references/extraction_table_template.csv`. Check every
  row has an effect + (CI or SE), a consistent `measure`, and a `source_id`.
- **Mode 2 (literature-driven)**:
  1. Use **`LiteratureSearch`** with the research question (apply `year_min`, `study_types` such as
     `RCT`/`Meta-Analysis`, `sjr_max`, `human` filters as appropriate). Records land in
     `references.jsonl`.
  2. Screen records (PRISMA): record every INCLUDE/EXCLUDE with a reason in `screening_log.csv`.
     Common exclusions: wrong population, wrong comparator, duplicate cohort (same participants in an
     extension — do not double-count), different estimand (e.g. randomized-withdrawal or post-lead-in
     designs), no extractable effect + dispersion.
  3. Extract the between-group effect + 95% CI from each included paper into `extraction_table.csv`.
     Read `references/methodology_notes.md` for SE derivation, per-arm-CI combination, and log-scale
     rules. Prefer the intention-to-treat / treatment-policy estimand and one arm per study.

### 2. VERIFY every number and citation (non-negotiable)
Before anything enters the report, verify each extracted effect and each citation (title, authors,
year, journal, DOI, NCT/PMID) against its source. If a verbatim transcript artifact exists, regex-check
each key value: `rg -i "<keyword>" /mnt/results/execution_trace/transcript.jsonl`. If the transcript is
missing, verify directly against the fetched source and say exact-wording recovery is unavailable —
never reconstruct numbers or references from memory. Fabricated effect sizes or citations are the worst
possible failure here.

### 3. Run the meta-analysis
Run `scripts/run_meta_analysis.R`. Set the config block or pass env vars:
```bash
META_INPUT=data/extraction_table.csv META_OUTDIR=/mnt/results/<run> \
META_MEASURE=MD META_SUBGROUP=subgroup META_TOPIC="<topic>" \
Rscript scripts/run_meta_analysis.R
```
Defaults (opinionated, documented in `references/methodology_notes.md`): random-effects, **REML**,
**Hartung-Knapp** CI, prediction interval, subgroup Q test. The script auto-installs `meta`/`metafor`
to `/workspace/.Rlib` if missing and emits all CSVs + 4 figures. Escape hatches: `META_TAU=DL`,
`META_HK=FALSE` (document any deviation).

### 4. Robustness (already produced by the script — interpret it)
- **Leave-one-out** (`leaveoneout.csv`): confirm no single study flips the conclusion.
- **Influence** (`influence_diagnostics.csv`): note influential studies (Cook's D, studentized
  residual) but investigate rather than delete — usually genuine effect variation, not error.
- **Small-study effects** (`smallstudy_test.txt`): Egger reported for completeness; **only call it
  publication bias when k >= 10**. With fewer studies, asymmetry is confounded by true heterogeneity.

### 5. Risk of bias
Write a structured narrative `data/risk_of_bias.csv` (columns: `study, randomization, deviations,
missing_data, measurement, selective_report, overall`; Low / Some concerns / High). This is a narrative
aid, not an automated RoB2 score — say so in the report.

### 6. Media-check every figure
For each PNG in `figures/`, run the `Read` tool in `media_output_check` mode. If a figure is blank,
clipped, unreadable, or low quality, fix and regenerate before continuing. (In the source workflow the
funnel plot needed `back="white"` and a solid reference line; the leave-one-out needed wrapped labels
and wider margins.)

### 7. Build the infographic (GenerateImage)
Create a one-page conceptual **infographic** with the **`GenerateImage`** tool summarizing: the headline
pooled effect + CI, the direction/magnitude, the effect ordering by subgroup (if any), heterogeneity in
plain words, and the take-home message. Save it as `figures/infographic.png`. Media-check it. This is a
schematic visual, so it MUST use `GenerateImage`, not plotting code. If `GenerateImage` is unavailable,
the report still builds without it (documented fallback).

### 8. Build the PDF report
This skill **chains the `pdf-report-generation` skill's conventions** (brand palette, ReportLab
Platypus patterns, `<super>`/`<sub>` tags never Unicode, `hAlign='CENTER'`, `KeepTogether` for figures).
Write a `config.json` (see the header of `scripts/build_report.py` for all keys: `title`, `subtitle`,
`measure`, `effect_word`, `outdir`, `out_pdf`, `infographic`, `narrative` with
`background`/`interpretation`/`limitations`/`next_steps`, and `references` with real DOIs). Then:
```bash
python scripts/build_report.py config.json
```
The report includes the infographic, intro, methods, results (all 4 figures + tables), discussion,
limitations, next steps, references, and a PRISMA appendix. The script validates the PDF (pypdf: pages,
size, extractable text). Then **media-check the rendered PDF pages** and regenerate on any defect.

### 9. Save a computational-record notebook
Save the code + key outputs to `/mnt/results/execution_trace/<machine_id>.ipynb` for reproducibility.

## Environment resources this skill uses
| Resource | Role |
|---|---|
| `LiteratureSearch` (Biomni) | Study discovery; filters by year, study type, journal quality, human-only |
| Direct Python/R imports and CLI checks | Confirm required packages/tools before running |
| R: `meta`, `metafor` | Pooling engine + diagnostics (auto-installed to `/workspace/.Rlib` if absent) |
| R: `ggplot2`, `ggrepel`, `patchwork` | Leave-one-out & heterogeneity/influence figures (base-R fallback if absent) |
| Python: `reportlab`, `pypdf`, `Pillow` | PDF assembly + validation + figure sizing |
| `GenerateImage` | Executive infographic (schematic visual) |
| `pdf-report-generation` skill | Brand conventions the report follows |

## Scientific caveats
- **One measure per pool.** Log-transform OR/RR/HR before pooling; the script does this automatically
  when `measure` is a ratio.
- **Random-effects, not fixed**, is the default; under heterogeneity a fixed/common-effect CI is
  overconfident. The prediction interval is often more decision-relevant than the CI.
- **Do not mix estimands or designs naively.** Separate RCT vs observational (use `design` subgroup);
  keep change-from-baseline distinct from post-lead-in "additional" change.
- **Egger/funnel need k >= 10** to interpret as publication bias.
- **Never fabricate or impute** an effect size or citation; verify everything (Step 2).

## Worked example
The `evidence-synthesis-meta-analysis` skill was distilled from a full GLP-1-receptor-agonist weight-loss
review (8 RCTs, pooled MD -10.93 percentage points, I-squared 98.5%). Use that as a mental template for a
continuous-outcome, literature-driven run; the same scripts handle a user-supplied OR table with only a
config change.

## Test prompts (representative triggers)
1. "Meta-analyze the effect of SGLT2 inhibitors on HbA1c versus placebo across published RCTs." *(continuous MD, literature-driven)*
2. "Here's a CSV of 12 trials with odds ratios and 95% CIs for statins and new-onset diabetes — pool them and give me a forest plot and PDF." *(binary OR, user table)*
3. "Synthesize the evidence on whether Mediterranean diet reduces cardiovascular events; include heterogeneity and a publication-bias check." *(ratio outcome, full robustness)*
