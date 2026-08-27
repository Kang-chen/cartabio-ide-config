#!/usr/bin/env Rscript
# ============================================================================
# STAGE 02 — TF-IDF / LSI / UMAP / initial clustering
# Loads checkpoints/01_qc.rds. Writes checkpoints/02_initial.rds + figs 05-06.
# ============================================================================
suppressPackageStartupMessages({
  library(Signac); library(Seurat); library(ggplot2)
})
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
cfg <- load_config(); set.seed(cfg$project$seed)

st <- readRDS(ckpt(cfg, "01_qc.rds")); obj <- st$obj
DefaultAssay(obj) <- "peaks"

log_msg("TF-IDF -> FindTopFeatures(", cfg$dimreduc$min_cutoff, ") -> RunSVD")
obj <- RunTFIDF(obj)
obj <- FindTopFeatures(obj, min.cutoff = cfg$dimreduc$min_cutoff)
obj <- RunSVD(obj)

# LSI component 1 is typically depth-driven -> show DepthCor, then drop it
save_fig(cfg, DepthCor(obj) & plot_theme(), "05_lsi_depthcor", 6, 4)
dims <- cfg$dimreduc$lsi_dims_vec
log_msg("UMAP + clustering on LSI dims", paste(range(dims), collapse = ":"))

obj <- RunUMAP(obj, reduction = "lsi", dims = dims)
obj <- FindNeighbors(obj, reduction = "lsi", dims = dims)
obj <- FindClusters(obj, algorithm = cfg$dimreduc$cluster_algorithm,
                    resolution = cfg$dimreduc$resolution_initial, verbose = FALSE)
n_init <- nlevels(obj$seurat_clusters)
log_msg("Initial clusters:", n_init)

save_fig(cfg, DimPlot(obj, label = TRUE, repel = TRUE) +
           scale_color_manual(values = clpal(n_init)) + NoLegend() +
           ggtitle(paste0("Initial clusters (n=", n_init, ", res=",
                          cfg$dimreduc$resolution_initial, ")")) & plot_theme(),
         "06_umap_initial_clusters", 6.5, 5.5)

st$obj <- obj; st$n_init <- n_init
saveRDS(st, ckpt(cfg, "02_initial.rds"))
log_msg("STAGE 02 done -> checkpoints/02_initial.rds"); cat("STAGE02_OK\n")
