# SuSiE single-trait fine-mapping: method reference

What the fine-mapping step in this skill actually does, why each parameter is set the way it is, and
how to read the output. The engine is `susieR::susie_rss` called directly on GWAS summary statistics +
a signed-r LD matrix (this is distinct from `coloc::runsusie`, which wraps SuSiE inside the two-trait
`coloc.susie` colocalization workflow).

## The model in one paragraph

SuSiE ("Sum of Single Effects") represents a locus's genetic signal as a sum of up to **L** independent
single-effect components. Each component contributes exactly one causal variant with some posterior
probability spread across the variants in LD with it. From the fit you get, per component, a **credible
set**: the smallest set of variants that together capture ≥ `coverage` (default 95%) of that component's
posterior — i.e. "the causal variant for this signal is one of these, with 95% probability." You also
get a per-variant **PIP** (posterior inclusion probability), the probability that variant is causal in
*any* component. A clean single-signal locus yields one small credible set with one high-PIP variant.

## The call (validated)

```r
susieR::susie_rss(
  z = beta / se,          # z-scores from the GWAS
  R = signed_LD,          # signed correlation r (NOT r^2), allele-oriented to effect allele
  n = N,                  # GWAS sample size (total; for case/control, total cases+controls)
  L = 10,                 # max number of causal signals to look for
  coverage = 0.95,        # credible-set coverage
  estimate_residual_variance = FALSE,  # recommended for summary-stat RSS mode
  check_prior = TRUE
)
```

### Why these settings

- **`z = beta/se`** — the RSS (Regression with Summary Statistics) likelihood works on z-scores. The
  script forms z from `beta` and `se` and drops any variant with non-finite z (missing/zero se).
- **`R` = signed r** — must be correlation, allele-oriented, not r². See `ancestry-ld-guide.md`.
- **`n` = N** — required for `susie_rss` to calibrate effect sizes. If not passed, the script falls back
  to a median `n` column in the sumstats; otherwise it errors rather than guess.
- **`L = 10`** — an *upper bound* on signals, not a target. SuSiE prunes empty components, so setting
  L above the true number is safe and standard; L too low can merge distinct signals. Raise for known
  multi-signal loci (e.g. L=15–20 at HLA-adjacent regions), but watch runtime and convergence.
- **`estimate_residual_variance = FALSE`** — the recommended default in RSS/summary-stat mode; estimating
  it from summary data can destabilize the fit, especially under any LD mismatch.
- **`check_prior = TRUE`** — warns if an effect's estimated prior variance is implausibly large, an early
  signal of LD/z inconsistency.

## Credible sets and purity

Credible sets are extracted with `susie_get_cs(..., min_abs_corr = 0.5, Xcorr = R)`. The **purity**
filter keeps only sets whose members are mutually correlated at |r| ≥ `min_abs_corr` (default 0.5). A
low-purity set (variants not in LD with each other) usually reflects a spurious component and is
dropped. The reported `within_cs_min_abs_corr` is the minimum pairwise |r| within the set:
- **= 1.0** with **size 1** → a single variant carries the signal (strongest possible result; the PROX1
  anchor: rs340874, size 1, min|r| 1.0, PIP 0.96).
- **high (e.g. > 0.9)** with size > 1 → a tight cluster of statistically indistinguishable variants;
  you have localized the signal but LD prevents resolving to one variant. This is a real, honest result
  — do not over-claim a single causal variant.
- **near `min_abs_corr`** → treat with suspicion; check the LD-mismatch diagnostics.

## Mandatory diagnostics

Run on **every** fit (see `ancestry-ld-guide.md` for interpretation):
- `estimate_s_rss(z, R, n)` → **s** (LD-mismatch scalar). Reported in `susie_report.json` as
  `estimated_s`. The script prints a loud CAUTION when s exceeds `--s-warn` (default 0.1).
- `kriging_rss(z, R, n)` → per-variant expected-vs-observed z; outlier count reported as
  `kriging_outliers`.

## Outputs

`run_susie_finemap.R` writes to `--out-dir`:

| File | Contents |
|---|---|
| `credible_set.csv` | one row per credible-set variant: `cs, cs_size, within_cs_min_abs_corr, snp, varid, pos, effect_allele, other_allele, beta, se, pval, eaf, z, pip` |
| `all_variants_pip.csv` | every analyzed variant with its `z` and `pip`, sorted by PIP |
| `susie_report.json` | `n_snps_analyzed, n_dropped_na_z, N, L, coverage, susie_converged, estimated_s, kriging_outliers, ld_asymmetry_fixed, n_credible_sets, top_pip, warnings` |
| `susie_fit.rds` | the full SuSiE fit object for downstream reuse / custom plots |

## Convergence and edge cases

- **`susie_converged = FALSE`** → the reported sets are unreliable. Usually caused by LD mismatch,
  too-large L, or a degenerate window. Re-check LD ancestry/build first, then reduce L.
- **0 credible sets** → no signal passed the coverage+purity bar. Legitimate for a null/weak window;
  suspicious if you *expected* a genome-wide-significant hit (then look at s and the window definition).
- **Very large windows (> ~10,000 SNPs)** → slow and less stable; the script warns. Fine-map a focused
  window around the lead signal (typically ±100–500 kb) rather than a whole chromosome arm.
- **MHC / chr6:25–34 Mb** → extreme, long-range LD makes fine-mapping unreliable; the script warns and
  you should interpret results there with extra caution or use specialized handling.

## Choosing the window

Fine-map a window that comfortably contains the LD block of the signal but is not so wide it dilutes the
model or drags in secondary signals you don't intend to model. A common choice is ±100–500 kb around the
lead variant, or the recombination-bounded block. The PROX1 anchor used a ~1.2 Mb region
(chr1:213.4–214.6 Mb) for LD construction and fine-mapped the variants within it.

## References

- Wang, Sarkar, Carbonetto, Stephens (2020). JRSS-B. doi:10.1111/rssb.12388 — SuSiE.
- Zou, Carbonetto, Wang, Stephens (2022). PLoS Genetics. doi:10.1371/journal.pgen.1010299 — SuSiE-RSS
  and the LD-mismatch diagnostics.
