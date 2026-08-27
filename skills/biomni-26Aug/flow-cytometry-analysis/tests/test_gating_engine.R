#!/usr/bin/env Rscript
# =====================================================================================
# test_gating_engine.R -- deterministic unit tests for scripts/gating_engine.R
# Runs on base R alone (native fallbacks); optional deps (diptest/mclust/flowDensity/
# flowClust/MASS) are exercised automatically when installed. Exits non-zero on failure.
#
#   Rscript tests/test_gating_engine.R
# =====================================================================================
suppressWarnings(suppressMessages({
  here <- tryCatch(dirname(sys.frame(1)$ofile), error = function(e) NA)
  eng  <- "scripts/gating_engine.R"
  if (!file.exists(eng)) eng <- file.path(dirname(getwd()), "scripts", "gating_engine.R")
  if (!file.exists(eng)) eng <- "/mnt/results/skills/flow-cytometry-analysis/scripts/gating_engine.R"
  source(eng)
}))
set.seed(1234)

.n_pass <- 0L; .n_fail <- 0L; .fails <- character(0)
ok <- function(cond, msg) {
  if (isTRUE(cond)) { .n_pass <<- .n_pass + 1L; cat(sprintf("  PASS: %s\n", msg)) }
  else { .n_fail <<- .n_fail + 1L; .fails <<- c(.fails, msg); cat(sprintf("  FAIL: %s\n", msg)) }
}
sect <- function(s) cat(sprintf("\n== %s ==\n", s))
cat(sprintf("Optional deps: diptest=%s mclust=%s flowDensity=%s flowClust=%s MASS=%s\n",
            .has("diptest"), .has("mclust"), .has("flowDensity"), .has("flowClust"), .has("MASS")))

# ------------------------------------------------------------------ 1. bimodal -> valley
sect("1. Bimodal viability -> valley cutoff in the trough (auto_ok)")
x_bi <- c(rnorm(9000, 0, 0.6), rnorm(1000, 6, 0.6))   # 90% live (low), 10% dead (high)
e <- estimate_threshold_1d(x_bi, direction = "keep_below", method = "auto")
ok(e$status == "auto_ok", "status auto_ok on clean bimodal")
ok(is.finite(e$cutoff) && e$cutoff > 1.5 && e$cutoff < 4.5, "cutoff lands between the two modes")
ok(grepl("valley", e$method_used), "method_used is a valley method")
ok(e$valley_confidence >= 0.10, "valley depth exceeds min-depth guard")
kp <- apply_threshold_1d(x_bi, e$cutoff, "keep_below")
ok(abs(mean(kp) - 0.90) < 0.03, "keeps ~90% (the live mode)")

# ------------------------------------------------------------------ 2. unimodal -> refuse
sect("2. Unimodal (all live) -> dip flags unimodal -> NO invented cutoff")
x_uni <- rnorm(10000, 0, 1)
e2 <- estimate_threshold_1d(x_uni, direction = "keep_below", method = "auto")
ok(grepl("^REVIEW", e2$status), "status is a REVIEW_* flag (no confident valley)")
ok(e2$method_used == "percentile", "falls back to conservative percentile")
ok(abs(e2$cutoff - as.numeric(quantile(x_uni, 0.95))) < 1e-9, "fallback cutoff == 95th percentile (legacy)")
if (.has("diptest")) ok(e2$status == "REVIEW_unimodal", "with diptest -> REVIEW_unimodal specifically")

# ------------------------------------------------------------------ 3. shallow -> REVIEW
sect("3. Shallow valley -> REVIEW (not silently applied)")
x_sh <- c(rnorm(6000, 0, 1.4), rnorm(4000, 2.2, 1.4))  # heavily overlapping -> shallow trough
e3 <- estimate_threshold_1d(x_sh, direction = "keep_below", method = "auto")
ok(grepl("^REVIEW", e3$status), "overlapping modes -> REVIEW_* (shallow or unimodal)")
ok(e3$method_used == "percentile", "shallow case falls back to percentile")

# ------------------------------------------------------------------ 4. control-anchored
sect("4. Control/FMO -> cutoff = control 99th percentile (precedence)")
ctrl <- rnorm(5000, 0, 1); stain <- c(rnorm(5000, 0, 1), rnorm(5000, 5, 1))
e4 <- estimate_threshold_1d(stain, direction = "keep_below", method = "auto", control = ctrl, control_pct = 0.99)
ok(e4$status == "control", "status == control when control supplied")
ok(abs(e4$cutoff - as.numeric(quantile(ctrl, 0.99))) < 1e-9, "cutoff == control 99th pct exactly")

# ------------------------------------------------------------------ 5. otsu + gmm
sect("5. Otsu and GMM place a cutoff in the gap of clean bimodal")
eo <- estimate_threshold_1d(x_bi, direction = "keep_below", method = "otsu")
ok(eo$status == "auto_ok" && eo$cutoff > 1 && eo$cutoff < 5, "otsu cutoff within the gap")
eg <- estimate_threshold_1d(x_bi, direction = "keep_below", method = "gmm")
if (.has("mclust")) {
  ok(eg$status == "auto_ok" && eg$cutoff > 1 && eg$cutoff < 5, "gmm antimode within the gap")
} else ok(eg$method_used == "percentile", "gmm gracefully falls back without mclust")

# ------------------------------------------------------------------ 6. percentile == legacy
sect("6. method=percentile reproduces the legacy fixed cutoff")
ep <- estimate_threshold_1d(x_bi, direction = "keep_below", method = "percentile")
ok(abs(ep$cutoff - as.numeric(quantile(x_bi, 0.95))) < 1e-9, "keep_below percentile == q95 (legacy live/dead)")
ep2 <- estimate_threshold_1d(x_bi, direction = "keep_above", method = "percentile")
ok(abs(ep2$cutoff - as.numeric(quantile(x_bi, 0.02))) < 1e-9, "keep_above percentile == q02 (legacy debris)")

# ------------------------------------------------------------------ 7. 2D joint gate
sect("7. 2D joint gate removes the dead/low-scatter corner, keeps the main blob")
n <- 8000
live <- cbind(rnorm(n, 5, 0.7), rnorm(n, 0, 0.7))       # high FSC, low viability
dead <- cbind(rnorm(n/8, 1, 0.6), rnorm(n/8, 5, 0.6))   # low FSC, high viability
X <- rbind(live, dead); is_live <- c(rep(TRUE, n), rep(FALSE, n/8))
g <- estimate_gate_2d(X[, 1], X[, 2], method = "auto", level = 0.99)
ok(mean(g$keep[is_live]) > 0.9, "keeps >90% of true live cells")
ok(mean(g$keep[!is_live]) < 0.5, "removes majority of the dead corner")
# ellipse coverage-level knob (the editable final_cutoff for a 2D gate) is monotonic:
# a higher level = looser gate = keeps MORE; lower level = tighter = keeps less.
g_loose <- estimate_gate_2d(X[, 1], X[, 2], method = "ellipse", level = 0.999)
g_tight <- estimate_gate_2d(X[, 1], X[, 2], method = "ellipse", level = 0.90)
ok(sum(g_loose$keep) > sum(g_tight$keep), "higher ellipse level keeps more (loosens the 2D gate)")
ok(grepl("ellipse", g_loose$method), "explicit level forces the robust-ellipse method")

# ------------------------------------------------------------------ 8. template round-trip
sect("8. Template write -> read -> lookup (incl. ALL broadcast + edit applied)")
tmpf <- tempfile(fileext = ".csv")
r1 <- make_gate_row("S1", "live_dead", "Viability", NA, e, "keep_below", pct_removed_1d(x_bi, e$cutoff, "keep_below"))
r2 <- make_gate_row("ALL", "debris", "FSC-A", NA, ep2, "keep_above", pct_removed_1d(x_bi, ep2$cutoff, "keep_above"))
df <- write_threshold_template(list(r1, r2), tmpf)
ok(all(GATE_TEMPLATE_COLS %in% colnames(df)), "template has the full schema")
tin <- read_threshold_template(tmpf)
tin$final_cutoff[tin$sample_id == "S1" & tin$gate == "live_dead"] <- 3.14159  # user edit
lu <- lookup_threshold(tin, "S1", "live_dead")
ok(!is.null(lu) && abs(lu$cutoff - 3.14159) < 1e-6 && isTRUE(lu$apply), "edited final_cutoff is read back exactly")
lb <- lookup_threshold(tin, "S9", "debris")
ok(!is.null(lb) && lb$direction == "keep_above", "sample_id=ALL broadcasts to an unlisted sample")
ok(is.null(lookup_threshold(tin, "S1", "nonexistent_gate")), "missing gate -> NULL (caller auto-proposes)")

# ------------------------------------------------------------------ 9. figures written
sect("9. Diagnostic figures are actually written to disk")
f1 <- tempfile(fileext = ".png"); f2 <- tempfile(fileext = ".png")
plot_gate_1d(x_bi, e$cutoff, "keep_below", "unit-test 1D", f1, status = e$status,
             depth = e$valley_confidence, unimodal = e$unimodal, dip_p = e$dip_p, valley_x = e$valley_x)
plot_gate_2d(X[, 1], X[, 2], g$keep, "FSC-A", "Viability", "unit-test 2D", f2)
ok(file.exists(f1) && file.info(f1)$size > 1000, "1D gate figure written (non-trivial size)")
ok(file.exists(f2) && file.info(f2)$size > 1000, "2D gate figure written (non-trivial size)")

# ------------------------------------------------------------------ 10. edge cases
sect("10. Edge cases: too-few events and zero-variance input")
ez <- estimate_threshold_1d(rep(3, 5), direction = "keep_below", method = "auto")
ok(ez$status == "REVIEW_too_few", "fewer than 10 events -> REVIEW_too_few")
 eflat <- estimate_threshold_1d(rep(3, 100), direction = "keep_below", method = "auto")
ok(grepl("^REVIEW", eflat$status), "zero-variance -> REVIEW (no invented cutoff)")

# ------------------------------------------------------------------ 11. max_remove_frac: asymmetric shoulder -> REVIEW_overremove
sect("11. Asymmetric shoulder (dominant + minor peak) with max_remove_frac -> REVIEW_overremove")
# Simulates the ISP V8 pattern: a dominant live peak with a minor shoulder that
# find_valley picks as the "second peak". The valley between them sits INSIDE the
# live population, so the valley cutoff would remove far too many events.
# Minor peak at -3 (n=4000, 20%), dominant peak at 0 (n=16000, 80%), well separated.
# keep_below: the valley cutoff between them removes the dominant 80% peak.
x_shoulder <- c(rnorm(4000, -3, 0.5), rnorm(16000, 0, 0.5))
e11 <- estimate_threshold_1d(x_shoulder, direction = "keep_below", method = "auto", max_remove_frac = 0.50)
ok(e11$status == "REVIEW_overremove", "asymmetric shoulder with cap -> REVIEW_overremove (not auto_ok)")
ok(e11$method_used == "percentile", "over-remove case falls back to percentile")
ok(abs(e11$cutoff - as.numeric(quantile(x_shoulder, 0.95))) < 1e-9, "cutoff == 95th percentile (conservative)")
# Without the cap, the same data would use the valley and over-remove:
e11_nocap <- estimate_threshold_1d(x_shoulder, direction = "keep_below", method = "auto")
kp_nocap <- apply_threshold_1d(x_shoulder, e11_nocap$cutoff, "keep_below")
ok(mean(kp_nocap) < 0.50, "without cap, valley cutoff removes >50% (demonstrates the bug)")
# With the cap, we keep ~95%:
kp_cap <- apply_threshold_1d(x_shoulder, e11$cutoff, "keep_below")
ok(abs(mean(kp_cap) - 0.95) < 0.02, "with cap, keeps ~95% (percentile fallback)")

# ------------------------------------------------------------------ 12. max_remove_frac: clean bimodal -> no regression
sect("12. Clean bimodal (90% live, 10% dead) with max_remove_frac=0.50 -> still uses valley")
# The same clean bimodal from test 1, but now with the safety cap. A genuine
# 10% dead population should NOT trigger the over-remove guard.
e12 <- estimate_threshold_1d(x_bi, direction = "keep_below", method = "auto", max_remove_frac = 0.50)
ok(e12$status == "auto_ok", "clean bimodal with cap -> still auto_ok (no false alarm)")
ok(grepl("valley", e12$method_used), "clean bimodal with cap -> still uses valley method")
kp12 <- apply_threshold_1d(x_bi, e12$cutoff, "keep_below")
ok(abs(mean(kp12) - 0.90) < 0.03, "clean bimodal with cap -> still keeps ~90%")

# ------------------------------------------------------------------ 13. max_remove_frac: 50% dead -> boundary (exactly 50% allowed)
sect("13. 50% dead with max_remove_frac=0.50 -> boundary: exactly 50% is allowed (strict >)")
# 50% live, 50% dead. The valley cutoff removes exactly ~50%, which is NOT > 0.50,
# so the guard should NOT fire and the valley should be used.
x_50 <- c(rnorm(5000, 0, 0.6), rnorm(5000, 6, 0.6))
e13 <- estimate_threshold_1d(x_50, direction = "keep_below", method = "auto", max_remove_frac = 0.50)
ok(e13$status == "auto_ok", "50% dead with cap=0.50 -> auto_ok (boundary: 50% is not > 50%)")
ok(grepl("valley", e13$method_used), "50% dead -> still uses valley method")
kp13 <- apply_threshold_1d(x_50, e13$cutoff, "keep_below")
ok(abs(mean(kp13) - 0.50) < 0.05, "50% dead -> keeps ~50% (valley cutoff preserved)")
# But with a slightly lower cap (0.45), the guard SHOULD fire:
e13b <- estimate_threshold_1d(x_50, direction = "keep_below", method = "auto", max_remove_frac = 0.45)
ok(e13b$status == "REVIEW_overremove", "50% dead with cap=0.45 -> REVIEW_overremove (50% > 45%)")
ok(e13b$method_used == "percentile", "50% dead with cap=0.45 -> falls back to percentile")

# ------------------------------------------------------------------ summary
cat(sprintf("\n==== %d passed, %d failed ====\n", .n_pass, .n_fail))
if (.n_fail > 0) { cat("FAILURES:\n"); cat(paste0("  - ", .fails, collapse = "\n"), "\n"); quit(status = 1) }
cat("ALL TESTS PASSED\n")
