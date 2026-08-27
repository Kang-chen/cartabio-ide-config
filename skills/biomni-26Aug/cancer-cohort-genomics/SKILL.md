---
id: "skill_861901b9194211e7a334b4f005209808"
name: cancer-cohort-genomics
description: "Use to quantify mutation, amplification, deletion, or combined somatic alteration frequencies for genes across cancer types or cohorts. Covers cBioPortal, TCGA, MSK-IMPACT, pan-cancer comparisons, mutation hotspots, allele distributions, and matched cross-cohort analyses."
category: "genomics_genetics"
visibility: "public"
starting-prompt: "How often is my gene somatically altered (mutation/CNA) across cancer cohorts? Break it down by cancer type with a PDF report."
---

# Cancer Cohort Genomics: Alteration Frequency Across Cohorts

Generalized from a proven KRAS × TCGA/MSK-IMPACT workflow. Works for **any gene or
gene set** and **any cBioPortal cohorts**. The output is a per-cancer-type alteration
frequency analysis plus a Phylo-branded PDF report (infographic, intro, methods,
results, conclusions, references, next steps).

## Scope

**Does:** somatic **mutation** (non-silent) and **copy-number alteration**
(amplification + deep deletion) frequency for a gene/gene set across cBioPortal
cohorts, broken down by cancer type; cross-cohort comparison; mutation hotspots;
branded PDF report with infographic and literature-grounded intro.

**Does NOT:** structural variants/fusions, survival/outcome association,
mutual-exclusivity/co-occurrence testing, or re-calling variants from raw data. (These
are possible future extensions; say so if asked, don't silently attempt them.)

## Inputs

- **Gene(s)** — one or more HUGO symbols (e.g. `KRAS`, or `ERBB2 EGFR`). Required.
- **Cohorts** — optional. If omitted, auto-select TCGA PanCancer Atlas + a large
  MSK-IMPACT cohort. User may name studies or a keyword to override.
- **Cancer-type filter** — optional (restrict to specific types).
- **Report** — on by default (PDF). Can be turned off for data-only.
- **Literature grounding** — on by default (LiteratureSearch for intro/citations);
  skippable for speed.

## Outputs (to `/mnt/results/`)

- `report_<gene>_alterations.pdf` — main deliverable (infographic + full report).
- `tables/<gene>_frequency_by_cancertype.csv` — per-type frequencies, all cohorts.
- `tables/<gene>_hotspots_by_cancertype.csv` and `<gene>_alleles_by_cancertype.csv`
  (when the gene is hotspot-driven).
- `figures/` — the selected figures as SVG + PNG.

## Environment resources this skill uses

- **cBioPortal REST API** (`https://www.cbioportal.org/api`) via
  `scripts/cbioportal_client.py`.
- **`LiteratureSearch`** (Biomni tool) — gene/cancer biology + therapeutic context for
  the intro; cite only real records from `references.jsonl`.
- **`GenerateImage`** (Biomni tool) — the summary infographic.
- **`pdf-report-generation`** skill — Phylo-branded ReportLab report (load it and follow
  its brand palette, table styles, validation).
- Use relevant Biomni resources for optional complementary gene annotation.

Before running, load the two references in this skill folder:
`references/cbioportal_api.md` (endpoints) and `references/conventions.md`
(denominators, caveats, sanity table). The scripts under `scripts/` implement the
whole pipeline; import them rather than re-writing API/analysis logic.

## Data sources & attribution (required)

All tumor genomic data is retrieved from **cBioPortal's public open-access REST API**
(`https://www.cbioportal.org/api`); the skill accesses only aggregate, summary-level
somatic mutation/CNA calls and non-identifying clinical attributes — **no
controlled-access or dbGaP-protected data**. Every report MUST include this
acknowledgment and the two cBioPortal citations:

> Tumor genomic data from The Cancer Genome Atlas (TCGA) open-access tier, obtained
> via cBioPortal (https://www.cbioportal.org); used per NIH GDC data-use policy.

- Cerami E, Gao J, Dogrusoz U, et al. The cBio Cancer Genomics Portal: An Open Platform
  for Exploring Multidimensional Cancer Genomics Data. *Cancer Discovery.*
  2012;2(5):401–404. doi:10.1158/2159-8290.CD-12-0095
- Gao J, Aksoy BA, Dogrusoz U, et al. Integrative Analysis of Complex Cancer Genomics
  and Clinical Profiles Using the cBioPortal. *Science Signaling.* 2013;6(269):pl1.
  doi:10.1126/scisignal.2004088

See `DATA_SOURCES.md` for the full notice and access-tier compliance details. If any
future change adds a controlled-access, dbGaP, or non-cBioPortal endpoint, flag it for
data-use review before use.

---

## Workflow

### Step 0 — Clarify (short; skip anything already specified)
Confirm with the user (mirror the `literature-review` clarify style — one compact round):
1. **Gene(s)** to analyze.
2. **Cohorts**: confirm the auto-selected defaults (TCGA PanCancer Atlas + best
   MSK-IMPACT) or take an override.
3. **Figures**: offer the menu and let them pick (default = all applicable):
   ranked bars, cross-cohort scatter, mutation-vs-CNA stacked, hotspot landscape.
4. **Report depth** (standard vs brief) and **literature grounding** on/off.
If the user already gave these, don't re-ask — proceed.

### Step 1 — Resolve gene(s) + cohorts
```python
import sys; sys.path.insert(0, "scripts")
import cbioportal_client as cb
genes = cb.resolve_genes(["KRAS"])          # any symbol(s)
entrez = [g["entrezGeneId"] for g in genes]
cohorts = cb.auto_select_cohorts()          # {"tcga":[...], "msk":study}
# override example: cohorts["msk"] = cb.find_studies_by_keyword("msk_impact_2017")[0]
```
For each study, resolve profiles with `cb.resolve_profiles(study_id)` →
`{"mutation":..., "cna":...}`. Skip a study for an assay it lacks (report mutation-only,
mark CNA NA).

### Step 2 — Denominators (common)
For each study, `cb.profiled_samples(study_id)` → `{"seq":set, "cna":set}`. The
**common denominator** per cancer type is `seq ∩ cna`. (See `conventions.md`; this is
the default and guarantees `any% ≥ max(mut%, amp%)`.)

### Step 3 — Fetch mutations
`cb.fetch_mutations(mut_profile, entrez, f"{study}_sequenced")`. Then
`analyze_alterations.nonsilent_mutated_samples(records)` for sample-level mutated sets.

### Step 4 — Fetch CNA
`cb.fetch_cna(cna_profile, entrez, f"{study}_cna")`. Then
`analyze_alterations.gistic_event_samples(records)` → `{"amp":set,"deepdel":set}`.

### Step 5 — Per-cancer-type split + frequency table
- **TCGA**: each study is one cancer type — use the study directly.
- **MSK (mixed)**: `cb.fetch_cancer_type(study)` maps sampleId→CANCER_TYPE; group the
  profiled/event sets by type.
Build rows with `analyze_alterations.compute_row_common(...)` per cohort×cancer_type.
Concatenate to a DataFrame, then **self-check**:
```python
import analyze_alterations as aa
bad = aa.validate_common_invariant(freq_df)
assert bad.empty, f"denominator invariant violated:\n{bad}"
```
For KRAS, also run `aa.sanity_check_kras(freq_df)` and review warnings before reporting.
For other genes, sanity-check top types against literature (Step 8). Save the CSV.

### Step 6 — Hotspots (conditional)
If `aa.has_recurrent_hotspots(all_mut_records)` is True (oncogene-like), build
`aa.hotspot_bins(records, hotspot_codons={12:"G12",13:"G13",61:"Q61",117:"K117",146:"A146"})`
and save hotspot/allele CSVs. If False (tumor-suppressor-like), skip the hotspot figure;
report top alleles and note the dispersed loss-of-function pattern instead.

### Step 7 — Figures (adaptive, per user selection)
Use `scripts/make_figures.py`. Generate only applicable figures:
- `fig_ranked_bars` — always.
- `fig_cross_cohort_scatter` — only if ≥2 cohorts share matched cancer types (harmonize
  via the mapping in `conventions.md`).
- `fig_mutation_vs_cna` — always (per cohort).
- `fig_hotspot_landscape` — only if hotspot-driven.
**Media-check every figure** with `Read(..., mode="media_output_check")` on the PNG;
regenerate if blank/clipped/overlapping before continuing.

### Step 8 — Literature grounding (optional, default on)
Use the Biomni **`LiteratureSearch`** tool (not hand-written web calls) with a few
targeted queries: gene function/oncogenicity, its alteration in the top-hit cancer
types, and therapeutic relevance (e.g. KRAS G12C inhibitors sotorasib/adagrasib).
Records accumulate in `/mnt/results/execution_trace/references.jsonl`; read the full
records and **cite only those**, by their returned index. This grounds the intro and
discussion and supplies the reference list. If the user turned this off, write a concise
intro from established facts without fabricating citations.

### Step 9 — Infographic
Use the **`GenerateImage`** tool to create ONE clean summary infographic (conceptual —
do not hand-plot it). Content: gene name, the top-altered cancer types with their
alteration %, dominant alteration type (mutation vs amplification), the key
hotspot/allele if applicable, and cross-cohort concordance. Save the PNG to
`/mnt/results/figures/` for embedding.

### Step 10 — PDF report
Load the **`pdf-report-generation`** skill and follow it (brand palette, table styles,
`hAlign="CENTER"`, `<sub>`/`<super>` not Unicode, pypdf + media-check validation).
Report structure:
1. **Infographic** (top of report / after title).
2. **Introduction** — gene biology, oncogenic role, therapeutic relevance, why a
   cross-cohort alteration survey matters (literature-grounded, inline `[N]` cites).
3. **Methods** — cBioPortal cohorts, molecular profiles, non-silent + GISTIC
   definitions, the **common denominator**, stability flag, hotspot binning.
4. **Results** — narrative + the selected figures + a top-types summary table
   (full per-type table as appendix).
5. **Conclusions** — cross-cohort concordance, notable cancer types, hotspot/allele
   patterns, and caveats.
6. **References** — the LiteratureSearch records actually cited, PLUS the required
   data acknowledgment and the two cBioPortal citations (Cerami et al. 2012; Gao et al.
   2013) from the "Data sources & attribution" section / `DATA_SOURCES.md`. Include the
   TCGA/GDC acknowledgment sentence verbatim.
7. **Next steps** — concrete follow-ups (e.g. add survival by alteration status,
   co-occurrence, larger MSK cohorts, structural variants, or a specific allele deep-dive).
Name it `report_<gene>_alterations.pdf`. Validate (≥2 pages, >5 KB, extractable text)
and media-check the PDF; fix and re-check if anything is blank/clipped.

---

## Scientific caveats (state these in the report)
Purity/stromal dilution can lower observed frequencies vs canonical (KRAS PAAD ~65–75%
vs canonical ~90%); pan-cohort rates reflect case-mix, not biology; cross-cohort label
matching is best-effort; only high-level (+2) / deep (−2) GISTIC events are counted;
curated calls are trusted (no re-calling). Full detail in `references/conventions.md`.

## Acceptance checks (run before declaring done)
- Gene(s) resolve; ≥1 cohort enumerated with a valid mutation and/or CNA profile.
- `validate_common_invariant` returns empty (any% ≥ max(mut%, amp%) for all rows).
- Every reported frequency is in [0,100] with a non-zero denominator.
- Selected figures render and pass media-check; PDF passes pypdf + media-check.
- CSV row counts match the number of cancer types per cohort.
- For KRAS, ballpark sanity passes (see `conventions.md`); for other genes, top types
  are consistent with the literature pulled in Step 8.

## Common pitfalls
- Using the per-assay denominator by default (can make any% < mut% — use the common
  denominator; per-assay is QC-only).
- Forcing a hotspot figure for a tumor suppressor (skip it; report allele/domain spread).
- Looping per-sample instead of batch `*/fetch` endpoints (slow; the client batches).
- Comparing pooled cohort rates and attributing case-mix differences to biology.
- Fabricating citations — cite only `references.jsonl` records from LiteratureSearch.
