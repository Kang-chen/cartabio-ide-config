# Scoring Methodology — Cell-Surface Target Discovery

## Design principle: specificity + safety, not essentiality

Antibody modalities (ADC, CAR-T, bispecific, radioligand) recognize an antigen on the
**outside** of the plasma membrane. The therapeutic mechanism does **not** require the
target to be essential for tumor survival. Therefore this skill departs deliberately
from dependency-driven (DepMap) target discovery:

| Axis | Small-molecule / degrader | Antibody modality (this skill) |
|------|---------------------------|--------------------------------|
| Primary selection | Tumor essentiality (DepMap) | Tumor-surface specificity + normal-tissue safety |
| Essentiality | Required | **Annotation only** |
| Topology | N/A | Must have an accessible extracellular ectodomain |
| Failure mode if misapplied | — | Essentiality gate enriches housekeeping genes (ATP1A1, CDH1) → on-target/off-tumor toxicity, and discards non-essential validated antigens (TROP2, c-MET, HER2) |

## Score shape

```
final_score = tumor_quality  ×  safety_factor  ×  consensus_multiplier
```

Safety is **multiplicative**, not a linear weight: a tumor-specific antigen that is also
expressed in a vital organ is disqualified regardless of how specific it looks within the
tumor microenvironment. (A 0.25 linear safety weight cannot veto a high TME-specificity
score — that is precisely how a housekeeping gene like CDH1 can top a naive specificity ranking.)

### tumor_quality (weights sum to 1.0; adaptive reweighting over available components)

| Component | Weight | Definition |
|-----------|--------|------------|
| specificity | 0.30 | `clip(log10(spec_vs_tme)/2, 0, 1)`; `spec_vs_tme = min(epi/CAF, epi/immune)` (median across datasets). 10× → 0.5, 100× → 1.0 |
| magnitude | 0.20 | `clip(epithelial_mean / 2.0, 0, 1)` (log-normalized CP10k) |
| homogeneity | 0.20 | `epithelial_pct / 100` — fraction of malignant cells expressing; low homogeneity ⇒ antigen-negative escape risk |
| accessibility | 0.15 | ectodomain accessibility: high 1.0 / partial 0.6 / low 0.3 (`none` gated upstream) |
| tractability | 0.15 | Open Targets antibody tractability bucket → 0–1 |

### safety_factor (therapeutic index)

`safety_score ∈ [0,1]` from `normal_tissue_safety.compute_therapeutic_index()`:
conservative min of protein-level and RNA (nTPM) safety across vital organs (heart, lung,
liver, kidney, brain, marrow, GI, pancreas, adrenal). 1 = not detected in vital tissue;
→ 0 as vital-organ expression rises. **Unassessed → 0.7** (neutral) and flagged
`safety_unassessed=True` so reviewers know the TI is unconfirmed.

**Baseline source (dual-signal HPA).** The normal-tissue baseline
(`target_baseline_expression_long.csv`) is built by `scripts/hpa_baseline.py` from two
Human Protein Atlas bulk downloads — RNA consensus nTPM and IHC protein levels — because
Open Targets Platform v4 removed `Target.expressions`. RNA drives `_rna_safety`
(nTPM <1 → 1.0, <10 → 0.7, <50 → 0.4, else 0.1); IHC protein level drives `_protein_safety`
({Not detected:1.0, Low:0.7, Medium:0.4, High:0.1}); the per-gene safety is the min of
whichever signals exist. A gene with only one signal uses that signal (the other is
unassessed, **not** fabricated); a gene absent from HPA → `safety_score = NaN` → neutral 0.7.
See `hpa_baseline_guide.md`.

**Honesty rule (report).** Always scope safety distributions to the correct set:
the *assessed* subset (genes with a computed HPA score) vs the *full scored set* (in which
un-annotated genes sit at the 0.7 neutral default). Report the count/% at the neutral
default, and never present the assessed-subset distribution as if it described the full set.

### consensus_multiplier (reproducibility across atlases)

`0.5 + 0.5·min(n_datasets_enriched, 3)/3`. A gene enriched in ≥3 atlases → 1.0; in 1 atlas
→ 0.67; in none → 0.5. Rewards reproducible targets, penalizes single-study artifacts.
A gene is "enriched" in a dataset when `spec_vs_tme ≥ 3` and `epithelial_mean ≥ 0.1`.

### Tiers

Tier 1 ≥ 0.55 · Tier 2 0.35–0.55 · Tier 3 < 0.35. Topology `none` (cytoplasmic / ER /
secreted-ECM) is excluded **before** scoring.

## Topology gate + surface confirmation (per-gene, never blanket)

The topology gate (`surfaceome_filter.apply_topology_filter`) is only meaningful if each
candidate carries a **per-gene** localization / ectodomain-accessibility call. For genome-scale
runs, `load_surfy_surfaceome()` derives these from SURFY Table S3 (topology string → non-cytoplasmic
ectodomain length; TM count / Almen class → accessibility; UniProt subcellular → localization). It is
a defect to assign every SURFY member `localization='plasma_membrane'` — the gate then excludes
nothing. Guardrail: on a genome-scale candidate set (≥200 genes) the filter **raises** if it removes
nothing ("a gate that never fires is not a gate").

Each scored candidate also gets a `surface_confirmation` label distinguishing confirmed
plasma-membrane residency from unconfirmed / contact-site predictions:

| Value | Condition | Meaning |
|-------|-----------|---------|
| `confirmed_experimental` | SURFY source = CSPA positive-training-set or GPI (UniProt); or a curated MS-validated seed entry | Experimentally supported surface protein |
| `confirmed_ot` | Open Targets `subcellularLocations` reports plasma-membrane | Independent database confirmation |
| `unconfirmed` | machine-learning surface prediction with neither of the above | Hypothesis; needs wet-lab surface confirmation |

`is_unconfirmed_surface` is `True` for `unconfirmed`. The count of unconfirmed candidates (overall and
within the top 20) is emitted in `report_facts.json` `topology` and must appear in the report — an
unconfirmed hit (e.g. an ER–PM contact-site protein whose only extracellular exposure is a few
residues) is never presented with the same confidence as a confirmed antigen. This is a labelling +
reporting step, not a change to the composite score.

## Annotation, not scored

- **DepMap essentiality** (`depmap_mean_gene_effect`) — reported for biology; never a gate.
- **Known-drug flag / max clinical phase** — raises reviewer confidence and powers the
  validation harness, but does not inflate the (novelty-oriented) score, so novel candidates
  are not buried under approved antigens.

## Validation harness

`references/known_surface_targets.csv` is scored by the identical pipeline. The report states:
- **recall@10 / @20 (pre-registered — the headline)** — how many *pre-registered* core validated
  targets land in the top K (of those scored). The validated set is **locked before ranking**: each row
  carries `date_added` and `provenance` (`pre_registered` | `added_post_ranking`), and the lock date is
  recorded (`HARNESS_LOCK_DATE`; `metrics.harness_locked_date`). A benchmark that admits the discovery
  it validates is circular, so a target promoted into the harness *after* ranking never enters the
  headline number.
- **augmented recall (only when the harness was augmented)** — if any core row is
  `added_post_ranking`, recall over the full (pre-registered + promoted) core is reported **separately
  and labelled** (`recall_augmented_at_10/20_str`, `harness_augmented_after_ranking=True`,
  `core_posthoc_genes`). It is never reported as the headline alone; `export_results` **raises** if the
  harness was augmented but only one recall figure is present.
- **excluded-by-topology count** — `n_known_core_excluded_topology` counts only core genes
  (`recall_core=1`) excluded by topology; `n_negative_controls_excluded_topology` counts
  negative controls (`clinical_status='not_a_target'`) excluded by topology. These are
  separate keys so a cytoplasmic negative control like CGN can never be conflated with a
  core validated target.
- **negative controls** — CDH1 / ATP1A1 / EPCAM should rank **low**; if they rank high, the
  safety or topology layer is misconfigured. Treat a failed harness as a pipeline failure,
  not a result.

### Negative-control verdict (derived, not chosen)

Each cautionary negative control (`clinical_status='not_a_target'`) receives a computed
verdict based on its rank percentile among all scored candidates:

| Verdict | Condition | Meaning |
|---------|-----------|---------|
| `excluded_topology` | Unscored due to topology | Correctly excluded by the topology filter |
| `ranks_high` | `rank / n_scored ≤ 0.25` | **FAIL** — control ranks in the top 25% |
| `ranks_mid` | `0.25 < percentile ≤ 0.50` | Borderline; not a hard fail |
| `ranks_low` | `percentile > 0.50` | Correctly demoted |

`negative_control_verdict` is `FAIL` if any control is `ranks_high`, else `PASS`. The
`negative_control_statement` names each offender with its rank. Both are emitted in
`validation_metrics.json` and `report_facts.json` under `negative_controls`.

### Holdout precision caveat

Recall@K is computed on the `recall_core=1` validated targets on a single fixed split.
`holdout_resolution_pp = round(100 / n_core_scored, 1)` gives the percentage-point
resolution of the metric (e.g. 4 scored core targets → 25.0 pp per target). No repeated
cross-validation or bootstrap interval is computed. The `holdout_caveat` string is emitted
in `validation_metrics.json` and `report_facts.json` under `validation`.

## Protein evidence (strict definition)

`has_protein_evidence` is computed from two signals already merged into the scored
DataFrame — no new data source is fetched:

| Column | Source | Meaning |
|--------|--------|---------|
| `has_ihc_protein_measurement` | `vital_protein_max` (from `normal_tissue_safety.py` / HPA IHC download) | `True` when HPA IHC protein measurement was performed for this gene (non-null `vital_protein_max`) |
| `is_plasma_membrane` | `annotate_targets.py` (Open Targets `subcellularLocations`) | `True` when OT confirms plasma-membrane localization |
| `has_protein_evidence` | derived | `has_ihc_protein_measurement OR is_plasma_membrane` |
| `protein_evidence_source` | derived | `'HPA IHC'` / `'OT plasma-membrane localization'` / `'both'` / `'none'` |

**A computed HPA safety score derived from RNA alone is NOT protein evidence.** The RNA
nTPM signal drives `_rna_safety` and the safety factor, but it is bulk RNA, not a protein
measurement. Only `has_ihc_protein_measurement` counts as protein-level validation. This
prevents the category error of citing an RNA-derived safety score as protein evidence.

## Cohort cell counts (analysed vs discovered)

The headline cell count must be the number of cells the analysis actually used, not the discovery
catalogue. `census_pull.pull_compartment_expression` subsamples each atlas (default ≤20,000 cells)
and drops atlases with <20 epithelial cells, then writes `cohort_cell_counts.csv` (per-atlas ×
per-compartment) and `cohort_summary.json` (`n_cells_discovered_full`, `n_cells_analyzed`,
`cohort_statement`, per-atlas ledger). `export_results._cohort_facts` re-derives the analysed total
from `compartment_expression.csv` (one count per dataset × compartment, summed over the four
compartments), and `_assert_cohort_consistent` **raises** if the reported analysed count does not
match. `report_facts.json` `cohort` therefore always reports discovered vs analysed side by side with
the per-atlas breakdown; the report quotes it verbatim.

## Coverage report (annotation coverage vs positive rate)

`coverage_report.csv` (from `export_results.py`) distinguishes two quantities that were
previously conflated under a single "coverage" label:

| Field | Meaning | Applies to |
|-------|---------|------------|
| `n_annotated` / `percent_annotated` | How many candidates have any (non-null) value for this column | All columns |
| `n_positive` / `percent_positive` | How many candidates are `True` | Boolean columns only; null for non-boolean |

For example, `is_plasma_membrane` and `has_known_drug` are fully populated (100% annotated)
but have different positive rates (e.g. 50% and 30%). The old single "coverage" number
reported the positive rate as if it were annotation coverage — a mislabel under a section
titled "Coverage Honesty."

## Rank-stability check

`rank_stability_check()` (in `score_targets.py`) perturbs two independent axes and reports
Spearman rho, top-K Jaccard, and a derived verdict for each comparison:

### Axis 1: therapeutic-index safety-aggregation rule (`ti_safety_weighting`)

Recomputes `safety_factor` from the raw `vital_protein_max` and `vital_rna_max` signals
under three alternative aggregation rules:

| Rule | Definition |
|------|------------|
| `conservative_min` | `min(protein_safety, rna_safety)` — the default |
| `lenient_mean` | `mean(protein_safety, rna_safety)` — averages whichever signals exist |
| `strict_max_penalty` | `min(protein_safety, rna_safety)` — same as conservative but applied at signal level |

### Axis 2: tumor-quality composite weights (`tumor_quality_weighting`)

Recomputes `tumor_quality` from the five `score_*` columns under three alternative weight sets:

| Weight set | specificity | magnitude | homogeneity | accessibility | tractability |
|------------|-------------|-----------|-------------|---------------|--------------|
| `equal` | 0.20 | 0.20 | 0.20 | 0.20 | 0.20 |
| `specificity_heavy` | 0.50 | 0.15 | 0.15 | 0.10 | 0.10 |
| `homogeneity_heavy` | 0.20 | 0.15 | 0.35 | 0.15 | 0.15 |

### Stability verdict thresholds

| Verdict | Condition |
|---------|-----------|
| `robust` | `rho ≥ 0.90` AND `jaccard ≥ 0.90` |
| `moderately_sensitive` | `rho ≥ 0.70` (but not robust) |
| `sensitive` | `rho < 0.70` |

`stability_verdict_overall` is the worst verdict across all comparisons. The
`stability_statement` quotes the worst case with its rho and Jaccard. Both are carried into
`therapeutic_index_stability.json`, `therapeutic_index_stability.csv` (with a `dimension`
column), and `report_facts.json` under `stability`. The verdict word is computed, not
chosen — do not call the ranking "robust" in the report unless `stability_verdict_overall`
says so.
