# =============================================================================
# Bulk RNA-seq Cell-Type Deconvolution -- Multi-method panel + concordance
# =============================================================================
# Runs a LICENSE-CLEAN panel of deconvolution methods (default BayesPrism + DWLS;
# optionally MuSiC + Bisque) via DIRECT method calls. Returns per-method
# proportions, a cross-method consensus, and a concordance table that flags
# method-fragile cell types.
#
# DESIGN (see references/method-selection-guide.md): no single method is
# universally best; reference quality + linear-scale preprocessing dominate.
# So we run a panel and report agreement rather than betting on one method.
# NEVER use CIBERSORTx or EPIC (non-commercial -- see references/license-notes.md).
#
# REBUILD NOTES (Biomni):
#   - omnideconv was dropped. Method dispatch is now direct calls only.
#   - n_cores defaults to min(parallel::detectCores() - 1, 8) instead of 1.
#   - All status/log messages use ASCII tokens ([OK] / [FAIL]); no Unicode.
# =============================================================================

suppressPackageStartupMessages({
    library(SingleCellExperiment)
})

# --- Preprocessing helpers ---------------------------------------------------

#' Coerce bulk to a numeric gene x sample matrix on LINEAR scale.
#' Most methods expect non-log expression; if the matrix looks log-scale
#' (negatives, or small dynamic range with max < ~30) we exponentiate (2^x).
.prep_bulk_linear <- function(bulk) {
    bulk <- as.matrix(bulk)
    storage.mode(bulk) <- "double"
    rng <- diff(range(bulk, na.rm = TRUE))
    looks_log <- any(bulk < 0, na.rm = TRUE) ||
                 (max(bulk, na.rm = TRUE) < 30 && rng < 30)
    scale_note <- "linear (used as-is)"
    if (looks_log) {
        message("[deconv] bulk looks log-scale -> exponentiating (2^x) to linear.")
        bulk <- 2^bulk
        scale_note <- "log-scale detected -> exponentiated 2^x"
    }
    attr(bulk, "scale_note") <- scale_note
    bulk
}

#' Extract a dense counts matrix (genes x cells), cell-type labels, donor ids.
.sce_parts <- function(sce) {
    cm <- SummarizedExperiment::assay(sce, "counts")
    cm <- as.matrix(cm)
    storage.mode(cm) <- "double"
    cd <- SummarizedExperiment::colData(sce)
    list(counts = cm,
         labels = as.character(cd$cell_type),
         batch  = as.character(cd$donor_id))
}

#' Cap cells per type so heavy methods (BayesPrism) stay tractable.
.downsample_reference <- function(sce, max_per_type = 300, seed = 1) {
    cd <- SummarizedExperiment::colData(sce)
    ct <- as.character(cd$cell_type)
    set.seed(seed)
    keep <- unlist(lapply(split(seq_along(ct), ct), function(idx) {
        if (length(idx) > max_per_type) sample(idx, max_per_type) else idx
    }), use.names = FALSE)
    if (length(keep) < length(ct)) {
        message("[deconv] downsampled reference to <= ", max_per_type,
                " cells/type (", length(keep), " of ", length(ct), " cells).")
        sce <- sce[, sort(keep)]
    }
    sce
}

#' Align a raw proportion matrix to (samples x cell_types), rows summing to 1.
.align_props <- function(mat, samples, cell_types) {
    mat <- as.matrix(mat)
    # If methods return cell_types x samples, transpose to samples x cell_types.
    if (!is.null(rownames(mat)) && all(samples %in% rownames(mat))) {
        mat <- mat[samples, , drop = FALSE]
    } else if (!is.null(colnames(mat)) && all(samples %in% colnames(mat))) {
        mat <- t(mat)[samples, , drop = FALSE]
    } else if (nrow(mat) == length(samples)) {
        rownames(mat) <- samples
    } else if (ncol(mat) == length(samples)) {
        mat <- t(mat); rownames(mat) <- samples
    } else {
        stop("Cannot align proportion matrix to sample set.")
    }
    # Columns -> reference cell types (fill any missing with 0, drop extras).
    out <- matrix(0, nrow = length(samples), ncol = length(cell_types),
                  dimnames = list(samples, cell_types))
    shared <- intersect(colnames(mat), cell_types)
    out[, shared] <- mat[, shared, drop = FALSE]
    rs <- rowSums(out); rs[rs == 0] <- 1
    out / rs
}

#' Tidy a samples x cell_types matrix into a data.frame with a sample_id column.
.props_to_df <- function(mat) {
    df <- as.data.frame(mat, check.names = FALSE)
    df <- cbind(sample_id = rownames(mat), df)
    rownames(df) <- NULL
    df
}

#' Choose a sensible BayesPrism core count. Caps at 8 to stay polite on
#' shared machines, falls back to 1 if parallel::detectCores() is unavailable.
.auto_n_cores <- function() {
    n <- tryCatch(parallel::detectCores(), error = function(e) NA_integer_)
    if (is.na(n) || !is.finite(n)) return(1L)
    as.integer(min(max(n - 1L, 1L), 8L))
}


# --- Per-method runners (direct calls) ---------------------------------------

#' BayesPrism (direct) -- Bayesian; robust to reference<->bulk mismatch. GPL.
.run_bayesprism <- function(sc_mat, labels, bulk, n_cores = 1) {
    if (!requireNamespace("BayesPrism", quietly = TRUE))
        stop("BayesPrism not installed: remotes::install_github('Danko-Lab/BayesPrism/BayesPrism')")
    prism <- BayesPrism::new.prism(
        reference = t(sc_mat),               # cells x genes
        mixture   = t(bulk),                 # samples x genes
        input.type = "count.matrix",
        cell.type.labels = labels,
        cell.state.labels = labels,
        key = NULL)
    res <- BayesPrism::run.prism(prism, n.cores = n_cores)
    BayesPrism::get.fraction(res, which.theta = "final", state.or.type = "type")
}

#' DWLS (direct) -- dampened weighted least squares; handles collinear types.
#' Builds the signature as the per-cell-type mean profile, then solves per sample.
.run_dwls <- function(sc_mat, labels, bulk) {
    if (!requireNamespace("DWLS", quietly = TRUE))
        stop("DWLS not installed: install.packages('DWLS')")
    cts <- sort(unique(labels))
    sig <- vapply(cts, function(ct) rowMeans(sc_mat[, labels == ct, drop = FALSE]),
                  numeric(nrow(sc_mat)))
    rownames(sig) <- rownames(sc_mat)
    out <- matrix(NA_real_, nrow = ncol(bulk), ncol = length(cts),
                  dimnames = list(colnames(bulk), cts))
    for (s in colnames(bulk)) {
        sol <- tryCatch({
            tr <- DWLS::trimData(sig, bulk[, s])
            DWLS::solveDampenedWLS(tr$sig, tr$bulk)
        }, error = function(e) NULL)
        if (!is.null(sol)) out[s, names(sol)] <- sol[names(sol)]
    }
    if (mean(is.na(out)) > 0.5) stop("DWLS failed on >50% of samples.")
    out[is.na(out)] <- 0
    out
}

#' MuSiC (direct) -- cross-subject weighting. Needs the SCE (uses donor_id).
.run_music <- function(sce, bulk) {
    if (!requireNamespace("MuSiC", quietly = TRUE))
        stop("MuSiC not installed: remotes::install_github('xuranw/MuSiC')")
    est <- MuSiC::music_prop(bulk.mtx = bulk, sc.sce = sce,
                             clusters = "cell_type", samples = "donor_id",
                             verbose = FALSE)
    est$Est.prop.weighted
}

#' Bisque (direct) -- fast reference-based decomposition. GPL.
.run_bisque <- function(sc_mat, labels, batch, bulk) {
    if (!requireNamespace("BisqueRNA", quietly = TRUE) ||
        !requireNamespace("Biobase", quietly = TRUE))
        stop("Bisque needs 'BisqueRNA' + 'Biobase': install.packages(c('BisqueRNA','Biobase'))")
    bulk_eset <- Biobase::ExpressionSet(assayData = bulk)
    sc_pheno <- Biobase::AnnotatedDataFrame(
        data.frame(cellType = labels, SubjectName = batch,
                   row.names = colnames(sc_mat)))
    sc_eset <- Biobase::ExpressionSet(assayData = sc_mat, phenoData = sc_pheno)
    res <- BisqueRNA::ReferenceBasedDecomposition(
        bulk_eset, sc_eset, markers = NULL, use.overlap = FALSE)
    t(res$bulk.props)                         # samples x cell_type
}


# --- Concordance + consensus -------------------------------------------------

#' Pairwise cross-method Pearson concordance (overall + per cell type) and a
#' list of method-fragile cell types (mean pairwise per-type r < threshold).
.compute_concordance <- function(prop_list, cell_types, fragile_threshold = 0.5) {
    methods <- names(prop_list)
    rows <- list(); per_ct <- list()
    if (length(methods) >= 2) {
        pairs <- utils::combn(methods, 2)
        for (p in seq_len(ncol(pairs))) {
            a <- pairs[1, p]; b <- pairs[2, p]
            A <- prop_list[[a]]; B <- prop_list[[b]]
            cc <- intersect(colnames(A), colnames(B))
            cc <- intersect(cc, cell_types)
            r_overall <- suppressWarnings(stats::cor(as.vector(A[, cc]), as.vector(B[, cc])))
            rows[[length(rows) + 1]] <- data.frame(
                method_a = a, method_b = b, cell_type = "__overall__",
                pearson_r = r_overall, n = length(cc) * nrow(A),
                stringsAsFactors = FALSE)
            for (ct in cc) {
                r_ct <- suppressWarnings(stats::cor(A[, ct], B[, ct]))
                rows[[length(rows) + 1]] <- data.frame(
                    method_a = a, method_b = b, cell_type = ct,
                    pearson_r = r_ct, n = nrow(A), stringsAsFactors = FALSE)
                per_ct[[ct]] <- c(per_ct[[ct]], r_ct)
            }
        }
    }
    table <- if (length(rows)) do.call(rbind, rows) else data.frame()
    fragile <- names(Filter(function(v) mean(v, na.rm = TRUE) < fragile_threshold, per_ct))
    list(table = table, fragile = fragile)
}

#' Elementwise mean across methods over shared samples + cell types.
.consensus <- function(prop_list, samples, cell_types) {
    arr <- lapply(prop_list, function(m) {
        out <- matrix(NA_real_, length(samples), length(cell_types),
                      dimnames = list(samples, cell_types))
        cc <- intersect(colnames(m), cell_types)
        out[samples, cc] <- m[samples, cc]
        out
    })
    Reduce(`+`, lapply(arr, function(m) { m[is.na(m)] <- 0; m })) / length(arr)
}


# --- Main entry point --------------------------------------------------------

#' Run the deconvolution panel.
#'
#' @param bulk gene x sample matrix (linear or log; auto-detected)
#' @param reference SingleCellExperiment (assay 'counts', colData cell_type/donor_id)
#' @param methods character vector; subset of c("bayesprism","dwls","music","bisque")
#' @param max_cells_per_type cap reference cells per type (speed; default 300)
#' @param n_cores cores for BayesPrism; default auto-detect (capped at 8)
#' @return list with per-method proportions, consensus, concordance, metadata
run_deconvolution <- function(bulk, reference,
                              methods = c("bayesprism", "dwls"),
                              max_cells_per_type = 300,
                              n_cores = NULL) {

    cat("\n=== Running deconvolution panel ===\n\n")
    methods <- tolower(methods)
    banned <- intersect(methods, c("cibersortx", "cibersort", "epic", "bseqsc"))
    if (length(banned))
        stop("Method(s) excluded by license (non-commercial): ",
             paste(banned, collapse = ", "),
             ". See references/license-notes.md. Use bayesprism/dwls/music/bisque.")
    valid <- c("bayesprism", "dwls", "music", "bisque")
    bad <- setdiff(methods, valid)
    if (length(bad)) stop("Unknown method(s): ", paste(bad, collapse = ", "),
                          ". Choose from: ", paste(valid, collapse = ", "))

    if (is.null(n_cores)) n_cores <- .auto_n_cores()
    cat("   BayesPrism n_cores:", n_cores, "\n")

    reference <- .downsample_reference(reference, max_per_type = max_cells_per_type)
    parts <- .sce_parts(reference)
    bulk_lin <- .prep_bulk_linear(bulk)
    scale_note <- attr(bulk_lin, "scale_note")

    # Restrict to genes shared by bulk and reference (in both matrices).
    common <- intersect(rownames(bulk_lin), rownames(parts$counts))
    if (length(common) < 50)
        warning("[deconv] only ", length(common),
                " shared genes -- check gene-ID type matches (symbols vs Ensembl).")
    cat("   Shared genes:", length(common), "| scale:", scale_note, "\n")
    sc_mat <- parts$counts[common, , drop = FALSE]
    bulk_c <- bulk_lin[common, , drop = FALSE]
    ref_c  <- reference[common, ]
    labels <- parts$labels
    batch  <- parts$batch
    cell_types <- sort(unique(labels))
    samples <- colnames(bulk_c)

    prop_list <- list()
    for (m in methods) {
        cat("\n   >>> method:", m, "\n")
        t0 <- Sys.time()
        raw <- tryCatch(switch(m,
            bayesprism = .run_bayesprism(sc_mat, labels, bulk_c, n_cores = n_cores),
            dwls       = .run_dwls(sc_mat, labels, bulk_c),
            music      = .run_music(ref_c, bulk_c),
            bisque     = .run_bisque(sc_mat, labels, batch, bulk_c)),
            error = function(e) {
                cat("       [FAIL]", m, "failed:", conditionMessage(e), "\n"); NULL })
        if (is.null(raw)) { cat("       (skipped -- no result)\n"); next }
        prop_list[[m]] <- .align_props(raw, samples, cell_types)
        dt <- round(as.numeric(difftime(Sys.time(), t0, units = "secs")), 1)
        cat("       [OK]", m, "done (", dt, "s )\n")
    }

    if (length(prop_list) == 0)
        stop("All methods failed. Install at least BayesPrism or DWLS and retry; ",
             "see references/license-notes.md for license-clean options.")

    consensus <- .consensus(prop_list, samples, cell_types)
    conc <- .compute_concordance(prop_list, cell_types)

    if (nrow(conc$table)) {
        ov <- conc$table[conc$table$cell_type == "__overall__", ]
        for (i in seq_len(nrow(ov)))
            cat(sprintf("   concordance %s vs %s: r = %.3f\n",
                        ov$method_a[i], ov$method_b[i], ov$pearson_r[i]))
    }
    if (length(conc$fragile))
        cat("   [WARN] method-fragile cell types (low cross-method r):",
            paste(conc$fragile, collapse = ", "), "\n")

    cat("\n[OK] Deconvolution completed!", length(prop_list), "method(s) [",
        paste(names(prop_list), collapse = ", "), "],", length(cell_types),
        "cell types,", length(samples), "samples\n\n")

    list(
        proportions = lapply(prop_list, .props_to_df),
        consensus = .props_to_df(consensus),
        concordance = conc$table,
        fragile_cell_types = conc$fragile,
        methods = names(prop_list),
        methods_requested = methods,
        cell_types = cell_types,
        samples = samples,
        n_shared_genes = length(common),
        bulk_scale = scale_note,
        reference_summary = list(
            n_cells = ncol(reference), n_genes = nrow(reference),
            cell_types = cell_types,
            n_per_type = as.list(table(labels)),
            n_donors = length(unique(batch)))
    )
}
