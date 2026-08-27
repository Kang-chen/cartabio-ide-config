
# Parse TusoAI history

Use this skill after a TusoAI run has produced a history file and the user wants to inspect progress, plot optimization performance, extract the selected best code, or decide what to do next.

The canonical reference is repository-root `examine_results.ipynb`. Follow its logic rather than inventing a new selection rule.

## Required inputs

Find or ask for:

- `history_path`: path to the TusoAI `history.json` or equivalent history file.
- The original runner/reference file used by `optimize`.
- The original `MethodTask` and `DataTask` definitions, especially `function_name`, `source_path`, and `repo_root`.
- The original `min_improvement` value from the run. Use the same threshold for both significant-improvement plots and history selection.

If `min_improvement` is not given, recover it from launch scripts, notebooks, logs, or history metadata before falling back to the setup default. Also search for the common misspelling `min_improvment` in user notes and scripts.

## Outputs

Write outputs under `/mnt/results/history_analysis/<run_name_or_timestamp>/`:

- `summary.md`: run path, threshold, selected code summary, improvement trajectory, and interpretation.
- `progress.png`: the same top-performance-over-iteration plot from `examine_results.ipynb`, with cumulative cost shown on the secondary x-axis when cost is present.
- `significant_improvements.txt` or `.md`: entries whose gains meet the original `min_improvement` threshold.
- `selected_code/`: selected function implementations and, when possible, a runnable materialized workspace/repo.
- `selected_summary.json`: selected accuracy, best accuracy, runtime, lineage, code length, selected function names, and whether signatures were aligned.

Do not delete or overwrite existing analysis directories; create a new timestamped directory or move old outputs aside.

## Workflow

### 1. Locate and validate the run

- Use `rg --files results . | rg 'history.*\.json|launch|run|log|notebook'` or similarly targeted searches.
- Inspect the launch script/notebook to recover `min_improvement`, task definitions, `history_name`, output directory, model settings, time/cost limits, and `load_history` if used.
- Confirm the history file has numeric `accuracy` fields. If the format is wrapped, use the contained `history` list.

### 2. Reproduce the notebook selection logic

Use the same helpers imported by `examine_results.ipynb`:

- `_dm_load_history_records_pool`
- `_dm_history_close_set`
- `_dm_history_complexity_score`
- `_dm_get_selected_history_summary`
- `_dm_collect_function_sources`
- `_dm_extract_base_functions`
- `_dm_init_repo_snapshots`
- `_dm_prepare_eval_workspace`
- `_dm_apply_function_updates`

Selection rule:

1. Load history records for the ordered target function names.
2. Compute the close set with `min_improvement`.
3. Identify the top-accuracy model in the close set.
4. Select the model with the best `_dm_history_complexity_score(model, top_accuracy)`, matching `examine_results.ipynb`.
5. Align selected function signatures to current source signatures before materializing code, as the notebook does.

### 3. Generate the same progress plot

Replicate `plot_history_progress(history_path, sig_improvement=min_improvement)`:

- Accumulate cost from each entry's `cost` field when present.
- Plot best-so-far accuracy/performance over history iteration.
- Show cumulative cost as a secondary x-axis.
- Mark significant improvements where the new best improves by at least `min_improvement`.
- Save the figure to `progress.png`; do not only display it interactively.

### 4. Extract and analyze selected code

- Print and save each selected target function's code by function name.
- Save the materialized runnable workspace if reference file and repo/task wiring are available.
- Summarize what changed relative to the baseline: added features, scoring formula changes, model architecture changes, data usage, complexity, runtime impact, and any suspicious leakage/overfitting risk.
- If the selected model is not the raw highest-accuracy model because the close-set/complexity rule chose a simpler nearby candidate, explicitly report both scores and why selection differed.

### 5. Interpret optimization health

Use the trajectory to answer:

- Did TusoAI keep finding significant improvements after the early phase?
- How long/costly was the plateau since the last significant improvement?
- Are improvements real relative to `min_improvement` and metric noise?
- Did code length/runtime grow in a way that suggests overfitting or excessive complexity?
- Should the user read the sibling `rerun.md` file in this skill folder (`Read(file_path=".../tusoai/rerun.md")`) to decide whether to continue unchanged or modify the TusoAI setup?

## Minimal script pattern

When converting the notebook into a script, preserve its function names and logic, but write files under `/mnt/results/history_analysis/...` and use a non-interactive matplotlib backend if needed.
