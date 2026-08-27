#!/usr/bin/env Rscript
# =============================================================================
# Immune repertoire analysis (TCR / BCR, AIRR) with immunarch 0.9.1
# Clonality, diversity, V/J gene usage, repertoire overlap + group comparison.
#
# Modality-aware: auto-detects single-cell vs bulk and adapts which diversity
# estimators are trusted and how clonality is framed (see MODALITY notes below).
#
# DEFAULT PATH: run this script as-is after editing the CONFIG block, OR pass a
# JSON/YAML-free config via environment variables (see CONFIG). It writes
# per-sample tables (CSV) + figures (PNG + editable SVG) and a metrics summary
# JSON that the companion make_report.py consumes.
#
# INSTALL (critical): immunarch is NOT preinstalled. Install v0.9.1 (classic
# in-memory loader, NO duckdb) on a MULTI-CORE machine:
#   install.packages("ggraph", Ncpus=8)                       # dep that fails otherwise
#   remotes::install_version("immunarch", version="0.9.1",
#                            dependencies=NA, upgrade="never") # NO duckdb
# Do NOT install the current CRAN immunarch: it pulls immundata->duckdb, which
# compiles pathologically slowly (>40 min even at -j8) and is unused here.
# =============================================================================

suppressWarnings(suppressMessages({
  library(immunarch); library(ggplot2); library(dplyr); library(tidyr)
}))

# ------------------------------ CONFIG ---------------------------------------
# Edit these, or override any via environment variable of the same name.
getcfg <- function(name, default) { v <- Sys.getenv(name, unset=NA); if (is.na(v) || v=="") default else v }

DATA_DIR   <- getcfg("REP_DATA_DIR",  "/mnt/shared-workspace/shared/rep_data") # dir of repertoire files + metadata.txt
OUT_DIR    <- getcfg("REP_OUT_DIR",   "/mnt/results/repertoire")               # deliverables root (S3-backed)
WORK_DIR   <- getcfg("REP_WORK_DIR",  "/workspace/rep_run")                    # local scratch (fast, POSIX)
CHAIN      <- getcfg("REP_CHAIN",     "auto")     # auto | TRB | TRA | TRG | TRD | IGH | IGK | IGL
RECEPTOR   <- getcfg("REP_RECEPTOR",  "auto")     # auto | TCR | BCR  (drives caveats + default chain)
MODALITY   <- getcfg("REP_MODALITY",  "auto")     # auto | single_cell | bulk
GROUP_COLS <- getcfg("REP_GROUP_COLS","")         # comma-sep metadata columns to group/compare by (optional)
SPECIES    <- getcfg("REP_SPECIES",   "hs")       # hs | mm  (immunarch gene-usage tables)
SEED       <- as.integer(getcfg("REP_SEED", "42"))
TOP_HEAD   <- c(10, 100, 1000)                     # top-clone occupancy cutoffs

set.seed(SEED)
FIG  <- file.path(OUT_DIR, "figures")
TAB  <- file.path(OUT_DIR, "tables")
WFIG <- file.path(WORK_DIR, "figures")
for (d in c(FIG, TAB, WFIG, WORK_DIR)) dir.create(d, recursive=TRUE, showWarnings=FALSE)

# Okabe-Ito colorblind-safe palette (recycled across samples/groups)
OKABE <- c("#0072B2","#E69F00","#009E73","#CC79A7","#D55E00","#56B4E9","#F0E442","#000000")
theme_set(theme_bw(base_size=12) + theme(text=element_text(family="Liberation Sans")))

logmsg <- function(...) cat(sprintf("[%s] ", format(Sys.time(), "%H:%M:%S")), ..., "\n")

# --- FUSE-safe figure saver: PNG must be written locally then copied (R's
#     file.copy yields 0-byte files on S3 mounts); SVG text writes directly. ---
save_fig <- function(p, name, w=8, h=5) {
  pw <- file.path(WFIG, paste0(name, ".png"))
  suppressMessages(ggsave(pw, p, width=w, height=h, dpi=150, bg="white"))
  ok <- suppressWarnings(system2("cp", c(pw, file.path(FIG, paste0(name, ".png")))))
  tryCatch(suppressMessages(ggsave(file.path(FIG, paste0(name, ".svg")), p, width=w, height=h, bg="white")),
           error=function(e) NULL)
  invisible(name)
}

wtab <- function(df, name) write.csv(df, file.path(TAB, paste0(name, ".csv")), row.names=FALSE)

# ------------------------------ LOAD -----------------------------------------
# immunarch repLoad auto-detects 10x (filtered_contig_annotations.csv), MiXCR,
# Adaptive/immunoSEQ, AIRR-C TSV, and immunarch-native formats. A metadata.txt
# (tab-separated, first column "Sample") in DATA_DIR is joined into $meta.
logmsg("Loading repertoires from", DATA_DIR)
immdata <- repLoad(DATA_DIR)
samples <- names(immdata$data)
if (length(samples) == 0) stop("No samples loaded from ", DATA_DIR)
logmsg("Loaded", length(samples), "samples:", paste(samples, collapse=", "))

# Stable sample order (metadata order if present, else load order)
SAMPLE_ORDER <- samples
meta <- immdata$meta
if (!is.null(meta) && "Sample" %in% colnames(meta)) {
  SAMPLE_ORDER <- meta$Sample[meta$Sample %in% samples]
  if (length(SAMPLE_ORDER) < length(samples)) SAMPLE_ORDER <- unique(c(SAMPLE_ORDER, samples))
}
immdata$data <- immdata$data[SAMPLE_ORDER]
PAL <- setNames(rep(OKABE, length.out=length(SAMPLE_ORDER)), SAMPLE_ORDER)

# ------------------------ RECEPTOR / CHAIN / MODALITY ------------------------
# Peek at V.name across samples to infer receptor + pick a default chain.
all_v <- unlist(lapply(immdata$data, function(d) if ("V.name" %in% colnames(d)) as.character(d$V.name) else character(0)))
has_TR <- any(grepl("^TR[ABGD]V", all_v)); has_IG <- any(grepl("^IG[HKL]V", all_v))
if (RECEPTOR == "auto") RECEPTOR <- if (has_IG && !has_TR) "BCR" else if (has_TR && has_IG) "MIXED" else if (has_TR) "TCR" else "UNKNOWN"
if (CHAIN == "auto") {
  CHAIN <- if (RECEPTOR == "BCR") "IGH" else "TRB"   # TRB / IGH are the canonical single-chain default
}
logmsg("Receptor:", RECEPTOR, "| analysis chain:", CHAIN)

# immunarch gene-usage table keys, e.g. "hs.trbv" / "hs.ighv"
gu_key <- function(seg) tolower(paste0(SPECIES, ".", CHAIN, seg))   # seg = "v" or "j"

# Modality auto-detection: single-cell repertoires are dominated by singletons
# (nearly every clonotype seen once => F2~0 => Chao1 explodes). Bulk data has a
# real abundance distribution with many doubletons.
singleton_frac <- function(d) { cc <- d$Clones; if (is.null(cc) || !length(cc)) return(NA_real_); mean(cc == 1) }
sfr <- sapply(immdata$data, singleton_frac)
if (MODALITY == "auto") {
  MODALITY <- if (mean(sfr, na.rm=TRUE) >= 0.85) "single_cell" else "bulk"
}
logmsg(sprintf("Modality: %s (mean singleton fraction = %.3f)", MODALITY, mean(sfr, na.rm=TRUE)))

# ------------------------------ SUMMARY --------------------------------------
# Manual Shannon + clonality index (immunarch has no valid "entropy" method).
shannon_of <- function(d) { p <- d$Proportion; p <- p[p > 0]; -sum(p * log(p)) }
clonality_of <- function(d) { p <- d$Proportion; p <- p[p > 0]; if (length(p) < 2) return(0); 1 - (-sum(p*log(p)))/log(length(p)) }
summ <- data.frame(
  Sample          = SAMPLE_ORDER,
  n_clonotypes    = sapply(immdata$data, nrow),
  total_abundance = sapply(immdata$data, function(d) sum(d$Clones)),
  singleton_frac  = round(sfr[SAMPLE_ORDER], 4),
  shannon_entropy = round(sapply(immdata$data, shannon_of), 3),
  clonality_index = round(sapply(immdata$data, clonality_of), 4),
  row.names = NULL
)
if (!is.null(meta)) {
  extra <- setdiff(colnames(meta), "Sample")
  if (length(extra)) summ <- merge(meta[, c("Sample", extra)], summ, by="Sample")
  summ <- summ[match(SAMPLE_ORDER, summ$Sample), ]
}
wtab(summ, "sample_summary")
logmsg("Wrote sample_summary")

# ------------------------------ CLONALITY ------------------------------------
clon_top   <- repClonality(immdata$data, .method="top",  .head=TOP_HEAD)   # matrix samples x head
clon_homeo <- repClonality(immdata$data, .method="homeo")                  # matrix samples x 5 bins
top_df   <- data.frame(Sample=rownames(clon_top), as.data.frame(unclass(clon_top)), check.names=FALSE, row.names=NULL)
homeo_df <- data.frame(Sample=rownames(clon_homeo), as.data.frame(unclass(clon_homeo)), check.names=FALSE, row.names=NULL)
wtab(top_df, "clonality_top"); wtab(homeo_df, "clonality_homeo")
save_fig(vis(clon_top),   "clonality_top_clones", 8, 5)
save_fig(vis(clon_homeo), "clonality_homeostasis", 8, 5)
logmsg("Clonality done")

# ------------------------------ DIVERSITY ------------------------------------
# Robust scalar extractor: handles rowname-indexed matrices (chao1/d50/top) AND
# Sample-column data.frames (inv.simp/gini.simp) -> named vector over SAMPLE_ORDER.
scal <- function(obj, col) {
  df <- as.data.frame(unclass(obj))
  if ("Sample" %in% colnames(df)) { v <- setNames(df[[col]], df$Sample) }
  else { v <- setNames(df[[col]], rownames(df)) }
  v[SAMPLE_ORDER]
}
div_chao <- repDiversity(immdata$data, .method="chao1")
div_inv  <- repDiversity(immdata$data, .method="inv.simp")
div_gini <- repDiversity(immdata$data, .method="gini.simp")
div_d50  <- repDiversity(immdata$data, .method="d50")
div_tab <- data.frame(
  Sample      = SAMPLE_ORDER,
  Chao1       = round(scal(div_chao, "Estimator"), 1),
  Shannon     = round(sapply(immdata$data[SAMPLE_ORDER], shannon_of), 3),
  InvSimpson  = round(scal(div_inv, "Value"), 2),
  GiniSimpson = round(scal(div_gini, "Value"), 4),
  D50         = scal(div_d50, "Clones"),
  row.names   = NULL
)
if (!is.null(meta)) {
  extra <- setdiff(colnames(meta), "Sample")
  if (length(extra)) div_tab <- merge(div_tab, meta[, c("Sample", extra)], by="Sample")
  div_tab <- div_tab[match(SAMPLE_ORDER, div_tab$Sample), ]
}
wtab(div_tab, "diversity_metrics")

# Faceted diversity bar chart (color by first group col if available, else Sample)
grpcol <- if (nchar(GROUP_COLS)) trimws(strsplit(GROUP_COLS, ",")[[1]])[1] else NA
long_div <- div_tab %>%
  select(Sample, any_of(c("Chao1","Shannon","InvSimpson","GiniSimpson","D50"))) %>%
  pivot_longer(-Sample, names_to="Metric", values_to="Value")
long_div$Sample <- factor(long_div$Sample, levels=SAMPLE_ORDER)
fill_aes <- if (!is.na(grpcol) && grpcol %in% colnames(div_tab)) {
  long_div <- merge(long_div, div_tab[, c("Sample", grpcol)], by="Sample"); grpcol
} else "Sample"
p_div <- ggplot(long_div, aes(x=Sample, y=Value, fill=.data[[fill_aes]])) +
  geom_col() + facet_wrap(~Metric, scales="free_y") +
  scale_fill_manual(values=OKABE) +
  theme(axis.text.x=element_text(angle=45, hjust=1), legend.position="right") +
  labs(title="Diversity metrics across samples", x=NULL, y=NULL)
save_fig(p_div, "diversity_all_metrics", 9, 6)

# Rarefaction (depth control) — plot clean lines from immunarch's raref object.
rr <- repDiversity(immdata$data, .method="raref", .verbose=FALSE)
rdf <- as.data.frame(rr); cn <- colnames(rdf)
size_col <- cn[grep("Size|size", cn)][1]; val_col <- cn[grep("^Mean$|Value|Estimator", cn)][1]
samp_col <- cn[grep("Sample", cn)][1]
if (!is.na(size_col) && !is.na(val_col) && !is.na(samp_col)) {
  rdf$Sample <- factor(rdf[[samp_col]], levels=SAMPLE_ORDER)
  p_rar <- ggplot(rdf, aes(x=.data[[size_col]], y=.data[[val_col]], color=Sample)) +
    geom_line(linewidth=0.9) + scale_color_manual(values=PAL) +
    labs(title="Rarefaction: clonotype richness vs. sampled units",
         x="Sampled units (cells or templates)", y="Estimated unique clonotypes")
  save_fig(p_rar, "diversity_rarefaction", 8, 5.5)
} else { save_fig(vis(rr), "diversity_rarefaction", 8, 5.5) }
logmsg("Diversity done")

# ------------------------------ GENE USAGE -----------------------------------
gu_v <- geneUsage(immdata$data, gu_key("v"), .norm=TRUE, .ambig="exc")
gu_j <- geneUsage(immdata$data, gu_key("j"), .norm=TRUE, .ambig="exc")
wtab(gu_v, paste0("vgene_usage_", CHAIN, "V")); wtab(gu_j, paste0("jgene_usage_", CHAIN, "J"))
save_fig(vis(gu_v), paste0("vgene_usage_", CHAIN, "V"), 10, 5)
save_fig(vis(gu_j), paste0("jgene_usage_", CHAIN, "J"), 8, 5)
# Cross-sample usage similarity (needs >=2 samples)
if (length(SAMPLE_ORDER) >= 2) {
  gu_js  <- geneUsageAnalysis(gu_v, .method="js",  .verbose=FALSE)
  gu_cor <- geneUsageAnalysis(gu_v, .method="cor", .verbose=FALSE)
  save_fig(vis(gu_js),  paste0("vgene_usage_JS_heatmap"), 7, 6)
  save_fig(vis(gu_cor), paste0("vgene_usage_cor_heatmap"), 7, 6)
}
logmsg("Gene usage done")

# ------------------------------ OVERLAP --------------------------------------
if (length(SAMPLE_ORDER) >= 2) {
  ov_pub <- repOverlap(immdata$data, .method="public",   .verbose=FALSE)
  ov_mor <- repOverlap(immdata$data, .method="morisita", .verbose=FALSE)
  write.csv(as.matrix(ov_pub), file.path(TAB, "overlap_public_shared.csv"))
  write.csv(as.matrix(ov_mor), file.path(TAB, "overlap_morisita.csv"))
  # Log-scaled shared-clonotype heatmap so an outlier (e.g. same-donor pair)
  # does not compress the small values into one indistinguishable band.
  m <- as.matrix(ov_pub)[SAMPLE_ORDER, SAMPLE_ORDER]
  long <- as.data.frame(as.table(m)); colnames(long) <- c("S1","S2","shared")
  long$S1 <- factor(long$S1, levels=SAMPLE_ORDER); long$S2 <- factor(long$S2, levels=rev(SAMPLE_ORDER))
  long$lab <- ifelse(is.na(long$shared), "", as.character(long$shared))
  p_ov <- ggplot(long, aes(S1, S2, fill=log1p(shared))) +
    geom_tile(color="white", linewidth=0.6) + geom_text(aes(label=lab), size=3.4) +
    scale_fill_gradient(low="#FAF3E0", high="#B34700", na.value="grey88",
                        name="shared\n(log scale)") +
    coord_equal() + theme(axis.text.x=element_text(angle=45, hjust=1)) +
    labs(title="Shared clonotypes between samples (public overlap)", x=NULL, y=NULL)
  save_fig(p_ov, "overlap_public_heatmap", 7.5, 6)
  save_fig(vis(ov_mor), "overlap_morisita_heatmap", 7, 6)
  # Flag likely same-source pairs (shared clonotypes >> background)
  offdiag <- m[upper.tri(m)]
  thr <- max(10, 5 * stats::median(offdiag, na.rm=TRUE))
  hits <- which(m >= thr & upper.tri(m), arr.ind=TRUE)
  if (nrow(hits)) {
    flag <- data.frame(SampleA=rownames(m)[hits[,1]], SampleB=colnames(m)[hits[,2]],
                       shared=m[hits], note="shared clonotypes >> background (possible same source/replicate)")
    wtab(flag, "overlap_flagged_pairs")
    logmsg("FLAGGED high-overlap pairs (possible same donor/replicate):")
    print(flag)
  }
  logmsg("Overlap done")
}

# --------------------- GROUP COMPARISON (Wilcoxon) ---------------------------
# Two-sided Wilcoxon rank-sum on scalar diversity metrics between two groups of
# a chosen metadata column. Exact p-values; exploratory when n is small.
if (nchar(GROUP_COLS) && !is.null(meta)) {
  gc <- trimws(strsplit(GROUP_COLS, ",")[[1]])[1]
  if (gc %in% colnames(div_tab)) {
    lv <- unique(na.omit(div_tab[[gc]]))
    if (length(lv) == 2) {
      metrics <- intersect(c("Chao1","Shannon","InvSimpson","GiniSimpson","D50"), colnames(div_tab))
      res <- lapply(metrics, function(mt) {
        x <- div_tab[[mt]][div_tab[[gc]] == lv[1]]; y <- div_tab[[mt]][div_tab[[gc]] == lv[2]]
        wt <- suppressWarnings(wilcox.test(x, y, exact=FALSE))
        data.frame(metric=mt, group1=lv[1], group2=lv[2],
                   median1=round(median(x),3), median2=round(median(y),3),
                   W=unname(wt$statistic), p_value=round(wt$p.value,4),
                   n1=length(x), n2=length(y),
                   note=if (min(length(x),length(y)) < 4) "Exploratory: small n" else "")
      })
      wil <- do.call(rbind, res); wtab(wil, "wilcoxon_tests")
      logmsg("Wilcoxon tests done on", gc)
    } else logmsg("Group column", gc, "does not have exactly 2 levels; skipping Wilcoxon")
  }
}

# ------------------------------ METRICS JSON ---------------------------------
# Small machine-readable summary for the report generator (modality/receptor/
# chain drive the report's framing; paths let it find tables/figures).
metrics <- list(
  receptor=RECEPTOR, chain=CHAIN, modality=MODALITY, species=SPECIES,
  n_samples=length(SAMPLE_ORDER), samples=as.list(SAMPLE_ORDER),
  mean_singleton_frac=round(mean(sfr, na.rm=TRUE), 4),
  group_cols=if (nchar(GROUP_COLS)) as.list(trimws(strsplit(GROUP_COLS, ",")[[1]])) else list(),
  chao1_min=min(div_tab$Chao1, na.rm=TRUE), chao1_max=max(div_tab$Chao1, na.rm=TRUE),
  obs_clonotypes_min=min(summ$n_clonotypes), obs_clonotypes_max=max(summ$n_clonotypes),
  tables_dir=TAB, figures_dir=FIG, seed=SEED,
  immunarch_version=as.character(packageVersion("immunarch"))
)
# jsonlite is normally preinstalled; guard so a missing pkg can't discard the
# analysis outputs (write a minimal JSON by hand as a fallback).
if (requireNamespace("jsonlite", quietly=TRUE)) {
  jsonlite::write_json(metrics, file.path(OUT_DIR, "analysis_metrics.json"),
                       auto_unbox=TRUE, pretty=TRUE)
} else {
  logmsg("WARNING: jsonlite not available; writing minimal metrics JSON by hand")
  esc <- function(x) gsub('"', '\\\\"', as.character(x))
  kv <- c(
    sprintf('"receptor":"%s"', esc(RECEPTOR)), sprintf('"chain":"%s"', esc(CHAIN)),
    sprintf('"modality":"%s"', esc(MODALITY)), sprintf('"species":"%s"', esc(SPECIES)),
    sprintf('"n_samples":%d', length(SAMPLE_ORDER)),
    sprintf('"chao1_min":%s', min(div_tab$Chao1, na.rm=TRUE)),
    sprintf('"chao1_max":%s', max(div_tab$Chao1, na.rm=TRUE)),
    sprintf('"obs_clonotypes_min":%d', min(summ$n_clonotypes)),
    sprintf('"obs_clonotypes_max":%d', max(summ$n_clonotypes)),
    sprintf('"immunarch_version":"%s"', esc(as.character(packageVersion("immunarch"))))
  )
  writeLines(paste0("{\n  ", paste(kv, collapse=",\n  "), "\n}"),
             file.path(OUT_DIR, "analysis_metrics.json"))
}
logmsg("ANALYSIS_DONE — outputs in", OUT_DIR)
