#!/usr/bin/env Rscript
# gene_de.R -- Stage 5 (per-gene differential expression) of
# signature-response-enrichment.
#
# Per-gene NR-vs-R differential expression at a given timepoint:
#   RNA-seq  -> limma-voom:  voom(dge) -> lmFit(E, ~grp) -> eBayes -> topTable
#   microarray -> limma:     lmFit(E, ~grp) -> eBayes -> topTable
# grp is factor(response_group, levels=c("R","NR")); the grpNR coefficient is NR vs R.
# BH-FDR on the per-gene family. Emits full topTable + a compact summary row
# (n_genes, min_FDR, n_FDR<0.05, n_nominal_p<0.05).
#
# AUTO-SKIP: <2 samples per group -> skip gracefully.
#
# INPUT:
#   --expr        expression matrix CSV (genes x samples).
#                 RNA-seq: raw/expected counts (voom applied). Microarray: log-intensities.
#   --meta        sample meta: sample_id, timepoint, patient_id
#   --response    response CSV: patient_id, response_group (R/NR)
#   --timepoint   timepoint to test
#   --platform    "rnaseq" (voom) or "microarray" (limma)
#   --out         full topTable CSV
#   --summary-out compact summary CSV (appended per call by the agent)
#
# USAGE:
#   Rscript gene_de.R --expr counts.csv --meta meta.csv --response resp.csv \
#       --timepoint 12 --platform rnaseq --out de_wk12.csv --summary-out de_summary.csv

suppressWarnings(suppressMessages({
  library(optparse); library(limma)
}))

opt <- parse_args(OptionParser(option_list = list(
  make_option("--expr", type = "character"),
  make_option("--meta", type = "character"),
  make_option("--response", type = "character"),
  make_option("--timepoint", type = "character"),
  make_option("--platform", type = "character", default = "rnaseq"),
  make_option("--out", type = "character"),
  make_option("--summary-out", type = "character", default = NULL, dest = "summary_out")
)))

expr <- as.matrix(read.csv(opt$expr, row.names = 1, check.names = FALSE))
mode(expr) <- "numeric"
meta <- read.csv(opt$meta, stringsAsFactors = FALSE)
resp <- read.csv(opt$response, stringsAsFactors = FALSE)

samp <- meta$sample_id[as.character(meta$timepoint) == as.character(opt$timepoint)]
samp <- intersect(samp, colnames(expr))
m <- meta[meta$sample_id %in% samp, ]
m <- merge(m, resp[, c("patient_id", "response_group")], by = "patient_id")
m <- m[match(samp, m$sample_id), ]
grp <- factor(m$response_group, levels = c("R", "NR"))
E <- expr[, samp, drop = FALSE]

write_empty <- function(reason) {
  message(sprintf("[de] SKIP: %s", reason))
  write.csv(data.frame(gene = character(0), logFC = numeric(0),
                       P.Value = numeric(0), adj.P.Val = numeric(0)),
            opt$out, row.names = FALSE)
}

if (sum(grp == "NR") < 2 || sum(grp == "R") < 2) {
  write_empty(sprintf("<2 per group (NR=%d,R=%d)", sum(grp == "NR"), sum(grp == "R")))
  quit(status = 0)
}

design <- model.matrix(~grp)  # grpNR = NR vs R
if (tolower(opt$platform) == "rnaseq") {
  suppressWarnings(suppressMessages(library(edgeR)))
  dge <- DGEList(counts = E)
  keep <- filterByExpr(dge, design)
  dge <- dge[keep, , keep.lib.sizes = FALSE]
  dge <- calcNormFactors(dge)
  v <- voom(dge, design)
  fit <- eBayes(lmFit(v, design))
} else {
  fit <- eBayes(lmFit(E, design))
}
tt <- topTable(fit, coef = "grpNR", number = Inf, sort.by = "P")
tt$gene <- rownames(tt)
tt <- tt[, c("gene", setdiff(colnames(tt), "gene"))]
write.csv(tt, opt$out, row.names = FALSE)

summ <- data.frame(
  timepoint = opt$timepoint, platform = opt$platform,
  n_genes = nrow(tt),
  min_FDR = min(tt$adj.P.Val, na.rm = TRUE),
  n_FDR_sig = sum(tt$adj.P.Val < 0.05, na.rm = TRUE),
  n_nominal = sum(tt$P.Value < 0.05, na.rm = TRUE),
  stringsAsFactors = FALSE)
if (!is.null(opt$summary_out)) {
  append <- file.exists(opt$summary_out)
  write.table(summ, opt$summary_out, sep = ",", row.names = FALSE,
              col.names = !append, append = append)
}
message(sprintf("[de] %s tp=%s: %d genes | min FDR=%.3g | FDR<0.05=%d | nominal<0.05=%d -> %s",
                opt$platform, opt$timepoint, summ$n_genes, summ$min_FDR,
                summ$n_FDR_sig, summ$n_nominal, opt$out))
