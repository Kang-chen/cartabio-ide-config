# Resource map: what this skill uses and what it defers to

## Biomni packages used directly (all pre-installed)
| Purpose | Package | Language |
|---|---|---|
| Census access | `cellxgene-census`, `tiledbsoma` | Python (no R equivalent — reason Census steps are Python) |
| Single-cell handling | `scanpy`, `anndata`, `scipy.sparse`, `numpy`, `pandas` | Python |
| Differential expression | `DESeq2`, `apeglm` | R (recommended stats path) |
| Figures | `matplotlib` (+ `adjustText` if available), `ggplot2`/`ggrepel`/`ComplexHeatmap` (optional R plots) | Python / R |
| PDF | `reportlab`, `pypdf` | Python (via the `pdf-report-generation` skill) |

Python-only DE fallback (not preferred): `pydeseq2`.

## Biomni agent tools this skill invokes
- **`LiteratureSearch`** — REQUIRED Step 7: ground the top DE genes and the gene-panel biology in
  real papers; populate the report's References and Next Steps. Verify specifics before writing;
  cite inline `[N]`.
- **Direct checks** — verify documented functions with imports when extending
  downstream.
- **`ManageMachine`** — provision a ≥32 GB machine for the pseudobulk build (Step 4); run it as a
  background job for large datasets.
- **`WebSearch` / `WebFetch`** — for CZ CELLxGENE Census schema/API docs or dataset landing pages
  when a filter/column name needs confirming.

## Sibling skills (reference, do NOT reimplement)
| Need | Skill | When |
|---|---|---|
| Phylo PDF branding/layout/validation | **`pdf-report-generation`** | Always — Step 8 defers all report mechanics to it. |
| Enrichment / pathways from the DE results | **`functional-enrichment-from-degs`**, **`pathway-enrichment`** | Optional downstream (GSEA/ORA on `pseudobulk_DE_all_results.csv`). Mention in Next Steps. |
| Generic counts→DE (no Census) | **`bulk-rnaseq-counts-to-de-deseq2`** | The user has a bulk/own count matrix rather than Census single-cell data. |
| Full scRNA preprocessing from raw droplets | **`scrnaseq-scanpy-core-analysis`** | The user needs QC/clustering/annotation, not an atlas query of a fixed set of genes. |
| Drug-target prioritization / genetic evidence | **`scrna-disease-drug-discovery`** | The user wants to turn DE hits into prioritized targets (Open Targets, GeneBass, TWAS). |
| Sourcing a disease absent from the Census | **`omics-dataset-retrieval`** | Pre-flight found the disease is not in the Census → pull from GEO/other repositories. |

## Optional downstream datalake / databases (for Next Steps, not core)
- **MSigDB** + `gseapy` — gene-set enrichment of DE results.
- **Open Targets**, **GeneBass**, **cBioPortal**, **DepMap** — genetic/functional target evidence.
- **CellMarker2** — sanity-check cell-type identities behind the pseudobulk groups.

## Census query cheat-sheet (verified patterns)
- Atlas (expression): `X_name="normalized"`, `var_value_filter="feature_name in [...]"`,
  `obs_value_filter="tissue_general in [...] and disease == '<control>' and is_primary_data == True"`.
- Pseudobulk (counts): `X_name="raw"`, obs filter on `dataset_id`/`tissue_general` +
  `disease in ['<case>','<control>']` + `is_primary_data == True`; stream via `obs_coords` batches.
- Gene resolution: read `ms["RNA"].var` columns `feature_id`, `feature_name`, `soma_joinid`.
- Label enumeration: read `.obs` columns `disease`, `tissue_general`, `dataset_id`, `donor_id`,
  `cell_type` with the tissue/primary-data filter, then group/count.
