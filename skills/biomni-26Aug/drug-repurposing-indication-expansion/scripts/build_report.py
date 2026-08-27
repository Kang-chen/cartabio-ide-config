"""Config-driven PDF report for drug-repurposing / indication expansion.

This is the disease-AGNOSTIC generalization of the original hard-coded IPF report. The
report STRUCTURE, METHODS prose, figures, tables and styling are fixed and driven by
the analysis outputs; all DISEASE-SPECIFIC narrative (executive summary, disease biology,
per-candidate interpretation, discussion, limitations emphasis, references) is supplied by
the agent in a `report_config` dict -- grounded in LiteratureSearch, never invented.

PDF layout, brand constants and figure-style conventions are owned by the
`pdf-report-generation` skill; `assets/report_style.py` LOADS the palette and typography
from that skill at runtime (no brand values are declared in this package), and this builder
only supplies the scientific content and the data-driven gates. Do not re-derive brand rules
here.

Design contract
---------------
The agent assembles `report_config` (see references/METHODS.md for the schema and
worked_example_ipf.md for a filled example) and the `stats` dict, provides the analysis
tables + figure/infographic paths, then calls `build(...)`. Nothing about the disease is
hard-coded here.

`report_config` keys (all strings unless noted; HTML inline markup allowed):
  title, subtitle                      -- report title / method subtitle
  disease_label                        -- short disease name for running header
  executive_summary : list[str]        -- 1-3 paragraphs (numbers via {stats} already filled by agent)
  key_finding_title, key_finding_body  -- the opening callout (finding + honest caveat)
  top_hit_rationale (REQUIRED)         -- rationale for the canonical #1-ranked hit; build()
                                          raises ValueError if missing/empty. An honest
                                          "likely non-specific / assay artifact" call is valid.
  top_hit_title (optional)             -- heading for that callout (default "Top-ranked candidate")
  controls_failure_acknowledgement     -- REQUIRED when the controls verdict is 'fail' or 'weak';
                                          build() raises ValueError if missing/empty. The verdict
                                          is recomputed inside build() from tables['controls'].
  compound_flags (optional)            -- SINGLE SOURCE OF TRUTH for compound mechanistic
                                          credibility: list[{name, classification, note}] with
                                          classification in {artifact, caution, credible}. Read by
                                          the page-1 infographic caption, the body flag table, and
                                          the front-matter consistency gate. A compound flagged
                                          here (artifact/caution) cannot appear unflagged on page 1.
  introduction : list[str]             -- disease biology + repurposing rationale paragraphs
  results_intro, results_top, results_moa, results_controls : list[str]  -- per-subsection prose
  discussion : list[str]               -- interpretation paragraphs
  limitations : list[(title, body)]    -- bullet limitations
  conclusions : list[str]              -- conclusion paragraphs
  bottom_line_body                     -- closing callout body
  references : list[str]               -- numbered reference strings; each MUST carry a PMID/DOI/URL
                                          locator or build() raises ValueError. Use
                                          literature_evidence.references_from_records() to build them.
  marker_note (optional)               -- caption addendum for the signature figure
  infographic_caption (optional)       -- PREFIX only for the infographic caption; the factual
                                          sentence is derived by build_report from the approved
                                          DataFrame via make_infographic.infographic_caption_from_data.

Public API:
  build(report_config, stats, tables, figures, out_pdf) -> out_pdf path
"""
import os
import re
from datetime import date

import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, Image, KeepTogether, ListFlowable, ListItem)

import report_style as rs


def _fig(path, w, cap, styles):
    from PIL import Image as PILImage
    iw, ih = PILImage.open(path).size
    h = w * ih / iw
    im = Image(path, width=w, height=h)
    im.hAlign = "CENTER"
    return KeepTogether([im, Spacer(1, 4), Paragraph(cap, styles["Caption"])])


def _para_list(paragraphs, style, styles):
    return [Paragraph(p, styles[style]) for p in (paragraphs or [])]


def _section(head, paragraphs, styles, style="Body", head_style="SectionHead"):
    """Return flowables for a section, keeping the heading with its first paragraph
    so a heading never orphans at the bottom of a page. Remaining paragraphs flow
    normally (they may break across pages)."""
    paras = _para_list(paragraphs, style, styles)
    head_flow = Paragraph(head, styles[head_style])
    if paras:
        out = [KeepTogether([head_flow, paras[0]])]
        out.extend(paras[1:])
        return out
    return [head_flow]


# ---------------------------------------------------------------------------
# Front-matter / verdict / flag consistency (defect-2 fix)
# ---------------------------------------------------------------------------
# Tokens are matched against NORMALISED text (lowercased, punctuation/hyphens collapsed to
# single spaces), so keep them hyphen-free here.
_FAILURE_TOKENS = (
    "did not validate", "not validate", "validation did not", "validation failed",
    "not validated", "failed validation", "did not pass", "does not validate",
    "control panel did not", "controls did not", "verdict fail", "validation fail",
    "exploratory",
)
_FLAG_TOKENS = (
    "flag", "flagged", "artifact", "caution", "cautioned", "non specific", "nonspecific",
    "cytotoxic", "steroid", "unvalidated", "implausible", "caveat", "not recommend",
    "off target", "promiscuous", "assay", "antiproliferative",
)


def _norm_name(s):
    """Lowercase, drop parentheticals, collapse non-alphanumerics to single spaces."""
    if not isinstance(s, str):
        return ""
    s = s.lower().strip()
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _flag_index(compound_flags):
    """Normalise report_config['compound_flags'] into {norm_name: {...}} — the ONE structure
    the page-1 caption, the body flag table, and the consistency gate all read.

    Accepts a list of {name, classification, note} dicts, or a dict {name: classification}
    or {name: {classification, note}}. Any classification other than 'credible'/'ok'/'clear'
    is treated as a flag that MUST be surfaced wherever the compound is named.
    """
    idx = {}
    if not compound_flags:
        return idx
    items = []
    if isinstance(compound_flags, dict):
        for k, v in compound_flags.items():
            if isinstance(v, dict):
                items.append({"name": k, "classification": v.get("classification", "caution"),
                              "note": v.get("note", "")})
            else:
                items.append({"name": k, "classification": str(v), "note": ""})
    else:
        for it in compound_flags:
            if isinstance(it, dict):
                items.append({"name": it.get("name", ""),
                              "classification": it.get("classification", "caution"),
                              "note": it.get("note", "")})
    for it in items:
        nm = _norm_name(it.get("name", ""))
        if not nm:
            continue
        cls = str(it.get("classification", "caution")).strip().lower()
        idx[nm] = {"name": it.get("name", ""), "classification": cls,
                   "note": (it.get("note", "") or ""),
                   "flagged": cls not in ("credible", "ok", "clear", "")}
    return idx


def _frontmatter_consistency_gate(cfg, status, flag_index, derived_caption):
    """Fail loudly BEFORE export when page 1 would contradict the analysis conclusion.

    Two checks (see references/METHODS.md 6.3):
      (a) if the controls verdict is 'fail', the agent's own front matter (executive_summary /
          key_finding) must state the failure — leading with the verdict, not a ranked slate;
      (b) any compound flagged (artifact/caution) in compound_flags that is NAMED anywhere in
          the front matter (agent text or the derived caption) must carry its flag/caveat near
          the mention — a compound flagged in the body cannot appear unflagged on page 1.
    """
    agent_fields = [cfg.get("key_finding_title", ""), cfg.get("key_finding_body", ""),
                    cfg.get("top_hit_title", ""), cfg.get("top_hit_rationale", ""),
                    cfg.get("infographic_caption", "")]
    es = cfg.get("executive_summary") or []
    if isinstance(es, str):
        es = [es]
    agent_fields += [str(x) for x in es]
    agent_fields = [str(f) for f in agent_fields if f]
    caption_raw = derived_caption or ""
    agent_norm = _norm_name(" ".join(agent_fields))   # combined text, for the verdict check (a)
    caption_norm = _norm_name(caption_raw)
    # Per-field normalised texts: a flag word in ONE field must not mask an unflagged compound
    # mention in ANOTHER field (e.g. a top_hit_rationale 'artifact' next to a key_finding that
    # names a different compound as a candidate). Each field is checked on its own.
    flag_check_texts = [_norm_name(f) for f in agent_fields] + [caption_norm]

    # (a) failed verdict must be stated in the agent's own front matter
    if status == "fail":
        if not any(tok in agent_norm for tok in _FAILURE_TOKENS):
            raise ValueError(
                "Front-matter consistency gate: the controls verdict is 'fail' but neither "
                "executive_summary nor key_finding states that validation did not pass. Page 1 "
                "must LEAD with the failed verdict (state the control panel 'did not validate' / "
                "results are 'exploratory') before naming any candidate, and must not present a "
                "ranked list as recommendations. Add this to executive_summary or "
                "key_finding_body.")
        if re.search(r"approved candidates are\s*#", caption_raw.lower()):
            raise ValueError(
                "Front-matter consistency gate: verdict is 'fail' but the derived infographic "
                "caption still presents a ranked 'approved candidates are #...' recommendation "
                "list. It must be reframed as exploratory/unvalidated when validation fails.")

    # (b) every flagged compound named up front must carry its flag near the mention.
    # Each text field is checked INDEPENDENTLY so the auto-annotated caption cannot mask an
    # unflagged mention in the agent's own text (and vice versa).
    def _named_unflagged(text_norm, nm, cls, note_tokens):
        padded = " " + text_norm + " "
        key = " " + nm + " "
        start = padded.find(key)
        while start != -1:
            lo = max(0, start - 120)
            hi = min(len(padded), start + len(key) + 120)
            window = padded[lo:hi]
            ok = (cls in window
                  or any(t in window for t in _FLAG_TOKENS)
                  or any(t in window for t in note_tokens))
            if not ok:
                return True
            start = padded.find(key, start + 1)
        return False

    for nm, info in flag_index.items():
        if not info["flagged"]:
            continue
        note_tokens = [w for w in re.split(r"[^a-z0-9]+", info["note"].lower()) if len(w) > 3]
        for text_norm in flag_check_texts:
            if _named_unflagged(text_norm, nm, info["classification"], note_tokens):
                raise ValueError(
                    f"Front-matter consistency gate: compound '{info['name']}' is classified "
                    f"'{info['classification']}' in compound_flags but is named in the front "
                    f"matter without its flag/caveat. A compound flagged in the body cannot "
                    f"appear unflagged on page 1 — annotate the mention (e.g. "
                    f"'[{info['classification']}: {info['note'][:40]}]') or drop it from the "
                    f"front matter.")


def build(report_config, stats, tables, figures, out_pdf):
    """Build the PDF. See module docstring for report_config schema.

    tables  : dict with 'approved' (DataFrame, ranked approved candidates w/ columns
              rank/drug/S_reversal/fdr_reversal/moa), optional 'literature' (DataFrame
              w/ drug/direction/evidence/clinical_status), and optional 'controls'
              (DataFrame from controls_and_moa.check_controls — when present, build()
              recomputes the verdict and gates on it).
    figures : dict with keys 'infographic'(optional), 'fig1','fig2','fig3','fig4' -> png paths.
    """
    styles = rs.build_styles()
    cfg = report_config
    disease_label = cfg.get("disease_label", "the disease")
    DATE = date.today().strftime("%B %d, %Y")
    chrome = rs.page_chrome_factory(cfg.get("title", "Drug Repurposing Report"))

    # GUARD: the canonical top-ranked hit must never be emitted unexplained. The agent must
    # supply a non-empty rationale (an honest "likely non-specific / assay artifact" call is a
    # valid rationale). This makes it structurally impossible to ship an unexplained #1.
    top_hit_rationale = (cfg.get("top_hit_rationale") or "").strip()
    if not top_hit_rationale:
        raise ValueError(
            "report_config['top_hit_rationale'] is required and must be non-empty: the canonical "
            "#1-ranked candidate must always be rationalized in the report (even if the rationale "
            "is that it is a likely non-specific / assay artifact).")

    # GUARD: controls validation gate. If tables['controls'] is present, RECOMPUTE the verdict
    # inside build() via controls_and_moa.controls_verdict — never read a status the agent typed
    # into report_config, so the report cannot claim a rosier validation state than the CSV
    # supports. If the verdict is 'fail' or 'weak', raise ValueError unless the agent supplies a
    # non-empty controls_failure_acknowledgement. If tables['controls'] is absent, do NOT raise
    # (backwards compatibility with existing call sites).
    verdict = None
    controls_df = tables.get("controls")
    if controls_df is not None:
        try:
            import controls_and_moa as _cam
            verdict = _cam.controls_verdict(controls_df)
        except Exception:
            verdict = None
        if verdict and verdict["status"] in ("fail", "weak"):
            ack = (cfg.get("controls_failure_acknowledgement") or "").strip()
            if not ack:
                raise ValueError(
                    f"report_config['controls_failure_acknowledgement'] is required when the "
                    f"controls verdict is '{verdict['status']}' ({verdict['summary']}). The "
                    f"positive-control panel did not validate on this signature; the report must "
                    f"acknowledge this explicitly so a failed-validation slate cannot read as "
                    f"confident.")
    status = verdict["status"] if verdict else None

    # SINGLE SOURCE OF TRUTH for compound classifications: page 1 (caption), the body flag
    # table, and the consistency gate all read this one structure. Then DERIVE the
    # verdict/flag-aware infographic caption up front so the gate inspects exactly what page 1
    # will show, and the same string is rendered under the infographic below.
    flag_index = _flag_index(cfg.get("compound_flags"))
    derived_caption = "Overview of the connectivity-based repurposing workflow applied in this report."
    try:
        import make_infographic as _mi
        derived_caption = _mi.infographic_caption_from_data(
            stats, tables.get("approved"), verdict=verdict,
            compound_flags=cfg.get("compound_flags"))
    except Exception:
        pass  # degrade to default string if module missing

    # GUARD: front-matter/verdict/flag consistency gate — fail loudly BEFORE export if page 1
    # would contradict the controls verdict or the compound-flag classifications (METHODS 6.3).
    _frontmatter_consistency_gate(cfg, status, flag_index, derived_caption)

    story = []
    # ---- Title ----
    story += [Spacer(1, 30),
              Paragraph(cfg["title"], styles["ReportTitle"]),
              Paragraph(cfg.get("subtitle", "Connectivity-Based Repurposing via LINCS Signature Reversal"),
                        styles["Subtitle"]),
              Spacer(1, 6),
              Paragraph(f"<i>Generated by Biomni  |  {DATE}</i>", styles["Attribution"]),
              rs.divider(), Spacer(1, 6)]

    # ---- Verdict-led headline (page 1 leads with the control-panel verdict) ----
    # Rendered straight from the recomputed verdict so the first thing a reader sees matches
    # the analysis conclusion. On fail/weak this is the mandatory "did not pass" banner, placed
    # BEFORE the infographic/candidates so the verdict precedes any named candidate.
    if verdict is not None:
        if status in ("fail", "weak"):
            _bf = "<br/>".join(verdict["failures"]) if verdict.get("failures") else verdict["summary"]
            _bf += ("<br/><br/>Candidates in this report are exploratory: the positive-control "
                    "panel did not validate on this signature.")
            story.append(rs.callout(
                "Method validation did not pass \u2014 results are exploratory", _bf, styles))
        else:
            story.append(rs.callout("Control validation: passed", verdict["summary"], styles))

    # ---- Optional infographic ----
    # The caption is DERIVED from the dataframe (derive-not-restate): even if a future image
    # model garbles the graphic, the printed caption directly beneath it states the real
    # canonical top candidates and counts read from the approved DataFrame, so the deliverable
    # cannot silently assert a ranking that disagrees with all_drugs_ranked.csv. The agent's
    # free-text cfg['infographic_caption'] is an optional PREFIX only.
    if figures.get("infographic") and os.path.exists(figures["infographic"]):
        agent_prefix = (cfg.get("infographic_caption") or "").strip()
        final_caption = (agent_prefix + " " + derived_caption) if agent_prefix else derived_caption
        story.append(_fig(figures["infographic"], 452, final_caption, styles=styles))

    # ---- Key finding callout (placed up front so it lands on page 1) ----
    if cfg.get("key_finding_title"):
        story.append(rs.callout(cfg["key_finding_title"], cfg.get("key_finding_body", ""), styles))

    # (The controls-validation banner is rendered as the verdict-led headline near the top of
    # page 1 — see above — so the verdict precedes any named candidate.)

    # ---- Mandatory top-ranked-candidate rationale (canonical #1 is always explained) ----
    story.append(rs.callout(
        cfg.get("top_hit_title", "Top-ranked candidate"), top_hit_rationale, styles))

    # ---- Executive summary ----
    story += _section("Executive Summary", cfg.get("executive_summary"), styles)

    # ---- 1. Introduction ----
    story += _section("1. Introduction", cfg.get("introduction"), styles)

    # ---- 2. Methods (fixed, data-driven) ----
    story.append(Paragraph("2. Methods", styles["SectionHead"]))
    story.append(Paragraph("2.1 Data sources", styles["SubHead"]))
    story.append(Paragraph(
        f"<b>Disease signature.</b> The up/down gene sets for <i>{disease_label}</i> were "
        f"{cfg.get('signature_provenance', 'obtained as described in the analysis inputs')}. "
        f"The signature comprises {stats['n_up']} up- and {stats['n_dn']} down-regulated genes.",
        styles["Body"]))
    story.append(Paragraph(
        f"<b>Drug perturbation signatures.</b> {stats['n_drugs']} single-drug up/down perturbation "
        f"signatures from the LINCS \u201cDrug Perturbations from GEO\u201d gene-set collection "
        f"({stats.get('n_human','?')} in human, {stats.get('n_mouse','?')} in mouse gene symbols). "
        "This is the gene-set (ranked up/down list) form of the L1000 data, not the continuous "
        "z-score matrix; the scoring method was chosen accordingly.", styles["Body"]))
    story.append(Paragraph(
        "<b>Approved-drug annotation.</b> The Broad Institute Drug Repurposing Hub provided clinical "
        "phase, mechanism of action (MOA), protein target, and indication. \u201cApproved\u201d was defined "
        "as clinical phase = <i>Launched</i>.", styles["Body"]))
    story.append(Paragraph("2.2 Gene-space harmonization", styles["SubHead"]))
    story.append(Paragraph(
        f"Because the disease signature is human but a fraction of drug signatures use mouse symbols, "
        f"mouse genes were mapped to human orthologs using the MGI mouse\u2013human homology table "
        f"(median mapping rate {stats.get('mouse_map_median','~86')}% per mouse signature; unmapped "
        f"symbols retained via uppercasing of conserved symbols). All signatures were restricted to a "
        f"common background of {stats['bg']:,} genes.", styles["Body"]))
    story.append(Paragraph("2.3 Connectivity scoring and significance", styles["SubHead"]))
    story.append(Paragraph(
        "For each drug we computed a <b>reversal connectivity score</b> (S<sub>reversal</sub>) that rewards "
        "overlap between disease-up genes and drug-down genes (and disease-down with drug-up), and penalizes "
        "same-direction overlap (disease mimicry). Each overlap was size-corrected against its hypergeometric "
        "expectation, yielding a signed statistic where <b>positive values indicate disease reversal</b>. "
        "Significance was assessed with a <b>10,000-fold permutation null</b> (gene labels shuffled preserving "
        "set sizes), and p-values were corrected across all drugs by the Benjamini\u2013Hochberg method (FDR). As "
        "an independent cross-check, a Kolmogorov\u2013Smirnov enrichment-based reversal score was computed and "
        f"combined with S<sub>reversal</sub> into a consensus rank (Spearman &#961; = {stats.get('rho','?')} "
        "between the two scores).", styles["Body"]))
    if figures.get("fig3"):
        story.append(_fig(figures["fig3"], 470,
            f"Figure 1. The {disease_label} disease signature used as the query "
            f"({stats['n_up']} up-, {stats['n_dn']} down-regulated genes). "
            + cfg.get("marker_note", "A drug is predicted therapeutic if its perturbation reverses this pattern."),
            styles))
    story.append(PageBreak())

    # ---- 3. Results ----
    story.append(Paragraph("3. Results", styles["SectionHead"]))
    story.append(Paragraph("3.1 Distribution of reversal scores", styles["SubHead"]))
    story += _para_list(cfg.get("results_intro"), "Body", styles)
    if figures.get("fig1"):
        story.append(_fig(figures["fig1"], 430,
            f"Figure 2. Distribution of {disease_label} signature-reversal connectivity scores across all "
            f"{stats['n_drugs']} LINCS drug signatures. Positive scores (shaded) indicate reversal; negative "
            "scores indicate mimicry.", styles))

    story.append(Paragraph("3.2 Top approved drug repurposing candidates", styles["SubHead"]))
    story += _para_list(cfg.get("results_top"), "Body", styles)
    if figures.get("fig2"):
        story.append(_fig(figures["fig2"], 420,
            f"Figure 3. Top approved drug repurposing candidates for {disease_label}, ordered by the single "
            "canonical rank (consensus of two connectivity methods; the same order used in Table 1 and the "
            "literature slate). Bar length is the reversal score. Green = significant at FDR < 0.05.", styles))

    # Table 1: top approved -- iterated in canonical_rank order (the frame is already sorted)
    appr = tables["approved"]
    n_top = min(15, len(appr))
    story.append(Paragraph(f"Table 1. Top {n_top} approved candidates (ordered by the canonical rank; consensus of two connectivity scores).",
                           styles["SubHead"]))
    hdr = ["Rank", "Drug", "S<sub>rev</sub>", "FDR", "Mechanism of action (first listed)"]
    rows = [[Paragraph(h, styles["TblHead"]) for h in hdr]]
    for _, r in appr.head(n_top).iterrows():
        moa = str(r["moa"]).split("|")[0] if pd.notna(r.get("moa")) else "n/a"
        # prefer the authoritative canonical_rank; fall back to a provided display rank
        if "canonical_rank" in r and pd.notna(r.get("canonical_rank")):
            rank_val = int(r["canonical_rank"])
        elif "rank" in r and pd.notna(r.get("rank")):
            rank_val = int(r["rank"])
        else:
            rank_val = int(r.get("consensus_rank", 0))
        rows.append([Paragraph(str(rank_val), styles["TblCell"]),
                     Paragraph(str(r["drug"]), styles["TblCellB"]),
                     Paragraph(f"{r['S_reversal']:.1f}", styles["TblCell"]),
                     Paragraph(f"{r['fdr_reversal']:.1e}", styles["TblCell"]),
                     Paragraph(moa, styles["TblCell"])])
    t1 = Table(rows, colWidths=[34, 96, 44, 52, 232], repeatRows=1)
    t1.hAlign = "CENTER"
    t1.setStyle(rs.table_style(len(rows)))
    story.append(t1)
    story.append(Paragraph(
        "Full ranked results for all drugs are provided in <font name='Courier'>all_drugs_ranked.csv</font>; "
        "the complete approved-candidate list in <font name='Courier'>approved_repurposing_candidates.csv</font>.",
        styles["Caption"]))
    story.append(PageBreak())

    story.append(Paragraph("3.3 Mechanistic themes and method validation", styles["SubHead"]))
    story += _para_list(cfg.get("results_moa"), "Body", styles)
    if figures.get("fig4"):
        story.append(_fig(figures["fig4"], 480,
            "Figure 4. (A) Mechanisms of action most frequent among top approved reversers (nominal). "
            "(B) Method validation: named control compounds, colored by their expected direction "
            "(reversers positive, disease-mimics negative).", styles))

    # ---- Flagged / cautioned compounds table (single source of truth = compound_flags) ----
    # The SAME structure page 1 reads, rendered in the body so a compound flagged here can
    # never appear unflagged on page 1 (both read flag_index / compound_flags).
    if flag_index:
        story.append(Paragraph("Table 2. Flagged / cautioned compounds (mechanistic credibility).",
                               styles["SubHead"]))
        _hf = ["Compound", "Classification", "Why flagged / cautioned"]
        _rf = [[Paragraph(h, styles["TblHead"]) for h in _hf]]
        for _info in flag_index.values():
            _rf.append([Paragraph(str(_info["name"]), styles["TblCellB"]),
                        Paragraph(str(_info["classification"]), styles["TblCell"]),
                        Paragraph(str(_info["note"] or "\u2014"), styles["TblCell"])])
        _tf = Table(_rf, colWidths=[120, 90, 248], repeatRows=1)
        _tf.hAlign = "CENTER"
        _tf.setStyle(rs.table_style(len(_rf), valign="TOP"))
        story.append(_tf)
        story.append(Paragraph(
            "A high connectivity score for these compounds should not be read as a therapeutic "
            "recommendation; these classifications are the single source of truth used on page 1 "
            "and throughout the report.", styles["Caption"]))

    story.append(Paragraph("3.4 Positive controls and literature grounding", styles["SubHead"]))
    story += _para_list(cfg.get("results_controls"), "Body", styles)
    lit = tables.get("literature")
    if lit is not None and len(lit):
        _lit_no = 3 if flag_index else 2
        story.append(Paragraph(f"Table {_lit_no}. Literature evidence for selected candidates and controls.", styles["SubHead"]))
        hdr2 = ["Drug / control", "Direction", "Evidence summary &amp; clinical status"]
        rows2 = [[Paragraph(h, styles["TblHead"]) for h in hdr2]]
        for _, r in lit.iterrows():
            rows2.append([Paragraph(str(r["drug"]), styles["TblCellB"]),
                          Paragraph(str(r["direction"]), styles["TblCell"]),
                          Paragraph(f"{r['evidence']} <b>[{r['clinical_status']}]</b>", styles["TblCell"])])
        t2 = Table(rows2, colWidths=[95, 80, 283], repeatRows=1)
        t2.hAlign = "CENTER"
        t2.setStyle(rs.table_style(len(rows2), valign="TOP"))
        story.append(t2)
    story.append(PageBreak())

    # ---- 4. Discussion ----
    story += _section("4. Discussion", cfg.get("discussion"), styles)
    story.append(Paragraph("4.1 Limitations", styles["SubHead"]))
    lims = cfg.get("limitations") or []
    li = [ListItem(Paragraph(f"<b>{t}.</b> {d}", styles["BodyL"]), value="\u2022") for t, d in lims]
    if li:
        story.append(ListFlowable(li, bulletType="bullet", start="\u2022", leftIndent=12))

    # ---- 5. Conclusions ----
    story += _section("5. Conclusions", cfg.get("conclusions"), styles)
    if cfg.get("bottom_line_body"):
        story.append(rs.callout("Bottom line", cfg["bottom_line_body"], styles))

    # ---- References ----
    story.append(PageBreak())
    story.append(Paragraph("References", styles["SectionHead"]))

    # GUARD: every reference must carry a verifiable locator token (PMID / PMCID / doi.org/ /
    # DOI: / NCT / http). A hand-typed citation with volume/issue/pages and no identifier
    # cannot be checked in one click by a reviewer. Same guard pattern as top_hit_rationale.
    _locator_tokens = ("pmid", "pmcid", "doi.org/", "doi:", "nct", "http")
    refs_list = cfg.get("references", [])
    bad_refs = []
    for i, ref in enumerate(refs_list, 1):
        ref_lower = str(ref).lower()
        if not any(tok in ref_lower for tok in _locator_tokens):
            bad_refs.append((i, str(ref)[:60]))
    if bad_refs:
        detail = "; ".join(f"[{i}] {preview}..." for i, preview in bad_refs)
        raise ValueError(
            f"report_config['references'] entries at index/indices {', '.join(str(i) for i, _ in bad_refs)} "
            f"lack a verifiable locator token (PMID / PMCID / doi.org/ / DOI: / NCT / http). "
            f"Offending entries: {detail}. Use literature_evidence.references_from_records() to "
            f"build reference strings from the LiteratureSearch records in references.jsonl, which "
            f"always append a PMID/DOI/URL locator.")

    for i, ref in enumerate(refs_list, 1):
        story.append(Paragraph(f"[{i}] {ref}", styles["Ref"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>Data resources.</b> LINCS L1000 gene-set signatures (disease and single-drug perturbations); "
        "Broad Institute Drug Repurposing Hub (clinical phase, MOA, target, indication); MGI mouse\u2013human "
        "orthology report.", styles["Caption"]))

    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)
    # Page geometry/margins are owned by the pdf-report-generation skill; use platform defaults.
    doc = SimpleDocTemplate(out_pdf, pagesize=letter, title=cfg["title"], author="Biomni")
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return out_pdf
