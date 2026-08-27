from __future__ import annotations


def test_every_raw_adjudication_requires_one_disposition():
    from evidence_lineage import accepted, adjudication_id, problems

    raw = {"claim_id": "C-1", "block_id": "P:S:1", "paper_id": "P"}
    aid = adjudication_id(raw, 1)
    failures = problems(
        [raw, raw],
        [{"evidence_id": "E-1"}],
        [accepted(aid, raw, "E-1")],
    )

    assert any("row count" in failure for failure in failures)


def test_accepted_lineage_must_equal_final_evidence():
    from evidence_lineage import accepted, adjudication_id, problems

    raw = {"claim_id": "C-1", "block_id": "P:S:1", "paper_id": "P"}
    lineage = [accepted(adjudication_id(raw, 1), raw, "E-lost")]

    assert any("final evidence" in failure for failure in problems([raw], [], lineage))


def test_lineage_rejects_a_disposition_attached_to_the_wrong_raw_row():
    from evidence_lineage import accepted, adjudication_id, problems, rejected

    raw = [
        {"claim_id": "C-001", "paper_id": "P1", "block_id": "P1:S:1"},
        {"claim_id": "C-002", "paper_id": "P2", "block_id": "P2:S:2"},
    ]
    lineage = [
        accepted(adjudication_id(raw[1], 2), raw[1], "E-2"),
        rejected(adjudication_id(raw[0], 1), raw[0], "bad quote"),
    ]

    failures = problems(raw, [{"evidence_id": "E-2"}], lineage)

    assert any("does not match its raw decision" in failure for failure in failures)
