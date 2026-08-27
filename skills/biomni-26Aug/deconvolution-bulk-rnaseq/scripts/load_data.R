# =============================================================================
# Bulk RNA-seq Cell-Type Deconvolution -- Data Loading
# =============================================================================
# Loads (1) a bulk expression matrix and (2) an annotated single-cell reference
# for deconvolution. Accepts user files (h5ad / Seurat .rds / SCE .rds for the
# reference; CSV/TSV/RDS for the bulk) OR a self-contained synthetic example with
# known ground-truth cell-type proportions.
#
# The reference is always standardised to a SingleCellExperiment with a 'counts'
# assay and colData columns 'cell_type' + 'donor_id' so the downstream scripts
# (run_deconvolution.R) have a single, predictable shape.
#
# REBUILD NOTES (Biomni):
#   - Log messages converted to ASCII tokens ([OK]/[WARN]); no Unicode glyphs.
# =============================================================================

suppressPackageStartupMessages({
    library(SingleCellExperiment)
})

# --- Reference loader --------------------------------------------------------

#' Load and standardise an annotated single-cell reference
#'
#' @param path Path to .h5ad (CELLxGENE/AnnData), Seurat .rds, or SCE .rds
#' @param cell_type_col Name of the cell-type annotation column in the source
#' @param batch_col Name of the subject/donor column (used by MuSiC/Bisque)
#' @return SingleCellExperiment with assay 'counts' + colData cell_type, donor_id
load_reference <- function(path, cell_type_col = "cell_type", batch_col = "donor_id") {

    cat("\n=== Loading single-cell reference ===\n\n")
    if (!file.exists(path)) stop("Reference file not found: ", path)
    ext <- tolower(tools::file_ext(path))
    cat("   File:", path, "(", ext, ")\n")

    if (ext == "h5ad") {
        if (!requireNamespace("zellkonverter", quietly = TRUE))
            stop("Reading .h5ad requires 'zellkonverter': BiocManager::install('zellkonverter')")
        sce <- zellkonverter::readH5AD(path)

    } else if (ext == "rds") {
        obj <- readRDS(path)
        if (inherits(obj, "Seurat")) {
            if (!requireNamespace("Seurat", quietly = TRUE))
                stop("Converting a Seurat reference requires the 'Seurat' package.")
            cat("   Detected Seurat object -- converting to SingleCellExperiment...\n")
            sce <- Seurat::as.SingleCellExperiment(obj)
        } else if (inherits(obj, "SingleCellExperiment")) {
            sce <- obj
        } else {
            stop("Unsupported .rds contents: expected Seurat or SingleCellExperiment, got ",
                 class(obj)[1])
        }
    } else {
        stop("Unsupported reference format '", ext,
             "'. Use .h5ad, or .rds (Seurat / SingleCellExperiment).")
    }

    sce <- .standardise_reference(sce, cell_type_col = cell_type_col, batch_col = batch_col)
    .print_reference_summary(sce)
    sce
}


#' Standardise an SCE: ensure 'counts' assay + colData cell_type/donor_id
.standardise_reference <- function(sce, cell_type_col = "cell_type", batch_col = "donor_id") {

    # Ensure a 'counts' assay (BayesPrism/MuSiC/DWLS want non-log counts).
    an <- SummarizedExperiment::assayNames(sce)
    if (!"counts" %in% an) {
        # Prefer an assay literally called counts/X/raw; else take the first.
        pick <- intersect(c("X", "raw", "RNA", "originalexp"), an)
        src <- if (length(pick)) pick[1] else an[1]
        cat("   No 'counts' assay -- using assay '", src, "' as counts.\n", sep = "")
        SummarizedExperiment::assay(sce, "counts") <- SummarizedExperiment::assay(sce, src)
    }

    cd <- SummarizedExperiment::colData(sce)

    # Cell-type column -> 'cell_type'
    if (!"cell_type" %in% colnames(cd)) {
        if (cell_type_col %in% colnames(cd)) {
            cd$cell_type <- cd[[cell_type_col]]
        } else {
            cand <- grep("cell.?type|celltype|annotation|label|ident",
                         colnames(cd), ignore.case = TRUE, value = TRUE)
            stop("Reference colData has no 'cell_type' (or '", cell_type_col, "').",
                 if (length(cand)) paste0(" Candidate columns: ", paste(cand, collapse = ", ")) else "")
        }
    }
    cd$cell_type <- as.character(cd$cell_type)

    # Subject/donor column -> 'donor_id' (MuSiC/Bisque weight by subject).
    if (!"donor_id" %in% colnames(cd)) {
        if (batch_col %in% colnames(cd)) {
            cd$donor_id <- as.character(cd[[batch_col]])
        } else if ("dataset_id" %in% colnames(cd)) {
            cd$donor_id <- as.character(cd$dataset_id)
        } else {
            cat("   [WARN] No donor/subject column -- using a single pseudo-donor.\n")
            cd$donor_id <- "donor1"
        }
    }
    cd$donor_id <- as.character(cd$donor_id)

    SummarizedExperiment::colData(sce) <- cd

    # Drop cell types with too few cells (unstable signatures).
    tab <- table(cd$cell_type)
    tiny <- names(tab)[tab < 3]
    if (length(tiny)) {
        cat("   [WARN] Dropping", length(tiny), "cell type(s) with <3 cells:",
            paste(tiny, collapse = ", "), "\n")
        keep <- !cd$cell_type %in% tiny
        sce <- sce[, keep]
    }
    sce
}


#' Print reference summary
.print_reference_summary <- function(sce) {
    ct <- SummarizedExperiment::colData(sce)$cell_type
    cat("   Cells:", ncol(sce), " | Genes:", nrow(sce),
        " | Donors:", length(unique(SummarizedExperiment::colData(sce)$donor_id)), "\n")
    cat("   Cell types (", length(unique(ct)), "):\n", sep = "")
    tab <- sort(table(ct), decreasing = TRUE)
    for (nm in names(tab)) cat(sprintf("     %-25s %d cells\n", nm, tab[nm]))
    cat("\n[OK] Reference loaded successfully!", ncol(sce), "cells,",
        length(unique(ct)), "cell types\n\n")
    invisible(sce)
}


# --- Bulk loader -------------------------------------------------------------

#' Load a bulk expression matrix (genes x samples)
#'
#' @param path .csv / .tsv / .txt (genes in rows, first column = gene ID) or .rds
#' @return numeric matrix, genes x samples
load_bulk <- function(path) {
    cat("=== Loading bulk expression matrix ===\n\n")
    if (!file.exists(path)) stop("Bulk file not found: ", path)
    ext <- tolower(tools::file_ext(path))

    if (ext == "rds") {
        m <- readRDS(path)
        m <- as.matrix(m)
    } else {
        sep <- if (ext %in% c("tsv", "txt")) "\t" else ","
        df <- utils::read.table(path, header = TRUE, sep = sep, row.names = 1,
                                check.names = FALSE, stringsAsFactors = FALSE)
        m <- as.matrix(df)
    }
    storage.mode(m) <- "double"
    if (is.null(rownames(m))) stop("Bulk matrix has no gene row names.")
    if (is.null(colnames(m))) colnames(m) <- paste0("sample", seq_len(ncol(m)))

    cat("   Genes:", nrow(m), " | Samples:", ncol(m), "\n")
    cat("   Value range: [", signif(min(m, na.rm = TRUE), 3), ",",
        signif(max(m, na.rm = TRUE), 3), "]\n")
    cat("\n[OK] Bulk loaded successfully!", nrow(m), "genes x", ncol(m), "samples\n\n")
    m
}


#' Quick compatibility check between bulk and reference (shared genes)
validate_inputs <- function(bulk, reference) {
    ref_genes <- rownames(reference)
    common <- intersect(rownames(bulk), ref_genes)
    cat("   Shared genes (bulk and reference):", length(common), "\n")
    if (length(common) < 50)
        warning("Only ", length(common), " shared genes -- check gene-ID type ",
                "(symbols vs Ensembl) matches between bulk and reference.")
    invisible(length(common))
}


# --- Example data (deterministic synthetic immune dataset) -------------------

#' Simulate a small annotated immune scRNA reference + matched bulk with KNOWN
#' ground-truth cell-type proportions. Pure base R (no heavy deps) so it is fast
#' and reproducible. The bulk is built as a proportion-weighted mixture of the
#' reference cell-type profiles, so deconvolution should recover the truth well.
#'
#' Two groups ("responder" vs "nonresponder") x two timepoints: composition is
#' similar at baseline and DIVERGES by week12 (nonresponders retain elevated
#' Monocytes and depressed CD8 T cells) -- a realistic "which populations
#' normalise with response" contrast.
#'
#' @return list(counts, cell_meta, bulk, metadata, ground_truth, signature)
.simulate_immune_data <- function(seed = 42,
                                  cells_per_type = 240,
                                  n_donors = 4,
                                  n_background = 538,
                                  n_subjects_per_group = 6) {
    set.seed(seed)

    cell_types <- c("CD4_T", "CD8_T", "B_cell", "NK_cell", "Monocyte", "Dendritic")

    # Curated, recognisable marker symbols per type (unique across types).
    type_markers <- list(
        CD4_T     = c("IL7R", "CD4", "CCR7", "TCF7", "LTB", "MAL", "CD40LG", "LEF1"),
        CD8_T     = c("CD8A", "CD8B", "GZMK", "CCL5", "GZMA", "DUSP2", "LYAR", "KLRG1"),
        B_cell    = c("MS4A1", "CD79A", "CD79B", "CD19", "TCL1A", "BANK1", "IGHM", "VPREB3"),
        NK_cell   = c("NKG7", "GNLY", "KLRD1", "NCAM1", "KLRF1", "PRF1", "GZMB", "FCGR3A"),
        Monocyte  = c("CD14", "LYZ", "S100A8", "S100A9", "FCN1", "VCAN", "CSF1R", "CEBPD"),
        Dendritic = c("FCER1A", "CLEC10A", "CD1C", "LILRA4", "IRF8", "ITGAX", "CLEC9A", "CD1E")
    )
    # Shared programs create collinearity (closely-related types) -- exercises
    # DWLS (collinear-robust) vs other methods, and method-fragility flagging.
    shared_T       <- c("CD3D", "CD3E", "CD3G", "TRAC", "TRBC2", "IL32", "CD2", "CD7")
    shared_myeloid <- c("HLA-DRA", "AIF1", "TYROBP", "FCER1G", "CST3", "COTL1")

    bg <- sprintf("BG%04d", seq_len(n_background))
    genes <- c(unlist(type_markers, use.names = FALSE), shared_T, shared_myeloid, bg)
    genes <- make.unique(genes)
    n_genes <- length(genes)

    # Baseline (background) mean expression for every gene, shared across types.
    base_mu <- 0.2 + rgamma(n_genes, shape = 1.2, rate = 1.2)
    mu <- matrix(base_mu, nrow = n_genes, ncol = length(cell_types),
                 dimnames = list(genes, cell_types))

    boost <- function(symbols, types, lo, hi) {
        idx <- match(symbols, genes)
        for (ty in types) mu[idx, ty] <<- mu[idx, ty] + runif(length(idx), lo, hi)
    }
    for (ty in cell_types) boost(type_markers[[ty]], ty, 18, 32)  # strong, type-specific
    boost(shared_T, c("CD4_T", "CD8_T"), 10, 18)                  # collinear T program
    boost(shared_myeloid, c("Monocyte", "Dendritic"), 10, 18)     # collinear myeloid program

    # ---- single cells: counts ~ Poisson(mu[, type] * cell size factor) -------
    donors <- paste0("donor", seq_len(n_donors))
    n_cells <- cells_per_type * length(cell_types)
    counts <- matrix(0L, nrow = n_genes, ncol = n_cells, dimnames = list(genes, NULL))
    cell_type_vec <- character(n_cells)
    donor_vec <- character(n_cells)

    j <- 0L
    for (ty in cell_types) {
        for (i in seq_len(cells_per_type)) {
            j <- j + 1L
            sf <- rgamma(1, shape = 8, rate = 8)            # ~1, modest cell-to-cell var
            counts[, j] <- rpois(n_genes, lambda = mu[, ty] * sf)
            cell_type_vec[j] <- ty
            donor_vec[j] <- donors[((j - 1L) %% n_donors) + 1L]
        }
    }
    colnames(counts) <- sprintf("cell%05d", seq_len(n_cells))
    cell_meta <- data.frame(cell_id = colnames(counts),
                            cell_type = cell_type_vec,
                            donor_id = donor_vec,
                            stringsAsFactors = FALSE)

    # True linear per-cell-type expression signature (genes x type).
    signature <- vapply(cell_types, function(ty)
        rowMeans(counts[, cell_type_vec == ty, drop = FALSE]),
        numeric(n_genes))
    rownames(signature) <- genes

    # ---- bulk: proportion-weighted mixtures with known ground truth ----------
    groups <- c("responder", "nonresponder")
    timepoints <- c("baseline", "week12")
    # Baseline composition (sums to 1), order matches cell_types.
    base_comp <- c(CD4_T = 0.30, CD8_T = 0.20, B_cell = 0.15,
                   NK_cell = 0.10, Monocyte = 0.18, Dendritic = 0.07)
    base_comp <- base_comp[cell_types]

    dirichlet <- function(alpha) { g <- rgamma(length(alpha), shape = alpha, rate = 1); g / sum(g) }
    conc <- 220  # concentration: higher = tighter around the target composition

    samples <- list(); gt <- list(); meta <- list(); k <- 0L
    libsize <- 2e5
    for (grp in groups) {
        for (s in seq_len(n_subjects_per_group)) {
            subj <- sprintf("%s_S%02d", substr(grp, 1, 2), s)
            for (tp in timepoints) {
                k <- k + 1L
                target <- base_comp
                if (tp == "week12" && grp == "nonresponder") {
                    # Inflammation persists: monocytes up, CD8 T down (renormalised).
                    target["Monocyte"] <- target["Monocyte"] + 0.14
                    target["CD8_T"]    <- max(target["CD8_T"] - 0.11, 0.02)
                    target <- target / sum(target)
                }
                comp <- dirichlet(target * conc)
                names(comp) <- cell_types
                lambda <- as.numeric(signature %*% comp)            # genes (linear mixture)
                lambda <- lambda / sum(lambda) * libsize
                expr <- rpois(n_genes, lambda = lambda)             # counts-like noise
                sid <- sprintf("%s_%s_%s", substr(grp, 1, 2), sprintf("S%02d", s), tp)
                samples[[sid]] <- expr
                gt[[sid]] <- comp
                meta[[sid]] <- data.frame(sample_id = sid, group = grp,
                                         timepoint = tp, subject_id = subj,
                                         stringsAsFactors = FALSE)
            }
        }
    }

    bulk <- do.call(cbind, samples); rownames(bulk) <- genes
    metadata <- do.call(rbind, meta); rownames(metadata) <- NULL
    ground_truth <- data.frame(sample_id = names(gt),
                               do.call(rbind, gt), check.names = FALSE,
                               stringsAsFactors = FALSE)
    rownames(ground_truth) <- NULL

    list(counts = counts, cell_meta = cell_meta, bulk = bulk,
         metadata = metadata, ground_truth = ground_truth,
         signature = signature, cell_types = cell_types)
}


#' Load the synthetic example dataset (reference SCE + bulk + metadata + truth)
#'
#' @return list(reference [SCE], bulk [matrix], metadata [df],
#'              ground_truth [df], cell_type_col, batch_col)
load_example_data <- function() {
    cat("\n=== Loading example data (synthetic immune PBMC-like cohort) ===\n\n")
    cat("   Reference : 6 immune cell types x 4 donors (single-cell counts)\n")
    cat("   Bulk      : 24 samples = responder vs nonresponder x baseline/week12\n")
    cat("   Truth     : known cell-type proportions per sample (for recovery checks)\n\n")

    sim <- .simulate_immune_data()

    sce <- SingleCellExperiment::SingleCellExperiment(
        assays  = list(counts = sim$counts),
        colData = S4Vectors::DataFrame(sim$cell_meta)
    )
    rownames(sce) <- rownames(sim$counts)
    colnames(sce) <- sim$cell_meta$cell_id

    .print_reference_summary(sce)
    cat("   Bulk samples:", ncol(sim$bulk), "| groups: responder, nonresponder",
        "| timepoints: baseline, week12\n\n")
    cat("[OK] Example data loaded successfully!", ncol(sce), "reference cells,",
        ncol(sim$bulk), "bulk samples\n\n")

    list(reference = sce, bulk = sim$bulk, metadata = sim$metadata,
         ground_truth = sim$ground_truth,
         cell_type_col = "cell_type", batch_col = "donor_id")
}
