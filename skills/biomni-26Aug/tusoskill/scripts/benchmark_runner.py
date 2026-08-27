#!/usr/bin/env python3
"""Run a benchmark command repeatedly and collect simple metrics.

The command may print JSON to stdout, or lines beginning with `METRIC_JSON:` followed by a JSON object.
This runner records stdout/stderr tails, duration, return code, and any parsed metrics.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict


def parse_metrics(stdout: str) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    for line in stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("METRIC_JSON:"):
            s = s.split(":", 1)[1].strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                metrics.update(obj)
    return metrics


def run_once(command: str, cwd: str | None, timeout: float | None) -> Dict[str, Any]:
    start = time.time()
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    proc = subprocess.run(command, shell=True, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    return {
        "command": command,
        "cwd": cwd,
        "returncode": proc.returncode,
        "duration_sec": time.time() - start,
        "maxrss_kb_delta": max(0, after - before),
        "metrics": parse_metrics(stdout),
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--command", required=True, help="Shell command to run")
    p.add_argument("--out", required=True, help="Output JSON summary path")
    p.add_argument("--jsonl", default="", help="Optional append-only JSONL path")
    p.add_argument("--cwd", default=None)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--timeout", type=float, default=None)
    args = p.parse_args()

    results = []
    for i in range(args.repeat):
        try:
            rec = run_once(args.command, args.cwd, args.timeout)
        except subprocess.TimeoutExpired as e:
            rec = {"command": args.command, "cwd": args.cwd, "returncode": "timeout", "duration_sec": args.timeout, "metrics": {}, "stdout_tail": (e.stdout or "")[-4000:] if isinstance(e.stdout, str) else "", "stderr_tail": (e.stderr or "")[-4000:] if isinstance(e.stderr, str) else ""}
        rec["repeat_index"] = i
        rec["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        results.append(rec)
        if args.jsonl:
            Path(args.jsonl).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.jsonl).open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
    summary = {"command": args.command, "repeat": args.repeat, "results": results}
    metric_values: Dict[str, list] = {}
    for rec in results:
        for k, v in rec.get("metrics", {}).items():
            if isinstance(v, (int, float)):
                metric_values.setdefault(k, []).append(float(v))
    summary["metric_summary"] = {k: {"mean": sum(v) / len(v), "min": min(v), "max": max(v), "n": len(v)} for k, v in metric_values.items() if v}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "returncodes": [r.get("returncode") for r in results], "metric_summary": summary["metric_summary"]}))


if __name__ == "__main__":
    main()
