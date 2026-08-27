"""Verified is not delivered.

A run built every artifact, passed all nine gates against its LOCAL paths,
reported may_finalize=yes, and handed back an empty results folder. The gates
were answering a question about files the caller could not see. The pipeline
ended at `verify` and no stage produced anything outside the run root.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

import pytest

import run_state
from run_state import (
    deliver,
    delivery_state,
    finalize_run,
    report_pdf_filename,
    stop_check,
)


def _passing_verdict(run):
    (run / "state").mkdir(parents=True, exist_ok=True)
    (run / "deliverables").mkdir(parents=True, exist_ok=True)
    review = run / "deliverables" / "review.md"
    report = run / "deliverables" / "report.pdf"
    if not review.exists():
        review.write_text("# Review\n")
    if not report.exists():
        report.write_bytes(b"%PDF fixture\n")
    (run / "state" / "verification_report.json").write_text(json.dumps(
        {"may_finalize": True,
         "results": [{"gate": "review", "ok": True, "detail": ""}]}))


def test_stop_check_fails_when_nothing_was_delivered(run_root):
    report = stop_check(run_root, None)
    gate = [r for r in report["results"] if r["gate"] == "delivered"][0]
    assert gate["ok"] is False
    assert "local disk" in gate["detail"]


def test_delivery_cannot_be_relaxed_by_a_blocker(run_root):
    """A blocker justifies delivering LESS, never leaving what exists on a disk
    the caller cannot see."""
    assert "delivered" in run_state.NON_RELAXABLE
    run_state.set_blocker(run_root, "platform_unavailable", "results mount down")
    report = stop_check(run_root, None, partial=True)
    assert report["may_finalize"] is False


def test_finalize_runs_reconciliation_preflight_copy_and_attestation_in_order(
    run_root, tmp_path, monkeypatch
):
    import reconcile_run

    events = []

    def reconcile(_root, *, write):
        events.append(("reconcile", write))
        return {}, []

    def check(_root, _pdf, partial=False, *, require_delivery=True):
        events.append(("check", require_delivery))
        return {"may_finalize": True, "results": []}

    def copy(_root, _dest, _pdf, **kwargs):
        events.append(("copy", kwargs["prepared"]))
        return {"delivered": True, "copied": [], "failures": []}

    def attest(_root):
        events.append(("attest", True))
        return {"delivered": True, "copied": [], "failures": []}

    monkeypatch.setattr(reconcile_run, "refresh", reconcile)
    monkeypatch.setattr(run_state, "stop_check", check)
    monkeypatch.setattr(run_state, "deliver", copy)
    monkeypatch.setattr(run_state, "finalize_delivery", attest)

    report = finalize_run(
        run_root,
        tmp_path / "results",
        run_root / "deliverables" / "report.pdf",
    )

    assert report["delivered"] is True
    assert events == [
        ("reconcile", True),
        ("check", False),
        ("copy", True),
        ("check", True),
        ("attest", True),
    ]


def test_long_run_can_finalize_after_manifest_metrics_refresh(
    run_root, tmp_path, monkeypatch
):
    """Reconciliation may update metrics hours after search without staling it."""
    (run_root / "corpus" / "ingestion.json").write_text(
        '{"since_offset":0}\n', encoding="utf-8"
    )
    (run_root / "deliverables" / "review.md").write_text(
        "# Verified review\n", encoding="utf-8"
    )
    report_pdf = run_root / "deliverables" / "report.pdf"
    report_pdf.write_bytes(b"%PDF verified fixture\n")
    for relative in (
        "deliverables/infographic_spec.json",
        "state/infographic_generate_image_request.json",
        "state/infographic_generation.json",
        "state/infographic_media_check.json",
    ):
        path = run_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    now = time.time()
    os.utime(run_root / "state" / "intake_snapshot.json", (now - 120, now - 120))
    for relative in (
        "corpus/references.jsonl",
        "corpus/ingestion.json",
        "corpus/corpus_ledger.json",
    ):
        os.utime(run_root / relative, (now - 60, now - 60))
    os.utime(run_root / "run_manifest.json", (now, now))
    monkeypatch.setattr(run_state, "GATES", ())
    incomplete = [
        (status.stage.id, status.state, status.missing, status.stale_against)
        for status in run_state.stage_statuses(run_root)
        if status.state not in {"complete", "not_required"}
        and status.stage.id not in {"verify", "deliver"}
    ]
    assert incomplete == []

    result = finalize_run(
        run_root,
        tmp_path / "bundle",
        report_pdf,
        report_root=tmp_path / "results",
    )

    assert result["delivered"] is True
    delivered, detail = delivery_state(run_root)
    assert delivered is True
    assert "copied to" in detail


def test_deliver_copies_and_verifies(run_root, tmp_path):
    _passing_verdict(run_root)
    dest = tmp_path / "results"
    report = deliver(run_root, dest)
    assert report["delivered"] is True
    assert report["copied"]
    for item in report["copied"]:
        assert item["bytes"] > 0 or item["valid_empty_ledger"] is True
    delivered, detail = delivery_state(run_root)
    assert delivered is True and "copied to" in detail


def test_delivery_publishes_prompt_named_pdf_in_results_root(run_root, tmp_path):
    _passing_verdict(run_root)
    results_root = tmp_path / "results"
    bundle = results_root / "literature-deep-review" / "run-001"

    report = deliver(run_root, bundle, report_root=results_root)

    expected_name = (
        "GRN-as-a-therapeutic-target-in-frontotemporal-dementia-"
        "literature-review.pdf"
    )
    visible = results_root / expected_name
    assert report_pdf_filename(run_root) == expected_name
    assert visible.read_bytes() == (
        run_root / "deliverables" / "report.pdf"
    ).read_bytes()
    assert (bundle / "deliverables" / "report.pdf").exists()
    assert pathlib.Path(report["visible_report"]["path"]) == visible
    assert report["visible_report"]["visibility"] == "results_root"


def test_report_filename_falls_back_to_the_recorded_question(run_root):
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["title"] = "REPLACE report title"
    manifest["question"] = "Is SLC33A1 a tractable target for neuropathy?"
    manifest_path.write_text(json.dumps(manifest))

    assert report_pdf_filename(run_root) == (
        "Is-SLC33A1-a-tractable-target-for-neuropathy-literature-review.pdf"
    )


def test_visible_report_does_not_overwrite_a_different_existing_pdf(
    run_root, tmp_path
):
    _passing_verdict(run_root)
    results_root = tmp_path / "results"
    results_root.mkdir()
    preferred = results_root / report_pdf_filename(run_root)
    preferred.write_bytes(b"%PDF older report\n")

    report = deliver(run_root, tmp_path / "bundle", report_root=results_root)
    visible = pathlib.Path(report["visible_report"]["path"])

    assert preferred.read_bytes() == b"%PDF older report\n"
    assert visible.parent == results_root
    assert visible != preferred
    assert visible.stem.startswith(preferred.stem + "-")


def test_delivery_includes_infographic_tool_provenance(run_root, tmp_path):
    _passing_verdict(run_root)
    state = run_root / "state"
    (state / "infographic_generate_image_request.json").write_text(
        '{"tool":"GenerateImage"}')
    (state / "infographic_generation.json").write_text(
        '{"tool":"GenerateImage","image_sha256":"fixture"}')

    destination = tmp_path / "results"
    deliver(run_root, destination)
    assert (destination / "state" /
            "infographic_generate_image_request.json").exists()
    assert (destination / "state" / "infographic_generation.json").exists()


def test_delivery_includes_the_evidence_backed_infographic_spec(run_root, tmp_path):
    _passing_verdict(run_root)
    (run_root / "deliverables" / "infographic_spec.json").write_text(
        '{"PROFILE":"target","_verified":true}'
    )
    destination = tmp_path / "results"

    deliver(run_root, destination)

    assert (destination / "deliverables" / "infographic_spec.json").exists()


def test_delivery_flips_the_gate(run_root, tmp_path):
    _passing_verdict(run_root)
    deliver(run_root, tmp_path / "results")
    report = stop_check(run_root, None)
    gate = [r for r in report["results"] if r["gate"] == "delivered"][0]
    assert gate["ok"] is True


def test_a_truncated_copy_is_a_failure(run_root, tmp_path, monkeypatch):
    """A short write to an object-store mount looks like success to shutil, so
    the copy is re-read rather than trusted."""
    _passing_verdict(run_root)
    dest = tmp_path / "results"

    real_copy = run_state.shutil.copy2

    def truncating(src, dst, *a, **k):
        real_copy(src, dst, *a, **k)
        import pathlib
        pathlib.Path(dst).write_bytes(b"")      # arrives empty
        return dst

    monkeypatch.setattr(run_state.shutil, "copy2", truncating)
    report = deliver(run_root, dest)
    assert report["delivered"] is False
    assert report["failures"]


def test_delivery_revalidates_destination_instead_of_trusting_receipt(run_root, tmp_path):
    _passing_verdict(run_root)
    dest = tmp_path / "results"
    report = deliver(run_root, dest)
    target = pathlib.Path(report["copied"][0]["path"])
    target.unlink()
    delivered, detail = delivery_state(run_root)
    assert delivered is False
    assert "destination missing" in detail


def test_delivery_revalidates_source_after_rebuild(run_root, tmp_path):
    _passing_verdict(run_root)
    deliver(run_root, tmp_path / "results")
    (run_root / "deliverables" / "review.md").write_text("# rebuilt\n")
    delivered, detail = delivery_state(run_root)
    assert delivered is False
    assert "source" in detail and ("size changed" in detail or "digest changed" in detail)


def test_final_attestation_is_the_successful_post_delivery_report(run_root, tmp_path):
    _passing_verdict(run_root)
    dest = tmp_path / "results"
    deliver(run_root, dest)
    final = {"may_finalize": True, "results": [
        {"gate": "delivered", "ok": True, "detail": "destination verified"}
    ]}
    (run_root / "state" / "verification_report.json").write_text(json.dumps(final))
    run_state.finalize_delivery(run_root)
    delivered = json.loads(
        (dest / "state" / "verification_report.json").read_text()
    )
    assert delivered["may_finalize"] is True
    assert (dest / run_state.DELIVERY_RECEIPT_NAME).exists()


def test_a_failing_run_is_never_delivered(run_root, tmp_path):
    (run_root / "state").mkdir(parents=True, exist_ok=True)
    (run_root / "state" / "verification_report.json").write_text(json.dumps(
        {"may_finalize": False,
         "results": [{"gate": "entailment", "ok": False, "detail": "boom"}]}))
    with pytest.raises(SystemExit, match="refusing to deliver"):
        deliver(run_root, tmp_path / "results")


def test_deliver_is_a_stage_so_an_undelivered_run_is_incomplete():
    ids = [s.id for s in run_state.STAGES]
    assert ids[-1] == "deliver", "delivery must be the last stage, after verify"


def test_re_running_the_gate_does_not_invalidate_the_delivery(run_root, tmp_path):
    """The deliver stage first consumed state/verification_report.json — which
    --stop-check REWRITES on every run. Delivering and then re-running the check
    that requires delivery marked the delivery stale, so the pipeline gate could
    never open again. Delivery depends on the deliverables, not on the gate.
    """
    import time

    _passing_verdict(run_root)
    (run_root / "deliverables").mkdir(parents=True, exist_ok=True)
    (run_root / "deliverables" / "review.md").write_text("# review\n")
    deliver(run_root, tmp_path / "results")

    time.sleep(run_state.STALE_TOLERANCE_SECONDS + 2)
    _passing_verdict(run_root)          # the second --stop-check

    state = {s.stage.id: s.state for s in run_state.stage_statuses(run_root)}
    assert state["deliver"] == "complete"


def test_rebuilding_after_delivery_does_make_it_stale(run_root, tmp_path):
    """The other half: delivery IS invalid once what was delivered is rebuilt."""
    import time

    _passing_verdict(run_root)
    (run_root / "deliverables").mkdir(parents=True, exist_ok=True)
    (run_root / "deliverables" / "review.md").write_text("# review\n")
    deliver(run_root, tmp_path / "results")

    time.sleep(run_state.STALE_TOLERANCE_SECONDS + 2)
    (run_root / "deliverables" / "review.md").write_text("# rebuilt\n")

    state = {s.stage.id: s.state for s in run_state.stage_statuses(run_root)}
    assert state["deliver"] == "stale"
