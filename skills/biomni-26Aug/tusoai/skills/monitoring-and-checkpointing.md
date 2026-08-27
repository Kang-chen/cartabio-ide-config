# Monitoring, checkpointing, and autonomous continuation

Use this file from leader launch until the global budget ends. Monitoring has two
purposes: keep Biomni and its machines alive, and distinguish productive search
from infrastructure failure or an invalid meta-setup.

## Required heartbeat cadence

While any node is active, make a real Biomni tool call at least every 8–10
minutes. A useful heartbeat performs work: checkpoint history, inspect managed
job state, update `run_state.json`, or send a concise progress report. Writing to
stdout inside the background process does not count as an agent heartbeat.

Do not poll a completion endpoint in a tight loop. Use managed callbacks plus the
bounded heartbeat cadence.

## 1. Checkpoint shared history and best code

At every heartbeat and immediately on any callback, run:

```bash
PYTHONPATH="$SKILL_ROOT/repo" python \
  "$SKILL_ROOT/scripts/checkpoint_history.py" \
  --history /mnt/shared-workspace/tusoai/<task_id>/run/history/history_<name>/history.json \
  --results-dir /mnt/results/tusoai/<task_id> \
  --min-improvement <absolute-threshold> \
  --label tusoai
```

This mirrors:

- full history;
- raw highest-score code;
- complexity-aware near-best selected code;
- history/candidate counts;
- per-run and estimated cluster optimization cost;
- latest stage and score/runtime summary.

Also copy the checkpoint status into the shared `checkpoints/<timestamp>/`
directory when it marks an epoch boundary or important recovery point.

## 2. Update authoritative state atomically

Update these fields in `run_state.json`:

- phase, epoch, active job handles, node states, and machine IDs;
- last heartbeat/checkpoint timestamps;
- history entry/candidate counts and growth since the prior checkpoint;
- best and selected score/runtime/code path;
- estimated cluster cost plus construction cost;
- remaining global cost and time after shutdown reserve;
- recent failure counts by stage/error class;
- API throttling/latency, evaluator timeout rate, OOMs, lock errors;
- resource utilization observations;
- blockers and immediate next actions.

Append a concise immutable event to `events.jsonl`. Never rely only on the latest
chat context to remember why parameters changed.

## 3. Assess search health with evidence

Track at least:

- candidate throughput per node and cluster;
- valid evaluation rate;
- score distribution and best-improvement timestamps;
- proportion of syntax/repair/evaluator/timeout/OOM/API failures;
- number of distinct run IDs contributing to shared history;
- CPU/GPU utilization, memory headroom, and IO wait;
- total and per-node model cost;
- recent prompt category/task selection if available from dynamic state;
- selected code length/runtime and any pathological complexity growth.

Interpret symptoms correctly:

- low score improvement with high valid throughput may be a scientific plateau;
- low valid throughput, repeated copy/import/timeout/OOM/lock errors is
  infrastructure failure;
- one run ID in a supposed cluster means followers are not contributing;
- many history directories means mismatched `history_name`/`output_dir`;
- unchanged scores after diverse candidate code can mean evaluator reachability
  failure;
- high 429/error latency means total LLM concurrency is too high;
- low GPU utilization with CPU preprocessing saturation means more GPUs will not
  help until the input pipeline is fixed.

## 4. Keep active machines awake

Never hibernate or release a node because its utilization temporarily falls or a
follower waits for a leader seed. Keep every participating machine active until
its managed job is terminal and its state is checkpointed.

If a platform power/lifecycle event pauses a node:

1. checkpoint shared state from another live node;
2. resume the same machine if safe, otherwise provision a compatible replacement;
3. verify environment/source/task/evaluator hashes;
4. relaunch with a new node ID, same history identity, and `load_history`;
5. record the event and continue.

Do not reset the cluster or discard history.

## 5. Handle node callbacks

On a node completion/failure callback:

1. call the managed job-output tool once and capture the terminal log;
2. checkpoint history before any cleanup;
3. read the node status JSON and classify the cause;
4. verify shared history parses and best code is mirrored;
5. update state/event log;
6. if global budget remains, repair/relaunch that node or start the next epoch;
7. do not ask the user for permission to continue within the authorized budget.

A completed node can coexist with active peers. Do not stop healthy peers merely
because one node reached its local limit.

## 6. Epoch-boundary decisions

Use 4-hour epochs by default, shorter when platform limits or uncertainty demand
it. At a boundary, choose one evidence-based action:

- continue unchanged with the same history;
- adjust node count or `n_jobs` for measured saturation/memory/API behavior;
- replace failed hardware while preserving config hashes;
- increase/decrease per-evaluation timeout or memory from observed failures;
- change provider/model when candidate reasoning quality is clearly limiting;
- refine target hints or add a small high-value MethodTask/DataTask;
- repair evaluator/context and start a new history only if score comparability is
  broken.

Do not change many dimensions at once. Record old/new values and rationale.

A score plateau alone does not end an authorized budget. Continue or make a
checkpointed meta-setup improvement. A thin idea backlog is a reason to refine
construction, not a stop condition.

## 7. Global cost and deadline enforcement

The checkpoint script estimates multi-machine optimization cost by summing the
maximum logged `total_cost` for each run ID. Add task-construction costs tracked in
the bundle manifest. Because calls can be in flight, reserve 5–10% of remaining
cost at launch.

Before every new epoch calculate:

- global remaining cost;
- global remaining seconds to the absolute deadline;
- shutdown/checkpoint reserve;
- epoch allowance;
- per-node cost limits.

Do not launch if the post-reserve time is under one minute or cost is exhausted.
Do not reuse the full original budget as each node's local budget.

## 8. User-visible progress

A concise default line is:

```text
TusoAI epoch E | nodes R/T | candidates N (+Δ) | best S | selected S2 | est. cost $C/$B | remaining Hh Mm | next action
```

Add a short block for a new best, infrastructure incident, parameter/meta-setup
change, or epoch boundary. Be explicit about uncertainty in cost estimates and
whether a score is revalidated.

Progress messages are heartbeats, not invitations to stop. Continue with the next
required tool call while budget remains.

## 9. Checkpoint completeness gate

A checkpoint is complete only when:

- shared history parses and is mirrored;
- best and selected code are materialized;
- node/job states are recorded;
- cost/deadline accounting is updated;
- `run_state.json` and `events.jsonl` are durable;
- the next action is explicit;
- no active machine has been hibernated or released.
