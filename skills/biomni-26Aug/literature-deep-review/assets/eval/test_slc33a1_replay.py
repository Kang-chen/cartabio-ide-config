"""Replay the structural failures observed in the uploaded SLC33A1 run."""
from __future__ import annotations

import json
import pathlib

from PIL import Image


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "slc33a1_regression.json"


def _case() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_observed_count_drift_is_repaired_from_canonical_rows(run_root):
    from reconcile_run import refresh

    observed = _case()["observed"]
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.setdefault("metrics", {})["evidence_accepted"] = observed[
        "manifest_evidence_accepted"
    ]
    manifest_path.write_text(json.dumps(manifest))

    receipt, failures = refresh(run_root, write=True)

    assert failures == []
    assert receipt["counts"]["evidence_accepted"] != observed[
        "manifest_evidence_accepted"
    ]
    assert json.loads(manifest_path.read_text())["metrics"]["evidence_accepted"] == (
        receipt["counts"]["evidence_accepted"]
    )


def test_observed_mixed_panels_remain_primary_data():
    from figure_selection import figure_role

    assert all(
        figure_role({}, caption) == "primary_data"
        for caption in _case()["mixed_panel_captions"]
    )


def test_unresolved_transient_retry_blocks_reconciliation(run_root):
    from reconcile_run import refresh

    observed = _case()["observed"]
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["config"]["require_global_transient_retry"] = True
    manifest_path.write_text(json.dumps(manifest))
    retry_path = run_root / "fulltext" / "global_transient_retry.json"
    retry_path.write_text(json.dumps({
        "completed": False,
        "remaining": observed["transient_retry_remaining"],
    }))

    _receipt, failures = refresh(run_root, write=True)

    assert any("transient-retrieval recovery is incomplete" in row for row in failures)


def test_observed_selected_export_gap_requires_dispositions(run_root):
    from reconcile_run import refresh

    observed = _case()["observed"]
    # The older replay had two silently missing exports. The current run closed
    # that gap, so inject the same defect to keep the disposition gate covered.
    assert observed["figures_selected"] == observed["figures_exported"]
    path = run_root / "deliverables" / "figures_cited" / "figures_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["selected_figure_ids"] = [
        {"paper_id": "P", "figure_id": f"missing-{index}"}
        for index in range(2)
    ]
    path.write_text(json.dumps(manifest))

    _receipt, failures = refresh(run_root, write=True)

    assert sum("selected figure" in failure for failure in failures) == 2


def test_observed_low_resolution_uncaptioned_fragment_is_not_report_eligible(
    tmp_path,
):
    from export_figures import image_candidate_disposition

    observed = _case()["observed"]
    fragment = tmp_path / "fragment.png"
    Image.new(
        "RGB",
        (observed["bad_embedded_width"], observed["bad_embedded_height"]),
        "white",
    ).save(fragment)

    reason = image_candidate_disposition({
        "figure_id": "fig44_embedded_p41_i2",
        "caption": "",
        "parent_figure_id": "",
        "image_path": str(fragment),
    })

    assert reason == "partial_embedded_fragment"


def test_publisher_header_in_the_crop_is_not_report_eligible(tmp_path):
    from export_figures import image_candidate_disposition

    crop = tmp_path / "crop-with-header.png"
    Image.new("RGB", (1200, 900), "white").save(crop)

    reason = image_candidate_disposition({
        "figure_id": "fig6_p08",
        "caption": "SLC33A1 loss changes treatment response.",
        "image_path": str(crop),
        "ocr": [{"text": "WILEY", "bbox": [[20, 8], [100, 8],
                                               [100, 35], [20, 35]]}],
    })

    assert reason == "page_header_contamination"


def test_text_clipped_at_crop_edge_is_not_report_eligible(tmp_path):
    from export_figures import image_candidate_disposition

    crop = tmp_path / "crop-with-clipped-label.png"
    Image.new("RGB", (1200, 900), "white").save(crop)

    reason = image_candidate_disposition({
        "figure_id": "fig2_p04",
        "caption": "SLC33A1 knockdown changes treatment response.",
        "image_path": str(crop),
        "ocr": [{"text": "SLC33A1", "bbox": [[0, 100], [90, 100],
                                                  [90, 130], [0, 130]]}],
    })

    assert reason == "clipped_text_at_crop_edge"


def test_adjacent_body_prose_is_not_report_eligible(tmp_path):
    from export_figures import image_candidate_disposition

    crop = tmp_path / "crop-with-body-prose.png"
    Image.new("RGB", (1200, 900), "white").save(crop)
    prose = "This adjacent paragraph contains enough words to be article body prose"
    ocr = [
        {"text": prose, "bbox": [[100, y], [900, y], [900, y + 25], [100, y + 25]]}
        for y in (300, 340, 380)
    ]

    reason = image_candidate_disposition({
        "figure_id": "fig3_p05",
        "caption": "SLC33A1 expression across cancer models.",
        "image_path": str(crop),
        "ocr": ocr,
    })

    assert reason == "adjacent_body_prose_contamination"


def test_observed_calendar_span_is_not_presented_as_active_report_runtime(run_root):
    from report_model import build_model, coverage_notes, load_contract

    observed = _case()["observed"]
    stats_path = run_root / "deliverables" / "review_stats.json"
    stats = json.loads(stats_path.read_text())
    stats["end_to_end_elapsed_seconds"] = observed["calendar_elapsed_seconds"]
    stats_path.write_text(json.dumps(stats))
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.setdefault("metrics", {})["managed_machines"] = {
        "critical_path_seconds": observed["managed_critical_path_seconds"]
    }
    manifest_path.write_text(json.dumps(manifest))

    notes = coverage_notes(run_root, build_model(run_root, load_contract()))

    assert not any("elapsed time" in note.lower() for note in notes)


def test_observed_methods_cannot_reclassify_transient_misses_as_paywalls():
    from report_model import corpus_accounting_errors

    observed = _case()["observed"]
    sections = {"methods": [{
        "text": (
            f"Full text was retrieved for {observed['papers_full_text']} papers; "
            f"{observed['paywalled'] + observed['transient_retry_remaining']} "
            "hard-paywalled records contributed metadata only."
        )
    }]}
    ledger = {
        "retrieval_classification": {
            "paywalled": observed["paywalled"],
            "retrieval_failed": observed["transient_retry_remaining"],
        }
    }

    errors = corpus_accounting_errors(sections, ledger)

    assert any("hard-paywalled" in error and "5" in error for error in errors)


def test_observed_conclusion_cannot_turn_an_enriched_context_into_exclusivity():
    from report_model import section_scope_errors

    sections = {"conclusions": [{
        "text": "KEAP1/NRF2 status defines the responsive subset.",
        "evidence_ids": ["E-1"],
    }]}
    evidence = [{
        "evidence_id": "E-1",
        "quote": "KEAP1-mutant lung adenocarcinomas depend on Slc33a1.",
    }]

    errors = section_scope_errors(sections, evidence)

    assert any("exclusive" in error.lower() for error in errors)
