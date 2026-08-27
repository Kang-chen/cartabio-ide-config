#!/usr/bin/env Rscript
# make_figures.R -- Stage 6 (figures) of signature-response-enrichment.
#
# Produces the four report figures, each saved as PNG (300 dpi) AND SVG (editable text):
#   heatmap     NR-vs-R dGSVA across gene_set x timepoint x cohort (main body = change
#               from baseline; optional right strip = absolute endpoint, flagged confounded)
#   volcano     per-gene DE (limma-voom); signatures highlighted
#   pd          pharmacodynamic pre/post signature change (pooled)
#   trajectory  dGSVA over on-treatment timepoints, NR vs R, mean +/- SEM
#
# Colors (Okabe-Ito, colorblind-safe): NR #D55E00, R #0072B2, volcano #E69F00 / #7E2F8E,
# PD #117733. Marks: * nominal p<0.05, ** FDR<0.05. Font: Liberation Sans.
#
# Each subcommand reads a tidy CSV (produced by the stat scripts) so figures never
# recompute statistics. Call the subcommand you need; unavailable modules are simply
# not called (graceful skip at the workflow level).
#
# USAGE (examples):
#   Rscript make_figures.R heatmap   --in dgsva_endpoint.csv --out-prefix figures/Fig1_heatmap
#   Rscript make_figures.R volcano   --in de_wk12.csv        --out-prefix figures/Fig2_volcano \
#       --sig-genes sigs.json
#   Rscript make_figures.R pd        --in pd_paired.csv      --out-prefix figures/Fig3_pd
#   Rscript make_figures.R trajectory --in traj_summary.csv  --out-prefix figures/Fig4_traj

suppressWarnings(suppressMessages({
  library(optparse); library(ggplot2); library(jsonlite)
}))

COL_NR <- "#D55E00"; COL_R <- "#0072B2"
COL_VA <- "#E69F00"; COL_VB <- "#7E2F8E"; COL_PD <- "#117733"
FONT <- "Liberation Sans"

base_theme <- function()
  theme_bw(base_family = FONT) +
  theme(text = element_text(family = FONT),
        panel.grid.minor = element_blank(),
        plot.title = element_text(face = "bold", size = 12),
        axis.text = element_text(size = 8.5))

save_both <- function(p, prefix, w = 9, h = 6) {
  dir.create(dirname(prefix), showWarnings = FALSE, recursive = TRUE)
  ggsave(paste0(prefix, ".png"), p, width = w, height = h, dpi = 300)
  # Cairo SVG keeps text as text; fall back to grDevices svg if svglite absent
  ok <- requireNamespace("svglite", quietly = TRUE)
  if (ok) ggsave(paste0(prefix, ".svg"), p, width = w, height = h, device = svglite::svglite)
  else ggsave(paste0(prefix, ".svg"), p, width = w, height = h)
  message(sprintf("[fig] wrote %s.png + .svg", prefix))
}

sig_mark <- function(p_nom, fdr)
  ifelse(!is.na(fdr) & fdr < 0.05, "**",
         ifelse(!is.na(p_nom) & p_nom < 0.05, "*", ""))

# ---- subcommand dispatch ----
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("first arg must be: heatmap | volcano | pd | trajectory")
sub <- args[1]; rest <- args[-1]

parse_common <- function(rest, extra = list()) {
  ol <- c(list(
    make_option("--in", type = "character", dest = "inp"),
    make_option("--out-prefix", type = "character", dest = "prefix")), extra)
  parse_args(OptionParser(option_list = ol), args = rest)
}

if (sub == "heatmap") {
  opt <- parse_common(rest)
  d <- read.csv(opt$inp, stringsAsFactors = FALSE)
  # expects: cohort, gene_set, endpoint_tp (or timepoint), diff_NR_minus_R,
  #          p_wilcox, fdr_wilcox
  tcol <- if ("timepoint" %in% names(d)) "timepoint" else "endpoint_tp"
  if (!"mark" %in% names(d))
    d$mark <- mapply(sig_mark, d$p_wilcox, d[["fdr_wilcox"]])
  d$facet <- d$cohort
  p <- ggplot(d, aes(x = factor(.data[[tcol]]), y = gene_set,
                     fill = diff_NR_minus_R)) +
    geom_tile(color = "grey85") +
    geom_text(aes(label = mark), size = 4, vjust = 0.75) +
    facet_wrap(~facet, nrow = 1, scales = "free_x") +
    scale_fill_gradient2(low = COL_R, mid = "white", high = COL_NR, midpoint = 0,
                         limits = c(-0.6, 0.6), oob = scales::squish,
                         name = "dGSVA\n(NR - R)") +
    labs(title = "Change-from-baseline dGSVA: non-responders vs responders",
         subtitle = "orange = higher in non-responders; * nominal p<0.05, ** FDR<0.05",
         x = "on-treatment timepoint", y = NULL) +
    base_theme()
  save_both(p, opt$prefix, w = 11, h = 6.5)

} else if (sub == "volcano") {
  opt <- parse_common(rest, list(
    make_option("--sig-genes", type = "character", default = NULL, dest = "sig_genes")))
  d <- read.csv(opt$inp, stringsAsFactors = FALSE)  # gene, logFC, P.Value, adj.P.Val
  d$neglog10p <- -log10(d$P.Value)
  d$hl <- "other"
  if (!is.null(opt$sig_genes) && file.exists(opt$sig_genes)) {
    sigs <- fromJSON(opt$sig_genes, simplifyVector = FALSE)
    nm <- names(sigs)
    for (i in seq_along(sigs)) {
      g <- toupper(unlist(sigs[[i]]))
      d$hl[toupper(d$gene) %in% g] <- nm[i]
    }
  }
  cols <- c(other = "grey75")
  hlnames <- setdiff(unique(d$hl), "other")
  pal <- c(COL_VA, COL_VB, COL_PD, COL_NR)
  for (i in seq_along(hlnames)) cols[hlnames[i]] <- pal[(i - 1) %% length(pal) + 1]
  p <- ggplot(d, aes(logFC, neglog10p, color = hl)) +
    geom_point(data = subset(d, hl == "other"), alpha = 0.3, size = 0.8) +
    geom_point(data = subset(d, hl != "other"), size = 1.8) +
    geom_hline(yintercept = -log10(0.05), linetype = "dashed", color = "grey50") +
    scale_color_manual(values = cols, name = "signature") +
    labs(title = "Per-gene differential expression (limma-voom): NR vs R",
         x = "log2 fold-change (NR vs R)", y = "-log10 p") +
    base_theme()
  save_both(p, opt$prefix, w = 8, h = 6)

} else if (sub == "pd") {
  opt <- parse_common(rest)
  d <- read.csv(opt$inp, stringsAsFactors = FALSE)  # gene_set, timepoint, mean, sem OR patient deltas
  if (all(c("mean", "sem", "timepoint", "gene_set") %in% names(d))) {
    p <- ggplot(d, aes(factor(timepoint), mean, group = gene_set)) +
      geom_line(color = COL_PD) +
      geom_point(color = COL_PD) +
      geom_errorbar(aes(ymin = mean - sem, ymax = mean + sem), width = 0.15,
                    color = COL_PD) +
      facet_wrap(~gene_set) +
      labs(title = "Pharmacodynamic change over treatment (GSVA)",
           x = "timepoint", y = "GSVA (mean +/- SEM)") + base_theme()
  } else {
    p <- ggplot(d, aes(factor(timepoint), value)) +
      geom_boxplot(outlier.size = 0.5, fill = COL_PD, alpha = 0.4) +
      facet_wrap(~gene_set) +
      labs(title = "Pharmacodynamic change over treatment (GSVA)",
           x = "timepoint", y = "GSVA / dGSVA") + base_theme()
  }
  save_both(p, opt$prefix, w = 8, h = 5)

} else if (sub == "trajectory") {
  opt <- parse_common(rest)
  d <- read.csv(opt$inp, stringsAsFactors = FALSE)
  # expects: gene_set, timepoint, response_group, mean, sem
  p <- ggplot(d, aes(factor(timepoint), mean, color = response_group,
                     group = response_group)) +
    geom_line(linewidth = 0.8) + geom_point(size = 1.8) +
    geom_errorbar(aes(ymin = mean - sem, ymax = mean + sem), width = 0.15) +
    facet_wrap(~gene_set, scales = "free_y") +
    scale_color_manual(values = c(NR = COL_NR, R = COL_R), name = "response") +
    labs(title = "Change-from-baseline dGSVA trajectories (GSVA): NR vs R",
         x = "on-treatment timepoint", y = "dGSVA (mean +/- SEM)") + base_theme()
  save_both(p, opt$prefix, w = 9, h = 6)

} else {
  stop(sprintf("unknown subcommand '%s' (use heatmap|volcano|pd|trajectory)", sub))
}
