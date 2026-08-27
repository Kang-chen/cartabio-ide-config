---
id: "skill_206b4bf7080d00a289bb97143936de68"
name: "scatac-multiome-analysis"
description: "Use for end-to-end scATAC-seq, snATAC-seq, or 10x Multiome chromatin-accessibility analysis from fragments, peak matrices, or Cell Ranger ATAC/ARC outputs. Covers ATAC QC, TF-IDF/LSI, clustering, MACS3 peaks, gene activity, cell annotation, label transfer, and differential accessibility with Signac/Seurat."
category: "epigenomics"
visibility: "public"
starting-prompt: "Analyze my scATAC-seq data (fragments/peak matrix): QC, LSI clustering, peak calling, and cell-type annotation from open chromatin."
---

# scATAC-seq End-to-End Analysis

A parameterized, fragments-first pipeline that takes raw single-cell ATAC-seq output to a
publication-style report: QC → LSI → clustering → per-cluster MACS3 peak recalling →
gene-activity cell-type annotation (with confidence flags) → differential accessibility →
Phylo PDF report. Every stage is a checkpointed R script driven by one `config.yaml`; the
report generator is Python (ReportLab). Human and mouse genomes are supported via a genome
registry that keeps the EnsDb annotation, BSgenome, and ENCODE blacklist mutually consistent.

## Scope

**Does:** ingest + QC + dimensionality reduction + clustering + MACS3 peak recalling +
marker/gene-activity annotation + optional label transfer + differential accessibility +
figures/tables + PDF report + provenance manifest, from a fragments file (the primitive),
optionally accelerated by a peak-barcode matrix, including 10x multiome (splits GEX + ATAC).

**Does NOT:** raw-read alignment / fragment generation (start from Cell Ranger ATAC/ARC or an
equivalent fragments file); TF motif / chromVAR analysis, peak-to-gene linkage, or trajectory
inference (these are recommended next steps, not implemented here); cross-sample batch
integration (single-sample workflow — integrate upstream or as a follow-up).

## Inputs

Provide via `config.yaml` (copy `scripts/config.example.yaml`):

- **Required:** an ATAC **fragments file** (`atac_fragments.tsv.gz`) **and its Tabix index**
  (`.tbi`). This is the analytical primitive — everything (QC metrics, peak recalling,
  gene activity, coverage) is recomputed from it.
- **Optional accelerator / QC comparison:** a peak-barcode matrix
  (`filtered_peak_bc_matrix.h5` or the MTX triplet). Used to seed cells and to compare a
  reference peak set against MACS3-recalled peaks. If absent, cells are called from the
  fragments via a knee/quantile cutoff and peaks come entirely from MACS3.
- **Multiome:** set `input.multiome: true` with a combined `filtered_feature_bc_matrix.h5`
  (Gene Expression + Peaks) plus `atac_fragments.tsv.gz`; the RNA modality is split out and
  used to annotate, then labels are transferred to the ATAC cells.
- **Genome:** `genome.build` ∈ {`hg38` (default), `hg19`, `mm10`}. Governs three coupled
  resources at once (see `references/genome_registry.md`). A mismatch silently corrupts TSS
  enrichment, blacklist filtering, and gene activity — always confirm the build.
- **Per-barcode singlecell CSV** (`singlecell.csv` from Cell Ranger) is used when present for
  exact FRiP/blacklist ratios; otherwise these are derived from the fragments.

## Outputs (saved to `project.results_dir`)

- `report_scatac*.pdf` — Phylo-branded report: infographic banner (live metrics), executive
  summary, intro, methods, results (15 figures + tables), conclusions & next steps, references.
- `figures/` — 15 numbered figures (PNG + SVG): 01–04 QC, 05 LSI depth-correlation,
  06 initial UMAP, 07 recalled UMAP, 08–12 annotation, 13 DA heatmap, 14–15 coverage tracks.
- `tables/` — `qc_summary.csv`, `peakset_comparison.csv`, `celltype_composition.csv`,
  `cluster_celltype_assignment.csv` (with confidence flags), `marker_activity_by_cluster.csv`,
  `celltype_zscore_by_cluster.csv`, `top_DA_peaks_per_celltype.csv`, `provenance_manifest.json`.
- `checkpoints/` — resumable `.rds` after each stage (01→05); optional slimmed `final_object.rds`
  if `output.save_rds: true` (metadata + reductions + recalled peak matrix; fragments dropped).
- `provenance_manifest.json` — parameters, package versions (`sessionInfo`), genome build,
  accession, seeds — for reproducibility.

## Environment (install once — see `references/environment.md`)

Not preinstalled in the base Biomni image; install before stage 01:
- **R packages:** `Signac`, `Seurat`, `hdf5r`, plus Bioconductor `EnsDb.Hsapiens.v86` /
  `.v75` / `EnsDb.Mmusculus.v79`, `BSgenome.Hsapiens.UCSC.hg38` / `.hg19` /
  `BSgenome.Mmusculus.UCSC.mm10` matching the build. `hdf5r` is only needed to read `.h5`.
- **MACS3** (Python): `uv pip install macs3` — required for peak recalling; the scripts locate
  it via `Sys.which("macs3")` (override with `peaks.macs_path`).
- **Report:** Python `reportlab`, `pypdf`, `pillow`, `pandas`, `pyyaml`, `matplotlib`.
- `presto` (R) is recommended — it makes the Wilcoxon differential-accessibility test fast.

## Report Packaging

When a PDF report is requested or listed in Outputs, run the analysis first, then
load and use the Biomni `pdf-report-generation` skill for the final PDF
deliverable. Build the PDF from this skill's markdown summary, result tables,
and generated figures, and save it using the PDF filename listed in Outputs, or
a stable descriptive filename when Outputs does not define one.

Keep this skill focused on scientific workflow and artifact content. Do not add
custom figure appearance or report layout instructions here; those are handled
by the platform prompt and dedicated reporting skills. If `pdf-report-generation`
is unavailable, use the packaged markdown/HTML/script fallback when present and
clearly disclose the fallback.

## Workflow

Run the stages in order from `scripts/` (each reads `config.yaml` and writes a checkpoint):

1. **`01_ingest_qc.R`** — Build the Signac ChromatinAssay from the fragments (+ matrix if
   given), attach genome annotations, compute ATAC QC (TSS enrichment, nucleosome signal,
   FRiP, blacklist ratio), write QC figures 01–04 and `qc_summary.csv`, then filter cells on
   the config thresholds. *Why:* ATAC quality is not captured by RNA-style metrics; TSS
   enrichment and FRiP are the load-bearing filters.
2. **`02_lsi_cluster.R`** — TF-IDF normalize, select top features, run SVD (LSI). Check
   depth correlation (figure 05) and **drop LSI component 1** (it captures sequencing depth),
   then UMAP + graph clustering on the remaining components. *Why:* scATAC data are sparse and
   near-binary; TF-IDF/LSI is the standard reduction and component 1 is almost always a depth
   artifact.
3. **`03_recall_peaks.R`** — Re-call peaks **per cluster** with MACS3 on group-partitioned
   fragments, merge to a non-overlapping set, prune to standard chromosomes, remove blacklist
   regions, requantify a cell × recalled-peak matrix, and repeat TF-IDF/LSI/UMAP/clustering.
   *Why:* aggregate (all-cell) peak calling misses regulatory elements specific to rare
   populations; per-cluster recalling is current best practice and yields a richer feature set.
4. **`04_annotate.R`** — Compute gene activity over a marker panel, z-score per cluster, and
   assign each cluster to the top-scoring marker set. Panel priority: user-supplied markers →
   **CellMarker 2.0** tissue-adaptive set (filtered to normal cells for `annotation.tissue`) →
   built-in fallback. Emit a **confidence flag** per cluster (low-confidence / ambiguous).
   Optionally transfer labels from an annotated scRNA-seq reference (Seurat anchors) and flag
   disagreements. *Why:* annotation from accessibility is noisier than from expression;
   confidence gating prevents over-trusting weak calls.
5. **`05_diff_access.R`** — Find variable peaks, run a one-vs-rest **Wilcoxon** test per cell
   type (positive only, subsampled per group), annotate each peak with its nearest gene, and
   render the DA heatmap (figure 13) + coverage tracks (figures 14–15). *Why:* differential
   accessibility identifies the cell-type-defining regulatory elements.
6. **`06_figures_tables.R`** — Write `provenance_manifest.json`, optionally save a slimmed
   `.rds`, and copy all figures/tables to `results_dir`.
7. **`make_report.py --config config.yaml`** — Build the Phylo PDF: renders the data-driven
   infographic banner from the tables, assembles all sections, and validates the PDF
   (page count, size, extractable text) before finishing.

A one-shot driver is in `scripts/run_all.sh` (edit the config path at the top).

## Scientific caveats (read before running)

- **Genome build is load-bearing.** The build selects EnsDb + BSgenome + blacklist together
  (`references/genome_registry.md`). Mixing builds (e.g. hg19 fragments with hg38 annotations)
  silently corrupts TSS enrichment, blacklist filtering, and gene activity. Confirm the build
  matches how the fragments were aligned.
- **Always drop LSI component 1** (verify with the DepthCor plot, figure 05). Keeping it lets
  sequencing depth dominate the embedding.
- **Recall peaks per cluster, not once over all cells.** Aggregate calling under-detects
  peaks in rare populations. The MACS3 output directory must exist before `CallPeaks`.
- **Restrict gene activity to the marker panel.** Computing gene activity over all ~genes is
  slow and unnecessary; the scripts restrict to panel genes (case-mapped to the annotation).
- **Differential accessibility: use Wilcoxon, not `test.use="LR"` with `latent.vars`.** LR with
  latent variables effectively hangs on ~10^5 peaks. Wilcoxon (accelerated by `presto`) with
  per-group subsampling is fast and appropriate. Never scale all peaks — order the top peaks
  to match the assay's feature order before `ScaleData` for the heatmap.
- **Trust, but flag, annotations.** Report and gate on annotation confidence; a top marker
  z-score below `min_top_zscore` (default ~1.0) is a weak call to review, not a fact. (In the
  reference PBMC run, one cluster mapped to dendritic cells at z≈0.97 — a deliberately kept
  cautionary example.) CellMarker 2.0 markers are tissue-specific: set `annotation.tissue`
  correctly, or the panel will be wrong for the sample.
- **Coverage plots** are rendered without the RNA-expression side panel on purpose (it renders
  cramped and unreadable); pass loci via `diffaccess.coverage_genes`.

## Resource notes

- **CellMarker 2.0** lives in the Biomni datalake at `/mnt/datalake/cellmarker2/`
  (`Cell_marker_Human.xlsx`, `Cell_marker_Mouse.xlsx`). Stage 04 filters to
  `cell_type == "Normal cell"` and the configured tissue, groups official gene `Symbol` by
  `cell_name`, and caps set size — no hardcoded tissue panel.
- **MACS3** is invoked through Signac's `CallPeaks`; the pipeline does not shell out directly.
- Method references (Signac, MACS, Cusanovich/LSI, Seurat label transfer, CellMarker 2.0,
  TF-IDF) are curated in `references/citations.md` with verified DOIs and are embedded in the
  report; if the workflow changes, re-verify via `LiteratureSearch` and update both together.
- See `references/parameters.md` for every config knob and its default/rationale.

## Files

```
scatac-multiome-analysis/
  SKILL.md
  scripts/
    config.example.yaml     # full parameter surface (copy to config.yaml)
    _common.R               # config loader, genome registry, helpers
    01_ingest_qc.R          # ingest + ATAC QC + filter
    02_lsi_cluster.R        # TF-IDF/LSI + initial clustering
    03_recall_peaks.R       # per-cluster MACS3 recall + requantify + recluster
    04_annotate.R           # gene-activity annotation + confidence + label transfer
    05_diff_access.R         # differential accessibility + heatmap + coverage
    06_figures_tables.R      # provenance manifest + slimmed rds + copy to results
    make_report.py          # Phylo PDF (infographic + all sections + references)
    run_all.sh              # one-shot driver
  references/
    genome_registry.md      # hg38/hg19/mm10 -> EnsDb + BSgenome + blacklist
    parameters.md           # every config parameter + default + rationale
    environment.md          # install steps + known environment gotchas
    citations.md            # curated method references (verified DOIs)
```
