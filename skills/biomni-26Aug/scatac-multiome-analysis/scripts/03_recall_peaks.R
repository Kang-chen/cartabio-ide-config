#!/usr/bin/env Rscript
# ============================================================================
# STAGE 03 — Per-cluster MACS3 peak recalling -> requantify -> recluster
# Loads checkpoints/02_initial.rds. Writes checkpoints/03_recalled.rds,
# recalled peakset, peakset_comparison table, fig 07.
# ============================================================================
suppressPackageStartupMessages({
  library(Signac); library(Seurat); library(GenomicRanges); library(ggplot2)
})
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
cfg <- load_config(); set.seed(cfg$project$seed)

st <- readRDS(ckpt(cfg, "02_initial.rds")); obj <- st$obj; gen <- st$genome
DefaultAssay(obj) <- "peaks"

macs <- if (identical(cfg$peaks$macs_path, "auto")) unname(Sys.which("macs3")) else cfg$peaks$macs_path
if (!nzchar(macs)) stop("macs3 not found. `uv pip install --system MACS3` (see references/environment.md).")
gsize <- if (identical(cfg$peaks$effective_genome_size, "auto")) gen$macs_gsize else cfg$peaks$effective_genome_size
macs_dir <- file.path(cfg$project$outdir, "macs"); dir.create(macs_dir, recursive = TRUE, showWarnings = FALSE)

log_msg("Per-cluster MACS3 CallPeaks (group.by=", cfg$peaks$group_by, ", -g", gsize, ")")
peaks <- CallPeaks(obj, group.by = cfg$peaks$group_by, macs2.path = macs,
                   effective.genome.size = gsize, outdir = macs_dir, cleanup = FALSE)
peaks <- keepStandardChromosomes(peaks, pruning.mode = "coarse")
peaks <- subsetByOverlaps(peaks, gen$blacklist, invert = TRUE)
n_recalled <- length(peaks)
log_msg("Recalled merged peaks:", n_recalled, "(vs", st$vendor_std_n, "vendor std-chrom)")
saveRDS(peaks, ckpt(cfg, "recalled_peaks.rds"))

# requantify cells x recalled peaks
log_msg("FeatureMatrix over recalled peaks (slow: ~minutes)")
mat <- FeatureMatrix(fragments = Fragments(obj), features = peaks, cells = colnames(obj))
recalled <- CreateChromatinAssay(counts = mat, fragments = Fragments(obj),
                                 genome = cfg$genome$build, annotation = Annotation(obj))
obj[["recalled"]] <- recalled
DefaultAssay(obj) <- "recalled"

# repeat reduction/clustering on recalled matrix
log_msg("Re-run TF-IDF/LSI/UMAP/clusters on recalled matrix")
obj <- RunTFIDF(obj)
obj <- FindTopFeatures(obj, min.cutoff = cfg$dimreduc$min_cutoff)
obj <- RunSVD(obj)
dims <- cfg$dimreduc$lsi_dims_vec
obj <- RunUMAP(obj, reduction = "lsi", dims = dims)
obj <- FindNeighbors(obj, reduction = "lsi", dims = dims)
obj <- FindClusters(obj, algorithm = cfg$dimreduc$cluster_algorithm,
                    resolution = cfg$dimreduc$resolution_final, verbose = FALSE)
n_final <- nlevels(obj$seurat_clusters)
log_msg("Final clusters (recalled):", n_final)

save_fig(cfg, DimPlot(obj, label = TRUE, repel = TRUE) +
           scale_color_manual(values = clpal(n_final)) + NoLegend() +
           ggtitle(paste0("Clusters on recalled peaks (n=", n_final, ")")) & plot_theme(),
         "07_umap_recalled_clusters", 6.5, 5.5)

write.csv(data.frame(
  peak_set = c("Vendor/initial (std chrom)", "MACS3 per-cluster recalled"),
  n_peaks  = c(st$vendor_std_n, n_recalled)),
  tabp(cfg, "peakset_comparison.csv"), row.names = FALSE)

st$obj <- obj; st$n_recalled <- n_recalled; st$n_final <- n_final
saveRDS(st, ckpt(cfg, "03_recalled.rds"))
log_msg("STAGE 03 done -> checkpoints/03_recalled.rds"); cat("STAGE03_OK\n")
