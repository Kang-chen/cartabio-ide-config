"""Active runtime remains additive across resumable invocations."""
from __future__ import annotations


def test_record_invocation_accumulates_instead_of_overwriting(monkeypatch):
    import runtime_metrics

    clock = iter((105.0, 212.5))
    monkeypatch.setattr(runtime_metrics.time, "time", lambda: next(clock))
    metrics = {}

    runtime_metrics.record_invocation(metrics, 100.0, "acquisition")
    runtime_metrics.record_invocation(metrics, 200.0, "resume")

    assert [row["elapsed_seconds"] for row in metrics["invocations"]] == [5.0, 12.5]
    assert metrics["active_invocation_seconds"] == 17.5
    assert metrics["wall_clock_seconds"] == 17.5
