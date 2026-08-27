"""Final delivery state must be derived from canonical artifacts, not memory."""

from __future__ import annotations

import csv
import hashlib
import json


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_refresh_repairs_slc33a1_style_metric_drift(run_root):
    from reconcile_run import refresh

    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    manifest.setdefault("metrics", {})["evidence_accepted"] = 108
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    stats_path = run_root / "deliverables" / "review_stats.json"
    stats = _json(stats_path)
    stats["evidence_accepted"] = 108
    stats_path.write_text(json.dumps(stats), encoding="utf-8")

    receipt, failures = refresh(run_root, write=True)

    assert failures == []
    assert receipt["counts"]["evidence_accepted"] == 3
    assert _json(manifest_path)["metrics"]["evidence_accepted"] == 3
    assert _json(stats_path)["evidence_accepted"] == 3


def test_grounded_claim_count_uses_the_shared_support_policy(run_root):
    """Indirect, conflicted, and refuted claims still carry grounding anchors."""
    from reconcile_run import refresh

    matrix_path = run_root / "deliverables" / "claim_evidence_matrix.csv"
    with matrix_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row, state in zip(
        rows,
        ("C1_INDIRECT", "C_CONFLICTED", "C_REFUTED"),
        strict=True,
    ):
        row["support_state"] = state
    with matrix_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    receipt, failures = refresh(run_root, write=True)

    assert failures == []
    assert receipt["counts"]["claims_grounded"] == 3


def test_reconciliation_does_not_touch_unchanged_manifest_or_stats(run_root):
    from reconcile_run import refresh

    manifest = run_root / "run_manifest.json"
    stats = run_root / "deliverables" / "review_stats.json"
    _receipt, failures = refresh(run_root, write=True)
    assert failures == []
    before = (manifest.stat().st_mtime_ns, stats.stat().st_mtime_ns)

    _receipt, failures = refresh(run_root, write=True)

    assert failures == []
    assert (manifest.stat().st_mtime_ns, stats.stat().st_mtime_ns) == before


def test_verify_catches_cross_artifact_evidence_loss(run_root):
    from reconcile_run import refresh

    with (run_root / "deliverables" / "evidence_table.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    with (run_root / "deliverables" / "evidence_table.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows[:-1])

    _receipt, failures = refresh(run_root, write=False)

    assert any("evidence_table.csv" in failure for failure in failures)


def test_adaptive_run_requires_managed_execution_receipt(run_root):
    from reconcile_run import refresh

    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    manifest["config"]["adaptive_managed_concurrency"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = refresh(run_root, write=False)

    assert any("managed_machines" in failure for failure in failures)


def test_quick_run_does_not_require_managed_execution_receipt(run_root):
    from reconcile_run import refresh

    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    manifest["mode"] = "quick"
    manifest["config"]["adaptive_managed_concurrency"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = refresh(run_root, write=False)

    assert not any("managed_machines" in failure for failure in failures)


def test_large_deep_run_cannot_silently_disable_managed_execution(
    run_root, monkeypatch
):
    import reconcile_run

    monkeypatch.setattr(reconcile_run, "MANAGED_EXECUTION_MIN_PAPERS", 1)
    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    manifest["mode"] = "deep"
    manifest["config"]["adaptive_managed_concurrency"] = False
    manifest["config"].pop("managed_execution_waiver", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = reconcile_run.refresh(run_root, write=False)

    assert any("disabled adaptive managed execution" in failure for failure in failures)


def test_managed_execution_waiver_must_be_explicit_but_is_honored(
    run_root, monkeypatch
):
    import reconcile_run

    monkeypatch.setattr(reconcile_run, "MANAGED_EXECUTION_MIN_PAPERS", 1)
    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    manifest["mode"] = "deep"
    manifest["config"]["adaptive_managed_concurrency"] = False
    manifest["config"]["managed_execution_waiver"] = {
        "approved_by_user": True,
        "reason": "managed machines unavailable",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = reconcile_run.refresh(run_root, write=False)

    assert not any("disabled adaptive managed execution" in failure for failure in failures)


def test_plain_text_managed_execution_waiver_is_not_enough(run_root):
    from reconcile_run import refresh

    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    manifest["mode"] = "broad"
    manifest["config"]["adaptive_managed_concurrency"] = False
    manifest["config"]["managed_execution_waiver"] = "machines unavailable"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = refresh(run_root, write=False)

    assert any("user-approved" in failure for failure in failures)


def test_even_small_broad_runs_require_managed_execution_or_a_waiver(run_root):
    from reconcile_run import refresh

    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    manifest["mode"] = "broad"
    manifest["config"]["adaptive_managed_concurrency"] = False
    manifest["config"].pop("managed_execution_waiver", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = refresh(run_root, write=False)

    assert any("disabled adaptive managed execution" in failure for failure in failures)


def test_managed_receipt_must_match_adaptive_machine_plan_and_skill(run_root):
    from reconcile_run import refresh

    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    provenance = manifest["skill_provenance"]
    manifest["config"].update({
        "adaptive_managed_concurrency": True,
        "managed_machines": 5,
        "managed_execution_waiver": None,
    })
    manifest["metrics"] = {
        "managed_machines": {
            "machine_count": 2,
            "machines": [{"machine_id": "worker-0"}, {"machine_id": "worker-1"}],
            "exchange_mode": "object-store",
            "skill_bundle_sha256": provenance["skill_bundle_sha256"],
            "skill_git_commit": provenance["git_commit"],
        }
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = refresh(run_root, write=False)

    assert any("expected 1" in failure for failure in failures)


def test_managed_receipt_must_use_the_provenance_skill_bundle(run_root):
    from reconcile_run import refresh

    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    provenance = manifest["skill_provenance"]
    manifest["config"].update({
        "adaptive_managed_concurrency": True,
        "managed_machines": 5,
        "managed_execution_waiver": None,
    })
    manifest.setdefault("metrics", {})["managed_machines"] = {
            "machine_count": 1,
            "machines": [{"machine_id": "worker-0"}],
            "exchange_mode": "object-store",
            "skill_bundle_sha256": "b" * 64,
            "skill_git_commit": provenance["git_commit"],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = refresh(run_root, write=False)

    assert any("skill bundle recorded in provenance" in failure for failure in failures)


def test_complete_managed_execution_receipt_passes(run_root):
    from reconcile_run import refresh

    manifest_path = run_root / "run_manifest.json"
    manifest = _json(manifest_path)
    provenance = manifest["skill_provenance"]
    manifest["config"].update({
        "adaptive_managed_concurrency": True,
        "managed_machines": 5,
        "managed_execution_waiver": None,
    })
    manifest.setdefault("metrics", {})["managed_machines"] = {
        "machine_count": 1,
        "machines": [{"machine_id": "worker-0"}],
        "background_launches": [{
            "machine_id": "worker-0",
            "run_in_background": True,
            "background_name": "literature-review-fixture-worker-0",
        }],
        "exchange_mode": "object-store",
        "skill_bundle_sha256": provenance["skill_bundle_sha256"],
        "skill_git_commit": provenance["git_commit"],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = refresh(run_root, write=True)

    assert failures == []


def test_reconciliation_requires_skill_provenance_receipt(run_root):
    from reconcile_run import refresh

    (run_root / "state" / "skill_provenance.json").unlink()

    _receipt, failures = refresh(run_root, write=False)

    assert any("skill_provenance.json is missing" in failure for failure in failures)


def test_selected_figure_must_export_or_record_a_reason(run_root):
    from reconcile_run import refresh

    path = run_root / "deliverables" / "figures_cited" / "figures_manifest.json"
    manifest = _json(path)
    manifest["selected_figure_ids"] = [
        {"paper_id": "10.1000/alpha", "figure_id": "never-accounted-for"}
    ]
    path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = refresh(run_root, write=False)

    assert any("selected figure" in failure for failure in failures)


def test_exported_figure_requires_structured_crop_quality_pass(run_root):
    from reconcile_run import refresh

    path = run_root / "deliverables" / "figures_cited" / "figures_manifest.json"
    manifest = _json(path)
    manifest["figures"][0].pop("quality_check", None)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    _receipt, failures = refresh(run_root, write=False)

    assert any("crop-quality receipt" in failure for failure in failures)


def test_stale_native_assembly_receipt_blocks_reconciliation(run_root):
    from reconcile_run import refresh

    destination = run_root / "evidence" / "entailment.jsonl"
    receipt_dir = run_root / "state" / "assemblies"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": 2,
        "kind": "entailment",
        "task_count": 3,
        "output_count": 3,
        "task_sha256": {f"task-{i}.json": "a" * 64 for i in range(3)},
        "output_sha256": {f"out-{i}.json": "b" * 64 for i in range(3)},
        "destination": "evidence/entailment.jsonl",
        "destination_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "complete": True,
        "units": [],
    }
    (receipt_dir / "entailment.json").write_text(json.dumps(receipt))
    destination.write_text(destination.read_text() + "\n")

    _receipt, failures = refresh(run_root, write=False)

    assert any("assembly destination hash" in failure for failure in failures)
