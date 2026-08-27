#!/usr/bin/env Rscript
# make_figures.R -- data-driven figures for direction-of-effect concordance:
#   fig1: evidence-matrix heatmap (targets x axes, fill = vote)
#   fig2: consensus summary bar (bar length = # concordant informative axes, fill = tier)
# Colorblind-safe, Liberation Sans, PNG + SVG. Writes fig_manifest.csv.
# Usage: Rscript make_figures.R --run RUN [--title-prefix "..."]

suppressWarnings(suppressMessages({
  library(ggplot2); library(dplyr); library(readr); library(tidyr)
}))

args <- commandArgs(trailingOnly = TRUE)
getarg <- function(flag, default = NULL) {
  i <- match(flag, args); if (!is.na(i) && i < length(args)) args[i + 1] else default
}
RUN <- getarg("--run"); if (is.null(RUN)) stop("--run required")
PREFIX <- getarg("--title-prefix", "")
figdir <- file.path(RUN, "figures"); dir.create(figdir, showWarnings = FALSE, recursive = TRUE)

VOTE_COLORS <- c("INHIBIT" = "#0279EE", "ACTIVATE" = "#FF9400",
                 "INHIBIT (allele-specific)" = "#75A025",
                 "not_informative" = "#ECE9E2", "CONTESTED" = "#B0413E")
TIER_COLORS <- c("High" = "#0279EE", "High-Moderate" = "#75A025",
                 "Moderate" = "#D4A04A", "Low-Contested" = "#B0413E")

base_theme <- theme_minimal(base_size = 12, base_family = "Liberation Sans") +
  theme(panel.grid.minor = element_blank(),
        plot.title = element_text(face = "bold", size = 13),
        axis.text = element_text(color = "#2C2A26"))

mat <- read_csv(file.path(RUN, "data", "evidence_matrix.csv"), show_col_types = FALSE)
calls <- read_csv(file.path(RUN, "data", "consensus_calls.csv"), show_col_types = FALSE)

# --- normalize vote label for allele-specific INHIBIT (note mentions allele-specific) ---
mat <- mat %>% mutate(
  vote_lab = ifelse(grepl("allele-specific", tolower(note)) & vote == "INHIBIT",
                    "INHIBIT (allele-specific)", vote),
  vote_lab = factor(vote_lab, levels = names(VOTE_COLORS)))

manifest <- data.frame(file = character(), kind = character(), caption = character())

# ================= FIG 1: evidence-matrix heatmap =================
axis_order <- unique(mat$axis)
mat$axis <- factor(mat$axis, levels = rev(axis_order))
mat$target <- factor(mat$target, levels = unique(mat$target))
p1 <- ggplot(mat, aes(x = target, y = axis, fill = vote_lab)) +
  geom_tile(color = "white", linewidth = 1.1) +
  geom_text(aes(label = ifelse(vote == "not_informative", "n/i", vote)),
            size = 2.9, color = ifelse(mat$vote == "not_informative", "#8A8378", "white"),
            family = "Liberation Sans") +
  scale_fill_manual(values = VOTE_COLORS, drop = FALSE, name = "Direction vote") +
  labs(title = paste0(PREFIX, "Evidence matrix: per-axis direction of effect"),
       x = NULL, y = NULL) +
  base_theme + theme(legend.position = "right")
f1 <- file.path(figdir, "fig1_evidence_matrix.png")
ggsave(f1, p1, width = 9.6, height = 4.2, dpi = 200, bg = "white")
ggsave(sub("\\.png$", ".svg", f1), p1, width = 9.6, height = 4.2, bg = "white")
manifest <- rbind(manifest, data.frame(file = basename(f1), kind = "evidence_matrix",
  caption = "Evidence matrix heatmap. Blue = INHIBIT; green = INHIBIT (allele-specific); orange = ACTIVATE; grey = not informative (n/i)."))

# ================= FIG 2: consensus summary bar =================
calls <- calls %>% mutate(
  confidence = factor(confidence, levels = names(TIER_COLORS)),
  label = paste0(consensus, "  (", concordance, ")"))
p2 <- ggplot(calls, aes(x = reorder(target, n_agree), y = n_agree, fill = confidence)) +
  geom_col(width = 0.66) +
  geom_text(aes(label = label), hjust = -0.05, size = 3.1, family = "Liberation Sans",
            color = "#2C2A26") +
  coord_flip() +
  scale_fill_manual(values = TIER_COLORS, drop = FALSE, name = "Confidence tier") +
  scale_y_continuous(expand = expansion(mult = c(0, 0.35))) +
  labs(title = paste0(PREFIX, "Consensus direction & agreement"),
       x = NULL, y = "# concordant informative axes") +
  base_theme
f2 <- file.path(figdir, "fig2_consensus_summary.png")
ggsave(f2, p2, width = 9.6, height = 0.7 + 0.6 * nrow(calls), dpi = 200, bg = "white")
ggsave(sub("\\.png$", ".svg", f2), p2, width = 9.6, height = 0.7 + 0.6 * nrow(calls), bg = "white")
manifest <- rbind(manifest, data.frame(file = basename(f2), kind = "consensus_summary",
  caption = "Per-target consensus direction; bar length = number of informative axes that agree; color = confidence tier."))

write_csv(manifest, file.path(figdir, "fig_manifest.csv"))
cat("Wrote figures + fig_manifest.csv to", figdir, "\n")
cat("REMINDER: run Read mode='media_output_check' on each PNG; regenerate on failure.\n")
