# =============================================================================
# load_data.R  —  unified loader + validator for bulk RNA-seq DE analysis
#
# Loads a raw-integer count matrix and sample metadata from a variety of sources,
# validates sample-ID concordance / orientation / integer-ness, and returns a
# clean (counts, coldata) pair used by every downstream engine.
#
# Supported `counts` inputs:
#   - in-memory matrix / data.frame (genes x samples)
#   - path to CSV / TSV (auto-detected by extension/sniff; first column = gene IDs)
#   - path to .rds holding a matrix, data.frame, or SummarizedExperiment
#   - a SummarizedExperiment / DESeqDataSet object (uses assay 'counts' or first assay)
#   - a tximport list (named list with $counts) -> uses txi$counts
#
# Supported `metadata` inputs:
#   - in-memory data.frame (rownames = sample IDs, or a sample-id column)
#   - path to CSV / TSV
#   - NULL when `counts` is a SummarizedExperiment carrying colData
#
# Returns: list(counts = integer matrix, coldata = data.frame, notes = character)
# =============================================================================

.install_if_needed <- function(pkgs, bioc = FALSE) {
  options(repos = c(CRAN = "https://cloud.r-project.org"))
  for (p in pkgs) {
    if (!requireNamespace(p, quietly = TRUE)) {
      if (bioc) {
        if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
        BiocManager::install(p, update = FALSE, ask = FALSE)
      } else {
        install.packages(p)
      }
    }
  }
}

.read_table_any <- function(path) {
  # Sniff delimiter: .tsv/.txt -> tab, .csv -> comma, else detect from header line.
  ext <- tolower(tools::file_ext(path))
  sep <- if (ext %in% c("tsv", "txt")) "\t" else if (ext == "csv") "," else {
    first <- readLines(path, n = 1L)
    if (grepl("\t", first)) "\t" else ","
  }
  df <- utils::read.delim(path, sep = sep, header = TRUE, row.names = 1,
                          check.names = FALSE, stringsAsFactors = FALSE)
  df
}

.extract_counts <- function(counts) {
  # Returns a numeric matrix (genes x samples) from the many accepted forms.
  if (is.character(counts) && length(counts) == 1L) {
    ext <- tolower(tools::file_ext(counts))
    if (ext == "rds") {
      obj <- readRDS(counts)
      return(.extract_counts(obj))
    }
    m <- as.matrix(.read_table_any(counts))
    storage.mode(m) <- "double"
    return(m)
  }
  # tximport-style list
  if (is.list(counts) && !is.data.frame(counts) && !is.null(counts$counts)) {
    return(as.matrix(counts$counts))
  }
  # SummarizedExperiment / DESeqDataSet
  cls <- class(counts)
  if (any(c("SummarizedExperiment", "RangedSummarizedExperiment", "DESeqDataSet") %in% cls) ||
      methods::is(counts, "SummarizedExperiment")) {
    .install_if_needed("SummarizedExperiment", bioc = TRUE)
    an <- SummarizedExperiment::assayNames(counts)
    a <- if (!is.null(an) && "counts" %in% an) {
      SummarizedExperiment::assay(counts, "counts")
    } else {
      SummarizedExperiment::assay(counts, 1L)
    }
    return(as.matrix(a))
  }
  # matrix / data.frame
  m <- as.matrix(counts)
  storage.mode(m) <- "double"
  m
}

.extract_coldata_from_se <- function(counts) {
  cls <- class(counts)
  if (any(c("SummarizedExperiment", "RangedSummarizedExperiment", "DESeqDataSet") %in% cls) ||
      methods::is(counts, "SummarizedExperiment")) {
    .install_if_needed("SummarizedExperiment", bioc = TRUE)
    return(as.data.frame(SummarizedExperiment::colData(counts)))
  }
  NULL
}

load_de_data <- function(counts, metadata = NULL, condition_col = "condition",
                         sample_id_col = NULL) {
  notes <- character(0)

  # ---- counts ----
  cmat <- .extract_counts(counts)

  # ---- metadata ----
  if (is.null(metadata)) {
    coldata <- .extract_coldata_from_se(counts)
    if (is.null(coldata)) {
      stop("No metadata supplied and `counts` is not a SummarizedExperiment with colData.")
    }
  } else if (is.character(metadata) && length(metadata) == 1L) {
    ext <- tolower(tools::file_ext(metadata))
    coldata <- if (ext == "rds") as.data.frame(readRDS(metadata)) else .read_table_any(metadata)
  } else {
    coldata <- as.data.frame(metadata)
  }

  # If a sample-id column was named, promote it to rownames.
  if (!is.null(sample_id_col) && sample_id_col %in% colnames(coldata)) {
    rownames(coldata) <- as.character(coldata[[sample_id_col]])
    coldata[[sample_id_col]] <- NULL
  }

  # ---- orientation check ----
  # Heuristic: if counts columns don't match metadata rows but rows do, transpose.
  if (!any(colnames(cmat) %in% rownames(coldata)) &&
      any(rownames(cmat) %in% rownames(coldata))) {
    cmat <- t(cmat)
    notes <- c(notes, "Count matrix was transposed so that columns = samples.")
  }

  # ---- sample-ID concordance ----
  common <- intersect(colnames(cmat), rownames(coldata))
  if (length(common) == 0L) {
    stop("Sample IDs do not match between counts (columns) and metadata (rows).\n",
         "  counts cols: ", paste(head(colnames(cmat), 5), collapse = ", "), " ...\n",
         "  metadata rows: ", paste(head(rownames(coldata), 5), collapse = ", "), " ...")
  }
  if (length(common) < ncol(cmat)) {
    notes <- c(notes, sprintf("Dropped %d count columns absent from metadata.",
                              ncol(cmat) - length(common)))
  }
  if (length(common) < nrow(coldata)) {
    notes <- c(notes, sprintf("Dropped %d metadata rows absent from counts.",
                              nrow(coldata) - length(common)))
  }
  cmat <- cmat[, common, drop = FALSE]
  coldata <- coldata[common, , drop = FALSE]

  # ---- grouping column ----
  if (!condition_col %in% colnames(coldata)) {
    stop("Grouping column '", condition_col, "' not found in metadata. ",
         "Available: ", paste(colnames(coldata), collapse = ", "))
  }
  coldata[[condition_col]] <- factor(coldata[[condition_col]])

  # ---- integer-ness check (warn, do not coerce silently) ----
  finite_vals <- cmat[is.finite(cmat)]
  is_integerish <- all(abs(finite_vals - round(finite_vals)) < 1e-8)
  has_negative <- any(finite_vals < 0)
  looks_log <- max(finite_vals, na.rm = TRUE) < 100 && !is_integerish

  if (!is_integerish || has_negative) {
    msg <- paste0(
      "WARNING: count matrix does not look like raw integer counts ",
      "(integerish=", is_integerish, ", negative=", has_negative,
      if (looks_log) ", values<100 & non-integer -> possibly log-scale" else "", ").\n",
      "  DESeq2/edgeR/limma-voom require RAW INTEGER COUNTS. If these are TPM/FPKM/log ",
      "values, count-based DE is invalid; only the limma-trend fallback applies ",
      "(see comparison-and-caveats.md)."
    )
    warning(msg)
    notes <- c(notes, "Input does NOT look like raw integer counts (see warning).")
  }

  list(
    counts = cmat,
    coldata = coldata,
    condition_col = condition_col,
    is_raw_counts = (is_integerish && !has_negative),
    notes = notes
  )
}
