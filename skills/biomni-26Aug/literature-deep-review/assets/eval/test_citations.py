"""Citations, reference ordering, and reference metadata hygiene.

Each test names the shipped defect it prevents.
"""
from __future__ import annotations

import re

import pytest

from report_model import (
    _citation,
    clean_journal,
    clean_title,
    display_axis,
    doi_year,
    format_authors,
)


# --- inline citation form ---------------------------------------------------

def test_anchor_carries_author_year_not_doi(model):
    """The shipped locator line read "10.1002/trc2.12452:S:0 · Abstract ·
    supports/primary · 10.1002/trc2.12452" — an internal block id standing in
    for a citation, and the DOI printed twice."""
    anchors = [a for claim in model["claims"] for a in claim["supporting"]]
    assert anchors
    for anchor in anchors:
        assert re.fullmatch(r"[A-Z][\w-]+(?: (?:et al\.|and [A-Z][\w-]+))? \d{4}",
                            anchor["citation"]), anchor["citation"]
        assert not anchor["citation"].startswith("10.")


def test_anchor_links_to_reference_entry(model):
    for claim in model["claims"]:
        for anchor in claim["supporting"]:
            assert anchor["reference_index"] in {r["index"]
                                                 for r in model["references"]}


def test_narrative_evidence_ids_resolve_to_citations(model):
    """Prose cited raw row hashes: "[E-648e8fe191f72194, E-e1f59bcaeec16378]"."""
    citations = model["evidence_citations"]
    assert citations
    for entry in citations.values():
        assert entry["citation"] and not entry["citation"].startswith("E-")
        assert entry["reference_index"]


@pytest.mark.parametrize("authors,expected", [
    ("Rademakers R", "Rademakers 2012"),
    ("Finch N; Baker M", "Finch and Baker 2012"),
    ("Ward MP; Carter LP; Huang JY", "Ward et al. 2012"),
])
def test_et_al_only_when_true(authors, expected):
    assert _citation({"authors": authors, "year": "2012"}) == expected


# --- reference list ---------------------------------------------------------

def test_references_follow_rendered_claim_order(model):
    """"First-citation order" walked evidence.jsonl row order, so the shipped
    list opened with a paper first cited by the second-to-last claim while the
    paper grounding C-001 sat at number 10."""
    first_paper_per_claim = [
        claim["supporting"][0]["paper_id"] for claim in model["claims"]
        if claim["supporting"]]
    seen: list[str] = []
    for pid in first_paper_per_claim:
        if pid not in seen:
            seen.append(pid)
    assert [r["paper_id"] for r in model["references"]] == seen


def test_reference_indices_are_contiguous(model):
    assert [r["index"] for r in model["references"]] == list(
        range(1, len(model["references"]) + 1))


def test_year_contradicting_its_doi_is_an_error(tmp_path):
    """A shipped reference read "Xia et al. (2021)" for
    10.1038/s41467-024-49028-z, a 2024 paper, and the wrong year propagated
    into the synthesis table."""
    import fixture_run
    from report_model import _reference_list

    refs = [{"paper_id": "p1", "doi": "10.1038/s41467-024-49028-z",
             "year": "2021", "title": "T", "authors": "Xia Z"}]
    evidence = [{"paper_id": "p1", "claim_id": "C-001", "stance": "supports"}]
    _, errors = _reference_list(refs, evidence, ["C-001"])
    assert any("2021" in e and "2024" in e for e in errors)
    assert fixture_run  # keeps the import meaningful for collection order


@pytest.mark.parametrize("reverse", [False, True])
def test_published_version_wins_all_citation_identity_fields(reverse):
    """An exact-title preprint/journal merge must never create a hybrid record.

    The SLC33A1 report printed the Nature Cell Biology journal name beside the
    bioRxiv DOI and called the already-published result a preprint.
    """
    from references_to_corpus import merge_records

    preprint = {
        "paper_id": "10.64898/2026.02.01.703113",
        "id_type": "doi",
        "doi": "10.64898/2026.02.01.703113",
        "title": "SLC33A1 exports oxidized glutathione",
        "year": "2026",
        "journal": "bioRxiv (preprint)",
        "url": "https://doi.org/10.64898/2026.02.01.703113",
        "pdf_url": "https://example.org/preprint.pdf",
        "is_preprint": True,
        "publication_role": "preprint",
    }
    published = {
        "paper_id": "10.1038/s41556-026-01922-y",
        "id_type": "doi",
        "doi": "10.1038/s41556-026-01922-y",
        "pmid": "fixture-pmid",
        "title": "SLC33A1 exports oxidized glutathione",
        "year": "2026",
        "journal": "Nature Cell Biology",
        "url": "https://doi.org/10.1038/s41556-026-01922-y",
        "is_preprint": False,
        "publication_role": "primary",
    }
    first, second = (published, preprint) if reverse else (preprint, published)

    merged = merge_records(first, second)

    assert merged["paper_id"] == published["pmid"]
    assert merged["id_type"] == "pmid"
    assert merged["doi"] == published["doi"]
    assert merged["pmid"] == published["pmid"]
    assert merged["url"] == published["url"]
    assert merged["journal"] == published["journal"]
    assert merged["is_preprint"] is False
    assert merged["publication_role"] == "primary"
    assert merged["pdf_url"] == preprint["pdf_url"]


@pytest.mark.parametrize("doi,expected", [
    ("10.1038/s41467-024-49028-z", "2024"),
    ("10.1038/s41586-020-2709-7", "2020"),
    ("10.1093/brain/awn352", ""),      # no year encoded: must not guess
    ("10.1002/alz.13703", ""),
])
def test_doi_year_extraction(doi, expected):
    assert doi_year(doi) == expected


# --- metadata hygiene -------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Cha Yang; Tuancheng Feng; Fenghua Hu", "Yang C, Feng T, Hu F"),
    ("Rojas JC; Wang P; Staffaroni AM; Heller C",
     "Rojas JC, Wang P, Staffaroni AM, et al."),
    ("Zhang, Jian", "Zhang J"),
    ("Elisa Ventura, Giacomo Ducci, Reyes Dominguez-Benot, et al.",
     "Ventura E, Ducci G, Dominguez-Benot R, et al."),
])
def test_author_formats_converge(raw, expected):
    """One shipped list carried three author conventions across 13 entries."""
    assert format_authors(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Brain : a journal of neurology", "Brain"),
    ("Molecular therapy : the journal of the American Society of Gene Therapy",
     "Molecular Therapy"),
    ("Npj Dementia", "npj Dementia"),
    ("Cell reports", "Cell Reports"),
    # A colon that is part of the name, not an NLM subtitle, must survive.
    ("Alzheimer's & Dementia: TRCI", "Alzheimer's & Dementia: TRCI"),
])
def test_journal_names_normalize(raw, expected):
    assert clean_journal(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("A single fibril study reveals ... | Communications Chemistry",
     "A single fibril study reveals ..."),
    ("Homozygous GRN mutations: new insights.", "Homozygous GRN mutations: new insights"),
    ("Silencing brain Apoe - PMC", "Silencing brain Apoe"),
])
def test_title_artifacts_stripped(raw, expected):
    assert clean_title(raw) == expected


# --- axis labels ------------------------------------------------------------

@pytest.mark.parametrize("axis,expected", [
    ("biomarker_engagement", "Biomarkers & target engagement"),
    ("mech_tau_neuroinflammation", "Mechanism: tau & neuroinflammation"),
    ("some_new_axis", "Some New Axis"),
])
def test_axis_identifiers_render_as_english(axis, expected):
    """The shipped headings, table rows and chart x-axis printed the raw
    identifiers, and two of them collided into
    "mechanism_biologybiomarker_engagement"."""
    assert display_axis(axis) == expected


def test_model_exposes_axis_labels(model):
    for claim in model["claims"]:
        assert "_" not in claim["cluster_label"]
    for row in model["synthesis_table"]:
        assert "_" not in row["axis_label"]


# --- every citation is clickable ---------------------------------------------

def _link_uris(pdf_path) -> list[str]:
    from pypdf import PdfReader

    uris: list[str] = []
    for page in PdfReader(str(pdf_path)).pages:
        for annot in (page.get("/Annots") or []):
            action = annot.get_object().get("/A") or {}
            uri = action.get("/URI") if hasattr(action, "get") else None
            if uri:
                uris.append(str(uri))
    return uris


@pytest.fixture
def built_pdf(run_root, tmp_path):
    import build_pdf
    from export_figures import export_cited_figures

    export_cited_figures(run_root)
    out = tmp_path / "report.pdf"
    assert build_pdf.main(["--root", str(run_root), "--out", str(out)]) == 0
    return out


def test_every_cited_reference_is_clickable(built_pdf, model):
    """`references.require_hyperlinks` sat in the contract with nothing reading
    it — a declared requirement no gate enforces is indistinguishable from no
    requirement."""
    targets = " ".join(_link_uris(built_pdf))
    for ref in model["references"]:
        assert ref["doi"] in targets, f"reference {ref['index']} is not clickable"


def test_table_1_sources_are_clickable(built_pdf, model):
    """Table 1 was the one place an author-year rendered as dead text, while the
    same citation under a quote was a link."""
    from build_pdf import _sources_markup

    for row in model["synthesis_table"]:
        assert row["sources"], row["axis"]
        markup = _sources_markup(row["sources"])
        assert "<a href=" in markup, f"{row['axis']} sources are not links"


def test_quote_attribution_and_figure_captions_are_clickable(built_pdf, model):
    from build_pdf import _attribution_markup

    for claim in model["claims"]:
        for anchor in claim["supporting"] + claim["contradicting"]:
            assert "<a href=" in _attribution_markup(anchor)
    # Figures carry a source link too.
    for fig in model["figures"]:
        assert fig["url"], fig["figure_id"]
        assert fig["url"] in " ".join(_link_uris(built_pdf))


def test_hyperlink_gate_catches_an_unlinked_reference(run_root, tmp_path):
    """The gate must fail on a PDF whose citations are text-only, or it is not a
    gate."""
    from verify_report_contract import _check_reference_hyperlinks

    refs = [{"paper_id": "p1", "doi": "10.9999/never-linked", "url": ""}]
    failures: list[str] = []
    _check_reference_hyperlinks({"require_hyperlinks": True}, refs,
                               tmp_path / "absent.pdf", failures, [])
    # No PDF: reported as "not checked", never as a pass.
    assert failures == []

    import build_pdf
    from export_figures import export_cited_figures

    export_cited_figures(run_root)
    pdf = tmp_path / "report.pdf"
    build_pdf.main(["--root", str(run_root), "--out", str(pdf)])
    failures = []
    _check_reference_hyperlinks({"require_hyperlinks": True}, refs, pdf,
                               failures, [])
    assert failures and "no clickable link" in failures[0]


@pytest.mark.parametrize("axis,expected", [
    # The ids two shipped runs actually chose, whose de-snaked fallback produced
    # the grammatical nonsense "Genetic Causal" and "Safety Counter".
    ("genetic_causal", "Genetic & causal evidence"),
    ("safety_counter", "Safety & counter-evidence"),
    ("therapeutic", "Therapeutic strategies"),
    ("mechanism", "Mechanism"),
])
def test_run_invented_axis_ids_render_as_english(axis, expected):
    assert display_axis(axis) == expected
