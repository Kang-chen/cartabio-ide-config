# =============================================================================
# Microplate Layout Design - Example Experiment Definitions
# =============================================================================
# Provides pre-built experiment definitions for testing and demonstration.
# Users can also define experiments interactively with define_experiment().
# =============================================================================

# --- Package check ---
.check_packages <- function() {
    options(repos = c(CRAN = "https://cloud.r-project.org"))
    required <- c("designit", "ggplot2", "jsonlite", "pwr", "ggplate")
    missing <- required[!sapply(required, requireNamespace, quietly = TRUE)]
    if (length(missing) > 0) {
        cat("Installing required packages:", paste(missing, collapse = ", "), "\n")
        install.packages(missing)
    }
    # Optional packages: check availability but do NOT auto-install.
    # Downstream scripts (visualize_plate.R, export_layout.R) handle these
    # gracefully with requireNamespace() checks at point of use.
    optional <- c("openxlsx", "agricolae", "plater", "patchwork")
    available_opt <- sapply(optional, requireNamespace, quietly = TRUE)
    if (!all(available_opt)) {
        missing_opt <- optional[!available_opt]
        cat("  Optional packages not installed:", paste(missing_opt, collapse = ", "), "\n")
        cat("  Install with: install.packages(c('", paste(missing_opt, collapse = "', '"), "'))\n")
        cat("  Core functionality works without them.\n")
    }
}

# --- Plate format definitions ---
.plate_formats <- list(
    "96"  = list(rows = 8,  cols = 12, row_labels = LETTERS[1:8],  col_labels = 1:12),
    "384" = list(rows = 16, cols = 24, row_labels = LETTERS[1:16], col_labels = 1:24)
)

# --- Define experiment interactively ---
define_experiment <- function(plate_format = 96,
                              treatments = c("Treatment_A", "Treatment_B", "Vehicle"),
                              n_replicates = 3,
                              controls = list(positive = NULL, negative = NULL, blank = NULL),
                              n_controls = list(positive = 4, negative = 4, blank = 4),
                              edge_strategy = "controls_only",
                              n_plates = 1,
                              covariates = NULL,
                              reserved_wells = NULL,
                              experiment_name = "Experiment",
                              assay_type = "general") {

    plate_fmt <- .plate_formats[[as.character(plate_format)]]
    if (is.null(plate_fmt)) {
        stop("Unsupported plate format: ", plate_format,
             ". Supported: ", paste(names(.plate_formats), collapse = ", "))
    }

    total_wells <- plate_fmt$rows * plate_fmt$cols * n_plates

    # Calculate edge wells
    edge_wells <- .get_edge_wells(plate_fmt)

    # Calculate available wells based on edge strategy
    if (edge_strategy == "empty") {
        interior_wells <- total_wells - length(edge_wells) * n_plates
        usable_for_samples <- interior_wells
    } else if (edge_strategy == "controls_only") {
        interior_wells <- total_wells - length(edge_wells) * n_plates
        usable_for_samples <- interior_wells
    } else {
        usable_for_samples <- total_wells
    }

    # Calculate total controls needed
    total_controls <- sum(unlist(n_controls[!sapply(controls, is.null)]))

    # Calculate sample wells needed
    n_sample_wells <- length(treatments) * n_replicates * n_plates
    wells_needed <- n_sample_wells + total_controls * n_plates

    # Reserved wells
    n_reserved <- if (!is.null(reserved_wells)) length(reserved_wells) * n_plates else 0

    experiment <- list(
        name = experiment_name,
        assay_type = assay_type,
        plate_format = plate_format,
        plate_dims = plate_fmt,
        n_plates = n_plates,
        treatments = treatments,
        n_replicates = n_replicates,
        controls = controls,
        n_controls = n_controls,
        edge_strategy = edge_strategy,
        edge_wells = edge_wells,
        covariates = covariates,
        reserved_wells = reserved_wells,
        total_wells = total_wells,
        wells_needed = wells_needed,
        n_reserved = n_reserved
    )
    class(experiment) <- "plate_experiment"

    cat("✓ Experiment defined successfully!\n")
    cat("  Name:", experiment_name, "\n")
    cat("  Plate format:", plate_format, "-well (", plate_fmt$rows, "x", plate_fmt$cols, ")\n")
    cat("  Plates:", n_plates, "\n")
    cat("  Treatments:", paste(treatments, collapse = ", "), "\n")
    cat("  Replicates per treatment:", n_replicates, "\n")
    cat("  Edge strategy:", edge_strategy, "\n")
    ctrl_names <- names(controls)[!sapply(controls, is.null)]
    if (length(ctrl_names) > 0) {
        cat("  Controls:", paste(ctrl_names, "=", controls[ctrl_names], collapse = ", "), "\n")
    }
    cat("  Total wells needed:", wells_needed, "of", total_wells, "available\n")

    return(experiment)
}

# --- Get edge wells for a plate format ---
.get_edge_wells <- function(plate_fmt) {
    edge <- character(0)
    for (r in 1:plate_fmt$rows) {
        for (c in 1:plate_fmt$cols) {
            if (r == 1 || r == plate_fmt$rows || c == 1 || c == plate_fmt$cols) {
                well <- paste0(plate_fmt$row_labels[r], plate_fmt$col_labels[c])
                edge <- c(edge, well)
            }
        }
    }
    return(edge)
}

# --- Pre-built example experiments ---
load_example_experiment <- function(example = "dose_response_96") {
    .check_packages()

    examples <- list(
        "dose_response_96" = function() {
            define_experiment(
                plate_format = 96,
                treatments = c("Drug", "Vehicle"),
                n_replicates = 5,
                controls = list(positive = "Staurosporine", negative = "DMSO", blank = "Media"),
                n_controls = list(positive = 4, negative = 4, blank = 4),
                edge_strategy = "controls_only",
                experiment_name = "Dose-Response Assay",
                assay_type = "dose_response",
                n_plates = 6
            )
        },
        "qpcr_96" = function() {
            define_experiment(
                plate_format = 96,
                treatments = c("Gene_A_Treated", "Gene_A_Control",
                               "Gene_B_Treated", "Gene_B_Control",
                               "HK_GAPDH_Treated", "HK_GAPDH_Control",
                               "HK_ACTB_Treated", "HK_ACTB_Control"),
                n_replicates = 3,
                controls = list(positive = NULL, negative = "NTC", blank = NULL),
                n_controls = list(positive = 0, negative = 8, blank = 0),
                edge_strategy = "include",
                experiment_name = "qPCR Gene Expression",
                assay_type = "qpcr"
            )
        },
        "cell_viability_384" = function() {
            define_experiment(
                plate_format = 384,
                treatments = c(paste0("Compound_", 1:8, "_High"),
                               paste0("Compound_", 1:8, "_Mid"),
                               paste0("Compound_", 1:8, "_Low"),
                               "Vehicle"),
                n_replicates = 4,
                controls = list(positive = "Staurosporine", negative = "DMSO", blank = "Media"),
                n_controls = list(positive = 8, negative = 8, blank = 8),
                edge_strategy = "empty",
                experiment_name = "384-well Cell Viability Screen",
                assay_type = "cell_viability"
            )
        },
        "simple_96" = function() {
            define_experiment(
                plate_format = 96,
                treatments = c("Treatment", "Control"),
                n_replicates = 6,
                controls = list(positive = "Pos_Ctrl", negative = "Neg_Ctrl", blank = "Blank"),
                n_controls = list(positive = 3, negative = 3, blank = 3),
                edge_strategy = "controls_only",
                experiment_name = "Simple 2-Group Comparison",
                assay_type = "general"
            )
        }
    )

    if (!example %in% names(examples)) {
        cat("Available examples:\n")
        for (nm in names(examples)) cat("  -", nm, "\n")
        stop("Unknown example: ", example)
    }

    cat("Loading example experiment: ", example, "\n\n")
    experiment <- examples[[example]]()
    return(experiment)
}

cat("✓ load_example_experiment.R loaded\n")
cat("  Use: experiment <- load_example_experiment('dose_response_96')\n")
cat("  Or:  experiment <- define_experiment(...)\n")
cat("  Available examples: dose_response_96, qpcr_96, cell_viability_384, simple_96\n")
