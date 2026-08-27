#!/usr/bin/env python
"""
Fetch model weights for the fast (no-training) benchmark path.

Two modes:
  --which base    Download the scGPT_human base checkpoint (MIT). Always needed
                  for prediction (provides args.json + vocab.json + weights).
                  Fetches the 3 files INDIVIDUALLY by file id (the Google-Drive
                  *folder* download is flaky and sometimes drops args.json).

  --which norman-ft
                  Download a scGPT checkpoint already fine-tuned on Norman 2019
                  from the HuggingFace Hub (matthewshu/scGPT-norman-ft). This is
                  a convenience mirror for a fast reproduction on Norman ONLY;
                  for any other dataset, train from base with train_scgpt.py.

Notes on licensing:
  - scGPT (code + weights) is MIT-licensed -> commercial use OK.
  - The norman-ft mirror is a derivative of the MIT scGPT base + the public
    Norman 2019 data (GEO GSE133344); use train-from-base if you need a clean
    provenance chain for a specific dataset.
"""
import argparse, os, sys, shutil
from pathlib import Path

# scGPT_human base (official Google Drive folder, downloaded per-file)
BASE_FILES = {
    "args.json":     "1hh2zGKyWAx3DyovD30GStZ3QlzmSqdk1",   # ~1.3 KB
    "vocab.json":    "1H3E_MJ-Dl36AQV6jLbna2EdvgPaqvqcC",   # ~1.32 MB
    "best_model.pt": "14AebJfGOUF047Eg40hk57HCtrb0fyDTm",   # ~205 MB
}
NORMAN_FT_REPO = "matthewshu/scGPT-norman-ft"   # fine-tuned best_model.pt (~207 MB)


def get_base(out_dir):
    import gdown
    os.makedirs(out_dir, exist_ok=True)
    for name, fid in BASE_FILES.items():
        dst = os.path.join(out_dir, name)
        gdown.download(id=fid, output=dst, quiet=False)
        sz = os.path.getsize(dst) if os.path.exists(dst) else 0
        print(f"[base] {name}: {sz} bytes", flush=True)
    missing = [n for n in BASE_FILES if not os.path.exists(os.path.join(out_dir, n))]
    if missing:
        sys.exit(f"[error] missing base files after download: {missing}")
    print(f"[done] base checkpoint -> {out_dir}", flush=True)


def get_norman_ft(out_dir, base_dir):
    """Fetch fine-tuned best_model.pt; reuse base args.json + vocab.json (same architecture)."""
    from huggingface_hub import hf_hub_download
    os.makedirs(out_dir, exist_ok=True)
    p = hf_hub_download(repo_id=NORMAN_FT_REPO, filename="best_model.pt")
    dst = os.path.join(out_dir, "best_model.pt")
    shutil.copy(p, dst)
    print(f"[norman-ft] best_model.pt: {os.path.getsize(dst)} bytes", flush=True)
    # architecture metadata (args.json/vocab.json) comes from the base checkpoint
    for meta in ("args.json", "vocab.json"):
        src = os.path.join(base_dir, meta)
        if os.path.exists(src) and not os.path.exists(os.path.join(out_dir, meta)):
            shutil.copy(src, os.path.join(out_dir, meta))
    if not os.path.exists(os.path.join(out_dir, "args.json")):
        print("[warn] args.json/vocab.json not found; run --which base first "
              "(same directory) or point predict.py --base_dir at the base checkpoint.", flush=True)
    print(f"[done] norman-ft checkpoint -> {out_dir}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", choices=["base", "norman-ft"], default="base")
    ap.add_argument("--out_dir", default="/workspace/save/scGPT_human")
    ap.add_argument("--base_dir", default="/workspace/save/scGPT_human",
                    help="Where base args.json/vocab.json live (used by --which norman-ft).")
    args = ap.parse_args()
    if args.which == "base":
        get_base(args.out_dir)
    else:
        get_norman_ft(args.out_dir, args.base_dir)


if __name__ == "__main__":
    main()
