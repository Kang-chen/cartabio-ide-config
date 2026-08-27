---
id: "skill_9fd3e0632ea54e148757bb2469292ab8"
name: "adme-ml-modeling"
description: "Use to audit, train, evaluate, save, and apply supervised single-endpoint small-molecule in-vitro ADME models from labeled assay tables. Covers solubility, permeability, clearance, binding, transporter, CYP, hERG, or BBB endpoints; leakage-resistant temporal/scaffold evaluation, censored values, calibrated uncertainty, applicability domains, and model-bundle scoring."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Audit this labelled single-endpoint in-vitro ADME assay table, train a leakage-resistant model with a locked outer temporal or scaffold split and nested inner selection, score the held-out molecules with calibrated intervals and applicability-domain flags, then produce a final PDF report of the locked outer assessment, uncertainty disclosure, and domain status."
---

# ADME ML modeling

Run the deterministic tools in `scripts/biomni_tools.py`. Keep scientific meaning in an
explicit dataset specification; do not infer assay compatibility from a filename or endpoint
name.

## Scope

Build, honestly evaluate, save, and apply supervised single-endpoint small-molecule in-vitro
ADME models from a labelled assay table. One endpoint and one compatible assay signature per
run; nested inner selection with a locked outer prospective-style assessment; censor-aware AFT
fitting; MAPIE conformal intervals or sets; applicability-domain flags; and model-bundle
scoring of new molecules.

**Do not use for:**

- unlabelled pretrained ADMET prediction (no labels to audit or assess);
- atom-level metabolism or metabolite-identification (regioselectivity, CYP site-of-metabolism);
- biologics (antibodies, peptides, proteins — the molecular features and assay context differ);
- in-vivo PK time series (concentration-vs-time profiles require PK/PD modelling, not a single
  endpoint);
- population PK / popPK (covariate-level mixed-effects modelling, not endpoint prediction).

## Inputs

Accepted table formats (read by `read_table`): CSV, TSV, SDF, and SMI/SMILES. The SMILES
column is required; its name defaults to `smiles` but can be set via `smiles_column`.

Required columns:

- `smiles_column` — a valid SMILES string per row.
- `endpoint.label_column` — the measurement column (numeric for regression, two-valued for
  classification).

Optional but recommended columns:

- `date_column` — measurement date; enables temporal splitting (the preferred default).
- `assay_context_columns` — assay/protocol ID, species, matrix, pH, etc.; the audit treats
  distinct combinations as distinct assay signatures and blocks mixed signatures.
- `endpoint.unit_column` — units; the audit blocks mixed or mismatched units.
- `endpoint.qualifier_column` — `<`, `>`, `<=`, `>=` censor qualifiers; preserved as intervals.
- `series_column` — project/series label for grouped random splits.
- `compound_id_column` — external compound identifier.

Bundled asset: `assets/sample-test-set.csv` — 100 **real** molecules with `smiles`,
`design_source`, `design_regime`, and `measured_logD` columns, built by
`scripts/build_sample_test_set.py` against the real Lipophilicity_AstraZeneca benchmark to span
**both** applicability-domain regimes: 50 **in-domain** molecules (`lipophilicity_holdout`)
randomly held out of the Lipophilicity training set — same endpoint and chemical space, real
measured logD, not verbatim reference members — and 50 **out-of-domain** molecules
(`aqsoldb_external`) from a distinct database (AqSolDB), restricted to molecular weights outside
the training set's drug-like MW window (a chemical-property criterion that does not use the AD
metric). Scoring it exercises the in-domain prediction path *and* the extrapolation-warning path.
`design_regime` records the *intended* regime only; the *achieved* nearest-neighbour similarity
and in-domain fraction are measured at runtime (`predictions.csv` / `prediction_manifest.json`),
never assumed and never tuned to a target. At the last regeneration the measured overall in-domain
fraction was **0.72** (in-domain block 0.96, out-of-domain block 0.48). See
`references/example_data.md` for composition, data sources, licences (CC BY 4.0), and the recorded
numbers.

## Operating rules

- Model one endpoint and one compatible assay signature per run.
- Call `inspect_adme_dataset` before training. Resolve `status=blocked`; never guess through
  mixed units, mixed assays, invalid class mappings, or contradictory censoring.
- Prefer a temporal split when complete measurement dates exist. Otherwise use scaffold.
  Use deployment/MOOD when the actual prospective structures are supplied. Treat random as
  an interpolation diagnostic only.
- Keep the locked outer test partition out of feature/model selection, calibration, threshold
  choice, and the locked-test applicability-domain assessment. The AD threshold and the
  locked-test AD flags are fitted on outer-training molecules only. The deployment bundle's AD
  reference set is deliberately different — all audited molecules (train+test) — and is used
  only to score new molecules, never in the locked assessment.
- Preserve `<`, `>`, `<=`, and `>=` labels as intervals. The runtime uses XGBoost AFT for
  censored positive quantities; set `scale: log10` when labels are log10 values of a positive
  physical endpoint.
- Report the locked outer result, the 1-NN baseline, uncertainty coverage, and domain status.
  Do not present inner-CV performance as prospective performance.
- Deliverables describe the analysis, not this skill. A report — its title, subtitle, executive
  summary, and body — states the endpoint, units, task, split, and assessment, and must never
  name this skill, state its version, or reference its development, revision, or repair history.
  Provenance lives only in `run_manifest.json`.
- The locked outer intervals and the deployment-bundle intervals come from different conformal
  estimators — outer is split conformal (single calibration quantile, constant symmetric
  width), deployment is CV+ (per-molecule, asymmetric) — so do not carry a width statement
  from `model_card.md` over to `predictions.csv`. The card describes each estimator separately.
- When the prediction manifest reports a `domain_warning`, lead the summary with it.
- Do not install dependencies into an active Biomni session. Build the pinned runtime from
  `pyproject.toml`/`uv.lock` in an **isolated** environment. Run `preflight` first: it returns
  `unsafe_environment` and refuses to proceed when a virtual-env marker (`VIRTUAL_ENV` /
  `UV_PROJECT_ENVIRONMENT`) points inside the live session workspace, and `missing_dependencies`
  when the pinned stack is absent. Route to the isolated environment instead of installing into the
  session; override the guard only with `ADME_SKILL_ACK_SESSION_ENV=1` for a genuinely isolated
  environment that happens to live under the session workspace.

## Workflow

1. Construct a dataset specification. Read `references/assay_schema.md` when endpoint units,
   class semantics, assay context, or censor qualifiers need framing. Declaring the assay
   context up front matters because ADME measurements are protocol-dependent: a solubility
   value measured at pH 7.4 is not interchangeable with one at pH 2, and merging them silently
   destroys the label scale.
2. Run `inspect_adme_dataset(dataset_spec, output_dir)`. The audit standardises structures,
   blocks mixed assays/units, preserves censoring, and checks for sufficient data — all
   before any model sees the data, so a blocked audit never leaks into a misleading model.
3. If ready, construct a run configuration and run
   `train_adme_model(dataset_spec, run_config, output_dir)`. The runtime locks the outer
   partition first, then selects candidates only on outer-training data. A temporal split is
   the right default for ADME because assay protocols, instruments, and project chemistry
   drift over time — a model validated on random splits over-estimates performance on the
   later, structurally dissimilar molecules that actually arrive in deployment.
4. Read `model_card.md`, `evaluation.json`, and `outer_test_predictions.csv`. Distinguish
   model selection (inner CV) from locked assessment (outer test). The card now describes
   both the outer split-conformal estimator and the deployment CV+ estimator separately.
5. For new structures, run
   `predict_adme_model(model_bundle_path, data_path, smiles_column, output_dir)`. The
   prediction manifest reports applicability-domain counts, interval-width stats, and a
   `domain_warning` when most scored molecules are out of domain.
6. Surface out-of-domain rows and wide intervals or ambiguous class sets before summarizing
   individual predictions. When the manifest carries a `domain_warning`, lead with it.
7. **Mandatory terminal step — the run is not complete until it has happened.** Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

Ask the user only when a missing scientific choice changes the problem: endpoint identity,
units/transformation, class mapping, assay compatibility, or deployment question. Make routine
implementation choices autonomously and record them.

## Default demonstration (real public benchmark)

The default demonstration and every headline number use **real measured** assay data. Fetch a real
single-endpoint benchmark with the standard library only (no credentials, no added dependency), then
run the ordinary audit → train → predict workflow:

```
python scripts/fetch_benchmark.py --name lipophilicity_astrazeneca --out /workspace/lipophilicity.csv
```

`fetch_benchmark.py` downloads **Lipophilicity_AstraZeneca** — 4,200 real experimental logD₇.₄
values (AstraZeneca 2016 via MoleculeNet; Harvard Dataverse/TDC; **CC BY 4.0**) — and
`dataset_spec_for(...)` frames it as `task=regression, scale=linear` (logD is a log-ratio that can
be negative; **not** `log10`) with a **scaffold** split (no dates). Feed that spec through
`inspect_adme_dataset` → `train_adme_model` (feature sets `ecfp` + `desc2d`) →
`predict_adme_model` on `assets/sample-test-set.csv`. Headline metrics then come from real labels
(a deterministic build at authoring selected XGBoost on 2-D descriptors with locked-outer R² ≈ 0.64,
MAE ≈ 0.61 logD, Spearman ≈ 0.79). `fetch_benchmark.py` also exposes AqSolDB (aqueous solubility,
CC BY 4.0); it records source, licence, and citation on every run.

The synthetic `scripts/make_example_data.py` generator is retained **only** as an offline,
network-free smoke test — it is not real measurements and its metrics must never be reported as
real-assay performance. There is no synthetic fallback for the demonstration: if the real fetch
cannot run unattended, stop and say so rather than substituting synthetic data. See
`references/example_data.md` for sources, licences, and the recorded numbers.

## Dataset specification

Pass a JSON-compatible object:

```json
{
  "data_path": "/workspace/caco2.csv",
  "smiles_column": "smiles",
  "date_column": "measured_at",
  "assay_context_columns": ["assay_id", "species", "matrix", "pH"],
  "series_column": "project_series",
  "endpoint": {
    "label_column": "log10_papp",
    "task": "regression",
    "scale": "log10",
    "unit": "log10 cm/s",
    "unit_column": "unit",
    "qualifier_column": "qualifier"
  }
}
```

For classification, provide an explicit mapping unless the source is already numeric `0/1`:

```json
{
  "label_column": "call",
  "task": "classification",
  "unit": "class",
  "class_mapping": {"inactive": 0, "active": 1},
  "positive_class": "active"
}
```

Do not set `allow_mixed_assays: true` merely to bypass an audit. Use it only after documented
harmonization establishes that the assay contexts share a response scale.

## Run configuration

Use conservative defaults:

```json
{
  "split": "auto",
  "test_fraction": 0.2,
  "inner_splits": 3,
  "feature_sets": ["ecfp", "desc2d"],
  "confidence_level": 0.9,
  "calibration_fraction": 0.2,
  "probability_threshold": 0.5,
  "n_bootstrap": 300,
  "seed": 0
}
```

- `auto`: time when a date column is declared, otherwise scaffold.
- `deployment`: require `deployment_path`; Splito MOOD selects the candidate split whose
  distance profile best matches those prospective structures.
- `cluster`: hold out molecular feature clusters.
- `random`: use only when interpolation among similar compounds is the deployment question.
- `models`: optionally restrict the maintained ladder. Uncensored tasks support dummy, true
  Morgan/Tanimoto 1-NN, ridge/logistic, and XGBoost. Censored tasks support only bound-aware
  `aft_constant` and `aft_xgb`.

Read `references/validation_design.md` before overriding the split or interpreting selection.

## Biomni registration

Register the functions, not the markdown workflow:

```python
from biomni_tools import inspect_adme_dataset, predict_adme_model, train_adme_model

agent.add_tool(inspect_adme_dataset)
agent.add_tool(train_adme_model)
agent.add_tool(predict_adme_model)
```

Use `tool_descriptions()` from the same module when generating Biomni tool-description entries.
See `references/biomni_runtime.md` for environment and deployment details.

## Required outputs

A training run must provide:

- `dataset_audit.json` and `split_assignments.csv`
- `model_comparison.csv` with an explicit selected row
- `evaluation.json` with locked outer metrics and grouped bootstrap intervals
- `outer_test_predictions.csv` with uncertainty and domain columns when supported
- `model_bundle.joblib` and `model_card.md`
- `run_manifest.json` with input, artifact, configuration, and dependency hashes/versions
- `report_adme_model.pdf` — Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

Prediction runs must provide `predictions.csv` plus `prediction_manifest.json`. The manifest
reports applicability-domain counts, interval-width stats, and a `domain_warning` when most
scored molecules are out of domain. Preserve rows with invalid structures and label them
`invalid_structure` instead of silently dropping them.

## Scientific caveats

- **Assay-context non-transferability.** A model trained on one assay signature (protocol,
  species, matrix, pH) does not transfer to another, even if both are called "solubility" or
  "Papp". The audit blocks mixed signatures by default; do not bypass it without documented
  harmonization.
- **Conformal validity under shift.** Conformal coverage guarantees rely on
  exchangeability. Temporal or chemical distribution shift weakens them — under-coverage on a
  time/scaffold test is a shift warning, not a calibration bug to relabel as a guarantee.
- **In-domain flag is not proof of accuracy.** The applicability-domain flag checks
  nearest-neighbour similarity against a threshold; it is a support indicator, not a
  correctness guarantee. An out-of-domain flag means the prediction is an extrapolative
  hypothesis requiring experimental confirmation.
- **Censored limits are intervals, not measurements.** `<` and `>` values are modelled as
  intervals via XGBoost AFT, never converted to exact values. MAE/RMSE are reported only on
  exact observations; interval C-index is the primary metric when censoring is material.

## Interpretation

Read `references/interpretation.md` before communicating model quality or individual
predictions. Always state:

- the outer split and intended deployment question;
- lift over dummy and 1-NN baselines from inner selection results;
- uncertainty coverage on the locked test set, not just nominal confidence;
- the fraction and identity of out-of-domain predictions;
- assay, unit, censoring, sample-size, and distribution-shift limitations.
