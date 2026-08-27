#!/usr/bin/env python3
"""Adaptively preprocess literature across Biomni managed machines."""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Iterable

from corpus_ledger import (
    atomic_json as write_corpus_ledger,
    refresh as refresh_corpus_ledger,
    validate as validate_corpus_ledger,
)
from object_exchange import (
    COMPLETION_NAME,
    ObjectExchangeError,
    create_bundle,
    materialize_directory,
    publish_bytes,
    publish_directory,
    publish_file,
    publish_json,
    read_publication,
    sha256_file,
)
from parse_quality import (
    DEFAULT_MIN_SUBSTANTIVE_SENTENCES,
    assess as assess_parse_quality,
)
from pipeline_io import safe_id
from skill_provenance import capture as capture_skill_provenance


DEFAULT_MAX_MACHINES = 5
DEFAULT_MAX_PROCESSES_PER_MACHINE = 16
ABSOLUTE_MAX_MACHINES = 5
ABSOLUTE_MAX_PROCESSES_PER_MACHINE = 16
CONCURRENCY_RAMP = (2, 3, 4, 6, 8, 12, 16)
CPU_OVERSUBSCRIPTION = 2
DEFAULT_WORKER_MEMORY_MB = 1400.0
MEMORY_BUDGET_FRACTION = 0.75
MIN_AVAILABLE_MEMORY_FRACTION = 0.15
MIN_THROUGHPUT_GAIN = 0.05
MAX_TRANSIENT_RETRY_FRACTION = 0.25
PROCESS_SAMPLE_SECONDS = 0.1
EXCHANGE_MODES = ("posix", "object-store")
PAPERS_PER_MACHINE_BY_OCR = {
    "off": 16,
    "targeted": 12,
    "all": 8,
}
SKILL_BUNDLE_INPUTS = (
    "SKILL.md",
    "assets",
    "references",
    "scripts",
    "templates",
)
PLAN_SCHEMA_VERSION = 2
MAX_PUBLICATION_ATTEMPTS = 10
MANAGED_LAUNCH_RECEIPTS = pathlib.Path("state/managed_launches")


@dataclass(frozen=True)
class MachineResources:
    logical_cpus: int
    available_memory_mb: float


def _read_records(path: pathlib.Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    rows: list[dict] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path} line {line_number} is not a JSON object")
        rows.append(value)
    return rows


def _write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl(path: pathlib.Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _exchange_write_json(
    path: pathlib.Path,
    value: object,
    exchange_mode: str,
) -> None:
    if exchange_mode == "object-store":
        publish_json(path, value)
    else:
        _write_json(path, value)


def _exchange_write_jsonl(
    path: pathlib.Path,
    rows: Iterable[dict],
    exchange_mode: str,
) -> None:
    if exchange_mode == "object-store":
        value = b"".join(
            json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n"
            for row in rows
        )
        publish_bytes(path, value)
    else:
        _write_jsonl(path, rows)


def _exchange_copy(
    source: pathlib.Path,
    destination: pathlib.Path,
    exchange_mode: str,
) -> None:
    if exchange_mode == "object-store":
        publish_file(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def expected_machine_count(
    record_count: int,
    max_machines: int,
    ocr_mode: str,
) -> int:
    papers_per_machine = PAPERS_PER_MACHINE_BY_OCR[ocr_mode]
    desired = math.ceil(record_count / papers_per_machine)
    return max(1, min(max_machines, record_count, desired))


def _copy_verified(
    source: pathlib.Path,
    destination: pathlib.Path,
    expected_sha256: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and sha256_file(destination) == expected_sha256:
        return
    temporary = destination.with_name(f".{destination.name}.tmp")
    shutil.copyfile(source, temporary)
    actual = sha256_file(temporary)
    if actual != expected_sha256:
        raise ValueError(
            f"input checksum mismatch for {source}: "
            f"expected {expected_sha256}, got {actual}"
        )
    temporary.replace(destination)


def _load_plan(exchange_root: pathlib.Path) -> dict:
    plan_path = exchange_root / "launch_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError(
            "adaptive runner requires launch plan "
            f"schema_version={PLAN_SCHEMA_VERSION}"
        )
    if plan.get("exchange_mode", "posix") == "object-store":
        ready_path = exchange_root / "READY.json"
        if not ready_path.exists():
            raise ValueError(f"object-store launch plan is incomplete: {ready_path}")
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        if (
            ready.get("plan_id") != plan.get("plan_id")
            or ready.get("launch_plan_sha256") != sha256_file(plan_path)
        ):
            raise ValueError(f"object-store launch plan failed verification: {plan_path}")
    return plan


def _indexed_by_paper(rows: Iterable[dict]) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for row in rows:
        paper_id = str(row.get("paper_id") or "")
        if not paper_id or paper_id in indexed:
            raise ValueError(f"duplicate or missing paper_id {paper_id!r}")
        indexed[paper_id] = row
    return indexed


def _available_memory_mb() -> float:
    meminfo = pathlib.Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return float(line.split()[1]) / 1024.0
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return float(pages * page_size) / (1024.0 * 1024.0)
    except (OSError, TypeError, ValueError):
        return 0.0


def detect_machine_resources() -> MachineResources:
    try:
        logical_cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        logical_cpus = os.cpu_count() or 1
    return MachineResources(
        logical_cpus=max(1, int(logical_cpus)),
        available_memory_mb=max(0.0, _available_memory_mb()),
    )


def _process_rss_mb(pid: int) -> float:
    status = pathlib.Path(f"/proc/{pid}/status")
    if not status.exists():
        return 0.0
    try:
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith(("VmHWM:", "VmRSS:")):
                return float(line.split()[1]) / 1024.0
    except (OSError, ValueError):
        return 0.0
    return 0.0


def _run_monitored(command: list[str]) -> tuple[int, float, float]:
    process = subprocess.Popen(command)
    peak_rss_mb = 0.0
    minimum_available_mb = _available_memory_mb()
    while process.poll() is None:
        peak_rss_mb = max(peak_rss_mb, _process_rss_mb(process.pid))
        available = _available_memory_mb()
        if available:
            minimum_available_mb = (
                min(minimum_available_mb, available)
                if minimum_available_mb else available
            )
        time.sleep(PROCESS_SAMPLE_SECONDS)
    peak_rss_mb = max(peak_rss_mb, _process_rss_mb(process.pid))
    return process.returncode, peak_rss_mb, minimum_available_mb


def _ramp(max_processes: int) -> list[int]:
    values = [value for value in CONCURRENCY_RAMP if value <= max_processes]
    if max_processes > 1 and max_processes not in values:
        values.append(max_processes)
    return sorted(set(values))


def _resource_cap(
    resources: MachineResources,
    max_processes: int,
    observed_worker_memory_mb: float,
) -> int:
    cpu_cap = max(1, resources.logical_cpus * CPU_OVERSUBSCRIPTION)
    worker_memory = observed_worker_memory_mb or DEFAULT_WORKER_MEMORY_MB
    if resources.available_memory_mb:
        memory_cap = max(
            1,
            int(
                resources.available_memory_mb
                * MEMORY_BUDGET_FRACTION
                / worker_memory
            ),
        )
    else:
        memory_cap = max_processes
    return max(1, min(max_processes, cpu_cap, memory_cap))


def prepare(
    records_path: pathlib.Path,
    claims_path: pathlib.Path,
    exchange_root: pathlib.Path,
    max_machines: int,
    max_processes_per_machine: int,
    review_mode: str = "broad",
    ocr_mode: str = "targeted",
    refresh_acquisition: bool = False,
    recovery_run_root: str = "",
    exchange_mode: str = "posix",
    skill_root: pathlib.Path | None = None,
    provenance_run_root: pathlib.Path | None = None,
) -> dict:
    if not 1 <= max_machines <= ABSOLUTE_MAX_MACHINES:
        raise ValueError(f"max_machines must be 1..{ABSOLUTE_MAX_MACHINES}")
    if not 1 <= max_processes_per_machine <= ABSOLUTE_MAX_PROCESSES_PER_MACHINE:
        raise ValueError(
            "max_processes_per_machine must be 1.."
            f"{ABSOLUTE_MAX_PROCESSES_PER_MACHINE}"
        )
    if review_mode not in {"quick", "deep", "broad"}:
        raise ValueError(f"invalid review_mode: {review_mode}")
    if ocr_mode not in {"off", "targeted", "all"}:
        raise ValueError(f"invalid ocr_mode: {ocr_mode}")
    if exchange_mode not in EXCHANGE_MODES:
        raise ValueError(f"invalid exchange_mode: {exchange_mode}")
    if exchange_mode == "object-store" and skill_root is None:
        raise ValueError("object-store exchange requires skill_root")
    records = _read_records(records_path)
    if not records:
        raise ValueError("records file is empty")

    # Scope and selection live beside the run's corpus.  Validate before any
    # machine is launched so an uncapped broad review cannot silently turn a
    # larger in-scope corpus into a hand-picked subset.  Unit callers that pass
    # an isolated records file have no corpus contract and are left unchanged.
    run_root = (
        records_path.parent.parent
        if records_path.parent.name == "corpus"
        else None
    )
    ledger_counts: dict[str, int] = {}
    if run_root is not None and (run_root / "corpus" / "references.jsonl").exists():
        _write_jsonl(run_root / "corpus" / "records.jsonl", records)
        ledger, errors = refresh_corpus_ledger(run_root, records_path)
        ledger["review_mode"] = review_mode
        manifest_path = run_root / "run_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            ledger["explicit_paper_cap"] = (manifest.get("config") or {}).get(
                "max_papers"
            )
        errors = validate_corpus_ledger(ledger)
        ledger["validation"] = {"final": False, "ok": not errors, "errors": errors}
        write_corpus_ledger(run_root / "corpus" / "corpus_ledger.json", ledger)
        ledger_counts = dict(ledger.get("counts") or {})
        if errors:
            raise ValueError("corpus selection gate: " + "; ".join(errors))

    inputs = exchange_root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    staged_records = inputs / "records.jsonl"
    staged_claims = inputs / claims_path.name
    _exchange_write_jsonl(staged_records, records, exchange_mode)
    _exchange_copy(claims_path, staged_claims, exchange_mode)
    claims_sha256 = sha256_file(claims_path)
    skill_bundle_path = ""
    skill_bundle_sha256 = ""
    skill_provenance: dict[str, object] = {}
    if skill_root is not None:
        missing = [
            name for name in ("SKILL.md", "scripts")
            if not (skill_root / name).exists()
        ]
        if missing:
            raise ValueError(
                "skill_root is missing required bundle inputs: "
                + ", ".join(missing)
            )
        include_names = tuple(
            name for name in SKILL_BUNDLE_INPUTS if (skill_root / name).exists()
        )
        with tempfile.TemporaryDirectory(prefix="ldr-skill-bundle-") as directory:
            local_bundle = pathlib.Path(directory) / "skill.tar"
            skill_bundle_sha256 = create_bundle(
                skill_root, local_bundle, include_names
            )
            staged_bundle = exchange_root / "skill.tar"
            _exchange_copy(local_bundle, staged_bundle, exchange_mode)
            skill_bundle_path = str(staged_bundle)
        identity_root = provenance_run_root or run_root
        if identity_root is not None:
            skill_provenance = capture_skill_provenance(
                identity_root,
                skill_root,
                known_bundle_sha256=skill_bundle_sha256,
            )
    plan_identity = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "records": records,
        "claims_sha256": claims_sha256,
        "skill_bundle_sha256": skill_bundle_sha256,
        "skill_git_commit": skill_provenance.get("git_commit", ""),
        "max_machines": max_machines,
        "max_processes_per_machine": max_processes_per_machine,
        "review_mode": review_mode,
        "ocr_mode": ocr_mode,
        "exchange_mode": exchange_mode,
        "refresh_acquisition": refresh_acquisition,
        "recovery_run_root": recovery_run_root,
        "corpus_ledger_counts": ledger_counts,
    }
    plan_id = hashlib.sha256(
        json.dumps(plan_identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    machine_count = expected_machine_count(
        len(records), max_machines, ocr_mode
    )
    queues: list[list[dict]] = [[] for _ in range(machine_count)]
    for index, record in enumerate(records):
        queues[index % machine_count].append(record)

    machines = []
    for index, rows in enumerate(queues):
        machine_id = f"worker-{index}"
        queue_path = inputs / f"machine_{machine_id}.jsonl"
        _exchange_write_jsonl(queue_path, rows, exchange_mode)
        machines.append({
            "machine_id": machine_id,
            "record_count": len(rows),
            "records_path": str(queue_path),
            "records_sha256": sha256_file(queue_path),
            "output_path": str(exchange_root / "outputs" / plan_id / machine_id),
        })

    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "exchange_mode": exchange_mode,
        "record_count": len(records),
        "machine_count": machine_count,
        "papers_per_machine_target": PAPERS_PER_MACHINE_BY_OCR[ocr_mode],
        "max_machines": max_machines,
        "max_processes_per_machine": max_processes_per_machine,
        "concurrency_ramp": _ramp(max_processes_per_machine),
        "review_mode": review_mode,
        "ocr_mode": ocr_mode,
        "refresh_acquisition": refresh_acquisition,
        "recovery_run_root": recovery_run_root,
        "claims_path": str(staged_claims),
        "claims_sha256": claims_sha256,
        "records_path": str(staged_records),
        "records_sha256": sha256_file(staged_records),
        "skill_bundle_path": skill_bundle_path,
        "skill_bundle_sha256": skill_bundle_sha256,
        "skill_git_commit": skill_provenance.get("git_commit", ""),
        "machines": machines,
    }
    launch_plan_path = exchange_root / "launch_plan.json"
    _exchange_write_json(launch_plan_path, plan, exchange_mode)
    _exchange_write_json(
        exchange_root / "READY.json",
        {
            "schema_version": 1,
            "plan_id": plan_id,
            "launch_plan_sha256": sha256_file(launch_plan_path),
        },
        exchange_mode,
    )
    return plan


def prepare_retry(
    run_root: pathlib.Path,
    claims_path: pathlib.Path,
    exchange_root: pathlib.Path,
    max_machines: int,
    max_processes_per_machine: int,
    review_mode: str,
    ocr_mode: str,
    exchange_mode: str = "posix",
    skill_root: pathlib.Path | None = None,
) -> dict:
    """Prepare a second managed wave for post-merge transient failures only."""
    misses = _read_records(run_root / "fulltext" / "not_retrieved.jsonl")
    transient = [
        row for row in misses
        if str(row.get("_not_retrieved_kind") or "") == "retrieval_failed"
    ]
    retry_record = run_root / "fulltext" / "transient_retry_records.jsonl"
    _write_jsonl(retry_record, transient)
    if not transient:
        result = {
            "schema_version": 1,
            "completed": True,
            "attempted": 0,
            "recovered": 0,
            "remaining": 0,
            "reason": "no transient retrieval failures remained after merge",
        }
        _write_json(run_root / "fulltext" / "global_transient_retry.json", result)
        if (run_root / "corpus" / "references.jsonl").exists():
            refresh_corpus_ledger(run_root)
        return result
    return prepare(
        retry_record,
        claims_path,
        exchange_root,
        max_machines,
        max_processes_per_machine,
        review_mode,
        ocr_mode,
        refresh_acquisition=True,
        recovery_run_root=str(run_root.resolve()),
        exchange_mode=exchange_mode,
        skill_root=skill_root,
        provenance_run_root=run_root,
    )


def record_background_launch(
    run_root: pathlib.Path,
    exchange_root: pathlib.Path,
    machine_id: str,
    background_name: str,
    job_id: str = "",
) -> dict:
    """Persist one successful Biomni tracked-background submission."""
    plan = _load_plan(exchange_root)
    planned = {str(row.get("machine_id") or "") for row in plan["machines"]}
    if machine_id not in planned:
        raise ValueError(f"launch plan has no queue for {machine_id}")
    name = background_name.strip()
    if not name:
        raise ValueError("background_name is required")
    receipt = {
        "schema_version": 1,
        "plan_id": plan["plan_id"],
        "machine_id": machine_id,
        "run_in_background": True,
        "background_name": name,
        "job_id": job_id.strip(),
    }
    destination = (
        pathlib.Path(run_root)
        / MANAGED_LAUNCH_RECEIPTS
        / str(plan["plan_id"])
        / f"{machine_id}.json"
    )
    _write_json(destination, receipt)
    return receipt


def _task_id(
    machine_id: str,
    plan_id: str,
    phase: str,
    index: int,
    records: list[dict],
) -> str:
    digest = hashlib.sha256(
        json.dumps(records, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    return f"{machine_id}_{plan_id}_{phase}_{index:03d}_{digest}"


def _completed_task_publication(task_root: pathlib.Path) -> pathlib.Path | None:
    completed = []
    for marker in sorted(task_root.glob(f"attempt-*/{COMPLETION_NAME}")):
        if not marker.is_file():
            continue
        try:
            read_publication(marker.parent)
        except (OSError, ValueError):
            continue
        completed.append(marker.parent)
    if len(completed) > 1:
        raise ValueError(f"task has multiple completed publications: {task_root}")
    return completed[0] if completed else None


def _next_task_publication(task_root: pathlib.Path) -> pathlib.Path:
    for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
        candidate = task_root / f"attempt-{attempt}"
        if not candidate.exists():
            return candidate
    raise ObjectExchangeError(
        f"task exhausted {MAX_PUBLICATION_ATTEMPTS} publication attempts: {task_root}"
    )


def _completed_machine_path(machine_output: pathlib.Path) -> pathlib.Path | None:
    completed = []
    for path in sorted(
        machine_output.glob("completion-attempt-*/machine_completion.json")
    ):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if value.get("completed") is True:
            completed.append(path)
    if len(completed) > 1:
        raise ValueError(
            f"machine has multiple completed publications: {machine_output}"
        )
    return completed[0] if completed else None


def _next_machine_completion_path(machine_output: pathlib.Path) -> pathlib.Path:
    for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
        candidate = (
            machine_output / f"completion-attempt-{attempt}"
            / "machine_completion.json"
        )
        if not candidate.parent.exists():
            return candidate
    raise ObjectExchangeError(
        "machine exhausted "
        f"{MAX_PUBLICATION_ATTEMPTS} completion attempts: {machine_output}"
    )


def _run_worker_task(
    *,
    records: list[dict],
    task_id: str,
    output: pathlib.Path,
    claims_path: pathlib.Path,
    skill_root: pathlib.Path,
    local_run_root: pathlib.Path,
    review_mode: str,
    ocr_mode: str,
    refresh_acquisition: bool = False,
    exchange_mode: str = "posix",
) -> dict:
    marker = output / "completion.json"
    completed_publication = (
        _completed_task_publication(output)
        if exchange_mode == "object-store" else None
    )
    if completed_publication is not None:
        publication = read_publication(completed_publication)
        result = dict((publication.get("metadata") or {}).get("task") or {})
        if result.get("task_id") != task_id:
            raise ValueError(f"incompatible cached task publication: {output}")
        return result
    if exchange_mode == "posix" and marker.exists():
        return json.loads(marker.read_text(encoding="utf-8"))

    records_path = local_run_root / "input_records.jsonl"
    _write_jsonl(records_path, records)
    command = [
        sys.executable,
        str(skill_root / "scripts" / "evidence_first.py"),
        "--run-root", str(local_run_root),
        "--claims", str(claims_path),
        "--records", str(records_path),
        "--review-mode", review_mode,
        "--backend", "none",
        "--ocr", ocr_mode,
        "--parse-jobs", "1",
        "--preprocess-only",
        "--run-id", task_id,
    ]
    if refresh_acquisition:
        command.append("--refresh-acquisition")
    started = time.monotonic()
    return_code, peak_rss_mb, minimum_available_mb = _run_monitored(command)
    if return_code:
        raise RuntimeError(f"{task_id} preprocessing failed with exit {return_code}")

    run_manifest_path = local_run_root / "run_manifest.json"
    shard_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    metrics = shard_manifest.get("metrics") or {}
    relaunches = 0
    if int(metrics.get("transient_retry_remaining") or 0) > 0:
        relaunches = 1
        return_code, second_peak, second_minimum = _run_monitored(command)
        peak_rss_mb = max(peak_rss_mb, second_peak)
        if second_minimum:
            minimum_available_mb = (
                min(minimum_available_mb, second_minimum)
                if minimum_available_mb else second_minimum
            )
        if return_code:
            raise RuntimeError(
                f"{task_id} transient recovery failed with exit {return_code}"
            )
        shard_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        metrics = shard_manifest.get("metrics") or {}

    publication_output = (
        _next_task_publication(output)
        if exchange_mode == "object-store" else output
    )
    result = {
        "task_id": task_id,
        "output_path": str(publication_output),
        "record_count": len(records),
        "paper_ids": [str(row.get("paper_id") or "") for row in records],
        "records_sha256": hashlib.sha256(
            json.dumps(records, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "peak_rss_mb": round(peak_rss_mb, 3),
        "minimum_available_memory_mb": round(minimum_available_mb, 3),
        "papers_full_text": int(metrics.get("papers_full_text") or 0),
        "papers_not_retrieved": int(metrics.get("papers_not_retrieved") or 0),
        "transient_retry_attempts": int(metrics.get("transient_retry_attempts") or 0),
        "transient_retry_recovered": int(metrics.get("transient_retry_recovered") or 0),
        "transient_retry_remaining": int(metrics.get("transient_retry_remaining") or 0),
        "relaunches": relaunches,
        "completed": True,
    }
    if exchange_mode == "object-store":
        publish_directory(
            local_run_root,
            publication_output,
            local_run_root / "state" / "publication" / "result.tar",
            ("fulltext", "run_manifest.json"),
            {"task": result},
        )
    else:
        output.mkdir(parents=True, exist_ok=True)
        for name in ("fulltext", "run_manifest.json"):
            source = local_run_root / name
            destination = output / name
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(source, destination)
        _write_json(marker, result)
    return result


def _run_wave(
    *,
    record_groups: list[list[dict]],
    machine: dict,
    phase: str,
    start_index: int,
    claims_path: pathlib.Path,
    skill_root: pathlib.Path,
    local_base: pathlib.Path,
    review_mode: str,
    ocr_mode: str,
    refresh_acquisition: bool = False,
    exchange_mode: str = "posix",
) -> dict:
    tasks = []
    machine_output = pathlib.Path(machine["output_path"])
    for offset, records in enumerate(record_groups):
        index = start_index + offset
        plan_id = machine_output.parent.name
        task_id = _task_id(
            machine["machine_id"], plan_id, phase, index, records
        )
        tasks.append({
            "records": records,
            "task_id": task_id,
            "output": machine_output / "tasks" / task_id,
            "local_run_root": local_base / task_id,
        })

    started = time.monotonic()
    results: list[dict] = []
    failed: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        future_to_task = {
            pool.submit(
                _run_worker_task,
                records=task["records"],
                task_id=task["task_id"],
                output=task["output"],
                claims_path=claims_path,
                skill_root=skill_root,
                local_run_root=task["local_run_root"],
                review_mode=review_mode,
                ocr_mode=ocr_mode,
                refresh_acquisition=refresh_acquisition,
                exchange_mode=exchange_mode,
            ): task
            for task in tasks
        }
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            try:
                results.append(future.result())
            except (RuntimeError, ObjectExchangeError, OSError):
                failed.append(task)

    # A pressure-related child failure must not lose a paper. Retry that task
    # serially; its partial local cache is reused, then stop further ramping.
    for task in failed:
        results.append(_run_worker_task(
            records=task["records"],
            task_id=task["task_id"],
            output=task["output"],
            claims_path=claims_path,
            skill_root=skill_root,
            local_run_root=task["local_run_root"],
            review_mode=review_mode,
            ocr_mode=ocr_mode,
            refresh_acquisition=refresh_acquisition,
            exchange_mode=exchange_mode,
        ))

    elapsed = max(time.monotonic() - started, 0.001)
    result_records = sum(int(row.get("record_count") or 0) for row in results)
    return {
        "phase": phase,
        "concurrency": len(tasks),
        "record_count": result_records,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_records_per_second": round(result_records / elapsed, 6),
        "parallel_failures_retried_serially": len(failed),
        "peak_worker_rss_mb": max(
            (float(row.get("peak_rss_mb") or 0) for row in results), default=0.0
        ),
        "minimum_available_memory_mb": min(
            (
                float(row.get("minimum_available_memory_mb") or 0)
                for row in results
                if float(row.get("minimum_available_memory_mb") or 0) > 0
            ),
            default=0.0,
        ),
        "transient_retry_remaining": sum(
            int(row.get("transient_retry_remaining") or 0) for row in results
        ),
        "transient_retry_attempts": sum(
            int(row.get("transient_retry_attempts") or 0) for row in results
        ),
        "tasks": sorted(results, key=lambda row: str(row.get("task_id") or "")),
    }


def _split_evenly(records: list[dict], workers: int) -> list[list[dict]]:
    buckets: list[list[dict]] = [[] for _ in range(min(workers, len(records)))]
    for index, record in enumerate(records):
        buckets[index % len(buckets)].append(record)
    return [bucket for bucket in buckets if bucket]


def _calibration_order(records: list[dict]) -> list[dict]:
    """Stable mixed ordering so a search facet does not bias every pilot."""
    return sorted(
        records,
        key=lambda row: hashlib.sha256(
            str(
                row.get("paper_id")
                or row.get("doi")
                or row.get("pmid")
                or row.get("title")
                or ""
            ).encode("utf-8")
        ).hexdigest(),
    )


def run_machine(
    exchange_root: pathlib.Path,
    machine_id: str,
    skill_root: pathlib.Path,
    local_base: pathlib.Path,
    review_mode: str,
    ocr_mode: str,
) -> dict:
    plan = _load_plan(exchange_root)
    exchange_mode = str(plan.get("exchange_mode") or "posix")
    machine = next(
        (row for row in plan["machines"] if row["machine_id"] == machine_id), None
    )
    if machine is None:
        raise ValueError(f"launch plan has no queue for {machine_id}")
    if review_mode != plan.get("review_mode") or ocr_mode != plan.get("ocr_mode"):
        raise ValueError(
            "run-machine mode/OCR does not match launch plan: "
            f"expected {plan.get('review_mode')}/{plan.get('ocr_mode')}, "
            f"got {review_mode}/{ocr_mode}"
        )
    machine_output = pathlib.Path(machine["output_path"])
    completion_path = (
        _completed_machine_path(machine_output)
        if exchange_mode == "object-store"
        else machine_output / "machine_completion.json"
    )
    if completion_path is not None and completion_path.exists():
        completed = json.loads(completion_path.read_text(encoding="utf-8"))
        if (
            completed.get("plan_id") != plan["plan_id"]
            or completed.get("review_mode") != review_mode
            or completed.get("ocr_mode") != ocr_mode
        ):
            raise ValueError(f"incompatible cached completion: {completion_path}")
        return completed

    local_inputs = local_base / "inputs" / str(plan["plan_id"])
    local_records = local_inputs / "records.jsonl"
    local_claims = local_inputs / pathlib.Path(str(plan["claims_path"])).name
    _copy_verified(
        pathlib.Path(machine["records_path"]),
        local_records,
        str(machine["records_sha256"]),
    )
    _copy_verified(
        pathlib.Path(plan["claims_path"]),
        local_claims,
        str(plan["claims_sha256"]),
    )
    records = _read_records(local_records)
    remaining = _calibration_order(records)
    resources = detect_machine_resources()
    configured_max = int(plan["max_processes_per_machine"])
    observed_worker_memory_mb = 0.0
    current_cap = _resource_cap(
        resources, configured_max, observed_worker_memory_mb
    )
    ramp = list(plan["concurrency_ramp"])
    if not ramp and remaining:
        ramp = [1]

    started = time.monotonic()
    waves: list[dict] = []
    all_tasks: list[dict] = []
    selected_processes = 1
    stop_reason = "record_count"
    previous_concurrency = 1
    previous_throughput = 0.0
    task_index = 0

    for concurrency in ramp:
        if concurrency > current_cap:
            stop_reason = "resource_cap"
            break
        if len(remaining) < concurrency:
            stop_reason = "insufficient_records_for_next_pilot"
            break
        pilot_records = remaining[:concurrency]
        remaining = remaining[concurrency:]
        wave = _run_wave(
            record_groups=[[record] for record in pilot_records],
            machine=machine,
            phase=f"pilot_{concurrency}",
            start_index=task_index,
            claims_path=local_claims,
            skill_root=skill_root,
            local_base=local_base,
            review_mode=review_mode,
            ocr_mode=ocr_mode,
            refresh_acquisition=bool(plan.get("refresh_acquisition")),
            exchange_mode=exchange_mode,
        )
        task_index += concurrency
        waves.append({key: value for key, value in wave.items() if key != "tasks"})
        all_tasks.extend(wave["tasks"])
        observed_worker_memory_mb = max(
            observed_worker_memory_mb, float(wave["peak_worker_rss_mb"])
        )
        current_cap = _resource_cap(
            resources, configured_max, observed_worker_memory_mb
        )

        memory_floor = resources.available_memory_mb * MIN_AVAILABLE_MEMORY_FRACTION
        memory_pressure = (
            bool(wave["minimum_available_memory_mb"])
            and wave["minimum_available_memory_mb"] < memory_floor
        )
        retry_fraction = (
            wave["transient_retry_attempts"] / max(1, wave["record_count"])
        )
        retrieval_pressure = (
            wave["transient_retry_remaining"] > 0
            or retry_fraction >= MAX_TRANSIENT_RETRY_FRACTION
        )
        process_pressure = wave["parallel_failures_retried_serially"] > 0
        plateau = (
            previous_throughput > 0
            and wave["throughput_records_per_second"]
            < previous_throughput * (1.0 + MIN_THROUGHPUT_GAIN)
        )

        if memory_pressure:
            selected_processes = previous_concurrency
            stop_reason = "memory_pressure"
            break
        if retrieval_pressure:
            selected_processes = previous_concurrency
            stop_reason = "retrieval_pressure"
            break
        if process_pressure:
            selected_processes = previous_concurrency
            stop_reason = "process_failure_pressure"
            break
        if plateau:
            selected_processes = previous_concurrency
            stop_reason = "throughput_plateau"
            break

        selected_processes = concurrency
        previous_concurrency = concurrency
        previous_throughput = float(wave["throughput_records_per_second"])
        if concurrency >= current_cap:
            stop_reason = "resource_cap"
            break
        stop_reason = "ramp_exhausted"

    selected_processes = max(1, min(selected_processes, current_cap))
    if remaining:
        final_groups = _split_evenly(remaining, selected_processes)
        wave = _run_wave(
            record_groups=final_groups,
            machine=machine,
            phase="final",
            start_index=task_index,
            claims_path=local_claims,
            skill_root=skill_root,
            local_base=local_base,
            review_mode=review_mode,
            ocr_mode=ocr_mode,
            refresh_acquisition=bool(plan.get("refresh_acquisition")),
            exchange_mode=exchange_mode,
        )
        waves.append({key: value for key, value in wave.items() if key != "tasks"})
        all_tasks.extend(wave["tasks"])

    result = {
        "schema_version": 1,
        "plan_id": plan["plan_id"],
        "machine_id": machine_id,
        "review_mode": review_mode,
        "ocr_mode": ocr_mode,
        "record_count": len(records),
        "selected_processes": selected_processes,
        "stop_reason": stop_reason,
        "resources": asdict(resources),
        "resource_cap": current_cap,
        "observed_peak_worker_rss_mb": round(observed_worker_memory_mb, 3),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "waves": waves,
        "tasks": sorted(all_tasks, key=lambda row: str(row.get("task_id") or "")),
        "completed": True,
    }
    if exchange_mode == "object-store":
        completion_path = _next_machine_completion_path(machine_output)
    assert completion_path is not None
    _exchange_write_json(completion_path, result, exchange_mode)
    return result


def _copy_tree(source: pathlib.Path, destination: pathlib.Path) -> None:
    if not source.exists():
        return
    for item in source.rglob("*"):
        if item.is_file():
            target = destination / item.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _local_artifact(run_root: pathlib.Path, subdir: str, value: object) -> object:
    if not value:
        return value
    destination = run_root / "fulltext" / subdir / pathlib.Path(str(value)).name
    if not destination.exists():
        raise ValueError(
            f"managed task did not publish referenced artifact: {destination}"
        )
    return str(destination)


def merge(exchange_root: pathlib.Path, run_root: pathlib.Path) -> dict:
    plan = _load_plan(exchange_root)
    exchange_mode = str(plan.get("exchange_mode") or "posix")
    recovery = bool(plan.get("recovery_run_root"))
    if (
        recovery
        and pathlib.Path(str(plan["recovery_run_root"])).resolve()
        != run_root.resolve()
    ):
        raise ValueError("recovery plan targets a different run root")
    completions = []
    missing = []
    for machine in plan["machines"]:
        machine_output = pathlib.Path(machine["output_path"])
        path = (
            _completed_machine_path(machine_output)
            if exchange_mode == "object-store"
            else machine_output / "machine_completion.json"
        )
        if path is None or not path.exists():
            missing.append(machine["machine_id"])
        else:
            completion = json.loads(path.read_text(encoding="utf-8"))
            if (
                completion.get("plan_id") != plan["plan_id"]
                or not completion.get("completed")
            ):
                raise ValueError(
                    f"managed completion does not match plan: {path}"
                )
            completions.append(completion)
    if missing:
        raise ValueError(
            f"incomplete managed machines: {', '.join(sorted(missing))}"
        )

    fulltext = run_root / "fulltext"
    papers_by_id: dict[str, dict] = (
        _indexed_by_paper(_read_records(fulltext / "papers.jsonl"))
        if recovery else {}
    )
    misses_by_id: dict[str, dict] = (
        _indexed_by_paper(_read_records(fulltext / "not_retrieved.jsonl"))
        if recovery else {}
    )
    task_metrics = []
    seen_task_ids: set[str] = set()
    parsed_ids: set[str] = set()
    if recovery:
        for parsed_path in (fulltext / "parsed").glob("*.json"):
            parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
            pid = str(parsed.get("paper_id") or "")
            if not pid or pid in parsed_ids:
                raise ValueError(f"existing parsed corpus has duplicate paper_id {pid!r}")
            parsed_ids.add(pid)
    for completion in completions:
        for task in completion["tasks"]:
            task_id = str(task.get("task_id") or "")
            if not task_id or task_id in seen_task_ids:
                raise ValueError(f"duplicate or missing managed task_id: {task_id!r}")
            seen_task_ids.add(task_id)
            published_output = pathlib.Path(task["output_path"])
            if exchange_mode == "object-store":
                output = (
                    run_root / "state" / "managed_downloads"
                    / str(plan["plan_id"]) / task_id
                )
                publication = materialize_directory(published_output, output)
                published_task = dict(
                    (publication.get("metadata") or {}).get("task") or {}
                )
                for field in (
                    "task_id",
                    "record_count",
                    "paper_ids",
                    "records_sha256",
                ):
                    if published_task.get(field) != task.get(field):
                        raise ValueError(
                            f"{task_id}: completion disagrees with task bundle "
                            f"for {field}"
                        )
            else:
                output = published_output
            source_fulltext = output / "fulltext"
            for subdir in ("pdfs", "figures"):
                _copy_tree(source_fulltext / subdir, fulltext / subdir)
            task_papers = _read_records(source_fulltext / "papers.jsonl")
            task_misses = _read_records(source_fulltext / "not_retrieved.jsonl")
            expected_ids = [str(value) for value in (task.get("paper_ids") or [])]
            if len(expected_ids) != int(task.get("record_count") or 0):
                raise ValueError(f"{task_id}: task paper_ids do not match record_count")
            outcome_ids = [
                str(row.get("paper_id") or "") for row in task_papers + task_misses
            ]
            if len(outcome_ids) != len(set(outcome_ids)):
                raise ValueError(f"{task_id}: duplicate paper outcome")
            if set(outcome_ids) != set(expected_ids):
                raise ValueError(
                    f"{task_id}: outcomes do not match task paper_ids"
                )
            for paper in task_papers:
                paper = dict(paper)
                for key in ("local_pdf", "local_xml", "figures_pdf"):
                    if paper.get(key):
                        paper[key] = _local_artifact(run_root, "pdfs", paper[key])
                pid = str(paper["paper_id"])
                if recovery:
                    misses_by_id.pop(pid, None)
                if pid in papers_by_id or (pid in misses_by_id and not recovery):
                    raise ValueError(f"paper {pid!r} appears in multiple managed tasks")
                papers_by_id[pid] = paper
            for miss in task_misses:
                pid = str(miss["paper_id"])
                if recovery:
                    misses_by_id.pop(pid, None)
                if pid in papers_by_id or (pid in misses_by_id and not recovery):
                    raise ValueError(f"paper {pid!r} appears in multiple managed tasks")
                misses_by_id[pid] = miss
            for parsed_path in (source_fulltext / "parsed").glob("*.json"):
                parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
                parsed_pid = str(parsed.get("paper_id") or "")
                if not parsed_pid or parsed_pid in parsed_ids:
                    raise ValueError(
                        f"{task_id}: duplicate or missing parsed paper_id {parsed_pid!r}"
                    )
                parsed_ids.add(parsed_pid)
                for figure in parsed.get("figures", []) or []:
                    if figure.get("image_path"):
                        figure["image_path"] = _local_artifact(
                            run_root, "figures", figure["image_path"]
                        )
                _write_json(fulltext / "parsed" / parsed_path.name, parsed)
            task_metrics.append(task)

    if parsed_ids != set(papers_by_id):
        missing_parsed = sorted(set(papers_by_id) - parsed_ids)
        extra_parsed = sorted(parsed_ids - set(papers_by_id))
        raise ValueError(
            "managed parsed artifacts do not match retrieved papers: "
            f"missing={missing_parsed[:8]} extra={extra_parsed[:8]}"
        )

    canonical_records = run_root / "corpus" / "records.jsonl"
    source_records = pathlib.Path(plan["records_path"])
    if exchange_mode == "object-store":
        local_records = (
            run_root / "state" / "managed_downloads"
            / str(plan["plan_id"]) / "inputs" / "records.jsonl"
        )
        _copy_verified(
            source_records,
            local_records,
            str(plan["records_sha256"]),
        )
        source_records = local_records
    original = _read_records(
        canonical_records if recovery and canonical_records.exists()
        else source_records
    )
    papers = [
        papers_by_id[str(row.get("paper_id") or "")]
        for row in original
        if str(row.get("paper_id") or "") in papers_by_id
    ]
    misses = [
        misses_by_id[str(row.get("paper_id") or "")]
        for row in original
        if str(row.get("paper_id") or "") in misses_by_id
    ]
    if len(papers) + len(misses) != len(original):
        raise ValueError("managed tasks do not account for every input record")
    _write_jsonl(fulltext / "papers.jsonl", papers)
    _write_jsonl(fulltext / "not_retrieved.jsonl", misses)
    parse_quality_rows = []
    for row in papers:
        parsed_path = fulltext / "parsed" / f"{safe_id(row['paper_id'])}.json"
        parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
        quality = parsed.get("parse_quality") or assess_parse_quality(
            parsed, min_sentences=DEFAULT_MIN_SUBSTANTIVE_SENTENCES,
        )
        parsed["parse_quality"] = quality
        _write_json(parsed_path, parsed)
        parse_quality_rows.append(quality)
    _write_jsonl(fulltext / "parse_quality.jsonl", parse_quality_rows)
    outcomes = {str(row["paper_id"]): ("parsed", row) for row in papers}
    outcomes.update({
        str(row["paper_id"]): ("not_retrieved", row) for row in misses
    })
    _write_jsonl(fulltext / "acquisition_routes.jsonl", [
        {
            "schema_version": 1,
            "paper_id": str(record.get("paper_id") or ""),
            "outcome": status,
            "access_state": str(record.get("access_state") or ""),
            "attempts": list(record.get("_attempts") or []),
            "final_reason": str(record.get("_not_retrieved_reason") or ""),
            "user_supplied": bool(record.get("_user_supplied")),
        }
        for selected in original
        for status, record in [outcomes[str(selected.get("paper_id") or "")]]
    ])

    transient_remaining = sum(
        1 for row in misses
        if str(row.get("_not_retrieved_kind") or "") == "retrieval_failed"
    )
    retry_input_count = int(plan.get("record_count") or 0) if recovery else 0
    retry_report = {
        "schema_version": 1,
        "completed": recovery or transient_remaining == 0,
        "attempted": retry_input_count,
        "recovered": (
            retry_input_count
            - sum(
                1 for row in _read_records(source_records)
                if str(row.get("paper_id") or "") in misses_by_id
            )
            if recovery else 0
        ),
        "remaining": transient_remaining,
        "reason": (
            "global managed retry completed"
            if recovery else
            "global managed retry required"
            if transient_remaining else
            "no transient retrieval failures remained after merge"
        ),
    }
    _write_json(fulltext / "global_transient_retry.json", retry_report)

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    metrics = manifest.setdefault("metrics", {})
    managed_metrics = {
        "adaptive": True,
        "exchange_mode": exchange_mode,
        "plan_id": plan["plan_id"],
        "machine_count": plan["machine_count"],
        "papers_per_machine_target": plan["papers_per_machine_target"],
        "max_processes_per_machine": plan["max_processes_per_machine"],
        "skill_bundle_sha256": plan.get("skill_bundle_sha256", ""),
        "skill_git_commit": plan.get("skill_git_commit", ""),
        "selected_processes_by_machine": {
            row["machine_id"]: row["selected_processes"] for row in completions
        },
        "max_selected_processes": max(
            (int(row["selected_processes"]) for row in completions), default=1
        ),
        "task_count": len(task_metrics),
        "papers_full_text": len(papers),
        "papers_not_retrieved": len(misses),
        "critical_path_seconds": max(
            (float(row.get("elapsed_seconds") or 0) for row in completions),
            default=0,
        ),
        "total_worker_seconds": sum(
            float(row.get("elapsed_seconds") or 0) for row in task_metrics
        ),
        "machines": completions,
        "background_launches": [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(
                (
                    run_root
                    / MANAGED_LAUNCH_RECEIPTS
                    / str(plan["plan_id"])
                ).glob("*.json")
            )
        ],
        "tasks": task_metrics,
        "recovery_wave": recovery,
        "global_transient_retry": retry_report,
    }
    metrics["managed_recovery" if recovery else "managed_machines"] = managed_metrics
    _write_json(manifest_path, manifest)
    if (run_root / "corpus" / "references.jsonl").exists():
        refresh_corpus_ledger(run_root)
    result = managed_metrics
    _exchange_write_json(
        exchange_root / "merge_manifest.json", result, exchange_mode
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    default_skill_root = pathlib.Path(__file__).resolve().parent.parent

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--records", required=True, type=pathlib.Path)
    prepare_parser.add_argument("--claims", required=True, type=pathlib.Path)
    prepare_parser.add_argument("--exchange-root", required=True, type=pathlib.Path)
    prepare_parser.add_argument("--max-machines", type=int, default=DEFAULT_MAX_MACHINES)
    prepare_parser.add_argument(
        "--max-processes-per-machine",
        "--processes-per-machine",
        dest="max_processes_per_machine",
        type=int,
        default=DEFAULT_MAX_PROCESSES_PER_MACHINE,
    )
    prepare_parser.add_argument(
        "--review-mode", choices=("quick", "deep", "broad"), default="broad"
    )
    prepare_parser.add_argument(
        "--ocr", choices=("off", "targeted", "all"), default="targeted"
    )
    prepare_parser.add_argument(
        "--exchange-mode",
        choices=EXCHANGE_MODES,
        default="object-store",
    )
    prepare_parser.add_argument(
        "--skill-root", type=pathlib.Path, default=default_skill_root
    )

    retry_parser = subparsers.add_parser("prepare-retry")
    retry_parser.add_argument("--run-root", required=True, type=pathlib.Path)
    retry_parser.add_argument("--claims", required=True, type=pathlib.Path)
    retry_parser.add_argument("--exchange-root", required=True, type=pathlib.Path)
    retry_parser.add_argument("--max-machines", type=int, default=DEFAULT_MAX_MACHINES)
    retry_parser.add_argument(
        "--max-processes-per-machine",
        type=int,
        default=DEFAULT_MAX_PROCESSES_PER_MACHINE,
    )
    retry_parser.add_argument(
        "--review-mode", choices=("quick", "deep", "broad"), default="broad"
    )
    retry_parser.add_argument(
        "--ocr", choices=("off", "targeted", "all"), default="targeted"
    )
    retry_parser.add_argument(
        "--exchange-mode",
        choices=EXCHANGE_MODES,
        default="object-store",
    )
    retry_parser.add_argument(
        "--skill-root", type=pathlib.Path, default=default_skill_root
    )

    machine_parser = subparsers.add_parser("run-machine")
    machine_parser.add_argument("--exchange-root", required=True, type=pathlib.Path)
    machine_parser.add_argument("--machine-id", required=True)
    machine_parser.add_argument("--skill-root", required=True, type=pathlib.Path)
    machine_parser.add_argument("--local-base", required=True, type=pathlib.Path)
    machine_parser.add_argument(
        "--review-mode", choices=("quick", "deep", "broad"), default="broad"
    )
    machine_parser.add_argument(
        "--ocr", choices=("off", "targeted", "all"), default="targeted"
    )

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--exchange-root", required=True, type=pathlib.Path)
    merge_parser.add_argument("--run-root", required=True, type=pathlib.Path)

    launch_parser = subparsers.add_parser("record-launch")
    launch_parser.add_argument("--run-root", required=True, type=pathlib.Path)
    launch_parser.add_argument("--exchange-root", required=True, type=pathlib.Path)
    launch_parser.add_argument("--machine-id", required=True)
    launch_parser.add_argument("--background-name", required=True)
    launch_parser.add_argument("--job-id", default="")

    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(
            args.records,
            args.claims,
            args.exchange_root,
            args.max_machines,
            args.max_processes_per_machine,
            args.review_mode,
            args.ocr,
            exchange_mode=args.exchange_mode,
            skill_root=args.skill_root,
        )
    elif args.action == "prepare-retry":
        result = prepare_retry(
            args.run_root,
            args.claims,
            args.exchange_root,
            args.max_machines,
            args.max_processes_per_machine,
            args.review_mode,
            args.ocr,
            exchange_mode=args.exchange_mode,
            skill_root=args.skill_root,
        )
    elif args.action == "run-machine":
        result = run_machine(
            args.exchange_root,
            args.machine_id,
            args.skill_root,
            args.local_base,
            args.review_mode,
            args.ocr,
        )
    elif args.action == "record-launch":
        result = record_background_launch(
            args.run_root,
            args.exchange_root,
            args.machine_id,
            args.background_name,
            args.job_id,
        )
    else:
        result = merge(args.exchange_root, args.run_root)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
