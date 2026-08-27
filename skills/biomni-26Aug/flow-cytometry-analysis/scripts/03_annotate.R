#!/usr/bin/env Rscript
# =====================================================================================
# 03_annotate.R  --  Annotate metaclusters to cell types (semi-automated) + merge.
#
# Strategy:
#   1) Compute per-cluster median marker expression; z-score lineage markers -> heatmap.
#   2) Score clusters against reference marker sets (CellMarker2 data lake, if available) to
#      PROPOSE cell-type labels. This is semi-automated: the analyst confirms/edits.
#   3) mergeClusters() to consolidate redundant clusters into named populations.
#   4) Emit an editable annotation template (CSV) so labels are explicit and reproducible.
#
# Two-tier note: annotate at this (coarse, interpretable) resolution; lineages later flagged
# as MERGED by the benchmark (05) should be subclustered at higher resolution (see references).
#
# Usage:
#   Rscript 03_annotate.R --sce <outdir>/sce_clustered.rds --outdir <outdir> \
#     --annotation <optional annotation.csv: cluster,population> \
#     --cellmarker <optional CellMarker2 tsv/csv> --tissue "bone marrow" --seed 1234
# Output: <outdir>/sce_annotated.rds, cluster_medians.rds, annotation_template.csv, marker heatmap.
# =====================================================================================

suppressPackageStartupMessages({
  library(optparse); library(CATALYST); library(SingleCellExperiment)
  library(ComplexHeatmap); library(matrixStats); library(grid)
})

opt <- parse_args(OptionParser(option_list = list(
  make_option("--sce", type = "character"),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--annotation", type = "character", default = NA),  # user-provided cluster->population map
  make_option("--cellmarker", type = "character", default = NA),  # CellMarker2 reference file
  make_option("--tissue", type = "character", default = NA),
  make_option("--seed", type = "integer", default = 1234L)
)))
set.seed(opt$seed)
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
logmsg("=== 03_annotate.R  |  seed=%d ===", opt$seed)

sce <- readRDS(opt$sce)
chosen_k <- S4Vectors::metadata(sce)$chosen_k
cl <- CATALYST::cluster_ids(sce, chosen_k)
tm <- rownames(sce)[SummarizedExperiment::rowData(sce)$marker_class == "type"]
ex <- SummarizedExperiment::assay(sce, "exprs")

# ------------------------------------------------------------------ per-cluster medians + z
clusters <- levels(cl)
med <- sapply(clusters, function(cc) matrixStats::rowMedians(ex[tm, cl == cc, drop = FALSE]))
rownames(med) <- tm; colnames(med) <- clusters
z <- t(scale(t(med)))  # z-score each marker across clusters
saveRDS(list(median = med, zscore = z, chosen_k = chosen_k, clusters = clusters),
        file.path(opt$outdir, "cluster_medians.rds"))

# ------------------------------------------------------------------ CellMarker2-assisted proposal
# Score each cluster by mean z of a cell type's marker set; propose the top-scoring type.
proposed <- setNames(rep(NA_character_, length(clusters)), clusters)
if (!is.na(opt$cellmarker) && file.exists(opt$cellmarker)) {
  cm <- tryCatch(read.delim(opt$cellmarker, stringsAsFactors = FALSE), error = function(e) NULL)
  if (!is.null(cm)) {
    # expect columns: cell_name/cell_type and marker/Symbol; filter by tissue if given
    ctcol <- intersect(c("cell_name", "cell_type", "celltype"), tolower(names(cm)))
    mkcol <- intersect(c("marker", "symbol", "gene"), tolower(names(cm)))
    names(cm) <- tolower(names(cm))
    if (length(ctcol) && length(mkcol)) {
      if (!is.na(opt$tissue) && "tissue_type" %in% names(cm))
        cm <- cm[grepl(opt$tissue, cm$tissue_type, ignore.case = TRUE), , drop = FALSE]
      sets <- split(toupper(cm[[mkcol[1]]]), cm[[ctcol[1]]])
      marker_up <- toupper(tm)
      score <- sapply(clusters, function(cc) {
        sapply(sets, function(g) {
          idx <- which(marker_up %in% g); if (!length(idx)) return(NA_real_); mean(z[idx, cc], na.rm = TRUE)
        })
      })
      if (is.matrix(score)) proposed[] <- rownames(score)[apply(score, 2, function(s) if (all(is.na(s))) NA_integer_ else which.max(s))]
      logmsg("CellMarker2-assisted proposals generated for %d clusters (tissue=%s).",
             sum(!is.na(proposed)), ifelse(is.na(opt$tissue), "any", opt$tissue))
    }
  }
} else {
  logmsg("No CellMarker2 file supplied; proposals left blank. Provide --cellmarker to auto-scaffold labels.")
  logmsg("  (CellMarker2 is available in the Biomni data lake; export a tissue-filtered marker table.)")
}

# top-3 markers per cluster as an annotation aid
top_markers <- sapply(clusters, function(cc) paste(names(sort(z[, cc], decreasing = TRUE))[1:3], collapse = ", "))

# ------------------------------------------------------------------ annotation map
# If a user annotation.csv is given, use it; else write a template for the analyst to fill.
if (!is.na(opt$annotation) && file.exists(opt$annotation)) {
  amap <- read.csv(opt$annotation, stringsAsFactors = FALSE)   # columns: cluster, population
  logmsg("Using user annotation map: %s (%d rows)", opt$annotation, nrow(amap))
} else {
  amap <- data.frame(cluster = clusters,
                     proposed_population = ifelse(is.na(proposed), "", proposed),
                     top_markers = top_markers,
                     population = ifelse(is.na(proposed), "", proposed),  # editable final label
                     stringsAsFactors = FALSE)
  tmpl <- file.path(opt$outdir, "annotation_template.csv")
  # write template to workspace then copy (S3 FUSE-safe), fall back to direct write for CSV
  write.csv(amap, tmpl, row.names = FALSE)
  logmsg("Wrote editable annotation template: %s", tmpl)
  logmsg("  -> Fill the 'population' column and rerun with --annotation to finalize labels.")
}

# ------------------------------------------------------------------ merge to populations
# Build a merging table for CATALYST::mergeClusters (old_cluster -> new_population).
merge_tbl <- data.frame(old_cluster = as.character(amap$cluster),
                        new_cluster = ifelse(nchar(amap$population) > 0, amap$population,
                                             paste0("cluster_", amap$cluster)),
                        stringsAsFactors = FALSE)
sce <- CATALYST::mergeClusters(sce, k = chosen_k, table = merge_tbl, id = "annotation", overwrite = TRUE)
n_pop <- length(unique(CATALYST::cluster_ids(sce, "annotation")))
logmsg("Merged %d metaclusters -> %d annotated populations (id='annotation').", length(clusters), n_pop)

# ------------------------------------------------------------------ marker z-score heatmap
ann_ids <- CATALYST::cluster_ids(sce, "annotation")
pops <- levels(ann_ids)
medp <- sapply(pops, function(pp) matrixStats::rowMedians(ex[tm, ann_ids == pp, drop = FALSE]))
rownames(medp) <- tm; colnames(medp) <- pops
zp <- t(scale(t(medp)))
counts <- table(ann_ids)
ht <- ComplexHeatmap::Heatmap(
  zp, name = "z-score",
  column_title = "Median marker expression by annotated population",
  row_names_gp = grid::gpar(fontsize = 8), column_names_gp = grid::gpar(fontsize = 8),
  top_annotation = ComplexHeatmap::HeatmapAnnotation(
    n = ComplexHeatmap::anno_barplot(as.integer(counts[pops])), annotation_name_gp = grid::gpar(fontsize = 8)))
png(file.path(figdir, "fig_marker_heatmap.png"), width = 1600, height = 1400, res = 180)
ComplexHeatmap::draw(ht); dev.off()

S4Vectors::metadata(sce)$annotation_map <- amap
saveRDS(sce, file.path(opt$outdir, "sce_annotated.rds"))
logmsg("Saved: %s", file.path(opt$outdir, "sce_annotated.rds"))
logmsg("=== 03 complete ===")
