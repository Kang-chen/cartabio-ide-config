# Reference & preprocessing tuning

Reference quality and preprocessing dominate deconvolution accuracy — more than the
choice of algorithm (Avila Cobos *Nat Commun* 2020). Get these right first.

## 1. Reference completeness (the #1 lever)

**The reference must contain every cell type that is present in the bulk.** If a
population in the bulk has no matching signature, its expression is misattributed to
the cell types that *are* present — biasing **all** methods, not just one.

- Build the reference from the **same tissue / disease context** as the bulk.
- Inspect the cell-type table printed by `load_reference()` and ask: *could the bulk
  contain a population not in this list?* (e.g., neutrophils are common in blood bulk
  but often dropped from scRNA references — a classic gap).
- A **matched smaller** reference beats a **large mismatched** atlas (BAL benchmark 2026).

## 2. Scale: linear, not log

BayesPrism / DWLS / MuSiC expect **non-log, counts-like** input.
`run_deconvolution()` auto-detects log2-scale bulk (negatives, or max < ~30 with
small range) and exponentiates `2^x`. Check the `bulk_scale` field in the result and
the `[deconv]` message. If your data is TPM/CPM (linear) this is a no-op — correct.

The single-cell reference should be **raw counts** (the `counts` assay). The Census
builder writes raw counts; `scrnaseq-*-core-analysis` outputs keep a counts layer.

## 3. Gene IDs must match

Bulk and reference must use the **same gene identifier type** — almost always **gene
symbols**. `run_deconvolution()` intersects row names and warns if < 50 genes are
shared (a near-certain ID mismatch: symbols vs Ensembl, or human vs mouse casing).
`scripts/census_reference.py` sets gene **symbols** as `var_names` for this reason.

## 4. Cells per type

- **≥ ~25–50 cells/type** for a stable signature; rarer types give noisy estimates.
  `load_reference()` drops types with < 3 cells outright.
- **Cap** very abundant types for speed: `run_deconvolution(..., max_cells_per_type = 300)`
  (default 300). BayesPrism cost scales with cell count — lower this (e.g., 150–200)
  if it is slow or memory-bound.
- **Merge** biologically-redundant subtypes if they are method-fragile and you don't
  need the fine distinction (e.g., collapse `CD4_T_naive` + `CD4_T_mem` → `CD4_T`).

## 5. Collinear / closely-related cell types

Closely-related types (T-cell subsets, keratinocyte subsets) share markers →
collinear signatures → unstable estimates. Symptoms: the pair shows low per-type
concordance and appears in `fragile_cell_types`.

- **DWLS** is the most collinearity-robust of the panel (it down-weights high-variance
  genes) — trust it more for fragile types.
- Consider analysing at a **coarser cell-type granularity** if subtypes are fragile.

## 6. Group-contrast settings (`proportion_contrasts`)

- `group_levels` — names the two groups to compare (default: the two most frequent).
- `timepoint_col` — tests **within each timepoint** separately, then BH-FDR across all
  cell-type × timepoint cells.
- `subject_col` + `mixed_model = TRUE` — fits `prop ~ group*timepoint + (1|subject)`
  (needs `lmerTest`); use for repeated-measures / longitudinal designs.
- `min_n` (default 3) — minimum samples per group for a Wilcoxon test. With small n,
  the smallest achievable p-value is bounded (n = 3/group → min two-sided p ≈ 0.1;
  n = 6/group → ≈ 0.002), which limits power after FDR. Prefer **≥ 5–6 per group**.

## 7. Sanity checks

- Proportions per sample should sum to ~1 (the scripts renormalize defensively).
- On a **simulated/known-truth** run (example data), check `ground_truth_recovery.csv`:
  overall Pearson r should be **> 0.8** and per-type RMSE small.
- If methods disagree wildly (overall r < 0.5), suspect a **scale** or **gene-ID**
  problem or a **missing cell type** before blaming the algorithm.
