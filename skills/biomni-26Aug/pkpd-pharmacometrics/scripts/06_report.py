#!/usr/bin/env python3
"""Stage 6 — assemble the Phylo-branded PDF report for the popPK/PD analysis.

TEMPLATE: adapt paths/labels per run. FOLLOW the `pdf-report-generation` skill (load it) for
the full branding/validation spec — this script is a scaffold implementing that spec, including
the required one-page INFOGRAPHIC and a NEXT STEPS section. Degrades gracefully for PK-only runs
(skips PD/dose sections). ALWAYS validate (pypdf + Read media_output_check) after building.

Env vars (all optional, sensible defaults):
  PKPD_DRUG        compound name in titles/filenames (default 'compound')
  PKPD_PK_ONLY     '1' -> skip PD/dose sections (default '0')
  PKPD_FIGDIR      figure input dir  (default /mnt/results/figures)
  PKPD_OUTDIR      table input dir   (default /mnt/results/tables)
  PKPD_REPORT_OUT  output PDF path   (default /mnt/results/report_<drug>_popPKPD.pdf)
  PKPD_REFS        '||'-delimited reference strings for the References section

Key rules inherited from pdf-report-generation:
- Phylo palette (gold #D4A04A accent); Helvetica; hAlign='CENTER' on every image/table/drawing.
- Use <sub>/<super> tags, NEVER unicode sub/superscripts (render as black boxes).
- KeepTogether(figure+caption); explicit colWidths; write directly to /mnt/results/.
- Dose outputs MUST carry: 'Model-based extrapolation for methodological illustration; not
  clinical dosing guidance.' and cite the target-window source.
"""
import os, glob, datetime
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, PageBreak, HRFlowable, KeepTogether)

# Paths are env-driven (defaults match the rest of the pipeline). This keeps the report portable
# for smoke tests / alternate output roots, matching the other stages' PKPD_FIGDIR/PKPD_OUTDIR.
FIG=os.environ.get("PKPD_FIGDIR","/mnt/results/figures")
TAB=os.environ.get("PKPD_OUTDIR","/mnt/results/tables")
DRUG=os.environ.get("PKPD_DRUG","compound")
OUT=os.environ.get("PKPD_REPORT_OUT", f"/mnt/results/report_{DRUG}_popPKPD.pdf")
PK_ONLY=os.environ.get("PKPD_PK_ONLY","0")=="1"
date_str=datetime.date.today().strftime("%B %d, %Y")

GOLD=HexColor("#D4A04A"); HEAD=HexColor("#111111"); BODY=HexColor("#2C2A26"); MUTED=HexColor("#8A8378")
ALT=HexColor("#F9F7F3"); BORDER=HexColor("#D5CFC5"); CARD=HexColor("#FAF9F3")
S=getSampleStyleSheet()
def add(n,**k):
    if n not in S.byName: S.add(ParagraphStyle(name=n,**k))
add("T",fontName="Helvetica-Bold",fontSize=23,textColor=HEAD,leading=28,spaceAfter=6)
add("Sub",fontName="Helvetica",fontSize=11.5,textColor=GOLD,spaceAfter=4)
add("Attr",fontName="Helvetica-Oblique",fontSize=9.5,textColor=MUTED,spaceAfter=8)
add("H",fontName="Helvetica-Bold",fontSize=15,textColor=HEAD,spaceBefore=16,spaceAfter=7)
add("H2",fontName="Helvetica-Bold",fontSize=11.5,textColor=BODY,spaceBefore=9,spaceAfter=3)
add("B",fontName="Helvetica",fontSize=10,textColor=BODY,alignment=TA_JUSTIFY,spaceAfter=6,leading=14.5)
add("Cap",fontName="Helvetica-Oblique",fontSize=8.5,textColor=MUTED,alignment=TA_CENTER,spaceAfter=12)
add("CH",fontName="Helvetica-Bold",fontSize=8.5,textColor=HexColor("#FFFFFF"),leading=11)
add("CL",fontName="Helvetica",fontSize=8.5,textColor=BODY,leading=11)
add("Card",fontName="Helvetica",fontSize=9.5,textColor=BODY,leading=13,alignment=TA_CENTER)
add("CardNum",fontName="Helvetica-Bold",fontSize=16,textColor=GOLD,leading=18,alignment=TA_CENTER)

def hf(c,doc):
    c.saveState(); w,h=letter; c.setFont("Helvetica",9); c.setFillColor(MUTED)
    c.drawString(60,h-40,f"Population PK/PD — {DRUG}")
    c.setStrokeColor(GOLD); c.setLineWidth(1); c.line(60,h-48,w-60,h-48)
    c.setStrokeColor(BORDER); c.setLineWidth(.75); c.line(60,40,w-60,40)
    c.setFont("Helvetica",8); c.drawCentredString(w/2,26,f"Page {doc.page}"); c.restoreState()
def P(t,s="B"): return Paragraph(t,S[s])
def div(): return HRFlowable(width=480,thickness=1,color=GOLD,spaceAfter=10,spaceBefore=4)
def _ar(p):
    from PIL import Image as I; im=I.open(p); return im.size[1]/im.size[0]
def fig(name,w,cap):
    p=os.path.join(FIG,name)
    if not os.path.exists(p): return None
    im=Image(p,width=w,height=w*_ar(p)); im.hAlign="CENTER"
    return KeepTogether([im,Spacer(1,4),Paragraph(cap,S["Cap"])])
def card(num,label):
    t=Table([[Paragraph(num,S["CardNum"])],[Paragraph(label,S["Card"])]],colWidths=[150])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),CARD),("BOX",(0,0),(-1,-1),0.5,BORDER),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)])); return t
def cards_row(items):
    row=[card(n,l) for n,l in items]; t=Table([row],colWidths=[160]*len(row))
    t.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE")])); t.hAlign="CENTER"; return t
def mktab(rows,widths):
    data=[[Paragraph(str(c),S["CH"]) for c in rows[0]]]+[[Paragraph(str(c),S["CL"]) for c in r] for r in rows[1:]]
    t=Table(data,colWidths=widths,repeatRows=1)
    sty=[("BACKGROUND",(0,0),(-1,0),GOLD),("GRID",(0,0),(-1,-1),0.5,BORDER),("BOX",(0,0),(-1,-1),.75,BORDER),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),("LEFTPADDING",(0,0),(-1,-1),6),
        ("RIGHTPADDING",(0,0),(-1,-1),6),("VALIGN",(0,0),(-1,-1),"MIDDLE")]
    for i in range(2,len(data),2): sty.append(("BACKGROUND",(0,i),(-1,i),ALT))
    t.setStyle(TableStyle(sty)); t.hAlign="CENTER"; return t

def load(name):
    p=os.path.join(TAB,name); return pd.read_csv(p) if os.path.exists(p) else None
pk=load("pk_parameters.csv"); pd_=load("pd_parameters.csv"); wt=load("dose_by_weight.csv")
import pickle
DISCLAIM=("<b>Model-based extrapolation for methodological illustration; not clinical dosing "
          "guidance.</b>")

story=[]
# ---------- Title ----------
story+= [Spacer(1,24), P(f"Population PK/PD Modeling of {DRUG.capitalize()}","T"),
         P("Exposure-Response Analysis"+("" if PK_ONLY else " and Model-Based Dose Recommendation"),"Sub"),
         P(f"<i>Generated by Biomni  |  {date_str}</i>","Attr"), div()]

# ---------- Infographic (one-page visual summary) ----------
story.append(P("Summary","H"))
info=[]
if pk is not None:
    def bt(df,par):
        r=df[df.Parameter==par]; 
        return f"{r['Back-transformed'].values[0]:.3g}" if len(r) and 'Back-transformed' in df else "-"
    info+= [(bt(pk,"tcl"),"CL/F (L/h)"),(bt(pk,"tv"),"V/F (L)"),(bt(pk,"tka"),"k<sub>a</sub> (1/h)")]
if info: story+= [cards_row(info[:3]), Spacer(1,8)]
if not PK_ONLY and wt is not None:
    dm=wt[wt.WT==70]["dose_mid"].values
    if len(dm):
        story.append(cards_row([(f"{dm[0]:.1f} mg/day","Dose @70 kg (target-gated)")]))
        story.append(Spacer(1,6))
        story.append(P("Headline exposure-response result shown above. "+DISCLAIM,"B"))
story.append(div())

# ---------- Intro / Methods ----------
story+= [P("1. Introduction","H"),
  P(f"This report develops a population pharmacokinetic{'' if PK_ONLY else '/pharmacodynamic'} "
    f"model for {DRUG} from concentration-time{'' if PK_ONLY else ' and response'} data using "
    "nonlinear mixed-effects modeling (nlmixr2/rxode2), separating typical parameters from "
    "between-subject and residual variability."),
  P("2. Methods","H"),
  P("<b>Data & validation.</b> The dataset was ingested under a NONMEM-style schema contract with "
    "loud validation (subject/observation/dose counts, data-quality flags, endpoint detection). "
    "See the validation summary. "+
    ("No distinct response endpoint was present, so the analysis is PK-only." if PK_ONLY else
     "A response endpoint was present, enabling PK/PD modeling.")),
  P("<b>PK model.</b> One-compartment first-order absorption/elimination with log-normal "
    "between-subject variability and combined additive+proportional residual error; allometric "
    "weight scaling where weight was available. FOCEi estimation (bobyqa fallback on convergence "
    "issues).")]
if not PK_ONLY:
    story.append(P("<b>PD model.</b> The PD structure was selected from the observed "
      "concentration-to-effect delay (direct Emax / effect-compartment / indirect-response) and "
      "fitted sequentially with PK parameters fixed. See Results for the chosen structure and "
      "its justification."))
    story.append(P("<b>Dose target.</b> A dose was derived only against an explicit, sourced "
      "therapeutic target window (never invented); exposure-response was simulated to steady "
      "state with rxode2."))
story.append(P("<b>Evaluation.</b> Goodness-of-fit, parameter precision (asymptotic %RSE / 95% "
  "CI), condition number, and a visual predictive check (VPC). Bootstrap CIs are an optional "
  "upgrade and were not run by default."))

story.append(PageBreak())
# ---------- Results ----------
story.append(P("3. Results","H"))
story.append(P("3.1 Observed data","H2"))
f=fig("fig_raw_data.png",470,"Figure 1. Observed data vs time (individual lines; gold = mean).");  story.append(f) if f else None
story.append(P("3.2 Population PK","H2"))
if pk is not None:
    cols=[c for c in ["Parameter","Back-transformed","%RSE","BSV(CV%)"] if c in pk.columns]
    rows=[["Parameter","Estimate","%RSE","BSV %CV"]]+[[r["Parameter"],
      f"{r.get('Back-transformed',''):.3g}" if pd.notna(r.get('Back-transformed')) else "-",
      f"{r.get('%RSE',''):.1f}" if pd.notna(r.get('%RSE')) else "-",
      f"{r.get('BSV(CV%)',''):.1f}" if pd.notna(r.get('BSV(CV%)')) else "-"] for _,r in pk.iterrows()]
    story.append(KeepTogether([mktab(rows,[150,90,70,80]),Spacer(1,3),Paragraph("Table 1. Population PK parameters.",S["Cap"])]))
for nm,cap in [("fig_pk_gof.png","Figure 2. PK goodness-of-fit."),
               ("fig_pk_vpc.png","Figure 3. PK visual predictive check.")]:
    f=fig(nm,440,cap); story.append(f) if f else None
if not PK_ONLY and pd_ is not None:
    story.append(P("3.3 Pharmacodynamics","H2"))
    struct=pd_["PD_structure"].iloc[0] if "PD_structure" in pd_.columns else "selected"
    story.append(P(f"Chosen PD structure: <b>{struct}</b> (selected from the observed "
      "concentration-effect delay)."))
    rows=[["Parameter","Estimate","%RSE","BSV %CV"]]+[[r["Parameter"],
      f"{r.get('Back-transformed',''):.3g}" if pd.notna(r.get('Back-transformed')) else "-",
      f"{r.get('%RSE',''):.1f}" if pd.notna(r.get('%RSE')) else "-",
      f"{r.get('BSV(CV%)',''):.1f}" if pd.notna(r.get('BSV(CV%)')) else "-"] for _,r in pd_.iterrows()]
    story.append(KeepTogether([mktab(rows,[150,90,70,80]),Spacer(1,3),Paragraph("Table 2. Population PD parameters.",S["Cap"])]))
    for nm,cap in [("fig_pd_gof.png","Figure 4. PD goodness-of-fit."),
                   ("fig_pd_vpc.png","Figure 5. PD visual predictive check."),
                   ("fig_exposure_response.png","Figure 6. Exposure-response with therapeutic band."),
                   ("fig_ss_profile.png","Figure 7. Steady-state approach at recommended dose.")]:
        f=fig(nm,450,cap); story.append(f) if f else None
    if wt is not None:
        rows=[["Body weight","Dose (mg/day)","Range (mg/day)","mg/kg/day"]]+[
          [f"{int(r.WT)} kg",f"{r.dose_mid:.1f}",
           f"{min(r.dose_lo,r.dose_hi):.1f}&ndash;{max(r.dose_lo,r.dose_hi):.1f}" if pd.notna(r.dose_hi) else f"&gt;{r.dose_lo:.1f}",
           f"{r.dose_per_kg:.3f}"] for _,r in wt.iterrows()]
        story.append(KeepTogether([mktab(rows,[90,120,150,80]),Spacer(1,3),
          Paragraph("Table 3. Target-gated dose by body weight. "+DISCLAIM,S["Cap"])]))

# ---------- Conclusions + Next steps ----------
story.append(P("4. Conclusions","H"))
story.append(P(f"A population {'PK' if PK_ONLY else 'PK/PD'} model was fitted and evaluated for "
  f"{DRUG}. "+("" if PK_ONLY else "An exposure-response analysis translated the model into a "
  "target-gated dose recommendation. "+DISCLAIM)))
story.append(P("5. Next Steps","H"))
nxt=["Prospectively study the recommended and bracketing dose levels to confirm the extrapolated exposure-response.",
     "Move from a sequential to a simultaneous PK/PD fit to propagate PK uncertainty.",
     "Expand covariate/pharmacogenetic testing (e.g. weight, age, sex, relevant genotypes) in a larger cohort.",
     "Add a bootstrap for empirical parameter confidence intervals (opt-in, compute-heavy).",
     "External validation in an independent cohort before any applied use."]
for n in nxt: story.append(P("&bull; "+n,"B"))

# ---------- References (fill from LiteratureSearch; verified DOIs) ----------
story.append(P("References","H"))
refs=os.environ.get("PKPD_REFS","").split("||")
for r in [x for x in refs if x.strip()]:
    story.append(Paragraph(r,ParagraphStyle("r",parent=S["B"],fontSize=8.5,leading=12,spaceAfter=5,textColor=MUTED)))
if not any(x.strip() for x in refs):
    story.append(P("<i>Populate via LiteratureSearch — target-window and parameter citations with DOIs.</i>","B"))

SimpleDocTemplate(OUT,pagesize=letter,topMargin=52,bottomMargin=52,leftMargin=60,rightMargin=60).build(
    story,onFirstPage=hf,onLaterPages=hf)
print("PDF:",OUT,os.path.getsize(OUT),"bytes")
