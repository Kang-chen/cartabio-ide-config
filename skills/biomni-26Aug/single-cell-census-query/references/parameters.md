# Parameters, defaults, and rationale

Every knob in this skill, why it has the default it does, and how to change it. Defaults come from
a validated end-to-end run (an allergic-airway analysis of a 2-gene panel across ~1M nasal cells).

## Core biological parameters (no defaults — set per task)
| Param | Where | Notes |
|---|---|---|
| `GENE_PANEL` | all scripts | HGNC symbols; resolved to Ensembl `feature_id` at runtime. Warn (never silently drop) on unresolved/deprecated symbols. Panels of any size work; keep to genes of genuine interest for the atlas. |
| `TISSUES` | atlas, pre-flight, pseudobulk (multi-dataset mode) | Census `tissue_general` values. Confirm they exist in the pre-flight step. |
| `CASE_LABEL` / `CONTROL_LABEL` | pre-flight, pseudobulk, DESeq2, figures | Census `disease` values. **Both must be verified present before analysis.** Control is usually `"normal"`. |
| `ORGANISM` | atlas, pre-flight, pseudobulk | Default `"Homo sapiens"`. Other organisms are an untested extension (Census supports mouse). |

## Reproducibility
| Param | Default | Rationale |
|---|---|---|
| `CENSUS_VERSION` | `None` → latest stable | **Always pin and record** the resolved version in the report methods. The Census is versioned; results are only reproducible against a fixed version. The source run used a pinned dated release. |

## Pseudobulk aggregation
| Param | Default | Rationale |
|---|---|---|
| `DATASET_ID` | `None` | Prefer a **single dataset containing both groups** (no assay/batch confound — the source run did this). `None` = include all datasets with the groups in `TISSUES` (multi-dataset; then set `COVARIATES` or flag batch confound). Find shared datasets via `enumerate_labels.py`. |
| `MIN_CELLS_PER_SAMPLE` | `10` | Drop donor×cell_type pseudobulk samples built from too few cells (noisy). |
| `DONOR_BATCH` | `8` | Donors read per streaming chunk. Lower it if a batch OOMs; raise it for speed on big-memory machines. |
| `MAX_ACC_GB` | `24` | Safety abort if the dense group×gene accumulator (`n_groups × n_genes × 8` bytes) would exceed this. Raise only with a correspondingly larger machine. |

## DESeq2 / statistics
| Param | Default | Rationale |
|---|---|---|
| `MIN_DONORS_PER_GROUP` | `3` | A cell type is only tested if it has ≥3 donors in **both** groups. Donor is the replicate unit — fewer than 3 is not estimable. |
| gene filter | `rowSums ≥ 10` **and** detected in `≥ 3` samples | Standard low-count prefilter; stabilizes dispersion estimates. |
| min subset size | `nrow ≥ 50` genes, `ncol ≥ 4` samples | Guards against degenerate fits per cell type. |
| test / correction | Wald, **BH-FDR < 0.05** | Balanced discovery vs. rigor; the recommended default. For a stricter bar use padj < 0.01; for more sensitivity, 0.1. |
| contrast | `c("disease", CASE, CONTROL)` | Positive log2FC = up in case. |
| `COVARIATES` | `c()` (none) | **Extension:** add e.g. `c("sex")` or `c("assay")` when metadata supports it and confounding is a concern. The design becomes `~ <covariates> + disease` (disease tested last). Covariates constant within a subset are auto-dropped to avoid full-rank errors. Only include covariates with ≥2 levels and enough samples per level. |

## Figures
| Param | Default | Rationale |
|---|---|---|
| `MIN_CELLS_DISPLAY` | `200` | Cell types below this are excluded from dotplots (unstable % expressing). Full table is still saved. |
| `TOP_N_CT` | `15` | Top cell types (by % expressing) shown per dotplot panel to keep them readable. |
| palette | Phylo GOLD/BLUE/ORANGE/GREEN/GREY | Colorblind-aware; consistent with Phylo branding. |

## PDF figure sizing (learned bug — important)
Tall stacked figures (e.g. multi-panel dotplots) overflow the ReportLab frame when scaled to a
large width: at width 380 pt a ~9.5×9.2 in figure became taller than the ~670 pt usable frame and
threw `LayoutError`. **Fix:** in the report's image helper, bound **both** width and height, e.g.

```python
from reportlab.lib.utils import ImageReader
def fig(path, w, cap, max_h=560):
    iw, ih = ImageReader(path).getSize()
    scale = min(w / iw, max_h / ih)          # <-- constrain by BOTH width and height
    img = Image(path, width=iw * scale, height=ih * scale); img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1, 3), Paragraph(cap, cap_style)])
```

## Bring-your-own data (no Census)
If the user has their own single-cell data instead of using the Census:
1. **Own `.h5ad`:** ensure `.obs` has `donor_id`, `cell_type`, and a disease/condition column; ensure
   `.X` (or a layer) holds **raw counts**. Adapt `build_pseudobulk.py` to iterate your AnnData in
   donor batches instead of `cellxgene_census.get_anndata` (same sparse group-indicator matmul).
2. **Own donor×cell_type raw-count matrix + coldata:** skip Steps 3–4 entirely and feed the matrix
   straight into `run_pseudobulk_deseq2.R` (it only needs `pseudobulk_counts.csv`,
   `pseudobulk_coldata.csv`, `pseudobulk_var.csv` in the expected format).
The atlas step (normalized expression across cell types) requires a normalized layer; provide one or
skip the atlas and run DE only.

## Python-only fallback (against the R-preferred default)
The recommended DE path is **R DESeq2** (battle-tested). If a pure-Python pipeline is required,
`pydeseq2` reproduces the core DESeq2 model on the same pseudobulk matrix — but it is less
battle-tested; note that in the report if used.
