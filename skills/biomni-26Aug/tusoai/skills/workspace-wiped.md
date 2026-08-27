
# Workspace-wiped recovery

Use this skill when the user says the workspace was wiped, reset, deleted, or lost and asks to recover or continue an earlier benchmark/data/TusoAI optimization task.

## Recovery principles

- **Never wipe, clean, reset hard, or delete broad directories.** Do not run destructive commands such as `rm -rf`, `git reset --hard`, `git clean`, or workspace reinitializers unless the user explicitly requests a specific safe deletion.
- Preserve all surviving files. Move questionable or conflicting artifacts into timestamped folders under `/mnt/results/recovery/` rather than deleting them.
- Reconstruct the setup in `/mnt/results/`; do not rely on temp directories.
- Prefer resuming from existing TusoAI history, model records, checkpoints, logs, or metric tables over starting from scratch.
- If a current best TusoAI model exists, use it as the starting point for continued optimization.

## Workflow

### 1. Triage without destruction

- Record current time, working directory, git branch, `git status --short`, and top-level file listing in `/mnt/results/recovery/<timestamp>/triage.txt`.
- Search for surviving artifacts with `rg --files` and targeted `find` commands. Look for `/mnt/results/`, TusoAI histories, model records, JSON/PKL/CSV metrics, launch scripts, runners, logs, notebooks, and downloaded data.
- Inspect shell history, notebooks, README files, and logs if available to recover the original user instruction and previous commands.

### 2. Recreate the original task context

- Restate the initial instruction from recovered notes or the user's message.
- Rebuild the benchmark/data setup plan: datasets, source URLs/accessions, scripts, simple baseline, metric implementations, and validation commands.
- Recreate missing directories under `/mnt/results/` only: `data/`, `scripts/`, `predictions/`, `metrics/`, `logs/`, `recovery/`, and `tusoai/` as needed.
- Redownload or regenerate only missing artifacts; skip files that already exist and pass basic size/checksum/schema checks.

### 3. Restore or infer TusoAI state

- Search for optimization histories, best model records, candidate source snapshots, scores, and launch configurations.
- Identify the current best model by the highest valid evaluation score in recovered history. If multiple histories exist, document which one was selected and why.
- If no history survived, recreate the baseline and launch configuration from the recovered setup, then start a fresh history under `/mnt/results/tusoai/`.
- If history survived, continue optimization from that history/current best model rather than discarding it.

### 4. Validate before continuing

- Run the data setup and metric sanity checks first.
- Run the evaluator on the recovered best model or baseline and confirm it prints the expected score format, such as `tuso_evaluate: <number>` for TusoAI runners.
- Compare recovered scores with previous logs when possible. If scores differ, investigate data paths, seeds, dependency versions, and metric assumptions before continuing.

### 5. Continue the optimization experiment

- Resume with conservative limits until the recovered pipeline is proven stable.
- Save new logs, histories, best models, and summaries under `/mnt/results/tusoai/` or `/mnt/results/logs/`.
- Keep a recovery note describing what was lost, what was reconstructed, which best model was used, and the exact commands needed to continue.

## Final response checklist

Report:

- What survived and where it was found.
- What was rebuilt under `/mnt/results/`.
- Whether a current best TusoAI model/history was found and used.
- Validation commands and results.
- The next command to continue optimization, if not already running.
