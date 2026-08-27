# Pilot Data-Based Power Analysis
# Functions for extracting parameters from pilot data and calculating power
# More accurate than literature CV estimates

# Guard-source the single variance identity from variance_model.R.
if (!exists("cv_to_dispersion", mode = "function")) {
  source("scripts/variance_model.R")
}

#' Extract dispersion parameters from DESeq2 object
#'
#' @param pilot_dds DESeq2 object from pilot data (must have run DESeq())
#' @return List with dispersion statistics
#' @export
#' @examples
#' # library(DESeq2)
#' # pilot_dds <- readRDS("pilot_deseq2_object.rds")
#' # dispersions <- extract_dispersion_deseq2(pilot_dds)
extract_dispersion_deseq2 <- function(pilot_dds) {

  if (!requireNamespace("DESeq2", quietly = TRUE)) {
    stop("Package 'DESeq2' is required. Install with BiocManager::install('DESeq2')")
  }

  if (!methods::is(pilot_dds, "DESeqDataSet")) {
    stop("pilot_dds must be a DESeq2DataSet object")
  }

  # Check if dispersions have been estimated
  if (is.null(DESeq2::dispersions(pilot_dds))) {
    message("Estimating dispersions...")
    pilot_dds <- DESeq2::estimateSizeFactors(pilot_dds)
    pilot_dds <- DESeq2::estimateDispersions(pilot_dds)
  }

  # Extract dispersion values
  gene_dispersions <- DESeq2::dispersions(pilot_dds)

  # Get summary statistics
  result <- list(
    median_dispersion = median(gene_dispersions, na.rm = TRUE),
    mean_dispersion = mean(gene_dispersions, na.rm = TRUE),
    dispersion_range = quantile(gene_dispersions, probs = c(0.25, 0.75), na.rm = TRUE),
    n_genes = length(gene_dispersions),
    fitted_dispersions = DESeq2::dispersions(pilot_dds),
    interpretation = interpret_dispersion(median(gene_dispersions, na.rm = TRUE))
  )

  return(result)
}


#' Calculate power from pilot DESeq2 data
#'
#' @param pilot_dds DESeq2 object from pilot data
#' @param n_per_group Proposed sample size per group
#' @param fold_change Minimum fold-change to detect
#' @param alpha Significance threshold (default: 0.05)
#' @param mean_count Per-gene expected count (lambda0). If NULL, derived from
#'   pilot data as mean(rowMeans(normalized counts)).
#' @return Power estimate
#' @export
#' @examples
#' # Calculate power using pilot data
#' # power <- calc_power_from_pilot(pilot_dds, n_per_group = 6, fold_change = 1.5)
calc_power_from_pilot <- function(pilot_dds,
                                  n_per_group,
                                  fold_change,
                                  alpha = 0.05,
                                  mean_count = NULL) {

  if (!requireNamespace("DESeq2", quietly = TRUE)) {
    stop("Package 'DESeq2' is required.")
  }

  if (!requireNamespace("RNASeqPower", quietly = TRUE)) {
    stop("Package 'RNASeqPower' is required.")
  }

  # Extract dispersion
  dispersion_params <- extract_dispersion_deseq2(pilot_dds)
  median_disp <- dispersion_params$median_dispersion

  # Derive mean_count (lambda0) from pilot data — same derivation as
  # samplesize_from_pilot() in sample_size_de.R
  if (is.null(mean_count)) {
    counts <- DESeq2::counts(pilot_dds, normalized = TRUE)
    mean_count <- mean(rowMeans(counts))
  }
  assert_count_scale(mean_count, "mean_count")

  # CV from dispersion only — the Poisson term 1/lambda0 is added inside
  # rnapower(), so we must NOT add it here (previous code added it twice)
  cv <- sqrt(median_disp)

  # Calculate power using RNASeqPower
  power <- RNASeqPower::rnapower(
    depth = mean_count,
    n = n_per_group,
    cv = cv,
    effect = fold_change,
    alpha = alpha
  )

  result <- list(
    power = power,
    parameters = list(
      n_per_group = n_per_group,
      fold_change = fold_change,
      alpha = alpha,
      mean_count = mean_count,
      estimated_cv = cv,
      median_dispersion = median_disp
    ),
    pilot_data_info = list(
      n_samples = ncol(pilot_dds),
      n_genes = nrow(pilot_dds)
    ),
    interpretation = interpret_power_pilot(power, cv)
  )

  return(result)
}


#' Calculate required sample size from pilot data
#'
#' @param pilot_dds DESeq2 object from pilot data
#' @param fold_change Minimum fold-change to detect
#' @param target_power Target statistical power (default: 0.8)
#' @param alpha Significance threshold (default: 0.05)
#' @param mean_count Per-gene expected count (lambda0). If NULL, derived from
#'   pilot data as mean(rowMeans(normalized counts)).
#' @return Required sample size per group
#' @export
calc_samplesize_from_pilot <- function(pilot_dds,
                                       fold_change,
                                       target_power = 0.8,
                                       alpha = 0.05,
                                       mean_count = NULL) {

  if (!requireNamespace("DESeq2", quietly = TRUE) || !requireNamespace("RNASeqPower", quietly = TRUE)) {
    stop("Packages 'DESeq2' and 'RNASeqPower' are required.")
  }

  # Extract parameters from pilot
  dispersion_params <- extract_dispersion_deseq2(pilot_dds)
  median_disp <- dispersion_params$median_dispersion

  # Derive mean_count (lambda0) from pilot data — same derivation as
  # samplesize_from_pilot() in sample_size_de.R
  if (is.null(mean_count)) {
    counts <- DESeq2::counts(pilot_dds, normalized = TRUE)
    mean_count <- mean(rowMeans(counts))
  }
  assert_count_scale(mean_count, "mean_count")

  # CV from dispersion only — the Poisson term 1/lambda0 is added inside
  # rnapower(), so we must NOT add it here (previous code added it twice)
  cv <- sqrt(median_disp)

  # Calculate required sample size
  required_n <- RNASeqPower::rnapower(
    depth = mean_count,
    cv = cv,
    effect = fold_change,
    alpha = alpha,
    power = target_power
  )

  required_n <- ceiling(required_n)

  result <- list(
    required_n_per_group = required_n,
    total_samples = required_n * 2,
    parameters = list(
      fold_change = fold_change,
      target_power = target_power,
      alpha = alpha,
      mean_count = mean_count,
      estimated_cv = cv,
      median_dispersion = median_disp
    ),
    pilot_data_info = list(
      n_samples = ncol(pilot_dds),
      n_genes = nrow(pilot_dds)
    ),
    recommendation = recommend_from_pilot(required_n, cv, fold_change)
  )

  return(result)
}


#' Use PROPER package for simulation-based power analysis (advanced)
#'
#' NOT IMPLEMENTED. This function previously printed "Running PROPER
#' simulation-based power analysis..." then "PROPER simulation complete" and
#' returned only a message string — no simulation was run. An agent could call
#' it, see a success message, and present simulation-based power as a completed
#' analysis. A stub that announces completion is worse than an absent function.
#'
#' @param pilot_dds DESeq2 object from pilot data
#' @param n_per_group_range Vector of sample sizes to test (e.g., 3:10)
#' @param fold_change_range Vector of fold-changes to test (e.g., c(1.5, 2, 3))
#' @param nsims Number of simulations (default: 10, use 100+ for publication)
#' @return Stops with an error — never returns
#' @export
calc_power_proper <- function(pilot_dds,
                              n_per_group_range = 3:8,
                              fold_change_range = c(1.5, 2, 3),
                              nsims = 10) {

  stop("calc_power_proper() is not implemented. This skill does not support simulation-based power analysis. Use calc_power_rnaseq() / calc_samplesize_de() for analytical power, or install and use the PROPER package directly following its vignette.")
}


#' Interpret dispersion value
#' @keywords internal
interpret_dispersion <- function(disp) {
  if (disp < 0.1) {
    return("Low dispersion: Little biological variability (typical of cell lines)")
  } else if (disp < 0.3) {
    return("Moderate dispersion: Typical of inbred model organisms")
  } else if (disp < 0.5) {
    return("High dispersion: Typical of outbred organisms or human samples")
  } else {
    return("Very high dispersion: Consider if batch effects or outliers are present")
  }
}


#' Interpret power from pilot analysis
#' @keywords internal
interpret_power_pilot <- function(power, cv) {
  interpretation <- paste0(
    "Power = ", round(power, 3), " based on pilot data (CV = ", round(cv, 3), ")\n"
  )

  if (power >= 0.8) {
    interpretation <- paste0(interpretation, "Good: Design has adequate power based on pilot variability")
  } else {
    interpretation <- paste0(interpretation, "Low: Pilot data shows higher variability than expected. Consider increasing sample size.")
  }

  return(interpretation)
}


#' Recommend design based on pilot analysis
#' @keywords internal
recommend_from_pilot <- function(n, cv, fc) {
  recommendation <- paste0(
    "Based on pilot data:\n",
    "  Required: ", n, " replicates per group (", n * 2, " total)\n",
    "  Estimated CV: ", round(cv, 3), "\n",
    "  Target fold-change: ", fc, "\n\n"
  )

  if (cv > 0.4) {
    recommendation <- paste0(
      recommendation,
      "Note: Pilot data shows high variability (CV > 0.4).\n",
      "Consider: (1) identifying sources of variation to reduce, or (2) increasing sample size further.\n"
    )
  }

  if (n > 15) {
    recommendation <- paste0(
      recommendation,
      "Large sample size required. Consider: (1) detecting larger effects, or (2) reducing technical variation.\n"
    )
  }

  return(recommendation)
}
