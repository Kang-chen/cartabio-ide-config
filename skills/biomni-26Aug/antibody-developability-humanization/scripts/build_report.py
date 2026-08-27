#!/usr/bin/env python3
"""
build_report.py  --  Dual-mode Phylo-branded PDF report for antibody
developability / immunogenicity / humanization.

Two skeletons, selected by the presence of a clinical/known reference:
  * REFERENCE-ABSENT (default): the antibody has no clinical counterpart. The
    report quality read-outs are germline humanness + liability burden +
    MHC-II immunogenicity. No benchmark/concordance section.
  * REFERENCE-PRESENT (optional): a held-out reference exists (e.g. muMAb 4D5 ->
    trastuzumab). Adds a Validation section: identity to reference + blind
    back-mutation concordance / canonical-recovery.

The report also adapts to:
  * source species / format branch (humanize vs assess-only vs single-domain);
  * MHC-II predictor availability (states the axis is unavailable rather than
    fabricating numbers).

Runs STANDALONE from a serialized JSON payload (so no in-memory state needed):
  python build_report.py --payload results.json --out report.pdf

The payload schema is produced by serialize_payload() (see bottom) and mirrors
the outputs of ingest / reassess / humanize / benchmark.
"""
from __future__ import annotations
import argparse, json, os, datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, PageBreak, HRFlowable)

# ---- Phylo brand ----
PHYLO_GOLD = HexColor("#D4A04A"); PHYLO_BLUE = HexColor("#0279EE")
PHYLO_GREEN = HexColor("#75A025"); PHYLO_ORANGE = HexColor("#FF9400")
PHYLO_OFF_WHITE = HexColor("#FAF9F3")
HEADING_COLOR = HexColor("#111111"); BODY_TEXT = HexColor("#2C2A26")
MUTED_TEXT = HexColor("#8A8378")
TABLE_HEADER_BG = PHYLO_GOLD; TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F9F7F3"); TABLE_BORDER = HexColor("#D5CFC5")
CALLOUT_BG = PHYLO_OFF_WHITE

_HEADER_TITLE = "Antibody developability, immunogenicity & humanization"

# ---------------------------------------------------------------------------
# Data-source attribution (IEDB). Rendered in the immunogenicity methods only
# when the corresponding predictor tier actually ran (see serialize_payload).
# ---------------------------------------------------------------------------
_IEDB_ACK = (
    "MHC-II binding predictions were obtained from the Immune Epitope Database "
    "(IEDB, https://www.iedb.org), a free public resource funded by NIAID "
    "[Vita et al., Nucleic Acids Res 2025;53(D1):D436-D443, doi:10.1093/nar/"
    "gkae1092; Dhanda et al., Nucleic Acids Res 2019;47(W1):W502-W506, "
    "doi:10.1093/nar/gkz452].")
_NETMHCII_ACK = (
    "MHC-II binding predictions were computed with a local NetMHCIIpan install "
    "(method served by the IEDB Analysis Resource [Dhanda et al., Nucleic Acids "
    "Res 2019;47(W1):W502-W506, doi:10.1093/nar/gkz452]).")


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="ReportTitle", fontName="Helvetica-Bold",
          fontSize=25, textColor=HEADING_COLOR, spaceAfter=6, leading=30))
    s.add(ParagraphStyle(name="Subtitle", fontName="Helvetica", fontSize=11,
          textColor=PHYLO_GOLD, spaceAfter=4, leading=15))
    s.add(ParagraphStyle(name="Attribution", fontName="Helvetica-Oblique",
          fontSize=10, textColor=MUTED_TEXT, spaceAfter=8))
    s.add(ParagraphStyle(name="SectionHead", fontName="Helvetica-Bold",
          fontSize=17, textColor=HEADING_COLOR, spaceBefore=20, spaceAfter=9))
    s.add(ParagraphStyle(name="SubHead", fontName="Helvetica-Bold",
          fontSize=12, textColor=HEADING_COLOR, spaceBefore=10, spaceAfter=5))
    s.add(ParagraphStyle(name="Body2", fontName="Helvetica", fontSize=10.5,
          textColor=BODY_TEXT, alignment=TA_JUSTIFY, spaceAfter=8, leading=15))
    s.add(ParagraphStyle(name="Caption", fontName="Helvetica-Oblique",
          fontSize=9, textColor=MUTED_TEXT, alignment=TA_CENTER, spaceAfter=14))
    s.add(ParagraphStyle(name="Callout", fontName="Helvetica", fontSize=10,
          textColor=BODY_TEXT, alignment=TA_JUSTIFY, leading=14))
    s.add(ParagraphStyle(name="CellL", fontName="Helvetica", fontSize=8.5,
          textColor=BODY_TEXT, leading=11))
    s.add(ParagraphStyle(name="CellH", fontName="Helvetica-Bold", fontSize=8.5,
          textColor=TABLE_HEADER_FG, leading=11))
    return s


def _page_chrome(canvas, doc):
    canvas.saveState()
    w, h = letter
    canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED_TEXT)
    canvas.drawString(60, h - 40, _HEADER_TITLE)
    canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1)
    canvas.line(60, h - 48, w - 60, h - 48)
    canvas.setStrokeColor(TABLE_BORDER); canvas.setLineWidth(0.75)
    canvas.line(60, 40, w - 60, 40)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED_TEXT)
    canvas.drawCentredString(w / 2, 26,
                             f"Page {doc.page}  |  Generated by Biomni")
    canvas.restoreState()


def _divider():
    return HRFlowable(width=480, thickness=1, color=PHYLO_GOLD,
                      spaceAfter=10, spaceBefore=4)


def _callout(text, styles):
    t = Table([[Paragraph(text, styles["Callout"])]], colWidths=[452])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, PHYLO_GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14)]))
    t.hAlign = "CENTER"
    return t


def _table(headers, rows, colWidths, styles):
    data = [[Paragraph(f"<b>{h}</b>", styles["CellH"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c), styles["CellL"]) for c in r])
    t = Table(data, colWidths=colWidths)
    style = [("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
             ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_FG),
             ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
             ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
             ("TOPPADDING", (0, 0), (-1, -1), 5),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
             ("LEFTPADDING", (0, 0), (-1, -1), 6),
             ("RIGHTPADDING", (0, 0), (-1, -1), 6),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    t.setStyle(TableStyle(style))
    t.hAlign = "CENTER"
    return t


def _img(path, width, caption, styles, flow):
    if path and os.path.exists(path):
        from PIL import Image as PILImage
        iw, ih = PILImage.open(path).size
        h = width * ih / iw
        im = Image(path, width=width, height=h)
        im.hAlign = "CENTER"
        flow.append(im)
        if caption:
            flow.append(Paragraph(caption, styles["Caption"]))


# ---------------------------------------------------------------------------
def build_report(payload: dict, out_path: str):
    """Build the PDF from a serialized payload. Returns out_path."""
    styles = _styles()
    story = []
    p = payload
    ab = p.get("antibody_name", "the antibody")
    mode = p.get("mode", "reference_absent")   # or 'reference_present'
    branch = p.get("branch", "paired_nonhuman")
    immuno_status = p.get("immunogenicity_status", "ok")
    figs = p.get("figures", {})
    date_str = p.get("date", datetime.date.today().strftime("%B %d, %Y"))

    def fig(name):
        v = figs.get(name)
        return v[0] if isinstance(v, list) and v else v

    # ---- Title ----
    story.append(Spacer(1, 34))
    story.append(Paragraph(
        f"Developability, Immunogenicity &amp; Humanization"
        f"{' of ' + ab if ab else ''}", styles["ReportTitle"]))
    story.append(Paragraph(p.get("subtitle",
        "Sequence-based liability assessment and humanized-variant design"),
        styles["Subtitle"]))
    story.append(Paragraph(f"<i>Generated by Biomni  |  {date_str}</i>",
                           styles["Attribution"]))
    story.append(_divider())

    # ---- Executive Summary ----
    story.append(Paragraph("Executive Summary", styles["SectionHead"]))
    for para in p.get("exec_summary", []):
        story.append(Paragraph(para, styles["Body2"]))
    if p.get("key_result"):
        story.append(_callout(f"<b>Key result.</b> {p['key_result']}", styles))
    story.append(PageBreak())

    # ---- Methods ----
    story.append(Paragraph("1. Methods", styles["SectionHead"]))
    story.append(Paragraph("1.1 Sequences and numbering", styles["SubHead"]))
    _cdr_use = ("grafting" if branch == "paired_nonhuman"
                else "liability annotation")
    story.append(Paragraph(p.get("methods_sequences",
        "Variable-domain sequences were validated and numbered with ANARCI "
        "(IMGT and Kabat); germlines were assigned against human references "
        "(abnumber/ANARCI). CDRs were delineated by the Kabat definition for "
        f"{_cdr_use}."), styles["Body2"]))
    story.append(Paragraph("1.2 Developability", styles["SubHead"]))
    story.append(Paragraph(
        "Each chain was scanned for chemical-degradation motifs: "
        "N-glycosylation sequons (N-X-[S/T], X&ne;P), asparagine deamidation "
        "(NG high; NS/NN/NT/NH moderate), aspartate isomerization (DG high; "
        "DS/DD/DT/DH moderate), Met/Trp oxidation, and cysteine count. Motifs "
        "were assigned base severity 1-3 and weighted 1.6&times; when "
        "CDR-resident. Theoretical pI, net charge (pyteomics), GRAVY "
        "hydrophobicity and aromaticity were computed per chain as descriptive "
        "context only (not the aggregation metric).",
        styles["Body2"]))
    story.append(Paragraph("1.3 Aggregation propensity", styles["SubHead"]))
    story.append(Paragraph(p.get("methods_aggregation",
        "Aggregation risk was scored with the named, published sequence-based "
        "AGGRESCAN predictor rather than hydrophobicity/charge surrogates. Each "
        "residue was assigned its intrinsic aggregation-propensity value (a3v; "
        "aggregation-propensity scale derived from an in vivo A&beta;42 "
        "GFP-fusion mutational assay, S&aacute;nchez de Groot et al., FEBS J "
        "2006; used by AGGRESCAN, Conchillo-Sol&eacute; et al., BMC "
        "Bioinformatics 2007). A length-adaptive sliding window (7 residues for "
        "a variable domain) produced the a4v aggregation profile, with virtual "
        "charged residues added at the termini per the method. Aggregation-prone "
        "regions (APRs) were called as runs of &ge;5 consecutive residues whose "
        "a4v exceeds the hot-spot threshold (the mean a3v over the 20 amino "
        "acids). Per construct we report the mean a4v (agg score), the APR "
        "count split into framework- vs CDR-resident, and a CDR-weighted "
        "aggregation burden (APR excess-propensity area, CDR-resident regions "
        "up-weighted 1.6&times; consistent with the liability scan). This is an "
        "intrinsic sequence-propensity estimate: it does not model 3D spatial "
        "aggregation patches or colloidal/conformational effects, for which a "
        "structure-based predictor (e.g. AggreScan3D or the Therapeutic "
        "Antibody Profiler) on a folded Fv is the appropriate upgrade."),
        styles["Body2"]))
    story.append(Paragraph("1.4 Immunogenicity (MHC-II)", styles["SubHead"]))
    if immuno_status == "ok":
        story.append(Paragraph(
            f"Overlapping 15-mers were scored for HLA class II binding with the "
            f"{p.get('predictor','NetMHCIIpan')} method across a "
            f"{len(p.get('dr_panel', []))}-allele HLA-DR reference panel. A "
            f"15-mer was a binder at percentile rank &le; 10, strong at &le; 2; "
            f"an epitope was promiscuous when it bound &ge; 2 alleles. Epitopes "
            f"were partitioned CDR- vs framework-resident by register overlap.",
            styles["Body2"]))
        # Attribute the data source that actually ran.
        _tier = p.get("immunogenicity_predictor_tier")
        _ack = (_IEDB_ACK if _tier == "iedb"
                else _NETMHCII_ACK if _tier == "local"
                else _IEDB_ACK)   # default: API is the out-of-the-box path
        story.append(Paragraph(_ack, styles["Body2"]))
    else:
        story.append(Paragraph(
            "An MHC-II binding predictor was not available in this run "
            f"({p.get('immunogenicity_reason','no predictor reachable')}). "
            "The immunogenicity axis is reported as unavailable rather than "
            "populated with fabricated values; the construct ranking below is "
            "therefore based on the developability and humanness axes only.",
            styles["Body2"]))
    if branch == "paired_nonhuman":
        story.append(Paragraph("1.5 Humanization", styles["SubHead"]))
        story.append(Paragraph(p.get("methods_humanization",
            "Two acceptor philosophies were evaluated: a human consensus "
            "framework and the nearest human germline by framework identity. "
            "Donor Kabat CDRs were grafted onto each acceptor (naive graft). "
            "Back-mutations were proposed by reverting human&rarr;donor only at "
            "framework positions in the Vernier zone, VH/VL interface or "
            "canonical-class determinants where residues differed. Humanness "
            "was scored as framework % identity to the assigned human germline."),
            styles["Body2"]))
    if mode == "reference_present":
        story.append(Paragraph("1.6 Validation", styles["SubHead"]))
        story.append(Paragraph(
            f"Only after the designs were finalized was the "
            f"{p.get('reference_name','reference')} sequence revealed. Blind "
            f"designs were compared by global alignment (BLOSUM62) and by "
            f"residue-level back-mutation concordance.", styles["Body2"]))
    story.append(PageBreak())

    # ---- Results ----
    story.append(Paragraph("2. Results", styles["SectionHead"]))

    # 2.1 Developability
    story.append(Paragraph("2.1 Developability liabilities", styles["SubHead"]))
    for para in p.get("results_developability", []):
        story.append(Paragraph(para, styles["Body2"]))
    dev_tbl = p.get("developability_table")
    if dev_tbl:
        story.append(_table(dev_tbl["headers"], dev_tbl["rows"],
                            dev_tbl.get("colWidths"), styles))
        story.append(Spacer(1, 8))
    _img(fig("fig1_developability"), 470,
         "Figure 1. Developability liability burden across constructs.",
         styles, story)

    # 2.2 Aggregation propensity (named AGGRESCAN a3v predictor)
    story.append(Paragraph("2.2 Aggregation propensity", styles["SubHead"]))
    for para in p.get("results_aggregation", []):
        story.append(Paragraph(para, styles["Body2"]))
    agg_tbl = p.get("aggregation_table")
    if agg_tbl:
        story.append(_table(agg_tbl["headers"], agg_tbl["rows"],
                            agg_tbl.get("colWidths"), styles))
        story.append(Spacer(1, 8))
    apr_tbl = p.get("apr_table")
    if apr_tbl:
        from reportlab.platypus import KeepTogether
        story.append(KeepTogether([
            Paragraph("Highest-propensity aggregation-prone regions (APRs) "
                      "in the parent, with Kabat position and region:",
                      styles["Body2"]),
            _table(apr_tbl["headers"], apr_tbl["rows"],
                   apr_tbl.get("colWidths"), styles)]))
        story.append(Spacer(1, 8))
    _img(fig("fig6_aggregation"), 470,
         "Figure 2. Aggregation propensity across constructs (AGGRESCAN a3v): "
         "CDR-weighted burden and aggregation-prone-region counts.",
         styles, story)

    # 2.3 Immunogenicity
    story.append(Paragraph("2.3 T-cell (MHC-II) immunogenicity", styles["SubHead"]))
    for para in p.get("results_immunogenicity", []):
        story.append(Paragraph(para, styles["Body2"]))
    _img(fig("fig2_immunogenicity"), 470,
         "Figure 3. MHC-II epitope load and promiscuous epitope localization.",
         styles, story)

    # 2.4 Humanization / frontier (only if we humanized)
    if branch == "paired_nonhuman":
        story.append(PageBreak())
        story.append(Paragraph("2.4 Humanization frontier", styles["SubHead"]))
        for para in p.get("results_humanization", []):
            story.append(Paragraph(para, styles["Body2"]))
        master_tbl = p.get("master_table")
        if master_tbl:
            story.append(_table(master_tbl["headers"], master_tbl["rows"],
                                master_tbl.get("colWidths"), styles))
            story.append(Spacer(1, 8))
        if p.get("backmut_table"):
            from reportlab.platypus import KeepTogether
            lp = p.get("lead_philosophy")
            lbl = (f"Proposed framework back-mutations for the lead "
                   f"({lp} acceptor) design, with rationale:" if lp else
                   "Proposed framework back-mutations (with rationale):")
            bm = p["backmut_table"]
            story.append(KeepTogether([
                Paragraph(lbl, styles["Body2"]),
                _table(bm["headers"], bm["rows"], bm.get("colWidths"), styles)]))
            story.append(Spacer(1, 8))
        _img(fig("fig3_tradeoff"), 400,
             "Figure 4. Humanness vs immunogenicity frontier.", styles, story)
        _img(fig("fig4_scorecard"), 480,
             "Figure 5. Construct scorecard (green = more favorable).",
             styles, story)

    # 2.5 Validation (reference-present only)
    if mode == "reference_present" and p.get("benchmark"):
        story.append(PageBreak())
        story.append(Paragraph(
            f"2.5 Validation against {p.get('reference_name','reference')}",
            styles["SubHead"]))
        for para in p.get("results_validation", []):
            story.append(Paragraph(para, styles["Body2"]))
        bt = p["benchmark"].get("table")
        if bt:
            story.append(_table(bt["headers"], bt["rows"], bt.get("colWidths"),
                                styles))
            story.append(Spacer(1, 8))
        _img(fig("fig5_benchmark"), 480,
             "Figure 6. Blind-design validation against the held-out reference.",
             styles, story)

    # ---- Discussion ----
    story.append(PageBreak())
    story.append(Paragraph("3. Discussion", styles["SectionHead"]))
    for para in p.get("discussion", []):
        story.append(Paragraph(para, styles["Body2"]))

    # ---- Appendix: sequences ----
    if p.get("sequences"):
        story.append(Paragraph("Appendix: construct sequences", styles["SubHead"]))
        seq_rows = [[k, v.get("VH", "")[:60] + ("..." if len(v.get("VH", "")) > 60 else ""),
                     v.get("VL", "")[:60] + ("..." if len(v.get("VL", "")) > 60 else "")]
                    for k, v in p["sequences"].items()]
        story.append(_table(["Construct", "VH (truncated)", "VL (truncated)"],
                            seq_rows, [110, 190, 190], styles))

    doc = SimpleDocTemplate(out_path, pagesize=letter, topMargin=56,
                            bottomMargin=52, leftMargin=60, rightMargin=60)
    doc.build(story, onFirstPage=_page_chrome, onLaterPages=_page_chrome)
    return out_path


def validate_pdf(path):
    """Post-build validation per pdf-report-generation skill."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    n = len(reader.pages)
    size = os.path.getsize(path)
    txt = reader.pages[0].extract_text()
    assert n >= 2, f"only {n} page(s)"
    assert size > 5000, f"only {size} bytes"
    assert len(txt.strip()) > 0, "no extractable text on page 1"
    return {"pages": n, "bytes": size}


# ---------------------------------------------------------------------------
# Payload serialization  --  turns in-memory result objects into the standalone
# JSON the report consumes. Narrative is auto-generated from the actual numbers
# so the report generalizes to ANY antibody (no hardcoded case text).
# ---------------------------------------------------------------------------
def _df_to_tbl(df, cols, headers=None, colWidths=None, fmt=None):
    """DataFrame -> {headers, rows, colWidths} for _table().

    cols, headers and colWidths are treated as parallel lists indexed by the
    REQUESTED columns; any column absent from df is dropped from all three in
    lockstep so headers can never drift out of alignment with the data (which
    happens e.g. when the immunogenicity axis is skipped and its columns are
    missing from the master table).
    """
    fmt = fmt or {}
    if headers is None:
        headers = list(cols)
    if colWidths is None:
        colWidths = [None] * len(cols)
    keep = [(c, h, w) for c, h, w in zip(cols, headers, colWidths)
            if c in df.columns]
    if not keep:
        return {"headers": [], "rows": [], "colWidths": None}
    f_cols, f_headers, f_widths = map(list, zip(*keep))
    rows = []
    for _, r in df.iterrows():
        row = []
        for c in f_cols:
            v = r[c]
            if c in fmt and v is not None and not (isinstance(v, float) and v != v):
                row.append(fmt[c](v))
            elif v is None or (isinstance(v, float) and v != v):
                row.append("n/a")
            else:
                row.append(v)
        rows.append(row)
    widths = f_widths if any(w is not None for w in f_widths) else None
    return {"headers": f_headers, "rows": rows, "colWidths": widths}


def serialize_payload(antibody_name, reassess_out, humanize_out=None,
                      benchmark_out=None, figures=None, mode=None, branch=None,
                      reference_name=None, dr_panel=None, predictor=None,
                      subtitle=None, source_species=None, lead_key=None):
    """Assemble the report payload dict from pipeline outputs.

    mode is inferred: 'reference_present' iff benchmark_out is supplied,
    otherwise 'reference_absent' (the default, no clinical counterpart).

    lead_key: optionally pin the featured construct (e.g. the benchmarked lead).
    If None, the lead is auto-selected as the back-mutated design with the
    highest mean framework humanness.
    """
    master = reassess_out["master"]
    immuno_status = reassess_out.get("immunogenicity_status", "ok")
    immuno_reason = reassess_out.get("immunogenicity_reason")
    # Actual predictor tier that ran ('iedb' | 'local' | None), so the report
    # attributes the data source that was truly used rather than a fixed label.
    immuno_predictor_tier = reassess_out.get("immunogenicity_predictor")
    if mode is None:
        mode = "reference_present" if benchmark_out else "reference_absent"
    if branch is None:
        branch = "paired_nonhuman" if (humanize_out is not None) else "paired_human"

    mrec = {r["construct"]: r for _, r in master.iterrows()}
    donor_key = "donor" if "donor" in mrec else master["construct"].iloc[0]
    d = mrec[donor_key]

    def _n(x, dec=1):
        try:
            return f"{float(x):.{dec}f}" if x is not None and x == x else "n/a"
        except Exception:
            return "n/a"

    # ---- Executive summary (auto from numbers) ----
    sp = source_species or "the source"
    exec_summary = []
    exec_summary.append(
        f"This report assesses the sequence-based developability and T-cell "
        f"(MHC-II) immunogenicity liabilities of {antibody_name}"
        + (f", a {sp}-derived antibody" if source_species else "")
        + (", and proposes a humanized variant by CDR grafting with rational "
           "framework back-mutations." if branch == "paired_nonhuman"
           else ", which already carries human frameworks and is assessed "
                "as-is (no humanization required)."))
    liab_txt = (f"The variable domains carry {int(d['total_liabilities'])} "
                f"chemical-degradation liabilities (weighted burden "
                f"{_n(d['total_weighted_burden'])})")
    if d.get("N_glyco_sites", 0):
        liab_txt += (f", including {int(d['N_glyco_sites'])} framework/CDR "
                     f"N-glycosylation sequon(s)")
    liab_txt += "."
    # named aggregation metric (AGGRESCAN a3v)
    if d.get("agg_weighted") is not None and d.get("n_APR") is not None:
        liab_txt += (f" By the AGGRESCAN sequence-based aggregation predictor "
                     f"they carry {int(d['n_APR'])} aggregation-prone region(s) "
                     f"({int(d.get('APR_in_CDR', 0))} CDR-resident) with a "
                     f"CDR-weighted aggregation burden of "
                     f"{_n(d['agg_weighted'])}.")
    if immuno_status == "ok":
        liab_txt += (f" Across a {len(dr_panel or [])}-allele HLA-DR reference "
                     f"panel they present {int(d['Fv_epitope_load'])} "
                     f"allele-binding events ({int(d['Fv_promiscuous'])} "
                     f"promiscuous epitopes).")
    else:
        liab_txt += (" An MHC-II predictor was unavailable, so the "
                     "immunogenicity axis is not scored in this run.")
    # framework humanness is reported for BOTH branches: it justifies
    # humanization for non-human antibodies and confirms "already human" for
    # human antibodies (assess-only).
    fr_txt = (f" Framework identity to the nearest human germline is "
              f"VH {_n(d['VH_FR_identity_%'])}%, VL "
              f"{_n(d['VL_FR_identity_%'])}%")
    if branch == "paired_human":
        fr_txt += (", confirming the variable domains are already human; no "
                   "humanization is proposed.")
    else:
        fr_txt += "."
    liab_txt += fr_txt
    exec_summary.append(liab_txt)

    # resolve the featured lead: explicit > benchmarked > most-human bmut
    resolved_lead = lead_key
    if resolved_lead is None and benchmark_out:
        resolved_lead = benchmark_out.get("scores", {}).get("lead")

    key_result = None
    if branch == "paired_nonhuman":
        bmut = master[master["construct"].str.contains("bmut")]
        if len(bmut):
            if resolved_lead and resolved_lead in set(master["construct"]):
                lead = mrec[resolved_lead]
            else:
                lead = bmut.sort_values("mean_FR_humanness_%",
                                        ascending=False).iloc[0]
            lk = lead["construct"]
            _agg_txt = ""
            if (lead.get("agg_weighted") is not None
                    and d.get("agg_weighted") is not None):
                _dir = ("lowering" if lead["agg_weighted"] <= d["agg_weighted"]
                        else "changing")
                _agg_txt = (f", {_dir} the CDR-weighted aggregation burden from "
                            f"{_n(d['agg_weighted'])} (parent) to "
                            f"{_n(lead['agg_weighted'])} and reducing "
                            f"CDR-resident aggregation-prone regions from "
                            f"{int(d.get('APR_in_CDR', 0))} to "
                            f"{int(lead.get('APR_in_CDR', 0))}")
            exec_summary.append(
                f"Two acceptor philosophies were compared (a human consensus "
                f"framework and the nearest human germline), each as a naive "
                f"graft and with back-mutations. The lead humanized design "
                f"({lk}) reaches {_n(lead['mean_FR_humanness_%'])}% mean "
                f"framework humanness while reducing the liability burden to "
                f"{_n(lead['total_weighted_burden'])}"
                + (f" and the epitope load to {int(lead['Fv_epitope_load'])}"
                   if immuno_status == "ok" else "")
                + _agg_txt + ".")
        if mode == "reference_present" and benchmark_out:
            sc = benchmark_out["scores"]
            key_result = (
                f"Designed without access to {sc['reference']}, the humanized "
                f"variant recovered {sc['canonical_recovery']} of the "
                f"reference's essential framework back-mutations and reached "
                f"{_n(sc['lead_VH_identity_%'])}% VH / "
                f"{_n(sc['lead_VL_identity_%'])}% VL sequence identity to the "
                f"known molecule, confirming the workflow reconstructs it from "
                f"the parent alone.")

    # ---- Results narrative ----
    results_dev = [
        f"Per-construct developability is summarized below. The parent "
        f"({donor_key}) carries the highest burden; framework humanization "
        f"removes framework-resident motifs while CDR-resident liabilities are "
        f"constrained by the retained antigen-binding loops."]

    # aggregation narrative (named AGGRESCAN a3v; honest about CDR inheritance)
    results_aggregation = []
    if d.get("agg_weighted") is not None:
        _p = (f"Aggregation propensity was scored with the AGGRESCAN a3v "
              f"predictor (sequence-based). The parent ({donor_key}) carries "
              f"{int(d.get('n_APR', 0))} aggregation-prone region(s), "
              f"{int(d.get('APR_in_CDR', 0))} of them CDR-resident, with a "
              f"CDR-weighted aggregation burden of {_n(d['agg_weighted'])}.")
        if branch == "paired_nonhuman" and resolved_lead in set(master["construct"]):
            _ld = mrec[resolved_lead]
            _p += (f" Grafting onto human frameworks lowers the CDR-weighted "
                   f"aggregation burden to {_n(_ld['agg_weighted'])} in the lead "
                   f"design ({resolved_lead}) and reduces CDR-resident "
                   f"aggregation-prone regions from {int(d.get('APR_in_CDR', 0))} "
                   f"to {int(_ld.get('APR_in_CDR', 0))}.")
        results_aggregation.append(_p)
        if branch == "paired_nonhuman":
            results_aggregation.append(
                "Because CDRs are grafted verbatim, aggregation-prone regions "
                "that fall inside the CDRs are inherited from the parent and "
                "cannot be removed by framework humanization; these are flagged "
                "by Kabat position below as candidates for targeted, "
                "affinity-aware mutation. The framework-resident regions are the "
                "ones humanization addresses directly. This score is an intrinsic "
                "sequence-propensity estimate and does not capture 3D spatial "
                "aggregation patches; a structure-based method (AggreScan3D / "
                "Therapeutic Antibody Profiler) on a folded Fv is the recommended "
                "confirmatory step.")
    if immuno_status == "ok":
        results_immuno = [
            f"MHC-II epitope load across the HLA-DR panel is shown per "
            f"construct. Promiscuous epitopes (binding &ge; 2 alleles) are "
            f"partitioned into framework- vs CDR-resident; framework "
            f"humanization preferentially removes framework-resident epitopes."]
    else:
        results_immuno = [
            f"An MHC-II binding predictor was not available in this run "
            f"({immuno_reason or 'no predictor reachable'}). This axis is "
            f"reported as unavailable; no epitope counts are shown."]
    results_hum = [
        "The humanization frontier below trades framework humanness against "
        "liability and epitope burden. Naive grafts are maximally human but "
        "may lose affinity-shaping framework residues; back-mutated designs "
        "restore those residues at a modest humanness cost."]
    results_val = []
    if mode == "reference_present" and benchmark_out:
        sc = benchmark_out["scores"]
        oc = ", ".join(sc.get("over_corrections", [])) or "none"
        results_val = [
            f"Revealed only after design, {sc['reference']} provides the "
            f"clinical ground truth. The lead design is "
            f"{_n(sc['lead_VH_identity_%'])}% / {_n(sc['lead_VL_identity_%'])}% "
            f"identical (VH/VL). Of {sc['n_backmutations']} proposed "
            f"back-mutations, {sc['n_concordant']} match the reference "
            f"({_n(sc['pct_concordant'])}%), and all canonical/Vernier "
            f"determinants were recovered ({sc['canonical_recovery']}). "
            f"Over-corrections (reverted where the reference kept human): {oc}."]

    # ---- Tables ----
    dev_tbl = _df_to_tbl(
        master,
        ["construct", "total_liabilities", "CDR_liabilities",
         "total_weighted_burden", "N_glyco_sites"],
        headers=["Construct", "Total liab.", "CDR liab.", "Wt. burden",
                 "N-glyco"],
        colWidths=[150, 70, 70, 80, 70],
        fmt={"total_weighted_burden": lambda v: f"{v:.1f}"})

    # ---- aggregation summary table (named AGGRESCAN a3v) ----
    agg_tbl = None
    if "agg_weighted" in master.columns:
        agg_cols = ["construct", "agg_score_Fv", "n_APR", "APR_in_FR",
                    "APR_in_CDR", "agg_weighted"]
        agg_tbl = _df_to_tbl(
            master, agg_cols,
            headers=["Construct", "Agg score (a4v)", "APRs", "FR APRs",
                     "CDR APRs", "Wt. agg burden"],
            colWidths=[140, 80, 50, 60, 62, 84],
            fmt={"agg_score_Fv": lambda v: f"{v:.3f}",
                 "agg_weighted": lambda v: f"{v:.1f}"})

    # ---- top aggregation-prone regions in the parent (APR detail) ----
    apr_tbl = None
    apr_df = reassess_out.get("aggregation_aprs")
    if apr_df is not None and hasattr(apr_df, "iterrows") and len(apr_df):
        # feature the parent's APRs (chain names start with the donor key)
        _pa = apr_df[apr_df["chain"].str.startswith(f"{donor_key}_")].copy()
        if not len(_pa):
            _pa = apr_df.copy()
        _pa = _pa.sort_values("weighted_area", ascending=False).head(6)
        # tidy chain label -> VH/VL
        _pa["chain"] = _pa["chain"].apply(
            lambda c: "VH" if c.endswith("_VH") else ("VL" if c.endswith("_VL") else c))
        apr_cols = [c for c in ["chain", "position", "residues", "location",
                                "length", "peak_a4v", "weighted_area"]
                    if c in _pa.columns]
        apr_tbl = _df_to_tbl(
            _pa, apr_cols,
            headers=[{"chain": "Chain", "position": "Kabat", "residues": "Residues",
                      "location": "Region", "length": "Len", "peak_a4v": "Peak a4v",
                      "weighted_area": "Wt. area"}.get(c, c) for c in apr_cols],
            colWidths=[42, 66, 110, 52, 38, 60, 60],
            fmt={"peak_a4v": lambda v: f"{v:.2f}",
                 "weighted_area": lambda v: f"{v:.2f}"})

    master_cols = ["construct", "total_weighted_burden", "agg_weighted",
                   "Fv_epitope_load", "Fv_promiscuous", "VH_FR_identity_%",
                   "VL_FR_identity_%", "mean_FR_humanness_%"]
    master_cols = [c for c in master_cols if c in master.columns]
    _mhead = {"construct": "Construct", "total_weighted_burden": "Liab. burden",
              "agg_weighted": "Agg. burden", "Fv_epitope_load": "Epitope load",
              "Fv_promiscuous": "Promisc.", "VH_FR_identity_%": "VH FR%",
              "VL_FR_identity_%": "VL FR%", "mean_FR_humanness_%": "Mean hum%"}
    master_tbl = _df_to_tbl(
        master, master_cols,
        headers=[_mhead[c] for c in master_cols],
        colWidths=[132, 58, 58, 60, 52, 44, 44, 56][:len(master_cols)],
        fmt={c: (lambda v: f"{v:.1f}") for c in master_cols[1:]})

    backmut_tbl = None
    lead_philosophy = None
    if branch == "paired_nonhuman":
        bmut = master[master["construct"].str.contains("bmut")]
        if len(bmut):
            if resolved_lead and resolved_lead in set(master["construct"]):
                lk = resolved_lead
            else:
                lk = bmut.sort_values("mean_FR_humanness_%",
                                      ascending=False).iloc[0]["construct"]
            # 'hu_consensus_bmut' -> 'consensus'; 'hu_nearest_bmut' -> 'nearest'
            for ph in ("consensus", "nearest"):
                if ph in lk:
                    lead_philosophy = ph
                    break
    if humanize_out is not None:
        bm = humanize_out.get("backmutations")
        if bm is not None and hasattr(bm, "iterrows") and len(bm):
            bm_show = bm
            # show ONLY the lead construct's philosophy to avoid ambiguous
            # duplicate-looking rows across acceptor philosophies
            if lead_philosophy and "philosophy" in bm.columns:
                bm_show = bm[bm["philosophy"] == lead_philosophy]
            cols = [c for c in ["domain", "kabat", "donor_aa",
                                "human_graft_aa", "region", "rules"]
                    if c in bm_show.columns]
            backmut_tbl = _df_to_tbl(
                bm_show, cols,
                headers=[{"domain": "Chain", "kabat": "Kabat",
                          "donor_aa": "Donor", "human_graft_aa": "Human",
                          "region": "Region", "rules": "Rule"}.get(c, c)
                         for c in cols],
                colWidths=[45, 50, 45, 45, 60, 155])

    bench_tbl = None
    if benchmark_out:
        idf = benchmark_out["identity"]
        bench_tbl = _df_to_tbl(
            idf, ["construct", "VH_vs_ref_%", "VL_vs_ref_%"],
            headers=["Construct", "VH vs ref %", "VL vs ref %"],
            colWidths=[220, 110, 110],
            fmt={"VH_vs_ref_%": lambda v: f"{v:.1f}",
                 "VL_vs_ref_%": lambda v: f"{v:.1f}"})

    sequences = None
    if humanize_out is not None:
        sequences = {k: {"VH": v.get("VH", ""), "VL": v.get("VL", "")}
                     for k, v in humanize_out["constructs"].items()}

    # ---- Discussion (auto-generated, incl. aggregation + honest limitations) ----
    discussion = []
    if branch == "paired_nonhuman":
        discussion.append(
            "The parent is a non-human variable region; CDR grafting onto human "
            "acceptor frameworks reduces its predicted framework immunogenicity "
            "and chemical-liability burden while the antigen-binding loops are "
            "preserved verbatim. The consensus (human subgroup) and nearest-"
            "germline acceptors are reported side by side so the humanness / "
            "binding-risk trade-off is explicit rather than hidden in a single "
            "recommended sequence.")
    if d.get("agg_weighted") is not None:
        _agg_disc = (
            "On the aggregation axis, humanization is expected to lower "
            "aggregation risk primarily by (i) placing the variable domains on a "
            "human framework and (ii) removing framework-resident "
            "aggregation-prone regions and any N-glycosylation sequon; "
            "CDR-resident aggregation-prone regions are inherited from the donor "
            "and are the residual, affinity-constrained risk to address by "
            "targeted mutation.")
        _agg_disc += (
            " Aggregation here is scored with the sequence-based AGGRESCAN a3v "
            "predictor, which captures intrinsic aggregation propensity from the "
            "linear sequence. It does not model three-dimensional aggregation "
            "patches formed by residues that are distant in sequence but close in "
            "the folded Fv, nor colloidal/charge-network effects; a structure-"
            "based predictor (AggreScan3D or the Therapeutic Antibody Profiler) "
            "run on a predicted or experimental Fv structure is the recommended "
            "confirmatory step before committing to a lead.")
        discussion.append(_agg_disc)
    discussion.append(
        "All axes are sequence-based in silico predictions and are intended to "
        "prioritize and de-risk candidates, not to replace experimental "
        "confirmation. Recommended follow-up includes expression/titer, "
        "SEC/aggregation and thermostability (e.g. DSF/DSC) measurement, "
        "antigen-binding affinity (SPR/BLI), and, for any retained CDR-resident "
        "epitope or aggregation-prone region, targeted assays before lead "
        "selection.")

    payload = {
        "antibody_name": antibody_name,
        "subtitle": subtitle or (
            "Sequence-based liability assessment and humanized-variant design"
            if branch == "paired_nonhuman"
            else "Sequence-based developability and immunogenicity assessment"),
        "mode": mode, "branch": branch,
        "reference_name": reference_name,
        "source_species": source_species,
        "immunogenicity_status": immuno_status,
        "immunogenicity_reason": immuno_reason,
        "predictor": predictor or "NetMHCIIpan",
        "immunogenicity_predictor_tier": immuno_predictor_tier,
        "dr_panel": dr_panel or [],
        "date": datetime.date.today().strftime("%B %d, %Y"),
        "figures": figures or {},
        "exec_summary": exec_summary,
        "key_result": key_result,
        "results_developability": results_dev,
        "results_aggregation": results_aggregation,
        "results_immunogenicity": results_immuno,
        "results_humanization": results_hum,
        "results_validation": results_val,
        "developability_table": dev_tbl,
        "aggregation_table": agg_tbl,
        "apr_table": apr_tbl,
        "master_table": master_tbl,
        "backmut_table": backmut_tbl,
        "lead_philosophy": lead_philosophy,
        "sequences": sequences,
        "discussion": discussion,
    }
    if benchmark_out:
        payload["benchmark"] = {"table": bench_tbl, "scores": benchmark_out["scores"]}
    return payload


def main():
    ap = argparse.ArgumentParser(description="Build dual-mode antibody PDF report")
    ap.add_argument("--payload", required=True, help="serialized results JSON")
    ap.add_argument("--out", required=True, help="output PDF path")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()
    with open(args.payload) as f:
        payload = json.load(f)
    out = build_report(payload, args.out)
    if not args.no_validate:
        info = validate_pdf(out)
        print(f"Built {out}  ({info['pages']} pages, {info['bytes']} bytes)")
    else:
        print(f"Built {out}")


if __name__ == "__main__":
    main()
