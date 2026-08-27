"""Parallel model work remains deterministic and fully assembled."""
from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest

from adjudication_batches import run_provider_batches
from batch_tasks import (
    DEFAULT_DIRECT_JOBS,
    MAX_DIRECT_JOBS,
    assemble_adjudications,
    assemble_entailment,
    assemble_narratives,
    emit_entailment_tasks,
    pack_native,
    run_direct_tasks,
    stage_workers,
)
from llm_adjudicator import AdjudicationError
from evidence_first import _native_adjudication_coverage
from semantic_verification import (
    FIRST_PASS_FIELDS,
    validate_verdict,
    verdict_is_acceptable,
)
from verify_entailment import verify


def _batches(count: int):
    return [
        (
            f"p{index}",
            [{"claim_id": f"C-{index:03d}", "claim_text": f"claim {index}"}],
            [{
                "block_id": f"p{index}:S:1",
                "block_type": "sentence",
                "text": f"result {index}",
                "page": 1,
                "section": "Results",
            }],
        )
        for index in range(count)
    ]


def test_provider_batches_run_concurrently_but_return_in_input_order(tmp_path):
    lock = threading.Lock()
    active = 0
    maximum = 0

    def fake(_backend, _model, prompt):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        paper_id = next(f"p{i}" for i in range(4) if f'"paper_id": "p{i}"' in prompt)
        return [{"paper_id": paper_id}], {"request_id": paper_id}

    results = run_provider_batches(
        _batches(4),
        {f"p{i}": {"paper_id": f"p{i}"} for i in range(4)},
        backend="openai",
        model="test",
        cache_dir=tmp_path / "cache",
        jobs=4,
        adjudicate_fn=fake,
    )
    assert maximum > 1
    assert [result.paper_id for result in results] == ["p0", "p1", "p2", "p3"]


def test_provider_batch_cache_avoids_repeat_calls(tmp_path):
    calls = 0

    def fake(_backend, _model, _prompt):
        nonlocal calls
        calls += 1
        return [], {}

    kwargs = {
        "backend": "openai",
        "model": "test",
        "cache_dir": tmp_path / "cache",
        "jobs": 2,
        "adjudicate_fn": fake,
    }
    run_provider_batches(_batches(2), {"p0": {}, "p1": {}}, **kwargs)
    cached = run_provider_batches(_batches(2), {"p0": {}, "p1": {}}, **kwargs)
    assert calls == 2
    assert all(result.cache_hit for result in cached)


def test_one_provider_failure_does_not_scramble_other_batches(tmp_path):
    def fake(_backend, _model, prompt):
        if '"paper_id": "p1"' in prompt:
            raise AdjudicationError("rate limited")
        return [], {}

    results = run_provider_batches(
        _batches(3),
        {f"p{i}": {"paper_id": f"p{i}"} for i in range(3)},
        backend="openai",
        model="test",
        cache_dir=tmp_path / "cache",
        jobs=3,
        adjudicate_fn=fake,
    )
    assert [result.paper_id for result in results] == ["p0", "p1", "p2"]
    assert not results[0].error and "rate limited" in results[1].error
    assert not results[2].error


def _acceptable_output(task: dict) -> dict:
    return {
        "entailment": "yes",
        "direction_match": True,
        "population_match": True,
        "intervention_match": True,
        "outcome_match": True,
        "result_type": "original",
        "scope_overreach": False,
        "reviewer": "fixture-reviewer",
        "rationale": "The quote directly states the scoped claim.",
        "verified_at": "2026-07-29T12:00:00Z",
    }


def test_partial_entailment_never_carries_a_claim():
    verdict = _acceptable_output({})
    verdict["entailment"] = "partial"

    assert verdict_is_acceptable(verdict) is False
    assert any("partial" in error for error in validate_verdict(verdict))


def test_yes_entailment_requires_every_match_axis():
    verdict = _acceptable_output({})
    verdict["outcome_match"] = False

    assert verdict_is_acceptable(verdict) is False
    assert any("outcome_match" in error for error in validate_verdict(verdict))


def test_entailment_tasks_are_blinded_and_assemble_complete_run(run_root):
    count = emit_entailment_tasks(run_root)
    tasks = sorted((run_root / "evidence" / "entailment_tasks").glob("*.json"))
    assert count == len(tasks) == 3
    for task_path in tasks:
        task = json.loads(task_path.read_text())
        assert not (set(task["payload"]) & FIRST_PASS_FIELDS)
        output = run_root / task["output_path"]
        output.write_text(json.dumps(_acceptable_output(task)))
    path, assembled = assemble_entailment(run_root)
    assert path.exists() and assembled == 3
    failures, _notes, stats = verify(run_root, require_entailment=True)
    assert failures == []
    assert stats["anchors_missing"] == 0


def test_native_worker_handoff_is_the_emitted_default(run_root, capsys):
    from grounded_quotes import build, emit_narrative_tasks

    emit_entailment_tasks(run_root)
    entailment_message = capsys.readouterr().out
    assert "native Biomni is default" in entailment_message
    assert "run-direct defaults" not in entailment_message

    grounded, failures = build(run_root)
    assert failures == []
    emit_narrative_tasks(run_root, grounded)
    narrative_message = capsys.readouterr().out
    assert "native Biomni is default" in narrative_message
    assert "run-direct defaults" not in narrative_message


def test_direct_entailment_pool_is_parallel_cached_and_assembled(run_root):
    emit_entailment_tasks(run_root)
    lock = threading.Lock()
    active = 0
    maximum = 0
    calls = 0

    def fake(_backend, _model, _prompt, **kwargs):
        nonlocal active, maximum, calls
        assert kwargs["schema_name"] == "anchor_entailment"
        with lock:
            active += 1
            calls += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return _acceptable_output({}), {"request_id": str(calls)}

    path, count = run_direct_tasks(
        run_root,
        "entailment",
        backend="openai",
        model="test",
        jobs=DEFAULT_DIRECT_JOBS,
        request_fn=fake,
    )
    assert path.name == "entailment_verdicts"
    assert count == 3 and maximum > 1
    assert assemble_entailment(run_root)[1] == 3

    run_direct_tasks(
        run_root,
        "entailment",
        backend="openai",
        model="test",
        jobs=DEFAULT_DIRECT_JOBS,
        request_fn=fake,
    )
    assert calls == 3


def test_direct_worker_limit_is_bounded(run_root):
    emit_entailment_tasks(run_root)
    with pytest.raises(ValueError, match="jobs must be between"):
        run_direct_tasks(
            run_root,
            "entailment",
            backend="openai",
            model="test",
            jobs=MAX_DIRECT_JOBS + 1,
        )


def test_every_grounding_anchor_requires_a_verdict(run_root):
    verdicts = (run_root / "evidence" / "entailment.jsonl").read_text().splitlines()
    (run_root / "evidence" / "entailment.jsonl").write_text("\n".join(verdicts[:-1]) + "\n")
    failures, _notes, stats = verify(run_root, require_entailment=True)
    assert stats["anchors_missing"] == 1
    assert any("no blinded entailment verdict" in failure for failure in failures)


def test_any_rejected_displayed_anchor_is_a_hard_failure(run_root):
    path = run_root / "evidence" / "entailment.jsonl"
    verdicts = [json.loads(line) for line in path.read_text().splitlines()]
    verdicts[0]["entailment"] = "no"
    path.write_text("".join(json.dumps(row) + "\n" for row in verdicts))
    failures, _notes, stats = verify(run_root, require_entailment=True)
    assert stats["anchors_rejected"] == 1
    assert any("known-bad anchors may not ship" in failure for failure in failures)


def test_narrative_assembly_requires_one_output_per_task(run_root):
    from grounded_quotes import build, emit_narrative_tasks

    grounded, failures = build(run_root)
    assert failures == []
    emit_narrative_tasks(run_root, grounded)
    tasks = sorted((run_root / "deliverables" / "narrative_tasks").glob("*.json"))
    for task_path in tasks:
        task = json.loads(task_path.read_text())
        output = run_root / task["output_path"]
        output.write_text(json.dumps({
            "claim_id": task["claim_id"],
            "observed_result": {
                "text": "Observed result.",
                "evidence_ids": [task["supporting_anchors"][0]["evidence_id"]],
            },
        }))
    path, count = assemble_narratives(run_root)
    assert path.exists() and count == len(tasks)


def test_direct_narrative_pool_writes_grounded_schema(run_root):
    from grounded_quotes import build, emit_narrative_tasks

    grounded, failures = build(run_root)
    assert failures == []
    emit_narrative_tasks(run_root, grounded)

    def fake(_backend, _model, prompt, **kwargs):
        assert kwargs["schema_name"] == "claim_narrative"
        task = json.loads(prompt.split("TASK:\n", 1)[1])
        anchor = (task["supporting_anchors"] + task["contradicting_anchors"])[0]
        return {
            "claim_id": task["claim_id"],
            "observed_result": {
                "text": "The accepted anchor reports the scoped result.",
                "evidence_ids": [anchor["evidence_id"]],
                "inference": False,
            },
            "authors_interpretation": None,
            "reviewer_inference": None,
            "contradiction": None,
            "evidence_gap": None,
        }, {}

    path, count = run_direct_tasks(
        run_root,
        "narratives",
        backend="openai",
        model="test",
        jobs=DEFAULT_DIRECT_JOBS,
        request_fn=fake,
    )
    assert path.name == "narrative_outputs"
    assert assemble_narratives(run_root)[1] == count


def test_adjudication_job_limit_is_enforced(tmp_path):
    with pytest.raises(ValueError, match="jobs must be between"):
        run_provider_batches(
            _batches(1), {}, backend="openai", model="test",
            cache_dir=tmp_path, jobs=99,
        )


def test_shared_worker_exchange_rewrites_outputs_and_assembles(tmp_path):
    root = tmp_path / "run"
    tasks = root / "evidence" / "adjudication_batches"
    tasks.mkdir(parents=True)
    (tasks / "batch_001.json").write_text(json.dumps({
        "batch_id": "batch_001",
        "prompt": "Judge these candidates.",
        "output_path": "evidence/adjudications/batch_001.jsonl",
    }))
    exchange = tmp_path / "shared"

    staged, count = stage_workers(root, exchange, "adjudications")

    assert count == 1
    worker_task = json.loads((staged / "batch_001.json").read_text())
    worker_output = pathlib.Path(worker_task["output_path"])
    assert worker_output.parent == exchange / "outputs" / "adjudications"
    pack = json.loads(
        (exchange / "native_packs" / "adjudications" / "pack_0001.json").read_text()
    )
    assert pack["task_count"] == 1
    assert pack["tasks"][0]["output_path"] == str(worker_output)
    worker_output.write_text('{"claim_id":"C-001"}\n')

    destination, rows = assemble_adjudications(root, exchange)
    assert rows == 1
    assert json.loads(destination.read_text()) == {"claim_id": "C-001"}
    receipt = json.loads(
        (root / "state" / "assemblies" / "adjudications.json").read_text()
    )
    assert receipt["complete"] is True
    assert receipt["task_count"] == receipt["output_count"] == 1


def test_compaction_resume_refuses_changed_task_inventory(tmp_path):
    root = tmp_path / "run"
    tasks = root / "evidence" / "adjudication_batches"
    tasks.mkdir(parents=True)
    task_path = tasks / "batch_001.json"
    task_path.write_text(json.dumps({
        "batch_id": "batch_001",
        "prompt": "Original bounded task.",
        "output_path": "evidence/adjudications/batch_001.jsonl",
    }))
    exchange = tmp_path / "shared"
    staged, _count = stage_workers(root, exchange, "adjudications")
    worker_task = json.loads((staged / "batch_001.json").read_text())
    pathlib.Path(worker_task["output_path"]).write_text("{}\n")

    task_path.write_text(json.dumps({
        "batch_id": "batch_001",
        "prompt": "Task silently changed after compaction.",
        "output_path": "evidence/adjudications/batch_001.jsonl",
    }))
    with pytest.raises(ValueError, match="changed after staging"):
        assemble_adjudications(root, exchange)


def test_zero_accept_adjudication_keeps_examined_block_coverage(tmp_path):
    root = tmp_path / "run"
    tasks = root / "evidence" / "adjudication_batches"
    tasks.mkdir(parents=True)
    (tasks / "batch_001.json").write_text(json.dumps({
        "batch_id": "batch_001",
        "paper_id": "P-001",
        "claim_ids": ["C-001"],
        "block_ids": ["P-001:S:1", "P-001:S:2"],
        "n_blocks": 2,
        "prompt": "Judge these candidates.",
        "output_path": "evidence/adjudications/batch_001.jsonl",
    }))
    exchange = tmp_path / "shared"
    staged, _count = stage_workers(root, exchange, "adjudications")
    worker_task = json.loads((staged / "batch_001.json").read_text())
    pathlib.Path(worker_task["output_path"]).write_text("")

    destination, row_count = assemble_adjudications(root, exchange)
    examined, receipt = _native_adjudication_coverage(root, destination)

    assert row_count == 0
    assert examined == {"C-001": {"P-001:S:1", "P-001:S:2"}}
    assert receipt["units"][0]["accepted_row_count"] == 0


def test_new_adjudication_batches_require_a_complete_negative_decision_audit(
    tmp_path,
):
    root = tmp_path / "run"
    tasks = root / "evidence" / "adjudication_batches"
    outputs = root / "evidence" / "adjudications"
    tasks.mkdir(parents=True)
    outputs.mkdir(parents=True)
    (tasks / "batch_001.json").write_text(json.dumps({
        "batch_id": "batch_001",
        "paper_id": "P-001",
        "claim_ids": ["C-001"],
        "block_ids": ["B-1", "B-2"],
        "n_blocks": 2,
        "audit_required": True,
        "output_path": "evidence/adjudications/batch_001.jsonl",
    }))
    (outputs / "batch_001.jsonl").write_text(
        json.dumps({
            "paper_id": "P-001", "claim_id": "C-001", "block_id": "B-1"
        }) + "\n" + json.dumps({"_decision_audit": {
            "candidate_blocks_reviewed": 2,
            "accepted_blocks": 1,
            "rejected_blocks": 1,
            "rejection_reasons": {"not_entailing": 1},
        }}) + "\n"
    )

    destination, count = assemble_adjudications(root)

    assert count == 1
    assert "_decision_audit" not in destination.read_text()
    audits = [json.loads(line) for line in
              (root / "evidence" / "adjudication_audit.jsonl").read_text().splitlines()]
    assert audits[0]["rejected_blocks"] == 1
    assert audits[0]["audit_status"] == "complete"


def test_native_packs_bound_tasks_but_keep_separate_output_paths(tmp_path):
    root = tmp_path / "run"
    source = root / "deliverables" / "narrative_tasks"
    source.mkdir(parents=True)
    for index in range(5):
        (source / f"C-{index:03d}.json").write_text(json.dumps({
            "task_id": f"narrative:C-{index:03d}",
            "claim_id": f"C-{index:03d}",
            "output_path": f"deliverables/narrative_outputs/C-{index:03d}.json",
            "instructions": "Write one narrative.",
        }))
    exchange = tmp_path / "shared"
    stage_workers(root, exchange, "narratives")

    packs_path, count = pack_native(
        exchange, "narratives", max_tasks=2, max_chars=100_000
    )
    assert count == 3
    packs = [json.loads(path.read_text())
             for path in sorted(packs_path.glob("pack_[0-9]*.json"))]
    assert [pack["task_count"] for pack in packs] == [2, 2, 1]
    outputs = [task["output_path"] for pack in packs for task in pack["tasks"]]
    assert len(outputs) == len(set(outputs)) == 5
    assert all("combined result" in pack["instructions"] for pack in packs)


def test_restaging_preserves_unchanged_completed_native_outputs(tmp_path):
    root = tmp_path / "run"
    source = root / "deliverables" / "narrative_tasks"
    source.mkdir(parents=True)
    task_path = source / "C-001.json"
    task_path.write_text(json.dumps({
        "task_id": "narrative:C-001",
        "claim_id": "C-001",
        "output_path": "deliverables/narrative_outputs/C-001.json",
        "instructions": "Write one narrative.",
    }))
    exchange = tmp_path / "shared"
    staged, _count = stage_workers(root, exchange, "narratives")
    output = pathlib.Path(
        json.loads((staged / task_path.name).read_text())["output_path"]
    )
    output.write_text('{"claim_id":"C-001"}\n')

    stage_workers(root, exchange, "narratives")

    assert output.read_text() == '{"claim_id":"C-001"}\n'
    manifest = json.loads(
        (exchange / "native_packs" / "narratives" / "manifest.json").read_text()
    )
    assert manifest["pending_task_count"] == 0
    assert manifest["completed_task_count"] == 1
    assert manifest["pack_count"] == 0


def test_restaging_invalidates_output_when_native_task_changes(tmp_path):
    root = tmp_path / "run"
    source = root / "deliverables" / "narrative_tasks"
    source.mkdir(parents=True)
    task_path = source / "C-001.json"
    task = {
        "task_id": "narrative:C-001",
        "claim_id": "C-001",
        "output_path": "deliverables/narrative_outputs/C-001.json",
        "instructions": "Write one narrative.",
    }
    task_path.write_text(json.dumps(task))
    exchange = tmp_path / "shared"
    staged, _count = stage_workers(root, exchange, "narratives")
    output = pathlib.Path(
        json.loads((staged / task_path.name).read_text())["output_path"]
    )
    output.write_text('{"claim_id":"C-001"}\n')

    task["instructions"] = "Write a revised narrative."
    task_path.write_text(json.dumps(task))
    stage_workers(root, exchange, "narratives")

    assert not output.exists()
    manifest = json.loads(
        (exchange / "native_packs" / "narratives" / "manifest.json").read_text()
    )
    assert manifest["pending_task_count"] == 1
    assert manifest["pack_count"] == 1
