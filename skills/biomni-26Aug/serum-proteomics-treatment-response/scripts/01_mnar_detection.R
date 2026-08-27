# =============================================================================
# 01_mnar_detection.R
# Missing-Not-At-Random (MNAR) protein detection for serum proteomics
#
# Purpose:
#   Identify proteins whose absence from serum is non-random with respect to
#   treatment response group. In DIA-MS data, a protein below the detection
#   limit (NA) carries biological information distinct from a low-abundance
#   quantified protein. This script separates MNAR proteins from quantified
#   proteins before downstream DE analysis.
#
# Inputs (loaded from environment or passed as arguments):
#   intensity_matrix  - data.frame, rows = proteins, cols = sample_ids
#                       NA values indicate non-detection (below LOD)
#   metadata          - data.frame with columns: sample_id, patient_id,
#                       timepoint, response_group
#   params            - list of analysis parameters (see run_mnar_detection())
#
# Outputs:
#   mnar_results.csv  - per-protein MNAR classification table
#   Returns:          - list with $mnar_table and $mnar_proteins vector
# =============================================================================

suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
})

# -----------------------------------------------------------------------------
# Main function
# -----------------------------------------------------------------------------

run_mnar_detection <- function(
    intensity_matrix,
    metadata,
    params = list()
) {
  # --- Default parameters ---
  p <- modifyList(
    list(
      baseline_timepoint  = "T1",          # timepoint to use for MNAR test
      timepoint_col       = "timepoint",
      response_col        = "response_group",
      sample_col          = "sample_id",
      mnar_fisher_p       = 0.05,          # Fisher p threshold
      min_detected_any    = 2,             # min detections in any group to test
      responder_label     = NULL           # e.g. "Early"; NULL = first level
    ),
    params
  )

  cat("=== MNAR Detection ===\n")
  cat(sprintf("Baseline timepoint: %s\n", p$baseline_timepoint))
  cat(sprintf("Fisher p threshold: %.3f\n", p$mnar_fisher_p))

  # --- Subset metadata to baseline timepoint ---
  meta_t1 <- metadata %>%
    filter(.data[[p$timepoint_col]] == p$baseline_timepoint)

  if (nrow(meta_t1) == 0) {
    stop(sprintf(
      "No samples found for timepoint '%s'. Check params$baseline_timepoint.",
      p$baseline_timepoint
    ))
  }

  # --- Align matrix to baseline samples ---
  t1_samples <- meta_t1[[p$sample_col]]
  t1_samples <- intersect(t1_samples, colnames(intensity_matrix))

  if (length(t1_samples) == 0) {
    stop("No overlap between metadata sample_ids and intensity_matrix column names.")
  }

  mat_t1 <- intensity_matrix[, t1_samples, drop = FALSE]
  meta_t1 <- meta_t1[meta_t1[[p$sample_col]] %in% t1_samples, ]

  groups <- unique(meta_t1[[p$response_col]])
  cat(sprintf("Response groups: %s\n", paste(groups, collapse = ", ")))
  cat(sprintf("Proteins to test: %d\n", nrow(mat_t1)))

  # --- Build detection matrix (TRUE = detected, FALSE = NA) ---
  det_mat <- !is.na(mat_t1)

  # --- Per-protein Fisher test across all group pairs ---
  results <- lapply(rownames(mat_t1), function(prot) {
    det_vec <- det_mat[prot, ]

    # Build detection counts per group
    group_counts <- sapply(groups, function(g) {
      samps <- meta_t1[[p$sample_col]][meta_t1[[p$response_col]] == g]
      samps <- intersect(samps, names(det_vec))
      c(detected   = sum(det_vec[samps]),
        undetected = sum(!det_vec[samps]),
        n_total    = length(samps))
    }, simplify = FALSE)

    # Detection rates per group
    rates <- sapply(group_counts, function(x) x["detected"] / x["n_total"])
    names(rates) <- groups

    # Skip if too few detections across all groups
    total_detected <- sum(sapply(group_counts, function(x) x["detected"]))
    if (total_detected < p$min_detected_any) {
      return(data.frame(
        protein          = prot,
        stringsAsFactors = FALSE,
        check.names      = FALSE
      ) %>%
        bind_cols(as.data.frame(t(rates)) %>%
                    setNames(paste0("det_rate_", groups))) %>%
        mutate(
          fisher_p         = NA_real_,
          fisher_adj_p     = NA_real_,
          mnar_class       = "undetected_all",
          mnar_direction   = NA_character_,
          n_groups_tested  = length(groups)
        ))
    }

    # Fisher exact test: 2×2 for two groups, or omnibus for >2
    if (length(groups) == 2) {
      ct <- matrix(
        c(group_counts[[1]]["detected"],   group_counts[[1]]["undetected"],
          group_counts[[2]]["detected"],   group_counts[[2]]["undetected"]),
        nrow = 2,
        dimnames = list(c("detected", "undetected"), groups)
      )
      ft <- tryCatch(fisher.test(ct), error = function(e) list(p.value = NA_real_))
      fp <- ft$p.value

      # Direction: which group has HIGHER detection rate?
      direction <- if (!is.na(fp) && fp < p$mnar_fisher_p) {
        if (rates[1] > rates[2]) paste0("MNAR_up_in_", groups[1])
        else                      paste0("MNAR_up_in_", groups[2])
      } else NA_character_

    } else {
      # Omnibus Fisher for >2 groups (simulate p-value)
      ct <- do.call(cbind, lapply(group_counts, function(x) {
        c(x["detected"], x["undetected"])
      }))
      rownames(ct) <- c("detected", "undetected")
      ft <- tryCatch(
        fisher.test(ct, simulate.p.value = TRUE, B = 2000),
        error = function(e) list(p.value = NA_real_)
      )
      fp <- ft$p.value
      direction <- if (!is.na(fp) && fp < p$mnar_fisher_p) {
        max_g <- groups[which.max(rates)]
        paste0("MNAR_up_in_", max_g)
      } else NA_character_
    }

    # MNAR class
    mnar_class <- if (is.na(fp)) {
      "untestable"
    } else if (fp < p$mnar_fisher_p) {
      "MNAR_significant"
    } else {
      "detected_random"
    }

    row <- data.frame(protein = prot, stringsAsFactors = FALSE)
    for (g in groups) {
      row[[paste0("det_rate_", g)]]   <- rates[g]
      row[[paste0("n_detected_", g)]] <- group_counts[[g]]["detected"]
      row[[paste0("n_total_", g)]]    <- group_counts[[g]]["n_total"]
    }
    row$fisher_p        <- fp
    row$fisher_adj_p    <- NA_real_   # filled after loop
    row$mnar_class      <- mnar_class
    row$mnar_direction  <- direction
    row$n_groups_tested <- length(groups)
    row
  })

  mnar_table <- bind_rows(results)

  # BH correction on testable proteins
  testable <- !is.na(mnar_table$fisher_p)
  mnar_table$fisher_adj_p[testable] <- p.adjust(
    mnar_table$fisher_p[testable], method = "BH"
  )

  # Convenience flag columns
  mnar_table$is_mnar <- mnar_table$mnar_class == "MNAR_significant"

  # Summary
  n_mnar <- sum(mnar_table$is_mnar, na.rm = TRUE)
  cat(sprintf("\nMNAR proteins detected: %d / %d\n", n_mnar, nrow(mnar_table)))
  if (n_mnar > 0) {
    top <- mnar_table %>%
      filter(is_mnar) %>%
      arrange(fisher_p) %>%
      head(5)
    cat("Top MNAR proteins:\n")
    print(top[, c("protein", "fisher_p", "fisher_adj_p", "mnar_direction")],
          row.names = FALSE)
  }

  cat("\n✓ MNAR detection complete.\n")
  list(
    mnar_table   = mnar_table,
    mnar_proteins = mnar_table$protein[mnar_table$is_mnar]
  )
}


# -----------------------------------------------------------------------------
# Granin dissociation helper
# Checks for the CHGA-absent / SCG2-present pattern (low-reserve, high-flux)
# -----------------------------------------------------------------------------

check_granin_dissociation <- function(mnar_table, params = list()) {
  p <- modifyList(
    list(
      chga_name        = "CHGA",
      scg2_name        = "SCG2",
      responder_group  = NULL   # label of the "good responder" group
    ),
    params
  )

  chga_row <- mnar_table[mnar_table$protein == p$chga_name, ]
  scg2_row <- mnar_table[mnar_table$protein == p$scg2_name, ]

  if (nrow(chga_row) == 0 && nrow(scg2_row) == 0) {
    cat("Neither CHGA nor SCG2 found in MNAR table — skipping granin check.\n")
    return(invisible(NULL))
  }

  cat("\n=== Granin Dissociation Check ===\n")

  if (nrow(chga_row) > 0) {
    cat(sprintf("CHGA: mnar_class=%s | direction=%s | fisher_p=%.4g\n",
                chga_row$mnar_class, chga_row$mnar_direction, chga_row$fisher_p))
  }
  if (nrow(scg2_row) > 0) {
    cat(sprintf("SCG2: mnar_class=%s | direction=%s | fisher_p=%.4g\n",
                scg2_row$mnar_class, scg2_row$mnar_direction, scg2_row$fisher_p))
  }

  # Dissociation pattern: CHGA MNAR in responders, SCG2 detected in responders
  chga_mnar_in_resp <- nrow(chga_row) > 0 &&
    chga_row$is_mnar &&
    !is.null(p$responder_group) &&
    grepl(p$responder_group, chga_row$mnar_direction, fixed = TRUE) == FALSE

  if (nrow(chga_row) > 0 && nrow(scg2_row) > 0) {
    cat("\nInterpretation: ")
    if (chga_row$is_mnar && !scg2_row$is_mnar) {
      cat("CHGA MNAR (low granule reserve) + SCG2 detected (active secretion flux)\n")
      cat("=> 'Low-reserve, high-flux' chromaffin phenotype consistent with MMIP Early Responders.\n")
    } else {
      cat("Pattern does not match canonical granin dissociation — review manually.\n")
    }
  }

  invisible(list(chga = chga_row, scg2 = scg2_row))
}
