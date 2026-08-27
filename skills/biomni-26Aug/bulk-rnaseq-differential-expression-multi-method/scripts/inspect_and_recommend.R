# =============================================================================
# inspect_and_recommend.R  —  data diagnostics + ADVISORY method recommendation
#
# Computes the facts the agent needs to recommend an engine, and a programmatic
# full-rank / confounding check on the proposed design. The numeric thresholds
# here are SOFT defaults that produce an advisory recommendation: the agent is
# expected to explain the recommendation in prose and let the user confirm or
# override. They are not hard gates on the analysis.
#
# Returns an object with:
#   $diagnostics      : named list of measured data characteristics
#   $full_rank        : logical (design is full rank?)
#   $confounding_msg  : character (warning text if not full rank, else "")
#   $recommended      : "DESeq2" | "edgeR" | "limma-voom" | "limma-trend (fallback)"
#   $rationale        : human-readable reason (one or two sentences)
#   $alternatives     : character vector of other reasonable engines
#   $proposed_design  : a formula
#   $pca              : data.frame of PC1/PC2 + grouping cols (for optional plotting)
# A print method renders a compact, agent-friendly summary.
# =============================================================================

inspect_and_recommend <- function(counts, coldata, condition_col = "condition",
                                  design = NULL,
                                  very_small_n_thresh = 2L, # <= this per group => lean edgeR (QL advantage real)
                                  borderline_n = 3L,        # == this => DESeq2 default, edgeR noted as strong alt
                                  large_n_thresh = 20L) {   # >= this per group => lean limma-voom
  options(repos = c(CRAN = "https://cloud.r-project.org"))
  counts <- as.matrix(counts)
  storage.mode(counts) <- "double"
  stopifnot(condition_col %in% colnames(coldata))
  coldata[[condition_col]] <- factor(coldata[[condition_col]])
  if (is.null(design)) design <- stats::as.formula(paste("~", condition_col))

  finite_vals <- counts[is.finite(counts)]
  is_integerish <- length(finite_vals) > 0 &&
    all(abs(finite_vals - round(finite_vals)) < 1e-8)
  has_negative <- any(finite_vals < 0)
  looks_log <- length(finite_vals) > 0 &&
    max(finite_vals, na.rm = TRUE) < 100 && !is_integerish

  group_tab <- table(coldata[[condition_col]])
  min_per_group <- as.integer(min(group_tab))
  n_groups <- length(group_tab)
  n_samples <- ncol(counts)

  # library size spread (raw); informative for normalization concerns
  lib <- colSums(counts)
  lib_ratio <- if (min(lib) > 0) max(lib) / min(lib) else Inf

  # number of model coefficients (design complexity)
  mm <- tryCatch(stats::model.matrix(design, data = coldata),
                 error = function(e) NULL)
  n_coef <- if (!is.null(mm)) ncol(mm) else NA_integer_
  # full-rank / confounding check
  full_rank <- NA
  confounding_msg <- ""
  if (!is.null(mm)) {
    rk <- as.integer(Matrix::rankMatrix(mm))
    full_rank <- (rk == ncol(mm))
    if (!full_rank) {
      confounding_msg <- sprintf(
        paste0("DESIGN IS NOT FULL RANK: model matrix has %d columns but rank %d. ",
               "A covariate is likely confounded/aliased with the condition ",
               "(e.g. batch perfectly nested within condition). The effect cannot be ",
               "estimated as specified \u2014 drop the aliased term or redesign before running."),
        ncol(mm), rk)
    }
  }
  is_multifactor <- length(all.vars(design)) > 1L

  # ---- advisory recommendation logic (soft) ----
  if (!is_integerish || has_negative) {
    recommended <- "limma-trend (fallback)"
    rationale <- paste0(
      "Input does not look like raw integer counts",
      if (looks_log) " (values < 100 and non-integer suggest a log scale)" else "",
      ". Count-based methods (DESeq2/edgeR) and voom require raw counts, so only the ",
      "limma-trend fallback applies \u2014 results are approximate and must be flagged.")
    alternatives <- character(0)
  } else if (min_per_group <= very_small_n_thresh) {
    recommended <- "edgeR"
    rationale <- sprintf(
      paste0("Smallest group has only %d replicate(s). edgeR's quasi-likelihood F-test ",
             "is robust for very small n, where per-gene dispersion is hard to estimate."),
      min_per_group)
    alternatives <- c("DESeq2")
  } else if (min_per_group >= large_n_thresh || (is_multifactor && n_coef >= 4L)) {
    recommended <- "limma-voom"
    rationale <- paste0(
      "Sample size is large and/or the design is complex/multi-factor. limma-voom is fast, ",
      "well-calibrated, and flexible with model formulas at this scale.")
    alternatives <- c("DESeq2", "edgeR")
  } else if (min_per_group == borderline_n) {
    # n == 3: DESeq2 default, but edgeR is a genuinely strong alternative at this n.
    recommended <- "DESeq2"
    rationale <- sprintf(
      paste0("Smallest group has %d replicates \u2014 a common, borderline-small design. ",
             "DESeq2 is the robust default here, but edgeR's quasi-likelihood F-test is a ",
             "strong alternative at this sample size (it models dispersion uncertainty well ",
             "with few replicates); consider running both and checking concordance."),
      min_per_group)
    alternatives <- c("edgeR", "limma-voom")
  } else {
    recommended <- "DESeq2"
    rationale <- paste0(
      "Typical experiment with raw counts and a simple-to-moderate design. DESeq2 is a ",
      "robust, widely validated default.")
    alternatives <- c("edgeR", "limma-voom")
  }

  # ---- PCA on log-CPM for QC/clustering (best-effort) ----
  pca_df <- NULL
  pca_msg <- ""
  pca_ok <- tryCatch({
    if (!requireNamespace("edgeR", quietly = TRUE)) {
      if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
      BiocManager::install("edgeR", update = FALSE, ask = FALSE)
    }
    keep <- edgeR::filterByExpr(counts, group = coldata[[condition_col]])
    logcpm <- edgeR::cpm(counts[keep, , drop = FALSE], log = TRUE, prior.count = 2)
    vars <- matrixStats_rowVars(logcpm)
    top <- order(vars, decreasing = TRUE)[seq_len(min(500L, nrow(logcpm)))]
    pc <- stats::prcomp(t(logcpm[top, , drop = FALSE]), scale. = FALSE)
    var_expl <- (pc$sdev^2) / sum(pc$sdev^2)
    # Avoid column-name collisions with any coldata column named sample/PC1/PC2.
    cd <- coldata[, setdiff(colnames(coldata), c("sample", "PC1", "PC2")), drop = FALSE]
    pca_df <- data.frame(sample = colnames(counts),
                         PC1 = pc$x[, 1], PC2 = pc$x[, 2],
                         cd, check.names = FALSE)
    attr(pca_df, "var_explained") <- var_expl[1:2]
    TRUE
  }, error = function(e) { pca_msg <<- paste("PCA skipped:", conditionMessage(e)); FALSE })

  diagnostics <- list(
    n_samples = n_samples,
    n_groups = n_groups,
    group_sizes = as.list(group_tab),
    min_per_group = min_per_group,
    is_raw_integer_counts = (is_integerish && !has_negative),
    looks_log_scale = looks_log,
    has_negative = has_negative,
    library_size_max_min_ratio = round(lib_ratio, 2),
    n_model_coefficients = n_coef,
    is_multifactor_design = is_multifactor,
    pca_note = if (pca_ok) "PCA computed (see $pca)" else pca_msg
  )

  out <- list(
    diagnostics = diagnostics,
    full_rank = full_rank,
    confounding_msg = confounding_msg,
    recommended = recommended,
    rationale = rationale,
    alternatives = alternatives,
    proposed_design = design,
    pca = pca_df
  )
  class(out) <- "de_recommendation"
  out
}

# Lightweight rowVars without depending on matrixStats being present.
matrixStats_rowVars <- function(x) {
  if (requireNamespace("matrixStats", quietly = TRUE)) return(matrixStats::rowVars(x))
  m <- rowMeans(x)
  rowSums((x - m)^2) / (ncol(x) - 1)
}

print.de_recommendation <- function(x, ...) {
  d <- x$diagnostics
  cat("=== Data diagnostics ===\n")
  cat(sprintf("  Samples: %d   Groups: %d   Smallest group: %d\n",
              d$n_samples, d$n_groups, d$min_per_group))
  cat(sprintf("  Group sizes: %s\n",
              paste(names(d$group_sizes), unlist(d$group_sizes), sep = "=", collapse = ", ")))
  cat(sprintf("  Raw integer counts: %s   (looks log-scale: %s, has negatives: %s)\n",
              d$is_raw_integer_counts, d$looks_log_scale, d$has_negative))
  cat(sprintf("  Library-size max/min ratio: %s\n", d$library_size_max_min_ratio))
  cat(sprintf("  Model coefficients: %s   Multi-factor design: %s\n",
              d$n_model_coefficients, d$is_multifactor_design))
  cat(sprintf("  %s\n", d$pca_note))
  cat("\n=== Full-rank / confounding check ===\n")
  if (isTRUE(x$full_rank)) {
    cat("  OK: design is full rank.\n")
  } else if (isFALSE(x$full_rank)) {
    cat("  ", x$confounding_msg, "\n", sep = "")
  } else {
    cat("  (could not build model matrix to check)\n")
  }
  cat("\n=== Advisory recommendation ===\n")
  cat(sprintf("  Recommended engine: %s\n", x$recommended))
  cat(sprintf("  Why: %s\n", x$rationale))
  if (length(x$alternatives)) {
    cat(sprintf("  Reasonable alternatives: %s\n", paste(x$alternatives, collapse = ", ")))
  }
  cat(sprintf("  Proposed design: %s\n", paste(deparse(x$proposed_design), collapse = " ")))
  cat("\n  NOTE: advisory only \u2014 confirm with the user before running.\n")
  invisible(x)
}
