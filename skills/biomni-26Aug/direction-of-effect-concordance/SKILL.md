---
id: "skill_967e65dbbeb85ee774732a6211f1000c"
name: direction-of-effect-concordance
description: "Use to decide whether a therapeutic target should be inhibited or activated. Integrates human genetics, CRISPR/functional screens, drug mechanisms, and mouse phenotypes into an activate-vs-inhibit consensus with confidence and discordance flags; triggers on agonist/antagonist, LoF protective/harmful, or target directionality questions."
category: drug_discovery
visibility: public
starting-prompt: "For genes <G1, G2, ...>, tell me whether to activate or inhibit each target and why, with a PDF report"
---

# Direction-of-Effect Concordance

Turn "should we **activate or inhibit** this target?" into a **decision-complete,
citation-honest PDF** grounded in the published record and structured target-evidence
databases. For each target the skill asks one decision-relevant question — *does the combined
evidence argue for reducing (INHIBIT) or increasing (ACTIVATE) the target's activity to treat
its indication?* — and answers it by integrating several independent evidence axes into a
single **direction-of-effect consensus** with a transparent confidence tier and explicit
discordance flags.

**This skill is evidence synthesis + structured reporting, not an analysis that runs the
assays.** It tells the user *which direction* to drug a target and *why*, reconciles genuine
cross-source conflicts instead of hiding them, and points to the Biomni sibling skills that
actually *execute* each axis. It is a sibling of `methods-landscape-review` (shares the
retrieve → verify → visualize → report backbone) but decides *target direction* rather than
comparing methods.

---

## When to Use This Skill

Trigger on requests like:
- "Should we **activate or inhibit** PCSK9 / SOST / this kinase?"
- "**Agonist or antagonist** for GLP1R?", "inhibitor vs activator", "which way to drug X"
- "What is the **direction of effect** for <gene>?", "is loss-of-function protective or harmful?"
- "Does the **genetics, CRISPR, mouse, and drug** evidence **agree** on target direction?"
- "Give me a **genetics-to-drug concordance** call for these targets, with a report."

Works for **one target or a set** (batch). If the user names targets but not their
indication/disease context, ask for it (direction is indication-specific). If they give a
disease and want target *discovery/ranking* (not direction), that is a different task — use
`open-targets` association ranking instead.

Do **not** use this skill to actually run a CRISPR screen, GWAS, or mouse study — hand off to
the sibling skills in Step 7.

---

## Scope

- **Does**: identifier resolution; multi-axis structured-evidence retrieval (Open Targets,
  DepMap CRISPR, human-genetics DBs); LiteratureSearch-first directional evidence; a per-axis
  raw→direction mapping; a per-target consensus + confidence tier + strict discordance flags;
  a blocking citation-verification gate; data-driven figures + a GenerateImage infographic; a
  Biomni-resource inventory; and a Phylo-branded PDF via `pdf-report-generation`.
- **Does NOT**: run the assays/screens itself, re-derive effect sizes from primary data,
  fabricate any number or citation, override a genuine axis conflict silently, or make
  direction calls for non-human targets (the structured sources are human).

## Inputs

- **Targets** (required): one or more gene symbols or IDs (symbol / Ensembl / UniProt / HGNC).
- **Indication / disease context** (required, per target or shared): free text or EFO/MONDO
  ID. Direction is indication-specific (e.g. a gene can be "inhibit" for one disease and
  "activate" for another).
- **Axis set** (optional): defaults to the 4 axes below; the user may **extend** (e.g. add an
  expression/eQTL axis or a protein-network axis) or **trim** (e.g. drop mouse KO for a target
  with no model). Each axis carries a fixed documented direction rule, so changing the set
  never breaks correctness.
- **Optional** retrieval filters (year range, study type, journal quartile, human-only, min
  sample size) and report depth (abstract-level vs. targeted full-text on pivotal papers).

## Outputs (all under `/mnt/results/<run_dir>/`)

- `data/evidence_matrix.csv` — one row per target × axis (raw readout, vote, source, cites).
- `data/consensus_calls.csv` — per-target consensus, concordance, confidence tier, flags.
- `data/opentargets_raw.json`, `data/depmap_summary.csv`, `data/genetics_summary.csv` — raw
  structured pulls, each tagged with source + release/version.
- `references.jsonl` (written by LiteratureSearch to
  `/mnt/results/execution_trace/references.jsonl`) + `data/citation_verification.json`
  (`doi_layer_status`: clean / partial / failed).
- `figures/*.{png,svg}` + `figures/fig_manifest.csv`, and one **infographic** `.png`.
- `synthesis.json` (agent-authored narrative) + `references.json` (verbatim verified refs).
- Final `report_<slug>.pdf` (+ optional per-target one-pagers when N_targets ≥ 6).

---

## The Direction-Mapping Rule (fixed, documented)

Every axis is translated to a therapeutic-direction vote with a **single rule**: *if loss or
reduction of the target's function is beneficial for the indication → **INHIBIT**; if gain or
restoration of function is beneficial → **ACTIVATE**.* Applied per axis:

| Axis | Raw readout → vote |
|---|---|
| **Human genetics** | protective **loss-of-function** allele → INHIBIT; **pathogenic gain-of-function** allele → INHIBIT (its activity must be reduced); protective GoF, or LoF that *causes* disease → **ACTIVATE** |
| **Functional / CRISPR** | LoF reproduces the desired therapeutic phenotype, or **selective** dependency in the disease-relevant lineage → INHIBIT; **broad pan-essentiality** → INHIBIT **with toxicity caveat**; LoF worsens the phenotype → ACTIVATE |
| **Drug MoA** | approved/late-clinical **inhibitor / antagonist / silencer / degrader** → INHIBIT; **agonist / activator / enzyme-replacement** → ACTIVATE |
| **Mouse KO** | knockout **mimics** the desired therapeutic phenotype → INHIBIT; knockout is **phenotypically silent** → *not-informative* (NOT opposing); knockout **worsens** the phenotype → ACTIVATE |

**Critical nuances the skill must honor:**
- A **null loss-of-function phenotype does not license ACTIVATE.** Knockdown of a toxic
  **gain-of-function allele** is mechanistically an **INHIBIT** strategy even when the germline
  knockout is silent (the canonical PNPLA3 I148M case).
- **DepMap gene-effect scores are inverted from intuition: negative = essential (knockout
  kills cells).** Interpret accordingly (see `references/direction_rules.md` and the sibling
  `gene-essentiality` skill). Never mis-sign this.
- **On-target safety signals** (e.g. cardiovascular MACE, hepatotoxicity) are reported as
  **safety flags**, never as direction reversals — they change *how/in whom* a target is
  drugged, not *which direction*.

## Confidence Tiers & Discordance

Per-target consensus = **majority vote across *informative* axes** (not-informative axes are
excluded from the denominator). Confidence tier:

- **High** — **≥3** informative axes, all concordant, no interpretive caveat.
- **High–Moderate** — all informative axes concordant (≥2, none opposing) but ≥1 needs
  **allele-/context-specific interpretation** (e.g. benefit is allele-specific). *This is the
  PNPLA3 case: 2 concordant high-strength axes (genetics + functional) with a silent KO and no
  drug as `not_informative`, not opposing.*
- **Moderate** — informative axes concordant, no opposing axis, but evidence is **thin**
  (exactly 2 informative axes with no caveat, or a 3/4 majority with one non-informative axis).
- **Low–Contested** — an axis **opposes** the majority direction, consensus is CONTESTED, or
  **fewer than 2 informative axes** (too thin to call). Do **not** auto-downgrade solely for
  having <3 axes — two concordant high-strength axes are a legitimate call.

**Strict any-conflict flag:** every axis that opposes the majority *or* requires special
interpretation is surfaced with a **mechanistic explanation**. Evidence-strength ordering
(human genetics ≈ approved-drug MoA > functional/CRISPR > mouse KO) is used **only** as a
tie-breaker and to justify the tier — **not** as an opaque numeric weighting model. When axes
genuinely conflict and cannot be reconciled, report **CONTESTED** and present both sides; do
not manufacture a false consensus.

---

## Workflow

Run steps in order. Create a run directory first:
`RUN=/mnt/results/target_direction_<slug> && mkdir -p $RUN/data $RUN/figures`.
Scripts live in this skill's `scripts/`; reference docs in `references/`.

### Step 0 — Align (brief)
Confirm the target list, each target's indication/context, the axis set (default 4; may
extend/trim), and report depth. 1–2 questions max; proceed if already specified.

### Step 1 — Resolve identifiers
`python scripts/pull_opentargets.py --resolve --targets "<G1,G2,...>" --indications "<...>" --out $RUN/data`
Resolves symbol → Ensembl ID (Open Targets `search`) and indication → EFO/MONDO; caches IDs.
**Flag ambiguous/deprecated symbols instead of guessing.** Human targets only.

### Step 2 — Pull structured evidence (per axis, per target)
- **Drug-MoA + mouse + genetics-association + safety → Open Targets:**
  `python scripts/pull_opentargets.py --targets "<...>" --indications "<...>" --out $RUN/data`
  (uses the **verified v2026.06** field names in `references/opentargets_queries.md`, including
  the schema-drift fixes: `drugAndClinicalCandidates` not `knownDrugs`; `maximumClinicalStage`
  not `isApproved`; MoA via `drug.mechanismsOfAction.rows{mechanismOfAction actionType}`).
- **Functional/CRISPR → DepMap:**
  `python scripts/pull_depmap.py --targets "<...>" --out $RUN/data`
  Reads only the target rows/columns from the DepMap CRISPR gene-effect and
  gene-dependency matrices—**never the full ~400 MB matrix into memory**. Applies
  the correct score-sign convention.
- **Human genetics (depth) → GeneBass / GWAS Catalog / gnomAD:** summarize pLoF-burden
  direction, GWAS direction of effect, and LoF-intolerance context into
  `$RUN/data/genetics_summary.csv`. Defer method specifics to `gwas-to-function-twas` /
  `genetic-variant-annotation` conventions.

### Step 3 — Directional literature (LiteratureSearch-first)
`python scripts/plan_queries.py --targets "<...>" --indications "<...>" --axes "<...>" --out $RUN/queries.json`
then run the Biomni **`LiteratureSearch`** tool for each planned query (one set **per axis per
target**; it writes structured records to `/mnt/results/execution_trace/references.jsonl`).
**Do NOT over-restrict `year_min`** — foundational LoF/knockout papers are often 2005–2015 and
must not be filtered out. `WebSearch`/`WebFetch` only to read the full text of an
*already-identified* pivotal paper for exact numbers, never as primary discovery.

### Step 4 — Build evidence matrix + consensus calls
`python scripts/build_evidence_matrix.py --run $RUN`
For each target × axis: raw readout → vote via the fixed rule above → reconcile to a per-target
consensus + confidence tier + strict flags. Writes `data/evidence_matrix.csv` and
`data/consensus_calls.csv`. Review the auto-derived votes and **correct any that the raw
readout does not actually support** before proceeding (the script is a scaffold, not an oracle;
allele-specific gain-of-function cases in particular need human judgment).

### Step 5 — CITATION-VERIFICATION GATE (mandatory, blocking)
`python scripts/verify_citations.py --run $RUN --refs /mnt/results/execution_trace/references.jsonl --transcript /mnt/results/execution_trace/transcript.jsonl`
Confirms **every** quantitative value AND **every** citation field (title, authors, year,
journal, DOI/NCT/accession) against the retrieved records, and re-checks against
`transcript.jsonl` (essential after a session is compacted — a correct DOI paired with an
invented title is the classic post-compaction failure). **Also confirms every `[n]` index
cited in `synthesis.json` / CSVs has a corresponding entry in `references.json`** — an index
that resolves in `references.jsonl` but is missing from `references.json` renders an
unresolvable `[n]` in the PDF body and is flagged as `failed`. Emits
`data/citation_verification.json` with `doi_layer_status` ∈ {clean, partial, failed} and
**exits non-zero unless clean/empty** → do not build the report until resolved. **Any claim
that cannot be verified is dropped or explicitly flagged — never guessed.** Titles copied
verbatim.

### Step 6 — Figures (data-driven) + infographic (GenerateImage)
`Rscript scripts/make_figures.R --run $RUN` (Python fallback:
`python scripts/make_figures.py --run $RUN`). Produces the **evidence-matrix heatmap**
(targets × axes; color = vote) and the **consensus summary bar** (bar length = # concordant
informative axes; color = confidence tier), as `.png`+`.svg` with a `fig_manifest.csv`.
Then build the **one-page conceptual infographic with the `GenerateImage` tool** (the
direction question, the targets, each verdict + confidence, the axis set) — **do NOT hand-draw
it in matplotlib/ggplot**; a prompt template is in `references/reporting_notes.md`.
**After each figure, run `Read` with `mode="media_output_check"`; regenerate on failure.**
Style: colorblind-safe palette, Liberation Sans, PNG + SVG.

### Step 7 — Inventory Biomni resources
Use the bundled resource catalog, direct package imports, `hpc_search_tools`, and
`Skill` to identify databases, packages, HPC tools, and **sibling skills** relevant
to the targets. Produce a short "relevant Biomni resources & how to run the
follow-ups" mapping that cross-references the skills that actually *execute* each axis:
`open-targets` (genetics/MoA), `gene-essentiality` (DepMap), `gwas-to-function-twas` /
`genetic-variant-annotation` (human genetics), `clinicaltrials-landscape` /
`literature-preclinical` (clinical/preclinical depth). Seed/fallback:
`references/biomni_resources_catalog.md`.

### Step 8 — Assemble the PDF (via `pdf-report-generation`)
Load the **`pdf-report-generation`** skill for its Phylo brand system, then author two content
artifacts:
- `$RUN/synthesis.json` — **ALL** narrative text (title, subtitle, executive_summary,
  direction_rule_table, methods, results_intro, per_target_sections [each may name a
  `figure`], discussion incl. **how conflicts were handled**, limitations, next_steps,
  callouts). Schema in `references/schemas.md`.
- `$RUN/references.json` — ordered `[{n, text}]`, verbatim verified references.

**Before building the PDF, enforce two consistency rules:**
1. **`synthesis.json` `doi_layer_status` must equal the value in
   `data/citation_verification.json` exactly** (the gate is ground truth). Do not self-report
   `clean` when the gate says `partial` or `failed`. `build_report.py` reads the gate status
   directly for the Methods section, so a mismatch will surface as an internal contradiction
   in the report.
2. **Deduplicate `references.json`** — one entry per paper. If two `[n]` markers resolve to the
   same DOI/title, merge them into a single reference and update every `cites` field in
   `evidence_matrix.csv` and `consensus_calls.csv` to point at the surviving index. Duplicate
   entries for the same paper are the most common source of DOI-title mismatches (one copy
   carries the wrong DOI).

Then build (layout only):
`python scripts/build_report.py --run $RUN --out /mnt/results/report_<slug>.pdf --infographic $RUN/figures/<infographic>.png`

**`build_report.py` is a layout engine, not a content author** — every sentence in
`synthesis.json` must already be source-bound and past the Step 5 gate. It also enforces a
pre-build assertion that every `[n]` in `synthesis.json` has a matching `references.json`
entry, failing loudly on orphan citations so they are never rendered as unresolvable markers
in the PDF. Sections in order:
title → **infographic** → executive summary → introduction → methods (sources +
direction-mapping rule + verification protocol + `doi_layer_status`) → results (evidence
matrix table + heatmap + consensus table + per-target findings) → discussion
(regime-/allele-conditional, honest about genuine disagreements) → limitations → **next
steps** → relevant-Biomni-resources → references (verbatim, verified). Validate with `pypdf`
(page count, extractable text) **and** a `Read media_output_check`; fix and re-check on any
failure. (Optional: emit per-target one-pagers when N_targets ≥ 6.)

---

## Scientific Caveats & Integrity Rules

- **Direction is indication-specific.** Always tie the call to a named disease/context; the
  same gene can be INHIBIT for one indication and ACTIVATE for another.
- **A silent knockout is not evidence for ACTIVATE.** Reconcile gain-of-function biology
  explicitly (knockdown of a toxic allele = INHIBIT).
- **DepMap scores are inverted** (negative = essential). Never mis-sign; broad essentiality is
  a toxicity caveat, not an ACTIVATE vote.
- **Safety ≠ direction.** On-target safety signals are flags, not reversals.
- **No unconditional call under conflict.** If axes genuinely disagree, report CONTESTED with
  both sides cited.
- **Citation honesty is non-negotiable** (Step 5). Report the real `doi_layer_status`; copy
  titles verbatim; prefer dropping a claim over guessing.
- **Human targets only** for the structured sources; flag non-human requests.

## Failure Modes to Avoid

- Reading a silent germline KO as opposing evidence (mis-handling gain-of-function alleles).
- Mis-signing DepMap essentiality (forgetting negative = essential).
- Recency-biased retrieval that buries foundational LoF/KO papers (mitigate in Step 3).
- Hallucinated/paraphrased citation fields after compaction (mitigate in Step 5 + transcript
  re-check).
- Loading full DepMap matrices into memory (read only target rows/columns).
- Letting `build_report.py` author science, or shipping auto-derived votes without human review.
- Manufacturing a consensus when axes truly conflict (report CONTESTED instead).

## Environment Notes

- Python: `reportlab`, `pypdf`, `pandas`, `numpy`, `requests` (all preinstalled). R:
  `ggplot2`, `ggprism`, `dplyr`, `tidyr`, `RColorBrewer` (preinstalled).
- Compute is trivial (API calls + light plotting + one GenerateImage + PDF). Runs on the
  **default sandbox**; no GPU/HPC. DepMap CRISPR CSVs are ~400 MB each — read only the target
  rows/columns (usecols / chunked filter), never the whole matrix.
- R `file.copy()` to `/mnt/results` yields 0-byte files — write figures directly to
  `/mnt/results/...` or stage in `/workspace` then shell `cp`.
- Open Targets data is CC0; cite the release (`meta { dataVersion { year month } }`).

## External Data Sources & Licenses

This skill synthesizes **third-party public data**; it ships no proprietary data of its own.
Users (including commercial users) must honor each source's license when running the skill or
redistributing its outputs. **`DATA_SOURCES.md` in this skill folder is the authoritative,
per-source table** (which source feeds which axis, license, commercial-use status, and
attribution/share-alike obligations). Summary:

- **Default-run sources are CC0 / CC BY** (light attribution footprint): **Open Targets** (CC0;
  cite the release), **DepMap** CRISPR (CC BY 4.0; attribute Broad + release), **gnomAD** (CC0),
  **GWAS Catalog** summary statistics (CC0; cite the primary study), **GeneBass** (open for
  research; UK Biobank-derived — heed UKB terms), and **Open Targets `mousePhenotypes`** for the
  mouse-KO axis. Directional evidence for every axis comes via the Biomni **`LiteratureSearch`**
  tool — cite the underlying **primary papers** (DOIs), not the aggregator.
- **Optional / extension sources include two share-alike (copyleft) resources that permit
  commercial use but require attribution + share-alike:**
  - **ChEMBL — CC BY-SA 3.0 Unported.** Commercial use allowed; **must attribute** (ChEMBL
    resource URL + release version + current ChEMBL paper, and preserve ChEMBL IDs) **and**
    redistribute any *adaptation of the data* under a compatible share-alike license. Only
    relevant if the optional ChEMBL/OpenFDA drug-depth layer is enabled.
  - **Human Protein Atlas (HPA) — CC BY-SA (share-alike), with a version nuance.** Commercial
    use allowed with **proper citation** (a primary HPA publication **and** a link to
    proteinatlas.org shown alongside the content). Historical portal versions (~v19–v22) are
    **CC BY-SA 3.0** (share-alike); the current license page states **CC BY 4.0**. Attribute per
    the **specific version** used and apply share-alike for CC BY-SA versions. HPA is an
    optional contextual source (surfaceome / tissue expression), not part of the default run.
- Other optional sources — **OpenFDA** (US-Gov public domain), **GTEx** (open-access summary
  data; individual genotypes controlled-access via dbGaP), **MouseMine/MGI** (CC BY 4.0),
  **MSigDB** (CC BY 4.0 core; KEGG/BioCarta-derived gene sets carry stricter, partly share-alike
  terms), **PrimeKG** and the **Broad Drug Repurposing Hub** (aggregate sources under their own
  licenses) — are documented in `DATA_SOURCES.md`.

**Share-alike in practice:** merely *citing* a value in the report is normal scholarly
attribution and does not relicense your report; the share-alike obligation triggers only when you
redistribute an *adapted copy of the data* from a CC BY-SA source (ChEMBL, a CC BY-SA HPA
version, or KEGG-derived MSigDB sets). Licenses change between releases — **verify the source's
own license page and cite the exact release you used.**
