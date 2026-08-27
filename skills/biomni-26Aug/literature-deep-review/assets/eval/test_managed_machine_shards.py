from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest

import managed_machine_shards as sharding
from object_exchange import publish_directory, publish_json


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _read_jsonl(path):
    return [json.loads(line) for line in pathlib.Path(path).read_text().splitlines()
            if line.strip()]


def _plan(tmp_path, count=23, machines=5, processes=16):
    records = tmp_path / "records.jsonl"
    claims = tmp_path / "claims.csv"
    _jsonl(records, [{"paper_id": f"p{i}"} for i in range(count)])
    claims.write_text("claim_id,claim_text\nC1,Claim\n")
    plan = sharding.prepare(
        records, claims, tmp_path / "exchange", machines, processes
    )
    return plan, tmp_path / "exchange"


def test_prepare_assigns_complete_queues_to_at_most_five_machines(tmp_path):
    plan, _exchange = _plan(tmp_path)

    assert plan["schema_version"] == 2
    assert plan["machine_count"] == 2
    assert plan["max_processes_per_machine"] == 16
    assert plan["concurrency_ramp"] == [2, 3, 4, 6, 8, 12, 16]
    assert plan["review_mode"] == "broad"
    assert plan["ocr_mode"] == "targeted"
    assert {row["machine_id"] for row in plan["machines"]} == {
        f"worker-{index}" for index in range(2)
    }
    staged = [
        row["paper_id"]
        for machine in plan["machines"]
        for row in _read_jsonl(machine["records_path"])
    ]
    assert set(staged) == {f"p{i}" for i in range(23)}
    assert len(staged) == 23


def _minimal_skill(tmp_path):
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: test\n---\n")
    (skill / "scripts" / "evidence_first.py").write_text("pass\n")
    return skill


def test_prepare_records_skill_commit_and_bundle_for_a_real_run(
    tmp_path, monkeypatch
):
    run = tmp_path / "run"
    records = run / "corpus" / "pivotal_papers.csv"
    records.parent.mkdir(parents=True)
    records.write_text("paper_id\np0\n", encoding="utf-8")
    claims = run / "corpus" / "claims.csv"
    claims.write_text("claim_id,claim_text\nC1,Claim\n", encoding="utf-8")
    (run / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("LITERATURE_REVIEW_SKILL_GIT_COMMIT", "c" * 40)

    plan = sharding.prepare(
        records,
        claims,
        tmp_path / "exchange",
        5,
        16,
        skill_root=_minimal_skill(tmp_path),
    )

    provenance = json.loads(
        (run / "state" / "skill_provenance.json").read_text(encoding="utf-8")
    )
    assert plan["skill_git_commit"] == "c" * 40
    assert plan["skill_bundle_sha256"] == provenance["skill_bundle_sha256"]
    assert json.loads((run / "run_manifest.json").read_text())[
        "skill_provenance"
    ] == provenance


def test_object_store_prepare_uses_five_machines_for_ocr_all_without_rename(
    tmp_path, monkeypatch
):
    records = tmp_path / "records.jsonl"
    claims = tmp_path / "claims.csv"
    _jsonl(records, [{"paper_id": f"p{i}"} for i in range(33)])
    claims.write_text("claim_id,claim_text\nC1,Claim\n")

    def reject_replace(*_args, **_kwargs):
        raise OSError("object mount does not support rename")

    monkeypatch.setattr(pathlib.Path, "replace", reject_replace)
    plan = sharding.prepare(
        records,
        claims,
        tmp_path / "exchange",
        5,
        16,
        "broad",
        "all",
        exchange_mode="object-store",
        skill_root=_minimal_skill(tmp_path),
    )

    assert plan["exchange_mode"] == "object-store"
    assert plan["machine_count"] == 5
    assert (tmp_path / "exchange" / "skill.tar").is_file()
    assert (tmp_path / "exchange" / "READY.json").is_file()
    assert sharding._load_plan(tmp_path / "exchange") == plan


def test_plan_identity_changes_when_execution_settings_change(tmp_path):
    records = tmp_path / "records.jsonl"
    claims = tmp_path / "claims.csv"
    _jsonl(records, [{"paper_id": "p0"}, {"paper_id": "p1"}])
    claims.write_text("claim_id,claim_text\nC1,Claim\n")
    first = sharding.prepare(
        records, claims, tmp_path / "first", 1, 2, "broad", "targeted"
    )
    second = sharding.prepare(
        records, claims, tmp_path / "second", 1, 4, "broad", "targeted"
    )
    third = sharding.prepare(
        records, claims, tmp_path / "third", 1, 2, "broad", "all"
    )

    assert len({first["plan_id"], second["plan_id"], third["plan_id"]}) == 3


def test_background_launch_receipt_is_bound_to_the_plan(tmp_path):
    plan, exchange = _plan(tmp_path, count=2, machines=1, processes=2)
    run = tmp_path / "run"

    receipt = sharding.record_background_launch(
        run,
        exchange,
        "worker-0",
        "literature-review-fixture-worker-0",
        "job-123",
    )

    assert receipt["plan_id"] == plan["plan_id"]
    assert receipt["run_in_background"] is True
    assert receipt["job_id"] == "job-123"
    assert (
        run
        / "state"
        / "managed_launches"
        / plan["plan_id"]
        / "worker-0.json"
    ).is_file()


def test_run_machine_refuses_mode_or_ocr_drift(tmp_path):
    _plan(tmp_path, count=2, machines=1, processes=2)
    with pytest.raises(ValueError, match="does not match launch plan"):
        sharding.run_machine(
            tmp_path / "exchange", "worker-0", tmp_path / "skill",
            tmp_path / "local", "quick", "off",
        )


def test_object_store_run_machine_copies_inputs_locally_and_publishes_completion(
    tmp_path, monkeypatch
):
    records = tmp_path / "records.jsonl"
    claims = tmp_path / "claims.csv"
    _jsonl(records, [{"paper_id": "p0"}, {"paper_id": "p1"}])
    claims.write_text("claim_id,claim_text\nC1,Claim\n")
    exchange = tmp_path / "exchange"
    plan = sharding.prepare(
        records,
        claims,
        exchange,
        1,
        2,
        "broad",
        "all",
        exchange_mode="object-store",
        skill_root=_minimal_skill(tmp_path),
    )
    fake_wave, _calls = _fake_wave_factory({2: 2.0})
    monkeypatch.setattr(sharding, "_run_wave", fake_wave)
    monkeypatch.setattr(
        sharding,
        "detect_machine_resources",
        lambda: sharding.MachineResources(2, 8_000),
    )

    result = sharding.run_machine(
        exchange,
        "worker-0",
        tmp_path / "skill",
        tmp_path / "local",
        "broad",
        "all",
    )

    assert result["completed"] is True
    assert (
        tmp_path / "local" / "inputs" / plan["plan_id"] / "records.jsonl"
    ).is_file()
    assert (
        pathlib.Path(plan["machines"][0]["output_path"])
        / "completion-attempt-1" / "machine_completion.json"
    ).is_file()


def test_resource_cap_uses_cpu_memory_and_configured_ceiling():
    resources = sharding.MachineResources(
        logical_cpus=8, available_memory_mb=16_000
    )
    assert sharding._resource_cap(resources, 16, 1_000) == 12
    assert sharding._resource_cap(resources, 16, 2_000) == 6
    assert sharding._resource_cap(resources, 4, 1_000) == 4


def _fake_wave_factory(throughput_by_concurrency, peak_rss_mb=500.0):
    calls = []

    def fake_wave(**kwargs):
        groups = kwargs["record_groups"]
        concurrency = len(groups)
        phase = kwargs["phase"]
        calls.append((phase, concurrency))
        throughput = throughput_by_concurrency.get(
            concurrency, float(concurrency)
        )
        tasks = [
            {
                "task_id": f"{phase}-{index}",
                "output_path": f"/tmp/{phase}-{index}",
                "record_count": len(records),
                "elapsed_seconds": 1.0,
            }
            for index, records in enumerate(groups)
        ]
        return {
            "phase": phase,
            "concurrency": concurrency,
            "record_count": sum(len(records) for records in groups),
            "elapsed_seconds": 1.0,
            "throughput_records_per_second": throughput,
            "parallel_failures_retried_serially": 0,
            "peak_worker_rss_mb": peak_rss_mb,
            "minimum_available_memory_mb": 10_000.0,
            "transient_retry_remaining": 0,
            "transient_retry_attempts": 0,
            "tasks": tasks,
        }

    return fake_wave, calls


def test_run_machine_ramps_beyond_two_when_throughput_improves(
    tmp_path, monkeypatch
):
    _plan(tmp_path, count=30, machines=1, processes=8)
    fake_wave, calls = _fake_wave_factory({2: 2, 3: 3, 4: 4, 6: 6, 8: 8})
    monkeypatch.setattr(sharding, "_run_wave", fake_wave)
    monkeypatch.setattr(
        sharding,
        "detect_machine_resources",
        lambda: sharding.MachineResources(8, 16_000),
    )

    result = sharding.run_machine(
        tmp_path / "exchange", "worker-0", tmp_path / "skill",
        tmp_path / "local", "broad", "targeted",
    )

    assert result["selected_processes"] == 8
    assert result["stop_reason"] == "resource_cap"
    assert [call for call in calls if call[0].startswith("pilot_")] == [
        ("pilot_2", 2), ("pilot_3", 3), ("pilot_4", 4),
        ("pilot_6", 6), ("pilot_8", 8),
    ]
    assert sum(task["record_count"] for task in result["tasks"]) == 30


def test_thirteen_paper_machine_can_select_four_processes(tmp_path, monkeypatch):
    _plan(tmp_path, count=13, machines=1, processes=16)
    fake_wave, calls = _fake_wave_factory({2: 2, 3: 3, 4: 4})
    monkeypatch.setattr(sharding, "_run_wave", fake_wave)
    monkeypatch.setattr(
        sharding,
        "detect_machine_resources",
        lambda: sharding.MachineResources(8, 32_000),
    )

    result = sharding.run_machine(
        tmp_path / "exchange", "worker-0", tmp_path / "skill",
        tmp_path / "local", "broad", "targeted",
    )

    assert result["selected_processes"] == 4
    assert result["stop_reason"] == "insufficient_records_for_next_pilot"
    assert calls == [
        ("pilot_2", 2), ("pilot_3", 3), ("pilot_4", 4), ("final", 4)
    ]


def test_run_machine_stops_at_previous_level_on_throughput_plateau(
    tmp_path, monkeypatch
):
    _plan(tmp_path, count=20, machines=1, processes=8)
    fake_wave, calls = _fake_wave_factory({2: 2.0, 3: 2.05})
    monkeypatch.setattr(sharding, "_run_wave", fake_wave)
    monkeypatch.setattr(
        sharding,
        "detect_machine_resources",
        lambda: sharding.MachineResources(8, 16_000),
    )

    result = sharding.run_machine(
        tmp_path / "exchange", "worker-0", tmp_path / "skill",
        tmp_path / "local", "broad", "targeted",
    )

    assert result["selected_processes"] == 2
    assert result["stop_reason"] == "throughput_plateau"
    assert calls[:2] == [("pilot_2", 2), ("pilot_3", 3)]
    assert calls[-1] == ("final", 2)


def test_run_machine_recalculates_cap_from_measured_worker_memory(
    tmp_path, monkeypatch
):
    _plan(tmp_path, count=12, machines=1, processes=8)
    fake_wave, calls = _fake_wave_factory({2: 2, 3: 3, 4: 4}, peak_rss_mb=500)
    monkeypatch.setattr(sharding, "_run_wave", fake_wave)
    # Default 1.4 GB estimate initially caps this machine at two; the measured
    # 0.5 GB pilot permits the controller to try three and four.
    monkeypatch.setattr(
        sharding,
        "detect_machine_resources",
        lambda: sharding.MachineResources(4, 4_000),
    )

    result = sharding.run_machine(
        tmp_path / "exchange", "worker-0", tmp_path / "skill",
        tmp_path / "local", "broad", "targeted",
    )

    assert ("pilot_3", 3) in calls
    assert ("pilot_4", 4) in calls
    assert result["selected_processes"] == 4


def test_run_machine_stops_ramping_when_provider_retries_rise(
    tmp_path, monkeypatch
):
    _plan(tmp_path, count=10, machines=1, processes=8)
    base_wave, calls = _fake_wave_factory({2: 2.0})

    def pressured_wave(**kwargs):
        result = base_wave(**kwargs)
        if kwargs["phase"] == "pilot_2":
            result["transient_retry_attempts"] = 1
        return result

    monkeypatch.setattr(sharding, "_run_wave", pressured_wave)
    monkeypatch.setattr(
        sharding,
        "detect_machine_resources",
        lambda: sharding.MachineResources(8, 16_000),
    )
    result = sharding.run_machine(
        tmp_path / "exchange", "worker-0", tmp_path / "skill",
        tmp_path / "local", "broad", "targeted",
    )
    assert result["selected_processes"] == 1
    assert result["stop_reason"] == "retrieval_pressure"
    assert calls[-1] == ("final", 1)


def test_run_machine_stops_at_previous_level_on_memory_pressure(
    tmp_path, monkeypatch
):
    _plan(tmp_path, count=15, machines=1, processes=8)
    base_wave, calls = _fake_wave_factory({2: 2.0, 3: 3.0})

    def pressured_wave(**kwargs):
        result = base_wave(**kwargs)
        if kwargs["phase"] == "pilot_3":
            result["minimum_available_memory_mb"] = 1_000
        return result

    monkeypatch.setattr(sharding, "_run_wave", pressured_wave)
    monkeypatch.setattr(
        sharding,
        "detect_machine_resources",
        lambda: sharding.MachineResources(8, 16_000),
    )
    result = sharding.run_machine(
        tmp_path / "exchange", "worker-0", tmp_path / "skill",
        tmp_path / "local", "broad", "targeted",
    )

    assert result["selected_processes"] == 2
    assert result["stop_reason"] == "memory_pressure"
    assert calls[-1] == ("final", 2)


def test_wave_really_runs_child_tasks_concurrently(tmp_path, monkeypatch):
    lock = threading.Lock()
    active = 0
    maximum = 0

    def fake_worker(**kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return {
            "task_id": kwargs["task_id"],
            "output_path": str(kwargs["output"]),
            "record_count": len(kwargs["records"]),
            "elapsed_seconds": 0.03,
            "peak_rss_mb": 100,
            "minimum_available_memory_mb": 1_000,
            "transient_retry_remaining": 0,
        }

    monkeypatch.setattr(sharding, "_run_worker_task", fake_worker)
    result = sharding._run_wave(
        record_groups=[[{"paper_id": f"p{i}"}] for i in range(4)],
        machine={"machine_id": "worker-0", "output_path": str(tmp_path / "out")},
        phase="pilot_4", start_index=0, claims_path=tmp_path / "claims.csv",
        skill_root=tmp_path / "skill", local_base=tmp_path / "local",
        review_mode="broad", ocr_mode="targeted",
    )
    assert maximum == 4
    assert result["parallel_failures_retried_serially"] == 0


def test_wave_retries_a_failed_parallel_task_serially(tmp_path, monkeypatch):
    attempts = {}

    def flaky_worker(**kwargs):
        task_id = kwargs["task_id"]
        attempts[task_id] = attempts.get(task_id, 0) + 1
        if len(attempts) == 1 and attempts[task_id] == 1:
            raise RuntimeError("simulated pressure kill")
        return {
            "task_id": task_id,
            "output_path": str(kwargs["output"]),
            "record_count": len(kwargs["records"]),
            "elapsed_seconds": 0.01,
            "peak_rss_mb": 100,
            "minimum_available_memory_mb": 1_000,
            "transient_retry_remaining": 0,
        }

    monkeypatch.setattr(sharding, "_run_worker_task", flaky_worker)
    result = sharding._run_wave(
        record_groups=[[{"paper_id": "p0"}], [{"paper_id": "p1"}]],
        machine={"machine_id": "worker-0", "output_path": str(tmp_path / "out")},
        phase="pilot_2", start_index=0, claims_path=tmp_path / "claims.csv",
        skill_root=tmp_path / "skill", local_base=tmp_path / "local",
        review_mode="broad", ocr_mode="targeted",
    )
    assert result["parallel_failures_retried_serially"] == 1
    assert result["record_count"] == 2
    assert sorted(attempts.values()) == [1, 2]


def test_merge_restores_order_and_rewrites_machine_local_paths(tmp_path):
    plan, exchange = _plan(tmp_path, count=3, machines=2, processes=8)
    for machine in plan["machines"]:
        tasks = []
        for index, paper in enumerate(_read_jsonl(machine["records_path"])):
            pid = paper["paper_id"]
            output = pathlib.Path(machine["output_path"]) / "tasks" / pid
            pdf = output / "fulltext" / "pdfs" / f"{pid}.pdf"
            image = output / "fulltext" / "figures" / f"{pid}__fig1.png"
            pdf.parent.mkdir(parents=True, exist_ok=True)
            image.parent.mkdir(parents=True, exist_ok=True)
            pdf.write_bytes(b"%PDF-1.7\n")
            image.write_bytes(b"png")
            _jsonl(output / "fulltext" / "papers.jsonl", [{
                **paper, "local_pdf": f"/workspace/task/{pid}.pdf"
            }])
            _jsonl(output / "fulltext" / "not_retrieved.jsonl", [])
            parsed_path = output / "fulltext" / "parsed" / f"{pid}.json"
            parsed_path.parent.mkdir(parents=True, exist_ok=True)
            parsed_path.write_text(json.dumps({
                "paper_id": pid,
                "figures": [{
                    "figure_id": "fig1",
                    "image_path": f"/workspace/task/{pid}__fig1.png",
                }],
            }))
            tasks.append({
                "task_id": pid,
                "output_path": str(output),
                "record_count": 1,
                "paper_ids": [pid],
                "elapsed_seconds": 1.0,
            })
        completion = {
            "plan_id": plan["plan_id"],
            "machine_id": machine["machine_id"],
            "review_mode": "broad",
            "ocr_mode": "targeted",
            "selected_processes": len(tasks),
            "elapsed_seconds": 1.0,
            "tasks": tasks,
            "completed": True,
        }
        _jsonl(
            pathlib.Path(machine["output_path"]) / "machine_completion.json",
            [completion],
        )

    run_root = tmp_path / "run"
    result = sharding.merge(exchange, run_root)
    merged = _read_jsonl(run_root / "fulltext" / "papers.jsonl")
    assert [row["paper_id"] for row in merged] == ["p0", "p1", "p2"]
    assert merged[0]["local_pdf"] == str(
        run_root / "fulltext" / "pdfs" / "p0.pdf"
    )
    parsed = json.loads(
        (run_root / "fulltext" / "parsed" / "p0.json").read_text()
    )
    assert parsed["figures"][0]["image_path"] == str(
        run_root / "fulltext" / "figures" / "p0__fig1.png"
    )
    assert result["machine_count"] == 1
    assert result["adaptive"] is True


def test_merge_refuses_incomplete_machine_set(tmp_path):
    _plan(tmp_path, count=13, machines=2, processes=8)
    with pytest.raises(ValueError, match="incomplete managed machines"):
        sharding.merge(tmp_path / "exchange", tmp_path / "run")


def test_prepare_retry_selects_only_transient_failures(tmp_path):
    run = tmp_path / "run"
    _jsonl(run / "fulltext" / "not_retrieved.jsonl", [
        {"paper_id": "timeout", "_not_retrieved_kind": "retrieval_failed"},
        {"paper_id": "closed", "_not_retrieved_kind": "paywalled"},
    ])
    claims = tmp_path / "claims.csv"
    claims.write_text("claim_id,claim_text\nC1,Claim\n")
    plan = sharding.prepare_retry(
        run, claims, tmp_path / "retry", 5, 16, "broad", "targeted"
    )
    assert plan["record_count"] == 1
    assert plan["refresh_acquisition"] is True
    assert _read_jsonl(plan["records_path"]) == [
        {"paper_id": "timeout", "_not_retrieved_kind": "retrieval_failed"}
    ]


def test_merge_refuses_task_that_drops_an_input_paper(tmp_path):
    plan, exchange = _plan(tmp_path, count=1, machines=1, processes=2)
    machine = plan["machines"][0]
    output = pathlib.Path(machine["output_path"]) / "tasks" / "broken"
    _jsonl(output / "fulltext" / "papers.jsonl", [])
    _jsonl(output / "fulltext" / "not_retrieved.jsonl", [])
    completion = {
        "plan_id": plan["plan_id"],
        "machine_id": machine["machine_id"],
        "review_mode": "broad",
        "ocr_mode": "targeted",
        "selected_processes": 1,
        "elapsed_seconds": 1.0,
        "tasks": [{
            "task_id": "broken", "output_path": str(output),
            "record_count": 1, "paper_ids": ["p0"],
        }],
        "completed": True,
    }
    _jsonl(pathlib.Path(machine["output_path"]) / "machine_completion.json",
           [completion])
    with pytest.raises(ValueError, match="outcomes do not match"):
        sharding.merge(exchange, tmp_path / "run")


def test_object_store_merge_materializes_verified_task_bundles(
    tmp_path, monkeypatch
):
    records = tmp_path / "records.jsonl"
    claims = tmp_path / "claims.csv"
    _jsonl(records, [{"paper_id": f"p{i}"} for i in range(9)])
    claims.write_text("claim_id,claim_text\nC1,Claim\n")
    exchange = tmp_path / "exchange"
    original_replace = pathlib.Path.replace

    def reject_shared_replace(source, target):
        if source.is_relative_to(exchange):
            raise OSError("object mount does not support rename")
        return original_replace(source, target)

    monkeypatch.setattr(pathlib.Path, "replace", reject_shared_replace)
    plan = sharding.prepare(
        records,
        claims,
        exchange,
        2,
        8,
        "broad",
        "all",
        exchange_mode="object-store",
        skill_root=_minimal_skill(tmp_path),
    )

    for machine in plan["machines"]:
        tasks = []
        for paper in _read_jsonl(machine["records_path"]):
            pid = paper["paper_id"]
            local = tmp_path / "worker-local" / machine["machine_id"] / pid
            _jsonl(local / "fulltext" / "papers.jsonl", [{
                **paper, "local_pdf": f"/workspace/{pid}.pdf"
            }])
            _jsonl(local / "fulltext" / "not_retrieved.jsonl", [])
            pdf = local / "fulltext" / "pdfs" / f"{pid}.pdf"
            pdf.parent.mkdir(parents=True, exist_ok=True)
            pdf.write_bytes(b"%PDF-1.7\n")
            parsed = local / "fulltext" / "parsed" / f"{pid}.json"
            parsed.parent.mkdir(parents=True, exist_ok=True)
            parsed.write_text(json.dumps({"paper_id": pid, "figures": []}))
            (local / "run_manifest.json").write_text("{}\n")
            publication = (
                pathlib.Path(machine["output_path"]) / "tasks" / pid / "attempt-1"
            )
            task = {
                "task_id": pid,
                "output_path": str(publication),
                "record_count": 1,
                "paper_ids": [pid],
                "records_sha256": f"sha-{pid}",
                "elapsed_seconds": 1.0,
            }
            publish_directory(
                local,
                publication,
                local / "publication" / "result.tar",
                ("fulltext", "run_manifest.json"),
                {"task": task},
            )
            tasks.append(task)
        completion = {
            "plan_id": plan["plan_id"],
            "machine_id": machine["machine_id"],
            "review_mode": "broad",
            "ocr_mode": "all",
            "selected_processes": len(tasks),
            "elapsed_seconds": 1.0,
            "tasks": tasks,
            "completed": True,
        }
        publish_json(
            pathlib.Path(machine["output_path"])
            / "completion-attempt-1" / "machine_completion.json",
            completion,
        )

    run_root = tmp_path / "run"
    result = sharding.merge(exchange, run_root)

    assert result["machine_count"] == 2
    assert [row["paper_id"] for row in _read_jsonl(
        run_root / "fulltext" / "papers.jsonl"
    )] == [f"p{i}" for i in range(9)]
    assert (
        run_root / "state" / "managed_downloads" / plan["plan_id"]
    ).is_dir()


def test_object_store_retries_publish_to_a_new_attempt_prefix(tmp_path):
    task_root = tmp_path / "shared" / "task"
    partial = task_root / "attempt-1" / "result.tar"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial")

    assert sharding._next_task_publication(task_root) == task_root / "attempt-2"


def test_corrupt_completion_markers_do_not_block_a_new_attempt(tmp_path):
    task_root = tmp_path / "shared" / "task"
    corrupt_task_marker = task_root / "attempt-1" / "DONE.json"
    corrupt_task_marker.parent.mkdir(parents=True)
    corrupt_task_marker.write_text("not-json")
    assert sharding._completed_task_publication(task_root) is None
    assert sharding._next_task_publication(task_root) == task_root / "attempt-2"

    machine_root = tmp_path / "shared" / "worker-0"
    corrupt_machine_marker = (
        machine_root / "completion-attempt-1" / "machine_completion.json"
    )
    corrupt_machine_marker.parent.mkdir(parents=True)
    corrupt_machine_marker.write_text("not-json")
    assert sharding._completed_machine_path(machine_root) is None
    assert sharding._next_machine_completion_path(machine_root) == (
        machine_root / "completion-attempt-2" / "machine_completion.json"
    )
