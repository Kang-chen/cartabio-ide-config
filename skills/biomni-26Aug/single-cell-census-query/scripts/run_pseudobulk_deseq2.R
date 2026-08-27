#!/usr/bin/env Rscript
# Step 5 — Pseudobulk DESeq2: CASE vs CONTROL, GLOBAL (per-donor) + per-cell-type.
# Donor is the replicate unit. Wald test, BH-FDR, padj < 0.05. Gene panel foregrounded.
# Generalized from a validated run. Edit the PARAMETERS block only.
suppressMessages({ library(DESeq2); library(data.table) })

# ============================== PARAMETERS ==============================
CASE_LABEL    <- "DISEASE_OF_INTEREST"   # `disease` value for case, e.g. "chronic rhinitis"
CONTROL_LABEL <- "normal"                 # `disease` value for control
GENE_PANEL    <- c("GENE1", "GENE2")     # e.g. c("IL13","TSLP")
PANEL_TAG     <- "panel"                  # output filename tag
COVARIATES    <- c()                      # optional, e.g. c("sex") — must be columns in coldata
MIN_DONORS_PER_GROUP <- 3                 # per-cell-type feasibility
MIN_TOTAL_COUNT <- 10                     # gene filter: rowSums(counts) >= this
MIN_SAMPLES_DETECTED <- 3                 # gene filter: detected in >= this many samples
IN  <- "/mnt/shared-workspace/shared"     # where build_pseudobulk.py wrote its CSVs
RES <- "/mnt/results"
# =======================================================================
dir.create(file.path(RES, "data"), showWarnings = FALSE, recursive = TRUE)

# short internal factor labels (safe for design formulas)
CASE_F <- "case"; CTRL_F <- "control"

message("Loading pseudobulk matrices...")
counts <- as.data.frame(fread(file.path(IN, "pseudobulk_counts.csv")))
rownames(counts) <- counts[[1]]; counts[[1]] <- NULL
counts <- as.matrix(counts); mode(counts) <- "integer"
coldata <- as.data.frame(fread(file.path(IN, "pseudobulk_coldata.csv")))
rownames(coldata) <- coldata$sample
var <- as.data.frame(fread(file.path(IN, "pseudobulk_var.csv")))
id2name <- setNames(var$feature_name, var$feature_id)

stopifnot(all(colnames(counts) == coldata$sample))
coldata$disease <- factor(
  ifelse(coldata$disease == CASE_LABEL, CASE_F, CTRL_F), levels = c(CTRL_F, CASE_F))
for (cv in COVARIATES) if (cv %in% colnames(coldata)) coldata[[cv]] <- factor(coldata[[cv]])

target_ids <- names(id2name)[id2name %in% GENE_PANEL]

# design formula: ~ [covariates +] disease  (disease last so it's the tested term)
design_formula <- if (length(COVARIATES) > 0)
  as.formula(paste("~", paste(c(COVARIATES, "disease"), collapse = " + "))) else ~ disease

run_deseq <- function(cts, cd, label) {
  keep <- rowSums(cts) >= MIN_TOTAL_COUNT & rowSums(cts > 0) >= MIN_SAMPLES_DETECTED
  cts <- cts[keep, , drop = FALSE]
  if (nrow(cts) < 50 || ncol(cts) < 4) return(NULL)
  if (length(unique(cd$disease)) < 2) return(NULL)
  if (min(table(cd$disease)) < MIN_DONORS_PER_GROUP) return(NULL)
  # drop covariates that are constant within this subset (avoid full-rank errors)
  fml <- design_formula
  for (cv in COVARIATES) if (cv %in% colnames(cd) && length(unique(cd[[cv]])) < 2) {
    fml <- as.formula(gsub(paste0(cv, " \\+ "), "", deparse(fml)))
  }
  dds <- tryCatch({
    d <- DESeqDataSetFromMatrix(cts, cd, design = fml); DESeq(d, quiet = TRUE)
  }, error = function(e) { message("  DESeq error [", label, "]: ", conditionMessage(e)); NULL })
  if (is.null(dds)) return(NULL)
  res <- as.data.frame(results(dds, contrast = c("disease", CASE_F, CTRL_F)))
  res$feature_id <- rownames(res); res$gene <- id2name[res$feature_id]
  res$cell_type <- label
  res$n_case <- sum(cd$disease == CASE_F); res$n_ctrl <- sum(cd$disease == CTRL_F)
  res[order(res$padj), ]
}

all_res <- list()

## ---- GLOBAL: collapse all cell types within a donor ----
message("Running GLOBAL pseudobulk (per donor)...")
keep_cols <- c("donor_id", "disease", COVARIATES)
donor_meta <- unique(coldata[, keep_cols[keep_cols %in% colnames(coldata)], drop = FALSE])
glob_cts <- sapply(donor_meta$donor_id, function(d) {
  cols <- coldata$sample[coldata$donor_id == d]
  rowSums(counts[, cols, drop = FALSE])
})
colnames(glob_cts) <- donor_meta$donor_id
glob_cd <- donor_meta; rownames(glob_cd) <- glob_cd$donor_id; glob_cd$sample <- glob_cd$donor_id
gres <- run_deseq(glob_cts, glob_cd, "GLOBAL")
if (!is.null(gres)) all_res[["GLOBAL"]] <- gres

## ---- PER CELL TYPE (feasible = >= MIN_DONORS_PER_GROUP donors in BOTH groups) ----
cts_by_type <- table(coldata$cell_type, coldata$disease)
feasible <- rownames(cts_by_type)[
  cts_by_type[, CTRL_F] >= MIN_DONORS_PER_GROUP & cts_by_type[, CASE_F] >= MIN_DONORS_PER_GROUP]
message("Feasible cell types (>= ", MIN_DONORS_PER_GROUP, " donors/group): ", length(feasible))
for (ct in feasible) {
  samp <- coldata$sample[coldata$cell_type == ct]
  r <- run_deseq(counts[, samp, drop = FALSE], coldata[samp, , drop = FALSE], ct)
  if (!is.null(r)) {
    all_res[[ct]] <- r
    message("  ", ct, ": ", sum(r$padj < 0.05, na.rm = TRUE), " DEGs padj<0.05")
  }
}

res_all <- do.call(rbind, all_res)
fwrite(res_all, file.path(RES, "data", "pseudobulk_DE_all_results.csv"))
sig <- res_all[!is.na(res_all$padj) & res_all$padj < 0.05, ]
fwrite(sig, file.path(RES, "data", "pseudobulk_DE_significant.csv"))

tgt <- res_all[res_all$gene %in% GENE_PANEL,
               c("cell_type","gene","baseMean","log2FoldChange","lfcSE","pvalue","padj","n_case","n_ctrl")]
tgt <- tgt[order(tgt$gene, tgt$padj), ]
fwrite(tgt, file.path(RES, "data", paste0(PANEL_TAG, "_DE_by_celltype.csv")))

message("\n=== DEG counts (padj<0.05) per comparison ===")
summ <- aggregate(padj ~ cell_type, data = res_all, FUN = function(x) sum(x < 0.05, na.rm = TRUE))
colnames(summ)[2] <- "n_DEG"; print(summ[order(-summ$n_DEG), ])
message("\n=== ", PANEL_TAG, " panel results ===")
print(tgt)
message("DONE")
