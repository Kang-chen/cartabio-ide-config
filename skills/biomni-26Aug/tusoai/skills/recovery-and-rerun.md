# Recovery, cold-start resume, and rerun decisions

Use this file whenever a sandbox restarts, a machine disappears or is paused, an
epoch ends, a workspace is reported wiped, or an earlier TusoAI run should
continue. Recovery is conservative and non-destructive: preserve state, prove
identity, then resume the same search whenever scores remain comparable.

## 1. Triage before creating or deleting anything

Record in `recovery/<timestamp>/triage.json`:

- current time, host/machine ID, working directory, Python/environment version;
- `SKILL_ROOT` version/hash;
- persistent task directory and results mirror listings;
- managed job and machine states;
- `run_state.json`, `events.jsonl`, cluster manifest, task bundle manifest;
- all history directories and their sizes/last modification times;
- node status/log files and latest checkpoint;
- source/evaluator/task/data hashes.

Forbidden during triage: `rm -rf` on broad paths, `git reset --hard`, `git clean`,
workspace reinitializers, overwriting the only history, or deleting lock files
without first proving they are stale.

Move conflicting artifacts to a timestamped recovery directory instead of
deleting them.

## 2. Determine whether this is the same comparable run

Resume the same history only when these match the recorded run:

- objective/metric/split/evaluator hash;
- source and editable target identities;
- task bundle or compatible Task definitions;
- immutable constraints and hidden-data policy;
- prepared data checksums;
- model/provider semantics sufficient for history use.

Changing machine count, `n_jobs`, cost/time limit, optimization model, timeout, or
adding compatible capacity usually permits `load_history`.

A material evaluator/metric/split change makes old scores non-comparable. Preserve
the old history as diagnostic evidence and start a new history identity. A small
task-set addition can reuse history only if all previous target names remain
compatible and the evaluator scale is unchanged.

## 3. Validate surviving history

1. Parse the shared history with `checkpoint_history.py`.
2. Confirm there is at least one valid candidate with code and finite accuracy.
3. Verify task/evaluator/source hashes from the run wiring/dev logs where
   available.
4. Rerun the protected baseline and one selected candidate from clean source.
5. Compare scores to prior logs within measured noise.
6. Inspect lock artifacts. A lock directory older than the configured stale
   threshold may be recovered by the v2 lock code; preserve evidence first.

If shared history is corrupt, try in order:

- the newest shared checkpoint copy;
- `/mnt/results/tusoai/<task_id>/history.json`;
- an earlier checkpoint under `checkpoints/`;
- per-node logs/code snapshots to reconstruct a seed.

Never overwrite the corrupt original before copying it into recovery evidence.

## 4. Restore environment and task bundle

Reuse the persistent environment when hashes and imports pass. If it is missing:

- rebuild under `/mnt/shared-workspace/tusoai/<task_id>/environment/`;
- pin the same dependency versions where possible;
- verify the bundled TusoAI source is first on `PYTHONPATH`;
- run the bundled test suite or at least import/copy/import-guard smoke tests;
- verify task bundle SHA-256 and unpickle it in a fresh process;
- verify evaluator/source/data paths on the replacement machine.

Do not independently reconstruct MethodTasks on each node when a valid task bundle
survives.

## 5. Recover a stopped, hibernated, or lost machine

An active node should never be intentionally hibernated by this skill. If the
platform did so anyway:

1. checkpoint shared state from a live node;
2. resume the same machine if its environment and job can safely continue;
3. otherwise mark the old node terminal/lost and provision a compatible
   replacement;
4. verify hashes and shared-FS probe visibility;
5. allocate a new node ID and remaining per-node epoch budget;
6. relaunch against the same `output_dir`, `history_name`, and `load_history`;
7. record the incident and continue without restarting seeding.

Do not hibernate healthy peers during replacement.

## 6. Recover from local `/workspace/` loss

Anything required for continuation should already be in shared storage. Recreate
only local scratch and random-access working copies. For each missing artifact:

- restore from shared/results/checkpoint source;
- verify checksum/schema before use;
- regenerate only when no valid durable copy exists;
- copy newly completed artifacts back immediately.

If a critical file existed only in `/workspace/`, document that persistence bug
in `events.jsonl` and change the layout before continuation.

## 7. Choose the rerun action

Use evidence from history, logs, and clean revalidation:

### Continue unchanged

Use when valid candidates are flowing, recent improvements exist, and failures are
mostly candidate-quality failures. Resume same task bundle/history with remaining
budget.

### Adjust resources

Use when OOM, oversubscription, low utilization, GPU starvation, or API rate limits
reduce throughput. Change node count, `n_jobs`, threads, memory, or timeout at an
epoch boundary; keep history.

### Increase/extend budget

Use only within user authorization or a new explicit budget. Preserve history and
set the persisted global total/absolute deadline correctly; do not reset spent
cost to zero.

### Change optimization model/provider

Use when code validity/reasoning is the bottleneck rather than evaluator setup.
Keep history if task/evaluator semantics are unchanged. Update model settings on
all nodes consistently.

### Refine/add MethodTasks or DataTasks

Use when current targets are valid but omit a high-leverage coherent path or
orthogonal data. Rebuild one new task bundle, validate compatibility, and retain
history only if old candidates map to the same target names/evaluator.

### Repair evaluation

Use when targets are unreachable, scores do not reproduce, subset overfitting or
leakage is found, or metric fidelity is wrong. Preserve old history, but start a
new comparable history after the score contract changes.

### Restart initialization

Use only when no valid history exists, target definitions were fundamentally
wrong, or all initial solutions/search context are unusable. Preserve old
artifacts and explain why history cannot be reused.

## 8. Resume protocol

Before launching:

- update `run_state.json` to `recovering`;
- record remaining global cost and absolute deadline from the original ledger;
- checkpoint and mirror current history;
- rerun evaluator audit/sentinel as appropriate;
- verify shared-FS semantics for new/replacement nodes;
- create a new epoch/node allocation without changing stable history identity;
- launch one leader only if no active leader exists; otherwise add followers;
- update state to `running` with job handles.

At the next heartbeat, verify new run IDs contribute candidates.

## 9. Recovery report

Report what survived, what was rebuilt, hashes used to establish identity,
history/best candidate chosen, validation results, machine/job replacements,
budget remaining, and exact continuation action. Be explicit about any gap or
non-comparable score.
