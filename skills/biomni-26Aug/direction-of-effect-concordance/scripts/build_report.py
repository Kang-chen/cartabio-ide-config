#!/usr/bin/env python3
"""
build_report.py -- LAYOUT-ONLY Phylo-branded PDF engine for direction-of-effect concordance.

It authors NO science. It reads:
  RUN/synthesis.json      (all narrative text; agent-authored, post citation-gate)
  RUN/references.json     (ordered [{n, text}], verbatim verified)
  RUN/data/evidence_matrix.csv, RUN/data/consensus_calls.csv
  RUN/data/citation_verification.json
  RUN/figures/*.png (+ fig_manifest.csv)
  optional infographic PNG
...and lays them out with the Phylo brand system (see the pdf-report-generation skill).

Usage:
  python build_report.py --run RUN --out /mnt/results/report_<slug>.pdf \
      [--infographic RUN/figures/infographic.png]
"""
import argparse, datetime, json, os, re, sys
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, Image, HRFlowable, KeepTogether)

# ---- Phylo palette ----
GOLD = HexColor("#D4A04A"); HEADING = HexColor("#111111"); BODY = HexColor("#2C2A26")
MUTED = HexColor("#8A8378"); TH_BG = GOLD; TH_FG = HexColor("#FFFFFF")
ALT = HexColor("#F9F7F3"); BORDER = HexColor("#D5CFC5"); CALLOUT = HexColor("#FAF9F3")
BLUE = HexColor("#0279EE"); GREEN = HexColor("#75A025"); ORANGE = HexColor("#FF9400")
RED = HexColor("#B0413E")
VOTE_COLOR = {"INHIBIT": BLUE, "ACTIVATE": ORANGE, "CONTESTED": RED,
              "not_informative": MUTED}
TIER_COLOR = {"High": BLUE, "High-Moderate": GREEN, "Moderate": GOLD, "Low-Contested": RED}

REPL = {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
        "\u2015": "-", "\u2212": "-", "\u00ad": "-", "\u2043": "-", "\u2018": "'",
        "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " ", "\u200b": "",
        "\u00ae": "(R)", "\u2122": "(TM)", "\u00a9": "(c)", "\u2606": "", "\u2605": "",
        "\ufe0f": ""}


def clean(s):
    # Coerce nullable DataFrame values (NaN, None, pd.NA, numpy nan) to a safe empty
    # string BEFORE str() turns them into the literal text "nan"/"None", which would
    # otherwise be passed to Paragraph() and printed in the PDF. ReportLab also raises
    # on bare float('nan') in some table cells.
    if s is None:
        s = ""
    else:
        try:
            # pandas/numpy missing-value sentinels
            if pd.isna(s):
                s = ""
        except (TypeError, ValueError):
            pass
    s = str(s)
    if s.lower() in ("nan", "none", "<na>", "null"):
        s = ""
    for k, v in REPL.items():
        s = s.replace(k, v)
    s = "".join(ch if ord(ch) < 128 else "" for ch in s)
    return re.sub(r"\s{2,}", " ", s).strip()


def gate_status(gate, syn):
    """Return the authoritative doi_layer_status from the citation-verification gate
    (citation_verification.json), overriding synthesis.json's self-reported value so the
    two never disagree in the PDF. Falls back to synthesis.json only when the gate file is
    absent."""
    gs = gate.get("doi_layer_status") or gate.get("status")
    if gs:
        return gs
    return syn.get("doi_layer_status", "n/a")


def assert_citations_resolve(syn, refs):
    """Pre-build defense: every [n] index cited in synthesis.json must have a matching entry
    in references.json. An orphan [n] renders an unresolvable marker in the PDF body. Fail
    loudly so the agent fixes references.json / synthesis.json before the PDF is built."""
    pat = re.compile(r"\[(\d+)\]")
    cited = set()
    for m in pat.findall(json.dumps(syn)):
        cited.add(int(m))
    ref_indices = set()
    for entry in refs:
        n = entry.get("n")
        if n is not None:
            try:
                ref_indices.add(int(n))
            except (TypeError, ValueError):
                pass
    orphans = sorted(cited - ref_indices)
    if orphans:
        sys.exit(f"ERROR: build_report.py pre-build assertion failed -- citation index(es) "
                 f"{orphans} are cited in synthesis.json but have no entry in references.json. "
                 f"Add the missing reference(s) or drop the citation(s) before building the PDF.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--infographic", default=None)
    args = ap.parse_args()
    RUN = args.run
    syn = json.load(open(os.path.join(RUN, "synthesis.json")))
    refs = json.load(open(os.path.join(RUN, "references.json")))
    assert_citations_resolve(syn, refs)
    emat = pd.read_csv(os.path.join(RUN, "data", "evidence_matrix.csv")).fillna("")
    calls = pd.read_csv(os.path.join(RUN, "data", "consensus_calls.csv")).fillna("")
    gate = {}
    gp = os.path.join(RUN, "data", "citation_verification.json")
    if os.path.exists(gp):
        gate = json.load(open(gp))
    fig_caps = {}
    fmp = os.path.join(RUN, "figures", "fig_manifest.csv")
    if os.path.exists(fmp):
        for _, r in pd.read_csv(fmp).fillna("").iterrows():
            fig_caps[r["file"]] = r["caption"]
    date_str = datetime.date.today().strftime("%B %d, %Y")
    title = clean(syn.get("title", "Direction-of-Effect Concordance"))

    styles = getSampleStyleSheet()

    def add(name, **kw):
        if name in styles.byName:
            for k, v in kw.items():
                setattr(styles[name], k, v)
        else:
            styles.add(ParagraphStyle(name=name, **kw))

    add("RTitle", fontName="Helvetica-Bold", fontSize=22, textColor=HEADING, leading=27, spaceAfter=6)
    add("Sub", fontName="Helvetica", fontSize=11.5, textColor=GOLD, spaceAfter=4)
    add("Attr", fontName="Helvetica-Oblique", fontSize=9.5, textColor=MUTED, spaceAfter=8)
    add("H1", fontName="Helvetica-Bold", fontSize=15.5, textColor=HEADING, spaceBefore=18, spaceAfter=8)
    add("H2", fontName="Helvetica-Bold", fontSize=12, textColor=HEADING, spaceBefore=10, spaceAfter=4)
    add("Body2", fontName="Helvetica", fontSize=10, textColor=BODY, alignment=TA_JUSTIFY, spaceAfter=7, leading=14.5)
    add("Cap", fontName="Helvetica-Oblique", fontSize=8.5, textColor=MUTED, alignment=TA_CENTER, spaceAfter=12)
    add("CellH", fontName="Helvetica-Bold", fontSize=7.6, textColor=TH_FG, leading=10)
    add("Cell", fontName="Helvetica", fontSize=7.8, textColor=BODY, leading=9.6)
    add("CellB", fontName="Helvetica-Bold", fontSize=7.8, textColor=BODY, leading=9.6)
    add("Ref", fontName="Helvetica", fontSize=8.2, textColor=BODY, leading=11, spaceAfter=3)
    add("CalloutTxt", fontName="Helvetica", fontSize=9.2, textColor=BODY, leading=13.5, alignment=TA_LEFT)

    def divider():
        return HRFlowable(width=480, thickness=1, color=GOLD, spaceAfter=10, spaceBefore=4)

    def callout(text):
        t = Table([[Paragraph(clean(text), styles["CalloutTxt"])]], colWidths=[470])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CALLOUT),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER), ("LINEBEFORE", (0, 0), (0, -1), 3, GOLD),
            ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 13), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
        t.hAlign = "CENTER"
        return t

    def para(txt, style="Body2"):
        return Paragraph(clean(txt), styles[style])

    def fig(fname, caption=None, w=468, h=None):
        p = os.path.join(RUN, "figures", fname)
        if not os.path.exists(p):
            return None
        from PIL import Image as PILImage
        iw, ih = PILImage.open(p).size
        height = h or w * ih / iw
        img = Image(p, width=w, height=height)
        img.hAlign = "CENTER"
        cap = caption or fig_caps.get(fname, "")
        return KeepTogether([img, Paragraph(clean(cap), styles["Cap"])]) if cap else img

    def page_chrome(canvas, doc):
        canvas.saveState(); w, h = letter
        canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
        canvas.drawString(60, h - 40, clean(title)[:95])
        canvas.setStrokeColor(GOLD); canvas.setLineWidth(1); canvas.line(60, h - 48, w - 60, h - 48)
        canvas.setStrokeColor(BORDER); canvas.setLineWidth(0.75); canvas.line(60, 40, w - 60, 40)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
        canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
        canvas.restoreState()

    S = []
    # ---- Title ----
    S.append(Spacer(1, 26))
    S.append(para(title, "RTitle"))
    if syn.get("subtitle"):
        S.append(para(syn["subtitle"], "Sub"))
    S.append(Paragraph(f"<i>Generated by Biomni  |  {date_str}</i>", styles["Attr"]))
    S.append(divider())

    # ---- Infographic ----
    if args.infographic and os.path.exists(args.infographic):
        from PIL import Image as PILImage
        iw, ih = PILImage.open(args.infographic).size
        w = 480; img = Image(args.infographic, width=w, height=w * ih / iw)
        img.hAlign = "CENTER"
        S.append(img)
        S.append(Paragraph("Figure 1. Direction-of-effect summary.", styles["Cap"]))

    # ---- Executive summary ----
    if syn.get("executive_summary"):
        S.append(para("Executive Summary", "H1"))
        for pnode in str(syn["executive_summary"]).split("\n\n"):
            S.append(para(pnode))
    for c in syn.get("callouts", []):
        S.append(callout(c))

    # consensus summary figure early
    cfig = fig("fig2_consensus_summary.png")
    if cfig:
        S.append(Spacer(1, 6)); S.append(cfig)

    # ---- Introduction ----
    if syn.get("introduction"):
        S.append(para("Introduction", "H1")); S.append(para(syn["introduction"]))

    # ---- Methods ----
    S.append(PageBreak())
    S.append(para("Methods", "H1"))
    if syn.get("methods"):
        for pnode in str(syn["methods"]).split("\n\n"):
            S.append(para(pnode))
    # direction rule table
    rule = syn.get("direction_rule_table")
    if rule:
        data = [[Paragraph(f"<b>{clean(c)}</b>", styles["CellH"]) for c in rule[0]]]
        for row in rule[1:]:
            data.append([Paragraph(clean(str(c)), styles["Cell"]) for c in row])
        t = Table(data, colWidths=[110, 360], repeatRows=1); t.hAlign = "CENTER"
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), TH_BG), ("TEXTCOLOR", (0, 0), (-1, 0), TH_FG),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
        S.append(t)
        S.append(Paragraph("Table 1. Per-axis raw-readout to therapeutic-direction mapping.", styles["Cap"]))
    if gate:
        S.append(para(f"<b>Citation integrity.</b> Citation-verification gate status: "
                      f"<b>{clean(gate_status(gate, syn))}</b> "
                      f"({gate.get('n_indices_used','?')} citation indices checked against "
                      f"{gate.get('n_records_indexed','?')} retrieved records"
                      f"{'; transcript re-checked' if gate.get('transcript_checked') else ''})."))

    # ---- Results ----
    S.append(para("Results", "H1"))
    if syn.get("results_intro"):
        S.append(para(syn["results_intro"]))

    # evidence matrix table
    S.append(para("Evidence matrix", "H2"))
    hdr = ["Target", "Axis", "Raw readout (direction of effect)", "Vote", "Refs"]
    data = [[Paragraph(f"<b>{h}</b>", styles["CellH"]) for h in hdr]]
    for _, r in emat.iterrows():
        vote = r["vote"]
        vc = VOTE_COLOR.get(vote, BODY)
        vote_disp = clean(vote).replace("not_informative", "not informative")
        data.append([Paragraph(f"<b>{clean(r['target'])}</b>", styles["CellB"]),
                     Paragraph(clean(r["axis"]), styles["Cell"]),
                     Paragraph(clean(r["raw_readout"]), styles["Cell"]),
                     Paragraph(f"<b>{vote_disp}</b>",
                               ParagraphStyle("v", parent=styles["Cell"], textColor=vc,
                                              fontName="Helvetica-Bold")),
                     Paragraph(clean(str(r.get("cites", ""))), styles["Cell"])])
    t = Table(data, colWidths=[44, 80, 232, 66, 60], repeatRows=1); t.hAlign = "CENTER"
    ts = [("BACKGROUND", (0, 0), (-1, 0), TH_BG), ("TEXTCOLOR", (0, 0), (-1, 0), TH_FG),
          ("GRID", (0, 0), (-1, -1), 0.5, BORDER), ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
          ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 4),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 5),
          ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    for i in range(2, len(data), 2):
        ts.append(("BACKGROUND", (0, i), (-1, i), ALT))
    t.setStyle(TableStyle(ts))
    S.append(t)
    S.append(Paragraph("Table 2. Per-target, per-axis directional evidence and votes. "
                       "Bracketed numbers are references.", styles["Cap"]))

    hfig = fig("fig1_evidence_matrix.png")
    if hfig:
        S.append(hfig)

    # per-target findings
    if syn.get("per_target_sections"):
        S.append(para("Per-target findings", "H2"))
        for sec in syn["per_target_sections"]:
            verdict = clean(sec.get("verdict", ""))
            conf = clean(sec.get("confidence", ""))
            head = f"<b>{clean(sec.get('target',''))} - {verdict}"
            head += f" ({conf})</b>" if conf else "</b>"
            S.append(para(head + "<br/>" + clean(sec.get("body", ""))))
            if sec.get("figure"):
                ff = fig(sec["figure"])
                if ff:
                    S.append(ff)

    # consensus table
    S.append(para("Consensus, confidence, and discordance flags", "H2"))
    hdr2 = ["Target", "Consensus", "Concordance", "Confidence tier", "Key flag"]
    d2 = [[Paragraph(f"<b>{h}</b>", styles["CellH"]) for h in hdr2]]
    for _, r in calls.iterrows():
        cc = VOTE_COLOR.get(r["consensus"], BODY)
        d2.append([Paragraph(f"<b>{clean(r['target'])}</b>", styles["CellB"]),
                   Paragraph(f"<b>{clean(r['consensus'])}</b>",
                             ParagraphStyle("c", parent=styles["Cell"], textColor=cc,
                                            fontName="Helvetica-Bold")),
                   Paragraph(clean(str(r["concordance"])), styles["Cell"]),
                   Paragraph(clean(str(r["confidence"])), styles["Cell"]),
                   Paragraph(clean(str(r["key_flag"])), styles["Cell"])])
    t2 = Table(d2, colWidths=[46, 66, 62, 106, 208], repeatRows=1); t2.hAlign = "CENTER"
    t2.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), TH_BG), ("TEXTCOLOR", (0, 0), (-1, 0), TH_FG),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER), ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
    S.append(t2)
    S.append(Paragraph("Table 3. Consensus calls with confidence tiers and the single most "
                       "important flag per target.", styles["Cap"]))

    # ---- Discussion / Limitations / Next steps / Resources ----
    for key, head in [("discussion", "Discussion"), ("limitations", "Limitations"),
                      ("next_steps", "Next Steps"),
                      ("biomni_resources", "Relevant Biomni Resources")]:
        if syn.get(key):
            S.append(para(head, "H1"))
            for pnode in str(syn[key]).split("\n\n"):
                S.append(para(pnode))

    # ---- References ----
    S.append(PageBreak())
    S.append(para("References", "H1"))
    for entry in sorted(refs, key=lambda e: e.get("n", 0)):
        S.append(Paragraph(f"{entry.get('n')}. {clean(entry.get('text',''))}", styles["Ref"]))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    doc = SimpleDocTemplate(args.out, pagesize=letter, topMargin=52, bottomMargin=52,
                            leftMargin=60, rightMargin=60, title=clean(title))
    doc.build(S, onFirstPage=page_chrome, onLaterPages=page_chrome)
    print(f"PDF -> {args.out} ({os.path.getsize(args.out)} bytes)")


if __name__ == "__main__":
    main()
