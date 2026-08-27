# =============================================================================
# Bulk RNA-seq Cell-Type Deconvolution -- Group contrasts on proportions
# =============================================================================
# Tests which cell-type proportions differ between two groups (e.g. responder
# vs non-responder), optionally at each timepoint, with BH-FDR across all
# cell-type x timepoint tests. Optional longitudinal mixed model
# prop ~ group * timepoint + (1|subject). Group levels are data-driven by
# default but can be locked with `group_levels`.
#
# REBUILD NOTES (Biomni):
#   - Defensive guard if `group_col` is missing from metadata.
#   - Clear error when neither group has min_n samples.
#   - ASCII tokens ([OK]/[WARN]) in log lines.
# =============================================================================

#' Mean +/- SD composition per group (and timepoint), long format -- for reports.
summarize_composition <- function(props_df, metadata,
                                  group_col = "group", timepoint_col = NULL) {
    ct_cols <- setdiff(colnames(props_df), "sample_id")
    m <- merge(props_df, metadata, by = "sample_id")
    if (!group_col %in% colnames(m))
        stop("summarize_composition(): metadata has no column '", group_col, "'.")
    keys <- c(group_col, timepoint_col)
    out <- list()
    for (ct in ct_cols) {
        agg_mean <- stats::aggregate(m[[ct]], by = m[keys], FUN = mean)
        agg_sd   <- stats::aggregate(m[[ct]], by = m[keys], FUN = stats::sd)
        d <- agg_mean; names(d)[ncol(d)] <- "mean_fraction"
        d$sd_fraction <- agg_sd$x
        d$cell_type <- ct
        out[[ct]] <- d
    }
    res <- do.call(rbind, out)
    rownames(res) <- NULL
    res[order(res$cell_type), ]
}


#' Two-group contrasts on cell-type proportions (Wilcoxon + BH-FDR).
#'
#' @param props_df  data.frame: sample_id + one column per cell type (fractions)
#' @param metadata  data.frame: sample_id, <group_col>, optional timepoint/subject
#' @param group_col grouping column to contrast
#' @param group_levels length-2 vector naming the two groups to compare
#'                     (default = the two most frequent levels)
#' @param timepoint_col optional column; if given, test within each timepoint
#' @param subject_col optional subject id (enables the mixed model)
#' @param mixed_model fit lmer(prop ~ group*timepoint + (1|subject)) per cell type
#' @param min_n minimum samples per group for a test (default 3)
#' @return data.frame of contrasts (sorted by p_value); mixed-model ANOVA tables
#'         attached as attr(.,"mixed_model_anova") when requested
proportion_contrasts <- function(props_df, metadata,
                                 group_col = "group",
                                 group_levels = NULL,
                                 timepoint_col = NULL,
                                 subject_col = NULL,
                                 mixed_model = FALSE,
                                 min_n = 3) {

    cat("\n=== Group contrasts on cell-type proportions ===\n\n")
    if (!"sample_id" %in% colnames(props_df)) stop("props_df needs a 'sample_id' column.")
    if (!group_col %in% colnames(metadata))
        stop("metadata has no column '", group_col,
             "'. Pass group_col = '<your_column>' explicitly.")

    ct_cols <- setdiff(colnames(props_df), "sample_id")
    m <- merge(props_df, metadata, by = "sample_id")
    m <- m[!is.na(m[[group_col]]), , drop = FALSE]
    if (nrow(m) == 0)
        stop("No samples remain after dropping NA '", group_col, "'.")

    # Determine the two groups to contrast.
    if (is.null(group_levels)) {
        freq <- sort(table(m[[group_col]]), decreasing = TRUE)
        if (length(freq) < 2)
            stop("'", group_col, "' has fewer than 2 non-NA levels; cannot contrast.")
        group_levels <- names(freq)[1:2]
        if (length(freq) > 2)
            cat("   [WARN]", length(freq), "groups present; contrasting the two largest:",
                paste(group_levels, collapse = " vs "), "\n")
    }
    if (length(group_levels) != 2 || any(is.na(group_levels)))
        stop("Need exactly two groups to contrast; got: ",
             paste(group_levels, collapse = ", "))
    g_a <- group_levels[1]; g_b <- group_levels[2]
    m <- m[m[[group_col]] %in% group_levels, , drop = FALSE]
    cat("   Contrast:", g_b, "vs", g_a, "(reference =", g_a, ")\n")

    tps <- if (!is.null(timepoint_col) && timepoint_col %in% colnames(m))
        unique(m[[timepoint_col]]) else NA
    cat("   Timepoints:", if (all(is.na(tps))) "none (pooled)" else
        paste(tps, collapse = ", "), "\n")

    res <- list()
    for (ct in ct_cols) {
        for (tp in tps) {
            d <- if (is.na(tp)) m else m[m[[timepoint_col]] == tp, , drop = FALSE]
            x <- d[d[[group_col]] == g_a, ct]
            y <- d[d[[group_col]] == g_b, ct]
            if (length(x) < min_n || length(y) < min_n) next
            wt <- suppressWarnings(stats::wilcox.test(y, x))
            res[[length(res) + 1]] <- data.frame(
                cell_type = ct,
                timepoint = if (is.na(tp)) "all" else as.character(tp),
                group_a = g_a, group_b = g_b,
                n_a = length(x), n_b = length(y),
                median_a = stats::median(x), median_b = stats::median(y),
                mean_a = mean(x), mean_b = mean(y),
                median_diff_b_minus_a = stats::median(y) - stats::median(x),
                p_value = wt$p.value, stringsAsFactors = FALSE)
        }
    }
    if (length(res) == 0) {
        cat("   [WARN] No testable cell type x timepoint cells (too few samples per group; min_n =",
            min_n, ").\n")
        return(data.frame())
    }
    out <- do.call(rbind, res)
    out$padj_BH <- stats::p.adjust(out$p_value, method = "BH")
    out$significant <- out$padj_BH < 0.05
    out <- out[order(out$p_value), ]
    rownames(out) <- NULL

    # Optional longitudinal mixed model.
    if (isTRUE(mixed_model)) {
        if (is.null(subject_col) || is.null(timepoint_col)) {
            cat("   [WARN] mixed_model=TRUE needs subject_col + timepoint_col -- skipping.\n")
        } else if (!requireNamespace("lmerTest", quietly = TRUE)) {
            cat("   [WARN] lmerTest not installed -- skipping mixed model.\n")
        } else {
            mm <- lapply(ct_cols, function(ct) {
                d <- m[, c(ct, group_col, timepoint_col, subject_col)]
                names(d) <- c("prop", "grp", "tp", "subj")
                d <- d[stats::complete.cases(d), ]
                if (length(unique(d$subj)) < 4 || length(unique(d$grp)) < 2) return(NULL)
                fit <- try(lmerTest::lmer(prop ~ grp * factor(tp) + (1 | subj), data = d),
                           silent = TRUE)
                if (inherits(fit, "try-error")) return(NULL)
                an <- as.data.frame(stats::anova(fit))
                an$term <- rownames(an); an$cell_type <- ct; rownames(an) <- NULL
                an
            })
            mm <- do.call(rbind, Filter(Negate(is.null), mm))
            attr(out, "mixed_model_anova") <- mm
            cat("   Mixed model fitted for", length(unique(mm$cell_type)), "cell types.\n")
        }
    }

    n_sig <- sum(out$significant, na.rm = TRUE)
    cat("\n[OK] Proportion contrasts completed!", nrow(out), "tests,",
        n_sig, "significant (BH-FDR < 0.05)\n\n")
    out
}
