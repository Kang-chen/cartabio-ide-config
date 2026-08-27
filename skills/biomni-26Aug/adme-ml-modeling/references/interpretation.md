# Interpreting ADME model output

## Contents

- Reading order
- Example assets
- Regression
- Classification
- Uncertainty and domain
- Claims to avoid

## Reading order

1. Confirm `dataset_audit.json` is ready and inspect its warnings.
2. Confirm `split_assignments.csv` matches the intended deployment question.
3. Read `model_comparison.csv` for dummy/1-NN lift and the selected inner-validation row.
4. Treat `evaluation.json` locked outer metrics as the prospective estimate.
5. Inspect empirical uncertainty coverage and applicability-domain strata.
6. Use `outer_test_predictions.csv` to identify failure modes, not to retune the model.

## Example assets

`assets/sample-test-set.csv` is 100 **real** molecules (`smiles`, `design_source`,
`design_regime`, `measured_logD`) built against the real Lipophilicity_AstraZeneca benchmark to
span both applicability-domain regimes: 50 in-domain molecules (`lipophilicity_holdout`) randomly
held out of the Lipophilicity training set — same endpoint and chemical space, real measured logD,
not verbatim reference members — and 50 out-of-domain molecules (`aqsoldb_external`) from a distinct
database (AqSolDB) restricted to molecular weights outside the training set's drug-like MW window (a
chemical-property criterion that does not use the AD metric). Scoring it exercises both the in-domain
prediction path and the extrapolation-warning path. `design_regime` is the intended regime only; the
achieved nearest-neighbour similarity and in-domain fraction are measured at runtime and reported in
`prediction_manifest.json` (last build: overall 0.72 in-domain; in-domain block 0.96, out-of-domain
block 0.48), never assumed and never tuned. See `references/example_data.md` for composition, data
sources, and licences (CC BY 4.0).

## Regression

Report MAE and RMSE in the declared reporting units, R² for explained variation, and Spearman
for ranking. For log10 endpoints, report the fraction within two-fold. Compare observed error
with replicate spread, but do not call replicate spread a universal irreducible limit unless
the replicate design supports that interpretation.

For censored regression, lead with interval C-index when censoring is material. MAE/RMSE are
computed only where truth is exact and must carry `n_exact`.

## Classification

Report ROC-AUC, average precision, balanced accuracy, MCC, and Brier score. Average precision is
especially important under imbalance. State the positive class and probability threshold.
Prediction sets containing both classes are unresolved; empty sets are calibration anomalies
and should be inspected rather than converted to a confident call.

## Uncertainty and domain

Report nominal confidence together with locked-test empirical coverage. Under-coverage on a
time/scaffold test is a distribution-shift warning. Width stratification differs by artifact:
for the CV+ deployment intervals in `predictions.csv`, reviewing interval width separately
for in-domain and out-of-domain rows is informative (widths are per-molecule); for the
split-conformal outer intervals in `outer_test_predictions.csv`, width is constant by
construction, so that stratification is vacuous and only the `in_applicability_domain` flag
signals extrapolation.

The applicability-domain figures come from two different reference sets. The locked-test
fraction and threshold in `evaluation.json` are computed against outer-training molecules only
(reported in `ad_reference_set_evaluation`); the in/out-of-domain flags in `predictions.csv` are
computed against all audited molecules (reported in `ad_reference_set_deployment`). State which
reference set a figure came from, reading it from those fields rather than restating a constant.

The domain flag is only a trust signal if error actually falls as similarity rises. The runtime
computes this from the out-of-fold strata and records
`applicability_domain.error_monotonicity` (a Spearman rho and a `verdict`:
`supported` / `not_evidenced` / `inverted` / `insufficient_data`). Report the verdict wherever the
domain flag appears, taking it from that field. When it is `not_evidenced` or `inverted`, say so
plainly — the flag is not demonstrably predictive of error on this dataset and must not be sold as a
validated trust signal.

Present each new prediction with:

- point value or calibrated probability;
- interval or prediction set when supported;
- nearest-neighbour similarity;
- in/out-of-domain flag;
- assay/unit context inherited from the model bundle.

## Claims to avoid

- Do not call inner CV or random-split performance prospective.
- Do not claim model superiority from overlapping, unstable intervals.
- Do not claim conformal coverage under unmeasured domain shift.
- Do not compare metrics across incompatible assays or units.
- Do not treat a censored limit as exact truth.
- Do not give out-of-domain predictions the same evidential weight as supported predictions.
- Do not present the applicability-domain flag as a validated trust signal when
  `error_monotonicity.verdict` is `not_evidenced` or `inverted`; report that it does not track
  error on this dataset.
- Do not infer causal ADME mechanisms from feature importance alone.
- Do not name this skill, its version, or its development/revision/repair history in any
  deliverable; a report describes the analysis (endpoint, split, assessment), and provenance
  lives in `run_manifest.json`.

