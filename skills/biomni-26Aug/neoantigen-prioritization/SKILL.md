---
name: neoantigen-prioritization
category: "drug_discovery"
description: >-
  Run a real-data-only, TESLA-guided neoantigen prioritization workflow from somatic VCF,
  patient HLA-I genotype, and optional RNA expression/RNA BAM to a ranked per-peptide Tier 1/2/3
  validation shortlist, complete CSV/JSON exports, figures, benchmark context, and a Biomni PDF.
  Use for personalized-vaccine or TCR-discovery candidate selection, especially when missense and
  indel/frameshift neoORF peptides, MHCflurry binding, expression-aware abundance, the seven TESLA
  presentation/recognition features, durable long-horizon execution, checkpointing, cold-start
  resume, context recovery, or auditable Biomni handoff are required. Also use to run or validate
  the bundled real Pt22 melanoma demo. Do not use for a patient-level checkpoint-response score;
  use neoantigen-io-response for that question.
starting-prompt: "Run this skill on its bundled Pt22 melanoma demo data (Hugo 2016, GSE78220): tier the peptides, benchmark score-ordering against the public TESLA table, and generate the PDF report + infographics."
---

# TESLA-guided neoantigen prioritization

Produce an auditable shortlist of individual tumor neoantigens for experimental validation. Run
the scientific pipeline as a persisted seven-phase state machine so it survives context compaction,
sandbox loss, long background calls, and multi-turn execution without abandoning or silently
changing the plan.

## Required reading

- Read [references/orchestration.md](references/orchestration.md) before every managed real-data or
  long-running execution. Follow its persistence, resume, invalidation, lifecycle, and privacy
  rules.
- Read [references/operations.md](references/operations.md) before running commands. Use its exact
  gates, CLI forms, demo command, and failure lookup.
- Read [references/tesla-methods.md](references/tesla-methods.md) before validating results,
  interpreting features, changing scientific behavior, refreshing the benchmark, or writing the
  report. Treat its feature definitions and caveats as authoritative.

Do not load the detailed methods merely to locate a command; use progressive disclosure.

## Scientific contract

Enforce these invariants throughout the run:

1. Use real somatic variants and a real patient HLA-I genotype. Never invent either.
2. Produce peptide-MHC-I binding only with MHCflurry on the supplied peptides and alleles. Require
   MHCflurry and its class-I presentation models. On absence or unusable required inputs, raise or
   preserve `EngineUnavailable` and emit no binding, tier, or priority numbers.
3. Validate wild-type residues before applying missense substitutions. Drop mismatches and report
   their count; never force-apply them.
4. Generate indel/frameshift neoORFs only from real annotated transcript/CDS context. Never infer a
   novel sequence without that evidence.
5. Preserve missing expression, VAF, stability, mutation-position, agretopicity, and foreignness as
   null. Never convert missing to zero, a population average, a representative value, or a
   synthetic fallback.
6. Renormalize the composite over available features exactly as implemented. Do not lower tier
   thresholds, tune weights to the case, or change scientific semantics during a run.
7. Treat Tier 1 and Tier 2 as experimental validation priorities, not proof of immunogenicity or a
   clinical treatment recommendation.
8. Distinguish case-specific results from the bundled TESLA benchmark, method diagrams, Pt22 demo,
   and synthetic code-path fixture.

Fail closed if any invariant is violated.

## Pipeline outcome

Transform:

```text
somatic VCF + HLA-I + optional expression/RNA BAM
  -> validated missense and indel/frameshift variants
  -> mutant 8–11mer peptides and matched WT peptides where defined
  -> MHCflurry binding against the patient's alleles
  -> expression/VAF joins
  -> seven TESLA presentation and recognition features
  -> composite priority and Tier 1/2/3/excluded calls
  -> validated CSV/JSON, figures, PDF, provenance, and durable run state
```

Use the companion `neoantigen-io-response` instead when the requested outcome is one patient-level
IO-response composite rather than a peptide shortlist.

## Long-horizon execution contract

Treat files as memory and conversation as a view onto that memory.

Store the authoritative run under:

```text
/mnt/shared-workspace/neoantigen-prioritization/<de-identified-run-id>/
```

Publish compact status and final deliverables under:

```text
/mnt/results/neoantigen-prioritization/<de-identified-run-id>/
```

Use `scripts/run_state.py` to create and advance:

- `state.json`: canonical phase, next action, fingerprints, attempts, errors, and artifacts;
- `plan.md`: frozen plan, decisions, and scientific invariants;
- `journal.jsonl`: append-only decisions, retries, and phase transitions;
- user-visible `status.json`: privacy-reduced best-so-far status.

Never rely on an internal to-do list as the only copy of the plan. Never store direct patient
identifiers in run IDs, paths, logs, progress messages, or reports.

### Resume before doing new work

On every invocation:

1. Check for an existing `state.json` before asking questions or rerunning setup.
2. Run `run_state.py verify`, then read full state, `plan.md`, and the relevant journal tail.
3. If fingerprints pass, continue exactly from `next_action`.
4. If the current phase is in progress, inspect its job/log/artifacts before restarting it.
5. Do not repeat a completed phase because the session is new or context was compacted.
6. If scientific input, configuration, or MHCflurry model state changed, preserve the old run and
   initialize a new run ID. Never overwrite a prior scientific run.

### Continue until a terminal condition

After each nonterminal phase heartbeat, make the next tool call advance the persisted plan. Do not
ask whether to continue merely because one phase succeeded, one attempt failed, the output looks
promising, or context is becoming long.

Stop early only for:

- an explicit user interruption;
- a platform hard stop, after checkpointing when possible;
- a genuine external block requiring user input, new authority, or changed external state;
- a scientific invariant failure that cannot legally be repaired;
- an explicit user time/resource budget, while preserving active best-so-far state.

Mark `complete` only after the Phase 6 handoff gate. An incomplete but resumable run remains active;
do not call it complete.

## Resolve inputs without stalling

For a real case, resolve and freeze:

- somatic VCF path and annotation mode (`CSQ`/`ANN` or VEP REST);
- genome build, GRCh37/hg19 or GRCh38/hg38;
- real four-digit HLA-A, HLA-B, and HLA-C alleles, or an upstream typing requirement;
- optional expression path, identifier column, value column, and TPM/RPKM units;
- optional tumor RNA BAM plus index;
- inclusion of indels/frameshift neoORFs, default yes;
- de-identified case/run ID;
- requested CSV/JSON, figures, report, benchmark refresh, and resource/time limits;
- confirmation that a peptide validation shortlist is the intended decision output.

Ask only unresolved blocking questions. Do not re-ask facts already supplied. A somatic VCF, known
genome build, and real HLA-I are blocking for a patient run. Expression and RNA BAM are optional;
document the evidence lost when absent.

If the user requests a demonstration and supplies no case data, run the real Pt22 demo. Never
present `assets/demo_somatic.vcf` as a patient; it is a labelled synthetic code-path fixture for
indel and filtering tests only.

## Execute the seven phases

Begin, checkpoint, and complete every phase with `scripts/run_state.py`. Store command logs and
gate artifacts under the durable run directory.

### Phase 0 — intake and frozen plan

Resolve missing decisions, de-identify the run, freeze `config.json`, fingerprint supplied inputs,
and initialize state. Record the exact deliverables and any budget.

Gate:

- `state.json`, `plan.md`, `journal.jsonl`, and `config.json` exist;
- the results directory contains privacy-reduced `status.json`;
- required questions are resolved;
- configuration and input fingerprints are frozen.

### Phase 1 — environment and input preflight

Validate file readability, VCF build/annotation, HLA normalization, optional expression/BAM schema,
disk capacity, dependencies, MHCflurry import, and downloaded class-I presentation models. Run the
engine-independent smoke suite. Use managed background execution for long calls; never use `nohup`,
shell `&`, or a detached subprocess.

Gate:

- `preflight.json` has `ok: true` and exact versions/commands;
- MHCflurry and real inputs are usable;
- the smoke suite passes;
- the expected prioritization command is recorded.

### Phase 2 — real prioritization

Run `scripts/neoantigen_tesla.py` from the skill root with frozen inputs. Write outputs and logs to
the durable run directory. Keep optional flags absent when their inputs are absent. Do not continue
from a partial export after `EngineUnavailable`.

Gate:

- `neoantigens.csv`, `prioritized_neoantigens.csv`, `summary.csv`, and `analysis.json` exist;
- the analysis engine names MHCflurry;
- at least one real candidate was scored;
- the durable command log has a successful exit status.

### Phase 3 — scientific and structural validation

Validate candidate/tier counts, schemas, HLA/rank presence, null preservation, configuration,
provenance, and output digests. Run the full static suite after any environment or skill-code change.
Refresh the real TESLA benchmark only when requested or when model/feature code changed; otherwise
record use of the bundled validated fixture.

Gate:

- `validation.json` records every assertion and evidence;
- candidate row count equals `n_candidates` and the tier-count sum;
- prioritized rows contain only Tier 1 or Tier 2;
- no missing measurement was imputed;
- no unsupported or fabricated binding value reached an output.

### Phase 4 — visualization and benchmark context

Generate the four run-specific data figures in PNG and SVG. Keep benchmark plots labelled as
benchmark context. Treat shipped infographics as schematics and do not transfer their Pt22 numbers
to a new case. Inspect figures before accepting them.

Gate:

- expected figure files exist and are readable;
- `visualization_manifest.json` links each figure to source digests;
- captions distinguish patient results, benchmark evidence, and schematic context;
- visual QA finds no clipping, missing legends, or identifiers.

If the user explicitly declined figures, create and validate a skip manifest with that decision;
do not silently omit the phase.

### Phase 5 — report

Build the Biomni PDF from validated outputs and figures. Identify optional missing evidence and
composite renormalization. State benchmark limitations and avoid clinical claims. Use only the
de-identified case ID in report metadata.

Gate:

- the PDF is readable and visually inspected;
- `report_manifest.json` records input digests, page count, size, and QA result;
- the report clearly distinguishes case-specific measurements from reference/demo content.

If the user explicitly declined a PDF, create and validate a skip manifest with that decision.

### Phase 6 — final QA and handoff

Run state verification, copy promised artifacts to the results directory, write `run_summary.md`,
fingerprint the copied files, and complete `handoff` in the state machine.

Gate:

- all earlier phases are complete;
- input and artifact verification passes;
- every promised artifact exists in the results directory;
- `run_summary.md` includes inputs/provenance, engine, counts, missingness, limitations, exact
  reproduction commands, and next experimental steps;
- `status.json` says `complete` only after final artifact fingerprints are recorded.

## Failure and retry rules

Diagnose before retrying.

- Retry a transient network, worker, or service failure up to three scoped attempts. Preserve each
  log and checkpoint the repair.
- Repair deterministic environment failures in preflight, then rerun the gate.
- Mark blocked when the VCF/build/HLA or required user decision is missing, or when all real alleles
  and peptides are unscorable.
- Fail closed on fabrication, imputation, inconsistent counts, or non-MHCflurry binding.
- Resume a repaired phase with `run_state.py begin`; retain its attempt history.

Do not switch to the demo, relax scientific gates, change weights, or substitute remembered outputs
to escape a failure.

## Progress protocol

Report at phase boundaries and about every ten minutes during long work:

```text
Phase N/7 | phase | completed evidence | next: concrete action
```

For a retry or block:

```text
Phase N/7 | phase | retry A/3 or BLOCKED | diagnosis | next: repair/input needed
```

Keep the message concise, de-identified, and consistent with `state.json`. Treat it as a heartbeat;
continue the run immediately unless a terminal condition applies.

## Output contract

Produce these analysis artifacts for every completed scientific run:

- `neoantigens.csv`: all scored peptides and provenance fields;
- `prioritized_neoantigens.csv`: Tier 1 and Tier 2 candidates;
- `summary.csv`: per-tier counts;
- `analysis.json`: full structured analysis and provenance.

Produce when included in the frozen deliverables:

- four run-specific data figures as PNG and SVG;
- a de-identified Biomni PDF report;
- refreshed real-TESLA benchmark artifacts;
- user-visible `run_summary.md` and `status.json`.

Never claim that a PDF or figure exists until its file is present, fingerprinted, and validated.

## Start now

First perform the cold-start resume check. If no compatible run exists, resolve the minimum
blocking inputs, initialize durable state, and execute Phase 0 through Phase 6 in order. Continue
from one gate to the next without abandoning the persisted plan.
