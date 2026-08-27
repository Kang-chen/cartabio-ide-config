# =============================================================================
# qc_plots.R  —  QC visualizations for a single engine's `fit` object
#
# Produces (PNG always; SVG too if svglite is installed), 300 DPI:
#   pca_<method>.{png,svg}            sample clustering on normalized expression
#   ma_<method>.{png,svg}             log2FC vs expression-strength (engine-native axis)
#   volcano_<method>.{png,svg}        log2FC vs -log10(padj)
#   meanvar_<method>.{png,svg}        mean-variance / dispersion (engine-appropriate)
#
# IMPORTANT axis note: the MA plot x-axis uses each engine's OWN baseMean_equiv
# scale (DESeq2: linear count -> log10 x; edgeR/limma: log2 expression -> linear x).
# This is a within-engine diagnostic and is never compared across engines.
# =============================================================================

run_all_qc <- function(fit, output_dir = "results", coldata = NULL,
                       condition_col = "condition", padj_cutoff = 0.05,
                       top_label = 10) {
  options(repos = c(CRAN = "https://cloud.r-project.org"))
  for (p in c("ggplot2", "ggrepel")) {
    if (!requireNamespace(p, quietly = TRUE)) install.packages(p)
  }
  has_svg <- requireNamespace("svglite", quietly = TRUE)
  dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
  library(ggplot2)

  m <- fit$method
  res <- fit$results
  save_plot <- function(p, stem, w = 7, h = 6) {
    ggsave(file.path(output_dir, paste0(stem, "_", m, ".png")), p,
           width = w, height = h, dpi = 300)
    if (has_svg) ggsave(file.path(output_dir, paste0(stem, "_", m, ".svg")), p,
                        width = w, height = h)
  }
  okpt <- c("#0279EE", "#FF9400", "#75A025", "#FD9BED", "#000000")

  # ---------- PCA ----------
  norm <- fit$normalized
  if (!is.null(norm) && ncol(norm) >= 2) {
    # DESeq2 normalized counts are linear -> log them for PCA; edgeR/limma already log2.
    mat <- if (identical(fit$baseMean_scale, "linear")) log2(norm + 1) else norm
    vars <- apply(mat, 1, stats::var)
    top <- order(vars, decreasing = TRUE)[seq_len(min(500L, nrow(mat)))]
    pc <- stats::prcomp(t(mat[top, , drop = FALSE]))
    ve <- (pc$sdev^2) / sum(pc$sdev^2)
    df <- data.frame(sample = colnames(mat), PC1 = pc$x[, 1], PC2 = pc$x[, 2])
    if (!is.null(coldata) && condition_col %in% colnames(coldata)) {
      df$group <- factor(coldata[colnames(mat), condition_col])
    } else df$group <- "sample"
    p <- ggplot(df, aes(PC1, PC2, color = group, label = sample)) +
      geom_point(size = 3) + ggrepel::geom_text_repel(size = 3, show.legend = FALSE) +
      scale_color_manual(values = rep(okpt, length.out = length(unique(df$group)))) +
      labs(title = paste0("PCA (", m, ")"),
           x = sprintf("PC1 (%.1f%%)", 100 * ve[1]),
           y = sprintf("PC2 (%.1f%%)", 100 * ve[2])) +
      theme_bw()
    save_plot(p, "pca")
  }

  # ---------- MA ----------
  ma <- res[is.finite(res$baseMean_equiv) & is.finite(res$log2FoldChange), ]
  ma$sig <- !is.na(ma$padj) & ma$padj < padj_cutoff
  x_lab <- if (identical(fit$baseMean_scale, "linear"))
    "mean normalized count (log10)" else "average log2 expression"
  p <- ggplot(ma, aes(baseMean_equiv, log2FoldChange, color = sig)) +
    geom_point(size = 0.7, alpha = 0.5) +
    geom_hline(yintercept = 0, linetype = 2) +
    scale_color_manual(values = c(`FALSE` = "grey70", `TRUE` = "#FF9400"),
                       name = paste0("padj<", padj_cutoff)) +
    labs(title = paste0("MA plot (", m, ")"), x = x_lab, y = "log2 fold change") +
    theme_bw()
  if (identical(fit$baseMean_scale, "linear")) p <- p + scale_x_log10()
  save_plot(p, "ma")

  # ---------- Volcano ----------
  vol <- res[is.finite(res$log2FoldChange) & !is.na(res$padj), ]
  vol$neglog10padj <- -log10(pmax(vol$padj, .Machine$double.xmin))
  vol$sig <- vol$padj < padj_cutoff & abs(vol$log2FoldChange) >= 1
  lab <- vol[order(vol$padj), ][seq_len(min(top_label, nrow(vol))), ]
  p <- ggplot(vol, aes(log2FoldChange, neglog10padj, color = sig)) +
    geom_point(size = 0.7, alpha = 0.5) +
    geom_vline(xintercept = c(-1, 1), linetype = 2, color = "grey50") +
    geom_hline(yintercept = -log10(padj_cutoff), linetype = 2, color = "grey50") +
    ggrepel::geom_text_repel(data = lab, aes(label = gene_id), size = 2.8,
                             show.legend = FALSE, max.overlaps = 20) +
    scale_color_manual(values = c(`FALSE` = "grey70", `TRUE` = "#0279EE"),
                       name = paste0("padj<", padj_cutoff, " & |log2FC|>=1")) +
    labs(title = paste0("Volcano (", m, ")"),
         x = "log2 fold change", y = "-log10 adjusted p-value") +
    theme_bw()
  save_plot(p, "volcano")

  # ---------- mean-variance / dispersion (engine-appropriate) ----------
  mv_done <- tryCatch({
    grDevices::png(file.path(output_dir, paste0("meanvar_", m, ".png")),
                   width = 7, height = 6, units = "in", res = 300)
    on.exit(grDevices::dev.off(), add = TRUE)
    if (m == "DESeq2") {
      DESeq2::plotDispEsts(fit$object, main = "DESeq2 dispersion estimates")
    } else if (m == "edgeR") {
      # glmQLFit carries the quasi-likelihood dispersions
      edgeR::plotQLDisp(fit$object, main = "edgeR QL dispersion")
    } else {
      # limma: plot residual SD vs average expression (the trend voom/eBayes models)
      limma::plotSA(fit$object, main = paste0(m, " mean-variance trend"))
    }
    TRUE
  }, error = function(e) { message("mean-variance plot skipped: ", conditionMessage(e)); FALSE })

  message(sprintf("QC plots for %s written to '%s'%s.", m, output_dir,
                  if (has_svg) " (PNG+SVG)" else " (PNG; install svglite for SVG)"))
  invisible(TRUE)
}

`%||%` <- function(a, b) if (is.null(a)) b else a
