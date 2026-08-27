#!/usr/bin/env python3
"""Launch one managed node in a cooperative TusoAI multi-machine cluster."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import pickle
import re
import sys
import time
import traceback
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_factory(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("tuso_task_factory", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load task factory: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _safe_history_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()).strip("_")
    if not safe:
        raise ValueError("history_name must be non-empty in multi-machine mode")
    return safe


def _history_path(output_dir: Path, history_name: str) -> Path:
    return output_dir / "history" / f"history_{_safe_history_name(history_name)}" / "history.json"


def _history_has_candidate(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        repo_root = os.environ.get("TUSOAI_REPO_ROOT")
        if repo_root:
            sys.path.insert(0, str(Path(repo_root).expanduser().resolve()))
        from tusoai.optimization import _dm_read_history_entries

        return any(
            isinstance(entry, dict)
            and isinstance(entry.get("code"), str)
            and entry.get("accuracy") is not None
            for entry in _dm_read_history_entries(path)
        )
    except Exception:
        return False


def _parse_gpu_ids(raw: str) -> list[int] | None:
    raw = raw.strip()
    if not raw or raw.lower() in {"none", "null"}:
        return None
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factory", required=True)
    parser.add_argument("--task-bundle", required=True)
    parser.add_argument("--task-bundle-sha256", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--role", choices=("leader", "follower"), required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--history-name", required=True)
    parser.add_argument("--deadline-epoch", type=float, required=True)
    parser.add_argument("--shutdown-reserve-seconds", type=int, default=2700)
    parser.add_argument("--node-cost-limit", type=float, required=True)
    parser.add_argument("--n-jobs", type=int, required=True)
    parser.add_argument("--cpu-threads-per-job", type=int, required=True)
    parser.add_argument("--gpu-ids", default="")
    parser.add_argument("--memory-limit-gb", type=int)
    parser.add_argument("--bootstrap-timeout-seconds", type=int, default=3600)
    parser.add_argument("--status-dir", required=True)
    args = parser.parse_args()

    started = time.time()
    output_dir = Path(args.output_dir).expanduser().resolve()
    status_path = Path(args.status_dir).expanduser().resolve() / f"node_{args.node_id}.json"
    history_path = _history_path(output_dir, args.history_name)
    base_status: dict[str, Any] = {
        "schema_version": 1,
        "node_id": args.node_id,
        "role": args.role,
        "pid": os.getpid(),
        "started_at": started,
        "history": str(history_path),
        "state": "starting",
    }
    _atomic_json(status_path, base_status)

    try:
        remaining_s = args.deadline_epoch - time.time() - args.shutdown_reserve_seconds
        if remaining_s < 60:
            raise RuntimeError("cluster deadline leaves less than one runnable minute after the shutdown reserve")
        time_limit_minutes = max(1, int(math.floor(remaining_s / 60.0)))

        bundle_path = Path(args.task_bundle).expanduser().resolve()
        payload = bundle_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != args.task_bundle_sha256:
            raise RuntimeError(f"task bundle digest mismatch: expected {args.task_bundle_sha256}, got {digest}")
        bundle: dict[str, Any] = pickle.loads(payload)
        factory = _load_factory(Path(args.factory).expanduser().resolve())
        ai = factory.build_ai()

        if args.role == "follower":
            wait_started = time.time()
            while not _history_has_candidate(history_path):
                if time.time() - wait_started > args.bootstrap_timeout_seconds:
                    raise TimeoutError("leader did not publish a valid seed candidate before bootstrap timeout")
                if time.time() >= args.deadline_epoch - args.shutdown_reserve_seconds:
                    raise TimeoutError("cluster deadline reached while waiting for leader seed")
                print(
                    f"[tuso-node {args.node_id}] waiting for leader seed at {history_path}",
                    flush=True,
                )
                time.sleep(30)

        load_history = str(history_path) if _history_has_candidate(history_path) else None
        optimize_kwargs = dict(bundle.get("optimize_kwargs") or {})
        if args.memory_limit_gb is not None:
            optimize_kwargs["memory_limit_gb"] = args.memory_limit_gb
        optimize_kwargs.update(
            {
                "method_tasks": list(bundle["method_tasks"]),
                "data_tasks": list(bundle["data_tasks"]),
                "reference_filename": str(bundle["reference_filename"]),
                "output_dir": str(output_dir),
                "history_name": args.history_name,
                "task_description": str(bundle["task_description"]),
                "global_hints": list(bundle.get("global_hints") or []),
                "TIME_LIMIT": time_limit_minutes,
                "COST_LIMIT": float(args.node_cost_limit),
                "n_jobs": max(1, int(args.n_jobs)),
                "cpu_threads_per_job": max(1, int(args.cpu_threads_per_job)),
                "gpu_ids": _parse_gpu_ids(args.gpu_ids),
                "multi_machine": True,
                "load_history": load_history,
            }
        )
        base_status.update(
            {
                "state": "running",
                "updated_at": time.time(),
                "time_limit_minutes": time_limit_minutes,
                "node_cost_limit": args.node_cost_limit,
                "n_jobs": args.n_jobs,
                "cpu_threads_per_job": args.cpu_threads_per_job,
                "gpu_ids": _parse_gpu_ids(args.gpu_ids),
                "load_history": load_history,
            }
        )
        _atomic_json(status_path, base_status)
        best_model, _history = ai.optimize(**optimize_kwargs)
        best_code_path = status_path.with_name(f"node_{args.node_id}_best.py")
        best_code_path.write_text(str(best_model.code).rstrip() + "\n", encoding="utf-8")
        base_status.update(
            {
                "state": "completed",
                "completed_at": time.time(),
                "best_accuracy": float(best_model.accuracy),
                "best_runtime": float(best_model.runtime),
                "best_code": str(best_code_path),
            }
        )
        _atomic_json(status_path, base_status)
        print(json.dumps(base_status, indent=2, sort_keys=True), flush=True)
        return 0
    except BaseException as exc:
        base_status.update(
            {
                "state": "failed",
                "failed_at": time.time(),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        _atomic_json(status_path, base_status)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
