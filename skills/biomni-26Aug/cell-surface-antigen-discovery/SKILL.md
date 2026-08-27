---
id: "skill_37c83b1a2ba2ef32921254e95c376919"
name: cell-surface-antigen-discovery
description: "Use to discover and rank tumor-selective, antibody-accessible cell-surface antigens for ADC, CAR-T, bispecific/T-cell engager, or radioligand programs. Integrates single-cell tumor expression, surface topology, normal-tissue HPA safety, tractability, and known-antigen controls."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Nominate cell-surface antigens for ADC or CAR-T development by integrating single-cell tumor specificity across atlases, the surfaceome with extracellular-topology accessibility, a Human Protein Atlas normal-tissue therapeutic index, and antibody tractability. Score known validated targets alongside novel candidates as a validation harness, add per-candidate literature evidence, and generate a PDF report with an infographic summary plus intro, methods, results, conclusions, figures, and references."
---

# Cell-Surface Antigen Discovery (ADC / CAR-T / Bispecific)

Nominate antibody-accessible cell-surface antigens for ADC, CAR-T, bispecific T-cell engager, and radioligand development. The pipeline prioritizes targets by **tumor-surface specificity + normal-tissue safety (therapeutic index)**, NOT by tumor essentiality — validated surface antigens (TROP2, c-MET, HER2) are typically non-essential, so essentiality is reported as an annotation only.

## Scope

This skill nominates antibody-accessible cell-surface antigens for ADC, CAR-T, bispecific T-cell engager, and radioligand development. It integrates single-cell tumor specificity across atlases, the surfaceome with extracellular-topology accessibility, a Human Protein Atlas normal-tissue therapeutic index, and antibody tractability — then scores known validated targets alongside novel candidates as a validation harness and produces a tiered, ranked target list with a PDF report.

**What it does NOT do:**
- Isoform or glycoform selection — the surfaceome seed is at the gene level; tumor-specific isoforms or glycoforms (e.g. MUC1 TN glycoform) are not distinguished.
- Quantitative shedding / soluble-antigen assessment — ectodomain shedding is flagged qualitatively in the surfaceome notes but not quantified; a separate soluble-antigen assay is needed.
- Mutation- or fusion-status stratification — the pipeline scores pan-tumor surface expression; it does not stratify by driver mutation, fusion, or subtype beyond the Census disease label.
- Antigen density estimation — the score uses expression magnitude and prevalence from scRNA-seq, not absolute protein copy number (flow cytometry or quantitative IHC is needed for that).
- Essentiality-driven target discovery — DepMap essentiality is annotation only, never a gate (see Why Specificity + Safety below).

## When to Use This Skill

- **You want cell-surface targets for an antibody modality** (ADC, CAR-T, bispecific, radioligand) in a defined tumor type
- **You have (or can pull) single-cell tumor data** and want compartment-specific surface antigens (malignant vs stroma vs immune vs endothelial)
- **You need a therapeutic-index-aware ranking** that flags normal-tissue (on-target/off-tumor) liabilities using real Human Protein Atlas protein + RNA data
- **You want a credibility check** — known validated targets are scored alongside novel candidates (recall harness), and negative controls must rank low
- **You want per-candidate literature evidence** folded into the report

**Don't use for:**
- Small-molecule / degrader target discovery (essentiality-driven) → use `scrna-disease-drug-discovery` or `genetic-target-hypothesis`
- General disease target prioritization with genetic evidence → use `scrna-disease-drug-discovery`
- Cell-type annotation only → use `scrnaseq-scanpy-core-analysis`

## Why Specificity + Safety, Not Essentiality (READ FIRST)

Antibody modalities recognize an antigen on the cell **surface**; the mechanism (payload delivery, T-cell redirection) does **not** require the target to be essential for tumor survival. Gating on tumor essentiality (e.g. DepMap CRISPR) systematically **enriches for housekeeping genes** (ion pumps, pan-epithelial adhesion) that are broadly expressed in normal tissues → severe on-target/off-tumor toxicity → undruggable, while **discarding the best targets** (TROP2, c-MET, HER2 are all non-essential). This skill therefore:

- Uses **tumor-surface specificity** and **normal-tissue therapeutic index** as the primary prioritization axes.
- Applies an **extracellular-topology filter** so cytoplasmic (e.g. cingulin/CGN), ER/organelle (e.g. ITPR3), and secreted-ECM (e.g. laminins) proteins cannot be nominated as "surface" antigens.
- Reports **DepMap essentiality as an annotation** (informative for biology, not a gate).

See [references/scoring_methodology.md](references/scoring_methodology.md).

## Step 0 — Check the Environment First

Before installing anything or writing code, confirm what the Biomni environment already provides. Many dependencies of this skill are **preinstalled**, and several data sources are available as **datalake datasets** or **queryable databases** — use them instead of re-fetching where possible.

Check Python packages directly:

```python
from importlib.util import find_spec

for package in ("cellxgene_census", "scanpy", "anndata"):
    print(package, "available" if find_spec(package) else "missing")
```

This workflow uses Human Protein Atlas, DepMap, and optional GTEx resources.

What you will typically find already present (verify at runtime; do not assume):

| Need | Usually available as | Notes |
|------|----------------------|-------|
| Single-cell tumor atlases | `cellxgene-census`, `scanpy`, `anndata` (preinstalled packages) | No install needed in most Biomni environments |
| Normal-tissue protein/RNA | **Human Protein Atlas** | The script pulls the full dual-signal baseline (see below) |
| Essentiality annotation | **DepMap** | Annotation only, never a gate |
| Normal-tissue RNA cross-check | **GTEx** | Optional orthogonal baseline |
| Tractability / known drugs / locations | Open Targets Platform GraphQL (public API) | Called by `scripts/annotate_targets.py` |
| PDF report | `reportlab`, `pypdf` (preinstalled) + **pdf-report-generation** skill | See report step |
| Report figures | `matplotlib`, `seaborn` (preinstalled) | Data plots only |

**Only `pip install` what is genuinely missing.** If `cellxgene-census` is absent: `pip install -U cellxgene-census` (needs Python 3.10–3.12, ≥16 GB RAM for typical use).

## Inputs

**Tumor expression source (choose one):**
- **CZ CELLxGENE Census** (default) — a *verified* disease label, or a **list** of labels. The demo targets **`"lung adenocarcinoma"`** specifically. Census splits lung cancer by subtype, so the umbrella `"non-small cell lung carcinoma"` is a *separate* label — pass `["lung adenocarcinoma", "non-small cell lung carcinoma"]` to union them (keep squamous separate to preserve adenocarcinoma specificity). The skill integrates **multiple whole-cell scRNA-seq datasets** (`suspension_type == 'cell'`); single-nucleus is de-weighted. **Coverage varies** — for single-nucleus-only or thin labels, supply a curated whole-cell atlas via the own-`.h5ad` path. See [references/census_atlas_guide.md](references/census_atlas_guide.md).
- **Your own annotated AnnData (.h5ad)** — must contain a cell-type/compartment column and (ideally) a malignant-cell annotation; supply column names.

**Surface-gene universe:** bundled surfaceome (`references/surfaceome_seed.csv`, `load_surfaceome()`) + optional live cross-check via Open Targets `subcellularLocations`. For a genome-wide run, use `surfaceome_filter.load_surfy_surfaceome()` — it downloads SURFY Table S3 (Bausch-Fluck 2018) and assigns **per-gene** topology, ectodomain accessibility, and localization from SURFY's own topology string / Almen class / evidence source. **Do NOT assign every SURFY member `localization='plasma_membrane'`** — that makes the topology gate inert (the gate then raises; see Common Issues). See [references/census_atlas_guide.md](references/census_atlas_guide.md).

**Normal-tissue baseline:** built by `scripts/hpa_baseline.py` from two Human Protein Atlas bulk downloads (RNA consensus nTPM + IHC protein levels). See [references/hpa_baseline_guide.md](references/hpa_baseline_guide.md).

**Validation harness:** bundled `references/known_surface_targets.csv` (clinically validated targets + cautionary negative controls). Used for recall@K; edit to add tumor-specific known targets.

## Outputs

**Ranked targets:**
- `ranked_surface_targets.csv` — all scored candidates: per-compartment expression, specificity ratios, n_datasets, topology/accessibility, **`surface_confirmation`** (confirmed_experimental / confirmed_ot / unconfirmed) + **`is_unconfirmed_surface`**, normal-tissue safety score, antibody tractability, known-drug flag, DepMap essentiality (annotation), composite surface-target score, tier
- `target_evidence_cards.json` — per-target evidence summaries (incl. literature) for the report

**Intermediate evidence (read from disk for the report — never from memory):**
- `cohort_cell_counts.csv` + `cohort_summary.json` — **per-atlas × per-compartment cell counts actually analysed** (post-subsampling), plus discovered-vs-analysed totals and the subsample cap. This is the source of the honest headline cohort number.
- `compartment_expression.csv` / `compartment_expression_consensus.csv` — per-dataset and cross-dataset compartment expression (the analysed matrices)
- `surfaceome_topology.csv` — surfaceome membership + ectodomain accessibility per candidate
- `target_annotations.csv` — Open Targets tractability, subcellular location, known drugs, DepMap essentiality
- `target_baseline_expression_long.csv` — HPA dual-signal normal-tissue baseline (from `hpa_baseline.py`)
- `therapeutic_index.csv` — normal-tissue safety score + vital-organ flags
- `validation_harness.csv` + `validation_metrics.json` — known-target ranks, **provenance (pre_registered / added_post_ranking)**, pre-registered recall@K (headline) + augmented recall@K (when applicable), harness lock date, negative-control verdict, holdout caveat
- `therapeutic_index_stability.csv` + `therapeutic_index_stability.json` — rank-stability check over TI safety-aggregation rules and tumor-quality weight alternatives (with derived verdict)
- `coverage_report.csv` — per-column annotation coverage (`n_annotated`/`percent_annotated`) and, for boolean columns, positive rate (`n_positive`/`percent_positive`)
- `report_facts.json` — pre-derived headline numbers and pre-formatted sentences for the report (**cohort** discovered-vs-analysed, tier counts, **topology confirmed/unconfirmed counts**, pre-registered + augmented recall, negative-control verdict, stability verdict, protein-evidence counts, safety counts, coverage figures, warnings). **The report MUST quote these verbatim.**
- `literature_evidence.json` — per-candidate literature hits (from the LiteratureSearch pass)

**Analysis object:**
- `analysis_objects.pkl` — all tables + metadata for downstream/custom use (`import pickle; objs = pickle.load(open('analysis_objects.pkl','rb'))`)
- `analysis_manifest.json` — provenance: Census version, datasets, filters, parameters, HPA download dates, step timing

**Visualizations (PNG + SVG, 300 DPI):**
- Compartment specificity heatmap (candidates × malignant/CAF/immune/endothelial)
- **Therapeutic-index map** (tumor specificity x-axis vs normal-tissue safety y-axis; known targets labeled)
- Validation-harness recall plot (where known targets rank)
- Top-target ranking bar chart with tier coloring

**Report:**
- `report_surface_antigen_discovery.pdf` — Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

## Clarification Questions

**Default settings (use unless user specifies otherwise):** Organism: human | Tumor source: CZI Census | Surfaceome: bundled seed + Open Targets cross-check | Normal-tissue baseline: HPA dual-signal (RNA + IHC) | Essentiality: annotation only | Validation harness: enabled | Literature evidence: enabled

### 1. **Input / data source** (ASK THIS FIRST):
   - Pull tumor single-cell data from the **CZI Census** by disease label, or use **your own annotated .h5ad**?
   - **Or use demo data?** — pinned Census run on **lung adenocarcinoma** (whole-cell, multi-atlas). All other parameters pre-defined.

> 🚨 **IF DEMO DATA SELECTED:** All parameters are pre-defined (human, lung adenocarcinoma, Census `2025-11-08`, bundled surfaceome + harness, HPA dual-signal baseline). **DO NOT ask questions 2–4.** Proceed directly to Step 1.

**Questions 2–4 are ONLY for users providing their own data / disease:**

### 2. **Tumor type & modality:** Which tumor type (disease label / EFO)? Which modality emphasis — ADC, CAR-T, bispecific (affects shedding/homogeneity weighting)?
### 3. **Compartments (own h5ad only):** Which column holds cell-type labels, and how is the malignant/epithelial compartment identified (label value or marker set)?
### 4. **Scope:** Bundled surfaceome seed (fast, ~hundreds of genes) or full in-silico surfaceome (comprehensive, ~2,800 genes, slower Census pull)?

## Data Integrity Rules

🚨 **NEVER reconstruct results from memory, notes, or session summaries.** 🚨

- Every quantitative value in the report must be read from a CSV on disk, not from session memory.
- If outputs are missing after a crash: STOP, check `results/`, re-run the step that produced the missing CSV. The consensus expression CSV, `target_baseline_expression_long.csv`, and `analysis_objects.pkl` are checkpoints.
- **NEVER** fabricate Census expression, normal-tissue values, tractability buckets, or literature citations. Missing values stay `null`/`NaN` and are reported as missing (the surfaceome filter and annotators emit "Removed N / Missing N" counts). Genes absent from HPA get `safety_score = NaN` → the scorer applies a **neutral 0.7** and flags them `safety_unassessed` — this must be stated honestly in the report (see Safety Honesty below).

## Standard Workflow

🚨 **MANDATORY: USE SCRIPTS EXACTLY AS SHOWN — DO NOT WRITE INLINE CODE** 🚨

**CRITICAL — DO NOT:**
- ❌ Write inline Census / API code → **STOP: use the script functions**
- ❌ Write a custom scorer → **STOP: use `score_surface_targets()`** (custom scorers drop the topology gate, therapeutic-index weighting, cross-dataset consensus, and the validation harness)
- ❌ Use DepMap essentiality as a filter → **STOP: it is an annotation only**
- ❌ Reconstruct values from memory after a crash → **STOP: read CSVs or re-run**
- ❌ Fabricate a normal-tissue baseline → **STOP: run `hpa_baseline.py`; missing genes stay NaN**

**⚠️ IF SCRIPTS FAIL — Script Failure Hierarchy:**
1. **Fix and Retry (90%)** — install missing package, run `discover` to fix Census labels, re-run the script
2. **Modify Script (5%)** — edit the script file, document the change, keep provenance + no-simulation behavior
3. **Use as Reference (4%)** — read the script, adapt the official API call, cite the source
4. **Write from Scratch (1%)** — only if genuinely impossible; explain why. NEVER substitute simulated values.

---

**Step 0 — Environment check:** confirm `cellxgene-census`/`scanpy`/`anndata` with
direct imports. Install only what is missing.

---

**Step 1 — Load surface-gene universe + tumor expression source:**
```python
from scripts.load_example_data import load_demo_inputs        # demo
inputs = load_demo_inputs()   # disease_label, census_version, surfaceome df, known_targets df

# (own data) instead:
# from scripts.surfaceome_filter import load_surfaceome
# surfaceome = load_surfaceome(include_in_silico=False)   # bundled seed
```
**VERIFICATION:** You MUST see: `"✓ Inputs loaded: N surface genes, M known targets"`

---

**Step 2 — Tumor specificity → surfaceome/topology → annotation:**
```python
from scripts.census_pull import discover_datasets, pull_compartment_expression
from scripts.surfaceome_filter import apply_topology_filter
from scripts.annotate_targets import annotate

datasets = discover_datasets(inputs["disease_label"], inputs["census_version"])   # whole-cell atlases
spec = pull_compartment_expression(inputs["surfaceome"]["gene_symbol"].tolist(),
                                   disease_label=inputs["disease_label"],
                                   census_version=inputs["census_version"], output_dir="results")
surf = apply_topology_filter(spec, inputs["surfaceome"])  # per-gene topology gate: excludes cytoplasmic/ER/secreted-ECM/no-ectodomain
ann  = annotate(surf["gene_symbol"].tolist(), output_dir="results")   # Open Targets tractability/locations/drugs + DepMap (annotation)

# GENOME-SCALE run (instead of the bundled seed):
#   from scripts.surfaceome_filter import load_surfy_surfaceome
#   surfaceome = load_surfy_surfaceome()               # ~2,800 SURFY genes, PER-GENE topology (downloads Table S3)
#   spec = pull_compartment_expression(surfaceome["gene_symbol"].tolist(), disease_label=..., ...)
#   surf = apply_topology_filter(spec, surfaceome)     # RAISES if a genome-scale set excludes nothing (inert gate)
```
> ⚠️ **Open Targets v4 note:** `annotate()` no longer returns a normal-tissue baseline (OT removed `Target.expressions`). The baseline now comes from HPA in Step 3. Do **not** add an `expressions` field back to the OT query — it will break the call.

> ⚠️ **Topology gate must be live.** `apply_topology_filter` gates on **per-gene** localization/accessibility. On a genome-scale set (≥200 genes) it **raises** if it excludes nothing — a blanket `plasma_membrane` assignment is the bug it guards against. It also carries a `surface_confirmation` call (experimental CSPA/GPI or Open Targets PM = confirmed; machine-learning-only = **unconfirmed**); the count of unconfirmed candidates is reported in Step 8, not folded into the pass list.

**VERIFICATION:** the topology filter prints "N retained (M excluded of K)" with M > 0 for a genome-scale run; `annotate()` prints an annotation count and reminds you to build the HPA baseline separately.

---

**Step 3 — Build the HPA normal-tissue baseline, then the therapeutic index:**
```python
from scripts.hpa_baseline import build_hpa_baseline_long
from scripts.normal_tissue_safety import compute_therapeutic_index

# Dual-signal HPA baseline (bulk download + cache on first run: RNA consensus + IHC protein)
build_hpa_baseline_long(surf["gene_symbol"].tolist(), output_dir="results")
#   -> results/target_baseline_expression_long.csv (gene_symbol,tissue,organs,rna_value,rna_level,protein_level)

ti = compute_therapeutic_index(ann, output_dir="results",
                               baseline_long_path="results/target_baseline_expression_long.csv")
```
The safety score is the **conservative min** of a protein-derived and an RNA-derived safety per gene, so a target that looks clean on RNA but is HIGH by IHC in a vital organ (or vice versa) is correctly demoted. Genes lacking HPA data → `safety_score = NaN`. See [references/hpa_baseline_guide.md](references/hpa_baseline_guide.md).
**VERIFICATION:** `"✓ HPA dual baseline: ... rows"` then `"✓ Therapeutic index: N gene(s) with favorable normal-tissue safety ..."`

---

**Step 4 — Score (topology gate + therapeutic-index weighting + consensus + harness):**
```python
from scripts.score_targets import score_surface_targets
scores = score_surface_targets(spec, surf, ann, ti, inputs["known_targets"], output_dir="results")
```
**DO NOT write a custom scorer.** `score_surface_targets()` applies the topology gate, cross-dataset consensus, therapeutic-index weighting, and the validation harness.
**VERIFICATION:** You MUST see: `"✓ Scoring complete: N candidates, T tier-1; validation recall@20 = X/Y known targets; stability verdict: <verdict>"`. The reported recall is the **pre-registered** headline (locked before ranking); if you later add a validated target to the harness, both pre-registered and augmented recall are emitted (see Step 5).
**If recall@20 is low (<50%):** check that whole-cell (not only snRNA) datasets were pulled and the disease label matched — see Common Issues.

---

**Step 5 — Literature evidence for top candidates (LiteratureSearch):**
Run a `LiteratureSearch` pass on the top candidates (Tier 1/2 and any novel Tier-3 of interest) to attach real citations, then persist them for the report and harness curation.

- **Per-candidate evidence:** for each top gene, search e.g. `"<GENE> <tumor type> antibody-drug conjugate OR CAR-T OR surface antigen"`. Record whether it is a validated/known target, in clinical development, or genuinely novel. Save a compact record per gene (title, year, one-line finding, DOI/URL) to `results/literature_evidence.json`.
- **Harness curation (anti-circularity):** the validated-target set is **pre-registered** — locked *before* ranking (`references/known_surface_targets.csv` carries `date_added` + `provenance`; lock date = `score_targets.HARNESS_LOCK_DATE`). If a candidate turns out to be a clinically validated antigen not already in the harness, you MAY add it — but set `provenance=added_post_ranking` and `date_added=<today>`. The harness then reports **both** the pre-registered recall (headline) **and** an augmented recall that includes the promoted target, labelled as such. **Never report the augmented figure alone** — a benchmark that admits the discovery it validates is circular. Adding a post-ranking target while reporting a single recall figure makes the export gate **raise**. Conversely, confirm the negative controls are still non-targets.
- **Report references:** every literature claim in the PDF must map to a citation captured here — do **not** write mechanistic/clinical claims from memory.

> Grounding rule: treat one-line search summaries as discovery signals; before making a specific clinical/mechanistic claim, verify it from the richer paper record. Never fabricate a citation, DOI, or trial ID.

**VERIFICATION:** `results/literature_evidence.json` exists and covers the candidates you cite.

---

**Step 6 — Generate visualizations:**
```python
from scripts.generate_visualizations import generate_all_plots
generate_all_plots(spec, surf, ann, ti, scores, inputs["known_targets"], output_dir="results")
```
🚨 **DO NOT write inline plotting code. The script handles PNG + SVG with graceful fallback.** 🚨
**VERIFICATION:** You MUST see: `"✓ All plots generated successfully!"`
**MANDATORY:** after plots are written, run a media output check (`Read(mode="media_output_check")`) on the therapeutic-index map and the ranking bar chart; regenerate if blank/clipped.

---

**Step 7 — Export results:**
```python
from scripts.export_results import export_all
export_all(spec, surf, ann, ti, scores, inputs["known_targets"], output_dir="results")
```
**DO NOT write custom export code. Use export_all().**
**VERIFICATION:** You MUST see: `"✓ Export consistency gate: all invariants satisfied."` (or a `⚠ EXPORT CONSISTENCY GATE — WARNINGS:` block) followed by `"=== Export Complete ==="`

---

**Step 8 — Generate the PDF report (MANDATORY terminal step — the run is not complete until this is done):**

🚨 **This step is required. The skill does not end until `report_surface_antigen_discovery.pdf` is written and validated.** 🚨

Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

**Load `results/report_facts.json`.** Every headline count, cohort cell count, recall figure, topology confirmed/unconfirmed count, negative-control verdict, stability verdict, protein-evidence count and coverage figure in the report MUST be the value or pre-formatted sentence from this file, quoted verbatim. Do not recompute, re-round, or rephrase. If `report_facts.json` carries a non-empty `warnings` list, every warning must appear in the Discussion. **Do not describe the ranking as robust, or the negative controls as behaving correctly, unless `report_facts.json` says so — those words are computed, not chosen.**

🚨 **Three honesty rules baked into `report_facts.json` (quote it; do not derive your own):**
- **Cohort:** the headline cell count is the **analysed** cohort (`cohort.cohort_statement` / `cohort.n_cells_analyzed`), NOT the discovery catalogue. Report discovered vs analysed side by side and include the per-atlas × per-compartment breakdown (`cohort.per_atlas_compartment_counts`) so a reader can see any atlas contributing very few cells to a compartment. (The export gate raises if the analysed count does not match the analysed matrices.)
- **Topology confirmation:** quote `topology.topology_statement` — state how many scored candidates are independently **confirmed** plasma-membrane vs **unconfirmed** machine-learning predictions, and name any unconfirmed candidate in the top 20. Do not present unconfirmed hits with the same confidence as confirmed antigens.
- **Recall:** the headline is the **pre-registered** recall (`validation.recall_pre_registered_at_10_str`/`_20_str`). If `validation.harness_augmented_after_ranking` is true, also report the augmented recall and label it; **never the augmented figure alone** (the export gate raises otherwise).

**Skill-specific content the report MUST carry (quote `report_facts.json` verbatim where noted):**

- **Executive summary** — built from `report_facts.json` `tiering_statement` and `validation_statement` (verbatim).
- **Methods** — the data sources actually used (Census version + datasets from `analysis_manifest.json`, HPA download build, Open Targets, DepMap) and the specificity/topology/safety/consensus logic and parameters; emit source attribution in Methods/References (see `DATA_SOURCES.md`).
- **Results** — open with the cohort: discovered vs analysed cells (`report_facts.json` `cohort.cohort_statement`) plus the per-atlas × per-compartment table (`cohort.per_atlas_compartment_counts`), so any atlas contributing few cells to a compartment is visible, not averaged away; the four analysis figures with captions; a top-20 ranked table (with the `surface_confirmation` column; note "full results in ranked_surface_targets.csv"); the topology-confirmation count (`report_facts.json` `topology.topology_statement` — confirmed vs unconfirmed, naming unconfirmed top-20 hits); and the validation-harness recall (`report_facts.json` `validation` — pre-registered headline, plus augmented if `harness_augmented_after_ranking`).
- **Discussion / Interpretation** — biological meaning and why the top candidates are attractive; reproduce every entry from `report_facts.json` `warnings`; quote `negative_controls.statement` verbatim (and if `negative_controls.verdict` is FAIL, say so plainly); quote `stability.statement` verbatim (do not call the ranking "robust" unless `stability.verdict` says robust).
- **Safety-honesty subsection (REQUIRED).** Quote `report_facts.json` `safety.safety_statement` verbatim; state how many scored candidates carry a *computed* HPA safety score vs how many fall back to the **neutral 0.7 default** because they are un-annotated, and scope every safety distribution to the correct set (assessed subset vs full scored set — never attribute the assessed-subset distribution to the full scored set).
- **Protein-evidence subsection.** Quote `report_facts.json` `protein_evidence.protein_evidence_statement` verbatim; HPA nTPM is bulk RNA and is NOT protein-level validation — only `has_ihc_protein_measurement` is. Do not claim "all top-ranked candidates have protein-level validation" unless `report_facts.json` says so.
- **Literature evidence** — per-candidate citations from `literature_evidence.json`, with inline references.

Name the file `report_surface_antigen_discovery.pdf` and write it directly to the results directory.
**VERIFICATION (from the pdf skill):** pypdf page_count ≥ 2, file_size > 5000, extractable text on page 1, and a `media_output_check` visual pass with figures ≥80% width, no orphaned captions, no half-empty pages. **The skill is not complete until this verification passes.**

## Scoring Methodology

Full details: [references/scoring_methodology.md](references/scoring_methodology.md).

**Score shape:** `final = tumor_quality × safety_factor × consensus_multiplier`. Normal-tissue therapeutic index is a **multiplicative** safety factor, not a linear term — a great tumor antigen that is also expressed in heart/liver/lung is not a target, however tumor-specific it looks within the microenvironment. This is what demotes broadly-expressed housekeeping genes like CDH1 / ATP1A1.

**Tumor-quality components (weights sum to 1.0):**

| Component | Weight | Description |
|-----------|--------|-------------|
| Tumor-surface specificity | 0.30 | Epithelial/malignant enrichment over CAF **and** immune (min of the two ratios, log-scaled) |
| Tumor expression magnitude | 0.20 | Mean expression in malignant/epithelial cells |
| Expression homogeneity / prevalence | 0.20 | % of malignant cells expressing (antigen-negative-escape risk for ADC/CAR-T) |
| Surface accessibility | 0.15 | Ectodomain accessibility from topology (high=1.0 / partial=0.6 / low=0.3); `none` gated out upstream |
| Antibody tractability | 0.15 | Open Targets antibody tractability bucket |

**× Safety factor (therapeutic index):** normal-tissue safety in [0,1] (1 = low vital-organ expression), from the HPA dual-signal baseline (conservative min of protein and RNA). Unassessed → **neutral 0.7** and flagged `safety_unassessed`.
**× Consensus multiplier:** `0.5 + 0.5·min(n_datasets_enriched, 3)/3` — reproducibility across atlases (3+ enriched datasets → full weight; single dataset → 0.67).
**Annotation only (NOT scored):** DepMap essentiality; known-drug flag (raises confidence, does not inflate the novelty-oriented score).
**Tiers:** Tier 1 (≥0.55), Tier 2 (0.35–0.55), Tier 3 (<0.35). Topology `none` → excluded before scoring.

**Validation harness:** the curated known targets are scored by the identical pipeline; the report states recall@10/@20 (how many known targets land in the top K) as a QC metric. Cautionary negative controls (CDH1, ATP1A1, CGN) should rank **low** — if they rank high, the safety/topology layers are misconfigured.

## Safety Honesty (READ BEFORE WRITING THE REPORT)

The therapeutic-index safety score is only as good as HPA coverage. Two rules prevent the most common reporting error:

1. **Scope every safety distribution to the correct set.** Report separately (a) the distribution over candidates that carry a **computed** HPA safety score, and (b) the full scored set, in which **un-annotated genes sit at the neutral 0.7 default**. Never present the assessed-subset distribution as if it described the full scored set.
2. **State the neutral-default count.** Give the number (and %) of scored candidates at the 0.7 neutral default (`safety_unassessed = True`). When you highlight a top-N composite list, note how many of those N have a computed safety score vs sit at the default — a high composite score driven by an *un-assessed* safety factor is a weaker claim than one backed by real HPA data.

Compute these directly from `therapeutic_index.csv` / `ranked_surface_targets.csv`; do not estimate.

## Scientific Caveats

These assumptions and edge cases must be reflected honestly in the report. They are factual constraints, not limitations to soften.

1. **Held-out recall is a small-n single fixed split, not cross-validated.** Recall@K is computed on the `recall_core=1` validated targets (9 in the bundled harness) on one fixed partition. One target moves recall by `100/n_core` percentage points (see `report_facts.json` `validation.holdout_caveat`). No repeated cross-validation or bootstrap interval is computed. A statistical reviewer will ask about this; state the caveat explicitly in the report.
2. **Cautionary negative controls are an empirical check that CAN FAIL.** If `report_facts.json` `negative_controls.verdict` is `FAIL`, say so plainly — do not soften it. A FAIL means one or more housekeeping/pan-epithelial controls (CDH1, ATP1A1, EPCAM) ranked in the top 25% of scored candidates, indicating the safety or topology layers did not fully demote them.
3. **Tier cutoffs are absolute thresholds on the composite scale.** An empty Tier 1/2 is a legitimate outcome and must be framed as "highest-ranked candidates", not "candidates clearing the nomination bar." The `report_facts.json` `warnings` list will flag `n_tier1 + n_tier2 == 0` if it occurs.
4. **HPA nTPM is bulk RNA and is NOT protein-level validation.** Only `has_ihc_protein_measurement` (a non-null `vital_protein_max` from the HPA IHC download) counts as protein evidence. A computed HPA safety score derived from RNA alone is a category error if cited as protein validation. See `report_facts.json` `protein_evidence`.
5. **The bundled surfaceome seed is a subset, not a genome-wide screen.** `references/surfaceome_seed.csv` contains ~65 validated surface genes. For a comprehensive run, fetch the full in-silico surfaceome (~2,800 genes) via the scope option. Novel candidates outside the seed will not appear unless the full surfaceome is used.
6. **snRNA-seq under-detects surface transcripts and is de-weighted.** Single-nucleus data systematically misses membrane-encoding transcripts. The pipeline prefers whole-cell assays (`suspension_type == 'cell'`); coverage varies by Census disease label. For snRNA-only labels, supply a curated whole-cell atlas via the own-`.h5ad` path.
7. **The specificity axis is epithelial-vs-stroma/immune, NOT verified malignant-vs-normal.** Tumor cells are grouped into the `epithelial` compartment, but the demo Census data carries no malignant-cell annotation, so "tumor specificity" here means enrichment in the epithelial compartment over CAF/immune/endothelial — it does NOT distinguish malignant epithelium from normal epithelium within the tumor. A target that is uniformly epithelial (malignant + normal) can look specific on this axis yet have a poor therapeutic window. State this prominently in the report; for a true malignant-vs-normal contrast, supply an `.h5ad` with a malignant-cell annotation. (The normal-tissue HPA safety axis partly compensates, but is not a substitute.)
8. **The reported cell count is the ANALYSED cohort, not the discovery catalogue.** Each atlas is subsampled (default ≤20,000 cells) before analysis, and atlases with <20 epithelial cells are dropped. `report_facts.json` `cohort` reports discovered vs analysed side by side with a per-atlas × per-compartment breakdown; the export gate raises if the headline analysed count does not match the analysed matrices. Never headline the multi-hundred-thousand-cell discovery total as the statistical basis.
9. **Genome-scale surface membership is a SURFY prediction, not proof of a plasma-membrane epitope.** `load_surfy_surfaceome()` assigns per-gene topology/accessibility/localization from SURFY Table S3; each candidate is labelled `confirmed_experimental` (CSPA/GPI), `confirmed_ot` (Open Targets plasma-membrane), or **`unconfirmed`** (machine-learning-only, no independent confirmation). The count of unconfirmed candidates is in `report_facts.json` `topology` and must be reported. An unconfirmed hit (e.g. an ER–PM contact-site protein) is a hypothesis requiring wet-lab surface confirmation (flow/IHC), not a validated antigen.
10. **Held-out recall is pre-registered, and augmentation is disclosed (anti-circularity).** The validated-target set is locked before ranking (`known_surface_targets.csv` `date_added`/`provenance`; `HARNESS_LOCK_DATE`). The headline recall is computed on the pre-registered set only. If a novel candidate is promoted into the harness after ranking, an augmented recall is reported **alongside** — and labelled — never as the headline alone; a benchmark that admits the discovery it validates is circular. The export gate raises if the harness was augmented but only one recall figure is reported.

## Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Census returns no cells | Disease/tissue label mismatch | Run `discover_datasets()` / the `czi-cellxgene-census` `discover` command; copy labels exactly (disease may be `||`-composite) |
| Malignant compartment mislabeled as immune | Cell-type string contains a substring of another compartment (e.g. "malignan**t cell**" ⊃ "t cell") | Fixed: `assign_compartment` uses word-boundary matching with epithelial checked first. Do not revert to substring `in` matching |
| Low known-target recall | Only snRNA-seq datasets pulled, or wrong disease label | Confirm whole-cell assays present; snRNA under-detects surface transcripts and is de-weighted |
| Malignant compartment empty | Census labels tumor cells as epithelial subtypes, not "malignant cell" | Use the compartment mapping in `census_pull.py`; for own data pass the malignant label/markers |
| `annotate()` returns no baseline / KeyError expressions | Open Targets v4 removed `Target.expressions` | Expected — build the baseline with `hpa_baseline.py` (Step 3). Do not re-add `expressions` to the OT query |
| HPA download returns a tiny file / not-a-zip error | Deprecated `normal_tissue.tsv.zip` (now HTTP 404, HTML page) | Use the current endpoints (already in `hpa_baseline.py`): `rna_tissue_consensus.tsv.zip` + `normal_ihc_data.tsv.zip` |
| Gene missing from surfaceome | Not in bundled seed | Use `load_surfy_surfaceome()` (genome-scale, per-gene topology), or rely on Open Targets `subcellularLocations` cross-check |
| `RuntimeError: Topology gate excluded 0 of N genome-scale candidates` | SURFY members were blanket-assigned `plasma_membrane`/`high` (inert gate) | Load the surfaceome with `load_surfy_surfaceome()` so localization/accessibility are per-gene; do NOT overwrite them with a constant |
| `RuntimeError: Recall-reporting gate ...` | A target was added to the harness *after* ranking but only one recall figure is reported | Report both pre-registered and augmented recall (see Step 5); set the post-hoc target's `provenance=added_post_ranking` |
| `RuntimeError: Cohort gate ...` | Headline cell count ≠ the analysed matrices (e.g. the discovery catalogue was used) | Quote `cohort_summary.json` / `report_facts.json` `cohort` — report the analysed count, not the catalogue |
| Could not download SURFY Table S3 | `wlab.ethz.ch` migrated / serves a Git-LFS pointer | `load_surfy_surfaceome()` falls back to a byte-identical mirror; or pass a local path via `source=` (download from wollscheidlab.org/SURFY, cite Bausch-Fluck 2018) |
| Open Targets rate limit | Too many gene queries | Built-in 0.5s throttle + retry; annotate top candidates first |
| `cellxgene_census` import error | Not installed / unsupported Python | `pip install -U cellxgene-census`; needs Python 3.10–3.12 |
| SVG export failed | Missing cairo backend | Normal — PNG always written; both created in most environments |

## Suggested Next Steps

1. **Protein-level validation** — IHC / flow on tumor microarrays for Tier-1 candidates; confirm membrane localization and homogeneity
2. **Shedding / sink assessment** — for high-ectodomain mucins (MUC1/MUC16), check soluble-antigen liability
3. **Normal-tissue deep dive** — `tissue-expression-from-degs` for full GTEx/HPA/cell-type breakdown of top candidates
4. **Bispecific pairing** — co-expression analysis of Tier-1 pairs for dual-antigen or logic-gated CAR-T
5. **Genetic / disease evidence** — `open-targets` and `scrna-disease-drug-discovery` for orthogonal support

## Related Skills

**Upstream/data:** `czi-cellxgene-census` (Census access), `scrnaseq-scanpy-core-analysis` (own-data annotation) | **Complementary:** `open-targets` (tractability/safety/known drugs), `tissue-expression-from-degs` (normal-tissue baseline) | **Reporting:** `pdf-report-generation` (used in Step 8) | **Contrast:** `scrna-disease-drug-discovery` (essentiality/genetics-driven, for non-surface modalities)

## Data Sources & Licenses

This skill integrates public biomedical data. Full details, required attribution strings, and
share-alike compatibility notes are in **[DATA_SOURCES.md](DATA_SOURCES.md)** — the authoritative
license record. Emit attribution in the report's Methods/References and in `analysis_manifest.json`.

| Source | Used for | License | Commercial | Attribution | Share-alike |
|--------|----------|---------|------------|-------------|-------------|
| CZ CELLxGENE Census | Tumor scRNA-seq expression | Data **CC BY 4.0**; code (`cellxgene-census`) MIT | Yes | Yes | No |
| **Human Protein Atlas** | Normal-tissue RNA + IHC safety baseline | **CC BY-SA** (download files; site parts now CC BY 4.0) | **Yes** | **Yes** | **Yes** |
| Open Targets Platform | Tractability, locations, known drugs | **CC0 1.0** (integration layer) | Yes | Good practice | No |
| **ChEMBL** (via Open Targets, drugs) | Known-drug / mechanism evidence | **CC BY-SA 3.0 Unported** | **Yes** | **Yes** (URL + release version) | **Yes** |
| DepMap | Essentiality — **annotation only, never a gate** | **CC BY 4.0** | Yes | Yes | No |
| SURFY in-silico surfaceome | Genome-wide surfaceome + topology | Journal supplementary (Bausch-Fluck 2018) — cite | Cite | Yes | N/A |
| GTEx (optional) | Orthogonal normal-tissue RNA | **CC BY 4.0** | Yes | Yes | No |

> ⚠️ **Share-alike sources (commercial use OK, but conditions apply): ChEMBL (CC BY-SA 3.0) and
> Human Protein Atlas (CC BY-SA).** Both **permit commercial use** but require **attribution +
> share-alike**: any redistributed *adaptation* that embeds their data (e.g. a table carrying HPA
> nTPM/IHC values or ChEMBL drug records) must itself be released under a compatible **CC BY-SA**
> license with the required attributions. A report that merely **cites** these sources and reports
> derived scores (the normal deliverable) satisfies the license with attribution — share-alike only
> bites when you redistribute an adaptation of the underlying data. See
> [DATA_SOURCES.md](DATA_SOURCES.md).

## References

1. **CZ CELLxGENE Census** — CZI. https://chanzuckerberg.github.io/cellxgene-census/
2. **Surfaceome** — Bausch-Fluck D, et al. (2018) *PNAS* 115(46):E10988–E10997 (in-silico human surfaceome / SURFY).
3. **Human Protein Atlas** — Uhlén M, et al. (2015) *Science* 347(6220):1260419 (normal-tissue RNA + IHC protein baseline). https://www.proteinatlas.org/
4. **Open Targets** — Ochoa D, et al. (2023) *Nucleic Acids Res* 51(D1):D1302–D1310 (tractability, subcellular location, known drugs).
5. **DepMap** — Tsherniak A, et al. (2017) *Cell* 170(3):564–576 (essentiality — used here as annotation, not a gate).
6. **NSCLC surface-antigen ADCs** — c-MET (telisotuzumab vedotin) and TROP2 (datopotamab deruxtecan) — clinical proof of concept that validated surface antigens are non-essential.

**Detailed guides:** [references/scoring_methodology.md](references/scoring_methodology.md) | [references/census_atlas_guide.md](references/census_atlas_guide.md) | [references/hpa_baseline_guide.md](references/hpa_baseline_guide.md) | [references/tnbc_worked_example.md](references/tnbc_worked_example.md)
**Scripts:** [scripts/](scripts/)
