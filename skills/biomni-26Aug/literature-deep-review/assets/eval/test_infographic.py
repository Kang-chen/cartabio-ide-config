"""The report's opening infographic, and the rule that its numbers are the
review's own.

The sibling target-infographic skill hand-authors its spec and asks the prompt
not to fabricate. That is enough where a human is copying Open Targets scores
they are looking at. It is not enough here: an unchecked graphic on page one,
above a report where every claim carries a verbatim quote, would be the single
least auditable thing in the document.
"""
from __future__ import annotations

import json

import pytest

from infographic_spec import (
    TOOL_ASPECT_RATIO,
    TOOL_NAME,
    TOOL_SEARCH_QUERY,
    build_tool_request,
    detect_profile,
    install_image,
    record_media_check,
    seed,
    verify,
    write_tool_request,
)

REAL_PANELS = {
    "PANEL_A_TITLE": "GRN — secreted progranulin",
    "PANEL_A_DESCRIPTION": "Neuron secretes progranulin; sortilin internalises "
                           "it to the lysosome.",
    "PANEL_B_TITLE": "Haploinsufficiency drives lysosomal dysfunction",
    "PANEL_B_DESCRIPTION": "Heterozygous loss halves progranulin; lysosomal "
                           "biogenesis rises in PGRN-deficient mice.",
    "PANEL_C_TITLE": "Raising progranulin",
    "PANEL_C_DESCRIPTION": "Top: low progranulin. Bottom (hypothesis): restored.",
    "DIRECTION": "Raise progranulin toward normal levels",
    "MODALITY": "anti-sortilin antibody",
    "EVIDENCE_3": "Evidence gap: no clinical outcome was grounded in this run.",
}


def _authored(run_root, **overrides):
    """Seed a spec, fill the authored fields, apply overrides, write it back."""
    path = run_root / "deliverables" / "infographic_spec.json"
    spec = seed(run_root)
    spec.update(REAL_PANELS)
    spec.update(overrides)
    for assertion in spec["SCIENTIFIC_ASSERTIONS"]:
        panel = assertion["panel"]
        assertion.update({
            "text": spec[f"PANEL_{panel}_DESCRIPTION"],
            "relation": "is represented by",
            "object": "the cited evidence-backed mechanism",
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False))
    return spec


def test_tool_request_loads_and_calls_biomni_generate_image_with_phylo_style(
        make_run):
    run = _prompt_ready(make_run())
    request = build_tool_request(run)

    assert request["tool"] == TOOL_NAME == "GenerateImage"
    assert TOOL_SEARCH_QUERY == "select:GenerateImage"
    assert request["load_tool"]["arguments"]["query"] == TOOL_SEARCH_QUERY
    assert request["arguments"]["file_name"] == "infographic.png"
    assert request["arguments"]["aspect_ratio"] == TOOL_ASPECT_RATIO == "3:2"
    prompt = request["arguments"]["prompt"]
    assert "PHYLO PALETTE only" in prompt
    assert "#FAF9F3" in prompt and "#CC2FB2" in prompt
    assert "Signifier" in prompt
    assert "DieGrotesk" in prompt
    assert request["style"]["body_font"] == "DieGrotesk"
    assert request["style"]["heading_font"] == "Signifier"
    assert "HORIZONTAL 3-PANEL" in prompt
    assert request["media_output_check"]["tool"] == "Read"
    assert request["media_output_check"]["arguments"]["mode"] == (
        "media_output_check")
    assert request["media_output_check"]["arguments"]["file_path"].endswith(
        "deliverables/infographic.png")
    checker = request["media_output_check"]
    assert checker["required_checks"]["antibody_binding_orientation"] == "pass"
    check_prompt = checker["arguments"]["media_output_check_prompt"].lower()
    assert "variable domain" in check_prompt
    assert "fc constant-region stem" in check_prompt
    assert "halo is on fc" in check_prompt


def test_antibody_infographic_cannot_pass_with_fc_anatomy_unchecked(run_root):
    _authored(run_root)

    with pytest.raises(ValueError, match="structured check"):
        record_media_check(
            run_root, "pass", "Antibody was not inspected.",
            panel_content_complete="pass", safe_margins="pass",
            scientific_assertions_correct="pass",
            model_and_outcome_scope_correct="pass",
            antibody_binding_orientation="not_applicable",
        )


def test_tool_request_is_persisted_for_the_generation_gate(make_run):
    run = _prompt_ready(make_run())
    path = write_tool_request(run)
    request = json.loads(path.read_text())
    assert request["tool"] == "GenerateImage"
    assert request["prompt_sha256"]


def test_report_contract_requires_biomni_generation_and_media_check():
    from report_model import load_contract

    visual = load_contract()["visual_abstract"]
    assert visual["generation_tool"] == "GenerateImage"
    assert visual["tool_request"] == (
        "state/infographic_generate_image_request.json")
    assert visual["generation_receipt"] == "state/infographic_generation.json"
    assert visual["require_media_output_check"] is True


def test_cli_gate_rejects_an_image_without_generate_image_provenance(run_root,
                                                                     capsys):
    import infographic_spec

    _authored(run_root)
    assert infographic_spec.main(["--root", str(run_root), "--verify"]) > 0
    assert "GenerateImage tool request" in capsys.readouterr().out


def test_install_records_request_and_image_hashes_for_the_gate(
        run_root, tmp_path, monkeypatch):
    import infographic_spec

    _authored(run_root)
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(infographic_spec, "RESULTS_DIR", results_dir)
    request_path = write_tool_request(run_root)
    image = results_dir / "infographic.png"
    image.write_bytes((run_root / "deliverables" / "infographic.png").read_bytes())
    install_image(run_root, image)
    record_media_check(
        run_root, "pass", "Final installed image is readable.",
        panel_content_complete="pass", safe_margins="pass",
        scientific_assertions_correct="pass",
        model_and_outcome_scope_correct="pass",
        antibody_binding_orientation="pass",
    )

    receipt = json.loads(
        (run_root / "state" / "infographic_generation.json").read_text())
    assert receipt["tool"] == "GenerateImage"
    assert receipt["request_sha256"]
    assert receipt["image_sha256"]
    assert receipt["prompt_sha256"] == json.loads(
        request_path.read_text())["prompt_sha256"]
    assert infographic_spec.main(["--root", str(run_root), "--verify"]) == 0


def test_generation_gate_requires_durable_media_output_check(
        run_root, tmp_path, monkeypatch):
    import infographic_spec

    _authored(run_root)
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(infographic_spec, "RESULTS_DIR", results_dir)
    write_tool_request(run_root)
    image = results_dir / "infographic.png"
    image.write_bytes((run_root / "deliverables" / "infographic.png").read_bytes())
    install_image(run_root, image)

    failures = verify(run_root, require_generation=True)
    assert any("media output check" in failure for failure in failures)

    record_media_check(
        run_root, "pass", "No garbled or invented labels.",
        panel_content_complete="pass", safe_margins="pass",
        scientific_assertions_correct="pass",
        model_and_outcome_scope_correct="pass",
        antibody_binding_orientation="pass",
    )
    assert verify(run_root, require_generation=True) == []


# --- profiles ----------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("What is the evidence for GRN as a therapeutic target in FTD?", "target"),
    ("Summarize the evidence for APOE as a target in Alzheimer's disease", "target"),
    ("Is TDP-43 mislocalization upstream or downstream of stress granules?",
     "general"),
    ("How well does plasma p-tau217 discriminate amyloid status?", "general"),
    ("Which conditioning regimen gives better outcomes in CAR-T?", "general"),
])
def test_profile_follows_the_question(question, expected):
    """`target` keeps the sibling skill's biology -> mechanism -> rationale arc;
    `general` covers every other review this skill runs, with the same 3-panel
    shape and Phylo styling but different panel semantics."""
    assert detect_profile({"question": question, "title": ""}) == expected


def test_profile_is_recorded_in_the_spec(run_root):
    assert seed(run_root)["PROFILE"] == "target"


# --- seeding pulls real facts, not placeholders ------------------------------

def test_seed_copies_the_reviews_own_evidence(run_root):
    """Header, tiers and sources are copied from the model so nobody retypes a
    number that already exists in a checked artifact."""
    spec = seed(run_root)
    assert spec["SUBJECT"] == "GRN"
    assert spec["CONTEXT"] == "frontotemporal dementia"
    assert "grounded claims" in spec["HEADLINE_TAG"]
    assert "Ward et al. 2024" in spec["EVIDENCE_1"]
    assert "one primary study" in spec["EVIDENCE_1"]


def test_seed_leaves_panels_for_the_author(run_root):
    """A schematic cannot be generated from claim text — someone has to decide
    what to draw. Seeding gives them the facts."""
    spec = seed(run_root)
    for field in ("PANEL_A_DESCRIPTION", "PANEL_B_DESCRIPTION",
                  "PANEL_C_DESCRIPTION"):
        assert spec[field].startswith("TODO")


# --- verification ------------------------------------------------------------

def test_unauthored_spec_fails(run_root):
    path = run_root / "deliverables" / "infographic_spec.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seed(run_root)))
    failures = verify(run_root)
    assert any("still unauthored" in f for f in failures)


def test_authored_spec_with_real_values_passes(run_root):
    _authored(run_root)
    assert verify(run_root) == []


def test_required_mode_fails_when_the_rendered_image_is_missing(run_root):
    _authored(run_root)
    (run_root / "deliverables" / "infographic.png").unlink()
    failures = verify(run_root)
    assert any("no infographic image" in failure for failure in failures)


def test_quick_mode_does_not_require_a_rendered_image(make_run):
    run = make_run(mode="quick")
    _authored(run)
    (run / "deliverables" / "infographic.png").unlink()
    assert verify(run) == []


def test_generate_image_result_is_installed_at_the_contract_path(
        run_root, tmp_path, monkeypatch):
    import infographic_spec
    from PIL import Image

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(infographic_spec, "RESULTS_DIR", results_dir)
    source = results_dir / "infographic.png"
    Image.new("RGB", (90, 60), "white").save(source)
    _authored(run_root)
    write_tool_request(run_root)
    destination = run_root / "deliverables" / "infographic.png"
    destination.unlink()

    installed = install_image(run_root, source)
    assert installed == destination
    assert installed.read_bytes() != source.read_bytes()
    with Image.open(installed) as composed:
        assert composed.size == (90, 60)


def test_unreadable_generate_image_result_is_rejected(
        run_root, tmp_path, monkeypatch):
    import infographic_spec

    _authored(run_root)
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(infographic_spec, "RESULTS_DIR", results_dir)
    write_tool_request(run_root)
    source = results_dir / "infographic.png"
    source.write_bytes(b"not an image")
    with pytest.raises(SystemExit, match="unreadable"):
        install_image(run_root, source)


def test_non_tool_result_path_is_rejected(run_root, tmp_path):
    _authored(run_root)
    write_tool_request(run_root)
    source = tmp_path / "infographic.png"
    source.write_bytes((run_root / "deliverables" / "infographic.png").read_bytes())
    with pytest.raises(SystemExit, match="results path returned"):
        install_image(run_root, source)


@pytest.mark.parametrize("field,value,needle", [
    ("PANEL_B_DESCRIPTION", "Penetrance is 97.3% in carriers.", "97.3"),
    ("PANEL_B_DESCRIPTION", "The lead variant rs5848 lowers expression.", "rs5848"),
    ("PANEL_A_DESCRIPTION", "Reduced by 62% (p=2.8e-56).", "p=2.8e-56"),
    ("EVIDENCE_2", "Mechanism: convergent — GCST90704646", "GCST90704646"),
])
def test_fabricated_values_are_caught(run_root, field, value, needle):
    """The number a reader trusts most is the one in the summary graphic, and it
    is the only one they cannot follow to a quote."""
    _authored(run_root, **{field: value})
    failures = verify(run_root)
    assert any(needle in f for f in failures), failures


def test_invented_citations_are_caught(run_root):
    _authored(run_root, EVIDENCE_2="Mechanism: convergent — Ripley et al. 2019")
    failures = verify(run_root)
    assert any("Ripley 2019" in f for f in failures)


def test_real_citations_from_the_reference_list_pass(run_root):
    _authored(run_root,
              EVIDENCE_2="Mechanism: single study — Tanaka et al. 2014")
    assert verify(run_root) == []


def test_empty_counterweight_bullet_fails(run_root):
    """A summary showing only supporting evidence misrepresents a review whose
    contract requires a contradiction axis."""
    _authored(run_root, EVIDENCE_3="")
    assert any("EVIDENCE_3" in f for f in verify(run_root))


def test_unsupported_exclusivity_in_panel_copy_fails(run_root):
    _authored(
        run_root,
        PANEL_C_DESCRIPTION=(
            "The treatment works only in the biomarker-positive subset."
        ),
    )

    failures = verify(run_root)

    assert any("exclusive" in failure.lower() for failure in failures)


def test_bad_profile_fails(run_root):
    _authored(run_root, PROFILE="mechanism-ish")
    assert any("PROFILE" in f for f in verify(run_root))


def test_missing_spec_is_reported_not_ignored(run_root):
    path = run_root / "deliverables" / "infographic_spec.json"
    if path.exists():
        path.unlink()
    assert any("no infographic spec" in f for f in verify(run_root))


def test_verify_stamps_its_result_into_the_spec(run_root, tmp_path, monkeypatch):
    """The PDF caption tells the reader whether the numbers above them were
    checked, which it can only do if the check leaves a trace."""
    import infographic_spec

    _authored(run_root)
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(infographic_spec, "RESULTS_DIR", results_dir)
    write_tool_request(run_root)
    generated = results_dir / "infographic.png"
    generated.write_bytes(
        (run_root / "deliverables" / "infographic.png").read_bytes())
    install_image(run_root, generated)
    record_media_check(
        run_root, "pass", "Final image passed visual inspection.",
        panel_content_complete="pass", safe_margins="pass",
        scientific_assertions_correct="pass",
        model_and_outcome_scope_correct="pass",
        antibody_binding_orientation="pass",
    )
    infographic_spec.main(["--root", str(run_root), "--verify"])
    spec = json.loads(
        (run_root / "deliverables" / "infographic_spec.json").read_text())
    assert spec["_verified"] is True


# --- placement in the delivered report ---------------------------------------

@pytest.fixture
def built(run_root, tmp_path):
    import subprocess

    import build_pdf

    _authored(run_root)
    out = tmp_path / "r.pdf"
    assert build_pdf.main(["--root", str(run_root), "--out", str(out)]) == 0
    try:
        text = subprocess.run(["pdftotext", "-layout", str(out), "-"],
                              capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("pdftotext (poppler) not available")
    return out, text


def test_infographic_opens_the_report(built):
    """Before the Contents: a table of contents above it would bury the one page
    that says what the review found."""
    _pdf, text = built
    page_one = text.split("\f")[0]
    assert "Infographic." in page_one
    assert page_one.index("Infographic.") < page_one.index("Contents")


def test_markdown_opens_with_the_same_infographic(run_root):
    import build_review

    _authored(run_root)
    assert build_review.main(["--root", str(run_root)]) == 0
    markdown = (run_root / "deliverables" / "review.md").read_text()
    assert "![Infographic](infographic.png)" in markdown
    assert markdown.index("![Infographic]") < markdown.index("## Summary")


def test_deep_builds_refuse_to_silently_omit_the_infographic(run_root, tmp_path):
    import build_pdf
    import build_review

    (run_root / "deliverables" / "infographic.png").unlink()
    assert build_review.main(["--root", str(run_root)]) == 1
    with pytest.raises(SystemExit, match="no infographic image"):
        build_pdf.main([
            "--root", str(run_root),
            "--out", str(tmp_path / "report.pdf"),
        ])


def test_caption_states_the_profile_and_grounding(built):
    _pdf, text = built
    assert "target biology" in text
    assert "drawn from this review's accepted evidence" in text


def test_caption_carries_the_contract_marker(built):
    from report_model import load_contract

    _pdf, text = built
    spec = load_contract()["visual_abstract"]
    markers = [spec["caption_marker"]] + list(spec.get("accept_markers") or [])
    assert any(m.lower() in text.lower() for m in markers)


def test_legacy_marker_still_accepted():
    """A run built before the rename must not fail against the current
    contract."""
    from report_model import load_contract

    assert "visual abstract" in load_contract()["visual_abstract"]["accept_markers"]


# --- the prompt must reach the renderer --------------------------------------

def test_the_prompt_carries_the_antibody_rules(make_run):
    """The template was referenced only by prose — SKILL.md named the file and
    no code read it — so the modality shape guide never entered the context at
    the moment the image was generated, and every delivered infographic drew the
    antibody binding through its Fc stem instead of its Fab arms."""
    from infographic_spec import prompt

    text, missing = prompt(_prompt_ready(make_run()))
    assert missing == []
    lowered = text.lower()
    # Assert what the instruction must CONVEY, not the sentences it uses. The
    # first version pinned two exact phrases and broke when the guidance was
    # rewritten from prohibitions into geometry — which was the improvement.
    assert "fab" in lowered and "fc" in lowered, "the parts are not named"
    assert "variable" in lowered and "constant region" in lowered
    assert "letter y" in lowered, "the shape is not described geometrically"
    # Which end binds, and which end must be free.
    assert "tips" in lowered and "touches nothing" in lowered
    # A self-check the renderer can act on, not just a prohibition.
    assert "self-check" in lowered and "180" in lowered
    assert "most common error" in lowered


def test_the_prompt_is_substituted_and_carries_no_operator_notes(make_run):
    from infographic_spec import prompt

    text, _ = prompt(_prompt_ready(make_run()))
    assert "{{" not in text, "an unsubstituted placeholder reached the renderer"
    assert not any(line.startswith("#") for line in text.splitlines()), (
        "operator-facing header lines were sent to the image model")
    assert "GRN" in text and "progranulin" in text


def test_an_unauthored_spec_refuses_to_emit_a_prompt(make_run):
    """Rendering from a spec with TODO placeholders would put "TODO — author
    this" on page one of the report."""
    from infographic_spec import prompt

    run = make_run()
    import json
    path = run / "deliverables" / "infographic_spec.json"
    spec = json.loads(path.read_text()) if path.exists() else {}
    spec["PANEL_B_DESCRIPTION"] = "TODO — author this"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2))

    _text, missing = prompt(run)
    assert "PANEL_B_DESCRIPTION" in missing


def test_every_template_placeholder_is_a_field_the_spec_defines():
    """A placeholder the seeder never writes can only ever be unauthored, so the
    prompt would refuse forever with no way for the operator to satisfy it."""
    import re

    from infographic_spec import AUTHORED_FIELDS, TEMPLATE

    placeholders = set(re.findall(r"\{\{([A-Z_0-9]+)\}\}", TEMPLATE.read_text()))
    seeded = {
        "PROFILE", "SUBJECT", "SUBJECT_LONG", "SUBJECT_CLASS_SHORT", "CONTEXT",
        "REVIEW_QUESTION", "HEADLINE_TAG", "MODALITY",
        "EVIDENCE_1", "EVIDENCE_2", "EVIDENCE_3",
    } | set(AUTHORED_FIELDS)
    assert placeholders <= seeded, (
        f"template asks for {sorted(placeholders - seeded)}, which nothing writes")


def _prompt_ready(run):
    """The file's own _authored(), plus the fields --prompt additionally needs.

    seed() leaves MODALITY and DIRECTION as TODO for a target profile, and the
    prompt legitimately refuses those — so a prompt test has to author them the
    way an operator would.
    """
    _authored(run, MODALITY="monoclonal antibody", DIRECTION="raise",
              SUBJECT_LONG="progranulin",
              SUBJECT_CLASS_SHORT="Secreted growth factor",
              CONTEXT="frontotemporal dementia")
    return run   # _authored returns the spec; prompt() wants the run root
