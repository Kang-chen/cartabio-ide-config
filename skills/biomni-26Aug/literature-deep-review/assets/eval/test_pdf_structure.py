"""The final PDF gate rejects files that tolerant readers might repair."""
from __future__ import annotations

from pypdf import PdfWriter

from verify_pdf_structure import verify


def test_line_numbered_preprint_caption_is_detected():
    from scripts.vendor.keyword_evidence.parse_pdf import _match_caption

    match = _match_caption(
        "498 Figure 3. SLC33A1 inhibition causes ER hyperoxidation.",
        font_size=9,
        body_font_size=10,
    )

    assert match is not None
    assert match.group("label") == "Figure 3"


def test_adjacent_embedded_panels_are_reassembled_before_fallback_ocr():
    from scripts.vendor.keyword_evidence.parse_pdf import _group_embedded_images

    groups = _group_embedded_images([
        (40, 100, 250, 300),
        (275, 100, 485, 300),
        (40, 500, 220, 680),
    ])

    assert groups == [
        [(40, 100, 250, 300), (275, 100, 485, 300)],
        [(40, 500, 220, 680)],
    ]


def _valid_pdf(path):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


def test_strict_structure_accepts_a_well_formed_pdf(tmp_path):
    path = tmp_path / "valid.pdf"
    _valid_pdf(path)

    failures, notes = verify(path)

    assert failures == []
    assert any("strict pypdf parse passed" in note for note in notes)


def test_strict_structure_rejects_a_truncated_pdf(tmp_path):
    path = tmp_path / "truncated.pdf"
    _valid_pdf(path)
    path.write_bytes(path.read_bytes()[:-24])

    failures, _notes = verify(path)

    assert any("strict PDF parse failed" in failure for failure in failures)
