# =============================================================================
# Bulk RNA-seq Cell-Type Deconvolution -- Export Results
# =============================================================================
# Writes all deconvolution outputs: per-method + consensus proportion tables,
# cross-method concordance, group contrasts, optional ground-truth recovery,
# the analysis object (RDS, for downstream skills), and a markdown report.
#
# REBUILD NOTES (Biomni):
#   - .composition_summary() now guards for missing group_col instead of crashing.
#   - RDS files use a workspace stage when writing to /mnt/results/ (FUSE-safe).
#   - ASCII tokens ([OK]/[FAIL]) replace earlier Unicode glyphs in messages.
# =============================================================================

#' Detect S3 FUSE-backed paths that reject random-access writes.
.is_fuse_path <- function(path) {
    np <- normalizePath(path, mustWork = FALSE)
    grepl("^/mnt/(results|shared-workspace)(/|$)", np)
}

#' Save an R object as RDS, staging through /workspace/ if the final path is on
#' an S3-FUSE mount. saveRDS uses sequential writes so it normally works on FUSE,
#' but the staging path is safer for very large objects.
.saveRDS_staged <- function(obj, path) {
    if (!.is_fuse_path(path)) {
        saveRDS(obj, path); return(invisible(path))
    }
    stage_dir <- file.path("/workspace", "deconv_stage")
    dir.create(stage_dir, showWarnings = FALSE, recursive = TRUE)
    tmp <- file.path(stage_dir, basename(path))
    saveRDS(obj, tmp)
    res <- system2("cp", c(shQuote(tmp), shQuote(path)), stdout = TRUE, stderr = TRUE)
    if (!file.exists(path) || file.size(path) == 0)
        stop("Failed to copy ", tmp, " -> ", path,
             if (length(res)) paste0(" (", paste(res, collapse = "; "), ")") else "")
    invisible(file.remove(tmp))
    invisible(path)
}


#' Export all deconvolution results.
#'
#' @param deconv result list from run_deconvolution()
#' @param metadata sample metadata (sample_id + group [+ timepoint, subject])
#' @param contrasts proportion_contrasts() output (optional)
#' @param ground_truth known proportions (sample_id + cell types), optional
#' @param output_dir output directory (default /mnt/results/deconvolution on Biomni)
#' @param group_col / timepoint_col metadata column names for composition summary
export_all <- function(deconv, metadata = NULL, contrasts = NULL,
                      ground_truth = NULL, output_dir = "results",
                      group_col = "group", timepoint_col = "timepoint") {

    cat("\n=== Exporting Results ===\n\n")
    dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

    # 1. Per-method proportions ----------------------------------------------
    cat("1. Per-method proportion tables...\n")
    for (m in names(deconv$proportions)) {
        f <- file.path(output_dir, paste0("proportions_", m, ".csv"))
        write.csv(deconv$proportions[[m]], f, row.names = FALSE)
        cat("   Saved:", f, "\n")
    }

    # 2. Consensus proportions ------------------------------------------------
    cat("2. Consensus proportions...\n")
    write.csv(deconv$consensus, file.path(output_dir, "consensus_proportions.csv"),
              row.names = FALSE)
    cat("   Saved:", file.path(output_dir, "consensus_proportions.csv"), "\n")

    # 3. Cross-method concordance --------------------------------------------
    cat("3. Method concordance...\n")
    if (!is.null(deconv$concordance) && nrow(deconv$concordance) > 0) {
        write.csv(deconv$concordance, file.path(output_dir, "method_concordance.csv"),
                  row.names = FALSE)
        cat("   Saved:", file.path(output_dir, "method_concordance.csv"), "\n")
    } else {
        cat("   (single method -- no cross-method concordance)\n")
    }

    # 4. Group contrasts ------------------------------------------------------
    if (!is.null(contrasts) && nrow(contrasts) > 0) {
        cat("4. Proportion contrasts...\n")
        write.csv(contrasts, file.path(output_dir, "proportion_contrasts.csv"),
                  row.names = FALSE)
        cat("   Saved:", file.path(output_dir, "proportion_contrasts.csv"), "\n")
        mm <- attr(contrasts, "mixed_model_anova")
        if (!is.null(mm)) {
            write.csv(mm, file.path(output_dir, "mixed_model_anova.csv"), row.names = FALSE)
            cat("   Saved:", file.path(output_dir, "mixed_model_anova.csv"), "\n")
        }
    }

    # 5. Composition summary (mean fraction per group [x timepoint]) ----------
    recovery <- NULL
    if (!is.null(metadata) && group_col %in% colnames(metadata)) {
        cat("5. Composition summary...\n")
        comp <- .composition_summary(deconv$consensus, metadata,
                                     group_col = group_col,
                                     timepoint_col = timepoint_col)
        write.csv(comp, file.path(output_dir, "composition_summary.csv"), row.names = FALSE)
        cat("   Saved:", file.path(output_dir, "composition_summary.csv"), "\n")
    } else if (!is.null(metadata)) {
        cat("5. Composition summary skipped (no '", group_col, "' column in metadata).\n", sep = "")
    }

    # 6. Ground-truth recovery (if known proportions provided) ----------------
    if (!is.null(ground_truth)) {
        cat("6. Ground-truth recovery...\n")
        recovery <- .recovery_metrics(deconv$consensus, ground_truth)
        write.csv(recovery, file.path(output_dir, "ground_truth_recovery.csv"),
                  row.names = FALSE)
        cat("   Saved:", file.path(output_dir, "ground_truth_recovery.csv"),
            sprintf("(overall r = %.3f)\n",
                    recovery$pearson_r[recovery$cell_type == "__overall__"]))
    }

    # 7. Analysis object (CRITICAL for downstream skills) ---------------------
    cat("7. Analysis object (RDS)...\n")
    deconvolution_results <- list(
        proportions = deconv$proportions,
        consensus = deconv$consensus,
        concordance = deconv$concordance,
        fragile_cell_types = deconv$fragile_cell_types,
        methods = deconv$methods,
        cell_types = deconv$cell_types,
        bulk_scale = deconv$bulk_scale,
        n_shared_genes = deconv$n_shared_genes,
        reference_summary = deconv$reference_summary,
        metadata = metadata,
        contrasts = contrasts,
        recovery = recovery)
    .saveRDS_staged(deconvolution_results, file.path(output_dir, "cell_type_deconvolution.rds"))
    cat("   Saved:", file.path(output_dir, "cell_type_deconvolution.rds"), "\n")
    cat("   (Load with: res <- readRDS('cell_type_deconvolution.rds'))\n")

    # 8. Markdown report ------------------------------------------------------
    cat("8. Markdown report...\n")
    .generate_markdown_report(deconv, contrasts, recovery, metadata, output_dir)
    cat("   Saved:", file.path(output_dir, "analysis_report.md"), "\n")

    # Summary -----------------------------------------------------------------
    cat("\n=== Export Complete ===\n")
    cat("\nOutput files in '", output_dir, "/':\n", sep = "")
    for (f in sort(list.files(output_dir))) {
        fsize <- file.size(file.path(output_dir, f))
        cat(sprintf("  %-42s %s\n", f, .format_size(fsize)))
    }
    cat("\n")
}


# --- Helpers -----------------------------------------------------------------

.format_size <- function(bytes) {
    if (is.na(bytes)) return("")
    if (bytes < 1024) return(paste0(bytes, " B"))
    if (bytes < 1024^2) return(sprintf("%.1f KB", bytes / 1024))
    sprintf("%.1f MB", bytes / 1024^2)
}

.composition_summary <- function(props_df, metadata, group_col = "group",
                                timepoint_col = "timepoint") {
    ct_cols <- setdiff(colnames(props_df), "sample_id")
    m <- merge(props_df, metadata, by = "sample_id")
    if (!group_col %in% colnames(m)) {
        warning(".composition_summary(): metadata has no '", group_col,
                "' column; returning empty frame.")
        return(data.frame())
    }
    keys <- c(group_col,
              if (!is.null(timepoint_col) && timepoint_col %in% colnames(m)) timepoint_col)
    out <- list()
    for (ct in ct_cols) {
        a <- stats::aggregate(m[[ct]], by = m[keys], FUN = mean)
        names(a)[ncol(a)] <- "mean_fraction"
        a$sd_fraction <- stats::aggregate(m[[ct]], by = m[keys], FUN = stats::sd)$x
        a$cell_type <- ct
        out[[ct]] <- a
    }
    res <- do.call(rbind, out); rownames(res) <- NULL
    res[order(res$cell_type), ]
}

#' Per-cell-type and overall Pearson r + RMSE vs known truth.
.recovery_metrics <- function(consensus, ground_truth) {
    cts <- intersect(setdiff(colnames(consensus), "sample_id"),
                     setdiff(colnames(ground_truth), "sample_id"))
    e <- consensus[match(ground_truth$sample_id, consensus$sample_id), , drop = FALSE]
    rows <- list()
    all_e <- c(); all_t <- c()
    for (ct in cts) {
        ev <- e[[ct]]; tv <- ground_truth[[ct]]
        all_e <- c(all_e, ev); all_t <- c(all_t, tv)
        rows[[ct]] <- data.frame(
            cell_type = ct,
            pearson_r = suppressWarnings(stats::cor(ev, tv)),
            rmse = sqrt(mean((ev - tv)^2)),
            mean_abs_error = mean(abs(ev - tv)), stringsAsFactors = FALSE)
    }
    overall <- data.frame(cell_type = "__overall__",
                          pearson_r = suppressWarnings(stats::cor(all_e, all_t)),
                          rmse = sqrt(mean((all_e - all_t)^2)),
                          mean_abs_error = mean(abs(all_e - all_t)),
                          stringsAsFactors = FALSE)
    rbind(overall, do.call(rbind, rows))
}


# --- Markdown report ---------------------------------------------------------
.generate_markdown_report <- function(deconv, contrasts, recovery, metadata, output_dir) {
    rs <- deconv$reference_summary
    L <- c(
        "# Cell-Type Deconvolution Report", "",
        paste("**Date:**", Sys.Date()),
        paste("**Methods:**", paste(deconv$methods, collapse = ", "),
              "(license-clean; CIBERSORTx/EPIC excluded)"),
        "",
        "## Summary", "",
        paste("- **Bulk samples deconvolved:**", length(deconv$samples)),
        paste("- **Cell types:**", length(deconv$cell_types), "--",
              paste(deconv$cell_types, collapse = ", ")),
        paste("- **Reference:**", rs$n_cells, "cells,", rs$n_donors, "donor(s),",
              rs$n_genes, "genes"),
        paste("- **Shared genes (bulk and reference):**", deconv$n_shared_genes),
        paste("- **Bulk scale:**", deconv$bulk_scale),
        "",
        "## Methods", "",
        paste("Cell-type proportions were estimated with a multi-method panel (",
              paste(deconv$methods, collapse = ", "),
              ") run via direct method calls."),
        "No single deconvolution method is universally best; reference quality and",
        "linear-scale preprocessing dominate accuracy, so a panel is run and",
        "cross-method concordance reported. Non-commercial methods (CIBERSORTx, EPIC)",
        "are excluded by license. Group differences in proportions were tested with",
        "Wilcoxon rank-sum and BH-FDR correction.",
        ""
    )

    # Concordance
    if (!is.null(deconv$concordance) && nrow(deconv$concordance) > 0) {
        ov <- deconv$concordance[deconv$concordance$cell_type == "__overall__", ]
        L <- c(L, "## Cross-method concordance", "",
               "| Method A | Method B | Pearson r |", "|---|---|---|")
        for (i in seq_len(nrow(ov)))
            L <- c(L, sprintf("| %s | %s | %.3f |", ov$method_a[i], ov$method_b[i], ov$pearson_r[i]))
        if (length(deconv$fragile_cell_types))
            L <- c(L, "", paste("**Method-fragile cell types (interpret with caution):**",
                                paste(deconv$fragile_cell_types, collapse = ", ")))
        L <- c(L, "")
    }

    # Recovery
    if (!is.null(recovery)) {
        ov <- recovery[recovery$cell_type == "__overall__", ]
        L <- c(L, "## Ground-truth recovery", "",
               sprintf("Overall Pearson r = **%.3f**, RMSE = **%.3f** between consensus",
                       ov$pearson_r, ov$rmse),
               "estimates and known proportions.", "")
    }

    # Contrasts
    if (!is.null(contrasts) && nrow(contrasts) > 0) {
        sig <- contrasts[which(contrasts$significant), ]
        L <- c(L, "## Group contrasts (top results)", "",
               "| Cell type | Timepoint | median A | median B | diff (B-A) | p | BH-FDR |",
               "|---|---|---|---|---|---|---|")
        top <- utils::head(contrasts, 12)
        for (i in seq_len(nrow(top)))
            L <- c(L, sprintf("| %s | %s | %.3f | %.3f | %+.3f | %.3g | %.3g |",
                              top$cell_type[i], top$timepoint[i], top$median_a[i],
                              top$median_b[i], top$median_diff_b_minus_a[i],
                              top$p_value[i], top$padj_BH[i]))
        L <- c(L, "",
               paste0("**", nrow(sig), "** cell type x timepoint contrast(s) significant at BH-FDR < 0.05",
                      if (nrow(sig) > 0) paste0(": ",
                          paste(unique(paste0(sig$cell_type, " (", sig$timepoint, ")")), collapse = ", ")) else "."),
               "")
    }

    L <- c(L, "## Output files", "",
           "| File | Description |", "|---|---|",
           "| proportions_<method>.csv | Per-method cell-type fractions (samples x cell type) |",
           "| consensus_proportions.csv | Cross-method mean fractions |",
           "| method_concordance.csv | Pairwise Pearson r (overall + per cell type) |",
           "| proportion_contrasts.csv | Group contrasts with BH-FDR |",
           "| composition_summary.csv | Mean +/- SD fraction per group/timepoint |",
           "| cell_type_deconvolution.rds | Full analysis object (downstream input) |",
           "",
           "## References", "",
           "- Avila Cobos F, et al. Benchmarking of cell type deconvolution pipelines for transcriptomics data. Nat Commun. 2020;11:5650.",
           "- Chu T, et al. Cell type and gene expression deconvolution with BayesPrism. Nat Cancer. 2022;3:505-517.",
           "- Tsoucas D, et al. Accurate estimation of cell-type composition from gene expression data (DWLS). Nat Commun. 2019;10:2975.")

    writeLines(L, file.path(output_dir, "analysis_report.md"))
}
