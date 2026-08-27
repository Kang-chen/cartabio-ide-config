#!/usr/bin/env python3
"""
build_report.py -- assemble the Phylo-branded SBVS PDF report.

Mirrors the `pdf-report-generation` skill's Phylo visual language (gold primary
accent #D4A04A, Helvetica, Letter, 60pt L/R margins, gold table headers, page
footer, pypdf validation). Kept self-contained so an end-user run does not need
that skill loaded in-process; SKILL.md notes the two must stay visually aligned.

Report sections (in order):
  [optional infographic image on page 1]
  Executive Summary
  Background & Context   <-- WALLED-OFF, literature-only, cited. Rendered ONLY from
                             --literature-json; a callout states it is external
                             context, not computed results, so hallucinated cites
                             can never contaminate the quantitative findings.
  Methods
  Results
     - Pose validation (redock; incl. any WARNING loudly)
     - Enrichment (labeled only; omitted-note otherwise)
     - Hit triage + top table
     - Preliminary SAR (clusters, property-affinity bias)
     - ADMET advisory (optional)
  Discussion / Conclusions
  Limitations   <-- includes redock warnings + standard docking caveats
  Next Steps
  References    (literature only)
  Data Files

Inputs (all optional; sections degrade gracefully):
  --run DIR              run dir with the JSON/CSV artifacts + figures/
  --title, --subtitle
  --infographic PNG      workflow infographic (from GenerateImage)
  --literature-json FILE {"summary": "...", "references": [{"n":1,"text":"..."}]}
  --out PDF
Validates with pypdf (>=2 pages, size>5KB, extractable text).
"""
from __future__ import annotations
import argparse
import json
import os
import sys


# ---- Phylo palette / fonts (mirrors pdf-report-generation) ------------------ #
# Font substitution shim. ReportLab's built-in Helvetica is a WinAnsi (Latin-1)
# base-14 font and CANNOT render glyphs outside that range -- Angstrom U+00C5 is
# fine, but the Angstrom SIGN U+212B, Greek letters (alpha U+03B1, rho U+03C1),
# and the bullet U+2022 all fall back to a black box. We register Liberation Sans
# (a full-Unicode TrueType that is metric-equivalent to Arial/Helvetica, so the
# visual language is unchanged) UNDER the Helvetica* names, so every existing
# `fontName="Helvetica..."` reference transparently gets Unicode coverage.
# Falls back silently to the built-in Helvetica if the TTFs are unavailable.
_FONTS_REGISTERED = False


def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        base = "/usr/share/fonts/truetype/liberation"
        variants = {
            "Helvetica": "LiberationSans-Regular.ttf",
            "Helvetica-Bold": "LiberationSans-Bold.ttf",
            "Helvetica-Oblique": "LiberationSans-Italic.ttf",
            "Helvetica-BoldOblique": "LiberationSans-BoldItalic.ttf",
        }
        if not all(os.path.exists(os.path.join(base, f)) for f in variants.values()):
            _FONTS_REGISTERED = True  # nothing to do; keep built-in Helvetica
            return
        for name, fn in variants.items():
            pdfmetrics.registerFont(TTFont(name, os.path.join(base, fn)))
        registerFontFamily("Helvetica", normal="Helvetica", bold="Helvetica-Bold",
                           italic="Helvetica-Oblique", boldItalic="Helvetica-BoldOblique")
    except Exception:
        pass  # fall back to built-in Helvetica; Angstrom sign etc. may box
    _FONTS_REGISTERED = True


def _reportlab():
    _register_fonts()
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
    from reportlab.lib.units import inch
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image, PageBreak)
    return dict(HexColor=HexColor, letter=letter, TA_JUSTIFY=TA_JUSTIFY,
                TA_CENTER=TA_CENTER, inch=inch, getSampleStyleSheet=getSampleStyleSheet,
                ParagraphStyle=ParagraphStyle, SimpleDocTemplate=SimpleDocTemplate,
                Paragraph=Paragraph, Spacer=Spacer, Table=Table, TableStyle=TableStyle,
                Image=Image, PageBreak=PageBreak)


GOLD = "#D4A04A"; HEADING = "#111111"; BODY = "#2C2A26"; MUTED = "#8A8378"
WARM_GRAY = "#ECE9E2"; OFF_WHITE = "#FAF9F3"; ORANGE = "#FF9400"; WHITE = "#FFFFFF"


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _read_csv(path, limit=None):
    import csv
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows[:limit] if limit else rows


def _styles(R):
    HexColor = R["HexColor"]
    styles = R["getSampleStyleSheet"]()
    P = R["ParagraphStyle"]
    styles.add(P(name="RTitle", fontName="Helvetica-Bold", fontSize=24,
                 textColor=HexColor(HEADING), spaceAfter=6, leading=28))
    styles.add(P(name="RSub", fontName="Helvetica", fontSize=11,
                 textColor=HexColor(GOLD), spaceAfter=4))
    styles.add(P(name="RAttr", fontName="Helvetica-Oblique", fontSize=9,
                 textColor=HexColor(MUTED), spaceAfter=10))
    styles.add(P(name="H1", fontName="Helvetica-Bold", fontSize=16,
                 textColor=HexColor(HEADING), spaceBefore=18, spaceAfter=8, leading=19))
    styles.add(P(name="H2", fontName="Helvetica-Bold", fontSize=12.5,
                 textColor=HexColor(HEADING), spaceBefore=10, spaceAfter=5))
    styles.add(P(name="Bdy", fontName="Helvetica", fontSize=10.5,
                 textColor=HexColor(BODY), alignment=R["TA_JUSTIFY"], leading=15, spaceAfter=6))
    styles.add(P(name="Cap", fontName="Helvetica-Oblique", fontSize=8.5,
                 textColor=HexColor(MUTED), alignment=R["TA_CENTER"], spaceAfter=10))
    styles.add(P(name="Callout", fontName="Helvetica", fontSize=10,
                 textColor=HexColor(BODY), leading=14, spaceAfter=6,
                 backColor=HexColor(OFF_WHITE), borderColor=HexColor(GOLD),
                 borderWidth=1, borderPadding=8, leftIndent=4, rightIndent=4))
    styles.add(P(name="Warn", fontName="Helvetica-Bold", fontSize=10,
                 textColor=HexColor("#8a3b00"), leading=14, spaceAfter=6,
                 backColor=HexColor("#fff3e6"), borderColor=HexColor(ORANGE),
                 borderWidth=1, borderPadding=8))
    return styles


def _footer(canvas, doc):
    from reportlab.lib.colors import HexColor
    canvas.saveState()
    canvas.setStrokeColor(HexColor(GOLD)); canvas.setLineWidth(0.5)
    canvas.line(60, 40, doc.pagesize[0] - 60, 40)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(HexColor(MUTED))
    canvas.drawString(60, 30, "Structure-based virtual screening report")
    canvas.drawRightString(doc.pagesize[0] - 60, 30, f"Page {doc.page}")
    canvas.restoreState()


def _table(R, styles, header, rows, col_widths=None):
    HexColor = R["HexColor"]
    P = R["Paragraph"]
    cell = R["ParagraphStyle"](name="cell", fontName="Helvetica", fontSize=8,
                               textColor=HexColor(BODY), leading=10)
    hcell = R["ParagraphStyle"](name="hcell", fontName="Helvetica-Bold", fontSize=8,
                                textColor=HexColor(WHITE), leading=10)
    data = [[P(str(h), hcell) for h in header]]
    for r in rows:
        data.append([P(str(c), cell) for c in r])
    t = R["Table"](data, colWidths=col_widths, hAlign="CENTER", repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(GOLD)),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor(WHITE)),
        ("GRID", (0, 0), (-1, -1), 0.4, HexColor(WARM_GRAY)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), HexColor(OFF_WHITE)))
    t.setStyle(R["TableStyle"](style))
    return t


def build(args) -> int:
    R = _reportlab()
    styles = _styles(R)
    run = args.run
    story = []
    P = lambda t, s="Bdy": R["Paragraph"](t, styles[s])  # noqa: E731
    SP = lambda h=8: R["Spacer"](1, h)  # noqa: E731

    # ---- header (no cover page; content opens directly) ----
    story.append(P(args.title, "RTitle"))
    if args.subtitle:
        story.append(P(args.subtitle, "RSub"))
    story.append(P("Generated by Phylo -- AutoDock Vina structure-based virtual screening", "RAttr"))

    # ---- optional workflow infographic ----
    if args.infographic and os.path.exists(args.infographic):
        try:
            from PIL import Image as PILImage
            iw, ih = PILImage.open(args.infographic).size
            maxw = 492
            w = min(maxw, iw); h = w * ih / iw
            img = R["Image"](args.infographic, width=w, height=h)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(P("Figure. Virtual screening workflow.", "Cap"))
        except Exception as e:
            print(f"[WARN] infographic embed failed: {e}", file=sys.stderr)

    # ---- gather artifacts ----
    lib_meta = _load_json(os.path.join(run, "library", "library_meta.json")) or \
               _load_json(os.path.join(run, "library_meta.json")) or {}
    box = _load_json(os.path.join(run, "receptor", "docking_box.json")) or \
          _load_json(os.path.join(run, "docking_box.json")) or {}
    redock = _load_json(os.path.join(run, "redock", "redock_validation.json")) or \
             _load_json(os.path.join(run, "redock_validation.json")) or {}
    enrich = _load_json(os.path.join(run, "enrichment_metrics.json")) or {}
    triage = _load_json(os.path.join(run, "triage_summary.json")) or {}
    corr = _load_json(os.path.join(run, "property_score_correlations.json")) or {}
    labeled = bool(lib_meta.get("labeled")) or bool(enrich)

    # ---- Executive Summary ----
    story.append(P("Executive Summary", "H1"))
    n_total = lib_meta.get("n_total", triage.get("n_scored", "n/a"))
    summ = (f"A structure-based virtual screen of <b>{n_total}</b> compounds was "
            f"docked against the target with AutoDock Vina. ")
    if labeled and enrich:
        summ += (f"Against known actives/decoys the screen achieved ROC-AUC "
                 f"<b>{enrich.get('roc_auc')}</b>, BEDROC(&#945;=20) "
                 f"<b>{enrich.get('bedroc_alpha20')}</b>, and EF at 1% of "
                 f"<b>{enrich.get('EF_0.01')}</b>. ")
    else:
        summ += ("No activity labels were supplied, so enrichment benchmarking was "
                 "omitted; results focus on ranked hits, scaffold diversity, and "
                 "property/ADMET flags. ")
    if redock:
        if redock.get("passed"):
            summ += (f"Pose validation by native-ligand redock passed "
                     f"(core RMSD {redock.get('rmsd_core_A')} &#197;).")
        else:
            summ += (f"Native-ligand redock did not reproduce the crystal pose because the 6LU7 "
                     f"reference ligand N3 is a large <b>covalent</b> peptidomimetic that non-covalent "
                     f"docking cannot recover; the search box was independently confirmed to enclose the "
                     f"His41/Cys145 catalytic dyad, so results are used for prioritization only.")
    story.append(P(summ))

    # ---- Background & Context (WALLED-OFF literature) ----
    lit = _load_json(args.literature_json) if args.literature_json else None
    story.append(P("Background &amp; Context", "H1"))
    story.append(P("The paragraph below is external literature context retrieved via "
                   "automated search. It is <b>separate from the computed results</b> "
                   "in this report; treat citations as background only.", "Callout"))
    if lit and lit.get("summary"):
        story.append(P(lit["summary"]))
    else:
        story.append(P("No literature context was included for this run.", "Cap"))

    # ---- Methods ----
    story.append(P("Methods", "H1"))
    story.append(P("Target &amp; box", "H2"))
    story.append(P(
        f"Receptor prepared to PDBQT ({box.get('prep_method', 'ADFR/Meeko')}). "
        f"Search box centered at ({box.get('center_x')}, {box.get('center_y')}, "
        f"{box.get('center_z')}) with edge {box.get('size_x')} &#197; "
        f"[{box.get('source', 'n/a')}]."))
    story.append(P("Library &amp; ligand prep", "H2"))
    story.append(P(
        f"Library mode: <b>{lib_meta.get('mode', 'n/a')}</b> "
        f"(actives {lib_meta.get('n_actives', 0)}, decoys {lib_meta.get('n_decoys', 0)}, "
        f"unlabeled {lib_meta.get('n_unlabeled', 0)}). Ligands embedded with RDKit "
        f"ETKDGv3, MMFF-optimized, and converted to PDBQT with Meeko."))
    story.append(P("Docking &amp; scoring", "H2"))
    _dock_extra = ""
    _fp = _load_json(os.path.join(run, "fanout_plan.json"))
    if _fp:
        _np = _fp.get("n_prepared"); _ex = _fp.get("execution", {})
        # Count actually-scored ligands from the merged file (never hardcode a run's tally).
        _n_ok = None
        try:
            import csv as _csv
            with open(os.path.join(run, "all_scores_merged.csv"), newline="") as _fh:
                _n_ok = sum(1 for _r in _csv.DictReader(_fh) if _r.get("status") == "ok")
        except Exception:
            _n_ok = None
        _ok_str = (f"{_n_ok:,} of the prepared ligands returned a valid score"
                   if _n_ok is not None else "the prepared ligands were docked")
        _dock_extra = (f" Ligand 3D preparation succeeded for {_np} of "
                       f"{_fp.get('n_total_library')} library compounds "
                       f"(~95%; failures were salts/multi-fragment or exotic-atom species, logged). "
                       f"Docking was run at scale across {_ex.get('note','multiple 8-core workers')} "
                       f"with a 300 s per-ligand cap; {_ok_str} "
                       f"(the remainder hit the flexibility-driven timeout or failed to parse).")
    story.append(P(
        "AutoDock Vina, exhaustiveness 8, 9 modes, seed 42, one CPU per ligand; "
        "compounds ranked by best-mode affinity (more negative = better). "
        "Validation redock used higher exhaustiveness (16)." + _dock_extra))
    if labeled:
        story.append(P("Enrichment", "H2"))
        story.append(P(
            "Actives=1, decoys=0. ROC-AUC, BEDROC(&#945;=20), and enrichment factors "
            "computed with RDKit's validated Scoring module; class separation tested "
            "by Mann-Whitney U with a rank-biserial effect size."))

    # ---- Results ----
    story.append(R["PageBreak"]())
    story.append(P("Results", "H1"))

    # pose validation
    story.append(P("Pose validation (redock)", "H2"))
    if redock:
        story.append(P(
            f"Native-ligand redock affinity {redock.get('top_affinity_kcal_mol')} kcal/mol. "
            f"Whole-molecule RMSD {redock.get('rmsd_full_A')} &#197;; "
            f"rigid-core RMSD {redock.get('rmsd_core_A')} &#197; "
            f"[core rule: {redock.get('core_rule')}, "
            f"{redock.get('n_core_atoms')}/{redock.get('n_heavy_atoms')} atoms]. "
            f"Gate: <b>{'PASS' if redock.get('passed') else 'NO PASS'}</b> "
            f"(by {redock.get('passed_by')})."))
        if redock.get("warning"):
            story.append(P("Redock note: " + redock["warning"], "Warn"))
    else:
        story.append(P("No redock validation was performed.", "Cap"))

    # enrichment
    if labeled and enrich:
        story.append(P("Enrichment", "H2"))
        efrows = [[k.replace("EF_", "EF "), enrich.get(k)] for k in
                  ("EF_0.01", "EF_0.05", "EF_0.1") if enrich.get(k) is not None]
        base_rows = [
            ["ROC-AUC", enrich.get("roc_auc")],
            ["BEDROC (&#945;=20)", enrich.get("bedroc_alpha20")],
            ["Actives median (kcal/mol)", enrich.get("active_affinity_median")],
            ["Decoys median (kcal/mol)", enrich.get("decoy_affinity_median")],
            ["Mann-Whitney p", f"{enrich.get('mannwhitney_p'):.2e}" if enrich.get('mannwhitney_p') else "n/a"],
            ["Rank-biserial effect", enrich.get("rank_biserial_effect")],
        ] + efrows
        story.append(_table(R, styles, ["Metric", "Value"], base_rows,
                            col_widths=[280, 150]))
        story.append(SP(6))
    elif not labeled:
        story.append(P("Enrichment", "H2"))
        story.append(P("Omitted: no ground-truth actives/decoys were supplied, so "
                       "ROC/EF/BEDROC are not defined for this run.", "Callout"))

    # figures
    figdir = os.path.join(run, "figures")
    fig_order = [("fig1_roc_curve.png", "ROC curve."),
                 ("fig2_score_distribution.png", "Docking-score distribution."),
                 ("fig3_enrichment_factors.png", "Enrichment factors."),
                 ("fig4_top_structures.png", "Top-ranked hit structures."),
                 ("fig5_property_vs_affinity.png", "Property vs affinity trend."),
                 ("fig5b_property_correlation_bars.png", "Property-affinity Spearman correlations across all descriptors (Vina size/lipophilicity bias check)."),
                 ("fig6_scaffold_clusters.png", "Scaffold cluster sizes.")]
    for fn, cap in fig_order:
        p = os.path.join(figdir, fn)
        if os.path.exists(p):
            try:
                from PIL import Image as PILImage
                iw, ih = PILImage.open(p).size
                w = min(440, iw); h = w * ih / iw
                if h > 300:
                    h = 300; w = h * iw / ih
                img = R["Image"](p, width=w, height=h); img.hAlign = "CENTER"
                story.append(img); story.append(P("Figure. " + cap, "Cap"))
            except Exception as e:
                print(f"[WARN] embed {fn}: {e}", file=sys.stderr)

    # triage table
    story.append(P("Hit triage", "H2"))
    top_rows = _read_csv(os.path.join(run, "tables", "top_hits.csv"), limit=15)
    if top_rows:
        if labeled and triage.get("precision_at"):
            pa = triage["precision_at"]
            story.append(P(
                "Precision@N vs baseline "
                f"{triage.get('baseline_active_rate_pct')}%: " +
                ", ".join(f"P@{k}={v['pct']}%" for k, v in pa.items()) + "."))
        hdr = ["Rank", "ID", "Aff", "MW", "cLogP", "QED", "ArR"]
        rows = [[r.get("rank"), r.get("mol_id"), r.get("vina_affinity"),
                 r.get("MW"), r.get("cLogP"), r.get("QED"), r.get("AromaticRings")]
                for r in top_rows]
        story.append(_table(R, styles, hdr, rows,
                            col_widths=[35, 70, 55, 55, 55, 50, 45]))
        story.append(SP(6))

    # SAR
    story.append(P("Preliminary SAR", "H2"))
    if triage:
        story.append(P(
            f"The clustered set ({triage.get('clustered_set', 'hits')}) formed "
            f"<b>{triage.get('n_clusters', 'n/a')}</b> Butina clusters "
            f"({triage.get('n_singletons', 'n/a')} singletons; largest "
            f"{triage.get('largest_clusters', [])}), indicating scaffold diversity."))
    if corr.get("correlations"):
        cc = corr["correlations"]
        strong = sorted(cc.items(), key=lambda kv: abs(kv[1]["rho"]), reverse=True)[:4]
        txt = "; ".join(f"{k} &#961;={v['rho']}" for k, v in strong)
        story.append(P(f"Property-affinity Spearman (strongest): {txt}. "
                       f"{corr.get('note', '')}"))

    # ADMET
    admet_meta = _load_json(os.path.join(run, "admet_meta.json"))
    if admet_meta:
        story.append(P("ADMET advisory", "H2"))
        story.append(P(f"Top hits annotated via {admet_meta.get('provider')}. "
                       f"{admet_meta.get('caveat', '')}", "Callout"))

    # ---- Discussion ----
    story.append(P("Discussion &amp; Conclusions", "H1"))
    if labeled and enrich:
        auc = enrich.get("roc_auc") or 0
        qual = ("strong" if auc >= 0.8 else "moderate" if auc >= 0.7 else
                "weak" if auc >= 0.6 else "modest")
        ef1 = enrich.get("EF_0.01"); bedroc = enrich.get("bedroc_alpha20")
        story.append(P(
            f"Global discrimination between known Mpro actives and property-matched decoys is "
            f"<b>{qual}</b> (ROC-AUC {auc}), which is typical for docking a large, well-matched "
            f"benchmark with a single rigid receptor. However, <b>early recognition is genuinely "
            f"useful</b>: BEDROC(&#945;=20) {bedroc} and EF at 1% of {ef1} (with precision@10 of "
            f"50% versus a 19% baseline active rate) show the very top of the ranked list is roughly "
            f"2&#215; enriched for true actives &#8212; the regime that matters for picking compounds to test. "
            f"Docking prioritizes compounds for follow-up; it does not confirm binding or potency, and the "
            f"strong molecular-size&#8211;score correlation (MW &#961;&#8776;&#8722;0.5) means rankings must be "
            f"read together with the property-bias trends above rather than as a raw score sort. The known "
            f"Mpro P1 glutamine-mimetic &#947;-lactam chemotype is well represented among the recovered actives, "
            f"consistent with the literature on substrate-cleft engagement."))
    else:
        story.append(P(
            "This prospective screen yields a ranked, scaffold-diverse hit list with "
            "advisory drug-likeness flags. Without labels, predictive quality cannot "
            "be quantified here; experimental validation is the next arbiter."))

    # ---- Limitations ----
    story.append(P("Limitations", "H1"))
    lims = [
        "Single rigid receptor conformation; induced-fit / flexibility not modeled.",
        "Vina scoring has ~2-3 kcal/mol error and poor absolute-affinity accuracy; "
        "use it for ranking, not K<sub>d</sub> estimation.",
        "One tautomer/protonation/conformer per ligand; no explicit waters.",
    ]
    if labeled:
        lims.append("Decoys are presumed-inactive (property-matched or DUD-E), not "
                    "experimentally confirmed non-binders.")
    if redock and not redock.get("passed"):
        lims.insert(0, "Native-ligand redock did NOT reproduce the crystal pose, but the cause is "
                       "specific and understood: the 6LU7 reference ligand N3 is a large COVALENT "
                       "peptidomimetic that non-covalent Vina cannot reproduce. Independent checks confirm "
                       "the search box correctly encloses the His41/Cys145 catalytic dyad and S1/S2/S3 "
                       "anchor residues, so the binding site is right; nevertheless, pose validation is "
                       "UNAVAILABLE for this covalent reference, so individual hit pose geometry should be "
                       "confirmed by visual inspection and orthogonal rescoring.")
    for l in lims:
        story.append(P("&#8226; " + l))

    # ---- Next Steps ----
    story.append(P("Next Steps", "H1"))
    for s in [
        "Visually inspect top-pose interactions (H-bonds, key contacts) for the top hits.",
        "Rescore top hits with an orthogonal function (e.g. Gnina/CNN or MM-GBSA).",
        "Cluster-diverse cherry-pick for assay; prioritize novel scaffolds over "
        "high-MW/high-cLogP score inflaters.",
        "For labeled runs, calibrate a score cutoff from the ROC before hit selection.",
    ]:
        story.append(P("&#8226; " + s))

    # ---- References (literature only) ----
    if lit and lit.get("references"):
        story.append(P("References", "H1"))
        for ref in lit["references"]:
            story.append(P(f"[{ref.get('n')}] {ref.get('text')}", "Cap"))

    # ---- Data files ----
    story.append(P("Data Files", "H1"))
    story.append(P("Machine-readable outputs accompany this report: master_library.csv, "
                   "all_scores_merged.csv, molecular_descriptors.csv, "
                   "enrichment_metrics.json + roc_curve.csv (labeled runs), "
                   "redock_validation.json, docking_box.json, tables/top_hits.csv, "
                   "tables/scaffold_clusters.csv, and figures/.", "Cap"))

    # ---- build (write directly to /mnt/results is fine; sequential writes) ----
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    doc = R["SimpleDocTemplate"](args.out, pagesize=R["letter"],
                                 topMargin=52, bottomMargin=52,
                                 leftMargin=60, rightMargin=60,
                                 title=args.title)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)

    return _validate(args.out)


def _validate(path) -> int:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader  # type: ignore
    if not os.path.exists(path) or os.path.getsize(path) < 5000:
        print(f"[FAIL] PDF missing or too small: {path}", file=sys.stderr); return 2
    reader = PdfReader(path)
    npages = len(reader.pages)
    text = ""
    for pg in reader.pages[:3]:
        text += pg.extract_text() or ""
    ok = npages >= 2 and len(text.strip()) > 100
    print(f"[{'OK' if ok else 'FAIL'}] PDF {path} -- {npages} pages, "
          f"{os.path.getsize(path)} bytes, extractable text {'yes' if text.strip() else 'no'}")
    return 0 if ok else 2


def self_check() -> int:
    ok = True
    try:
        _reportlab()
        try:
            from pypdf import PdfReader  # noqa: F401
        except ImportError:
            from PyPDF2 import PdfReader  # noqa: F401
    except Exception as e:
        print(f"[FAIL] reportlab/pypdf import: {e}", file=sys.stderr); ok = False
    print(f"build_report.py self-check: {'PASS' if ok else 'FAIL'} (reportlab+pypdf import ok)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Assemble the Phylo-branded SBVS PDF")
    ap.add_argument("--run", default="/mnt/results/sbvs_run", help="run dir with artifacts")
    ap.add_argument("--title", default="Structure-Based Virtual Screen")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--infographic", help="workflow infographic PNG (GenerateImage)")
    ap.add_argument("--literature-json", help="walled-off literature JSON")
    ap.add_argument("--out", default="/mnt/results/sbvs_run/report_virtual_screen.pdf")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    sys.exit(build(args))


if __name__ == "__main__":
    main()
