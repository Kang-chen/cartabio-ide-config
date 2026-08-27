#!/usr/bin/env python3
"""Display-time quote/caption hygiene helpers for the deliverable builders.

The *acceptance* layer (``evidence_first.py``) already rejects garbled/merged
caption quotes and sub-sentence fragments, so a clean run's canonical evidence is
already safe. These helpers are a thin, reusable **display** guard for the
per-run Markdown / PDF builders so that:

  1. a garbled or fragmentary anchor is never rendered verbatim even if a *stale*
     or hand-edited evidence file slips one through (defense in depth), and
  2. an embedded figure's "Original caption (verbatim)" line is trimmed to whole
     sentences and never ends on a dangling fragment (parsed caption blocks often
     capture only the first line or two, e.g. ``"Fig. 7 ... of 3-"``).

Import these in ``build_review.py`` / ``build_pdf.py`` instead of re-implementing
the logic per run:

    from quote_display import anchor_text_unclean, caption_for_display

Both functions degrade gracefully if ``evidence_first`` cannot be imported.
"""
from __future__ import annotations

import re

try:  # reuse the canonical detectors so display == acceptance semantics
    from evidence_first import (
        looks_column_garbled as _looks_garbled,
        is_incomplete_sentence_quote as _incomplete_q,
    )
except Exception:  # pragma: no cover - fallback keeps builders runnable
    def _looks_garbled(_t: str) -> bool:
        return False

    def _incomplete_q(_q: str, _bt, _st=None) -> bool:
        return False

# `evidence_first` pulls in the vendor parsers and their heavy dependencies, so
# on a renderer-only host the import above can fail and take every garbling
# check with it. `quote_integrity` is pure stdlib and always importable, so the
# damage detectors that matter most survive that failure rather than silently
# degrading to "nothing is garbled".
from quote_integrity import problems as _quote_problems  # noqa: E402


_FIGLABEL = re.compile(r"^\s*((?:Fig(?:ure)?\.?)\s*\d+[.:]?\s*)", re.I)
_DANGLING = re.compile(
    r"[\s,;:\-\u2013\u2014]+(?:of|in|the|and|a|an|to|with|for|by|on|at|as|or|from|"
    r"that|which|was|were|is|are)\s*$",
    re.I,
)


def anchor_block_type(anchor: dict) -> str:
    """Resolve an anchor's block type. ``grounded_quotes.json`` anchors may omit
    ``block_type``; infer it from the ``block_id`` suffix
    (``:CAP:`` -> caption, ``:OCR:`` -> figure_ocr, else sentence)."""
    bt = anchor.get("block_type")
    if bt:
        return bt
    bid = anchor.get("block_id", "")
    if ":CAP:" in bid:
        return "caption"
    if ":OCR:" in bid:
        return "figure_ocr"
    return "sentence"


def anchor_text_unclean(anchor: dict) -> bool:
    """True if an accepted anchor's quote is merged/garbled or (for a body
    sentence) an incomplete sentence fragment, i.e. must not be shown verbatim.

    Clean captions / figure-OCR lines are legitimately partial, so the
    complete-sentence rule is applied only to ``sentence`` blocks; the
    merged/garbled check applies to every block type."""
    q = anchor.get("quote", "")
    bt = anchor_block_type(anchor)
    if _looks_garbled(q) or _quote_problems(q):
        return True
    if bt == "sentence" and _incomplete_q(q, "sentence", anchor.get("source_text")):
        return True
    return False


def anchor_quote_for_display(anchor: dict, max_chars: int) -> str:
    """The quote as the report should print it.

    A caption anchor is abridged the same way an embedded figure's caption is.
    A journal figure legend is one "sentence" of running text describing every
    panel, and quoting it whole put 200-400 words of panel-by-panel statistics
    under a claim — "b-d, Percentage time freezing... **P < 0.01,***P < 0.001"
    — filling most of a page with material that has nothing to do with the
    claim it is meant to support. The finding is in the legend's opening
    sentences; the rest is apparatus.

    Sentence anchors are returned untouched. Abridging runs on whole sentences
    and marks the result, so the verbatim promise holds either way.
    """
    quote = str(anchor.get("quote") or "")
    if anchor_block_type(anchor) != "caption":
        return quote
    return caption_for_display(quote, max_chars=max_chars)


def caption_for_display(text: str, max_chars: int | None = None) -> str:
    """Trim a verbatim caption for embed display so it never ends on a dangling
    fragment. Peels the leading ``Fig. N`` label (so it is not mistaken for a
    sentence end), keeps whole sentences by cutting back to the last
    sentence-terminal punctuation, and otherwise keeps the leading title clause
    before the first sub-panel marker (dangling numbers / function words
    stripped, ellipsis appended so it reads as abbreviated, not broken).

    ``max_chars`` additionally caps the length at a SENTENCE boundary. A caption
    is context for the crop above it; one shipped figure reproduced 300 words of
    panel-by-panel legend under a schematic, and the caption became the page.
    Cutting mid-sentence would break the verbatim promise, so the cap is applied
    by dropping whole trailing sentences and marking the result as abridged.
    """
    s = re.sub(r"\(cid:\d+\)", "", str(text or ""))
    s = re.sub(r"\s{2,}", " ", s).strip()
    if not s:
        return s
    m0 = _FIGLABEL.match(s)
    label = (m0.group(1).strip() if m0 else "")
    body = s[m0.end():] if m0 else s

    def _join(lbl: str, txt: str) -> str:
        txt = txt.strip()
        joined = (lbl + " " + txt).strip() if lbl else txt
        return _abridge(joined, max_chars)

    if re.search(r"[.!?][\"'\u201d\u2019)\]]*$", body):
        return _join(label, body)
    matches = list(re.finditer(r"[.!?][\"'\u201d\u2019)\]]*\s", body))
    if matches:
        return _join(label, body[:matches[-1].end()])
    panel = re.search(r"\s(?:[a-zA-Z]\s|\([a-z]\))", body)
    head = (body[:panel.start()] if panel else body).strip()
    prev = None
    while prev != head:
        prev = head
        head = re.sub(r"[\s,;:\-\u2013\u2014]*\d*[\-\u2013\u2014]?$", "", head).strip()
        head = _DANGLING.sub("", head).strip()
    return _abridge(_join(label, head) + "\u2026", max_chars)


def _abridge(text: str, max_chars: int | None) -> str:
    """Cut ``text`` to whole sentences within ``max_chars``.

    Never cuts mid-sentence: what remains must still be an exact quotation of
    the source. When even the first sentence is over budget the whole sentence
    is kept, because a truncated sentence is no longer verbatim and being long
    is the lesser fault.
    """
    if not max_chars or len(text) <= max_chars:
        return text
    ends = [m.end() for m in re.finditer(r"[.!?][\"'\u201d\u2019)\]]*(?:\s|$)", text)]
    kept = [e for e in ends if e <= max_chars]
    if not kept:
        return text if not ends else text[:ends[0]].strip()
    return text[:kept[-1]].strip() + " [\u2026]"
