from __future__ import annotations

import json

from reuse_prior_fulltext import seed


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_prior_version_family_fulltext_is_copied_into_the_current_run(tmp_path):
    current = tmp_path / "current"
    prior = tmp_path / "prior"
    source_pdf = prior / "fulltext" / "pdfs" / "preprint.pdf"
    source_pdf.parent.mkdir(parents=True, exist_ok=True)
    source_pdf.write_bytes(b"%PDF-1.4 prior run")
    _jsonl(prior / "corpus" / "references.jsonl", [{
        "paper_id": "preprint", "study_id": "gssg-study",
    }])
    _jsonl(prior / "fulltext" / "papers.jsonl", [{
        "paper_id": "preprint", "local_pdf": str(source_pdf),
        "access": "free_to_read",
    }])
    _jsonl(current / "corpus" / "references.jsonl", [{
        "paper_id": "journal-version", "study_id": "gssg-study",
    }])
    selected = current / "corpus" / "records.jsonl"
    _jsonl(selected, [{"paper_id": "journal-version"}])

    overrides = seed(current, prior, selected)

    assert len(overrides) == 1
    assert overrides[0]["reused_from_paper_id"] == "preprint"
    assert (current / "fulltext" / "prior_reuse" /
            "journal-version.pdf").read_bytes() == source_pdf.read_bytes()
