# Compute & scaling: pilot → plan → confirm → fan-out

Docking is the bottleneck of any virtual screen. This skill uses **adaptive fan-out**: it
measures real throughput on a small pilot, estimates cost/time, confirms with the user before
committing a fleet, and falls back to a single machine gracefully.

## Why pilot instead of assuming a rate

Per-ligand Vina time is **not constant**. It swings with ligand flexibility (rotatable
bonds), box size, and exhaustiveness. A useful starting anchor is **~13 s/ligand/core** at
exhaustiveness 8 (measured on DUD-E EGFR), but treat that only as a prior — the pilot
measures the truth for *this* receptor/library/box.

## The procedure

1. **Pilot + plan.**
   ```bash
   python run_docking.py --mode plan --library master_library.csv --pdbqt-dir pdbqt \
       --receptor-pdbqt rec.pdbqt --box docking_box.json --outdir $RUN \
       --pilot 20 --cores-per-worker 8 --target-hours 2 --max-workers 5 --abort-hours 8
   ```
   This docks 20 ligands locally, measures lig/s/core, and writes `fanout_plan.json`:
   measured rate, recommended worker count, estimated wall hours, estimated worker-hours
   (a cost proxy), and `exceeds_abort_threshold`.

2. **Report and confirm (the "beat").** Surface a single clear message to the user, e.g.
   *"3042 ligands, measured ~13 s/lig/core → ~0.7 h on 2×8-core workers (~1.4 worker-hours).
   Proceed?"* Always confirm when `exceeds_abort_threshold` is true (a 50k-ligand run that
   quietly spins up a big fleet is exactly the side-effect a user should approve first).

3. **Execute.**
   - **Fan-out:** provision N workers with `ManageMachine` (e.g. `{cpu_cores: 8, memory_gb: 8}`
     each; the session cap is 5 machines). On each worker run the shard command from the plan:
     ```bash
     python dock_worker.py --library master_library.csv --pdbqt-dir pdbqt \
         --receptor-pdbqt rec.pdbqt --box docking_box.json \
         --shard i --nshards N --out $RUN/scores/scores_shard{i}.csv
     ```
     Shards are split by `index % nshards`. Each worker writes locally then FUSE-safe-copies
     its shard to `$RUN/scores/`.
   - **Local fallback (automatic):** small library or no `ManageMachine` access:
     ```bash
     python run_docking.py --mode local --library ... --pdbqt-dir ... --receptor-pdbqt ... \
         --box ... --scores-dir $RUN/scores --nproc 8
     ```

4. **Collect.**
   ```bash
   python run_docking.py --mode collect --library master_library.csv \
       --scores-dir $RUN/scores --outdir $RUN
   ```
   Merges all `scores_*.csv` shards, joins to the library, and writes `all_scores_merged.csv`.

## FUSE-safe I/O (critical)

`/mnt/results` and `/mnt/shared-workspace` are **S3-backed FUSE** mounts:
- **No** append / random-access / streaming writes; **no** `rm -rf` on directories; **no**
  recursive `find`.
- Pattern: do per-ligand and append work on **local `/workspace`**, then a single shell `cp`
  to `/mnt/...` at the end. The scripts already do this.
- R's `file.copy()` produces **0-byte files** on these mounts — always use a shell `cp`.
- For multi-machine handoff, use `/mnt/shared-workspace/shared/<file>`; for user-facing
  deliverables use `/mnt/results/`.

## Throughput sanity checks

- Verify running Vina procs with `ps -eo comm | grep -c vina` and count produced pose/score
  rows; **load average can misreport as 0.00** on these machines, so don't trust it.
- On the default `worker-0` (minimal footprint, autoscaling ~1 CPU) a big screen will crawl —
  provision real workers for anything beyond a few hundred ligands.

## Wall-clock limits

A sandbox hard-dies at ~24 h wall-clock, and background jobs do **not** extend that. For very
large screens, shard so each worker finishes well within the window and checkpoint shard CSVs
to `/mnt/shared-workspace/` as they complete; a lost worker then only loses its own shard.
