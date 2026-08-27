from __future__ import annotations

import json

import pytest

from corpus_ledger import REQUIRED_BROAD_AXES, refresh


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _run(tmp_path, selected=("p1", "p2")):
    _json(tmp_path / "run_manifest.json", {
        "mode": "broad", "config": {"max_papers": None}
    })
    _jsonl(tmp_path / "corpus" / "references.jsonl", [
        {"paper_id": "p1"}, {"paper_id": "p2"}, {"paper_id": "p3"}
    ])
    _jsonl(tmp_path / "corpus" / "records.jsonl", [
        {"paper_id": pid} for pid in selected
    ])
    _json(tmp_path / "corpus" / "coverage_matrix.json", {
        "axes": [
            {"axis": axis, "status": "searched_with_evidence", "queries": [axis]}
            for axis in REQUIRED_BROAD_AXES
        ]
    })
    _jsonl(tmp_path / "fulltext" / "parse_quality.jsonl", [
        {
            "paper_id": pid,
            "state": "usable",
            "reason": "fixture full text",
        }
        for pid in selected
    ])
    return tmp_path


def test_uncapped_broad_review_refuses_silent_in_scope_narrowing(tmp_path):
    root = _run(tmp_path)
    _ledger, errors = refresh(root)
    assert any("omitted in-scope papers: p3" in error for error in errors)


def test_explicit_scope_exclusion_with_reason_allows_broad_selection(tmp_path):
    root = _run(tmp_path)
    _jsonl(root / "corpus" / "scope_decisions.jsonl", [
        {"paper_id": "p3", "in_scope": False, "reason": "unrelated disease"}
    ])
    ledger, errors = refresh(root)
    assert errors == []
    assert ledger["counts"]["in_scope"] == 2
    assert ledger["counts"]["selected"] == 2


def test_final_ledger_requires_post_merge_retry_for_transient_misses(tmp_path):
    root = _run(tmp_path, selected=("p1", "p2", "p3"))
    _jsonl(root / "fulltext" / "papers.jsonl", [
        {"paper_id": "p1"}, {"paper_id": "p2"}
    ])
    _jsonl(root / "fulltext" / "not_retrieved.jsonl", [{
        "paper_id": "p3",
        "_not_retrieved_kind": "retrieval_failed",
        "_not_retrieved_reason": "timeout",
    }])
    _ledger, errors = refresh(root, final=True)
    assert any("global post-merge retry" in error for error in errors)

    _json(root / "fulltext" / "global_transient_retry.json", {
        "completed": True, "attempted": 1, "recovered": 0, "remaining": 1
    })
    _ledger, errors = refresh(root, final=True)
    assert errors == []


def test_prior_source_must_be_reconciled_if_not_retained(tmp_path):
    root = _run(tmp_path)
    _jsonl(root / "corpus" / "scope_decisions.jsonl", [
        {"paper_id": "p3", "in_scope": False, "reason": "unrelated disease"}
    ])
    _jsonl(root / "corpus" / "prior_references.jsonl", [{"paper_id": "old"}])
    _ledger, errors = refresh(root)
    assert any("prior source is not retained" in error for error in errors)

    _jsonl(root / "corpus" / "prior_run_reconciliation.jsonl", [{
        "paper_id": "old", "status": "superseded",
        "reason": "newer report of the same cohort",
        "replacement_paper_ids": ["p1"],
    }])
    _ledger, errors = refresh(root)
    assert errors == []


def test_broad_coverage_matrix_is_executable_not_prose(tmp_path):
    root = _run(tmp_path, selected=("p1", "p2", "p3"))
    _json(root / "corpus" / "coverage_matrix.json", {"axes": []})
    _ledger, errors = refresh(root)
    assert any("missing axis safety_essentiality" in error for error in errors)


def test_scope_decision_for_unknown_paper_is_rejected(tmp_path):
    root = _run(tmp_path)
    _jsonl(root / "corpus" / "scope_decisions.jsonl", [
        {"paper_id": "unknown", "in_scope": False, "reason": "noise"}
    ])
    with pytest.raises(ValueError, match="absent from references"):
        refresh(root)


def test_prior_retrieval_regression_blocks_final_assembly(tmp_path):
    root = _run(tmp_path, selected=("p1", "p2", "p3"))
    _jsonl(root / "fulltext" / "papers.jsonl", [
        {"paper_id": "p1"}, {"paper_id": "p2"}
    ])
    _jsonl(root / "fulltext" / "not_retrieved.jsonl", [{
        "paper_id": "p3", "_not_retrieved_kind": "paywalled",
        "_not_retrieved_reason": "publisher denied access",
    }])
    _json(root / "corpus" / "prior_corpus_ledger.json", {
        "records": [{"paper_id": "p3", "retrieved": True}]
    })

    ledger, errors = refresh(root, final=True)

    assert ledger["retrieval_regressions"][0]["paper_id"] == "p3"
    assert any("prior run retrieved" in error for error in errors)


def test_prior_version_family_does_not_regress_when_replacement_is_retrieved(tmp_path):
    root = _run(tmp_path, selected=("p1", "p2", "p3"))
    refs = [
        {"paper_id": "p1"}, {"paper_id": "p2"},
        {"paper_id": "p3", "study_id": "study-gssg"},
    ]
    _jsonl(root / "corpus" / "references.jsonl", refs)
    _jsonl(root / "corpus" / "records.jsonl", refs)
    _jsonl(root / "fulltext" / "papers.jsonl", refs)
    _json(root / "corpus" / "prior_corpus_ledger.json", {
        "records": [{
            "paper_id": "preprint-gssg", "study_id": "study-gssg",
            "retrieved": True,
        }]
    })

    ledger, errors = refresh(root, final=True)

    assert ledger["retrieval_regressions"] == []
    assert errors == []
