#!/usr/bin/env python
"""
Fine-tune pretrained scGPT_human for perturbation prediction on a GEARS dataset
(train-from-base path; scGPT_human is MIT-licensed -> fully commercial-safe).

Based on the official scGPT perturbation tutorial (bowang-lab), adapted with:
  - use_fast_transformer=False (no flash-attn dependency; identical model math)
  - Wqkv -> in_proj key remap so the pretrained (flash) attention weights load
    into the standard MultiheadAttention model
  - best-model checkpointing (local .pt then copy; S3 FUSE has no random-access writes)
  - resume, wall-time cap, and early stopping

Generalized over --dataset (norman | adamson | dixit | replogle_* | custom-registered).

Usage:
  python train_scgpt.py --dataset norman --max_epochs 15 --time_cap_min 120
  python train_scgpt.py --dataset adamson --max_epochs 6 --time_cap_min 90
"""
import argparse, json, os, time, warnings, shutil
from pathlib import Path
import numpy as np
import torch

warnings.filterwarnings("ignore")
import logging
logging.getLogger("scgpt").setLevel(logging.WARNING)

from gears import PertData
from scgpt.model import TransformerGenerator
from scgpt.loss import masked_mse_loss
from scgpt.tokenizer.gene_tokenizer import GeneVocab
from scgpt.utils import set_seed, map_raw_id_to_vocab_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="norman", help="GEARS dataset name (or registered custom name).")
    ap.add_argument("--max_epochs", type=int, default=15)
    ap.add_argument("--time_cap_min", type=float, default=120.0)
    ap.add_argument("--early_stop", type=int, default=5)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch_size", type=int, default=16)
    ap.add_argument("--max_seq_len", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--split_seed", type=int, default=42)
    ap.add_argument("--data_dir", default="/workspace/data")
    ap.add_argument("--load_model", default="/workspace/save/scGPT_human")
    ap.add_argument("--save_dir", default="/mnt/results/execution_trace/finetune")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    set_seed(args.seed)
    os.makedirs(args.save_dir, exist_ok=True)
    local_dir = "/workspace/save/finetune_local"
    os.makedirs(local_dir, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def save_ckpt(state_dict):
        lp = local_dir + "/best_model.pt"
        torch.save(state_dict, lp)
        shutil.copy(lp, args.save_dir + "/best_model.pt")   # local -> persistent

    # ---- tutorial-faithful settings ----
    pad_token = "<pad>"; special_tokens = [pad_token, "<cls>", "<eoc>"]
    pad_value = 0; pert_pad_id = 2
    include_zero_gene = "all"; max_seq_len = args.max_seq_len
    CLS = False; CCE = False; MVC = False; ECS = False
    cell_emb_style = "cls"; mvc_decoder_style = "inner product, detach"; amp = True
    dropout = 0.2

    print(f"[data] loading {args.dataset} + simulation split (seed {args.split_seed})...", flush=True)
    pert_data = PertData(args.data_dir)
    pert_data.load(data_name=args.dataset)
    pert_data.prepare_split(split="simulation", seed=args.split_seed)
    pert_data.get_dataloader(batch_size=args.batch_size, test_batch_size=args.batch_size)

    model_dir = Path(args.load_model)
    vocab = GeneVocab.from_file(model_dir / "vocab.json")
    for s in special_tokens:
        if s not in vocab:
            vocab.append_token(s)
    pert_data.adata.var["id_in_vocab"] = [1 if g in vocab else -1
                                          for g in pert_data.adata.var["gene_name"]]
    genes = pert_data.adata.var["gene_name"].tolist()
    gene_ids_in_vocab = np.array(pert_data.adata.var["id_in_vocab"])
    print(f"[vocab] match {int((gene_ids_in_vocab >= 0).sum())}/{len(gene_ids_in_vocab)} genes", flush=True)
    vocab.set_default_index(vocab["<pad>"])
    gene_ids = np.array([vocab[g] if g in vocab else vocab["<pad>"] for g in genes], dtype=int)
    n_genes = len(genes)

    with open(model_dir / "args.json") as f:
        mc = json.load(f)
    model = TransformerGenerator(
        len(vocab), mc["embsize"], mc["nheads"], mc["d_hid"], mc["nlayers"],
        nlayers_cls=mc["n_layers_cls"], n_cls=1, vocab=vocab, dropout=dropout,
        pad_token=pad_token, pad_value=pad_value, pert_pad_id=pert_pad_id, do_mvc=MVC,
        cell_emb_style=cell_emb_style, mvc_decoder_style=mvc_decoder_style,
        use_fast_transformer=False,
    )

    load_param_prefixs = ["encoder", "value_encoder", "transformer_encoder"]
    ckpt = args.save_dir + "/best_model.pt"
    start_epoch = 1; best_val = float("inf"); patience = 0; history = []
    if args.resume and os.path.exists(ckpt):
        model.load_state_dict(torch.load(ckpt, map_location="cpu"))
        if os.path.exists(args.save_dir + "/state.json"):
            st = json.load(open(args.save_dir + "/state.json"))
            start_epoch = st["epoch"] + 1; best_val = st["best_val"]
            patience = st["patience"]; history = st.get("history", [])
        print(f"[resume] from epoch {start_epoch}, best_val={best_val:.4f}", flush=True)
    else:
        model_dict = model.state_dict()
        pretrained = torch.load(model_dir / "best_model.pt", map_location="cpu")
        pretrained = {k: v for k, v in pretrained.items()
                      if any(k.startswith(p) for p in load_param_prefixs)}
        # scGPT_human was trained with the fused flash-attn QKV (self_attn.Wqkv, [3*emb, emb]),
        # mathematically identical to nn.MultiheadAttention in_proj_{weight,bias}. Remap keys.
        remapped = {}
        for k, v in pretrained.items():
            if k.endswith("self_attn.Wqkv.weight"):
                remapped[k.replace("Wqkv.weight", "in_proj_weight")] = v
            elif k.endswith("self_attn.Wqkv.bias"):
                remapped[k.replace("Wqkv.bias", "in_proj_bias")] = v
            else:
                remapped[k] = v
        pretrained = {k: v for k, v in remapped.items()
                      if k in model_dict and v.shape == model_dict[k].shape}
        n_attn = sum(1 for k in pretrained if "in_proj_weight" in k)
        model_dict.update(pretrained); model.load_state_dict(model_dict)
        print(f"[init] loaded {len(pretrained)} pretrained tensors from scGPT_human "
              f"(incl. {n_attn} remapped attention layers)", flush=True)
    model.to(device)

    criterion = masked_mse_loss
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, 1, gamma=0.9)
    scaler = torch.cuda.amp.GradScaler(enabled=amp)

    train_loader = pert_data.dataloader["train_loader"]
    val_loader = pert_data.dataloader["val_loader"]

    def run_epoch(loader, train=True):
        model.train() if train else model.eval()
        total_loss = 0.0; nb = 0
        for batch_data in loader:
            batch_size = len(batch_data.y); batch_data.to(device)
            x = batch_data.x
            ori = x[:, 0].view(batch_size, n_genes)
            pert_flags = x[:, 1].long().view(batch_size, n_genes)
            tgt = batch_data.y
            input_gene_ids = torch.arange(n_genes, device=device, dtype=torch.long)
            if len(input_gene_ids) > max_seq_len:
                input_gene_ids = torch.randperm(len(input_gene_ids), device=device)[:max_seq_len]
            iv = ori[:, input_gene_ids]; ipf = pert_flags[:, input_gene_ids]; tv = tgt[:, input_gene_ids]
            mig = map_raw_id_to_vocab_id(input_gene_ids, gene_ids).repeat(batch_size, 1)
            mask = torch.zeros_like(iv, dtype=torch.bool, device=device)
            with torch.cuda.amp.autocast(enabled=amp):
                with torch.set_grad_enabled(train):
                    out = model(mig, iv, ipf, src_key_padding_mask=mask,
                                CLS=CLS, CCE=CCE, MVC=MVC, ECS=ECS, do_sample=not train)
                    ov = out["mlm_output"]
                    mpos = torch.ones_like(iv, dtype=torch.bool)
                    loss = criterion(ov, tv, mpos)
            if train:
                model.zero_grad(); scaler.scale(loss).backward(); scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=False)
                scaler.step(optimizer); scaler.update()
            total_loss += loss.item(); nb += 1
            del out, ov, loss, iv, ipf, tv, mig, mask
        torch.cuda.empty_cache()
        return total_loss / max(nb, 1)

    t_start = time.time(); cap_s = args.time_cap_min * 60
    print(f"[train] {args.dataset} start_epoch={start_epoch} max_epochs={args.max_epochs} "
          f"time_cap={args.time_cap_min}min device={device}", flush=True)
    stopped_reason = "completed"
    for epoch in range(start_epoch, args.max_epochs + 1):
        te = time.time()
        tr_loss = run_epoch(train_loader, True)
        va_loss = run_epoch(val_loader, False)
        scheduler.step()
        dt = time.time() - te
        history.append({"epoch": epoch, "train_loss": tr_loss, "val_loss": va_loss, "epoch_sec": dt})
        print(f"[epoch {epoch}] train_mse={tr_loss:.4f} val_mse={va_loss:.4f} ({dt:.0f}s) best={best_val:.4f}", flush=True)
        if va_loss < best_val:
            best_val = va_loss; patience = 0
            save_ckpt(model.state_dict())
        else:
            patience += 1
        json.dump({"epoch": epoch, "best_val": best_val, "patience": patience,
                   "history": history, "stopped_reason": "running"},
                  open(args.save_dir + "/state.json", "w"), indent=2)
        if patience >= args.early_stop:
            stopped_reason = f"early_stop(patience={args.early_stop})"
            print(f"[stop] {stopped_reason}", flush=True); break
        elapsed = time.time() - t_start
        if elapsed + dt * 1.15 > cap_s:
            stopped_reason = f"time_cap({args.time_cap_min}min) reached after epoch {epoch}"
            print(f"[stop] {stopped_reason}", flush=True); break

    final = {"dataset": args.dataset,
             "epoch": history[-1]["epoch"] if history else start_epoch - 1,
             "best_val": best_val, "patience": patience, "history": history,
             "stopped_reason": stopped_reason, "total_train_sec": time.time() - t_start,
             "use_fast_transformer": False, "max_epochs": args.max_epochs,
             "time_cap_min": args.time_cap_min, "lr": args.lr, "batch_size": args.batch_size,
             "split_seed": args.split_seed}
    json.dump(final, open(args.save_dir + "/state.json", "w"), indent=2)
    print(f"[done] best_val={best_val:.4f} reason={stopped_reason} total={final['total_train_sec']:.0f}s", flush=True)


if __name__ == "__main__":
    main()
