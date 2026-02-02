---
name: cartapa-dataset-checker
description: >-
  Validate and quality-check spatial proteomics datasets for CartaPA analysis.
  Triggers: "check dataset", "validate h5ad", "verify data quality", "dataset QC",
  "check celltype annotations", "verify embeddings", "data validation".
  Checks cell counts, obs metadata (treatment status, patient ID, celltypes),
  coordinate validity, embedding dimensions, and known dataset-specific issues.
  Supports CODEX-HCC, CODEX-TNBC, IMC-TNBC, SAFE-HNSCC datasets.
---

# CartaPA Dataset Checker

Validate spatial proteomics datasets before CartaPA model training or embedding extraction.

## Quick Validation

```python
# Run comprehensive check on any h5ad
python scripts/check_dataset.py --input data.h5ad --dataset-type auto
```

## Core Checks

### 1. Basic Structure
- Cell count matches expected range per dataset
- Required obs columns present: `slice_id`, `patient_id`, `cell_type`/`celltype`
- Spatial coordinates valid (no NaN, reasonable range)
- Expression matrix shape matches var count

### 2. Treatment/Response Labels
- `state` or `treatment` column: check for pre/post values
- `response` or `label` column: check for 0/1 or R/NR values
- Verify consistency within patient (all cells from same patient have same response)

### 3. Celltype Annotations
Check for known issues (see [references/celltype-issues.md](references/celltype-issues.md)):

| Dataset | Issue | Solution |
|---------|-------|----------|
| IMC-TNBC | Epithelial cells not labeled "epi", may look like immune | Check paper for actual marker names |
| SAFE-HNSCC | Missing stromal cell category | Verify with original annotation |
| CODEX-TNBC | Unclear pre/post labels in raw data | Cross-reference metadata carefully |

### 4. Coordinate Validation
- Check for tile-level vs slice-level coordinates (T4 issue)
- Flag if all cells share same small coordinate range (~0-1500)
- Verify no coordinate overlap between slices

### 5. Embedding Quality (if present)
- `obsm['X_cartapa']` shape should be (N, 128)
- Check for NaN or Inf values
- Verify response probability range [0, 1]

## Dataset-Specific Expectations

| Dataset | Cells | Slices | Proteins | Key obs columns |
|---------|-------|--------|----------|-----------------|
| CODEX-HCC Pre | ~490K | 24 | 51 | state=Pre, patient_id, celltype |
| CODEX-TNBC | ~1.9M | 28 | 56 | patient_id, pre_or_post, celltype |
| IMC-TNBC | ~1M | 243 | 41 | patient_id, treatment, celltype |
| SAFE-HNSCC | ~2.1M | 41 | 27 | slice_id, patient_id, celltype |

## Validation Commands

```bash
# Full validation with report
python scripts/check_dataset.py --input data.h5ad --report results/validation_report.md

# Quick celltype summary
python scripts/check_dataset.py --input data.h5ad --quick --celltypes-only

# Check coordinates for tile issues
python scripts/check_dataset.py --input data.h5ad --check-coords --visualize
```

## Output

Validation produces:
- Console summary with pass/fail indicators
- Optional markdown report with detailed findings
- Optional visualization of coordinate distribution

## When to Use

Run validation:
1. **Before model training**: Ensure data quality
2. **After embedding extraction**: Verify embeddings attached correctly
3. **When integrating new datasets**: Check format compatibility
4. **After coordinate fixes**: Verify spatial structure
