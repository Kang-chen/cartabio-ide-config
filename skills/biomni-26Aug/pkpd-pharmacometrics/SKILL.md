---
id: "skill_1c6f8253c75e4fca5754cfe885207a35"
name: pkpd-pharmacometrics
description: "Use to fit population PK or PK/PD models from NONMEM-style concentration-time data with nlmixr2/rxode2. Covers compartmental absorption/clearance models, Emax/effect-compartment/indirect response, covariates, VPCs, multi-dose simulation, exposure-response, and methodologic dose selection gated by a sourced target window."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Fit a population PK/PD model to my concentration-time data and run an exposure-response dose analysis."
---

# Population PK/PD Modeling & Exposure-Response Dose Analysis

## Scope

**What this skill does.** A staged, general-purpose population pharmacometrics pipeline:
ingest & validate concentration-time (± response) data → diagnose the concentration→effect
delay → fit a **population PK** model → optionally fit a **PD** model whose *structure is chosen
from the data* → evaluate (goodness-of-fit + parameter precision + **VPC**) → optionally run
**exposure-response simulation** and a **target-gated dose recommendation** → produce a
Phylo-branded **PDF report** (infographic, intro, methods, results, conclusions, next steps,
references) by delegating to the `pdf-report-generation` skill.

**What this skill does NOT do.**
- It does **not** produce clinical dosing guidance. Every dose output is a model-based
  extrapolation labeled *"methodological illustration, not clinical dosing guidance."*
- It does **not** invent therapeutic target windows (see the hard guardrail below).
- It does **not** write NONMEM control streams, run population meta-analyses across studies,
  or do physiologically-based PK (PBPK). It is nonlinear mixed-effects (NLME) modeling of a
  single dataset.

**Engine.** R with `nlmixr2` / `rxode2` (FOCEi estimation; rxode2 for simulation). Figures use
`ggplot2`/`ggprism`. VPC uses `vpc`/`tidyvpc`.

---

## CRITICAL environment note — the stack is NOT preinstalled

The Biomni base R environment is RNA-seq / single-cell focused (DESeq2, Seurat, clusterProfiler).
The pharmacometric stack (`nlmixr2`, `rxode2`, `nlmixr2est`, `nlmixr2data`, `vpc`) is **not**
preinstalled. rxode2 **compiles ODE models on the fly**, so a **C compiler (gcc/cc) and gfortran
are mandatory** (both are present in the standard sandbox).

**Always** install into a persistent library and set the path in every R invocation:

```bash
# Stage 0 — run scripts/00_setup_env.sh (idempotent; slow first time → run in background)
export R_LIBS_USER=/workspace/.Rlib
```
```r
# Top of EVERY R script/invocation in this skill:
.libPaths(c("/workspace/.Rlib", .libPaths()))
```

Verified working versions in this environment: **rxode2 5.1.2, nlmixr2est 6.0.1,
nlmixr2 5.0.0** on **R 4.4.2**. The install takes ~10–20 min the first time — launch it in the
background and treat it as a **prerequisite gate**: do not start modeling until
`library(nlmixr2)` loads and a trivial `rxode2({d/dt(x) <- -x})` compiles.

---

## Inputs — the schema contract (validate loudly, never drop silently)

Accept a tidy long-format table (CSV/TSV/data.frame) using NONMEM-style data items. Map the
user's columns to these canonical names and **report every mapping, coercion, and dropped row**:

| Item | Meaning | Required | Notes |
|------|---------|----------|-------|
| `ID` | Subject identifier | yes | any type; kept as factor/int |
| `TIME` | Time since first dose (h) | yes | numeric, monotonic within ID |
| `DV` | Dependent variable (obs) | yes | concentration and/or response value |
| `AMT` | Dose amount | yes (≥1 dose row) | 0/NA on observation rows |
| `EVID` | Event ID | recommended | 0 = obs, 1 = dose; derived if absent |
| `CMT` | Compartment | for multi-endpoint | must equal the **endpoint name** for obs (see gotcha) |
| `DVID` | DV type label | for PK+PD | e.g. `cp`/`conc` vs `pca`/`effect`; splits endpoints |
| `MDV` | Missing DV flag | optional | 1 = ignore row |
| `WT`,`AGE`,`SEX`,... | Covariates | optional | for allometric scaling / covariate testing |

**Validation rules (see `references/schema_contract.md`; implemented in
`scripts/01_ingest_validate.R`):**
- Confirm required items exist (or can be derived); **fail loudly** with a clear message if not.
- Count and **report**: n subjects, n dose events, n observations **per endpoint**, rows with
  missing/zero DV, negative/duplicated times, subjects with no dose or no obs.
- Never silently discard rows. If rows are excluded, list how many and why in a validation
  summary that is printed AND embedded in the report Methods.
- If **no distinct response endpoint** is present (only concentrations), set `PK_ONLY = TRUE`
  and skip PD/dose stages gracefully (still produce a report).

Public example data for demos/QA (via `nlmixr2data`): `theo_sd`/`Theoph` (PK-only, single oral
dose) and `warfarin` (PK `cp` + PD `pca`, indirect-response signature).

---

## Outputs

- `tables/` — tidy validated dataset, validation summary, PK parameter table, PD parameter
  table (if fit), exposure-response / dose tables.
- `figures/` — raw-data spaghetti, PK GOF, individual fits, **VPC**, PD GOF (if fit),
  exposure-response, steady-state profile (PNG + SVG each).
- `report_<drug>_popPKPD.pdf` — the Phylo-branded report (see Report section).
- Saved model objects (`.rds`) for reproducibility.

---

## Workflow (staged; degrades gracefully to PK-only)

Track progress with `TodoWrite`. Adapt the reference scripts in `scripts/` to the dataset —
they are templates, not a rigid CLI.

### Stage 0 — Environment gate
Run `scripts/00_setup_env.sh` (background). Verify `nlmixr2` loads and rxode2 compiles a trivial
model before proceeding.

### Stage 1 — Ingest & validate (`scripts/01_ingest_validate.R`)
Apply the schema contract. Emit the validation summary. Set `PK_ONLY` if there is no response
endpoint. Save the tidy dataset.

### Stage 2 — EDA & delay diagnosis (`scripts/02_eda_delay_diagnose.R`)
Plot concentration (and response) vs time (individual + mean). **Compute the delay signature**:
the lag between concentration Tmax and the response peak/nadir, and inspect the
concentration–effect relationship for hysteresis. This drives PD-structure selection. If
`PK_ONLY`, skip the effect analysis.

### Stage 3 — Population PK (`scripts/03_fit_pk.R`)
Fit a 1- or 2-compartment model with first-order absorption (choose by inspection / OBJF).
Random effects (log-normal BSV) on ka/CL/V; combined additive+proportional residual error.
Offer **allometric weight scaling** (exponent 0.75 on CL, 1 on V, referenced to 70 kg) when a
weight covariate exists. Estimate with `est="focei"`; if convergence is poor
(`false convergence`), refit with `foceiControl(outerOpt="bobyqa")`. Report parameters with
asymptotic **%RSE and 95% CI**, BSV %CV, shrinkage. Produce GOF plots, individual fits, and a
**VPC** (default tier).

### Stage 4 — Population PD (optional; skip if `PK_ONLY`) (`scripts/04_fit_pd.R`)
Fit **sequentially**: fix individual PK parameters at their PK estimates; estimate only PD
parameters + PD BSV. **Choose the PD structure from Stage 2's delay signature — do NOT hardcode**
(templates in `references/model_library.md`):
- **Direct Emax / Imax** — negligible delay; effect tracks concentration instantaneously.
- **Effect-compartment (link) model** — moderate hysteresis; add a `ke0` link compartment.
- **Indirect-response / turnover** — large delay (e.g. warfarin ~42 h); drug inhibits/stimulates
  a synthesis (`kin`) or loss (`kout`) rate.
Surface the recommended structure to the user **with the measured delay as justification**;
let them override. Fix a boundary/unidentifiable `Imax`/`Emax` at 1 only when justified (restores
a well-conditioned covariance step). Report PD parameters (%RSE, CI, BSV), GOF, individual fits,
and a PD **VPC**.

### Stage 5 — Exposure-response & dose (optional; **target-gated**) (`scripts/05_exposure_response.R`)
Simulate regimens with rxode2 (steady-state and/or multiple-dose) across a dose grid; summarize
steady-state exposure and predicted effect. **Before emitting any dose, obtain an explicit
therapeutic target window** (see guardrail). Solve for the dose(s) achieving the target
(interpolate); report by body weight if allometric scaling was used. Include time-to-steady-state.

### Stage 6 — PDF report (`scripts/06_report.py`)
Build the report by following the `pdf-report-generation` skill (load it). See Report section.

---

## HARD GUARDRAIL — dose is target-in-required, never target-invented

The skill **must refuse to output any numeric dose** (mg, mg/kg, mg/day) unless a therapeutic
target window is supplied by **one** of:
1. **User-supplied** target on the modeled endpoint (e.g. "keep INR 2–3", "trough > 5 mg/L,
   peak < 20 mg/L", "effect 20–30% of baseline"), or
2. **Literature-cited** target found via the `LiteratureSearch` tool, with the citation recorded
   and shown in the report (e.g. for warfarin: PCA 20–30% of control as an INR 2–3 surrogate,
   O'Leary & Abbrecht 1981; INR≈1/PCA, Xue/Holford 2017).

If neither is available, **do not print a plausible-looking dose.** Instead, either run
`LiteratureSearch` to find a sourced window, or ask the user for one. This prevents the failure
mode of inventing a target and printing a clinical-looking mg/day for a real drug.

**Every** dose output — in chat, tables, and the report — must carry the disclaimer:
*"Model-based extrapolation for methodological illustration; not clinical dosing guidance."*
State the observed-design limits (e.g. "only a single dose level was studied, so alternative
doses are extrapolation").

---

## Model evaluation tiers

- **Default (always):** goodness-of-fit plots (OBS vs PRED/IPRED, CWRES vs time & vs PRED),
  parameter precision (**asymptotic %RSE + 95% CI**), **condition number**, and a
  **Visual Predictive Check (VPC)** — simulation-only, cheap, and the single most informative
  diagnostic.
- **Opt-in upgrade (only if the user asks):** **bootstrap confidence intervals** (many model
  refits — computationally expensive; document runtime and consider a larger machine). Do not run
  bootstrap by default.

---

## PDF report (delegate to `pdf-report-generation`)

Load the `pdf-report-generation` skill and follow its Phylo branding, ReportLab patterns, and
validation. The report MUST contain, in order:

1. **Infographic / one-page visual summary** — headline: chosen PK & PD model structures, key
   parameter values (CL/V/ka; PD params), the exposure-response headline (recommended dose +
   target window + its citation), and the disclaimer. Built with ReportLab callout + key-number
   "cards" + a small chart (per the pdf skill), `hAlign="CENTER"` on every element.
2. **Introduction** — drug/endpoint context and modeling objectives.
3. **Methods** — data + **validation summary**, PK model, **PD-structure choice with the
   measured-delay justification**, estimation settings, evaluation approach, dose-target source.
4. **Results** — parameter tables + all figures with captions: raw data, PK GOF, individual
   fits, **VPC**, PD GOF (if fit), exposure-response, steady-state profile, dose-by-weight table.
5. **Conclusions** — key parameters, headline dose (target-gated, disclaimed).
6. **Next steps** — prospective dose levels to study, external validation, covariate/PGx
   expansion, sequential→simultaneous fit, bootstrap upgrade.
7. **References** — verified via `LiteratureSearch`; inline-numbered; DOIs where available.

**Validate** the PDF (pypdf: ≥2 pages, >5 kB, extractable text) and run
`Read(mode="media_output_check")` on representative pages; regenerate if defects.

---

## Scientific caveats (carry into every run)

- **Sequential PK→PD** fixes PK and does not propagate PK uncertainty into PD/dose. Note it;
  offer a simultaneous fit as a next step.
- **Single-dose-level designs** make dose recommendations extrapolations beyond observed data.
- **Small / imbalanced cohorts** limit covariate and pharmacogenetic (e.g. CYP2C9/VKORC1)
  inference — flag as discovery-only.
- **Surrogate endpoints** (e.g. PCA for INR) are explicit modeling assumptions; state them.
- rxode2/nlmixr2 have sharp edges — see `references/troubleshooting.md` before debugging blindly.

---

## References within this skill
- `references/schema_contract.md` — data-item dictionary + validation rules + example-data mapping.
- `references/model_library.md` — PK and the three PD structure templates + init heuristics.
- `references/troubleshooting.md` — verified nlmixr2/rxode2 gotchas and fixes.
- `scripts/` — staged reference implementations to adapt.
- `examples/example_prompts.md` — trigger prompts for QA.
