# =============================================================================
# filter_counts.R  —  shared low-expression pre-filter for ALL engines
#
# Applies ONE common filter (edgeR::filterByExpr) to the raw count matrix using
# the grouping variable, so the SAME filtered gene set is handed to whichever
# engine(s) run. This is essential for fair cross-method concordance: otherwise
# the overlap would partly measure differences in each engine's own internal
# filtering rather than differences in the statistical methods.
#
# Returns: list(counts = filtered matrix, coldata = unchanged, keep = logical,
#               n_before, n_after, filter_summary = character)
# =============================================================================

source_local <- function(f) {
  # source a sibling script regardless of caller's working dir, if available
  here <- tryCatch(dirname(sys.frame(1)$ofile), error = function(e) NA)
  cand <- if (!is.na(here)) file.path(here, f) else f
  if (file.exists(cand)) source(cand)
}

filter_counts <- function(counts, coldata, condition_col = "condition",
                          group = NULL, design = NULL, ...) {
  options(repos = c(CRAN = "https://cloud.r-project.org"))
  if (!requireNamespace("edgeR", quietly = TRUE)) {
    if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
    BiocManager::install("edgeR", update = FALSE, ask = FALSE)
  }

  counts <- as.matrix(counts)
  storage.mode(counts) <- "double"

  if (is.null(group)) {
    if (!condition_col %in% colnames(coldata)) {
      stop("filter_counts: grouping column '", condition_col, "' not in coldata.")
    }
    group <- factor(coldata[[condition_col]])
  }

  n_before <- nrow(counts)

  # filterByExpr keeps genes with a worthwhile count in a minimum number of
  # samples, sized to the smallest group. Use the design if supplied (better for
  # multi-factor models); otherwise use the group factor.
  keep <- if (!is.null(design)) {
    mm <- stats::model.matrix(design, data = coldata)
    edgeR::filterByExpr(counts, design = mm)
  } else {
    edgeR::filterByExpr(counts, group = group)
  }

  counts_f <- counts[keep, , drop = FALSE]
  n_after <- nrow(counts_f)

  summary_txt <- sprintf(
    "Shared filterByExpr pre-filter: %d -> %d genes kept (%d removed); applied identically before all engines.",
    n_before, n_after, n_before - n_after)
  message(summary_txt)

  list(
    counts = counts_f,
    coldata = coldata,
    keep = keep,
    n_before = n_before,
    n_after = n_after,
    filter_summary = summary_txt
  )
}
