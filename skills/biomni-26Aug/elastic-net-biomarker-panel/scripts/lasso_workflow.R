# Elastic-Net Biomarker Panel Discovery Workflow
# Core pipeline: nested CV LASSO with stability selection
# Reference: Ali et al., Nature Medicine 2025 (github.com/NeuroGenomicsAndInformatics/NatMed_2025_GNPC)
#
# Pipeline:
#   1. Repeated nested CV (50 iterations, 70/30 class-balanced splits)
#   2. Inner CV (cv.glmnet, 10-fold) for lambda selection per iteration
#   3. Feature tracking across iterations
#   4. Stability selection (features in >80% of iterations)
#   5. Final model on full discovery data with stable features
#   6. Per-fold AUC estimation via pROC
#   7. Locked-panel cross-validation (selection-biased estimate, flagged in report)

# Required packages
library(glmnet)
library(pROC)

#' Run elastic-net biomarker panel selection with stability analysis
#'
#' Implements the Ali et al. (Nat Med 2025) pipeline:
#' - Repeated 70/30 train/test splits with class balancing
#' - cv.glmnet for optimal lambda per split
#' - Feature selection frequency tracking
#' - Stability selection (features in > threshold fraction of splits)
#' - Final locked model for clinical use
#' - Locked-panel cross-validated AUC (selection-biased; flagged in report)
#'
#' Preprocessing provenance: when prepare_feature_matrix() sets the
#' 'prep_params' attribute on X, this function records whether the
#' candidate feature pool was fixed on the whole cohort upstream
#' (leakage) or is filtered fold-internally (no leakage). The derived
#' leakage_status string is written to parameters.csv by export_all()
#' and printed verbatim in the report — never hand-authored.
#'
#' @param X Feature matrix (samples x features). May carry a 'prep_params'
#'   attribute from prepare_feature_matrix().
#' @param y Binary outcome vector (0/1)
#' @param n_repeats Number of repeated CV iterations (default: 50)
#' @param train_fraction Fraction of data for training (default: 0.7)
#' @param alpha Elastic net mixing parameter: 1=LASSO, 0=Ridge (default: 1.0)
#' @param n_inner_folds Folds for inner CV lambda selection (default: 10)
#' @param stability_threshold Min selection frequency for stable features (default: 0.8)
#' @param top_n_features Max features to select per iteration (default: NULL = all non-zero)
#' @param preprocess_per_fold If TRUE, apply variance filtering and z-scaling
#'   inside each training fold only (leakage-free). If FALSE (default), X is
#'   used as-is — the candidate pool was fixed upstream and leakage_status
#'   will reflect that.
#' @param top_n_variable Number of most variable features to retain per fold
#'   when preprocess_per_fold=TRUE (default: NULL = no variance filter)
#' @param scale_features If TRUE, z-scale features within each training fold
#'   when preprocess_per_fold=TRUE (default: FALSE)
#' @param seed Random seed for reproducibility (default: 42)
#'
#' @return list with final_model, stable_features, feature_importance,
#'   fold_aucs, locked_panel_cv, parameters (including leakage_status), etc.
run_lasso_panel <- function(X, y,
                             n_repeats = 50,
                             train_fraction = 0.7,
                             alpha = 1.0,
                             n_inner_folds = 10,
                             stability_threshold = 0.8,
                             top_n_features = NULL,
                             preprocess_per_fold = FALSE,
                             top_n_variable = NULL,
                             scale_features = FALSE,
                             seed = 42) {

    cat("\n=== Elastic-Net Biomarker Panel Selection ===\n\n")
    cat("Parameters:\n")
    cat("  Samples:", nrow(X), "| Features:", ncol(X), "\n")
    cat("  Outcome: ", sum(y == 1), "positives,", sum(y == 0), "negatives\n")
    cat("  Repeats:", n_repeats, "| Train fraction:", train_fraction, "\n")
    cat("  Alpha:", alpha, ifelse(alpha == 1, "(pure LASSO)", "(elastic net)"), "\n")
    cat("  Inner CV folds:", n_inner_folds, "\n")
    cat("  Stability threshold:", stability_threshold, "\n")
    cat("  Preprocess per fold:", preprocess_per_fold, "\n")
    cat("  Top N variable (per fold):",
        if (is.null(top_n_variable)) "NULL (no filter)" else top_n_variable, "\n")
    cat("  Scale features (per fold):", scale_features, "\n\n")

    # ---- Read preprocessing provenance from the 'prep_params' attribute ----
    # prepare_feature_matrix() sets this; it survives positional passing.
    pp0 <- attr(X, 'prep_params')
    global_variance_filter_n <- if (!is.null(pp0)) pp0$global_variance_filter_n else NA
    global_scaling <- if (!is.null(pp0)) pp0$global_scaling else NA
    global_log_transformed <- if (!is.null(pp0)) pp0$log_transformed else NA
    n_input_features <- if (!is.null(pp0)) pp0$n_input_features else NA

    # ---- Detect whether the per-fold variance filter is actually active ----
    fold_variance_filter_active <- isTRUE(preprocess_per_fold) &&
        !is.null(top_n_variable) && ncol(X) > top_n_variable

    # ---- Loud warning when the per-fold variance filter is a silent no-op ----
    if (isTRUE(preprocess_per_fold) && !is.null(top_n_variable) &&
        ncol(X) <= top_n_variable) {
        cat("\n")
        cat("============================================================\n")
        cat("WARNING: PER-FOLD VARIANCE FILTER IS INACTIVE\n")
        cat("  preprocess_per_fold = TRUE but ncol(X) =", ncol(X),
            "<= top_n_variable =", top_n_variable, "\n")
        cat("  The per-fold variance filter will NOT remove any features.\n")
        cat("  If the candidate pool was already fixed upstream (e.g. by\n")
        cat("  prepare_feature_matrix with top_n_variable), the feature\n")
        cat("  selection pool is NOT fold-internal — leakage_status will\n")
        cat("  reflect this.\n")
        cat("============================================================\n\n")
    }

    # ---- Derive leakage_status (never upgrade to the clean claim on missing info) ----
    # When pp0 is NULL we have no upstream provenance — must NOT claim clean.
    if (is.null(pp0)) {
        leakage_status <- paste(
            "unknown upstream preprocessing -- leakage status not established")
    } else if (isTRUE(preprocess_per_fold) && fold_variance_filter_active &&
               isTRUE(scale_features)) {
        leakage_status <- paste(
            "fold-internal preprocessing: variance filter and z-scaling",
            "computed from training rows only")
    } else if (!is.na(global_variance_filter_n)) {
        # A global variance filter was applied upstream on the whole cohort
        leakage_status <- paste(
            "PARTIAL: the candidate feature pool was fixed on the whole",
            "cohort upstream (top N by variance); only z-scaling is",
            "fold-internal")
    } else if (isTRUE(global_scaling)) {
        # Global scaling applied upstream but no variance filter
        leakage_status <- "whole-cohort preprocessing (legacy path)"
    } else {
        # pp0 exists but no global filter or scaling — upstream was benign
        leakage_status <- "whole-cohort preprocessing (legacy path)"
    }

    cat("  Leakage status:", leakage_status, "\n\n")

    set.seed(seed)
    feature_names <- colnames(X)
    n_samples <- nrow(X)
    n_train <- round(n_samples * train_fraction)

    # Track feature selection across iterations
    selection_count <- rep(0, ncol(X))
    names(selection_count) <- feature_names

    # Track coefficients across iterations
    coef_matrix <- matrix(0, nrow = n_repeats, ncol = ncol(X))
    colnames(coef_matrix) <- feature_names

    # Track per-fold performance
    fold_aucs <- numeric(n_repeats)
    fold_sensitivities <- numeric(n_repeats)
    fold_specificities <- numeric(n_repeats)
    all_predictions <- data.frame()

    cat("Running", n_repeats, "iterations...\n")
    pb_interval <- max(1, n_repeats %/% 10)

    for (i in seq_len(n_repeats)) {
        if (i %% pb_interval == 0 || i == 1) {
            cat("  Iteration", i, "/", n_repeats, "...")
        }

        # Class-balanced train/test split
        idx_pos <- which(y == 1)
        idx_neg <- which(y == 0)
        n_train_pos <- round(length(idx_pos) * train_fraction)
        n_train_neg <- round(length(idx_neg) * train_fraction)

        train_pos <- sample(idx_pos, n_train_pos)
        train_neg <- sample(idx_neg, n_train_neg)
        train_idx <- c(train_pos, train_neg)
        test_idx <- setdiff(seq_len(n_samples), train_idx)

        X_train <- X[train_idx, , drop = FALSE]
        y_train <- y[train_idx]
        X_test <- X[test_idx, , drop = FALSE]
        y_test <- y[test_idx]

        # ---- Per-fold preprocessing (leakage-free path) ----
        if (isTRUE(preprocess_per_fold)) {
            # Variance filter on training rows only
            if (!is.null(top_n_variable) && ncol(X_train) > top_n_variable) {
                train_vars <- apply(X_train, 2, var, na.rm = TRUE)
                top_idx <- order(train_vars, decreasing = TRUE)[1:top_n_variable]
                X_train <- X_train[, top_idx, drop = FALSE]
                X_test <- X_test[, top_idx, drop = FALSE]
            }
            # Z-scaling from training rows only
            if (scale_features) {
                train_center <- colMeans(X_train, na.rm = TRUE)
                train_sd <- apply(X_train, 2, sd, na.rm = TRUE)
                train_sd[train_sd == 0 | is.na(train_sd)] <- 1
                X_train <- sweep(sweep(X_train, 2, train_center, "-"), 2, train_sd, "/")
                X_test <- sweep(sweep(X_test, 2, train_center, "-"), 2, train_sd, "/")
                X_train[is.nan(X_train)] <- 0
                X_test[is.nan(X_test)] <- 0
            }
        }

        # Inner CV for lambda selection
        cv_fit <- tryCatch({
            cv.glmnet(
                x = X_train,
                y = y_train,
                family = "binomial",
                alpha = alpha,
                nfolds = min(n_inner_folds, length(y_train)),
                type.measure = "auc",
                standardize = FALSE  # Already scaled or unscaled by design
            )
        }, error = function(e) {
            cat(" (cv.glmnet error in fold", i, ":", conditionMessage(e), ")\n")
            return(NULL)
        })

        if (is.null(cv_fit)) {
            fold_aucs[i] <- NA
            next
        }

        # Extract selected features at lambda.min
        coefs <- as.vector(coef(cv_fit, s = "lambda.min"))[-1]  # Remove intercept
        names(coefs) <- colnames(X_train)
        selected <- which(coefs != 0)

        if (length(selected) > 0) {
            # Track selection (map back to original feature names)
            sel_names <- names(coefs)[selected]
            selection_count[sel_names] <- selection_count[sel_names] + 1

            # Track coefficients
            coef_matrix[i, sel_names] <- coefs[selected]

            # Limit to top N if requested
            if (!is.null(top_n_features) && length(selected) > top_n_features) {
                top_idx <- order(abs(coefs[selected]), decreasing = TRUE)[1:top_n_features]
                selected <- selected[top_idx]
            }
        }

        # Predict on test set
        pred_probs <- as.vector(predict(cv_fit, newx = X_test,
                                         s = "lambda.min", type = "response"))

        # Calculate AUC
        if (length(unique(y_test)) == 2) {
            roc_obj <- tryCatch(
                roc(y_test, pred_probs, quiet = TRUE),
                error = function(e) NULL
            )
            if (!is.null(roc_obj)) {
                fold_aucs[i] <- auc(roc_obj)
                # Sensitivity/specificity at optimal threshold (Youden)
                coords <- coords(roc_obj, "best", ret = c("sensitivity", "specificity"))
                fold_sensitivities[i] <- coords$sensitivity[1]
                fold_specificities[i] <- coords$specificity[1]
            }
        }

        # Store predictions
        fold_preds <- data.frame(
            sample_id = rownames(X_test),
            true_label = y_test,
            predicted_prob = pred_probs,
            fold = i,
            stringsAsFactors = FALSE
        )
        all_predictions <- rbind(all_predictions, fold_preds)

        if (i %% pb_interval == 0 || i == 1) {
            cat(" AUC =", round(fold_aucs[i], 3),
                "| Selected:", length(selected), "features\n")
        }
    }

    # ---- Stability Selection ----
    cat("\n--- Stability Selection ---\n")
    valid_folds <- sum(!is.na(fold_aucs))
    selection_freq <- selection_count / valid_folds
    stable_features <- names(selection_freq[selection_freq >= stability_threshold])

    cat("  Valid iterations:", valid_folds, "/", n_repeats, "\n")
    cat("  Features selected at least once:", sum(selection_count > 0), "\n")
    cat("  Stable features (>", stability_threshold * 100, "%):",
        length(stable_features), "\n")

    if (length(stable_features) < 2) {
        if (length(stable_features) == 0) {
            cat("  WARNING: No features passed stability threshold.\n")
        } else {
            cat("  WARNING: Only", length(stable_features), "feature passed threshold.\n")
        }
        cat("  Relaxing to top 10 most frequently selected features.\n")
        top_10 <- names(sort(selection_freq, decreasing = TRUE))[1:min(10, length(selection_freq))]
        stable_features <- top_10[selection_freq[top_10] > 0]
        # Ensure at least 2 features for glmnet
        if (length(stable_features) < 2) {
            stable_features <- names(sort(selection_freq, decreasing = TRUE))[1:2]
        }
        cat("  Using", length(stable_features), "features (frequency:",
            round(min(selection_freq[stable_features]), 3), "-",
            round(max(selection_freq[stable_features]), 3), ")\n")
    }

    cat("  Stable feature panel:\n")
    for (feat in stable_features) {
        cat("    -", feat, "(selected in",
            round(selection_freq[feat] * 100, 1), "% of folds)\n")
    }

    # ---- Feature Importance Table ----
    feature_importance <- data.frame(
        feature = feature_names,
        selection_frequency = selection_freq,
        mean_coefficient = colMeans(coef_matrix, na.rm = TRUE),
        sd_coefficient = apply(coef_matrix, 2, sd, na.rm = TRUE),
        is_stable = feature_names %in% stable_features,
        stringsAsFactors = FALSE
    )
    feature_importance <- feature_importance[order(-feature_importance$selection_frequency), ]

    # ---- Final Model ----
    cat("\n--- Final Model (locked panel) ---\n")

    final_model <- NULL
    if (length(stable_features) > 0) {
        # Fit final model using only stable features on full discovery data
        X_stable <- X[, stable_features, drop = FALSE]

        final_cv <- cv.glmnet(
            x = X_stable,
            y = y,
            family = "binomial",
            alpha = alpha,
            nfolds = min(n_inner_folds, length(y)),
            type.measure = "auc",
            standardize = FALSE
        )

        final_model <- final_cv

        # Final model AUC on full data (training AUC — for reference only)
        final_preds <- as.vector(predict(final_cv, newx = X_stable,
                                          s = "lambda.min", type = "response"))
        final_roc <- roc(y, final_preds, quiet = TRUE)
        cat("  Final model AUC (training, for reference):", round(auc(final_roc), 3), "\n")
        cat("  Note: Use CV AUC below for unbiased performance estimate\n")
    } else {
        cat("  WARNING: No stable features found. Final model not created.\n")
    }

    # ---- Locked-Panel Cross-Validation ----
    # Score the stable-feature panel in the same fold structure used for
    # selection. This is a SELECTION-BIASED estimate: the panel features were
    # chosen using all n_repeats folds, so re-scoring them in those same folds
    # re-uses selection information. The report must flag this (spec item 5).
    locked_panel_cv <- NULL
    if (length(stable_features) >= 2) {
        cat("\n--- Locked-Panel Cross-Validation ---\n")
        cat("  (Selection-biased: panel chosen using all folds, then re-scored)\n")

        lp_fold_aucs <- numeric(n_repeats)
        lp_fold_sensitivities <- numeric(n_repeats)
        lp_fold_specificities <- numeric(n_repeats)
        lp_all_predictions <- data.frame()

        # Re-use the same seed for the same fold structure
        set.seed(seed)

        for (i in seq_len(n_repeats)) {
            # Same class-balanced split as the main loop
            idx_pos <- which(y == 1)
            idx_neg <- which(y == 0)
            n_train_pos <- round(length(idx_pos) * train_fraction)
            n_train_neg <- round(length(idx_neg) * train_fraction)

            train_pos <- sample(idx_pos, n_train_pos)
            train_neg <- sample(idx_neg, n_train_neg)
            train_idx <- c(train_pos, train_neg)
            test_idx <- setdiff(seq_len(n_samples), train_idx)

            X_train_lp <- X[train_idx, stable_features, drop = FALSE]
            y_train_lp <- y[train_idx]
            X_test_lp <- X[test_idx, stable_features, drop = FALSE]
            y_test_lp <- y[test_idx]

            # Per-fold preprocessing for the locked panel (same logic as main)
            if (isTRUE(preprocess_per_fold) && scale_features) {
                train_center <- colMeans(X_train_lp, na.rm = TRUE)
                train_sd <- apply(X_train_lp, 2, sd, na.rm = TRUE)
                train_sd[train_sd == 0 | is.na(train_sd)] <- 1
                X_train_lp <- sweep(sweep(X_train_lp, 2, train_center, "-"),
                                    2, train_sd, "/")
                X_test_lp <- sweep(sweep(X_test_lp, 2, train_center, "-"),
                                   2, train_sd, "/")
                X_train_lp[is.nan(X_train_lp)] <- 0
                X_test_lp[is.nan(X_test_lp)] <- 0
            }

            lp_fit <- tryCatch({
                cv.glmnet(
                    x = X_train_lp,
                    y = y_train_lp,
                    family = "binomial",
                    alpha = alpha,
                    nfolds = min(n_inner_folds, length(y_train_lp)),
                    type.measure = "auc",
                    standardize = FALSE
                )
            }, error = function(e) NULL)

            if (is.null(lp_fit)) {
                lp_fold_aucs[i] <- NA
                next
            }

            lp_preds <- as.vector(predict(lp_fit, newx = X_test_lp,
                                           s = "lambda.min", type = "response"))

            if (length(unique(y_test_lp)) == 2) {
                lp_roc <- tryCatch(
                    roc(y_test_lp, lp_preds, quiet = TRUE),
                    error = function(e) NULL
                )
                if (!is.null(lp_roc)) {
                    lp_fold_aucs[i] <- auc(lp_roc)
                    lp_coords <- coords(lp_roc, "best",
                                        ret = c("sensitivity", "specificity"))
                    lp_fold_sensitivities[i] <- lp_coords$sensitivity[1]
                    lp_fold_specificities[i] <- lp_coords$specificity[1]
                }
            }

            lp_fold_preds <- data.frame(
                sample_id = rownames(X_test_lp),
                true_label = y_test_lp,
                predicted_prob = lp_preds,
                fold = i,
                stringsAsFactors = FALSE
            )
            lp_all_predictions <- rbind(lp_all_predictions, lp_fold_preds)
        }

        lp_valid_aucs <- lp_fold_aucs[!is.na(lp_fold_aucs)]
        lp_mean_auc <- mean(lp_valid_aucs)

        locked_panel_cv <- list(
            mean_auc = lp_mean_auc,
            fold_aucs = lp_fold_aucs,
            fold_sensitivities = lp_fold_sensitivities,
            fold_specificities = lp_fold_specificities,
            cv_predictions = lp_all_predictions,
            panel_features = stable_features
        )

        cat("  Locked-panel mean AUC:", round(lp_mean_auc, 3),
            "(selection-biased)\n")
        cat("  Full-model mean AUC:  ", round(mean(fold_aucs[!is.na(fold_aucs)]), 3), "\n")
    }

    # ---- Summary Statistics ----
    valid_aucs <- fold_aucs[!is.na(fold_aucs)]
    mean_auc <- mean(valid_aucs)
    auc_ci <- quantile(valid_aucs, c(0.025, 0.975))

    cat("\n--- Cross-Validation Performance ---\n")
    cat("  Mean AUC:", round(mean_auc, 3),
        "(95% CI:", round(auc_ci[1], 3), "-", round(auc_ci[2], 3), ")\n")
    cat("  Mean Sensitivity:", round(mean(fold_sensitivities, na.rm = TRUE), 3), "\n")
    cat("  Mean Specificity:", round(mean(fold_specificities, na.rm = TRUE), 3), "\n")

    # ---- Compile Results ----
    result <- list(
        final_model = final_model,
        stable_features = stable_features,
        feature_importance = feature_importance,
        fold_aucs = fold_aucs,
        mean_auc = mean_auc,
        auc_ci = auc_ci,
        fold_sensitivities = fold_sensitivities,
        fold_specificities = fold_specificities,
        cv_predictions = all_predictions,
        selection_frequencies = selection_freq,
        coef_matrix = coef_matrix,
        locked_panel_cv = locked_panel_cv,
        parameters = list(
            n_repeats = n_repeats,
            train_fraction = train_fraction,
            alpha = alpha,
            n_inner_folds = n_inner_folds,
            stability_threshold = stability_threshold,
            seed = seed,
            n_samples = nrow(X),
            n_features = ncol(X),
            n_stable = length(stable_features),
            preprocess_per_fold = preprocess_per_fold,
            top_n_variable = if (is.null(top_n_variable)) NA else top_n_variable,
            scale_features = scale_features,
            fold_variance_filter_active = fold_variance_filter_active,
            global_variance_filter_n = global_variance_filter_n,
            global_variance_filter_applied = !is.na(global_variance_filter_n),
            global_scaling = global_scaling,
            leakage_status = leakage_status
        )
    )

    cat("\n\u2713 LASSO panel selection completed successfully!\n")
    cat("  Panel size:", length(stable_features), "features\n")
    cat("  CV AUC:", round(mean_auc, 3), "\n")
    if (!is.null(locked_panel_cv)) {
        cat("  Locked-panel CV AUC:", round(locked_panel_cv$mean_auc, 3),
            "(selection-biased)\n")
    }

    return(result)
}

#' Apply locked biomarker panel to new data
#'
#' @param model_result Result from run_lasso_panel()
#' @param new_X New feature matrix (samples x features)
#'
#' @return Predicted probabilities
predict_biomarker_panel <- function(model_result, new_X) {
    if (is.null(model_result$final_model)) {
        stop("No final model available. Panel selection may have failed.")
    }

    # Subset to stable features
    stable <- model_result$stable_features
    available <- intersect(stable, colnames(new_X))

    if (length(available) == 0) {
        stop("None of the panel features found in new data.")
    }

    if (length(available) < length(stable)) {
        cat("WARNING:", length(stable) - length(available),
            "panel features missing from new data.\n")
        cat("  Available:", length(available), "/", length(stable), "\n")
    }

    # Predict
    preds <- as.vector(predict(
        model_result$final_model,
        newx = new_X[, available, drop = FALSE],
        s = "lambda.min",
        type = "response"
    ))

    names(preds) <- rownames(new_X)
    return(preds)
}
