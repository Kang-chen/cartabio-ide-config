# =============================================================================
# Load Example Data for Survival Analysis
# =============================================================================
# Provides three example datasets:
#   1. Rotterdam Breast Cancer - survival::rotterdam (no download, recommended)
#      2,982 patients, OS and RFS endpoints
#   2. TCGA BRCA (breast cancer) - Real-world clinical data via RTCGA.clinical
#      Requires download; errors with a named fallback when unavailable
#   3. NCCTG Lung - Built-in survival::lung dataset (no download needed)
# =============================================================================

options(repos = c(CRAN = "https://cloud.r-project.org"))

# --- Helper: ensure Bioconductor package installed ---
.ensure_bioc_package <- function(pkg) {
    if (!requireNamespace(pkg, quietly = TRUE)) {
        if (!requireNamespace("BiocManager", quietly = TRUE)) {
            install.packages("BiocManager")
        }
        cat("  Installing", pkg, "...\n")
        BiocManager::install(pkg, ask = FALSE, update = FALSE)
    }
}


# =============================================================================
# RFS Endpoint Derivation Helper
# =============================================================================
# Derives a recurrence-free survival (RFS) endpoint from recurrence and death
# columns, following the survival-package conventions documented in ?rotterdam.
#
# Liberal convention (the ?rotterdam example):
#   time  <- ifelse(recur == 1, rtime, dtime)
#   event <- pmax(recur, death)
#   Counts later deaths as RFS events even when they occur after recurrence
#   censoring.
#
# Conservative convention:
#   Additionally censors at rtime the subjects with recur == 0 & death == 1 &
#   dtime > rtime (documented as ~43 in rotterdam). These subjects died after
#   their recurrence follow-up ended; we cannot know whether they recurred.
#
# This helper NEVER exposes a pmin()-style construction. Using pmin(rtime,
# dtime) would move death events earlier than they occurred and matches
# neither survival-package convention.
# =============================================================================

derive_rfs_endpoint <- function(clinical, recur_time_col, recur_event_col,
                                death_time_col, death_event_col,
                                convention = c("liberal", "conservative")) {
    convention <- match.arg(convention)

    rtime  <- clinical[[recur_time_col]]
    recur  <- clinical[[recur_event_col]]
    dtime  <- clinical[[death_time_col]]
    death  <- clinical[[death_event_col]]

    # --- Liberal: the ?rotterdam example convention ---
    # time  <- ifelse(recur == 1, rtime, dtime)
    # event <- pmax(recur, death)
    lib_time  <- ifelse(recur == 1, rtime, dtime)
    lib_event <- pmax(recur, death)

    # --- Conservative: censor the ~43 subjects who died after recurrence FU ---
    # For recur == 0 & death == 1 & dtime > rtime: use rtime as the time and
    # censor (event = 0) instead of counting the later death.
    late_death <- (recur == 0 & death == 1 & dtime > rtime)
    late_death[is.na(late_death)] <- FALSE

    cons_time  <- ifelse(late_death, rtime, lib_time)
    cons_event <- ifelse(late_death, 0L, lib_event)

    # --- Count how many subjects differ between conventions ---
    n_affected <- sum(late_death)
    cat("  RFS endpoint convention:", convention, "\n")
    cat("  Subjects whose time differs between liberal and conservative:",
        n_affected, "\n")

    if (convention == "liberal") {
        out_time  <- lib_time
        out_event <- lib_event
    } else {
        out_time  <- cons_time
        out_event <- cons_event
    }

    out <- data.frame(
        time  = out_time,
        event = as.integer(out_event),
        stringsAsFactors = FALSE
    )

    # Carry convention metadata as attributes so the report prints them
    attr(out, "endpoint_convention") <- convention
    attr(out, "n_convention_affected") <- as.integer(n_affected)

    return(out)
}


# =============================================================================
# Option 0: Rotterdam Breast Cancer - survival::rotterdam (No Download)
# =============================================================================
# Source: survival::rotterdam — Royston & Altman (2013), BMC Med Res Methodol
# 2,982 primary breast cancers from the Rotterdam tumor bank
# Endpoints: Overall Survival (OS) and Recurrence-Free Survival (RFS)
# Covariates on native scale: age, meno, size, grade, nodes, pgr, er, hormon,
# chemo — NO log2 receptor transforms
# =============================================================================

load_rotterdam <- function(endpoint = c("OS", "RFS")) {
    endpoint <- match.arg(endpoint)
    cat("\n=== Loading Rotterdam Breast Cancer Data ===\n\n")

    library(survival)
    data("rotterdam", package = "survival", envir = environment())
    rt <- get("rotterdam", envir = environment())

    endpoint_code <- endpoint
    endpoint_label <- if (endpoint == "OS") {
        "Overall Survival (OS)"
    } else {
        "Recurrence-Free Survival (RFS)"
    }

    endpoint_convention <- NA_character_
    n_convention_affected <- NA_integer_

    if (endpoint == "OS") {
        # Overall survival: time to death, event = death
        time_years <- rt$dtime / 365.25
        event <- rt$death
    } else {
        # RFS: derive via the validated helper (liberal by default)
        rfs <- derive_rfs_endpoint(
            rt,
            recur_time_col  = "rtime",
            recur_event_col = "recur",
            death_time_col  = "dtime",
            death_event_col = "death",
            convention = "liberal"
        )
        time_years <- rfs$time / 365.25
        event <- rfs$event
        endpoint_convention <- attr(rfs, "endpoint_convention")
        n_convention_affected <- attr(rfs, "n_convention_affected")
    }

    # Build clinical data frame — covariates on native scale
    clinical <- data.frame(
        sample_id = paste0("ROT_", sprintf("%04d", rt$pid)),
        time_years = time_years,
        event = as.integer(event),
        age = rt$age,
        meno = factor(rt$meno, levels = c(0, 1),
                      labels = c("Premenopausal", "Postmenopausal")),
        size = rt$size,                          # ordered factor: <=20, 20-50, >50
        grade = factor(rt$grade, levels = c(2, 3),
                       labels = c("Grade 2", "Grade 3")),
        nodes = rt$nodes,
        pgr = rt$pgr,
        er = rt$er,
        hormon = factor(rt$hormon, levels = c(0, 1),
                        labels = c("No", "Yes")),
        chemo = factor(rt$chemo, levels = c(0, 1),
                       labels = c("No", "Yes")),
        stringsAsFactors = FALSE
    )

    # Remove rows with missing time or event
    clinical <- clinical[!is.na(clinical$time_years) &
                         !is.na(clinical$event) &
                         clinical$time_years > 0, ]

    result <- list(
        clinical = clinical,
        event_col = "event",
        time_col = "time_years",
        strata_col = "size",
        dataset_name = "Rotterdam Breast Cancer",
        description = paste0(
            "Rotterdam Breast Cancer - ", nrow(clinical), " patients, ",
            sum(clinical$event), " events (",
            round(100 * mean(clinical$event)), "% event rate), ",
            endpoint_label
        ),
        endpoint_code = endpoint_code,
        endpoint_label = endpoint_label,
        endpoint_convention = endpoint_convention,
        n_convention_affected = n_convention_affected,
        report_context = list(
            disease = "Primary Breast Cancer",
            source = "Rotterdam tumor bank (survival::rotterdam)",
            citation = "Royston P, Altman DG. BMC Med Res Methodol 2013;13:33",
            endpoints = endpoint_label,
            covariates = c("age", "meno", "size", "grade", "nodes", "pgr",
                          "er", "hormon", "chemo"),
            notes = paste(
                "Covariates on native scale (no log2 receptor transforms).",
                "Grade has only levels 2 and 3 in this dataset."
            )
        )
    )

    cat("✓ Rotterdam data loaded successfully!\n")
    cat("  Endpoint:", endpoint_label, "\n")
    cat("  Samples:", nrow(clinical), "\n")
    cat("  Events:", sum(clinical$event), "(", round(100 * mean(clinical$event)),
        "% event rate)\n")
    cat("  Median observation time:", round(median(clinical$time_years), 1), "years\n")
    cat("  Max observation time:", round(max(clinical$time_years), 1), "years\n")

    return(result)
}

# =============================================================================
# Option 1: TCGA Breast Cancer (BRCA) - Real-World Clinical Data
# =============================================================================
# Source: The Cancer Genome Atlas via RTCGA.clinical
# ~1,100 patients with overall survival, stage, ER/PR/HER2 status
# Clear survival differences by stage and receptor status
# =============================================================================

load_tcga_brca <- function(data_dir = "data", allow_download = FALSE) {
    cat("\n=== Loading TCGA BRCA Survival Data ===\n\n")

    if (!dir.exists(data_dir)) dir.create(data_dir, recursive = TRUE)

    # Check cache
    cache_file <- file.path(data_dir, "tcga_brca_survival.rds")
    if (file.exists(cache_file)) {
        cat("  Loading from cache...\n")
        data <- readRDS(cache_file)
        cat("✓ TCGA BRCA data loaded successfully!\n")
        cat("  Samples:", nrow(data$clinical), "\n")
        cat("  Events:", sum(data$clinical$event, na.rm = TRUE), "\n")
        return(data)
    }

    # When RTCGA.clinical is absent and downloads are not allowed, stop
    # immediately with a named fallback — do NOT trigger an unattended
    # Bioconductor install that agents route around to cBioPortal/METABRIC.
    if (!requireNamespace("RTCGA.clinical", quietly = TRUE) && !allow_download) {
        stop(
            "RTCGA.clinical is not installed and downloads are disabled.\n",
            "Use the packaged breast-cancer dataset instead:\n",
            "  data <- load_example_data(dataset = \"rotterdam\")\n",
            "Rotterdam Breast Cancer: n=2982, ships with the survival package, ",
            "no download required."
        )
    }

    # Only reach .ensure_bioc_package when download is explicitly allowed
    .ensure_bioc_package("RTCGA.clinical")

    cat("  Extracting BRCA clinical data...\n")
    library(RTCGA.clinical)
    data("BRCA.clinical", package = "RTCGA.clinical", envir = environment())

    raw <- BRCA.clinical

    # --- Extract and clean survival variables ---

    # Vital status: alive=0, dead=1
    vital <- tolower(trimws(raw$patient.vital_status))
    event <- ifelse(vital == "dead", 1L, 0L)

    # Survival time in days -> years
    days_death <- suppressWarnings(as.numeric(raw$patient.days_to_death))
    days_fu <- suppressWarnings(as.numeric(raw$patient.days_to_last_followup))
    time_days <- ifelse(!is.na(days_death) & event == 1, days_death, days_fu)
    time_years <- time_days / 365.25

    # Age at diagnosis
    age <- suppressWarnings(
        as.numeric(raw$patient.age_at_initial_pathologic_diagnosis)
    )

    # Pathologic stage (simplify to I-IV)
    stage_raw <- tolower(trimws(raw$patient.stage_event.pathologic_stage))
    stage <- case_when_stage(stage_raw)

    # Receptor status
    er_status <- clean_receptor(
        raw$patient.breast_carcinoma_estrogen_receptor_status
    )
    pr_status <- clean_receptor(
        raw$patient.breast_carcinoma_progesterone_receptor_status
    )
    her2_status <- clean_receptor(
        raw$patient.lab_proc_her2_neu_immunohistochemistry_receptor_status
    )

    # Molecular subtype (from receptor status)
    mol_subtype <- ifelse(
        er_status == "Negative" & pr_status == "Negative" & her2_status == "Negative",
        "Triple Negative",
        ifelse(
            her2_status == "Positive" & (er_status == "Negative" & pr_status == "Negative"),
            "HER2+",
            ifelse(
                er_status == "Positive" | pr_status == "Positive",
                ifelse(her2_status == "Positive", "HR+/HER2+", "HR+/HER2-"),
                NA_character_
            )
        )
    )

    # Build clinical data frame
    clinical <- data.frame(
        sample_id = raw$patient.bcr_patient_barcode,
        time_years = time_years,
        event = event,
        age = age,
        age_group = cut(age, breaks = c(0, 50, 65, Inf),
                        labels = c("<50", "50-65", ">65")),
        stage = stage,
        er_status = er_status,
        pr_status = pr_status,
        her2_status = her2_status,
        mol_subtype = mol_subtype,
        stringsAsFactors = FALSE
    )

    # Remove rows with missing time or event
    clinical <- clinical[!is.na(clinical$time_years) &
                         !is.na(clinical$event) &
                         clinical$time_years > 0, ]

    # Result
    result <- list(
        clinical = clinical,
        event_col = "event",
        time_col = "time_years",
        strata_col = "mol_subtype",
        dataset_name = "TCGA BRCA",
        description = paste0(
            "TCGA Breast Cancer (BRCA) - ", nrow(clinical), " patients, ",
            sum(clinical$event), " events (",
            round(100 * mean(clinical$event)), "% event rate)"
        ),
        report_context = list(
            disease = "Breast Invasive Carcinoma",
            source = "The Cancer Genome Atlas (TCGA)",
            citation = "Cancer Genome Atlas Network. Nature 2012;490:61-70",
            endpoints = "Overall survival (OS)",
            covariates = c("age", "stage", "ER status", "PR status",
                          "HER2 status", "molecular subtype"),
            notes = paste(
                "Molecular subtypes defined by receptor status:",
                "HR+/HER2- (Luminal A-like), HR+/HER2+ (Luminal B-like),",
                "HER2+ (HER2-enriched), Triple Negative (Basal-like)"
            )
        )
    )

    # Cache
    saveRDS(result, cache_file)
    cat("  Cached to:", cache_file, "\n")

    cat("✓ TCGA BRCA data loaded successfully!\n")
    cat("  Samples:", nrow(clinical), "\n")
    cat("  Events:", sum(clinical$event), "(", round(100 * mean(clinical$event)),
        "% event rate)\n")
    cat("  Median observation time:", round(median(clinical$time_years), 1), "years\n")
    cat("  Max observation time:", round(max(clinical$time_years), 1), "years\n")

    return(result)
}


# =============================================================================
# Option 2: NCCTG Lung Cancer - Built-in Dataset (No Download)
# =============================================================================
# Source: North Central Cancer Treatment Group (Loprinzi et al., 1994)
# 228 patients with advanced lung cancer
# Clear survival differences by sex and ECOG performance status
# =============================================================================

load_lung_example <- function() {
    cat("\n=== Loading NCCTG Lung Cancer Data ===\n\n")

    library(survival)
    lung_data <- survival::lung

    clinical <- data.frame(
        sample_id = paste0("LUNG_", sprintf("%03d", 1:nrow(lung_data))),
        time_years = lung_data$time / 365.25,
        event = lung_data$status - 1,  # survival::lung uses 1=censored, 2=dead
        age = lung_data$age,
        age_group = cut(lung_data$age, breaks = c(0, 60, 70, Inf),
                        labels = c("<60", "60-70", ">70")),
        sex = factor(lung_data$sex, levels = 1:2, labels = c("Male", "Female")),
        ecog_ps = factor(lung_data$ph.ecog, levels = 0:4,
                        labels = c("Asymptomatic", "Symptomatic-ambulatory",
                                   "In bed <50%", "In bed >50%", "Bedridden")),
        karnofsky_physician = lung_data$ph.karno,
        karnofsky_patient = lung_data$pat.karno,
        calories = lung_data$meal.cal,
        weight_loss = lung_data$wt.loss,
        stringsAsFactors = FALSE
    )

    # Remove rows with missing time or event
    clinical <- clinical[!is.na(clinical$time_years) &
                         !is.na(clinical$event) &
                         clinical$time_years > 0, ]

    result <- list(
        clinical = clinical,
        event_col = "event",
        time_col = "time_years",
        strata_col = "sex",
        dataset_name = "NCCTG Lung",
        description = paste0(
            "NCCTG Lung Cancer - ", nrow(clinical), " patients, ",
            sum(clinical$event), " events (",
            round(100 * mean(clinical$event)), "% event rate)"
        ),
        report_context = list(
            disease = "Advanced Lung Cancer",
            source = "North Central Cancer Treatment Group (NCCTG)",
            citation = "Loprinzi CL, et al. J Clin Oncol. 1994;12:601-607",
            endpoints = "Overall survival (OS)",
            covariates = c("age", "sex", "ECOG performance status",
                          "Karnofsky score"),
            notes = "Performance status is a strong prognostic factor in advanced lung cancer."
        )
    )

    cat("✓ NCCTG Lung data loaded successfully!\n")
    cat("  Samples:", nrow(clinical), "\n")
    cat("  Events:", sum(clinical$event), "(", round(100 * mean(clinical$event)),
        "% event rate)\n")
    cat("  Median observation time:", round(median(clinical$time_years), 1), "years\n")

    return(result)
}


# =============================================================================
# Router: Load Example Data
# =============================================================================

load_example_data <- function(dataset = "rotterdam", data_dir = "data",
                              endpoint = "OS", allow_download = FALSE) {
    switch(dataset,
        "rotterdam" = load_rotterdam(endpoint = endpoint),
        "tcga_brca" = load_tcga_brca(data_dir, allow_download = allow_download),
        "lung" = load_lung_example(),
        stop("Unknown dataset: '", dataset,
             "'. Use 'rotterdam', 'tcga_brca', or 'lung'.")
    )
}


# =============================================================================
# Available Endpoints for a Dataset
# =============================================================================
# Returns the time-to-event endpoints a cohort actually carries, so the workflow
# can analyse EVERY endpoint rather than silently defaulting to one. The set is
# derived from what the underlying data provides: Rotterdam ships both a
# death/overall-survival endpoint and a recurrence/recurrence-free-survival
# endpoint, whereas the TCGA and lung example cohorts carry overall survival
# only. For a user-supplied cohort, the endpoints are whatever time/event pairs
# the analyst passes to load_user_data() (typically one).
#
# The documented workflow loops over available_endpoints(dataset) and analyses
# each one; it must not analyse a subset without stating which it ran and why.
# =============================================================================

available_endpoints <- function(dataset = "rotterdam") {
    switch(dataset,
        "rotterdam" = c("OS", "RFS"),
        "tcga_brca" = c("OS"),
        "lung"      = c("OS"),
        stop("Unknown dataset: '", dataset,
             "'. Use 'rotterdam', 'tcga_brca', or 'lung'.")
    )
}


# =============================================================================
# List Available Example Datasets
# =============================================================================
# Prints a table of the three packaged datasets with live availability status,
# disease, endpoint, and N — so the choice is made from a printed table rather
# than from environment probing.
# =============================================================================

list_example_datasets <- function() {
    cat("\n=== Available Example Datasets ===\n\n")

    datasets <- list(
        list(name = "rotterdam", disease = "Breast Cancer",
             endpoint = "OS / RFS", n = 2982,
             available = requireNamespace("survival", quietly = TRUE),
             download = FALSE,
             note = "Recommended — ships with survival package"),
        list(name = "tcga_brca", disease = "Breast Cancer (TCGA)",
             endpoint = "OS", n = 1100,
             available = requireNamespace("RTCGA.clinical", quietly = TRUE),
             download = TRUE,
             note = "Requires RTCGA.clinical (~50MB)"),
        list(name = "lung", disease = "Lung Cancer (NCCTG)",
             endpoint = "OS", n = 228,
             available = requireNamespace("survival", quietly = TRUE),
             download = FALSE,
             note = "Quick demo — ships with survival package")
    )

    cat(sprintf("%-12s  %-22s  %-10s  %6s  %-10s  %s\n",
        "Dataset", "Disease", "Endpoint", "N", "Available", "Notes"))
    cat(paste(rep("-", 80), collapse = ""), "\n")
    for (d in datasets) {
        cat(sprintf("%-12s  %-22s  %-10s  %6d  %-10s  %s\n",
            d$name, d$disease, d$endpoint, d$n,
            if (d$available) "Yes" else "No",
            d$note))
    }
    cat("\n")
    cat("Load with: load_example_data(dataset = \"<name>\")\n")
    cat("Rotterdam endpoint: load_example_data(dataset = \"rotterdam\", endpoint = \"RFS\")\n\n")

    invisible(datasets)
}


# =============================================================================
# Load User Data
# =============================================================================

load_user_data <- function(file_path, time_col, event_col,
                           strata_col = NULL, sep = ",") {
    cat("\n=== Loading User Clinical Data ===\n\n")

    if (!file.exists(file_path)) {
        stop("File not found: ", file_path)
    }

    clinical <- read.csv(file_path, stringsAsFactors = FALSE, sep = sep)
    cat("  Loaded:", nrow(clinical), "rows x", ncol(clinical), "columns\n")

    # Validate required columns
    if (!time_col %in% colnames(clinical))
        stop("Time column '", time_col, "' not found. Available: ",
             paste(colnames(clinical), collapse = ", "))
    if (!event_col %in% colnames(clinical))
        stop("Event column '", event_col, "' not found. Available: ",
             paste(colnames(clinical), collapse = ", "))

    # Validate data types
    clinical[[time_col]] <- as.numeric(clinical[[time_col]])
    clinical[[event_col]] <- as.integer(clinical[[event_col]])

    if (any(clinical[[time_col]] < 0, na.rm = TRUE))
        warning("Negative survival times detected - check time column encoding.")
    if (!all(clinical[[event_col]] %in% c(0, 1), na.rm = TRUE))
        warning("Event column should be binary (0/1). Found values: ",
                paste(unique(clinical[[event_col]]), collapse = ", "))

    # Add sample_id if missing
    if (!"sample_id" %in% colnames(clinical)) {
        clinical$sample_id <- paste0("S_", seq_len(nrow(clinical)))
    }

    result <- list(
        clinical = clinical,
        event_col = event_col,
        time_col = time_col,
        strata_col = strata_col,
        dataset_name = basename(file_path),
        description = paste0(
            basename(file_path), " - ", nrow(clinical), " patients, ",
            sum(clinical[[event_col]], na.rm = TRUE), " events"
        ),
        report_context = list(
            disease = "User-provided clinical data",
            source = file_path,
            endpoints = paste("User-defined:", event_col),
            covariates = setdiff(colnames(clinical), c(time_col, event_col, "sample_id"))
        )
    )

    cat("✓ User data loaded successfully!\n")
    cat("  Samples:", nrow(clinical), "\n")
    cat("  Events:", sum(clinical[[event_col]], na.rm = TRUE), "\n")

    return(result)
}


# =============================================================================
# Helpers
# =============================================================================

case_when_stage <- function(stage_raw) {
    stage <- rep(NA_character_, length(stage_raw))
    stage[grepl("stage iv", stage_raw)] <- "Stage IV"
    stage[grepl("stage iii", stage_raw) & !grepl("stage iv", stage_raw)] <- "Stage III"
    stage[grepl("stage ii", stage_raw) & !grepl("stage iii|stage iv", stage_raw)] <- "Stage II"
    stage[grepl("stage i", stage_raw) & !grepl("stage ii|stage iii|stage iv", stage_raw)] <- "Stage I"
    return(stage)
}

clean_receptor <- function(x) {
    x <- tolower(trimws(x))
    result <- rep(NA_character_, length(x))
    result[x %in% c("positive")] <- "Positive"
    result[x %in% c("negative")] <- "Negative"
    result[x %in% c("indeterminate", "equivocal")] <- "Equivocal"
    return(result)
}
