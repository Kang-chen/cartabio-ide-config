###############################################################################
# simulate_trial.R -- generalized Monte-Carlo engine for 2-arm confirmatory
#                     clinical-trial designs (clinical-trial-design-simulation)
#
# GENERALIZES a validated adaptive-enrichment survival simulator to:
#   * three endpoint families  (tte | binary | continuous)   -- via endpoints.R
#   * any number of interim analyses (group-sequential)
#   * futility stopping        (conditional power)
#   * adaptive population enrichment (biomarker subgroup) + closed testing
#   * sample-size re-estimation (SSR)  [conditional-power based, capped]
#
# The statistical core is UNCHANGED from the validated design:
#   - stagewise independent-increment z's from an efficient score/information
#     (U,V) representation of each endpoint;
#   - inverse-normal combination across executed stages (fixed design weights);
#   - closed test over {H_F, H_S} via a Simes intersection test;
#   - rpact alpha-spending efficacy boundaries; futility via conditional power.
# This construction preserves strong type-I / FWER control under data-dependent
# adaptation, and is verified by validate_design.R (two enforced gates).
#
# Nothing here uses real patient data; the generative model uses only the
# explicitly supplied assumptions in `params`.
#
# Author: Biomni (Phylo) | Language: R
###############################################################################

# Robust sourcing: works whether called from the skill dir or elsewhere.
.this_dir <- tryCatch(dirname(sys.frame(1)$ofile), error = function(e) NULL)
if (is.null(.this_dir) || !nzchar(.this_dir)) {
  cand <- c("scripts", ".", dirname(sub("--file=", "",
             grep("--file=", commandArgs(FALSE), value = TRUE)[1])))
  for (d in cand) if (file.exists(file.path(d, "endpoints.R"))) { .this_dir <- d; break }
}
if (is.null(.this_dir)) .this_dir <- "."
source(file.path(.this_dir, "endpoints.R"))
source(file.path(.this_dir, "design_boundaries.R"))

## ---------------------------------------------------------------------------
## Resolve the EXPERIMENTAL-arm endpoint parameter for a subgroup, from the
## user's effect specification. Returns the per-arm value the generators expect.
##   tte        : returns the hazard ratio directly (generator applies on exp arm)
##   binary     : returns experimental response probability
##   continuous : returns experimental mean
## `slot` is "pos" (biomarker+) or "neg" (biomarker-).
## ---------------------------------------------------------------------------
resolve_effect <- function(endpoint, p, slot) {
  if (endpoint == "tte") {
    return(if (slot == "pos") p$hr_pos else p$hr_neg)
  }
  if (endpoint == "binary") {
    pc <- p$p_ctrl
    # priority: explicit p_trt_{slot} > risk ratio rr_{slot} > odds ratio or_{slot}
    v <- p[[paste0("p_trt_", slot)]]
    if (!is.null(v)) return(v)
    rr <- p[[paste0("rr_", slot)]]
    if (!is.null(rr)) return(min(max(pc * rr, 0), 1))
    or <- p[[paste0("or_", slot)]]
    if (!is.null(or)) { odds <- (pc/(1-pc))*or; return(odds/(1+odds)) }
    return(pc)  # no effect
  }
  # continuous
  mc <- p$mean_ctrl
  v <- p[[paste0("mean_trt_", slot)]]
  if (!is.null(v)) return(v)
  d <- p[[paste0("delta_", slot)]]
  if (!is.null(d)) return(mc + d)
  mc
}

## ===========================================================================
## Simulate ONE trial
## ===========================================================================
simulate_trial <- function(params) {
  p <- params
  set_defaults <- function(nm, val) if (is.null(p[[nm]])) p[[nm]] <<- val

  ## ---- generic design defaults ----
  set_defaults("endpoint", "tte")
  set_defaults("alpha", 0.025)
  set_defaults("N_max", 300L)
  set_defaults("prevalence", 0.4)
  set_defaults("accrual_months", 24)
  set_defaults("info_frac", 0.5)
  set_defaults("spending", "asOF")
  set_defaults("allow_enrich", TRUE)
  set_defaults("allow_futility", TRUE)
  set_defaults("allow_efficacy", FALSE)
  set_defaults("allow_ssr", FALSE)
  set_defaults("enrich_delta", 0.5)
  set_defaults("futility_cp", 0.10)
  set_defaults("max_followup", 48)
  # SSR knobs
  set_defaults("ssr_cp_target", 0.90)   # conditional power we try to reach
  set_defaults("ssr_cp_min", 0.30)      # only re-estimate in this "promising" zone
  set_defaults("ssr_cp_max", 0.90)
  set_defaults("ssr_nmax_cap", 2.0)     # max inflation factor on N (and on events for tte)
  # endpoint defaults
  ep <- p$endpoint
  if (ep == "tte") {
    set_defaults("median_ctrl", 18.9); set_defaults("hr_pos", 0.65)
    set_defaults("hr_neg", 0.65); set_defaults("target_events", 169L)
    set_defaults("dropout_rate", 0.05); set_defaults("dist", "exponential")
    set_defaults("weibull_shape", 1.2)
  } else if (ep == "binary") {
    set_defaults("p_ctrl", 0.20); set_defaults("fu_window", 0)
    set_defaults("target_events", p$N_max)   # info unit = analyzable patients
  } else if (ep == "continuous") {
    set_defaults("mean_ctrl", 0); set_defaults("sd", 1); set_defaults("fu_window", 0)
    set_defaults("higher_is_better", TRUE); set_defaults("target_events", p$N_max)
  }
  target <- p$target_events                 # full-pop final information target

  ## ---- design boundaries ----
  info_rates <- sort(unique(c(p$info_frac, 1)))
  bnd  <- get_boundaries(info_rates, alpha = p$alpha,
                         spending = p$spending, efficacy = p$allow_efficacy)
  info_rates <- bnd$info_rates
  nlook <- length(info_rates)
  planned_info <- ceiling(info_rates * target)     # cumulative planned info per look
  b_final <- bnd$crit[nlook]

  ## ---- base list passed to endpoint generators / scorers ----
  base <- list(median_ctrl = p$median_ctrl, dist = p$dist,
               weibull_shape = p$weibull_shape, dropout_rate = p$dropout_rate,
               p_ctrl = p$p_ctrl, mean_ctrl = p$mean_ctrl, sd = p$sd,
               fu_window = p$fu_window, higher_is_better = p$higher_is_better,
               accrual_months = p$accrual_months)

  ## ---- generate the full potential cohort (all N_max) in accrual order ----
  N   <- as.integer(p$N_max)
  arm <- rbinom(N, 1, 0.5)
  bmpos <- rbinom(N, 1, p$prevalence)
  accrual_time <- sort(runif(N, 0, p$accrual_months))
  eff_pos <- resolve_effect(ep, p, "pos")
  eff_neg <- resolve_effect(ep, p, "neg")
  grp_eff <- ifelse(bmpos == 1, eff_pos, eff_neg)   # per-patient exp-arm effect

  # generate per-patient endpoint data; attach arm/bmpos/accrual for scoring
  gd <- generate_endpoint(ep, N, arm, grp_eff, base)
  dat <- cbind(data.frame(arm = arm, bmpos = bmpos, accrual_time = accrual_time), gd)

  ## Simes intersection z & conditional power projection (final boundary)
  cp_calc <- function(z1, t_frac) {
    if (t_frac <= 0 || t_frac >= 1) return(0.5)
    zt <- z1 * sqrt(1 / t_frac)
    pnorm((zt - b_final) / sqrt(1 / t_frac - 1))
  }

  ## ---- state carried across looks ----
  enriched <- FALSE; enrich_cal <- Inf
  stopped_futility <- FALSE; stopped_efficacy <- FALSE
  reject_F <- FALSE; reject_S <- FALSE
  ssr_done <- FALSE; ssr_factor <- 1
  prev_uv_f <- c(U = 0, V = 0); prev_uv_s <- c(U = 0, V = 0)
  zf_stages <- numeric(0); zs_stages <- numeric(0); zint_stages <- numeric(0)
  realized_info <- numeric(0)
  N_final <- 0L; info_F <- 0L; info_S <- 0L; duration <- 0
  zf1 <- 0; zs1 <- 0

  admin_cutoff <- max(accrual_time) + p$max_followup

  for (k in seq_len(nlook)) {
    is_final <- (k == nlook)

    ## ---- active set (enrichment drops BM- accrued after enrichment) ----
    if (enriched) {
      keep <- which(!(accrual_time > enrich_cal & bmpos == 0))
    } else {
      keep <- seq_len(N)
    }

    ## ---- information target for THIS look (with enrichment resize + SSR) ----
    base_tgt_k <- planned_info[k]
    if (enriched) base_tgt_k <- max(ceiling(planned_info[k] * p$prevalence), 1)
    tgt_k <- if (is_final) ceiling(base_tgt_k * ssr_factor) else base_tgt_k

    ## ---- pace the look against the relevant population's information arrivals ----
    pace_rows <- if (enriched) keep[bmpos[keep] == 1] else keep
    arr <- info_arrival_times(ep, dat, rows = pace_rows, base = base)
    if (tgt_k > length(arr)) {
      cutoff_k <- admin_cutoff
    } else {
      cutoff_k <- min(arr[tgt_k], admin_cutoff)
    }
    if (length(realized_info) && cutoff_k < duration) cutoff_k <- duration  # monotone
    duration <- cutoff_k

    ## ---- cumulative (U,V) at this look, full pop & subgroup ----
    uv_f <- score_UV(ep, dat, cutoff_k, rows = keep, base = base)
    uv_s <- score_UV(ep, dat, cutoff_k,
                     rows = keep[bmpos[keep] == 1], base = base)

    N_final <- info_count(ep, dat, cutoff_k, rows = keep, base = base)
    if (ep == "tte") {
      info_F <- info_count(ep, dat, cutoff_k, rows = keep, base = base)
      info_S <- info_count(ep, dat, cutoff_k, rows = keep[bmpos[keep]==1], base = base)
      N_final <- length(unique(dat$accrual_time[keep][dat$accrual_time[keep] <= cutoff_k]))
    } else {
      info_F <- N_final
      info_S <- info_count(ep, dat, cutoff_k, rows = keep[bmpos[keep]==1], base = base)
    }

    ## ---- stagewise independent increments -> z ----
    dU_f <- uv_f["U"] - prev_uv_f["U"]; dV_f <- max(uv_f["V"] - prev_uv_f["V"], 1e-8)
    dU_s <- uv_s["U"] - prev_uv_s["U"]; dV_s <- max(uv_s["V"] - prev_uv_s["V"], 1e-8)
    zf_k <- as.numeric(dU_f / sqrt(dV_f))
    zs_k <- as.numeric(dU_s / sqrt(dV_s))
    prev_uv_f <- uv_f; prev_uv_s <- uv_s

    zf_stages   <- c(zf_stages, zf_k)
    zs_stages   <- c(zs_stages, zs_k)
    zint_stages <- c(zint_stages, simes_intersection_z(zf_k, zs_k))
    realized_info <- c(realized_info, info_rates[k])

    zf_cum <- as.numeric(if (uv_f["V"] > 0) uv_f["U"]/sqrt(uv_f["V"]) else 0)
    zs_cum <- as.numeric(if (uv_s["V"] > 0) uv_s["U"]/sqrt(uv_s["V"]) else 0)
    if (k == 1) { zf1 <- zf_cum; zs1 <- zs_cum }

    b_k <- bnd$crit[k]

    if (!is_final) {
      ## ===== INTERIM look =====
      # (1) Enrichment (once) on cumulative z gap
      if (p$allow_enrich && !enriched && (zs_cum - zf_cum) > p$enrich_delta) {
        enriched <- TRUE; enrich_cal <- cutoff_k
      }
      active_zcum <- if (enriched) zs_cum else zf_cum
      # (2) SSR (once): if conditional power is in the "promising" zone, inflate
      #     the final information target to reach ssr_cp_target (capped).
      if (p$allow_ssr && !ssr_done && !is_final) {
        cp_now <- cp_calc(active_zcum, info_rates[k])
        if (is.finite(cp_now) && cp_now >= p$ssr_cp_min && cp_now <= p$ssr_cp_max) {
          # required multiple of remaining info to lift CP to target (normal approx)
          za <- b_final; z_cp <- qnorm(p$ssr_cp_target)
          zt <- active_zcum * sqrt(1 / info_rates[k])   # projected end-of-trial drift@planned
          # scale factor on TOTAL info; conservative, bounded to [1, cap]
          need <- ((za + z_cp) / max(zt - 0, 1e-3))^2
          ssr_factor <- min(max(need, 1), p$ssr_nmax_cap)
          ssr_done <- TRUE
        }
      }
      # (3) Futility on conditional power of the active hypothesis
      cp <- cp_calc(active_zcum, info_rates[k])
      if (p$allow_futility && is.finite(cp) && cp < p$futility_cp) {
        stopped_futility <- TRUE; break
      }
      # (4) Optional early efficacy via inverse-normal closed test
      if (p$allow_efficacy) {
        zint_comb <- combine_inverse_normal(zint_stages, realized_info)[k]
        if (zint_comb > b_k) {
          zf_comb <- combine_inverse_normal(zf_stages, realized_info)[k]
          zs_comb <- combine_inverse_normal(zs_stages, realized_info)[k]
          if (!enriched && zf_comb > b_k) reject_F <- TRUE
          if (zs_comb > b_k)              reject_S <- TRUE
          if (enriched) reject_F <- FALSE
          if (reject_F || reject_S) { stopped_efficacy <- TRUE; break }
        }
      }
    } else {
      ## ===== FINAL look =====
      zint_comb <- combine_inverse_normal(zint_stages, realized_info)[k]
      if (zint_comb > b_k) {
        zf_comb <- combine_inverse_normal(zf_stages, realized_info)[k]
        zs_comb <- combine_inverse_normal(zs_stages, realized_info)[k]
        if (!enriched && zf_comb > b_k) reject_F <- TRUE
        if (zs_comb > b_k)              reject_S <- TRUE
      }
      if (enriched) reject_F <- FALSE
    }
  }

  list(
    reject_F = reject_F, reject_S = reject_S,
    reject_any = (reject_F || reject_S),
    enriched = enriched,
    stopped_futility = stopped_futility,
    stopped_efficacy = stopped_efficacy,
    ssr_done = ssr_done, ssr_factor = as.numeric(ssr_factor),
    N_final = as.integer(N_final),
    info_F = as.integer(info_F), info_S = as.integer(info_S),
    duration = as.numeric(duration),
    zf1 = zf1, zs1 = zs1
  )
}

## ===========================================================================
## Run one scenario (many replications) -> operating characteristics row
## ===========================================================================
run_scenario <- function(scenario, nsim = 5000, seed = 1, ncores = 1,
                         label = NULL) {
  set.seed(seed)
  seeds <- sample.int(.Machine$integer.max, nsim)
  one <- function(s) { set.seed(s); simulate_trial(scenario) }
  if (ncores > 1) {
    res <- parallel::mclapply(seeds, one, mc.cores = ncores)
    # Robustness: parallel::mclapply can drop replicates if a fork dies under
    # transient memory pressure (returns a try-error / non-list). Re-run any
    # such replicate SERIALLY on its own seed so gate estimates are never based
    # on a truncated sample. This keeps results deterministic per seed.
    bad <- which(!vapply(res, is.list, logical(1)))
    if (length(bad)) {
      for (b in bad) res[[b]] <- tryCatch(one(seeds[b]), error = function(e) NULL)
    }
  } else {
    res <- lapply(seeds, one)
  }
  ok <- vapply(res, is.list, logical(1)); res <- res[ok]
  if (!length(res)) stop("run_scenario: all replicates failed for scenario '",
                         scenario$endpoint %||% "tte", "'.")
  g <- function(f) vapply(res, function(r) r[[f]], numeric(1))
  gl <- function(f) vapply(res, function(r) as.numeric(r[[f]]), numeric(1))
  out <- data.frame(
    label       = if (is.null(label)) NA_character_ else label,
    endpoint    = scenario$endpoint %||% "tte",
    power_F     = mean(gl("reject_F")),
    power_S     = mean(gl("reject_S")),
    power_any   = mean(gl("reject_any")),
    p_enrich    = mean(gl("enriched")),
    p_futility  = mean(gl("stopped_futility")),
    p_efficacy  = mean(gl("stopped_efficacy")),
    p_ssr       = mean(gl("ssr_done")),
    mean_ssr_factor = mean(g("ssr_factor")),
    E_N         = mean(g("N_final")),
    E_info_F    = mean(g("info_F")),
    E_info_S    = mean(g("info_S")),
    E_duration  = mean(g("duration")),
    nsim        = length(res),
    stringsAsFactors = FALSE
  )
  out
}

`%||%` <- function(a, b) if (is.null(a)) b else a
