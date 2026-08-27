#!/usr/bin/env python3
"""Audit a TusoAI evaluator and local resource envelope before a long run."""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCORE_RE = re.compile(
    r"^\s*tuso_evaluate:\s*([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)\s*$",
    re.MULTILINE,
)


def _total_memory_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / (1024 * 1024)
    except Exception:
        return None
    return None


def _gpu_inventory() -> list[dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    command = [
        exe,
        "--query-gpu=index,name,memory.total,uuid",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    except Exception:
        return []
    gpus: list[dict[str, Any]] = []
    for row in result.stdout.splitlines():
        parts = [part.strip() for part in row.split(",", 3)]
        if len(parts) == 4:
            gpus.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "memory_mib": int(parts[2]),
                    "uuid": parts[3],
                }
            )
    return gpus


def _parse_max_rss_kb(time_output: str) -> int | None:
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", time_output)
    return int(match.group(1)) if match else None


def _run_once(python: str, evaluator: Path, timeout: int, env: dict[str, str]) -> dict[str, Any]:
    time_exe = Path("/usr/bin/time")
    with tempfile.TemporaryDirectory(prefix="tuso_audit_") as tmpdir:
        time_log = Path(tmpdir) / "time.txt"
        if time_exe.exists():
            command = [str(time_exe), "-v", "-o", str(time_log), python, str(evaluator)]
        else:
            command = [python, str(evaluator)]
        started = time.perf_counter()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "timed_out": True,
                "runtime_s": time.perf_counter() - started,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "score": None,
                "score_line_count": 0,
                "max_rss_kb": None,
            }
        runtime_s = time.perf_counter() - started
        stdout = result.stdout or ""
        matches = SCORE_RE.findall(stdout)
        score: float | None = None
        if len(matches) == 1:
            candidate = float(matches[0])
            if math.isfinite(candidate):
                score = candidate
        time_text = time_log.read_text(encoding="utf-8") if time_log.exists() else ""
        return {
            "ok": result.returncode == 0 and score is not None and len(matches) == 1,
            "timed_out": timed_out,
            "returncode": result.returncode,
            "runtime_s": runtime_s,
            "stdout": stdout[-4000:],
            "stderr": (result.stderr or "")[-4000:],
            "score": score,
            "score_line_count": len(matches),
            "max_rss_kb": _parse_max_rss_kb(time_text),
        }


def _hardcoded_path_findings(evaluator: Path, repo_root: Path | None) -> list[str]:
    findings: list[str] = []
    text = evaluator.read_text(encoding="utf-8")
    if repo_root is not None and str(repo_root.resolve()) in text:
        findings.append("evaluator contains the absolute repository root")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"evaluator could not be parsed as Python: {exc}"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        is_sys_path_edit = (
            isinstance(fn, ast.Attribute)
            and fn.attr in {"insert", "append", "extend"}
            and isinstance(fn.value, ast.Attribute)
            and isinstance(fn.value.value, ast.Name)
            and fn.value.value.id == "sys"
            and fn.value.attr == "path"
        )
        if is_sys_path_edit:
            findings.append(f"evaluator mutates sys.path at line {getattr(node, 'lineno', '?')}")
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluator", required=True)
    parser.add_argument("--repo-root")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--threads-per-job", type=int, default=1)
    parser.add_argument("--memory-headroom", type=float, default=1.35)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    evaluator = Path(args.evaluator).expanduser().resolve()
    if not evaluator.exists():
        raise FileNotFoundError(evaluator)
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else None
    env = os.environ.copy()
    env.setdefault("PYTHONHASHSEED", "0")
    runs = [_run_once(args.python, evaluator, args.timeout, env) for _ in range(max(1, args.repeats))]
    valid_runs = [run for run in runs if run["ok"]]
    scores = [float(run["score"]) for run in valid_runs]
    runtimes = [float(run["runtime_s"]) for run in valid_runs]
    rss_values = [int(run["max_rss_kb"]) for run in valid_runs if run.get("max_rss_kb") is not None]

    affinity_cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    total_memory_gb = _total_memory_gb()
    peak_rss_gb = max(rss_values) / (1024 * 1024) if rss_values else None
    jobs_by_cpu = max(1, affinity_cores // max(1, args.threads_per_job))
    jobs_by_memory = None
    if total_memory_gb and peak_rss_gb and peak_rss_gb > 0:
        usable = total_memory_gb * 0.75
        jobs_by_memory = max(1, int(usable // (peak_rss_gb * max(1.0, args.memory_headroom))))
    recommended_upper_bound = min(jobs_by_cpu, jobs_by_memory) if jobs_by_memory else jobs_by_cpu

    report = {
        "schema_version": 1,
        "created_at": time.time(),
        "evaluator": str(evaluator),
        "repo_root": str(repo_root) if repo_root else None,
        "valid": len(valid_runs) == len(runs) and bool(valid_runs),
        "runs": runs,
        "score": {
            "mean": statistics.mean(scores) if scores else None,
            "stdev": statistics.stdev(scores) if len(scores) > 1 else 0.0 if scores else None,
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
        },
        "runtime_s": {
            "mean": statistics.mean(runtimes) if runtimes else None,
            "stdev": statistics.stdev(runtimes) if len(runtimes) > 1 else 0.0 if runtimes else None,
        },
        "peak_rss_gb": peak_rss_gb,
        "hardcoded_path_findings": _hardcoded_path_findings(evaluator, repo_root),
        "resources": {
            "logical_cpus": os.cpu_count(),
            "affinity_cpus": affinity_cores,
            "total_memory_gb": total_memory_gb,
            "gpus": _gpu_inventory(),
            "jobs_by_cpu": jobs_by_cpu,
            "jobs_by_memory": jobs_by_memory,
            "recommended_n_jobs_upper_bound": recommended_upper_bound,
            "note": "Also cap by API concurrency and, for GPU evaluators, measured GPU-memory capacity.",
        },
    }
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
