# Direction-of-Effect Mapping Rules

This is the scientific core of the skill. Each evidence axis produces a **raw readout**, which
maps to a **therapeutic-direction vote** ∈ {`INHIBIT`, `ACTIVATE`, `not_informative`} via a
fixed rule. The master principle:

> If **loss / reduction** of the target's function is beneficial for the indication → **INHIBIT**.
> If **gain / restoration** of function is beneficial → **ACTIVATE**.

Direction is **always indication-specific**. Record the indication with every call.

---

## Axis 1 — Human genetics (direction of effect)

| Raw genetic readout | Vote | Reasoning |
|---|---|---|
| Protective **loss-of-function** allele (pLoF carriers have lower disease risk / better biomarker) | **INHIBIT** | Nature already ran the "reduce this target" experiment and it helped. |
| **Pathogenic gain-of-function** allele (hyperactive protein causes disease) | **INHIBIT** | The activity must be *reduced*; drug the target down. (e.g. PCSK9 GoF → ADH.) |
| Pathogenic **toxic gain-of-function** at a specific allele (mutant protein is actively harmful) | **INHIBIT** (allele-directed) | Knock down the *mutant* protein. Germline null may be silent — that is NOT opposing (see PNPLA3 I148M). Tier ≤ High–Moderate. |
| **Loss-of-function causes disease** (haploinsufficiency, recessive deficiency) | **ACTIVATE** | Restore/augment function (gene therapy, enzyme replacement, agonist). |
| Protective **gain-of-function** allele (more activity is protective) | **ACTIVATE** | Increase the target's activity. |
| No consistent human genetic signal | `not_informative` | Do not invent a direction. |

Sources: Open Targets `associatedDiseases` datatype scores + `variant`/`credibleSet`/L2G;
GeneBass pLoF burden direction; GWAS Catalog effect direction; gnomAD LoF-intolerance (pLI,
LOEUF) as *context* (a highly LoF-intolerant gene tempers an INHIBIT call with a tolerability
caveat, but constraint alone is not a direction vote).

## Axis 2 — Functional / CRISPR

| Raw functional readout | Vote | Reasoning |
|---|---|---|
| CRISPR/RNAi LoF **reproduces the desired therapeutic phenotype** (e.g. knockdown lowers the pathogenic output) | **INHIBIT** | Perturbing the target down does the therapeutic thing. |
| **Selective dependency** in the disease-relevant lineage (DepMap: strongly negative gene-effect in relevant models, near-zero elsewhere) | **INHIBIT** | Tumor/lineage needs the target → inhibiting it is selectively lethal. |
| **Broad pan-essentiality** (DepMap: strongly negative gene-effect across most/all cell lines) | **INHIBIT** + **toxicity caveat** | Direction may still be INHIBIT, but flag on-target toxicity / narrow therapeutic index. |
| LoF **worsens** the phenotype or removes a protective function | **ACTIVATE** | Losing the target is bad → augment it. |
| No informative functional perturbation data | `not_informative` | — |

**DepMap score convention (do not get this wrong):** in `CRISPRGeneEffect.csv`, **negative =
essential** (knockout kills cells); ~0 = non-essential; −1 ≈ median of common essentials.
`CRISPRGeneDependency.csv` gives probability-of-dependency in [0,1] (higher = more dependent).
See the `gene-essentiality` sibling skill. When correlating essentiality with anything, invert
first (`-gene_effect`). Sanity check: known essentials (RPS14, RPL11) must have negative
gene-effect.

## Axis 3 — Drug mechanism of action

| Approved / late-clinical drug action type | Vote | Reasoning |
|---|---|---|
| **Inhibitor / antagonist / negative modulator / silencer (siRNA/ASO) / degrader (PROTAC) / blocking antibody** | **INHIBIT** | The clinic is already reducing the target. Strong, human-validated signal. |
| **Agonist / activator / positive modulator / enzyme replacement / gene therapy restoring function** | **ACTIVATE** | The clinic is increasing/restoring the target. |
| Only preclinical or no drugs | `not_informative` | Note the absence explicitly (it is itself a finding). |

Source: Open Targets `drugAndClinicalCandidates` → `drug.mechanismsOfAction.rows{actionType}`
+ `maximumClinicalStage`. Prefer **approved** > Phase 3 > earlier when assigning weight to this
axis. ChEMBL/OpenFDA can add MoA/pharmacovigilance depth.

## Axis 4 — Mouse knockout

| Mouse KO readout | Vote | Reasoning |
|---|---|---|
| KO **mimics the desired therapeutic phenotype** (e.g. resistant to the disease, better biomarker) | **INHIBIT** | Removing the gene in vivo does the therapeutic thing. |
| KO is **phenotypically silent** for the indication | `not_informative` | **NOT opposing.** A silent null does not argue for ACTIVATE. Especially relevant for toxic gain-of-function targets. |
| KO **worsens** or **causes** the disease phenotype | **ACTIVATE** | Losing the gene is harmful → augment it. |
| Conditional/tissue-specific KO gives a directionally clear result | map as above | Note the conditional context. |

Source: Open Targets `mousePhenotypes` (phenotype classes) + literature for phenotype
direction. MouseMine gene sets can add context.

---

## Reconciliation → consensus, tier, flags

1. **Denominator = informative axes only.** `not_informative` axes are excluded.
2. **Consensus = majority direction** across informative axes. Ties or true opposition →
   `CONTESTED` (do not force a call).
3. **Confidence tier** (graded by concordance, interpretive caveat, and evidence breadth;
   the denominator is *informative* axes):
   - **High** — ≥3 informative axes, all concordant, no interpretive caveat.
   - **High–Moderate** — all informative axes concordant but ≥1 needs allele-/context-specific
     interpretation (e.g. benefit is allele-specific), regardless of how many axes are
     informative, as long as there is no opposition and ≥2 informative axes. *This is the
     PNPLA3 case: human genetics (toxic GoF) + functional (ASO knockdown) agree; the silent
     germline KO and absent drug are `not_informative` rather than opposing.*
   - **Moderate** — informative axes concordant with no opposing axis, but the evidence is
     thin (exactly 2 informative axes and no interpretive caveat), or a 3/4 majority with one
     non-informative axis.
   - **Low–Contested** — an axis opposes the majority (true discordance), consensus is
     `CONTESTED`, or fewer than 2 informative axes (too thin to call).

   Rationale for the thinness rule: two concordant *high-strength* axes (human genetics plus a
   functional or approved-drug readout) are a legitimate directional call; a single lone axis
   is not. Do not treat "fewer than 3 axes" as automatically contested — reserve
   Low–Contested for genuine opposition or a single informative axis.
4. **Strict any-conflict flag:** every axis that opposes the majority OR needs special
   interpretation is surfaced with a mechanistic explanation in `key_flag` / `flagged`.
5. **Evidence-strength ordering** (tie-breaker + tier justification only, NOT numeric weights):
   `human genetics ≈ approved-drug MoA  >  functional/CRISPR  >  mouse KO`.
   Rationale: human genetics and approved drugs are direct human-validated readouts; CRISPR is
   strong mechanistic but cell-context-dependent; mouse KO can diverge from human biology.
6. **Safety ≠ direction.** On-target safety signals (MACE, hepatotoxicity, etc.) go in the
   safety flag, never flip the direction.

## Worked reconciliations (from the validated PCSK9/PNPLA3/SOST run)

- **PCSK9 → INHIBIT, High (4/4).** Protective human LoF + LDLR-degradation mechanism +
  approved inhibitors (evolocumab/alirocumab/inclisiran) + KO lowers cholesterol. Flags:
  murine Pcsk9-null NASH/HCC on high-cholesterol diet, modest T2D PheWAS signal → *safety
  context*, not reversal.
- **SOST → INHIBIT, High (4/4).** LoF causes high bone mass (sclerosteosis/van Buchem) +
  Wnt-disinhibition mechanism + approved antibody (romosozumab) + KO increases bone mass.
  Flag: romosozumab black-box cardiovascular (MACE) warning → *safety*, not reversal.
- **PNPLA3 → INHIBIT, High–Moderate.** I148M is the largest common risk allele + it is a
  **toxic gain-of-function** (sequesters ABHD5, blocks lipolysis) + lead agent is an ASO that
  knocks PNPLA3 down. Two informative axes agree (human genetics = allele-directed INHIBIT;
  functional = ASO knockdown reduces steatosis). **Mouse-KO axis is silent → not-informative,
  NOT opposing**, and there is no approved drug (drug-MoA axis `not_informative`). So the call
  rests on 2/2 concordant informative axes with an allele-specific interpretive caveat → tier
  **High–Moderate** (concordant + caveat), *not* Low–Contested — because the two axes carrying
  it are high-strength (human genetics + functional) and nothing opposes. This is the canonical
  case showing that a silent KO and an absent drug reduce breadth without creating conflict.

These illustrate the two rules people most often get wrong: (a) a silent KO is `not_informative`,
not ACTIVATE; (b) knockdown of a gain-of-function allele is an INHIBIT strategy.
