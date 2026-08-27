#!/usr/bin/env Rscript
# =====================================================================================
# 02_cluster.R  --  Automated gating via unsupervised clustering.
#
# FlowSOM self-organizing map (xdim x ydim nodes) -> ConsensusClusterPlus metaclustering.
# Resolution is chosen from the ConsensusClusterPlus DELTA-AREA plateau (elbow), NOT from a
# known number of populations -- because in the wild you do not have that oracle. The chosen
# resolution is exposed via --k so an analyst can override after inspecting the delta-area plot.
#
# Usage:
#   Rscript 02_cluster.R --sce <outdir>/sce_prepped.rds --outdir <outdir> \
#     --xdim 10 --ydim 10 --maxK 20 --k auto --seed 1234
#   # --k auto  -> pick meta-k at the delta-area elbow; or --k meta12 to force.
# Output: <outdir>/sce_clustered.rds, delta-area figure, chosen_k recorded in metadata.
# =====================================================================================

suppressPackageStartupMessages({
  library(optparse); library(CATALYST); library(SingleCellExperiment); library(ggplot2)
})

opt <- parse_args(OptionParser(option_list = list(
  make_option("--sce", type = "character"),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--xdim", type = "integer", default = 10L),
  make_option("--ydim", type = "integer", default = 10L),
  make_option("--maxK", type = "integer", default = 20L),
  make_option("--k", type = "character", default = "auto"),
  make_option("--seed", type = "integer", default = 1234L)
)))
set.seed(opt$seed)
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
logmsg("=== 02_cluster.R  |  seed=%d ===", opt$seed)

sce <- readRDS(opt$sce)

# ------------------------------------------------------------------ cluster
# features="type" uses only lineage markers (marker_class == "type").
t0 <- Sys.time()
sce <- CATALYST::cluster(sce, features = "type",
                         xdim = opt$xdim, ydim = opt$ydim, maxK = opt$maxK,
                         verbose = FALSE, seed = opt$seed)
logmsg("FlowSOM %dx%d (%d SOM nodes) + ConsensusClusterPlus maxK=%d in %.1f min",
       opt$xdim, opt$ydim, opt$xdim * opt$ydim, opt$maxK, as.numeric(difftime(Sys.time(), t0, units = "mins")))
# SUBSAMPLING POLICY: population discovery uses EVERY post-QC event. FlowSOM trains the SOM on all
# cells; there is no per-well subsampling in the analysis path, so rare/mixed populations are not
# thinned. (The only subsample in the pipeline is the UMAP/t-SNE embedding in 04 -- visualization only.)
logmsg("Clustering used ALL %d post-QC cells (no subsampling; %d cells/sample min..max %d).",
       ncol(sce), min(table(sce$sample_id)), max(table(sce$sample_id)))

# ------------------------------------------------------------------ delta-area -> resolution
# CATALYST stores ConsensusClusterPlus output in metadata(sce)$delta_area (a ggplot) and the
# consensus objects; we recompute the delta-area values to locate the elbow programmatically.
ccp <- S4Vectors::metadata(sce)$cluster_codes
# delta-area diagnostic figure (CATALYST helper)
da_plot <- tryCatch(CATALYST::delta_area(sce), error = function(e) NULL)
if (!is.null(da_plot)) {
  da_plot <- da_plot + ggplot2::theme(text = ggplot2::element_text(family = "Liberation Sans"))
  ggsave(file.path(figdir, "fig_delta_area.png"), da_plot, width = 6, height = 4, dpi = 150)
  ggsave(file.path(figdir, "fig_delta_area.svg"), da_plot, width = 6, height = 4)
}

# Programmatic elbow: pull relative change in consensus-CDF area if available; else fall back
# to the largest drop in incremental delta area.
pick_k_from_delta <- function(sce, maxK) {
  # Try to reconstruct delta-area y-values from the stored plot data.
  d <- tryCatch(ggplot2::ggplot_build(CATALYST::delta_area(sce))$data[[1]], error = function(e) NULL)
  if (is.null(d) || !all(c("x", "y") %in% names(d))) return(NA_integer_)
  x <- d$x; y <- d$y
  # elbow = last k where incremental gain in area still exceeds 10% of the max gain
  gain <- c(NA, diff(y))
  thr <- 0.1 * max(gain, na.rm = TRUE)
  cand <- x[which(gain >= thr)]
  k <- if (length(cand)) max(cand) else which.max(y)
  as.integer(max(2, min(k, maxK)))
}

if (opt$k == "auto") {
  k_elbow <- pick_k_from_delta(sce, opt$maxK)
  if (is.na(k_elbow)) { k_elbow <- min(12L, opt$maxK); logmsg("Delta-area elbow not resolvable; defaulting to meta%d.", k_elbow) }
  chosen_k <- paste0("meta", k_elbow)
  logmsg("Resolution chosen from delta-area elbow: %s (override with --k).", chosen_k)
} else {
  chosen_k <- if (grepl("^meta", opt$k)) opt$k else paste0("meta", opt$k)
  logmsg("Resolution set by user: %s", chosen_k)
}

# sanity: chosen_k must be an available metaclustering level
avail <- colnames(S4Vectors::metadata(sce)$cluster_codes)
if (!chosen_k %in% avail) stop(sprintf("chosen_k '%s' not in available levels: %s", chosen_k, paste(avail, collapse = ", ")))
n_clusters <- length(unique(S4Vectors::metadata(sce)$cluster_codes[[chosen_k]]))
logmsg("Chosen resolution %s -> %d metaclusters (of 2..%d available).", chosen_k, n_clusters, opt$maxK)

S4Vectors::metadata(sce)$chosen_k <- chosen_k
saveRDS(sce, file.path(opt$outdir, "sce_clustered.rds"))
logmsg("Saved: %s", file.path(opt$outdir, "sce_clustered.rds"))
logmsg("REMINDER: inspect fig_delta_area before trusting the auto elbow; then consider the")
logmsg("          two-tier strategy (annotate coarse, subcluster merged lineages). See references.")
logmsg("=== 02 complete ===")
