# =============================================================================
# 03_treatment_patterns.R  --  generic treatment-pattern profiling
# -----------------------------------------------------------------------------
# INPUT : /workspace/rwe/base_cohort.RData + drugs table (via CFG$paths$drugs)
# OUTPUT: tables/treatment_class_summary.csv, treatment_top_agents.csv,
#         treatment_summary.csv ; /workspace/rwe/treatment.RData ;
#         /workspace/rwe/tx_patient_flags.RData (for Table 1)
#
# The classifier is 100% config-driven via CFG$treatment_map (display class ->
# substrings, matched in list order, first hit wins). Antibiotics/vasopressors
# are just example instances -- swap the map for any drug class.
# =============================================================================
if (!exists("SCRIPTS_DIR")) {
  .fa <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  SCRIPTS_DIR <- if (length(.fa)) dirname(sub("^--file=", "", .fa)) else "."
}
source(file.path(SCRIPTS_DIR, "_utils.R"))
# classify_drug() is now defined in _utils.R (shared with the exposure-based
# arm assignment in 01_build_cohort.R) so both use identical matching semantics.

run_treatment <- function(CFG) {
  load("/workspace/rwe/base_cohort.RData")     # analysis, enc, CFG
  drugs <- remap_cols(read_any(CFG$paths$drugs), CFG$cols$drugs)
  if (is.null(drugs) || length(CFG$treatment_map) == 0) {
    cat("[03] No drugs table or empty treatment_map -> skipping treatment profiling.\n")
    tx_pt <- data.table(subject_id = integer(0), on_treatment = integer(0))
    save(tx_pt, file = "/workspace/rwe/tx_patient_flags.RData")
    return(invisible(NULL))
  }
  drugs[, starttime := as_dt(starttime)]
  drugs[, drug_l := tolower(as.character(drug))]

  # apply route / exclusion filters + classification ONCE to all drug rows
  d_all <- copy(drugs)
  if (!is.null(CFG$systemic_routes)) d_all <- d_all[route %in% CFG$systemic_routes]
  if (!is.null(CFG$drug_exclude))    d_all <- d_all[!grepl(CFG$drug_exclude, drug_l)]
  d_all[, tx_class := classify_drug(drug_l, CFG$treatment_map)]
  d_all <- d_all[!is.na(tx_class)]

  # ---- BOTH-GROUP exposure flag (for Table 1 / comparison) -----------------
  # A patient is "on treatment" per CFG$treatment_exposure_scope:
  #   "index_encounter" (default): >=1 in-class exposure during the index hadm.
  #   "any_encounter": ever exposed in any admission.
  # Computed for ALL patients (both groups) so the comparator column populates.
  scope <- CFG$treatment_exposure_scope %||% "index_encounter"
  if (scope == "any_encounter") {
    tx_pt <- unique(d_all[subject_id %in% analysis$subject_id, .(subject_id)])[, on_treatment := 1L]
  } else {
    idx_all <- analysis[, .(subject_id, hadm_id)]
    d_idx <- merge(d_all, idx_all, by = c("subject_id", "hadm_id"))
    tx_pt <- unique(d_idx[, .(subject_id)])[, on_treatment := 1L]
  }

  # ---- cohort-only detail (class/agent/timing deliverables) ----------------
  idx <- analysis[group == CFG$cohort_label, .(subject_id, hadm_id, admittime)]
  setnames(idx, "admittime", "idx_admittime")
  dcoh <- merge(d_all, idx, by = c("subject_id","hadm_id"))
  dcoh[, hrs_from_adm := as.numeric(difftime(starttime, idx_admittime, units = "hours"))]

  n_adm <- uniqueN(idx$hadm_id)

  # ---- class distribution (by admissions) ----------------------------------
  class_summ <- dcoh[, .(N_admissions = uniqueN(hadm_id),
                         N_orders = .N), by = tx_class][order(-N_admissions)]
  write_out(class_summ, "tables", "treatment_class_summary.csv")

  # ---- top agents ----------------------------------------------------------
  top_agents <- dcoh[, .(N_orders = .N, N_admissions = uniqueN(hadm_id)),
                     by = .(agent = drug_l)][order(-N_admissions, -N_orders)][1:min(.N, 15)]
  write_out(top_agents, "tables", "treatment_top_agents.csv")

  # ---- exposure, combination therapy, time-to-first ------------------------
  exposed_adm <- uniqueN(dcoh$hadm_id)

  # combination therapy within window
  win <- dcoh[hrs_from_adm >= CFG$combo_window_start & hrs_from_adm <= CFG$combo_window_end]
  ncls <- win[, .(n_classes = uniqueN(tx_class)), by = hadm_id]
  n_combo_denom <- nrow(ncls)
  n_combo <- sum(ncls$n_classes >= CFG$combo_min_classes)
  med_classes <- if (nrow(ncls)) median(ncls$n_classes) else NA_real_

  # time to first exposure
  ttf <- dcoh[hrs_from_adm >= CFG$ttf_window_start]
  ttf[, hrs := pmax(hrs_from_adm, 0)]
  ttf1 <- ttf[, .(t_first = min(hrs)), by = hadm_id]
  med_ttf <- if (nrow(ttf1)) median(ttf1$t_first) else NA_real_

  tx_summary <- data.table(
    Metric = c(sprintf("Cohort admissions with %s", tolower(CFG$treatment_label)),
               sprintf("Combination therapy (>=%d classes, %g-%gh)",
                       CFG$combo_min_classes, CFG$combo_window_start, CFG$combo_window_end),
               "Median distinct classes (window)",
               "Median time to first exposure (h)"),
    Value = c(sprintf("%d/%d (%.0f%%)", exposed_adm, n_adm, 100 * exposed_adm / n_adm),
              sprintf("%d/%d (%.0f%%)", n_combo, n_combo_denom,
                      ifelse(n_combo_denom > 0, 100 * n_combo / n_combo_denom, NA)),
              ifelse(is.na(med_classes), "NA", sprintf("%g", med_classes)),
              ifelse(is.na(med_ttf), "NA", sprintf("%.1f", med_ttf)))
  )
  write_out(tx_summary, "tables", "treatment_summary.csv")

  # ---- patient-level flag for Table 1 (both groups, computed above) --------
  save(tx_pt, file = "/workspace/rwe/tx_patient_flags.RData")

  save(class_summ, top_agents, tx_summary, dcoh, file = "/workspace/rwe/treatment.RData")
  cat("[03] Treatment profiling done.\n")
  print(class_summ); print(tx_summary)
  invisible(tx_summary)
}

if (sys.nframe() == 0) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) >= 1) source(args[1])
  run_treatment(CFG)
}
