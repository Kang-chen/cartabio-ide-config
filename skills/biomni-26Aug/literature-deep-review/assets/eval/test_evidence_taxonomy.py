"""Evidence dimensions remain independent in reader-facing labels."""
from evidence_taxonomy import enrich_anchor, support_description


def test_primary_paper_abstract_anchor_is_not_called_secondary():
    anchor = {
        "paper_id": "P-1",
        "section": "Abstract",
        "source_locator": "Abstract",
        "evidence_kind": "primary",
    }

    enriched = enrich_anchor(anchor, {"study_type": "experimental study"})

    assert enriched["publication_type"] == "primary_report"
    assert enriched["anchor_depth"] == "abstract_only"
    assert enriched["claim_relationship"] == "direct"


def test_multiple_papers_from_one_study_are_labeled_as_one_study():
    label = support_description("C1_SINGLE_DIRECT", {
        "n_primary_studies": 1,
        "n_primary_papers": 3,
        "n_primary_cohorts": 1,
    })

    assert label == "One primary study reported across 3 papers"
