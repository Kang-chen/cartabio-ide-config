# test_export_consistency.R
# Tests for the design consistency gate in scripts/export_design.R.
#
# (1) Mutating design_params$n_per_group must cause export_complete_design()
#     to stop() and write no files.
# (2) A batch_design with the wrong row count must trip gate check 3.
# (3) Happy path: the produced file set must match the SKILL.md Outputs list,
#     including the three sensitivity CSVs.
#
# Run:  Rscript assets/eval/test_export_consistency.R
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

message("\n=== test_export_consistency.R ===\n")

script_dir <- file.path(getwd(), "scripts")
if (!file.exists(file.path(script_dir, "export_design.R"))) {
  script_dir <- "scripts"
}

# Check dependencies
has_rnapower <- requireNamespace("RNASeqPower", quietly = TRUE)
has_rnaseqsamplesize <- requireNamespace("RnaSeqSampleSize", quietly = TRUE)
has_jsonlite <- requireNamespace("jsonlite", quietly = TRUE)

if (!has_rnapower || !has_rnaseqsamplesize) {
  skip_test("RNASeqPower or RnaSeqSampleSize not installed — cannot build recommendation fixture")
  message("\n=== Summary ===")
  message(sprintf("  Passed: %d, Failed: %d, Skipped: %d", n_pass, n_fail, n_skip))
  if (n_pass == 0 && n_skip > 0) {
    message("All tests skipped — treating as FAILURE.")
    quit(status = 2)
  }
  quit(status = 0)
}

# Source required scripts
source(file.path(script_dir, "variance_model.R"))
library(RNASeqPower)
library(RnaSeqSampleSize)
source(file.path(script_dir, "power_rnaseq.R"))
source(file.path(script_dir, "export_design.R"))
source(file.path(script_dir, "batch_assignment.R"))

# --- Build a recommendation fixture ----------------------------------------

message("\n--- Building recommendation fixture ---")

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

# Build a batch_design matching the recommendation
# Derive rec_n the same way build_design_params does
tp <- rec$parameters$target_power
if (tp >= 0.85) {
  rec_n <- rec$fdr_required_n$power_90
  if (is.na(rec_n)) rec_n <- rec$per_gene_required_n$power_90
} else {
  rec_n <- rec$fdr_required_n$power_80
  if (is.na(rec_n)) rec_n <- rec$per_gene_required_n$power_80
}
if (is.null(rec_n) || is.na(rec_n)) {
  fail("Could not determine rec_n from recommendation fixture")
  quit(status = 1)
}
message(sprintf("  Recommended n per group: %d", rec_n))

# Build planned metadata with the correct n
source(file.path(script_dir, "load_example_data.R"))
planned <- make_planned_metadata(n_per_group = rec_n,
                                 conditions = c("untreated", "treated"))
# Use batch_size that yields ~3 batches (ceil(total/3) per batch)
batch_size <- ceiling(nrow(planned) / 3)
batch_design <- assign_samples_to_batches(planned,
                                          batch_size = batch_size,
                                          balance_vars = "condition")

# Build design_params
design_params <- build_design_params(rec, batch_design)

# --- Test 1: mutated n_per_group must stop and write no files ---------------

message("\n--- Test 1: mutated n_per_group stops and writes no files ---")

test_dir <- tempfile(pattern = "export_test_1_")
dir.create(test_dir, recursive = TRUE)

bad_params <- design_params
bad_params$n_per_group <- 5  # deliberately wrong

stopped <- tryCatch({
  export_complete_design(batch_design, bad_params, output_dir = test_dir,
                         recommendation = rec)
  FALSE
}, error = function(e) {
  message("  (correctly stopped: ", conditionMessage(e), ")")
  TRUE
})

assert_true(stopped, "export_complete_design stops on mutated n_per_group")

# Verify no files were written
files_written <- list.files(test_dir, recursive = TRUE)
assert_true(length(files_written) == 0,
            "No files written when validation fails")
if (length(files_written) > 0) {
  message("  Files found: ", paste(files_written, collapse = ", "))
}
unlink(test_dir, recursive = TRUE)

# --- Test 2: wrong batch_design row count trips check 3 ---------------------

message("\n--- Test 2: wrong batch_design row count trips check 3 ---")

test_dir2 <- tempfile(pattern = "export_test_2_")
dir.create(test_dir2, recursive = TRUE)

# Create a batch_design with wrong number of rows (too few)
bad_batch <- batch_design[1:5, ]

stopped2 <- tryCatch({
  export_complete_design(bad_batch, design_params, output_dir = test_dir2,
                         recommendation = rec)
  FALSE
}, error = function(e) {
  message("  (correctly stopped: ", conditionMessage(e), ")")
  TRUE
})

assert_true(stopped2, "export_complete_design stops on wrong batch row count")
unlink(test_dir2, recursive = TRUE)

# --- Test 3: happy path — file set matches SKILL.md Outputs -----------------

message("\n--- Test 3: happy path — file set matches Outputs ---")

test_dir3 <- tempfile(pattern = "export_test_3_")
dir.create(test_dir3, recursive = TRUE)

result <- tryCatch({
  export_complete_design(batch_design, design_params, output_dir = test_dir3,
                         recommendation = rec)
}, error = function(e) {
  message("  ERROR: ", conditionMessage(e))
  NULL
})

if (!is.null(result)) {
  files_produced <- sort(list.files(test_dir3))

  # Expected file set per SKILL.md Outputs
  expected_files <- sort(c(
    "batch_layout_for_lab.csv",
    "statistical_analysis_plan.md",
    "lab_protocol_checklist.md",
    "batch_design.rds",
    "design_parameters.rds",
    "design_parameters.json",
    "power_analysis_results.csv",
    "sample_size_recommendation.txt",
    "cv_sensitivity.csv",
    "de_proportion_sensitivity.csv",
    "mean_count_sensitivity.csv",
    "batch_design_validation.txt"
  ))

  message("  Files produced: ", paste(files_produced, collapse = ", "))
  message("  Expected:       ", paste(expected_files, collapse = ", "))

  assert_true(identical(files_produced, expected_files),
              "Produced file set exactly matches SKILL.md Outputs list")

  # Verify sensitivity CSVs have content
  for (csv_name in c("cv_sensitivity.csv", "de_proportion_sensitivity.csv",
                     "mean_count_sensitivity.csv")) {
    csv_path <- file.path(test_dir3, csv_name)
    if (file.exists(csv_path)) {
      content <- read.csv(csv_path)
      assert_true(nrow(content) > 0,
                  sprintf("%s has at least one data row", csv_name))
    } else {
      fail(sprintf("%s was not written", csv_name))
    }
  }
} else {
  fail("Happy path export completed without error")
}

unlink(test_dir3, recursive = TRUE)

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
