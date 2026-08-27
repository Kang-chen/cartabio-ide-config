# ============================================================================
# make_figures.R  --  figure factory for consensus-disease-signature
# Sourced by run_meta_signature.R. Defines make_all_figures().
#
# Every figure is saved as PNG (dpi=150) + SVG. Each block is wrapped so a
# sparse result (e.g. few core genes, no enrichment hits) skips that panel
# instead of aborting the whole run. The AGENT verifies each PNG with a media
# output check after the run.
#
# Palette: Control #0279EE (blue), Case #FF9400 (orange); diverging heatmap
# blue-white-orange. Fonts: Liberation Sans (SVG keeps text editable).
# ============================================================================

suppressMessages({
  library(ggplot2); library(reshape2)
})

# Null-coalesce (make_figures may be sourced standalone; engine also defines this).
if (!exists("%||%")) `%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

.save_fig <- function(p, outdir, name, w, h, is_ggplot = TRUE) {
  png_path <- file.path(outdir, "figures", paste0(name, ".png"))
  svg_path <- file.path(outdir, "figures", paste0(name, ".svg"))
  if (is_ggplot) {
    ggsave(png_path, p, width = w, height = h, dpi = 150)
    ggsave(svg_path, p, width = w, height = h)
  } else {
    # p is a function that draws to the current device (base / ComplexHeatmap)
    png(png_path, width = w, height = h, units = "in", res = 150); p(); dev.off()
    svglite_ok <- requireNamespace("svglite", quietly = TRUE)
    if (svglite_ok) { svglite::svglite(svg_path, width = w, height = h); p(); dev.off() }
    else { svg(svg_path, width = w, height = h); p(); dev.off() }
  }
  message("  saved figure: ", name)
}

# Expected analysis figures (must be produced as PNG under <outdir>/figures/).
.EXPECTED_FIGS <- c("QC_sample_distributions", "volcano_per_study", "concordance_scatter",
                    "consensus_heatmap", "forest_top_genes", "enrichment_ORA_dotplots",
                    "GSEA_hallmark_barplot")

.verify_figures <- function(outdir) {
  figdir <- file.path(outdir, "figures")
  missing <- .EXPECTED_FIGS[!file.exists(file.path(figdir, paste0(.EXPECTED_FIGS, ".png")))]
  if (length(missing) > 0) {
    warning("[make_figures] ", length(missing), " of ", length(.EXPECTED_FIGS),
            " expected figures were NOT produced: ", paste(missing, collapse = ", "),
            ". The PDF will have blank sections where these should appear.")
  } else {
    message("[make_figures] all ", length(.EXPECTED_FIGS), " expected figures produced.")
  }
}

make_all_figures <- function(studies, de, meta, enr, OUTDIR, DISEASE, COL_CTRL, COL_CASE, THEME,
                             sensitivity = NULL) {
  case_lab <- DISEASE

  # ---- (1) QC: per-sample expression distributions (boxplots) -------------
  # RNA-seq `matrix` cohorts hold raw counts; log2(x+1) is applied here so the
  # axis label matches the data and cohorts of different depth are comparable.
  # (Microarray inputs are already log2 and this transform is applied to the
  # already-ingested values consistently across cohorts.)
  tryCatch({
    qc <- do.call(rbind, lapply(names(studies), function(s) {
      ex <- studies[[s]]$exprs; grp <- studies[[s]]$group
      is_rnaseq <- (studies[[s]]$type %||% "microarray") == "rnaseq"
      if (is_rnaseq) ex <- log2(ex + 1)                 # counts -> log2 for display
      idx <- seq_len(ncol(ex))
      data.frame(study = s, sample = colnames(ex)[idx],
                 group = grp[idx], med = apply(ex[, idx, drop = FALSE], 2, median),
                 q1 = apply(ex[, idx, drop = FALSE], 2, quantile, 0.25),
                 q3 = apply(ex[, idx, drop = FALSE], 2, quantile, 0.75))
    }))
    qc$study <- factor(qc$study, levels = names(studies))
    qc <- qc[order(qc$study, qc$group, qc$med), ]
    qc$sid <- ave(seq_len(nrow(qc)), qc$study, FUN = seq_along)
    p <- ggplot(qc, aes(sid, med, color = group)) +
      geom_linerange(aes(ymin = q1, ymax = q3), linewidth = 0.3) + geom_point(size = 0.6) +
      facet_wrap(~study, scales = "free_x", nrow = 1) +
      scale_color_manual(values = c(Control = COL_CTRL, Case = COL_CASE), labels = c("Control", case_lab)) +
      labs(x = "Sample (ordered by median within group)",
           y = "log2 expression (median, IQR)", color = NULL,
           title = "Per-sample expression distributions (QC)") +
      THEME + theme(axis.text.x = element_blank(), axis.ticks.x = element_blank())
    .save_fig(p, OUTDIR, "QC_sample_distributions", 12, 4)
  }, error = function(e) warning("[make_figures] [skip] QC panel: ", conditionMessage(e)))

  # ---- (2) Per-study volcano ---------------------------------------------
  tryCatch({
    vd <- do.call(rbind, lapply(names(de), function(s) {
      d <- de[[s]]; d$study <- s
      d$sig <- ifelse(d$FDR < 0.05 & abs(d$log2FC) >= 1,
                      ifelse(d$log2FC > 0, "Up", "Down"), "NS"); d
    }))
    p <- ggplot(vd, aes(log2FC, -log10(P), color = sig)) +
      geom_point(size = 0.4, alpha = 0.5) + facet_wrap(~study, nrow = 1) +
      scale_color_manual(values = c(Up = COL_CASE, Down = COL_CTRL, NS = "grey80")) +
      geom_vline(xintercept = c(-1, 1), linetype = 2, linewidth = 0.3) +
      labs(x = "log2 fold change", y = "-log10 P", color = NULL,
           title = paste0("Per-study differential expression: ", case_lab, " vs Control")) + THEME
    .save_fig(p, OUTDIR, "volcano_per_study", 12, 4)
  }, error = function(e) warning("[make_figures] [skip] volcano: ", conditionMessage(e)))

  # ---- (3) Concordance scatter of per-study log2FC (pairwise) -------------
  tryCatch({
    if (length(de) >= 2) {
      sg <- Reduce(intersect, lapply(de, function(d) d$gene))
      M <- sapply(de, function(d) d$log2FC[match(sg, d$gene)]); colnames(M) <- names(de)
      combs <- combn(colnames(M), 2, simplify = FALSE)
      dfc <- do.call(rbind, lapply(combs, function(cc) {
        r <- cor(M[, cc[1]], M[, cc[2]], use = "complete.obs")
        data.frame(x = M[, cc[1]], y = M[, cc[2]],
                   pair = sprintf("%s vs %s (r=%.2f)", cc[1], cc[2], r))
      }))
      p <- ggplot(dfc, aes(x, y)) + geom_point(size = 0.3, alpha = 0.3, color = "#75A025") +
        geom_abline(slope = 1, intercept = 0, linetype = 2, color = "grey40") +
        facet_wrap(~pair, nrow = 1) + labs(x = "log2FC", y = "log2FC",
        title = "Cross-cohort concordance of effect sizes") + THEME
      .save_fig(p, OUTDIR, "concordance_scatter", 12, 4.2)
    }
  }, error = function(e) warning("[make_figures] [skip] concordance: ", conditionMessage(e)))

  # ---- (4) Consensus heatmap: top core genes x cohort log2FC -------------
  tryCatch({
    if (requireNamespace("ComplexHeatmap", quietly = TRUE) && requireNamespace("circlize", quietly = TRUE)) {
      lfc_cols <- grep("^log2FC_", colnames(meta), value = TRUE)
      up <- meta[meta$core & meta$direction == "UP", ]; up <- up[order(-up$est), ]
      dn <- meta[meta$core & meta$direction == "DOWN", ]; dn <- dn[order(dn$est), ]
      sel <- rbind(head(up, 30), head(dn, 30))
      if (nrow(sel) >= 4 && length(lfc_cols) >= 2) {
        H <- as.matrix(sel[, lfc_cols]); rownames(H) <- sel$gene
        colnames(H) <- sub("^log2FC_", "", lfc_cols)
        col_fun <- circlize::colorRamp2(c(-4, 0, 4), c(COL_CTRL, "white", COL_CASE))
        drawer <- function() ComplexHeatmap::draw(ComplexHeatmap::Heatmap(
          H, name = "log2FC", col = col_fun, cluster_columns = FALSE, na_col = "grey85",
          row_split = sel$direction, row_names_gp = grid::gpar(fontsize = 6),
          column_names_gp = grid::gpar(fontsize = 8), column_names_rot = 45,
          column_title = paste0("Top core consensus genes: ", case_lab),
          column_title_gp = grid::gpar(fontsize = 10, fontface = "bold")),
          padding = grid::unit(c(2, 6, 2, 2), "mm"))   # extra left padding: avoid title clip
        .save_fig(drawer, OUTDIR, "consensus_heatmap", 5.4, min(10, 2 + nrow(sel) * 0.13), is_ggplot = FALSE)
      } else {
        warning("[make_figures] [skip] heatmap: too few core genes (", nrow(sel), ") or log2FC columns (", length(lfc_cols), ")")
      }
    } else {
      warning("[make_figures] [skip] heatmap: ComplexHeatmap/circlize not installed")
    }
  }, error = function(e) warning("[make_figures] [skip] heatmap: ", conditionMessage(e)))

  # ---- (5) Forest plot of top consensus genes -----------------------------
  tryCatch({
    lfc_cols <- grep("^log2FC_", colnames(meta), value = TRUE)
    top <- meta[meta$core, ]; top <- top[order(-abs(top$est)), ]; top <- head(top, 20)
    if (nrow(top) >= 3) {
      long <- reshape2::melt(top[, c("gene", "est", lfc_cols)], id.vars = c("gene", "est"),
                             variable.name = "cohort", value.name = "lfc")
      long$cohort <- sub("^log2FC_", "", long$cohort)
      long$gene <- factor(long$gene, levels = rev(top$gene))
      p <- ggplot(long, aes(lfc, gene)) +
        geom_vline(xintercept = 0, color = "grey60") +
        geom_point(aes(color = cohort), position = position_dodge(0.5), size = 1.3, alpha = 0.8) +
        geom_point(aes(x = est), color = "black", shape = 18, size = 2.6) +
        labs(x = "log2FC (points = cohorts; diamond = meta estimate)", y = NULL,
             title = paste0("Top consensus genes: ", case_lab)) + THEME
      .save_fig(p, OUTDIR, "forest_top_genes", 8, 7)
    }
  }, error = function(e) warning("[make_figures] [skip] forest: ", conditionMessage(e)))

  # ---- (6) ORA dotplots (GO-BP + Reactome, up/down) -----------------------
  tryCatch({
    wrap_lab <- function(x, width = 42) vapply(x, function(s) paste(strwrap(s, width = width), collapse = "\n"), "")
    mk <- function(obj, ttl) {
      if (is.null(obj)) return(NULL); df <- as.data.frame(obj); if (nrow(df) == 0) return(NULL)
      df <- head(df[order(df$p.adjust), ], 10)
      df$Description <- wrap_lab(df$Description); df$Description <- factor(df$Description, levels = rev(df$Description))
      gr <- sapply(strsplit(df$GeneRatio, "/"), function(z) as.numeric(z[1]) / as.numeric(z[2]))
      ggplot(df, aes(gr, Description, size = Count, color = p.adjust)) + geom_point() +
        scale_color_gradient(low = COL_CASE, high = "#0279EE") +
        labs(x = "GeneRatio", y = NULL, title = ttl, size = "Count", color = "p.adjust") +
        THEME + theme(plot.title = element_text(size = 9))
    }
    plots <- Filter(Negate(is.null), list(
      mk(enr$go_up, "GO-BP: UP"), mk(enr$go_dn, "GO-BP: DOWN"),
      mk(enr$re_up, "Reactome: UP"), mk(enr$re_dn, "Reactome: DOWN")))
    if (length(plots) > 0 && requireNamespace("patchwork", quietly = TRUE)) {
      p <- patchwork::wrap_plots(plots, ncol = 2)
      .save_fig(p, OUTDIR, "enrichment_ORA_dotplots", 15, 9)
    } else if (length(plots) > 0) {
      .save_fig(plots[[1]], OUTDIR, "enrichment_ORA_dotplots", 8, 5)
    }
  }, error = function(e) warning("[make_figures] [skip] ORA dotplots: ", conditionMessage(e)))

  # ---- (7) Hallmark GSEA barplot -----------------------------------------
  tryCatch({
    g <- enr$gsea
    if (!is.null(g)) {
      g <- g[g$padj < 0.05, ]; g <- g[order(g$NES), ]
      if (nrow(g) >= 1) {
        g$pathway <- gsub("HALLMARK_", "", g$pathway); g$pathway <- gsub("_", " ", g$pathway)
        g$pathway <- factor(g$pathway, levels = g$pathway)
        g$dir <- ifelse(g$NES > 0, "Up", "Down")
        p <- ggplot(g, aes(NES, pathway, fill = dir)) + geom_col() +
          scale_fill_manual(values = c(Up = COL_CASE, Down = COL_CTRL)) +
          labs(x = "Normalized enrichment score", y = NULL, fill = NULL,
               title = paste0("Hallmark pathway enrichment (GSEA): ", case_lab)) + THEME
        .save_fig(p, OUTDIR, "GSEA_hallmark_barplot", 9, max(4, nrow(g) * 0.22))
      }
    }
  }, error = function(e) warning("[make_figures] [skip] GSEA barplot: ", conditionMessage(e)))

  # ---- (8) Heterogeneous-control sensitivity: consensus preservation ------
  # Only drawn when a non-inflammatory-control sensitivity meta was run (>=2 such
  # cohorts). Shows primary consensus size, the non-inflammatory-subset consensus
  # size, and how many primary consensus genes are preserved in the subset.
  tryCatch({
    if (!is.null(sensitivity)) {
      pct <- 100 * sensitivity$preservation_fraction
      sd <- data.frame(
        metric = factor(
          c("Primary consensus\n(all cohorts)",
            "Subset consensus\n(non-inflammatory controls)",
            "Primary preserved\nin subset"),
          levels = c("Primary consensus\n(all cohorts)",
                     "Subset consensus\n(non-inflammatory controls)",
                     "Primary preserved\nin subset")),
        value = c(sensitivity$n_primary_consensus,
                  sensitivity$n_consensus_subset,
                  sensitivity$n_primary_preserved),
        grp = c("primary", "subset", "preserved"))
      p <- ggplot(sd, aes(metric, value, fill = grp)) +
        geom_col(width = 0.65, show.legend = FALSE) +
        geom_text(aes(label = value), vjust = -0.3, size = 3.2) +
        scale_fill_manual(values = c(primary = COL_CASE, subset = "#75A025", preserved = COL_CTRL)) +
        labs(x = NULL, y = "Number of consensus genes",
             title = sprintf("Control-heterogeneity sensitivity: %.0f%% of primary consensus genes preserved\nin non-inflammatory-control subset (%s)",
                             pct, paste(sensitivity$subset_cohorts, collapse = ", "))) +
        THEME + theme(plot.title = element_text(size = 9),
                      plot.margin = margin(6, 10, 6, 6))
      .save_fig(p, OUTDIR, "sensitivity_preservation", 8, 4.5)
    }
  }, error = function(e) warning("[make_figures] [skip] sensitivity fig: ", conditionMessage(e)))

  # ---- Post-run verification: check core expected figures exist ----------
  .verify_figures(OUTDIR)
  if (!is.null(sensitivity) && !file.exists(file.path(OUTDIR, "figures", "sensitivity_preservation.png")))
    warning("[make_figures] sensitivity result present but sensitivity_preservation.png was not produced.")

  invisible(TRUE)
}
