"""Classify parsed full text and choose the best recovery result.

Retrieval and parsing are separate boundaries.  A server can return a valid
JATS document that contains only metadata or figure captions; that is retrieved
content, but it is not a usable body-text parse.  This module makes that
distinction durable instead of letting a zero-sentence artifact count as an
ordinary parsed paper.
"""
from __future__ import annotations

from typing import Any


USABLE = "usable"
LOW_QUALITY = "low_quality"
FIGURE_ONLY = "figure_only"
UNUSABLE = "unusable"
VALID_STATES = frozenset({USABLE, LOW_QUALITY, FIGURE_ONLY, UNUSABLE})

MIN_SUBSTANTIVE_SENTENCE_CHARS = 20
DEFAULT_MIN_SUBSTANTIVE_SENTENCES = 20


def _nonempty_figures(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row for row in (parsed.get("figures") or [])
        if str(row.get("caption") or "").strip() or row.get("image_path")
    ]


def substantive_sentences(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Body/abstract sentences long enough to carry a scientific assertion."""
    return [
        row for row in (parsed.get("sentences") or [])
        if len(str(row.get("text") or "").strip()) >= MIN_SUBSTANTIVE_SENTENCE_CHARS
    ]


def assess(
    parsed: dict[str, Any],
    *,
    min_sentences: int,
    recovery_attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a durable quality receipt for one retrieved source document."""
    sentences = parsed.get("sentences") or []
    substantive = substantive_sentences(parsed)
    figures = _nonempty_figures(parsed)
    threshold = max(1, int(min_sentences))
    if len(substantive) >= threshold:
        state = USABLE
        reason = "body text meets the configured sentence threshold"
    elif substantive:
        state = LOW_QUALITY
        reason = "body text exists but is below the configured sentence threshold"
    elif figures:
        state = FIGURE_ONLY
        reason = "no substantive body sentences; figure captions or images remain"
    else:
        state = UNUSABLE
        reason = "retrieved source yielded neither substantive body text nor figures"
    return {
        "schema_version": 1,
        "paper_id": str(parsed.get("paper_id") or ""),
        "state": state,
        "parser": str(parsed.get("parser") or ""),
        "sentence_count": len(sentences),
        "substantive_sentence_count": len(substantive),
        "minimum_substantive_sentences": threshold,
        "figure_count": len(parsed.get("figures") or []),
        "nonempty_figure_count": len(figures),
        "reason": reason,
        "recovery_attempts": list(recovery_attempts or []),
    }


def quality_key(parsed: dict[str, Any]) -> tuple[int, int, int]:
    """Rank recovery candidates: body text, then captions, then image supply."""
    figures = _nonempty_figures(parsed)
    return (
        len(substantive_sentences(parsed)),
        sum(bool(str(row.get("caption") or "").strip()) for row in figures),
        sum(bool(row.get("image_path")) for row in figures),
    )


def prefer(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Choose the richer parse without mutating either candidate."""
    return candidate if quality_key(candidate) > quality_key(current) else current
