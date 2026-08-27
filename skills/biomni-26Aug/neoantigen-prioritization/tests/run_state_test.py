"""Smoke tests for the durable long-horizon run-state helper."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_state.py"


def _run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"expected exit {expected}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        run_dir = root / "shared" / "case-a-run"
        results_dir = root / "results" / "case-a-run"
        vcf = root / "case.vcf"
        config = root / "config.json"
        vcf.write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
        config.write_text(json.dumps({"include_indels": True}), encoding="utf-8")

        _run(
            "init",
            "--run-dir", str(run_dir),
            "--results-dir", str(results_dir),
            "--case-id", "case-a",
            "--input", f"vcf={vcf}",
            "--config", str(config),
        )
        assert (run_dir / "state.json").is_file()
        assert (run_dir / "plan.md").is_file()
        assert (run_dir / "journal.jsonl").is_file()
        assert (results_dir / "status.json").is_file()

        # The state machine must reject phase skipping.
        _run("begin", "--run-dir", str(run_dir), "preflight", expected=2)

        _run("begin", "--run-dir", str(run_dir), "intake")
        _run("complete", "--run-dir", str(run_dir), "intake", expected=2)
        _run(
            "complete", "--run-dir", str(run_dir), "intake",
            "--artifact", f"plan={run_dir / 'plan.md'}",
        )

        # A failed phase can be diagnosed, checkpointed, and retried without losing history.
        _run("begin", "--run-dir", str(run_dir), "preflight")
        _run(
            "fail", "--run-dir", str(run_dir), "preflight",
            "--error", "temporary model check failure",
        )
        _run(
            "begin", "--run-dir", str(run_dir), "preflight",
            "--next-action", "rerun the model check",
        )
        _run(
            "note", "--run-dir", str(run_dir),
            "--message", "model check recovered",
            "--next-action", "write preflight evidence",
        )
        preflight = run_dir / "preflight.json"
        preflight.write_text(json.dumps({"ok": True}), encoding="utf-8")
        _run(
            "complete", "--run-dir", str(run_dir), "preflight",
            "--artifact", f"preflight={preflight}",
        )

        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        assert state["phases"]["preflight"]["attempts"] == 2
        assert state["current_phase"] == "prioritization"
        assert state["artifacts"]["preflight"]["sha256"]
        _run("verify", "--run-dir", str(run_dir))

        # Fingerprint drift must be visible and non-zero, never silently accepted.
        vcf.write_text("##fileformat=VCFv4.3\n", encoding="utf-8")
        drift = _run("verify", "--run-dir", str(run_dir), expected=2)
        assert '"ok": false' in drift.stdout
        assert "sha256 changed" in drift.stdout

        # Restoring the content restores trust for a full SHA-256 fingerprint. Complete the
        # remaining state machine and verify that only the final handoff marks the run complete.
        vcf.write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
        _run("verify", "--run-dir", str(run_dir))
        config.write_text(json.dumps({"include_indels": False}), encoding="utf-8")
        config_drift = _run("verify", "--run-dir", str(run_dir), expected=2)
        assert '"kind": "config"' in config_drift.stdout
        config.write_text(json.dumps({"include_indels": True}), encoding="utf-8")
        _run("verify", "--run-dir", str(run_dir))
        for phase in ("prioritization", "validation", "visualization", "reporting", "handoff"):
            _run("begin", "--run-dir", str(run_dir), phase)
            artifact = run_dir / f"{phase}.json"
            artifact.write_text(json.dumps({"phase": phase, "ok": True}), encoding="utf-8")
            _run(
                "complete", "--run-dir", str(run_dir), phase,
                "--artifact", f"{phase}={artifact}",
            )

        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        published = json.loads((results_dir / "status.json").read_text(encoding="utf-8"))
        assert state["status"] == "complete" and state["current_phase"] is None
        assert published["status"] == "complete" and published["last_error"] is None
        assert all(phase["status"] == "completed" for phase in state["phases"].values())
        _run("verify", "--run-dir", str(run_dir))

    print("PASS: durable state transitions, retry, publication, and drift detection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
