# =============================================================================
# 05_biomarker_tiering.R
# Biomarker Tier 1 / 2 / 3 ranking for serum proteomics treatment-response
#
# Purpose:
#   Integrate MNAR detection, Wilcoxon DE, and longitudinal LME results into
#   a ranked biomarker tier table. Tier assignment reflects both statistical
#   strength and biological specificity for the SAM/HPA/ECM axes relevant to
#   psoriasis treatment response. An optional cross-species concordance flag
#   (from mouse model DE) upgrades candidates to Tier 2.
#
# Tier logic:
#   Tier 1 (★★★) — Highest priority for ELISA validation:
#     • MNAR pattern: Fisher adj_p < 0.001  [pre-treatment reserve marker]
#     • OR LME interaction adj_p < 0.001 + T2_surge trajectory  [reactogenicity]
#
#   Tier 2 (★★) — Strong candidates:
#     • DE adj_p < 0.01 AND |log2FC| > 1.0
#     • OR cross-species concordance flag (same direction in mouse model)
#     • OR LME interaction adj_p < 0.05 (without T2 surge)
#
#   Tier 3 (★) — Exploratory:
#     • DE adj_p < 0.05 AND |log2FC| > 0.58
#     • (everything significant that doesn't meet Tier 1/2)
#
# Inputs:
#   de_result   - output from run_wilcoxon_de()
#   lme_result  - output from run_longitudinal_lme() [optional]
#   xspecies_df - data.frame: protein, xspecies_log2FC, xspecies_p,
#                 xspecies_direction [optional]
#   params      - list of thresholds
#
# Outputs:
#   biomarker_tiers.csv  - ranked tier table with mechanistic annotation
#   Returns:             - list with $tier_table
# =============================================================================

suppressPackageStartupMessages({
  library(dplyr)
})

# -----------------------------------------------------------------------------
# Curated mechanistic annotation lookup
# Covers SAM axis, HPA axis, ECM/ITIH, APO/HDL, acute-phase, neuroendocrine
# -----------------------------------------------------------------------------

MECHANISTIC_KEYWORDS <- list(
  # Sympatho-adrenal medullary (SAM) axis
  SAM_chromaffin    = c("CHGA","SCG2","CHGB","SCG3","SCG5","VGF","PCSK1N","STXBP5"),
  SAM_catecholamine = c("DBH","PNMT","TH","COMT","MAOA","MAOB"),
  SAM_adrenergic    = c("ADRB1","ADRB2","ADRA1A","ADRA2A","ADRA2B"),
  SAM_KKS           = c("KLKB1","KNG1","KNG2","BDKRB1","BDKRB2"),
  SAM_RAS           = c("AGT","ACE","ACE2","AGTR1","AGTR2","REN","CBG","SERPINA6"),

  # HPA axis
  HPA_glucocorticoid  = c("SERPINA6","CBG","FKBP5","NR3C1","CRH","ACTH","POMC"),
  HPA_steroidogenesis = c("DBI","ACBP","TSPO","CYP11A1","CYP11B1","CYP11B2",
                           "STAR","HSD3B1","HSD3B2","PTGDS"),

  # ECM / tissue integrity
  ECM_ITIH   = c("ITIH1","ITIH2","ITIH3","ITIH4","AMBP","BIKUNIN"),
  ECM_matrix = c("FN1","VTN","CLU","GSN","HSPG2","LAMA1"),

  # Apolipoprotein / HDL
  APO_HDL = c("APOA1","APOA2","APOA4","APOB","APOC1","APOC2","APOC3",
               "APOD","APOE","APOM","PON1","PON2","PON3","CLU","LCAT"),

  # Acute-phase / inflammation
  ACUTE_PHASE_pos = c("SAA1","SAA2","SAA4","CRP","ORM1","ORM2","HP","HPR",
                       "FGA","FGB","FGG","SERPINA1","SERPINA3","C3","C4A","C4B"),
  ACUTE_PHASE_neg = c("ITIH1","ITIH2","ITIH3","ITIH4","ALB","TTR","APOA1",
                       "APOA2","FETUB","AHSG"),

  # Neuroimmune / GABA
  NEUROIMMUNE = c("DBI","ACBP","TSPO","GABRA1","GABRB2","GABRG2"),

  # Skin / psoriasis-relevant
  SKIN_PSORIASIS = c("S100A8","S100A9","S100A7","ELANE","MMP9","TIMP1",
                      "IL17A","IL23A","TNF","CXCL1","CXCL8","CCL20"),

  # Gut-brain axis
  GUT_BRAIN = c("CLDN3","OCLN","TJP1","FABP2","LBP","CD14","IFABP")
)

# Human-readable axis labels
AXIS_LABELS <- c(
  SAM_chromaffin      = "SAM axis — chromaffin granule protein",
  SAM_catecholamine   = "SAM axis — catecholamine synthesis",
  SAM_adrenergic      = "SAM axis — adrenergic receptor",
  SAM_KKS             = "SAM axis — kallikrein-kinin system",
  SAM_RAS             = "SAM/RAS axis — renin-angiotensin",
  HPA_glucocorticoid  = "HPA axis — glucocorticoid binding/signalling",
  HPA_steroidogenesis = "HPA axis — steroidogenesis (TSPO/DBI)",
  ECM_ITIH            = "ECM stability — ITIH/bikunin family",
  ECM_matrix          = "ECM stability — matrix proteins",
  APO_HDL             = "Apolipoprotein / HDL particle",
  ACUTE_PHASE_pos     = "Acute-phase protein (positive)",
  ACUTE_PHASE_neg     = "Acute-phase protein (negative)",
  NEUROIMMUNE         = "Neuroimmune — GABA/TSPO/DBI",
  SKIN_PSORIASIS      = "Skin/psoriasis — cytokine/protease",
  GUT_BRAIN           = "Gut-brain axis — barrier/LPS"
)

annotate_mechanism <- function(proteins) {
  sapply(proteins, function(prot) {
    hits <- names(Filter(function(grp) prot %in% grp, MECHANISTIC_KEYWORDS))
    if (length(hits) == 0) return("unknown")
    paste(AXIS_LABELS[hits], collapse = "; ")
  }, USE.NAMES = FALSE)
}


# -----------------------------------------------------------------------------
# Main function
# -----------------------------------------------------------------------------

run_biomarker_tiering <- function(
    de_result,
    lme_result   = NULL,
    xspecies_df  = NULL,
    params       = list()
) {
  p <- modifyList(
    list(
      # Tier 1 thresholds
      tier1_mnar_adj_p      = 0.001,
      tier1_lme_adj_p       = 0.001,
      tier1_require_t2surge = TRUE,

      # Tier 2 thresholds
      tier2_de_adj_p        = 0.01,
      tier2_de_log2fc       = 1.0,
      tier2_lme_adj_p       = 0.05,
      tier2_xspecies_p      = 0.05,

      # Tier 3 thresholds
      tier3_de_adj_p        = 0.05,
      tier3_de_log2fc       = 0.58,

      # Output
      include_unannotated   = TRUE   # include proteins with no mechanism hit
    ),
    params
  )

  cat("=== Biomarker Tiering ===\n")

  # --- Extract DE table ---
  de_table <- de_result$de_table

  # --- Merge LME results ---
  if (!is.null(lme_result)) {
    lme_cols <- lme_result$lme_table %>%
      select(protein, interaction_p, interaction_adj_p,
             sig_interaction, trajectory_type)
    de_table <- left_join(de_table, lme_cols, by = "protein")
  } else {
    de_table$interaction_p     <- NA_real_
    de_table$interaction_adj_p <- NA_real_
    de_table$sig_interaction   <- FALSE
    de_table$trajectory_type   <- NA_character_
  }

  # --- Merge cross-species results ---
  # FIX: dplyr select() does not support `newname = any_of(candidates)` syntax.
  # Instead: rename the column to a canonical name before joining.
  if (!is.null(xspecies_df)) {
    xs <- xspecies_df

    # Standardise column names to canonical names
    xs <- .rename_if_present(xs, c("log2FC","mouse_log2FC"),    "xspecies_log2FC")
    xs <- .rename_if_present(xs, c("p_value","mouse_p","pval"), "xspecies_p")
    xs <- .rename_if_present(xs, c("direction","mouse_direction"), "xspecies_direction")

    # Keep only needed columns
    xs_keep <- intersect(
      c("protein","xspecies_log2FC","xspecies_p","xspecies_direction"),
      colnames(xs)
    )
    xs <- xs[, xs_keep, drop = FALSE]

    de_table <- left_join(de_table, xs, by = "protein")

    # Concordance: same direction in human and mouse, xspecies p significant
    de_table$xspecies_concordant <- FALSE
    if ("xspecies_p" %in% colnames(de_table) &&
        "xspecies_direction" %in% colnames(de_table)) {
      de_table$xspecies_concordant <-
        !is.na(de_table$xspecies_p) &
        de_table$xspecies_p < p$tier2_xspecies_p &
        !is.na(de_table$direction) &
        !is.na(de_table$xspecies_direction) &
        (
          (grepl("UP",   de_table$direction) & grepl("UP",   de_table$xspecies_direction)) |
          (grepl("DOWN", de_table$direction) & grepl("DOWN", de_table$xspecies_direction))
        )
    }

    # Ensure columns exist even if not in xs
    if (!"xspecies_log2FC"    %in% colnames(de_table)) de_table$xspecies_log2FC    <- NA_real_
    if (!"xspecies_p"         %in% colnames(de_table)) de_table$xspecies_p         <- NA_real_
    if (!"xspecies_direction" %in% colnames(de_table)) de_table$xspecies_direction <- NA_character_

  } else {
    de_table$xspecies_log2FC     <- NA_real_
    de_table$xspecies_p          <- NA_real_
    de_table$xspecies_direction  <- NA_character_
    de_table$xspecies_concordant <- FALSE
  }

  # --- Assign tiers ---
  de_table$tier <- assign_tiers(de_table, p)

  # --- Mechanistic annotation ---
  de_table$mechanism <- annotate_mechanism(de_table$protein)

  # --- Build final tier table ---
  tier_cols <- c(
    "protein", "tier",
    "log2FC", "adj_p", "direction", "is_mnar", "mnar_direction", "fisher_adj_p",
    "interaction_adj_p", "trajectory_type",
    "xspecies_concordant", "xspecies_log2FC",
    "mechanism"
  )
  tier_cols <- intersect(tier_cols, colnames(de_table))
  tier_table <- de_table %>%
    filter(!is.na(tier)) %>%
    select(all_of(tier_cols))

  # FIX: Sort correctly for MNAR Tier 1 proteins (adj_p = NA, ranked by fisher_adj_p).
  # Use a composite sort key: within each tier, MNAR proteins (adj_p=NA) sort by
  # fisher_adj_p; quantified proteins sort by adj_p; then by |log2FC| descending.
  tier_table <- tier_table %>%
    mutate(
      .sort_p = dplyr::coalesce(adj_p, fisher_adj_p, interaction_adj_p, 1.0),
      .sort_fc = ifelse(is.na(log2FC), 0, abs(log2FC))
    ) %>%
    arrange(tier, .sort_p, desc(.sort_fc)) %>%
    select(-.sort_p, -.sort_fc)

  # Add tier stars
  tier_table$tier_stars <- c("1" = "★★★", "2" = "★★", "3" = "★")[
    as.character(tier_table$tier)
  ]

  # --- Summary ---
  tier_counts <- table(tier_table$tier)
  cat(sprintf("\nTier 1 (★★★): %d proteins\n", tier_counts["1"] %||% 0))
  cat(sprintf("Tier 2 (★★):  %d proteins\n", tier_counts["2"] %||% 0))
  cat(sprintf("Tier 3 (★):   %d proteins\n", tier_counts["3"] %||% 0))

  cat("\nTop Tier 1 candidates:\n")
  t1 <- tier_table %>% filter(tier == 1) %>% head(10)
  if (nrow(t1) > 0) {
    print(t1[, intersect(c("protein","tier_stars","log2FC","adj_p",
                            "fisher_adj_p","is_mnar","trajectory_type","mechanism"),
                          colnames(t1))],
          row.names = FALSE)
  } else {
    cat("  (none at current thresholds — consider relaxing tier1_mnar_adj_p)\n")
  }

  cat("\n✓ Biomarker tiering complete.\n")
  list(tier_table = tier_table)
}


# -----------------------------------------------------------------------------
# Helper: rename a column to canonical_name if any of candidates exist
# (only renames if canonical_name is not already present)
# -----------------------------------------------------------------------------

.rename_if_present <- function(df, candidates, canonical_name) {
  if (canonical_name %in% colnames(df)) return(df)  # already correct
  for (cand in candidates) {
    if (cand %in% colnames(df)) {
      colnames(df)[colnames(df) == cand] <- canonical_name
      return(df)
    }
  }
  df  # none found — return unchanged
}


# -----------------------------------------------------------------------------
# Helper: assign tier per protein
# -----------------------------------------------------------------------------

assign_tiers <- function(de_table, p) {
  sapply(seq_len(nrow(de_table)), function(i) {
    row <- de_table[i, ]

    # --- Tier 1 ---
    # MNAR: Fisher adj_p < tier1_mnar_adj_p
    mnar_t1 <- isTRUE(row$is_mnar) &&
      !is.na(row$fisher_adj_p) &&
      row$fisher_adj_p < p$tier1_mnar_adj_p

    # LME T2 surge: interaction adj_p < tier1_lme_adj_p + T2_surge trajectory
    lme_t1 <- !is.na(row$interaction_adj_p) &&
      row$interaction_adj_p < p$tier1_lme_adj_p &&
      (!p$tier1_require_t2surge ||
         (!is.na(row$trajectory_type) && row$trajectory_type == "T2_surge"))

    if (mnar_t1 || lme_t1) return(1L)

    # --- Tier 2 ---
    # Strong DE: adj_p < 0.01 AND |log2FC| >= 1.0
    de_t2 <- !is.na(row$adj_p) &&
      row$adj_p < p$tier2_de_adj_p &&
      !is.na(row$log2FC) &&
      abs(row$log2FC) >= p$tier2_de_log2fc

    # Cross-species concordance (same direction, xspecies p significant)
    xs_t2 <- isTRUE(row$xspecies_concordant)

    # LME significant interaction (without T2 surge requirement)
    lme_t2 <- !is.na(row$interaction_adj_p) &&
      row$interaction_adj_p < p$tier2_lme_adj_p

    if (de_t2 || xs_t2 || lme_t2) return(2L)

    # --- Tier 3 ---
    # Standard DE threshold
    de_t3 <- !is.na(row$adj_p) &&
      row$adj_p < p$tier3_de_adj_p &&
      !is.na(row$log2FC) &&
      abs(row$log2FC) >= p$tier3_de_log2fc

    # Any MNAR protein not already Tier 1 (relaxed Fisher threshold)
    mnar_t3 <- isTRUE(row$is_mnar)

    if (de_t3 || mnar_t3) return(3L)

    return(NA_integer_)
  })
}


# -----------------------------------------------------------------------------
# Null-coalescing operator
# -----------------------------------------------------------------------------

`%||%` <- function(a, b) if (!is.null(a) && length(a) > 0 && !is.na(a[1])) a else b
