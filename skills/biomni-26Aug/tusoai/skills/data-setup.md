
# Data setup for paper benchmark reproduction

Use this skill when the user wants a working data, inference, and evaluation scaffold for reproducing the **evaluation metrics** from a paper's benchmarks without rerunning the paper's full methods. The goal is a validated, repeatable pipeline with a simple baseline and exact metric computation.

## Non-negotiables

- **Never wipe the workspace or machine.** Do not delete repository contents, home directories, shared caches, or prior `/mnt/results/` artifacts.
- Store all downloaded data, generated files, caches, scripts, logs, predictions, and reports under a `/mnt/results/` directory. Do not use `/tmp`, `/var/tmp`, or disposable scratch folders except for process-local ephemeral files that are immediately moved into `/mnt/results/`.
- Do **not** rerun expensive published methods unless explicitly requested. Implement a transparent simple baseline that exercises the same input/output and metric pipeline.
- Compute metrics exactly as in the original paper whenever possible. Prefer the paper's official code, supplement, benchmark scripts, or primary benchmark documentation over reimplementing from memory.
- If any dataset, split, label, metric detail, or accession is unavailable, document the gap and create the closest faithful fallback without pretending it is exact.

## Expected deliverables

Create the smallest complete scaffold needed for the current repository:

1. `/mnt/results/README.md` or `/mnt/results/benchmark_plan.md` explaining datasets, sources, metrics, baselines, commands, and gaps.
2. Download/preparation scripts under `/mnt/results/scripts/`.
3. Metric implementation under `/mnt/results/scripts/` or a small repository module, with tests or assertions for edge cases.
4. Simple baseline inference code that produces predictions in the same shape/format required by the evaluation.
5. A manifest under `/mnt/results/` listing data files, URLs/accessions, checksums when practical, and preparation status.
6. Validation outputs under `/mnt/results/`, including at least a few completed datasets or representative subsets and their computed metrics.

## Workflow

### 1. Understand the benchmark contract

- Read the paper, supplement, official repository, benchmark pages, and dataset accessions.
- Make a benchmark matrix with one row per dataset/task/split: input modalities, labels/targets, train/validation/test split, prediction output, metric names, metric formulas, aggregation rules, and expected direction.
- Identify which claims are benchmark metrics versus method-running details. The setup must reproduce the metric computation and data flow, not the expensive original model training.

### 2. Source and stage data safely

- Create `/mnt/results/data/`, `/mnt/results/scripts/`, `/mnt/results/predictions/`, `/mnt/results/metrics/`, and `/mnt/results/logs/` as needed.
- Download from primary sources first: official project repository, cited data portals, accession databases, benchmark organizers, or paper supplements.
- Keep raw and processed files separate, e.g. `/mnt/results/data/raw/` and `/mnt/results/data/processed/`.
- Record every source URL/accession, local path, command, timestamp, and any filtering/subsetting in a manifest.
- Use resumable, idempotent scripts. Before downloading large files, check whether the file already exists and looks complete.

### 3. Implement a simple baseline

- Use a principled lightweight baseline that fits the task, such as majority class, nearest centroid, linear/logistic model, random forest, k-nearest neighbors, ridge regression, correlation-based matching, distance heuristic, or marginal-frequency sampler.
- Keep the baseline deterministic with fixed seeds.
- Match the published benchmark input/output interface exactly: sample IDs, class labels, matrix orientation, genomic coordinates, ranking format, or score columns.
- Avoid data leakage. Use only the allowed training/reference data for fitting and the held-out labels only for metric computation.

### 4. Implement exact metrics

- Prefer importing or adapting official metric code if license and dependencies allow; cite its source in notes.
- Match label preprocessing, filtering, class ordering, thresholding, tie handling, averaging mode, confidence intervals, and per-dataset aggregation from the paper.
- For higher-is-better optimization runners, transform loss/error metrics only after preserving the original reported metric in output tables.
- Add small sanity tests for each metric: perfect predictions, constant predictions, missing labels, ties, and shape mismatches where relevant.

### 5. Run and validate

- Run the whole pipeline from data preparation through baseline prediction and metric computation on a few datasets or small representative subsets first.
- Save command logs to `/mnt/results/logs/` and metric tables to `/mnt/results/metrics/`.
- Validate that each script can be rerun without corrupting existing outputs.
- If a full run is practical, run it; otherwise document remaining commands and resource requirements clearly.

### 6. Report status

In the final response, summarize:

- Which datasets and benchmarks were fully staged, partially staged, or blocked.
- What baseline was implemented and why it is appropriate.
- Which metrics were implemented exactly and which required assumptions.
- Commands run and where outputs live under `/mnt/results/`.
- Any next steps needed to complete full-scale reproduction.
