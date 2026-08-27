"""End-to-end orchestrator for pharmacovigilance AE signal detection.

Ties the modules together into one callable pipeline:

    resolve_drugs -> query_faers (drug totals, per-drug reaction counts,
    drug x event co-occurrence, background totals) -> compute_disproportionality
    -> annotate_signals -> generate_figures -> tables -> generate_report

Usage (from an agent / notebook)::

    from run_analysis import run_analysis, AnalysisConfig
    cfg = AnalysisConfig(query="semaglutide", out_dir="/mnt/results/semaglutide_pv")
    result = run_analysis(cfg)          # returns a dict of artifacts + the ctx

Two tool integration points CANNOT be executed from inside a plain module and
are therefore left to the AGENT (they are agent tools, not Python APIs):

  * ``LiteratureSearch`` - the orchestrator builds the query string
    (:func:`annotate_signals.build_literature_query`) and exposes it in the
    returned dict as ``literature_query``; the agent runs LiteratureSearch,
    then passes the returned records back via ``cfg.references`` (or calls
    :func:`finalize_report` again) so they appear in the report + ground the
    ``lit_support`` flag.
  * ``GenerateImage`` - the orchestrator exposes ``infographic_prompt``; the
    agent generates the schematic image and passes its path via
    ``cfg.infographic_path`` for inclusion in the report.

This keeps the module deterministic and testable while still driving the two
agent-only capabilities.

IMPORTANT interpretation note (must be surfaced in any deliverable):
disproportionality signals are hypothesis-generating measures of *differential
reporting*, not of causal risk or incidence.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from . import query_faers as qf
from . import resolve_drugs as rd
from . import compute_disproportionality as cd
from . import annotate_signals as an
from . import generate_figures as gfig
from . import generate_report as grep


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
@dataclass
class AnalysisConfig:
    query: object                       # str, list[str] (explicit), class, or target
    out_dir: str                        # where artifacts are written
    mode: Optional[str] = None          # force explicit|class|target (else auto)
    subject_label: Optional[str] = None  # human label for the report title
    comparator: Optional[List[str]] = None  # custom background drug list (None=full FAERS)
    top_n_events: int = 500             # reaction terms to pull per drug (<=500)
    criteria: cd.SignalCriteria = field(default_factory=cd.SignalCriteria)
    pool: bool = True                   # also analyse the pooled/combined set
    pool_label: str = "combined"        # name of the pooled pseudo-drug
    api_key: Optional[str] = None       # optional openFDA API key (higher limits)
    references: List[dict] = field(default_factory=list)   # from LiteratureSearch
    infographic_path: Optional[str] = None                # from GenerateImage
    make_report: bool = True
    make_figures: bool = True


# --------------------------------------------------------------------------- #
# step 1: resolve + validate the drug set
# --------------------------------------------------------------------------- #
def resolve(cfg: AnalysisConfig) -> dict:
    res = rd.resolve_drugs(cfg.query, mode=cfg.mode, api_key=cfg.api_key)
    return res


# --------------------------------------------------------------------------- #
# step 2: pull counts from FAERS
# --------------------------------------------------------------------------- #
def _pull_counts(drugs: List[str], cfg: AnalysisConfig,
                 progress=print) -> dict:
    """Return the raw counts needed to build every 2x2 table.

    Structure::
        {"drug_totals": {drug: a+b},
         "event_totals": {event: a+c across universe},
         "cooccur": DataFrame[drug, event, a],
         "n_total": N,
         "events": [union of top events across drugs]}
    """
    # background universe N and per-event totals use the comparator (or full FAERS)
    bg_search = qf.background_search(cfg.comparator)
    n_total = qf.get_total(bg_search, cfg.api_key)
    progress(f"  background universe N = {n_total:,}")

    drug_totals: Dict[str, int] = {}
    per_drug_events: Dict[str, Dict[str, int]] = {}
    for d in drugs:
        s = qf.drug_event_search([d])
        drug_totals[d] = qf.get_total(s, cfg.api_key)
        per_drug_events[d] = qf.get_term_counts(s, cfg.top_n_events, cfg.api_key)
        progress(f"  {d}: {drug_totals[d]:,} reports, "
                 f"{len(per_drug_events[d])} distinct top reactions")
        time.sleep(0.2)

    # union of events we will test
    events = sorted({e for m in per_drug_events.values() for e in m})

    # event totals across the whole universe (a+c). One count query per event is
    # expensive; instead pull the top reaction terms of the WHOLE background in a
    # single faceted call and fall back to per-event queries for any missing.
    event_totals = qf.get_term_counts(bg_search, cfg.top_n_events, cfg.api_key)
    missing = [e for e in events if e not in event_totals]
    for e in missing:
        c = qf.count_single_term(bg_search, e, cfg.api_key)
        if c is not None:
            event_totals[e] = c
        time.sleep(0.05)

    # co-occurrence a = count of (drug AND event)
    rows = []
    for d in drugs:
        for e, a in per_drug_events[d].items():
            rows.append({"drug": d, "event": e, "a": a})
    cooccur = pd.DataFrame(rows)

    return {"drug_totals": drug_totals, "event_totals": event_totals,
            "cooccur": cooccur, "n_total": n_total, "events": events,
            "per_drug_events": per_drug_events}


def _add_pool(counts: dict, drugs: List[str], cfg: AnalysisConfig,
              progress=print) -> dict:
    """Add a pooled pseudo-drug (union of the member drugs) to the counts."""
    pool_search = qf.drug_event_search(drugs)
    counts["drug_totals"][cfg.pool_label] = qf.get_total(pool_search, cfg.api_key)
    pooled_events = qf.get_term_counts(pool_search, cfg.top_n_events, cfg.api_key)
    progress(f"  pooled '{cfg.pool_label}': "
             f"{counts['drug_totals'][cfg.pool_label]:,} reports")
    extra = []
    for e, a in pooled_events.items():
        extra.append({"drug": cfg.pool_label, "event": e, "a": a})
        if e not in counts["event_totals"]:
            c = qf.count_single_term(qf.background_search(cfg.comparator), e,
                                     cfg.api_key)
            if c is not None:
                counts["event_totals"][e] = c
    counts["cooccur"] = pd.concat([counts["cooccur"], pd.DataFrame(extra)],
                                  ignore_index=True)
    return counts


# --------------------------------------------------------------------------- #
# step 3-4: compute + annotate
# --------------------------------------------------------------------------- #
def compute_and_annotate(counts: dict, drugs: List[str], cfg: AnalysisConfig,
                         label_reps: Optional[Dict[str, str]] = None
                         ) -> pd.DataFrame:
    """Compute disproportionality and annotate the results.

    ``label_reps`` maps a pooled/pseudo-drug name to a representative real drug
    whose FDA label should be used for label-status annotation of pooled rows.
    """
    res = cd.compute_disproportionality(
        counts["cooccur"], n_total=counts["n_total"],
        drug_totals=counts["drug_totals"],
        event_totals=counts["event_totals"], criteria=cfg.criteria)

    # label cache: pooled pseudo-drug uses a representative member's label
    label_cache: Dict[str, dict] = {}
    reps = label_reps or {}
    for d in res["drug"].unique():
        real = reps.get(d, d)
        label_cache[d] = qf.fetch_drug_label(real, cfg.api_key)

    res = an.annotate_signals(res, drugs=list(res["drug"].unique()),
                              api_key=cfg.api_key, label_cache=label_cache,
                              criteria=cfg.criteria)
    return res


# --------------------------------------------------------------------------- #
# step 5: tables
# --------------------------------------------------------------------------- #
def _fmt_ror(r) -> str:
    if pd.isna(r["ror"]):
        return "n/a"
    return f"{r['ror']:.1f} ({r['ror_lower']:.1f}\u2013{r['ror_upper']:.1f})"


def _fmt_sci(x) -> str:
    """Format a large number as a x 10^b string using <super> tags for PDF."""
    if pd.isna(x):
        return "n/a"
    if x < 1e4:
        return f"{x:.0f}"
    exp = int(np.floor(np.log10(x)))
    mant = x / 10 ** exp
    return f"{mant:.1f}\u00d710<super>{exp}</super>"


def signal_counts(res: pd.DataFrame, subject: str) -> dict:
    """SINGLE SOURCE OF TRUTH for every signal count used in the report.

    All tables, figures, and report text must read their counts from this dict
    so the numbers can never disagree (the previous bug printed the noise-
    *included* total 68 alongside the noise-*excluded* label split 21+36=57 in
    the same sentence).

    Guarantees, by construction:
        n_pass_criteria == n_genuine + n_noise
        n_genuine       == n_labeled + n_unlabeled + n_unknown

    Definitions (all restricted to the primary ``subject`` rows):
        n_pass_criteria : rows with signal == True  (statistical criteria only)
        n_noise         : signal & is_noise         (non-clinical artifacts)
        n_genuine       : signal & ~is_noise        (genuine ADR signals)
        n_labeled       : genuine & label_status in {labeled, boxed}
        n_unlabeled     : genuine & label_status == unlabeled
        n_unknown       : genuine & label_status == unknown
        n_low_confidence: genuine & low_confidence  (flagged, NOT removed)
    """
    sub = res[res["drug"] == subject] if subject in set(res["drug"]) else res
    sig = sub.get("signal", pd.Series(False, index=sub.index)).fillna(False).astype(bool)
    noise = sub.get("is_noise", pd.Series(False, index=sub.index)).fillna(False).astype(bool)
    genuine_mask = sig & ~noise
    genuine = sub[genuine_mask]
    ls = genuine["label_status"] if "label_status" in genuine.columns else pd.Series([], dtype=str)
    n_labeled = int(ls.isin(["labeled", "boxed"]).sum())
    n_unlabeled = int((ls == "unlabeled").sum())
    n_unknown = int((ls == "unknown").sum())
    lc = (genuine["low_confidence"].fillna(False).astype(bool)
          if "low_confidence" in genuine.columns else pd.Series(False, index=genuine.index))
    return {
        "n_events_tested": int(sub.shape[0]),
        "n_pass_criteria": int(sig.sum()),
        "n_noise": int((sig & noise).sum()),
        "n_genuine": int(genuine_mask.sum()),
        "n_labeled": n_labeled,
        "n_unlabeled": n_unlabeled,
        "n_unknown": n_unknown,
        "n_low_confidence": int(lc.sum()),
    }


def write_tables(res: pd.DataFrame, subject: str, out_dir: str,
                 counts: dict, cfg: AnalysisConfig) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    tables: Dict[str, str] = {}
    sc = signal_counts(res, subject)   # single source of truth

    # full machine-readable table
    full = os.path.join(out_dir, "disproportionality_full.csv")
    res.to_csv(full, index=False)
    tables["full_csv"] = full

    # overview -- every count derived from signal_counts() (single source of truth)
    primary = subject
    sub = res[res["drug"] == primary] if primary in set(res["drug"]) else res
    ov = pd.DataFrame([
        {"Metric": "Drugs analysed", "Value": ", ".join(
            d for d in counts["drug_totals"] if d != cfg.pool_label)},
        {"Metric": "Background universe (N)", "Value": f"{counts['n_total']:,}"},
        {"Metric": "Reaction terms tested", "Value": f"{sc['n_events_tested']:,}"},
        {"Metric": "Signals passing criteria", "Value": f"{sc['n_pass_criteria']:,}"},
        {"Metric": "  \u2014 non-clinical artifacts excluded", "Value": f"{sc['n_noise']:,}"},
        {"Metric": "Genuine ADR signals", "Value": f"{sc['n_genuine']:,}"},
        {"Metric": "  \u2014 labeled (incl. boxed)", "Value": f"{sc['n_labeled']:,}"},
        {"Metric": "  \u2014 unlabeled", "Value": f"{sc['n_unlabeled']:,}"},
        {"Metric": "  \u2014 unknown (no label)", "Value": f"{sc['n_unknown']:,}"},
        {"Metric": "  \u2014 low-confidence (flagged, retained)", "Value": f"{sc['n_low_confidence']:,}"},
        {"Metric": "Signal criteria", "Value": cfg.criteria.describe()},
        {"Metric": "Low-confidence rule", "Value": cfg.criteria.describe_confidence()},
    ])
    ov_p = os.path.join(out_dir, "table1_overview.csv")
    ov.to_csv(ov_p, index=False)
    tables["overview"] = ov_p

    # top signals (genuine ADRs only); low-confidence rows are KEPT and marked
    clean = sub[sub.get("signal", False) & (~sub.get("is_noise", False))]
    top = clean.sort_values("ror", ascending=False).head(20).copy()
    if "low_confidence" not in top.columns:
        top["low_confidence"] = False
    top_disp = pd.DataFrame({
        "Adverse event": top["event"].map(gfig._display),
        "Cases": top["a"].astype(int),
        "ROR (95% CI)": top.apply(_fmt_ror, axis=1),
        "PRR": top["prr"].round(1),
        "\u03c7\u00b2": top["chi2"].map(_fmt_sci),
        "Label status": top["label_status"],
        "Conf.": top["low_confidence"].map(lambda x: "low" if bool(x) else "\u2713"),
    })
    tp = os.path.join(out_dir, "table2_top_signals.csv")
    top_disp.to_csv(tp, index=False)
    tables["top_signals"] = tp

    # unlabeled shortlist
    unlab = clean[clean["label_status"] == "unlabeled"].sort_values(
        "ror", ascending=False).head(15).copy()
    if len(unlab):
        unlab_disp = pd.DataFrame({
            "Adverse event": unlab["event"].map(gfig._display),
            "Cases": unlab["a"].astype(int),
            "ROR (95% CI)": unlab.apply(_fmt_ror, axis=1),
            "Category": unlab["category"],
            "SOC": unlab["soc"],
        })
        up = os.path.join(out_dir, "table3_unlabeled_signals.csv")
        unlab_disp.to_csv(up, index=False)
        tables["unlabeled"] = up
    return tables


# --------------------------------------------------------------------------- #
# step 6: figures
# --------------------------------------------------------------------------- #
def write_figures(res: pd.DataFrame, subject: str, out_dir: str,
                  member_drugs: List[str]) -> Dict[str, str]:
    fig_dir = os.path.join(out_dir, "figures")
    figs = gfig.generate_all_figures(res, subject, fig_dir, drugs=member_drugs)
    out = {k: v["png"] for k, v in figs.items()}
    # the cross-drug heatmap is only meaningful with >1 member drug; for a single
    # drug it is a degenerate one-column figure, so omit it from the report set.
    if len(set(member_drugs)) < 2:
        out.pop("heatmap", None)
    return out


# --------------------------------------------------------------------------- #
# step 7: report context + build
# --------------------------------------------------------------------------- #
def build_context(res: pd.DataFrame, subject: str, cfg: AnalysisConfig,
                  counts: dict, tables: Dict[str, str],
                  figures: Dict[str, str], drugs: List[str],
                  dropped: List[tuple]) -> "grep.ReportContext":
    sub = res[res["drug"] == subject] if subject in set(res["drug"]) else res
    clean = sub[sub.get("signal", False) & (~sub.get("is_noise", False))]
    sc = signal_counts(res, subject)   # single source of truth
    top = clean.sort_values("ror", ascending=False).head(3)
    # top_signals tuples carry a 7th element: low_confidence flag
    top_signals = [(r.event, r.ror, r.ror_lower, r.ror_upper, r.a, r.label_status,
                    bool(getattr(r, "low_confidence", False)))
                   for _, r in top.iterrows()]
    # names of low-confidence signals among the genuine set (for report text)
    lc_clean = clean[clean.get("low_confidence", False).fillna(False)] if "low_confidence" in clean.columns else clean.iloc[0:0]
    low_conf_signals = [(r.event, r.ror, r.a, str(getattr(r, "low_confidence_reason", "")))
                        for _, r in lc_clean.sort_values("ror", ascending=False).iterrows()]
    comp = ("the full FAERS background" if not cfg.comparator
            else "a custom comparator set (" + ", ".join(cfg.comparator[:5]) + ")")
    ctx = grep.ReportContext(
        subject=cfg.subject_label or subject,
        mode=res.attrs.get("mode", cfg.mode or "explicit"),
        drugs=drugs,
        n_drugs_reports=int(counts["drug_totals"].get(subject, 0)),
        n_background=int(counts["n_total"]),
        criteria_text=cfg.criteria.describe(),
        confidence_text=cfg.criteria.describe_confidence(),
        n_signals=sc["n_pass_criteria"],
        n_noise_signals=sc["n_noise"],
        n_genuine_signals=sc["n_genuine"],
        n_events_tested=sc["n_events_tested"],
        comparator_desc=comp,
        top_signals=top_signals,
        n_labeled_signals=sc["n_labeled"],
        n_unlabeled_signals=sc["n_unlabeled"],
        n_unknown_signals=sc["n_unknown"],
        n_low_confidence=sc["n_low_confidence"],
        low_conf_signals=low_conf_signals,
        figures=figures,
        tables={k: v for k, v in tables.items() if k in ("overview", "top_signals", "unlabeled")},
        table_colwidths={
            "overview": [232, 260],
            "top_signals": [138, 36, 110, 30, 50, 78, 40],
            "unlabeled": [150, 45, 118, 80, 99],
        },
        table_aligns={
            "overview": ["l", "l"],
            "top_signals": ["l", "c", "c", "c", "c", "c", "c"],
            "unlabeled": ["l", "c", "c", "l", "l"],
        },
        references=cfg.references,
        infographic=cfg.infographic_path,
        dropped_drugs=dropped,
    )
    return ctx


# --------------------------------------------------------------------------- #
# top-level driver
# --------------------------------------------------------------------------- #
def run_analysis(cfg: AnalysisConfig, progress=print) -> dict:
    """Run the full pipeline; returns a dict of artifacts and the report ctx.

    The returned dict includes ``literature_query`` and ``infographic_prompt``
    so the agent can run the two agent-only tools and (optionally) re-run
    :func:`finalize_report` with the enriched context.
    """
    os.makedirs(cfg.out_dir, exist_ok=True)
    progress("[1/6] Resolving drug set ...")
    r = resolve(cfg)
    drugs = r["drugs"]
    if not drugs:
        raise ValueError(f"No FAERS-queryable drugs resolved from {cfg.query!r}. "
                         f"Dropped: {r.get('dropped')}")
    progress(f"  resolved {len(drugs)} drug(s): {', '.join(drugs)}  (mode={r['mode']})")

    progress("[2/6] Pulling FAERS counts ...")
    counts = _pull_counts(drugs, cfg, progress)
    subject = drugs[0]
    label_reps = {}
    if cfg.pool and len(drugs) > 1:
        counts = _add_pool(counts, drugs, cfg, progress)
        subject = cfg.pool_label
        label_reps[cfg.pool_label] = drugs[0]  # representative label

    progress("[3/6] Computing disproportionality + annotating ...")
    res = compute_and_annotate(counts, drugs, cfg, label_reps=label_reps)
    res.attrs["mode"] = r["mode"]

    progress("[4/6] Writing tables ...")
    tables = write_tables(res, subject, cfg.out_dir, counts, cfg)

    figures: Dict[str, str] = {}
    if cfg.make_figures:
        progress("[5/6] Generating figures ...")
        figures = write_figures(res, subject, cfg.out_dir, drugs)

    # literature + infographic prompts for the agent
    clean = res[(res["drug"] == subject) & res.get("signal", False)
                & (~res.get("is_noise", False))]
    top_events = clean.sort_values("ror", ascending=False)["event"].head(8).map(gfig._display).tolist()
    lit_query = an.build_literature_query(cfg.subject_label or subject, top_events)

    ctx = build_context(res, subject, cfg, counts, tables, figures, drugs,
                        r.get("dropped", []))
    lit_prompt = grep.infographic_prompt(ctx)

    report_path = None
    if cfg.make_report:
        progress("[6/6] Building PDF report ...")
        report_path = os.path.join(
            cfg.out_dir, f"report_{_slug(cfg.subject_label or subject)}.pdf")
        grep.build_report(ctx, report_path)
        v = grep.validate_pdf(report_path)
        progress(f"  report: {report_path}  ({v['pages']} pages, "
                 f"{'OK' if v['ok'] else 'ISSUES: ' + ';'.join(v['issues'])})")

    return {"results": res, "counts": counts, "tables": tables,
            "figures": figures, "context": ctx, "report": report_path,
            "drugs": drugs, "mode": r["mode"], "dropped": r.get("dropped", []),
            "literature_query": lit_query,
            "infographic_prompt": lit_prompt}


def finalize_report(ctx: "grep.ReportContext", out_dir: str,
                    references: Optional[List[dict]] = None,
                    infographic_path: Optional[str] = None) -> str:
    """Rebuild the report after the agent has run LiteratureSearch/GenerateImage.

    Pass the LiteratureSearch records and/or the GenerateImage path; returns the
    new report path.
    """
    if references is not None:
        ctx.references = references
    if infographic_path is not None:
        ctx.infographic = infographic_path
    path = os.path.join(out_dir, f"report_{_slug(ctx.subject)}.pdf")
    grep.build_report(ctx, path)
    return path


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")[:60]
