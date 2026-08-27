# Methods reference — tissue-expression specificity & on-target safety

Exact, defensible method recipes used by `scripts/tissue_expression.py`. Copy the
rationale into the report Methods section (adapt numbers to the actual run). All choices
were validated on a GCGR reference run and generalize to any human protein-coding gene.

---

## 1. Tau (τ) tissue-specificity score

**Formula (Yanai et al., 2005):**

```
tau = Σ_i (1 − x̂_i) / (n − 1),   where  x̂_i = x_i / max(x)
```

- Computed on **log2(x + 1)**-transformed per-tissue expression, then max-normalized.
  (Log transform is standard for τ on TPM/nTPM; it prevents one very high tissue from
  saturating the score and matches how HPA/GTEx-based specificity is usually reported.)
- Range **0 → 1**: 0 = uniformly expressed (housekeeping), 1 = single-tissue specific.
- Computed **independently per atlas at native resolution** (no organ-collapsing for τ) —
  collapsing would change n and distort the score.
- Negatives clipped to 0; returns NaN if the gene is not expressed anywhere (max ≤ 0).
- Report **both** log2-τ (primary) and linear-τ (for transparency; some pipelines use raw).

**Interpretation guide** (state in report): τ ≳ 0.85 = highly tissue-specific;
τ ≈ 0.5–0.85 = moderately selective; τ ≲ 0.3 = broadly/ubiquitously expressed.
These are conventional soft cutoffs, not hard biological boundaries.

Also report **HPA's native specificity call** (e.g. "Tissue enriched", "Group enriched",
"Tissue enhanced", "Low tissue specificity") and native specificity score for context —
it is an independent categorical assessment, not a substitute for the computed τ.

---

## 2. High-baseline tissue flagging (dual threshold, per atlas)

A tissue is flagged **high-baseline** within an atlas if **either** rule fires:

| Rule | Threshold | Meaning |
|---|---|---|
| Absolute (moderate) | value ≥ **10** TPM / nTPM | widely used "clearly expressed" floor |
| Absolute (high) | value ≥ **25** TPM / nTPM | strongly-expressing tier (annotation only) |
| Relative | value ≥ **90th percentile** of that atlas's per-tissue distribution | top decile for this gene |

- `high_baseline = (value ≥ 10) OR (value ≥ 90th percentile)`.
- **Tier** label: `high (≥25)`, `moderate (≥10)`, else `low (<10)`.
- Record which rule(s) each tissue met (`abs≥25` / `abs≥10` / `top10%`).

**Why dual:** the absolute floor catches biologically meaningful expression even when a
gene is broadly expressed (relative-only would miss it); the relative rule catches the
target's *own* top tissues even when its absolute levels are modest. Constants
(`ABS_MODERATE=10`, `ABS_HIGH=25`, `REL_PCTL=90`) live at the top of the script — adjust
only with explicit justification, and document any change in the report.

---

## 3. Cross-atlas concordance (organ-collapsed)

- Keep full-resolution profiles for τ and ranked bars. For **concordance and the safety
  heatmap only**, collapse GTEx fine sites to **organ level** (mean of member-site median
  TPM; e.g. 13 brain subregions → "Brain", 2 kidney → "Kidney") and harmonize names to
  HPA organ labels via the curated `GTEX_TO_ORGAN` / `HPA_NAME_MAP` dictionaries.
- Sites with no clean HPA consensus-tissue counterpart are dropped (`__drop__`):
  cultured fibroblasts, EBV-transformed lymphocytes, tibial nerve, whole blood.
- Compute **Spearman ρ (primary)** on raw organ values — rank-based, robust to skew and
  to the fact that nTPM ≠ TPM. Also report **Pearson r on log2(x+1)** values.
- Report **n shared organs** with ρ/r and p-values.

**Critical caveat (state in report):** HPA nTPM and GTEx TPM are **not** unit-identical.
Concordance measures whether the two atlases agree on **where** the gene is expressed
(the pattern/ranking), **not** on absolute magnitudes. Interpret accordingly.

If only one atlas is available, concordance is skipped and the report says so.

---

## 4. On-target safety organ matrix (vital core + data-driven)

- Panel = a fixed **vital / high-consequence organ core** — **heart, brain/CNS, liver,
  kidney, lung** — shown for *every* target regardless of expression (a low-but-nonzero
  vital-organ signal still matters for on-target safety), **UNION** any organ that scored
  high-baseline for this target in either atlas.
- Per organ, report GTEx median TPM + HPA nTPM and an on-target flag
  (`High on-target expression` ≥25 in either atlas; `Moderate` ≥10; else `Low / negligible`).
- Rows ordered high → moderate → low, then by max expression.
- Organs annotated with physiological **system** (vital organs marked); the heatmap color-codes
  organ labels by system.

**Interpretation logic:** high on-target expression in the target's intended organ(s) is
expected pharmacology; high on-target expression in a **vital, non-intended** organ is the
key safety signal to surface. Bulk expression ≠ drug exposure — the target being present in
an organ does not prove the drug reaches/affects it, but it is the relevant on-target risk flag.

---

## 5. Literature grounding (Biomni `LiteratureSearch`)

For the top on-target organ(s), run a small number of **focused** `LiteratureSearch`
queries to add cited biological/safety context, e.g.:

- `"<GENE> hepatic safety"` / `"<GENE> liver toxicity"` (if liver is high)
- `"<GENE> renal expression"` / `"<GENE> kidney function"` (if kidney is high)
- `"<GENE> <top-organ> physiology"` for the target's dominant organ
- `"<GENE> on-target toxicity"` for a known-liability check

Rules:
- Cite **every** external claim inline `[N]`; never fabricate references, PMIDs, or DOIs.
- Prefer strongest evidence (reviews, trials, large studies) where available.
- If `LiteratureSearch` returns nothing relevant, say so — do not invent context.
- Keep it targeted (top organs only) — this is a safety read, not a full literature review.
  For a deep literature synthesis, defer to the `literature-preclinical` / `literature-review` skills.

---

## 6. Figures

Data plots via matplotlib/seaborn (Phylo palette, Liberation Sans, colorblind-aware,
saved SVG + PNG with editable text). See `scripts/make_figures.py`. Four figures:

1. **Ranked tissue bars** — per-tissue expression (GTEx + HPA), high-baseline colored.
2. **Concordance scatter** — log2 GTEx vs log2 HPA over shared organs, ρ annotated (both atlases only).
3. **τ / specificity summary** — τ bars + high-baseline flag table + HPA native call.
4. **Safety heatmap** — safety organ panel × {GTEx, HPA}, log-scaled, system-colored labels.

Plus a **`GenerateImage` infographic** (conceptual summary: target + top tissues + τ +
key safety takeaway) — schematic, so it MUST use `GenerateImage`, not plotting code.

**Every figure passes a `media_output_check` (Read tool) before inclusion.** Regenerate
if blank/clipped/unreadable.

---

## Key assumptions to document in every report

- nTPM (HPA) and TPM (GTEx) are compared on ranks/log-scale (pattern, not magnitude).
- Bulk-tissue expression reflects the tissue's cell mixture; cell-type resolution is out
  of scope (note HPA single-cell / GTEx snRNA-seq as a future extension).
- τ is computed on median profiles.
- Expression in an organ ≠ the drug reaches/affects it; high on-target expression in vital
  organs is the relevant on-target risk signal.
- GTEx vintage depends on source (curated datalake v11 vs v8 API) — state which was used.
