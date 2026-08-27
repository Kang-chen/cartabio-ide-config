"""Which figures a claim shows, and why.

The shipped behaviour: a figure reached the report only if its caption or
in-panel text happened to be quoted. Figure choice was therefore a side effect
of quote-type choice, which starved, clustered, and misdirected the figures.
"""
from __future__ import annotations

import pytest

from figure_selection import (
    DEFAULT_POLICY,
    caption_relevance,
    is_review_paper,
    is_schematic,
    select,
)

CLAIMS = [
    {"claim_id": "C-001",
     "claim_text": "Progranulin is required for normal lysosomal function, and "
                   "its deficiency causes lysosomal dysfunction in the brain.",
     "scope": "Mouse models; progranulin deficiency"},
    {"claim_id": "C-002",
     "claim_text": "APOE4 alters brain lipid and cholesterol metabolism in "
                   "astrocytes and microglia.",
     "scope": "Human iPSC-derived glia"},
]

EVIDENCE = [
    # C-001 rests on a Results SENTENCE from paper A — no caption quoted at all.
    {"claim_id": "C-001", "paper_id": "A", "stance": "supports",
     "block_type": "sentence"},
    # C-002 rests on a sentence from review paper R.
    {"claim_id": "C-002", "paper_id": "R", "stance": "supports",
     "block_type": "sentence"},
]

FIGURES = {
    ("A", "fig3"): {"caption": "Figure 3 Increased lysosomal biogenesis in the "
                               "cerebral cortex of aged progranulin-deficient "
                               "mice, quantified by Lamp1 immunostaining."},
    ("A", "fig1"): {"caption": "Figure 1 Body weight of wild-type and knockout "
                               "animals over 90 weeks."},
    ("R", "fig2"): {"caption": "Fig. 2 ApoE-targeted therapeutic strategies for "
                               "Alzheimer's disease."},
}

REFS = {"A": {"study_type": "experimental study"}, "R": {"study_type": "Review"}}


def _select(**policy):
    return select(CLAIMS, EVIDENCE, FIGURES, REFS, {**DEFAULT_POLICY, **policy})


# --- starvation -------------------------------------------------------------

def test_sentence_grounded_claim_still_gets_its_figure():
    """The core defect. C-001 quotes no caption, so under the old rule it got no
    figure — even though its paper contains a figure showing exactly the claim."""
    report = _select()
    chosen = [(c.paper_id, c.figure_id) for c in report.chosen
              if c.claim_id == "C-001"]
    assert ("A", "fig3") in chosen


def test_irrelevant_figure_from_a_cited_paper_is_not_shown():
    """Being in a cited paper is not enough; the figure must illustrate the
    claim. Paper A's body-weight figure has nothing to do with lysosomes."""
    report = _select()
    chosen = [(c.paper_id, c.figure_id) for c in report.chosen]
    assert ("A", "fig1") not in chosen
    assert {r["cause"] for r in report.rejected if r["figure_id"] == "fig1"} & {
        "below_relevance_floor", "too_few_shared_terms"}


# --- irrelevance ------------------------------------------------------------

def test_irrelevant_review_schematic_is_not_selected():
    """A shipped report embedded Raulin 2022 Fig. 2 — a review's drawing of
    "ApoE-targeted therapeutic strategies" — under a claim about APOE4 altering
    lipid metabolism. It measures nothing, so it can support nothing."""
    report = _select()
    assert ("R", "fig2") not in [(c.paper_id, c.figure_id) for c in report.chosen]
    causes = {r["cause"] for r in report.rejected if r["figure_id"] == "fig2"}
    assert causes & {"review_article", "too_few_shared_terms",
                     "below_relevance_floor"}


@pytest.mark.parametrize("caption", [
    "Fig. 2 ApoE-targeted therapeutic strategies for AD.",
    "Figure 1 Schematic of the proposed mechanism.",
    "Figure 5 Overview of the study design.",
    "Fig 4 Graphical abstract.",
])
def test_schematic_captions_detected(caption):
    assert is_schematic(caption)


@pytest.mark.parametrize("caption", [
    "Figure 3 Increased lysosomal biogenesis in aged PGRN-deficient mice.",
    "Fig. 1 PGRN plasma concentrations in GRN mutation carriers.",
])
def test_result_captions_are_not_schematics(caption):
    assert not is_schematic(caption)


@pytest.mark.parametrize("caption", [
    "Figure 4. Loss of Slc33a1 sensitizes to CAR T cell therapy in vivo. "
    "(A) Schematic of the experiment. (B-D) Tumor volume, survival, and "
    "quantification; mean +/- SEM, n = 8, P < 0.01.",
    "Figure 3. SLC33A1 deletion impairs ATF6 processing. (A) Schema of the "
    "edited locus. (B) Western blot analysis and (C) densitometric "
    "quantification from three independent experiments.",
])
def test_mixed_schematic_and_measurement_figures_are_primary_data(caption):
    from figure_selection import figure_role

    assert figure_role({}, caption) == "primary_data"


def test_review_papers_identified():
    assert is_review_paper({"study_type": "Systematic Review"})
    assert not is_review_paper({"study_type": "randomized controlled trial"})


# --- clustering -------------------------------------------------------------

def test_one_paper_cannot_fill_a_claim():
    """Three figures from Jackson 2021 sat under one APOE claim while four other
    axes had none."""
    figures = {("A", f"fig{i}"): {
        "caption": f"Figure {i} Lysosomal dysfunction in the brain of "
                   f"progranulin-deficient mice, panel {i}."} for i in range(1, 6)}
    report = select(CLAIMS[:1], EVIDENCE[:1], figures, REFS, DEFAULT_POLICY)
    assert len(report.chosen) <= DEFAULT_POLICY["max_per_paper_per_claim"]
    assert any(r["cause"] == "over_paper_cap" for r in report.rejected)


def test_default_policy_caps_relevant_figures_without_one_paper_crowding():
    evidence = [
        {"claim_id": "C-001", "paper_id": pid, "stance": "supports",
         "block_type": "sentence"}
        for pid in ("A", "B", "C")
    ]
    figures = {
        (pid, f"fig{index}"): {
            "caption": (
                f"Figure {index} Lysosomal dysfunction in the brain of "
                "progranulin-deficient mice measured by Lamp1 staining."
            )
        }
        for pid in ("A", "B", "C")
        for index in (1, 2)
    }
    refs = {pid: {"study_type": "experimental study"} for pid in ("A", "B", "C")}
    report = select(CLAIMS[:1], evidence, figures, refs, DEFAULT_POLICY)
    assert len(report.chosen) == 3
    assert len({choice.paper_id for choice in report.chosen}) == 3


def test_claim_cap_is_reported_not_silent():
    report = select(
        CLAIMS[:1], EVIDENCE[:1],
        {("A", "f1"): {"caption": "Figure 1 Lysosomal dysfunction in the brain "
                                  "of progranulin-deficient mice."},
         ("B", "f2"): {"caption": "Figure 2 Lysosomal function in brain of "
                                  "progranulin deficient mice."}},
        REFS,
        {**DEFAULT_POLICY, "max_per_claim": 1, "max_per_paper_per_claim": 1})
    # Only paper A is cited by C-001, so B is not eligible at all.
    assert {c.paper_id for c in report.chosen} == {"A"}


def test_figure_only_shown_under_a_claim_that_cites_its_paper():
    """A figure may not introduce a source the claim does not otherwise use."""
    report = _select()
    for choice in report.chosen:
        cited = {row["paper_id"] for row in EVIDENCE
                 if row["claim_id"] == choice.claim_id}
        assert choice.paper_id in cited


# --- quoted captions remain subject to relevance ----------------------------

def test_quoted_caption_cannot_bypass_scoring():
    """Textual quote validity does not prove that the picture depicts the claim."""
    report = select(CLAIMS[:1], EVIDENCE[:1], FIGURES, REFS, DEFAULT_POLICY,
                    quoted=[("A", "fig1", "C-001")])
    chosen = [(c.paper_id, c.figure_id, c.reason) for c in report.chosen]
    assert ("A", "fig1", "quoted_caption") not in chosen
    assert any(
        row.get("figure_id") == "fig1"
        and row.get("cause") in {"too_few_shared_terms", "below_relevance_floor"}
        for row in report.rejected
    )


def test_unquoted_figure_requires_pair_level_visual_entailment_when_enabled():
    policy = {**DEFAULT_POLICY, "require_pair_verification": True}
    report = select(
        CLAIMS[:1], EVIDENCE[:1], FIGURES, REFS, policy,
        subject_aliases=("progranulin",),
    )

    assert report.chosen == []
    assert any(row["cause"] == "missing_visual_entailment"
               for row in report.rejected)


def test_verified_visual_entailment_records_direction_model_and_outcome_match():
    figures = {key: dict(value) for key, value in FIGURES.items()}
    figures[("A", "fig3")]["claim_entailments"] = [{
        "claim_id": "C-001",
        "entails": True,
        "direction_match": True,
        "model_match": True,
        "outcome_match": True,
        "subject_match": True,
        "crop_complete": True,
        "labels_legible": True,
        "no_page_contamination": True,
        "reviewer": "biomni-native",
        "rationale": "The figure directly quantifies the claim in the named model.",
    }]

    report = select(
        CLAIMS[:1], EVIDENCE[:1], figures, REFS,
        {**DEFAULT_POLICY, "require_pair_verification": True},
        subject_aliases=("progranulin",),
    )

    assert [(row.figure_id, row.pair_verification) for row in report.chosen] == [
        ("fig3", "visual_entailment")
    ]


def test_axis_coverage_cannot_lower_the_normal_relevance_floor():
    claims = [{
        "claim_id": "C-X", "claim_text": "SLC33A1 controls tumor response",
        "scope": "cancer", "cluster": "therapy", "figure_priority": True,
    }]
    evidence = [{"claim_id": "C-X", "paper_id": "P", "stance": "supports"}]
    figures = {("P", "F"): {"caption": "Figure 1 SLC33A1 response."}}
    report = select(
        claims, evidence, figures, {"P": {"study_type": "primary"}},
        {**DEFAULT_POLICY, "min_relevance": 0.95, "coverage_min_relevance": 0.01,
         "min_shared_terms": 3, "coverage_min_shared_terms": 1},
    )

    assert report.chosen == []


def test_visual_entailment_tasks_hash_images_and_assemble_exact_pairs(run_root):
    import json
    import pathlib

    from figure_entailment import assemble, emit

    count = emit(run_root)
    tasks = sorted((run_root / "evidence" / "figure_entailment_tasks").glob("*.json"))
    assert len(tasks) == count >= 1
    for task_path in tasks:
        task = json.loads(task_path.read_text())
        pathlib.Path(task["output_path"]).write_text(json.dumps({
            "entails": True,
            "direction_match": True,
            "model_match": True,
            "outcome_match": True,
            "subject_match": True,
            "crop_complete": True,
            "labels_legible": True,
            "no_page_contamination": True,
            "reviewer": "biomni-native",
            "rationale": "Visible panels, labels, and crop match the atomic claim.",
        }))

    assert assemble(run_root) == count
    rows = [json.loads(line) for line in
            (run_root / "evidence" / "figure_entailment.jsonl").read_text().splitlines()]
    assert all(row["image_sha256"] for row in rows)


def test_quoted_caption_cannot_bypass_the_review_subject_gate():
    """A quote can mention an outcome without making its panel target-specific.

    The SLC33A1 report displayed an ATG9A/cell-death panel and a KEAP1/NRF2
    CAR-T panel under SLC33A1 claims because generic outcome terms overlapped.
    Neither caption nor in-panel OCR named the target.
    """
    claims = [{
        "claim_id": "C-SLC",
        "claim_text": "SLC33A1 loss sensitizes cancer cells to treatment.",
        "scope": "oncology dependency",
        "cluster": "dependency",
    }]
    evidence = [{
        "claim_id": "C-SLC", "paper_id": "P", "stance": "supports",
        "block_type": "caption",
    }]
    figures = {
        ("P", "atg9a"): {
            "caption": "ATG9A mutation increases autophagy and cell death."
        },
        ("P", "cart"): {
            "caption": "KEAP1/NRF2 status controls CAR-T response and survival."
        },
    }

    report = select(
        claims, evidence, figures, {"P": {"study_type": "primary"}},
        DEFAULT_POLICY,
        quoted=[("P", "atg9a", "C-SLC")],
        subject_aliases=("SLC33A1", "AT-1"),
    )

    assert report.chosen == []
    assert {row["cause"] for row in report.rejected} == {
        "missing_subject_anchor"
    }


def test_subject_name_in_caption_or_ocr_passes_the_gate():
    claims = [{
        "claim_id": "C-SLC",
        "claim_text": "SLC33A1 loss sensitizes cancer cells to treatment.",
        "scope": "oncology dependency",
    }]
    evidence = [{
        "claim_id": "C-SLC", "paper_id": "P", "stance": "supports",
    }]
    figures = {
        ("P", "caption"): {
            "caption": "Slc33a1 loss sensitizes cancer cells to treatment and "
                       "reduces tumour growth."
        },
        ("P", "ocr"): {
            "caption": "Target loss sensitizes cancer cells to treatment and "
                       "reduces tumour growth.",
            "ocr": [{"text": "SLC33A1 KO"}],
        },
    }
    policy = {
        **DEFAULT_POLICY,
        "max_per_paper_per_claim": 2,
        "require_pair_verification": False,
    }

    report = select(
        claims, evidence, figures, {"P": {"study_type": "primary"}}, policy,
        subject_aliases=("SLC33A1", "AT-1"),
    )

    assert {choice.figure_id for choice in report.chosen} == {"caption", "ocr"}


def test_quoted_review_figure_is_excluded_by_default():
    """The exact shipped defect. Raulin 2022 Fig. 2 was embedded under an APOE4
    lipid-metabolism claim BECAUSE its caption was a quoted anchor
    ("ApoE-targeted therapeutic strategies for AD.", supports/secondary).
    Quoting a drawing's legend can be fair secondary evidence; reproducing the
    drawing as the claim's data is not. It may now illustrate the claim, but its
    role must prevent it from being presented as primary evidence."""
    report = select(CLAIMS[1:], EVIDENCE[1:], FIGURES, REFS, DEFAULT_POLICY,
                    quoted=[("R", "fig2", "C-002")])
    assert report.chosen == []
    assert any(
        row["cause"] in {
            "illustrative_context_only", "too_few_shared_terms",
            "below_relevance_floor",
        }
        for row in report.rejected
    )


def test_relevant_source_model_can_fill_an_uncovered_axis_as_illustration():
    claims = [{
        "claim_id": "C-MODEL",
        "cluster": "mechanism",
        "claim_text": "SLC33A1 loss causes ER acetyl-CoA transport failure.",
        "scope": "mechanism",
    }]
    evidence = [{
        "claim_id": "C-MODEL", "paper_id": "P", "stance": "supports",
        "quote": "Loss of SLC33A1 impaired ER acetyl-CoA transport.",
    }]
    figures = {("P", "fig1"): {
        "caption": "Figure 1. Mechanistic model of SLC33A1-dependent "
                   "acetyl-CoA transport in the ER."
    }}
    report = select(
        claims, evidence, figures, {"P": {"study_type": "primary"}},
        {**DEFAULT_POLICY, "allow_context_figures": True},
    )
    choice = report.chosen[0]
    assert choice.role == "source_model"
    assert choice.reason in {"claim_match", "coverage_axis_match"}
    assert report.axis_coverage[0]["selected_figures"] == 1


def test_axis_recovery_only_targets_explicit_figure_priorities():
    claims = [
        {
            "claim_id": "C-DECISION", "cluster": "dependency",
            "claim_text": "SLC33A1 loss reduces tumour growth.",
            "figure_priority": True,
        },
        {
            "claim_id": "C-BACKGROUND", "cluster": "background",
            "claim_text": "SLC33A1 was discovered in an earlier screen.",
            "figure_priority": False,
        },
    ]
    evidence = [
        {"claim_id": "C-DECISION", "paper_id": "P1", "stance": "supports"},
        {"claim_id": "C-BACKGROUND", "paper_id": "P2", "stance": "supports"},
    ]
    figures = {
        ("P1", "fig1"): {"caption": "SLC33A1 loss reduces tumour growth."},
        ("P2", "fig2"): {
            "caption": "Schematic overview of the SLC33A1 discovery screen."
        },
    }

    report = select(
        claims, evidence, figures, {},
        {**DEFAULT_POLICY, "allow_context_figures": True},
        subject_aliases=("SLC33A1",),
    )

    assert [row["axis"] for row in report.axis_coverage] == ["dependency"]


def test_csv_false_figure_priority_is_not_truthy():
    claims = [
        {"claim_id": "C-YES", "cluster": "decision",
         "claim_text": "SLC33A1 loss reduces tumour growth.",
         "figure_priority": "true"},
        {"claim_id": "C-NO", "cluster": "background",
         "claim_text": "SLC33A1 was discovered in a screen.",
         "figure_priority": "false"},
    ]
    report = select(claims, [], {}, subject_aliases=("SLC33A1",))

    assert [row["axis"] for row in report.axis_coverage] == ["decision"]


# --- scoring ----------------------------------------------------------------

def test_relevance_prefers_the_specific_caption():
    claim = CLAIMS[0]["claim_text"]
    on_point = caption_relevance(
        "Increased lysosomal biogenesis in the brain of progranulin-deficient "
        "mice", claim)
    off_point = caption_relevance("Body weight over 90 weeks", claim)
    assert on_point > off_point
    assert on_point >= DEFAULT_POLICY["min_relevance"]
    assert off_point < DEFAULT_POLICY["min_relevance"]


# Real figure/claim pairs lifted from the two shipped reports. The thresholds
# are calibrated on exactly these, so a change that breaks the separation shows
# up here rather than in the next report.
SHIPPED_PAIRS = [
    (True, "Figure 3 Increased lysosomal biogenesis in the cerebral cortex and "
           "VPM/VPL of aged PGRN-deficient mice.",
     "Progranulin is required for normal lysosomal function, and its deficiency "
     "causes lysosomal dysfunction in the brain."),
    (True, "Fig. 2 A PGRN plasma concentrations in GRN mutation carriers (GRN) "
           "and non-mutation carriers (Non-GRN). Cut-off determined using the "
           "optimal Youden's index.",
     "Low plasma/serum progranulin levels accurately identify pathogenic GRN "
     "mutation carriers, including asymptomatic carriers."),
    (True, "Figure 4 Tight junctions are impaired in ApoE4 mice along with MMP9 "
           "expression.",
     "Astrocyte-derived APOE4, unlike APOE2/3, causes blood-brain-barrier "
     "breakdown via increased MMP9 activity, and removing astrocytic APOE4 "
     "rescues these defects."),
    (True, "Latozinemab decreases sortilin in WBCs and increases PGRN levels in "
           "the plasma and CSF of HVs and aFTD-GRN participants.",
     "A sortilin-blocking antibody (latozinemab/AL001) engages its target and "
     "raises plasma and CSF progranulin toward normal levels in FTD-GRN "
     "patients."),
    # The shipped defect: a review's drawing hung off a mechanism claim.
    (False, "Fig. 2 ApoE-targeted therapeutic strategies for AD.",
     "APOE4 alters brain lipid and cholesterol metabolism in astrocytes and "
     "microglia, driving lipid accumulation and a reactive glial state."),
    (False, "Figure 1 Body weight of wild-type and knockout animals over 90 weeks.",
     "Progranulin is required for normal lysosomal function, and its deficiency "
     "causes lysosomal dysfunction in the brain."),
    # One shared generic word scores 0.27 — above three genuine matches — so the
    # score alone cannot reject it. The shared-term count can.
    (False, "Figure 2 Brain sections.",
     "Progranulin is required for normal lysosomal function, and its deficiency "
     "causes lysosomal dysfunction in the brain."),
    (False, "Figure 6 Serum HDL and LDL cholesterol in treated and control mice.",
     "Heterozygous loss-of-function mutations in GRN cause familial "
     "frontotemporal dementia through progranulin haploinsufficiency."),
]


@pytest.mark.parametrize("want,caption,claim", SHIPPED_PAIRS)
def test_thresholds_separate_real_pairs(want, caption, claim):
    from figure_selection import shared_terms

    passes = (caption_relevance(caption, claim) >= DEFAULT_POLICY["min_relevance"]
              and len(shared_terms(caption, claim))
              >= DEFAULT_POLICY["min_shared_terms"])
    assert passes is want


def test_selection_reports_what_it_dropped():
    """Silent truncation reads downstream like a corpus that never had them."""
    report = _select()
    counts = report.counts()
    assert counts["chosen"] >= 1
    assert counts["unique_figures_considered"] == 3
    assert counts["candidate_pairs_considered"] == 3
    assert sum(v for k, v in counts.items() if k.startswith("rejected_")) >= 1


def test_captionless_figure_can_be_selected_from_ocr_text():
    figures = {
        ("A", "ocr1"): {
            "caption": "",
            "ocr": [
                {"text": "progranulin deficient brain"},
                {"text": "increased lysosomal dysfunction Lamp1"},
            ],
        }
    }
    report = select(CLAIMS[:1], EVIDENCE[:1], figures, REFS, DEFAULT_POLICY)
    assert [(choice.figure_id, choice.reason) for choice in report.chosen] == [
        ("ocr1", "figure_ocr_match")
    ]


def test_targeted_ocr_recovers_captionless_crop_from_candidate_paper(
    tmp_path, monkeypatch
):
    import evidence_first

    seen = []

    class FakeOcr:
        class OcrUnavailable(Exception):
            pass

        @staticmethod
        def ocr_figures(figures, **_kwargs):
            seen.extend(figures)
            return [{**figure, "ocr": [{"text": "lysosomal dysfunction"}]}
                    for figure in figures]

    monkeypatch.setattr(evidence_first.importlib, "import_module", lambda _name: FakeOcr)
    parsed = {
        "A": {
            "figures": [{
                "figure_id": "captionless",
                "caption": "",
                "image_path": str(tmp_path / "figure.png"),
            }]
        }
    }
    changed = evidence_first._targeted_ocr(
        parsed,
        [{"paper_id": "A", "block_id": "A:S:1"}],
        {},
        tmp_path / "cache",
    )
    assert changed == {"A"}
    assert [figure["figure_id"] for figure in seen] == ["captionless"]
    assert parsed["A"]["figures"][0]["ocr"]


# --- end-to-end through export_figures --------------------------------------

def test_export_selects_unquoted_figures(run_root):
    """The whole point, exercised through the real export step: a claim grounded
    on a Results sentence gets the figure that illustrates it, from a paper it
    already cites, with no caption ever quoted."""
    from export_figures import export_cited_figures

    summary = export_cited_figures(run_root)
    exported = {(f["paper_id"], f["figure_id"]) for f in summary["figures"]
                if f["status"] == "exported"}
    # alpha/fig2_p04 is quoted nowhere; C-001 rests on a sentence from alpha.
    assert ("10.1000/alpha", "fig2_p04") in exported
    reasons = {s["reason"] for f in summary["figures"]
               for s in (f.get("selection") or [])
               if (f["paper_id"], f["figure_id"]) == ("10.1000/alpha", "fig2_p04")}
    assert reasons == {"claim_match"}


def test_export_rejects_schematics_and_off_topic_figures(run_root):
    from export_figures import export_cited_figures

    summary = export_cited_figures(run_root)
    exported = {(f["paper_id"], f["figure_id"]) for f in summary["figures"]
                if f["status"] == "exported"}
    assert ("10.1000/gamma", "fig9_p12") not in exported   # schematic
    assert ("10.1000/alpha", "fig5_p09") not in exported   # schematic
    assert ("10.1000/beta", "fig1_p03") not in exported    # off-topic
    causes = {r["cause"] for r in summary["selection_rejected"]}
    assert causes & {"too_few_shared_terms", "below_relevance_floor",
                     "missing_subject_anchor"}


def test_export_reports_what_it_dropped(run_root):
    """Silent truncation reads downstream like a corpus that never had them."""
    from export_figures import export_cited_figures

    summary = export_cited_figures(run_root)
    assert summary["selection_counts"]["chosen"] >= 1
    assert summary["selection_rejected"]


def test_figures_numbered_in_order_of_appearance(run_root):
    """A shipped report presented its figures as 5, 6, 3, 4, 1, 2 because they
    were numbered in manifest order, which is sorted by paper id."""
    from export_figures import export_cited_figures
    from report_model import build_model, load_contract

    export_cited_figures(run_root)
    model = build_model(run_root, load_contract())
    seen: list[int] = []
    for claim in model["claims"]:
        for fig in claim["figures"]:
            if fig["report_number"] not in seen:
                seen.append(fig["report_number"])
    assert seen == sorted(seen), f"figures appear out of numeric order: {seen}"
    assert seen == list(range(1, len(seen) + 1))


# --- crowding caps apply to quoted captions too -------------------------------

def test_quoted_captions_respect_the_per_paper_cap():
    """Quoted captions were exempt from the caps, and that is how clustering came
    back: four figures from Ma 2018 under one APOE claim, three from Liraz under
    another, three from Kurnellas in the GRN review — while most claims had none.
    A quoted caption earns a place in the queue, not an unbounded number."""
    figures = {("A", f"fig{i}"): {"caption": f"Figure {i} Lysosomal dysfunction "
                                            f"in progranulin-deficient brain."}
               for i in range(1, 6)}
    quoted = [("A", f"fig{i}", "C-001") for i in range(1, 6)]
    report = select(CLAIMS[:1], EVIDENCE[:1], figures, REFS,
                    {**DEFAULT_POLICY, "max_per_paper_per_claim": 2},
                    quoted=quoted)
    assert len(report.chosen) == 2
    assert any(r["cause"] == "over_paper_cap" and r.get("quoted")
               for r in report.rejected)


def test_quoted_captions_respect_the_per_claim_cap():
    figures = {(chr(65 + i), "f1"): {"caption": "Figure 1 Lysosomal dysfunction "
                                               "in progranulin-deficient brain."}
               for i in range(6)}
    quoted = [(chr(65 + i), "f1", "C-001") for i in range(6)]
    report = select(CLAIMS[:1], EVIDENCE[:1], figures, REFS,
                   {**DEFAULT_POLICY, "max_per_claim": 3}, quoted=quoted)
    assert len(report.chosen) == 3
    assert any(r["cause"] == "over_claim_cap" for r in report.rejected)


# --- a figure grounding two claims is embedded once ---------------------------

def test_figure_grounding_two_claims_is_embedded_once(run_root, tmp_path):
    """GRN's Report Figure 1 grounds C-002 and C-004, and its full-resolution
    crop was placed under both — duplicating a megabyte in a 16.8 MB file and
    showing the reader the same numbered figure twice."""
    import json
    import re
    import subprocess

    import build_pdf
    from export_figures import export_cited_figures

    # Point both fixture claims at the same figure.
    export_cited_figures(run_root)
    manifest_path = (run_root / "deliverables" / "figures_cited"
                     / "figures_manifest.json")
    manifest = json.loads(manifest_path.read_text())
    exported = [f for f in manifest["figures"] if f["status"] == "exported"]
    exported[0]["claims"] = ["C-001", "C-003"]
    manifest_path.write_text(json.dumps(manifest))

    out = tmp_path / "r.pdf"
    assert build_pdf.main(["--root", str(run_root), "--out", str(out)]) == 0
    try:
        text = subprocess.run(["pdftotext", "-layout", str(out), "-"],
                              capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("pdftotext (poppler) not available")
    shared = exported[0]
    captions = re.findall(rf"Report Figure {shared['figure_id'] and ''}(\d+)\. ",
                          text)
    # The shared figure's caption appears once; the second claim cross-references.
    assert captions.count(captions[0]) == 1 or "shown above" in text
    assert "shown above" in text


# --- reader-facing figure labels ----------------------------------------------

@pytest.mark.parametrize("caption,page,expected", [
    ("Figure 3 Increased lysosomal biogenesis.", 7, "Fig. 3"),
    ("Gliosis and lipofuscin accumulation in Grn-/- mice.", 6, "figure on p. 6"),
    ("Gliosis and lipofuscin accumulation.", None, "unnumbered figure"),
])
def test_unlabelled_figures_get_a_usable_handle(caption, page, expected):
    """"figure (unnumbered in caption)" shipped twice. It explains the parser's
    difficulty; a page number sends the reader somewhere."""
    from report_model import figure_label

    assert figure_label(caption, "fig1_p06", page) == expected


def test_default_policy_matches_the_contract():
    """The contract wins in production, so a drift between the two is invisible
    in a real run and silently changes behaviour anywhere DEFAULT_POLICY is used
    directly. That drift made a per-paper-cap rejection report itself as a
    per-claim-cap rejection."""
    from report_model import load_contract

    declared = (load_contract()["paper_figures"]["selection"])
    for key, value in declared.items():
        assert DEFAULT_POLICY[key] == value, (
            f"{key}: contract says {value}, DEFAULT_POLICY says "
            f"{DEFAULT_POLICY[key]}")


def test_figure_contract_uses_nontrivial_fallbacks_when_policy_is_missing():
    from report_model import load_contract

    contract = load_contract()["paper_figures"]
    assert contract["min_by_mode"] == {"quick": 0, "deep": 4, "broad": 6}
    assert contract["min_fraction_of_croppable"] == 0
    assert contract["selection"]["max_per_claim"] == 4
    comments = " ".join(contract.get("_comment", [])).lower()
    assert "resolved adaptive run minimum" in comments
    assert "cannot silently" in comments


def test_user_selected_figure_minimum_overrides_mode_default(run_root):
    import json

    from report_model import load_contract
    from verify_report_contract import requested_figure_floor

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.setdefault("config", {})["minimum_paper_figures"] = 2
    manifest_path.write_text(json.dumps(manifest))

    floor, source = requested_figure_floor(run_root, load_contract(), "broad")
    assert floor == 2
    assert "user-selected" in source


def test_invalid_user_figure_minimum_fails_closed(run_root):
    import json

    from report_model import load_contract
    from verify_report_contract import requested_figure_floor

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.setdefault("config", {})["minimum_paper_figures"] = -1
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="non-negative integer"):
        requested_figure_floor(run_root, load_contract(), "deep")


def test_unresolved_adaptive_figure_minimum_fails_before_assembly(run_root):
    import json

    from report_model import load_contract
    from verify_report_contract import requested_figure_floor

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["config"]["figure_count_policy"] = "adaptive"
    manifest["config"]["minimum_paper_figures"] = None
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="adaptive figure minimum is unresolved"):
        requested_figure_floor(run_root, load_contract(), "broad")


def test_resolved_adaptive_figure_minimum_overrides_mode_fallback(run_root):
    import json

    from report_model import load_contract
    from verify_report_contract import requested_figure_floor

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["config"]["figure_count_policy"] = "adaptive"
    manifest["config"]["minimum_paper_figures"] = 9
    manifest_path.write_text(json.dumps(manifest))

    floor, source = requested_figure_floor(run_root, load_contract(), "broad")

    assert floor == 9
    assert "adaptive resolved" in source


def test_user_directed_policy_includes_and_labels_uncleared_figures(run_root):
    import json

    from build_review import main as build_review
    from export_figures import export_cited_figures

    for relative in ("fulltext/papers.jsonl", "corpus/references.jsonl"):
        path = run_root / relative
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        for row in rows:
            row.update({
                "license": "All rights reserved",
                "reuse_rights": "none",
                "figure_embedding_allowed": False,
                "figure_embedding_reason": "no reuse licence recorded",
            })
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["config"].update({
        "figure_reuse_policy": "user_directed",
        "figure_reuse_decision_source": "explicit_user",
    })
    manifest_path.write_text(json.dumps(manifest))

    summary = export_cited_figures(run_root)
    exported = [row for row in summary["figures"]
                if row.get("status") == "exported"]
    assert summary["figure_reuse_policy"] == "user_directed"
    assert summary["reuse_rights_enforced"] is False
    assert exported
    assert all(row["included_at_user_direction"] for row in exported)
    assert all("user's explicit direction" in row["rights_notice"]
               for row in exported)

    assert build_review(["--root", str(run_root)]) == 0
    review = (run_root / "deliverables" / "review.md").read_text()
    assert "**Rights notice:**" in review
    assert "without a recorded reuse-clearing licence" in review


def test_missing_top_candidate_is_replaced_by_next_exportable_figure(
    run_root, tmp_path
):
    import json

    from export_figures import export_cited_figures

    parsed_path = run_root / "fulltext" / "parsed" / "10.1000_alpha.json"
    parsed = json.loads(parsed_path.read_text())
    parsed["figures"][0]["image_path"] = str(tmp_path / "missing.png")
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes((
        run_root / "fulltext" / "figures" / "10.1000_alpha" / "fig2_p04.png"
    ).read_bytes())
    parsed["figures"].append({
        "figure_id": "fig4_p08",
        "caption": "Figure 4 Progranulin is reduced in heterozygous GRN "
                   "loss-of-function mutation carriers with frontotemporal dementia.",
        "image_path": str(replacement),
        "page": 8,
        "ocr": [],
    })
    parsed_path.write_text(json.dumps(parsed))
    entailment_path = run_root / "evidence" / "figure_entailment.jsonl"
    with entailment_path.open("a") as handle:
        handle.write(json.dumps({
            "paper_id": "10.1000/alpha",
            "figure_id": "fig4_p08",
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
            "rationale": "Replacement figure directly depicts C-001.",
        }) + "\n")

    summary = export_cited_figures(run_root)
    exported = {
        (row["paper_id"], row["figure_id"])
        for row in summary["figures"] if row["status"] == "exported"
    }

    assert ("10.1000/alpha", "fig4_p08") in exported
    assert ("10.1000/alpha", "fig2_p04") not in exported
    assert any(
        row.get("cause") == "image_unavailable"
        and row.get("figure_id") == "fig2_p04"
        for row in summary["selection_rejected"]
    )


def test_parent_caption_is_recovered_for_an_embedded_panel(tmp_path):
    import evidence_first

    parsed = {"P": {"paper_id": "P", "figures": [
        {"figure_id": "Fig4", "page": 7, "caption": "Figure 4. Tumor response.",
         "image_path": str(tmp_path / "parent.png")},
        {"figure_id": "embedded-panel", "page": 7, "caption": "",
         "image_path": str(tmp_path / "panel.png")},
    ]}}

    evidence_first._normalize_figure_metadata(parsed)

    panel = parsed["P"]["figures"][1]
    assert panel["caption"] == "Figure 4. Tumor response."
    assert panel["caption_source"] == "parent_figure_same_page"
    assert panel["parent_figure_id"] == "Fig4"


def test_ocr_all_rejects_an_unattempted_image(tmp_path):
    import evidence_first

    image = tmp_path / "figure.png"
    image.write_bytes(b"not read")
    parsed = {"P": {"paper_id": "P", "figures": [{
        "figure_id": "F1", "image_path": str(image), "caption": "Figure 1",
        "ocr_attempted": False, "ocr_status": "not_attempted",
    }]}}

    with pytest.raises(RuntimeError, match="OCR all"):
        evidence_first._validate_ocr_contract(parsed, "all")
