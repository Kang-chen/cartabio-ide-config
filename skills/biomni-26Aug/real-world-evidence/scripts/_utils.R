# =============================================================================
# _utils.R  --  shared helpers for the RWE cohort pipeline
# Sourced by 01-05. Depends on: data.table, dplyr. CFG must already be in scope.
# =============================================================================
suppressMessages({
  library(data.table)
  library(dplyr)
})

# null-coalesce
`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

# ---- IO: read CSV or parquet by extension -----------------------------------
read_any <- function(path) {
  if (is.na(path) || !nzchar(path)) return(NULL)
  if (!file.exists(path)) stop("Input file not found: ", path)
  if (grepl("\\.parquet$", path, ignore.case = TRUE)) {
    if (!requireNamespace("arrow", quietly = TRUE))
      stop("Parquet input needs the 'arrow' package.")
    as.data.table(arrow::read_parquet(path))
  } else {
    fread(path)
  }
}

# ---- Column remap: rename YOUR columns -> canonical names --------------------
# mapping is a named vector c(canonical = "your_col"). Missing source columns are
# skipped with a warning so optional fields degrade gracefully.
remap_cols <- function(dt, mapping) {
  if (is.null(dt)) return(NULL)
  dt <- as.data.table(dt)
  for (canon in names(mapping)) {
    src <- mapping[[canon]]
    if (!is.na(src) && src %in% names(dt)) {
      if (src != canon) setnames(dt, src, canon)
    }
  }
  dt
}

# ---- Datetime parsing (MIMIC-style 'YYYY-MM-DD HH:MM:SS', UTC) ---------------
as_dt <- function(x) as.POSIXct(as.character(x),
                                format = "%Y-%m-%d %H:%M:%S", tz = "UTC")
as_d  <- function(x) as.Date(as.character(x))

# ---- Code matcher: does a code match a {prefix, exact} rule? -----------------
code_matches <- function(code, rule) {
  if (is.null(rule)) return(rep(FALSE, length(code)))
  hit <- rep(FALSE, length(code))
  if (length(rule$prefix)) for (p in rule$prefix) hit <- hit | startsWith(code, p)
  if (length(rule$exact))  hit <- hit | code %in% rule$exact
  hit
}

# ---- Cohort flag over a diagnoses table -------------------------------------
# Returns a data.table of subject_id, hadm_id flagged as qualifying.
flag_cohort <- function(dx, cohort_codes) {
  dx <- copy(dx)
  dx[, icd_code := toupper(trimws(as.character(icd_code)))]
  dx[, icd_version := as.character(icd_version)]
  dx[, qual := FALSE]
  for (ver in names(cohort_codes)) {
    idx <- dx$icd_version == ver
    if (any(idx)) dx[idx, qual := code_matches(icd_code, cohort_codes[[ver]])]
  }
  dx[qual == TRUE, .(subject_id, hadm_id)] |> unique()
}

# ---- Drug classifier (shared) -----------------------------------------------
# Classify a vector of (lower-cased) drug names into a display class using a
# map of `class name -> substrings`, matched in list order, first hit wins.
# Shared by 03_treatment_patterns.R (exposure profiling) and the exposure-based
# arm assignment below so both use identical matching semantics.
classify_drug <- function(drug_l, treatment_map) {
  cls <- rep(NA_character_, length(drug_l))
  for (class_name in names(treatment_map)) {
    subs <- treatment_map[[class_name]]
    pat  <- paste(subs, collapse = "|")
    hit  <- is.na(cls) & grepl(pat, drug_l)
    cls[hit] <- class_name
  }
  cls
}

# ---- Exposure-based arm assignment (active-comparator, new-user) -------------
# Given a drugs table and two exposure maps (cohort vs comparator), assign each
# patient to an arm by DRUG EXPOSURE (not by diagnosis code). This is the
# generalizable core of `comparator = "active_comparator"`.
#
# Returns a data.table: subject_id, arm ("cohort"/"comparator"),
#   first_exposure_time (POSIXct of the first qualifying fill for that arm),
#   qual_hadm (hadm_id carrying that first qualifying fill).
#
# overlap_rule:
#   "first_exposure" (default): a patient exposed to BOTH classes is assigned to
#      whichever qualifying drug they filled FIRST (new-user logic); time zero
#      is that first qualifying fill. Patients exposed to NEITHER are dropped.
#   "exclude": patients exposed to BOTH qualifying classes (anywhere in the
#      ascertainment set) are dropped; the rest keep their single arm.
#
# `eligible_ids` restricts assignment to the disease-eligible pool.
# Route / drug-exclude filtering is applied by the CALLER before passing `drugs`.
assign_exposure_arms <- function(drugs, cohort_map, comparator_map,
                                 eligible_ids, overlap_rule = "first_exposure") {
  stopifnot(length(cohort_map) > 0, length(comparator_map) > 0)
  d <- copy(drugs)
  d[, drug_l := tolower(as.character(drug))]
  d <- d[subject_id %in% eligible_ids]
  d[, arm := NA_character_]
  d[!is.na(classify_drug(drug_l, cohort_map)),     arm := "cohort"]
  # comparator only where not already a cohort-class drug (maps are disjoint by
  # construction; this guards accidental substring overlap, cohort takes priority
  # only at the row level -- patient-level overlap is handled below).
  d[is.na(arm) & !is.na(classify_drug(drug_l, comparator_map)), arm := "comparator"]
  d <- d[!is.na(arm)]
  if (!nrow(d)) return(data.table(subject_id = integer(0), arm = character(0),
                                  first_exposure_time = as_dt(NA), qual_hadm = integer(0)))

  # earliest qualifying fill per patient-arm
  setorder(d, subject_id, starttime)
  per_arm <- d[, .(first_time = starttime[1], qual_hadm = hadm_id[1]), by = .(subject_id, arm)]

  both <- per_arm[, .N, by = subject_id][N >= 2, subject_id]
  if (overlap_rule == "exclude") {
    per_arm <- per_arm[!subject_id %in% both]
    winners <- per_arm
  } else {  # "first_exposure"
    setorder(per_arm, subject_id, first_time)
    winners <- per_arm[, .SD[1], by = subject_id]   # earliest qualifying fill wins the arm
  }
  data.table(subject_id = winners$subject_id, arm = winners$arm,
             first_exposure_time = winners$first_time, qual_hadm = winners$qual_hadm)
}

# ---- Severity assignment (most-severe-wins) ---------------------------------
assign_severity <- function(dx, severity_tiers) {
  if (is.null(severity_tiers)) return(NULL)
  dx <- copy(dx)
  dx[, icd_code := toupper(trimws(as.character(icd_code)))]
  dx[, icd_version := as.character(icd_version)]
  out <- data.table(subject_id = integer(0), hadm_id = integer(0), tier = character(0))
  # Walk tiers from most to least severe; assign on first match per hadm.
  assigned <- data.table()
  for (t in severity_tiers) {
    hit <- copy(dx)[, keep := FALSE]
    for (ver in names(t$codes)) {
      idx <- hit$icd_version == ver
      if (any(idx)) hit[idx, keep := code_matches(icd_code, t$codes[[ver]])]
    }
    tier_hits <- unique(hit[keep == TRUE, .(subject_id, hadm_id)])
    if (nrow(tier_hits)) {
      tier_hits[, tier := t$name]
      out <- rbind(out, tier_hits)
    }
  }
  # first tier (most severe) wins per hadm
  out[, .SD[1], by = .(subject_id, hadm_id)]
}

# ---- median [IQR] and n(%) formatters ---------------------------------------
fmt_med_iqr <- function(x, digits = 1) {
  x <- x[!is.na(x)]
  if (!length(x)) return("NA")
  q <- quantile(x, c(.5, .25, .75), na.rm = TRUE)
  sprintf("%.*f [%.*f-%.*f]", digits, q[1], digits, q[2], digits, q[3])
}
fmt_n_pct <- function(n, N, digits = 1) {
  if (N == 0) return("0 (NA)")
  sprintf("%d (%.*f%%)", n, digits, 100 * n / N)
}

# ---- Safe writer to /mnt/results (CSV is FUSE-safe) --------------------------
write_out <- function(dt, subdir, filename) {
  d <- file.path(CFG$paths$out_dir, subdir)
  dir.create(d, showWarnings = FALSE, recursive = TRUE)
  fwrite(dt, file.path(d, filename))
  invisible(file.path(d, filename))
}
