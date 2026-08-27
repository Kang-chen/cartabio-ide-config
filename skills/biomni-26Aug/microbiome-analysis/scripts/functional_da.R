#!/usr/bin/env Rscript
# =============================================================================
# Functional differential abundance on PICRUSt2 enzyme (EC) metagenome predictions.
# ADAPTABLE TEMPLATE - edit CONFIG, then:
#   micromamba run -n base Rscript functional_da.R
# Requires: ALDEx2, data.table
#
# DEFAULT feature space is EC numbers (IUBMB Enzyme Commission), PICRUSt2's default
# output. KEGG Orthology (KO) is NOT used by default: KEGG is not licensed for
# commercial use (see references/DATA_SOURCES.md). The script is feature-agnostic, so
# an academic user covered by KEGG's terms may point FEATURE_UNSTRAT at their own KO
# table and rename OUTFILE accordingly (no KEGG data ships with this skill).
#
# NOTE: PICRUSt2 predicts genomic POTENTIAL. Expect MANY significant gene
# families - they reflect correlated, community-wide taxonomic shifts, not many
# independent changes. Interpret via curated metabolite gene-set DIRECTION
# (see metabolite_modules.py), not raw significance counts.
# =============================================================================
suppressMessages({ library(ALDEx2); library(data.table) })

# ------------------------------ CONFIG ---------------------------------------
# Default: EC predictions (license-clean). For academic KO use, swap to
# picrust2_out/KO_metagenome_out/pred_metagenome_unstrat.tsv.gz and update OUTFILE.
FEATURE_UNSTRAT <- "picrust2_out/EC_metagenome_out/pred_metagenome_unstrat.tsv.gz"
METADATA        <- "metadata.tsv"
GROUP_COL       <- "group"
PREVALENCE      <- 0.10
OUTFILE         <- "results/tables/functional_da_EC.csv"
SEED            <- 42
# -----------------------------------------------------------------------------
set.seed(SEED); dir.create(dirname(OUTFILE), recursive = TRUE, showWarnings = FALSE)

mat <- as.data.frame(fread(FEATURE_UNSTRAT)); rownames(mat) <- mat[[1]]; mat[[1]] <- NULL
md  <- as.data.frame(fread(METADATA));        rownames(md)  <- md[[1]];  md[[1]]  <- NULL
common <- intersect(colnames(mat), rownames(md))
mat <- mat[, common, drop = FALSE]; md <- md[common, , drop = FALSE]

keep <- rowSums(mat > 0) / ncol(mat) >= PREVALENCE
mat  <- mat[keep, , drop = FALSE]
conds <- as.character(md[[GROUP_COL]])
cat(sprintf("ALDEx2 on %d gene families x %d samples\n", nrow(mat), ncol(mat)))

x <- aldex(round(mat), conds, mc.samples = 128, test = "t", effect = TRUE, denom = "all")
# diff.btw sign = (group2 - group1) alphabetically. Positive = higher in the
# alphabetically-later group. Document which group that is for your data.
out <- data.frame(feature = rownames(x),
                  clr_diff = x$diff.btw, effect_size = x$effect,
                  welch_p = x$we.ep, welch_q = x$we.eBH,
                  wilcox_p = x$wi.ep, wilcox_q = x$wi.eBH,
                  clr_abund = x$rab.all)
out <- out[order(out$wilcox_q), ]
fwrite(out, OUTFILE)
cat(sprintf("Significant (wilcox q<0.05): %d / %d\n",
            sum(out$wilcox_q < 0.05, na.rm = TRUE), nrow(out)))
cat("Wrote ", OUTFILE, "\n")
