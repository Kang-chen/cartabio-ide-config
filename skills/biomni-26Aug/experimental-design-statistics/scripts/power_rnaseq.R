# RNA-seq Power Analysis
# Functions for calculating statistical power and required sample size for bulk RNA-seq experiments
# Based on: Hart et al. (2013) J Comput Biol 20(12):970-978

# Guard-source the single variance identity from variance_model.R.
if (!exists("cv_to_dispersion", mode = "function")) {
  source("scripts/variance_model.R")
}

#' Warn when the unit of replication is technical rather than biological
#'
#' Count-data power assumes BIOLOGICAL replicates (the CV/dispersion is biological).
#' This emits a warning if a caller declares technical replicates, without changing
#' the statistics. For an enforced hard guard (e.g. qPCR/ΔCt), see
#' assert_biological_replication() in scripts/power_qpcr.R.
#'
#' @param n_unit "biological" or "technical"
#' @param fn_name Calling function name, used in the message
#' @return Invisibly TRUE if biological, FALSE if technical
#' @keywords internal
warn_if_technical_unit <- function(n_unit, fn_name = "this function") {
  if (identical(n_unit, "technical")) {
    warning(sprintf(
      "%s: n_unit='technical' was chosen. Power/sample-size here assumes BIOLOGICAL replicates and a BIOLOGICAL CV/dispersion. Technical replicates measure only technical noise (pseudoreplication) and do not support generalisable inference. The numbers are reported but should be treated as biological replicates.",
      fn_name), call. = FALSE)
    return(invisible(FALSE))
  }
  invisible(TRUE)
}


#' Calculate power for RNA-seq experiment
#'
#' @param mean_count Per-gene expected count (lambda0). This is the Poisson term
#'   in CV^2 = 1/lambda0 + BCV^2. NOT library size in reads or millions of reads.
#' @param n_per_group Number of biological replicates per condition
#' @param cv Coefficient of variation (biological variability)
#' @param fold_change Minimum fold-change to detect
#' @param alpha Significance threshold (default: 0.05)
#' @param n_unit Unit of replication: "biological" (default, required for valid inference)
#'   or "technical". Technical replicates measure only technical noise; choosing
#'   "technical" emits a warning. Does NOT alter the statistics.
#' @param depth DEPRECATED. Use mean_count instead. Maps to mean_count for one
#'   release with a warning.
#' @return Power estimate (0-1)
#' @export
#' @examples
#' # Calculate power for 3 replicates per group
#' calc_power_rnaseq(mean_count = 20, n_per_group = 3, cv = 0.4, fold_change = 2)
calc_power_rnaseq <- function(mean_count,
                              n_per_group,
                              cv,
                              fold_change,
                              alpha = 0.05,
                              n_unit = c("biological", "technical"),
                              depth = NULL) {

  # Check inputs
  if (!requireNamespace("RNASeqPower", quietly = TRUE)) {
    stop("Package 'RNASeqPower' is required. Install with BiocManager::install('RNASeqPower')")
  }

  # Deprecated depth -> mean_count mapping
  if (!is.null(depth)) {
    if (missing(mean_count)) {
      warning("Argument 'depth' is deprecated; use 'mean_count' (per-gene expected count, lambda0) instead. Mapping depth -> mean_count for this call only.", call. = FALSE)
      mean_count <- depth
    } else {
      stop("Both 'depth' (deprecated) and 'mean_count' were supplied. Use only 'mean_count'.")
    }
  }

  n_unit <- match.arg(n_unit)
  warn_if_technical_unit(n_unit, "calc_power_rnaseq")

  assert_count_scale(mean_count, "mean_count")
  if (mean_count <= 0 || n_per_group < 2 || cv <= 0 || fold_change <= 1) {
    stop("Invalid parameters: mean_count, n, and cv must be positive; fold_change must be >1; n must be >=2")
  }

  # Calculate power using RNASeqPower
  power <- RNASeqPower::rnapower(
    depth = mean_count,
    n = n_per_group,
    cv = cv,
    effect = fold_change,
    alpha = alpha
  )

  # Return formatted result
  result <- list(
    power = power,
    parameters = list(
      mean_count = mean_count,
      n_per_group = n_per_group,
      cv = cv,
      fold_change = fold_change,
      alpha = alpha,
      n_unit = n_unit
    ),
    interpretation = interpret_power(power),
    note = "Per-gene power without multiple testing correction. For experiment-wide (FDR-aware) calculations, use samplesize_from_pilot() or calc_samplesize_de() from scripts/sample_size_de.R."
  )

  cat("✓ Power analysis completed successfully!\n")
  cat(sprintf("  Power = %.3f for n=%d %s reps/group, FC=%.1f, CV=%.2f\n",
              power, n_per_group, n_unit, fold_change, cv))
  cat("  NOTE: Per-gene power (no multiple testing correction).\n")
  cat("  For FDR-aware power, use samplesize_from_pilot() from scripts/sample_size_de.R.\n")

  return(result)
}


#' Calculate required sample size for target power
#'
#' @param mean_count Per-gene expected count (lambda0). NOT library size in reads.
#' @param cv Coefficient of variation
#' @param fold_change Minimum fold-change to detect
#' @param target_power Target statistical power (default: 0.8)
#' @param alpha Significance threshold (default: 0.05)
#' @param n_unit Unit of replication: "biological" (default, required) or "technical".
#'   Choosing "technical" emits a warning. Does NOT alter the statistics.
#' @param depth DEPRECATED. Use mean_count instead. Maps to mean_count for one
#'   release with a warning.
#' @return Required sample size per group
#' @export
#' @examples
#' # Calculate required n for 80% power
#' calc_sample_size_rnaseq(mean_count = 20, cv = 0.4, fold_change = 2, target_power = 0.8)
calc_sample_size_rnaseq <- function(mean_count,
                                    cv,
                                    fold_change,
                                    target_power = 0.8,
                                    alpha = 0.05,
                                    n_unit = c("biological", "technical"),
                                    depth = NULL) {

  if (!requireNamespace("RNASeqPower", quietly = TRUE)) {
    stop("Package 'RNASeqPower' is required. Install with BiocManager::install('RNASeqPower')")
  }

  # Deprecated depth -> mean_count mapping
  if (!is.null(depth)) {
    if (missing(mean_count)) {
      warning("Argument 'depth' is deprecated; use 'mean_count' (per-gene expected count, lambda0) instead. Mapping depth -> mean_count for this call only.", call. = FALSE)
      mean_count <- depth
    } else {
      stop("Both 'depth' (deprecated) and 'mean_count' were supplied. Use only 'mean_count'.")
    }
  }

  n_unit <- match.arg(n_unit)
  warn_if_technical_unit(n_unit, "calc_sample_size_rnaseq")

  assert_count_scale(mean_count, "mean_count")
  if (mean_count <= 0 || cv <= 0 || fold_change <= 1 || target_power <= 0 || target_power >= 1) {
    stop("Invalid parameters: mean_count and cv must be positive; fold_change must be >1; power must be 0-1")
  }

  # Calculate required sample size
  required_n <- RNASeqPower::rnapower(
    depth = mean_count,
    cv = cv,
    effect = fold_change,
    alpha = alpha,
    power = target_power
  )

  # Round up to nearest integer
  required_n <- ceiling(required_n)

  # Return formatted result
  result <- list(
    required_n_per_group = required_n,
    parameters = list(
      mean_count = mean_count,
      cv = cv,
      fold_change = fold_change,
      target_power = target_power,
      alpha = alpha,
      n_unit = n_unit
    ),
    total_samples = required_n * 2,  # Assuming 2 conditions
    recommendation = recommend_sample_size(required_n, cv, fold_change)
  )

  cat("✓ Sample size calculation completed successfully!\n")
  cat(sprintf("  Required n = %d %s reps/group (%d total) for power=%.2f\n",
              required_n, n_unit, required_n * 2, target_power))

  return(result)
}


#' Estimate CV from pilot data
#'
#' @param pilot_counts Matrix of raw counts from pilot data (genes x samples)
#' @return Estimated CV
#' @export
#' @examples
#' # Estimate CV from pilot count matrix
#' # pilot_counts <- read.csv("pilot_data.csv", row.names = 1)
#' # cv_estimate <- estimate_cv_from_pilot(pilot_counts)
estimate_cv_from_pilot <- function(pilot_counts) {

  if (!is.matrix(pilot_counts) && !is.data.frame(pilot_counts)) {
    stop("pilot_counts must be a matrix or data frame")
  }

  # Convert to matrix if data frame
  if (is.data.frame(pilot_counts)) {
    pilot_counts <- as.matrix(pilot_counts)
  }

  # Remove low count genes (mean < 10)
  mean_counts <- rowMeans(pilot_counts)
  pilot_counts <- pilot_counts[mean_counts >= 10, ]

  # Calculate CV for each gene
  gene_means <- rowMeans(pilot_counts)
  gene_sds <- apply(pilot_counts, 1, sd)
  gene_cvs <- gene_sds / gene_means

  # Return median CV
  estimated_cv <- median(gene_cvs, na.rm = TRUE)

  result <- list(
    estimated_cv = estimated_cv,
    cv_range = quantile(gene_cvs, probs = c(0.25, 0.75), na.rm = TRUE),
    n_genes_used = nrow(pilot_counts),
    recommendation = recommend_cv(estimated_cv)
  )

  return(result)
}


#' Interpret power value
#' @keywords internal
interpret_power <- function(power) {
  if (power >= 0.9) {
    return("Excellent: High power to detect effect")
  } else if (power >= 0.8) {
    return("Good: Adequate power for most applications")
  } else if (power >= 0.7) {
    return("Moderate: Consider increasing sample size")
  } else {
    return("Low: Increase sample size or relax effect size threshold")
  }
}


#' Recommend sample size based on parameters
#' @keywords internal
recommend_sample_size <- function(n, cv, fc) {
  recommendation <- paste0(
    "Recommended: ", n, " replicates per group (", n * 2, " total samples)\n",
    "For CV=", round(cv, 2), " and fold-change=", fc, "\n"
  )

  if (n < 3) {
    recommendation <- paste0(recommendation, "Warning: n<3 is generally too low for RNA-seq\n")
  }

  if (n > 20) {
    recommendation <- paste0(recommendation, "Note: Consider if this large n is feasible. Could you detect larger effects with fewer samples?\n")
  }

  return(recommendation)
}


#' Recommend CV interpretation
#' @keywords internal
recommend_cv <- function(cv) {
  if (cv < 0.2) {
    return("Low variability: Typical of cell lines or controlled conditions")
  } else if (cv < 0.3) {
    return("Moderate variability: Typical of inbred mice or primary cells")
  } else if (cv < 0.5) {
    return("High variability: Typical of human samples or outbred animals")
  } else {
    return("Very high variability: Consider if experimental variation can be reduced")
  }
}


#' Calculate power for range of parameters (for plotting)
#'
#' @param mean_count Per-gene expected count (lambda0). NOT library size in reads.
#' @param n_range Vector of sample sizes to test
#' @param cv Coefficient of variation
#' @param fold_change Fold-change threshold
#' @param alpha Significance threshold
#' @return Data frame with n and corresponding power
#' @export
calc_power_range <- function(mean_count, n_range, cv, fold_change, alpha = 0.05) {

  if (!requireNamespace("RNASeqPower", quietly = TRUE)) {
    stop("Package 'RNASeqPower' is required.")
  }

  assert_count_scale(mean_count, "mean_count")

  power_values <- sapply(n_range, function(n) {
    RNASeqPower::rnapower(
      depth = mean_count,
      n = n,
      cv = cv,
      effect = fold_change,
      alpha = alpha
    )
  })

  result <- data.frame(
    n_per_group = n_range,
    power = power_values,
    mean_count = mean_count,
    cv = cv,
    fold_change = fold_change
  )

  return(result)
}


#' Generate complete design recommendation
#'
#' Computes per-gene (RNASeqPower) and FDR-aware (RnaSeqSampleSize) power,
#' reconciles them, and returns an honest recommendation with explicit tradeoffs.
#' This is the primary function agents should use instead of interpreting raw
#' power numbers independently.
#'
#' @param cv Coefficient of variation (default: 0.40 for human PBMC)
#' @param target_fc Target fold-change to detect (default: 1.5)
#' @param target_power Target power (default: 0.90)
#' @param mean_count Per-gene expected count (lambda0, default: 20). The Poisson
#'   term in CV^2 = 1/lambda0 + BCV^2. NOT library size in reads or millions of
#'   reads.
#' @param alpha Per-gene significance threshold (default: 0.05)
#' @param fdr FDR threshold for experiment-wide power (default: 0.05)
#' @param n_genes Total number of genes tested (default: 20000)
#' @param pi0 Proportion of non-DE genes (default: NULL = not stated; uses 0.9
#'   with a prominent warning and forces the pi0 sensitivity sweep above the
#'   headline recommendation)
#' @param library_depth_M Reporting-only library depth in millions of reads.
#'   Never enters any calculation. When NULL the recommendation text contains
#'   no "M reads" claim; when supplied it prints as context only.
#' @param tissue_type Optional tissue label for the CV source
#' @param depth DEPRECATED. Use mean_count instead. Maps to mean_count for one
#'   release with a warning.
#' @return List with recommendation text, power tables, and required sample sizes
#' @export
#' @examples
#' # Standard recommendation for human PBMC experiment
#' rec <- generate_design_recommendation(cv = 0.40, target_fc = 1.5, target_power = 0.90)
#' cat(rec$recommendation_text)
generate_design_recommendation <- function(cv = 0.40,
                                           target_fc = 1.5,
                                           target_power = 0.90,
                                           mean_count = 20,
                                           alpha = 0.05,
                                           fdr = 0.05,
                                           n_genes = 20000,
                                           pi0 = NULL,
                                           library_depth_M = NULL,
                                           tissue_type = NULL,
                                           depth = NULL) {

  if (!requireNamespace("RNASeqPower", quietly = TRUE)) {
    stop("Package 'RNASeqPower' is required.")
  }

  # Deprecated depth -> mean_count mapping
  if (!is.null(depth)) {
    if (missing(mean_count)) {
      warning("Argument 'depth' is deprecated; use 'mean_count' (per-gene expected count, lambda0) instead. Mapping depth -> mean_count for this call only.", call. = FALSE)
      mean_count <- depth
    } else {
      stop("Both 'depth' (deprecated) and 'mean_count' were supplied. Use only 'mean_count'.")
    }
  }

  assert_count_scale(mean_count, "mean_count")

  has_fdr <- requireNamespace("RnaSeqSampleSize", quietly = TRUE)

  # pi0 handling: NULL means "not stated" — use 0.9 but warn prominently
  pi0_stated <- !is.null(pi0)
  if (!pi0_stated) pi0 <- 0.9

  # Compute dispersion from CV via the single source of truth
  dispersion <- cv_to_dispersion(cv, mean_count = mean_count)
  m1 <- ceiling(n_genes * (1 - pi0))

  # --- Per-gene required n (RNASeqPower) ---
  per_gene_n_90 <- ceiling(RNASeqPower::rnapower(
    depth = mean_count, cv = cv, effect = target_fc, alpha = alpha, power = 0.90
  ))
  per_gene_n_80 <- ceiling(RNASeqPower::rnapower(
    depth = mean_count, cv = cv, effect = target_fc, alpha = alpha, power = 0.80
  ))

  # --- FDR-aware required n (RnaSeqSampleSize) ---
  fdr_n_90 <- NA
  fdr_n_80 <- NA
  if (has_fdr) {
    fdr_n_90 <- tryCatch(
      ceiling(RnaSeqSampleSize::sample_size(
        power = 0.90, m = n_genes, m1 = m1, f = fdr,
        rho = target_fc, lambda0 = mean_count, phi0 = dispersion
      )), error = function(e) NA)
    fdr_n_80 <- tryCatch(
      ceiling(RnaSeqSampleSize::sample_size(
        power = 0.80, m = n_genes, m1 = m1, f = fdr,
        rho = target_fc, lambda0 = mean_count, phi0 = dispersion
      )), error = function(e) NA)
  }

  # --- Sample-size grid for the power table (derived from the recommendation) ---
  # The exported power table (power_analysis_results.csv, design_parameters.json)
  # and the power-vs-n figure must span the sample sizes the report leads with, so
  # a reader can look the recommendation up directly and see the curve reach or
  # exceed the target power AT the recommendation instead of terminating below it.
  # We KEEP the low-n pilot anchors and EXTEND the grid past the largest headline n.
  #
  # n_fc2_* (FC>=2 budget n) are computed here, before the grid, so the budget
  # recommendation can also be included as an exact, look-up-able row.
  n_fc2_80 <- ceiling(RNASeqPower::rnapower(
    depth = mean_count, cv = cv, effect = 2.0, alpha = alpha, power = 0.80
  ))
  n_fc2_90 <- ceiling(RNASeqPower::rnapower(
    depth = mean_count, cv = cv, effect = 2.0, alpha = alpha, power = 0.90
  ))

  # Every sample size the report leads with, guaranteed as exact rows in the grid.
  key_ns <- c(per_gene_n_80, per_gene_n_90, fdr_n_80, fdr_n_90, n_fc2_90)
  key_ns <- unique(key_ns[is.finite(key_ns) & key_ns >= 1])
  rec_n_max <- max(key_ns)
  low_n <- c(3, 5, 8, 10, 15, 20)  # pilot / low-n anchors (kept)
  # Extend past the largest recommendation (to a multiple of 5, and at least 5
  # beyond) so the curve visibly plateaus at/above target -- not terminating on it.
  upper_n <- max(5 * ceiling(rec_n_max * 1.25 / 5), rec_n_max + 5)
  extended_n <- if (upper_n >= 25) seq(25, upper_n, by = 5) else integer(0)
  n_values <- sort(unique(as.integer(c(low_n, key_ns, extended_n, upper_n))))
  n_values <- n_values[n_values >= 2]

  # Loud guard: the grid MUST contain every headline n and extend beyond the
  # largest one, so a truncated grid can never reach export. This cannot trigger
  # on a correct construction; it catches future regressions at the source.
  if (!all(key_ns %in% n_values) || max(n_values) <= rec_n_max) {
    stop(sprintf(
      paste0("Power-table grid does not span the recommendation ",
             "(largest headline n = %d, grid max = %d, missing = {%s}). ",
             "This is a grid-construction bug in generate_design_recommendation()."),
      rec_n_max, max(n_values),
      paste(setdiff(key_ns, n_values), collapse = ", ")))
  }

  fold_changes <- c(1.5, 2.0, 3.0)
  power_table <- expand.grid(n = n_values, fc = fold_changes)
  power_table$per_gene <- NA
  power_table$fdr_aware <- NA

  for (i in seq_len(nrow(power_table))) {
    power_table$per_gene[i] <- RNASeqPower::rnapower(
      depth = mean_count, n = power_table$n[i], cv = cv,
      effect = power_table$fc[i], alpha = alpha
    )
    if (has_fdr) {
      power_table$fdr_aware[i] <- tryCatch(
        RnaSeqSampleSize::est_power(
          n = power_table$n[i], rho = power_table$fc[i],
          lambda0 = mean_count, phi0 = dispersion, f = fdr,
          m = n_genes, m1 = m1
        ), error = function(e) NA)
    }
  }

  # Reshape to wide format for display
  power_wide <- data.frame(n = n_values)
  for (fc in fold_changes) {
    fc_data <- power_table[power_table$fc == fc, ]
    col_pg <- paste0("FC", fc, "_per_gene")
    col_fdr <- paste0("FC", fc, "_fdr_aware")
    power_wide[[col_pg]] <- fc_data$per_gene
    power_wide[[col_fdr]] <- fc_data$fdr_aware
  }

  # --- CV sensitivity (common tissue types) ---
  cv_values <- unique(sort(c(0.20, 0.30, cv, 0.50, 0.60)))
  cv_values <- cv_values[cv_values > 0]
  cv_labels <- c("0.20" = "cell lines", "0.30" = "sorted cells",
                 "0.40" = "PBMCs", "0.50" = "whole tissue",
                 "0.60" = "heterogeneous clinical")
  cv_sensitivity <- data.frame(cv = cv_values, per_gene_n_80 = NA, tissue_hint = NA_character_)
  for (i in seq_along(cv_values)) {
    cv_sensitivity$per_gene_n_80[i] <- ceiling(RNASeqPower::rnapower(
      depth = mean_count, cv = cv_values[i], effect = target_fc,
      alpha = alpha, power = 0.80
    ))
    cv_key <- sprintf("%.2f", cv_values[i])
    cv_sensitivity$tissue_hint[i] <- if (cv_key %in% names(cv_labels)) cv_labels[[cv_key]] else ""
  }

  # --- pi0 (DE proportion) sensitivity sweep ---
  # Mirrors cv_sensitivity construction. The assumed DE proportion moves
  # required n more than any other parameter currently swept.
  de_props <- c(0.01, 0.05, 0.10, 0.20)
  pi0_sensitivity <- data.frame(
    de_proportion = de_props,
    pi0 = 1 - de_props,
    de_genes = ceiling(n_genes * de_props),
    fdr_n_per_group = NA_real_
  )
  if (has_fdr) {
    for (i in seq_along(de_props)) {
      pi0_sensitivity$fdr_n_per_group[i] <- tryCatch(
        ceiling(RnaSeqSampleSize::sample_size(
          power = target_power, m = n_genes,
          m1 = ceiling(n_genes * de_props[i]), f = fdr,
          rho = target_fc, lambda0 = mean_count, phi0 = dispersion
        )), error = function(e) NA)
    }
  }

  # --- mean_count (lambda0) sensitivity sweep ---
  mean_count_values <- c(5, 10, 20, 50, 100, 300)
  mean_count_values <- unique(sort(c(mean_count_values[mean_count_values >= 1], mean_count)))
  mean_count_sensitivity <- data.frame(
    mean_count_per_gene = mean_count_values,
    dispersion = NA_real_,
    fdr_n_per_group = NA_real_
  )
  if (has_fdr) {
    for (i in seq_along(mean_count_values)) {
      mc <- mean_count_values[i]
      disp_mc <- cv_to_dispersion(cv, mean_count = mc)
      mean_count_sensitivity$dispersion[i] <- disp_mc
      mean_count_sensitivity$fdr_n_per_group[i] <- tryCatch(
        ceiling(RnaSeqSampleSize::sample_size(
          power = target_power, m = n_genes, m1 = m1, f = fdr,
          rho = target_fc, lambda0 = mc, phi0 = disp_mc
        )), error = function(e) NA)
    }
  }

  # --- Find practical recommendation ---
  # n_fc2_80 / n_fc2_90 (FC>=2 budget n) are computed above, before the power-table
  # grid, so the grid can include the budget recommendation as an exact row.

  # --- Build recommendation text ---
  txt <- character()
  txt <- c(txt, "=== SAMPLE SIZE RECOMMENDATION ===\n")

  # CV assumption warning (prominent, at top)
  if (!is.null(tissue_type)) {
    txt <- c(txt, sprintf("\nUsing CV = %.2f (%s)", cv, tissue_type))
  } else {
    txt <- c(txt, sprintf("\n\u26a0\ufe0f  Using CV = %.2f (assumed \u2014 tissue type not confirmed)", cv))
    txt <- c(txt, "  CV varies substantially by sample type (0.2 for cell lines to 0.6+ for clinical samples).")
    txt <- c(txt, "  See CV sensitivity analysis below \u2014 your required n may differ significantly.")
  }

  # pi0 assumption warning (prominent, at top — before headline)
  if (!pi0_stated) {
    txt <- c(txt, sprintf(
      "\n\u26a0\ufe0f  Using DE proportion = %.0f%% (assumed \u2014 pi0 not stated by caller)",
      (1 - pi0) * 100))
    txt <- c(txt, "  The assumed DE proportion moves required n substantially (see pi0 sensitivity below).")
    txt <- c(txt, "  State pi0 explicitly when you have a defensible estimate.")
  }

  # Target line — never claims "M reads" unless library_depth_M is supplied
  target_line <- sprintf("Target: Detect FC >= %.1f at %.0f%% power (CV=%.2f, mean count/gene=%g, FDR<%.2f)",
                          target_fc, target_power * 100, cv, mean_count, fdr)
  if (!is.null(library_depth_M)) {
    target_line <- paste0(target_line,
      sprintf("\n  assumed library depth: %gM reads (context only, not an input to the power model)",
              library_depth_M))
  }
  txt <- c(txt, target_line, "")

  # pi0 sensitivity (emitted ABOVE the headline recommendation when pi0 not stated)
  if (has_fdr && !all(is.na(pi0_sensitivity$fdr_n_per_group))) {
    txt <- c(txt, sprintf("Sensitivity to DE proportion assumption (FDR-aware, %.0f%% power, FC=%.1f):",
                           target_power * 100, target_fc))
    for (i in seq_len(nrow(pi0_sensitivity))) {
      marker <- if (pi0_sensitivity$de_proportion[i] == (1 - pi0)) " <-- your assumption" else ""
      txt <- c(txt, sprintf("  DE=%.0f%% (pi0=%.2f, %d DE genes) -> n = %s per group%s",
                             pi0_sensitivity$de_proportion[i] * 100,
                             pi0_sensitivity$pi0[i],
                             pi0_sensitivity$de_genes[i],
                             if (is.na(pi0_sensitivity$fdr_n_per_group[i])) "NA" else as.character(pi0_sensitivity$fdr_n_per_group[i]),
                             marker))
    }
    txt <- c(txt, "")
  }

  txt <- c(txt, sprintf("\nPer-gene power (RNASeqPower, no multiple testing correction):"))
  txt <- c(txt, sprintf("  Required n = %d per group (%d total) for 90%% power at FC=%.1f",
                         per_gene_n_90, per_gene_n_90 * 2, target_fc))
  txt <- c(txt, sprintf("  Required n = %d per group (%d total) for 80%% power at FC=%.1f",
                         per_gene_n_80, per_gene_n_80 * 2, target_fc))

  if (has_fdr) {
    txt <- c(txt, sprintf("\nFDR-aware power (RnaSeqSampleSize, accounts for %s tests):",
                           format(n_genes, big.mark = ",")))
    if (!is.na(fdr_n_90)) {
      txt <- c(txt, sprintf("  Required n = %d per group (%d total) for 90%% FDR-aware power at FC=%.1f",
                             fdr_n_90, fdr_n_90 * 2, target_fc))
    } else {
      txt <- c(txt, sprintf("  90%% FDR-aware power at FC=%.1f may require very large n (>100)", target_fc))
    }
    if (!is.na(fdr_n_80)) {
      txt <- c(txt, sprintf("  Required n = %d per group (%d total) for 80%% FDR-aware power at FC=%.1f",
                             fdr_n_80, fdr_n_80 * 2, target_fc))
    }
  }

  # Check if ANY FDR-aware values were computed
  any_fdr <- any(!is.na(power_table$fdr_aware))
  col_suffix <- if (any_fdr) " (per-gene/FDR)" else " (per-gene)"

  txt <- c(txt, "\nPower at practical sample sizes:")
  txt <- c(txt, sprintf("  %-4s | %-22s | %-22s | %-22s",
                         "n", paste0("FC=", fold_changes[1], col_suffix),
                         paste0("FC=", fold_changes[2], col_suffix),
                         paste0("FC=", fold_changes[3], col_suffix)))
  for (n_val in n_values) {
    row_parts <- character()
    for (fc in fold_changes) {
      pg <- power_table$per_gene[power_table$n == n_val & power_table$fc == fc]
      fa <- power_table$fdr_aware[power_table$n == n_val & power_table$fc == fc]
      if (!is.na(fa)) {
        row_parts <- c(row_parts, sprintf("%-22s", sprintf("%.0f%% / %.0f%%", pg * 100, fa * 100)))
      } else {
        row_parts <- c(row_parts, sprintf("%-22s", sprintf("%.0f%%", pg * 100)))
      }
    }
    txt <- c(txt, sprintf("  %-4d | %s", n_val, paste(row_parts, collapse = " | ")))
  }
  if (!any_fdr) {
    txt <- c(txt, "  \u26a0\ufe0f  FDR-aware power unavailable (RnaSeqSampleSize not installed).")
    txt <- c(txt, "  All values are per-gene only and UNDERESTIMATE the sample size needed for experiment-wide discovery.")
  }

  # Tradeoff warning
  power_at_8_fc15 <- power_table$per_gene[power_table$n == 8 & power_table$fc == target_fc]
  fdr_at_8_fc15 <- power_table$fdr_aware[power_table$n == 8 & power_table$fc == target_fc]
  txt <- c(txt, "\nPRACTICAL TRADEOFF:")
  txt <- c(txt, sprintf("  n=8 per group achieves only %.0f%% per-gene power for FC=%.1f%s.",
                         power_at_8_fc15 * 100, target_fc,
                         if (!is.na(fdr_at_8_fc15)) sprintf(" (%.0f%% FDR-aware)", fdr_at_8_fc15 * 100) else ""))
  txt <- c(txt, sprintf("  For FC>=2.0: n>=%d for 80%% per-gene power, n>=%d for 90%%.", n_fc2_80, n_fc2_90))
  txt <- c(txt, sprintf("  For FC=%.1f at 80%% per-gene power: n>=%d per group (%d total).",
                         target_fc, per_gene_n_80, per_gene_n_80 * 2))

  # Per-gene vs FDR-aware caveat (BEFORE recommendation so agents see it first)
  if (target_power >= 0.85) {
    rec_n <- per_gene_n_90
    rec_label <- "90%"
  } else {
    rec_n <- per_gene_n_80
    rec_label <- "80%"
  }

  if (!any_fdr) {
    fdr_est_n <- ceiling(rec_n * 1.5)
    txt <- c(txt, "\n\u26a0\ufe0f  IMPORTANT: Per-gene vs. experiment-wide power")
    txt <- c(txt, "  The values above are PER-GENE power estimates (no multiple testing correction).")
    txt <- c(txt, "  FDR-aware power across ~20,000 genes is typically 1.5-2x lower, requiring more samples.")
    txt <- c(txt, "  Install RnaSeqSampleSize for exact FDR-aware estimates (see Installation).")
  } else {
    txt <- c(txt, "\n\u26a0\ufe0f  IMPORTANT: Per-gene vs. experiment-wide power")
    txt <- c(txt, "  Per-gene power overestimates discovery rate. The FDR-aware column reflects realistic")
    txt <- c(txt, "  detection after multiple testing correction across all genes.")
  }

  # Primary recommendation as a RANGE
  if (has_fdr && !is.na(if (target_power >= 0.85) fdr_n_90 else fdr_n_80)) {
    fdr_rec_n <- if (target_power >= 0.85 && !is.na(fdr_n_90)) fdr_n_90 else fdr_n_80
    txt <- c(txt, sprintf("\nRECOMMENDED RANGE: n = %d\u2013%d per group (%d\u2013%d total)",
                           rec_n, fdr_rec_n, rec_n * 2, fdr_rec_n * 2))
    txt <- c(txt, sprintf("  n = %d: %s per-gene power at FC=%.1f (optimistic, single-gene test)",
                           rec_n, rec_label, target_fc))
    txt <- c(txt, sprintf("  n = %d: %s FDR-aware power at FC=%.1f (accounts for multiple testing)",
                           fdr_rec_n, rec_label, target_fc))
    txt <- c(txt, "  For grant proposals targeting experiment-wide discovery, use the FDR-aware estimate.")
  } else if (!any_fdr) {
    fdr_est_n <- ceiling(rec_n * 1.5)
    txt <- c(txt, sprintf("\nRECOMMENDED RANGE: n = %d\u2013%d per group (%d\u2013%d total)",
                           rec_n, rec_n * 2, rec_n * 2, rec_n * 4))
    txt <- c(txt, sprintf("  n = %d: %s per-gene power at FC=%.1f (optimistic, single-gene test)",
                           rec_n, rec_label, target_fc))
    txt <- c(txt, sprintf("  n ~ %d\u2013%d: estimated for equivalent FDR-aware power (1.5\u20132x multiplier)",
                           fdr_est_n, rec_n * 2))
    txt <- c(txt, "  Without RnaSeqSampleSize, exact FDR-aware n cannot be computed \u2014 treat upper bound seriously.")
  } else {
    # FDR package installed but computation returned NA (very large n needed)
    txt <- c(txt, sprintf("\nRECOMMENDED RANGE: n >= %d per group (%d+ total)",
                           rec_n, rec_n * 2))
    txt <- c(txt, sprintf("  n = %d: %s per-gene power at FC=%.1f (optimistic, single-gene test)",
                           rec_n, rec_label, target_fc))
    txt <- c(txt, sprintf("  FDR-aware %s power at FC=%.1f may require n > 100 per group.",
                           rec_label, target_fc))
  }
  pg_at_budget <- RNASeqPower::rnapower(
    depth = mean_count, n = n_fc2_90, cv = cv, effect = target_fc, alpha = alpha
  )
  txt <- c(txt, sprintf("  If budget-constrained: n = %d per group for 90%% per-gene power at FC>=2.0",
                         n_fc2_90))
  txt <- c(txt, sprintf("  (but only %.0f%% for FC=%.1f).", pg_at_budget * 100, target_fc))

  # CV sensitivity (expanded to common tissue types)
  txt <- c(txt, sprintf("\nSensitivity to CV assumption (per-gene, 80%% power, FC=%.1f):", target_fc))
  for (i in seq_len(nrow(cv_sensitivity))) {
    marker <- if (cv_sensitivity$cv[i] == cv) " <-- your CV" else ""
    hint <- if (!is.na(cv_sensitivity$tissue_hint[i]) && nchar(cv_sensitivity$tissue_hint[i]) > 0)
      sprintf(" (%s)", cv_sensitivity$tissue_hint[i]) else ""
    txt <- c(txt, sprintf("  CV=%.2f%s -> n = %d per group%s",
                           cv_sensitivity$cv[i], hint, cv_sensitivity$per_gene_n_80[i], marker))
  }

  # mean_count sensitivity (shows n is nearly flat in lambda0 at fixed CV)
  if (has_fdr && !all(is.na(mean_count_sensitivity$fdr_n_per_group))) {
    txt <- c(txt, sprintf("\nSensitivity to mean count/gene assumption (FDR-aware, %.0f%% power, FC=%.1f):",
                           target_power * 100, target_fc))
    for (i in seq_len(nrow(mean_count_sensitivity))) {
      marker <- if (mean_count_sensitivity$mean_count_per_gene[i] == mean_count) " <-- your assumption" else ""
      txt <- c(txt, sprintf("  mean count/gene=%g (disp=%.4f) -> n = %s per group%s",
                             mean_count_sensitivity$mean_count_per_gene[i],
                             mean_count_sensitivity$dispersion[i],
                             if (is.na(mean_count_sensitivity$fdr_n_per_group[i])) "NA" else as.character(mean_count_sensitivity$fdr_n_per_group[i]),
                             marker))
    }
    txt <- c(txt, "  (n is nearly flat in mean_count at fixed CV because dispersion = CV^2 - 1/lambda0")
    txt <- c(txt, "   absorbs the change — this is a parameterization artifact, not robustness.)")
  }

  # Replicates vs. count tradeoff (show at both rec_n and low n for context)
  pwr_at_rec <- RNASeqPower::rnapower(depth = mean_count, n = rec_n, cv = cv, effect = target_fc, alpha = alpha)
  txt <- c(txt, sprintf("\nReplicates vs. per-gene count tradeoff (per-gene power at n=%d, FC=%.1f):", rec_n, target_fc))
  for (mc in c(10, 20, 40)) {
    pwr_at_mc <- RNASeqPower::rnapower(depth = mc, n = rec_n, cv = cv, effect = target_fc, alpha = alpha)
    txt <- c(txt, sprintf("  mean count/gene=%d: %.0f%% power", mc, pwr_at_mc * 100))
  }
  pwr_extra_n <- RNASeqPower::rnapower(depth = mean_count, n = rec_n + 2, cv = cv, effect = target_fc, alpha = alpha)
  txt <- c(txt, sprintf("  mean count/gene=%g + 2 extra replicates (n=%d): %.0f%% power", mean_count, rec_n + 2, pwr_extra_n * 100))
  if (pwr_at_rec > 0.85) {
    txt <- c(txt, sprintf("  Note: At n=%d, power is already %.0f%%, so gains from both count and replicates", rec_n, pwr_at_rec * 100))
    txt <- c(txt, "  are marginal. At lower n (e.g., n=5-10), adding replicates has dramatically more impact.")
  }
  # Show comparison at n=5 where replicate advantage is clearer
  pwr_n5_20 <- RNASeqPower::rnapower(depth = 20, n = 5, cv = cv, effect = target_fc, alpha = alpha)
  pwr_n5_40 <- RNASeqPower::rnapower(depth = 40, n = 5, cv = cv, effect = target_fc, alpha = alpha)
  pwr_n7_20 <- RNASeqPower::rnapower(depth = 20, n = 7, cv = cv, effect = target_fc, alpha = alpha)
  txt <- c(txt, sprintf("  Comparison at low n: n=5 at count=20=%.0f%%, n=5 at count=40=%.0f%%, n=7 at count=20=%.0f%%",
                         pwr_n5_20 * 100, pwr_n5_40 * 100, pwr_n7_20 * 100))
  txt <- c(txt, "  (Adding 2 replicates beats doubling per-gene count, especially at small sample sizes.)")

  # Practical notes
  txt <- c(txt, "\nNote: Adding more replicates has greater impact on DE detection than deeper")
  txt <- c(txt, "sequencing at typical depths. Consider IHW instead of BH-FDR for")
  txt <- c(txt, "~5-10% more discoveries at the same FDR threshold.")

  recommendation_text <- paste(txt, collapse = "\n")

  cat(recommendation_text, "\n")
  cat("\n✓ Design recommendation generated successfully!\n")

  return(list(
    recommendation_text = recommendation_text,
    per_gene_required_n = list(power_90 = per_gene_n_90, power_80 = per_gene_n_80),
    fdr_required_n = list(power_90 = fdr_n_90, power_80 = fdr_n_80),
    power_table = power_wide,
    power_table_long = power_table,
    cv_sensitivity = cv_sensitivity,
    pi0_sensitivity = pi0_sensitivity,
    mean_count_sensitivity = mean_count_sensitivity,
    budget_constrained_n = n_fc2_90,
    parameters = list(
      cv = cv, target_fc = target_fc, target_power = target_power,
      mean_count = mean_count, alpha = alpha, fdr = fdr, n_genes = n_genes,
      pi0 = pi0, pi0_stated = pi0_stated, dispersion = dispersion,
      library_depth_M = library_depth_M
    )
  ))
}
