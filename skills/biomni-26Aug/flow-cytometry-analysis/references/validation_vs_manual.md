# Validation against a manual-gating export (MANDATORY when one exists)

This is the check that, in the reference ADCC session, was the *only* thing that revealed a large
gating error the pipeline's own diagnostics had missed. **Whenever a manual-gating export exists
(a FlowJo "Export Statistics" file, or any per-sample population count / percentage table), reconcile
the automated pipeline against it BEFORE trusting any downstream fit, abundance comparison, or report.**
This is not optional polish — it is a gate. If the reconciliation flags large discrepancies, stop and
fix gating first.

## Why (the failure this prevents)

The automated pipeline reported ~40–50% dead target cells in no-antibody control wells; the scientist's
manual FlowJo gating showed ~10%. Every internal diagnostic (bimodal BV421, monotonic dose-response,
r≈0.81 correlations) looked *self-consistent*, so the pipeline "validated" itself and was wrong. The
manual export was external ground truth. The discrepancy localized immediately to the scatter/singlet
gate (kept 16,183 "cells" vs the manual 22,654 singlets; recovered 895 target cells vs 7,175 manual),
not to the viability threshold. **Self-consistency is not validation. An independent reference is.**

## What to reconcile

`scripts/08_validate_vs_manual.R` compares, per sample:

1. **Total post-QC cell count** (pipeline, from `sce_prepped.rds`) vs the manual top-level
   **singlets / "Cells"** count. *This is the primary over-gating detector* and needs only step-01
   output — run it as early as possible. A large deficit (pipeline ≪ manual) means the scatter/singlet
   gate is too tight (see `qc_gating.md` → "inspect the SCATTER gate FIRST").
2. **Per-population counts and percentages** (if an annotated abundance table from `04_quantify_dr.R`
   is available) vs the manual per-population counts / `%parent` (or `%total`). Matched by sample id and
   by population name (normalized, case-insensitive; supply a mapping for non-obvious names).

## Inputs

| Input | Notes |
|---|---|
| `--manual <file>` | FlowJo Export Statistics or any tidy/wide table. `.csv`/`.tsv` always supported; `.xlsx` if `readxl` is installed. |
| `--sce <sce_prepped.rds>` | For the total post-QC cell-count reconciliation. |
| `--abundance <abundance.csv>` | Optional. Per-sample per-population counts/percent from `04`. Enables population-level reconciliation. |
| `--sample-col`, `--count-col`, `--pct-col` | Optional overrides if auto-detection of the manual export's columns is wrong. |
| `--tol-pp` (default 5) | Flag any `%` discrepancy larger than this many percentage points. |
| `--tol-rel` (default 0.20) | Flag any count discrepancy larger than this relative fraction. |
| `--map <auto\|name=Manual;...>` | Optional population-name mapping (pipeline→manual). |

## FlowJo "Export Statistics" shape (what the parser expects)

FlowJo exports are typically **wide**: one row per sample (file), and columns whose headers encode the
gate hierarchy with `/` separators plus a statistic, e.g.:

```
Name, Cells/Single Cells | Count, Cells/Single Cells/Target cells | Count, .../Target cells/DEAD+ | Freq. of Parent (%)
```

The parser:
- picks the **sample column** = first non-numeric/text column (or `--sample-col`);
- classifies each remaining column as a **count** (`Count`/`#` in header) or **percent**
  (`Freq`/`%`/`percent` in header), and takes the **leaf** gate name (after the last `/`, before `|`)
  as the population;
- melts to long `(sample, population, stat, value)`.

If your export is already tidy/long, pass `--sample-col`, `--count-col`, `--pct-col` explicitly.

## Verdict and behavior

- Writes `validation_vs_manual.csv` (per-sample, per-population: pipeline value, manual value, delta,
  flagged?) and logs a summary.
- Emits **PASS** only if no total-cell deficit beyond `--tol-rel` and no population flagged beyond
  `--tol-pp` / `--tol-rel`.
- Emits **REVIEW** otherwise, with an explicit instruction: *do not trust downstream fits/abundances/
  report until the flagged gates are resolved; inspect the scatter/singlet gate first.*
- The report step (`07`) should surface the verdict; a REVIEW verdict must not be silently dropped.

## If no manual export exists

Say so explicitly, and fall back to the internal sanity checks in `qc_gating.md` (over-gating alarm,
implausible-baseline trip-wire). Do **not** treat internal self-consistency as if it were external
validation.

## References
- Weber & Robinson 2016 (population-matching conventions, shared with `benchmarking_metrics.md`).
- FlowJo Export Statistics documentation (BD Life Sciences).
