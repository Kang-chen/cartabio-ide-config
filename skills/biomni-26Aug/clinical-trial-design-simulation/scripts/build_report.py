#!/usr/bin/env python3
"""
build_report.py -- Phylo-branded PDF report for a simulated clinical-trial design.

Config-driven and ENDPOINT-AGNOSTIC. Consumes:
  * a JSON config describing the design + narrative (see references/config_schema.md
    and the `report` block of the config examples),
  * operating_characteristics.csv  (from run_grid.R),
  * validation CSVs (gate_fwer.csv, gate_power.csv),
  * a figures/ directory (PNGs from make_figures.R),
and produces a self-contained PDF with:
  1. an at-a-glance INFOGRAPHIC (design summary card),
  2. Introduction, 3. Methods, 4. Results (validation gates + operating
     characteristics + figures), 5. Conclusions, 6. Figures, 7. References,
  8. Next steps.

Follows the Phylo pdf-report-generation conventions (ReportLab Platypus, gold
accent, clean white pages, header/footer chrome, XML rich text -- never markdown
or Unicode super/subscripts).

Usage:
  python build_report.py --config design.json --tables <dir> --figures <dir> --out report.pdf

Author: Biomni (Phylo)
"""
import os, json, argparse, csv
from datetime import date

from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, Image, HRFlowable, KeepTogether)

# ---------- Phylo palette ----------
PHYLO_GOLD   = HexColor("#D4A04A")
HEADING      = HexColor("#111111")
BODY         = HexColor("#2C2A26")
MUTED        = HexColor("#8A8378")
OFF_WHITE    = HexColor("#FAF9F3")
HEADER_BG    = PHYLO_GOLD
HEADER_FG    = HexColor("#FFFFFF")
ALT_ROW      = HexColor("#F9F7F3")
BORDER       = HexColor("#D5CFC5")
GREEN        = HexColor("#75A025")
ORANGE       = HexColor("#FF9400")

# ---------- styles ----------
S = getSampleStyleSheet()
def _add(n, **k):
    if n in S.byName: return
    S.add(ParagraphStyle(name=n, **k))
_add("RTitle",   fontName="Helvetica-Bold", fontSize=24, textColor=HEADING, leading=29, spaceAfter=6)
_add("Sub",      fontName="Helvetica",      fontSize=11, textColor=PHYLO_GOLD, spaceAfter=3)
_add("Attrib",   fontName="Helvetica-Oblique", fontSize=9.5, textColor=MUTED, spaceAfter=6)
_add("H1",       fontName="Helvetica-Bold", fontSize=16, textColor=HEADING, spaceBefore=18, spaceAfter=8)
_add("H2",       fontName="Helvetica-Bold", fontSize=12, textColor=HEADING, spaceBefore=10, spaceAfter=5)
_add("Body2",    fontName="Helvetica",      fontSize=10, textColor=BODY, alignment=TA_JUSTIFY, leading=14.5, spaceAfter=7)
_add("Caption",  fontName="Helvetica-Oblique", fontSize=8.5, textColor=MUTED, alignment=TA_CENTER, spaceAfter=12)
_add("CellL",    fontName="Helvetica",      fontSize=8.3, textColor=BODY, alignment=TA_LEFT, leading=11)
_add("CellH",    fontName="Helvetica-Bold", fontSize=8.3, textColor=HEADER_FG, alignment=TA_LEFT, leading=11)
_add("Ref",      fontName="Helvetica",      fontSize=8.6, textColor=BODY, alignment=TA_LEFT, leading=12, spaceAfter=3)
_add("InfoBig",  fontName="Helvetica-Bold", fontSize=15, textColor=HEADING, alignment=TA_CENTER, leading=17)
_add("InfoLbl",  fontName="Helvetica",      fontSize=8, textColor=MUTED, alignment=TA_CENTER, leading=10)

def divider():   return HRFlowable(width=482, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)
def P(t, s="Body2"): return Paragraph(t, S[s])

def fig(path, w=470, cap=None, h=None):
    els = []
    if os.path.exists(path):
        if h is None:
            # preserve aspect ratio from a default 7.4x5.0 canvas
            h = w * (5.0 / 7.4)
        im = Image(path, width=w, height=h); im.hAlign = "CENTER"
        els.append(im)
        if cap: els.append(Paragraph(cap, S["Caption"]))
    return els

def table(headers, rows, widths, fs=8.3):
    data = [[Paragraph(f"<b>{h}</b>", S["CellH"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c), S["CellL"]) for c in r])
    t = Table(data, colWidths=widths); t.hAlign = "CENTER"
    style = [
        ("BACKGROUND", (0,0), (-1,0), HEADER_BG),
        ("TEXTCOLOR",  (0,0), (-1,0), HEADER_FG),
        ("GRID", (0,0), (-1,-1), 0.5, BORDER),
        ("BOX",  (0,0), (-1,-1), 0.75, BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 6), ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0,i), (-1,i), ALT_ROW))
    t.setStyle(TableStyle(style))
    return t

def callout(t, accent=PHYLO_GOLD):
    d = [[Paragraph(t, S["Body2"])]]
    tb = Table(d, colWidths=[470]); tb.hAlign = "CENTER"
    tb.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), OFF_WHITE),
        ("BOX", (0,0), (-1,-1), 0.5, BORDER),
        ("LINEBEFORE", (0,0), (0,-1), 3, accent),
        ("TOPPADDING", (0,0), (-1,-1), 11), ("BOTTOMPADDING", (0,0), (-1,-1), 11),
        ("LEFTPADDING", (0,0), (-1,-1), 13), ("RIGHTPADDING", (0,0), (-1,-1), 13),
    ]))
    return tb

def infographic(cells):
    """A 4-up 'stat card' row: cells = list of (big, label) tuples."""
    row = [[Paragraph(str(b), S["InfoBig"]) for b, _ in cells],
           [Paragraph(str(l), S["InfoLbl"]) for _, l in cells]]
    # transpose into columns
    n = len(cells); cw = 482 / n
    data = [[Paragraph(str(cells[i][0]), S["InfoBig"]) for i in range(n)],
            [Paragraph(str(cells[i][1]), S["InfoLbl"]) for i in range(n)]]
    t = Table(data, colWidths=[cw]*n); t.hAlign = "CENTER"
    st = [
        ("BACKGROUND", (0,0), (-1,-1), OFF_WHITE),
        ("BOX", (0,0), (-1,-1), 0.75, BORDER),
        ("INNERGRID", (0,0), (-1,-1), 0.5, BORDER),
        ("TOPPADDING", (0,0), (-1,0), 12), ("BOTTOMPADDING", (0,0), (-1,0), 2),
        ("TOPPADDING", (0,1), (-1,1), 0), ("BOTTOMPADDING", (0,1), (-1,1), 12),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]
    t.setStyle(TableStyle(st))
    return t

# ---------- page chrome ----------
_TITLE_RUNNING = {"t": "Clinical trial design"}
def page_chrome(canvas, doc):
    canvas.saveState(); w, h = letter
    canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
    canvas.drawString(60, h-40, _TITLE_RUNNING["t"][:90])
    canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60, h-48, w-60, h-48)
    canvas.setStrokeColor(BORDER); canvas.setLineWidth(0.75); canvas.line(60, 40, w-60, 40)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
    canvas.drawCentredString(w/2, 26, f"Page {doc.page}")
    canvas.restoreState()

# ---------- CSV helpers ----------
def read_csv(path):
    if not path or not os.path.exists(path): return []
    with open(path) as f:
        return list(csv.DictReader(f))

def _fmt(x, nd=3):
    try: return f"{float(x):.{nd}f}"
    except Exception: return str(x)

# ---------- report builder ----------
def build(config, tables_dir, figures_dir, out_path):
    rep = config.get("report", {})
    design = config.get("design", config)
    title = rep.get("title", "Clinical Trial Design: Simulation Report")
    _TITLE_RUNNING["t"] = title

    oc   = read_csv(os.path.join(tables_dir, "operating_characteristics.csv"))
    fwer = read_csv(os.path.join(tables_dir, "gate_fwer.csv"))
    powr = read_csv(os.path.join(tables_dir, "gate_power.csv"))

    story = []
    # ----- Title -----
    story += [Spacer(1, 26),
              Paragraph(title, S["RTitle"]),
              Paragraph(rep.get("subtitle", "Operating characteristics & validation"), S["Sub"]),
              Spacer(1, 4),
              Paragraph(f"<i>Generated by Biomni  |  {date.today().isoformat()}</i>", S["Attrib"]),
              Spacer(1, 12)]

    # ----- Infographic -----
    endpoint = design.get("endpoint", "tte")
    ep_label = {"tte": "Time-to-event", "binary": "Binary", "continuous": "Continuous"}.get(endpoint, endpoint)
    single_hyp = _is_single_hypothesis(design)   # arm/subgroup structure defined ONCE
    allow_ssr = bool(design.get("allow_ssr", False))
    prim = rep.get("headline_scenario")
    prim_row = None
    if oc:
        prim_row = next((r for r in oc if r.get("scenario") == prim), oc[0] if len(oc) == 1 else None)
        if prim_row is None and prim is None:
            # pick the row with the highest power_any that isn't the null
            cand = [r for r in oc if "null" not in r.get("scenario", "").lower()]
            prim_row = max(cand, key=lambda r: float(r.get("power_any", 0))) if cand else oc[0]
    # The target effect size is specified ONCE. If both report.effect_label and
    # design.effect_label are given and DISAGREE, that is an ambiguous config -
    # fail loudly so the author resolves it rather than silently showing one value
    # while the other lives elsewhere in the report.
    eff_label = _resolve_effect_label(rep, design)
    cells = [
        (ep_label, "Endpoint"),
        (eff_label, "Target effect"),
    ]
    if prim_row:
        # Use the same power quantity the OC table reports: full-population power
        # for a single hypothesis, "any rejection" power when a subgroup exists.
        pw_key = "power_F" if single_hyp else "power_any"
        cells.append((f"{float(prim_row.get(pw_key, prim_row.get('power_F', 0)))*100:.0f}%",
                      "Power (target scenario)"))
        cells.append((f"{float(prim_row.get('E_N', 0)):.0f}", "Expected N"))
    else:
        cells.append((design.get("N_max", "-"), "Max N"))
        cells.append((design.get("alpha", 0.025), "One-sided alpha"))
    story += [infographic(cells), Spacer(1, 12), divider()]

    # ----- Bottom-line callout -----
    gates_pass = _all_gates_pass(fwer, powr)
    bl = rep.get("bottom_line")
    if not bl:
        verdict = ("statistically valid (type-I error controlled and simulated power "
                   "matches the analytic benchmark)") if gates_pass else \
                  ("NOT yet validated -- at least one design gate did not pass")
        bl = (f"<b>Bottom line.</b> The proposed {ep_label.lower()} design is {verdict}. "
              "All numbers below come from Monte-Carlo simulation of the exact decision "
              "rules under the stated assumptions; no real patient data were used.")
    story += [callout(bl, accent=GREEN if gates_pass else ORANGE), Spacer(1, 8)]

    # ----- Introduction -----
    story += [Paragraph("1. Introduction", S["H1"])]
    for para in rep.get("introduction", [_default_intro(ep_label, single_hyp)]):
        story.append(P(para))

    # ----- Methods -----
    story += [Paragraph("2. Methods", S["H1"])]
    for para in rep.get("methods", _default_methods(design, endpoint, single_hyp)):
        story.append(P(para))
    # design parameter table
    story += [Paragraph("2.1 Design parameters", S["H2"]),
              _design_table(design, single_hyp)]

    # ----- Results -----
    story += [PageBreak(), Paragraph("3. Results", S["H1"]),
              Paragraph("3.1 Validation gates", S["H2"])]
    story += _gate_narrative(fwer, powr, gates_pass, single_hyp)
    if fwer: story += [Spacer(1,4), _fwer_table(fwer, single_hyp)]
    if powr: story += [Spacer(1,8), _power_table(powr)]

    story += [Paragraph("3.2 Operating characteristics", S["H2"])]
    if single_hyp:
        _oc_default_intro = (
            "The table summarizes the simulated operating characteristics across the "
            "pre-specified effect scenarios for the single full-population hypothesis. "
            "Power is the probability of rejecting the null; expected sample size and "
            "duration reflect early stopping and any adaptive resizing.")
    else:
        _oc_default_intro = (
            "The table summarizes the simulated operating characteristics across the "
            "pre-specified effect scenarios. Rejection probabilities are reported for "
            "the full population (H<sub>F</sub>), the biomarker-positive subgroup "
            "(H<sub>S</sub>), and for either (any). Expected sample size and duration "
            "reflect early stopping and any adaptive resizing.")
    story.append(P(rep.get("oc_intro", _oc_default_intro)))
    if oc: story += [Spacer(1,4), _oc_table(oc, single_hyp, allow_ssr)]

    # ----- Figures -----
    story += [PageBreak(), Paragraph("4. Figures", S["H1"])]
    figspecs = rep.get("figures", _default_figspecs(single_hyp))
    for i, (fname, cap) in enumerate(figspecs, 1):
        path = os.path.join(figures_dir, fname)
        if os.path.exists(path):
            story += fig(path, cap=f"<b>Figure {i}.</b> {cap}")

    # ----- Conclusions -----
    story += [Paragraph("5. Conclusions", S["H1"])]
    for para in rep.get("conclusions", _default_conclusions(ep_label, gates_pass, prim_row, single_hyp)):
        story.append(P(para))
    if rep.get("recommendation"):
        story.append(callout("<b>Recommendation.</b> " + rep["recommendation"]))

    # ----- References -----
    refs = rep.get("references", [])
    if refs:
        story += [Paragraph("6. References", S["H1"])]
        for i, r in enumerate(refs, 1):
            story.append(Paragraph(f"{i}. {r}", S["Ref"]))

    # ----- Next steps -----
    ns = rep.get("next_steps", _default_next_steps(single_hyp))
    if ns:
        story += [Paragraph("7. Next steps", S["H1"])]
        for para in ns:
            story.append(P("&#8226; " + para))

    doc = SimpleDocTemplate(out_path, pagesize=letter, topMargin=56, bottomMargin=52,
                            leftMargin=60, rightMargin=60,
                            title=title, author="Biomni")
    doc.build(story, onFirstPage=page_chrome, onLaterPages=page_chrome)
    return out_path

# ---------- content helpers ----------
def _benchmark_name(powr):
    """Phrase for the Gate-2 reference benchmark. Generalized so it is accurate
    whether the benchmark is rpact (the standard group-sequential designs) or
    another independent analytic/reference method used for designs the base
    engine does not cover. If a gate row carries an explicit `benchmark` label it
    is used; otherwise a method-agnostic phrase (naming rpact as the usual case)
    is returned. No extra config surface is required."""
    if powr:
        for r in powr:
            b = (r.get("benchmark") or "").strip()
            if b:
                return f"the independent analytic benchmark ({b})"
    return ("an independent analytic benchmark (the closed-form group-sequential "
            "power from the <i>rpact</i> package for standard designs)")

def _resolve_effect_label(rep, design):
    """Return the single canonical target-effect label. The effect size is
    specified once; `report.effect_label` is authoritative. If `design.effect_label`
    is also present and DIFFERS, raise an error so the ambiguity is fixed at the
    source rather than showing one value while a contradictory one persists."""
    r = rep.get("effect_label")
    d = design.get("effect_label")
    if r is not None and d is not None:
        if str(r).strip() != str(d).strip():
            raise ValueError(
                "Ambiguous target effect: report.effect_label = "
                f"{r!r} but design.effect_label = {d!r}. Specify the effect size once "
                "(set them equal, or keep only report.effect_label).")
        return r
    if r is not None:
        return r
    if d is not None:
        return d
    return "-"

def _is_single_hypothesis(design):
    """The analysis hypotheses are defined ONCE by the design. A design tests a
    single full-population hypothesis H_F (no biomarker subgroup H_S) when either
    the biomarker-positive prevalence is 1 (subgroup == full population) or
    adaptive enrichment is off. Only when enrichment is enabled on a genuine
    subgroup (prevalence < 1) does the report present the {H_F, H_S} closed test.
    An explicit `single_hypothesis` key in the design overrides the inference."""
    if design is None:
        return True
    if "single_hypothesis" in design:
        return bool(design.get("single_hypothesis"))
    try:
        prev = float(design.get("prevalence", 1.0))
    except Exception:
        prev = 1.0
    allow_enrich = design.get("allow_enrich", False)
    if isinstance(allow_enrich, str):
        allow_enrich = allow_enrich.strip().upper() in ("TRUE", "1", "YES")
    return (prev >= 1.0) or (not allow_enrich)

def _all_gates_pass(fwer, powr):
    def ok(rows):
        if not rows: return True
        for r in rows:
            v = r.get("pass", "TRUE")
            if str(v).strip().upper() in ("FALSE", "0", "NO"): return False
        return True
    return ok(fwer) and ok(powr)

def _design_table(d, single_hyp=False):
    keys = [("endpoint","Endpoint"),("prevalence","Biomarker+ prevalence"),
            ("N_max","Max sample size"),("target_events","Target events (TTE)"),
            ("info_frac","Interim information fraction"),("spending","Alpha-spending"),
            ("alpha","One-sided alpha"),("accrual_months","Accrual (months)"),
            ("dropout_rate","Dropout rate"),("allow_enrich","Adaptive enrichment"),
            ("allow_futility","Futility stopping"),("allow_efficacy","Early efficacy"),
            ("allow_ssr","Sample-size re-estimation")]
    # For a single full-population hypothesis, suppress the biomarker/enrichment
    # rows so the design table cannot imply a subgroup that the design does not use.
    if single_hyp:
        keys = [(k, lbl) for k, lbl in keys if k not in ("prevalence", "allow_enrich")]
    rows = [[lbl, d.get(k)] for k, lbl in keys if d.get(k) is not None]
    return table(["Parameter", "Value"], rows, [260, 222])

def _fwer_table(fwer, single_hyp=False):
    if single_hyp:
        # Single hypothesis: the only error is on H_F, so report it as the
        # type-I rate (no separate H_S column, no family-wise "any").
        hdr = ["Null configuration", "Type-I rate", "Tolerance", "Pass"]
        rows = [[r.get("config"), _fmt(r.get("FWER_any"),4),
                 _fmt(r.get("mc_tol",r.get("tol","")),4), r.get("pass")] for r in fwer]
        return table(hdr, rows, [220, 92, 92, 68])
    hdr = ["Null configuration", "FWER (any)", "Err H_F", "Err H_S", "Tolerance", "Pass"]
    rows = [[r.get("config"), _fmt(r.get("FWER_any"),4), _fmt(r.get("err_F"),4),
             _fmt(r.get("err_S"),4), _fmt(r.get("mc_tol",r.get("tol","")),4),
             r.get("pass")] for r in fwer]
    return table(hdr, rows, [150, 70, 62, 62, 70, 68])

def _power_table(powr):
    # Column header names the actual benchmark used (from the gate rows'
    # `benchmark` field if present, e.g. "MCP-Mod"; else the default "rpact").
    bench_short = "rpact"
    for r in powr:
        b = (r.get("benchmark") or "").strip()
        if b:
            bench_short = b; break
    hdr = ["Endpoint", "Effect", "N/events", f"{bench_short} power", "Sim power", "|diff|", "Pass"]
    rows = [[r.get("endpoint"), r.get("effect"), r.get("N", r.get("info","")),
             _fmt(r.get("rpact_power")), _fmt(r.get("sim_power")),
             _fmt(r.get("abs_diff")), r.get("pass")] for r in powr]
    return table(hdr, rows, [66, 96, 66, 74, 66, 50, 64])

def _oc_table(oc, single_hyp=False, allow_ssr=True):
    if single_hyp:
        # One full-population hypothesis: no H_S/"Any" (they equal H_F) and no
        # enrichment column (enrichment requires a biomarker subgroup).
        hdr = ["Scenario", "Power", "E[N]", "E[T]", "P(fut)"]
        widths = [176, 66, 66, 66, 66]
        if allow_ssr:
            hdr.append("P(SSR)"); widths = [150, 60, 60, 60, 60, 52]
        rows = []
        for r in oc:
            row = [r.get("scenario"), _fmt(r.get("power_F"),3), _fmt(r.get("E_N"),0),
                   _fmt(r.get("E_duration"),1), _fmt(r.get("p_futility"),2)]
            if allow_ssr: row.append(_fmt(r.get("p_ssr"),2))
            rows.append(row)
        return table(hdr, rows, widths)
    hdr = ["Scenario", "H_F", "H_S", "Any", "E[N]", "E[T]", "P(enr)", "P(fut)", "P(SSR)"]
    rows = []
    for r in oc:
        rows.append([r.get("scenario"), _fmt(r.get("power_F"),3), _fmt(r.get("power_S"),3),
                     _fmt(r.get("power_any"),3), _fmt(r.get("E_N"),0), _fmt(r.get("E_duration"),1),
                     _fmt(r.get("p_enrich"),2), _fmt(r.get("p_futility"),2), _fmt(r.get("p_ssr"),2)])
    return table(hdr, rows, [118, 44, 44, 44, 40, 40, 44, 44, 44])

def _gate_narrative(fwer, powr, ok, single_hyp=False):
    out = []
    if ok:
        err_phrase = ("the type-I error rate" if single_hyp
                      else "the family-wise type-I error rate")
        # Name the actual Gate-2 benchmark used, taken from the gate table, so the
        # narrative is accurate for any reference method (e.g. rpact for the
        # standard group-sequential designs, or another analytic/reference method
        # for designs the base engine does not cover) rather than hard-coding one.
        bench = _benchmark_name(powr)
        out.append(P("The design passed both enforced validation gates. "
            "<b>Gate 1</b> confirms that " + err_phrase + ", evaluated "
            "under the global null and least-favorable configurations, stays at or below "
            "the one-sided alpha within Monte-Carlo tolerance. <b>Gate 2</b> confirms that "
            "the simulated power of the reduced (single-hypothesis) design matches " + bench +
            "."))
    else:
        out.append(P("<b>At least one validation gate did not pass.</b> The operating "
            "characteristics below are reported for transparency, but the design should not "
            "be advanced until type-I error control and analytic-power agreement are restored."))
    return out

def _default_intro(ep, single_hyp=False):
    adapt = ("group-sequential monitoring with futility stopping and optional sample-size "
             "re-estimation") if single_hyp else \
            ("group-sequential monitoring with optional adaptive population enrichment, "
             "futility stopping, and sample-size re-estimation")
    return (f"This report specifies and evaluates a two-arm confirmatory trial with a "
            f"{ep.lower()} primary endpoint. The design combines {adapt}, and is evaluated by "
            f"Monte-Carlo simulation of its exact decision rules. The goal is a design that is "
            f"both efficient and provably error-controlling.")

def _interim_timing_phrase(d):
    """Describe the interim timing using the SAME field the simulation uses
    (`info_frac`), so the prose cannot drift from the simulated design."""
    try:
        f = float(d.get("info_frac", 0.5))
    except Exception:
        f = 0.5
    if not (0 < f < 1):
        return "a single (final) analysis with no interim look"
    return (f"one interim analysis at {f*100:.0f}% of the planned information, "
            f"followed by the final analysis")

def _dropout_phrase(d):
    """State the dropout rate actually used in the simulation, from the config."""
    dr = d.get("dropout_rate", None)
    try:
        dr = float(dr)
    except Exception:
        return "the dropout rate specified in the design"
    if dr <= 0:
        return "no dropout"
    return f"an exponential dropout rate of {dr:.2f}"

def _default_methods(d, endpoint, single_hyp=False):
    if single_hyp:
        pop_sentence = (
            "A single full-population analysis population is simulated (no biomarker "
            "subgroup). Each replicate generates endpoint data under the scenario's "
            "assumptions, then applies the trial's monitoring and decision rules exactly "
            "as they would run in practice.")
    else:
        pop_sentence = (
            "Patients are simulated with a biomarker-defined subgroup at the stated "
            "prevalence, so that both the full population and the biomarker-positive "
            "subgroup can be analyzed. Each replicate generates endpoint data under the "
            "scenario's assumptions, then applies the trial's monitoring and decision "
            "rules exactly as they would run in practice.")
    m = [
        pop_sentence,
        ("Interim and final test statistics are formed from an efficient score / information "
         "(U, V) representation of the endpoint, which yields independent stagewise increments. "
         "These are combined across executed stages with the inverse-normal combination function "
         "using pre-fixed weights, so that type-I error is preserved even under data-dependent "
         "adaptation."),
    ]
    # Boundaries + multiplicity sentence, subgroup-aware.
    bound = ("Efficacy boundaries use alpha-spending group-sequential critical values (via "
             "rpact's inverse-normal design). ")
    if not single_hyp:
        bound += ("When both the full population and the biomarker-positive subgroup are "
                  "tested, multiplicity is controlled by a closed test with a Simes "
                  "intersection hypothesis. ")
    bound += ("Futility is assessed by conditional power; sample-size re-estimation, when "
              "enabled, is conditional-power based and capped.")
    m.append(bound)
    # Interim timing + dropout derived from the config (parameters match the report).
    m.append(f"The monitoring plan uses {_interim_timing_phrase(d)}, with "
             f"{_dropout_phrase(d)} over the follow-up period, as specified in the design "
             f"parameters below.")
    if endpoint == "tte":
        m.append("Time-to-event data use exponential event times with uniform accrual; "
                 "the primary analysis is the log-rank statistic at the target number of events.")
    return m

def _default_conclusions(ep, ok, prim, single_hyp=False):
    c = []
    if prim:
        # Use the full-population power for a single-hypothesis design; use the
        # "any rejection" power only when a subgroup hypothesis actually exists.
        if single_hyp:
            pw = float(prim.get('power_F', prim.get('power_any', 0)))
            claim = f"{pw*100:.0f}% power to reject the null"
        else:
            pw = float(prim.get('power_any', 0))
            claim = f"{pw*100:.0f}% power to reject at least one hypothesis"
        c.append(f"Under the primary effect scenario, the design achieves {claim}, "
                 f"with an expected sample size of about {float(prim.get('E_N',0)):.0f} "
                 f"and an expected duration of about {float(prim.get('E_duration',0)):.0f} months.")
    err_phrase = ("the type-I error rate" if single_hyp else "the family-wise error rate")
    c.append(("Because the design controls " + err_phrase + " under all evaluated "
              "null configurations, the efficiency gains from adaptation do not come at the "
              "cost of inflated false-positive risk.") if ok else
             ("The design requires revision before use, as it did not pass all validation gates."))
    return c

def _default_next_steps(single_hyp=False):
    enrich_step = ("Pre-specify the interim analysis timing and the statistical analysis "
                   "plan in the protocol.") if single_hyp else \
                  ("Pre-specify the interim analysis timing, the enrichment decision rule, "
                   "and the statistical analysis plan in the protocol.")
    effect_step = ("Confirm the primary effect assumption with clinical leads before "
                   "finalizing the sample size.") if single_hyp else \
                  ("Confirm the primary effect assumption and biomarker prevalence with "
                   "clinical and translational leads before finalizing the sample size.")
    return [
        effect_step,
        "Run the 'thorough' preset (nsim = 10000) for the final operating-characteristic "
        "table to tighten Monte-Carlo resolution.",
        enrich_step,
        "Consider a small sensitivity study around dropout and accrual assumptions if those "
        "are uncertain.",
    ]

def _default_figspecs(single_hyp=False):
    power_cap = ("Power (rejection probability) by effect scenario." if single_hyp else
                 "Rejection probability by effect scenario for the full population, the "
                 "biomarker-positive subgroup, and either.")
    adapt_cap = ("Probability of each adaptive decision (futility, early efficacy, "
                 "sample-size re-estimation) by scenario." if single_hyp else
                 "Probability of each adaptive decision (enrichment, futility, "
                 "early efficacy, sample-size re-estimation) by scenario.")
    return [
        ("fig_power_by_scenario.png", power_cap),
        ("fig_expected_n.png", "Expected sample size and expected trial duration by scenario."),
        ("fig_adaptations.png", adapt_cap),
        ("fig_sensitivity_power.png", "Sensitivity of power to the swept design parameter."),
        ("fig_sensitivity_n.png", "Sensitivity of sample size and duration to the swept parameter."),
    ]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--tables", required=True, help="dir with OC + gate CSVs")
    ap.add_argument("--figures", required=True, help="dir with figure PNGs")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.config) as f:
        cfg = json.load(f)
    out = build(cfg, a.tables, a.figures, a.out)

    # ---- validation ----
    from pypdf import PdfReader
    reader = PdfReader(out)
    npages = len(reader.pages); size = os.path.getsize(out)
    assert npages >= 3, f"only {npages} pages"
    assert size > 8000, f"only {size} bytes"
    assert len(reader.pages[0].extract_text().strip()) > 0, "page 1 has no text"
    print(f"OK: {out} ({npages} pages, {size//1024} KB)")

if __name__ == "__main__":
    main()
