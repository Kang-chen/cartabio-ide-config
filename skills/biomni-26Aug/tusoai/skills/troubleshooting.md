# TusoAI v2 troubleshooting matrix

Use this file when a concrete symptom appears. Check infrastructure before
concluding the scientific search has plateaued.

## `shutil.copy`, chmod, utime, `Operation not permitted`, or FUSE metadata errors

The v2 bundle uses data-only `copyfile_portable` and portable tree copying. If this
error appears:

1. confirm `sys.path` imports `$SKILL_ROOT/repo/tusoai`, not an older installed
   package;
2. record `tusoai.__file__` and source hash;
3. run `python -m pytest -q tests/test_fs_utils.py` in the bundled repo;
4. locate remaining external code that calls `shutil.copy/copy2/copystat` on the
   shared mount;
5. use local `/workspace/` only for the incompatible random-access/metadata
   operation, then data-copy the completed artifact back;
6. do not move the authoritative history to ephemeral storage.

## Candidate code changes, but every score equals the original

Treat this as evaluator reachability failure, not a plateau.

- run the sentinel reachability test;
- inspect hard-coded `sys.path`, absolute imports, editable installations,
  subprocess working directories, compiled extensions, caches, and duplicated
  package names;
- verify `source_path`/`repo_root` and exact target name;
- inspect import-guard errors in dev logs;
- ensure the runner calls the optimized function on the metric path.

The v2 evaluator bootstrap explicitly loads the dynamic import guard. Do not
bypass it; fix the runner.

## Import guard reports the original repository

The runner or imported code resolved inside a protected original `repo_root`.
Remove absolute path insertion and import via the dynamic repository's normal
package path. Keep runner and method repository as siblings or place the runner
inside the repository. Re-run baseline and sentinel.

## Missing, duplicate, NaN, or malformed `tuso_evaluate`

The runner must emit one standalone finite line. Remove extra score prints,
format scientific notation normally, move diagnostics outside the line, and
ensure all successful branches reach it. Candidate exceptions must return a
failed evaluation, not a favorable fallback score.

## Multiple history directories in a supposed multi-machine run

Nodes used different/empty `history_name` or `output_dir`. Stop launching new
nodes, checkpoint every history, compare evaluator/task hashes, and choose the
valid shared history. Relaunch all compatible nodes with one stable non-empty
history identity. Do not merge score histories blindly when contracts differ.

## Followers duplicate seeding or spend before useful evolution

Followers were launched before a valid seed candidate existed or rebuilt tasks.
Use leader-first bootstrap, serialize task construction once, and start followers
only after history contains code plus numeric accuracy. Followers load the same
bundle and shared history.

## Only one `run_id` appears after followers started

Followers are not contributing. Check managed job state, node status/logs,
shared-FS visibility, bundle/evaluator hashes, credentials, bootstrap wait,
history identity, and lock errors. Do not assume CPU utilization alone proves
shared search.

## Shared history JSON corruption, lost entries, or lock timeout

- stop launching new nodes but do not hibernate/kill healthy current operations
  before checkpointing;
- copy the history and lock artifacts into recovery evidence;
- run the shared-FS probe on all nodes;
- confirm the v2 atomic writer/hybrid lock is imported;
- inspect mount semantics and concurrent visibility;
- recover from the newest valid checkpoint/results mirror;
- use a supported shared POSIX path or fall back to one machine if the probe
  fails.

Increasing lock stale/timeout variables is appropriate only when writes are
legitimately slow; it does not repair a non-coherent filesystem.

## OOM or machine death

`memory_limit_gb` is per evaluation and `n_jobs` multiplies memory. Use measured
peak RSS with headroom, reduce concurrent jobs, increase machine RAM, and keep
parent/cache reserve. Check GPU memory separately. Checkpoint shared history,
replace the node, and resume; do not restart.

## CPU usage exceeds planned capacity or throughput worsens with more jobs

Nested BLAS/OpenMP threads are oversubscribed or evaluator IO/API is saturated.
Set `cpu_threads_per_job`, inspect affinity, reduce `n_jobs`, batch/precompute IO,
and compare candidates/hour. More processes are not inherently more performance.

## GPUs exist but are idle

Confirm the evaluator actually executes CUDA/JAX/CuPy code and sees the local
device. Check `CUDA_VISIBLE_DEVICES`, driver/runtime versions, data transfer, CPU
preprocessing, and whether candidate code falls back to CPU. Do not provision
additional GPUs until one GPU improves evaluator throughput.

## CUDA OOM or cross-node GPU inconsistency

Start with one job per local GPU, lower batch/model memory, and measure. Use local
`gpu_ids`; never pass cluster-global IDs. Ensure every node uses comparable GPU
math/precision. Do not mix required-GPU and CPU-only candidates in one score
history unless portability is a hard contract.

## API 429s, timeouts, or escalating LLM latency

Total cluster concurrency exceeds provider capacity. Reduce active nodes or
`n_jobs`, add backoff where supported, and allocate budgets to productive nodes.
Do not interpret provider failures as bad candidate code. Preserve task caches to
avoid repeated construction calls.

## Candidate timeout rate is high

Separate legitimate slower-but-valid methods from hangs. Inspect baseline/runtime
distribution, increase timeout only with budget/memory justification, add hints
about complexity, precompute invariant data, or reduce evaluator workload only if
scientifically equivalent. Do not weaken validation to make candidates pass.

## TusoAI stops immediately on resume

Check process-local `TIME_LIMIT`/`COST_LIMIT`, absolute cluster deadline, remaining
node budget, and whether a wrapper calculated less than one minute after reserve.
Also verify valid history records can be restored. Extend only within the global
ledger; do not erase spent cost.

## History exists but no valid records can be restored

It may contain only run wiring/dev-like entries, target names may differ, code may
be missing, or compact history may be corrupt. Inspect entry stages and task-name
compatibility. Start followers only after a valid code/accuracy record. Recover
from checkpoint if necessary.

## Extra skill instructions appear ignored

Open `task_spec.json` and the actual serialized Task objects. For each user
constraint, identify its route into `task_description`, `data_available`, target
`hints`, `global_hints`, DataTask fields, source paths, or evaluator assertions.
Any instruction present only in `SKILL.md`, chat, or `RUNBOOK.md` is not guaranteed
to reach TusoAI. Fix the coverage matrix and rebuild one task bundle.

## Good validation score but implausible/brittle code

Inspect for leakage, split-specific constants, hidden paths, randomness, writes,
network, exception fallbacks, and evaluator exploitation. Reapply to clean source,
run multiple seeds/folds/full data, and compare complexity-aware selected code.
Reject non-reproducible or invalid improvements regardless of history rank.

## Machine unexpectedly hibernated or stopped

This skill must not hibernate active nodes. Determine whether the action came from
platform lifecycle, another agent, or an erroneous instruction. Checkpoint from a
live node, resume or replace the machine, verify hashes, relaunch with the same
history, and record the incident. Remove any active-run hibernation instruction;
hibernation is post-run cleanup only.
