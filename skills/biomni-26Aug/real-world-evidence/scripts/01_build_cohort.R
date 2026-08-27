# =============================================================================
# 01_build_cohort.R  --  code-based cohort, index encounter, comparator
# -----------------------------------------------------------------------------
# INPUT : CFG (sourced config) + patient-level tables it points to
# OUTPUT: /workspace/rwe/base_cohort.RData with objects:
#           patients, enc, icu, dx, cohort_ids, sev, index_enc, analysis (per-patient)
#         + tables/cohort_flow.csv (screening funnel)
# Generic: cohort membership and severity come entirely from CFG$cohort_codes /
# CFG$severity_tiers; nothing disease-specific is hardcoded.
# =============================================================================
# Source shared helpers. Resolve this scripts/ folder robustly: use an existing
# SCRIPTS_DIR if the caller set one, else derive from the Rscript --file= arg,
# else fall back to the current directory.
if (!exists("SCRIPTS_DIR")) {
  .fa <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  SCRIPTS_DIR <- if (length(.fa)) dirname(sub("^--file=", "", .fa)) else "."
}
source(file.path(SCRIPTS_DIR, "_utils.R"))

run_build_cohort <- function(CFG) {
  # ---- load & remap --------------------------------------------------------
  patients <- remap_cols(read_any(CFG$paths$patients),   CFG$cols$patients)
  enc      <- remap_cols(read_any(CFG$paths$encounters), CFG$cols$encounters)
  dx       <- remap_cols(read_any(CFG$paths$diagnoses),  CFG$cols$diagnoses)
  icu      <- remap_cols(read_any(CFG$paths$icu_stays),  CFG$cols$icu_stays)

  stopifnot(!is.null(patients), !is.null(enc), !is.null(dx))

  # types
  enc[, admittime := as_dt(admittime)]
  enc[, dischtime := as_dt(dischtime)]
  if ("deathtime" %in% names(enc)) enc[, deathtime := as_dt(deathtime)]
  if ("dod" %in% names(patients)) patients[, dod := as_d(dod)]
  if (!is.null(icu)) { icu[, intime := as_dt(intime)]; icu[, outtime := as_dt(outtime)] }

  n_screened <- uniqueN(patients$subject_id)

  # ==========================================================================
  # ARM DEFINITION
  # --------------------------------------------------------------------------
  # Two regimes, selected by CFG$comparator:
  #
  #  (A) Code-based cohort (comparator = "rest_of_population" | "rest_of_icu"):
  #      cohort_codes DEFINE the cohort; the comparator is everyone else (or the
  #      rest of the ICU). This is the original behavior (e.g. the sepsis
  #      worked example) and is unchanged.
  #
  #  (B) Exposure-based active comparator (comparator = "active_comparator"):
  #      cohort_codes define ELIGIBILITY (e.g. all type-2 diabetes: E11 + 250);
  #      the two ARMS are defined by DRUG EXPOSURE via CFG$exposure_cohort_map
  #      and CFG$exposure_comparator_map (same shape as CFG$treatment_map). This
  #      is the correct design for comparative drug studies -- arms must be
  #      defined by what patients received, NOT by diagnosis-code vintage.
  #
  # In BOTH regimes the downstream contract is identical: build `analysis` with
  # a `group` column set to CFG$cohort_label / CFG$comparator_label, plus
  # cohort_ids / comp_ids. Nothing downstream (02/04/05/06) changes.
  # ==========================================================================
  active_cmp <- identical(CFG$comparator, "active_comparator")

  # ELIGIBILITY (regime B) or COHORT membership (regime A) both start from codes.
  cohort_hadm  <- flag_cohort(dx, CFG$cohort_codes)          # subject_id, hadm_id
  eligible_ids <- unique(cohort_hadm$subject_id)             # disease-eligible pool
  n_eligible   <- length(eligible_ids)

  # severity (optional)
  sev <- assign_severity(dx, CFG$severity_tiers)

  # ---- exposure-based arms (regime B): assign cohort/comparator by drug ------
  exposure_arms <- NULL
  if (active_cmp) {
    stopifnot(
      "active_comparator requires CFG$exposure_cohort_map"     = length(CFG$exposure_cohort_map)     > 0,
      "active_comparator requires CFG$exposure_comparator_map" = length(CFG$exposure_comparator_map) > 0)
    drugs_arm <- remap_cols(read_any(CFG$paths$drugs), CFG$cols$drugs)
    stopifnot("active_comparator requires a drugs table" = !is.null(drugs_arm))
    drugs_arm[, starttime := as_dt(starttime)]
    drugs_arm[, drug_l := tolower(as.character(drug))]
    # same route / exclusion filters the treatment profiler uses, so the arm
    # exposure and the profiled exposure are defined consistently.
    if (!is.null(CFG$systemic_routes)) drugs_arm <- drugs_arm[route %in% CFG$systemic_routes]
    if (!is.null(CFG$drug_exclude))    drugs_arm <- drugs_arm[!grepl(CFG$drug_exclude, drug_l)]
    overlap_rule <- CFG$exposure_overlap_rule %||% "first_exposure"
    exposure_arms <- assign_exposure_arms(
      drugs_arm, CFG$exposure_cohort_map, CFG$exposure_comparator_map,
      eligible_ids, overlap_rule = overlap_rule)
    cohort_ids <- exposure_arms[arm == "cohort", subject_id]
    n_cohort   <- length(cohort_ids)
    cat(sprintf(paste0("[01] active_comparator: eligible=%d -> cohort-arm=%d, ",
                       "comparator-arm=%d (overlap rule: %s)\n"),
                n_eligible, n_cohort, exposure_arms[arm == "comparator", .N], overlap_rule))
  } else {
    cohort_ids <- eligible_ids
    n_cohort   <- length(cohort_ids)
  }

  # ---- index encounter -----------------------------------------------------
  # Regime A: first admission carrying a qualifying (cohort) code.
  # Regime B: the encounter carrying the patient's FIRST QUALIFYING EXPOSURE
  #           (immortal-time-safe: time zero cannot predate the exposure that
  #           defines the arm). Falls back to first eligible admission if the
  #           qualifying fill's hadm is not in the encounter table.
  if (active_cmp) {
    coh_arm <- exposure_arms[arm == "cohort"]
    idx_from_exp <- enc[coh_arm[, .(subject_id, hadm_id = qual_hadm)],
                        on = .(subject_id, hadm_id), nomatch = 0]
    setorder(idx_from_exp, subject_id, admittime)
    index_enc <- idx_from_exp[, .SD[1], by = subject_id]
    # fallback for any cohort-arm patient whose qual_hadm is absent from enc
    missing_idx <- setdiff(cohort_ids, index_enc$subject_id)
    if (length(missing_idx)) {
      fb <- enc[cohort_hadm, on = .(subject_id, hadm_id), nomatch = 0][subject_id %in% missing_idx]
      setorder(fb, subject_id, admittime)
      index_enc <- rbind(index_enc, fb[, .SD[1], by = subject_id], fill = TRUE)
    }
  } else {
    qual_enc <- enc[cohort_hadm, on = .(subject_id, hadm_id), nomatch = 0]
    setorder(qual_enc, subject_id, admittime)
    index_enc <- qual_enc[, .SD[1], by = subject_id]
  }

  # attach first ICU stay within index encounter (for time origin / LOS)
  if (!is.null(icu)) {
    icu_idx <- icu[index_enc[, .(subject_id, hadm_id)], on = .(subject_id, hadm_id), nomatch = 0]
    setorder(icu_idx, subject_id, intime)
    icu_idx <- icu_idx[, .SD[1], by = subject_id]
    index_enc <- merge(index_enc, icu_idx[, .(subject_id, stay_id, intime, outtime, los)],
                       by = "subject_id", all.x = TRUE)
  } else {
    index_enc[, `:=`(intime = NA, outtime = NA, los = NA_real_)]
  }

  # optional ICU requirement
  n_before_icu_req <- nrow(index_enc)
  if (isTRUE(CFG$require_icu)) index_enc <- index_enc[!is.na(intime)]
  cohort_ids <- index_enc$subject_id

  # ---- comparator ----------------------------------------------------------
  if (active_cmp) {
    # Regime B: comparator arm = disease-eligible patients assigned to the
    # comparator EXPOSURE (not "everyone else").
    comp_ids <- exposure_arms[arm == "comparator", subject_id]
  } else if (CFG$comparator == "rest_of_icu") {
    stopifnot(!is.null(icu))
    icu_subj <- unique(icu$subject_id)
    comp_ids <- setdiff(icu_subj, cohort_ids)
  } else {
    comp_ids <- setdiff(unique(patients$subject_id), cohort_ids)
  }

  # comparator index encounter.
  #  - Regime B (active_comparator): the encounter carrying the comparator arm's
  #    first qualifying exposure (immortal-time-safe, symmetric with the cohort).
  #  - Regime A ICU-based: first admission THAT HAS AN ICU STAY (so the
  #    in-hospital-death denominator matches the cohort's ICU definition).
  #  - Regime A otherwise: first admission overall.
  icu_based <- (!active_cmp) &&
    ((CFG$comparator == "rest_of_icu") || (CFG$time_origin == "index_icu_in"))
  comp_enc <- enc[subject_id %in% comp_ids]
  if (active_cmp) {
    comp_arm <- exposure_arms[arm == "comparator"]
    cidx <- enc[comp_arm[, .(subject_id, hadm_id = qual_hadm)],
                on = .(subject_id, hadm_id), nomatch = 0]
    setorder(cidx, subject_id, admittime)
    comp_index <- cidx[, .SD[1], by = subject_id]
    missing_c <- setdiff(comp_ids, comp_index$subject_id)
    if (length(missing_c)) {
      fbc <- enc[cohort_hadm, on = .(subject_id, hadm_id), nomatch = 0][subject_id %in% missing_c]
      setorder(fbc, subject_id, admittime)
      comp_index <- rbind(comp_index, fbc[, .SD[1], by = subject_id], fill = TRUE)
    }
    if (!is.null(icu)) {
      icu_c <- icu[comp_index[, .(subject_id, hadm_id)], on = .(subject_id, hadm_id), nomatch = 0]
      setorder(icu_c, subject_id, intime); icu_c <- icu_c[, .SD[1], by = subject_id]
      comp_index <- merge(comp_index, icu_c[, .(subject_id, stay_id, intime, outtime, los)],
                          by = "subject_id", all.x = TRUE)
    } else {
      comp_index[, `:=`(intime = NA, outtime = NA, los = NA_real_)]
    }
  } else if (icu_based && !is.null(icu)) {
    icu_first <- icu[order(subject_id, intime)][, .SD[1], by = .(subject_id, hadm_id)]
    # keep only comparator admissions that have an ICU stay, earliest by ICU intime
    comp_icu <- merge(comp_enc, icu_first[, .(subject_id, hadm_id, stay_id, intime, outtime, los)],
                      by = c("subject_id", "hadm_id"))
    setorder(comp_icu, subject_id, intime)
    comp_index <- comp_icu[, .SD[1], by = subject_id]
  } else {
    setorder(comp_enc, subject_id, admittime)
    comp_index <- comp_enc[, .SD[1], by = subject_id]
    if (!is.null(icu)) {
      icu_c <- icu[comp_index[, .(subject_id, hadm_id)], on = .(subject_id, hadm_id), nomatch = 0]
      setorder(icu_c, subject_id, intime)
      icu_c <- icu_c[, .SD[1], by = subject_id]
      comp_index <- merge(comp_index, icu_c[, .(subject_id, stay_id, intime, outtime, los)],
                          by = "subject_id", all.x = TRUE)
    } else {
      comp_index[, `:=`(intime = NA, outtime = NA, los = NA_real_)]
    }
  }

  # ---- per-patient analysis table (cohort + comparator) --------------------
  build_pt <- function(idx, grp) {
    dt <- merge(idx, patients, by = "subject_id", all.x = TRUE)
    dt[, group := grp]
    dt
  }
  analysis <- rbind(build_pt(index_enc, CFG$cohort_label),
                    build_pt(comp_index, CFG$comparator_label),
                    fill = TRUE)

  # age
  if (isTRUE(CFG$age_is_precomputed)) {
    analysis[, age := suppressWarnings(as.numeric(dob))]
  } else {
    analysis[, age := as.numeric(difftime(admittime, as_d(dob), units = "days")) / 365.25]
  }

  # in-hospital death at index encounter
  if (!"expire_flag" %in% names(analysis)) analysis[, expire_flag := NA_integer_]
  analysis[, inhosp_death := as.integer(
    (!is.na(expire_flag) & expire_flag == 1) |
    ("deathtime" %in% names(analysis) & !is.na(deathtime))
  )]
  # any death recorded (in- or out-of-hospital)
  analysis[, any_death := as.integer(
    (!is.na(dod)) |
    ("deathtime" %in% names(analysis) & !is.na(deathtime)) |
    (!is.na(expire_flag) & expire_flag == 1)
  )]

  # ICU LOS days
  analysis[, icu_los := suppressWarnings(as.numeric(los))]

  # ---- cohort flow ---------------------------------------------------------
  if (active_cmp) {
    # Exposure-based funnel: screened -> disease-eligible -> two exposure arms.
    flow <- data.table(
      Step = c("Patients screened",
               "Disease-eligible (code definition)",
               sprintf("%s arm (exposure)", CFG$cohort_label),
               sprintf("%s arm (exposure)", CFG$comparator_label)),
      N = c(n_screened, n_eligible, length(cohort_ids), length(comp_ids)))
  } else {
    flow <- data.table(
      Step = c("Patients screened",
               sprintf("Meeting %s code definition", CFG$cohort_label),
               if (isTRUE(CFG$require_icu)) "With ICU stay in index encounter" else NULL,
               "In final cohort",
               sprintf("Comparator (%s)", CFG$comparator_label)),
      N = c(n_screened, n_cohort,
            if (isTRUE(CFG$require_icu)) nrow(index_enc) else NULL,
            length(cohort_ids), length(comp_ids)))
  }
  write_out(flow, "tables", "cohort_flow.csv")

  dir.create("/workspace/rwe", showWarnings = FALSE, recursive = TRUE)
  save(patients, enc, icu, dx, cohort_ids, comp_ids, sev, index_enc, comp_index,
       analysis, flow, exposure_arms, CFG, file = "/workspace/rwe/base_cohort.RData")

  if (active_cmp) {
    cat(sprintf("[01] active_comparator: %s arm=%d  %s arm=%d  (eligible=%d, screened=%d)\n",
                CFG$cohort_label, length(cohort_ids), CFG$comparator_label,
                length(comp_ids), n_eligible, n_screened))
  } else {
    cat(sprintf("[01] Cohort=%d  Comparator=%d  (screened=%d)\n",
                length(cohort_ids), length(comp_ids), n_screened))
  }
  print(flow)
  invisible(analysis)
}

# Allow standalone execution: Rscript 01_build_cohort.R my_config.R
if (sys.nframe() == 0) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) >= 1) source(args[1])
  run_build_cohort(CFG)
}
