---
id: "skill_dfc4fd9cdc6b7fba7c880cc7f87ecd7f"
name: flow-cytometry-analysis
description: "Use for automated analysis of flow, mass, CyTOF, or spectral cytometry FCS files. Covers acquisition and spillover QC, compensation, arcsinh/logicle transforms, permissive control-aware gating, FlowSOM/CATALYST clustering, immunophenotype annotation, cross-sample quantification, manual-gate reconciliation, and diffcyt differential abundance."
category: proteomics_metabolomics
visibility: public
starting-prompt: >
  I have flow/CyTOF FCS files — identify and quantify cell populations across my samples and make
  a PDF report with an intro, methods, results, conclusions, figures, and next steps.
---

# Cytometry Clustering & Annotation

End-to-end workflow that turns raw flow or mass-cytometry (CyTOF) data into annotated cell
populations, quantifies them across samples, optionally benchmarks the automated populations against
manual gates, optionally runs differential-abundance testing, and produces a Phylo-branded PDF
report with an infographic.

The pipeline is **R / Bioconductor** (CATALYST + FlowSOM + ConsensusClusterPlus + diffcyt, with
flowCore for FCS I/O and transforms). The **report + infographic** step is Python (ReportLab via the
`pdf-report-generation` skill + `GenerateImage`).

---

## Scope

**Does:** modality-aware QC/pre-gating → transform (+ compensation for flow) → unsupervised
clustering ("automated gating") → cell-type annotation → cross-sample abundance quantification →
(optional) benchmarking vs manual gates → (optional) differential abundance → PDF report.

**v2.2.0 additions (diagnostics-on / removal-opt-in):** time-based acquisition QC (flow-rate,
signal-stability, and margin-event checks over the Time channel); spillover **condition-number**
diagnostics plus optional **external** compensation matrices; **batch-aware** cutoff harmonization;
an opt-in **OpenCyto / flowWorkspace** hierarchical-gating backend (real `GatingSet` +
`gatingTemplate`) alongside the built-in engine; and **CyTOF bead normalization**
(`CATALYST::normCytof`). Time-QC and spillover diagnostics **run and report by default but remove no
events**; harmonization, OpenCyto, and bead normalization are **fully opt-in**. All v2.1.0 behavior
is unchanged when the new flags are left at their defaults.

**Does NOT:**
- Interactive 2D manual polygon gating (this replaces it with clustering-based automated gating).
- **Targeted cytotoxicity dose-response assays** (ADCC/CDC/ADCP: gate a marker-defined target
  population → % viability-dye⁺ "DEAD" → fit a 4PL/Hill dose-response and AUC per antibody/compound).
  That is a *sequential targeted-gating* workflow, not unsupervised clustering — **do not silently
  substitute clustering for it.** This skill's clustering-based gating is the wrong tool for a per-well
  %DEAD-vs-dose readout. If asked for one, say so explicitly. (The gating lessons here — permissive
  scatter gates, mandatory manual-gate reconciliation, baseline sanity checks — still apply and were in
  fact learned from exactly such an ADCC assay; see `references/qc_gating.md`.)
- **Spectral unmixing** — raw spectral data (unmixing pending) is detected and refused; **already-unmixed** spectral data (Cytek Aurora / Sony ID7000 standard output, identified by named fluorophore/marker channels) is accepted and processed as fluorescence flow.
- scRNA-seq → use `scrnaseq-scanpy-core-analysis` / `scrnaseq-seurat-core-analysis`.
- Bulk transcriptomics/proteomics clustering → use `bulk-omics-clustering`.

---

## Inputs

| Input | Description |
|---|---|
| `.fcs` files | One or many. Flow or CyTOF. Real experimental data (needs QC) or benchmark data. |
| `metadata.csv` (multi-sample) | Columns: `file, sample_id, group, batch, patient` (see `assets/metadata_template.csv`). `group`/`batch` drive differential abundance; extras are optional. |
| `controls.csv` (optional) | Unstained/FMO controls to anchor positive/negative cutoffs: `channel, control_file, control_type{unstained\|fmo}, percentile` (see `assets/controls_template.csv`). Pass via `--controls`. |
| External spillover matrix (optional) | A pre-computed compensation/spillover matrix as CSV (channels × channels, header + matching row names) for when the FCS lacks a usable embedded `$SPILL`. Pass via `--spillover`; aligned to the panel by channel-name intersection and its **condition number** reported. |
| OpenCyto gating template (optional) | A `gatingTemplate` CSV (`alias,pop,parent,dims,gating_method,gating_args,…`) for the opt-in `--gate-engine opencyto` backend. If omitted, a shipped modality default is used; supplying `--gate-template` auto-selects the opencyto engine. |
| Edited `gating_thresholds.csv` (optional) | Reviewed cutoffs from PASS 1 applied in PASS 2 (`--thresholds` with `--gate-review apply`, or just `--thresholds` which skips the propose-stop); schema in `assets/gating_thresholds_template_example.csv`. |
| HDCytoData dataset name | e.g. `Levine_32dim` — benchmark data with ground-truth labels; installs via BiocManager. |
| Per-cell label column (optional) | Manual-gate labels (HDCytoData `population_id`, or a user column) enable benchmarking. |
| Manual-gating **statistics** export (optional, strongly encouraged) | A FlowJo *Export Statistics* file (or any per-sample population count / % table). Drives the **mandatory reconciliation** in step 4b (`--manual`). Distinct from per-cell labels: this is aggregate per-sample stats, which real customer data usually has and per-cell labels usually don't. `.csv`/`.tsv`, or `.xlsx` if `readxl` is installed. |

---

## Outputs (per-run artifacts to `/mnt/results/<run>/`; the PDF report always goes to the results root `/mnt/results/`)

- **SCE** (`sce_annotated.rds`) — QC'd, transformed, clustered, annotated SingleCellExperiment.
- **QC/transform log** (`qc_transform_log.txt`/`.csv`) — modality detected, transform + compensation
  applied, cells removed at each QC stage.
- **Tables** (`.csv`) — **editable `gating_thresholds_template.csv`** (proposed QC cutoffs, per-gate,
  for review/override); abundance per sample; (if labeled) per-population precision/recall/F1 + status;
  (if ≥3/group) diffcyt DA/DS results; (if a manual export was supplied) `validation_vs_manual.csv`
  with per-sample/per-population pipeline-vs-manual deltas and a **PASS/REVIEW verdict**.
- **Figures** (`.png` + `.svg`) — **per-gate QC diagnostics** (`gate_<sample>_<gate>.png`, actual
  cutoff/2D region drawn on the density), marker z-score heatmap, delta-area, UMAP/tSNE, abundance,
  frequency heatmap, benchmark F1, resolution sensitivity.
- **PDF report** (`report_<dataset>.pdf`) — **mandatory** final deliverable, **always written to the
  results root `/mnt/results/`** (never the per-run subfolder) so it is easy to find: infographic +
  Introduction, Methods, Results, Conclusions, References (verbatim, citation-verified), Next steps.

---

## Workflow

Run the scripts in order (each writes an `.rds` the next reads). All scripts take arguments so they
generalize beyond the worked example; defaults reproduce the reference run. **Exception:**
`08_validate_vs_manual.R` (step **4b** below) is a validation *gate*, not a pipeline stage — despite
its file number it should run **as early as its inputs allow** (right after `01` for the total-cell
over-gating check; re-run after `04` for the population-level check) and **before** you trust `05`/`06`/`07`.
Every real run **ends with the mandatory report** (step 7): `build_manifest.R` collects the run's
numbers into `run_manifest.json`, then `07_build_report.py` turns them, the figures, and
CrossRef-verified references into the PDF — written to the results root.

### 1. Load, transform, and QC — `scripts/01_load_and_qc.R`  **(QC is first-class — see below)**
- Load FCS (`flowCore::read.flowSet`) or an HDCytoData dataset; build a CATALYST SCE via `prepData`.
- **Auto-detect modality** from FCS keywords: `$CYT`/`$CYTSN` + mass-tag channel names (isotope
  masses, `Di`) ⇒ **CyTOF**; fluorochrome channels + `$PnV` voltages + `FSC/SSC` ⇒ **fluorescence flow**.
- **Transform (logged explicitly, override-able):**
  - CyTOF ⇒ arcsinh **cofactor = 5**.
  - Flow ⇒ **logicle/biexponential** (`flowCore::estimateLogicle`, per-channel, data-driven) or a
    **per-channel estimated arcsinh cofactor**. **Never silently hardcode 150** — 150 is only an
    explicit coarse fallback.
- **Compensation (flow):** apply embedded spillover (`flowCore::spillover` → `compensate`) if present;
  else warn and proceed uncompensated, noting it. The embedded matrix is **validated before inversion**
  (square, finite, invertible via `solve()`): a **singular / non-invertible** spillover matrix (seen in
  real BD LSRII exports) no longer crashes the run — it is skipped with a warning and the data proceed
  **UNCOMPENSATED**. **Raw spectral panels** (unmixing pending) are refused with an override hint; already-unmixed spectral data proceeds as fluorescence flow.
  **v2.2.0 (item 2):** every applied matrix (embedded or external) is graded by **`spillover_diagnostics`**
  — an SVD **condition-number (κ)** check returning verdict well/ill/singular; an **ill-conditioned**
  matrix (κ > `--spillover-kappa-max`, default 1000) is **reported but still applied** (diagnostics-on),
  a **singular** one is skipped (UNCOMPENSATED). An **external** compensation matrix can be supplied as
  CSV via `--spillover` and is aligned to the panel by channel-name intersection.
- **Provenance:** read cohort/tissue/disease/source from dataset or FCS/`metadata.csv` metadata —
  never infer. (e.g. `Levine_32dim` = 2 **healthy** donors; `Levine_13dim` = the **AML** cohort.)
- See `references/transform_and_compensation.md`.

### QC / pre-gating (do this — real FCS is not pre-cleaned)
Benchmark data (HDCytoData) ships pre-cleaned; **real customer FCS does not**. Skipping QC lets
debris, doublets, dead cells, and beads leak into clustering and create junk clusters (the platelet
CD41/CD61 and erythroid CD235ab "doublet" clusters in the worked example are exactly this). QC is a
**first-class, modality-aware stage**. Cutoffs are **data-driven, reviewable, and multivariate**
(see `references/threshold_selection.md`); per-stage counts are logged and **each gate is drawn** to
`figures/gate_<sample>_<gate>.png` with the actual cutoff (1D) or joint region (2D) on the density:
- **Data-driven cutoffs.** 1D thresholds sit at the **density valley** (antimode), not a fixed
  percentile — `--gate-method valley|gmm|otsu|percentile|control|auto` (default `auto`). An **honesty
  guard** (Hartigan dip test `--dip-alpha` + minimum valley depth `--valley-min-depth`) refuses to
  invent a cutoff on a unimodal or shallow channel and falls back to a conservative percentile with a
  **REVIEW** flag.
- **Multivariate (2D) gates** (`--multivariate on`, default): debris = FSC-A × SSC-A robust ellipse
  (flowClust/MCD); live/dead = viability × scatter (flow) or Pt × DNA (CyTOF) diagnostic — not just
  single channels.
- **Viability × lineage-marker diagnostic** (`--lineage-markers CD15,CD66b,...`):
  in addition to the viability × scatter / Pt × DNA 2D diagnostic above, a **viability × lineage-marker**
  biaxial plot (`gate_<sample>_live_dead_lineage_<marker>.png`) is emitted for each resolved marker.
  **Rationale:** neutrophils take up viability dye without being dead, so a viability × scatter (FSC)
  view alone can misgate live neutrophils as dead; plotting viability against a granulocyte lineage
  marker (CD15/CD66b/CD16/CD11b) makes the "dye-bright but live" population obvious to a reviewer.
  `--lineage-markers` takes comma-separated antigen names, resolved against the panel antigens
  (word-boundary match, so `CD15` matches `CD15 FITC`); if omitted, auto-detects the granulocyte-first
  defaults `CD15,CD66b,CD16,CD11b` present in the panel. **Diagnostic only** (`apply=N`) — gating
  behavior is unchanged; decoupled from `--multivariate` (always emits when a viability channel + a
  resolved lineage marker exist). If no lineage marker is present in the panel, logs a note and skips
  gracefully (no error).
- **Control/FMO anchoring** (`--controls controls.csv`): anchor positive/negative cutoffs to unstained
  or FMO controls (see `assets/controls_template.csv`); control cutoffs take precedence when supplied.
- **Time-based acquisition QC (v2.2.0, item 1)** (`--time-qc off|report|remove|auto`, default `auto`):
  data-driven **flow-rate**, **signal-stability**, and **margin-event** checks over the acquisition
  Time channel flag unstable acquisition (fluidics clogs, bubbles, drift, boundary pile-ups) and draw
  `figures/time_qc_<sample>.png`. Default `auto` = **report** for flow (flags but **removes 0 events** —
  diagnostics-on); `remove` opts in to dropping flagged events; `off` disables. `--time-qc-backend`
  (`native` default) accepts `flowai`/`peacoqc` requests but transparently falls back to the equivalent
  native MAD engine rather than failing when those packages are absent.
- **Reviewable, two-pass by default.** Every proposed cutoff is written to an
  **editable template** (`gating_thresholds_template.csv`; see
  `assets/gating_thresholds_template_example.csv`). A normal run (`--gate-review propose`, the
  default) writes the template + per-gate figures and **stops before writing the SCE** for human
  review; edit `final_cutoff`/`apply`, then rerun with `--gate-review apply --thresholds
  <edited.csv>` to produce the SCE. **Non-stopping escape hatches:** `--gate-review auto` (proceed
  unattended — for pipelines/batch jobs), `--qc off` (no QC, no stop), a supplied `--thresholds`
  (treated as "apply my edits," skips the stop), and the **openCyto backend** (no propose/apply
  loop — logs a note and proceeds; confirmation-by-default is builtin-engine only).
  `--threshold-scope per_sample|pooled|batch` chooses per-sample cutoffs (default), a harmonized
  across-sample median (`pooled`), or **within-batch** confidence-weighted harmonization (`batch`,
  v2.2.0 item 4 — shrinks each gate's cutoff toward its within-batch consensus by
  `(1 − valley_confidence)·--harmonize-shrink`, so confident valleys stay put and only uncertain
  ones are pulled together).
- **Modality gates.** Flow: FSC/SSC debris → **FSC-A/FSC-H singlet** → live/dead. CyTOF: **DNA
  intercalator** (Ir191/Ir193 intact) → **Gaussian-parameter doublet** (event length) →
  **cisplatin/viability** (Pt195) → **bead handling**. Both modalities also emit the
  **viability × lineage-marker diagnostic** (`--lineage-markers`) described above. By
  default beads are gated out; **v2.2.0
  (item 8)** adds opt-in **bead-based signal normalization** — `--cytof-norm on|auto` runs
  `CATALYST::normCytof` (`--beads dvs`, `--cytof-norm-k`), which **skips** the bead-removal gate so bead
  events survive for normalization and are then removed by `normCytof` (`auto` = on when a bead channel
  is present). When a flow file has **height-only scatter** (FSC-H/SSC-H, no `-A` channels — e.g.
  older FACSCalibur exports), debris gating **falls back to the height channels** instead of silently
  removing nothing; the singlet gate (which needs A-vs-H) is then correctly skipped.
- Gates are automated with tunable defaults; if data is pre-cleaned, state that QC was skipped and why.
- **GATE GENTLY (hard rule).** Scatter/singlet gates default to **permissive** (debris ≈2nd pct;
  singlet band = median ± **4·MAD**, exposed via `--singlet-mad-k`/`--debris-pct`). **Over-gating is a
  silent failure mode** — a too-tight scatter/singlet gate discards *real* cells non-randomly and
  biases every downstream number without ever erroring. `01` logs in→out per gate and raises a loud
  **OVER-GATING ALARM** when scatter+singlet jointly remove >30% (`--overgate-alarm`). Never use a
  hardcoded ratio window (e.g. 0.75–1.25).
- **If results ever disagree with manual gating, inspect the scatter/singlet gate FIRST**, then the
  marker-positive gate, and the viability threshold **last**. Do not "recalibrate" the viability cutoff
  to paper over an upstream scatter problem.
- See `references/qc_gating.md` and `references/threshold_selection.md`.

### Gating backend — built-in (default) vs OpenCyto (v2.2.0, opt-in) — `scripts/gating_opencyto.R`
QC and marker gating run through one of two engines, selected by `--gate-engine`:
- **`builtin`** (default) — the v2.1.0 data-driven per-gate engine described above (density-valley
  cutoffs, 2D ellipses, honesty guards, editable template + per-gate figures). Unchanged.
- **`opencyto`** (opt-in, item 6) — a real **openCyto / flowWorkspace** `GatingSet` driven by a
  `gatingTemplate` CSV for reproducible, shareable **hierarchical** gating. Transforms are
  modality-aware (arcsinh for CyTOF; data-driven `flowCore::estimateLogicle` for flow). Shipped default
  templates live in `assets/gating_template_flow.csv` and `assets/gating_template_cytof.csv`; supply
  your own with `--gate-template` (which auto-selects this engine). Requires the openCyto stack
  (openCyto, flowWorkspace, CytoML) — the run **stops with a clear message** if it is missing rather
  than silently degrading. The resolved hierarchy is written to `gating_hierarchy.txt`.

### 2. Cluster ("automated gating") — `scripts/02_cluster.R`
- `CATALYST::cluster(sce, features = "type", xdim = 10, ydim = 10, maxK = 20, seed = ...)`
  (FlowSOM SOM → ConsensusClusterPlus metaclustering).
- **Choose resolution WITHOUT an oracle:** default to the ConsensusClusterPlus **delta-area plateau**
  (elbow) + stability. Do **not** assume you know the number of populations. Expose the resolution knob.
- See `references/clustering_and_resolution.md`.

### 3. Annotate + two-tier refinement — `scripts/03_annotate.R`
- Compute per-cluster median marker expression; z-score lineage markers → heatmap.
- Scaffold labels from **CellMarker2** (data lake) marker sets; analyst confirms (semi-automated).
- `CATALYST::mergeClusters` to consolidate redundant clusters into named populations.
- **Two-tier strategy (baked in):** annotate at a coarse, interpretable resolution, then **subcluster
  specifically the lineages flagged as merged** (e.g. split a CD3+ cluster into CD4/CD8).

### 4. Quantify + dimensionality reduction — `scripts/04_quantify_dr.R`
- `runDR` UMAP (+ tSNE cross-check) on a **balanced subsample** (default **10,000 cells/sample**,
  `--cells_per_sample`, capped at the smallest sample) for speed. **Visualization only** — clustering
  and abundance/% use all cells.
- Abundance counts and % per sample; cluster-by-sample frequency heatmap; summary table.

> **Subsampling policy (by stage) — no events are lost in analysis.** QC (`01`), clustering /
> population discovery (`02`, FlowSOM trains on **every** post-QC event), annotation (`03`), abundance
> and % (`04`), benchmarking (`05`), and differential abundance (`06`) all use **ALL post-QC cells** —
> rare/mixed populations are never thinned. The **only** subsample in the whole pipeline is the
> UMAP/t-SNE **embedding** in `04` (`--cells_per_sample`, default 10,000/sample, capped at the smallest
> sample), which sets **plot density only** and feeds no count, frequency, cluster assignment, or
> statistic. `02` and `04` log the exact cell counts used at each stage so this is auditable. (There is
> no dose-response / per-well biomarker subsampling here — that ADCC-style targeted-gating workflow is
> out of scope; see **Scope**.)

### 4b. Validate vs a manual-gating export (MANDATORY when one exists) — `scripts/08_validate_vs_manual.R`
Whenever a manual-gating export is available (a FlowJo *Export Statistics* file, or any per-sample
population count / % table), **reconcile the pipeline against it before trusting downstream fits,
abundance comparisons, or the report.** This is a gate, not polish — it is the check that catches
gating errors the pipeline's own diagnostics cannot see.
- Reconciles, per sample: (1) **total post-QC cell count** vs the manual singlet/"Cells" count (the
  primary **over-gating detector** — needs only `01`'s `sce_prepped.rds`, so run it early), and
  (2) **per-population counts/%** vs the manual export (when an abundance table from `04` exists).
- Flags discrepancies beyond `--tol-pp` (percentage points) / `--tol-rel` (relative count) and emits a
  **PASS / REVIEW** verdict. A **REVIEW** means: stop, fix gating, inspect the scatter/singlet gate first.
- **Independent ground truth beats internal self-consistency.** Bimodal histograms, a monotonic
  dose-response, and plausible correlations are *not* validation — a biased gate produces those too.
  When any external reference exists, use it; do not treat self-consistency as proof.
- See `references/validation_vs_manual.md`.

### 5. Benchmark vs manual gates (conditional) — `scripts/05_benchmark.R`
Runs only if per-cell labels exist. Restrict to labeled cells; map each cluster to its
**max-overlap** gold population (Weber & Robinson standard).
- **CRITICAL fix:** name the mapping vector — `c2p <- setNames(colnames(ct)[apply(ct,1,which.max)],
  rownames(ct))` — an unnamed vector makes name-indexing return all `NA`.
- **Detect many-to-one collapses** and label affected populations **"merged with <other>"** with
  status ∈ {recovered, merged, split, missed} — do NOT let a bare F1=0 read as "population missed."
  (Worked example: CD4 precision 0.567/recall 1.00 with CD8 F1=0 is a *resolution* artifact; CD8
  recovers to **F1=0.98 at 100 nodes**.)
- Report per-population precision/recall/F1, overall accuracy, ARI, NMI, and a **resolution-sensitivity
  sweep** to separate merges from true misses.
- See `references/benchmarking_metrics.md`.

### 6. Differential abundance (conditional, strict) — `scripts/06_diff_abundance.R`
- Run **diffcyt-DA-edgeR** (abundance) and **DS-limma** (state markers) **only when ≥3 samples per
  group** and ≥2 groups. Otherwise **refuse to emit p-values** and report descriptive abundance +
  an explicit limitation. This refusal is the point — do not p-hack n=2.
- Design formula includes **covariates**: `~ batch + group` (not group alone).
- See `references/diffcyt_design.md`.

### 7. Report + infographic (MANDATORY) — `scripts/07_build_report.py`
The PDF report is **not optional** — every real run ends here.

**Mandatory instruction:** Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

- **Build the run manifest first** — `scripts/build_manifest.R <run_dir>` writes `run_manifest.json`
  (modality; cell/sample/marker/cluster/population counts; transform; compensation; QC removal
  summary + detail; top population; and — when present — `benchmark{}` and `diff_abundance{}` blocks)
  by reading the pipeline artifacts **read-only**. `07` consumes this manifest and it **never edits**
  `01`–`08`.
- Generate a one-page **infographic** with the **Biomni `GenerateImage` tool** (pipeline flow
  FCS → QC/compensate → transform → cluster → annotate → quantify → benchmark/DA; headline
  population count + top metric). The infographic is **required** — `07` **fails fast** if it is
  missing or the file does not exist.
- Build the Phylo PDF by following the **`pdf-report-generation`** system skill (ReportLab template):
  Introduction, Methods (exact transform/compensation + **QC gates & counts removed** + **per-gate
  removal %, any OVER-GATING alarm, and the manual-gate validation verdict** + clustering
  params + resolution rationale), Results (figures + tables, with merged-population labels),
  Conclusions, **References**, **Next steps**.
- **Citation integrity (hard rule).** Reference metadata (title, venue, year, volume, issue, page,
  DOI) is taken **verbatim from CrossRef** and stored in `assets/references_cytometry.json`; `07`
  renders references from that store — **never from memory** — and unavailable fields render as `n/a`.
  Run `scripts/verify_citations.py` to audit DOI↔metadata consistency **before** shipping a report;
  real DOIs with fabricated titles are exactly the failure mode this guards against.
- **Output location:** the PDF is **always written to the results root `/mnt/results/`** (never the
  per-run subfolder), so the deliverable is easy to find.
- **Never bury a REVIEW verdict or an over-gating alarm** — if `08` returned REVIEW, the report must
  state that gating disagrees with the manual reference and that downstream results are provisional.
- Run a media output check on every figure and on the final PDF; regenerate on failure.

---

## Environment resources this skill uses

- **R/Bioconductor:** CATALYST, FlowSOM, ConsensusClusterPlus, diffcyt, flowCore, SingleCellExperiment,
  ComplexHeatmap, ggplot2/ggprism/ggrepel, uwot, Rtsne, scater; **flowDensity + flowClust** (data-driven
  valley / model-based gating) and **diptest + mclust + MASS** (unimodality guard, GMM antimode, robust
  MCD ellipse) for `gating_engine.R`. Install cytometry packages on demand via `BiocManager` into
  `/workspace/.Rlib` (not in the base R set). flowCore/CATALYST provide `estimateLogicle`, `compensate`,
  `spillover`, `normCytof`. The gating engine **degrades gracefully** — if flowDensity/flowClust/mclust/
  diptest/MASS are absent it uses native base-R fallbacks (KDE valley, Otsu, KDE peak-count for
  unimodality, percentile).
- **OpenCyto backend (v2.2.0, opt-in — `--gate-engine opencyto`):** openCyto, flowWorkspace, CytoML
  (with their graph deps Rgraphviz/RBGL/graph) for `scripts/gating_opencyto.R`; **PeacoQC** is available
  as an optional time-QC backend (the native MAD engine is the default). Installed on demand into
  `/workspace/.Rlib` and **not required** for the default built-in path — the run stops with a clear
  message only if `--gate-engine opencyto` (or a time-QC backend) is requested without them.
- **R (validation):** base R for `08_validate_vs_manual.R`; `readxl` **only** if the manual export is
  `.xlsx` (CSV/TSV need no extra package).
- **Python:** fcsparser (quick FCS sanity/preview), reportlab, pypdf; `requests` + `PyYAML` for
  `scripts/verify_citations.py`, the CrossRef citation-integrity audit over
  `assets/references_cytometry.json` (verbatim reference store consumed by `07`).
- **Data lake:** **CellMarker2** for cluster→cell-type annotation scaffolding; MSigDB / Human Protein
  Atlas optional for marker context.
- **Tools/skills:** `LiteratureSearch` (grounded citations), `pdf-report-generation` (PDF), `GenerateImage`
  (infographic — the **Biomni GenerateImage tool**), `ManageMachine` (right-size compute for real runs).
  `scripts/build_manifest.R` assembles the read-only `run_manifest.json` that the report step consumes.

## Compute

Authoring/small runs are trivial. For real datasets (~1e5–1e6 cells × 30–40 markers, memory-light,
< ~4–8 GB): the bottlenecks are Bioconductor install (~15–40 min) and ConsensusClusterPlus
metaclustering (minutes). Provision ~8 CPU / 32 GB via `ManageMachine`; run UMAP/tSNE on a subsample;
subsample/chunk SOM training for >2–3e6 cells. Set seeds for reproducibility.

## Scientific caveats

- Transform and compensation **must match modality**; raw spectral data must be unmixed upstream; already-unmixed spectral data is detected from named fluorophore/marker channels and processed as flow (override with `--spectral-state`).
- **QC before clustering** removes debris/doublets/dead/beads; skipping it produces junk clusters.
- **Gate GENTLY — over-gating is the dominant silent failure mode.** Tight scatter/singlet gates
  discard *real* cells non-randomly and bias every downstream number without erroring. Default to
  permissive gates; heed the OVER-GATING alarm (>30% scatter/singlet removal); never hardcode a ratio
  window like 0.75–1.25.
- **Data-driven cutoffs are proposals, not gospel.** Valley/GMM/Otsu 1D thresholds and 2D gates are
  written to an editable template with per-gate figures; a `REVIEW_*` status (unimodal / shallow valley
  / too few events / no matching control) means the pipeline **refused** to trust an automatic cutoff —
  inspect the figure and confirm or override before relying on it. See `references/threshold_selection.md`.
- **On any automated-vs-manual discrepancy, suspect the scatter/singlet gate FIRST** — then the
  marker-positive gate, then the viability threshold last. In the reference ADCC case the viability
  cutoff was correct all along; the scatter gate was the culprit.
- **Reconcile against a manual-gating export whenever one exists** (step 4b) *before* trusting fits.
  Internal self-consistency (bimodality, monotonic trends, plausible correlations) is **not**
  validation — seek independent ground truth.
- **Sanity-check implausible readouts** (e.g. an ADCC 0-dose control >25–30% dead, or per-well target
  counts that track dose): treat as a gating-review trigger, not a biological explanation to accept.
- Benchmarking uses labeled cells only and **flags merges** (many-to-one) rather than reporting bare F1=0.
- Differential abundance needs **≥3 samples/group** with covariates; below that, descriptive only.
- Resolution selection and annotation involve judgment; default to delta-area + a two-tier strategy.
- **Time-based acquisition QC is diagnostics-first (v2.2.0).** By default (`--time-qc auto`, flow) it
  **flags** unstable flow-rate / signal drift / margin pile-ups and writes `figures/time_qc_<sample>.png`
  but **removes no events**; opt in with `--time-qc remove`. `flowAI`/`PeacoQC` backends are optional —
  absent them the native MAD engine runs, not a silent skip.
- **A high spillover condition number is a warning, not an auto-fix (v2.2.0).** Large κ means
  compensation is numerically fragile; the matrix is reported and still applied. Fix it upstream — do
  not trust marker-positivity on a κ ≫ 1000 panel without review; a singular matrix is skipped and the
  data proceed UNCOMPENSATED.
- **Batch harmonization is opt-in and confidence-weighted (v2.2.0).** `--threshold-scope batch` pulls
  each gate's cutoff toward its within-batch consensus by `(1 − valley_confidence)·shrink`, leaving
  confident valleys alone; it cannot rescue a mis-specified `batch` column — provenance still comes from
  metadata, and groups smaller than the minimum are left unchanged.
- **OpenCyto is an alternative backend, not a validator (v2.2.0).** It yields reproducible hierarchical
  gates, but a `gatingTemplate` still encodes human choices — reconcile against a manual export (step 4b)
  exactly as for the built-in engine.
- **CyTOF bead normalization needs bead channels (v2.2.0).** `--cytof-norm on|auto` acts only when bead
  channels exist and **skips** the bead-removal gate so beads survive for `normCytof`, which then removes
  them — do not additionally gate beads out.
- **Citations are verbatim, not remembered.** Reference titles/venues/years/DOIs come from CrossRef
  and live in `assets/references_cytometry.json`; the report renders them from that store and
  `scripts/verify_citations.py` audits DOI↔metadata consistency. Never hand-type a citation from
  memory — real DOIs paired with fabricated titles are a known failure mode.
- Read **provenance from metadata**, never infer cohort/health status.
