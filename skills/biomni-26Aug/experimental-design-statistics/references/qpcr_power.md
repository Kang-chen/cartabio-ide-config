# qPCR / ΔCt Power Analysis

Power analysis for qPCR / RT-qPCR / ddPCR designs analysed on the ΔCt (delta-Ct) scale, where the effect is a difference in cycle threshold and the test is a t-test (2 groups) or one-way ANOVA (3+ groups).

## When to Use

Use `scripts/power_qpcr.R` for continuous-readout qPCR designs. The count-data functions (`calc_power_rnaseq`, `calc_sample_size_rnaseq`, `calc_samplesize_de`, `calc_power_atac`) based on RNASeqPower/DESeq2/ssizeRNA do **not** apply to qPCR.

## Functions

```r
source("scripts/power_qpcr.R")

# Smallest number of BIOLOGICAL replicates for target power
ss <- samplesize_qpcr_ddct(
  delta_ct = 1.0,        # ΔΔCt effect in Ct units (1 Ct ≈ 2-fold)
  sd_biological = 0.8,   # BIOLOGICAL ΔCt SD (prior ~0.5-1.0 Ct), NOT technical (~0.1-0.3)
  power = 0.9, test = "t",
  n_contrasts = 4, correction = "holm"   # multiple-testing correction of alpha
)

# Power at a proposed n, and sensitivity to the (uncertain) biological SD
pw   <- calc_power_qpcr_ddct(delta_ct = 1.0, sd_biological = 0.8, n_biological = 5,
                             test = "t", n_contrasts = 4, correction = "holm")
sens <- sensitivity_qpcr_over_sd(delta_ct = 1.0, n_biological = ss$required_n_biological,
                                 sd_range = c(0.4, 0.6, 0.8, 1.0),
                                 test = "t", n_contrasts = 4, correction = "holm")
```

## Effect Size

- Cohen's d = `delta_ct / sd_biological` (t-test); ANOVA uses Cohen's f.
- Effect size, `effective_alpha` (after correction), `power`, and the `assumptions` are returned.

## Biological vs. Technical Replication (Enforced)

The unit is **BIOLOGICAL replicates** and `sd_biological` is the **biological ΔCt SD** — this is enforced in code:

- `calc_power_qpcr_ddct()` / `samplesize_qpcr_ddct()` call `assert_biological_replication()`, which **stops with an error** if `sd_type = "technical"` or `n_unit = "technical"`. qPCR/ΔCt power MUST use the biological ΔCt SD (~0.5–1.0 Ct) and count biological replicates; technical replicates (~0.1–0.3 Ct well-to-well) are pseudoreplication and powering off them produces a badly underpowered design.
- The count-data functions (`calc_power_rnaseq`, `calc_sample_size_rnaseq`, `calc_samplesize_de`, `calc_power_atac`) take `n_unit = "biological"` (default) and **warn** if `n_unit = "technical"` is chosen. The statistics are unchanged; the warning flags that the CV/dispersion is biological and the result should be read as biological replicates.
- To check a unit/SD directly: `assert_biological_replication(n_unit, sd_type, context = "...")`.

## Common Issue

| Issue | Cause | Solution |
|-------|-------|----------|
| qPCR design powered off a tiny SD looks great but fails | Technical SD used where biological is required | qPCR/ΔCt power needs the biological ΔCt SD (~0.5–1.0 Ct), not technical (~0.1–0.3 Ct). `calc_power_qpcr_ddct()` errors via `assert_biological_replication()` if `sd_type='technical'`. Supply the SD of per-sample ΔCt across biological replicates. |
| qPCR power calculation: "Package 'pwr' is required" | pwr not installed | `install.packages('pwr')`. The qPCR module uses `pwr.t.test` / `pwr.anova.test`. |
