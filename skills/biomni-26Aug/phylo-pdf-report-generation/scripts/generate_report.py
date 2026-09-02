#!/usr/bin/env python3
"""Generate a Phylo-branded PDF report from structured JSON content.

Usage:
    python3 generate_report.py <report_content.json> <output.pdf>

The input JSON schema is documented in SKILL.md under ## Inputs.
Uses ReportLab Platypus (flowables) for automatic page breaks, text wrapping,
and table layout with Phylo brand styling (gold primary accent, clean white
pages, consistent header/footer on every page).

ReportLab writes PDFs sequentially, so the output may be written directly to
the results mount — no staging copy is needed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

# ─── Phylo Brand Constants ───────────────────────────────────────────────────
# House palette (see the pdf-report-generation provider's brand section).
# Gold is the primary accent: table headers, dividers, subtitle, highlights.

PHYLO_BLACK = HexColor("#000000")      # Headings, primary text
PHYLO_WARM_GRAY = HexColor("#ECE9E2")  # Backgrounds, alternating table rows
PHYLO_OFF_WHITE = HexColor("#FAF9F3")  # Page background, light sections
PHYLO_LIME = HexColor("#E9ED4C")       # Chart color (use sparingly)
PHYLO_ORANGE = HexColor("#FF9400")     # Warnings, secondary chart color
PHYLO_GREEN = HexColor("#75A025")      # Success states, positive indicators
PHYLO_PINK = HexColor("#FD9BED")       # Tertiary accent (use sparingly)
PHYLO_BLUE = HexColor("#0279EE")       # Links, chart color

# Derived / functional — these drive the report styling
PHYLO_GOLD = HexColor("#D4A04A")       # PRIMARY ACCENT: table headers, dividers
HEADING_COLOR = HexColor("#111111")    # Headings (near-black)
BODY_TEXT = HexColor("#2C2A26")        # Body text (warm dark)
MUTED_TEXT = HexColor("#8A8378")       # Captions, footnotes, secondary text
TABLE_HEADER_BG = PHYLO_GOLD           # Table header background (gold)
TABLE_HEADER_FG = HexColor("#FFFFFF")  # Table header text (white)
TABLE_ALT_ROW = HexColor("#F9F7F3")    # Alternating row shading
TABLE_BORDER = HexColor("#D5CFC5")     # Table grid lines (warm gray)
DIVIDER_COLOR = PHYLO_GOLD             # Section dividers (gold)
LINK_COLOR = HexColor("#0563C1")       # Hyperlinks

PAGE_WIDTH, PAGE_HEIGHT = letter  # 612 x 792 pt
MARGIN = 60  # house style: 60 pt side margins -> 492 pt usable width
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
TOP_MARGIN = 0.9 * inch   # clears the header rule
BOTTOM_MARGIN = 0.9 * inch  # clears the footer rule

FONT_HEADING = "Helvetica-Bold"
FONT_BODY = "Helvetica"        # Liberation Sans metric-equivalent built-in
FONT_ITALIC = "Helvetica-Oblique"
FONT_MONO = "Courier"          # Liberation Mono metric-equivalent built-in

#: Canonical section ids in mandatory order. The heading text rendered for
#: these ids is fixed so downstream text-extraction checks find the exact
#: universal report headings.
CANONICAL_SECTIONS = {
    "task_context": "Task Context",
    "methods": "Methods & Sources",
    "results": "Results",
    "conclusions": "Conclusions & Interpretation",
    "limitations": "Limitations",
}
SECTION_ORDER = list(CANONICAL_SECTIONS)


def _escape(text: object) -> str:
    """Escape XML-special characters for ReportLab Paragraph markup."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ─── Styles ──────────────────────────────────────────────────────────────────

def build_styles() -> dict[str, ParagraphStyle]:
    """Return a dict of named ParagraphStyles for the report."""
    base = getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {}

    styles["ReportTitle"] = ParagraphStyle(
        "ReportTitle",
        parent=base["Title"],
        fontName=FONT_HEADING,
        fontSize=26,
        leading=32,
        textColor=HEADING_COLOR,
        spaceBefore=0,
        spaceAfter=6,
    )

    styles["Subtitle"] = ParagraphStyle(
        "Subtitle",
        parent=base["Normal"],
        fontName=FONT_BODY,
        fontSize=11,
        leading=15,
        textColor=PHYLO_GOLD,
        spaceAfter=4,
    )

    styles["Attribution"] = ParagraphStyle(
        "Attribution",
        parent=base["Normal"],
        fontName=FONT_ITALIC,
        fontSize=10,
        leading=14,
        textColor=MUTED_TEXT,
        spaceAfter=8,
    )

    styles["SectionHead"] = ParagraphStyle(
        "SectionHead",
        parent=base["Heading1"],
        fontName=FONT_HEADING,
        fontSize=18,
        leading=22,
        textColor=HEADING_COLOR,
        spaceBefore=24,
        spaceAfter=10,
    )

    styles["Subheading"] = ParagraphStyle(
        "Subheading",
        parent=base["Heading2"],
        fontName=FONT_HEADING,
        fontSize=13,
        leading=18,
        textColor=HEADING_COLOR,
        spaceBefore=14,
        spaceAfter=6,
    )

    styles["Body"] = ParagraphStyle(
        "Body",
        parent=base["Normal"],
        fontName=FONT_BODY,
        fontSize=10.5,
        leading=15,
        alignment=TA_JUSTIFY,
        textColor=BODY_TEXT,
        spaceAfter=8,
    )

    styles["BodyLeft"] = ParagraphStyle(
        "BodyLeft",
        parent=styles["Body"],
        alignment=TA_LEFT,
    )

    styles["Caption"] = ParagraphStyle(
        "Caption",
        parent=base["Normal"],
        fontName=FONT_ITALIC,
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=MUTED_TEXT,
        spaceBefore=4,
        spaceAfter=14,
    )

    styles["TableTitle"] = ParagraphStyle(
        "TableTitle",
        parent=base["Normal"],
        fontName=FONT_HEADING,
        fontSize=10.5,
        leading=14,
        textColor=HEADING_COLOR,
        spaceBefore=10,
        spaceAfter=4,
    )

    styles["TableCell"] = ParagraphStyle(
        "TableCell",
        parent=base["Normal"],
        fontName=FONT_BODY,
        fontSize=9,
        leading=12,
        textColor=BODY_TEXT,
    )

    styles["TableCellMono"] = ParagraphStyle(
        "TableCellMono",
        parent=styles["TableCell"],
        fontName=FONT_MONO,
    )

    styles["TableHeader"] = ParagraphStyle(
        "TableHeader",
        parent=base["Normal"],
        fontName=FONT_HEADING,
        fontSize=9,
        leading=12,
        textColor=TABLE_HEADER_FG,
    )

    styles["Bullet"] = ParagraphStyle(
        "Bullet",
        parent=styles["Body"],
        leftIndent=20,
        bulletIndent=8,
        spaceAfter=4,
        alignment=TA_LEFT,
    )

    styles["Reference"] = ParagraphStyle(
        "Reference",
        parent=base["Normal"],
        fontName=FONT_BODY,
        fontSize=9,
        leading=12,
        leftIndent=20,
        firstLineIndent=-20,
        spaceAfter=4,
        textColor=BODY_TEXT,
    )

    return styles


# ─── Page Chrome ─────────────────────────────────────────────────────────────

def _page_header_footer(canvas, doc):
    """Canvas callback for all pages: clean scientific header/footer.

    Header: report title in muted text with a gold underline. Footer: thin
    warm-gray line and a centered muted page number. No explicit product
    branding in the header — the report title only.
    """
    canvas.saveState()
    w, h = letter

    # Header
    report_title = getattr(doc, "phylo_report_title", "")
    if report_title:
        canvas.setFont(FONT_BODY, 9)
        canvas.setFillColor(MUTED_TEXT)
        canvas.drawString(MARGIN, h - 40, report_title)
    canvas.setStrokeColor(PHYLO_GOLD)
    canvas.setLineWidth(1)
    canvas.line(MARGIN, h - 48, w - MARGIN, h - 48)

    # Footer
    canvas.setStrokeColor(TABLE_BORDER)
    canvas.setLineWidth(0.75)
    canvas.line(MARGIN, 40, w - MARGIN, 40)
    canvas.setFont(FONT_BODY, 8)
    canvas.setFillColor(MUTED_TEXT)
    canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")

    canvas.restoreState()


# ─── Content Block Renderers ─────────────────────────────────────────────────

def _render_paragraph(block: dict, styles: dict) -> Paragraph:
    # Body text may intentionally carry ReportLab XML tags (<b>, <super>...).
    return Paragraph(str(block["text"]), styles["Body"])


def _render_subheading(block: dict, styles: dict) -> Paragraph:
    return Paragraph(_escape(block["text"]), styles["Subheading"])


def _render_bullet_list(block: dict, styles: dict) -> list:
    flowables = []
    for item in block.get("items", []):
        flowables.append(Paragraph(str(item), styles["Bullet"], bulletText="\u2022"))
    flowables.append(Spacer(1, 6))
    return flowables


def _render_numbered_list(block: dict, styles: dict) -> list:
    flowables = []
    for i, item in enumerate(block.get("items", []), 1):
        flowables.append(Paragraph(str(item), styles["Bullet"], bulletText=f"{i}."))
    flowables.append(Spacer(1, 6))
    return flowables


def _render_figure(block: dict, styles: dict, base_dir: Path) -> list:
    """Embed an image proportionally scaled, centered, caption bound to it."""
    flowables = []
    img_path = Path(block["path"])
    if not img_path.is_absolute():
        img_path = base_dir / img_path

    if not img_path.exists():
        # Render a placeholder paragraph instead of silently skipping
        flowables.append(
            Paragraph(
                f"<i>[Figure not found: {_escape(block['path'])}]</i>",
                styles["Caption"],
            )
        )
        return flowables

    iw, ih = ImageReader(str(img_path)).getSize()
    max_w = CONTENT_WIDTH
    max_h = PAGE_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN - 1.2 * inch
    scale = min(max_w / iw, max_h / ih, 1.0)
    img = Image(str(img_path), width=iw * scale, height=ih * scale)
    img.hAlign = "CENTER"

    caption_text = block.get("caption", "")
    if caption_text:
        caption = Paragraph(_escape(caption_text), styles["Caption"])
        flowables.append(KeepTogether([img, Spacer(1, 4), caption]))
    else:
        flowables.append(img)
        flowables.append(Spacer(1, 12))

    return flowables


def _render_table(block: dict, styles: dict) -> list:
    """Render a centered table with gold header and warm-gray grid."""
    flowables = []
    title = block.get("title", "")
    if title:
        flowables.append(Paragraph(_escape(title), styles["TableTitle"]))

    headers = block.get("headers", [])
    rows = block.get("rows", [])

    if not headers and not rows:
        return flowables

    table_data = []
    if headers:
        table_data.append(
            [Paragraph(f"<b>{_escape(h)}</b>", styles["TableHeader"]) for h in headers]
        )

    for row in rows:
        cell_row = []
        for cell in row:
            cell_str = str(cell)
            if _is_numeric_cell(cell_str) or _is_gene_id(cell_str):
                cell_row.append(Paragraph(_escape(cell_str), styles["TableCellMono"]))
            else:
                cell_row.append(Paragraph(_escape(cell_str), styles["TableCell"]))
        table_data.append(cell_row)

    n_cols = len(headers) if headers else len(rows[0]) if rows else 1
    col_widths = [CONTENT_WIDTH / n_cols] * n_cols

    tbl = Table(table_data, colWidths=col_widths, repeatRows=1 if headers else 0)
    tbl.hAlign = "CENTER"

    style_cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
    ]

    if headers:
        style_cmds.append(("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG))
        style_cmds.append(("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_FG))
        for i in range(2, len(table_data), 2):
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))

    tbl.setStyle(TableStyle(style_cmds))
    flowables.append(tbl)
    flowables.append(Spacer(1, 12))
    return flowables


def _is_numeric_cell(s: str) -> bool:
    """Check if a string looks like a number (int, float, scientific notation)."""
    s = s.strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_gene_id(s: str) -> bool:
    """Check if a string looks like a gene or protein identifier."""
    s = s.strip()
    if not s:
        return False
    # Ensembl: ENSG00000141510, ENSG00000141510.6
    if s.upper().startswith(("ENSG", "ENSP", "ENST", "ENSMUSG", "ENSRNOG")):
        return True
    # UniProt: P04637, Q9H3D4
    if len(s) == 6 and s[0].isupper() and s[1:5].isdigit() and s[5].isupper() and s[5].isalpha():
        return True
    # Common gene symbol patterns (all caps, short)
    if s.isupper() and len(s) <= 10 and s.replace("_", "").isalpha():
        return True
    return False


# ─── Section Renderer ────────────────────────────────────────────────────────

def divider(width: str | float = "100%") -> HRFlowable:
    """Gold section divider matching the header underline."""
    return HRFlowable(
        width=width,
        thickness=1,
        color=DIVIDER_COLOR,
        spaceBefore=4,
        spaceAfter=10,
    )


def render_section(section: dict, styles: dict, base_dir: Path) -> list:
    """Render a single section with its canonical heading and content blocks."""
    section_id = section.get("id", "")
    # Canonical ids map to fixed heading text so the universal report
    # headings appear verbatim; other ids use the provided heading as-is.
    heading = CANONICAL_SECTIONS.get(section_id, section.get("heading", section_id))
    heading_flowables = [Paragraph(_escape(heading), styles["SectionHead"]), divider()]

    block_flowables: list = []
    for block in section.get("content", []):
        btype = block.get("type", "paragraph")
        if btype == "paragraph":
            block_flowables.append(_render_paragraph(block, styles))
        elif btype == "subheading":
            block_flowables.append(_render_subheading(block, styles))
        elif btype == "bullet_list":
            block_flowables.extend(_render_bullet_list(block, styles))
        elif btype == "numbered_list":
            block_flowables.extend(_render_numbered_list(block, styles))
        elif btype == "figure":
            block_flowables.extend(_render_figure(block, styles, base_dir))
        elif btype == "table":
            block_flowables.extend(_render_table(block, styles))

    if block_flowables:
        # Bind the heading to its first block so a section heading is never
        # orphaned at the bottom of a page.
        return [KeepTogether(heading_flowables + [block_flowables[0]])] + block_flowables[1:]
    return heading_flowables


# ─── Title Block (page 1 — no separate cover page) ──────────────────────────

def build_title_block(metadata: dict, styles: dict) -> list:
    """Title content as flowables at the top of the story."""
    flowables = []
    flowables.append(Spacer(1, 40))

    title = metadata.get("title", "Untitled Report")
    flowables.append(Paragraph(_escape(title), styles["ReportTitle"]))

    subtitle = metadata.get("subtitle", "")
    if subtitle:
        flowables.append(Paragraph(_escape(subtitle), styles["Subtitle"]))

    flowables.append(Spacer(1, 8))

    attribution_parts = ["Generated by Biomni"]
    date = metadata.get("date", "")
    if date:
        attribution_parts.append(str(date))
    author = metadata.get("author", "")
    if author:
        attribution_parts.append(str(author))
    project = metadata.get("project", "")
    if project:
        attribution_parts.append(str(project))
    flowables.append(
        Paragraph("<i>" + _escape("  |  ".join(attribution_parts)) + "</i>",
                  styles["Attribution"])
    )

    flowables.append(Spacer(1, 24))
    return flowables


# ─── References & Output Files ───────────────────────────────────────────────

def build_references(references: list, styles: dict) -> list:
    """Build the references section."""
    if not references:
        return []
    flowables = []
    flowables.append(Paragraph("References", styles["SectionHead"]))
    flowables.append(divider())
    for ref in references:
        ref_id = ref.get("id", "")
        citation = ref.get("citation", "")
        flowables.append(
            Paragraph(f"[{_escape(ref_id)}] {_escape(citation)}", styles["Reference"])
        )
    flowables.append(Spacer(1, 12))
    return flowables


def build_output_files_table(output_files: list, styles: dict) -> list:
    """Build the output files reproducibility table."""
    if not output_files:
        return []
    flowables = []
    flowables.append(Paragraph("Output Files", styles["Subheading"]))
    rows = [[f.get("filename", ""), f.get("description", "")] for f in output_files]
    flowables.extend(
        _render_table(
            {"headers": ["Filename", "Description"], "rows": rows},
            styles,
        )
    )
    return flowables


# ─── Main Document Builder ───────────────────────────────────────────────────

def generate_report(content_path: str, output_path: str) -> str:
    """Generate a Phylo-branded PDF from a report_content.json file.

    Args:
        content_path: Path to the JSON content file.
        output_path: Path to write the PDF.

    Returns:
        The absolute path to the generated PDF.
    """
    content_path = Path(content_path).resolve()
    output_path = Path(output_path).resolve()
    base_dir = content_path.parent

    with open(content_path, "r", encoding="utf-8") as f:
        content = json.load(f)

    metadata = content.get("metadata", {})
    sections = content.get("sections", [])
    references = content.get("references", [])
    output_files_table = content.get("output_files_table", [])

    styles = build_styles()

    doc = BaseDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        title=metadata.get("title", "Phylo Report"),
        author=metadata.get("author", "Biomni"),
    )
    doc.phylo_report_title = metadata.get("title", "")

    frame = Frame(
        MARGIN,
        BOTTOM_MARGIN,
        CONTENT_WIDTH,
        PAGE_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN,
        id="content",
        showBoundary=0,
    )
    doc.addPageTemplates([
        PageTemplate(id="content", frames=[frame], onPage=_page_header_footer),
    ])

    story: list = []

    # Title block on page 1 (no separate cover page)
    story.extend(build_title_block(metadata, styles))

    # Sections: canonical ids first in mandatory order, then any extras
    ordered = sorted(
        sections,
        key=lambda s: SECTION_ORDER.index(s.get("id"))
        if s.get("id") in SECTION_ORDER
        else len(SECTION_ORDER),
    )
    for section in ordered:
        story.extend(render_section(section, styles, base_dir))

    # References
    story.extend(build_references(references, styles))

    # Output files table
    story.extend(build_output_files_table(output_files_table, styles))

    doc.build(story)
    return str(output_path)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 3:
        print(
            "Usage: python3 generate_report.py <report_content.json> <output.pdf>",
            file=sys.stderr,
        )
        sys.exit(1)
    content_file = sys.argv[1]
    output_file = sys.argv[2]
    if not os.path.exists(content_file):
        print(f"Error: content file not found: {content_file}", file=sys.stderr)
        sys.exit(1)
    result = generate_report(content_file, output_file)
    print(f"Report generated: {result}", file=sys.stderr)


if __name__ == "__main__":
    main()
