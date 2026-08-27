"""
constraint_report.py — Phylo-branded PDF for the gnomAD constraint-gating workflow.

Follows the pdf-report-generation skill conventions (gold accent, Helvetica base,
centered figures/tables, header/footer chrome). Sections: Intro, Methods, Results
(flag table + 3 figures + version-shift note), per-gene disease table, Conclusions.

build_report(df, figures, out_path, thresholds=(0.35, 0.90)) -> out_path
"""

from datetime import date
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, PageBreak, HRFlowable, KeepTogether)

PHYLO_GOLD = HexColor("#D4A04A"); HEADING = HexColor("#111111"); BODY = HexColor("#2C2A26")
MUTED = HexColor("#8A8378"); TH_BG = PHYLO_GOLD; TH_FG = HexColor("#FFFFFF")
ALT = HexColor("#F9F7F3"); BORDER = HexColor("#D5CFC5"); CALL = HexColor("#FAF9F3")
C_FLAG = HexColor("#0072B2"); C_NOFLAG = HexColor("#E69F00")

# KO-tolerance tier -> colour (matches constraint_druggability.tier_color)
_TIER_HEX = {
    "Very low (near-essential)": HexColor("#7A0177"),
    "Low (LoF-intolerant)":      HexColor("#D55E00"),
    "Intermediate":              HexColor("#E69F00"),
    "Tolerant":                  HexColor("#009E73"),
    "Not determined":            HexColor("#999999"),
}


def _tier_hex(t):
    return _TIER_HEX.get(str(t), HexColor("#999999"))


def _styles():
    s = getSampleStyleSheet()
    def add(name, **kw):
        if name in s.byName:
            for k, v in kw.items():
                setattr(s[name], k, v)
        else:
            s.add(ParagraphStyle(name=name, **kw))
    add("ReportTitle", fontName="Helvetica-Bold", fontSize=23, textColor=HEADING, spaceAfter=6, leading=28)
    add("Subtitle", fontName="Helvetica", fontSize=11, textColor=PHYLO_GOLD, spaceAfter=4)
    add("Attribution", fontName="Helvetica-Oblique", fontSize=9.5, textColor=MUTED, spaceAfter=8)
    add("SectionHead", fontName="Helvetica-Bold", fontSize=16, textColor=HEADING, spaceBefore=18, spaceAfter=8)
    add("SubHead", fontName="Helvetica-Bold", fontSize=12, textColor=HEADING, spaceBefore=10, spaceAfter=5)
    add("Body", fontName="Helvetica", fontSize=10.5, textColor=BODY, alignment=TA_JUSTIFY, spaceAfter=8, leading=15)
    add("Caption", fontName="Helvetica-Oblique", fontSize=9, textColor=MUTED, alignment=TA_CENTER, spaceAfter=14)
    add("THcell", fontName="Helvetica-Bold", fontSize=8.5, textColor=TH_FG, alignment=TA_CENTER, leading=10)
    add("Tcell", fontName="Helvetica", fontSize=8.5, textColor=BODY, alignment=TA_CENTER, leading=10)
    add("TcellL", fontName="Helvetica", fontSize=8.2, textColor=BODY, alignment=TA_LEFT, leading=10.5)
    add("GeneName", fontName="Helvetica-Bold", fontSize=8.6, textColor=BODY, alignment=TA_LEFT, leading=11)
    return s


def _divider():
    return HRFlowable(width=480, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)


def _callout(text, s):
    t = Table([[Paragraph(text, s["Body"])]], colWidths=[468])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CALL), ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                           ("LINEBEFORE", (0, 0), (0, -1), 3, PHYLO_GOLD),
                           ("TOPPADDING", (0, 0), (-1, -1), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
                           ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    return t


def _fmt(v):
    if v is None or (isinstance(v, float) and v != v):
        return "NA"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _safe_str(v, default="-"):
    """Coerce a nullable DataFrame value to a safe string for Paragraph().

    When a DataFrame is re-read from CSV, empty cells become pandas NaN (float64),
    which is truthy in Python. ``NaN or '-'`` therefore evaluates to NaN (not '-'),
    and ``Paragraph(NaN, ...)`` crashes with ``AttributeError: 'float' object has
    no attribute 'split'``. This helper treats None, NaN, and empty string as the
    default and otherwise returns ``str(v)``.
    """
    if v is None:
        return default
    if isinstance(v, float) and v != v:  # NaN check without importing pandas
        return default
    s = str(v)
    return s if s != "" else default


def build_report(df, figures, out_path, thresholds=(0.35, 0.90)):
    loeuf_cut, pli_cut = thresholds
    s = _styles()
    story = []
    today = date.today().strftime("%B %d, %Y")

    resolved = df[df["LoF_intolerant"].isin(["Yes", "No"])]
    flagged = list(resolved.loc[resolved["LoF_intolerant"] == "Yes", "gene"])
    n_flag = len(flagged)
    n_total = len(df)
    flag_list = ", ".join(flagged) if flagged else "none"

    def hf(canvas, doc):
        canvas.saveState(); w, h = letter
        canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
        canvas.drawString(60, h - 40, "Gene Constraint Gating (gnomAD LOEUF / pLI)")
        canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60, h - 48, w - 60, h - 48)
        canvas.setStrokeColor(BORDER); canvas.setLineWidth(0.75); canvas.line(60, 40, w - 60, 40)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
        canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
        canvas.restoreState()

    # ---- Title ----
    story += [Spacer(1, 28),
              Paragraph("Genetic Constraint Gating", s["ReportTitle"]),
              Paragraph("Flagging loss-of-function-intolerant genes with gnomAD LOEUF and pLI", s["Subtitle"]),
              Spacer(1, 6),
              Paragraph(f"<i>Generated by Biomni  |  {today}</i>", s["Attribution"]),
              _divider()]

    # ---- Intro ----
    story += [Paragraph("1. Introduction", s["SectionHead"]),
              Paragraph(
        "Genes differ in how strongly selection removes protein-truncating (loss-of-function, LoF) variants "
        "from the population. Genes depleted of LoF variation are <b>LoF-intolerant</b> and are strong candidates "
        "for dominant, haploinsufficiency-driven disease. gnomAD quantifies this with two gene-level metrics: "
        "<b>pLI</b> (probability of LoF-intolerance; &#8805; 0.90 marks intolerance) and <b>LOEUF</b> (LoF observed/"
        "expected upper 90% CI bound; <i>lower</i> is more constrained, &lt; 0.35 marks intolerance). LOEUF is a "
        "continuous score that behaves better for small genes and is gnomAD's recommended primary metric.", s["Body"]),
              _callout(
        f"<b>Bottom line.</b> Under standard thresholds (LOEUF &lt; {loeuf_cut} <b>or</b> pLI &#8805; {pli_cut}) on "
        f"gnomAD v2.1.1, <b>{n_flag} of {n_total}</b> input gene(s) are flagged LoF-intolerant: <b>{flag_list}</b>. "
        f"Each flagged gene is then read as a <b>drug target</b>: its constraint is translated into a knockout-tolerance "
        f"tier, a systemic on-target safety risk, and a recommended modality strategy (Section 3.3). Genes whose call "
        f"shifts between v2.1.1 and the larger v4.1 cohort are annotated as borderline.", s)]

    # ---- Methods ----
    story += [Paragraph("2. Methods", s["SectionHead"]),
              Paragraph(
        "Gene-level LoF constraint was retrieved from the gnomAD GraphQL API for <b>v2.1.1</b> (GRCh37, ~125k exomes; "
        "primary flagging basis) and <b>v4.1</b> (GRCh38, ~730k exomes; comparison). For each gene's canonical "
        "transcript we recorded observed/expected LoF counts, the point o/e, <b>LOEUF</b> (field "
        "<font name='Courier'>oe_lof_upper</font>), pLI, and the LoF Z-score. Input gene symbols, aliases and "
        "Ensembl IDs were resolved to current symbols via MyGene.info; genes without a constraint record are reported "
        "as not-available rather than dropped or imputed.", s["Body"]),
              Paragraph(
        f"<b>Intolerance flag.</b> A gene is flagged LoF-intolerant if <b>LOEUF &lt; {loeuf_cut} OR pLI &#8805; "
        f"{pli_cut}</b> (standard gnomAD convention), computed on v2.1.1. Disease and inheritance annotations are "
        "grounded in ClinGen gene-disease validity curation (via MyGene.info); when no curated association exists "
        "the field is left explicitly marked, never model-generated.", s["Body"]),
              _callout(
        "<b>Interpretation caveat.</b> Population LoF constraint reflects selection against germline heterozygous LoF "
        "only. It does not capture recessive genes, somatic-only tumor-suppressor roles, gain-of-function, or dosage "
        "effects from copy-number/structural variation, and is unreliable for very small genes (few expected LoF) and "
        "for genes in segmental duplications. Pair every flag with the gene's known mechanism.", s)]

    story.append(PageBreak())

    # ---- Results: flag table ----
    story += [Paragraph("3. Results", s["SectionHead"]),
              Paragraph(
        "Table 1 lists the constraint metrics and flags, ordered from most to least constrained by v2.1.1 LOEUF. "
        "Flagged genes are shown in blue.", s["Body"]),
              Paragraph("Table 1. gnomAD LoF constraint metrics and intolerance flags.", s["SubHead"])]

    hdrs = ["Gene", "o/e<br/>(v2)", "LOEUF<br/>(v2)", "pLI<br/>(v2)", "LoF Z<br/>(v2)",
            "LOEUF<br/>(v4)", "pLI<br/>(v4)", "LoF-<br/>intol.", "Version<br/>shift"]
    tdata = [[Paragraph(h, s["THcell"]) for h in hdrs]]
    for _, r in df.iterrows():
        tdata.append([
            Paragraph(str(r["gene"]), s["GeneName"]),
            Paragraph(_fmt(r.get("oe_lof_v2")), s["Tcell"]),
            Paragraph(_fmt(r.get("LOEUF_v2")), s["Tcell"]),
            Paragraph(_fmt(r.get("pLI_v2")), s["Tcell"]),
            Paragraph(_fmt(r.get("lof_z_v2")), s["Tcell"]),
            Paragraph(_fmt(r.get("LOEUF_v4")), s["Tcell"]),
            Paragraph(_fmt(r.get("pLI_v4")), s["Tcell"]),
            Paragraph(f'<b>{r["LoF_intolerant"]}</b>', s["Tcell"]),
            Paragraph(_safe_str(r.get("version_shift")), s["Tcell"]),
        ])
    t1 = Table(tdata, colWidths=[52, 40, 46, 48, 42, 46, 46, 40, 78], repeatRows=1)
    t1.hAlign = "CENTER"
    ts = [("BACKGROUND", (0, 0), (-1, 0), TH_BG), ("TEXTCOLOR", (0, 0), (-1, 0), TH_FG),
          ("GRID", (0, 0), (-1, -1), 0.5, BORDER), ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
          ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
          ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
          ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for i, (_, r) in enumerate(df.iterrows(), start=1):
        if i % 2 == 0:
            ts.append(("BACKGROUND", (0, i), (-1, i), ALT))
        col = C_FLAG if r["LoF_intolerant"] == "Yes" else (C_NOFLAG if r["LoF_intolerant"] == "No" else MUTED)
        ts.append(("TEXTCOLOR", (7, i), (7, i), col))
    t1.setStyle(TableStyle(ts))
    story.append(t1)
    story.append(Paragraph("o/e = LoF observed/expected point ratio; LOEUF = o/e upper 90% CI bound (lower = more "
                           f"constrained); pLI = probability of LoF-intolerance. Flag rule: LOEUF &lt; {loeuf_cut} or "
                           f"pLI &#8805; {pli_cut} on v2.1.1.", s["Caption"]))

    # ---- Figures ----
    def add_fig(path, cap):
        if path:
            img = Image(path, width=452, height=294); img.hAlign = "CENTER"
            story.append(KeepTogether([Spacer(1, 6), img, Spacer(1, 4), Paragraph(cap, s["Caption"])]))

    add_fig(figures.get("ranked_loeuf"),
            "Figure 1. Genes ranked by v2.1.1 LOEUF. Dashed line = intolerance cutoff; blue = flagged.")
    if figures.get("pli_vs_loeuf"):
        img = Image(figures["pli_vs_loeuf"], width=402, height=320); img.hAlign = "CENTER"
        story.append(KeepTogether([Spacer(1, 8), img, Spacer(1, 4),
                     Paragraph("Figure 2. pLI vs LOEUF with standard-threshold quadrants; flagged genes cluster in the "
                               "low-LOEUF / high-pLI corner.", s["Caption"])]))
    story.append(PageBreak())
    story += [Paragraph("3.1 Sensitivity to gnomAD version", s["SubHead"]),
              Paragraph("Constraint estimates sharpen with cohort size. Figure 3 contrasts v2.1.1 and v4.1 LOEUF; genes "
                        "with a material shift are flagged in Table 1 and warrant interpretation beyond a single-version "
                        "call.", s["Body"])]
    add_fig(figures.get("version_shift"),
            "Figure 3. LOEUF shift from gnomAD v2.1.1 (grey) to v4.1 (blue). Dashed line = 0.35 cutoff.")

    # ---- Per-gene disease table (grounded) ----
    story.append(PageBreak())
    story += [Paragraph("3.2 Gene function and curated disease association", s["SubHead"]),
              Paragraph("Disease and inheritance below are from ClinGen gene-disease validity curation (via "
                        "MyGene.info). Genes with no curated entry are marked accordingly and were not annotated from "
                        "model knowledge.", s["Body"])]
    dhdr = [Paragraph("Gene", s["THcell"]), Paragraph("Flag", s["THcell"]),
            Paragraph("Curated disease (classification; inheritance)", s["THcell"]), Paragraph("MONDO", s["THcell"])]
    ddata = [dhdr]
    for _, r in df.iterrows():
        ddata.append([Paragraph(str(r["gene"]), s["GeneName"]),
                      Paragraph(f'<b>{r["LoF_intolerant"]}</b>', s["Tcell"]),
                      Paragraph(_safe_str(r.get("disease_label"), default="no curated disease association retrieved"), s["TcellL"]),
                      Paragraph(_safe_str(r.get("mondo_id")), s["Tcell"])])
    td = Table(ddata, colWidths=[52, 34, 300, 82], repeatRows=1); td.hAlign = "CENTER"
    tds = [("BACKGROUND", (0, 0), (-1, 0), TH_BG), ("TEXTCOLOR", (0, 0), (-1, 0), TH_FG),
           ("GRID", (0, 0), (-1, -1), 0.5, BORDER), ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
           ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
           ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for i, (_, r) in enumerate(df.iterrows(), start=1):
        if i % 2 == 0:
            tds.append(("BACKGROUND", (0, i), (-1, i), ALT))
        col = C_FLAG if r["LoF_intolerant"] == "Yes" else (C_NOFLAG if r["LoF_intolerant"] == "No" else MUTED)
        tds.append(("TEXTCOLOR", (1, i), (1, i), col))
    td.setStyle(TableStyle(tds))
    story.append(td)
    story.append(Paragraph("Table 2. Curated gene-disease validity (ClinGen) alongside the constraint call.", s["Caption"]))

    # ---- Drug-target interpretation (KO-tolerance -> modality risk & strategy) ----
    if "ko_tolerance_tier" in df.columns:
        story.append(PageBreak())
        story += [Paragraph("3.3 Drug-target interpretation: knockout tolerance", s["SectionHead"]),
                  Paragraph(
            "The constraint flag is next read as a <b>drug-target</b> signal. The key principle is that "
            "gnomAD constraint measures selection against <b>germline heterozygous</b> loss-of-function \u2014 whether "
            "losing one copy in every cell of a developing human is tolerated. It is a statement about organism-level "
            "essentiality, <i>not</i> about whether inhibiting the protein in a specific adult tissue or tumour is "
            "viable. A strong LoF-intolerant flag is therefore a <b>safety / modality caution</b> for systemic, "
            "complete inhibition or degradation \u2014 not a veto on the gene as a target. Constrained "
            "tumour-suppressor-like genes are typically drugged through a <b>dependency created by their loss</b> "
            "(synthetic lethality, paralog dependency, or a downstream node), not by inhibiting the gene itself.",
            s["Body"]),
                  Paragraph(
            "Each gene is placed on a <b>knockout-tolerance tier</b> (derived from LOEUF, pLI and, where available, "
            "gnomAD's LOEUF percentile) that maps to a <b>systemic on-target risk</b> and a recommended strategy. "
            "Tiers, from most to least constrained: <b>Very low (near-essential)</b>, <b>Low (LoF-intolerant)</b>, "
            "<b>Intermediate</b>, <b>Tolerant</b>.", s["Body"]),
                  Paragraph("Table 3. Knockout-tolerance tier, systemic on-target risk and recommended strategy.", s["SubHead"])]

        ihdr = [Paragraph("Gene", s["THcell"]), Paragraph("KO-tolerance tier", s["THcell"]),
                Paragraph("Systemic<br/>on-target risk", s["THcell"]),
                Paragraph("Recommended target strategy", s["THcell"])]
        idata = [ihdr]
        for _, r in df.iterrows():
            idata.append([
                Paragraph(str(r["gene"]), s["GeneName"]),
                Paragraph(_safe_str(r.get("ko_tolerance_tier")), s["Tcell"]),
                Paragraph(f'<b>{_safe_str(r.get("systemic_target_risk"))}</b>', s["Tcell"]),
                Paragraph(_safe_str(r.get("target_strategy")), s["TcellL"]),
            ])
        ti = Table(idata, colWidths=[50, 96, 58, 264], repeatRows=1); ti.hAlign = "CENTER"
        tis = [("BACKGROUND", (0, 0), (-1, 0), TH_BG), ("TEXTCOLOR", (0, 0), (-1, 0), TH_FG),
               ("GRID", (0, 0), (-1, -1), 0.5, BORDER), ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
               ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
               ("VALIGN", (0, 0), (-1, -1), "TOP")]
        for i, (_, r) in enumerate(df.iterrows(), start=1):
            if i % 2 == 0:
                tis.append(("BACKGROUND", (0, i), (-1, i), ALT))
            tis.append(("TEXTCOLOR", (1, i), (1, i), _tier_hex(r.get("ko_tolerance_tier"))))
        ti.setStyle(TableStyle(tis))
        story.append(ti)
        story.append(Paragraph("Tier and risk are derived from the population LoF-constraint metrics; the strategy "
                               "column is mechanism-agnostic guidance, not a specific drug recommendation.", s["Caption"]))

        if figures.get("druggability"):
            img = Image(figures["druggability"], width=452, height=267); img.hAlign = "CENTER"
            story.append(KeepTogether([Spacer(1, 8), img, Spacer(1, 4),
                         Paragraph("Figure 4. Drug-target reading: each gene placed by LOEUF (x) and knockout-tolerance "
                                   "tier (y); the shaded zone (LOEUF &lt; 0.35) marks high systemic on-target risk.",
                                   s["Caption"])]))

        # concise per-gene interpretation callouts
        story.append(Paragraph("Per-gene reading", s["SubHead"]))
        for _, r in df.iterrows():
            tier = _safe_str(r.get("ko_tolerance_tier"))
            note = _safe_str(r.get("systemic_target_note"))
            act = _safe_str(r.get("actionability"))
            story.append(Paragraph(
                f'<b>{r["gene"]}</b> \u2014 {tier}; systemic on-target risk '
                f'<b>{_safe_str(r.get("systemic_target_risk"))}</b> (\u201c{act}\u201d). {note}', s["Body"]))

    # ---- Conclusions ----
    # summarise the drug-target reading if present
    drug_line = ""
    if "ko_tolerance_tier" in df.columns:
        res = df[df["LoF_intolerant"].isin(["Yes", "No"])]
        high = list(res.loc[res["systemic_target_risk"] == "High", "gene"])
        if high:
            drug_line = (" As drug targets, the LoF-intolerant genes carry <b>high systemic on-target risk</b> for "
                         f"complete inhibition/degradation (<b>{', '.join(high)}</b>): each is best pursued through a "
                         "dependency created by its loss (synthetic lethality / paralog / downstream node) or with an "
                         "engineered therapeutic window, rather than blunt systemic ablation.")

    story += [Paragraph("4. Conclusions", s["SectionHead"]),
              Paragraph(
        f"Of {n_total} input gene(s), <b>{n_flag}</b> meet the standard gnomAD LoF-intolerance criteria on v2.1.1"
        + (f": <b>{flag_list}</b>. " if flagged else ". ")
        + "Genes supported by concordant LOEUF and pLI are the highest-confidence LoF-intolerant candidates. Genes "
        "flagged with a version shift, or that are small/segmental-duplication-prone, should be interpreted together "
        "with their curated disease mechanism rather than the population flag alone." + drug_line, s["Body"]),
              _callout(
        "<b>Practical guidance.</b> Population LoF constraint captures only germline heterozygous LoF selection; it "
        "under-calls recessive, somatic, and gain-of-function disease genes, and reflects organism-level essentiality "
        "rather than tissue- or tumour-specific druggability. Use the flag and knockout-tolerance tier to set the "
        "<b>modality strategy and on-target safety expectation</b> \u2014 then confirm against inheritance and molecular "
        "mechanism.", s),
              Paragraph("<b>Data &amp; outputs.</b> Constraint from the gnomAD GraphQL API (v2.1.1 primary, v4.1 "
                        "comparison); disease grounding from ClinGen via MyGene.info. Full per-gene values are in the "
                        "accompanying CSV; figures are provided as PNG and editable SVG.", s["Body"])]

    doc = SimpleDocTemplate(out_path, pagesize=letter, topMargin=56, bottomMargin=52,
                            leftMargin=60, rightMargin=60, title="Genetic Constraint Gating")
    doc.build(story, onFirstPage=hf, onLaterPages=hf)
    return out_path
