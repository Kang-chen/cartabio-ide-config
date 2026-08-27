#!/usr/bin/env Rscript
# ============================================================================
# STAGE 05 — Differential accessibility (fast Wilcoxon) + heatmap + coverage
# Loads checkpoints/04_annotated.rds. Writes checkpoints/05_final.rds,
# top_DA_peaks_per_celltype table, figs 13 (tile heatmap) + 14/15 (coverage).
# ============================================================================
suppressPackageStartupMessages({
  library(Signac); library(Seurat); library(GenomicRanges); library(ggplot2); library(dplyr); library(tidyr)
})
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
cfg <- load_config(); set.seed(cfg$project$seed)

st <- readRDS(ckpt(cfg, "04_annotated.rds")); obj <- st$obj
DefaultAssay(obj) <- "recalled"; Idents(obj) <- "cell_type"

# ---- fast DA: variable peaks + Wilcoxon + per-ident subsample ---------------
log_msg("DA peaks: FindTopFeatures(", cfg$diffaccess$var_features_cutoff,
        ") + Wilcoxon (subsample", cfg$diffaccess$max_cells_per_ident, "cells/ident)")
obj <- FindTopFeatures(obj, min.cutoff = cfg$diffaccess$var_features_cutoff)
vf <- VariableFeatures(obj)
da <- FindAllMarkers(obj, features = vf, only.pos = cfg$diffaccess$only_pos,
                     min.pct = cfg$diffaccess$min_pct, test.use = cfg$diffaccess$test,
                     max.cells.per.ident = cfg$diffaccess$max_cells_per_ident, verbose = FALSE)
if (nrow(da) > 0) {
  cf <- ClosestFeature(obj, regions = da$gene)
  da$closest_gene <- cf$gene_name[match(da$gene, cf$query_region)]
  da$distance <- cf$distance[match(da$gene, cf$query_region)]
  write.csv(da, tabp(cfg, "top_DA_peaks_per_celltype.csv"), row.names = FALSE)
  log_msg("DA peaks found:", nrow(da), "across", length(unique(da$cluster)), "cell types")
} else log_msg("WARNING: no DA peaks passed thresholds")

# ---- clean per-cell-type averaged z-score tile heatmap (fig 13) -------------
if (nrow(da) > 0) {
  ct_levels <- names(sort(table(obj$cell_type), decreasing = TRUE))
  topN <- cfg$diffaccess$top_n_per_type
  top <- da %>% mutate(cluster = factor(cluster, levels = ct_levels[ct_levels %in% unique(cluster)])) %>%
    group_by(cluster) %>% arrange(p_val_adj, .by_group = TRUE) %>%
    slice_head(n = topN) %>% ungroup() %>% arrange(cluster)
  peaks <- top$gene
  avg <- AverageExpression(obj, assays = "recalled", features = peaks,
                           group.by = "cell_type", slot = "data")$recalled
  avg <- avg[peaks, ct_levels, drop = FALSE]
  zz <- t(scale(t(avg))); zz[is.na(zz)] <- 0; zz[zz > 2.5] <- 2.5; zz[zz < -2.5] <- -2.5
  lab <- top$closest_gene; lab[is.na(lab) | lab == ""] <- top$gene[is.na(lab) | lab == ""]
  rowlab <- make.unique(lab); rownames(zz) <- rowlab
  df <- as.data.frame(zz) %>% tibble::rownames_to_column("peak") %>%
    pivot_longer(-peak, names_to = "cell_type", values_to = "z")
  df$peak <- factor(df$peak, levels = rev(rowlab))
  df$cell_type <- factor(df$cell_type, levels = ct_levels)
  p_hm <- ggplot(df, aes(cell_type, peak, fill = z)) + geom_tile() +
    scale_fill_gradient2(low = "#2166AC", mid = "white", high = "#B2182B", midpoint = 0,
                         name = "Row z-score\n(accessibility)", limits = c(-2.5, 2.5)) +
    labs(x = "Cell type", y = "Top DA peaks (closest gene)",
         title = "Top cell-type-specific accessible peaks") +
    plot_theme() + theme(axis.text.x = element_text(angle = 40, hjust = 1),
                         axis.text.y = element_text(size = 7), panel.grid = element_blank(),
                         plot.title = element_text(face = "bold"))
  save_fig(cfg, p_hm, "13_DA_peak_heatmap", 8.5, max(6, 0.18 * length(peaks) + 2))
}

# ---- coverage plots at configured loci (figs 14/15) -------------------------
cov_genes <- cfg$diffaccess$coverage_genes
fig_ids <- c("14_coverage", "15_coverage")
for (i in seq_along(cov_genes)) {
  g <- cov_genes[[i]]
  nm <- if (i <= 2) paste0(fig_ids[i], "_", g) else paste0("coverage_", g)
  tryCatch({
    pc <- CoveragePlot(obj, region = g, assay = "recalled",
                       extend.upstream = 2000, extend.downstream = 2000)
    save_fig(cfg, pc, nm, 8, 6); log_msg("Coverage", g, "done")
  }, error = function(e) log_msg("Coverage", g, "failed:", conditionMessage(e)))
}

st$obj <- obj; st$da <- da
saveRDS(st, ckpt(cfg, "05_final.rds"))
log_msg("STAGE 05 done -> checkpoints/05_final.rds"); cat("STAGE05_OK\n")
