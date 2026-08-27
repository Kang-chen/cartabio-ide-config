# Method selection guide

## The core finding: there is no universally-best method

Multiple independent benchmarks converge on the same conclusion — deconvolution
accuracy is **method × reference × tissue dependent**, and **reference quality +
linear-scale preprocessing dominate the choice of algorithm**:

- Avila Cobos F, et al. *Nat Commun* 2020 — pipeline benchmark; preprocessing and
  reference choice matter more than the estimator.
- Dietrich A, et al. *omnideconv* 2024 — ensemble/benchmark framework; no single
  winner across datasets.
- *Brief Bioinform* 2025 deconvolution guideline; real-paired BAL benchmark 2026 —
  a well-matched smaller reference beats a larger mismatched one.

**Consequence for this skill:** run a **panel** of methods and report
**cross-method concordance** rather than betting on one. Cell types whose estimate
*flips* across methods are flagged as **method-fragile** and interpreted with
caution.

## What we run (and why)

| Method | Idea | Wins when | License |
|--------|------|-----------|---------|
| **BayesPrism** (primary) | Bayesian; jointly models the reference and the bulk, *adjusting* the reference to fit the mixture | Reference and bulk are from **different platforms / donors** (e.g., a Census reference vs your bulk cohort); complex tissue | GPL-3 |
| **DWLS** (cross-check) | Dampened weighted least squares; down-weights high-variance genes | Many **closely-related / collinear** cell types (e.g., T-cell or keratinocyte subsets) | GPL-2 |
| **MuSiC** (optional) | Weights genes by **cross-subject consistency**; needs multi-subject reference (`donor_id`) | Reference has several donors; want subject-robust marker weighting | GPL-3 |
| **Bisque** (optional) | Fast marker-based reference decomposition | Quick concordance check; large sample counts | GPL-3 |

Default = **BayesPrism + DWLS** — a robust-to-mismatch primary plus a
collinearity-aware cross-check. Add MuSiC + Bisque for a 4-method concordance panel
when the reference has multiple donors.

## How concordance is computed

`run_deconvolution()` returns `method_concordance.csv`:
- **`__overall__`** rows — Pearson r across all (sample × cell-type) estimates for
  each method pair. Overall r > ~0.8 indicates the methods broadly agree.
- **per-cell-type** rows — Pearson r across samples for each cell type. Low per-type
  r → that population is **method-fragile**.
- `fragile_cell_types` — types whose mean pairwise per-type r < 0.5.

Report the consensus (mean across methods) as the headline estimate, but always
state which types were fragile.

## The dominant levers (more important than the method)

1. **The reference must contain every cell type present in the bulk.** A missing
   population forces its signal onto the wrong types and biases *all* methods
   (Avila Cobos 2020). This is the single most common failure mode.
2. **Score on linear scale.** BayesPrism/DWLS/MuSiC expect non-log,
   counts-like input. `run_deconvolution()` auto-detects log2 bulk and
   exponentiates (`2^x`); verify the `bulk_scale` note in the output.
3. **Matched > large.** A smaller reference from the right tissue/condition beats a
   big mismatched atlas (real-paired BAL benchmark 2026).
4. **Enough cells per type.** Rare types (<≈25–50 cells) give noisy signatures;
   merge or drop them. See [parameter-tuning.md](parameter-tuning.md).

## Excluded by license (not by performance)

CIBERSORTx is a frequent benchmark top-performer but is **Stanford non-commercial**
— off-limits on a commercial platform. EPIC is academic-only; BSeq-sc wraps
CIBERSORT. See [license-notes.md](license-notes.md). `run_deconvolution()` refuses
these method names.
