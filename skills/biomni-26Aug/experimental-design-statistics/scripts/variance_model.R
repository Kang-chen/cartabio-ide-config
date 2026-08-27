# Variance Model — single source of truth for the CV/dispersion identity
#
# The negative-binomial RNA-seq variance decomposition is:
#
#   CV_total^2 = 1 / lambda0 + BCV^2
#
# where lambda0 is the per-gene expected count (NOT library size in reads or
# millions of reads) and BCV is the biological coefficient of variation.
# Dispersion (phi) = BCV^2 = CV_total^2 - 1/lambda0.
#
# This file is the ONLY place this identity is written. Every script that needs
# dispersion from CV (or vice versa) guard-sources this file and calls the
# functions below. Hardcoding the identity elsewhere is what allowed the units
# to diverge across three call sites in a previous version.

#' Assert that a value is a per-gene expected count (lambda0), not a library size
#'
#' Library sizes in reads (e.g. 20e6) or millions of reads (e.g. 20 when the
#' caller means "20M") are routinely passed into the lambda0 slot by mistake.
#' This guard stops that before it produces a silently wrong dispersion.
#'
#' @param x Numeric value to check
#' @param arg_name Name of the argument being checked (for the error message)
#' @return Invisible TRUE if the check passes
#' @export
assert_count_scale <- function(x, arg_name = "mean_count") {
  if (!is.numeric(x) || length(x) != 1 || is.na(x)) {
    stop(sprintf("%s must be a single finite numeric value, got: %s",
                 arg_name, deparse(x)))
  }
  if (x > 1000) {
    stop(sprintf("%s = %g looks like a library size in reads or millions of reads, not a per-gene expected count (lambda0). lambda0 is the expected read count for a single gene, typically 5-300. Pass library depth separately via library_depth_M for reporting only.",
                 arg_name, x))
  }
  if (x < 1) {
    stop(sprintf("%s = %g is below 1. lambda0 (per-gene expected count) must be >= 1 for the variance decomposition CV^2 = 1/lambda0 + BCV^2 to be meaningful.",
                 arg_name, x))
  }
  invisible(TRUE)
}

#' Convert CV to dispersion (biological coefficient of variation squared)
#'
#' Dispersion (phi) = BCV^2 = CV_total^2 - 1/lambda0
#'
#' @param cv Coefficient of variation (biological, total)
#' @param mean_count Expected counts for the gene (lambda0). The Poisson term in
#'   CV^2 = 1/lambda0 + BCV^2. NOT library size in reads or millions of reads.
#' @return Dispersion parameter (phi = BCV^2)
#' @export
cv_to_dispersion <- function(cv, mean_count = 20) {
  assert_count_scale(mean_count, "mean_count")
  if (!is.numeric(cv) || length(cv) != 1 || is.na(cv) || cv <= 0) {
    stop("cv must be a single positive numeric value")
  }
  disp <- cv^2 - 1 / mean_count
  if (disp <= 0) {
    warning(sprintf(
      "CV=%.3f too low for mean_count=%g — dispersion would be negative (%.6f). Using CV^2=%.6f directly. This means the Poisson term 1/lambda0 dominates and BCV is near zero.",
      cv, mean_count, disp, cv^2))
    disp <- cv^2
  }
  return(disp)
}

#' Convert dispersion back to CV
#'
#' CV_total = sqrt(dispersion + 1/lambda0)
#'
#' @param dispersion Dispersion parameter (phi = BCV^2)
#' @param mean_count Expected counts for the gene (lambda0). The Poisson term in
#'   CV^2 = 1/lambda0 + BCV^2. NOT library size in reads or millions of reads.
#' @return Total coefficient of variation
#' @export
dispersion_to_cv <- function(dispersion, mean_count = 20) {
  assert_count_scale(mean_count, "mean_count")
  if (!is.numeric(dispersion) || length(dispersion) != 1 || is.na(dispersion) || dispersion < 0) {
    stop("dispersion must be a single non-negative numeric value")
  }
  sqrt(dispersion + 1 / mean_count)
}
