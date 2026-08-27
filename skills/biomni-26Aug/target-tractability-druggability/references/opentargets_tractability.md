# Open Targets tractability — bucket meanings & interpretation

Open Targets computes **tractability** as a set of boolean "buckets" per **modality**. Each bucket
is a heuristic flag; a `True` means the target satisfies that evidence criterion. Buckets are
returned by the GraphQL `tractability { label modality value }` field. `modality` is one of:

| `modality` code | Meaning |
|---|---|
| `SM` | Small molecule |
| `AB` | Antibody |
| `PR` | PROTAC / molecular-glue degrader |
| `OC` | Other clinical modality (e.g. oligonucleotide, enzyme, cell therapy) |

> **Do not hardcode a fixed set of modalities.** Iterate over whatever `modality` values the API
> returns for the target. Newer releases can add/rename buckets — group dynamically by `modality`
> and count `value == True`.

## What the buckets mean (as of Platform v25–v26; verify labels against the live API)

### Small molecule (`SM`) — roughly ordered strongest → weakest
- **Approved Drug** — an approved small molecule hits this target.
- **Advanced Clinical** — SM in Phase 2/3.
- **Phase 1 Clinical** — SM in Phase 1.
- **Structure with Ligand** — a PDB structure exists with a bound drug-like ligand.
- **High-Quality Ligand** — a high-quality small-molecule binder is known (ChEMBL activity).
- **High-Quality Pocket** — a DoGSiteScorer/fpocket-style druggable pocket is predicted.
- **Med-Quality Pocket** — a lower-confidence pocket.
- **Druggable Family** — the target belongs to a historically druggable protein family
  (kinase, GPCR, NHR, ion channel, protease, …).

### Antibody (`AB`)
- **Approved Drug / Advanced Clinical / Phase 1 Clinical** — antibody in the clinic.
- **UniProt loc high/med conf** — UniProt subcellular-localization evidence that the target is
  cell-surface / secreted (**accessible** to an antibody).
- **GO CC high/med conf** — GO Cellular-Component evidence of surface/secreted localization.
- **UniProt SigP or TMHMM** — signal peptide / transmembrane prediction (accessibility).
- **Human Protein Atlas loc** — HPA localization evidence.

> **Antibody deal-breaker:** if the *only* True buckets are localization buckets pointing to an
> **intracellular** location — i.e. no accessibility/surface/secreted signal — the target is
> effectively **not antibody-tractable** with conventional mAbs. Intracellular localization is the
> single most common reason a target fails antibody tractability.

### PROTAC / degrader (`PR`)
- **Approved Drug / Advanced Clinical / Phase 1 Clinical** — a degrader in the clinic.
- **Literature** — degrader activity reported in the literature.
- **UniProt Ubiquitination / Database Ubiquitination** — the target is known to be ubiquitinated
  (a degrader-enabling signal).
- **Half-life Data** — protein half-life measured (degradation kinetics known).
- **Small Molecule Binder** — there exists a small-molecule handle a PROTAC can be built onto.

> A degrader needs (a) a small-molecule binding handle and (b) accessibility to the ubiquitin–
> proteasome system. `Small Molecule Binder` + ubiquitination/half-life buckets True = strong
> in-principle degrader enablement even when no degrader is yet in the clinic.

### Other clinical (`OC`)
- Clinical-stage buckets for miscellaneous modalities. Usually all False unless the target has a
  non-SM/AB/PR clinical agent.

## Interpretation rubric (use consistently)

For each modality, translate bucket counts into a plain verdict:

- **Clinical precedent present** (any Approved-Drug / Advanced-Clinical / Phase-1 bucket True) →
  the modality is **validated** for this target. This dominates the verdict.
- **Enabling evidence only** (structure/ligand/pocket/ubiquitination buckets True, no clinical) →
  the modality is **plausible/emerging** but unproven.
- **Localization/family flags only** → weak signal; interpret in context (for AB, surface vs
  intracellular is decisive).
- **All False** → no tractability evidence for that modality.

**Key nuance to always state:** an *Approved Drug* bucket can be True while *High-Quality Pocket*
is False (the classic "undruggable pocket, druggable target" situation — e.g. covalent or cryptic-
pocket strategies that bypass a shallow orthosteric surface). Report both facts rather than
collapsing them.

## Companion annotations to pull alongside tractability

- **`isEssential`** and **`depMapEssentiality`** — dependency signal (see
  `../references/` note: DepMap scores are **inverted** — *negative = essential*; a strong
  dependency motivates mutant-/context-selective strategies to preserve a therapeutic window).
- **`drugAndClinicalCandidates`** — curated known drugs / clinical candidates (drug name, type,
  max clinical stage, mechanism of action, indications). This is **not exhaustive** — treat as a
  curated snapshot, not the full competitive landscape.
- **`safetyLiabilities`** — curated safety events. Zero records ≠ "safe"; it means no curated
  liability in Open Targets.
- **`meta { dataVersion { year month iteration } }`** — always cite the data release in the report.
- **Optional target–disease association** (`disease → associatedTargets` or
  `target → associatedDiseases`) — only when the user supplies a disease.

## Field-name stability warning

The Platform GraphQL schema changes between releases. Fields that have moved recently:
- `knownDrugs` on `Target` was replaced by **`drugAndClinicalCandidates`**.
- On the `Drug` type, `isApproved` and `maximumClinicalTrialPhase` were removed; use
  **`maximumClinicalStage`** (and the row-level `maxClinicalStage`).
- Drug `diseases` rows are `ClinicalDiseaseListItem` with nested `disease { name }`.

**Always** check `payload["errors"]` (GraphQL returns errors in the body with HTTP 200). If a field
is rejected (`Cannot query field "X"`), run an introspection query or consult the playground at
`https://api.platform.opentargets.org/api/v4/graphql/browser` and adapt. The helper script
`opentargets_druggability.py` is written defensively for exactly this reason.

## License & commercial use (state accurately in the report)

- **Open Targets Platform data: CC0 1.0 (public domain).** Verified at
  [platform-docs.opentargets.org/licence](https://platform-docs.opentargets.org/licence). The data
  is dedicated to the public domain with no restrictions on downstream use, including commercial.
  **Do not state the Platform data is "CC BY 4.0"** — it is CC0 1.0. A prior candidate run made this
  error; the actual license is more permissive than claimed, but the statement was factually wrong.
- **Open Targets codebases** (pipelines, GraphQL API, React UI): **Apache 2.0**.
- **Individual data sources** within Open Targets retain their own licenses (e.g. ChEMBL = CC BY-SA
  3.0, UniProt = CC BY 4.0, Cancer Gene Census / Project Score / Genomics England PanelApp =
  "commercial use for Open Targets" only). However, Open Targets confirms that **all** listed data
  sources "have agreed for their data to be used without restriction by all Open Targets users."
- When reporting commercial-use status, cite the upstream license page and verify the current terms
  rather than relying on memory.
