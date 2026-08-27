#!/usr/bin/env Rscript
# run_susie_finemap.R -- single-dataset SuSiE fine-mapping from GWAS summary statistics + a signed-r
# LD matrix, with the SuSiE LD-mismatch diagnostics that make the result trustworthy.
#
# This is the single-trait analytical core: unlike coloc::runsusie (which runs SuSiE inside the two-trait
# coloc.susie colocalization workflow), here we call susieR::susie_rss() directly to produce the 95%
# credible set(s) + per-variant PIP for ONE trait.
#
# Inputs:
#   --sumstats   harmonized GWAS TSV with columns: snp, effect_allele, other_allele, beta, se, pval
#                (pos, eaf, varid optional but carried through). z is formed as beta/se.
#   --ld         signed-r LD matrix TSV (square; header + rownames = snp ids), oriented to effect allele
#                (produced by ld_utils.py). NOT r^2.
#   --ld-snps    ordered snp-id list matching the LD matrix (one per line).
#
# Key params (validated on the PROX1 T2D locus: rs340874 PIP=0.96, estimated_s=0.019):
#   susie_rss(z, R, n, L=10, coverage=0.95, estimate_residual_variance=FALSE, check_prior=TRUE)
#
# Guardrails:
#   * estimate_s_rss()  -> LD-mismatch lambda (s). ~0 = z consistent with LD; large = mismatch.
#   * kriging_rss()     -> per-variant expected-vs-observed z; flags outliers/allele issues.
#   * Drops NA / non-finite z (missing beta/se), warns on MHC and very large windows.
#
# Usage:
#   Rscript run_susie_finemap.R --sumstats tidy_b38.tsv --ld ld.tsv --ld-snps ld_snps.txt \
#       --type cc --n 655666 --L 10 --coverage 0.95 --out-dir finemap_results

suppressWarnings(suppressMessages({
  ok <- requireNamespace("susieR", quietly = TRUE) &&
        requireNamespace("data.table", quietly = TRUE) &&
        requireNamespace("jsonlite", quietly = TRUE)
}))
if (!ok) {
  message("Installing susieR / data.table / jsonlite ...")
  install.packages(c("susieR", "data.table", "jsonlite", "Rfast"),
                   repos = "https://cloud.r-project.org")
}
suppressWarnings(suppressMessages({
  library(susieR); library(data.table); library(jsonlite)
}))

# ---------- args ----------
args <- commandArgs(trailingOnly = TRUE)
getarg <- function(flag, default = NULL, is_flag = FALSE) {
  i <- match(flag, args)
  if (is.na(i)) return(default)
  if (is_flag) return(TRUE)
  args[i + 1]
}
sumstats_f <- getarg("--sumstats")
ld_f       <- getarg("--ld")
ldsnps_f   <- getarg("--ld-snps")
trait_type <- getarg("--type", "quant")            # quant | cc (only affects reporting; z is beta/se)
n_arg      <- getarg("--n", NA)                      # sample size; else read from sumstats 'n' col
L          <- as.integer(getarg("--L", "10"))
coverage   <- as.numeric(getarg("--coverage", "0.95"))
min_abs_corr <- as.numeric(getarg("--min-abs-corr", "0.5"))
id_col     <- getarg("--id-col", "snp")
out_dir    <- getarg("--out-dir", "finemap_results")
s_warn     <- as.numeric(getarg("--s-warn", "0.1"))  # estimated_s above this -> loud caution

if (is.null(sumstats_f) || is.null(ld_f) || is.null(ldsnps_f))
  stop("ERROR: --sumstats, --ld, and --ld-snps are required.")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

# ---------- load ----------
ss <- as.data.frame(fread(sumstats_f))
if (!id_col %in% names(ss)) stop(sprintf("ERROR: id col '%s' not in sumstats (%s)",
                                         id_col, paste(head(names(ss)), collapse = ",")))
need <- c("effect_allele", "other_allele", "beta", "se")
miss <- setdiff(need, names(ss))
if (length(miss)) stop(sprintf("ERROR: sumstats missing columns: %s", paste(miss, collapse = ",")))

ld_snps <- readLines(ldsnps_f)
R <- as.matrix(fread(ld_f), rownames = 1)
# align matrix to the snp order in ld_snps if needed
if (!all(rownames(R) == ld_snps)) {
  common0 <- intersect(ld_snps, rownames(R))
  R <- R[common0, common0, drop = FALSE]
  ld_snps <- common0
}

# ---------- intersect sumstats <-> LD ----------
ss <- ss[ss[[id_col]] %in% ld_snps, , drop = FALSE]
ss <- ss[!duplicated(ss[[id_col]]), , drop = FALSE]
# z, drop non-finite (missing beta/se)
ss$z <- ss$beta / ss$se
n_before <- nrow(ss)
bad <- !is.finite(ss$z) | !is.finite(ss$se) | ss$se <= 0
n_dropped_z <- sum(bad)
ss <- ss[!bad, , drop = FALSE]

common <- intersect(ss[[id_col]], ld_snps)
ss <- ss[match(common, ss[[id_col]]), , drop = FALSE]
R <- R[common, common, drop = FALSE]
z <- ss$z
names(z) <- ss[[id_col]]
n_snps <- length(z)
if (n_snps < 5) stop(sprintf("ERROR: only %d variants shared between sumstats and LD -- check id_col / build / region.", n_snps))

# sample size
N <- suppressWarnings(as.numeric(n_arg))
if (is.na(N)) {
  if ("n" %in% names(ss)) N <- round(stats::median(as.numeric(ss$n), na.rm = TRUE))
  else stop("ERROR: sample size unknown -- pass --n or include an 'n' column in the sumstats.")
}

# ---------- symmetry hygiene (tiny float asymmetry from PLINK export) ----------
asym <- max(abs(R - t(R)), na.rm = TRUE)
if (asym > 0) R <- (R + t(R)) / 2
diag(R) <- 1

# ---------- MHC / window warnings ----------
warns <- c()
if ("pos" %in% names(ss)) {
  chrs <- unique(ss$chr); # may be absent
  posr <- range(ss$pos, na.rm = TRUE)
}
if (n_snps > 10000) warns <- c(warns, sprintf("large window: %d SNPs (fine-mapping may be slow/unstable)", n_snps))

# ---------- LD-mismatch diagnostics ----------
est_s <- tryCatch(estimate_s_rss(z = z, R = R, n = N), error = function(e) NA_real_)
krig <- tryCatch(kriging_rss(z = z, R = R, n = N), error = function(e) NULL)
n_krig_outlier <- if (!is.null(krig) && !is.null(krig$conditional_dist)) {
  cd <- krig$conditional_dist
  if ("logLR" %in% names(cd)) sum(cd$logLR > 2 & abs(cd$z) > 2, na.rm = TRUE) else NA_integer_
} else NA_integer_

if (!is.na(est_s) && est_s > s_warn)
  warns <- c(warns, sprintf("HIGH estimated_s = %.4f (> %.2f): likely LD/ancestry mismatch -- credible set is UNTRUSTWORTHY. Check --superpop matches GWAS ancestry or use in-sample LD.", est_s, s_warn))

# ---------- SuSiE ----------
fit <- tryCatch(
  susie_rss(z = z, R = R, n = N, L = L, coverage = coverage,
            estimate_residual_variance = FALSE, check_prior = TRUE),
  error = function(e) { message("susie_rss error: ", conditionMessage(e)); NULL })
if (is.null(fit)) stop("ERROR: susie_rss failed -- see message above (often LD mismatch or degenerate window).")

converged <- isTRUE(fit$converged)
pip <- susie_get_pip(fit)
cs <- susie_get_cs(fit, coverage = coverage, min_abs_corr = min_abs_corr, Xcorr = R)

# ---------- assemble outputs ----------
ss$pip <- pip[match(ss[[id_col]], names(z))]
allv <- ss[, intersect(c(id_col, "varid", "chr", "pos", "effect_allele", "other_allele",
                         "beta", "se", "pval", "eaf", "maf", "z", "pip"), names(ss)), drop = FALSE]
allv <- allv[order(-allv$pip), ]
fwrite(allv, file.path(out_dir, "all_variants_pip.csv"))

cs_rows <- list()
if (length(cs$cs) > 0) {
  for (k in seq_along(cs$cs)) {
    idx <- cs$cs[[k]]
    csname <- names(cs$cs)[k]
    sub <- ss[idx, , drop = FALSE]
    sub$cs <- csname
    sub$cs_size <- length(idx)
    sub$within_cs_min_abs_corr <- if (!is.null(cs$purity)) cs$purity[k, "min.abs.corr"] else NA_real_
    cs_rows[[k]] <- sub
  }
  cs_df <- do.call(rbind, cs_rows)
  cs_df <- cs_df[order(cs_df$cs, -cs_df$pip), ]
  keep <- intersect(c("cs", "cs_size", "within_cs_min_abs_corr", id_col, "varid", "chr", "pos",
                      "effect_allele", "other_allele", "beta", "se", "pval", "eaf", "z", "pip"),
                    names(cs_df))
  fwrite(cs_df[, keep, drop = FALSE], file.path(out_dir, "credible_set.csv"))
  n_cs <- length(cs$cs)
} else {
  # write an empty file with a header so downstream never crashes
  fwrite(data.frame(cs = character(), snp = character(), pip = numeric()),
         file.path(out_dir, "credible_set.csv"))
  n_cs <- 0
  warns <- c(warns, "SuSiE returned 0 credible sets (weak signal, too few SNPs, or LD mismatch).")
}

saveRDS(fit, file.path(out_dir, "susie_fit.rds"))

top <- allv[which.max(allv$pip), ]
report <- list(
  n_snps_analyzed = n_snps,
  n_dropped_na_z = n_dropped_z,
  N = N,
  trait_type = trait_type,
  L = L, coverage = coverage, min_abs_corr = min_abs_corr,
  susie_converged = converged,
  estimated_s = if (is.na(est_s)) NULL else round(est_s, 5),
  kriging_outliers = n_krig_outlier,
  ld_asymmetry_fixed = asym,
  n_credible_sets = n_cs,
  top_pip = list(snp = as.character(top[[id_col]]), pip = round(top$pip, 4),
                 pval = if ("pval" %in% names(top)) top$pval else NULL),
  warnings = warns
)
write(toJSON(report, auto_unbox = TRUE, pretty = TRUE, null = "null"),
      file.path(out_dir, "susie_report.json"))

# ---------- console summary ----------
cat(sprintf("\u2713 SuSiE fine-mapping complete\n"))
cat(sprintf("  variants analyzed : %d  (dropped NA-z: %d)\n", n_snps, n_dropped_z))
cat(sprintf("  sample size N     : %s\n", format(N, big.mark = ",")))
cat(sprintf("  converged         : %s\n", converged))
cat(sprintf("  estimated_s (LD-mismatch lambda): %s  %s\n",
            ifelse(is.na(est_s), "NA", sprintf("%.4f", est_s)),
            ifelse(!is.na(est_s) && est_s > s_warn, "  <-- HIGH: CAUTION (see warnings)", "(~0 = OK)")))
cat(sprintf("  credible sets     : %d\n", n_cs))
if (n_cs > 0) {
  cs_df2 <- fread(file.path(out_dir, "credible_set.csv"))
  for (csn in unique(cs_df2$cs)) {
    sub <- cs_df2[cs_df2$cs == csn, ]
    cat(sprintf("    %s: size=%d  top=%s (PIP=%.3f)  min|r|=%.3f\n",
                csn, nrow(sub), sub[[id_col]][1], max(sub$pip),
                suppressWarnings(as.numeric(sub$within_cs_min_abs_corr[1]))))
  }
}
cat(sprintf("  top PIP variant   : %s (PIP=%.3f)\n", as.character(top[[id_col]]), top$pip))
if (length(warns)) { cat("  WARNINGS:\n"); for (w in warns) cat("   - ", w, "\n") }
