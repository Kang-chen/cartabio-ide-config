# Long-horizon orchestration and memory

Use this reference whenever a run may span multiple Biomni turns, sandboxes, workers, or
long-running calls. The state files, not conversational memory, are authoritative.

## Contents

1. Durable directory contract
2. State and journal contract
3. Cold-start resume protocol
4. Checkpoint protocol
5. Invalidation and branching rules
6. Background work and lifecycle safety
7. Failure handling
8. Progress messages
9. Completion gate

## 1. Durable directory contract

Use a de-identified run ID. Never place a patient name, medical-record number, date of birth, or
other direct identifier in a directory, log, command line, report title, or progress message.

Keep durable working state under:

```text
/mnt/shared-workspace/neoantigen-prioritization/<run-id>/
├── state.json                 # canonical machine-readable state
├── plan.md                    # human-readable frozen plan
├── journal.jsonl              # append-only phase and recovery events
├── preflight.json             # environment and input checks
├── logs/                      # stdout/stderr from long commands
├── outputs/                   # analysis JSON and CSVs
├── benchmark/                 # optional refreshed benchmark
├── figures/                   # run-specific plots
└── report/                    # report before handoff
```

Publish compact status and final user-facing artifacts under:

```text
/mnt/results/neoantigen-prioritization/<run-id>/
```

Treat `/workspace` and other session-local paths as scratch. If a tool requires local random
access, copy the durable input into scratch, run the tool, and copy the completed artifact back to
the durable run directory immediately. Do not make an ephemeral path the only copy of an input,
configuration, model-independent intermediate, or result.

Keep the skill source read-only during a scientific run. Do not mix outputs into the skill folder.

## 2. State and journal contract

Use `scripts/run_state.py` for all state transitions. It writes `state.json` atomically, appends
events to `journal.jsonl`, fingerprints inputs and artifacts, and publishes a privacy-reduced
`status.json` to the configured results directory.

`state.json` is authoritative for:

- the de-identified case and run IDs;
- the frozen input fingerprints and configuration hash;
- current and completed phase states;
- the exact next action;
- attempts, failure details, and recovery state;
- artifact paths and SHA-256 values;
- terminal status.

`plan.md` is the human-readable phase map and invariant checklist. Add concise decisions to it if
the user resolves an ambiguity, but do not make its checkbox state authoritative; phase status
lives in `state.json`.

`journal.jsonl` is append-only. Record meaningful decisions and failure recovery. Do not paste
large command output into it. Store command output in `logs/` and journal the log path.

For files at or below 256 MiB, the state helper hashes the full file by default. Larger inputs use
an explicit metadata fingerprint to avoid repeatedly hashing multi-gigabyte BAMs. For a large
clinical input, prefer `--hash-mode full` when time and storage permit; otherwise preserve its
size, modification time, index, upstream accession, and acquisition checksum in `plan.md`.

## 3. Cold-start resume protocol

Run this protocol before asking intake questions or launching any computation:

1. Identify the requested run ID or inspect the case's durable run directory.
2. If no `state.json` exists, initialize a new run.
3. If `state.json` exists, run:

   ```bash
   python3 scripts/run_state.py verify --run-dir <durable-run-directory>
   python3 scripts/run_state.py status --run-dir <durable-run-directory> --full
   ```

4. Read `plan.md`, the full state, and only the journal tail needed to understand the current
   phase. Do not reload every historical log into model context.
5. If verification succeeds, resume exactly from `next_action`.
6. If the current phase is `in_progress`, inspect the named output/log before rerunning anything.
   A tool-managed background job may have completed while the agent was inactive.
7. If verification detects drift, stop reuse of downstream artifacts and apply the invalidation
   rules below.
8. Never rerun a completed phase merely because the conversation is new or memory is compacted.

The one-line resume message is:

```text
Resuming <run-id> at <phase> | verified inputs/artifacts | next: <next_action>
```

## 4. Checkpoint protocol

Begin every phase before its first mutating or expensive command:

```bash
python3 scripts/run_state.py begin \
  --run-dir <durable-run-directory> \
  <phase> \
  --next-action "<specific executable next step>"
```

Checkpoint within a long phase after a meaningful substep, before a background call, after a
callback, and before any operation that could lose context:

```bash
python3 scripts/run_state.py note \
  --run-dir <durable-run-directory> \
  --message "<what just completed and where its log lives>" \
  --next-action "<single concrete next step>"
```

Complete a phase only after its gate has passed. Fingerprint every gate artifact:

```bash
python3 scripts/run_state.py complete \
  --run-dir <durable-run-directory> \
  <phase> \
  --artifact <name>=<path> \
  --message "<gate evidence>" \
  --next-action "Begin <next-phase>"
```

After a phase-completion progress message, continue to the next phase in the same turn whenever
the environment is usable. A progress message is a heartbeat, not a request for permission.

## 5. Invalidation and branching rules

Never silently accept changed inputs or configuration.

- If an input fingerprint changes before prioritization begins, update the frozen configuration
  only with explicit user intent, then initialize a new run ID.
- If an input or configuration changes after prioritization begins, initialize a new run ID. Keep
  the old run immutable for auditability.
- If only a presentation setting changes, such as the report title or figure destination, preserve
  the analysis run and create a clearly named reporting branch in `plan.md`; do not overwrite the
  original report artifact.
- If a completed artifact is missing or its digest changes, mark it untrusted. Recreate it from the
  latest still-verified upstream phase, record the reason, and give the replacement a new artifact
  name. Do not rewrite history in `journal.jsonl`.
- If the MHCflurry model version changes, treat binding and every downstream score as invalid.
- If only the optional benchmark fixture changes, invalidate visualization/reporting components
  that consume it, not the patient analysis outputs.

The state helper intentionally does not offer an `invalidate` or overwrite command. Create a new
run for scientific-input drift; this makes accidental reuse harder.

## 6. Background work and lifecycle safety

Use the platform's managed background execution for any call likely to approach a foreground
timeout. Never use shell `&`, `nohup`, detached subprocesses, or an unmanaged process.

Before launching a long call:

1. checkpoint the exact command and log path with `run_state.py note`;
2. direct logs to the durable run directory;
3. use the run ID and phase in the background-job name;
4. request a realistic timeout and worker size;
5. ensure the output target is durable.

On callback, inspect exit status and artifacts before marking the step complete. If a long-running
job remains active, report that fact and continue through the platform callback mechanism; do not
poll in a tight loop.

Use CPU/memory evidence from preflight to size a worker. Do not provision a GPU solely because an
ML library is installed. Use a GPU only when the selected MHCflurry installation is explicitly
GPU-enabled and the environment exposes a supported accelerator.

Assume the sandbox can disappear between any two calls. A substep that exists only in model
memory has not been checkpointed.

## 7. Failure handling

Classify failures before acting:

- **Transient infrastructure:** network timeout, worker loss, temporary service error. Preserve
  logs, retry the same scoped step up to three times with backoff or a repaired environment.
- **Deterministic environment:** missing MHCflurry, missing models, unsupported Python package,
  unreadable output directory. Repair in preflight, rerun the preflight gate, then continue.
- **Scientific input:** missing HLA-I, ambiguous genome build, unusable VCF annotation, unsupported
  HLA alleles leaving no scored candidates, mismatched BAM/index. Mark blocked and request the
  specific missing decision or file.
- **Scientific invariant:** fabricated value path, non-MHCflurry binding source, imputation of a
  missing measurement, inconsistent candidate counts. Fail closed; do not publish numbers.

Record a recoverable failure:

```bash
python3 scripts/run_state.py fail \
  --run-dir <durable-run-directory> \
  <phase> \
  --error "<concise diagnosis>" \
  --next-action "<repair and verification step>"
```

Add `--blocked` only when progress requires user input, new authority, or an external state change.
After repair, call `begin` on the same phase; the attempt counter preserves the history.

Do not convert a hard scientific failure into a demo run unless the user explicitly asked for the
demo. Do not treat a partial CSV, stale report, or fixture output as patient output.

## 8. Progress messages

Report at phase boundaries and approximately every 10 minutes during long foreground work:

```text
Phase <N>/7 | <phase> | <completed evidence> | next: <next action>
```

For failures:

```text
Phase <N>/7 | <phase> | retry <attempt>/3 or BLOCKED | <diagnosis> | next: <repair>
```

Keep messages free of direct identifiers and raw variant details. Use the de-identified run ID.
The next action after a nonterminal heartbeat is a tool call that advances the persisted plan, not
a request to continue.

## 9. Completion gate

Mark the run complete only when all seven phases are `completed`, `run_state.py verify` succeeds,
and the handoff directory contains the promised artifacts. Completion requires:

- frozen input/config provenance;
- MHCflurry named as the binding engine;
- internally consistent candidate and tier counts;
- complete CSV and JSON exports;
- run-specific figures when requested;
- a readable PDF when requested;
- a concise limitations and missingness summary;
- a final user-visible `status.json` with `status: complete`.

If a requested optional artifact cannot be produced, either repair it or explicitly renegotiate
the deliverable with the user. Do not mark the original plan complete while silently omitting it.
