# Export Experimental Design Functions
# Generate lab-ready files and documentation

# Guarded source: export_batch_validation() calls check_confounding(),
# check_balance(), and check_covariate_condition_balance() from
# batch_validation.R. Source it here so this script is self-contained
# and works even when sourced in isolation (not just in documented order).
if (!exists("check_confounding", mode = "function")) {
  source("scripts/batch_validation.R")
}

#' Coerce nullable values to safe strings for downstream text/CSV consumers
#'
#' Replaces NA/NaN/Inf/-Inf with a placeholder so that values exported to CSV
#' or passed to ReportLab Paragraph() do not crash downstream rendering.
#' Preserves non-NA numeric and character values unchanged.
#' @param x A vector, data frame, or list to sanitize
#' @return Same type with NA/NaN/Inf replaced by the placeholder string
.sanitize_na <- function(x, placeholder = "N/A") {
  if (is.data.frame(x)) {
    for (col in names(x)) {
      x[[col]] <- .sanitize_na(x[[col]], placeholder)
    }
    return(x)
  }
  if (is.list(x) && !is.data.frame(x)) {
    return(lapply(x, .sanitize_na, placeholder = placeholder))
  }
  if (is.numeric(x)) {
    x[is.na(x) | is.nan(x) | is.infinite(x)] <- NA  # normalize NaN/Inf to NA
    return(x)
  }
  if (is.character(x)) {
    x[is.na(x)] <- placeholder
    return(x)
  }
  x
}

#' Check if batch design uses demo/placeholder sample IDs
#' @param batch_assignment Data frame with batch assignments
#' @return TRUE if sample IDs look like demo placeholders (S01, S02, ...)
.is_demo_data <- function(batch_assignment) {
  if (!"sample_id" %in% colnames(batch_assignment)) return(FALSE)
  ids <- batch_assignment$sample_id
  # Demo data uses S01-S20 pattern from load_example_data()
  all(grepl("^S\\d{2}$", ids))
}

#' Compute the true sample-ID span from a set of IDs
#'
#' Returns the first and last IDs by numeric-aware order (so S2 < S10, robust
#' beyond the 2-digit demo IDs) plus the count. Used so any stated ID range
#' reflects the data actually written, not the first/last row of a batch-sorted
#' table.
#' @param ids Character vector of sample IDs
#' @return list(first, last, n)
.sample_id_span <- function(ids) {
  ids <- as.character(ids)
  num <- suppressWarnings(as.integer(gsub("\\D", "", ids)))
  ord <- if (length(ids) > 0 && !any(is.na(num))) order(num) else order(ids)
  sorted <- ids[ord]
  list(first = sorted[1], last = sorted[length(sorted)], n = length(ids))
}

#' Guard: a stated sample-ID range must match the exported layout
#'
#' Fails before export if the stated endpoints are not the true min/max of the
#' IDs in the data, or are not present in it. Catches any reintroduction of the
#' "first/last row of the batch-sorted table" bug.
#' @param stated_first,stated_last Endpoints about to be written
#' @param ids Sample IDs in the exported table
#' @return Invisible TRUE if consistent; otherwise stop()
.assert_id_range_matches <- function(stated_first, stated_last, ids) {
  span <- .sample_id_span(ids)
  if (!identical(as.character(stated_first), span$first) ||
      !identical(as.character(stated_last), span$last) ||
      !(stated_first %in% ids) || !(stated_last %in% ids)) {
    stop(sprintf(
      paste0("Stated sample-ID range %s-%s does not match the exported layout ",
             "(actual span %s-%s across %d IDs). The range must be derived from ",
             "the data, not from batch-sorted row order."),
      stated_first, stated_last, span$first, span$last, span$n))
  }
  invisible(TRUE)
}

#' Build design parameters from a recommendation object
#'
#' Derives all design parameters from the recommendation returned by
#' generate_design_recommendation(), the batch design, and optional pilot
#' result — rather than having the caller hand-type them. Applies the
#' FDR-aware-over-per-gene fallback rule internally: n_per_group is the
#' FDR-aware recommendation for the target power, falling back to per-gene
#' only when the FDR value is NA.
#'
#' @param recommendation List from generate_design_recommendation()
#' @param batch_design Data frame with batch assignments
#' @param assay Assay type string (default: "RNA-seq")
#' @param condition_var Name of the condition column (default: "condition")
#' @param multiple_testing Correction method (default: "BH-FDR")
#' @param pilot_result Optional list from samplesize_from_pilot()
#' @param extra Optional named list of extra parameters to merge in
#' @return A design_params list suitable for export_complete_design()
#' @export
build_design_params <- function(recommendation,
                                batch_design,
                                assay = "RNA-seq",
                                condition_var = "condition",
                                multiple_testing = "BH-FDR",
                                pilot_result = NULL,
                                extra = list()) {

  if (is.null(recommendation) || is.null(recommendation$parameters)) {
    stop("recommendation must be a list from generate_design_recommendation() with a $parameters field")
  }

  p <- recommendation$parameters
  tp <- p$target_power

  # FDR-aware-over-per-gene fallback rule
  fdr_n <- recommendation$fdr_required_n
  per_gene_n <- recommendation$per_gene_required_n

  if (tp >= 0.85) {
    rec_n <- fdr_n$power_90
    if (is.na(rec_n)) rec_n <- per_gene_n$power_90
    power_basis <- if (!is.na(fdr_n$power_90)) "FDR-aware RnaSeqSampleSize" else "per-gene RNASeqPower"
  } else {
    rec_n <- fdr_n$power_80
    if (is.na(rec_n)) rec_n <- per_gene_n$power_80
    power_basis <- if (!is.na(fdr_n$power_80)) "FDR-aware RnaSeqSampleSize" else "per-gene RNASeqPower"
  }

  if (is.na(rec_n)) stop("Could not determine n_per_group from recommendation — both FDR-aware and per-gene values are NA")

  # Derive conditions from batch_design
  conditions <- if (!is.null(batch_design) && condition_var %in% colnames(batch_design)) {
    unique(batch_design[[condition_var]])
  } else {
    c("untreated", "treated")
  }

  params <- list(
    assay = assay,
    conditions = conditions,
    n_per_group = rec_n,
    total_samples = if (!is.null(batch_design)) nrow(batch_design) else rec_n * length(conditions),
    power = tp,
    power_basis = power_basis,
    alpha = p$alpha,
    fdr = p$fdr,
    effect_size = p$target_fc,
    cv = p$cv,
    mean_count = p$mean_count,
    n_genes = p$n_genes,
    pi0 = p$pi0,
    library_depth_M = p$library_depth_M,
    multiple_testing = multiple_testing,
    dispersion = p$dispersion,
    power_table = recommendation$power_table
  )

  # Merge pilot info if available
  if (!is.null(pilot_result) && !is.null(pilot_result$pilot_parameters)) {
    params$pilot_cv <- pilot_result$pilot_parameters$pilot_cv
    params$pilot_dispersion <- pilot_result$pilot_parameters$median_dispersion
    params$pilot_mean_count <- pilot_result$pilot_parameters$mean_count
  }

  # Merge extra parameters
  for (key in names(extra)) {
    params[[key]] <- extra[[key]]
  }

  return(params)
}

#' Validate consistency between design_params and the recommendation that produced it
#'
#' Checks that the exported plan describes the design that was actually computed.
#' On any mismatch, stop() with a table of expected / found / source-field.
#' strict = FALSE downgrades to a warning plus a written report, but the
#' default must halt.
#'
#' @param design_params List built by build_design_params() or hand-constructed
#' @param recommendation List from generate_design_recommendation()
#' @param batch_design Data frame with batch assignments
#' @param pilot_result Optional pilot result list
#' @param strict If TRUE (default), halt on mismatch; if FALSE, warn and write report
#' @return Invisible TRUE if all checks pass
#' @export
validate_design_consistency <- function(design_params,
                                        recommendation,
                                        batch_design,
                                        pilot_result = NULL,
                                        strict = TRUE) {

  mismatches <- data.frame(
    check = character(),
    expected = character(),
    found = character(),
    source_field = character(),
    stringsAsFactors = FALSE
  )

  p <- recommendation$parameters
  tp <- p$target_power

  # Check 1: n_per_group equals FDR-aware recommendation, fallback to per-gene
  expected_n <- if (tp >= 0.85) {
    fdr_n <- recommendation$fdr_required_n$power_90
    if (is.na(fdr_n)) recommendation$per_gene_required_n$power_90 else fdr_n
  } else {
    fdr_n <- recommendation$fdr_required_n$power_80
    if (is.na(fdr_n)) recommendation$per_gene_required_n$power_80 else fdr_n
  }
  if (!is.na(expected_n) && design_params$n_per_group != expected_n) {
    mismatches <- rbind(mismatches, data.frame(
      check = "n_per_group matches FDR-aware recommendation",
      expected = as.character(expected_n),
      found = as.character(design_params$n_per_group),
      source_field = "recommendation$fdr_required_n / $per_gene_required_n",
      stringsAsFactors = FALSE
    ))
  }

  # Check 2: cv, effect_size, alpha, fdr, n_genes, power match recommendation$parameters
  param_checks <- list(
    list(name = "cv", dp = design_params$cv, rec = p$cv),
    list(name = "effect_size", dp = design_params$effect_size, rec = p$target_fc),
    list(name = "alpha", dp = design_params$alpha, rec = p$alpha),
    list(name = "fdr", dp = design_params$fdr, rec = p$fdr),
    list(name = "n_genes", dp = design_params$n_genes, rec = p$n_genes),
    list(name = "power", dp = design_params$power, rec = p$target_power)
  )
  for (chk in param_checks) {
    if (!is.null(chk$dp) && !is.null(chk$rec) && !identical(chk$dp, chk$rec)) {
      mismatches <- rbind(mismatches, data.frame(
        check = paste0(chk$name, " matches recommendation"),
        expected = as.character(chk$rec),
        found = as.character(chk$dp),
        source_field = paste0("recommendation$parameters$", chk$name),
        stringsAsFactors = FALSE
      ))
    }
  }

  # Check 3: nrow(batch_design) == n_per_group * length(conditions)
  expected_rows <- design_params$n_per_group * length(design_params$conditions)
  if (!is.null(batch_design) && nrow(batch_design) != expected_rows) {
    mismatches <- rbind(mismatches, data.frame(
      check = "batch_design row count matches n_per_group * conditions",
      expected = as.character(expected_rows),
      found = as.character(nrow(batch_design)),
      source_field = "nrow(batch_design) vs design_params$n_per_group * length(conditions)",
      stringsAsFactors = FALSE
    ))
  }

  # Check 4: condition levels in batch_design match design_params$conditions
  if (!is.null(batch_design) && "condition" %in% colnames(batch_design)) {
    bd_conditions <- unique(batch_design$condition)
    if (!setequal(bd_conditions, design_params$conditions)) {
      mismatches <- rbind(mismatches, data.frame(
        check = "condition levels match",
        expected = paste(sort(design_params$conditions), collapse = ", "),
        found = paste(sort(bd_conditions), collapse = ", "),
        source_field = "batch_design$condition vs design_params$conditions",
        stringsAsFactors = FALSE
      ))
    }
  }

  # Check 5: total_samples equals nrow(batch_design) if present
  if (!is.null(design_params$total_samples) && !is.null(batch_design)) {
    if (design_params$total_samples != nrow(batch_design)) {
      mismatches <- rbind(mismatches, data.frame(
        check = "total_samples matches batch_design rows",
        expected = as.character(nrow(batch_design)),
        found = as.character(design_params$total_samples),
        source_field = "design_params$total_samples vs nrow(batch_design)",
        stringsAsFactors = FALSE
      ))
    }
  }

  # Check 6: pilot CV not silently different
  if (!is.null(pilot_result) && !is.null(pilot_result$pilot_parameters)) {
    pilot_cv <- pilot_result$pilot_parameters$pilot_cv
    if (!is.null(design_params$cv) && !is.null(pilot_cv)) {
      if (abs(design_params$cv - pilot_cv) > 0.01) {
        mismatches <- rbind(mismatches, data.frame(
          check = "design_params$cv matches pilot CV",
          expected = sprintf("%.4f", pilot_cv),
          found = sprintf("%.4f", design_params$cv),
          source_field = "pilot_result$pilot_parameters$pilot_cv",
          stringsAsFactors = FALSE
        ))
      }
    }
  }

  # Check 7: exported power-table grid spans the headline n
  # The report leads with design_params$n_per_group; a reader verifies it against
  # power_analysis_results.csv and design_parameters.json$power_table, both of
  # which serialize design_params$power_table. Halt before export if the headline
  # n is not a row in that grid, or if the grid stops below it.
  if (!is.null(design_params$power_table) &&
      !is.null(design_params$power_table$n) &&
      !is.null(design_params$n_per_group) &&
      !is.na(design_params$n_per_group)) {
    grid_n <- design_params$power_table$n
    hn <- design_params$n_per_group
    if (!(hn %in% grid_n)) {
      mismatches <- rbind(mismatches, data.frame(
        check = "headline n present as a row in exported power table",
        expected = sprintf("n = %s among grid rows", hn),
        found = sprintf("grid n = {%s}", paste(sort(unique(grid_n)), collapse = ", ")),
        source_field = "design_params$power_table$n vs design_params$n_per_group",
        stringsAsFactors = FALSE
      ))
    }
    if (max(grid_n) < hn) {
      mismatches <- rbind(mismatches, data.frame(
        check = "exported power-table grid reaches the headline n",
        expected = sprintf("max grid n >= %s", hn),
        found = sprintf("max grid n = %s", max(grid_n)),
        source_field = "max(design_params$power_table$n) vs design_params$n_per_group",
        stringsAsFactors = FALSE
      ))
    }
  }

  if (nrow(mismatches) > 0) {
    msg <- "=== DESIGN CONSISTENCY VALIDATION FAILED ===\n"
    for (i in seq_len(nrow(mismatches))) {
      msg <- paste0(msg, sprintf("  CHECK: %s\n    expected: %s | found: %s | source: %s\n",
                                 mismatches$check[i], mismatches$expected[i],
                                 mismatches$found[i], mismatches$source_field[i]))
    }
    if (strict) {
      stop(msg)
    } else {
      warning(msg)
      writeLines(msg, "design_consistency_report.txt")
      message("Design consistency report written to design_consistency_report.txt")
    }
  }

  invisible(TRUE)
}

#' Export batch assignment layout for lab use
#'
#' @param batch_assignment Data frame with batch assignments
#' @param output_file Output CSV file path
#' @export
export_batch_layout <- function(batch_assignment, output_file = "batch_layout_for_lab.csv") {

  # Reorder columns for lab convenience
  priority_cols <- c("sample_id", "batch", "condition", "processing_order",
                     "plate", "well", "overall_sequence")

  # Get columns that exist
  existing_priority <- priority_cols[priority_cols %in% colnames(batch_assignment)]
  other_cols <- setdiff(colnames(batch_assignment), existing_priority)

  # Reorder
  export_data <- batch_assignment[, c(existing_priority, other_cols)]

  # Sort by batch and processing order (if available)
  if ("batch" %in% colnames(export_data)) {
    if ("processing_order" %in% colnames(export_data)) {
      export_data <- export_data[order(export_data$batch, export_data$processing_order), ]
    } else {
      export_data <- export_data[order(export_data$batch), ]
    }
  }

  # Check for demo/placeholder data
  is_demo <- .is_demo_data(export_data)

  # Derive the example-ID range from the data actually written (numeric-aware
  # span of the IDs), NOT from the first/last row of the batch-sorted table.
  id_range <- NULL
  if (is_demo) {
    span <- .sample_id_span(export_data$sample_id)
    id_range <- sprintf("%s-%s", span$first, span$last)
    # Guard: fail before writing if the stated range does not match the data.
    .assert_id_range_matches(span$first, span$last, export_data$sample_id)
  }

  if (is_demo) {
    # Write CSV with template header comment (dynamic ID range)
    header_lines <- c(
      sprintf("# TEMPLATE - This batch layout uses EXAMPLE sample IDs (%s)", id_range),
      "# Replace sample_id column with your actual sample identifiers before lab use"
    )
    writeLines(c(header_lines, readLines(textConnection(
      capture.output(write.csv(export_data, stdout(), row.names = FALSE))
    ))), output_file)
  } else {
    write.csv(export_data, output_file, row.names = FALSE)
  }

  message(paste0("Batch layout exported to: ", output_file))
  if (is_demo) {
    message(sprintf("  \u26a0\ufe0f  TEMPLATE: Uses example sample IDs (%s).", id_range))
    message("     Replace with your actual sample IDs before using in the lab.")
  }
  message(paste0("Samples: ", nrow(export_data)))
  message(paste0("Batches: ", length(unique(export_data$batch))))

  # Return invisibly
  invisible(export_data)
}


#' Export statistical analysis plan
#'
#' @param design_params List with experimental design parameters
#' @param output_file Output markdown file path
#' @export
export_statistical_plan <- function(design_params, output_file = "statistical_analysis_plan.md") {

  # Derive total samples from design_params rather than recomputing
  total_samples <- if (!is.null(design_params$total_samples)) {
    design_params$total_samples
  } else {
    design_params$n_per_group * length(design_params$conditions)
  }

  # Create markdown document
  plan_text <- paste0(
    "# Statistical Analysis Plan\n\n",
    "**Generated:** ", format(Sys.time(), "%Y-%m-%d %H:%M:%S"), "\n\n",
    "---\n\n",
    "## Experimental Design\n\n",
    "**Assay Type:** ", design_params$assay, "\n\n",
    "**Experimental Groups:**\n",
    "- ", paste(design_params$conditions, collapse = "\n- "), "\n\n",
    "**Sample Size:** ", design_params$n_per_group, " biological replicates per group (",
    total_samples, " total samples)\n\n"
  )

  # Add batch information if present
  if (!is.null(design_params$batches)) {
    plan_text <- paste0(
      plan_text,
      "**Batch Structure:** ", design_params$batches, " processing batches\n",
      "- Each batch contains all experimental conditions to prevent confounding\n\n"
    )
  }

  # Add power analysis results — relabel and add basis
  if (!is.null(design_params$power)) {
    power_basis <- if (!is.null(design_params$power_basis)) design_params$power_basis else "not stated"
    plan_text <- paste0(
      plan_text,
      "## Power Analysis\n\n",
      "**Target statistical power:** ", round(design_params$power, 3), "\n",
      "**Power basis:** ", power_basis, "\n",
      "**Minimum Detectable Effect:** ", design_params$effect_size, "-fold change\n",
      "**Significance Threshold:** α = ", design_params$alpha, "\n\n"
    )
  }

  # Add key design parameters so the plan is checkable against design_parameters.json
  plan_text <- paste0(plan_text, "## Key Design Parameters\n\n")
  if (!is.null(design_params$cv)) {
    cv_source <- if (!is.null(design_params$tissue_type)) design_params$tissue_type else "assumed / pilot-derived"
    plan_text <- paste0(plan_text, "**Coefficient of variation (CV):** ", design_params$cv, " (source: ", cv_source, ")\n")
  }
  if (!is.null(design_params$mean_count)) {
    plan_text <- paste0(plan_text, "**Mean count per gene (lambda0):** ", design_params$mean_count, "\n")
  }
  if (!is.null(design_params$n_genes)) {
    plan_text <- paste0(plan_text, "**Number of genes tested:** ", format(design_params$n_genes, big.mark = ","), "\n")
  }
  if (!is.null(design_params$pi0)) {
    plan_text <- paste0(plan_text, "**Assumed DE proportion:** ", sprintf("%.0f%%", (1 - design_params$pi0) * 100),
                        " (pi0 = ", design_params$pi0, ")\n")
  }
  if (!is.null(design_params$dispersion)) {
    plan_text <- paste0(plan_text, "**Dispersion (phi):** ", round(design_params$dispersion, 4), "\n")
  }
  plan_text <- paste0(plan_text, "\n")

  # Build model formula from design parameters
  formula_parts <- character()
  if (!is.null(design_params$batches) && design_params$batches > 1) {
    formula_parts <- c(formula_parts, "batch")
  }
  if (!is.null(design_params$covariates)) {
    formula_parts <- c(formula_parts, design_params$covariates)
  }
  formula_parts <- c(formula_parts, "condition")
  model_formula <- paste0("~ ", paste(formula_parts, collapse = " + "))

  # Derive FDR threshold from design_params$fdr instead of hard-coded 0.05
  fdr_threshold <- if (!is.null(design_params$fdr)) design_params$fdr else 0.05

  # Add statistical methods
  plan_text <- paste0(
    plan_text,
    "## Statistical Methods\n\n",
    "### Differential Expression Analysis\n",
    "- **Method:** DESeq2 (negative binomial model)\n",
    "- **Model formula:** `", model_formula, "`\n",
    "- **Significance threshold:** α = ", design_params$alpha, "\n",
    "- **Multiple testing correction:** ", design_params$multiple_testing, "\n",
    "- **FDR threshold:** < ", fdr_threshold, "\n",
    "- **Alternative correction:** Independent Hypothesis Weighting (IHW) for ~5-10% more discoveries at same FDR\n",
    "  - Weights p-values by an independent covariate (e.g., mean expression)\n",
    "  - Recommended when testing >5,000 features\n\n"
  )

  # Add batch correction if needed
  if (!is.null(design_params$batches) && design_params$batches > 1) {
    plan_text <- paste0(
      plan_text,
      "### Batch Effect Correction\n",
      "- **Design:** Batch included in DESeq2 design formula\n",
      "- **Formula:** `", model_formula, "`\n",
      "- Include batch as a covariate to account for batch effects. The batch design ensures batch and condition are not confounded, making this correction valid.\n",
      "- **Alternative methods if needed:** SVA, ComBat, RUVSeq\n\n"
    )
  }

  # Derive library-size QC from design_params$library_depth_M when present
  lib_qc <- if (!is.null(design_params$library_depth_M)) {
    sprintf("> %g million reads per sample (assumed library depth: %gM)", design_params$library_depth_M, design_params$library_depth_M)
  } else {
    "> 10 million reads per sample (default threshold — no library depth was specified in the design)"
  }

  # Add quality control criteria
  plan_text <- paste0(
    plan_text,
    "## Quality Control Criteria\n\n",
    "### Sample-level QC\n",
    "- **Library size:** ", lib_qc, "\n",
    "- **Alignment rate:** > 80%\n",
    "- **rRNA contamination:** < 10%\n",
    "- **Sample correlation:** > 0.8 within groups\n\n",
    "### Gene-level QC\n",
    "- **Minimum expression:** CPM > 1 in at least n samples per group\n",
    "- **Filter low-count genes** before differential expression\n\n"
  )

  # Add analysis workflow
  plan_text <- paste0(
    plan_text,
    "## Analysis Workflow\n\n",
    "1. **Quality Control**\n",
    "   - FastQC on raw reads\n",
    "   - Alignment QC (STAR/HISAT2)\n",
    "   - Check library sizes and alignment rates\n\n",
    "2. **Normalization**\n",
    "   - DESeq2 size factor normalization\n",
    "   - Alternative: TMM normalization (edgeR)\n\n",
    "3. **Batch Effect Assessment**\n",
    "   - PCA visualization\n",
    "   - Check for batch-condition confounding\n",
    "   - Apply correction if needed\n\n",
    "4. **Differential Expression**\n",
    "   - DESeq2 analysis\n",
    "   - Multiple testing correction: ", design_params$multiple_testing, "\n",
    "   - Volcano plots and MA plots\n\n",
    "5. **Downstream Analysis**\n",
    "   - GO enrichment analysis\n",
    "   - GSEA pathway analysis\n",
    "   - Validation of top hits\n\n"
  )

  # Reproducibility — drop invented "Random seed: 42" unless a seed was actually used
  seed_line <- if (!is.null(design_params$seed)) {
    paste0("- **Random seed:** Set to ", design_params$seed, " for all analyses\n")
  } else {
    "- **Random seed:** Not specified in the design — set and record one before analysis\n"
  }

  plan_text <- paste0(
    plan_text,
    "## Reproducibility\n\n",
    seed_line,
    "- **Software versions:** Record all R/Bioconductor package versions\n",
    "- **Code repository:** All analysis code version controlled\n",
    "- **Data archiving:** Raw data archived with appropriate accession (GEO/SRA)\n\n",
    "---\n\n",
    "*This statistical analysis plan was generated using the experimental-design-statistics workflow.*\n"
  )

  # Write to file
  writeLines(plan_text, output_file)

  message(paste0("Statistical analysis plan exported to: ", output_file))

  invisible(plan_text)
}


#' Generate lab protocol checklist
#'
#' @param batch_design Data frame with batch assignments (optional)
#' @param metadata_to_record Vector of metadata fields to record
#' @param output_file Output markdown file path
#' @export
generate_lab_protocol <- function(batch_design = NULL,
                                  metadata_to_record = c("date", "operator", "reagent_lots", "instrument"),
                                  output_file = "lab_protocol_checklist.md") {

  protocol_text <- paste0(
    "# Lab Protocol Checklist\n\n",
    "**Generated:** ", format(Sys.time(), "%Y-%m-%d %H:%M:%S"), "\n\n",
    "---\n\n",
    "## Sample Processing Protocol\n\n",
    "Follow this checklist for each processing batch to ensure proper documentation and prevent batch effects.\n\n"
  )

  if (!is.null(batch_design)) {
    n_batches <- length(unique(batch_design$batch))
    protocol_text <- paste0(
      protocol_text,
      "**Total Samples:** ", nrow(batch_design), "\n",
      "**Number of Batches:** ", n_batches, "\n",
      "**Batch Assignment File:** batch_layout_for_lab.csv\n\n"
    )
  }

  protocol_text <- paste0(
    protocol_text,
    "## Before Starting\n\n",
    "- [ ] Review batch assignment file\n",
    "- [ ] Verify all samples are available\n",
    "- [ ] Prepare reagents and check expiration dates\n",
    "- [ ] Record reagent lot numbers\n",
    "- [ ] Calibrate instruments if needed\n",
    "- [ ] Randomize sample positions within plates\n\n",
    "## For Each Batch\n\n",
    "### Documentation (CRITICAL)\n\n",
    "Record the following for each batch:\n\n"
  )

  # Add metadata fields
  metadata_checklist <- c(
    "date" = "- [ ] **Processing date:** YYYY-MM-DD\n",
    "operator" = "- [ ] **Operator/technician:** Name\n",
    "reagent_lots" = "- [ ] **Reagent lot numbers:** All lots used\n",
    "instrument" = "- [ ] **Instrument/equipment ID:** \n",
    "plate" = "- [ ] **Plate/chip barcode:** \n",
    "library_prep" = "- [ ] **Library prep kit:** Name and lot\n",
    "protocol_deviations" = "- [ ] **Protocol deviations:** Note any changes\n"
  )

  for (field in metadata_to_record) {
    if (field %in% names(metadata_checklist)) {
      protocol_text <- paste0(protocol_text, metadata_checklist[[field]])
    }
  }

  protocol_text <- paste0(
    protocol_text,
    "\n### Sample Processing\n\n",
    "- [ ] Samples processed according to batch assignment\n",
    "- [ ] Randomized within batch (avoid systematic patterns)\n",
    "- [ ] Avoid plate edge wells when possible\n",
    "- [ ] Include positive and negative controls\n",
    "- [ ] Record any sample failures or issues\n\n",
    "### Quality Checks During Processing\n\n",
    "- [ ] RNA quality (RIN score if applicable)\n",
    "- [ ] Concentration measurements\n",
    "- [ ] Visual inspection for contamination\n",
    "- [ ] Volume checks\n\n",
    "### Post-Processing\n\n",
    "- [ ] Library QC (Bioanalyzer/TapeStation)\n",
    "- [ ] Quantification (Qubit)\n",
    "- [ ] Pooling calculations\n",
    "- [ ] Submit for sequencing\n\n",
    "## Critical Rules\n\n",
    "1. **NEVER process all samples from one condition in a single batch**\n",
    "   - Each batch must contain samples from all experimental conditions\n",
    "   - This prevents confounding of batch with biological effects\n\n",
    "2. **Document everything**\n",
    "   - Better to record too much than too little\n",
    "   - Metadata essential for diagnosing batch effects later\n\n",
    "3. **Randomize when possible**\n",
    "   - Randomize sample order within batches\n",
    "   - Randomize plate positions\n",
    "   - Avoid processing samples in same order as conditions\n\n",
    "4. **Use controls**\n",
    "   - Process same control sample(s) in each batch\n",
    "   - Enables batch effect correction later\n\n",
    "## Troubleshooting\n\n",
    "**Sample failure:**\n",
    "- Document which sample(s) failed and why\n",
    "- Do NOT replace with different sample from same condition\n",
    "- If replacement needed, process in new batch or proportionally across batches\n\n",
    "**Batch delay:**\n",
    "- Record date and duration of delay\n",
    "- Note any storage condition changes\n",
    "- Document as potential batch covariate\n\n",
    "**Protocol deviation:**\n",
    "- Record exact deviation\n",
    "- Note which samples affected\n",
    "- Discuss with statistician if major deviation\n\n",
    "---\n\n",
    "*This checklist was generated using the experimental-design-statistics workflow.*\n"
  )

  # Write to file
  writeLines(protocol_text, output_file)

  message(paste0("Lab protocol checklist exported to: ", output_file))

  invisible(protocol_text)
}


#' Export sample size recommendation as a text file
#'
#' Writes the recommendation produced by generate_design_recommendation() (from
#' scripts/power_rnaseq.R) to sample_size_recommendation.txt. If a
#' samplesize_from_pilot() result is supplied, its FDR-aware estimate is
#' prepended so the file records both the pilot-based and the per-gene/FDR-aware
#' recommendation.
#'
#' @param recommendation List returned by generate_design_recommendation()
#' @param output_file Output text file path
#' @param pilot_result Optional list returned by samplesize_from_pilot() (or
#'   calc_samplesize_de()); when provided its FDR-aware n is recorded at the top
#' @export
export_sample_size_recommendation <- function(recommendation,
                                              output_file = "sample_size_recommendation.txt",
                                              pilot_result = NULL) {

  lines <- character()

  # Optional pilot-based FDR-aware header
  if (!is.null(pilot_result)) {
    lines <- c(lines, "SAMPLE SIZE RECOMMENDATION")
    lines <- c(lines, "==========================")
    lines <- c(lines, paste0("Generated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S")))

    # Read pilot parameters from $pilot_parameters (where samplesize_from_pilot
    # actually returns them), not the top-level fields that were dead branches
    pp <- pilot_result$pilot_parameters
    if (!is.null(pp)) {
      if (!is.null(pp$median_dispersion)) {
        lines <- c(lines, paste0("Median dispersion: ", round(pp$median_dispersion, 4)))
      }
      if (!is.null(pp$pilot_cv)) {
        lines <- c(lines, paste0("Median CV: ", round(pp$pilot_cv, 4)))
      }
      if (!is.null(pp$mean_count)) {
        lines <- c(lines, paste0("Mean count per gene: ", round(pp$mean_count, 2)))
      }
      if (!is.null(pp$n_genes)) {
        lines <- c(lines, paste0("Genes: ", pp$n_genes))
      }
    }
    lines <- c(lines, "")
    fdr_n <- pilot_result$required_n_per_group
    if (is.null(fdr_n) && !is.null(pilot_result$required_n)) fdr_n <- pilot_result$required_n
    if (!is.null(fdr_n)) {
      lines <- c(lines, sprintf("FDR-aware sample size (samplesize_from_pilot): n = %d per group (%d total)",
                                fdr_n, fdr_n * 2))
      lines <- c(lines, "  Method: RnaSeqSampleSize (FDR-aware, pilot-derived dispersion)")
      if (!is.null(pp) && !is.null(pp$median_dispersion)) {
        lines <- c(lines, paste0("  Pilot median dispersion: ", round(pp$median_dispersion, 4)))
      }
      if (!is.null(pp) && !is.null(pp$pilot_cv)) {
        lines <- c(lines, paste0("  Pilot CV: ", round(pp$pilot_cv, 4)))
      }
    }
    lines <- c(lines, "")
  }

  # Append the full generate_design_recommendation() text
  if (!is.null(recommendation) && !is.null(recommendation$recommendation_text)) {
    lines <- c(lines, recommendation$recommendation_text)
  } else if (is.character(recommendation)) {
    lines <- c(lines, recommendation)
  }

  writeLines(lines, output_file)
  message(paste0("Sample size recommendation exported to: ", output_file))

  invisible(output_file)
}


#' Export batch design validation report as a text file
#'
#' Runs check_confounding() and (optionally) check_balance() /
#' check_covariate_condition_balance() from scripts/batch_validation.R and
#' captures their console output into batch_design_validation.txt.
#'
#' @param batch_design Data frame with batch assignments
#' @param condition_var Name of the condition column
#' @param covariates Optional vector of covariate column names
#' @param output_file Output text file path
#' @export
export_batch_validation <- function(batch_design,
                                    condition_var = "condition",
                                    covariates = NULL,
                                    output_file = "batch_design_validation.txt") {

  # Capture all console output from the validation functions
  report_lines <- character()

  confounding_check <- check_confounding(batch_design, condition_var)

  report_lines <- c(report_lines, "BATCH DESIGN VALIDATION REPORT")
  report_lines <- c(report_lines, "===============================")
  report_lines <- c(report_lines, paste0("Generated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S")))
  report_lines <- c(report_lines, paste0("Total samples: ", nrow(batch_design)))
  report_lines <- c(report_lines, paste0("Number of batches: ", length(unique(batch_design$batch))))
  if (!is.null(covariates)) {
    report_lines <- c(report_lines, paste0("Balanced variables: ", paste(covariates, collapse = ", ")))
  }
  report_lines <- c(report_lines, "")

  report_lines <- c(report_lines, "=== CRITICAL: Confounding Check ===")
  report_lines <- c(report_lines, confounding_check$message)
  report_lines <- c(report_lines, paste0("Test: ", confounding_check$test_name))
  report_lines <- c(report_lines, paste0("P-value: ", format(confounding_check$p_value, digits = 4)))
  report_lines <- c(report_lines, paste0("Status: ", confounding_check$status))
  report_lines <- c(report_lines, "")

  # Covariate-condition balance
  if (!is.null(covariates)) {
    covar_cond <- check_covariate_condition_balance(batch_design, condition_var, covariates)
    report_lines <- c(report_lines, "=== Covariate-Condition Balance ===")
    report_lines <- c(report_lines, "")
    for (i in seq_len(nrow(covar_cond))) {
      report_lines <- c(report_lines, sprintf("%s: %s (p = %.4f, %s)",
                                              covar_cond$covariate[i],
                                              covar_cond$interpretation[i],
                                              covar_cond$p_value[i],
                                              covar_cond$test_used[i]))
    }
    report_lines <- c(report_lines, "")

    # Covariate-batch balance
    balance_check <- check_balance(batch_design, covariates)
    report_lines <- c(report_lines, "=== Covariate-Batch Balance ===")
    report_lines <- c(report_lines, "")
    for (i in seq_len(nrow(balance_check))) {
      report_lines <- c(report_lines, sprintf("%s: %s (p = %.4f, %s)",
                                              balance_check$covariate[i],
                                              balance_check$interpretation[i],
                                              balance_check$p_value[i],
                                              balance_check$test_used[i]))
    }
    report_lines <- c(report_lines, "")
  }

  # Contingency table
  report_lines <- c(report_lines, "=== Batch x Condition Contingency Table ===")
  report_lines <- c(report_lines, "")
  cont_table <- table(batch_design$batch, batch_design[[condition_var]])
  report_lines <- c(report_lines, capture.output(print(cont_table)))
  report_lines <- c(report_lines, "")

  # Overall assessment
  report_lines <- c(report_lines, "=== Overall Assessment ===")
  report_lines <- c(report_lines, "")
  if (confounding_check$status == "PASSED") {
    report_lines <- c(report_lines, "PASS: No batch-condition confounding detected.")
    report_lines <- c(report_lines, "Batch effects can be separated from biological effects.")
  } else {
    report_lines <- c(report_lines, "FAIL: Batch-condition confounding detected.")
    report_lines <- c(report_lines, "DO NOT use this design. Regenerate batch assignment.")
  }

  writeLines(report_lines, output_file)
  message(paste0("Batch design validation exported to: ", output_file))

  invisible(output_file)
}


#' Export all design files in one call
#'
#' @param batch_design Data frame with batch assignments
#' @param design_params List with experimental design parameters
#' @param output_dir Directory for output files (default: current directory)
#' @param recommendation Optional list from generate_design_recommendation()
#'   (scripts/power_rnaseq.R). When provided, sample_size_recommendation.txt is
#'   generated automatically.
#' @param pilot_result Optional list from samplesize_from_pilot()
#'   (scripts/sample_size_de.R). When provided alongside recommendation, the
#'   FDR-aware pilot estimate is recorded at the top of
#'   sample_size_recommendation.txt.
#' @param condition_var Name of the condition column for batch validation
#'   (default: "condition")
#' @param covariates Optional vector of covariate column names for batch
#'   validation. When provided, batch_design_validation.txt includes
#'   covariate-balance checks.
#' @export
export_complete_design <- function(batch_design,
                                   design_params,
                                   output_dir = ".",
                                   recommendation = NULL,
                                   pilot_result = NULL,
                                   condition_var = "condition",
                                   covariates = NULL) {

  # Create output directory if needed
  if (!dir.exists(output_dir)) {
    dir.create(output_dir, recursive = TRUE)
  }

  message("\n=== Exporting Complete Experimental Design ===\n")

  # --- Consistency gate: validate BEFORE any file is written ---
  # This is the executable form of "do claims about generated outputs match
  # the artifacts that were actually produced?" It halts (strict=TRUE) if the
  # exported plan would describe a design different from the one computed.
  if (!is.null(recommendation)) {
    validate_design_consistency(design_params, recommendation, batch_design,
                                pilot_result = pilot_result, strict = TRUE)
    message("Design consistency validation passed.")
  }

  # Export batch layout
  batch_file <- file.path(output_dir, "batch_layout_for_lab.csv")
  export_batch_layout(batch_design, batch_file)

  # Export statistical plan
  plan_file <- file.path(output_dir, "statistical_analysis_plan.md")
  export_statistical_plan(design_params, plan_file)

  # Export lab protocol
  protocol_file <- file.path(output_dir, "lab_protocol_checklist.md")
  generate_lab_protocol(batch_design, output_file = protocol_file)

  # Save design objects as RDS for downstream use (CRITICAL)
  batch_rds <- file.path(output_dir, "batch_design.rds")
  saveRDS(batch_design, batch_rds)
  message(paste0("Saved: ", batch_rds))
  message("  (Load with: batch_design <- readRDS('batch_design.rds'))")

  params_rds <- file.path(output_dir, "design_parameters.rds")
  saveRDS(design_params, params_rds)
  message(paste0("Saved: ", params_rds))
  message("  (Load with: design_params <- readRDS('design_parameters.rds'))")

  # Export power analysis results CSV if power table available
  power_csv <- NULL
  if (!is.null(design_params$power_table)) {
    power_csv <- file.path(output_dir, "power_analysis_results.csv")
    # Coerce NA/NaN/Inf to safe strings so downstream consumers (e.g. ReportLab
    # Paragraph()) do not crash on raw NaN/None values read from the CSV.
    power_table_safe <- .sanitize_na(design_params$power_table)
    write.csv(power_table_safe, power_csv, row.names = FALSE, na = "N/A")
    message(paste0("Saved: ", power_csv))
  }

  # Also save parameters as JSON for easier reading
  params_json <- file.path(output_dir, "design_parameters.json")
  if (requireNamespace("jsonlite", quietly = TRUE)) {
    jsonlite::write_json(design_params, params_json, pretty = TRUE, auto_unbox = TRUE)
    message(paste0("Saved: ", params_json))
  } else {
    # Fallback: save as text representation if jsonlite not available
    writeLines(capture.output(str(design_params)), params_json)
    message(paste0("Saved: ", params_json, " (as text format)"))
  }

  # Export sample size recommendation (if recommendation provided)
  rec_file <- NULL
  if (!is.null(recommendation)) {
    rec_file <- file.path(output_dir, "sample_size_recommendation.txt")
    export_sample_size_recommendation(recommendation, rec_file, pilot_result = pilot_result)
  }

  # --- Sensitivity CSVs: written from recommendation fields, never hand-typed ---
  # Package-owned emission removes the hand-labelling step that previously
  # produced a column headed 'depth_M' holding per-gene counts.
  cv_sens_file <- NULL
  de_sens_file <- NULL
  mc_sens_file <- NULL
  if (!is.null(recommendation)) {
    if (!is.null(recommendation$cv_sensitivity) &&
        nrow(recommendation$cv_sensitivity) > 0) {
      cv_sens_file <- file.path(output_dir, "cv_sensitivity.csv")
      write.csv(.sanitize_na(recommendation$cv_sensitivity), cv_sens_file,
                row.names = FALSE, na = "N/A")
      message(paste0("Saved: ", cv_sens_file))
    }
    if (!is.null(recommendation$pi0_sensitivity) &&
        nrow(recommendation$pi0_sensitivity) > 0) {
      de_sens_file <- file.path(output_dir, "de_proportion_sensitivity.csv")
      write.csv(.sanitize_na(recommendation$pi0_sensitivity), de_sens_file,
                row.names = FALSE, na = "N/A")
      message(paste0("Saved: ", de_sens_file))
    }
    if (!is.null(recommendation$mean_count_sensitivity) &&
        nrow(recommendation$mean_count_sensitivity) > 0) {
      mc_sens_file <- file.path(output_dir, "mean_count_sensitivity.csv")
      write.csv(.sanitize_na(recommendation$mean_count_sensitivity), mc_sens_file,
                row.names = FALSE, na = "N/A")
      message(paste0("Saved: ", mc_sens_file))
    }
  }

  # Export batch design validation report
  val_file <- file.path(output_dir, "batch_design_validation.txt")
  export_batch_validation(batch_design, condition_var = condition_var,
                          covariates = covariates, output_file = val_file)

  # Check if demo data
  is_demo <- .is_demo_data(batch_design)

  message("\n=== Export Complete ===")
  message("Files created:")
  if (is_demo) {
    message(paste0("  1. ", batch_file, " - TEMPLATE batch assignments (replace sample IDs with your own)"))
  } else {
    message(paste0("  1. ", batch_file, " - Lab-ready sample assignments"))
  }
  message(paste0("  2. ", plan_file, " - Pre-registration analysis plan"))
  message(paste0("  3. ", protocol_file, " - Lab processing checklist"))
  message(paste0("  4. ", batch_rds, " - Batch design object (for downstream use)"))
  message(paste0("  5. ", params_rds, " - Design parameters (for downstream use)"))
  message(paste0("  6. ", params_json, " - Design parameters (human-readable)"))
  if (!is.null(power_csv)) {
    message(paste0("  7. ", power_csv, " - Power analysis results"))
  }
  if (!is.null(rec_file)) {
    message(paste0("  8. ", rec_file, " - Sample size recommendation"))
  }
  if (!is.null(cv_sens_file)) {
    message(paste0("  9. ", cv_sens_file, " - CV sensitivity table"))
  }
  if (!is.null(de_sens_file)) {
    message(paste0("  10. ", de_sens_file, " - DE proportion sensitivity table"))
  }
  if (!is.null(mc_sens_file)) {
    message(paste0("  11. ", mc_sens_file, " - Mean count sensitivity table"))
  }
  message(paste0("  12. ", val_file, " - Batch design validation report"))
  if (is_demo) {
    message("\n\u26a0\ufe0f  NOTE: Batch layout uses example/template sample IDs.")
    message("  Regenerate with your actual sample metadata before handing to your lab team.")
  } else {
    message("\nShare these files with your lab team and analysis collaborators.")
  }

  # Return file paths
  invisible(list(
    batch_layout = batch_file,
    statistical_plan = plan_file,
    lab_protocol = protocol_file,
    batch_design_rds = batch_rds,
    design_params_rds = params_rds,
    design_params_json = params_json,
    power_analysis = power_csv,
    sample_size_recommendation = rec_file,
    cv_sensitivity = cv_sens_file,
    de_proportion_sensitivity = de_sens_file,
    mean_count_sensitivity = mc_sens_file,
    batch_validation = val_file
  ))
}
