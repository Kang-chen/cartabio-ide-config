"""
Assemble the Phylo-branded binding-pocket PDF report.

Follows the pdf-report-generation skill (ReportLab Platypus, Phylo palette/fonts,
clean header/footer, validated output). Sections, in order:

  Title -> Infographic panel -> Executive summary (Introduction) -> Methods ->
  Results (contact table + figures + interaction breakdown [+ concordance]) ->
  Conclusions -> References -> Next steps

The INFOGRAPHIC panel at the top is a compact "at-a-glance" card: target, ligand
(+ formula/MW), counts of pocket residues / core contacts / H-bonds, the key
residues, an interaction-type mini bar chart, and (for kinases) the DFG note.

Everything is data-driven from the `payload` dict produced by run_pipeline. No
values are hard-coded to any specific target.

PDF rules honored: no Unicode subscripts (use <sub>/<super>); explicit colWidths;
hAlign CENTER on Drawing/Image/Table; KeepTogether for figure+caption; repeatRows
for long tables; write directly to /mnt/results; validate after build.
"""

import os
from datetime import datetime

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---- Phylo brand palette -----------------------------------------------------
PHYLO_GOLD = HexColor("#D4A04A")
HEADING_COLOR = HexColor("#111111")
BODY_TEXT = HexColor("#2C2A26")
MUTED_TEXT = HexColor("#8A8378")
TABLE_HEADER_BG = PHYLO_GOLD
TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F9F7F3")
TABLE_BORDER = HexColor("#D5CFC5")
CALLOUT_BG = HexColor("#FAF9F3")
PHYLO_BLUE = HexColor("#0279EE")
PHYLO_GREEN = HexColor("#75A025")
PHYLO_ORANGE = HexColor("#FF9400")
PHYLO_PINK = HexColor("#FD9BED")
PHYLO_RED = HexColor("#E9134C")
CHART_COLORS = [PHYLO_GOLD, PHYLO_BLUE, PHYLO_GREEN, PHYLO_ORANGE, PHYLO_PINK, HexColor("#000000")]

USABLE_W = 492  # letter, 60pt margins


def _pretty(s):
    """Replace tokens that fall back to boxes in Helvetica with HTML entities."""
    if not s:
        return s
    return (s.replace("alphaC", "&#945;C")
             .replace("beta3", "&#946;3")
             .replace("\u00c5", "&#197;")
             .replace("<=", "&#8804;")
             .replace(">=", "&#8805;"))


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle(name="RTitle", fontName="Helvetica-Bold", fontSize=24,
                          textColor=HEADING_COLOR, leading=29, spaceAfter=4))
    ss.add(ParagraphStyle(name="RSub", fontName="Helvetica", fontSize=11,
                          textColor=PHYLO_GOLD, spaceAfter=3))
    ss.add(ParagraphStyle(name="RAttr", fontName="Helvetica-Oblique", fontSize=9.5,
                          textColor=MUTED_TEXT, spaceAfter=6))
    ss.add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=15,
                          textColor=HEADING_COLOR, spaceBefore=16, spaceAfter=7))
    ss.add(ParagraphStyle(name="Body2", fontName="Helvetica", fontSize=10,
                          textColor=BODY_TEXT, alignment=TA_JUSTIFY, leading=14.5,
                          spaceAfter=7))
    ss.add(ParagraphStyle(name="Cap", fontName="Helvetica-Oblique", fontSize=8.5,
                          textColor=MUTED_TEXT, alignment=TA_CENTER, spaceAfter=12))
    ss.add(ParagraphStyle(name="Cell", fontName="Helvetica", fontSize=7.6,
                          textColor=BODY_TEXT, leading=9.5))
    ss.add(ParagraphStyle(name="CellH", fontName="Helvetica-Bold", fontSize=7.8,
                          textColor=TABLE_HEADER_FG, leading=9.5))
    ss.add(ParagraphStyle(name="Info", fontName="Helvetica", fontSize=9,
                          textColor=BODY_TEXT, leading=12.5))
    ss.add(ParagraphStyle(name="InfoBig", fontName="Helvetica-Bold", fontSize=17,
                          textColor=HEADING_COLOR, leading=19))
    ss.add(ParagraphStyle(name="InfoLbl", fontName="Helvetica", fontSize=7.5,
                          textColor=MUTED_TEXT, leading=9))
    # Note: ReportLab's default sample stylesheet already defines "Bullet", so we
    # use a distinct name ("RBullet") to avoid a KeyError on ss.add().
    ss.add(ParagraphStyle(name="RBullet", fontName="Helvetica", fontSize=9.8,
                          textColor=BODY_TEXT, leading=13.5, leftIndent=12,
                          spaceAfter=3, bulletIndent=2))
    return ss


def _header_footer(title):
    def cb(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(MUTED_TEXT)
        canvas.drawString(60, h - 40, title[:95])
        canvas.setStrokeColor(PHYLO_GOLD)
        canvas.setLineWidth(1)
        canvas.line(60, h - 48, w - 60, h - 48)
        canvas.setStrokeColor(TABLE_BORDER)
        canvas.setLineWidth(0.75)
        canvas.line(60, 40, w - 60, 40)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED_TEXT)
        canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
        canvas.restoreState()
    return cb


def _divider():
    return HRFlowable(width=USABLE_W, thickness=1, color=PHYLO_GOLD, spaceBefore=4, spaceAfter=8)


def _mini_interaction_chart(type_counts):
    """Small horizontal bar chart of interaction-type residue counts.

    Bars are hand-drawn with Rect/String primitives rather than ReportLab's
    HorizontalBarChart, which renders unreliably as a Platypus flowable (bars
    and category labels can silently drop out). Manual primitives always draw.
    """
    items = [(k, v) for k, v in sorted(type_counts.items(), key=lambda x: -x[1]) if v]
    if not items:
        return None
    # top-to-bottom = largest-to-smallest
    cats = [k for k, _ in items]
    vals = [v for _, v in items]
    vmax = max(vals) or 1

    W = 147.0               # fits the right infographic cell (167 - 2*10 padding)
    row_h = 15.0
    pad_top, pad_bot = 6.0, 6.0
    label_w = 66.0          # left gutter for category names
    val_w = 15.0            # right gutter for value labels
    bar_x = label_w
    bar_wmax = W - label_w - val_w
    n = len(cats)
    H = pad_top + pad_bot + row_h * n

    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, fillColor=CALLOUT_BG, strokeColor=TABLE_BORDER,
               strokeWidth=0.5))
    for i, (cat, val) in enumerate(zip(cats, vals)):
        # rows drawn top-down
        cy = H - pad_top - (i + 1) * row_h
        bar_h = 8.0
        by = cy + (row_h - bar_h) / 2.0
        bw = max(1.5, bar_wmax * (val / vmax))
        # category label (right-aligned into the gutter)
        d.add(String(label_w - 4, by + 1.0, _pretty(str(cat)),
                     fontSize=7, fontName="Helvetica",
                     fillColor=BODY_TEXT, textAnchor="end"))
        # bar
        d.add(Rect(bar_x, by, bw, bar_h, fillColor=PHYLO_GOLD,
                   strokeColor=None, strokeWidth=0))
        # value label just past the bar
        d.add(String(bar_x + bw + 3, by + 1.0, str(val),
                     fontSize=7, fontName="Helvetica-Bold",
                     fillColor=BODY_TEXT, textAnchor="start"))
    d.hAlign = "CENTER"
    return d


def _infographic(payload, ss):
    """Top at-a-glance card built as a 2-column table."""
    t = payload["target"]
    lg = payload["ligand"]
    s = payload["summary"]

    def stat_cell(value, label):
        # single paragraph: big number then muted label, so nothing splits across
        # table cells and labels can wrap cleanly within the (wider) column.
        html = (f'<font size="17"><b>{value}</b></font><br/>'
                f'<font size="7.5" color="#8A8378">{_pretty(label)}</font>')
        return Paragraph(html, ss["Body2"])

    # left column: identity
    lig_line = f"<b>{lg.get('code','?')}</b>"
    if lg.get("name"):
        lig_line += f" &mdash; {lg['name']}"
    formula = lg.get("formula") or ""
    mw = lg.get("formula_weight")
    sub = []
    if formula:
        sub.append(f"Formula {formula}")
    if mw:
        sub.append(f"MW {float(mw):.1f}")
    left = [
        Paragraph("TARGET", ss["InfoLbl"]),
        Paragraph(_pretty(t.get("name") or t.get("pdb_id", "?")), ss["Info"]),
        Spacer(1, 3),
        Paragraph("LIGAND", ss["InfoLbl"]),
        Paragraph(_pretty(lig_line), ss["Info"]),
        Paragraph(_pretty("; ".join(sub)), ss["InfoLbl"]) if sub else Spacer(1, 0),
        Spacer(1, 3),
        Paragraph("STRUCTURE(S)", ss["InfoLbl"]),
        Paragraph(_pretty(payload.get("structures_line", t.get("pdb_id", "?"))), ss["Info"]),
    ]
    if payload.get("kinase", {}).get("is_kinase"):
        left.append(Spacer(1, 3))
        left.append(Paragraph("KINASE MOTIFS", ss["InfoLbl"]))
        left.append(Paragraph(_pretty(payload["kinase"].get("summary_line", "")), ss["InfoLbl"]))

    # middle column: 4 big stats in a 2x2
    n_key = s.get("n_key_residues", len(payload.get("key_residues", [])))
    stats_tbl = Table(
        [[stat_cell(s["n_contact_residues"], "pocket residues"),
          stat_cell(s["n_core_residues"], "core \u2264 4.0\u00c5")],
         [stat_cell(s["n_hbonds"], "candidate H-bonds"),
          stat_cell(n_key, "key residues")]],
        colWidths=[75, 75])
    stats_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    # right column: mini chart
    chart = _mini_interaction_chart(payload.get("type_counts", {}))
    right = [Paragraph("INTERACTION TYPES", ss["InfoLbl"]), Spacer(1, 2),
             chart or Paragraph("n/a", ss["InfoLbl"])]

    card = Table([[left, [stats_tbl], right]], colWidths=[150, 175, 167])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 0.75, PHYLO_GOLD),
        ("LINEBEFORE", (1, 0), (1, 0), 0.5, TABLE_BORDER),
        ("LINEBEFORE", (2, 0), (2, 0), 0.5, TABLE_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    card.hAlign = "CENTER"
    return card


def _contact_table(contacts, ss, max_rows=26, has_kinase=False, comparison_labels=None):
    """Main results table of pocket residues."""
    comparison_labels = comparison_labels or []
    headers = ["Residue", "Min d (\u00c5)", "Core", "Contacts\n\u22644.5\u00c5",
               "Nearest\nfragment", "Interaction", "Conf.", "H-bonds"]
    if has_kinase:
        headers.insert(1, "Kinase region")
    col_head = [Paragraph(_pretty(h.replace("\n", "<br/>")), ss["CellH"]) for h in headers]
    rows = [col_head]
    cs = sorted(contacts, key=lambda c: c["min_dist"])[:max_rows]
    any_tentative = False
    for c in cs:
        hb = "; ".join(f"{h['prot_atom']}\u2013{h['lig_atom']} {h['dist']}" for h in c["hbonds"][:3])
        conf = c.get("interaction_confidence", "high")
        src = c.get("interaction_source", "geometry")
        itype = c.get("interaction_type", "vdW")
        # mark tentative calls with a dagger and italics so they are visually distinct
        if conf == "tentative":
            any_tentative = True
            itype_disp = f"<i>{_pretty(itype)}</i> \u2020"
            conf_disp = "<i>tentative</i>"
        else:
            itype_disp = _pretty(itype)
            conf_disp = "high"
        conf_disp += f"<br/><font size=5 color='#888888'>{src}</font>"
        cells = [
            f"{c['resname']}{c['resseq']}" + (f"/{c['chain']}" if c.get("chain") else ""),
            f"{c['min_dist']:.2f}",
            "\u25cf" if c["core_contact"] else "",
            str(c["n_wide"]),
            _pretty(str(c.get("nearest_fragment", c.get("nearest_lig_atom", "")))),
            itype_disp,
            conf_disp,
            _pretty(hb),
        ]
        if has_kinase:
            cells.insert(1, _pretty(c.get("kinase_region", "")))
        rows.append([Paragraph(str(x), ss["Cell"]) for x in cells])

    if has_kinase:
        widths = [54, 60, 32, 24, 32, 56, 52, 44, 96]
    else:
        widths = [60, 38, 24, 40, 58, 88, 52, 120]
    # scale to usable width
    scale = USABLE_W / sum(widths)
    widths = [w * scale for w in widths]
    t = Table(rows, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, TABLE_BORDER),
        ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(2, len(rows), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    t.setStyle(TableStyle(style))
    t.hAlign = "CENTER"
    return t, len(cs)


def _concordance_table(rows, labels, ss, max_rows=26):
    headers = ["Residue", "d primary"] + [f"d {l}" for l in labels] + ["In all", "Identity"]
    head = [Paragraph(_pretty(h), ss["CellH"]) for h in headers]
    out = [head]
    for r in sorted(rows, key=lambda x: x["min_dist_primary"])[:max_rows]:
        cells = [f"{r['resname']}{r['resseq']}", f"{r['min_dist_primary']:.2f}"]
        for l in labels:
            v = r.get(f"min_dist_{l}")
            cells.append(f"{v:.2f}" if v is not None else "\u2013")
        cells.append("yes" if r["present_in_all"] else "no")
        cells.append("=" if r["identity_conserved"] else "\u2260")
        out.append([Paragraph(str(x), ss["Cell"]) for x in cells])
    ncol = len(headers)
    widths = [USABLE_W / ncol] * ncol
    t = Table(out, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
             ("GRID", (0, 0), (-1, -1), 0.4, TABLE_BORDER),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for i in range(2, len(out), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    t.setStyle(TableStyle(style))
    t.hAlign = "CENTER"
    return t


def _fig(path, ss, caption, width=USABLE_W, max_h=430):
    if not path or not os.path.exists(path):
        return None
    from PIL import Image as PILImage
    iw, ih = PILImage.open(path).size
    w = width
    h = w * ih / iw
    if h > max_h:
        h = max_h
        w = h * iw / ih
    img = Image(path, width=w, height=h)
    img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1, 3), Paragraph(_pretty(caption), ss["Cap"])])


def build_report(payload, out_path):
    """
    Build the PDF from the pipeline payload.

    payload keys (all data-driven):
      target {name, pdb_id, method, resolution_A}
      ligand {code, name, formula, formula_weight, smiles}
      structures_line (str), summary {...}, type_counts {...}, key_residues [..],
      contacts [..], kinase {is_kinase, summary_line}, comparison {rows, labels, summary},
      figures {interaction, distance, heatmap, pocket3d, closeup3d},
      references [..], methods_params {...}, extended (bool), notes [..]
    """
    ss = _styles()
    title = f"Binding-Pocket Contact Map: {payload['ligand'].get('code','ligand')} \u2013 {payload['target'].get('name') or payload['target'].get('pdb_id','')}"
    doc = SimpleDocTemplate(out_path, pagesize=letter, topMargin=54, bottomMargin=54,
                            leftMargin=60, rightMargin=60, title=title)
    story = []

    # Title
    story.append(Spacer(1, 8))
    story.append(Paragraph(_pretty(title), ss["RTitle"]))
    story.append(Paragraph("Protein\u2013ligand binding-pocket interaction analysis", ss["RSub"]))
    story.append(Paragraph("<i>Generated by Biomni  |  " + datetime.now().strftime("%Y-%m-%d") + "</i>",
                           ss["RAttr"]))
    story.append(_divider())

    # Infographic
    story.append(_infographic(payload, ss))
    story.append(Spacer(1, 10))

    # Introduction / Executive summary
    story.append(Paragraph("Introduction", ss["H2"]))
    for para in payload.get("intro_paragraphs", []):
        story.append(Paragraph(_pretty(para), ss["Body2"]))

    # Methods
    story.append(Paragraph("Methods", ss["H2"]))
    for para in payload.get("methods_paragraphs", []):
        story.append(Paragraph(_pretty(para), ss["Body2"]))

    # Results
    story.append(Paragraph("Results", ss["H2"]))
    for para in payload.get("results_paragraphs", []):
        story.append(Paragraph(_pretty(para), ss["Body2"]))

    has_kinase = bool(payload.get("kinase", {}).get("is_kinase"))
    ctable, nshown = _contact_table(payload["contacts"], ss, has_kinase=has_kinase)
    story.append(Spacer(1, 2))
    story.append(ctable)
    total = len(payload["contacts"])
    engine = payload.get("typing_engine", "geometry")
    plip_info = payload.get("plip_info", {}) or {}
    engine_str = (f"PLIP v{plip_info.get('version','?')} (primary)"
                  if engine == "PLIP" and plip_info.get("used")
                  else "hardened distance/angle geometry")
    conf = payload.get("confidence_counts", {}) or {}
    note = (f"Table 1. Pocket residues within 4.5 &#197; of the ligand (showing {nshown} of {total}; "
            f"full table in the CSV). The <b>Conf.</b> column gives the confidence tier and the engine "
            f"that made each call ({engine_str}); <b>tentative</b> calls are italicised and marked "
            f"&#8224;. Confidence totals: {conf.get('high', 0)} high, {conf.get('tentative', 0)} tentative. "
            f"A tentative call is distance-only, near a geometric threshold, or dependent on an assumed "
            f"ligand protonation state, and should be confirmed structurally.")
    story.append(Paragraph(_pretty(note), ss["Cap"]))

    # Figures
    figs = payload.get("figures", {})
    for key, cap in [
        ("interaction", "Figure 1. Interaction diagram: contacting residues arranged around the ligand; spoke length scales with minimum contact distance; dashed spokes mark candidate hydrogen bonds; colors denote interaction type."),
        ("distance", "Figure 2. Per-residue minimum heavy-atom distance to the ligand. Dashed line = 4.0 &#197; core shell; dotted line = 4.5 &#197; wide shell. 'H' marks residues forming candidate H-bonds."),
        ("heatmap", "Figure 3. Fragment&#8211;residue contact map: number of heavy-atom contacts (&#8804;4.5 &#197;) between each ligand chemical fragment and each pocket residue."),
        ("pocket3d", "Figure 4. Three-dimensional view of the binding pocket: protein cartoon (transparent), ligand and key residues as sticks, candidate hydrogen bonds as dashed lines."),
        ("closeup3d", "Figure 5. Close-up of the polar contact network around the ligand."),
    ]:
        block = _fig(figs.get(key), ss, cap)
        if block:
            story.append(block)

    # Concordance (optional)
    comp = payload.get("comparison")
    if comp and comp.get("rows"):
        story.append(Paragraph("Cross-structure concordance", ss["H2"]))
        for para in comp.get("paragraphs", []):
            story.append(Paragraph(_pretty(para), ss["Body2"]))
        story.append(_concordance_table(comp["rows"], comp["labels"], ss))
        story.append(Paragraph(_pretty("Table 2. Reproducibility of pocket contacts across structures."),
                               ss["Cap"]))

    # Conclusions
    story.append(Paragraph("Conclusions", ss["H2"]))
    for para in payload.get("conclusions_paragraphs", []):
        story.append(Paragraph(_pretty(para), ss["Body2"]))

    # Caveats callout
    if payload.get("caveats"):
        cav = "<b>Caveats.</b> " + " ".join(payload["caveats"])
        box = Table([[Paragraph(_pretty(cav), ss["Info"])]], colWidths=[USABLE_W])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
            ("LINEBEFORE", (0, 0), (0, -1), 3, PHYLO_GOLD),
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
        box.hAlign = "CENTER"
        story.append(Spacer(1, 4))
        story.append(box)

    # References
    refs = payload.get("references") or []
    story.append(Paragraph("References", ss["H2"]))
    if refs:
        from importlib import import_module
        try:
            lc = import_module("literature_context")
            renderer = lc.reference_to_citation
        except Exception:  # noqa: BLE001
            renderer = lambda e: e.get("citation", str(e))  # noqa: E731
        for i, r in enumerate(refs, 1):
            story.append(Paragraph(_pretty(f"{i}. {renderer(r)}"), ss["Body2"]))
    else:
        story.append(Paragraph(
            "No literature references were retrieved for this pocket; findings above "
            "are derived solely from the crystallographic coordinates.", ss["Body2"]))

    # Next steps
    story.append(Paragraph("Next steps", ss["H2"]))
    for step in payload.get("next_steps", []):
        story.append(Paragraph(_pretty("&#8226; " + step), ss["RBullet"]))

    doc.build(story, onFirstPage=_header_footer(title), onLaterPages=_header_footer(title))
    print(f"[OK] wrote {out_path} ({os.path.getsize(out_path):,} bytes)")
    return out_path


def validate_pdf(path, min_pages=2, min_bytes=5000):
    """Quick structural validation of the built PDF."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    n = len(reader.pages)
    size = os.path.getsize(path)
    assert n >= min_pages, f"only {n} page(s)"
    assert size > min_bytes, f"only {size} bytes"
    txt = reader.pages[0].extract_text() or ""
    assert len(txt.strip()) > 0, "no extractable text on page 1"
    print(f"[OK] PDF valid: {n} pages, {size:,} bytes")
    return {"pages": n, "bytes": size}
