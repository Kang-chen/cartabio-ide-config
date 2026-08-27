###############################################################################
# run_pipeline.R -- turnkey driver: JSON config -> validated design + OC + figures.
#
# Reads a single JSON config (see references/config_schema.md) and, in order:
#   1. runs the TWO enforced validation gates (FWER + power-vs-rpact);
#      if either fails and enforcement is on, it STOPS (no report is produced);
#   2. runs the operating-characteristic grid over the config's scenarios;
#   3. runs an optional sensitivity sweep;
#   4. writes all tables (CSV) and figures (PNG+SVG);
#   5. prints the exact `build_report.py` command to render the PDF.
#
# Usage:
#   Rscript run_pipeline.R <config.json> <output_dir> [quick|thorough]
#
# The output_dir gets:  tables/{gate_fwer,gate_power,operating_characteristics,
#   sensitivity_analysis}.csv  and  figures/*.png|svg
#
# Nothing here uses real patient data; all inputs come from the config.
# Author: Biomni (Phylo) | Language: R
###############################################################################

suppressWarnings(suppressMessages({ library(jsonlite) }))

.this_dir <- tryCatch(dirname(sub("--file=", "",
   grep("--file=", commandArgs(FALSE), value = TRUE)[1])), error = function(e) ".")
if (is.na(.this_dir) || !nzchar(.this_dir)) .this_dir <- "."
source(file.path(.this_dir, "validate_design.R"))
source(file.path(.this_dir, "run_grid.R"))
source(file.path(.this_dir, "make_figures.R"))

`%||%` <- function(a, b) if (is.null(a)) b else a

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2)
  stop("Usage: Rscript run_pipeline.R <config.json> <output_dir> [quick|thorough]")
cfg_path <- args[1]; out_dir <- args[2]
preset   <- if (length(args) >= 3) args[3] else NULL   # NULL -> use config or 'quick'

cfg    <- fromJSON(cfg_path, simplifyVector = FALSE)
design <- cfg$design %||% cfg
val    <- cfg$validation %||% list()
grd    <- cfg$grid %||% list()
sensc  <- cfg$sensitivity %||% NULL
preset <- preset %||% (cfg$runtime$preset %||% "quick")
ncores <- cfg$runtime$ncores %||% 4L

TAB <- file.path(out_dir, "tables"); FIG <- file.path(out_dir, "figures")
dir.create(TAB, showWarnings = FALSE, recursive = TRUE)
dir.create(FIG, showWarnings = FALSE, recursive = TRUE)

# jsonlite returns lists; coerce scalar design fields to atomic
design <- lapply(design, function(x) if (is.list(x) && length(x) == 1) x[[1]] else x)

endpoint <- design$endpoint %||% "tte"
nsim_val <- if (identical(preset, "thorough")) 10000L else 2000L
enforce  <- isTRUE(val$enforce %||% TRUE)

cat(sprintf("\n########## PIPELINE: endpoint=%s, preset=%s ##########\n", endpoint, preset))

## ---- 1. GATE 1: FWER --------------------------------------------------------
# Build a base (global-null) scenario from the design; null_variants from config.
base_scen <- design
nv <- lapply(val$fwer_null_variants %||% list(), function(v) {
  v <- lapply(v, function(x) if (is.list(x) && length(x) == 1) x[[1]] else x); v
})
g1 <- gate_fwer(base_scen, null_variants = nv, nsim = nsim_val,
                seed0 = val$seed_fwer %||% 100, ncores = ncores, enforce = enforce)
write.csv(g1$table, file.path(TAB, "gate_fwer.csv"), row.names = FALSE)

## ---- 2. GATE 2: power vs rpact ---------------------------------------------
pg <- val$power_grid %||% list()
g2args <- list(endpoint = endpoint, alpha = design$alpha %||% 0.025,
               info_frac = design$info_frac %||% 0.5,
               spending  = design$spending %||% "asOF",
               nsim = nsim_val, seed0 = val$seed_power %||% 500,
               tol = val$power_tol %||% 0.02, ncores = ncores, enforce = enforce)
if (endpoint == "tte") {
  g2args$median_ctrl <- design$median_ctrl %||% 18.9
  g2args$hr_grid <- unlist(pg$hr_grid %||% list(0.60, 0.65, 0.70))
  g2args$accrual_months <- design$accrual_months %||% 24
} else if (endpoint == "binary") {
  g2args$p_ctrl <- design$p_ctrl %||% 0.20
  g2args$p_trt_grid <- unlist(pg$p_trt_grid %||% list(0.35, 0.40, 0.45))
} else {
  g2args$mean_ctrl <- design$mean_ctrl %||% 0
  g2args$sd <- design$sd %||% 1
  g2args$delta_grid <- unlist(pg$delta_grid %||% list(0.35, 0.45, 0.55))
}
g2 <- do.call(gate_power_vs_rpact, g2args)
write.csv(g2$table, file.path(TAB, "gate_power.csv"), row.names = FALSE)

cat(sprintf("\n>>> GATES: FWER pass=%s (worst %.4f) | POWER pass=%s (worst %.3f)\n",
            g1$pass, g1$worst, g2$pass, g2$worst))
if (enforce && !(g1$pass && g2$pass))
  stop("Enforced gate(s) failed -- pipeline halted, no operating characteristics produced.")

## ---- 3. Operating-characteristic grid --------------------------------------
scen <- grd$scenarios
if (is.null(scen)) stop("config$grid$scenarios is required (named effect scenarios).")
scen <- lapply(scen, function(v)
  lapply(v, function(x) if (is.list(x) && length(x) == 1) x[[1]] else x))
oc <- run_grid(design, scen, preset = preset, seed0 = grd$seed %||% 10, ncores = ncores)
save_grid(oc, file.path(TAB, "operating_characteristics.csv"))

## ---- 4. Sensitivity sweep (optional) ---------------------------------------
if (!is.null(sensc)) {
  sc_scen <- lapply(sensc$scenario, function(x) if (is.list(x) && length(x)==1) x[[1]] else x)
  vals <- unlist(sensc$values)
  sens <- sensitivity_grid(design, sc_scen, sensc$param, vals,
                           preset = preset, seed0 = sensc$seed %||% 50, ncores = ncores)
  save_grid(sens, file.path(TAB, "sensitivity_analysis.csv"))
  sens_csv <- file.path(TAB, "sensitivity_analysis.csv")
} else sens_csv <- NULL

## ---- 5. Figures ------------------------------------------------------------
make_figures(file.path(TAB, "operating_characteristics.csv"), FIG, sens_csv)

## ---- 6. Report command -----------------------------------------------------
py <- file.path(.this_dir, "build_report.py")
cat("\n########## PIPELINE COMPLETE ##########\n")
cat("Tables :", TAB, "\nFigures:", FIG, "\n\nRender the PDF with:\n")
cat(sprintf("  python3 %s --config %s --tables %s --figures %s --out %s\n",
            py, cfg_path, TAB, FIG, file.path(out_dir, "report.pdf")))
