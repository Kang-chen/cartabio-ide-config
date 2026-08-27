# CZ CELLxGENE Census — multi-atlas pull guide

## Why whole-cell, multi-atlas

- **Whole-cell over single-nucleus.** Single-nucleus RNA-seq under-detects membrane and
  secreted transcripts, systematically missing surface antigens (single-nucleus atlases
  are a poor substrate for surfaceome discovery). `census_pull.py`
  filters on `suspension_type == 'cell'` by default and de-weights nucleus data.
- **Multiple atlases.** A target enriched in one study can be a batch/annotation artifact.
  The skill aggregates across datasets and rewards cross-dataset consensus.

## Step 1 — discover the right labels first

Census labels must match exactly, and `disease` can be a `||`-composite string in newer
schemas. Always discover before pulling:

```python
from census_pull import discover_datasets
ds = discover_datasets("lung adenocarcinoma", census_version="2025-11-08")
print(ds)   # dataset_id, n_cells, suspension_type, is_whole_cell
```
Or use the `czi-cellxgene-census` skill's `discover` command for a broader label sweep
(tissue / disease / assay / cell_type) and copy labels verbatim.

## Verified Census coverage (checked 2026-06, Census `2025-11-08`)

Disease coverage is uneven — **confirm it before promising a multi-atlas run.** Verified facts:

- **The demo tumor is `lung adenocarcinoma` (LUAD)** (~0.83M cells, whole-cell, many atlases) — ideal for the multi-atlas engine, and the lung subtype where the validated ADC/bispecific antigens concentrate (EGFR/HER2-mutant; c-MET and CEACAM5 in nonsquamous disease; TROP2).
- **Lung-cancer labels are split by granularity in Census** — distinct labels: `lung adenocarcinoma` (~0.83M), `squamous cell lung carcinoma` (~0.24M), `non-small cell lung carcinoma` (~0.09M, umbrella-level, mostly adeno), `small cell lung carcinoma` (~0.08M). A `disease == 'lung adenocarcinoma'` pull does NOT include cells labeled only `non-small cell lung carcinoma`. To maximize malignant coverage, pass a list — `pull_compartment_expression(genes, ['lung adenocarcinoma', 'non-small cell lung carcinoma'])` — but keep `squamous cell lung carcinoma` separate to preserve adenocarcinoma specificity.
- **Other Census-rich whole-cell tumors:** `breast cancer` (~1.3M), `malignant ovarian serous tumor` (~0.93M), `colon adenocarcinoma` (~0.26M), `squamous cell lung carcinoma`, `gastric cancer`, `renal cell carcinoma`, `metastatic melanoma`.
- **Coverage varies sharply** — some tumor labels are single-nucleus-only or hold only a few thousand cells in one dataset, where a `whole_cell_only=True` pull returns nothing. For those, supply a curated whole-cell atlas via the own-`.h5ad` path instead of Census.
- Always verify the exact label from `census["census_info"]["summary_cell_counts"]` (category == `disease`) — do not guess.

## Compartment mapping

`census_pull.assign_compartment()` maps Cell Ontology `cell_type` labels to:

- **epithelial** (the target compartment) — epithelial / ductal / acinar / malignant /
  neoplastic / tumor / carcinoma / ADM. Tumor cells are grouped here.
- **caf** — fibroblast / myofibroblast / stellate / mesenchymal stromal
- **immune** — T/B/NK/macrophage/monocyte/dendritic/mast/plasma/lymphocyte/neutrophil/myeloid
- **endothelial** — endothelial / vascular
- **other** — everything else (pericyte, Schwann, endocrine, etc.; not used in specificity)

If an atlas labels tumor cells only as generic epithelial subtypes (not "malignant cell"),
they are still captured. For your own annotated `.h5ad`, pass the malignant label/markers
so the malignant compartment is not diluted by normal epithelium.

## Expression normalization

Raw counts are pulled (`X_name="raw"`) and normalized deterministically with CP10k + log1p
(`scanpy`), so results are reproducible across Census versions. `pct_expressing` uses raw > 0.

## Cohort accounting (analysed vs discovered)

`pull_compartment_expression()` subsamples each atlas to `max_cells_per_dataset` (default 20,000)
**before** analysis and drops atlases with <20 epithelial cells. The **discovery catalogue** for a
disease label can therefore be ~10× the number of cells actually analysed — do not headline the
catalogue figure. Two artefacts record the honest counts:

- `cohort_cell_counts.csv` — one row per atlas: `n_discovered` (catalogue whole-cell count),
  `n_subsampled`, per-compartment counts (`n_epithelial`/`n_caf`/`n_immune`/`n_endothelial`/`n_other`),
  `used`, `skip_reason`. This exposes any atlas contributing very few cells to a compartment.
- `cohort_summary.json` — totals (`n_cells_discovered_full`, `n_cells_analyzed`, `n_datasets_analyzed`),
  the subsample cap, and a pre-formatted `cohort_statement` (e.g. "5 atlases totalling 831,387 whole-cell
  cells discovered; 83,412 cells analysed after per-atlas subsampling to 20,000").

`export_results` re-derives the analysed count from `compartment_expression.csv` (the analysed matrices)
and **raises** if the reported analysed count does not match — so the report can only headline the
analysed cohort.

## Scope: seed vs full surfaceome

- **Bundled seed** (`references/surfaceome_seed.csv`, ~65 curated genes incl. the validation
  harness antigens and a range of surface/topology examples) — fast demo / smoke test.
- **Full in-silico surfaceome** (~2,800 genes; Bausch-Fluck SURFY) — genome-wide discovery.
  Use `surfaceome_filter.load_surfy_surfaceome()` — it downloads SURFY Table S3 and derives a
  **per-gene** topology / ectodomain-accessibility / localization call from SURFY's own topology
  string, TM count, Almen class, and evidence source (CSPA training set / GPI / machine learning).
  Pass the returned DataFrame to `pull_compartment_expression()` and `apply_topology_filter()`.

  ```python
  from surfaceome_filter import load_surfy_surfaceome, apply_topology_filter
  surfaceome = load_surfy_surfaceome()                 # downloads + caches Table S3
  spec = pull_compartment_expression(surfaceome["gene_symbol"].tolist(), disease_label, census_version)
  surf = apply_topology_filter(spec, surfaceome)       # per-gene gate; RAISES if it excludes nothing
  ```

  🚨 **Do NOT hand-build the surfaceome by assigning every SURFY member `localization='plasma_membrane'`
  and `ectodomain_accessibility='high'`.** That makes the topology gate inert (it then excludes nothing
  and `apply_topology_filter` raises on a genome-scale set). SURFY carries real per-gene topology — use
  it. Organelle-membrane residents and proteins with no accessible ectodomain (e.g. ESYT3, a 5-residue
  extracellular loop) are gated; machine-learning-only surface predictions without independent Open
  Targets / CSPA confirmation are kept but flagged `unconfirmed` (reported in `report_facts.json`).

  Download note: `wlab.ethz.ch` migrated to `wollscheidlab.org/SURFY` and now serves a Git-LFS pointer
  for the `.xlsx`; the loader falls back to a byte-identical mirror (sha256-verified) and validates the
  payload is a real workbook. Pass `source=<local path>` to run fully offline.

## Cost / runtime

Pulling ~60 genes across ~5 datasets is a few minutes. The full surfaceome (~2,800
genes) per dataset is heavier — pull per dataset (the script already iterates), and cap
`max_datasets` if needed. Pin `census_version` in the report for reproducibility.

## Reproducibility checklist

- [ ] `census_version` recorded in the manifest
- [ ] `suspension_type == 'cell'` applied (or snRNA inclusion justified)
- [ ] `is_primary_data == True` to avoid duplicate cells
- [ ] disease label confirmed via `discover` (mind `||`-composite labels)
- [ ] ≥2 datasets contributing to consensus where available
