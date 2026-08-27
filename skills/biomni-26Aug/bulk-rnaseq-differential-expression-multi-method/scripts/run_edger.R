# =============================================================================
# run_edger.R  —  edgeR quasi-likelihood F-test engine (standardized output)
#
# Uses the recommended edgeR QL pipeline:
#   DGEList -> calcNormFactors (TMM) -> estimateDisp -> glmQLFit -> glmQLFTest
#
# Contrast / coefficient handling (pick ONE):
#   - contrast : a named contrast over the design columns, e.g.
#                makeContrasts-style string OR a numeric contrast vector OR
#                c(factor, numerator, denominator) which is converted internally.
#   - coef     : an integer index or column name of the design matrix to test.
#   Defaults to the last design coefficient if neither given; reports colnames.
#
# baseMean_equiv: edgeR logCPM (the `logCPM` column from topTags) -> LOG2 scale.
# stat_type: QL F.
# =============================================================================

run_edger <- function(counts, coldata, design = ~ condition,
                      contrast = NULL, coef = NULL, ref_level = NULL,
                      condition_col = "condition",
                      filter_summary = NA_character_, full_rank = NA) {
  options(repos = c(CRAN = "https://cloud.r-project.org"))
  for (p in c("edgeR", "limma")) {
    if (!requireNamespace(p, quietly = TRUE)) {
      if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
      BiocManager::install(p, update = FALSE, ask = FALSE)
    }
  }

  counts <- as.matrix(counts); storage.mode(counts) <- "double"
  coldata <- as.data.frame(coldata)
  if (condition_col %in% colnames(coldata)) {
    coldata[[condition_col]] <- factor(coldata[[condition_col]])
    if (!is.null(ref_level)) {
      coldata[[condition_col]] <- stats::relevel(coldata[[condition_col]], ref = ref_level)
    }
  }

  mm <- stats::model.matrix(design, data = coldata)

  y <- edgeR::DGEList(counts = counts)
  y <- edgeR::calcNormFactors(y)                 # TMM normalization
  y <- edgeR::estimateDisp(y, mm)
  fit <- edgeR::glmQLFit(y, mm)

  # --- resolve what to test ---
  used <- NULL
  if (!is.null(contrast)) {
    cvec <- .edger_contrast_vector(contrast, colnames(mm))
    qlf <- edgeR::glmQLFTest(fit, contrast = cvec)
    used <- if (length(contrast) == 3L) paste(contrast, collapse = " | ") else
      paste("contrast vector over", paste(colnames(mm), collapse = "/"))
  } else {
    if (is.null(coef)) {
      coef <- ncol(mm)                              # default: last coefficient
      message("edgeR: no contrast/coef given; testing the LAST design coefficient '",
              colnames(mm)[coef], "'. For multi-factor designs put the condition last ",
              "or pass an explicit contrast/coef.")
    }
    qlf <- edgeR::glmQLFTest(fit, coef = coef)
    used <- if (is.numeric(coef)) colnames(mm)[coef] else coef
  }

  tt <- edgeR::topTags(qlf, n = Inf, sort.by = "none")$table

  standardized <- data.frame(
    gene_id        = rownames(tt),
    baseMean_equiv = tt$logCPM,                   # LOG2 scale
    log2FoldChange = tt$logFC,                     # edgeR logFC is log2 by default
    pvalue         = tt$PValue,
    padj           = tt$FDR,                       # BH-adjusted
    method         = "edgeR",
    stringsAsFactors = FALSE
  )
  rownames(standardized) <- NULL

  list(
    results        = standardized,
    method         = "edgeR",
    baseMean_scale = "log2",
    stat_type      = "QL F",
    normalized     = edgeR::cpm(y, log = TRUE, prior.count = 2),
    object         = fit,          # glmQLFit (carries QL dispersions for plotQLDisp)
    dge            = y,            # DGEList (for plotBCV / further use)
    coef_names     = colnames(mm),
    meta = list(
      design = design, contrast = used, ref_level = ref_level,
      filter_summary = filter_summary, full_rank = full_rank,
      package_version = as.character(utils::packageVersion("edgeR"))
    )
  )
}

# Convert a contrast into a numeric vector over the treatment-coded design columns.
# Accepts: a numeric vector (passed through) or c(factor, num, den).
# With treatment coding the reference level IS the intercept (coefficient 0), so:
#   - both levels non-reference -> num_col(+1), den_col(-1)
#   - numerator is the reference -> only den_col present -> (-1) == -(den vs ref), correct
#   - denominator is the reference -> only num_col present -> (+1) == (num vs ref), correct
# An error is raised only when NEITHER level matches a design column (typo / wrong factor).
.edger_contrast_vector <- function(contrast, design_cols) {
  if (is.numeric(contrast)) return(contrast)
  if (length(contrast) == 3L) {
    fac <- contrast[1]; num <- contrast[2]; den <- contrast[3]
    col_num <- paste0(fac, num); col_den <- paste0(fac, den)
    if (!(col_num %in% design_cols) && !(col_den %in% design_cols)) {
      stop("Could not build edgeR contrast for ", paste(contrast, collapse = "/"),
           ": neither level maps to a design column. Design columns: ",
           paste(design_cols, collapse = ", "),
           ". (At most one level may be the reference; check spelling and the factor name.)")
    }
    v <- stats::setNames(rep(0, length(design_cols)), design_cols)
    if (col_num %in% design_cols) v[col_num] <- 1
    if (col_den %in% design_cols) v[col_den] <- -1
    return(v)
  }
  stop("Unsupported edgeR contrast specification (use a numeric vector or c(factor,num,den)).")
}
