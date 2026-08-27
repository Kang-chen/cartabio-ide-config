# Methodology: Disproportionality Analysis

How `compute_disproportionality.py` turns FAERS counts into signals. All formulas
are implemented in `compute_2x2` and `compute_disproportionality`.

## The 2×2 table

For each (drug, event) pair, against a comparison universe of N reports:

|                | Event present | Event absent |
|----------------|---------------|--------------|
| **Drug present** | a | b |
| **Drug absent**  | c | d |

- `a` = reports mentioning **both** the drug and the event (co-occurrence count)
- `b` = drug_total − a  (reports with the drug but not this event)
- `c` = event_total − a (reports with the event but not this drug)
- `d` = N − a − b − c   (reports with neither)

`drug_total` = reports mentioning the drug (a+b); `event_total` = reports
mentioning the event across the whole universe (a+c); `N` = size of the
comparison universe (whole FAERS with a coded reaction, or the comparator set).

**A row is skipped if any of a, b, c, d < 0** (inconsistent counts, usually from
mismatched universes).

> ⚠️ **event_total must be a whole-universe count.** If you derive it by summing
> `a` across only your drug set, `c` and `d` are wrong unless that set is the
> entire universe. The orchestrator pulls whole-background event totals via a
> faceted background query plus a per-term single-count fallback.

## Statistics

**Reporting Odds Ratio (ROR)** and 95% CI:
```
ROR   = (a/b) / (c/d) = (a·d)/(b·c)
SE     = sqrt(1/a + 1/b + 1/c + 1/d)
95% CI = exp( ln(ROR) ± 1.96 · SE )
```

**Proportional Reporting Ratio (PRR):**
```
PRR = [a/(a+b)] / [c/(c+d)]
```

**Chi-square** with **Yates continuity correction**:
`scipy.stats.chi2_contingency([[a,b],[c,d]], correction=True)` → χ² and p-value.

**Continuity correction for zero cells:** when any cell == 0, add
`continuity_correction` (default 0.5) to **all four cells** before computing
ratios/CI (`_cc`), so ROR and its SE stay finite.

**Multiplicity — Benjamini-Hochberg FDR:** `statsmodels.stats.multitest.multipletests(p, method="fdr_bh")`
applied **per drug** (each drug's family of events is corrected independently),
yielding an `fdr` column (the BH-adjusted q-value).

## Signal criteria (`SignalCriteria`)

Default rule (Evans-style, aligned with common EMA screening practice):

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `ror_ci_lower_min` | 1.0 | ROR 95% CI **lower** bound must exceed this |
| `prr_min` | 2.0 | PRR threshold |
| `chi2_min` | 4.0 | χ² threshold (≈ p < 0.05, 1 df) |
| `min_cases` | 3 | minimum `a` (co-occurrence count) |
| `use_fdr` | True | also require FDR-significance |
| `fdr_q` | 0.05 | BH-FDR q threshold |
| `continuity_correction` | 0.5 | added to all cells when any cell is 0 |

A row is flagged **`signal = True`** when:
```
(ror_lower > ror_ci_lower_min) & (prr >= prr_min) & (chi2 >= chi2_min)
    & (a >= min_cases) & (fdr < fdr_q if use_fdr)
```
Missing values are treated as non-signals (`.fillna(False)`).
`SignalCriteria.describe()` renders the rule as the human-readable string used in
tables and the report.

## Low-confidence flagging (marks, never removes, signals)

A statistically-flagged signal can still be fragile or inflated. `flag_low_confidence`
(in `compute_disproportionality.py`, called at the end of `annotate_signals` once
`signal` and `is_noise` exist) adds four columns and **never changes `signal`**:

| Column | Meaning |
|--------|---------|
| `low_count` | `signal & (a < min_cases_confident)` (default 10) — unstable small count |
| `extreme_ror` | `signal & (ror >= max(extreme_ror_abs_floor, exp(Q3 + k·IQR of ln ROR)))` — implausible high outlier |
| `low_confidence` | logical OR of the two |
| `low_confidence_reason` | e.g. `a<10`, `ROR outlier (>55.1)`, or both |

- The **extreme-ROR fence** is a Tukey "far-out" fence on `ln(ROR)` computed **per drug over that drug's genuine (non-noise) signals** (so administrative/procedure noise does not distort it), and requires **≥ 4** genuine signals — otherwise only the absolute floor (`extreme_ror_abs_floor`, default 25) applies. The final threshold is `max(abs_floor, fence)`, so nothing is flagged when the whole distribution is legitimately high.
- **Why two triggers.** Small counts are the classic fragility problem, but they mainly surface for *rarely reported* drugs: OpenFDA's reaction facet returns only the top ~500 most-frequent terms, so for a high-volume drug the smallest `a` in the table is already large (e.g. semaglutide's smallest genuine-signal `a` is 83), making `low_count` a no-op there. Extreme ROR catches the *other* failure mode — notoriety/stimulated reporting or a mechanism/efficacy-adjacent term producing an implausibly large ROR (e.g. semaglutide "Allodynia", ROR ≈ 70 on 228 cases). Both are unioned into `low_confidence`.
- Low-confidence signals are **retained everywhere** (full CSV, top-signal table, figures) and **marked** (a `Conf.` column, bar hatching, forest diamonds/rings, volcano rings) plus named in the report — they are hypothesis-fragile, not deleted.

## Why these choices

- **ROR vs PRR:** ROR is less biased for rare events and has a clean CI; PRR is
  the classic MHRA measure. Requiring **both** plus χ² and a case floor reduces
  false positives from tiny or noisy cells.
- **CI lower bound > 1** rather than point estimate > 1: demands the elevation be
  statistically distinguishable from the background.
- **FDR** controls the false-discovery rate across the hundreds of events tested
  per drug; without it, ~5% of null events would pass χ² by chance.

## What this does NOT establish

Disproportionality is **hypothesis-generating**. A signal means an event is
reported more often than expected for the drug — it does **not** establish
causation, incidence, or risk. Known confounders: confounding by indication,
notoriety/stimulated reporting (e.g. media, litigation), channelling, and
co-medication. FAERS has **no exposure denominator**, so absolute rates cannot be
computed. Always frame outputs accordingly.

## Validation

The implementation was validated against an independent Phase A JAK1 analysis:
ROR, PRR, χ², and 95% CI matched to displayed precision across sampled rows
(including an under-reported event with ROR < 1 and a small-count event with
a = 76), and the end-to-end signal set matched the reference at **100% agreement**
(283 signals) with the background universe N = 20,328,571.
