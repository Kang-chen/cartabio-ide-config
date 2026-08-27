# =============================================================================
# 05_comparison.R  --  focused cohort vs comparator comparison table
# -----------------------------------------------------------------------------
# INPUT : /workspace/rwe/base_cohort.RData (+ tx flags, survival if available)
# OUTPUT: tables/cohort_vs_comparator.csv ; /workspace/rwe/comparison.RData
# A compact, report-ready comparison of the key clinical variables with
# EXPLORATORY p-values (no multiple-testing correction; stated in Methods).
# =============================================================================
if (!exists("SCRIPTS_DIR")) {
  .fa <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  SCRIPTS_DIR <- if (length(.fa)) dirname(sub("^--file=", "", .fa)) else "."
}
source(file.path(SCRIPTS_DIR, "_utils.R"))

run_comparison <- function(CFG) {
  load("/workspace/rwe/base_cohort.RData")
  tx_flags_file <- "/workspace/rwe/tx_patient_flags.RData"
  if (file.exists(tx_flags_file)) {
    load(tx_flags_file)
    analysis <- merge(analysis, tx_pt, by = "subject_id", all.x = TRUE)
    analysis[is.na(on_treatment), on_treatment := 0L]
  }

  grp <- CFG$cohort_label; cmp <- CFG$comparator_label
  A <- analysis[group == grp]; B <- analysis[group == cmp]

  num_row <- function(label, col, d = 1) {
    a <- A[[col]]; b <- B[[col]]
    p <- tryCatch(wilcox.test(a, b)$p.value, error = function(e) NA_real_)
    data.table(Variable = label, X = fmt_med_iqr(a, d), Y = fmt_med_iqr(b, d),
               p = p)
  }
  bin_row <- function(label, col) {
    a <- A[[col]]; b <- B[[col]]
    na1 <- sum(a == 1, na.rm = TRUE); nb1 <- sum(b == 1, na.rm = TRUE)
    m <- matrix(c(na1, sum(a == 0, na.rm = TRUE), nb1, sum(b == 0, na.rm = TRUE)), 2)
    p <- tryCatch(fisher.test(m)$p.value, error = function(e) NA_real_)
    data.table(Variable = label,
               X = sprintf("%d/%d (%.0f%%)", na1, sum(!is.na(a)), 100*na1/sum(!is.na(a))),
               Y = sprintf("%d/%d (%.0f%%)", nb1, sum(!is.na(b)), 100*nb1/sum(!is.na(b))),
               p = p)
  }

  rows <- list()
  if ("age" %in% names(analysis))       rows[[length(rows)+1]] <- num_row("Age, years", "age")
  if ("icu_los" %in% names(analysis) && any(!is.na(analysis$icu_los)))
                                        rows[[length(rows)+1]] <- num_row("ICU LOS, days", "icu_los")
  if ("inhosp_death" %in% names(analysis)) rows[[length(rows)+1]] <- bin_row("In-hospital death", "inhosp_death")
  if ("on_treatment" %in% names(analysis)) rows[[length(rows)+1]] <-
      bin_row(sprintf("%s use", CFG$treatment_label %||% "Treatment"), "on_treatment")

  tab <- rbindlist(rows)
  setnames(tab, c("X","Y"), c(grp, cmp))
  tab[, `p (exploratory)` := ifelse(is.na(p), "", sprintf("%.3f", p))]
  tab[, p := NULL]

  write_out(tab, "tables", "cohort_vs_comparator.csv")
  save(tab, file = "/workspace/rwe/comparison.RData")
  cat("[05] Comparison table:\n"); print(tab)
  invisible(tab)
}

if (sys.nframe() == 0) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) >= 1) source(args[1])
  run_comparison(CFG)
}
