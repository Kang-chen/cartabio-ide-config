# Ranking and robustness

Raw connectivity output is dominated by one-off, non-reproducible signatures and by pan-assay-active
cytotoxins. This reference defines the tiered, reproducibility-weighted ranking and the robustness
suite that make the output trustworthy.

## Aggregate signatures → compounds
Each L1000 signature is one (compound × cell line × dose × time). Aggregate the reverser rows to
per-compound statistics:
- `n_reversing_sigs` — # independent reversing signatures for the compound.
- `n_cell_lines` — # distinct cell lines in which it reverses.
- `median_z_sum`, `best_z_sum` — reversal strength (more negative = stronger).
- `best_fdr_down` — best significance.
- `n_mimicking_sigs` — # signatures where the same compound *mimics* the query (promiscuity signal).

## Tiering
- **Tier-1 (primary):** compounds with **≥ 2 independent reversing signatures** (reproducible).
- **Tier-2:** single-signature compounds (reported separately; do not rank against Tier-1).

## Composite score (Tier-1 only)
```python
import numpy as np
def zscore(s):
    s = np.asarray(s, float)
    return (s - np.nanmean(s)) / (np.nanstd(s) + 1e-9)

strength = -df["median_z_sum"]                                   # more positive = stronger reversal
repro    = np.log1p(df["n_reversing_sigs"]) + np.log1p(df["n_cell_lines"])
signif   = -np.log10(df["best_fdr_down"].clip(lower=1e-320))
# reverser specificity penalizes compounds that also strongly mimic the query
df["reverser_specificity"] = df["n_reversing_sigs"] / (df["n_reversing_sigs"] + df["n_mimicking_sigs"])

df["reverser_score"] = (0.45*zscore(repro) + 0.30*zscore(strength) + 0.25*zscore(signif)) \
                        * df["reverser_specificity"]
df = df.sort_values("reverser_score", ascending=False).reset_index(drop=True)
df["rank"] = df.index + 1
```
Weights (0.45 reproducibility / 0.30 strength / 0.25 significance) prioritize reproducible hits;
tune only with justification. `reverser_specificity` ∈ (0,1]: 1.0 = pure reverser; lower = also a
mimicker (e.g. broadly cytotoxic compounds). This is what keeps promiscuous agents from topping the
list even when their raw z-sum is extreme.

## MANDATORY robustness checks
### 1. Reproducibility tiering
Already applied above — Tier-1 requires ≥2 signatures.

### 2. Positive-control recovery (the key validation)
Do drugs already used for this disease appear as reversers **without being provided as input**? This
is the single strongest evidence the map is meaningful.

**CRITICAL — classify by ACTUAL table membership, never by inference.** A drug that is not in Tier-1
is NOT automatically "Tier-2"; check whether it is present in the Tier-2 table too. A drug absent
from *both* tiers is genuinely "not recovered". Conflating "not in Tier-1" with "Tier-2 reverser" is
a real bug that produces false claims (e.g. wrongly stating a drug was recovered).
```python
tier1_lookup = {r["compound"].lower(): r for r in tier1.to_dict("records")}
tier2_lookup = {r["compound"].lower(): r for r in tier2.to_dict("records")}
def classify(drug):
    d = drug.lower()
    if d in tier1_lookup: return ("Tier-1", tier1_lookup[d])
    if d in tier2_lookup: return ("Tier-2", tier2_lookup[d])
    return ("Absent", None)                     # genuinely not found as a reverser
```
Report each known drug as Tier-1 (with rank) / Tier-2 (single-signature) / Absent. Interpret
"Absent" honestly: drugs acting via gut-luminal, microbiome-, or host-metabolism-dependent routes
(topical agents, prodrugs) are expected L1000 false negatives, not evidence the method failed.

### 3. Promiscuity / specificity control
Flag the compounds that are strong reversers AND strong mimickers (low `reverser_specificity`). These
are usually broadly bioactive/cytotoxic; keep them but annotate the caveat.

## DEFAULT-ON checks (document; allow disabling)
### Sensitivity to signature choice
Re-run the query with an alternative signature (e.g. pre-housekeeping-filter, or a different size)
and correlate the two rankings (Spearman). Report whether the top validated hits are stable. Report
the correlation qualitatively if the exact recompute is not perfectly reproducible; do not over-state
a precise p-value you cannot regenerate.

**Acceptable alternative when re-query is not feasible:** report cell-context stability — how many
Tier-1 compounds reverse in disease-relevant cell lines (see the cell-line-context check below) — as
a proxy for signature-construction sensitivity. State explicitly which alternative was used so the
reader can judge the robustness evidence.

### Cell-line-context (tissue-relevant) view
Restrict to cell lines relevant to the disease tissue (e.g. colorectal lines for gut disease:
`HT29, HCT116, SW480, SW620, LOVO, CACO2, HCT15, COLO205, LS180, DLD1, NCIH508, T84, RKO, GP2D`) and
report how many Tier-1 compounds still reverse there. Adds tissue relevance beyond the cancer-cell
average.

## OPTIONAL checks
- **Consensus-library corroboration:** repeat the query with `l1000_mean_cp`; report how many Tier-1
  compounds are re-identified. Expect lower overlap due to annotation-coverage loss, not signal loss.
- **Pathway/target enrichment:** MSigDB-Hallmark and Reactome on the signature and on hit targets
  (**KEGG is excluded — non-commercial license; use the MSigDB Hallmark collection only**).
- **ADMET:** `predict_admet_properties` on top hits' SMILES (from the Broad hub) for early
  developability flags.

## Save everything
Write a `robustness_summary.json` with: signature sizes, coverage %, DB signature counts, #unique
reversers, Tier-1/Tier-2 counts, strongest z-sum, positive-control classification, gut-supported
count, and any sensitivity/corroboration numbers. The report reads from this file — no hardcoded
numbers.
