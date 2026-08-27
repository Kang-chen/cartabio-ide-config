#!/usr/bin/env Rscript
# ============================================================================
# STAGE 01 — Ingest (fragments-first) + QC + filter
# Reads CONFIG. Writes checkpoints/01_qc.rds + QC figures/tables.
#   CONFIG=config.yaml Rscript scripts/01_ingest_qc.R
# ============================================================================
suppressPackageStartupMessages({
  library(Signac); library(Seurat); library(GenomicRanges); library(ggplot2); library(dplyr)
})
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
cfg <- load_config(); ensure_dirs(cfg); set.seed(cfg$project$seed)

gen <- resolve_genome(cfg$genome$build)
frags <- cfg$input$fragments
stopifnot(file.exists(frags))
if (!file.exists(paste0(frags, ".tbi")))
  log_msg("WARNING: no .tbi index next to fragments; Signac may need to index it.")

# ---------------------------------------------------------------------------
# Build the initial feature x cell matrix.
#  (a) 10x peak matrix provided  -> use it (fast path + enables vendor comparison)
#  (b) fragments only            -> call an initial peak set with MACS3, quantify
# ---------------------------------------------------------------------------
get_cells <- function() {
  if (!is.null(cfg$input$cell_barcodes) && file.exists(cfg$input$cell_barcodes))
    return(readLines(cfg$input$cell_barcodes))
  NULL
}

if (!is.null(cfg$input$peak_matrix_h5) && file.exists(cfg$input$peak_matrix_h5)) {
  log_msg("Ingest path (a): 10x peak matrix")
  if (!requireNamespace("hdf5r", quietly = TRUE))
    stop("hdf5r required to read peak_matrix_h5. Install via `conda install -c conda-forge r-hdf5r` ",
         "(see references/environment.md), or omit peak_matrix_h5 to use the fragments-only path.")
  counts <- Read10X_h5(cfg$input$peak_matrix_h5)
  if (is.list(counts)) counts <- counts[["Peaks"]] %||% counts[[1]]
  meta <- if (!is.null(cfg$input$singlecell_csv) && file.exists(cfg$input$singlecell_csv))
            read.csv(cfg$input$singlecell_csv, header = TRUE, row.names = 1) else NULL
  chrom_assay <- CreateChromatinAssay(counts = counts, sep = c(":", "-"),
                                      genome = cfg$genome$build, fragments = frags,
                                      min.cells = 10, min.features = 200)
  obj <- CreateSeuratObject(counts = chrom_assay, assay = "peaks", meta.data = meta)
  vendor_n <- nrow(counts)
} else {
  log_msg("Ingest path (b): fragments-only -> initial MACS3 peaks")
  cells <- get_cells()
  if (is.null(cells)) {
    log_msg("No cell list; deriving candidate cells by fragment-count knee")
    fc <- CountFragments(frags)                 # barcode fragment counts
    fc <- fc[order(-fc$frequency_count), ]
    # simple knee: keep barcodes above the log-count inflection (documented heuristic)
    y <- log10(fc$frequency_count + 1); x <- seq_along(y)
    d2 <- c(NA, diff(diff(y)), NA); knee <- which.min(d2)
    knee <- max(knee, 500)                       # floor to avoid degenerate tiny sets
    cells <- fc$CB[seq_len(min(knee, nrow(fc)))]
    log_msg("Knee kept", length(cells), "candidate barcodes")
  }
  macs <- if (identical(cfg$peaks$macs_path, "auto")) unname(Sys.which("macs3")) else cfg$peaks$macs_path
  stopifnot(nzchar(macs))
  gsize <- if (identical(cfg$peaks$effective_genome_size, "auto")) gen$macs_gsize else cfg$peaks$effective_genome_size
  init_peaks <- CallPeaks(frags, macs2.path = macs, effective.genome.size = gsize,
                          outdir = file.path(cfg$project$outdir, "macs"))
  init_peaks <- keepStandardChromosomes(init_peaks, pruning.mode = "coarse")
  init_peaks <- subsetByOverlaps(init_peaks, gen$blacklist, invert = TRUE)
  fragobj <- CreateFragmentObject(path = frags, cells = cells)
  counts <- FeatureMatrix(fragments = fragobj, features = init_peaks, cells = cells)
  chrom_assay <- CreateChromatinAssay(counts = counts, genome = cfg$genome$build,
                                      fragments = fragobj, min.cells = 10, min.features = 200)
  obj <- CreateSeuratObject(counts = chrom_assay, assay = "peaks")
  vendor_n <- nrow(counts)
}
log_msg("Initial object:", ncol(obj), "cells x", nrow(obj), "peaks")

# keep standard chromosomes + attach annotation (UCSC-style already)
main.chroms <- GenomeInfoDb::standardChromosomes(gen$bsgenome)
keep <- as.character(seqnames(granges(obj))) %in% main.chroms
obj <- obj[keep, ]
Annotation(obj) <- gen$annotations
vendor_std_n <- nrow(obj)
log_msg("After standard-chrom filter:", vendor_std_n, "peaks")

# ---------------------------------------------------------------------------
# QC metrics
# ---------------------------------------------------------------------------
log_msg("QC: nucleosome signal + TSS enrichment (fast=FALSE for TSS plot)")
obj <- NucleosomeSignal(obj)
obj <- TSSEnrichment(obj, fast = FALSE)

# FRiP + blacklist: prefer 10x per-barcode columns; else compute from fragments
md <- obj@meta.data
if (all(c("peak_region_fragments", "passed_filters") %in% colnames(md))) {
  obj$pct_reads_in_peaks <- md$peak_region_fragments / md$passed_filters * 100
  if ("blacklist_region_fragments" %in% colnames(md))
    obj$blacklist_ratio <- md$blacklist_region_fragments / md$peak_region_fragments
} else {
  obj$pct_reads_in_peaks <- FRiP(obj, assay = "peaks", total.fragments = "nCount_peaks")$FRiP * 100
}
if (is.null(obj$blacklist_ratio)) {
  obj$blacklist_ratio <- FractionCountsInRegion(obj, assay = "peaks", regions = gen$blacklist)
}

# ---------------------------------------------------------------------------
# QC figures (pre-filter)
# ---------------------------------------------------------------------------
qc_cols <- intersect(c("nCount_peaks","TSS.enrichment","nucleosome_signal",
                       "pct_reads_in_peaks","blacklist_ratio"), colnames(obj@meta.data))
save_fig(cfg, VlnPlot(obj, features = qc_cols, pt.size = 0, ncol = length(qc_cols)) & plot_theme(),
         "01_qc_violin_prefilter", 3 * length(qc_cols), 4)

obj$high.tss <- ifelse(obj$TSS.enrichment > cfg$qc$min_tss, "High", "Low")
save_fig(cfg, TSSPlot(obj, group.by = "high.tss") + NoLegend() + plot_theme(),
         "02_tss_enrichment", 6, 4)

obj$nuc.group <- ifelse(obj$nucleosome_signal > cfg$qc$max_nucleosome, "high_NS", "low_NS")
save_fig(cfg, FragmentHistogram(obj, group.by = "nuc.group") + plot_theme(),
         "03_fragment_histogram", 7, 4)

save_fig(cfg, DensityScatter(obj, x = "nCount_peaks", y = "TSS.enrichment",
                             log_x = TRUE, quantiles = TRUE) & plot_theme(),
         "04_qc_scatter_count_tss", 6.5, 5)

# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------
n_before <- ncol(obj)
q <- cfg$qc
keep_cells <- with(obj@meta.data,
  nCount_peaks > q$min_count & nCount_peaks < q$max_count &
  pct_reads_in_peaks > q$min_frip & nucleosome_signal < q$max_nucleosome &
  TSS.enrichment > q$min_tss & blacklist_ratio < q$max_blacklist)
obj <- obj[, which(keep_cells)]
n_after <- ncol(obj)
log_msg("QC filter:", n_before, "->", n_after, "cells (", n_before - n_after, "removed )")

qc_summary <- data.frame(metric = c("cells_before_QC","cells_after_QC","cells_removed",
  "median_nCount","median_TSS","median_FRiP","median_nucleosome",
  "vendor_peaks_raw","vendor_peaks_std_chrom",
  "min_count","max_count","min_FRiP","max_nucleosome","min_TSS","max_blacklist"),
  value = c(n_before, n_after, n_before - n_after,
    round(median(obj$nCount_peaks)), round(median(obj$TSS.enrichment), 2),
    round(median(obj$pct_reads_in_peaks), 1), round(median(obj$nucleosome_signal), 2),
    vendor_n, vendor_std_n,
    q$min_count, q$max_count, q$min_frip, q$max_nucleosome, q$min_tss, q$max_blacklist))
write.csv(qc_summary, tabp(cfg, "qc_summary.csv"), row.names = FALSE)

saveRDS(list(obj = obj, genome = gen, vendor_n = vendor_n, vendor_std_n = vendor_std_n),
        ckpt(cfg, "01_qc.rds"))
log_msg("STAGE 01 done -> checkpoints/01_qc.rds"); cat("STAGE01_OK\n")
