# =============================================================================
# export_results.R  —  standardized exports + run log
#
# Works on the `fit` object returned by any run_*() engine. All engines return
# the same structure:
#   fit$results        : standardized data.frame
#                        (gene_id, baseMean_equiv, log2FoldChange, pvalue, padj, method)
#   fit$method         : "DESeq2" | "edgeR" | "limma-voom" | "limma-trend"
#   fit$baseMean_scale : "linear" | "log2"   (scale of baseMean_equiv for THIS engine)
#   fit$stat_type      : native statistic label (Wald z / QL F / moderated t)
#   fit$normalized     : normalized expression matrix (for export)
#   fit$object         : the underlying fitted object (for saveRDS)
#   fit$meta           : list with design, contrast, ref_level, filter_summary,
#                        full_rank, package_version, thresholds, ...
#
# Writes (to output_dir):
#   de_results_<method>.csv
#   de_significant_<method>.csv      (padj < cutoff)
#   de_significant_fc_<method>.csv   (padj < cutoff & |log2FC| >= lfc_cutoff)
#   normalized_counts_<method>.csv
#   de_fit_<method>.rds
#   run_log.txt                      (appended; one block per engine run)
# =============================================================================

# Null/NA-coalescing helper (defined first so it is available throughout).
`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L ||
                             (length(a) == 1L && is.na(a))) b else a

# saveRDS to S3-backed mounts (/mnt/results, /mnt/shared-workspace) fails or
# yields 0-byte files because .rds uses random-access writes. Write to local
# scratch first, then shell-copy into place.
.safe_saveRDS <- function(object, path) {
  on_s3 <- grepl("^/mnt/(results|shared-workspace)", normalizePath(dirname(path),
                                                                   mustWork = FALSE))
  if (on_s3) {
    tmp <- file.path(tempdir(), basename(path))
    saveRDS(object, tmp)
    ok <- file.copy(tmp, path, overwrite = TRUE)
    # R's file.copy can also 0-byte on FUSE; fall back to shell cp if so.
    if (!isTRUE(ok) || file.info(path)$size %in% c(0, NA)) {
      system2("cp", c(shQuote(tmp), shQuote(path)))
    }
    unlink(tmp)
  } else {
    saveRDS(object, path)
  }
  invisible(path)
}

export_de <- function(fit, output_dir = "results",
                      padj_cutoff = 0.05, lfc_cutoff = 1) {
  dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
  m <- fit$method
  res <- fit$results

  # ---- standardized full table ----
  f_all <- file.path(output_dir, sprintf("de_results_%s.csv", m))
  utils::write.csv(res, f_all, row.names = FALSE)

  # ---- significant (padj only; the headline definition) ----
  sig <- res[!is.na(res$padj) & res$padj < padj_cutoff, , drop = FALSE]
  sig <- sig[order(sig$padj), , drop = FALSE]
  f_sig <- file.path(output_dir, sprintf("de_significant_%s.csv", m))
  utils::write.csv(sig, f_sig, row.names = FALSE)

  # ---- significant with fold-change subset ----
  sig_fc <- sig[abs(sig$log2FoldChange) >= lfc_cutoff, , drop = FALSE]
  f_sigfc <- file.path(output_dir, sprintf("de_significant_fc_%s.csv", m))
  utils::write.csv(sig_fc, f_sigfc, row.names = FALSE)

  # ---- normalized counts ----
  f_norm <- NA_character_
  if (!is.null(fit$normalized)) {
    f_norm <- file.path(output_dir, sprintf("normalized_counts_%s.csv", m))
    utils::write.csv(as.data.frame(fit$normalized), f_norm)
  }

  # ---- fitted object ----
  # saveRDS uses random-access writes, which break on S3-backed mounts; the
  # helper writes to local scratch then copies into place.
  f_rds <- file.path(output_dir, sprintf("de_fit_%s.rds", m))
  .safe_saveRDS(fit$object, f_rds)

  # ---- run log ----
  n_up <- sum(sig$log2FoldChange > 0, na.rm = TRUE)
  n_dn <- sum(sig$log2FoldChange < 0, na.rm = TRUE)
  meta <- fit$meta
  log_path <- file.path(output_dir, "run_log.txt")
  lines <- c(
    "======================================================================",
    sprintf("Engine:                 %s", m),
    sprintf("Timestamp:              %s", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z")),
    sprintf("Design:                 %s",
            if (!is.null(meta$design)) paste(deparse(meta$design), collapse = " ") else "NA"),
    sprintf("Contrast/coefficient:   %s",
            if (!is.null(meta$contrast)) paste(meta$contrast, collapse = " | ") else "NA"),
    sprintf("Reference level:        %s", meta$ref_level %||% "NA"),
    sprintf("Filter summary:         %s", meta$filter_summary %||% "NA"),
    sprintf("Design full rank:       %s", meta$full_rank %||% "NA"),
    sprintf("Significance:           padj < %s (BH-FDR); FC subset |log2FC| >= %s",
            padj_cutoff, lfc_cutoff),
    sprintf("Significant genes:      %d (up: %d, down: %d)", nrow(sig), n_up, n_dn),
    sprintf("Native statistic:       %s", fit$stat_type %||% "NA"),
    sprintf("baseMean_equiv_scale:   %s   (NOT comparable across engines)", fit$baseMean_scale),
    sprintf("Package version:        %s", meta$package_version %||% "NA"),
    ""
  )
  cat(paste(lines, collapse = "\n"), file = log_path, append = file.exists(log_path))

  message(sprintf("Exported %s results: %d sig genes (padj<%s). Files in '%s'.",
                  m, nrow(sig), padj_cutoff, output_dir))
  invisible(list(all = f_all, significant = f_sig, significant_fc = f_sigfc,
                 normalized = f_norm, rds = f_rds, run_log = log_path,
                 n_sig = nrow(sig), n_up = n_up, n_down = n_dn))
}
