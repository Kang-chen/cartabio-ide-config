#!/usr/bin/env Rscript
# ============================================================================
# STAGE 04 — Gene activity + tiered cell-type annotation (confidence-gated)
# Loads checkpoints/03_recalled.rds. Writes checkpoints/04_annotated.rds,
# annotation tables, marker figs 08-10 (+12), composition fig 11.
#
# Tiers: (1) marker gene-activity z-score [always] ->
#        (2) CellMarker2 tissue panel [use_cellmarker2] ->
#        (3) optional Seurat label transfer from an scRNA-seq reference.
# ============================================================================
suppressPackageStartupMessages({
  library(Signac); library(Seurat); library(ggplot2); library(dplyr)
})
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
cfg <- load_config(); set.seed(cfg$project$seed)

st <- readRDS(ckpt(cfg, "03_recalled.rds")); obj <- st$obj
DefaultAssay(obj) <- "recalled"

# ---- built-in PBMC/immune fallback panel -----------------------------------
PBMC_PANEL <- list(
  "CD14 Mono"=c("CD14","LYZ","S100A8","S100A9","CSF3R"),
  "CD16 Mono"=c("FCGR3A","MS4A7","CDKN1C"),
  "CD4 T"=c("IL7R","CD4","CD3D","CD3E","CD3G","TCF7"),
  "CD8 T"=c("CD8A","CD8B","CD3D","GZMK"),
  "NK"=c("GNLY","NKG7","KLRD1","NCAM1","KLRF1"),
  "B"=c("MS4A1","CD79A","CD79B","BANK1","IGHM"),
  "DC"=c("FCER1A","CST3","CLEC9A","IRF8","IL3RA"),
  "Platelet"=c("PPBP","PF4","ITGA2B"))

# ---- build marker sets: custom > CellMarker2(tissue) > PBMC fallback --------
build_marker_sets <- function() {
  # (a) explicit override
  if (!is.null(cfg$annotation$marker_panel) && file.exists(cfg$annotation$marker_panel)) {
    log_msg("Marker panel: custom file", cfg$annotation$marker_panel)
    return(yaml::read_yaml(cfg$annotation$marker_panel))
  }
  # (b) CellMarker2 tissue panel
  if (isTRUE(cfg$annotation$use_cellmarker2) && !is.null(cfg$annotation$tissue)) {
    sp <- if (cfg$genome$build == "mm10") "Mouse" else "Human"
    f <- sprintf("/mnt/datalake/cellmarker2/Cell_marker_%s.xlsx", sp)
    if (file.exists(f) && requireNamespace("readxl", quietly = TRUE)) {
      log_msg("Marker panel: CellMarker2", sp, "tissue =", cfg$annotation$tissue)
      cm <- readxl::read_excel(f)
      tis <- cfg$annotation$tissue
      sub <- cm[!is.na(cm$Symbol) & cm$cell_type == "Normal cell" &
                (grepl(tis, cm$tissue_type, ignore.case = TRUE) |
                 grepl(tis, cm$tissue_class, ignore.case = TRUE)), ]
      if (nrow(sub) > 0) {
        sets <- split(toupper(sub$Symbol), sub$cell_name)
        # keep markers with reasonable support; cap per set; drop tiny sets
        sets <- lapply(sets, function(g) names(sort(table(g), decreasing = TRUE)))
        sets <- lapply(sets, function(g) head(unique(g), 8))
        sets <- sets[vapply(sets, length, 1L) >= 2]
        if (length(sets) >= 2) return(sets)
        log_msg("CellMarker2 tissue subset too small; falling back to PBMC panel")
      } else log_msg("No CellMarker2 rows for tissue; falling back to PBMC panel")
    } else log_msg("CellMarker2 file/readxl unavailable; falling back to PBMC panel")
  }
  log_msg("Marker panel: built-in PBMC/immune fallback")
  PBMC_PANEL
}
marker_sets <- build_marker_sets()

# ---- gene activity restricted to panel genes (all-gene run times out) -------
ann <- Annotation(obj); avail <- unique(ann$gene_name)
gp <- intersect(toupper(unique(unlist(marker_sets))), toupper(avail))
# map back to actual-case symbols present in annotation
gp <- avail[toupper(avail) %in% gp]
log_msg("GeneActivity on", length(gp), "marker genes")
ga <- GeneActivity(obj, features = gp)
obj[["ACTIVITY"]] <- CreateAssayObject(counts = ga)
DefaultAssay(obj) <- "ACTIVITY"
obj <- NormalizeData(obj, scale.factor = median(obj$nCount_ACTIVITY))

# ---- per-cluster mean activity -> z-score -> marker-set score ---------------
avg <- as.matrix(AverageExpression(obj, assays = "ACTIVITY",
                 features = rownames(obj[["ACTIVITY"]]), group.by = "seurat_clusters")$ACTIVITY)
colnames(avg) <- sub("^g", "", colnames(avg))                 # gotcha: strip 'g' prefix
avg <- avg[, levels(obj$seurat_clusters), drop = FALSE]
z <- t(scale(t(avg))); z[is.na(z)] <- 0
setU <- lapply(marker_sets, toupper); rownames(z) <- toupper(rownames(z))
score_mat <- sapply(setU, function(g) { g <- intersect(g, rownames(z))
  if (length(g) == 0) rep(NA_real_, ncol(z)) else colMeans(z[g, , drop = FALSE]) })
rownames(score_mat) <- colnames(z)

# ---- confidence-gated assignment -------------------------------------------
assign_one <- function(v) {
  v[is.na(v)] <- -Inf; o <- order(v, decreasing = TRUE)
  top <- v[o[1]]; second <- if (length(v) > 1) v[o[2]] else -Inf
  flag <- "ok"
  if (top < cfg$annotation$min_top_zscore) flag <- "low-confidence"
  else if ((top - second) < cfg$annotation$min_margin) flag <- "ambiguous"
  data.frame(cell_type = colnames(score_mat)[o[1]], top_score = round(top, 2),
             margin = round(top - second, 2), confidence = flag)
}
assign_tbl <- do.call(rbind, lapply(seq_len(nrow(score_mat)), function(i) assign_one(score_mat[i, ])))
assign_tbl <- cbind(cluster = rownames(score_mat), assign_tbl)
write.csv(assign_tbl, tabp(cfg, "cluster_celltype_assignment.csv"), row.names = FALSE)
write.csv(round(score_mat, 3), tabp(cfg, "celltype_zscore_by_cluster.csv"))
n_flag <- sum(assign_tbl$confidence != "ok")
if (n_flag > 0) log_msg("NOTE:", n_flag, "cluster(s) flagged low-confidence/ambiguous — see assignment table")

# ---- write cell_type metadata (gotcha: names + AddMetaData) -----------------
map <- setNames(assign_tbl$cell_type, assign_tbl$cluster)
ct <- unname(map[as.character(obj$seurat_clusters)]); names(ct) <- colnames(obj)
ct[is.na(ct)] <- "Unknown"
obj <- AddMetaData(obj, factor(ct, levels = sort(unique(ct))), col.name = "cell_type")

# ---- optional label transfer from scRNA-seq reference ----------------------
if (identical(cfg$annotation$method, "label_transfer") &&
    !is.null(cfg$annotation$reference_rds) && file.exists(cfg$annotation$reference_rds)) {
  log_msg("Label transfer from reference:", cfg$annotation$reference_rds)
  tryCatch({
    ref <- readRDS(cfg$annotation$reference_rds)
    DefaultAssay(obj) <- "ACTIVITY"
    anchors <- FindTransferAnchors(reference = ref, query = obj,
                 reduction = "cca", reference.assay = DefaultAssay(ref), query.assay = "ACTIVITY")
    pred <- TransferData(anchorset = anchors, refdata = ref[[cfg$annotation$reference_label_col]][,1],
                         weight.reduction = obj[["lsi"]], dims = cfg$dimreduc$lsi_dims_vec)
    obj$predicted_celltype <- pred$predicted.id
    obj$predicted_score <- pred$prediction.score.max
    # disagreement flag between marker call and transfer
    disagree <- as.character(obj$cell_type) != as.character(obj$predicted_celltype)
    log_msg("Label transfer done; marker/transfer disagreement in",
            round(100 * mean(disagree, na.rm = TRUE), 1), "% of cells (see metadata)")
  }, error = function(e) log_msg("Label transfer failed (kept marker calls):", conditionMessage(e)))
}

# ---- figures ---------------------------------------------------------------
DefaultAssay(obj) <- "ACTIVITY"
mk <- unique(unlist(lapply(marker_sets, function(g) head(g, 3))))
mk <- mk[mk %in% rownames(obj)]
save_fig(cfg, DotPlot(obj, features = mk, group.by = "seurat_clusters") + RotatedAxis() +
           ggtitle("Marker gene activity by cluster") & plot_theme(),
         "08_marker_dotplot", max(8, 0.4 * length(mk)), 7)

fp_feats <- head(mk, 9)
save_fig(cfg, FeaturePlot(obj, features = fp_feats, ncol = 3, order = TRUE) & plot_theme(),
         "09_marker_featureplots", 11, 3.5 * ceiling(length(fp_feats) / 3))

nct <- nlevels(obj$cell_type)
save_fig(cfg, DimPlot(obj, group.by = "cell_type", label = TRUE, repel = TRUE) +
           scale_color_manual(values = clpal(nct)) +
           ggtitle("Cell-type annotation (marker gene activity)") & plot_theme(),
         "10_umap_annotated", 7.5, 5.5)

comp <- as.data.frame(table(obj$cell_type)); colnames(comp) <- c("cell_type", "n_cells")
comp$pct <- round(100 * comp$n_cells / sum(comp$n_cells), 1)
comp <- comp[order(-comp$n_cells), ]
write.csv(comp, tabp(cfg, "celltype_composition.csv"), row.names = FALSE)
save_fig(cfg, ggplot(comp, aes(reorder(cell_type, n_cells), n_cells, fill = cell_type)) +
           geom_col() + coord_flip() + scale_fill_manual(values = clpal(nrow(comp))) +
           labs(x = NULL, y = "Cells", title = "Cell-type composition") +
           plot_theme() + theme(legend.position = "none"),
         "11_celltype_composition", 6.5, 4.5)

Idents(obj) <- "cell_type"
save_fig(cfg, DotPlot(obj, features = mk, group.by = "cell_type") + RotatedAxis() +
           ggtitle("Marker gene activity by cell type") & plot_theme(),
         "12_marker_dotplot_annotated", max(8, 0.4 * length(mk)), 6)

# marker activity table (mean z per marker-set per cluster already saved as zscore table)
write.csv(round(t(score_mat), 3), tabp(cfg, "marker_activity_by_cluster.csv"))

st$obj <- obj; st$marker_sets <- marker_sets; st$assign_tbl <- assign_tbl; st$n_flag <- n_flag
saveRDS(st, ckpt(cfg, "04_annotated.rds"))
log_msg("STAGE 04 done -> checkpoints/04_annotated.rds"); cat("STAGE04_OK\n")
