# Design methods: statistical core

This skill simulates two-arm confirmatory trials and evaluates their operating
characteristics by Monte-Carlo simulation of the **exact decision rules**. The
statistical machinery is identical across endpoint families; only the data
generator and the score/information map change.

## 1. Endpoint abstraction (score / information representation)

Every endpoint is reduced to an efficient **score `U` and information `V`**,
oriented so that **positive `U` means the experimental arm is better**. This is
the single abstraction that makes group-sequential + combination testing valid
for all three families with one engine (`endpoints.R`).

| Endpoint | Statistic | `U` (score) | `V` (information) |
|---|---|---|---|
| Time-to-event | Log-rank | `O_c - E_c` (control observed minus expected events) | `survdiff` variance `var[1,1]` |
| Binary | Score / hypergeometric | `a - n1 * m1 / N` (2×2 table) | `n1 n0 m1 m0 / (N^2 (N-1))` |
| Continuous | Two-sample mean | `diff * V` with `diff = mean_trt - mean_ctrl` | `(n1 n0) / (N * s^2)` |

The single-look z is `U / sqrt(V)`; this reduces **exactly** to the classical
log-rank z (TTE) and the two-sample z (continuous), and to the score
chi-square for binary (the small N-1 hypergeometric correction is why the binary
score z differs from Wald by O(1/N)).

### Stagewise independent increments
At analysis `k` with cumulative `(U_k, V_k)`, the *stage* increment is
`dU = U_k - U_{k-1}`, `dV = V_k - V_{k-1}`, and the stagewise statistic is
`z_k = dU / sqrt(dV)`. Under the independent-increments structure of these
score processes, the `z_k` are asymptotically independent — the property that
makes the inverse-normal combination valid.

## 2. Combination across stages

Executed stages are combined with the **inverse-normal combination function**
using **pre-fixed weights** (fixed at design time, not data-dependent):

```
incr = diff(c(0, info_rates))          # planned information increments
w    = sqrt(incr / sum(incr))          # fixed weights
Z_comb(k) = cumsum(w * z_stages) / sqrt(cumsum(w^2))
```

Because the weights are fixed in advance, `Z_comb` controls the type-I error
even when the *timing* or *population* is modified mid-trial (the basis of
Wassmer/Bauer adaptive theory). This is what lets enrichment and SSR preserve
alpha.

## 3. Boundaries (alpha spending)

Efficacy boundaries come from **rpact's `getDesignInverseNormal`** with an
alpha-spending function (default O'Brien-Fleming-like, `asOF`). For a
single-hypothesis reduced design the final critical value is `qnorm(1 - alpha)`;
for K looks the boundary vector is the spending-function critical values. If
efficacy stopping is disabled, interim efficacy boundaries are set to `Inf`
(only the final look can reject).

## 4. Closed testing over {H_F, H_S}

When both the **full population** `H_F` and the **biomarker-positive subgroup**
`H_S` are tested, multiplicity is controlled by a **closed test**. The
intersection hypothesis `H_F ∩ H_S` is tested with a **Simes** combination of
the two stagewise p-values, itself carried through the inverse-normal
combination across stages. An elementary hypothesis is rejected only if **both**
the intersection and its own combination statistic cross the boundary.

- With `prevalence = 1` the subgroup equals the full population, so `z_S == z_F`,
  the Simes intersection returns the same p-value (no penalty: for equal p's,
  `min(2p/1, 2p/2) = p`), and the closed test reduces to a single-hypothesis
  test — which is exactly why the single-hypothesis Gate 2 benchmark is valid.

## 5. Adaptive population enrichment

At the (single) interim, if the subgroup signal exceeds the full-population
signal by a pre-set margin (`z_S,cum - z_F,cum > enrich_delta`), the trial
**enriches**: subsequent enrollment/target is restricted to the biomarker-positive
subgroup, and only `H_S` remains testable at the end (`reject_F` is forced
`FALSE`). Enrichment fires **at most once**. Because the decision uses only the
combination test with fixed weights, alpha is preserved (verified by Gate 1
under least-favorable nulls).

## 6. Futility stopping (conditional power)

At each interim, conditional power `CP` is computed under the current trend:

```
t_frac = info_rates[k]
z_t    = z_cum * sqrt(1 / t_frac)                       # projected to full info
CP     = pnorm((z_t - b_final) / sqrt(1/t_frac - 1))
```

If `CP < futility_cp` the trial stops for futility (non-binding by default — the
boundary is not lowered to "spend" the futility, so type-I error is conservative).

## 7. Sample-size re-estimation (SSR)

If enabled, at the interim the **final-stage target** is rescaled by a
conditional-power factor, capped:

```
z_alpha = b_final
z_cp    = qnorm(ssr_cp_target)
z_t     = z_cum * sqrt(1 / info_rates[k])
need    = ((z_alpha + z_cp) / max(z_t, 1e-3))^2
factor  = min(max(need, 1), ssr_nmax_cap)
```

SSR only fires when the interim conditional power sits in a "promising"
window `[ssr_cp_min, ssr_cp_max]` (Mehta-Pocock style) — not when the trial is
already very likely to win or is hopeless. Crucially, **the final test still
uses the pre-fixed combination weights**, so the increase in sample size does
NOT inflate alpha (verified: continuous+SSR FWER ≈ 0.027 < tolerance). Using the
naive unweighted statistic after SSR would inflate type-I error; the
combination weighting is what protects it.

## 8. What is simulated vs assumed

- **Simulated:** patient-level endpoint data, accrual, dropout, the interim and
  final analyses, and all adaptive decisions, replicated `nsim` times.
- **Assumed (from config only):** control-arm parameters, treatment effects per
  subgroup, prevalence, accrual/follow-up, dropout. No real patient data are
  used anywhere.

## References
- Simon N, Simon R. Adaptive enrichment designs for clinical trials. *Biostatistics* 2013;14:613-625.
- Wang SJ, Hung HMJ, O'Neill RT. Adaptive patient enrichment designs. *Biometrical J* 2009;51:358-374.
- Jenkins M, Stone A, Jennison C. An adaptive seamless phase II/III design. *Pharm Stat* 2011;10:347-356.
- Mehta CR, Pocock SJ. Adaptive increase in sample size when interim results are promising. *Stat Med* 2011;30:3267-3284.
- Cui L, Hung HMJ, Wang SJ. Modification of sample size in group sequential trials. *Biometrics* 1999;55:853-857.
- Wassmer G, Pahlke F. *rpact: Confirmatory Adaptive Clinical Trial Design and Analysis.*
