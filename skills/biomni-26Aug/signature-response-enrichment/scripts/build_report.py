#!/usr/bin/env python3
"""
build_report.py — Stage 6 of signature-response-enrichment.

Assemble the media-checkable PDF from a run config + the catalog/stat CSVs. The report
is PARAMETERIZED (nothing about TYK2/adalimumab/psoriasis is hardcoded); all content
comes from `--config` (JSON) and the CSVs it points to.

Report order (fixed template):
  Title
  -> Table 1: discovery catalog (all screened datasets + include/exclude reasons)
  -> Summary + headline callout
  -> Methods (datasets, signatures+coverage, GSVA settings,
              "Which test produced which result" mapping block)
  -> Results (analysed-cohort table, dGSVA endpoint, CAMERA, per-gene DE, pharmacodynamic)
  -> Limitations, Next steps, References

Every figure/table/subheading names the method that produced it
(GSVA dGSVA / CAMERA / limma-voom / Fisher). The headline presents the dGSVA concordance
AND the FDR-adjusted results side by side, with honest sensitivity caveats.

Usage:
  python build_report.py --config run_config.json --out report.pdf

See references/worked_example.md for a complete run_config.json example.

IMPORTANT: write the PDF to /workspace first if targeting an S3-backed path that rejects
random-access writes, then copy with a shell `cp` to /mnt/results. (ReportLab writes
sequentially, so direct write to /mnt/results usually works; the validator reads it back.)
"""
import argparse
import json
import os
import sys

import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assets"))
import report_style as RS  # noqa: E402


def P(txt, style):
    return Paragraph(txt, style)


def divider():
    from reportlab.platypus import HRFlowable
    return HRFlowable(width="100%", thickness=0.8, color=RS.colors.HexColor(RS.PHYLO_GOLD),
                      spaceBefore=4, spaceAfter=6)


def catalog_table(catalog_csv, S, col_widths=None):
    """Table 1 - the discovery catalog. Groups clearly off-target rows into a footnote row
    only if the config pre-summarized them; otherwise lists every row as-is."""
    df = pd.read_csv(catalog_csv).fillna("")
    # choose the compact, report-friendly columns if present
    prefer = ["accession", "platform", "treatments", "tissue",
              "response_metric_in_metadata", "decision"]
    cols = [c for c in prefer if c in df.columns] or list(df.columns)
    header_labels = {
        "accession": "Accession", "platform": "Platform", "treatments": "Treatment(s)",
        "tissue": "Tissue / design", "response_metric_in_metadata": "Response in metadata",
        "decision": "Decision (reason)",
    }
    head = [P(header_labels.get(c, c), S["TableHead"]) for c in cols]
    rows = [head]
    for _, r in df.iterrows():
        dec = str(r.get("decision", ""))
        reason = str(r.get("reason", ""))
        cells = []
        for c in cols:
            v = str(r.get(c, ""))
            if c == "decision" and reason:
                v = f"{dec} &mdash; {reason}" if dec else reason
            cells.append(P(v, S["TableCell"]))
        rows.append(cells)
    if col_widths is None:
        # letter usable width ~ 6.5in = 468pt; distribute
        n = len(cols)
        col_widths = [468.0 / n] * n
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(RS.table_style())
    return t


def generic_table(csv_path, S, caption=None, col_widths=None, header_map=None):
    df = pd.read_csv(csv_path).fillna("")
    cols = list(df.columns)
    header_map = header_map or {}
    head = [P(header_map.get(c, c), S["TableHead"]) for c in cols]
    rows = [head] + [[P(str(r[c]), S["TableCell"]) for c in cols]
                     for _, r in df.iterrows()]
    if col_widths is None:
        n = len(cols)
        col_widths = [468.0 / n] * n
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(RS.table_style())
    return t


def build(config, out_path):
    S = RS.build_styles()
    story = []

    meta = config["meta"]
    story.append(P(meta["title"], S["Title"]))
    if meta.get("subtitle"):
        story.append(P(meta["subtitle"], S["Subtitle"]))
    if meta.get("attribution"):
        story.append(P(meta["attribution"], S["Attribution"]))
    story.append(divider())

    # ---- Table 1: discovery catalog (evidence base BEFORE claims) ----
    story.append(P("Datasets discovered and screened", S["SectionHead"]))
    if config.get("catalog_lead"):
        story.append(P(config["catalog_lead"], S["Body"]))
    story.append(catalog_table(config["catalog_csv"], S,
                               config.get("catalog_col_widths")))
    story.append(P(config.get("catalog_caption",
                              "Table 1. Datasets screened during discovery "
                              "(GEO + ArrayExpress/BioStudies). Inclusion required the "
                              "drug arm, longitudinal on-treatment sampling, and a "
                              "recoverable per-patient response metric."), S["Caption"]))
    story.append(PageBreak())

    # ---- Summary + headline callout ----
    story.append(P("Summary", S["SectionHead"]))
    for para in config["summary_paragraphs"]:
        story.append(P(para, S["Body"]))
    if config.get("headline"):
        story.append(P(config["headline"]["head"], S["CalloutHead"]))
        story.append(P(config["headline"]["body"], S["Callout"]))
    # Fig 1 (main heatmap) on the summary page
    f1 = config["figures"].get("heatmap")
    if f1 and os.path.exists(f1["path"]):
        img = Image(f1["path"], width=f1.get("w", 500), height=f1.get("h", 240))
        img.hAlign = "CENTER"
        story.append(KeepTogether([img, P(f1["caption"], S["Caption"])]))
    story.append(PageBreak())

    # ---- Methods ----
    story.append(P("Methods", S["SectionHead"]))
    for sub in config["methods_subsections"]:
        story.append(P(sub["head"], S["SubHead"]))
        for para in sub["paragraphs"]:
            story.append(P(para, S["Body"]))
    # "Which test produced which result" mapping block (always present)
    story.append(P("Which test produced which result", S["SubHead"]))
    for bullet in config["method_mapping"]:
        story.append(P(f"&#8226; {bullet}", S["Body"]))
    story.append(PageBreak())

    # ---- Results ----
    story.append(P("Results", S["SectionHead"]))
    for block in config["results_blocks"]:
        if block.get("head"):
            story.append(P(block["head"], S["SubHead"]))
        for para in block.get("paragraphs", []):
            story.append(P(para, S["Body"]))
        if block.get("table_csv"):
            story.append(generic_table(block["table_csv"], S,
                                       col_widths=block.get("col_widths"),
                                       header_map=block.get("header_map")))
        if block.get("table_caption"):
            story.append(P(block["table_caption"], S["Caption"]))
        for figkey in block.get("figures", []):
            fg = config["figures"].get(figkey)
            if fg and os.path.exists(fg["path"]):
                img = Image(fg["path"], width=fg.get("w", 500), height=fg.get("h", 300))
                img.hAlign = "CENTER"
                story.append(KeepTogether([img, P(fg["caption"], S["Caption"])]))
        if block.get("page_break"):
            story.append(PageBreak())

    # ---- Limitations / Next steps / References ----
    for sec_key, sec_title in [("limitations", "Limitations"),
                               ("next_steps", "Next steps"),
                               ("references", "References")]:
        if config.get(sec_key):
            story.append(P(sec_title, S["SectionHead"]))
            for para in config[sec_key]:
                story.append(P(para, S["Body"]))

    doc = SimpleDocTemplate(out_path, pagesize=letter,
                            leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.7 * inch,
                            title=meta.get("title", "Report"))
    doc.build(story)
    sz = os.path.getsize(out_path) / 1024.0
    sys.stderr.write(f"[report] wrote {out_path} ({sz:.1f} KB)\n"
                     "[report] NEXT: run validate_report.py, then media-check figure "
                     "pages and regenerate on any failure.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="run config JSON")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with open(args.config) as fh:
        config = json.load(fh)
    build(config, args.out)


if __name__ == "__main__":
    main()
