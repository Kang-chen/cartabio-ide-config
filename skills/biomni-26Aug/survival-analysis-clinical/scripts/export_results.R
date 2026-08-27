# =============================================================================
# Export Survival Analysis Results
# =============================================================================
# Exports all results: CSVs, RDS objects, summary tables, and PDF report.
#
# Usage:
#   source("scripts/export_results.R")
#   export_all(result, output_dir = "results")
# =============================================================================

export_all <- function(result, output_dir = "results") {
    cat("\n=== Exporting Survival Analysis Results ===\n\n")

    if (!dir.exists(output_dir)) dir.create(output_dir, recursive = TRUE)

    # --- 1. Cox model coefficients ---
    cat("1. Cox model coefficients...\n")
    coef_out <- result$cox$coefficients
    # Remove reference level rows (NA coefficients) for clean output
    coef_out <- coef_out[!is.na(coef_out$pval), ]
    write.csv(coef_out,
              file.path(output_dir, "cox_coefficients.csv"),
              row.names = FALSE)
    cat("   Saved: cox_coefficients.csv\n\n")

    # --- 2. Patient risk scores ---
    cat("2. Patient risk scores...\n")
    scores_df <- data.frame(
        sample_id = result$clinical$sample_id,
        risk_score = result$cox$risk_scores,
        risk_group = result$clinical[[result$risk_col]],
        stringsAsFactors = FALSE
    )
    write.csv(scores_df, file.path(output_dir, "risk_scores.csv"),
              row.names = FALSE)
    cat("   Saved: risk_scores.csv\n\n")

    # --- 3. Clinical data with risk groups ---
    cat("3. Annotated clinical data...\n")
    write.csv(result$clinical,
              file.path(output_dir, "clinical_annotated.csv"),
              row.names = FALSE)
    cat("   Saved: clinical_annotated.csv\n\n")

    # --- 4. Survival summary table ---
    cat("4. Survival summary statistics...\n")
    summary_df <- .build_summary_table(result)
    write.csv(summary_df, file.path(output_dir, "survival_summary.csv"),
              row.names = FALSE)
    cat("   Saved: survival_summary.csv\n\n")

    # --- 5. PH assumption test ---
    cat("5. Proportional hazards test results...\n")
    ph_df <- data.frame(
        variable = rownames(result$ph_test$table),
        chisq = result$ph_test$table[, "chisq"],
        df = result$ph_test$table[, "df"],
        p = result$ph_test$table[, "p"],
        stringsAsFactors = FALSE,
        row.names = NULL
    )
    write.csv(ph_df, file.path(output_dir, "ph_assumption_test.csv"),
              row.names = FALSE)
    cat("   Saved: ph_assumption_test.csv\n\n")

    # --- 6. Analysis object (RDS) - CRITICAL for downstream skills ---
    cat("6. Saving analysis object (RDS)...\n")
    saveRDS(result, file.path(output_dir, "survival_model.rds"))
    cat("   Saved: survival_model.rds\n")
    cat("   (Load with: model <- readRDS('results/survival_model.rds'))\n\n")

    # --- 6b. Key metrics (single-row headline table) ---
    cat("6b. Key metrics table...\n")
    key_metrics <- .build_key_metrics(result)
    write.csv(key_metrics, file.path(output_dir, "key_metrics.csv"),
              row.names = FALSE)
    cat("   Saved: key_metrics.csv\n\n")

    # --- 6c. Reference levels ---
    cat("6c. Reference levels...\n")
    ref_df <- .build_reference_levels_table(result)
    write.csv(ref_df, file.path(output_dir, "reference_levels.csv"),
              row.names = FALSE)
    cat("   Saved: reference_levels.csv\n\n")

    # --- 6d. Missingness assessment ---
    cat("6d. Missingness assessment...\n")
    miss_df <- .build_missingness_table(result)
    write.csv(miss_df, file.path(output_dir, "missingness_assessment.csv"),
              row.names = FALSE)
    cat("   Saved: missingness_assessment.csv\n\n")

    # --- 6e. PH sensitivity (conditional) ---
    if (!is.null(result$ph_sensitivity)) {
        cat("6e. PH sensitivity analysis...\n")
        write.csv(result$ph_sensitivity,
                  file.path(output_dir, "ph_sensitivity.csv"),
                  row.names = FALSE)
        cat("   Saved: ph_sensitivity.csv\n\n")
    }

    # --- 7. Markdown report ---
    cat("7. Generating markdown report...\n")
    md_content <- .build_markdown_report(result, output_dir)
    writeLines(md_content, file.path(output_dir, "survival_report.md"))
    cat("   Saved: survival_report.md\n\n")

    # --- 8. Consistency check (before the verification token) ---
    .assert_export_consistency(result, output_dir)

    cat("\n=== Export Complete ===\n")
    cat("\nFiles in", output_dir, ":\n")
    files <- list.files(output_dir, recursive = FALSE)
    for (f in files) {
        size <- file.info(file.path(output_dir, f))$size
        cat("  ", f, "(", .format_size(size), ")\n")
    }
}


# =============================================================================
# Helpers
# =============================================================================

.build_summary_table <- function(result) {
    clinical <- result$clinical
    risk_col <- result$risk_col
    time_col <- result$time_col
    event_col <- result$event_col

    groups <- unique(clinical[[risk_col]])
    groups <- groups[!is.na(groups)]

    rows <- lapply(groups, function(g) {
        subset <- clinical[clinical[[risk_col]] == g & !is.na(clinical[[risk_col]]), ]
        f <- as.formula(paste0("Surv(", time_col, ", ", event_col, ") ~ 1"))
        fit <- survival::survfit(f, data = subset)
        tbl <- summary(fit)$table
        median_surv <- tbl["median"]
        lcl <- tbl["0.95LCL"]
        ucl <- tbl["0.95UCL"]
        median_reliable <- !is.na(ucl)

        data.frame(
            group = g,
            n = nrow(subset),
            events = sum(subset[[event_col]], na.rm = TRUE),
            event_rate = round(mean(subset[[event_col]], na.rm = TRUE), 3),
            median_survival = if (median_reliable) round(median_surv, 2) else NA_real_,
            median_lower_ci = round(lcl, 2),
            median_upper_ci = round(ucl, 2),
            median_reliable = median_reliable,
            stringsAsFactors = FALSE
        )
    })

    do.call(rbind, rows)
}

.build_markdown_report <- function(result, output_dir = NULL) {
    ctx <- result$report_context
    coef <- result$cox$coefficients
    # Filter out NA rows (reference factor levels)
    coef <- coef[!is.na(coef$pval), ]
    median_reliable <- isTRUE(result$median_reliable)
    endpoint_label <- result$endpoint_label %||% "Overall Survival (OS)"

    # C-index: show all three, headline is optimism-corrected
    c_idx <- result$c_index
    c_apparent <- if (!is.null(c_idx)) c_idx["apparent"] else result$concordance
    c_cv <- if (!is.null(c_idx)) c_idx["cv"] else NA
    c_opt <- if (!is.null(c_idx)) c_idx["optimism_corrected"] else NA

    lines <- c(
        paste("#", result$dataset_name, "-", endpoint_label, "Report"),
        "",
        paste("**Date:**", Sys.Date()),
        paste("**Endpoint:**", endpoint_label),
        "",
        "**KM cohort:**",
        paste("- N:", result$n_total),
        paste("- Events:", result$n_events, "(",
              round(100 * result$n_events / result$n_total, 1), "%)"),
        "",
        "**Cox analysis set:**",
        paste("- N:", result$cox$n),
        paste("- Events:", result$cox$nevent),
        if (!is.null(result$n_excluded) && result$n_excluded > 0)
            paste("- Excluded:", result$n_excluded, "(",
                  round(100 * result$n_excluded / result$n_total, 1),
                  "%) due to missing covariates")
        else
            "- Excluded: 0",
        if (isTRUE(result$complete_case_warning))
            "- **WARNING: >20% excluded — complete-case analysis subject to selection bias**"
        else NULL,
        "",
        paste("**C-index (apparent):**", round(c_apparent, 3)),
        if (!is.na(c_cv))
            paste("**C-index (5-fold CV):**", round(c_cv, 3))
        else NULL,
        if (!is.na(c_opt))
            paste("**C-index (optimism-corrected):**", round(c_opt, 3),
                  "  **(headline)**")
        else NULL,
        if (median_reliable)
            paste("**Median survival:**", round(result$median_survival, 2), "years")
        else
            "**Median survival:** Not reached (KM curve does not cross 50%)",
        if (!is.null(result$median_followup) && !is.na(result$median_followup))
            paste("**Median follow-up (reverse KM):**",
                  round(result$median_followup, 2), "years")
        else NULL,
        if (!is.null(result$epv))
            paste("**Events per variable (EPV):**", round(result$epv, 1),
                  if (result$epv < 10) "**(low — model may be overfitted)**" else "(adequate)")
        else NULL,
        ""
    )

    # Add landmark survival table
    if (!is.null(result$landmark_survival)) {
        ls <- result$landmark_survival
        lines <- c(lines,
            "### Landmark Survival Rates",
            "",
            "| Timepoint | Survival | 95% CI | N at Risk |",
            "|-----------|----------|--------|----------|"
        )
        for (i in seq_len(nrow(ls))) {
            lines <- c(lines, sprintf("| %g-year | %.1f%% | %.1f%%-%.1f%% | %d |",
                ls$time[i], 100 * ls$survival[i],
                100 * ls$lower_ci[i], 100 * ls$upper_ci[i], ls$n_risk[i]))
        }
        lines <- c(lines, "")
    }

    lines <- c(lines,
        "## Methods",
        "",
        paste("- **Disease:**", ctx$disease %||% "Not specified"),
        paste("- **Data source:**", ctx$source %||% "Not specified"),
        paste("- **Endpoint:**", result$endpoint_label %||%
              (ctx$endpoints %||% "Overall survival")),
        if (!is.null(result$endpoint_convention) &&
            !is.na(result$endpoint_convention))
            paste("  - Convention:", result$endpoint_convention,
                  "(", result$n_convention_affected,
                  "subjects affected by convention choice)")
        else NULL,
        paste("- **Cox model covariates:**",
              paste(names(coef(result$cox$model)), collapse = ", ")),
        paste("- **Risk stratification:**", result$risk_strata_method, "split")
    )

    # Report dropped covariates
    if (length(result$dropped_covariates) > 0) {
        lines <- c(lines,
            paste("- **Dropped covariates:**",
                  paste(names(result$dropped_covariates), collapse = ", ")))
        for (nm in names(result$dropped_covariates)) {
            lines <- c(lines, paste("  -", nm, ":", result$dropped_covariates[[nm]]))
        }
    }

    # Report reference levels
    if (length(result$reference_levels) > 0) {
        ref_strs <- vapply(names(result$reference_levels), function(nm) {
            rl <- result$reference_levels[[nm]]
            paste0(nm, " = ", rl$reference, " (N=", rl$n, ")")
        }, character(1))
        lines <- c(lines,
            paste("- **Reference groups:**", paste(ref_strs, collapse = "; ")))
    }

    # Report informative missingness
    if (!is.null(result$diagnostics$missing_assessment)) {
        informative <- Filter(function(x) isTRUE(x$informative),
                              result$diagnostics$missing_assessment)
        if (length(informative) > 0) {
            lines <- c(lines,
                "- **⚠️ Informative missingness detected:**")
            for (nm in names(informative)) {
                ma <- informative[[nm]]
                lines <- c(lines, sprintf(
                    "  - %s: %d missing (%.1f%%), event rate %.1f%% (missing) vs %.1f%% (non-missing), Fisher p=%s",
                    nm, ma$n_missing, ma$pct_missing,
                    100 * ma$event_rate_missing, 100 * ma$event_rate_nonmissing,
                    format.pval(ma$fisher_p, digits = 3)))
            }
        }
    }

    # Report follow-up anomaly
    if (isTRUE(result$diagnostics$followup_anomaly)) {
        lines <- c(lines, paste0(
            "- **⚠️ Follow-up anomaly:** Median follow-up (",
            round(result$median_followup, 2),
            " yr) is very short relative to max observation time (",
            round(result$diagnostics$max_obs_time, 1),
            " yr). May indicate missing follow-up data for censored patients."))
    }

    lines <- c(lines, "",
        "## Cox Proportional Hazards Results",
        "",
        "| Variable | HR | 95% CI | p-value |",
        "|----------|---:|-------:|--------:|"
    )

    for (i in seq_len(nrow(coef))) {
        lines <- c(lines, paste0(
            "| ", coef$variable[i],
            " | ", sprintf("%.2f", coef$hazard_ratio[i]),
            " | ", sprintf("%.2f", coef$hr_lower[i]),
            "-", sprintf("%.2f", coef$hr_upper[i]),
            " | ", format.pval(coef$pval[i], digits = 3), " |"
        ))
    }

    # PH sensitivity analysis (sensitivity, not replacement).
    # Non-estimable late-period rows are dropped upstream; render only the
    # estimable rows and state any degeneracy explicitly instead of emitting NA.
    has_ps_rows <- !is.null(result$ph_sensitivity) && nrow(result$ph_sensitivity) > 0
    if (has_ps_rows || !is.null(result$ph_sensitivity_note)) {
        lines <- c(lines, "",
            "### Sensitivity Analysis: Time-Varying Coefficients",
            "",
            "The global PH test is violated. The following sensitivity models",
            "fit early vs late hazard ratios via `survSplit` and stratified",
            "Cox models. These are sensitivity analyses, not replacements for",
            "the primary Cox table."
        )
        if (has_ps_rows) {
            lines <- c(lines, "",
                "| Covariate | Period | HR | 95% CI | p | Model |",
                "|-----------|--------|---:|-------:|--:|-------|"
            )
            ps <- result$ph_sensitivity
            for (i in seq_len(nrow(ps))) {
                # Non-estimable rows are dropped upstream; guard defensively.
                if (is.na(ps$HR[i])) next
                lines <- c(lines, sprintf(
                    "| %s | %s | %.2f | %.2f-%.2f | %s | %s |",
                    ps$covariate[i], ps$period[i],
                    ps$HR[i], ps$CI_lower[i], ps$CI_upper[i],
                    format.pval(ps$p[i], digits = 3), ps$model_type[i]))
            }
        }
        if (!is.null(result$ph_sensitivity_note)) {
            lines <- c(lines, "",
                paste0("> **Note on non-estimable late-period effects:** ",
                       result$ph_sensitivity_note))
        }
    }

    lines <- c(lines, "",
        "## Proportional Hazards Assumption",
        "")

    global_p <- result$ph_test$table["GLOBAL", "p"]
    if (global_p < 0.05) {
        lines <- c(lines,
            paste("**WARNING:** Global PH test p =", format.pval(global_p, digits = 3),
                  "- proportional hazards assumption may be violated."),
            "Consider time-varying coefficients or stratified Cox model.")
    } else {
        lines <- c(lines,
            paste("PH assumption satisfied (global p =",
                  format.pval(global_p, digits = 3), ")"))
    }

    # --- Generated Files: derived from actual output_dir contents ---
    lines <- c(lines, "",
        "## Generated Files",
        ""
    )

    if (!is.null(output_dir) && dir.exists(output_dir)) {
        actual_files <- list.files(output_dir, recursive = FALSE)
        file_desc <- .file_description_map()
        lines <- c(lines, "| File | Description |", "|------|------------|")
        for (f in actual_files) {
            desc <- file_desc[[f]]
            if (is.null(desc)) desc <- "(exported artifact)"
            lines <- c(lines, paste0("| ", f, " | ", desc, " |"))
        }
        # Note any expected artifacts that are absent
        expected <- c("cox_coefficients.csv", "risk_scores.csv",
                       "clinical_annotated.csv", "survival_summary.csv",
                       "ph_assumption_test.csv", "survival_model.rds",
                       "key_metrics.csv", "reference_levels.csv",
                       "missingness_assessment.csv",
                       "km_overall.png", "forest_plot.png",
                       "schoenfeld_diagnostics.png", "cumulative_hazard.png")
        missing <- setdiff(expected, actual_files)
        if (length(missing) > 0) {
            lines <- c(lines, "",
                "**Not generated:**")
            for (m in missing) {
                lines <- c(lines, paste("- `", m, "` (not generated)", sep = ""))
            }
        }
    } else {
        lines <- c(lines,
            "(Output directory not available at report build time)")
    }

    # --- Citation: derive methods from actual package availability ---
    has_survminer <- requireNamespace("survminer", quietly = TRUE)
    has_ggprism <- requireNamespace("ggprism", quietly = TRUE)
    methods_str <- "Cox PH (survival R package)"
    if (has_survminer) methods_str <- paste(methods_str, ", KM estimation (survminer)", sep = "")
    viz_str <- "ggplot2"
    if (has_ggprism) viz_str <- paste(viz_str, "+ ggprism theme", sep = " ")

    lines <- c(lines, "",
        "## Citation",
        "",
        paste("- **Data:**", ctx$citation %||% "User-provided data"),
        paste("- **Methods:**", methods_str),
        paste("- **Visualization:**", viz_str)
    )

    paste(lines, collapse = "\n")
}

.format_size <- function(bytes) {
    if (is.na(bytes)) return("?")
    if (bytes < 1024) return(paste(bytes, "B"))
    if (bytes < 1024^2) return(paste(round(bytes / 1024, 1), "KB"))
    return(paste(round(bytes / 1024^2, 1), "MB"))
}


# =============================================================================
# Key Metrics Table (Item 9)
# =============================================================================
# Single-row table containing every headline number, read from the result
# object — never recomputed. This is the canonical source for the report,
# infographic, and tables.
# =============================================================================

.build_key_metrics <- function(result) {
    c_idx <- result$c_index
    c_apparent <- if (!is.null(c_idx)) unname(c_idx["apparent"]) else result$concordance
    c_cv <- if (!is.null(c_idx)) unname(c_idx["cv"]) else NA_real_
    c_opt <- if (!is.null(c_idx)) unname(c_idx["optimism_corrected"]) else NA_real_

    global_p <- result$ph_test$table["GLOBAL", "p"]
    ph_violated <- !is.na(global_p) && global_p < 0.05

    # Risk group log-rank
    risk_lr <- result$risk_logrank
    risk_chisq <- if (!is.null(risk_lr)) risk_lr$chisq else NA
    risk_p <- if (!is.null(risk_lr))
        1 - pchisq(risk_lr$chisq, length(risk_lr$n) - 1) else NA

    # Strata log-rank
    strata_lr <- result$strata_logrank
    strata_chisq <- if (!is.null(strata_lr)) strata_lr$chisq else NA
    strata_p <- if (!is.null(strata_lr))
        1 - pchisq(strata_lr$chisq, length(strata_lr$n) - 1) else NA

    # Landmark columns (one per timepoint)
    landmark_cols <- list()
    if (!is.null(result$landmark_survival)) {
        ls <- result$landmark_survival
        for (i in seq_len(nrow(ls))) {
            colname <- paste0("landmark_", ls$time[i], "yr_survival")
            landmark_cols[[colname]] <- ls$survival[i]
        }
    }

    metrics <- data.frame(
        dataset_name = result$dataset_name,
        endpoint_code = result$endpoint_code %||% "OS",
        endpoint_label = result$endpoint_label %||% "Overall Survival (OS)",
        endpoint_convention = result$endpoint_convention %||% NA_character_,
        n_convention_affected = result$n_convention_affected %||% NA_integer_,
        n_total = result$n_total,
        n_events = result$n_events,
        event_rate = round(result$n_events / result$n_total, 4),
        n_cox = result$cox$n,
        n_events_cox = result$cox$nevent,
        n_excluded = result$n_excluded %||% 0,
        epv = result$epv %||% NA,
        c_index_apparent = c_apparent,
        c_index_cv = c_cv,
        c_index_optimism_corrected = c_opt,
        median_survival = if (isTRUE(result$median_reliable))
            result$median_survival else NA_real_,
        median_reliable = isTRUE(result$median_reliable),
        median_followup = result$median_followup %||% NA,
        ph_global_p = global_p,
        ph_violated = ph_violated,
        risk_strata_method = result$risk_strata_method,
        risk_logrank_chisq = risk_chisq,
        risk_logrank_p = risk_p,
        strata_col = result$strata_col %||% NA_character_,
        strata_logrank_chisq = strata_chisq,
        strata_logrank_p = strata_p,
        n_dropped_covariates = length(result$dropped_covariates),
        complete_case_warning = isTRUE(result$complete_case_warning),
        stringsAsFactors = FALSE
    )

    # Add landmark columns
    for (colname in names(landmark_cols)) {
        metrics[[colname]] <- landmark_cols[[colname]]
    }

    metrics
}


# =============================================================================
# Reference Levels Table (Item 9)
# =============================================================================

.build_reference_levels_table <- function(result) {
    refs <- result$reference_levels
    if (length(refs) == 0) {
        return(data.frame(covariate = character(0), reference = character(0),
                          n = integer(0), rule = character(0),
                          stringsAsFactors = FALSE))
    }
    rows <- lapply(names(refs), function(nm) {
        rl <- refs[[nm]]
        data.frame(
            covariate = nm,
            reference = rl$reference,
            n = rl$n,
            rule = rl$rule %||% "unknown",
            stringsAsFactors = FALSE
        )
    })
    do.call(rbind, rows)
}


# =============================================================================
# Missingness Assessment Table (Item 9)
# =============================================================================

.build_missingness_table <- function(result) {
    ma <- result$diagnostics$missing_assessment
    if (length(ma) == 0) {
        return(data.frame(covariate = character(0), n_missing = integer(0),
                          pct_missing = numeric(0),
                          event_rate_missing = numeric(0),
                          event_rate_nonmissing = numeric(0),
                          fisher_p = numeric(0),
                          informative = logical(0),
                          stringsAsFactors = FALSE))
    }
    rows <- lapply(names(ma), function(nm) {
        m <- ma[[nm]]
        data.frame(
            covariate = nm,
            n_missing = m$n_missing,
            pct_missing = m$pct_missing,
            event_rate_missing = m$event_rate_missing,
            event_rate_nonmissing = m$event_rate_nonmissing,
            fisher_p = m$fisher_p,
            informative = m$informative,
            stringsAsFactors = FALSE
        )
    })
    do.call(rbind, rows)
}


# =============================================================================
# File Description Map (Item 8)
# =============================================================================

.file_description_map <- function() {
    list(
        "cox_coefficients.csv" = "Hazard ratios with CIs and p-values",
        "risk_scores.csv" = "Patient risk scores and group assignments",
        "clinical_annotated.csv" = "Full clinical data with risk groups",
        "survival_summary.csv" = "Summary statistics by risk group",
        "ph_assumption_test.csv" = "Schoenfeld residual test results",
        "survival_model.rds" = "Complete analysis object for downstream use",
        "key_metrics.csv" = "Single-row headline metrics table",
        "reference_levels.csv" = "Covariate reference levels and selection rules",
        "missingness_assessment.csv" = "Missingness and informative-missingness test",
        "ph_sensitivity.csv" = "PH sensitivity analysis (time-varying coefficients)",
        "survival_report.md" = "Comprehensive markdown report",
        "km_overall.png" = "Overall Kaplan-Meier survival curve (PNG)",
        "km_overall.svg" = "Overall Kaplan-Meier survival curve (SVG)",
        "km_stratified.png" = "Stratified survival curves (PNG)",
        "km_stratified.svg" = "Stratified survival curves (SVG)",
        "forest_plot.png" = "Forest plot of hazard ratios (PNG)",
        "forest_plot.svg" = "Forest plot of hazard ratios (SVG)",
        "km_risk_groups.png" = "Risk group survival curves (PNG)",
        "km_risk_groups.svg" = "Risk group survival curves (SVG)",
        "schoenfeld_diagnostics.png" = "PH assumption diagnostic plots (PNG)",
        "schoenfeld_diagnostics.svg" = "PH assumption diagnostic plots (SVG)",
        "cumulative_hazard.png" = "Cumulative hazard plot (PNG)",
        "cumulative_hazard.svg" = "Cumulative hazard plot (SVG)"
    )
}


# =============================================================================
# Export Consistency Gate (Item 10)
# =============================================================================
# Called at the end of export_all(), BEFORE the "=== Export Complete ===" line.
# Hard stop() on row-count invariants (code bugs); loud WARN for soft checks.
# =============================================================================

.assert_export_consistency <- function(result, output_dir) {
    cat("\n=== Consistency Check ===\n")

    checks_passed <- 0
    checks_total <- 6
    hard_failures <- c()

    # (1) nrow(cox_coefficients.csv) == length(coef(result$cox$model))
    cox_csv <- tryCatch(
        read.csv(file.path(output_dir, "cox_coefficients.csv")),
        error = function(e) NULL)
    n_coef_model <- length(coef(result$cox$model))
    if (!is.null(cox_csv)) {
        if (nrow(cox_csv) == n_coef_model) {
            checks_passed <- checks_passed + 1
            cat("  [1] cox_coefficients rows == model coefficients: PASS\n")
        } else {
            hard_failures <- c(hard_failures,
                paste0("[1] cox_coefficients.csv has ", nrow(cox_csv),
                       " rows but model has ", n_coef_model, " coefficients"))
            cat("  [1] cox_coefficients rows == model coefficients: FAIL\n")
        }
    } else {
        hard_failures <- c(hard_failures, "[1] cox_coefficients.csv not readable")
        cat("  [1] cox_coefficients rows == model coefficients: FAIL (unreadable)\n")
    }

    # (2) nrow(risk_scores.csv) == nrow(clinical_annotated.csv) == result$n_total
    risk_csv <- tryCatch(
        read.csv(file.path(output_dir, "risk_scores.csv")),
        error = function(e) NULL)
    clin_csv <- tryCatch(
        read.csv(file.path(output_dir, "clinical_annotated.csv")),
        error = function(e) NULL)
    n_risk <- if (!is.null(risk_csv)) nrow(risk_csv) else NA
    n_clin <- if (!is.null(clin_csv)) nrow(clin_csv) else NA
    if (!is.na(n_risk) && !is.na(n_clin) &&
        n_risk == result$n_total && n_clin == result$n_total) {
        checks_passed <- checks_passed + 1
        cat("  [2] risk_scores == clinical_annotated == n_total: PASS\n")
    } else {
        hard_failures <- c(hard_failures,
            paste0("[2] row counts: risk_scores=", n_risk,
                   " clinical_annotated=", n_clin,
                   " n_total=", result$n_total))
        cat("  [2] risk_scores == clinical_annotated == n_total: FAIL\n")
    }

    # (3) sum(survival_summary.csv$n) == non-NA risk-group assignments
    #     sum(events) == events among them
    summ_csv <- tryCatch(
        read.csv(file.path(output_dir, "survival_summary.csv")),
        error = function(e) NULL)
    if (!is.null(summ_csv)) {
        risk_col <- result$risk_col
        non_na <- sum(!is.na(result$clinical[[risk_col]]))
        events_in_groups <- sum(result$clinical[[result$event_col]][
            !is.na(result$clinical[[risk_col]])])
        if (sum(summ_csv$n) == non_na && sum(summ_csv$events) == events_in_groups) {
            checks_passed <- checks_passed + 1
            cat("  [3] survival_summary N/events == risk group assignments: PASS\n")
        } else {
            hard_failures <- c(hard_failures,
                paste0("[3] survival_summary: sum(n)=", sum(summ_csv$n),
                       " vs non-NA=", non_na,
                       "; sum(events)=", sum(summ_csv$events),
                       " vs events=", events_in_groups))
            cat("  [3] survival_summary N/events == risk group assignments: FAIL\n")
        }
    } else {
        hard_failures <- c(hard_failures, "[3] survival_summary.csv not readable")
        cat("  [3] survival_summary N/events == risk group assignments: FAIL\n")
    }

    # (4) GLOBAL p in ph_assumption_test.csv == result$ph_test$table["GLOBAL","p"] to 6dp
    ph_csv <- tryCatch(
        read.csv(file.path(output_dir, "ph_assumption_test.csv")),
        error = function(e) NULL)
    if (!is.null(ph_csv)) {
        csv_global_p <- ph_csv$p[ph_csv$variable == "GLOBAL"]
        model_global_p <- result$ph_test$table["GLOBAL", "p"]
        if (!is.na(csv_global_p) && !is.na(model_global_p) &&
            round(csv_global_p, 6) == round(model_global_p, 6)) {
            checks_passed <- checks_passed + 1
            cat("  [4] PH GLOBAL p matches to 6dp: PASS\n")
        } else {
            cat("  [4] PH GLOBAL p matches to 6dp: WARN",
                "(csv=", csv_global_p, " model=", model_global_p, ")\n")
        }
    } else {
        cat("  [4] PH GLOBAL p matches to 6dp: WARN (ph_assumption_test.csv unreadable)\n")
    }

    # (5) Every filename referenced in survival_report.md exists in output_dir
    md_text <- tryCatch(
        readLines(file.path(output_dir, "survival_report.md")),
        error = function(e) "")
    actual_files <- list.files(output_dir, recursive = FALSE)
    # Extract filenames mentioned in the report
    referenced <- unique(regmatches(
        md_text,
        regexpr("[a-zA-Z0-9_]+\\.(csv|png|svg|rds|md)", md_text)))
    missing_refs <- setdiff(referenced, actual_files)
    if (length(missing_refs) == 0) {
        checks_passed <- checks_passed + 1
        cat("  [5] All report-referenced files exist: PASS\n")
    } else {
        cat("  [5] All report-referenced files exist: WARN (missing:",
            paste(missing_refs, collapse = ", "), ")\n")
    }

    # (6) key_metrics.csv$c_index_apparent == result$cox$concordance
    km_csv <- tryCatch(
        read.csv(file.path(output_dir, "key_metrics.csv")),
        error = function(e) NULL)
    if (!is.null(km_csv)) {
        km_c <- km_csv$c_index_apparent
        model_c <- result$cox$concordance
        if (!is.na(km_c) && !is.na(model_c) &&
            round(km_c, 6) == round(model_c, 6)) {
            checks_passed <- checks_passed + 1
            cat("  [6] key_metrics c_index_apparent == model concordance: PASS\n")
        } else {
            cat("  [6] key_metrics c_index_apparent == model concordance: WARN",
                "(key_metrics=", km_c, " model=", model_c, ")\n")
        }
    } else {
        cat("  [6] key_metrics c_index_apparent == model concordance: WARN (unreadable)\n")
    }

    # Report result
    if (length(hard_failures) > 0) {
        cat("\n  HARD FAILURES (code bugs):\n")
        for (hf in hard_failures) cat("   ", hf, "\n")
        stop("Export consistency check FAILED on row-count invariants. ",
             "This indicates a code bug, not a data issue.")
    }

    cat("\n=== Consistency check: PASSED (", checks_passed, "/", checks_total,
        ") ===\n", sep = "")
}


# Null-coalescing operator
`%||%` <- function(x, y) if (is.null(x)) y else x
