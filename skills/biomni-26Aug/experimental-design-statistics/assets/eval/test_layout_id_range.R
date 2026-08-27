# test_layout_id_range.R
# Regression test for the sample-ID range fix in scripts/export_design.R.
#
# The exported batch layout's TEMPLATE line previously stated the first/last row
# of the batch-SORTED table as an ID "range", which contradicted the file's own
# contents (e.g. "S05-S56" while the layout ran S01-S62). The stated range must
# be derived from the data actually written (numeric-aware span), and a
# pre-export check must fail before writing if it does not match.
#
# (1) With a batch order that reverses the ID order, the written CSV header still
#     states the true span S01-S12 (would read "S12-S01" on the pre-fix code).
# (2) .assert_id_range_matches() stops on a stated range that is not the true span.
# (3) Non-demo IDs (not S##) produce no TEMPLATE header and no crash.
#
# Run:  Rscript assets/eval/test_layout_id_range.R
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

message("\n=== test_layout_id_range.R ===\n")

script_dir <- file.path(getwd(), "scripts")
if (!file.exists(file.path(script_dir, "export_design.R"))) {
  script_dir <- "scripts"
}
source(file.path(script_dir, "export_design.R"))

# --- Test 1: batch-sorted reorder must not change the stated range -----------

message("\n--- Test 1: stated range is the true span regardless of row order ---")

n <- 12
sid <- sprintf("S%02d", 1:n)
# Put S01-S06 in batch 2 and S07-S12 in batch 1, reversed within each batch, so
# after the export sort (batch, processing_order) the first row is S12 and the
# last row is S01 -- the exact situation the pre-fix code mis-reported.
df <- data.frame(
  sample_id = sid,
  condition = rep(c("untreated", "treated"), length.out = n),
  batch = ifelse(seq_len(n) <= 6, 2L, 1L),
  processing_order = c(6:1, 6:1),
  stringsAsFactors = FALSE
)

tmp <- tempfile(fileext = ".csv")
invisible(export_batch_layout(df, tmp))
lines <- readLines(tmp)
hdr <- lines[1]
message("  header: ", hdr)
assert_true(grepl("(S01-S12)", hdr, fixed = TRUE),
            "CSV header states the true span (S01-S12)")
assert_true(!grepl("S12-S01", hdr, fixed = TRUE),
            "CSV header does NOT state the batch-sorted endpoints (S12-S01)")

# The layout body must still contain all 12 IDs.
assert_true(all(vapply(sid, function(s) any(grepl(s, lines, fixed = TRUE)), logical(1))),
            "all 12 sample IDs appear in the written layout")

# --- Test 2: the guard rejects a range that does not match the data ----------

message("\n--- Test 2: pre-export guard rejects a mismatched stated range ---")

ids62 <- sprintf("S%02d", 1:62)
stopped <- tryCatch({
  .assert_id_range_matches("S05", "S56", ids62); FALSE
}, error = function(e) { message("  (correctly stopped: ", conditionMessage(e), ")"); TRUE })
assert_true(stopped, ".assert_id_range_matches stops on S05-S56 for an S01-S62 layout")

# And it passes for the correct span.
ok <- tryCatch({ .assert_id_range_matches("S01", "S62", ids62); TRUE },
               error = function(e) FALSE)
assert_true(ok, ".assert_id_range_matches passes for the true span S01-S62")

# --- Test 3: non-demo IDs produce no TEMPLATE header ------------------------

message("\n--- Test 3: real (non-demo) IDs write no TEMPLATE header ---")

df2 <- data.frame(
  sample_id = paste0("Patient_", LETTERS[1:6]),
  condition = rep(c("untreated", "treated"), 3),
  batch = rep(1:2, 3),
  processing_order = 1:6,
  stringsAsFactors = FALSE
)
tmp2 <- tempfile(fileext = ".csv")
invisible(export_batch_layout(df2, tmp2))
hdr2 <- readLines(tmp2)[1]
assert_true(!grepl("TEMPLATE", hdr2, fixed = TRUE),
            "no TEMPLATE header for real sample IDs")

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
