#!/usr/bin/env python
"""
Predict post-perturbation expression on a GEARS test split and save raw arrays
IMMEDIATELY (before any fragile metric code). Metrics are computed separately on
CPU by aggregate_metrics.py.

Pluggable model adapters (--model):
  scgpt     scGPT TransformerGenerator; load fine-tuned or base+ft checkpoint.
  baseline  Control-mean predictor: every perturbation -> mean control profile.
            (A must-have reference: any real model must beat this on DE genes.)
  gears     GEARS GNN perturbation model (optional; requires a trained GEARS model
            dir saved via gears.save_model, or trains briefly if --gears_epochs>0).

All adapters emit the SAME output contract into --out_dir (written locally to
/workspace first, then copied; S3 FUSE has no random-access writes):
  test_pred.npy    (N, n_genes) float32
  test_truth.npy   (N, n_genes) float32
  test_pertcat.npy (N,) str
  test_de_idx.npy  (N, K) int32     # per-cell top-K DE gene indices
  ctrl_mean.npy    (n_genes,) float32
  meta.json        genes, subgroup, set2conditions, seed, model, vocab_match
"""
import argparse, json, os, time, warnings, shutil
from pathlib import Path
import numpy as np
import torch

warnings.filterwarnings("ignore")
import logging
logging.getLogger("scgpt").setLevel(logging.ERROR)

from gears import PertData


# ----------------------------------------------------------------------------- data
def load_pertdata(args):
    pd = PertData(args.data_dir)
    pd.load(data_name=args.dataset)
    pd.prepare_split(split=args.split, seed=args.split_seed)
    pd.get_dataloader(batch_size=args.batch_size, test_batch_size=args.batch_size)
    return pd


def collect_truth_and_deidx(loader):
    """Iterate once to gather ground-truth y, pert labels, and per-cell DE indices."""
    pert_cat, truth, de_idx_all = [], [], []
    for batch in loader:
        pert_cat.extend(batch.pert)
        truth.append(batch.y.detach().cpu().numpy().astype(np.float32))
        de = np.stack([np.asarray(d.cpu() if torch.is_tensor(d) else d, dtype=np.int32)
                       for d in batch.de_idx])
        de_idx_all.append(de)
    return (np.array(pert_cat),
            np.concatenate(truth, 0),
            np.concatenate(de_idx_all, 0))


# ------------------------------------------------------------------ scGPT adapter
def predict_scgpt(pd, args, device):
    from scgpt.model import TransformerGenerator
    from scgpt.tokenizer.gene_tokenizer import GeneVocab

    pad_token = "<pad>"; special_tokens = [pad_token, "<cls>", "<eoc>"]
    adata = pd.adata
    base = Path(args.base_model)
    vocab = GeneVocab.from_file(base / "vocab.json")
    for s in special_tokens:
        if s not in vocab:
            vocab.append_token(s)
    genes = adata.var["gene_name"].tolist(); n_genes = len(genes)
    vocab.set_default_index(vocab["<pad>"])
    gene_ids = np.array([vocab[g] if g in vocab else vocab["<pad>"] for g in genes], dtype=int)
    match = int(sum(1 for g in genes if g in vocab))
    print(f"[vocab] match {match}/{n_genes}", flush=True)

    mc = json.load(open(base / "args.json"))
    model = TransformerGenerator(
        len(vocab), mc["embsize"], mc["nheads"], mc["d_hid"], mc["nlayers"],
        nlayers_cls=mc["n_layers_cls"], n_cls=1, vocab=vocab, dropout=0.2,
        pad_token=pad_token, pad_value=0, pert_pad_id=2, do_mvc=False,
        cell_emb_style="cls", mvc_decoder_style="inner product, detach",
        use_fast_transformer=False,
    )
    # load fine-tuned weights (shape-matched subset; tolerant of key drift)
    ckpt = args.ft_ckpt or str(base / "best_model.pt")
    sd = torch.load(ckpt, map_location="cpu")
    # remap fused flash-attn Wqkv -> in_proj when loading a base (non-ft) checkpoint
    remap = {}
    for k, v in sd.items():
        if k.endswith("self_attn.Wqkv.weight"):
            remap[k.replace("Wqkv.weight", "in_proj_weight")] = v
        elif k.endswith("self_attn.Wqkv.bias"):
            remap[k.replace("Wqkv.bias", "in_proj_bias")] = v
        else:
            remap[k] = v
    md = model.state_dict()
    ld = {k: v for k, v in remap.items() if k in md and v.shape == md[k].shape}
    md.update(ld); model.load_state_dict(md); model.to(device); model.eval()
    print(f"[load] {len(ld)}/{len(md)} params from {ckpt}", flush=True)

    pert_cat, pred, truth, de_idx_all = [], [], [], []
    tl = pd.dataloader["test_loader"]; nb = len(tl); t0 = time.time()
    for i, batch in enumerate(tl):
        batch.to(device)
        pert_cat.extend(batch.pert)
        with torch.no_grad():
            p = model.pred_perturb(batch, include_zero_gene="all", gene_ids=gene_ids)
        pred.append(p.detach().cpu().numpy().astype(np.float32))
        truth.append(batch.y.detach().cpu().numpy().astype(np.float32))
        de = np.stack([np.asarray(d.cpu() if torch.is_tensor(d) else d, dtype=np.int32)
                       for d in batch.de_idx])
        de_idx_all.append(de)
        if i % 25 == 0 or i == nb - 1:
            print(f"[pred] batch {i}/{nb} ({time.time()-t0:.0f}s)", flush=True)
    return (np.array(pert_cat), np.concatenate(pred, 0),
            np.concatenate(truth, 0), np.concatenate(de_idx_all, 0),
            genes, match)


# --------------------------------------------------------------- baseline adapter
def predict_baseline(pd, args, device):
    """Control-mean predictor. pred[cell] = mean control expression, for every cell."""
    adata = pd.adata
    genes = adata.var["gene_name"].tolist(); n_genes = len(genes)
    ctrl_idx = np.where(adata.obs["condition"].values == "ctrl")[0]
    ctrl_mean = np.asarray(adata.X[ctrl_idx].mean(axis=0)).ravel().astype(np.float32)
    pert_cat, truth, de_idx_all = collect_truth_and_deidx(pd.dataloader["test_loader"])
    pred = np.broadcast_to(ctrl_mean, (truth.shape[0], n_genes)).astype(np.float32).copy()
    print(f"[baseline] pred = control mean broadcast over {truth.shape[0]} cells", flush=True)
    return pert_cat, pred, truth, de_idx_all, genes, n_genes


# ------------------------------------------------------------------ GEARS adapter
def predict_gears(pd, args, device):
    """GEARS GNN. Load a saved model dir (--gears_model_dir) or train briefly."""
    from gears import GEARS
    gears_model = GEARS(pd, device=str(device))
    gears_model.model_initialize(hidden_size=args.gears_hidden)
    if args.gears_model_dir and os.path.exists(args.gears_model_dir):
        gears_model.load_pretrained(args.gears_model_dir)
        print(f"[gears] loaded pretrained model from {args.gears_model_dir}", flush=True)
    elif args.gears_epochs > 0:
        print(f"[gears] training {args.gears_epochs} epochs ...", flush=True)
        gears_model.train(epochs=args.gears_epochs)
    else:
        raise SystemExit("[error] gears needs --gears_model_dir OR --gears_epochs > 0")

    adata = pd.adata
    genes = adata.var["gene_name"].tolist(); n_genes = len(genes)
    pert_cat, truth, de_idx_all = collect_truth_and_deidx(pd.dataloader["test_loader"])

    # GEARS predicts per-condition mean profiles; expand to per-cell to match truth.
    # Build the list of 1/2-gene perturbations from the test pert labels.
    def to_pert_list(lbl):
        gs = [g for g in lbl.split("+") if g != "ctrl"]
        return gs
    uniq = sorted(set(pert_cat))
    cond_pred = {}
    for lbl in uniq:
        gl = to_pert_list(lbl)
        if not gl:
            continue
        try:
            out = gears_model.predict([gl])
            key = "_".join(gl)
            cond_pred[lbl] = np.asarray(list(out.values())[0], dtype=np.float32)
        except Exception as e:
            print(f"[gears][warn] predict failed for {lbl}: {e}", flush=True)
    pred = np.zeros_like(truth, dtype=np.float32)
    for i, lbl in enumerate(pert_cat):
        if lbl in cond_pred:
            pred[i] = cond_pred[lbl]
    print(f"[gears] filled predictions for {len(cond_pred)}/{len(uniq)} conditions", flush=True)
    return pert_cat, pred, truth, de_idx_all, genes, n_genes


ADAPTERS = {"scgpt": predict_scgpt, "baseline": predict_baseline, "gears": predict_gears}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=list(ADAPTERS), default="scgpt")
    ap.add_argument("--dataset", default="norman")
    ap.add_argument("--split", default="simulation")
    ap.add_argument("--split_seed", type=int, default=42)
    ap.add_argument("--batch_size", type=int, default=32)
    ap.add_argument("--data_dir", default="/workspace/data")
    ap.add_argument("--out_dir", default="/mnt/results/execution_trace/preds")
    # scgpt
    ap.add_argument("--base_model", default="/workspace/save/scGPT_human")
    ap.add_argument("--ft_ckpt", default=None, help="Fine-tuned .pt (default: base_model/best_model.pt).")
    # gears
    ap.add_argument("--gears_model_dir", default=None)
    ap.add_argument("--gears_epochs", type=int, default=0)
    ap.add_argument("--gears_hidden", type=int, default=64)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    local = "/workspace/preds"; os.makedirs(local, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"[data] {args.dataset} + {args.split} split (seed {args.split_seed}) | model={args.model}", flush=True)
    pd = load_pertdata(args)
    adata = pd.adata

    pert_cat, pred, truth, de_idx_all, genes, vocab_match = ADAPTERS[args.model](pd, args, device)

    print(f"[pred] {pred.shape}. SAVING NOW (before metrics).", flush=True)
    np.save(f"{local}/test_pred.npy", pred)
    np.save(f"{local}/test_truth.npy", truth)
    np.save(f"{local}/test_pertcat.npy", pert_cat)
    np.save(f"{local}/test_de_idx.npy", de_idx_all)
    ctrl_idx = np.where(adata.obs["condition"].values == "ctrl")[0]
    ctrl_mean = np.asarray(adata.X[ctrl_idx].mean(axis=0)).ravel().astype(np.float32)
    np.save(f"{local}/ctrl_mean.npy", ctrl_mean)
    json.dump({
        "dataset": args.dataset, "model": args.model, "split": args.split,
        "seed": args.split_seed, "n_genes": len(genes), "genes": genes,
        "vocab_match": vocab_match, "subgroup": pd.subgroup,
        "set2conditions": {k: list(v) for k, v in pd.set2conditions.items()},
    }, open(f"{local}/meta.json", "w"))
    for f in ["test_pred.npy", "test_truth.npy", "test_pertcat.npy",
              "test_de_idx.npy", "ctrl_mean.npy", "meta.json"]:
        shutil.copy(f"{local}/{f}", f"{args.out_dir}/{f}")
    print(f"[save] all arrays -> {args.out_dir}", flush=True)
    print("[done]", flush=True)


if __name__ == "__main__":
    main()
