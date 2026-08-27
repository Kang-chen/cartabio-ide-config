---
id: "skill_bed70a7f98864bd48324eb9295c4fdd0"
name: "literature-deep-review"
description: "Use for audit-ready biomedical reviews that require exact quotations, stable sentence or figure locators, claim-evidence tables, contradiction checks, or verifiable figure evidence. Retrieves full text, independently verifies anchors and reuse permissions, and delivers Markdown, machine-readable evidence, and a verified PDF; not for ordinary narrative overviews."
category: "literature"
visibility: "public"
starting-prompt: "Run a grounded evidence review of GRN as a therapeutic target in frontotemporal dementia, with exact quotations and resolvable locators behind every delivered claim."
---

# Literature Deep Review

## When to Use This Skill

Use this skill only when the user explicitly needs one or more of:

- exact source sentences, page/section/figure locators, or figure-level evidence;
- an audit-ready claim-to-evidence table;
- a contradiction or null-result hunt;
- a scoped mechanism, direction-of-effect, or target-evidence decision;
- a comprehensive report whose claims must survive deterministic verification.

Use an ordinary literature skill for a narrative overview that does not require
sentence-level grounding. Never treat an abstract as full-text evidence.

This skill does NOT:

- perform quantitative meta-analysis;
- pool effect sizes across studies;
- enforce systematic-review protocol compliance (e.g., PRISMA registration or a
  pre-registered protocol).

Two invariants define a shippable result:

1. Every displayed claim has at least one exact, resolvable supporting or
   contradicting anchor.
2. Every displayed anchor has a within-agent blinded entailment verdict (the
   coordinator re-checks each anchor blind to its first-pass stance; this is not
   an independent third-party adjudication); a missing or rejected verdict fails
   delivery.

The canonical product is `evidence/evidence.jsonl`. Builders generate prose,
tables, crops, and reports from canonical artifacts; do not hand-edit evidence
into a report.

## Installation

Set the mounted skill root and install its pinned runtime:

```bash
LDR="$(pwd)"
bash "$LDR/scripts/install.sh"
```

The standard installer first imports EasyOCR and PyTorch and reuses Biomni's
working copies. It installs the EasyOCR runtime only when either import is
unavailable, then initializes the English OCR model so failure happens before a
long run.

`--with-ocr` remains accepted as a backward-compatible no-op. Add
`--with-marker-fallback` only when the default parsers fail. `qpdf` is optional;
when present, the structural gate runs `qpdf --check` in addition to strict
`pypdf` parsing.

| Type | Components | Purpose / licence note |
|---|---|---|
| Required Python | requests, NumPy, pypdfium2, pdfplumber, pySBD, Pillow, pypdf, ReportLab, Matplotlib | HTTP, parsing, segmentation, rendering, gates; permissive licences |
| Required OCR runtime | EasyOCR, PyTorch, English model weights | Figure text and provenance boxes; Apache/BSD families |
| Optional fallback | marker-pdf | Difficult PDFs; GPL-3.0, so verify deployment policy |
| Optional system | qpdf | Extra PDF integrity check; Apache-2.0 |
| Biomni tools | `LiteratureSearch`; `GenerateImage` for `deep`/`broad`; `ManageMachine`; `Bash` | Discovery, required visual abstract, and distributed deterministic processing |

Biomni performs every reasoning stage natively.

## Inputs

| Input | Required | Accepted form |
|---|---:|---|
| Review question and decision | yes | Text, with population/model, perturbation, outcome, date, and design scope |
| Literature records | yes | Biomni `LiteratureSearch` records ingested from `references.jsonl` |
| Claims | yes | `templates/claims.csv` or JSONL with stable `claim_id` and `claim_text` |
| Full text | conditional | Directly retrievable internet PDFs/repository copies, or user-supplied PDF paths |
| Review brief | recommended | `templates/review_brief.md` |
| Report settings | optional | `templates/run_manifest.json`; mode is `quick`, `deep`, or `broad` |

Any PDF returned to a fresh unauthenticated internet request may be read and
OCRed by default. The pipeline never supplies credentials or circumvents an
authentication challenge or technical control. User-supplied PDFs may also be
analyzed. A user may explicitly direct the report to include accessible source
figures even when recorded metadata does not establish reuse permission; label
those figures and never describe them as open or licensed. Reading/OCR access
and figure-reproduction permission remain separate decisions.

## Outputs

The run lives on local POSIX storage. Only checkpoints and final artifacts use
shared/object-backed mounts.

| Artifact | Role |
|---|---|
| `run_manifest.json` | Scope, configuration, models, timings, resume identity, and mirrored skill provenance |
| `state/skill_provenance.json` | Exact Git commit plus runtime-directory and managed-bundle SHA-256 identity |
| `corpus/references_snapshot.jsonl` | Frozen search slice |
| `corpus/references.jsonl` / `claims.jsonl` | Canonical publication metadata and atomic claim inventory |
| `corpus/corpus_ledger.json` | Canonical paper flow from discovery through figure production |
| `corpus/coverage_matrix.json` | Broad-review queries/status for every required decision axis |
| `fulltext/global_transient_retry.json` | Post-merge transient recovery disposition |
| `fulltext/acquisition_routes.jsonl` / `parse_quality.jsonl` | Per-paper retrieval route, recovery attempts, and usable/low-quality/figure-only/unusable parse state |
| `fulltext/blocks.jsonl` | Located source text/caption/OCR blocks |
| `evidence/adjudications.jsonl` / `evidence_lineage.jsonl` | Raw decisions and an accepted/rejected/duplicate disposition for every decision |
| `evidence/evidence.jsonl` | Canonical accepted evidence |
| `evidence/entailment.jsonl` | Within-agent blinded entailment verdict for every displayed anchor (coordinator re-check blind to first-pass stance; not an independent third-party adjudication) |
| `deliverables/evidence_table.csv` | Human-readable evidence audit table |
| `deliverables/claim_evidence_matrix.csv` | Derived support state per claim |
| `deliverables/grounded_quotes.json` / `.md` | Exact displayed anchors |
| `deliverables/infographic_spec.json` | Evidence-backed authored content for the opening infographic |
| `state/infographic_generate_image_request.json` | Exact Biomni `GenerateImage` request and Phylo prompt |
| `state/infographic_generation.json` | Request, prompt, and installed-image hashes |
| `state/infographic_media_check.json` | Biomni `Read(media_output_check)` result tied to the final image hash |
| `deliverables/review.md` | Deterministically built review |
| `deliverables/report.pdf` | Canonical verified PDF |
| `/mnt/results/<prompt-derived-title>-literature-review.pdf` | User-visible PDF in the Results root |
| `state/delivery.json` / `delivery_receipt.json` | Source/destination hashes and final attestation |
| `state/assemblies/*.json` | Exact native task/input/output hashes |
| `state/final_reconciliation.json` | Canonical cross-artifact counts, hashes, and delivery disposition |
| `state/quality_summary.json` | Compact parse, acquisition, adjudication, figure, infographic, and execution QA summary |

Report deliverable: Use the pdf-report-generation skill to generate a pdf report
with infographics (use the Biomni GenerateImage tool), methods, results,
conclusions, figures, references, and next steps from all of the analyses.

`deliverables/claim_narratives.jsonl` is required for `deep` and `broad`.
Produce its independent per-claim tasks with native Biomni reasoning in the
coordinator, then assemble them deterministically. Prod does not expose
general-purpose subagents; do not ask for them and do not describe their absence
as a disabled profile.

## Clarification Questions

Ask together and only for missing information.

1. **Input files first:** Do you have specific PDFs, a citation list, or a prior
   claim table to use? Accepted formats are PDF, CSV, JSONL, PMID, PMCID, and
   DOI. Otherwise use `LiteratureSearch`; the starting prompt is the built-in
   demonstration topic.
2. **Depth and paper-count ballpark:** Choose `quick`, `deep`, or `broad`, then
   ask which planning preference the user wants: all relevant papers available;
   approximately N full texts (accept a number or range); or decide after seeing
   search availability. `quick` and `deep` use starting caps of 5 and 15 only
   when the user delegates this choice; `broad` has no built-in paper cap. Treat
   a ballpark as a planning preference, not an exact ceiling. After search, show
   the relevant unique-paper count and ask the user to confirm all papers or an
   exact maximum. Never describe broad mode as having a fixed maximum.
3. **Decision and scope:** What decision will this inform, and what
   populations/species/models, interventions, outcomes, dates, and designs are
   in or out?
4. **Access:** Use directly accessible internet PDFs plus supplied PDFs by
   default. If the user explicitly requests open-access-only sources, filter the
   paper set before acquisition. Processing stays in Biomni.
5. **Paper figures (three separate choices):** First ask what figure density the
   report should contain. Offer none/text-only; concise (usually 1-3; floor 1);
   standard (usually 4-8; floor 4); comprehensive (usually 8-15+; floor 8);
   adaptive to the retrieved corpus and populated evidence axes (recommended
   for `deep`/`broad`, with no fixed cap); or a custom exact minimum. State that
   these are floors or expected bands, never maximums. Additional figures must
   add material, nonredundant information. Record `config.figure_count_policy`
   as `fixed` or `adaptive`. For a fixed choice, record the floor immediately
   as `config.minimum_paper_figures`. For adaptive, initially record that field
   as null. After full-text and figure-candidate inventory, resolve it by running:

   ```bash
   python scripts/intake_policy.py --manifest RUN/run_manifest.json \
     --resolve-adaptive --full-text-papers N --populated-axes N \
     --eligible-figures N
   ```

   The resolver records the full-text, populated-axis, and eligible-supply inputs,
   then uses the largest of the mode baseline (`quick=1`, `deep=4`, `broad=6`),
   one figure per populated evidence axis, and one per five retrieved full texts,
   limited by eligible supply. This is a minimum, not a stopping rule; continue
   selecting eligible, nonredundant figures after meeting it.
   If figures are requested or the policy is adaptive, ALWAYS also ask how to
   inspect text inside those figures: targeted OCR of figures implicated by
   relevant captions
   (recommended), captions only with no OCR, or OCR of all eligible figures
   (slow/noisy). Do not infer caption-only processing from install size,
   runtime, background execution, or a missing answer. Record the processing
   choice as `config.ocr`, and its origin as `config.ocr_decision_source`
   (`explicit_user` or `delegated_default`). With no paper figures, record
   `ocr=off` and `ocr_decision_source=no_figures`. The generated visual abstract is separate
   and does not answer this paper-figure OCR question. Finally, whenever the
   policy is adaptive or the fixed minimum is positive, ask whether to include
   only figures with recorded reuse clearance or to include accessible figures
   at the user's direction even when reuse rights are unknown/restricted. The
   latter is a normal supported user
   choice, not an organizational exception. Record `config.figure_reuse_policy`
   as `reuse_cleared_only` or `user_directed` and record
   `config.figure_reuse_decision_source`. Never imply that user direction creates
   a licence; disclose the recorded rights status in each affected caption.
6. **Presentation:** The standard package includes Markdown, evidence files, and
   the verified PDF. `deep`/`broad` also require the visual abstract; for
   `quick`, ask whether to include one.

If the user delegates choices, use `quick`, directly accessible internet and
repository full text, native adjudication, and the standard output package. Use
no paper figures for delegated `quick`. For delegated `deep`/`broad`, use the
adaptive figure policy and resolve its corpus-scaled minimum after inventory;
never substitute a fixed four-figure default. Resolve delegated OCR to `off`
only for the no-figure quick choice, and to `targeted` for adaptive or positive
fixed choices. Caption-only/no-OCR processing with
requested paper figures is valid only after the user explicitly selects it;
never silently label an unanswered OCR question as delegated. The delegated
reuse policy is `reuse_cleared_only`; `user_directed` requires the user's
explicit choice. Before installing dependencies or launching acquisition,
validate the recorded choices:

```bash
python "$LDR/scripts/intake_policy.py" --manifest "$RUN/run_manifest.json"
```

For the demonstration topic, ask only structured choices after question 1.

## Standard Workflow

### Background execution is the default

Every deterministic shell stage that may run longer than the foreground limit
MUST be submitted through Biomni `Bash` with `run_in_background=true` and a
stable `background_name`. For broad reviews, distribute acquisition, parsing,
and figure OCR across up to five `ManageMachine` machines. One tracked
background `Bash` job per machine adaptively pilots 2, 3, 4, 6, 8, 12, and up
to 16 Python processes. It keeps the last faster, healthy level and stops
ramping when throughput plateaus, retrieval errors increase, or measured CPU or
memory pressure makes the next level unsafe. The pilot papers count toward the
review; this is the default execution mode, not an optional tuning suggestion.
Native adjudication, entailment, and narrative authoring remain in the Biomni
coordinator on Prod, with independent tasks packed into bounded coordinator
turns. For example:

```text
Bash(command='<the evidence_first.py command>', run_in_background=true,
     background_name='literature-review-<run-id>-evidence')
```

Use Biomni's tracked background jobs, never shell `&`, `nohup`, or a polling
loop. Wait for completion callbacks; call `BashOutput` only after the callback
when the final log is needed. One named job on each distinct machine owns its
local adaptive process pool. Keep search decisions, native reasoning, short
assembly, and gates in the coordinator.

Broad mode always uses this managed path unless the user explicitly approves a
platform-availability exception. Record it only as
`config.managed_execution_waiver={"approved_by_user":true,"reason":"..."}`;
a plain string, convenience, or speed preference is not a waiver. Deep runs
with at least 12 selected papers use the same rule. Final reconciliation checks
the adaptive machine count, object-store courier, completed-machine inventory,
and exact skill bundle used by the workers.

### Step 1 — Plan, search, and freeze the corpus

Create one run identity. Do not create a sibling run after context loss:

```bash
RUN="/workspace/literature-deep-review/<run-id>"
CHECKPOINT="/mnt/shared-workspace/shared/literature-deep-review/<run-id>"
NATIVE_EXCHANGE="$RUN/state/native_exchange"
RESULTS_ROOT="/mnt/results"
RESULTS="/mnt/results/literature-deep-review/<run-id>"
```

Write a concise, subject-specific `run_manifest.json.title` from the user's
prompt; never leave a generic or placeholder title. Delivery sanitizes that
title into the visible PDF filename and falls back to the recorded question.
Record `subject`, `subject_long`, and every target/topic synonym used in search
as `subject_aliases`. Figure selection uses these names as a direct-grounding
gate: generic outcome overlap is not enough to attach a panel to the target.

Immediately after creating `run_manifest.json`, bind the run to the installed
skill. This resolves the exact local Git package when available and otherwise
uses the commit metadata stamped by the Git-managed Biomni deployer. An ad-hoc
package must pass `--git-commit <40-character-SHA>` or set
`LITERATURE_REVIEW_SKILL_GIT_COMMIT`. Failure is an intake blocker, not a
warning:

```bash
python "$LDR/scripts/skill_provenance.py" --run-root "$RUN" --skill-root "$LDR"
python "$LDR/scripts/intake_policy.py" --manifest "$RUN/run_manifest.json"
```

These commands create immutable origin provenance and an immutable search-brief
snapshot. Never edit, copy over, or hot-patch a skill script during a run, and
never rewrite the provenance receipt to clear drift. If a committed fix is
required after evidence and entailment are frozen, deploy that fix and append an
audited coordinator transition instead of repeating acquisition:

```bash
python "$LDR/scripts/skill_provenance.py" --run-root "$RUN" --skill-root "$LDR" \
  --record-upgrade --reason "<committed bug fix>" --resume-from-stage reconcile
```

The upgrade command refuses the same Git commit, preserves the original worker
identity, and freezes the corpus, full text, evidence, assemblies, and managed
launches. Uncommitted self-patching remains a non-relaxable failure.

Use `ToolSearch` only if `LiteratureSearch` or `GenerateImage` is not already in
the tool schema. For a deferred tool, call `ToolSearch` with the exact
`select:<tool-name>` query and wait for its result before calling the loaded
tool. Use `Skill(action="load", ...)` to load the `pdf-report-generation` skill for
the report deliverable (see Outputs), and otherwise only for a real sibling
skill; there is no `BiomniResourcesLookup` tool.

Before calling `LiteratureSearch`, capture the global reference-log offset.
Search one facet at a time: synonyms, mechanism/direction, population/model,
outcomes, foundational work, and contradiction/null/rescue evidence. Then
ingest only the new slice:

`LiteratureSearch` uses Biomni's configured Consensus and Exa providers. Treat
their records as discovery metadata. Preserve an Exa result URL as a direct PDF
candidate when it ends in `.pdf`; acquisition still validates the returned
bytes before treating it as full text. Consensus results and Exa-extracted page
text are not PDF binaries and must not be promoted to verified full text. They
remain abstract/provider-text context unless a normal acquisition route obtains
the source document. `GenerateImage` is a separate Biomni image tool and is not
a literature or PDF retrieval route.

Europe PMC calls use a machine-wide process-safe pacer plus `Retry-After`,
exponential backoff, and jitter. Keep the default
`EPMC_MIN_INTERVAL_S=0.5` unless measured provider guidance supports a change;
never increase worker count by bypassing the pacer or rotating identity/IP.

```bash
OFFSET=$(python "$LDR/scripts/references_to_corpus.py" --refs /mnt/results/execution_trace/references.jsonl --print-offset)
# Call LiteratureSearch for each planned facet.
python "$LDR/scripts/references_to_corpus.py" --refs /mnt/results/execution_trace/references.jsonl --run-root "$RUN" --since-offset "$OFFSET"
cp "$LDR/templates/coverage_matrix.json" "$RUN/corpus/coverage_matrix.json"
```

Before corpus freeze, reconcile exact-title preprint/journal duplicates. The
version of record must supply one atomic citation identity—DOI, PMID/PMCID,
year, journal, URL, preprint flag, and publication role—while an accessible
preprint PDF may remain an alternate reading route. Never combine a journal
venue with its preprint DOI or call a version-of-record result a preprint. The
canonical ledger retains merged identifiers so this is auditable.

For broad target reviews, fill every row of `coverage_matrix.json`. Required
axes are dependency/causality, direction of effect, mechanism and competing
models, pharmacology/target engagement, biomarker/patient context,
safety/essentiality, translational/clinical evidence, contradictions/nulls, and
combinations. Mark each axis `searched_with_evidence`, `searched_empty`, or
`not_applicable`, include the actual queries, and give a reason for either empty
or not-applicable outcomes. Searched-empty axes remain visible in the report's
synthesis table. This is an executable coverage contract.

For `deep`/`broad`, show clusters, disagreements, the number of relevant unique
papers available, and the proposed full-text set before acquisition. If the
user does not respond, use every selected paper in broad mode. Draft atomic
claims from full abstracts using `templates/claims.csv`. Every claim row must
populate the `cluster` column with the evidence axis the claim belongs to (e.g.
`mechanism`, `direction_of_effect`, matching the axes in
`coverage_matrix.json`); a claim with no `cluster` is a drafting error, because
Results, the evidence-axis synthesis table, and per-axis figure coverage all
group claims by it. Set
`figure_priority=true` only for decision-critical claims whose experimental
visuals would materially affect the decision; mark background/context claims
false. Adaptive axis recovery operates only on those explicit priorities.

Each claim must contain one testable proposition: one scoped population/model,
perturbation or exposure, direction, and outcome. Split conjunctions that join
distinct findings (for example, a dependency result and a named-compound
result) before adjudication. Corpus-landscape statements such as “no clinical
agent exists” are search/synthesis conclusions; report them from coverage and
search dispositions, not as quotation-grounded atomic claims.

For broad mode, the selected set is every relevant unique record returned across
the search facets after deduplication and scope filtering, unless the user asks
to narrow it. Do not silently select an arbitrary fixed-size subset.

Write exclusions to `corpus/scope_decisions.jsonl` with `paper_id`,
`in_scope=false`, and a reason. Records without an explicit exclusion are in
scope. If a prior report/run is supplied, copy its cited records to
`corpus/prior_references.jsonl`; every prior source absent from the new
selected set must be recorded in `corpus/prior_run_reconciliation.jsonl` as
`superseded` or `excluded` with a reason and optional replacement IDs. Also copy
the prior run's `corpus/corpus_ledger.json` to
`corpus/prior_corpus_ledger.json` and reuse matching cached full text before
network acquisition. Final assembly fails if the current run loses a paper or
preprint/journal version family that the prior run successfully retrieved.

```bash
python "$LDR/scripts/reuse_prior_fulltext.py" --run-root "$RUN" \
  --prior-run "<prior-run-root>" --selected "$RUN/corpus/pivotal_papers.csv"
```

Broad mode processes every record in the selected set when `--max-papers` is
omitted (`config.max_papers=null`). Use `--max-papers N` only for a user-chosen
positive ceiling; do not add a ceiling merely because many papers are available.

Run the selection gate before launch. `managed_machine_shards.py prepare` runs
the same gate and refuses an uncapped broad subset:

```bash
python "$LDR/scripts/corpus_ledger.py" --root "$RUN" \
  --selected "$RUN/corpus/pivotal_papers.csv"
```

**Verification message:** report the unique record count, proposed full-text
count, claim count, excluded scope, and frozen snapshot path.

### Step 2 — Acquire, parse, retrieve, and adjudicate

Run each worker on its local `/workspace`. `$CHECKPOINT` is an immutable courier,
not a compute filesystem: never put parser caches, extracted bundles, native
task packs, or merge state there. The managed exchange writes each object once,
publishes `READY.json` after the input plan, and publishes each task's `DONE.json`
only after its tar bundle and checksum manifest. It never renames, overwrites,
appends, or deletes an object on the S3/FUSE mount.

Create the adaptive paper-queue launch plan. This also publishes one verified
`skill.tar`, records its SHA-256 beside the Git commit, and mirrors that identity
into the launch plan; do not `cp -R` the skill tree to shared storage:

```bash
python "$LDR/scripts/managed_machine_shards.py" prepare \
  --records "$RUN/corpus/pivotal_papers.csv" \
  --claims "$RUN/corpus/claims.csv" --exchange-root "$CHECKPOINT/managed" \
  --exchange-mode object-store --skill-root "$LDR" \
  --max-machines 5 --max-processes-per-machine 16 \
  --review-mode <mode> --ocr <recorded-ocr>
```

Require `READY.json` and its verified plan before launching. The planner chooses
machines from selected-paper count and OCR cost: approximately one machine per
8 papers for `ocr=all`, 12 for targeted OCR, or 16 with OCR off, capped at five.
Thus 33 papers with `ocr=all` launch five machines, not three or one.

Call `ManageMachine(action="list")`, then create exactly the missing machine
names from `launch_plan.json` with `ManageMachine(action="create",
machines=[...])`; `worker-0` commonly already exists. On every machine, copy
`skill.tar` to a machine-local directory, verify its SHA-256 against the plan,
extract it locally, and run `scripts/install.sh` once. The installer checks
EasyOCR/PyTorch before installing anything. Do not execute the skill from the
shared mount.

Submit one tracked background job per machine. `run-machine` copies and verifies
its claims and paper queue locally, pilots real work at increasing process
counts, and finishes at the last healthy level. It publishes one immutable
bundle per task under a unique `attempt-N/` prefix. Separate Biomni `Bash` calls
to the same machine would be serialized. Each call uses the plan's `machine_id`,
the extracted local skill, and a unique local base:

```text
Bash(machine_id='worker-N', run_in_background=true,
     background_name='literature-review-<run-id>-worker-N',
     command='python <local-skill>/scripts/managed_machine_shards.py run-machine --exchange-root <checkpoint>/managed --machine-id worker-N --skill-root <local-skill> --local-base /workspace/literature-review-<run-id>-worker-N --review-mode <mode> --ocr <recorded-ocr>')
```

After each successful `Bash` submission, persist its returned job ID (when the
tool supplies one). This receipt is required for every machine and is checked
against the completion inventory:

```bash
python "$LDR/scripts/managed_machine_shards.py" record-launch \
  --run-root "$RUN" --exchange-root "$CHECKPOINT/managed" \
  --machine-id worker-N \
  --background-name "literature-review-<run-id>-worker-N" --job-id "<job-id>"
```

After every completion callback, inspect
`completion-attempt-*/machine_completion.json`, especially
`selected_processes`, `stop_reason`, `waves`, and remaining transient failures.
Each task automatically retries transient retrieval once more using its local
cache; a failed parallel task is retried serially before the machine fails. Then
merge in original paper order. `merge` downloads each completed bundle, verifies
the marker, manifest, size, and SHA-256, extracts on coordinator-local POSIX
storage, and refuses missing, corrupt, duplicate, or unaccounted papers:

```bash
python "$LDR/scripts/managed_machine_shards.py" merge \
  --exchange-root "$CHECKPOINT/managed" --run-root "$RUN"
```

Read `fulltext/global_transient_retry.json`. If `completed=false`, launch one
global recovery wave over only the remaining `retrieval_failed` records. The
command excludes confirmed paywalls:

```bash
python "$LDR/scripts/managed_machine_shards.py" prepare-retry \
  --run-root "$RUN" --claims "$RUN/corpus/claims.csv" \
  --exchange-root "$CHECKPOINT/managed-retry" \
  --exchange-mode object-store --skill-root "$LDR" \
  --max-machines 5 --max-processes-per-machine 16 \
  --review-mode <mode> --ocr <recorded-ocr>
# Launch every machine in that retry plan as above, then:
python "$LDR/scripts/managed_machine_shards.py" merge \
  --exchange-root "$CHECKPOINT/managed-retry" --run-root "$RUN"
```

```bash
python "$LDR/scripts/evidence_first.py" --run-root "$RUN" --review-mode broad \
  --claims "$RUN/corpus/claims.csv" --records "$RUN/corpus/pivotal_papers.csv" \
  --backend none --preprocessed-run \
  --question "<question>" --title "<title>"
```

Transient retrieval failures are automatically retried once with the negative
cache bypassed before a miss is finalized. Confirmed paywalls are not retried.
The canonical run emits `evidence/adjudication_batches/batch_*.json`; stage the
self-contained tasks, then complete them natively in the Biomni coordinator:

```bash
python "$LDR/scripts/batch_tasks.py" stage-workers --root "$RUN" --kind adjudications --exchange-root "$NATIVE_EXCHANGE"
```

`stage-workers` also emits bounded packs under
`$NATIVE_EXCHANGE/native_packs/adjudications/`. This is coordinator-local POSIX
state; never stage native reasoning packs on S3. The coordinator SHOULD complete one
pack per turn where context permits. Every packed task remains independent and
writes its own output; never transfer evidence or decisions between tasks.
It also freezes an input inventory. Assembly refuses a missing, duplicate, or
changed task/output and writes `state/assemblies/<kind>.json` with every hash.
Adjudication receipts retain each batch's paper, claims, and examined block IDs
even when the batch accepts zero anchors; a later import therefore preserves
negative-search coverage instead of mistaking an empty result for lost work.
An uncited contradiction narrative is allowed only when it sets
`no_qualifying_anchor=true`; otherwise omit it. This distinguishes an explicit
searched-but-empty disposition from an unsupported reported finding.
After compaction, resume from `corpus/corpus_ledger.json`, the staged inventory,
and assembly receipts—never from a remembered worker summary. Synthesis reads
only canonical merged artifacts, so work completed on another machine or before
compaction cannot silently disappear.
Do not treat retrieval as a binary success. Inspect `parse_quality.jsonl` and
recover zero/weak-body parses from an available PDF before adjudication. A
caption-only result is `figure_only`, not ordinary body text. Every raw
adjudication must also survive in `evidence_lineage.jsonl` as accepted,
rejected with a reason, or duplicate; silently dropping a row blocks delivery.
Managed machines run code; they are not reasoning agents and MUST NOT
adjudicate claims. Do not ask for a general-purpose subagent and do not route
review work to a database-query agent.

Assemble and import native results:

```bash
python "$LDR/scripts/batch_tasks.py" assemble-adjudications --root "$RUN" --exchange-root "$NATIVE_EXCHANGE"
# Re-run evidence_first.py with --backend none --preprocessed-run and
# --adjudications-file "$RUN/evidence/adjudications.jsonl".
```

Every newly emitted adjudication batch ends with one `_decision_audit` JSONL
record. It accounts for all candidate blocks as accepted or rejected and gives
rejection-reason counts. Do not omit it when no evidence is accepted; negative
review coverage is part of the scientific record and is persisted as
`evidence/adjudication_audit.jsonl`.

Access and reuse rights are separate. Evidence `access` is one of
`oa_licensed`, `free_to_read`, `licensed_copy`, `user_supplied`, or `unknown`;
`unknown` is never upgraded to open access and fails final delivery until it is
resolved. Figure export follows `config.figure_reuse_policy`. Under
`reuse_cleared_only`, unknown/ineligible reuse rights are excluded. Under
`user_directed`, accessible figures may be included after the user's explicit
choice; the figure manifest and both report formats must state that the recorded
licence did not establish reuse permission. This provenance is disclosure, not
a legal conclusion or a claim of open licensing.

Support states are derived by `scripts/support_policy.py`. Convergence requires
primary support from at least two independent studies and not a single cohort;
multiple publications from one study do not create convergence. See
`references/evidence_contract.md`. Report labels keep publication type, anchor
depth, claim relationship, and independence separate: an abstract anchor from
a primary paper is `primary report · abstract-only anchor`, never “secondary”
merely because the body was unavailable.

**Verification message:** report acquired/failed papers, cache hits, parse and
adjudication job counts, batch successes/failures, accepted/rejected evidence,
access states, and elapsed stage timings from the manifest.

### Step 3 — Blind-review anchors and build the report

Create one blinded task for every displayed supporting or contradicting anchor:

```bash
python "$LDR/scripts/batch_tasks.py" emit-entailment --root "$RUN"
python "$LDR/scripts/batch_tasks.py" stage-workers --root "$RUN" --kind entailment --exchange-root "$NATIVE_EXCHANGE"
# Process $NATIVE_EXCHANGE/native_packs/entailment/pack_*.json natively.
python "$LDR/scripts/batch_tasks.py" assemble-entailment --root "$RUN" --exchange-root "$NATIVE_EXCHANGE"
```

Each task omits first-pass stance, evidence kind, rationale, and support tier.
Complete one pending native pack per coordinator turn where context permits.
Any missing, partial, scope-mismatched, or rejected displayed anchor is a hard
failure regardless of the claim's aggregate support state.
Only an internally coherent `entailment=yes` verdict with all four match axes
true and `scope_overreach=false` may carry a claim. `partial` remains an audit
outcome but never passes the display gate.

Ground quotes, select real paper figures under the recorded reuse policy, and
emit narrative tasks:

```bash
python "$LDR/scripts/grounded_quotes.py" --root "$RUN" --strict
python "$LDR/scripts/figure_entailment.py" --root "$RUN" --emit
# For every evidence/figure_entailment_tasks/*.json, call Biomni Read on the
# exact image_path in media_output_check mode and write the required JSON object
# to output_path. These independent image checks may be interleaved with native
# narrative packs; never infer a visual verdict from the caption alone.
python "$LDR/scripts/figure_entailment.py" --root "$RUN" --assemble
python "$LDR/scripts/export_figures.py" --run-root "$RUN"
python "$LDR/scripts/batch_tasks.py" stage-workers --root "$RUN" --kind narratives --exchange-root "$NATIVE_EXCHANGE"
# Process $NATIVE_EXCHANGE/native_packs/narratives/pack_*.json natively.
python "$LDR/scripts/batch_tasks.py" assemble-narratives --root "$RUN" --exchange-root "$NATIVE_EXCHANGE"
```

Complete the pending native narrative packs in the coordinator. Author
`deliverables/report_sections.json` using only verified evidence IDs. Every
evidence-based conclusion must also list the atomic `claim_ids` it synthesizes;
each listed claim must contribute a cited row. If every cited row is
secondary/indirect, hedge the statement and set `qualified=true` so the report
labels that limitation rather than presenting it as direct evidence.
Put evidence-backed executive-summary statements in `key_findings`. The
`external_findings` section is exclusively for material conference, registry,
or announcement-level findings with no retained full-text evidence ID; the
builders reject a grounded statement mislabelled as external.
For `deep` and `broad`, generate and install the required opening infographic;
for `quick`, do this only when the user requested one:

```bash
python "$LDR/scripts/infographic_spec.py" --root "$RUN" --seed
# Author the TODO panel fields and every atomic SCIENTIFIC_ASSERTIONS item in
# deliverables/infographic_spec.json. Each item binds one visible assertion to
# exact claim/evidence IDs and states direction, tested model, and outcome.
python "$LDR/scripts/infographic_spec.py" --root "$RUN" --write-tool-request
```

This is an agent-tool boundary, not a shell step. Do not continue to the report
builders until all of the following actions appear as real Biomni tool calls in
the execution trace:

1. If `GenerateImage` is not loaded, call `ToolSearch` with
   `query="select:GenerateImage"` and wait for the result.
2. Read `state/infographic_generate_image_request.json`, then call the loaded
   Biomni `GenerateImage` tool with exactly its `arguments` object. The prompt in
   that request contains the complete established Phylo three-panel style.
3. Require the tool result to report success and a saved `/mnt/results/...`
   path. Describing a call in prose, copying a placeholder PNG, or drawing the
   schematic with plotting code does not execute `GenerateImage` and does not
   satisfy this step.
4. Install the exact returned path. Installation deterministically overlays the
   verified title and evidence bands in a stock font so the image model
   cannot invent citations in those text-dense regions.
5. Call Biomni `Read` on the final installed
   `deliverables/infographic.png` with `mode="media_output_check"` and the
   request's prompt. Regenerate if it reports garbled labels, clipping, invented
   numeric/bracketed citations, a placeholder, non-Phylo styling, disputed
   mechanisms drawn as established, unsupported exclusive wording, or an
   antibody shown binding through its Fc stem. When an antibody is present,
   require one connected Y-shaped molecule whose variable region at an extreme
   Fab-arm tip contacts the antigen/target; the Fc constant-region stem must
   point away and touch neither antigen, target, nor membrane. The binding halo
   must sit at the Fab-variable-region contact, never on Fc.
   Independently trace every causal arrow and outcome glyph against
   `SCIENTIFIC_ASSERTIONS`; fail a reversed direction or an upgraded model or
   outcome (for example, tumour regression drawn from cell-viability evidence).
6. Record the pass against the exact final image bytes, then verify all hashes:

```bash
python "$LDR/scripts/infographic_spec.py" --root "$RUN" \
  --install-image "<path returned by GenerateImage>"
python "$LDR/scripts/infographic_spec.py" --root "$RUN" \
  --record-media-check pass --media-check-detail "<inspection result>" \
  --panel-content-check pass --safe-margins-check pass \
  --scientific-assertions-check pass --model-outcome-scope-check pass \
  --antibody-binding-check "<pass when an antibody is drawn; otherwise not_applicable>"
python "$LDR/scripts/infographic_spec.py" --root "$RUN" --verify
```

Biomni owns the tool and its platform configuration. Do not invent evidence or
use this generated schematic to replace real paper figures. Missing, unreadable,
unrequested, or stale infographics block `deep` and `broad` delivery and cannot
be waived as partial.

The user's figure minimum is the delivery floor. Do not replace it with a
higher corpus-size formula. Beyond that floor, include only relevant,
policy-eligible figures that add material, nonredundant panel-level information;
the report should not become a figure dump. User-directed inclusion makes
unknown/restricted-rights figures policy-eligible but does not relax scientific
relevance, provenance, or source-link requirements. A custom distinct-paper
coverage rule, when explicitly requested, counts paper IDs rather than several
crops from one paper.

Figure selection compares a panel only with the atomic claim and scope, never
with generic downstream vocabulary copied from all of the claim's quotes. It
also requires the caption or in-figure OCR to name one of the recorded subject
aliases; even a quoted caption cannot bypass this direct-grounding gate.
Selection checks only axes explicitly marked by `figure_priority=true`. If one
has no visual and its cited full texts contain an eligible nonredundant
primary-data figure, add the best one even after the numeric floor has been met.
Do not force a fixed global coverage fraction. Roles are explicit:
`primary_data` may depict original results; `source_model` and `review_context`
are illustrative only, are excluded by default, do not satisfy the source-
figure floor, and never increase a support tier; the generated infographic is
synthesis only. For still-uncovered priority axes, the exporter runs a second
targeted OCR pass over image-backed captionless crops from that axis's cited
papers, persists the OCR, and reruns selection.

A figure containing both a schematic setup panel and measured panels remains a
primary-data figure when its caption identifies actual measurements such as
quantification, western blots, survival curves, imaging, sample sizes, or test
statistics. Reserve `source_model` for wholly conceptual figures. Selection
validates that each crop exists and decodes before ranking; an unavailable top
candidate is recorded and the next eligible candidate is considered. Every
selected figure must finish as exported or carry an explicit reuse/image/export
failure disposition. Per-axis coverage reports exported visuals, not merely
pre-export selections.

Line-numbered preprint captions are valid captions and must be parsed rather
than falling through to embedded-image mode. When a captionless PDF stores one
multi-panel figure as adjacent raster tiles, reassemble adjacent tiles before
OCR and rank the composite. Never enlarge a small, unlabeled embedded fragment
into a report figure: incomplete fallback tiles are rejected explicitly. A
single paper may contribute at most one figure to one claim by default; further
figures for that claim should come from distinct papers and add genuinely new
panel-level information.

Crop QA is part of scientific selection. Parser crops keep padding around the
full image union without a hard page-top inset. Reject a crop whose top band
still contains a publisher/journal running header, or whose panels/labels are
clipped; re-render from the full page before selecting an alternative. A crop
that fails this visual QA cannot count toward the user's figure floor.

Every parsed figure records `caption_source`, `ocr_attempted`, `ocr_status`, and
`ocr_error`. A uniquely captioned figure on the same page may supply a parent
caption to an embedded panel crop, with `parent_figure_id` retained. Under
`ocr=all`, every image-backed crop must be attempted successfully or the run
fails; “all” may not be inferred from an empty `ocr` array.

Before building, inspect `figures_manifest.json`. Report unique parsed figure
crops, claim–figure pairs, cited source papers with eligible figures, selected
source-paper count, figure roles, per-axis coverage/gap reasons, second-pass OCR
recoveries, and each rejection cause separately. Do not describe rejection
events as unique “figures considered.” The full-text count is not a figure
denominator: a retrieved paper may yield no accepted evidence, no crop, only a
contextual model, or no figure relevant to a delivered claim. If the user's
floor is unmet, automatically retry remaining transient acquisitions and rerun
selection. Never silently lower or raise the floor.

Refresh the canonical ledger before building. PDF and Markdown render corpus
transition counts and retrieval classifications from this artifact, so authored
Methods prose cannot disagree with machine results:

```bash
python "$LDR/scripts/corpus_ledger.py" --root "$RUN" --final
```

Methods may summarize canonical counts but must not collapse transient retrieval
failures into paywalls. The report must name every selected unretrieved record
with its final classification and every retrieved full text that yielded no
accepted grounding anchor. Operational stage timings belong in receipts and the
verification message, not in the scientific report. In particular, the span
from `created_at` to a later resumed `updated_at` is calendar span, never active
runtime. Conclusions must not turn an observed selected/enriched context into
an exclusive population boundary: “works only in X” or “X defines the responsive
subset” requires that exclusivity in the cited anchors; otherwise state what was
observed and which populations remain untested. Conclusions are also audited
against their declared claim IDs and support kinds; a secondary description of
an unretrieved pivotal result cannot be written as though the pivotal paper was
directly verified.

Claim IDs are immutable across claims, evidence, figures, narratives, Markdown,
and PDF. Preserve gaps rather than renumbering a retained claim onto a dropped
claim's ID.

Build both canonical views locally:

```bash
python "$LDR/scripts/build_review.py" --root "$RUN"
python "$LDR/scripts/build_pdf.py" --root "$RUN" --out "$RUN/deliverables/report.pdf"
```

**Verification message:** report entailment coverage, failed anchors, support
states, figure reuse policy, adaptive full-text/axis/eligible/desired/floor
values, policy-eligible supply/export count, any
user-directed inclusions, narrative coverage, and the Markdown/PDF paths.

### Step 4 — Verify, export, and attest delivery

Freeze protected inputs, then launch one tracked background finalization job.
`--deliver` writes canonical reconciliation once, runs a pre-delivery gate suite,
copies every required artifact, re-reads source and destination sizes/SHA-256
digests, runs the post-copy stop-check, copies the successful final verification
report last, and publishes `delivery_receipt.json`:

```bash
python "$LDR/scripts/run_state.py" --root "$RUN" --protect
python "$LDR/scripts/run_state.py" --root "$RUN" --deliver "$RESULTS" \
  --report-root "$RESULTS_ROOT" --pdf "$RUN/deliverables/report.pdf"
```

Submit the second command through Biomni `Bash` with
`run_in_background=true` and
`background_name="literature-review-finalize"`; wait for its callback. Do not
split reconciliation, verification, copying, and attestation across separate
interactive tool turns. The finalizer prints the origin/coordinator commits and,
for adaptive figures, the retrieved-full-text, populated-axis, eligible-supply,
unlimited-desired, and resolved-floor values.

The complete audit bundle remains under the run-specific `$RESULTS` directory.
Delivery also copies the same verified bytes to a descriptive PDF directly
under `$RESULTS_ROOT`, records that absolute path and hash in the receipt, and
does not overwrite an existing different report with the same title.

The gates cover exact skill commit/package identity, corpus/worker completeness,
managed machine-count and bundle receipts, final cross-artifact reconciliation,
exact quote presence, independent
entailment, rights-aware figure supply, report contract, strict PDF parsing,
optional `qpdf --check`, protected-path drift, fatal errors, and delivered-file
integrity. Visually inspect every PDF page in the media viewer. Do not lower the
contract to clear a failure.

For a genuine closed-list blocker, record it before `--deliver --partial`.
Partial delivery may omit unavailable artifacts but never relaxes canonical
reconciliation, protected drift, fatal errors, destination verification, or the
requirement to deliver what does exist.

**Verification message:** report the skill Git commit and package SHA-256,
`may_finalize`, each gate result, visual page count, destination, copied artifact
count, delivery SHA-256 attestation, and receipt path.

### Step 5 — Generate the report (mandatory terminal step)

Use the pdf-report-generation skill to generate a pdf report with infographics
(use the Biomni GenerateImage tool), methods, results, conclusions, figures,
references, and next steps from all of the analyses.

### What actually costs time

Run long stages in Biomni's tracked background mode by default and keep the user
updated.
Adaptive managed-machine execution finds a safe process count from the actual
machine and corpus instead of assuming two processes. The speedup applies to
acquisition, parsing, and OCR; publisher throttles, hard-to-parse PDFs, machine
provisioning, and native reasoning still bound total runtime. Packed native
tasks reduce coordinator handoff turns, but they do not create concurrent
reasoning agents on Prod. Do not promise a new end-to-end runtime before
comparing stage timings on the same frozen corpus. Report the chosen process
count and stop reason for every machine. Use summed task/pack timings and the
managed critical path for performance claims; label resumable wall-clock span
as calendar span. Then use the formula in
`references/performance.md` to update the estimate.

## Scientific caveats

These bound what a delivered review may claim. They are enforced at the points
noted in the workflow and are consolidated here so they are read together.

- **Entailment verification is within-agent, not independent.** The blinded
  entailment verdict behind every displayed anchor is a coordinator re-check
  performed blind to the anchor's first-pass stance — it reduces first-pass
  bias, but it is not an independent third-party adjudication or external
  validation.
- **An abstract is never full-text evidence.** Consensus records and
  Exa-extracted page text are discovery/context only; a claim is grounded only by
  a verbatim anchor from retrieved full text.
- **Transient retrieval failure is not a paywall.** Methods must name each
  unretrieved record with its true classification and must not collapse transient
  failures into "paywalled".
- **Do not over-generalize the studied population.** "Works only in X" or "X
  defines the responsive subset" requires that exclusivity in the cited anchors;
  otherwise state what was observed and which populations remain untested.
- **Unretrieved or secondary results are labelled, not upgraded.** A conclusion
  resting on a secondary description of an unretrieved pivotal result must not be
  written as though the pivotal paper was directly verified.
- **Illustrative figures carry no evidential weight.** `source_model` and
  `review_context` crops and the generated infographic are synthesis/illustration
  only; they never raise a support tier.
- **This is not a quantitative synthesis.** No meta-analysis, effect-size
  pooling, or systematic-review protocol compliance is performed (see When to Use
  This Skill).

## Common Issues

| Problem | Cause | Fix |
|---|---|---|
| A managed task cannot see another machine's files | Machine-local `/workspace` is private | Use the object-store exchange through `$CHECKPOINT/managed`; each machine computes locally and publishes an immutable bundle plus `DONE.json` |
| Shared checkpoint rejects `os.replace` | S3/FUSE is not POSIX | Do not fall back to one machine; use `--exchange-mode object-store`, require `READY.json`, and keep extraction/merge under local `$RUN` |
| Papers or reasoning disappear after compaction | Coordinator prose was treated as state | Resume from `corpus_ledger.json`, task inventories, and assembly receipts; final gates refuse omissions |
| Transiently blocked papers remain missing | Per-machine retries ended before a global view existed | Run `prepare-retry` on the merged `retrieval_failed` set; do not retry confirmed paywalls |
| Native reasoning is coordinator-bound on Prod | Managed machines execute code, not Biomni reasoning | Process the emitted `native_packs` in the coordinator; never ask for a subagent |
| Paper is reported inaccessible despite repository text | Access classes were conflated | Preserve `free_to_read`; use `unknown` when classification is absent |
| Figure floor fails | Too few figures under the selected reuse policy | Ask whether the user wants `user_directed` inclusion; if yes, record it and rerun export without mislabeling rights status |
| PDF opens but gate fails | Truncated/corrupt structure or stale build | Rebuild locally; run `verify_pdf_structure.py`; use `qpdf --check` if installed |
| Delivery becomes stale | A canonical source or destination changed | Re-run verification and `--deliver`; receipt hashes are intentionally rechecked |

Resume any interrupted run with:

```bash
python "$LDR/scripts/run_state.py" --root "$RUN" --show
```

It derives stage state from artifacts and prints the next command. Do not infer
completion from prose or create a second run.

## Suggested Next Steps

- Re-run a warm benchmark after the first successful delivery and compare
  `acquire_parse` and `adjudication` timings with the prior 80-minute baseline.
- Narrow, split, or remove claims whose anchors fail blinded entailment.
- Add user-supplied full text when access gaps materially affect the
  decision; ask separately whether its figures may be included at the user's
  direction and label the recorded rights status.
- Export the evidence JSONL/CSV to downstream target, safety, or experimental
  design workflows; preserve evidence and locator IDs.

## Related Skills

| Skill/tool | Relationship |
|---|---|
| `LiteratureSearch` | Required Biomni discovery interface |
| `GenerateImage` | Required opening infographic for `deep`/`broad`; optional for `quick` |
| `literature-preclinical` | Prefer for narrative preclinical evidence without sentence-level audit requirements |
| `methods-landscape-review` | Prefer for methods-oriented narrative landscapes |
| `experimental-design-statistics` | Use after evidence gaps become testable study questions |

## References

- [Evidence contract](references/evidence_contract.md) — canonical schemas,
  support policy, and deterministic verification.
- [Modes and intake](references/modes_and_intake.md) — mode budgets, allocation,
  and steering.
- [Performance and caching](references/performance.md) — worker limits, caches,
  timing interpretation, and tuning.
- [Figures and quotes](references/figures_and_quotes.md) — quotation quality,
  figure provenance, and reuse-rights rationale.
- `templates/report_contract.json` — independent report requirements.
- `tests/README.md` — regression commands and synthetic fixture coverage.
