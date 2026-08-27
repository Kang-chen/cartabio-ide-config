"""Surviving a lost context: stage derivation, handoff, and one stop gate.

A `broad` review outlives a model context routinely — a query-dependent corpus,
all selected full-text acquisitions unless the user sets a cap, parsing, OCR,
batched adjudication, then two builds and five gates. The expensive failure when
a context dies is not confusion, it is
starting a SECOND run beside the first: it re-searches and re-adjudicates a
corpus that already exists, and the two then disagree.

Imported from the context-management skill's cold-start, next-action and
finalization-gate protocols. Its leases, work-item claim tokens, contract
amendments and control-plane checkpoints are deliberately NOT here — this skill
is single-coordinator and its artifacts are already append-only.
"""

from __future__ import annotations

import json
import os
import time

import pytest

import run_state
from run_state import (
    STAGES,
    collect_exclusions,
    ledger,
    next_action,
    record,
    stage_statuses,
    stop_check,
    write_context,
)


def _complete_pipeline(run):
    """Fill the two stages the fixture legitimately does not produce."""
    (run / "corpus" / "ingestion.json").write_text('{"since_offset": 0}')
    inaccessible = {
        "paper_id": "10.1000/delta",
        "title": "A deliberately inaccessible fixture paper",
        "access_state": "not_retrievable",
    }
    for relative in (
        "corpus/references.jsonl",
        "corpus/records.jsonl",
        "fulltext/papers.jsonl",
    ):
        path = run / relative
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        if not any(row.get("paper_id") == inaccessible["paper_id"] for row in rows):
            rows.append(inaccessible)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return run


# --- state is derived, never stored ------------------------------------------


def test_stage_status_comes_from_artifacts_on_disk(run_root):
    """Nothing to keep in sync and nothing to corrupt: a run restored from a
    backup reports itself correctly with no repair step."""
    statuses = {s.stage.id: s.state for s in stage_statuses(run_root)}
    assert statuses["intake"] == "complete"
    assert statuses["ground"] == "complete"
    assert statuses["build"] == "pending"


def test_intake_stays_incomplete_without_an_explicit_ocr_decision(run_root):
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["config"].pop("ocr_decision_source")
    manifest_path.write_text(json.dumps(manifest))

    intake = {s.stage.id: s for s in stage_statuses(run_root)}["intake"]
    assert intake.state == "partial"
    assert any("ocr_decision_source" in item for item in intake.missing)
    assert next_action(run_root)[0] == "intake"


def test_no_state_file_is_written_by_reading(run_root):
    before = sorted(p.name for p in run_root.iterdir())
    stage_statuses(run_root)
    next_action(run_root)
    assert sorted(p.name for p in run_root.iterdir()) == before


def test_next_action_is_the_first_incomplete_stage(run_root):
    _complete_pipeline(run_root)
    stage, command = next_action(run_root)
    assert stage == "infographic"
    assert "infographic_spec.py" in command


def test_every_stage_declares_a_command():
    """The exact next action is the single most useful field in a cold start;
    a stage with no command cannot supply one."""
    for stage in STAGES:
        assert stage.command.strip(), stage.id


def test_managed_exchange_is_object_safe_and_native_packs_stay_local():
    commands = {stage.id: stage.command for stage in STAGES}
    assert "--exchange-mode object-store" in commands["acquire"]
    for stage_id in ("adjudicate", "entailment", "narrative"):
        assert "$RUN/state/native_exchange" in commands[stage_id]
        assert "$CHECKPOINT/native_packs" not in commands[stage_id]


# --- staleness ---------------------------------------------------------------


def test_same_second_writes_are_not_stale(run_root):
    """A stage fast enough to write its outputs in the same second as its inputs
    would otherwise report itself stale forever, on filesystem timestamp
    granularity rather than on anything real."""
    assert not any(s.stale_against for s in stage_statuses(run_root)
                   if s.state == "stale" and s.stage.id == "ground")


def test_a_genuinely_newer_input_reopens_its_stage(run_root):
    """A corpus re-ingested after adjudication means the evidence no longer
    covers the corpus."""
    _complete_pipeline(run_root)
    later = time.time() + 3600
    os.utime(run_root / "corpus" / "references.jsonl", (later, later))
    stale = {s.stage.id for s in stage_statuses(run_root) if s.state == "stale"}
    assert "claims" in stale
    # A reopened stage takes precedence over later pending ones: the run must go
    # back and redo it, not carry on from where it happened to stop.
    assert next_action(run_root)[0] == "claims"


def test_runtime_metric_writes_do_not_reopen_search(run_root):
    """The mutable manifest is an execution log, not the frozen search brief."""
    _complete_pipeline(run_root)
    later = time.time() + 3600
    os.utime(run_root / "run_manifest.json", (later, later))

    search = {s.stage.id: s for s in stage_statuses(run_root)}["search"]

    assert search.state == "complete"
    assert search.stale_against == []


# --- the ledger --------------------------------------------------------------


def test_recorded_decisions_are_append_only(run_root):
    record(run_root, "scope_decision", "Excluded non-human studies")
    record(run_root, "scope_decision", "Superseded: include primate studies")
    entries = ledger(run_root)
    assert [e["seq"] for e in entries] == [1, 2]
    assert "Excluded non-human studies" in entries[0]["summary"]


def test_exclusions_are_gathered_from_where_they_already_live(run_root):
    """The reasons a run drops things are already recorded, but scattered in
    shapes only their own consumer understands — selection_rejected in the figure
    manifest, evidence_kind_relabel_reason on an evidence row, access_state on a
    paper. None of them reached a resuming context."""
    from export_figures import export_cited_figures

    _complete_pipeline(run_root)
    export_cited_figures(run_root)
    kinds = {e["kind"] for e in collect_exclusions(run_root)}
    assert "paper_not_retrieved" in kinds
    assert "figure_not_shown" in kinds


def test_exclusions_are_a_view_not_a_copy(run_root):
    """They stay owned by the artifacts that produce them, so the ledger cannot
    drift from the manifest it reports on."""
    _complete_pipeline(run_root)
    before = (run_root / "state" / run_state.LEDGER).exists()
    collect_exclusions(run_root)
    assert (run_root / "state" / run_state.LEDGER).exists() is before


# --- cold-start packets -------------------------------------------------------


def test_context_packet_names_the_exact_next_command(run_root):
    _complete_pipeline(run_root)
    current, _handoff = write_context(run_root)
    text = current.read_text()
    assert "## Exact next action" in text
    assert "infographic_spec.py" in text


def test_handoff_tells_a_cold_context_to_resume_not_restart(run_root):
    """The expensive failure of a lost context is a second run beside the
    first."""
    _current, handoff = write_context(run_root)
    text = handoff.read_text()
    assert "do not start a new" in text.lower()
    assert str(run_root) in text


def test_handoff_names_what_must_not_be_changed(run_root):
    _current, handoff = write_context(run_root)
    text = handoff.read_text()
    assert "evidence/evidence.jsonl" in text
    assert "references_snapshot.jsonl" in text
    assert "support tier" in text


def test_context_is_regenerated_not_edited(run_root):
    """A summary a model wrote from memory is exactly the thing that goes stale
    without anyone noticing."""
    current, _ = write_context(run_root)
    current.write_text("stale hand-written note")
    current, _ = write_context(run_root)
    assert "stale hand-written note" not in current.read_text()
    assert "Exact next action" in current.read_text()


# --- one finalization gate ----------------------------------------------------


def test_stop_check_refuses_while_stages_are_incomplete(run_root):
    """A gate suite that passes because the artifacts it checks were never built
    is worse than no gate at all."""
    report = stop_check(run_root, None)
    assert report["may_finalize"] is False
    pipeline = [r for r in report["results"] if r["gate"] == "pipeline"][0]
    assert pipeline["ok"] is False
    assert "build" in pipeline["detail"]


def test_stop_check_reports_every_gate_not_just_the_first(run_root):
    report = stop_check(run_root, None)
    gates = {r["gate"] for r in report["results"]}
    assert {"pipeline", "review", "entailment", "contract", "infographic"} <= gates


def test_stop_check_writes_a_durable_report(run_root):
    stop_check(run_root, None)
    saved = json.loads(
        (run_root / "state" / "verification_report.json").read_text())
    assert saved["checks"] == len(saved["results"])
    assert saved["may_finalize"] is False


def test_stop_check_exit_code_matches_the_verdict(run_root):
    assert run_state.main(["--root", str(run_root), "--stop-check"]) == 1


def test_a_missing_pdf_is_a_failure_not_a_skip(run_root):
    """Silently skipping the PDF gates would let a report with no PDF finalize."""
    report = stop_check(run_root, None)
    pdf_gates = [r for r in report["results"]
                 if r["gate"].startswith("pdf_") and not r["ok"]]
    assert pdf_gates
    assert all("needs --pdf" in r["detail"] for r in pdf_gates)


def test_a_gate_that_cannot_run_counts_as_failed(run_root, monkeypatch):
    """A gate that errored is not a gate that passed."""
    monkeypatch.setattr(run_state, "GATES",
                        (("bogus", ("no_such_gate.py", "--root", "{root}")),))
    report = stop_check(run_root, None)
    bogus = [r for r in report["results"] if r["gate"] == "bogus"][0]
    assert bogus["ok"] is False
    assert "missing gate script" in bogus["detail"]


# --- what was deliberately not imported --------------------------------------


def test_no_parallel_control_plane_was_introduced(run_root):
    """The skill's own SKILL.md says the manifest is the complete run controller
    with no separate phase DAG, leases or completion gate. Deriving state from
    artifacts honours that; a second store would create a source of truth that
    can disagree with the first."""
    write_context(run_root)
    for absent in ("work", "workers", "checkpoints", "plan", "contract"):
        assert not (run_root / absent).exists(), (
            f"{absent}/ suggests a parallel control plane was added")


# --- existence is not completeness for an accumulating stage ------------------


def _papers(run, n):
    """n retrievable papers, with the earlier stages complete so `parse` is the
    first thing outstanding — otherwise next_action reports the earlier gap and
    says nothing about parsing."""
    (run / "corpus" / "ingestion.json").write_text('{"since_offset": 0}')
    (run / "fulltext" / "papers.jsonl").write_text("".join(
        json.dumps({"paper_id": f"10.1000/p{i}", "access_state": "oa_licensed"})
        + "\n" for i in range(n)))


def test_a_half_finished_parse_does_not_report_complete(run_root):
    """The hole this closed: fulltext/parsed/ gains one JSON per paper, so a
    context that died after 12 of 30 left a directory that looked finished. The
    run would then adjudicate against 12 papers and never say so."""
    _papers(run_root, 3)
    parsed = run_root / "fulltext" / "parsed"
    for extra in sorted(parsed.glob("*.json"))[1:]:
        extra.unlink()

    status = {s.stage.id: s for s in stage_statuses(run_root)}["parse"]
    assert status.state == "partial"
    assert status.progress == "1/3"
    assert next_action(run_root)[0] == "parse"


def test_a_finished_parse_reports_complete(run_root):
    _papers(run_root, 3)
    status = {s.stage.id: s for s in stage_statuses(run_root)}["parse"]
    assert status.state == "complete"
    assert status.progress == "3/3"


def test_unretrievable_papers_are_not_outstanding_work(run_root):
    """A paywalled paper is a recorded gap, not work still to do. Counting it
    would leave the stage permanently short of a total it can never reach."""
    (run_root / "fulltext" / "papers.jsonl").write_text(
        json.dumps({"paper_id": "10.1000/alpha", "access_state": "oa_licensed"})
        + "\n"
        + json.dumps({"paper_id": "10.1000/beta", "access_state": "oa_licensed"})
        + "\n"
        + json.dumps({"paper_id": "10.1000/gamma", "access_state": "oa_licensed"})
        + "\n"
        + json.dumps({"paper_id": "10.1000/paywalled",
                      "access_state": "not_retrievable"}) + "\n")
    status = {s.stage.id: s for s in stage_statuses(run_root)}["parse"]
    assert status.expected_units == 3
    assert status.state == "complete"


def test_atomically_written_outputs_need_no_counting(run_root):
    """evidence.jsonl is written to a .tmp and renamed, so it is absent or
    whole. Counting it would be machinery guarding a case that cannot arise."""
    counted = {s.id for s in STAGES if s.counted}
    assert "adjudicate" not in counted
    assert counted == {"parse"}


# --- protected paths: enforcement, not a prose request ------------------------


def test_protected_inputs_are_frozen_and_drift_is_caught(run_root):
    """The handoff could only ASK that nobody edit evidence.jsonl. A prose
    checklist is exactly what the source skill says is insufficient."""
    run_state.protect(run_root)
    assert run_state.protected_drift(run_root) == []

    (run_root / "evidence" / "evidence.jsonl").write_text('{"tampered": true}\n')
    drift = run_state.protected_drift(run_root)
    assert any("evidence/evidence.jsonl" in d for d in drift)


def test_a_deleted_protected_path_is_drift_too(run_root):
    run_state.protect(run_root)
    (run_root / "evidence" / "evidence.jsonl").unlink()
    assert any("missing" in d for d in run_state.protected_drift(run_root))


def test_unprotected_run_reports_no_drift(run_root):
    """Absence of a baseline is not a violation — it is a run that never froze
    one, and inventing failures there would train people to ignore the gate."""
    assert run_state.protected_drift(run_root) == []


# --- typed errors -------------------------------------------------------------


def test_error_type_must_come_from_the_taxonomy(run_root):
    """The type decides retryability, so a free-text type is a retry decision
    nobody made."""
    with pytest.raises(SystemExit) as excinfo:
        run_state.record_error(run_root, "PAPER_MISSING", "x")
    assert "classify it" in str(excinfo.value)


def test_each_error_carries_its_retry_guidance(run_root):
    entry = run_state.record_error(run_root, "DATA_ACCESS", "Baker 2006 paywalled")
    assert "not a retry" in entry["guidance"]
    assert run_state.record_error(
        run_root, "RATE_LIMIT", "429")["guidance"].startswith("honour Retry-After")


def test_resolution_is_an_append_not_a_rewrite(run_root):
    """Failures are preserved: a paper that could not be retrieved is part of
    what this review is, and Limitations is only honest if it survives."""
    entry = run_state.record_error(run_root, "TIMEOUT", "parse timed out")
    run_state.resolve_error(run_root, entry["id"], "raised the limit; reparsed")
    rows = [json.loads(x) for x in
            (run_root / "state" / "errors.jsonl").read_text().splitlines() if x]
    assert len(rows) == 2
    assert rows[0]["message"] == "parse timed out"
    assert run_state.open_errors(run_root) == []


def test_an_unresolved_fatal_error_blocks_finalization(run_root):
    run_state.record_error(run_root, "EVALUATOR_INVALID",
                           "entailment gate is wrong", severity="fatal")
    report = stop_check(run_root, None)
    fatal = [r for r in report["results"] if r["gate"] == "fatal_errors"][0]
    assert fatal["ok"] is False
    assert report["may_finalize"] is False


# --- closed-list blockers and honest partial delivery -------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "diminishing_returns",
        "good_enough",
        "thin_backlog",
        "cost_spent",
    ],
)
def test_convenience_is_not_a_blocker(run_root, kind):
    """An open vocabulary is how "we found enough" becomes a blocker."""
    with pytest.raises(SystemExit) as excinfo:
        run_state.set_blocker(run_root, kind, "...")
    assert "list is closed" in str(excinfo.value)


def test_a_real_barrier_is_accepted(run_root):
    """The genuine case for this skill: a corpus that is mostly paywalled."""
    entry = run_state.set_blocker(
        run_root, "credentials_or_access", "12 of 30 papers are paywalled")
    assert run_state.active_blocker(run_root)["kind"] == "credentials_or_access"
    assert entry["active"] is True


def test_partial_without_a_blocker_is_refused(run_root):
    """Partial delivery is an honest account of a barrier, not an early exit."""
    report = stop_check(run_root, None, partial=True)
    assert report["may_finalize"] is False
    assert any(r["gate"] == "partial_authorized" for r in report["results"])


def test_partial_with_a_blocker_relaxes_completion_checks(make_run, tmp_path):
    """A blocker relaxes the completion checks — but not delivery.

    This test used to finalize without delivering anything. It cannot any more,
    and that is the intended change: a blocker justifies delivering LESS, never
    leaving what exists on a disk the caller cannot see. A run that passed every
    gate and handed back an empty results folder is exactly the failure the
    delivered gate was added for, so partial delivery now has to deliver too.
    """
    import json

    # This test exercises generic partial delivery, so use quick mode where the
    # infographic is deliberately optional. Deep/broad cannot waive it.
    run_root = make_run(mode="quick")
    (run_root / "state").mkdir(parents=True, exist_ok=True)
    (run_root / "state" / "verification_report.json").write_text(json.dumps(
        {"may_finalize": True, "results": []}))
    run_state.set_blocker(run_root, "credentials_or_access", "papers paywalled")
    run_state.deliver(run_root, tmp_path / "results", partial=True)
    assert stop_check(run_root, None, partial=True)["may_finalize"] is True


def test_partial_never_relaxes_protected_drift(run_root):
    """A blocker explains missing work. It does not make a tampered run
    deliverable."""
    run_state.protect(run_root)
    (run_root / "evidence" / "evidence.jsonl").write_text('{"tampered": true}\n')
    run_state.set_blocker(run_root, "credentials_or_access", "papers paywalled")
    report = stop_check(run_root, None, partial=True)
    assert report["may_finalize"] is False
    assert report["non_relaxable"] >= 1


def test_partial_never_relaxes_an_unresolved_fatal_error(run_root):
    run_state.record_error(run_root, "DATA_INTEGRITY", "corpus checksum differs",
                           severity="fatal")
    run_state.set_blocker(run_root, "irrecoverable_data", "source archive lost")
    assert stop_check(run_root, None, partial=True)["may_finalize"] is False


def test_partial_never_relaxes_a_required_infographic():
    assert "infographic" in run_state.NON_RELAXABLE


def test_clearing_a_blocker_restores_the_full_standard(run_root):
    run_state.set_blocker(run_root, "credentials_or_access", "papers paywalled")
    run_state.clear_blocker(run_root, "institutional access granted")
    assert run_state.active_blocker(run_root) is None
    assert stop_check(run_root, None, partial=True)["may_finalize"] is False


def test_stage_commands_use_flags_that_actually_exist():
    """The exact next action is the point of this module, so a command naming a
    flag that does not exist is worse than no command. The first version invented
    --acquire/--parse/--adjudicate and used --root where the script wants
    --run-root; a resuming context would have been sent to run nothing."""
    import pathlib
    import re

    scripts = pathlib.Path(__file__).resolve().parent.parent / "scripts"
    for stage in STAGES:
        invocations = re.findall(
            r'scripts/(\w+\.py)(.*?)(?=(?:python [^\n;]*scripts/)|$)',
            stage.command,
        )
        for name, arguments in invocations:
            source = (scripts / name).read_text()
            real = set(re.findall(r'"(--[a-z-]+)"', source))
            for flag in re.findall(r"--[a-z-]+", arguments):
                assert flag in real, (
                    f"stage {stage.id} tells the operator to pass {flag} to "
                    f"{name}, which does not accept it")


# --- mode-scoped stages -------------------------------------------------------


def test_quick_mode_does_not_demand_an_infographic(make_run):
    """`quick` is the CLI default, and the contract asks for the infographic
    only in deep/broad. Marking the stage unconditionally required wedged the
    default mode shut: the operator satisfied the contract, the pipeline gate
    still reported work outstanding, and the only way out was building an
    artifact nothing had asked for."""
    run = make_run(mode="quick")
    for name in ("infographic.png", "infographic_spec.json"):
        target = run / "deliverables" / name
        if target.exists():
            target.unlink()

    states = {s.stage.id: s.state for s in stage_statuses(run)}
    assert states["infographic"] == "not_required"

    report = stop_check(run, None)
    pipeline = [r for r in report["results"] if r["gate"] == "pipeline"][0]
    assert "infographic" not in pipeline["detail"]
    gate = [r for r in report["results"] if r["gate"] == "infographic"][0]
    assert gate["ok"] is True


def test_deep_and_broad_still_demand_the_infographic(make_run):
    for mode in ("deep", "broad"):
        run = make_run(mode=mode)
        for name in ("infographic.png", "infographic_spec.json"):
            target = run / "deliverables" / name
            if target.exists():
                target.unlink()
        states = {s.stage.id: s.state for s in stage_statuses(run)}
        assert states["infographic"] == "pending", mode


def test_stage_modes_are_read_from_the_contract_not_duplicated():
    """The figure selection policy already drifted from the contract once by
    being written down twice. The stage table reads required_modes instead."""
    import json
    import pathlib

    contract = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "templates" / "report_contract.json").read_text())
    expected = tuple(contract["visual_abstract"]["required_modes"])
    stage = next(s for s in STAGES if s.id == "infographic")
    assert stage.modes == expected


def test_infographic_stage_requires_a_real_biomni_generate_image_call():
    stage = next(item for item in STAGES if item.id == "infographic")
    assert "state/infographic_generate_image_request.json" in stage.produces
    assert "state/infographic_generation.json" in stage.produces
    assert "state/infographic_media_check.json" in stage.produces
    for required in (
        "--write-tool-request",
        "ToolSearch",
        "select:GenerateImage",
        "wait",
        "actual GenerateImage tool call",
        "media_output_check",
        "--install-image",
        "--record-media-check",
    ):
        assert required in stage.command


def test_a_stage_the_mode_skips_is_never_the_next_action(make_run):
    """not_required must not be confused with outstanding: a resuming context
    that reads it as the next action would build an unwanted artifact forever."""
    run = make_run(mode="quick")
    for name in ("infographic.png", "infographic_spec.json"):
        target = run / "deliverables" / name
        if target.exists():
            target.unlink()
    assert next_action(run)[0] != "infographic"


def test_the_pipeline_gate_can_actually_reach_complete(make_run):
    """Every other stop-check test asserts a refusal. A gate that can only ever
    fail is as useless as one that always passes, so pin the open path too."""
    run = make_run(mode="broad")
    for stage in STAGES:
        for rel in stage.produces:
            path = run / rel
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text("{}" if path.suffix == ".json" else "x")
            else:
                path.mkdir(parents=True, exist_ok=True)
    outstanding = [s.stage.id for s in stage_statuses(run)
                   if s.state not in ("complete", "not_required")
                   and s.stage.id != "verify"]
    assert outstanding == []


def test_adjudication_is_one_batch_per_paper():
    """Adjudication batches are the run's wall-clock. At 4 claims per call a
    27-paper / 25-claim broad review produced up to 189 units, and in-session
    adjudication does them one conversational turn at a time. The block budget
    scales with the claim count, so fewer/larger prompts do not mean thinner
    evidence."""
    import math

    from evidence_first import MODE_DEFAULTS

    for mode in ("deep", "broad"):
        d = MODE_DEFAULTS[mode]
        per_paper = math.ceil(25 / d["claims_per_call"])
        assert per_paper == 1, f"{mode} still splits 25 claims into {per_paper} calls"
        blocks_per_claim = d["max_blocks_per_call"] / 25
        assert blocks_per_claim >= 8, (
            f"{mode} gives each claim only {blocks_per_claim:.1f} candidate blocks")


def test_the_batches_are_written_as_independent_units(tmp_path):
    """They can only be parallelised if each one is self-contained."""
    import json

    from evidence_first import _emit_adjudication_batches

    claims = {f"C-{i:03d}": {"claim_id": f"C-{i:03d}", "claim_text": f"c{i}"}
              for i in range(1, 6)}
    blocks = {f"b{i}": {"block_id": f"b{i}", "paper_id": f"p{i % 3}",
                        "block_type": "sentence", "text": f"s{i}", "page": 1,
                        "section": "Results"} for i in range(30)}
    cands = [{"paper_id": b["paper_id"], "claim_id": f"C-{(i % 5) + 1:03d}",
              "block_id": b["block_id"], "retrieval_score": 1.0 / (i + 1)}
             for i, b in enumerate(blocks.values())]
    papers = {f"p{i}": {"paper_id": f"p{i}", "title": f"P{i}"} for i in range(3)}

    n = _emit_adjudication_batches(tmp_path, cands, claims, blocks, papers, 25, 200)
    assert n == 3, "expected one batch per paper"
    for path in (tmp_path / "evidence" / "adjudication_batches").glob("*.json"):
        batch = json.loads(path.read_text())
        # Self-contained: the prompt is here, not fetched from somewhere else.
        assert batch["prompt"] and batch["claim_ids"] and batch["output_path"]
        assert batch["output_path"].endswith(f"{batch['batch_id']}.jsonl")
        assert "one JSON object per line" in batch["instructions"]


def test_skill_uses_managed_machines_without_prod_subagents():
    import pathlib

    skill = (pathlib.Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
    background = skill.split("Background execution is the default", 1)[1][:1800]
    assert "five `ManageMachine` machines" in background
    assert "adaptively pilots 2, 3, 4, 6, 8, 12" in background
    assert "throughput plateaus" in background
    assert "general-purpose `Agent`" not in skill
    assert "external key" not in skill.lower()


def test_the_skill_states_a_runtime_expectation():
    """ "Never promise an exact runtime" is right, and it left the operator with
    no idea whether 2 hours was normal. A range plus what drives it is not a
    promise."""
    import pathlib

    skill = (pathlib.Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
    assert "What actually costs time" in skill
    assert "background" in skill.split("What actually costs time")[1][:900]


def test_long_stages_default_to_biomni_tracked_background_execution():
    import pathlib

    skill = (pathlib.Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
    section = skill.split("Background execution is the default", 1)[1][:1800]
    assert "MUST" in section
    assert "run_in_background=true" in section
    assert "background_name" in section
    assert "never shell `&`, `nohup`" in section

    for stage_id in ("acquire", "parse"):
        stage = next(item for item in STAGES if item.id == stage_id)
        assert "run_in_background=true" in stage.command


def test_native_biomni_is_the_only_documented_reasoning_path():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    skill = (root / "SKILL.md").read_text()
    guidance = "\n".join([
        skill,
        (root / "references" / "modes_and_intake.md").read_text(),
        (root / "references" / "performance.md").read_text(),
        (root / "templates" / "review_brief.md").read_text(),
    ])
    lowered = guidance.lower()

    assert "biomni performs every reasoning stage natively" in lowered
    assert "disabled by default" not in lowered
    for forbidden in (
        "openai",
        "gemini",
        "ollama",
        "api key",
        "cloud provider",
        "direct provider",
        "direct-provider",
        "run-direct",
    ):
        assert forbidden not in lowered

    manifest = json.loads((root / "templates" / "run_manifest.json").read_text())
    assert manifest["config"]["backend"] == "none"
    assert manifest["config"]["adaptive_managed_concurrency"] is True
    assert manifest["config"]["max_processes_per_machine"] == 16
    assert manifest["config"]["concurrency_ramp"] == [2, 3, 4, 6, 8, 12, 16]
    assert manifest["config"]["native_task_packing"] is True

    for stage_id in ("adjudicate", "entailment", "narrative"):
        command = next(item.command for item in STAGES if item.id == stage_id)
        assert "--backend openai" not in command
        assert "Agent(" not in command
        assert "native Biomni coordinator" in command

    acquire = next(item.command for item in STAGES if item.id == "acquire")
    assert "ManageMachine" in acquire
    assert "machine_id" in acquire


def test_the_narrative_work_is_handed_over_as_discrete_units(run_root):
    """Adjudication batches arrive as files with "run these in parallel" printed
    at the moment of handover. The narratives had only a paragraph in SKILL.md —
    the same one-indirection-away failure that kept the antibody shape guide out
    of the renderer. Emit them the same way."""
    from grounded_quotes import build, emit_narrative_tasks

    data, _failures = build(run_root)
    n = emit_narrative_tasks(run_root, data)
    assert n > 0

    import json
    for path in (run_root / "deliverables" / "narrative_tasks").glob("*.json"):
        task = json.loads(path.read_text())
        # Self-contained: the quotes are here, not fetched from elsewhere.
        assert task["claim_text"]
        assert task["support_label"]
        assert "supporting_anchors" in task
        assert task["facets_required"]


def test_the_narrative_stage_command_uses_native_task_packs():
    stage = next(s for s in STAGES if s.id == "narrative")
    assert "native_packs/narratives" in stage.command
    assert "separate outputs" in stage.command
    assert "Agent(" not in stage.command
    assert "narrative_tasks" in stage.command


def test_narrative_task_emission_never_costs_the_grounding_step(run_root, capsys):
    """The task files help parallelise the narratives; the grounded quotes are
    the product. Emission runs after the quotes are written, so letting it raise
    would fail the step with its real output already on disk."""
    import builtins

    import grounded_quotes

    real_import = builtins.__import__

    def boom(name, *args, **kwargs):
        if name == "support_policy":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = boom
    try:
        rc = grounded_quotes.main(["--root", str(run_root)])
    finally:
        builtins.__import__ = real_import

    assert rc == 0, "a broken task emission failed the whole grounding step"
    assert (run_root / "deliverables" / "grounded_quotes.json").exists()
    assert "not emitted" in capsys.readouterr().err
