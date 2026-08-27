
# Rerun or change a TusoAI optimization

Use this skill when a TusoAI run has finished, stalled, hit a limit, or the user wants to keep optimizing. First determine whether TusoAI is working properly and whether it is still improving; then choose the smallest change likely to help.

Start by reading the sibling `parse-history.md` file in this skill folder (`Read(file_path=".../tusoai/parse-history.md")`) when a history file exists. Decisions should be based on the same progress plot, selected best code, significant-improvement threshold, and best-code analysis used by `examine_results.ipynb`.

## Questions to answer first

Before recommending another run, answer these from logs/history/evaluator output:

1. Is TusoAI running correctly?
   - Did the evaluator consistently print `tuso_evaluate: <number>`?
   - Are failures candidate-quality failures or setup/runtime failures?
   - Are target functions being replaced in the intended files?
   - Are costs, time limits, and API/model settings sane?
2. Is TusoAI optimizing?
   - Did best performance improve by at least the original `min_improvement`?
   - When was the last significant improvement?
   - Is the recent plateau short or long relative to total iterations/cost?
   - Are improvements robust under rerunning the best model/evaluator?
3. Does the selected code look useful?
   - Is it principled and compatible with the method?
   - Did it exploit leakage, hidden labels, paths, randomness, or evaluator quirks?
   - Did runtime or code complexity become unacceptable?
4. Does validation still represent the real target?
   - Is the subset too small, too easy, too noisy, or no longer aligned with full-dataset performance?

## Decision options

### 1. Just keep running TusoAI

Choose this when the history shows recent significant improvements, valid candidates are being evaluated, and selected code looks plausible. Continue with `load_history=<history_path>`, same tasks, same evaluation, and the same `min_improvement` unless metric noise has been remeasured.

### 2. Increase the time or cost limit

Choose this when TusoAI is working and improving but stopped because it hit `TIME_LIMIT`, `COST_LIMIT`, a notebook/agent timeout, or an external process limit. Increase the limiting budget and keep `load_history` so the run resumes from the best discovered history.

### 3. Use a different LLM

Choose this when current candidates are syntactically valid but edits are now larger, more coupled, or require stronger code reasoning than the current model seems to handle. Also choose this when many failures are due to subtle interface mistakes. Keep history unless changing initialization completely.

### 4. Add or change MethodTasks

Choose this when the current target functions plateaued, but other self-contained parts of the method could improve the metric: feature engineering, scoring, calibration, post-processing, model construction, or hyperparameter selection. Add only a few high-quality tasks and preserve the runner/evaluation contract, including the preferred `runner.py` plus `method_repo/method.py` structure and relative runner-to-method imports.

### 5. Add or change DataTasks

Choose this when orthogonal data could help the task and can be safely integrated through narrow placeholders. Read the sibling `data-tasks.md` file in this skill folder (`Read(file_path=".../tusoai/data-tasks.md")`) to ask Biomni what data sources could help, stage data under `/mnt/results/`, create absolute-path `read_cmd` strings, add no-op placeholders at the correct method-repository call sites, and wire `create_data_subtask`. Preserve the sibling `runner.py` plus `method_repo/method.py` pattern, with runner imports resolved by relative path rather than absolute path. Update existing DataTasks if the data source, placeholder, or usage instructions are too broad, stale, leaky, or not being used.

### 6. Change initialization completely

Choose this only when the run never optimized at all, the baseline framework boxed TusoAI into a bad design, target functions were wrong, or initializations strongly biased the search toward invalid code. This usually means **do not load the old history** for the new run, because the search should restart from a redesigned baseline/task setup.

### 7. Change the evaluation

Choose this when the best code improves the validation subset but does not generalize, exploits subset artifacts, overfits a tiny split, or fails on full data. Broaden the representative subset, add folds or seeds, improve metric fidelity, precompute stable artifacts, and set `min_improvement` above measured noise. After changing evaluation substantially, be cautious about loading old history because old scores may not be comparable.

## Recommended rerun plan format

Produce a concrete plan with:

- Diagnosis: working/not working, optimizing/not optimizing, last significant improvement, plateau length, and selected best score.
- Recommendation: one of the seven options above, plus why.
- Exact changes: launch parameters, model/provider, task definitions, DataTasks, evaluation changes, initialization policy, and whether to use `load_history`.
- Commands: the exact rerun command or script edits, with logs written under `/mnt/results/`.
- Safeguards: rerun baseline/best evaluator, keep existing history, do not delete prior outputs, and save the new run under a distinct `history_name`.

## Rerun defaults

- If continuing unchanged, set `load_history` to the prior history file and keep `min_improvement` unchanged.
- If only budgets or LLM changed, load history.
- If adding MethodTasks/DataTasks but the evaluation metric is unchanged, loading history is usually useful, but verify task/function compatibility.
- If changing evaluation substantially, treat old history as diagnostic; do not compare old and new scores directly.
- If changing initialization completely because nothing worked, start without `load_history`.
