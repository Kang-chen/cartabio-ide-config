# History inspection, finalist selection, and revalidation

Use this file for checkpoints and final selection. The highest validation score
is **not** the final method: the default final deliverable is TusoAI's
complexity/runtime-aware near-best selection. The raw maximum is kept only for
comparison. Compute the complexity-aware pick with TusoAI's own selector
(`pick_history_solution` in `repo/examine_results.ipynb`) and revalidate from
clean source before shipping.

Rationale: on small/scaffold-split tasks the per-fold CV signal is noisy, so
`argmax` CV overfits the objective and transfers poorly. The complexity-aware
pick — the simplest, fastest candidate within `min_improvement` of the top score
— generalized marginally better in practice (smaller CV→test gap, lower fold
variance) while never touching test labels. Prefer it as the final method.

## 1. Preserve the complete run first

Before analysis:

- checkpoint and mirror shared `history.json`;
- preserve every `dev_<run-id>.json`, `prompt_io_<run-id>.json`, node log/status,
  cluster manifest, task spec/bundle manifest, source/evaluator hashes, and
  environment lock;
- never edit or compact the only history copy during analysis.

Use `scripts/checkpoint_history.py` for the standard mirror. It supports the
compact string-table history format and estimates per-run cluster cost.

## 2. Understand the two standard selections

- **Complexity-aware selected (DEFAULT final method):** among candidates within
  `min_improvement` of the top score (the "close set"), pick the one that
  maximizes TusoAI's complexity score — shortest and fastest code. This is the
  candidate that gets shipped as the final deliverable.
- **Raw best (comparison only):** candidate with maximum numeric `accuracy`. Keep
  it for the comparison table and provenance; do not ship it as final unless the
  complexity-aware pick fails revalidation.

Compute the complexity-aware selection with TusoAI's own logic rather than
reimplementing it:

```python
# from repo/examine_results.ipynb :: pick_history_solution
from tusoai.optimization import (
    _dm_load_history_records_pool, _dm_history_close_set,
    _dm_history_complexity_score,
)
records  = _dm_load_history_records_pool(history_path, ordered_fn_names)
close    = _dm_history_close_set(records, min_improvement)   # best_acc - acc < min_improvement
top_acc  = max(close, key=lambda m: float(m.accuracy))
selected = max(close, key=lambda m: _dm_history_complexity_score(m, top_acc))
```

- `min_improvement` is the value used by the optimization run (e.g. `0.005`); read
  it from the run's `optimize_kwargs`/evaluator config, do not assume.
- `_dm_history_complexity_score(candidate, anchor) = (anchor_lines/cand_lines) *
  (anchor_runtime/cand_runtime)`; higher = simpler and faster.

Both are written to results. A slightly lower CV score that is much simpler and
faster typically generalizes better; that is exactly why the complexity-aware
pick is the default, with the raw best retained for comparison.

Do not compare scores from histories with different evaluator/split/metric hashes
as though they share one scale.

## 3. Build a finalist table

Select at least:

- complexity-aware selected (the primary/default finalist);
- raw best (comparison only);
- fastest candidate within the near-best set;
- one or more recent/diverse high performers when scores are noisy;
- protected baseline.

For each record:

- score, runtime, code length, lineage, run ID, step/optimization time;
- target functions changed;
- dependencies and hardware assumptions;
- evaluator diagnostic summary;
- first appearance and repeat occurrences;
- leakage/path/write/network/nondeterminism review;
- whether it can be applied cleanly to protected source.

Deduplicate identical code before expensive validation.

## 4. Inspect for invalid optimization behavior

Reject or quarantine candidates that:

- read validation/test labels, expected outputs, evaluator source, or score files;
- hard-code sample IDs, paths, split-specific constants, or answers;
- import the original repository instead of the dynamic/applied source;
- make network calls or persistent writes during evaluation;
- exploit randomness, time, process state, environment variables, or stale cache;
- change public signatures, schemas, order, shape, dtype, or seed semantics;
- reduce data, iterations, precision, convergence, or validation strength without
  authorization;
- silently fail and return a favorable fallback;
- depend on unavailable/private packages or one node's local files;
- create unacceptable runtime, memory, or code complexity.

Review prompt/dev logs when code is surprising. A high score with implausible
mechanism deserves more scrutiny, not less.

## 5. Apply finalists to clean protected source

For each finalist:

1. copy the protected baseline source into a fresh validation directory;
2. apply only the target blocks/patch represented by the candidate;
3. verify import resolution points into that clean applied source;
4. rebuild/reinstall compiled or editable packages as required;
5. run unit/interface assertions before the metric;
6. record an exact patch and resulting source hash.

Do not validate a leftover TusoAI temporary workspace as the final deliverable.

## 6. Revalidate statistically and scientifically

Run baseline and finalists under identical conditions with enough repetitions to
exceed measured noise. Depending on the task, add:

- multiple seeds/folds/splits;
- full dataset after subset search;
- held-out cohorts or time periods;
- edge cases and schema tests;
- CPU/GPU portability checks;
- runtime and peak RSS;
- ablation of added external data;
- calibration or subgroup metrics;
- exact output digest for deterministic paths.

Report mean, standard deviation, repetitions, and raw values. The final
improvement must exceed the previously chosen `min_improvement` and remain valid
under the real target distribution.

If the best history score does not reproduce, diagnose data, seed, dependency,
hardware, or evaluator differences and choose a reproducible finalist. Do not
hide regression behind the historical maximum.

## 7. Final export

Export to `/mnt/results/tusoai/<task_id>/`:

- `final_method/` clean source tree or exact edited files — this is the
  **complexity-aware selection** (unless it failed revalidation and a documented
  fallback was used);
- `final.patch` against protected baseline;
- `selected.py` (complexity-aware = the shipped final method) and `best_score.py`
  (raw best, comparison/provenance only);
- `history.json` and run manifests;
- `final_validation.json` with raw repetitions;
- `report.md` covering objective, evaluator, task context, cluster topology,
  budgets/cost, candidate throughput, final method rationale, performance,
  runtime/memory, data use, rejected invalid candidates, limitations, and exact
  reproduction commands;
- plots when useful;
- environment lock and source/evaluator/task hashes.

Only after these artifacts are durable and every active job is terminal may
machines be released or hibernated.
