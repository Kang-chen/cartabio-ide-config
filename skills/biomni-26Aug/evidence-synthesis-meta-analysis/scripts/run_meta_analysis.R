#!/usr/bin/env Rscript
# ==============================================================================
# run_meta_analysis.R  --  generalizable random-effects meta-analysis engine
# Part of the `evidence-synthesis-meta-analysis` Biomni skill.
#
# Reads a config block + an extraction_table.csv (schema in
# references/extraction_table_template.csv) and produces: pooled model, tidy
# results CSVs, robustness diagnostics, and 4 media-check-ready figures.
#
# USAGE (edit the CONFIG block, or override via environment variables):
#   Rscript run_meta_analysis.R
# Env overrides (optional): META_INPUT, META_OUTDIR, META_MEASURE, META_SUBGROUP,
#   META_TOPIC, META_TAU (REML|DL|PM), META_HK (TRUE|FALSE), META_FAVOURS
# ==============================================================================

## ---------------- CONFIG (defaults; overridable by env vars) -----------------
cfg <- list(
  input    = Sys.getenv("META_INPUT",    "data/extraction_table.csv"),
  outdir   = Sys.getenv("META_OUTDIR",   "."),                # figures/ and data/ created under here
  measure  = Sys.getenv("META_MEASURE",  "MD"),               # MD|SMD|OR|RR|HR  (taken from table if column present)
  subgroup = Sys.getenv("META_SUBGROUP", "subgroup"),         # column name for subgroups; "" or missing = none
  topic    = Sys.getenv("META_TOPIC",    "Meta-analysis"),
  tau      = Sys.getenv("META_TAU",      "REML"),             # REML|DL|PM|EB ...
  hk       = toupper(Sys.getenv("META_HK", "TRUE")) == "TRUE",# Hartung-Knapp CI
  favours  = Sys.getenv("META_FAVOURS",  "Favours treatment | Favours control")
)

## ---------------- Environment ------------------------------------------------
# meta/metafor may live in a session-local lib; add common locations harmlessly.
for (p in c("/workspace/.Rlib", file.path(Sys.getenv("HOME"), ".Rlib")))
  if (dir.exists(p)) .libPaths(c(p, .libPaths()))
need <- c("meta", "metafor")
miss <- need[!vapply(need, requireNamespace, logical(1), quietly = TRUE)]
if (length(miss)) {
  dir.create("/workspace/.Rlib", showWarnings = FALSE, recursive = TRUE)
  install.packages(miss, lib = "/workspace/.Rlib",
                   repos = "https://cloud.r-project.org")
  .libPaths(c("/workspace/.Rlib", .libPaths()))
}
suppressMessages({library(meta); library(metafor)})
has_ggplot <- requireNamespace("ggplot2", quietly = TRUE)

datadir <- file.path(cfg$outdir, "data"); figdir <- file.path(cfg$outdir, "figures")
dir.create(datadir, showWarnings = FALSE, recursive = TRUE)
dir.create(figdir,  showWarnings = FALSE, recursive = TRUE)

## ---------------- Load & validate extraction table ---------------------------
raw <- read.csv(cfg$input, stringsAsFactors = FALSE, comment.char = "#")
raw <- raw[!is.na(raw$effect) & nzchar(trimws(as.character(raw$study))), ]
if (nrow(raw) < 2) stop("Need >= 2 studies with an effect estimate to pool.")

# Measure: prefer per-row 'measure' column if present & consistent, else cfg.
measure <- cfg$measure
if ("measure" %in% names(raw)) {
  ms <- unique(toupper(trimws(raw$measure[nzchar(raw$measure)])))
  if (length(ms) > 1) stop("Multiple effect measures in one table (", paste(ms, collapse=","),
                            "). One measure per meta-analysis.")
  if (length(ms) == 1) measure <- ms
}
measure <- toupper(measure)
is_ratio <- measure %in% c("OR", "RR", "HR")

# Effect + SE on the ANALYSIS scale (log for ratios).
qn <- qnorm(0.975)
te <- if (is_ratio) log(raw$effect) else raw$effect
se <- suppressWarnings(as.numeric(raw$se))
need_se <- is.na(se)
if (any(need_se)) {
  lo <- raw$ci_lo[need_se]; hi <- raw$ci_hi[need_se]
  if (any(is.na(lo) | is.na(hi)))
    stop("Rows without SE must have ci_lo and ci_hi to derive SE: rows ",
         paste(which(need_se)[is.na(lo) | is.na(hi)], collapse=", "))
  se[need_se] <- if (is_ratio) (log(hi) - log(lo)) / (2*qn) else (hi - lo) / (2*qn)
}
if (any(is.na(se) | se <= 0)) stop("Non-positive / missing SE after derivation.")

# Subgroup column (optional)
sg_col <- cfg$subgroup
use_sg <- nzchar(sg_col) && sg_col %in% names(raw) && any(nzchar(raw[[sg_col]]))
sg <- if (use_sg) raw[[sg_col]] else NULL

# Order rows by effect for a readable forest plot
ord <- order(te)
raw <- raw[ord, ]; te <- te[ord]; se <- se[ord]; if (use_sg) sg <- sg[ord]

## ---------------- Fit pooled model (meta::metagen) ---------------------------
m <- metagen(TE = te, seTE = se, studlab = raw$study, sm = measure,
             method.tau = cfg$tau,
             method.random.ci = if (cfg$hk) "HK" else "classic",
             prediction = TRUE,
             subgroup = sg, subgroup.name = if (use_sg) sg_col else NULL)
saveRDS(m, file.path(datadir, "meta_model.rds"))

bt <- function(x) if (is_ratio) exp(x) else x   # back-transform for display
fmt <- function(x) formatC(bt(x), digits = if (is_ratio) 3 else 2, format = "f")

## ---------------- Tidy results CSV -------------------------------------------
res <- data.frame(
  level = "Overall (random effects)", k = m$k,
  effect = fmt(m$TE.random), ci_lo = fmt(m$lower.random), ci_hi = fmt(m$upper.random),
  stat = round(m$statistic.random, 3), pval = signif(m$pval.random, 3),
  stringsAsFactors = FALSE)
res <- rbind(res, data.frame(level = "Overall (common effect, reference)", k = m$k,
  effect = fmt(m$TE.common), ci_lo = fmt(m$lower.common), ci_hi = fmt(m$upper.common),
  stat = round(m$statistic.common, 3), pval = signif(m$pval.common, 3)))
if (use_sg && !is.null(m$TE.random.w)) {
  res <- rbind(res, data.frame(
    level = paste0("Subgroup: ", names(m$TE.random.w)), k = as.integer(m$k.w),
    effect = fmt(m$TE.random.w), ci_lo = fmt(m$lower.random.w), ci_hi = fmt(m$upper.random.w),
    stat = NA, pval = NA))
}
# heterogeneity + PI as extra rows
het <- data.frame(level = c("tau^2","tau","I^2 (%)","I^2 low","I^2 high","H",
                            "Cochran Q","Q df","Q pval","PI low","PI high",
                            if (use_sg) c("Q between","Q between df","Q between pval")),
                  k = NA,
                  effect = c(round(m$tau2,3), round(m$tau,3), round(m$I2*100,1),
                             round(m$lower.I2*100,1), round(m$upper.I2*100,1), round(m$H,2),
                             round(m$Q,2), m$df.Q, signif(m$pval.Q,3),
                             fmt(m$lower.predict), fmt(m$upper.predict),
                             if (use_sg) c(round(m$Q.b.random,2), m$df.Q.b, signif(m$pval.Q.b.random,3))),
                  ci_lo = NA, ci_hi = NA, stat = NA, pval = NA)
write.csv(rbind(res, het), file.path(datadir, "meta_results.csv"), row.names = FALSE)

## ---------------- Robustness: leave-one-out ----------------------------------
loo <- metainf(m, pooled = "random")
loo_df <- data.frame(omitted = loo$studlab,
                     effect = fmt(loo$TE), ci_lo = fmt(loo$lower), ci_hi = fmt(loo$upper),
                     I2 = round(loo$I2*100,1), tau2 = round(loo$tau2,3))
write.csv(loo_df, file.path(datadir, "leaveoneout.csv"), row.names = FALSE)

## ---------------- Robustness: influence (metafor) ----------------------------
res_rma <- rma(yi = te, sei = se, method = cfg$tau)
inf <- influence(res_rma)
inf_df <- data.frame(study = raw$study,
                     rstudent = round(inf$inf$rstudent, 3),
                     cooks_d = round(inf$inf$cook.d, 3),
                     dffits = round(inf$inf$dffits, 3),
                     influential = ifelse(is.na(inf$is.infl), FALSE, inf$is.infl))
write.csv(inf_df, file.path(datadir, "influence_diagnostics.csv"), row.names = FALSE)

## ---------------- Robustness: Egger (guarded by k >= 10) ---------------------
egg <- tryCatch(regtest(res_rma), error = function(e) NULL)
egger_line <- if (is.null(egg)) "Egger's test: not computable." else
  sprintf("Egger's test: z/t = %.2f, p = %.3f. %s",
          egg$zval, egg$pval,
          if (m$k >= 10) "k >= 10: interpretable as a small-study-effects signal."
          else "k < 10: UNDERPOWERED; report for completeness only, do NOT declare publication bias.")
writeLines(c(egger_line, sprintf("k = %d studies.", m$k)),
           file.path(datadir, "smallstudy_test.txt"))

## ---------------- Figures ----------------------------------------------------
okabe <- c("#0072B2","#009E73","#E69F00","#F0E442","#D55E00","#56B4E9","#CC79A7","#999999")
xlab_txt <- switch(measure, MD="Mean difference", SMD="Standardized mean difference",
                   OR="Odds ratio", RR="Risk ratio", HR="Hazard ratio")
fav <- strsplit(cfg$favours, "\\s*\\|\\s*")[[1]]

# 1) Forest plot (meta's own renderer -> robust, publication-style)
png(file.path(figdir, "forest_main.png"), width = 2000, height = 130 + 90*nrow(raw), res = 200)
forest(m, prediction = TRUE, print.tau2 = TRUE, print.I2 = TRUE,
       leftcols = c("studlab"), leftlabs = c("Study"),
       smlab = xlab_txt, xlab = paste(fav[1], "  <->  ", if(length(fav)>1) fav[2] else ""),
       col.square = "#0072B2", col.diamond = "#D55E00", col.predict = "#333333")
invisible(dev.off())
svg(file.path(figdir, "forest_main.svg"), width = 10, height = (130 + 90*nrow(raw))/200)
forest(m, prediction = TRUE, print.tau2 = TRUE, print.I2 = TRUE,
       leftcols = c("studlab"), leftlabs = c("Study"), smlab = xlab_txt,
       col.square = "#0072B2", col.diamond = "#D55E00", col.predict = "#333333")
invisible(dev.off())

# 2) Funnel plot (agent/subgroup-coloured; white panel; solid reference line)
sg_fac <- if (use_sg) factor(sg) else factor(rep("Studies", nrow(raw)))
cols   <- okabe[as.integer(sg_fac)]
draw_funnel <- function() {
  par(mar = c(4.6, 4.8, 1.4, 1.4), family = "sans")
  funnel(res_rma, level = 95, refline = as.numeric(coef(res_rma)),
         back = "white", shade = "#BDBDBD", hlines = NA,
         xlab = xlab_txt, ylab = "Standard error",
         pch = 21, bg = cols, col = "black", cex = 1.6, las = 1)
  abline(v = as.numeric(coef(res_rma)), lwd = 2.4, col = "#333333")
  if (use_sg) legend("topright", legend = levels(sg_fac), pt.bg = okabe[seq_along(levels(sg_fac))],
                     pch = 21, col = "black", bty = "n", cex = 0.85, title = sg_col)
}
png(file.path(figdir, "funnel.png"), width = 1500, height = 1260, res = 200); draw_funnel(); invisible(dev.off())
svg(file.path(figdir, "funnel.svg"), width = 7.5, height = 6.3); draw_funnel(); invisible(dev.off())

# 3) Leave-one-out + 4) Heterogeneity/influence panel (ggplot if available, else base)
loo_plot <- loo   # meta metainf object
if (has_ggplot) {
  suppressMessages(library(ggplot2))
  fam <- "Liberation Sans"
  d <- data.frame(omitted = factor(loo$studlab, levels = rev(loo$studlab)),
                  est = bt(loo$TE), lo = bt(loo$lower), hi = bt(loo$upper))
  pooled_est <- bt(m$TE.random); pooled_lo <- bt(m$lower.random); pooled_hi <- bt(m$upper.random)
  g1 <- ggplot(d, aes(est, omitted)) +
    annotate("rect", xmin = pooled_lo, xmax = pooled_hi, ymin = -Inf, ymax = Inf, alpha = .15, fill = "#0072B2") +
    geom_vline(xintercept = pooled_est, linetype = "dashed", colour = "#0072B2") +
    geom_errorbar(aes(xmin = lo, xmax = hi), width = .25, orientation = "y", colour = "#333333") +
    geom_point(size = 2.4, colour = "#D55E00") +
    labs(x = paste0("Pooled ", measure, " (one study omitted)"), y = NULL,
         title = "Leave-one-out sensitivity",
         subtitle = "Dashed line / band = full-model estimate and 95% CI") +
    theme_minimal(base_size = 11) +
    theme(text = element_text(family = fam), plot.margin = margin(10,28,10,14))
  ggsave(file.path(figdir, "leaveoneout.png"), g1, width = 8, height = 3 + .32*nrow(d), dpi = 200)
  ggsave(file.path(figdir, "leaveoneout.svg"), g1, width = 8, height = 3 + .32*nrow(d))

  # heterogeneity panel: (A) effects by subgroup, (B) Cook's distance
  dA <- data.frame(study = factor(raw$study, levels = rev(raw$study)),
                   est = bt(te), lo = bt(te - qn*se), hi = bt(te + qn*se),
                   grp = sg_fac)
  gA <- ggplot(dA, aes(est, study, colour = grp)) +
    geom_vline(xintercept = bt(m$TE.random), linetype = "dashed", colour = "grey40") +
    geom_errorbar(aes(xmin = lo, xmax = hi), width = .25, orientation = "y") +
    geom_point(size = 2.3) + scale_colour_manual(values = okabe, name = if (use_sg) sg_col else NULL) +
    labs(x = xlab_txt, y = NULL, title = "A. Study effects") +
    theme_minimal(base_size = 10) + theme(text = element_text(family = fam),
      legend.position = if (use_sg) "bottom" else "none")
  dB <- data.frame(study = factor(raw$study, levels = rev(raw$study)), cook = inf$inf$cook.d)
  gB <- ggplot(dB, aes(cook, study)) +
    geom_segment(aes(x = 0, xend = cook, yend = study), colour = "#999999") +
    geom_point(size = 2.3, colour = "#D55E00") +
    labs(x = "Cook's distance", y = NULL, title = "B. Influence") +
    theme_minimal(base_size = 10) + theme(text = element_text(family = fam))
  if (requireNamespace("patchwork", quietly = TRUE)) {
    library(patchwork); panel <- gA + gB + plot_layout(widths = c(1.4, 1))
    ggsave(file.path(figdir, "heterogeneity_panel.png"), panel, width = 10, height = 3 + .3*nrow(raw), dpi = 200)
    ggsave(file.path(figdir, "heterogeneity_panel.svg"), panel, width = 10, height = 3 + .3*nrow(raw))
  } else {
    ggsave(file.path(figdir, "heterogeneity_panel.png"), gA, width = 7, height = 3 + .3*nrow(raw), dpi = 200)
  }
} else {
  # base-R fallbacks
  png(file.path(figdir, "leaveoneout.png"), width = 1600, height = 200 + 80*nrow(raw), res = 200)
  forest(loo); invisible(dev.off())
  png(file.path(figdir, "heterogeneity_panel.png"), width = 1400, height = 200 + 80*nrow(raw), res = 200)
  plot(inf); invisible(dev.off())
}

## ---------------- Console summary --------------------------------------------
cat("\n================ META-ANALYSIS COMPLETE ================\n")
cat(sprintf("Measure: %s | k = %d studies | model: random-effects %s%s\n",
            measure, m$k, cfg$tau, if (cfg$hk) " + Hartung-Knapp" else ""))
cat(sprintf("Pooled %s = %s (95%% CI %s to %s), p = %s\n", measure,
            fmt(m$TE.random), fmt(m$lower.random), fmt(m$upper.random), signif(m$pval.random,3)))
cat(sprintf("Heterogeneity: I^2 = %.1f%%, tau^2 = %.3f, Q = %.1f (df=%d, p=%s)\n",
            m$I2*100, m$tau2, m$Q, m$df.Q, signif(m$pval.Q,3)))
cat(sprintf("95%% prediction interval: %s to %s\n", fmt(m$lower.predict), fmt(m$upper.predict)))
if (use_sg) cat(sprintf("Between-subgroup Q = %.1f (df=%d, p=%s)\n",
                        m$Q.b.random, m$df.Q.b, signif(m$pval.Q.b.random,3)))
cat(egger_line, "\n")
cat("Outputs -> ", normalizePath(datadir), " and ", normalizePath(figdir), "\n", sep="")
