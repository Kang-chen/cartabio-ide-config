# DataTasks and durable benchmark/data staging

Use a DataTask when a narrow target function can benefit from a prepared external
or heterogeneous data source that supplies orthogonal signal. Do not create a
DataTask merely to describe the existing training matrix; MethodTask
`data_available` is sufficient for that.

Examples include genomic annotations, eQTL/chromatin/conservation priors, pathway
or perturbation embeddings, protein structural/property tables, literature
derived features, or other stable resources that can be joined without hidden
validation labels.

## Core invariants

- No hidden validation/test labels, answer keys, evaluator outputs, or future
  information may be staged or exposed.
- All data paths used by candidates are absolute paths visible identically from
  every cluster node.
- Downloads, preprocessing, indexing, embedding generation, and network calls
  happen before optimization. Candidate functions only read prepared artifacts.
- The original method remains valid when the DataTask placeholder is a no-op.
- Row/sample identity, order, shape, and schema are asserted by the evaluator.
- Large data and indexes live in `/mnt/shared-workspace`; small user-visible
  manifests/samples may be mirrored to `/mnt/results`.
- Data licenses, access controls, and privacy constraints are recorded.

## 1. Ask what orthogonal signal could help

Before downloading externally, search Biomni's registered resources, datalake,
functions, and databases for task-relevant sources. Prefer mounted or registered
resources because they are faster, reproducible, and survive sandbox recreation.

For each proposed source evaluate:

- biological/mechanistic relevance;
- whether it adds information not already present;
- key/join coverage and missingness;
- leakage or temporal contamination risk;
- license/access/privacy constraints;
- size, preprocessing cost, and per-candidate read cost;
- whether a compact prepared representation is possible.

Prefer a few high-signal sources over many weak, redundant files.

## 2. Stage data durably and reproducibly

Use:

```
/mnt/shared-workspace/tusoai/<task_id>/data/<source_name>/
├── source_notes.md
├── manifest.json
├── raw/
├── processed/
├── sample/
├── prepare.py or prepare.sh
└── validation.json
```

Record source URLs/accessions, checksums, timestamps, licenses, transformations,
filters, join keys, row counts, schema, and leakage review. Make preparation
idempotent and resumable. Never broadly overwrite existing data; version changed
processing outputs.

When a format requires random-access writes that the shared mount cannot support,
build it under `/workspace/`, close and validate it, then copy the completed file
to the shared directory immediately.

## 3. Prepare for cheap candidate reads

Candidate evaluation may run thousands of times. Convert expensive raw data into
a representation that minimizes repeated work:

- indexed Parquet/Arrow/NPY/NPZ or a compact table keyed by stable IDs;
- precomputed embeddings, priors, aggregates, or lookup dictionaries;
- memory-mappable arrays where the shared filesystem supports them;
- a small, deterministic read path with bounded memory;
- no download, remote query, model inference, or large preprocessing inside the
  candidate function.

Cache validity must be tied to checksums and preprocessing version. Avoid one
shared mutable sqlite/HDF5 writer during concurrent evaluation.

## 4. Add a true no-op placeholder at the correct call site

Before `create_data_subtask`, add a uniquely named function in the editable
method repository and call it where the extra information can affect the method.
The baseline placeholder must:

- preserve the original output exactly;
- perform no data read or network call;
- make no persistent write;
- accept enough context for a useful future implementation;
- return a neutral object or unchanged value;
- be reachable by the evaluator.

Example:

```python
def add_pathway_priors(features, gene_ids):
    """TusoAI DataTask placeholder; baseline is an exact no-op."""
    return features
```

Verify both baseline equivalence and candidate reachability before task
construction.

## 5. Validate a minimal absolute-path read command

The DataTask `read_cmd` executes during task construction to summarize data. It
must use an absolute shared path and return a compact object/schema rather than
printing or materializing the whole dataset.

Example:

```python
import pandas as pd
annotations = pd.read_parquet(
    "/mnt/shared-workspace/tusoai/<task_id>/data/pathways/processed/gene_features.parquet"
)
annotations = annotations.head(2000)
```

Run the command in the same persistent environment on at least two proposed
nodes. Record runtime, peak RSS, schema, row count, key uniqueness, missingness,
and checksum. Do not create a DataTask from an untested command.

## 6. Construct a narrow DataTask

Provide:

- `function_name`: exact no-op placeholder;
- `task_description`: same global objective and metric;
- `file_description`: what the prepared source contains, key columns, units,
  coverage, missingness, and trust boundaries;
- `data_usage`: one concrete role such as generating numerical features,
  embeddings, priors, or calibrated scores;
- `read_cmd`: deterministic absolute-path preview;
- `hints`: join-key rules, shape/order preservation, missing-data behavior,
  prohibited leakage, runtime/memory limits, and allowed dependencies;
- `source_path` and `repo_root`: the real editable file.

Do not tell TusoAI to perform broad exploratory analysis or ingest arbitrary raw
files in the candidate function. The data source and integration point should be
specific.

## 7. Include DataTasks in the one shared task bundle

Build DataTasks only on the coordinator and serialize them with MethodTasks into
`task_bundle.pkl`. Followers load the exact objects; they do not re-preview data
or regenerate instructions.

Fingerprint every referenced data file and include the checksums in the task
spec or bundle manifest. A follower with missing or different data is not allowed
to join the shared history.

## 8. DataTask launch gate

Advance only when:

- source relevance and leakage review are documented;
- raw/processed manifests and checksums exist;
- every node sees identical absolute files;
- the read command is deterministic, compact, and tested;
- the placeholder is a baseline-preserving no-op and evaluator-reachable;
- expensive work is outside candidate evaluation;
- evaluator assertions protect row identity/schema and hidden labels;
- the task is included in the single hashed task bundle.
