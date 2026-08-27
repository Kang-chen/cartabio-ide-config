#!/usr/bin/env Rscript
# =====================================================================
# make_figures.R  --  adaptive, artifact-driven figures for the
# methods-landscape-review skill.
#
# NOTHING is hardcoded. Every figure is rendered from the curated
# artifacts you produced during screening/extraction, so every plotted
# value traces back to a source paper. If an artifact is absent, its
# figure is silently skipped (the report builder only embeds what exists).
#
# MODE is auto-detected:
#   comparison mode  -> comparison_matrix.csv / performance_claims.json /
#                       benchmark_catalog.json present
#   topic mode       -> theme_table.csv present
# Both may run if both artifact sets exist.
#
# Usage:
#   Rscript make_figures.R --run <dir> [--out <dir>] [--title-prefix "..."]
#   <dir> holds the artifacts; --out defaults to <dir> (write figs beside them)
#
# Output: fig_*.png (dpi 300) + fig_*.svg, plus a manifest fig_manifest.csv
#         listing {file, mode, caption} for the report builder to consume.
#
# Style: Okabe-Ito colorblind-safe palette, Liberation Sans, theme_prism.
# NOTE: write figures DIRECTLY to the target dir. R's file.copy() to
#       /mnt/results yields 0-byte files, so never stage-then-copy in R.
# =====================================================================

suppressPackageStartupMessages({
  library(ggplot2); library(dplyr); library(tidyr)
  have_prism     <- requireNamespace("ggprism",   quietly = TRUE)
  have_patchwork <- requireNamespace("patchwork", quietly = TRUE)
  library(jsonlite)
})

# ---------- args ----------
args <- commandArgs(trailingOnly = TRUE)
getarg <- function(flag, default = NULL) {
  i <- match(flag, args); if (!is.na(i) && i < length(args)) args[i + 1] else default
}
run_dir <- getarg("--run", ".")
out_dir <- getarg("--out", run_dir)
tprefix <- getarg("--title-prefix", "")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

# ---------- palette / theme ----------
# Okabe-Ito (colorblind-safe). Recycled by index for arbitrary categories.
OKABE <- c("#0072B2","#D55E00","#009E73","#CC79A7","#E69F00",
           "#56B4E9","#F0E442","#999999","#000000")
pal_n <- function(n) rep(OKABE, length.out = n)

base_theme <- (if (have_prism) ggprism::theme_prism(base_size = 11) else theme_minimal(base_size = 11)) +
  theme(text          = element_text(family = "Liberation Sans"),
        plot.title    = element_text(size = 12, face = "bold"),
        plot.subtitle = element_text(size = 8.5, color = "grey30"),
        plot.caption  = element_text(size = 7, color = "grey40", hjust = 0),
        legend.title  = element_text(size = 9))

manifest <- data.frame(file = character(), mode = character(),
                       caption = character(), stringsAsFactors = FALSE)
save_fig <- function(plot, stem, mode, caption, w, h) {
  png <- file.path(out_dir, paste0(stem, ".png"))
  svg <- file.path(out_dir, paste0(stem, ".svg"))
  ggsave(png, plot, width = w, height = h, dpi = 300)
  try(ggsave(svg, plot, width = w, height = h), silent = TRUE)
  manifest <<- rbind(manifest, data.frame(file = basename(png), mode = mode,
                                          caption = caption, stringsAsFactors = FALSE))
  cat(sprintf("  saved %s\n", basename(png)))
}
rd <- function(f) file.path(run_dir, f)
ex <- function(f) file.exists(rd(f))
wrap <- function(x, width = 22) vapply(x, function(s)
  paste(strwrap(s, width = width), collapse = "\n"), character(1))

# =====================================================================
# COMPARISON MODE
# =====================================================================

## ---- FIG: comparison matrix as a qualitative table-heatmap ----
# comparison_matrix.csv: first column = Dimension, remaining columns = methods.
# Cells are short text descriptors (e.g. "Negative binomial GLM"). We render
# a labeled tile grid (no numeric scale) so structural differences are legible.
if (ex("comparison_matrix.csv")) {
  cm <- read.csv(rd("comparison_matrix.csv"), check.names = FALSE,
                 stringsAsFactors = FALSE)
  dim_col <- names(cm)[1]
  methods <- names(cm)[-1]
  long <- pivot_longer(cm, all_of(methods), names_to = "method", values_to = "value")
  long$method    <- factor(long$method, levels = methods)
  long$dimension <- factor(long[[dim_col]], levels = rev(cm[[dim_col]]))
  # tile fill by method (structural, not a performance score)
  fillmap <- setNames(pal_n(length(methods)), methods)
  p <- ggplot(long, aes(method, dimension)) +
    geom_tile(aes(fill = method), color = "white", linewidth = 1.1, alpha = 0.16) +
    geom_text(aes(label = wrap(value, 20)), size = 2.5,
              family = "Liberation Sans", lineheight = 0.82, color = "grey12") +
    scale_fill_manual(values = fillmap, guide = "none") +
    labs(title = paste0(tprefix, "Method characteristics matrix"),
         subtitle = "Structural / algorithmic comparison across dimensions",
         x = NULL, y = NULL,
         caption = "Descriptors transcribed from method papers and benchmark syntheses (see references).") +
    base_theme +
    theme(axis.text.x = element_text(face = "bold", size = 9.5),
          axis.text.y = element_text(size = 8.5, lineheight = 0.8))
  nrow_cm <- nrow(cm)
  save_fig(p, "fig_comparison_matrix", "comparison",
           "Structural comparison of methods across evaluation dimensions.",
           w = max(6.5, 2 + 1.9 * length(methods)),
           h = max(4.5, 0.62 * nrow_cm + 1.6))
}

## ---- FIG: performance claims scorecard (ordinal, qualitative) ----
# performance_claims.json: method, dimension, finding, benchmark, source, doi,
# evidence_thickness. We derive an ORDINAL 1-3 direction ONLY from the finding
# text via transparent keyword rules, and print the verbatim finding in-cell.
# The caption states explicitly this is a qualitative summary, not re-measured.
if (ex("performance_claims.json")) {
  pc <- fromJSON(rd("performance_claims.json"), simplifyDataFrame = TRUE)
  if (is.data.frame(pc) && nrow(pc) > 0 &&
      all(c("method","dimension","finding") %in% names(pc))) {
    score_finding <- function(txt) {
      t <- tolower(txt)
      pos <- c("superior","best","recommended","robust","well","good","higher tpr",
               "controls fdr","accurate","reproducible","favorable","strong","outperum",
               "outperform","top","most sensitive","preferred")
      neg <- c("inflat","liberal","fail","spurious","poor","worst","unfavorable",
               "false positive","not accurate","biased","overly","weak","struggle",
               "loses control","anti-conservative")
      hp <- any(vapply(pos, function(k) grepl(k, t, fixed = TRUE), logical(1)))
      hn <- any(vapply(neg, function(k) grepl(k, t, fixed = TRUE), logical(1)))
      if (hn && !hp) return(1L); if (hp && !hn) return(3L); return(2L)
    }
    pc$score <- vapply(pc$finding, score_finding, integer(1))
    # keep it legible: cap dimensions shown, order methods by appearance
    pc$method    <- factor(pc$method, levels = unique(pc$method))
    dim_levels   <- unique(pc$dimension)
    pc$dimension <- factor(pc$dimension, levels = rev(dim_levels))
    p <- ggplot(pc, aes(method, dimension, fill = factor(score))) +
      geom_tile(color = "white", linewidth = 1.1) +
      geom_text(aes(label = wrap(finding, 24)), size = 2.25,
                family = "Liberation Sans", lineheight = 0.8, color = "grey12") +
      scale_fill_manual(
        values = c("1" = "#F4C7C3", "2" = "#FCE8B2", "3" = "#C6E5C3"),
        labels = c("1" = "Unfavorable", "2" = "Mixed / neutral", "3" = "Favorable"),
        name = "Direction of finding", drop = FALSE) +
      labs(title = paste0(tprefix, "Benchmark-derived performance scorecard"),
           subtitle = "Direction inferred from finding text; cell shows the verbatim finding",
           x = NULL, y = NULL,
           caption = paste("Ordinal colors are a transparent keyword summary of published qualitative findings,",
                           "NOT a single re-run benchmark metric. Each cell traces to source + DOI in the claims table.")) +
      base_theme +
      theme(axis.text.x = element_text(face = "bold", size = 9.5),
            axis.text.y = element_text(size = 8, lineheight = 0.8),
            legend.position = "right")
    save_fig(p, "fig_performance_scorecard", "comparison",
             "Qualitative performance scorecard (direction inferred from finding text; verbatim findings shown).",
             w = max(7, 2.2 + 1.9 * nlevels(pc$method)),
             h = max(4.5, 0.7 * nlevels(pc$dimension) + 1.6))

    ## ---- FIG: evidence thickness per method ----
    # How much independent evidence backs claims about each method.
    if ("evidence_thickness" %in% names(pc)) {
      et_order <- c("head_to_head","multiple_benchmarks","single_benchmark",
                    "single_study","anecdotal")
      et <- pc %>%
        mutate(evidence_thickness = ifelse(is.na(evidence_thickness) | evidence_thickness == "",
                                           "unspecified", evidence_thickness)) %>%
        count(method, evidence_thickness, name = "n")
      present <- intersect(et_order, unique(et$evidence_thickness))
      extra   <- setdiff(unique(et$evidence_thickness), et_order)
      et$evidence_thickness <- factor(et$evidence_thickness, levels = c(present, extra))
      p2 <- ggplot(et, aes(method, n, fill = evidence_thickness)) +
        geom_col(width = 0.66, color = "white") +
        scale_fill_manual(values = pal_n(nlevels(et$evidence_thickness)),
                          name = "Evidence thickness") +
        labs(title = paste0(tprefix, "Evidence backing each method"),
             subtitle = "Number of extracted claims by strength of supporting evidence",
             x = NULL, y = "Number of claims",
             caption = "Evidence thickness assigned during extraction (head_to_head strongest).") +
        base_theme + theme(axis.text.x = element_text(face = "bold"))
      save_fig(p2, "fig_evidence_thickness", "comparison",
               "Count of extracted claims per method, colored by strength of supporting evidence.",
               w = max(6.5, 2 + 1.4 * nlevels(pc$method)), h = 4.8)
    }
  }
}

## ---- FIG: benchmark catalog overview ----
# benchmark_catalog.json: benchmark_name, benchmark_type, organism, truth_basis,
# key_metric, defining_paper, doi. Show the landscape of evidence sources.
if (ex("benchmark_catalog.json")) {
  bc <- fromJSON(rd("benchmark_catalog.json"), simplifyDataFrame = TRUE)
  if (is.data.frame(bc) && nrow(bc) > 0 && "benchmark_name" %in% names(bc)) {
    bc$benchmark_name <- factor(bc$benchmark_name, levels = rev(bc$benchmark_name))
    type_col <- if ("benchmark_type" %in% names(bc)) "benchmark_type" else NULL
    org_lab  <- if ("organism" %in% names(bc)) paste0("  [", bc$organism, "]") else ""
    bc$fillg <- if (!is.null(type_col)) bc[[type_col]] else "benchmark"
    p <- ggplot(bc, aes(y = benchmark_name, x = 1, fill = fillg)) +
      geom_col(width = 0.85, color = "white") +
      geom_text(aes(x = 0.02, label = paste0(as.character(benchmark_name), org_lab)),
                hjust = 0, size = 2.8, family = "Liberation Sans", color = "grey10") +
      scale_fill_manual(values = pal_n(length(unique(bc$fillg))),
                        name = "Benchmark type") +
      scale_x_continuous(expand = c(0, 0)) +
      labs(title = paste0(tprefix, "Benchmark evidence landscape"),
           subtitle = "Independent benchmarks / reference datasets underpinning the comparison",
           x = NULL, y = NULL,
           caption = "Each benchmark is a distinct source of ground truth (see catalog table for truth basis + DOI).") +
      base_theme +
      theme(axis.text.y = element_blank(), axis.ticks.y = element_blank(),
            axis.text.x = element_blank(), axis.ticks.x = element_blank(),
            panel.grid = element_blank(), legend.position = "right")
    save_fig(p, "fig_benchmark_catalog", "comparison",
             "Landscape of independent benchmarks / reference datasets underpinning the comparison.",
             w = 9, h = max(3.5, 0.55 * nrow(bc) + 1.6))
  }
}

# =====================================================================
# TOPIC MODE
# =====================================================================
# theme_table.csv (topic mode): theme, n_papers, [consensus_level],
# [evidence_quality]. consensus_level in {strong,moderate,weak,contested};
# evidence_quality in {high,moderate,low}. Only the columns present are used.
if (ex("theme_table.csv")) {
  tt <- read.csv(rd("theme_table.csv"), check.names = FALSE, stringsAsFactors = FALSE)
  if ("theme" %in% names(tt) && nrow(tt) > 0) {
    if (!"n_papers" %in% names(tt)) tt$n_papers <- 1
    tt$theme <- factor(tt$theme, levels = rev(tt$theme[order(tt$n_papers)]))

    ## ---- FIG: theme evidence map (papers per theme, colored by consensus) ----
    if ("consensus_level" %in% names(tt)) {
      cons_levels <- c("strong","moderate","weak","contested")
      tt$consensus_level <- factor(tt$consensus_level,
        levels = intersect(cons_levels, unique(tt$consensus_level)))
      cons_pal <- c(strong = "#009E73", moderate = "#56B4E9",
                    weak = "#E69F00", contested = "#D55E00")
      p <- ggplot(tt, aes(n_papers, theme, fill = consensus_level)) +
        geom_col(width = 0.72, color = "white") +
        scale_fill_manual(values = cons_pal, name = "Consensus", drop = FALSE) +
        labs(title = paste0(tprefix, "Evidence map by theme"),
             subtitle = "Papers per theme, colored by strength of consensus",
             x = "Number of papers", y = NULL,
             caption = "Themes and consensus levels assigned during screening/extraction; contested = active disagreement.") +
        base_theme
    } else {
      p <- ggplot(tt, aes(n_papers, theme)) +
        geom_col(width = 0.72, fill = OKABE[1], color = "white") +
        labs(title = paste0(tprefix, "Evidence map by theme"),
             subtitle = "Number of papers per theme",
             x = "Number of papers", y = NULL,
             caption = "Themes assigned during screening/extraction.") +
        base_theme
    }
    save_fig(p, "fig_theme_map", "topic",
             "Evidence map: number of papers per theme (colored by consensus where available).",
             w = 8.5, h = max(3.5, 0.5 * nrow(tt) + 1.6))

    ## ---- FIG: evidence-quality distribution ----
    if ("evidence_quality" %in% names(tt)) {
      eq_levels <- c("high","moderate","low")
      eqt <- tt %>%
        mutate(evidence_quality = factor(evidence_quality,
               levels = intersect(eq_levels, unique(evidence_quality)))) %>%
        count(evidence_quality, wt = n_papers, name = "papers")
      eq_pal <- c(high = "#009E73", moderate = "#E69F00", low = "#D55E00")
      p2 <- ggplot(eqt, aes(evidence_quality, papers, fill = evidence_quality)) +
        geom_col(width = 0.6, color = "white") +
        scale_fill_manual(values = eq_pal, guide = "none") +
        labs(title = paste0(tprefix, "Evidence quality distribution"),
             subtitle = "Papers grouped by assessed evidence quality",
             x = NULL, y = "Number of papers",
             caption = "Evidence quality assessed during screening (study design, sample size, replication).") +
        base_theme
      save_fig(p2, "fig_evidence_quality", "topic",
               "Distribution of papers by assessed evidence quality.",
               w = 6.5, h = 4.6)
    }
  }
}

# =====================================================================
# manifest
# =====================================================================
if (nrow(manifest) > 0) {
  write.csv(manifest, file.path(out_dir, "fig_manifest.csv"), row.names = FALSE)
  cat(sprintf("\nWrote %d figure(s) + fig_manifest.csv to %s\n", nrow(manifest), out_dir))
} else {
  cat("\nNo artifacts found to plot. Nothing written.\n",
      "Expected one of: comparison_matrix.csv, performance_claims.json,\n",
      "benchmark_catalog.json (comparison mode) or theme_table.csv (topic mode).\n")
}
