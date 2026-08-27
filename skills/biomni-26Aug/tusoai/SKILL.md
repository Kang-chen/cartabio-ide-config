---
id: "skill_4824c98eebb04d268bd7434b34682102"
name: "tusoai"
description: "Use TusoAI external APIs for fast, many-iteration computational-method development when the required API keys are available and changes can stay focused. Covers evaluator fidelity, durable checkpoints, resumable multi-machine search, history review, and recovery."
category: "data_analysis"
visibility: "public"
starting-prompt: "Set up the RNA integration benchmark from this paper: https://www.nature.com/articles/s41592-021-01336-8#Sec2 Using the tusoai skill, make a method for this task. Use these user-provided API keys when running TusoAI: ChatGPT/OpenAI API key: [INSERT OPENAI API KEY]; Semantic Scholar API key: [INSERT SEMANTIC SCHOLAR API KEY]. Run with a $10 cost limit and a 72-hour time limit. After the method is built, apply it to their full testing set and measure performance exactly as they do, comparing performance against the precomputed baselines they provide."
---

# TusoAI v2 — autonomous, persistent, multi-machine method development

This skill runs the bundled TusoAI system inside Biomni as a durable scientific
optimization service. Its job is not merely to call `optimize()`: it must build a
faithful evaluator, translate every user constraint into TusoAI-visible context,
calibrate resources, launch a cooperative multi-machine search, survive sandbox
lifecycle events, monitor search health, resume automatically, and return a
revalidated method rather than an unexamined high score.

The bundled repository is the user-supplied TusoAI revision plus Biomni-specific
filesystem, evaluator-import, shared-history, and test hardening. Treat
`repo/README.md` as the API reference and this file as the orchestration contract.

## 0. Resolve the installed skill root once

At activation, resolve `SKILL_ROOT` and record it in the run state. Prefer, in
order:

1. `/mnt/skills/system/tusoai`
2. `/mnt/results/skills/tusoai`
3. `/mnt/results/skills/tusoai_v2`
4. the unique directory under `/mnt/results/skills/` whose `SKILL.md` starts with
   `# TusoAI v2` and which contains `repo/tusoai/optimization.py`.

Never assume the archive's local folder name. Verify these paths before acting:

- `$SKILL_ROOT/repo/tusoai/optimization.py`
- `$SKILL_ROOT/scripts/audit_setup.py`
- `$SKILL_ROOT/templates/task_spec.json`

Add `$SKILL_ROOT/repo` to `PYTHONPATH` or `sys.path` when importing TusoAI. Never
install or import an older system copy ahead of the bundled repository. Record
`$SKILL_ROOT/VERSION` and the SHA-256 of `repo/tusoai/optimization.py` in
`run_state.json`.

## Mission and completion standard

Build the highest-quality method TusoAI can find within the authorized global
cost and wall-clock budget while preserving the scientific contract. A run is
complete only after:

- evaluator fidelity and candidate reachability have been verified;
- every user constraint has an explicit enforcement route;
- all compatible provisioned CPUs and GPUs have been used without unsafe
  oversubscription;
- the active global budget is exhausted, the user interrupts, or a genuine
  unrecoverable blocker is documented;
- the best candidates are rerun from clean source, checked for leakage and
  robustness, and exported with the history, task spec, state, and exact launch
  configuration.

A plateau, one good result, one failed machine, a callback, or the end of an epoch
is not completion. If budget remains, diagnose and continue in the same task.

## Non-negotiable invariants

### A. Never hibernate an active cluster

Once a leader, follower, evaluator, checkpoint process, or GPU node participates
in an active TusoAI epoch, do **not** call hibernate, suspend, stop, release,
scale-down, or delete on that machine. Apparent idleness between candidate jobs
is part of the search and is not permission to hibernate it.

A machine may be released or hibernated only after all of the following are true:

1. its managed TusoAI job is terminal or has been deliberately stopped at a
   checkpoint boundary;
2. shared `history.json`, node status, stdout/stderr, selected code, and
   `run_state.json` have been durably checkpointed;
3. no other active node depends on local-only state from that machine; and
4. the run is globally complete, or the machine is being replaced immediately
   by a compatible node that resumes the same shared history.

Never use GPU hibernation as a way to survive a timeout. Use bounded epochs,
managed jobs, checkpoints, and resume. If the platform itself pauses a machine,
resume or replace it and continue from shared history; do not restart the search.

### B. Use managed background execution only

Long TusoAI nodes must be launched with Biomni's tracked background mechanism,
with a distinct `background_name` and `machine_id`. Forbidden: shell `&`,
`nohup`, `screen`, `tmux`, `subprocess.Popen` used as an unmanaged daemon, or any
process invisible to Biomni's job lifecycle.

Use callback-driven completion. While jobs are active, make a real tool call at
least every 8–10 minutes to checkpoint history, update state, or report health.
Log lines alone are not a watchdog heartbeat. Do not leave a multi-hour job
running while the agent is silent.

### C. Persistent state is authoritative

Treat process memory and `/workspace/` as disposable. The authoritative task
workspace is:

```
/mnt/shared-workspace/tusoai/<task_id>/
```

Use `/mnt/results/tusoai/<task_id>/` for user-visible mirrors and final outputs.
Use `/workspace/` only for formats that require local random-access writes; copy
completed files back immediately.

Never broadly clean or delete a workspace. On conflicts, preserve artifacts under
`recovery/<timestamp>/` and reconstruct from the newest valid state.

### D. Multi-machine means one shared search, not unrelated runs

For more than one optimizer process, every node must use all of the following:

- `multi_machine=True`;
- the same absolute shared `output_dir`;
- the same **non-empty** `history_name`;
- the same task bundle hash, evaluator hash, source/repository fingerprint,
  dependency lock, model settings, and immutable constraints;
- a unique node ID and unique stdout/stderr path, but **not** a unique history
  name;
- a shared filesystem that has passed the cross-machine probe in
  `scripts/shared_fs_probe.py`.

`multi_machine=True` without an identical non-empty `history_name` creates
separate histories and is a configuration failure.

### E. Global budgets must be enforced outside each process

TusoAI's `TIME_LIMIT` and `COST_LIMIT` are process-local. Maintain one persisted
global budget in `run_state.json`. Divide each epoch's remaining cost across
active nodes with safety headroom; never give every node the full global limit.
Use an absolute cluster deadline and derive each node's `TIME_LIMIT` from the
remaining time minus a shutdown/checkpoint reserve.

Default execution policy when the user does not specify a budget:

- 4-hour resumable epochs;
- a 45-minute reserve before the platform's hard wall-clock boundary;
- continue epochs until the authorized task budget is exhausted;
- do not schedule a single 24-hour call exactly at a 24-hour sandbox cap.

### F. The evaluator and hidden data are protected

Do not modify scoring code, splits, labels, held-out data, benchmark definitions,
or test expectations to improve the score. Candidate functions may not read
hidden labels, evaluator internals, previous expected outputs, or network data.
Do not weaken validation, sample counts, seeds, precision, or convergence criteria
unless the user explicitly changes the scientific contract.

## Canonical package map

Read a referenced file in full before performing that operation.

| Trigger | Canonical file |
|---|---|
| Evaluator setup, baseline/noise, candidate reachability | `skills/evaluator-and-reachability.md` |
| Translate instructions into MethodTasks/DataTasks and a serialized bundle | `skills/task-construction.md` |
| New external/orthogonal data or benchmark data staging | `skills/data-tasks.md` |
| Resource sizing, shared-FS proof, leader/follower launch | `skills/multi-machine.md` |
| Active run health, heartbeats, cost, checkpoints, epoch continuation | `skills/monitoring-and-checkpointing.md` |
| Select, inspect, and revalidate history candidates | `skills/history-selection.md` |
| Cold-start resume, machine loss, wiped workspace, rerun decisions | `skills/recovery-and-rerun.md` |
| A concrete error or pathological run symptom | `skills/troubleshooting.md` |
| Public API and source behavior | `repo/README.md` |
| Biomni-specific source patches | `repo/PATCH_NOTES_BIOMNI_V2.md` |

There is one canonical copy of each sub-skill. Do not use stale root-level
duplicates from earlier TusoAI skill archives.

## Persistent layout and required artifacts

Create the following without deleting prior runs:

```
/mnt/shared-workspace/tusoai/<task_id>/
├── run_state.json                 # authoritative state machine
├── events.jsonl                   # append-only decisions and failures
├── RUNBOOK.md                     # concise human-readable context
├── task_spec.json                 # immutable scientific/context contract
├── task_factory.py                # constructs AI client and tasks
├── task_bundle.pkl                # tasks built once, shared by all nodes
├── task_bundle_manifest.json      # hash + target names + evaluator
├── cluster_manifest.json          # nodes, resources, budgets, deadline
├── environment/                   # persistent venv/conda environment
├── source/                        # protected baseline source/repository
├── runner.py                      # verified evaluator
├── data/                          # staged immutable/processed data
├── cache/                         # TusoAI construction/literature caches
├── run/                           # shared TusoAI output_dir
├── logs/                          # one log per node and epoch
├── status/                        # one status JSON per node
├── checkpoints/                   # periodic history/code/state snapshots
└── recovery/                      # preserved conflicting/corrupt artifacts

/mnt/results/tusoai/<task_id>/
├── status.json
├── history.json
├── best_score.py
├── selected.py
├── final_method/
├── final_validation.json
├── report.md
├── plots/
└── logs/                          # concise or final log mirrors
```

After every material phase transition and every checkpoint, atomically update
`run_state.json` and append one event to `events.jsonl`. Use
`templates/run_state.json` as the schema. `scripts/run_state.py` provides atomic
`init`, `patch`, `event`, and `show` operations. Record hashes, not just paths.

## The context packet: make instructions actually reach TusoAI

Every task must have a validated `task_spec.json`, based on
`templates/task_spec.json`. It is the single source of truth for:

- objective and score direction;
- exact evaluator and metric semantics;
- editable targets and source locations;
- available inputs/data/dependencies;
- immutable scientific, API, shape, ordering, seed, privacy, and leakage
  constraints;
- global budget and hardware policy;
- an explicit coverage row for every immutable constraint.

No important instruction may live only in this skill, a chat message, or
`RUNBOOK.md`. Before task construction, map each instruction to at least one
actual enforcement route:

- objective and scientific context → `task_description`;
- per-target inputs → `data_available` or DataTask `file_description` /
  `data_usage`;
- target-specific constraints → each task's `hints`;
- cross-target constraints → `global_hints`;
- edit location → `source_path` and `repo_root`;
- correctness, leakage, schema, and score direction → evaluator assertions;
- external data access → staged absolute paths and DataTask `read_cmd`;
- dependencies/resource limits → installed environment plus hints and launch
  configuration.

Run:

```
python "$SKILL_ROOT/scripts/validate_task_spec.py" \
  /mnt/shared-workspace/tusoai/<task_id>/task_spec.json
```

Do not construct tasks until it passes. After construction, persist the exact
MethodTask/DataTask objects once in `task_bundle.pkl`; every machine loads that
same hash. Reconstructing tasks independently on followers wastes model calls and
can give nodes divergent search spaces.

## Phase state machine

### Phase 0 — Discover, fingerprint, and resume before creating anything

1. Derive a stable, filesystem-safe `<task_id>`.
2. Resolve `SKILL_ROOT` and bundled source version.
3. Inspect the persistent task directory, results mirror, managed job registry,
   machine registry, and existing histories.
4. If `run_state.json` describes the same repository/evaluator/task-spec hashes,
   follow `skills/recovery-and-rerun.md` and resume at the recorded phase. Never
   rebuild a valid task bundle or restart seeding merely because process state was
   lost.
5. On a clean start, copy templates, initialize state/events, record the original
   user request verbatim in `RUNBOOK.md`, fingerprint source and evaluator, and
   protect a baseline source snapshot.

Phase exit gate: state is durable; clean-start versus resume is unambiguous;
source, evaluator, and task identity are recorded.

### Phase 1 — Build and prove the evaluator

Read `skills/evaluator-and-reachability.md` in full. Build the smallest faithful
runner that exercises the real method and exact metric. Run the evaluator audit
for at least three baseline repetitions, estimate score noise/runtime/RSS, and
verify exactly one finite `tuso_evaluate` line.

Perform a **candidate reachability test**: in a temporary copied source tree,
apply a deliberate, reversible sentinel change to the intended target and prove
the evaluator result or an explicit assertion changes. If it does not, stop and
fix imports/call sites. A runner that silently imports the original repository
cannot guide optimization.

Use relative runner-to-repository imports. Never insert the original repository's
absolute path into `sys.path`. Make the verified evaluator and protected data
read-only where practical.

Phase exit gate: baseline is reproducible; noise-informed `min_improvement` is
chosen; candidate edits reach the score; timeout and memory envelope are known.

### Choosing Biomni's initial method (when Biomni builds it)

This applies only when **Biomni itself constructs the initial method** that
TusoAI will then optimize. It is distinct from two other things and must not be
confused with either:

- it is **not** TusoAI's own seed — i.e., not the starting method repository /
  baseline source you begin from; and
- it is **not** TusoAI's internal initialization/seeding of baseline
  descriptions produced during its search.

When Biomni is authoring that first method from scratch, it is **recommended to
start from a simpler form of a family of solutions already known to work well in
the relevant field** rather than an exotic or bespoke first attempt. Prefer a
lean, well-understood instance of an established approach for the problem type —
for example optimal transport for coupling/alignment problems, a graph/network
model for relational or spatial structure, a matrix-factorization or
regularized-regression baseline for factorized or predictive problems, or the
canonical statistical model for the domain. Keep this initial version deliberately
simple and correct; TusoAI's search is what elaborates and specializes it. A
strong-but-simple, field-appropriate starting point gives TusoAI a productive,
well-behaved region to improve from, whereas an idiosyncratic or over-engineered
first method tends to waste early search budget.

### Phase 2 — Build the task/context bundle once

Read `skills/task-construction.md`; read `skills/data-tasks.md` if any external
data could add orthogonal signal.

1. Complete and validate `task_spec.json`.
2. Choose a small set of high-value, uniquely named editable targets. Prefer
   1–4 coherent targets over a broad repository-wide edit surface.
3. Put constraints in model-visible fields and evaluator assertions using the
   context-packet mapping above.
4. Use shared persistent cache/data paths.
5. Implement `task_factory.py` from the template.
6. Build tasks once with `templates/build_task_bundle.py`; store and verify the
   SHA-256 manifest.
7. Smoke-test task serialization/deserialization in the persistent environment.

Phase exit gate: the task spec validates; the bundle hash is fixed; all nodes will
use identical task objects; no user constraint lacks an enforcement route.

### Phase 3 — Calibrate and prove the cluster

Read `skills/multi-machine.md` in full.

1. Inventory all available CPU machines and GPU nodes. Inspect the actual method
   and evaluator for CUDA/JAX/CuPy/PyTorch GPU use before provisioning GPU nodes.
2. Provision compatible machines aggressively enough to use the authorized
   compute, normally at the largest useful CPU/RAM shape. Do not provision GPUs
   for a CPU-only evaluator.
3. Compute each node's `n_jobs` from measured CPU, RAM, API, and GPU limits. Do
   not equate logical CPU count with safe worker count.
4. Install or reuse the exact same persistent environment on every node and
   verify source/task/evaluator hashes.
5. Run `shared_fs_probe.py` concurrently from every proposed node. Multi-machine
   launch is forbidden if any entry is lost, JSON is corrupted, locks remain, or
   atomic replacement fails.
6. Write `cluster_manifest.json` with an absolute deadline, per-node cost limit,
   `n_jobs`, `cpu_threads_per_job`, local `gpu_ids`, and machine IDs.

Phase exit gate: every node is compatible and hash-identical; shared filesystem
semantics pass; resource and budget arithmetic fit with headroom.

### Phase 4 — Leader bootstrap, then follower fan-out

Use `templates/launch_tusoai_node.py` from the persistent task workspace.

1. Start exactly one leader as a managed background job with
   `multi_machine=True`, stable shared `output_dir`, and stable non-empty
   `history_name`.
2. Keep the leader machine awake. Monitor until the shared history contains at
   least one valid candidate record and checkpoint it.
3. Start all followers as managed jobs. Followers load the exact task bundle and
   shared history; they do not rebuild tasks or seed independently.
4. Give each node a unique log/status path and background name. Use local GPU IDs
   on each machine.
5. Immediately record all job handles, machine IDs, deadline, and node budgets in
   `run_state.json`.

Do not launch multiple leaders into an empty history. Duplicate seeding increases
cost, races initialization, and reduces useful search time.

### Phase 5 — Monitor, checkpoint, and continue until the global budget ends

Read `skills/monitoring-and-checkpointing.md` in full.

At least every 8–10 minutes while any node is active:

1. make a real Biomni tool call;
2. inspect node terminal/running state without destructive intervention;
3. run `scripts/checkpoint_history.py` against the shared history;
4. mirror status/history/selected code to `/mnt/results/`;
5. update cluster cost estimate, best score, candidate throughput, recent failure
   modes, last heartbeat, and next action in `run_state.json`;
6. emit a concise progress line to the user when appropriate.

At an epoch callback, checkpoint first, then assess whether failures reflect bad
candidates or bad infrastructure. If the global budget remains, the legal next
action is to resume another epoch or repair/relaunch failed nodes—not to ask the
user whether to continue. Reuse the same history and task bundle.

Change the meta-setup only at a checkpoint boundary and only with evidence:
misaligned evaluation, unreachable target, high infrastructure-failure rate,
poor task context, insufficient data, unsafe resource pressure, or sustained
search pathology. Preserve the old history and write a new comparable run ID if
the score contract changes.

Phase exit gate: global cost/deadline exhausted, explicit user interruption, or a
recorded unrecoverable blocker after honest repair attempts.

### Phase 6 — Select, revalidate, and report

Read `skills/history-selection.md` in full.

1. Preserve the full shared history and all node logs.
2. **The final deliverable is TusoAI's complexity-aware near-best selection, not
   the raw highest-score candidate.** Compute it with TusoAI's own selector
   (`pick_history_solution` from `repo/examine_results.ipynb`, i.e. the
   `_dm_history_close_set` + `_dm_history_complexity_score` logic) using the run's
   `min_improvement`. Also extract the raw highest-score candidate, but keep it
   only for comparison/provenance — do **not** ship it as the final method unless
   the complexity-aware pick fails revalidation (see step 5).
3. Inspect code for leakage, evaluator exploitation, hard-coded paths, hidden
   labels, nondeterminism, writes/network, brittle imports, and unnecessary
   complexity.
4. Rebuild from the protected source and apply each finalist cleanly. Apply the
   complexity-aware selection as the primary finalist and the raw best as the
   comparison finalist.
5. Rerun baseline plus finalists for multiple repetitions and, where relevant,
   folds/seeds/full datasets. Confirm the improvement exceeds measured noise.
   Ship the complexity-aware selection as final; fall back to the raw best (or
   the next-best reproducible complexity-aware candidate) only if the
   complexity-aware pick fails to reproduce or fails an integrity check.
6. Export final source, exact patch, environment, commands, task spec, bundle and
   evaluator hashes, history, validation table, resource/cost summary, accepted
   method rationale, and remaining uncertainties.
7. Only after durable final outputs exist may cluster machines be released or
   hibernated.

## Resource utilization rules

- **CPU-only evaluator:** use multiple CPU machines when shared-FS and API
  concurrency permit. On each host, cap `n_jobs` by CPU threads, 75% usable RAM,
  evaluator RSS × 1.35 headroom, and provider concurrency.
- **GPU evaluator:** provision GPU nodes only after a CUDA smoke test. Start with
  one evaluation job per GPU; increase only after measured GPU-memory headroom.
  `gpu_ids` are local indices on each node. Keep enough CPU threads per GPU to
  feed preprocessing and data loading.
- **Nested parallelism:** explicitly set `cpu_threads_per_job`; TusoAI propagates
  it to OMP/MKL/OpenBLAS/NumExpr/BLIS. Avoid `n_jobs × BLAS_threads` exceeding the
  host's available CPUs.
- **Mixed hardware:** do not combine CPU-only and GPU-required nodes in one
  history unless candidate code is contractually portable across both and the
  evaluator has been tested on both. Hardware-dependent scores are not directly
  comparable.
- **Memory limit:** `memory_limit_gb` is per evaluation process, not per host.
  Ensure `n_jobs × expected_RSS` plus parent/cache overhead fits physical RAM.
- **API throughput:** more CPU processes do not help after model-provider rate
  limits saturate. Use observed 429s/latency to set the cluster concurrency cap.

## Progress format

Use a compact heartbeat such as:

```
TusoAI epoch E | nodes R/T | candidates N (+Δ) | best S | selected S2 | est. cluster cost $C/$B | deadline YYYY-MM-DD HH:MM TZ | action
```

For failures, identify node, stage, error class, whether shared history remains
valid, and the automatic recovery action. Do not report an infrastructure failure
as a scientific plateau.

## Stop conditions

Before the global budget is exhausted, the run may stop only for:

- explicit user interruption;
- platform hard-stop with no replacement capacity;
- invalid or unavailable credentials/data that cannot be repaired;
- shared filesystem semantics that make multi-machine unsafe **and** no usable
  single-machine fallback;
- no legal editable target that reaches the evaluator;
- a safety/privacy restriction that forbids continuation.

When multi-machine is unsafe but one machine is usable, fall back to the largest
compatible single machine, maximize safe `n_jobs`, preserve the same history, and
continue. A cluster problem is not permission to abandon the task.

## Start now

On every invocation:

1. resolve `SKILL_ROOT` and read the relevant canonical sub-skill;
2. run the cold-start/resume check before creating or deleting anything;
3. persist or validate the context packet;
4. prove evaluator reachability;
5. build one task bundle;
6. calibrate and prove the shared cluster;
7. launch leader then followers without hibernating active machines;
8. checkpoint and continue across bounded epochs until the global budget ends;
9. revalidate and export the final method before releasing resources.
