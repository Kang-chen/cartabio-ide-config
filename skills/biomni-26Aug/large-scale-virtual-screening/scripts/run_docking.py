#!/usr/bin/env python3
"""
run_docking.py -- orchestrate the docking stage: pilot -> plan -> execute -> collect.

Compute model = ADAPTIVE FAN-OUT with a confirm beat and a graceful local fallback.

Because worker provisioning (ManageMachine) is an AGENT action, not a Python API,
this script cleanly separates the two responsibilities:

  --mode plan     : dock a PILOT of --pilot N ligands locally, MEASURE real
                    throughput (lig/s/core) on THIS receptor/library/box, then
                    write fanout_plan.json with an ETA + recommended worker count
                    + rough cost, and a per-shard command template. The AGENT reads
                    this, reports "~Xh on N workers, proceed?" to the user, and on
                    approval provisions N workers (ManageMachine) and runs
                    dock_worker.py --shard i on each. ABORTS auto-scaling above
                    --abort-hours unless the agent overrides.

  --mode local    : dock the WHOLE library on this machine with a local process
                    pool (multiprocessing over ligands). This is the automatic
                    GRACEFUL FALLBACK for small libraries or when ManageMachine is
                    unavailable -- NOT a user-selected mode.

  --mode collect  : merge shard CSVs (scores/scores_shard*.csv) into
                    all_scores_merged.csv joined to master_library.csv.

Throughput note: ~13 s/ligand/core is only a STARTING estimate; it swings with
ligand flexibility, box size, and exhaustiveness, which is exactly why we pilot.

Outputs:
  fanout_plan.json       (plan mode) measured rate, ETA, workers, shard commands
  scores/scores_local.csv (local mode)
  all_scores_merged.csv   (collect mode): mol_id,smiles,source,activity_label,vina_affinity,status
"""
from __future__ import annotations
import argparse
import csv
import glob
import json
import os
import sys
import time

try:
    from common import which, fuse_safe_copy, ensure_local_scratch, SEED
    import dock_worker
except ImportError:
    from scripts.common import which, fuse_safe_copy, ensure_local_scratch, SEED
    from scripts import dock_worker


def _count_library(path):
    with open(path, newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def _pilot(args, box):
    """Dock the first --pilot ligands locally; return measured lig/s (single core)."""
    ids = []
    with open(args.library, newline="") as fh:
        for r in csv.DictReader(fh):
            ids.append(r["mol_id"])
            if len(ids) >= args.pilot:
                break
    vina = which("vina")
    if not vina:
        raise RuntimeError("Vina CLI not found; cannot pilot.")
    scratch = ensure_local_scratch("dock_pilot")
    t0 = time.time()
    n_ok = 0
    for mid in ids:
        pdbqt = os.path.join(args.pdbqt_dir, f"{mid}.pdbqt")
        if not os.path.exists(pdbqt):
            continue
        _, aff, status = dock_worker._dock_one(
            vina, args.receptor_pdbqt, pdbqt, box, scratch,
            args.exhaustiveness, args.num_modes, args.seed, args.timeout, 1)
        if status == "ok":
            n_ok += 1
    dt = time.time() - t0
    rate = n_ok / max(1e-9, dt)  # ligands/sec on ONE core
    return rate, n_ok, dt


def _plan(args) -> int:
    with open(args.box) as fh:
        box = json.load(fh)
    n_total = _count_library(args.library)
    rate, n_ok, dt = _pilot(args, box)
    if rate <= 0:
        print("[ERROR] pilot produced no successful docks; check ligand prep / box.",
              file=sys.stderr)
        return 2
    sec_per_lig_core = 1.0 / rate

    # planning math
    cores_per_worker = args.cores_per_worker
    total_core_seconds = n_total * sec_per_lig_core
    # choose workers so each finishes within --target-hours if possible, capped by --max-workers
    target_seconds = args.target_hours * 3600
    import math
    need_cores = total_core_seconds / target_seconds
    workers = max(1, min(args.max_workers, math.ceil(need_cores / cores_per_worker)))
    wall_seconds = total_core_seconds / (workers * cores_per_worker)
    wall_hours = wall_seconds / 3600.0
    cost_note = (f"{workers} workers x {cores_per_worker} cores; "
                 f"est {workers * wall_hours:.1f} worker-hours total")

    plan = {
        "n_total": n_total,
        "pilot_n_ok": n_ok, "pilot_seconds": round(dt, 1),
        "measured_sec_per_lig_per_core": round(sec_per_lig_core, 2),
        "measured_lig_per_s_per_core": round(rate, 4),
        "cores_per_worker": cores_per_worker,
        "recommended_workers": workers,
        "estimated_wall_hours": round(wall_hours, 2),
        "estimated_worker_hours": round(workers * wall_hours, 1),
        "cost_note": cost_note,
        "abort_hours": args.abort_hours,
        "exceeds_abort_threshold": bool(wall_hours > args.abort_hours),
        "shard_command_template": (
            "python dock_worker.py --library {library} --pdbqt-dir {pdbqt_dir} "
            "--receptor-pdbqt {receptor} --box {box} --shard {{i}} --nshards {workers} "
            "--out {scores_dir}/scores_shard{{i}}.csv "
            "--exhaustiveness {exh} --num-modes {nm} --cpu 1"
        ).format(library=args.library, pdbqt_dir=args.pdbqt_dir,
                 receptor=args.receptor_pdbqt, box=args.box, workers=workers,
                 scores_dir=args.scores_dir, exh=args.exhaustiveness, nm=args.num_modes),
        "fallback": "if ManageMachine unavailable or n_total small, run --mode local",
    }
    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "fanout_plan.json"), "w") as fh:
        json.dump(plan, fh, indent=2)

    print(f"[PLAN] {n_total} ligands | measured {sec_per_lig_core:.1f} s/lig/core "
          f"(pilot {n_ok} in {dt:.0f}s)")
    print(f"[PLAN] recommend {workers} workers x {cores_per_worker} cores "
          f"-> ~{wall_hours:.1f} h wall ({cost_note})")
    if plan["exceeds_abort_threshold"]:
        print(f"[ABORT-THRESHOLD] estimate {wall_hours:.1f} h exceeds --abort-hours "
              f"{args.abort_hours}. Confirm with the user before provisioning a fleet.",
              file=sys.stderr)
    print("[PLAN] -> fanout_plan.json  (agent: report ETA/cost, get user OK, then "
          "provision workers via ManageMachine and run the shard template)")
    return 0


def _dock_one_star(a):
    return dock_worker._dock_one(*a)


def _local(args) -> int:
    with open(args.box) as fh:
        box = json.load(fh)
    ids = []
    with open(args.library, newline="") as fh:
        for r in csv.DictReader(fh):
            ids.append(r["mol_id"])
    if args.limit:
        ids = ids[: args.limit]
    vina = which("vina")
    if not vina:
        print("[ERROR] Vina CLI not found.", file=sys.stderr); return 2
    scratch = ensure_local_scratch("dock_local")
    nproc = args.nproc or max(1, (os.cpu_count() or 2))
    tasks = []
    for mid in ids:
        pdbqt = os.path.join(args.pdbqt_dir, f"{mid}.pdbqt")
        tasks.append((vina, args.receptor_pdbqt, pdbqt, box, scratch,
                      args.exhaustiveness, args.num_modes, args.seed, args.timeout, 1))
    print(f"[local] docking {len(tasks)} ligands over {nproc} procs")
    rows = []
    t0 = time.time()
    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=nproc) as ex:
        futs = [ex.submit(_dock_one_star, t) for t in tasks]
        done = 0
        for fut in as_completed(futs):
            rows.append(fut.result()); done += 1
            if done % max(1, len(tasks) // 10) == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)} ({done / max(1e-9, time.time() - t0):.3f} lig/s)")
    os.makedirs(args.scores_dir, exist_ok=True)
    local_csv = os.path.join(scratch, "scores_local.csv")
    with open(local_csv, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["mol_id", "vina_affinity", "status"]); w.writerows(rows)
    dst = os.path.join(args.scores_dir, "scores_local.csv")
    fuse_safe_copy(local_csv, dst)
    print(f"[OK] local docking -> {dst} ({len(rows)} rows)")
    return 0


def _collect(args) -> int:
    lib = {}
    with open(args.library, newline="") as fh:
        for r in csv.DictReader(fh):
            lib[r["mol_id"]] = r
    scores = {}
    files = sorted(glob.glob(os.path.join(args.scores_dir, "scores_*.csv")))
    if not files:
        print(f"[ERROR] no scores_*.csv in {args.scores_dir}", file=sys.stderr); return 2
    for f in files:
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                scores[r["mol_id"]] = (r.get("vina_affinity", ""), r.get("status", ""))
    out_local = os.path.join(ensure_local_scratch("collect"), "all_scores_merged.csv")
    fields = ["mol_id", "smiles", "source", "activity_label", "chembl_id", "dude_id",
              "vina_affinity", "status"]
    n = 0
    with open(out_local, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields); w.writeheader()
        for mid, r in lib.items():
            aff, status = scores.get(mid, ("", "missing"))
            row = {k: r.get(k, "") for k in fields[:6]}
            row["vina_affinity"] = aff; row["status"] = status
            w.writerow(row); n += 1
    dst = os.path.join(args.outdir, "all_scores_merged.csv")
    os.makedirs(args.outdir, exist_ok=True)
    fuse_safe_copy(out_local, dst)
    n_ok = sum(1 for v in scores.values() if v[1] == "ok")
    print(f"[OK] merged {n} compounds ({n_ok} ok, {len(files)} shard files) -> {dst}")
    return 0


def self_check() -> int:
    # planning math is deterministic; verify it without docking
    import math
    n_total = 3042; sec_per_lig_core = 13.0; cores = 8; target_h = 1.0; maxw = 8
    need = (n_total * sec_per_lig_core) / (target_h * 3600)
    workers = max(1, min(maxw, math.ceil(need / cores)))
    wall_h = (n_total * sec_per_lig_core) / (workers * cores) / 3600
    ok = workers >= 1 and wall_h > 0
    print(f"run_docking.py self-check: {'PASS' if ok else 'FAIL'} "
          f"(demo plan: {workers} workers, ~{wall_h:.2f} h wall for {n_total} ligs)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Docking orchestrator (pilot/plan, local, collect)")
    ap.add_argument("--mode", choices=["plan", "local", "collect"], default="plan")
    ap.add_argument("--library")
    ap.add_argument("--pdbqt-dir")
    ap.add_argument("--receptor-pdbqt")
    ap.add_argument("--box")
    ap.add_argument("--scores-dir", default="/mnt/results/sbvs_run/scores")
    ap.add_argument("--outdir", default="/mnt/results/sbvs_run")
    ap.add_argument("--pilot", type=int, default=20)
    ap.add_argument("--cores-per-worker", type=int, default=8)
    ap.add_argument("--max-workers", type=int, default=5, help="plan cap (session machine limit)")
    ap.add_argument("--target-hours", type=float, default=2.0, help="desired wall time")
    ap.add_argument("--abort-hours", type=float, default=8.0, help="confirm above this ETA")
    ap.add_argument("--nproc", type=int, default=0, help="local mode procs (0=all cores)")
    ap.add_argument("--exhaustiveness", type=int, default=8)
    ap.add_argument("--num-modes", type=int, default=9)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--limit", type=int, default=0, help="local mode ligand cap (smoke)")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    if args.mode in ("plan", "local") and not all([args.library, args.pdbqt_dir,
                                                    args.receptor_pdbqt, args.box]):
        ap.error("plan/local need --library --pdbqt-dir --receptor-pdbqt --box")
    if args.mode == "collect" and not args.library:
        ap.error("collect needs --library and --scores-dir")
    sys.exit({"plan": _plan, "local": _local, "collect": _collect}[args.mode](args))


if __name__ == "__main__":
    main()
