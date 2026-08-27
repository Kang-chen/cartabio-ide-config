# Analysis conventions & scientific caveats

These are the definitions the skill uses. They are the versions the KRAS/TCGA/MSK-IMPACT
run converged on after fixing subtle bugs — follow them unless the user asks otherwise.

## What counts as an alteration

- **Mutation (sample-level, non-silent).** A sample is "mutated" if it has ≥1 mutation
  in the queried gene whose `mutationType` is NOT in the silent set:
  `{"Silent","Synonymous","3'UTR","5'UTR","3'Flank","5'Flank","Intron","IGR","RNA"}`.
  Multiple mutations in one sample count once (dedupe to sample).
- **Amplification.** GISTIC discrete `alteration == +2` (high-level amplification).
- **Deep deletion.** GISTIC discrete `alteration == -2` (deep/homozygous deletion).
- **Shallow calls (+1 / -1) are NOT counted** as events.
- **"Any alteration".** Mutation OR amplification, per sample, counted once.
  (For a canonical oncogene, deep deletion is rare and not part of "any"; if you study
  a tumor suppressor, consider making "any" = mutation OR deep deletion instead — state
  the choice in the report.)

## Denominator (THE correctness-critical decision)

**Default = COMMON denominator: samples profiled for BOTH mutation AND CNA.**
For each cancer type, let `both = sequenced_samples ∩ cna_samples`. Then:

```
mut_freq = |mutated ∩ both| / |both|
amp_freq = |amplified ∩ both| / |both|
any_freq = |(mutated ∩ both) ∪ (amplified ∩ both)| / |both|
```

This guarantees the invariant **`any% ≥ max(mut%, amp%)`** and makes mutation, CNA,
and "any" directly comparable within and across cohorts. `validate_common_invariant()`
in `analyze_alterations.py` checks it; the set of violating rows must be empty before
reporting.

**Why not per-assay?** cBioPortal's website reports mutation frequency over
mutation-profiled samples and CNA frequency over CNA-profiled samples separately. When
the two profiled sets differ in size, "any alteration" computed over their *union* can
come out **smaller** than the mutation-only frequency (e.g. KRAS PAAD showed mut 65.4%
but any 63.6%). That is mathematically correct but reads as a bug and misleads readers.
`compute_row_perassay()` is provided for parity/QC, but the common denominator is the
default for the report.

## Stability flag

Cancer types with common-denominator `N < 20` are flagged `stable = "no"`
(low-confidence). They are KEPT in the full tables but excluded from ranked figures and
de-emphasized in the narrative.

## Hotspot binning (conditional)

- Parse `proteinChange` (HGVSp_short) with regex `^([A-Za-z])(\d+)` → (refAA, codon).
- Bin by codon (e.g. KRAS: 12→G12, 13→G13, 61→Q61, 117→K117, 146→A146, else Other).
- Report both codon-bin composition and top specific alleles (e.g. G12D/G12V/G12C/G13D/Q61H).
- **Skip the hotspot figure when the gene is not hotspot-driven.** Use
  `has_recurrent_hotspots()` (True if the top codon holds ≥20% of non-silent mutations).
  Oncogenes (KRAS, BRAF, EGFR, PIK3CA) pass; tumor suppressors (TP53, PTEN, RB1, APC)
  usually fail — for those, report top alleles/domains and note the dispersed
  loss-of-function pattern instead of forcing a hotspot chart.

## Cross-cohort cancer-type harmonization

TCGA study codes are one-per-type; MSK uses `CANCER_TYPE` strings. Matching is
best-effort by oncotree-like label. Multiple TCGA types may map to one MSK label, e.g.:

- Lung Adenocarcinoma + Lung Squamous Cell Carcinoma → Non-Small Cell Lung Cancer
- Colorectal Adenocarcinoma → Colorectal Cancer
- Pancreatic Adenocarcinoma → Pancreatic Cancer
- Uterine Corpus Endometrial Carcinoma + Uterine Carcinosarcoma → Endometrial Cancer
- Stomach + Esophageal Adenocarcinoma → Esophagogastric Cancer
- Glioblastoma + Brain Lower Grade Glioma → Glioma
- Cholangiocarcinoma + Liver Hepatocellular Carcinoma → Hepatobiliary Cancer

Unmatched types are still reported within their own cohort; the scatter uses only
matched pairs.

## Scientific caveats to state in every report

1. **Tumor purity / stromal dilution.** Bulk and targeted-panel sequencing of low-purity
   tumors underestimate mutation frequency. Example: KRAS in pancreatic ductal
   adenocarcinoma is canonically ~90% but shows ~65–75% in these cohorts. Verify large
   deviations are real (cross-cohort agreement) rather than a query bug.
2. **Pan-cohort rate ≠ biology.** A cohort's overall gene alteration rate reflects its
   cancer-type case-mix (e.g. MSK enrichment for GI/lung raises pooled KRAS rates), not a
   true difference in per-type biology. Always compare per cancer type.
3. **Curated calls are trusted.** Frequencies use cBioPortal's curated somatic
   mutation/CNA calls; no re-calling from raw data.
4. **Label harmonization is approximate.** Cross-cohort matching is by name; borderline
   histologies may not align perfectly.
5. **GISTIC thresholding.** Only high-level (+2) amplification and deep (−2) deletion are
   events; shallow copy-number changes are excluded, which is conservative.

## Sanity-check ballparks (KRAS example)

Use these to catch query bugs for KRAS. For other genes, establish expected ballparks
from `LiteratureSearch` before trusting the numbers.

| Cancer type | Expected KRAS mutation % |
|---|---|
| Pancreatic | ~60–95 (canonical ~90; lower in bulk/panel) |
| Colorectal | ~30–50 |
| Lung adenocarcinoma | ~20–40 |
| Endometrial | ~10–30 |
