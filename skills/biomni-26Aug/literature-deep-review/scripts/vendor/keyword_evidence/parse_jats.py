"""JATS / PMC full-text XML parser — produces the SAME output contract as
`parse_pdf.parse_pdf`, so the deep-dive grounding join (`sourcemap.py`) and the
extraction pipeline consume it with zero changes.

Why this exists
---------------
Many papers whose *publisher PDF* is paywalled (403) are nonetheless fully open
as **JATS XML** served by Europe PMC's `fullTextXML` endpoint (CC-BY / CC0 /
NIH-PMC-open licences). Retrieving that XML and parsing it is a legitimate
open-access route — it never bypasses a paywall; it uses the copy the archive
itself publishes openly. In a PINK1 pilot this roughly doubled the recoverable
full-text corpus (9 -> ~15 papers).

Figures: why a supplementary PDF is needed
------------------------------------------
JATS carries figure *captions* but no page geometry, so there is nothing in the
XML to crop an image out of — `image_path` was unconditionally None and an
XML-sourced paper could never contribute a figure to the report, only a caption.
`acquire.acquire_pdf` now makes a best-effort supplementary fetch of an open PDF
after an XML win and records it as `record["figures_pdf"]` (distinct from
`local_pdf`; the XML stays the text source). Pass that path as `figures_pdf` and
this parser will crop the figures out of it and join them onto the JATS figures
by label ("Figure 3" <-> "Fig. 3"). Omit it and behaviour is exactly as before.

Output contract (identical keys to parse_pdf):
  {
    "paper_id": str,
    "parser":  "jats-xml",
    "n_pages": None,               # XML has no page geometry; section is the locator
    "sections": [{"title": str, "full_title": str,
                  "page_start": None, "page_end": None}, ...],
    "sentences": [{"sentence_id": int, "text": str, "section": str,
                   "page": None, "bbox": None}, ...],
    "figures": [{"figure_id": str, "label": str, "page": None,
                 "caption": str, "caption_bbox": None, "figure_bbox": None,
                 "image_path": None}, ...],
  }

Figures matched to a supplementary PDF additionally carry a real `image_path`,
the PDF's `page`/`figure_bbox`/`caption_bbox`, and `image_source="figures_pdf"`
so downstream can tell a cropped figure from a caption-only one. The caption
always stays the JATS one (it is cleaner than the PDF's reflowed text).

Locators for XML-sourced evidence are section-based (e.g. "Results",
"Discussion") plus, for figure/table captions, the figure label (e.g.
"Figure 3"). Downstream code already renders `section` and `figure_id`; a null
`page` simply means "page not applicable (parsed from full-text XML)".

A JATS nested `<sec><title>` is free text and is often a whole sentence, so the
`section` locator is canonicalized ("3.1 Results in mice" -> "Results") and
capped at `_MAX_SECTION_CHARS`; the untouched heading survives as the section's
`full_title`.
"""
from __future__ import annotations

import pathlib
import re
from typing import Any
from xml.etree import ElementTree as ET

try:
    import pysbd  # same segmenter the PDF parser uses -> identical sentences
    _SEG = pysbd.Segmenter(language="en", clean=False)

    def _segment(text: str) -> list[str]:
        try:
            return _SEG.segment(text)
        except Exception:
            return [text]
except Exception:  # pragma: no cover - pysbd should be present with the PDF stack
    def _segment(text: str) -> list[str]:
        # Conservative regex fallback: split on sentence-final punctuation
        # followed by whitespace + capital / digit.
        return re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)


def _clean_text(s: str) -> str:
    """Collapse whitespace; mirror parse_pdf._clean_text behaviour."""
    return re.sub(r"\s+", " ", (s or "")).strip()


def _strip_ns(tag: str) -> str:
    """Drop any XML namespace prefix: '{ns}sec' -> 'sec'."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


# Elements that render as their own block. Text on either side of a block
# boundary is separated by a space; everything else (<italic>, <sup>, <sub>,
# <bold>, <xref>, ...) is inline and must join with NO separator.
_BLOCK_TAGS = frozenset({"title", "p", "list-item", "sec", "label", "caption"})

# A block boundary never inserts a space in front of these: they bind to the
# preceding word ("(Fig. 3)", "levels," ...).
_NO_SPACE_BEFORE = ".,;:!?)]}%"


def _itertext_clean(el: ET.Element) -> str:
    """All descendant text of an element, whitespace-collapsed, with block
    boundaries separated.

    We keep xref text (e.g. "Fig. 2") because it is part of the human-readable
    sentence, and we drop table markup noise by concatenating text nodes.

    Why a walker instead of ``"".join(el.itertext())``: itertext() has no idea
    where an element begins or ends, so a caption of the form
    ``<caption><title>...study participants.</title><p>CSF samples...</p></caption>``
    came out as "...study participants.CSF samples..." — a fused word that
    downstream then rejected as garbled OCR, throwing away real evidence. A
    blanket ``" ".join`` is equally wrong: it would produce "PGRN - / - mice"
    and "Fig . 3". So we walk the tree and insert a separator only at a
    _BLOCK_TAGS boundary, and only when the text so far does not already end in
    whitespace and the next chunk does not open with whitespace or closing
    punctuation."""
    parts: list[str] = []
    pending = False  # a block boundary is waiting to be honoured

    def _push(text: str | None) -> None:
        nonlocal pending
        if not text:
            return
        if pending:
            pending = False
            if (parts and not parts[-1][-1:].isspace()
                    and not text[:1].isspace()
                    and text[:1] not in _NO_SPACE_BEFORE):
                parts.append(" ")
        parts.append(text)

    def _walk(node: ET.Element) -> None:
        nonlocal pending
        block = _strip_ns(node.tag) in _BLOCK_TAGS
        if block and parts:
            pending = True
        _push(node.text)
        for child in node:
            _walk(child)
            _push(child.tail)
        if block:
            pending = True

    _walk(el)
    return _clean_text("".join(parts))


# Section titles we never want to mine as evidence (references, funding, etc.).
_SKIP_SEC = re.compile(
    r"^(references?|bibliography|acknowledge?ments?|funding|"
    r"author contributions?|competing interests?|conflicts? of interest|"
    r"supplementary|supporting information|abbreviations?|"
    r"data availability|ethics|contributor information|"
    r"disclosure|footnotes?|appendix)\b",
    re.IGNORECASE,
)


# Evidence locators carry the section label, and the report contract rejects
# one longer than this (templates/report_contract.json: max_section_chars).
_MAX_SECTION_CHARS = 60
_TRUNC_MARK = "..."

# A JATS <sec><title> is free text: nested titles are routinely whole sentences
# ("PGRN loss induces demyelination as well as neuronal Tdp-43 inclusion
# formation in white matter regions, ..."), which is useless as a locator and
# blows the contract's length gate. A title that *opens* with one of these
# names collapses to the canonical label; anything else is truncated on a word
# boundary. Ordered most-specific-first so "Materials and Methods" is matched
# before its "Methods" suffix-name.
_CANONICAL_SECTIONS: tuple[tuple[str, str], ...] = (
    ("materials and methods", "Materials and Methods"),
    ("methods and materials", "Materials and Methods"),
    ("materials & methods", "Materials and Methods"),
    ("acknowledgements", "Acknowledgements"),
    ("acknowledgments", "Acknowledgements"),
    ("supplementary", "Supplementary"),
    ("introduction", "Introduction"),
    ("conclusions", "Conclusions"),
    ("conclusion", "Conclusion"),
    ("background", "Background"),
    ("discussion", "Discussion"),
    ("references", "References"),
    ("abstract", "Abstract"),
    ("results", "Results"),
    ("methods", "Methods"),
)

# Numeric heading prefixes ("3.", "3.1 ", "2)") are not part of the name.
_SEC_NUM_PREFIX = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s*")


def _canonical_section(title: str) -> str | None:
    """Canonical section name if `title` opens with one, else None."""
    low = _SEC_NUM_PREFIX.sub("", (title or "").strip().lower())
    for prefix, canon in _CANONICAL_SECTIONS:
        if low.startswith(prefix) and not low[len(prefix):len(prefix) + 1].isalpha():
            return canon
    return None


def _short_section(title: str) -> str:
    """A section label fit for an evidence locator: canonical when we recognise
    the heading, otherwise capped at _MAX_SECTION_CHARS on a word boundary.

    The untouched title is not lost — callers carry it alongside as the
    section's `full_title`.
    """
    t = _clean_text(title)
    canon = _canonical_section(t)
    if canon:
        return canon
    if len(t) <= _MAX_SECTION_CHARS:
        return t
    cut = t[:_MAX_SECTION_CHARS - len(_TRUNC_MARK)].rstrip()
    if " " in cut:
        cut = cut[:cut.rindex(" ")].rstrip()
    return cut.rstrip(",;:-") + _TRUNC_MARK


def _find_body(root: ET.Element) -> ET.Element | None:
    for el in root.iter():
        if _strip_ns(el.tag) == "body":
            return el
    return None


def _find_abstract(root: ET.Element) -> str:
    """Concatenate abstract paragraph text.

    Join on ". " between blocks so a heading token (e.g. a structured-abstract
    "Background" label) never fuses with the following word into one bogus token
    like "AbstractParkinson's". We collect <p> descendants when present, else
    the whole <abstract> text.
    """
    parts: list[str] = []
    for el in root.iter():
        if _strip_ns(el.tag) != "abstract":
            continue
        ps = [ _itertext_clean(p) for p in el.iter() if _strip_ns(p.tag) == "p" ]
        ps = [p for p in ps if p]
        if ps:
            parts.extend(ps)
        else:
            t = _itertext_clean(el)
            if t:
                parts.append(t)
    # Ensure each block ends with terminal punctuation so pysbd splits cleanly.
    norm = []
    for p in parts:
        p = p.strip()
        if p and p[-1] not in ".!?":
            p = p + "."
        norm.append(p)
    return _clean_text(" ".join(norm))


def _title(root: ET.Element) -> str | None:
    for el in root.iter():
        if _strip_ns(el.tag) == "article-title":
            return _itertext_clean(el)
    return None


def _walk_section(sec: ET.Element, inherited_title: str):
    """Yield (section_label, section_title, paragraph_text) for a <sec>,
    recursing into nested <sec> and pulling <p>/<title> text.

    `section_label` is the short, canonicalized locator (`_short_section`);
    `section_title` is the untouched JATS title, so a nested heading's full
    wording is still available to callers. Figures/tables handled separately.
    """
    # Section title (first direct <title> child)
    title = inherited_title
    for child in sec:
        if _strip_ns(child.tag) == "title":
            t = _itertext_clean(child)
            if t:
                title = t
            break

    # Skip on the *raw* title: it carries more to match against than the label.
    if _SKIP_SEC.match(title or ""):
        return  # skip references/acknowledgements/etc entirely

    label = _short_section(title)
    for child in sec:
        tag = _strip_ns(child.tag)
        if tag == "sec":
            yield from _walk_section(child, title)
        elif tag in ("p", "list", "statement"):
            txt = _itertext_clean(child)
            if txt:
                yield (label, title, txt)
        # <fig>/<table-wrap> captions collected in _collect_figures, not here


def _collect_figures(body: ET.Element) -> list[dict[str, Any]]:
    figs: list[dict[str, Any]] = []
    counter = 0
    for el in body.iter():
        tag = _strip_ns(el.tag)
        if tag not in ("fig", "table-wrap"):
            continue
        counter += 1
        label = ""
        caption = ""
        for child in el.iter():
            ct = _strip_ns(child.tag)
            if ct == "label" and not label:
                label = _itertext_clean(child)
            if ct == "caption" and not caption:
                caption = _itertext_clean(child)
        fid = label.replace(" ", "").replace(".", "") or f"{tag}{counter}"
        figs.append({
            "figure_id": fid,
            "label": label or f"{tag} {counter}",
            "page": None,
            "caption": caption,
            "caption_bbox": None,
            "figure_bbox": None,
            "image_path": None,
        })
    return figs


# Join key for matching a JATS figure label to a PDF caption label.
# "Figure 3." / "Fig 3" / "FIG. 3a" / "Table 1" all reduce to fig3 / fig3a / table1.
_LABEL_KEY_RE = re.compile(r"\b(fig(?:ure)?|table)\.?\s*(\d+)\s*([a-z]?)\b",
                           re.IGNORECASE)


def _label_keys(label: str) -> list[str]:
    """Canonical match keys for a figure label, most specific first.

    A JATS label of "Figure 3" and a PDF caption of "Fig. 3a" must still join,
    so a panel-suffixed label also yields its unsuffixed key.
    """
    m = _LABEL_KEY_RE.search(label or "")
    if not m:
        return []
    kind = "table" if m.group(1).lower().startswith("table") else "fig"
    base = f"{kind}{int(m.group(2))}"
    suffix = m.group(3).lower()
    return [base + suffix, base] if suffix else [base]


def _attach_pdf_crops(
    figures: list[dict[str, Any]],
    figures_pdf: str | pathlib.Path,
    figures_dir: str | pathlib.Path,
    paper_id: str,
    figure_dpi: int = 200,
) -> int:
    """Crop figure images out of a supplementary PDF onto JATS figures.

    Best-effort by design: the PDF is an optional extra obtained after the XML
    already won, so any failure here (missing PDF stack, unreadable file, no
    label match) must leave the caption-only figures exactly as they were.
    Mutates `figures` in place and returns how many gained an image.
    """
    try:
        # Heavy, optional deps (pdfplumber / pypdfium2 / PIL / numpy) — the XML
        # path must stay importable without them.
        import parse_pdf  # noqa: PLC0415 - deliberately lazy
    except Exception:
        return 0
    if not pathlib.Path(figures_pdf).exists():
        return 0
    try:
        parsed = parse_pdf.parse_pdf_pypdfium2(
            figures_pdf, paper_id, figures_dir, figure_dpi=figure_dpi,
        )
    except Exception:
        return 0

    by_key: dict[str, dict[str, Any]] = {}
    for pf in parsed.get("figures", []) or []:
        if not pf.get("image_path"):
            continue  # caption detected but nothing croppable — no use here
        for k in _label_keys(pf.get("label") or ""):
            by_key.setdefault(k, pf)

    n_matched = 0
    used: set[str] = set()
    for fig in figures:
        if fig.get("image_path"):
            continue
        pf = next((by_key[k] for k in _label_keys(fig.get("label") or "")
                   if k in by_key), None)
        if pf is None or pf["image_path"] in used:
            continue  # never hand the same crop to two different captions
        fig["image_path"] = pf["image_path"]
        fig["page"] = pf.get("page")
        fig["figure_bbox"] = pf.get("figure_bbox")
        fig["caption_bbox"] = pf.get("caption_bbox")
        fig["image_source"] = "figures_pdf"
        used.add(pf["image_path"])
        n_matched += 1
    return n_matched


def parse_jats_xml(
    xml_path: str,
    paper_id: str,
    include_abstract: bool = True,
    figures_pdf: str | pathlib.Path | None = None,
    figures_dir: str | pathlib.Path | None = None,
    figure_dpi: int = 200,
) -> dict[str, Any]:
    """Parse a JATS/PMC full-text XML file into the parse_pdf output contract.

    `figures_pdf` (with `figures_dir`) is the optional supplementary PDF from
    `acquire.acquire_pdf` — see the module docstring. It only ever adds figure
    images; the text always comes from the XML.
    """
    with open(xml_path, "rb") as f:
        raw = f.read()
    # Tolerate stray encoding / DOCTYPE issues.
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # Retry after stripping a leading BOM / xml declaration quirks.
        txt = raw.decode("utf-8", "ignore")
        root = ET.fromstring(txt)

    sentences: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    # Short section label -> the full JATS title it was derived from, in first-
    # seen order. Two nested headings can share a label (both "Results"); the
    # first title wins, which is the one the reader recognises.
    seen_sections: dict[str, str] = {}
    sid = 0

    def _emit(section: str, text: str):
        nonlocal sid
        for s in _segment(text):
            s_clean = _clean_text(s)
            if len(s_clean) < 5:
                continue
            sentences.append({
                "sentence_id": sid,
                "text": s_clean,
                "section": section,
                "page": None,
                "bbox": None,
            })
            sid += 1

    # Abstract first (mirrors the abstract-as-section idea; section="Abstract").
    if include_abstract:
        abs_txt = _find_abstract(root)
        if abs_txt:
            seen_sections.setdefault("Abstract", "Abstract")
            _emit("Abstract", abs_txt)

    body = _find_body(root)
    if body is not None:
        # Top-level <sec> children; if body has loose <p>, treat as "Body".
        top_secs = [c for c in body if _strip_ns(c.tag) == "sec"]
        if top_secs:
            for sec in top_secs:
                for label, full_title, para in _walk_section(sec, "Body"):
                    seen_sections.setdefault(label, full_title)
                    _emit(label, para)
        else:
            for c in body:
                if _strip_ns(c.tag) in ("p", "list", "statement"):
                    txt = _itertext_clean(c)
                    if txt:
                        seen_sections.setdefault("Body", "Body")
                        _emit("Body", txt)
        figures = _collect_figures(body)
    else:
        figures = []

    sections = [{"title": t, "full_title": full,
                 "page_start": None, "page_end": None}
                for t, full in seen_sections.items()]

    # Optional: recover real figure crops from the supplementary PDF. Never
    # affects the text, the sections, or the success of this parse.
    n_images = 0
    if figures_pdf and figures_dir and figures:
        n_images = _attach_pdf_crops(figures, figures_pdf, figures_dir,
                                     paper_id, figure_dpi)

    return {
        "paper_id": paper_id,
        "parser": "jats-xml",
        "n_pages": None,
        "sections": sections,
        "sentences": sentences,
        "figures": figures,
        "title": _title(root),
        # Provenance for honest downstream reporting: how many of these
        # captions actually carry an image, and where those images came from.
        "figures_pdf": str(figures_pdf) if figures_pdf else None,
        "n_figure_images": n_images,
    }


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("xml_path")
    ap.add_argument("--paper-id", default="test")
    ap.add_argument("--figures-pdf", default=None,
                    help="Supplementary open PDF to crop figure images from "
                         "(acquire.py's record['figures_pdf']).")
    ap.add_argument("--figures-dir", default="./_figures")
    args = ap.parse_args()
    res = parse_jats_xml(args.xml_path, args.paper_id,
                         figures_pdf=args.figures_pdf,
                         figures_dir=args.figures_dir)
    print(json.dumps({
        "paper_id": res["paper_id"],
        "parser": res["parser"],
        "n_sentences": len(res["sentences"]),
        "n_figures": len(res["figures"]),
        "n_figure_images": res["n_figure_images"],
        "n_sections": len(res["sections"]),
        "sections": [s["title"] for s in res["sections"]],
        "sample_sentence": res["sentences"][0] if res["sentences"] else None,
    }, indent=2))
