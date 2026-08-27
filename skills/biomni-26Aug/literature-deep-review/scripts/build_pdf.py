#!/usr/bin/env python3
"""Render the report PDF deterministically from the canonical report model.

Structure and content live here; visual styling comes from the
``pdf-report-generation`` skill when it is importable, and falls back to the
documented Phylo constants below when it is not. That split is deliberate: the
skill owns branding, but the *shape* of the report must not be re-improvised
per run, because that is exactly what let one run ship five paper figures and
the next ship one.

Sections follow ``templates/report_contract.json`` and are validated by
``verify_report_contract.py``:

    Title · Summary (visual abstract + evidence-axis synthesis table) ·
    Introduction · Methods · Results (per-claim narrative + quotes + real
    figure crops) · Conclusions · Figures (+ synthesis panel) · Next steps ·
    Corpus accountability · References

The prose sections come from ``deliverables/report_sections.json``, the same
artifact ``build_review.py`` reads, so the two deliverables cannot be different
documents. The four ``--*-file`` arguments still work and are used as a fallback
per section when that file does not supply one.

Under each central claim, Results separates the five narrative facets from
``deliverables/claim_narratives.jsonl`` — observed result (anchored by the
verbatim quote), authors' interpretation, reviewer inference, contradiction,
evidence gap. Reviewer inference is rendered in its own labelled style; it is
never allowed to look like something a source said.

    python build_pdf.py --root "$RUN" \
        --out "$RUN/deliverables/report.pdf"
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Any

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from quote_display import (  # noqa: E402
    anchor_quote_for_display, anchor_text_unclean, caption_for_display,
)
from infographic_spec import image_failure, image_path  # noqa: E402
import report_style  # noqa: E402
from report_model import (  # noqa: E402
    FIGURE_CAPTION_MAX_CHARS,
    INFERENCE_LABEL,
    NARRATIVE_FACET_LABEL,
    SECTION_KEYS,
    SECTION_TITLE,
    build_model,
    figure_caption_prefix,
    load_contract,
    searched_through,
    section_placeholder,
    readable_figure_locator,)
from synthesis_panel import (  # noqa: E402
    assert_panel_matches_claims, panel_caption, render_panel,
)

# Report styling — palette, fonts, page geometry and the header/footer canvas
# routine — comes entirely from report_style.py, which mirrors the platform
# pdf-report-generation skill. No brand palette or hex literal lives here.

# Tallest an embedded paper figure may be, in inches. Two thirds of the text
# block, so a dense multi-panel figure is legible and its caption still fits on
# the same page.
FIGURE_MAX_HEIGHT_IN = 6.2




def _esc(text: Any, preferred_font: str | None = None) -> str:
    """Normalize and XML-escape a string for the page.

    Normalization runs on EVERY string reaching the page, quotes included: it
    only collapses characters with an exact meaning-preserving equivalent
    (presentation ligatures, non-breaking spaces, soft hyphens). Meaningful
    glyphs (β, ε4, Greek, en dashes) are left intact for the DejaVu Unicode body
    font to render. ``preferred_font`` is accepted for call-site compatibility
    and ignored; font selection now lives in report_style.
    """
    from xml.sax.saxutils import escape
    return escape(report_style.normalize(str(text if text is not None else "")))


# Markdown emphasis in AUTHORED prose. `**bold**`, `*italic*` and `` `code` ``
# are how a reviewer writes emphasis, review.md renders them, and the PDF
# escaped them — so a shipped report printed
#
#   "This review asks a focused question: **how strong, and of what kind, ...**"
#
# with the asterisks visible, in the Introduction and in five of six Conclusions
# paragraphs. Applied AFTER escaping, so the delimiters are matched against text
# whose real angle brackets are already entities and cannot be confused with the
# tags being inserted.
_MD_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
_MD_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])")
_MD_CODE = re.compile(r"`(?=\S)([^`]+?)(?<=\S)`")


def _esc_prose(text: Any) -> str:
    """``_esc`` plus Markdown emphasis, for reviewer-authored prose only.

    Deliberately NOT used for quotes. A verbatim sentence containing an asterisk
    (a significance marker: "*P<0.05, **P<0.01") must render its asterisks as
    asterisks, and reinterpreting them as emphasis would silently alter quoted
    text — the one thing this skill may not do.
    """
    out = _esc(text)
    out = _MD_BOLD.sub(r"<b>\1</b>", out)
    out = _MD_ITALIC.sub(r"<i>\1</i>", out)
    return _MD_CODE.sub(r'<font face="{}">\1</font>'.format(
        report_style.register().mono), out)


def _attr(value: Any) -> str:
    """Escape a value for use as an XML ATTRIBUTE — quotes included.

    ``saxutils.escape`` only handles ``& < >``. A scraped URL containing a
    double quote (``https://ex.org/a"onmouseover="x``) therefore closed the
    href early and reportlab's paraparser raised ``ValueError: syntax error``,
    killing the entire build over one malformed link. ``quoteattr`` picks and
    emits the surrounding quotes itself, so callers must NOT add their own.
    """
    from xml.sax.saxutils import quoteattr
    return quoteattr(str(value if value is not None else ""))


def _link(text: str, url: str) -> str:
    """Clickable blue anchor. Every citation and reference must use this."""
    if not url:
        return _esc(text)
    return (f'<a href={_attr(url)}><font color="{report_style.LINK}">'
            f'{_esc(text)}</font></a>')


def _statement_markup(statement: dict, inference_label: str, *,
                      heading: str = "", number: int | None = None,
                      suppress_inference_marker: bool = False,
                      evidence_citations: dict | None = None) -> str:
    """One narrative statement as reportlab markup, attribution included.

    The sources a statement rests on are PRINTED. A reader who doubts a sentence
    should be able to go straight to them, and a sentence that cites nothing has
    to say so — that is the whole separation the report promises.

    What is printed is the citation, not the row id. The shipped reports emitted
    ``[E-648e8fe191f72194, E-e1f59bcaeec16378]`` into running prose, which
    attributes a sentence to something no reader can look up. The ids still
    validate the statement against evidence.jsonl in ``report_model``; here they
    resolve to "Ward et al. 2024 [1]".
    """
    markup = f"{number}. " if number else ""
    if heading:
        markup += f"<b>{_esc(heading)}.</b> "
    # Authored prose, so Markdown emphasis is honoured. Quotes go through
    # ``_esc`` in ``_anchor_flowables`` and keep their asterisks literal.
    markup += _esc_prose(statement["text"])
    cites = _citation_markup(statement["evidence_ids"], evidence_citations)
    if cites:
        markup += f' <font color="{report_style.MUTED}" size="7.5">[{cites}]</font>'
    if statement["inference"] and not suppress_inference_marker:
        markup += (f' <font color="{report_style.MUTED}" size="7.5"><i>'
                   f'[{_esc(inference_label)}]</i></font>')
    if statement.get("evidence_qualification") == "secondary/indirect":
        markup += (f' <font color="{report_style.MUTED}" size="7.5"><i>'
                   '[secondary/indirect evidence]</i></font>')
    if statement.get("no_qualifying_anchor"):
        markup += (f' <font color="{report_style.MUTED}" size="7.5"><i>'
                   '[no qualifying verbatim anchor retained]</i></font>')
    return markup


def _citation_markup(evidence_ids: list[str],
                     evidence_citations: dict | None) -> str:
    """Cited evidence rows as a de-duplicated, hyperlinked citation list.

    Several rows commonly come from one paper — three quotes from Ward 2024
    grounding one sentence — and repeating the citation three times says
    nothing extra, so identical citations collapse to one.
    """
    if not evidence_ids:
        return ""
    lookup = evidence_citations or {}
    seen: list[str] = []
    for eid in evidence_ids:
        entry = lookup.get(eid)
        if not entry or not entry.get("citation"):
            # An id with no resolvable source still has to appear: dropping it
            # would silently turn a cited sentence into an uncited one.
            rendered = _esc(eid)
        else:
            index = entry.get("reference_index")
            label = (f"{entry['citation']} [{index}]" if index
                     else entry["citation"])
            rendered = _link(label, str(entry.get("url") or ""))
        if rendered not in seen:
            seen.append(rendered)
    return ", ".join(seen)


def _facet_flowables(narrative: dict, key: str, styles,
                     inference_label: str,
                     evidence_citations: dict | None = None) -> list:
    """A labelled narrative facet, or nothing when the run authored none."""
    from reportlab.platypus import Paragraph

    statement = (narrative or {}).get(key)
    if not statement:
        return []
    is_inference = statement["inference"] or key == "reviewer_inference"
    return [Paragraph(
        _statement_markup(
            statement, inference_label, heading=NARRATIVE_FACET_LABEL[key],
            # The heading already reads "Reviewer inference".
            suppress_inference_marker=(key == "reviewer_inference"),
            evidence_citations=evidence_citations),
        styles["Inference"] if is_inference else styles["Facet"])]


def _anchor_flowables(anchors: list[dict], styles) -> list:
    """The verbatim quotes and their locators. Never a paraphrase.

    Rendered under the observed result (and under the contradiction) rather
    than alone: the quote is what ANCHORS the stated finding, and a reader must
    be able to see the finding and the exact sentence it came from together.

    The attribution line reads as a citation, not as machine provenance. It
    used to be::

        10.1002/trc2.12452:S:0 · Abstract · supports/primary · 10.1002/trc2.12452

    — an internal block id, then the section, then the stance, then the same
    DOI a second time, with nothing a reader recognises as a source. Now::

        Ward et al. 2024 [1], Alz Dement TRCI · Abstract, p. 1 · supports/primary

    with the author-year hyperlinked to the DOI and ``[1]`` pointing into the
    reference list. The block id keeps travelling in evidence.jsonl and
    grounded_quotes.json, where a machine reads it.
    """
    from reportlab.platypus import Paragraph

    out: list = []
    for anchor in anchors:
        if anchor_text_unclean(anchor):
            continue
        out.append(Paragraph(
            f"“{_esc(anchor_quote_for_display(anchor, FIGURE_CAPTION_MAX_CHARS))}”",
            styles["Quote"]))
        out.append(Paragraph(_attribution_markup(anchor), styles["Locator"]))
    return out


def _attribution_markup(anchor: dict) -> str:
    """The citation + locator + stance line rendered under one quote."""
    cite = str(anchor.get("citation") or "").strip()
    index = anchor.get("reference_index")
    if not cite:
        # No usable author/year metadata. The DOI is a worse citation than an
        # author-year but a better one than nothing, so it stands in rather
        # than leaving the quote unattributed.
        cite = str(anchor.get("doi") or anchor.get("paper_id") or "source")
    label = f"{cite} [{index}]" if index else cite
    head = _link(label, str(anchor.get("url") or ""))
    journal = str(anchor.get("journal") or "").strip()
    if journal:
        head += f", {_esc(journal)}"
    parts = [head]
    locator = _locator_text(anchor)
    if locator:
        parts.append(_esc(locator))
    taxonomy = [
        str(anchor.get("publication_type") or "").replace("_", " "),
        str(anchor.get("anchor_depth") or "").replace("_", " "),
        str(anchor.get("claim_relationship") or "").replace("_", " "),
        str(anchor.get("stance") or ""),
    ]
    if any(taxonomy):
        parts.append(_esc(" · ".join(value for value in taxonomy if value)))
    return " · ".join(parts)


def _locator_text(anchor: dict) -> str:
    """"page 1 · Abstract" -> "Abstract, p. 1".

    The stored locator leads with the page because that is how the parser
    builds it, which puts the least identifying part first and repeats the
    separator the attribution line already uses. Section first, page as a
    suffix, reads the way a citation does.
    """
    raw = str(anchor.get("source_locator") or "").strip()
    if not raw:
        return ""
    page = ""
    sections: list[str] = []
    for part in (p.strip() for p in raw.split("·")):
        if not part:
            continue
        match = re.fullmatch(r"pages?\s*([0-9ivxlcIVXLC]+)", part, re.IGNORECASE)
        if match:
            page = match.group(1)
        else:
            readable = readable_figure_locator(part)
            if readable:
                sections.append(readable)
    body = ", ".join(sections)
    if page:
        return f"{body}, p. {page}" if body else f"p. {page}"
    return body


def _section_flowables(model: dict, key: str, fallback, styles) -> list:
    """A prose section, from report_sections.json or the legacy ``--*-file``.

    The structured artifact wins when it supplies the section; the loose file is
    the fallback so runs written against the old CLI keep building. A section
    with neither prints an explicit not-supplied note rather than a blank —
    an empty heading is indistinguishable from a builder that dropped it.
    """
    from reportlab.platypus import Paragraph

    label = model.get("inference_label") or INFERENCE_LABEL
    statements = (model.get("sections") or {}).get(key) or []
    if statements:
        return [Paragraph(
            _statement_markup(statement, label,
                              number=i if key == "next_steps" else None,
                              evidence_citations=model.get("evidence_citations")),
            styles["Inference"] if statement["inference"] else styles["Body"])
            for i, statement in enumerate(statements, 1)]
    if isinstance(fallback, (list, tuple)):
        items = [str(s).strip() for s in fallback if str(s).strip()]
        if items:
            return [Paragraph(f"{i}. {_esc(s)}", styles["Body"])
                    for i, s in enumerate(items, 1)]
    elif str(fallback or "").strip():
        return [Paragraph(_esc(str(fallback).strip()), styles["Body"])]
    return [Paragraph(_esc(section_placeholder(key)), styles["Body"])]


def _image_flowable(path: pathlib.Path, max_w: float, max_h: float):
    from reportlab.platypus import Image as RLImage
    from PIL import Image as PILImage

    with PILImage.open(path) as im:
        im.verify()
    with PILImage.open(path) as im:
        w, h = im.size
    if not w or not h:
        raise ValueError(f"image has zero extent: {path}")
    scale = min(max_w / w, max_h / h, 1.0)
    return RLImage(str(path), width=w * scale, height=h * scale)


def _safe_image_flowable(path: pathlib.Path, max_w: float, max_h: float):
    """``_image_flowable`` that returns None instead of aborting the build.

    Existence was checked but validity never was, so a zero-byte or truncated
    PNG raised ``PIL.UnidentifiedImageError`` out of the whole run. A single bad
    crop must not cost the report; it is skipped with a warning and the figure
    gates then fail on the resulting shortfall, which is the correct outcome.
    """
    try:
        return _image_flowable(path, max_w, max_h)
    except Exception as exc:  # noqa: BLE001 - any decode failure is a skip
        print(f"WARN: skipping unreadable image {path}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def _doc_template(out_path: pathlib.Path, model: dict, body_font: str):
    """A document template that feeds its headings to the table of contents.

    reportlab builds a TOC by having the template call ``notify('TOCEntry', ...)``
    as each flowable is laid out — there is no way to know a heading's page
    before the layout that places it. Headings are tagged with ``_toc_level``
    when they are created; anything untagged is ignored.
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    class _Doc(BaseDocTemplate):
        def afterFlowable(self, flowable):  # noqa: N802 - reportlab's spelling
            level = getattr(flowable, "_toc_level", None)
            if level is None:
                return
            text = flowable.getPlainText()
            self.notify("TOCEntry", (level, text, self.page))

    doc = _Doc(
        str(out_path), pagesize=LETTER,
        leftMargin=report_style.LEFT_MARGIN,
        rightMargin=report_style.RIGHT_MARGIN,
        topMargin=report_style.TOP_MARGIN,
        bottomMargin=report_style.BOTTOM_MARGIN,
        title=model["title"], author="Biomni", invariant=1,
        # The canvas declares its initial font in every page's resource
        # dictionary whether or not a glyph is ever drawn with it, so a document
        # typeset entirely in embedded custom fonts still listed a non-embedded
        # Helvetica under
        # ``pdffonts``. That listing is how a reviewer checks for this whole
        # class of defect, so it has to be true.
        initialFontName=body_font,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=report_style.make_header_footer(model["title"]))])
    return doc


def _toc_flowables(styles, body_font: str) -> list:
    """The Contents block. Sections and evidence axes, with page numbers.

    Worth the two layout passes now that a report's length follows its evidence
    rather than a page budget: the shipped 15-page reports were already hard to
    navigate, and the Results section is the bulk of a longer one.
    """
    from reportlab.platypus import Paragraph, tableofcontents

    from reportlab.platypus import TableStyle

    toc = tableofcontents.TableOfContents()
    # TableOfContents lays itself out as a Table, and Table declares its default
    # FONTNAME (Helvetica) as a page resource whether or not a glyph is drawn
    # with it — the same way the synthesis table did. Left alone it puts a
    # non-embedded base-14 font back into an otherwise fully embedded document.
    toc.tableStyle = TableStyle(list(toc.tableStyle.getCommands()) + [
        ("FONTNAME", (0, 0), (-1, -1), body_font),
    ])
    toc.levelStyles = [styles["TOC0"], styles["TOC1"]]
    return [Paragraph("Contents", styles["H1"]), toc]


def _heading(text: str, styles, level: int | None = None, style: str = "H1"):
    """A heading paragraph, optionally tagged for the table of contents."""
    from reportlab.platypus import Paragraph

    para = Paragraph(_esc(text), styles[style])
    if level is not None:
        para._toc_level = level
    return para




def _sources_markup(sources: list[dict], kind: str = "primary") -> str:
    """Table 1's Sources cell: hyperlinked author-year citations.

    Every other citation in the report resolves to its DOI; this cell rendered
    the same author-year strings as dead text, so the reader's map of the whole
    review was the one place they could not follow a source.

    ``kind`` says whether these are the primary studies or the secondary
    statements the axis falls back to. The distinction has to survive into the
    cell: naming a review article beside a tier that says "primary" would trade
    one misreading for a worse one.
    """
    if not sources:
        return "—"
    parts = []
    for source in sources:
        index = source.get("reference_index")
        label = f"{source['citation']} [{index}]" if index else source["citation"]
        parts.append(_link(label, str(source.get("url") or "")))
    joined = ", ".join(parts)
    if kind == "secondary":
        joined += (f'<br/><font size="7" color="{report_style.MUTED}">'
                  'secondary / framing</font>')
    return joined


def _synthesis_table_flowable(model: dict, styles, avail_w: float):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    head = ["Evidence axis", "Bottom line", "Strongest support", "Sources"]
    data = [[Paragraph(f"<b>{_esc(h)}</b>", styles["CellHead"]) for h in head]]
    for row in model["synthesis_table"]:
        # The tier is the strongest reached ANYWHERE on the axis, over n claims;
        # the sources are the papers behind that claim. Saying so stops the cell
        # reading as a contradiction when "One primary study" sits
        # next to two named papers.
        tier = _esc(row["support_label"])
        if row.get("n_claims", 0) > 1:
            tier += (f'<br/><font color="{report_style.MUTED}" size="7">strongest of '
                     f'{row["n_claims"]} claims on this axis</font>')
        data.append([
            Paragraph(_esc(row.get("axis_label") or row["axis"]), styles["Cell"]),
            Paragraph(_esc(row["bottom_line"]), styles["Cell"]),
            Paragraph(tier, styles["Cell"]),
            Paragraph(_sources_markup(row["sources"],
                                      row.get("sources_kind") or "primary"),
                      styles["Cell"]),
        ])
    # The axis column was 0.19 and broke "biomarker_engagement" mid-word across
    # two lines. English axis labels wrap at spaces, and the extra width keeps
    # the longest of them on two lines rather than three.
    widths = [avail_w * f for f in (0.22, 0.34, 0.23, 0.21)]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        # Every cell is a Paragraph with its own font, but Table still declares
        # its default FONTNAME (Helvetica) as a page resource — which is the
        # last thing putting a non-embedded base-14 font into an otherwise
        # fully embedded document.
        ("FONTNAME", (0, 0), (-1, -1), report_style.register().body),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(report_style.GOLD)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(report_style.RULE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor(report_style.TABLE_ALT_ROW)]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _paper_accountability_table(model: dict, styles, avail_w: float):
    """One auditable disposition row for every selected paper."""
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    head = ["Selected paper", "Retrieval", "Parse", "Evidence", "Report use"]
    data = [[Paragraph(f"<b>{_esc(value)}</b>", styles["CellHead"])
             for value in head]]
    for row in model.get("paper_accountability") or []:
        title = str(row.get("title") or row.get("paper_id") or "")
        retrieval = (
            "retrieved" if row.get("retrieved")
            else str(row.get("retrieval_kind") or "not attempted").replace("_", " ")
        )
        parse = str(row.get("parse_quality") or "—").replace("_", " ")
        evidence = (
            f"{int(row.get('accepted_evidence_count') or 0)} accepted; "
            f"{int(row.get('rejected_adjudication_count') or 0)} rejected"
        )
        report_use = (
            ("cited" if row.get("cited") else "consulted, not cited")
            + f"; {int(row.get('exported_figure_count') or 0)} figure(s)"
        )
        data.append([
            Paragraph(_esc(title), styles["Cell"]),
            Paragraph(_esc(retrieval), styles["Cell"]),
            Paragraph(_esc(parse), styles["Cell"]),
            Paragraph(_esc(evidence), styles["Cell"]),
            Paragraph(_esc(report_use), styles["Cell"]),
        ])
    table = Table(
        data,
        colWidths=[avail_w * value for value in (0.40, 0.13, 0.13, 0.17, 0.17)],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), report_style.register().body),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(report_style.GOLD)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor(report_style.RULE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor(report_style.TABLE_ALT_ROW)]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _probe_image(path: pathlib.Path) -> bool:
    """Can this image actually be embedded? Decides the figure list up front."""
    return _safe_image_flowable(path, 100.0, 100.0) is not None


def _resolve_infographic(root: pathlib.Path, explicit: pathlib.Path | None,
                         model: dict) -> pathlib.Path | None:
    """The infographic image, from the CLI flag or the contract's path.

    ``--visual-abstract`` still works; the contract's ``image`` is the default so
    a run does not have to pass a path it already declared.
    """
    if explicit and explicit.exists():
        return explicit
    spec = (model.get("contract") or {}).get("visual_abstract") or {}
    declared = str(spec.get("image") or "deliverables/infographic.png")
    candidate = root / declared
    if candidate.exists():
        return candidate
    legacy = root / "deliverables" / "visual_abstract.png"
    return legacy if legacy.exists() else None


def _infographic_caption(root: pathlib.Path, model: dict) -> str:
    """The caption under the infographic, carrying its contract marker.

    Says which profile was used and that the spec was verified, because the
    claim a reader most needs about a summary graphic is whether its numbers are
    the report's own. The marker word ("Infographic") is what the contract gate
    matches on.
    """
    spec_path = root / "deliverables" / "infographic_spec.json"
    profile, verified = "", False
    try:
        import json as _json
        spec = _json.loads(spec_path.read_text(encoding="utf-8"))
        profile = str(spec.get("PROFILE") or "")
        verified = bool(spec.get("_verified"))
    except Exception:  # noqa: BLE001 - an absent spec is reported, not fatal
        pass
    caption = ("Infographic. Three-panel summary of the review: A, the system "
               "under study; B, what the evidence shows; C, what follows.")
    if profile == "target":
        caption = ("Infographic. Three-panel summary of the review: A, target "
                   "biology; B, how the target drives the disease; C, the "
                   "therapeutic rationale.")
    caption += (" Schematic; every value shown is drawn from this review's "
                "accepted evidence and appears with its quote in Results.")
    if not verified:
        caption += (" (Spec not verified against the evidence for this build — "
                    "run scripts/infographic_spec.py --verify.)")
    return caption


def _figure_flowables(fig: dict, figures_dir: pathlib.Path, styles,
                      avail_w: float, embedded_numbers: set[int],
                      prefix: str = "Figure") -> list:
    """One embedded paper figure with its caption, kept on one page.

    Sizing changed from ``0.82 * avail_w`` by ``3.4in``. That box rendered a
    three-panel ROC figure with its sensitivity/specificity tables at a size
    where none of the numbers could be read — in a 10 MB file whose source crop
    had the resolution to spare. Figures now take the full text width and up to
    two thirds of the page height, which is what a multi-panel biomedical
    figure needs to be worth reproducing at all.
    """
    from reportlab.platypus import KeepTogether, Paragraph

    flow = _safe_image_flowable(_figure_path(figures_dir, fig),
                                avail_w, FIGURE_MAX_HEIGHT_IN * 72)
    if flow is None:
        # Unreadable on the second pass: drop the LABEL too, never a caption
        # without its crop.
        embedded_numbers.discard(fig["report_number"])
        return []
    caption = caption_for_display(fig.get("caption") or "",
                                  max_chars=FIGURE_CAPTION_MAX_CHARS)
    src = f" {_link('full caption at source', fig['url'])}" if fig.get("url") else ""
    grounds = ", ".join(fig.get("claim_display_ids") or fig.get("claims") or [])
    cap = (f"{prefix} {fig['report_number']}. {_esc(fig['citation'])}, "
           f"{_esc(fig['label'])}")
    role = str(fig.get("role") or "primary_data")
    role_label = {
        "primary_data": "primary-data figure",
        "source_model": "source mechanism/model; illustrative only",
        "review_context": "review/context figure; illustrative only",
    }.get(role, role.replace("_", " "))
    if grounds:
        verb = "grounds" if role == "primary_data" else "illustrates"
        cap += f" — {verb} {_esc(grounds)}"
    cap += f" — {_esc(role_label)}."
    # Why THIS figure, out of the paper's dozen. Boxes drawn on the image are
    # only half the answer; the caption names the caption terms that scored and
    # the in-figure text that was boxed, so the choice is auditable from the page.
    note = str(fig.get("provenance_note") or "").strip()
    if note:
        cap += f" <i>{_esc(note)}</i>"
    if caption:
        cap += f" Source caption: “{_esc(caption)}”"
    rights_notice = str(fig.get("rights_notice") or "").strip()
    if rights_notice:
        cap += f' <b>Rights notice:</b> {_esc(rights_notice)}'
    return [KeepTogether([flow, Paragraph(cap + src, styles["Caption"])])]


def _figure_path(figures_dir: pathlib.Path, fig: dict) -> pathlib.Path:
    stored = fig.get("image_path")
    if stored:
        return pathlib.Path(stored)
    return figures_dir / str(fig.get("image") or "")


def build(root: pathlib.Path, out_path: pathlib.Path,
          visual_abstract: pathlib.Path | None,
          intro: str = "", methods: str = "", conclusions: str = "",
          next_steps: list[str] | None = None,
          contract: dict | None = None) -> dict:
    import reportlab.rl_config as rl_config
    from reportlab.lib.units import inch
    from reportlab.platypus import PageBreak, Paragraph

    # Byte-for-byte determinism. Without this reportlab stamps a wall-clock
    # /CreationDate and a random /ID, so two builds of the same run produced
    # different files and "rerun it and diff" was not a usable check. The
    # docstring promised determinism long before it was true.
    rl_config.invariant = 1

    from reconcile_run import refresh as reconcile

    _receipt, reconciliation_failures = reconcile(root, write=True)
    if reconciliation_failures:
        raise SystemExit(
            "final reconciliation: " + "; ".join(reconciliation_failures)
        )

    fonts = report_style.register()

    model = build_model(root, contract if contract is not None else load_contract())
    drift = (assert_panel_matches_claims(model)
             + list(model.get("stale_derived") or []))
    if drift:
        raise SystemExit("panel/claim drift: " + "; ".join(drift))
    if model.get("reference_errors"):
        raise SystemExit("reference list: " + "; ".join(model["reference_errors"]))
    # Same abort as the Markdown builder, on the same list: an unattributable
    # statement, a citation to an evidence row that does not exist, or a central
    # claim with no stated observed result.
    if model.get("narrative_errors"):
        raise SystemExit("narrative: " + "; ".join(model["narrative_errors"]))
    if model.get("coverage_errors"):
        raise SystemExit("coverage: " + "; ".join(model["coverage_errors"]))
    # Font names are resolved (and DejaVu registered) before anything is
    # measured, so the stylesheet and the page chrome agree on the family.
    print(report_style.describe(), file=sys.stderr)
    for missing in model.get("figures_missing") or []:
        print(f"WARN: manifest figure {missing.get('paper_id')}/"
              f"{missing.get('figure_id')} claims status=exported but its image "
              f"is not on disk ({missing.get('resolved') or missing.get('image')})"
              " — it is excluded from BOTH deliverables", file=sys.stderr)

    styles = report_style.build_styles()
    figure_prefix = figure_caption_prefix(model.get("contract"))
    deliverables = root / "deliverables"
    figures_dir = deliverables / "figures_cited"
    panel_path = render_panel(model, deliverables / "synthesis_panel.png")

    # Which paper figures will actually be EMBEDDED, decided before a word is
    # written so the header count, the Results crops and the Figures list are
    # one list. Previously a figure whose image was unusable was skipped in
    # Results but still printed its bullet in the Figures list — and because
    # both gates count "Report Figure N" LABELS, a report embedding zero paper
    # figures passed with paper_figures=5/4.
    embedded_numbers: set[int] = set()
    for claim in model["claims"]:
        for fig in claim["figures"]:
            num = fig["report_number"]
            if num not in embedded_numbers and \
                    _probe_image(_figure_path(figures_dir, fig)):
                embedded_numbers.add(num)
    embeddable = [f for f in model["figures"]
                  if f["report_number"] in embedded_numbers]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = _doc_template(out_path, model, fonts.body)
    avail_w = doc.width

    story: list[Any] = []
    stats = model.get("stats") or {}
    n_anchors = sum(len(c["supporting"]) + len(c["contradicting"])
                    for c in model["claims"])

    # --- Title -------------------------------------------------------------
    story += [Paragraph(
        _esc(model["title"], fonts.display), styles["DocTitle"]
    )]
    if model.get("question"):
        story.append(Paragraph(_esc(model["question"]), styles["Subtitle"]))
    # "45 verbatim anchors · 6 real paper figures" was pipeline vocabulary, and
    # "real" reads as fending off an accusation. Say the same thing plainly, and
    # date the review: the PDF's own /CreationDate is pinned to a fixed epoch
    # for byte-determinism, so without this the document carries no indication
    # of when the literature was searched.
    meta_bits = [
        f"Mode: {_esc(model['mode'])}",
        f"{_esc(stats.get('papers_full_text', '?'))} retrieved full texts",
        f"{len(model['claims'])} claims",
        f"{n_anchors} verbatim quotes",
        f"{len(embeddable)} figures reproduced from source papers",
    ]
    searched = searched_through(model)
    if searched:
        meta_bits.append(f"literature searched through {_esc(searched)}")
    story.append(Paragraph(" &nbsp;·&nbsp; ".join(meta_bits), styles["Meta"]))

    # --- Infographic ---------------------------------------------------------
    # Before the Contents, not after it: this is the reader's entry point into
    # the report, and a table of contents above it would bury the one page that
    # explains what the review found. Its spec is seeded from this model and
    # verified against the evidence (scripts/infographic_spec.py), so unlike an
    # ordinary title-page graphic every value in it is quotable.
    infographic = _resolve_infographic(root, visual_abstract, model)
    infographic_required = model.get("mode") in set(
        ((model.get("contract") or {}).get("visual_abstract") or {})
        .get("required_modes") or [])
    if infographic_required and infographic is None:
        raise SystemExit(image_failure(image_path(root, model.get("contract")))
                         or "required infographic image is missing")
    if infographic_required and infographic is not None:
        failure = image_failure(infographic)
        if failure:
            raise SystemExit(failure)
    abstract_flow = (_safe_image_flowable(infographic, avail_w, 4.6 * inch)
                     if infographic and infographic.exists() else None)
    if abstract_flow is not None:
        story.append(abstract_flow)
        story.append(Paragraph(_infographic_caption(root, model),
                               styles["Caption"]))

    # --- Contents ------------------------------------------------------------
    story += _toc_flowables(styles, fonts.body)
    story.append(PageBreak())

    # --- Summary: synthesis table -------------------------------------------
    story += [_heading("Summary", styles, 0)]
    story += [_heading("Evidence-axis synthesis", styles, 1, "H2"),
              _synthesis_table_flowable(model, styles, avail_w),
              Paragraph("Table 1. Evidence-axis synthesis. Support tiers are "
                        "computed deterministically from the accepted evidence "
                        "rows.", styles["Caption"])]

    # A decision-relevant result that exists only at conference-abstract,
    # trial-registry or press-release level cannot be full-text grounded, and in
    # a shipped report that meant the single most important fact about the target
    # — the phase 3 trial missing its primary endpoint — appeared nowhere in the
    # summary, reaching the reader two thirds of the way through Conclusions as
    # an unattributed "[reviewer inference]". An honest gap in grounding must not
    # demote an important finding; it changes how the finding is LABELLED, not
    # where it sits.
    key_findings = (model.get("sections") or {}).get("key_findings") or []
    if key_findings:
        story.append(_heading(SECTION_TITLE["key_findings"], styles, 1, "H2"))
        story += _section_flowables(model, "key_findings", "", styles)

    external = (model.get("sections") or {}).get("external_findings") or []
    if external:
        story.append(_heading(SECTION_TITLE["external_findings"], styles, 1, "H2"))
        story.append(Paragraph(
            "Reported here because they bear on the conclusion, and marked as "
            "outside the grounded corpus: each is a conference abstract, trial "
            "registry entry or announcement with no retrievable full text, so no "
            "verbatim anchor exists for it.", styles["Body"]))
        story += _section_flowables(model, "external_findings", "", styles)

    # --- Introduction / Methods --------------------------------------------
    for key in ("introduction", "methods"):
        story.append(_heading(SECTION_TITLE[key], styles, 0))
        story += _section_flowables(
            model, key, {"introduction": intro, "methods": methods}[key], styles)

    if model.get("corpus_flow"):
        story.append(_heading("Corpus completeness", styles, 1, "H2"))
        flow = " → ".join(
            f"{row['state'].replace('_', ' ')} {row['count']}"
            for row in model["corpus_flow"]
        )
        story.append(Paragraph(_esc(flow) + ".", styles["Body"]))
        classifications = (
            (model.get("corpus_ledger") or {}).get("retrieval_classification") or {}
        )
        if classifications:
            detail = ", ".join(
                f"{count} {kind.replace('_', ' ')}"
                for kind, count in sorted(classifications.items())
            )
            story.append(Paragraph(
                "Unretrieved full texts: " + _esc(detail) + ".",
                styles["Body"],
            ))

    # --- Results ------------------------------------------------------------
    inference_label = model.get("inference_label") or INFERENCE_LABEL
    story.append(_heading("Results", styles, 0))
    story.append(Paragraph(
        "Each grounded claim shows at least one exact verbatim quote from "
        "the retrieved full text, its locator, and the deterministically computed "
        "support state.", styles["Body"]))
    if any(c.get("narrative") for c in model["claims"]):
        story.append(Paragraph(
            "Each central claim is separated into the observed result — "
            "anchored by the quote — the authors' interpretation, the "
            f"reviewer's inference (labelled '{_esc(inference_label)}' and "
            "attributable to no source), the contradiction or alternative "
            "explanation, and the evidence gap. Every statement names the "
            "sources it rests on.", styles["Body"]))
    cites = model.get("evidence_citations")
    # Report numbers whose image has already been embedded, so a figure grounding
    # several claims is placed once and cross-referenced thereafter.
    placed_figures: set[int] = set()
    current_axis = None
    for claim in model["claims"]:
        if claim["cluster"] != current_axis:
            current_axis = claim["cluster"]
            story.append(_heading(
                claim.get("cluster_label") or current_axis, styles, 1, "H2"))
        block: list[Any] = [Paragraph(
            f"<b>{_esc(claim.get('display_id') or claim['claim_id'], fonts.display)}."
            f"</b> {_esc(claim['claim_text'], fonts.display)}",
            styles["ClaimHead"],
        )]
        scope = f" &nbsp;|&nbsp; scope: {_esc(claim['scope'])}" if claim["scope"] else ""
        block.append(Paragraph(
            f"Support: {_esc(claim['support_label'])}{scope}", styles["Support"]))
        story += block

        # Facet order is fixed by report_model.NARRATIVE_FACETS, and the quote
        # stays the anchor of the observed result: the stated finding first,
        # then the exact sentence it came from. A claim with no authored
        # narrative renders exactly as it did before the artifact existed.
        narrative = claim.get("narrative") or {}
        story += _facet_flowables(narrative, "observed_result", styles,
                                  inference_label, cites)
        story += _anchor_flowables(claim["supporting"], styles)
        story += _facet_flowables(narrative, "authors_interpretation", styles,
                                  inference_label, cites)
        story += _facet_flowables(narrative, "reviewer_inference", styles,
                                  inference_label, cites)
        if narrative.get("contradiction"):
            story += _facet_flowables(narrative, "contradiction", styles,
                                      inference_label, cites)
        elif any(not anchor_text_unclean(a) for a in claim["contradicting"]):
            story.append(Paragraph(
                "<b>Contradicting / countervailing evidence:</b>",
                styles["Body"]))
        story += _anchor_flowables(claim["contradicting"], styles)
        story += _facet_flowables(narrative, "evidence_gap", styles,
                                  inference_label, cites)

        for fig in claim["figures"]:
            if fig["report_number"] not in embedded_numbers:
                continue
            # A figure can ground more than one claim. Embed the IMAGE once, at
            # the first claim that uses it, and cross-reference it afterwards.
            # GRN's Report Figure 1 grounds two claims and its full-resolution
            # crop was placed under both, which both duplicated a megabyte and
            # showed the reader the same numbered figure twice.
            if fig["report_number"] in placed_figures:
                story.append(Paragraph(
                    f"Also grounded by {figure_prefix} "
                    f"{fig['report_number']} ({_esc(fig['citation'])}, "
                    f"{_esc(fig['label'])}), shown above.", styles["Facet"]))
                continue
            placed_figures.add(fig["report_number"])
            story += _figure_flowables(fig, figures_dir, styles, avail_w,
                                       embedded_numbers, figure_prefix)

    # --- Conclusions --------------------------------------------------------
    story.append(_heading(SECTION_TITLE["conclusions"], styles, 0))
    story += _section_flowables(model, "conclusions", conclusions, styles)

    # --- Limitations & evidence gaps -----------------------------------------
    # The counters in `coverage_notes` were all in the run's artifacts and none
    # of them reached the delivered report: one review acquired 17 of 25 selected
    # papers, another 18 of 30 with 12 paywalled, and both mentioned it only in a
    # Methods sentence. A reader weighing a support tier has to know how much of
    # the intended corpus is missing.
    story.append(_heading(SECTION_TITLE["limitations"], styles, 0))
    authored = _section_flowables(model, "limitations", "", styles)
    notes = model.get("coverage_notes") or []
    if notes:
        story += [Paragraph(f"• {_esc(note)}", styles["Body"]) for note in notes]
        # Authored prose follows the measured facts rather than replacing them.
        if (model.get("sections") or {}).get("limitations"):
            story += authored
    else:
        story += authored

    # --- Figures + synthesis panel ------------------------------------------
    # Only figures whose crop is genuinely EMBEDDED above get a bullet. A bullet
    # is a "Report Figure N" label, and a label is what the gates count; listing
    # one for a figure that was skipped is how a report with no paper figures
    # reported paper_figures=5/4 and passed.
    embedded_figs = [f for f in embeddable
                     if f["report_number"] in embedded_numbers]
    story.append(_heading("Figures", styles, 0))
    if embedded_figs:
        n_papers = len({f["paper_id"] for f in embedded_figs})
        story.append(Paragraph(
            f"The Results section reproduces {len(embedded_figs)} figures from "
            f"{n_papers} cited papers, each shown under the claim it grounds. "
            "Every one is a crop of the published figure, not a redrawn chart.",
            styles["Body"]))
        for fig in embedded_figs:
            grounds = ", ".join(fig.get("claim_display_ids")
                                or fig.get("claims") or [])
            story.append(Paragraph(
                f"• {figure_prefix} {fig['report_number']} — "
                f"{_esc(fig['citation'])}, {_esc(fig['label'])}"
                + (f" (grounds {_esc(grounds)})" if grounds else "")
                + (f" — {_link('source', fig['url'])}" if fig.get("url") else ""),
                styles["Body"]))
    else:
        story.append(Paragraph(
            "No paper-figure crops were obtainable for the cited corpus.",
            styles["Body"]))
    panel_flow = (_safe_image_flowable(panel_path, avail_w * 0.85, 3.6 * inch)
                  if panel_path and panel_path.exists() else None)
    if panel_flow is not None:
        story.append(panel_flow)
        story.append(Paragraph(_esc(panel_caption(model)), styles["Caption"]))

    # --- Next steps ----------------------------------------------------------
    story.append(_heading(SECTION_TITLE["next_steps"], styles, 0))
    story += _section_flowables(model, "next_steps", next_steps or [], styles)

    # --- Paper-level accountability -----------------------------------------
    if model.get("paper_accountability"):
        story.append(_heading("Corpus accountability", styles, 0))
        story.append(Paragraph(
            "Every selected paper is listed below, including retrieved papers "
            "that yielded no accepted grounding evidence.", styles["Body"]))
        story.append(_paper_accountability_table(model, styles, avail_w))

    # --- References ----------------------------------------------------------
    story += [PageBreak(), _heading("References", styles, 0)]
    for ref in model["references"]:
        title = _link(ref["title"], ref["url"]) if ref["url"] else _esc(ref["title"])
        journal = f" {_esc(ref['journal'])}." if ref["journal"] else ""
        doi = f" {_link('doi:' + ref['doi'], ref['url'])}" if ref["doi"] else ""
        story.append(Paragraph(
            f"{ref['index']}. {_esc(ref['authors'])} ({_esc(ref['year'])}). "
            f"{title}.{journal}{doi}", styles["Body"]))

    # multiBuild, not build: a table of contents needs a second pass, because
    # the page a heading lands on is only known once the first layout has run.
    doc.multiBuild(story)
    return {
        "claims": len(model["claims"]),
        # The figures actually EMBEDDED, which is the only count a caller or a
        # gate may trust; the model list can be longer if a crop was unreadable.
        "figures": len(embedded_figs),
        "embedded_figure_numbers": sorted(embedded_numbers),
        "model_figures": len(model["figures"]),
        "visual_abstract": abstract_flow is not None,
        "anchors": n_anchors,
        "panel": panel_flow is not None,
        "references": len(model["references"]),
        "sections": sum(1 for k in SECTION_KEYS
                        if (model.get("sections") or {}).get(k)),
        "narratives": sum(1 for c in model["claims"] if c.get("narrative")),
        "out": str(out_path),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--visual-abstract", default=None)
    ap.add_argument("--contract", default=None,
                    help="defaults to templates/report_contract.json")
    # Superseded by deliverables/report_sections.json, which the Markdown
    # builder reads too. Kept as a per-section fallback so runs written against
    # the old CLI keep building.
    ap.add_argument("--intro-file", default=None)
    ap.add_argument("--methods-file", default=None)
    ap.add_argument("--conclusions-file", default=None)
    ap.add_argument("--next-steps-file", default=None,
                    help="one step per line")
    args = ap.parse_args(argv)

    contract = load_contract(args.contract)
    # An explicitly named contract that cannot be read must not silently become
    # "no contract" — that turns the narrative requirement off and reports
    # success.
    if args.contract and not contract:
        raise SystemExit(f"could not read contract: {args.contract}")

    def _text(path: str | None) -> str:
        return pathlib.Path(path).read_text(encoding="utf-8").strip() if path else ""

    steps = [s.strip() for s in _text(args.next_steps_file).splitlines() if s.strip()]
    summary = build(
        pathlib.Path(args.root).resolve(),
        pathlib.Path(args.out).resolve(),
        pathlib.Path(args.visual_abstract).resolve() if args.visual_abstract else None,
        intro=_text(args.intro_file),
        methods=_text(args.methods_file),
        conclusions=_text(args.conclusions_file),
        next_steps=steps,
        contract=contract,
    )

    from verify_pdf_structure import verify as verify_pdf_structure

    structure_failures, _structure_notes = verify_pdf_structure(
        pathlib.Path(args.out).resolve()
    )
    if structure_failures:
        raise SystemExit(
            "built PDF failed structural validation: "
            + "; ".join(structure_failures)
        )

    # Keep the cold-start packet fresh at a natural boundary. A context can die
    # at any moment and cannot warn us first, so the packet is regenerated
    # whenever the run reaches a state worth resuming from. Best-effort: a
    # failure here must never cost a built deliverable.
    try:
        from run_state import write_context
        write_context(pathlib.Path(args.root).resolve())
    except Exception as exc:  # noqa: BLE001 - the deliverable is what matters
        print(f"WARN: could not refresh context packet: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)

    print("BUILD-PDF: " + " ".join(f"{k}={v}" for k, v in summary.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
