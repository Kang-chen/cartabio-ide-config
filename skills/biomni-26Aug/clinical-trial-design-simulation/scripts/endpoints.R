###############################################################################
# endpoints.R  --  Endpoint abstraction for clinical-trial-design-simulation
#
# Provides, for each supported primary endpoint, a COMMON interface used by the
# simulation engine (simulate_trial.R):
#
#   generate_endpoint(endpoint, n, arm, group_effect, base, ...)
#       -> a per-patient data.frame with everything needed to (a) know when a
#          patient becomes "analyzable" on the trial calendar and (b) compute a
#          test statistic on any analysis subset.
#
#   score_UV(endpoint, dat, subset_idx)
#       -> c(U = score, V = information) computed on the CUMULATIVE analysis
#          set `subset_idx`. Oriented so POSITIVE = experimental better.
#          Stagewise (independent-increment) z-scores are obtained by the engine
#          as dU/sqrt(dV) using increments of (U, V) between looks. This score /
#          information (efficient-score) representation is what makes the
#          inverse-normal combination across stages valid for ALL endpoints.
#
#   analysis_clock(endpoint, dat)
#       -> a numeric "calendar readiness time" per patient (for time-to-event
#          this is the event calendar time; for binary/continuous it is the
#          calendar time the fixed follow-up window completes). The engine paces
#          interim/final looks against these times exactly as for events.
#
#   info_unit(endpoint) -> label used in reports ("events" | "patients").
#
# DESIGN NOTES
#  * Time-to-event: efficient score = logrank U = (obs - exp) in the control
#    group; information V = logrank variance. Identical to a standard logrank z.
#  * Binary: efficient score for a 2x2 table under the null is the numerator of
#    the score (Cochran-Armitage / unconditional) test:
#        U = sum over analyzable pts of (Y_i * (arm_i - pbar_arm)) reduces, on a
#        2-group table, to  U = a - n1*m1/N  (a = responders in exp arm),
#        V = n1*n0*m1*m0 / (N^2 (N-1))   (hypergeometric variance).
#    z = U/sqrt(V) is the score (chi-square) test for two proportions, oriented
#    positive when the experimental response rate exceeds control.
#  * Continuous: efficient score for a mean difference (known/observed sd) is
#        U = (xbar_exp - xbar_ctrl) / s_pooled^2 * (n1 n0 / N)   [Fisher score],
#    with information V = n1 n0 / (N s_pooled^2). Then U/sqrt(V) equals the usual
#    two-sample z / t statistic (large-sample), oriented positive when the
#    experimental mean exceeds control (higher = better by convention; set
#    `higher_is_better = FALSE` to flip).
#
# All generators use ONLY explicitly supplied assumptions -- no real patient data.
###############################################################################

suppressMessages({
  library(survival)
})

## ---------------------------------------------------------------------------
## Helpers
## ---------------------------------------------------------------------------
.rt_weibull <- function(n, rate, shape) {
  # Weibull with the SAME mean-hazard scaling convention as the reference sim:
  # scale chosen so median matches an exponential with `rate` at shape=1.
  scale_i <- (1 / rate) / gamma(1 + 1 / shape)
  rweibull(n, shape = shape, scale = scale_i)
}

## ===========================================================================
## 1. TIME-TO-EVENT
## ===========================================================================
# base: list(median_ctrl, accrual_months, dropout_rate, dist, weibull_shape,
#            followup_cap optional). group_effect = hazard ratio (exp vs ctrl),
# applied only on the experimental arm.
.gen_tte <- function(n, arm, group_effect, base) {
  lambda_c <- log(2) / base$median_ctrl                 # control hazard (per month)
  hr_i     <- ifelse(arm == 1, group_effect, 1)         # HR applied on exp arm
  lam_i    <- lambda_c * hr_i
  dist  <- if (is.null(base$dist)) "exponential" else base$dist
  shape <- if (is.null(base$weibull_shape)) 1.2 else base$weibull_shape
  if (dist == "weibull") {
    t_event <- .rt_weibull(n, rate = lam_i, shape = shape)
  } else {
    t_event <- rexp(n, rate = lam_i)
  }
  drate <- if (is.null(base$dropout_rate)) 0 else base$dropout_rate
  t_drop <- if (drate > 0) rexp(n, rate = drate / 12) else rep(Inf, n)
  data.frame(t_event = t_event, t_drop = t_drop)
}

## calendar time each patient's EVENT occurs (Inf if censored by dropout).
.clock_tte <- function(dat) {
  cal <- dat$accrual_time + dat$t_event
  cal[dat$t_drop < dat$t_event] <- Inf
  cal
}

## U/V (logrank efficient score) on the analysis subset defined by a calendar
## cutoff. The engine passes an already-subset data.frame with columns
## time,status,arm (built from the calendar cutoff) -- see build_tte_analysis().
.score_tte <- function(time, status, arm) {
  if (length(unique(arm)) < 2 || sum(status) < 1) return(c(U = 0, V = 0))
  sd <- survdiff(Surv(time, status) ~ arm)
  U  <- sd$obs[1] - sd$exp[1]          # control obs - exp  (>0 => exp better)
  V  <- sd$var[1, 1]
  c(U = as.numeric(U), V = as.numeric(V))
}

## build (time,status) at a calendar cutoff for a set of patient rows
build_tte_analysis <- function(dat, cutoff, rows = NULL) {
  if (is.null(rows)) rows <- seq_len(nrow(dat))
  d <- dat[rows, , drop = FALSE]
  # follow-up time clipped at cutoff; event only if it occurred by cutoff and
  # before dropout; otherwise censored at min(cutoff, dropout cal time).
  cal_event  <- d$accrual_time + d$t_event
  cal_drop   <- d$accrual_time + d$t_drop
  entered    <- d$accrual_time <= cutoff
  d <- d[entered, , drop = FALSE]
  cal_event <- cal_event[entered]; cal_drop <- cal_drop[entered]
  obs_time <- pmin(cal_event, cal_drop, cutoff) - d$accrual_time
  status   <- as.integer(cal_event <= cutoff & cal_event <= cal_drop)
  list(time = obs_time, status = status, arm = d$arm)
}

## ===========================================================================
## 2. BINARY (responder / non-responder)
## ===========================================================================
# base: list(p_ctrl, fu_window optional, accrual_months). group_effect is the
# EXPERIMENTAL-arm response probability implied for that patient's subgroup
# (the engine computes it from p_ctrl + rr/or; see resolve_effect()).
.gen_binary <- function(n, arm, group_effect, base) {
  p_ctrl <- base$p_ctrl
  p_i    <- ifelse(arm == 1, group_effect, p_ctrl)
  y      <- rbinom(n, 1, p_i)
  data.frame(y = y)
}

## readiness time = accrual + fixed follow-up window (default 0 => immediate).
.clock_fixedfu <- function(dat, base) {
  fu <- if (is.null(base$fu_window)) 0 else base$fu_window
  dat$accrual_time + fu
}

## score for 2x2 table on analyzable rows: U = a - n1*m1/N ; V hypergeometric.
.score_binary <- function(y, arm) {
  N <- length(y); if (N < 2) return(c(U = 0, V = 0))
  n1 <- sum(arm == 1); n0 <- N - n1
  m1 <- sum(y == 1);   m0 <- N - m1
  if (n1 == 0 || n0 == 0 || m1 == 0 || m0 == 0) return(c(U = 0, V = 1e-8))
  a  <- sum(y == 1 & arm == 1)          # responders in experimental arm
  U  <- a - n1 * m1 / N                  # >0 => exp responds more than expected
  V  <- n1 * n0 * m1 * m0 / (N^2 * (N - 1))
  c(U = as.numeric(U), V = as.numeric(max(V, 1e-8)))
}

## ===========================================================================
## 3. CONTINUOUS (mean difference)
## ===========================================================================
# base: list(mean_ctrl, sd, higher_is_better optional, fu_window, accrual_months)
# group_effect = the EXPERIMENTAL-arm mean for that subgroup (engine derives it
# from mean_ctrl + delta).
.gen_continuous <- function(n, arm, group_effect, base) {
  mu_i <- ifelse(arm == 1, group_effect, base$mean_ctrl)
  x    <- rnorm(n, mean = mu_i, sd = base$sd)
  data.frame(x = x)
}

## Fisher-score for mean difference; oriented by higher_is_better.
.score_continuous <- function(x, arm, higher_is_better = TRUE) {
  N <- length(x); if (N < 3) return(c(U = 0, V = 0))
  n1 <- sum(arm == 1); n0 <- N - n1
  if (n1 < 2 || n0 < 2) return(c(U = 0, V = 1e-8))
  s2 <- var(x)                                   # pooled sample variance
  if (!is.finite(s2) || s2 <= 0) return(c(U = 0, V = 1e-8))
  diff <- mean(x[arm == 1]) - mean(x[arm == 0])  # exp - ctrl
  if (!higher_is_better) diff <- -diff
  V <- (n1 * n0) / (N * s2)                       # information
  U <- diff * V                                   # score = diff * information
  c(U = as.numeric(U), V = as.numeric(max(V, 1e-8)))
}

## ===========================================================================
## PUBLIC DISPATCH
## ===========================================================================
generate_endpoint <- function(endpoint, n, arm, group_effect, base) {
  switch(endpoint,
    "tte"        = .gen_tte(n, arm, group_effect, base),
    "binary"     = .gen_binary(n, arm, group_effect, base),
    "continuous" = .gen_continuous(n, arm, group_effect, base),
    stop("Unknown endpoint: ", endpoint))
}

analysis_clock <- function(endpoint, dat, base) {
  switch(endpoint,
    "tte"        = .clock_tte(dat),
    "binary"     = .clock_fixedfu(dat, base),
    "continuous" = .clock_fixedfu(dat, base),
    stop("Unknown endpoint: ", endpoint))
}

info_unit <- function(endpoint) {
  if (endpoint == "tte") "events" else "patients"
}

## Compute (U,V) on a cumulative analysis set (rows analyzable by `cutoff`).
## Returns c(U, V) oriented positive = experimental better.
score_UV <- function(endpoint, dat, cutoff, rows = NULL, base = NULL) {
  if (is.null(rows)) rows <- seq_len(nrow(dat))
  if (endpoint == "tte") {
    an <- build_tte_analysis(dat, cutoff, rows)
    return(.score_tte(an$time, an$status, an$arm))
  }
  # binary / continuous: analyzable = readiness clock <= cutoff, among rows
  clk <- analysis_clock(endpoint, dat, base)
  keep <- rows[clk[rows] <= cutoff]
  if (length(keep) < 3) return(c(U = 0, V = 1e-8))
  d <- dat[keep, , drop = FALSE]
  if (endpoint == "binary")     return(.score_binary(d$y, d$arm))
  hib <- if (is.null(base$higher_is_better)) TRUE else base$higher_is_better
  .score_continuous(d$x, d$arm, higher_is_better = hib)
}

## Number of "information units" accrued by a calendar cutoff (events for tte,
## analyzable patients otherwise) -- used to pace looks to planned information.
info_count <- function(endpoint, dat, cutoff, rows = NULL, base = NULL) {
  if (is.null(rows)) rows <- seq_len(nrow(dat))
  if (endpoint == "tte") {
    an <- build_tte_analysis(dat, cutoff, rows)
    return(sum(an$status))
  }
  clk <- analysis_clock(endpoint, dat, base)
  sum(clk[rows] <= cutoff)
}

## Vector of per-patient calendar times at which each successive information unit
## arrives, restricted to `rows`, SORTED. Used to find the cutoff that yields a
## target information count. For tte these are event calendar times (finite ones);
## for binary/continuous these are readiness times.
info_arrival_times <- function(endpoint, dat, rows = NULL, base = NULL) {
  if (is.null(rows)) rows <- seq_len(nrow(dat))
  if (endpoint == "tte") {
    cal <- .clock_tte(dat)[rows]
    return(sort(cal[is.finite(cal)]))
  }
  clk <- analysis_clock(endpoint, dat, base)[rows]
  sort(clk[is.finite(clk)])
}
