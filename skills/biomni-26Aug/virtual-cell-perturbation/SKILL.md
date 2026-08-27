---
id: "skill_ee05eb5df2ca51cf172af24e65f59022"
name: virtual-cell-perturbation
description: "Use to predict or benchmark single-cell transcriptional responses to CRISPR, CRISPRi, or CRISPRa perturbations. Evaluates virtual-cell models such as scGPT, GEARS, scFoundation, or CellFlow on Perturb-seq/GEARS-format data, including unseen-perturbation generalization and held-out differential-expression metrics."
category: "transcriptomics"
visibility: "public"
starting-prompt: "Benchmark a single-cell perturbation-response model (e.g. scGPT/GEARS) on my Perturb-seq data and report predicted-vs-held-out DE with a PDF."
---

# Virtual Cell Perturbation Benchmark

Predict single-cell transcriptional responses to genetic perturbations and benchmark
predicted vs. measured differential expression on **held-out** perturbations, then write
a polished Phylo-branded PDF report.

This skill is **generalized along three axes**:

1. **Any GEARS dataset** — `norman`, `adamson`, `dixit`, `replogle_k562_essential`,
   `replogle_rpe1_essential`, or a **user-supplied AnnData** in GEARS/`PertData` format.
2. **Any perturbation model** — a small adapter interface with three built-in predictors:
   **scGPT** (foundation model), **GEARS** (graph neural net), and a **control-mean baseline**
   ("predict no change"). New models plug in via one function (see `references/model_adapters.md`).
3. **Any split / evaluation regime** — GEARS `simulation` / `simulation_single` split with a
   seed, or an explicit train/test perturbation list, plus a configurable metric set.

## Scope

**Does:** load a Perturb-seq dataset, build a held-out split, run one or more perturbation
predictors, compute the canonical GEARS metrics (per-perturbation and stratified by
generalization regime) against held-out cells and a control-mean baseline, make figures,
pull benchmark-context literature, and assemble a PDF report.

**Does NOT:** reimplement scGPT/GEARS internals, design wet-lab CRISPR screens, call
differential expression on raw counts (GEARS handles DE-gene bookkeeping), or ship any
non-commercial data or weights.

## When to use vs. other skills

- Use this for **predicting/benchmarking perturbation responses** (a model produces a
  post-perturbation expression profile that you score against held-out cells).
- Use `pooled-crispr-screens` instead for **screen hit-calling** (MAGeCK/enrichment of guides).
- Use `scrnaseq-scanpy-core-analysis` / `scrnaseq-seurat-core-analysis` for general scRNA-seq
  QC/clustering with no perturbation-prediction model.

## Inputs

- **Dataset**: a GEARS dataset name, or a path to an AnnData (`.h5ad`) with the GEARS-required
  fields (`condition`, `cell_type`/covariate, gene names in `var`). See
  `references/datasets_licensing.md` for the exact schema and how to run a custom dataset.
- **Model(s)**: any of `scgpt`, `gears`, `baseline` (default `scgpt,baseline`).
- **Weights** (scGPT): either `train_from_base` (fine-tune scGPT_human, MIT — default) or
  `checkpoint` (path or HuggingFace id of a pre-trained fine-tune, faster).
- **Split**: `simulation` (default) + `--split_seed` (default `42`), or explicit
  `--train_perts` / `--test_perts` files.

## Outputs

- `report_<dataset>_perturbation_benchmark.pdf` — Phylo-branded, with an infographic summary
  panel, intro, methods, results (embedded figures), conclusions, references, and next steps.
- `figures/F1..F6` as PNG (150 dpi) + editable SVG.
- Intermediate arrays / summary JSON / per-perturbation table stay in the working/trace area
  (not surfaced as user deliverables unless asked).

---

## CRITICAL prerequisites (read before running)

### 1. GPU + from-scratch environment
scGPT and GEARS are **NOT pre-installed** in Biomni. You must:
- Run on a **GPU sandbox** (`Gpu` tool). Prior validated runs used NVIDIA A10G (~23 GB).
  The `baseline`-only path is CPU-feasible.
- Bootstrap the environment with `scripts/setup_env.sh` (verified conda/pip recipe;
  ~7–8 min, I/O-bound).

### 2. GPU sandbox `timeout` = TOTAL LIFETIME (not per-call)
This is the single most important operational rule. The `Gpu` `timeout` parameter is the
sandbox's **entire lifetime**, after which everything is killed (exit 137). Set it large:
- **Prediction** jobs: `timeout` ≥ 7200 s (~30 min actual at batch 32 + setup).
- **Fine-tuning** jobs: `timeout` ≥ 9000 s (~15 min/epoch, dataloader is nproc-limited).
Undersizing this caused repeated silent sandbox deaths in development.

### 3. Memory: use batch_size = 32 for prediction
scGPT attention is O(seq²) over the full gene panel (~5000 genes). **batch 64 OOMs**;
**batch 32 is safe** (~8 GB). Always `export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.

### 4. S3-FUSE has no random-access writes
`.pt` / `.npy` / `.h5ad` must be written to **local `/workspace` first, then copied** to
`/mnt/results`. All scripts already do this. (R's `file.copy` to `/mnt/results` yields 0-byte
files — use a shell `cp`.)

### 5. Only use open / commercial-use data
Default to open deposited datasets and the **train-from-base scGPT_human (MIT)** weights so
the whole pipeline is unambiguously commercial-safe. If a user dataset's license is unknown
or restricted, flag it before running. See `references/datasets_licensing.md`.

---

## Workflow

Run the numbered steps. Each script takes CLI args; defaults reproduce the validated
scGPT-on-Norman benchmark.

### Step 0 — Provision GPU + bootstrap env
```bash
# On a Gpu sandbox created with a LARGE timeout (>=7200 for predict, >=9000 for train):
bash scripts/setup_env.sh          # conda env at /workspace/scgpt_env (~7-8 min)
```

### Step 1 — Data + split
```bash
PY=/workspace/scgpt_env/bin/python
$PY scripts/prepare_data.py --dataset norman --split simulation --split_seed 42 \
    --batch_size 32 --data_dir /workspace/data
# Prints & asserts split regime composition (provenance). Verifies dataset is open.
```

### Step 2 — Get weights (scGPT only; choose ONE path)
First get the **base** scGPT_human checkpoint (needed for either path — it provides
`args.json` + `vocab.json` + base weights):
```bash
$PY scripts/get_checkpoint.py --which base --out_dir /workspace/save/scGPT_human
```
Then either fine-tune from base, or fetch a ready fine-tune:
```bash
# (a) train-from-base (default, MIT, license-clean; ~90+ min with a time cap):
$PY scripts/train_scgpt.py --dataset norman --split_seed 42 --max_epochs 6 \
    --time_cap_min 105 --load_model /workspace/save/scGPT_human \
    --save_dir /workspace/save/ft         # -> /workspace/save/ft/best_model.pt (+ state.json)
# (b) OR Norman-only fast path: download a published fine-tune (reuses base args/vocab):
$PY scripts/get_checkpoint.py --which norman-ft --out_dir /workspace/save/ft \
    --base_dir /workspace/save/scGPT_human
```
GEARS trains quickly inside its adapter; the baseline needs no weights.

### Step 3 — Predict (ONE model per call; arrays saved immediately)
`predict.py` takes a single `--model`; run it once per model you want to compare.
The `baseline` is also recomputed for free inside Step 4, so a single scGPT run is
usually enough — add explicit baseline/gears runs only if you want their arrays saved.
```bash
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
$PY scripts/predict.py --model scgpt --dataset norman --split_seed 42 \
    --batch_size 32 --base_model /workspace/save/scGPT_human \
    --ft_ckpt /workspace/save/ft/best_model.pt --out_dir /mnt/results/execution_trace/preds
# optional extra models (separate out_dirs):
# $PY scripts/predict.py --model gears    --dataset norman --gears_epochs 15 --out_dir .../preds_gears
# $PY scripts/predict.py --model baseline --dataset norman --out_dir .../preds_baseline
```

### Step 4 — Metrics vs. held-out DE + baseline
```bash
$PY scripts/aggregate_metrics.py --dataset norman --split_seed 42 \
    --preds_dir /mnt/results/execution_trace/preds \
    --out /mnt/results/execution_trace/benchmark_metrics.json \
    --pkl /mnt/results/execution_trace/benchmark_full.pkl
```
Uses **verbatim GEARS** `compute_metrics` (mse, mse_de, pearson, pearson_de) and
`deeper_analysis` (pearson_delta, pearson_delta_de, `frac_correct_direction_{20,50,100,200,all}`),
per-perturbation and stratified by regime.

### Step 5 — Figures (worker-0, no GPU; run media check after each)
```bash
python scripts/make_figures.py --pkl /mnt/results/execution_trace/benchmark_full.pkl \
    --metrics /mnt/results/execution_trace/benchmark_metrics.json \
    --preds_dir /mnt/results/execution_trace/preds --figdir /mnt/results/figures \
    --named_example JUN+CEBPA \
    --curve /workspace/save/ft/state.json   # optional: real training curve for provenance (F2)
```
`--curve` is optional: include it ONLY if you actually fine-tuned (Step 2a). For a
downloaded checkpoint, omit it — F2 falls back to a provenance table, never a fake curve.

### Step 6 — Literature (benchmark context)
Use the **`LiteratureSearch`** tool (NOT a script) to pull published perturbation-prediction
benchmarks for the intro/discussion + references, e.g. queries like
"single-cell perturbation response prediction benchmark", "scGPT perturbation Norman",
"GEARS genetic perturbation prediction", "CellFlow perturbation". `LiteratureSearch` appends
structured records to `execution_trace/references.jsonl`. Then format them for the report:
```bash
python scripts/literature_search.py \
    --refs_jsonl /mnt/results/execution_trace/references.jsonl \
    --out /mnt/results/execution_trace/references.json
```
This de-dupes and keeps the verified anchor papers (scGPT, GEARS, and the dataset paper)
first. Verify every cited number against the returned records; never fabricate citations.

### Step 7 — PDF report (leverages the `pdf-report-generation` skill)
Load and follow the **`pdf-report-generation`** skill for ReportLab styling conventions
(Phylo gold-accent palette, title page, per-page header/footer, validation).
`scripts/build_report.py` implements that template — it consumes `benchmark_metrics.json` +
`figures/` + `references.json` and assembles the sections
**infographic summary → intro → methods → results → conclusions → next steps → references**:
```bash
python scripts/build_report.py \
    --metrics /mnt/results/execution_trace/benchmark_metrics.json \
    --figdir /mnt/results/figures \
    --references /mnt/results/execution_trace/references.json \
    --curve /workspace/save/ft/state.json \
    --out /mnt/results/report_norman_perturbation_benchmark.pdf
```
It self-validates (page count, size, extractable text). After it runs, also do a visual
`Read(mode="media_output_check")` on the PDF; regenerate if anything is blank or clipped.

---

## Metrics & scientific caveats

- **Judge perturbation skill on the delta metrics.** Absolute-expression correlations
  (`pearson`, `pearson_de`) are misleadingly high for *any* method because most genes don't
  move; a trivial control-mean baseline matches a real model on them. Real skill shows up in
  **`pearson_delta`** (change-from-control correlation) and **top-k DE direction match**.
- **Control-mean baseline nuance (do not misreport).** For the "predict no change" baseline,
  Δ-correlations and **top-k** direction metrics are exactly 0 by construction, but
  `frac_correct_direction_all` is a small **nonzero** (~0.26) artifact — the sign of a near-zero
  predicted change coincidentally matches a near-zero true change for unchanged genes. Compare
  models on **top-k DE** direction, not the all-gene one.
- **Generalization is regime-dependent.** GEARS `simulation` split stratifies test
  perturbations into `combo_seen0/1/2` and `unseen_single`. Accuracy is highest for
  combinations of previously seen genes and lowest when a gene was never seen; report the
  stratified breakdown, and treat tiny regimes (e.g. `combo_seen0`, often n≈2) as illustrative.
- **Genome/vocab overlap.** scGPT only scores genes present in its vocabulary (~90% for Norman).
  Report the match fraction.
- **Provenance, not fabrication.** Only plot a training curve if you actually trained
  (`--curve` state.json). Never synthesize a curve for a downloaded checkpoint.
- **Reproducibility.** GEARS splits are deterministic per seed; record the seed and the split
  regime counts so results are reproducible.

## Compute / resource summary

| Stage | Target | Time (validated) | Memory |
|---|---|---|---|
| Env bootstrap | GPU sandbox | ~7–8 min (I/O) | small |
| Fine-tune scGPT | GPU (A10G) | ~15 min/epoch | ~8 GB @ batch 32 |
| Predict | GPU (A10G) | ~30 min | ~8 GB @ batch 32 |
| Metrics + figures + report | worker-0 (CPU) | minutes | small |

Set `Gpu` `timeout` ≥ 7200 s (predict) / ≥ 9000 s (train). Chunk long work and checkpoint to
persistent storage.

## Files

- `scripts/setup_env.sh` — verified conda/pip environment recipe.
- `scripts/prepare_data.py` — load GEARS dataset / user AnnData, build split, licensing guard.
- `scripts/train_scgpt.py` — fine-tune scGPT_human (train-from-base path).
- `scripts/get_checkpoint.py` — fetch a pre-trained checkpoint (fast path).
- `scripts/predict.py` — pluggable adapters (scgpt / gears / baseline); saves arrays immediately.
- `scripts/aggregate_metrics.py` — verbatim GEARS metrics, per-pert + regime-stratified.
- `scripts/make_figures.py` — generalized F1–F6 with mandatory media checks.
- `scripts/literature_search.py` — format `LiteratureSearch` records into `references.json`.
- `scripts/build_report.py` — Phylo PDF template (use with `pdf-report-generation`).
- `references/model_adapters.md` — how to add a new model in one function.
- `references/metrics.md` — exact metric definitions and interpretation.
- `references/datasets_licensing.md` — dataset schema, available datasets, licenses.
- `references/gpu_platform_notes.md` — the timeout/OOM/S3-FUSE gotchas in detail.
