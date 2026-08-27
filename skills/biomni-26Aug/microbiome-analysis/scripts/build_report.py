#!/usr/bin/env python3
"""
Reusable Phylo-branded PDF report builder (reportlab).

This is a ready-made IMPLEMENTATION of the `pdf-report-generation` system skill:
same Phylo palette, Helvetica typography, letter page with 60pt margins, gold
header underline / footer chrome, gold-header tables, gold-accent callouts, and
pypdf validation. Load `pdf-report-generation` for the full rationale; use this
module to build the report quickly. It ADDS three helpers that skill does not
cover but this workflow needs: infographic(), references_block(), nextsteps_block().

Import this module and assemble a `story` list of flowables, then call build().

    import build_report as B
    story = []
    story += [B.SP(24), B.P("My Study Title","ReportTitle"),
              B.P("Subtitle","Subtitle"), B.divider()]
    # 1. Infographic summary page (image made with the GenerateImage TOOL, not here)
    story += B.infographic("results/figures/infographic.png",
                           "Figure 1. Visual summary of the analysis.")
    story += [B.PageBreak()]
    # 2. Introduction (grounded in LiteratureSearch; inline [N] citations)
    story += [B.P("Introduction","SectionHead"), B.P("... dysbiosis in ... [1,2].")]
    # 3-6. Methods / Results / Conclusions / Figures
    story += [B.P("Executive Summary","SectionHead"), B.callout("<b>Key finding.</b> ...")]
    story += B.figel("results/figures/alpha.png", 440, "Figure 2. ...")
    story += [B.mktable(["Metric","p","q"], [["Shannon","0.04","0.07"]], [160,90,90])]
    # 7. References (numbers must match the inline [N] markers)
    story += B.references_block([
        "Author A, et al. Title. Journal. 2021;12(3):45-58. doi:10.x/y",
        "Author B, et al. Title. Journal. 2019;7:e12345.",
    ])
    # 8. Next steps
    story += B.nextsteps_block([
        "Confirm predicted SCFA capacity with shotgun metagenomics or targeted metabolomics.",
        "Expand the healthy-control arm to improve power.",
    ])
    B.build(story, "/mnt/results/report_mystudy.pdf", running_title="My Study")

Design lessons baked in (validated on a real 12->10 page report):
  * figel() preserves image ASPECT RATIO and wraps image+caption in KeepTogether
    so captions never orphan onto the next page and figures never distort.
  * Build the story FRESH each run: reportlab consumes/mutates flowables during
    build(); calling build() twice on the same list yields a 0-byte PDF. If you
    need to rebuild, regenerate the story from scratch.
  * R's file.copy() writes 0-byte files on /mnt/results (FUSE). Not an issue here
    because reportlab writes the PDF directly.
  * The infographic is a SCHEMATIC made with the GenerateImage tool, then embedded
    here. Do not draw it with matplotlib; do not hardcode fake numbers into it.
Requires: reportlab, pypdf (for validate), pillow.
"""
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, Image, HRFlowable, KeepTogether)
from PIL import Image as PILImage

# ---------- Phylo brand palette ----------
PHYLO_GOLD  = HexColor("#D4A04A")   # table headers / dividers / callout accent
HEADING     = HexColor("#111111")
BODY        = HexColor("#2C2A26")
MUTED       = HexColor("#8A8378")
HDR_FG      = HexColor("#FFFFFF")
ALT_ROW     = HexColor("#F9F7F3")
BORDER      = HexColor("#D5CFC5")
CALLOUT_BG  = HexColor("#FAF9F3")
# Colorblind-safe data-plot palette suggestions (use in your plotting code):
#   two groups: control "#75A025" (green) vs case "#FF9400" (orange)
#   up/down/ns: up "#FF9400", down "#0279EE", n.s. "#BBBBBB"

RUNNING_TITLE = "Report"   # set via build(running_title=...)

styles = getSampleStyleSheet()
def _add(**kw):
    name = kw.pop("name")
    if name in styles.byName: del styles.byName[name]
    styles.add(ParagraphStyle(name=name, **kw))
_add(name="ReportTitle", fontName="Helvetica-Bold", fontSize=25, textColor=HEADING, spaceAfter=6, leading=30)
_add(name="Subtitle",    fontName="Helvetica",      fontSize=11.5, textColor=PHYLO_GOLD, spaceAfter=4)
_add(name="Attribution", fontName="Helvetica-Oblique", fontSize=9.5, textColor=MUTED, spaceAfter=8)
_add(name="SectionHead", fontName="Helvetica-Bold", fontSize=16, textColor=HEADING, spaceBefore=20, spaceAfter=8)
_add(name="SubHead",     fontName="Helvetica-Bold", fontSize=12, textColor=HEADING, spaceBefore=12, spaceAfter=5)
_add(name="Body",        fontName="Helvetica",      fontSize=10, textColor=BODY, alignment=TA_JUSTIFY, spaceAfter=8, leading=14.5)
_add(name="Caption",     fontName="Helvetica-Oblique", fontSize=8.5, textColor=MUTED, alignment=TA_CENTER, spaceAfter=14)
_add(name="CellL",       fontName="Helvetica",      fontSize=8.5, textColor=BODY, alignment=TA_LEFT, leading=11)
_add(name="CellC",       fontName="Helvetica",      fontSize=8.5, textColor=BODY, alignment=TA_CENTER, leading=11)
_add(name="CellHdr",     fontName="Helvetica-Bold", fontSize=8.5, textColor=HDR_FG, alignment=TA_CENTER, leading=11)
_add(name="Callout",     fontName="Helvetica",      fontSize=9.5, textColor=BODY, alignment=TA_LEFT, leading=14)
_add(name="RefItem",     fontName="Helvetica",      fontSize=9,   textColor=BODY, alignment=TA_LEFT, leading=13, spaceAfter=6, leftIndent=14, firstLineIndent=-14)
_add(name="Bullet",      fontName="Helvetica",      fontSize=10,  textColor=BODY, alignment=TA_LEFT, leading=14, spaceAfter=6, leftIndent=14, firstLineIndent=-14)

def _page_chrome(canvas, doc):
    canvas.saveState(); w, h = letter
    canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED); canvas.drawString(60, h-40, RUNNING_TITLE)
    canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60, h-48, w-60, h-48)
    canvas.setStrokeColor(BORDER); canvas.setLineWidth(0.75); canvas.line(60, 40, w-60, 40)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED); canvas.drawCentredString(w/2, 26, f"Page {doc.page}")
    canvas.restoreState()

# ---- flowable helpers ----
P  = lambda t, s="Body": Paragraph(t, styles[s])
SP = lambda h=8: Spacer(1, h)

def divider():
    return HRFlowable(width=480, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)

def callout(text):
    t = Table([[Paragraph(text, styles["Callout"])]], colWidths=[470])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),CALLOUT_BG),("BOX",(0,0),(-1,-1),0.5,BORDER),
        ("LINEBEFORE",(0,0),(0,-1),3,PHYLO_GOLD),("TOPPADDING",(0,0),(-1,-1),11),("BOTTOMPADDING",(0,0),(-1,-1),11),
        ("LEFTPADDING",(0,0),(-1,-1),14),("RIGHTPADDING",(0,0),(-1,-1),14)]))
    t.hAlign = "CENTER"; return t

def mktable(headers, rows, colWidths, align_first_left=True):
    data = [[Paragraph(h, styles["CellHdr"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c), styles["CellL"] if (j==0 and align_first_left) else styles["CellC"])
                     for j, c in enumerate(r)])
    t = Table(data, colWidths=colWidths, repeatRows=1)
    ts = [("BACKGROUND",(0,0),(-1,0),PHYLO_GOLD),("GRID",(0,0),(-1,-1),0.5,BORDER),
          ("BOX",(0,0),(-1,-1),0.75,BORDER),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
          ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),("VALIGN",(0,0),(-1,-1),"MIDDLE")]
    for i in range(2, len(data), 2): ts.append(("BACKGROUND",(0,i),(-1,i),ALT_ROW))
    t.setStyle(TableStyle(ts)); t.hAlign = "CENTER"; return t

def figel(path, width, caption, max_h=430):
    """Image at target width; height from the PNG aspect ratio (capped at max_h);
    image+caption kept together so captions never orphan. Returns [] if missing."""
    if not os.path.exists(path):
        return []
    pw, ph = PILImage.open(path).size
    h = width * ph / pw
    if h > max_h:
        h = max_h; width = h * pw / ph
    im = Image(path, width=width, height=h); im.hAlign = "CENTER"
    return [KeepTogether([im, Paragraph(caption, styles["Caption"])])]

def infographic(path, caption="Figure 1. Visual summary of the analysis.", max_h=560):
    """Embed a full-width infographic/summary image (made with the GenerateImage
    TOOL, not drawn here) as the report's opening visual. Preserves aspect ratio,
    caps height so it fits a page, and keeps image+caption together. Returns [] if
    the file is missing so the report still builds."""
    if not os.path.exists(path):
        return []
    # content width for letter with 60pt margins = ~492pt; go near-full-width
    return figel(path, 480, caption, max_h=max_h)

def references_block(refs, heading="References"):
    """Numbered reference list whose numbers MUST match the inline [N] markers used
    in the body. `refs` is an ordered list of formatted citation strings (ideally
    built from LiteratureSearch records: authors, title, journal, year, DOI)."""
    out = [P(heading, "SectionHead")]
    if not refs:
        out.append(P("<i>No external references were cited.</i>", "Body"))
        return out
    for i, r in enumerate(refs, 1):
        out.append(Paragraph(f"{i}. {r}", styles["RefItem"]))
    return out

def nextsteps_block(items, heading="Next Steps"):
    """Bulleted next-steps / recommendations section. `items` is a list of strings."""
    out = [P(heading, "SectionHead")]
    if not items:
        return out
    for it in items:
        out.append(Paragraph(f"&bull;&nbsp; {it}", styles["Bullet"]))
    return out

def fmt(x, nd=3):
    """Scientific notation for very small/large numbers; use <sub>/<super>, not unicode."""
    try:
        xf = float(x)
        if xf != 0 and (abs(xf) < 1e-3 or abs(xf) >= 1e4):
            return f"{xf:.2e}"
        return f"{xf:.{nd}f}"
    except Exception:
        return str(x)

def build(story, out_path, running_title="Report"):
    """Build the PDF fresh from `story`. Returns (n_pages, n_images). Validates with pypdf."""
    global RUNNING_TITLE
    RUNNING_TITLE = running_title
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    doc = SimpleDocTemplate(out_path, pagesize=letter, topMargin=58, bottomMargin=52,
                            leftMargin=60, rightMargin=60, title=running_title, author="Biomni")
    doc.build(story, onFirstPage=_page_chrome, onLaterPages=_page_chrome)
    try:
        from pypdf import PdfReader
        r = PdfReader(out_path)
        n_img = 0
        for pg in r.pages:
            res = pg.get("/Resources") or {}
            if "/XObject" in res:
                xo = res["/XObject"].get_object()
                n_img += sum(1 for o in xo if xo[o].get_object().get("/Subtype") == "/Image")
        print(f"Built {out_path}: {len(r.pages)} pages, {n_img} images, {os.path.getsize(out_path)//1024} KB")
        return len(r.pages), n_img
    except Exception as e:
        print(f"Built {out_path} (validation skipped: {e})")
        return None, None
