# =============================================================================
# 02_wilcoxon_de.R
# Wilcoxon rank-sum differential expression for small serum proteomics cohorts
#
# Purpose:
#   Non-parametric DE for discovery cohorts (typically n=8–15 per group) where
#   normality cannot be assumed. Handles MNAR proteins separately: they are
#   flagged from 01_mnar_detection.R and excluded from the Wilcoxon test.
#   log2FC is computed as the difference of group medians (on log2 scale).
#
# Inputs:
#   intensity_matrix  - data.frame, rows = proteins, cols = sample_ids (log2)
#   metadata          - data.frame: sample_id, patient_id, timepoint, response_group
#   mnar_table        - output$mnar_table from run_mnar_detection() [optional]
#   params            - list of analysis parameters
#
# Outputs:
#   de_results.csv    - full DE table with log2FC, p-value, adj_p, MNAR flag
#   Returns:          - list with $de_table
# =============================================================================

suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
})

# -----------------------------------------------------------------------------
# Main function
# -----------------------------------------------------------------------------

run_wilcoxon_de <- function(
    intensity_matrix,
    metadata,
    mnar_table  = NULL,
    params      = list()
) {
  p <- modifyList(
    list(
      de_timepoint        = "T1",          # timepoint for baseline DE
      timepoint_col       = "timepoint",
      response_col        = "response_group",
      sample_col          = "sample_id",
      group_a             = NULL,          # "numerator" group (e.g. "Early")
      group_b             = NULL,          # "denominator" group (e.g. "Poor")
      de_adj_p            = 0.05,
      de_log2fc           = 0.58,          # ~1.5-fold
      min_detected_frac   = 0.5,           # min fraction detected in ≥1 group
      log2_transform      = TRUE           # apply log2 if not already done
    ),
    params
  )

  cat("=== Wilcoxon Differential Expression ===\n")
  cat(sprintf("Timepoint: %s\n", p$de_timepoint))

  # --- Subset to DE timepoint ---
  meta_de <- metadata %>%
    filter(.data[[p$timepoint_col]] == p$de_timepoint)

  de_samples <- intersect(meta_de[[p$sample_col]], colnames(intensity_matrix))
  mat_de <- intensity_matrix[, de_samples, drop = FALSE]
  meta_de <- meta_de[meta_de[[p$sample_col]] %in% de_samples, ]

  # --- Determine groups ---
  all_groups <- unique(meta_de[[p$response_col]])
  if (is.null(p$group_a) || is.null(p$group_b)) {
    if (length(all_groups) < 2) stop("Need at least 2 response groups.")
    p$group_a <- as.character(all_groups[1])
    p$group_b <- as.character(all_groups[2])
    cat(sprintf("Auto-selected groups: A=%s vs B=%s\n", p$group_a, p$group_b))
  }
  cat(sprintf("Comparison: %s (A) vs %s (B)\n", p$group_a, p$group_b))
  cat(sprintf("log2FC = median(A) - median(B)\n"))

  samps_a <- meta_de[[p$sample_col]][meta_de[[p$response_col]] == p$group_a]
  samps_b <- meta_de[[p$sample_col]][meta_de[[p$response_col]] == p$group_b]
  samps_a <- intersect(samps_a, colnames(mat_de))
  samps_b <- intersect(samps_b, colnames(mat_de))

  cat(sprintf("n(A)=%d, n(B)=%d\n", length(samps_a), length(samps_b)))

  # --- Optional log2 transform ---
  if (p$log2_transform) {
    # Only transform if values look like raw intensities (median > 100)
    med_val <- median(as.matrix(mat_de), na.rm = TRUE)
    if (med_val > 100) {
      cat("Applying log2 transform (values appear to be raw intensities).\n")
      mat_de <- log2(mat_de + 1)
    } else {
      cat("Values appear already log2-transformed (median = %.2f). Skipping transform.\n",
          med_val)
    }
  }

  # --- Filter: keep proteins detected in ≥50% of at least one group ---
  det_a <- rowMeans(!is.na(mat_de[, samps_a, drop = FALSE]))
  det_b <- rowMeans(!is.na(mat_de[, samps_b, drop = FALSE]))
  keep  <- (det_a >= p$min_detected_frac) | (det_b >= p$min_detected_frac)
  mat_filt <- mat_de[keep, , drop = FALSE]
  cat(sprintf("Proteins passing detection filter: %d / %d\n",
              sum(keep), nrow(mat_de)))

  # --- Wilcoxon test per protein ---
  de_rows <- lapply(rownames(mat_filt), function(prot) {
    vals_a <- as.numeric(mat_filt[prot, samps_a])
    vals_b <- as.numeric(mat_filt[prot, samps_b])

    vals_a_obs <- vals_a[!is.na(vals_a)]
    vals_b_obs <- vals_b[!is.na(vals_b)]

    if (length(vals_a_obs) < 2 || length(vals_b_obs) < 2) {
      return(data.frame(
        protein    = prot,
        log2FC     = NA_real_,
        median_A   = ifelse(length(vals_a_obs) > 0, median(vals_a_obs), NA_real_),
        median_B   = ifelse(length(vals_b_obs) > 0, median(vals_b_obs), NA_real_),
        n_A        = length(vals_a_obs),
        n_B        = length(vals_b_obs),
        wilcox_p   = NA_real_,
        adj_p      = NA_real_,
        direction  = NA_character_,
        sig        = FALSE,
        stringsAsFactors = FALSE
      ))
    }

    wt <- tryCatch(
      wilcox.test(vals_a_obs, vals_b_obs, exact = FALSE),
      error   = function(e) list(p.value = NA_real_),
      warning = function(w) suppressWarnings(
        wilcox.test(vals_a_obs, vals_b_obs, exact = FALSE)
      )
    )

    log2fc <- median(vals_a_obs, na.rm = TRUE) - median(vals_b_obs, na.rm = TRUE)

    data.frame(
      protein    = prot,
      log2FC     = log2fc,
      median_A   = median(vals_a_obs),
      median_B   = median(vals_b_obs),
      n_A        = length(vals_a_obs),
      n_B        = length(vals_b_obs),
      wilcox_p   = wt$p.value,
      adj_p      = NA_real_,
      direction  = ifelse(log2fc > 0,
                          paste0("UP_in_", p$group_a),
                          paste0("DOWN_in_", p$group_a)),
      sig        = FALSE,
      stringsAsFactors = FALSE
    )
  })

  de_table <- bind_rows(de_rows)

  # --- BH correction ---
  testable <- !is.na(de_table$wilcox_p)
  de_table$adj_p[testable] <- p.adjust(de_table$wilcox_p[testable], method = "BH")

  # --- Significance flag ---
  de_table$sig <- !is.na(de_table$adj_p) &
    de_table$adj_p < p$de_adj_p &
    abs(de_table$log2FC) >= p$de_log2fc

  # --- Merge MNAR flags ---
  if (!is.null(mnar_table)) {
    mnar_cols <- mnar_table %>%
      select(protein, is_mnar, mnar_class, mnar_direction, fisher_p, fisher_adj_p)
    de_table <- left_join(de_table, mnar_cols, by = "protein")
  } else {
    de_table$is_mnar        <- FALSE
    de_table$mnar_class     <- "not_tested"
    de_table$mnar_direction <- NA_character_
    de_table$fisher_p       <- NA_real_
    de_table$fisher_adj_p   <- NA_real_
  }

  # --- Add proteins that are MNAR-only (not in quantified set) ---
  if (!is.null(mnar_table)) {
    mnar_only <- mnar_table %>%
      filter(is_mnar, !protein %in% de_table$protein) %>%
      mutate(
        log2FC    = NA_real_,
        median_A  = NA_real_,
        median_B  = NA_real_,
        n_A       = NA_integer_,
        n_B       = NA_integer_,
        wilcox_p  = NA_real_,
        adj_p     = NA_real_,
        direction = mnar_direction,
        sig       = FALSE
      ) %>%
      select(protein, log2FC, median_A, median_B, n_A, n_B,
             wilcox_p, adj_p, direction, sig,
             is_mnar, mnar_class, mnar_direction, fisher_p, fisher_adj_p)
    de_table <- bind_rows(de_table, mnar_only)
  }

  # --- Sort ---
  de_table <- de_table %>%
    arrange(adj_p, desc(abs(log2FC)))

  # --- Summary ---
  n_sig   <- sum(de_table$sig, na.rm = TRUE)
  n_mnar  <- sum(de_table$is_mnar, na.rm = TRUE)
  n_up    <- sum(de_table$sig & grepl("UP", de_table$direction), na.rm = TRUE)
  n_down  <- sum(de_table$sig & grepl("DOWN", de_table$direction), na.rm = TRUE)

  cat(sprintf("\nSignificant DE proteins: %d (UP=%d, DOWN=%d)\n", n_sig, n_up, n_down))
  cat(sprintf("MNAR proteins included: %d\n", n_mnar))
  cat("\nTop 10 by adj_p:\n")
  print(
    head(de_table[, c("protein","log2FC","wilcox_p","adj_p","direction","is_mnar")], 10),
    row.names = FALSE
  )

  cat("\n✓ Wilcoxon DE complete.\n")
  list(
    de_table   = de_table,
    group_a    = p$group_a,
    group_b    = p$group_b,
    n_sig      = n_sig,
    n_mnar     = n_mnar
  )
}


# -----------------------------------------------------------------------------
# Helper: quick volcano summary (text only, no plot)
# -----------------------------------------------------------------------------

summarise_volcano <- function(de_table, adj_p_thresh = 0.05, log2fc_thresh = 0.58) {
  sig <- de_table %>%
    filter(!is.na(adj_p), adj_p < adj_p_thresh, abs(log2FC) >= log2fc_thresh)

  cat(sprintf("\n--- Volcano Summary (adj_p<%.2f, |log2FC|>%.2f) ---\n",
              adj_p_thresh, log2fc_thresh))
  cat(sprintf("Total significant: %d\n", nrow(sig)))
  cat(sprintf("  UP:   %d\n", sum(grepl("UP",   sig$direction))))
  cat(sprintf("  DOWN: %d\n", sum(grepl("DOWN", sig$direction))))
  cat(sprintf("  MNAR: %d\n", sum(sig$is_mnar, na.rm = TRUE)))

  if (nrow(sig) > 0) {
    cat("\nTop hits:\n")
    print(head(sig[, c("protein","log2FC","adj_p","direction","is_mnar")], 10),
          row.names = FALSE)
  }
  invisible(sig)
}
