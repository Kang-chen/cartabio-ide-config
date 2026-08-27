from __future__ import annotations

import json

import pytest


COMMIT = "c" * 40


def _skill(tmp_path):
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: fixture\n---\n", encoding="utf-8")
    (root / "scripts" / "worker.py").write_text("print('ok')\n", encoding="utf-8")
    return root


def test_capture_binds_manifest_to_exact_package_bytes(tmp_path):
    from skill_provenance import capture, problems

    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    skill = _skill(tmp_path)

    record = capture(run, skill, git_commit=COMMIT)

    receipt = json.loads((run / "state" / "skill_provenance.json").read_text())
    manifest = json.loads((run / "run_manifest.json").read_text())
    assert record["git_commit"] == COMMIT
    assert record["git_commit_source"] == "argument"
    assert receipt == record
    assert manifest["skill_provenance"] == record
    assert problems(run, skill) == []


def test_verification_detects_skill_file_drift(tmp_path):
    from skill_provenance import capture, problems

    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    skill = _skill(tmp_path)
    capture(run, skill, git_commit=COMMIT)

    (skill / "scripts" / "worker.py").write_text("print('changed')\n", encoding="utf-8")

    assert any("changed after provenance capture" in item for item in problems(run, skill))


def test_verification_detects_manifest_receipt_disagreement(tmp_path):
    from skill_provenance import capture, problems

    run = tmp_path / "run"
    run.mkdir()
    manifest_path = run / "run_manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    skill = _skill(tmp_path)
    capture(run, skill, git_commit=COMMIT)
    manifest = json.loads(manifest_path.read_text())
    manifest["skill_provenance"]["git_commit"] = "d" * 40
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert any("Git commit differs" in item for item in problems(run, skill))


def test_capture_uses_git_managed_deployment_metadata(tmp_path):
    from skill_provenance import (
        DEPLOYMENT_METADATA_NAME,
        capture,
        directory_sha256,
    )

    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    skill = _skill(tmp_path)
    directory_hash, file_count = directory_sha256(skill)
    (skill / DEPLOYMENT_METADATA_NAME).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "skill": "literature-deep-review",
                "skill_commit": COMMIT,
                "skill_directory_sha256": directory_hash,
                "file_count": file_count,
            }
        ),
        encoding="utf-8",
    )

    record = capture(run, skill)

    assert record["git_commit"] == COMMIT
    assert record["git_commit_source"] == "deployment_metadata"


def test_capture_cannot_refresh_provenance_after_skill_drift(tmp_path):
    from skill_provenance import capture

    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    skill = _skill(tmp_path)
    capture(run, skill, git_commit=COMMIT)
    receipt = (run / "state" / "skill_provenance.json").read_bytes()

    (skill / "scripts" / "worker.py").write_text("print('patched')\n")

    with pytest.raises(ValueError, match="immutable"):
        capture(run, skill, git_commit=COMMIT)
    assert (run / "state" / "skill_provenance.json").read_bytes() == receipt


def test_idempotent_capture_does_not_rewrite_origin_receipt(tmp_path):
    from skill_provenance import capture

    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    skill = _skill(tmp_path)
    first = capture(run, skill, git_commit=COMMIT)
    receipt = run / "state" / "skill_provenance.json"
    before = receipt.stat().st_mtime_ns

    second = capture(run, skill, git_commit=COMMIT)

    assert second == first
    assert receipt.stat().st_mtime_ns == before


def test_deployment_metadata_rejects_a_hot_patched_skill(tmp_path):
    from skill_provenance import (
        DEPLOYMENT_METADATA_NAME,
        capture,
        directory_sha256,
    )

    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    skill = _skill(tmp_path)
    directory_hash, file_count = directory_sha256(skill)
    (skill / DEPLOYMENT_METADATA_NAME).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "skill": "literature-deep-review",
                "skill_commit": COMMIT,
                "skill_directory_sha256": directory_hash,
                "file_count": file_count,
            }
        )
    )
    (skill / "scripts" / "worker.py").write_text("print('hot patch')\n")

    with pytest.raises(ValueError, match="differ from.*deployment metadata"):
        capture(run, skill)


def test_committed_upgrade_preserves_origin_and_freezes_scientific_artifacts(
    tmp_path,
):
    from skill_provenance import capture, problems, record_upgrade

    run = tmp_path / "run"
    (run / "corpus").mkdir(parents=True)
    (run / "evidence").mkdir(parents=True)
    (run / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    (run / "corpus" / "claims.jsonl").write_text(
        '{"claim_id":"C-1"}\n', encoding="utf-8"
    )
    evidence = run / "evidence" / "evidence.jsonl"
    evidence.write_text('{"evidence_id":"E-1"}\n', encoding="utf-8")
    (run / "evidence" / "entailment.jsonl").write_text(
        '{"evidence_id":"E-1","entailment":"yes"}\n', encoding="utf-8"
    )
    skill = _skill(tmp_path)
    origin = capture(run, skill, git_commit=COMMIT)
    origin_bytes = (run / "state" / "skill_provenance.json").read_bytes()

    (skill / "scripts" / "worker.py").write_text("print('fixed')\n")
    upgrade = record_upgrade(
        run,
        skill,
        git_commit="d" * 40,
        reason="Fix the final reconciliation counter",
        resume_from_stage="reconcile",
    )

    assert upgrade["from_identity"]["git_commit"] == COMMIT
    assert upgrade["to_identity"]["git_commit"] == "d" * 40
    assert (run / "state" / "skill_provenance.json").read_bytes() == origin_bytes
    assert (
        json.loads((run / "run_manifest.json").read_text())["skill_provenance"]
        == origin
    )
    assert problems(run, skill) == []

    evidence.write_text('{"evidence_id":"tampered"}\n', encoding="utf-8")
    assert any(
        "upgrade-protected artifact changed" in item for item in problems(run, skill)
    )
