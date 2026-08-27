# Meta-Analysis Methodology Notes

Reference for the `evidence-synthesis-meta-analysis` skill. Read this before running so the
statistical choices are deliberate, not accidental. All analysis is in R with `meta` + `metafor`.

---

## 1. The unifying engine: generic inverse-variance (GIV)

Every pooled estimate is a **precision-weighted average** of study effects: weight_i = 1 / (SE_i² + tau²).
Because virtually every trial/study reports an effect with a 95% CI, GIV lets us pool *any* measure
(MD, SMD, OR, RR, HR) with one consistent method — as long as effects are on the correct analysis scale.

**Analysis scale by measure:**

| Measure | Natural scale (as reported) | Analysis scale (what gets pooled) | Back-transform for display |
|---|---|---|---|
| MD  (mean difference)               | difference in units    | same (difference)     | none |
| SMD (standardized mean difference)  | Cohen's d / Hedges' g  | same                  | none |
| OR  (odds ratio)                    | ratio                  | **log(OR)**           | exp() |
| RR  (risk ratio)                    | ratio                  | **log(RR)**           | exp() |
| HR  (hazard ratio)                  | ratio                  | **log(HR)**           | exp() |

**Do NOT mix measures in one pool.** One meta-analysis = one measure. Use separate analyses (or
subgroups) if a question spans, e.g., both continuous and binary outcomes.

## 2. Standard error derivation

From a symmetric 95% CI on the **analysis scale**:

```
SE = (CI_hi - CI_lo) / (2 * 1.959964)     # 1.959964 = qnorm(0.975); (hi - lo)/3.92
```

- For **ratios** (OR/RR/HR): take logs first, then apply the formula:
  `SE = (log(CI_hi) - log(CI_lo)) / 3.92`.
- For a difference built from **two per-arm CIs** (e.g. paper reports each arm's mean change + CI
  but not the between-arm difference CI): derive each arm's SE, then
  `SE_diff = sqrt(SE_trt^2 + SE_ctrl^2)` and `effect_diff = mean_trt - mean_ctrl`.
- If a paper reports SD instead of CI: `SE_arm = SD / sqrt(n_arm)`.
- Never invent an SE. If neither CI, SE, nor SD is recoverable, the study cannot enter GIV pooling —
  record it as excluded with reason "no dispersion reported".

## 3. Model defaults (opinionated)

```r
metagen(TE = te, seTE = se, sm = <measure>,
        method.tau = "REML",          # between-study variance estimator
        method.random.ci = "HK",      # Hartung-Knapp-Sidik-Jonkman CI (t-based, conservative)
        prediction = TRUE,            # 95% prediction interval
        subgroup = subgroup)          # if a moderator is provided
```

- **Random-effects, REML, HKSJ** is the default. Rationale: HKSJ gives better coverage than the
  standard normal-based random-effects CI, especially with few studies and high heterogeneity.
- Report the **common/fixed-effect** estimate only as a secondary reference; under heterogeneity it
  understates uncertainty and is not the headline.

### Always report
- Pooled effect + HKSJ 95% CI (back-transformed for ratios).
- **Heterogeneity**: tau^2, tau, I^2 (with 95% CI), H, Cochran Q (df, p).
- **95% prediction interval** — the range a *future* study's true effect would plausibly fall in.
  This is often more decision-relevant than the CI when heterogeneity is high.

### Escape hatches (document the choice if you deviate)
- `method.tau = "DL"` (DerSimonian-Laird): older default; use only for exact replication of a prior review.
- `method.random.ci = "classic"`: turns off HKSJ; acceptable when k is large and heterogeneity low.
- `common = TRUE, random = FALSE`: fixed/common-effect only — appropriate ONLY when studies are
  functionally identical (rare; I^2 near 0 and clinical homogeneity).

## 4. Subgroups & meta-regression
- Categorical moderator -> subgroup analysis with a **between-subgroup Q test** (`Q.b`, p). A significant
  test means the moderator explains part of the heterogeneity.
- Continuous moderator -> `metareg()` meta-regression.
- Pre-specify moderators; post-hoc subgroups are exploratory and must be labeled as such.

## 5. Robustness suite (run every time)
1. **Leave-one-out** (`metainf(m, pooled = "random")`): confirms no single study drives the result.
2. **Influence diagnostics** (`metafor::influence(rma(...))`): studentized residuals + Cook's distance;
   flag but do NOT delete outliers — investigate whether they are data errors or true effect variation.
3. **Small-study effects / publication bias**: funnel plot + Egger's regression test.
   **Interpret as a bias verdict ONLY when k >= 10** (Cochrane guidance). With fewer studies the test is
   underpowered and asymmetry is easily confounded by genuine between-study heterogeneity. Report the
   plot/number for completeness with this caveat.

## 6. Risk of bias
Use a **structured narrative** summary (Cochrane-domain-informed): per study, rate domains
(randomization / sequence generation, deviations from intended intervention, missing outcome data,
outcome measurement, selective reporting) as Low / Some concerns / High, plus an overall judgment.
This is a narrative aid, NOT an automated RoB2 instrument — say so.

## 7. Anti-hallucination rules (non-negotiable)
- Every extracted number and every citation (title, authors, year, journal, DOI, NCT/PMID) must be
  verified against the original source before it enters the report. If a verbatim transcript artifact
  exists (`/mnt/results/execution_trace/transcript.jsonl`), regex-verify each key value:
  `rg -i "<keyword>" /mnt/results/execution_trace/transcript.jsonl`. Otherwise verify against the fetched paper.
- Never impute, simulate, or "reconstruct from memory" an effect size or reference.
- A shared control arm (multi-arm trial) must not be double-counted: enter one comparison per study in
  the primary pool (typically the highest/target dose), and note the choice.

## 8. Common failure modes to guard against
- Pooling ratios on the natural (not log) scale -> wrong weights and CI. Always log OR/RR/HR first.
- Mixing estimands (e.g. treatment-policy vs. per-protocol; change-from-baseline vs.
  change-from-reduced-baseline after a lead-in) -> pool like-with-like; put different estimands in
  separate analyses or exclude with a documented reason.
- Mixing RCT and observational effects without separation -> subgroup or analyze separately.
- Genome/units drift for lab outcomes -> confirm all studies report the same unit before pooling MD.
