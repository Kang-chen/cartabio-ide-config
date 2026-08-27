#!/usr/bin/env python
"""
Load a Perturb-seq dataset via GEARS PertData, build a held-out split, and print
the split composition for provenance. Generalized over dataset and split.

Supports:
  - Built-in GEARS datasets: norman, adamson, dixit, replogle_k562_essential,
    replogle_rpe1_essential  (all open, commercial-use-safe deposited data).
  - A user-supplied AnnData (.h5ad) in GEARS format via --adata / --data_name.

The GEARS `simulation` split is deterministic per seed, so recording the seed +
regime counts makes the benchmark reproducible.

This script does NOT need a GPU; it only downloads/caches data and materializes a
split. It also runs a lightweight OPEN-DATA guard (see OPEN_DATASETS below).
"""
import argparse, json, sys
from pathlib import Path

# Datasets known to be open / commercial-use-friendly (deposited public data).
# Extend this list only with datasets you have verified are open.
OPEN_DATASETS = {
    "norman": "Norman et al. 2019, Science; GEO GSE133344 (public).",
    "adamson": "Adamson et al. 2016, Cell; GEO GSE90546 (public).",
    "dixit": "Dixit et al. 2016, Cell; GEO GSE90063 (public).",
    "replogle_k562_essential": "Replogle et al. 2022, Cell; public (Weissman lab).",
    "replogle_rpe1_essential": "Replogle et al. 2022, Cell; public (Weissman lab).",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="norman",
                    help="GEARS dataset name, or 'custom' when using --adata.")
    ap.add_argument("--adata", default=None,
                    help="Path to a user AnnData (.h5ad) in GEARS format (with --dataset custom).")
    ap.add_argument("--data_name", default=None,
                    help="Name to register a custom dataset under (default: derived from --adata).")
    ap.add_argument("--data_dir", default="/workspace/data")
    ap.add_argument("--split", default="simulation",
                    choices=["simulation", "simulation_single", "combo_seen0",
                             "combo_seen1", "combo_seen2", "single", "no_test", "custom"])
    ap.add_argument("--split_seed", type=int, default=42)
    ap.add_argument("--train_gene_set_size", type=float, default=0.75)
    ap.add_argument("--batch_size", type=int, default=32)
    ap.add_argument("--test_batch_size", type=int, default=32)
    ap.add_argument("--allow_unlisted", action="store_true",
                    help="Bypass the open-data guard (use only if YOU verified the license).")
    args = ap.parse_args()

    # ---- open-data / licensing guard ----
    if args.dataset != "custom" and args.dataset not in OPEN_DATASETS and not args.allow_unlisted:
        sys.stderr.write(
            f"[license] '{args.dataset}' is not in the verified open-dataset list.\n"
            f"[license] Verify it is open / commercial-use safe, then re-run with "
            f"--allow_unlisted, or pick one of: {', '.join(OPEN_DATASETS)}.\n")
        sys.exit(2)
    if args.dataset == "custom":
        print("[license] Custom dataset: YOU are responsible for confirming it is open / "
              "commercial-use safe before using this in a commercial context.", flush=True)
    else:
        print(f"[license] {args.dataset}: {OPEN_DATASETS[args.dataset]}", flush=True)

    from gears import PertData

    pert_data = PertData(args.data_dir)

    if args.dataset == "custom":
        import scanpy as sc
        if not args.adata:
            sys.exit("[error] --dataset custom requires --adata /path/to.h5ad")
        name = args.data_name or Path(args.adata).stem
        adata = sc.read_h5ad(args.adata)
        # GEARS ingests a raw AnnData with obs['condition'] and a covariate column.
        pert_data.new_data_process(dataset_name=name, adata=adata)
        pert_data.load(data_path=str(Path(args.data_dir) / name))
    else:
        pert_data.load(data_name=args.dataset)

    pert_data.prepare_split(split=args.split, seed=args.split_seed,
                            train_gene_set_size=args.train_gene_set_size)
    pert_data.get_dataloader(batch_size=args.batch_size, test_batch_size=args.test_batch_size)

    adata = pert_data.adata
    n_cells, n_genes = adata.shape
    conditions = adata.obs["condition"].nunique()

    # split sizes at condition level
    s2c = {k: list(v) for k, v in pert_data.set2conditions.items()}
    sizes = {k: len(v) for k, v in s2c.items()}

    # test regime composition
    subgroup = getattr(pert_data, "subgroup", {}) or {}
    test_sub = {k: len(v) for k, v in subgroup.get("test_subgroup", {}).items()}

    print("\n=== dataset ===", flush=True)
    print(f"  dataset      : {args.dataset}", flush=True)
    print(f"  cells        : {n_cells}", flush=True)
    print(f"  genes        : {n_genes}", flush=True)
    print(f"  conditions   : {conditions}", flush=True)
    print("=== split (seed {}) ===".format(args.split_seed), flush=True)
    for k in ("train", "val", "test"):
        if k in sizes:
            print(f"  {k:5s} conditions: {sizes[k]}", flush=True)
    print("=== test regime composition ===", flush=True)
    for k, v in sorted(test_sub.items()):
        print(f"  {k:16s}: {v}", flush=True)

    # persist a small provenance record next to the data
    prov = {
        "dataset": args.dataset, "split": args.split, "split_seed": args.split_seed,
        "train_gene_set_size": args.train_gene_set_size,
        "n_cells": int(n_cells), "n_genes": int(n_genes), "n_conditions": int(conditions),
        "split_sizes": sizes, "test_subgroup_counts": test_sub,
    }
    out = Path(args.data_dir) / f"split_provenance_{args.dataset}_seed{args.split_seed}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(prov, open(out, "w"), indent=2)
    print(f"\n[save] provenance -> {out}", flush=True)
    print("[done]", flush=True)


if __name__ == "__main__":
    main()
