# =============================================================================
# load_mimic_demo.R  --  fetch the open PhysioNet MIMIC-IV Clinical Database
#                        Demo and map it onto the skill's table contract.
# -----------------------------------------------------------------------------
# The demo (~100 ICU patients) is OPEN ACCESS (no credentialing) under the
# PhysioNet Open Data Commons license. For the FULL MIMIC-IV you must complete
# PhysioNet credentialing + the CITI course; this loader does NOT automate that.
#
# Usage:  Rscript load_mimic_demo.R [dest_dir]
#   dest_dir defaults to /workspace/mimic
# Downloads only the tables the pipeline needs and writes them as CSVs whose
# paths you then reference from your config (see examples/mimic_sepsis_config.R).
# =============================================================================

download_mimic_demo <- function(dest_dir = "/workspace/mimic") {
  base <- "https://physionet.org/files/mimic-iv-demo/2.2"
  files <- list(
    "hosp/patients.csv.gz"          = file.path(dest_dir, "hosp"),
    "hosp/admissions.csv.gz"        = file.path(dest_dir, "hosp"),
    "hosp/diagnoses_icd.csv.gz"     = file.path(dest_dir, "hosp"),
    "hosp/d_icd_diagnoses.csv.gz"   = file.path(dest_dir, "hosp"),
    "hosp/prescriptions.csv.gz"     = file.path(dest_dir, "hosp"),
    "icu/icustays.csv.gz"           = file.path(dest_dir, "icu"),
    "icu/inputevents.csv.gz"        = file.path(dest_dir, "icu"),
    "icu/d_items.csv.gz"            = file.path(dest_dir, "icu")
  )
  for (rel in names(files)) {
    outdir <- files[[rel]]
    dir.create(outdir, showWarnings = FALSE, recursive = TRUE)
    dest <- file.path(outdir, basename(rel))
    if (!file.exists(dest)) {
      url <- paste0(base, "/", rel)
      cat("Downloading", rel, "...\n")
      utils::download.file(url, dest, quiet = TRUE, mode = "wb")
    }
  }
  cat("MIMIC-IV demo downloaded to", dest_dir, "\n")
  invisible(dest_dir)
}

# Build a `drugs` table for VASOPRESSORS from icu/inputevents (continuous meds
# are recorded there, not in prescriptions). Maps itemid -> drug name via d_items.
# Returns a data.table with subject_id, hadm_id, drug, route, starttime.
build_vasopressor_drugs <- function(dest_dir = "/workspace/mimic") {
  suppressMessages(library(data.table))
  ie  <- fread(file.path(dest_dir, "icu", "inputevents.csv.gz"))
  di  <- fread(file.path(dest_dir, "icu", "d_items.csv.gz"))
  ie  <- merge(ie, di[, .(itemid, label)], by = "itemid", all.x = TRUE)
  ie[, drug := tolower(label)]
  out <- ie[, .(subject_id, hadm_id, drug, route = "IV DRIP",
                starttime = as.character(starttime))]
  out
}

if (sys.nframe() == 0) {
  args <- commandArgs(trailingOnly = TRUE)
  dest <- if (length(args) >= 1) args[1] else "/workspace/mimic"
  download_mimic_demo(dest)
}
