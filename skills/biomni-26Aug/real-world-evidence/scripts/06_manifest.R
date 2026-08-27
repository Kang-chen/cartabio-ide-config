# =============================================================================
# 06_manifest.R  --  Assemble report_manifest.json for the Python PDF builder
# -----------------------------------------------------------------------------
# Reads CFG + the computed CSVs and writes <out_dir>/report_manifest.json. The
# manifest carries study identity, method parameters, headline numbers, figure
# captions, discussion/limitations/next-steps text, and (optionally) a list of
# references produced by Biomni LiteratureSearch.
#
# LITERATURE: This script does NOT call LiteratureSearch itself (that is an
# agent tool, not an R function). The agent runs LiteratureSearch with
# CFG$literature_queries, then passes the formatted citation strings in via the
# `references` argument (a character vector) OR by writing them to
# <out_dir>/tables/references.txt (one per line) before this script runs.
# If neither is present, the References section is omitted.
# =============================================================================
if (!exists("SCRIPTS_DIR")) {
  .fa <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  SCRIPTS_DIR <- if (length(.fa)) dirname(sub("^--file=", "", .fa)) else "."
}
source(file.path(SCRIPTS_DIR, "_utils.R"))
suppressMessages(library(jsonlite))

run_manifest <- function(CFG, references = NULL) {
  out_dir <- CFG$paths$out_dir
  tdir <- file.path(out_dir, "tables")
  rd <- function(f) if (file.exists(file.path(tdir, f)))
    fread(file.path(tdir, f)) else NULL

  t1  <- rd("table1_cohort.csv")
  cmp <- rd("cohort_vs_comparator.csv")
  lm  <- rd("survival_landmark_cohort.csv")
  txs <- rd("treatment_summary.csv")
  lr  <- rd("survival_logrank.csv")

  gcol <- CFG$cohort_label
  ccol <- CFG$comparator_label

  # ---- pull headline values defensively (never hardcode) -------------------
  gv <- function(tbl, var, col) {
    if (is.null(tbl) || !("Variable" %in% names(tbl)) || !(col %in% names(tbl)))
      return(NA_character_)
    v <- tbl[[col]][tbl$Variable == var]
    if (length(v)) as.character(v[1]) else NA_character_
  }
  n_cohort   <- gv(t1, "N (patients)", gcol)
  death_c    <- gv(t1, "In-hospital death", gcol)
  death_k    <- gv(t1, "In-hospital death", ccol)
  surv30 <- if (!is.null(lm) && 30 %in% lm$Day)
    sprintf("%.0f%%", 100 * lm$Survival[lm$Day == 30][1]) else NA_character_
  surv90 <- if (!is.null(lm) && 90 %in% lm$Day)
    sprintf("%.0f%%", 100 * lm$Survival[lm$Day == 90][1]) else NA_character_

  # ---- key finding sentence (assembled from real numbers) ------------------
  bits <- c()
  if (!is.na(n_cohort)) bits <- c(bits, sprintf("The cohort comprised %s %s patients.", n_cohort, gcol))
  if (!is.na(death_c) && !is.na(death_k))
    bits <- c(bits, sprintf("In-hospital death was %s in %s vs %s in %s.", death_c, gcol, death_k, ccol))
  if (!is.na(surv30) && !is.na(surv90))
    bits <- c(bits, sprintf("Landmark survival was %s at 30 days and %s at 90 days.", surv30, surv90))
  key_finding <- if (length(bits)) paste(bits, collapse = " ") else NULL

  # ---- method parameter table ----------------------------------------------
  mp <- list()
  add_mp <- function(k, v) if (length(v) && !all(is.na(v)))
    mp[[length(mp) + 1]] <<- list(k, paste(v, collapse = ", "))
  add_mp("Cohort definition", "diagnosis code-based (see config)")
  add_mp("Index encounter", CFG$index_rule)
  add_mp("Time origin", CFG$time_origin)
  add_mp("Comparator", CFG$comparator)
  add_mp("Treatment class", CFG$treatment_label)
  add_mp("Systemic routes only", if (is.null(CFG$systemic_routes)) "no (all routes)" else "yes")
  add_mp("Exposure scope", CFG$treatment_exposure_scope %||% "index_encounter")
  add_mp("Landmark times (days)", CFG$landmark_times)
  add_mp("EPV threshold for Cox", CFG$epv_min)
  add_mp("Multiple-testing correction", CFG$multiple_testing %||% "none")

  # ---- log-rank note for discussion ----------------------------------------
  # Column names as written by 04_survival.R: Comparison, ChiSq, df, p (exploratory).
  # Resolve defensively by matching known aliases so casing changes don't break it.
  pick_col <- function(tbl, aliases) {
    hit <- names(tbl)[tolower(names(tbl)) %in% tolower(aliases)]
    if (length(hit)) hit[1] else NA_character_
  }
  lr_note <- NULL
  if (!is.null(lr) && nrow(lr) > 0) {
    c_cmp <- pick_col(lr, c("Comparison", "comparison"))
    c_chi <- pick_col(lr, c("ChiSq", "chisq", "chi_sq"))
    c_p   <- pick_col(lr, c("p (exploratory)", "p_value", "p", "pvalue"))
    if (!is.na(c_cmp) && !is.na(c_p)) {
      lr_note <- paste(vapply(seq_len(nrow(lr)), function(i)
        sprintf("Log-rank %s: chi-sq=%s, p=%s (exploratory).",
                lr[[c_cmp]][i],
                if (!is.na(c_chi)) lr[[c_chi]][i] else "NA",
                lr[[c_p]][i]), character(1)), collapse = " ")
    }
  }

  # ---- references (from LiteratureSearch, passed in or from file) ----------
  if (is.null(references)) {
    rf <- file.path(tdir, "references.txt")
    if (file.exists(rf)) references <- readLines(rf, warn = FALSE)
  }
  references <- references[nzchar(trimws(references))]

  # ---- figure captions (map known filenames) -------------------------------
  fig_caps <- list()
  fmap <- c(
    "fig_km_cohort.png" = "Kaplan-Meier survival for the cohort vs comparator, with 95% CI.",
    "fig_km_treatment.png" = "Kaplan-Meier survival stratified by treatment exposure.",
    "fig_treatment_classes.png" = "Distribution of treatment classes in the cohort.",
    "fig_severity.png" = "Cohort severity / subtype distribution.",
    "fig_time_to_first.png" = "Distribution of time to first treatment exposure.")
  for (nm in names(fmap)) fig_caps[[nm]] <- fmap[[nm]]

  manifest <- list(
    study_title = CFG$study_title,
    cohort_label = gcol,
    comparator_label = ccol,
    primary_endpoint = CFG$primary_endpoint,
    output_filename = sprintf("report_%s.pdf", CFG$slug),
    key_finding = key_finding,
    method_params = mp,
    figure_captions = fig_caps,
    discussion = c(
      sprintf(paste0("This real-world analysis characterizes presentation, ",
        "treatment, and %s in the %s cohort relative to a %s comparator. ",
        "Observational EHR data are subject to confounding by indication, so ",
        "associations are descriptive rather than causal."),
        tolower(CFG$primary_endpoint), gcol, ccol),
      paste0(if (!is.null(lr_note)) paste0(lr_note, " ") else "",
        "Patient-level and admission-level mortality denominators can differ, ",
        "and in-hospital versus all-cause survival curves may diverge when ",
        "deaths occur after discharge.")),
    limitations = c(
      "Retrospective, single-source design; no randomization.",
      "Diagnosis-code phenotyping may misclassify cases.",
      "All p-values exploratory; no multiple-testing correction applied.",
      "Treatment exposure reflects orders, not confirmed administration.",
      "Residual and unmeasured confounding cannot be excluded."),
    conclusions = c(sprintf(paste0("In this real-world %s cohort, severity, ",
      "treatment intensity, and %s were characterized and compared with a ",
      "%s group using a reproducible, config-driven pipeline. Findings are ",
      "hypothesis-generating and should be confirmed in validated cohorts."),
      gcol, tolower(CFG$primary_endpoint), ccol)),
    next_steps = c(
      "Validate the cohort definition against chart review or a validated phenotype.",
      "Expand to the full (credentialed) data source to increase power and EPV.",
      "Pre-specify confounders and apply propensity or regression adjustment.",
      "Run sensitivity analyses (alternative code sets, windows, comparators)."),
    references = as.list(references)
  )

  # Force multi-paragraph text fields to serialize as JSON ARRAYS even when they
  # hold a single element. Without this, auto_unbox=TRUE collapses a length-1
  # character vector to a bare JSON string, which the Python builder would then
  # iterate character-by-character (one char per line). as.list() guarantees an
  # array; the Python side also guards against this (see mget() in build_report.py).
  for (fld in c("executive_summary", "discussion", "limitations",
                "conclusions", "next_steps", "references")) {
    if (!is.null(manifest[[fld]])) manifest[[fld]] <- as.list(manifest[[fld]])
  }

  outp <- file.path(out_dir, "report_manifest.json")
  write(toJSON(manifest, auto_unbox = TRUE, pretty = TRUE, null = "null"), outp)
  cat(sprintf("[manifest] wrote %s (%d references)\n", outp, length(references)))
  invisible(outp)
}

if (sys.nframe() == 0) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) >= 1) source(args[1])
  run_manifest(CFG)
}
