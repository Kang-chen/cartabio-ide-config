# Caveats and limitations

This skill produces *simulated* operating characteristics for trial **designs**.
It is a design/planning tool, not a trial-analysis or regulatory-submission tool.
Be explicit about the following when reporting results.

## Modeling assumptions
- **Everything is driven by the config's assumptions.** Control-arm parameters,
  treatment effects, prevalence, accrual, and dropout are inputs, not data. The
  outputs are only as good as those assumptions — always state them and run
  sensitivity analyses around the uncertain ones.
- **Event/response/outcome generators are parametric.** TTE uses exponential (or
  Weibull) event times; binary uses independent Bernoulli; continuous uses normal
  with a common SD. Non-proportional hazards, time-varying effects, over-dispersion,
  informative dropout, and correlated/clustered outcomes are **not** modeled. If
  any of these are expected (e.g. delayed treatment effect in immuno-oncology),
  the log-rank/z power here can be optimistic or pessimistic.
- **One interim analysis** by default (info_frac + final). The engine supports
  more looks in principle, but the examples and the enrichment/SSR rules are
  written and validated for a single interim. Multi-look enrichment/SSR would
  need re-validation.
- **Enrichment and SSR fire at most once**, at the interim, and use simple
  threshold rules. They are illustrative of the mechanism, not tuned optimal
  decision rules.

## Statistical scope
- **Asymptotic tests.** The log-rank and score statistics are large-sample
  approximations. At small information (few events / small N) the normal
  approximation and thus both the simulated and analytic power degrade; expect
  slightly larger sim-vs-rpact gaps for small designs (this is why the TTE Gate 2
  residual is ~0.01-0.015, not ~0.005).
- **Type-I error is verified by simulation, not proven.** Gate 1 checks FWER
  under the *evaluated* null configurations to Monte-Carlo tolerance. It is not a
  mathematical proof of strong control for arbitrary parameter values. Choose
  least-favorable nulls deliberately (e.g. prevalence extremes for enrichment).
- **Non-binding futility.** Futility does not lower the efficacy boundary, so the
  reported FWER is conservative. If a *binding* futility rule is intended, the
  boundaries would need recalculation and the type-I claim re-checked.
- **Single primary endpoint, two arms.** No co-primary endpoints, multi-arm /
  platform structure, or multiplicity across endpoints. The only multiplicity
  handled is the {full population, biomarker+ subgroup} closed test.

## Numerical / reproducibility
- **Monte-Carlo error is real.** Every proportion carries SE `sqrt(p(1-p)/nsim)`.
  The `quick` preset (nsim = 1000) is for iteration only; use `thorough`
  (nsim = 10000) for any number that will be quoted. Differences under ~2 SE are
  not resolved.
- **Reproducible per seed**, but results depend on the RNG stream. The gates and
  grid use fixed seeds; changing `ncores` does not change results because failed
  parallel replicates are re-run serially on their own seed.
- **rpact is the benchmark of record** for Gate 2. If rpact's sizing conventions
  change, re-check the event-driven TTE path (large pool, no admin cap) that makes
  the simulation and analytic design refer to the *same* trial.

## Interpretation guidance
- **"Power" here is design power under assumed effects**, marginal over the
  adaptive decisions. It is not a probability that a specific future trial will
  succeed.
- **Expected N and duration** reflect early stopping and resizing; they are means
  over replicates, not guarantees. Report the distribution (or at least a range)
  for planning.
- The **enrichment "does not lengthen the trial" style insight** (from the NSCLC
  example) is specific to event-driven designs where restricting to the subgroup
  changes the event-accrual rate; it does not generalize to fixed-N binary/continuous
  designs, where enrichment changes *whom* you enroll, not calendar time.

## Not in scope
- Bayesian designs, response-adaptive randomization, dose-finding, basket/umbrella
  designs, and time-to-event cure models are out of scope.
- No cost, logistics, or site-level operational modeling beyond accrual/dropout.
- This skill does not perform the *analysis* of a completed trial and must not be
  used to compute a final p-value for real trial data.
