# =============================================================================
# Microplate Layout Design - Export Results
# =============================================================================
# Exports plate layouts in multiple formats for lab use and downstream analysis.
# =============================================================================

suppressPackageStartupMessages({
    library(jsonlite)
})

# --- Provenance source tag: render a human-readable tag from the source code ---
# Turns "inferred" / "example_default" / "user" / "function_default" into a
# bracketed annotation for the quality report.
.source_tag <- function(src) {
    if (is.null(src) || is.na(src)) return("")
    switch(src,
        "user" = "[DECLARED]",
        "example_default" = "[EXAMPLE DEFAULT]",
        "function_default" = "[DEFAULT]",
        "inferred" = "[INFERRED — not declared]",
        "")
}

# --- Safe list subsetting: return only names that actually exist ---
# R's x[nms] returns an NA-named NULL element for absent names, which
# jsonlite serialises as a positional numeric key (e.g. "18": {}). This
# helper filters to only the names present, preventing that regression.
.pick <- function(x, nms) x[nms[nms %in% names(x)]]

# --- Render the design_provenance data.frame as report text lines ---
.render_provenance_lines <- function(prov) {
    lines <- sprintf("  %-25s %-30s %-18s %s",
        "Parameter", "Value", "Source", "Derivation")
    lines <- c(lines, paste0("  ", paste(rep("-", 78), collapse = "")))
    for (i in seq_len(nrow(prov))) {
        lines <- c(lines, sprintf("  %-25s %-30s %-18s %s",
            prov$parameter[i],
            substr(as.character(prov$value[i]), 1, 30),
            prov$source[i],
            prov$derivation[i]))
    }
    lines
}

# --- Main export function ---
export_all <- function(layout, experiment = NULL, output_dir = "layout_results") {
    if (!inherits(layout, "plate_layout")) {
        stop("Input must be a 'plate_layout' object from generate_plate_layout()")
    }

    if (is.null(experiment)) experiment <- layout$experiment
    dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

    cat("\n=== Exporting Plate Layout ===\n")
    cat("Output directory:", output_dir, "\n\n")

    plate_data <- layout$plate_data

    # FIX 4: track export status so failures surface in the return value and
    # the quality report instead of being silently swallowed.
    export_status <- list(excel = "not_attempted", warnings = character(0))

    # 1. Tidy CSV (one row per well)
    cat("1. Tidy CSV (plate_layout.csv)...\n")
    # FIX 1: include measurand + bio_sample columns when present so the tidy
    # export captures the full multi-measurand design.
    tidy_cols <- c("plate", "well", "row_label", "col_label",
                   "row", "col", "is_edge", "well_role",
                   "sample_id", "treatment", "replicate", "sample_type")
    if ("measurand" %in% colnames(plate_data)) tidy_cols <- c(tidy_cols, "measurand")
    if ("bio_sample" %in% colnames(plate_data)) tidy_cols <- c(tidy_cols, "bio_sample")
    tidy_df <- plate_data[, tidy_cols]

    # Add dose_uM column: parse concentration from treatment labels using the
    # shared parse_concentration_uM() parser (defined in power_analysis.R) so
    # the CSV and any dose-based analysis cannot disagree about what the doses
    # are. NA for non-dose groups (controls, calibrators, treatments without a
    # parseable concentration unit, vehicle).
    if (exists("parse_concentration_uM", mode = "function")) {
        tidy_df$dose_uM <- sapply(as.character(tidy_df$treatment),
                                  parse_concentration_uM)
    } else {
        # Fallback if power_analysis.R was not sourced: inline copy of the
        # same regex. This branch should not fire in the documented workflow.
        .parse_dose_um <- function(label) {
            if (is.na(label)) return(NA_real_)
            m <- regmatches(label, regexec("([0-9]+\\.?[0-9]*(?:[eE][+-]?[0-9]+)?)\\s*(nM|uM|µM|mM|M)\\b", label))
            if (length(m[[1]]) < 3) return(NA_real_)
            val <- as.numeric(m[[1]][2]); unit <- m[[1]][3]
            switch(unit, "nM" = val * 1e-3, "uM" = val, "µM" = val,
                   "mM" = val * 1e3, "M" = val * 1e6, NA_real_)
        }
        tidy_df$dose_uM <- sapply(as.character(tidy_df$treatment), .parse_dose_um)
    }

    write.csv(tidy_df, file.path(output_dir, "plate_layout.csv"), row.names = FALSE)
    cat("   Saved:", file.path(output_dir, "plate_layout.csv"), "\n")

    # 1b. Design parameter provenance CSV
    if (!is.null(experiment$design_provenance)) {
        prov_path <- file.path(output_dir, "design_provenance.csv")
        write.csv(experiment$design_provenance, prov_path, row.names = FALSE)
        cat("   Saved:", prov_path, "\n")
    }

    # 2. Plate-shaped grid CSV (one per plate)
    cat("2. Grid CSV (plate_layout_grid.csv)...\n")
    for (p in 1:experiment$n_plates) {
        plate_subset <- plate_data[plate_data$plate == p, ]
        dims <- experiment$plate_dims

        # Create a matrix with well contents
        grid <- matrix("", nrow = dims$rows, ncol = dims$cols)
        rownames(grid) <- dims$row_labels
        colnames(grid) <- dims$col_labels

        for (i in seq_len(nrow(plate_subset))) {
            r <- plate_subset$row[i]
            c <- plate_subset$col[i]
            content <- plate_subset$sample_id[i]
            if (is.na(content)) {
                if (plate_subset$well_role[i] == "empty") {
                    content <- "[EMPTY]"
                } else {
                    content <- ""
                }
            }
            grid[r, c] <- content
        }

        suffix <- if (experiment$n_plates > 1) paste0("_plate", p) else ""
        grid_path <- file.path(output_dir, paste0("plate_layout_grid", suffix, ".csv"))
        write.csv(grid, grid_path)
        cat("   Saved:", grid_path, "\n")
    }

    # 3. Excel with color-coded cells (if openxlsx available)
    cat("3. Excel workbook (plate_layout.xlsx)...\n")
    xlsx_path <- file.path(output_dir, "plate_layout.xlsx")
    if (requireNamespace("openxlsx", quietly = TRUE)) {
        # FIX 4: do NOT silently swallow Excel failures. .export_excel() verifies
        # the file exists and is non-empty; on failure we fall back to CSV-only
        # and RECORD the failure so it appears in export_all()'s return and the
        # quality report.
        export_status$excel <- tryCatch({
            .export_excel(layout, experiment, output_dir)
            "ok"
        }, error = function(e) {
            msg <- conditionMessage(e)
            cat("   ⚠️  Excel export FAILED:", msg, "\n")
            cat("   Falling back to CSV-only (plate_layout.csv + grid CSVs).\n")
            # Remove any truncated/partial file so .verify_outputs() is accurate.
            if (file.exists(xlsx_path) && (is.na(file.info(xlsx_path)$size) ||
                                           file.info(xlsx_path)$size <= 1000)) {
                unlink(xlsx_path)
            }
            export_status$warnings <<- c(export_status$warnings,
                                         paste("Excel export failed:", msg))
            paste0("failed: ", msg)
        })
    } else {
        cat("   (openxlsx not available - skipping Excel export; CSV exports cover this)\n")
        export_status$excel <- "skipped (openxlsx not installed)"
    }

    # 4. Layout object (RDS)
    cat("4. Layout object (layout_object.rds)...\n")
    saveRDS(layout, file.path(output_dir, "layout_object.rds"))
    cat("   Saved:", file.path(output_dir, "layout_object.rds"), "\n")
    cat("   (Load with: layout <- readRDS('layout_object.rds'))\n")

    # 5. Experiment parameters (JSON)
    cat("5. Experiment parameters (experiment_parameters.json)...\n")
    params <- list(
        experiment_name = experiment$name,
        assay_type = experiment$assay_type,
        plate_format = experiment$plate_format,
        n_plates = experiment$n_plates,
        treatments = experiment$treatments,
        n_replicates = experiment$n_replicates,
        # FIX 3: persist the DECLARED biological/technical replicate vocabulary.
        n_biological = experiment$n_biological,
        n_technical = experiment$n_technical,
        # Provenance: source tags + full provenance table so the JSON carries
        # whether each value was declared, defaulted, or inferred.
        n_biological_source = experiment$n_biological_source,
        n_technical_source = experiment$n_technical_source,
        design_provenance = experiment$design_provenance,
        # FIX 1: persist ratiometric / measurand design fields when present.
        measurands = experiment$measurands,
        normalization = experiment$normalization,
        reference_measurands = experiment$reference_measurands,
        interplate_calibrator = experiment$interplate_calibrator,
        controls = experiment$controls,
        n_controls = experiment$n_controls,
        edge_strategy = experiment$edge_strategy,
        method = layout$method,
        seed = layout$seed,
        quality_scores = layout$quality,
        power_analysis = if (!is.null(layout$power_analysis))
            .pick(layout$power_analysis, c("power", "biological_power",
                                     "biological_power_valid", "biological_power_caveat",
                                     "sd_type", "delta", "sd", "required_bio_n",
                                     "bio_power_at_3", "bio_power_at_5",
                                     "reference_contrast",
                                     "parameters", "interpretation",
                                     "recommendation", "biological_recommendation")) else NULL,
        # FIX 4: record export status (e.g. Excel ok / failed / skipped).
        export_status = export_status,
        timestamp = Sys.time()
    )

    # Consistency gate: assert no numeric-named keys leaked into the
    # power_analysis subset (R returns an NA-named NULL element for absent
    # names, which jsonlite serialises as a positional key like "18": {}).
    if (!is.null(params$power_analysis)) {
        bad_keys <- names(params$power_analysis)[grepl("^[0-9]+$", names(params$power_analysis))]
        if (length(bad_keys) > 0) {
            stop("JSON consistency gate FAILED: numeric key(s) in power_analysis: ",
                 paste(bad_keys, collapse = ", "),
                 ". This indicates a list-subset regression (absent name -> NA-named NULL).")
        }
    }

    write_json(params, file.path(output_dir, "experiment_parameters.json"),
               pretty = TRUE, auto_unbox = TRUE,
               null = "null", na = "null")
    cat("   Saved:", file.path(output_dir, "experiment_parameters.json"), "\n")

    # 6. Quality report (text)
    cat("6. Quality report (layout_quality_report.txt)...\n")
    # FIX 4: pass export status so the report surfaces any export failure.
    .write_quality_report(layout, experiment, output_dir, export_status = export_status)

    # 7. Plater-format CSV (if plater available)
    cat("7. Plater-format CSV...\n")
    if (requireNamespace("plater", quietly = TRUE)) {
        tryCatch({
            .export_plater_format(layout, experiment, output_dir)
        }, error = function(e) {
            cat("   Plater export failed:", conditionMessage(e), "\n")
            export_status$warnings <<- c(export_status$warnings,
                                         paste("Plater export failed:", conditionMessage(e)))
        })
    } else {
        cat("   (plater not available - grid CSV serves same purpose)\n")
    }

    # FIX 4: verify every written file is non-empty; list offenders.
    verification <- .verify_outputs(output_dir)
    if (length(verification$offenders) > 0) {
        export_status$warnings <- c(export_status$warnings,
            paste("Empty/missing output files:",
                  paste(verification$offenders, collapse = ", ")))
    }

    cat("\n=== Export Complete ===\n")
    cat("All files saved to:", output_dir, "\n")
    if (length(export_status$warnings) > 0) {
        cat("\n⚠️  Export completed WITH WARNINGS:\n")
        for (w in export_status$warnings) cat("   -", w, "\n")
    }

    # FIX 4: return a status object (not just the dir) so callers can detect
    # partial/failed exports programmatically.
    invisible(list(
        output_dir = output_dir,
        excel = export_status$excel,
        warnings = export_status$warnings,
        verified_files = verification$ok,
        offenders = verification$offenders,
        census = layout$well_census
    ))
}

# =============================================================================
# verify_report_figures() - FIX defect 2: pre-export figure-integrity gate
# =============================================================================
# Any figure that DEPICTS PLATE WELLS must either be generated from the actual
# layout table, or carry an explicit non-data marker. Report Figure 1 is often
# an AI-generated conceptual infographic (per the platform's "always draw an
# infographic" preference). If that infographic shows a plate-like grid of
# coloured wells it reads like a plate map and can be mistaken for the real
# layout, which is Figure 2 (plate_treatment_map, rendered from the layout
# table). Call this in Step 5 BEFORE assembling the PDF; it blocks that failure.
#
#   figures : character vector of figure file paths intended for the report
#             (INCLUDE the infographic).
#   strict  : TRUE (default) -> stop() on any offender; FALSE -> warn + return.
#
# Classification (filename-based; R cannot read pixels):
#   * script data figures  (plate maps + power curve, incl. per-plate variants)
#       -> allowed: rendered from the actual layout table.
#   * images whose filename carries a schematic marker ("schematic" /
#     "illustrative") -> allowed: they declare themselves non-data. The in-figure
#     "Schematic - illustrative only, not a data figure" label and the real-count
#     annotations are enforced via SKILL.md Step 5 + a media_output_check.
#   * anything else -> OFFENDER: regenerate from the layout table, or add the
#     schematic marker to BOTH the filename and the figure itself.
verify_report_figures <- function(figures, output_dir = "layout_results", strict = TRUE) {
    if (length(figures) == 0) {
        cat("   verify_report_figures: no figures supplied.\n")
        return(invisible(list(pass = TRUE, data = character(0),
                              schematic = character(0), offenders = character(0))))
    }
    known_prefixes <- c("plate_treatment_map", "plate_sample_type_map",
                        "plate_replicate_map", "plate_edge_risk",
                        "plate_quality_dashboard", "power_curve")
    schematic_markers <- c("schematic", "illustrative")
    classify1 <- function(path) {
        b <- tolower(basename(path))
        stem <- sub("\\.(png|svg|pdf|jpg|jpeg|tif|tiff)$", "", b)
        if (any(vapply(known_prefixes, function(p) startsWith(stem, p), logical(1))))
            return("data")
        if (any(vapply(schematic_markers, function(m) grepl(m, b, fixed = TRUE), logical(1))))
            return("schematic")
        "offender"
    }
    cls <- vapply(figures, classify1, character(1))
    data_figs <- figures[cls == "data"]
    schematic_figs <- figures[cls == "schematic"]
    offenders <- figures[cls == "offender"]

    cat("\n=== Figure integrity check (defect-2 gate) ===\n")
    cat(sprintf("   Data figures (from layout table):      %d\n", length(data_figs)))
    cat(sprintf("   Declared schematics (non-data marker): %d\n", length(schematic_figs)))
    if (length(offenders) > 0) {
        msg <- paste0(
            "Figure integrity gate FAILED: ", length(offenders),
            " figure(s) may depict plate wells but are neither a script-generated ",
            "plate map nor marked as a schematic: ",
            paste(basename(offenders), collapse = ", "),
            ". Fix each: (a) regenerate it from the layout table via ",
            "visualize_all_plates(), OR (b) if it is a conceptual infographic, add ",
            "an in-figure label 'Schematic - illustrative only, not a data figure', ",
            "remove any specific well-colour assignment, print the run's real counts ",
            "beside it, and rename it with a 'schematic' marker (e.g. ",
            "'workflow_schematic.png'). The authoritative layout is ",
            "plate_treatment_map + plate_layout.csv.")
        if (strict) stop(msg)
        cat("   \u26a0\ufe0f  ", msg, "\n", sep = "")
        return(invisible(list(pass = FALSE, data = data_figs,
                              schematic = schematic_figs, offenders = offenders)))
    }
    cat("   \u2713 All report figures are data figures or declared schematics.\n")
    invisible(list(pass = TRUE, data = data_figs,
                   schematic = schematic_figs, offenders = character(0)))
}

# --- FIX 4: verify all written outputs are non-empty ---
# Walks the output directory and flags any zero-byte (or, for .xlsx, sub-1KB)
# files as offenders. Returns the list of good files and the offenders.
.verify_outputs <- function(output_dir, min_xlsx_bytes = 1000) {
    files <- list.files(output_dir, full.names = TRUE, recursive = FALSE)
    files <- files[!dir.exists(files)]
    ok <- character(0)
    offenders <- character(0)
    for (f in files) {
        sz <- file.info(f)$size
        is_xlsx <- grepl("\\.xlsx$", f, ignore.case = TRUE)
        bad <- is.na(sz) || sz == 0 || (is_xlsx && sz <= min_xlsx_bytes)
        if (bad) offenders <- c(offenders, basename(f)) else ok <- c(ok, basename(f))
    }
    cat(sprintf("   Output verification: %d file(s) OK", length(ok)))
    if (length(offenders) > 0) {
        cat(sprintf(", %d EMPTY/INVALID: %s\n",
                    length(offenders), paste(offenders, collapse = ", ")))
    } else {
        cat(", 0 empty.\n")
    }
    list(ok = ok, offenders = offenders)
}

# --- Strip dangling drawing/printer/VML relationship references from an xlsx ---
# openxlsx 4.2.x writes sheet rels that point at ../drawings/drawingN.xml,
# ../printerSettings/printerSettingsN.bin, and ../drawings/vmlDrawingN.vml
# when addStyle() is used with cell fills, but never writes those target files.
# Excel and LibreOffice ignore the missing targets, but strict OOXML readers
# (e.g. Python openpyxl) raise a KeyError and refuse to open the workbook.
# This post-save step removes the dangling Relationship entries and the
# corresponding <drawing>/<legacyDrawing> element refs from the sheet XML,
# leaving cell data and fill styles (which live in xl/styles.xml) untouched.
.fix_xlsx_dangling_rels <- function(xlsx_path) {
    if (!file.exists(xlsx_path)) return(invisible(FALSE))
    exdir <- tempfile()
    utils::unzip(xlsx_path, exdir = exdir)

    # 1. Drop dangling drawing/printer/VML Relationship entries from sheet rels.
    rels_dir <- file.path(exdir, "xl", "worksheets", "_rels")
    if (dir.exists(rels_dir)) {
        rels_files <- list.files(rels_dir, pattern = "\\.xml\\.rels$",
                                 full.names = TRUE)
        for (rp in rels_files) {
            txt <- paste(readLines(rp, warn = FALSE), collapse = "")
            txt <- gsub("<Relationship[^>]*Target=\"\\.\\./drawings/[^\"]*\"[^>]*/>",
                        "", txt)
            txt <- gsub("<Relationship[^>]*Target=\"\\.\\./printerSettings/[^\"]*\"[^>]*/>",
                        "", txt)
            writeLines(txt, rp)
        }
    }

    # 2. Drop <drawing>/<legacyDrawing> element refs from the sheet XML.
    sheet_dir <- file.path(exdir, "xl", "worksheets")
    if (dir.exists(sheet_dir)) {
        sheet_files <- list.files(sheet_dir, pattern = "^sheet[0-9]+\\.xml$",
                                  full.names = TRUE)
        for (sp in sheet_files) {
            txt <- paste(readLines(sp, warn = FALSE), collapse = "")
            txt <- gsub("<drawing[^>]*/>", "", txt)
            txt <- gsub("<legacyDrawing[^>]*/>", "", txt)
            writeLines(txt, sp)
        }
    }

    # 3. Re-zip into an ABSOLUTE temp path, then file.rename() into place.
    # The previous approach used setwd(exdir) + a relative utils::zip() output
    # path; once cwd changed to exdir the relative xlsx_path no longer resolved,
    # so the re-zip failed ("zip error: Could not create output file"), the
    # error was swallowed by the caller's tryCatch, and the original unfixed
    # file (still carrying dangling ../drawings/drawingN.xml rels) was kept —
    # breaking strict OOXML readers (openpyxl KeyError). Using an absolute temp
    # output and renaming it over the target avoids the cwd dependency entirely.
    files <- list.files(exdir, recursive = TRUE)
    owd <- setwd(exdir); on.exit(setwd(owd))
    tmp_zip <- tempfile(fileext = ".xlsx")
    zip_cmd <- Sys.which("zip")
    if (nzchar(zip_cmd)) {
        utils::zip(zipfile = tmp_zip, files = files, flags = "-qX", zip = zip_cmd)
    } else {
        # Fallback: shell out to zip if R_ZIPCMD is unset.
        system2("zip", c("-qX", tmp_zip, files))
    }
    setwd(owd)  # restore cwd before renaming (on.exit also guards this)
    # Replace the original with the cleaned archive. file.rename() across the
    # same filesystem is atomic; fall back to copy+unlink if rename fails.
    if (!file.rename(tmp_zip, xlsx_path)) {
        file.copy(tmp_zip, xlsx_path, overwrite = TRUE)
        unlink(tmp_zip)
    }
    invisible(TRUE)
}

# --- Excel export with colors ---
.export_excel <- function(layout, experiment, output_dir) {
    library(openxlsx)

    wb <- createWorkbook()
    plate_data <- layout$plate_data
    dims <- experiment$plate_dims

    # Color palette for treatments
    all_treatments <- unique(plate_data$treatment[!is.na(plate_data$treatment)])
    colors <- c("#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#F44336",
                "#00BCD4", "#795548", "#607D8B", "#E91E63", "#CDDC39",
                "#3F51B5", "#009688", "#FF5722", "#8BC34A", "#673AB7")
    if (length(all_treatments) > length(colors)) {
        colors <- rep(colors, ceiling(length(all_treatments) / length(colors)))
    }
    treatment_colors <- setNames(colors[seq_along(all_treatments)], all_treatments)

    for (p in 1:experiment$n_plates) {
        sheet_name <- if (experiment$n_plates > 1) paste("Plate", p) else "Plate Layout"
        addWorksheet(wb, sheet_name)

        plate_subset <- plate_data[plate_data$plate == p, ]

        # Create grid matrix
        grid <- matrix("", nrow = dims$rows, ncol = dims$cols)
        for (i in seq_len(nrow(plate_subset))) {
            r <- plate_subset$row[i]
            c <- plate_subset$col[i]
            content <- plate_subset$sample_id[i]
            if (is.na(content)) {
                grid[r, c] <- if (plate_subset$well_role[i] == "empty") "[EMPTY]" else ""
            } else {
                grid[r, c] <- content
            }
        }

        # Write with headers
        header <- c("", dims$col_labels)
        writeData(wb, sheet_name, t(header), startRow = 1, startCol = 1, colNames = FALSE)

        for (r in 1:dims$rows) {
            row_data <- c(dims$row_labels[r], grid[r, ])
            writeData(wb, sheet_name, t(row_data), startRow = r + 1, startCol = 1, colNames = FALSE)

            # Color cells
            for (c in 1:dims$cols) {
                well_idx <- which(plate_subset$row == r & plate_subset$col == c)
                if (length(well_idx) == 1) {
                    trt <- plate_subset$treatment[well_idx]
                    if (!is.na(trt) && trt %in% names(treatment_colors)) {
                        style <- createStyle(fgFill = treatment_colors[trt],
                                             halign = "center", fontSize = 9)
                        addStyle(wb, sheet_name, style, rows = r + 1, cols = c + 1)
                    } else if (plate_subset$well_role[well_idx] == "empty") {
                        style <- createStyle(fgFill = "#E0E0E0",
                                             halign = "center", fontSize = 9)
                        addStyle(wb, sheet_name, style, rows = r + 1, cols = c + 1)
                    }
                }
            }
        }

        # Auto-width
        setColWidths(wb, sheet_name, cols = 1:(dims$cols + 1), widths = "auto")

        # Add legend sheet
        if (p == experiment$n_plates) {
            addWorksheet(wb, "Legend")
            legend_data <- data.frame(
                Treatment = all_treatments,
                Color = treatment_colors[all_treatments],
                stringsAsFactors = FALSE
            )
            writeData(wb, "Legend", legend_data, startRow = 1)
            for (i in seq_len(nrow(legend_data))) {
                style <- createStyle(fgFill = legend_data$Color[i])
                addStyle(wb, "Legend", style, rows = i + 1, cols = 2)
            }
        }
    }

    xlsx_path <- file.path(output_dir, "plate_layout.xlsx")
    saveWorkbook(wb, xlsx_path, overwrite = TRUE)

    # Strip dangling drawing/printer/VML relationship references that openxlsx
    # 4.2.x writes (but never fulfills) when addStyle() is used with cell fills.
    # These break strict OOXML readers (e.g. openpyxl) while leaving cell data
    # and fill styles intact. Safe no-op if the file is missing.
    tryCatch(.fix_xlsx_dangling_rels(xlsx_path),
             error = function(e) {
                 cat("   (note: xlsx rel cleanup skipped:", conditionMessage(e), ")\n")
             })

    # FIX 4: verify the workbook actually landed on disk and is non-trivial.
    # A valid .xlsx (zip container) is always well over 1 KB; a 0-byte or tiny
    # file means saveWorkbook silently failed.
    if (!file.exists(xlsx_path)) {
        stop("Excel file was not written: ", xlsx_path)
    }
    fsize <- file.info(xlsx_path)$size
    if (is.na(fsize) || fsize <= 1000) {
        stop(sprintf("Excel file is empty/too small (%s bytes): %s",
                     ifelse(is.na(fsize), "NA", fsize), xlsx_path))
    }
    cat("   Saved:", xlsx_path, sprintf("(%d bytes, verified)\n", fsize))
    invisible(xlsx_path)
}

# --- Quality report ---
.write_quality_report <- function(layout, experiment, output_dir, export_status = NULL) {
    quality <- layout$quality
    plate_data <- layout$plate_data

    assigned <- plate_data[!is.na(plate_data$sample_id), ]

    # FIX 3: read the DECLARED replicate vocabulary (single source of truth).
    n_bio <- if (!is.null(experiment$n_biological)) experiment$n_biological
             else if (experiment$n_plates > 1) experiment$n_plates else 1L
    n_tech <- if (!is.null(experiment$n_technical)) experiment$n_technical
              else experiment$n_replicates

    lines <- c(
        paste("=== Plate Layout Quality Report ==="),
        paste("Generated:", Sys.time()),
        "",
        paste("Experiment:", experiment$name),
        paste("Plate format:", experiment$plate_format, "-well"),
        paste("Method:", layout$method),
        paste("Edge strategy:", experiment$edge_strategy),
        paste("Seed:", layout$seed),
        "",
        "--- Replication Design ---",
        # Report n_biological/n_technical with their provenance source tag so
        # an inferred value can never be presented as declared. The source is
        # read from the experiment object (set by define_experiment /
        # load_example_experiment).
        sprintf("Biological replicates (independent preps): %d  %s", n_bio,
                .source_tag(experiment$n_biological_source)),
        sprintf("Technical replicates (within-plate):       %d  %s", n_tech,
                .source_tag(experiment$n_technical_source)),
        sprintf("Plates:                                    %d", experiment$n_plates),
        if (!is.null(experiment$normalization) &&
            identical(experiment$normalization, "ratiometric"))
            sprintf("Normalization:                             ratiometric (measurands: %s; references: %s)",
                    paste(experiment$measurands, collapse = ", "),
                    paste(experiment$reference_measurands, collapse = ", "))
        else NULL,
        if (!is.null(experiment$interplate_calibrator) && experiment$n_plates > 1)
            sprintf("Inter-plate calibrator:                    %s (on every plate)",
                    experiment$interplate_calibrator)
        else NULL,
        "",
        "--- Design Parameter Provenance ---",
        if (!is.null(experiment$design_provenance))
            .render_provenance_lines(experiment$design_provenance)
        else "  (no provenance table available — define_experiment() did not record one)",
        "",
        "--- Quality Scores ---",
        sprintf("Overall score:        %.0f%% %s",
                quality$overall_score * 100,
                if (quality$overall_score >= 0.8) "(GOOD)" else "(NEEDS REVIEW)"),
        sprintf("Spatial balance:      %.0f%%", quality$spatial_score * 100),
        sprintf("Control distribution: %.0f%%", quality$control_score * 100),
        sprintf("Edge protection:      %.0f%%", quality$edge_score * 100),
        "",
        "--- Well Counts ---",
        sprintf("Total wells:          %d", nrow(plate_data)),
        sprintf("Sample wells:         %d", sum(assigned$sample_type == "sample", na.rm = TRUE)),
        # FIX 1: surface inter-plate calibrator wells when present.
        if (any(assigned$sample_type == "calibrator", na.rm = TRUE))
            sprintf("Calibrator wells:     %d", sum(assigned$sample_type == "calibrator", na.rm = TRUE))
        else NULL,
        sprintf("Positive controls:    %d", sum(assigned$sample_type == "positive", na.rm = TRUE)),
        sprintf("Negative controls:    %d", sum(assigned$sample_type == "negative", na.rm = TRUE)),
        sprintf("Blanks:               %d", sum(assigned$sample_type == "blank", na.rm = TRUE)),
        "",
        "--- Well Census (edge vs interior) ---",
        if (!is.null(layout$well_census)) {
            c <- layout$well_census
            sprintf("Edge wells (total):       %d", c$edge_total)
        } else NULL,
        if (!is.null(layout$well_census))
            sprintf("  holding controls:       %d", layout$well_census$edge_assigned)
        else NULL,
        if (!is.null(layout$well_census))
            sprintf("  reserved, unassigned:   %d  <- edge-effect protection",
                    layout$well_census$edge_reserved_unassigned)
        else NULL,
        if (!is.null(layout$well_census))
            sprintf("  empty (edge buffer):     %d", layout$well_census$edge_empty)
        else NULL,
        if (!is.null(layout$well_census))
            sprintf("Interior wells (total):   %d", layout$well_census$interior_total)
        else NULL,
        if (!is.null(layout$well_census))
            sprintf("  assigned:                %d", layout$well_census$interior_assigned)
        else NULL,
        if (!is.null(layout$well_census))
            sprintf("  unused:                  %d  <- NOT edge protection",
                    layout$well_census$interior_unused)
        else NULL,
        if (!is.null(layout$well_census))
            sprintf("Plate utilization:        %.0f%% (%d of %d wells assigned)",
                    layout$well_census$utilization * 100,
                    layout$well_census$n_assigned, layout$well_census$total_wells)
        else
            sprintf("Plate utilization:        %.0f%% (%d of %d wells assigned)",
                    (nrow(assigned) / nrow(plate_data)) * 100, nrow(assigned), nrow(plate_data)),
        "",
        "--- Treatments ---"
    )

    trt_counts <- table(assigned$treatment[assigned$sample_type == "sample"])
    for (trt in names(trt_counts)) {
        lines <- c(lines, sprintf("  %-25s %d wells", trt, trt_counts[trt]))
    }

    lines <- c(lines, "",
        "--- Recommendations ---")

    if (quality$spatial_score < 0.7) {
        lines <- c(lines,
            "  WARNING: Low spatial balance. Consider using 'osat_spatial' method",
            "  or increasing max_iter for better optimization.")
    }
    if (quality$control_score < 1) {
        lines <- c(lines,
            "  WARNING: Controls not distributed across all quadrants.",
            "  Add more controls or adjust edge_strategy.")
    }
    if (quality$overall_score >= 0.8) {
        lines <- c(lines, "  Layout quality is GOOD. Ready for use.")
    }

    # Edge strategy note: read from the single-source well census instead of
    # recomputing n_unassigned (which conflated edge-reserved and interior-
    # unused wells, producing the impossible '444 edge wells' claim). The
    # census separates edge protection from interior under-utilization.
    census <- layout$well_census
    if (!is.null(census) && experiment$edge_strategy %in% c("controls_only", "empty")) {
        lines <- c(lines,
            sprintf("  Edge wells reserved for edge-effect protection: %d of %d edge wells.",
                    census$wells_reserved_by_edge_strategy, census$edge_total),
            "  Outer wells have 10-30% higher evaporation rates that can confound treatment effects.",
            "  To use all wells, set edge_strategy='include' (lower protection).")
        # Under-utilization gate: if interior wells are unused beyond the edge
        # strategy, announce it instead of framing the shortfall as deliberate.
        if (isTRUE(census$under_utilized)) {
            lines <- c(lines,
                sprintf("  WARNING: %d interior wells (%.0f%% of the deck) are unused and are NOT",
                        census$interior_unused,
                        census$interior_unused / census$total_wells * 100),
                sprintf("  explained by the edge strategy. This design needs ~%d plates, not %d.",
                        census$suggested_n_plates, experiment$n_plates))
        }
    }

    # Power analysis section (if assessed via assess_layout_power())
    if (!is.null(layout$power_analysis)) {
        pa <- layout$power_analysis
        # FIX 2: label the biological-power number with its validity (a value
        # computed from a technical SD is not a valid biological-power estimate).
        bio_valid_tag <- if (isTRUE(pa$biological_power_valid)) "(biological SD)"
                         else if (!is.null(pa$biological_power_caveat)) "[INVALID/UNVERIFIED]"
                         else ""
        bio_power_str <- if (!is.null(pa$biological_power) && !is.na(pa$biological_power))
            sprintf("Biological power:     %.3f (plate-level, n=%d) %s",
                    pa$biological_power, pa$n_plates, bio_valid_tag)
        else NULL

        # FIX defect 1: headline technical power = the model the data will
        # actually be analysed with. For a dose-response series that is the
        # 1-df trend test across the ordered doses; the omnibus ANOVA is demoted
        # to a model-free lower bound (it discards the dose ordering). Every
        # other assay type is unchanged (t-test / omnibus ANOVA headline).
        hl_power <- if (!is.null(pa$headline_power)) pa$headline_power else pa$power
        hl_model <- if (!is.null(pa$headline_power_model)) pa$headline_power_model
                    else pa$parameters$test_type
        power_lines <- c(
            sprintf("Technical power:      %.3f (well-level, n=%d) %s",
                    hl_power, pa$min_n_per_group,
                    if (hl_power >= 0.8) "(ADEQUATE)" else "(UNDERPOWERED)"),
            sprintf("Headline model:       %s", hl_model))
        if (!is.null(pa$trend_power) && !is.na(pa$trend_power)) {
            power_lines <- c(power_lines,
                sprintf("Model-free lower bound (omnibus ANOVA): %.3f  [discards dose ordering]",
                        pa$omnibus_power),
                if (!is.null(pa$trend_assumptions))
                    strwrap(pa$trend_assumptions, width = 78, prefix = "  ", initial = "  ")
                else NULL)
        }

        lines <- c(lines, "",
            "--- Power Analysis ---",
            power_lines,
            bio_power_str,
            # FIX 2: when the effect came from delta/SD, show that basis.
            if (!is.null(pa$sd_type) && !is.na(pa$sd_type))
                sprintf("Effect basis:         delta=%.2f / %s SD=%.2f", pa$delta, pa$sd_type, pa$sd)
            else NULL,
            if (!is.null(pa$biological_power_caveat))
                paste("Biological power note:", pa$biological_power_caveat)
            else NULL,
            sprintf("Effect size:          %.2f (%s)",
                    pa$parameters$effect_size,
                    pa$parameters$effect_size_label),
            sprintf("Alpha:                %.3f", pa$parameters$alpha),
            sprintf("Min replicates/group: %d", pa$min_n_per_group),
            sprintf("Treatments:           %d", pa$n_treatments),
            "",
            "Per-treatment power:")
        for (i in seq_len(nrow(pa$per_treatment))) {
            lines <- c(lines, sprintf("  %-25s n=%-3d power=%.3f",
                        pa$per_treatment$treatment[i],
                        pa$per_treatment$n[i],
                        pa$per_treatment$power[i]))
        }
        lines <- c(lines, "", paste("Assessment:", pa$interpretation),
                   paste("Recommendation:", pa$recommendation))

        # FIX 3: design-aware pseudoreplication section using DECLARED counts.
        # Reports the declared n_biological/n_technical and, for multi-plate
        # designs that DISTRIBUTE biological reps across plates (ratiometric
        # split-by-sample), states which biological reps each plate holds
        # instead of mislabeling every plate as "biological n=1".
        ratiometric_split <- identical(experiment$normalization, "ratiometric") &&
                             experiment$n_plates > 1
        if (!is.null(pa$n_plates) && pa$n_plates > 1) {
            lines <- c(lines, "",
                "IMPORTANT - Replication & Pseudoreplication:",
                sprintf("  Declared design: n_biological=%d, n_technical=%d across %d plates.",
                        n_bio, n_tech, pa$n_plates))
            if (ratiometric_split) {
                # Distribute-by-sample: report the bio reps per plate from the data.
                lines <- c(lines,
                    "  Biological replicates are DISTRIBUTED across plates (split by whole sample);",
                    "  each plate holds a subset of the biological replicates, not a full replica.")
                # Derive which biological reps landed on each plate from sample_ids.
                samp_wells <- plate_data[!is.na(plate_data$sample_type) &
                                         plate_data$sample_type == "sample", ]
                bio_idx <- suppressWarnings(as.integer(sub(".*_bio([0-9]+)_.*", "\\1",
                                                           samp_wells$sample_id)))
                for (p in sort(unique(samp_wells$plate))) {
                    reps_here <- sort(unique(bio_idx[samp_wells$plate == p & !is.na(bio_idx)]))
                    if (length(reps_here) > 0) {
                        lines <- c(lines, sprintf(
                            "    Plate %d holds biological reps %s of the n_biological=%d design.",
                            p, paste(reps_here, collapse = ","), n_bio))
                    }
                }
                lines <- c(lines,
                    "  Analyze with n = number of biological replicates (NOT total wells).")
            } else {
                lines <- c(lines,
                    sprintf("  n=%d above is total wells per treatment across %d plates.",
                            pa$min_n_per_group, pa$n_plates),
                    sprintf("  Wells per plate per treatment: ~%d (technical replicates)",
                            pa$wells_per_plate_per_group),
                    sprintf("  Biological n = %d ONLY IF each plate is an independent preparation.",
                            pa$n_plates),
                    "  Do NOT use total well count as n in publication statistics.")
            }
        } else {
            lines <- c(lines, "",
                "IMPORTANT - Replication & Pseudoreplication:",
                sprintf("  Declared design: n_biological=%d, n_technical=%d (single plate).",
                        n_bio, n_tech),
                if (n_bio <= 1)
                    "  Single plate: all wells are technical replicates (biological n=1)."
                else
                    sprintf("  Single plate holds technical reps; biological n=%d requires independent preparations.",
                            n_bio),
                "  For biological power, repeat the experiment on independent days/passages.")
        }

        if (!is.null(pa$required_bio_n)) {
            lines <- c(lines, "",
                "BIOLOGICAL REPLICATION PLAN:",
                if (!is.null(pa$reference_contrast))
                    sprintf("  Reference contrast: %s", pa$reference_contrast)
                else NULL,
                sprintf("  Power with 3 independent preparations: %.1f%%", pa$bio_power_at_3 * 100),
                sprintf("  Power with 5 independent preparations: %.1f%%", pa$bio_power_at_5 * 100),
                sprintf("  Required for 80%% biological power: %d independent preparations", pa$required_bio_n),
                "  Each preparation = new cell passage/batch on a separate day.",
                "  Average technical replicates within each plate, analyze with n = # preparations.")
        }

        # FIX 2: power vs assumed biological SD (only when delta/SD was given).
        if (!is.null(pa$sensitivity_over_sd)) {
            sd_tbl <- pa$sensitivity_over_sd
            lines <- c(lines, "",
                "POWER vs BIOLOGICAL SD (effect size depends on the assumed SD):")
            for (i in seq_len(nrow(sd_tbl))) {
                lines <- c(lines, sprintf("  biological SD=%.2f  ->  d=%.2f  ->  power=%.1f%%",
                            sd_tbl$biological_sd[i], sd_tbl$cohens_d[i], sd_tbl$power[i] * 100))
            }
            lines <- c(lines,
                "  (qPCR dCt biological SD prior ~0.5-1.0 Ct vs ~0.4 Ct technical floor)")
        }
    }

    # FIX 4: surface export status so an Excel failure is visible in the report,
    # not silently swallowed.
    if (!is.null(export_status)) {
        lines <- c(lines, "", "--- Export Status ---",
                   sprintf("  Excel (.xlsx): %s", export_status$excel))
        if (length(export_status$warnings) > 0) {
            lines <- c(lines, "  WARNINGS:")
            for (w in export_status$warnings) lines <- c(lines, paste("   -", w))
        } else {
            lines <- c(lines, "  No export warnings; all files written.")
        }
    }

    report_path <- file.path(output_dir, "layout_quality_report.txt")
    writeLines(lines, report_path)
    cat("   Saved:", report_path, "\n")
}

# --- Plater-format export ---
.export_plater_format <- function(layout, experiment, output_dir) {
    plate_data <- layout$plate_data
    dims <- experiment$plate_dims

    for (p in 1:experiment$n_plates) {
        plate_subset <- plate_data[plate_data$plate == p, ]

        # Plater format: first row is column headers, subsequent rows start with row label
        lines <- character(0)
        lines <- c(lines, paste(c("", dims$col_labels), collapse = ","))

        for (r in 1:dims$rows) {
            row_vals <- character(dims$cols)
            for (c in 1:dims$cols) {
                idx <- which(plate_subset$row == r & plate_subset$col == c)
                if (length(idx) == 1 && !is.na(plate_subset$treatment[idx])) {
                    row_vals[c] <- plate_subset$treatment[idx]
                } else {
                    row_vals[c] <- ""
                }
            }
            lines <- c(lines, paste(c(dims$row_labels[r], row_vals), collapse = ","))
        }

        suffix <- if (experiment$n_plates > 1) paste0("_plate", p) else ""
        plater_path <- file.path(output_dir, paste0("plate_plater_format", suffix, ".csv"))
        writeLines(lines, plater_path)
        cat("   Saved:", plater_path, "\n")
    }
}

cat("✓ export_layout.R loaded\n")
cat("  Use: export_all(layout, experiment, output_dir = 'layout_results')\n")
