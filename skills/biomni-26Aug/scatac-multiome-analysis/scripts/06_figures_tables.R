#!/usr/bin/env Rscript
# ============================================================================
# STAGE 06 — Provenance manifest + optional slimmed RDS + copy to results
# Loads checkpoints/05_final.rds. Writes provenance_manifest.json, optional
# slimmed final .rds, and copies all figures/tables to results_dir.
# (Figures/tables themselves are written by stages 01-05.)
# ============================================================================
suppressPackageStartupMessages({
  library(Signac); library(Seurat); library(jsonlite)
})
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
cfg <- load_config(); set.seed(cfg$project$seed)

st <- readRDS(ckpt(cfg, "05_final.rds")); obj <- st$obj

# ---- provenance manifest ----------------------------------------------------
si <- sessionInfo()
pkg_ver <- function(p) tryCatch(as.character(packageVersion(p)), error = function(e) NA)
manifest <- list(
  project = cfg$project$name,
  generated = format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"),
  genome_build = cfg$genome$build,
  seed = cfg$project$seed,
  input = list(fragments = cfg$input$fragments,
               peak_matrix_h5 = cfg$input$peak_matrix_h5,
               multiome = cfg$input$multiome),
  cells_after_QC = ncol(obj),
  n_recalled_peaks = st$n_recalled,
  n_final_clusters = st$n_final,
  n_cell_types = tryCatch(nlevels(obj$cell_type), error = function(e) NA),
  low_confidence_clusters = tryCatch(st$n_flag, error = function(e) NA),
  parameters = list(qc = cfg$qc, dimreduc = cfg$dimreduc, peaks = cfg$peaks,
                    annotation = cfg$annotation, diffaccess = cfg$diffaccess),
  package_versions = list(
    R = paste(R.version$major, R.version$minor, sep = "."),
    Signac = pkg_ver("Signac"), Seurat = pkg_ver("Seurat"),
    GenomicRanges = pkg_ver("GenomicRanges"), MACS3 = tryCatch(
      system2(if (identical(cfg$peaks$macs_path,"auto")) Sys.which("macs3") else cfg$peaks$macs_path,
              "--version", stdout = TRUE, stderr = TRUE), error = function(e) NA)),
  R_platform = si$platform
)
write_json(manifest, tabp(cfg, "provenance_manifest.json"), auto_unbox = TRUE, pretty = TRUE)
log_msg("Wrote provenance_manifest.json")

# ---- optional slimmed final RDS ---------------------------------------------
if (isTRUE(cfg$output$save_rds)) {
  out <- obj
  if (isTRUE(cfg$output$slim_rds)) {
    # drop the fragment cache to shrink; keep meta + reductions + recalled counts
    tryCatch({ Fragments(out[["recalled"]]) <- NULL }, error = function(e) NULL)
    tryCatch({ Fragments(out[["peaks"]]) <- NULL }, error = function(e) NULL)
    if ("peaks" %in% Assays(out)) out[["peaks"]] <- NULL   # keep only recalled + ACTIVITY
  }
  local_rds <- file.path(cfg$project$outdir, "final_object.rds")   # local disk (random-access)
  saveRDS(out, local_rds)
  dir.create(cfg$project$results_dir, recursive = TRUE, showWarnings = FALSE)
  system2("cp", c(local_rds, file.path(cfg$project$results_dir, "final_object.rds")))  # shell cp
  log_msg("Saved", ifelse(isTRUE(cfg$output$slim_rds), "slimmed", "full"), "RDS ->", cfg$project$results_dir)
}

# ---- copy figures + tables to results --------------------------------------
copy_to_results(cfg)
# also copy the manifest explicitly (tables copy already includes it)
log_msg("STAGE 06 done"); cat("STAGE06_OK\n")
