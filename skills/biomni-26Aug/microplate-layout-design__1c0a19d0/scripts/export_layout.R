# =============================================================================
# Microplate Layout Design - Export Results
# =============================================================================
# Exports plate layouts in multiple formats for lab use and downstream analysis.
# =============================================================================

suppressPackageStartupMessages({
    library(jsonlite)
})

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

    # 1. Tidy CSV (one row per well)
    cat("1. Tidy CSV (plate_layout.csv)...\n")
    tidy_df <- plate_data[, c("plate", "well", "row_label", "col_label",
                               "row", "col", "is_edge", "well_role",
                               "sample_id", "treatment", "replicate", "sample_type")]
    write.csv(tidy_df, file.path(output_dir, "plate_layout.csv"), row.names = FALSE)
    cat("   Saved:", file.path(output_dir, "plate_layout.csv"), "\n")

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
    if (requireNamespace("openxlsx", quietly = TRUE)) {
        tryCatch({
            .export_excel(layout, experiment, output_dir)
        }, error = function(e) {
            cat("   Excel export failed:", conditionMessage(e), "\n")
            cat("   (CSV exports are available as alternative)\n")
        })
    } else {
        cat("   (openxlsx not available - skipping Excel export)\n")
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
        controls = experiment$controls,
        n_controls = experiment$n_controls,
        edge_strategy = experiment$edge_strategy,
        method = layout$method,
        seed = layout$seed,
        quality_scores = layout$quality,
        power_analysis = if (!is.null(layout$power_analysis))
            layout$power_analysis[c("power", "biological_power", "required_bio_n",
                                     "bio_power_at_3", "bio_power_at_5",
                                     "parameters", "interpretation",
                                     "recommendation", "biological_recommendation")] else NULL,
        timestamp = Sys.time()
    )
    write_json(params, file.path(output_dir, "experiment_parameters.json"),
               pretty = TRUE, auto_unbox = TRUE)
    cat("   Saved:", file.path(output_dir, "experiment_parameters.json"), "\n")

    # 6. Quality report (text)
    cat("6. Quality report (layout_quality_report.txt)...\n")
    .write_quality_report(layout, experiment, output_dir)

    # 7. Plater-format CSV (if plater available)
    cat("7. Plater-format CSV...\n")
    if (requireNamespace("plater", quietly = TRUE)) {
        tryCatch({
            .export_plater_format(layout, experiment, output_dir)
        }, error = function(e) {
            cat("   Plater export failed:", conditionMessage(e), "\n")
        })
    } else {
        cat("   (plater not available - grid CSV serves same purpose)\n")
    }

    cat("\n=== Export Complete ===\n")
    cat("All files saved to:", output_dir, "\n")

    invisible(output_dir)
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
    cat("   Saved:", xlsx_path, "\n")
}

# --- Quality report ---
.write_quality_report <- function(layout, experiment, output_dir) {
    quality <- layout$quality
    plate_data <- layout$plate_data

    assigned <- plate_data[!is.na(plate_data$sample_id), ]

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
        sprintf("Positive controls:    %d", sum(assigned$sample_type == "positive", na.rm = TRUE)),
        sprintf("Negative controls:    %d", sum(assigned$sample_type == "negative", na.rm = TRUE)),
        sprintf("Blanks:               %d", sum(assigned$sample_type == "blank", na.rm = TRUE)),
        sprintf("Empty (edge buffer):  %d", sum(plate_data$well_role == "empty")),
        sprintf("Unassigned:           %d",
                sum(is.na(plate_data$sample_id) & plate_data$well_role != "empty")),
        sprintf("Plate utilization:    %.0f%% (%d of %d wells assigned)",
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

    # Edge strategy utilization note
    n_unassigned <- sum(is.na(plate_data$sample_id) & plate_data$well_role != "empty")
    if (experiment$edge_strategy %in% c("controls_only", "empty") && n_unassigned > 0) {
        utilization_pct <- round((nrow(assigned) / nrow(plate_data)) * 100)
        lines <- c(lines,
            sprintf("  NOTE: %d edge wells are intentionally unassigned for edge effect protection.", n_unassigned),
            sprintf("  Plate utilization is %d%%. This is a deliberate tradeoff — outer wells", utilization_pct),
            "  have 10-30% higher evaporation rates that can confound treatment effects.",
            "  To use all wells, set edge_strategy='include' (lower protection).")
    }

    # Power analysis section (if assessed via assess_layout_power())
    if (!is.null(layout$power_analysis)) {
        pa <- layout$power_analysis
        bio_power_str <- if (!is.null(pa$biological_power) && !is.na(pa$biological_power))
            sprintf("Biological power:     %.3f (plate-level, n=%d)", pa$biological_power, pa$n_plates)
        else NULL

        lines <- c(lines, "",
            "--- Power Analysis ---",
            sprintf("Technical power:      %.3f (well-level, n=%d) %s",
                    pa$power, pa$min_n_per_group,
                    if (pa$power >= 0.8) "(ADEQUATE)" else "(UNDERPOWERED)"),
            bio_power_str,
            sprintf("Effect size:          %.2f (%s)",
                    pa$parameters$effect_size,
                    pa$parameters$effect_size_label),
            sprintf("Test type:            %s", pa$parameters$test_type),
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

        # Pseudoreplication warning
        if (!is.null(pa$n_plates) && pa$n_plates > 1) {
            lines <- c(lines, "",
                "IMPORTANT - Pseudoreplication:",
                sprintf("  n=%d above is total wells per treatment across %d plates.",
                        pa$min_n_per_group, pa$n_plates),
                sprintf("  Wells per plate per treatment: ~%d (technical replicates)",
                        pa$wells_per_plate_per_group),
                sprintf("  Biological n = %d (if each plate is an independent preparation)",
                        pa$n_plates),
                "  Do NOT use total well count as n in publication statistics.")
        } else {
            lines <- c(lines, "",
                "IMPORTANT - Pseudoreplication:",
                "  Single-plate design. All wells are technical replicates (biological n=1).",
                "  For biological power, repeat the experiment on independent days/passages.")
        }

        if (!is.null(pa$required_bio_n)) {
            lines <- c(lines, "",
                "BIOLOGICAL REPLICATION PLAN:",
                sprintf("  Power with 3 independent preparations: %.1f%%", pa$bio_power_at_3 * 100),
                sprintf("  Power with 5 independent preparations: %.1f%%", pa$bio_power_at_5 * 100),
                sprintf("  Required for 80%% biological power: %d independent preparations", pa$required_bio_n),
                "  Each preparation = new cell passage/batch on a separate day.",
                "  Average technical replicates within each plate, analyze with n = # preparations.")
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
