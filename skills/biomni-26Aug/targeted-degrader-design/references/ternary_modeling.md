# Ternary-complex structural modeling for degraders

Reference material for the **advanced tier** of the `targeted-degrader-design`
skill. The core steps 1–8 model the **warhead only**. This document covers how to
predict and rank the **productive ternary complex** (E3 ligase – degrader –
target), which is what actually drives degradation. The field is moving fast and
no single tool is a settled winner; the guidance below is deliberately hedged.

All citations here were taken from tool output in the session that produced this
skill. Where a claim is a benchmark result, the specific numbers are quoted from
the source abstract/highlight; treat them as the authors' reported values, not as
independently reproduced results.

---

## The modern three-stage stack

A realistic degrader ternary workflow today has three stages:

1. **Structural engine** — a cofolding model produces a candidate ternary pose.
2. **(Optional) glue/degrader-specific enhancement** — a method that improves or
   re-samples the ternary pose beyond a vanilla cofold.
3. **Physics-based ranking** — free-energy methods rank analogs / optimize
   potency once a ternary pose exists.

### Stage 1 — Structural engine (cofolding)

**In-platform options:** `boltz-2`, `chai-1` (both predict protein +
small-molecule + nucleic-acid complexes). `alphafold-v2` is available but is a
monomer/complex protein folder, **not** a small-molecule cofolding/ternary engine.

**AlphaFold3 is not in this platform.** It is frequently cited as the strongest
cofolder for molecular-glue ternaries, but the "best engine" is **unsettled**:

- Liao, Zhu, Xie, et al. (2025), *J Chem Inf Model*, DOI 10.1021/acs.jcim.5c01860
  — "AlphaFold 3 is the best cofolding method for predicting molecular glue
  ternary structures, but current methods struggle with large interaction
  interfaces, domain–domain complexes, and degrader complexes." (Preprint
  version: bioRxiv 2025.05.25.655997, DOI 10.1101/2025.05.25.655997.)
- Riepenhausen, Sarnow, Robaa, Sippl (2026), *Arch Pharm*, DOI 10.1002/ardp.70225
  — "Boltz-2 outperformed AlphaFold 3 in predicting PROTAC- and molecular
  glue–mediated ternary complexes … across 40 benchmark structures," with
  misorientation/flexibility-driven failures limiting generalizability.

**Practical guidance:** run more than one available engine (Boltz-2 and Chai-1),
inspect poses manually, and do not assume a single winner. Expect **PROTAC**
ternaries to be harder than glue ternaries (linker flexibility).

### Stage 2 — Modality routing / binder design

- Small-molecule glue or PROTAC → cofold (Stage 1).
- **Peptide or protein binder** ("glue" that is actually a biologic) →
  `boltzgen` (BoltzGen: "Universal binder design vs proteins, peptides, small
  molecules, DNA, RNA"). This is the better fit when the recruiting moiety is not
  a classic small molecule.

Named small-molecule glue-enhancement models (e.g. steered/guided-diffusion
"…Fold" variants, or dedicated glue-pose models) are sometimes cited in this
stage. **None are verified in this environment.** Treat any such tool as a
candidate to evaluate on a known-answer benchmark before depending on it — do not
write it into a workflow as a hard dependency.

### Stage 3 — Physics-based ranking (the reliable differentiator)

Cofolding **scores** are weak at ranking potency; use free-energy methods once a
ternary pose exists:

- Lukauskis, Kashif-Khan, Tame, Potterton (2025), ChemRxiv,
  DOI 10.26434/chemrxiv-2025-tb29n — "FEP outperforms Boltz-2 for molecular glue
  ternary complex binding, achieving better absolute predictability (RMSE
  0.3–1.25 kcal/mol) and stronger correlations across 93 compounds, while Boltz-2
  shows RMSE > 3 kcal/mol and poor/negative correlations." → FEP is the more
  reliable tool for prospective ranking / SAR.
- Izaguirre, McDargh, Trovato, et al. (2025), bioRxiv,
  DOI 10.1101/2025.01.13.632817 (**GlueMap**) — predicted ternary-complex
  stability (rigorous FEP-like estimates) correlates with degradation potency,
  and a supervised VAE + attention regression gives accurate prospective ranking,
  reportedly outperforming Boltzmann-based structure/cofolding models.
- Dudas, Athanasiou, Mobarec, et al. (2025), *J Chem Theory Comput*,
  DOI 10.1021/acs.jctc.5c00064 — quantifies **cooperativity** through binding
  free energies for molecular-glue degraders; identifies potent glues across
  targets.
- Furui & Ohue (2024), *ACS Omega*, DOI 10.1021/acsomega.4c11413 — predicted
  (HelixFold3) holo structures can substitute for crystal structures in
  early-stage **FEP** with good binding-site accuracy → precedent for seeding
  physics-based ranking from a *predicted* (not crystal) structure.

**Note on FEP+ specifically:** commercial FEP+ (Schrödinger) is not part of the
Biomni catalog. If a licensed FEP engine is unavailable, use it as an external
step, or substitute an available free-energy / ensemble approach; do not imply
FEP+ runs inside this environment.

### Method-choice reviews (context)

- Solazzo, Chen, Riniker (2026), *Curr Opin Struct Biol*,
  DOI 10.1016/j.sbi.2025.103217 — "Machine learning, docking, or physics for
  structure prediction of ligand-induced ternary complexes": ML and docking can
  predict ternary structures but both face real limitations. Useful framing for
  choosing among the three stages above.

---

## Why PROTACs are harder than glues (design implication)

- A **molecular glue** ternary has a small, often shallow, cooperative
  protein–protein interface; the glue occupies a compact pocket.
- A **PROTAC** ternary is dominated by **linker conformational sampling** — the
  same two proteins can be bridged in many geometries, and only some are
  productive for ubiquitin transfer.
- Benchmarks explicitly flag **degrader complexes** and **large interaction
  interfaces** as current failure modes for cofolding.

**Implication:** if a workflow built a PROTAC library (skill steps 1–8), the
ternary tier must use PROTAC-aware conformational sampling and treat the linker
explicitly. A glue-only pipeline will under-serve PROTAC ternary prediction. This
is why the main skill keeps ternary modeling as a separate, clearly-scoped tier
rather than folding it into the 2D design pipeline.

---

## Suggested minimal advanced-tier recipe

1. Cofold the ternary with **Boltz-2** and **Chai-1** (both in-platform); compare
   poses. For a peptide/protein recruiter, use **BoltzGen** instead.
2. Inspect top poses for a plausible, buried E3–target interface; discard
   misoriented models. Expect lower confidence for PROTACs than glues.
3. If a licensed free-energy engine is available, rank analogs by FEP / ternary
   free energy (this is the step that actually correlates with potency). Otherwise
   report cofolding poses as **hypotheses only**, and say ranking was not done.
4. State clearly which stages were run and which tools are external vs
   in-platform. Never present a cofolding score as a potency prediction.
