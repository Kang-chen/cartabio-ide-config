---
id: "skill_99b62aa3fd5ef6688b599c2a76b92313"
name: "single-cell-census-query"
description: "Use to query CZ CELLxGENE Census for a gene panel's expression across cell types or to run donor-level pseudobulk disease-vs-control differential expression from public single-cell atlases. Produces cell-type expression and case-control evidence for genes in a tissue."
category: "transcriptomics"
visibility: "public"
starting-prompt: "Query CZ CELLxGENE Census for the expression of my genes of interest across cell types in a tissue, run a case/control pseudobulk differential expression comparison for a disease vs. healthy, and generate a PDF report with an infographic, intro, methods, results, conclusions, figures, references, and next steps."
---

# Single-Cell Census Query: Expression Atlas + Pseudobulk Differential Expression

Query the **CZ CELLxGENE Census** for how a gene panel is expressed across cell types in one or
more tissues, then run a rigorous **donor-level pseudobulk differential expression** (DE) analysis
comparing a disease (case) against a control condition — all from public single-cell atlases — and
deliver a Phylo-branded PDF report.

This skill encodes a **tested, generalizable procedure**. Every biological specific (which genes,
tissue, disease) is a parameter, not a hard-coded value. It was validated end-to-end on a real
allergic-airway analysis; the scripts here are the generalized versions of that proven run.

---

## Scope

**Does:**
1. **Expression atlas** — pull a gene panel across all cell types in chosen tissue(s) from the
   Census `normalized` layer; summarize mean expression + % of cells expressing per cell type.
2. **Pseudobulk DE** — aggregate **raw** counts to donor × cell_type pseudobulk samples and run
   DESeq2 (global + per-cell-type) for case vs. control, with donor as the replicate unit.
3. **Report** — a Phylo PDF (infographic summary, intro, methods, results, figures, references,
   next steps), delegating the PDF mechanics to the `pdf-report-generation` skill.

**Does NOT** (delegates to sibling skills — reference, don't reimplement):
- Functional enrichment / pathway analysis of the DE results → `functional-enrichment-from-degs`,
  `pathway-enrichment`.
- Drug-target prioritization / genetic-evidence integration → `scrna-disease-drug-discovery`.
- Full scRNA preprocessing (QC, clustering, annotation) from raw droplet data →
  `scrnaseq-scanpy-core-analysis`.
- Finding datasets when the Census lacks the disease → `omics-dataset-retrieval` (GEO, etc.).
- Generic counts→DE on a user's own matrix (no Census) → `bulk-rnaseq-counts-to-de-deseq2`.

---

## Inputs (parameters — replace the placeholders, do not hard-code biology)

| Parameter | Meaning | Example |
|---|---|---|
| `GENE_PANEL` | HGNC gene symbols of interest (resolved to Ensembl IDs at runtime) | `["IL13","TSLP"]` |
| `TISSUES` | Census `tissue_general` value(s) for the atlas | `["lung","nose"]` |
| `CASE_LABEL` | Census `disease` value for the case group | `"chronic rhinitis"` |
| `CONTROL_LABEL` | Census `disease` value for the control group | `"normal"` |
| `ORGANISM` | Census organism | `"Homo sapiens"` (default) |
| `CENSUS_VERSION` | Pinned Census release (record for reproducibility) | e.g. `"2025-11-08"` |
| `MIN_CELLS_PER_SAMPLE` | Drop pseudobulk samples with fewer cells | `10` (default) |
| `MIN_DONORS_PER_GROUP` | Min donors per group for a cell type to be testable | `3` (default) |
| `DONOR_BATCH` | Donors per streaming read (memory control) | `8` (default) |
| `COVARIATES` | Optional design covariates (see extension) | `[]` or `["sex"]` |

**Alternate input (extensible):** if the user has their **own** single-cell `.h5ad` or a
donor × cell_type raw-count matrix, skip Step 3 (Census pull) and feed the matrix directly to
Step 4's pseudobulk build / Step 5's DESeq2. See `references/parameters.md` → "Bring-your-own data".

---

## Outputs (saved to `/mnt/results/`)

- `data/<panel>_expression_by_celltype.csv` — per-cell-type mean expression + % expressing.
- `data/pseudobulk_counts.csv`, `pseudobulk_coldata.csv`, `pseudobulk_var.csv` — pseudobulk matrix.
- `data/pseudobulk_DE_all_results.csv`, `pseudobulk_DE_significant.csv` — full + significant DE.
- `data/<panel>_DE_by_celltype.csv` — the gene-panel DE table across cell types.
- `figures/*.png` + `*.svg` — dotplots, volcano, gene-panel forest, DEG-count barplot, boxplots,
  heatmap (Phylo palette).
- `report_<title>.pdf` — the final Phylo-branded report.

---

## CRITICAL pre-flight: verify labels BEFORE analysis (missing-label protocol)

**The single most important lesson from the source analysis: the requested disease may not exist
in the Census.** (In the original run, "asthma" had zero cells — all 259 disease labels were
enumerated to confirm — so a documented proxy, chronic rhinitis, was used *with user confirmation*.)

**You MUST, before committing to any comparison:**
1. Enumerate available `disease` labels for the target tissue(s) and confirm `CASE_LABEL` and
   `CONTROL_LABEL` **both exist with primary-data cells**. Use `scripts/enumerate_labels.py`.
2. Enumerate available `tissue_general` values and confirm `TISSUES` exist.
3. Report per-(cell_type × group) **donor counts** so the user sees which cell types are testable
   (≥ `MIN_DONORS_PER_GROUP` donors per group) and how imbalanced the groups are.

**If `CASE_LABEL` is absent:** do NOT silently substitute. Surface the absence plainly, then either
- **propose a documented biological proxy** (a related condition present in the Census) and get the
  user's explicit confirmation before running, or
- point the user to `omics-dataset-retrieval` to source the disease from GEO/other repositories.

**Prefer a single shared dataset.** When both groups are available inside one `dataset_id`, run the
comparison within that dataset — this removes assay/batch confounding (the source run did exactly
this). If groups only exist across different datasets, say so and add dataset/assay as a covariate
(see extension) or flag the batch confound as a limitation.

---

## Workflow

Use `TodoWrite` to track these steps. Language policy: **Python only where required** (Census
access via `cellxgene-census`/`tiledbsoma` has no R equivalent), **R (DESeq2 + ggplot2)** for
statistics and figures.

### Step 1 — Resolve inputs & scope
- Resolve `GENE_PANEL` symbols → Ensembl `feature_id`s from the Census `var` table; warn on any
  unresolved/deprecated symbol (do not proceed with a silently-dropped gene).
- **Pin and record `CENSUS_VERSION`** (default to the latest stable release; print it into the
  report methods). Reproducibility depends on this.

### Step 2 — Pre-flight label verification (mandatory)
- Run `scripts/enumerate_labels.py` for the missing-label protocol above. Get user confirmation on
  any proxy. Decide single- vs multi-dataset comparison.

### Step 3 — Expression atlas query
- Run `scripts/query_expression_atlas.py`: pull an AnnData restricted to `GENE_PANEL` across
  `TISSUES` for `disease == CONTROL_LABEL` (normal reference) using the **`normalized`** layer.
  Restricting `var` to the panel keeps this tiny even across millions of cells.
- Compute per-cell-type **mean expression** and **% cells expressing**; save the CSV.

### Step 4 — Build donor × cell_type pseudobulk (raw counts)
- Run `scripts/build_pseudobulk.py`: stream the **`raw`** X matrix in donor-batches of
  `DONOR_BATCH`, summing counts per `donor_id || cell_type || disease` via a sparse
  group-indicator matmul (never load the full cells × genes matrix). Keep samples with
  ≥ `MIN_CELLS_PER_SAMPLE` cells.
- **Compute/memory:** this is the dominant cost. See "Compute guidance" — provision ≥32 GB and run
  as a **background** job for large datasets; estimate total time from the first donor-batch.

### Step 5 — Pseudobulk DESeq2 DE
- Run `scripts/run_pseudobulk_deseq2.R`: **GLOBAL** (collapse cell types within donor) + **per
  cell type** (only cell types with ≥ `MIN_DONORS_PER_GROUP` donors in **both** groups). Design
  `~ disease` (or `~ <covariates> + disease` if `COVARIATES` set), contrast case vs control,
  **BH-FDR < 0.05**. Save all + significant + the gene-panel-focused table.

### Step 6 — Figures
- Run `scripts/make_figures.py`: gene-panel **dotplots** (mean expr + % expressing across cell
  types), **volcano** (global DE), gene-panel **forest** (log2FC ± SE across cell types),
  **DEG-count barplot**, pseudobulk **boxplots** for the panel genes, top-DEG **heatmap**. Phylo
  palette; editable SVG text.
- **MANDATORY:** after saving each figure, run `Read(..., mode="media_output_check")` on the PNG
  and regenerate anything blank/clipped/overlapping before continuing (this caught real overlap
  bugs in the source run).

### Step 7 — Literature grounding (required)
- Run `LiteratureSearch` on the top DE genes and the gene-panel biology in the disease context to
  ground the interpretation in real papers. Cite with inline `[N]`; verify specifics before
  writing. Populate the report's **References** and inform **Next Steps**.

### Step 8 — Assemble the PDF report
- **Load and follow the `pdf-report-generation` skill** for all Phylo branding, layout, and
  validation mechanics — do not re-derive them. The report must contain, in order: an **infographic
  summary** (one-glance visual of the headline result), **Introduction**, **Methods** (incl. the
  pinned Census version, filters, thresholds, and any proxy/limitation), **Results** (figures +
  tables with captions), **Conclusions**, **References** (from Step 7), and **Next Steps**.
- **Image sizing (learned bug):** tall stacked figures overflow the frame at large widths. In the
  figure helper, bound **both** width and height (e.g. scale so height ≤ ~560 pt) — see
  `references/parameters.md` → "PDF figure sizing".
- **Validate:** page count ≥ 2, size > 5 KB, extractable text, then `media_output_check` on the PDF.

---

## Scientific caveats (state the relevant ones in the report)

- **Donor is the replicate unit.** Pseudobulk avoids pseudoreplication; never run per-cell DE
  across cells from few donors and call it powered.
- **Atlas uses `normalized`; DE uses `raw`.** DESeq2 requires integer counts. Do not feed
  normalized values to DESeq2.
- **Group imbalance / small case N.** Public atlases often have far more controls than cases (the
  source run had 15 case vs. 227 control donors). Report per-group donor N honestly and flag
  results as discovery-limited when the case group is small.
- **Batch/assay confounding.** Prefer within-dataset comparisons; otherwise adjust for
  dataset/assay or flag the confound.
- **Proxy conditions are proxies.** If a proxy disease is used, say so prominently — it is not the
  originally requested disease.
- **Gene symbol drift & genome build.** Resolve symbols → Ensembl IDs explicitly; the Census is a
  fixed build per version, so record the version.
- **Organism.** Defaults to human; other organisms are an untested extension.

---

## Compute guidance (size from evidence, not intuition)

- **Atlas query (Step 3):** minutes; memory scales with #cells × panel size (panels are small, so
  this stays light even across millions of cells).
- **Pseudobulk streaming (Step 4):** the dominant cost. As a real datapoint, a **~1M-cell /
  ~240-donor / ~60k-gene** dataset took **~65 min** and needed a **32 GB** machine. Use
  `ManageMachine` to provision **≥ 32 GB**, run the build as a **background** job, and **estimate
  total runtime from the first donor-batch** rather than guessing. The dense group×gene accumulator
  is `n_groups × n_genes × 8` bytes — check it fits before starting.
- **DESeq2 (Step 5):** minutes to tens of minutes, scales with #cell-types × #genes.
- Small pre-flight label queries (Step 2) are seconds–minutes on the default worker.

---

## Reference files
- `scripts/enumerate_labels.py` — pre-flight: disease/tissue labels + per-(cell_type×group) donor counts.
- `scripts/query_expression_atlas.py` — gene-panel × cell-type expression (normalized layer).
- `scripts/build_pseudobulk.py` — streaming donor×cell_type raw-count aggregation.
- `scripts/run_pseudobulk_deseq2.R` — global + per-cell-type DESeq2 with the confirmed filters.
- `scripts/make_figures.py` — all figures, Phylo palette, media-check ready.
- `references/parameters.md` — every threshold/default, rationale, and how to change it; BYO-data
  path; PDF figure-sizing fix.
- `references/resource_map.md` — Biomni resources this skill uses and the sibling skills it defers to.
