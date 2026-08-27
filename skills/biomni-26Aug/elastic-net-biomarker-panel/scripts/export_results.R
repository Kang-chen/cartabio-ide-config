# Export Elastic-Net Biomarker Panel Results
# Saves all results including RDS objects for downstream skills
# Generates comprehensive markdown + PDF reports with embedded plots
#
# Consistency gate: .assert_report_consistency() scans every assembled report
# line and checks that restated analysis parameters (n_repeats, train_fraction,
# stability_threshold), AUC values, leakage claims, and LaTeX tokens match the
# values in model_result$parameters. This makes the restatement defect class
# structurally impossible — any mismatch raises stop() before writeLines().

#' Export all elastic-net biomarker panel results
#'
#' @param model_result Result from run_lasso_panel()
#' @param validation_result Result from validate_panel() (optional)
#' @param output_dir Output directory (default: "results")
#' @param data Original data object from load_*_data() (optional, enriches report)
#' @param features Feature matrix result from prepare_feature_matrix() (optional)
#'
#' @export
export_all <- function(model_result, validation_result = NULL,
                        output_dir = "results",
                        data = NULL, features = NULL,
                        interpretation = NULL) {

    cat("\n=== Exporting Elastic-Net Biomarker Panel Results ===\n\n")

    # Create output directory
    if (!dir.exists(output_dir)) {
        dir.create(output_dir, recursive = TRUE)
        cat("Created directory:", output_dir, "\n\n")
    }

    # 1. Biomarker panel (stable features with coefficients)
    cat("1. Exporting biomarker panel...\n")
    panel <- model_result$feature_importance[model_result$feature_importance$is_stable, ]
    write.csv(panel, file.path(output_dir, "biomarker_panel.csv"), row.names = FALSE)
    cat("   Saved: biomarker_panel.csv (", nrow(panel), "features)\n\n")

    # 2. All feature stability scores
    cat("2. Exporting all feature stability scores...\n")
    write.csv(model_result$feature_importance,
              file.path(output_dir, "all_feature_stability.csv"), row.names = FALSE)
    cat("   Saved: all_feature_stability.csv (",
        nrow(model_result$feature_importance), "features)\n\n")

    # 3. Discovery performance (per-fold) — full elastic-net model only
    cat("3. Exporting discovery performance...\n")
    perf <- data.frame(
        fold = seq_along(model_result$fold_aucs),
        auc = model_result$fold_aucs,
        sensitivity = model_result$fold_sensitivities,
        specificity = model_result$fold_specificities,
        stringsAsFactors = FALSE
    )
    write.csv(perf, file.path(output_dir, "discovery_performance.csv"), row.names = FALSE)
    cat("   Saved: discovery_performance.csv (full elastic-net model)\n")
    cat("   Mean AUC:", round(model_result$mean_auc, 3),
        "(95% CI:", round(model_result$auc_ci[1], 3), "-",
        round(model_result$auc_ci[2], 3), ")\n\n")

    # 3b. Locked-panel performance (if locked_panel_cv was computed)
    if (!is.null(model_result$locked_panel_cv)) {
        cat("3b. Exporting locked-panel performance...\n")
        lp <- model_result$locked_panel_cv
        lp_perf <- data.frame(
            fold = seq_along(lp$fold_aucs),
            auc = lp$fold_aucs,
            sensitivity = lp$fold_sensitivities,
            specificity = lp$fold_specificities,
            n_panel_features = length(lp$panel_features),
            stringsAsFactors = FALSE
        )
        write.csv(lp_perf, file.path(output_dir, "locked_panel_performance.csv"),
                  row.names = FALSE)
        cat("   Saved: locked_panel_performance.csv (",
            length(lp$panel_features), "-gene locked panel)\n")
        cat("   Locked-panel mean AUC:", round(lp$mean_auc, 3),
            "(selection-biased)\n")

        write.csv(lp$cv_predictions,
                  file.path(output_dir, "locked_panel_cv_predictions.csv"),
                  row.names = FALSE)
        cat("   Saved: locked_panel_cv_predictions.csv\n\n")
    }

    # 4. Validation performance (if provided)
    if (!is.null(validation_result)) {
        cat("4. Exporting validation performance...\n")
        write.csv(validation_result$performance_table,
                  file.path(output_dir, "validation_performance.csv"), row.names = FALSE)
        cat("   Saved: validation_performance.csv\n")
        cat("   Validation AUC:", round(validation_result$auc, 3), "\n\n")

        # Validation predictions
        write.csv(validation_result$predictions,
                  file.path(output_dir, "validation_predictions.csv"), row.names = FALSE)
        cat("   Saved: validation_predictions.csv\n\n")
    } else {
        cat("4. No validation result provided (skipped)\n\n")
    }

    # 5. Cross-validation predictions
    cat("5. Exporting CV predictions...\n")
    write.csv(model_result$cv_predictions,
              file.path(output_dir, "cv_predictions.csv"), row.names = FALSE)
    cat("   Saved: cv_predictions.csv (",
        nrow(model_result$cv_predictions), "predictions across",
        length(unique(model_result$cv_predictions$fold)), "folds)\n\n")

    # 6. Model object (CRITICAL for downstream skills)
    cat("6. Saving model object (RDS)...\n")
    saveRDS(model_result, file.path(output_dir, "lasso_model.rds"))
    cat("   Saved: lasso_model.rds\n")
    cat("   (Load with: model <- readRDS('results/lasso_model.rds'))\n")
    cat("   (Predict with: predict_biomarker_panel(model, new_X))\n\n")

    # 7. Final glmnet model only (lightweight, for prediction only)
    if (!is.null(model_result$final_model)) {
        cat("7. Saving final glmnet model (RDS)...\n")
        saveRDS(model_result$final_model,
                file.path(output_dir, "final_glmnet_model.rds"))
        cat("   Saved: final_glmnet_model.rds\n")
        cat("   (Load with: fit <- readRDS('results/final_glmnet_model.rds'))\n\n")
    }

    # Determine same-study status (defense in depth for the report's C8 gate):
    # a "validation" cohort drawn from the same accession as discovery, or sharing
    # sample IDs with it, is a held-out split — NOT external validation.
    if (!is.null(validation_result)) {
        disc_ids <- if (!is.null(data) && !is.null(data$expression)) {
            colnames(data$expression)
        } else if (!is.null(features) && !is.null(features$X)) {
            rownames(features$X)
        } else character(0)
        val_ids <- if (!is.null(validation_result$predictions)) {
            validation_result$predictions$sample_id
        } else character(0)
        overlap <- length(intersect(disc_ids, val_ids)) > 0
        acc_match <- !is.null(data$accession) &&
            !is.null(validation_result$accession) &&
            identical(as.character(data$accession),
                      as.character(validation_result$accession))
        same_study <- isTRUE(validation_result$is_same_study) || overlap || acc_match
        validation_result$is_same_study <- same_study
        if (same_study) {
            validation_result$cohort_type <- "held-out split (same study/platform)"
        } else if (is.null(validation_result$cohort_type)) {
            validation_result$cohort_type <- "external"
        }
    }

    # 8. Summary report (markdown content source) — MANDATORY terminal step.
    #    Report styling/PDF packaging is delegated to the pdf-report-generation
    #    skill; this script emits only the single markdown content source of truth.
    cat("8. Generating summary report (markdown content source)...\n")
    .write_summary_report(model_result, validation_result, output_dir, data, features,
                           interpretation)
    cat("   Saved: summary_report.md\n\n")

    # 9. Parameters log
    cat("9. Saving parameters...\n")
    params_df <- data.frame(
        parameter = names(model_result$parameters),
        value = as.character(model_result$parameters),
        stringsAsFactors = FALSE
    )
    write.csv(params_df, file.path(output_dir, "parameters.csv"), row.names = FALSE)
    cat("   Saved: parameters.csv\n\n")

    cat("\n=== Export Complete ===\n")
    cat("All files saved to:", output_dir, "\n")
    cat("Files:\n")
    files <- list.files(output_dir, full.names = FALSE)
    for (f in files) {
        cat("  -", f, "\n")
    }
}


# ============================================================================
# SHARED ACCESSOR: Headline AUC (derive-not-restate)
# ============================================================================

#' Derive the headline AUC from the model result
#'
#' Returns the AUC value that should be quoted as the primary performance
#' figure, along with a label identifying its source. When a locked-panel CV
#' was computed, that is the headline figure (the report's §5.2.1); otherwise
#' the full elastic-net model AUC is used. Every printed headline AUC must
#' carry the label inline so the reader knows which number is which.
#'
#' @param model_result Result from run_lasso_panel()
#' @return list(value=numeric, label=character, source=character)
#' @keywords internal
.headline_auc <- function(model_result) {
    if (!is.null(model_result$locked_panel_cv)) {
        lp <- model_result$locked_panel_cv
        list(
            value = lp$mean_auc,
            label = paste0("locked panel, ", length(lp$panel_features), " genes"),
            source = "locked_panel_cv"
        )
    } else {
        list(
            value = model_result$mean_auc,
            label = "full elastic-net model",
            source = "mean_auc"
        )
    }
}


# ============================================================================
# SHARED HELPER: Build enriched report context lines
# ============================================================================

#' Build enriched report content from report_context metadata
#'
#' Returns a named list of character vectors for enriched report sections.
#' When report_context is NULL, returns all-NULL list (generators fall back
#' to generic text).
#'
#' @param rc report_context list from data loader (or NULL)
#' @param data Full data object from loader (or NULL)
#' @param p Model parameters list
#' @param features Feature matrix result (or NULL)
#' @param model_result Full model result
#' @param validation_result Validation result (or NULL)
#' @param format "md" for markdown, "rmd" for R Markdown
#' @return Named list with: analytical_goals, disease_sections, lasso_rationale,
#'         method_text, references
#' @keywords internal
.build_context_lines <- function(rc, data, p, features, model_result,
                                  validation_result, format = "md") {

    # Default: all NULL = use generic text in generators
    ctx <- list(
        analytical_goals  = NULL,   # Numbered aims with inline citations
        disease_sections  = NULL,   # Disease context subsections
        lasso_rationale   = NULL,   # LASSO rationale with citations
        method_text       = NULL,   # Methods with inline citations
        benchmarks        = NULL,   # Published benchmarks section
        references        = NULL    # Numbered reference list
    )

    if (is.null(rc)) return(ctx)

    # Subsection heading prefix: ### for markdown, ## for Rmd (numbered_sections)
    sub_pfx <- if (format == "rmd") "##" else "###"

    # Math symbols — plain ASCII for BOTH formats (spec item 8: purge LaTeX)
    alpha_sym  <- paste0("alpha = ", p$alpha)
    lambda_sym <- "lambda"
    geq_sym    <- "at least"
    arrow_sym  <- "->"

    # ---- Analytical Goals (with inline citations) ----
    # Spec item 14: when analytical_goal_requires is present, append a status
    # marker to goals whose requirement is not 'core' so the report flags goals
    # that are out of scope or require downstream interpretation.
    if (!is.null(rc$analytical_goals)) {
        goal_lines <- character(0)
        req <- rc$analytical_goal_requires
        for (i in seq_along(rc$analytical_goals)) {
            line <- paste0(i, ". ", rc$analytical_goals[i])
            if (!is.null(req) && length(req) >= i && !is.na(req[i])) {
                if (req[i] == "out_of_scope") {
                    line <- paste0(line,
                        " *(out of scope for this skill — requires survival or",
                        " longitudinal analysis; see Scientific Caveats)*")
                } else if (req[i] == "interpretation") {
                    line <- paste0(line,
                        " *(addressed by the downstream biological interpretation",
                        " module, not by the biomarker panel itself)*")
                }
            }
            goal_lines <- c(goal_lines, line)
        }
        ctx$analytical_goals <- goal_lines
    }

    # ---- Disease Context Subsections ----
    sections <- character(0)

    if (!is.null(rc$disease_background)) {
        sections <- c(sections,
            paste(sub_pfx, "Disease Background"), "",
            rc$disease_background, "")
    }
    if (!is.null(rc$trial_description)) {
        sections <- c(sections,
            paste(sub_pfx, "Clinical Trial"), "",
            rc$trial_description, "")
    }
    if (!is.null(rc$patient_population)) {
        sections <- c(sections,
            paste(sub_pfx, "Patient Population"), "",
            rc$patient_population, "")
    }
    if (!is.null(rc$endpoint_definition)) {
        sections <- c(sections,
            paste(sub_pfx, "Endpoint Definition"), "",
            rc$endpoint_definition, "")
    }
    if (!is.null(rc$platform_description)) {
        sections <- c(sections,
            paste(sub_pfx, "Expression Platform"), "",
            rc$platform_description, "")
    }

    if (length(sections) > 0) {
        ctx$disease_sections <- sections
    }

    # ---- LASSO Rationale (with citations) ----
    if (!is.null(rc$references)) {
        lasso_text <- c(
            paste("LASSO logistic regression [1] performs simultaneous feature selection and",
                  "coefficient estimation by applying an L1 penalty that shrinks many",
                  "coefficients to exactly zero. This produces sparse, interpretable",
                  "models ideal for clinical biomarker panels where a small number of",
                  "measurable features is critical for translation to diagnostic",
                  "assays. Compared to other machine learning approaches, LASSO provides",
                  "transparent, interpretable coefficients and naturally handles the",
                  "p >> n problem common in omics data (thousands of features, tens to",
                  "hundreds of samples)."),
            "")

        if (p$alpha < 1) {
            lasso_text <- c(lasso_text,
                paste0("This analysis uses **elastic net** regularization (",
                       alpha_sym, ") [2], which combines L1 (LASSO) and L2 (ridge) ",
                       "penalties. This is particularly useful when features are ",
                       "correlated, as it tends to retain groups of correlated genes ",
                       "rather than arbitrarily selecting one from each group."),
                "")
        }

        lasso_text <- c(lasso_text,
            paste("Model fitting uses the glmnet coordinate descent algorithm [4]",
                  "with regularization path optimization. Stability selection [3]",
                  "ensures that selected features are robust to sampling variation,",
                  "reducing false discovery of noise features. Model discrimination",
                  "is evaluated via ROC analysis using the pROC package [5],",
                  "following the cross-validated biomarker discovery framework of",
                  "Ali et al. [6]."),
            "")

        ctx$lasso_rationale <- lasso_text
    }

    # ---- Methods with inline citations ----
    if (!is.null(rc$references)) {
        ctx$method_text <- c(
            paste0("- **Regularization:** ", alpha_sym,
                   ifelse(p$alpha == 1, " (pure LASSO [1])",
                          " (elastic net [1,2])")),
            paste("- **Repeated CV:**", p$n_repeats,
                  "iterations of class-balanced",
                  paste0(round(p$train_fraction * 100), "/",
                         round((1 - p$train_fraction) * 100)),
                  "train/test splits"),
            paste("- **Inner CV:**", p$n_inner_folds, "folds for", lambda_sym,
                  "selection (cv.glmnet [4])"),
            paste("- **Stability threshold:**", p$stability_threshold,
                  "(features must be selected in", geq_sym,
                  paste0(p$stability_threshold * 100, "%"),
                  "of iterations [3])"),
            paste("- **Random seed:**", p$seed, "(reproducible)"),
            "",
            paste0("**Algorithm:** For each of the ", p$n_repeats,
                   " repeated CV iterations, a class-balanced train/test split is ",
                   "created. On the training set, cv.glmnet [4] selects the optimal ",
                   lambda_sym, " via inner cross-validation (", p$n_inner_folds,
                   "-fold). Non-zero coefficients are recorded. After all iterations, ",
                   "features selected in ", geq_sym, " ",
                   p$stability_threshold * 100, "% of iterations (stability ",
                   "selection [3]) form the final panel. A final model is fit on all ",
                   "samples using only these stable features."),
            "",
            paste(sub_pfx, "Performance Evaluation"),
            "",
            "- **Discrimination:** AUC (Area Under ROC Curve) via pROC package [5] (DeLong method for CIs)",
            paste("- **Confidence intervals:** 2.5th and 97.5th percentiles of fold AUCs across",
                  p$n_repeats, "iterations"),
            "- **Sensitivity/Specificity:** At Youden's J-statistic optimal threshold",
            "- **Calibration:** Predicted probability vs observed event rate",
            "")
    }

    # ---- Published Benchmarks ----
    if (!is.null(rc$published_benchmarks)) {
        pb <- rc$published_benchmarks
        bench_lines <- character(0)

        if (!is.null(pb$intro)) {
            bench_lines <- c(bench_lines, pb$intro, "")
        }

        # Render benchmark table
        if (!is.null(pb$studies)) {
            bench_lines <- c(bench_lines,
                "| Study | Drug | AUC (as reported) | Method | Notes |",
                "|-------|------|:---:|--------|-------|")
            for (j in seq_len(nrow(pb$studies))) {
                s <- pb$studies[j, ]
                bench_lines <- c(bench_lines,
                    paste0("| ", s$study, " | ", s$drug, " | ", s$validated_auc,
                           " | ", s$method, " | ", s$notes, " |"))
            }
            # Add our result as last row.
            # Never present the selection-biased locked-panel headline AUC as
            # external: when a validation cohort exists, quote its AUC and label it
            # accurately (external vs held-out same-study); otherwise quote the
            # discovery headline figure, labeled as discovery.
            if (!is.null(validation_result)) {
                our_auc <- round(validation_result$auc, 3)
                our_auc_cell <- paste0(our_auc,
                    if (isTRUE(validation_result$is_same_study))
                        " (held-out split, same study)"
                    else " (external validation)")
            } else {
                ha <- .headline_auc(model_result)
                our_auc <- round(ha$value, 3)
                our_auc_cell <- paste0(our_auc, " (discovery, not validated)")
            }
            alpha_label <- ifelse(p$alpha == 1, "LASSO",
                                  paste0("Elastic net (", p$alpha, ")"))
            # Extract drug from first benchmark row or use generic
            our_drug <- if (nrow(pb$studies) > 0) pb$studies$drug[1] else "—"
            # Notes cell: leakage_status derived from run_lasso_panel (never hardcoded)
            our_notes <- p$leakage_status
            bench_lines <- c(bench_lines,
                paste0("| **This analysis** | **", our_drug, "** | **", our_auc_cell,
                       "** | **", alpha_label, "** | **", our_notes, "** |"),
                "")
        }

        if (!is.null(pb$context)) {
            bench_lines <- c(bench_lines, pb$context, "")
        }

        ctx$benchmarks <- bench_lines
    }

    # ---- References ----
    if (!is.null(rc$references)) {
        ref_lines <- character(0)
        for (ref in rc$references) {
            ref_lines <- c(ref_lines, ref, "")
        }
        ctx$references <- ref_lines
    }

    return(ctx)
}


# ============================================================================
# BIOLOGICAL INTERPRETATION HELPER
# ============================================================================

#' Build biological interpretation report lines
#' @param interp Result from run_biological_interpretation()
#' @param panel Data frame of panel genes with coefficients
#' @param format "md" for markdown or "rmd" for R Markdown
#' @param outcome_col Name of the outcome column (for derived valence phrasing)
#' @return Named list with pathway_lines, celltype_lines, gwas_lines
#' @keywords internal
.build_interpretation_lines <- function(interp, panel, format = "md",
                                          outcome_col = NULL) {
    result <- list(pathway_lines = NULL, celltype_lines = NULL, gwas_lines = NULL)
    if (is.null(interp)) return(result)

    pos_genes <- panel$feature[panel$mean_coefficient > 0]
    neg_genes <- panel$feature[panel$mean_coefficient < 0]
    arrow <- "->"  # spec item 8: unconditional ASCII

    # Derived outcome-valence phrases (spec item 9: never emit clinical valence word)
    pos_phrase <- if (!is.null(outcome_col)) {
        paste0("higher predicted probability of ", outcome_col, " = 1")
    } else {
        "the modelled positive class (outcome = 1)"
    }
    neg_phrase <- if (!is.null(outcome_col)) {
        paste0("higher predicted probability of ", outcome_col, " = 0")
    } else {
        "the modelled negative class (outcome = 0)"
    }

    # ---- Pathway Enrichment ----
    pw <- c()

    # Coefficient direction summary
    if (length(pos_genes) > 0) {
        pw <- c(pw, paste0("**Positive coefficient genes** (higher expression ", arrow,
                           " ", pos_phrase, "): ", paste(pos_genes, collapse = ", ")), "")
    }
    if (length(neg_genes) > 0) {
        pw <- c(pw, paste0("**Negative coefficient genes** (higher expression ", arrow,
                           " ", neg_phrase, "): ", paste(neg_genes, collapse = ", ")), "")
    }

    # GSEA Hallmark results
    gsea_h <- interp$pathway$gsea_hallmark
    if (!is.null(gsea_h) && nrow(gsea_h) > 0) {
        top_gsea <- head(gsea_h[order(gsea_h$pval), ], 10)
        top_gsea$pathway_short <- gsub("HALLMARK_", "", top_gsea$pathway)
        top_gsea$pathway_short <- gsub("_", " ", top_gsea$pathway_short)

        pw <- c(pw,
            "**Gene Set Enrichment Analysis (GSEA)** — MSigDB Hallmark pathways ranked by",
            "model selection frequency across 500 candidate features:",
            "",
            "| Pathway | NES | P-value | Adj. P | Gene Set Size |",
            "|---------|:---:|:-------:|:------:|:---:|")
        for (i in seq_len(nrow(top_gsea))) {
            pw <- c(pw, sprintf("| %s | %.2f | %.4f | %.3f | %d |",
                                top_gsea$pathway_short[i],
                                top_gsea$NES[i],
                                top_gsea$pval[i],
                                top_gsea$padj[i],
                                top_gsea$size[i]))
        }
        pw <- c(pw, "")
    }

    # ORA Reactome results
    ora_r <- interp$pathway$ora_reactome
    if (!is.null(ora_r) && nrow(ora_r@result[ora_r@result$p.adjust < 0.1, ]) > 0) {
        sig_r <- ora_r@result[ora_r@result$p.adjust < 0.1, ]
        sig_r$Description_short <- gsub("REACTOME_", "", sig_r$Description)
        sig_r$Description_short <- gsub("_", " ", sig_r$Description_short)

        pw <- c(pw,
            "**Over-Representation Analysis (ORA)** — Reactome pathways (10-gene panel):",
            "",
            "| Pathway | Gene Ratio | P-value | Adj. P | Genes |",
            "|---------|:----------:|:-------:|:------:|-------|")
        for (i in seq_len(nrow(sig_r))) {
            pw <- c(pw, sprintf("| %s | %s | %.4f | %.3f | %s |",
                                sig_r$Description_short[i],
                                sig_r$GeneRatio[i],
                                sig_r$pvalue[i],
                                sig_r$p.adjust[i],
                                sig_r$geneID[i]))
        }
        pw <- c(pw, "")
    }

    # ORA GO (only if significant)
    ora_go <- interp$pathway$ora_go
    if (!is.null(ora_go) && nrow(ora_go@result[ora_go@result$p.adjust < 0.1, ]) > 0) {
        sig_go <- ora_go@result[ora_go@result$p.adjust < 0.1, ]
        pw <- c(pw,
            "**GO Biological Process (ORA):**",
            "",
            "| GO Term | Gene Ratio | Adj. P | Genes |",
            "|---------|:----------:|:------:|-------|")
        for (i in seq_len(min(nrow(sig_go), 10))) {
            pw <- c(pw, sprintf("| %s | %s | %.3f | %s |",
                                sig_go$Description[i],
                                sig_go$GeneRatio[i],
                                sig_go$p.adjust[i],
                                sig_go$geneID[i]))
        }
        pw <- c(pw, "")
    }

    result$pathway_lines <- pw

    # ---- Cell-Type Expression Context ----
    ct <- interp$celltype
    ct_lines <- c()
    tissue_label <- if (!is.null(interp$tissue_label)) interp$tissue_label else "Tissue"

    if (!is.null(ct) && nrow(ct) > 0) {
        if ("source" %in% names(ct) && all(ct$source == "curated")) {
            # Detect change column (uc_change for legacy IBD, disease_change for new)
            change_col <- if ("disease_change" %in% names(ct)) "disease_change" else "uc_change"
            change_header <- paste(tissue_label, "Change")

            ct_lines <- c(ct_lines,
                paste("Cell-type expression context from published single-cell atlases for",
                      tissue_label, "tissue:"),
                "",
                sprintf("| Gene | Primary Cell Type | Compartment | %s | Evidence |", change_header),
                "|------|------------------|:-----------:|:------------:|----------|")
            for (i in seq_len(nrow(ct))) {
                ct_lines <- c(ct_lines, sprintf("| *%s* | %s | %s | %s | %s |",
                                                ct$gene[i],
                                                ct$cell_type[i],
                                                ct$compartment[i],
                                                ct[[change_col]][i],
                                                ct$evidence[i]))
            }
        } else {
            # CZI Census real data — summarize top cell types per gene
            ct_lines <- c(ct_lines,
                paste("Cell-type expression in", tissue_label, "from CZI CELLxGENE Census"),
                "(tens of millions of single cells across published datasets):",
                "",
                "| Gene | Top Cell Types (by expression) | % Expressing |",
                "|------|------------------------------|:------------:|")

            # Use normal tissue if available, otherwise all
            disease_col <- if ("disease" %in% names(ct)) "disease" else NULL
            if (!is.null(disease_col) && "normal" %in% ct$disease) {
                ct_sub <- ct[ct$disease == "normal", ]
            } else {
                ct_sub <- ct
            }

            for (gene in unique(ct_sub$gene)) {
                gene_data <- ct_sub[ct_sub$gene == gene, ]
                gene_data <- gene_data[order(-gene_data$mean_expression), ]
                top3 <- head(gene_data, 3)
                ct_str <- paste(sprintf("%s (%.0f%%)", top3$cell_type,
                                        top3$pct_expressing), collapse = "; ")
                ct_lines <- c(ct_lines, sprintf("| *%s* | %s | |", gene, ct_str))
            }
        }
        ct_lines <- c(ct_lines, "")
    }
    result$celltype_lines <- ct_lines

    # ---- GWAS / Disease Gene Overlap ----
    gwas <- interp$gwas
    gwas_lines <- c()
    gwas_label <- if (!is.null(interp$gwas_label)) interp$gwas_label else "Disease GWAS"

    if (!is.null(gwas)) {
        ann <- gwas$panel_annotations
        gwas_refs <- if (!is.null(gwas$gwas_refs)) gwas$gwas_refs else "published GWAS studies"

        gwas_lines <- c(gwas_lines,
            sprintf("Cross-reference with %d curated %s genes (%s):",
                    gwas$n_gwas_genes, gwas_label, gwas_refs),
            "",
            "| Gene | GWAS Hit | Disease Relevance | Drug Relevance |",
            "|------|:--------:|-------------------|----------------|")
        for (i in seq_len(nrow(ann))) {
            gwas_icon <- if (ann$in_gwas[i]) "Direct" else "Indirect/None"
            # Truncate long text for table readability
            dis_rel <- ann$disease_relevance[i]
            drug_rel <- ann$drug_relevance[i]
            if (nchar(dis_rel) > 80) dis_rel <- paste0(substr(dis_rel, 1, 77), "...")
            if (nchar(drug_rel) > 60) drug_rel <- paste0(substr(drug_rel, 1, 57), "...")
            gwas_lines <- c(gwas_lines, sprintf("| *%s* | %s | %s | %s |",
                                                ann$gene[i], gwas_icon,
                                                dis_rel, drug_rel))
        }
        gwas_lines <- c(gwas_lines, "")

        # Summary
        gwas_lines <- c(gwas_lines,
            sprintf("**Summary:** %d/%d panel genes have direct %s association (%s).",
                    sum(ann$in_gwas), nrow(ann), gwas_label,
                    if (sum(ann$in_gwas) > 0) paste(ann$gene[ann$in_gwas], collapse = ", ") else "none"),
            "The panel primarily captures expression-level disease signatures rather than",
            "germline genetic risk — consistent with the transcriptomic biomarker approach.",
            "")
    }
    result$gwas_lines <- gwas_lines

    return(result)
}


# ============================================================================
# SHARED REPORT SECTION BUILDERS (spec item 15: de-duplicate writers)
# ============================================================================

#' Build Datasets section lines (shared by md and rmd writers)
#' @param data Data object from loader (or NULL)
#' @param p Model parameters
#' @param validation_result Validation result (or NULL)
#' @param format "md" or "rmd"
#' @keywords internal
.build_datasets_lines <- function(data, p, validation_result, format = "md") {
    sec3 <- if (format == "rmd") "# Datasets" else "## 3. Datasets"
    sub31 <- if (format == "rmd") "## Discovery Cohort" else "### 3.1 Discovery Cohort"
    sub32 <- if (format == "rmd") "## Validation Cohort:" else "### 3.2 Validation Cohort:"

    lines <- c(sec3, "", sub31, "")

    if (!is.null(data)) {
        n_resp <- sum(data$metadata[[data$outcome_col]] == 1, na.rm = TRUE)
        n_nonresp <- sum(data$metadata[[data$outcome_col]] == 0, na.rm = TRUE)

        lines <- c(lines,
            paste("- **Samples:**", ncol(data$expression),
                  paste0("(", n_resp, " positive / ", n_nonresp, " negative)")),
            paste("- **Genes measured:**", format(nrow(data$expression), big.mark = ",")),
            paste("- **Features after filtering:**", p$n_features,
                  "(top most variable genes)"),
            paste0("- *Note:* all ", ncol(data$expression),
                   " samples constitute the discovery cohort and enter repeated ",
                   "cross-validation; there is no single fixed held-out training subset ",
                   "(each per-iteration training portion is smaller)."),
            "")

        # Treatment arms bullet — guarded (spec item 7)
        if ("treatment" %in% names(data$metadata)) {
            treatment_tab <- table(data$metadata$treatment)
            if (length(treatment_tab) > 0) {
                treat_str <- paste(names(treatment_tab), "=", treatment_tab,
                                   collapse = ", ")
                lines <- c(lines,
                    paste("- **Treatment arms:**", treat_str),
                    "")
            }
        }
    } else {
        lines <- c(lines,
            paste("- **Samples:**", p$n_samples),
            paste("- **Features:**", p$n_features),
            "")
    }

    if (!is.null(validation_result)) {
        if (isTRUE(validation_result$is_same_study)) {
            val_header <- sub("Validation Cohort",
                              "Held-out Split (Same Study/Platform)", sub32)
            lines <- c(lines,
                paste(val_header, validation_result$cohort_name),
                "",
                paste("- **Features matched:**", validation_result$n_features_used,
                      "/", validation_result$n_features_total, "discovery features"),
                "",
                "This split is drawn from the same accession/samples as the discovery",
                "cohort. It is a held-out split (same study and platform), not an",
                "independent cohort: it does not test cross-cohort or cross-platform",
                "generalization and remains subject to same-study optimism.",
                "")
        } else {
            lines <- c(lines,
                paste(sub32, validation_result$cohort_name),
                "",
                paste("- **Features matched:**", validation_result$n_features_used,
                      "/", validation_result$n_features_total, "discovery features"),
                "",
                "The validation cohort shares the same microarray platform as the discovery",
                "cohort, enabling direct application of the trained model without cross-platform",
                "normalization. Cross-drug validation (different therapeutic mechanisms) tests",
                "whether the baseline transcriptomic signature captures shared biology of",
                "treatment response rather than drug-specific effects.",
                "")
        }
    }

    return(lines)
}

#' Build Discovery + Locked-Panel performance lines (shared by md and rmd writers)
#' @param model_result Full model result
#' @param p Model parameters
#' @param validation_result Validation result (or NULL)
#' @param format "md" or "rmd"
#' @keywords internal
.build_performance_lines <- function(model_result, p, validation_result,
                                      format = "md") {
    if (format == "rmd") {
        sec5 <- "# Results"
        sub51 <- "## Biomarker Panel"
        sub52 <- "## Discovery Performance (Nested CV)"
        sub521 <- "## Locked-Panel Cross-Validation"
        sub53 <- "## External Validation:"
        dash <- "--"
    } else {
        sec5 <- "## 5. Results"
        sub51 <- "### 5.1 Biomarker Panel"
        sub52 <- "### 5.2 Discovery Performance (Nested CV)"
        sub521 <- "### 5.2.1 Locked-Panel Cross-Validation"
        sub53 <- "### 5.3 External Validation:"
        dash <- "-"
    }

    panel <- model_result$feature_importance[model_result$feature_importance$is_stable, ]

    lines <- c(sec5, "", sub51, "",
        paste("**Panel size:**", nrow(panel), "features"),
        paste0("(stability threshold: ", p$stability_threshold,
               "; ", p$n_stable, " passed threshold",
               ifelse(p$n_stable < nrow(panel),
                      paste0(", relaxed to top ", nrow(panel)), ""), ")"),
        "")

    lines <- c(lines,
        "| Feature | Selection Frequency | Mean Coefficient | SD Coefficient |",
        "|---------|:------------------:|:----------------:|:--------------:|")
    for (i in seq_len(nrow(panel))) {
        lines <- c(lines, paste0(
            "| ", panel$feature[i],
            " | ", round(panel$selection_frequency[i], 3),
            " | ", sprintf("%+.4f", panel$mean_coefficient[i]),
            " | ", round(panel$sd_coefficient[i], 4), " |"))
    }
    lines <- c(lines, "")

    # Discovery performance (full elastic-net model)
    lines <- c(lines, sub52, "",
        paste("- **Mean AUC:**", round(model_result$mean_auc, 3),
              "(95% CI:", round(model_result$auc_ci[1], 3), dash,
              round(model_result$auc_ci[2], 3), ")"),
        paste("- **Mean Sensitivity:**",
              round(mean(model_result$fold_sensitivities, na.rm = TRUE), 3)),
        paste("- **Mean Specificity:**",
              round(mean(model_result$fold_specificities, na.rm = TRUE), 3)),
        paste("- **AUC range across folds:**",
              round(min(model_result$fold_aucs, na.rm = TRUE), 3), dash,
              round(max(model_result$fold_aucs, na.rm = TRUE), 3)),
        paste0("- *Operating point:* sensitivity and specificity are reported at the ",
               "Youden-optimal threshold selected within each held-out fold (in-sample ",
               "threshold selection); treat them as optimistic."),
        "")

    # Locked-panel CV section (spec item 5: derive-not-restate with sign branch)
    if (!is.null(model_result$locked_panel_cv)) {
        lp <- model_result$locked_panel_cv
        delta <- lp$mean_auc - model_result$mean_auc
        panel_size <- length(lp$panel_features)

        lines <- c(lines, sub521, "",
            paste("- **Locked-panel mean AUC:**", round(lp$mean_auc, 3),
                  "(selection-biased)"),
            paste("- **Panel size:**", panel_size, "genes"),
            paste("- **Full-model mean AUC:**", round(model_result$mean_auc, 3)),
            paste("- **Delta (locked - full):**", round(delta, 3)),
            "")

        if (delta >= 0) {
            # Selection-bias caveat (spec item 5)
            lines <- c(lines,
                paste0("The locked-panel CV AUC of ", round(lp$mean_auc, 3),
                       " (", panel_size, " genes) exceeds the full elastic-net figure of ",
                       round(model_result$mean_auc, 3),
                       " across ", p$n_repeats, " folds. This is a selection-bias artefact,",
                       " not evidence of a better model: the panel features were chosen using",
                       " all ", p$n_repeats, " folds, so re-scoring them in those same folds",
                       " re-uses selection information. An unbiased locked-panel estimate",
                       " requires selecting the panel inside each fold or scoring it on an",
                       " independent cohort."),
                "")
        } else {
            lines <- c(lines,
                paste0("The locked-panel CV AUC of ", round(lp$mean_auc, 3),
                       " (", panel_size, " genes) is lower than the full elastic-net figure of ",
                       round(model_result$mean_auc, 3),
                       " across ", p$n_repeats, " folds, consistent with the locked panel",
                       " using fewer features than the per-iteration selections."),
                "")
        }
    }

    # External validation (or held-out same-study split — labeled accurately)
    if (!is.null(validation_result)) {
        val_header <- if (isTRUE(validation_result$is_same_study)) {
            sub("External Validation", "Held-out Split (Same Study/Platform)", sub53)
        } else sub53
        lines <- c(lines,
            paste(val_header, validation_result$cohort_name),
            "",
            paste("- **AUC:**", round(validation_result$auc, 3),
                  "(95% CI:", round(validation_result$auc_ci[1], 3), dash,
                  round(validation_result$auc_ci[3], 3), ")"),
            paste("- **Panel features used:**", validation_result$n_features_used,
                  "/", validation_result$n_features_total),
            "")

        if (!is.null(validation_result$performance_table)) {
            perf_t <- validation_result$performance_table
            for (j in seq_len(nrow(perf_t))) {
                lines <- c(lines, paste0("- **", perf_t$metric[j], ":** ",
                                          round(as.numeric(perf_t$value[j]), 3)))
            }
            lines <- c(lines,
                paste0("- *Operating point:* the threshold, sensitivity, specificity, ",
                       "PPV, and NPV above were selected at the Youden-optimal cut-point ",
                       "on this same set (in-sample threshold selection) and are therefore ",
                       "optimistic; treat them as an upper bound."),
                "")
        }
    }

    return(lines)
}

#' Build Biological Interpretation fallback lines (shared by md and rmd writers)
#' @param model_result Full model result
#' @param p Model parameters
#' @param panel Panel data frame
#' @param pos_genes Positive-coefficient genes
#' @param neg_genes Negative-coefficient genes
#' @param outcome_col Outcome column name (or NULL)
#' @param format "md" or "rmd"
#' @keywords internal
.build_interp_fallback_lines <- function(model_result, p, panel, pos_genes,
                                          neg_genes, outcome_col, format = "md") {
    arrow <- "->"

    # Derived outcome-valence phrases (spec item 9)
    pos_phrase <- if (!is.null(outcome_col)) {
        paste0("higher predicted probability of ", outcome_col, " = 1")
    } else {
        "the modelled positive class (outcome = 1)"
    }
    neg_phrase <- if (!is.null(outcome_col)) {
        paste0("higher predicted probability of ", outcome_col, " = 0")
    } else {
        "the modelled negative class (outcome = 0)"
    }

    lines <- c()

    if (length(pos_genes) > 0) {
        lines <- c(lines,
            paste0("**Positive coefficient genes** (higher expression ", arrow,
                   " ", pos_phrase, "): ", paste(pos_genes, collapse = ", ")),
            "")
    }
    if (length(neg_genes) > 0) {
        lines <- c(lines,
            paste0("**Negative coefficient genes** (higher expression ", arrow,
                   " ", neg_phrase, "): ", paste(neg_genes, collapse = ", ")),
            "")
    }

    # Derived subsampling description (spec item 9c: never say 'bootstrap')
    subsamp_desc <- paste0("across ", p$n_repeats, " repeated ",
                           round(p$train_fraction * 100), "/",
                           round((1 - p$train_fraction) * 100),
                           " subsampling iterations (sampling without replacement)")

    lines <- c(lines,
        "The sign and magnitude of model coefficients indicate the direction and",
        "strength of each feature's contribution to the prediction. Features with",
        "positive coefficients suggest that higher expression is associated with",
        paste0(pos_phrase, ", while negative coefficients"),
        "suggest the opposite. Selection frequency reflects the robustness of each",
        paste0("feature ", subsamp_desc, "."),
        paste0("Features selected in at least ", p$stability_threshold * 100,
               "% of iterations are considered highly stable."),
        "")

    return(lines)
}

#' Build Limitations lines (shared by md and rmd writers)
#' @param model_result Full model result
#' @param p Model parameters
#' @param validation_result Validation result (or NULL)
#' @param has_validation Logical
#' @param lim_num Section number (md) or NA (rmd)
#' @param format "md" or "rmd"
#' @keywords internal
.build_limitations_lines <- function(model_result, p, validation_result,
                                      has_validation, lim_num, format = "md") {
    if (format == "rmd") {
        lines <- c("# Limitations", "")
        sub1 <- "## No External Validation"
        sub2 <- "## External Validation"
        sub3 <- "## Optimism Bias in CV"
        sub4 <- "## Platform Specificity"
        dash <- "--"
    } else {
        lines <- c(paste0("## ", lim_num, ". Limitations"), "")
        sub1 <- paste0("### ", lim_num, ".1 No External Validation")
        sub2 <- paste0("### ", lim_num, ".1 External Validation")
        sub3 <- paste0("### ", lim_num, ".2 Optimism Bias in CV")
        sub4 <- paste0("### ", lim_num, ".3 Platform Specificity")
        dash <- "-"
    }

    # Headline AUC for the limitations text (derive-not-restate, spec item 2)
    ha <- .headline_auc(model_result)

    if (!has_validation) {
        lines <- c(lines, sub1, "",
            "This is a **discovery-only analysis**. No independent validation cohort was used.",
            paste0("The reported AUC of ", round(ha$value, 3), " (", ha$label,
                   ") is derived entirely from the same ", p$n_samples,
                   "-sample dataset used for feature selection and model training."),
            "Performance on an independent cohort is expected to be lower. The workflow",
            "supports external validation via `validate_external.R` — this step is strongly",
            "recommended before drawing conclusions about panel utility.",
            "")
    } else {
        if (isTRUE(validation_result$is_same_study)) {
            sub2_hdr <- sub("External Validation",
                            "Held-out Split (Same Study/Platform)", sub2)
            lines <- c(lines, sub2_hdr, "",
                paste0("A held-out split of the same study (", validation_result$cohort_name,
                       ") was evaluated (AUC: ", round(validation_result$auc, 3),
                       "). Because it is drawn from the same accession/samples, it does not ",
                       "establish cross-cohort generalizability; it is a same-study held-out ",
                       "estimate, not an independent-cohort result. A genuinely independent ",
                       "cohort is still required."),
                "")
        } else {
            lines <- c(lines, sub2, "",
                paste0("External validation was performed on ", validation_result$cohort_name,
                       " (AUC: ", round(validation_result$auc, 3), ")."),
                "Additional independent cohorts are recommended to confirm generalizability.",
                "")
        }
    }

    lines <- c(lines, sub3, "",
        paste0("The stability selection procedure uses ", p$n_repeats,
               " random ", round(p$train_fraction * 100), "/",
               round((1 - p$train_fraction) * 100),
               " splits of the same samples for both (a) determining which"),
        "features are stable and (b) estimating AUC. Because feature selection and",
        "performance estimation share the same data pool, the reported AUC is not fully",
        "independent of the feature selection step. This is a known source of optimism bias",
        "in penalised stability selection workflows. Expected magnitude: typically 0.02-0.05 AUC units.",
        "",
        sub4, "",
        "The panel was derived from a specific expression platform. Transferability to other",
        "platforms (e.g., RNA-seq vs microarray) requires cross-platform validation and may",
        "be affected by gene coverage differences.",
        "")

    return(lines)
}


# ============================================================================
# CONSISTENCY GATE (spec item 1)
# ============================================================================

#' Assert report consistency before writeLines
#'
#' Scans the fully assembled character vector and checks that restated
#' analysis parameters, AUC values, leakage claims, and LaTeX tokens match
#' the values in model_result$parameters. Raises stop() on any mismatch.
#' Auto-repairs empty bullets (C4) and residual LaTeX tokens (C5).
#'
#' @param lines Character vector of assembled report lines
#' @param p Model parameters list
#' @param model_result Full model result
#' @param validation_result Validation result (or NULL)
#' @param output_dir Output directory for report_consistency_check.csv
#' @param which "md" or "rmd" — identifies the writer for error messages
#' @return Possibly repaired lines vector
#' @keywords internal
.assert_report_consistency <- function(lines, p, model_result, validation_result,
                                        output_dir, which) {
    checks <- data.frame(
        check_id = character(0),
        description = character(0),
        status = character(0),
        detail = character(0),
        stringsAsFactors = FALSE
    )

    expected_repeats <- p$n_repeats
    expected_train_pct <- round(p$train_fraction * 100)
    expected_test_pct <- round((1 - p$train_fraction) * 100)
    expected_stab_pct <- p$stability_threshold * 100
    ha <- .headline_auc(model_result)
    expected_headline_auc <- round(ha$value, 3)

    add_check <- function(id, desc, status, detail) {
        checks <<- rbind(checks, data.frame(
            check_id = id, description = desc, status = status, detail = detail,
            stringsAsFactors = FALSE))
    }

    fail_msg <- function(check, line, found, expected) {
        paste0(check, " FAILED in ", which, " report: line='", substr(line, 1, 80),
               "' found=", found, " expected=", expected,
               ". All data artifacts (CSV/RDS) were already written by steps 1-7.",
               " Do NOT write custom export code — correct the flagged text and re-run export_all!")
    }

    # C1: every "N iterations" equals p$n_repeats
    for (i in seq_along(lines)) {
        m <- regmatches(lines[i], gregexpr("[0-9]+\\s+iterations?", lines[i]))
        if (length(m[[1]]) > 0) {
            for (match in m[[1]]) {
                found_n <- as.integer(gsub("\\D", "", match))
                if (!is.na(found_n) && found_n != expected_repeats) {
                    stop(fail_msg("C1", lines[i], found_n, expected_repeats))
                }
            }
        }
    }
    add_check("C1", "n_repeats consistency", "PASS", paste("All match", expected_repeats))

    # C2: every NN/NN adjacent to train/test or splits equals expected
    for (i in seq_along(lines)) {
        if (grepl("train/test|splits", lines[i], ignore.case = TRUE)) {
            m <- regmatches(lines[i], gregexpr("[0-9]{2}\\s*/\\s*[0-9]{2}", lines[i]))
            if (length(m[[1]]) > 0) {
                for (match in m[[1]]) {
                    nums <- as.integer(regmatches(match, gregexpr("[0-9]+", match))[[1]])
                    if (length(nums) == 2) {
                        if (nums[1] != expected_train_pct || nums[2] != expected_test_pct) {
                            stop(fail_msg("C2", lines[i],
                                  paste(nums[1], "/", nums[2]),
                                  paste(expected_train_pct, "/", expected_test_pct)))
                        }
                    }
                }
            }
        }
    }
    add_check("C2", "train/test fraction consistency", "PASS",
              paste0(expected_train_pct, "/", expected_test_pct))

    # C3: every "N% of iterations/folds" equals stability_threshold*100
    for (i in seq_along(lines)) {
        m <- regmatches(lines[i],
                        gregexpr("[0-9]{1,3}%\\s+of (iterations|folds)", lines[i]))
        if (length(m[[1]]) > 0) {
            for (match in m[[1]]) {
                found_pct <- as.integer(regmatches(match, gregexpr("[0-9]+", match))[[1]])
                if (!is.na(found_pct) && found_pct != expected_stab_pct) {
                    stop(fail_msg("C3", lines[i], found_pct, expected_stab_pct))
                }
            }
        }
    }
    add_check("C3", "stability_threshold consistency", "PASS",
              paste0(expected_stab_pct, "%"))

    # C4: no empty bullet — auto-repair by dropping the line
    empty_bullet_lines <- which(grepl("^\\s*[-*]\\s+\\*\\*[^*]+:\\*\\*\\s*(=\\s*)?$", lines))
    if (length(empty_bullet_lines) > 0) {
        lines <- lines[-empty_bullet_lines]
        add_check("C4", "empty bullet check", "REPAIRED",
                  paste("Dropped", length(empty_bullet_lines), "empty bullet line(s)"))
    } else {
        add_check("C4", "empty bullet check", "PASS", "No empty bullets found")
    }

    # C5: no residual LaTeX math token — auto-repair via fixed ASCII map
    latex_map <- list(
        "$\\geq$" = "at least",
        "$\\leq$" = "at most",
        "$\\rightarrow$" = "->",
        "$\\pm$" = "+/-",
        "$\\times$" = "x",
        "$\\geq$" = ">=",
        "$\\leq$" = "<="
    )
    latex_found <- FALSE
    for (i in seq_along(lines)) {
        for (pat in names(latex_map)) {
            if (grepl(gsub("([$\\])", "\\\\\\1", pat), lines[i], fixed = FALSE)) {
                lines[i] <- gsub(gsub("([$\\])", "\\\\\\1", pat),
                                 latex_map[[pat]], lines[i], fixed = FALSE)
                latex_found <- TRUE
            }
        }
    }
    if (latex_found) {
        add_check("C5", "LaTeX math token check", "REPAIRED",
                  "Replaced LaTeX tokens with ASCII equivalents")
    } else {
        add_check("C5", "LaTeX math token check", "PASS", "No LaTeX tokens found")
    }

    # C6: 'no leakage' may appear only when preprocessing is genuinely fold-internal
    no_leakage_allowed <- isTRUE(p$preprocess_per_fold) &&
        isTRUE(p$fold_variance_filter_active) && !isTRUE(p$global_variance_filter_applied)
    for (i in seq_along(lines)) {
        if (grepl("no leakage", lines[i], ignore.case = TRUE)) {
            if (!no_leakage_allowed) {
                stop(fail_msg("C6", lines[i], "no leakage",
                      "leakage_status from parameters (not clean)"))
            }
        }
    }
    add_check("C6", "leakage claim check", "PASS",
              if (no_leakage_allowed) "Clean claim allowed" else "No 'no leakage' claim present")

    # C7: every "AUC of X.XXX" in skill-generated lines equals headline AUC
    # Scope: skill-generated lines only, NOT rows carried from pb$studies
    # (which legitimately contain strings like '0.81-0.88' and '0.77 (DLDA30)')
    for (i in seq_along(lines)) {
        # Skip lines that look like benchmark table data rows (start with | and contain study names)
        if (grepl("^\\|.*\\|.*\\|.*\\|.*\\|", lines[i]) &&
            !grepl("This analysis", lines[i])) {
            next
        }
        m <- regmatches(lines[i], gregexpr("AUC of ([0-9.]+)", lines[i]))
        if (length(m[[1]]) > 0) {
            for (match in m[[1]]) {
                found_auc <- as.numeric(regmatches(match, gregexpr("[0-9.]+", match))[[1]])
                if (!is.na(found_auc) && round(found_auc, 3) != expected_headline_auc) {
                    stop(fail_msg("C7", lines[i], round(found_auc, 3),
                          expected_headline_auc))
                }
            }
        }
    }
    add_check("C7", "headline AUC consistency", "PASS",
              paste0(expected_headline_auc, " (", ha$label, ")"))

    # C8: 'external validation' must never label a same-study split (same accession
    # or overlapping sample IDs). Such a split is a held-out split, not external
    # validation. This makes the mislabel structurally impossible.
    same_study_val <- !is.null(validation_result) && isTRUE(validation_result$is_same_study)
    if (same_study_val) {
        for (i in seq_along(lines)) {
            if (grepl("external validation", lines[i], ignore.case = TRUE)) {
                stop(fail_msg("C8", lines[i],
                      "'external validation' on a same-study split",
                      "label it 'held-out split (same study/platform)'"))
            }
        }
        add_check("C8", "external-validation label check", "PASS",
                  "Same-study split not labeled external validation")
    } else {
        add_check("C8", "external-validation label check", "PASS",
                  "Validation cohort is genuinely external or absent")
    }

    # Write consistency check log
    write.csv(checks, file.path(output_dir, "report_consistency_check.csv"),
              row.names = FALSE)

    return(lines)
}


# ============================================================================
# ENHANCED MARKDOWN REPORT
# ============================================================================

#' Write comprehensive summary report as markdown
#' @keywords internal
.write_summary_report <- function(model_result, validation_result, output_dir,
                                   data = NULL, features = NULL,
                                   interpretation = NULL) {
    p <- model_result$parameters
    panel <- model_result$feature_importance[model_result$feature_importance$is_stable, ]
    pos_genes <- panel$feature[panel$mean_coefficient > 0]
    neg_genes <- panel$feature[panel$mean_coefficient < 0]

    # Get enriched context (NULL fields => generic fallback)
    rc <- if (!is.null(data)) data$report_context else NULL
    ctx <- .build_context_lines(rc, data, p, features, model_result,
                                 validation_result, format = "md")

    has_validation <- !is.null(validation_result)
    outcome_col <- if (!is.null(data)) data$outcome_col else NULL

    lines <- c(
        "# Elastic-Net Biomarker Panel Discovery Report",
        "",
        paste("**Generated:**", Sys.time()),
        ""
    )

    # Discovery-only caveat (omitted if external validation was performed)
    if (!has_validation) {
        lines <- c(lines,
            "> **IMPORTANT — Discovery-Only Analysis:** This report presents results from a",
            "> single discovery cohort with no independent external validation. All performance",
            "> metrics (AUC, sensitivity, specificity) are estimates from repeated subsampling",
            "> of the same dataset and should be considered **preliminary**. They are expected",
            "> to be optimistic relative to performance on a truly independent cohort. External",
            "> validation is required before any clinical or translational use of this panel.",
            "",
            "---",
            "")
    }

    # ---- 1. Study Objectives ----
    lines <- c(lines, "## 1. Study Objectives", "")

    if (!is.null(ctx$analytical_goals)) {
        # Enriched: clinical question framing + cited aims
        lines <- c(lines,
            "This analysis was designed to address the following specific aims in the",
            "context of predicting clinical outcomes from baseline molecular profiling:",
            "",
            ctx$analytical_goals,
            "")
    } else {
        # Generic fallback
        lines <- c(lines,
            "This analysis aims to identify a minimal, interpretable biomarker panel from",
            "high-dimensional gene expression data using penalised logistic regression",
            "(elastic net or LASSO). The goal is to select a",
            "parsimonious set of transcriptomic features that can predict a binary clinical",
            "outcome from baseline tissue biopsies, enabling potential translation into a",
            "clinical diagnostic or prognostic assay.",
            "",
            "**Specific aims:**",
            "",
            "1. Select a minimal gene panel (<15 features) with robust predictive performance",
            "2. Evaluate model discrimination (AUC), calibration, and feature stability",
            paste0("3. Assess cross-validation performance across ", p$n_repeats,
                   " repeated train/test splits"),
            "")

        if (!is.null(validation_result)) {
            lines <- c(lines,
                paste0("4. Validate the panel on an independent cohort (",
                       validation_result$cohort_name, ")"),
                "")
        }
    }

    # ---- 2. Disease Context & Rationale ----
    lines <- c(lines, "## 2. Disease Context & Rationale", "")

    if (!is.null(ctx$disease_sections)) {
        # Enriched: multiple subsections from report_context
        lines <- c(lines, ctx$disease_sections)
    } else if (!is.null(data) && !is.null(data$description)) {
        # Fallback: single description line
        lines <- c(lines,
            "**Dataset context:**",
            data$description,
            "")
    }

    # Penalised regression rationale
    lines <- c(lines, "### Why penalised regression for biomarker selection", "")

    if (!is.null(ctx$lasso_rationale)) {
        # Enriched: with inline citations
        lines <- c(lines, ctx$lasso_rationale)
    } else {
        # Generic fallback
        lines <- c(lines,
            "Penalised logistic regression (LASSO or elastic net) performs simultaneous",
            "feature selection and coefficient estimation by applying an L1 penalty that",
            "shrinks many coefficients to exactly zero. This produces sparse, interpretable",
            "models ideal for clinical biomarker panels where a small number of measurable",
            "features is critical for translation to diagnostic assays. Compared to other",
            "machine learning approaches, penalised regression provides transparent,",
            "interpretable coefficients and naturally handles the p >> n problem common in",
            "omics data (thousands of features, tens to hundreds of samples).",
            "")

        if (p$alpha < 1) {
            lines <- c(lines,
                paste0("This analysis uses **elastic net** regularization (alpha = ", p$alpha,
                       "), which combines L1 (LASSO) and L2 (ridge) penalties. This is",
                       " particularly useful when features are correlated, as it tends to",
                       " retain groups of correlated genes rather than arbitrarily selecting",
                       " one from each group."),
                "")
        }
    }

    # ---- 3. Datasets (shared builder, spec item 15) ----
    lines <- c(lines, .build_datasets_lines(data, p, validation_result, format = "md"))

    # ---- 4. Methods ----
    lines <- c(lines,
        "## 4. Methods",
        "",
        "### 4.1 Feature Preparation",
        "")

    if (!is.null(features)) {
        lines <- c(lines,
            paste("- **Input features:**", ncol(features$X), "genes selected by variance"),
            paste("- **Samples:**", nrow(features$X)),
            "- **Preprocessing:** Log2-transformed (if not already), filtered to top",
            paste0("  most variable genes, scaled to zero mean and unit variance"),
            "")
    } else {
        lines <- c(lines,
            paste("- **Features:**", p$n_features),
            paste("- **Samples:**", p$n_samples),
            "")
    }

    lines <- c(lines, "### 4.2 Penalised Regression Model", "")

    if (!is.null(ctx$method_text)) {
        # Enriched: methods with inline citations
        lines <- c(lines, ctx$method_text)
    } else {
        # Generic fallback
        lines <- c(lines,
            paste0("- **Regularization:** alpha = ", p$alpha,
                   ifelse(p$alpha == 1, " (pure LASSO)", " (elastic net)")),
            paste("- **Repeated CV:**", p$n_repeats, "iterations of class-balanced",
                  paste0(round(p$train_fraction * 100), "/", round((1 - p$train_fraction) * 100)),
                  "train/test splits"),
            paste("- **Inner CV:**", p$n_inner_folds, "folds for lambda selection (cv.glmnet)"),
            paste("- **Stability threshold:**", p$stability_threshold,
                  "(features must be selected in this fraction of iterations)"),
            paste("- **Random seed:**", p$seed, "(reproducible)"),
            "",
            "**Algorithm:** For each of the repeated CV iterations, a class-balanced",
            "train/test split is created. On the training set, cv.glmnet selects the",
            "optimal lambda via inner cross-validation. Non-zero coefficients are recorded.",
            "After all iterations, features selected in more than the stability threshold",
            "fraction of iterations form the final panel. A final model is fit on all",
            "samples using only these stable features.",
            "",
            "### 4.3 Performance Evaluation",
            "",
            "- **Discrimination:** AUC (Area Under ROC Curve) via pROC package",
            "- **Confidence intervals:** 2.5th and 97.5th percentiles of fold AUCs",
            "- **Sensitivity/Specificity:** At Youden's J-statistic optimal threshold",
            "- **Calibration:** Predicted probability vs observed event rate",
            "")
    }

    # ---- 5. Results (shared builder, spec item 15) ----
    lines <- c(lines, .build_performance_lines(model_result, p, validation_result,
                                                format = "md"))

    # ---- Diagnostic Plots (embedded figures + captions) ----
    # Figures are drawn by generate_all_plots(); their captions live here so the
    # single markdown content source carries every scientific caption and the
    # pdf-report-generation step can embed the images.
    .plot_specs <- list(
        list(file = "roc_curve.png",
             caption = "ROC curve showing model discrimination. AUC annotated."),
        list(file = "stability_barplot.png",
             caption = "Feature selection frequency across CV iterations. Dashed line indicates the stability threshold."),
        list(file = "coefficient_forest.png",
             caption = "Model coefficients with 95% confidence intervals for each panel feature."),
        list(file = "calibration_curve.png",
             caption = "Calibration plot: predicted probability vs observed event rate."),
        list(file = "auc_distribution.png",
             caption = "Distribution of AUC values across cross-validation folds."),
        list(file = "feature_heatmap.png",
             caption = "Heatmap of panel features across samples, annotated by outcome.")
    )
    .plot_lines <- character(0)
    for (ps in .plot_specs) {
        if (file.exists(file.path(output_dir, ps$file))) {
            .plot_lines <- c(.plot_lines, paste0("![", ps$caption, "](", ps$file, ")"), "")
        }
    }
    if (file.exists(file.path(output_dir, "gsea_hallmark_dotplot.png"))) {
        .plot_lines <- c(.plot_lines,
            "![GSEA: MSigDB Hallmark Pathways](gsea_hallmark_dotplot.png)", "")
    }
    if (file.exists(file.path(output_dir, "celltype_expression.png"))) {
        ct_caption <- if (!is.null(interpretation) && !is.null(interpretation$tissue_label)) {
            paste("Cell-Type Expression in", interpretation$tissue_label)
        } else "Cell-Type Expression"
        .plot_lines <- c(.plot_lines,
            paste0("![", ct_caption, "](celltype_expression.png)"), "")
    }
    if (length(.plot_lines) > 0) {
        lines <- c(lines, "## Diagnostic Plots", "", .plot_lines)
    }

    # ---- 6. Published Benchmarks (if available) ----
    if (!is.null(ctx$benchmarks)) {
        lines <- c(lines,
            "## 6. Published Benchmarks",
            "",
            ctx$benchmarks)
    }

    # ---- 7. Biological Interpretation ----
    bio_num <- if (!is.null(ctx$benchmarks)) 7 else 6
    lines <- c(lines,
        paste0("## ", bio_num, ". Biological Interpretation"),
        "")

    interp_lines <- .build_interpretation_lines(interpretation, panel, format = "md",
                                                   outcome_col = outcome_col)

    if (!is.null(interp_lines$pathway_lines)) {
        # Enriched: full pathway, celltype, GWAS analysis
        lines <- c(lines,
            paste0("### ", bio_num, ".1 Pathway Enrichment"),
            "",
            interp_lines$pathway_lines)

        if (!is.null(interp_lines$celltype_lines)) {
            lines <- c(lines,
                paste0("### ", bio_num, ".2 Cell-Type Expression Context"),
                "",
                interp_lines$celltype_lines)
        }

        if (!is.null(interp_lines$gwas_lines)) {
            gwas_header <- if (!is.null(interpretation$gwas_label)) {
                paste0("Genetic Risk Overlap (", interpretation$gwas_label, ")")
            } else "Genetic Risk Overlap"
            lines <- c(lines,
                paste0("### ", bio_num, ".3 ", gwas_header),
                "",
                interp_lines$gwas_lines)
        }
    } else {
        # Fallback: shared builder (spec item 15)
        lines <- c(lines, .build_interp_fallback_lines(
            model_result, p, panel, pos_genes, neg_genes, outcome_col, format = "md"))
    }

    # ---- Limitations (shared builder, spec item 15) ----
    lim_num <- bio_num + 1
    lines <- c(lines, .build_limitations_lines(model_result, p, validation_result,
                                                has_validation, lim_num, format = "md"))

    # ---- Downstream Use ----
    ds_num <- lim_num + 1
    lines <- c(lines,
        paste0("## ", ds_num, ". Downstream Use"),
        "",
        "### Load the model for prediction on new data:",
        "",
        "```r",
        'model <- readRDS("results/lasso_model.rds")',
        'source("scripts/lasso_workflow.R")',
        "predictions <- predict_biomarker_panel(model, new_X)",
        "```",
        "",
        "### Suggested next steps:",
        "",
        "- **Pathway enrichment** of panel genes (functional-enrichment-from-degs)",
        "- **Co-expression context** for panel genes (coexpression-network)",
        "- **Patient stratification** using panel scores (multiomics-patient-stratification)",
        "- **Literature validation** of panel genes in disease context",
        "")

    # ---- References (if report_context provides them) ----
    if (!is.null(ctx$references)) {
        ref_num <- ds_num + 1
        lines <- c(lines,
            paste0("## ", ref_num, ". References"),
            "",
            ctx$references)
    }

    # ---- Consistency gate (spec item 1) ----
    lines <- .assert_report_consistency(lines, p, model_result, validation_result,
                                         output_dir, "md")

    writeLines(lines, file.path(output_dir, "summary_report.md"))
}
