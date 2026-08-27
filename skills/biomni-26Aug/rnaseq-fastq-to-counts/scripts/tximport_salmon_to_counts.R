#!/usr/bin/env Rscript
# tximport_salmon_to_counts.R
#
# Aggregate salmon transcript-level quantifications into a DE-ready gene-level
# count matrix using tximport (preinstalled in the Biomni R environment).
#
# Salmon (`salmon quant -l A --validateMappings`) auto-detects library type and
# writes per-sample `quant.sf` (transcript-level TPM + estimated counts) plus a
# `lib_format_counts.json` recording the inferred library type (ISR = reverse-
# stranded/dUTP, ISF = forward, IU/U = unstranded). tximport with
# countsFromAbundance="lengthScaledTPM" (or the default "no") collapses
# transcripts to genes; DESeq2 recommends importing via tximport rather than
# rounding TPM by hand, because it also produces the average-transcript-length
# offset needed for correct dispersion estimation.
#
# tx2gene maps transcript_id -> gene_id. Build it from the SAME annotation used
# to build the salmon index (Ensembl/GENCODE GTF), so IDs match exactly.
#
# Usage:
#   Rscript tximport_salmon_to_counts.R \
#     --quant-dirs S1=/path/S1/quant.sf,S2=/path/S2/quant.sf \
#     --tx2gene tx2gene.tsv \
#     --outdir /mnt/results/<run> \
#     [--counts-from-abundance no|scaledTPM|lengthScaledTPM] \
#     [--ignore-tx-version]
#
# tx2gene.tsv: 2 columns, no header preferred (transcript_id <tab> gene_id).
#   Build from a GTF, e.g.:
#     library(GenomicFeatures) # or parse the GTF: transcript_id -> gene_id
#   or simply:  awk -F'\t' '$3=="transcript"' ann.gtf | \
#     sed -E 's/.*transcript_id "([^"]+)".*gene_id "([^"]+)".*/\1\t\2/' > tx2gene.tsv
#   (adjust field order to your GTF; Ensembl lists gene_id before transcript_id).
#
# Outputs (in --outdir):
#   counts_matrix.tsv   integer gene x sample matrix (rounded from tximport counts); DESeq2 input
#   tpm_matrix.tsv      gene-level TPM (abundance) matrix (for QC / visualization only, NOT for DE)
#   salmon_library_types.json  inferred library type per sample (from lib_format_counts.json if present)

suppressWarnings(suppressMessages({
  library(tximport)
}))

# ---- tiny arg parser ----
args <- commandArgs(trailingOnly = TRUE)
getopt <- function(flag, default = NULL, is_flag = FALSE) {
  hit <- which(args == flag)
  if (length(hit) == 0) return(default)
  if (is_flag) return(TRUE)
  if (hit[1] == length(args)) stop(paste("missing value for", flag))
  args[hit[1] + 1]
}
quant_dirs <- getopt("--quant-dirs")
tx2gene_path <- getopt("--tx2gene")
outdir <- getopt("--outdir")
cfa <- getopt("--counts-from-abundance", "lengthScaledTPM")
ignore_ver <- getopt("--ignore-tx-version", is_flag = TRUE)

if (is.null(quant_dirs) || is.null(tx2gene_path) || is.null(outdir)) {
  stop("required: --quant-dirs name=path,... --tx2gene tx2gene.tsv --outdir DIR")
}
dir.create(outdir, showWarnings = FALSE, recursive = TRUE)

# ---- parse name=path pairs ----
pairs <- strsplit(quant_dirs, ",")[[1]]
files <- character(0); snames <- character(0)
for (p in pairs) {
  kv <- strsplit(p, "=")[[1]]
  if (length(kv) != 2) stop(paste("bad --quant-dirs entry:", p))
  snames <- c(snames, trimws(kv[1]))
  fp <- trimws(kv[2])
  # accept either the quant.sf file or its parent directory
  if (dir.exists(fp)) fp <- file.path(fp, "quant.sf")
  if (!file.exists(fp)) stop(paste("quant.sf not found:", fp))
  files <- c(files, fp)
}
names(files) <- snames
cat("Samples:", paste(snames, collapse = ", "), "\n")

# ---- tx2gene ----
t2g <- read.delim(tx2gene_path, header = FALSE, stringsAsFactors = FALSE)
if (ncol(t2g) < 2) stop("tx2gene must have >=2 columns: transcript_id, gene_id")
t2g <- t2g[, 1:2]; colnames(t2g) <- c("TXNAME", "GENEID")
cat("tx2gene:", nrow(t2g), "transcript->gene mappings\n")

# ---- import ----
txi <- tximport(files, type = "salmon", tx2gene = t2g,
                countsFromAbundance = cfa,
                ignoreTxVersion = isTRUE(ignore_ver))

counts <- txi$counts
tpm <- txi$abundance
# DESeq2 needs integer counts; tximport lengthScaledTPM/scaledTPM counts are
# suitable to round. (For DESeqDataSetFromTximport you would pass txi directly;
# here we also emit a plain rounded matrix for portability.)
counts_int <- round(counts)
storage.mode(counts_int) <- "integer"

write.table(data.frame(gene_id = rownames(counts_int), counts_int, check.names = FALSE),
            file = file.path(outdir, "counts_matrix.tsv"),
            sep = "\t", quote = FALSE, row.names = FALSE)
write.table(data.frame(gene_id = rownames(tpm), tpm, check.names = FALSE),
            file = file.path(outdir, "tpm_matrix.tsv"),
            sep = "\t", quote = FALSE, row.names = FALSE)

# ---- library types from salmon's lib_format_counts.json ----
libs <- list()
for (nm in snames) {
  qdir <- dirname(files[[nm]])
  lf <- file.path(qdir, "lib_format_counts.json")
  if (file.exists(lf)) {
    txt <- paste(readLines(lf, warn = FALSE), collapse = " ")
    m <- regmatches(txt, regexpr('"expected_format"\\s*:\\s*"[^"]+"', txt))
    fmt <- if (length(m)) sub('.*"([^"]+)"$', "\\1", m) else NA
    libs[[nm]] <- fmt
  } else {
    libs[[nm]] <- NA
  }
}
# minimal JSON writer (avoid extra deps)
json_lines <- sapply(names(libs), function(nm) {
  v <- libs[[nm]]; v <- if (is.na(v)) "null" else paste0('"', v, '"')
  paste0('  "', nm, '": ', v)
})
writeLines(c("{", paste(json_lines, collapse = ",\n"), "}"),
           file.path(outdir, "salmon_library_types.json"))

n_det <- sum(rowSums(counts_int) > 0)
cat(sprintf("\nWrote %s\n  genes: %d | detected (>0 any sample): %d | samples: %d\n",
            file.path(outdir, "counts_matrix.tsv"), nrow(counts_int), n_det, length(snames)))
cat("Inferred salmon library types:",
    paste(names(libs), unlist(lapply(libs, function(x) ifelse(is.na(x), "NA", x))),
          sep = "=", collapse = ", "), "\n")
