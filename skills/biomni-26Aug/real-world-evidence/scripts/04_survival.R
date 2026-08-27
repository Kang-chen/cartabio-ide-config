# =============================================================================
# 04_survival.R  --  survival dataset, Kaplan-Meier, landmark rates,
#                    log-rank, and EPV-GATED Cox regression
# -----------------------------------------------------------------------------
# INPUT : /workspace/rwe/base_cohort.RData
# OUTPUT: tables/survival_landmark_cohort.csv, survival_logrank.csv,
#         tables/cox_results.csv (ONLY if EPV>=CFG$epv_min) ;
#         /workspace/rwe/survival.RData
#
# GUARDRAILS (see references/survival-guardrails.md):
#  - Cox / multivariable models run ONLY if events/covariates >= CFG$epv_min.
#    Otherwise the script prints an explicit suppression note and produces
#    descriptive + KM + landmark + univariable log-rank only.
#  - Report LANDMARK RATES, not the median, when the median's upper CI is NA.
#  - All p-values are EXPLORATORY; no multiple-testing correction.
# =============================================================================
if (!exists("SCRIPTS_DIR")) {
  .fa <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  SCRIPTS_DIR <- if (length(.fa)) dirname(sub("^--file=", "", .fa)) else "."
}
source(file.path(SCRIPTS_DIR, "_utils.R"))
suppressMessages(library(survival))

run_survival <- function(CFG) {
  load("/workspace/rwe/base_cohort.RData")   # analysis, patients, CFG

  A <- copy(analysis)

  # ---- time origin ---------------------------------------------------------
  A[, admittime := as_dt(admittime)]
  if ("intime" %in% names(A)) A[, intime := as_dt(intime)]
  A[, t0 := if (CFG$time_origin == "index_icu_in" && "intime" %in% names(A))
              fifelse(!is.na(intime), intime, admittime) else admittime]

  # ---- death observation & datetime ---------------------------------------
  if (!"deathtime" %in% names(A)) A[, deathtime := as_dt(NA)]
  if (!"dod" %in% names(A))       A[, dod := as_d(NA)]
  if (!"expire_flag" %in% names(A)) A[, expire_flag := NA_integer_]

  A[, death_obs := as.integer((!is.na(dod)) | (!is.na(deathtime)) |
                              (!is.na(expire_flag) & expire_flag == 1))]
  # death datetime: prefer deathtime, else dod at noon
  A[, death_dt := as_dt(NA)]
  A[!is.na(deathtime), death_dt := deathtime]
  A[is.na(death_dt) & !is.na(dod), death_dt := as_dt(paste(dod, "12:00:00"))]

  # ---- censoring: last observed discharge per subject ----------------------
  # use encounter table for the latest discharge
  enc[, dischtime := as_dt(dischtime)]
  last_disch <- enc[, .(last_disch = max(dischtime, na.rm = TRUE)), by = subject_id]
  A <- merge(A, last_disch, by = "subject_id", all.x = TRUE)

  A[, surv_end := fifelse(death_obs == 1 & !is.na(death_dt), death_dt, last_disch)]
  A[, surv_days := as.numeric(difftime(surv_end, t0, units = "days"))]
  A <- A[!is.na(surv_days) & surv_days >= 0]
  A[, event := death_obs]
  A[, grp := factor(group, levels = c(CFG$comparator_label, CFG$cohort_label))]

  # ---- KM fits -------------------------------------------------------------
  fit_all  <- survfit(Surv(surv_days, event) ~ grp, data = A)
  fit_coh  <- survfit(Surv(surv_days, event) ~ 1,
                      data = A[group == CFG$cohort_label])

  # landmark table for the cohort
  landmark <- function(fit, times) {
    s <- summary(fit, times = times, extend = TRUE)
    data.table(Day = times,
               Survival = round(s$surv, 3),
               `Lower 95%` = round(s$lower, 3),
               `Upper 95%` = round(s$upper, 3),
               `N at risk` = s$n.risk,
               `Cum events` = cumsum(s$n.event))
  }
  lm_coh <- landmark(fit_coh, CFG$landmark_times)
  write_out(lm_coh, "tables", "survival_landmark_cohort.csv")

  # ---- log-rank cohort vs comparator --------------------------------------
  lr <- survdiff(Surv(surv_days, event) ~ grp, data = A)
  lr_p <- 1 - pchisq(lr$chisq, df = length(lr$n) - 1)
  logrank <- data.table(
    Comparison = sprintf("%s vs %s", CFG$cohort_label, CFG$comparator_label),
    ChiSq = round(lr$chisq, 3),
    df = length(lr$n) - 1,
    `p (exploratory)` = sprintf("%.3f", lr_p))
  write_out(logrank, "tables", "survival_logrank.csv")

  # ---- EPV GATE for Cox ----------------------------------------------------
  n_events <- sum(A$event)
  covs <- CFG$cox_covariates
  cox_tab <- NULL
  epv <- if (length(covs) > 0) n_events / length(covs) else Inf
  cox_ran <- FALSE
  if (length(covs) > 0 && epv >= CFG$epv_min) {
    covs_present <- covs[covs %in% names(A)]
    if (length(covs_present) > 0) {
      f <- as.formula(paste("Surv(surv_days, event) ~ grp +",
                            paste(covs_present, collapse = " + ")))
      cx <- coxph(f, data = A)
      sm <- summary(cx)
      cox_tab <- data.table(
        Term = rownames(sm$coefficients),
        HR = round(sm$coefficients[, "exp(coef)"], 3),
        `Lower 95%` = round(sm$conf.int[, "lower .95"], 3),
        `Upper 95%` = round(sm$conf.int[, "upper .95"], 3),
        `p (exploratory)` = sprintf("%.3f", sm$coefficients[, "Pr(>|z|)"]))
      # PH check
      ph <- tryCatch(cox.zph(cx), error = function(e) NULL)
      attr(cox_tab, "ph_global_p") <- if (!is.null(ph)) ph$table["GLOBAL", "p"] else NA
      write_out(cox_tab, "tables", "cox_results.csv")
      cox_ran <- TRUE
      cat(sprintf("[04] Cox ran: %d events / %d covariates = EPV %.1f (>= %d).\n",
                  n_events, length(covs_present), epv, CFG$epv_min))
    }
  }
  if (!cox_ran) {
    cat(sprintf(paste0("[04] Cox SUPPRESSED: EPV = %s (events=%d, covariates=%d) ",
                       "< threshold %d. Descriptive + KM + landmark + log-rank only.\n"),
                ifelse(is.finite(epv), sprintf("%.1f", epv), "NA (no covariates)"),
                n_events, length(covs), CFG$epv_min))
  }

  epv_note <- list(n_events = n_events, n_covariates = length(covs),
                   epv = epv, epv_min = CFG$epv_min, cox_ran = cox_ran)

  save(A, fit_all, fit_coh, lm_coh, logrank, cox_tab, epv_note,
       file = "/workspace/rwe/survival.RData")
  cat("[04] Survival landmark (cohort):\n"); print(lm_coh)
  cat("[04] Log-rank:\n"); print(logrank)
  invisible(lm_coh)
}

if (sys.nframe() == 0) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) >= 1) source(args[1])
  run_survival(CFG)
}
