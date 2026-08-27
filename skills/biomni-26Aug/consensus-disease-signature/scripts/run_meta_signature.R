#!/usr/bin/env Rscript
# ============================================================================
# run_meta_signature.R  --  consensus-disease-signature skill (core engine)
#
# Build a consensus up/down transcriptional signature for ANY disease/condition by
# combining multiple bulk-transcriptome cohorts with random-effects effect-size
# meta-analysis, then run functional enrichment. Writes tables, figures, and a
# summary.json consumed by build_report.py.
#
# USAGE:
#   Rscript run_meta_signature.R <config.yaml>
#
# The config drives EVERYTHING (disease, cohorts, contrast, platform, output dir).
# See references/example_config.yaml for a worked example, and references/parameters.md
# for the full parameter reference.
#
# METHOD (validated; see the skill's SKILL.md for rationale):
#   ingest -> sample-select -> QC + duplicate check -> annotate/collapse ->
#   per-study DE (limma / limma-voom) -> random-effects meta (metafor) ->
#   consensus (FDR<0.05 & sign-consistent; core adds |log2FC|>=1) ->
#   heterogeneous-control sensitivity check ->
#   enrichment (ORA GO + Reactome + Hallmark GSEA).
#
# Literature validation (LiteratureSearch) and the PDF/infographic are handled by
# the agent + build_report.py, NOT here (this script writes summary.json for them).
# ============================================================================

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript run_meta_signature.R <config.yaml>")
CFG_PATH <- args[[1]]

# Persistent per-machine R library first (installs survive /workspace snapshots).
if (dir.exists("/workspace/.Rlib")) .libPaths(c("/workspace/.Rlib", .libPaths()))

suppressMessages({
  library(yaml); library(limma); library(metafor)
  library(clusterProfiler); library(org.Hs.eg.db); library(fgsea); library(msigdbr)
  library(ggplot2); library(jsonlite)
})
SCRIPT_DIR <- dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE)[1]))
if (is.na(SCRIPT_DIR) || SCRIPT_DIR == "") SCRIPT_DIR <- "."
source(file.path(SCRIPT_DIR, "annotate_platforms.R"))

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

cfg <- yaml::read_yaml(CFG_PATH)
DISEASE   <- cfg$disease %||% "disease"
OUTDIR    <- cfg$output_dir %||% "/mnt/results"
CONTRAST  <- cfg$contrast                       # list(case=..., control=..., column=...)
COHORTS   <- cfg$cohorts                        # list of per-cohort specs
LFC_CORE  <- cfg$core_lfc %||% 1.0
FDR_CUT   <- cfg$fdr %||% 0.05
# Control types considered "non-inflammatory" for the heterogeneous-control sensitivity
# meta-analysis (see section 4b). Case-insensitive substring match against each cohort's
# `control_type`. Config-overridable.
NONINFLAM_CTRL <- tolower(as.character(cfg$noninflammatory_control_types %||%
                                        c("normal", "trauma", "healthy", "control")))

dir.create(file.path(OUTDIR, "tables"),  recursive = TRUE, showWarnings = FALSE)
dir.create(file.path(OUTDIR, "figures"), recursive = TRUE, showWarnings = FALSE)
WORK <- "/workspace/meta_sig"; dir.create(WORK, recursive = TRUE, showWarnings = FALSE)

THEME <- theme_bw(base_size = 10) +
  theme(text = element_text(family = "Liberation Sans"),
        strip.background = element_rect(fill = "#ECE9E2"))
COL_CTRL <- "#0279EE"; COL_CASE <- "#FF9400"
say <- function(...) cat(sprintf(...), "\n")

# ============================================================================
# 1. INGEST each cohort -> list(exprs = gene x sample matrix, group = factor)
#    Two ingestion modes:
#      source: "geo"    -> retrieve GEO series with GEOquery, annotate by platform
#      source: "matrix" -> read a user expression matrix + sample metadata (CSV/TSV).
#                          (ArrayExpress/BioStudies E-MTAB flat files use this path.)
# ============================================================================
ingest_cohort <- function(co) {
  say("[ingest] %s (source=%s)", co$id, co$source)
  if (co$source == "geo") {
    suppressMessages(library(GEOquery))
    gse <- getGEO(co$id, GSEMatrix = TRUE, getGPL = FALSE,
                  destdir = WORK, AnnotGPL = FALSE)[[co$platform_index %||% 1]]
    pd <- Biobase::pData(gse)
    ex <- Biobase::exprs(gse)
    # If values look like linear intensities, log2 them (microarray convention).
    if (isTRUE(co$log2_transform) || (max(ex, na.rm = TRUE) > 100 && is.null(co$log2_transform)))
      ex <- log2(ex + 1)
    gene_ex <- collapse_to_genes(ex, co$platform)
    grp <- build_group(pd, co)
    keep <- !is.na(grp)
    list(exprs = gene_ex[, keep, drop = FALSE], group = droplevels(grp[keep]),
         platform = co$platform, type = co$type %||% "microarray",
         control_type = co$control_type %||% "unspecified")
  } else if (co$source == "matrix") {
    ex <- as.matrix(read_table_any(co$matrix_path, row1 = TRUE))
    md <- read_table_any(co$metadata_path, row1 = FALSE)
    rownames(md) <- md[[co$sample_id_col %||% 1]]
    common <- intersect(colnames(ex), rownames(md))
    if (length(common) < 3) stop(sprintf("[%s] <3 samples shared between matrix and metadata; check sample IDs.", co$id))
    ex <- ex[, common, drop = FALSE]; md <- md[common, , drop = FALSE]
    if (isTRUE(co$log2_transform)) ex <- log2(ex + 1)
    # If features aren't symbols, map them; else identity.
    gene_ex <- collapse_to_genes(ex, co$platform %||% "SYMBOL")
    grp <- build_group(md, co)
    keep <- !is.na(grp)
    list(exprs = gene_ex[, keep, drop = FALSE], group = droplevels(grp[keep]),
         platform = co$platform %||% "SYMBOL", type = co$type %||% "microarray",
         control_type = co$control_type %||% "unspecified")
  } else stop("Unknown cohort source: ", co$source)
}

# Flexible table reader (CSV or TSV, first column optionally rownames).
read_table_any <- function(path, row1 = TRUE) {
  sep <- if (grepl("\\.tsv$|\\.txt$", path)) "\t" else ","
  df <- read.delim(path, sep = sep, check.names = FALSE, stringsAsFactors = FALSE,
                   row.names = if (row1) 1 else NULL)
  df
}

# Build a 2-level contrast factor from a metadata frame using the config.
# co may specify: group_column, case_values, control_values, and optional
# `filters` (named list col -> allowed values) to restrict tissue/visit/etc.
build_group <- function(md, co) {
  gc   <- co$group_column %||% CONTRAST$column
  case <- co$case_values  %||% CONTRAST$case
  ctrl <- co$control_values %||% CONTRAST$control
  stopifnot(!is.null(gc), gc %in% colnames(md))
  v <- as.character(md[[gc]])
  # Apply arbitrary inclusion filters (e.g. tissue == "colon", visit == "Week 0").
  keep <- rep(TRUE, nrow(md))
  if (!is.null(co$filters)) for (fc in names(co$filters))
    keep <- keep & (as.character(md[[fc]]) %in% co$filters[[fc]])
  g <- rep(NA_character_, nrow(md))
  g[keep & v %in% case] <- "Case"
  g[keep & v %in% ctrl] <- "Control"
  factor(g, levels = c("Control", "Case"))
}

studies <- lapply(COHORTS, ingest_cohort)
names(studies) <- vapply(COHORTS, function(x) x$id, "")
for (s in names(studies))
  say("  %s: %d Control / %d Case, %d genes", s,
      sum(studies[[s]]$group == "Control"), sum(studies[[s]]$group == "Case"),
      nrow(studies[[s]]$exprs))
saveRDS(studies, file.path(WORK, "studies.rds"))

# ============================================================================
# 2. CROSS-COHORT DUPLICATE CHECK
#    Re-deposited series inflate apparent replication and bias the meta-analysis.
#    Cross-correlate mean expression on shared genes; flag pairs with r > 0.999.
#    (This is exactly how GSE87466 was found to be a re-deposit of GSE92415.)
# ============================================================================
dup_warnings <- character(0)
if (length(studies) >= 2) {
  ids <- names(studies)
  for (i in 1:(length(ids)-1)) for (j in (i+1):length(ids)) {
    g1 <- studies[[ids[i]]]$exprs; g2 <- studies[[ids[j]]]$exprs
    sg <- intersect(rownames(g1), rownames(g2))
    if (length(sg) < 50) next
    r <- suppressWarnings(cor(rowMeans(g1[sg, , drop = FALSE]),
                              rowMeans(g2[sg, , drop = FALSE])))
    if (!is.na(r) && r > 0.999)
      dup_warnings <- c(dup_warnings,
        sprintf("POSSIBLE DUPLICATE: %s vs %s (mean-expr r=%.4f > 0.999). Consider dropping one to preserve independence.",
                ids[i], ids[j], r))
  }
}
if (length(dup_warnings)) { say("!! %s", dup_warnings) } else say("[dup-check] no near-identical cohorts detected.")

# ============================================================================
# 3. PER-STUDY DIFFERENTIAL EXPRESSION
#    microarray -> limma moderated t-test on log2 intensities.
#    rnaseq     -> limma-voom on raw counts (filterByExpr + calcNormFactors).
#    Retain log2FC AND its SE (= stdev.unscaled * sqrt(s2.post)) -> the effect size.
# ============================================================================
run_de <- function(st) {
  group <- st$group; design <- model.matrix(~group)
  if ((st$type %||% "microarray") == "rnaseq") {
    suppressMessages(library(edgeR))
    dge <- DGEList(counts = round(st$exprs))
    keep <- filterByExpr(dge, design); dge <- dge[keep, , keep.lib.sizes = FALSE]
    dge <- calcNormFactors(dge)
    v <- voom(dge, design); fit <- eBayes(lmFit(v, design)); mat_rows <- rownames(v)
  } else {
    fit <- eBayes(lmFit(st$exprs, design)); mat_rows <- rownames(st$exprs)
  }
  tt <- topTable(fit, coef = 2, number = Inf, sort.by = "none")
  se <- fit$stdev.unscaled[, 2] * sqrt(fit$s2.post)
  data.frame(gene = mat_rows, log2FC = tt$logFC, SE = se[mat_rows],
             t = tt$t, AveExpr = tt$AveExpr, P = tt$P.Value, FDR = tt$adj.P.Val,
             row.names = NULL)
}
de <- lapply(studies, run_de)
for (s in names(de)) say("  DE %s: %d genes, %d at FDR<%.2g", s, nrow(de[[s]]),
                         sum(de[[s]]$FDR < FDR_CUT, na.rm = TRUE), FDR_CUT)
saveRDS(de, file.path(WORK, "de.rds"))

# ============================================================================
# 4. RANDOM-EFFECTS EFFECT-SIZE META-ANALYSIS (metafor)
#    Per gene present in >= 2 cohorts: rma(yi=log2FC, sei=SE, REML) with DL fallback.
#    Refactored into run_meta() so the same estimator can be reused for the
#    heterogeneous-control sensitivity analysis (section 4b) on a cohort subset.
# ============================================================================
# run_meta: given a named list of per-cohort DE data.frames (each with columns
# gene, log2FC, SE), return a meta data.frame with pooled est/se/z/p/I2/tau2/k,
# BH-FDR, direction, and consensus/core flags. `fdr` and `lfc_core` set the flags.
run_meta <- function(de_list, fdr = FDR_CUT, lfc_core = LFC_CORE) {
  all_genes <- Reduce(union, lapply(de_list, function(d) d$gene))
  lfc <- sapply(de_list, function(d) d$log2FC[match(all_genes, d$gene)])
  sei <- sapply(de_list, function(d) d$SE[match(all_genes, d$gene)])
  if (is.null(dim(lfc))) { lfc <- matrix(lfc, ncol = length(de_list)); sei <- matrix(sei, ncol = length(de_list)) }
  rownames(lfc) <- rownames(sei) <- all_genes
  colnames(lfc) <- colnames(sei) <- names(de_list)
  k <- rowSums(!is.na(lfc))
  test_genes <- all_genes[k >= 2]
  meta_one <- function(g) {
    yi <- lfc[g, ]; si <- sei[g, ]; ok <- !is.na(yi) & !is.na(si) & si > 0
    yi <- yi[ok]; si <- si[ok]
    fit <- tryCatch(rma(yi = yi, sei = si, method = "REML", control = list(maxiter = 200)),
                    error = function(e) tryCatch(rma(yi = yi, sei = si, method = "DL"),
                                                 error = function(e2) NULL))
    if (is.null(fit)) return(c(est = NA, se = NA, z = NA, p = NA, I2 = NA, tau2 = NA, k = length(yi), consistent = NA))
    c(est = as.numeric(fit$b), se = fit$se, z = fit$zval, p = fit$pval,
      I2 = fit$I2, tau2 = fit$tau2, k = length(yi),
      consistent = as.numeric(all(yi > 0) | all(yi < 0)))
  }
  M <- t(vapply(test_genes, meta_one, numeric(8)))
  m <- data.frame(gene = test_genes, M, row.names = NULL)
  m$FDR <- p.adjust(m$p, method = "BH")
  m$direction <- ifelse(m$est > 0, "UP", "DOWN")
  m$consensus <- m$FDR < fdr & m$consistent == 1
  m$core <- m$consensus & abs(m$est) >= lfc_core
  for (s in names(de_list)) m[[paste0("log2FC_", s)]] <- lfc[m$gene, s]
  m
}

say("[meta] running PRIMARY random-effects model over all %d cohorts...", length(de))
meta <- run_meta(de, FDR_CUT, LFC_CORE)
say("[meta] genes in >=2 cohorts: %d", nrow(meta))
saveRDS(meta, file.path(WORK, "meta.rds"))
write.csv(meta, file.path(OUTDIR, "tables", "meta_analysis_full.csv"), row.names = FALSE)

n_cons <- sum(meta$consensus); n_core <- sum(meta$core)
# --- count-consistency guard: consensus (FDR & sign-consistent) is a SUBSET of ---
# --- FDR-significant, so it must never exceed n_fdr_sig. These are DISTINCT       ---
# --- quantities and are reported under distinct keys in summary.json.             ---
n_fdr_sig <- sum(meta$FDR < FDR_CUT, na.rm = TRUE)
if (n_cons > n_fdr_sig)
  warning(sprintf("[consistency] n_consensus (%d) > n_fdr_sig (%d) -- should be impossible; check meta table.",
                  n_cons, n_fdr_sig))
say("[counts] FDR<%.2g significant (any direction): %d ; direction-consistent consensus: %d",
    FDR_CUT, n_fdr_sig, n_cons)
say("[consensus] %d consensus (UP %d / DOWN %d); core |log2FC|>=%.1f: %d",
    n_cons, sum(meta$consensus & meta$direction == "UP"),
    sum(meta$consensus & meta$direction == "DOWN"), LFC_CORE, n_core)
write.csv(meta[meta$consensus & meta$direction == "UP", ],
          file.path(OUTDIR, "tables", "consensus_UP_genes.csv"), row.names = FALSE)
write.csv(meta[meta$consensus & meta$direction == "DOWN", ],
          file.path(OUTDIR, "tables", "consensus_DOWN_genes.csv"), row.names = FALSE)

# ============================================================================
# 4b. HETEROGENEOUS-CONTROL HANDLING + SENSITIVITY META-ANALYSIS
#    Different cohorts may use different control groups (e.g. Normal, OA, trauma).
#    Pooling them silently can confound the disease signal (disease controls such
#    as OA carry their own inflammation). We therefore (i) record each cohort's
#    control_type, (ii) flag heterogeneity, and (iii) if >=2 cohorts share a
#    "non-inflammatory" control, re-run the SAME meta-analysis on that subset and
#    report how many PRIMARY consensus genes are preserved (direction-consistent
#    & FDR<fdr) in the cleaner subset -- a robustness check, not a replacement.
# ============================================================================
ctrl_types <- vapply(studies, function(s) s$control_type %||% "unspecified", "")
uniq_ctrl  <- unique(tolower(ctrl_types[nzchar(ctrl_types) & ctrl_types != "unspecified"]))
heterogeneous_controls <- length(uniq_ctrl) > 1
is_noninflam <- function(ct) any(vapply(NONINFLAM_CTRL, function(p) grepl(p, tolower(ct), fixed = TRUE), logical(1)))
noninflam_ids <- names(studies)[vapply(ctrl_types, is_noninflam, logical(1))]
say("[controls] control_type per cohort: %s", paste(sprintf("%s=%s", names(ctrl_types), ctrl_types), collapse = ", "))
say("[controls] heterogeneous=%s ; non-inflammatory-control cohorts: %s",
    heterogeneous_controls, if (length(noninflam_ids)) paste(noninflam_ids, collapse = ", ") else "(none)")

sensitivity <- NULL
if (heterogeneous_controls && length(noninflam_ids) >= 2) {
  say("[sensitivity] re-running meta on %d non-inflammatory-control cohorts: %s",
      length(noninflam_ids), paste(noninflam_ids, collapse = ", "))
  meta_ni <- run_meta(de[noninflam_ids], FDR_CUT, LFC_CORE)
  write.csv(meta_ni, file.path(OUTDIR, "tables", "sensitivity_noninflammatory_meta.csv"), row.names = FALSE)
  saveRDS(meta_ni, file.path(WORK, "meta_ni.rds"))
  # preservation: of primary consensus genes tested in the subset, fraction that
  # are FDR<fdr AND same sign in the subset.
  prim_cons <- meta$gene[meta$consensus]
  ni_by_gene <- meta_ni[match(prim_cons, meta_ni$gene), ]
  tested_in_ni <- !is.na(ni_by_gene$gene)
  same_dir <- sign(ni_by_gene$est) == sign(meta$est[match(prim_cons, meta$gene)])
  preserved <- tested_in_ni & ni_by_gene$consensus & same_dir
  sensitivity <- list(
    subset_cohorts = noninflam_ids,
    n_subset_cohorts = length(noninflam_ids),
    subset_control_types = unname(ctrl_types[noninflam_ids]),
    n_consensus_subset = sum(meta_ni$consensus),
    n_primary_consensus = length(prim_cons),
    n_primary_tested_in_subset = sum(tested_in_ni),
    n_primary_preserved = sum(preserved, na.rm = TRUE),
    preservation_fraction = round(sum(preserved, na.rm = TRUE) / max(1, sum(tested_in_ni)), 3)
  )
  say("[sensitivity] primary consensus=%d ; subset consensus=%d ; preserved=%d/%d (%.0f%%)",
      sensitivity$n_primary_consensus, sensitivity$n_consensus_subset,
      sensitivity$n_primary_preserved, sensitivity$n_primary_tested_in_subset,
      100 * sensitivity$preservation_fraction)
} else if (heterogeneous_controls) {
  say("[sensitivity] skipped: <2 cohorts share a non-inflammatory control (heterogeneous controls still flagged).")
}

# ============================================================================
# 5. FUNCTIONAL ENRICHMENT
#    ORA (GO-BP + Reactome) on core UP / core DOWN separately; universe = meta-tested.
#    GSEA (Hallmark) on the z-score-ranked list.
#    KEGG was removed (not licensed for commercial use); Reactome (ReactomePA,
#    CC0/CC-BY) replaces it. See DATA_SOURCES.md.
#    NOTE: load ONLY Hallmark / small MSigDB subcollections. Loading full C2
#    (~7000 sets) spiked memory and caused OOM in the UC run.
# ============================================================================
sym2entrez <- function(syms) {
  valid <- intersect(unique(syms), AnnotationDbi::keys(org.Hs.eg.db, keytype = "SYMBOL"))
  if (length(valid) == 0) {
    warning("[enrichment] no input features are valid gene SYMBOLs; enrichment will be skipped.")
    return(setNames(character(0), character(0)))
  }
  m <- suppressMessages(AnnotationDbi::select(org.Hs.eg.db, keys = valid,
                                              columns = "ENTREZID", keytype = "SYMBOL"))
  m <- m[!is.na(m$ENTREZID) & !duplicated(m$SYMBOL), ]
  setNames(m$ENTREZID, m$SYMBOL)
}
lut <- sym2entrez(meta$gene)
universe <- unique(na.omit(lut[meta$gene]))
core_up <- na.omit(lut[meta$gene[meta$core & meta$direction == "UP"]])
core_dn <- na.omit(lut[meta$gene[meta$core & meta$direction == "DOWN"]])

safe_ora_go <- function(genes) tryCatch({
  e <- enrichGO(genes, OrgDb = org.Hs.eg.db, ont = "BP", pAdjustMethod = "BH",
                pvalueCutoff = 0.05, qvalueCutoff = 0.1, universe = universe, readable = TRUE)
  if (!is.null(e) && nrow(as.data.frame(e)) > 0) clusterProfiler::simplify(e, cutoff = 0.7) else e
}, error = function(err) NULL)
# Reactome ORA (ReactomePA::enrichPathway) replaces KEGG. Commercial-use friendly
# (Reactome is CC0/CC-BY). Wrapped in tryCatch so a missing package / network issue
# degrades gracefully to a missing table rather than aborting the run.
safe_ora_reactome <- function(genes) tryCatch({
  if (!requireNamespace("ReactomePA", quietly = TRUE)) return(NULL)
  e <- ReactomePA::enrichPathway(gene = as.character(genes), organism = "human",
                                 pAdjustMethod = "BH", pvalueCutoff = 0.05,
                                 universe = as.character(universe), readable = TRUE)
  if (!is.null(e) && nrow(as.data.frame(e)) > 0) e else NULL
}, error = function(err) NULL)

go_up <- safe_ora_go(core_up); go_dn <- safe_ora_go(core_dn)
re_up <- safe_ora_reactome(core_up); re_dn <- safe_ora_reactome(core_dn)
wdf <- function(x, path) if (!is.null(x) && nrow(as.data.frame(x)) > 0) write.csv(as.data.frame(x), path, row.names = FALSE)
wdf(go_up, file.path(OUTDIR, "tables", "enrichment_GO_BP_UP.csv"))
wdf(go_dn, file.path(OUTDIR, "tables", "enrichment_GO_BP_DOWN.csv"))
wdf(re_up, file.path(OUTDIR, "tables", "enrichment_Reactome_UP.csv"))
wdf(re_dn, file.path(OUTDIR, "tables", "enrichment_Reactome_DOWN.csv"))

gsea <- tryCatch({
  hm <- msigdbr(species = "Homo sapiens", category = "H")
  paths <- split(hm$entrez_gene, hm$gs_name)
  rk <- setNames(meta$z, lut[meta$gene]); rk <- rk[!is.na(names(rk)) & !is.na(rk)]
  rk <- sort(rk, decreasing = TRUE)
  set.seed(1)
  as.data.frame(fgsea(pathways = paths, stats = rk, minSize = 10, maxSize = 500, eps = 0))
}, error = function(e) NULL)
if (!is.null(gsea)) {
  gsea$leadingEdge <- vapply(gsea$leadingEdge, function(x) paste(head(x, 6), collapse = ","), "")
  write.csv(gsea, file.path(OUTDIR, "tables", "GSEA_hallmark.csv"), row.names = FALSE)
}
saveRDS(list(go_up=go_up, go_dn=go_dn, re_up=re_up, re_dn=re_dn, gsea=gsea),
        file.path(WORK, "enrichment.rds"))

# ============================================================================
# 6. FIGURES  (all saved PNG dpi=150 + SVG; agent verifies via media_output_check)
#    Generated by a companion sourced file to keep this engine readable.
# ============================================================================
source(file.path(SCRIPT_DIR, "make_figures.R"), local = TRUE)  # defines make_all_figures()
make_all_figures(studies, de, meta,
                 list(go_up=go_up, go_dn=go_dn, re_up=re_up, re_dn=re_dn, gsea=gsea),
                 OUTDIR, DISEASE, COL_CTRL, COL_CASE, THEME, sensitivity = sensitivity)

# ============================================================================
# 7. summary.json  (headline numbers + top genes for build_report.py)
# ============================================================================
topN <- function(dir, n = 15) {
  d <- meta[meta$core & meta$direction == dir, ]
  d <- d[order(if (dir == "UP") -d$est else d$est), ]
  head(data.frame(gene = d$gene, log2FC = round(d$est, 2), FDR = signif(d$FDR, 3)), n)
}
pairwise_r <- NULL
if (length(de) >= 2) {
  sg <- Reduce(intersect, lapply(de, function(d) d$gene))
  Mlfc <- sapply(de, function(d) d$log2FC[match(sg, d$gene)])
  cc <- cor(Mlfc, use = "pairwise.complete.obs")
  pairwise_r <- as.data.frame(as.table(cc)); pairwise_r <- pairwise_r[pairwise_r$Var1 != pairwise_r$Var2, ]
}
summary <- list(
  disease = DISEASE, generated = as.character(Sys.Date()),
  cohorts = lapply(names(studies), function(s) list(
    id = s, platform = studies[[s]]$platform, type = studies[[s]]$type,
    control_type = studies[[s]]$control_type %||% "unspecified",
    n_control = sum(studies[[s]]$group == "Control"), n_case = sum(studies[[s]]$group == "Case"))),
  contrast = list(case = CONTRAST$case, control = CONTRAST$control),
  # Distinct data-type summary so the report describes the ACTUAL inputs (limma
  # vs limma-voom) rather than assuming a platform.
  data_types = unname(sort(unique(vapply(studies, function(s) s$type %||% "microarray", "")))),
  # Heterogeneous-control handling (section 4b).
  control_types = unname(ctrl_types),
  heterogeneous_controls = heterogeneous_controls,
  sensitivity = sensitivity,
  # n_fdr_sig = FDR<fdr in ANY direction; n_consensus = that AND sign-consistent
  # across all contributing cohorts. Distinct quantities; never interchangeable.
  n_genes_tested = nrow(meta), n_fdr_sig = n_fdr_sig,
  n_consensus = n_cons, n_consensus_up = sum(meta$consensus & meta$direction == "UP"),
  n_consensus_down = sum(meta$consensus & meta$direction == "DOWN"),
  n_core = n_core, n_core_up = sum(meta$core & meta$direction == "UP"),
  n_core_down = sum(meta$core & meta$direction == "DOWN"),
  median_I2_sig = round(median(meta$I2[meta$FDR < FDR_CUT], na.rm = TRUE), 1),
  fdr_cut = FDR_CUT, core_lfc = LFC_CORE,
  pairwise_lfc_r = if (!is.null(pairwise_r)) round(pairwise_r$Freq, 3) else NULL,
  pairwise_r_min = if (!is.null(pairwise_r)) round(min(pairwise_r$Freq), 2) else NULL,
  pairwise_r_max = if (!is.null(pairwise_r)) round(max(pairwise_r$Freq), 2) else NULL,
  top_up = topN("UP"), top_down = topN("DOWN"),
  n_gsea_sig = if (!is.null(gsea)) sum(gsea$padj < 0.05, na.rm = TRUE) else 0,
  duplicate_warnings = dup_warnings
)
write_json(summary, file.path(OUTDIR, "tables", "summary.json"),
           auto_unbox = TRUE, pretty = TRUE, digits = 6)
say("[done] wrote summary.json and all tables/figures under %s", OUTDIR)
