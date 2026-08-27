###############################################################################
# design_boundaries.R -- group-sequential efficacy/futility boundaries via rpact
#
# Boundaries depend only on (kMax, alpha, information rates, spending family,
# whether early efficacy is allowed). They are endpoint-agnostic because the
# inverse-normal combination test operates on standardized z-scores. Cached.
#
#   get_boundaries(info_rates, alpha, spending, efficacy)
#     -> list(kMax, info_rates, crit, alpha)
#        crit = z efficacy boundary at each look (Inf at interims when efficacy
#               stopping is disabled, so only the final look can reject).
#
# `combine_inverse_normal()` lives here too because it is the statistical glue
# shared by every endpoint and every adaptation.
###############################################################################

suppressMessages(library(rpact))

.boundary_cache <- new.env(parent = emptyenv())

get_boundaries <- function(info_rates, alpha = 0.025,
                           spending = "asOF", efficacy = TRUE) {
  info_rates <- sort(unique(c(info_rates, 1)))          # ensure final look = 1
  key <- paste0(paste(round(info_rates, 4), collapse = "_"),
                "|a", alpha, "|", spending, "|eff", efficacy)
  if (!is.null(.boundary_cache[[key]])) return(.boundary_cache[[key]])
  kMax <- length(info_rates)
  if (efficacy && kMax > 1) {
    d <- getDesignInverseNormal(kMax = kMax, alpha = alpha, sided = 1,
                                informationRates = info_rates,
                                typeOfDesign = spending)
    crit <- d$criticalValues
  } else if (efficacy && kMax == 1) {
    crit <- qnorm(1 - alpha)
  } else {
    # No early efficacy: interims are futility-only; all alpha spent at the end.
    crit <- c(rep(Inf, kMax - 1), qnorm(1 - alpha))
  }
  out <- list(kMax = kMax, info_rates = info_rates,
              crit = as.numeric(crit), alpha = alpha)
  .boundary_cache[[key]] <- out
  out
}

## Inverse-normal combination of stagewise (independent-increment) z-scores.
##   Z_comb,k = cumsum(w_j Z_j) / sqrt(cumsum(w_j^2)),  w_j = sqrt(info increment)
## Weights use the PRE-SPECIFIED information increments (fixed at design time),
## which is what preserves type-I error under data-dependent adaptation.
combine_inverse_normal <- function(z_stages, info_rates) {
  info_rates <- sort(unique(c(info_rates, 1)))
  incr <- diff(c(0, info_rates[seq_along(z_stages)]))
  w <- sqrt(incr / sum(incr))
  cumsum(w * z_stages) / sqrt(cumsum(w^2))
}

## Simes intersection-hypothesis z (for closed testing over {H_F, H_S}).
## Returns the z that the intersection test would compare to the boundary.
simes_intersection_z <- function(zf, zs) {
  pf <- pnorm(-zf); ps <- pnorm(-zs)
  pp <- sort(c(pf, ps))
  qnorm(1 - min(pp * 2 / seq_along(pp)))
}

## Conditional power for futility at an interim, given cumulative z1 at info t.
## Uses the design's final boundary b. (Brownian-motion CP under current trend.)
cp_current_trend <- function(z1, t_frac, b) {
  if (t_frac <= 0 || t_frac >= 1) return(NA_real_)
  zt <- z1 * sqrt(1 / t_frac)
  pnorm((zt - b) / sqrt(1 / t_frac - 1))
}
