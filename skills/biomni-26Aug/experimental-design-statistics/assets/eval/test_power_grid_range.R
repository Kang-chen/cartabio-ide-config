# test_power_grid_range.R
# Regression tests for the derive-not-restate fix in scripts/power_rnaseq.R and
# the pre-export grid check (check 7) in scripts/export_design.R.
#
# The headline sample size is computed from sample_size()/rnapower(), NOT from
# the power table. Before the fix, the exported power table used a fixed grid
# c(3, 5, 8, 10, 15, 20), so a headline n > 20 fell outside the delivered
# evidence. These tests lock in that the grid now spans the recommendation and
# that a truncated grid can never reach export.
#
# (1) The recommendation's power-table grid contains the headline n as a row.
# (2) The grid extends strictly beyond the headline n (curve does not end on it).
# (3) The low-n pilot anchors {3,5,8,10,15,20} are retained.
# (4) per_gene_n90 (also > 20 for the reference case) is a row too.
# (5) FDR-aware power at the headline n is >= target power.
# (6) export_complete_design() STOPS (writes nothing) when the exported
#     power_table is truncated below the headline n.
#
# Run:  Rscript assets/eval/test_power_grid_range.R
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

message("\n=== test_power_grid_range.R ===\n")

script_dir <- file.path(getwd(), "scripts")
if (!file.exists(file.path(script_dir, "power_rnaseq.R"))) {
  script_dir <- "scripts"
}

has_rnapower <- requireNamespace("RNASeqPower", quietly = TRUE)
has_rnaseqsamplesize <- requireNamespace("RnaSeqSampleSize", quietly = TRUE)

if (!has_rnapower || !has_rnaseqsamplesize) {
  skip_test("RNASeqPower or RnaSeqSampleSize not installed — cannot build FDR-aware recommendation")
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
source(file.path(script_dir, "export_design.R"))
source(file.path(script_dir, "batch_assignment.R"))
source(file.path(script_dir, "load_example_data.R"))

# --- Build the recommendation for the case that originally failed -----------
# CV = 0.4, mean count 20, 20,000 genes, pi0 = 0.9 (unset), 1.5x FC, 90% power,
# FDR 0.05. Headline (FDR-aware n90) was 31; per-gene n90 was 27 — both > 20.

message("\n--- Building recommendation (reference failing case) ---")

rec <- generate_design_recommendation(
  cv = 0.40,
  target_fc = 1.5,
  target_power = 0.90,
  mean_count = 20,
  alpha = 0.05,
  fdr = 0.05,
  n_genes = 20000
)

if (is.null(rec) || is.null(rec$parameters) || is.null(rec$power_table)) {
  fail("generate_design_recommendation returned a valid recommendation with a power table")
  quit(status = 1)
}
pass("generate_design_recommendation returned a valid recommendation with a power table")

# Derive the headline n exactly as build_design_params() does.
tp <- rec$parameters$target_power
if (tp >= 0.85) {
  headline_n <- rec$fdr_required_n$power_90
  if (is.na(headline_n)) headline_n <- rec$per_gene_required_n$power_90
} else {
  headline_n <- rec$fdr_required_n$power_80
  if (is.na(headline_n)) headline_n <- rec$per_gene_required_n$power_80
}

grid_n <- sort(unique(rec$power_table$n))
message(sprintf("  headline n = %d", headline_n))
message(sprintf("  per_gene n90 = %d", rec$per_gene_required_n$power_90))
message(sprintf("  exported grid n = {%s}", paste(grid_n, collapse = ", ")))

# --- Test 1: headline n is a row in the grid --------------------------------

message("\n--- Test 1: headline n present in exported grid ---")
assert_true(headline_n %in% grid_n,
            sprintf("headline n (%d) is a row in the exported power-table grid", headline_n))

# --- Test 2: grid extends strictly beyond the headline n --------------------

message("\n--- Test 2: grid extends beyond the headline n ---")
assert_true(max(grid_n) > headline_n,
            sprintf("grid max (%d) is strictly greater than headline n (%d)",
                    max(grid_n), headline_n))

# --- Test 3: low-n pilot anchors retained -----------------------------------

message("\n--- Test 3: low-n pilot anchors retained ---")
low_n <- c(3, 5, 8, 10, 15, 20)
assert_true(all(low_n %in% grid_n),
            "low-n pilot anchors {3,5,8,10,15,20} are all still present")

# --- Test 4: per_gene_n90 (also > 20 here) is a row -------------------------

message("\n--- Test 4: per_gene_n90 present in grid ---")
pg90 <- rec$per_gene_required_n$power_90
assert_true(pg90 %in% grid_n,
            sprintf("per_gene_n90 (%d) is a row in the grid", pg90))

# --- Test 5: FDR-aware power at the headline n meets target -----------------
# The headline (fdr_n90) is the n where FDR-aware power reaches the target. Read
# the value the reviewer actually sees -- the exported power table's FDR-aware
# column at the FC used for the headline -- and confirm it reaches the target.
# This only holds if est_power() uses the same DE proportion (m1) as the
# sample_size() headline; a bare est_power(n, ...) call (default m1) would read
# ~0.69 here and this test would fail.

message("\n--- Test 5: exported FDR-aware power at headline n >= target ---")
fc_col <- sprintf("FC%s_fdr_aware", rec$parameters$target_fc)  # e.g. "FC1.5_fdr_aware"
pt <- rec$power_table
pwr_at_headline <- if (fc_col %in% names(pt)) pt[[fc_col]][pt$n == headline_n] else NA_real_
message(sprintf("  exported %s at n = %d: %.3f", fc_col, headline_n, pwr_at_headline))
assert_true(length(pwr_at_headline) == 1 && !is.na(pwr_at_headline) &&
              pwr_at_headline >= rec$parameters$target_power,
            sprintf("exported FDR-aware power at headline n (%d) is >= target (%.2f)",
                    headline_n, rec$parameters$target_power))

# --- Test 6: export gate stops on a truncated grid --------------------------
# Build a consistent design, then truncate its power_table below the headline n
# and confirm export_complete_design() halts and writes nothing (check 7).

message("\n--- Test 6: export gate stops when grid truncated below headline n ---")

planned <- make_planned_metadata(n_per_group = headline_n,
                                 conditions = c("untreated", "treated"))
batch_size <- ceiling(nrow(planned) / 3)
batch_design <- assign_samples_to_batches(planned, batch_size = batch_size,
                                          balance_vars = "condition")
design_params <- build_design_params(rec, batch_design)

bad_params <- design_params
bad_params$power_table <- bad_params$power_table[bad_params$power_table$n <= 20, ]

test_dir <- tempfile(pattern = "grid_range_test_")
dir.create(test_dir, recursive = TRUE)

stopped <- tryCatch({
  export_complete_design(batch_design, bad_params, output_dir = test_dir,
                         recommendation = rec)
  FALSE
}, error = function(e) {
  message("  (correctly stopped: ", conditionMessage(e), ")")
  TRUE
})

assert_true(stopped, "export_complete_design stops when power_table is truncated below headline n")

files_written <- list.files(test_dir, recursive = TRUE)
assert_true(length(files_written) == 0,
            "No files written when the grid check fails")
if (length(files_written) > 0) {
  message("  Files found: ", paste(files_written, collapse = ", "))
}
unlink(test_dir, recursive = TRUE)

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
