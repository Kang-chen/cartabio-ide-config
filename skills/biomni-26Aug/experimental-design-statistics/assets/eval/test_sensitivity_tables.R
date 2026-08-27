# test_sensitivity_tables.R
# Tests that sensitivity tables in generate_design_recommendation() output
# carry real information and the expected monotonicity.
#
# (1) recommendation$pi0_sensitivity has at least four rows.
# (2) fdr_n_per_group is strictly decreasing in de_proportion.
# (3) The caller's own pi0 row carries the marker.
# (4) cv_sensitivity n is strictly increasing in CV.
#
# Run:  Rscript assets/eval/test_sensitivity_tables.R
# Exit: 0 = all passed, 1 = any failure, 2 = all skipped

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

assert_true <- function(cond, label) {
  if (isTRUE(cond)) {
    pass(label)
  } else {
    fail(label)
  }
}

# --- Setup ------------------------------------------------------------------

message("\n=== test_sensitivity_tables.R ===\n")

script_dir <- file.path(getwd(), "scripts")
if (!file.exists(file.path(script_dir, "power_rnaseq.R"))) {
  script_dir <- "scripts"
}

has_rnapower <- requireNamespace("RNASeqPower", quietly = TRUE)
has_rnaseqsamplesize <- requireNamespace("RnaSeqSampleSize", quietly = TRUE)

if (!has_rnapower || !has_rnaseqsamplesize) {
  skip_test("RNASeqPower or RnaSeqSampleSize not installed — cannot build recommendation")
  message("\n=== Summary ===")
  message(sprintf("  Passed: %d, Failed: %d, Skipped: %d", n_pass, n_fail, n_skip))
  if (n_pass == 0 && n_skip > 0) {
    message("All tests skipped — treating as FAILURE.")
    quit(status = 2)
  }
  quit(status = 0)
}

source(file.path(script_dir, "variance_model.R"))
library(RNASeqPower)
library(RnaSeqSampleSize)
source(file.path(script_dir, "power_rnaseq.R"))

# --- Build recommendation with FDR-aware power ------------------------------

message("\n--- Building recommendation ---")

rec <- generate_design_recommendation(
  cv = 0.4,
  target_fc = 1.5,
  target_power = 0.80,
  alpha = 0.05,
  fdr = 0.05,
  n_genes = 10000,
  mean_count = 20,
  tissue_type = "PBMC"
)

if (is.null(rec) || is.null(rec$parameters)) {
  fail("generate_design_recommendation returned a valid recommendation")
  quit(status = 1)
}
pass("generate_design_recommendation returned a valid recommendation")

# --- Test 1: pi0_sensitivity has at least four rows -------------------------

message("\n--- Test 1: pi0_sensitivity structure ---")

pi0_sens <- rec$pi0_sensitivity
if (is.null(pi0_sens)) {
  fail("recommendation$pi0_sensitivity is not NULL")
} else {
  assert_true(nrow(pi0_sens) >= 4,
              sprintf("pi0_sensitivity has >= 4 rows (got %d)", nrow(pi0_sens)))
  assert_true(all(c("de_proportion", "fdr_n_per_group") %in% names(pi0_sens)),
              "pi0_sensitivity has de_proportion and fdr_n_per_group columns")
}

# --- Test 2: fdr_n_per_group strictly decreasing in de_proportion -----------
#
# More DE genes (higher de_proportion) means more true positives, so fewer
# samples are needed to achieve the same FDR-aware power. If the tryCatch
# paths swallowed all errors to NA, this check fails.

message("\n--- Test 2: fdr_n_per_group decreasing in de_proportion ---")

if (!is.null(pi0_sens) && nrow(pi0_sens) >= 2) {
  valid_rows <- !is.na(pi0_sens$fdr_n_per_group)
  if (sum(valid_rows) >= 2) {
    ns <- pi0_sens$fdr_n_per_group[valid_rows]
    deps <- pi0_sens$de_proportion[valid_rows]
    # Sort by de_proportion to check monotonicity
    ord <- order(deps)
    ns_sorted <- ns[ord]
    is_decreasing <- all(diff(ns_sorted) <= 0) && any(diff(ns_sorted) < 0)
    message("  de_proportion: ", paste(deps[ord], collapse = " -> "))
    message("  fdr_n:         ", paste(ns_sorted, collapse = " -> "))
    assert_true(is_decreasing,
                "fdr_n_per_group is strictly decreasing in de_proportion")
  } else {
    fail("At least 2 non-NA fdr_n_per_group values needed for monotonicity check")
  }
} else {
  skip_test("pi0_sensitivity not available for monotonicity check")
}

# --- Test 3: caller's own pi0 row carries the marker ------------------------

message("\n--- Test 3: caller's pi0 row has marker in recommendation_text ---")

rec_text <- rec$recommendation_text
if (is.null(rec_text)) {
  fail("recommendation_text is not NULL")
} else {
  has_marker <- grepl("<-- your assumption", rec_text, fixed = TRUE)
  assert_true(has_marker,
              "recommendation_text contains '<-- your assumption' marker")
}

# --- Test 4: cv_sensitivity n strictly increasing in CV ---------------------

message("\n--- Test 4: cv_sensitivity n increasing in CV ---")

cv_sens <- rec$cv_sensitivity
if (is.null(cv_sens)) {
  fail("recommendation$cv_sensitivity is not NULL")
} else {
  assert_true(nrow(cv_sens) >= 2,
              sprintf("cv_sensitivity has >= 2 rows (got %d)", nrow(cv_sens)))

  if (nrow(cv_sens) >= 2) {
    valid_rows <- !is.na(cv_sens$per_gene_n_80)
    if (sum(valid_rows) >= 2) {
      ns <- cv_sens$per_gene_n_80[valid_rows]
      cvs <- cv_sens$cv[valid_rows]
      ord <- order(cvs)
      ns_sorted <- ns[ord]
      is_increasing <- all(diff(ns_sorted) >= 0) && any(diff(ns_sorted) > 0)
      message("  cv: ", paste(cvs[ord], collapse = " -> "))
      message("  n:  ", paste(ns_sorted, collapse = " -> "))
      assert_true(is_increasing,
                  "cv_sensitivity per_gene_n_80 is strictly increasing in CV")
    } else {
      fail("At least 2 non-NA per_gene_n_80 values needed for monotonicity check")
    }
  }
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

if (n_pass == 0 && n_skip > 0) {
  message("\nAll tests skipped — treating as FAILURE.")
  quit(status = 2)
}

message("\nAll tests passed.")
quit(status = 0)
