# GPU & platform notes (hard-won operational rules)

These rules come from actually running the reference benchmark. Ignoring them wastes
GPU sandboxes and hours. Read before launching GPU work.

## 1. `Gpu` `timeout` is the sandbox TOTAL LIFETIME — not a per-command idle timeout

The single most important rule. `timeout` bounds how long the **entire sandbox** lives.
If a command runs past it, the whole sandbox is terminated mid-run and **`/workspace` is
lost**. In the reference build, three sandboxes were killed this way before the cause was
understood (they showed up as `exit 137`).

- Prediction (~30 min at batch 32): create with `timeout >= 7200` (2 h).
- Fine-tuning: `timeout >= 9000` (2.5 h) and use the script's own `--time_cap_min` early
  stop so training checkpoints and exits *before* the sandbox deadline.
- Always set `timeout` generously above your expected runtime. There is no penalty for a
  higher ceiling; there is a large penalty (lost work) for a ceiling that is too low.

## 2. There is no background exec on the GPU tool — use in-script time caps + checkpoints

The `Gpu` tool runs commands synchronously. Long jobs must protect themselves:
- `train_scgpt.py` has `--time_cap_min`: it stops after the epoch that would exceed the
  cap, saves the best checkpoint, and writes `state.json`. Set the cap comfortably below
  the sandbox `timeout` (e.g. cap 105 min inside a 9000 s = 150 min sandbox).
- It also supports `--resume`: rerun with a fresh sandbox to continue from the last saved
  checkpoint. Prefer several short capped runs over one long run.
- **Save prediction arrays IMMEDIATELY** after the forward pass, before any metric code
  (`predict.py` does this). If downstream code fails, the expensive GPU output survives.

## 3. Batch size and CUDA memory

- Attention cost is O(seq²) and the Norman gene set is ~5045 genes, so memory scales
  sharply with batch size. **Batch 32 is safe (~8 GB); batch 64 OOMs** on a 24 GB A10G.
- Always export before the run:
  ```
  export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
  ```
- Default GPU for this workload: **A10G (24 GB)** is sufficient. Larger gene sets or
  bigger batches may need an A100.

## 4. S3 FUSE mounts do not support random-access writes

`/mnt/results` and `/mnt/shared-workspace` are S3-backed. Writing `.pt`, `.npy`, `.h5ad`,
`.db`, etc. directly there can corrupt or fail. Pattern used throughout the scripts:

```python
np.save("/workspace/preds/test_pred.npy", pred)          # local disk
shutil.copy("/workspace/preds/test_pred.npy", out_dir)   # then copy to /mnt/results
torch.save(sd, "/workspace/.../best_model.pt"); shutil.copy(..., save_dir)  # same for .pt
```

CSV / JSON / PNG / SVG / gzip write fine directly to `/mnt/results`. In **R**,
`file.copy()` to `/mnt/results` produces 0-byte files — use a shell `cp` instead.

## 5. Environment must be bootstrapped from scratch

scGPT and GEARS are **not** pre-installed; verify with direct imports if needed. Build
the environment once with `scripts/setup_env.sh` (conda, python=3.10). Pinned,
order-sensitive recipe:

- `torch==2.1.0 torchvision==0.16.0 torchtext==0.16.0` (cu121 index).
- `"numpy<2"`, `torch-geometric==2.4.0`, then `torch-scatter/torch-sparse/torch-cluster`
  from the `torch-2.1.0+cu121` wheel index.
- `cell-gears==0.0.2 --no-deps`, `scgpt==0.2.4 --no-deps` (install `--no-deps` to avoid
  pulling incompatible transitive pins), then the scanpy/anndata/etc. stack with
  `"pandas<2.2"`.
- On `/workspace`, `pip`/conda envs and downloaded weights **persist across hibernate**;
  use `Gpu` hibernate/resume to avoid re-installing between sessions.

## 6. Download robustness

- The `scGPT_human` Google-Drive **folder** download is flaky and sometimes drops
  `args.json`. Download the three files **individually by id** (`get_checkpoint.py --which base`).
- Verify sizes after download: `args.json` ~1.3 KB, `vocab.json` ~1.32 MB,
  base `best_model.pt` ~205 MB, norman-ft `best_model.pt` ~207 MB. A tiny `best_model.pt`
  usually means an unconfirmed Drive "are you sure" HTML page — retry with `gdown` (it
  handles the confirm token).

## 7. Misc gotchas
- `np.float` is removed in NumPy ≥ 1.24 — use `float` or `np.float32`/`np.float64`.
- Set `logging.getLogger("scgpt").setLevel(logging.ERROR)` to silence noisy per-batch logs.
- Run a `Read(mode="media_output_check")` on every saved figure PNG and on the final PDF;
  regenerate anything blank, clipped, or low-quality before finishing.

## 8. GPU concurrency limit
The `hpc_run_tool` path can return **429** when the concurrent GPU-job cap is hit. There
is no automatic retry — tell the user and wait, or reduce concurrency. This skill's core
workflow uses the `Gpu` sandbox tool (not the HPC queue), but the same account-level GPU
limits apply if you mix in HPC tools.
