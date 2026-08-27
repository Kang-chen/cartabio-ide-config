#!/usr/bin/env Rscript
# =====================================================================================
# 01_load_and_qc.R  --  Load cytometry data, detect modality, transform (+compensate),
#                       and run FIRST-CLASS, modality-aware QC gating before clustering.
#
# Why QC is first-class: benchmark data (HDCytoData) ships pre-cleaned, but real customer
# FCS does NOT. Debris, doublets, dead cells and beads that leak into clustering create junk
# clusters. This script gates them out (per-stage counts logged) and records exactly which
# transform/compensation was applied. Provenance is READ from metadata, never inferred.
#
# Usage (all args optional; defaults reproduce the Levine_32dim worked example):
#   Rscript 01_load_and_qc.R \
#     --input <dir_of_fcs | file.fcs | HDCytoData:Levine_32dim> \
#     --metadata metadata.csv \        # file,sample_id,group,batch,patient (multi-sample FCS)
#     --outdir /mnt/results/cyto_run \
#     --modality auto \                # auto | cytof | flow   (override detection)
#     --spectral-state auto \          # auto | raw | unmixed  (override spectral-unmixing detection)
#     --transform auto \               # auto | arcsinh | logicle  (override)
#     --cofactor auto \                # auto | <number>  (arcsinh cofactor; CyTOF default 5)
#     --compensate auto \              # auto | on | off   (flow spillover)
#     --label-col population_id \      # per-cell manual-gate label column (enables benchmark)
#     --qc on \                        # on | off  (off only for known pre-cleaned data)
#     --singlet-mad-k 4 \              # PERMISSIVE singlet band (MADs); larger = gentler (default 4)
#     --debris-pct 0.02 \             # gentle FSC/SSC debris floor percentile (default 0.02)
#     --overgate-alarm 0.30 \         # loud alarm if scatter+singlet jointly remove > this fraction
#     --gate-review propose \          # propose (default)|apply|auto. propose = PASS 1: write
#                                      #   template+figures then STOP for human edit (resume with
#                                      #   --gate-review apply --thresholds <edited.csv>). apply =
#                                      #   PASS 2. auto = proceed unattended (escape hatch).
#     --seed 1234
#
# TWO-PASS DEFAULT: a normal run (--qc on, builtin engine, no --thresholds)
# writes the gate template + per-gate figures and STOPS before the SCE for human review. Edit
# gating_thresholds_template.csv, then rerun with --gate-review apply --thresholds <edited.csv>.
# Non-stopping escape hatches: --gate-review auto (proceed unattended), --qc off, a supplied
# --thresholds, and the openCyto backend (no propose/apply loop; logs a note and proceeds).
#
# GATING PRINCIPLE (hard-won): scatter/singlet gates default to PERMISSIVE. Over-gating removes
# REAL cells silently and biases every downstream population/readout -- it does NOT fail loudly.
# If automated results ever disagree with manual gating, inspect the scatter/singlet gate FIRST,
# not the marker/viability threshold. See references/qc_gating.md and references/validation_vs_manual.md.
#
# Output: <outdir>/sce_prepped.rds, <outdir>/qc_transform_log.txt, QC gate figures.
# =====================================================================================

suppressPackageStartupMessages({
  library(optparse); library(flowCore); library(CATALYST)
  library(SingleCellExperiment); library(ggplot2); library(matrixStats)
})

# Source the data-driven gating engine (valley/gmm/otsu/control 1D + 2D joint gates +
# unimodality guard + editable-template + diagnostic figures). Resolve path from --file=.
.args_all <- commandArgs(FALSE)
.this <- sub("^--file=", "", grep("^--file=", .args_all, value = TRUE))
SCRIPT_DIR <- if (length(.this)) dirname(normalizePath(.this[1])) else "scripts"
source(file.path(SCRIPT_DIR, "gating_engine.R"))

# ------------------------------------------------------------------ args
opt <- parse_args(OptionParser(option_list = list(
  make_option("--input", type = "character"),
  make_option("--metadata", type = "character", default = NA),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--modality", type = "character", default = "auto"),
  make_option("--spectral-state", type = "character", default = "auto", dest = "spectral_state",
              help = "auto | raw | unmixed  (override spectral-unmixing detection)"),
  make_option("--transform", type = "character", default = "auto"),
  make_option("--cofactor", type = "character", default = "auto"),
  make_option("--compensate", type = "character", default = "auto"),
  make_option("--label-col", type = "character", default = NA, dest = "label_col"),
  make_option("--qc", type = "character", default = "on"),
  make_option("--singlet-mad-k", type = "double", default = 4.0, dest = "singlet_mad_k",
              help = "PERMISSIVE-by-default singlet band width in MADs for the FSC-A/FSC-H ratio (default 4; larger = gentler). Tight singlet gates silently discard REAL cells and bias downstream populations -- err gentle."),
  make_option("--debris-pct", type = "double", default = 0.02, dest = "debris_pct",
              help = "Lower-percentile FSC/SSC debris floor (default 0.02 = gentle). Raise only with cause."),
  make_option("--overgate-alarm", type = "double", default = 0.30, dest = "overgate_alarm",
              help = "If scatter+singlet gates JOINTLY remove more than this fraction, raise a loud OVER-GATING alarm (default 0.30 = 30%)."),
  make_option("--viab-max-remove", type = "double", default = 0.50, dest = "viab_max_remove",
              help = "Safety cap on the live/dead gate: if the data-driven valley cutoff would remove more than this fraction of events, reject it and fall back to the conservative 95th-percentile cutoff (default 0.50 = 50%). Rationale: a viability valley that removes >50%% of events is almost certainly cutting into the live population (e.g. neutrophils with non-specific dye uptake), not separating live from dead. Set to 1.0 to disable. Override with --thresholds if the sample genuinely has >50%% dead cells."),
  # ---- data-driven / reviewable / multivariate gating (gating_engine.R) ----
  make_option("--gate-method", type = "character", default = "auto", dest = "gate_method",
              help = "Cutoff method for data-driven gates: valley|gmm|otsu|percentile|control|auto (default auto = valley with honest unimodal/shallow fallback to conservative percentile)."),
  make_option("--multivariate", type = "character", default = "on",
              help = "on|off: use 2D joint gates (debris FSC-A x SSC-A; live/dead viability x scatter or Pt x DNA) in addition to 1D cutoffs (default on)."),
  make_option("--gate-review", type = "character", default = "propose", dest = "gate_review",
              help = "propose|apply|auto. propose (default) = PASS 1: write template+figures then STOP for human edit (resume with --gate-review apply --thresholds <edited.csv>). apply = PASS 2: apply an edited --thresholds CSV. auto = one-pass smart proposals, write template+figures, and PROCEED to the SCE unattended (escape hatch for pipelines/batch jobs). Confirmation-by-default applies to the builtin engine with --qc on; --qc off, a supplied --thresholds, and the openCyto backend all skip the stop."),
  make_option("--thresholds", type = "character", default = NA,
              help = "Edited gating_thresholds CSV to APPLY (pass 2; final_cutoff/apply columns honored; sample_id=ALL broadcasts)."),
  make_option("--controls", type = "character", default = NA,
              help = "Controls CSV (channel,control_file,control_type{unstained|fmo},percentile) to anchor positive/negative cutoffs to unstained/FMO controls."),
  make_option("--valley-min-depth", type = "double", default = 0.10, dest = "valley_min_depth",
              help = "Minimum normalized valley depth (0-1) to TRUST a data-driven cutoff; below this -> REVIEW_shallow + conservative percentile (default 0.10)."),
  make_option("--dip-alpha", type = "double", default = 0.05, dest = "dip_alpha",
              help = "Hartigan dip-test alpha for the unimodality guard; unimodal -> no valley invented (default 0.05)."),
  make_option("--threshold-scope", type = "character", default = "per_sample", dest = "threshold_scope",
              help = "per_sample|pooled|batch. per_sample computes cutoffs per sample (correct for staining variation); pooled harmonizes to the across-sample median cutoff; batch harmonizes cutoffs WITHIN each metadata batch via confidence-weighted shrinkage (item 4). Default per_sample."),
  # ---- v2.2.0 item 4: batch-aware cutoff harmonization ----
  make_option("--harmonize-shrink", type = "double", default = 1.0, dest = "harmonize_shrink",
              help = "Max shrink weight toward the (gate,batch) consensus cutoff for --threshold-scope batch; effective weight = (1 - valley_confidence) * shrink. 0 = no harmonization, 1 = full (default 1.0)."),
  # ---- v2.2.0 item 1: time-based acquisition QC ----
  make_option("--time-qc", type = "character", default = "auto", dest = "time_qc",
              help = "off|report|remove|auto. Flow-rate + signal-stability + margin-event QC over the Time channel. auto = report when modality=flow, a Time channel exists and --qc on (diagnostics-on: flags + figures, removes 0 events); remove opts in to dropping flagged events (default auto)."),
  make_option("--time-qc-backend", type = "character", default = "native", dest = "time_qc_backend",
              help = "native|flowai|peacoqc. Backend for time-based QC; native uses the built-in MAD engine. flowAI/PeacoQC are opt-in and fall back to native if unavailable (default native)."),
  make_option("--time-qc-mad-k", type = "double", default = 5.0, dest = "time_qc_mad_k",
              help = "MAD multiplier for flagging unstable time-bins in native time-QC (default 5)."),
  # ---- v2.2.0 item 2: compensation / spillover diagnostics ----
  make_option("--spillover", type = "character", default = NA,
              help = "External compensation/spillover matrix CSV (channels x channels, header + rownames) to APPLY instead of the embedded matrix (flow). Aligned to data channels by intersection."),
  make_option("--spillover-kappa-max", type = "double", default = 1e3, dest = "spillover_kappa_max",
              help = "Condition-number (kappa) threshold above which a spillover matrix is flagged ILL-conditioned. Reported and STILL applied (diagnostics-on); default 1000."),
  # ---- v2.2.0 item 8: CyTOF bead normalization ----
  make_option("--cytof-norm", type = "character", default = "off", dest = "cytof_norm",
              help = "off|on|auto. CATALYST::normCytof bead-based signal normalization (CyTOF). auto = on when modality=cytof and a bead channel is present. When on, the threshold bead-removal gate is skipped so beads survive for normCytof (default off)."),
  make_option("--beads", type = "character", default = "dvs",
              help = "Bead type for normCytof: dvs (masses 140,151,153,165,175) or beta, or a comma-separated list of masses (default dvs)."),
  make_option("--cytof-norm-k", type = "integer", default = 500L, dest = "cytof_norm_k",
              help = "normCytof smoothing window k (default 500)."),
  # ---- v2.2.0 item 6: OpenCyto hierarchical gating (opt-in backend) ----
  make_option("--gate-engine", type = "character", default = "builtin", dest = "gate_engine",
              help = "builtin|opencyto. builtin = data-driven per-gate engine (default, v2.1.0 behavior). opencyto = openCyto/flowWorkspace GatingSet + gatingTemplate hierarchical gating (requires --gate-template or a shipped default)."),
  make_option("--gate-template", type = "character", default = NA, dest = "gate_template",
              help = "openCyto gatingTemplate CSV for --gate-engine opencyto (alias,pop,parent,dims,gating_method,gating_args,...). If omitted, a shipped default template for the detected modality is used."),
  # ---- viability x lineage-marker live/dead diagnostic ----
  make_option("--lineage-markers", type = "character", default = NA, dest = "lineage_markers",
              help = "Comma-separated antigen names for viability x lineage-marker live/dead diagnostics (e.g. CD15,CD66b). Resolved against the panel antigens (word-boundary match, so 'CD15' matches 'CD15 FITC'); if omitted, auto-detects granulocyte-first defaults (CD15,CD66b,CD16,CD11b) present in the panel. Diagnostic only (apply=N); gating behavior unchanged. Rationale: neutrophils take up viability dye without being dead, so a viability x scatter view alone can misgate live neutrophils as dead; viability x a granulocyte lineage marker makes the dye-bright-but-live population obvious. If no lineage marker is present, logs a note and skips gracefully (no error)."),
  make_option("--seed", type = "integer", default = 1234L)
)))
set.seed(opt$seed)
dir.create(opt$outdir, recursive = TRUE, showWarnings = FALSE)
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
cat("", file = LOG)  # truncate
logmsg("=== 01_load_and_qc.R  |  seed=%d  |  %s ===", opt$seed, as.character(Sys.time()))

# ------------------------------------------------------------------ helpers
# Detect modality from FCS keywords + channel names.
# Spectral classification: distinguish RAW spectral (unmixing pending -> refuse) from
# ALREADY-UNMIXED spectral (proceed as fluorescence flow) using channel/marker STRUCTURE,
# not the instrument name alone. Cytek Aurora raw channels are UV1-A..UV16-A, V1-A..V16-A,
# B1-A..B14-A, YG1-A..YG10-A, R1-A..R8-A; unmixed Aurora shows fluorochrome tags (PerCP-A,
# FITC-A). Sony SP6800 (32-ch PMT) / ID7000 (up to 184 detectors) use numbered channels raw
# and fluorochrome names once unmixed. Instrument name is a HINT only; it never triggers an
# unavoidable abort by itself.
detect_modality <- function(ff) {
  kw <- flowCore::keyword(ff)
  # Coerce possibly-NULL/NA keywords to length-1 strings. paste(NULL, NULL) returns
  # character(0), which otherwise cascades into length-zero logicals and an
  # "argument is of length zero" crash on minimally-annotated FCS exports (no $CYT/$CYTSN).
  .kw1 <- function(x) { v <- kw[[x]]; if (is.null(v) || all(is.na(v))) "" else as.character(v)[1] }
  cyt_raw <- trimws(paste(.kw1("$CYT"), .kw1("$CYTSN")))
  cyt <- toupper(cyt_raw)
  cyt_empty <- cyt_raw == ""
  chans <- as.character(flowCore::pData(flowCore::parameters(ff))$name)
  descs <- as.character(flowCore::pData(flowCore::parameters(ff))$desc); descs[is.na(descs)] <- ""
  mass_like <- grepl("Di$|_Di|[0-9]{2,3}(Nd|Sm|Eu|Gd|Tb|Dy|Ho|Er|Tm|Yb|Lu|La|Ce|Pr|Pt|Ir|Rh|In|Cd|Xe|Ba|Te|Sn|Y|Cs)", chans)
  has_scatter <- any(grepl("FSC|SSC", chans, ignore.case = TRUE))
  n_chan <- length(chans)
  is_cytof <- grepl("CYTOF|HELIOS|MASS|DVS|FLUIDIGM", cyt) || (mean(mass_like) > 0.3)
  is_flow  <- has_scatter || grepl("FACS|LSR|FORTESSA|CANTO|SYMPHONY|AURORA|CYTEK|ATTUNE|CALIBUR|NAVIOS|GALLIOS|INFLUX|MOFLO", cyt)
  # --- spectral classification (data-driven, not instrument-name-only) ---
  instrument_spectral <- grepl("AURORA|CYTEK|SPECTRAL|SP6800|ID7000", cyt)
  # non-scatter / non-time channels are the ones that carry fluor/marker identity
  non_aux <- !grepl("FSC|SSC|Time|Event_length|Center|Offset|Width|Residual", chans, ignore.case = TRUE)
  non_aux_chans <- chans[non_aux]; non_aux_descs <- descs[non_aux]
  # named fluorophore / antibody-marker vocabulary (already-unmixed signature)
  fluor_re <- "FITC|PE|APC|BV|BUV|PE-Cy|APC-Cy|PerCP|Vio|Spark|NovaFluor|eFluor|Brilliant|CD[0-9]|HLA|Ig|TCR|Ki-?67|Granzyme|IFN|IL-?[0-9]|TNF|GM-CSF|CXCR|CCR|CD[0-9]+[a-z]"
  named_fluor_n <- sum(grepl(fluor_re, non_aux_descs, ignore.case = TRUE) |
                       grepl(fluor_re, non_aux_chans, ignore.case = TRUE))
  named_fluor_frac <- if (length(non_aux_chans) > 0) named_fluor_n / length(non_aux_chans) else 0
  # raw detector channels: real instrument conventions (NOT generic single-letter patterns)
  # Cytek Aurora raw: ^(UV|V|B|YG|R)[0-9]+-?A?$  ; Sony SP6800/ID7000: ^(CH|FL)?[0-9]+-?A?$
  cytek_raw_re <- "^(UV|V|B|YG|R)[0-9]+-?A?$"
  sony_raw_re  <- "^(CH|FL)?[0-9]+-?A?$"
  raw_detector_n <- sum(grepl(cytek_raw_re, non_aux_chans, ignore.case = TRUE) |
                        grepl(sony_raw_re,  non_aux_chans, ignore.case = TRUE))
  raw_detector_frac <- if (length(non_aux_chans) > 0) raw_detector_n / length(non_aux_chans) else 0
  raw_detector <- instrument_spectral && raw_detector_frac > 0.5 && named_fluor_frac < 0.2 && n_chan > 30
  # spectral_state (auto): unmixed if named fluorophores/markers dominate; raw if raw detectors
  # dominate; ambiguous -> fail open as unmixed (logged warning, user can force raw).
  spectral_state <- if (!instrument_spectral) "none"
                    else if (named_fluor_frac >= 0.5) "unmixed"
                    else if (raw_detector) "raw"
                    else "unmixed"  # ambiguous -> fail open
  list(modality = if (is_cytof && !is_flow) "cytof" else if (is_flow) "flow" else if (is_cytof) "cytof" else "unknown",
       spectral = (spectral_state == "raw"),  # backward-compat: only raw triggers the guard
       spectral_state = spectral_state, instrument_spectral = isTRUE(instrument_spectral),
       named_fluor_frac = named_fluor_frac, raw_detector = isTRUE(raw_detector),
       cyt = cyt, cyt_empty = isTRUE(cyt_empty), has_scatter = has_scatter, n_chan = n_chan)
}

# Per-channel arcsinh cofactor estimate for flow (data-driven; fallback path).
est_arcsinh_cofactor <- function(mat) {
  q <- apply(mat, 2, function(x) stats::quantile(abs(x[is.finite(x)]), 0.2, na.rm = TRUE))
  cf <- pmax(q, 1); stats::median(cf, na.rm = TRUE)
}

# NOTE: per-gate diagnostic figures are now produced by the gating engine
# (plot_gate_1d / plot_gate_2d in gating_engine.R), which draws the ACTUAL cutoff (1D)
# or joint region (2D) on each channel's density. The old generic scatter overlay
# (save_gate_fig) is retired -- it never drew the threshold and was never called.

# ------------------------------------------------------------------ load
is_hd <- grepl("^HDCytoData:", opt$input, ignore.case = TRUE)
label_col <- opt$label_col
lab_codes <- NULL; lab_levels <- NULL  # set in HDCytoData path; remain NULL for real FCS
prov <- list()  # provenance read from metadata

if (is_hd) {
  ds <- sub("^HDCytoData:", "", opt$input, ignore.case = TRUE)
  if (!requireNamespace("HDCytoData", quietly = TRUE))
    stop("HDCytoData not installed. Install via BiocManager into /workspace/.Rlib.")
  logmsg("Loading HDCytoData benchmark dataset: %s", ds)
  se <- do.call(paste0(ds, "_SE"), list(), envir = asNamespace("HDCytoData"))
  # Provenance from the dataset help/metadata (READ, do not infer).
  prov$dataset <- ds
  prov$provenance <- tryCatch(paste(S4Vectors::metadata(se)$description, collapse = " "), error = function(e) NA)
  logmsg("Provenance (from package metadata): %s", ifelse(is.na(prov$provenance), "not available", prov$provenance))
  # Build flowSet from the SE: cells in rows, channels in cols.
  ex  <- SummarizedExperiment::assay(se)
  cd  <- SummarizedExperiment::colData(se)   # channel_name, marker_name, marker_class
  rd  <- SummarizedExperiment::rowData(se)   # patient_id, population_id
  samp_col <- intersect(c("patient_id", "sample_id", "group_id"), colnames(rd))[1]
  samples  <- as.character(rd[[samp_col]])
  panel <- data.frame(fcs_colname = as.character(cd$marker_name),
                      antigen     = as.character(cd$marker_name),
                      marker_class= as.character(cd$marker_class), stringsAsFactors = FALSE)
  # carry per-cell labels (ground truth) ordered to match flowSet concatenation.
  # Labels are embedded as a numeric parameter column ("_label_code") inside each
  # flowFrame so they survive QC subsetting (which removes cells) and stay aligned
  # with the post-QC SCE. The code->level map is stored in lab_levels.
  if (is.na(label_col) && "population_id" %in% colnames(rd)) label_col <- "population_id"
  if (!is.na(label_col)) {
    lab_all <- as.character(rd[[label_col]])
    lab_levels <- sort(unique(na.omit(lab_all)))
    lab_codes  <- setNames(seq_along(lab_levels), lab_levels)
  }
  ffs <- lapply(unique(samples), function(s) {
    mat <- as.matrix(ex[samples == s, , drop = FALSE])
    if (!is.na(label_col)) {
      lc <- as.character(rd[[label_col]][samples == s])
      code <- ifelse(is.na(lc), NA_real_, as.numeric(lab_codes[lc]))
      mat <- cbind(mat, `_label_code` = code)
    }
    flowCore::flowFrame(mat)
  })
  names(ffs) <- unique(samples); fs <- as(ffs, "flowSet")
  md <- data.frame(file_name = unique(samples), sample_id = unique(samples),
                   condition = "benchmark", stringsAsFactors = FALSE)
  modality <- if (opt$modality == "auto") "cytof" else opt$modality  # HDCytoData CyTOF benchmarks
  spectral <- FALSE
  pop_ordered <- NULL  # labels are now carried inside the flowFrames; extracted post-QC
  raw_ff <- fs[[1]]  # for modality/QC field checks
} else {
  # ---- real FCS ----
  if (dir.exists(opt$input)) {
    fcs_files <- list.files(opt$input, pattern = "\\.fcs$", full.names = TRUE, ignore.case = TRUE)
  } else fcs_files <- opt$input
  if (length(fcs_files) == 0) stop("No .fcs files found at --input")
  logmsg("Loading %d FCS file(s)", length(fcs_files))
  fs <- flowCore::read.flowSet(files = fcs_files, transformation = FALSE, truncate_max_range = FALSE)
  raw_ff <- fs[[1]]
  # metadata
  if (!is.na(opt$metadata) && file.exists(opt$metadata)) {
    md_in <- read.csv(opt$metadata, stringsAsFactors = FALSE)
    md <- data.frame(file_name = md_in$file, sample_id = md_in$sample_id,
                     condition = if ("group" %in% names(md_in)) md_in$group else "sample",
                     batch = if ("batch" %in% names(md_in)) md_in$batch else NA,
                     patient = if ("patient" %in% names(md_in)) md_in$patient else NA,
                     stringsAsFactors = FALSE)
    prov$provenance <- "user FCS; provenance from metadata.csv / FCS keywords"
  } else {
    bn <- basename(flowCore::sampleNames(fs))
    md <- data.frame(file_name = bn, sample_id = tools::file_path_sans_ext(bn),
                     condition = "sample", batch = NA, patient = NA, stringsAsFactors = FALSE)
    prov$provenance <- "user FCS; no metadata.csv supplied"
  }
  # panel from FCS parameters
  pn <- flowCore::pData(flowCore::parameters(raw_ff))
  ag <- as.character(pn$desc); ag[is.na(ag) | ag == ""] <- as.character(pn$name)[is.na(ag) | ag == ""]
  # mark scatter/time/bead/viability/autofluorescence as non-type ("none"); everything else "type"
  # also check the antigen ($PnS) field, not just the channel name ($PnN),
  # so fluorophore-named viability channels (FVS780-A / "Viability") and autofluorescence
  # channels (AF-A) are correctly excluded from clustering features.
  nm <- as.character(pn$name)
  is_none_ch  <- grepl("FSC|SSC|Time|Event_length|Center|Offset|Width|Residual|bead|DNA|Ir19|Pt19|Live|Dead|Viability", nm, ignore.case = TRUE)
  is_none_ag  <- grepl("FSC|SSC|Time|Event_length|Center|Offset|Width|Residual|bead|DNA|Ir19|Pt19|Live|Dead|Viability|FVS|Zombie|7AAD|7-AAD|Aqua|Ghost|eFluor.?506|^PI$|L/D|LiveDead|Autofluorescence", ag, ignore.case = TRUE)
  is_none_af  <- grepl("^AF(-A)?$", nm, ignore.case = TRUE)  # autofluorescence channel (Cytek Aurora)
  is_none <- is_none_ch | is_none_ag | is_none_af
  panel <- data.frame(fcs_colname = nm, antigen = ag,
                      marker_class = ifelse(is_none, "none", "type"), stringsAsFactors = FALSE)
  # modality detection
  det <- detect_modality(raw_ff)
  modality <- if (opt$modality != "auto") opt$modality else det$modality
  # spectral state: explicit user override ALWAYS wins over auto-detection
  spectral_state <- switch(opt$spectral_state,
    raw = "raw", unmixed = "unmixed", auto = det$spectral_state)
  spectral <- (spectral_state == "raw")   # only raw triggers the guard
  # already-unmixed spectral IS fluorescence flow: force modality=flow unless user overrode it
  if (spectral_state == "unmixed" && opt$modality == "auto") modality <- "flow"
  if (det$cyt_empty)
    logmsg("NOTE: $CYT/$CYTSN keyword empty/missing; modality classified from channel/marker structure only (assumption logged).")
  logmsg("Modality detection: %s | spectral_state=%s (instrument_spectral=%s, named_fluor_frac=%.2f, raw_detector=%s, n_chan=%d, $CYT='%s')",
         modality, spectral_state, det$instrument_spectral, det$named_fluor_frac, det$raw_detector, det$n_chan, det$cyt)
  logmsg("Spectral decision: %s -- %s",
         spectral_state,
         if (spectral_state == "unmixed") "already-unmixed spectral treated as fluorescence flow"
         else if (spectral_state == "raw") "raw spectral (unmixing pending) -> will refuse"
         else "not spectral")
  if (opt$spectral_state != "auto")
    logmsg("  (user override --spectral-state=%s applied; auto-detection was %s)", opt$spectral_state, det$spectral_state)
  pop_ordered <- NULL
  if (!is.na(label_col)) logmsg("Per-cell label column requested: %s (benchmark enabled downstream)", label_col)
}

# ------------------------------------------------------------------ spectral guard (raw only)
if (isTRUE(spectral)) {
  logmsg("!! RAW SPECTRAL data detected (unmixing pending). This skill does NOT perform spectral unmixing.")
  logmsg("!! If this data is ALREADY UNMIXED (per-fluorophore abundances), rerun with --spectral-state unmixed.")
  logmsg("!! To force processing as-is, rerun with --spectral-state unmixed --modality flow.")
  stop("Raw spectral data requires upstream unmixing; aborting per skill scope. Override with --spectral-state unmixed.")
}
if (modality == "unknown") {
  logmsg("Modality could not be auto-detected; defaulting to 'flow'. Override with --modality.")
  modality <- "flow"
}

# ------------------------------------------------------------------ compensation (flow) + spillover diagnostics (item 2)
# v2.2.0 item 2: ALWAYS compute a condition-number diagnostic (kappa/rcond/verdict) on the
# spillover matrix and RECORD it (diagnostics-on). An ILL-conditioned matrix (kappa >
# --spillover-kappa-max) is REPORTED but STILL applied; only malformed/singular matrices fall
# back to UNCOMPENSATED (preserves v2.1.0 apply/skip logic -> no drift). An external --spillover
# CSV takes precedence over the embedded matrix (aligned to data channels by intersection).
comp_applied <- "none"
comp_info <- list(source = "none", dim = NA_integer_, rcond = NA_real_, kappa = NA_real_,
                  verdict = "none", applied = FALSE)
do_comp <- switch(opt$compensate, on = TRUE, off = FALSE, auto = (modality == "flow"), FALSE)
if (do_comp) {
  chans <- flowCore::colnames(raw_ff)
  mat <- NULL; src <- "none"; align_note <- ""
  # (a) external spillover CSV takes precedence over the embedded matrix
  if (!is.na(opt$spillover) && nzchar(opt$spillover)) {
    if (!file.exists(opt$spillover)) {
      logmsg("WARNING: --spillover '%s' not found; using embedded spillover.", opt$spillover)
    } else {
      ext <- tryCatch(read_spillover_csv(opt$spillover), error = function(e) NULL)
      if (is.null(ext)) {
        logmsg("WARNING: --spillover CSV '%s' unreadable; using embedded spillover.", opt$spillover)
      } else {
        al <- align_spillover(ext, chans)
        if (isTRUE(al$ok)) {
          mat <- al$matrix; src <- "external"
          align_note <- sprintf(" [aligned %d ch; %d missing, %d extra]", al$info$n_matched,
                                length(al$info$missing_in_matrix), length(al$info$extra_in_matrix))
          logmsg("Using EXTERNAL spillover from %s%s", opt$spillover, align_note)
        } else logmsg("WARNING: external spillover from %s could not be aligned to data channels (%d matched); using embedded.",
                      opt$spillover, al$info$n_matched)
      }
    }
  }
  # (b) embedded spillover fallback
  if (is.null(mat)) {
    sp <- tryCatch(flowCore::spillover(raw_ff), error = function(e) NULL)
    sp <- Filter(Negate(is.null), sp)
    if (length(sp) >= 1 && !is.null(sp[[1]])) { mat <- sp[[1]]; src <- "embedded" }
  }
  if (!is.null(mat)) {
    # ALWAYS compute the condition-number diagnostic (report even when applied)
    dg <- spillover_diagnostics(mat, kappa_max = opt$spillover_kappa_max)
    comp_info$source <- src; comp_info$dim <- dg$n; comp_info$rcond <- dg$rcond
    comp_info$kappa <- dg$kappa; comp_info$verdict <- dg$verdict
    logmsg("Spillover diagnostics [%s]: kappa=%s rcond=%s verdict=%s (%s)", src,
           ifelse(is.finite(dg$kappa), sprintf("%.4g", dg$kappa), "NA"),
           ifelse(is.finite(dg$rcond), sprintf("%.4g", dg$rcond), "NA"), dg$verdict, dg$notes)
    if (identical(dg$verdict, "ill"))
      logmsg("  !! ILL-CONDITIONED spillover (kappa=%.4g > --spillover-kappa-max=%.4g): the inverse AMPLIFIES noise and can destabilize positivity gates. REPORTED and STILL APPLIED (diagnostics-on / removal-opt-in). Inspect the spillover matrix / single-stain controls.",
             dg$kappa, opt$spillover_kappa_max)
    # apply defensively: malformed/singular -> UNCOMPENSATED; well/ill -> apply
    comp_try <- if (dg$verdict %in% c("malformed", "singular")) {
                  simpleError(sprintf("spillover matrix %s (%s)", dg$verdict, dg$notes))
                } else tryCatch(flowCore::compensate(fs, mat), error = function(e) e)
    if (!inherits(comp_try, "error")) {
      fs <- comp_try; comp_info$applied <- TRUE
      comp_applied <- sprintf("%s spillover (%d x %d, verdict=%s)%s", src, dg$n, dg$n, dg$verdict, align_note)
      logmsg("Compensation applied: %s", comp_applied)
    } else {
      comp_applied <- sprintf("none (%s spillover not applied: %s - proceeding UNCOMPENSATED)",
                              src, conditionMessage(comp_try))
      logmsg("WARNING: %s", comp_applied)
    }
  } else {
    comp_applied <- "none (no spillover found - proceeding UNCOMPENSATED)"
    logmsg("WARNING: %s", comp_applied)
  }
} else logmsg("Compensation: skipped (modality=%s, --compensate=%s)", modality, opt$compensate)

# ------------------------------------------------------------------ decide transform + cofactor
if (opt$transform == "auto") transform <- if (modality == "cytof") "arcsinh" else "logicle" else transform <- opt$transform
if (opt$cofactor == "auto") {
  cofactor <- if (modality == "cytof") 5 else NA  # flow: prefer logicle; arcsinh cofactor estimated if chosen
} else cofactor <- as.numeric(opt$cofactor)
logmsg("Transform decision: %s | cofactor: %s | modality: %s",
       transform, ifelse(is.na(cofactor), "n/a (logicle) / estimated", cofactor), modality)

# ------------------------------------------------------------------ v2.2.0 feature setup (items 4/6/8)
# Initialize v2.2.0 summaries unconditionally so downstream metadata is always well-formed,
# regardless of modality / which features run. Defaults preserve v2.1.0 behavior (no drift).
harmonization <- list(scope = opt$threshold_scope, n_batches = 0L,
                      n_groups_harmonized = 0L, shrink = opt$harmonize_shrink)
gate_hierarchy <- NULL   # populated by the opencyto backend (item 6); NULL for builtin
cytof_norm_summary <- list(applied = FALSE, beads = opt$beads, k = opt$cytof_norm_k,
                           n_removed = 0L, n_beads = NA_integer_)
# item 8: decide whether CyTOF bead normalization is active (opt-in). auto = cytof + bead channel.
cytof_bead_ch <- if (modality == "cytof") {
  cn <- flowCore::colnames(raw_ff); h <- cn[grepl("Ce140|140Ce|bead|EQ", cn, ignore.case = TRUE)]
  if (length(h)) h[1] else NA_character_
} else NA_character_
cytof_norm_on <- switch(opt$cytof_norm,
  on   = (modality == "cytof"),
  auto = (modality == "cytof" && !is.na(cytof_bead_ch)),
  off  = FALSE, FALSE)
if (opt$cytof_norm != "off" && modality != "cytof")
  logmsg("NOTE: --cytof-norm=%s ignored (modality=%s, not cytof).", opt$cytof_norm, modality)
if (cytof_norm_on)
  logmsg("CyTOF bead normalization ENABLED (normCytof beads=%s k=%d): the threshold bead-removal gate will be SKIPPED so bead events survive for normalization.",
         opt$beads, opt$cytof_norm_k)
if (!is.na(opt$gate_template) && nzchar(opt$gate_template) && opt$gate_engine == "builtin") {
  logmsg("NOTE: --gate-template supplied; switching --gate-engine to 'opencyto'.")
  opt$gate_engine <- "opencyto"
}
if (opt$gate_engine == "opencyto") {
  logmsg("Gating engine: openCyto/flowWorkspace hierarchical backend (item 6, opt-in).")
  source(file.path(SCRIPT_DIR, "gating_opencyto.R"))
}

# ------------------------------------------------------------------ QC gating (BEFORE clustering)
# Data-driven (density valley), reviewable (editable template + per-gate figures), and
# multivariate (2D joint gates) QC gating via gating_engine.R. Gates compose by INTERSECTION;
# each 1D gate row carries ONE editable cutoff; 2D gates are accept/reject (region2d).
# Data-driven cutoffs are found on the (locally arcsinh-transformed) channel so valleys are
# visible; when a channel is unimodal (Hartigan dip test) or its valley is too shallow, NO
# cutoff is invented -> conservative percentile + REVIEW flag (honesty rule; matches legacy
# behavior on already-clean data). scatter/singlet gates stay PERMISSIVE; the per-gate WARNING
# and OVER-GATING alarm are preserved. See references/qc_gating.md + threshold_selection.md.
qc_on <- (opt$qc == "on")
PER_GATE_WARN <- 0.20  # per-gate warn threshold for a single scatter/singlet gate
CONTROLS <- NULL       # populated by load_controls() when --controls supplied

# ---- resolve lineage markers for the viability x lineage live/dead diagnostic.
# Neutrophils take up viability dye without being dead, so a viability x scatter (FSC) view
# alone can misgate live neutrophils as dead. Plotting viability against a granulocyte lineage
# marker (CD15/CD66b/CD16/CD11b) makes the dye-bright-but-live population obvious to a reviewer.
# Resolve antigen names -> fcs_colname channels via the global panel map (fcs_colname <-> antigen).
# NA/empty --lineage-markers -> auto-detect granulocyte-first defaults present in the panel.
# Word-boundary regex (\bCD15\b, PCRE) tolerates fluorophore/clone suffixes in real FCS $PnS
# antigen fields ("CD15 FITC") while dodging the CD16<->CD166 collision (\bCD16\b != "CD166").
DEFAULT_LINEAGE_MARKERS <- c("CD15", "CD66b", "CD16", "CD11b")
resolve_lineage_markers <- function(panel, user_arg) {
  ag <- if (is.na(user_arg) || !nzchar(user_arg)) character(0)        # NA/empty -> auto-detect
        else toupper(trimws(strsplit(user_arg, ",")[[1]]))
  ag <- ag[ag != ""]
  if (!length(ag)) ag <- toupper(DEFAULT_LINEAGE_MARKERS)             # granulocyte-first defaults
  pag <- toupper(panel$antigen)
  hits <- vapply(ag, function(a) {
    pat <- sprintf("\\b%s\\b", a)                                     # word-boundary regex
    i <- which(grepl(pat, pag, perl = TRUE))                          # tolerant of "CD15 FITC";
    if (length(i)) panel$fcs_colname[i[1]] else NA_character_         # \bCD16\b != "CD166"
  }, character(1))
  out <- hits[!is.na(hits)]; names(out) <- names(hits)[!is.na(hits)]
  out
}
LINEAGE_CH <- resolve_lineage_markers(panel, opt$lineage_markers)

# ---- resolve the viability / live-dead dye channel.
# Real spectral FCS (e.g. Cytek Aurora / SpectroFlo) often names the viability channel by
# FLUOROPHORE in $PnN (e.g. "FVS780-A") with the dye identity ("Viability") only in the marker
# field $PnS. The legacy resolver searched channel names only and silently missed this case,
# skipping the live/dead gate AND the viability x lineage diagnostic. resolve_viab_channel()
# first tries the current channel-name patterns, then falls back to matching a viability-dye
# vocabulary against panel$antigen ($PnS) -> panel$fcs_colname ($PnN) -- the same marker-field
# resolution pattern used by resolve_lineage_markers(). No behavior change when the channel
# name already matches; the chosen channel + its source (channel_name vs marker_field) are
# logged so a reviewer can see how the dye was found.
VIAB_CHANNEL_PATTERNS <- c("Live", "Dead", "Viability", "Zombie", "7AAD", "7-AAD", "DAPI",
                           "^PI$", "L/D", "LiveDead", "FVS", "Aqua", "Ghost", "eFluor ?506")
VIAB_MARKER_PATTERNS  <- c("Viability", "Live", "Dead", "FVS", "Zombie", "7-?AAD", "L/D",
                           "Aqua", "Ghost", "eFluor ?506", "DAPI", "\\bPI\\b")
resolve_viab_channel <- function(panel, ch_names, log = TRUE) {
  # 1) channel-name match (legacy behavior, preserved exactly)
  pat_ch <- paste(VIAB_CHANNEL_PATTERNS, collapse = "|")
  hit <- ch_names[grepl(pat_ch, ch_names, ignore.case = TRUE)]
  if (length(hit)) {
    if (log) logmsg("Viability channel resolved: %s (from channel name)", hit[1])
    return(hit[1])
  }
  # 2) marker-field fallback: panel$antigen ($PnS) -> panel$fcs_colname ($PnN)
  pag <- toupper(panel$antigen); pfc <- panel$fcs_colname
  for (p in VIAB_MARKER_PATTERNS) {
    i <- which(grepl(p, pag, ignore.case = TRUE, perl = TRUE))
    if (length(i)) {
      if (log) logmsg("Viability channel resolved: %s (from marker field '%s' -> $PnN)", pfc[i[1]], panel$antigen[i[1]])
      return(pfc[i[1]])
    }
  }
  if (log) logmsg("Viability channel: NOT FOUND (neither channel name nor marker field matched viability vocabulary)")
  NA_character_
}
VIAB_CH <- resolve_viab_channel(panel, as.character(flowCore::pData(flowCore::parameters(raw_ff))$name))

# ------------------------------------------------------------------ time-based acquisition QC (item 1)
# Data-driven flow-rate + signal-stability + margin-event QC over the acquisition Time channel
# (pure functions in gating_engine.R). Default 'auto' = REPORT when modality=flow, a Time channel
# exists and --qc on: it FLAGS unstable acquisition and writes figures/time_qc_<sample>.png but
# REMOVES 0 events (diagnostics-on). 'remove' opts in to dropping flagged events; 'off' disables.
time_qc_mode <- switch(opt$time_qc,
  off = "off", report = "report", remove = "remove",
  auto = if (modality == "flow" && qc_on) "report" else "off", "off")
time_qc_summary <- list(mode = time_qc_mode, backend = opt$time_qc_backend,
                        pct_rate = NA_real_, pct_signal = NA_real_, pct_margin = NA_real_, removed = 0L)
if (time_qc_mode != "off") {
  logmsg("--- Time-based acquisition QC (item 1) | mode=%s | backend=%s | mad_k=%g ---",
         time_qc_mode, opt$time_qc_backend, opt$time_qc_mad_k)
  if (opt$time_qc_backend != "native")
    logmsg("NOTE: --time-qc-backend=%s requested; flowAI/PeacoQC integration is opt-in -- falling back to the native MAD engine (equivalent flow-rate/signal/margin checks).", opt$time_qc_backend)
  sns_t <- flowCore::sampleNames(fs); frames_t <- list()
  agg_rate <- c(); agg_sig <- c(); agg_marg <- c(); total_removed <- 0L
  for (s in sns_t) {
    ffx <- fs[[s]]; exx <- flowCore::exprs(ffx); cnx <- colnames(exx)
    tch <- cnx[grepl("Time", cnx, ignore.case = TRUE)][1]
    if (is.na(tch)) { logmsg("  [%s] no Time channel; time-QC skipped for this sample.", s); frames_t[[s]] <- ffx; next }
    mch <- setdiff(cnx, c(tch, "_label_code"))
    # per-channel dynamic range from FCS $PnR when present (better margin detection)
    rngs <- tryCatch({
      pr <- flowCore::pData(flowCore::parameters(ffx))
      mn <- suppressWarnings(as.numeric(pr$minRange)); mx <- suppressWarnings(as.numeric(pr$maxRange))
      names(mn) <- as.character(pr$name); names(mx) <- as.character(pr$name)
      rl <- lapply(mch, function(c0) if (!is.na(mn[c0]) && !is.na(mx[c0])) c(mn[c0], mx[c0]) else NULL)
      if (all(vapply(rl, is.null, logical(1)))) NULL else rl
    }, error = function(e) NULL)
    qc <- time_acquisition_qc(exx[, tch], expr = exx[, mch, drop = FALSE], ranges = rngs,
                              checks = c("rate", "signal", "margin"), mad_k = opt$time_qc_mad_k)
    plot_time_qc(qc, s, file.path(figdir, sprintf("time_qc_%s.png", s)))
    agg_rate <- c(agg_rate, qc$pct_rate); agg_sig <- c(agg_sig, qc$pct_signal); agg_marg <- c(agg_marg, qc$pct_margin)
    logmsg("  [%s] time-QC: rate=%.2f%% signal=%.2f%% margin=%.2f%% total=%.2f%% (n=%d)",
           s, qc$pct_rate, ifelse(is.na(qc$pct_signal), 0, qc$pct_signal),
           ifelse(is.na(qc$pct_margin), 0, qc$pct_margin), qc$pct_total, qc$n)
    if (time_qc_mode == "remove") {
      nrem <- sum(!qc$keep); total_removed <- total_removed + nrem
      frames_t[[s]] <- ffx[qc$keep, ]
      logmsg("  [%s] time-QC REMOVE: dropped %d flagged events (%.2f%%).", s, nrem, 100 * nrem / max(qc$n, 1))
    } else frames_t[[s]] <- ffx
  }
  if (time_qc_mode == "remove") { fs2 <- as(frames_t, "flowSet"); flowCore::sampleNames(fs2) <- sns_t; fs <- fs2 }
  time_qc_summary$pct_rate   <- if (length(agg_rate)) mean(agg_rate, na.rm = TRUE) else NA_real_
  time_qc_summary$pct_signal <- if (length(agg_sig))  mean(agg_sig,  na.rm = TRUE) else NA_real_
  time_qc_summary$pct_margin <- if (length(agg_marg)) mean(agg_marg, na.rm = TRUE) else NA_real_
  time_qc_summary$removed    <- as.integer(total_removed)
  logmsg("Time-QC summary: mean rate=%.2f%% signal=%.2f%% margin=%.2f%% | removed=%d (mode=%s).",
         ifelse(is.na(time_qc_summary$pct_rate), 0, time_qc_summary$pct_rate),
         ifelse(is.na(time_qc_summary$pct_signal), 0, time_qc_summary$pct_signal),
         ifelse(is.na(time_qc_summary$pct_margin), 0, time_qc_summary$pct_margin),
         time_qc_summary$removed, time_qc_mode)
  if (time_qc_mode == "report")
    logmsg("(REPORT mode: 0 events removed by design; set --time-qc remove to drop flagged events.)")
}

# ---- load unstained/FMO controls (optional) -> named list: channel-regex -> {values,type,pct} ----
load_controls <- function(path) {
  if (is.na(path) || !file.exists(path)) return(NULL)
  cc <- utils::read.csv(path, stringsAsFactors = FALSE)
  out <- list()
  for (i in seq_len(nrow(cc))) {
    f <- cc$control_file[i]
    if (is.null(f) || is.na(f) || !file.exists(f)) { logmsg("  control: file not found, skipped: %s", as.character(f)); next }
    ff <- tryCatch(flowCore::read.FCS(f, transformation = FALSE, truncate_max_range = FALSE), error = function(e) NULL)
    if (is.null(ff)) { logmsg("  control: unreadable FCS, skipped: %s", f); next }
    exx <- flowCore::exprs(ff); hit <- colnames(exx)[grepl(cc$channel[i], colnames(exx), ignore.case = TRUE)][1]
    if (is.na(hit)) { logmsg("  control: channel '%s' not found in %s, skipped", cc$channel[i], basename(f)); next }
    pct <- if ("percentile" %in% names(cc) && !is.na(cc$percentile[i])) as.numeric(cc$percentile[i]) else 0.99
    out[[cc$channel[i]]] <- list(values = exx[, hit], type = cc$control_type[i], pct = pct)
    logmsg("  control loaded: channel~'%s' <- %s (%s, pct=%.3g, n=%d)", cc$channel[i], basename(f), cc$control_type[i], pct, nrow(exx))
  }
  if (length(out) == 0) NULL else out
}

# ---- per-frame gating -> list(frame = gated flowFrame, rows = template rows) ----
gate_frame <- function(ff, sid, template) {
  ex <- flowCore::exprs(ff); n0 <- nrow(ex); ch <- colnames(ex)
  marker_ch <- setdiff(ch, "_label_code")
  keep <- rep(TRUE, n0); rows <- list(); stage <- list()
  getc <- function(pats) { h <- ch[grepl(paste(pats, collapse = "|"), ch, ignore.case = TRUE)]; if (length(h)) h[1] else NA_character_ }
  is_scatter_ch <- function(nm) grepl("FSC|SSC", nm, ignore.case = TRUE)
  cf_gate <- if (modality == "cytof") (if (is.na(cofactor)) 5 else cofactor) else 150
  tf <- function(v, nm) if (is_scatter_ch(nm)) v else asinh(v / cf_gate)   # valley scale: arcsinh for fluor/mass, linear for scatter
  ctrl_for <- function(nm) {
    if (is.null(CONTROLS)) return(NULL)
    key <- names(CONTROLS)[vapply(names(CONTROLS), function(k) grepl(k, nm, ignore.case = TRUE), logical(1))]
    if (!length(key)) return(NULL)
    tf(CONTROLS[[key[1]]]$values, nm)
  }
  record <- function(before, after, name, scatter = FALSE) {
    frac <- if (before > 0) 1 - after / before else 0
    stage[[name]] <<- c(in_ = before, out = after, frac = frac, scatter = as.numeric(scatter))
    flag <- if (scatter && frac > PER_GATE_WARN)
      sprintf("  <-- WARNING: removed %.1f%% of entering events; a tight scatter/singlet gate discards REAL cells and biases downstream. Loosen and inspect this gate FIRST if results disagree with manual gating.", 100 * frac) else ""
    logmsg("    [%s] %s: %d -> %d (%.1f%% removed)%s", sid, name, before, after, 100 * frac, flag)
  }
  # 1D data-driven gate: engine estimate (or template override) -> row + figure -> apply
  do_1d <- function(gate, chname, direction, fallback_pct = NULL, method = opt$gate_method, scatter = FALSE, use_control = FALSE, max_remove_frac = NA_real_) {
    if (is.na(chname)) return(invisible())
    before <- sum(keep); idx <- which(keep)
    xv_all <- tf(ex[, chname], chname); xv <- xv_all[idx]
    control <- if (use_control) ctrl_for(chname) else NULL
    lu <- if (!is.null(template)) lookup_threshold(template, sid, gate) else NULL
    if (!is.null(lu) && is.finite(lu$cutoff)) {
      est <- list(cutoff = lu$cutoff, method_used = "user", status = "user_edit", valley_confidence = NA,
                  unimodal = NA, dip_p = NA, valley_x = NA, notes = "from template"); applyit <- lu$apply
    } else {
      est <- estimate_threshold_1d(xv, direction = direction, method = method, control = control,
                                   fallback_pct = fallback_pct, valley_min_depth = opt$valley_min_depth, dip_alpha = opt$dip_alpha,
                                   max_remove_frac = max_remove_frac)
      applyit <- if (!is.null(lu)) lu$apply else TRUE
    }
    pr <- pct_removed_1d(xv, est$cutoff, direction)
    row <- make_gate_row(sid, gate, chname, NA, est, direction, pr); row$apply <- if (applyit) "Y" else "N"
    rows[[length(rows) + 1]] <<- row
    plot_gate_1d(xv, est$cutoff, direction, sprintf("%s | %s (%s)", sid, gate, chname),
                 file.path(figdir, sprintf("gate_%s_%s.png", sid, gate)), control = control, status = est$status,
                 depth = est$valley_confidence, unimodal = est$unimodal, dip_p = est$dip_p, valley_x = est$valley_x)
    if (isTRUE(applyit)) { keep <<- keep & apply_threshold_1d(xv_all, est$cutoff, direction); record(before, sum(keep), gate, scatter = scatter) }
    else logmsg("    [%s] %s: proposed (apply=N) -- not applied", sid, gate)
  }
  # fixed-cutoff row (MAD-band bounds / percentile bounds); template-overridable; optional figure
  do_fixed <- function(gate, chname, cutoff, direction, scatter = FALSE, note = "", vec_all = NULL, fig = FALSE) {
    before <- sum(keep); idx <- which(keep)
    xv_all <- if (is.null(vec_all)) tf(ex[, chname], chname) else vec_all; xv <- xv_all[idx]
    lu <- if (!is.null(template)) lookup_threshold(template, sid, gate) else NULL; applyit <- TRUE; status <- "fixed"
    if (!is.null(lu)) { applyit <- lu$apply; if (is.finite(lu$cutoff)) { cutoff <- lu$cutoff; status <- "user_edit"; note <- "from template" } }
    est <- list(cutoff = cutoff, method_used = if (status == "user_edit") "user" else "fixed", status = status,
                valley_confidence = NA, unimodal = NA, dip_p = NA, valley_x = NA, notes = note)
    row <- make_gate_row(sid, gate, chname, NA, est, direction, pct_removed_1d(xv, cutoff, direction))
    row$apply <- if (applyit) "Y" else "N"; rows[[length(rows) + 1]] <<- row
    if (fig) plot_gate_1d(xv, cutoff, direction, sprintf("%s | %s (%s)", sid, gate, chname),
                          file.path(figdir, sprintf("gate_%s_%s.png", sid, gate)), status = status)
    if (isTRUE(applyit)) { keep <<- keep & apply_threshold_1d(xv_all, cutoff, direction); record(before, sum(keep), gate, scatter = scatter) }
  }
  # MAD band -> two editable rows (low keep_above + high keep_below), computed on current survivors
  do_madband <- function(gate, chname, k, scatter = TRUE) {
    if (is.na(chname)) return(invisible())
    v_all <- tf(ex[, chname], chname); v <- v_all[which(keep)]
    lo <- stats::median(v, na.rm = TRUE) - k * stats::mad(v, na.rm = TRUE)
    hi <- stats::median(v, na.rm = TRUE) + k * stats::mad(v, na.rm = TRUE)
    do_fixed(sprintf("%s_low", gate), chname, lo, "keep_above", scatter = scatter, note = sprintf("median-%g*MAD", k), vec_all = v_all)
    do_fixed(sprintf("%s_high", gate), chname, hi, "keep_below", scatter = scatter, note = sprintf("median+%g*MAD", k), vec_all = v_all)
  }
  # 2D joint gate: keep main population (flowClust or robust ellipse). Reviewable via CSV:
  #   apply=Y/N            -> accept/reject the gate (as any gate)
  #   final_cutoff (0,1)   -> ellipse COVERAGE LEVEL. Blank keeps the automatic main-pop gate
  #                           (flowClust if available, else robust ellipse); a finite value
  #                           switches to a robust ellipse at that coverage. Raise toward 0.999
  #                           to LOOSEN (keep more); lower toward 0.95 to TIGHTEN (keep less).
  # Blank-by-default final_cutoff means an unedited template reproduces the auto gate exactly,
  # while a wet-lab reviewer can still LOOSEN an over-aggressive 2D gate per sample from the CSV.
  do_2d <- function(gate, chx, chy, apply_default = TRUE, scatter = FALSE) {
    if (is.na(chx) || is.na(chy)) return(invisible())
    before <- sum(keep); idx <- which(keep)
    xa <- tf(ex[, chx], chx); ya <- tf(ex[, chy], chy)
    lu <- if (!is.null(template)) lookup_threshold(template, sid, gate) else NULL
    applyit <- if (!is.null(lu)) lu$apply else apply_default
    level_default <- 0.99
    user_level <- if (!is.null(lu) && is.finite(lu$cutoff)) lu$cutoff else NA_real_
    if (!is.na(user_level) && (user_level <= 0 || user_level >= 1)) {
      logmsg("    [%s] %s: final_cutoff=%.4g outside (0,1); ignoring, using automatic gate", sid, gate, user_level)
      user_level <- NA_real_
    }
    if (!is.na(user_level)) {                                   # reviewer set an explicit ellipse level
      g2 <- estimate_gate_2d(xa[idx], ya[idx], method = "ellipse", level = user_level)
      lvl_show <- user_level; status2 <- "user_edit"
      note2 <- sprintf("2D %s x %s; robust ellipse level=%.4g (user)", chx, chy, user_level)
    } else {                                                    # automatic main-population gate
      g2 <- estimate_gate_2d(xa[idx], ya[idx], method = "auto", level = level_default)
      lvl_show <- level_default; status2 <- if (!is.null(lu)) lu$status else "auto_ok"
      note2 <- sprintf("2D %s x %s (keep main pop); final_cutoff = ellipse level in (0,1), blank=auto, raise->looser", chx, chy)
    }
    keepvec <- rep(FALSE, n0); keepvec[idx] <- g2$keep
    pr <- if (before > 0) 1 - sum(g2$keep) / before else 0
    est <- list(cutoff = lvl_show, method_used = g2$method, status = status2, valley_confidence = NA,
                unimodal = NA, dip_p = NA, valley_x = NA, notes = note2)
    row <- make_gate_row(sid, gate, chx, chy, est, "region2d", pr); row$apply <- if (applyit) "Y" else "N"
    # proposed_cutoff shows the default level knob (0.99); final_cutoff stays BLANK unless the
    # reviewer set one, so re-running an unedited template keeps the automatic gate.
    if (is.na(user_level)) row$final_cutoff <- NA
    rows[[length(rows) + 1]] <<- row
    # pass the actual ellipse geometry so the figure draws the true gate boundary (NULL for flowClust)
    ell_geom <- if (!is.null(g2$center) && !is.null(g2$cov))
      list(center = g2$center, cov = g2$cov, level = if (!is.null(g2$level)) g2$level else level_default) else NULL
    plot_gate_2d(xa[idx], ya[idx], g2$keep, chx, chy,
                 sprintf("%s | %s%s", sid, gate, if (applyit) "" else " (diagnostic, apply=N)"),
                 file.path(figdir, sprintf("gate_%s_%s.png", sid, gate)), ellipse = ell_geom)
    if (isTRUE(applyit)) { keep <<- keep & keepvec; record(before, sum(keep), gate, scatter = scatter) }
    else logmsg("    [%s] %s: proposed (apply=N) -- diagnostic only", sid, gate)
  }

  MULTI <- (opt$multivariate == "on")
  if (modality == "flow") {
    fscA <- getc(c("FSC-A", "FSC.A", "^FSC$")); fscH <- getc(c("FSC-H", "FSC.H")); sscA <- getc(c("SSC-A", "SSC.A", "^SSC$")); sscH <- getc(c("SSC-H", "SSC.H"))
    # debris gate prefers AREA, but falls back to HEIGHT when area is absent (older FACSCalibur / height-only sets).
    fsc_deb <- if (!is.na(fscA)) fscA else fscH; ssc_deb <- if (!is.na(sscA)) sscA else sscH
    # 1) debris: 2D scatter ellipse (preferred) OR two 1D floors (legacy)
    if (MULTI && !is.na(fsc_deb) && !is.na(ssc_deb)) do_2d("debris_2d", fsc_deb, ssc_deb, apply_default = TRUE, scatter = TRUE)
    else { do_1d("debris_fsc", fsc_deb, "keep_above", fallback_pct = opt$debris_pct, method = "percentile", scatter = TRUE)
           do_1d("debris_ssc", ssc_deb, "keep_above", fallback_pct = opt$debris_pct, method = "percentile", scatter = TRUE) }
    # 2) singlet: FSC-A/FSC-H ratio MAD band (two editable rows, applied on the ratio)
    if (!is.na(fscA) && !is.na(fscH)) {
      before <- sum(keep); r <- ex[, fscA] / (ex[, fscH] + 1e-6); rv <- r[which(keep)]
      lo <- stats::median(rv, na.rm = TRUE) - opt$singlet_mad_k * stats::mad(rv, na.rm = TRUE)
      hi <- stats::median(rv, na.rm = TRUE) + opt$singlet_mad_k * stats::mad(rv, na.rm = TRUE)
      do_fixed("singlet_low",  "FSC-A/FSC-H", lo, "keep_above", scatter = TRUE, note = sprintf("median-%g*MAD ratio", opt$singlet_mad_k), vec_all = r)
      do_fixed("singlet_high", "FSC-A/FSC-H", hi, "keep_below", scatter = TRUE, note = sprintf("median+%g*MAD ratio", opt$singlet_mad_k), vec_all = r)
    }
    # 3) live/dead: 1D viability valley (editable; control-anchored if provided) + 2D diagnostic
    # resolve viability via resolve_viab_channel() (channel name first, then marker
    # field) so fluorophore-named viability channels (FVS780-A / "Viability") are found. The
    # global VIAB_CH is resolved once from the panel; fall back to a per-frame channel-name
    # match in case the flowSet frame has different channel naming.
    vch <- VIAB_CH
    if (is.na(vch)) vch <- getc(c("Live", "Dead", "Viability", "Zombie", "7AAD", "DAPI", "^PI$", "L/D", "LiveDead"))
    if (!is.na(vch)) {
      do_1d("live_dead", vch, "keep_below", fallback_pct = 0.95, use_control = TRUE, max_remove_frac = opt$viab_max_remove)
      if (MULTI && !is.na(fscA)) do_2d("live_dead_2d", vch, fscA, apply_default = FALSE)
      # viability x lineage-marker diagnostic. Neutrophils take up viability dye
      # without being dead, so viability x FSC alone can misgate live neutrophils as dead.
      # Plotting viability against a granulocyte lineage marker (CD15/CD66b/CD16/CD11b) makes
      # the dye-bright-but-live population obvious. Diagnostic only (apply=N); gating unchanged.
      # Decoupled from --multivariate (always emits when vch + a resolved lineage marker exist).
      for (ag in names(LINEAGE_CH)) {
        lch <- LINEAGE_CH[[ag]]
        if (lch %in% ch) do_2d(sprintf("live_dead_lineage_%s", ag), vch, lch, apply_default = FALSE)
      }
    }
  } else if (modality == "cytof") {
    dna <- getc(c("Ir191", "Ir193", "DNA", "191Ir", "193Ir"))
    # Pt/cisplatin viability -- channel-name match first (isotope mass tag), then
    # marker-field fallback (panel$antigen may carry "Cisplatin"/"Pt195"/"Viability" in $PnS
    # while $PnN is the isotope). Mirrors the flow-branch resolve_viab_channel() pattern.
    pt <- getc(c("Pt195", "195Pt", "cisplatin", "Live_Dead", "Dead"))
    if (is.na(pt)) {
      pag <- toupper(panel$antigen)
      for (p in c("Cisplatin", "Pt195", "195Pt", "Viability", "Live_Dead", "Dead")) {
        i <- which(grepl(p, pag, ignore.case = TRUE, perl = TRUE))
        if (length(i)) { pt <- panel$fcs_colname[i[1]]; logmsg("Viability channel resolved: %s (from marker field '%s' -> $PnN)", pt, panel$antigen[i[1]]); break }
      }
    }
    bd  <- getc(c("Ce140", "140Ce", "bead", "EQ")); el <- getc(c("Event_length", "Event-length", "length"))
    # 1) DNA/intact: valley low bound (keep_above) + 99.5th-pct high bound (keep_below).
    #    v2.2.0 item 8: normalization beads are DNA-NEGATIVE, so dna_intact_low would remove
    #    them. When cytof bead normalization is enabled, skip this low bound so beads survive
    #    into the SCE for CATALYST::normCytof (which then removes them). The high bound (trim
    #    high-DNA doublets) is kept -- DNA-low beads pass it.
    if (!is.na(dna)) {
      if (cytof_norm_on) logmsg("    [%s] dna_intact_low: SKIPPED (item 8 cytof-norm on; DNA-negative beads retained for normCytof).", sid)
      else do_1d("dna_intact_low", dna, "keep_above", fallback_pct = 0.05, scatter = TRUE)
      dv <- tf(ex[, dna], dna)[which(keep)]
      do_fixed("dna_intact_high", dna, as.numeric(stats::quantile(dv, 0.995, na.rm = TRUE)), "keep_below",
               scatter = TRUE, note = "99.5th pct (trim high-DNA doublets)")
    }
    # 2) Gaussian-parameter doublet: event length MAD band
    if (!is.na(el)) do_madband("gaussian_doublet", el, opt$singlet_mad_k, scatter = TRUE)
    # 3) live/dead (Pt/cisplatin): 1D valley (editable; control-anchored) + 2D Pt x DNA diagnostic
    if (!is.na(pt)) {
      do_1d("live_dead", pt, "keep_below", fallback_pct = 0.95, use_control = TRUE, max_remove_frac = opt$viab_max_remove)
      if (MULTI && !is.na(dna)) do_2d("live_dead_2d", pt, dna, apply_default = FALSE)
      # Pt viability x lineage-marker diagnostic (analogous to the flow branch).
      # Diagnostic only (apply=N); decoupled from --multivariate.
      for (ag in names(LINEAGE_CH)) {
        lch <- LINEAGE_CH[[ag]]
        if (lch %in% ch) do_2d(sprintf("live_dead_lineage_%s", ag), pt, lch, apply_default = FALSE)
      }
    }
    # 4) bead removal (normalization beads): valley (keep_below) or 99th-pct fallback.
    #    v2.2.0 item 8: when CyTOF bead normalization is enabled, RETAIN beads so normCytof
    #    can normalize to them (it removes them afterward) -- skip the threshold bead gate.
    if (!is.na(bd)) {
      if (cytof_norm_on) logmsg("    [%s] beads: RETAINED for normCytof (item 8); threshold bead-removal gate skipped.", sid)
      else do_1d("beads", bd, "keep_below", fallback_pct = 0.99)
    }
  }
  # always: drop non-finite marker events (exclude _label_code, legitimately NA for unlabeled cells)
  before <- sum(keep); keep <- keep & rowSums(!is.finite(ex[, marker_ch, drop = FALSE])) == 0
  if (sum(keep) < before) logmsg("    [%s] non-finite drop: %d -> %d", sid, before, sum(keep))
  # OVER-GATING alarm: scatter/singlet must stay permissive
  scat <- do.call(rbind, stage[vapply(stage, function(s) unname(s["scatter"]) == 1, logical(1))])
  if (!is.null(scat) && nrow(scat) > 0) {
    scat_frac <- 1 - min(scat[, "out"]) / max(scat[, "in_"])
    if (scat_frac > opt$overgate_alarm)
      logmsg("  !! OVER-GATING ALARM [%s]: scatter/singlet gates jointly removed %.1f%% of events (> %.0f%%). This is the exact failure mode where tight scatter/singlet gates silently discard REAL cells and bias downstream populations. ACTION: loosen gates; if results disagree with manual gating, inspect the scatter/singlet gate FIRST (not the marker/viability threshold); do NOT rationalize the anomaly as biology until this is ruled out.",
             sid, 100 * scat_frac, 100 * opt$overgate_alarm)
  }
  # VIABILITY over-removal alarm: if the live/dead gate removed > viab_max_remove, warn loudly.
  # Neutrophils take up viability dye non-specifically; a valley cutoff can gate out live
  # granulocytes. The live_dead_lineage_* diagnostic figures make this visible.
  if (!is.null(stage$live_dead)) {
    viab_frac <- as.numeric(stage$live_dead["frac"])
    if (is.finite(viab_frac) && viab_frac > opt$viab_max_remove)
      logmsg("  !! VIABILITY OVER-REMOVAL [%s]: live/dead gate removed %.1f%% of events (> %.0f%%). Neutrophils take up viability dye without being dead -- a valley cutoff can gate out live granulocytes. Inspect the live_dead_lineage_* diagnostic figures. If the sample genuinely has high dead-cell fraction, override with --thresholds or raise --viab-max-remove.",
             sid, 100 * viab_frac, 100 * opt$viab_max_remove)
  }
  logmsg("  %s: %d -> %d cells kept (%.1f%% removed total)", sid, n0, sum(keep), 100 * (1 - sum(keep) / n0))
  list(frame = ff[keep, ], rows = rows)
}

if (qc_on) {
  logmsg("--- QC gating (data-driven / reviewable / multivariate) ---")
  logmsg("PRINCIPLE: scatter/singlet gates default to PERMISSIVE. Over-gating removes REAL cells")
  logmsg("           silently and biases downstream populations/readouts -- it does NOT fail loudly.")
  logmsg("           HONESTY: unimodal channel or shallow valley -> NO cutoff invented -> conservative percentile + REVIEW flag.")
  logmsg("method=%s | multivariate=%s | review=%s | scope=%s | valley-min-depth=%.2f | dip-alpha=%.3g",
         opt$gate_method, opt$multivariate, opt$gate_review, opt$threshold_scope, opt$valley_min_depth, opt$dip_alpha)
  logmsg("           singlet band = median +/- %g*MAD | debris floor = %.3g pct | over-gate alarm = %.0f%% | per-gate warn = %.0f%% | viab-max-remove = %.0f%%",
         opt$singlet_mad_k, opt$debris_pct, 100 * opt$overgate_alarm, 100 * PER_GATE_WARN, 100 * opt$viab_max_remove)
  CONTROLS <- load_controls(opt$controls)
  if (!is.null(CONTROLS)) logmsg("Controls: %d channel(s) loaded -> control-anchored cutoffs take precedence.", length(CONTROLS))
  # log lineage-marker resolution for the viability x lineage live/dead diagnostic.
  if (length(LINEAGE_CH)) {
    logmsg("Lineage markers for viability x lineage diagnostic: %s",
           paste(sprintf("%s -> %s", names(LINEAGE_CH), unname(LINEAGE_CH)), collapse = ", "))
  } else {
    req <- if (is.na(opt$lineage_markers)) paste(DEFAULT_LINEAGE_MARKERS, collapse = ",")
           else opt$lineage_markers
    logmsg("live_dead_lineage: no lineage markers (%s) found in panel; viability x lineage diagnostic skipped (no error).", req)
  }

  if (opt$gate_engine == "opencyto") {
    # ---- item 6 (v2.2.0): openCyto / flowWorkspace hierarchical gating backend (opt-in) ----
    # Replaces the builtin per-gate template with a real GatingSet + gatingTemplate hierarchy.
    # Returns the terminal-population flowSet keyed by the SAME sampleNames (raw exprs +
    # "_label_code" preserved), so the downstream prepData + label-extraction contract below is
    # unchanged. Data-driven cutoffs, harmonization and the propose/apply loop are builtin-only.
    if (!is.null(CONTROLS))
      logmsg("NOTE: --controls anchor builtin cutoffs; the openCyto backend uses its gatingTemplate methods instead.")
    if (opt$gate_review == "propose")
      logmsg("NOTE: --gate-review propose (default) is a builtin-engine checkpoint; the openCyto backend has no propose/apply loop -- proceeding to the SCE unattended.")
    oc <- run_opencyto_gating(fs, modality = modality, template_path = opt$gate_template,
                              transform = transform, cofactor = cofactor,
                              script_dir = SCRIPT_DIR, outdir = opt$outdir, logmsg = logmsg)
    fs <- oc$fs
    gate_hierarchy <- oc$hierarchy
    logmsg("openCyto gating complete: terminal population handed to clustering (see gating_hierarchy.txt).")
  } else {
  template <- NULL
  thresholds_supplied <- !is.na(opt$thresholds) && file.exists(opt$thresholds)
  if (opt$gate_review == "apply") {
    if (!thresholds_supplied) stop("--gate-review apply requires --thresholds <edited csv>")
    template <- read_threshold_template(opt$thresholds); logmsg("PASS 2 (apply): applying edited thresholds from %s (%d rows).", opt$thresholds, nrow(template))
  } else if (thresholds_supplied) {
    template <- read_threshold_template(opt$thresholds); logmsg("Applying supplied thresholds from %s (%d rows).", opt$thresholds, nrow(template))
    if (opt$gate_review == "propose")
      logmsg("--thresholds supplied with --gate-review propose: thresholds applied, propose-stop skipped (proceeding to SCE).")
  }

  run_pass <- function(tmpl) {
    sns <- flowCore::sampleNames(fs); frames <- list(); allrows <- list()
    for (s in sns) { res <- gate_frame(fs[[s]], s, tmpl); frames[[s]] <- res$frame; allrows <- c(allrows, res$rows) }
    fs_new <- as(frames, "flowSet"); flowCore::sampleNames(fs_new) <- sns
    list(fs = fs_new, rows = allrows)
  }
  pass <- run_pass(template)

  # pooled scope: harmonize each gate to the across-sample median cutoff, then re-apply
  if (opt$threshold_scope == "pooled" && is.null(template)) {
    df <- do.call(rbind, pass$rows); num <- suppressWarnings(as.numeric(df$final_cutoff))
    keeprow <- !is.na(num) & df$direction %in% c("keep_below", "keep_above")
    if (any(keeprow)) {
      agg <- aggregate(num[keeprow], by = list(gate = df$gate[keeprow], direction = df$direction[keeprow]), FUN = function(z) stats::median(z, na.rm = TRUE))
      pooled <- data.frame(sample_id = "ALL", gate = agg$gate, channel_x = "", channel_y = "", method = "pooled_median",
                           proposed_cutoff = agg$x, direction = agg$direction, pct_removed = NA, valley_confidence = NA,
                           unimodal = "NA", status = "pooled", final_cutoff = agg$x, apply = "Y", notes = "median across samples", stringsAsFactors = FALSE)
      logmsg("threshold-scope=pooled: harmonizing %d gate(s) to across-sample median cutoffs and re-applying.", nrow(pooled))
      pass <- run_pass(pooled)
    } else logmsg("threshold-scope=pooled requested but no editable 1D cutoffs to pool; using per-sample result.")
  }

  # batch scope (item 4): harmonize per-sample cutoffs WITHIN each metadata batch by
  # confidence-weighted shrinkage toward the (gate,batch) consensus, then re-apply. Falls back
  # to pooled harmonization (+ warning) when no usable batch column is present.
  if (opt$threshold_scope == "batch" && is.null(template)) {
    have_batch <- ("batch" %in% names(md)) && !all(is.na(md$batch))
    if (!have_batch) {
      logmsg("threshold-scope=batch requested but metadata has no usable 'batch' column; FALLING BACK to pooled harmonization.")
      df <- do.call(rbind, pass$rows); num <- suppressWarnings(as.numeric(df$final_cutoff))
      keeprow <- !is.na(num) & df$direction %in% c("keep_below", "keep_above")
      if (any(keeprow)) {
        agg <- aggregate(num[keeprow], by = list(gate = df$gate[keeprow], direction = df$direction[keeprow]), FUN = function(z) stats::median(z, na.rm = TRUE))
        pooled <- data.frame(sample_id = "ALL", gate = agg$gate, channel_x = "", channel_y = "", method = "pooled_median",
                             proposed_cutoff = agg$x, direction = agg$direction, pct_removed = NA, valley_confidence = NA,
                             unimodal = "NA", status = "pooled", final_cutoff = agg$x, apply = "Y", notes = "median across samples (batch fallback)", stringsAsFactors = FALSE)
        pass <- run_pass(pooled)
      }
      harmonization$scope <- "batch->pooled(fallback)"
    } else {
      # gate rows are keyed by sampleNames(fs); map each to its batch via md (file_name, then
      # sample_id, then extension-stripped sample_id) so the join is robust to .fcs extensions.
      sn <- flowCore::sampleNames(fs)
      idx <- match(sn, md$file_name)
      if (anyNA(idx)) idx <- match(sn, md$sample_id)
      if (anyNA(idx)) idx <- match(tools::file_path_sans_ext(sn), md$sample_id)
      batch_map <- data.frame(sample_id = sn, batch = as.character(md$batch)[idx], stringsAsFactors = FALSE)
      if (anyNA(batch_map$batch))
        logmsg("WARNING: %d/%d sample(s) could not be matched to a metadata batch (batch=NA); those stay per-sample.",
               sum(is.na(batch_map$batch)), length(sn))
      hz <- harmonize_cutoffs(do.call(rbind, pass$rows), batch_map,
                              shrink = opt$harmonize_shrink, cutoff_col = "final_cutoff")
      logmsg("threshold-scope=batch: harmonized %d (gate,batch) group(s) across %d batch(es) (shrink=%.2f) and re-applying.",
             hz$n_groups_harmonized, hz$n_batches, hz$shrink)
      pass <- run_pass(hz$rows)
      harmonization$scope <- "batch"; harmonization$n_batches <- hz$n_batches
      harmonization$n_groups_harmonized <- hz$n_groups_harmonized
    }
  }

  tmpl_path <- file.path(opt$outdir, "gating_thresholds_template.csv")
  write_threshold_template(pass$rows, tmpl_path)
  logmsg("Wrote gating threshold template: %s (%d rows)", tmpl_path, length(pass$rows))
  logmsg("Wrote per-gate diagnostic figures to: %s (gate_<sample>_<gate>.png)", figdir)

  should_stop <- (opt$gate_review == "propose") && !thresholds_supplied
  if (should_stop) {
    logmsg("=== PASS 1 (propose) COMPLETE -- human-in-the-loop checkpoint ===")
    logmsg("ACTION: review %s and the gate_*.png figures; edit final_cutoff and apply (Y/N);", tmpl_path)
    logmsg("        then rerun with:  --gate-review apply --thresholds <your_edited.csv>")
    logmsg("(No SCE is written in propose mode by design.)")
    logmsg("(To run unattended without review: --gate-review auto)")
    quit(save = "no", status = 0)
  }
  fs <- pass$fs
  }
  logmsg("NOTE: CyTOF bead *normalization* (CATALYST::normCytof) is recommended when bead channels")
  logmsg("      are present; this script does threshold-based bead *removal*. See references/qc_gating.md.")
} else {
  logmsg("QC gating SKIPPED (--qc off). Use only for known pre-cleaned data (e.g. HDCytoData benchmarks).")
}


# ------------------------------------------------------------------ build CATALYST SCE + transform
# Extract embedded per-cell labels from the (post-QC) flowSet before prepData, then
# strip the helper column so prepData only sees marker channels.
if (!is.null(lab_levels)) {
  pop_ordered <- unlist(lapply(flowCore::sampleNames(fs), function(s) {
    ex <- flowCore::exprs(fs[[s]])
    codes <- ex[, "_label_code"]
    lv <- ifelse(is.na(codes), NA_character_, lab_levels[as.integer(codes)])
    lv
  }))
  fs <- flowCore::fsApply(fs, function(ff) {
    ex <- flowCore::exprs(ff); ex <- ex[, setdiff(colnames(ex), "_label_code"), drop = FALSE]
    ff@exprs <- ex; ff
  })
}
md$file_name <- as.character(md$file_name)
factors <- intersect(c("condition", "batch", "patient"), colnames(md))
sce <- CATALYST::prepData(
  fs, panel = panel, md = md,
  features = panel$fcs_colname,
  panel_cols = list(channel = "fcs_colname", antigen = "antigen", class = "marker_class"),
  md_cols   = list(file = "file_name", id = "sample_id", factors = factors),
  transform = (transform == "arcsinh"),
  cofactor  = if (transform == "arcsinh" && !is.na(cofactor)) cofactor else 5,
  FACS = (modality == "flow"))

# For flow with logicle (or estimated arcsinh), apply the chosen transform to the exprs assay.
if (modality == "flow" && transform == "logicle") {
  tm <- rownames(sce)[SummarizedExperiment::rowData(sce)$marker_class == "type"]
  ex <- SummarizedExperiment::assay(sce, "counts")
  lgcl <- flowCore::estimateLogicle(flowCore::flowFrame(t(ex[tm, , drop = FALSE])), channels = tm)
  ex_t <- ex
  ex_t[tm, ] <- t(flowCore::exprs(flowCore::transform(flowCore::flowFrame(t(ex[tm, , drop = FALSE])), lgcl)))
  SummarizedExperiment::assay(sce, "exprs") <- ex_t
  logmsg("Applied per-channel logicle transform to %d type markers (flowCore::estimateLogicle).", length(tm))
} else if (modality == "flow" && transform == "arcsinh" && is.na(cofactor)) {
  tm <- rownames(sce)[SummarizedExperiment::rowData(sce)$marker_class == "type"]
  cf <- est_arcsinh_cofactor(t(SummarizedExperiment::assay(sce, "counts")[tm, , drop = FALSE]))
  SummarizedExperiment::assay(sce, "exprs") <- asinh(SummarizedExperiment::assay(sce, "counts") / cf)
  cofactor <- cf; logmsg("Applied estimated arcsinh cofactor=%.1f (flow).", cf)
}

# attach per-cell ground-truth labels if available (benchmark path)
if (!is.null(pop_ordered)) {
  if (length(pop_ordered) == ncol(sce)) { sce$population_id <- factor(pop_ordered); logmsg("Attached %d per-cell labels as population_id.", ncol(sce)) }
  else logmsg("WARNING: label length (%d) != n cells (%d); labels not attached.", length(pop_ordered), ncol(sce))
}

# ------------------------------------------------------------------ CyTOF bead normalization (item 8)
# Opt-in (default off). CATALYST::normCytof normalizes signal to bead intensity over acquisition
# time and removes bead (and bead-bead doublet / low-signal bead) events. Applied AFTER labels
# are attached so population_id rides along in colData and stays aligned. When off (default), the
# SCE is identical to v2.1.0 (no drift).
if (cytof_norm_on) {
  beads_arg <- if (opt$beads %in% c("dvs", "beta")) opt$beads else suppressWarnings(as.numeric(strsplit(opt$beads, ",")[[1]]))
  logmsg("--- CyTOF bead normalization (item 8): CATALYST::normCytof (beads=%s, k=%d) ---", opt$beads, opt$cytof_norm_k)
  n_before <- ncol(sce)
  nc <- tryCatch(
    CATALYST::normCytof(sce, beads = beads_arg, k = opt$cytof_norm_k,
                        assays = c("counts", "exprs"), overwrite = TRUE,
                        transform = TRUE, plot = FALSE, verbose = FALSE),
    error = function(e) e)
  if (inherits(nc, "error")) {
    logmsg("WARNING: normCytof failed (%s); proceeding WITHOUT bead normalization.", conditionMessage(nc))
  } else {
    sce_norm <- if (is.list(nc) && !is.null(nc$data)) nc$data else nc
    n_removed <- if (is.list(nc) && !is.null(nc$removed)) ncol(nc$removed) else (n_before - ncol(sce_norm))
    n_beads   <- if (is.list(nc) && !is.null(nc$beads)) ncol(nc$beads) else NA_integer_
    sce <- sce_norm
    cytof_norm_summary$applied  <- TRUE
    cytof_norm_summary$n_removed <- as.integer(n_removed)
    cytof_norm_summary$n_beads   <- if (is.na(n_beads)) NA_integer_ else as.integer(n_beads)
    logmsg("normCytof applied: %d -> %d cells (%d removed; %s bead events identified).",
           n_before, ncol(sce), n_removed, ifelse(is.na(n_beads), "NA", as.character(n_beads)))
  }
}

# ------------------------------------------------------------------ QC summary + save
tm <- rownames(sce)[SummarizedExperiment::rowData(sce)$marker_class == "type"]
ex <- SummarizedExperiment::assay(sce, "exprs")
logmsg("--- Post-QC SCE ---")
logmsg("cells=%d | type_markers=%d | samples=%d", ncol(sce), length(tm), length(unique(sce$sample_id)))
logmsg("non-finite events in exprs: %d", sum(!is.finite(ex)))
logmsg("marker range: min=%.3f max=%.3f", min(ex[tm, ], na.rm = TRUE), max(ex[tm, ], na.rm = TRUE))
S4Vectors::metadata(sce)$qc <- list(modality = modality, transform = transform, cofactor = cofactor,
                                    compensation = comp_applied, qc_gating = qc_on, provenance = prov,
                                    gate_params = list(singlet_mad_k = opt$singlet_mad_k,
                                                       debris_pct = opt$debris_pct,
                                                       overgate_alarm = opt$overgate_alarm),
                                    # ---- v2.2.0 (diagnostics-on / removal-opt-in) ----
                                    time_qc = time_qc_summary,
                                    compensation_diag = comp_info,
                                    harmonization = harmonization,
                                    cytof_norm = cytof_norm_summary,
                                    gate_engine = opt$gate_engine,
                                    gate_hierarchy = gate_hierarchy)
saveRDS(sce, file.path(opt$outdir, "sce_prepped.rds"))
logmsg("Saved: %s", file.path(opt$outdir, "sce_prepped.rds"))
logmsg("=== 01 complete ===")
