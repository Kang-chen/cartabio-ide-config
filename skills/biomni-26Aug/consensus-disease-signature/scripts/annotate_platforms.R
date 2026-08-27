#!/usr/bin/env Rscript
# ============================================================================
# annotate_platforms.R
# Reusable probe -> gene-symbol annotation for common expression platforms.
#
# Part of the `consensus-disease-signature` skill.
#
# Exposes:
#   annotate_symbols(probes, gpl)   -> named character vector: probe -> SYMBOL
#   collapse_to_genes(exprs, gpl)   -> gene x sample matrix (max-mean collapse)
#   PLATFORM_DB                     -> lookup table of platform -> annotation pkg
#
# WHY probe->symbol + collapse: cross-platform meta-analysis must be done in a
# shared feature space. Gene SYMBOL is the common denominator across Affymetrix,
# Illumina, and RNA-seq. Multiple probes per gene are collapsed to the probe with
# the highest mean expression (most reliable measurement), a standard choice.
# ============================================================================

suppressMessages({
  library(AnnotationDbi)
})

# ---- Platform -> Bioconductor annotation package -------------------------
# Extend this table for new platforms. Key = GPL accession (or a custom tag);
# value = list(pkg = <annotation .db package>, strip_pm = <logical>).
#
# strip_pm handles the Affymetrix "PM" array quirk (e.g. GPL13158, HT HG-U133+ PM):
# probe IDs carry an "_PM" infix (1007_PM_s_at) but the standard hgu133plus2.db
# keys are "1007_s_at". Stripping "_PM" recovers the mapping. This single fix took
# the UC GSE92415 annotation from 3 -> 21,358 mapped symbols.
PLATFORM_DB <- list(
  "GPL6244"  = list(pkg = "hugene10sttranscriptcluster.db", strip_pm = FALSE), # Affy Human Gene 1.0 ST
  "GPL570"   = list(pkg = "hgu133plus2.db",                 strip_pm = FALSE), # Affy HG-U133 Plus 2.0
  "GPL571"   = list(pkg = "hgu133a2.db",                    strip_pm = FALSE), # Affy HG-U133A 2.0
  "GPL96"    = list(pkg = "hgu133a.db",                     strip_pm = FALSE), # Affy HG-U133A
  "GPL13158" = list(pkg = "hgu133plus2.db",                 strip_pm = TRUE),  # Affy HT HG-U133+ PM
  "GPL16311" = list(pkg = "hgu133plus2.db",                 strip_pm = TRUE),  # HT HG-U133+ PM variant
  "GPL10558" = list(pkg = "illuminaHumanv4.db",             strip_pm = FALSE), # Illumina HumanHT-12 v4
  "GPL6947"  = list(pkg = "illuminaHumanv3.db",             strip_pm = FALSE)  # Illumina HumanHT-12 v3
)

# ---- annotate_symbols ----------------------------------------------------
# probes : character vector of probe/feature IDs (rownames of the matrix)
# gpl    : platform tag; must be a key in PLATFORM_DB, OR the literal "SYMBOL"
#          when features are already gene symbols (user matrix / RNA-seq), OR
#          "ENSEMBL"/"ENTREZID" to map from those IDs via org.Hs.eg.db.
# Returns a named character vector mapping each input probe to a SYMBOL (NA if none).
annotate_symbols <- function(probes, gpl) {
  probes <- as.character(probes)

  # Already gene symbols -> identity map
  if (gpl == "SYMBOL") {
    return(setNames(probes, probes))
  }

  # Ensembl / Entrez -> symbol via org.Hs.eg.db
  if (gpl %in% c("ENSEMBL", "ENTREZID")) {
    suppressMessages(library(org.Hs.eg.db))
    key <- sub("\\.[0-9]+$", "", probes)  # drop Ensembl version suffix if present
    m <- AnnotationDbi::select(org.Hs.eg.db, keys = unique(key),
                               columns = "SYMBOL", keytype = gpl)
    m <- m[!is.na(m$SYMBOL) & !duplicated(m[[gpl]]), ]
    lut <- setNames(m$SYMBOL, m[[gpl]])
    return(setNames(lut[key], probes))
  }

  # Platform annotation package
  if (!gpl %in% names(PLATFORM_DB)) {
    stop(sprintf(paste0("Platform '%s' not in PLATFORM_DB. Add it (map to a Bioconductor ",
                        ".db package) or pre-map features to gene symbols and pass gpl='SYMBOL'."), gpl))
  }
  spec <- PLATFORM_DB[[gpl]]
  if (!requireNamespace(spec$pkg, quietly = TRUE)) {
    stop(sprintf("Annotation package '%s' not installed. Install via BiocManager::install('%s').",
                 spec$pkg, spec$pkg))
  }
  suppressMessages(library(spec$pkg, character.only = TRUE))
  db <- get(spec$pkg)

  std <- if (isTRUE(spec$strip_pm)) sub("_PM", "", probes) else probes
  m <- AnnotationDbi::select(db, keys = unique(std),
                             columns = "SYMBOL", keytype = "PROBEID")
  m <- m[!is.na(m$SYMBOL) & !duplicated(m$PROBEID), ]
  lut <- setNames(m$SYMBOL, m$PROBEID)
  setNames(lut[std], probes)
}

# ---- collapse_to_genes ---------------------------------------------------
# ex  : numeric matrix, features (rows) x samples (cols), rownames = probe IDs
# gpl : platform tag (see annotate_symbols)
# Returns a gene x sample matrix; for each gene, the probe with the highest
# mean expression is kept (max-mean collapse).
collapse_to_genes <- function(ex, gpl) {
  stopifnot(is.matrix(ex) || is.data.frame(ex))
  ex <- as.matrix(ex)
  sym <- annotate_symbols(rownames(ex), gpl)
  sym <- sym[!is.na(sym)]
  ex <- ex[names(sym), , drop = FALSE]
  ord <- order(rowMeans(ex), decreasing = TRUE)
  ex <- ex[ord, , drop = FALSE]
  g <- sym[ord]
  keep <- !duplicated(g)
  ex <- ex[keep, , drop = FALSE]
  rownames(ex) <- g[keep]
  ex
}
