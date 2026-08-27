from __future__ import annotations

from pathlib import Path

from tusoai.optimization import run_and_evaluate


def test_run_and_evaluate_accepts_one_scientific_notation_score(tmp_path: Path) -> None:
    runner = tmp_path / "runner.py"
    runner.write_text("print('diagnostic')\nprint('tuso_evaluate: 1.25e-1')\n", encoding="utf-8")

    result = run_and_evaluate(runner, timeout=10, val_limit=2)

    assert isinstance(result, dict)
    assert result["evaluation"] == 0.125
    assert result["model_info"] == ""
    assert result["runtime"] >= 0


def test_run_and_evaluate_rejects_duplicate_score_lines(tmp_path: Path) -> None:
    runner = tmp_path / "runner.py"
    runner.write_text(
        "print('tuso_evaluate: 0.1')\nprint('tuso_evaluate: 0.2')\n",
        encoding="utf-8",
    )

    result = run_and_evaluate(runner, timeout=10, val_limit=2)

    assert isinstance(result, str)
    assert "exactly one standalone line" in result
    assert "found 2" in result
