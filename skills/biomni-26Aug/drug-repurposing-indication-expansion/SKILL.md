---
id: "skill_9406c66f225cadbbb3cf0c85b95a7e5a"
name: "drug-repurposing-indication-expansion"
description: "Use to nominate approved or clinical drugs for a new disease indication by reversing a disease expression signature with LINCS L1000/Connectivity Map (CMap). Can start from a disease name, up/down genes, or DE table and emphasizes indication expansion, Repurposing Hub annotations, controls, and ranked clinically annotated candidates."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Find approved drugs that could be repurposed for <disease> by reversing its transcriptomic signature, validate the directionality with known controls, and generate a PDF report with the ranked candidates, mechanisms, and literature evidence."
---

# Drug Repurposing / Indication Expansion by Connectivity Signature Reversal

Nominate existing drugs for a new disease indication by **connectivity mapping**: score
every drug perturbation signature against a disease signature and rank drugs whose
signature **reverses** the disease transcriptome (pushes it toward healthy). This is the
Connectivity Map / LINCS L1000 paradigm, implemented on the **gene-set (up/down list)** form
of the data and made fully **disease-, drug-, and signature-agnostic**.

The engine is validated: on the built-in IPF signature it reproduces the reference results
to 4 decimals, correctly scores **bleomycin** (the canonical fibrosis inducer) as a top
disease-*mimic*, and surfaces mechanistically credible reversers. See
`references/worked_example_ipf.md`.

---

## Scope

**Does:** turn a disease signature (name / gene lists / DE table) into a permutation-tested,
FDR-corrected ranked list of drugs predicted to reverse it; annotate with clinical phase /
MOA / target / SMILES (Broad Repurposing Hub); validate with user-named positive/negative
controls + an independent enrichment cross-check; nominate targets (optional single-gene
mode); add ADMET descriptors and ClinicalTrials.gov novelty flags (optional); ground top
hits in the literature; produce a PDF report with an infographic.

**Does NOT:** prove clinical efficacy (results are hypothesis-generating), model tissue
delivery / dose / cell-type specificity, or use the continuous L1000 z-score matrix (a
set-based score is used because that is the data form available). Does not score drugs
absent from the LINCS perturbation library (e.g. the two approved IPF drugs have no
signature). The opening infographic is a schematic of the method, not a data figure — it
never carries ranks, gene names or scores.

---

## Inputs

One disease signature, via any of three modes (`scripts/resolve_inputs.py`):
1. **Disease name** — matched to one of ~333 built-in LINCS disease signatures
   (`match_disease` fuzzy-ranks; confirm the choice with the user if ambiguous).
2. **Explicit gene lists** — human symbols `up=[...]`, `dn=[...]`.
3. **DE results table** — CSV/TSV; up/dn derived from log2FC + adjusted p (auto-detected
   columns; `padj<0.05` & `|log2FC|>=1` default, or top-N).

Optional: a drug library override (default = 271 LINCS single-drug perturbations), lists of
expected-reverser and expected-mimic control drugs, and mode flags (target nomination,
ADMET, trials).

**Genome/identifier note:** the disease signature is treated as **human gene symbols**
(uppercased). Mouse drug signatures are mapped to human via the MGI ortholog table
(runtime download; `assets/HOM_MouseHumanSequence.rpt` offline fallback). Supply mouse
disease signatures pre-mapped to human.

---

## Outputs (save under `/mnt/results/<task>/`)

- `tables/all_drugs_ranked.csv` — every drug scored (S_reversal, FDR, enrichment, consensus
  rank) with a single authoritative integer **`canonical_rank`** that defines the one ordering
  used everywhere. Rows are written in `canonical_rank` order.
- `tables/approved_repurposing_candidates.csv` — approved (Launched) reversers, annotated, in
  `canonical_rank` order (same relative order as the full list).
- `tables/controls.csv` — positive/negative control check results: each control's score,
  direction, FDR (`fdr_reversal`, `fdr_mimic`), `significant` flag, and the three-valued
  `matches_expectation` verdict (requires significance, not just sign).
- `tables/literature_evidence.csv` — the literature evidence table for selected candidates
  and controls (drug, direction, evidence summary, clinical status).
- `figures/` — `fig1_score_distribution`, `fig2_top20_approved`, `fig3_signature_overview`,
  `fig4_moa_and_validation` (PNG+SVG) and `infographic.png`.
- `report_<disease>_drug_repurposing.pdf` — Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.
- Optional: `tables/target_nomination.csv`, `tables/admet.csv`, `tables/trials_check.csv`.

---

## Workflow

Run scripts from `scripts/` and use `assets/report_style.py`. **After Writing any new .py
in a persistent ExecuteCode kernel, call `importlib.invalidate_caches()` before importing
it** (directory listing is cached). Put both `scripts/` and `assets/` on `sys.path`.

Report styling is owned by the `pdf-report-generation` skill (see the terminal report step);
`build_report.py` supplies only the scientific content and the data-driven gates. The
Okabe–Ito dict in `report_style.py` is *figure* colouring for `make_figures.py`
(colorblind-safe data series), not report styling, and stays local.

### 0. Confirm resources (optional)
Use the LINCS and Broad Hub resources described in
`references/datalake_reference.md`.

### 1. Resolve the disease signature — `resolve_inputs.py`
Match a name (and confirm with the user), or parse the user's gene lists / DE table. Get
`(disease_up, disease_dn)` as human symbols. *Why:* a wrong or low-quality signature
invalidates everything downstream; the up/down split defines what "reversal" means.

### 2. Build the ortholog map — `build_orthologs.load_ortholog_map(workdir)`
MGI mouse->human map (~20,181 symbols). *Why:* ~40% of drug signatures are murine; without
mapping they cannot overlap a human disease signature.

### 3. Harmonize signatures — `harmonize_signatures.harmonize(...)`
Split library into up/dn, detect organism per signature, map mouse->human, drop ambiguous
genes, drop signatures with <5 genes, restrict to a common background N. Produces the
bundle `{disease_up, disease_dn, pert_sigs, BG, meta}`. *Why:* size-correction and fair
overlap require a shared gene universe.

### 4. Score connectivity — `connectivity_score.run(bundle_pickle, out_csv)`
Size-corrected reversal score `S_reversal = z_reversal − z_mimic`, 10,000-fold permutation
null (seed=42), BH-FDR. Positive = reversal. *Why:* hypergeometric size-correction stops
large gene sets dominating; the permutation null calibrates significance.

### 5. Enrichment cross-check + canonical ranking — `enrichment_crosscheck.run(conn_csv, bundle_pickle, out_csv)`
Independent KS enrichment score + consensus rank; reports Spearman ρ vs the overlap score.
*Why:* agreement of two different statistics (ρ≈0.87 for IPF) shows the ranking is robust,
not an artifact of one method.

This step also produces the **single canonical ranking of the whole drug list**: an integer
**`canonical_rank`** (1 = best) assigned by sorting on `consensus_rank` (asc) with a
deterministic tie-break `S_reversal` (desc) → `fdr_reversal` (asc) → drug name (asc)
(`assign_canonical_rank`). **`canonical_rank` is the ONE ordering that every downstream output
reads — tables, all four figures, the literature slate, and every number/name in the report —
and nothing re-sorts by another key.** This prevents the failure mode where different views
(raw `S_reversal` vs consensus vs a re-sorted figure) disagree about which drug is "#1". The
float `consensus_rank` is kept only as a diagnostic column.

### 6. Annotate with the Broad Hub — `annotate_hub.annotate(consensus_df, out_csv)`
Salt-aware name matching (+ false-positive guard) adds clinical_phase, MOA, target, SMILES;
`clean_drug` deduplicates L1000 time variants; **Approved = Launched**. *Why:* repurposing
prioritizes already-approved drugs and needs mechanism/target context.

### 7. Validate with controls + MOA — `controls_and_moa.py`
`check_controls(expected_reversers, expected_mimics)` — the **strongest internal validity
check**. It returns a three-valued `matches_expectation` that requires **SIGNIFICANCE, not
just sign**: 'yes' only when the direction matches AND that direction is significant
(FDR < 0.05); 'no (significant opposite)' when the opposite direction is significant;
'inconclusive (n.s.)' otherwise. A near-zero score at FDR ~1.0 is 'inconclusive', never
'yes'. The output also carries `p_reversal`, `p_mimic`, `fdr_reversal`, `fdr_mimic`, and a
boolean `significant` column. After checking, compute `controls_verdict(controls_df)` and
pass its DataFrame as `tables['controls']` to `build_report.build()`. When the verdict is
'fail' or 'weak', `build()` requires `report_config['controls_failure_acknowledgement']`
and prints a validation banner — so a slate from a failed control panel cannot read as
confident. `moa_enrichment(...)` — Fisher over-representation (usually nominal with small
counts; say so). *Why:* a method that cannot place a known disease-inducer as a mimic
should not be trusted, and a control marked 'yes' at FDR 0.99 is not validation.

### 8. Ground top hits in the literature — `literature_evidence.py` + **LiteratureSearch tool**
`candidate_slate(annotated_df, k, ensure_ranks=(1,))` returns the top-k approved reversers
**unioned with the canonical #1 hit** (via `ensure_ranks`), all ordered by `canonical_rank`.
Use `top_hit_row(annotated_df)` to get the canonical #1 explicitly. For each slate member,
build a query with `build_query(...)` and **run the Biomni `LiteratureSearch` tool** (do NOT
shell out); collect grounded summaries + refs, then `assemble_evidence_table(rows)`. Build
the report's reference list with `references_from_records(records)` — it formats authors,
title, journal, year from the structured records in `references.jsonl` and ALWAYS appends a
verifiable locator (PMID / DOI / URL), so bibliographic detail comes from the retrieved
record, not the model's memory. *Why:*
every narrative claim and reference in the report must be grounded, never invented.

**The canonical #1-ranked hit MUST be rationalized** — it is force-included in the slate even
when it is not approved, so the report never emits an unexplained top hit. When the #1 hit is a
non-therapeutic / promiscuous compound (e.g. a research chemical or a broad cytotoxic), an
honest **"likely non-specific / assay-artifact"** rationale is a valid and expected outcome;
say so plainly rather than inventing efficacy.

### 9. (Optional) extra evidence layers
- **Target nomination** — `gene_perturbation_mode.run_target_nomination(...)` (knockdown
  that reverses -> INHIBIT; overexpression that reverses -> ACTIVATE).
- **ADMET** — `admet_mode.compute_admet(annotated_df)` (RDKit Lipinski/Veber on Hub SMILES).
- **Trials novelty** — `trials_check.check_trials(top_drugs, disease)` (ClinicalTrials.gov
  v2 API: already-trialled vs novel). The `disease` string passed to `check_trials` MUST be
  the same string as `report_config['disease_label']` — a familial-hypercholesterolemia
  report must not query 'hypercholesterolemia'. The report must quote `n_trials_matched`
  (intervention-verified), never `n_trials_query_total` (loose full-text total).

### 10. Figures — `make_figures.make_all(...)`
Four data-driven figures (PNG+SVG). **Run the mandatory `Read` media-output-check on each
PNG**; regenerate if blank/clipped/unreadable.

### 11. Infographic — `make_infographic.build_infographic_prompt(...)` + **GenerateImage tool**
The opening infographic is a **conceptual/schematic** figure, so build the grounded prompt
and produce it with the **GenerateImage** tool (load via ToolSearch) — **never matplotlib**.
Save `figures/infographic.png` and media-check it. **Reject and regenerate if the image
contains ANY drug name, gene symbol, rank number, numbered compound list, heatmap or
colour-scale legend** — the infographic is schematic only and all factual content lives in
the derived caption (`infographic_caption_from_data` reads the approved DataFrame directly).
The derived caption is **verdict- and flag-aware**: when the controls verdict is fail/weak it
leads with the failure and frames the top compounds as exploratory (never "recommendations"),
and any compound listed in `report_config['compound_flags']` is annotated with its flag — so
page 1 can never contradict the validation verdict or the body's compound classifications.

### 12. Generate the PDF report — MANDATORY TERMINAL STEP
Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses. **The run is not complete until this step has produced the PDF.** *Why:* the report is the deliverable — it is what turns the ranked drug list, the controls verdict, and the literature grounding into something a reader can act on.

Assemble `report_config` (schema in `references/METHODS.md`; filled example in
`worked_example_ipf.md`) with disease-specific narrative **grounded in steps 7–9**, then call
`build_report.build(report_config, stats, tables, figures, out_pdf)` and write the PDF directly
to `/mnt/results/`. Pass the approved-candidate table **already in `canonical_rank` order** (do
not re-sort it); Table 1 and Figure 3 render in that order and print the `canonical_rank`
integer. `build()` enforces the data-driven gates that keep the report honest:

- **Verdict-led front matter.** `build()` recomputes the controls verdict from
  `tables['controls']` and renders a **verdict-led headline** as the first thing on page 1 — on
  fail/weak it states the panel did not validate before any candidate is named, and requires a
  non-empty `report_config['controls_failure_acknowledgement']` (raises `ValueError` otherwise).
- **No unexplained top hit.** `report_config` must include a non-empty `top_hit_rationale` (the
  grounded explanation of the canonical #1 hit from step 8) or `build()` raises `ValueError` —
  an honest "likely non-specific / assay artifact" rationale is valid.
- **Verifiable references.** Every reference must carry a PMID/DOI/URL locator (build them with
  `literature_evidence.references_from_records`) or `build()` raises `ValueError`.
- **Single source of truth for flags.** Provide `report_config['compound_flags']` —
  `list[{name, classification, note}]` with `classification` in `{artifact, caution, credible}`
  — which drives both the body "Flagged / cautioned compounds" table and the page-1 caption. A
  **consistency gate** raises `ValueError` before export if the verdict is 'fail' and the
  executive summary / key finding does not state it, or if any flagged (artifact/caution)
  compound is named unflagged anywhere in the front matter. *Why:* the first thing a reader sees
  can never contradict what the analysis concluded.

Then QC the PDF with `validate_report.validate(pdf_path, stats, tables, top_hit_name=...,
trials=..., disease_label=..., n_references=..., controls=..., compound_flags=...)`: it
reconciles the PDF's headline numbers against the analysis outputs, verifies the canonical #1
hit is **named and rationalized**, checks that the trials `query_condition` matches
`disease_label` and that quoted trial counts are `n_trials_matched` values (not loose
`n_trials_query_total` totals), and flags orphan citation markers, locator-free references, and
any flagged compound named without its flag (mirrors the hard build-time gate). Review warnings
before delivering. *Why:* an automated number-reconciliation pass catches drift between the
narrative and the tables before the report reaches the user.

---

## Scientific caveats (state these honestly in every report)

- **Hypothesis-generating, not proof.** Transcriptomic reversal ≠ clinical efficacy. Require
  experimental validation.
- **Report controls honestly**, including failures and absent controls. A correct
  negative-control (disease-inducer scores as a mimic) is the key evidence the direction is real.
- **High score ≠ good drug.** Flag hits whose score reflects an irrelevant mechanism
  (worked example: corticosteroids top the IPF list but are ineffective/harmful in fibrosis)
  and hits that already failed trials for the indication (imatinib in IPF). Record these in
  `report_config['compound_flags']` (`{name, classification, note}`; classification
  `artifact` / `caution` / `credible`) — the **single source of truth** that drives the body
  flag table AND the page-1 caption/headline, so a compound flagged in the body can never
  appear unflagged on page 1.
- **The canonical #1 hit may be non-approved or artifactual — but must still be explained.**
  Because ranking is over the whole library, the canonical #1 can be a research chemical or a
  promiscuous cytotoxic rather than a clinical candidate. Report it honestly (an
  "assay-artifact / non-specific" rationale is valid); the pipeline forces it into the
  literature slate and requires a rationale so it is never emitted unexplained. Prioritize
  approved/clinical agents for the actual repurposing shortlist.
- **Data limitations:** gene-set (not z-score) input; drug signatures from heterogeneous
  cell lines/doses; cross-species ortholog mapping noise; only drugs with a LINCS signature
  can be scored; "approved" coverage is partial.
- **Reproduction tolerance:** scores are deterministic (identical to 4 decimals); FDR-sig and
  approved counts may differ by ±1 vs a prior run (permutation floor; time-variant dedup).
- **Cross-signature stability:** when more than one signature is run for the same disease,
  report `enrichment_crosscheck.rank_agreement` and do not merge the shortlists — two
  signatures for the same disease can produce effectively unrelated rankings (Spearman rho
  as low as ~0.3), and a qualitative 'markedly different' description is not a substitute
  for the number.

---

## Files

```
SKILL.md
scripts/
  resolve_inputs.py            # disease-signature input contract (name / gene lists / DE table)
  build_orthologs.py           # MGI mouse->human map (runtime download + bundled fallback)
  harmonize_signatures.py      # split up/dn, organism detect, ortholog map, common background
  connectivity_score.py        # size-corrected reversal score + permutation null + FDR
  enrichment_crosscheck.py     # independent KS enrichment score + consensus rank
  annotate_hub.py              # Broad Repurposing Hub annotation (salt-aware matching)
  controls_and_moa.py          # positive/negative control checks + MOA over-representation
  literature_evidence.py       # LiteratureSearch query builder + evidence table (agent runs the tool)
  gene_perturbation_mode.py    # OPTIONAL: target nomination via gene-perturbation reversal
  admet_mode.py                # OPTIONAL: RDKit drug-likeness descriptors
  trials_check.py              # OPTIONAL: ClinicalTrials.gov novelty cross-check
  make_figures.py              # 4 data-driven report figures (PNG+SVG)
  make_infographic.py          # GenerateImage prompt builder for the opening infographic
  build_report.py              # config-driven PDF report
  validate_report.py           # post-build number reconciliation QC
assets/
  report_style.py              # ReportLab helpers; LOADS palette+typography from pdf-report-generation
assets/eval/
  test_report_fixes.py         # tests: styling source (#D4A04A/#D5CFC5) + front-matter consistency gate
  HOM_MouseHumanSequence.rpt   # bundled MGI ortholog fallback (~15 MB)
references/
  METHODS.md                   # authoritative methods + report_config schema
  datalake_reference.md        # resource contents and schemas
  worked_example_ipf.md        # validated IPF end-to-end numbers (repro smoke test)
```
