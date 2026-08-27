#!/usr/bin/env Rscript
# =============================================================================
# 16S community structure + differential abundance (3-method consensus)
# ADAPTABLE TEMPLATE - edit the CONFIG block, then run:
#   micromamba run -n base Rscript da_diversity.R
#
# Requires: phyloseq, ANCOMBC (ancombc2), ALDEx2, Maaslin2, lme4, vegan,
#           dplyr, tidyr, data.table
# See references/commands_and_environment.md for install recipes.
# =============================================================================
suppressMessages({
  library(phyloseq); library(vegan); library(lme4)
  library(dplyr); library(tidyr); library(data.table)
})

# ------------------------------ CONFIG ---------------------------------------
FEATURE_TABLE <- "feature_table.tsv"   # tab-delim, first col = feature ID, cols = samples (counts)
TAXONOMY      <- "taxonomy.tsv"        # feature ID + taxonomy ranks (Kingdom..Genus/Species)
METADATA      <- "metadata.tsv"        # first col = sample ID; must contain GROUP_COL
OUTDIR        <- "results/tables"
GROUP_COL     <- "group"               # 2-level grouping column (e.g. Case/Control)
REF_LEVEL     <- "HC"                  # reference (control) level of GROUP_COL
SUBJECT_COL   <- "host_subject_id"     # subject ID for repeated-measures; set NA if 1 sample/subject
COVARIATES    <- c("sex", "bmi_num")   # adjust DA for these; character(0) for none
PREVALENCE    <- 0.10                  # keep features present in >= this fraction of samples
RAREFY_DEPTH  <- 10000                 # alpha-diversity rarefaction depth
AGG_RANK      <- "Genus"               # taxonomic rank for DA
SEED          <- 42
# -----------------------------------------------------------------------------
set.seed(SEED); dir.create(OUTDIR, recursive = TRUE, showWarnings = FALSE)
has_subject <- !is.na(SUBJECT_COL) && nzchar(SUBJECT_COL)

## ---- load ----
otu <- as.data.frame(fread(FEATURE_TABLE)); rownames(otu) <- otu[[1]]; otu[[1]] <- NULL
tax <- as.data.frame(fread(TAXONOMY));      rownames(tax) <- tax[[1]]; tax[[1]] <- NULL
md  <- as.data.frame(fread(METADATA));      rownames(md)  <- md[[1]];  md[[1]]  <- NULL
common <- intersect(colnames(otu), rownames(md))
stopifnot(length(common) > 0)
otu <- otu[, common, drop = FALSE]; md <- md[common, , drop = FALSE]
md[[GROUP_COL]] <- relevel(factor(md[[GROUP_COL]]), ref = REF_LEVEL)

ps <- phyloseq(otu_table(as.matrix(otu), taxa_are_rows = TRUE),
               tax_table(as.matrix(tax)),
               sample_data(md))
cat(sprintf("Loaded %d features x %d samples | groups: %s\n",
            ntaxa(ps), nsamples(ps),
            paste(names(table(md[[GROUP_COL]])), table(md[[GROUP_COL]]), collapse=", ")))

## ============================ 1. ALPHA DIVERSITY =============================
psr <- rarefy_even_depth(ps, sample.size = RAREFY_DEPTH, rngseed = SEED,
                         replace = FALSE, verbose = FALSE)
alpha <- estimate_richness(psr, measures = c("Observed","Shannon","InvSimpson"))
alpha$sample <- rownames(alpha); alpha[[GROUP_COL]] <- md[alpha$sample, GROUP_COL]
if (has_subject) alpha$subject <- md[alpha$sample, SUBJECT_COL]
# Faith_PD if a tree is present (attach tree to ps beforehand if available)
fwrite(alpha, file.path(OUTDIR, "alpha_diversity.csv"))

alpha_stats <- lapply(c("Observed","Shannon","InvSimpson"), function(m){
  if (has_subject) {
    # linear mixed model with subject random effect (handles repeated measures)
    fit <- tryCatch(lmer(reformulate(c(GROUP_COL, "(1|subject)"), response = m), data = alpha),
                    error = function(e) NULL)
    if (is.null(fit)) return(NULL)
    co <- summary(fit)$coefficients
    grp_row <- grep(GROUP_COL, rownames(co))[1]
    p <- 2*pnorm(-abs(co[grp_row, "t value"]))
    data.frame(metric=m, estimate=co[grp_row,"Estimate"], se=co[grp_row,"Std. Error"],
               p_value=p, method="LMM(subject RE)")
  } else {
    w <- wilcox.test(reformulate(GROUP_COL, response = m), data = alpha)
    data.frame(metric=m, estimate=NA, se=NA, p_value=w$p.value, method="Wilcoxon")
  }
}) %>% bind_rows()
# subject-mean Wilcoxon sensitivity analysis when repeated measures
if (has_subject) {
  sens <- lapply(c("Observed","Shannon","InvSimpson"), function(m){
    agg <- alpha %>% group_by(subject) %>%
      summarise(v=mean(.data[[m]]), g=first(.data[[GROUP_COL]]), .groups="drop")
    w <- wilcox.test(v ~ g, data = agg)
    data.frame(metric=m, estimate=NA, se=NA, p_value=w$p.value, method="Wilcoxon(subj-mean)")
  }) %>% bind_rows()
  alpha_stats <- bind_rows(alpha_stats, sens)
}
alpha_stats$p_adj <- p.adjust(alpha_stats$p_value, method = "BH")
fwrite(alpha_stats, file.path(OUTDIR, "alpha_diversity_stats.csv"))

## ============================ 2. BETA DIVERSITY =============================
# Bray-Curtis on relative abundance. For UniFrac, attach a phylo tree to ps and
# add phyloseq::distance(ps, "unifrac"/"wunifrac").
psrel <- transform_sample_counts(ps, function(x) x/sum(x))
bc <- phyloseq::distance(psrel, method = "bray")
lab <- attr(bc, "Labels")
grp <- md[[GROUP_COL]][match(lab, rownames(md))]
# subject-level permutation (block by subject) controls pseudoreplication
if (has_subject) {
  blk <- factor(md[[SUBJECT_COL]][match(lab, rownames(md))])
  perm <- how(nperm = 999, blocks = blk)
  ad <- adonis2(bc ~ grp, permutations = perm)
} else {
  ad <- adonis2(bc ~ grp, permutations = 999)
}
beta <- data.frame(distance="Bray-Curtis", R2=ad$R2[1], F=ad$F[1], p_value=ad$`Pr(>F)`[1],
                   test=ifelse(has_subject,"PERMANOVA (all samples)","PERMANOVA"))
fwrite(beta, file.path(OUTDIR, "beta_permanova.csv"))

## ================= 3. DIFFERENTIAL ABUNDANCE (3-method) =====================
psg <- tax_glom(ps, taxrank = AGG_RANK, NArm = FALSE)
# prevalence filter
keep <- rowSums(otu_table(psg) > 0) / nsamples(psg) >= PREVALENCE
psg <- prune_taxa(keep, psg)
mat <- as(otu_table(psg), "matrix"); if (!taxa_are_rows(psg)) mat <- t(mat)
gname <- make.unique(as.character(tax_table(psg)[, AGG_RANK]))
rownames(mat) <- gname
cat(sprintf("DA on %d %s-level features (prevalence >= %.0f%%)\n", nrow(mat), AGG_RANK, 100*PREVALENCE))

# ---- ANCOM-BC2 (PRIMARY): subject random effect + covariate adjustment ----
anc <- tryCatch({
  library(ANCOMBC)
  fix <- paste(c(GROUP_COL, COVARIATES), collapse = " + ")
  rand <- if (has_subject) sprintf("(1 | %s)", SUBJECT_COL) else NULL
  out <- ancombc2(data = psg, fix_formula = fix, rand_formula = rand,
                  group = GROUP_COL, p_adj_method = "BH", prv_cut = PREVALENCE)
  res <- out$res
  lfc_col <- grep(paste0("^lfc_", GROUP_COL), names(res), value = TRUE)[1]
  q_col   <- grep(paste0("^q_",   GROUP_COL), names(res), value = TRUE)[1]
  diff_col<- grep(paste0("^diff_",GROUP_COL), names(res), value = TRUE)[1]
  data.frame(name = res$taxon, anc_lfc = res[[lfc_col]], anc_q = res[[q_col]],
             anc_sig = res[[diff_col]])
}, error = function(e){ message("ANCOM-BC2 failed: ", e$message); NULL })

# ---- ALDEx2 (CLR; Welch + Wilcoxon) ----
ax <- tryCatch({
  library(ALDEx2)
  conds <- as.character(md[colnames(mat), GROUP_COL])
  x <- aldex(round(mat), conds, mc.samples = 128, test = "t", effect = TRUE, denom = "all")
  # diff.btw sign = group2 - group1 alphabetically; document direction downstream
  data.frame(name = rownames(x), ax_clrdiff = x$diff.btw, ax_effect = x$effect,
             ax_q = x$wi.eBH, ax_sig = x$wi.eBH < 0.05)
}, error = function(e){ message("ALDEx2 failed: ", e$message); NULL })

# ---- MaAsLin2 (TSS/log linear model) ----
maa <- tryCatch({
  library(Maaslin2)
  df_in <- as.data.frame(t(mat))
  fixed <- c(GROUP_COL, COVARIATES)
  rnd   <- if (has_subject) SUBJECT_COL else NULL
  fit <- Maaslin2(input_data = df_in, input_metadata = md,
                  output = file.path(OUTDIR, "maaslin2_out"),
                  fixed_effects = fixed, random_effects = rnd,
                  normalization = "TSS", transform = "LOG",
                  plot_heatmap = FALSE, plot_scatter = FALSE)
  r <- fit$results %>% filter(grepl(GROUP_COL, metadata))
  data.frame(name = r$feature, maa_coef = r$coef, maa_q = r$qval, maa_sig = r$qval < 0.05)
}, error = function(e){ message("MaAsLin2 failed: ", e$message); NULL })

## ---- consensus: >= 2 methods, concordant direction ----
cons <- Reduce(function(a,b) merge(a,b,by="name",all=TRUE),
               Filter(Negate(is.null), list(anc, ax, maa)))
sgn <- function(v) ifelse(is.na(v), NA, sign(v))
dir_mat <- cbind(sgn(cons$anc_lfc), sgn(cons$ax_effect), sgn(cons$maa_coef))
sig_mat <- cbind(cons$anc_sig, cons$ax_sig, cons$maa_sig)
cons$n_sig <- rowSums(sig_mat, na.rm = TRUE)
# concordant = all significant methods agree on sign
concord <- vapply(seq_len(nrow(cons)), function(i){
  sig_idx <- which(sig_mat[i, ] %in% TRUE)
  signs <- dir_mat[i, sig_idx]
  signs <- signs[!is.na(signs)]
  length(signs) >= 2 && length(unique(signs)) == 1
}, logical(1))
cons$consensus_hit <- (cons$n_sig >= 2) & concord
cons$direction <- ifelse(is.na(cons$anc_lfc), NA,
                         ifelse(cons$anc_lfc > 0, "Enriched (higher in non-ref)",
                                "Depleted (higher in ref)"))
fwrite(cons, file.path(OUTDIR, "da_consensus.csv"))
cat(sprintf("Consensus hits (>=2 methods, concordant): %d\n", sum(cons$consensus_hit, na.rm=TRUE)))
cat("Done. Tables in ", OUTDIR, "\n")
