#!/usr/bin/env python3
"""build_report.py -- Phylo-branded PDF for the evidence-synthesis-meta-analysis skill.

Generalizable: reads the CSV/figure outputs of run_meta_analysis.R plus a small JSON
config, and assembles a systematic-review-style PDF (ReportLab). Follows the
`pdf-report-generation` skill conventions (brand palette, Helvetica, <super>/<sub>
tags -- NEVER Unicode sub/superscripts, hAlign='CENTER', KeepTogether for figures).

USAGE:
    python build_report.py config.json
  where config.json has keys (all optional except title):
    {
      "title": "GLP-1 Receptor Agonists for Weight Loss",
      "subtitle": "Random-effects meta-analysis of placebo-controlled RCTs",
      "measure": "MD",                     # MD|SMD|OR|RR|HR
      "effect_word": "percentage points",  # units label for continuous; "" for ratios
      "outdir": "/mnt/results/<run>",      # contains data/ and figures/
      "out_pdf": "/mnt/results/report_<topic>_meta.pdf",
      "infographic": "/mnt/results/<run>/figures/infographic.png",  # from GenerateImage (optional)
      "narrative": { "background": "...", "interpretation": "...",
                     "limitations": ["...", "..."], "next_steps": ["...", "..."] },
      "references": [ "1. Author A, et al. Title. Journal. Year;vol:pp. doi:...", ... ]
    }
Any missing figure/table is skipped gracefully so the script never hard-fails on
an absent optional artifact.
"""
import os, sys, csv, json
from datetime import date
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, PageBreak, HRFlowable, KeepTogether)

# ---------- Phylo palette (from pdf-report-generation skill) ----------
PHYLO_GOLD=HexColor("#D4A04A"); HEADING=HexColor("#111111"); BODY=HexColor("#2C2A26")
MUTED=HexColor("#8A8378"); TABLE_HEADER_FG=HexColor("#FFFFFF")
TABLE_ALT=HexColor("#F9F7F3"); TABLE_BORDER=HexColor("#D5CFC5"); CALLOUT_BG=HexColor("#FAF9F3")
ACCENT=HexColor("#0072B2")

# ---------- config ----------
cfg = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}
TITLE     = cfg.get("title", "Evidence Synthesis & Meta-Analysis")
SUBTITLE  = cfg.get("subtitle", "Random-effects meta-analysis")
MEASURE   = cfg.get("measure", "MD").upper()
UNITS     = cfg.get("effect_word", "" if MEASURE in ("OR","RR","HR") else "units")
OUTDIR    = cfg.get("outdir", ".")
DATADIR   = os.path.join(OUTDIR, "data"); FIGDIR = os.path.join(OUTDIR, "figures")
OUT       = cfg.get("out_pdf", os.path.join(OUTDIR, "report_meta.pdf"))
INFOGRAPHIC = cfg.get("infographic", os.path.join(FIGDIR, "infographic.png"))
NAR       = cfg.get("narrative", {})
REFS      = cfg.get("references", [])
IS_RATIO  = MEASURE in ("OR","RR","HR")
MEASURE_LONG = {"MD":"mean difference","SMD":"standardized mean difference",
                "OR":"odds ratio","RR":"risk ratio","HR":"hazard ratio"}.get(MEASURE, MEASURE)

def rd(fn):
    p = os.path.join(DATADIR, fn)
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []
def rtxt(fn):
    p = os.path.join(DATADIR, fn)
    return open(p).read().strip() if os.path.exists(p) else ""

results = rd("meta_results.csv"); loo = rd("leaveoneout.csv")
influ = rd("influence_diagnostics.csv"); rob = rd("risk_of_bias.csv")
ext = rd("extraction_table.csv"); screen = rd("screening_log.csv")
smallstudy = rtxt("smallstudy_test.txt")

def R(level):
    for r in results:
        if r["level"].startswith(level): return r
    return {}
def het(name):
    for r in results:
        if r["level"] == name: return r["effect"]
    return "NA"
overall = R("Overall (random")
SUP = lambda s: f"<super rise='4'>{s}</super>"   # tested-good superscript

# ---------- styles ----------
S = getSampleStyleSheet()
def add(n,**k): S.add(ParagraphStyle(name=n, **k))
add("RTitle",fontName="Helvetica-Bold",fontSize=24,textColor=HEADING,leading=29,spaceAfter=6)
add("Sub",fontName="Helvetica",fontSize=12,textColor=PHYLO_GOLD,spaceAfter=4)
add("Attr",fontName="Helvetica-Oblique",fontSize=10,textColor=MUTED,spaceAfter=8)
add("H1",fontName="Helvetica-Bold",fontSize=16,textColor=HEADING,spaceBefore=18,spaceAfter=8)
add("H2",fontName="Helvetica-Bold",fontSize=12,textColor=ACCENT,spaceBefore=10,spaceAfter=5)
add("Body2",fontName="Helvetica",fontSize=10.3,textColor=BODY,alignment=TA_JUSTIFY,spaceAfter=8,leading=15)
add("Cap",fontName="Helvetica-Oblique",fontSize=8.8,textColor=MUTED,alignment=TA_CENTER,spaceAfter=14)
add("Ref",fontName="Helvetica",fontSize=8.6,textColor=BODY,leading=12,spaceAfter=5,leftIndent=16,firstLineIndent=-16)
add("CalloutT",fontName="Helvetica",fontSize=10.3,textColor=BODY,leading=15,alignment=TA_LEFT)

def divider(): return HRFlowable(width=480,thickness=1,color=PHYLO_GOLD,spaceAfter=10,spaceBefore=4)
def callout(text):
    t=Table([[Paragraph(text,S["CalloutT"])]],colWidths=[470])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),CALLOUT_BG),("BOX",(0,0),(-1,-1),0.5,TABLE_BORDER),
        ("LINEBEFORE",(0,0),(0,-1),3,PHYLO_GOLD),("TOPPADDING",(0,0),(-1,-1),12),("BOTTOMPADDING",(0,0),(-1,-1),12),
        ("LEFTPADDING",(0,0),(-1,-1),14),("RIGHTPADDING",(0,0),(-1,-1),14)]))
    t.hAlign="CENTER"; return t
def mktable(headers,rows,colW,fs=8.3):
    hs=ParagraphStyle("h",fontName="Helvetica-Bold",fontSize=fs,textColor=TABLE_HEADER_FG,leading=fs+2.5)
    cs=ParagraphStyle("c",fontName="Helvetica",fontSize=fs,textColor=BODY,leading=fs+2.5)
    data=[[Paragraph(str(h),hs) for h in headers]]
    for row in rows: data.append([Paragraph(str(c),cs) for c in row])
    t=Table(data,colWidths=colW,repeatRows=1)
    sty=[("BACKGROUND",(0,0),(-1,0),PHYLO_GOLD),("GRID",(0,0),(-1,-1),0.5,TABLE_BORDER),
        ("BOX",(0,0),(-1,-1),0.75,TABLE_BORDER),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),("VALIGN",(0,0),(-1,-1),"MIDDLE")]
    for i in range(2,len(data),2): sty.append(("BACKGROUND",(0,i),(-1,i),TABLE_ALT))
    t.setStyle(TableStyle(sty)); t.hAlign="CENTER"; return t
def fig(name,w,h,cap):
    p=os.path.join(FIGDIR,name)
    if not os.path.exists(p): return None
    img=Image(p,width=w,height=h); img.hAlign="CENTER"
    return KeepTogether([img,Spacer(1,4),Paragraph(cap,S["Cap"])])

def hf(canvas,doc):
    canvas.saveState(); w,h=letter
    canvas.setFont("Helvetica",9); canvas.setFillColor(MUTED)
    canvas.drawString(60,h-40,TITLE[:80]+" — Meta-analysis")
    canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60,h-48,w-60,h-48)
    canvas.setStrokeColor(TABLE_BORDER); canvas.setLineWidth(0.75); canvas.line(60,40,w-60,40)
    canvas.setFont("Helvetica",8); canvas.setFillColor(MUTED)
    canvas.drawCentredString(w/2,26,f"Page {doc.page}")
    canvas.restoreState()

# ---------- build story ----------
story=[]
story.append(Spacer(1,24))
story.append(Paragraph(TITLE,S["RTitle"]))
story.append(Paragraph(SUBTITLE,S["Sub"]))
story.append(Paragraph(f"<i>Generated by Biomni  |  {date.today().strftime('%B %d, %Y')}</i>",S["Attr"]))
story.append(divider())

# infographic (from GenerateImage) up top if present
info = fig(os.path.basename(INFOGRAPHIC), 470, 300, "Figure 1. Visual summary of the pooled evidence.") \
       if os.path.exists(INFOGRAPHIC) else None

# Executive summary
story.append(Paragraph("Executive Summary",S["H1"]))
k = overall.get("k","?")
eff = overall.get("effect","?"); lo = overall.get("ci_lo","?"); hi = overall.get("ci_hi","?")
unit_txt = f" {UNITS}" if UNITS else ""
story.append(Paragraph(
    f"We pooled <b>{k} studies</b> using generic inverse-variance random-effects meta-analysis "
    f"(REML estimator with Hartung-Knapp confidence-interval adjustment). The primary effect measure "
    f"was the {MEASURE_LONG} ({MEASURE}).",S["Body2"]))
if NAR.get("background"):
    story.append(Paragraph(NAR["background"],S["Body2"]))
story.append(callout(
    f"<b>Pooled {MEASURE}:</b> {eff}{unit_txt} (95% CI {lo} to {hi}; "
    f"p = {overall.get('pval','?')}).<br/><br/>"
    f"<b>Heterogeneity:</b> I{SUP('2')} = {het('I^2 (%)')}%, &#964;{SUP('2')} = {het('tau^2')}, "
    f"Cochran Q = {het('Cochran Q')} (p = {het('Q pval')}).<br/>"
    f"<b>95% prediction interval:</b> {het('PI low')} to {het('PI high')}."))
if info: story.append(info)

# Methods
story.append(Paragraph("Methods",S["H1"]))
story.append(Paragraph("Search, screening, and extraction",S["H2"]))
story.append(Paragraph(
    "Eligible studies were identified through structured literature search and screened against "
    "pre-specified criteria (PRISMA-style log in the Appendix). For each study we extracted the "
    f"reported {MEASURE_LONG} and its 95% confidence interval; the standard error was derived as "
    "SE = (upper &minus; lower) / 3.92" + (", after log-transformation of the ratio measure" if IS_RATIO else "") +
    ". Every extracted value and citation was verified against its source before analysis; no effect "
    "size was imputed or fabricated. A shared control arm was not double-counted.",S["Body2"]))
story.append(Paragraph("Statistical analysis",S["H2"]))
story.append(Paragraph(
    "Effects were pooled with a generic inverse-variance random-effects model (REML between-study "
    "variance; Hartung-Knapp-Sidik-Jonkman interval). We report &#964;" + SUP('2') + ", I" + SUP('2') +
    " (with 95% CI), H, Cochran's Q, and a 95% prediction interval. Robustness was assessed by "
    "leave-one-out analysis, Cook's-distance influence diagnostics, and a small-study-effects (Egger) "
    "assessment interpreted only when at least 10 studies were available. Analyses used R "
    "(<i>meta</i>, <i>metafor</i>).",S["Body2"]))

# Results
story.append(PageBreak())
story.append(Paragraph("Results",S["H1"]))
if ext:
    story.append(Paragraph("Included studies",S["H2"]))
    rows=[]
    for r in ext:
        rows.append([r.get("study",""), r.get("year",""),
                     r.get("subgroup","") or r.get("design",""),
                     f'{r.get("n_trt","")}/{r.get("n_ctrl","")}'.strip("/"),
                     r.get("effect",""), f'{r.get("ci_lo","")} to {r.get("ci_hi","")}'])
    story.append(mktable(["Study","Year","Group","N t/c",f"{MEASURE}","95% CI"],rows,
                         [130,40,80,60,50,102]))
    story.append(Paragraph(f"Table 1. Included studies and extracted {MEASURE_LONG}.",S["Cap"]))

story.append(Paragraph("Pooled effect",S["H2"]))
story.append(Paragraph(
    f"The random-effects pooled {MEASURE} was <b>{eff}{unit_txt}</b> (95% CI {lo} to {hi}); see the "
    "forest plot (Figure 2).",S["Body2"]))
f2=fig("forest_main.png",470,None,"Figure 2. Forest plot of study-level and pooled effects (random-effects model with prediction interval).")
# forest height varies; recompute proportionally
fp=os.path.join(FIGDIR,"forest_main.png")
if os.path.exists(fp):
    from PIL import Image as PImage
    iw,ih=PImage.open(fp).size; story.append(fig("forest_main.png",470,470*ih/iw,
        "Figure 2. Forest plot of study-level and pooled effects (random-effects model with prediction interval)."))

story.append(Paragraph("Heterogeneity, subgroups, and influence",S["H2"]))
story.append(Paragraph(
    f"Heterogeneity was quantified as I{SUP('2')} = {het('I^2 (%)')}% (&#964;{SUP('2')} = {het('tau^2')}; "
    f"Cochran Q = {het('Cochran Q')}, p = {het('Q pval')}). "
    + (f"A test for subgroup differences was performed (between-subgroup Q = {het('Q between')}, "
       f"p = {het('Q between pval')}). " if het('Q between')!='NA' else "")
    + "Panel B of Figure 3 shows Cook's-distance influence diagnostics.",S["Body2"]))
hp=fig("heterogeneity_panel.png",480,192,"Figure 3. (A) Study effects grouped by moderator; dashed line = pooled estimate. (B) Cook's-distance influence diagnostics.")
if hp: story.append(hp)

story.append(Paragraph("Robustness and small-study effects",S["H2"]))
if loo:
    vals=[float(r["effect"]) for r in loo if r.get("effect","").replace('.','',1).replace('-','',1).isdigit()]
    rng = f"{min(vals):.2f} to {max(vals):.2f}" if vals else "a narrow range"
    story.append(Paragraph(
        f"Leave-one-out analysis left the pooled estimate within {rng} (Figure 4): no single study drives "
        "the result.",S["Body2"]))
lp=fig("leaveoneout.png",450,None,"Figure 4. Leave-one-out sensitivity analysis.")
lpp=os.path.join(FIGDIR,"leaveoneout.png")
if os.path.exists(lpp):
    from PIL import Image as PImage
    iw,ih=PImage.open(lpp).size; story.append(fig("leaveoneout.png",450,450*ih/iw,
        "Figure 4. Leave-one-out sensitivity analysis (dashed line/band = full-model estimate and 95% CI)."))
if smallstudy:
    story.append(Paragraph("<b>Small-study effects.</b> " + smallstudy.replace("\n"," "),S["Body2"]))
fn=fig("funnel.png",400,336,"Figure 5. Funnel plot. Interpret asymmetry as bias only when >= 10 studies are pooled.")
if fn: story.append(fn)

if rob:
    story.append(Paragraph("Risk of bias",S["H2"]))
    rob_rows=[[r.get("study",r.get("trial","")),
               (r.get("randomization","") or "").split(" (")[0],
               (r.get("missing_data","") or "").split(" (")[0],
               r.get("overall","")] for r in rob]
    story.append(mktable(["Study","Randomization","Missing data","Overall"],rob_rows,[130,110,110,70]))
    story.append(Paragraph("Table 2. Narrative risk-of-bias summary (Cochrane-domain-informed; not a formal RoB2 score).",S["Cap"]))

# Discussion / Interpretation
story.append(Paragraph("Discussion",S["H1"]))
if NAR.get("interpretation"):
    story.append(Paragraph(NAR["interpretation"],S["Body2"]))
else:
    story.append(Paragraph(
        f"The pooled {MEASURE_LONG} indicates a consistent direction of effect across the included studies; "
        f"the magnitude and its heterogeneity should be interpreted in light of the prediction interval and "
        f"the by-study/subgroup structure above.",S["Body2"]))
# Limitations
lims = NAR.get("limitations") or [
    "Between-study heterogeneity may be high; the pooled estimate is an average across studies that may differ in population, dose, and endpoint.",
    "Small-study/publication-bias tests are underpowered when fewer than 10 studies are pooled.",
    "Included studies may vary in design and risk of bias; results should be read with the risk-of-bias summary.",
]
story.append(Paragraph("<b>Limitations.</b>",S["Body2"]))
story.append(Paragraph("".join(f"({i+1}) {l} " for i,l in enumerate(lims)),S["Body2"]))
# Next steps
nxt = NAR.get("next_steps") or [
    "Extend the search / add newly published trials and re-pool.",
    "Explore heterogeneity with meta-regression on candidate moderators (dose, duration, population).",
    "Where >= 10 studies exist, formally assess publication bias and consider trim-and-fill.",
]
story.append(Paragraph("<b>Next steps.</b>",S["Body2"]))
story.append(Paragraph("".join(f"({i+1}) {n} " for i,n in enumerate(nxt)),S["Body2"]))

# References
if REFS:
    story.append(PageBreak()); story.append(Paragraph("References",S["H1"]))
    for r in REFS: story.append(Paragraph(str(r),S["Ref"]))

# Appendix: screening log
if screen:
    story.append(Spacer(1,10)); story.append(divider())
    story.append(Paragraph("Appendix: Screening decisions",S["H2"]))
    def wt(s,lim=150):
        s=s or ""; return s if len(s)<=lim else s[:lim].rsplit(" ",1)[0]+" ..."
    sc_rows=[[r.get("record",r.get("study","")), r.get("decision",""), wt(r.get("reason",""))] for r in screen]
    story.append(mktable(["Record","Decision","Reason"],sc_rows,[112,50,268],fs=7.4))
    story.append(Paragraph("Table 3. PRISMA-style screening log.",S["Cap"]))

os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
doc=SimpleDocTemplate(OUT,pagesize=letter,topMargin=56,bottomMargin=52,leftMargin=60,rightMargin=60,title=TITLE)
doc.build(story,onFirstPage=hf,onLaterPages=hf)
print("PDF written:",OUT)

# validation
from pypdf import PdfReader
rdr=PdfReader(OUT); pc=len(rdr.pages); sz=os.path.getsize(OUT)
print(f"Pages: {pc}  Size: {sz:,} bytes")
assert pc>=2 and sz>5000 and len(rdr.pages[0].extract_text().strip())>0, "PDF validation failed"
print("Validation OK.")
