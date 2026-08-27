#!/usr/bin/env python3
"""Scaffold a Biomni skill package from the interview answers. Stdlib only.

This is the primary enforcement layer. The report sentence and the report path are written together,
from one source, as one string — so they cannot come apart. Composing them by hand is how skills end
up with one and not the other.

    scaffold_skill.py --slug my-skill --archetype analysis-workflow --category transcriptomics \
        --record /workspace/skill_design/my-skill.json --facts-requirement required \
        --dest /mnt/results/skills

    scaffold_skill.py --slug my-skill --archetype format-utility --category general --record design.json \
        --facts-requirement not_applicable --facts-not-applicable-reason "pure formatter" --dry-run

    scaffold_skill.py --figures-from-steps /mnt/results/skills/my-skill   # after the steps exist

Refuses to write a package without a concrete four-field starting task. Q2/Q3/Q4 remain authoring
judgements: missing values are blocked unless --auto-progress
is passed — in which case defaults are used and every one is recorded as agent-chosen and disclosed in
the generated SKILL.md.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import secrets
import sys

sys.dont_write_bytecode = True  # a stray __pycache__ in a package is a hard validator error

CANONICAL_KEY_ORDER = ("id", "name", "description", "category", "visibility", "starting-prompt")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ARCHETYPES = (
    "analysis-workflow", "evidence-synthesis", "protocol-workflow",
    "correctness-guidance", "format-utility", "meta-tooling",
)

CATEGORIES = (
    "data_analysis", "data_discovery", "drug_discovery", "epigenomics", "experimental_design",
    "functional_analysis", "functional_genomics", "general", "genomics_genetics", "integration",
    "literature", "molecular_design", "multi_omics", "pathway_analysis",
    "proteomics_metabolomics", "reporting", "transcriptomics",
)

PLACEHOLDERS = {"", "tbd", "n/a", "na", "none", "standard", "as appropriate", "see above", "todo"}

_HERE = pathlib.Path(__file__).resolve().parent
_SENTENCE_FILE = _HERE.parent / "assets" / "contract" / "delegation_sentence.txt"
_GATE_FILE = _HERE / "check_skill.py"
_EVIDENCE_FILE = _HERE / "evidence_contract.py"

# Defaults offered per interview question. Q1 and Q2 have none by design: guessing the subject/input
# produces a skill for data, evidence, or material that does not exist.
DEFAULTS = {
    "q3": "Results driven by batch structure, by a single outlier sample, or by an identifier "
          "mapping that silently dropped features.",
    "q4": "This skill is hypothesis-generating only. It does not claim validation at any threshold.",
    "q5": "A bench scientist, who needs the ranked results table, the report, and one figure "
          "per analysis step showing that step's result.",
    "q6": "Permissive-licensed sources only. Anything with unclear terms is reported as a blocker "
          "rather than used.",
    "q7": "Nothing written yet; scripts are authored fresh.",
}

DEFAULT_QUESTION = {
    "q3": "What result would look like a hit and be an artifact?",
    "q4": "Where is the line between validated and a hypothesis, and what number decides?",
    "q5": "Who reads the output, and which figure would they need to believe each analysis step?",
    "q6": "Every data source and package — where from, what terms?",
    "q7": "What have you already written for this?",
}


def sentence() -> str:
    """The one string in the package required to be byte-identical to other packages."""
    if not _SENTENCE_FILE.exists():
        sys.exit(f"missing contract file: {_SENTENCE_FILE}")
    return " ".join(_SENTENCE_FILE.read_text(encoding="utf-8").split())


def gate_module():
    """The sibling gate, loaded by path. Same reasoning as receipt_keys(): by name it could resolve to
    some other check_skill on sys.path, and the import is deferred to call time so nothing leaves a
    __pycache__ behind (sys.dont_write_bytecode is set above, before any of this runs)."""
    import importlib.util

    if not _GATE_FILE.exists():
        sys.exit(f"missing sibling gate: {_GATE_FILE}")
    spec = importlib.util.spec_from_file_location("_gate_contract", _GATE_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def evidence_module():
    """Load the versioned contract builder by path so the scaffolder and checker share one schema."""
    import importlib.util

    if not _EVIDENCE_FILE.exists():
        sys.exit(f"missing sibling evidence contract: {_EVIDENCE_FILE}")
    spec = importlib.util.spec_from_file_location("_evidence_contract", _EVIDENCE_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def receipt_keys() -> tuple[str, ...]:
    """The run-receipt keys, read out of the gate that requires them by name. Restating them here
    would be two spellings of one contract — the drift this package exists to catch."""
    keys = tuple(getattr(gate_module(), "EVIDENCE_RECEIPT_KEYS", ()))
    if not keys:
        sys.exit(f"{_GATE_FILE} defines no EVIDENCE_RECEIPT_KEYS — gate and scaffolder have diverged")
    return keys


def mint_id() -> str:
    return "skill_" + secrets.token_hex(16)


# Q2/Q3/Q4 are the interview. A one-line answer there is a generic caveat wearing the right heading,
# so they carry a real length floor. The others have legitimately short true answers — "Nothing
# written yet." is a complete answer to Q7 — so they only have to not be a placeholder word.
MIN_LEN = {"q2": 40, "q3": 40, "q4": 30}


def is_placeholder(v: object, qid: str = "") -> bool:
    if not isinstance(v, str):
        return True
    s = v.strip()
    if s.lower().rstrip(".") in PLACEHOLDERS:
        return True
    return len(s) < MIN_LEN.get(qid, 8)


def yaml_dq(v: str) -> str:
    """A YAML double-quoted scalar. An unescaped quote in a description emits invalid YAML that the
    gate's per-line regex still accepts and the platform's real parser rejects — an unloadable
    package that looks clean. One pass over the characters, so escaping the backslash cannot
    re-escape what the quote escape just wrote."""
    esc = {"\\": "\\\\", '"': '\\"', "\t": "\\t", "\n": "\\n", "\r": "\\r"}
    body = "".join(esc.get(c) or (c if c >= " " and c != "\x7f" else "\\x%02x" % ord(c)) for c in v)
    return f'"{body}"'


def frontmatter(slug: str, description: str, category: str, visibility: str,
                starting_prompt: str | None) -> str:
    vals = {
        "id": mint_id(),
        "name": slug,
        "description": description,
        "category": category,
        "visibility": visibility,
        "starting-prompt": starting_prompt,
    }
    lines = ["---"]
    for k in CANONICAL_KEY_ORDER:            # order is enforced by the validator
        v = vals[k]
        if v is None:
            continue
        one = " ".join(str(v).split())        # single-line, double-quoted, never a block scalar
        lines.append(f"{k}: {yaml_dq(one)}")
    lines.append("---")
    return "\n".join(lines)


def outputs_block(report_name: str, facts_requirement: str) -> str:
    """The contract outputs, plus a blocking marker for the one that cannot be derived.

    The report is universal and facts follow their explicit applicability decision.
    The analysis result table is workflow-specific; OP001 requires it,
    while the author supplies the filename because only the workflow defines its shape.
    """
    marker = todo("outputs", "name the machine-readable result file this run writes — the exact "
                             "filename, and one line on what a row is")
    facts_line = (
        "- `report_facts.json` — evidence-bearing claims, operational definitions, provenance, and "
        "validated denominator/completion partitions\n"
        if facts_requirement == "required"
        else "- Facts artifact — `not_applicable`; the machine-readable reason is in `skill_contract.json`\n"
    )
    report_line = f"- `{report_name}` — {sentence()}\n"
    return report_line + f"- {marker}\n" + facts_line


def report_section_contract(archetype: str) -> str:
    """Universal headings with archetype-specific meanings; never push deliverables into prompts."""
    meanings = {
        "analysis-workflow": (
            "Methods & Sources covers data provenance, filtering, parameters, software, and the "
            "analysis design; Results reports validated quantitative findings and figures."
        ),
        "evidence-synthesis": (
            "Methods & Sources covers the search, source-selection, extraction, and evidence-"
            "grading method; Results reports the synthesized evidence and disagreements."
        ),
        "protocol-workflow": (
            "Methods & Sources covers materials, authoritative procedure sources, and execution "
            "conditions; Results presents the protocol, checkpoints, outputs, and acceptance criteria."
        ),
        "correctness-guidance": (
            "Methods & Sources covers the rules, authorities, and evaluation approach; Results "
            "presents the guidance, decisions, counterexamples, and validation outcomes."
        ),
        "format-utility": (
            "Methods & Sources covers the input contract, transformation, and validation method; "
            "Results presents the transformed artifact and integrity checks."
        ),
        "meta-tooling": (
            "Methods & Sources covers the generation or inspection method and governing contracts; "
            "Results presents the produced package or tooling outcome and its validation evidence."
        ),
    }
    return (
        "The PDF must use these visible top-level sections in this order, adapting their contents "
        "to this skill rather than adding generic filler:\n\n"
        "1. `Task Context` — the research or practitioner question, supplied inputs, scope, and "
        "decision the output informs.\n"
        "2. `Methods & Sources` — " + meanings[archetype] + "\n"
        "3. `Results` — the run's actual outputs and evidence, never a description of what the skill "
        "could do.\n"
        "4. `Conclusions & Interpretation` — supported takeaways, their practical meaning, and "
        "appropriate next steps.\n"
        "5. `Limitations` — run-specific uncertainty, missing coverage, failed or unavailable checks, "
        "and claims the evidence does not support.\n\n"
        "Include references and a compact output-artifact table where applicable. Empty boilerplate "
        "does not satisfy a section."
    )


def terminal_step(step_no: int, report_name: str, *, archetype: str,
                  figures_applicable: bool, figure_not_applicable_reason: str,
                  bundled_files: list[str], infographic_required: bool,
                  infographic_reason: str) -> str:
    # One QC call runs the gates and records each outcome with its evidence. The key list is loaded
    # from the checker so adding an outcome cannot silently leave the generated instructions stale.
    keys = ", ".join(f"`{k}`" for k in receipt_keys())
    # The two list arguments start EMPTY, and the marker that says to fill them sits outside the
    # fence. Both halves were review findings. `[...]` reads as a documentation placeholder and is
    # not one — Python evaluates it to `[Ellipsis]`, so a copied call reached write_receipt() and
    # died on `pathlib.Path(Ellipsis)` with a TypeError naming nothing, having passed the gate on the
    # way. `[]` is safe if executed: _bundled() raises a GateFailure that says what is missing. And
    # the marker cannot live in the fence, because TF001 counts markers in prose only — strip_code()
    # drops fenced blocks, so a TODO(author) inside one is invisible to the gate that exists to catch
    # it. RC009 blocks on the empty lists themselves, so deleting the marker alone is not a pass.
    bundled_literal = repr(bundled_files)
    infographic_procedure = ""
    infographic_argument = "[]"
    if infographic_required:
        infographic_procedure = (
            "Generate `infographic.png` with Biomni `GenerateImage` before report assembly. "
            "Immediately after the tool returns—and before any transformation or replacement—"
            "snapshot its trace and pixels:\n\n"
            "```python\n"
            "from report_qc import record_generated_infographic\n"
            "record_generated_infographic(\"infographic.png\", log_path=\"qc_run_log.json\")\n"
            "```\n\n"
        )
        infographic_argument = '["infographic.png"]'
    else:
        infographic_procedure = (
            f"An infographic is not applicable: {infographic_reason}. Do not call `GenerateImage` "
            "for decorative compliance.\n\n"
        )
    return (
        f"### Step {step_no} — Final report (MANDATORY TERMINAL STEP)\n"
        f"**The run is not complete until this step has produced `{report_name}` at the results "
        f"root.**\n"
        f"{sentence()}\n\n"
        f"Produce one combined PDF with a short narrative. "
        + ("Place the qualitative GenerateImage infographic near the beginning as the first "
           "substantive visual; " if infographic_required else "") +
        f"rendered page images are "
        f"inspection evidence only, never substitutes for the PDF. End the task with a concise "
        f"conclusion and links to the PDF and supporting artifacts, not a bare file listing.\n\n"
        f"{report_section_contract(archetype)}\n\n"
        f"{infographic_procedure}"
        f"Render the PDF to a fresh workspace file named by `workspace_report_file`; do not open or "
        f"truncate an existing PDF on the object-backed results mount. The `staged_copy` call below "
        f"publishes the completed file under its declared results-root name.\n\n"
        f"Then verify the run and write its receipt with a single call. `write_receipt` runs every "
        f"gate — the report exists at the results root and is big enough, each declared figure is "
        f"present and non-blank, the exact infographic came from a same-ID `GenerateImage` call and "
        f"result and is the first embedded image on page 1, and the finished PDF carries the markers "
        f"declared by the resolved report-style provider — then records what each one "
        f"returned. Load and follow the selected provider skill's complete report instructions and "
        f"assets. QC prefers a provider-owned `report_style.json` under that provider's assets; when an existing installed "
        f"provider has none, it derives only its declared aliases and PDF marker colors from that "
        f"provider's immutable `SKILL.md`. Neither source is a theme recipe. Never stage, synthesize, "
        f"or copy provider evidence into a workspace or results directory. A missing or ambiguous "
        f"installed provider source is a blocker, not permission to reconstruct one. Run bundled "
        f"commands through `run_bundled`, which writes the QC-owned "
        f"`qc_run_log.json` from the subprocess result and output hashes. Do not author execution "
        f"events or copy transcript identifiers. Record PDF visual review as an explicit attestation; "
        f"it is not described as independently verified. Produce every source-witness artifact "
        f"declared in `skill_contract.json`. The receipt writer rejects unmatched hashes, partial page "
        f"coverage, a visual-review verdict other than pass, any unresolved visual-review issue, or "
        f"source-value disagreement. Fix and rerender every failed page or figure; listing a visual "
        f"defect under Limitations does not make it pass. It raises if any gate failed, **after** "
        f"writing the receipt, so a failed run "
        f"leaves the diagnostic behind.\n\n"
        f"Record each answer from `## Clarification Questions` in `selected_branch_ids` using the "
        f"displayed `<question_id>:<choice_id>` value. The receipt derives its required outputs only "
        f"from those selected branches; do not union mutually exclusive branch artifacts. The "
        f"receipt derives any explicit report-style provider from immutable user messages and "
        f"otherwise resolves the default from `skill_contract.json`; a caller variable cannot "
        f"authorize an override. Never infer styling from the enterprise, account, project, or "
        f"customer context. The absence of an affirmative styling directive is not ambiguity: use "
        f"the contract default without asking a styling clarification. Ask only when user messages "
        f"contain conflicting affirmative selections or request a provider that cannot be resolved "
        f"safely. The fenced call below is the stable public "
        f"API; execute it before "
        f"reading `report_qc.py`, and inspect helper internals only if a `GateFailure` is not specific "
        f"enough to act on.\n\n"
        f"```python\n"
        f"from report_qc import (outputs_for_selected_branches, record_pdf_review, staged_copy,\n"
        f"                       write_receipt)\n"
        f"selected_outputs = outputs_for_selected_branches(selected_branch_ids)\n"
        f"staged_copy(workspace_report_file, \"{report_name}\")\n"
        f"record_pdf_review(\n"
        f"    report_name=\"{report_name}\", text_artifact=extracted_text_file,\n"
        f"    rendered_page_files=rendered_page_files,\n"
        f"    reviewed_page_numbers=reviewed_page_numbers,\n"
        f"    review_attestation=visual_review_notes,\n"
        f"    review_verdict=visual_review_verdict,\n"
        f"    review_issues=visual_review_issues,\n"
        f")\n"
        f"write_receipt(\n"
        f"    report_name=\"{report_name}\",\n"
        f"    figures={'figures' if figures_applicable else '[]'},\n"
        f"    figure_not_applicable_reason={'None' if figures_applicable else repr(figure_not_applicable_reason)},\n"
        f"    bundled_files={bundled_literal},\n"
        f"    outputs=selected_outputs,\n"
        f"    infographics={infographic_argument},\n"
        f"    qc_run_log=\"qc_run_log.json\",\n"
        f")\n"
        f"```\n\n"
        f"The receipt is `run_receipt.json` at the **results root**, not beside this SKILL.md — once "
        f"this skill is installed its own directory is read-only, so a per-run receipt written there "
        f"cannot work. It records {keys}, each with the path, byte count, colours or transcript "
        f"record the outcome was read from.\n\n"
        f"**Do not write this file by hand.** `check_skill.py --require-run-receipt` requires the "
        f"schema marker and per-outcome evidence, so a hand-written block of `true`s fails. Whatever "
        f"did not hold is recorded `false` with a `<key>_reason`; fix the run rather than the "
        f"receipt.\n"
    )


def figures_block(steps: list[tuple[int, str]] | None = None) -> str:
    """The figure contract. Rows are derived from real result steps, or blocked until there are any.

    With no analysis steps to derive from, the table remains a blocking marker. Run
    `--figures-from-steps` once the workflow has real steps so every row is tied to an actual result.
    """
    if not steps:
        # No angle brackets and no double hyphen in the marker text: both end an HTML comment early in
        # some parsers, and the second would truncate the marker the gate greps for.
        rows = todo("figures", "add one row per analysis step once the steps are written, or derive "
                               "them by running scaffold_skill.py with the figures-from-steps flag")
    else:
        made = []
        for n, slug in steps:
            ask = todo(f"figure{n}", f"what result must step {n} make visible?")
            made.append(f"| {n} | `figures/figure_{n}_{slug}.png` | {ask} |")
        rows = "\n".join(made)
    return (
        "## Figures\n\n"
        "One representative figure per result-producing step, showing **this run's actual result** — not a "
        "schematic, not an illustrative example. Every figure needs a caption stating what it shows. "
        "If a step genuinely has nothing to plot, replace its row with a one-line reason. A caption is required at run time, so fill these in before the first run.\n\n"
        "| Step | File | What it must make visible |\n|---|---|---|\n" + rows + "\n\n"
        "The report reads this inventory from `report_facts.json`'s `figures` array rather than "
        "restating it, so it cannot claim a figure that was never produced.\n"
    )


def todo(qid: str, question: str) -> str:
    return f"<!-- TODO(author): {qid.upper()} unanswered — {question} — check_skill.py fails on this -->"


def build_skill_md(args, record: dict, auto: list[str], contract: dict) -> str:
    slug = args.slug
    is_workflow = args.archetype == "analysis-workflow"
    report_name = args.report_name or f"report_{slug.replace('-', '_')}.pdf"

    parts: list[str] = []
    evidence = evidence_module()
    task = record["starting_task"]
    prompt = evidence.starting_prompt(task)
    capabilities = record.get("capabilities") if isinstance(record.get("capabilities"), dict) else {}
    runtime = record.get("runtime_instructions") if isinstance(record.get("runtime_instructions"), dict) else {}
    figures = record.get("figures") if isinstance(record.get("figures"), dict) else {}
    execution = record.get("execution") if isinstance(record.get("execution"), dict) else {}
    policy = contract["deliverable_policy"]
    facts_spec = contract["facts"]
    facts_required = facts_spec["requirement"] == "required"
    facts_name = str(facts_spec.get("schema", "report_facts.json"))
    facts_source = str(facts_spec.get("runtime_payload_artifact", ""))
    infographic_required = policy["infographic"]["required"] is True
    infographic_reason = str(policy["infographic"].get("not_applicable_reason", "")).strip()
    figures_applicable = figures.get("applicable") is True
    figure_reason = str(figures.get("not_applicable_reason", "")).strip()
    bundled_files = list(execution.get("bundled_file_refs", [])) if execution.get("bundled_commands_applicable") else []
    derived_description = evidence.catalog_description(capabilities)
    description = derived_description or args.description
    parts.append(frontmatter(slug, description, args.category, args.visibility, prompt))
    parts.append(f"\n<!-- archetype: {args.archetype} -->\n<!-- contract: evidence-v1 -->\n")
    parts.append(f"# {slug.replace('-', ' ').title()}\n")

    parts.append("## When to Use This Skill\n\n" + str(capabilities.get("trigger", "")).strip() + "\n")
    caveats = runtime.get("caveats", []) if isinstance(runtime.get("caveats"), list) else []
    why = caveats[0].get("statement", "") if caveats and isinstance(caveats[0], dict) else ""
    parts.append("## Why This, Not The Obvious Thing (READ FIRST)\n\n" + why + "\n")
    parts.append("## Inputs\n\n" + "\n".join(f"- {item}" for item in runtime.get("inputs", [])) + "\n")

    parts.append("## Outputs\n\n" + outputs_block(report_name, args.facts_requirement))
    parts.append(
        "\n**Write the report to the results root under the name above.** Data tables, figures "
        "and intermediates go in `data/`, `figures/`, `tables/`. Note that `GenerateImage` "
        "strips directory components, so schematics always land at the root regardless of the "
        "path you pass it.\n"
    )

    question_lines = []
    for number, question in enumerate(record.get("clarification_questions", []), 1):
        if not isinstance(question, dict):
            continue
        mode = "select one" if question.get("selection_mode") == "single" else "select one or more"
        question_lines.append(f"{number}. **{question.get('prompt')}** ({mode})")
        for choice in question.get("choices", []):
            if isinstance(choice, dict):
                branch_id = f"{question.get('id')}:{choice.get('id')}"
                question_lines.append(
                    f"   - `{choice.get('id')}` — {choice.get('label', choice.get('id'))}; "
                    f"runtime branch ID `{branch_id}`"
                )
    parts.append("## Clarification Questions\n\n" + "\n".join(question_lines) + "\n")

    # Every archetype receives report_qc.py. Domain steps follow the archetype; facts and figures
    # follow their own applicability decisions so a non-analysis skill cannot promise an artifact
    # whose runtime path was omitted by an unrelated archetype branch.
    workflow = runtime.get("workflow", []) if isinstance(runtime.get("workflow"), list) else []
    parts.append("## Standard Workflow\n\n")
    if is_workflow:
        # Q7 describes existing assets and is rendered under Existing materials. Runtime steps come
        # only from the structured workflow contract so an asset inventory cannot become a command.
        parts.append("**Step 1 — Load and validate the input.** Enforce every item in `## Inputs`; "
                     "stop with `not_computable` on a mismatch.\n\n")
        for number, step in enumerate(workflow, 2):
            if isinstance(step, dict):
                parts.append(f"**Step {number} — {step.get('title')}.** {step.get('instruction')}\n\n")
        next_step = len(workflow) + 2
    else:
        for number, step in enumerate(workflow, 1):
            if isinstance(step, dict):
                parts.append(f"**Step {number} — {step.get('title')}.** {step.get('instruction')}\n\n")
        next_step = len(workflow) + 1

    if figures_applicable:
        parts.append(
            f"**Step {next_step} — Generate and validate figures.** Render only results produced by "
            "the preceding validated steps, write `figures/manifest.json`, then validate every "
            "declared artifact before facts or the receipt can use it.\n\n"
            "```python\n"
            "from report_qc import assert_figures\n"
            "figures = assert_figures(\"figures/manifest.json\")\n"
            "```\n"
        )
        parts.append(figures_block())
        next_step += 1

    if facts_required:
        empty_figures = "figures = []\n" if not figures_applicable else ""
        parts.append(
            f"**Step {next_step} — Write `{facts_name}` from the runtime payload.** The workflow "
            f"must produce `{facts_source}` with every headline, denominator, and partition-member "
            "field declared in `skill_contract.json`. Semantic gates run before the facts artifact "
            "is written, so a failing run never produces claims a report could quote.\n\n"
            "```python\n"
            "from report_qc import write_facts_from_artifact\n"
            f"{empty_figures}"
            f"write_facts_from_artifact({facts_name!r}, source={facts_source!r}, "
            "figures=figures, contract=\"skill_contract.json\")\n"
            "```\n"
        )
        next_step += 1
    parts.append("\n" + terminal_step(next_step, report_name, archetype=args.archetype,
                                      figures_applicable=figures_applicable,
                                      figure_not_applicable_reason=figure_reason,
                                      bundled_files=bundled_files,
                                      infographic_required=infographic_required,
                                      infographic_reason=infographic_reason))

    # Q7's real job: it decides whether the package reuses the author's scripts or writes fresh ones.
    # It is authoring context, so it is recorded as context — it is not an executable step and must
    # not be interpolated into one.
    parts.append("## Existing materials\n\n" + "\n".join(
        f"- {item}" for item in runtime.get("existing_materials", [])
    ) + "\n\n"
                 "This records what existed before the skill did, and whether `scripts/` reuses it "
                 "or was authored fresh. It is provenance, not a procedure — the runnable steps are "
                 "in `## Standard Workflow`.\n")

    parts.append("## Scientific caveats\n\n" + "\n".join(
        f"- {item.get('statement')} Evidence: `{item.get('evidence_ref')}`."
        for item in caveats if isinstance(item, dict)
    ) + "\n\n"
                 "Each caveat must name the artifact field or the number that says whether it "
                 "fired. An unbound caveat is prose; a bound one is a gate.\n")

    maturity = record.get("maturity", "generated")
    parts.append(f"## Evidence Tier\n\nContract maturity: `{maturity}`. Do not claim a higher tier than the validation matrix supports.\n")
    sources = runtime.get("data_sources", []) if isinstance(runtime.get("data_sources"), list) else []
    source_text = "\n".join(
        f"- {source.get('name')} ({source.get('version')}): {source.get('uri')} — license "
        f"{source.get('license')}; commercial use `{source.get('commercial_status')}` "
        f"({source.get('commercial_evidence')}); included `{str(source.get('included')).lower()}`; "
        f"verify with `{source.get('verification_ref')}`."
        for source in sources if isinstance(source, dict)
    ) or ("- Commercial use: not applicable because no external data source or package dependency "
          "is used; see the machine-readable applicability decisions in `skill_contract.json`.")
    parts.append("## Data Sources & Licenses\n\n" + source_text + "\n")
    identity = record.get("resource_identity") if isinstance(record.get("resource_identity"), dict) else {}
    if identity.get("applicable") is True:
        identity_text = (
            f"Resolve `{', '.join(identity.get('identifier_fields', []))}` against "
            f"{identity.get('authoritative_source_uri')} and compare "
            f"`{', '.join(identity.get('identity_fields', []))}`. Write decisions to "
            f"`{identity.get('verification_artifact')}` and require "
            f"`{identity.get('violation_json_path')} = 0` through a source-assertion witness. "
            "A valid identifier attached to mismatched "
            "metadata is a failure, not a resolved resource; exclude it or finalize "
            "`not_computable` before facts or the report are written."
        )
    else:
        identity_text = "Not applicable: " + str(identity.get("not_applicable_reason", "unresolved"))
    parts.append("## Resource Identity\n\n" + identity_text + "\n")
    parts.append("## Common Issues\n\n| Symptom | Cause | Fix |\n|---|---|---|\n")
    parts.append("## Suggested Next Steps\n")
    parts.append("## Related Skills\n")

    if auto:
        rows = "\n".join(
            f"| {q.upper()} | {DEFAULT_QUESTION.get(q, '')} | {DEFAULTS[q]} |" for q in sorted(auto)
        )
        parts.append(
            "## Unconfirmed design choices\n\n"
            "These were chosen without the author confirming them. Each is a real assumption, not a "
            "placeholder, and each could be wrong.\n\n"
            "| Question | Asked | Assumed |\n|---|---|---|\n" + rows + "\n\n"
            "Because the evidence-tier question was not confirmed, this skill claims "
            "**hypothesis-generating only** and must not be presented as validated.\n"
        )

    return "\n".join(parts) + "\n"


def figures_from_steps(argv: list[str]) -> int:
    """Second pass: derive the figure table from the result steps the author has actually written.

    The initial scaffold has no authored result steps, so its figure table remains blocked. This
    pass reads the completed step titles and uses the gate's own NON_ANALYSIS pattern to derive one
    row per analysis step without duplicating classification logic.
    """
    ap = argparse.ArgumentParser(prog="scaffold_skill.py --figures-from-steps")
    ap.add_argument("--figures-from-steps", dest="pkg", required=True,
                    help="package directory whose SKILL.md already has real analysis steps")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    md = pathlib.Path(args.pkg).expanduser() / "SKILL.md"
    if not md.exists():
        sys.exit(f"no SKILL.md at {md}")
    text = md.read_text(encoding="utf-8")

    gate = gate_module()
    steps = [(int(n), title.strip()) for n, title in gate.STEP_TITLE_RE.findall(text)
             if not gate.NON_ANALYSIS.search(title)]
    if not steps:
        sys.exit("no result-producing steps found in SKILL.md — write the numbered steps first. Steps that "
                 "load, export, plot or write the report do not get a figure of their own.")

    rows = "\n".join(
        f"| {n} | `figures/figure_{n}_{slugify(title)}.png` | "
        f"{todo(f'figure{n}', f'what result must step {n} make visible?')} |"
        for n, title in steps
    )
    sec = re.search(r"^(#{2,3}\s*Figures\b.*?)(^\| Step \|.*?)(?=\n\n)", text, re.M | re.S)
    if not sec:
        sys.exit("could not find the '## Figures' table to replace — has the section been renamed?")
    table = "| Step | File | What it must make visible |\n|---|---|---|\n" + rows
    out = text[: sec.start(2)] + table + text[sec.end(2):]

    if args.dry_run:
        print(table)
        return 0
    md.write_text(out, encoding="utf-8")
    print(f"derived {len(steps)} figure row(s) from {md}: steps "
          + ", ".join(str(n) for n, _ in steps)
          + "\nEach still carries a TODO(author) for what it must make visible — the gate fails until "
            "those are answered.")
    return 0


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return "_".join(s.split("_")[:3]) or "analysis"


def _deliverable_filenames(task: dict) -> list[str]:
    """Extract concrete sample-run filenames without putting deliverable prose in the prompt."""
    return list(dict.fromkeys(re.findall(
        r"(?<![\w./-])([A-Za-z0-9][A-Za-z0-9_./-]*\.[A-Za-z0-9]{1,8})(?![\w/-])",
        str(task.get("deliverables", "")),
    )))


def build_eval_yaml(slug: str, contract: dict) -> str:
    """Project a reviewer-compatible declarative eval from the same immutable contract."""
    task = contract["starting_task"]
    expected = _deliverable_filenames(task)
    policy = contract["deliverable_policy"]
    if policy["report"]["required"] is not True:
        raise SystemExit("every generated skill must require a PDF report")
    if not any(name.lower().endswith(".pdf") for name in expected):
        raise SystemExit("starting_task.deliverables must name the report used by eval.yaml")
    if not expected:
        raise SystemExit("starting_task.deliverables must name at least one concrete sample artifact")
    invariants = [
        "Every expected artifact is present at the results root, or the run ends with an explicit partial/not_computable reason.",
        "The final answer is grounded in generated artifacts and does not invent identifiers, values, sources, or validation states.",
        "Source and commercial-use claims match skill_contract.json and DATA_SOURCES.md.",
        "run_receipt.json is generated by report_qc.write_receipt; required outcomes pass and authorized non-applicable outcomes include reasons.",
    ]
    invariants.append(
        "The PDF contains Task Context, Methods & Sources, Results, "
        "Conclusions & Interpretation, and Limitations."
    )
    if policy["infographic"]["required"] is True:
        invariants.append("The same-ID Biomni GenerateImage infographic is the first substantive visual on page 1.")
    lines = [
        f"skill_id: {yaml_dq(slug)}",
        "version: 1",
        "eval_source: skill_author",
        "verification_status: unreviewed",
        "evals:",
        "  - id: sample-prompt",
        "    type: capability",
        f"    prompt: {yaml_dq(evidence_module().starting_prompt(task))}",
        "    expected_outputs:",
        *[f"      - {yaml_dq(name)}" for name in expected],
        "    invariants:",
        *[f"      - {yaml_dq(item)}" for item in invariants],
        "    judge_criteria:",
        "      - The skill follows its declared workflow and selected clarification branch.",
        "      - Computation-critical source witnesses, evidence status, and limitations are explicit.",
        "      - The result is scientifically and operationally useful without unsupported certainty.",
    ]
    return "\n".join(lines) + "\n"


def build_data_sources_md(contract: dict) -> str:
    """Project the commercial-use ledger consumed by generation and later review."""
    sources = contract.get("runtime_instructions", {}).get("data_sources", [])
    lines = [
        "# Data Sources & Licenses",
        "",
        "This file is generated from `skill_contract.json`; repair the contract and regenerate all derived files.",
        "",
        "| Name | Type | Version | URI | License | Commercial status | Evidence | Included | Verification | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    if not sources:
        lines.append("| None | not applicable | not applicable | not applicable | not applicable | not applicable | Commercial use is not applicable because no external source is used. | false | not applicable | See the contract applicability decisions. |")
    for source in sources:
        cells = [
            source.get("name"), source.get("type"), source.get("version"), source.get("uri"),
            source.get("license"), source.get("commercial_status"),
            source.get("commercial_evidence"), str(source.get("included")).lower(),
            source.get("verification_ref"), source.get("notes"),
        ]
        safe = [str(value).replace("|", "\\|").replace("\n", " ") for value in cells]
        lines.append("| " + " | ".join(safe) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    # Dispatched before the main parser, whose --slug/--archetype/--category are required and are
    # meaningless for a pass that edits a package that already exists.
    if "--figures-from-steps" in sys.argv[1:]:
        return figures_from_steps(sys.argv[1:])

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--archetype", required=True, choices=ARCHETYPES)
    ap.add_argument("--category", required=True, choices=CATEGORIES)
    ap.add_argument("--visibility", default="internal", choices=["internal", "public", "shared"])
    ap.add_argument("--description", default="")
    ap.add_argument("--facts-requirement", required=True,
                    choices=["required", "not_applicable"],
                    help="whether evidence-bearing facts/provenance apply to this skill")
    ap.add_argument("--facts-not-applicable-reason", default="")
    ap.add_argument("--report-name", dest="report_name",
                    help="required for analysis-workflow; defaults to report_<slug>.pdf")
    ap.add_argument("--record", help="JSON file of interview answers (keys q1..q7)")
    ap.add_argument("--dest", default="/mnt/results/skills")
    ap.add_argument("--dirs", default="scripts,references,assets/eval",
                    help="only these subdirectories are created — no empty scaffolding")
    ap.add_argument("--auto-progress", action="store_true",
                    help="use published defaults for Q3-Q7 and disclose every one in the package")
    ap.add_argument("--dry-run", action="store_true", help="print SKILL.md to stdout, write nothing")
    args = ap.parse_args()

    if not SLUG_RE.match(args.slug) or len(args.slug) >= 65:
        sys.exit(f"slug {args.slug!r} must be lowercase-kebab and under 65 chars")

    record: dict = {}
    if args.record:
        p = pathlib.Path(args.record).expanduser()
        if not p.exists():
            sys.exit(f"record not found: {p}")
        record = json.loads(p.read_text(encoding="utf-8"))

    task = record.get("starting_task")
    required_task_fields = (
        "user_prompt", "subject_input", "objective", "decision_context", "deliverables"
    )
    if not isinstance(task, dict) or any(
        not isinstance(task.get(field), str) or not task[field].strip()
        for field in required_task_fields
    ):
        sys.exit(
            "refusing to scaffold: starting_task must define a short user_prompt plus concrete "
            "subject_input, objective, decision_context, and deliverables"
        )
    if args.facts_requirement == "not_applicable" and not args.facts_not_applicable_reason.strip():
        sys.exit("refusing to scaffold: facts marked not_applicable need --facts-not-applicable-reason")
    facts_record = record.get("facts") if isinstance(record.get("facts"), dict) else {}
    if args.facts_requirement == "required":
        if facts_record.get("schema", "report_facts.json") != "report_facts.json":
            sys.exit("refusing to scaffold: required facts schema must be report_facts.json")
        payload_artifact = facts_record.get("runtime_payload_artifact")
        if not isinstance(payload_artifact, str) or not payload_artifact.strip():
            sys.exit("refusing to scaffold: required facts need facts.runtime_payload_artifact")
        portable_payload = pathlib.PurePosixPath(payload_artifact.replace("\\", "/"))
        if portable_payload.is_absolute() or ".." in portable_payload.parts:
            sys.exit("refusing to scaffold: facts.runtime_payload_artifact must stay beneath results")
        if portable_payload == pathlib.PurePosixPath("report_facts.json"):
            sys.exit(
                "refusing to scaffold: facts.runtime_payload_artifact must be distinct from "
                "report_facts.json"
            )
    policy = record.get("deliverable_policy")
    if not isinstance(policy, dict) or policy.get("audience") not in ("user_facing", "composable_helper"):
        sys.exit("refusing to scaffold: deliverable_policy must explicitly choose user_facing or composable_helper")
    for kind in ("report", "infographic"):
        choice = policy.get(kind)
        if not isinstance(choice, dict) or not isinstance(choice.get("required"), bool):
            sys.exit(f"refusing to scaffold: deliverable_policy.{kind}.required must be explicit")
        if choice["required"] is False and not str(choice.get("not_applicable_reason", "")).strip():
            sys.exit(f"refusing to scaffold: deliverable_policy.{kind} needs a not-applicable reason")
    if policy["report"]["required"] is not True:
        sys.exit("refusing to scaffold: every generated skill must require a PDF report")
    if record.get("maturity") in {"user_validated", "installable"}:
        sys.exit(
            "refusing to scaffold: a creation run cannot self-assign user_validated or installable; "
            "promote only after a separate child run records the user's clarification selection"
        )

    # Q1 and Q2 block even under --auto-progress.
    for qid, why in (("q1", "the concrete subject/input the skill operates on"),
                     ("q2", "what a competent practitioner would get wrong")):
        if is_placeholder(record.get(qid), qid):
            if args.auto_progress:
                sys.exit(
                    f"refusing to scaffold: {qid.upper()} ({why}) has no answer, and it cannot be "
                    f"defaulted even with --auto-progress. Ask the author and wait."
                )
            print(f"warning: {qid.upper()} unanswered — a TODO(author) marker will be written and "
                  f"check_skill.py will fail until it is resolved", file=sys.stderr)

    auto: list[str] = []
    if args.auto_progress:
        auto = [q for q in DEFAULTS if is_placeholder(record.get(q), q)]

    if not args.description:
        args.description = (f"{args.slug.replace('-', ' ')}. "
                            f"TODO(author): write a description under 500 chars, routing verbs first.")

    evidence = evidence_module()
    contract = evidence.build_contract(
        slug=args.slug,
        archetype=args.archetype,
        record=record,
        facts_requirement=args.facts_requirement,
        facts_not_applicable_reason=args.facts_not_applicable_reason,
    )
    text = build_skill_md(args, record, auto, contract)
    eval_yaml = build_eval_yaml(args.slug, contract)
    data_sources_md = build_data_sources_md(contract)

    if args.dry_run:
        print(text)
        return 0

    pkg = pathlib.Path(args.dest).expanduser() / args.slug
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(text, encoding="utf-8")
    (pkg / "skill_contract.json").write_text(evidence.canonical_json(contract), encoding="utf-8")
    (pkg / "eval.yaml").write_text(eval_yaml, encoding="utf-8")
    (pkg / "DATA_SOURCES.md").write_text(data_sources_md, encoding="utf-8")
    for d in [x.strip() for x in args.dirs.split(",") if x.strip()]:
        (pkg / d).mkdir(parents=True, exist_ok=True)

    template_sources = sorted((_HERE.parent / "templates").glob("*.py"))
    if template_sources:
        (pkg / "scripts").mkdir(parents=True, exist_ok=True)
        for src in template_sources:
            (pkg / "scripts" / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"wrote {pkg}/SKILL.md")
    if auto:
        print(f"auto-progress: {len(auto)} answer(s) defaulted and disclosed: {', '.join(sorted(auto))}")
    print(f"\nnext: python3 {_HERE}/check_skill.py {pkg} --contract A")
    return 0


if __name__ == "__main__":
    sys.exit(main())
