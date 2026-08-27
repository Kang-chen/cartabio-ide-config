# Datasets & licensing (commercial-use safety)

This skill is designed so that a default run uses only **open, commercial-use-friendly**
components. Before using any dataset or model in a commercial context, confirm its
license — `prepare_data.py` enforces an allow-list guard for datasets, and the notes
below record what has been verified.

## Models

| Component | License | Commercial use | Notes |
|-----------|---------|----------------|-------|
| scGPT (code + `scGPT_human` weights) | MIT | Yes | Official repo `bowang-lab/scGPT`; PyPI `scgpt` v0.2.4 is MIT. |
| GEARS (code) | MIT | Yes | `snap-stanford/GEARS`; PyPI `cell-gears` v0.0.2. |
| `matthewshu/scGPT-norman-ft` (HF) | derivative of MIT scGPT + public Norman data | Yes, with attribution | Convenience mirror for the Norman fast path only; for a clean provenance chain on any dataset, train from the MIT base with `train_scgpt.py`. |

**Default license-safe path:** train scGPT from the MIT `scGPT_human` base on an open
dataset. The fine-tuned HF mirror is a *convenience* fast path for Norman reproduction,
not the license-safety default.

## Datasets (allow-listed in `prepare_data.py`)

All are deposited public Perturb-seq datasets, distributed by GEARS via `PertData.load`:

| `--dataset` | Study | Deposit | Notes |
|-------------|-------|---------|-------|
| `norman` | Norman et al. 2019, *Science* | GEO **GSE133344** | CRISPRa, K562, single + two-gene perturbations. |
| `adamson` | Adamson et al. 2016, *Cell* | GEO **GSE90546** | CRISPRi UPR screen. |
| `dixit` | Dixit et al. 2016, *Cell* | GEO **GSE90063** | Original Perturb-seq. |
| `replogle_k562_essential` | Replogle et al. 2022, *Cell* | public (Weissman lab) | Genome-scale essential-gene screen (K562). |
| `replogle_rpe1_essential` | Replogle et al. 2022, *Cell* | public (Weissman lab) | Essential-gene screen (RPE1). |

Public GEO deposits and the associated papers make the underlying **data** freely usable
for research and, in practice, commercial R&D. Note a subtlety worth recording: some
*analysis-notebook repositories* around these datasets carry copyleft code licenses
(e.g. the Norman analysis notebooks are LGPL-3.0). That license governs **that code**,
not the deposited sequencing data — this skill does not vendor those notebooks.

## The licensing guard (`prepare_data.py`)

- Datasets in the allow-list (`OPEN_DATASETS`) run without friction and print their
  provenance line.
- Any other `--dataset` name **exits with a message** instructing the user to verify the
  license and re-run with `--allow_unlisted` (an explicit, logged override).
- `--dataset custom` (a user-supplied `.h5ad` via `--adata`) prints a reminder that the
  user is responsible for confirming the license before commercial use.

To add a dataset to the allow-list, verify its deposit + license, then add an entry to
`OPEN_DATASETS` in `prepare_data.py` with a one-line provenance string.

## Custom AnnData requirements

A user dataset must be ingestible by GEARS `new_data_process`:
- `adata.obs['condition']` with control labelled `"ctrl"` and perturbations as
  `"GENE+ctrl"` (single) or `"GENEA+GENEB"` (combo), using symbols in `adata.var`.
- a cell-type / covariate column as required by the GEARS version in use.
- raw or normalized counts in `adata.X`; GEARS computes its own DE gene sets into
  `adata.uns` during processing.

If a dataset's genes barely overlap the scGPT vocabulary, expect weaker absolute metrics;
`predict.py` prints the vocab-match fraction so this is visible up front.

## Always re-verify
Licenses change. For any commercial deployment, re-confirm the current license of scGPT,
GEARS, any downloaded checkpoint, and the specific dataset deposit before shipping.
