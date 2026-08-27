# Ancestry-aware LD for fine-mapping

Fine-mapping is only as trustworthy as the LD matrix behind it. SuSiE (and every summary-statistics
fine-mapper) reconstructs which correlated variant is causal by comparing the GWAS z-scores against a
**reference LD matrix**. If that LD does not match the ancestry composition of the GWAS sample, the
math silently breaks: the model can place ~all posterior probability on the wrong variant and report a
falsely tiny, falsely confident credible set. This guide is the core of what makes this skill
"ancestry-aware."

## The one rule

**The LD reference must match the ancestry of the GWAS sample.** Everything below is how to satisfy
that rule and how to detect when you have failed to.

## Priority order for choosing an LD source

1. **In-sample LD (best).** If you have (or the study released) an LD matrix computed from the *same*
   individuals as the GWAS, use it. This eliminates ancestry mismatch by construction. Large consortia
   increasingly release these (e.g., per-locus LD from the actual analysis cohort).

2. **Ancestry-matched external reference panel (usual case).** When in-sample LD is unavailable, use a
   reference panel of the *same* single ancestry as the GWAS — e.g. 1000 Genomes Phase 3 superpopulation
   EUR / AFR / EAS / SAS / AMR, or a larger matched panel (UK Biobank-derived, TOPMed, HRC) if you have
   access. Resolve the ancestry from:
   - explicit user input (`--ancestry`), then
   - GWAS Catalog study metadata (`initialSampleSize`, `ancestries[]` — the `fetch_gwas_catalog.py`
     script maps these free-text descriptions to a superpop and prints its guess), then
   - the source publication.
   Do **not** default to EUR silently. If you cannot determine ancestry, stop and ask.

3. **Multi-ancestry GWAS (the hard case).** A meta-analysis across ancestries (e.g. "European +
   East Asian + African") has **no single matching LD panel**. Options, in order of preference:
   - Use the released **in-sample/meta LD** if the consortium provides it.
   - Fine-map the **largest single-ancestry stratum separately** using its matched panel, then combine
     insight across strata qualitatively. This is usually the most defensible.
   - Approximate with the LD of the **dominant ancestry**, but only if it overwhelmingly dominates the
     effective sample size — and report it as an approximation with the LD-mismatch diagnostic front
     and center.
   - Consider a multi-ancestry method (SuSiEx, MESuSiE, PAINTOR) — out of scope for this skill, but the
     right tool when several ancestries contribute comparably. Document the hand-off.

## The mandatory guardrail: LD-mismatch diagnostics

**Every run** of `run_susie_finemap.R` computes two diagnostics from `susieR`. Never skip them; never
report a credible set without reporting `estimated_s`.

- **`estimate_s_rss(z, R, n)`** returns a scalar **s** (a.k.a. lambda) in `[0,1]` measuring the
  inconsistency between the z-scores and the LD matrix. **s ≈ 0** = z-scores are consistent with the LD
  (good). **s large** = mismatch.
- **`kriging_rss(z, R, n)`** predicts each variant's z from the others via the LD and flags observed-vs-
  expected outliers — useful for catching individual allele-flip / strand / build errors even when
  overall s looks acceptable.

### Interpreting s (heuristic, not a hard cutoff)

| estimated s | Interpretation | Action |
|---|---|---|
| < 0.1 | LD consistent with z-scores | proceed; credible set trustworthy |
| ~0.1–0.2 | mild mismatch | proceed with caution; inspect kriging outliers; prefer a better-matched panel |
| > 0.2 | serious mismatch | **do not trust the credible set**; fix the LD source (ancestry, build, in-sample LD) before reporting |

These bands are guidance, not law — s scales with signal strength and locus. The decisive question is
always: *does the LD ancestry match the GWAS ancestry?* A low s with a matched panel is reassuring; a
low s obtained by shopping for whatever panel minimizes s is self-deception.

**Worked anchor (PROX1 T2D):** European GWAS (Xue 2018) + 1000G EUR panel → estimated **s = 0.0188**,
kriging outliers = 0. Clean match; the single-variant credible set (rs340874, PIP 0.96) is trustworthy.

## Signed r vs r²

The LD matrix passed to SuSiE must be **signed correlation r**, oriented so the sign is consistent with
the GWAS effect allele — **not** r². The in-skill `ld_utils.py` helper (`scripts/ld_utils.py`)
produces a signed-r matrix harmonized to the effect allele. r² (always positive) is only for *coloring*
the regional plot, never for the model. A matrix of r² fed to `susie_rss` will corrupt the fit.

## Common LD failure modes (and how this skill catches them)

- **Ancestry mismatch** → high `estimated_s`. Fix the panel.
- **Build mismatch** (GWAS in GRCh37, panel in GRCh38 or vice versa) → variants don't align / huge s.
  Resolve build first (`detect_build_liftover.py`) so positions and the panel share one assembly.
- **Allele flips / strand ambiguity** (palindromic A/T, C/G SNPs) → kriging outliers. Harmonization in
  `ld_utils.py` orients alleles; ambiguous palindromes are flagged.
- **Tiny float asymmetry** from PLINK export (R ≠ Rᵀ by ~1e-16) → auto-symmetrized in
  `run_susie_finemap.R` (reported as `ld_asymmetry_fixed`); this is benign, not a mismatch.

## References

- Zou, Carbonetto, Wang, Stephens (2022) *Fine-mapping from summary data with the "Sum of Single
  Effects" model.* PLoS Genetics. doi:10.1371/journal.pgen.1010299 — source of `estimate_s_rss` /
  `kriging_rss`.
- Wang, Sarkar, Carbonetto, Stephens (2020) *A simple new approach to variable selection in regression,
  with application to genetic fine mapping.* JRSS-B. doi:10.1111/rssb.12388 — original SuSiE.
