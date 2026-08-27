# Evidence and maturity contract

**Load when** filling `skill_contract.json`, deciding whether a control applies, creating a run
receipt, or advancing maturity.
**Skip if** only reading an already validated package.
**What this will not tell you** which scientific method is correct. It makes claims traceable and
unsupported branches visible; domain judgement and review still decide correctness.

## Contents

1. Applicability model
2. Contract fields
3. Runtime evidence
4. Maturity transitions
5. Cross-archetype examples

## Applicability model

The universal core is: a short user-facing research question plus a concrete four-field internal
starting task, tested capability claims, mapped clarification branches, bounded failure, an
auto-versus-guided validation matrix, and an explicit Personal Skills confirmation gate.

Conditional controls are declared individually:

- Deliverables: set `deliverable_policy.audience`, then decide report and infographic applicability
  explicitly. User-facing scientific output normally requires both. A composable helper may set
  either to `required: false` only with a resolved reason; report-specific receipt outcomes then use
  the typed `not_applicable` state. A PDF report is universal; infographic and facts applicability
  remain explicit decisions.

- Facts/provenance: `required` for evidence-bearing claims; otherwise `not_applicable` with a reason.
- Source assertions: required for exact external facts used in computation or conclusions; otherwise
  state why the run relies only on user-provided or transformation-local facts.
- Denominator/completion partitions: required when counts or completion claims exist; otherwise state
  why no partition applies.
- Inference readiness: required for population-level or comparative inference; descriptive work states
  why it does not infer.
- External dependencies: each live service gets timeouts, retries, a wall-clock budget, failure
  fixtures, and `partial` or `not_computable` finalization.
- Figures: declare `figures.applicable` from the requested deliverables and result shape, not from the
  archetype. When false, provide a non-empty reason.
- Bundled command execution: declare whether package commands apply, list their files, and enumerate
  only the branch artifacts those commands produce in `execution.command_output_paths`; artifacts
  produced by platform tools use resolved results-root filesystem evidence instead. An artifact-only
  skill records why command evidence does not apply.

Applicability is not an archetype exemption. A literature meta-analysis can require inference; a
protocol calculator can require quantitative facts; an analysis that only reformats a user table may
not need external source assertions.

## Contract fields

`skill_contract.json` uses schema `phylo-skill-evidence/1`.

### Concrete starting task

Provide `user_prompt`, `subject_input`, `objective`, `decision_context`, and `deliverables`.
`user_prompt` is the catalog sample: one short, natural research question with all scientifically
required identifiers and no instructions about PDFs, reports, figures, infographics, methods,
references, or next steps. Put those obligations in `deliverables` and the generated skill body.
Name the dataset, material and scale, or concrete input object in the internal fields, and name each
applicable sample-run artifact in `deliverables`. A report-producing skill names its root PDF. The scaffolder renders `starting-prompt`
from `user_prompt`; do not hand-edit the rendered value.

### Source assertions and witnesses

For every computation-critical assertion record:

- stable `id`, asserted `field`, and `asserted_value`;
- primary-source URI and retrieval date/version;
- verification method;
- runtime witness artifact, JSON path, and expected value.

Examples include genome build, coordinate convention, accession, replicate relationship, tool/API
signature, reagent concentration, or a quoted literature fact. A runtime witness must be produced
before a downstream mask, annotation, inference, or protocol calculation uses the value.

### Resource identity

An external identifier is not evidence that the surrounding metadata belongs to it. Skills that emit
citations, accessions, datasets, reagents, protocols, or versioned tools set
`resource_identity.applicable=true` and compare both an identifier and independent identity fields
(for example PMID/DOI plus title/year, or accession plus organism/build). The contract names the
authoritative source, result and verification artifacts, and a mismatch fixture that pairs one real
identifier with another resource's metadata. A mismatch is excluded or makes the run
`not_computable` before facts are written. The verification artifact exposes a zero-valued violation
field wired into `source_assertions`, so `write_receipt` must verify it at runtime.

### Clarification branches

Declare each question's prompt, choices, and `selection_mode` (`single` or `multiple`). Then add one
branch row per offered choice: question ID, choice ID, implementation references, expected artifacts,
terminal fallback, and eval references. Do not advertise a choice that runtime prose would need to
invent. A zero-result query or failed source lookup follows the declared fallback; it never authorizes
an unlisted source substitution.

### Authoring context and runtime instructions

Keep interview answers outside the distributable instructions. Populate `runtime_instructions` only
with validated structured fields: concrete inputs, executable workflow steps, artifact-bound caveats,
source/license records with verification references, and existing-material provenance. The scaffolder
renders the runtime SKILL.md from those fields. An interview answer may motivate them but is never
itself executable guidance.

### Facts and semantic accounting

Each headline field has an operational definition. Count-bearing outputs declare mutually exclusive
partition members, a denominator, and `sum_members_equals_denominator`. Include at least one
known-answer semantic eval for the author's named wrong default.

Useful generic partitions:

- computation: eligible, computed, failed, skipped/capped, not computable;
- retrieval: eligible, attempted, resolved, failed, not attempted;
- evidence screening: identified, deduplicated, screened, excluded, included;
- protocol execution: planned, completed, deviated, failed, not performed.

Required facts name both the canonical `report_facts.json` schema artifact and a distinct
`facts.runtime_payload_artifact` beneath the results root. The workflow writes the complete domain
payload there, then calls `write_facts_from_artifact(..., figures=validated_figures,
contract="skill_contract.json")`. That helper rejects external paths and figure inventories that
disagree, attaches the validated inventory, and applies definitions and identities before writing the
canonical facts artifact. Agreement between a wrong table and wrong PDF is not semantic validation;
the known-answer eval is what pins the intended classification.

### Capability ledger

Each capability has a claim, status (`tested`, `conditional`, or `unsupported`), implementation
references, and eval references. `catalog_claim_ids` may name only tested capabilities. The scaffolder
derives the catalog description from those tested claims plus the trigger, so prose cannot silently
advertise an untested branch.

### Validation matrix

Record auto and guided trials separately. Each is `passed`, `failed`, or `not_run`; a pass names its
evidence, and `not_run` names a reason. Auto tests the deterministic baseline. Guided validation must
exercise a scientifically or operationally meaningful clarification branch in a separate child run
before `user_validated`. Record `selection_source: user_message`, that child run's `external_task_id`,
and selected branches as `<question_id>:<choice_id>`. An autonomous branch choice, a creation-run plan
approval, or a synthetic receipt does not count as user validation. The contract rejects branch IDs
that were not offered, and the runtime receipt derives required outputs only from selected branches.

### Inference and external services

An inferential branch records experimental unit, replicate type, minimum independent units, design
identifiability, permutation/resampling support, and a runtime preflight reference. Files and technical
extractions never satisfy a biological-replication minimum merely because their count is larger.

Each external service records connect/read timeouts, maximum retries, total wall-clock budget, terminal
states, and injected timeout/rate-limit/empty/malformed/partial-success fixtures. Optional enrichment
cannot block applicable primary deliverables.

Each data-source row records name, type, URI, version, license, one of `allowed`,
`no_prohibition_found`, `prohibited`, or `not_checked`, the commercial-use evidence, inclusion state,
verification reference, and notes. Included sources cannot be `prohibited` or `not_checked`.
`DATA_SOURCES.md` is generated from this ledger.

## Runtime evidence

Evidence-v1 receipts use `phylo-run-receipt/2`. Run applicable bundled commands through
`report_qc.run_bundled`, which invokes `subprocess.run` without a shell and writes
`phylo-qc-run-log/1` from measured return codes and hashes. Do not ask the authoring agent to compose
execution events or copy identifiers from the Biomni transcript; the public transcript schema does
not expose the event IDs the old draft assumed. A command event credits an expected output only when
its content fingerprint was created or changed by that invocation; a non-empty file left by an
earlier run is not current-run evidence. Give each logically distinct branch execution a stable
`invocation_id`; retry that branch with the same ID so only its latest attempt counts, while other
successful branch invocations remain represented. `write_receipt` matches:

- bundled-file hashes to successful QC-owned command events;
- command-produced output hashes to produced artifacts recorded by those events, while independently
  produced outputs are resolved beneath the results root and hashed from the filesystem;
- source assertions to runtime witness values;
- the exact PDF hash to text and page rasters regenerated by `record_pdf_review` with bounded Poppler
  calls, plus each regenerated artifact's own hash; review bytes are copied without scratch-file
  metadata because the object-backed results mount does not support ordinary `copystat` semantics;
- a clearly labelled visual-review attestation to all rendered page numbers and review evidence.
- the canonical facts artifact to its semantic contract, with a content hash, whenever facts apply.

Text extraction, page rendering, and page visual review are separate outcomes. Never set visual review
true because text was extractable. The attestation is not independent machine verification. The
receipt is derived output; do not write booleans by hand.

## Maturity transitions

1. `generated` — scaffold exists; no validation implied.
2. `structurally_valid` — Contract A/B and evidence-v1 structural rules pass.
3. `evidence_validated` — auto validation, known-answer evals, runtime witnesses, and receipt pass.
4. `user_validated` — a separate child run passed after a real user selected clarification.
5. `installable` — all prior quality states hold; this is eligibility, not registration or approval.

Never describe `generated` or `structurally_valid` as ready to install. After validation, offer a
private preview/save; registration requires explicit confirmation stored outside the immutable
package and keyed to its reviewed hash. Review-loop drafts defer the offer until expert handoff.
Promoting a package updates both the contract maturity and the single matching Evidence Tier line in
`SKILL.md`; a stale label is an EV015 failure.

## Cross-archetype examples

| Profile | Concrete starting subject | Facts | Special controls |
|---|---|---|---|
| Quantitative analysis | named dataset, design, contrast and threshold | required | inference readiness, semantic partitions, figures |
| Literature synthesis | exact question, date/evidence scope and decision | required | primary-source provenance, retrieval completion, bounded APIs |
| Protocol generation | material, sample count/scale, endpoint and constraints | required | source-backed parameters, structured checklist, deviations |
| Pure formatter | named input file/schema and exact transformation | report required; facts may be not applicable | explicit audience and infographic applicability; receipt still required |
