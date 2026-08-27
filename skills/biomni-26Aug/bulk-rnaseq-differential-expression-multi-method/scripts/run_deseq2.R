# =============================================================================
# run_deseq2.R  —  DESeq2 engine (standardized output)
#
# Signature shared by all engines:
#   run_deseq2(counts, coldata, design = ~ condition,
#              contrast = NULL, coef = NULL, ref_level = NULL,
#              condition_col = "condition", alpha = 0.05, shrink = TRUE)
#
# Contrast / coefficient handling (pick ONE):
#   - contrast = c(factor, numerator, denominator)   e.g. c("condition","treated","control")
#   - coef     = a coefficient name from resultsNames(dds)
#   If neither is given, defaults to the 2nd resultsNames() coefficient and reports
#   the available coefficients so the user can choose for multi-group designs.
#
# IMPORTANT (cross-method comparability):
#   The standardized `log2FoldChange` is the UNSHRUNK value from results(), so it is
#   directly comparable to edgeR/limma. apeglm shrinkage is computed only when a `coef`
#   is available and stored separately in fit$shrunk_lfc for DESeq2's OWN MA/volcano.
#
# baseMean_equiv: DESeq2 baseMean = mean of normalized counts -> LINEAR scale.
# =============================================================================

run_deseq2 <- function(counts, coldata, design = ~ condition,
                       contrast = NULL, coef = NULL, ref_level = NULL,
                       condition_col = "condition", alpha = 0.05,
                       shrink = TRUE, filter_summary = NA_character_,
                       full_rank = NA) {
  options(repos = c(CRAN = "https://cloud.r-project.org"))
  for (p in c("DESeq2", "apeglm")) {
    if (!requireNamespace(p, quietly = TRUE)) {
      if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
      BiocManager::install(p, update = FALSE, ask = FALSE)
    }
  }

  counts <- as.matrix(counts); storage.mode(counts) <- "integer"
  coldata <- as.data.frame(coldata)
  if (condition_col %in% colnames(coldata)) {
    coldata[[condition_col]] <- factor(coldata[[condition_col]])
    if (!is.null(ref_level)) {
      coldata[[condition_col]] <- stats::relevel(coldata[[condition_col]], ref = ref_level)
    }
  }

  dds <- DESeq2::DESeqDataSetFromMatrix(countData = counts, colData = coldata,
                                        design = design)
  dds <- DESeq2::DESeq(dds)
  coef_names <- DESeq2::resultsNames(dds)

  # --- unshrunk results for the requested comparison (this is the standardized LFC) ---
  if (!is.null(contrast)) {
    res <- DESeq2::results(dds, contrast = contrast, alpha = alpha)
    used <- paste(contrast, collapse = " | ")
  } else if (!is.null(coef)) {
    res <- DESeq2::results(dds, name = coef, alpha = alpha)
    used <- coef
  } else {
    coef <- coef_names[2]
    res <- DESeq2::results(dds, name = coef, alpha = alpha)
    used <- coef
  }
  res_df <- as.data.frame(res)

  # --- apeglm shrinkage for DESeq2's OWN visualization only (needs a coef) ---
  shrunk_lfc <- NULL
  if (shrink) {
    coef_for_shrink <- if (!is.null(coef)) coef else
      tryCatch(.match_contrast_to_coef(contrast, coef_names), error = function(e) NA_character_)
    if (!is.na(coef_for_shrink) && coef_for_shrink %in% coef_names) {
      sh <- tryCatch(
        DESeq2::lfcShrink(dds, coef = coef_for_shrink, type = "apeglm", quiet = TRUE),
        error = function(e) NULL)
      if (!is.null(sh)) {
        shrunk_lfc <- data.frame(gene_id = rownames(sh),
                                 log2FoldChange_shrunk = as.data.frame(sh)$log2FoldChange,
                                 stringsAsFactors = FALSE)
      }
    }
  }

  standardized <- data.frame(
    gene_id        = rownames(res_df),
    baseMean_equiv = res_df$baseMean,          # LINEAR scale
    log2FoldChange = res_df$log2FoldChange,    # UNSHRUNK
    pvalue         = res_df$pvalue,
    padj           = res_df$padj,
    method         = "DESeq2",
    stringsAsFactors = FALSE
  )
  rownames(standardized) <- NULL

  list(
    results       = standardized,
    method        = "DESeq2",
    baseMean_scale = "linear",
    stat_type     = "Wald z (DESeq2 stat column, not exported to standardized schema)",
    normalized    = DESeq2::counts(dds, normalized = TRUE),
    shrunk_lfc    = shrunk_lfc,
    object        = dds,
    coef_names    = coef_names,
    meta = list(
      design = design, contrast = used, ref_level = ref_level,
      filter_summary = filter_summary, full_rank = full_rank,
      package_version = as.character(utils::packageVersion("DESeq2")),
      alpha = alpha
    )
  )
}

# Best-effort map of a contrast c(factor, num, den) to the DESeq2 coef name
# 'factor_num_vs_den' so apeglm shrinkage can run. Returns NA if not resolvable.
.match_contrast_to_coef <- function(contrast, coef_names) {
  if (is.null(contrast) || length(contrast) != 3L) return(NA_character_)
  cand <- paste0(contrast[1], "_", contrast[2], "_vs_", contrast[3])
  if (cand %in% coef_names) cand else NA_character_
}
