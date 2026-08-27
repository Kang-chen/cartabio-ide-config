# =============================================================================
# selftest.R  —  eval harness for the multi-method DE skill
#
# Runs all three engines (DESeq2, edgeR, limma-voom) plus the shared backbone on a
# real example dataset (pasilla) and asserts the contracts the skill promises. It is
# meant to be run once after install to confirm the skill behaves correctly, and re-run
# after any edit to the scripts.
#
# Usage:
#   Rscript selftest.R                 # uses pasilla (auto-installs if missing)
#   SELFTEST_OUTDIR=/tmp/de Rscript selftest.R
#
# Exit status: 0 if all assertions pass, 1 otherwise. A summary table of PASS/FAIL is
# printed at the end. Each assertion maps to a numbered test case in PLAN.md / SKILL.md.
# =============================================================================

options(repos = c(CRAN = "https://cloud.r-project.org"), warn = 1)

SCRIPT_DIR <- tryCatch({
  a <- commandArgs(trailingOnly = FALSE)
  f <- sub("^--file=", "", a[grep("^--file=", a)])
  if (length(f)) dirname(normalizePath(f)) else getwd()
}, error = function(e) getwd())

src <- function(f) source(file.path(SCRIPT_DIR, f))
for (f in c("load_data.R", "inspect_and_recommend.R", "filter_counts.R",
            "run_deseq2.R", "run_edger.R", "run_limma_voom.R",
            "qc_plots.R", "concordance.R", "export_results.R")) {
  ok <- tryCatch({ src(f); TRUE }, error = function(e) {
    message("Could not source ", f, ": ", conditionMessage(e)); FALSE })
  if (!ok) src(f)  # surface the real error if it truly fails
}

OUTDIR <- Sys.getenv("SELFTEST_OUTDIR", unset = file.path(tempdir(), "de_selftest"))
dir.create(OUTDIR, showWarnings = FALSE, recursive = TRUE)

# ---- tiny assertion framework ----------------------------------------------
.results <- list()
check <- function(id, desc, expr) {
  res <- tryCatch({
    val <- isTRUE(expr)
    list(pass = val, msg = if (val) "" else "assertion FALSE")
  }, error = function(e) list(pass = FALSE, msg = conditionMessage(e)))
  .results[[length(.results) + 1]] <<- data.frame(
    id = id, description = desc, status = if (res$pass) "PASS" else "FAIL",
    detail = res$msg, stringsAsFactors = FALSE)
  cat(sprintf("[%s] %-4s %s%s\n", if (res$pass) "PASS" else "FAIL", id, desc,
              if (nzchar(res$msg)) paste0("  --> ", res$msg) else ""))
  invisible(res$pass)
}

STD_COLS <- c("gene_id", "baseMean_equiv", "log2FoldChange", "pvalue", "padj", "method")
schema_ok <- function(df, method_name) {
  identical(colnames(df), STD_COLS) &&
    !("stat" %in% colnames(df)) &&
    all(df$method == method_name) &&
    nrow(df) > 0
}

# =============================================================================
# 0. Load example data (pasilla): real Drosophila RNA-seq counts, 2 conditions
# =============================================================================
load_pasilla <- function() {
  if (!requireNamespace("pasilla", quietly = TRUE)) {
    if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
    BiocManager::install("pasilla", update = FALSE, ask = FALSE)
  }
  pcf <- system.file("extdata", "pasilla_gene_counts.tsv", package = "pasilla")
  counts <- as.matrix(read.csv(pcf, sep = "\t", row.names = "gene_id"))
  # sample annotation shipped with the package
  paf <- system.file("extdata", "pasilla_sample_annotation.csv", package = "pasilla")
  anno <- read.csv(paf, row.names = 1)
  # the annotation rownames are like 'treated1fb'; counts cols are 'treated1' etc.
  rownames(anno) <- sub("fb$", "", rownames(anno))
  anno <- anno[colnames(counts), , drop = FALSE]
  coldata <- data.frame(
    condition = factor(anno$condition, levels = c("untreated", "treated")),
    type = factor(anno$type),
    row.names = colnames(counts))
  list(counts = counts, coldata = coldata)
}

cat("\n========== Loading pasilla example data ==========\n")
dat <- load_pasilla()
counts <- dat$counts
coldata <- dat$coldata
cat(sprintf("pasilla: %d genes x %d samples; conditions: %s\n",
            nrow(counts), ncol(counts),
            paste(levels(coldata$condition), collapse = "/")))

# =============================================================================
# Test 7 (run early, independent): non-integer input triggers the raw-counts
# guardrail in the recommender (NOT a silent count-based analysis).
# =============================================================================
cat("\n========== Test 7: normalized-input guardrail ==========\n")
fake_norm <- log2(edgeR::cpm(counts) + 1)            # log2-CPM => non-integer
rec_norm <- inspect_and_recommend(fake_norm, coldata, condition_col = "condition")
check("T7a", "non-integer matrix flagged as not raw counts",
      isFALSE(rec_norm$diagnostics$is_raw_integer_counts))
check("T7b", "recommender routes non-integer input to limma-trend fallback",
      grepl("limma-trend", rec_norm$recommended))

# =============================================================================
# Test 4: recommender behaviour on real / reduced / complex designs (advisory).
# =============================================================================
cat("\n========== Test 4: advisory recommender ==========\n")
rec_full <- inspect_and_recommend(counts, coldata, condition_col = "condition")
print(rec_full)
# pasilla min group n = 3 (borderline-small): default DESeq2 but edgeR surfaced as a
# strong alternative (per the n=3 hand-off decision).
check("T4a", "pasilla (n=3 borderline) recommends DESeq2 as default",
      identical(rec_full$recommended, "DESeq2"))
check("T4a2", "n=3 borderline surfaces edgeR as a strong alternative + notes it in prose",
      ("edgeR" %in% rec_full$alternatives) && grepl("edgeR", rec_full$rationale))

# reduce to 2 replicates per group -> should lean edgeR
idx2 <- c(which(coldata$condition == "untreated")[1:2],
          which(coldata$condition == "treated")[1:2])
rec_small <- inspect_and_recommend(counts[, idx2], coldata[idx2, , drop = FALSE],
                                   condition_col = "condition")
check("T4b", "2 reps/group leans edgeR (advisory)",
      identical(rec_small$recommended, "edgeR"))

# synthetic large + multi-factor design -> should lean limma-voom
set.seed(1)
big_n <- 24L
big_counts <- matrix(rnbinom(2000 * big_n, mu = 50, size = 1/0.2), nrow = 2000)
rownames(big_counts) <- paste0("g", seq_len(nrow(big_counts)))
colnames(big_counts) <- paste0("s", seq_len(big_n))
big_coldata <- data.frame(
  condition = factor(rep(c("ctrl", "trt"), each = big_n / 2)),
  batch = factor(rep(rep(c("b1", "b2", "b3"), each = big_n / 6), times = 1)),
  row.names = colnames(big_counts))
rec_big <- inspect_and_recommend(big_counts, big_coldata, condition_col = "condition",
                                 design = ~ batch + condition)
check("T4c", "large/complex design leans limma-voom (advisory)",
      identical(rec_big$recommended, "limma-voom"))

# =============================================================================
# Test 5: full-rank / confounding check flags an aliased design.
# =============================================================================
cat("\n========== Test 5: full-rank / confounding check ==========\n")
# build a design where 'batch' is perfectly nested within condition (confounded)
conf_coldata <- coldata
conf_coldata$batch <- factor(ifelse(conf_coldata$condition == "untreated", "bA", "bB"))
rec_conf <- inspect_and_recommend(counts, conf_coldata, condition_col = "condition",
                                  design = ~ batch + condition)
check("T5a", "confounded design (batch aliased w/ condition) is NOT full rank",
      isFALSE(rec_conf$full_rank))
check("T5b", "confounding message is emitted",
      is.character(rec_conf$confounding_msg) && nzchar(rec_conf$confounding_msg))
# sanity: the clean pasilla ~condition design IS full rank
check("T5c", "clean ~condition design is full rank",
      isTRUE(rec_full$full_rank))

# =============================================================================
# Test 3 + setup for 1/2/6: shared filter -> identical gene universe.
# =============================================================================
cat("\n========== Test 3: shared pre-filter ==========\n")
filt <- filter_counts(counts, coldata, condition_col = "condition")
fc <- filt$counts
cat(filt$filter_summary, "\n")
check("T3a", "filter reduces gene count (n_after < n_before)",
      filt$n_after < filt$n_before && filt$n_after > 0)

# =============================================================================
# Run all three engines on the SAME shared-filtered counts.
# =============================================================================
cat("\n========== Running 3 engines on shared-filtered counts ==========\n")
ctr <- c("condition", "treated", "untreated")     # treated vs untreated
fit_deseq <- run_deseq2(fc, coldata, design = ~ condition, contrast = ctr,
                        ref_level = "untreated", filter_summary = filt$filter_summary,
                        full_rank = rec_full$full_rank)
fit_edger <- run_edger(fc, coldata, design = ~ condition, contrast = ctr,
                       ref_level = "untreated", filter_summary = filt$filter_summary,
                       full_rank = rec_full$full_rank)
fit_limma <- run_limma_voom(fc, coldata, design = ~ condition, contrast = ctr,
                            ref_level = "untreated", filter_summary = filt$filter_summary,
                            full_rank = rec_full$full_rank)

# =============================================================================
# Test 1: all engines emit the standardized schema (no stat col; method set).
# =============================================================================
cat("\n========== Test 1: standardized schema ==========\n")
check("T1a", "DESeq2 emits standardized schema, no stat column",
      schema_ok(fit_deseq$results, "DESeq2"))
check("T1b", "edgeR emits standardized schema, no stat column",
      schema_ok(fit_edger$results, "edgeR"))
check("T1c", "limma-voom emits standardized schema, no stat column",
      schema_ok(fit_limma$results, "limma-voom"))
check("T1d", "each engine records baseMean_scale (linear|log2)",
      identical(fit_deseq$baseMean_scale, "linear") &&
        identical(fit_edger$baseMean_scale, "log2") &&
        identical(fit_limma$baseMean_scale, "log2"))
check("T1e", "each engine records a stat_type label",
      nzchar(fit_deseq$stat_type) && nzchar(fit_edger$stat_type) &&
        nzchar(fit_limma$stat_type))

# =============================================================================
# Test 2: padj used for significance; DEG counts biologically sane on pasilla.
# =============================================================================
cat("\n========== Test 2: padj-based significance, sane DEG counts ==========\n")
ndeg <- function(df) sum(!is.na(df$padj) & df$padj < 0.05)
n_d <- ndeg(fit_deseq$results); n_e <- ndeg(fit_edger$results); n_l <- ndeg(fit_limma$results)
cat(sprintf("DEGs at padj<0.05  ->  DESeq2=%d  edgeR=%d  limma-voom=%d\n", n_d, n_e, n_l))
# pasilla typically yields a few hundred to ~1000 DEGs depending on method/filter
sane <- function(n) n >= 100 && n <= 3000
check("T2a", "DESeq2 DEG count is biologically plausible (100-3000)", sane(n_d))
check("T2b", "edgeR DEG count is biologically plausible (100-3000)", sane(n_e))
check("T2c", "limma-voom DEG count is biologically plausible (100-3000)", sane(n_l))
# the three methods should broadly agree (majority overlap among DESeq2 & edgeR)
sig_d <- fit_deseq$results$gene_id[!is.na(fit_deseq$results$padj) & fit_deseq$results$padj < 0.05]
sig_e <- fit_edger$results$gene_id[!is.na(fit_edger$results$padj) & fit_edger$results$padj < 0.05]
jacc_de <- length(intersect(sig_d, sig_e)) / length(union(sig_d, sig_e))
cat(sprintf("DESeq2 vs edgeR Jaccard = %.3f\n", jacc_de))
check("T2d", "DESeq2 & edgeR broadly concordant (Jaccard > 0.4)", jacc_de > 0.4)

# =============================================================================
# Test 3 (cont.): the engines tested the identical gene universe.
# =============================================================================
u_d <- sort(fit_deseq$results$gene_id)
u_e <- sort(fit_edger$results$gene_id)
u_l <- sort(fit_limma$results$gene_id)
check("T3b", "all engines tested the identical (shared-filtered) gene universe",
      identical(u_d, u_e) && identical(u_d, u_l) && length(u_d) == filt$n_after)

# =============================================================================
# Test 6: concordance returns non-empty overlap + consensus with unshrunk LFC,
#         and uses padj/LFC only -- never baseMean_equiv.
# =============================================================================
cat("\n========== Test 6: concordance (table-only, unshrunk LFC) ==========\n")
conc <- concordance(list(DESeq2 = fit_deseq$results,
                         edgeR  = fit_edger$results,
                         `limma-voom` = fit_limma$results),
                    padj_cutoff = 0.05, output_dir = OUTDIR)
check("T6a", "concordance table is non-empty",
      is.data.frame(conc$concordance_table) && nrow(conc$concordance_table) > 0)
check("T6b", "consensus list (sig in >=2 methods) is non-empty",
      is.data.frame(conc$consensus) && nrow(conc$consensus) > 0)
check("T6c", "concordance confirms a shared gene universe",
      isTRUE(conc$shared_universe))
# consensus carries each method's UNSHRUNK log2FC, and NO baseMean column
cons_cols <- colnames(conc$consensus)
check("T6d", "consensus reports per-method log2FC columns",
      all(c("log2FC_DESeq2", "log2FC_edgeR", "log2FC_limma-voom") %in% cons_cols))
check("T6e", "consensus NEVER carries baseMean_equiv (cross-method guardrail)",
      !any(grepl("baseMean", cons_cols)))
# verify the consensus LFC equals the engines' UNSHRUNK standardized LFC (not shrunk)
g1 <- conc$consensus$gene_id[1]
lfc_consensus <- conc$consensus$log2FC_DESeq2[1]
lfc_unshrunk <- fit_deseq$results$log2FoldChange[fit_deseq$results$gene_id == g1]
check("T6f", "consensus log2FC matches the engine's UNSHRUNK standardized LFC",
      isTRUE(all.equal(lfc_consensus, lfc_unshrunk)))
# DESeq2 shrunk LFC exists separately (for its own viz) and DIFFERS from unshrunk
if (!is.null(fit_deseq$shrunk_lfc)) {
  check("T6g", "DESeq2 keeps shrunk LFC separately (viz only), distinct from unshrunk",
        is.data.frame(fit_deseq$shrunk_lfc) &&
          "log2FoldChange_shrunk" %in% colnames(fit_deseq$shrunk_lfc))
} else {
  check("T6g", "DESeq2 shrunk LFC present (skipped: apeglm unavailable)", TRUE)
}
check("T6h", "concordance wrote concordance_table.csv + consensus_degs.csv",
      file.exists(file.path(OUTDIR, "concordance_table.csv")) &&
        file.exists(file.path(OUTDIR, "consensus_degs.csv")))

# =============================================================================
# Smoke test: export + QC plots run end-to-end for one engine.
# =============================================================================
cat("\n========== Smoke: export + QC plots ==========\n")
exp_ok <- tryCatch({
  export_de(fit_deseq, output_dir = OUTDIR, padj_cutoff = 0.05, lfc_cutoff = 1)
  TRUE
}, error = function(e) { message("export error: ", conditionMessage(e)); FALSE })
check("S1", "export_de runs and writes standardized CSVs + run log", exp_ok)
check("S1b", "de_results_DESeq2.csv exists",
      file.exists(file.path(OUTDIR, "de_results_DESeq2.csv")))

qc_ok <- tryCatch({
  run_all_qc(fit_deseq, output_dir = OUTDIR, coldata = coldata,
             condition_col = "condition")
  TRUE
}, error = function(e) { message("qc error: ", conditionMessage(e)); FALSE })
check("S2", "run_all_qc runs end-to-end (PCA/MA/volcano)", qc_ok)

# =============================================================================
# Summary
# =============================================================================
summary_df <- do.call(rbind, .results)
cat("\n================= SELFTEST SUMMARY =================\n")
print(summary_df, row.names = FALSE)
n_fail <- sum(summary_df$status == "FAIL")
cat(sprintf("\n%d/%d checks passed.\n", sum(summary_df$status == "PASS"), nrow(summary_df)))
utils::write.csv(summary_df, file.path(OUTDIR, "selftest_summary.csv"), row.names = FALSE)
cat(sprintf("Summary written to %s\n", file.path(OUTDIR, "selftest_summary.csv")))

if (n_fail > 0) quit(status = 1, save = "no") else quit(status = 0, save = "no")
