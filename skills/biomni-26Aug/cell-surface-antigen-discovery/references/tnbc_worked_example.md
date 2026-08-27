# Worked Example — Triple-Negative Breast Cancer (TNBC)

A real end-to-end run of this pipeline on TNBC, used here to illustrate expected outputs,
the funnel, and — importantly — how to report the safety axis honestly. All numbers below
are from an actual run (CZ CELLxGENE Census `2025-11-08`); your run will differ with data
version and label choices.

> This is a worked example for calibration, not a set of values to copy into a new report.
> Every number in a fresh report must be read from that run's CSVs.

## Setup

- **Disease label:** triple-negative breast cancer (whole-cell scRNA-seq via Census).
- **Surfaceome universe:** full in-silico surfaceome (~2,800 genes).
- **Normal-tissue baseline:** HPA dual-signal (RNA consensus nTPM + IHC protein).
- **Validation harness + negative controls:** enabled.

## The funnel

| Stage | Count |
|-------|-------|
| Total cells integrated (4 datasets) | 61,219 |
| Malignant/epithelial cells | 26,343 |
| Immune cells | 29,902 |
| CAF | 3,353 |
| Endothelial | 1,621 |
| Surfaceome universe (combined) | 2,805 |
| After cross-dataset consensus | 2,680 |
| Topology-accessible & scored | 2,674 |
| Annotated (Open Targets + HPA safety computed) | 504 |

The compartment split is where the **compartment-assignment bug** would have silently
corrupted everything: Census labels tumor cells with strings like `"malignant cell"`, which
*contains the substring* `"t cell"`. Naive substring matching routes malignant cells into the
immune compartment, collapsing tumor specificity. This skill uses word-boundary matching with
the epithelial compartment checked first (see `census_pull.py::assign_compartment`); the
regression test asserts `assign_compartment('malignant cell') == 'epithelial'`.

## Results (top of the composite ranking)

No Tier-1 candidates; 2 Tier-2 (ADAM12, NCMAP); the rest Tier-3. This is a *realistic*
outcome for a tumor whose best-known antigens (TROP2, HER2-low) are broadly expressed — the
multiplicative safety factor and the topology gate deliberately prevent inflated scores.

| Rank | Gene | Final | Tier | spec_vs_TME | Safety factor | Safety provenance |
|------|------|-------|------|-------------|---------------|-------------------|
| 1 | ADAM12 | 0.409 | Tier 2 | 14.5 | 1.0 | **computed** (HPA) |
| 2 | NCMAP | 0.355 | Tier 2 | 27.7 | 0.7 | **computed** (HPA) |
| 3 | FREM2 | 0.316 | Tier 3 | 54.7 | 0.7 | computed |

> Note that a 0.7 safety factor can be either a *computed* "moderate" HPA score **or** the
> *neutral default for an un-annotated gene* — these are not the same claim. Always check
> `safety_unassessed` before describing a candidate as "safe."

## Validation harness (QC)

- **recall@10 = 0/10**, **recall@20 = 0/10** core validated targets in the top 20.
- Best-ranked core validated target landed at rank **154**; best of the broader known set at
  rank **79**.
- **Negative controls ranked low, as required:** EPCAM rank **511**, CDH1 rank **886**,
  ATP1A1 rank **1478**; CGN (cytoplasmic) was **removed by the topology gate** before scoring.

**How to read a low recall.** Here it reflects that TNBC's clinically validated antigens are
broadly expressed (correctly demoted by the safety axis) and that the run emphasized novelty.
A low recall is a signal to check (a) whole-cell vs snRNA datasets, (b) the disease label, and
(c) whether the harness list matches the tumor — not something to hide. The negative controls
ranking low is the more important QC signal, and here it passed.

## Safety honesty — the key reporting lesson

This is the single most common way the report goes wrong. In this run:

- **Full scored set (2,674 candidates):** safety-factor distribution was
  `0.1: 400 · 0.4: 90 · 0.7: 2,183 · 1.0: 1`.
  Of the 2,183 at 0.7, **2,170 are the neutral default for un-annotated genes** (only 13 were
  computed at 0.7). So **most of the scored set has no computed safety score**.
- **Assessed subset (504 annotated candidates):** distribution was
  `0.1: 400 · 0.4: 90 · 0.7: 13 · 1.0: 1` — i.e. only **14 of 504** assessed candidates had a
  *favorable* computed safety (≥0.7), and 400 were flagged for vital-organ expression.

❌ **Wrong (hallucination):** "Across the 2,674 scored candidates, the safety distribution is
heavily skewed to the floor (400 at 0.1 ...)" — this attributes the *assessed-subset*
distribution to the *full set* and hides that 2,170 genes were never assessed.

✅ **Right:** report the two distributions separately, state that **2,183/2,674 (≈82%)** of the
scored set sits at the neutral 0.7 default (2,170 un-annotated + 13 computed), and when
highlighting a top-N list, note how many have a *computed* safety score vs the default.

See the Safety Honesty section of the SKILL and `scoring_methodology.md`.

## Takeaways for a new run

1. Trust the topology gate and the multiplicative safety factor even when they yield few
   Tier-1 hits — that is the point (no housekeeping genes on top).
2. Verify `assign_compartment` behavior on your Census labels before interpreting specificity.
3. Report safety by **assessed subset vs full scored set**, and always disclose the
   neutral-default fraction.
4. Ground every clinical/mechanistic claim about a candidate in `literature_evidence.json`.
