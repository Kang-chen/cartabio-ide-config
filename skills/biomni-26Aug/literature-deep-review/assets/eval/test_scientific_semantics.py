"""Scientific direction, model, outcome, and scope cannot drift in synthesis."""
from __future__ import annotations

from scientific_semantics import assertion_errors, statement_semantic_errors


def test_reversed_mechanistic_direction_is_rejected():
    rows = [{"quote": "IXA4 increased GSSG and activated IRE1/XBP1s signalling."}]

    errors = statement_semantic_errors(
        "IXA4 decreases GSSG and inhibits IRE1/XBP1s signalling.",
        rows,
        where="infographic panel B",
    )

    assert any("direction reversal" in error for error in errors)


def test_cell_viability_cannot_be_upgraded_to_tumor_regression():
    rows = [{
        "quote": "IXA4 reduced proliferation and viability in cultured cancer cells.",
        "section": "Results",
    }]

    errors = statement_semantic_errors(
        "IXA4 causes tumour regression and improves survival.",
        rows,
        where="conclusion",
    )

    assert any("outcome escalation" in error for error in errors)


def test_cell_line_result_cannot_be_generalized_to_most_normal_tissues():
    rows = [{"quote": "Two non-transformed cell lines remained viable after knockout."}]

    errors = statement_semantic_errors(
        "Most normal cells and tissues tolerate SLC33A1 loss.",
        rows,
        where="summary",
    )

    assert any("population overreach" in error for error in errors)


def test_structured_visual_assertion_rejects_animal_outcome_from_cell_only_anchor():
    evidence = {"E-1": {
        "evidence_id": "E-1",
        "claim_id": "C-1",
        "quote": "Knockdown reduced viability in cultured tumor cells.",
    }}
    assertion = {
        "assertion_id": "INFO-C-01",
        "panel": "C",
        "text": "Knockdown causes tumor regression.",
        "subject": "SLC33A1",
        "relation": "loss causes",
        "object": "tumor regression",
        "direction": "decrease",
        "model": "animal",
        "outcome": "tumor_burden",
        "claim_ids": ["C-1"],
        "evidence_ids": ["E-1"],
    }

    errors = assertion_errors(assertion, evidence, where="INFO-C-01")

    assert any("outcome" in error for error in errors)
    assert any("model=animal" in error for error in errors)
