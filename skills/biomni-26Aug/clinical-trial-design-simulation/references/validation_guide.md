# Validation guide: the two enforced gates

A simulated design is only trustworthy if it (a) controls type-I error and
(b) reproduces a known analytic benchmark where one exists. This skill enforces
**both** before any report is produced. Both live in `validate_design.R` and are
run automatically by `run_pipeline.R`. With `enforce = TRUE` (default), a failing
gate raises an error and **no operating characteristics or PDF are produced**.

## Gate 1 — Family-wise type-I error (FWER)

**What it checks:** under the null, the probability of rejecting *any*
hypothesis (`FWER_any = P(reject H_F or H_S)`) does not exceed the one-sided
alpha, within Monte-Carlo tolerance.

**Configurations tested:** the global null, plus any `fwer_null_variants` you
supply (typically prevalence extremes for enrichment designs, which are the
least-favorable configurations for the closed test).

**Tolerance:** `alpha + 3 * sqrt(alpha (1-alpha) / nsim)` (a 3-SE upper band).
At alpha = 0.025 this is ≈ 0.031 for nsim = 10000, ≈ 0.036 for nsim = 2000. Use
`nsim >= 10000` for a definitive statement.

**Interpretation:**
- Pass ⇒ the design controls type-I error under the evaluated nulls.
- Because futility is non-binding and boundaries are OBF-like, the observed FWER
  is usually **below** alpha (some alpha is "unspent"). That is expected and fine.
- Enrichment and SSR both add error-inflation *risk*; Gate 1 is where you confirm
  they were implemented correctly. A design with SSR should still land at or
  below tolerance (in the examples, continuous+SSR ≈ 0.027).

**If it FAILS:**
1. Check the null is truly null in *both* subgroups (the gate auto-nullifies the
   effect, but a config with, e.g., a residual `rr_pos` can leak).
2. Confirm the final test uses the **combination** statistic (`combine_inverse_normal`),
   not a naive pooled statistic — SSR/enrichment only preserve alpha under the
   fixed-weight combination.
3. Increase `nsim`: a single 2000-rep run has SE ≈ 0.0035; an apparent 0.033 may
   be noise. Re-run at 10000 before concluding.

## Gate 2 — Power vs the rpact analytic benchmark

**What it checks:** for a **reduced, single-hypothesis** version of the design
(`prevalence = 1`, no enrichment), the simulated power matches rpact's closed-form
group-sequential power across an effect grid, within `power_tol` (default 0.02).

**Why single-hypothesis:** rpact gives exact analytic power for a standard
group-sequential test but not for the full closed/adaptive procedure. Reducing to
one hypothesis isolates the **endpoint score, information accrual, boundaries, and
combination** — the parts that must match theory. If these are right, the adaptive
layer built on top is trustworthy.

**Sizing (important):** the gate sizes each effect with rpact's `getSampleSize*`
and then simulates the **same** design:
- **Binary / continuous:** fixed-N; simulate exactly `N` subjects.
- **TTE: event-driven.** The design is defined by a **target number of events**,
  not calendar time. The simulator uses a large subject pool (`N_max = 2000`) with
  **no administrative truncation** (`max_followup = 1000`) so the event target is
  actually reached and events accrue in the Schoenfeld regime. rpact power is taken
  with `directionUpper = FALSE` (survival: hazard ratio < 1 is benefit) and a
  matching large `maxNumberOfSubjects`.

  > This event-driven sizing is the single most common source of a *spurious* TTE
  > power mismatch. If you instead cap follow-up so the target events cannot be
  > reached, the trial ends at the admin cutoff with fewer events than planned and
  > simulated power falls systematically below rpact — a **sizing artifact, not a
  > calibration bug.** The fix is to reach the events (large pool / longer
  > follow-up), not to loosen the tolerance.

**Expected agreement:** |sim - rpact| ≈ 0.005-0.018 at nsim = 2000-5000. TTE runs
slightly noisier than binary/continuous because the log-rank is a large-sample
approximation; a residual ~0.01-0.015 is normal. If you must, `power_tol` up to
0.03 for TTE is defensible, but first confirm the events are being reached.

**If it FAILS:**
1. **TTE:** verify events are reached — print `E_info_F` from the OC row; it
   should equal the target. If it is short, increase `N_max`/`max_followup`.
2. Check the boundary orientation matches (`directionUpper`).
3. Check `combine_inverse_normal(c(z), c(1))` returns exactly `z` (single stage).
4. Increase `nsim`.

## Running the gates directly

```r
source("validate_design.R")
g1 <- gate_fwer(base_null_scenario, null_variants = list(...), nsim = 10000)
g2 <- gate_power_vs_rpact("tte", median_ctrl = 18.9, hr_grid = c(.60,.65,.70), nsim = 5000)
g1$pass; g1$worst; g1$table      # each gate returns list(table, pass, worst, gate)
```

Both gates return `list(table, pass, worst, gate)`. `run_pipeline.R` writes
`gate_fwer.csv` and `gate_power.csv`, which the report reads directly.

## A note on Monte-Carlo error
Every simulated proportion `p` carries SE `= sqrt(p(1-p)/nsim)`. The OC tables
report `mc_se_any`. When comparing two scenarios or against a threshold, remember
a difference under ~2 SE is not resolved — increase `nsim` (use the `thorough`
preset) rather than over-interpreting noise.
