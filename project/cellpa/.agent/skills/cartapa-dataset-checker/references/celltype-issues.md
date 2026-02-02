# Celltype Annotation Issues Reference

## IMC-TNBC

### Epithelial Cell Naming
- **Problem**: In h5ad annotations, epithelial cells do NOT contain "epi" substring
- **Original names**: May appear as markers like "CK+", "E-cadherin+", "pan-CK+"
- **Risk**: Easy to misclassify as immune cells based on name alone
- **Solution**: Check original paper Table S1 for marker-to-celltype mapping

### Cell Type List (from paper)
```
- Tumor cells (CK+, Ki67+ variants)
- Myoepithelial cells
- Endothelial cells
- Fibroblasts
- Macrophages (CD68+, CD163+)
- T cells (CD4+, CD8+, regulatory)
- B cells
- NK cells
- Dendritic cells
```

## SAFE-HNSCC

### Missing Stromal Category
- **Problem**: Original annotations lack dedicated "stromal" cell type
- **Impact**: May affect TME composition analysis
- **Workaround**: Check for fibroblast/CAF markers in expression data

### FOV/Tile Coordinate Issue 
- **Problem**: Coordinates are tile-local (0-1500 range per FOV)
- **Symptom**: All slices overlap when combined
- **Solution**: Apply grid-based offset per FOV (see fix_coords.py)

### Patient Naming
- Format: `PIO{number}` (e.g., PIO1, PIO2, ...)
- Slice ID format: `safe_hnscc_all_PIO{number}`

## CODEX-TNBC

### Pre/Post Treatment Ambiguity
- **Problem**: Raw data labels for treatment timing unclear
- **Risk**: Incorrect train/test splits if pre/post confused
- **Solution**: Cross-reference with:
  - Metadata CSV
  - Patient clinical info
  - Original publication supplementary tables

### Response Labels
- May use different formats: 0/1, R/NR, Responder/Non-responder
- Normalize to 0/1 for model training

## CODEX-HCC

### Standard Reference (fewest issues)
- 24 Pre-treatment regions, 24 Post-treatment regions
- Clear patient_id (70-86)
- Consistent celltype annotations (14 types)
- state column: "Pre" or "Post"

### Cell Types (14 categories)
```
Unknown
Endothelial cells
CD4 T cells
CD8 T cells
Treg cells
B cells
NK cells
Dendritic cells
Macrophages
Mast cells
Neutrophils
Fibroblasts
Hepatocytes
Tumor cells
```

## General Validation Checklist

### Before Model Training
- [ ] Cell count within expected range
- [ ] All required obs columns present
- [ ] No duplicate cell IDs
- [ ] Celltype distribution reasonable (no single type >80%)
- [ ] Response labels balanced or documented

### Coordinate Checks
- [ ] Spatial range appropriate for dataset type
- [ ] No NaN coordinates
- [ ] Slices don't overlap (if multi-slice)
- [ ] Coordinate units consistent (microns vs pixels)

### Embedding Checks (post-extraction)
- [ ] Shape: (n_cells, 128) for CartaPA embeddings
- [ ] No NaN/Inf values
- [ ] Response probabilities in [0, 1]
- [ ] Cell order matches original h5ad
