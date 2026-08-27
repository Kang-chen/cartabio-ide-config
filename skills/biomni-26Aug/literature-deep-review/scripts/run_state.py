#!/usr/bin/env python3
"""Where is this run, what comes next, and may it finish — read from artifacts.

A `broad` review searches a query-dependent corpus, acquires every selected full
text unless the user sets a cap, parses and OCRs them, adjudicates evidence in
batched model calls, then builds and gates two deliverables. That outlives a
model context routinely. When the context dies, the
next one currently has to re-read SKILL.md and infer from directory listings
where the run got to — and the most common failure of that inference is starting
a *second* run beside the first.

Adapted from the ``context-management`` skill's cold-start, next-action and
finalization-gate protocols. Deliberately NOT adopted: leases, multi-worker
coordination, contract amendments, work-item claim tokens, and control-plane
checkpoint/restore. This skill's own SKILL.md says the manifest is the complete
run controller with "no separate phase DAG, leases, or completion gate", and that
is right for a single-coordinator pipeline whose artifacts are already
append-only. Adding a parallel graph would create a second source of truth that
can disagree with the first.

**So stage status is DERIVED, never stored.** Each stage is defined by the
artifacts it produces; a stage is complete when they exist and are non-empty, and
stale when an input is newer than an output. There is nothing to keep in sync,
nothing to corrupt, and a run recovered from a backup reports itself correctly
without any repair step.

Existence is not always completeness, though. Most outputs here are written to a
temporary file and renamed, so they are either absent or whole — but a stage
whose output ACCUMULATES is different: ``fulltext/parsed/`` gains one JSON per
paper, so a context that died after 12 of 30 leaves a directory that looks
finished, and the run would adjudicate against 12 papers and never say so. Those
stages are marked ``counted`` and compare their unit count against the upstream
artifact that says how many there should be.

Two gaps are known and NOT solved here, because naming them beats implying they
are covered:

  * ``record()`` is never called by the pipeline. Exclusions are gathered
    automatically, but a genuine decision — why this corpus slice, why these 25
    of 78 papers — is captured only if someone invokes it. That is the "the
    model remembers" anti-pattern the source skill names, surviving in the one
    place this module does not reach.
Also imported, after a fuller read of the source skill's references:

  * **Protected paths.** The inputs whose silent change invalidates the review
    are hashed and re-checked by every stop-check. The handoff used to only ASK
    that nobody edit ``evidence/evidence.jsonl``; a prose checklist is exactly
    what the source skill says is insufficient.
  * **Typed errors.** The type decides retryability — a 429 is waited out, a
    paywall never is, a corrupt PDF is neither — so retrying an unchanged
    deterministic failure stops being the default. An unresolved ``fatal``
    blocks finalization.
  * **Closed-list blockers and honest partial delivery.** The real case here is
    a corpus that is mostly paywalled: the review cannot be completed as scoped,
    and before this the only outcomes were "pass" or "fail". Partial delivery
    states what exists, what is missing and why. It never relaxes protected
    drift or an unresolved fatal error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Sequence

SCRIPTS = pathlib.Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from report_model import read_json, read_jsonl, resolve_review_mode  # noqa: E402
from intake_policy import figure_intake_errors, intake_snapshot_errors  # noqa: E402

CONTEXT_DIR = "context"
CURRENT_MD = "current.md"
HANDOFF_MD = "handoff.md"
LEDGER = "decisions.jsonl"
ERRORS = "errors.jsonl"
PROTECTED = "protected.json"
BLOCKER = "blocker.json"

# Error types this pipeline actually produces, from the source skill's taxonomy.
# Typed rather than free text because the type decides retryability: a 429 is
# waited out, a paywall is not, and a corrupt PDF is neither — it is a parse
# defect to fix. Retrying an unchanged deterministic failure is the anti-pattern
# the taxonomy exists to prevent.
ERROR_TYPES = {
    "NETWORK_TRANSIENT": "retry with bounded backoff and jitter",
    "RATE_LIMIT": "honour Retry-After; reduce concurrency, never scope",
    "DATA_ACCESS": "paywalled or unauthorized — a recorded gap, not a retry",
    "DATA_INTEGRITY": "checksum or content mismatch; quarantine and re-fetch",
    "ARTIFACT_VALIDATION": "produced artifact failed its own schema or gate",
    "TIMEOUT": "raise the limit or reduce the unit, then retry once",
    "EVALUATOR_INVALID": "a gate itself is wrong; fix it and invalidate results",
    "CONTEXT_DRIFT": "state no longer matches what a prior context assumed",
    "PLATFORM_HARD_STOP": "lifecycle limit reached; checkpoint and hand off",
    "UNKNOWN": "classify before retrying",
}

# Severities. A `fatal` that is unresolved blocks finalization outright.
SEVERITIES = ("info", "warning", "error", "fatal")

# The only blocker kinds accepted. A closed list on purpose: "diminishing
# returns", "good enough" and "several failed ideas" are NOT blockers, and an
# open vocabulary is how they become one.
BLOCKER_KINDS = (
    "user_stop",
    "safety_or_legal",
    "credentials_or_access",
    "irrecoverable_data",
    "platform_unavailable",
    "contract_impossible",
    "no_legal_execution_path",
)

# Inputs whose silent change would invalidate the review. Hashed at `--protect`
# and re-checked by every stop-check.
#
# This closes the gap the handoff could only ASK about. Telling a reader not to
# edit evidence.jsonl is a prose checklist, and the source skill is explicit that
# a prose checklist is insufficient — the whole point of protecting a path is
# that nobody has to be trusted to remember.
DEFAULT_PROTECTED = (
    "state/skill_provenance.json",  # exact skill commit and package hash
    "state/skill_provenance_upgrades.jsonl",  # committed coordinator transitions
    "state/intake_snapshot.json",  # immutable search brief
    "corpus/references_snapshot.jsonl",  # the frozen slice this corpus is from
    "corpus/corpus_ledger.json",  # every paper's end-to-end disposition
    "evidence/evidence.jsonl",  # the canonical product
    "evidence/adjudication_audit.jsonl",  # negative-decision coverage
    "evidence/figure_entailment.jsonl",  # exact visual/crop verdicts
    "deliverables/grounded_quotes.json",  # the anchors the report renders
    "state/assemblies",  # exact native task/output inventories
)


@dataclass(frozen=True)
class Stage:
    """One pipeline step, identified by what it leaves on disk.

    ``produces`` is the evidence the step ran. ``consumes`` is what it reads, so
    staleness can be detected: a corpus re-ingested after adjudication means the
    evidence no longer covers the corpus.

    ``counted`` marks a stage whose output ACCUMULATES — one file per unit of
    work rather than one file written atomically at the end. Existence is not
    completeness for those: ``fulltext/parsed/`` holds one JSON per paper, so a
    context that died after 12 of 30 papers leaves a directory that looks
    finished. The run would then adjudicate against 12 papers and never notice.
    A counted stage is complete only when its unit count matches the upstream
    artifact that says how many there should be.

    ``modes`` restricts a stage to the review modes that actually require it.
    Empty means every mode. A stage the contract does not ask for in this mode
    is not outstanding work, and reporting it as such would wedge the run shut:
    the operator does everything the contract asks, the pipeline gate still says
    incomplete, and the only way out is building an artifact nothing wanted.
    """
    id: str
    title: str
    produces: tuple[str, ...]
    consumes: tuple[str, ...] = ()
    command: str = ""
    optional: bool = False
    counted: bool = False
    modes: tuple[str, ...] = ()

    def applies_to(self, mode: str) -> bool:
        return not self.modes or mode in self.modes


def _contract_modes(key: str) -> tuple[str, ...]:
    """The modes the contract requires ``key`` in.

    Read rather than hardcoded so the stage table cannot drift from the gate
    that enforces it — the same duplication that already put the figure
    selection policy out of sync with the contract once.
    """
    contract = read_json(SKILL / "templates" / "report_contract.json") or {}
    block = contract.get(key) or {}
    return tuple(block.get("required_modes") or ())


# The pipeline as SKILL.md describes it. Order matters: the first incomplete
# stage is the next action.
STAGES: tuple[Stage, ...] = (
    Stage(
        "intake",
        "Record the brief and create the run",
        produces=(
            "run_manifest.json",
            "state/skill_provenance.json",
            "state/intake_snapshot.json",
        ),
        command=(
            "ask any missing paper-figure/OCR question, create "
            "$RUN/run_manifest.json, then python "
            '"$LDR/scripts/skill_provenance.py" --run-root "$RUN" '
            '--skill-root "$LDR", then python '
            '"$LDR/scripts/intake_policy.py" --manifest '
            '"$RUN/run_manifest.json"'
        ),
    ),
    Stage(
        "search",
        "Search broadly and ingest the corpus",
        produces=(
            "corpus/references.jsonl",
            "corpus/ingestion.json",
            "corpus/corpus_ledger.json",
        ),
        consumes=("state/intake_snapshot.json",),
        command=(
            'python "$LDR/scripts/references_to_corpus.py" --refs '
            "/mnt/results/execution_trace/references.jsonl "
            '--run-root "$RUN" --since-offset "$OFFSET"'
        ),
    ),
    Stage("claims", "Draft the candidate claims",
          produces=("corpus/claims.jsonl",),
          consumes=("corpus/references.jsonl",),
          command="draft corpus/claims.jsonl (one row per candidate claim)"),
    Stage("acquire", "Acquire full text for the selected papers",
          produces=("fulltext/papers.jsonl", "fulltext/not_retrieved.jsonl",
                    "fulltext/global_transient_retry.json"),
          consumes=("corpus/references.jsonl",),
          command=('prepare managed_machine_shards.py with '
                   '--exchange-mode object-store --skill-root "$LDR" for up to five '
                   'ManageMachine paper queues with adaptive concurrency '
                   '(--max-processes-per-machine 16 --review-mode <mode> '
                   '--ocr <recorded-ocr>; pilot 2, 3, 4, 6, 8, 12, up to 16); '
                   'submit one '
                   'run-machine call per machine with '
                   'Biomni Bash(machine_id=<worker>, run_in_background=true, '
                   'background_name="literature-review-<worker>")')),
    Stage("parse", "Parse full texts into located blocks",
          produces=("fulltext/parsed",),
          consumes=("fulltext/papers.jsonl",),
          counted=True,
          command=('after all managed Bash(run_in_background=true, '
                   'background_name="literature-review-<worker>") callbacks, '
                   'inspect selected_processes, stop_reason, and waves; '
                   'run managed_machine_shards.py merge and resume '
                   'evidence_first.py with --preprocessed-run')),
    Stage("adjudicate", "Judge stance and accept evidence rows",
          produces=("evidence/evidence.jsonl",),
          consumes=("corpus/claims.jsonl", "fulltext/parsed"),
          command=('python "$LDR/scripts/batch_tasks.py" stage-workers '
                   '--root "$RUN" --kind adjudications '
                   '--exchange-root "$RUN/state/native_exchange"; process each '
                   '"$RUN/state/native_exchange/native_packs/adjudications/pack_*.json" in '
                   'the native Biomni coordinator, preserving separate outputs; '
                   'then python "$LDR/scripts/batch_tasks.py" '
                   'assemble-adjudications --root "$RUN" '
                   '--exchange-root "$RUN/state/native_exchange"; '
                   'then python "$LDR/scripts/evidence_first.py" '
                   '--run-root "$RUN" --review-mode <mode> '
                   '--claims "$RUN/corpus/claims.csv" '
                   '--records "$RUN/corpus/pivotal_papers.csv" '
                   '--backend none --preprocessed-run --adjudications-file '
                   '"$RUN/evidence/adjudications.jsonl" '
                   '--question "<question>" --title "<title>"')),
    Stage("entailment", "Blind-review every displayed grounding anchor",
          produces=("evidence/entailment.jsonl",),
          consumes=("evidence/evidence.jsonl", "corpus/claims.jsonl"),
          command=('python "$LDR/scripts/batch_tasks.py" emit-entailment '
                   '--root "$RUN"; then python '
                   '"$LDR/scripts/batch_tasks.py" stage-workers '
                   '--root "$RUN" --kind entailment '
                   '--exchange-root "$RUN/state/native_exchange"; process each '
                   '"$RUN/state/native_exchange/native_packs/entailment/pack_*.json" in the '
                   'native Biomni coordinator, preserving blinded independent '
                   'outputs; '
                   'then python '
                   '"$LDR/scripts/batch_tasks.py" assemble-entailment '
                   '--root "$RUN" --exchange-root "$RUN/state/native_exchange"')),
    Stage("ground", "Extract the verbatim anchors per claim",
          produces=("deliverables/grounded_quotes.json",),
          consumes=("evidence/evidence.jsonl",),
          command=('python "$LDR/scripts/grounded_quotes.py" --root "$RUN" '
                   '--strict')),
    Stage("figure_verify", "Visually verify exact claim/figure/crop pairs",
          produces=("evidence/figure_entailment.jsonl",),
          consumes=("evidence/evidence.jsonl", "corpus/claims.jsonl"),
          modes=("deep", "broad"),
          command=('python "$LDR/scripts/figure_entailment.py" --root "$RUN" '
                   '--emit; complete every emitted task with Biomni Read in '
                   'media_output_check mode; then python '
                   '"$LDR/scripts/figure_entailment.py" --root "$RUN" --assemble')),
    Stage("figures", "Select and export the paper figures",
          produces=("deliverables/figures_cited/figures_manifest.json",),
          consumes=("evidence/evidence.jsonl", "corpus/claims.jsonl",
                    "evidence/figure_entailment.jsonl"),
          command='python "$LDR/scripts/export_figures.py" --run-root "$RUN"'),
    Stage("narrative", "Author the per-claim narratives and prose sections",
          produces=("deliverables/claim_narratives.jsonl",
                    "deliverables/report_sections.json"),
          consumes=("deliverables/grounded_quotes.json",),
          command=("stage narrative_tasks: python "
                   "\"$LDR/scripts/batch_tasks.py\" stage-workers "
                   "--root \"$RUN\" --kind narratives --exchange-root "
                   "\"$RUN/state/native_exchange\"; process each "
                   "\"$RUN/state/native_exchange/native_packs/narratives/pack_*.json\" in "
                   "the native Biomni coordinator, preserving separate outputs; "
                   "then python "
                   "\"$LDR/scripts/batch_tasks.py\" assemble-narratives "
                   "--root \"$RUN\" --exchange-root \"$RUN/state/native_exchange\"; author "
                   "deliverables/report_sections.json")),
    Stage("infographic", "Seed, generate, install and verify the opening infographic",
          produces=("deliverables/infographic_spec.json",
                    "state/infographic_generate_image_request.json",
                    "state/infographic_generation.json",
                    "state/infographic_media_check.json",
                    "deliverables/infographic.png"),
          consumes=("evidence/evidence.jsonl",),
          modes=_contract_modes("visual_abstract"),
          command=('AGENT TOOL ACTION (not one Bash command): python '
                   '"$LDR/scripts/infographic_spec.py" --root "$RUN" '
                   '--seed; author the panels; then python '
                   '"$LDR/scripts/infographic_spec.py" --root "$RUN" '
                   '--write-tool-request. If GenerateImage is not loaded, call '
                   'ToolSearch(query="select:GenerateImage") and wait for its '
                   'result. Read state/infographic_generate_image_request.json; '
                   'make an actual GenerateImage tool call with exactly its '
                   'arguments (plain text does not execute a tool). Require a '
                   'success result, then run '
                   'python "$LDR/scripts/infographic_spec.py" --root "$RUN" '
                   '--install-image <returned-path>. Call Read on the installed '
                   'deliverables/infographic.png with mode="media_output_check" '
                   'and the request QC prompt; regenerate on failure. After a '
                   'pass, run python "$LDR/scripts/infographic_spec.py" --root '
                   '"$RUN" --record-media-check pass --media-check-detail '
                   '"<inspection result>"; then python '
                   '"$LDR/scripts/infographic_spec.py" --root "$RUN" --verify')),
    Stage("build", "Render both deliverables",
          produces=("deliverables/review.md", "deliverables/report.pdf"),
          consumes=("deliverables/grounded_quotes.json",
                    "deliverables/report_sections.json"),
          command=('python "$LDR/scripts/build_review.py" --root "$RUN" && '
                   'python "$LDR/scripts/build_pdf.py" --root "$RUN" '
                   '--out "$RUN/deliverables/report.pdf"')),
    Stage("verify", "Run the gates and finalize",
          produces=("state/verification_report.json",),
          consumes=("deliverables/review.md",),
          command=('python "$LDR/scripts/run_state.py" --root "$RUN" '
                   '--stop-check --pdf "$RUN/deliverables/report.pdf"')),
    Stage("deliver", "Copy the deliverables to the results destination",
          produces=("state/delivery.json",),
          # The DELIVERABLES, not the verification report. Delivery is invalid
          # when what was delivered has since been rebuilt — that is real
          # staleness. It is not invalidated by the gate suite running again,
          # and consuming the report made it so: --stop-check rewrites
          # verification_report.json on every run, which would mark the delivery
          # stale the moment you re-ran the check that delivery is required to
          # satisfy. The gate could then never open again.
          consumes=("deliverables/review.md", "deliverables/report.pdf",
                    "deliverables/evidence_table.csv",
                    "deliverables/claim_evidence_matrix.csv",
                    "deliverables/grounded_quotes.json",
                    "deliverables/review_stats.json",
                    "evidence/evidence.jsonl", "evidence/entailment.jsonl"),
          command=('python "$LDR/scripts/run_state.py" --root "$RUN" '
                   '--deliver "$RESULTS" --report-root "$RESULTS_ROOT"')),
)


@dataclass
class StageStatus:
    stage: Stage
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    stale_against: list[str] = field(default_factory=list)
    done_units: int = 0
    expected_units: int = 0
    applicable: bool = True

    @property
    def state(self) -> str:
        if not self.applicable:
            return "not_required"
        if self.missing:
            return "pending" if not self.present else "partial"
        if self.stage.counted and self.expected_units:
            if self.done_units == 0:
                return "pending"
            if self.done_units < self.expected_units:
                return "partial"
        return "stale" if self.stale_against else "complete"

    @property
    def progress(self) -> str:
        if not self.stage.counted or not self.expected_units:
            return ""
        return f"{self.done_units}/{self.expected_units}"


def _parse_progress(root: pathlib.Path) -> tuple[int, int]:
    """(papers parsed, papers that should be parsed).

    A paper only needs parsing if its full text was actually retrieved — a
    paywalled one is a recorded gap, not outstanding work, and counting it would
    leave the stage permanently short of a total it can never reach.
    """
    parsed = len(list((root / "fulltext" / "parsed").glob("*.json"))) \
        if (root / "fulltext" / "parsed").is_dir() else 0
    retrievable = sum(
        1 for row in read_jsonl(root / "fulltext" / "papers.jsonl")
        if str(row.get("access_state") or "") in {"oa_licensed", "free_to_read"})
    return parsed, retrievable


COUNTERS = {"parse": _parse_progress}


def _exists_nonempty(root: pathlib.Path, rel: str) -> bool:
    path = root / rel
    if rel == "fulltext/not_retrieved.jsonl":
        # Zero misses is a complete acquisition outcome, represented by an
        # intentionally empty JSONL file.
        return path.is_file()
    if path.is_dir():
        return any(p.is_file() and p.stat().st_size > 0 for p in path.iterdir())
    return path.is_file() and path.stat().st_size > 0


# How much newer an input must be than an output before the output counts as
# stale, in seconds. Without a tolerance a stage that runs fast enough to write
# its outputs in the same second as its inputs reports itself stale forever,
# because the comparison then turns on filesystem timestamp granularity rather
# than on anything real. Genuine staleness — a corpus re-ingested after
# adjudication — is minutes or hours out, far outside this window.
STALE_TOLERANCE_SECONDS = 5.0


def _mtime(root: pathlib.Path, rel: str) -> float:
    path = root / rel
    if path.is_dir():
        times = [p.stat().st_mtime for p in path.rglob("*") if p.is_file()]
        return max(times) if times else 0.0
    return path.stat().st_mtime if path.exists() else 0.0


def stage_statuses(root: pathlib.Path) -> list[StageStatus]:
    """Every stage's status, derived from the artifacts on disk.

    Stages the current review mode does not require report ``not_required``
    rather than being dropped, so the handoff still accounts for all twelve and
    a reader can see the stage was skipped by policy, not forgotten.
    """
    mode = resolve_review_mode(root)
    out: list[StageStatus] = []
    for stage in STAGES:
        status = StageStatus(stage, applicable=stage.applies_to(mode))
        if not status.applicable:
            out.append(status)
            continue
        for rel in stage.produces:
            (status.present if _exists_nonempty(root, rel)
             else status.missing).append(rel)
        if stage.id == "intake" and status.present:
            manifest = read_json(root / "run_manifest.json", {}) or {}
            status.missing.extend(
                f"figure/OCR intake: {error}"
                for error in figure_intake_errors(manifest)
            )
            snapshot = read_json(root / "state" / "intake_snapshot.json", {}) or {}
            status.missing.extend(
                f"intake snapshot: {error}"
                for error in intake_snapshot_errors(manifest, snapshot)
            )
        counter = COUNTERS.get(stage.id)
        if counter is not None:
            status.done_units, status.expected_units = counter(root)
        if not status.missing:
            newest_output = min(_mtime(root, rel) for rel in stage.produces)
            for rel in stage.consumes:
                if not _exists_nonempty(root, rel):
                    continue
                if _mtime(root, rel) > newest_output + STALE_TOLERANCE_SECONDS:
                    status.stale_against.append(rel)
        out.append(status)
    return out


def next_action(root: pathlib.Path) -> tuple[str, str]:
    """(stage id, exact command) for the first stage that is not complete.

    The single most useful field for a cold start. Everything else in the
    handoff exists to make this one line trustworthy.
    """
    for status in stage_statuses(root):
        if status.state not in ("complete", "not_required"):
            if status.stage.optional and status.state == "pending":
                continue
            return status.stage.id, status.stage.command
    return "done", ('python "$LDR/scripts/run_state.py" --root "$RUN" '
                    "--stop-check   # all stages complete")


# --- the decision / exclusion ledger -----------------------------------------


def record(root: pathlib.Path, kind: str, summary: str, *,
           detail: str = "", evidence: str = "", stage: str = "") -> dict:
    """Append one durable decision, exclusion or failure.

    The reasons a run drops things are already recorded — but scattered, in
    shapes only their own consumer understands: ``selection_rejected`` in the
    figure manifest, ``evidence_kind_relabel_reason`` on an evidence row,
    ``access_state`` on a paper. None of them reaches a resuming context, and
    only some reach the report. One append-only ledger makes "why is this
    missing?" answerable in a single place, by a human or by the next context.

    Append-only on purpose: a superseded decision is preserved, not rewritten.
    """
    entry = {
        "seq": _next_seq(root),
        "kind": kind,
        "stage": stage,
        "summary": summary,
        "detail": detail,
        "evidence": evidence,
    }
    path = root / "state" / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _next_seq(root: pathlib.Path) -> int:
    return len(read_jsonl(root / "state" / LEDGER)) + 1


# --- typed errors, and resolution as an append -------------------------------


def record_error(root: pathlib.Path, error_type: str, message: str, *,
                 severity: str = "error", stage: str = "",
                 evidence: str = "", next_action_text: str = "") -> dict:
    """Record one classified failure. Type decides what may be retried.

    Failures are preserved, never deleted — a paper that could not be retrieved
    and a batch that failed twice are both part of what this review is, and the
    report's Limitations section is only honest if they survive.
    """
    if error_type not in ERROR_TYPES:
        raise SystemExit(
            f"unknown error type {error_type!r}; classify it as one of: "
            + ", ".join(sorted(ERROR_TYPES)))
    if severity not in SEVERITIES:
        raise SystemExit(f"severity must be one of {SEVERITIES}")
    entry = {
        "id": f"E-{len(read_jsonl(root / 'state' / ERRORS)) + 1:06d}",
        "type": error_type,
        "severity": severity,
        "stage": stage,
        "message": message,
        "evidence": evidence,
        "guidance": ERROR_TYPES[error_type],
        "next_action": next_action_text,
        "resolved": False,
    }
    _append(root / "state" / ERRORS, entry)
    return entry


def resolve_error(root: pathlib.Path, error_id: str, resolution: str,
                  evidence: str = "") -> dict:
    """Resolve by APPENDING, never by rewriting the original record."""
    entry = {"id": error_id, "resolution": resolution, "evidence": evidence,
             "resolved": True}
    _append(root / "state" / ERRORS, entry)
    return entry


def open_errors(root: pathlib.Path) -> list[dict]:
    """Errors with no later resolution record, newest state per id."""
    state: dict[str, dict] = {}
    for row in read_jsonl(root / "state" / ERRORS):
        eid = str(row.get("id") or "")
        if row.get("resolved"):
            state.pop(eid, None)
        else:
            state[eid] = row
    return list(state.values())


def _append(path: pathlib.Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --- protected paths ----------------------------------------------------------


def _digest(root: pathlib.Path, rel: str) -> str | None:
    """SHA-256 of a file, or of a directory's sorted (path, size, hash) list."""
    import hashlib

    target = root / rel
    if target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()
    if target.is_dir():
        digest = hashlib.sha256()
        for path in sorted(p for p in target.rglob("*") if p.is_file()):
            digest.update(str(path.relative_to(target)).encode())
            digest.update(str(path.stat().st_size).encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()
    return None


def protect(root: pathlib.Path, paths: Sequence[str] = ()) -> dict:
    """Freeze the hashes of the inputs whose silent change invalidates the run."""
    wanted = list(paths) or list(DEFAULT_PROTECTED)
    index = {rel: _digest(root, rel) for rel in wanted}
    index = {rel: h for rel, h in index.items() if h}
    out = root / "state" / PROTECTED
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    return index


def protected_drift(root: pathlib.Path) -> list[str]:
    """Protected paths whose content no longer matches the frozen hash."""
    index = read_json(root / "state" / PROTECTED, None)
    if not isinstance(index, dict) or not index:
        return []
    drift: list[str] = []
    for rel, expected in sorted(index.items()):
        actual = _digest(root, rel)
        if actual is None:
            drift.append(f"{rel}: protected path is missing")
        elif actual != expected:
            drift.append(f"{rel}: content changed since it was protected")
    return drift


# --- closed-list blockers -----------------------------------------------------


def set_blocker(root: pathlib.Path, kind: str, summary: str, *,
                evidence: str = "", user_action: str = "") -> dict:
    if kind not in BLOCKER_KINDS:
        raise SystemExit(
            f"{kind!r} is not a blocker. The list is closed: "
            + ", ".join(BLOCKER_KINDS)
            + ". Diminishing returns, a thin backlog, cost already spent and "
              "'good enough' are not blockers — they are reasons to keep going.")
    entry = {"kind": kind, "summary": summary, "evidence": evidence,
             "user_action": user_action, "active": True}
    path = root / "state" / BLOCKER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    return entry


def clear_blocker(root: pathlib.Path, reason: str) -> None:
    path = root / "state" / BLOCKER
    if path.exists():
        path.write_text(json.dumps(
            {"active": False, "cleared_because": reason}, indent=2) + "\n",
            encoding="utf-8")


def active_blocker(root: pathlib.Path) -> dict | None:
    entry = read_json(root / "state" / BLOCKER, None)
    return entry if isinstance(entry, dict) and entry.get("active") else None


def ledger(root: pathlib.Path) -> list[dict]:
    return read_jsonl(root / "state" / LEDGER)


def collect_exclusions(root: pathlib.Path) -> list[dict]:
    """Reasons already recorded elsewhere, gathered into ledger shape.

    Read, not copied: these stay owned by the artifacts that produce them. This
    is a view so the handoff and the report's Limitations section can answer
    "what did this run drop, and why?" without knowing five formats.
    """
    out: list[dict] = []
    for row in read_jsonl(root / "fulltext" / "papers.jsonl"):
        state = str(row.get("access_state") or "")
        if state and state != "oa_licensed" and state != "free_to_read":
            out.append({"kind": "paper_not_retrieved", "stage": "acquire",
                        "summary": f"{row.get('paper_id')}: {state}",
                        "evidence": "fulltext/papers.jsonl"})
    manifest = read_json(
        root / "deliverables" / "figures_cited" / "figures_manifest.json", {}) or {}
    for rejected in manifest.get("selection_rejected") or []:
        out.append({
            "kind": "figure_not_shown", "stage": "figures",
            "summary": (f"{rejected.get('paper_id')}/{rejected.get('figure_id')}: "
                        f"{rejected.get('cause')}"),
            "evidence": "deliverables/figures_cited/figures_manifest.json"})
    for row in read_jsonl(root / "evidence" / "evidence.jsonl"):
        reason = str(row.get("evidence_kind_relabel_reason") or "")
        if reason:
            out.append({"kind": "evidence_downgraded", "stage": "adjudicate",
                        "summary": f"{row.get('evidence_id')}: {reason}",
                        "evidence": "evidence/evidence.jsonl"})
    return out


# --- cold-start packets -------------------------------------------------------


def write_context(root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Regenerate context/current.md and context/handoff.md.

    Regenerated from artifacts every time, never edited by hand — a summary a
    model wrote from memory is exactly the thing that goes stale without anyone
    noticing. If these disagree with the run directory, the directory wins and
    the fix is to regenerate.
    """
    manifest = read_json(root / "run_manifest.json", {}) or {}
    statuses = stage_statuses(root)
    stage_id, command = next_action(root)
    exclusions = collect_exclusions(root)
    decisions = ledger(root)

    lines = [
        f"# {manifest.get('title') or 'Literature deep review'} — current state",
        "",
        f"- Question: {manifest.get('question') or '(not recorded)'}",
        f"- Mode: {resolve_review_mode(root)}",
        f"- Run root: `{root}`",
        "",
        "## Exact next action",
        "",
        f"Stage `{stage_id}`:",
        "",
        "```bash",
        command,
        "```",
        "",
        "## Pipeline",
        "",
        "| Stage | State | Missing |",
        "|---|---|---|",
    ]
    for status in statuses:
        missing = ", ".join(status.missing) or "—"
        if status.progress and status.state == "partial":
            missing = f"{status.progress} units done"
        if status.stale_against:
            missing = f"stale against {', '.join(status.stale_against)}"
        lines.append(f"| {status.stage.id} | {status.state} | {missing} |")

    if decisions:
        lines += ["", "## Decisions recorded", ""]
        lines += [f"- [{d['kind']}] {d['summary']}" for d in decisions[-12:]]
    if exclusions:
        lines += ["", f"## Dropped ({len(exclusions)} total, newest 12)", ""]
        lines += [f"- [{e['kind']}] {e['summary']}" for e in exclusions[-12:]]

    context_dir = root / CONTEXT_DIR
    context_dir.mkdir(parents=True, exist_ok=True)
    current = context_dir / CURRENT_MD
    current.write_text("\n".join(lines) + "\n", encoding="utf-8")

    handoff = context_dir / HANDOFF_MD
    handoff.write_text(_handoff_text(root, manifest, stage_id, command),
                       encoding="utf-8")
    return current, handoff


def _handoff_text(
    root: pathlib.Path, manifest: dict, stage_id: str, command: str
) -> str:
    """Cold-start instructions: enough to resume with no conversation history."""
    return f"""# Cold-start handoff

A previous context was working on this run. **Resume it — do not start a new
one.** A second run beside the first is the most common and most expensive
failure of a lost context: it re-searches, re-acquires and re-adjudicates a
corpus that already exists, and the two runs then disagree.

## 1. Orient

```bash
RUN={root}
LDR=/path/to/literature-deep-review
python "$LDR/scripts/run_state.py" --root "$RUN" --show
```

Read `context/current.md` first. It is regenerated from the run's artifacts, so
it cannot be stale in the way a hand-written note can — if it disagrees with the
directory, the directory wins and you regenerate with `--write-context`.

## 2. What is binding

- The question: {manifest.get("question") or "(not recorded)"}
- `templates/report_contract.json` defines what a finished report must contain.
- `evidence/evidence.jsonl` is the canonical product. Prose, tables and figures
  are views over it and are never hand-authored.
- `corpus/corpus_ledger.json` accounts for every paper from discovery through
  selection, acquisition, citation and figure production. Reconstruct corpus
  counts from it; never from conversational memory or worker summaries.
- Every delivered claim must show a verbatim quote with a resolvable locator.

## 3. What must not be changed

- Accepted evidence rows: correct them by re-running adjudication, not by
  editing the file.
- `corpus/references_snapshot.jsonl`: the frozen slice this corpus was built
  from. Editing it makes the corpus unreproducible.
- A claim's support tier: it is derived from the evidence, never asserted.

## 4. Next action

Stage `{stage_id}`:

```bash
{command}
```

## 5. Finishing

Completion is not a judgement call. Launch this as one tracked background job:

```bash
python "$LDR/scripts/run_state.py" --root "$RUN" --deliver "$RESULTS" \
  --report-root "$RESULTS_ROOT" --pdf "$RUN/deliverables/report.pdf"
```

The command reconciles, preflights, copies, rechecks, and attests without an
interactive gap between those steps.
"""


# --- delivery -----------------------------------------------------------------

# Every completed review owes these artifacts to the caller. Optional visual and
# narrative files are copied when present, but their absence is enforced by the
# review-mode contract rather than silently invented here.
REQUIRED_DELIVERABLES = (
    "run_manifest.json",
    "state/intake_snapshot.json",
    "corpus/references.jsonl",
    "corpus/claims.jsonl",
    "corpus/corpus_ledger.json",
    "fulltext/global_transient_retry.json",
    "fulltext/acquisition_routes.jsonl",
    "deliverables/review.md",
    "deliverables/evidence_table.csv",
    "deliverables/claim_evidence_matrix.csv",
    "deliverables/grounded_quotes.json",
    "deliverables/grounded_quotes.md",
    "deliverables/review_stats.json",
    "evidence/adjudications.jsonl",
    "evidence/evidence.jsonl",
    "evidence/evidence_lineage.jsonl",
    "evidence/rejected_evidence.jsonl",
    "evidence/entailment.jsonl",
    "fulltext/parse_quality.jsonl",
    "state/final_reconciliation.json",
    "state/quality_summary.json",
    "state/skill_provenance.json",
)

OPTIONAL_DELIVERABLES = (
    "corpus/coverage_matrix.json",
    "fulltext/not_retrieved.jsonl",
    "evidence/adjudication_audit.jsonl",
    "evidence/figure_entailment.jsonl",
    "deliverables/infographic.png",
    "deliverables/infographic_spec.json",
    "state/infographic_generate_image_request.json",
    "state/infographic_generation.json",
    "state/infographic_media_check.json",
    "corpus/references_snapshot.jsonl",
    "corpus/scope_decisions.jsonl",
    "fulltext/papers.jsonl",
    "deliverables/figures_cited",
    "deliverables/claim_narratives.jsonl",
    "deliverables/report_sections.json",
    "state/assemblies",
    "state/managed_launches",
    "state/skill_provenance_upgrades.jsonl",
)

FINAL_ATTESTATION = "state/verification_report.json"
DELIVERY_RECEIPT_NAME = "delivery_receipt.json"
REPORT_FILENAME_MAX_STEM = 96
REPORT_FILENAME_FALLBACK = "literature-deep-review"
VALID_EMPTY_DELIVERABLES = frozenset({
    "evidence/adjudications.jsonl",
    "evidence/evidence_lineage.jsonl",
    "evidence/rejected_evidence.jsonl",
})
GENERIC_REPORT_TITLES = frozenset({
    "grounded literature review",
    "literature deep review",
    "replace report title",
})


def report_pdf_filename(root: pathlib.Path) -> str:
    """A readable, prompt-derived filename for the user-visible PDF copy."""
    manifest = read_json(root / "run_manifest.json")
    title = str(manifest.get("title") or "").strip()
    question = str(manifest.get("question") or "").strip()
    source = title
    if not source or source.casefold() in GENERIC_REPORT_TITLES:
        source = question
    ascii_source = unicodedata.normalize("NFKD", source).encode(
        "ascii", "ignore"
    ).decode("ascii")
    words = re.findall(r"[A-Za-z0-9]+", ascii_source)
    stem = "-".join(words) or REPORT_FILENAME_FALLBACK
    if not re.search(r"\b(?:report|review)\b", ascii_source, re.IGNORECASE):
        suffix = "-literature-review"
        stem = stem[: REPORT_FILENAME_MAX_STEM - len(suffix)].rstrip("-") + suffix
    else:
        stem = stem[:REPORT_FILENAME_MAX_STEM].rstrip("-")
    return f"{stem}.pdf"


def _visible_report_target(
    results_root: pathlib.Path,
    source: pathlib.Path,
    filename: str,
) -> pathlib.Path:
    """Preserve an existing different report instead of overwriting it."""
    preferred = results_root / filename
    if not preferred.exists() or _file_digest(preferred) == _file_digest(source):
        return preferred
    suffix = _file_digest(source)[:8]
    return preferred.with_name(f"{preferred.stem}-{suffix}.pdf")


def deliver(
    root: pathlib.Path,
    dest: pathlib.Path,
    pdf: pathlib.Path | None = None,
    *,
    partial: bool = False,
    report_root: pathlib.Path | None = None,
    prepared: bool = False,
) -> dict:
    """Copy the deliverables to ``dest`` and verify they arrived intact.

    The run root is the worker's local disk and ``dest`` is the caller's results
    mount. Those are not the same filesystem, and the pipeline used to end at
    ``verify`` with nothing producing anything outside the run root: a run built
    every artifact, passed all nine gates against its LOCAL paths, reported
    may_finalize=yes, and delivered an empty results folder. The gates were
    answering a question nobody had asked them.

    Copy, never rename. ``atomic_json`` and ``write_jsonl`` finish with
    ``Path.replace``, which an S3-backed mount rejects — which is why the run
    has to happen on local disk and be copied here as a distinct step.

    Verification is byte-level, because a truncated or zero-byte copy on an
    object-store mount looks exactly like a success to ``shutil``.
    """
    if not prepared:
        from reconcile_run import refresh as reconcile

        _receipt, reconciliation_failures = reconcile(root, write=True)
        if reconciliation_failures:
            raise SystemExit(
                "refusing to deliver: final reconciliation failed: "
                + "; ".join(reconciliation_failures[:4])
            )

        # Direct callers must still prove every non-delivery gate passed. The
        # CLI finalizer supplies a fresh preflight and uses ``prepared=True``.
        verdict = read_json(root / FINAL_ATTESTATION)
        unmet = [
            r["gate"]
            for r in (verdict.get("results") or [])
            if not r.get("ok") and r["gate"] != "delivered"
        ]
        if not verdict or unmet:
            raise SystemExit(
                f"refusing to deliver: {len(unmet) or 1} gate(s) still failing "
                f"({', '.join(unmet[:4])}). Fix them, re-run --stop-check, then "
                "deliver."
            )

    if pdf is None:
        canonical_pdf = root / "deliverables" / "report.pdf"
        pdf = canonical_pdf if canonical_pdf.exists() else None

    required = [root / rel for rel in REQUIRED_DELIVERABLES]
    if pdf is None:
        missing = ["deliverables/report.pdf"]
    else:
        required.append(pdf)
        missing = []
    missing.extend(
        str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        for path in required if not path.exists()
    )
    if missing and not partial:
        raise SystemExit(
            "refusing to deliver: required artifact(s) missing: "
            + ", ".join(missing)
        )
    if partial and not active_blocker(root):
        raise SystemExit(
            "refusing partial delivery without an active closed-list blocker"
        )

    dest.mkdir(parents=True, exist_ok=True)
    visible_root = (report_root or dest).resolve()
    visible_root.mkdir(parents=True, exist_ok=True)
    copied: list[dict] = []
    failures: list[str] = []

    targets = [path for path in required if path.exists()] + [
        path for rel in OPTIONAL_DELIVERABLES
        if (path := root / rel).exists()
        and (path.is_dir() or path.stat().st_size > 0)
    ]
    files: list[tuple[pathlib.Path, pathlib.Path]] = []
    for source in targets:
        if source.is_dir():
            for child in sorted(path for path in source.rglob("*") if path.is_file()):
                files.append((child, child.relative_to(root)))
        else:
            try:
                relative = source.relative_to(root)
            except ValueError:
                relative = pathlib.Path(source.name)
            files.append((source, relative))

    seen_destinations: set[pathlib.Path] = set()
    for source, relative in files:
        if relative in seen_destinations:
            failures.append(f"duplicate delivery destination: {relative}")
            continue
        seen_destinations.add(relative)
        target = dest / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            want, got = source.stat().st_size, target.stat().st_size
            digest = _file_digest(source)
            empty_is_valid = str(relative) in VALID_EMPTY_DELIVERABLES
            if got != want or (got == 0 and not empty_is_valid):
                failures.append(f"{relative}: copied {got} bytes of {want}")
            elif digest != _file_digest(target):
                failures.append(f"{relative}: content differs after copy")
            copied.append({
                "source": str(relative),
                "destination": str(relative),
                "path": str(target),
                "bytes": got,
                "sha256": digest,
                "valid_empty_ledger": empty_is_valid and got == 0,
            })
        except Exception as exc:  # noqa: BLE001 - a failed copy is a failed delivery
            failures.append(f"{relative}: {type(exc).__name__}: {exc}")

    visible_report: dict[str, object] = {}
    if pdf is not None and pdf.exists():
        try:
            visible_target = _visible_report_target(
                visible_root, pdf, report_pdf_filename(root)
            )
            shutil.copy2(pdf, visible_target)
            want, got = pdf.stat().st_size, visible_target.stat().st_size
            digest = _file_digest(pdf)
            if got != want or got == 0:
                failures.append(
                    f"user-visible report: copied {got} bytes of {want}"
                )
            elif digest != _file_digest(visible_target):
                failures.append("user-visible report: content differs after copy")
            try:
                visible_source = str(pdf.relative_to(root))
            except ValueError:
                visible_source = str(pdf)
            visible_report = {
                "source": visible_source,
                "destination": visible_target.name,
                "path": str(visible_target),
                "bytes": got,
                "sha256": digest,
                "visibility": "results_root",
            }
            copied.append(visible_report)
        except Exception as exc:  # noqa: BLE001 - failed visibility is failed delivery
            failures.append(
                f"user-visible report: {type(exc).__name__}: {exc}"
            )

    report = {
        "schema_version": 3,
        "run_root": str(root),
        "destination": str(dest),
        "results_root": str(visible_root),
        "visible_report": visible_report,
        "copied": copied,
        "failures": failures,
        "missing_required": missing,
        "partial": bool(partial),
        "delivered": not failures,
    }
    out = root / "state" / "delivery.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _file_digest(path: pathlib.Path) -> str:
    """sha256 of one absolute file. Distinct from ``_digest(root, rel)``, which
    hashes a run-relative protected path and returns a marker when absent."""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json_if_changed(path: pathlib.Path, value: dict) -> bool:
    """Write deterministic JSON without changing mtime when content is equal."""
    payload = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    if path.exists() and path.read_bytes() == payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return True


def delivery_state(root: pathlib.Path) -> tuple[bool, str]:
    """Re-read every source and destination named by the delivery receipt."""
    report = read_json(root / "state" / "delivery.json")
    if not report:
        return False, ("nothing delivered — the deliverables are still only on "
                       "the worker's local disk; run --deliver \"$RESULTS\"")
    if report.get("failures"):
        return False, "; ".join(report["failures"][:3])
    dest = pathlib.Path(str(report.get("destination") or ""))
    failures: list[str] = []
    for item in report.get("copied") or []:
        source = root / str(item.get("source") or "")
        target = pathlib.Path(str(item.get("path") or ""))
        expected_size = int(item.get("bytes") or 0)
        expected_digest = str(item.get("sha256") or "")
        for label, path in (("source", source), ("destination", target)):
            if not path.exists():
                failures.append(f"{label} missing: {path}")
                continue
            if path.stat().st_size != expected_size:
                failures.append(
                    f"{label} size changed: {path} "
                    f"({path.stat().st_size} != {expected_size})"
                )
            elif _file_digest(path) != expected_digest:
                failures.append(f"{label} digest changed: {path}")

    attestation = report.get("attestation") or {}
    if attestation:
        target = dest / str(attestation.get("destination") or "")
        if not target.exists():
            failures.append(f"final attestation missing: {target}")
        elif _file_digest(target) != str(attestation.get("sha256") or ""):
            failures.append(f"final attestation digest changed: {target}")
    if failures:
        return False, "; ".join(failures[:3])
    n = len(report.get("copied") or [])
    return True, f"{n} artifact(s) copied to and verified at {dest}"


def finalize_delivery(root: pathlib.Path) -> dict:
    """Copy the successful final report last and publish the delivery receipt."""
    receipt_path = root / "state" / "delivery.json"
    report = read_json(receipt_path)
    if not report or report.get("failures"):
        raise SystemExit("cannot finalize an absent or failed delivery receipt")
    verification = root / FINAL_ATTESTATION
    verdict = read_json(verification)
    if not verdict.get("may_finalize"):
        raise SystemExit("final verification report does not authorize delivery")
    dest = pathlib.Path(str(report["destination"]))
    target = dest / "state" / "verification_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(verification, target)
    digest = _file_digest(target)
    if digest != _file_digest(verification):
        raise SystemExit("final verification report differs after delivery")
    report["attestation"] = {
        "destination": "state/verification_report.json",
        "bytes": target.stat().st_size,
        "sha256": digest,
    }
    receipt_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    delivered_receipt = dest / DELIVERY_RECEIPT_NAME
    shutil.copy2(receipt_path, delivered_receipt)
    return report


def finalize_run(
    root: pathlib.Path,
    dest: pathlib.Path,
    pdf: pathlib.Path,
    *,
    partial: bool = False,
    report_root: pathlib.Path | None = None,
) -> dict:
    """Reconcile, verify, copy, and attest in one interruption-safe command."""
    from reconcile_run import refresh as reconcile

    _receipt, reconciliation_failures = reconcile(root, write=True)
    if reconciliation_failures:
        raise SystemExit(
            "refusing to finalize: final reconciliation failed: "
            + "; ".join(reconciliation_failures[:4])
        )
    preflight = stop_check(
        root,
        pdf,
        partial=partial,
        require_delivery=False,
    )
    if not preflight["may_finalize"]:
        failed = [
            result["gate"] for result in preflight["results"] if not result.get("ok")
        ]
        raise SystemExit(
            "refusing to finalize: pre-delivery gates failed: " + ", ".join(failed[:4])
        )
    report = deliver(
        root,
        dest,
        pdf,
        partial=partial,
        report_root=report_root,
        prepared=True,
    )
    if not report["delivered"]:
        return report
    final = stop_check(root, pdf, partial=partial, require_delivery=True)
    if not final["may_finalize"]:
        raise SystemExit(
            "delivery copied bytes but final attestation failed; inspect "
            "state/verification_report.json before sharing the report"
        )
    return finalize_delivery(root)


# --- one finalization gate ----------------------------------------------------

GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("provenance", ("skill_provenance.py", "--run-root", "{root}", "--verify")),
    ("corpus", ("corpus_ledger.py", "--root", "{root}", "--final")),
    ("reconciliation", ("reconcile_run.py", "--root", "{root}")),
    ("review", ("verify_review.py", "--root", "{root}", "--strict-counts")),
    ("entailment", ("verify_entailment.py", "--root", "{root}",
                    "--require-entailment")),
    ("pdf_quotes", ("verify_pdf_quotes.py", "--root", "{root}",
                    "--pdf", "{pdf}")),
    ("pdf_assets", ("verify_pdf_assets.py", "--root", "{root}", "--pdf", "{pdf}")),
    ("pdf_structure", ("verify_pdf_structure.py", "--pdf", "{pdf}")),
    ("contract", ("verify_report_contract.py", "--root", "{root}",
                  "--pdf", "{pdf}")),
    ("infographic", ("infographic_spec.py", "--root", "{root}", "--verify")),
)


# Checks a blocker may relax, and the ones it never may. Partial delivery is an
# honest "here is what exists and why the rest cannot", not a way to wave through
# a corrupt run: malformed state, protected drift and a broken artifact stay
# fatal no matter what is blocking.
RELAXABLE = {"pipeline", "review", "entailment", "pdf_quotes", "pdf_assets",
             "pdf_structure", "contract"}
# Delivery is never relaxed. A blocker can justify delivering LESS, but not
# leaving what exists on a disk the caller cannot see. A required infographic
# is likewise part of the report, not optional decoration a partial run may omit.
NON_RELAXABLE = {
    "provenance", "corpus", "reconciliation", "protected", "fatal_errors",
    "delivered", "infographic"
}


def stop_check(
    root: pathlib.Path,
    pdf: pathlib.Path | None,
    partial: bool = False,
    *,
    require_delivery: bool = True,
) -> dict:
    """Run every gate and return one verdict.

    The gates already existed and were run by hand, each printing its own
    pass/fail — so "did this pass?" was a prose judgement over five scrollbacks,
    which is exactly the shape of decision that goes wrong at the end of a long
    session. One command, one ``may_finalize``.

    Stages that are incomplete are reported as failures too: a gate suite that
    passes because the artifacts it checks were never built is worse than no
    gate at all.
    """
    results: list[dict] = []

    incomplete = [s.stage.id for s in stage_statuses(root)
                  if s.state not in ("complete", "not_required")
                  and s.stage.id not in {"verify", "deliver"}
                  and not (s.stage.optional and s.state == "pending")]
    if incomplete:
        results.append({"gate": "pipeline", "ok": False,
                        "detail": f"stages not complete: {', '.join(incomplete)}"})
    else:
        results.append({"gate": "pipeline", "ok": True, "detail": "all stages complete"})

    skipped = {s.stage.id for s in stage_statuses(root) if s.state == "not_required"}
    for name, argv in GATES:
        if name in skipped:
            results.append({"gate": name, "ok": True,
                            "detail": f"not required in {resolve_review_mode(root)} mode"})
            continue
        script = SCRIPTS / argv[0]
        if not script.exists():
            results.append({"gate": name, "ok": False,
                            "detail": f"missing gate script {argv[0]}"})
            continue
        if "{pdf}" in argv and pdf is None:
            results.append({"gate": name, "ok": False,
                            "detail": "needs --pdf"})
            continue
        rendered = [str(script)] + [
            a.format(root=str(root), pdf=str(pdf) if pdf else "") for a in argv[1:]]
        try:
            proc = subprocess.run([sys.executable, *rendered], capture_output=True,
                                  text=True, timeout=900)
            ok = proc.returncode == 0
            detail = (proc.stdout.strip().splitlines() or [""])[-1]
        except Exception as exc:  # noqa: BLE001 - a gate that cannot run is a failure
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append({"gate": name, "ok": ok, "detail": detail})

    # Integrity checks, which no blocker relaxes.
    drift = protected_drift(root)
    results.append({
        "gate": "protected", "ok": not drift,
        "detail": ("; ".join(drift) if drift else
                   "protected inputs unchanged since they were frozen")})

    # Delivery is checked LAST and cannot be relaxed. Everything above this line
    # can be true of files that exist only on the worker's local disk, which is
    # precisely how a run passed all nine gates and handed back an empty results
    # folder. "Verified" is not "delivered".
    delivered, delivery_detail = delivery_state(root)
    if not require_delivery:
        delivered = True
        delivery_detail = "pre-delivery gate; byte-level delivery check pending"
    results.append({"gate": "delivered", "ok": delivered,
                    "detail": delivery_detail})

    fatal = [e for e in open_errors(root) if e.get("severity") == "fatal"]
    results.append({
        "gate": "fatal_errors", "ok": not fatal,
        "detail": ("; ".join(f"{e['id']} {e['type']}: {e['message']}"
                             for e in fatal) if fatal
                   else "no unresolved fatal errors")})

    failures = [r for r in results if not r["ok"]]
    blocker = active_blocker(root)
    non_relaxable = [r for r in failures if r["gate"] in NON_RELAXABLE]

    if partial and blocker and not non_relaxable:
        may_finalize = True
    else:
        may_finalize = not failures

    report = {
        "checks": len(results),
        "failures": len(failures),
        "non_relaxable": len(non_relaxable),
        "partial": bool(partial),
        "blocker": blocker,
        "may_finalize": may_finalize,
        "phase": "final" if require_delivery else "pre_delivery",
        "results": results,
    }
    if partial and not blocker:
        report["may_finalize"] = False
        report["results"].append({
            "gate": "partial_authorized", "ok": False,
            "detail": ("--partial needs an active closed-list blocker. Set one "
                       "with --block <kind> <summary>, or finish the work.")})
        report["checks"] = len(report["results"])
        report["failures"] += 1
    out = root / "state" / "verification_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_json_if_changed(out, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--show", action="store_true",
                        help="print stage status and the exact next action")
    parser.add_argument("--write-context", action="store_true",
                        help="regenerate context/current.md and handoff.md")
    parser.add_argument("--stop-check", action="store_true",
                        help="run every gate and print one may_finalize verdict")
    parser.add_argument("--pdf", default=None, help="report PDF, for the PDF gates")
    parser.add_argument("--deliver", metavar="DEST", default=None,
                        help="copy the deliverables to DEST (the results mount) "
                             "and verify they arrived intact")
    parser.add_argument(
        "--report-root",
        default=None,
        help="copy a prompt-named PDF directly into this visible Results root; "
             "defaults to --deliver DEST",
    )
    parser.add_argument("--partial", action="store_true",
                        help="allow honest partial delivery under an active "
                             "closed-list blocker")
    parser.add_argument("--protect", nargs="*", metavar="PATH",
                        help="freeze hashes of the inputs whose silent change "
                             "would invalidate the review (default: the "
                             "corpus snapshot, evidence and grounded quotes)")
    parser.add_argument("--error", nargs=2, metavar=("TYPE", "MESSAGE"),
                        help="record a classified failure")
    parser.add_argument("--severity", default="error", choices=SEVERITIES)
    parser.add_argument("--resolve", nargs=2, metavar=("ERROR_ID", "RESOLUTION"),
                        help="append a resolution for a recorded error")
    parser.add_argument("--block", nargs=2, metavar=("KIND", "SUMMARY"),
                        help=f"declare a blocker; kinds: {', '.join(BLOCKER_KINDS)}")
    parser.add_argument("--unblock", metavar="REASON",
                        help="clear the active blocker")
    parser.add_argument("--record", nargs=2, metavar=("KIND", "SUMMARY"),
                        help="append a decision or exclusion to the ledger")
    parser.add_argument("--stage", default="", help="stage for --record")
    parser.add_argument("--evidence", default="", help="evidence path for --record")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    if not any((args.show, args.write_context, args.stop_check, args.deliver,
                args.record,
                args.protect is not None, args.error, args.resolve, args.block,
                args.unblock)):
        args.show = True

    if args.protect is not None:
        index = protect(root, args.protect)
        print(f"PROTECTED: {len(index)} path(s) frozen")
        for rel in sorted(index):
            print(f"  {rel}")
    if args.error:
        entry = record_error(root, args.error[0], args.error[1],
                             severity=args.severity, stage=args.stage,
                             evidence=args.evidence)
        print(f"ERROR {entry['id']} [{entry['severity']}] {entry['type']}: "
              f"{entry['guidance']}")
    if args.resolve:
        resolve_error(root, args.resolve[0], args.resolve[1], args.evidence)
        print(f"RESOLVED: {args.resolve[0]}")
    if args.block:
        entry = set_blocker(root, args.block[0], args.block[1],
                            evidence=args.evidence)
        print(f"BLOCKED [{entry['kind']}]: {entry['summary']}")
    if args.unblock:
        clear_blocker(root, args.unblock)
        print(f"UNBLOCKED: {args.unblock}")

    if args.record:
        entry = record(root, args.record[0], args.record[1],
                       stage=args.stage, evidence=args.evidence)
        print(f"RECORDED: #{entry['seq']} [{entry['kind']}] {entry['summary']}")

    if args.show:
        stage_id, command = next_action(root)
        for status in stage_statuses(root):
            marker = {"complete": "ok ", "stale": "STALE", "partial": "part",
                      "pending": "  - "}[status.state]
            progress = f"  [{status.progress}]" if status.progress else ""
            print(f"  {marker} {status.stage.id:<12} {status.stage.title}{progress}")
            if status.missing:
                print(f"        missing: {', '.join(status.missing)}")
            if status.stale_against:
                print(f"        stale against: {', '.join(status.stale_against)}")
        dropped = collect_exclusions(root)
        if dropped:
            print(f"\n  dropped: {len(dropped)} item(s) — see context/current.md")
        print(f"\nNEXT [{stage_id}]: {command}")

    if args.write_context:
        current, handoff = write_context(root)
        print(f"CONTEXT: {current}\nHANDOFF: {handoff}")

    if args.deliver:
        pdf = (pathlib.Path(args.pdf).resolve() if args.pdf
               else root / "deliverables" / "report.pdf")
        report = finalize_run(
            root,
            pathlib.Path(args.deliver).resolve(),
            pdf,
            partial=args.partial,
            report_root=(
                pathlib.Path(args.report_root).resolve()
                if args.report_root else None
            ),
        )
        for item in report["copied"]:
            print(f"  -> {item['path']} ({item['bytes']} bytes)")
        for failure in report["failures"]:
            print(f"  FAIL {failure}")
        print(f"DELIVER: artifacts={len(report['copied'])} "
              f"failures={len(report['failures'])} "
              f"result={'delivered' if report['delivered'] else 'incomplete'}")
        if not report["delivered"]:
            return 1
        delivered, detail = delivery_state(root)
        print(f"DELIVER: final_attestation={'yes' if delivered else 'no'} {detail}")
        quality = read_json(root / "state" / "quality_summary.json", {}) or {}
        figure_policy = quality.get("figure_policy") or {}
        adaptive = figure_policy.get("adaptive_resolution") or {}
        if adaptive:
            print(
                "ADAPTIVE-FIGURES: "
                f"full_text={adaptive.get('full_text_papers')} "
                f"axes={adaptive.get('populated_axes')} "
                f"eligible={adaptive.get('eligible_figures')} "
                f"desired={adaptive.get('unlimited_desired_minimum')} "
                f"floor={adaptive.get('resolved_minimum')}"
            )
        execution = quality.get("execution") or {}
        origin = execution.get("skill_provenance") or {}
        upgrades = execution.get("skill_provenance_upgrades") or []
        coordinator = (upgrades[-1].get("to_identity") or {}) if upgrades else origin
        print(
            "SKILL-IDENTITY: "
            f"origin_commit={origin.get('git_commit')} "
            f"coordinator_commit={coordinator.get('git_commit')} "
            f"upgrades={len(upgrades)}"
        )
        return 0 if delivered else 1

    if args.stop_check:
        pdf = pathlib.Path(args.pdf).resolve() if args.pdf else None
        report = stop_check(root, pdf, partial=args.partial)
        for result in report["results"]:
            print(f"  {'PASS' if result['ok'] else 'FAIL'} {result['gate']:<12} "
                  f"{result['detail']}")
        if report.get("blocker"):
            b = report["blocker"]
            print(f"  BLOCKER [{b['kind']}]: {b['summary']}")
        print(f"STOP-CHECK: checks={report['checks']} "
              f"failures={report['failures']} "
              f"non_relaxable={report['non_relaxable']} "
              f"partial={'yes' if report['partial'] else 'no'} "
              f"may_finalize={'yes' if report['may_finalize'] else 'no'}")
        return 0 if report["may_finalize"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
