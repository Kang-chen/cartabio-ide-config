# Evaluation Tests

This directory contains tests for the `experimental-design-statistics` skill package.

## Test Files

| File | What it tests | Dependencies |
|------|--------------|--------------|
| `test_variance_model.R` | CV/dispersion identity arithmetic, `assert_count_scale` guard, the units-defect regression test (fixed-dispersion n must decrease as lambda0 increases), and the analytic anchor (`power == 0.1904`). | `RNASeqPower`, `RnaSeqSampleSize` (tests skip if absent, but all-skipped = failure) |
| `test_export_consistency.R` | The design consistency gate: mutated `n_per_group` must stop and write no files; wrong batch row count must trip check 3; happy-path file set must match the SKILL.md Outputs list including the three sensitivity CSVs. | `RNASeqPower`, `RnaSeqSampleSize`, `jsonlite` |
| `test_sensitivity_tables.R` | Sensitivity tables carry real information: `pi0_sensitivity` has >= 4 rows, `fdr_n_per_group` is strictly decreasing in `de_proportion`, the caller's row carries the marker, and `cv_sensitivity` n is strictly increasing in CV. | `RNASeqPower`, `RnaSeqSampleSize` |
| `test_power_grid_range.R` | The exported power-table grid spans the recommendation: the headline n is a row, the grid extends strictly beyond it, the low-n anchors {3,5,8,10,15,20} are retained, FDR-aware power at the headline n meets target, and the export gate (check 7) stops (writes nothing) when the grid is truncated below the headline n. | `RNASeqPower`, `RnaSeqSampleSize` |
| `test_layout_id_range.R` | The batch-layout TEMPLATE line states the true sample-ID span derived from the data (not the first/last row of the batch-sorted table), and the pre-export guard stops before writing when a stated range does not match the layout. | none (base R) |
| `test_cv_by_tissue_assay.R` | `plot_cv_by_tissue` serves one assay directly (default bulk RNA-seq), one point per tissue with the error bar taken from the database's own CV_Min/CV_Max: bulk-RNA-seq-only tissues, PBMC = 0.40 (0.30-0.50), non-zero ranges for single-row tissues, multi-organism tissues (Brain) combined, no clipping against the 0.6 axis, and an informative error for an unknown assay. | `ggplot2`, `ggprism` |

## Running

From the package root:

```bash
Rscript assets/eval/test_variance_model.R
Rscript assets/eval/test_export_consistency.R
Rscript assets/eval/test_sensitivity_tables.R
Rscript assets/eval/test_power_grid_range.R
Rscript assets/eval/test_layout_id_range.R
Rscript assets/eval/test_cv_by_tissue_assay.R
```

Exit codes: `0` = all passed, `1` = any failure, `2` = all skipped (also failure — no evidence gathered).

## Test Data

See `datasets/README.md` for the pasilla dataset dependency used by the example workflow.
