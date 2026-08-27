# =============================================================================
# concordance.R  —  on-demand cross-method DEG concordance (TABLE ONLY)
#
# Compares significant-gene calls across 2+ engines that were run on the SAME
# shared-filtered gene universe (see filter_counts.R). This is a ROBUSTNESS check,
# NOT a way to gain significance.
#
# Comparability rules (enforced here):
#   - Significance is decided ONLY by `padj` (BH-FDR) at `padj_cutoff`
#     (optionally combined with |log2FoldChange| >= lfc_cutoff if lfc_cutoff > 0).
#   - The consensus list reports the UNSHRUNK `log2FoldChange` carried in each
#     engine's standardized table (the engines already report unshrunk LFC).
#   - `baseMean_equiv` is NEVER used here (its scale differs by engine).
#
# Inputs:
#   results_list : named list of standardized data.frames from run_*() ($results),
#                  e.g. list(DESeq2 = res1, edgeR = res2, `limma-voom` = res3)
#   padj_cutoff  : significance threshold (default 0.05)
#   lfc_cutoff   : optional |log2FC| filter applied to significance (default 0 = none)
#   min_methods  : a gene is "consensus" if significant in >= this many methods (default 2)
#
# Writes (to output_dir):
#   concordance_table.csv : per-method DEG counts + pairwise overlaps + Jaccard
#   consensus_degs.csv    : genes significant in >= min_methods, with each method's
#                           unshrunk log2FC and padj, and n_methods_significant
# Returns these as a list (invisibly).
# =============================================================================

concordance <- function(results_list, padj_cutoff = 0.05, lfc_cutoff = 0,
                        min_methods = 2L, output_dir = "results") {
  stopifnot(length(results_list) >= 2L)
  dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
  if (is.null(names(results_list)) || any(names(results_list) == "")) {
    names(results_list) <- vapply(results_list, function(r) unique(r$method)[1], character(1))
  }
  methods <- names(results_list)

  # --- guardrail: refuse to touch baseMean_equiv in any cross-method computation ---
  # (defensive: ensure callers didn't pass a column we must not compare)
  sig_set <- function(df) {
    keep <- !is.na(df$padj) & df$padj < padj_cutoff
    if (lfc_cutoff > 0) keep <- keep & is.finite(df$log2FoldChange) &
      abs(df$log2FoldChange) >= lfc_cutoff
    df$gene_id[keep]
  }
  sig_lists <- lapply(results_list, sig_set)
  names(sig_lists) <- methods

  # universe = union of all gene_ids tested (should be identical if shared-filtered)
  universes <- lapply(results_list, function(d) d$gene_id)
  shared_ok <- length(unique(lapply(universes, function(u) sort(unique(u))))) == 1L

  # ---------- summary / overlap table ----------
  rows <- list()
  for (mi in methods) {
    rows[[length(rows) + 1]] <- data.frame(
      comparison = mi, type = "n_significant",
      value = length(sig_lists[[mi]]), stringsAsFactors = FALSE)
  }
  # all pairwise overlaps + Jaccard
  if (length(methods) >= 2L) {
    cb <- utils::combn(methods, 2L)
    for (k in seq_len(ncol(cb))) {
      a <- cb[1, k]; b <- cb[2, k]
      inter <- length(intersect(sig_lists[[a]], sig_lists[[b]]))
      uni <- length(union(sig_lists[[a]], sig_lists[[b]]))
      rows[[length(rows) + 1]] <- data.frame(
        comparison = paste(a, b, sep = " & "), type = "overlap",
        value = inter, stringsAsFactors = FALSE)
      rows[[length(rows) + 1]] <- data.frame(
        comparison = paste(a, b, sep = " & "), type = "jaccard",
        value = if (uni > 0) round(inter / uni, 4) else NA_real_,
        stringsAsFactors = FALSE)
    }
  }
  # genes significant in ALL methods
  all_inter <- Reduce(intersect, sig_lists)
  rows[[length(rows) + 1]] <- data.frame(
    comparison = paste(methods, collapse = " & "), type = "overlap_all",
    value = length(all_inter), stringsAsFactors = FALSE)
  concordance_table <- do.call(rbind, rows)
  attr(concordance_table, "shared_universe") <- shared_ok

  # ---------- consensus list (sig in >= min_methods) ----------
  all_sig_genes <- unique(unlist(sig_lists))
  n_sig_by_gene <- vapply(all_sig_genes, function(g)
    sum(vapply(sig_lists, function(s) g %in% s, logical(1))), integer(1))
  consensus_genes <- all_sig_genes[n_sig_by_gene >= min_methods]

  # assemble per-method UNSHRUNK log2FC + padj for consensus genes
  consensus <- data.frame(gene_id = consensus_genes,
                          n_methods_significant = n_sig_by_gene[consensus_genes],
                          stringsAsFactors = FALSE)
  for (mi in methods) {
    d <- results_list[[mi]]
    idx <- match(consensus_genes, d$gene_id)
    consensus[[paste0("log2FC_", mi)]] <- d$log2FoldChange[idx]   # UNSHRUNK
    consensus[[paste0("padj_", mi)]]   <- d$padj[idx]
  }
  # order by how many methods agree, then by best (smallest) padj available
  if (nrow(consensus) > 0L) {
    padj_cols <- consensus[, grep("^padj_", names(consensus)), drop = FALSE]
    best_padj <- do.call(pmin, c(padj_cols, na.rm = TRUE))
    consensus <- consensus[order(-consensus$n_methods_significant, best_padj), ,
                           drop = FALSE]
  }

  utils::write.csv(concordance_table, file.path(output_dir, "concordance_table.csv"),
                   row.names = FALSE)
  utils::write.csv(consensus, file.path(output_dir, "consensus_degs.csv"),
                   row.names = FALSE)

  if (!shared_ok) {
    warning("Gene universes differ across methods \u2014 run all engines on the SAME ",
            "shared-filtered counts (filter_counts.R) so concordance reflects method, ",
            "not filtering, differences.")
  }
  message(sprintf(paste0("Concordance: %s significant genes overlap across all %d methods; ",
                         "%d consensus genes (sig in >=%d). Tables in '%s'."),
                  length(all_inter), length(methods), nrow(consensus), min_methods, output_dir))

  invisible(list(concordance_table = concordance_table,
                 consensus = consensus,
                 sig_lists = sig_lists,
                 shared_universe = shared_ok))
}
