---
id: "skill_b14b08ae68081bbdaa898cc98affac2a"
name: "drug-bioactivity-chembl"
description: "Use to profile a compound's potency, binding affinity, and target selectivity from ChEMBL, or rank compounds for one target. Accepts compound names, ChEMBL IDs, or SMILES; covers IC50/Ki/Kd assay curation, on-target vs off-target activity, selectivity windows, and target-family profiles."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Profile the potency and selectivity of imatinib against its targets from ChEMBL and write a PDF report."
---

# Drug Bioactivity & Selectivity Profiler (ChEMBL)

Turn a bare compound identifier into a quantitative, provenance-tracked potency
and **selectivity** profile from ChEMBL, plus a shareable Phylo-branded PDF
report. Compound-agnostic and target-family-agnostic: the compound is resolved
by name/ID/SMILES, and targets are **discovered from the data** — no hard-coded
compound or target lists.

## Scope

**Does:** resolve a compound in ChEMBL → mine IC50/Ki/Kd (± EC50/Potency)
bioactivity → curate with a documented "Standard" filter and reconciling
provenance → aggregate per-target potency as **median + range + IQR + geomean
with assay context** → auto-detect primary target(s) → compute **fold-selectivity**
over off-targets (incl. whole families) → report cell-line antiproliferation
separately → ground in literature → build a **PDF report** (infographic + intro,
methods, results, figures, conclusions, references, next steps).

**Does NOT:** run docking/QSAR/ML potency prediction; invent activity values not
in ChEMBL; merge cellular growth readouts into molecular-target potency; or treat
a single (n=1) datapoint as a robust potency/selectivity claim.

## When to use

Trigger whenever the user asks about a compound's **potency, IC50/Ki/Kd, binding
affinity, on-target vs off-target activity, selectivity window, SAR/bioactivity
data, or a ChEMBL profile** — e.g. "how selective is olaparib for PARP1/2?",
"pull imatinib's kinase potency and off-targets", "IC50 values for compound X",
"which known inhibitors of EGFR are most potent" (target-centric mode). Do not
require the word "ChEMBL".

## Inputs

- **Compound** (primary mode): a name, synonym, ChEMBL ID (`CHEMBLxxxx`), or
  SMILES. Optionally a user-specified target list to restrict to.
- **Target** (target-centric mode): a target ChEMBL ID, to rank many compounds.
- Optional: which measurement types to include (default `IC50,Ki,Kd,Kd(app)`;
  add `EC50,Potency` for functional potency, kept in a separate bucket).

## Outputs (all to `/mnt/results/`)

- `report_<compound>_chembl_potency.pdf` — the report (infographic + all sections).
- `<compound>_potency_aggregated.csv` — per-target median/IQR/range/geomean/n by type.
- `<compound>_potency_records_filtered.csv` — record-level curated data + flags.
- `<compound>_selectivity.csv` — fold-selectivity table.
- `<compound>_cellline_activity.csv` — cellular activity summary (when present).
- `figures/` — the figures as PNG + SVG.

## Environment notes

- ChEMBL is accessed via the **public REST API** (`https://www.ebi.ac.uk/chembl/api/data`).
  The `chembl_webresource_client` package is **not installed** — use direct REST
  (the helper scripts already do). See `references/chembl_api.md`.
- Preinstalled and used: `pandas`, `numpy`, `matplotlib` (+ `reportlab`/`pypdf`
  via the pdf skill). Optional enrichment: `predict_admet_properties`
  (`biomni.tool.pharmacology`) for predicted ADMET from the compound's SMILES.
- Compute is trivial (REST pull + pandas on ~1k–few-k rows); runs on the default
  machine. The only latency is the paginated pull + a short `LiteratureSearch`.

## Data sources and licensing

This skill mines external data. Record the sources and their licenses here, and
**attribute them in every derived report**. See `DATA_SOURCES.md` for the full
details and the required attribution/citation strings.

| Source | Used for | License | Commercial use | Obligations |
|--------|----------|---------|----------------|-------------|
| **ChEMBL** (EMBL-EBI) | All IC50/Ki/Kd (± EC50/Potency) bioactivity, molecule resolution, assay/target metadata | **CC BY-SA 3.0** (Creative Commons Attribution-Share Alike 3.0 Unported) | **Yes — permitted** | **Attribution required** + **share-alike**: derived/adapted datasets must be redistributed under the same CC BY-SA 3.0 terms |

- **ChEMBL is the only external *data* source** the current workflow queries
  (via the public REST API; no key/registration required). Per EMBL-EBI:
  *"The ChEMBL data is made available on a Creative Commons Attribution-Share
  Alike 3.0 Unported License."* Commercial use **is allowed**, but you must
  (a) **attribute** ChEMBL and (b) apply **share-alike** to any adapted/derived
  data you redistribute. Cite the ChEMBL release used and, where possible, the
  underlying primary-literature document IDs that ChEMBL abstracts.
- **Human Protein Atlas (HPA)** is **not used by this skill.** It is noted here
  only for cross-skill consistency: *if* a future variant of this skill were to
  pull HPA data (e.g. for target expression/localization context), HPA is
  likewise **CC BY-SA** — commercial use permitted with **attribution +
  share-alike**. Do not imply HPA provenance in reports unless HPA is actually
  queried.
- `predict_admet_properties` (`biomni.tool.pharmacology`) is a **model**, not a
  data source; its outputs are *predicted* and must be labeled as such (they are
  not ChEMBL measurements and carry no data-license obligation here).
- **Reporting rule:** every report this skill produces must include a data-source
  line attributing **ChEMBL (CC BY-SA 3.0)**. Because the redistributed CSVs are
  an adaptation of ChEMBL, they inherit the **share-alike** obligation — state
  this in the report's data/attribution note.

---

## Workflow

Use the helper library in `scripts/chembl_potency.py` (mining/curation/stats) and
`scripts/figures.py` (figures). Follow `references/chembl_api.md` and
`references/curation_and_stats.md` for the exact contracts. Run stages in
`ExecuteCode` so state persists.

### 1. Resolve the compound (and exclude analogues)
`resolve_compound(query, smiles=...)` → one molecule. **Print all candidates**
and confirm you picked the intended molecule; exclude close back-up analogues
(the olaparib run had to exclude AZD2461 `CHEMBL4098253`). Prefer exact name
match, else highest `max_phase`. Fail loudly if not found.

### 2. Pull bioactivity
`fetch_activities(molecule_chembl_id, activity_types=...)` (paginated). For
target-centric mode pass `target_chembl_id=` instead. Report how many raw
records were pulled and the breakdown by `standard_type`.

### 3. Build frame + classify assays
`build_frame(acts)` → tidy DataFrame (keeps assay-context columns; folds
Kd(app)→Kd with a flag). `classify_assays(df)` → `assay_class` ∈ {biochemical,
cellular_target_engagement, antiproliferation}. This separates isolated-enzyme
potency from whole-cell readouts.

### 4. Tier targets (DATA-DRIVEN) — with human review
`tier_targets(df)` assigns `tier` ∈ {primary, offtarget, cellular}. It
auto-detects primary target(s) via a support-weighted score. **You MUST**:
- print `primary_candidate_table(protein)` and inspect the top candidates;
- cross-check against the drug's known mechanism (`LiteratureSearch`);
- **override** with `tier_targets(df, primary_target_ids=[...])` if the auto-pick
  disagrees (ChEMBL often splits one biological target across several records —
  e.g. imatinib's ABL1/ABL2/Bcr-Abl). For a genuine multi-target drug, designate
  several primaries.
Record in Methods whether primaries were auto-detected or set manually.

### 5. Curate with the "Standard" filter + provenance
`standard_filter(df)` → `(clean, provenance)`. Keeps exact `=` nM measurements;
drops transcription errors and non-nM units; sets censored (`>`,`<`) values aside
as **bounds**; retains "Outside typical range" (flagged). **Verify the provenance
reconciles**: `raw == clean + txn_error + non_nM + censored`. Put the provenance
table in the report.

### 6. Aggregate potency
`aggregate(clean)` → per (target × type): n, median, IQR, min–max, geomean,
n_studies. Median + range is the headline. Do not average across measurement
types. (Target-centric: `aggregate(clean, group_col="molecule_chembl_id", label_col="molecule_pref_name")`.)

### 7. Selectivity
`selectivity(agg, primary_labels=[...])` → fold vs each primary (and the
primary-vs-primary ratio). Censored off-targets → "≥ Nx" bounds; n=1 targets
flagged provisional.

### 8. Sanity check
Run `sanity_flag(primary_median_nM, lo, hi)` with a literature-informed band and
a `LiteratureSearch` for the discovery/potency paper. If the primary median is
orders of magnitude off, suspect a units error or wrong primary assignment —
investigate before reporting.

### 9. Cellular activity (secondary, separate)
Summarize the `cellular` tier antiproliferation IC50s descriptively (e.g. by a
grouping you build from assay/cell-line text such as sensitivity subgroups).
Report in µM, clearly labeled as cellular — NOT target potency.

### 10. Literature grounding (Biomni `LiteratureSearch`)
Multi-query: (a) compound + mechanism/target, (b) discovery/potency paper,
(c) selectivity / off-target liabilities. Ground the Introduction and Conclusions
and cite with inline `[N]` **only from returned records** (they land in
`references.jsonl`). Verify any quoted number/claim against the record before
writing it. Never fabricate citations.

### 11. Optional structure-based enrichment
Pull `canonical_smiles` from the resolved molecule and optionally call
`predict_admet_properties([smiles])` for a **predicted** ADMET context box.
Label as predicted, not measured.

### 12. Figures (media-checked)
Use `scripts/figures.py`:
1. `fig_potency_landscape` — per-target measurement strip plot, median marked.
2. `fig_median_range_forest` — median + IQR + range per target (**core visual**).
3. `fig_selectivity` — fold-selectivity bar chart (log; censored as bounds).
4. `fig_data_composition` — counts by type / assay_type / tier / year.
5. `fig_cellular` — cellular activity by group (only if cell data exist).

**Display hygiene (keeps report figures legible — set per compound):**
- `fig_median_range_forest(..., top_n=14)` — caps the forest at the N most-potent
  targets (primary targets always kept) so a many-target drug stays readable and
  the figure embeds wide, not thin. Set `top_n=None` to show all. A legend
  (primary vs off-target, IQR, range) is drawn below the axes.
- `fig_selectivity(..., min_n=2, top_n=20, max_label_len=48, drop_uninformative=True)`
  — excludes single-point (`n<min_n`) off-targets whose fold is unstable, caps the
  bar count, wraps long target names onto two lines (no truncation), and drops
  non-descriptive ChEMBL names (e.g. "Unchecked", "Molecular identity unknown")
  from the headline figure only (they stay in the data tables).

After saving EACH figure, run `Read(path=..., mode="media_output_check")` and
regenerate if blank/clipped/unreadable. In tables, keep ChEMBL IDs and numeric
ranges (e.g. IQR `32.0-218.0`) in columns wide enough that they never wrap across
two lines — split IDs read as two different numbers.

### 13. Build the PDF (leverage `pdf-report-generation`)
**Load the `pdf-report-generation` skill and follow its ReportLab/Phylo
conventions** (gold `#D4A04A` accents, Helvetica, `<sub>`/`<super>` tags — never
Unicode subscripts, `hAlign="CENTER"` on every Image/Table/Drawing,
`KeepTogether` for figure+caption, direct write to `/mnt/results/`, pypdf
validation). Report structure (in order):

- **Infographic summary page** — a visual one-pager: compound name/ID + a headline
  callout (primary-target median potency), the selectivity-window number
  (nearest off-target fold and the family range), record/target counts, and small
  visual callout boxes. Build with ReportLab flowables (callout boxes + a compact
  bar/graphic), OR embed a compact composite figure. Keep it scannable.
- **1. Introduction** — compound MOA, target biology, why selectivity matters (lit-grounded).
- **2. Methods** — data source (ChEMBL + REST), compound ID + excluded analogues,
  target tiering (auto vs manual), the "Standard" filter, the provenance table,
  aggregation + stats, sanity check.
- **3. Results** — primary-target potency table + landscape figure; off-target
  selectivity table + forest + selectivity figures; data-composition figure;
  cellular activity (secondary) with its figure.
- **4. Conclusions** — the potency + selectivity story in plain terms.
- **References** — numbered, matching inline `[N]`; real records only.
- **Next steps** — concrete follow-ups (e.g. confirm ambiguous single-n
  off-targets, orthogonal assay for the nearest off-target, cellular validation
  in relevant genotypes, ADMET/PK follow-up).

Validate: `pypdf` page_count ≥ 2, size > 5 KB, extractable text, then a visual
`Read(mode="media_output_check")` on the PDF.

### 14. Save all deliverables
Write the CSVs and figures listed under **Outputs** to `/mnt/results/`, templated
on the compound name. Mention the PDF filename in your final message.

---

## Scientific caveats (read before reporting)

- **ChEMBL splits biological targets** across multiple target records; pure-median
  ranking can mislabel the primary. Always human-review the primary-target
  candidates (Step 4).
- **Median + range, not mean.** Multi-source affinity data are log-normal with
  outliers; the mean is misleading.
- **Keep censored values.** `>`/`<` records demonstrate *lack* of activity and are
  essential for an honest selectivity window (as lower bounds).
- **Never merge cellular and biochemical potency.** Cellular IC50s run ~100–1000×
  weaker for legitimate biological reasons.
- **n = 1 is provisional.** Flag single-measurement targets; do not build a
  selectivity claim on one point.
- **Provenance must reconcile.** If `raw ≠ clean + exclusions`, debug before reporting.
- **Cite only returned records.** No fabricated PMIDs/DOIs. Verify quoted numbers
  against the record.
- **Predicted ≠ measured.** ADMET predictions are model outputs; label them.

## Error handling

- Compound not found → report it; suggest a ChEMBL ID or SMILES; do not guess.
- Multiple candidate molecules → list them, pick the intended one, exclude analogues.
- No biochemical protein-target data (only cell-line data) → say so; report the
  cellular section only and skip selectivity.
- ChEMBL unreachable / transient errors → retry (helper backs off); if it stays
  down, report the failure rather than fabricating data.
- Primary median fails the sanity band → investigate units / target assignment;
  flag prominently rather than publishing a suspect number.

## Worked example (reference only)

The skill was distilled from an olaparib (`CHEMBL521686`) → PARP-superfamily
profile: 1108 raw IC50/Ki/Kd records → 256 biochemical protein-target → 230 clean
exact-nM after removing 1 transcription error + 19 non-nM + 6 censored; PARP1
median IC50 5.0 nM, PARP2 ~1.1 nM (dual PARP1/2, ~4.7× PARP2-preferring), with a
clean window to the rest of the family (PARP3 ~10×; tankyrases/mono-ART PARPs
100–3000×+ weaker). It was validated on a *different* compound (imatinib,
`CHEMBL941`, ~2677 records, kinase targets) to confirm it is compound-agnostic.
These numbers are illustrative context, not defaults — the skill computes
everything fresh for the requested compound.
