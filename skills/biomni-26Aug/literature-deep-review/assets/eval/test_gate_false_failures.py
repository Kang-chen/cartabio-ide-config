"""Gates that failed a correct report.

Each of these fired on a real Biomni run whose content was sound, and each cost
the operator a diagnose-and-patch cycle before it could deliver. Two are
regressions from earlier skill changes: display-id handling and renaming the
infographic's caption marker, neither of which updated the gate that checks it.
The third is the same shape one level out — a gate holding a private copy of
what it is supposed to be checking.
"""
from __future__ import annotations

import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def test_the_report_is_checked_for_the_ids_it_actually_prints(run_root):
    """The verifier checks the canonical IDs the report actually prints."""
    from report_model import build_model, load_contract
    from verify_review import _claims_missing_from_report

    model = build_model(run_root, load_contract())
    claims_by_id = {r["claim_id"]: r for r in model["claims"]}
    # A report that prints only display ids — which is what build_review writes.
    rendered = "\n".join(f"{r['display_id']}. {r['claim_text']}"
                         for r in model["claims"])
    assert _claims_missing_from_report(run_root, claims_by_id, rendered) == []


def test_a_genuinely_absent_claim_is_still_caught(run_root):
    """The fix must not blind the gate."""
    from report_model import build_model, load_contract
    from verify_review import _claims_missing_from_report

    model = build_model(run_root, load_contract())
    claims_by_id = {r["claim_id"]: r for r in model["claims"]}
    rendered = "\n".join(f"{r['display_id']}." for r in model["claims"][1:])
    missing = _claims_missing_from_report(run_root, claims_by_id, rendered)
    assert len(missing) == 1


def test_the_infographic_marker_comes_from_the_contract():
    """The gate hardcoded "visual abstract" while the contract declared
    caption_marker "infographic" and the builder wrote "Infographic"."""
    import json

    from verify_pdf_assets import _infographic_markers

    contract = json.loads(
        (SCRIPTS.parent / "templates" / "report_contract.json").read_text())
    declared = contract["visual_abstract"]["caption_marker"].lower()
    markers = _infographic_markers("visual abstract")
    assert declared in markers, f"{declared!r} not among {markers}"
    # The legacy marker still passes, so older reports do not regress.
    assert "visual abstract" in markers


def test_only_cited_references_are_checked_for_hyperlinks(run_root):
    """corpus/references.jsonl is the whole discovered pool. Checking it flagged
    66 "unlinked references" for a report whose 27 real citations were all
    linked — a failure about documents that are not in the document."""
    import json

    from report_model import load_contract
    from verify_report_contract import _cited_references

    pool = [{"paper_id": "10.1000/alpha", "doi": "10.1000/alpha"},
            {"paper_id": "10.9999/never-cited", "doi": "10.9999/never-cited"}]
    (run_root / "corpus" / "references.jsonl").write_text(
        "\n".join(json.dumps(r) for r in pool) + "\n")

    cited = _cited_references(run_root, load_contract(), pool)
    ids = {r["paper_id"] for r in cited}
    assert "10.9999/never-cited" not in ids, (
        "an uncited corpus-pool entry is still being checked for a link")


def test_an_unbuildable_model_falls_back_to_the_full_pool(tmp_path):
    """Silently skipping the check would be worse than over-reporting; a broken
    model must surface through its own gate."""
    from report_model import load_contract
    from verify_report_contract import _cited_references

    pool = [{"paper_id": "x", "doi": "10.1/x"}]
    assert _cited_references(tmp_path / "nonexistent", load_contract(), pool) == pool


def test_multiple_figures_from_one_paper_do_not_fake_paper_coverage(tmp_path):
    """The old gate compared a paper fraction to a figure count."""
    import json

    from verify_report_contract import _check_paper_figures

    root = tmp_path / "run"
    (root / "evidence").mkdir(parents=True)
    (root / "fulltext" / "parsed").mkdir(parents=True)
    (root / "corpus").mkdir(parents=True)
    manifest_dir = root / "deliverables" / "figures_cited"
    manifest_dir.mkdir(parents=True)
    papers = [f"P{i}" for i in range(1, 6)]
    (root / "evidence" / "evidence.jsonl").write_text(
        "".join(json.dumps({"paper_id": pid, "stance": "supports"}) + "\n"
                for pid in papers)
    )
    (root / "corpus" / "references.jsonl").write_text(
        "".join(json.dumps({
            "paper_id": pid, "figure_embedding_allowed": True,
        }) + "\n" for pid in papers)
    )
    for pid in papers:
        image = root / "fulltext" / f"{pid}.png"
        image.write_bytes(b"crop")
        (root / "fulltext" / "parsed" / f"{pid}.json").write_text(json.dumps({
            "paper_id": pid,
            "figures": [{"figure_id": "F1", "image_path": str(image)}],
        }))
    (manifest_dir / "figures_manifest.json").write_text(json.dumps({
        "figures": [
            {"paper_id": "P1", "figure_id": f"F{i}", "status": "exported"}
            for i in range(1, 5)
        ],
    }))

    failures: list[str] = []
    _check_paper_figures(
        {"paper_figures": {
            "caption_prefix": "Report Figure",
            "min_by_mode": {"deep": 0},
            "min_fraction_of_croppable": 0.8,
        }},
        "deep",
        root,
        "report figure 1 report figure 2 report figure 3 report figure 4",
        4,
        failures,
        [],
    )
    assert any("too few cited papers contribute" in failure for failure in failures)


def test_user_directed_figures_count_as_policy_eligible_supply(run_root):
    import json

    from verify_report_contract import croppable_supply

    refs = run_root / "corpus" / "references.jsonl"
    rows = [json.loads(line) for line in refs.read_text().splitlines() if line]
    for row in rows:
        row["figure_embedding_allowed"] = False
        row["reuse_rights"] = "none"
    refs.write_text("".join(json.dumps(row) + "\n" for row in rows))
    papers = run_root / "fulltext" / "papers.jsonl"
    prows = [json.loads(line) for line in papers.read_text().splitlines() if line]
    for row in prows:
        row["figure_embedding_allowed"] = False
        row["reuse_rights"] = "none"
    papers.write_text("".join(json.dumps(row) + "\n" for row in prows))

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["config"].update({
        "figure_reuse_policy": "user_directed",
        "figure_reuse_decision_source": "explicit_user",
    })
    manifest_path.write_text(json.dumps(manifest))

    supply = croppable_supply(run_root)
    assert supply["figure_reuse_policy"] == "user_directed"
    assert supply["policy_eligible"] == supply["cited"]
    assert supply["croppable"]
