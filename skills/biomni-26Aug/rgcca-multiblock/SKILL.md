---
name: rgcca-multiblock
description: >
  Run RGCCA (Regularised Generalised Canonical Correlation Analysis) multiblock
  integration on 2+ omics or clinical data blocks using the RGCCA R package v3.0.3.
  Use this skill whenever a user wants to integrate multiple data blocks (e.g.
  transcriptomics + proteomics + clinical), run SGCCA, PLS, CCA, or any RGCCA-family
  method, tune regularisation parameters by cross-validation or permutation, compare
  parameter grid runs, or extract block scores, loadings, and AVE metrics from a
  multiblock latent-variable model. Triggers on: "RGCCA", "SGCCA", "multiblock",
  "multi-omics integration", "canonical correlation", "block scores", "AVE inner",
  "regularised CCA", "latent variable integration", "multiblock PLS", "GCCA".
---

# RGCCA Multiblock Analysis Skill

## Scope

Runs the full RGCCA analysis pipeline — QC, design matrix construction, single or
grid-search fitting, optional CV/permutation tuning, plot generation, and ranked run
comparison — on any set of named data blocks. Uses the `RGCCA` R package (v3.0.3)
via `Rscript` subprocess. Does NOT perform upstream preprocessing (normalisation,
batch correction, feature selection) beyond scaling; those steps must be done before
calling this skill.

---

## Inputs

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `blocks` | dict | yes | `{block_name: path_to_csv}` — one CSV per block |
| `sample_id_col` | str or null | no | Column name holding sample IDs; null = use row index |
| `response_block` | str | no | Name of the supervised response block |
| `design` | str or dict | yes | Named mode or explicit J×J connection matrix |
| `preprocessing` | dict | no | `scale`, `scale_block`, `NA_method` |
| `allow_constant_columns` | bool | no | Default false — fail loudly on constant columns |
| `parameter_grid` | list of dicts | yes | Each entry: lists of values for tau, ncomp, scheme, method, sparsity, superblock, comp_orth |
| `tuning.cv` | dict | no | CV tuning config (enabled, par_type, par_value, k, n_run, metric, prediction_model) |
| `tuning.permutation` | dict | no | Permutation tuning config (enabled, par_type, par_value, n_perms) |
| `ranking_criterion` | str | yes | Key from run manifest metrics to rank runs by |
| `ranking_direction` | str | no | "max" (default) or "min" |
| `plots.n_mark` | int | no | Top-N loadings shown per block (default 10) |
| `plots.comp` | list | no | Component indices to plot (default [1, 2]) |
| `seed` | int | no | Random seed (default 42) |

### Block CSV format

- Header row required.
- Rows = samples, columns = features (plus optional sample ID column).
- All values must be numeric (except the sample ID column).
- All blocks must share the same sample IDs.

### Design modes (both naming conventions accepted)

| Accepted strings | Meaning |
|-----------------|---------|
| `"full"`, `"pair"` | All block pairs connected (1 − I matrix) |
| `"all"` | All connections including self-loops |
| `"star"`, `"response"`, `"response-centered"` | Star topology centred on `response_block` |
| dict-of-dicts | Explicit J×J matrix: `{"blockA": {"blockB": 1, "blockC": 0}, ...}` |

### Valid RGCCA parameter values

- **method**: `"rgcca"`, `"sgcca"`, `"pca"`, `"spca"`, `"pls"`, `"spls"`, `"cca"`, `"ifa"`, `"ra"`, `"gcca"`, `"maxvar"`, `"maxvar-b"`, `"maxvar-a"`, `"mfa"`, `"mcia"`, `"mcoa"`, `"cpca-1"`, `"cpca-2"`, `"cpca-4"`, `"hpca"`, `"maxbet-b"`, `"maxbet"`, `"maxdiff-b"`, `"maxdiff"`, `"sabscor"`, `"ssqcor"`, `"ssqcov-1"`, `"ssqcov-2"`, `"ssqcov"`, `"sumcor"`, `"sumcov-1"`, `"sumcov-2"`, `"sumcov"`, `"sabscov-1"`, `"sabscov-2"`
- **scheme**: `"horst"`, `"centroid"`, `"factorial"`
- **NA_method**: `"na.ignore"`, `"na.omit"`
- **scale_block**: `"none"`, `"inertia"`, `"lambda1"`, `"ssq"`
- **tau**: scalar in [0, 1] or list per block; 0 = CCA-like, 1 = PCA-like
- **sparsity**: scalar in (0, 1] or list per block; 1 = no sparsity
- **ncomp**: positive integer or list per block

### Ranking criterion keys (available in run manifest)

- `AVE_inner_mean` — mean AVE_inner across components
- `AVE_outer_mean` — mean AVE_outer across components
- `AVE_inner_comp1` — AVE_inner for component 1
- `AVE_outer_comp1` — AVE_outer for component 1
- `crit_final` — final convergence criterion value
- `cv_metric_mean` — mean CV metric (only if CV tuning was run)
- `perm_best_crit` — best permutation criterion (only if permutation tuning was run)

---

## Outputs (all written to `/mnt/results/rgcca_<timestamp>/`)

```
rgcca_<timestamp>/
├── config_used.json
├── qc_report.txt
├── design_matrix.csv
├── run_manifest.json
├── ranked_runs.csv
├── ranked_runs_summary.md
├── tuning/
│   ├── cv_best_params.json, cv_stats.csv, cv_plot.png/.svg
│   └── perm_best_params.json, perm_stats.csv, perm_plot.png/.svg
└── runs/
    └── run_NNNN_<hash>/
        ├── config_run.json, manifest.json, summary.txt
        ├── scores_<block>.csv
        ├── weights_a_<block>.csv, weights_astar_<block>.csv
        └── plots/
            ├── plot_samples_*.png/.svg
            ├── plot_loadings_*.png/.svg
            ├── plot_cor_circle_*.png/.svg
            └── plot_ave.png/.svg
```

---

## Workflow Steps

1. **Load and validate config** — parse the config dict/JSON; raise `RGCCAConfigError` for missing required keys, unknown design modes, or unrecognised `ranking_criterion`.
2. **Load blocks** — read each CSV; parse or infer sample IDs.
3. **QC blocks** (`rgcca_qc.py`) — align sample IDs, check missingness, detect constant columns, duplicated IDs, non-numeric features. Fail loudly unless `allow_constant_columns: true`.
4. **Build design matrix** (`rgcca_design.py`) — construct J×J connection matrix from named mode or explicit dict; write to `design_matrix.csv`.
5. **Expand parameter grid** — Cartesian product of all grid entries; deduplicate.
6. **Fit each run** (`rgcca_fit.R`) — one `Rscript` call per parameter combination; serialise scores, loadings, AVE, manifest.
7. **Generate plots** (`rgcca_plots.R`) — one `Rscript` call per run; save all plot types as PNG + SVG.
8. **Run CV tuning** (`rgcca_tune_cv.R`) — if `tuning.cv.enabled`; serialise best params and stats.
9. **Run permutation tuning** (`rgcca_tune_perm.R`) — if `tuning.permutation.enabled`; serialise best params and stats.
10. **Rank runs** (`rgcca_compare.py`) — load all manifests; sort by `ranking_criterion`; write `ranked_runs.csv` and `ranked_runs_summary.md` explaining why the top run was selected.
11. **Return summary dict** — paths to all outputs, top-run params, key metrics.

---

## How to Call This Skill

```python
from rgcca_runner import run_rgcca

config = {
    "blocks": {
        "transcriptomics": "/path/to/rna.csv",
        "proteomics":      "/path/to/prot.csv",
        "clinical":        "/path/to/clin.csv"
    },
    "sample_id_col": "sample_id",
    "response_block": "clinical",
    "design": "star",
    "preprocessing": {
        "scale": True,
        "scale_block": "inertia",
        "NA_method": "na.ignore"
    },
    "allow_constant_columns": False,
    "parameter_grid": [
        {
            "tau":       [0, 0.5, 1],
            "ncomp":     [2],
            "scheme":    ["factorial"],
            "method":    ["rgcca"],
            "sparsity":  [1],
            "superblock":[False],
            "comp_orth": [True]
        }
    ],
    "tuning": {
        "cv": {
            "enabled": True,
            "par_type": "tau",
            "par_value": [0, 0.5, 1],
            "k": 5,
            "n_run": 10,
            "metric": "RMSE",   # RGCCA 3.0.3: "RMSE" or "MAE" only
            "prediction_model": "lm"
        },
        "permutation": {
            "enabled": True,
            "par_type": "tau",
            "par_value": [0, 0.5, 1],
            "n_perms": 100
        }
    },
    "ranking_criterion": "AVE_inner_mean",
    "ranking_direction": "max",
    "plots": {"n_mark": 10, "comp": [1, 2]},
    "seed": 42
}

result = run_rgcca(config)
print(result["ranked_runs_path"])
print(result["top_run"])
```

---

## Scientific Caveats

- **Sample size**: RGCCA is reliable with as few as 10–15 matched samples, but CV tuning with k=5 requires at least 5 samples. Use `k=3` or `k=2` (LOO) for very small datasets.
- **Block scaling**: `scale_block="inertia"` is recommended when blocks have very different numbers of features. Without it, large blocks dominate the solution.
- **tau interpretation**: tau=0 maximises inter-block covariance (CCA-like); tau=1 maximises within-block variance (PCA-like). For small n, tau=1 is more stable.
- **Sparsity and sgcca**: `method="sgcca"` with `sparsity < 1` performs variable selection. The selected variables are not stable across bootstrap resamples unless n is large; interpret with caution.
- **AVE_inner vs AVE_outer**: AVE_inner measures how well the latent components capture inter-block relationships; AVE_outer measures within-block variance explained. Neither is universally better — choose `ranking_criterion` based on your scientific goal.
- **Superblock**: Setting `superblock=True` adds a concatenated block; useful for visualising all variables in one space but changes the optimisation problem.
- **Reproducibility**: The seed is passed to both Python and R (`set.seed()`). Results are fully reproducible given the same RGCCA version.
- **RGCCA version**: This skill targets RGCCA 3.0.3. The API changed substantially between v2 and v3; do not use with v2.x.
- **SVG export**: SVG plots require the `svglite` R package. If `svglite` is unavailable (e.g., due to a `systemfonts` version conflict), the skill automatically falls back to PNG-only output. Install `svglite` with `install.packages("svglite")` in an environment where `systemfonts >= 1.3.0` is available.
- **CV metric**: `rgcca_cv()` in RGCCA 3.0.3 only accepts `"RMSE"` or `"MAE"` as the `metric` argument. The `"cor"` value used in older documentation is not valid.
- **Response block and cor_circle**: When a response block is specified, RGCCA enforces non-orthogonal components for the response block. The `cor_circle` plot is skipped for the response block in this case (RGCCA 3.0.3 restriction); this is handled gracefully.
