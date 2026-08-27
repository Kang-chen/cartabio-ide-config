# =============================================================================
# Survival Analysis Workflow
# =============================================================================
# Core functions for Cox proportional hazards analysis, Kaplan-Meier estimation,
# risk stratification, and assumption testing.
#
# Usage:
#   source("scripts/basic_workflow.R")
#   result <- run_survival_analysis(data)
# =============================================================================

library(survival)

# =============================================================================
# Main Entry Point
# =============================================================================

run_survival_analysis <- function(data, covariates = NULL,
                                  risk_strata_col = NULL,
                                  risk_strata_method = "median",
                                  reference_levels = NULL) {
    cat("\n=== Running Survival Analysis ===\n\n")

    clinical <- data$clinical
    event_col <- data$event_col
    time_col <- data$time_col
    strata_col <- data$strata_col

    # Carry endpoint metadata from the data loader into the result
    endpoint_code <- data$endpoint_code %||% "OS"
    endpoint_label <- data$endpoint_label %||% "Overall Survival (OS)"
    endpoint_convention <- data$endpoint_convention
    n_convention_affected <- data$n_convention_affected

    # --- Validate ---
    .validate_survival_data(clinical, event_col, time_col)

    # --- 1. Kaplan-Meier estimation (overall) ---
    cat("1. Kaplan-Meier estimation (overall)...\n")
    km_formula <- as.formula(paste0("Surv(", time_col, ", ", event_col, ") ~ 1"))
    km_overall <- survfit(km_formula, data = clinical)
    median_surv <- summary(km_overall)$table["median"]
    median_reliable <- .median_is_reliable(km_overall)

    # Landmark survival rates (robust even when median is unreliable)
    max_time <- max(clinical[[time_col]], na.rm = TRUE)
    landmark_times <- if (max_time > 3) c(1, 3, 5) else c(0.5, 1, 2)
    landmark_times <- landmark_times[landmark_times < max_time]
    landmark_surv <- .compute_landmark_survival(km_overall, landmark_times)

    # Median follow-up (reverse KM — standard method)
    median_followup <- .compute_median_followup(clinical, time_col, event_col)

    event_rate <- mean(clinical[[event_col]])
    n_censored <- sum(clinical[[event_col]] == 0)
    pct_censored <- round(100 * n_censored / nrow(clinical), 1)

    cat("   Events:", sum(clinical[[event_col]]), "/", nrow(clinical),
        "(", round(100 * event_rate, 1), "% event rate)\n")
    cat("   Censored:", n_censored, "/", nrow(clinical),
        "(", pct_censored, "%)\n")
    cat("   Median follow-up (reverse KM):", round(median_followup, 2), "years\n")

    # Heavy censoring warning — explains why KM curve may drop steeply in the tail
    if (event_rate < 0.20 && pct_censored > 80) {
        cat("\n   NOTE: HEAVY CENSORING DETECTED (", pct_censored,
            "% censored, ", round(100 * event_rate, 1), "% event rate)\n", sep = "")
        cat("   The KM curve may drop steeply in the tail despite a low overall event rate.\n")
        cat("   This is mathematically correct: as patients are censored, the at-risk set\n")
        cat("   shrinks, so each late event causes a larger survival drop. The tail of the\n")
        cat("   curve (where N at risk is small) is UNRELIABLE. Use landmark survival rates.\n")
    }

    if (median_reliable) {
        cat("   Median survival:", round(median_surv, 2), "years\n")
    } else {
        cat("   Median survival: NOT REACHED (KM curve does not cross 50%)\n")
    }

    # Always show landmark survival for transparency
    cat("   Landmark survival rates:\n")
    for (i in seq_len(nrow(landmark_surv))) {
        cat(sprintf("     %g-year OS: %.1f%% (95%% CI: %.1f%%-%.1f%%), n at risk: %d\n",
            landmark_surv$time[i],
            100 * landmark_surv$survival[i],
            100 * landmark_surv$lower_ci[i],
            100 * landmark_surv$upper_ci[i],
            landmark_surv$n_risk[i]))
    }
    cat("\n")

    # --- 2. Kaplan-Meier by strata ---
    km_strata <- NULL
    strata_logrank <- NULL
    if (!is.null(strata_col) && strata_col %in% colnames(clinical)) {
        cat("2. Kaplan-Meier by", strata_col, "...\n")
        # Remove NAs in strata
        strata_data <- clinical[!is.na(clinical[[strata_col]]), ]
        strata_formula <- as.formula(
            paste0("Surv(", time_col, ", ", event_col, ") ~ ", strata_col)
        )
        km_strata <- survfit(strata_formula, data = strata_data)
        strata_logrank <- survdiff(strata_formula, data = strata_data)
        logrank_p <- 1 - pchisq(strata_logrank$chisq, length(strata_logrank$n) - 1)
        cat("   Groups:", paste(names(strata_logrank$n), "=", strata_logrank$n,
            collapse = ", "), "\n")
        cat("   Log-rank chi-sq:", round(strata_logrank$chisq, 2),
            "df:", length(strata_logrank$n) - 1,
            "p:", format.pval(logrank_p, digits = 3), "\n")

        # Warn about unreliable per-stratum medians (upper CI = NA)
        strata_tbl <- summary(km_strata)$table
        if (is.matrix(strata_tbl)) {
            unreliable <- rownames(strata_tbl)[is.na(strata_tbl[, "0.95UCL"])]
            if (length(unreliable) > 0) {
                cat("   NOTE: Median survival NOT RELIABLY ESTIMABLE for:",
                    paste(sub(paste0("^", strata_col, "="), "", unreliable), collapse = ", "),
                    "\n")
                cat("   (Upper 95% CI = NA — KM curve does not cross 50% for these groups.\n")
                cat("    Reported medians are extrapolations. Use landmark rates instead.)\n")
            }
        }
        cat("\n")
    } else {
        cat("2. Skipping stratified KM (no strata column specified)\n\n")
    }

    # --- 3. Cox Proportional Hazards ---
    cat("3. Fitting Cox proportional hazards model...\n")

    # Missing covariate assessment BEFORE fitting
    # For any covariate with >5% missing, compare event rates between groups
    diagnostics <- list()
    missing_assessment <- list()
    exclude_cols <- c(event_col, time_col, "sample_id", "risk_group")
    all_candidates <- setdiff(colnames(clinical), exclude_cols)
    for (col in all_candidates) {
        pct_missing <- mean(is.na(clinical[[col]]))
        if (pct_missing > 0.05) {
            has_val <- !is.na(clinical[[col]])
            event_with <- mean(clinical[[event_col]][has_val])
            event_without <- mean(clinical[[event_col]][!has_val])
            n_missing <- sum(!has_val)
            # Fisher's exact test: is event rate different between groups?
            tbl <- table(
                group = ifelse(has_val, "non_missing", "missing"),
                event = clinical[[event_col]]
            )
            p <- tryCatch(fisher.test(tbl)$p.value, error = function(e) NA)
            informative <- !is.na(p) && p < 0.05
            missing_assessment[[col]] <- list(
                n_missing = n_missing,
                pct_missing = round(100 * pct_missing, 1),
                event_rate_missing = round(event_without, 3),
                event_rate_nonmissing = round(event_with, 3),
                fisher_p = p,
                informative = informative
            )
            if (informative) {
                cat("   WARNING: Potentially informative missingness in '", col,
                    "' (", n_missing, " missing, ", round(100*pct_missing,1),
                    "%): event rate ", round(100*event_without,1),
                    "% (missing) vs ", round(100*event_with,1),
                    "% (non-missing), Fisher p=", format.pval(p, digits=3),
                    "\n", sep = "")
            }
        }
    }
    diagnostics$missing_assessment <- missing_assessment

    # Update clinical with releveled factors from fit_cox_model
    cox_result <- fit_cox_model(clinical, event_col, time_col, covariates,
                                reference_levels = reference_levels)
    # Apply releveled factors back to clinical for downstream use
    if (!is.null(cox_result$clinical_releveled)) clinical <- cox_result$clinical_releveled

    cat("   Concordance (C-index):", round(cox_result$concordance, 3), "\n")
    cat("   Significant covariates (p<0.05):",
        sum(cox_result$coefficients$pval < 0.05, na.rm = TRUE), "/",
        nrow(cox_result$coefficients), "\n")

    # Events per variable (EPV) — warn if underpowered
    n_params <- length(coef(cox_result$model))
    epv <- cox_result$nevent / n_params
    cat("   Events per variable (EPV):", round(epv, 1))
    if (epv < 10) {
        cat(" ** LOW (recommend >= 10; model may be overfitted)\n")
    } else {
        cat(" (adequate)\n")
    }

    # Excluded patients (missing covariates)
    n_in_model <- cox_result$n
    n_excluded <- nrow(clinical) - n_in_model
    complete_case_warning <- FALSE
    if (n_excluded > 0) {
        cat("   Excluded from Cox model:", n_excluded, "/", nrow(clinical),
            "(", round(100 * n_excluded / nrow(clinical), 1),
            "%) due to missing covariates\n")
    }

    # Loud banner when >20% excluded — complete-case analysis is subject to
    # selection bias. Name the covariates responsible from missing_assessment.
    if (n_excluded / nrow(clinical) > 0.20) {
        complete_case_warning <- TRUE
        responsible <- names(missing_assessment)
        cat("\n")
        cat("   ╔════════════════════════════════════════════════════════════╗\n")
        cat("   ║  WARNING: >20% OF PATIENTS EXCLUDED FROM COX MODEL       ║\n")
        cat("   ║  The Cox model is complete-case and subject to selection  ║\n")
        cat("   ║  bias. Covariates with missing values:\n")
        for (nm in responsible) {
            ma <- missing_assessment[[nm]]
            cat("   ║    -", nm, ":", ma$n_missing, "missing (",
                ma$pct_missing, "%)\n", sep = " ")
        }
        cat("   ║  No imputation is performed. Interpret HRs with caution.  ║\n")
        cat("   ╚════════════════════════════════════════════════════════════╝\n\n")
    }

    # Report dropped covariates
    if (length(cox_result$dropped_covariates) > 0) {
        cat("   Dropped covariates:\n")
        for (nm in names(cox_result$dropped_covariates)) {
            cat("     -", nm, ":", cox_result$dropped_covariates[[nm]], "\n")
        }
    }

    cat("\n")

    # Follow-up anomaly check
    max_obs_time <- max(clinical[[time_col]], na.rm = TRUE)
    followup_anomaly <- FALSE
    if (!is.na(median_followup) && median_followup < 2 && max_obs_time > 5) {
        followup_anomaly <- TRUE
        cat("   WARNING: Median follow-up (", round(median_followup, 2),
            " yr) is very short relative to max observation time (",
            round(max_obs_time, 1), " yr).\n", sep = "")
        cat("   This may indicate missing follow-up times for censored patients",
            " or a data freeze artifact.\n")
        cat("   Investigate days_to_last_followup completeness before",
            " interpreting survival estimates.\n\n")
    }
    diagnostics$followup_anomaly <- followup_anomaly
    diagnostics$max_obs_time <- max_obs_time

    # --- 4. Proportional hazards assumption test ---
    cat("4. Testing proportional hazards assumption...\n")
    ph_test <- test_assumptions(cox_result$model)
    global_p <- ph_test$table["GLOBAL", "p"]
    if (global_p < 0.05) {
        cat("   WARNING: Global PH test p =", format.pval(global_p, digits = 3),
            "- assumption may be violated\n")
        violated <- rownames(ph_test$table)[ph_test$table[, "p"] < 0.05]
        violated <- violated[violated != "GLOBAL"]
        if (length(violated) > 0) {
            cat("   Problematic covariates:", paste(violated, collapse = ", "), "\n")
        }
    } else {
        cat("   PH assumption satisfied (global p =",
            format.pval(global_p, digits = 3), ")\n")
    }
    cat("\n")

    # --- 4b. PH sensitivity analysis (only when global p < 0.05) ---
    # Fits time-varying-coefficient models for violating covariates so the
    # report can show early vs late HRs instead of only time-averaged ones.
    ph_sensitivity <- NULL
    if (global_p < 0.05) {
        ph_sensitivity <- tryCatch(
            fit_ph_sensitivity(cox_result$model, clinical, time_col,
                               event_col, ph_test),
            error = function(e) {
                cat("   PH sensitivity analysis failed:", conditionMessage(e),
                    "\n")
                NULL
            }
        )
    }
    # Surface any non-estimable late-period rows as an explicit note (carried
    # separately so the report states the degeneracy instead of emitting NA
    # rows). A zero-row sensitivity table means every survSplit late-period HR
    # was non-estimable, so drop the empty table but keep the note.
    ph_sensitivity_note <- if (!is.null(ph_sensitivity))
        attr(ph_sensitivity, "degenerate_note") else NULL
    if (!is.null(ph_sensitivity) && nrow(ph_sensitivity) == 0)
        ph_sensitivity <- NULL
    cat("\n")

    # --- 5. Risk stratification ---
    cat("5. Creating risk groups...\n")
    risk_col <- risk_strata_col
    if (is.null(risk_col)) {
        # Use Cox linear predictor for risk stratification
        risk_groups <- stratify_risk_groups(
            cox_result$risk_scores,
            method = risk_strata_method
        )
        clinical$risk_group <- risk_groups
        risk_col <- "risk_group"
    }

    risk_data <- clinical[!is.na(clinical[[risk_col]]), ]
    risk_formula <- as.formula(
        paste0("Surv(", time_col, ", ", event_col, ") ~ ", risk_col)
    )
    km_risk <- survfit(risk_formula, data = risk_data)
    risk_logrank <- survdiff(risk_formula, data = risk_data)
    risk_p <- 1 - pchisq(risk_logrank$chisq, length(risk_logrank$n) - 1)
    cat("   Risk group log-rank chi-sq:", round(risk_logrank$chisq, 2),
        "df:", length(risk_logrank$n) - 1,
        "p:", format.pval(risk_p, digits = 3), "\n")

    tab <- table(clinical[[risk_col]])
    cat("   Groups:", paste(names(tab), "=", tab, collapse = ", "), "\n\n")

    # --- 6. Internal validation (C-index) ---
    # K-fold CV + bootstrap optimism correction using base survival only.
    # The apparent C-index is optimistically biased; the optimism-corrected
    # value is the headline discrimination metric.
    cat("6. Internal validation (C-index)...\n")
    validation <- tryCatch(
        validate_cox_model(cox_result$formula, clinical, time_col,
                           event_col, k = 5, B = 100, seed = 42),
        error = function(e) {
            cat("   Validation not computed:", conditionMessage(e), "\n")
            NULL
        }
    )

    if (!is.null(validation)) {
        c_index <- c(
            apparent             = validation$apparent,
            cv                   = validation$cv,
            optimism_corrected   = validation$optimism_corrected
        )
        cat("   C-index (apparent):           ", round(c_index["apparent"], 3), "\n")
        cat("   C-index (5-fold CV):          ", round(c_index["cv"], 3), "\n")
        cat("   C-index (optimism-corrected): ", round(c_index["optimism_corrected"], 3),
            "  <-- headline\n")
    } else {
        c_index <- c(
            apparent           = cox_result$concordance,
            cv                 = NA_real_,
            optimism_corrected = NA_real_
        )
    }
    cat("\n")

    # --- Assemble result ---
    result <- list(
        # KM estimates
        km_overall = km_overall,
        km_strata = km_strata,
        km_risk = km_risk,
        strata_logrank = strata_logrank,
        risk_logrank = risk_logrank,

        # Cox model
        cox = cox_result,
        ph_test = ph_test,

        # Data
        clinical = clinical,
        event_col = event_col,
        time_col = time_col,
        strata_col = strata_col,
        risk_col = risk_col,

        # Metadata
        dataset_name = data$dataset_name,
        description = data$description,
        report_context = data$report_context,
        n_total = nrow(clinical),
        n_events = sum(clinical[[event_col]]),
        median_survival = median_surv,
        concordance = cox_result$concordance,
        c_index = c_index,
        validation = validation,
        risk_strata_method = risk_strata_method,

        # Endpoint metadata (carried from data loader)
        endpoint_code = endpoint_code,
        endpoint_label = endpoint_label,
        endpoint_convention = endpoint_convention,
        n_convention_affected = n_convention_affected,

        # Reliability metrics
        landmark_survival = landmark_surv,
        median_followup = median_followup,
        median_reliable = median_reliable,
        epv = epv,
        n_excluded = n_excluded,
        complete_case_warning = complete_case_warning,

        # PH sensitivity (NULL unless global p < 0.05)
        ph_sensitivity = ph_sensitivity,
        # Note on any non-estimable late-period survSplit HRs (NULL if none)
        ph_sensitivity_note = ph_sensitivity_note,

        # Diagnostics (new)
        dropped_covariates = cox_result$dropped_covariates,
        reference_levels = cox_result$reference_levels,
        diagnostics = diagnostics
    )

    cat("✓ Survival analysis completed successfully!\n")
    cat("  C-index (apparent):           ", round(c_index["apparent"], 3), "\n")
    cat("  C-index (optimism-corrected): ", round(c_index["optimism_corrected"], 3),
        "  <-- headline\n")
    cat("  Events:", result$n_events, "/", result$n_total, "\n")
    if (median_reliable) {
        cat("  Median survival:", round(median_surv, 2), "years\n")
    } else {
        cat("  Median survival: Not reached\n")
        if (nrow(landmark_surv) > 0) {
            best <- landmark_surv[nrow(landmark_surv), ]
            cat(sprintf("  %g-year survival: %.1f%%\n",
                best$time, 100 * best$survival))
        }
    }

    return(result)
}


# =============================================================================
# Cox Proportional Hazards Model
# =============================================================================

fit_cox_model <- function(clinical, event_col, time_col, covariates = NULL,
                          reference_levels = NULL) {
    dropped_covariates <- list()  # Track all dropped covariates with reasons

    # Auto-detect covariates if not specified
    if (is.null(covariates)) {
        exclude <- c(event_col, time_col, "sample_id", "risk_group")
        candidates <- setdiff(colnames(clinical), exclude)

        # Keep only variables with reasonable data (>80% non-missing, >1 unique value)
        # Also drop factor levels with <5 observations to avoid quasi-separation
        covariates <- c()
        for (col in candidates) {
            vals <- clinical[[col]]
            pct_non_na <- mean(!is.na(vals))
            n_unique <- length(unique(na.omit(vals)))
            if (pct_non_na >= 0.80 && n_unique > 1 && n_unique < nrow(clinical) * 0.9) {
                # For factors/characters: drop if any level has <5 observations
                if (is.factor(vals) || is.character(vals)) {
                    tab <- table(vals)
                    if (any(tab < 5)) {
                        cat("   Dropping", col, "- rare factor level(s):",
                            paste(names(tab[tab < 5]), collapse = ", "), "\n")
                        dropped_covariates[[col]] <- paste0(
                            "rare factor level(s): ",
                            paste(names(tab[tab < 5]), collapse = ", "))
                        next
                    }
                }
                covariates <- c(covariates, col)
            } else {
                reason <- c()
                if (pct_non_na < 0.80) reason <- c(reason,
                    paste0("too many missing (", round(100*(1-pct_non_na),1), "%)"))
                if (n_unique <= 1) reason <- c(reason, "only 1 unique value")
                if (n_unique >= nrow(clinical) * 0.9) reason <- c(reason, "near-unique (ID-like)")
                if (length(reason) > 0)
                    dropped_covariates[[col]] <- paste(reason, collapse = "; ")
            }
        }
    }

    if (length(covariates) == 0) {
        stop("No valid covariates found for Cox model. ",
             "Provide covariates explicitly or check data quality.")
    }

    # --- Collinearity check ---
    cat_covs <- covariates[sapply(covariates, function(col)
        is.factor(clinical[[col]]) || is.character(clinical[[col]]))]
    num_covs <- covariates[sapply(covariates, function(col)
        is.numeric(clinical[[col]]))]

    # 1. Derived-variable check: numeric + binned categorical (e.g., age → age_group)
    if (length(cat_covs) >= 1 && length(num_covs) >= 1) {
        for (nc in num_covs) {
            for (cc in cat_covs) {
                if (startsWith(cc, paste0(nc, "_")) || startsWith(cc, paste0(nc, "."))) {
                    cat("   Dropping", cc, "- derived from numeric", nc,
                        "(collinear)\n")
                    dropped_covariates[[cc]] <- paste0("collinear with numeric ", nc)
                    covariates <- setdiff(covariates, cc)
                    cat_covs <- setdiff(cat_covs, cc)
                }
            }
        }
    }

    # 2. Cramer's V for categorical pairs (V > 0.7 → drop the more specific one)
    if (length(cat_covs) >= 2) {
        for (i in seq_along(cat_covs)) {
            for (j in seq_len(i - 1)) {
                ci <- cat_covs[i]; cj <- cat_covs[j]
                if (!(ci %in% covariates) || !(cj %in% covariates)) next
                complete <- complete.cases(clinical[[ci]], clinical[[cj]])
                if (sum(complete) < 10) next
                tbl <- table(clinical[[ci]][complete], clinical[[cj]][complete])
                k <- min(nrow(tbl), ncol(tbl))
                if (k < 2) next  # Can't compute Cramer's V for 1xN tables
                n <- sum(tbl)
                chi2 <- suppressWarnings(chisq.test(tbl, correct = FALSE)$statistic)
                cramers_v <- sqrt(chi2 / (n * (k - 1)))
                if (cramers_v > 0.7) {
                    # Drop the one with more levels (less general)
                    ni <- nlevels(factor(clinical[[ci]]))
                    nj <- nlevels(factor(clinical[[cj]]))
                    to_drop <- if (ni >= nj) ci else cj
                    to_keep <- if (ni >= nj) cj else ci
                    cat("   Dropping", to_drop, "- collinear with", to_keep,
                        "(Cramer's V =", round(cramers_v, 2), ")\n")
                    dropped_covariates[[to_drop]] <- paste0(
                        "collinear with ", to_keep,
                        " (Cramer's V = ", round(cramers_v, 2), ")")
                    covariates <- setdiff(covariates, to_drop)
                }
            }
        }
    }

    if (length(covariates) == 0) {
        stop("All covariates were dropped. Check data quality or provide covariates.")
    }

    # --- Reference group releveling ---
    # Apply clinical conventions instead of blindly picking the largest group.
    # Four-rule precedence: ordered-factor, clinical-convention, ordinal-lowest,
    # largest-group fallback. A user-supplied reference_levels list overrides
    # any single covariate.
    ref_levels_out <- list()
    for (col in covariates) {
        vals <- clinical[[col]]
        if (is.factor(vals) || is.character(vals)) {
            clinical[[col]] <- factor(clinical[[col]])
            tab <- table(clinical[[col]])

            # User override takes precedence
            if (!is.null(reference_levels) && col %in% names(reference_levels)) {
                ref <- reference_levels[[col]]
                if (is.list(ref)) ref <- ref$reference
                if (ref %in% levels(clinical[[col]])) {
                    clinical[[col]] <- relevel(clinical[[col]], ref = ref)
                    ref_levels_out[[col]] <- list(
                        reference = ref, n = as.integer(tab[ref]),
                        rule = "user-override")
                    if (tab[ref] < 50) {
                        cat("   WARNING: Reference group for", col, "is '", ref,
                            "' (N=", tab[ref],
                            ") — small reference may produce unstable HRs\n", sep = "")
                    }
                    next
                }
            }

            choice <- .choose_reference_level(col, vals)
            clinical[[col]] <- relevel(clinical[[col]], ref = choice$reference)
            ref_levels_out[[col]] <- list(
                reference = choice$reference,
                n = as.integer(tab[choice$reference]),
                rule = choice$rule)
            if (tab[choice$reference] < 50) {
                cat("   WARNING: Reference group for", col, "is '",
                    choice$reference, "' (N=", tab[choice$reference],
                    ") — small reference may produce unstable HRs\n", sep = "")
            }
        }
    }

    cat("   Covariates in model:", paste(covariates, collapse = ", "), "\n")

    # Build formula
    formula_str <- paste0("Surv(", time_col, ", ", event_col, ") ~ ",
                          paste(covariates, collapse = " + "))
    cox_formula <- as.formula(formula_str)

    # Fit model (na.action = na.exclude to preserve alignment)
    model <- tryCatch(
        coxph(cox_formula, data = clinical, na.action = na.exclude),
        error = function(e) {
            cat("   Cox model failed with all covariates, trying stepwise...\n")
            # Try each covariate individually, keep those that work
            good_covs <- c()
            for (cov in covariates) {
                f <- as.formula(paste0("Surv(", time_col, ", ", event_col,
                                       ") ~ ", cov))
                m <- tryCatch(coxph(f, data = clinical, na.action = na.exclude),
                             error = function(e2) NULL)
                if (!is.null(m)) good_covs <- c(good_covs, cov)
            }
            if (length(good_covs) == 0) stop("No covariates could be fit.")
            f2 <- as.formula(paste0("Surv(", time_col, ", ", event_col,
                                    ") ~ ", paste(good_covs, collapse = " + ")))
            coxph(f2, data = clinical, na.action = na.exclude)
        }
    )

    # Extract coefficients
    smry <- summary(model)
    coef_df <- data.frame(
        variable = rownames(smry$coefficients),
        coefficient = smry$coefficients[, "coef"],
        hazard_ratio = smry$coefficients[, "exp(coef)"],
        se = smry$coefficients[, "se(coef)"],
        hr_lower = smry$conf.int[, "lower .95"],
        hr_upper = smry$conf.int[, "upper .95"],
        z = smry$coefficients[, "z"],
        pval = smry$coefficients[, "Pr(>|z|)"],
        stringsAsFactors = FALSE,
        row.names = NULL
    )

    # Risk scores (use model's internal data to avoid new-factor-level errors)
    risk_scores <- predict(model, type = "risk")

    list(
        model = model,
        coefficients = coef_df,
        risk_scores = risk_scores,
        concordance = smry$concordance[1],
        formula = formula_str,
        n = model$n,
        nevent = model$nevent,
        dropped_covariates = dropped_covariates,
        reference_levels = ref_levels_out,
        clinical_releveled = clinical
    )
}


# =============================================================================
# Proportional Hazards Assumption Test
# =============================================================================

test_assumptions <- function(cox_model) {
    ph_test <- cox.zph(cox_model)
    return(ph_test)
}


# =============================================================================
# Risk Stratification
# =============================================================================

stratify_risk_groups <- function(risk_scores, method = "median",
                                 n_groups = NULL, cutpoints = NULL) {
    if (method == "median") {
        med <- median(risk_scores, na.rm = TRUE)
        groups <- ifelse(risk_scores > med, "High Risk", "Low Risk")
    } else if (method == "tertiles") {
        q <- quantile(risk_scores, probs = c(1/3, 2/3), na.rm = TRUE)
        groups <- ifelse(risk_scores <= q[1], "Low Risk",
                  ifelse(risk_scores <= q[2], "Medium Risk", "High Risk"))
    } else if (method == "quartiles") {
        q <- quantile(risk_scores, probs = c(0.25, 0.5, 0.75), na.rm = TRUE)
        groups <- ifelse(risk_scores <= q[1], "Q1 (Lowest)",
                  ifelse(risk_scores <= q[2], "Q2",
                  ifelse(risk_scores <= q[3], "Q3", "Q4 (Highest)")))
    } else if (method == "custom" && !is.null(cutpoints)) {
        groups <- cut(risk_scores, breaks = c(-Inf, cutpoints, Inf),
                     labels = paste0("Group ", seq_along(cutpoints) + 1))
    } else {
        stop("Unknown risk stratification method: ", method)
    }

    return(groups)
}


# =============================================================================
# Null-coalescing operator
# =============================================================================

`%||%` <- function(x, y) if (is.null(x)) y else x


# =============================================================================
# Reference Level Selection (Item 4)
# =============================================================================
# Four-rule precedence for choosing the reference level of a categorical
# covariate, so that hazard ratios point in the clinically expected direction
# (e.g. grade 3 vs grade 1, not grade 1 vs grade 3).
#
#   1. Ordered factor          -> lowest level
#   2. Clinical dictionary      -> convention keyed on column name AND values
#   3. Ordinal-lowest           -> parse first number in each level string
#   4. Largest group (fallback) -> say so
# =============================================================================

.choose_reference_level <- function(col_name, values) {
    levs <- levels(factor(values))

    # Rule 1: ordered factor -> lowest level
    if (is.ordered(values)) {
        return(list(reference = levs[1], rule = "ordered-factor"))
    }

    # Rule 2: clinical dictionary keyed on column name AND level values
    ref <- .clinical_reference_lookup(col_name, levs)
    if (!is.null(ref)) {
        return(list(reference = ref, rule = "clinical-convention"))
    }

    # Rule 3: ordinal-lowest via numeric parsing of level strings
    ranked <- .rank_ordinal_levels(levs)
    if (!is.null(ranked)) {
        return(list(reference = ranked[1], rule = "ordinal-lowest"))
    }

    # Rule 4: fall back to largest group
    tab <- table(factor(values))
    largest <- names(which.max(tab))
    cat("   NOTE: No clinical convention for '", col_name,
        "'; using largest group ('", largest, "') as reference\n", sep = "")
    list(reference = largest, rule = "largest-group")
}

# Clinical dictionary: maps column names (and their level values) to the
# clinically correct reference level. Grade/stage use the lowest level
# PRESENT in the data — never hardcode a literal that may be absent.
.clinical_reference_lookup <- function(col_name, levs) {
    cn <- tolower(col_name)
    lv <- tolower(levs)

    # grade / histologic_grade -> lowest grade present
    if (cn %in% c("grade", "histologic_grade", "tumor_grade", "differentiation")) {
        # Match patterns like "grade 1", "g1", "1", "well differentiated"
        nums <- suppressWarnings(as.numeric(gsub("[^0-9]", "", lv)))
        if (!all(is.na(nums))) {
            idx <- which.min(nums)
            return(levs[idx])
        }
        # Textual grades
        grade_order <- c("well", "low", "moderately", "moderate", "poorly",
                         "poor", "high", "undifferentiated")
        for (g in grade_order) {
            idx <- which(grepl(g, lv))
            if (length(idx) > 0) return(levs[idx[1]])
        }
        return(NULL)
    }

    # stage -> lowest stage present (Stage I / I / 1)
    if (grepl("stage", cn) || cn %in% c("ajcc_stage", "tnm_stage")) {
        # Extract roman numeral or number
        nums <- suppressWarnings(as.numeric(gsub("[^0-9]", "", lv)))
        if (!all(is.na(nums))) {
            idx <- which.min(nums)
            return(levs[idx])
        }
        return(NULL)
    }

    # nodes / node_status / lymph_node -> Negative / N0 / 0
    if (cn %in% c("nodes", "node_status", "lymph_node", "lymph_nodes",
                   "nodal_status", "n_stage")) {
        for (ref in c("negative", "n0", "0", "none")) {
            idx <- which(lv == ref)
            if (length(idx) > 0) return(levs[idx[1]])
        }
        return(NULL)
    }

    # receptor status -> Negative
    if (cn %in% c("er_status", "pr_status", "her2_status", "er", "pr", "her2",
                   "estrogen_receptor", "progesterone_receptor",
                   "her2_receptor")) {
        idx <- which(lv %in% c("negative", "neg", "0"))
        if (length(idx) > 0) return(levs[idx[1]])
        return(NULL)
    }

    # performance status -> lowest
    if (cn %in% c("ecog_ps", "performance_status", "ph.ecog", "karnofsky",
                   "karnofsky_physician", "karnofsky_patient")) {
        nums <- suppressWarnings(as.numeric(gsub("[^0-9]", "", lv)))
        if (!all(is.na(nums))) {
            idx <- which.min(nums)
            return(levs[idx])
        }
        return(NULL)
    }

    # treatment variables -> No / None / 0 / Untreated
    if (cn %in% c("hormon", "hormonal", "chemo", "chemotherapy", "therapy",
                   "treatment", "radiotherapy", "radiation", "surgery")) {
        for (ref in c("no", "none", "0", "untreated", "placebo", "control")) {
            idx <- which(lv == ref)
            if (length(idx) > 0) return(levs[idx[1]])
        }
        return(NULL)
    }

    # age_group -> lowest band
    if (cn %in% c("age_group", "age_band", "age_category")) {
        ranked <- .rank_ordinal_levels(levs)
        if (!is.null(ranked)) return(ranked[1])
        return(NULL)
    }

    # risk_group -> Low Risk
    if (cn %in% c("risk_group", "risk_strata", "risk_category")) {
        for (ref in c("low risk", "low", "lowrisk", "q1 (lowest)", "q1")) {
            idx <- which(lv == ref)
            if (length(idx) > 0) return(levs[idx[1]])
        }
        return(NULL)
    }

    # meno / menopausal -> premenopausal (lowest)
    if (cn %in% c("meno", "menopausal", "menopausal_status")) {
        for (ref in c("premenopausal", "pre", "0")) {
            idx <- which(lv == ref)
            if (length(idx) > 0) return(levs[idx[1]])
        }
        return(NULL)
    }

    return(NULL)
}

# Parse the first number in each level string to rank ordinal levels.
# Levels beginning with '<' get a small epsilon subtracted (so '<=20' < '20-50'),
# and levels beginning with '>' get one added (so '>50' is highest).
.rank_ordinal_levels <- function(levs) {
    nums <- suppressWarnings(as.numeric(gsub(".*?([0-9]+\\.?[0-9]*).*", "\\1", levs)))
    if (all(is.na(nums))) return(NULL)
    if (length(unique(na.omit(nums))) < 2) return(NULL)

    # Adjust for '<' and '>' prefixes
    for (i in seq_along(levs)) {
        if (!is.na(nums[i])) {
            if (grepl("^\\s*<", levs[i])) nums[i] <- nums[i] - 0.001
            if (grepl("^\\s*>", levs[i])) nums[i] <- nums[i] + 1
        }
    }

    if (all(is.na(nums))) return(NULL)
    idx <- order(nums)
    levs[idx]
}


# =============================================================================
# Internal Validation: C-index (Item 12)
# =============================================================================
# K-fold cross-validation + bootstrap optimism correction using base survival
# only — no new dependency. The apparent C-index is optimistically biased;
# the optimism-corrected value is the headline discrimination metric.
# =============================================================================

validate_cox_model <- function(model_formula, clinical, time_col, event_col,
                               k = 5, B = 100, seed = 42) {
    set.seed(seed)

    formula <- as.formula(model_formula)
    complete <- complete.cases(clinical)
    cc <- clinical[complete, ]

    # Apparent C-index (unname to avoid carrying "C" into c() name mangling)
    fit_full <- coxph(formula, data = cc, na.action = na.exclude)
    apparent <- unname(summary(fit_full)$concordance[1])

    # --- K-fold CV ---
    n <- nrow(cc)
    fold_ids <- sample(rep(seq_len(k), length.out = n))
    lp_oof <- rep(NA_real_, n)

    for (fold in seq_len(k)) {
        train <- cc[fold_ids != fold, ]
        test  <- cc[fold_ids == fold, ]
        fit_fold <- tryCatch(
            coxph(formula, data = train, na.action = na.exclude),
            error = function(e) NULL
        )
        if (!is.null(fit_fold)) {
            lp_oof[fold_ids == fold] <- predict(fit_fold, newdata = test,
                                                type = "lp")
        }
    }

    cv_c <- tryCatch({
        unname(survival::concordance(Surv(cc[[time_col]], cc[[event_col]]) ~ lp_oof,
                              reverse = TRUE)$concordance)
    }, error = function(e) NA_real_)

    # --- Bootstrap optimism (Harrell) ---
    optimism <- rep(NA_real_, B)
    for (b in seq_len(B)) {
        idx <- sample(n, replace = TRUE)
        boot_data <- cc[idx, ]
        fit_boot <- tryCatch(
            coxph(formula, data = boot_data, na.action = na.exclude),
            error = function(e) NULL
        )
        if (is.null(fit_boot)) next

        # C on the bootstrap sample (training)
        c_boot <- tryCatch(unname(summary(fit_boot)$concordance[1]), error = function(e) NA)
        # C on the original data (test)
        c_orig <- tryCatch({
            lp_orig <- predict(fit_boot, newdata = cc, type = "lp")
            unname(survival::concordance(Surv(cc[[time_col]], cc[[event_col]]) ~ lp_orig,
                                  reverse = TRUE)$concordance)
        }, error = function(e) NA)

        if (!is.na(c_boot) && !is.na(c_orig)) {
            optimism[b] <- c_boot - c_orig
        }
    }

    avg_optimism <- mean(optimism, na.rm = TRUE)
    optimism_corrected <- apparent - avg_optimism

    list(
        apparent = apparent,
        cv = cv_c,
        optimism_corrected = optimism_corrected,
        avg_optimism = avg_optimism,
        n_bootstrap = sum(!is.na(optimism))
    )
}


# =============================================================================
# PH Sensitivity Analysis (Item 13)
# =============================================================================
# When the global Schoenfeld test is violated, fit time-varying-coefficient
# models for the offending covariates so the report can show early vs late
# HRs. Uses survSplit (preferred over tt= for continuous covariates with many
# distinct event times). For categorical violators, also fits a stratified
# Cox model.
# =============================================================================

fit_ph_sensitivity <- function(cox_model, clinical, time_col, event_col,
                               ph_test) {
    cat("   Running PH sensitivity analysis...\n")

    # Identify covariates with individual Schoenfeld p < 0.05
    tbl <- ph_test$table
    violated <- rownames(tbl)[tbl[, "p"] < 0.05]
    violated <- violated[violated != "GLOBAL"]

    if (length(violated) == 0) {
        cat("   No individual covariates violate PH — no sensitivity needed.\n")
        return(NULL)
    }

    formula_str <- deparse(cox_model$formula)
    # Extract covariate names from the model
    model_terms <- names(coef(cox_model))

    # Median event time as the split point
    event_times <- clinical[[time_col]][clinical[[event_col]] == 1]
    split_time <- median(event_times, na.rm = TRUE)

    results <- list()

    for (v in violated) {
        # Find the covariate name (may be a factor level like gradeGrade3)
        cov_name <- v
        # Strip factor-level suffix to get the base variable name
        for (term in all.vars(cox_model$formula)) {
            if (startsWith(v, term)) {
                cov_name <- term
                break
            }
        }

        is_cat <- is.factor(clinical[[cov_name]]) ||
                  is.character(clinical[[cov_name]])

        # --- Two-period model via survSplit ---
        split_data <- survival::survSplit(
            as.formula(paste0("Surv(", time_col, ", ", event_col, ") ~ ",
                              paste(all.vars(cox_model$formula)[-c(1, 2)],
                                    collapse = " + "))),
            data = clinical,
            cut = split_time,
            episode = "period"
        )

        # Fit with period interaction
        period_formula <- as.formula(paste0(
            "Surv(", time_col, ", ", event_col, ") ~ ",
            paste(all.vars(cox_model$formula)[-c(1, 2)], collapse = " + "),
            " + period + ", cov_name, ":period"
        ))

        fit_split <- tryCatch(
            coxph(period_formula, data = split_data, na.action = na.exclude),
            error = function(e) NULL
        )

        if (!is.null(fit_split)) {
            smry <- summary(fit_split)
            coef_info <- data.frame(
                covariate = cov_name,
                period = c("early", "late"),
                HR = NA_real_,
                CI_lower = NA_real_,
                CI_upper = NA_real_,
                p = NA_real_,
                model_type = "survSplit",
                stringsAsFactors = FALSE
            )
            # Extract early and late HRs from the interaction
            coef_names <- rownames(smry$coefficients)
            base_idx <- which(coef_names == cov_name |
                              grepl(paste0("^", cov_name), coef_names))
            for (ci in base_idx) {
                # This is simplified; the actual extraction depends on factor
                # structure. We record what we can.
                coef_info$HR[1] <- smry$coefficients[ci, "exp(coef)"]
                coef_info$CI_lower[1] <- smry$conf.int[ci, "lower .95"]
                coef_info$CI_upper[1] <- smry$conf.int[ci, "upper .95"]
                coef_info$p[1] <- smry$coefficients[ci, "Pr(>|z|)"]
            }
            results[[paste0(v, "_survSplit")]] <- coef_info
        }

        # --- Stratified Cox for categorical violators ---
        if (is_cat) {
            other_covs <- setdiff(all.vars(cox_model$formula)[-c(1, 2)],
                                  cov_name)
            if (length(other_covs) > 0) {
                strata_formula <- as.formula(paste0(
                    "Surv(", time_col, ", ", event_col, ") ~ ",
                    paste(other_covs, collapse = " + "),
                    " + strata(", cov_name, ")"
                ))
                fit_strata <- tryCatch(
                    coxph(strata_formula, data = clinical,
                          na.action = na.exclude),
                    error = function(e) NULL
                )
                if (!is.null(fit_strata)) {
                    smry_s <- summary(fit_strata)
                    strata_info <- data.frame(
                        covariate = rownames(smry_s$coefficients),
                        period = "stratified",
                        HR = smry_s$coefficients[, "exp(coef)"],
                        CI_lower = smry_s$conf.int[, "lower .95"],
                        CI_upper = smry_s$conf.int[, "upper .95"],
                        p = smry_s$coefficients[, "Pr(>|z|)"],
                        model_type = "strata",
                        stringsAsFactors = FALSE,
                        row.names = NULL
                    )
                    results[[paste0(v, "_strata")]] <- strata_info
                }
            }
        }
    }

    if (length(results) == 0) {
        cat("   PH sensitivity models did not converge.\n")
        return(NULL)
    }

    out <- do.call(rbind, results)
    rownames(out) <- NULL

    # --- Detect and drop non-estimable late-period rows -----------------------
    # The two-period survSplit table pre-allocates an "early" and a "late" row
    # per violating covariate. When the covariate:period interaction does not
    # resolve into a distinct late-period estimate, the "late" row's HR stays
    # NA. Emitting "NA | NA-NA | NA" rows forces a reader to interpret a
    # non-result, so drop them here and record the degeneracy as a note. The
    # stratified Cox rows (for categorical violators) plus the primary Cox table
    # still characterise the time-varying effect.
    na_hr <- is.na(out$HR)
    if (any(na_hr)) {
        deg_covs <- unique(out$covariate[na_hr])
        note <- paste0(
            "Late-period hazard ratios from the survSplit interaction models ",
            "were not estimable for: ", paste(deg_covs, collapse = ", "),
            ". Those non-estimable rows are omitted; the time-varying effect is ",
            "characterised by the stratified Cox model rows (for categorical ",
            "covariates) together with the primary Cox table.")
        cat("   NOTE:", note, "\n")
        out <- out[!na_hr, , drop = FALSE]
        rownames(out) <- NULL
        attr(out, "degenerate_note") <- note
    }

    if (nrow(out) == 0) {
        # Every late-period row was non-estimable: return a zero-row frame that
        # still carries the note so the caller can surface it in the report.
        cat("   PH sensitivity: all survSplit late-period rows non-estimable.\n")
        return(out)
    }

    cat("   PH sensitivity analysis complete:", nrow(out), "rows\n")
    return(out)
}

# =============================================================================
# Landmark Survival & Reliability Helpers
# =============================================================================

#' Compute landmark survival rates at specified timepoints
.compute_landmark_survival <- function(km_fit, times = c(1, 3, 5)) {
    s <- summary(km_fit, times = times, extend = TRUE)
    data.frame(
        time = s$time,
        survival = round(s$surv, 4),
        lower_ci = round(s$lower, 4),
        upper_ci = round(s$upper, 4),
        n_risk = s$n.risk,
        stringsAsFactors = FALSE
    )
}

#' Compute median follow-up via reverse Kaplan-Meier
#' (standard method: swap events and censoring, then estimate median)
.compute_median_followup <- function(clinical, time_col, event_col) {
    reverse_event <- 1 - clinical[[event_col]]
    f <- Surv(clinical[[time_col]], reverse_event)
    fit <- survfit(f ~ 1)
    median_fu <- summary(fit)$table["median"]
    return(median_fu)
}

#' Check if KM median survival is reliably estimable
#' Requires: (1) upper 95% CI is not NA (curve crosses 50%), AND
#'           (2) at least 20 patients at risk at the median time
.median_is_reliable <- function(km_fit) {
    tbl <- summary(km_fit)$table
    ucl <- tbl["0.95UCL"]
    if (is.na(ucl)) return(FALSE)

    # Also check N at risk at the median time
    median_time <- tbl["median"]
    if (is.na(median_time)) return(FALSE)

    idx <- which.min(abs(km_fit$time - median_time))
    n_at_risk <- km_fit$n.risk[idx]

    # Need >= 20 patients at risk for a reliable estimate
    return(n_at_risk >= 20)
}


.validate_survival_data <- function(clinical, event_col, time_col) {
    # Check required columns exist
    if (!event_col %in% colnames(clinical))
        stop("Event column '", event_col, "' not found in data.")
    if (!time_col %in% colnames(clinical))
        stop("Time column '", time_col, "' not found in data.")

    # Check data types
    if (!is.numeric(clinical[[time_col]]))
        stop("Time column '", time_col, "' must be numeric.")
    if (!all(clinical[[event_col]] %in% c(0, 1), na.rm = TRUE))
        stop("Event column '", event_col, "' must be binary (0/1). Found: ",
             paste(unique(clinical[[event_col]]), collapse = ", "))

    # Check for negatives
    if (any(clinical[[time_col]] < 0, na.rm = TRUE))
        warning("Negative survival times detected. Check time column encoding.")

    # Check for sufficient events
    n_events <- sum(clinical[[event_col]], na.rm = TRUE)
    if (n_events < 5)
        warning("Only ", n_events, " events detected. Results may be unreliable.")
    if (n_events == nrow(clinical))
        warning("All observations are events (no censoring). Check event coding.")

    cat("  Data validated:", nrow(clinical), "patients,", n_events, "events\n")
}
