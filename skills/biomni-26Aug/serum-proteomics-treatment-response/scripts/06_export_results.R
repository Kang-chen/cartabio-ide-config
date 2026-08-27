# =============================================================================
# 06_export_results.R
# Export all serum proteomics analysis results to CSV and RDS
#
# Purpose:
#   Collect outputs from all pipeline modules and write them to the results/
#   directory. Produces a self-contained analysis_object.rds for downstream
#   skills (lasso-biomarker-panel, functional-enrichment-from-degs).
#
# Usage:
#   source("scripts/06_export_results.R")
#   export_all(
#     mnar_result    = mnar_result,
#     de_result      = de_result,
#     lme_result     = lme_result,       # optional
#     cluster_result = cluster_result,   # optional
#     tier_result    = tier_result,
#     output_dir     = "results"
#   )
# =============================================================================

suppressPackageStartupMessages({
  library(dplyr)
})

# -----------------------------------------------------------------------------
# Main export function
# -----------------------------------------------------------------------------

export_all <- function(
    mnar_result    = NULL,
    de_result      = NULL,
    lme_result     = NULL,
    cluster_result = NULL,
    tier_result    = NULL,
    output_dir     = "results",
    study_label    = "serum_proteomics"
) {
  dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
  cat(sprintf("=== Exporting results to: %s/ ===\n", output_dir))

  files_written <- character(0)

  # --- 1. MNAR results ---
  if (!is.null(mnar_result) && !is.null(mnar_result$mnar_table)) {
    path <- file.path(output_dir, "mnar_results.csv")
    write.csv(mnar_result$mnar_table, path, row.names = FALSE)
    cat(sprintf("  ✓ mnar_results.csv (%d proteins)\n",
                nrow(mnar_result$mnar_table)))
    files_written <- c(files_written, path)
  }

  # --- 2. DE results ---
  if (!is.null(de_result) && !is.null(de_result$de_table)) {
    path <- file.path(output_dir, "de_results.csv")
    write.csv(de_result$de_table, path, row.names = FALSE)
    cat(sprintf("  ✓ de_results.csv (%d proteins, %d significant)\n",
                nrow(de_result$de_table),
                sum(de_result$de_table$sig, na.rm = TRUE)))
    files_written <- c(files_written, path)

    # Significant subset
    sig_de <- de_result$de_table %>% filter(sig | is_mnar)
    if (nrow(sig_de) > 0) {
      path_sig <- file.path(output_dir, "de_results_significant.csv")
      write.csv(sig_de, path_sig, row.names = FALSE)
      cat(sprintf("  ✓ de_results_significant.csv (%d proteins)\n", nrow(sig_de)))
      files_written <- c(files_written, path_sig)
    }
  }

  # --- 3. LME results ---
  if (!is.null(lme_result) && !is.null(lme_result$lme_table)) {
    path <- file.path(output_dir, "lme_results.csv")
    write.csv(lme_result$lme_table, path, row.names = FALSE)
    cat(sprintf("  ✓ lme_results.csv (%d proteins, %d sig interaction)\n",
                nrow(lme_result$lme_table),
                sum(lme_result$lme_table$sig_interaction, na.rm = TRUE)))
    files_written <- c(files_written, path)
  }

  # --- 4. Trajectory clusters ---
  if (!is.null(cluster_result) && !is.null(cluster_result$cluster_table)) {
    path <- file.path(output_dir, "trajectory_clusters.csv")
    write.csv(cluster_result$cluster_table, path, row.names = FALSE)
    cat(sprintf("  ✓ trajectory_clusters.csv (%d patients, k=%d)\n",
                nrow(cluster_result$cluster_table),
                cluster_result$best_k))
    files_written <- c(files_written, path)

    # Flare→Clear patients
    if ("flare_clear_flag" %in% colnames(cluster_result$cluster_table)) {
      fc <- cluster_result$cluster_table %>% filter(flare_clear_flag)
      if (nrow(fc) > 0) {
        path_fc <- file.path(output_dir, "flare_clear_patients.csv")
        write.csv(fc, path_fc, row.names = FALSE)
        cat(sprintf("  ✓ flare_clear_patients.csv (%d patients)\n", nrow(fc)))
        files_written <- c(files_written, path_fc)
      }
    }
  }

  # --- 5. Biomarker tier table ---
  if (!is.null(tier_result) && !is.null(tier_result$tier_table)) {
    path <- file.path(output_dir, "biomarker_tiers.csv")
    write.csv(tier_result$tier_table, path, row.names = FALSE)
    tier_counts <- table(tier_result$tier_table$tier)
    cat(sprintf("  ✓ biomarker_tiers.csv (Tier1=%d, Tier2=%d, Tier3=%d)\n",
                tier_counts["1"] %||% 0,
                tier_counts["2"] %||% 0,
                tier_counts["3"] %||% 0))
    files_written <- c(files_written, path)

    # Tier 1 only
    t1 <- tier_result$tier_table %>% filter(tier == 1)
    if (nrow(t1) > 0) {
      path_t1 <- file.path(output_dir, "biomarker_tier1_priority.csv")
      write.csv(t1, path_t1, row.names = FALSE)
      cat(sprintf("  ✓ biomarker_tier1_priority.csv (%d proteins)\n", nrow(t1)))
      files_written <- c(files_written, path_t1)
    }
  }

  # --- 6. Analysis object (RDS) ---
  analysis_object <- list(
    study_label    = study_label,
    timestamp      = Sys.time(),
    mnar_result    = mnar_result,
    de_result      = de_result,
    lme_result     = lme_result,
    cluster_result = cluster_result,
    tier_result    = tier_result
  )
  path_rds <- file.path(output_dir, "analysis_object.rds")
  saveRDS(analysis_object, path_rds)
  cat(sprintf("  ✓ analysis_object.rds (complete pipeline object)\n"))
  files_written <- c(files_written, path_rds)

  # --- 7. Text summary ---
  summary_path <- file.path(output_dir, "analysis_summary.txt")
  .write_summary(analysis_object, summary_path)
  cat(sprintf("  ✓ analysis_summary.txt\n"))
  files_written <- c(files_written, summary_path)

  cat(sprintf("\n=== Export Complete: %d files written ===\n", length(files_written)))
  cat("Files:\n")
  for (f in files_written) cat(sprintf("  %s\n", f))

  invisible(files_written)
}


# -----------------------------------------------------------------------------
# Helper: write plain-text summary
# -----------------------------------------------------------------------------

.write_summary <- function(obj, path) {
  lines <- c(
    "=== Serum Proteomics Treatment-Response Analysis Summary ===",
    sprintf("Study label: %s", obj$study_label),
    sprintf("Timestamp:   %s", format(obj$timestamp, "%Y-%m-%d %H:%M:%S")),
    "",
    "--- MNAR Detection ---"
  )

  if (!is.null(obj$mnar_result)) {
    mt <- obj$mnar_result$mnar_table
    lines <- c(lines,
      sprintf("  Proteins tested:   %d", nrow(mt)),
      sprintf("  MNAR significant:  %d", sum(mt$is_mnar, na.rm = TRUE)),
      sprintf("  Top MNAR proteins: %s",
              paste(head(mt$protein[mt$is_mnar], 5), collapse = ", "))
    )
  } else lines <- c(lines, "  Not run.")

  lines <- c(lines, "", "--- Differential Expression (Wilcoxon) ---")
  if (!is.null(obj$de_result)) {
    dt <- obj$de_result$de_table
    lines <- c(lines,
      sprintf("  Comparison:        %s vs %s",
              obj$de_result$group_a, obj$de_result$group_b),
      sprintf("  Proteins tested:   %d", nrow(dt)),
      sprintf("  Significant DE:    %d", sum(dt$sig, na.rm = TRUE)),
      sprintf("  UP in group A:     %d",
              sum(dt$sig & grepl("UP", dt$direction), na.rm = TRUE)),
      sprintf("  DOWN in group A:   %d",
              sum(dt$sig & grepl("DOWN", dt$direction), na.rm = TRUE))
    )
  } else lines <- c(lines, "  Not run.")

  lines <- c(lines, "", "--- Longitudinal LME ---")
  if (!is.null(obj$lme_result)) {
    lt <- obj$lme_result$lme_table
    lines <- c(lines,
      sprintf("  Proteins modelled: %d", nrow(lt)),
      sprintf("  Sig interaction:   %d", sum(lt$sig_interaction, na.rm = TRUE)),
      sprintf("  T2-surge proteins: %d",
              sum(lt$trajectory_type == "T2_surge", na.rm = TRUE)),
      sprintf("  Model failures:    %d",
              sum(lt$model_status != "converged", na.rm = TRUE))
    )
  } else lines <- c(lines, "  Not run.")

  lines <- c(lines, "", "--- Trajectory Clustering ---")
  if (!is.null(obj$cluster_result)) {
    ct <- obj$cluster_result$cluster_table
    lines <- c(lines,
      sprintf("  Patients clustered: %d", nrow(ct)),
      sprintf("  Optimal k:          %d", obj$cluster_result$best_k),
      sprintf("  Flare→Clear:        %d",
              sum(ct$flare_clear_flag, na.rm = TRUE))
    )
  } else lines <- c(lines, "  Not run.")

  lines <- c(lines, "", "--- Biomarker Tiers ---")
  if (!is.null(obj$tier_result)) {
    tt <- obj$tier_result$tier_table
    tc <- table(tt$tier)
    lines <- c(lines,
      sprintf("  Tier 1 (★★★): %d", tc["1"] %||% 0),
      sprintf("  Tier 2 (★★):  %d", tc["2"] %||% 0),
      sprintf("  Tier 3 (★):   %d", tc["3"] %||% 0),
      "",
      "  Top Tier 1 proteins:"
    )
    t1 <- tt %>% filter(tier == 1) %>% head(10)
    for (i in seq_len(nrow(t1))) {
      lines <- c(lines, sprintf("    %s | %s | %s",
                                t1$protein[i],
                                t1$tier_stars[i],
                                substr(t1$mechanism[i], 1, 60)))
    }
  } else lines <- c(lines, "  Not run.")

  lines <- c(lines, "",
    "--- Downstream Skills ---",
    "  lasso-biomarker-panel:          load analysis_object.rds → de_result$de_table",
    "  functional-enrichment-from-degs: use de_results_significant.csv protein list",
    "  proteomics-diff-exp:            upstream PSM aggregation if needed"
  )

  writeLines(lines, path)
}


# -----------------------------------------------------------------------------
# Null-coalescing operator
# -----------------------------------------------------------------------------

`%||%` <- function(a, b) if (!is.null(a) && length(a) > 0 && !is.na(a[1])) a else b
