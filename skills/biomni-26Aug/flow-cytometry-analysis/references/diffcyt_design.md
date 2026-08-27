# Differential abundance & state (diffcyt) — with a hard rigor gate

Runs **only** when sample metadata contains a grouping variable with ≥2 groups.

## THE RIGOR GATE (the entire payoff of doing this properly)

> **Emit p-values only if every compared group has ≥ 3 samples.**

With 2-vs-2 (or fewer) the between-sample variance cannot be reliably estimated; any p-value is
theatre. When the smallest group is below the threshold, the pipeline **refuses to test** and instead
reports a **descriptive** result (per-group means/medians, fold-change, per-sample points) with an
explicit, logged limitation. Do not soften this into a "borderline significant" claim — the whole
point is to *not* emit a p-value on n = 2.

```r
if (min(table(group)) < min_n) {         # min_n default = 3
  # -> descriptive_only mode: means/medians + fold-change + per-sample boxplot
  # -> log the refusal + reason; write no p-values
}
```

The default `min_n = 3` is the floor for the *simplest* two-group comparison; more groups, paired
designs, or small effect sizes need more. It is overridable **upward** but the refusal path is not
optional.

## Design formula: batch + covariates, not group alone

Confounders bias abundance shifts. Put known technical/biological covariates in the model and keep
the tested term last:

```
~ <covariates> + <batch> + group          # e.g. ~ age + sex + batch + condition
```

```r
design <- diffcyt::createDesignMatrix(ei, cols_design = c(covariates, batch, group))
```

Fixed batch effects belong in the design; for repeated measures on the same donor, use a
block/random effect (diffcyt supports `formula`-based methods for this).

## Two analyses

- **Differential abundance (DA)** — are population *proportions* different between groups?
  `diffcyt-DA-edgeR` fits a negative-binomial GLM to cluster counts.
- **Differential state (DS)** — within a population, are *state/functional* marker medians different?
  `diffcyt-DS-limma` uses empirical-Bayes moderated linear models on per-cluster median expression.

```r
da <- diffcyt::diffcyt(sce, clustering_to_use = chosen_k, analysis_type = "DA",
                       method_DA = "diffcyt-DA-edgeR", design = design, contrast = contrast)
ds <- diffcyt::diffcyt(sce, clustering_to_use = chosen_k, analysis_type = "DS",
                       method_DS = "diffcyt-DS-limma", design = design, contrast = contrast)
```

Report at FDR (Benjamini–Hochberg); default threshold 0.05. Always show the per-sample abundance
plot alongside any test so readers see the actual spread, not just a p-value.

## Contrast note (honest limitation)
The default contrast tests the last design coefficient (the group term's non-reference level). For
>2 groups or a specific non-adjacent comparison, construct the contrast vector explicitly with
`diffcyt::createContrast()` — the skill logs this rather than silently testing the wrong thing.

## References
- Weber, Nowicka, Soneson, Robinson. diffcyt. Communications Biology 2019. doi:10.1038/s42003-019-0415-5.
- Robinson et al., edgeR; Ritchie et al., limma.
