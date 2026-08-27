# =============================================================================
# 03_longitudinal_lme.R
# Longitudinal mixed-effects modelling for serum proteomics
#
# Purpose:
#   Fit per-protein linear mixed-effects models to detect proteins with
#   significant time × response_group interaction — the statistical signature
#   of "psychological reactogenicity" (T2 surge in responders, absent in
#   non-responders). Random intercept per patient accounts for repeated measures.
#
# Model:
#   intensity ~ time * response_group + [covariates] + (1 | patient_id)
#   Fitted with nlme::lme(); time and response_group are treated as factors.
#
# Inputs:
#   intensity_matrix  - data.frame, rows = proteins, cols = sample_ids (log2)
#   metadata          - data.frame: sample_id, patient_id, timepoint,
#                       response_group [+ optional covariate columns]
#   params            - list of analysis parameters
#
# Outputs:
#   lme_results.csv   - per-protein interaction p-value, trajectory type
#   Returns:          - list with $lme_table
# =============================================================================

suppressPackageStartupMessages({
  library(nlme)
  library(dplyr)
  library(tidyr)
})

# -----------------------------------------------------------------------------
# Main function
# -----------------------------------------------------------------------------

run_longitudinal_lme <- function(
    intensity_matrix,
    metadata,
    params = list()
) {
  p <- modifyList(
    list(
      timepoints          = c("T1", "T2", "T3"),
      timepoint_col       = "timepoint",
      response_col        = "response_group",
      sample_col          = "sample_id",
      patient_col         = "patient_id",
      covariates          = c(),            # e.g. c("age", "sex", "baseline_PASI")
      ref_timepoint       = "T1",           # reference level for time factor
      ref_group           = NULL,           # reference level for response_group
      lme_interaction_p   = 0.05,
      min_obs_per_protein = 10,             # min non-NA observations to fit model
      t2_surge_group      = NULL,           # group expected to show T2 surge
      log2_transform      = TRUE
    ),
    params
  )

  cat("=== Longitudinal Mixed-Effects Modelling ===\n")
  cat(sprintf("Timepoints: %s\n", paste(p$timepoints, collapse = " → ")))
  cat(sprintf("Interaction p threshold: %.3f\n", p$lme_interaction_p))

  # --- Subset metadata to relevant timepoints ---
  meta_long <- metadata %>%
    filter(.data[[p$timepoint_col]] %in% p$timepoints)

  long_samples <- intersect(meta_long[[p$sample_col]], colnames(intensity_matrix))
  mat_long <- intensity_matrix[, long_samples, drop = FALSE]
  meta_long <- meta_long[meta_long[[p$sample_col]] %in% long_samples, ]

  # --- Optional log2 transform ---
  if (p$log2_transform) {
    med_val <- median(as.matrix(mat_long), na.rm = TRUE)
    if (med_val > 100) {
      mat_long <- log2(mat_long + 1)
    }
  }

  # --- Set factor levels ---
  meta_long[[p$timepoint_col]] <- factor(
    meta_long[[p$timepoint_col]],
    levels = p$timepoints
  )
  if (!is.null(p$ref_timepoint)) {
    meta_long[[p$timepoint_col]] <- relevel(
      meta_long[[p$timepoint_col]], ref = p$ref_timepoint
    )
  }

  meta_long[[p$response_col]] <- factor(meta_long[[p$response_col]])
  if (!is.null(p$ref_group)) {
    meta_long[[p$response_col]] <- relevel(
      meta_long[[p$response_col]], ref = p$ref_group
    )
  }

  cat(sprintf("Total observations: %d\n", nrow(meta_long)))
  cat(sprintf("Proteins to model: %d\n", nrow(mat_long)))

  # --- Build covariate formula string ---
  cov_str <- if (length(p$covariates) > 0) {
    paste("+", paste(p$covariates, collapse = " + "))
  } else ""

  fixed_formula <- as.formula(
    sprintf("intensity ~ %s * %s %s",
            p$timepoint_col, p$response_col, cov_str)
  )
  random_formula <- as.formula(sprintf("~ 1 | %s", p$patient_col))

  cat(sprintf("Fixed formula: %s\n", deparse(fixed_formula)))

  # --- Fit per-protein ---
  lme_rows <- lapply(rownames(mat_long), function(prot) {
    # Build long-format data for this protein
    prot_vals <- as.numeric(mat_long[prot, ])
    names(prot_vals) <- colnames(mat_long)

    df_prot <- meta_long %>%
      mutate(intensity = prot_vals[.data[[p$sample_col]]]) %>%
      filter(!is.na(intensity))

    # Add covariate columns if needed
    for (cov in p$covariates) {
      if (!cov %in% colnames(df_prot)) {
        return(.lme_na_row(prot, "missing_covariate"))
      }
    }

    if (nrow(df_prot) < p$min_obs_per_protein) {
      return(.lme_na_row(prot, "too_few_obs"))
    }

    # Check each patient has ≥2 timepoints (needed for random intercept)
    pts_ok <- df_prot %>%
      group_by(.data[[p$patient_col]]) %>%
      summarise(n_tp = n(), .groups = "drop") %>%
      filter(n_tp >= 2)

    if (nrow(pts_ok) < 3) {
      return(.lme_na_row(prot, "too_few_repeated_patients"))
    }

    df_prot <- df_prot %>%
      filter(.data[[p$patient_col]] %in% pts_ok[[p$patient_col]])

    # Fit LME
    fit <- tryCatch(
      nlme::lme(
        fixed  = fixed_formula,
        random = random_formula,
        data   = df_prot,
        method = "ML",
        control = lmeControl(opt = "optim", maxIter = 100, msMaxIter = 100)
      ),
      error   = function(e) NULL,
      warning = function(w) tryCatch(
        suppressWarnings(nlme::lme(
          fixed  = fixed_formula,
          random = random_formula,
          data   = df_prot,
          method = "ML",
          control = lmeControl(opt = "optim")
        )),
        error = function(e2) NULL
      )
    )

    if (is.null(fit)) return(.lme_na_row(prot, "model_failed"))

    # Extract interaction term p-value
    coef_tbl <- tryCatch(summary(fit)$tTable, error = function(e) NULL)
    if (is.null(coef_tbl)) return(.lme_na_row(prot, "summary_failed"))

    interaction_rows <- grep(":", rownames(coef_tbl), value = TRUE)
    if (length(interaction_rows) == 0) return(.lme_na_row(prot, "no_interaction_term"))

    # Take minimum p across all interaction terms (most significant)
    interaction_p <- min(coef_tbl[interaction_rows, "p-value"], na.rm = TRUE)

    # Extract T2 coefficient in the surge group (if specified)
    t2_coef <- NA_real_
    if (!is.null(p$t2_surge_group)) {
      t2_term <- grep(
        paste0(p$timepoint_col, "T2.*", p$t2_surge_group, "|",
               p$t2_surge_group, ".*", p$timepoint_col, "T2"),
        rownames(coef_tbl), value = TRUE
      )
      if (length(t2_term) > 0) {
        t2_coef <- coef_tbl[t2_term[1], "Value"]
      }
    }

    data.frame(
      protein         = prot,
      interaction_p   = interaction_p,
      interaction_adj_p = NA_real_,
      t2_coef_surge   = t2_coef,
      n_obs           = nrow(df_prot),
      n_patients      = nrow(pts_ok),
      model_status    = "converged",
      trajectory_type = NA_character_,
      stringsAsFactors = FALSE
    )
  })

  lme_table <- bind_rows(lme_rows)

  # --- BH correction ---
  testable <- lme_table$model_status == "converged" & !is.na(lme_table$interaction_p)
  lme_table$interaction_adj_p[testable] <- p.adjust(
    lme_table$interaction_p[testable], method = "BH"
  )

  # --- Flag significant interaction ---
  lme_table$sig_interaction <- !is.na(lme_table$interaction_adj_p) &
    lme_table$interaction_adj_p < p$lme_interaction_p

  # --- Classify trajectory type ---
  lme_table$trajectory_type <- classify_trajectory(
    lme_table,
    t2_surge_group = p$t2_surge_group,
    interaction_p_thresh = p$lme_interaction_p
  )

  # --- Summary ---
  n_sig <- sum(lme_table$sig_interaction, na.rm = TRUE)
  n_surge <- sum(lme_table$trajectory_type == "T2_surge", na.rm = TRUE)
  n_failed <- sum(lme_table$model_status != "converged")

  cat(sprintf("\nSignificant time×group interaction: %d proteins\n", n_sig))
  cat(sprintf("T2-surge pattern: %d proteins\n", n_surge))
  cat(sprintf("Model failures: %d\n", n_failed))

  if (n_sig > 0) {
    cat("\nTop interaction proteins:\n")
    top <- lme_table %>%
      filter(sig_interaction) %>%
      arrange(interaction_p) %>%
      head(10)
    print(top[, c("protein","interaction_p","interaction_adj_p","trajectory_type")],
          row.names = FALSE)
  }

  cat("\n✓ Longitudinal LME complete.\n")
  list(lme_table = lme_table)
}


# -----------------------------------------------------------------------------
# Helper: NA row for failed models
# -----------------------------------------------------------------------------

.lme_na_row <- function(prot, status) {
  data.frame(
    protein           = prot,
    interaction_p     = NA_real_,
    interaction_adj_p = NA_real_,
    t2_coef_surge     = NA_real_,
    n_obs             = NA_integer_,
    n_patients        = NA_integer_,
    model_status      = status,
    trajectory_type   = NA_character_,
    stringsAsFactors  = FALSE
  )
}


# -----------------------------------------------------------------------------
# Helper: classify trajectory type from LME results
# -----------------------------------------------------------------------------

classify_trajectory <- function(lme_table, t2_surge_group = NULL,
                                 interaction_p_thresh = 0.05) {
  sapply(seq_len(nrow(lme_table)), function(i) {
    row <- lme_table[i, ]
    if (is.na(row$interaction_p) || row$model_status != "converged") {
      return(NA_character_)
    }
    if (!row$sig_interaction) return("stable_no_interaction")

    # T2 surge: significant interaction + positive T2 coefficient in surge group
    if (!is.null(t2_surge_group) && !is.na(row$t2_coef_surge)) {
      if (row$t2_coef_surge > 0) return("T2_surge")
      if (row$t2_coef_surge < 0) return("T2_dip")
    }

    "significant_interaction_unclassified"
  })
}
