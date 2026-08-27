#!/usr/bin/env Rscript
# =====================================================================================
# gating_opencyto.R  --  item 6 (v2.2.0): openCyto / flowWorkspace hierarchical gating
#                        backend for 01_load_and_qc.R (OPT-IN via --gate-engine opencyto).
#
# WHY: the builtin engine is a flat, data-driven per-gate template (one editable cutoff per
# gate, composed by intersection). Some labs prefer a REPRODUCIBLE, SHAREABLE gating
# HIERARCHY expressed as an openCyto gatingTemplate CSV (a real GatingSet with parent/child
# populations, flowClust/mindensity/singletGate methods, etc.). This module runs that stack
# and hands the terminal population back to the pipeline unchanged.
#
# DESIGN CONTRACT (must match the builtin `fs <- pass$fs` handoff exactly):
#   input  fs : per-sample flowSet, already COMPENSATED (flow) as the pipeline does before QC,
#               RAW (untransformed) values, possibly carrying a helper "_label_code" column
#               (benchmark ground-truth codes) that MUST survive to downstream label extraction.
#   output fs : SAME sampleNames, subset to the terminal (leaf) population, RAW exprs,
#               "_label_code" preserved. prepData owns the real transform, so we never hand
#               transformed data back.
#
# HOW: the GatingSet is used ONLY to COMPUTE a per-sample, root-relative (absolute) logical
# index for the terminal population (gh_pop_get_indices returns length == total events). That
# index is applied POSITIONALLY to the ORIGINAL raw flowFrame (which still has "_label_code"),
# so the transform used for gating never leaks into the returned data.
#
# This file is sourced LAZILY by 01_load_and_qc.R only when --gate-engine opencyto is set, so
# v2.1.0 (builtin) users never need the openCyto/flowWorkspace Bioconductor stack installed.
# =====================================================================================

# --- locate a shipped default gatingTemplate for the modality --------------------------------
# assets/ is a sibling of scripts/ (script_dir): <pkg>/scripts/gating_opencyto.R + <pkg>/assets/.
opencyto_default_template <- function(modality, script_dir) {
  fn <- if (identical(modality, "cytof")) "gating_template_cytof.csv" else "gating_template_flow.csv"
  file.path(dirname(script_dir), "assets", fn)
}

# --- per-modality transformList used ONLY for gating (flowClust/mindensity need a sane scale) -
# flow  : logicle on fluorescence channels; FSC/SSC/Time stay LINEAR (flowClust scatter gates
#         expect linear scatter). cytof : arcsinh(cofactor) on mass channels; Time + Event_length
#         stay LINEAR. Helper "_label_code" is never transformed.
opencyto_gating_transform <- function(fs, modality, cofactor) {
  cn     <- flowCore::colnames(fs)
  helper <- "_label_code"
  if (identical(modality, "cytof")) {
    lin  <- c("Time", "Event_length")
    mass <- setdiff(cn, c(lin, helper))
    cof  <- if (!is.null(cofactor) && !is.na(cofactor)) cofactor else 5
    if (!length(mass)) return(NULL)
    flowCore::transformList(mass, flowCore::arcsinhTransform(a = 0, b = 1 / cof, c = 0))
  } else {
    scatter <- grep("FSC|SSC|Time", cn, ignore.case = TRUE, value = TRUE)
    fluor   <- setdiff(cn, c(scatter, helper))
    if (!length(fluor)) return(NULL)
    flowCore::estimateLogicle(fs[[1]], channels = fluor)
  }
}

# --- main entry point ------------------------------------------------------------------------
# Returns list(fs = filtered flowSet, hierarchy = <list for SCE metadata>, terminal = <chr>,
#              template = <path>, hierarchy_file = <path or NA>).
run_opencyto_gating <- function(fs, modality, template_path = NA, transform = NA,
                                cofactor = NA, script_dir = "scripts",
                                outdir = ".", logmsg = message) {

  # hard dependency check (opt-in feature -> fail clearly, never silently downgrade)
  need <- c("flowWorkspace", "openCyto")
  miss <- need[!vapply(need, requireNamespace, logical(1), quietly = TRUE)]
  if (length(miss))
    stop(sprintf(paste0("--gate-engine opencyto requires the Bioconductor package(s): %s. ",
                        "Install them (BiocManager::install) or use --gate-engine builtin."),
                 paste(miss, collapse = ", ")))
  suppressPackageStartupMessages({ library(flowWorkspace); library(openCyto) })

  # 1. resolve the gatingTemplate (explicit --gate-template wins; else shipped default) --------
  tmpl_path <- if (!is.null(template_path) && !is.na(template_path) && nzchar(template_path))
                 template_path else opencyto_default_template(modality, script_dir)
  if (!file.exists(tmpl_path))
    stop(sprintf("openCyto gatingTemplate not found: %s", tmpl_path))
  logmsg("openCyto backend: modality=%s | template=%s", modality, basename(tmpl_path))

  sns <- flowCore::sampleNames(fs)

  # 2. strip the helper label column for GATING only (indices re-applied positionally to the
  #    ORIGINAL fs, which keeps "_label_code"); avoids NA label values entering the h5 backend.
  helper   <- "_label_code"
  keep_cn  <- setdiff(flowCore::colnames(fs), helper)
  fs_gate  <- if (length(keep_cn) < length(flowCore::colnames(fs))) fs[, keep_cn] else fs

  # 3. transform a COPY for gating (never returned)
  tl   <- opencyto_gating_transform(fs_gate, modality, cofactor)
  fs_t <- if (is.null(tl)) fs_gate else flowCore::transform(fs_gate, tl)

  # 4. build the GatingSet and run the template
  gs <- flowWorkspace::GatingSet(fs_t)
  gt <- openCyto::gatingTemplate(tmpl_path)
  logmsg("openCyto: running gatingTemplate over %d sample(s) ...", length(sns))
  openCyto::gt_gating(gt, gs)

  # 5. resolve populations, parents and the terminal (leaf) population ------------------------
  pf      <- flowWorkspace::gs_get_pop_paths(gs, path = "full")   # "root","/A","/A/B",...
  nonroot <- setdiff(pf, "root")
  if (!length(nonroot)) stop("openCyto produced no populations (empty gatingTemplate?).")
  is_leaf <- !vapply(nonroot, function(nd) any(startsWith(nonroot, paste0(nd, "/"))), logical(1))
  leaves  <- nonroot[is_leaf]
  depth   <- function(p) nchar(gsub("[^/]", "", p))
  if (length(leaves) == 1L) {
    terminal <- leaves
  } else {
    terminal <- leaves[which.max(vapply(leaves, depth, integer(1)))]
    logmsg(paste0("openCyto WARNING: template hierarchy has %d leaf populations (%s); ",
                  "using the deepest one as the retained set: %s. Supply a linear template ",
                  "or a single leaf for unambiguous downstream gating."),
           length(leaves), paste(basename(leaves), collapse = ", "), basename(terminal))
  }
  parent_of <- setNames(
    vapply(nonroot, function(nd) tryCatch(flowWorkspace::gs_pop_get_parent(gs, nd),
                                          error = function(e) NA_character_), character(1)),
    nonroot)
  logmsg("openCyto populations: %s", paste(c("root", basename(nonroot)), collapse = " -> "))
  logmsg("openCyto terminal (retained) population: %s", basename(terminal))

  # 6. per-sample terminal index -> subset ORIGINAL raw fs (keeps "_label_code"); collect the
  #    full per-node absolute counts for the hierarchy record. ----------------------------------
  frames    <- vector("list", length(sns)); names(frames) <- sns
  hier_rows <- list(); ret_rows <- list()
  for (s in sns) {
    tot <- nrow(flowCore::exprs(fs[[s]]))
    cnts <- setNames(c(tot, vapply(nonroot, function(nd)
              sum(flowWorkspace::gh_pop_get_indices(gs[[s]], nd)), integer(1))), c("root", nonroot))
    for (nd in c("root", nonroot)) {
      par <- if (identical(nd, "root")) NA_character_ else parent_of[[nd]]
      pcp <- if (is.na(par)) NA_real_ else 100 * cnts[[nd]] / cnts[[par]]
      hier_rows[[length(hier_rows) + 1L]] <- data.frame(
        sample_id = s, population = nd, parent = par,
        count = as.integer(cnts[[nd]]), pct_of_parent = pcp, stringsAsFactors = FALSE)
    }
    idx <- flowWorkspace::gh_pop_get_indices(gs[[s]], terminal)
    if (length(idx) != tot)
      stop(sprintf("openCyto index length (%d) != events (%d) for %s.", length(idx), tot, s))
    frames[[s]] <- fs[[s]][idx, ]
    ret_rows[[length(ret_rows) + 1L]] <- data.frame(
      sample_id = s, input = tot, retained = sum(idx),
      pct_removed = 100 * (1 - sum(idx) / tot), stringsAsFactors = FALSE)
  }
  fs_out <- as(frames, "flowSet"); flowCore::sampleNames(fs_out) <- sns
  hier_df <- do.call(rbind, hier_rows); ret_df <- do.call(rbind, ret_rows)

  tot_in  <- sum(ret_df$input); tot_out <- sum(ret_df$retained)
  logmsg("openCyto gating retained %d / %d events (%.1f%% removed) across %d sample(s).",
         tot_out, tot_in, 100 * (1 - tot_out / tot_in), length(sns))

  # 7. human-readable hierarchy tree (mean across samples) + per-sample retained ---------------
  hfile <- file.path(outdir, "gating_hierarchy.txt")
  hier_written <- tryCatch({
    con <- file(hfile, "w"); on.exit(close(con), add = TRUE)
    writeLines(c(
      sprintf("openCyto gating hierarchy  (engine=opencyto, modality=%s)", modality),
      sprintf("template : %s", tmpl_path),
      sprintf("samples  : %d", length(sns)),
      sprintf("terminal : %s  (this population is handed to clustering)", terminal),
      "",
      "Population tree  --  mean count across samples [mean % of parent]:"), con)
    ord <- c("root", nonroot[order(vapply(nonroot, depth, integer(1)))])
    for (nd in ord) {
      sub <- hier_df[hier_df$population == nd, ]
      mc  <- mean(sub$count); mp <- mean(sub$pct_of_parent)
      ind <- strrep("  ", depth(nd))
      lab <- if (identical(nd, "root")) "root" else nd
      tag <- if (identical(nd, terminal)) "   <- terminal (retained)" else ""
      line <- if (identical(nd, "root")) sprintf("%s%-42s %8.0f%s", ind, lab, mc, tag)
              else sprintf("%s%-42s %8.0f [%5.1f%%]%s", ind, lab, mc, mp, tag)
      writeLines(line, con)
    }
    writeLines(c("", "Per-sample retained (terminal population):"), con)
    for (i in seq_len(nrow(ret_df)))
      writeLines(sprintf("  %-20s input=%-8d retained=%-8d removed=%5.1f%%",
                         ret_df$sample_id[i], ret_df$input[i], ret_df$retained[i],
                         ret_df$pct_removed[i]), con)
    TRUE
  }, error = function(e) { logmsg("NOTE: could not write gating_hierarchy.txt (%s).", conditionMessage(e)); FALSE })
  if (isTRUE(hier_written)) logmsg("Wrote openCyto hierarchy: %s", hfile)

  # 8. structured record for SCE metadata (read-only, surfaced by build_manifest.R + 07) -------
  hierarchy <- list(
    engine       = "opencyto",
    template     = tmpl_path,
    populations  = basename(nonroot),
    paths        = nonroot,
    terminal     = terminal,
    n_samples    = length(sns),
    total_input  = as.integer(tot_in),
    total_retained = as.integer(tot_out),
    pct_removed  = 100 * (1 - tot_out / tot_in),
    counts       = hier_df,
    retained     = ret_df,
    hierarchy_file = if (isTRUE(hier_written)) hfile else NA_character_)

  list(fs = fs_out, hierarchy = hierarchy, terminal = terminal, template = tmpl_path,
       hierarchy_file = hierarchy$hierarchy_file)
}
