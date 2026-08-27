#!/usr/bin/env Rscript
# =====================================================================================
# 05_benchmark.R  --  Benchmark automated clusters vs manual gates (CONDITIONAL).
#
# Runs ONLY if per-cell manual-gate labels exist (sce$population_id). Uses the standard
# max-overlap cluster->gold mapping (Weber & Robinson, Cytometry A 2016).
#
# TWO NON-NEGOTIABLE CORRECTNESS RULES (both learned the hard way):
#   (1) NAME the mapping vector. An unnamed vector makes name-indexing return all-NA:
#         c2p <- setNames(colnames(ct)[apply(ct, 1, which.max)], rownames(ct))   # correct
#   (2) DETECT many-to-one collapses. When >=2 gold populations map to the SAME cluster, the
#       "loser" gets F1=0 -- that is a RESOLUTION artifact (merged), NOT "population missed".
#       Label such populations status="merged" (with the partner) so no reader misreads F1=0.
#
# Also runs a RESOLUTION-SENSITIVITY sweep (coarse..fine..full SOM) to separate merges from
# true misses, and reports overall accuracy, per-population precision/recall/F1, ARI, NMI.
#
# Usage:
#   Rscript 05_benchmark.R --sce <outdir>/sce_dr.rds --outdir <outdir> \
#     --sweep meta10,meta14,meta20,som100 --seed 1234
# Output: benchmark_per_population.csv, resolution_sensitivity.csv, benchmark F1 figure, benchmark.rds
# =====================================================================================

suppressPackageStartupMessages({
  library(optparse); library(CATALYST); library(SingleCellExperiment); library(ggplot2)
})

opt <- parse_args(OptionParser(option_list = list(
  make_option("--sce", type = "character"),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--sweep", type = "character", default = ""),  # e.g. meta10,meta14,meta20,som100
  make_option("--seed", type = "integer", default = 1234L)
)))
set.seed(opt$seed)
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, recursive = TRUE, showWarnings = FALSE)
tabdir <- file.path(opt$outdir, "tables"); dir.create(tabdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
logmsg("=== 05_benchmark.R  |  seed=%d ===", opt$seed)

sce <- readRDS(opt$sce)
if (!"population_id" %in% names(SummarizedExperiment::colData(sce))) {
  logmsg("No per-cell labels (population_id) -> BENCHMARK SKIPPED (this is expected for real unlabeled data).")
  quit(save = "no", status = 0)
}

# restrict to labeled cells
truth_all <- as.character(sce$population_id)
labeled <- !is.na(truth_all) & truth_all != "unassigned"
logmsg("Benchmarking on %d labeled cells (of %d total; %.1f%%).", sum(labeled), length(truth_all), 100 * mean(labeled))

# ---- core benchmark at the CHOSEN resolution -----------------------------------------------
chosen_k <- S4Vectors::metadata(sce)$chosen_k
cl_chosen <- as.character(CATALYST::cluster_ids(sce, chosen_k))[labeled]
truth <- factor(truth_all[labeled])
pops <- levels(truth)

benchmark_at <- function(cl_vec, truth, pops) {
  ct <- table(cluster = cl_vec, truth = truth)
  # (1) NAMED max-overlap mapping cluster -> gold population
  c2p <- setNames(colnames(ct)[apply(ct, 1, which.max)], rownames(ct))
  pred <- factor(unname(c2p[as.character(cl_vec)]), levels = pops)
  # per-population precision/recall/F1
  res <- data.frame(population = pops, n_truth = as.integer(table(truth)[pops]),
                    precision = NA_real_, recall = NA_real_, F1 = NA_real_, stringsAsFactors = FALSE)
  for (p in pops) {
    tp <- sum(pred == p & truth == p, na.rm = TRUE)
    fp <- sum(pred == p & truth != p, na.rm = TRUE)
    fn <- sum(pred != p & truth == p, na.rm = TRUE)
    prec <- if ((tp + fp) > 0) tp / (tp + fp) else NA_real_
    rec  <- if ((tp + fn) > 0) tp / (tp + fn) else 0
    f1   <- if (!is.na(prec) && (prec + rec) > 0) 2 * prec * rec / (prec + rec) else 0
    res[res$population == p, c("precision", "recall", "F1")] <- c(prec, rec, f1)
  }
  # (2) many-to-one COLLAPSE detection: which gold pops share a winning cluster
  win_cluster <- c2p                       # cluster -> gold
  gold_to_cluster <- sapply(pops, function(p) { # gold -> its max-overlap cluster
    sub <- ct[, p]; if (sum(sub) == 0) NA_character_ else names(sub)[which.max(sub)] })
  status <- setNames(rep("recovered", length(pops)), pops)
  for (p in pops) {
    ccl <- gold_to_cluster[p]
    if (is.na(ccl)) { status[p] <- "missed"; next }
    # who does that cluster actually get assigned to?
    assigned_gold <- win_cluster[ccl]
    if (!is.na(assigned_gold) && assigned_gold != p) status[p] <- paste0("merged_with:", assigned_gold)
    else if (res$F1[res$population == p] == 0) status[p] <- "missed"
  }
  res$status <- status[res$population]
  # overall metrics
  acc <- mean(pred == truth, na.rm = TRUE)
  w <- res$n_truth / sum(res$n_truth)
  wf1 <- sum(res$F1 * w, na.rm = TRUE)
  # aricode::ARI/NMI convert character vectors to integers internally and can overflow
  # on long vectors (~100k+ cells). Pre-convert to integer factors to avoid the overflow.
  ari <- if (requireNamespace("aricode", quietly = TRUE))
    aricode::ARI(as.integer(factor(cl_vec)), as.integer(factor(truth))) else NA_real_
  nmi <- if (requireNamespace("aricode", quietly = TRUE))
    aricode::NMI(as.integer(factor(cl_vec)), as.integer(factor(truth))) else NA_real_
  list(res = res, acc = acc, wf1 = wf1, ari = ari, nmi = nmi,
       n_recovered = sum(res$status == "recovered"), n_merged = sum(grepl("^merged", res$status)),
       n_missed = sum(res$status == "missed"))
}

core <- benchmark_at(cl_chosen, truth, pops)
write.csv(core$res, file.path(tabdir, "benchmark_per_population.csv"), row.names = FALSE)
logmsg("CHOSEN (%s): accuracy=%.3f weighted-F1=%.3f ARI=%s NMI=%s | recovered=%d merged=%d missed=%d",
       chosen_k, core$acc, core$wf1,
       ifelse(is.na(core$ari), "NA", sprintf("%.3f", core$ari)),
       ifelse(is.na(core$nmi), "NA", sprintf("%.3f", core$nmi)),
       core$n_recovered, core$n_merged, core$n_missed)
# explicitly report merges so F1=0 is never misread
merged_rows <- core$res[grepl("^merged", core$res$status), ]
if (nrow(merged_rows)) for (i in seq_len(nrow(merged_rows)))
  logmsg("  MERGED (not missed): %s -> %s (F1=%.2f is a resolution artifact; check sweep)",
         merged_rows$population[i], sub("merged_with:", "", merged_rows$status[i]), merged_rows$F1[i])

# ------------------------------------------------------------------ benchmark F1 figure
res <- core$res[order(core$res$F1), ]
res$population <- factor(res$population, levels = res$population)
res$grp <- ifelse(res$F1 >= 0.7, "recovered (F1>=0.7)",
            ifelse(grepl("^merged", res$status), "merged (resolution)",
              ifelse(res$F1 > 0, "partial", "missed")))
cols <- c("recovered (F1>=0.7)" = "#75A025", "partial" = "#FF9400",
          "merged (resolution)" = "#0279EE", "missed" = "#A9A9A9")
pf <- ggplot(res, aes(population, F1, fill = grp)) + geom_col() + coord_flip() +
  scale_fill_manual(values = cols, name = NULL) + ylim(0, 1) +
  labs(x = NULL, y = "F1 vs manual gate", title = sprintf("Benchmark per population (%s)", chosen_k)) +
  theme_bw(base_size = 11) + theme(text = element_text(family = "Liberation Sans"))
ggsave(file.path(figdir, "fig_benchmark_F1.png"), pf, width = 7, height = 5, dpi = 150)
ggsave(file.path(figdir, "fig_benchmark_F1.svg"), pf, width = 7, height = 5)

# ------------------------------------------------------------------ resolution sensitivity sweep
sweep <- if (nchar(opt$sweep)) strsplit(opt$sweep, ",")[[1]] else character(0)
avail <- colnames(S4Vectors::metadata(sce)$cluster_codes)
# translate a token like "som100" -> the SOM (som100) level if present, else keep metaN
resolve_level <- function(tok) {
  if (tok %in% avail) return(tok)
  if (grepl("^som", tok) && "som100" %in% avail) return("som100")
  NA_character_
}
sweep_rows <- list()
if (length(sweep)) {
  for (tok in sweep) {
    lv <- resolve_level(tok); if (is.na(lv)) { logmsg("  sweep level '%s' unavailable; skipping.", tok); next }
    clv <- as.character(CATALYST::cluster_ids(sce, lv))[labeled]
    b <- benchmark_at(clv, truth, pops)
    sweep_rows[[tok]] <- data.frame(level = lv, n_clusters = length(unique(clv)),
                                    accuracy = round(b$acc, 3), weighted_F1 = round(b$wf1, 3),
                                    recovered = b$n_recovered, merged = b$n_merged, missed = b$n_missed)
    logmsg("  SWEEP %s (%s): acc=%.3f wF1=%.3f recovered=%d merged=%d missed=%d",
           tok, lv, b$acc, b$wf1, b$n_recovered, b$n_merged, b$n_missed)
  }
}
if (length(sweep_rows)) {
  sw <- do.call(rbind, sweep_rows); write.csv(sw, file.path(tabdir, "resolution_sensitivity.csv"), row.names = FALSE)
  # sweep line figure
  swl <- reshape(sw[, c("level", "accuracy", "weighted_F1")], direction = "long",
                 varying = c("accuracy", "weighted_F1"), v.names = "value", timevar = "metric",
                 times = c("accuracy", "weighted_F1"), idvar = "level")
  swl$level <- factor(swl$level, levels = sw$level)
  ps <- ggplot(swl, aes(level, value, color = metric, group = metric)) +
    geom_line() + geom_point() + ylim(0, 1) +
    labs(x = "clustering resolution", y = "score", title = "Resolution sensitivity") +
    theme_bw(base_size = 11) + theme(text = element_text(family = "Liberation Sans"),
                                     axis.text.x = element_text(angle = 30, hjust = 1))
  ggsave(file.path(figdir, "fig_resolution_sensitivity.png"), ps, width = 6.5, height = 4.5, dpi = 150)
  ggsave(file.path(figdir, "fig_resolution_sensitivity.svg"), ps, width = 6.5, height = 4.5)
}

saveRDS(list(core = core, sweep = if (length(sweep_rows)) do.call(rbind, sweep_rows) else NULL),
        file.path(opt$outdir, "benchmark.rds"))
logmsg("Saved: %s", file.path(opt$outdir, "benchmark.rds"))
logmsg("=== 05 complete ===")
