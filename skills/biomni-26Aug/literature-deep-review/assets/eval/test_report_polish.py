"""Presentation defects found by reading a delivered report.

Each of these shipped. None was caught by the suite, because each lives at the
seam between a correct computation and how a reader construes it: an em dash
that means "no primary study" and reads as "no evidence", an acronym title-cased
into a word no biologist writes, a zero-length bar that looks like a broken
chart, a review schematic presented as primary data.
"""
from __future__ import annotations

import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pytest  # noqa: E402

from figure_selection import is_review_paper, is_schematic  # noqa: E402
from report_model import display_axis  # noqa: E402


# --- axis labels --------------------------------------------------------------

@pytest.mark.parametrize("axis_id,expected", [
    ("biomarker_pgrn", "Biomarker PGRN"),
    ("biomarker_nfl", "Biomarker NfL"),
    ("modality_aav_preclinical", "Modality AAV Preclinical"),
    ("mechanism_tdp43", "Mechanism TDP-43"),
])
def test_acronyms_survive_the_de_snaker(axis_id, expected):
    """A run may invent axis ids, and the de-snaker title-cased them into
    "Biomarker Pgrn", "Biomarker Nfl", "Modality Aav" and "Mechanism Tdp43" —
    printed in the contents, the synthesis table and the chart of a delivered
    report."""
    assert display_axis(axis_id) == expected


def test_ordinary_words_are_still_title_cased():
    assert display_axis("mechanism_lysosome") == "Mechanism Lysosome"


def test_curated_labels_still_win_over_the_fallback():
    assert display_axis("genetics_causality") == "Genetics & causality"


# --- review figures -----------------------------------------------------------

SHIPPED_REVIEW_FIGURE = (
    "Fig. 1 Comparison of patients carrying GRN mutations with rodent and "
    "human-derived models of PGRN deficiency. a In humans, homozygous GRN "
    "mutations are extremely rare and cause CLN11 or FTD syndromes "
    "[6, 37, 88, 118, 119].")


def test_the_review_schematic_that_shipped_is_now_excluded():
    """It reached page 6 of a delivered report as though it were primary data.
    Two independent signals missed it: the caption says "Comparison of ... with"
    rather than "schematic", and its paper carried no study_type."""
    assert is_schematic(SHIPPED_REVIEW_FIGURE)
    assert is_review_paper({}, SHIPPED_REVIEW_FIGURE)


@pytest.mark.parametrize("caption", [
    ("Fig. 2 In vivo assessment of an anti-sortilin antibody in a mouse model "
     "of GRN haploinsufficiency. A Cell lysate sortilin levels and B plasma "
     "PGRN levels from WT and Grn mice."),
    ("Fig. 4 Pharmacokinetics of latozinemab in HVs and aFTD-GRN participants. "
     "A-B Mean SD serum concentrations plotted as a function of time."),
    ("Extended Data Fig. 2 | Reduced lysosomal and neuropathology defects in an "
     "aged FTD-GRN mouse model following PR006 treatment."),
    # A primary figure may cite ONE method source without becoming a review.
    "Fig. 3 Quantification of lipofuscin autofluorescence, as described in [12].",
])
def test_real_primary_figures_still_pass(caption):
    """The tightening must not cost the report its data figures — all four of
    these are reproduced in the delivered report and belong there."""
    assert not is_schematic(caption)
    assert not is_review_paper({}, caption)


def test_study_type_still_decides_when_it_is_recorded():
    assert is_review_paper({"study_type": "systematic review"}, "Fig. 1 Results.")


# --- the Sources cell ---------------------------------------------------------

def test_an_indirect_axis_names_its_secondary_sources(make_run):
    """The cell listed primary studies only, so an axis at indirect/background
    tier rendered as an em dash — and the delivered report showed "GRN
    loss-of-function causes FTD" beside a blank Sources cell, which reads as
    UNSOURCED for a claim carrying three verbatim quotes."""
    from report_model import build_model, load_contract

    model = build_model(make_run(), load_contract())
    for row in model["synthesis_table"]:
        if row["support_state"] == "C1_INDIRECT" and row["n_sources_total"]:
            assert row["sources"], (
                f"axis {row['axis']} has sources but renders none")
            assert row["sources_kind"] == "secondary"


def test_secondary_sources_are_labelled_as_such():
    """Naming a framing statement beside a tier that says "primary" would trade
    one misreading for a worse one."""
    from build_pdf import _sources_markup

    sources = [{"citation": "Almeida et al. 2023", "url": "https://doi.org/10.x"}]
    assert "secondary" in _sources_markup(sources, "secondary")
    assert "secondary" not in _sources_markup(sources, "primary")


def test_an_axis_with_no_sources_at_all_still_shows_a_dash():
    from build_pdf import _sources_markup
    assert _sources_markup([], "secondary") == "—"


# --- cross-references, locators, caption anchors, case reports ----------------

def test_claim_cross_references_keep_canonical_ids():
    """Cross-references must retain the same ID used by machine artifacts."""
    from report_model import _retarget_claim_references

    display = {f"C-{i:03d}": f"C-{i:03d}"
               for i in [1, 2, 3, 5, 8, 40, 63]}
    rows = [{"narrative": {"n": {"text": "the reduction seen in C-040, and "
                                         "apoE is required (see C-063)."}}}]
    unresolved = _retarget_claim_references(rows, {}, display)
    text = rows[0]["narrative"]["n"]["text"]
    assert "C-040" in text and "C-063" in text
    assert unresolved == []


def test_a_reference_to_a_dropped_claim_is_marked_not_renumbered():
    """Pointing the reader at a different, unrelated claim is worse than telling
    them the target is absent."""
    from report_model import _retarget_claim_references

    sections = {"c": [{"text": "claims C-020, C-021 and C-061 rest on it."}]}
    unresolved = _retarget_claim_references([], sections, {"C-001": "C-001"})
    assert sections["c"][0]["text"].count("(not retained)") == 3
    assert unresolved == ["C-020", "C-021", "C-061"]


@pytest.mark.parametrize("raw,expected", [
    ("figure fig6_p12", ""),               # positional; contradicted "Fig 4"
    ("figure fig3_p07", ""),
    ("figure Figure2", "Fig. 2"),
    ("figure Fig4", "Fig. 4"),
    ("figure ExtendedDataFigure2", "Extended Data Fig. 2"),
    ("Figure caption", "Figure caption"),  # a section name, not a handle
    ("Abstract", "Abstract"),
])
def test_internal_figure_handles_do_not_reach_the_reader(raw, expected):
    from report_model import readable_figure_locator
    assert readable_figure_locator(raw) == expected


def test_a_caption_anchor_is_abridged_but_a_sentence_is_not():
    """A journal figure legend is one run-on description of every panel. Quoting
    it whole put 200-400 words of panel statistics under a claim."""
    from quote_display import anchor_quote_for_display

    legend = ("The R136S mutation ameliorates APOE4-driven neurodegeneration. "
              "a, Representative images of 10-month-old PS19-E4 mouse brain "
              "sections stained with Sudan black. " + "b, Quantification of "
              "hippocampal volume (n = 31). " * 12)
    caption_anchor = {"block_id": "p:CAP:Fig4", "quote": legend}
    sentence_anchor = {"block_id": "p:SENT:1",
                       "quote": "Our data show that R136S fully protects."}

    abridged = anchor_quote_for_display(caption_anchor, 420)
    assert len(abridged) < len(legend)
    assert abridged.startswith("The R136S mutation ameliorates")
    assert anchor_quote_for_display(sentence_anchor, 420) == \
        sentence_anchor["quote"]


def test_a_case_report_states_its_result_without_statistics():
    """_RESULT_MARKER wanted magnitudes, direction words, p-values or n=. A
    case report's finding has none, so a primary human observation was demoted
    to background in a review that called it its strongest proof-of-concept."""
    from anchor_policy import may_be_primary

    assert may_be_primary(
        "We describe in vivo follow-up PET imaging and postmortem findings from "
        "an autosomal dominant Alzheimer's disease PSEN1 E280A carrier who was "
        "also homozygous for the APOE3 Christchurch variant and was protected "
        "against Alzheimer's symptoms for almost three decades beyond the "
        "expected age of onset.", "Abstract")


@pytest.mark.parametrize("quote,section", [
    ("In late-onset AD, the apolipoprotein E gene (APOE) e4 allele is the "
     "strongest genetic risk factor.", "Introduction"),
    ("APOE4-targeting ASOs reduced tau pathology in mice carrying Mapt P301S "
     "but currently there are no trials in humans [13, 19].", "Introduction"),
    ("Sortilin (SORT1) is a scavenger receptor responsible for the uptake of "
     "PGRN into the cell targeting its degradation.", "Background"),
])
def test_background_framing_is_still_demoted(quote, section):
    """Widening the marker must not let review framing count as primary."""
    from anchor_policy import may_be_primary
    assert not may_be_primary(quote, section)


# --- axis labels the run supplies --------------------------------------------

def test_a_run_supplied_axis_label_wins(make_run):
    """No rule recovers English from a snake_case id: lowering_silencing wants
    an ampersand, protective_mimicry does not, mechanism_lysosome wants a colon.
    A delivered report headed its sections "Lowering Silencing" and "Structure
    Lipidation". The run knows what it meant, so it can say so."""
    import json

    from report_model import build_model, load_contract

    run = make_run()
    manifest_path = run / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    axes = {r["cluster"] for r in build_model(run, load_contract())["claims"]}
    chosen = sorted(axes)[0]
    manifest["axis_labels"] = {chosen: "Lowering & silencing"}
    manifest_path.write_text(json.dumps(manifest))

    model = build_model(run, load_contract())
    assert model["axis_labels"][chosen] == "Lowering & silencing"
    assert any(r["axis_label"] == "Lowering & silencing"
               for r in model["synthesis_table"] if r["axis"] == chosen)
    assert any(r["cluster_label"] == "Lowering & silencing"
               for r in model["claims"] if r["cluster"] == chosen)


def test_an_empty_override_falls_back_rather_than_blanking_the_heading():
    from report_model import display_axis
    assert display_axis("mechanism_lysosome", {"mechanism_lysosome": "  "}) == \
        "Mechanism Lysosome"
    assert display_axis("biomarker_nfl", {}) == "Biomarker NfL"


# --- figure crops -------------------------------------------------------------

def test_a_crop_does_not_widen_into_the_neighbouring_text_column():
    """Strategy A widened every crop to the page margins for "visual context".
    On a two-column page that captures the adjacent column: a delivered report
    reproduced a figure with two columns of the article's prose baked in, with
    provenance boxes drawn over the prose as though it were the figure."""
    import sys
    vendor = (pathlib.Path(__file__).resolve().parent.parent
              / "scripts" / "vendor" / "keyword_evidence")
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from parse_pdf import _text_free_span

    page_w = 612.0
    caption = {"bbox": (50, 400, 300, 415)}
    right_column = {"bbox": (320, 90, 560, 400)}
    figure = (50, 100, 290, 390)

    _left, right = _text_free_span([caption, right_column], caption,
                                   figure, page_w)
    assert right < 320, "crop still reaches into the neighbouring column"


def test_a_single_column_figure_still_widens_to_the_margins():
    """The widening exists for axis labels and panel letters outside the raster
    bbox; the fix must not cost that."""
    import sys
    vendor = (pathlib.Path(__file__).resolve().parent.parent
              / "scripts" / "vendor" / "keyword_evidence")
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from parse_pdf import _text_free_span

    page_w = 612.0
    caption = {"bbox": (50, 400, 300, 415)}
    _left, right = _text_free_span([caption], caption, (120, 100, 480, 390),
                                   page_w)
    assert right >= page_w


def test_in_figure_text_never_clips_its_own_figure():
    import sys
    vendor = (pathlib.Path(__file__).resolve().parent.parent
              / "scripts" / "vendor" / "keyword_evidence")
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from parse_pdf import _text_free_span

    caption = {"bbox": (50, 400, 300, 415)}
    inside = {"bbox": (200, 150, 260, 165)}     # a rasterised panel label
    _left, right = _text_free_span([caption, inside], caption,
                                   (120, 100, 480, 390), 612.0)
    assert right >= 612.0


def test_image_union_padding_does_not_clip_a_figure_near_the_page_top():
    import sys
    vendor = (pathlib.Path(__file__).resolve().parent.parent
              / "scripts" / "vendor" / "keyword_evidence")
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from parse_pdf import _padded_image_bbox

    assert _padded_image_bbox((40.0, 3.0, 500.0, 300.0), 330.0) == (
        40.0, 0.0, 500.0, 306.0
    )


def _vendor():
    import sys
    v = (pathlib.Path(__file__).resolve().parent.parent
         / "scripts" / "vendor" / "keyword_evidence")
    if str(v) not in sys.path:
        sys.path.insert(0, str(v))
    return v


def test_the_text_walk_fallback_also_stops_at_the_next_column():
    """The first fix covered only the image-union strategy, so the fallback path
    kept shipping crops with the article's other column in them — visible on
    page 9 of a delivered report, where a figure crop carries a full column of
    running prose beside the gel panels."""
    vendor = _vendor()
    src = (vendor / "parse_pdf.py").read_text()
    start = src.index("Strategy B: closest text block above")
    walk = src[start:src.index("Pick whichever has a usable", start)]
    assert "_text_free_span" in walk, (
        "the text-walk fallback still widens to the page margins unconditionally")


def test_a_table_caption_produces_no_crop():
    """A table caption sits ABOVE its table; every crop rule here reads the
    region above the caption. Applied to a table it captures whatever preceded
    it — a delivered report showed "Report Figure 7" as a bare line reading
    "Table 2." with no table under it."""
    _vendor()
    import parse_pdf

    src = pathlib.Path(parse_pdf.__file__).read_text()
    assert "is_table" in src
    guard = src[src.index("image_path = None"):]
    assert guard.lstrip().splitlines()[1].strip().startswith("if (not is_table)"), (
        "table captions can still emit a crop")


def test_the_caption_regex_still_matches_tables_for_text_evidence():
    """Only the CROP is skipped. The caption remains usable as text evidence,
    which is what a table caption was good for in the first place."""
    _vendor()
    from parse_pdf import _CAPTION_RE
    assert _CAPTION_RE.match("Table 2. Prediction of disease progression")
