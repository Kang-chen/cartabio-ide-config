#!/usr/bin/env python3
"""Deterministic scientific-scope checks shared by prose and infographics.

These checks are deliberately conservative.  They do not try to decide whether
a biological claim is true.  They stop a narrower and more damaging class of
errors: reversing a reported direction, upgrading a cell-culture result to an
animal/clinical outcome, or generalising a tested model to an untested
population.  Biomni still performs the scientific judgement; this module makes
the dimensions of that judgement explicit and auditable.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

DIRECTIONS = frozenset({"increase", "decrease", "mixed", "no_change", "not_applicable"})
MODELS = frozenset({
    "biochemical", "cell_free", "cell_line", "organoid", "ex_vivo",
    "animal", "human_observational", "human_interventional", "unspecified",
})
OUTCOMES = frozenset({
    "mechanism", "molecular_marker", "cell_viability", "tumor_burden",
    "animal_survival", "human_biomarker", "clinical_outcome", "unspecified",
})

_UP = re.compile(
    r"(?:\u2191|\b(?:increase[sd]?|increasing|higher|elevat(?:e[sd]?|ion)|"
    r"activat(?:e[sd]?|ion)|induc(?:e[sd]?|tion)|accumulat(?:e[sd]?|ion)|ris(?:e[sn]?|ing)|"
    r"up-?regulat(?:e[sd]?|ion)|gain(?:ed)?|enhanc(?:e[sd]?|ement))\b)",
    re.IGNORECASE,
)
_DOWN = re.compile(
    r"(?:\u2193|\b(?:decrease[sd]?|decreasing|lower|reduc(?:e[sd]?|tion)|"
    r"inhibit(?:s|ed|ion)?|suppress(?:e[sd]?|ion)|deplet(?:e[sd]?|ion)|fall(?:s|ing)?|"
    r"down-?regulat(?:e[sd]?|ion)|loss|impair(?:e[sd]?|ment))\b)",
    re.IGNORECASE,
)
_NO_CHANGE = re.compile(
    r"\b(?:no (?:significant )?(?:change|effect|difference)|unchanged|null result)\b",
    re.IGNORECASE,
)
_IN_VIVO = re.compile(
    r"\b(?:in vivo|xenograft|mouse|mice|murine|animal|tumou?r (?:volume|burden|growth)|"
    r"kaplan[- ]meier|survival)\b",
    re.IGNORECASE,
)
_CELL_ONLY = re.compile(
    r"\b(?:cell line|cells? in culture|in vitro|viability|proliferation|colony formation|"
    r"IC50|EC50)\b",
    re.IGNORECASE,
)
_REGRESSION = re.compile(
    r"\b(?:tumou?r regression|tumou?r regresses?|tumou?r shrink(?:s|age)?|"
    r"eradicated? tumou?r|complete response|improved survival|survival benefit)\b",
    re.IGNORECASE,
)
_UNIVERSAL_SCOPE = re.compile(
    r"\b(?:most|all|generally|broadly|across)\s+(?:normal|healthy|non[- ]?malignant)\s+"
    r"(?:cells?|tissues?|populations?)|\bnormal (?:cells?|tissues?) (?:tolerate|are spared)\b",
    re.IGNORECASE,
)


def infer_direction(text: str) -> str | None:
    """Return the unambiguous direction expressed in ``text`` when possible."""
    value = str(text or "")
    up, down, no_change = bool(_UP.search(value)), bool(_DOWN.search(value)), bool(_NO_CHANGE.search(value))
    if no_change and not up and not down:
        return "no_change"
    if up and down:
        return "mixed"
    if up:
        return "increase"
    if down:
        return "decrease"
    return None


def _source_text(rows: Iterable[dict]) -> str:
    return " ".join(
        " ".join(str(row.get(key) or "") for key in (
            "quote", "source_text", "scope_note", "section", "source_locator"
        ))
        for row in rows
    )


def statement_semantic_errors(text: str, rows: Iterable[dict], *, where: str) -> list[str]:
    """Catch direction, outcome, and population upgrades in evidence-backed prose."""
    rows = list(rows)
    source = _source_text(rows)
    if not source.strip():
        return []
    errors: list[str] = []
    stated_direction = infer_direction(text)
    source_directions = {
        direction for row in rows
        if (direction := infer_direction(_source_text([row]))) not in {None, "mixed"}
    }
    if stated_direction in {"increase", "decrease", "no_change"} and len(source_directions) == 1:
        source_direction = next(iter(source_directions))
        if stated_direction != source_direction:
            errors.append(
                f"{where}: direction reversal: prose says {stated_direction}, but all cited "
                f"anchors with a clear direction say {source_direction}"
            )
    if _REGRESSION.search(text) and not _IN_VIVO.search(source):
        qualifier = "cell-culture" if _CELL_ONLY.search(source) else "non-in-vivo"
        errors.append(
            f"{where}: outcome escalation: prose claims tumour regression or survival, but "
            f"the cited anchors are {qualifier} and contain no in-vivo outcome"
        )
    if _UNIVERSAL_SCOPE.search(text) and not _UNIVERSAL_SCOPE.search(source):
        errors.append(
            f"{where}: population overreach: prose generalises to normal cells/tissues, but "
            "the cited anchors do not establish that population-wide scope"
        )
    return errors


def assertion_errors(assertion: dict, evidence_by_id: dict[str, dict], *, where: str) -> list[str]:
    """Validate one structured visual assertion against its exact evidence rows."""
    errors: list[str] = []
    required = ("assertion_id", "panel", "text", "subject", "relation", "object",
                "direction", "model", "outcome", "claim_ids", "evidence_ids")
    missing = [key for key in required if not assertion.get(key)]
    if missing:
        return [f"{where}: missing required assertion fields: {', '.join(missing)}"]
    direction = str(assertion.get("direction") or "")
    model = str(assertion.get("model") or "")
    outcome = str(assertion.get("outcome") or "")
    if direction not in DIRECTIONS:
        errors.append(f"{where}: direction must be one of {', '.join(sorted(DIRECTIONS))}")
    if model not in MODELS:
        errors.append(f"{where}: model must be one of {', '.join(sorted(MODELS))}")
    if outcome not in OUTCOMES:
        errors.append(f"{where}: outcome must be one of {', '.join(sorted(OUTCOMES))}")
    ids = [str(value) for value in assertion.get("evidence_ids") or []]
    unknown = [value for value in ids if value not in evidence_by_id]
    if unknown:
        errors.append(f"{where}: unknown evidence_ids: {', '.join(unknown)}")
    rows = [evidence_by_id[value] for value in ids if value in evidence_by_id]
    cited_claims = {str(row.get("claim_id") or "") for row in rows}
    undeclared = cited_claims - {str(value) for value in assertion.get("claim_ids") or []}
    if undeclared:
        errors.append(f"{where}: evidence belongs to undeclared claim_ids: {', '.join(sorted(undeclared))}")
    errors += statement_semantic_errors(str(assertion.get("text") or ""), rows, where=where)
    authored_direction = infer_direction(str(assertion.get("text") or ""))
    if direction in {"increase", "decrease", "no_change"} and authored_direction not in {None, direction}:
        errors.append(
            f"{where}: structured direction is {direction}, but assertion text expresses "
            f"{authored_direction}"
        )
    source = _source_text(rows)
    if outcome in {"tumor_burden", "animal_survival", "clinical_outcome"} and not _IN_VIVO.search(source):
        errors.append(f"{where}: outcome={outcome} is not established by an in-vivo/clinical anchor")
    if model in {"animal", "human_observational", "human_interventional"} and _CELL_ONLY.search(source) and not _IN_VIVO.search(source):
        errors.append(f"{where}: model={model} upgrades cell-only evidence")
    return errors


# The two summary sections that carry a STRICT evidence class. Every other
# section accepts both cited and reviewer-inference statements, so it imposes no
# class of its own here.
_SECTION_EVIDENCE_CLASS = {
    "key_findings": "grounded",       # renders grounded results: must cite evidence
    "external_findings": "external",  # ungrounded but sourced + resolvable
}

# A locator a reader can actually follow: a URL, a DOI, a trial-registry id
# (NCT/ISRCTN/accession style), or a EudraCT number. A bare word like
# "conference" is a source, not a resolvable locator.
_RESOLVABLE_LOCATOR_RE = re.compile(
    r"https?://\S+"
    r"|(?:doi:\s*)?10\.\d{4,9}/\S+"
    r"|[A-Za-z]{2,}\d{4,}"
    r"|\d{4}-\d{6}-\d{2}",
    re.IGNORECASE,
)


def _resolvable_locator(locator: str) -> bool:
    return bool(locator) and bool(_RESOLVABLE_LOCATOR_RE.search(locator))


def section_classification_errors(key: str, statement: dict, index: int) -> list[str]:
    """Reject a statement placed in a section whose evidence class it does not fit.

    Two summary sections carry a strict evidence class. ``key_findings`` renders
    grounded executive-summary results and must cite retained evidence_ids.
    ``external_findings`` renders MATERIAL results that have no retrievable full
    text — a conference abstract, a trial-registry entry, an announcement — so it
    must NOT carry evidence_ids, and, being ungrounded, it must still name its
    source and a resolvable locator so the reader can find it. Any statement
    whose evidence class does not match its section is rejected.
    """
    expected = _SECTION_EVIDENCE_CLASS.get(key)
    if expected is None:
        return []
    where = f"report_sections.json {key}[{index}]"
    has_evidence = bool(statement.get("evidence_ids"))

    if expected == "grounded":
        if not has_evidence:
            return [
                f"{where}: must cite retained evidence_ids; otherwise move it to "
                "external_findings or mark it as reviewer inference in another "
                "section."
            ]
        return []

    # expected == "external"
    if has_evidence:
        return [
            f"{where}: has retained evidence_ids, so it is grounded. Move it to "
            "key_findings; external_findings is reserved for material results "
            "with no retrievable full-text anchor."
        ]
    errors: list[str] = []
    if not str(statement.get("source") or "").strip():
        errors.append(
            f"{where}: an external finding must name its source (the conference, "
            "trial registry, or announcement it comes from), because it carries "
            "no retrievable full-text anchor."
        )
    if not _resolvable_locator(str(statement.get("locator") or "").strip()):
        errors.append(
            f"{where}: an external finding must carry a resolvable locator — a "
            "DOI, URL, or registry identifier the reader can follow to the "
            "reported result."
        )
    return errors
