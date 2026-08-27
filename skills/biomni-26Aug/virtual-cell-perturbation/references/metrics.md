# Metrics: definitions, conventions, and the direction-metric trap

All metrics are computed with the **original GEARS implementation**
(`from gears.inference import compute_metrics, deeper_analysis`) so numbers follow
the scGPT / GEARS paper conventions and are directly comparable to published results.
Do not reimplement them.

## How the results dict is built (`aggregate_metrics.py`)

For predicted array `pred` and measured array `truth` (both `(N, n_genes)`), and the
per-cell DE indices `de_idx` `(N, K)`:

```python
pred_de  = np.take_along_axis(pred,  de_idx, axis=1)   # (N, K)
truth_de = np.take_along_axis(truth, de_idx, axis=1)
res = {"pert_cat": pert_cat,
       "pred": pred.astype(float), "truth": truth.astype(float),
       "pred_de": pred_de.astype(float), "truth_de": truth_de.astype(float)}
m, mp = compute_metrics(res)      # overall + per-pert
d      = deeper_analysis(adata, res)   # per-pert deltas, direction, top-k DE
```

`deeper_analysis` needs the `adata` (for control means and precomputed DE gene sets in
`adata.uns`), so it must run on a machine where the GEARS `PertData` has been loaded.

## Metric families

**Absolute expression** (easy — dominated by unchanged genes)
- `pearson` — Pearson r between predicted and measured mean expression, all genes.
- `pearson_de` — same, restricted to the top-20 DE genes.
- `mse`, `mse_de` — mean squared error, all genes / DE genes (lower is better).

**Change-from-control (Δ)** (the meaningful test)
- `pearson_delta` — r between predicted Δ (perturbed − control) and measured Δ, all genes.
- `pearson_delta_de` — same on the top-20 DE genes.
Subtracting the control mean removes the large, trivially-predictable baseline
expression, so Δ-correlations reflect how well a model predicts the *effect* of a
perturbation, not just typical expression levels.

**Direction match** (`frac_correct_direction_k`)
- Fraction of the top-k DE genes for which `sign(pred_delta) == sign(true_delta)`.
- Computed for k ∈ {20, 50, 100, 200, all}.

## The direction-metric trap (READ THIS)

A control-mean baseline predicts **zero** change for every gene. As a result:

- `pearson_delta`, `pearson_delta_de` → **exactly 0** (a constant-zero vector has no
  correlation with anything).
- `frac_correct_direction_{20,50,100,200}` → **exactly 0** by construction on the DE
  genes (a zero delta has no sign, scored as incorrect).
- **BUT** `frac_correct_direction_all` → **≈ 0.25–0.30, NONZERO.** Over *all* genes,
  most genes barely change; the sign of a near-zero predicted delta coincidentally
  matches the sign of a near-zero measured delta often enough to give ~0.26. This is a
  sign-agreement artefact among unchanged genes, **not** evidence of predictive skill.

**Consequence:** always compare models on **top-k DE direction** (`frac_correct_direction_20`),
never on `frac_correct_direction_all`. Reporting the all-gene number as if the baseline
"gets direction right 26% of the time" is misleading — flag it explicitly, as the report
template does.

This nuance was the single most important correctness fix in the reference build; the
report and figures both foreground the DE direction metric and annotate the all-gene
exception.

## Aggregation

- Per-perturbation values come from `mp` (compute_metrics) and `d` (deeper_analysis).
- Overall = mean AND median over per-perturbation values, skipping NaN
  (NaN occurs when a perturbation has too few cells for a stable correlation).
- **Report both mean and median.** The per-perturbation distribution is usually
  left-skewed (a few hard perturbations drag the mean below the median), so the median
  is the more robust central estimate.
- `by_regime` stratifies the same per-pert values by GEARS test subgroup
  (`combo_seen0/1/2`, `unseen_single`, ...). Low-n regimes (e.g. combo_seen0 with n=2 on
  Norman) are noisy — report them but label as illustrative.

## Sanity checks before trusting a run
- Baseline `pearson_delta` and `frac_correct_direction_20` must be ~0. If they are not,
  `de_idx`/`ctrl_mean` alignment is wrong.
- Model `pearson` (all genes) is typically very high (>0.98) for any reasonable model —
  do **not** celebrate this; it mostly reflects unchanged genes.
- Model `pearson_delta_de` should clearly exceed 0 and exceed the baseline.
