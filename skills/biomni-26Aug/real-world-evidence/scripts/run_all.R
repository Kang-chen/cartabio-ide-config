# =============================================================================
# run_all.R  --  Top-level driver for the RWE cohort pipeline
# -----------------------------------------------------------------------------
# Sources a study config, runs the full analysis in order, builds the
# data-faithful infographic, writes the report manifest, and (optionally) the
# PDF. Every step reads parameters from CFG -- nothing disease-specific here.
#
# USAGE:
#   Rscript run_all.R  path/to/my_study_config.R
#
# The config MUST define `CFG` (copy scripts/00_config_template.R and edit).
#
# LITERATURE (agent step): before/after running this, the AGENT should call
# Biomni LiteratureSearch with CFG$literature_queries and write the formatted
# citations to <out_dir>/tables/references.txt (one per line). 06_manifest.R
# picks them up automatically so the PDF gets a real References section.
#
# PDF: this driver shells out to build_report.py at the end. If Python/ReportLab
# is unavailable in your environment, run that step separately:
#   python scripts/build_report.py <out_dir>
# =============================================================================
suppressMessages({ library(data.table) })

# --- resolve this script's directory so sub-scripts can be sourced reliably ---
.fa <- grep("^--file=", commandArgs(FALSE), value = TRUE)
SCRIPTS_DIR <- if (length(.fa)) dirname(sub("^--file=", "", .fa)) else "."

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1)
  stop("Usage: Rscript run_all.R <config.R>  (copy 00_config_template.R)")
config_path <- args[[1]]
if (!file.exists(config_path)) stop("Config not found: ", config_path)

message("== RWE pipeline ==")
message("Config : ", config_path)
source(config_path)                 # defines CFG
stopifnot(is.list(CFG))
message("Study  : ", CFG$study_title)
message("Out dir: ", CFG$paths$out_dir)

dir.create(file.path(CFG$paths$out_dir, "tables"),  showWarnings = FALSE, recursive = TRUE)
dir.create(file.path(CFG$paths$out_dir, "figures"), showWarnings = FALSE, recursive = TRUE)

# --- source pipeline steps (each defines a run_* function) --------------------
step_files <- c("_utils.R", "01_build_cohort.R", "02_table1.R",
                "03_treatment_patterns.R", "04_survival.R", "05_comparison.R",
                "make_infographic.R", "06_manifest.R")
for (f in step_files) source(file.path(SCRIPTS_DIR, f))

run_step <- function(label, fn) {
  message("\n--- ", label, " ---")
  t <- system.time(fn(CFG))
  message(sprintf("    done (%.1fs)", t[["elapsed"]]))
}

run_step("1/7 Build cohort",       run_build_cohort)
# Treatment patterns MUST run before Table 1: 02_table1.R loads
# /workspace/rwe/tx_patient_flags.RData (written by 03_treatment_patterns.R)
# to add the "on_treatment" column. Running Table 1 first silently skips the
# merge (file.exists() guard) and drops the treatment-use row from Table 1.
run_step("2/7 Treatment patterns", run_treatment)
run_step("3/7 Table 1",            run_table1)
run_step("4/7 Survival",           run_survival)
run_step("5/7 Comparison",         run_comparison)
run_step("6/7 Infographic",        run_infographic)
run_step("7/7 Report manifest",    run_manifest)

# --- PDF (Python/ReportLab) ---------------------------------------------------
message("\n--- Building PDF (build_report.py) ---")
py <- Sys.which("python3"); if (!nzchar(py)) py <- Sys.which("python")
if (nzchar(py)) {
  cmd <- sprintf("%s %s %s", shQuote(py),
                 shQuote(file.path(SCRIPTS_DIR, "build_report.py")),
                 shQuote(CFG$paths$out_dir))
  rc <- system(cmd)
  if (rc != 0) message("    build_report.py exited non-zero (rc=", rc,
                       "); run it manually: python build_report.py <out_dir>")
} else {
  message("    python not found; run: python scripts/build_report.py ",
          CFG$paths$out_dir)
}

message("\n== pipeline complete ==  see ", CFG$paths$out_dir)
