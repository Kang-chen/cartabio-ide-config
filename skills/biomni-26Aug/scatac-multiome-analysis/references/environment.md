# Environment setup & gotchas

Hard-won notes from building this pipeline. Reading this first saves the most time.

## Compute sizing

- Right-size a **~64 GB** machine via `ManageMachine` before running. The 10k-PBMC
  run (~2.5 GB fragments, ~10k cells, ~180k recalled peaks) fits comfortably in 64 GB;
  scale up for more cells/peaks or multiome.
- Slow stages (tens of minutes each): **TSS enrichment** (`fast=FALSE`), **per-cluster
  MACS3 CallPeaks**, and **FeatureMatrix** requantification. Budget ~1 hr end-to-end
  for a 10k-cell dataset; more for larger.
- `nproc` reports **1 at idle** under autoscaling — this is normal; cores scale up
  under load. Don't hardcode thread counts off an idle `nproc`.

## Package installation

### R packages (Signac, Seurat, annotation)
- Install into the persistent library so they survive machine restarts:
  `.libPaths("/workspace/.Rlib")` (create it once). Signac, Seurat, GenomicRanges,
  BiocManager are typically present; **Signac may need compiling on first install**.
- Genome annotation packages install from Bioconductor on first use and are the
  **#1 environment cost**: `EnsDb.Hsapiens.v86`, `BSgenome.Hsapiens.UCSC.hg38`
  (and hg19/mm10 equivalents), plus `biovizBase`, `GenomeInfoDb`. Install these in
  a background step before the run and wait for completion.
- `presto` (fast Wilcoxon) via `remotes::install_github("immunogenomics/presto")`.

### hdf5r — the classic trap
- Only needed if you read a 10x `*_peak_bc_matrix.h5` (fragments-only runs skip it).
- `install.packages("hdf5r")` **fails to compile** without system HDF5.
- Fix: `conda install -y -c conda-forge r-hdf5r` (pulls `hdf5` + `r-hdf5r`).
- **Caveat:** conda installs land outside `/workspace` and do **NOT** persist across
  machine restarts — reinstall after a restart, or convert the h5 to a fragments-only
  workflow (recommended for portability).

### MACS3 (peak caller)
- `uv pip install --system MACS3` (installs `macs3` on PATH). Confirm with
  `Sys.which("macs3")`; pass that path to `CallPeaks(macs2.path=...)`.
- `CallPeaks` requires the **`outdir` to already exist** — `dir.create(macs_dir,
  recursive=TRUE)` first, or it errors `Requested output directory does not exist`.

## Filesystem rules (S3-backed mounts)

- **Write random-access formats to `/workspace` (local disk), then copy to
  `/mnt/results`.** Affected here: `.rds`, `.h5`. Direct writes of these to the
  S3-backed `/mnt/results` or `/mnt/shared-workspace` fail or corrupt.
- **Use shell `cp`, not R `file.copy()`, to move files to `/mnt/results`** — R's
  `file.copy()` produces **0-byte files** on the FUSE mount. From R use
  `system2("cp", c(src, dst))` or do the copy in a Bash step.
- CSV / PNG / SVG / JSON write directly to `/mnt/results` fine.

## Checkpointing (resume without recomputation)

Save an `.rds` checkpoint **after each expensive stage and BEFORE any figure block**
(a figure crash otherwise loses the whole stage). Stage checkpoints used here:
`01_qc.rds` → `02_initial.rds` → `03_recalled.rds` → `04_annotated.rds` →
`05_final.rds`. Each script loads the previous checkpoint, so any failure resumes
from the last good stage. This turned a fragile multi-hour run into recoverable
~20-min stages.

## Biomni resources this skill uses

- **MACS3** — per-cluster peak calling (installed via uv).
- **Signac / Seurat** + Bioconductor annotation — the R analysis stack.
- **CellMarker2** datalake — tissue-appropriate marker panels (annotation).
- **`pdf-report-generation` skill** — Phylo-branded ReportLab PDF + infographic.
- **`LiteratureSearch`** (optional) — method/marker references for the report.
- **Direct checks** — confirm packages with imports.
- HPC cluster is **not** required; the whole pipeline runs in a sandbox machine.
