###############################################################################
# validate_design.R -- TWO ENFORCED validation gates for any simulated design
#
# Gate 1 (TYPE-I / FWER):  Under a grid of NULL configurations (no treatment
#   effect anywhere), the simulated probability of ANY rejection must not exceed
#   alpha beyond Monte-Carlo tolerance. If enrichment/closed testing is used,
#   this is the strong FWER over {H_F, H_S}. HARD STOP on violation.
#
# Gate 2 (POWER vs rpact):  For a fixed / single-hypothesis reduced version of
#   the design (no enrichment, no futility, dropout OFF), the simulated power
#   must match the analytic power from rpact's getPower{Survival,Rates,Means}
#   within a tolerance. This confirms the endpoint test statistic + boundaries
#   are correctly calibrated against an independent analytic ground truth.
#   HARD STOP on violation.
#
# Rationale for the reduced comparison: enrichment's closed test legitimately
# costs power (multiplicity), and dropout legitimately lowers power; rpact models
# neither. The apples-to-apples benchmark is therefore the single-hypothesis,
# no-dropout design -- exactly what this gate simulates.
#
# Both gates return a data.frame of evidence AND (by default) enforce.
###############################################################################

.this_dir <- tryCatch(dirname(sys.frame(1)$ofile), error = function(e) NULL)
if (is.null(.this_dir) || !nzchar(.this_dir)) {
  cand <- c("scripts", ".")
  for (d in cand) if (file.exists(file.path(d, "simulate_trial.R"))) { .this_dir <- d; break }
}
if (is.null(.this_dir)) .this_dir <- "."
source(file.path(.this_dir, "simulate_trial.R"))
suppressMessages(library(rpact))

## Monte-Carlo tolerance for the FWER gate: alpha + z * SE(alpha).
mc_fwer_tolerance <- function(alpha, nsim, z = 3) {
  alpha + z * sqrt(alpha * (1 - alpha) / nsim)
}

## ---------------------------------------------------------------------------
## GATE 1 : FWER under the null
##   base_scenario : the design to test (its effect params are overridden to null)
##   null_variants : list of extra param overrides to widen the null space
##                   (e.g. different prevalence, least-favorable configs)
## ---------------------------------------------------------------------------
gate_fwer <- function(base_scenario, null_variants = NULL,
                      nsim = 10000, ncores = 4, seed0 = 100,
                      enforce = TRUE, verbose = TRUE) {
  ep <- base_scenario$endpoint %||% "tte"
  # null override by endpoint (no effect in either subgroup)
  null_over <- switch(ep,
    "tte"        = list(hr_pos = 1.0, hr_neg = 1.0),
    "binary"     = list(p_trt_pos = base_scenario$p_ctrl,
                        p_trt_neg = base_scenario$p_ctrl,
                        rr_pos = NULL, rr_neg = NULL, or_pos = NULL, or_neg = NULL),
    "continuous" = list(mean_trt_pos = base_scenario$mean_ctrl,
                        mean_trt_neg = base_scenario$mean_ctrl,
                        delta_pos = 0, delta_neg = 0))
  base_null <- modifyList(base_scenario, null_over)

  variants <- c(list(list(label = "Global null")), null_variants %||% list())
  rows <- list()
  for (i in seq_along(variants)) {
    v <- variants[[i]]
    lab <- v$label %||% paste0("null_variant_", i)
    v$label <- NULL
    sc <- modifyList(base_null, v)
    r <- run_scenario(sc, nsim = nsim, seed = seed0 + i, ncores = ncores, label = lab)
    rows[[i]] <- data.frame(config = lab, FWER_any = r$power_any,
                            err_F = r$power_F, err_S = r$power_S,
                            nsim = r$nsim, stringsAsFactors = FALSE)
  }
  tab <- do.call(rbind, rows)
  alpha <- base_scenario$alpha %||% 0.025
  tol <- mc_fwer_tolerance(alpha, nsim)
  tab$alpha <- alpha; tab$mc_tol <- round(tol, 4)
  tab$pass <- tab$FWER_any <= tol
  worst <- max(tab$FWER_any)
  if (verbose) {
    cat(sprintf("\n=== GATE 1: FWER under the null (alpha=%.3f, tol=%.4f, nsim=%d) ===\n",
                alpha, tol, nsim))
    print(tab[, c("config","FWER_any","err_F","err_S","mc_tol","pass")], row.names = FALSE)
    cat(sprintf("Worst-case FWER = %.4f  =>  %s\n", worst,
                if (all(tab$pass)) "PASS" else "FAIL"))
  }
  if (enforce && !all(tab$pass)) {
    stop(sprintf("GATE 1 FAILED: max FWER %.4f exceeds tolerance %.4f. ",
                 worst, tol),
         "The design does NOT control type-I error; not producing a report.")
  }
  invisible(list(table = tab, pass = all(tab$pass), worst = worst, gate = "fwer"))
}

## ---------------------------------------------------------------------------
## GATE 2 : power vs rpact analytic benchmark (single-hypothesis reduced design)
##   Provide the reduced design's info (endpoint, alpha, info_frac, effect grid).
##   For each effect the fixed-design information target is computed from rpact's
##   getSampleSize* so simulation and analytic power refer to the SAME design.
## ---------------------------------------------------------------------------
gate_power_vs_rpact <- function(endpoint, alpha = 0.025, info_frac = 0.5,
                                spending = "asOF", tol = 0.02, nsim = 5000,
                                ncores = 4, seed0 = 500, enforce = TRUE,
                                verbose = TRUE,
                                # tte:
                                median_ctrl = 18.9, hr_grid = c(0.60, 0.65, 0.70),
                                accrual_months = 24,
                                # binary:
                                p_ctrl = 0.20, p_trt_grid = c(0.35, 0.40, 0.45),
                                # continuous:
                                mean_ctrl = 0, sd = 1, delta_grid = c(0.35, 0.45, 0.55),
                                # design target power used to size N/events:
                                target_power = 0.80) {
  info_rates <- sort(unique(c(info_frac, 1)))
  gsd <- getDesignInverseNormal(kMax = length(info_rates), alpha = alpha, sided = 1,
                                informationRates = info_rates, typeOfDesign = spending)
  rows <- list()

  if (endpoint == "tte") {
    for (hr in hr_grid) {
      # Size the EVENT-DRIVEN design: fix a realistic follow-up so the required
      # events are attainable within a generous pool (N > events). rpact power is
      # taken in event-driven mode (maxNumberOfEvents fixed, large subject pool),
      # and the simulator uses the SAME event target with a large N pool and no
      # administrative truncation -- so both refer to the identical design.
      ss <- getSampleSizeSurvival(gsd, hazardRatio = hr,
                                  lambda2 = log(2)/median_ctrl,
                                  dropoutRate1 = 0, dropoutRate2 = 0,
                                  accrualTime = accrual_months, followUpTime = 18,
                                  sided = 1, alpha = alpha, beta = 1 - target_power)
      events <- ceiling(max(ss$maxNumberOfEvents))
      Npool  <- 2000L                      # large pool => events accrue early (matches Schoenfeld regime)
      pw <- getPowerSurvival(gsd, hazardRatio = hr, lambda2 = log(2)/median_ctrl,
                             maxNumberOfEvents = events, maxNumberOfSubjects = Npool,
                             dropoutRate1 = 0, dropoutRate2 = 0,
                             accrualTime = accrual_months, sided = 1, alpha = alpha,
                             directionUpper = FALSE)
      rpact_pw <- as.numeric(max(pw$overallReject))
      sc <- list(endpoint = "tte", median_ctrl = median_ctrl, hr_pos = hr, hr_neg = hr,
                 prevalence = 1.0, N_max = Npool, target_events = events,
                 info_frac = info_frac, spending = spending, accrual_months = accrual_months,
                 dropout_rate = 0, allow_enrich = FALSE, allow_futility = FALSE,
                 allow_efficacy = (length(info_rates) > 1), allow_ssr = FALSE,
                 dist = "exponential", max_followup = 1000)
      sim <- run_scenario(sc, nsim = nsim, seed = seed0, ncores = ncores)
      rows[[length(rows)+1]] <- data.frame(
        endpoint="tte", effect=sprintf("HR=%.2f", hr), N=events, info=events,
        rpact_power=round(rpact_pw,3), sim_power=round(sim$power_F,3),
        abs_diff=round(abs(rpact_pw - sim$power_F),3), stringsAsFactors=FALSE)
    }

  } else if (endpoint == "binary") {
    for (pt in p_trt_grid) {
      ss <- getSampleSizeRates(gsd, pi1 = pt, pi2 = p_ctrl, sided = 1,
                               alpha = alpha, beta = 1 - target_power)
      Nmax <- ceiling(max(ss$maxNumberOfSubjects))
      pw <- getPowerRates(gsd, pi1 = pt, pi2 = p_ctrl, maxNumberOfSubjects = Nmax,
                          sided = 1, alpha = alpha, directionUpper = TRUE)
      rpact_pw <- as.numeric(max(pw$overallReject))
      sc <- list(endpoint = "binary", p_ctrl = p_ctrl, p_trt_pos = pt, p_trt_neg = pt,
                 prevalence = 1.0, N_max = Nmax, target_events = Nmax, fu_window = 0,
                 info_frac = info_frac, spending = spending,
                 allow_enrich = FALSE, allow_futility = FALSE,
                 allow_efficacy = (length(info_rates) > 1), allow_ssr = FALSE)
      sim <- run_scenario(sc, nsim = nsim, seed = seed0, ncores = ncores)
      rows[[length(rows)+1]] <- data.frame(
        endpoint="binary", effect=sprintf("p=%.2f vs %.2f", pt, p_ctrl), N=Nmax, info=Nmax,
        rpact_power=round(rpact_pw,3), sim_power=round(sim$power_F,3),
        abs_diff=round(abs(rpact_pw - sim$power_F),3), stringsAsFactors=FALSE)
    }

  } else if (endpoint == "continuous") {
    for (d in delta_grid) {
      ss <- getSampleSizeMeans(gsd, alternative = d, stDev = sd, sided = 1,
                               alpha = alpha, beta = 1 - target_power)
      Nmax <- ceiling(max(ss$maxNumberOfSubjects))
      pw <- getPowerMeans(gsd, alternative = d, stDev = sd, maxNumberOfSubjects = Nmax,
                          sided = 1, alpha = alpha, directionUpper = TRUE)
      rpact_pw <- as.numeric(max(pw$overallReject))
      sc <- list(endpoint = "continuous", mean_ctrl = mean_ctrl, delta_pos = d,
                 delta_neg = d, sd = sd, prevalence = 1.0, N_max = Nmax,
                 target_events = Nmax, fu_window = 0, info_frac = info_frac,
                 spending = spending, higher_is_better = TRUE,
                 allow_enrich = FALSE, allow_futility = FALSE,
                 allow_efficacy = (length(info_rates) > 1), allow_ssr = FALSE)
      sim <- run_scenario(sc, nsim = nsim, seed = seed0, ncores = ncores)
      rows[[length(rows)+1]] <- data.frame(
        endpoint="continuous", effect=sprintf("delta=%.2f", d), N=Nmax, info=Nmax,
        rpact_power=round(rpact_pw,3), sim_power=round(sim$power_F,3),
        abs_diff=round(abs(rpact_pw - sim$power_F),3), stringsAsFactors=FALSE)
    }
  } else stop("Unknown endpoint: ", endpoint)

  tab <- do.call(rbind, rows)
  tab$pass <- tab$abs_diff <= tol
  worst <- max(tab$abs_diff)
  if (verbose) {
    cat(sprintf("\n=== GATE 2: power vs rpact (%s, tol=%.3f, nsim=%d) ===\n",
                endpoint, tol, nsim))
    print(tab, row.names = FALSE)
    cat(sprintf("Worst-case |diff| = %.3f  =>  %s\n", worst,
                if (all(tab$pass)) "PASS" else "FAIL"))
  }
  if (enforce && !all(tab$pass)) {
    stop(sprintf("GATE 2 FAILED: max |sim - rpact| = %.3f exceeds tol %.3f. ",
                 worst, tol),
         "The endpoint test/boundaries are not calibrated to the analytic benchmark.")
  }
  invisible(list(table = tab, pass = all(tab$pass), worst = worst, gate = "power"))
}

`%||%` <- function(a, b) if (is.null(a)) b else a
