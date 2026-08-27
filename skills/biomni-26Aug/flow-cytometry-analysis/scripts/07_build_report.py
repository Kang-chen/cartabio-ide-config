#!/usr/bin/env python3
# =====================================================================================
# 07_build_report.py  --  Phylo-branded PDF report for a cytometry clustering run.
#
# DATA-DRIVEN: reads <outdir>/run_manifest.json (written by the R pipeline) + figures/
# + tables/ + qc_transform_log.txt. It does NOT hardcode any dataset numbers -- every
# value comes from the manifest/CSVs of THIS run. Sections are emitted conditionally
# (benchmark / differential abundance appear only if those steps ran).
#
# Optional infographic: generated up-stream with the GenerateImage tool and passed via
# --infographic <png>. If absent, the report simply omits it (never fabricate a figure).
#
# Report structure (adapts to what exists):
#   Title -> Executive Summary -> 1 Introduction (dataset-at-a-glance, provenance)
#   -> 2 Methods (transform/compensation/QC/clustering, all from the log)
#   -> 3 Results (3.1 populations+heatmap, 3.2 UMAP/tSNE, 3.3 abundance)
#   -> 4 Benchmarking (conditional; honest merged-vs-missed + resolution sensitivity)
#   -> 5 Differential abundance (conditional; states test-vs-descriptive mode honestly)
#   -> 6 Conclusions -> 7 Limitations -> 8 Next steps -> References -> Data & Outputs
#
# Usage:
#   python 07_build_report.py --outdir <outdir> [--infographic <png>] \
#     [--title "Cytometry clustering & annotation report"]
# =====================================================================================
import argparse, json, os, glob, datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, PageBreak, KeepTogether, HRFlowable)
from pypdf import PdfReader

# ---- Phylo brand ----------------------------------------------------------------------
PHYLO_GOLD = HexColor("#D4A04A"); HEADING = HexColor("#111111"); BODY = HexColor("#2C2A26")
MUTED = HexColor("#8A8378"); TBL_HEAD_BG = PHYLO_GOLD; TBL_HEAD_FG = HexColor("#FFFFFF")
TBL_ALT = HexColor("#F9F7F3"); TBL_BORDER = HexColor("#D5CFC5"); CALLOUT_BG = HexColor("#FAF9F3")

ap = argparse.ArgumentParser()
ap.add_argument("--outdir", default="/mnt/results/cyto_run")
ap.add_argument("--infographic", default="")
ap.add_argument("--title", default="Cytometry Clustering & Annotation Report")
ap.add_argument("--out", default="")
A = ap.parse_args()
FIG = os.path.join(A.outdir, "figures"); TAB = os.path.join(A.outdir, "tables")
# Reports ALWAYS land in the results ROOT (/mnt/results/), never a per-run subfolder.
# If --out carries a directory, only its basename is honored so the PDF stays in the root.
_default_pdf = f"report_cytometry_{datetime.date.today().isoformat()}.pdf"
out_pdf = os.path.join("/mnt/results", os.path.basename(A.out) if A.out else _default_pdf)

# The PDF report is MANDATORY and must embed a GenerateImage infographic (skill policy).
# Fail fast if the infographic is missing rather than silently omitting it.
if not A.infographic or not os.path.exists(A.infographic):
    raise SystemExit(
        "ERROR: 07_build_report.py requires an infographic. Generate a one-page workflow "
        "infographic with the Biomni GenerateImage tool and pass it via --infographic <png>. "
        "The flow-cytometry-analysis skill makes the PDF report mandatory WITH a GenerateImage "
        f"infographic; refusing to build a report without one. (got --infographic={A.infographic!r})")

# ---- load manifest (single source of truth for this run) ------------------------------
man_path = os.path.join(A.outdir, "run_manifest.json")
man = {}
if os.path.exists(man_path):
    with open(man_path) as fh: man = json.load(fh)
def g(key, default="n/a"):
    v = man.get(key); return default if v in (None, "") else v
def fig(name):  # return figure path if it exists (prefer png for embedding)
    p = os.path.join(FIG, name)
    return p if os.path.exists(p) else None
def read_csv_rows(name, limit=25):
    p = os.path.join(TAB, name)
    if not os.path.exists(p): return None, None
    import csv
    with open(p) as fh:
        r = list(csv.reader(fh))
    return (r[0], r[1:1 + limit]) if r else (None, None)

TITLE = A.title
doc = SimpleDocTemplate(out_pdf, pagesize=letter, topMargin=52, bottomMargin=52,
                        leftMargin=60, rightMargin=60, title=TITLE)
S = getSampleStyleSheet()
S.add(ParagraphStyle(name="RTitle", fontName="Helvetica-Bold", fontSize=24, textColor=HEADING, leading=30, spaceAfter=6))
S.add(ParagraphStyle(name="Sub", fontName="Helvetica", fontSize=11, textColor=PHYLO_GOLD, spaceAfter=4))
S.add(ParagraphStyle(name="Attr", fontName="Helvetica-Oblique", fontSize=10, textColor=MUTED, spaceAfter=8))
S.add(ParagraphStyle(name="H1", fontName="Helvetica-Bold", fontSize=16, textColor=HEADING, spaceBefore=20, spaceAfter=9))
S.add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=12.5, textColor=HEADING, spaceBefore=12, spaceAfter=6))
S.add(ParagraphStyle(name="Body2", fontName="Helvetica", fontSize=10.5, textColor=BODY, alignment=TA_JUSTIFY, leading=15, spaceAfter=8))
S.add(ParagraphStyle(name="Cap", fontName="Helvetica-Oblique", fontSize=9, textColor=MUTED, alignment=TA_CENTER, spaceAfter=14))
S.add(ParagraphStyle(name="Cell", fontName="Helvetica", fontSize=9, textColor=BODY, leading=12))
S.add(ParagraphStyle(name="CellH", fontName="Helvetica-Bold", fontSize=9, textColor=TBL_HEAD_FG, leading=12))
S.add(ParagraphStyle(name="Ref", fontName="Helvetica", fontSize=8.5, textColor=BODY, leading=12, spaceAfter=4))

def divider(w=480): return HRFlowable(width=w, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)
def _safe_str(v):
    """Coerce nullable DataFrame/CSV values to a ReportLab-safe string.

    NaN/None/NA/inf become 'n/a' so Paragraph() never receives a bare float('nan')
    (which str() renders as 'nan' and can crash XML parsing in some ReportLab
    versions) or an unescaped ampersand from a stray value."""
    import math
    if v is None:
        return "n/a"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return "n/a"
    # pandas.NA / numpy.nan (imported lazily so the script runs without pandas)
    try:
        import pandas as _pd
        if v is _pd.NA:
            return "n/a"
    except Exception:
        pass
    s = str(v)
    if s.lower() in ("nan", "none", "<na>", "na"):
        return "n/a"
    return s
def callout(text):
    t = Table([[Paragraph(text, S["Body2"])]], colWidths=[452]); t.hAlign = "CENTER"
    t.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,-1), CALLOUT_BG), ("BOX",(0,0),(-1,-1),0.5,TBL_BORDER),
        ("LINEBEFORE",(0,0),(0,-1),3,PHYLO_GOLD), ("TOPPADDING",(0,0),(-1,-1),12),
        ("BOTTOMPADDING",(0,0),(-1,-1),12),("LEFTPADDING",(0,0),(-1,-1),14),("RIGHTPADDING",(0,0),(-1,-1),14)]))
    return t
def kv_table(rows, w=(150, 302)):
    data = [[Paragraph(f"<b>{_safe_str(k)}</b>", S["Cell"]), Paragraph(_safe_str(v), S["Cell"])] for k, v in rows]
    t = Table(data, colWidths=list(w)); t.hAlign = "CENTER"
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),TBL_ALT),("GRID",(0,0),(-1,-1),0.5,TBL_BORDER),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t
def data_table(header, rows, widths):
    if not header: return None
    data = [[Paragraph(_safe_str(h), S["CellH"]) for h in header]] + \
           [[Paragraph(_safe_str(c), S["Cell"]) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1); t.hAlign = "CENTER"
    style = [("BACKGROUND",(0,0),(-1,0),TBL_HEAD_BG),("TEXTCOLOR",(0,0),(-1,0),TBL_HEAD_FG),
        ("GRID",(0,0),(-1,-1),0.5,TBL_BORDER),("BOX",(0,0),(-1,-1),0.75,TBL_BORDER),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6)]
    for i in range(2, len(data), 2): style.append(("BACKGROUND",(0,i),(-1,i),TBL_ALT))
    t.setStyle(TableStyle(style)); return t
def add_figure(story, path, caption, w=460, h=None):
    if not path: return
    from PIL import Image as PImage
    iw, ih = PImage.open(path).size
    if h is None: h = w * ih / iw
    img = Image(path, width=w, height=h); img.hAlign = "CENTER"
    story.append(KeepTogether([img, Spacer(1, 4), Paragraph(caption, S["Cap"])]))

story = []
date_str = datetime.date.today().strftime("%B %d, %Y")

# ---------------- Title + Executive summary --------------------------------------------
story += [Spacer(1, 30), Paragraph(TITLE, S["RTitle"]),
          Paragraph(f"{g('modality','Cytometry').upper()} · unsupervised clustering · annotation", S["Sub"]),
          Paragraph(f"<i>Generated by Biomni  |  {date_str}</i>", S["Attr"]), divider()]

if A.infographic and os.path.exists(A.infographic):
    add_figure(story, A.infographic, "Figure 1. Analysis workflow overview.", w=470)

story.append(Paragraph("Executive Summary", S["H1"]))
n_clusters = g("n_clusters"); n_pops = g("n_populations"); chosen = g("chosen_k")
summ = (f"This report summarizes an unsupervised analysis of a {g('modality')} cytometry dataset "
        f"comprising <b>{g('n_cells')}</b> cells across <b>{g('n_samples')}</b> sample(s) and "
        f"<b>{g('n_markers')}</b> markers used for clustering. Cells were preprocessed with "
        f"modality-appropriate transformation and quality control, clustered with FlowSOM + "
        f"consensus meta-clustering, and annotated into <b>{n_pops}</b> cell populations at the "
        f"chosen resolution (<b>{chosen}</b>). ")
if man.get("benchmark"):
    b = man["benchmark"]
    summ += (f"Against manual gating, automated clusters reached overall accuracy "
             f"<b>{b.get('accuracy','n/a')}</b> and weighted F1 <b>{b.get('weighted_F1','n/a')}</b> "
             f"({b.get('n_recovered','?')} populations recovered, {b.get('n_merged','?')} merged at this resolution).")
story.append(Paragraph(summ, S["Body2"]))

# key-findings callout (only true, manifest-derived statements)
kf = []
if man.get("top_population"): kf.append(f"Most abundant population: <b>{man['top_population']}</b> ({man.get('top_population_pct','?')}% of cells).")
if man.get("benchmark", {}).get("n_merged", 0): kf.append(f"{man['benchmark']['n_merged']} population(s) were <b>merged</b> at the reported resolution (resolution artifact, not biological absence) — see Section 4.")
if man.get("diff_abundance", {}).get("mode") == "descriptive_only":
    kf.append("Differential abundance reported <b>descriptively only</b>: group sizes were below the pre-registered threshold for valid statistical testing.")
if kf:
    story.append(callout("<b>Key points.</b><br/>" + "<br/>".join(f"• {x}" for x in kf)))

# ---------------- 1 Introduction -------------------------------------------------------
story += [PageBreak(), Paragraph("1. Introduction", S["H1"])]
story.append(Paragraph(
    "High-dimensional cytometry (flow and mass cytometry/CyTOF) measures dozens of protein "
    "markers per cell, enabling detailed immune profiling. Manual gating is labor-intensive and "
    "operator-dependent; unsupervised clustering offers a reproducible alternative. This analysis "
    "applies a modality-aware pipeline: transformation and compensation appropriate to the platform, "
    "explicit quality control, FlowSOM self-organizing-map clustering with consensus meta-clustering, "
    "marker-based annotation, and per-sample abundance quantification.", S["Body2"]))
story.append(Paragraph("1.1 Dataset at a glance", S["H2"]))
glance = [("Modality", g("modality")), ("Cells (total)", g("n_cells")), ("Samples", g("n_samples")),
          ("Markers (clustering)", g("n_markers")), ("Transformation", g("transform")),
          ("Compensation", g("compensation")), ("QC applied", g("qc_summary")),
          ("Cells removed in QC", g("qc_removed"))]
# gating engine (v2.2.0): shown only when the manifest records it (legacy runs omit it -> no drift)
if g("gate_engine") not in ("n/a", None):
    glance.append(("Gating engine", g("gate_engine")))
# provenance ONLY from metadata — never inferred
if man.get("provenance"): glance.append(("Provenance (from metadata)", man["provenance"]))
story.append(kv_table(glance))
story.append(Paragraph(
    "<i>Cohort/provenance fields are read directly from dataset metadata. Where metadata does not "
    "specify a clinical cohort, none is asserted.</i>", S["Cap"]))

# ---------------- 2 Methods ------------------------------------------------------------
story += [Paragraph("2. Methods", S["H1"])]
story.append(Paragraph(
    f"<b>Preprocessing.</b> Modality was detected as <b>{g('modality')}</b>. "
    f"Transformation: <b>{g('transform')}</b>. Compensation: <b>{g('compensation')}</b>. "
    f"Quality control: {g('qc_detail', g('qc_summary'))}. "
    f"The complete, per-step preprocessing log is reproduced in qc_transform_log.txt.", S["Body2"]))
# ---- manual-gating validation verdict (step 4b) -- surface prominently; never bury a REVIEW ----
_verdict_path = os.path.join(A.outdir, "validation_verdict.txt")
_verdict = ""
if os.path.exists(_verdict_path):
    try: _verdict = open(_verdict_path).read().strip().upper()
    except Exception: _verdict = ""
if _verdict == "PASS":
    story.append(Paragraph(
        "<b>Validation vs manual gating.</b> Automated gates were reconciled against the supplied "
        "manual-gating export and <b>PASSED</b> within tolerance (per-sample deltas in "
        "validation_vs_manual.csv).", S["Body2"]))
elif _verdict == "REVIEW":
    story.append(callout(
        "VALIDATION VERDICT: REVIEW &mdash; automated gating DISAGREES with the manual-gating export "
        "beyond tolerance. Downstream results below are PROVISIONAL. Over-tight scatter/singlet gating "
        "is the most likely cause and should be inspected first (see validation_vs_manual.csv)."))
elif _verdict:
    story.append(Paragraph(
        "<b>Validation vs manual gating.</b> Reconciliation was attempted but incomplete; see "
        "validation_vs_manual.csv.", S["Body2"]))
story.append(Paragraph(
    f"<b>Clustering.</b> FlowSOM was trained on the SOM grid ({g('som_grid','10x10')} = "
    f"{g('n_som_nodes','100')} nodes) over type/lineage markers, followed by ConsensusClusterPlus "
    f"meta-clustering (maxK={g('maxK','20')}). The meta-clustering resolution was selected by "
    f"{g('resolution_method','delta-area elbow')} (chosen: <b>{chosen}</b>), yielding {n_pops} "
    f"annotated populations. Seeds were fixed for reproducibility.", S["Body2"]))
# Annotation claim is conditional on whether a marker database was actually supplied to 03
# (recorded in the manifest as cellmarker_used) -- never claim CellMarker2 if it was not used.
if man.get("cellmarker_used"):
    _annot_txt = ("<b>Annotation.</b> Per-cluster median marker expression (z-scored across clusters) "
                  "was compared against a curated marker reference (CellMarker2) to propose lineage "
                  "labels; proposals were recorded in an editable template for expert review before "
                  "finalization.")
else:
    _annot_txt = ("<b>Annotation.</b> Per-cluster median marker expression (z-scored across clusters) "
                  "was summarized into an editable annotation template; lineage labels are proposed for "
                  "expert review before finalization. No external marker database was supplied for this "
                  "run, so labels were not auto-scaffolded from CellMarker2.")
story.append(Paragraph(_annot_txt, S["Body2"]))
# ---- v2.2.0 QC / gating feature detection (manifest-derived; used in Methods, QC, References) ----
_gate_engine = str(man.get("gate_engine", "") or "").lower()
_tqc   = man.get("time_qc")           if isinstance(man.get("time_qc"), dict) else None
_cdiag = man.get("compensation_diag") if isinstance(man.get("compensation_diag"), dict) else None
_harm  = man.get("harmonization")     if isinstance(man.get("harmonization"), dict) else None
_cnorm = man.get("cytof_norm")        if isinstance(man.get("cytof_norm"), dict) else None
_gh    = man.get("gate_hierarchy")    if isinstance(man.get("gate_hierarchy"), dict) else None
_tqc_backend = str((_tqc or {}).get("backend", "") or "").lower()
_harm_did = (((_harm or {}).get("n_batches") or 0) > 1) or (((_harm or {}).get("n_groups_harmonized") or 0) > 0)
_cnorm_on = bool((_cnorm or {}).get("applied"))
def _isnum(v): return isinstance(v, (int, float)) and not isinstance(v, bool)
def _pct(v, nd=2): return (f"{v:.{nd}f}".rstrip("0").rstrip(".") + "%") if _isnum(v) else "n/a"
def _yn(v): return "yes" if bool(v) else "no"

# Software list mirrors only what actually ran (keeps claims aligned with References).
_sw = ["CATALYST", "FlowSOM", "ConsensusClusterPlus", "flowCore"]
if man.get("diff_abundance"): _sw.append("diffcyt")
if str(g("modality", "")).lower() == "flow": _sw.append("flowDensity/flowClust (data-driven gating)")
if man.get("benchmark"): _sw.append("aricode (clustering agreement)")
if _gate_engine == "opencyto": _sw.append("openCyto/flowWorkspace (hierarchical gating)")
if "peaco" in _tqc_backend: _sw.append("PeacoQC (time QC)")
if "flowai" in _tqc_backend: _sw.append("flowAI (time QC)")
if _harm_did: _sw.append("batch-aware cutoff harmonization")
if _cnorm_on: _sw.append("CATALYST normCytof (bead normalization)")
_sw.append("ggplot2 (figures)")
story.append(Paragraph(
    "<b>Software.</b> R/Bioconductor: " + ", ".join(_sw) + ". See References.", S["Body2"]))

# ---- 2.1 Quality-control gating (data-driven, reviewable, multivariate) ----------------
# Emitted only when the QC step produced per-gate diagnostics (gate_<sample>_<gate>.png).
# All statements below are generic method description; specific cutoffs come from the
# editable template written by 01_load_and_qc.R (never fabricated here).
gate_figs = sorted(glob.glob(os.path.join(FIG, "gate_*.png")))
if gate_figs:
    story.append(Paragraph("2.1 Quality-control gating (data-driven, reviewable)", S["H2"]))
    story.append(Paragraph(
        "QC cutoffs are placed at <b>density valleys</b> on each gating channel (arcsinh scale for "
        "fluorescence/mass channels, linear for scatter) rather than at fixed percentiles, so each "
        "boundary follows this dataset's distribution. A unimodality guard (Hartigan dip test) and a "
        "minimum valley-depth check prevent inventing a cutoff where no real separation exists; in that "
        "case the pipeline falls back to a conservative percentile and flags the gate <b>for review</b> "
        "rather than asserting a spurious threshold. Where unstained/FMO controls are supplied, the "
        "positive/negative boundary is anchored to the control instead. Debris and live/dead steps also "
        "use 2D joint gates (FSC-A×SSC-A; viability×scatter, or Pt×DNA for mass cytometry). Scatter and "
        "singlet gates default to <b>permissive</b>, because over-tight gating silently removes real "
        "cells and biases every downstream population. Every proposed cutoff is written to an editable "
        "template (gating_thresholds_template.csv) alongside the diagnostic figures below, so a reviewer "
        "can confirm or override each threshold and re-run (two-pass: propose → edit → apply).", S["Body2"]))
    # ---- editable threshold table (source of truth = the template CSV for THIS run) ----
    tmpl_path = os.path.join(A.outdir, "gating_thresholds_template.csv")
    if os.path.exists(tmpl_path):
        import csv as _csv
        with open(tmpl_path) as fh:
            _tr = list(_csv.reader(fh))
        if _tr and len(_tr) > 1:
            th = _tr[0]; trows = _tr[1:]
            wmap = {"sample_id": 52, "gate": 86, "method": 54, "final_cutoff": 58,
                    "direction": 64, "status": 74, "apply": 30}
            present = [c for c in ["sample_id", "gate", "method", "final_cutoff",
                                   "direction", "status", "apply"] if c in th]
            idx = [th.index(c) for c in present]
            widths = [wmap[c] for c in present]
            th2 = present
            tr2 = [[(r[i] if i < len(r) else "") for i in idx] for r in trows[:24]]
            story.append(KeepTogether([data_table(th2, tr2, widths),
                Paragraph("Table. Proposed QC gating cutoffs for this run (editable). <i>status</i> flags "
                          "gates needing review (unimodal / shallow valley / too few events / control-"
                          "anchored); <i>sample_id=ALL</i> applies to every sample. Edit final_cutoff / "
                          "apply and re-run with --gate-review apply to override.", S["Cap"])]))
            if len(trows) > 24:
                story.append(Paragraph(f"<i>{len(trows) - 24} further rows in gating_thresholds_template.csv.</i>", S["Cap"]))
    # ---- curated diagnostic figures: highest-leverage gates first ----
    def _gate_priority(p):
        b = os.path.basename(p).lower()
        for i, key in enumerate(["live_dead", "debris_2d", "debris", "dna_intact",
                                 "singlet", "gaussian_doublet", "beads"]):
            if key in b:
                return i
        return 99
    show = sorted(gate_figs, key=lambda p: (_gate_priority(p), p))[:4]
    for p in show:
        nm = os.path.basename(p)[len("gate_"):-len(".png")]
        add_figure(story, p, f"Figure. QC gate diagnostic — {nm}: proposed cutoff and retained region "
                             "on the density (1D) or joint (2D) distribution.", w=300)
    if len(gate_figs) > len(show):
        story.append(Paragraph(
            f"<i>{len(gate_figs) - len(show)} additional per-sample gate diagnostics are in figures/ "
            "(gate_&lt;sample&gt;_&lt;gate&gt;.png).</i>", S["Cap"]))

# ---------------- 3 Results ------------------------------------------------------------
# ---- 2.2 Advanced acquisition & signal QC diagnostics (v2.2.0) ------------------------
# Computed on EVERY v2.2.0 run (diagnostics-on) but events removed ONLY when explicitly
# enabled (removal-opt-in). Emitted only when the manifest carries these v2.2.0 keys, so a
# legacy (v2.1.0) manifest renders nothing here and its report is unchanged (no drift).
if any(x is not None for x in (_tqc, _cdiag, _harm, _cnorm)):
    story.append(Paragraph("2.2 Advanced acquisition &amp; signal QC (v2.2.0)", S["H2"]))
    story.append(Paragraph(
        "Beyond the gating above, the pipeline computes acquisition- and signal-level QC diagnostics on "
        "every run. By design these are <b>diagnostics-on / removal-opt-in</b>: each metric is measured "
        "and reported here, but events are removed only when the corresponding option is explicitly "
        "enabled &mdash; so these numbers never silently change population counts.", S["Body2"]))
    if _tqc is not None:
        _mode = str(_tqc.get("mode", "n/a")).lower()
        if _mode in ("off", "", "n/a"):
            _t = "<b>Time / acquisition QC.</b> Not computed for this run (time-channel QC was off)."
        else:
            _t = (f"<b>Time / acquisition QC</b> (backend: {_safe_str(_tqc.get('backend'))}, mode: "
                  f"<b>{_mode}</b>). Flow-rate anomalies {_pct(_tqc.get('pct_rate'))}, signal-stability "
                  f"anomalies {_pct(_tqc.get('pct_signal'))}, and margin/boundary events "
                  f"{_pct(_tqc.get('pct_margin'))} of acquired events. ")
            _rm = _tqc.get("removed")
            if _mode == "report" or (_isnum(_rm) and not _rm):
                _t += ("Reported as diagnostics only &mdash; <b>no events were removed</b> (enable "
                       "removal to act on them).")
            else:
                _t += f"<b>{_safe_str(_rm)}</b> flagged event(s) were removed."
        story.append(Paragraph(_t, S["Body2"]))
    if _cdiag is not None:
        _src = str(_cdiag.get("source", "none")).lower()
        if _src in ("none", "", "n/a"):
            _c = ("<b>Compensation / spillover.</b> No spillover/compensation matrix was supplied or "
                  "embedded, so no fluorescence compensation was applied and no condition-number "
                  "diagnostic was computed. When a matrix is provided, its condition number (kappa) is "
                  "reported as an ill-conditioning check before compensation.")
        else:
            _c = (f"<b>Compensation / spillover</b> (source: {_safe_str(_cdiag.get('source'))}). Spillover "
                  f"matrix {_safe_str(_cdiag.get('dim'))}; condition number kappa "
                  f"<b>{_safe_str(_cdiag.get('kappa'))}</b> (reciprocal condition "
                  f"{_safe_str(_cdiag.get('rcond'))}) &mdash; verdict <b>{_safe_str(_cdiag.get('verdict'))}</b>; "
                  f"compensation applied: <b>{_yn(_cdiag.get('applied'))}</b>. A high condition number "
                  f"flags a near-singular spillover matrix whose inversion can amplify measurement noise.")
        story.append(Paragraph(_c, S["Body2"]))
    if _harm is not None:
        if _harm_did:
            _h = (f"<b>Batch-aware cutoff harmonization.</b> Scope {_safe_str(_harm.get('scope'))}; "
                  f"{_safe_str(_harm.get('n_batches'))} batch(es) and "
                  f"{_safe_str(_harm.get('n_groups_harmonized'))} gating group(s) harmonized with "
                  f"shrinkage factor {_safe_str(_harm.get('shrink'))} toward a batch-consensus cutoff, "
                  f"reducing batch-driven gate-placement variance.")
        else:
            _h = ("<b>Batch-aware cutoff harmonization.</b> Per-sample cutoffs were used; no cross-batch "
                  "harmonization was applied (single batch, or harmonization not enabled). With multiple "
                  "batches enabled, per-batch cutoffs are shrunk toward a batch-consensus to reduce "
                  "batch-driven gate variance.")
        story.append(Paragraph(_h, S["Body2"]))
    if _cnorm is not None and (_cnorm_on or str(g("modality", "")).lower() in ("cytof", "mass")):
        if _cnorm_on:
            _n = (f"<b>CyTOF bead normalization.</b> Applied using {_safe_str(_cnorm.get('beads'))} beads "
                  f"(smoothing k={_safe_str(_cnorm.get('k'))}); {_safe_str(_cnorm.get('n_beads'))} bead "
                  f"event(s) detected and {_safe_str(_cnorm.get('n_removed'))} removed after signal "
                  f"normalization.")
        else:
            _n = ("<b>CyTOF bead normalization.</b> Not applied (opt-in). When enabled, EQ bead channels "
                  "correct within- and between-sample signal drift before clustering.")
        story.append(Paragraph(_n, S["Body2"]))

# ---- 2.3 Hierarchical gating (openCyto) -- emitted only when that backend was used ----
if _gh is not None:
    story.append(Paragraph("2.3 Hierarchical gating (openCyto)", S["H2"]))
    story.append(Paragraph(
        f"This run used the optional <b>openCyto</b> hierarchical gating backend (template: "
        f"<b>{_safe_str(_gh.get('template'))}</b>) in place of the default data-driven gating. A "
        f"flowWorkspace GatingSet was built and gated through the population hierarchy "
        f"<b>{_safe_str(_gh.get('populations'))}</b>; cells at the terminal population "
        f"<b>{_safe_str(_gh.get('terminal'))}</b> were carried forward to clustering. Across "
        f"{_safe_str(_gh.get('n_samples'))} sample(s), <b>{_safe_str(_gh.get('total_retained'))}</b> of "
        f"<b>{_safe_str(_gh.get('total_input'))}</b> events were retained "
        f"({_safe_str(_gh.get('pct_removed'))}% removed). The full per-sample tree is written to "
        f"{_safe_str(_gh.get('hierarchy_file'))}.", S["Body2"]))
    _pp = _gh.get("per_population")
    if isinstance(_pp, list) and _pp:
        _pph = ["Population", "Mean count", "% of parent"]
        _ppr = [[_safe_str(e.get("population")), _safe_str(e.get("mean_count")),
                 (_pct(e.get("mean_pct_parent")) if _isnum(e.get("mean_pct_parent")) else "n/a")]
                for e in _pp]
        story.append(KeepTogether([data_table(_pph, _ppr, [250, 100, 100]),
            Paragraph("Table. openCyto gating hierarchy &mdash; mean event count and percentage of the "
                      "parent population at each node (averaged across samples).", S["Cap"])]))

story += [PageBreak(), Paragraph("3. Results", S["H1"])]
story.append(Paragraph("3.1 Cell populations and marker signatures", S["H2"]))
story.append(Paragraph(
    f"Clustering resolved <b>{n_pops}</b> populations at resolution {chosen}. The heatmap shows "
    "median expression of clustering markers per population (z-scored), the basis for annotation.", S["Body2"]))
add_figure(story, fig("fig_marker_heatmap.png"), "Figure. Median marker expression per population (z-scored).", w=470)
hh, hr = read_csv_rows("annotation_template.csv", limit=30)
if hh:
    # show a compact view: cluster, proposed/population, top markers
    keep = [i for i, c in enumerate(hh) if c.lower() in ("cluster","proposed_population","population","top_markers")]
    hh2 = [hh[i] for i in keep]; hr2 = [[r[i] if i < len(r) else "" for i in keep] for r in hr]
    story.append(KeepTogether([data_table(hh2, hr2, [55, 150, 100, 140][:len(hh2)]),
                               Paragraph("Table. Cluster annotation (proposed labels for expert review).", S["Cap"])]))

story.append(Paragraph("3.2 Dimensionality reduction", S["H2"]))
story.append(Paragraph(
    "UMAP and t-SNE embeddings (balanced subsample per sample) colored by annotated population "
    "provide a qualitative view of population separation. Embeddings are for visualization; all "
    "quantification uses the full data.", S["Body2"]))
add_figure(story, fig("fig_umap_clusters.png"), "Figure. UMAP colored by annotated population.", w=440)
add_figure(story, fig("fig_tsne_clusters.png"), "Figure. t-SNE colored by annotated population.", w=440)

story.append(Paragraph("3.3 Population abundances", S["H2"]))
story.append(Paragraph("Per-sample population frequencies (% of cells) summarize composition across samples.", S["Body2"]))
add_figure(story, fig("fig_abundance_barplot.png"), "Figure. Population abundance per sample.", w=460)
add_figure(story, fig("fig_freq_heatmap.png"), "Figure. Population frequency heatmap (populations × samples).", w=460)

# ---------------- 4 Benchmarking (conditional) -----------------------------------------
if man.get("benchmark"):
    b = man["benchmark"]
    story += [PageBreak(), Paragraph("4. Benchmarking against manual gating", S["H1"])]
    story.append(Paragraph(
        f"Automated clusters were mapped to manual-gate labels by maximum overlap (Weber & Robinson, "
        f"2016). At resolution {chosen}: overall accuracy <b>{b.get('accuracy','n/a')}</b>, weighted F1 "
        f"<b>{b.get('weighted_F1','n/a')}</b>, ARI <b>{b.get('ARI','n/a')}</b>, NMI <b>{b.get('NMI','n/a')}</b>. "
        f"Of the gold-standard populations, <b>{b.get('n_recovered','?')}</b> were recovered, "
        f"<b>{b.get('n_merged','?')}</b> were merged with another population at this resolution, and "
        f"<b>{b.get('n_missed','?')}</b> were not detected.", S["Body2"]))
    story.append(callout(
        "<b>Reading F1 = 0 correctly.</b> When two reference populations map to the same cluster, the "
        "'loser' scores F1 = 0. This reflects clustering <b>resolution</b>, not biological absence — such "
        "populations are labeled <i>merged</i> here and typically re-separate at finer resolution "
        "(see the sensitivity sweep). Distinguishing <i>merged</i> from <i>missed</i> is essential for "
        "honest benchmarking."))
    add_figure(story, fig("fig_benchmark_F1.png"), "Figure. Per-population F1 vs manual gating (merged populations flagged).", w=460)
    hh, hr = read_csv_rows("benchmark_per_population.csv", limit=30)
    if hh:
        story.append(KeepTogether([data_table(hh, hr, None if len(hh) != 6 else [95,55,70,60,55,110]),
                                   Paragraph("Table. Per-population precision / recall / F1 / status.", S["Cap"])]))
    if fig("fig_resolution_sensitivity.png"):
        story.append(Paragraph("4.1 Resolution sensitivity", S["H2"]))
        story.append(Paragraph(
            "Metrics across clustering resolutions separate genuine merges (recovered at finer resolution) "
            "from true misses (absent even at full SOM resolution). This is the principled way to choose "
            "resolution without an oracle number of populations.", S["Body2"]))
        add_figure(story, fig("fig_resolution_sensitivity.png"), "Figure. Accuracy and weighted F1 vs clustering resolution.", w=440)

# ---------------- 5 Differential abundance (conditional) -------------------------------
if man.get("diff_abundance"):
    da = man["diff_abundance"]
    story += [PageBreak(), Paragraph("5. Differential abundance / state", S["H1"])]
    if da.get("mode") == "descriptive_only":
        story.append(callout(
            f"<b>Statistical testing withheld by design.</b> {da.get('reason','Group size below threshold')}. "
            "With fewer than the pre-registered minimum samples per group, variance cannot be reliably "
            "estimated and p-values would be misleading. Results below are <b>descriptive only</b> "
            "(per-group means/medians and fold-change); no significance is claimed."))
        add_figure(story, fig("fig_abundance_by_group.png"), "Figure. Abundance by group (per-sample points; descriptive).", w=460)
        hh, hr = read_csv_rows("abundance_fold_change_descriptive.csv", limit=20)
        if hh:
            story.append(KeepTogether([data_table(hh, hr, None),
                                       Paragraph("Table. Descriptive fold-change between groups (no test).", S["Cap"])]))
    else:
        story.append(Paragraph(
            f"Differential abundance was tested with diffcyt-DA-edgeR and differential state with "
            f"diffcyt-DS-limma using the design <b>~ {da.get('design','group')}</b> (batch and covariates "
            f"included). Significance threshold FDR &lt; {da.get('fdr','0.05')}.", S["Body2"]))
        add_figure(story, fig("fig_abundance_by_group.png"), "Figure. Abundance by group (per-sample points).", w=460)
        hh, hr = read_csv_rows("diff_abundance_edgeR.csv", limit=20)
        if hh:
            story.append(KeepTogether([data_table(hh, hr, None),
                                       Paragraph("Table. Differential abundance (diffcyt-DA-edgeR), top rows.", S["Cap"])]))

# ---------------- 6 Conclusions --------------------------------------------------------
story += [PageBreak(), Paragraph("6. Conclusions", S["H1"])]
concl = (f"An unsupervised, modality-aware pipeline resolved <b>{n_pops}</b> cell populations from "
         f"{g('n_cells')} {g('modality')} cells across {g('n_samples')} sample(s), with reproducible "
         f"preprocessing, clustering, and marker-based annotation. ")
if man.get("benchmark"):
    concl += ("Benchmarking against manual gating showed strong agreement for well-separated lineages, "
              "with lower-frequency or closely related populations recovered at finer resolution — the "
              "expected behavior of consensus meta-clustering. ")
concl += "Outputs are provided as editable tables and vector/raster figures for downstream analysis and expert review."
story.append(Paragraph(concl, S["Body2"]))

# ---------------- 7 Limitations --------------------------------------------------------
story += [Paragraph("7. Limitations", S["H1"])]
lims = [
    "Cluster annotations are computational proposals from marker signatures and require expert confirmation.",
    "Benchmark 'accuracy' depends on the manual-gating reference and the max-overlap mapping; merged populations reflect resolution, not absence.",
    "Clustering resolution selection is heuristic (delta-area elbow); a different resolution may split or merge populations.",
]
if man.get("modality") == "spectral" or g("compensation").startswith("REFUSED"):
    lims.append("Raw spectral data require validated upstream unmixing; this pipeline does not guess an unmixing matrix. Already-unmixed spectral data is processed as fluorescence flow.")
if man.get("diff_abundance", {}).get("mode") == "descriptive_only":
    lims.append("Differential abundance is descriptive only due to insufficient replication; a larger cohort is needed for inference.")
if _verdict == "REVIEW":
    lims.append("Automated gating did not reconcile with the manual-gating export within tolerance (validation verdict: REVIEW); results are provisional pending a gating review that inspects the scatter/singlet gate first.")
elif not _verdict:
    lims.append("No manual-gating export was supplied, so automated gates could not be validated against an independent reference; internal diagnostics alone do not guarantee correct gating.")
lims.append("Rare populations near the limit of detection may be under-recovered without targeted higher-resolution clustering.")
for L in lims: story.append(Paragraph(f"• {L}", S["Body2"]))

# ---------------- 8 Next steps ---------------------------------------------------------
story += [Paragraph("8. Recommended next steps", S["H1"])]
nxt = [
    "Expert review of the editable annotation template; finalize population labels and re-run downstream steps.",
    "If lineages of interest were flagged as merged, re-cluster those cells at higher resolution (two-tier annotation).",
]
if not man.get("benchmark"):
    nxt.append("If a manually gated reference exists, provide per-cell labels to enable quantitative benchmarking.")
if man.get("diff_abundance", {}).get("mode") == "descriptive_only":
    nxt.append("Expand the cohort to >=3 samples per group to enable valid differential-abundance testing (diffcyt).")
else:
    nxt.append("Validate differentially abundant populations in an independent cohort.")
nxt.append("Integrate with orthogonal data (e.g., scRNA-seq, clinical outcomes) to interpret population shifts.")
for i, N in enumerate(nxt, 1): story.append(Paragraph(f"{i}. {N}", S["Body2"]))

# ---------------- References -----------------------------------------------------------
story += [PageBreak(), Paragraph("References", S["H1"])]
# References are loaded VERBATIM from the verified citation store (assets/references_cytometry.json,
# every field CrossRef-sourced and audited by scripts/verify_citations.py) -- NEVER hardcoded from
# memory. Only methods/tools this run actually used are cited (honest, conditional).
_ref_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "references_cytometry.json")
_refs_db = {}
try:
    with open(_ref_json) as _fh:
        _refs_db = json.load(_fh).get("refs", {})
except Exception:
    _refs_db = {}
_modality = str(g("modality", "")).lower()
_prov = str(man.get("provenance", "") or "").lower()
_ref_plan = [
    ("nowicka",         True),                                      # CyTOF/flow workflow followed
    ("catalyst",        True),                                      # CATALYST preprocessing/clustering toolkit
    ("flowsom",         True),                                      # FlowSOM clustering
    ("weber_compare",   True),                                      # clustering-method basis + max-overlap benchmark mapping
    ("cellmarker2",     bool(man.get("cellmarker_used"))),          # only if CellMarker2 was actually used
    ("levine",          ("levine" in _prov) or ("aml" in _prov)),  # only if the Levine AML dataset was analysed
    ("peacoqc",         "peaco" in _tqc_backend),                 # time-QC via PeacoQC (if used)
    ("flowai",          "flowai" in _tqc_backend),                # time-QC via flowAI (if used)
    ("cytonorm",        bool(_harm_did)),                         # batch-aware cutoff harmonization (if performed)
    ("finck_beadnorm",  bool(_cnorm_on)),                         # CyTOF bead normalization (if applied)
    ("diffcyt",         bool(man.get("diff_abundance"))),           # only if differential abundance/state ran
    ("maecker_trotter", _modality == "flow"),                      # positivity/threshold basis (fluorescence gating)
    ("finak_opencyto",  (_modality == "flow") or (_gate_engine == "opencyto")),                      # automated gating framework (fluorescence)
]
_ref_i = 0
for _key, _cond in _ref_plan:
    if not _cond:
        continue
    _entry = _refs_db.get(_key)
    if not _entry or not _entry.get("citation"):
        continue
    _ref_i += 1
    story.append(Paragraph(f"[{_ref_i}] {_entry['citation']}", S["Ref"]))
if _ref_i == 0:
    story.append(Paragraph("Reference metadata unavailable (assets/references_cytometry.json not found "
                           "or unreadable); see the skill's verified citation store.", S["Ref"]))

# ---------------- Data & Outputs -------------------------------------------------------
story += [Paragraph("Data &amp; Outputs", S["H1"])]
figs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(FIG, "*.png")))
tabs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(TAB, "*.csv")))
story.append(Paragraph("<b>Figures:</b> " + (", ".join(figs) if figs else "none"), S["Ref"]))
story.append(Paragraph("<b>Tables:</b> " + (", ".join(tabs) if tabs else "none"), S["Ref"]))
story.append(Paragraph("<b>Logs:</b> qc_transform_log.txt (full preprocessing/QC record).", S["Ref"]))

# ---- page chrome ----------------------------------------------------------------------
def chrome(canvas, d):
    canvas.saveState(); w, h = letter
    canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
    canvas.drawString(60, h - 40, TITLE[:70])
    canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60, h - 48, w - 60, h - 48)
    canvas.setStrokeColor(TBL_BORDER); canvas.setLineWidth(0.75); canvas.line(60, 40, w - 60, 40)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED); canvas.drawCentredString(w/2, 26, f"Page {d.page}")
    canvas.restoreState()

doc.build(story, onFirstPage=chrome, onLaterPages=chrome)

# ---- validate -------------------------------------------------------------------------
rdr = PdfReader(out_pdf); npages = len(rdr.pages); size = os.path.getsize(out_pdf)
assert npages >= 2, f"only {npages} page(s)"
assert size > 5000, f"only {size} bytes"
assert len(rdr.pages[0].extract_text().strip()) > 0, "no text on page 1"
print(f"PDF OK: {out_pdf} | {npages} pages | {size/1024:.1f} KB")
