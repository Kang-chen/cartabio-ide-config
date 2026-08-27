"""Phylo-branded PDF report for a pharmacovigilance signal-detection run.

Generalizes the validated JAK1 worked-example report into a drug/class/target-
agnostic builder. Follows the ``pdf-report-generation`` skill conventions:

  * ReportLab ``platypus`` with the Phylo brand palette (gold #D4A04A) and the
    Helvetica family (NEVER unicode sub/superscripts -- use ``<sub>``/``<super>``
    tags; U+00B2 and U+03C7 are safe in Helvetica for chi-square).
  * letter pagesize; margins top 58 / bottom 52 / left+right 60; content width
    ~492 pt; all Images/Tables/Drawings ``hAlign="CENTER"``.
  * Tables use explicit ``colWidths`` + ``repeatRows=1``; every figure+caption
    and table+caption is wrapped in ``KeepTogether`` to avoid page splits.

Report sections (auto-populated from the analysis result dict):
  Title -> Executive summary -> [infographic] -> Methods -> Results
  (overview table, top-signals table, figures, cross-drug + unlabeled views)
  -> Limitations -> Conclusions & next steps -> References.

The caller (``run_analysis``) passes a ``ReportContext`` describing the run and
paths to already-generated figures/tables. This module does NOT compute stats or
draw data figures; it only assembles the document. The optional infographic is a
conceptual schematic produced separately via the GenerateImage tool and passed
in as an image path (see :func:`infographic_prompt` for the recommended prompt).
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import pandas as pd
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, Image, HRFlowable,
                                KeepTogether)

# --------------------------------------------------------------------------- #
# brand palette + styles (verbatim from the validated worked example)
# --------------------------------------------------------------------------- #
PHYLO_GOLD = HexColor("#D4A04A")
HEADING_COLOR = HexColor("#111111")
BODY_TEXT = HexColor("#2C2A26")
MUTED_TEXT = HexColor("#8A8378")
TABLE_HEADER_BG = PHYLO_GOLD
TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F9F7F3")
TABLE_BORDER = HexColor("#D5CFC5")
CALLOUT_BG = HexColor("#FAF9F3")
DIVIDER_COLOR = HexColor("#D5CFC5")
CONTENT_W = 492


def _build_styles():
    styles = getSampleStyleSheet()
    def add(name, **kw):
        if name in styles.byName:
            return
        styles.add(ParagraphStyle(name=name, **kw))
    add("ReportTitle", fontName="Helvetica-Bold", fontSize=24,
        textColor=HEADING_COLOR, leading=28, spaceAfter=6)
    add("Subtitle", fontName="Helvetica", fontSize=11.5, textColor=MUTED_TEXT,
        leading=15, spaceAfter=2)
    add("Attribution", fontName="Helvetica-Oblique", fontSize=9.5,
        textColor=MUTED_TEXT, spaceAfter=4)
    add("SectionHead", fontName="Helvetica-Bold", fontSize=16,
        textColor=HEADING_COLOR, leading=19, spaceBefore=6, spaceAfter=8)
    add("SubHead", fontName="Helvetica-Bold", fontSize=12,
        textColor=HEADING_COLOR, leading=15, spaceBefore=8, spaceAfter=4)
    add("Body2", fontName="Helvetica", fontSize=10.3, textColor=BODY_TEXT,
        leading=15, spaceAfter=8, alignment=4)  # justified
    add("Caption", fontName="Helvetica-Oblique", fontSize=8.7,
        textColor=MUTED_TEXT, leading=11, spaceAfter=8, alignment=1)
    add("CalloutTxt", fontName="Helvetica", fontSize=10, textColor=BODY_TEXT,
        leading=14)
    add("THead", fontName="Helvetica-Bold", fontSize=8.8,
        textColor=TABLE_HEADER_FG, leading=11)
    add("TCell", fontName="Helvetica", fontSize=8.6, textColor=BODY_TEXT,
        leading=11)
    add("TCellC", fontName="Helvetica", fontSize=8.6, textColor=BODY_TEXT,
        leading=11, alignment=1)
    add("RefTxt", fontName="Helvetica", fontSize=8.5, textColor=BODY_TEXT,
        leading=11, spaceAfter=3)
    return styles


_STYLES = _build_styles()


# --------------------------------------------------------------------------- #
# flowable helpers (ported verbatim; validated in Phase A)
# --------------------------------------------------------------------------- #
def divider(w: int = CONTENT_W):
    return HRFlowable(width=w, thickness=1, color=DIVIDER_COLOR,
                      spaceAfter=10, spaceBefore=4)


def callout(text: str, title: Optional[str] = None, w: int = CONTENT_W):
    inner = []
    if title:
        inner.append(Paragraph(f"<b>{title}</b>", _STYLES["CalloutTxt"]))
    inner.append(Paragraph(text, _STYLES["CalloutTxt"]))
    it = Table([[i] for i in inner], colWidths=[w - 28])
    it.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    t = Table([[it]], colWidths=[w])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, PHYLO_GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14)]))
    t.hAlign = "CENTER"
    return t


def df_to_table(csv, colw, aligns=None, maxrows=None, caption=None):
    """Render a CSV (or DataFrame) as a Phylo-styled table (+ optional caption).

    Table+caption are wrapped in KeepTogether to avoid page splits.
    """
    df = pd.read_csv(csv) if isinstance(csv, str) else csv.copy()
    if maxrows:
        df = df.head(maxrows)
    heads = [Paragraph(str(c), _STYLES["THead"]) for c in df.columns]
    body = [heads]
    for _, r in df.iterrows():
        row = []
        for j, c in enumerate(df.columns):
            st = _STYLES["TCellC"] if (aligns and aligns[j] == "c") else _STYLES["TCell"]
            row.append(Paragraph(str(r[c]), st))
        body.append(row)
    t = Table(body, colWidths=colw, repeatRows=1)
    sty = [("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
           ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_FG),
           ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
           ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
           ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
           ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for i in range(2, len(body), 2):
        sty.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    t.setStyle(TableStyle(sty))
    t.hAlign = "CENTER"
    if caption is not None:
        return KeepTogether([t, Spacer(1, 4), Paragraph(caption, _STYLES["Caption"])])
    return t


def fig(path, w, h, cap):
    img = Image(path, width=w, height=h)
    img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1, 4), Paragraph(cap, _STYLES["Caption"])])


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(DIVIDER_COLOR)
    canvas.setLineWidth(0.5)
    canvas.line(60, 40, letter[0] - 60, 40)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED_TEXT)
    canvas.drawString(60, 30, "Generated by Biomni")
    canvas.drawRightString(letter[0] - 60, 30, f"Page {doc.page}")
    canvas.restoreState()


# --------------------------------------------------------------------------- #
# report context
# --------------------------------------------------------------------------- #
@dataclass
class ReportContext:
    """Everything the report needs, assembled by the orchestrator.

    Numeric fields drive the executive summary; ``figures``/``tables`` are paths
    to already-generated artifacts; ``references`` is a list of dicts with keys
    like ``title``, ``authors``, ``journal``, ``year`` (from LiteratureSearch).
    """
    subject: str                       # e.g. "JAK1-targeting drugs"
    mode: str                          # explicit | class | target
    drugs: List[str]
    n_drugs_reports: int               # pooled/primary report count
    n_background: int                  # background universe size (N)
    criteria_text: str                 # SignalCriteria.describe()
    n_signals: int                     # rows passing statistical criteria (incl. noise)
    n_events_tested: int
    confidence_text: str = ""          # SignalCriteria.describe_confidence()
    n_noise_signals: int = 0           # signal & is_noise (non-clinical artifacts)
    n_genuine_signals: int = 0         # signal & ~is_noise (headline count)
    comparator_desc: str = "the full FAERS background"
    top_signals: List[tuple] = field(default_factory=list)
    n_labeled_signals: int = 0
    n_unlabeled_signals: int = 0
    n_unknown_signals: int = 0
    n_low_confidence: int = 0          # genuine signals flagged low-confidence
    low_conf_signals: List[tuple] = field(default_factory=list)  # (event, ror, a, reason)
    figures: Dict[str, str] = field(default_factory=dict)
    tables: Dict[str, str] = field(default_factory=dict)
    table_colwidths: Dict[str, list] = field(default_factory=dict)
    table_aligns: Dict[str, list] = field(default_factory=dict)
    references: List[dict] = field(default_factory=list)
    infographic: Optional[str] = None
    extra_conclusions: List[str] = field(default_factory=list)
    dropped_drugs: List[tuple] = field(default_factory=list)


def infographic_prompt(ctx: "ReportContext") -> str:
    """Recommended GenerateImage prompt for the conceptual method schematic.

    A schematic/infographic (boxes, arrows, icons) -- NOT a data plot -- so it
    must be produced with the GenerateImage tool, per visualization policy.
    """
    return (
        "Clean modern scientific infographic, horizontal flow diagram, muted "
        "gold (#D4A04A) and slate colour scheme on off-white, titled "
        f"'Pharmacovigilance Signal Detection: {ctx.subject}'. Four connected "
        "stages left to right with simple line icons and short labels: "
        "1) 'FAERS / OpenFDA reports' (database icon); "
        "2) 'Drug vs background 2x2 table' (grid icon); "
        "3) 'Disproportionality: ROR, PRR, chi-square, FDR' (bar-chart icon); "
        "4) 'Annotated signals vs drug label' (checklist icon). "
        "Minimal, professional, lots of whitespace, thin arrows between stages, "
        "no photorealism, flat vector style, legible sans-serif labels."
    )


_HOW_TO_READ = (
    "These are hypothesis-generating <b>reporting</b> signals, not measures of "
    "risk. Spontaneous reporting systems such as FAERS are subject to "
    "under-reporting, stimulated/notoriety reporting, indication (channeling) "
    "bias, and the absence of a denominator of treated patients. "
    "Disproportionality cannot establish causality or incidence and must be "
    "interpreted alongside controlled epidemiology and the approved label.")


def _exec_summary(story, ctx: "ReportContext"):
    story.append(Paragraph("Executive Summary", _STYLES["SectionHead"]))
    drug_list = ", ".join(f"<b>{d}</b>" for d in ctx.drugs[:8])
    more = "" if len(ctx.drugs) <= 8 else f" and {len(ctx.drugs) - 8} others"
    story.append(Paragraph(
        f"We performed a pharmacovigilance signal-detection analysis on the U.S. "
        f"FDA Adverse Event Reporting System (FAERS), accessed through the "
        f"openFDA API, for {ctx.subject} ({drug_list}{more}). The set was "
        f"analysed against {ctx.comparator_desc} "
        f"({ctx.n_background / 1e6:.1f} million reports with a coded reaction). "
        f"For every drug-event pair we computed the Reporting Odds Ratio (ROR) "
        f"and Proportional Reporting Ratio (PRR) with 95% confidence intervals, "
        f"a Yates-corrected &#967;&#178; test, and Benjamini-Hochberg "
        f"false-discovery-rate control.", _STYLES["Body2"]))
    # unlabeled/unknown breakdown that always sums to n_genuine_signals
    unl = ctx.n_unlabeled_signals
    unk = ctx.n_unknown_signals
    unk_txt = f", {unk:,} with no retrievable label" if unk else ""
    story.append(Paragraph(
        f"Across {ctx.n_events_tested:,} tested reaction terms, "
        f"<b>{ctx.n_signals:,}</b> drug-event pairs met the statistical signal "
        f"criteria. After excluding {ctx.n_noise_signals:,} non-clinical "
        f"reporting artifacts (administrative, product-quality, and procedure "
        f"terms), <b>{ctx.n_genuine_signals:,} genuine adverse-event signals</b> "
        f"remain ({ctx.n_labeled_signals:,} already described in the label, "
        f"{unl:,} not found in the label text{unk_txt}). "
        + _top_signal_sentence(ctx), _STYLES["Body2"]))
    if ctx.n_low_confidence:
        story.append(Paragraph(
            f"Of these, <b>{ctx.n_low_confidence:,}</b> "
            f"{'is' if ctx.n_low_confidence == 1 else 'are'} marked "
            f"<b>low-confidence</b> (fragile case count and/or an implausibly "
            f"extreme ROR consistent with notoriety/stimulated reporting rather "
            f"than a stable safety effect); these are retained and marked, not "
            f"removed. " + _low_conf_sentence(ctx), _STYLES["Body2"]))
    story.append(callout(_HOW_TO_READ, title="How to read this report"))


def _top_signal_sentence(ctx: "ReportContext") -> str:
    if not ctx.top_signals:
        return ""
    ev, ror, lo, hi, cases, status = ctx.top_signals[0][:6]
    low = bool(ctx.top_signals[0][6]) if len(ctx.top_signals[0]) > 6 else False
    tag = " \u2014 flagged low-confidence" if low else ""
    return (f"The strongest signal by ROR is <b>{str(ev).title()}</b> "
            f"(ROR {ror:.1f}, 95% CI {lo:.1f}-{hi:.1f}; {int(cases):,} cases; "
            f"{status}{tag}).")


def _low_conf_sentence(ctx: "ReportContext") -> str:
    if not ctx.low_conf_signals:
        return ""
    names = ", ".join(
        f"{str(ev).title()} (ROR {ror:.1f}, {int(a):,} cases)"
        for ev, ror, a, _ in ctx.low_conf_signals[:4])
    return f"Low-confidence signal(s): {names}."


def _methods(story, ctx: "ReportContext"):
    story.append(Paragraph("Methods", _STYLES["SectionHead"]))
    story.append(Paragraph("Data source and drug definition", _STYLES["SubHead"]))
    mode_txt = {
        "explicit": "The analysed drugs were specified directly.",
        "class": ("Class members were resolved from FDA established-"
                  "pharmacologic-class labels via the openFDA "
                  "<font face='Courier'>/drug/label</font> endpoint."),
        "target": ("Drugs were resolved from the molecular target via the Open "
                   "Targets Platform (target &#8594; known/clinical drugs), then "
                   "validated against FAERS."),
    }.get(ctx.mode, "")
    story.append(Paragraph(
        "Adverse-event reports were retrieved from openFDA "
        "(<font face='Courier'>api.fda.gov/drug/event</font>), which serves "
        "de-duplicated FAERS case reports. Drugs were identified by the "
        "normalized <font face='Courier'>openfda.generic_name</font> field. "
        f"{mode_txt} Only drugs with a non-zero normalized FAERS report count "
        "were retained. Reaction terms use the MedDRA Preferred Term captured "
        "in <font face='Courier'>patient.reaction.reactionmeddrapt</font>.",
        _STYLES["Body2"]))
    if ctx.dropped_drugs:
        dd = "; ".join(f"{n} ({r})" for n, r in ctx.dropped_drugs)
        story.append(Paragraph(
            f"<i>Candidates dropped for insufficient FAERS coverage:</i> {dd}.",
            _STYLES["Body2"]))
    story.append(Paragraph("Disproportionality statistics", _STYLES["SubHead"]))
    story.append(Paragraph(
        "For each drug-event combination a 2&#215;2 contingency table was "
        "formed: <i>a</i> = reports with the drug and the event; <i>b</i> = "
        "drug with other events; <i>c</i> = event with other drugs; <i>d</i> = "
        "all remaining reports. ROR = (<i>a/b</i>)/(<i>c/d</i>) with 95% CI = "
        "exp[ln ROR &#177; 1.96&#8730;(1/<i>a</i>+1/<i>b</i>+1/<i>c</i>+1/<i>d</i>)]. "
        "PRR = [<i>a</i>/(<i>a+b</i>)] / [<i>c</i>/(<i>c+d</i>)]. A Yates-"
        "corrected &#967;&#178; statistic was computed per table, with "
        "Benjamini-Hochberg FDR q-values across terms within each drug. Zero "
        "cells received a 0.5 continuity correction.", _STYLES["Body2"]))
    story.append(Paragraph("Signal criteria", _STYLES["SubHead"]))
    story.append(Paragraph(
        f"A drug-event pair was flagged as a <b>signal</b> when it met: "
        f"{ctx.criteria_text}. Because FAERS contains tens of millions of "
        f"reports, p-values are extremely small for almost any real "
        f"association; effect size (ROR/PRR magnitude) rather than statistical "
        f"significance is the primary basis for prioritization.",
        _STYLES["Body2"]))
    story.append(Paragraph("Annotation", _STYLES["SubHead"]))
    story.append(Paragraph(
        "Flagged terms were annotated on three axes: (1) whether the term is "
        "already described in the FDA label (boxed warning or adverse-reactions "
        "section, via the openFDA <font face='Courier'>/drug/label</font> "
        "endpoint); (2) an approximate MedDRA System Organ Class from a curated "
        "keyword mapping (a heuristic, not the licensed MedDRA hierarchy); and "
        "(3) exclusion of non-clinical administrative / product-quality / "
        "indication terms that are not adverse drug reactions.", _STYLES["Body2"]))


def _results(story, ctx: "ReportContext"):
    story.append(PageBreak())
    story.append(Paragraph("Results", _STYLES["SectionHead"]))
    _t2cap = ("Table 2. Strongest genuine disproportionality signals for the "
              "primary subject (non-clinical artifacts excluded). The "
              "&#8220;Conf.&#8221; column marks rows flagged low-confidence "
              "(&#8220;low&#8221;) versus robust (&#10003;); low-confidence "
              "rows are retained here, not removed.")
    for key, cap in (("overview", "Table 1. Analysis overview (all signal "
                      "counts derive from a single source of truth: signals "
                      "passing criteria = genuine + artifacts; genuine = "
                      "labeled + unlabeled + unknown)."),
                     ("top_signals", _t2cap)):
        if key in ctx.tables and os.path.exists(ctx.tables[key]):
            cw = ctx.table_colwidths.get(key)
            al = ctx.table_aligns.get(key)
            story.append(df_to_table(ctx.tables[key], cw, aligns=al, caption=cap))
            story.append(Spacer(1, 8))

    figspec = [
        ("bar", "Figure 1. Ranked disproportionality signals (ROR with 95% CI); "
         "colour = label status.", 8.2),
        ("volcano", "Figure 2. Volcano plot of log2 ROR versus -log10 BH-FDR "
         "q-value; the strongest signals are numbered with a side key.", 7.4),
        ("forest", "Figure 3. Forest plot of top signals (ROR, log scale, "
         "95% CI).", 7.0),
        ("heatmap", "Figure 4. Cross-drug reporting pattern (ROR per drug); "
         "* marks under-reported (ROR<1) cells.", 7.4),
        ("summary", "Figure 5. Signal summary by System Organ Class and label "
         "status.", 8.2),
    ]
    from reportlab.lib.utils import ImageReader
    # usable frame height (letter 792 - top 58 - bottom 52) minus room for the
    # caption + spacer, so a tall figure never overflows the frame.
    MAX_FIG_H = 792 - 58 - 52 - 40
    for name, cap, w_in in figspec:
        p = ctx.figures.get(name)
        if p and os.path.exists(p):
            iw, ih = ImageReader(p).getSize()
            w = w_in * 72
            h = w * ih / iw
            if h > MAX_FIG_H:                 # scale down by height, keep aspect
                h = MAX_FIG_H
                w = h * iw / ih
            story.append(fig(p, w, h, cap))
            story.append(Spacer(1, 8))

    if "unlabeled" in ctx.tables and os.path.exists(ctx.tables["unlabeled"]):
        story.append(df_to_table(
            ctx.tables["unlabeled"], ctx.table_colwidths.get("unlabeled"),
            aligns=ctx.table_aligns.get("unlabeled"),
            caption="Table 3. Potentially unlabeled signals (not found in the "
                    "current label) -- hypothesis-generating shortlist."))


def _limitations(story, ctx: "ReportContext"):
    story.append(Paragraph("Limitations", _STYLES["SectionHead"]))
    story.append(Paragraph(
        "This analysis inherits the well-known limitations of spontaneous-report "
        "disproportionality. (1) <b>No causality</b>: a signal reflects "
        "differential reporting, not risk. (2) <b>No denominator</b>: FAERS has "
        "no count of treated patients, so incidence cannot be estimated. "
        "(3) <b>Reporting biases</b>: under-reporting, notoriety/stimulated "
        "reporting after label changes or litigation, and channeling by "
        "indication all distort counts; notably, established risks can appear "
        "<i>under</i>-reported (ROR&lt;1) for individual drugs when a class "
        "warning shifts attention. (4) <b>Duplicate and confounded reports</b> "
        "and co-medication (masking) affect the 2&#215;2 counts. (5) The SOC "
        "mapping and label matching here are automated heuristics, not the "
        "licensed MedDRA hierarchy or a manual label review.", _STYLES["Body2"]))
    story.append(Paragraph(
        "(6) <b>Extreme ROR and case-count fragility</b>: an implausibly large "
        "ROR is frequently an artifact of notoriety/stimulated reporting or of "
        "a mechanism/efficacy-adjacent term rather than a stable safety effect, "
        "and small case counts give unstable estimates. Such rows are marked "
        f"low-confidence ({ctx.confidence_text or 'small count or extreme-ROR outlier'}) "
        "and should be interpreted with particular caution. Because the openFDA "
        "reaction facet returns only the most frequently reported terms, very "
        "small counts (and the case-count trigger) mainly surface for rarely "
        "reported drugs; for high-volume drugs the dominant low-confidence "
        "trigger is an extreme-ROR outlier.", _STYLES["Body2"]))


def _conclusions(story, ctx: "ReportContext"):
    story.append(Paragraph("Conclusions & Next Steps", _STYLES["SectionHead"]))
    base = [
        (f"The pipeline recovered the expected safety profile for {ctx.subject} "
         f"({ctx.n_labeled_signals:,} labeled-ADR signals), providing internal "
         f"validation of the method."),
        ("Potentially unlabeled signals should be triaged by clinical "
         "plausibility, case-level review, and comparison with controlled "
         "epidemiology before any interpretation."),
        ("Recommended follow-up: (a) sensitivity analysis with a matched-"
         "indication comparator rather than the whole database; (b) time-trend "
         "/ disproportionality-over-time to detect notoriety effects; (c) "
         "case-level narrative review of the top unlabeled terms; (d) "
         "cross-check against published pharmacovigilance and trial evidence."),
    ]
    for c in base + ctx.extra_conclusions:
        story.append(Paragraph(f"&#8226; {c}", _STYLES["Body2"]))


def _references(story, ctx: "ReportContext"):
    if not ctx.references:
        return
    story.append(Paragraph("References", _STYLES["SectionHead"]))
    for i, r in enumerate(ctx.references, 1):
        authors = r.get("authors", "")
        if isinstance(authors, list):
            authors = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        parts = [p for p in [authors, r.get("title", ""),
                             r.get("journal", ""), str(r.get("year", ""))] if p]
        story.append(Paragraph(f"[{i}] " + ". ".join(parts) + ".",
                               _STYLES["RefTxt"]))


def build_report(ctx: "ReportContext", out_path: str,
                 workspace_dir: str = "/workspace") -> str:
    """Assemble the full PDF and return the final output path.

    ReportLab cannot write random-access PDFs directly onto S3-backed mounts, so
    the document is built in ``workspace_dir`` first and then copied (via shell
    ``cp``) to ``out_path`` when ``out_path`` is on /mnt.
    """
    date_str = _dt.date.today().strftime("%B %d, %Y")
    tmp = out_path
    on_s3 = out_path.startswith("/mnt/")
    if on_s3:
        os.makedirs(workspace_dir, exist_ok=True)
        tmp = os.path.join(workspace_dir, os.path.basename(out_path))

    story = []
    story.append(Spacer(1, 28))
    story.append(Paragraph(
        f"Adverse-Event Signal Detection: {ctx.subject}", _STYLES["ReportTitle"]))
    story.append(Paragraph(
        "FAERS / OpenFDA Disproportionality Analysis "
        "(Reporting Odds Ratio &amp; Proportional Reporting Ratio)",
        _STYLES["Subtitle"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"<i>Generated by Biomni  |  {date_str}</i>",
                           _STYLES["Attribution"]))
    story.append(divider())

    _exec_summary(story, ctx)

    if ctx.infographic and os.path.exists(ctx.infographic):
        from reportlab.lib.utils import ImageReader
        iw, ih = ImageReader(ctx.infographic).getSize()
        w = 6.6 * 72
        h = w * ih / iw
        max_h = 792 - 58 - 52 - 60      # leave room for caption + summary above
        if h > max_h:
            h = max_h
            w = h * iw / ih
        story.append(Spacer(1, 6))
        story.append(fig(ctx.infographic, w, h,
                         "Figure. Analysis workflow overview."))

    story.append(PageBreak())
    _methods(story, ctx)
    _results(story, ctx)
    story.append(PageBreak())
    _limitations(story, ctx)
    _conclusions(story, ctx)
    _references(story, ctx)

    doc = SimpleDocTemplate(tmp, pagesize=letter, topMargin=58,
                            bottomMargin=52, leftMargin=60, rightMargin=60,
                            title=f"AE Signal Detection: {ctx.subject}")
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)

    if on_s3:
        import subprocess
        subprocess.run(["cp", tmp, out_path], check=True)
    return out_path


def validate_pdf(path: str, min_pages: int = 2, min_bytes: int = 5000) -> dict:
    """Structural validation: page count, size, and text extractability."""
    out = {"ok": False, "path": path, "pages": 0, "bytes": 0,
           "has_text": False, "issues": []}
    if not os.path.exists(path):
        out["issues"].append("file missing")
        return out
    out["bytes"] = os.path.getsize(path)
    if out["bytes"] < min_bytes:
        out["issues"].append(f"file too small ({out['bytes']} bytes)")
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        out["pages"] = len(reader.pages)
        text = "".join((reader.pages[i].extract_text() or "")
                       for i in range(min(3, out["pages"])))
        out["has_text"] = len(text.strip()) > 50
        if out["pages"] < min_pages:
            out["issues"].append(f"only {out['pages']} page(s)")
        if not out["has_text"]:
            out["issues"].append("no extractable text")
    except Exception as e:
        out["issues"].append(f"pypdf error: {e}")
    out["ok"] = not out["issues"]
    return out
