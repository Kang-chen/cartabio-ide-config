#!/usr/bin/env Rscript
# =====================================================================================
# 04_quantify_dr.R  --  Cross-sample abundance quantification + dimensionality reduction.
#
#   - UMAP (+ tSNE cross-check) on a BALANCED subsample (default 10,000 cells/sample, capped at the
#     smallest sample's size) for speed/clarity. This affects the DR *visualization only* — clustering
#     and abundance/% use ALL cells.
#   - Population counts and % per sample.
#   - Cluster-by-sample relative-frequency heatmap.
#
# Usage:
#   Rscript 04_quantify_dr.R --sce <outdir>/sce_annotated.rds --outdir <outdir> \
#     --cells-per-sample 10000 --dr both --seed 1601
# Output: abundance_by_sample.csv, UMAP/tSNE figures, freq heatmap, sce_dr.rds
# =====================================================================================

suppressPackageStartupMessages({
  library(optparse); library(CATALYST); library(SingleCellExperiment)
  library(ggplot2); library(ggrepel); library(ComplexHeatmap); library(grid)
})

opt <- parse_args(OptionParser(option_list = list(
  make_option("--sce", type = "character"),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--cells_per_sample", type = "integer", default = 10000L, dest = "cps"),
  make_option("--dr", type = "character", default = "both"),   # umap | tsne | both
  make_option("--seed", type = "integer", default = 1601L)
)))
set.seed(opt$seed)
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, recursive = TRUE, showWarnings = FALSE)
tabdir <- file.path(opt$outdir, "tables"); dir.create(tabdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
logmsg("=== 04_quantify_dr.R  |  seed=%d ===", opt$seed)

sce <- readRDS(opt$sce)
ann <- CATALYST::cluster_ids(sce, "annotation")
samp <- sce$sample_id

# ------------------------------------------------------------------ abundance per sample
tab <- table(population = ann, sample = samp)
props <- sweep(tab, 2, colSums(tab), "/") * 100
ab <- data.frame(population = rownames(tab), stringsAsFactors = FALSE)
for (s in colnames(tab)) { ab[[paste0(s, "_n")]] <- as.integer(tab[, s]); ab[[paste0(s, "_pct")]] <- round(props[, s], 3) }
ab$Total_n <- rowSums(tab); ab$Overall_pct <- round(rowSums(tab) / sum(tab) * 100, 3)
ab <- ab[order(-ab$Total_n), ]
write.csv(ab, file.path(tabdir, "abundance_by_sample.csv"), row.names = FALSE)
logmsg("Wrote abundance_by_sample.csv (%d populations x %d samples)", nrow(ab), ncol(tab))
logmsg("Abundance counts/%% computed on ALL %d post-QC cells (no subsampling; every event assigned).", ncol(sce))

# abundance barplot (% per sample)
long <- do.call(rbind, lapply(colnames(tab), function(s)
  data.frame(population = rownames(tab), sample = s, pct = props[, s])))
long$population <- factor(long$population, levels = ab$population)
pal <- c("#E6194B","#3CB44B","#4363D8","#F58231","#911EB4","#42D4F4","#F032E6","#BFEF45",
         "#FABED4","#469990","#9A6324","#800000","#000075","#A9A9A9","#808000","#FFD8B1")
pbar <- ggplot(long, aes(population, pct, fill = sample)) +
  geom_col(position = "dodge") + coord_flip() +
  labs(x = NULL, y = "% of sample", title = "Population abundance per sample") +
  theme_bw(base_size = 11) + theme(text = element_text(family = "Liberation Sans"))
ggsave(file.path(figdir, "fig_abundance_barplot.png"), pbar, width = 7, height = 5, dpi = 150)
ggsave(file.path(figdir, "fig_abundance_barplot.svg"), pbar, width = 7, height = 5)

# cluster-by-sample frequency heatmap (z across samples per population)
zf <- t(scale(t(props)))
zf[!is.finite(zf)] <- 0
ht <- ComplexHeatmap::Heatmap(zf, name = "z(freq)",
  column_title = "Relative population frequency across samples",
  row_names_gp = grid::gpar(fontsize = 8), column_names_gp = grid::gpar(fontsize = 8),
  right_annotation = ComplexHeatmap::rowAnnotation(n = ComplexHeatmap::anno_barplot(rowSums(tab))))
png(file.path(figdir, "fig_freq_heatmap.png"), width = 1400, height = 1200, res = 170)
ComplexHeatmap::draw(ht); dev.off()

# ------------------------------------------------------------------ dimensionality reduction
# This is the ONLY subsampling stage in the pipeline. It sets EMBEDDING DENSITY for the UMAP/t-SNE
# figures and nothing else -- clustering (02), annotation (03), abundance/% (above), benchmarking (05)
# and differential abundance (06) all use ALL post-QC cells. Raising --cells_per_sample makes the
# plots denser; it does NOT change any count, frequency, cluster assignment, or statistic.
n_per <- min(opt$cps, min(table(samp)))
if (n_per < opt$cps)
  logmsg("DR embedding subsample capped at %d cells/sample (smallest sample has %d < requested %d).",
         n_per, min(table(samp)), opt$cps)
logmsg("Running DR (UMAP/tSNE) on %d cells/sample across %d samples -- VISUALIZATION ONLY (analysis stages used all cells).",
       n_per, length(unique(samp)))
sce <- CATALYST::runDR(sce, dr = "UMAP", cells = n_per, features = "type")
if (opt$dr %in% c("tsne", "both")) sce <- CATALYST::runDR(sce, dr = "TSNE", cells = n_per, features = "type")

plot_dr <- function(sce, dr, color_by) {
  p <- CATALYST::plotDR(sce, dr, color_by = color_by)
  # centroid labels so color is secondary (dense scatters are hard with many hues)
  d <- p$data
  # Current CATALYST returns coordinate columns named 'x'/'y'; older versions used
  # 'UMAP1'/'TSNE1'/'dim1'. Handle both conventions.
  if (all(c("x", "y") %in% names(d))) {
    xy <- c("x", "y")
  } else {
    xy <- grep("^(UMAP|TSNE|dim)", names(d), value = TRUE, ignore.case = TRUE)[1:2]
  }
  d$.col <- d[[color_by]]
  cent <- aggregate(d[, xy], list(.col = d$.col), median)
  p + ggrepel::geom_label_repel(data = cent, aes(x = .data[[xy[1]]], y = .data[[xy[2]]], label = .col),
        size = 2.6, fill = "white", color = "black", max.overlaps = Inf, seed = 1, inherit.aes = FALSE) +
    ggplot2::guides(color = "none") +
    ggplot2::theme(text = ggplot2::element_text(family = "Liberation Sans"))
}
pu <- plot_dr(sce, "UMAP", "annotation")
ggsave(file.path(figdir, "fig_umap_clusters.png"), pu, width = 6.5, height = 5.5, dpi = 150)
ggsave(file.path(figdir, "fig_umap_clusters.svg"), pu, width = 6.5, height = 5.5)
# UMAP by ground truth if labels exist
if ("population_id" %in% names(SummarizedExperiment::colData(sce))) {
  pg <- plot_dr(sce, "UMAP", "population_id")
  ggsave(file.path(figdir, "fig_umap_groundtruth.png"), pg, width = 6.5, height = 5.5, dpi = 150)
  ggsave(file.path(figdir, "fig_umap_groundtruth.svg"), pg, width = 6.5, height = 5.5)
}
if (opt$dr %in% c("tsne", "both")) {
  pt <- plot_dr(sce, "TSNE", "annotation")
  ggsave(file.path(figdir, "fig_tsne_clusters.png"), pt, width = 6.5, height = 5.5, dpi = 150)
  ggsave(file.path(figdir, "fig_tsne_clusters.svg"), pt, width = 6.5, height = 5.5)
}

saveRDS(sce, file.path(opt$outdir, "sce_dr.rds"))
logmsg("Saved: %s", file.path(opt$outdir, "sce_dr.rds"))
logmsg("=== 04 complete ===")
