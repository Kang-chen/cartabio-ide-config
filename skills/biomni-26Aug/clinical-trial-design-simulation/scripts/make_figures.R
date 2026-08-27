###############################################################################
# make_figures.R -- publication-quality figures for a simulated trial design.
#
# ENDPOINT-AGNOSTIC. Reads two tidy CSVs produced by run_grid.R:
#   * operating_characteristics.csv  (one row per effect scenario; run_grid())
#   * sensitivity_analysis.csv       (one row per swept value; sensitivity_grid())
# and produces a consistent figure set (PNG @150dpi + editable SVG) using an
# Okabe-Ito colorblind-safe palette and Liberation Sans (Arial-metric).
#
# Figures produced (only those whose inputs are present are drawn):
#   fig_power_by_scenario : power_F / power_S / power_any across effect scenarios
#   fig_expected_n        : E[N] and E[duration] across scenarios
#   fig_adaptations       : P(enrich), P(futility), P(efficacy), P(SSR) by scenario
#   fig_sensitivity_power : power vs the swept parameter (if sensitivity CSV given)
#   fig_sensitivity_n     : E[N]/E[duration] vs the swept parameter
#
# Usage (Rscript):
#   Rscript make_figures.R <oc_csv> <fig_dir> [sensitivity_csv]
#
# Nothing here uses real patient data.
# Author: Biomni (Phylo) | Language: R
###############################################################################

suppressMessages({
  library(data.table); library(ggplot2)
})
has_svglite <- requireNamespace("svglite", quietly = TRUE)

## Okabe-Ito colorblind-safe palette
CB <- c("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000")

base_theme <- theme_bw(base_size = 12) +
  theme(text = element_text(family = "Liberation Sans"),
        panel.grid.minor = element_blank(),
        legend.position = "bottom",
        strip.background = element_rect(fill = "#F0F0F0", colour = "grey70"),
        plot.title = element_text(face = "bold", size = 13))

save_fig <- function(p, name, fig_dir, w = 7.4, h = 5.0) {
  dir.create(fig_dir, showWarnings = FALSE, recursive = TRUE)
  ggsave(file.path(fig_dir, paste0(name, ".png")), p, width = w, height = h,
         dpi = 150, bg = "white")
  if (has_svglite) {
    ggsave(file.path(fig_dir, paste0(name, ".svg")), p, width = w, height = h,
           device = svglite::svglite, bg = "white")
  } else {
    ggsave(file.path(fig_dir, paste0(name, ".svg")), p, width = w, height = h, bg = "white")
  }
  cat("saved", name, "\n")
}

# keep scenario order as given in the CSV (factor by first appearance)
.ord_factor <- function(x) factor(x, levels = unique(x))

## ---------------------------------------------------------------------------
make_figures <- function(oc_csv, fig_dir, sensitivity_csv = NULL) {
  oc <- fread(oc_csv)
  stopifnot(nrow(oc) >= 1, "scenario" %in% names(oc))
  oc[, scenario := .ord_factor(scenario)]

  ## Detect whether the design has a genuine biomarker subgroup. The analysis
  ## hypotheses are defined ONCE by the design; the figures must reflect exactly
  ## that. If there is no H_S column, or H_S / "Any" are identical to the full
  ## population across all scenarios (prevalence == 1 / no enrichment), the design
  ## is single-hypothesis and only the full-population series is drawn.
  .single_hyp_oc <- function(dt) {
    if (!("power_S" %in% names(dt))) return(TRUE)
    fn <- function(a, b) all(abs(as.numeric(a) - as.numeric(b)) < 1e-9, na.rm = TRUE)
    eqS <- fn(dt$power_S, dt$power_F)
    eqA <- if ("power_any" %in% names(dt)) fn(dt$power_any, dt$power_F) else TRUE
    isTRUE(eqS && eqA)
  }
  single_hyp <- .single_hyp_oc(oc)

  ## ---- FIG 1: power by scenario ------------------------------------------
  if (single_hyp) {
    p1 <- ggplot(oc, aes(scenario, power_F)) +
      geom_col(width = 0.6, fill = CB[1]) +
      geom_hline(yintercept = 0.80, linetype = "dashed", colour = "grey30") +
      annotate("text", x = 0.6, y = 0.83, label = "80% power", hjust = 0,
               size = 3, colour = "grey30", family = "Liberation Sans") +
      scale_y_continuous(limits = c(0, 1), expand = expansion(c(0, 0.02))) +
      labs(x = NULL, y = "Rejection probability", title = "Power by scenario") +
      base_theme + theme(axis.text.x = element_text(angle = 25, hjust = 1))
  } else {
    pw <- melt(oc, id.vars = "scenario",
               measure.vars = intersect(c("power_any", "power_F", "power_S"), names(oc)),
               variable.name = "hyp", value.name = "power")
    pw[, hyp := factor(hyp, levels = c("power_any", "power_F", "power_S"),
                       labels = c("Any (F or S)", "Full population", "Biomarker+ subgroup"))]
    p1 <- ggplot(pw, aes(scenario, power, fill = hyp)) +
      geom_col(position = position_dodge(width = 0.8), width = 0.75) +
      geom_hline(yintercept = c(0.025, 0.80), linetype = c("dotted", "dashed"),
                 colour = c("grey40", "grey30")) +
      annotate("text", x = 0.6, y = 0.83, label = "80% power", hjust = 0,
               size = 3, colour = "grey30", family = "Liberation Sans") +
      scale_fill_manual(values = CB[c(7, 1, 2)], name = NULL) +
      scale_y_continuous(limits = c(0, 1), expand = expansion(c(0, 0.02))) +
      labs(x = NULL, y = "Rejection probability", title = "Power by scenario") +
      base_theme + theme(axis.text.x = element_text(angle = 25, hjust = 1))
  }
  save_fig(p1, "fig_power_by_scenario", fig_dir)

  ## ---- FIG 2: expected N and duration ------------------------------------
  if (all(c("E_N", "E_duration") %in% names(oc))) {
    dn <- melt(oc, id.vars = "scenario",
               measure.vars = c("E_N", "E_duration"),
               variable.name = "metric", value.name = "value")
    dn[, metric := factor(metric, levels = c("E_N", "E_duration"),
                          labels = c("Expected sample size E[N]",
                                     "Expected duration E[T] (months)"))]
    p2 <- ggplot(dn, aes(scenario, value, fill = metric)) +
      geom_col(width = 0.7) +
      facet_wrap(~ metric, scales = "free_y") +
      scale_fill_manual(values = CB[c(1, 3)], guide = "none") +
      labs(x = NULL, y = NULL, title = "Expected sample size and duration") +
      base_theme + theme(axis.text.x = element_text(angle = 25, hjust = 1))
    save_fig(p2, "fig_expected_n", fig_dir, w = 8.0)
  }

  ## ---- FIG 3: adaptation probabilities -----------------------------------
  ## Only include adaptation types that actually occur (nonzero in >=1 scenario),
  ## so a design without a given adaptation (e.g. no enrichment when there is no
  ## biomarker subgroup) does not show a misleading zero-height/labelled series.
  adapt_all <- c("p_enrich", "p_futility", "p_efficacy", "p_ssr")
  adapt_lab <- c(p_enrich = "Enrichment", p_futility = "Futility stop",
                 p_efficacy = "Early efficacy", p_ssr = "SSR fired")
  adapt_col <- c(p_enrich = CB[3], p_futility = CB[2], p_efficacy = CB[1], p_ssr = CB[5])
  adapt_cols <- intersect(adapt_all, names(oc))
  if (length(adapt_cols)) {
    nz <- adapt_cols[colSums(oc[, ..adapt_cols], na.rm = TRUE) > 0]
    if (length(nz)) {
      da <- melt(oc, id.vars = "scenario", measure.vars = nz,
                 variable.name = "event", value.name = "prob")
      da[, event := factor(event, levels = nz, labels = unname(adapt_lab[nz]))]
      da <- da[!is.na(event)]
      p3 <- ggplot(da, aes(scenario, prob, fill = event)) +
        geom_col(position = position_dodge(width = 0.8), width = 0.75) +
        scale_fill_manual(values = unname(adapt_col[nz]), name = NULL) +
        scale_y_continuous(limits = c(0, 1), expand = expansion(c(0, 0.02))) +
        labs(x = NULL, y = "Probability", title = "Adaptive-decision probabilities") +
        base_theme + theme(axis.text.x = element_text(angle = 25, hjust = 1))
      save_fig(p3, "fig_adaptations", fig_dir)
    }
  }

  ## ---- Sensitivity figures (optional) ------------------------------------
  if (!is.null(sensitivity_csv) && file.exists(sensitivity_csv)) {
    ss <- fread(sensitivity_csv)
    if (nrow(ss) && all(c("param_value") %in% names(ss))) {
      pname <- if ("param_name" %in% names(ss)) ss$param_name[1] else "parameter"
      single_hyp_ss <- .single_hyp_oc(ss)
      if (single_hyp_ss) {
        p4 <- ggplot(ss, aes(param_value, power_F)) +
          geom_line(linewidth = 0.9, colour = CB[1]) + geom_point(size = 2, colour = CB[1]) +
          geom_hline(yintercept = 0.80, linetype = "dashed", colour = "grey30") +
          scale_y_continuous(limits = c(0, 1)) +
          labs(x = pname, y = "Rejection probability",
               title = sprintf("Sensitivity of power to %s", pname)) +
          base_theme
      } else {
        spw <- melt(ss, id.vars = "param_value",
                    measure.vars = intersect(c("power_any", "power_F", "power_S"), names(ss)),
                    variable.name = "hyp", value.name = "power")
        spw[, hyp := factor(hyp, levels = c("power_any", "power_F", "power_S"),
                            labels = c("Any (F or S)", "Full population", "Biomarker+ subgroup"))]
        p4 <- ggplot(spw, aes(param_value, power, colour = hyp)) +
          geom_line(linewidth = 0.9) + geom_point(size = 2) +
          geom_hline(yintercept = 0.80, linetype = "dashed", colour = "grey30") +
          scale_colour_manual(values = CB[c(7, 1, 2)], name = NULL) +
          scale_y_continuous(limits = c(0, 1)) +
          labs(x = pname, y = "Rejection probability",
               title = sprintf("Sensitivity of power to %s", pname)) +
          base_theme
      }
      save_fig(p4, "fig_sensitivity_power", fig_dir)

      if (all(c("E_N", "E_duration") %in% names(ss))) {
        sdn <- melt(ss, id.vars = "param_value",
                    measure.vars = c("E_N", "E_duration"),
                    variable.name = "metric", value.name = "value")
        sdn[, metric := factor(metric, levels = c("E_N", "E_duration"),
                              labels = c("E[N]", "E[T] (months)"))]
        p5 <- ggplot(sdn, aes(param_value, value, colour = metric)) +
          geom_line(linewidth = 0.9) + geom_point(size = 2) +
          facet_wrap(~ metric, scales = "free_y") +
          scale_colour_manual(values = CB[c(1, 3)], guide = "none") +
          labs(x = pname, y = NULL,
               title = sprintf("Sensitivity of sample size / duration to %s", pname)) +
          base_theme
        save_fig(p5, "fig_sensitivity_n", fig_dir, w = 8.0)
      }
    }
  }
  invisible(TRUE)
}

## ---- CLI entry -------------------------------------------------------------
## Only run when THIS file is the script passed to Rscript (not when sourced).
.is_main <- function(fname) {
  ca <- commandArgs(FALSE)
  f  <- sub("--file=", "", grep("--file=", ca, value = TRUE))
  length(f) == 1 && identical(basename(f), fname)
}
if (.is_main("make_figures.R")) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) >= 2) {
    sens <- if (length(args) >= 3) args[3] else NULL
    make_figures(args[1], args[2], sens)
  }
}
