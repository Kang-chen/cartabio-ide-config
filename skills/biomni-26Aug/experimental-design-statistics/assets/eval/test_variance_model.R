# test_variance_model.R
# Unit tests for scripts/variance_model.R and the CV/dispersion identity.
#
# These are pure-arithmetic tests where possible (no Bioconductor download).
# Tests that require RNASeqPower or RnaSeqSampleSize are guarded and skip
# with a SKIPPED message if the package is unavailable — but an all-skipped
# run is treated as FAILURE, never as a pass.
#
# Run:  Rscript assets/eval/test_variance_model.R
# Exit: 0 = all passed, 1 = any failure, 2 = all skipped (also failure)

# --- Test harness -----------------------------------------------------------

n_pass <- 0
n_fail <- 0
n_skip <- 0
failures <- character()

pass <- function(msg) {
  n_pass <<- n_pass + 1
  message("  PASS: ", msg)
}

fail <- function(msg) {
  n_fail <<- n_fail + 1
  failures <<- c(failures, msg)
  message("  FAIL: ", msg)
}

skip_test <- function(msg) {
  n_skip <<- n_skip + 1
  message("  SKIPPED: ", msg)
}

assert_equal <- function(actual, expected, label, tol = 1e-6) {
  if (is.na(actual) && is.na(expected)) {
    pass(label)
  } else if (abs(actual - expected) < tol) {
    pass(label)
  } else {
    fail(sprintf("%s — expected %g, got %g", label, expected, actual))
  }
}

assert_true <- function(cond, label) {
  if (isTRUE(cond)) {
    pass(label)
  } else {
    fail(label)
  }
}

assert_stops <- function(expr, label) {
  result <- tryCatch({
    eval(expr)
    FALSE
  }, error = function(e) TRUE)
  assert_true(result, label)
}

# --- Setup ------------------------------------------------------------------

message("\n=== test_variance_model.R ===\n")

# Source variance_model.R (pure R, no dependencies)
script_dir <- file.path(getwd(), "scripts")
if (!file.exists(file.path(script_dir, "variance_model.R"))) {
  # Try relative to package root
  script_dir <- "scripts"
}
source(file.path(script_dir, "variance_model.R"))

# Check for optional packages
has_rnapower <- requireNamespace("RNASeqPower", quietly = TRUE)
has_rnaseqsamplesize <- requireNamespace("RnaSeqSampleSize", quietly = TRUE)

# --- Test group 1: cv_to_dispersion arithmetic -----------------------------

message("\n--- Group 1: cv_to_dispersion arithmetic ---")

# cv_to_dispersion(0.4, mean_count = 20) == 0.11
# 0.4^2 - 1/20 = 0.16 - 0.05 = 0.11
assert_equal(cv_to_dispersion(0.4, mean_count = 20), 0.11,
             "cv_to_dispersion(0.4, 20) == 0.11")

# cv_to_dispersion(0.4, mean_count = 300) == 0.16 - 1/300
expected_300 <- 0.4^2 - 1/300
assert_equal(cv_to_dispersion(0.4, mean_count = 300), expected_300,
             "cv_to_dispersion(0.4, 300) == 0.16 - 1/300")

# dispersion_to_cv round-trip
disp <- cv_to_dispersion(0.4, mean_count = 20)
cv_back <- dispersion_to_cv(disp, mean_count = 20)
assert_equal(cv_back, 0.4, "dispersion_to_cv round-trip == 0.4", tol = 1e-10)

# --- Test group 2: assert_count_scale guard --------------------------------

message("\n--- Group 2: assert_count_scale guard ---")

# assert_count_scale(20e6) must stop()
assert_stops(quote(assert_count_scale(20e6)),
             "assert_count_scale(20e6) stops (library size in reads)")

# assert_count_scale(20) must pass
result <- tryCatch({
  assert_count_scale(20)
  TRUE
}, error = function(e) FALSE)
assert_true(result, "assert_count_scale(20) passes")

# assert_count_scale(0.5) must stop (below 1)
assert_stops(quote(assert_count_scale(0.5)),
             "assert_count_scale(0.5) stops (below 1)")

# --- Test group 3: THE REGRESSION TEST FOR THE UNITS DEFECT ----------------
#
# At FIXED dispersion (not fixed CV), required n must be strictly smaller
# at lambda0 = 300 than at lambda0 = 5. A units error that collapses 1/lambda0
# to ~0 flattens this response and the test fails.
#
# Note: we hold dispersion FIXED, not CV. At fixed CV the response is near-flat
# because dispersion = CV^2 - 1/lambda0 rises as lambda0 rises and cancels the
# gain — which is exactly why a prior remediation run measured n as 31/32/33/33
# and concluded robustness.

message("\n--- Group 3: regression test for units defect (fixed dispersion) ---")

fixed_disp <- 0.11  # = cv_to_dispersion(0.4, 20)

if (has_rnapower) {
  library(RNASeqPower)
  n_low <- rnapower(depth = 5, cv = sqrt(fixed_disp + 1/5),
                    effect = 1.5, alpha = 0.05, power = 0.80)
  n_high <- rnapower(depth = 300, cv = sqrt(fixed_disp + 1/300),
                     effect = 1.5, alpha = 0.05, power = 0.80)
  message(sprintf("  rnapower: n(lambda0=5)=%.2f, n(lambda0=300)=%.2f", n_low, n_high))
  assert_true(n_high < n_low,
              "rnapower: n at lambda0=300 < n at lambda0=5 (fixed dispersion)")
} else {
  skip_test("RNASeqPower not installed — cannot run rnapower regression test")
}

if (has_rnaseqsamplesize) {
  library(RnaSeqSampleSize)
  # Use sample_size at fixed dispersion, varying lambda0
  n_low_fdr <- tryCatch(
    sample_size(power = 0.80, m = 10000, m1 = 500, f = 0.05,
                rho = 1.5, lambda0 = 5, phi0 = fixed_disp),
    error = function(e) NA)
  n_high_fdr <- tryCatch(
    sample_size(power = 0.80, m = 10000, m1 = 500, f = 0.05,
                rho = 1.5, lambda0 = 300, phi0 = fixed_disp),
    error = function(e) NA)
  if (!is.na(n_low_fdr) && !is.na(n_high_fdr)) {
    message(sprintf("  sample_size: n(lambda0=5)=%.2f, n(lambda0=300)=%.2f",
                    n_low_fdr, n_high_fdr))
    assert_true(n_high_fdr < n_low_fdr,
                "sample_size: n at lambda0=300 < n at lambda0=5 (fixed dispersion)")
  } else {
    skip_test("RnaSeqSampleSize::sample_size returned NA — cannot run FDR regression test")
  }
} else {
  skip_test("RnaSeqSampleSize not installed — cannot run FDR regression test")
}

# --- Test group 4: Analytic anchor pinning the parameterization ------------
#
# calc_power_rnaseq(mean_count = 20, n_per_group = 3, cv = 0.4,
#                   fold_change = 1.5)$power == 0.1904 to 4 dp

message("\n--- Group 4: analytic anchor ---")

if (has_rnapower) {
  source(file.path(script_dir, "power_rnaseq.R"))
  res <- calc_power_rnaseq(mean_count = 20, n_per_group = 3, cv = 0.4,
                           fold_change = 1.5)
  power_val <- round(res$power, 4)
  assert_equal(power_val, 0.1904,
               "calc_power_rnaseq(20, 3, 0.4, 1.5)$power == 0.1904", tol = 5e-5)
} else {
  skip_test("RNASeqPower not installed — cannot run analytic anchor test")
}

# --- Summary ----------------------------------------------------------------

message("\n=== Summary ===")
message(sprintf("  Passed: %d", n_pass))
message(sprintf("  Failed: %d", n_fail))
message(sprintf("  Skipped: %d", n_skip))

if (n_fail > 0) {
  message("\nFailures:")
  for (f in failures) message("  - ", f)
  quit(status = 1)
}

# All-skipped run is failure, never a pass
if (n_pass == 0 && n_skip > 0) {
  message("\nAll tests were skipped — treating as FAILURE (no evidence gathered).")
  quit(status = 2)
}

message("\nAll tests passed.")
quit(status = 0)
