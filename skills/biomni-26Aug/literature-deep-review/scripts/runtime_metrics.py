"""Accumulate active invocation time across resumable pipeline calls."""
from __future__ import annotations

import datetime
import time


def record_invocation(metrics: dict, started_epoch: float, stage: str) -> float:
    elapsed = round(max(0.0, time.time() - started_epoch), 3)
    invocations = metrics.setdefault("invocations", [])
    invocations.append({
        "stage": stage,
        "elapsed_seconds": elapsed,
        "finished_at": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
    })
    total = round(sum(float(row["elapsed_seconds"]) for row in invocations), 3)
    metrics["active_invocation_seconds"] = total
    # Backward-compatible name, now accumulated rather than overwritten by the
    # last short resume call.
    metrics["wall_clock_seconds"] = total
    return elapsed
