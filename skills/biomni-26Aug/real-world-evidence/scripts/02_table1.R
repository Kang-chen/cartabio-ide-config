# =============================================================================
# 02_table1.R  --  baseline characteristics (cohort vs comparator)
# -----------------------------------------------------------------------------
# INPUT : /workspace/rwe/base_cohort.RData (+ treatment flags if 03 ran first)
# OUTPUT: tables/table1_cohort.csv  and /workspace/rwe/table1.RData
# Auto-types variables: numeric -> median [IQR] + Wilcoxon; binary/categorical
# -> n(%) + Fisher/chi-square. ALL p-values are EXPLORATORY (see report Methods).
# =============================================================================
if (!exists("SCRIPTS_DIR")) {
  .fa <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  SCRIPTS_DIR <- if (length(.fa)) dirname(sub("^--file=", "", .fa)) else "."
}
source(file.path(SCRIPTS_DIR, "_utils.R"))

run_table1 <- function(CFG) {
  load("/workspace/rwe/base_cohort.RData")            # analysis, CFG, ...
  # optional treatment flags produced by 03
  tx_flags_file <- "/workspace/rwe/tx_patient_flags.RData"
  if (file.exists(tx_flags_file)) {
    load(tx_flags_file)                               # tx_pt: subject_id, on_treatment
    analysis <- merge(analysis, tx_pt, by = "subject_id", all.x = TRUE)
    analysis[is.na(on_treatment), on_treatment := 0L]
  }

  grp <- CFG$cohort_label
  cmp <- CFG$comparator_label
  A <- analysis[group == grp]; B <- analysis[group == cmp]

  num_test <- function(a, b) tryCatch(
    wilcox.test(a, b)$p.value, error = function(e) NA_real_)
  bin_test <- function(av, bv) {
    m <- matrix(c(sum(av == 1, na.rm = TRUE), sum(av == 0, na.rm = TRUE),
                  sum(bv == 1, na.rm = TRUE), sum(bv == 0, na.rm = TRUE)),
                nrow = 2)
    tryCatch(fisher.test(m)$p.value, error = function(e) NA_real_)
  }

  rows <- list()
  add_num <- function(label, col, digits = 1) {
    a <- A[[col]]; b <- B[[col]]
    rows[[length(rows) + 1]] <<- data.table(
      Variable = label,
      A = fmt_med_iqr(a, digits), B = fmt_med_iqr(b, digits),
      p = num_test(a, b))
  }
  add_bin <- function(label, col) {
    a <- A[[col]]; b <- B[[col]]
    rows[[length(rows) + 1]] <<- data.table(
      Variable = label,
      A = fmt_n_pct(sum(a == 1, na.rm = TRUE), sum(!is.na(a))),
      B = fmt_n_pct(sum(b == 1, na.rm = TRUE), sum(!is.na(b))),
      p = bin_test(a, b))
  }

  # N row
  rows[[1]] <- data.table(Variable = "N (patients)",
                          A = as.character(nrow(A)), B = as.character(nrow(B)), p = NA_real_)
  # standard variables (only those present)
  if ("age" %in% names(analysis))          add_num("Age, years", "age")
  if ("sex" %in% names(analysis)) {
    # female fraction; treat common encodings
    fem <- function(x) as.integer(toupper(as.character(x)) %in% c("F","FEMALE","2"))
    A[, .female := fem(sex)]; B[, .female := fem(sex)]
    rows[[length(rows) + 1]] <- data.table(
      Variable = "Female n(%)",
      A = fmt_n_pct(sum(A$.female, na.rm = TRUE), nrow(A)),
      B = fmt_n_pct(sum(B$.female, na.rm = TRUE), nrow(B)),
      p = bin_test(A$.female, B$.female))
  }
  if ("icu_los" %in% names(analysis) && any(!is.na(analysis$icu_los)))
    add_num("ICU LOS, days", "icu_los")
  if ("inhosp_death" %in% names(analysis)) add_bin("In-hospital death", "inhosp_death")
  if ("any_death" %in% names(analysis))    add_bin("Any death recorded", "any_death")
  if ("on_treatment" %in% names(analysis)) add_bin(
    sprintf("%s use", CFG$treatment_label %||% "Treatment"), "on_treatment")

  tab <- rbindlist(rows)
  setnames(tab, c("A", "B"), c(grp, cmp))
  tab[, `p (exploratory)` := ifelse(is.na(p), "", sprintf("%.3f", p))]
  tab[, p := NULL]

  write_out(tab, "tables", "table1_cohort.csv")
  save(tab, file = "/workspace/rwe/table1.RData")
  cat("[02] Table 1:\n"); print(tab)
  invisible(tab)
}

if (sys.nframe() == 0) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) >= 1) source(args[1])
  run_table1(CFG)
}
