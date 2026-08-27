# =====================================================================================
# gating_engine.R  --  Data-driven, reviewable, multivariate threshold estimation for
#                      cytometry QC gating.
#
# WHY THIS EXISTS: the legacy QC path in 01_load_and_qc.R used FIXED percentiles for every
# cutoff (live/dead = "drop top 5%", debris = "2nd pct floor", DNA = "5th-99.5th", beads =
# "99th"). Fixed cutoffs assume a fixed dead/debris fraction and provably bias every
# downstream population proportion when wrong (Petrunkina & Harrison 2011). This module
# instead LEARNS each cutoff from the channel's own density (valley/antimode detection),
# refuses to invent a cutoff when the data is unimodal (Hartigan's dip test + valley-depth
# guard), supports multivariate (2D) joint gates, and can anchor cutoffs to unstained/FMO
# controls. Every proposal is emitted to an editable template + diagnostic figure so a
# human can confirm or override it (two-pass review).
#
# DESIGN PRINCIPLES
#   * Honesty over automation: if there is no real valley, say so (REVIEW_*) and fall back
#     to the legacy conservative percentile -- never fabricate a data-driven cutoff.
#   * Permissive by default: mirrors the skill's anti-over-gating philosophy.
#   * Dependency-optional: flowDensity::deGate, flowClust, mclust, diptest, MASS are USED
#     when present but every path has a dependency-free native fallback, so the module (and
#     its unit tests) run on base R alone.
#   * Pure functions on numeric vectors/matrices -> independently unit-testable
#     (see tests/test_gating_engine.R). No global state; no file I/O except the explicit
#     figure/template writers.
#
# Grounding: flowDensity deGate places cutoffs at density valleys/inflections to reproduce
# manual gates (Malek 2015; Mair 2018); openCyto formalises data-driven template gating
# (Finak 2014); flowClust gives robust 2D model-based gates (Lo 2008/2009); controls/FMO
# anchor positivity (Lee 2019). See references/threshold_selection.md.
# =====================================================================================

# ---- optional-dependency probe -------------------------------------------------------
.has <- function(pkg) isTRUE(requireNamespace(pkg, quietly = TRUE))

# =====================================================================================
# 1. Density peak/valley analysis (native, dependency-free)
# =====================================================================================

# Kernel-density modes: return the smoothed density plus indices/positions of local
# maxima (peaks) and minima (valleys), found from sign changes of the discrete gradient.
kde_modes <- function(x, adjust = 1, n = 512) {
  x <- x[is.finite(x)]
  if (length(x) < 10 || stats::sd(x) == 0) return(NULL)
  d <- stats::density(x, adjust = adjust, n = n)
  dy <- diff(d$y)
  s <- sign(dy); s[s == 0] <- 1               # treat flats as ascending to avoid spurious modes
  peak_idx <- which(diff(s) < 0) + 1L         # +then- : local max
  vall_idx <- which(diff(s) > 0) + 1L         # -then+ : local min
  list(x = d$x, y = d$y,
       peaks   = data.frame(x = d$x[peak_idx], y = d$y[peak_idx]),
       valleys = data.frame(x = d$x[vall_idx], y = d$y[vall_idx]))
}

# Deepest valley between the two most prominent peaks.
#   cutoff : x-position of the trough (NA if <2 peaks or no interior valley)
#   depth  : normalised trough depth in [0,1]; 0 = flat/shallow, ->1 = deep separation
#            depth = 1 - valley_height / min(flanking_peak_heights)
#   peak_ratio : height ratio of the two tallest peaks (max/min); >5 suggests a shoulder,
#                not a true second mode. The caller (estimate_threshold_1d) uses this to
#                penalize depth and avoid placing a cutoff inside the main live population
#                when the "second peak" is a minor shoulder (e.g. neutrophil dye uptake).
find_valley <- function(x, adjust = 1, n = 512) {
  km <- kde_modes(x, adjust = adjust, n = n)
  none <- list(cutoff = NA_real_, depth = 0, n_peaks = if (is.null(km)) 0L else nrow(km$peaks),
               peak_x = NA_real_, valley_y = NA_real_, peak_ratio = NA_real_, km = km)
  if (is.null(km) || nrow(km$peaks) < 2) return(none)
  pk <- km$peaks[order(-km$peaks$y), ][1:2, ]      # two tallest peaks
  pk <- pk[order(pk$x), ]                            # order left->right
  vb <- km$valleys[km$valleys$x > pk$x[1] & km$valleys$x < pk$x[2], , drop = FALSE]
  if (nrow(vb) == 0) return(none)
  v <- vb[which.min(vb$y), ]                          # deepest interior trough
  depth <- 1 - v$y / min(pk$y)
  peak_ratio <- max(pk$y) / min(pk$y)
  list(cutoff = as.numeric(v$x), depth = as.numeric(depth), n_peaks = nrow(km$peaks),
       peak_x = pk$x, valley_y = as.numeric(v$y), peak_ratio = as.numeric(peak_ratio), km = km)
}

# Hartigan's dip test for unimodality (diptest); native peak-count fallback.
#   unimodal = TRUE  -> do NOT invent a valley-based cutoff.
dip_unimodal <- function(x, alpha = 0.05) {
  x <- x[is.finite(x)]
  if (.has("diptest") && length(x) >= 10) {
    dt <- diptest::dip.test(x)
    return(list(unimodal = (dt$p.value > alpha), p = as.numeric(dt$p.value), method = "hartigan_dip"))
  }
  km <- kde_modes(x)
  np <- if (is.null(km)) 1L else nrow(km$peaks)
  list(unimodal = (np < 2), p = NA_real_, method = "kde_peakcount")
}

# =====================================================================================
# 2. Alternative univariate threshold methods
# =====================================================================================

# Otsu between-class variance maximisation (histogram based).
otsu_threshold <- function(x, nbins = 256) {
  x <- x[is.finite(x)]; rng <- range(x)
  if (diff(rng) == 0) return(NA_real_)
  h <- graphics::hist(x, breaks = seq(rng[1], rng[2], length.out = nbins + 1), plot = FALSE)
  p <- h$counts / sum(h$counts); mids <- h$mids
  omega <- cumsum(p); mu <- cumsum(p * mids); muT <- sum(p * mids)
  denom <- omega * (1 - omega); denom[denom <= 0] <- NA
  sigmaB <- (muT * omega - mu)^2 / denom
  as.numeric(mids[which.max(sigmaB)])
}

# 2-component Gaussian mixture antimode (mclust); NA if unavailable / single component.
gmm_threshold <- function(x) {
  x <- x[is.finite(x)]
  if (!.has("mclust")) return(list(cutoff = NA_real_, notes = "mclust unavailable"))
  # mclust::Mclust resolves its internal mclustBIC only when the package is ATTACHED
  # (a known non-interactive quirk); library() is idempotent so this is safe to repeat.
  m <- tryCatch({
    suppressWarnings(suppressPackageStartupMessages(library(mclust)))
    mclust::Mclust(x, G = 2, verbose = FALSE)
  }, error = function(e) NULL)
  if (is.null(m)) return(list(cutoff = NA_real_, notes = "gmm fit failed"))
  mu <- m$parameters$mean
  if (length(mu) < 2) return(list(cutoff = NA_real_, notes = "gmm collapsed to 1 component"))
  v <- m$parameters$variance$sigmasq; if (length(v) == 1) v <- rep(v, 2)
  pro <- m$parameters$pro; ord <- order(mu); lo <- ord[1]; hi <- ord[2]
  gr <- seq(mu[lo], mu[hi], length.out = 512)
  d_lo <- pro[lo] * stats::dnorm(gr, mu[lo], sqrt(v[lo]))
  d_hi <- pro[hi] * stats::dnorm(gr, mu[hi], sqrt(v[hi]))
  list(cutoff = as.numeric(gr[which.min(abs(d_lo - d_hi))]),
       notes = sprintf("gmm 2-comp means %.2f/%.2f", mu[lo], mu[hi]))
}

# =====================================================================================
# 3. Main 1D threshold estimator (the workhorse)
# =====================================================================================
# direction : "keep_below" (drop high tail: viability/dead, beads) |
#             "keep_above" (drop low  tail: debris, DNA-low)
# method    : "auto"|"valley"|"gmm"|"otsu"|"percentile"|"control"
# control   : optional numeric vector from an unstained/FMO control for this channel;
#             when non-NULL it ALWAYS wins (threshold = control_pct quantile of control).
# fallback_pct : conservative legacy percentile used when no trustworthy valley exists
#             (defaults preserve legacy behaviour: 0.95 for keep_below, 0.02 for keep_above).
# Returns a one-row-friendly list with cutoff + full provenance.
# max_remove_frac : safety cap (0..1, default NA = no cap). When set, if the valley
#   cutoff would remove more than this fraction of events, reject it and fall back to
#   the conservative percentile. Rationale: a viability valley that removes >50% of
#   events is almost certainly cutting into the live population (e.g. neutrophils with
#   non-specific dye uptake), not separating live from dead. The analyst can override
#   with a manual threshold if the sample genuinely has >50% dead cells.
estimate_threshold_1d <- function(x, direction = c("keep_below", "keep_above"),
                                  method = "auto", control = NULL,
                                  fallback_pct = NULL, control_pct = 0.99,
                                  valley_min_depth = 0.10, dip_alpha = 0.05, adjust = 1,
                                  max_remove_frac = NA_real_) {
  direction <- match.arg(direction)
  x <- x[is.finite(x)]
  if (is.null(fallback_pct)) fallback_pct <- if (direction == "keep_below") 0.95 else 0.02
  out <- list(cutoff = NA_real_, method_used = method, status = NA_character_,
              valley_confidence = NA_real_, unimodal = NA, dip_p = NA_real_,
              n_peaks = NA_integer_, valley_x = NA_real_, notes = "")
  if (length(x) < 10) { out$cutoff <- NA_real_; out$status <- "REVIEW_too_few"; out$method_used <- "none"
                        out$notes <- "fewer than 10 finite events"; return(out) }
  legacy_cut <- as.numeric(stats::quantile(x, fallback_pct, na.rm = TRUE))

  # --- control-anchored threshold takes precedence whenever a control is supplied -------
  if (!is.null(control)) {
    cc <- control[is.finite(control)]
    if (length(cc) >= 10) {
      out$cutoff <- as.numeric(stats::quantile(cc, control_pct, na.rm = TRUE))
      out$method_used <- "control"; out$status <- "control"
      out$notes <- sprintf("control %.3g-pct (n=%d)", control_pct, length(cc)); return(out)
    }
  }
  if (method == "control") {   # requested but no usable control
    out$cutoff <- legacy_cut; out$method_used <- "percentile"; out$status <- "REVIEW_no_control"
    out$notes <- "control requested but none usable; conservative percentile"; return(out)
  }
  if (method == "percentile") {
    out$cutoff <- legacy_cut; out$method_used <- "percentile"; out$status <- "percentile"
    out$notes <- sprintf("percentile %.3g", fallback_pct); return(out)
  }
  if (method == "otsu") {
    ot <- otsu_threshold(x)
    out$cutoff <- if (is.na(ot)) legacy_cut else ot
    out$method_used <- if (is.na(ot)) "percentile" else "otsu"
    out$status <- if (is.na(ot)) "REVIEW_otsu" else "auto_ok"; return(out)
  }
  if (method == "gmm") {
    g <- gmm_threshold(x); out$notes <- g$notes
    out$cutoff <- if (is.na(g$cutoff)) legacy_cut else g$cutoff
    out$method_used <- if (is.na(g$cutoff)) "percentile" else "gmm"
    out$status <- if (is.na(g$cutoff)) "REVIEW_gmm" else "auto_ok"; return(out)
  }

  # --- valley / auto: dip-test + depth guard, refine location with deGate if present ----
  dip <- dip_unimodal(x, alpha = dip_alpha)
  out$unimodal <- dip$unimodal; out$dip_p <- dip$p
  fv <- find_valley(x, adjust = adjust)
  out$valley_confidence <- fv$depth; out$n_peaks <- fv$n_peaks; out$valley_x <- fv$cutoff
  cut_valley <- fv$cutoff
  if (.has("flowDensity") && .has("flowCore") && is.finite(fv$cutoff)) {
    dg <- tryCatch({
      ff <- flowCore::flowFrame(matrix(x, ncol = 1, dimnames = list(NULL, "V")))
      flowDensity::deGate(ff, channel = "V", tinypeak.removal = 0.02)
    }, error = function(e) NA_real_)
    if (is.finite(dg)) { cut_valley <- as.numeric(dg); out$valley_x <- cut_valley }
  }
  # --- peak-ratio guard: penalize depth when the two tallest peaks are highly asymmetric ---
  # A peak_ratio > 5 means the "second peak" is <20% of the main peak — likely a shoulder,
  # not a true second mode. Penalize depth by dividing by the ratio so a shoulder-induced
  # valley can't pass the min-depth threshold. (ISP V8 case: ratio=4.07, depth 0.68 -> 0.167)
  effective_depth <- fv$depth
  if (is.finite(fv$peak_ratio) && fv$peak_ratio > 1) {
    effective_depth <- fv$depth / fv$peak_ratio
  }
  # --- max-remove guard: if the valley cutoff would remove too many events, reject it ---
  valley_remove_frac <- if (is.finite(cut_valley)) pct_removed_1d(x, cut_valley, direction) else NA_real_
  if (isTRUE(dip$unimodal)) {
    out$cutoff <- legacy_cut; out$method_used <- "percentile"; out$status <- "REVIEW_unimodal"
    out$notes <- "unimodal (dip test) -> no valley invented; conservative percentile"
  } else if (!is.finite(cut_valley) || effective_depth < valley_min_depth) {
    out$cutoff <- legacy_cut; out$method_used <- "percentile"; out$status <- "REVIEW_shallow"
    out$notes <- sprintf("valley depth %.3f (effective %.3f after peak-ratio %.1f) < %.3f -> conservative percentile",
                         fv$depth, effective_depth, ifelse(is.finite(fv$peak_ratio), fv$peak_ratio, NA), valley_min_depth)
  } else if (is.finite(max_remove_frac) && is.finite(valley_remove_frac) && valley_remove_frac > max_remove_frac) {
    out$cutoff <- legacy_cut; out$method_used <- "percentile"; out$status <- "REVIEW_overremove"
    out$notes <- sprintf("valley cutoff would remove %.1f%% (> %.0f%% cap) -> conservative percentile; likely non-specific dye uptake (e.g. neutrophils)",
                         100 * valley_remove_frac, 100 * max_remove_frac)
  } else {
    out$cutoff <- cut_valley
    out$method_used <- if (.has("flowDensity")) "valley(deGate)" else "valley(kde)"
    out$status <- "auto_ok"; out$notes <- sprintf("valley depth %.3f (effective %.3f, peak-ratio %.1f, remove %.1f%%)",
                         fv$depth, effective_depth, ifelse(is.finite(fv$peak_ratio), fv$peak_ratio, NA),
                         ifelse(is.finite(valley_remove_frac), 100 * valley_remove_frac, NA))
  }
  out
}

# Fraction of finite events a 1D cutoff would remove (for template pct_removed).
pct_removed_1d <- function(x, cutoff, direction) {
  x <- x[is.finite(x)]; if (!length(x) || !is.finite(cutoff)) return(NA_real_)
  if (direction == "keep_below") mean(x >= cutoff) else mean(x <= cutoff)
}

# Apply a 1D cutoff -> logical keep vector aligned to the ORIGINAL (possibly non-finite) x.
apply_threshold_1d <- function(x, cutoff, direction) {
  keep <- rep(FALSE, length(x)); ok <- is.finite(x)
  if (!is.finite(cutoff)) { keep[ok] <- TRUE; return(keep) }
  keep[ok] <- if (direction == "keep_below") x[ok] < cutoff else x[ok] > cutoff
  keep
}

# =====================================================================================
# 4. Multivariate (2D) joint gate: keep the dominant/intact population
# =====================================================================================
# Robust 2D ellipse around the MAIN population (native fallback). A robust MCD
# location/scatter (MASS::cov.rob) down-weights off-population outliers (the dead corner,
# low-scatter debris), so the Mahalanobis ellipse tracks the true main-population spread
# instead of being pulled tight by only the densest core or wide by the outliers.
gate_2d_ellipse <- function(x, y, level = 0.99) {
  ok <- is.finite(x) & is.finite(y)
  keep <- rep(FALSE, length(x))
  if (sum(ok) < 20) { keep[ok] <- TRUE; return(list(keep = keep, method = "passthrough")) }
  X <- cbind(x, y)[ok, , drop = FALSE]
  est <- if (.has("MASS")) tryCatch(MASS::cov.rob(X, method = "mcd"), error = function(e) NULL) else NULL
  if (is.null(est) || any(!is.finite(est$cov)) || any(!is.finite(est$center)))
    est <- list(center = colMeans(X), cov = stats::cov(X))
  md2 <- tryCatch(stats::mahalanobis(X, est$center, est$cov),
                  error = function(e) rep(NA_real_, nrow(X)))
  kk <- rep(TRUE, nrow(X))                                  # permissive default
  fin <- is.finite(md2); kk[fin] <- md2[fin] <= stats::qchisq(level, df = 2)
  keep[which(ok)] <- kk
  list(keep = keep, center = est$center, cov = est$cov, level = level,
       method = if (.has("MASS")) "cov.rob_ellipse" else "cov_ellipse")
}

# 2D joint gate dispatcher: flowClust model-based main cluster if available, else ellipse.
estimate_gate_2d <- function(x, y, method = "auto", level = 0.99, K = 1:3) {
  ok <- is.finite(x) & is.finite(y)
  if (method %in% c("auto", "flowclust") && .has("flowClust") && .has("flowCore") && sum(ok) >= 50) {
    res <- tryCatch({
      ff <- flowCore::flowFrame(as.matrix(data.frame(X = x[ok], Y = y[ok])))
      fc <- flowClust::flowClust(ff, varNames = c("X", "Y"), K = K, criterion = "BIC")
      lab <- flowClust::Map(fc, rm.outliers = TRUE)
      main <- as.integer(names(sort(table(lab), decreasing = TRUE))[1])
      kk <- !is.na(lab) & lab == main
      keep <- rep(FALSE, length(x)); keep[which(ok)] <- kk
      list(keep = keep, method = "flowClust", K = fc@K, main_cluster = main)
    }, error = function(e) NULL)
    if (!is.null(res)) return(res)
  }
  gate_2d_ellipse(x, y, level = level)
}

# =====================================================================================
# 5. Editable threshold TEMPLATE (two-pass human-in-the-loop review)
# =====================================================================================
GATE_TEMPLATE_COLS <- c("sample_id", "gate", "channel_x", "channel_y", "method",
                        "proposed_cutoff", "direction", "pct_removed", "valley_confidence",
                        "unimodal", "status", "final_cutoff", "apply", "notes")

# Build one template row from an estimate_threshold_1d() result (+ context).
make_gate_row <- function(sample_id, gate, channel_x, channel_y = NA, est,
                          direction, pct_removed) {
  data.frame(
    sample_id = sample_id, gate = gate, channel_x = channel_x,
    channel_y = ifelse(is.na(channel_y), "", channel_y),
    method = est$method_used,
    proposed_cutoff = round(as.numeric(est$cutoff), 6),
    direction = direction,
    pct_removed = round(as.numeric(pct_removed), 5),
    valley_confidence = ifelse(is.na(est$valley_confidence), NA, round(est$valley_confidence, 4)),
    unimodal = ifelse(is.na(est$unimodal), "NA", ifelse(isTRUE(est$unimodal), "Y", "N")),
    status = est$status,
    final_cutoff = round(as.numeric(est$cutoff), 6),   # pre-filled, user-editable
    apply = "Y",
    notes = est$notes,
    stringsAsFactors = FALSE)
}

write_threshold_template <- function(rows, path) {
  df <- if (is.data.frame(rows)) rows else do.call(rbind, rows)
  if (is.null(df) || nrow(df) == 0) {
    # No gates fired (e.g., no recognizable QC channels): write a header-only template, do not crash.
    df <- as.data.frame(matrix(character(0), nrow = 0, ncol = length(GATE_TEMPLATE_COLS),
                               dimnames = list(NULL, GATE_TEMPLATE_COLS)), stringsAsFactors = FALSE)
  }
  for (c in setdiff(GATE_TEMPLATE_COLS, colnames(df))) df[[c]] <- NA
  df <- df[, GATE_TEMPLATE_COLS, drop = FALSE]
  utils::write.csv(df, path, row.names = FALSE)
  invisible(df)
}

read_threshold_template <- function(path) utils::read.csv(path, stringsAsFactors = FALSE)

# Resolve the cutoff/apply for one sample+gate. Exact sample match wins; else sample_id=ALL
# broadcasts; returns NULL if not present (caller then computes/auto-proposes).
lookup_threshold <- function(tmpl, sample_id, gate) {
  r <- tmpl[tmpl$sample_id == sample_id & tmpl$gate == gate, , drop = FALSE]
  if (!nrow(r)) r <- tmpl[tmpl$sample_id == "ALL" & tmpl$gate == gate, , drop = FALSE]
  if (!nrow(r)) return(NULL)
  r <- r[1, ]
  ap <- toupper(trimws(as.character(r$apply))) %in% c("Y", "YES", "TRUE", "1")
  list(cutoff = suppressWarnings(as.numeric(r$final_cutoff)), apply = ap,
       direction = as.character(r$direction), status = as.character(r$status))
}

# =====================================================================================
# 6. Diagnostic FIGURES (proposed cutoff shown for human confirmation)
# =====================================================================================
# 1D: histogram + KDE, cutoff line, shaded REMOVED region, valley marker, optional control
# overlay; status/depth/unimodal flags in the subtitle so the reviewer can judge trust.
plot_gate_1d <- function(x, cutoff, direction, title, path, control = NULL,
                         status = NA, depth = NA, unimodal = NA, dip_p = NA, valley_x = NULL) {
  suppressPackageStartupMessages(require(ggplot2, quietly = TRUE))
  x <- x[is.finite(x)]; if (length(x) < 5) return(invisible(NULL))
  d <- stats::density(x)
  sub <- sprintf("status: %s | valley depth: %s | unimodal: %s%s",
                 status, ifelse(is.na(depth), "NA", sprintf("%.3f", depth)),
                 ifelse(is.na(unimodal), "NA", ifelse(isTRUE(unimodal), "Y", "N")),
                 ifelse(is.na(dip_p), "", sprintf(" (dip p=%.3g)", dip_p)))
  cap_bits <- "orange line = cutoff; shaded = removed"
  if (!is.null(control)) cap_bits <- c(cap_bits, "dashed blue = control")
  if (!is.null(valley_x) && is.finite(valley_x)) cap_bits <- c(cap_bits, "green dot = antimode")
  cap <- paste(cap_bits, collapse = " | ")
  rx <- if (direction == "keep_below") c(cutoff, Inf) else c(-Inf, cutoff)
  p <- ggplot(data.frame(value = x), aes(value)) +
    geom_histogram(aes(y = after_stat(density)), bins = 100, fill = "#ECE9E2", color = NA) +
    geom_line(data = data.frame(x = d$x, y = d$y), aes(x, y), color = "#000000", linewidth = 0.4) +
    annotate("rect", xmin = rx[1], xmax = rx[2], ymin = 0, ymax = Inf, alpha = 0.10, fill = "#FF9400") +
    geom_vline(xintercept = cutoff, color = "#FF9400", linewidth = 0.9) +
    labs(title = title, subtitle = sub, x = "intensity", y = "density", caption = cap) +
    theme_bw(base_size = 10) +
    theme(text = element_text(family = "Liberation Sans"),
          plot.title = element_text(size = 10, face = "bold"),
          plot.subtitle = element_text(size = 8),
          plot.caption = element_text(size = 7, hjust = 0),
          plot.margin = margin(6, 12, 6, 6))
  if (!is.null(control)) {
    dc <- stats::density(control[is.finite(control)])
    p <- p + geom_line(data = data.frame(x = dc$x, y = dc$y), aes(x, y),
                       color = "#0279EE", linewidth = 0.5, linetype = "dashed")
  }
  if (!is.null(valley_x) && is.finite(valley_x))
    p <- p + geom_point(data = data.frame(x = valley_x, y = 0), aes(x, y),
                        color = "#75A025", size = 2.5)
  ggplot2::ggsave(path, p, width = 6.8, height = 4.0, dpi = 120)
  invisible(p)
}

# 2D: scatter/hexbin coloured by kept vs removed for joint gates.
# 2D scatter with kept/removed points AND the actual gate boundary overlaid, so a reviewer
# can judge the gate GEOMETRY (not just which dots survived). `ellipse` = list(center, cov,
# level): when supplied the EXACT Mahalanobis boundary is drawn (solid); otherwise (e.g. a
# flowClust cluster gate that has no single ellipse) the kept-region is outlined (dashed, approx).
plot_gate_2d <- function(x, y, keep, xlab, ylab, title, path, ellipse = NULL) {
  suppressPackageStartupMessages(require(ggplot2, quietly = TRUE))
  ok <- is.finite(x) & is.finite(y)
  df <- data.frame(x = x[ok], y = y[ok], kept = ifelse(keep[ok], "kept", "removed"))
  # Exact ellipse boundary from the gate's own center/cov/level (chol maps the unit circle).
  eb <- NULL
  if (!is.null(ellipse) && all(is.finite(ellipse$center)) && all(is.finite(as.matrix(ellipse$cov)))) {
    eb <- tryCatch({
      U <- chol(ellipse$cov); r <- sqrt(stats::qchisq(ellipse$level, df = 2))
      th <- seq(0, 2 * pi, length.out = 220); circ <- rbind(cos(th), sin(th))
      pts <- t(ellipse$center + t(U) %*% (r * circ))
      data.frame(x = pts[, 1], y = pts[, 2])
    }, error = function(e) NULL)
  }
  cap <- if (!is.null(eb)) "blue = kept | orange = removed | black line = gate boundary"
         else "blue = kept | orange = removed | dashed = kept-region outline (approx)"
  p <- ggplot(df, aes(x, y, color = kept)) +
    geom_point(size = 0.2, alpha = 0.3) +
    scale_color_manual(values = c(kept = "#0279EE", removed = "#FF9400"), name = "") +
    labs(title = title, x = xlab, y = ylab, caption = cap) +
    guides(color = guide_legend(override.aes = list(size = 2, alpha = 1))) +
    theme_bw(base_size = 10) +
    theme(text = element_text(family = "Liberation Sans"),
          plot.caption = element_text(size = 7, hjust = 0))
  if (!is.null(eb)) {
    p <- p + geom_path(data = eb, aes(x, y), inherit.aes = FALSE, color = "#000000", linewidth = 0.7)
  } else if (sum(df$kept == "kept") > 20) {
    p <- p + stat_ellipse(data = df[df$kept == "kept", ], aes(x, y), inherit.aes = FALSE,
                          type = "norm", level = 0.95, color = "#000000", linewidth = 0.6, linetype = 2)
  }
  ggplot2::ggsave(path, p, width = 5.6, height = 4.2, dpi = 120)
  invisible(p)
}

# =====================================================================================
# 7. Time-based acquisition QC  (v2.2.0 item 1)
# =====================================================================================
# WHY: fixed QC ignores WHEN events were acquired. Clogs, bubbles, and detector drift make
# whole time-windows untrustworthy; flowAI/PeacoQC show 27-43% of events in some files sit in
# anomalous windows. These native, dependency-free functions bin events by acquisition Time and
# flag (a) flow-rate outliers, (b) per-channel signal drift, (c) boundary/margin pile-ups, via
# robust MAD z-scores -- conservative by default (mad_k=5) to preserve the anti-over-gating
# philosophy. Grounding: flowAI (Monaco 2016), PeacoQC (Emmaneel 2021/2022).
# All functions are PURE (numeric in -> list out); 01 wires the keep-mask under --time-qc.

.fmt_pct <- function(x) if (is.null(x) || length(x) != 1 || !is.finite(x)) "NA" else sprintf("%.2f", x)

# (a) Flow-rate stability: bin by Time, flag bins whose event count deviates > mad_k MADs.
flow_rate_qc <- function(time, n_bins = 100, mad_k = 5) {
  time <- as.numeric(time); n <- length(time); ok <- is.finite(time)
  empty <- list(keep = rep(TRUE, n), flagged = rep(FALSE, n), pct = 0,
                n_flagged_bins = 0L, n_bins = 0L, bins = NULL, status = "skipped",
                notes = "insufficient/degenerate Time")
  if (sum(ok) < 50 || diff(range(time[ok])) <= 0) return(empty)
  rng <- range(time[ok]); breaks <- seq(rng[1], rng[2], length.out = n_bins + 1)
  bin <- findInterval(time, breaks, rightmost.closed = TRUE); bin[!ok] <- NA
  counts <- tabulate(bin[ok], nbins = n_bins)
  med <- stats::median(counts); sc <- stats::mad(counts)
  if (!is.finite(sc) || sc == 0) sc <- stats::sd(counts)
  bins <- data.frame(bin = seq_len(n_bins), t_lo = breaks[-length(breaks)],
                     t_hi = breaks[-1], count = counts, z = 0, flagged = FALSE)
  if (!is.finite(sc) || sc == 0)
    return(list(keep = rep(TRUE, n), flagged = rep(FALSE, n), pct = 0, n_flagged_bins = 0L,
                n_bins = n_bins, bins = bins, status = "ok", notes = "flat rate; nothing flagged"))
  z <- (counts - med) / sc; bin_flag <- abs(z) > mad_k
  bins$z <- z; bins$flagged <- bin_flag
  flagged <- rep(FALSE, n); flagged[ok] <- bin_flag[bin[ok]]
  list(keep = !flagged, flagged = flagged, pct = 100 * mean(flagged),
       n_flagged_bins = sum(bin_flag), n_bins = n_bins, bins = bins, status = "ok",
       notes = sprintf("%d/%d bins flagged (|z|>%g MAD of per-bin counts)", sum(bin_flag), n_bins, mad_k))
}

# (b) Signal stability: per-channel per-bin median; flag a time-bin if ANY channel's binned
#     median drifts > mad_k MADs from that channel's median-of-binned-medians.
signal_stability_qc <- function(time, expr, n_bins = 100, mad_k = 5) {
  time <- as.numeric(time); expr <- as.matrix(expr); n <- length(time)
  if (nrow(expr) != n) stop("signal_stability_qc: nrow(expr) must equal length(time)")
  ok <- is.finite(time)
  okc <- which(apply(expr, 2, function(col) sum(is.finite(col)) > 0))
  empty <- list(keep = rep(TRUE, n), flagged = rep(FALSE, n), pct = 0, n_bins = 0L,
                channel = data.frame(), bins = NULL, status = "skipped",
                notes = "insufficient/degenerate Time or no usable channels")
  if (sum(ok) < 50 || diff(range(time[ok])) <= 0 || !length(okc)) return(empty)
  rng <- range(time[ok]); breaks <- seq(rng[1], rng[2], length.out = n_bins + 1)
  bin <- findInterval(time, breaks, rightmost.closed = TRUE); bin[!ok] <- NA
  chans <- colnames(expr); if (is.null(chans)) chans <- paste0("ch", seq_len(ncol(expr)))
  bin_flag <- rep(FALSE, n_bins); zmax <- rep(0, n_bins)
  csum <- data.frame(channel = character(0), max_abs_z = numeric(0),
                     n_flagged_bins = integer(0), stringsAsFactors = FALSE)
  for (j in okc) {
    col <- expr[, j]
    m <- tapply(col[ok], bin[ok], function(v) stats::median(v[is.finite(v)]))
    mv <- rep(NA_real_, n_bins); mv[as.integer(names(m))] <- as.numeric(m)
    center <- stats::median(mv, na.rm = TRUE); scl <- stats::mad(mv, na.rm = TRUE)
    if (!is.finite(scl) || scl == 0) scl <- stats::sd(mv, na.rm = TRUE)
    if (!is.finite(scl) || scl == 0) next
    zc <- abs(mv - center) / scl; zc[!is.finite(zc)] <- 0
    fj <- zc > mad_k; bin_flag <- bin_flag | fj; zmax <- pmax(zmax, zc)
    csum <- rbind(csum, data.frame(channel = chans[j], max_abs_z = max(zc),
                                   n_flagged_bins = sum(fj), stringsAsFactors = FALSE))
  }
  flagged <- rep(FALSE, n); flagged[ok] <- bin_flag[bin[ok]]
  bins <- data.frame(bin = seq_len(n_bins), t_lo = breaks[-length(breaks)],
                     t_hi = breaks[-1], max_abs_z = zmax, flagged = bin_flag)
  if (nrow(csum)) csum <- csum[order(-csum$max_abs_z), , drop = FALSE]
  list(keep = !flagged, flagged = flagged, pct = 100 * mean(flagged), n_bins = n_bins,
       channel = csum, bins = bins, status = "ok",
       notes = sprintf("%d/%d bins flagged for signal drift (|z|>%g MAD; %d channels)",
                       sum(bin_flag), n_bins, mad_k, length(okc)))
}

# (c) Margin/boundary events: values pinned at a channel's dynamic-range edge (clamped
#     out-of-range pile-ups). ranges = optional list of c(min,max) per channel (from FCS $PnR);
#     when NULL, observed per-channel min/max is used (catches equal-valued pile-ups only).
margin_events <- function(expr, ranges = NULL, tol_frac = 1e-5) {
  expr <- as.matrix(expr); n <- nrow(expr)
  chans <- colnames(expr); if (is.null(chans)) chans <- paste0("ch", seq_len(ncol(expr)))
  margin <- rep(FALSE, n)
  csum <- data.frame(channel = character(0), lo = numeric(0), hi = numeric(0),
                     n_low = integer(0), n_high = integer(0), stringsAsFactors = FALSE)
  for (j in seq_len(ncol(expr))) {
    col <- expr[, j]; fin <- is.finite(col); if (!any(fin)) next
    if (!is.null(ranges)) { lo <- ranges[[j]][1]; hi <- ranges[[j]][2] }
    else { lo <- min(col[fin]); hi <- max(col[fin]) }
    span <- hi - lo; if (!is.finite(span) || span <= 0) next
    tol <- tol_frac * span
    is_lo <- fin & (col <= lo + tol); is_hi <- fin & (col >= hi - tol)
    margin <- margin | is_lo | is_hi
    csum <- rbind(csum, data.frame(channel = chans[j], lo = lo, hi = hi,
                                   n_low = sum(is_lo), n_high = sum(is_hi), stringsAsFactors = FALSE))
  }
  list(keep = !margin, flagged = margin, pct = 100 * mean(margin), channel = csum,
       status = "ok", notes = sprintf("%d margin events (%.3f%%) across %d channels",
                                      sum(margin), 100 * mean(margin), ncol(expr)))
}

# Combiner: run selected checks, OR their per-event flags. PURE -- returns keep-mask + per-check %.
time_acquisition_qc <- function(time, expr = NULL, ranges = NULL,
                                checks = c("rate", "signal", "margin"),
                                n_bins = 100, mad_k = 5, tol_frac = 1e-5) {
  checks <- match.arg(checks, several.ok = TRUE)
  time <- as.numeric(time); n <- length(time)
  res <- list(); flagged_any <- rep(FALSE, n)
  pr <- ps <- pm <- NA_real_
  if ("rate" %in% checks) {
    r <- flow_rate_qc(time, n_bins = n_bins, mad_k = mad_k); res$rate <- r
    pr <- r$pct; flagged_any <- flagged_any | r$flagged
  }
  if ("signal" %in% checks && !is.null(expr)) {
    s <- signal_stability_qc(time, expr, n_bins = n_bins, mad_k = mad_k); res$signal <- s
    ps <- s$pct; flagged_any <- flagged_any | s$flagged
  }
  if ("margin" %in% checks && !is.null(expr)) {
    mm <- margin_events(expr, ranges = ranges, tol_frac = tol_frac); res$margin <- mm
    pm <- mm$pct; flagged_any <- flagged_any | mm$flagged
  }
  list(keep = !flagged_any, flagged = flagged_any, pct_rate = pr, pct_signal = ps,
       pct_margin = pm, pct_total = 100 * mean(flagged_any), n = n, checks = checks, detail = res)
}

# Diagnostic figure: events-per-time-bin, rate-flagged bins in orange, signal-drift windows shaded.
plot_time_qc <- function(qc, sample_id, path) {
  suppressPackageStartupMessages(require(ggplot2, quietly = TRUE))
  rate <- qc$detail$rate
  if (is.null(rate) || is.null(rate$bins)) return(invisible(NULL))
  b <- rate$bins; b$tmid <- (b$t_lo + b$t_hi) / 2
  b$flagged <- factor(ifelse(b$flagged, "TRUE", "FALSE"), levels = c("FALSE", "TRUE"))
  w <- if (nrow(b) > 0) (b$t_hi[1] - b$t_lo[1]) else 1
  cap <- sprintf("rate flagged: %s%% | signal: %s%% | margin: %s%%",
                 .fmt_pct(qc$pct_rate), .fmt_pct(qc$pct_signal), .fmt_pct(qc$pct_margin))
  p <- ggplot(b, aes(tmid, count)) +
    geom_col(aes(fill = flagged), width = w) +
    scale_fill_manual(values = c(`FALSE` = "#0279EE", `TRUE` = "#FF9400"),
                      name = "rate-flagged bin", drop = FALSE) +
    labs(title = sprintf("Time-based acquisition QC: %s", sample_id),
         x = "acquisition time (bin midpoint)", y = "events per bin", caption = cap) +
    theme_bw(base_size = 10) +
    theme(text = element_text(family = "Liberation Sans"),
          plot.caption = element_text(size = 7, hjust = 0))
  sig <- qc$detail$signal
  if (!is.null(sig) && !is.null(sig$bins) && any(sig$bins$flagged)) {
    sb <- sig$bins[sig$bins$flagged, , drop = FALSE]
    p <- p + annotate("rect", xmin = sb$t_lo, xmax = sb$t_hi, ymin = 0, ymax = Inf,
                      alpha = 0.12, fill = "#75A025")
  }
  ggplot2::ggsave(path, p, width = 6.8, height = 3.6, dpi = 120)
  invisible(p)
}

# =====================================================================================
# 8. Compensation / spillover diagnostics  (v2.2.0 item 2)
# =====================================================================================
# WHY: compensation multiplies data by inverse(spillover). If the spillover matrix is
# ill-conditioned (near-collinear detectors), that inverse AMPLIFIES noise, so downstream
# positivity gates become unstable. We ALWAYS report the 2-norm condition number (kappa) and
# reciprocal condition (rcond), classify the matrix, and (in 01) still apply unless singular
# (report-only; preserves v2.1.0 apply/skip logic). SVD-based so it is dependency-free.
# kappa = smax/smin ; rcond = smin/smax. Grounding: Roederer 2001 (compensation caveats).
spillover_diagnostics <- function(mat, kappa_max = 1e3) {
  out <- list(n = NA_integer_, square = FALSE, finite = FALSE, singular = NA,
              rcond = NA_real_, kappa = NA_real_, verdict = "malformed", notes = "")
  if (is.null(mat)) { out$notes <- "NULL matrix"; return(out) }
  m <- tryCatch(as.matrix(mat), error = function(e) NULL)
  if (is.null(m) || length(dim(m)) != 2L) { out$notes <- "not a 2D matrix"; return(out) }
  out$n <- nrow(m); out$square <- (nrow(m) == ncol(m)); out$finite <- all(is.finite(m))
  if (!out$square || !out$finite || nrow(m) < 1) {
    out$notes <- sprintf("square=%s finite=%s dim=%dx%d", out$square, out$finite, nrow(m), ncol(m))
    return(out)
  }
  sv <- tryCatch(svd(m, nu = 0, nv = 0)$d, error = function(e) NULL)
  if (is.null(sv) || !all(is.finite(sv))) { out$notes <- "SVD failed"; return(out) }
  smin <- min(sv); smax <- max(sv)
  tol <- max(dim(m)) * .Machine$double.eps * smax
  out$kappa <- if (smin <= 0) Inf else smax / smin
  out$rcond <- if (smax <= 0) 0 else smin / smax
  out$singular <- smin <= tol
  out$verdict <- if (isTRUE(out$singular)) "singular" else if (out$kappa > kappa_max) "ill" else "well"
  out$notes <- sprintf("kappa=%.4g rcond=%.4g verdict=%s (tol=%.2e)",
                       out$kappa, out$rcond, out$verdict, tol)
  out
}

# Read an external compensation matrix CSV (header + rownames; square channels x channels).
read_spillover_csv <- function(path) {
  df <- utils::read.csv(path, header = TRUE, row.names = 1, check.names = FALSE)
  as.matrix(df)
}

# Align an external spillover matrix to the data's channel names (intersection; symmetric).
align_spillover <- function(mat, channels) {
  m <- as.matrix(mat)
  if (is.null(colnames(m)) && !is.null(rownames(m))) colnames(m) <- rownames(m)
  if (is.null(rownames(m)) && !is.null(colnames(m))) rownames(m) <- colnames(m)
  cn <- colnames(m)
  keep <- intersect(channels, cn)
  info <- list(n_matched = length(keep),
               missing_in_matrix = setdiff(channels, cn),
               extra_in_matrix = setdiff(cn, channels))
  if (length(keep) < 1) return(list(matrix = NULL, info = info, ok = FALSE))
  list(matrix = m[keep, keep, drop = FALSE], info = info, ok = TRUE)
}

# =====================================================================================
# 9. Batch-aware cutoff harmonization  (v2.2.0 item 4)
# =====================================================================================
# WHY: with per-sample cutoffs, a noisy/low-confidence sample can get an idiosyncratic cutoff
# that fractures a population across a batch. Here each per-sample cutoff is shrunk toward its
# (gate, batch) median CONSENSUS by an amount (1 - valley_confidence) * shrink -- so a confident
# bimodal cutoff (depth ~ 1) stays put, while a shallow/low-confidence one borrows strength from
# same-batch siblings. Groups with < min_group samples are left unchanged (nothing to borrow).
# PURE: template rows in -> augmented rows out (writes harmonized value into cutoff_col).
# Grounding: batch-consensus harmonization is the spirit of CytoNorm (Van Gassen 2020).
harmonize_cutoffs <- function(rows, batch_map, shrink = 1.0, min_group = 2,
                              cutoff_col = "final_cutoff", na_confidence = 0) {
  df <- as.data.frame(rows, stringsAsFactors = FALSE)
  if (!all(c("sample_id", "gate", cutoff_col) %in% colnames(df)))
    stop("harmonize_cutoffs: rows must contain sample_id, gate, and ", cutoff_col)
  if (is.data.frame(batch_map)) {
    key <- stats::setNames(as.character(batch_map$batch), as.character(batch_map$sample_id))
  } else {
    key <- stats::setNames(as.character(batch_map), names(batch_map))
  }
  df$batch <- unname(key[as.character(df$sample_id)]); df$batch[is.na(df$batch)] <- "unknown"
  conf <- suppressWarnings(as.numeric(df$valley_confidence))
  conf[!is.finite(conf)] <- na_confidence; conf <- pmin(pmax(conf, 0), 1)
  df$pre_harmonize_cutoff <- suppressWarnings(as.numeric(df[[cutoff_col]]))
  df$batch_consensus <- NA_real_; df$shrink_weight <- 0
  df$harmonized_cutoff <- df$pre_harmonize_cutoff
  grp <- interaction(df$gate, df$batch, drop = TRUE)
  gsum <- list()
  for (g in levels(grp)) {
    idx <- which(grp == g); cuts <- df$pre_harmonize_cutoff[idx]; valid <- is.finite(cuts)
    if (sum(valid) < min_group) next
    consensus <- stats::median(cuts[valid])
    sh <- (1 - conf[idx]) * shrink; sh[!is.finite(sh)] <- shrink; sh <- pmin(pmax(sh, 0), 1)
    df$batch_consensus[idx] <- consensus; df$shrink_weight[idx] <- sh
    df$harmonized_cutoff[idx] <- ifelse(valid, (1 - sh) * cuts + sh * consensus, cuts)
    gsum[[g]] <- data.frame(gate = df$gate[idx][1], batch = df$batch[idx][1],
                            n = sum(valid), consensus = consensus, stringsAsFactors = FALSE)
  }
  df[[cutoff_col]] <- df$harmonized_cutoff
  groups <- if (length(gsum)) do.call(rbind, gsum) else
    data.frame(gate = character(0), batch = character(0), n = integer(0), consensus = numeric(0))
  list(rows = df, n_batches = length(unique(df$batch[df$batch != "unknown"])),
       n_groups_harmonized = nrow(groups), groups = groups, shrink = shrink)
}
