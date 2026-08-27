#!/usr/bin/env Rscript
# delta_gsva_stats.R -- Stage 5 (primary + concordance) of signature-response-enrichment.
#
# The poster's exact test: change-from-baseline dGSVA, non-responder (NR) vs responder (R)
# at the treatment endpoint, plus cross-cohort directional concordance via Fisher's method.
#
#   dGSVA(t) = GSVA(t) - GSVA(baseline)          # per patient, per gene set
#   endpoint contrast: NR vs R  (direction = mean_NR - mean_R; positive = higher in NR)
#   primary test: Wilcoxon rank-sum (two-sided p reported; one-sided NR>R p for Fisher)
#   t-test reported alongside
#   Fisher's method (one cohort per p): X = -2*sum(log p); p = pchisq(X, 2k, lower=FALSE)
#   Multiplicity: BH-FDR across the gene_set x timepoint family.
#   Marks: * nominal p<0.05, ** FDR<0.05.
#
# INPUT (per cohort, repeatable via --cohort blocks in a JSON manifest):
#   --manifest JSON list, each entry:
#     { "cohort": "NAME",
#       "gsva_csv": "gsva_scores.csv",         # sets x samples
#       "sample_meta_csv": "meta.csv",         # columns: sample_id, patient_id, timepoint
#       "response_csv": "response.csv",        # columns: patient_id, response_group (R/NR)
#       "baseline_tp": "0", "endpoint_tp": "12" }
#   --fdr-alpha 0.05
#   --cohort-parent (optional) JSON map {"cohort":"parent_study"} marking cohorts that are
#       splits of the SAME parent dataset (e.g. a discovery/refinement split). Fisher's
#       method assumes independence; sub-cohorts of one study are NOT independent
#       (pseudo-replication), so concordance is reported two ways when this is supplied:
#       (1) INDEPENDENT-ONLY (one representative per parent = the smallest-p / most
#           conservative? no -- we keep ALL distinct parents, collapsing each shared-parent
#           group to its single most significant cohort) -- the PREFERRED value; and
#       (2) ALL-COHORTS (every split combined) -- a SENSITIVITY CHECK only, flagged.
#       Cohorts absent from the map are treated as independent (their own parent).
#   --out-per-cohort  dGSVA per-cohort endpoint stats CSV
#   --out-concordance cross-cohort Fisher concordance CSV
#
# USAGE:
#   Rscript delta_gsva_stats.R --manifest cohorts.json \
#       --out-per-cohort dgsva_endpoint.csv --out-concordance concordance.csv \
#       [--cohort-parent parents.json]

suppressWarnings(suppressMessages({ library(optparse); library(jsonlite) }))

opt <- parse_args(OptionParser(option_list = list(
  make_option("--manifest", type = "character"),
  make_option("--fdr-alpha", type = "double", default = 0.05, dest = "fdr_alpha"),
  make_option("--cohort-parent", type = "character", default = NULL, dest = "parent_map"),
  make_option("--out-per-cohort", type = "character", dest = "out_pc"),
  make_option("--out-concordance", type = "character", dest = "out_conc")
)))

# Optional parent-study map for independence-aware concordance (see header).
parent_of <- list()
if (!is.null(opt$parent_map)) {
  pm <- fromJSON(opt$parent_map, simplifyVector = TRUE)
  parent_of <- as.list(pm)
}
get_parent <- function(cohort) {
  p <- parent_of[[cohort]]
  if (is.null(p) || is.na(p) || !nzchar(p)) cohort else p  # default: own parent (independent)
}

num_tp <- function(x) as.numeric(gsub("[^0-9.-]", "", as.character(x)))
sig_mark <- function(p_nom, fdr)
  ifelse(!is.na(fdr) & fdr < 0.05, "**",
         ifelse(!is.na(p_nom) & p_nom < 0.05, "*", ""))

cohorts <- fromJSON(opt$manifest, simplifyVector = FALSE)
all_rows <- list()

for (co in cohorts) {
  gsva <- as.matrix(read.csv(co$gsva_csv, row.names = 1, check.names = FALSE))
  meta <- read.csv(co$sample_meta_csv, stringsAsFactors = FALSE)
  resp <- read.csv(co$response_csv, stringsAsFactors = FALSE)
  base_tp <- co$baseline_tp; end_tp <- co$endpoint_tp

  # long: sample_id, patient_id, timepoint -> attach gsva per set
  sets <- rownames(gsva)
  for (gs in sets) {
    sc <- data.frame(sample_id = colnames(gsva), score = gsva[gs, ],
                     stringsAsFactors = FALSE)
    m <- merge(meta, sc, by = "sample_id")
    m <- merge(m, resp[, c("patient_id", "response_group")], by = "patient_id")
    # baseline & endpoint per patient
    b <- aggregate(score ~ patient_id,
                   data = m[as.character(m$timepoint) == as.character(base_tp), ], mean)
    e <- aggregate(score ~ patient_id,
                   data = m[as.character(m$timepoint) == as.character(end_tp), ], mean)
    names(b)[2] <- "base"; names(e)[2] <- "end"
    d <- merge(b, e, by = "patient_id")
    grp <- unique(m[, c("patient_id", "response_group")])
    d <- merge(d, grp, by = "patient_id")
    d$delta <- d$end - d$base
    nr <- d$delta[d$response_group == "NR"]; r <- d$delta[d$response_group == "R"]
    if (length(nr) < 2 || length(r) < 2) next
    diff <- mean(nr) - mean(r)
    w <- suppressWarnings(wilcox.test(nr, r))          # two-sided
    w1 <- suppressWarnings(wilcox.test(nr, r, alternative = "greater"))  # NR>R (Fisher)
    tt <- suppressWarnings(t.test(nr, r))
    all_rows[[length(all_rows) + 1]] <- data.frame(
      cohort = co$cohort, gene_set = gs, endpoint_tp = end_tp,
      n_NR = length(nr), n_R = length(r),
      diff_NR_minus_R = diff,
      p_wilcox = w$p.value, p_wilcox_oneside_NRgtR = w1$p.value,
      p_ttest = tt$p.value, stringsAsFactors = FALSE)
  }
}

pc <- do.call(rbind, all_rows)
if (is.null(pc)) stop("No testable gene_set x cohort with >=2 per group.")
# BH-FDR across the family (per cohort's gene_set x timepoint)
pc$fdr_wilcox <- p.adjust(pc$p_wilcox, method = "BH")
pc$mark <- mapply(sig_mark, pc$p_wilcox, pc$fdr_wilcox)
write.csv(pc, opt$out_pc, row.names = FALSE)
message(sprintf("[dgsva] wrote per-cohort endpoint stats (%d rows) -> %s",
                nrow(pc), opt$out_pc))

# ---- cross-cohort concordance (Fisher's method, one-sided NR>R) ----
# Fisher assumes INDEPENDENT p-values. Sub-cohorts of one parent study are not
# independent, so we report an independent-only value (preferred) and an all-cohorts
# value (sensitivity), and flag when they differ because of shared parents.
fisher_combine <- function(pvals) {
  pvals <- pvals[is.finite(pvals) & pvals > 0]
  k <- length(pvals)
  if (k == 0) return(c(NA, 0))
  X <- -2 * sum(log(pvals))
  c(pchisq(X, df = 2 * k, lower.tail = FALSE), k)
}
pc$parent <- vapply(pc$cohort, get_parent, character(1))
n_parents_total <- length(unique(pc$parent))
n_cohorts_total <- length(unique(pc$cohort))
shared_parents <- n_parents_total < n_cohorts_total

conc_rows <- list()
for (gs in unique(pc$gene_set)) {
  sub <- pc[pc$gene_set == gs, ]
  all_same_dir <- all(sub$diff_NR_minus_R > 0)  # NR>R in every cohort?
  # ALL-COHORTS (sensitivity): every split contributes a p-value.
  fc_all <- fisher_combine(sub$p_wilcox_oneside_NRgtR)
  # INDEPENDENT-ONLY (preferred): collapse each parent to its single most significant
  # (smallest one-sided p) cohort, so no parent is counted more than once.
  idx_keep <- unlist(lapply(split(seq_len(nrow(sub)), sub$parent), function(ix)
    ix[which.min(sub$p_wilcox_oneside_NRgtR[ix])]))
  sub_ind <- sub[idx_keep, ]
  fc_ind <- fisher_combine(sub_ind$p_wilcox_oneside_NRgtR)
  conc_rows[[length(conc_rows) + 1]] <- data.frame(
    gene_set = gs,
    n_cohorts = fc_all[2], n_independent_parents = fc_ind[2],
    all_cohorts_NR_gt_R = all_same_dir,
    fisher_one_sided_p_independent = fc_ind[1],   # PREFERRED
    fisher_one_sided_p_all_cohorts = fc_all[1],   # sensitivity only
    shared_parent_cohorts = shared_parents,
    stringsAsFactors = FALSE)
}
conc <- do.call(rbind, conc_rows)
write.csv(conc, opt$out_conc, row.names = FALSE)
if (shared_parents) {
  dup <- unique(pc$parent[duplicated(pc$parent)])
  message(sprintf(paste0("[dgsva] WARNING: %d cohort(s) share a parent study (%s). ",
                         "Fisher assumes independence -- the 'independent' column ",
                         "(one cohort per parent) is the value to report; ",
                         "'all_cohorts' is a SENSITIVITY CHECK ONLY (pseudo-replicated)."),
                  n_cohorts_total - n_parents_total, paste(dup, collapse = ", ")))
}
message("[dgsva] cross-cohort concordance (Fisher one-sided NR>R):")
apply(conc, 1, function(r)
  message(sprintf("  %-26s parents=%s/%s cohorts allNR>R=%s  Fisher p(indep)=%s  p(all)=%s",
                  r["gene_set"], r["n_independent_parents"], r["n_cohorts"],
                  r["all_cohorts_NR_gt_R"],
                  formatC(as.numeric(r["fisher_one_sided_p_independent"]), format = "g", digits = 3),
                  formatC(as.numeric(r["fisher_one_sided_p_all_cohorts"]), format = "g", digits = 3))))
if (nrow(conc) && any(conc$n_independent_parents < 2, na.rm = TRUE))
  message(paste0("[dgsva] NOTE: <2 INDEPENDENT parent studies for some sets; ",
                 "the independent Fisher concordance is n/a there -- report per-cohort ",
                 "effects instead of a combined p."))
