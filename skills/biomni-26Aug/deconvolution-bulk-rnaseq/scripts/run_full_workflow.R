#!/usr/bin/env Rscript
# =============================================================================
# Bulk RNA-seq Cell-Type Deconvolution -- Full workflow orchestrator
# =============================================================================
# Loads inputs (or the bundled synthetic example), runs the multi-method
# deconvolution panel, tests group contrasts, generates figures, and exports
# all CSVs / RDS / report into output_dir. The Python wrapper
# (`scripts/deconvolution.py`) drives this script via Rscript.
#
# Usage (CLI):
#   Rscript scripts/run_full_workflow.R \
#     --bulk path/to/bulk.csv \
#     --reference path/to/reference.h5ad \
#     --metadata path/to/metadata.csv \
#     --output-dir /mnt/results/deconvolution \
#     [--methods bayesprism,dwls] \
#     [--group-col group] [--timepoint-col timepoint] [--subject-col subject_id] \
#     [--ground-truth path/to/ground_truth.csv] \
#     [--max-cells-per-type 300] [--n-cores AUTO] \
#     [--cell-type-col cell_type] [--batch-col donor_id] \
#     [--example]        # write the synthetic example to output_dir and exit
#
# CSV inputs:
#   bulk:        first column = gene symbol, columns 2..N = sample_id
#   metadata:    must contain 'sample_id' column + group_col (and optional timepoint/subject cols)
#   ground_truth (optional): 'sample_id' + one column per cell type
# =============================================================================

# --- Arg parsing (lightweight; no extra deps) --------------------------------
.parse_args <- function(argv) {
    out <- list(
        bulk = NULL, reference = NULL, metadata = NULL,
        output_dir = "results",
        methods = c("bayesprism", "dwls"),
        group_col = "group", group_levels = NULL,
        timepoint_col = NULL, subject_col = NULL,
        ground_truth = NULL,
        max_cells_per_type = 300L, n_cores = NA_integer_,
        cell_type_col = "cell_type", batch_col = "donor_id",
        example = FALSE
    )
    i <- 1L
    while (i <= length(argv)) {
        a <- argv[[i]]
        take <- function() { i <<- i + 1L; argv[[i]] }
        switch(a,
            "--bulk"          = { out$bulk <- take() },
            "--reference"     = { out$reference <- take() },
            "--metadata"      = { out$metadata <- take() },
            "--output-dir"    = { out$output_dir <- take() },
            "--methods"       = { out$methods <- strsplit(take(), ",")[[1]] },
            "--group-col"     = { out$group_col <- take() },
            "--group-levels"  = { v <- take(); out$group_levels <- if (nzchar(v)) strsplit(v, ",")[[1]] else NULL },
            "--timepoint-col" = { v <- take(); out$timepoint_col <- if (nzchar(v)) v else NULL },
            "--subject-col"   = { v <- take(); out$subject_col <- if (nzchar(v)) v else NULL },
            "--ground-truth"  = { v <- take(); out$ground_truth <- if (nzchar(v)) v else NULL },
            "--max-cells-per-type" = { out$max_cells_per_type <- as.integer(take()) },
            "--n-cores"       = { v <- take(); out$n_cores <- if (toupper(v) == "AUTO") NA_integer_ else as.integer(v) },
            "--cell-type-col" = { out$cell_type_col <- take() },
            "--batch-col"     = { out$batch_col <- take() },
            "--example"       = { out$example <- TRUE },
            "--help"          = {
                cat("See scripts/run_full_workflow.R header for usage.\n")
                quit(save = "no", status = 0)
            },
            stop("Unknown argument: ", a)
        )
        i <- i + 1L
    }
    out
}

`%||%` <- function(a, b) if (is.null(a)) b else a

# --- Locate scripts/ relative to this file -----------------------------------
.script_dir <- function() {
    # Rscript: try commandArgs trick first
    cmd_args <- commandArgs(trailingOnly = FALSE)
    fa <- "--file="
    m <- grep(fa, cmd_args, fixed = TRUE, value = TRUE)
    if (length(m)) return(normalizePath(dirname(sub(fa, "", m[1])), mustWork = FALSE))
    # source()'d: best-effort fallback
    if (!is.null(sys.frames()) && length(sys.frames()) >= 1) {
        sf <- sys.frame(1)
        if (!is.null(sf$ofile)) return(normalizePath(dirname(sf$ofile), mustWork = FALSE))
    }
    normalizePath("scripts", mustWork = FALSE)
}

# --- Main --------------------------------------------------------------------
main <- function(argv) {
    args <- .parse_args(argv)
    sd <- .script_dir()
    cat("[workflow] script dir:", sd, "\n")

    source(file.path(sd, "load_data.R"))
    source(file.path(sd, "run_deconvolution.R"))
    source(file.path(sd, "deconv_stats.R"))
    source(file.path(sd, "deconv_plots.R"))
    source(file.path(sd, "export_results.R"))

    dir.create(args$output_dir, showWarnings = FALSE, recursive = TRUE)
    cat("[workflow] output_dir:", args$output_dir, "\n")

    # --- Example-write path: dump synthetic data + exit ----------------------
    if (isTRUE(args$example)) {
        ex <- load_example_data()
        bulk_df <- data.frame(gene = rownames(ex$bulk),
                              as.data.frame(ex$bulk),
                              check.names = FALSE)
        write.csv(bulk_df, file.path(args$output_dir, "example_bulk.csv"), row.names = FALSE)
        write.csv(ex$metadata,     file.path(args$output_dir, "example_metadata.csv"), row.names = FALSE)
        write.csv(ex$ground_truth, file.path(args$output_dir, "example_ground_truth.csv"), row.names = FALSE)
        # Reference: save as .rds (load_reference handles SCE .rds).
        ref_path <- file.path(args$output_dir, "example_reference.rds")
        # Stage to /workspace/ if output is on FUSE; saveRDS is sequential so usually OK,
        # but use the staging helper for safety.
        if (exists(".saveRDS_staged")) .saveRDS_staged(ex$reference, ref_path) else saveRDS(ex$reference, ref_path)
        cat("[workflow] wrote example inputs into", args$output_dir, "\n")
        return(invisible(0L))
    }

    # --- Validate required args ---------------------------------------------
    if (is.null(args$bulk) || is.null(args$reference) || is.null(args$metadata))
        stop("--bulk, --reference, and --metadata are required (or pass --example).")

    # --- Step 1: load ---------------------------------------------------------
    reference <- load_reference(args$reference,
                                cell_type_col = args$cell_type_col,
                                batch_col = args$batch_col)
    bulk <- load_bulk(args$bulk)
    validate_inputs(bulk, reference)

    metadata <- utils::read.csv(args$metadata, stringsAsFactors = FALSE,
                                check.names = FALSE)
    if (!"sample_id" %in% colnames(metadata))
        stop("metadata CSV must have a 'sample_id' column.")

    ground_truth <- NULL
    if (!is.null(args$ground_truth) && nzchar(args$ground_truth) && file.exists(args$ground_truth)) {
        ground_truth <- utils::read.csv(args$ground_truth, stringsAsFactors = FALSE,
                                        check.names = FALSE)
        cat("[workflow] ground truth:", nrow(ground_truth), "samples x",
            ncol(ground_truth) - 1L, "cell types\n")
    }

    # --- Step 2: deconvolve ---------------------------------------------------
    deconv <- run_deconvolution(
        bulk, reference,
        methods            = args$methods,
        max_cells_per_type = args$max_cells_per_type,
        n_cores            = if (is.na(args$n_cores)) NULL else args$n_cores)

    # --- Step 3: stats --------------------------------------------------------
    contrasts <- NULL
    if (!is.null(args$group_col) && args$group_col %in% colnames(metadata)) {
        contrasts <- tryCatch(
            proportion_contrasts(
                deconv$consensus, metadata,
                group_col     = args$group_col,
                group_levels  = args$group_levels,
                timepoint_col = args$timepoint_col,
                subject_col   = args$subject_col,
                mixed_model   = !is.null(args$subject_col) && !is.null(args$timepoint_col)),
            error = function(e) {
                cat("   [WARN] proportion_contrasts failed:", conditionMessage(e), "\n")
                NULL
            })
    } else {
        cat("[workflow] no group_col '", args$group_col,
            "' in metadata -- skipping contrasts.\n", sep = "")
    }

    # --- Step 4: plots --------------------------------------------------------
    tryCatch(
        generate_all_plots(deconv, metadata = metadata, contrasts = contrasts,
                           ground_truth = ground_truth,
                           group_col = args$group_col,
                           timepoint_col = args$timepoint_col %||% "timepoint",
                           output_dir = args$output_dir),
        error = function(e) cat("[workflow] plotting failed:", conditionMessage(e), "\n"))

    # --- Step 5: export -------------------------------------------------------
    export_all(deconv, metadata = metadata, contrasts = contrasts,
               ground_truth = ground_truth, output_dir = args$output_dir,
               group_col = args$group_col,
               timepoint_col = args$timepoint_col %||% "timepoint")

    cat("\n[workflow] DONE -- outputs in:", args$output_dir, "\n")
    invisible(0L)
}

if (sys.nframe() == 0L) {
    argv <- commandArgs(trailingOnly = TRUE)
    main(argv)
}
