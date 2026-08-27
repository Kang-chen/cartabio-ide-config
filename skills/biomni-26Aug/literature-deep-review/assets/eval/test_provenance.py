"""Figure provenance: showing the reader why THIS figure is under THIS claim.

Two shipped reports reproduced 27 figures between them and not one carried a mark
explaining the choice — even though the box-drawing code existed and
``report_model`` already preferred an annotated copy. The boxes came from
``ocr_lines``, populated only by evidence rows whose block_type was
``figure_ocr``; there were 0 and 1 such rows against 35 caption anchors.
"""
from __future__ import annotations

import pytest

from figure_provenance import MIN_CONF, Provenance, TermHit, annotate, find_term_hits
from figure_selection import shared_term_words, surface_form

CLAIM = ("Low plasma progranulin accurately identifies pathogenic GRN mutation "
         "carriers, including asymptomatic carriers.")
SCOPE = "Humans; symptomatic and asymptomatic GRN carriers; plasma/serum"


def _line(text, conf=0.9, box=(10, 20, 110, 40)):
    x0, y0, x1, y1 = box
    return {"text": text, "conf": conf,
            "bbox": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


# --- matching ---------------------------------------------------------------

def test_in_figure_text_matching_the_claim_is_found():
    hits = find_term_hits(
        [_line("PGRN (ng/ml)"), _line("GRN carriers"), _line("scale bar 20 um")],
        CLAIM, SCOPE)
    assert {h.text for h in hits} == {"GRN carriers"}


def test_matching_uses_the_same_stemmer_as_the_score():
    """An in-figure "carriers" has to match a claim saying "carrier", by the same
    rule that scored the caption — one vocabulary applied in two places."""
    hits = find_term_hits([_line("asymptomatic carrier")], CLAIM, SCOPE)
    assert hits


def test_unrelated_in_figure_text_is_not_boxed():
    hits = find_term_hits(
        [_line("body weight (g)"), _line("weeks"), _line("n.s.")], CLAIM, SCOPE)
    assert hits == []


def test_low_confidence_lines_are_ignored():
    assert find_term_hits([_line("GRN carriers", conf=MIN_CONF - 0.1)],
                          CLAIM, SCOPE) == []


def test_a_rasterized_paragraph_is_not_a_term_hit():
    """Some journals rasterize the caption INTO the image. Boxing that would
    ring most of the picture and claim it as the reason."""
    paragraph = ("GRN carriers were compared with non-carriers across three "
                 "independent cohorts using an identical assay protocol and "
                 "the same reference range throughout.")
    assert find_term_hits([_line(paragraph)], CLAIM, SCOPE) == []


# --- honesty of the caption note --------------------------------------------

def test_note_never_implies_the_picture_was_read_when_it_was_not():
    """Even though the installer preflights EasyOCR, a broken runtime must say
    the figure was not read — silence reads as "checked, nothing found"."""
    note = Provenance(caption_terms=["progranulin"], figure_hits=[],
                      ocr_available=False).caption_note()
    assert "OCR unavailable" in note
    assert "boxed" not in note.replace("no regions are boxed", "")


def test_note_distinguishes_read_but_no_match_from_not_read():
    read_no_match = Provenance(caption_terms=["progranulin"],
                               ocr_available=True).caption_note()
    assert "no claim term appears as text inside the figure" in read_no_match


def test_note_names_both_signals_when_both_exist():
    note = Provenance(
        caption_terms=["progranulin", "carriers"],
        figure_hits=[TermHit("carriers", "GRN carriers", 0.9,
                             [(0, 0), (1, 0), (1, 1), (0, 1)])],
        ocr_available=True).caption_note()
    assert "caption terms" in note and "boxed in-figure text" in note


# --- readable labels --------------------------------------------------------

@pytest.mark.parametrize("stem,text,expected", [
    ("frontotempor", "familial frontotemporal dementia", "frontotemporal"),
    ("heterozygou", "heterozygous carriers", "heterozygous"),
    ("lysosom", "increased lysosomal biogenesis", "lysosomal"),
    ("defici", "PGRN-deficient mice", "deficient"),
])
def test_stems_render_as_words(stem, text, expected):
    """The first version printed the stems: a caption reading "matched on
    frontotempor, heterozygou, lysosom, defici" describes the stemmer, not the
    evidence."""
    assert surface_form(stem, text) == expected


def test_shared_term_words_are_readable():
    words = shared_term_words(
        "Fig. 2 Plasma progranulin in heterozygous GRN carriers", CLAIM, SCOPE)
    assert words
    assert all(not w.endswith(("tempor", "zygou")) for w in words)


# --- drawing ----------------------------------------------------------------

def test_annotate_writes_a_separate_file_and_leaves_the_crop_intact(tmp_path):
    """The plain crop must survive unaltered: it is the report's unmodified
    reproduction of the published figure."""
    from PIL import Image

    src = tmp_path / "fig.png"
    Image.new("RGB", (320, 200), (240, 240, 245)).save(src)
    before = src.read_bytes()
    dst = tmp_path / "fig.annotated.png"
    hit = TermHit("carriers", "GRN carriers", 0.9,
                  [(20, 40), (140, 40), (140, 60), (20, 60)])
    assert annotate(src, dst, [hit]) is True
    assert dst.exists()
    assert src.read_bytes() == before


def test_annotate_declines_when_there_is_nothing_to_draw(tmp_path):
    from PIL import Image

    src = tmp_path / "fig.png"
    Image.new("RGB", (100, 100)).save(src)
    dst = tmp_path / "out.png"
    assert annotate(src, dst, []) is False
    assert not dst.exists()


def test_annotate_survives_an_unreadable_image(tmp_path):
    """Annotation is cosmetic and must never cost the figure."""
    src = tmp_path / "broken.png"
    src.write_bytes(b"not a png")
    hit = TermHit("x", "x", 0.9, [(0, 0), (1, 0), (1, 1), (0, 1)])
    assert annotate(src, tmp_path / "o.png", [hit]) is False


# --- end to end through the export step -------------------------------------

def test_export_produces_annotated_figures_and_notes(run_root):
    from export_figures import export_cited_figures

    figures = [f for f in export_cited_figures(run_root)["figures"]
               if f["status"] == "exported"]
    assert figures
    annotated = [f for f in figures if f.get("annotated_image")]
    assert annotated, "no figure carried provenance boxes"
    for fig in annotated:
        note = fig["provenance_note"]
        assert note.startswith("Why this figure:")
        assert "boxed in-figure text" in note
        prov = fig["provenance"]
        assert prov["caption_terms"] and prov["figure_term_hits"]
        assert prov["ocr_available"] is True


def test_client_report_prefers_clean_crop_over_ocr_box_overlay(run_root):
    from export_figures import export_cited_figures
    from report_model import build_model, load_contract

    manifest = export_cited_figures(run_root)
    annotated = next(
        row for row in manifest["figures"]
        if row.get("status") == "exported" and row.get("annotated_image")
    )
    report_figure = next(
        row for row in build_model(run_root, load_contract())["figures"]
        if row["figure_id"] == annotated["figure_id"]
    )

    assert report_figure["image"].endswith(annotated["image"])


def test_boxed_terms_come_from_the_claim(run_root):
    """A box must mark a term the CLAIM contains, never "something interesting"."""
    import json

    from export_figures import export_cited_figures
    from figure_selection import _terms

    claims = {}
    for line in (run_root / "corpus" / "claims.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            claims[row["claim_id"]] = _terms(row["claim_text"]) | _terms(
                row.get("scope", ""))

    for fig in export_cited_figures(run_root)["figures"]:
        for hit in (fig.get("provenance") or {}).get("figure_term_hits") or []:
            wanted = set().union(*(claims[c] for c in fig["claims"] if c in claims))
            assert _terms(hit["term"]) & wanted, hit


def test_pdf_caption_carries_the_provenance_note(run_root, tmp_path):
    import subprocess

    import build_pdf
    from export_figures import export_cited_figures

    export_cited_figures(run_root)
    out = tmp_path / "r.pdf"
    assert build_pdf.main(["--root", str(run_root), "--out", str(out)]) == 0
    try:
        text = subprocess.run(["pdftotext", "-layout", str(out), "-"],
                              capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("pdftotext (poppler) not available")
    assert "Why this figure:" in text


# --- the annotation must not damage the figure -------------------------------

def test_a_box_never_stamps_text_onto_the_figure(tmp_path):
    """The label used to be drawn above each box on an opaque background. The
    space above a hit is not empty — the hit usually IS a panel title or an axis
    label — so the stamp painted over the figure's own text and the shipped
    report showed "latozinemab mab concentration in HVs" and "sortilinilin
    levels". A report about faithful reproduction cannot return damaged figures.
    """
    from PIL import Image, ImageChops

    from figure_provenance import TermHit, annotate

    src = tmp_path / "fig.png"
    Image.new("RGB", (400, 200), "white").save(src)
    dst = tmp_path / "fig.annotated.png"
    hit = TermHit(term="latozinemab", text="latozinemab",
                  conf=0.9, bbox=[(100, 100), (200, 100), (200, 130), (100, 130)])
    assert annotate(src, dst, [hit]) is True

    with Image.open(dst) as out:
        pixels = out.convert("RGB").load()
        # Well above the box is where the old label landed. It must be untouched.
        for x in range(100, 200, 7):
            for y in range(70, 95):
                assert pixels[x, y] == (255, 255, 255), (
                    f"annotation painted over ({x},{y}) above the box")


def test_the_box_itself_is_still_drawn(tmp_path):
    """Removing the label must not remove the mark."""
    from PIL import Image

    from figure_provenance import TermHit, annotate

    src = tmp_path / "fig.png"
    Image.new("RGB", (400, 200), "white").save(src)
    dst = tmp_path / "fig.annotated.png"
    annotate(src, dst, [TermHit(term="pgrn", text="pgrn", conf=0.9,
                                bbox=[(50, 50), (150, 50), (150, 90), (50, 90)])])
    with Image.open(dst) as out:
        colours = set(out.convert("RGB").getcolors(maxcolors=1 << 20) or [])
    assert len(colours) > 1, "nothing was drawn at all"


# --- term matching must discriminate -----------------------------------------

def _ocr(*texts):
    return [{"text": t, "conf": 0.9,
             "bbox": [[i * 10, 0], [i * 10 + 9, 0], [i * 10 + 9, 9], [i * 10, 9]]}
            for i, t in enumerate(texts)]


def test_generic_compound_fragments_are_not_boxed():
    """`_terms` splits "anti-sortilin" into its parts, so "anti" matched every
    "Anti-sort" tick label on an axis while the specific compound sat unused."""
    from figure_provenance import find_term_hits

    hits = find_term_hits(_ocr("Anti-sort", "Anti-sort", "Anti-sort", "Anti-sort"),
                          "anti-sortilin antibody raises progranulin")
    assert hits == []


def test_a_term_repeated_across_the_figure_is_capped():
    """A term matching fifteen lines is wallpaper: it stops distinguishing the
    region it marks from the rest of the picture."""
    from figure_provenance import MAX_HITS_PER_TERM, find_term_hits

    hits = find_term_hits(_ocr(*["latozinemab concentration"] * 15),
                          "latozinemab elevates progranulin")
    assert len(hits) <= MAX_HITS_PER_TERM


def test_total_boxes_are_capped_per_figure():
    from figure_provenance import MAX_BOXES, find_term_hits

    lines = _ocr(*[f"latozinemab sortilin progranulin carrier variant {i}"
                   for i in range(40)])
    hits = find_term_hits(
        lines, "latozinemab binds sortilin and raises progranulin in carriers "
               "with a variant")
    assert len(hits) <= MAX_BOXES


def test_the_most_specific_term_wins_the_box():
    """A line reading "anti-sortilin antibody" should be boxed for sortilin, not
    for whichever stem happens to sort first alphabetically."""
    from figure_provenance import find_term_hits

    hits = find_term_hits(_ocr("anti-sortilin antibody"),
                          "anti-sortilin antibody raises progranulin")
    assert hits and "sortilin" in hits[0].term.lower()
