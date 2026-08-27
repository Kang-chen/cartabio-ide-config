# =============================================================================
# run_limma_voom.R  —  limma-voom engine (standardized output)
#                      + limma-trend FALLBACK for non-count (e.g. log-TPM) input
#
# voom path (default, REQUIRES raw counts):
#   DGEList -> calcNormFactors (TMM) -> voom -> lmFit -> (contrasts) -> eBayes -> topTable
#
# limma-trend fallback (fallback = TRUE, for already-normalized log2 values ONLY):
#   lmFit on the supplied log2 matrix -> eBayes(trend = TRUE) -> topTable.
#   This is a LAST RESORT with strong caveats: log2(TPM) carries transcript-length
#   normalization that violates the logCPM mean-variance assumption, so results are
#   approximate and must be flagged. See comparison-and-caveats.md.
#
# Contrast / coefficient handling: same options as run_edger (contrast vector,
#   c(factor,num,den), or coef index/name). Defaults to last design coefficient.
#
# baseMean_equiv: limma AveExpr -> LOG2 scale.   stat_type: moderated t.
# =============================================================================

run_limma_voom <- function(counts, coldata, design = ~ condition,
                           contrast = NULL, coef = NULL, ref_level = NULL,
                           condition_col = "condition", fallback = FALSE,
                           filter_summary = NA_character_, full_rank = NA) {
  options(repos = c(CRAN = "https://cloud.r-project.org"))
  for (p in c("edgeR", "limma")) {
    if (!requireNamespace(p, quietly = TRUE)) {
      if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
      BiocManager::install(p, update = FALSE, ask = FALSE)
    }
  }

  mat <- as.matrix(counts); storage.mode(mat) <- "double"
  coldata <- as.data.frame(coldata)
  if (condition_col %in% colnames(coldata)) {
    coldata[[condition_col]] <- factor(coldata[[condition_col]])
    if (!is.null(ref_level)) {
      coldata[[condition_col]] <- stats::relevel(coldata[[condition_col]], ref = ref_level)
    }
  }
  mm <- stats::model.matrix(design, data = coldata)

  method_name <- if (fallback) "limma-trend" else "limma-voom"
  norm_mat <- NULL

  if (!fallback) {
    # ---- voom path (raw counts) ----
    y <- edgeR::DGEList(counts = mat)
    y <- edgeR::calcNormFactors(y)
    v <- limma::voom(y, mm, plot = FALSE)
    fit <- limma::lmFit(v, mm)
    norm_mat <- v$E                                # log2-CPM (voom-normalized)
    expr_for_object <- v
  } else {
    # ---- limma-trend fallback (already-normalized log2 values) ----
    warning("limma-trend FALLBACK in use: input treated as already-normalized log2 ",
            "values. If these are log2(TPM), length normalization violates the logCPM ",
            "mean-variance assumption \u2014 results are APPROXIMATE and must be flagged.")
    fit <- limma::lmFit(mat, mm)
    norm_mat <- mat
    expr_for_object <- mat
  }

  # --- resolve contrast/coefficient ---
  used <- NULL
  if (!is.null(contrast)) {
    cvec <- .limma_contrast_vector(contrast, colnames(mm))
    fit2 <- limma::contrasts.fit(fit, contrasts = cvec)
    fit2 <- if (fallback) limma::eBayes(fit2, trend = TRUE) else limma::eBayes(fit2)
    coef_use <- 1L
    used <- if (length(contrast) == 3L) paste(contrast, collapse = " | ") else
      paste("contrast vector over", paste(colnames(mm), collapse = "/"))
  } else {
    fit2 <- if (fallback) limma::eBayes(fit, trend = TRUE) else limma::eBayes(fit)
    if (is.null(coef)) {
      coef_use <- ncol(mm)
      message(method_name, ": no contrast/coef given; testing the LAST design ",
              "coefficient '", colnames(mm)[coef_use], "'. For multi-factor designs ",
              "put the condition last or pass an explicit contrast/coef.")
    } else coef_use <- coef
    used <- if (is.numeric(coef_use)) colnames(mm)[coef_use] else coef_use
  }

  tt <- limma::topTable(fit2, coef = coef_use, number = Inf, sort.by = "none")

  standardized <- data.frame(
    gene_id        = rownames(tt),
    baseMean_equiv = tt$AveExpr,                  # LOG2 scale
    log2FoldChange = tt$logFC,                     # log2
    pvalue         = tt$P.Value,
    padj           = tt$adj.P.Val,                 # BH-adjusted
    method         = method_name,
    stringsAsFactors = FALSE
  )
  rownames(standardized) <- NULL

  list(
    results        = standardized,
    method         = method_name,
    baseMean_scale = "log2",
    stat_type      = "moderated t",
    normalized     = norm_mat,
    object         = fit2,
    coef_names     = colnames(mm),
    meta = list(
      design = design, contrast = used, ref_level = ref_level,
      filter_summary = filter_summary, full_rank = full_rank,
      package_version = as.character(utils::packageVersion("limma")),
      fallback = fallback
    )
  )
}

# Same treatment-coding logic as .edger_contrast_vector (see notes there), returning
# a one-column contrast matrix as limma::contrasts.fit expects.
.limma_contrast_vector <- function(contrast, design_cols) {
  if (is.numeric(contrast)) {
    return(matrix(contrast, ncol = 1, dimnames = list(design_cols, "contrast")))
  }
  if (length(contrast) == 3L) {
    fac <- contrast[1]; num <- contrast[2]; den <- contrast[3]
    col_num <- paste0(fac, num); col_den <- paste0(fac, den)
    if (!(col_num %in% design_cols) && !(col_den %in% design_cols)) {
      stop("Could not build limma contrast for ", paste(contrast, collapse = "/"),
           ": neither level maps to a design column. Design columns: ",
           paste(design_cols, collapse = ", "),
           ". (At most one level may be the reference; check spelling and the factor name.)")
    }
    v <- stats::setNames(rep(0, length(design_cols)), design_cols)
    if (col_num %in% design_cols) v[col_num] <- 1
    if (col_den %in% design_cols) v[col_den] <- -1
    return(matrix(v, ncol = 1, dimnames = list(design_cols, "contrast")))
  }
  stop("Unsupported limma contrast specification (use a numeric vector or c(factor,num,den)).")
}
