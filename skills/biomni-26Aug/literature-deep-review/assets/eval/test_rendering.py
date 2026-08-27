"""Font coverage, claim numbering, and what actually lands in the PDF."""
from __future__ import annotations

import json
import subprocess

import pytest

from report_fonts import (
    markup_for_render,
    normalize_for_render,
    register,
    unrenderable,
)


# --- font coverage ----------------------------------------------------------

def test_unicode_family_is_registered():
    """The shipped PDFs used base-14 Helvetica with nothing embedded, so every
    non-WinAnsi codepoint drew .notdef."""
    fonts = register()
    assert fonts.embedded, (
        "the bundled Biomni fonts and Unicode fallback must register")
    assert fonts.body == "DieGrotesk"
    assert fonts.display == "Signifier"
    assert fonts.fallback == "DejaVuSans"


def test_brand_font_uses_dejavu_only_for_missing_scientific_glyphs():
    markup = markup_for_render("SLC33A1 and Aβ42; P ≤ 0.05")
    assert markup.startswith("SLC33A1 and A")
    assert '<font face="DejaVuSans">β</font>' in markup
    assert '<font face="DejaVuSans">≤</font>' in markup
    assert "SLC33A1</font>" not in markup


@pytest.mark.parametrize("text", [
    "patients‑derived plasma",   # U+2011: became a black box
    "Aβ42 fibrils",              # beta: was dropped entirely
    "APOE ε4 carriers",          # epsilon: was dropped entirely
    "P ≤ 0.001, 20 µm, ≥ 2 studies",
    "Grn−/− microglia",
])
def test_biomedical_text_is_renderable(text):
    assert unrenderable(text) == {}


@pytest.mark.parametrize("raw,expected", [
    ("ﬁbril", "fibril"),          # presentation ligature decomposes
    ("a b", "a b"),               # NBSP is a space
    ("soft­hyphen", "softhyphen"),
])
def test_normalization_preserves_meaning(raw, expected):
    assert normalize_for_render(raw) == expected


@pytest.mark.parametrize("text", ["Aβ42", "APOE ε4", "P ≤ 0.05"])
def test_normalization_never_strips_meaningful_characters(text):
    """Greek letters and comparison operators carry meaning. Substituting or
    dropping them silently rewrites a quote, which is the one thing a
    verbatim-grounding skill may not do."""
    assert normalize_for_render(text) == text


def test_font_gate_rejects_undrawable_text(model):
    from build_pdf import _font_coverage_failures

    assert _font_coverage_failures(model) == []
    # A codepoint outside even DejaVu must be caught, not silently boxed.
    model["claims"][0]["claim_text"] += " \U0001F600 ᚠ"
    failures = _font_coverage_failures(model)
    assert failures and "U+16A0" in failures[0]


# --- claim numbering --------------------------------------------------------

def test_delivered_claim_ids_remain_canonical(model):
    """A rendered ID must still join to evidence, figures, and narratives.

    Renumbering C-003 as C-002 made the PDF easier to scan but broke the audit
    trail: the figure manifest and evidence table still called it C-003.
    """
    assert [c["claim_id"] for c in model["claims"]] == ["C-001", "C-003", "C-005"]
    assert [c["display_id"] for c in model["claims"]] == ["C-001", "C-003", "C-005"]


def test_figures_reference_display_ids(model):
    """A figure's "grounds C-005" must use the number the reader can find."""
    shown = {c["display_id"] for c in model["claims"]}
    for fig in model["figures"]:
        assert fig["claim_display_ids"]
        assert set(fig["claim_display_ids"]) <= shown


# --- end-to-end -------------------------------------------------------------

@pytest.fixture
def built_pdf(run_root, tmp_path):
    import build_pdf

    out = tmp_path / "report.pdf"
    assert build_pdf.main(["--root", str(run_root), "--out", str(out)]) == 0
    return out


def _pdftotext(path) -> str:
    try:
        return subprocess.run(["pdftotext", "-layout", str(path), "-"],
                              capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("pdftotext (poppler) not available")


def test_pdf_embeds_every_font_it_uses(built_pdf):
    try:
        listing = subprocess.run(["pdffonts", str(built_pdf)],
                                 capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("pdffonts (poppler) not available")
    rows = [r for r in listing.splitlines()[2:] if r.strip()]
    assert rows
    assert any("DieGrotesk" in row for row in rows)
    assert any("Signifier" in row for row in rows)
    assert not any("Helvetica" in row for row in rows)
    for row in rows:
        # Columns: name type encoding emb sub uni object ID
        assert row.split()[-5] == "yes", f"non-embedded font in output: {row}"


def test_pdf_shows_no_replacement_boxes(built_pdf):
    text = _pdftotext(built_pdf)
    assert "■" not in text, "black box (.notdef) rendered into the PDF"
    assert "‑" in text or "‐" in text, (
        "the fixture's non-breaking hyphen should survive to the page")


def test_pdf_embeds_dejavu_only_when_brand_font_lacks_a_glyph(run_root, tmp_path):
    import build_pdf

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["title"] = "Aβ42 target review"
    manifest_path.write_text(json.dumps(manifest))
    out = tmp_path / "fallback.pdf"
    assert build_pdf.main(["--root", str(run_root), "--out", str(out)]) == 0

    text = _pdftotext(out)
    assert "Aβ42 target review" in text
    listing = subprocess.run(
        ["pdffonts", str(out)], capture_output=True, text=True, check=True
    ).stdout
    assert "DieGrotesk" in listing
    assert "Signifier" in listing
    assert "DejaVuSans" in listing


def test_pdf_attribution_line_is_a_citation(built_pdf):
    text = _pdftotext(built_pdf)
    assert "Ward et al. 2024 [1]" in text
    # The internal block id must not be printed to a reader.
    assert ":S:0" not in text
    # ...and the DOI must not be repeated as though it were the citation.
    assert "10.1000/alpha · " not in text


def test_pdf_uses_english_axis_headings(built_pdf):
    text = _pdftotext(built_pdf)
    assert "Genetics & causality" in text
    assert "genetics_causality" not in text


# --- Markdown emphasis in authored prose --------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("**bold** text", "<b>bold</b> text"),
    ("*italic* text", "<i>italic</i> text"),
    ("**The mechanistic case is the most convergent.** apoE-isoform",
     "<b>The mechanistic case is the most convergent.</b> apoE-isoform"),
])
def test_authored_prose_renders_markdown_emphasis(raw, expected):
    """A shipped report printed the asterisks: "This review asks a focused
    question: **how strong, and of what kind, ...**" in the Introduction and in
    five of six Conclusions paragraphs. review.md rendered them; the PDF escaped
    them."""
    from build_pdf import _esc_prose

    assert _esc_prose(raw) == expected


@pytest.mark.parametrize("text", [
    "2 * 3 * 4 arithmetic",
    "mean ± SEM (*P<0.05, **P<0.01)",
])
def test_bare_asterisks_are_not_emphasis(text):
    from build_pdf import _esc_prose

    assert "<b>" not in _esc_prose(text) and "<i>" not in _esc_prose(text)


def test_quotes_keep_their_asterisks_literal():
    """A verbatim sentence's significance markers must stay markers.
    Reinterpreting them as emphasis would silently alter quoted text."""
    from build_pdf import _esc

    out = _esc("Values are mean±SEM (*P<0.05, **P<0.01, unpaired t-test).")
    assert "**P" in out and "<b>" not in out


def test_pdf_shows_no_literal_markdown(run_root, tmp_path):
    import subprocess

    import build_pdf

    sections = run_root / "deliverables" / "report_sections.json"
    data = json.loads(sections.read_text())
    data["conclusions"] = [{"text": "**The mechanistic case is convergent.** "
                                    "Detail follows.", "inference": True}]
    sections.write_text(json.dumps(data))

    out = tmp_path / "r.pdf"
    assert build_pdf.main(["--root", str(run_root), "--out", str(out)]) == 0
    try:
        text = subprocess.run(["pdftotext", str(out), "-"], capture_output=True,
                              text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("pdftotext (poppler) not available")
    assert "**" not in text
    assert "The mechanistic case is convergent." in text
