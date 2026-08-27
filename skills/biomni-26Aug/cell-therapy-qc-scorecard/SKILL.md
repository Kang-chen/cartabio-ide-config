---
id: "skill_30aae8fada7b78e30f7275374cebbe68"
name: "cell-therapy-qc-scorecard"
description: "Use to QC, release-test, or compare scRNA-seq lots of cell-therapy products such as CAR-T, CAR-NK, iPSC-derived, or primary-cell products. Scores identity/purity, residual pluripotency, off-target lineages, maturity, and technical quality as GREEN/AMBER/RED."
category: "transcriptomics"
visibility: "public"
starting-prompt: "Run a single-cell RNA-seq QC release scorecard on my cell-therapy product. Score identity/purity, residual pluripotency, off-target lineage, maturity, and technical QC per unit into a GREEN/AMBER/RED call, and generate a Phylo-branded PDF report with an infographic summary plus machine-readable tables."
---

# Cell-Therapy scRNA-seq QC Release Scorecard

Turn single-cell RNA-seq of a cell-therapy product into a lot-release **scorecard**: for each
unit (lot/batch/sample), score five identity & safety modules and roll them up to a
**GREEN / AMBER / RED** call, then deliver a Phylo-branded PDF (with an infographic summary
page) and machine-readable tables.

This skill sits **on top of** the standard scRNA-seq pipeline. It does not reinvent QC,
normalization, clustering, or annotation — it calls the `scrnaseq-scanpy-core-analysis` scripts
for those and adds the **release-testing scoring layer** (identity anchoring, residual-pluripotency
detection, off-target lineage detection, maturity indexing, and threshold-based GREEN/AMBER/RED
calls) plus the branded reporting layer.

## When to Use This Skill

Use when the user wants to **QC, release-test, characterize, or compare** a **cell-therapy product**
measured by scRNA-seq, including:

- iPSC/ESC-derived products: iNK, iT, iCAR-T, iCAR-NK, iMacrophage, iCardiomyocyte, iHepatocyte,
  iBeta-cell, iNeuron, iMSC, iRPE, etc.
- Engineered primary-cell products: CAR-T, CAR-NK, TCR-T, TILs, gene-edited cells.
- Any question of the form: *purity? identity? residual undifferentiated cells? off-target
  cell types? maturity? is this lot releasable? how do batches compare?*

Trigger phrases include (even without the word "QC"): "release test my iPSC-NK lot", "is this
CAR-T product clean", "check for residual iPSCs / residual pluripotency", "what's the purity of
my differentiation", "compare these manufacturing batches", "characterize my cell product",
"off-target cell types in my product".

**Don't use for:** bulk RNA-seq (use `bulk-rnaseq-counts-to-de-deseq2`); generic exploratory
scRNA-seq with no product/release framing (use `scrnaseq-scanpy-core-analysis`); disease/drug
target discovery (use `scrna-disease-drug-discovery`); spatial data.

## What Makes This Different From Generic scRNA-seq QC

Generic QC asks "which cells are low quality?" **Release QC asks "is this product what it claims
to be, is it pure, is it safe, and is it mature enough to release?"** That requires product-aware
logic that generic pipelines don't have:

- **Identity is expression-anchored, not cluster-label-based.** A lot is judged on whether cells
  express the *target-cell* effector/identity program, not on how many Leiden clusters annotate to
  the target type.
- **Safety modules are product-type-conditional.** Residual-pluripotency scoring only runs for
  iPSC/ESC-derived products; a maturity module only runs when an immature→mature axis is defined
  for the target cell type.
- **Every module is expressed as a GREEN/AMBER/RED release call** against defensible thresholds,
  and the unit's overall call is the **worst active module** (a single RED fails the lot).

## Inputs

**Product description (required, drives all adaptive logic):**
- **Target cell type** — e.g. "NK cell", "cardiomyocyte", "CD8 T cell", "hepatocyte". Free text;
  the skill resolves it to a marker panel (see Step 3).
- **Source** — `ipsc`, `esc`, or `primary`. Determines whether the residual-pluripotency module
  (Module B) runs. Default: infer from the description; if it mentions iPSC/iOP/"iPSC-derived"/
  "reprogrammed" → `ipsc`; if "ESC"/"embryonic stem" → `esc`; else `primary`.
- **Engineering (optional)** — e.g. "MSLN-CAR", "anti-CD19 CAR", transgene names. Recorded in the
  report; transgene detection is reported if the transgene is present as a feature.

**Data (required) — any one of:**
- Local files: one or more 10x directories, `.h5` (CellRanger), or `.h5ad`. Multiple units = one
  per lot/batch/sample.
- A **GEO accession** (e.g. `GSE291599`): the skill downloads the supplementary matrices and treats
  each GSM/sample as a unit. See `references/marker_panel_sources.md` for the reliable GEO fetch route.

**Metadata (optional):** a CSV mapping unit → {type/label, expected species, condition}. If absent,
each input file becomes a unit named by its filename/GSM.

**Species:** `human` or `mouse` (default `human`). For **multi-species references** (e.g. a
human product xenografted into mouse, aligned to a combined `GRCh38_mm10` reference), the skill
splits reads by species and keeps the product species (default: keep human cells with
`human_frac > 0.9`). Configurable per unit.

## Outputs

Written under `/mnt/results/<run_name>/` (default `run_name = cell_therapy_qc_scorecard`):

**PDF report** — `report_<product>_qc_release.pdf`
- Page 1: **infographic summary** — one card per unit with its overall GREEN/AMBER/RED call and a
  compact 5-module status strip.
- Then: Introduction, Methods, Results (per-module figures + tables), Scorecard, Discussion,
  Limitations, Next Steps, References. Phylo-branded (see `references/report_layout.md`).

**Machine-readable tables** (CSV) in `tables/`:
- `01_species_composition.csv` — per unit, fraction of reads/cells by species (multi-species runs).
- `02_qc_filtering_summary.csv` — per unit: n_cells in/out, retention %, doublet n & rate, median
  counts/genes/mito.
- `03_per_unit_qc_metrics.csv` — per unit: every module's headline metric (e.g. % target-cell
  purity, % residual pluripotent, % off-target, % mature, % contamination).
- `04_per_cell_module_scores.csv` — per-cell flags & scores across all units (identity class,
  pluripotency call, off-target lineage, maturity index, etc.).
- `05_scorecard_calls.csv` — per unit × module GREEN/AMBER/RED + overall call.
- `06_thresholds_reference.csv` — the exact numeric thresholds used (with provenance).
- `07_scorecard_summary_readable.csv` — human-readable one-row-per-unit summary.

**Per-unit objects** in `h5ad/`: `<unit>_processed.h5ad` (normalized + all module flags in `.obs`).

**Figures** in `figures/` (PNG + SVG): QC distributions, scorecard heatmap, per-module UMAP overlays,
cross-lot comparison.

## Adaptive Module Set

The skill always computes Modules A, C, E; it computes B and D **only when applicable**. See
`references/qc_release_methodology.md` for the full rationale and `references/thresholds_defaults.md`
for the default cut points.

| Module | What it measures | When it runs | Default thresholds (GREEN / AMBER / RED) |
|--------|------------------|--------------|-------------------------------------------|
| **A. Target-cell identity & purity** | % cells expressing the target-cell identity/effector program | **Always** | ≥90% / 75–90% / <75% |
| **B. Residual pluripotency** | % cells that are residual undifferentiated iPSC/ESC | **iPSC/ESC only** | <0.01% / 0.01–0.1% / >0.1% |
| **C. Off-target lineage** | % cells committed to a non-target lineage | **Always** | <2% / 2–10% / >10% |
| **D. Target-cell maturity** | % target cells that are mature vs. immature/progenitor | When a maturity axis exists for the target | ≥60% / 40–60% / <40% |
| **E. Technical QC** | retention %, cross-species contamination %, mito % | **Always** | composite (see methodology) |

**Overall call per unit = the worst active module.** A single RED fails the lot.

Thresholds are **defaults, not universal standards** — regulatory lot-release criteria are
product-specific and must be set with the sponsor. The skill prints thresholds in the report and in
`06_thresholds_reference.csv` so they are auditable and overridable via config.

## Standard Workflow

Work through `scripts/` in order. Each script is runnable standalone and prints a `✓` verification
line. The scripts call `scrnaseq-scanpy-core-analysis` for the heavy lifting, so make its `scripts/`
directory importable (Step 0 handles this).

**Step 0 — Setup & config** | `scripts/setup_qc_release.py`
Builds the run config (product description → resolved source/species/module set), makes both this
skill's `scripts/` and the `scrnaseq-scanpy-core-analysis` `scripts/` importable, and creates the
output tree. Verify packages with direct imports.

```python
from setup_qc_release import build_config
cfg = build_config(
    product="iPSC-derived NK cell (MSLN-CAR)",   # free text
    target_cell="NK cell",
    source="ipsc",            # or "esc" / "primary"; omit to auto-infer from `product`
    species="human",
    inputs=["GSE291599"],      # GEO accession OR list of local paths
    run_name="ipsc_nk_qc",
)
```

**Step 1 — Load units** | `scripts/load_units.py`
Loads each unit into an AnnData (local 10x/H5/H5AD **or** GEO download). Standardizes gene symbols,
records raw cell counts.

**Step 2 — Harmonize species** | `scripts/harmonize_species.py`
For multi-species references: split features by species prefix, compute per-cell species fraction,
keep the product species (default `human_frac > 0.9`), write `01_species_composition.csv`.
No-op for single-species data.

**Step 3 — Derive marker panels** | `scripts/derive_marker_panels.py`
Resolves the target cell type and all off-target lineages to marker gene panels using **CellMarker2**
as the primary source, refined by `LiteratureSearch` for product-specific markers
(identity anchors, maturity axis, pluripotency panel). Emits the panels used so
they are auditable. See `references/marker_panel_sources.md`.

**Step 4 — Per-unit QC** | reuse `scrnaseq-scanpy-core-analysis` scripts
Per unit: `calculate_qc_metrics` → `batch_mad_outlier_detection` → `run_scrublet_detection` →
`filter_by_mad_outliers`, then `run_standard_normalization`. Writes `02_qc_filtering_summary.csv`.
Clustering/UMAP are optional (only needed for figures); identity is expression-anchored, not
cluster-based.

**Step 5 — Score modules** | `scripts/score_modules.py`
Computes per-cell flags for every **active** module (A/C/E always; B if iPSC/ESC; D if a maturity
axis exists) using the panels from Step 3. **Read `references/qc_release_methodology.md` before
editing thresholds or logic** — the identity-anchor and pluripotency-specificity logic encode
hard-won corrections (see Scientific Caveats). Writes `04_per_cell_module_scores.csv`.

**Step 6 — Build scorecard** | `scripts/build_scorecard.py`
Aggregates per-cell flags to per-unit headline metrics, applies GREEN/AMBER/RED thresholds, computes
the overall (worst-active-module) call. Writes `03_per_unit_qc_metrics.csv`, `05_scorecard_calls.csv`,
`06_thresholds_reference.csv`, `07_scorecard_summary_readable.csv`.

**Step 7 — Figures** | `scripts/make_figures.py`
QC distributions, scorecard heatmap (call text in every cell), per-module UMAP overlays, cross-lot
comparison bars. **Media-check every figure** with `Read(mode="media_output_check")` and regenerate
if blank/clipped. Uses the dtype-safe boolean helper (see Scientific Caveats).

**Step 8 — PDF report** | `scripts/generate_report.py`
Assembles the Phylo-branded PDF with the infographic summary page first. Follows the
`pdf-report-generation` skill conventions (see `references/report_layout.md`). Validates with `pypdf`
(≥2 pages, size > 5 KB, extractable text) and `Read(mode="media_output_check")`.

## Configuration Schema

`build_config()` returns / accepts a dict. Any field can be overridden.

```python
{
  "product": str,             # free-text product name (report title)
  "target_cell": str,         # e.g. "NK cell" — resolved to a marker panel
  "source": "ipsc"|"esc"|"primary",   # gates Module B
  "engineering": str|None,    # e.g. "MSLN-CAR"; transgene features reported if present
  "species": "human"|"mouse",
  "multispecies": bool,       # if True, run species split (Step 2)
  "keep_species_frac": 0.9,   # keep cells with product-species frac above this
  "inputs": [str],            # GEO accession(s) OR local file/dir paths
  "unit_metadata": str|None,  # optional CSV: unit -> {type,label,condition}
  "modules": {"A":True,"B":auto,"C":True,"D":auto,"E":True},  # override to force on/off
  "thresholds": {...},        # override any module's GREEN/AMBER/RED cut points
  "run_name": str,            # output folder under /mnt/results/
}
```

## Compute

`worker-0` (default) is sufficient for typical lots: ~4–8 GB RAM per unit at a few thousand cells,
minutes per unit. No HPC/GPU needed. For many large units, process sequentially (the scripts do) or
scale up with `ManageMachine`. Reuse the persistent kernel state across steps — don't reload data
you already have in memory.

## Scientific Caveats (read before trusting or editing)

These encode corrections learned from a real iPSC-NK release run. They are the difference between a
plausible-looking scorecard and a correct one.

1. **Identity must be expression-anchored, not signature-score-gated.** `sc.tl.score_genes` applies a
   background correction; in a product where the target program dominates the transcriptome (e.g. NK
   effectors), the "background" is high and the corrected identity score goes **negative even in
   obvious target cells**. Anchor identity on *raw expression* of a small set of target-cell markers
   (≥1 marker detected), then classify fidelity — do **not** gate identity on a positive signature
   score. (Module A.)

2. **Residual-pluripotency calls need co-expression specificity, not single markers.** POU5F1 (OCT4)
   and DNMT3B appear sporadically in proliferating non-pluripotent cells; a naïve "≥2 pluripotency
   markers" rule flags proliferating target cells as residual iPSCs. Require co-expression of
   *specific* pluripotency TFs (e.g. NANOG/LIN28A/TDGF1/PRDM14/UTF1/ZSCAN10/SALL4), exclude cells
   that are target-identity-positive, and validate against a per-unit shuffled-null score threshold.
   A count of cells co-expressing the canonical POU5F1+NANOG+LIN28A triad is the strongest evidence.
   (Module B.)

3. **Off-target calls must be restricted to target-negative cells.** iPSC-derived products often carry
   "leaky" off-lineage transcripts while remaining target cells. Count a cell as off-target only if it
   co-expresses ≥2 markers of a non-target lineage **and** is target-anchor-negative. Otherwise you
   mislabel lineage-aberrant-but-real target cells as contamination. (Module C.)

4. **Residual pluripotency is a limit-of-detection statement.** With no cells passing a specific,
   validated pluripotency call, report "below detection at this depth/cell number" — not "0% / absent".
   Single-cell scRNA-seq LOD for rare residual iPSCs is far coarser than ddPCR/qPCR release assays
   (which reach ~0.001–0.01%); state this explicitly and recommend an orthogonal assay for true
   release. (Module B / Limitations.)

5. **dtype-safe boolean masks.** Integer (0/1) `.obs` columns break Python's `~` bitwise-NOT
   (`~[0,0,1] → [-1,-1,-2]`, which then mis-indexes). Always coerce to bool before masking:
   ```python
   def B(ad, col): return ad.obs[col].values.astype(bool)
   ```
   Used throughout `score_modules.py` and `make_figures.py`.

6. **Thresholds are defaults, not standards.** No universal numeric release threshold exists for most
   of these modules; they are product- and sponsor-specific. Always surface the thresholds used and
   frame RED/AMBER/GREEN as guidance requiring sponsor sign-off. (See `references/thresholds_defaults.md`.)

7. **Doublets and cross-species contamination are honest inputs, not cosmetics.** Report actual
   per-unit doublet rates and species-contamination fractions; do not round them away. Pre-filtered
   matrices (e.g. CellRanger-filtered) usually show low Scrublet rates — note that context.

## Related Skills

**Calls:** `scrnaseq-scanpy-core-analysis` (QC/normalize/cluster scripts), `pdf-report-generation`
(report conventions), and `LiteratureSearch` (marker/threshold grounding). **Alternative for R
users:** `scrnaseq-seurat-core-analysis` (this skill
is Python because its validated scoring logic and the reporting stack are Python). **Downstream:**
`functional-enrichment-from-degs` (characterize off-target populations).

## References (methodology & provenance)

- `references/qc_release_methodology.md` — the five modules, why each matters, exact scoring logic.
- `references/product_type_registry.md` — target-cell → marker-panel + maturity-axis registry, and
  which source types trigger Module B.
- `references/marker_panel_sources.md` — CellMarker2 usage, LiteratureSearch grounding, GEO fetch route.
- `references/thresholds_defaults.md` — default GREEN/AMBER/RED cut points and their provenance.
- `references/report_layout.md` — Phylo PDF layout, infographic summary spec, brand tokens.
