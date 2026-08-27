---
name: lipidomics-summary-stats
description: >
  Read complex lipid and free sterol Excel spreadsheets, harmonize column names,
  compute per-metabolite log2FC and p-values for all pairwise comparisons, and
  output a styled master Excel workbook with per-class tabs and a significant
  hits summary.
tags: [lipidomics, statistics, excel, log2FC, t-test]
language: R
---

# Lipidomics Summary Statistics Pipeline

## Overview

This skill integrates complex lipid (CL) and free sterol (FS) data from separate
Excel spreadsheets into a single master workbook. For each metabolite it computes
log2 fold changes and Welch's t-test p-values across all specified pairwise
comparisons, then writes a styled Excel file with per-class tabs and a
consolidated significant-hit summary.

## Data Layout

Both CL and FS input files share the same row-per-sample layout:
- **Rows** = samples (one per mouse)
- **Columns** = metabolites
- First 4 columns: SampleID, Genotype, Virus, Condition
- Remaining columns: metabolite concentrations

The pipeline transposes this to metabolite-per-row for statistics computation.

## Inputs

### Required

| Parameter | Type | Description |
|-----------|------|-------------|
| `cl_file` | string | Path to the complex lipids Excel file (multi-tab, one tab per lipid class) |
| `fs_file` | string | Path to the free sterols Excel file (single-tab) |

### Optional (with defaults)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sample_key` | data.frame | 21-sample mouse tumor key | Mapping of Sample IDs (character) to Condition names |
| `condition_order` | character vector | `c("WT","Cpt2-KO","Cpt1-KO","Tsc1-KO","Cpt2/Cpt1-DKO","Cpt2/Tsc1-DKO")` | Display order for conditions |
| `fc_threshold` | numeric | 1 | Minimum absolute log2FC for significance |
| `p_threshold` | numeric | 0.05 | Maximum p-value for significance |
| `min_per_group` | integer | 2 | Minimum positive non-NA values per group for t-test |
| `output_file` | string | `"CL_FS_Master_Summary_Statistics.xlsx"` | Output Excel filename |

### Sample Key Format

A data.frame with columns `SampleID` (character) and `Condition` (character):

```r
sample_key <- data.frame(
  SampleID = as.character(1:21),
  Condition = c("WT","WT","WT","WT","WT","WT",
                "Cpt2/Cpt1-DKO","Cpt2/Cpt1-DKO","Cpt2/Cpt1-DKO",
                "Cpt2/Tsc1-DKO","Cpt2/Tsc1-DKO","Cpt2/Tsc1-DKO",
                "Tsc1-KO","Tsc1-KO","Tsc1-KO",
                "Cpt1-KO","Cpt1-KO","Cpt1-KO",
                "Cpt2-KO","Cpt2-KO","Cpt2-KO"),
  stringsAsFactors = FALSE
)
```

## Pipeline Steps

### Step 1: Read & Harmonize Complex Lipid Data

For each class tab in the CL file:

1. Read with `read_excel(path, sheet = s, skip = 7)` — row 1 becomes the header (metabolite names)
2. Set first 4 column names to SampleID, Genotype, Virus, Condition
3. Filter rows to numeric Sample IDs only (`grepl("^[0-9]+$", SampleID)`)
4. Identify metabolite columns (skip first 4 + any "Total" columns)
5. Transpose to metabolite-per-row matrix
6. Drop metabolites where ALL values are NA
7. Compute per-metabolite, per-comparison statistics:
   - **log2FC**: `mean(log2(ko_vals)) - mean(log2(ref_vals))` where values are positive, non-NA, and each group has >= `min_per_group` values
   - **p-value**: `t.test(log2(ko_vals), log2(ref_vals))$p.value` (Welch's t-test)
8. Build a data.frame per class with sample values, means, log2 means, log2FC, p-values

Tab-to-class matching uses regex patterns with case-insensitive matching to handle
variations like "SM data" vs "SM Data".

### Step 2: Read & Harmonize Free Sterol Data

1. Read with `read_excel(fs_path, sheet = 1, skip = 8)` — **no row-1-as-header override** (different from CL)
2. Same transpose and statistics computation as Step 1
3. All FS metabolites are retained (even those with all-zero values)

### Step 3: Combine into Unified DataFrames

1. **"All Data" sheet**: `rbind` all per-class data.frames
2. **"Significant FC Summary" sheet**: long-format table of all significant hits with columns: Class, Metabolite, Comparison, Log2.FC, P.value, Direction

### Step 4: Write Styled Excel Workbook

- Blue header row (#4472C4) with white bold text
- Thin grey borders on all cells
- Number formatting (4 decimal places for p-values/FC/log2 means, 2 for concentrations/counts)
- Auto-width columns, frozen header row
- Sheet order: All Data, Significant FC Summary, then per-class sheets

## Comparisons

Nine pairwise comparisons are computed:

| # | KO Condition | Reference |
|---|-------------|-----------|
| 1 | Cpt2-KO | WT |
| 2 | Cpt1-KO | WT |
| 3 | Tsc1-KO | WT |
| 4 | Cpt2/Cpt1-DKO | WT |
| 5 | Cpt2/Tsc1-DKO | WT |
| 6 | Cpt1-KO | Cpt2-KO |
| 7 | Tsc1-KO | Cpt2-KO |
| 8 | Cpt2/Cpt1-DKO | Cpt2-KO |
| 9 | Cpt2/Tsc1-DKO | Cpt2-KO |

## Output Spec

The output Excel workbook contains:

| Sheet | Description |
|-------|-------------|
| All Data | All metabolites from all classes (1 row per metabolite) |
| Significant FC Summary | Long-format table of significant hits only |
| PC, P-PC, O-PC, LPC, PE, P-PE, LPE, PS, LPS, PI, PG, SM, Cer, TG, DG, AC, CE | Per-class CL data |
| FS | Free sterol data |

### Column Naming Convention

- Sample columns: `Sample.1(WT)`, `Sample.2(WT)`, ... (condition in parentheses)
- Counts: `n.WT`, `n.Cpt2-KO`, ...
- Means: `Mean.WT`, `Mean.Cpt2-KO`, ...
- Log2 means: `Log2.Mean.WT`, `Log2.Mean.Cpt2-KO`, ...
- Log2FC: `Log2.FC.Cpt2-KO/WT`, `Log2.FC.Cpt2/Cpt1-DKO/Cpt2-KO`, ...
- P-values: `p-value.Cpt2-KO/WT`, `p-value.Cpt2/Cpt1-DKO/Cpt2-KO`, ...

## Known Differences Between CL and FS Input Formats

| Aspect | Complex Lipids | Free Sterols |
|--------|---------------|--------------|
| Skip rows | 7 | 8 |
| Header override | Row 1 becomes header | No override needed |
| Number of tabs | Multiple (one per class) | Single tab |
| Class label | Derived from tab name via regex | "FS" |
| Concentration unit | pmol/mg | ng/mg |
| NA handling | All-NA metabolites dropped | All metabolites retained |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| R kernel crashes | Run via `Rscript` in Bash instead of the R kernel |
| Excel file is 0 bytes on /mnt/results | Write to `/workspace/` first, then `cp` to `/mnt/results/` (FUSE limitation) |
| Tab name mismatch | The pipeline uses regex patterns with case-insensitive matching; check config.yaml patterns |
| t-test fails with <2 values | Comparisons with fewer than `min_per_group` positive values per group produce NA |
| openxlsx not installed | Install with `install.packages("openxlsx", lib = "/mnt/shared-workspace/r-libs")` |
| readxl "New names" warnings | These are harmless — caused by empty cells in the header row before override |

## Dependencies

- R >= 4.0
- readxl
- openxlsx
