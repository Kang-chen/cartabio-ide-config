# Task construction and instruction propagation

Use this file after evaluator fidelity is proven. The goal is to give TusoAI a
compact, high-signal search space in which every user instruction is visible to
the model or enforced by code—not merely recorded in prose that the optimizer
never sees.

## Required outputs

- validated `task_spec.json`;
- `task_factory.py`;
- persistent construction/literature cache;
- `task_bundle.pkl` and `task_bundle_manifest.json`;
- `instruction_coverage.md` or the coverage section of the task spec;
- a smoke-test log showing the bundle can be loaded on another process/node.

## 1. Build the context packet first

Copy `templates/task_spec.json` and replace every placeholder. Include:

- concise objective and exact metric;
- each editable function/class/method and its source location;
- actual runtime inputs and available data;
- immutable interfaces, shapes, ordering, dtypes, seeds, privacy, and scientific
  constraints;
- allowed dependencies and hardware;
- forbidden leakage/evaluator manipulation;
- budgets and resource policy;
- a constraint-coverage row for every immutable constraint.

Run `scripts/validate_task_spec.py`. Fix every missing enforcement route before
calling a task-construction LLM.

## 2. Map instructions to the fields TusoAI consumes

Use this mapping deliberately:

| Information | Required destination |
|---|---|
| Scientific objective, metric, desired behavior | `task_description` |
| Inputs available to one MethodTask | `data_available` |
| Function-local contract and feasible interventions | that Task's `hints` |
| Constraints shared by every target | `global_hints` |
| Exact editable file/repository | `source_path`, `repo_root` |
| External data semantics | DataTask `file_description`, `data_usage`, `read_cmd` |
| Hard correctness/leakage rules | evaluator assertions, plus hints |
| Installed packages/hardware capabilities | environment and hints |

Do not assume the optimizer reads Biomni's skill text or `RUNBOOK.md`. It sees the
Task objects, optimize arguments, source, evaluator diagnostics, and prompt
libraries. Anything essential must reach one of those channels.

## 3. Select targets with high leverage and low coupling

Prefer a small number of coherent targets that:

- are on the real score path;
- can be replaced without changing public interfaces;
- have enough context in their source block;
- expose meaningful algorithmic/data choices;
- can be evaluated within the candidate timeout;
- do not force broad cross-repository edits.

Typical targets include feature construction, model fitting, scoring, calibration,
post-processing, candidate ranking, or a narrow data-prior function. Avoid dozens
of tiny helpers; task-selection probability and feedback become diluted. Target
names in one joint run must be unique.

Use a class target or scoped method only when its complete behavior is contained
in the replaceable block and the evaluator reaches it.

## 4. Construct MethodTasks with high-signal search context

For each MethodTask:

- `function_name`: exact top-level/class/scoped target;
- `task_description`: same core objective across tasks, with metric direction;
- `data_available`: precise inputs, shapes, labels available at training time,
  validation rules, and installed resources;
- `hints`: target-specific contract, known failure modes, runtime/memory limits,
  and promising intervention families—not a verbose restatement of the entire
  skill;
- `source_path` and `repo_root`: absolute persistent paths or paths resolved
  consistently across every node;
- `paper_searches`, `instruction_count`, `num_init`, and category count sized to
  the global budget.

Do not overload hints with mutually conflicting directions. Distinguish hard
constraints from optional ideas. Keep hard constraints short and repeated in the
evaluator where possible.

Initial solutions should be valid, diverse algorithmic baselines—not cosmetic
parameter variants. Include the real original implementation unless there is a
clear reason not to use it.

## 5. Construct DataTasks only for orthogonal prepared data

Read `skills/data-tasks.md`. A DataTask is not a generic preview of the existing
training table. It should let one narrow function use an additional prepared
source that can add signal independently of the method logic.

All expensive download/index/preprocessing occurs before optimization. Candidate
functions read stable prepared files through absolute shared paths.

## 6. Reuse construction work; never rebuild it per machine

Use a persistent cache under:

```
/mnt/shared-workspace/tusoai/<task_id>/cache/
```

Set `clear=False` unless the source/context truly changed. Literature searches,
PDF summaries, categories, instructions, and initializations can dominate startup
cost; followers must not repeat them.

Implement `task_factory.py` from `templates/task_factory.py` with:

- `build_ai()` — a fresh provider client for each node;
- `build_task_bundle(ai)` — called exactly once on the coordinator to create all
  MethodTask/DataTask objects and optimize metadata.

Then run:

```bash
PYTHONPATH="$SKILL_ROOT/repo" python \
  /mnt/shared-workspace/tusoai/<task_id>/build_task_bundle.py \
  --factory /mnt/shared-workspace/tusoai/<task_id>/task_factory.py \
  --output /mnt/shared-workspace/tusoai/<task_id>/task_bundle.pkl \
  --manifest /mnt/shared-workspace/tusoai/<task_id>/task_bundle_manifest.json
```

Every node must verify the manifest SHA-256 before unpickling. The bundle is
trusted local state; never load an untrusted external pickle.

## 7. Put cluster-invariant and node-local settings in the right place

Bundle these cluster-invariant settings:

- task objects;
- evaluator path;
- `task_description` and `global_hints`;
- `timeout`, `bug_retries`, `prompt_samples`, `min_improvement`, `max_islands`,
  `sensitive_data`, and per-evaluation memory policy.

Do **not** freeze these node/epoch-specific settings into the bundle:

- shared `output_dir`, `history_name`, and `load_history`;
- `TIME_LIMIT` and per-node `COST_LIMIT`;
- `n_jobs`, `cpu_threads_per_job`, and local `gpu_ids`;
- node ID, role, machine ID, log/status path, or absolute deadline.

The launch wrapper supplies those consistently from `cluster_manifest.json`.

## 8. Smoke-test the serialized bundle

In a fresh Python process and, ideally, on a second provisioned node:

1. verify the bundle hash;
2. unpickle it using the same bundled TusoAI source;
3. list target names and confirm uniqueness;
4. verify every source/evaluator/data path exists;
5. ensure no task points into `/workspace/` or a node-local home directory;
6. inspect hints/global hints against the constraint coverage matrix;
7. run the baseline evaluator from that node.

Do not launch a cluster with path-divergent or unpicklable tasks.

## 9. Task-construction quality gate

Advance only when:

- every immutable constraint is enforced somewhere real;
- objective, score direction, available data, and edit scope are unambiguous;
- target names are unique and candidate-reachable;
- task count is focused;
- caches and all paths are persistent/shared;
- one exact task bundle is built and hashed;
- a second process/node can load it and run the baseline;
- no follower will independently perform task construction or literature search.
