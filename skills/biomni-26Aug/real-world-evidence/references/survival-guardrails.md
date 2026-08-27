# Survival & Regression Guardrails

The survival module (`04_survival.R`) is deliberately conservative. This note
explains each guardrail, why it exists, and how to interpret the outputs
correctly. Read with `config-reference.md` for the exact config knobs.

---

## 1. Events per variable (EPV): the gate on multivariable Cox

**Rule as implemented:** `EPV = n_events / n_candidate_covariates`. A multivariable
Cox model runs **only** if `length(cox_covariates) > 0` AND `EPV >= CFG$epv_min`
(default 10). Otherwise the model is **suppressed** and the report shows only
descriptive statistics, Kaplan-Meier curves, landmark survival, and univariable
log-rank tests.

**Why 10?** The classic Peduzzi/Concato simulations showed that when the number
of events per predictor falls much below ~10, Cox regression coefficients become
**biased, imprecise, and unreliable**, with confidence-interval coverage and
type-I error degrading as predictors are added relative to events [11, 13]. Ten
EPV emerged as the prudent minimum and is the most widely cited threshold [11].

**Why not always 10?** EPV is a rule of thumb, not a law. More recent work shows
the adequate ratio is **data-dependent**: with many low-prevalence predictors,
EPV of **20 or more** may be needed to remove bias, while in some simple settings
lower ratios suffice [12]. The threshold is exposed as `CFG$epv_min` precisely so
you can raise it (e.g. to 20) for models with several rare or binary covariates.

**What to do when EPV is too low.** Options, in rough order of preference:
1. Reduce the number of covariates to the few most important, pre-specified ones.
2. Collect more events (longer follow-up, larger / full data source).
3. Use penalized Cox (ridge / lasso / elastic net), which stabilizes estimates in
   low-EPV settings [14]. This is beyond the default pipeline but is the right
   direction if you must adjust with few events.
Do **not** simply run the full model and report the coefficients — that is exactly
the failure mode the gate prevents.

## 2. Proportional-hazards assumption

When a Cox model does run, the pipeline stores a `cox.zph` test as an attribute of
the result. A significant global or per-covariate `cox.zph` p-value indicates the
**proportional-hazards assumption is violated** — the hazard ratio is not constant
over time, so a single HR is misleading [10]. Remedies include stratification,
time-varying covariates, or restricting to a time window. Always check it;
never report a Cox HR without at least noting the PH check.

## 3. Kaplan-Meier, landmark survival, and unreliable medians

KM is the workhorse and always runs. Two practical points:

- **Report landmark survival, not just the median.** In small cohorts the KM
  curve often does not drop below 50%, so the **median survival is undefined**
  (the upper confidence limit comes back as `NA`). Rather than report a fragile or
  missing median, the pipeline reports survival probabilities at fixed landmark
  times (`CFG$landmark_times`, default 7/14/30/60/90 days) with 95% CIs. These are
  stable and directly interpretable.
- **CIs widen as the risk set shrinks.** Late-time landmark estimates rest on few
  at-risk patients; the `N at risk` column is printed alongside every landmark so
  readers can judge reliability.

## 4. Log-rank vs. a single-timepoint proportion — NOT a contradiction

A common source of confusion (and a real QA finding on the worked example): Table 1
may report **in-hospital death p = 0.03 (Fisher's exact)** while the **log-rank
test is p = 0.54**. These are not inconsistent — they answer different questions:

| Test | Estimand | Uses | Sensitive to |
|------|----------|------|--------------|
| Fisher's exact on "in-hospital death" | Difference in the **proportion** dead by one endpoint (in-hospital) | Counts at a single point | The raw event proportion, ignoring timing and censoring |
| Log-rank | Difference in the **entire survival curve** over follow-up | Times-to-event + censoring | The shape/separation of curves across all follow-up |

A group can have a higher in-hospital death **proportion** yet a survival **curve**
that is not statistically separated over the full follow-up (especially with small
n and wide CIs), or vice versa. Report both, and interpret each for what it is:
the proportion is a snapshot; the log-rank is a trajectory. Neither "overrides"
the other.

## 5. Two mortality denominators

Mortality can be computed at the **patient level** (one index encounter per
patient) or the **admission level** (all encounters). They legitimately differ —
e.g. in the worked example, patient-level in-hospital mortality (29.4%) differs
from the admission-level figure because some patients have multiple admissions.
The pipeline uses the **patient level** for Table 1 and survival (unambiguous
denominator); if you also quote an admission-level number, label it explicitly.

## 6. In-hospital vs. all-cause death, and censoring

Death is observed if any of: a date-of-death field, an in-unit death time, or an
expiry flag is present. Survival time is censored at the patient's **last observed
discharge**. Consequences to communicate:

- **In-hospital and all-cause survival curves can diverge** when deaths occur
  *after* discharge (captured by a linked date-of-death but not the in-hospital
  flag). This is expected, not a bug.
- Administrative censoring at last discharge means very long "survival" times can
  reflect end of follow-up, not confirmed survival. The report states the
  censoring rule so readers interpret tails correctly.

## 7. All p-values are exploratory

Every p-value in the pipeline (Wilcoxon, Fisher, log-rank, and any Cox output) is
labeled **EXPLORATORY**. No multiple-testing correction is applied by default
(`CFG$multiple_testing = "none"`). With many comparisons, expect some small
p-values by chance; treat them as signals to investigate, not as confirmatory
evidence [3]. If you need confirmatory inference, pre-specify a small number of
hypotheses and correct accordingly.

---

### References

3. Zoccali C, Tripepi G. Real-world evidence and observational studies:
   methodological challenges in clinical research. *Eur J Clin Invest.* 2025.
   doi:10.1111/eci.70153
10. Gomes A, Costa B, Nunes V, et al. Cox regression in survival analysis:
    practical insights for clinicians. *Acta Med Port.* 2026. doi:10.20344/amp.23078
11. Peduzzi P, Concato J, Feinstein AR, Holford TR. Importance of events per
    independent variable in proportional hazards regression analysis. II.
    Accuracy and precision of regression estimates. *J Clin Epidemiol.* 1995.
    doi:10.1016/0895-4356(95)00048-8
12. Ogundimu EO, Altman DG, Collins GS. Adequate sample size for developing
    prediction models is not simply related to events per variable. *J Clin
    Epidemiol.* 2016. doi:10.1016/j.jclinepi.2016.02.031
13. Concato J, Peduzzi P, Holford TR, Feinstein AR. Importance of events per
    independent variable in proportional hazards analysis. I. Background, goals,
    and general strategy. *J Clin Epidemiol.* 1995. doi:10.1016/0895-4356(95)00510-2
14. Ojeda F, Müller C, Börnigen D, et al. Comparison of Cox model methods in a
    low-dimensional setting with few events. *Genomics Proteomics Bioinformatics.*
    2016. doi:10.1016/j.gpb.2016.03.006
