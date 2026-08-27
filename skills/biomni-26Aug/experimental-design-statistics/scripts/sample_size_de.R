# Sample Size Estimation for Differential Expression
# FDR-aware sample size estimation using RnaSeqSampleSize
# Per-gene estimates via RNASeqPower for comparison
#
# Key references:
#   Li et al. (2018) BMC Bioinformatics 19:191 - RnaSeqSampleSize
#   Hart et al. (2013) J Comput Biol 20(12):970-978 - RNASeqPower

# Guard-source the single variance identity from variance_model.R.
# This file defines cv_to_dispersion(), dispersion_to_cv(), and
# assert_count_scale() — the ONLY place the CV^2 = 1/lambda0 + BCV^2 identity
# is written. If power_rnaseq.R was sourced first it may already exist.
if (!exists("cv_to_dispersion", mode = "function")) {
  source("scripts/variance_model.R")
}

# Replication-unit warning helper. Defined defensively so this script remains
# self-sourcing; if power_rnaseq.R is also sourced, that definition is used instead.
if (!exists("warn_if_technical_unit")) {
  warn_if_technical_unit <- function(n_unit, fn_name = "this function") {
    if (identical(n_unit, "technical")) {
      warning(sprintf(
        "%s: n_unit='technical' was chosen. Power/sample-size here assumes BIOLOGICAL replicates and a BIOLOGICAL CV/dispersion. Technical replicates measure only technical noise (pseudoreplication) and do not support generalisable inference.",
        fn_name), call. = FALSE)
      return(invisible(FALSE))
    }
    invisible(TRUE)
  }
}


#' Calculate FDR-aware sample size for differential expression
#'
#' Uses RnaSeqSampleSize to determine the required n per group while
#' controlling false discovery rate across all genes.
#'
#' @param cv Coefficient of variation (biological). Converted to dispersion internally.
#' @param fold_change Minimum fold-change to detect (default: 1.5)
#' @param power Target statistical power (default: 0.9)
#' @param fdr Target false discovery rate (default: 0.05)
#' @param n_genes Total number of genes tested (default: 20000)
#' @param pi0 Proportion of non-DE genes (default: 0.9, i.e., 10% DE)
#' @param mean_count Average read count per gene (lambda0, default: 20)
#' @param dispersion Dispersion parameter. If NULL, computed from cv via cv_to_dispersion().
#' @param n_unit Unit of replication: "biological" (default, required) or "technical".
#'   The CV/dispersion is biological; choosing "technical" emits a warning. Does NOT
#'   alter the statistics.
#' @return List with required_n_per_group, total_samples, parameters, and power_at_n
#' @export
#' @examples
#' # FDR-aware sample size for human PBMC (CV=0.40)
#' calc_samplesize_de(cv = 0.4, fold_change = 1.5, power = 0.9)
calc_samplesize_de <- function(cv = 0.4,
                               fold_change = 1.5,
                               power = 0.9,
                               fdr = 0.05,
                               n_genes = 20000,
                               pi0 = 0.9,
                               mean_count = 20,
                               dispersion = NULL,
                               n_unit = c("biological", "technical")) {

  if (!requireNamespace("RnaSeqSampleSize", quietly = TRUE)) {
    stop("Package 'RnaSeqSampleSize' is required. Install with BiocManager::install('RnaSeqSampleSize')")
  }

  n_unit <- match.arg(n_unit)
  warn_if_technical_unit(n_unit, "calc_samplesize_de")

  # Validate inputs
  assert_count_scale(mean_count, "mean_count")
  if (cv <= 0) stop("cv must be positive")
  if (fold_change <= 1) stop("fold_change must be > 1")
  if (power <= 0 || power >= 1) stop("power must be between 0 and 1")
  if (fdr <= 0 || fdr >= 1) stop("fdr must be between 0 and 1")
  if (pi0 <= 0 || pi0 >= 1) stop("pi0 must be between 0 and 1")

  # Compute dispersion from CV if not provided
  if (is.null(dispersion)) {
    dispersion <- cv_to_dispersion(cv, mean_count = mean_count)
  } else {
    # Validate consistency between cv and dispersion
    expected_disp <- cv_to_dispersion(cv, mean_count = mean_count)
    if (abs(dispersion - expected_disp) / expected_disp > 0.3) {
      warning(sprintf(
        "Provided dispersion (%.3f) differs >30%% from CV-implied value (%.3f). Using provided dispersion.",
        dispersion, expected_disp
      ))
    }
  }

  # Number of DE genes
  m1 <- ceiling(n_genes * (1 - pi0))

  # FDR-aware sample size via RnaSeqSampleSize
  required_n <- RnaSeqSampleSize::sample_size(
    power = power,
    m = n_genes,
    m1 = m1,
    f = fdr,
    rho = fold_change,
    lambda0 = mean_count,
    phi0 = dispersion
  )

  required_n <- ceiling(required_n)

  # Also compute power at this n for verification
  achieved_power <- tryCatch({
    RnaSeqSampleSize::est_power(
      n = required_n,
      rho = fold_change,
      lambda0 = mean_count,
      phi0 = dispersion,
      f = fdr,
      m = n_genes,
      m1 = m1
    )
  }, error = function(e) NA)

  output <- list(
    required_n_per_group = required_n,
    total_samples = required_n * 2,
    parameters = list(
      cv = cv,
      dispersion = dispersion,
      fold_change = fold_change,
      fdr = fdr,
      target_power = power,
      n_genes = n_genes,
      de_genes = m1,
      pi0 = pi0,
      mean_count = mean_count,
      n_unit = n_unit
    ),
    achieved_power = achieved_power,
    method = "RnaSeqSampleSize (FDR-aware, negative binomial exact test)",
    recommendation = recommend_sample_size_de(required_n, fold_change)
  )

  cat("✓ FDR-aware sample size estimation completed successfully!\n")
  cat(sprintf("  Required n = %d %s reps/group (%d total samples)\n", required_n, n_unit, required_n * 2))
  cat(sprintf("  For detecting FC=%.1f at %.0f%% power (FDR<%.2f)\n", fold_change, power * 100, fdr))
  cat(sprintf("  Dispersion = %.3f (from CV=%.2f)\n", dispersion, cv))

  return(output)
}


#' Calculate FDR-aware sample size from pilot DESeq2 data
#'
#' Extracts dispersion and mean count from a DESeq2 object, then uses
#' RnaSeqSampleSize to determine the required sample size.
#'
#' @param pilot_dds DESeq2 object with pilot data (dispersions estimated)
#' @param fold_change Minimum fold-change to detect
#' @param power Target power (default: 0.9)
#' @param fdr Target FDR (default: 0.05)
#' @param expected_de_prop Expected proportion of DE genes (default: 0.1)
#' @return List with required_n_per_group, pilot_parameters, and recommendation
#' @export
samplesize_from_pilot <- function(pilot_dds,
                                  fold_change,
                                  power = 0.9,
                                  fdr = 0.05,
                                  expected_de_prop = 0.1) {

  if (!requireNamespace("DESeq2", quietly = TRUE)) {
    stop("Package 'DESeq2' is required.")
  }
  if (!requireNamespace("RnaSeqSampleSize", quietly = TRUE)) {
    stop("Package 'RnaSeqSampleSize' is required. Install with BiocManager::install('RnaSeqSampleSize')")
  }

  # Ensure dispersions are estimated
  if (is.null(DESeq2::dispersions(pilot_dds))) {
    pilot_dds <- DESeq2::estimateSizeFactors(pilot_dds)
    pilot_dds <- DESeq2::estimateDispersions(pilot_dds)
  }

  # Extract parameters from pilot data
  median_disp <- median(DESeq2::dispersions(pilot_dds), na.rm = TRUE)
  counts <- DESeq2::counts(pilot_dds, normalized = TRUE)
  mean_count <- mean(rowMeans(counts))
  n_genes <- nrow(pilot_dds)
  m1 <- ceiling(n_genes * expected_de_prop)
  pi0 <- 1 - expected_de_prop

  # Compute CV from dispersion for reporting
  pilot_cv <- sqrt(median_disp)

  # FDR-aware sample size
  required_n <- RnaSeqSampleSize::sample_size(
    power = power,
    m = n_genes,
    m1 = m1,
    f = fdr,
    rho = fold_change,
    lambda0 = mean_count,
    phi0 = median_disp
  )

  required_n <- ceiling(required_n)

  output <- list(
    required_n_per_group = required_n,
    total_samples = required_n * 2,
    pilot_parameters = list(
      median_dispersion = median_disp,
      pilot_cv = pilot_cv,
      mean_count = mean_count,
      n_genes = n_genes,
      expected_de_genes = m1
    ),
    parameters = list(
      fold_change = fold_change,
      power = power,
      fdr = fdr
    ),
    method = "RnaSeqSampleSize (FDR-aware, pilot-derived dispersion)",
    recommendation = recommend_from_pilot_de(required_n, median_disp, fold_change)
  )

  cat("✓ Sample size estimation from pilot data completed successfully!\n")
  cat(sprintf("  Required n = %d per group (%d total samples)\n", required_n, required_n * 2))
  cat(sprintf("  Pilot median dispersion = %.3f (CV ≈ %.2f)\n", median_disp, pilot_cv))

  return(output)
}


#' Calculate sample size for paired design
#'
#' For paired designs (e.g., before/after treatment), variance is reduced.
#' Applies a conservative 30% dispersion reduction to account for pairing.
#'
#' @param pilot_dds DESeq2 object with pilot paired data
#' @param fold_change Minimum fold-change to detect
#' @param power Target power (default: 0.9)
#' @param fdr Target FDR (default: 0.05)
#' @return Required number of pairs
#' @export
paired_samplesize <- function(pilot_dds,
                              fold_change,
                              power = 0.9,
                              fdr = 0.05) {

  if (!requireNamespace("DESeq2", quietly = TRUE)) {
    stop("Package 'DESeq2' is required.")
  }
  if (!requireNamespace("RnaSeqSampleSize", quietly = TRUE)) {
    stop("Package 'RnaSeqSampleSize' is required.")
  }

  if (is.null(DESeq2::dispersions(pilot_dds))) {
    pilot_dds <- DESeq2::estimateSizeFactors(pilot_dds)
    pilot_dds <- DESeq2::estimateDispersions(pilot_dds)
  }

  median_disp <- median(DESeq2::dispersions(pilot_dds), na.rm = TRUE)
  adjusted_disp <- median_disp * 0.7  # Conservative 30% reduction for pairing

  counts <- DESeq2::counts(pilot_dds, normalized = TRUE)
  mean_count <- mean(rowMeans(counts))
  n_genes <- nrow(pilot_dds)
  m1 <- ceiling(n_genes * 0.1)

  required_pairs <- RnaSeqSampleSize::sample_size(
    power = power,
    m = n_genes,
    m1 = m1,
    f = fdr,
    rho = fold_change,
    lambda0 = mean_count,
    phi0 = adjusted_disp
  )

  required_pairs <- ceiling(required_pairs)

  output <- list(
    required_pairs = required_pairs,
    total_samples = required_pairs * 2,
    pilot_parameters = list(
      median_dispersion = median_disp,
      adjusted_dispersion = adjusted_disp,
      mean_count = mean_count,
      n_genes = n_genes
    ),
    parameters = list(
      fold_change = fold_change,
      power = power,
      fdr = fdr
    ),
    note = "Paired design: Required number is pairs (not independent samples). Dispersion adjusted by 0.7x for pairing.",
    recommendation = recommend_paired_design(required_pairs, fold_change)
  )

  cat("✓ Paired sample size estimation completed successfully!\n")
  cat(sprintf("  Required pairs = %d (%d total samples)\n", required_pairs, required_pairs * 2))

  return(output)
}


#' FDR-aware power for a given sample size
#'
#' Computes experiment-wide power accounting for multiple testing correction.
#'
#' @param n_per_group Sample size per group
#' @param cv Coefficient of variation
#' @param fold_change Fold-change to detect
#' @param fdr FDR threshold (default: 0.05)
#' @param mean_count Mean read count (default: 20)
#' @param n_genes Total genes (default: 20000)
#' @param pi0 Proportion of non-DE genes (default: 0.9, i.e., 10% DE). Sets the
#'   number of DE genes m1 = n_genes * (1 - pi0) passed to est_power(), so the
#'   FDR-aware power uses the same DE proportion as the sample-size headline.
#' @param dispersion Dispersion. If NULL, computed from cv.
#' @return FDR-aware power (0-1)
#' @export
calc_fdr_power <- function(n_per_group, cv = 0.4, fold_change = 1.5,
                           fdr = 0.05, mean_count = 20, n_genes = 20000,
                           pi0 = 0.9, dispersion = NULL) {

  if (!requireNamespace("RnaSeqSampleSize", quietly = TRUE)) {
    stop("Package 'RnaSeqSampleSize' is required.")
  }

  assert_count_scale(mean_count, "mean_count")

  if (is.null(dispersion)) {
    dispersion <- cv_to_dispersion(cv, mean_count = mean_count)
  }

  m1 <- ceiling(n_genes * (1 - pi0))
  power <- tryCatch({
    RnaSeqSampleSize::est_power(
      n = n_per_group,
      rho = fold_change,
      lambda0 = mean_count,
      phi0 = dispersion,
      f = fdr,
      m = n_genes,
      m1 = m1
    )
  }, error = function(e) {
    warning(sprintf("FDR-aware power calculation failed: %s", e$message))
    NA
  })

  return(power)
}


#' Quick reference table for sample sizes by effect size
#'
#' Shows both per-gene (RNASeqPower) and FDR-aware (RnaSeqSampleSize) estimates.
#'
#' @param cv Coefficient of variation (default: 0.4 for human samples)
#' @param target_power Target power (default: 0.8)
#' @param mean_count Per-gene expected count / lambda0 (default: 20)
#' @return Data frame with effect sizes and corresponding sample sizes
#' @export
quick_reference_samplesize <- function(cv = 0.4, target_power = 0.8, mean_count = 20) {

  if (!requireNamespace("RNASeqPower", quietly = TRUE)) {
    stop("Package 'RNASeqPower' is required.")
  }

  assert_count_scale(mean_count, "mean_count")

  effect_sizes <- c(1.25, 1.5, 2, 3, 4)
  per_gene_n <- numeric(length(effect_sizes))
  fdr_aware_n <- numeric(length(effect_sizes))

  dispersion <- cv_to_dispersion(cv, mean_count = mean_count)

  for (i in seq_along(effect_sizes)) {
    # Per-gene (no multiple testing correction)
    per_gene_n[i] <- ceiling(RNASeqPower::rnapower(
      depth = mean_count, cv = cv, effect = effect_sizes[i],
      alpha = 0.05, power = target_power
    ))

    # FDR-aware (accounts for 20,000 tests)
    fdr_aware_n[i] <- tryCatch({
      if (requireNamespace("RnaSeqSampleSize", quietly = TRUE)) {
        ceiling(RnaSeqSampleSize::sample_size(
          power = target_power, m = 20000, m1 = 2000, f = 0.05,
          rho = effect_sizes[i], lambda0 = mean_count, phi0 = dispersion
        ))
      } else {
        NA
      }
    }, error = function(e) NA)
  }

  result <- data.frame(
    fold_change = effect_sizes,
    per_gene_n = per_gene_n,
    fdr_aware_n = fdr_aware_n,
    per_gene_total = per_gene_n * 2,
    fdr_aware_total = fdr_aware_n * 2,
    difficulty = c("Very challenging", "Challenging", "Moderate", "Easy", "Very easy")
  )

  return(result)
}


#' Recommend sample size for DE
#' @keywords internal
recommend_sample_size_de <- function(n, fc) {
  recommendation <- paste0(
    "Recommended: ", n, " replicates per group (", n * 2, " total samples)\n",
    "For fold-change = ", fc, "\n"
  )

  if (n < 3) {
    recommendation <- paste0(recommendation, "Warning: Minimum 3 replicates recommended for RNA-seq\n")
  } else if (n >= 3 && n <= 6) {
    recommendation <- paste0(recommendation, "Good: This is a standard RNA-seq design\n")
  } else if (n > 12) {
    recommendation <- paste0(recommendation, "Large n required. Consider if budget allows, or detect larger effects.\n")
  }

  return(recommendation)
}


#' Recommend based on pilot data
#' @keywords internal
recommend_from_pilot_de <- function(n, disp, fc) {
  recommendation <- paste0(
    "Based on pilot data:\n",
    "  Required: ", n, " replicates per group\n",
    "  Observed dispersion: ", round(disp, 3), "\n",
    "  Target fold-change: ", fc, "\n\n"
  )

  if (disp > 0.5) {
    recommendation <- paste0(
      recommendation,
      "High dispersion in pilot data. Consider:\n",
      "  1. Identifying and removing batch effects\n",
      "  2. Increasing sample size\n",
      "  3. Detecting larger fold-changes only\n"
    )
  }

  return(recommendation)
}


#' Recommend paired design
#' @keywords internal
recommend_paired_design <- function(n_pairs, fc) {
  recommendation <- paste0(
    "Paired design recommendation:\n",
    "  Required: ", n_pairs, " pairs (", n_pairs * 2, " total samples)\n",
    "  Target fold-change: ", fc, "\n\n",
    "Paired designs benefit from reduced variance.\n"
  )

  if (n_pairs < 5) {
    recommendation <- paste0(
      recommendation,
      "Warning: Small sample size (<5 pairs) may have limited power even with pairing.\n"
    )
  }

  return(recommendation)
}
