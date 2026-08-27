# =============================================================================
# Bulk RNA-seq Cell-Type Deconvolution -- Visualization
# =============================================================================
# Generates publication-quality figures from a deconvolution run:
#   1. Stacked-bar composition (mean fraction per group x timepoint)
#   2. Per-cell-type boxplots (top differential types, by group)
#   3. Composition trajectories over timepoints (if timepoints present)
#   4. Cross-method concordance scatter (first two methods)
#   5. Ground-truth recovery scatter (if known proportions provided)
# All plots use ggprism::theme_prism() and are saved PNG + SVG (graceful fallback).
#
# REBUILD NOTES (Biomni):
#   - When output_dir is on an S3-FUSE mount (/mnt/...), the base-R svg() device
#     does random-access writes which FUSE rejects. The save helper now stages
#     SVG (and PNG when needed) to /workspace/ first and copies into place.
#   - ASCII tokens ([OK]/[FAIL]) replace earlier Unicode glyphs.
# =============================================================================

suppressPackageStartupMessages({
    library(ggplot2)
    library(ggprism)
})

.has_svglite <- requireNamespace("svglite", quietly = TRUE)
if (.has_svglite) suppressPackageStartupMessages(library(svglite))

# --- Save helper (PNG + SVG with fallback) -----------------------------------

# Output path lives on an S3 FUSE mount where random-access writes fail
# (matters for base-R svg() / matplotlib-style devices).
.is_fuse_path <- function(path) {
    np <- normalizePath(path, mustWork = FALSE)
    grepl("^/mnt/(results|shared-workspace)(/|$)", np)
}

# Write `writer(fp)` to `final_path` directly when safe; stage through
# /workspace/ otherwise and shell-cp into place. Returns final_path on success.
.write_with_staging <- function(final_path, writer) {
    if (!.is_fuse_path(final_path)) {
        writer(final_path)
        return(final_path)
    }
    stage_dir <- file.path("/workspace", "deconv_stage")
    dir.create(stage_dir, showWarnings = FALSE, recursive = TRUE)
    tmp <- file.path(stage_dir, basename(final_path))
    writer(tmp)
    # Use shell cp -- file.copy() returns 0-byte files on S3-FUSE in R.
    res <- system2("cp", c(shQuote(tmp), shQuote(final_path)), stdout = TRUE, stderr = TRUE)
    if (!file.exists(final_path) || file.size(final_path) == 0)
        stop("Failed to copy ", tmp, " -> ", final_path,
             if (length(res)) paste0(" (", paste(res, collapse = "; "), ")") else "")
    invisible(file.remove(tmp))
    final_path
}

.save_ggplot <- function(plot, base_path, width = 8, height = 6, dpi = 300) {
    png_path <- sub("\\.(svg|png)$", ".png", base_path)
    tryCatch({
        .write_with_staging(png_path, function(fp)
            ggsave(fp, plot = plot, width = width, height = height, dpi = dpi, device = "png"))
        cat("   Saved:", png_path, "\n")
    }, error = function(e) cat("   (PNG export failed:", conditionMessage(e), ")\n"))

    svg_path <- sub("\\.(svg|png)$", ".svg", base_path)
    saved <- FALSE
    if (.has_svglite) {
        saved <- tryCatch({
            .write_with_staging(svg_path, function(fp)
                ggsave(fp, plot = plot, width = width, height = height, device = "svg"))
            TRUE
        }, error = function(e) FALSE)
    }
    if (!saved) {
        tryCatch({
            .write_with_staging(svg_path, function(fp) {
                grDevices::svg(fp, width = width, height = height); print(plot); grDevices::dev.off()
            })
            saved <- TRUE
        }, error = function(e) NULL)
    }
    if (saved) cat("   Saved:", svg_path, "\n") else cat("   (SVG export failed)\n")
}

# --- Data shaping ------------------------------------------------------------
.long_props <- function(props_df, metadata = NULL) {
    ct_cols <- setdiff(colnames(props_df), "sample_id")
    long <- do.call(rbind, lapply(ct_cols, function(ct)
        data.frame(sample_id = props_df$sample_id, cell_type = ct,
                   fraction = props_df[[ct]], stringsAsFactors = FALSE)))
    if (!is.null(metadata)) long <- merge(long, metadata, by = "sample_id")
    long
}

.group_colors <- function(levels) {
    pal <- c("#0279EE", "#D33682", "#2CA02C", "#FF7F0E", "#8E44AD", "#17BECF")
    setNames(pal[seq_along(levels)], levels)
}

.order_timepoints <- function(x) {
    # Heuristic order: baseline/day/week/month tokens, else first-seen order.
    u <- unique(as.character(x))
    key <- suppressWarnings(as.numeric(gsub("[^0-9.]", "", u)))
    base_first <- grepl("base|pre|d0|day0|t0|screen", u, ignore.case = TRUE)
    ord <- order(!base_first, ifelse(is.na(key), Inf, key), u)
    factor(as.character(x), levels = u[ord])
}


# --- Plot 1: stacked-bar composition -----------------------------------------
plot_composition <- function(props_df, metadata, output_dir,
                            group_col = "group", timepoint_col = "timepoint") {
    cat("\n   [1] Composition stacked bar...\n")
    if (!group_col %in% colnames(metadata)) {
        cat("       (skipped -- metadata has no '", group_col, "' column)\n", sep = ""); return(invisible(NULL))
    }
    long <- .long_props(props_df, metadata)
    has_tp <- !is.null(timepoint_col) && timepoint_col %in% colnames(long)
    keys <- c(group_col, if (has_tp) timepoint_col, "cell_type")
    agg <- stats::aggregate(long$fraction, by = long[keys], FUN = mean)
    names(agg)[ncol(agg)] <- "fraction"
    if (has_tp) agg[[timepoint_col]] <- .order_timepoints(agg[[timepoint_col]])

    p <- ggplot(agg, aes(x = .data[[group_col]], y = fraction, fill = cell_type)) +
        geom_col(position = "stack", width = 0.7, color = "white", linewidth = 0.15) +
        labs(x = NULL, y = "Mean cell-type fraction",
             title = "Deconvolved cell-type composition", fill = "Cell type") +
        theme_prism(base_size = 12) +
        theme(plot.title = element_text(hjust = 0.5, face = "bold", size = 14),
              axis.text.x = element_text(angle = 45, hjust = 1, vjust = 1,
                                         margin = margin(t = 4)),
              plot.margin = margin(t = 10, r = 14, b = 24, l = 14),
              legend.position = "right", legend.title = element_text())
    if (has_tp) p <- p + facet_wrap(stats::as.formula(paste("~", timepoint_col)))
    .save_ggplot(p, file.path(output_dir, "composition_stacked.png"),
                 width = if (has_tp) 11 else 8, height = 7)
}


# --- Plot 2: per-cell-type boxplots ------------------------------------------
plot_celltype_boxplots <- function(props_df, metadata, output_dir, contrasts = NULL,
                                   group_col = "group", timepoint_col = "timepoint",
                                   top_n = 6) {
    cat("   [2] Per-cell-type boxplots...\n")
    if (!group_col %in% colnames(metadata)) {
        cat("       (skipped -- metadata has no '", group_col, "' column)\n", sep = ""); return(invisible(NULL))
    }
    long <- .long_props(props_df, metadata)
    has_tp <- !is.null(timepoint_col) && timepoint_col %in% colnames(long)
    # Choose cell types: most-significant first if contrasts available.
    if (!is.null(contrasts) && nrow(contrasts) > 0) {
        ord <- contrasts$cell_type[order(contrasts$p_value)]
        cts <- unique(ord)[seq_len(min(top_n, length(unique(ord))))]
    } else {
        cts <- setdiff(colnames(props_df), "sample_id")[seq_len(
            min(top_n, length(setdiff(colnames(props_df), "sample_id"))))]
    }
    long <- long[long$cell_type %in% cts, , drop = FALSE]
    long$cell_type <- factor(long$cell_type, levels = cts)
    if (has_tp) long[[timepoint_col]] <- .order_timepoints(long[[timepoint_col]])
    gcol <- .group_colors(unique(long[[group_col]]))

    p <- ggplot(long, aes(x = .data[[group_col]], y = fraction, fill = .data[[group_col]])) +
        geom_boxplot(outlier.size = 0.6, width = 0.6, alpha = 0.85) +
        geom_jitter(width = 0.12, size = 0.7, alpha = 0.5) +
        scale_fill_manual(values = gcol) +
        labs(x = NULL, y = "Cell-type fraction",
             title = "Cell-type fractions by group") +
        theme_prism(base_size = 11) +
        theme(plot.title = element_text(hjust = 0.5, face = "bold", size = 13),
              legend.position = "none",
              axis.text.x = element_text(angle = 45, hjust = 1, vjust = 1,
                                         margin = margin(t = 6)),
              plot.margin = margin(t = 10, r = 14, b = 36, l = 14),
              panel.spacing = unit(1, "lines"))
    if (has_tp) {
        p <- p + facet_grid(stats::as.formula(paste("cell_type ~", timepoint_col)),
                            scales = "free_y")
    } else {
        p <- p + facet_wrap(~ cell_type, scales = "free_y")
    }
    .save_ggplot(p, file.path(output_dir, "celltype_boxplots.png"),
                 width = if (has_tp) 11 else 11, height = 12)
}


# --- Plot 3: trajectories over timepoints ------------------------------------
plot_trajectories <- function(props_df, metadata, output_dir,
                             group_col = "group", timepoint_col = "timepoint") {
    if (is.null(timepoint_col) || !timepoint_col %in% colnames(metadata)) return(invisible(NULL))
    if (!group_col %in% colnames(metadata)) return(invisible(NULL))
    long <- .long_props(props_df, metadata)
    if (length(unique(long[[timepoint_col]])) < 2) return(invisible(NULL))
    cat("   [3] Composition trajectories...\n")
    long[[timepoint_col]] <- .order_timepoints(long[[timepoint_col]])
    keys <- c(group_col, timepoint_col, "cell_type")
    mean_df <- stats::aggregate(long$fraction, by = long[keys], FUN = mean)
    names(mean_df)[ncol(mean_df)] <- "mean"
    se_df <- stats::aggregate(long$fraction, by = long[keys],
                              FUN = function(v) stats::sd(v) / sqrt(length(v)))
    mean_df$se <- se_df$x
    gcol <- .group_colors(unique(mean_df[[group_col]]))

    p <- ggplot(mean_df, aes(x = .data[[timepoint_col]], y = mean,
                             color = .data[[group_col]], group = .data[[group_col]])) +
        geom_line(linewidth = 0.9) +
        geom_point(size = 2) +
        geom_errorbar(aes(ymin = mean - se, ymax = mean + se), width = 0.12) +
        scale_color_manual(values = gcol) +
        facet_wrap(~ cell_type, scales = "free_y") +
        labs(x = NULL, y = "Mean fraction (+/- SE)", color = "Group",
             title = "Cell-type composition trajectories") +
        theme_prism(base_size = 11) +
        theme(plot.title = element_text(hjust = 0.5, face = "bold", size = 13),
              legend.position = "bottom", legend.title = element_text())
    .save_ggplot(p, file.path(output_dir, "composition_trajectories.png"),
                 width = 10, height = 7)
}


# --- Plot 4: cross-method concordance scatter --------------------------------
plot_concordance <- function(deconv, output_dir) {
    if (length(deconv$methods) < 2) {
        cat("   [4] Concordance scatter skipped (need >=2 methods).\n"); return(invisible(NULL)) }
    cat("   [4] Cross-method concordance scatter...\n")
    m1 <- deconv$methods[1]; m2 <- deconv$methods[2]
    a <- .long_props(deconv$proportions[[m1]]); names(a)[3] <- "frac_a"
    b <- .long_props(deconv$proportions[[m2]]); names(b)[3] <- "frac_b"
    d <- merge(a, b, by = c("sample_id", "cell_type"))
    r <- suppressWarnings(stats::cor(d$frac_a, d$frac_b))
    lim <- range(c(d$frac_a, d$frac_b), na.rm = TRUE)

    p <- ggplot(d, aes(x = frac_a, y = frac_b, color = cell_type)) +
        geom_abline(slope = 1, intercept = 0, linetype = 2, color = "grey50") +
        geom_point(size = 1.8, alpha = 0.8) +
        coord_equal(xlim = lim, ylim = lim) +
        labs(x = paste0(m1, " fraction"), y = paste0(m2, " fraction"),
             color = "Cell type",
             title = sprintf("Method concordance: %s vs %s (r = %.3f)", m1, m2, r)) +
        theme_prism(base_size = 12) +
        theme(plot.title = element_text(hjust = 0.5, face = "bold", size = 13),
              legend.position = "right", legend.title = element_text())
    .save_ggplot(p, file.path(output_dir, "method_concordance_scatter.png"),
                 width = 8, height = 7)
}


# --- Plot 5: ground-truth recovery scatter -----------------------------------
plot_recovery <- function(deconv, ground_truth, output_dir) {
    if (is.null(ground_truth)) return(invisible(NULL))
    cat("   [5] Ground-truth recovery scatter...\n")
    est <- .long_props(deconv$consensus); names(est)[3] <- "estimated"
    tru <- .long_props(ground_truth);     names(tru)[3] <- "truth"
    d <- merge(est, tru, by = c("sample_id", "cell_type"))
    if (nrow(d) == 0) { cat("   (no overlapping cell types with truth)\n"); return(invisible(NULL)) }
    r <- suppressWarnings(stats::cor(d$estimated, d$truth))
    rmse <- sqrt(mean((d$estimated - d$truth)^2))
    lim <- range(c(d$estimated, d$truth), na.rm = TRUE)

    p <- ggplot(d, aes(x = truth, y = estimated, color = cell_type)) +
        geom_abline(slope = 1, intercept = 0, linetype = 2, color = "grey50") +
        geom_point(size = 1.8, alpha = 0.8) +
        coord_equal(xlim = lim, ylim = lim) +
        labs(x = "True fraction", y = "Estimated fraction (consensus)",
             color = "Cell type",
             title = sprintf("Recovery vs ground truth (r = %.3f, RMSE = %.3f)", r, rmse)) +
        theme_prism(base_size = 12) +
        theme(plot.title = element_text(hjust = 0.5, face = "bold", size = 13),
              legend.position = "right", legend.title = element_text())
    .save_ggplot(p, file.path(output_dir, "ground_truth_recovery.png"),
                 width = 8, height = 7)
}


# --- Main entry point --------------------------------------------------------

#' Generate all deconvolution figures.
#'
#' @param deconv result list from run_deconvolution()
#' @param metadata sample metadata (sample_id + group [+ timepoint, subject]); if
#'                 NULL, group-based plots are skipped
#' @param contrasts proportion_contrasts() output (orders the boxplot panels)
#' @param ground_truth known proportions (sample_id + cell types) for recovery plot
#' @param group_col / timepoint_col metadata column names
#' @param props which proportions to plot: "consensus" or a method name
#' @param output_dir output directory (default /mnt/results/deconvolution on Biomni)
generate_all_plots <- function(deconv, metadata = NULL, contrasts = NULL,
                              ground_truth = NULL, group_col = "group",
                              timepoint_col = "timepoint", props = "consensus",
                              output_dir = "results") {
    cat("\n=== Generating deconvolution figures ===\n")
    dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

    props_df <- if (props == "consensus" || !props %in% names(deconv$proportions))
        deconv$consensus else deconv$proportions[[props]]

    if (!is.null(metadata)) {
        tryCatch(plot_composition(props_df, metadata, output_dir, group_col, timepoint_col),
                 error = function(e) cat("   [FAIL] composition:", conditionMessage(e), "\n"))
        tryCatch(plot_celltype_boxplots(props_df, metadata, output_dir, contrasts, group_col, timepoint_col),
                 error = function(e) cat("   [FAIL] boxplots:", conditionMessage(e), "\n"))
        tryCatch(plot_trajectories(props_df, metadata, output_dir, group_col, timepoint_col),
                 error = function(e) cat("   [FAIL] trajectories:", conditionMessage(e), "\n"))
    } else {
        cat("   (no metadata -- skipping group composition/boxplot/trajectory plots)\n")
    }
    tryCatch(plot_concordance(deconv, output_dir),
             error = function(e) cat("   [FAIL] concordance scatter:", conditionMessage(e), "\n"))
    tryCatch(plot_recovery(deconv, ground_truth, output_dir),
             error = function(e) cat("   [FAIL] recovery scatter:", conditionMessage(e), "\n"))

    n_png <- length(list.files(output_dir, pattern = "\\.png$"))
    cat("\n[OK] All plots generated successfully!", n_png, "figures saved\n\n")
}
