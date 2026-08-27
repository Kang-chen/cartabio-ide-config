# Multi-machine resource calibration and launch

TusoAI multi-machine mode is cooperative shared-history search: one optimizer
process runs on each machine, each process has its own local worker pool, and all
processes exchange candidates through the same `history.json`. It is not a remote
scheduler inside one Python process. Biomni must provision, configure, launch,
and supervise each node.

## Required outputs

- `evaluator_audit.json` with resource measurements;
- cross-node `shared_fs_probe` verification;
- persistent environment/source/task hashes for every node;
- `cluster_manifest.json`;
- one managed job handle, log, and status path per node;
- stable shared `output_dir`, `history_name`, and history path.

## 1. Decide which hardware is actually useful

Inspect source, dependencies, and a real evaluator run for:

- PyTorch CUDA calls, JAX GPU backend, CuPy, CUDA extensions, `*.cu`/`*.cuh`,
  `nvcc`, GPU-specific model libraries, or explicit device configuration;
- CPU-bound NumPy/SciPy/BLAS, multiprocessing, heavy parsing, IO, or memory
  pressure;
- evaluator startup cost and API/model-provider concurrency.

Provision GPU nodes only when the evaluator can use them and the candidate
contract permits GPU code. The TusoAI orchestrator itself does not become faster
merely because a GPU exists; candidate evaluation must use it.

When compute is authorized and useful, provision multiple compatible machines,
usually at the largest practical CPU/RAM shape. Record exact machine IDs and
hardware. Never hibernate active nodes.

## 2. Derive safe per-node parallelism

For each node calculate:

```
jobs_by_cpu    = floor(available_cpu_threads / cpu_threads_per_job)
jobs_by_memory = floor(0.75 * physical_ram / (1.35 * measured_eval_peak_rss))
jobs_by_api    = provider_concurrency_allowance_for_this_node
jobs_by_gpu    = measured_concurrent_evaluations_that_fit_local_gpus
n_jobs         = max(1, min(applicable limits))
```

Use CPU affinity, not only `os.cpu_count()`. Include parent process, source copies,
LLM responses, caches, and filesystem overhead. The 25% RAM reserve and 35%
per-evaluation headroom are defaults, not excuses to ignore a measured worse case.

For CPU BLAS/JAX/PyTorch code, choose `cpu_threads_per_job` explicitly. TusoAI
sets OMP/MKL/OpenBLAS/NumExpr/BLIS limits for evaluator subprocesses. Avoid nested
oversubscription.

For GPU evaluation, start with one job per GPU and inspect peak GPU memory and
utilization. `gpu_ids` are **local** to each node, commonly `[0]`, not cluster-wide
indices or UUIDs. Increase jobs per GPU only when repeated measurements show safe
memory and no throughput loss.

If API 429s or latency rises sharply, reduce total cluster concurrency even when
CPUs are idle. Search throughput, not nominal process count, is the objective.

## 3. Require homogeneous score semantics

All machines must have identical:

- bundled TusoAI source hash and Python version;
- dependency versions and environment variables relevant to the evaluator;
- source snapshot and evaluator hash;
- task bundle hash;
- staged data and checksums;
- model/provider settings;
- score precision, seeds, and hardware-dependent math settings.

Do not combine CPU and GPU nodes if candidate behavior or score changes by
hardware. A mixed cluster is allowed only after explicit cross-hardware baseline
and sentinel tests show comparable semantics and every candidate is required to
support both paths.

## 4. Prove shared filesystem semantics across all nodes

TusoAI v2 uses complete-file replacement plus a hybrid directory/advisory lock.
Multi-machine operation still requires a genuinely shared filesystem with
cross-host visibility and atomic operations.

On the coordinator:

```bash
python "$SKILL_ROOT/scripts/shared_fs_probe.py" init \
  --root /mnt/shared-workspace/tusoai/<task_id> \
  --iterations 25
```

Launch the following **concurrently as managed jobs on every proposed node**:

```bash
python "$SKILL_ROOT/scripts/shared_fs_probe.py" write \
  --root /mnt/shared-workspace/tusoai/<task_id> \
  --node-id <unique-node-id> \
  --iterations 25
```

After all callbacks, verify from at least two nodes:

```bash
python "$SKILL_ROOT/scripts/shared_fs_probe.py" verify \
  --root /mnt/shared-workspace/tusoai/<task_id> \
  --expected-node <node-0> \
  --expected-node <node-1> \
  ... \
  --iterations 25
```

Any missing/extra record, invalid JSON, stale lock artifact, invisible marker, or
atomic-replace failure blocks multi-machine launch. Repair/move to a supported
shared POSIX path, or use the largest single machine with safe `n_jobs`. Do not
pretend separate local histories are one cluster.

## 5. Allocate global budgets safely

Persist:

- global cost budget;
- construction cost already spent;
- optimization cost estimated from shared history;
- epoch cost allowance;
- absolute global deadline;
- shutdown/checkpoint reserve;
- per-node `COST_LIMIT` for this epoch.

Because `COST_LIMIT` is process-local, use conservative allocation:

```
epoch_remaining = min(global_remaining, chosen_epoch_allowance)
allocatable      = max(0, epoch_remaining - safety_reserve)
node_limit       = allocatable * node_weight / sum(active_node_weights)
```

A reasonable safety reserve is 5–10% for in-flight calls. Weight nodes only when
measured throughput differs. Do not give each node `global_remaining`.

Use the same absolute deadline on all nodes. The launch wrapper subtracts the
shutdown reserve and converts the remaining seconds to TusoAI minutes. If fewer
than 60 runnable seconds remain, checkpoint instead of launching.

## 6. Create one stable shared history identity

Choose once per comparable score contract:

```
output_dir  = /mnt/shared-workspace/tusoai/<task_id>/run
history_name = <task_id>_<evaluator-hash-prefix>
history_path = <output_dir>/history/history_<sanitized-history-name>/history.json
```

Every node and resumed epoch uses those exact values. Node IDs belong in log and
status filenames, not `history_name`.

If the evaluator or score contract changes materially, preserve the old run and
start a new history identity. If only time, cost, model, or machine count changes,
resume the same history.

## 7. Bootstrap one leader before followers

1. Copy the launch template into the task workspace.
2. Start one leader with the validated task bundle and cluster-critical settings.
3. Keep the machine awake and emit agent heartbeats.
4. Wait until shared history contains at least one valid candidate (`code` plus
   numeric `accuracy`), not merely a `run_wiring` entry.
5. Checkpoint history and record the leader's node status.
6. Start followers. Their wrapper waits for and then loads the shared history.

Starting all nodes against an empty history can duplicate task seeding and LLM
cost and can race initialization. Leader-first fan-out converts most capacity to
useful evolution.

Each launch must explicitly pass:

- `multi_machine=True`;
- identical `output_dir`, `history_name`, task bundle, evaluator, and source;
- `load_history=<shared history>` when a valid candidate exists;
- node-local `n_jobs`, `cpu_threads_per_job`, `gpu_ids`;
- per-node epoch `COST_LIMIT` and derived `TIME_LIMIT`;
- unique node ID, role, managed background name, log, and status file.

## 8. Launch as tracked Biomni jobs

A representative managed command is:

```bash
PYTHONPATH="$SKILL_ROOT/repo" python \
  /mnt/shared-workspace/tusoai/<task_id>/launch_tusoai_node.py \
  --factory /mnt/shared-workspace/tusoai/<task_id>/task_factory.py \
  --task-bundle /mnt/shared-workspace/tusoai/<task_id>/task_bundle.pkl \
  --task-bundle-sha256 <sha256> \
  --node-id <node-id> \
  --role leader \
  --output-dir /mnt/shared-workspace/tusoai/<task_id>/run \
  --history-name <stable-name> \
  --deadline-epoch <absolute-epoch-seconds> \
  --shutdown-reserve-seconds 2700 \
  --node-cost-limit <usd> \
  --n-jobs <jobs> \
  --cpu-threads-per-job <threads> \
  --gpu-ids <comma-separated-local-ids-or-empty> \
  --status-dir /mnt/shared-workspace/tusoai/<task_id>/status
```

Run it with Biomni's tracked background flag, redirect stdout/stderr to the
node/epoch log, and target the correct `machine_id`. Do not launch it through an
untracked shell daemon.

## 9. Validate the live cluster

Within the first checkpoint interval verify:

- every node status is `running` or a documented wait state;
- shared candidate count increases;
- history entries contain multiple `run_id` values after followers start;
- no node created a different history directory;
- no lock timeout/JSON decode/copy metadata errors occur;
- CPU/GPU utilization and memory match the plan;
- candidate throughput improves over one node;
- API errors remain acceptable;
- estimated cluster cost tracks the allocated epoch budget.

If additional nodes reduce throughput or destabilize the evaluator, scale at an
epoch boundary after checkpointing. Do not hibernate or kill healthy active nodes
mid-candidate merely to tune utilization.
