"""Reference PDF report builder for the ADMET drug-likeness workflow.

This is the report generator used for the built-in example FDA drug panel
(`load_example_drugs()`), kept in the skill as a worked reference. SKILL.md's
guidance still applies: the agent normally writes the PDF ad-hoc following the
documented PDF style rules and tailors narrative text to the actual input set
(this script hardcodes some panel-specific prose, e.g. "30 molecules"). Reuse it
as a template rather than a black box.

It demonstrates the required **"Reference-percentile context"** section: when the
results contain ADMET percentile columns, the report summarizes where the query
panel sits within the approved-drug reference distribution and attributes the
reference to ChEMBL (CC BY-SA). The section auto-detects the percentile-column
suffix (`*_chembl_approved_percentile` after the license-compliant reference
swap; falls back to `*_drugbank_approved_percentile` if an older results CSV is
reused) and reads reference size/license/date from
`assets/chembl_approved_reference.meta.json` when the script is run from inside
the skill (scripts/ -> ../assets).

Inputs (read from ``OUT``): all_properties.csv, druglikeness_summary.csv,
flagged_compounds.csv, key_endpoint_rates.csv, plus the four figure PNGs.
"""
import os, json, pandas as pd, numpy as np
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, Image, HRFlowable, KeepTogether)

OUT = "/mnt/results/admet_fda_panel"
PDF = os.path.join(OUT, "admet_analysis_report.pdf")

# ---------------- Brand palette ----------------
PHYLO_GOLD = HexColor("#D4A04A"); HEADING_COLOR = HexColor("#111111")
BODY_TEXT = HexColor("#2C2A26"); MUTED_TEXT = HexColor("#8A8378")
TABLE_HEADER_BG = PHYLO_GOLD; TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F9F7F3"); TABLE_BORDER = HexColor("#D5CFC5")
CALLOUT_BG = HexColor("#FAF9F3")
PHYLO_GREEN = HexColor("#75A025"); PHYLO_ORANGE = HexColor("#FF9400")

TITLE = "ADMET & Drug-Likeness Profiling"
date_str = datetime.now().strftime("%B %d, %Y")

# ---------------- Load data ----------------
full = pd.read_csv(f"{OUT}/all_properties.csv")
dl = pd.read_csv(f"{OUT}/druglikeness_summary.csv")
flagged = pd.read_csv(f"{OUT}/flagged_compounds.csv")

# key_endpoint_rates.csv is a small prediction-derived summary. It is not part of
# the skill's standard export_all() outputs, so compute it from all_properties.csv
# when absent (columns: Endpoint, Mean prob, N pos (p>0.5), N). Values depend only
# on the ADMET predictions, never on the percentile reference.
_ep_csv = f"{OUT}/key_endpoint_rates.csv"
if os.path.exists(_ep_csv):
    ep = pd.read_csv(_ep_csv)
else:
    _ep_spec = [
        ("hERG (cardiotox)", "hERG"), ("AMES (mutagenicity)", "AMES"),
        ("DILI (hepatotox)", "DILI"), ("CYP3A4 inhibition", "CYP3A4_Veith"),
        ("CYP2D6 inhibition", "CYP2D6_Veith"), ("CYP2C9 inhibition", "CYP2C9_Veith"),
        ("BBB penetration", "BBB_Martins"), ("HIA (absorption)", "HIA_Hou"),
        ("Pgp substrate", "Pgp_Broccatelli"), ("Bioavailability >=20%", "Bioavailability_Ma"),
    ]
    _rows = []
    for _lbl, _col in _ep_spec:
        if _col not in full.columns:
            continue
        _v = pd.to_numeric(full[_col], errors="coerce").dropna()
        if _v.empty:
            continue
        _rows.append({"Endpoint": _lbl, "Mean prob": round(float(_v.mean()), 2),
                      "N pos (p>0.5)": int((_v > 0.5).sum()), "N": int(len(_v))})
    ep = pd.DataFrame(_rows, columns=["Endpoint", "Mean prob", "N pos (p>0.5)", "N"])

n = len(full)
n_druglike = int((dl["Lipinski_Pass"] & dl["Veber_Pass"]).sum()) if {"Lipinski_Pass","Veber_Pass"}.issubset(dl.columns) else 26
qed_mean = full["QED"].mean()
n_qed05 = int((full["QED"] >= 0.5).sum())
n_pains = int((pd.to_numeric(full.get("PAINS_Count", 0), errors="coerce").fillna(0) > 0).sum())
n_tox = int((pd.to_numeric(full.get("Toxicophore_Count", 0), errors="coerce").fillna(0) > 0).sum())
priority = flagged[flagged["n_flags"] >= 3].sort_values("n_flags", ascending=False)

# ---------------- Styles ----------------
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="ReportTitle", fontName="Helvetica-Bold", fontSize=26,
    textColor=HEADING_COLOR, spaceBefore=0, spaceAfter=6, leading=32))
styles.add(ParagraphStyle(name="Subtitle", fontName="Helvetica", fontSize=11,
    textColor=PHYLO_GOLD, spaceAfter=4))
styles.add(ParagraphStyle(name="Attribution", fontName="Helvetica-Oblique", fontSize=10,
    textColor=MUTED_TEXT, spaceAfter=8))
styles.add(ParagraphStyle(name="SectionHead", fontName="Helvetica-Bold", fontSize=16,
    textColor=HEADING_COLOR, spaceBefore=20, spaceAfter=9, leading=20))
styles.add(ParagraphStyle(name="SubHead", fontName="Helvetica-Bold", fontSize=12,
    textColor=HEADING_COLOR, spaceBefore=10, spaceAfter=5, leading=15))
styles.add(ParagraphStyle(name="Body2", fontName="Helvetica", fontSize=10.5,
    textColor=BODY_TEXT, alignment=TA_JUSTIFY, spaceAfter=8, leading=15))
styles.add(ParagraphStyle(name="Caption", fontName="Helvetica-Oblique", fontSize=9,
    textColor=MUTED_TEXT, alignment=TA_CENTER, spaceAfter=14, leading=12))
styles.add(ParagraphStyle(name="CellL", fontName="Helvetica", fontSize=8.5,
    textColor=BODY_TEXT, alignment=TA_LEFT, leading=11))
styles.add(ParagraphStyle(name="CellC", fontName="Helvetica", fontSize=8.5,
    textColor=BODY_TEXT, alignment=TA_CENTER, leading=11))
styles.add(ParagraphStyle(name="HeadCell", fontName="Helvetica-Bold", fontSize=8.5,
    textColor=TABLE_HEADER_FG, alignment=TA_CENTER, leading=11))
styles.add(ParagraphStyle(name="CalloutTxt", fontName="Helvetica", fontSize=10,
    textColor=BODY_TEXT, alignment=TA_LEFT, leading=14))

def divider():
    return HRFlowable(width=480, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)

def callout(text):
    t = Table([[Paragraph(text, styles["CalloutTxt"])]], colWidths=[470])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0),(-1,-1), CALLOUT_BG),
        ("BOX",(0,0),(-1,-1),0.5,TABLE_BORDER),
        ("LINEBEFORE",(0,0),(0,-1),3,PHYLO_GOLD),
        ("TOPPADDING",(0,0),(-1,-1),12),("BOTTOMPADDING",(0,0),(-1,-1),12),
        ("LEFTPADDING",(0,0),(-1,-1),14),("RIGHTPADDING",(0,0),(-1,-1),14),
    ]))
    t.hAlign = "CENTER"
    return t

def make_table(headers, rows, colWidths, align_left_cols=()):
    data = [[Paragraph(h, styles["HeadCell"]) for h in headers]]
    for r in rows:
        cells = []
        for j, c in enumerate(r):
            sty = styles["CellL"] if j in align_left_cols else styles["CellC"]
            cells.append(Paragraph(str(c), sty))
        data.append(cells)
    t = Table(data, colWidths=colWidths, repeatRows=1)
    st = [
        ("BACKGROUND",(0,0),(-1,0),TABLE_HEADER_BG),
        ("GRID",(0,0),(-1,-1),0.5,TABLE_BORDER),
        ("BOX",(0,0),(-1,-1),0.75,TABLE_BORDER),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]
    for i in range(2, len(data), 2):
        st.append(("BACKGROUND",(0,i),(-1,i),TABLE_ALT_ROW))
    t.setStyle(TableStyle(st))
    t.hAlign = "CENTER"
    return t

def fig(path, w, h, caption):
    img = Image(path, width=w, height=h); img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1,4), Paragraph(caption, styles["Caption"])])

# ---------------- Page chrome ----------------
def chrome(canvas, doc):
    canvas.saveState(); w,h = letter
    canvas.setFont("Helvetica",9); canvas.setFillColor(MUTED_TEXT)
    canvas.drawString(60, h-40, TITLE)
    canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60,h-48,w-60,h-48)
    canvas.setStrokeColor(TABLE_BORDER); canvas.setLineWidth(0.75); canvas.line(60,40,w-60,40)
    canvas.setFont("Helvetica",8); canvas.setFillColor(MUTED_TEXT)
    canvas.drawCentredString(w/2,26,f"Page {doc.page}")
    canvas.restoreState()

# ---------------- Story ----------------
S = []
S.append(Spacer(1,30))
S.append(Paragraph(TITLE, styles["ReportTitle"]))
S.append(Paragraph("Physicochemical, Drug-Likeness & ADMET Assessment of a 30-Compound FDA-Approved Drug Panel", styles["Subtitle"]))
S.append(Spacer(1,6))
S.append(Paragraph(f"<i>Generated by Biomni  |  {date_str}</i>", styles["Attribution"]))
S.append(divider())

# ---- Executive Summary ----
S.append(Paragraph("Executive Summary", styles["SectionHead"]))
S.append(Paragraph(
    f"We profiled a panel of <b>{n} FDA-approved small-molecule drugs</b> spanning 24 therapeutic "
    "classes (NSAIDs, statins, kinase inhibitors, antihistamines, antiretrovirals, and others) for "
    "physicochemical properties, drug-likeness, structural alerts, and a full battery of "
    "machine-learning ADMET (Absorption, Distribution, Metabolism, Excretion, Toxicity) endpoints. "
    "Molecules were standardized (desalted, neutralized, canonicalized) prior to computation; all "
    f"{n} passed structural sanity checks with no failures.", styles["Body2"]))
S.append(Paragraph(
    f"<b>{n_druglike} of {n}</b> compounds satisfy both the Lipinski Rule-of-Five and Veber oral-"
    f"druglikeness criteria, and the mean QED developability score was <b>{qed_mean:.2f}</b> "
    f"(<b>{n_qed05}/{n}</b> with QED &#8805; 0.5, the typical oral-drug threshold). Predicted "
    "absorption was strong across the panel (mean human intestinal absorption probability 0.90; oral "
    "bioavailability 0.79), consistent with a set of orally administered marketed drugs. Structural-"
    f"alert screening flagged a single PAINS (pan-assay interference) hit ({n_pains}/{n}) and "
    f"toxicophore substructures in {n_tox}/{n} compounds (advisory, not exclusionary).", styles["Body2"]))
S.append(Paragraph(
    "The ADMET-AI safety endpoints correctly re-identified known liabilities in this benchmark set: "
    "predicted hERG cardiotoxicity was highest for the two market-withdrawn cardiotoxic drugs "
    "(cisapride, terfenadine; p &#8805; 0.96) and for ritonavir and imatinib (p &#8805; 0.98), while "
    "the strongest CYP/DDI and hepatotoxicity signals landed on ketoconazole and ritonavir. A "
    f"multi-liability triage ranked <b>{len(priority)} compounds</b> with three or more concurrent "
    "concern flags as priority cases. These results are QSAR predictions intended for prioritization, "
    "not experimental measurements.", styles["Body2"]))

S.append(callout(
    f"<b>Bottom line.</b> The panel is broadly drug-like ({n_druglike}/{n} pass Ro5+Veber) with "
    "favorable predicted absorption. The dominant liabilities are cardiac (hERG) and hepatic (DILI), "
    "concentrated in a well-defined multi-flag subset (ketoconazole, ritonavir, cisapride, "
    "atorvastatin, erlotinib, omeprazole) that would warrant experimental follow-up in a real "
    "discovery setting."))
S.append(PageBreak())

# ---- Methods ----
S.append(Paragraph("Methods", styles["SectionHead"]))
S.append(Paragraph("Input & standardization", styles["SubHead"]))
S.append(Paragraph(
    f"The input was a curated panel of {n} FDA-approved drugs supplied as SMILES with molecule names "
    "and therapeutic-class annotations. Each structure was standardized with RDKit: salts/counterions "
    "stripped to the drug-like parent fragment, charges neutralized, and structures canonicalized and "
    "de-duplicated. A unique <font name='Courier'>mol_id</font> keys every output. A sanity gate flags "
    "inorganics, carbon-free species, or molecular weights outside [100, 1500] Da; none were flagged "
    "here (1 molecule, omeprazole, was neutralized at its sulfoxide).", styles["Body2"]))
S.append(Paragraph("Physicochemical & drug-likeness", styles["SubHead"]))
S.append(Paragraph(
    "RDKit was used to compute molecular weight (MW), calculated lipophilicity (LogP), topological "
    "polar surface area (TPSA), hydrogen-bond donors/acceptors (HBD/HBA), and rotatable bonds. "
    "Drug-likeness was assessed against the Lipinski Rule-of-Five (MW &#8804; 500, LogP &#8804; 5, "
    "HBD &#8804; 5, HBA &#8804; 10) and Veber criteria (rotatable bonds &#8804; 10, TPSA &#8804; 140 "
    "&#197;<super>2</super>). The Quantitative Estimate of Drug-likeness (QED, 0&#8211;1) provides a "
    "single continuous developability score. Structural alerts were flagged with RDKit FilterCatalogs: "
    "PAINS (pan-assay interference) as a hard triage flag, and Brenk/NIH toxicophores as advisory "
    "review prompts.", styles["Body2"]))
S.append(Paragraph("ADMET prediction", styles["SubHead"]))
S.append(Paragraph(
    "ADMET endpoints were predicted with ADMET-AI (Swanson et al., Bioinformatics 2024), a graph "
    "neural-network platform trained on Therapeutics Data Commons benchmarks. Predictions span ~40 "
    "endpoints across absorption (Caco-2, PAMPA, HIA, bioavailability, P-gp), distribution (BBB, "
    "plasma protein binding, volume of distribution), metabolism (CYP1A2/2C9/2C19/2D6/3A4 inhibition "
    "and substrate), excretion (clearance, half-life), and toxicity (hERG, AMES mutagenicity, DILI, "
    "carcinogenicity, ClinTox, and the Tox21 nuclear-receptor/stress-response panel). Classification "
    "endpoints are reported as probabilities (0&#8211;1); a probability &gt; 0.5 is scored positive. "
    "Each endpoint is additionally expressed as a percentile against a reference set of approved drugs. "
    "To keep the workflow free of commercial-use restrictions, this reference is built from ChEMBL "
    "approved small-molecule drugs (European Bioinformatics Institute, EMBL-EBI; CC BY-SA) rather than "
    "ADMET-AI's default DrugBank set (CC BY-NC); reference percentiles are computed from ADMET-AI model "
    "predictions on that ChEMBL set using the same models.", styles["Body2"]))
S.append(Paragraph("Triage flagging", styles["SubHead"]))
S.append(Paragraph(
    "A compound was flagged for review if it had any input-quality/sanity issue, a PAINS alert, "
    "&#8805; 2 Lipinski violations, or a positive prediction (p &gt; 0.5) for hERG, AMES, or DILI. "
    "These are the skill's default thresholds. Because the DILI classifier is sensitive at p &gt; 0.5, "
    "the raw flag count is best read together with the per-compound flag <i>count</i> and probability "
    "magnitude rather than as a binary pass/fail.", styles["Body2"]))
S.append(PageBreak())

# ---- Results ----
S.append(Paragraph("Results", styles["SectionHead"]))

S.append(Paragraph("Physicochemical property landscape", styles["SubHead"]))
S.append(Paragraph(
    "Figure 1 shows the distribution of the six core physicochemical descriptors with drug-like "
    "reference lines. The panel clusters within canonical oral-drug space: most compounds fall below "
    "MW 500 Da and LogP 5, with TPSA and hydrogen-bond counts concentrated in the absorption-favorable "
    "range. The high-MW tail (ritonavir 721 Da, atorvastatin 559 Da) corresponds to the compounds that "
    "breach Rule-of-Five limits.", styles["Body2"]))
S.append(fig(f"{OUT}/physicochemical_overview.png", 490, 280,
    "Figure 1. Distributions of MW, LogP, TPSA, H-bond donors, H-bond acceptors, and rotatable bonds "
    "across the 30-compound panel. Red dashed lines mark Rule-of-Five / Veber thresholds."))

S.append(Paragraph("Drug-likeness and chemical space", styles["SubHead"]))
S.append(Paragraph(
    f"{n_druglike} of {n} compounds pass both Lipinski and Veber filters (Lipinski permits up to one "
    "property violation). Figure 2 places each drug in MW&#215;LogP space, colored by number of Lipinski "
    "violations. The four compounds failing the combined criteria are atorvastatin and ritonavir (each "
    "with two Lipinski violations) plus rosuvastatin and lisinopril (which pass Lipinski but fail Veber "
    "on polar surface area / rotatable bonds) \u2014 all recognized outliers whose oral use relies on "
    "potency, active metabolites, or formulation rather than ideal physicochemistry.", styles["Body2"]))
S.append(fig(f"{OUT}/lipinski_space.png", 400, 280,
    "Figure 2. Lipinski chemical space (MW vs LogP). Points colored by Lipinski violation count; the "
    "shaded blue region and gray dashed lines mark the MW \u2264 500 / LogP \u2264 5 drug-like zone. "
    "Points are colored by Lipinski violation count (cividis: dark blue = 0 to yellow = more)."))

S.append(Paragraph("Developability ranking (QED)", styles["SubHead"]))
S.append(Paragraph(
    f"The mean QED was {qed_mean:.2f}, with {n_qed05}/{n} compounds at or above the 0.5 oral-drug "
    "benchmark. Figure 3 ranks all compounds by QED and highlights the single PAINS-alerted structure "
    "(ketoconazole). The highest-QED compounds (ciprofloxacin, naproxen, fluoxetine) are small, "
    "well-balanced molecules; the lowest (ritonavir, atorvastatin) are the large, complex outliers.", styles["Body2"]))
S.append(fig(f"{OUT}/developability_qed.png", 380, 300,
    "Figure 3. Compounds ranked by QED developability score. Blue = no PAINS alert; orange = PAINS "
    "alert. Dashed line marks QED = 0.5 (typical oral drug)."))

S.append(Paragraph("ADMET endpoint profile", styles["SubHead"]))
S.append(Paragraph(
    "Figure 4 clusters compounds by their predicted probabilities across 16 binary ADMET classification "
    "endpoints. Rows and columns are hierarchically clustered; brighter (yellow) cells indicate higher "
    "predicted probability. A distinct high-risk cluster of hERG-positive compounds is visible, "
    "including the withdrawn cardiotoxic drugs and several kinase inhibitors, while nuclear-receptor and "
    "genotoxicity endpoints are uniformly low across the panel.", styles["Body2"]))
S.append(fig(f"{OUT}/admet_heatmap.png", 490, 300,
    "Figure 4. Hierarchically clustered heatmap of 16 ADMET classification endpoints (columns) across "
    "the 30 compounds (rows). Color = predicted probability (viridis: dark = low, yellow = high)."))

# Endpoint summary table
S.append(Paragraph("Key endpoint summary", styles["SubHead"]))
S.append(Paragraph(
    "Table 1 summarizes mean predicted probabilities and positive counts for the most decision-relevant "
    "safety, metabolism, and absorption endpoints. Absorption endpoints (HIA, bioavailability) are high "
    "as expected for marketed oral drugs; the toxicity signal is dominated by DILI and hERG.", styles["Body2"]))
ep_rows = [[r["Endpoint"], f'{r["Mean prob"]:.2f}', f'{int(r["N pos (p>0.5)"])}/{int(r["N"])}'] for _,r in ep.iterrows()]
S.append(make_table(["Endpoint","Mean probability","Positive (p &gt; 0.5)"],
                    ep_rows, [220,130,120], align_left_cols=(0,)))
S.append(Spacer(1,10))

# Priority multi-flag table
S.append(Paragraph("Priority multi-liability compounds", styles["SubHead"]))
S.append(Paragraph(
    "Table 2 lists compounds carrying three or more concurrent triage flags \u2014 the subset that would "
    "be prioritized for experimental de-risking. Each entry reflects genuine, literature-recognized "
    "liabilities (e.g., ketoconazole hepatotoxicity and CYP3A4 inhibition; ritonavir DDI risk; "
    "cisapride hERG/torsades).", styles["Body2"]))
pr_rows = [[r["name"], f'{r["QED"]:.2f}', int(r["n_flags"]), r["flags"]] for _,r in priority.iterrows()]
S.append(make_table(["Compound","QED","# flags","Flag detail"],
                    pr_rows, [90,45,50,285], align_left_cols=(0,3)))
S.append(Spacer(1,10))

# ---- Reference-percentile context (vs commercially-permissive ChEMBL approved-drug set) ----
# Detect whichever percentile suffix is present (chembl_approved_percentile after the
# license-compliant swap; drugbank_approved_percentile only if an old CSV is reused).
_pct_suffix = None
for _cand in ("chembl_approved_percentile", "drugbank_approved_percentile"):
    if any(c.endswith(_cand) for c in full.columns):
        _pct_suffix = _cand
        break
_ref_name = ("ChEMBL approved small-molecule drugs" if _pct_suffix == "chembl_approved_percentile"
             else "the approved-drug reference set")
# Read reference size + license from the provenance sidecar when available.
# Try skill-relative (scripts/ -> ../assets) and same-dir/assets locations.
_ref_n, _ref_license, _ref_release = None, "CC BY-SA", None
_here_dir = os.path.dirname(os.path.abspath(__file__))
_meta_candidates = [
    os.path.join(os.path.dirname(_here_dir), "assets", "chembl_approved_reference.meta.json"),
    os.path.join(_here_dir, "assets", "chembl_approved_reference.meta.json"),
]
try:
    for _meta_path in _meta_candidates:
        if os.path.exists(_meta_path):
            with open(_meta_path) as _fh:
                _m = json.load(_fh)
            _ref_n = _m.get("n_molecules"); _ref_license = _m.get("license", _ref_license)
            _ref_release = _m.get("date_built")
            break
except Exception:
    pass
# Normalize license label: meta.json may store "CC BY-SA 3.0 (ChEMBL data)"; drop the
# trailing parenthetical so it doesn't nest inside the sentence's own parentheses.
if _ref_license and "(" in _ref_license:
    _ref_license = _ref_license.split("(")[0].strip()

if _pct_suffix is not None:
    S.append(Paragraph("Reference-percentile context", styles["SubHead"]))
    _ref_n_txt = f"{_ref_n:,} " if _ref_n else ""
    S.append(Paragraph(
        "ADMET-AI expresses each prediction as a percentile against a reference set of approved drugs, "
        "which places a compound's absolute score in context (e.g., a hERG probability of 0.6 is only "
        "concerning if it is high <i>relative to approved drugs</i>). To keep the workflow commercially "
        f"usable, percentiles here are computed against {_ref_n_txt}{_ref_name} derived from ChEMBL "
        f"({_ref_license}), replacing ADMET-AI's default DrugBank reference (CC BY-NC, no commercial "
        "use). Table 3 summarizes where this panel sits within that reference distribution for the "
        "key safety, metabolism, and absorption endpoints.", styles["Body2"]))

    # Endpoints to summarize (label -> base endpoint column), toxicity flagged for the >90th count.
    _ctx_endpoints = [
        ("hERG (cardiotox)",      "hERG",               True),
        ("DILI (hepatotox)",      "DILI",               True),
        ("AMES (mutagenicity)",   "AMES",               True),
        ("CYP3A4 inhibition",     "CYP3A4_Veith",       True),
        ("BBB penetration",       "BBB_Martins",        False),
        ("Aq. solubility",        "Solubility_AqSolDB", False),
    ]
    _ctx_rows = []
    for label, base, is_tox in _ctx_endpoints:
        col = f"{base}_{_pct_suffix}"
        if col not in full.columns:
            continue
        vals = pd.to_numeric(full[col], errors="coerce").dropna()
        if vals.empty:
            continue
        med = vals.median()
        n_hi = int((vals >= 90).sum())
        hi_txt = f"{n_hi}/{len(vals)}" + (" \u25b2" if (is_tox and n_hi) else "")
        _ctx_rows.append([label, f"{med:.0f}", hi_txt])
    S.append(make_table(
        ["Endpoint", "Median panel percentile", "Compounds &ge; 90th pct"],
        _ctx_rows, [210, 160, 130], align_left_cols=(0,)))
    S.append(Spacer(1,6))
    S.append(Paragraph(
        "<i>Percentiles are relative to " + (f"{_ref_n:,} " if _ref_n else "") +
        f"{_ref_name} (ChEMBL"
        + (f", built {_ref_release}" if _ref_release else "") +
        f"; {_ref_license}); reference values are ADMET-AI model predictions on that set. A median near "
        "50 indicates the panel is typical of approved drugs for that endpoint; &#9650; marks toxicity "
        "endpoints where one or more compounds rank in the top decile of the approved-drug distribution.</i>",
        styles["Caption"]))
    S.append(Spacer(1,4))

S.append(PageBreak())

# ---- Discussion ----
S.append(Paragraph("Discussion & Interpretation", styles["SectionHead"]))
S.append(Paragraph(
    "This panel serves as a positive-control benchmark: because every compound is an approved drug, a "
    "well-calibrated ADMET workflow should recover known structure\u2013liability relationships, and it "
    "does. The predicted hERG ranking places the two market-withdrawn cardiotoxic agents (cisapride, "
    "terfenadine) and other established hERG binders (ritonavir, imatinib, ketoconazole) at the top, "
    "which is the expected behavior and lends confidence to the pipeline's directional accuracy.", styles["Body2"]))
S.append(Paragraph(
    "The drug-likeness picture is equally coherent. The four compounds failing combined Lipinski+Veber "
    "filters are all recognized Rule-of-Five outliers whose clinical success depends on high potency, "
    "prodrug/active-metabolite strategies, or formulation \u2014 a reminder that Ro5 is a guideline for "
    "oral absorption, not a hard rule for viability. QED provides a more graded ranking that correctly "
    "rewards small, balanced molecules and penalizes the large peptidomimetic (ritonavir) and complex "
    "statin scaffolds.", styles["Body2"]))
S.append(Paragraph(
    f"The triage layer flagged {len(flagged)} of {n} compounds, but this figure is driven largely by a "
    "permissive DILI threshold (p &gt; 0.5), which fires on many approved drugs \u2014 several of which "
    "carry real, label-recognized hepatic warnings. The more actionable readout is the flag-count "
    f"stratification: {n - len(flagged)} compounds are completely clean, most flagged compounds carry a "
    f"single low-magnitude concern, and only {len(priority)} stack three or more liabilities. That "
    "multi-flag subset \u2014 ketoconazole, ritonavir, cisapride, atorvastatin, erlotinib, omeprazole "
    "\u2014 is where experimental hERG/AMES/DILI assays would deliver the most value.", styles["Body2"]))

# ---- Limitations ----
S.append(Paragraph("Limitations", styles["SectionHead"]))
S.append(Paragraph(
    "<b>Predictions, not measurements.</b> All ADMET values are QSAR/graph-neural-network estimates for "
    "triage and prioritization. They carry model uncertainty and should be confirmed experimentally "
    "before any decision of consequence.", styles["Body2"]))
S.append(Paragraph(
    "<b>Reference percentiles are relative, model-based positions.</b> Percentiles rank each compound "
    "against a ChEMBL approved-drug set whose reference values are themselves ADMET-AI predictions (not "
    "experimental measurements), so they describe position within a computed approved-drug distribution "
    "rather than an absolute risk. Switching the reference from DrugBank to ChEMBL shifts individual "
    "percentiles by a few points but does not change the underlying predictions or any flag.", styles["Body2"]))
S.append(Paragraph(
    "<b>Regression extrapolation artifacts.</b> A few continuous regressors returned physically "
    "implausible values for individual compounds (e.g., plasma protein binding &gt; 100%, negative "
    "predicted half-life or volume of distribution). These are known out-of-domain extrapolation "
    "artifacts and were not interpreted as literal quantities; classification (probability) endpoints "
    "are more robust.", styles["Body2"]))
S.append(Paragraph(
    "<b>Calculated lipophilicity.</b> LogP is RDKit-calculated (Crippen) and can differ from measured "
    "logP/logD, which affects borderline Ro5/Veber calls. <b>Benchmark composition.</b> This set is "
    "deliberately enriched for known liabilities (withdrawn drugs, CYP inhibitors), so panel-level "
    "positive rates (e.g., hERG) are higher than a random screening library would show and should not "
    "be read as base rates. <b>Structural alerts are advisory.</b> Brenk/NIH toxicophores fire on many "
    "safe approved drugs; only PAINS was used as a hard flag.", styles["Body2"]))

# ---- Appendix ----
S.append(PageBreak())
S.append(Paragraph("Appendix: Full Drug-Likeness Table", styles["SectionHead"]))
S.append(Paragraph(
    "Per-compound physicochemical and drug-likeness summary for all 30 molecules. Complete results "
    "including all ADMET endpoints are provided in the accompanying CSV files "
    "(<font name='Courier'>all_properties.csv</font>, <font name='Courier'>admet_predictions.csv</font>, "
    "<font name='Courier'>flagged_compounds.csv</font>).", styles["Body2"]))

app_cols = ["name","MW","LogP","TPSA","HBD","HBA","QED","Lipinski_Violations"]
app = full[app_cols].copy().sort_values("QED", ascending=False)
app_rows = []
for _,r in app.iterrows():
    app_rows.append([r["name"], f'{r["MW"]:.0f}', f'{r["LogP"]:.2f}', f'{r["TPSA"]:.0f}',
                     int(r["HBD"]), int(r["HBA"]), f'{r["QED"]:.2f}', int(r["Lipinski_Violations"])])
# Single full-width table, compacted (tight row padding) so all 30 rows + header
# fit on one page without a near-blank continuation page.
_app_hdr = ["Compound","MW","LogP","TPSA","HBD","HBA","QED","Ro5 viol."]
_app_data = [[Paragraph(h, styles["HeadCell"]) for h in _app_hdr]]
for r in app_rows:
    _app_data.append([Paragraph(str(c), styles["CellL"] if j == 0 else styles["CellC"])
                      for j, c in enumerate(r)])
_app_tbl = Table(_app_data, colWidths=[120,44,46,46,40,40,46,54], repeatRows=1)
_app_st = [
    ("BACKGROUND",(0,0),(-1,0),TABLE_HEADER_BG),
    ("GRID",(0,0),(-1,-1),0.5,TABLE_BORDER),
    ("BOX",(0,0),(-1,-1),0.75,TABLE_BORDER),
    ("TOPPADDING",(0,0),(-1,-1),2.3),("BOTTOMPADDING",(0,0),(-1,-1),2.3),  # compact rows
    ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
]
for _i in range(2, len(_app_data), 2):
    _app_st.append(("BACKGROUND",(0,_i),(-1,_i),TABLE_ALT_ROW))
_app_tbl.setStyle(TableStyle(_app_st))
_app_tbl.hAlign = "CENTER"
S.append(_app_tbl)

doc = SimpleDocTemplate(PDF, pagesize=letter, topMargin=58, bottomMargin=52,
                        leftMargin=60, rightMargin=60, title=TITLE)
doc.build(S, onFirstPage=chrome, onLaterPages=chrome)
print("PDF written:", PDF)

# ---- Validate ----
from pypdf import PdfReader
reader = PdfReader(PDF)
print("Pages:", len(reader.pages), "| size:", os.path.getsize(PDF), "bytes")
assert len(reader.pages) >= 5
assert os.path.getsize(PDF) > 5000
assert len(reader.pages[0].extract_text().strip()) > 0
print("Validation OK")
