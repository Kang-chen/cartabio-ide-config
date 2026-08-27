"""
report_style.py — shared ReportLab styles & palette for signature-response-enrichment.

Imported by build_report.py so styles/colors are defined once, not re-derived per run.
Colors and fonts match the worked example (colorblind-safe Okabe-Ito).

GLYPH SAFETY (Helvetica / ReportLab):
  SAFE   : &mdash; &ndash; &#916;(dGSVA) &#945;(a) &#946;(b) &#947;(g) &#954;(k)
           &#8226;(bullet) &minus; &#215;(x) &#177;(+/-) &#8776;(~=) &#8805;(>=)
           &#8242;(prime) &rarr;(->) &lt; &gt; &amp; &lsquo; &rsquo;
  UNSAFE : &nbsp;  &#8209;(non-breaking hyphen)   <- render as .notdef black squares.
           Never emit these; use plain ASCII space / hyphen.
"""
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

# ---- palette (Okabe-Ito, colorblind-safe) ----
COL_NR = "#D55E00"     # non-responders (orange)
COL_R = "#0072B2"      # responders (blue)
COL_VOLC_A = "#E69F00" # volcano signature A (amber)
COL_VOLC_B = "#7E2F8E" # volcano signature B (purple)
COL_PD = "#117733"     # pharmacodynamic (green)
PHYLO_GOLD = "#D4A04A" # accent
INK = "#222222"
MUTED = "#555555"
RULE = "#BBBBBB"

FONT = "Helvetica"
FONT_B = "Helvetica-Bold"
FONT_I = "Helvetica-Oblique"

# sig marks used throughout: * nominal p<0.05, ** FDR<0.05
SIG_LEGEND = "* nominal p&lt;0.05, ** FDR&lt;0.05"


def build_styles():
    ss = getSampleStyleSheet()
    S = {}
    S["Title"] = ParagraphStyle("Title", parent=ss["Title"], fontName=FONT_B,
                                fontSize=18, leading=22, textColor=colors.HexColor(INK),
                                spaceAfter=4)
    S["Subtitle"] = ParagraphStyle("Subtitle", parent=ss["Normal"], fontName=FONT,
                                   fontSize=11, leading=14, textColor=colors.HexColor(MUTED),
                                   spaceAfter=2)
    S["Attribution"] = ParagraphStyle("Attribution", parent=ss["Normal"], fontName=FONT_I,
                                      fontSize=8.5, leading=11,
                                      textColor=colors.HexColor(MUTED), spaceAfter=6)
    S["SectionHead"] = ParagraphStyle("SectionHead", parent=ss["Heading1"], fontName=FONT_B,
                                      fontSize=13, leading=16, textColor=colors.HexColor(INK),
                                      spaceBefore=10, spaceAfter=4)
    S["SubHead"] = ParagraphStyle("SubHead", parent=ss["Heading2"], fontName=FONT_B,
                                  fontSize=10.5, leading=13, textColor=colors.HexColor(INK),
                                  spaceBefore=6, spaceAfter=2)
    S["Body"] = ParagraphStyle("Body", parent=ss["Normal"], fontName=FONT,
                               fontSize=9.5, leading=13, textColor=colors.HexColor(INK),
                               alignment=TA_LEFT, spaceAfter=5)
    S["Caption"] = ParagraphStyle("Caption", parent=ss["Normal"], fontName=FONT,
                                  fontSize=8, leading=10.5, textColor=colors.HexColor(MUTED),
                                  alignment=TA_LEFT, spaceBefore=3, spaceAfter=8)
    S["TableCell"] = ParagraphStyle("TableCell", parent=ss["Normal"], fontName=FONT,
                                    fontSize=7.2, leading=8.8, textColor=colors.HexColor(INK))
    S["TableHead"] = ParagraphStyle("TableHead", parent=ss["Normal"], fontName=FONT_B,
                                    fontSize=7.4, leading=9, textColor=colors.white)
    S["Callout"] = ParagraphStyle("Callout", parent=ss["Normal"], fontName=FONT,
                                  fontSize=9.5, leading=13, textColor=colors.HexColor(INK),
                                  backColor=colors.HexColor("#FBF3E2"),
                                  borderColor=colors.HexColor(PHYLO_GOLD), borderWidth=0.8,
                                  borderPadding=7, spaceBefore=4, spaceAfter=8)
    S["CalloutHead"] = ParagraphStyle("CalloutHead", parent=S["Callout"], fontName=FONT_B,
                                      spaceBefore=14, spaceAfter=4, borderWidth=0,
                                      backColor=None, borderPadding=0)
    return S


def table_style(header_bg=PHYLO_GOLD):
    """Standard result-table styling: gold header, zebra body, thin grid."""
    from reportlab.platypus import TableStyle
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), FONT_B),
        ("FONTSIZE", (0, 0), (-1, -1), 7.2),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(RULE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F5F5F5")]),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ])
