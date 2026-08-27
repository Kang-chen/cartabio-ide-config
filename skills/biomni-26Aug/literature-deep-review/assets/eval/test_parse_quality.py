from __future__ import annotations


def _parsed(sentences=(), figures=()):
    return {
        "paper_id": "P1",
        "parser": "test",
        "sentences": [{"text": text} for text in sentences],
        "figures": list(figures),
    }


def test_zero_body_with_caption_is_explicitly_figure_only():
    from parse_quality import FIGURE_ONLY, assess

    receipt = assess(
        _parsed(figures=[{"caption": "Figure 1. SLC33A1 structure."}]),
        min_sentences=20,
    )

    assert receipt["state"] == FIGURE_ONLY
    assert receipt["substantive_sentence_count"] == 0


def test_empty_retrieved_document_is_not_a_successful_parse():
    from parse_quality import UNUSABLE, assess

    assert assess(_parsed(), min_sentences=20)["state"] == UNUSABLE


def test_richer_recovery_parse_wins():
    from parse_quality import prefer

    current = _parsed(figures=[{"caption": "Figure 1."}])
    recovered = _parsed(sentences=["This sentence contains a substantive result."])

    assert prefer(current, recovered) is recovered
