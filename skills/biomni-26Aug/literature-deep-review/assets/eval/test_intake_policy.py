"""Figure count must never silently become permission to disable OCR."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import evidence_first
from evidence_first import (
    MODE_DEFAULTS,
    _apply_paper_limit,
    _resolved_run_config,
    _targeted_ocr,
)
from intake_policy import (
    adaptive_figure_minimum,
    figure_intake_errors,
    main as intake_main,
    recommended_ocr_mode,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _manifest(figures, ocr=None, source=None, reuse="reuse_cleared_only",
              reuse_source="explicit_user", count_policy="fixed"):
    return {
        "mode": "deep",
        "config": {
            "figure_count_policy": count_policy,
            "minimum_paper_figures": figures,
            "ocr": ocr,
            "ocr_decision_source": source,
            "figure_reuse_policy": reuse,
            "figure_reuse_decision_source": reuse_source,
        },
    }


def test_requested_figures_require_a_recorded_ocr_decision():
    errors = figure_intake_errors(_manifest(3))
    assert any("config.ocr" in error for error in errors)
    assert any("ocr_decision_source" in error for error in errors)


def test_caption_only_is_valid_after_an_explicit_user_choice():
    assert figure_intake_errors(_manifest(3, "off", "explicit_user")) == []


def test_caption_only_cannot_be_silently_used_as_a_default():
    errors = figure_intake_errors(_manifest(3, "off", "delegated_default"))
    assert any("only after an explicit user choice" in error for error in errors)


def test_delegated_positive_figure_default_is_targeted_ocr():
    assert recommended_ocr_mode(3) == "targeted"
    assert figure_intake_errors(
        _manifest(3, "targeted", "delegated_default")
    ) == []


def test_unresolved_adaptive_policy_starts_with_targeted_ocr():
    assert figure_intake_errors(
        _manifest(
            None,
            "targeted",
            "delegated_default",
            count_policy="adaptive",
        )
    ) == []


def test_adaptive_floor_scales_with_corpus_and_has_no_fixed_four_figure_cap():
    assert adaptive_figure_minimum("broad", 43, 6, 30) == 9
    assert adaptive_figure_minimum("broad", 100, 7, 30) == 20
    assert adaptive_figure_minimum("broad", 43, 12, 30) == 12


def test_adaptive_floor_cannot_exceed_materially_eligible_supply():
    assert adaptive_figure_minimum("broad", 43, 8, 5) == 5


def test_resolver_writes_the_adaptive_floor_to_the_manifest(tmp_path):
    manifest = _manifest(
        None,
        "targeted",
        "delegated_default",
        count_policy="adaptive",
    )
    manifest["mode"] = "broad"
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert intake_main([
        "--manifest", str(path),
        "--resolve-adaptive",
        "--full-text-papers", "43",
        "--populated-axes", "6",
        "--eligible-figures", "30",
    ]) == 0

    resolved = json.loads(path.read_text(encoding="utf-8"))
    assert resolved["config"]["minimum_paper_figures"] == 9
    assert resolved["config"]["adaptive_figure_resolution"] == {
        "eligible_figures": 30,
        "full_text_papers": 43,
        "limited_by_eligible_supply": False,
        "mode_baseline": 6,
        "one_per_five_full_texts": 9,
        "populated_axes": 6,
        "resolved_minimum": 9,
        "unlimited_desired_minimum": 9,
    }


def test_adaptive_resolution_does_not_rewrite_the_intake_snapshot(tmp_path):
    manifest = _manifest(
        None,
        "targeted",
        "delegated_default",
        count_policy="adaptive",
    )
    manifest.update({"mode": "broad", "question": "Is GRN causal?"})
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert intake_main(["--manifest", str(path)]) == 0
    snapshot = tmp_path / "state" / "intake_snapshot.json"
    before = snapshot.read_bytes()

    assert (
        intake_main(
            [
                "--manifest",
                str(path),
                "--resolve-adaptive",
                "--full-text-papers",
                "30",
                "--populated-axes",
                "9",
                "--eligible-figures",
                "12",
            ]
        )
        == 0
    )

    assert snapshot.read_bytes() == before


def test_intake_snapshot_rejects_a_changed_review_question(tmp_path):
    manifest = _manifest(4, "targeted", "explicit_user")
    manifest.update({"mode": "deep", "question": "Is GRN causal?"})
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert intake_main(["--manifest", str(path)]) == 0

    manifest["question"] = "Is MAPT causal?"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SystemExit):
        intake_main(["--manifest", str(path)])


def test_all_figure_ocr_requires_an_explicit_user_choice():
    errors = figure_intake_errors(_manifest(3, "all", "delegated_default"))
    assert any("all-figure OCR requires" in error for error in errors)


def test_no_figures_can_disable_ocr_without_a_separate_question():
    assert recommended_ocr_mode(0) == "off"
    assert figure_intake_errors(
        _manifest(0, "off", "no_figures", reuse_source="no_figures")
    ) == []


def test_user_can_explicitly_authorize_figures_without_cleared_reuse_rights():
    assert figure_intake_errors(
        _manifest(3, "targeted", "explicit_user", "user_directed",
                  "explicit_user")
    ) == []


def test_user_directed_reuse_cannot_be_silently_inferred():
    errors = figure_intake_errors(
        _manifest(3, "targeted", "delegated_default", "user_directed",
                  "delegated_default")
    )
    assert any("requires an explicit user choice" in error for error in errors)


def _args(ocr=None, max_papers=None):
    return SimpleNamespace(
        ocr=ocr,
        max_papers=max_papers,
        backend="none",
        model=None,
        marker_fallback=False,
        adjudication_jobs=4,
    )


def test_runner_preserves_the_recorded_intake_fields():
    manifest = _manifest(3, "targeted", "explicit_user")
    config = _resolved_run_config(dict(MODE_DEFAULTS["deep"]), _args(), manifest)
    assert config["minimum_paper_figures"] == 3
    assert config["ocr_decision_source"] == "explicit_user"
    assert config["ocr"] == "targeted"


def test_runner_rejects_an_ocr_override_that_conflicts_with_intake():
    manifest = _manifest(3, "targeted", "explicit_user")
    with pytest.raises(ValueError, match="conflicts with the recorded"):
        _resolved_run_config(
            dict(MODE_DEFAULTS["deep"]), _args(ocr="off"), manifest
        )


def test_broad_mode_keeps_every_selected_paper_by_default():
    records = [{"paper_id": f"paper-{index}"} for index in range(64)]
    assert MODE_DEFAULTS["broad"]["max_papers"] is None
    assert _apply_paper_limit(records, MODE_DEFAULTS["broad"]["max_papers"]) == records


def test_user_can_set_any_positive_paper_cap():
    records = [{"paper_id": f"paper-{index}"} for index in range(80)]
    assert len(_apply_paper_limit(records, 60)) == 60


def test_clarification_asks_for_a_paper_count_ballpark_before_search():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    questions = skill.split("## Clarification Questions", 1)[1].split(
        "## Standard Workflow", 1
    )[0]

    assert "paper-count ballpark" in questions
    assert "all relevant papers" in questions
    assert "approximately N" in questions
    assert "decide after seeing" in questions
    assert "planning preference, not an exact ceiling" in questions


def test_clarification_offers_comprehensive_and_adaptive_figure_counts():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    questions = skill.split("## Clarification Questions", 1)[1].split(
        "## Standard Workflow", 1
    )[0].lower()

    assert "standard (usually 4-8; floor 4)" in questions
    assert "comprehensive (usually 8-15+; floor 8)" in questions
    assert "adaptive to the retrieved corpus" in questions
    assert "one per five retrieved full texts" in questions
    assert "never substitute a fixed four-figure default" in questions


def test_intake_reference_defers_the_exact_cap_until_after_search():
    intake = (SKILL_ROOT / "references" / "modes_and_intake.md").read_text(
        encoding="utf-8"
    )

    assert "What ballpark number of full texts" in intake
    assert "give a number or range" in intake
    assert "Only write `config.max_papers`" in intake
    assert "after an exact\nceiling is confirmed" in intake


def test_runner_preserves_a_manifest_paper_cap_when_cli_omits_it():
    manifest = _manifest(3, "targeted", "explicit_user")
    manifest["config"]["max_papers"] = 60
    config = _resolved_run_config(dict(MODE_DEFAULTS["broad"]), _args(), manifest)
    assert config["max_papers"] == 60


@pytest.mark.parametrize("invalid", [0, -1, 2.5, True, "60"])
def test_runner_rejects_invalid_manifest_paper_caps(invalid):
    manifest = _manifest(3, "targeted", "explicit_user")
    manifest["config"]["max_papers"] = invalid
    with pytest.raises(ValueError, match="positive integer"):
        _resolved_run_config(dict(MODE_DEFAULTS["broad"]), _args(), manifest)


def test_requested_ocr_cannot_fall_back_to_caption_only(monkeypatch, tmp_path):
    class OcrUnavailable(RuntimeError):
        pass

    fake_ocr = SimpleNamespace(
        OcrUnavailable=OcrUnavailable,
        ocr_figures=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OcrUnavailable("easyocr is not installed")
        ),
    )
    monkeypatch.setattr(
        evidence_first.importlib,
        "import_module",
        lambda name: fake_ocr if name == "ocr_figures" else None,
    )
    parsed = {
        "paper-1": {
            "figures": [{"figure_id": "fig1", "image_path": "fig1.png"}]
        }
    }
    candidates = [{"paper_id": "paper-1", "block_id": "paper-1:CAP:fig1"}]

    with pytest.raises(RuntimeError, match="OCR was requested but is unavailable"):
        _targeted_ocr(parsed, candidates, {}, tmp_path)
