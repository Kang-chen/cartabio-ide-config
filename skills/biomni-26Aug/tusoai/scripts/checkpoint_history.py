#!/usr/bin/env python3
"""Mirror a shared TusoAI history and best-so-far code into user-visible results."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "repo"))

from tusoai.fs_utils import copyfile_portable, replace_file_portable  # noqa: E402
from tusoai.optimization import (  # noqa: E402
    ModelRecord,
    _dm_history_close_set,
    _dm_history_complexity_score,
    _dm_read_history_entries,
)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    replace_file_portable(tmp, path)


def _valid_candidates(entries: list[dict[str, Any]]) -> list[tuple[dict[str, Any], ModelRecord]]:
    candidates: list[tuple[dict[str, Any], ModelRecord]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("code"), str):
            continue
        try:
            accuracy = float(entry.get("accuracy"))
            runtime = float(entry.get("runtime")) if entry.get("runtime") is not None else math.inf
        except (TypeError, ValueError):
            continue
        if not math.isfinite(accuracy):
            continue
        candidates.append(
            (
                entry,
                ModelRecord(
                    code=entry["code"],
                    file=None,
                    accuracy=accuracy,
                    runtime=runtime,
                    lineage=str(entry.get("lineage", "")),
                ),
            )
        )
    return candidates


def _cluster_cost(entries: list[dict[str, Any]]) -> tuple[float, dict[str, float]]:
    by_run: dict[str, float] = defaultdict(float)
    legacy_max = 0.0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            cost = float(entry.get("total_cost", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        run_id = entry.get("run_id")
        if run_id:
            by_run[str(run_id)] = max(by_run[str(run_id)], cost)
        else:
            legacy_max = max(legacy_max, cost)
    if legacy_max:
        by_run["legacy_or_single_machine"] = legacy_max
    return sum(by_run.values()), dict(sorted(by_run.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--min-improvement", type=float, default=0.01)
    parser.add_argument("--label", default="tusoai")
    args = parser.parse_args()

    history = Path(args.history).expanduser().resolve()
    results = Path(args.results_dir).expanduser().resolve()
    results.mkdir(parents=True, exist_ok=True)
    entries = _dm_read_history_entries(history)
    candidates = _valid_candidates(entries)
    cluster_cost, cost_by_run = _cluster_cost(entries)

    status: dict[str, Any] = {
        "schema_version": 1,
        "updated_at": time.time(),
        "history": str(history),
        "history_entries": len(entries),
        "candidate_entries": len(candidates),
        "estimated_cluster_optimization_cost": cluster_cost,
        "cost_by_run": cost_by_run,
        "best_accuracy": None,
        "selected_accuracy": None,
        "selected_runtime": None,
        "selected_code_lines": None,
        "latest_stage": entries[-1].get("stage") if entries and isinstance(entries[-1], dict) else None,
    }

    if candidates:
        records = [record for _, record in candidates]
        best = max(records, key=lambda record: float(record.accuracy))
        close = _dm_history_close_set(records, min_improvement=max(0.0, args.min_improvement))
        anchor = max(close, key=lambda record: float(record.accuracy))
        selected = max(close, key=lambda record: _dm_history_complexity_score(record, anchor))
        _atomic_text(results / f"{args.label}_best_score.py", best.code.rstrip() + "\n")
        _atomic_text(results / f"{args.label}_selected.py", selected.code.rstrip() + "\n")
        status.update(
            {
                "best_accuracy": float(best.accuracy),
                "best_runtime": float(best.runtime),
                "selected_accuracy": float(selected.accuracy),
                "selected_runtime": float(selected.runtime),
                "selected_code_lines": sum(1 for line in selected.code.splitlines() if line.strip()),
            }
        )

    history_copy = results / f"{args.label}_history.json"
    tmp_copy = results / f".{history_copy.name}.{uuid.uuid4().hex}.tmp"
    copyfile_portable(history, tmp_copy)
    replace_file_portable(tmp_copy, history_copy)
    _atomic_text(results / f"{args.label}_status.json", json.dumps(status, indent=2, sort_keys=True))
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
