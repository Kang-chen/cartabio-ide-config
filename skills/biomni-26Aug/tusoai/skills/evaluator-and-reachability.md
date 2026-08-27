# Evaluator fidelity and candidate reachability

Read this file before creating MethodTasks or launching TusoAI. The evaluator is
the optimization objective. A fast cluster attached to the wrong, noisy, leaky,
or unreachable evaluator only produces invalid confidence more quickly.

## Required outputs

Under `/mnt/shared-workspace/tusoai/<task_id>/`, create:

- `runner.py` or the repository-relative evaluator;
- `evaluator_audit.json`;
- `baseline_runs.json` or equivalent raw results;
- `candidate_reachability.md` with commands and observed outcome;
- protected source/data fingerprints in `run_state.json`;
- evaluator assertions or tests for key invariants.

## 1. Establish the benchmark contract

Write down, from the user request and primary benchmark materials:

- exact target and unit of prediction;
- data modalities and allowed train/reference inputs;
- validation/test split and any grouping constraints;
- metric formula, preprocessing, label ordering, tie handling, aggregation,
  missing-value behavior, and score direction;
- deterministic seed policy and expected stochasticity;
- output type, shape, schema, ordering, and units;
- prohibited sources of leakage;
- expected runtime/memory scale.

For a loss, keep reporting the original loss but print its negative value for
TusoAI. Do not silently replace a published metric with a convenient proxy.
Prefer official metric code or an exact licensed adaptation over reimplementation
from memory.

## 2. Use a repository layout that candidate copies can actually reach

Preferred layout:

```
<task>/runner.py
<task>/method_repo/
    package_or_module.py
```

The runner imports the method using paths relative to itself or normal package
installation. Do not hard-code `/mnt/shared-workspace/.../method_repo` or insert
the original repository into `sys.path`: TusoAI evaluates a dynamic copied
repository, and an absolute original path can bypass every candidate edit.

For a target outside the runner, set both `source_path` and `repo_root` on the
Task. `source_path` must identify the file inside `repo_root`. Confirm target
names are exact; scoped methods such as `Model.fit` are supported.

The v2 bundled source explicitly loads an import guard and fails if an import
resolves inside the protected original repository rather than the dynamic copy.
Do not disable that guard to make a runner pass.

## 3. Enforce the score-line contract

A successful evaluator must exit with code 0 and print exactly one standalone
finite line:

```text
tuso_evaluate: <number>
```

Higher is better. The v2 parser accepts decimal/scientific finite numbers and
rejects missing, duplicate, embedded, NaN, or infinite scores. Print diagnostic
material before the score. Use:

- `tuso_model_start` / `tuso_model_end` for global diagnostics; or
- `tuso_fnlog_start:<function>` / `tuso_fnlog_end:<function>` for per-target
  diagnostics.

Do not print hidden labels, sensitive rows, or giant arrays. Set
`sensitive_data=True` when diagnostics themselves could disclose sensitive data.

## 4. Run the evaluator audit

From the persistent environment, run at least three repetitions:

```bash
python "$SKILL_ROOT/scripts/audit_setup.py" \
  --evaluator /mnt/shared-workspace/tusoai/<task_id>/runner.py \
  --repo-root /mnt/shared-workspace/tusoai/<task_id>/method_repo \
  --repeats 3 \
  --timeout <seconds> \
  --threads-per-job <threads> \
  --output /mnt/shared-workspace/tusoai/<task_id>/evaluator_audit.json
```

Treat any invalid run as a launch blocker. Inspect all warnings about `sys.path`
or absolute repository paths. The audit records runtime, score variance, peak RSS
when `/usr/bin/time` is available, CPU affinity, memory, GPUs, and a conservative
worker-count ceiling.

Run more repetitions when the metric is stochastic or noisy. Record raw scores,
not only a mean.

## 5. Choose `min_improvement` from observed noise and scientific relevance

`min_improvement` is an absolute score difference. Set it above score noise and
below the smallest scientifically useful change. A practical starting point is
at least the larger of:

- three baseline score standard deviations; and
- the domain's minimum meaningful absolute improvement.

Do not use an arbitrary default when the score scale is unusual. If the evaluator
is intentionally stochastic, fix seeds where scientifically valid or evaluate
multiple seeds/folds inside one score. Do not let candidate luck dominate search.

## 6. Prove candidate reachability

This gate catches the most damaging silent failure: TusoAI edits a copy, but the
runner imports or calls something else.

1. Preserve the protected baseline.
2. Create a temporary copied repository under the task workspace.
3. Apply a deliberate, reversible sentinel modification to the intended target.
   Choose a change that must alter a visible assertion or score without exposing
   hidden data. Examples: add a unique diagnostic marker; return a known dummy
   prediction on a tiny fixture; or deliberately raise a unique exception.
4. Run the evaluator against the copied source through the same import route that
   TusoAI will use.
5. Confirm the marker, score change, or unique failure appears.
6. Restore/remove the temporary copy and document commands and evidence.

A sentinel that produces the unchanged baseline score is a hard failure. Trace
imports, source paths, call sites, caching, subprocesses, and compiled extensions
until the candidate copy is authoritative.

Also verify the reverse: the normal baseline passes when the protected source is
used.

## 7. Protect correctness and leakage boundaries

Add evaluator checks for the constraints most likely to be gamed accidentally:

- exact row/sample IDs and order;
- output shape/type/dtype/schema;
- no NaN/Inf unless explicitly valid;
- no access to held-out labels before scoring;
- no network access or persistent writes from candidate functions;
- stable seed behavior;
- train/validation separation and grouping;
- finite runtime and memory;
- optional full-data or cross-fold guardrail.

Keep hidden labels and evaluator-only files outside the editable repository and
read-only where possible. Avoid environment variables whose names reveal answers.

## 8. Calibrate timeout and memory

Use the baseline distribution, not guesswork:

- `timeout` should allow normal variance plus initialization and candidate
  overhead, but should terminate pathological methods promptly;
- `memory_limit_gb` is per candidate subprocess and must be below physical RAM;
- account for `n_jobs` concurrent processes and parent/cache overhead;
- separate cold-start from steady-state behavior when imports/JIT matter.

If a GPU evaluator is possible, test CPU and GPU explicitly and decide which
hardware contract the search will optimize. Scores from materially different
hardware paths must remain comparable.

## 9. Final evaluator gate

Do not advance until all are true:

- exact score-line contract passes every repetition;
- score direction is correct;
- baseline and metric match the intended benchmark;
- noise and `min_improvement` are recorded;
- target edits demonstrably reach the evaluator;
- no hard-coded original-repository import remains;
- hidden data/evaluator files are protected;
- timeout, peak memory, and hardware behavior are measured;
- rerunning the same baseline is sufficiently stable for the search objective.
