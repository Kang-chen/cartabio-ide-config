
# DataTasks for orthogonal data-derived information

Use this skill when a TusoAI setup should let the optimizer use **new data as an orthogonal source of information**, not merely preview an existing training file. Examples include:

- Creating enhancer-gene linking features from heterogeneous genomic annotations, sequence, eQTL, chromatin, or conservation data.
- Creating gene embeddings or gene covariates from genetic, pathway, literature, or perturbation resources for genetic perturbation prediction.
- Creating priors, motif/context features, structural annotations, or property tables for protein design.

A DataTask gives TusoAI instructions for one placeholder function that can read a staged data source and add useful derived information to the method. It should be narrow, optional, and safely integrated into the method repository.

## Core rules

- Add DataTasks only when extra data could improve the metric independently of the main method logic.
- Stage all candidate data and generated data notes under `/mnt/results/`; never use temp directories as the durable source.
- Use **absolute paths** inside DataTask `read_cmd` strings so TusoAI evaluation workspaces can find the staged data unambiguously.
- First get a minimal, deterministic `read_cmd` that runs and returns compact schema/statistics. Do not write a heavy feature pipeline before validating the read command.
- Add placeholder functions in the **method repository at the correct call site** before creating DataTasks. Use a layout like `runner.py` plus `method_repo/method.py`; the runner must import/call the method through a relative path to the sibling method repository, and the method must remain valid if placeholders do nothing.
- Placeholder functions should be true no-ops by default: no data loading, no network, no persistent writes, and no changed predictions/features unless TusoAI rewrites them.
- Keep all expensive downloads, preprocessing, and indexing outside candidate functions. Candidate functions may read prepared files but should not create them.
- Do not expose hidden validation/test labels through a DataTask.

## Ask Biomni what data could help

When setting up DataTasks with Biomni, explicitly ask it to propose useful data sources for the task before choosing files:

```text
Ask Biomni: For this task and metric, what external or heterogeneous biological data sources could provide orthogonal signal? Prioritize sources that can be downloaded or summarized into `/mnt/results/`, used without hidden labels, and exposed through a narrow placeholder feature/embedding/prior function.
```

For each proposed source, gather into `/mnt/results/data_tasks/<source_name>/`:

- `source_notes.md`: why the data might help, task relevance, license/access notes, and leakage risks.
- `manifest.json` or `manifest.tsv`: source URLs/accessions, local absolute paths, file sizes/checksums when practical, and preparation commands.
- Raw/processed files under `raw/` and `processed/`.
- A tiny sample or schema summary for fast inspection.
- The final read command and any read-command validation log.

Prefer a small number of high-signal sources over many loosely related files.

## Add the placeholder into the method repository

Before calling `create_data_subtask`, edit the target method repository so the new data feature function exists and is called in the right location. Prefer keeping placeholders under `method_repo/` (for example `method_repo/method.py`) and keeping `runner.py` as the evaluator that imports that repository by relative path.

Good placeholder properties:

- Its name describes the data and output, e.g. `gencode_grch38_primary_assembly_genome_features`.
- Its input/output contract is narrow and stable, such as `features_df -> features_df`, `gene_ids -> embedding_df`, or `protein_df -> prior_df`.
- It is located next to the relevant feature-building or scoring code in the method repository (for example `method_repo/method.py`), not in the runner unless the method truly lives there.
- Its baseline body returns inputs unchanged or an empty same-schema table.
- Any example data-loading code is commented out until the `read_cmd` has been validated.

Example no-op feature placeholder:

```python
def gencode_grch38_primary_assembly_genome_features(features_df):
    """Optional DataTask hook. TusoAI may add GENCODE-derived SNP/gene features.

    Baseline intentionally does nothing. Do not load data here unless a DataTask
    candidate rewrites this function using the prepared absolute-path read_cmd.
    """
    # Example only; keep commented in the baseline until read_cmd is validated:
    # data = pd.read_csv("/mnt/results/data_tasks/gencode/raw/GRCh38.primary_assembly.genome.fa.gz", ...)
    return features_df
```

Then make the existing method call the placeholder where the new data belongs, for example immediately after the base `features_df` is constructed and before scoring/training.

## Build the DataTask

Use `create_data_subtask` after the placeholder and read command exist. Prefer explicit `read_cmd` over inferred `data_path` for unusual formats, compressed files, multi-file data, or when absolute paths matter.

Example:

```python
from pathlib import Path

results_root = Path("/mnt/results")
gencode_path = (results_root / "data_tasks" / "gencode" / "raw" / "GRCh38.primary_assembly.genome.fa.gz").resolve()

read_cmd = f"""
import pandas as pd
# Absolute path is required for DataTasks.
data = pd.read_csv({str(gencode_path)!r}, sep="\t", comment=">", header=None, compression="gzip")
print(data.head())
print(data.shape)
"""

data_usage = "create SNP-gene features"
data_instruction_count = 30
function_name = "gencode_grch38_primary_assembly_genome_features"
file_description = "Gencode GRCh38 primary assembly genome sequence"

data_task, data_cost = ai.create_data_subtask(
    function_name=function_name,
    task_description=task_description,
    file_description=file_description,
    data_usage=data_usage,
    read_cmd=read_cmd,
    cache_dir=str(results_root / "tusoai_cache"),
    data_instruction_count=data_instruction_count,
    clear=False,
    hints=gencode_data_hints,
    source_path="method.py",
    repo_root="method_repo",
)
```

Notes:

- `function_name` must match the placeholder exactly. Use `Class.method` only for class methods.
- `task_description` should be very short — a compact noun phrase like `"enhancer-gene linking"` or `"cell type deconvolution"`, not a full sentence.
- `source_path` and `repo_root` should point to the method repository file containing the placeholder if it is not in the runner; in the preferred sibling layout, use `repo_root="method_repo"` and `source_path="method.py"` from the runner/setup working directory.
- `file_description` describes the staged file, not the whole benchmark.
- `data_usage` states the specific derived signal the function should create.
- `hints` should forbid hidden-label use, network downloads, persistent writes, and output-schema changes.

## Recommended DataTask hints

Include hints like:

- `Use only the prepared data loaded by the read_cmd; do not download data or access the network.`
- `Use the absolute paths provided in the read_cmd if loading this data.`
- `Keep the function signature and output schema unchanged.`
- `Any useful comments should be minimal, must not mention TusoAI, and must live inside the function being optimized (not in the surrounding runner or repo).`
- `The baseline is a no-op, so it is acceptable to return the input unchanged if useful features cannot be created safely.`
- `Add compact deterministic features/priors only; avoid expensive model training or large joins inside the candidate function.`
- `Do not use validation/test labels, metric files, or hidden benchmark answers.`
- `Additional hyperparameters should be automatically estimated from the data when possible.`

## Validation checklist

Before launching optimization:

1. Run the placeholder method baseline and confirm the metric is unchanged or valid.
2. Run the `read_cmd` alone and save its output/log under `/mnt/results/data_tasks/<source_name>/`.
3. Confirm the `read_cmd` uses absolute paths to staged files under `/mnt/results/`.
4. Confirm the runner imports/calls the placeholder in the correct method path by resolving the sibling `method_repo` relative to `runner.py`, not by hard-coding an absolute path.
5. Create the DataTask and inspect the generated data summary/instructions for leakage or huge raw-data dumps.
6. Run one short TusoAI/evaluator smoke test to confirm candidate replacement can edit the placeholder function.
