#!/usr/bin/env Rscript
# deseq_smoketest.R
#
# Assert that a count matrix is DE-ready: it must load into a
# DESeqDataSetFromMatrix without error, be integer-valued, and have gene rows.
# This does NOT run a differential test (a single sample cannot) — it only
# proves the matrix is a valid DESeq2 input, catching the common failure modes:
# non-integer values (TPM/FPKM leaked in), NA/negative counts, empty matrix,
# or duplicated gene IDs.
#
# Usage:
#   Rscript deseq_smoketest.R --counts counts_matrix.tsv [--coldata coldata.csv]
#
# coldata.csv (optional): first column = sample names matching matrix columns,
# plus any covariates. Without it, a placeholder design (~ 1) is used.

suppressWarnings(suppressMessages(library(DESeq2)))

args <- commandArgs(trailingOnly = TRUE)
getopt <- function(flag, default = NULL) {
  hit <- which(args == flag); if (length(hit) == 0) return(default)
  args[hit[1] + 1]
}
counts_path <- getopt("--counts")
coldata_path <- getopt("--coldata")
if (is.null(counts_path)) stop("required: --counts counts_matrix.tsv")

counts <- as.matrix(read.delim(counts_path, row.names = 1, check.names = FALSE))
stopifnot("matrix is empty" = nrow(counts) > 0 && ncol(counts) > 0)
if (any(is.na(counts))) stop("FAIL: matrix contains NA values")
if (any(counts < 0)) stop("FAIL: matrix contains negative values")
if (any(counts != round(counts))) stop("FAIL: matrix is not integer-valued (TPM/FPKM?). DESeq2 needs raw counts.")
if (any(duplicated(rownames(counts)))) stop("FAIL: duplicated gene IDs in matrix")
storage.mode(counts) <- "integer"

if (!is.null(coldata_path)) {
  colData <- read.csv(coldata_path, row.names = 1, check.names = FALSE)
  colData <- colData[colnames(counts), , drop = FALSE]
  design <- if (ncol(colData) >= 1 && length(unique(colData[[1]])) > 1)
    as.formula(paste("~", colnames(colData)[1])) else ~ 1
} else {
  colData <- data.frame(row.names = colnames(counts),
                        sample = colnames(counts))
  design <- ~ 1
}

dds <- DESeqDataSetFromMatrix(countData = counts, colData = colData, design = design)
dds <- estimateSizeFactors(dds)

cat("DESeq2 smoke-test PASSED\n")
cat(sprintf("  genes: %d | samples: %d | design: %s\n",
            nrow(dds), ncol(dds), paste(deparse(design), collapse = "")))
cat(sprintf("  size factors: %s\n",
            paste(sprintf("%s=%.3f", colnames(dds), sizeFactors(dds)), collapse = ", ")))
if (ncol(dds) < 4) {
  cat("  NOTE: <4 samples — sufficient for a validity check, NOT for a real DE test.\n")
  cat("        Add >=2 samples per condition and a real design to run DESeq().\n")
}
