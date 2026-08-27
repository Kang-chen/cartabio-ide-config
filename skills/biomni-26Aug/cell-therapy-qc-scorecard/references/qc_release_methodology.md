# QC Release Methodology — the five modules

This document explains *why* each module exists and *exactly how* it is scored.
Read it before editing thresholds or logic in `scripts/score_modules.py`. The
logic here was hardened on a real iPSC-derived NK-cell release dataset; the
"caveats" are corrections to plausible-but-wrong first attempts.

## The core question

Generic scRNA-seq QC answers "which cells are low quality?" A cell-therapy
**release** QC answers a different question: **is this product what it claims to
be, is it pure, is it safe, and is it mature enough to release?** Every module
below is framed as a GREEN / AMBER / RED release call, and a unit's **overall
call is the worst active module** — a single RED fails the lot.

Modules A, C, E always run. Module B runs only for iPSC/ESC-derived products.
Module D runs only when a maturity axis is defined for the target cell type.

---

## Module A — Target-cell identity & purity  (always)

**What it measures:** the fraction of cells that express the target-cell
identity/effector program.

**How it is scored (expression-anchored, NO signature gate):**
1. Define a small `identity_anchor` panel of canonical target markers (e.g. for
   NK: GNLY, NKG7, KLRD1, NCR1, KLRF1, PRF1, GZMB).
2. A cell is **target-anchor-positive** if it detects ≥1 anchor gene (raw
   log-normalized expression > 0).
3. Among anchor-positive cells, count how many *aberrant* off-lineage programs
   the cell also expresses (≥2 markers of a non-target lineage = one aberrant
   program). This grades **fidelity**:
   - **clean target** = anchor-positive, 0 aberrant programs
   - **aberrant target** = anchor-positive, ≥1 aberrant program
4. **Purity = % anchor-positive cells.** A **true contaminant** is
   anchor-*negative* AND expresses a non-target lineage.

**Caveat #1 — do NOT gate identity on `sc.tl.score_genes`.** In a product where
the target program dominates the transcriptome (NK effectors are a large fraction
of all reads), `score_genes`' background correction makes the corrected identity
score **negative even in obvious target cells**, so a "score > 0" gate throws
away real target cells. On the reference dataset, GNLY|NKG7|KLRD1 detection alone
was ~99% of cells, while a score-gated call collapsed purity spuriously. Anchor on
raw detection; use the signature score only for soft ordering, never as the gate.

**Why the fidelity split matters:** iPSC-derived products commonly show broad,
low-level lineage "leakiness" (e.g. sporadic CD3/TRAC in NK cells) without being
real contaminants. Reporting aberrant-target separately from true-contaminant
keeps the purity number honest and surfaces a genuine manufacturing signal
(lineage promiscuity) instead of inflating a contamination number.

Default thresholds: GREEN ≥90% · AMBER 75–90% · RED <75% purity.

---

## Module B — Residual pluripotency  (iPSC/ESC only)

**What it measures:** the fraction of cells that are residual undifferentiated
iPSC/ESC — the key **tumorigenicity safety** readout for stem-cell-derived
products.

**How it is scored (co-expression specificity + per-unit null):**
1. Panels: a broad `core` set and a `specific` set of pluripotency TFs, plus the
   canonical `triad` POU5F1 + NANOG + LIN28A.
2. A cell is called residual-pluripotent only if ALL hold:
   - co-expresses ≥2 `core` TFs, AND
   - expresses ≥1 `specific` TF (NANOG/LIN28A/TDGF1/PRDM14/UTF1/ZSCAN10/SALL4), AND
   - its pluripotency signature score exceeds a **per-unit shuffled-null
     threshold** (shuffle gene labels, recompute the score n_perm times, take the
     99.9th percentile as the noise floor), AND
   - it is NOT target-identity-positive.
3. Also report the count of cells co-expressing the full triad (strongest
   evidence of a true residual iPSC).

**Caveat #2 — single markers are non-specific.** POU5F1 (OCT4) and DNMT3B appear
sporadically in *proliferating* non-pluripotent cells. A naïve "≥2 pluripotency
markers" rule on the reference data flagged ~0.2–0.4% of cells — enough to trip
RED — but 60–80% of those were *also* strongly target-positive (proliferating NK
cells with sporadic OCT4/DNMT3B), and **zero** cells co-expressed the POU5F1+
NANOG+LIN28A triad. Requiring specific-TF co-expression, excluding target-positive
cells, and validating against the null removes this false positive.

**Caveat #4 — this is a limit-of-detection statement.** When no cell passes the
specific, validated call, report **"below detection at this depth/cell number,"**
not "0% / absent." scRNA-seq LOD for rare residual iPSCs is far coarser than
ddPCR/qPCR release assays (which reach ~0.001–0.01%). Always recommend an
orthogonal assay for true release certification.

Default thresholds: GREEN <0.01% · AMBER 0.01–0.1% · RED >0.1%. (Frame all three
against the LOD caveat.)

---

## Module C — Off-target lineage  (always)

**What it measures:** the fraction of cells committed to a **non-target lineage**
— unwanted differentiation products.

**How it is scored (restricted to target-negative cells):**
1. For each non-target lineage panel (fibroblast, epithelial, endothelial,
   hepatic, neural, cardiac, myeloid, erythroid, T, B — minus the target's own
   lineage), a cell is off-target if it co-expresses ≥2 markers AND is
   **target-anchor-negative** AND has a positive lineage signature score.
2. Off-target % = % of cells positive for any non-target lineage.

**Caveat #3 — restrict to target-negative cells.** Without the anchor-negative
restriction, lineage-leaky-but-real target cells get mislabeled as contamination.
On the reference data, tumor-infiltrating units' off-target signal was dominated
by EPCAM+ cells = residual xenograft tumor (a real, interpretable contaminant),
which the restriction correctly isolates from aberrant NK cells.

Default thresholds: GREEN <2% · AMBER 2–10% · RED >10%.

---

## Module D — Target-cell maturity  (when an axis exists)

**What it measures:** among target cells, the fraction that are **mature** vs.
immature/progenitor — a potency-relevant readout.

**How it is scored:** maturity_index = score(mature markers) − score(immature
markers), computed per cell; `is_mature = index > 0` among target cells. Also flag
proliferating cells (score over MKI67/TOP2A/CENPF/CCNB1/UBE2C > 0.1). Maturity %
is expressed as a fraction of target cells.

If the target has no defined immature→mature axis in the registry (e.g. MSC, RPE),
Module D is turned off automatically. The agent can enable it by supplying
`literature_markers['maturity_mature'/'maturity_immature']`.

Default thresholds: GREEN ≥60% · AMBER 40–60% · RED <40%.

---

## Module E — Technical QC  (always)

**What it measures:** whether the library itself is releasable, independent of
biology. Composite of:
- **Cell retention %** after MAD outlier + doublet filtering (high is good).
- **Cross-species contamination %** (multi-species references only; low is good).
- **Median mitochondrial %** (low is good).

The module call is the **worst** of the available sub-metrics.

**Caveat #7 — report honest numbers.** Report actual per-unit doublet rates and
species-contamination fractions; do not round them away. CellRanger-*filtered*
matrices typically show low Scrublet rates (often <1–2%, well below the expected
2–5% collision rate) — note that context so a low rate is not mistaken for a
processing error.

Default thresholds: retention GREEN ≥80% / RED <60%; species-contam GREEN ≤1% /
RED >5%; mito GREEN ≤10% / RED >20%.

---

## Overall call & auditability

- **Overall = worst active module.** One RED fails the lot; all-GREEN passes.
- **Caveat #6 — thresholds are defaults, not standards.** No universal numeric
  release threshold exists for most modules; they are product- and
  sponsor-specific. The exact thresholds used are always written to
  `06_thresholds_reference.csv` and printed in the report so they are auditable
  and overridable via `cfg['thresholds']`.

## Caveat #5 — dtype-safe boolean masks (implementation)

Integer (0/1) `.obs` columns break Python's `~` bitwise-NOT
(`~np.array([0,0,1]) → [-1,-1,-2]`, which then mis-indexes via fancy indexing and
silently collapses masks — this caused a blank UMAP panel on the reference run).
Always coerce to bool before masking:
```python
def B(ad, col): return ad.obs[col].values.astype(bool)
```
Used throughout `score_modules.py` and `make_figures.py`.
