#!/usr/bin/env python3
"""Orthogonal evidence labels for reporting.

Publication type, anchor location, claim relationship, and independence answer
different questions.  Keeping them separate prevents labels such as
"secondary evidence" from being applied merely because a primary paper was
available only through its abstract.
"""
from __future__ import annotations

import re
from collections import defaultdict

_REVIEW_TYPES = re.compile(
    r"\b(?:review|meta-analysis|perspective|commentary|editorial|guideline)\b",
    re.IGNORECASE,
)


def publication_type(row: dict, reference: dict | None = None) -> str:
    source = " ".join(str((reference or {}).get(key) or row.get(key) or "")
                      for key in ("study_type", "publication_type"))
    if _REVIEW_TYPES.search(source):
        return "review_or_commentary"
    return "primary_report" if source.strip() else "publication_type_unspecified"


def anchor_depth(row: dict) -> str:
    source = " ".join(str(row.get(key) or "") for key in (
        "section", "source_locator", "block_type"
    )).casefold()
    if "abstract" in source:
        return "abstract_only"
    if "result" in source or "figure_ocr" in source or "caption" in source:
        return "results_or_figure"
    if "method" in source:
        return "methods"
    if "discussion" in source or "conclusion" in source:
        return "discussion"
    if "introduction" in source or "background" in source:
        return "introduction_or_background"
    return "fulltext_unspecified" if row.get("source_locator") else "metadata_only"


def claim_relationship(row: dict) -> str:
    explicit = str(row.get("claim_relationship") or "").strip()
    if explicit:
        return explicit
    kind = str(row.get("evidence_kind") or "")
    if kind == "primary":
        return "direct"
    if kind == "secondary":
        return "indirect_or_citation"
    if kind == "inferred":
        return "reviewer_inference"
    return "unspecified"


def enrich_anchor(row: dict, reference: dict | None = None) -> dict:
    enriched = dict(row)
    enriched["publication_type"] = publication_type(row, reference)
    enriched["anchor_depth"] = anchor_depth(row)
    enriched["claim_relationship"] = claim_relationship(row)
    enriched["independence"] = {
        "study_id": str(row.get("study_id") or row.get("paper_id") or ""),
        "cohort_id": str(row.get("cohort_id") or ""),
        "publication_role": str(row.get("publication_role") or ""),
    }
    return enriched


def support_description(state: str, basis: dict) -> str:
    """Human-readable support without pretending papers equal replications."""
    studies = int(basis.get("n_primary_studies") or 0)
    papers = int(basis.get("n_primary_papers") or 0)
    cohorts = int(basis.get("n_primary_cohorts") or 0)
    if state == "C2_CONVERGENT":
        cohort_note = f", {cohorts} recorded cohort(s)" if cohorts else ""
        return f"Convergent ({studies} independent primary studies; {papers} papers{cohort_note})"
    if state == "C1_SINGLE_DIRECT":
        if studies == 1 and papers > 1:
            return f"One primary study reported across {papers} papers"
        return "One primary study"
    if state == "C1_INDIRECT":
        return "Indirect/background relationship only"
    return {
        "C_CONFLICTED": "Conflicted (support and contradiction)",
        "C_REFUTED": "Refuted by qualifying evidence",
        "C_INSUFFICIENT": "Insufficient qualifying evidence",
    }.get(state, state)


def support_metadata_by_claim(evidence: list[dict]) -> dict[str, dict]:
    """Dynamic support basis and label, keyed by canonical claim ID."""
    from evidence_first import support_basis

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in evidence:
        grouped[str(row.get("claim_id") or "")].append(row)
    out = {}
    for claim_id, rows in grouped.items():
        basis = support_basis(rows)
        out[claim_id] = {
            "basis": basis,
            "label": support_description(basis["support_state"], basis),
        }
    return out
