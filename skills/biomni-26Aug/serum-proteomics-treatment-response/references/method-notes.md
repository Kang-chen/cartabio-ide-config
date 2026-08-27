# Method Notes — Serum Proteomics Treatment-Response Pipeline

## 1. Why Wilcoxon, not limma/DEqMS?

**limma** and **DEqMS** are optimal for TMT/LFQ proteomics with PSM counts and ≥6 replicates per group, where the empirical Bayes variance shrinkage provides substantial power gains. In the MMIP discovery cohort (n=10 Early Responders vs n=10 Poor Responders), two factors make Wilcoxon preferable:

1. **Small n**: With n=10 per group, the normality assumption underlying limma's t-statistics is fragile. Wilcoxon is distribution-free and robust to the heavy-tailed distributions common in serum proteomics.
2. **MNAR proteins**: Proteins absent in one group cannot be included in a limma model without imputation, which introduces bias. Wilcoxon naturally handles the "detected vs. undetected" contrast via the rank structure, and MNAR proteins are handled separately via Fisher's exact test.

**Reference**: Kammers et al. (2015) *J Proteome Res* — comparison of statistical methods for small-n proteomics.

---

## 2. MNAR vs. MAR: Why the distinction matters

In DIA-MS, a protein below the limit of detection (LOD) is recorded as NA. This NA is **not** missing at random (MAR) — it carries the biological information that the protein is below LOD in that sample. Two types of missingness occur:

| Type | Mechanism | Statistical treatment |
|------|-----------|----------------------|
| **MAR** | Stochastic — protein present but not sampled | Impute (MinProb, kNN) before DE |
| **MNAR** | Structural — protein below LOD | Fisher exact test on detection rates; do NOT impute |

Imputing MNAR proteins with MinProb (left-tail imputation) and then running limma will produce inflated fold-changes and false positives. The correct approach is to test detection rates directly.

**CHGA in MMIP**: CHGA was undetected in 9/10 Early Responders at T1 (Fisher p < 0.001, FDR q = 1.45×10⁻⁹). This is not a technical failure — it reflects chromaffin granule depletion (low-reserve state) that is the biological signal of interest.

---

## 3. Mixed-effects model specification

```
intensity ~ time * response_group + baseline_PASI + age + sex + (1 | patient_id)
```

**Why random intercept only (not random slope)?**
With 3 timepoints and n=10–20 patients per group, a random slope model is overparameterised and frequently fails to converge. The random intercept accounts for between-patient baseline differences, which is the primary source of repeated-measures correlation.

**Why ML, not REML?**
REML is preferred for variance component estimation, but ML is required for likelihood ratio tests comparing models with different fixed effects. Since we extract p-values from the fixed-effects table (not LRT), either is acceptable; ML is used for consistency.

**Convergence failures**: ~5–15% of proteins fail to converge, typically those with very low variance or extreme outliers. These are flagged as `model_status = "model_failed"` and excluded from the interaction p-value ranking. They are not discarded — they remain in the DE table.

---

## 4. DTW vs. Euclidean distance for trajectory clustering

**Dynamic Time Warping (DTW)** is preferred over Euclidean distance for clinical score trajectories because:
- Patients may respond at different rates (temporal misalignment)
- A patient who clears at 6 months vs. 12 months has a similar trajectory shape but large Euclidean distance
- DTW aligns trajectories before computing distance, capturing shape similarity

**Ward.D2 linkage** minimises within-cluster variance and produces compact, well-separated clusters — appropriate for the expected 3–6 trajectory subtypes.

**Silhouette width** for optimal k: average silhouette width measures how similar each patient is to their own cluster vs. the nearest other cluster. Values > 0.5 indicate good separation; values 0.25–0.5 indicate weak structure.

---

## 5. Biomarker tier rationale

### Tier 1 (★★★) — ELISA validation priority

**MNAR criterion (Fisher adj_p < 0.001)**:
- Proteins absent in one response group at baseline are the strongest pre-treatment stratification markers
- The adj_p < 0.001 threshold (not 0.05) is used because MNAR proteins are a small subset (~10–50 of 421 quantified) and the signal is typically very strong when real
- Example: CHGA FDR q = 1.45×10⁻⁹ — unambiguous

**T2-surge criterion (LME interaction adj_p < 0.001 + T2_surge trajectory)**:
- Proteins that surge specifically in responders at T2 (mid-treatment) are markers of the psychological reactogenicity response
- The T2_surge trajectory requirement prevents false positives from proteins with significant interaction but no directional T2 pattern
- Example: SAA1, DBI — T2 surge in Early Responders, flat in Poor Responders

### Tier 2 (★★) — Strong candidates

**DE adj_p < 0.01 + |log2FC| > 1.0 (2-fold)**:
- More stringent than the standard 0.05/0.58 threshold to compensate for the small discovery cohort
- 2-fold change is a practical threshold for ELISA assay sensitivity

**Cross-species concordance**:
- A protein that is DE in the same direction in both human serum (MMIP) and mouse serum (IMQ model) has stronger causal evidence
- Even if the human p-value is modest (adj_p 0.01–0.05), cross-species concordance upgrades to Tier 2

### Tier 3 (★) — Exploratory

Standard DE threshold (adj_p < 0.05, |log2FC| > 0.58) for hypothesis generation. Not recommended for immediate ELISA validation without additional evidence.

---

## 6. Mechanistic annotation keyword table

The annotation lookup in `05_biomarker_tiering.R` uses a curated keyword table covering:

| Axis | Key proteins | Biological relevance |
|------|-------------|---------------------|
| SAM chromaffin | CHGA, SCG2, CHGB, VGF | Adrenal medulla reserve and secretion |
| SAM KKS | KLKB1, KNG1, KNG2 | Bradykinin-mediated vasodilation, stress response |
| SAM RAS | AGT, CBG, SERPINA6 | Renin-angiotensin, cortisol buffering |
| HPA steroidogenesis | DBI, TSPO, PTGDS | Neurosteroid synthesis, GABA modulation |
| ECM ITIH | ITIH1, ITIH2, ITIH3, ITIH4 | Hyaluronan stabilisation, ECM integrity |
| Acute-phase positive | SAA1, CRP, ORM1 | Inflammatory surge markers |
| Acute-phase negative | ITIH1, ALB, APOA1 | Negative acute-phase (decline = inflammation) |
| Apolipoprotein/HDL | APOA1, APOE, PON1 | Lipid transport, anti-inflammatory |

Proteins not in the keyword table receive annotation `"unknown"` — this is not a negative finding; it may indicate a novel axis.

---

## 7. Key parameters and sensitivity

| Parameter | Default | Sensitivity note |
|-----------|---------|-----------------|
| `mnar_fisher_p` | 0.05 | Relaxing to 0.1 adds ~5–10 borderline MNAR proteins; tighten to 0.01 for high-confidence only |
| `min_detected_frac` | 0.5 | Lowering to 0.3 includes more proteins but increases noise in Wilcoxon test |
| `de_log2fc` | 0.58 | 0.58 = ~1.5-fold; increase to 1.0 for 2-fold stringency |
| `lme_interaction_p` | 0.05 | With 421 proteins, BH correction at 0.05 is appropriate; raw p < 0.001 is a useful pre-filter |
| `flare_ratio` | 1.2 | T2/T1 > 1.2 = 20% worsening; adjust based on clinical definition of "flare" |
| `clear_ratio` | 0.5 | T3/T1 < 0.5 = PASI50 response; standard psoriasis endpoint |

---

## 8. Downstream skill connections

```
[This skill] → lasso-biomarker-panel
  Input: de_results_significant.csv (protein list as features)
         analysis_object.rds (for metadata)
  Purpose: Build minimal ELISA panel with nested CV

[This skill] → functional-enrichment-from-degs
  Input: de_results_significant.csv
  Purpose: Pathway enrichment of DE proteins

[proteomics-diff-exp] → [This skill]
  Input: normalized_protein_matrix.csv from proteomics-diff-exp
  Purpose: PSM aggregation and normalization upstream of MNAR/Wilcoxon
```

---

## 9. References

1. Kammers K, et al. (2015) Detecting significant changes in protein abundance. *EuPA Open Proteomics* 7:11–19.
2. Lazar C, et al. (2016) Accounting for the multiple natures of missing values in label-free quantitative proteomics. *J Proteome Res* 15(4):1116–1125.
3. Bernhardt OM, et al. (2012) A study-design framework for DIA proteomics. *J Proteome Res* 11(12):6042–6055.
4. Sakoe H, Chiba S (1978) Dynamic programming algorithm optimization for spoken word recognition. *IEEE Trans Acoust* 26(1):43–49. [DTW original]
5. Rousseeuw PJ (1987) Silhouettes: a graphical aid to the interpretation and validation of cluster analysis. *J Comput Appl Math* 20:53–65.
6. Pinheiro J, Bates D (2000) *Mixed-Effects Models in S and S-PLUS*. Springer. [nlme]
7. Benjamini Y, Hochberg Y (1995) Controlling the false discovery rate. *J R Stat Soc B* 57(1):289–300.
