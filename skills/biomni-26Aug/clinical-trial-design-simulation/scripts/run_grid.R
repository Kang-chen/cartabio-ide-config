###############################################################################
# run_grid.R -- run a grid of scenarios through the trial simulator and collect
#               operating characteristics into a tidy data.frame.
#
# This is the top-level driver used to produce a design's operating
# characteristics (OC) table and the sensitivity analyses that feed the figures
# and the report. It does NOT itself validate the design -- call the two enforced
# gates in validate_design.R first (Gate 1 FWER, Gate 2 power-vs-rpact).
#
# Runtime toggle:
#   preset = "quick"    -> nsim = 1000   (fast, ~MC-SE 0.013 at p=0.5; for iteration)
#   preset = "thorough" -> nsim = 10000  (publication; ~MC-SE 0.004 at p=0.5)
#   preset = "custom"   -> use the explicit `nsim` argument
# The MC standard error of any reported proportion p is sqrt(p(1-p)/nsim); it is
# reported alongside every row so readers can judge resolution.
#
# Nothing here uses real patient data.
#
# Author: Biomni (Phylo) | Language: R
###############################################################################

.this_dir <- tryCatch(dirname(sys.frame(1)$ofile), error = function(e) NULL)
if (is.null(.this_dir) || !nzchar(.this_dir)) {
  cand <- c("scripts", ".", dirname(sub("--file=", "",
             grep("--file=", commandArgs(FALSE), value = TRUE)[1])))
  for (d in cand) if (file.exists(file.path(d, "simulate_trial.R"))) { .this_dir <- d; break }
}
if (is.null(.this_dir)) .this_dir <- "."
source(file.path(.this_dir, "simulate_trial.R"))

`%||%` <- function(a, b) if (is.null(a)) b else a

## ---------------------------------------------------------------------------
## Resolve nsim from a preset.
## ---------------------------------------------------------------------------
resolve_nsim <- function(preset = c("quick", "thorough", "custom"), nsim = NULL) {
  preset <- match.arg(preset)
  if (preset == "quick")    return(1000L)
  if (preset == "thorough") return(10000L)
  if (is.null(nsim)) stop("preset='custom' requires an explicit nsim.")
  as.integer(nsim)
}

## ---------------------------------------------------------------------------
## run_grid()
##   base      : list of design parameters shared by all scenarios (endpoint,
##               prevalence, N_max/target_events, info_frac, adaptation flags,
##               alpha, accrual, dropout, etc.)
##   scenarios : named list of lists; each element overrides `base` with the
##               effect assumptions for that scenario (e.g. hr_pos/hr_neg,
##               p_trt_pos/p_trt_neg, mean_trt_pos/mean_trt_neg, prevalence...).
##               The element NAME becomes the scenario label.
##   preset    : "quick" | "thorough" | "custom"
##   seed0     : base RNG seed; scenario i uses seed0 + i (reproducible).
##
## Returns a data.frame with one row per scenario and columns:
##   scenario, endpoint, power_F, power_S, power_any, p_enrich, p_futility,
##   p_efficacy, p_ssr, mean_ssr_factor, E_N, E_info_F, E_info_S, E_duration,
##   nsim, mc_se_any  (MC SE of power_any).
## ---------------------------------------------------------------------------
run_grid <- function(base, scenarios,
                     preset = c("quick", "thorough", "custom"),
                     nsim = NULL, seed0 = 1, ncores = 4, verbose = TRUE) {
  preset <- match.arg(preset)
  nsim   <- resolve_nsim(preset, nsim)
  stopifnot(is.list(scenarios), length(scenarios) >= 1)
  if (is.null(names(scenarios)))
    names(scenarios) <- paste0("scenario_", seq_along(scenarios))

  if (verbose)
    cat(sprintf("\n=== run_grid: %d scenario(s), endpoint=%s, preset=%s (nsim=%d) ===\n",
                length(scenarios), base$endpoint %||% "tte", preset, nsim))

  rows <- vector("list", length(scenarios))
  for (i in seq_along(scenarios)) {
    lab <- names(scenarios)[i]
    sc  <- modifyList(base, scenarios[[i]])
    r   <- run_scenario(sc, nsim = nsim, seed = seed0 + i, ncores = ncores, label = lab)
    r$scenario  <- lab
    r$mc_se_any <- sqrt(pmax(r$power_any * (1 - r$power_any), 0) / r$nsim)
    rows[[i]]   <- r
    if (verbose)
      cat(sprintf("  [%2d/%2d] %-22s power_any=%.3f (SE %.3f)  E[N]=%.0f  E[dur]=%.1f  P(enrich)=%.2f  P(SSR)=%.2f\n",
                  i, length(scenarios), lab, r$power_any, r$mc_se_any,
                  r$E_N, r$E_duration, r$p_enrich, r$p_ssr))
  }
  out <- do.call(rbind, rows)
  # tidy column order
  front <- c("scenario", "endpoint", "power_F", "power_S", "power_any", "mc_se_any")
  out <- out[, c(front, setdiff(names(out), c(front, "label")))]
  rownames(out) <- NULL
  out
}

## ---------------------------------------------------------------------------
## sensitivity_grid()
##   Vary ONE design/effect parameter over a vector of values, holding a fixed
##   effect scenario, and tabulate the OC. Useful for the report's sensitivity
##   figures (e.g. power vs prevalence, E[N] vs enrichment threshold).
##   param  : name of the field in the scenario to vary
##   values : vector of values to sweep
##   scenario : the (single) effect scenario list applied on top of base
## ---------------------------------------------------------------------------
sensitivity_grid <- function(base, scenario, param, values,
                             preset = c("quick", "thorough", "custom"),
                             nsim = NULL, seed0 = 1, ncores = 4, verbose = TRUE) {
  preset <- match.arg(preset)
  nsim   <- resolve_nsim(preset, nsim)
  if (verbose)
    cat(sprintf("\n=== sensitivity_grid: sweep '%s' over %d values (nsim=%d) ===\n",
                param, length(values), nsim))
  rows <- vector("list", length(values))
  for (i in seq_along(values)) {
    sc <- modifyList(base, scenario)
    sc[[param]] <- values[i]
    r <- run_scenario(sc, nsim = nsim, seed = seed0 + i, ncores = ncores,
                      label = sprintf("%s=%s", param, format(values[i])))
    r$param_name  <- param
    r$param_value <- values[i]
    r$mc_se_any   <- sqrt(pmax(r$power_any * (1 - r$power_any), 0) / r$nsim)
    rows[[i]]     <- r
    if (verbose)
      cat(sprintf("  %s=%-8s power_any=%.3f (SE %.3f)  E[N]=%.0f  E[dur]=%.1f\n",
                  param, format(values[i]), r$power_any, r$mc_se_any, r$E_N, r$E_duration))
  }
  out <- do.call(rbind, rows)
  front <- c("param_name", "param_value", "power_F", "power_S", "power_any", "mc_se_any")
  out <- out[, c(front, setdiff(names(out), c(front, "label", "scenario")))]
  rownames(out) <- NULL
  out
}

## ---------------------------------------------------------------------------
## save_grid()  -- write an OC table to CSV under an output dir.
## ---------------------------------------------------------------------------
save_grid <- function(tab, path) {
  dir.create(dirname(path), showWarnings = FALSE, recursive = TRUE)
  write.csv(tab, path, row.names = FALSE)
  invisible(path)
}
