"""Structural guarantees: the same skill must not ship differently shaped reports.

Two `broad` runs of this skill produced documents that differed in kind, not
degree — one separated all 14 of its claims into the five narrative facets, the
other authored none and shipped a bare quote catalogue; one carried a
safety/contradiction axis, the other looked only for confirmation. Both passed
every gate.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from report_model import build_model, coverage_notes, load_contract


def test_production_clis_expose_only_native_biomni_reasoning():
    scripts = pathlib.Path(__file__).resolve().parent.parent / "scripts"
    for script in ("evidence_first.py", "batch_tasks.py"):
        result = subprocess.run(
            [sys.executable, str(scripts / script), "--help"],
            capture_output=True,
            text=True,
            check=True,
        )
        help_text = result.stdout.lower()
        for forbidden in ("openai", "gemini", "ollama", "api key", "run-direct"):
            assert forbidden not in help_text


# --- narratives are not optional in deep/broad ------------------------------

def test_missing_narrative_artifact_fails_a_broad_run(make_run):
    run = make_run(with_narratives=False, mode="broad")
    model = build_model(run, load_contract())
    assert any("claim_narratives.jsonl" in e for e in model["narrative_errors"])


def test_missing_narrative_artifact_is_fine_in_quick(make_run):
    """`quick` is not in the contract's required_modes; a short review may be a
    quote catalogue."""
    run = make_run(with_narratives=False, mode="quick")
    model = build_model(run, load_contract())
    assert model["narrative_errors"] == []


def test_builders_refuse_a_broad_run_with_no_narrative(make_run, tmp_path):
    import build_pdf

    run = make_run(with_narratives=False, mode="broad")
    with pytest.raises(SystemExit) as excinfo:
        build_pdf.main(["--root", str(run), "--out", str(tmp_path / "r.pdf")])
    assert "claim_narratives" in str(excinfo.value)


# --- the review has to have looked for disconfirming evidence ---------------

def _drop_contradiction_axis(run):
    """Rewrite every claim onto a mechanism axis and strip contradicting quotes."""
    claims_path = run / "corpus" / "claims.jsonl"
    rows = [json.loads(line) for line in
            claims_path.read_text().splitlines() if line.strip()]
    for row in rows:
        row["cluster"] = "mech_lipid_bbb"
    claims_path.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    grounded_path = run / "deliverables" / "grounded_quotes.json"
    grounded = json.loads(grounded_path.read_text())
    for entry in grounded.values():
        entry["contradicting_anchors"] = []
    grounded_path.write_text(json.dumps(grounded))
    return run


def test_review_with_no_contradiction_axis_fails(make_run):
    """The APOE review's six axes were all mechanism or efficacy — not one
    covered harm, null results or risk — on a target whose central development
    risk is exactly that."""
    run = _drop_contradiction_axis(make_run(mode="broad"))
    model = build_model(run, load_contract())
    assert model["coverage_errors"]
    assert "safety" in model["coverage_errors"][0]


def test_fixture_with_a_safety_axis_passes(model):
    assert model["coverage_errors"] == []


def test_searched_empty_axis_is_rendered_as_a_known_gap(run_root):
    coverage = run_root / "corpus" / "coverage_matrix.json"
    matrix = json.loads(coverage.read_text())
    row = next(r for r in matrix["axes"] if r["axis"] == "translational_clinical")
    row.update({
        "status": "searched_empty",
        "queries": ["GRN phase 3 clinical trial"],
        "reason": "No qualifying full-text study was retained.",
    })
    coverage.write_text(json.dumps(matrix))

    model = build_model(run_root, load_contract())

    empty = {row["axis"]: row for row in model["searched_empty_axes"]}
    assert empty["translational_clinical"]["reason"].startswith("No qualifying")
    synthesis = {row["axis"]: row for row in model["synthesis_table"]}
    assert synthesis["translational_clinical"]["n_claims"] == 0
    assert synthesis["translational_clinical"]["sources"] == []


def test_uncited_contradiction_requires_explicit_no_anchor_disposition(run_root):
    narratives = run_root / "deliverables" / "claim_narratives.jsonl"
    rows = [json.loads(line) for line in narratives.read_text().splitlines() if line]
    rows[0]["contradiction"] = {
        "text": "A null result was reported but no qualifying quote was retained.",
        "evidence_ids": [],
        "inference": True,
    }
    narratives.write_text("".join(json.dumps(row) + "\n" for row in rows))

    model = build_model(run_root, load_contract())
    assert any("no_qualifying_anchor" in error for error in model["narrative_errors"])

    rows[0]["contradiction"]["no_qualifying_anchor"] = True
    narratives.write_text("".join(json.dumps(row) + "\n" for row in rows))
    model = build_model(run_root, load_contract())
    assert not any("no_qualifying_anchor" in error for error in model["narrative_errors"])


def test_conclusion_must_map_each_proposition_to_its_atomic_claim(run_root):
    sections_path = run_root / "deliverables" / "report_sections.json"
    sections = json.loads(sections_path.read_text())
    sections["conclusions"] = [{
        "text": "The target has direct mechanistic and translational support.",
        "evidence_ids": ["E-alpha01"],
    }]
    sections_path.write_text(json.dumps(sections))

    errors = build_model(run_root, load_contract())["narrative_errors"]

    assert any("supplies no claim_ids" in error for error in errors)


def test_indirect_only_conclusion_requires_explicit_qualification(run_root):
    evidence_path = run_root / "evidence" / "evidence.jsonl"
    evidence = [
        json.loads(line) for line in evidence_path.read_text().splitlines() if line
    ]
    row = next(item for item in evidence if item["evidence_id"] == "E-alpha01")
    row["evidence_kind"] = "secondary"
    evidence_path.write_text("".join(json.dumps(item) + "\n" for item in evidence))
    sections_path = run_root / "deliverables" / "report_sections.json"
    sections = json.loads(sections_path.read_text())
    sections["conclusions"] = [{
        "text": "Prior literature indirectly supports the mechanism.",
        "evidence_ids": ["E-alpha01"],
        "claim_ids": ["C-001"],
    }]
    sections_path.write_text(json.dumps(sections))

    model = build_model(run_root, load_contract())
    assert any("relies only on secondary/indirect" in error
               for error in model["narrative_errors"])

    sections["conclusions"][0]["qualified"] = True
    sections_path.write_text(json.dumps(sections))
    model = build_model(run_root, load_contract())
    assert not any("relies only on secondary/indirect" in error
                   for error in model["narrative_errors"])
    assert model["sections"]["conclusions"][0]["evidence_qualification"] == (
        "secondary/indirect"
    )


def test_a_contradicting_quote_anywhere_satisfies_coverage(make_run):
    """The requirement is that the review looked, not that it named an axis a
    particular way."""
    run = _drop_contradiction_axis(make_run(mode="broad"))
    grounded_path = run / "deliverables" / "grounded_quotes.json"
    grounded = json.loads(grounded_path.read_text())
    first = next(iter(grounded.values()))
    first["contradicting_anchors"] = [{
        "quote": "PGRN loss does not exacerbate TDP-43 pathology in these mice.",
        "paper_id": "10.1000/gamma", "stance": "contradicts",
        "evidence_kind": "primary", "source_locator": "page 4 · Results",
    }]
    grounded_path.write_text(json.dumps(grounded))
    assert build_model(run, load_contract())["coverage_errors"] == []


# --- limitations are measured, not just asserted ----------------------------

def test_coverage_notes_report_the_retrieval_shortfall(run_root):
    """One review acquired 17 of 25 selected papers, another 18 of 30 with 12
    paywalled; both mentioned it only in a Methods sentence and a Next step."""
    stats_path = run_root / "deliverables" / "review_stats.json"
    stats = json.loads(stats_path.read_text())
    stats.update({"claims_drafted": 5})
    stats_path.write_text(json.dumps(stats))

    def rows(path):
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    refs_path = run_root / "corpus" / "references.jsonl"
    refs = rows(refs_path) + [
        {"paper_id": f"missing-{index}", "title": f"Missing {index}"}
        for index in range(1, 4)
    ]
    refs_path.write_text("".join(json.dumps(row) + "\n" for row in refs))
    records_path = run_root / "corpus" / "records.jsonl"
    records_path.write_text("".join(json.dumps(row) + "\n" for row in refs))
    misses_path = run_root / "fulltext" / "not_retrieved.jsonl"
    misses_path.write_text("".join(
        json.dumps({
            "paper_id": f"missing-{index}",
            "_not_retrieved_kind": "paywalled",
            "_not_retrieved_reason": "confirmed paywall",
        }) + "\n" for index in range(1, 4)
    ))
    from corpus_ledger import refresh
    refresh(run_root)

    model = build_model(run_root, load_contract())
    notes = coverage_notes(run_root, model)
    assert any("3 of 6 selected papers" in n for n in notes)
    assert any("2 drafted claim" in n for n in notes)


def test_coverage_notes_report_figures_passed_over(run_root):
    from export_figures import export_cited_figures

    export_cited_figures(run_root)
    model = build_model(run_root, load_contract())
    notes = coverage_notes(run_root, model)
    assert any(
        "figure crops are shown" in n
        and "unique crops" in n
        and "claim–figure pairs" in n
        for n in notes
    )


# --- the delivered document ---------------------------------------------------

@pytest.fixture
def built(run_root, tmp_path):
    import build_pdf
    import build_review
    from export_figures import export_cited_figures

    export_cited_figures(run_root)
    out = tmp_path / "report.pdf"
    assert build_pdf.main(["--root", str(run_root), "--out", str(out)]) == 0
    assert build_review.main(["--root", str(run_root)]) == 0
    return out, (run_root / "deliverables" / "review.md").read_text()


def _text(path) -> str:
    try:
        return subprocess.run(["pdftotext", "-layout", str(path), "-"],
                              capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("pdftotext (poppler) not available")


def test_pdf_has_contents_and_limitations(built):
    pdf, _ = built
    text = _text(pdf)
    assert "Contents" in text
    assert "Limitations & evidence gaps" in text


def test_markdown_has_the_same_sections_as_the_pdf(built):
    pdf, markdown = built
    pdf_text = _text(pdf)
    for section in load_contract()["required_sections"]:
        assert section in pdf_text, f"{section} missing from PDF"
        assert section in markdown, f"{section} missing from review.md"


def test_pdf_shows_a_search_date(built):
    """/CreationDate is pinned to a fixed epoch for byte-determinism, so without
    this the document carries no indication of when the literature was read."""
    pdf, _ = built
    assert "literature searched through 2026-07-27" in " ".join(_text(pdf).split())


def test_synthesis_panel_does_not_disclaim_its_own_bars(model):
    """The old caption ended "read the tiers, not the totals, and do not treat
    this as a quantitative measure of evidence strength" — correct about claim
    counts, and therefore an argument for plotting something else."""
    from synthesis_panel import panel_caption

    caption = panel_caption(model)
    assert "independent primary studies" in caption
    assert "do not treat this as a quantitative measure" not in caption


def test_markdown_lists_every_selected_paper_before_references(model):
    from build_review import render

    markdown = render(model, None)

    assert "## Corpus accountability" in markdown
    accountability = markdown.index("## Corpus accountability")
    references = markdown.index("## References")
    assert accountability < references
    for paper in model["paper_accountability"]:
        title = str(paper.get("title") or paper["paper_id"]).replace("|", "\\|")
        assert title in markdown


# --- the figure caption prefix cannot be shortened ---------------------------

def test_figure_prefix_survives_a_quoted_source_label(built):
    """verify_pdf_assets counts paper figures by finding "<prefix> N" in the
    flattened PDF text, and a report figure's caption reproduces the SOURCE
    figure's own label. Shortening the prefix to "Figure" made the quoted
    "Figure 3 Increased lysosomal biogenesis..." count as a third report figure,
    so a two-figure report claimed three. Tried, measured, reverted."""
    import re

    from report_model import figure_caption_prefix, load_contract

    pdf, _ = built
    prefix = figure_caption_prefix(load_contract())
    text = _text(pdf).lower()
    counted = set(re.findall(rf"{re.escape(prefix.lower())}\s*(\d+)", text))
    assert len(counted) == 2, (
        f"prefix {prefix!r} counted {sorted(counted)} report figures; the "
        "fixture embeds 2. A prefix that collides with a source figure label "
        "inflates the count the figure gate trusts.")
    # And the source label really is present, so the test is exercising it.
    assert "figure 3 increased lysosomal biogenesis" in text


# --- the locator gate backstops the Front-matter defect ----------------------

def _locator_failures(run):
    """Run the contract's locator check directly; the CLI additionally needs a
    PDF and this check only reads evidence.jsonl."""
    from verify_report_contract import _check_locators

    failures: list[str] = []
    _check_locators(load_contract(), run, failures, [])
    return failures


def test_front_matter_locator_fails_the_contract_gate(make_run):
    """Ten abstract sentences shipped located at "page 1 · Front matter".
    scripts/section_labels.py fixes the cause; the contract forbids the value for
    an evidence row so a regression cannot ship silently. Front matter is titles,
    author lists and affiliations — none of which is evidence of anything."""
    failures = _locator_failures(make_run(front_matter_locator=True))
    assert any("unusable section label" in f for f in failures)


def test_clean_locators_pass_the_gate(run_root):
    assert _locator_failures(run_root) == []


# --- the report must be dated, or say it is not ------------------------------

@pytest.mark.parametrize("field", [
    "searched_through", "run_started_utc", "started_utc", "created_at",
])
def test_search_date_found_in_any_manifest_field(run_root, field):
    """Neither shipped report carried a date ANYWHERE: no run_started_utc in the
    manifest, and /CreationDate is pinned to a fixed epoch for determinism."""
    from report_model import build_model, load_contract, searched_through

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for key in ("searched_through", "run_started_utc", "started_utc",
                "created_at"):
        manifest.pop(key, None)
    manifest[field] = "2026-07-28T11:00:00Z"
    manifest_path.write_text(json.dumps(manifest))
    assert searched_through(build_model(run_root, load_contract())) == "2026-07-28"


def test_undated_run_says_so_in_limitations(run_root):
    """Absence used to be silent: the meta line simply omitted the date."""
    from report_model import build_model, coverage_notes, load_contract

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for key in ("searched_through", "run_started_utc"):
        manifest.pop(key, None)
    manifest_path.write_text(json.dumps(manifest))

    model = build_model(run_root, load_contract())
    notes = coverage_notes(run_root, model)
    assert any("undated" in n for n in notes)


def test_a_bare_epoch_is_not_printed_as_a_date(run_root):
    from report_model import build_model, load_contract, searched_through

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["run_started_utc"] = "1769000000"
    manifest.pop("searched_through", None)
    manifest_path.write_text(json.dumps(manifest))
    assert searched_through(build_model(run_root, load_contract())) == ""


def test_grounded_external_finding_must_be_key_finding(run_root):
    evidence = [json.loads(line) for line in
                (run_root / "evidence" / "evidence.jsonl").read_text().splitlines()
                if line.strip()]
    sections_path = run_root / "deliverables" / "report_sections.json"
    sections = json.loads(sections_path.read_text())
    sections["external_findings"] = [{
        "text": "This result is grounded in retained full text.",
        "evidence_ids": [evidence[0]["evidence_id"]],
    }]
    sections_path.write_text(json.dumps(sections))

    model = build_model(run_root, load_contract())

    assert any("Move it to key_findings" in error
               for error in model["narrative_errors"])
