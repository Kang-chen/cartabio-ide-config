#!/usr/bin/env python3
"""
dock_worker.py -- dock one shard of a prepared library with AutoDock Vina.

Self-contained so it can run on a fan-out worker machine that only has the
skill scripts + a shared filesystem. Docks ligand PDBQTs whose mol_id satisfies
  (index % --nshards == --shard)
using the same box, and writes scores to a LOCAL csv first, then FUSE-safe-copies
to the destination (S3-backed /mnt paths reject streaming/append writes).

Per-ligand Vina call: --exhaustiveness 8 --num_modes 9 --seed 42 --cpu 1
(one CPU per ligand; parallelism comes from many ligands / many workers).

Output shard CSV schema (STABLE):  mol_id,vina_affinity,status
  status in {ok, timeout, error, missing_pdbqt}

Usage (single shard):
  python dock_worker.py --library master_library.csv --pdbqt-dir ligands/pdbqt \
     --receptor-pdbqt rec.pdbqt --box docking_box.json \
     --shard 0 --nshards 4 --out /mnt/results/run/scores/scores_shard0.csv
"""
from __future__ import annotations
import argparse
import csv
import os
import subprocess
import sys
import time

try:
    from common import which, parse_vina_stdout, parse_vina_posefile, fuse_safe_copy, ensure_local_scratch, SEED
except ImportError:
    from scripts.common import (which, parse_vina_stdout, parse_vina_posefile,
                                fuse_safe_copy, ensure_local_scratch, SEED)


def _dock_one(vina, receptor, pdbqt, box, scratch, exhaustiveness, num_modes, seed, timeout, cpu):
    mol_id = os.path.splitext(os.path.basename(pdbqt))[0]
    out_pose = os.path.join(scratch, f"{mol_id}_out.pdbqt")
    cmd = [vina, "--receptor", receptor, "--ligand", pdbqt,
           "--center_x", str(box["center_x"]), "--center_y", str(box["center_y"]),
           "--center_z", str(box["center_z"]),
           "--size_x", str(box["size_x"]), "--size_y", str(box["size_y"]),
           "--size_z", str(box["size_z"]),
           "--exhaustiveness", str(exhaustiveness), "--num_modes", str(num_modes),
           "--seed", str(seed), "--cpu", str(cpu), "--out", out_pose]
    env = dict(os.environ)
    env["PATH"] = "/opt/conda/bin:/usr/local/bin:" + env.get("PATH", "")
    try:
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return mol_id, "", "timeout"
    aff = parse_vina_stdout(res.stdout)
    if aff is None:
        aff = parse_vina_posefile(out_pose)
    try:
        if os.path.exists(out_pose):
            os.remove(out_pose)
    except OSError:
        pass
    if aff is None:
        return mol_id, "", "error"
    return mol_id, f"{aff:.3f}", "ok"


def run(args) -> int:
    vina = which("vina")
    if not vina:
        print("[ERROR] Vina CLI not found (PATH incl. /opt/conda/bin, /usr/local/bin)", file=sys.stderr)
        return 2
    import json
    with open(args.box) as fh:
        box = json.load(fh)

    ids = []
    with open(args.library, newline="") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            if i % args.nshards == args.shard:
                ids.append(r["mol_id"])

    scratch = ensure_local_scratch(f"dock_shard{args.shard}")
    local_csv = os.path.join(scratch, os.path.basename(args.out))
    n = len(ids)
    print(f"[info] shard {args.shard}/{args.nshards}: {n} ligands")

    t0 = time.time()
    done = 0
    with open(local_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mol_id", "vina_affinity", "status"])
        for mid in ids:
            pdbqt = os.path.join(args.pdbqt_dir, f"{mid}.pdbqt")
            if not os.path.exists(pdbqt):
                w.writerow([mid, "", "missing_pdbqt"]); done += 1; continue
            mol_id, aff, status = _dock_one(
                vina, args.receptor_pdbqt, pdbqt, box, scratch,
                args.exhaustiveness, args.num_modes, args.seed, args.timeout, args.cpu)
            w.writerow([mol_id, aff, status])
            done += 1
            if done % max(1, n // 20) == 0 or done == n:
                rate = done / max(1e-9, time.time() - t0)
                print(f"  shard{args.shard}: {done}/{n} ({rate:.3f} lig/s)")
                fh.flush()

    # FUSE-safe hand-off to destination (single sequential copy)
    fuse_safe_copy(local_csv, args.out)
    dt = time.time() - t0
    print(f"[OK] shard {args.shard} done: {n} ligands in {dt:.0f}s "
          f"({n / max(1e-9, dt):.3f} lig/s) -> {args.out}")
    return 0


def self_check() -> int:
    ok = which("vina") is not None
    print(f"dock_worker.py self-check: {'PASS' if ok else 'WARN'} "
          f"(vina {'found' if ok else 'NOT on PATH -- required at run time'})")
    return 0  # don't fail the suite just because vina isn't on this node


def main():
    ap = argparse.ArgumentParser(description="Dock one shard with Vina (FUSE-safe output)")
    ap.add_argument("--library")
    ap.add_argument("--pdbqt-dir")
    ap.add_argument("--receptor-pdbqt")
    ap.add_argument("--box")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--out", default="/mnt/results/sbvs_run/scores/scores_shard0.csv")
    ap.add_argument("--exhaustiveness", type=int, default=8)
    ap.add_argument("--num-modes", type=int, default=9)
    ap.add_argument("--cpu", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=600, help="per-ligand seconds")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    for req in ("library", "pdbqt_dir", "receptor_pdbqt", "box"):
        if not getattr(args, req):
            ap.error(f"--{req.replace('_', '-')} is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
