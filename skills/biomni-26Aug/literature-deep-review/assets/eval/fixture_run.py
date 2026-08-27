#!/usr/bin/env python3
"""Build a synthetic run root that reproduces the defects found in shipped reports.

The skill had no tests. Every fix in this directory is verified against a run
assembled here rather than against a real 15-page PDF, because the defects we
are guarding are structural and a fixture can carry all of them at once:

  * a codepoint outside WinAnsi (U+2011) that rendered as a .notdef box
  * an anchor whose only citation is a DOI, printed twice
  * an evidence file whose row order is NOT the rendered claim order, which is
    what made "first-citation order" produce a reference list starting at the
    second-to-last claim
  * a letter-spaced / ligature-split / merged-word quote that the garbled
    detector waved through
  * a locator claiming "Front matter" for an Abstract block and "Methods" for a
    Results sentence
  * a claim-id sequence with a hole in it
  * a figure whose caption has nothing to do with the claim it is attached to

Callers get a directory; they do not get to mutate the module-level dicts, so
one test cannot bleed into another.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any


_SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent
_FIXTURE_PROVENANCE: dict[str, Any] | None = None


def _fixture_provenance() -> dict[str, Any]:
    global _FIXTURE_PROVENANCE
    if _FIXTURE_PROVENANCE is None:
        from skill_provenance import PACKAGE_INPUTS, bundle_sha256, directory_sha256

        directory_hash, file_count = directory_sha256(_SKILL_ROOT)
        _FIXTURE_PROVENANCE = {
            "schema_version": 1,
            "git_commit": "a" * 40,
            "git_commit_source": "synthetic_fixture",
            "skill_directory_sha256": directory_hash,
            "skill_bundle_sha256": bundle_sha256(_SKILL_ROOT),
            "package_inputs": list(PACKAGE_INPUTS),
            "file_count": file_count,
            "entrypoint_version_id": "fixture-version",
            "captured_at": "2026-07-27T09:00:00+00:00",
        }
    return dict(_FIXTURE_PROVENANCE)


def _png(width: int = 640, height: int = 400) -> bytes:
    """A real, decodable PNG.

    Built rather than hard-coded: the builders run ``PIL.Image.verify`` and skip
    anything that fails, so a byte-literal with a bad CRC makes every figure
    silently vanish from the fixture and the figure tests pass by testing
    nothing. Default size is figure-shaped so aspect-ratio scaling is exercised.
    """
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (235, 235, 240)).save(buffer, format="PNG")
    return buffer.getvalue()


# U+2011 NON-BREAKING HYPHEN. Present in text scraped from several publishers
# and absent from WinAnsiEncoding, so base-14 Helvetica drew .notdef for it.
NB_HYPHEN = "‑"


def _safe(name: str) -> str:
    """Mirrors ``export_figures._safe`` so fixture paths match real ones."""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name))


def _ocr(text: str, x0: float, y0: float, x1: float, y1: float,
         conf: float = 0.92) -> dict:
    """One in-figure OCR line, in EasyOCR's 4-point polygon form."""
    return {"text": text, "conf": conf,
            "bbox": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")


def _write_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _anchor(paper: str, block: str, quote: str, locator: str, *,
            stance: str = "supports", kind: str = "primary",
            authors: str = "", year: str = "", doi: str = "",
            figure_id: str = "", section: str = "", page: int = 1) -> dict:
    row = {
        "block_id": f"{paper}:{block}",
        "paper_id": paper,
        "doi": doi or paper,
        "quote": quote,
        "source_locator": locator,
        # `section` is what the contract's locator gate reads, and it is a
        # SEPARATE field from the rendered `source_locator`. A fixture that set
        # only the latter passed the renderers and failed the gate.
        "section": section or locator.split("·")[-1].strip(),
        "page": page,
        "stance": stance,
        "evidence_kind": kind,
        "authors": authors,
        "year": year,
        "url": f"https://doi.org/{doi or paper}",
    }
    if figure_id:
        row["figure_id"] = figure_id
        row["block_type"] = "caption"
    return row


# --- the three papers the fixture cites -------------------------------------
#
# Deliberately ordered so that alphabetical, evidence-file, and first-citation
# orderings all differ. A test that cannot tell them apart proves nothing.
PAPERS = {
    "10.1000/alpha": {
        "authors": "Ward MP; Carter LP; Huang JY",
        "year": "2024",
        "title": "Phase 1 study of a sortilin-blocking antibody",
        "journal": "Alzheimer's & Dementia: TRCI",
    },
    "10.1000/beta": {
        "authors": "Yoshinori Tanaka; J. Chambers; T. Matsuwaki",
        "year": "2014",
        "title": "Lysosomal dysfunction in aged progranulin-deficient mice",
        "journal": "Acta Neuropathologica Communications",
    },
    "10.1000/gamma": {
        "authors": "Cha Yang; Tuancheng Feng; Fenghua Hu",
        "year": "2025",
        "title": "Progranulin deficiency does not exacerbate TDP-43 pathology"
                 " | npj Dementia",
        "journal": "Npj Dementia",
    },
}


def build(
    root: pathlib.Path,
    *,
    mode: str = "broad",
    with_narratives: bool = True,
    with_bad_figure: bool = True,
    front_matter_locator: bool = False,
) -> pathlib.Path:
    """Write a complete run root at ``root`` and return it.

    ``front_matter_locator`` reproduces the shipped defect where abstract
    sentences carried the locator "page 1 · Front matter". It is OFF by default:
    the contract now forbids that section value for an evidence row, so leaving
    it on would make every other test fail the locator gate for the wrong reason.
    """
    root = pathlib.Path(root)

    # --- claims. C-002 is deliberately absent: the report must preserve that
    # canonical gap instead of renumbering C-003 onto a different claim ID.
    claims = [
        {"claim_id": "C-001", "cluster": "genetics_causality",
         "claim_text": "Heterozygous loss-of-function mutations in GRN cause "
                       "familial frontotemporal dementia.",
         "scope": "Humans; heterozygous GRN mutation carriers"},
        {"claim_id": "C-003", "cluster": "mechanism_biology",
         "claim_text": "Progranulin is required for normal lysosomal function.",
         "scope": "Mouse models; progranulin deficiency"},
        {"claim_id": "C-005", "cluster": "safety_contradictions",
         "claim_text": "The GRN-to-TDP-43 link is context-dependent rather "
                       "than absolute.",
         "scope": "Mouse models"},
    ]
    _write_jsonl(root / "corpus" / "claims.jsonl", claims)

    # --- references, in an order matching neither the alphabet nor the body.
    _write_jsonl(root / "corpus" / "references.jsonl", [
        {"paper_id": pid, "doi": pid, "url": f"https://doi.org/{pid}",
         "access_state": "oa_licensed", "access": "oa_licensed",
         "license": "CC BY 4.0", "reuse_rights": "full",
         "figure_embedding_allowed": True, **meta}
        for pid, meta in PAPERS.items()
    ])
    _write_jsonl(root / "fulltext" / "papers.jsonl", [
        {"paper_id": pid, "doi": pid, "access_state": "oa_licensed",
         "access": "oa_licensed", "license": "CC BY 4.0",
         "reuse_rights": "full", "figure_embedding_allowed": True, **meta}
        for pid, meta in PAPERS.items()
    ])
    _write_jsonl(root / "corpus" / "records.jsonl", [
        {"paper_id": pid, **meta} for pid, meta in PAPERS.items()
    ])
    _write_jsonl(root / "fulltext" / "not_retrieved.jsonl", [])

    # --- evidence. Row order is gamma, beta, alpha; rendered claim order is
    # alpha (C-001), beta (C-003), gamma (C-005). A reference list built by
    # walking this file in order comes out backwards.
    evidence = [
        {**_anchor(
            "10.1000/gamma", "S:24",
            "In both mouse models, we failed to detect significant effects of "
            "PGRN deficiency on TDP-43 protein levels.",
            "page 3 · Results",
            authors=PAPERS["10.1000/gamma"]["authors"], year="2025"),
         "claim_id": "C-005", "evidence_id": "E-gamma01",
         "block_type": "sentence"},
        {**_anchor(
            "10.1000/beta", "CAP:fig3_p07",
            "Figure 3 Increased lysosomal biogenesis in the cerebral cortex of "
            "aged PGRN-deficient mice.",
            "page 7 · Figure caption",
            authors=PAPERS["10.1000/beta"]["authors"], year="2014",
            figure_id="fig3_p07"),
         "claim_id": "C-003", "evidence_id": "E-beta01"},
        {**_anchor(
            "10.1000/alpha", "S:0",
            f"Heterozygous mutations in the GRN gene lead to reduced "
            f"progranulin levels and are causative of frontotemporal dementia "
            f"with high{NB_HYPHEN}penetrance.",
            "page 1 · Front matter" if front_matter_locator else "page 1 · Abstract",
            authors=PAPERS["10.1000/alpha"]["authors"], year="2024"),
         "claim_id": "C-001", "evidence_id": "E-alpha01",
         "block_type": "sentence"},
    ]
    for row in evidence:
        row["access"] = "oa_licensed"
    _write_jsonl(root / "evidence" / "evidence.jsonl", evidence)
    adjudications = [
        {
            "claim_id": row["claim_id"],
            "paper_id": row["paper_id"],
            "block_id": row["block_id"],
            "quote": row["quote"],
            "stance": row["stance"],
            "evidence_kind": row["evidence_kind"],
        }
        for row in evidence
    ]
    _write_jsonl(root / "evidence" / "adjudications.jsonl", adjudications)
    from evidence_lineage import accepted, adjudication_id

    _write_jsonl(root / "evidence" / "evidence_lineage.jsonl", [
        accepted(adjudication_id(raw, ordinal), raw, evidence[ordinal - 1]["evidence_id"])
        for ordinal, raw in enumerate(adjudications, 1)
    ])
    _write_jsonl(root / "evidence" / "rejected_evidence.jsonl", [])
    _write_jsonl(root / "evidence" / "entailment.jsonl", [
        {
            "verdict_id": f"V-{row['evidence_id']}",
            "claim_id": row["claim_id"],
            "evidence_id": row["evidence_id"],
            "entailment": "yes",
            "direction_match": True,
            "population_match": True,
            "intervention_match": True,
            "outcome_match": True,
            "result_type": "original",
            "scope_overreach": False,
            "reviewer": "fixture-reviewer",
            "rationale": "Synthetic fixture anchor matches its claim.",
            "verified_at": "2026-07-27T09:30:00Z",
        }
        for row in evidence
    ])

    # --- grounded_quotes.json: the anchors, keyed by claim.
    grounded = {
        "C-001": {"support_state": "C1_SINGLE_DIRECT",
                  "supporting_anchors": [evidence[2]],
                  "contradicting_anchors": []},
        "C-003": {"support_state": "C1_SINGLE_DIRECT",
                  "supporting_anchors": [evidence[1]],
                  "contradicting_anchors": []},
        "C-005": {"support_state": "C1_SINGLE_DIRECT",
                  "supporting_anchors": [evidence[0]],
                  "contradicting_anchors": []},
    }
    _write_json(root / "deliverables" / "grounded_quotes.json", grounded)
    (root / "deliverables" / "grounded_quotes.md").write_text(
        "# Grounded quotes\n\nSynthetic fixture anchors.\n", encoding="utf-8"
    )
    (root / "deliverables" / "evidence_table.csv").write_text(
        "evidence_id,claim_id,paper_id\n"
        + "".join(
            f"{row['evidence_id']},{row['claim_id']},{row['paper_id']}\n"
            for row in evidence
        ),
        encoding="utf-8",
    )
    matrix_text = "claim_id,support_state\n" + "".join(
        f"{claim['claim_id']},C1_SINGLE_DIRECT\n" for claim in claims
    )
    (root / "deliverables" / "claim_evidence_matrix.csv").write_text(
        matrix_text, encoding="utf-8"
    )
    (root / "synthesis").mkdir(parents=True, exist_ok=True)
    (root / "synthesis" / "claim_evidence_matrix.csv").write_text(
        matrix_text, encoding="utf-8"
    )

    # --- figures. beta/fig3_p07 genuinely illustrates C-003. gamma/fig9_p12 is
    # the shipped defect in miniature: a therapeutic-strategies schematic hung
    # off a mechanism claim because its caption happened to be an anchor.
    figures_dir = root / "deliverables" / "figures_cited"
    figures_dir.mkdir(parents=True, exist_ok=True)
    figs = [{
        "paper_id": "10.1000/beta", "figure_id": "fig3_p07",
        "status": "exported", "page": 7,
        "caption": "Figure 3 Increased lysosomal biogenesis in the cerebral "
                   "cortex of aged PGRN-deficient mice.",
        "image": "10.1000_beta__fig3_p07.png",
        "claims": ["C-003"], "doi": "10.1000/beta",
        "url": "https://doi.org/10.1000/beta",
        "figure_embedding_allowed": True,
        "quality_check": {"status": "pass"},
        "selection": [{"claim_id": "C-003", "relevance": 0.5}],
    }]
    if with_bad_figure:
        figs.append({
            "paper_id": "10.1000/gamma", "figure_id": "fig9_p12",
            "status": "exported", "page": 12,
            "caption": "Fig. 9 Overview of candidate therapeutic strategies "
                       "for dementia.",
            "image": "10.1000_gamma__fig9_p12.png",
            "claims": ["C-005"], "doi": "10.1000/gamma",
            "url": "https://doi.org/10.1000/gamma",
            "figure_embedding_allowed": True,
            "quality_check": {"status": "pass"},
            "selection": [{"claim_id": "C-005", "relevance": 0.5}],
        })
    for fig in figs:
        (figures_dir / fig["image"]).write_bytes(_png())
    _write_json(figures_dir / "figures_manifest.json",
                {"figures_exported": len(figs), "figures": figs})

    # --- parsed full texts, which is where figure metadata really comes from.
    #
    # Each paper carries MORE figures than the run quoted, which is the point:
    # under the old rule only a quoted caption could reach the report, so
    # alpha/fig2 — a figure that plainly illustrates C-001 — was invisible. Also
    # present: a schematic and an off-topic figure that must stay out.
    parsed_dir = root / "fulltext" / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    # (figure_id, caption, ocr_lines). The OCR lines are what a real run stores
    # under a parsed figure's ``ocr`` key when configured with --ocr targeted;
    # supplying them here exercises the provenance-box path without needing the
    # EasyOCR engine (and therefore torch) installed to run the tests.
    parsed_figures = {
        "10.1000/alpha": [
            ("fig2_p04", "Figure 2 Plasma progranulin is reduced in "
                         "heterozygous GRN loss-of-function mutation carriers "
                         "with frontotemporal dementia.",
             [_ocr("progranulin (ng/ml)", 40, 300, 150, 320),
              _ocr("GRN carriers", 200, 360, 300, 378),
              _ocr("p < 0.001", 320, 120, 390, 138),
              _ocr("n.s.", 470, 130, 505, 146)]),
            ("fig5_p09", "Figure 5 Schematic of the proposed therapeutic "
                         "strategy.", []),
        ],
        "10.1000/beta": [
            ("fig3_p07", "Figure 3 Increased lysosomal biogenesis in the "
                         "cerebral cortex of aged PGRN-deficient mice.",
             [_ocr("Lamp1", 30, 40, 90, 58),
              _ocr("lysosomal area", 120, 250, 250, 270),
              _ocr("WT", 300, 360, 330, 378),
              _ocr("KO", 360, 360, 392, 378)]),
            ("fig1_p03", "Figure 1 Body weight of wild-type and knockout "
                         "animals over 90 weeks.",
             [_ocr("body weight (g)", 30, 200, 150, 220)]),
        ],
        "10.1000/gamma": [
            ("fig9_p12", "Fig. 9 Overview of candidate therapeutic strategies "
                         "for dementia.", []),
        ],
    }
    figures_root = root / "fulltext" / "figures"
    for pid, entries in parsed_figures.items():
        paper_dir = figures_root / _safe(pid)
        paper_dir.mkdir(parents=True, exist_ok=True)
        records = []
        for fid, caption, ocr_lines in entries:
            image = paper_dir / f"{fid}.png"
            image.write_bytes(_png())
            records.append({"figure_id": fid, "caption": caption,
                            "image_path": str(image),
                            "page": int(fid.split("_p")[-1]),
                            "ocr": ocr_lines})
        _write_json(parsed_dir / f"{_safe(pid)}.json",
                    {"paper_id": pid, "figures": records})
    _write_jsonl(root / "evidence" / "figure_entailment.jsonl", [{
        "paper_id": "10.1000/alpha",
        "figure_id": "fig2_p04",
        "claim_id": "C-001",
        "entails": True,
        "direction_match": True,
        "model_match": True,
        "outcome_match": True,
        "subject_match": True,
        "crop_complete": True,
        "labels_legible": True,
        "no_page_contamination": True,
        "reviewer": "fixture-visual-reviewer",
        "rationale": "The plotted GRN-carrier measurement directly depicts C-001.",
    }, {
        "paper_id": "10.1000/beta",
        "figure_id": "fig3_p07",
        "claim_id": "C-003",
        "entails": True,
        "direction_match": True,
        "model_match": True,
        "outcome_match": True,
        "subject_match": True,
        "crop_complete": True,
        "labels_legible": True,
        "no_page_contamination": True,
        "reviewer": "fixture-visual-reviewer",
        "rationale": "The complete crop directly depicts C-003.",
    }])
    _write_jsonl(root / "fulltext" / "parse_quality.jsonl", [
        {
            "schema_version": 1,
            "paper_id": pid,
            "state": "figure_only",
            "parser": "fixture",
            "sentence_count": 0,
            "substantive_sentence_count": 0,
            "minimum_substantive_sentences": 20,
            "figure_count": len(entries),
            "nonempty_figure_count": len(entries),
            "reason": "synthetic fixture stores only figure metadata",
            "recovery_attempts": [],
        }
        for pid, entries in parsed_figures.items()
    ])

    # --- narratives + prose sections
    if with_narratives:
        _write_jsonl(root / "deliverables" / "claim_narratives.jsonl", [
            {"claim_id": c["claim_id"],
             "observed_result": {
                 "text": f"Observed finding for {c['claim_id']}.",
                 "evidence_ids": [eid]},
             "reviewer_inference": {
                 "text": "Reviewer reading of the same result.",
                 "inference": True}}
            for c, eid in zip(claims, ["E-alpha01", "E-beta01", "E-gamma01"])
        ])
    _write_json(root / "deliverables" / "report_sections.json", {
        key: [{"text": f"{key.replace('_', ' ').capitalize()} paragraph.",
               "inference": True}]
        for key in ("introduction", "methods", "conclusions", "next_steps")
    })

    # The report opens with a 3-panel infographic. A real one is rendered from
    # deliverables/infographic_spec.json by an image model; the fixture writes a
    # placeholder PNG so the embed, caption and contract-marker paths are
    # exercised without a network call.
    (root / "deliverables" / "infographic.png").write_bytes(_png(1536, 1024))

    provenance = _fixture_provenance()
    manifest_path = root / "run_manifest.json"
    _write_json(
        manifest_path,
        {
            "title": "GRN as a therapeutic target in frontotemporal dementia",
            "question": "What is the evidence for GRN as a therapeutic target?",
            "mode": mode,
            "skill_provenance": provenance,
            "config": {
                "minimum_paper_figures": len(figs),
                "ocr": "targeted" if figs else "off",
                "ocr_decision_source": "explicit_user" if figs else "no_figures",
                "figure_reuse_policy": "reuse_cleared_only",
                "figure_reuse_decision_source": (
                    "explicit_user" if figs else "no_figures"
                ),
                "adaptive_managed_concurrency": False,
                "managed_execution_waiver": {
                    "approved_by_user": True,
                    "reason": "synthetic fixture does not provision managed machines",
                },
            },
            "run_started_utc": "2026-07-27T09:00:00Z",
            # Header fields the infographic spec seeds from.
            "subject": "GRN",
            "subject_long": "progranulin",
            "subject_class": "Secreted growth factor · lysosomal regulator",
            "context": "frontotemporal dementia",
        },
    )
    from intake_policy import ensure_intake_snapshot

    ensure_intake_snapshot(
        manifest_path,
        json.loads(manifest_path.read_text(encoding="utf-8")),
    )
    _write_json(root / "state" / "skill_provenance.json", provenance)
    _write_json(root / "deliverables" / "review_stats.json", {
        "review_mode": mode, "papers_full_text": 3,
    })
    from corpus_ledger import REQUIRED_BROAD_AXES, refresh

    _write_json(root / "corpus" / "coverage_matrix.json", {
        "axes": [
            {"axis": axis, "status": "searched_with_evidence",
             "queries": [f"GRN {axis}"]}
            for axis in REQUIRED_BROAD_AXES
        ]
    })
    _write_json(root / "fulltext" / "global_transient_retry.json", {
        "completed": True, "attempted": 0, "recovered": 0, "remaining": 0,
        "reason": "no transient retrieval failures remained after merge",
    })
    _write_jsonl(root / "fulltext" / "acquisition_routes.jsonl", [
        {
            "schema_version": 1,
            "paper_id": pid,
            "outcome": "parsed",
            "access_state": "oa_licensed",
            "attempts": [{"source": "fixture", "reason": "retrieved"}],
            "final_reason": "",
            "user_supplied": False,
        }
        for pid in PAPERS
    ])
    refresh(root, final=True)
    return root


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    import sys
    dest = build(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "./_fixture"))
    print(f"fixture run written to {dest}")
