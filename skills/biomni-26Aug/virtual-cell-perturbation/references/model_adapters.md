# Model adapters

The benchmark separates **prediction** (`scripts/predict.py`, produces raw arrays)
from **evaluation** (`scripts/aggregate_metrics.py`, computes GEARS metrics). Any
model can be plugged in by implementing an adapter that emits the standard output
contract; the metric and figure code never changes.

## Output contract (what every adapter must produce)

Written by `predict.py` into `--out_dir` (locally to `/workspace/preds` first, then
copied — S3 FUSE does not support random-access writes):

| file | shape | dtype | meaning |
|------|-------|-------|---------|
| `test_pred.npy` | (N, n_genes) | float32 | predicted post-perturbation expression, per test cell |
| `test_truth.npy` | (N, n_genes) | float32 | measured post-perturbation expression |
| `test_pertcat.npy` | (N,) | str | perturbation label per cell, e.g. `"JUN+CEBPA"`, `"MAP2K3+ctrl"` |
| `test_de_idx.npy` | (N, K) | int32 | per-cell indices of the top-K DE genes (K=20) |
| `ctrl_mean.npy` | (n_genes,) | float32 | mean control (unperturbed) profile |
| `meta.json` | — | — | genes, subgroup, set2conditions, seed, model, vocab_match |

`N` = number of held-out test cells; `pred[i]`, `truth[i]`, `pert_cat[i]`, `de_idx[i]`
must be row-aligned. `de_idx` and `truth` come straight from the GEARS test loader,
so they are identical regardless of model — only `pred` differs per adapter.

## Built-in adapters

### `scgpt` (default)
scGPT `TransformerGenerator`. Constructed from the base checkpoint's `args.json`:

```
TransformerGenerator(len(vocab), embsize=512, nhead=8, d_hid=512, nlayers=12,
    nlayers_cls=3, n_cls=1, vocab=vocab, dropout=0.2, pad_token="<pad>",
    pad_value=0, pert_pad_id=2, do_mvc=False, cell_emb_style="cls",
    mvc_decoder_style="inner product, detach", use_fast_transformer=False)
```

- `special_tokens = ["<pad>", "<cls>", "<eoc>"]`, `include_zero_gene="all"`.
- **Prediction** uses `model.pred_perturb(batch, include_zero_gene="all", gene_ids=gene_ids)`
  — the exact tutorial `eval_perturb` call.
- **Weight loading** is tolerant: keep only shape-matched keys
  (`{k:v for k,v in sd.items() if k in md and v.shape==md[k].shape}`).
- **Wqkv → in_proj remap** (critical): the public `scGPT_human` base checkpoint was
  trained with the fused flash-attention QKV projection (`self_attn.Wqkv.{weight,bias}`,
  shape `[3*emb, emb]`), which is mathematically identical to
  `nn.MultiheadAttention.in_proj_{weight,bias}`. Because we build the model with
  `use_fast_transformer=False` (no flash-attn dependency), the keys must be remapped
  when loading a **base** checkpoint. Fine-tuned checkpoints produced by this skill's
  `train_scgpt.py` are already in the `in_proj` layout, so the remap is a no-op for them.
- `--ft_ckpt` selects a fine-tuned checkpoint; default is `<base_model>/best_model.pt`.

### `baseline` (always run this)
Control-mean predictor: `pred[cell] = ctrl_mean` for every cell. This is the honest
floor — any real model must beat it on **DE genes** and on **Δ / direction** metrics.
It is cheap (no GPU) and is also recomputed for free inside `aggregate_metrics.py`.

### `gears` (optional)
GEARS GNN. Either load a saved model directory (`--gears_model_dir`) or train briefly
(`--gears_epochs > 0`). GEARS predicts a per-condition mean profile; the adapter expands
those to per-cell rows so the output matches the `test_truth` layout. GEARS uses the
same `PertData` object, so splits and DE indices are identical to the scGPT run — a
clean apples-to-apples comparison.

## Adding a new model

1. Write `predict_<name>(pd, args, device)` that returns
   `(pert_cat, pred, truth, de_idx_all, genes, vocab_match_or_ngenes)`.
   - Reuse `collect_truth_and_deidx(pd.dataloader["test_loader"])` to get
     `truth`, `pert_cat`, `de_idx` for free; you only need to fill `pred`.
2. Register it in the `ADAPTERS` dict in `predict.py`.
3. Everything downstream (metrics, figures, report) works unchanged.

**Key invariant:** never recompute DE genes or ground truth yourself — take them from
the GEARS loader so all models are scored on identical targets.
