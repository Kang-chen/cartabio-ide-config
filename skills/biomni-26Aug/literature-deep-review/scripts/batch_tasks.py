#!/usr/bin/env python3
"""Emit and assemble independent Biomni worker tasks without shared appends."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import math
import pathlib
import re
import sys
from typing import Callable

from adjudication_batches import atomic_json
from llm_adjudicator import AdjudicationError, request_json

from semantic_verification import (
    MATCH_FLAGS,
    RESULT_TYPES,
    blinded_payload,
    validate_verdict,
    verdict_id,
)


GROUNDING_STANCES = frozenset({"supports", "contradicts"})
NON_GROUNDING_KIND = "inferred"
WORKER_KINDS = {
    "adjudications": "evidence/adjudication_batches",
    "entailment": "evidence/entailment_tasks",
    "narratives": "deliverables/narrative_tasks",
}
DIRECT_KINDS = frozenset({"entailment", "narratives"})
DEFAULT_DIRECT_JOBS = 8
MAX_DIRECT_JOBS = 16
DEFAULT_NATIVE_PACK_TASKS = {
    "adjudications": 4,
    "entailment": 8,
    "narratives": 4,
}
DEFAULT_NATIVE_TARGET_PACKS = {
    "adjudications": 8,
    "entailment": 8,
    "narratives": 8,
}
DEFAULT_NATIVE_PACK_CHARS = 180_000

RequestJson = Callable[..., tuple[dict, dict]]

_ENTAILMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "entailment": {"type": "string", "enum": ["yes", "partial", "no"]},
        **{flag: {"type": "boolean"} for flag in MATCH_FLAGS},
        "result_type": {"type": "string", "enum": sorted(RESULT_TYPES)},
        "scope_overreach": {"type": "boolean"},
        "reviewer": {"type": "string"},
        "rationale": {"type": "string"},
        "verified_at": {"type": "string"},
    },
    "required": [
        "entailment", *MATCH_FLAGS, "result_type", "scope_overreach",
        "reviewer", "rationale", "verified_at",
    ],
    "additionalProperties": False,
}

_NARRATIVE_STATEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "inference": {"type": "boolean"},
        "no_qualifying_anchor": {"type": "boolean"},
    },
    "required": ["text", "evidence_ids", "inference"],
    "additionalProperties": False,
}
_NARRATIVE_FACETS = (
    "observed_result",
    "authors_interpretation",
    "reviewer_inference",
    "contradiction",
    "evidence_gap",
)
_NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "claim_id": {"type": "string"},
        "observed_result": _NARRATIVE_STATEMENT_SCHEMA,
        **{
            facet: {"anyOf": [_NARRATIVE_STATEMENT_SCHEMA, {"type": "null"}]}
            for facet in _NARRATIVE_FACETS[1:]
        },
    },
    "required": ["claim_id", *_NARRATIVE_FACETS],
    "additionalProperties": False,
}


def read_jsonl(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path} line {line_number} is not a JSON object")
        rows.append(value)
    return rows


def atomic_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temp.replace(path)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:160]


def _under_root(root: pathlib.Path, relative: str) -> pathlib.Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"task output escapes run root: {relative!r}") from exc
    return path


def _worker_output(
    root: pathlib.Path,
    task: dict,
    *,
    exchange_root: pathlib.Path | None,
    kind: str,
) -> pathlib.Path:
    if exchange_root is None:
        return _under_root(root, str(task.get("output_path") or ""))
    filename = pathlib.Path(str(task.get("output_path") or "")).name
    if not filename:
        raise ValueError("task has no output_path filename")
    return exchange_root.resolve() / "outputs" / kind / filename


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_time(value: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _task_unit(task: pathlib.Path, output: pathlib.Path) -> dict:
    payload = json.loads(task.read_text(encoding="utf-8"))
    return {
        "task_file": task.name,
        "task_id": str(payload.get("task_id") or payload.get("batch_id") or ""),
        "output_file": output.name,
        "output_sha256": _sha256(output),
    }


def _inventory_path(exchange_root: pathlib.Path, kind: str) -> pathlib.Path:
    return exchange_root.resolve() / "inventory" / f"{kind}.json"


def _validate_inventory(
    root: pathlib.Path,
    exchange_root: pathlib.Path,
    kind: str,
    tasks: list[pathlib.Path],
) -> dict:
    path = _inventory_path(exchange_root, kind)
    if not path.exists():
        raise ValueError(f"worker inventory missing: {path}")
    inventory = json.loads(path.read_text(encoding="utf-8"))
    expected = {str(row["task_file"]): row for row in inventory.get("tasks") or []}
    actual = {task.name: task for task in tasks}
    if set(expected) != set(actual):
        raise ValueError(
            f"{kind} task set drifted after staging: "
            f"expected={len(expected)} actual={len(actual)}"
        )
    outputs: set[str] = set()
    for name, task_path in actual.items():
        source_hash = _sha256(task_path)
        if source_hash != str(expected[name].get("source_sha256") or ""):
            raise ValueError(f"{kind} source task changed after staging: {name}")
        staged_path = exchange_root.resolve() / "tasks" / kind / name
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        if staged.get("_source_task_sha256") != source_hash:
            raise ValueError(f"{kind} staged task hash mismatch: {name}")
        output = str(staged.get("output_path") or "")
        if not output or output in outputs:
            raise ValueError(f"{kind} task has duplicate or missing output: {name}")
        if pathlib.Path(output).name != str(expected[name].get("output_file") or ""):
            raise ValueError(f"{kind} staged output changed after staging: {name}")
        outputs.add(output)
    return inventory


def _assembly_receipt(
    root: pathlib.Path,
    kind: str,
    tasks: list[pathlib.Path],
    outputs: list[pathlib.Path],
    destination: pathlib.Path,
    units: list[dict] | None = None,
) -> None:
    if units is None:
        units = [_task_unit(task, output) for task, output in zip(tasks, outputs)]
    completed_at = _utc_now()
    timing_path = root / "state" / "timings" / f"{kind}.json"
    timing = {}
    if timing_path.exists():
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
    started_at = str(timing.get("started_at") or "")
    started = _parse_time(started_at)
    completed = _parse_time(completed_at)
    elapsed = (
        round(max(0.0, (completed - started).total_seconds()), 3)
        if started is not None and completed is not None else None
    )
    timing.update({
        "kind": kind,
        "started_at": started_at or None,
        "completed_at": completed_at,
        "elapsed_seconds": elapsed,
        "task_count": len(tasks),
    })
    atomic_json(timing_path, timing)
    receipt = {
        "schema_version": 2,
        "kind": kind,
        "task_count": len(tasks),
        "output_count": len(outputs),
        "task_sha256": {path.name: _sha256(path) for path in tasks},
        "output_sha256": {path.name: _sha256(path) for path in outputs},
        "destination": str(destination.relative_to(root)),
        "destination_sha256": _sha256(destination),
        "complete": len(tasks) == len(outputs),
        "units": units,
        "timing": timing,
    }
    atomic_json(root / "state" / "assemblies" / f"{kind}.json", receipt)
    manifest_path = root / "run_manifest.json"
    if elapsed is not None and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        metrics = manifest.setdefault("metrics", {})
        metrics.setdefault("stage_timings_seconds", {})[f"native_{kind}"] = elapsed
        atomic_json(manifest_path, manifest)


def stage_workers(
    root: pathlib.Path, exchange_root: pathlib.Path, kind: str
) -> tuple[pathlib.Path, int]:
    """Stage tasks, preserving completed outputs when the source is unchanged."""
    source_dir = root / WORKER_KINDS[kind]
    tasks = sorted(source_dir.glob("*.json"))
    if not tasks:
        raise ValueError(f"no {kind} task files under {source_dir}")
    destination = exchange_root.resolve() / "tasks" / kind
    outputs = exchange_root.resolve() / "outputs" / kind
    destination.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    source_names = {path.name for path in tasks}
    for stale in destination.glob("*.json"):
        if stale.name in source_names:
            continue
        try:
            stale_task = json.loads(stale.read_text(encoding="utf-8"))
            stale_output = pathlib.Path(str(stale_task.get("output_path") or ""))
            if stale_output.parent == outputs and stale_output.exists():
                stale_output.unlink()
        except (json.JSONDecodeError, OSError):
            pass
        stale.unlink()
    inventory_rows = []
    fingerprint = hashlib.sha256(
        "".join(f"{path.name}:{_sha256(path)}\n" for path in tasks).encode()
    ).hexdigest()
    timing_path = root / "state" / "timings" / f"{kind}.json"
    previous_timing = {}
    if timing_path.exists():
        previous_timing = json.loads(timing_path.read_text(encoding="utf-8"))
    started_at = (
        str(previous_timing.get("started_at") or "")
        if previous_timing.get("task_fingerprint") == fingerprint else ""
    )
    atomic_json(timing_path, {
        "kind": kind,
        "task_fingerprint": fingerprint,
        "task_count": len(tasks),
        "started_at": started_at or _utc_now(),
        "completed_at": None,
        "elapsed_seconds": None,
    })
    for task_path in tasks:
        source_bytes = task_path.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        task = json.loads(source_bytes)
        output_name = pathlib.Path(str(task.get("output_path") or "")).name
        if not output_name:
            raise ValueError(f"{task_path}: task has no output_path filename")
        worker_output = outputs / output_name
        staged_path = destination / task_path.name
        previous_hash = ""
        if staged_path.exists():
            try:
                previous = json.loads(staged_path.read_text(encoding="utf-8"))
                previous_hash = str(previous.get("_source_task_sha256") or "")
            except (json.JSONDecodeError, OSError):
                previous_hash = ""
        if worker_output.exists() and previous_hash != source_hash:
            worker_output.unlink()
        task["output_path"] = str(worker_output)
        task["_source_task_sha256"] = source_hash
        task["instructions"] = (
            str(task.get("instructions") or task.get("prompt") or "").strip()
            + " Write only the required result to output_path on the shared mount."
        ).strip()
        staged_path.write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        inventory_rows.append({
            "task_file": task_path.name,
            "task_id": str(task.get("task_id") or task.get("batch_id") or ""),
            "source_sha256": source_hash,
            "output_file": output_name,
        })
    atomic_json(_inventory_path(exchange_root, kind), {
        "schema_version": 1,
        "kind": kind,
        "task_count": len(tasks),
        "tasks": inventory_rows,
    })
    packs_path, pack_count = pack_native(exchange_root, kind)
    print(
        f"NATIVE-PACKS: kind={kind} packs={pack_count} path={packs_path}"
    )
    return destination, len(tasks)


def pack_native(
    exchange_root: pathlib.Path,
    kind: str,
    *,
    max_tasks: int | None = None,
    max_chars: int = DEFAULT_NATIVE_PACK_CHARS,
) -> tuple[pathlib.Path, int]:
    """Pack staged tasks into bounded coordinator turns without merging outputs."""
    if kind not in WORKER_KINDS:
        raise ValueError(f"unknown worker kind: {kind}")
    source = exchange_root.resolve() / "tasks" / kind
    all_task_paths = sorted(source.glob("*.json"))
    if not all_task_paths:
        raise ValueError(f"no staged {kind} tasks under {source}")
    task_limit = max_tasks or max(
        DEFAULT_NATIVE_PACK_TASKS[kind],
        math.ceil(len(all_task_paths) / DEFAULT_NATIVE_TARGET_PACKS[kind]),
    )
    if task_limit < 1 or max_chars < 1:
        raise ValueError("native pack limits must be positive")

    destination = exchange_root.resolve() / "native_packs" / kind
    destination.mkdir(parents=True, exist_ok=True)
    for stale in destination.glob("pack_*.json"):
        stale.unlink()

    pending_tasks: list[tuple[pathlib.Path, dict]] = []
    for task_path in all_task_paths:
        task = json.loads(task_path.read_text(encoding="utf-8"))
        output = pathlib.Path(str(task.get("output_path") or ""))
        if not output.exists():
            pending_tasks.append((task_path, task))
    if kind == "narratives":
        pending_tasks.sort(
            key=lambda item: (str(item[1].get("cluster") or ""), item[0].name)
        )

    packs: list[list[tuple[pathlib.Path, dict, int]]] = []
    current: list[tuple[pathlib.Path, dict, int]] = []
    current_chars = 0
    for task_path, task in pending_tasks:
        encoded_chars = len(json.dumps(task, ensure_ascii=False))
        if current and (
            len(current) >= task_limit
            or current_chars + encoded_chars > max_chars
        ):
            packs.append(current)
            current = []
            current_chars = 0
        current.append((task_path, task, encoded_chars))
        current_chars += encoded_chars
    if current:
        packs.append(current)

    manifest_rows = []
    for index, packed_tasks in enumerate(packs, 1):
        pack_id = f"pack_{index:04d}"
        task_values = [task for _path, task, _chars in packed_tasks]
        payload = {
            "pack_id": pack_id,
            "kind": kind,
            "task_count": len(task_values),
            "instructions": (
                "Complete every task independently using native Biomni reasoning. "
                "Write each result only to that task's output_path; do not emit a "
                "combined result. Do not transfer conclusions, labels, or evidence "
                "between tasks. For entailment, preserve the blinded payload."
            ),
            "tasks": task_values,
        }
        pack_path = destination / f"{pack_id}.json"
        atomic_json(pack_path, payload)
        manifest_rows.append({
            "pack_id": pack_id,
            "path": str(pack_path),
            "task_count": len(task_values),
            "char_count": sum(chars for _path, _task, chars in packed_tasks),
            "task_files": [path.name for path, _task, _chars in packed_tasks],
            "clusters": sorted({
                str(task.get("cluster") or "") for _path, task, _chars in packed_tasks
                if str(task.get("cluster") or "")
            }),
        })
    atomic_json(destination / "manifest.json", {
        "kind": kind,
        "task_count": len(all_task_paths),
        "pending_task_count": len(pending_tasks),
        "completed_task_count": len(all_task_paths) - len(pending_tasks),
        "pack_count": len(packs),
        "max_tasks_per_pack": task_limit,
        "max_pack_chars": max_chars,
        "packs": manifest_rows,
    })
    return destination, len(packs)


def assemble_adjudications(
    root: pathlib.Path, exchange_root: pathlib.Path | None = None
) -> tuple[pathlib.Path, int]:
    tasks = sorted((root / "evidence" / "adjudication_batches").glob("batch_*.json"))
    if not tasks:
        raise ValueError("no adjudication task files; run evidence_first.py first")
    rows: list[dict] = []
    missing: list[str] = []
    outputs: list[pathlib.Path] = []
    completed_units: list[dict] = []
    audit_rows: list[dict] = []
    if exchange_root is not None:
        _validate_inventory(root, exchange_root, "adjudications", tasks)
    for task_path in tasks:
        task = json.loads(task_path.read_text(encoding="utf-8"))
        output = _worker_output(
            root, task, exchange_root=exchange_root, kind="adjudications"
        )
        if not output.exists():
            missing.append(str(output))
            continue
        outputs.append(output)
        output_rows = read_jsonl(output)
        audit_values = [row["_decision_audit"] for row in output_rows
                        if set(row) == {"_decision_audit"}]
        task_rows = [row for row in output_rows if set(row) != {"_decision_audit"}]
        paper = str(task.get("paper_id") or "")
        claim_ids = [str(value) for value in (task.get("claim_ids") or [])]
        block_ids = [str(value) for value in (task.get("block_ids") or [])]
        seen_rows: set[tuple[str, str, str]] = set()
        for row in task_rows:
            row_key = (
                str(row.get("paper_id") or ""),
                str(row.get("claim_id") or ""),
                str(row.get("block_id") or ""),
            )
            if paper and row_key[0] != paper:
                raise ValueError(
                    f"{task_path.name}: output paper_id {row_key[0]!r} "
                    f"does not match {paper!r}"
                )
            if claim_ids and row_key[1] not in claim_ids:
                raise ValueError(
                    f"{task_path.name}: output claim_id {row_key[1]!r} is outside batch"
                )
            if block_ids and row_key[2] not in block_ids:
                raise ValueError(
                    f"{task_path.name}: output block_id {row_key[2]!r} is outside batch"
                )
            if row_key in seen_rows:
                raise ValueError(
                    f"{task_path.name}: duplicate adjudication output {row_key!r}"
                )
            seen_rows.add(row_key)
        unique_accepted_blocks = {key[2] for key in seen_rows if key[2]}
        if task.get("audit_required"):
            if len(audit_values) != 1 or not isinstance(audit_values[0], dict):
                raise ValueError(
                    f"{task_path.name}: requires exactly one final _decision_audit object"
                )
            audit = dict(audit_values[0])
            reviewed = int(audit.get("candidate_blocks_reviewed") or -1)
            accepted_blocks = int(audit.get("accepted_blocks") or 0)
            rejected_blocks = int(audit.get("rejected_blocks") or 0)
            if reviewed != len(block_ids):
                raise ValueError(
                    f"{task_path.name}: audit reviewed {reviewed} blocks; expected "
                    f"{len(block_ids)}"
                )
            if accepted_blocks != len(unique_accepted_blocks):
                raise ValueError(
                    f"{task_path.name}: audit accepted_blocks={accepted_blocks}; "
                    f"observed {len(unique_accepted_blocks)}"
                )
            if accepted_blocks + rejected_blocks != reviewed:
                raise ValueError(
                    f"{task_path.name}: audit accepted+rejected does not equal reviewed"
                )
            reasons = audit.get("rejection_reasons")
            if not isinstance(reasons, dict) or sum(int(v) for v in reasons.values()) != rejected_blocks:
                raise ValueError(
                    f"{task_path.name}: rejection_reasons must account for every rejected block"
                )
            audit_rows.append({
                "batch_id": str(task.get("batch_id") or task.get("task_id") or ""),
                "paper_id": paper,
                **audit,
                "audit_status": "complete",
            })
        else:
            audit_rows.append({
                "batch_id": str(task.get("batch_id") or task.get("task_id") or ""),
                "paper_id": paper,
                "candidate_blocks_reviewed": len(block_ids),
                "accepted_blocks": len(unique_accepted_blocks),
                "rejected_blocks": max(0, len(block_ids) - len(unique_accepted_blocks)),
                "rejection_reasons": {},
                "audit_status": "legacy_not_required",
            })
        rows.extend(task_rows)
        completed_units.append({
            "task_file": task_path.name,
            "batch_id": str(task.get("batch_id") or task.get("task_id") or ""),
            "paper_id": paper,
            "claim_ids": claim_ids,
            "block_ids": block_ids,
            "candidate_block_count": int(task.get("n_blocks") or len(block_ids)),
            "accepted_row_count": len(task_rows),
            "accepted_block_count": int(audit_rows[-1]["accepted_blocks"]),
            "rejected_block_count": int(audit_rows[-1]["rejected_blocks"]),
            "audit_status": str(audit_rows[-1]["audit_status"]),
            "output_file": output.name,
            "output_sha256": _sha256(output),
        })
    if missing:
        raise ValueError(
            f"{len(missing)} adjudication output(s) missing: {', '.join(missing[:8])}"
        )
    destination = root / "evidence" / "adjudications.jsonl"
    atomic_jsonl(destination, rows)
    atomic_jsonl(root / "evidence" / "adjudication_audit.jsonl", audit_rows)
    _assembly_receipt(
        root,
        "adjudications",
        tasks,
        outputs,
        destination,
        units=completed_units,
    )
    return destination, len(rows)


def emit_entailment_tasks(root: pathlib.Path) -> int:
    claims = {
        str(row.get("claim_id") or ""): row
        for row in read_jsonl(root / "corpus" / "claims.jsonl")
    }
    evidence = read_jsonl(root / "evidence" / "evidence.jsonl")
    tasks_dir = root / "evidence" / "entailment_tasks"
    if tasks_dir.exists():
        for stale in tasks_dir.glob("*.json"):
            stale.unlink()
    tasks_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir = root / "evidence" / "entailment_verdicts"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for row in sorted(evidence, key=lambda item: str(item.get("evidence_id") or "")):
        if row.get("stance") not in GROUNDING_STANCES:
            continue
        if row.get("evidence_kind") == NON_GROUNDING_KIND:
            continue
        claim_id = str(row.get("claim_id") or "")
        evidence_id = str(row.get("evidence_id") or "")
        if not claim_id or not evidence_id or claim_id not in claims:
            raise ValueError(
                f"grounding evidence has unresolved claim/evidence id: {row!r}"
            )
        filename = f"{_safe(evidence_id)}.json"
        output_path = f"evidence/entailment_verdicts/{filename}"
        task = {
            "task_id": f"entailment:{evidence_id}",
            "payload": blinded_payload(claims[claim_id], row),
            "output_path": output_path,
            "instructions": (
                "Judge only the supplied claim, scope, and quote. Do not inspect "
                "the first-pass stance, evidence kind, rationale, or support tier. "
                "Write one JSON object to output_path."
            ),
            "required_output": {
                "entailment": "yes|partial|no",
                **{flag: "boolean" for flag in MATCH_FLAGS},
                "result_type": "|".join(sorted(RESULT_TYPES)),
                "scope_overreach": "boolean",
                "reviewer": "stable reviewer/model identifier",
                "rationale": "short explanation",
                "verified_at": "ISO-8601 timestamp or empty string",
            },
        }
        destination = tasks_dir / filename
        destination.write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        count += 1
    print(
        f"ENTAILMENT-TASKS: {count} independent unit(s) -> {tasks_dir} "
        "(native Biomni is default; stage-workers emits bounded native packs)"
    )
    return count


def assemble_entailment(
    root: pathlib.Path, exchange_root: pathlib.Path | None = None
) -> tuple[pathlib.Path, int]:
    tasks = sorted((root / "evidence" / "entailment_tasks").glob("*.json"))
    if not tasks:
        raise ValueError("no entailment tasks; run batch_tasks.py emit-entailment")
    verdicts: list[dict] = []
    missing: list[str] = []
    failures: list[str] = []
    outputs: list[pathlib.Path] = []
    if exchange_root is not None:
        _validate_inventory(root, exchange_root, "entailment", tasks)
    for task_path in tasks:
        task = json.loads(task_path.read_text(encoding="utf-8"))
        payload = task.get("payload") or {}
        output = _worker_output(
            root, task, exchange_root=exchange_root, kind="entailment"
        )
        if not output.exists():
            missing.append(str(output))
            continue
        outputs.append(output)
        verdict = json.loads(output.read_text(encoding="utf-8"))
        if not isinstance(verdict, dict):
            failures.append(f"{output}: verdict is not a JSON object")
            continue
        verdict["claim_id"] = str(payload.get("claim_id") or "")
        verdict["evidence_id"] = str(payload.get("evidence_id") or "")
        verdict["verdict_id"] = verdict_id(
            verdict["claim_id"], verdict["evidence_id"], str(verdict.get("reviewer") or "")
        )
        errors = validate_verdict(verdict)
        if errors:
            failures.extend(f"{output}: {error}" for error in errors)
            continue
        verdicts.append(verdict)
    if missing:
        failures.append(
            f"{len(missing)} entailment output(s) missing: {', '.join(missing[:8])}"
        )
    if failures:
        raise ValueError("; ".join(failures))
    destination = root / "evidence" / "entailment.jsonl"
    atomic_jsonl(destination, verdicts)
    _assembly_receipt(root, "entailment", tasks, outputs, destination)
    return destination, len(verdicts)


def assemble_narratives(
    root: pathlib.Path, exchange_root: pathlib.Path | None = None
) -> tuple[pathlib.Path, int]:
    tasks = sorted((root / "deliverables" / "narrative_tasks").glob("*.json"))
    if not tasks:
        raise ValueError("no narrative tasks; run grounded_quotes.py first")
    narratives: list[dict] = []
    missing: list[str] = []
    outputs: list[pathlib.Path] = []
    if exchange_root is not None:
        _validate_inventory(root, exchange_root, "narratives", tasks)
    for task_path in tasks:
        task = json.loads(task_path.read_text(encoding="utf-8"))
        output = _worker_output(
            root, task, exchange_root=exchange_root, kind="narratives"
        )
        if not output.exists():
            missing.append(str(output))
            continue
        outputs.append(output)
        narrative = json.loads(output.read_text(encoding="utf-8"))
        expected = str(task.get("claim_id") or "")
        if not isinstance(narrative, dict) or narrative.get("claim_id") != expected:
            raise ValueError(f"{output}: expected one narrative for {expected}")
        narratives.append(narrative)
    if missing:
        raise ValueError(
            f"{len(missing)} narrative output(s) missing: {', '.join(missing[:8])}"
        )
    destination = root / "deliverables" / "claim_narratives.jsonl"
    atomic_jsonl(destination, narratives)
    _assembly_receipt(root, "narratives", tasks, outputs, destination)
    return destination, len(narratives)


def _direct_spec(kind: str) -> tuple[dict, str, str]:
    if kind == "entailment":
        return (
            _ENTAILMENT_SCHEMA,
            "anchor_entailment",
            "Independently judge only the supplied blinded claim, scope, and "
            "quote. Do not infer first-pass labels. Assess direction, population, "
            "intervention, outcome, whether the statement is this paper's original "
            "result, and scope overreach. Return only the required JSON object.",
        )
    if kind == "narratives":
        return (
            _NARRATIVE_SCHEMA,
            "claim_narrative",
            "Write one concise scientific narrative for the supplied claim using "
            "only its accepted anchors. observed_result is required. Cite only "
            "evidence_ids present in the task. Use null for an unsupported optional "
            "facet. A reviewer inference or evidence gap with no evidence_ids must "
            "set inference=true. An uncited contradiction must additionally set "
            "no_qualifying_anchor=true; otherwise use null. Never invent an "
            "experiment, result, or citation. "
            "Return only the required JSON object.",
        )
    raise ValueError(f"direct provider execution does not support kind={kind!r}")


def _direct_prompt(kind: str, task: dict) -> str:
    _schema, _schema_name, instructions = _direct_spec(kind)
    return (
        f"{instructions}\n\nTASK:\n"
        + json.dumps(task, ensure_ascii=False, sort_keys=True)
    )


def _validate_direct_result(kind: str, task: dict, result: dict) -> list[str]:
    if not isinstance(result, dict):
        return ["provider result is not a JSON object"]
    if kind == "entailment":
        payload = task.get("payload") or {}
        reviewer = str(result.get("reviewer") or "")
        verdict = {
            **result,
            "claim_id": str(payload.get("claim_id") or ""),
            "evidence_id": str(payload.get("evidence_id") or ""),
            "verdict_id": verdict_id(
                str(payload.get("claim_id") or ""),
                str(payload.get("evidence_id") or ""),
                reviewer,
            ),
        }
        return validate_verdict(verdict)

    expected = str(task.get("claim_id") or "")
    errors = []
    if result.get("claim_id") != expected:
        errors.append(
            f"claim_id={result.get('claim_id')!r}; expected {expected!r}"
        )
    anchors = {
        str(anchor.get("evidence_id") or "")
        for key in ("supporting_anchors", "contradicting_anchors")
        for anchor in (task.get(key) or [])
        if anchor.get("evidence_id")
    }
    for facet in _NARRATIVE_FACETS:
        statement = result.get(facet)
        if statement is None:
            if facet == "observed_result":
                errors.append("observed_result is required")
            continue
        if (
            not isinstance(statement, dict)
            or not str(statement.get("text") or "").strip()
        ):
            errors.append(f"{facet} must be a non-empty statement object or null")
            continue
        raw_ids = statement.get("evidence_ids")
        if not isinstance(raw_ids, list):
            errors.append(f"{facet}.evidence_ids must be a JSON array")
            continue
        if not isinstance(statement.get("inference"), bool):
            errors.append(f"{facet}.inference must be a JSON boolean")
            continue
        cited = {str(value) for value in raw_ids}
        unknown = sorted(cited - anchors)
        if unknown:
            errors.append(f"{facet} cites unknown evidence_ids: {', '.join(unknown)}")
        if not cited and statement.get("inference") is not True:
            errors.append(f"{facet} has no evidence_ids and is not an inference")
    return errors


def _direct_cache_path(
    root: pathlib.Path,
    kind: str,
    backend: str,
    model: str,
    prompt: str,
    schema: dict,
) -> pathlib.Path:
    digest = hashlib.sha256(
        (
            kind
            + "\0"
            + backend
            + "\0"
            + model
            + "\0"
            + json.dumps(schema, sort_keys=True)
            + "\0"
            + prompt
        ).encode()
    ).hexdigest()
    return root / "cache" / f"direct_{kind}" / f"{digest}.json"


def _run_direct_one(
    root: pathlib.Path,
    kind: str,
    task_path: pathlib.Path,
    backend: str,
    model: str,
    request_fn: RequestJson,
) -> tuple[pathlib.Path, bool, str]:
    try:
        task = json.loads(task_path.read_text(encoding="utf-8"))
        schema, schema_name, _instructions = _direct_spec(kind)
        prompt = _direct_prompt(kind, task)
        cache_path = _direct_cache_path(
            root, kind, backend, model, prompt, schema
        )
        cache_hit = False
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached = {}
            result = cached.get("result") if isinstance(cached, dict) else None
            errors = _validate_direct_result(kind, task, result)
            cache_hit = not errors
        if not cache_hit:
            result, meta = request_fn(
                backend,
                model,
                prompt,
                schema=schema,
                schema_name=schema_name,
            )
            errors = _validate_direct_result(kind, task, result)
            if errors:
                raise AdjudicationError("; ".join(errors))
            atomic_json(cache_path, {"result": result, "meta": meta})
        output = _worker_output(root, task, exchange_root=None, kind=kind)
        atomic_json(output, result)
        return output, cache_hit, ""
    except (
        AdjudicationError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        return task_path, False, f"{type(exc).__name__}: {exc}"


def run_direct_tasks(
    root: pathlib.Path,
    kind: str,
    *,
    backend: str,
    model: str,
    jobs: int = DEFAULT_DIRECT_JOBS,
    request_fn: RequestJson = request_json,
) -> tuple[pathlib.Path, int]:
    """Run provider-backed entailment or narrative tasks in a bounded pool."""
    if kind not in DIRECT_KINDS:
        raise ValueError(
            f"--kind must be one of {', '.join(sorted(DIRECT_KINDS))}"
        )
    if jobs < 1 or jobs > MAX_DIRECT_JOBS:
        raise ValueError(
            f"jobs must be between 1 and {MAX_DIRECT_JOBS}, got {jobs}"
        )
    tasks = sorted((root / WORKER_KINDS[kind]).glob("*.json"))
    if not tasks:
        raise ValueError(f"no {kind} task files under {root / WORKER_KINDS[kind]}")
    worker_count = min(jobs, len(tasks))
    if worker_count == 1:
        results = [
            _run_direct_one(root, kind, task, backend, model, request_fn)
            for task in tasks
        ]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [
                pool.submit(
                    _run_direct_one, root, kind, task, backend, model, request_fn
                )
                for task in tasks
            ]
            results = [future.result() for future in futures]
    failures = [f"{path}: {error}" for path, _hit, error in results if error]
    if failures:
        raise ValueError(
            f"{len(failures)} of {len(tasks)} {kind} task(s) failed: "
            + "; ".join(failures[:8])
        )
    destination = results[0][0].parent
    cache_hits = sum(1 for _path, hit, _error in results if hit)
    print(
        f"DIRECT-TASKS: kind={kind} jobs={worker_count} "
        f"cache_hits={cache_hits}/{len(tasks)}"
    )
    return destination, len(tasks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        "stage-workers",
        "pack-native",
        "assemble-adjudications",
        "emit-entailment",
        "assemble-entailment",
        "assemble-narratives",
    ))
    parser.add_argument("--root", required=True)
    parser.add_argument("--exchange-root")
    parser.add_argument("--kind", choices=sorted(WORKER_KINDS))
    parser.add_argument("--max-tasks-per-pack", type=int)
    parser.add_argument(
        "--max-pack-chars", type=int, default=DEFAULT_NATIVE_PACK_CHARS
    )
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    exchange_root = (
        pathlib.Path(args.exchange_root).resolve() if args.exchange_root else None
    )
    try:
        if args.action == "stage-workers":
            if exchange_root is None or args.kind is None:
                raise ValueError("stage-workers requires --exchange-root and --kind")
            path, count = stage_workers(root, exchange_root, args.kind)
        elif args.action == "pack-native":
            if exchange_root is None or args.kind is None:
                raise ValueError("pack-native requires --exchange-root and --kind")
            path, count = pack_native(
                exchange_root,
                args.kind,
                max_tasks=args.max_tasks_per_pack,
                max_chars=args.max_pack_chars,
            )
        elif args.action == "assemble-adjudications":
            path, count = assemble_adjudications(root, exchange_root)
        elif args.action == "emit-entailment":
            count = emit_entailment_tasks(root)
            path = root / "evidence" / "entailment_tasks"
        elif args.action == "assemble-entailment":
            path, count = assemble_entailment(root, exchange_root)
        else:
            path, count = assemble_narratives(root, exchange_root)
    except (AdjudicationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"BATCH-TASKS: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"BATCH-TASKS: action={args.action} records={count} path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
