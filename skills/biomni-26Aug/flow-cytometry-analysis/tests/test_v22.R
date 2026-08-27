#!/usr/bin/env Rscript
# =====================================================================================
# test_v22.R -- deterministic unit tests for the v2.2.0 additions to the flow-cytometry
# skill (all PURE numeric functions; no FCS I/O, no Bioconductor gating stack required):
#   item 1  time-based acquisition QC   : flow_rate_qc / signal_stability_qc /
#                                         margin_events / time_acquisition_qc
#   item 2  compensation/spillover QC   : spillover_diagnostics / read_spillover_csv /
#                                         align_spillover
#   item 4  batch-aware harmonization   : harmonize_cutoffs
#   item 6  openCyto backend (helpers)  : opencyto_default_template + shipped templates
#
# Runs on base R alone. Exits non-zero on any failure. Mirrors tests/test_gating_engine.R.
#   Rscript tests/test_v22.R
# =====================================================================================
suppressWarnings(suppressMessages({
  here <- tryCatch(dirname(sys.frame(1)$ofile), error = function(e) NA)
  eng  <- "scripts/gating_engine.R"
  if (!file.exists(eng)) eng <- file.path(dirname(getwd()), "scripts", "gating_engine.R")
  if (!file.exists(eng)) eng <- "/mnt/results/skills/flow-cytometry-analysis/scripts/gating_engine.R"
  source(eng)
  oc <- "scripts/gating_opencyto.R"
  if (!file.exists(oc)) oc <- file.path(dirname(getwd()), "scripts", "gating_opencyto.R")
  if (!file.exists(oc)) oc <- "/mnt/results/skills/flow-cytometry-analysis/scripts/gating_opencyto.R"
  OC_SRC <- tryCatch({ source(oc); TRUE }, error = function(e) { message("oc source failed: ", conditionMessage(e)); FALSE })
  SCRIPT_DIR <- dirname(oc)
}))
set.seed(1234)

.n_pass <- 0L; .n_fail <- 0L; .fails <- character(0)
ok <- function(cond, msg) {
  if (isTRUE(cond)) { .n_pass <<- .n_pass + 1L; cat(sprintf("  PASS: %s\n", msg)) }
  else { .n_fail <<- .n_fail + 1L; .fails <<- c(.fails, msg); cat(sprintf("  FAIL: %s\n", msg)) }
}
sect <- function(s) cat(sprintf("\n== %s ==\n", s))

# =====================================================================================
sect("1. flow_rate_qc: clean uniform = nothing flagged; clog spike = flagged bin")
tt_clean <- runif(10000, 0, 100)
r_clean  <- flow_rate_qc(tt_clean, n_bins = 100, mad_k = 5)
ok(r_clean$status == "ok", "clean uniform Time -> status ok")
ok(length(r_clean$keep) == length(tt_clean), "keep mask length == n events")
ok(is.finite(r_clean$pct) && r_clean$pct < 10, "clean uniform flags < 10% (permissive MAD)")

tt_clog <- c(runif(9500, 0, 100), rep(50, 500))          # 500-event acquisition spike at t=50
r_clog  <- flow_rate_qc(tt_clog, n_bins = 100, mad_k = 5)
ok(r_clog$n_flagged_bins >= 1, "clog spike flags >= 1 rate bin")
ok(r_clog$pct > 0 && sum(r_clog$flagged) >= 500, "the 500 spike events are flagged")
ok(all(r_clog$flagged[9501:10000]), "every event in the spike window is flagged")

r_deg <- flow_rate_qc(rep(7, 5000), n_bins = 100)         # zero-range Time
ok(r_deg$status == "skipped" && all(r_deg$keep), "degenerate (constant) Time -> skipped, keeps all")
r_few <- flow_rate_qc(runif(20), n_bins = 100)
ok(r_few$status == "skipped" && all(r_few$keep), "< 50 events -> skipped, keeps all")

# =====================================================================================
sect("2. signal_stability_qc: stable channels clean; a late-time drift is caught")
n <- 10000; tt <- runif(n, 0, 100)
expr_stab <- cbind(a = rnorm(n), b = rnorm(n))
s_stab <- signal_stability_qc(tt, expr_stab, n_bins = 100, mad_k = 5)
ok(s_stab$status == "ok", "stable signal -> status ok")
ok(s_stab$pct < 10, "stable signal flags < 10%")

expr_drift <- cbind(stable = rnorm(n), drift_ch = rnorm(n) + ifelse(tt > 90, 15, 0))
s_drift <- signal_stability_qc(tt, expr_drift, n_bins = 100, mad_k = 5)
ok(s_drift$pct > 0, "late-time drift -> some bins flagged")
ok(nrow(s_drift$channel) >= 1 && s_drift$channel$channel[1] == "drift_ch",
   "drifting channel ranks first by max|z|")
ok(mean(s_drift$flagged[tt > 90]) > 0.5, "majority of late-time (drift) events flagged")
serr <- try(signal_stability_qc(1:10, matrix(0, 5, 2)), silent = TRUE)
ok(inherits(serr, "try-error"), "nrow(expr) != length(time) -> error (contract guard)")

# =====================================================================================
sect("3. margin_events: boundary pile-ups flagged; wide explicit ranges flag nothing")
m <- matrix(rnorm(2000), 1000, 2, dimnames = list(NULL, c("A", "B")))
m[1:15, "A"] <- 100    # high-edge pile-up on A
m[16:25, "B"] <- -100  # low-edge  pile-up on B
me <- margin_events(m, ranges = NULL)
ok(sum(me$flagged) >= 25 && me$pct > 0, "injected edge pile-ups flagged")
okA <- me$channel[me$channel$channel == "A", ]
okB <- me$channel[me$channel$channel == "B", ]
ok(nrow(okA) == 1 && okA$n_high >= 15, "channel A reports >= 15 high-edge events")
ok(nrow(okB) == 1 && okB$n_low  >= 10, "channel B reports >= 10 low-edge events")
me_wide <- margin_events(m, ranges = list(c(-1e4, 1e4), c(-1e4, 1e4)))
ok(me_wide$pct == 0, "explicit wide ranges -> no margin events")

# =====================================================================================
sect("4. time_acquisition_qc: OR-combines checks; per-check pct; subset checks -> NA")
tt2   <- c(runif(9500, 0, 100), rep(50, 500))
expr2 <- cbind(stable = rnorm(10000),
               drift_ch = rnorm(10000) + ifelse(c(runif(9500,0,100), rep(50,500)) > 90, 15, 0))
expr2[1:20, "stable"] <- max(expr2[, "stable"]) + 5  # a few margin events
tqc <- time_acquisition_qc(tt2, expr2, checks = c("rate", "signal", "margin"),
                           n_bins = 100, mad_k = 5)
ok(length(tqc$keep) == 10000 && all(tqc$keep == !tqc$flagged), "keep == !flagged, full length")
ok(is.finite(tqc$pct_rate) && tqc$pct_rate > 0, "rate check populated & positive (spike)")
ok(is.finite(tqc$pct_margin) && tqc$pct_margin > 0, "margin check populated & positive")
ok(abs(tqc$pct_total - 100 * mean(tqc$flagged)) < 1e-9, "pct_total == 100*mean(flagged)")
tqc_rate <- time_acquisition_qc(tt2, expr2, checks = "rate")
ok(is.na(tqc_rate$pct_signal) && is.na(tqc_rate$pct_margin), "unrequested checks report NA")

# =====================================================================================
sect("5. spillover_diagnostics: identity/well/ill/singular/malformed classification")
d_id <- spillover_diagnostics(diag(4))
ok(d_id$square && d_id$finite && d_id$n == 4L, "identity: square, finite, n=4")
ok(abs(d_id$kappa - 1) < 1e-8 && abs(d_id$rcond - 1) < 1e-8, "identity: kappa==1, rcond==1")
ok(identical(d_id$singular, FALSE) && d_id$verdict == "well", "identity -> well, not singular")

m_well <- diag(4); m_well[upper.tri(m_well)] <- 0.05; m_well[lower.tri(m_well)] <- 0.03
d_well <- spillover_diagnostics(m_well)
ok(d_well$verdict == "well" && d_well$kappa < 1e3, "typical small off-diagonal -> well")

m_ill <- matrix(c(1, 1, 1, 1 + 1e-8), 2, 2)   # near-collinear columns
d_ill <- spillover_diagnostics(m_ill)
ok(d_ill$kappa > 1e3 && d_ill$verdict == "ill" && identical(d_ill$singular, FALSE),
   "near-collinear -> ill (kappa>1e3), not singular")

d_sing <- spillover_diagnostics(matrix(1, 2, 2))  # rank-deficient (smin ~ 0 up to FP noise)
ok(identical(d_sing$singular, TRUE) && d_sing$verdict == "singular" && d_sing$kappa > 1e6,
   "rank-deficient -> singular flag fires, kappa huge/Inf")

ok(spillover_diagnostics(matrix(1:6, 2, 3))$verdict == "malformed", "non-square -> malformed")
ok(spillover_diagnostics(NULL)$verdict == "malformed", "NULL -> malformed")
m_na <- diag(2); m_na[1, 1] <- NA
ok(spillover_diagnostics(m_na)$verdict == "malformed", "non-finite entry -> malformed")

# kappa_max threshold governs the well/ill boundary
ok(spillover_diagnostics(m_well, kappa_max = 1.0)$verdict == "ill", "low kappa_max forces ill")
ok(spillover_diagnostics(m_well, kappa_max = 1e6)$verdict == "well", "high kappa_max keeps well")

# =====================================================================================
sect("6. read_spillover_csv + align_spillover: round-trip, intersection, symmetry")
chs <- c("Live", "CD3", "CD4", "CD8")
sm <- diag(4); dimnames(sm) <- list(chs, chs)
sm["CD3", "CD4"] <- 0.10; sm["CD4", "CD8"] <- 0.07
spf <- tempfile(fileext = ".csv"); utils::write.csv(sm, spf, row.names = TRUE)
sm_in <- read_spillover_csv(spf)
ok(all(dim(sm_in) == c(4, 4)), "spillover CSV round-trips to 4x4")
ok(all(colnames(sm_in) == chs) && all(rownames(sm_in) == chs), "channel names preserved")
ok(abs(sm_in["CD3", "CD4"] - 0.10) < 1e-9, "off-diagonal value preserved exactly")

al <- align_spillover(sm_in, c("CD3", "CD8", "Xtra"))
ok(al$ok && all(dim(al$matrix) == c(2, 2)), "align -> 2x2 intersection")
ok(all(rownames(al$matrix) == c("CD3", "CD8")), "intersection preserves requested order")
ok(al$info$n_matched == 2 && "Xtra" %in% al$info$missing_in_matrix, "missing channel reported")
ok(all(c("Live", "CD4") %in% al$info$extra_in_matrix), "extra matrix channels reported")
ok(!align_spillover(sm_in, c("Foo", "Bar"))$ok, "no overlap -> ok = FALSE")
m_ronly <- matrix(1:4, 2, 2); rownames(m_ronly) <- c("A", "B")   # colnames NULL
al_sym <- align_spillover(m_ronly, c("A", "B"))
ok(al_sym$ok && all(colnames(al_sym$matrix) == c("A", "B")), "row-only names -> colnames filled")

# =====================================================================================
sect("7. harmonize_cutoffs: confident stays; low-confidence borrows batch consensus")
mkrows <- function(sid, gate, cut, conf = NULL) {
  d <- data.frame(sample_id = sid, gate = gate, final_cutoff = cut, stringsAsFactors = FALSE)
  if (!is.null(conf)) d$valley_confidence <- conf
  d
}
# confident (conf=1) -> shrink weight 0 -> unchanged even though consensus=2
r_conf <- harmonize_cutoffs(mkrows(c("S1", "S2"), "g1", c(1, 3), c(1, 1)),
                            batch_map = c(S1 = "B1", S2 = "B1"))
ok(all(abs(r_conf$rows$harmonized_cutoff - c(1, 3)) < 1e-9), "conf=1 cutoffs unchanged")
ok(all(abs(r_conf$rows$batch_consensus - 2) < 1e-9), "batch consensus computed (median=2)")
ok(abs(r_conf$rows$final_cutoff[1] - r_conf$rows$harmonized_cutoff[1]) < 1e-9,
   "cutoff_col overwritten with harmonized value")

# low confidence (conf=0), full shrink -> both collapse to consensus=2
r_low <- harmonize_cutoffs(mkrows(c("S1", "S2"), "g1", c(1, 3), c(0, 0)),
                           batch_map = c(S1 = "B1", S2 = "B1"), shrink = 1.0)
ok(all(abs(r_low$rows$harmonized_cutoff - 2) < 1e-9), "conf=0, shrink=1 -> both == consensus (2)")

# partial confidence (conf=0.5) -> weight 0.5
r_half <- harmonize_cutoffs(mkrows(c("S1", "S2"), "g1", c(1, 3), c(0.5, 0.5)),
                            batch_map = c(S1 = "B1", S2 = "B1"), shrink = 1.0)
ok(all(abs(r_half$rows$harmonized_cutoff - c(1.5, 2.5)) < 1e-9), "conf=0.5 -> half-way to consensus")

# min_group: a lone sample in its (gate,batch) is left untouched
r_solo <- harmonize_cutoffs(
  rbind(mkrows(c("S1", "S2"), "g1", c(1, 3), c(0, 0)), mkrows("S3", "g1", 9, 0)),
  batch_map = c(S1 = "B1", S2 = "B1", S3 = "B2"))
solo <- r_solo$rows[r_solo$rows$sample_id == "S3", ]
ok(abs(solo$harmonized_cutoff - 9) < 1e-9, "lone (gate,batch) sample unchanged (min_group)")

# batch_map as data.frame == named vector
bm_df <- data.frame(sample_id = c("S1", "S2"), batch = c("B1", "B1"), stringsAsFactors = FALSE)
r_df <- harmonize_cutoffs(mkrows(c("S1", "S2"), "g1", c(1, 3), c(0, 0)), batch_map = bm_df)
ok(all(abs(r_df$rows$harmonized_cutoff - r_low$rows$harmonized_cutoff) < 1e-9),
   "data.frame batch_map == named-vector batch_map")

# n_batches / n_groups_harmonized bookkeeping
r_two <- harmonize_cutoffs(
  mkrows(c("S1", "S2", "S3", "S4"), "g1", c(1, 3, 5, 7), c(0, 0, 0, 0)),
  batch_map = c(S1 = "B1", S2 = "B1", S3 = "B2", S4 = "B2"))
ok(r_two$n_batches == 2 && r_two$n_groups_harmonized == 2, "2 batches -> 2 harmonized groups")

# missing valley_confidence column -> na_confidence (0) -> full shrink to consensus
r_nc <- harmonize_cutoffs(mkrows(c("S1", "S2"), "g1", c(1, 3)),  # no conf column
                          batch_map = c(S1 = "B1", S2 = "B1"), shrink = 1.0)
ok(all(abs(r_nc$rows$harmonized_cutoff - 2) < 1e-9), "no valley_confidence -> na_confidence full shrink")

# =====================================================================================
sect("8. openCyto helpers: default template resolution + shipped template integrity")
if (OC_SRC) {
  tf <- opencyto_default_template("flow", SCRIPT_DIR)
  tc <- opencyto_default_template("cytof", SCRIPT_DIR)
  ok(grepl("assets/gating_template_flow\\.csv$", tf), "flow -> gating_template_flow.csv path")
  ok(grepl("assets/gating_template_cytof\\.csv$", tc), "cytof -> gating_template_cytof.csv path")
  ok(file.exists(tf) && file.exists(tc), "both shipped templates exist on disk")
  ok(exists("run_opencyto_gating") && exists("opencyto_gating_transform"),
     "module exports run_opencyto_gating + opencyto_gating_transform")
  need_cols <- c("alias", "pop", "parent", "dims", "gating_method")
  for (tp in c(tf, tc)) {
    if (file.exists(tp)) {
      hdr <- colnames(utils::read.csv(tp, nrows = 1, check.names = FALSE))
      ok(all(need_cols %in% hdr), sprintf("%s has required openCyto columns", basename(tp)))
    }
  }
} else {
  cat("  (openCyto module did not source in this environment -- skipping item-6 helper tests)\n")
}

# =====================================================================================
cat(sprintf("\n==== %d passed, %d failed ====\n", .n_pass, .n_fail))
if (.n_fail > 0) { cat("FAILURES:\n"); cat(paste0("  - ", .fails, collapse = "\n"), "\n"); quit(status = 1) }
cat("ALL v2.2.0 TESTS PASSED\n")
