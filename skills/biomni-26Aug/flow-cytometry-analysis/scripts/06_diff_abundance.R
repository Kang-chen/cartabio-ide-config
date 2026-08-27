#!/usr/bin/env Rscript
# =====================================================================================
# 06_diff_abundance.R  --  Differential abundance / state between conditions (CONDITIONAL).
#
# Runs ONLY when a grouping variable with >=2 groups exists in sample metadata.
#
# THE RIGOR GATE (the whole point of this script):
#   Emit p-values ONLY if EVERY compared group has >= MIN_N_PER_GROUP samples (default 3).
#   With 2-vs-2 (or fewer) the design is under-powered and variance is unestimable ->
#   REFUSE p-values and fall back to a DESCRIPTIVE report (per-group mean/median abundance,
#   fold-change, per-sample points) with an explicit logged limitation. "The rigor payoff
#   is refusing to emit a p-value on n=2." -- do not soften this.
#
# Design formula uses BATCH + COVARIATES, not group alone:  ~ <covariates> + <batch> + group
# Abundance: diffcyt-DA-edgeR (GLM on cluster counts). State: diffcyt-DS-limma (median
# state-marker expression per cluster). Weber et al., Commun Biol 2019.
#
# Usage:
#   Rscript 06_diff_abundance.R --sce <outdir>/sce_annotated.rds --outdir <outdir> \
#     --group condition --batch batch --covariates age,sex \
#     --min_n 3 --contrast "responder-nonresponder"
# =====================================================================================

suppressPackageStartupMessages({
  library(optparse); library(CATALYST); library(SingleCellExperiment); library(ggplot2)
})

opt <- parse_args(OptionParser(option_list = list(
  make_option("--sce", type = "character"),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--group", type = "character", default = "condition"),  # grouping column in colData
  make_option("--batch", type = "character", default = ""),           # optional batch column
  make_option("--covariates", type = "character", default = ""),      # optional comma-sep covariates
  make_option("--min_n", type = "integer", default = 3L),             # samples/group required for p-values
  make_option("--contrast", type = "character", default = ""),        # "grpA-grpB"; default = first two levels
  make_option("--fdr", type = "double", default = 0.05)
)))
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, recursive = TRUE, showWarnings = FALSE)
tabdir <- file.path(opt$outdir, "tables"); dir.create(tabdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
logmsg("=== 06_diff_abundance.R  |  group=%s batch=%s covariates=%s min_n=%d ===",
       opt$group, opt$batch, opt$covariates, opt$min_n)

sce <- readRDS(opt$sce)
cd <- SummarizedExperiment::colData(sce)

# ---- guard 1: grouping variable present with >=2 groups ----------------------------------
ei <- CATALYST::ei(sce)  # experiment info: one row per sample
if (!opt$group %in% colnames(ei)) {
  logmsg("Grouping variable '%s' not in sample metadata -> DIFFERENTIAL ANALYSIS SKIPPED.", opt$group)
  quit(save = "no", status = 0)
}
grp <- factor(ei[[opt$group]])
if (nlevels(grp) < 2) {
  logmsg("Only one level of '%s' -> nothing to compare -> SKIPPED.", opt$group); quit(save = "no", status = 0)
}
tab <- table(grp)
logmsg("Groups (%s): %s", opt$group, paste(sprintf("%s=%d", names(tab), tab), collapse = ", "))

# ---- ALWAYS produce descriptive abundance (works at any n) -------------------------------
counts <- table(cluster = CATALYST::cluster_ids(sce, S4Vectors::metadata(sce)$chosen_k),
                sample  = CATALYST::sample_ids(sce))
props <- sweep(counts, 2, colSums(counts), "/") * 100
# map sample -> group
samp_grp <- setNames(as.character(ei[[opt$group]]), as.character(ei$sample_id))
long <- as.data.frame(as.table(props)); colnames(long) <- c("cluster", "sample", "pct")
long$group <- samp_grp[as.character(long$sample)]
desc <- aggregate(pct ~ cluster + group, long, function(x) c(mean = mean(x), median = median(x), sd = sd(x)))
desc <- do.call(data.frame, desc)
write.csv(desc, file.path(tabdir, "abundance_by_group_descriptive.csv"), row.names = FALSE)
# per-sample boxplot (always safe, always honest)
pb <- ggplot(long, aes(group, pct, fill = group)) +
  geom_boxplot(outlier.shape = NA, alpha = .5) +
  geom_jitter(width = .15, size = 1) + facet_wrap(~cluster, scales = "free_y") +
  labs(x = NULL, y = "% of sample", title = "Abundance by group (per-sample points)") +
  theme_bw(base_size = 9) + theme(text = element_text(family = "Liberation Sans"), legend.position = "none")
ggsave(file.path(figdir, "fig_abundance_by_group.png"), pb, width = 9, height = 7, dpi = 150)
ggsave(file.path(figdir, "fig_abundance_by_group.svg"), pb, width = 9, height = 7)

# ---- guard 2: THE RIGOR GATE ------------------------------------------------------------
min_group <- min(tab)
if (min_group < opt$min_n) {
  logmsg("*** RIGOR GATE TRIGGERED: smallest group has %d sample(s) < required %d. ***", min_group, opt$min_n)
  logmsg("*** REFUSING to emit p-values (design under-powered; variance unestimable). ***")
  logmsg("*** Reported: DESCRIPTIVE abundance only (means/medians/fold-change, per-sample points). ***")
  # descriptive fold-change between first two groups
  g <- levels(grp)[1:2]
  fc <- reshape(desc[desc$group %in% g, c("cluster", "group", "pct.mean")], idvar = "cluster",
                timevar = "group", direction = "wide")
  fcn <- grep("pct.mean", colnames(fc), value = TRUE)
  fc$log2FC <- log2((fc[[fcn[2]]] + 1e-6) / (fc[[fcn[1]]] + 1e-6))
  fc <- fc[order(-abs(fc$log2FC)), ]
  write.csv(fc, file.path(tabdir, "abundance_fold_change_descriptive.csv"), row.names = FALSE)
  saveRDS(list(mode = "descriptive_only", reason = sprintf("min group n=%d < %d", min_group, opt$min_n),
               descriptive = desc, fold_change = fc),
          file.path(opt$outdir, "diff_abundance.rds"))
  logmsg("Saved descriptive-only results. NO statistical test performed by design.")
  logmsg("=== 06 complete (descriptive mode) ===")
  quit(save = "no", status = 0)
}

# ---- statistical testing (only reached when every group has >= min_n samples) ------------
if (!requireNamespace("diffcyt", quietly = TRUE)) {
  logmsg("diffcyt not installed; install via BiocManager. Descriptive results already written."); quit(save = "no", status = 0)
}
logmsg("Rigor gate PASSED (min group n=%d >= %d). Running diffcyt.", min_group, opt$min_n)

# build design: ~ covariates + batch + group   (group LAST so it is the tested term)
terms <- character(0)
covs <- if (nchar(opt$covariates)) strsplit(opt$covariates, ",")[[1]] else character(0)
for (cv in covs) if (cv %in% colnames(ei)) terms <- c(terms, cv) else logmsg("  covariate '%s' absent; ignored.", cv)
if (nchar(opt$batch) && opt$batch %in% colnames(ei)) terms <- c(terms, opt$batch) else if (nchar(opt$batch)) logmsg("  batch '%s' absent; ignored.", opt$batch)
formula_rhs <- paste(c(terms, opt$group), collapse = " + ")
logmsg("Design formula: ~ %s", formula_rhs)
design <- diffcyt::createDesignMatrix(ei, cols_design = c(terms, opt$group))

# contrast: default tests the last coefficient (the group term's non-reference level)
ncoef <- ncol(design)
contrast_vec <- rep(0, ncoef); contrast_vec[ncoef] <- 1
if (nchar(opt$contrast)) logmsg("  (requested contrast '%s'; using last-coefficient test for the group term)", opt$contrast)
contrast <- diffcyt::createContrast(contrast_vec)

# DA: differential ABUNDANCE of clusters (edgeR GLM on counts)
da <- tryCatch(
  diffcyt::diffcyt(sce, clustering_to_use = S4Vectors::metadata(sce)$chosen_k,
                   analysis_type = "DA", method_DA = "diffcyt-DA-edgeR",
                   design = design, contrast = contrast, verbose = FALSE),
  error = function(e) { logmsg("DA error: %s", conditionMessage(e)); NULL })
if (!is.null(da)) {
  da_res <- as.data.frame(diffcyt::topTable(da, all = TRUE, show_props = TRUE))
  write.csv(da_res, file.path(tabdir, "diff_abundance_edgeR.csv"), row.names = FALSE)
  nsig <- sum(da_res$p_adj < opt$fdr, na.rm = TRUE)
  logmsg("DA-edgeR: %d cluster(s) significant at FDR<%.2f.", nsig, opt$fdr)
}

# DS: differential STATE (median state-marker expression; limma)
ds <- tryCatch(
  diffcyt::diffcyt(sce, clustering_to_use = S4Vectors::metadata(sce)$chosen_k,
                   analysis_type = "DS", method_DS = "diffcyt-DS-limma",
                   design = design, contrast = contrast, verbose = FALSE),
  error = function(e) { logmsg("DS error: %s", conditionMessage(e)); NULL })
if (!is.null(ds)) {
  ds_res <- as.data.frame(diffcyt::topTable(ds, all = TRUE))
  write.csv(ds_res, file.path(tabdir, "diff_state_limma.csv"), row.names = FALSE)
  nsig <- sum(ds_res$p_adj < opt$fdr, na.rm = TRUE)
  logmsg("DS-limma: %d cluster-marker pair(s) significant at FDR<%.2f.", nsig, opt$fdr)
}

saveRDS(list(mode = "tested", design = formula_rhs,
             da = if (exists("da_res")) da_res else NULL,
             ds = if (exists("ds_res")) ds_res else NULL, descriptive = desc),
        file.path(opt$outdir, "diff_abundance.rds"))
logmsg("Saved: %s", file.path(opt$outdir, "diff_abundance.rds"))
logmsg("=== 06 complete (tested mode) ===")
