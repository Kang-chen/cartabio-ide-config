# =============================================================================
# make_infographic.R  --  DATA-FAITHFUL composed-panel infographic
# -----------------------------------------------------------------------------
# Builds a one-page visual summary from the REAL computed outputs (the CSVs in
# tables/). Every number and every bar height is read from those files -- nothing
# is drawn by an image model. This is the DEFAULT infographic (CFG$infographic_mode
# == "composed_panel").
#
# HARD RULE (see references/config-reference.md, section 10): an image-generation
# model must NEVER render actual numbers or proportional bars. If a designed
# "generated_shell" look is desired, GenerateImage may draw ONLY an empty layout
# (boxes/arrows/labels, no numbers) and every value must be overlaid
# programmatically from these same outputs.
#
# OUTPUT: figures/infographic_summary.png (+ .svg)
# =============================================================================
if (!exists("SCRIPTS_DIR")) {
  .fa <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  SCRIPTS_DIR <- if (length(.fa)) dirname(sub("^--file=", "", .fa)) else "."
}
source(file.path(SCRIPTS_DIR, "_utils.R"))
suppressMessages({ library(ggplot2); library(patchwork) })

# Phylo palette
PAL <- c(blue="#0279EE", orange="#FF9400", green="#75A025", gold="#D4A04A",
         pink="#FD9BED", black="#000000", gray="#8A8378")
theme_ig <- function() theme_void(base_family = "Liberation Sans") +
  theme(plot.title = element_text(face = "bold", size = 12, hjust = 0,
                                  color = "#111111"),
        plot.margin = margin(6, 10, 6, 10))

run_infographic <- function(CFG) {
  tdir <- file.path(CFG$paths$out_dir, "tables")
  rd  <- function(f) if (file.exists(file.path(tdir, f)))
    fread(file.path(tdir, f)) else NULL

  flow  <- rd("cohort_flow.csv")
  t1    <- rd("table1_cohort.csv")
  lm    <- rd("survival_landmark_cohort.csv")
  txs   <- rd("treatment_summary.csv")
  cls   <- rd("treatment_class_summary.csv")

  gcol <- CFG$cohort_label

  # ---- Panel A: cohort funnel (real counts) --------------------------------
  pa <- ggplot() + theme_ig() + ggtitle("Cohort selection")
  if (!is.null(flow)) {
    flow[, Step := factor(Step, levels = rev(Step))]
    pa <- ggplot(flow, aes(x = N, y = Step)) +
      geom_col(fill = PAL["blue"], width = 0.65) +
      geom_text(aes(label = N), hjust = -0.15, size = 3.6, family = "Liberation Sans") +
      scale_x_continuous(expand = expansion(mult = c(0, 0.18))) +
      labs(title = "Cohort selection", x = NULL, y = NULL) +
      theme_minimal(base_family = "Liberation Sans") +
      theme(plot.title = element_text(face = "bold", size = 12),
            panel.grid = element_blank(), axis.text.x = element_blank(),
            axis.text.y = element_text(size = 8.5))
  }

  # ---- Panel B: big-number callouts (real values) --------------------------
  # Pull a few headline numbers straight from Table 1 (cohort column).
  get_val <- function(var) if (!is.null(t1) && var %in% t1$Variable)
    as.character(t1[[gcol]][t1$Variable == var]) else NA_character_
  # NOTE: each label MUST describe exactly the value beside it. The third tile
  # shows a SURVIVAL probability, so it is labelled "30-day survival" (NOT
  # CFG$primary_endpoint, which is a mortality endpoint -- mixing them mislabels
  # the number).
  kv <- data.frame(
    label = c(sprintf("%s patients", gcol), "In-hospital death",
              "30-day survival", "Median time-to-first tx (h)"),
    value = c(get_val("N (patients)"),
              get_val("In-hospital death"),
              if (!is.null(lm) && any(lm$Day == 30))
                sprintf("%.0f%%", 100 * lm$Survival[lm$Day == 30][1]) else NA,
              if (!is.null(txs)) txs$Value[grepl("time to first", txs$Metric, ignore.case = TRUE)][1] else NA),
    x = c(0, 1, 0, 1), y = c(1, 1, 0, 0), stringsAsFactors = FALSE)
  kv <- kv[!is.na(kv$value), ]
  pb <- ggplot(kv, aes(x, y)) +
    geom_tile(fill = PAL["gold"], width = 0.96, height = 0.9, alpha = 0.12) +
    geom_text(aes(label = value), vjust = -0.1, size = 6, fontface = "bold",
              color = "#111111", family = "Liberation Sans") +
    geom_text(aes(label = label), vjust = 1.9, size = 3, color = PAL["gray"],
              family = "Liberation Sans") +
    scale_x_continuous(limits = c(-0.6, 1.6)) +
    scale_y_continuous(limits = c(-0.6, 1.6)) +
    labs(title = "Key numbers") + theme_ig()

  # ---- Panel C: KM landmark survival (real values) -------------------------
  pc <- ggplot() + theme_ig() + ggtitle("Survival (landmark)")
  if (!is.null(lm)) {
    pc <- ggplot(lm, aes(x = Day, y = Survival)) +
      geom_ribbon(aes(ymin = `Lower 95%`, ymax = `Upper 95%`),
                  fill = PAL["blue"], alpha = 0.15) +
      geom_line(color = PAL["blue"], linewidth = 1) +
      geom_point(color = PAL["blue"], size = 2) +
      scale_y_continuous(limits = c(0, 1), labels = scales::percent) +
      scale_x_continuous(limits = c(0, NA),
                         breaks = sort(unique(c(0, lm$Day)))) +
      labs(title = "Survival (landmark)", x = "Days from origin", y = "Survival") +
      theme_minimal(base_family = "Liberation Sans") +
      theme(plot.title = element_text(face = "bold", size = 12),
            panel.grid.minor = element_blank())
  }

  # ---- Panel D: top treatment classes (real counts) ------------------------
  pd <- ggplot() + theme_ig() + ggtitle("Top treatment classes")
  if (!is.null(cls) && nrow(cls) > 0) {
    top <- head(cls[order(-N_admissions)], 6)
    top[, tx_class := factor(tx_class, levels = rev(tx_class))]
    pd <- ggplot(top, aes(x = N_admissions, y = tx_class)) +
      geom_col(fill = PAL["green"], width = 0.65) +
      geom_text(aes(label = N_admissions), hjust = -0.2, size = 3.4,
                family = "Liberation Sans") +
      scale_x_continuous(expand = expansion(mult = c(0, 0.18))) +
      labs(title = "Top treatment classes", x = "Admissions", y = NULL) +
      theme_minimal(base_family = "Liberation Sans") +
      theme(plot.title = element_text(face = "bold", size = 12),
            panel.grid = element_blank(), axis.text.x = element_blank(),
            axis.text.y = element_text(size = 8.5))
  }

  title <- sprintf("%s  \u2014  Real-World Evidence Summary", CFG$study_title)
  ig <- (pa | pb) / (pc | pd) +
    plot_annotation(title = title,
      theme = theme(plot.title = element_text(face = "bold", size = 14,
                                              family = "Liberation Sans")))

  fdir <- file.path(CFG$paths$out_dir, "figures")
  dir.create(fdir, showWarnings = FALSE, recursive = TRUE)
  # PNG is a binary/random-access format: ggsave() cannot write it directly to
  # an S3-backed path (/mnt/results, /mnt/shared-workspace), and R's file.copy()
  # produces a 0-byte file there (FUSE limitation). Stage on local /workspace,
  # then move with a SHELL cp (works on FUSE). SVG is text and writes directly.
  stage_png <- tempfile(pattern = "infographic_", tmpdir = "/workspace",
                        fileext = ".png")
  ggsave(stage_png, ig, width = 10, height = 7, dpi = 150)
  ggsave(file.path(fdir, "infographic_summary.svg"), ig, width = 10, height = 7)
  dest_png <- file.path(fdir, "infographic_summary.png")
  rc <- system2("cp", c(shQuote(stage_png), shQuote(dest_png)))
  unlink(stage_png)
  if (rc != 0 || !file.exists(dest_png) || file.info(dest_png)$size == 0)
    warning("infographic PNG copy to results failed (rc=", rc, ")")
  cat("[infographic] wrote figures/infographic_summary.png (+ .svg)\n")
  invisible(dest_png)
}

if (sys.nframe() == 0) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) >= 1) source(args[1])
  run_infographic(CFG)
}
