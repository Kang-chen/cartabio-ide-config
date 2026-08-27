#!/usr/bin/env python3
"""Render ``deliverables/review.md`` deterministically from canonical artifacts.

Same model, same sections, same figures as the PDF — see ``report_model.py``.
Running this twice on the same run produces byte-identical output; that is the
property the skill previously lacked, and its absence is why two runs of the
same pipeline produced reports with five figures and with one.

The document sections are the PDF's sections, from the same model:

    Title · Summary (evidence-axis synthesis table) · Introduction · Methods ·
    Results · Conclusions · Figures (+ synthesis panel) · Next steps ·
    Corpus accountability · References

This file used to emit five of them. The prose sections reached the PDF through
four loose ``--*-file`` arguments that the Markdown had no way to receive, so
"one model, cannot drift apart" was true of the evidence and false of the
document. Both now read ``deliverables/report_sections.json``, and this builder
refuses to write a file missing any section the contract requires.

Under each central claim, Results separates the five narrative facets —
observed result (anchored by the verbatim quote), authors' interpretation,
reviewer inference, contradiction, evidence gap — from
``deliverables/claim_narratives.jsonl``. Reviewer inference is always rendered
under its own label; a statement that neither cites evidence nor declares itself
an inference aborts the build.

    python build_review.py --root "$RUN"
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from quote_display import (  # noqa: E402
    anchor_quote_for_display, anchor_text_unclean, caption_for_display,
)
from infographic_spec import image_failure, image_path  # noqa: E402
from report_model import (  # noqa: E402
    FIGURE_CAPTION_MAX_CHARS,
    INFERENCE_LABEL,
    NARRATIVE_FACET_LABEL,
    SECTION_KEYS,
    SECTION_TITLE,
    build_model,
    figure_caption_prefix,
    load_contract,
    missing_markdown_sections,
    searched_through,
    section_placeholder,
    readable_figure_locator,)
from synthesis_panel import (  # noqa: E402
    assert_panel_matches_claims, panel_caption, render_panel,
)


def _cited_sources_md(evidence_ids: list[str],
                      evidence_citations: dict | None) -> str:
    """Cited evidence rows as a de-duplicated author-year list.

    Mirrors ``build_pdf._citation_markup`` exactly — the two deliverables must
    attribute a sentence identically, and the shipped pair printed raw
    ``E-648e8fe191f72194`` hashes in both.
    """
    if not evidence_ids:
        return ""
    lookup = evidence_citations or {}
    seen: list[str] = []
    for eid in evidence_ids:
        entry = lookup.get(eid)
        if not entry or not entry.get("citation"):
            rendered = eid
        else:
            index = entry.get("reference_index")
            label = (f"{entry['citation']} [{index}]" if index
                     else entry["citation"])
            url = entry.get("url")
            rendered = f"[{label}]({url})" if url else label
        if rendered not in seen:
            seen.append(rendered)
    return ", ".join(seen)


def _statement_line(statement: dict, inference_label: str, *,
                    heading: str = "", number: int | None = None,
                    suppress_inference_marker: bool = False,
                    evidence_citations: dict | None = None) -> str:
    """One narrative statement, with its attribution visible on the same line.

    The sources are printed, not just validated: a reader who wants to check a
    sentence should not have to guess which papers it rests on. An un-cited
    statement is legal only when it declares itself an inference, and it then
    carries the label — never bare prose sitting beside a quote.
    """
    line = f"{number}. " if number else ""
    if heading:
        line += f"**{heading}.** "
    line += statement["text"]
    cites = _cited_sources_md(statement["evidence_ids"], evidence_citations)
    if cites:
        line += f" [{cites}]"
    if statement["inference"] and not suppress_inference_marker:
        line += f" *[{inference_label}]*"
    if statement.get("evidence_qualification") == "secondary/indirect":
        line += " *[secondary/indirect evidence]*"
    if statement.get("no_qualifying_anchor"):
        line += " *[no qualifying verbatim anchor retained]*"
    return line


def _facet_md(key: str, narrative: dict, inference_label: str,
              evidence_citations: dict | None = None) -> list[str]:
    """A labelled narrative facet, or nothing when the run authored none."""
    statement = (narrative or {}).get(key)
    if not statement:
        return []
    return [_statement_line(
        statement, inference_label, heading=NARRATIVE_FACET_LABEL[key],
        # The heading already says "Reviewer inference"; a second marker on the
        # same line is noise, not clarity.
        suppress_inference_marker=(key == "reviewer_inference"),
        evidence_citations=evidence_citations), ""]


def _section_md(model: dict, key: str) -> list[str]:
    """A prose section's statements, or an explicit not-supplied note."""
    statements = (model.get("sections") or {}).get(key) or []
    if not statements:
        return [f"_{section_placeholder(key)}_", ""]
    label = model.get("inference_label") or INFERENCE_LABEL
    out: list[str] = []
    for i, statement in enumerate(statements, 1):
        out += [_statement_line(statement, label,
                                number=i if key == "next_steps" else None,
                                evidence_citations=model.get("evidence_citations")),
                ""]
    return out


def _anchor_md(anchor: dict) -> list[str]:
    """A quote block plus its attribution. Verbatim quote, never paraphrase.

    Same line as the PDF renders: author-year, journal, locator, stance. The
    internal ``block_id`` stays in evidence.jsonl and grounded_quotes.json,
    where a machine reads it — it is not a citation.
    """
    cite = str(anchor.get("citation") or "").strip() or str(
        anchor.get("doi") or anchor.get("paper_id") or "source")
    index = anchor.get("reference_index")
    label = f"{cite} [{index}]" if index else cite
    url = anchor.get("url") or ""
    head = f"[{label}]({url})" if url else label
    bits = [head]
    if anchor.get("journal"):
        bits[0] += f", {anchor['journal']}"
    locator = _locator_md(anchor)
    if locator:
        bits.append(locator)
    bits.append(
        " · ".join(filter(None, (
            str(anchor.get("publication_type") or "").replace("_", " "),
            str(anchor.get("anchor_depth") or "").replace("_", " "),
            str(anchor.get("claim_relationship") or "").replace("_", " "),
            str(anchor.get("stance") or ""),
        )))
    )
    quote = anchor_quote_for_display(anchor, FIGURE_CAPTION_MAX_CHARS)
    return [f"> “{quote}”", "", "> — " + " · ".join(bits), ""]


def _locator_md(anchor: dict) -> str:
    """"page 1 · Abstract" -> "Abstract, p. 1"; see build_pdf._locator_text."""
    raw = str(anchor.get("source_locator") or "").strip()
    if not raw:
        return ""
    page, sections = "", []
    for part in (p.strip() for p in raw.split("·")):
        if not part:
            continue
        match = re.fullmatch(r"pages?\s*([0-9ivxlcIVXLC]+)", part, re.IGNORECASE)
        if match:
            page = match.group(1)
        else:
            readable = readable_figure_locator(part)
            if readable:
                sections.append(readable)
    body = ", ".join(sections)
    if page:
        return f"{body}, p. {page}" if body else f"p. {page}"
    return body


def _figure_md(fig: dict, rel_dir: str, prefix: str = "Figure") -> list[str]:
    caption = caption_for_display(fig.get("caption") or "",
                                  max_chars=FIGURE_CAPTION_MAX_CHARS)
    src = f"[source]({fig['url']})" if fig.get("url") else ""
    grounds = ", ".join(fig.get("claim_display_ids") or fig.get("claims") or [])
    line = f"*{prefix} {fig['report_number']}. {fig['citation']}, {fig['label']}"
    role = str(fig.get("role") or "primary_data")
    role_label = {
        "primary_data": "primary-data figure",
        "source_model": "source mechanism/model; illustrative only",
        "review_context": "review/context figure; illustrative only",
    }.get(role, role.replace("_", " "))
    if grounds:
        verb = "grounds" if role == "primary_data" else "illustrates"
        line += f" — {verb} {grounds}"
    line += f" — {role_label}"
    line += ".*"
    note = str(fig.get("provenance_note") or "").strip()
    if note:
        line += f" _{note}_"
    if caption:
        line += f" Source caption: “{caption}”"
    rights_notice = str(fig.get("rights_notice") or "").strip()
    if rights_notice:
        line += f" **Rights notice:** {rights_notice}"
    if src:
        line += f" {src}"
    return [f"![{fig['citation']} {fig['label']}]({rel_dir}/{fig['image']})", "", line, ""]


def render(model: dict, panel_rel: str | None,
           infographic_rel: str | None = None) -> str:
    out: list[str] = []
    stats = model.get("stats") or {}

    out += [f"# {model['title']}", ""]
    if model.get("question"):
        out += [f"**Question.** {model['question']}", ""]
    meta = [
        f"Mode: {model['mode']}",
        f"{stats.get('papers_full_text', '?')} retrieved full texts",
        f"{len(model['claims'])} claims",
        f"{sum(len(c['supporting']) + len(c['contradicting']) for c in model['claims'])}"
        " verbatim quotes",
        f"{len(model['figures'])} figures reproduced from source papers",
    ]
    searched = searched_through(model)
    if searched:
        meta.append(f"literature searched through {searched}")
    out += [" · ".join(meta), ""]

    if infographic_rel:
        out += [f"![Infographic]({infographic_rel})", "",
                "*Infographic. Three-panel visual summary drawn from the "
                "evidence in this review.*", ""]

    # --- Summary + evidence-axis synthesis table --------------------------
    out += ["## Summary", ""]
    out += ["### Evidence-axis synthesis", ""]
    out += ["| Evidence axis | Bottom line | Strongest support | Sources |",
            "|---|---|---|---|"]
    for row in model["synthesis_table"]:
        # Hyperlinked, like every other citation — this cell used to be the one
        # place an author-year rendered as dead text.
        sources = ", ".join(
            (f"[{s['citation']}"
             + (f" [{s['reference_index']}]" if s.get("reference_index") else "")
             + f"]({s['url']})") if s.get("url")
            else s["citation"]
            for s in row["sources"]) or "—"
        # Say when these are the fallback secondary sources, so the Markdown and
        # the PDF cannot disagree about what a citation in this cell means.
        if sources != "—" and row.get("sources_kind") == "secondary":
            sources += " *(secondary / framing)*"
        tier = row["support_label"]
        if row.get("n_claims", 0) > 1:
            tier += f" (strongest of {row['n_claims']} claims on this axis)"
        out.append(
            f"| {row.get('axis_label') or row['axis']} | {row['bottom_line']} | "
            f"{tier} | {sources} |"
        )
    out += ["", "*Table 1. Evidence-axis synthesis. Support tiers are computed "
            "deterministically from the accepted evidence rows.*", ""]

    # See build_pdf: a conference-level or registry-level result that bears on the
    # conclusion belongs in the summary, labelled as ungrounded — not buried in
    # Conclusions as an unattributed inference, which is where a shipped report
    # put the phase 3 endpoint miss that was the most important fact it had.
    if (model.get("sections") or {}).get("key_findings"):
        out += [f"### {SECTION_TITLE['key_findings']}", ""]
        out += _section_md(model, "key_findings")

    if (model.get("sections") or {}).get("external_findings"):
        out += [f"### {SECTION_TITLE['external_findings']}", "",
                "Reported here because they bear on the conclusion, and marked as "
                "outside the grounded corpus: each is a conference abstract, trial "
                "registry entry or announcement with no retrievable full text, so "
                "no verbatim anchor exists for it.", ""]
        out += _section_md(model, "external_findings")

    # --- Introduction / Methods ---------------------------------------------
    for key in ("introduction", "methods"):
        out += [f"## {SECTION_TITLE[key]}", ""] + _section_md(model, key)

    if model.get("corpus_flow"):
        flow = " → ".join(
            f"{row['state'].replace('_', ' ')} {row['count']}"
            for row in model["corpus_flow"]
        )
        out += ["### Corpus completeness", "", flow + ".", ""]
        classifications = (
            (model.get("corpus_ledger") or {}).get("retrieval_classification") or {}
        )
        if classifications:
            out += [
                "Unretrieved full texts: "
                + ", ".join(
                    f"{count} {kind.replace('_', ' ')}"
                    for kind, count in sorted(classifications.items())
                )
                + ".",
                "",
            ]

    # --- Results -------------------------------------------------------------
    #
    # Facet order is fixed by report_model.NARRATIVE_FACETS and the verbatim
    # quote stays the anchor of the observed result: the quote follows the
    # stated finding, so the reader sees what was measured and then the exact
    # sentence it came from. A claim with no authored narrative renders exactly
    # as it did before the narrative artifacts existed.
    inference_label = model.get("inference_label") or INFERENCE_LABEL
    cites = model.get("evidence_citations")
    figure_prefix = figure_caption_prefix(model.get("contract"))
    placed_figures: set[int] = set()
    out += ["## Results", ""]
    current_axis = None
    for claim in model["claims"]:
        if claim["cluster"] != current_axis:
            current_axis = claim["cluster"]
            out += [f"### {claim.get('cluster_label') or current_axis}", ""]
        out += [f"#### {claim.get('display_id') or claim['claim_id']}. "
                f"{claim['claim_text']}", ""]
        out += [f"**Support:** {claim['support_label']}"
                + (f" · scope: {claim['scope']}" if claim.get("scope") else ""), ""]
        narrative = claim.get("narrative") or {}
        out += _facet_md("observed_result", narrative, inference_label, cites)
        shown = 0
        for anchor in claim["supporting"]:
            if anchor_text_unclean(anchor):
                continue
            out += _anchor_md(anchor)
            shown += 1
        out += _facet_md("authors_interpretation", narrative, inference_label, cites)
        out += _facet_md("reviewer_inference", narrative, inference_label, cites)
        if narrative.get("contradiction"):
            out += _facet_md("contradiction", narrative, inference_label, cites)
        elif claim["contradicting"]:
            out += ["**Contradicting / countervailing evidence:**", ""]
        for anchor in claim["contradicting"]:
            if anchor_text_unclean(anchor):
                continue
            out += _anchor_md(anchor)
            shown += 1
        if not shown:
            out += ["_No clean verbatim anchor available for this claim._", ""]
        out += _facet_md("evidence_gap", narrative, inference_label, cites)
        for fig in claim["figures"]:
            # Embedded once, cross-referenced afterwards — see build_pdf.
            if fig["report_number"] in placed_figures:
                out += [f"_Also grounded by {figure_prefix} "
                        f"{fig['report_number']} ({fig['citation']}, "
                        f"{fig['label']}), shown above._", ""]
                continue
            placed_figures.add(fig["report_number"])
            out += _figure_md(fig, "figures_cited", figure_prefix)

    # --- Conclusions ---------------------------------------------------------
    out += [f"## {SECTION_TITLE['conclusions']}", ""] + _section_md(
        model, "conclusions")

    # --- Limitations & evidence gaps ----------------------------------------
    out += [f"## {SECTION_TITLE['limitations']}", ""]
    notes = model.get("coverage_notes") or []
    if notes:
        out += [f"- {note}" for note in notes] + [""]
        if (model.get("sections") or {}).get("limitations"):
            out += _section_md(model, "limitations")
    else:
        out += _section_md(model, "limitations")

    # --- Figures -----------------------------------------------------------
    out += ["## Figures", ""]
    if model["figures"]:
        out += [f"{len(model['figures'])} figures reproduced from "
                f"{len({f['paper_id'] for f in model['figures']})} cited "
                "papers, each shown under the claim it grounds. Every one is a "
                "crop of the published figure, not a redrawn chart.", ""]
        if any(fig.get("included_at_user_direction") for fig in model["figures"]):
            out += ["Some figures were included at the user's explicit direction "
                    "without a recorded reuse-clearing licence; those figures are "
                    "labeled individually below.", ""]
        for fig in model["figures"]:
            claims = ", ".join(fig.get("claim_display_ids") or fig["claims"])
            out.append(f"- **{figure_prefix} {fig['report_number']}** — "
                       f"{fig['citation']}, {fig['label']}"
                       + (f" (grounds {claims})" if claims else "")
                       + (f" — [source]({fig['url']})" if fig.get("url") else ""))
        out.append("")
    else:
        out += ["_No paper figures were exported for this run._", ""]
    if panel_rel:
        out += [f"![Synthesis panel]({panel_rel})", "", f"*{panel_caption(model)}*", ""]

    # --- Next steps ----------------------------------------------------------
    out += [f"## {SECTION_TITLE['next_steps']}", ""] + _section_md(
        model, "next_steps")

    # --- Paper-level accountability -----------------------------------------
    if model.get("paper_accountability"):
        out += ["## Corpus accountability", "",
                "Every selected paper is listed, including retrieved papers "
                "that yielded no accepted grounding evidence.", "",
                "| Selected paper | Retrieval | Parse | Evidence | Report use |",
                "|---|---|---|---|---|"]
        for row in model["paper_accountability"]:
            title = str(row.get("title") or row.get("paper_id") or "").replace("|", "\\|")
            retrieval = (
                "retrieved" if row.get("retrieved")
                else str(row.get("retrieval_kind") or "not attempted").replace("_", " ")
            )
            parse = str(row.get("parse_quality") or "—").replace("_", " ")
            evidence = (
                f"{int(row.get('accepted_evidence_count') or 0)} accepted; "
                f"{int(row.get('rejected_adjudication_count') or 0)} rejected"
            )
            use = (
                ("cited" if row.get("cited") else "consulted, not cited")
                + f"; {int(row.get('exported_figure_count') or 0)} figure(s)"
            )
            out.append(f"| {title} | {retrieval} | {parse} | {evidence} | {use} |")
        out.append("")

    # --- References --------------------------------------------------------
    out += ["## References", ""]
    for ref in model["references"]:
        title = f"[{ref['title']}]({ref['url']})" if ref["url"] else ref["title"]
        journal = f" {ref['journal']}." if ref["journal"] else ""
        doi = f" doi:{ref['doi']}" if ref["doi"] else ""
        out.append(f"{ref['index']}. {ref['authors']} ({ref['year']}). {title}."
                   f"{journal}{doi}".rstrip())
    out.append("")

    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--contract", default=None,
                    help="defaults to templates/report_contract.json")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    contract = load_contract(args.contract)
    # An explicitly named contract that cannot be read must not silently become
    # "no contract" — that turns the section gate off and reports success.
    if args.contract and not contract:
        print(f"FAIL: could not read contract: {args.contract}")
        return 1
    from reconcile_run import refresh as reconcile

    _receipt, reconciliation_failures = reconcile(root, write=True)
    if reconciliation_failures:
        for failure in reconciliation_failures:
            print(f"FAIL: reconciliation: {failure}")
        return 1
    model = build_model(root, contract)

    # ``build_model`` has already dropped any manifest figure whose image is not
    # on disk, so this list and the PDF's are the same list. Say so out loud —
    # a silently shorter figure list is the defect this whole module exists for.
    for missing in model.get("figures_missing") or []:
        print(f"WARN: manifest figure {missing.get('paper_id')}/"
              f"{missing.get('figure_id')} claims status=exported but its image "
              f"is not on disk ({missing.get('resolved') or missing.get('image')})"
              " — it is excluded from BOTH deliverables", file=sys.stderr)

    problems = (assert_panel_matches_claims(model)
                + list(model.get("reference_errors") or [])
                + list(model.get("stale_derived") or [])
                + list(model.get("narrative_errors") or [])
                + list(model.get("coverage_errors") or []))
    for failure in problems:
        print(f"FAIL: {failure}")
    if problems:
        return len(problems)

    deliverables = root / "deliverables"
    out_path = pathlib.Path(args.out).resolve() if args.out else deliverables / "review.md"

    visual = contract.get("visual_abstract") or {}
    infographic = image_path(root, contract)
    infographic_required = model.get("mode") in set(
        visual.get("required_modes") or [])
    infographic_rel = None
    if infographic_required or infographic.exists():
        failure = image_failure(infographic)
        if failure and infographic_required:
            print(f"FAIL: {failure}")
            return 1
        if failure:
            print(f"WARN: {failure}; omitting optional infographic",
                  file=sys.stderr)
        else:
            infographic_rel = os.path.relpath(infographic, out_path.parent)

    panel_path = render_panel(model, deliverables / "synthesis_panel.png")
    panel_rel = panel_path.name if panel_path else None

    # Gate the Markdown against the SAME section list the PDF is held to,
    # before it is written. Nothing checked review.md's shape at all, which is
    # how it shipped with four of the contract's sections simply absent.
    text = render(model, panel_rel, infographic_rel)
    missing = missing_markdown_sections(text, contract)
    for failure in missing:
        print(f"FAIL: {failure}")
    if missing:
        return len(missing)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    # Keep the cold-start packet fresh at a natural boundary. A context can die
    # at any moment and cannot warn us first, so the packet is regenerated
    # whenever the run reaches a state worth resuming from. Best-effort: a
    # failure here must never cost a built deliverable.
    try:
        from run_state import write_context
        write_context(root)
    except Exception as exc:  # noqa: BLE001 - the deliverable is what matters
        print(f"WARN: could not refresh context packet: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)

    print(f"BUILD-REVIEW: claims={len(model['claims'])} "
          f"figures={len(model['figures'])} "
          f"sections={sum(1 for k in SECTION_KEYS if (model.get('sections') or {}).get(k))}"
          f"/{len(SECTION_KEYS)} "
          f"narratives={sum(1 for c in model['claims'] if c.get('narrative'))}"
          f"/{len(model['claims'])} "
          f"infographic={'yes' if infographic_rel else 'no'} "
          f"panel={'yes' if panel_rel else 'no'} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
