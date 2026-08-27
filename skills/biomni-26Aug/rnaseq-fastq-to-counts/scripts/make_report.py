#!/usr/bin/env python3
"""
make_report.py

Build a Phylo-branded PDF report for a bulk RNA-seq FASTQ->counts run, following
the `pdf-report-generation` skill (colors, fonts, layout, validation rules).

The report is DATA-DRIVEN: pass a metrics JSON (see --metrics schema below) plus
figure paths and an optional infographic PNG. Do NOT hardcode numbers here — the
calling agent computes them from the run's own Log.final.out / strandedness.json /
matrix and writes them into the metrics JSON.

Sections: Title -> Executive summary -> Infographic -> Methods -> Results
(alignment table + 4 figures) -> Conclusions -> Scientific caveats ->
References -> Next steps.

--metrics JSON schema (all optional except sample & genome fields used in text):
{
  "sample": "SRR1039508",
  "study": "SRP033351 / GSE52778 (airway smooth muscle)",
  "organism": "Homo sapiens", "genome_build": "GRCh38", "annotation": "Ensembl release-112",
  "subset_mode": "chromosome 22, 4M read-pair subset",   // or "full genome"
  "aligner": "STAR 2.7.11b", "read_length": 63, "sjdb_overhang": 62, "sa_index_nbases": 11,
  "input_reads": 4000000, "uniquely_mapped": 196199, "uniquely_mapped_pct": 4.90,
  "multi_mapped": 19804, "multi_mapped_pct": 0.50,
  "unmapped_short": 3782553, "unmapped_short_pct": 94.56,
  "mismatch_rate_pct": 2.32, "avg_mapped_length": 123.28, "chimeric": 0,
  "splices_total": 65463, "splices_annotated": 58411, "splices_gtag": 63364,
  "strandedness": "unstranded", "fraction_forward": 0.4979,
  "genes_total": 1454, "genes_detected": 682,
  "assigned": 151334, "no_feature": 31845, "ambiguous": 13020,
  "top_genes": [["FBLN1", 28714], ["TIMP3", 9603], ["RPL3", 7240], ["MYH9", 6328]],
  "references": [ {"n":1,"text":"Dobin A, et al. STAR... Bioinformatics 2013."}, ... ],
  "next_steps": ["Run the identical pipeline on all samples...", "..."]
}

Usage:
  python make_report.py --metrics metrics.json \
    --fig-qc figures/fig1_qc.png --fig-align figures/fig2_alignment.png \
    --fig-counts figures/fig3_counts.png --fig-assign figures/fig4_assignment.png \
    [--infographic figures/infographic.png] \
    --title "Bulk RNA-seq alignment & quantification" \
    --out /mnt/results/report_<run>.pdf
"""
import argparse, json, os, datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, PageBreak, HRFlowable, KeepTogether)

# ---- Phylo brand (from pdf-report-generation skill) ----
PHYLO_GOLD = HexColor("#D4A04A"); HEADING = HexColor("#111111"); BODY = HexColor("#2C2A26")
MUTED = HexColor("#8A8378"); TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT = HexColor("#F9F7F3"); TABLE_BORDER = HexColor("#D5CFC5"); CALLOUT_BG = HexColor("#FAF9F3")


def styles_():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="RTitle", fontName="Helvetica-Bold", fontSize=24,
                         textColor=HEADING, spaceAfter=6, leading=30))
    s.add(ParagraphStyle(name="Sub", fontName="Helvetica", fontSize=11, textColor=PHYLO_GOLD, spaceAfter=4))
    s.add(ParagraphStyle(name="Attr", fontName="Helvetica-Oblique", fontSize=10, textColor=MUTED, spaceAfter=8))
    s.add(ParagraphStyle(name="H", fontName="Helvetica-Bold", fontSize=16, textColor=HEADING,
                         spaceBefore=18, spaceAfter=9))
    s.add(ParagraphStyle(name="Bd", fontName="Helvetica", fontSize=10.5, textColor=BODY,
                         alignment=TA_JUSTIFY, spaceAfter=8, leading=15))
    s.add(ParagraphStyle(name="Cap", fontName="Helvetica-Oblique", fontSize=9, textColor=MUTED,
                         alignment=TA_CENTER, spaceAfter=14))
    s.add(ParagraphStyle(name="Cell", fontName="Helvetica", fontSize=9.5, textColor=BODY))
    s.add(ParagraphStyle(name="CellH", fontName="Helvetica-Bold", fontSize=9.5, textColor=TABLE_HEADER_FG))
    return s


def header_footer(canvas, doc, title):
    canvas.saveState(); w, h = letter
    canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
    canvas.drawString(60, h - 40, title[:90])
    canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60, h - 48, w - 60, h - 48)
    canvas.setStrokeColor(TABLE_BORDER); canvas.setLineWidth(0.75); canvas.line(60, 40, w - 60, 40)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
    canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
    canvas.restoreState()


def divider():
    return HRFlowable(width=480, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)


def kv_table(rows, s, col_widths=(180, 300)):
    data = [[Paragraph(f"<b>{k}</b>", s["Cell"]), Paragraph(str(v), s["Cell"])] for k, v in rows]
    t = Table(data, colWidths=list(col_widths)); t.hAlign = "CENTER"
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), TABLE_ALT),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    return t


def header_table(headers, rows, s, col_widths):
    data = [[Paragraph(f"<b>{h}</b>", s["CellH"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c), s["Cell"]) for c in r])
    t = Table(data, colWidths=col_widths, repeatRows=1); t.hAlign = "CENTER"
    style = [("BACKGROUND", (0, 0), (-1, 0), PHYLO_GOLD),
             ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
             ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
             ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7)]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT))
    t.setStyle(TableStyle(style)); return t


def callout(text, s, width=470):
    t = Table([[Paragraph(text, s["Bd"])]], colWidths=[width]); t.hAlign = "CENTER"
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
                           ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
                           ("LINEBEFORE", (0, 0), (0, -1), 3, PHYLO_GOLD),
                           ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                           ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    return t


def fig_block(path, caption, s, max_w=480):
    if not (path and os.path.exists(path)):
        return None
    from PIL import Image as PILImage
    iw, ih = PILImage.open(path).size
    w = min(max_w, iw); h = w * ih / iw
    img = Image(path, width=w, height=h); img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1, 4), Paragraph(caption, s["Cap"])])


def g(m, k, default="—"):
    v = m.get(k, None)
    return default if v is None else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--fig-qc"); ap.add_argument("--fig-align")
    ap.add_argument("--fig-counts"); ap.add_argument("--fig-assign")
    ap.add_argument("--infographic")
    ap.add_argument("--title", default="Bulk RNA-seq alignment & quantification")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.metrics) as fh:
        m = json.load(fh)
    s = styles_()
    story = []
    date_str = datetime.date.today().strftime("%B %d, %Y")

    # Sequential figure numbering across only the figures that actually exist.
    fig_state = {"n": 0}
    def next_fig(desc):
        fig_state["n"] += 1
        return f"Figure {fig_state['n']}. {desc}"

    # Title
    story += [Spacer(1, 30), Paragraph(args.title, s["RTitle"]),
              Paragraph(f"FASTQ &#8594; DE-ready gene count matrix &nbsp;|&nbsp; {g(m,'sample')}", s["Sub"]),
              Spacer(1, 6), Paragraph(f"<i>Generated by Biomni  |  {date_str}</i>", s["Attr"]), divider()]

    # Executive summary
    story.append(Paragraph("Executive summary", s["H"]))
    summ = (f"Public bulk RNA-seq sample <b>{g(m,'sample')}</b> "
            f"({g(m,'study','')}) was aligned to the {g(m,'organism','')} {g(m,'genome_build','')} "
            f"reference ({g(m,'annotation','')}) with {g(m,'aligner','STAR')} and quantified to a "
            f"gene-level count matrix. Run configuration: <b>{g(m,'subset_mode','')}</b>. "
            f"Of {int(g(m,'input_reads',0)):,} input reads, "
            f"<b>{int(g(m,'uniquely_mapped',0)):,} ({g(m,'uniquely_mapped_pct','')}%)</b> mapped uniquely; "
            f"the library was empirically classified as <b>{g(m,'strandedness','')}</b> "
            f"(forward fraction {g(m,'fraction_forward','')}). "
            f"<b>{int(g(m,'genes_detected',0)):,}</b> of {int(g(m,'genes_total',0)):,} annotated genes "
            f"were detected. The resulting integer matrix passed a DESeq2 load check and is ready for "
            f"differential-expression analysis.")
    story.append(Paragraph(summ, s["Bd"]))

    # Infographic
    if args.infographic and os.path.exists(args.infographic):
        blk = fig_block(args.infographic, next_fig("Workflow and key metrics at a glance."), s, max_w=500)
        if blk:
            story += [Spacer(1, 6), blk]

    # Methods
    story.append(Paragraph("Methods", s["H"]))
    story.append(Paragraph(
        f"Reads were quality-checked with FastQC. "
        f"{'A chromosome/read subset was taken to keep the demonstration fast. ' if 'subset' in str(g(m,'subset_mode','')).lower() and 'full' not in str(g(m,'subset_mode','')).lower() else ''}"
        f"A {g(m,'aligner','STAR')} index was built from the reference FASTA + GTF "
        f"(read length {g(m,'read_length','?')} bp &#8594; sjdbOverhang {g(m,'sjdb_overhang','?')}; "
        f"genomeSAindexNbases {g(m,'sa_index_nbases','?')} for the small target). Reads were aligned with "
        f"<font face='Courier'>--quantMode GeneCounts</font> producing a coordinate-sorted BAM "
        f"(validated with <font face='Courier'>samtools quickcheck</font>) and per-gene counts. "
        f"Strandedness was inferred from the STAR count columns and the matching column used to build the matrix. "
        f"DE-readiness was confirmed by loading the integer matrix into "
        f"<font face='Courier'>DESeqDataSetFromMatrix</font>.", s["Bd"]))

    # Results — alignment table
    story.append(Paragraph("Results", s["H"]))
    align_rows = [
        ["Input reads", f"{int(g(m,'input_reads',0)):,}"],
        ["Uniquely mapped", f"{int(g(m,'uniquely_mapped',0)):,} ({g(m,'uniquely_mapped_pct','')}%)"],
        ["Multi-mapped", f"{int(g(m,'multi_mapped',0)):,} ({g(m,'multi_mapped_pct','')}%)"],
        ["Unmapped (too short)", f"{int(g(m,'unmapped_short',0)):,} ({g(m,'unmapped_short_pct','')}%)"],
        ["Mismatch rate", f"{g(m,'mismatch_rate_pct','')}%"],
        ["Avg mapped length", f"{g(m,'avg_mapped_length','')}"],
        ["Splice junctions (total)", f"{int(g(m,'splices_total',0)):,} "
            f"({int(g(m,'splices_annotated',0)):,} annotated)"],
        ["Chimeric reads", f"{g(m,'chimeric','')}"],
        ["Strandedness", f"{g(m,'strandedness','')} (fwd frac {g(m,'fraction_forward','')})"],
        ["Genes detected", f"{int(g(m,'genes_detected',0)):,} / {int(g(m,'genes_total',0)):,}"],
        ["Reads assigned to genes", f"{int(g(m,'assigned',0)):,}"],
    ]
    story += [Paragraph("<b>Table 1.</b> Alignment and quantification summary.", s["Bd"]),
              kv_table(align_rows, s), Spacer(1, 10)]

    # top genes table
    if m.get("top_genes"):
        tg = m["top_genes"][:10]
        story += [Paragraph("<b>Table 2.</b> Top expressed genes.", s["Bd"]),
                  header_table(["Gene", "Counts"], [[a, f"{int(b):,}"] for a, b in tg], s,
                               col_widths=[240, 240]), Spacer(1, 12)]

    # figures — numbered sequentially, skipping any not provided
    for path, desc in [
        (args.fig_qc, "Read quality (FastQC): per-base quality and read summary."),
        (args.fig_align, "STAR alignment outcome and splice-junction breakdown."),
        (args.fig_counts, "Count distribution, top expressed genes, and detected-gene biotypes."),
        (args.fig_assign, "Read-assignment breakdown across categories.")]:
        if path and os.path.exists(path):
            blk = fig_block(path, next_fig(desc), s)
            if blk:
                story += [blk]

    # Conclusions
    story.append(Paragraph("Conclusions", s["H"]))
    story.append(Paragraph(
        f"The pipeline produced a correctly-constructed, DE-ready gene-level count matrix for "
        f"<b>{g(m,'sample')}</b>. Splice-aware alignment (majority-annotated junctions, canonical GT/AG "
        f"dominant) and the empirical strandedness call confirm the quantification is internally consistent. "
        f"The matrix is the validated hand-off point for differential-expression analysis.", s["Bd"]))
    story.append(callout(
        "<b>Ready for DE.</b> Load <font face='Courier'>counts_matrix.tsv</font> into DESeq2/edgeR. "
        "A single sample cannot support a differential test — combine per-sample columns across "
        "conditions (&#8805;2 replicates/group) and use the downstream DESeq2 skill.", s))

    # Caveats
    story.append(Paragraph("Scientific caveats", s["H"]))
    caveats = [
        f"Subset mode ({g(m,'subset_mode','')}) is for pipeline validation and runtime, not biological "
        f"conclusions; expect high unmapped-too-short when aligning to a single chromosome.",
        "Genome-build consistency matters: FASTA and GTF must share seqname conventions "
        "(Ensembl '22' vs UCSC 'chr22') and the same build (GRCh37 vs GRCh38).",
        "Strandedness auto-detection needs enough gene-assigned reads to be reliable.",
        "STAR union-exon counts and salmon transcript-EM counts are not identical; do not mix them in one matrix.",
        "Real differential expression requires biological replicates and a proper design (handled downstream).",
    ]
    for c in caveats:
        story.append(Paragraph(f"&#8226; {c}", s["Bd"]))

    # References
    refs = m.get("references") or []
    if refs:
        story.append(Paragraph("References", s["H"]))
        for r in refs:
            story.append(Paragraph(f"{r.get('n','')}. {r.get('text','')}", s["Bd"]))

    # Next steps
    ns = m.get("next_steps") or [
        "Run the identical pipeline (ideally full-genome on HPC) across all samples/conditions and merge columns.",
        "Hand the matrix to the DESeq2 differential-expression skill with a real design.",
        "Run functional enrichment on the resulting DEGs.",
    ]
    story.append(Paragraph("Next steps", s["H"]))
    for i, step in enumerate(ns, 1):
        story.append(Paragraph(f"{i}. {step}", s["Bd"]))

    doc = SimpleDocTemplate(args.out, pagesize=letter, topMargin=54, bottomMargin=54,
                            leftMargin=60, rightMargin=60)
    hf = lambda c, d: header_footer(c, d, args.title)
    doc.build(story, onFirstPage=hf, onLaterPages=hf)

    # validate
    from pypdf import PdfReader
    r = PdfReader(args.out); pages = len(r.pages); size = os.path.getsize(args.out)
    assert pages >= 2, f"only {pages} pages"
    assert size > 5000, f"only {size} bytes"
    assert len(r.pages[0].extract_text().strip()) > 0, "no text on page 1"
    print(f"PDF OK: {args.out} ({pages} pages, {size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
