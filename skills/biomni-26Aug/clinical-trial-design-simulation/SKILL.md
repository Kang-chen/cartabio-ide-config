---
id: "skill_70b5de4b518cd101e23530cd587523c4"
name: "clinical-trial-design-simulation"
description: "Use to size, power, simulate, or compare two-arm confirmatory clinical trials with survival, binary, or continuous endpoints. Supports fixed and adaptive designs, interim monitoring, futility, enrichment, sample-size re-estimation, and validation of power, type-I error/FWER, duration, and expected sample size."
category: "experimental_design"
visibility: "public"
starting-prompt: "Size and power my two-arm trial by simulation (endpoint, effect size, adaptive features) and report operating characteristics with a design PDF."
---

# Clinical Trial Design & Simulation

## Scope
Simulate and evaluate **two-arm confirmatory trial designs** (one primary
endpoint) end-to-end: it powers designs, computes operating characteristics under
user-specified effect scenarios, **enforces two validation gates** (type-I/FWER
control and agreement with the rpact analytic benchmark), and renders a
Phylo-branded PDF. It does **NOT** analyze completed-trial data, compute a final
p-value for real patients, or handle multi-arm/platform, Bayesian, dose-finding,
or basket/umbrella designs (see `references/caveats.md`).

## Inputs
- A **JSON config** (see `references/config_schema.md` and the three runnable
  examples in `scripts/config_examples/`). It specifies the endpoint, design and
  adaptation parameters, the effect scenarios to evaluate, the validation grids,
  runtime preset, and the report narrative/branding.
- No patient data. Everything is generated from the config's assumptions
  (control-arm parameters, treatment effects per biomarker subgroup, prevalence,
  accrual, dropout).

## Analysis population & internal consistency (define once, use everywhere)
The design's structure is defined **once** in the config, and the tables, figures,
and report prose are derived from those same values so they cannot drift apart:

- **Analysis hypotheses are set by `prevalence`.** `prevalence = 1.0` (or
  `allow_enrich = false`) ⇒ a **single full-population hypothesis** `H_F`: the
  report/figures show one power series ("Power") and a "Type-I rate", and never
  mention a biomarker subgroup, `H_S`, "any", enrichment, or a closed test.
  `prevalence < 1` **with** `allow_enrich = true` ⇒ the **{H_F, H_S} closed
  test**, and the report shows both hypotheses plus enrichment. You do not restate
  the subgroup anywhere else; it follows from `prevalence`. (An explicit
  `design.single_hypothesis` boolean overrides the inference if ever needed.)
- **Operating parameters are reported from the config actually simulated.** The
  Methods text derives the **interim timing** from `info_frac` (one interim at
  that information fraction, else a single final look) and states the exact
  **`dropout_rate`** used — it never hard-codes timing or dropout that could
  disagree with the simulation.
- **The target effect size is specified once.** Give the effect label in
  **`report.effect_label`** (authoritative). If `design.effect_label` is also
  present and disagrees, the report build **fails loudly** so the ambiguity is
  fixed at the source rather than showing two different effects. The infographic's
  power/`E[N]` cards are read from the `headline_scenario` row, so keep that
  scenario consistent with the effect label.

## Outputs (saved under an output dir; copy user-facing files to results)
- `tables/gate_fwer.csv`, `tables/gate_power.csv` — the two validation gates.
- `tables/operating_characteristics.csv` — power (full-population `H_F`; plus the
  subgroup `H_S` and "any" only when the design uses a biomarker subgroup),
  expected N, expected duration, and adaptation probabilities per scenario, with
  Monte-Carlo SE.
- `tables/sensitivity_analysis.csv` — one-parameter sweep (optional).
- `figures/*.png` + `*.svg` — power-by-scenario, expected N/duration, adaptation
  probabilities, and sensitivity figures (Okabe-Ito, editable SVG).
- `report.pdf` — Phylo-branded report: at-a-glance infographic, introduction,
  methods, results (validation + OC + figures), conclusions, references, next steps.

## Environment
- **R** with `rpact` (boundaries + analytic benchmark), `survival`, `data.table`,
  `ggplot2`, `svglite`, `jsonlite`. **Python** with `reportlab` + `pypdf` for the PDF.
- Statistical details are in `references/design_methods.md`; the validation
  philosophy in `references/validation_guide.md`.

## Workflow

The fastest path is the **turnkey driver** — one config in, validated tables +
figures out, then render the PDF.

```bash
cd scripts
# 1. Validate + simulate everything from a config (quick = iterate; thorough = quote)
Rscript run_pipeline.R config_examples/nsclc_egfr_enrichment.json /workspace/run1 quick
# -> runs BOTH gates (halts if a gate fails), then OC grid + sensitivity + figures.
# 2. Render the Phylo PDF (command is printed at the end of step 1)
python3 build_report.py --config config_examples/nsclc_egfr_enrichment.json \
    --tables /workspace/run1/tables --figures /workspace/run1/figures \
    --out /mnt/results/<name>/report.pdf
```

### Step-by-step (what each step is for, and why it matters)
1. **Write/adapt the config.** Start from the closest example. Set the endpoint,
   control-arm assumptions, the target effect(s) **per biomarker subgroup**
   (`hr_pos`/`hr_neg`, `p_trt_*`, `mean_trt_*`/`delta_*`), prevalence, and which
   adaptations to enable. Prevalence `1.0` ⇒ a single full-population hypothesis.
2. **Run the validation gates FIRST (enforced).** `run_pipeline.R` runs
   `gate_fwer` (FWER ≤ alpha under the global + least-favorable nulls) and
   `gate_power_vs_rpact` (simulated power matches rpact for the reduced,
   single-hypothesis design). *If either fails, stop and fix the design — do not
   report operating characteristics from an unvalidated design.* This is the step
   that makes the numbers trustworthy, and the step that catches bugs in any new
   adaptation logic.
3. **Compute operating characteristics.** The grid simulates every effect
   scenario (always include a null) and reports power for the full population, the
   biomarker+ subgroup, and either, plus expected N/duration and the probability
   of each adaptive decision (enrichment, futility, early efficacy, SSR).
4. **Sensitivity analysis.** Sweep the one or two assumptions you are least sure
   about (prevalence, control rate, effect size, SSR target) — design conclusions
   should be robust to these, and reviewers will ask.
5. **Render the PDF.** `build_report.py` turns the tables + figures + config
   narrative into a self-contained report with an infographic and the standard
   sections. It re-checks page count and text before declaring success.
6. **Iterate on `quick` (nsim = 1000), finalize on `thorough` (nsim = 10000).**
   Every reported proportion has Monte-Carlo SE `sqrt(p(1-p)/nsim)`; quote numbers
   only from a thorough run.

### Using the pieces directly (R)
```r
source("scripts/validate_design.R"); source("scripts/run_grid.R")
gate_fwer(base_null_scenario, null_variants = list(...), nsim = 10000)   # -> list(table,pass,worst)
gate_power_vs_rpact("tte", median_ctrl = 18.9, hr_grid = c(.60,.65,.70)) # -> list(table,pass,worst)
run_grid(base, scenarios, preset = "thorough")                          # OC table
```

## Statistical caveats (read before quoting results)
- **Assumptions in, operating characteristics out.** Results are only as valid as
  the config's assumptions; always state them and show sensitivity.
- **Type-I error is verified by simulation, not proven** — choose least-favorable
  nulls deliberately (prevalence extremes for enrichment designs).
- **TTE designs are event-driven.** Power depends on reaching the target *events*;
  the validation harness uses a large subject pool with no administrative cap so
  the simulated and rpact designs refer to the same trial. A finite follow-up cap
  that prevents reaching the events causes a spurious power shortfall (a sizing
  artifact, not a bug) — see `references/validation_guide.md`.
- **Adaptations (enrichment, SSR) preserve alpha only through the fixed-weight
  inverse-normal combination.** The `allow_ssr` path is new code; Gate 1 is where
  its FWER is confirmed for your specific design.
- Parametric generators (exponential/Weibull, Bernoulli, normal); no
  non-proportional hazards, over-dispersion, or informative dropout. Asymptotic
  tests degrade at very small information. Full list in `references/caveats.md`.

## Reference files
- `references/design_methods.md` — endpoint score/information map, combination
  test, boundaries, closed testing, enrichment, futility, SSR formulas.
- `references/validation_guide.md` — the two gates, tolerances, and troubleshooting.
- `references/config_schema.md` — every config field with defaults.
- `references/caveats.md` — assumptions, scope limits, interpretation guidance.
- `scripts/config_examples/` — `nsclc_egfr_enrichment.json` (TTE + enrichment),
  `binary_response_gs.json` (binary + group-sequential + futility),
  `continuous_ssr.json` (continuous + sample-size re-estimation).
