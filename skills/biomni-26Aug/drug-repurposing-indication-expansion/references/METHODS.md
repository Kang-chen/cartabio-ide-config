# Methods reference: connectivity-based drug repurposing / indication expansion

This document is the authoritative description of the scoring methodology and the
`report_config` schema. It is intended for the agent (and any reviewer) to understand
exactly what each script computes and how the pieces fit together. It is disease-agnostic.

---

## 1. Rationale

**Connectivity mapping** represents a disease and a drug each as a transcriptomic
signature (sets of up- and down-regulated genes). A drug whose perturbation signature is
*anti-correlated* with the disease signature — i.e. it pushes the transcriptome back
toward the healthy state — is nominated as a therapeutic candidate. A drug whose signature
is *correlated* with the disease signature *mimics* the disease (useful as a negative
control; e.g. a known disease-inducing agent should score as a mimic).

This is the Connectivity Map / LINCS L1000 paradigm, adapted to the **gene-set (ranked
up/down list)** form of the data available in the environment (not the continuous z-score
matrix). The scoring statistic is therefore **set-overlap based**, not cosine/XSum on
z-scores.

---

## 2. Inputs and harmonization

### 2.1 Disease signature (`resolve_inputs.py`)
Three acceptable input modes (dual/triple contract — this is the core of generalizability):

- **Built-in** — match a disease *name* to one of the ~333 `<disease>-up` / `<disease>-dn`
  entries in `disease_signatures-v1.0.gmt`. `match_disease()` fuzzy-ranks candidates; the
  agent should confirm the chosen disease with the user when the top match is ambiguous.
  `load_builtin_disease(base_name)` returns `(up_genes, dn_genes)`.
- **Explicit gene lists** — the user supplies `up=[...]`, `dn=[...]` symbols directly.
- **DE results table** — `signature_from_de_table(path, ...)` derives up/dn from a
  differential-expression table (auto-detects gene / log2FC / adj-p columns; thresholds
  `padj<0.05` & `|log2FC|>=1` by default, or top-N by |log2FC|).

The disease signature is always treated as **human gene symbols** (uppercased). If a user
supplies a mouse signature, map it to human first (same ortholog map as below).

### 2.2 Perturbation library
- **Default (drug screen):** `single_drug_perturbations-v1.0.gmt` (271 drug up/dn signatures).
- **Optional (target nomination):** `single_gene_perturbations-v1.0.gmt` via
  `gene_perturbation_mode.py`.

### 2.3 Gene-space harmonization (`harmonize_signatures.py`)
- Split each library entry `<name>-up` / `<name>-dn` into a base name + direction
  (`split_updn_library`, regex `[-_](up|dn|down)$`).
- Detect organism per signature (`classify_organism` / `_mouse_style_strict`): a symbol is
  "mouse-style" if length>=2, first char uppercase, and the remainder has lowercase and no
  uppercase (e.g. `Col1a1`); a signature is mouse if it has more mouse-style than
  all-uppercase symbols.
- Map mouse -> human orthologs (`build_orthologs.load_ortholog_map`): MGI homology table,
  grouped by `DB Class Key`, keyed by UPPER mouse symbol -> set of UPPER human symbols
  (~20,181 mouse symbols). Human signatures are simply uppercased. Unmapped mouse symbols
  fall back to uppercasing (conserved-symbol heuristic).
- After mapping, genes that end up in *both* up and dn (ambiguous) are removed from both.
- Drop signatures with `< min_set_genes` (default 5) total mapped genes.
- Restrict everything to a common **background** = union of all genes appearing in the
  disease signature and all retained perturbation signatures (defines `N` for the
  hypergeometric correction; ~15,229 for the IPF worked example).

---

## 3. Connectivity scoring (`connectivity_score.py`)

### 3.1 Size-corrected overlap
Every set overlap is standardized against its hypergeometric expectation so that large
gene sets do not dominate:

```
hyper_z(overlap, sizeA, sizeB, N):
    if N<=1 or sizeA==0 or sizeB==0: return 0.0
    mean = sizeA*sizeB/N
    var  = mean * (1 - sizeA/N) * (1 - sizeB/N) * (N/(N-1))
    if var<=0: return 0.0
    return (overlap - mean) / sqrt(var)
```

### 3.2 Reversal score
```
z_reversal = hyper_z(|disease_up ∩ drug_dn|) + hyper_z(|disease_dn ∩ drug_up|)
z_mimic    = hyper_z(|disease_up ∩ drug_up|) + hyper_z(|disease_dn ∩ drug_dn|)
S_reversal = z_reversal - z_mimic          # POSITIVE => reversal (therapeutic hypothesis)
```

### 3.3 Permutation null and FDR
For each drug, a null distribution of `S_reversal` is built by drawing overlaps from the
hypergeometric distribution (`rng.hypergeometric(K, N-K, n, size=nperm)`, seed=42,
nperm=10,000) and recomputing `z_reversal - z_mimic`:
- `p_reversal = (#{null >= observed} + 1) / (nperm + 1)`
- `p_mimic    = (#{null <= observed} + 1) / (nperm + 1)`
- `S_reversal_z = (observed - null_mean) / null_sd`
- `fdr_reversal` = BH-FDR on `p_reversal` across all drugs via `statsmodels ... multipletests(method="fdr_bh")`.
- `fdr_mimic` = BH-FDR on `p_mimic` across all drugs (same method). This lets `check_controls`
  distinguish a **significant mimic** (e.g. a disease-inducing control) from a null result —
  without it, a drug scored as a mimic shows `fdr_reversal = 1.0` and there is no statistic
  that says "this is a SIGNIFICANT mimic".

The permutation floor is `1/(nperm+1)`; the significant count can differ by ±1 from a
prior run only through this discretization, not through non-determinism (scores are exact).

### 3.4 Independent enrichment cross-check (`enrichment_crosscheck.py`)
A Kolmogorov–Smirnov enrichment score of each disease gene set within the drug's ranked
signature (`ordered = list(drug_up) + list(drug_dn)`):
```
enrichment_score(ordered, gene_set):   # standard KS; ES>0 when the set sits at the TOP
    ... running max of (hits/n - j/t) [b] and (j/t - hits/n) [a] ...
    return b if b > a else -a
reversal_enrich = es(disease_dn) - es(disease_up)
```
Consensus rank = mean of `rank(S_reversal, desc)` and `rank(reversal_enrich, desc)`. The
Spearman ρ between the two scores (≈0.87 in the IPF example) is a robustness indicator and
is reported.

**Canonical ranking (single source of truth).** The float `consensus_rank` is converted to an
integer **`canonical_rank`** (1 = best) by `assign_canonical_rank`: sort on `consensus_rank`
(asc) with a deterministic tie-break `S_reversal` (desc) → `fdr_reversal` (asc) → drug name
(asc). Every downstream artifact — `all_drugs_ranked.csv`, `approved_repurposing_candidates.csv`,
all four figures, the `candidate_slate`, Table 1, and every ranked mention in the report — is
ordered by `canonical_rank` with **no re-sorting by any other key**. `candidate_slate(...,
ensure_ranks=(1,))` additionally force-includes the canonical #1 so the top hit is always
rationalized, and `build_report.build` requires a `top_hit_rationale` for it.

---

## 4. Annotation and validation

### 4.1 Broad Repurposing Hub annotation (`annotate_hub.py`)
Match each scored perturbation to the Hub by a normalized name (`norm`: lowercase, strip
parentheticals, collapse non-alphanumerics), with **salt-aware** recovery (`hub_base`
strips a trailing salt token such as `propionate`, `bromide`) and a **false-positive
guard** (does not match a base name to an unrelated longer drug). Adds `clinical_phase`,
`moa`, `target`, `disease_area`, `indication`, `smiles`, etc. `clean_drug` removes L1000
time annotations like `(30 h)` so time-course replicates collapse to one drug. **Approved
= clinical_phase == "Launched".**

### 4.2 Controls (`controls_and_moa.check_controls`)
The single strongest internal validity check. The agent supplies drugs *expected* to
reverse (standard-of-care, mechanistically rational) and drugs *expected* to mimic (a
known disease-inducing agent). The function reports each control's score, direction, and
whether it matches expectation — and **honestly reports absence** when a control is not in
the library. (IPF worked example: bleomycin, the canonical fibrosis inducer, correctly
scores as a top disease-mimic.)

The verdict is **significance-aware and three-valued** — a sign alone is never enough:
- `matches_expectation = 'yes'` — direction matches expectation AND that direction is
  significant (the relevant FDR < `fdr_thresh`, default 0.05).
- `matches_expectation = 'no (significant opposite)'` — the opposite direction is
  significant (e.g. a control expected as a reverser is a significant mimic).
- `matches_expectation = 'inconclusive (n.s.)'` — neither direction is significant.

Output columns include `control, expected, present, S_reversal, p_reversal, p_mimic,
fdr_reversal, fdr_mimic, significant, direction, matches_expectation`. The `significant`
boolean is true when at least one FDR < `fdr_thresh`. 'yes' is unreachable from a sign
alone — a near-zero score at FDR ~1.0 becomes 'inconclusive (n.s.)'.

`controls_verdict(controls_df, fdr_thresh=0.05) -> dict` summarises the panel:
- `status = 'fail'` — any control is 'no (significant opposite)', OR zero present controls
  are 'yes'.
- `status = 'weak'` — any inconclusive AND fewer than half of the present controls are 'yes'.
- `status = 'pass'` — otherwise.
- `failures` — list of strings naming each offending control with its `S_reversal` and FDR,
  suitable for pasting into a report banner.

### 4.3 MOA over-representation (`controls_and_moa.moa_enrichment`)
Fisher's exact test (BH-FDR) for each MOA term: enriched among significant approved
reversers vs the approved-with-signature background. With small hit counts these are
usually nominal-only; report honestly.

### 4.4 Optional modes
- `gene_perturbation_mode.py` — target nomination (knockdown that reverses -> INHIBIT;
  overexpression that reverses -> ACTIVATE).
- `admet_mode.py` — RDKit physicochemical/drug-likeness descriptors on Hub SMILES (Lipinski/Veber).
- `trials_check.py` — ClinicalTrials.gov v2 API: is a candidate already trialled for the
  indication (external validation, lower novelty) or novel? The API query uses both
  `query.intr` (drug) and `query.cond` (disease). Two counts are returned:
  - `n_trials_query_total` — the loose full-text total (any study whose text mentions both
    strings). This is NOT the count to quote in the report.
  - `n_trials_matched` — the **verified** count: studies where the drug actually appears in
    the intervention names (case-insensitive substring of the drug's first whitespace-delimited
    token, salt-stripped). The report must quote `n_trials_matched`, never
    `n_trials_query_total`.
  - `verified_nct_ids` — the NCT IDs of the intervention-verified studies.
  - `query_condition` — the exact disease string sent as `query.cond`. This MUST equal the
    report's `disease_label` — a familial-hypercholesterolemia report must not silently query
    'hypercholesterolemia'.
  - `truncated` — True when the page hit `max_studies`.

---

## 5. Literature grounding (`literature_evidence.py`)

The **agent** runs the Biomni `LiteratureSearch` tool (never shelled out) once per top
candidate, using `build_query(drug, disease, moa, mode)`. Results are structured into an
evidence table via `assemble_evidence_table(rows)`; the structured records land in
`references.jsonl`. **All narrative claims in the report must be grounded in these results
— never invented.** `candidate_slate(annotated_df, k)` selects the top-k approved reversers
to check.

`references_from_records(records, drugs=None) -> list[str]` formats numbered reference
strings directly from the LiteratureSearch records (authors, title, journal, year) and
**ALWAYS appends a verifiable locator** — 'PMID: <pmid>' / 'doi:<doi>' / the URL — skipping
records with none. Bibliographic detail then comes from the retrieved record instead of the
model's memory. Every reference in the report must carry a PMID/DOI/URL locator; `build()`
raises `ValueError` for any reference that does not. A hand-typed citation with
volume/issue/pages and no identifier cannot be checked in one click by a reviewer.

---

## 6. Report (`build_report.py`) — `report_config` schema

PDF layout, brand constants and figure-style conventions are owned by the
`pdf-report-generation` skill; `assets/report_style.py` **loads the palette and typography
from that skill at runtime** (parsing its SKILL.md; no brand colours/fonts are declared in
this package) and `build_report.py` only supplies the scientific content and the data-driven
gates. Do not re-derive brand rules here. The report-layer helpers fail loudly if that skill
cannot be found — they never silently fall back to an unbranded palette.

The report **structure, methods prose, figures, tables, and styling are fixed and
data-driven**; every disease-specific sentence is supplied by the agent in `report_config`,
grounded in the analysis + literature. Keys (all strings unless noted; ReportLab inline
HTML markup allowed — use `<b>`, `<i>`, `<sub>`, `<super>`, `&#961;`, NOT unicode):

| key | type | content |
|---|---|---|
| `title`, `subtitle` | str | report title / method subtitle |
| `disease_label` | str | short disease name (running header + captions) |
| `signature_provenance` | str | one clause describing where the signature came from |
| `executive_summary` | list[str] | 1–3 paragraphs; agent fills numbers from `stats` |
| `key_finding_title`, `key_finding_body` | str | opening callout: finding + honest caveat |
| `top_hit_rationale` | str (**required**) | rationale for the canonical #1-ranked hit; `build()` raises if missing/empty. Honest "likely non-specific / assay artifact" is valid |
| `top_hit_title` | str (optional) | heading for the top-hit callout (default "Top-ranked candidate") |
| `controls_failure_acknowledgement` | str (**required when controls verdict is fail/weak**) | acknowledgement that the positive-control panel did not validate; `build()` raises if missing/empty when the verdict is 'fail' or 'weak'. The verdict is recomputed inside `build()` from `tables['controls']` — never read from a status the agent typed |
| `compound_flags` | list[{name, classification, note}] (optional) | **single source of truth** for compound mechanistic credibility; `classification` in {`artifact`, `caution`, `credible`}. Drives the body "Flagged / cautioned compounds" table AND the page-1 caption/headline. The front-matter consistency gate raises if a flagged (artifact/caution) compound is named unflagged in the front matter |
| `introduction` | list[str] | disease biology + repurposing rationale |
| `results_intro` / `results_top` / `results_moa` / `results_controls` | list[str] | per-subsection prose |
| `discussion` | list[str] | interpretation, including boundaries of the method |
| `limitations` | list[(title, body)] | bullet limitations |
| `conclusions` | list[str] | conclusions |
| `bottom_line_body` | str | closing callout |
| `references` | list[str] | numbered reference strings; each MUST carry a PMID/DOI/URL locator or `build()` raises. Use `literature_evidence.references_from_records()` |
| `marker_note` | str (optional) | caption addendum for the signature figure |
| `infographic_caption` | str (optional) | **PREFIX only** for the infographic caption; the factual sentence is derived by `build_report` from the approved DataFrame via `make_infographic.infographic_caption_from_data` |

`stats` dict keys: `n_up, n_dn, n_drugs, n_human, n_mouse, n_approved, n_appr_sig, bg,
mouse_map_median, rho`.

`tables` dict: `approved` (DataFrame with `rank, drug, S_reversal, fdr_reversal, moa`),
optional `literature` (DataFrame with `drug, direction, evidence, clinical_status`),
optional `controls` (DataFrame from `controls_and_moa.check_controls` — when present,
`build()` recomputes the verdict via `controls_and_moa.controls_verdict` and gates on it).

`figures` dict: optional `infographic` + `fig1..fig4` PNG paths (from `make_figures.py` /
GenerateImage).

### 6.1 Infographic
The opening infographic is a **conceptual/schematic** figure and MUST be produced with the
**GenerateImage** tool (not matplotlib). `make_infographic.build_infographic_prompt()`
builds a prompt that carries NO data-bearing content — no drug names, no gene symbols, no
ranks, no heatmaps (a hard NEGATIVE-CONSTRAINT clause forbids them). The `top_drugs`
parameter is accepted for call-site compatibility but ignored. The agent calls GenerateImage
and saves `figures/infographic.png`. Reject and regenerate if the image contains ANY drug
name, gene symbol, rank number, numbered compound list, heatmap or colour-scale legend.

The factual caption is **derived** from the approved DataFrame by
`make_infographic.infographic_caption_from_data(stats, approved_df, verdict=..., compound_flags=...)`
— it reads `canonical_rank` and `drug` directly off the frame and states the real top
compounds and counts. It is **verdict- and flag-aware**: when the controls verdict is
fail/weak it leads with the failure and frames the top compounds as exploratory (never
"recommendations"), and any compound in `compound_flags` is annotated with its flag.
`build_report` composes this UNCONDITIONALLY (the agent's `infographic_caption` is a PREFIX
only), so the deliverable cannot silently assert a ranking that disagrees with
`all_drugs_ranked.csv` or that contradicts the validation verdict / compound flags.

### 6.2 Post-build QC (`validate_report.py`)
After building, `validate(pdf_path, stats, tables, top_hit_name=..., trials=...,
disease_label=..., n_references=..., controls=..., verdict=..., compound_flags=...)`
reconciles the PDF's numbers against the analysis outputs (headline stats present verbatim,
top drugs named, references present, no contradictory "significant" counts) and, when
`controls`/`verdict` and `compound_flags` are supplied, re-checks the verdict/flag
consistency at warn level (mirrors the hard build-time gate). Review warnings before
delivering.

### 6.3 Consistency gates
- **Controls gate (build-time):** when `tables['controls']` is present, `build()`
  recomputes `controls_verdict` and, if 'fail' or 'weak', requires
  `controls_failure_acknowledgement` and renders a verdict-led banner as the FIRST element on
  page 1 (before any candidate is named). A slate from a failed control panel cannot read as
  confident.
- **Front-matter consistency gate (build-time):** the page-1 headline and the derived caption
  are produced FROM the recomputed verdict and from `compound_flags`, so the first thing a
  reader sees is derived from the validation verdict rather than written independently of it.
  `build()` raises `ValueError` before export if (i) the verdict is 'fail' and neither
  `executive_summary` nor `key_finding` states the failure (page 1 must lead with the verdict,
  not present a ranked list as recommendations), or (ii) any flagged (artifact/caution)
  compound is named unflagged anywhere in the front matter. Each front-matter field is checked
  independently so a flag word in one field cannot mask an unflagged mention in another.
- **Reference-locator gate (build-time):** every entry in `report_config['references']`
  must contain a verifiable locator token (PMID / PMCID / doi.org/ / DOI: / NCT / http).
  `build()` raises `ValueError` naming the offending indices otherwise.
- **Trials-consistency check (validate-time):** when `trials` and `disease_label` are
  passed to `validate()`, every distinct `query_condition` in the trials frame must
  normalize to a string containing or contained by `disease_label`, and any integer quoted
  next to 'trial'/'trials' in the report text must match an `n_trials_matched` value, not
  an `n_trials_query_total` value.
- **Reference-orphan check (validate-time):** distinct `[n]` markers in the body must not
  exceed the reference-list length, and references lacking a locator token are counted.

---

## 7. Honest-interpretation requirements (non-negotiable)

Connectivity ranks are **hypothesis-generating**, not proof of efficacy. Every report must:
- Report the control validation result honestly (including failures/absences).
- State that transcriptomic reversal does not model tissue delivery, cell-type specificity,
  dose, or disease chronicity.
- Flag hits whose high score reflects a mechanism unlikely to help (worked example:
  corticosteroids top the IPF list but are ineffective/harmful in fibrosis) and hits that
  have already failed trials for the indication (imatinib in IPF).
- Never claim clinical efficacy from the score alone.
