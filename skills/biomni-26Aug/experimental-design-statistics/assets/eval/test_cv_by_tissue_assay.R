# test_cv_by_tissue_assay.R
# Regression test for the CV-by-tissue plot fix in scripts/plot_power_curves.R.
#
# The packaged plot previously aggregated CV across ALL assays, diluting the
# bulk-RNA-seq CV the design rests on and clipping high-CV assays (scRNA-seq up
# to ~1.5) against the fixed y-axis. The plot must now serve one assay directly
# (default: bulk RNA-seq), using the database's own per-row CV/CV_Min/CV_Max.
#
# (1) The default plot's data is bulk-RNA-seq-only (14 rows).
# (2) Human PBMC reads CV=0.40 with CV_Min/Max=0.30/0.50 (the design assumption).
# (3) No CV_Max exceeds the 0.6 axis limit (nothing clips).
# (4) Rows sharing a tissue name across organisms stay distinct (Human/Mouse Brain).
# (5) An unknown assay errors with the list of available assays.
#
# Run:  Rscript assets/eval/test_cv_by_tissue_assay.R
# Exit: 0 = all passed, 1 = any failure, 2 = all skipped

# --- Test harness -----------------------------------------------------------

n_pass <- 0
n_fail <- 0
n_skip <- 0
failures <- character()

pass <- function(msg) { n_pass <<- n_pass + 1; message("  PASS: ", msg) }
fail <- function(msg) { n_fail <<- n_fail + 1; failures <<- c(failures, msg); message("  FAIL: ", msg) }
skip_test <- function(msg) { n_skip <<- n_skip + 1; message("  SKIPPED: ", msg) }
assert_true <- function(cond, label) { if (isTRUE(cond)) pass(label) else fail(label) }

# --- Setup ------------------------------------------------------------------

message("\n=== test_cv_by_tissue_assay.R ===\n")

script_dir <- file.path(getwd(), "scripts")
if (!file.exists(file.path(script_dir, "plot_power_curves.R"))) {
  script_dir <- "scripts"
}

has_ggplot <- requireNamespace("ggplot2", quietly = TRUE)
has_ggprism <- requireNamespace("ggprism", quietly = TRUE)
db_path <- "references/cv_tissue_database.csv"

if (!has_ggplot || !has_ggprism) {
  skip_test("ggplot2 or ggprism not installed — cannot build the plot object")
  message("\n=== Summary ===")
  message(sprintf("  Passed: %d, Failed: %d, Skipped: %d", n_pass, n_fail, n_skip))
  quit(status = 2)
}
if (!file.exists(db_path)) {
  skip_test("references/cv_tissue_database.csv not found — run from the package root")
  message("\n=== Summary ===")
  message(sprintf("  Passed: %d, Failed: %d, Skipped: %d", n_pass, n_fail, n_skip))
  quit(status = 2)
}

source(file.path(script_dir, "plot_power_curves.R"))

# --- Build the default (bulk RNA-seq) plot and inspect its data --------------

message("\n--- Default plot: bulk RNA-seq only ---")

p <- plot_cv_by_tissue(cv_database_path = db_path,
                       output_file = tempfile(fileext = ".svg"))
pd <- p$data

bulk <- read.csv(db_path, stringsAsFactors = FALSE)
bulk <- bulk[bulk$Assay == "Bulk RNA-seq", ]
n_tissues <- length(unique(bulk$Tissue))
assert_true(nrow(pd) == n_tissues,
            sprintf("plot data has one row per bulk RNA-seq tissue (%d)", n_tissues))

pbmc <- pd[pd$Tissue == "PBMC", ]
assert_true(nrow(pbmc) == 1 &&
              isTRUE(all.equal(pbmc$CV_typical, 0.40)) &&
              isTRUE(all.equal(pbmc$CV_min, 0.30)) &&
              isTRUE(all.equal(pbmc$CV_max, 0.50)),
            "PBMC shows CV=0.40 (0.30-0.50), the design's CV assumption")

assert_true(all(pd$CV_max <= 0.6),
            "no CV_Max exceeds the 0.6 axis limit (nothing clips)")

# Brain is reported for Human (0.25-0.45) and Mouse (0.20-0.35) in bulk RNA-seq;
# they combine into a single Brain row spanning the observed range.
brain <- pd[pd$Tissue == "Brain", ]
assert_true(nrow(brain) == 1 && brain$CV_min <= 0.20 && brain$CV_max >= 0.45,
            "multi-organism tissue (Brain) combines into one row spanning the observed range")

# Every error bar has non-zero width because the range comes from CV_Min/CV_Max.
assert_true(all(pd$CV_max > pd$CV_min),
            "all tissues show a non-zero CV range (from CV_Min/CV_Max)")

# --- Unknown assay must error with the available list -----------------------

message("\n--- Unknown assay errors ---")

erred <- tryCatch({
  plot_cv_by_tissue(cv_database_path = db_path, assay = "Nonexistent",
                    output_file = tempfile(fileext = ".svg"))
  FALSE
}, error = function(e) {
  message("  (correctly stopped: ", conditionMessage(e), ")")
  grepl("Available assays", conditionMessage(e), fixed = TRUE)
})
assert_true(erred, "unknown assay stops and lists available assays")

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
