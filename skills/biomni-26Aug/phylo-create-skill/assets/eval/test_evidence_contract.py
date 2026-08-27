#!/usr/bin/env python3
"""Cross-archetype mutation tests for the evidence-v1 generation contract."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SCAFFOLD = ROOT / "scripts" / "scaffold_skill.py"
CHECK = ROOT / "scripts" / "check_skill.py"
TODO_RE = re.compile(r"<!-- TODO\(author\): (\w+) unanswered[^>]*-->")
EVAL_ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def write_text_pdf(path: pathlib.Path, lines: list[str]) -> None:
    """Write one valid text PDF without relying on optional Python PDF packages."""
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
               for line in lines]
    stream = ("BT /F1 12 Tf 72 720 Td 16 TL "
              + " ".join(f"({line}) Tj T*" for line in escaped)
              + " ET").encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    payload.extend(b"%" + b"padding" * 3_000)
    path.write_bytes(payload)


def record(profile: str) -> tuple[str, str, dict]:
    profiles = {
        "quantitative": (
            "analysis-workflow",
            "required",
            {
                "user_prompt": "Which genes respond to dexamethasone after adjusting for cell line?",
                "subject_input": "the Bioconductor airway RNA-seq demo count matrix and metadata",
                "objective": "Estimate the dexamethasone effect after multivariable adjustment.",
                "decision_context": "Fit ~ cell_line + dex; treated versus untreated; FDR 0.05.",
                "deliverables": "report_quantitative.pdf, de_results.csv, and report_facts.json.",
            },
        ),
        "literature": (
            "evidence-synthesis",
            "required",
            {
                "user_prompt": "Does early ctDNA clearance predict progression-free survival in metastatic colorectal cancer?",
                "subject_input": "primary studies of ctDNA molecular response in metastatic colorectal cancer from 2020 through 2026",
                "objective": "Assess whether early ctDNA clearance predicts progression-free survival.",
                "decision_context": "Separate prospective from retrospective evidence and report assay/platform heterogeneity.",
                "deliverables": "report_literature.pdf, evidence_table.csv, and report_facts.json.",
            },
        ),
        "protocol": (
            "protocol-workflow",
            "required",
            {
                "user_prompt": "How should twelve human PBMC DNA samples be prepared for Oxford Nanopore ligation sequencing?",
                "subject_input": "500 ng high-molecular-weight human PBMC DNA for Oxford Nanopore ligation sequencing",
                "objective": "Generate a barcoded library-preparation protocol for twelve samples.",
                "decision_context": "Prioritize native DNA preservation, 20 kb N50, and one PromethION flow cell.",
                "deliverables": "report_protocol.pdf, protocol_parameters.json, and report_facts.json.",
            },
        ),
        "utility": (
            "format-utility",
            "not_applicable",
            {
                "user_prompt": "What does each field in example.csv mean?",
                "subject_input": "example.csv with columns sample_id, condition, and batch",
                "objective": "Render a validated Markdown data dictionary without scientific interpretation.",
                "decision_context": "Preserve row order and flag missing column descriptions.",
                "deliverables": "report_utility.pdf and data_dictionary.md.",
            },
        ),
    }
    archetype, facts_requirement, task = profiles[profile]
    output = {
        "quantitative": "de_results.csv",
        "literature": "evidence_table.csv",
        "protocol": "protocol_parameters.json",
        "utility": "data_dictionary.md",
    }[profile]
    source = {
        "id": f"{profile}-source",
        "field": "input.identity",
        "asserted_value": task["subject_input"],
        "primary_source_uri": "https://example.org/primary-source",
        "retrieved_at": "2026-08-14",
        "verification_method": "compare the primary source metadata to the runtime input manifest",
        "runtime_witness": {
            "artifact": "input_manifest.json",
            "json_path": "input.identity",
            "expected_value": task["subject_input"],
        },
    }
    if profile == "literature":
        source.update({
            "field": "output.resource_identity_violations",
            "asserted_value": 0,
            "verification_method": "resolve every identifier and compare independent metadata",
            "runtime_witness": {
                "artifact": "reference_identity.json",
                "json_path": "violations_in_output",
                "expected_value": 0,
            },
        })
    facts = {
        "runtime_payload_artifact": "facts_payload.json",
        "headline_definitions": [
            {"field": "completion.completed", "operational_definition": "items with a verified terminal result"},
        ],
        "partition_groups": [
            {"name": "completion", "denominator_field": "completion.eligible",
             "member_fields": ["completion.completed", "completion.not_computable"],
             "identity": "sum_members_equals_denominator"},
        ],
        "known_answer_eval_refs": ["assets/eval/test_profile.py"],
    }
    if facts_requirement == "not_applicable":
        facts = {"runtime_payload_artifact": "", "headline_definitions": [], "partition_groups": [],
                 "known_answer_eval_refs": [],
                 "partition_not_applicable_reason": "formatter makes no evidence-bearing claims"}
    external = {
        "applicable": False,
        "not_applicable_reason": "all required inputs are bundled or user-provided",
        "services": [],
    }
    if profile == "literature":
        external = {
            "applicable": True,
            "not_applicable_reason": "",
            "services": [{
                "name": "literature-index",
                "connect_timeout_seconds": 10,
                "read_timeout_seconds": 30,
                "max_retries": 2,
                "wall_clock_budget_seconds": 180,
                "terminal_states": ["complete", "partial", "not_computable"],
                "failure_fixture_refs": ["assets/eval/test_profile.py"],
            }],
        }
    inference = {
        "applicable": False,
        "not_applicable_reason": "this profile does not estimate population-level effects",
    }
    if profile == "quantitative":
        inference = {
            "applicable": True,
            "experimental_unit": "independent airway donor culture",
            "replicate_type": "biological",
            "minimum_independent_units": 3,
            "design_identifiability_check": "full-rank model matrix",
            "permutation_support_check": "not used; parametric model with residual degrees-of-freedom preflight",
            "runtime_preflight_ref": "scripts/report_qc.py",
        }
    capability_id = f"{profile}-core"
    data = {
        "q1": task["subject_input"],
        "q2": "A plausible result is wrong when input identity or applicability is assumed instead of verified against the declared source.",
        "q3": "A completion claim is misleading unless eligible, completed, failed, capped, and not-computable items are partitioned explicitly.",
        "q4": "The output is evidence-validated only after its known-answer fixture and runtime witnesses pass.",
        "q5": (f"A scientist needs {output} and a root PDF to compose a downstream artifact."
               if profile == "utility" else
               f"A scientist needs {output} and a root PDF to decide whether the result is usable."),
        "q6": "Primary source https://example.org/primary-source; user data are not redistributed.",
        "q7": "No pre-existing implementation; package scripts are authored fresh.",
        "starting_task": task,
        "deliverable_policy": ({
            "audience": "composable_helper",
            "report": {"required": True, "not_applicable_reason": ""},
            "infographic": {"required": False,
                            "not_applicable_reason": "A decorative visual would not improve the transformation result."},
        } if profile == "utility" else {
            "audience": "user_facing",
            "report": {"required": True, "not_applicable_reason": ""},
            "infographic": {"required": True, "not_applicable_reason": ""},
        }),
        "facts": facts,
        "source_assertions": [source] if facts_requirement == "required" else [],
        "source_assertions_not_applicable_reason": (
            "formatter uses only the user-provided file schema" if facts_requirement == "not_applicable" else ""
        ),
        "resource_identity": ({
            "applicable": True,
            "artifact": output,
            "authoritative_source_uri": "https://example.org/primary-source",
            "identifier_fields": ["doi", "pmid"],
            "identity_fields": ["title", "year"],
            "verification_artifact": "reference_identity.json",
            "violation_json_path": "violations_in_output",
            "expected_violations": 0,
            "failure_policy": "exclude_or_not_computable",
            "mismatch_fixture_refs": ["assets/eval/test_profile.py"],
        } if profile == "literature" else {
            "applicable": False,
            "not_applicable_reason": "this profile emits no externally identified resources",
        }),
        "clarification_questions": [{
            "id": "input",
            "prompt": "Which declared input profile should this run use?",
            "selection_mode": "single",
            "choices": [
                {"id": f"{profile}-example", "label": "Declared example"},
                {"id": f"{profile}-provided", "label": "User-provided equivalent"},
            ],
        }],
        "clarification_branches": [{
            "question_id": "input",
            "choice_id": f"{profile}-example",
            "implementation_refs": ["scripts/report_qc.py"],
            "artifact_paths": [output],
            "fallback_status": "not_computable",
            "eval_refs": ["assets/eval/test_profile.py"],
        }, {
            "question_id": "input",
            "choice_id": f"{profile}-provided",
            "implementation_refs": ["scripts/report_qc.py"],
            "artifact_paths": [output],
            "fallback_status": "not_computable",
            "eval_refs": ["assets/eval/test_profile.py"],
        }],
        "runtime_instructions": {
            "inputs": [task["subject_input"]],
            "workflow": [{
                "title": "Estimate adjusted effects" if profile == "quantitative" else "Produce the declared result",
                "instruction": f"Process the declared input and write {output}; stop as not_computable when the input contract fails.",
            }],
            "caveats": [{
                "statement": "Do not report a completion claim when the accounting partition fails.",
                "evidence_ref": ("data_dictionary.md:validation" if profile == "utility"
                                 else "report_facts.json:completion"),
            }],
            "data_sources": ([{
                "name": "Primary source",
                "type": "primary reference",
                "uri": "https://example.org/primary-source",
                "version": "2026-08-14 snapshot",
                "license": "Terms verified at retrieval time",
                "commercial_status": "no_prohibition_found",
                "commercial_evidence": "Primary-source terms reviewed on 2026-08-14",
                "verification_ref": "input_manifest.json:input.identity",
                "notes": "Fixture source used to exercise the ledger.",
                "included": True,
            }] if facts_requirement == "required" else []),
            "existing_materials": ["No pre-existing implementation; package scripts are authored fresh."],
        },
        "capabilities": {
            "trigger": f"Use when the named {profile} example needs this exact workflow.",
            "catalog_claim_ids": [capability_id],
            "entries": [{
                "id": capability_id,
                "claim": f"Execute the tested {profile} example.",
                "status": "tested",
                "implementation_refs": ["scripts/report_qc.py"],
                "eval_refs": ["assets/eval/test_profile.py"],
            }],
        },
        "validation_matrix": {
            "auto": {"status": "not_run", "reason": "package has not been forward-tested"},
            "guided": {"status": "not_run", "reason": "guided validation is deferred during authoring"},
        },
        "inference_readiness": inference,
        "external_dependencies": external,
        "figures": {
            "applicable": profile == "quantitative",
            "not_applicable_reason": "the requested deliverable has no result figure"
            if profile != "quantitative" else "",
        },
        "execution": ({
            "bundled_commands_applicable": False,
            "bundled_file_refs": [],
            "command_output_paths": [],
            "not_applicable_reason": "the composable helper uses a platform transformation tool",
        } if profile == "utility" else {
            "bundled_commands_applicable": True,
            "bundled_file_refs": ["scripts/report_qc.py"],
            "command_output_paths": [output],
            "not_applicable_reason": "",
        }),
    }
    return archetype, facts_requirement, data


def make_fixture(root: pathlib.Path, profile: str) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    archetype, facts_requirement, data = record(profile)
    slug = f"evidence-{profile}"
    rec = root / f"{slug}.json"
    rec.write_text(json.dumps(data), encoding="utf-8")
    cmd = [
        sys.executable, str(SCAFFOLD), "--slug", slug, "--archetype", archetype,
        "--category", "general", "--record", str(rec), "--dest", str(root / "skills"),
        "--facts-requirement", facts_requirement,
    ]
    if facts_requirement == "not_applicable":
        cmd += ["--facts-not-applicable-reason", "formatter makes no evidence-bearing claims"]
    run = subprocess.run(cmd, capture_output=True, text=True, env=EVAL_ENV)
    if run.returncode:
        raise RuntimeError(run.stdout + run.stderr)
    pkg = root / "skills" / slug
    eval_file = pkg / "assets" / "eval" / "test_profile.py"
    eval_file.parent.mkdir(parents=True, exist_ok=True)
    eval_file.write_text("# known-answer and failure-injection fixture\n", encoding="utf-8")
    md = pkg / "SKILL.md"
    text = md.read_text(encoding="utf-8")
    replacements = {
        "STEP2": f"Run the declared {profile} procedure and write its structured result.",
        "OUTPUTS": f"`{data['clarification_branches'][0]['artifact_paths'][0]}` — structured result",
        "RECEIPT": "The paths below are the executed package script and declared result.",
    }
    text = TODO_RE.sub(lambda match: replacements.get(match.group(1), match.group(0)), text)
    if data["execution"]["bundled_commands_applicable"]:
        text = text.replace("bundled_files=[],", 'bundled_files=["scripts/report_qc.py"],')
    text = text.replace(
        "outputs=[],",
        f'outputs=["{data["clarification_branches"][0]["artifact_paths"][0]}"],',
    )
    md.write_text(text, encoding="utf-8")
    if archetype == "analysis-workflow":
        subprocess.run(
            [sys.executable, str(SCAFFOLD), "--figures-from-steps", str(pkg)],
            check=True, capture_output=True, text=True, env=EVAL_ENV,
        )
        text = md.read_text(encoding="utf-8")
        md.write_text(
            TODO_RE.sub(
                lambda match: "effect size and adjusted significance for each tested feature"
                if match.group(1).startswith("FIGURE") else match.group(0),
                text,
            ),
            encoding="utf-8",
        )
    return pkg


def run_check(pkg: pathlib.Path) -> tuple[int, str]:
    run = subprocess.run(
        [sys.executable, str(CHECK), str(pkg), "--contract", "A"],
        capture_output=True, text=True, env=EVAL_ENV,
    )
    return run.returncode, run.stdout + run.stderr


def mutate(pkg: pathlib.Path, target: pathlib.Path, change) -> pathlib.Path:
    target.mkdir(parents=True)
    actual = target / pkg.name
    shutil.copytree(pkg, actual)
    contract_path = actual / "skill_contract.json"
    data = json.loads(contract_path.read_text(encoding="utf-8"))
    change(data, actual)
    contract_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return actual


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        fixtures = {profile: make_fixture(root / profile, profile)
                    for profile in ("quantitative", "literature", "protocol", "utility")}
        for profile, pkg in fixtures.items():
            code, output = run_check(pkg)
            results.append((f"valid {profile} profile", code in (0, 2), f"exit {code}"))

        multi = mutate(fixtures["utility"], root / "multiple-choice", lambda data, pkg:
                       data["clarification_questions"][0].update(selection_mode="multiple"))
        code, output = run_check(multi)
        results.append(("multi-select clarification questions remain valid when every choice is mapped",
                        code in (0, 2), f"exit {code}"))

        authoring_only = root / "authoring-only"
        archetype, facts_requirement, authoring_record = record("utility")
        sentinel = "AUTHOR_ONLY_SENTINEL must never become a runtime instruction."
        for qid in ("q1", "q2", "q3", "q4", "q5", "q6", "q7"):
            authoring_record[qid] = f"{sentinel} Interview field {qid}."
        record_path = root / "authoring-only.json"
        record_path.write_text(json.dumps(authoring_record), encoding="utf-8")
        run = subprocess.run([
            sys.executable, str(SCAFFOLD), "--slug", "authoring-only", "--archetype", archetype,
            "--category", "general", "--record", str(record_path), "--dest", str(authoring_only),
            "--facts-requirement", facts_requirement, "--facts-not-applicable-reason",
            "formatter makes no evidence-bearing claims",
        ], capture_output=True, text=True, env=EVAL_ENV)
        generated_text = (authoring_only / "authoring-only" / "SKILL.md").read_text(encoding="utf-8")
        results.append(("interview prose is not copied into distributable runtime instructions",
                        run.returncode == 0 and sentinel not in generated_text,
                        "absent" if sentinel not in generated_text else "leaked"))

        branch_record_path = root / "selected-branch-outputs.json"
        _, branch_facts_requirement, branch_record = record("protocol")
        branch_record["clarification_branches"][0]["artifact_paths"] = ["example_result.json"]
        branch_record["clarification_branches"][1]["artifact_paths"] = ["provided_result.json"]
        branch_record_path.write_text(json.dumps(branch_record), encoding="utf-8")
        branch_dest = root / "selected-branch-outputs"
        run = subprocess.run([
            sys.executable, str(SCAFFOLD), "--slug", "selected-branch-outputs",
            "--archetype", "protocol-workflow", "--category", "general",
            "--record", str(branch_record_path), "--dest", str(branch_dest),
            "--facts-requirement", branch_facts_requirement,
        ], capture_output=True, text=True, env=EVAL_ENV)
        branch_text = (branch_dest / "selected-branch-outputs" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        results.append((
            "receipt outputs are derived from selected clarification branches at runtime",
            run.returncode == 0
            and "outputs_for_selected_branches" in branch_text
            and "outputs=selected_outputs" in branch_text
            and "outputs=['example_result.json', 'provided_result.json']" not in branch_text,
            "runtime-derived" if "outputs=selected_outputs" in branch_text else "static union",
        ))

        no_figure_record_path = root / "analysis-without-figures.json"
        _, no_figure_facts_requirement, no_figure_record = record("quantitative")
        no_figure_record["figures"] = {
            "applicable": False,
            "not_applicable_reason": "the requested analysis has no interpretable result figure",
        }
        no_figure_record_path.write_text(json.dumps(no_figure_record), encoding="utf-8")
        no_figure_dest = root / "analysis-without-figures"
        run = subprocess.run([
            sys.executable, str(SCAFFOLD), "--slug", "analysis-without-figures",
            "--archetype", "analysis-workflow", "--category", "general",
            "--record", str(no_figure_record_path), "--dest", str(no_figure_dest),
            "--facts-requirement", no_figure_facts_requirement,
        ], capture_output=True, text=True, env=EVAL_ENV)
        no_figure_text = (no_figure_dest / "analysis-without-figures" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        results.append((
            "analysis workflows skip figure validation when figures are not applicable",
            run.returncode == 0
            and 'assert_figures("figures/manifest.json")' not in no_figure_text
            and "figures = []" in no_figure_text,
            "guarded" if 'assert_figures("figures/manifest.json")' not in no_figure_text
            else "assert_figures called",
        ))

        base = fixtures["quantitative"]
        broken_root = root / "broken"
        broken_root.mkdir()

        def expect(rule: str, change, case: str = "exact broken shape") -> None:
            fixture_name = f"{rule.lower()}-{re.sub(r'[^a-z0-9]+', '-', case.lower()).strip('-')}"
            pkg = mutate(base, broken_root / fixture_name, change)
            code, output = run_check(pkg)
            results.append((f"{rule} rejects {case}", code == 1 and rule in output,
                            f"exit {code}, {'fired' if rule in output else 'silent'}"))

        def prompt_mutation(value: str, case: str) -> None:
            def change(data: dict, pkg: pathlib.Path) -> None:
                data["starting_task"]["user_prompt"] = value
                md = pkg / "SKILL.md"
                text = md.read_text(encoding="utf-8")
                old = next(line for line in text.splitlines() if line.startswith("starting-prompt:"))
                md.write_text(
                    text.replace(old, f"starting-prompt: {json.dumps(value)}"),
                    encoding="utf-8",
                )

            expect("EV002", change, case)

        missing_parent = broken_root / "ev001"
        missing_parent.mkdir()
        missing = missing_parent / base.name
        shutil.copytree(base, missing)
        (missing / "skill_contract.json").unlink()
        code, output = run_check(missing)
        results.append(("EV001 requires the versioned contract", code == 1 and "EV001" in output,
                        f"exit {code}"))

        expect("EV002", lambda data, pkg: data["starting_task"].update(subject_input="<dataset>"))
        malformed_starting_task = mutate(
            base, broken_root / "ev002-nonstring-starting-task",
            lambda data, pkg: data["starting_task"].update(user_prompt=[]),
        )
        code, output = run_check(malformed_starting_task)
        results.append((
            "EV002 reports a non-string starting-task scalar without crashing",
            code == 1 and "EV002" in output and "Traceback" not in output,
            f"exit {code}, {'traceback' if 'Traceback' in output else 'clean finding'}",
        ))
        _, _, malformed_record = record("quantitative")
        malformed_record["starting_task"]["user_prompt"] = 7
        malformed_record_path = broken_root / "malformed-scaffold-record.json"
        malformed_record_path.write_text(json.dumps(malformed_record), encoding="utf-8")
        malformed_scaffold = subprocess.run([
            sys.executable, str(SCAFFOLD), "--slug", "malformed-starting-task",
            "--archetype", "analysis-workflow", "--category", "general",
            "--record", str(malformed_record_path), "--facts-requirement", "required",
            "--dry-run",
        ], capture_output=True, text=True, env=EVAL_ENV)
        malformed_output = malformed_scaffold.stdout + malformed_scaffold.stderr
        results.append((
            "scaffolder rejects non-string starting-task scalars without crashing",
            malformed_scaffold.returncode != 0 and "starting_task must define" in malformed_output
            and "Traceback" not in malformed_output,
            f"exit {malformed_scaffold.returncode}",
        ))
        _, _, missing_policy_record = record("quantitative")
        missing_policy_record.pop("deliverable_policy")
        missing_policy_path = broken_root / "missing-policy-record.json"
        missing_policy_path.write_text(json.dumps(missing_policy_record), encoding="utf-8")
        missing_policy_scaffold = subprocess.run([
            sys.executable, str(SCAFFOLD), "--slug", "missing-policy",
            "--archetype", "analysis-workflow", "--category", "general",
            "--record", str(missing_policy_path), "--facts-requirement", "required", "--dry-run",
        ], capture_output=True, text=True, env=EVAL_ENV)
        missing_policy_output = missing_policy_scaffold.stdout + missing_policy_scaffold.stderr
        results.append((
            "scaffolder refuses to guess report and infographic applicability",
            missing_policy_scaffold.returncode != 0 and "deliverable_policy" in missing_policy_output,
            f"exit {missing_policy_scaffold.returncode}",
        ))
        _, _, no_report_record = record("utility")
        no_report_record["deliverable_policy"]["report"] = {
            "required": False,
            "not_applicable_reason": "composable helper",
        }
        no_report_path = broken_root / "no-report-record.json"
        no_report_path.write_text(json.dumps(no_report_record), encoding="utf-8")
        no_report_scaffold = subprocess.run([
            sys.executable, str(SCAFFOLD), "--slug", "no-report",
            "--archetype", "format-utility", "--category", "general",
            "--record", str(no_report_path), "--facts-requirement", "not_applicable",
            "--facts-not-applicable-reason", "formatter makes no evidence-bearing claims",
            "--dry-run",
        ], capture_output=True, text=True, env=EVAL_ENV)
        no_report_output = no_report_scaffold.stdout + no_report_scaffold.stderr
        results.append((
            "scaffolder requires a PDF report for every generated skill",
            no_report_scaffold.returncode != 0 and "every generated skill" in no_report_output,
            f"exit {no_report_scaffold.returncode}",
        ))
        _, _, overclaimed_record = record("quantitative")
        overclaimed_record["maturity"] = "user_validated"
        overclaimed_path = broken_root / "overclaimed-maturity-record.json"
        overclaimed_path.write_text(json.dumps(overclaimed_record), encoding="utf-8")
        overclaimed_scaffold = subprocess.run([
            sys.executable, str(SCAFFOLD), "--slug", "overclaimed-maturity",
            "--archetype", "analysis-workflow", "--category", "general",
            "--record", str(overclaimed_path), "--facts-requirement", "required", "--dry-run",
        ], capture_output=True, text=True, env=EVAL_ENV)
        overclaimed_output = overclaimed_scaffold.stdout + overclaimed_scaffold.stderr
        results.append((
            "creation runs cannot self-assign user-validated maturity",
            overclaimed_scaffold.returncode != 0 and "cannot self-assign" in overclaimed_output,
            f"exit {overclaimed_scaffold.returncode}",
        ))
        prompt_mutation(
            "Analyze the airway data and generate a PDF report with figures and next steps?",
            "deliverable instructions in the user-facing prompt",
        )
        prompt_mutation(
            "Please perform a carefully documented and extensively validated multivariable "
            "differential-expression analysis of the complete Bioconductor airway dataset, "
            "including every possible sensitivity analysis and a detailed comparison of all "
            "reasonable modeling alternatives before selecting the final result?",
            "a user-facing prompt longer than the bounded sample-question budget",
        )
        prompt_mutation(
            "Identify dexamethasone-responsive genes after adjusting for cell line",
            "a sample prompt that is not phrased as a research question",
        )
        methods_question = json.loads((base / "skill_contract.json").read_text(encoding="utf-8"))
        methods_question["starting_task"]["user_prompt"] = (
            "Which methods best distinguish treatment effects from batch effects in the airway data?"
        )
        methods_pkg = broken_root / "valid-methods-question" / base.name
        shutil.copytree(base, methods_pkg)
        (methods_pkg / "skill_contract.json").write_text(
            json.dumps(methods_question, indent=2) + "\n",
            encoding="utf-8",
        )
        methods_md = methods_pkg / "SKILL.md"
        methods_text = methods_md.read_text(encoding="utf-8")
        methods_old = next(
            line for line in methods_text.splitlines() if line.startswith("starting-prompt:")
        )
        methods_md.write_text(
            methods_text.replace(
                methods_old,
                f"starting-prompt: {json.dumps(methods_question['starting_task']['user_prompt'])}",
            ),
            encoding="utf-8",
        )
        methods_eval = methods_pkg / "eval.yaml"
        base_prompt = json.loads(
            base.joinpath("skill_contract.json").read_text(encoding="utf-8")
        )["starting_task"]["user_prompt"]
        methods_eval.write_text(
            methods_eval.read_text(encoding="utf-8").replace(
                base_prompt,
                methods_question["starting_task"]["user_prompt"],
            ),
            encoding="utf-8",
        )
        code, output = run_check(methods_pkg)
        results.append((
            "EV002 permits scientific questions that legitimately mention methods",
            code == 0 and "EV002" not in output,
            f"exit {code}, {'clean' if 'EV002' not in output else 'fired'}",
        ))
        expect("EV004", lambda data, pkg: data["facts"].update(requirement="not_applicable", not_applicable_reason=""))
        expect("EV005", lambda data, pkg: data["facts"].update(partition_groups=[]))
        expect(
            "EV005",
            lambda data, pkg: data["facts"].update(
                runtime_payload_artifact="./report_facts.json"
            ),
            "a runtime facts payload aliased to report_facts.json",
        )
        expect("EV006", lambda data, pkg: data["source_assertions"][0].update(primary_source_uri=""))
        expect(
            "EV006",
            lambda data, pkg: data.update(resource_identity={
                "applicable": True,
                "artifact": "evidence_table.csv",
                "authoritative_source_uri": "https://example.org/primary-source",
                "identifier_fields": ["doi"],
                "identity_fields": ["doi"],
                "verification_artifact": "reference_identity.json",
                "violation_json_path": "violations_in_output",
                "expected_violations": 0,
                "failure_policy": "exclude_or_not_computable",
                "mismatch_fixture_refs": ["assets/eval/test_profile.py"],
            }),
            "identifier presence without independent identity fields",
        )
        unlinked_identity = mutate(
            fixtures["literature"], broken_root / "ev006-unlinked-identity",
            lambda data, pkg: data["source_assertions"][0]["runtime_witness"].update(
                artifact="other.json"
            ),
        )
        code, output = run_check(unlinked_identity)
        results.append((
            "EV006 rejects resource identity not linked to the runtime receipt witness",
            code == 1 and "EV006" in output,
            f"exit {code}, {'fired' if 'EV006' in output else 'silent'}",
        ))
        def boolean_identity_zero(data, pkg):
            data["resource_identity"]["expected_violations"] = False
            data["source_assertions"][0]["asserted_value"] = False
            data["source_assertions"][0]["runtime_witness"]["expected_value"] = False

        boolean_identity = mutate(
            fixtures["literature"], broken_root / "ev006-boolean-identity-zero",
            boolean_identity_zero,
        )
        code, output = run_check(boolean_identity)
        results.append((
            "EV006 rejects a Boolean false presented as a numeric zero identity-violation count",
            code == 1 and "EV006" in output,
            f"exit {code}, {'fired' if 'EV006' in output else 'silent'}",
        ))
        malformed_identity_fields = mutate(
            fixtures["literature"], broken_root / "ev006-unhashable-identity-field",
            lambda data, pkg: data["resource_identity"].update(identity_fields=[{}]),
        )
        code, output = run_check(malformed_identity_fields)
        results.append((
            "EV006 reports a non-string identity field without crashing",
            code == 1 and "EV006" in output and "Traceback" not in output,
            f"exit {code}, {'traceback' if 'Traceback' in output else 'clean finding'}",
        ))
        expect("EV007", lambda data, pkg: data["source_assertions"][0].update(runtime_witness={}))
        expect(
            "EV007",
            lambda data, pkg: data["source_assertions"][0]["runtime_witness"].update(
                expected_value="not-the-asserted-value"
            ),
            "a runtime witness that disagrees with the asserted source value",
        )
        def boolean_numeric_mismatch(data, pkg):
            data["source_assertions"][0]["asserted_value"] = 1
            data["source_assertions"][0]["runtime_witness"]["expected_value"] = True

        expect(
            "EV007", boolean_numeric_mismatch,
            "a Boolean runtime witness for a numeric source assertion",
        )
        expect(
            "EV007",
            lambda data, pkg: data["source_assertions"][0]["runtime_witness"].update(
                artifact="../outside-witness.json"
            ),
            "a parent-relative source witness outside the results root",
        )
        expect(
            "EV007",
            lambda data, pkg: data["source_assertions"][0]["runtime_witness"].update(
                artifact="/tmp/outside-witness.json"
            ),
            "an absolute source witness outside the results root",
        )
        expect("EV008", lambda data, pkg: data["clarification_branches"][0].update(implementation_refs=[]))
        expect("EV008", lambda data, pkg: data["clarification_questions"][0].update(selection_mode="many"),
               "an invalid clarification selection mode")
        malformed_choice_id = mutate(
            base, broken_root / "ev008-unhashable-choice-id",
            lambda data, pkg: data["clarification_questions"][0]["choices"][0].update(id=[1]),
        )
        code, output = run_check(malformed_choice_id)
        results.append((
            "EV008 reports a non-string choice ID without crashing",
            code == 1 and "EV008" in output and "Traceback" not in output,
            f"exit {code}, {'traceback' if 'Traceback' in output else 'clean finding'}",
        ))
        malformed_artifact_paths = mutate(
            base, broken_root / "ev008-non-array-artifact-paths",
            lambda data, pkg: data["clarification_branches"][0].update(artifact_paths=1),
        )
        code, output = run_check(malformed_artifact_paths)
        results.append((
            "EV008 reports non-array artifact paths without crashing",
            code == 1 and "EV008" in output and "Traceback" not in output,
            f"exit {code}, {'traceback' if 'Traceback' in output else 'clean finding'}",
        ))
        expect("EV009", lambda data, pkg: data["runtime_instructions"].update(workflow=[]),
               "missing structured runtime steps")
        expect("EV009", lambda data, pkg: data["capabilities"]["entries"][0].update(eval_refs=[]))
        expect("EV010", lambda data, pkg: data["capabilities"]["entries"][0].update(status="conditional"))
        expect("EV011", lambda data, pkg: data["validation_matrix"].update(auto={"status": "passed"}))
        expect("EV012", lambda data, pkg: data.update(inference_readiness={"applicable": True}))
        expect("EV013", lambda data, pkg: data.update(external_dependencies={"applicable": True, "services": [{}]}))
        expect("EV014", lambda data, pkg: data["pdf_review"].update(visual_review_required=False))
        expect("EV014", lambda data, pkg: data.update(figures={"applicable": False}),
               "figure non-applicability without a reason")
        expect(
            "EV014", lambda data, pkg: data["execution"].pop("command_output_paths"),
            "bundled execution without an explicit command-produced output set",
        )
        expect("EV015", lambda data, pkg: data.update(maturity="evidence_validated"))
        def stale_maturity(data, pkg):
            (pkg / "run_receipt.json").write_text("{}\n", encoding="utf-8")
            data["maturity"] = "evidence_validated"
            data["validation_matrix"]["auto"] = {
                "status": "passed", "evidence_refs": ["run_receipt.json"],
            }
        expect("EV015", stale_maturity, "a stale SKILL.md maturity after validation")
        expect("EV016", lambda data, pkg: data.update(installation={"approved": False}))
        def mutable_approval(data, pkg):
            data["installation"]["approved"] = True
        expect("EV016", mutable_approval, "mutable approval stored inside the reviewed package")
        expect(
            "EV017",
            lambda data, pkg: data["deliverable_policy"]["report"].update(
                required=False, not_applicable_reason=""
            ),
            "report non-applicability without a reason",
        )
        expect(
            "EV017",
            lambda data, pkg: data["deliverable_policy"]["report"].update(
                required=False, not_applicable_reason="helper output"
            ),
            "an infographic required without a report",
        )
        expect(
            "EV017",
            lambda data, pkg: data["deliverable_policy"]["report"].update(
                default_style_provider="../customer-style"
            ),
            "a report-style provider path that is not a skill slug",
        )
        expect(
            "EV017",
            lambda data, pkg: data["deliverable_policy"]["report"].update(
                explicit_style_override_allowed=False
            ),
            "a generated report contract that disables explicit style composition",
        )
        def remove_style_contract(data, pkg):
            data["deliverable_policy"]["report"].pop("default_style_provider")
            data["deliverable_policy"]["report"].pop("explicit_style_override_allowed")
        legacy = mutate(base, broken_root / "ev017-pre-style-compatible", remove_style_contract)
        code, output = run_check(legacy)
        results.append(("pre-style evidence-v1 contracts remain valid", code in (0, 2), f"exit {code}"))
        expect(
            "EV017",
            lambda data, pkg: data["deliverable_policy"]["report"].pop(
                "default_style_provider"
            ),
            "a partial style contract missing its default provider",
        )
        expect(
            "EV017",
            lambda data, pkg: data["deliverable_policy"]["report"].pop(
                "explicit_style_override_allowed"
            ),
            "a partial style contract missing its override policy",
        )
        expect(
            "EV009",
            lambda data, pkg: data["runtime_instructions"]["data_sources"][0].update(
                commercial_status="not_checked"
            ),
            "an included data source whose commercial terms were not checked",
        )
        def drift_eval(data, pkg):
            path = pkg / "eval.yaml"
            path.write_text(path.read_text(encoding="utf-8").replace(
                data["starting_task"]["user_prompt"], "Which unrelated question?"
            ), encoding="utf-8")
        expect("EV017", drift_eval, "a sample eval prompt that drifted from the contract")
        def drift_sources(data, pkg):
            path = pkg / "DATA_SOURCES.md"
            path.write_text(path.read_text(encoding="utf-8").replace(
                data["runtime_instructions"]["data_sources"][0]["commercial_evidence"],
                "different evidence",
            ), encoding="utf-8")
        expect("EV017", drift_sources, "a data-source projection that drifted from the contract")
        expect("EV004", lambda data, pkg: data.update(facts=None), "a null facts object")
        expect(
            "EV006",
            lambda data, pkg: data["source_assertions"][0].update(retrieved_at="2026-99-99"),
            "an impossible retrieval date",
        )
        expect(
            "EV010",
            lambda data, pkg: data["capabilities"].update(catalog_claim_ids=None),
            "a null catalog claim list",
        )
        expect(
            "EV013",
            lambda data, pkg: data.update(external_dependencies={
                "applicable": True,
                "services": ["pubchem"],
            }),
            "a string external-service entry without traceback",
        )
        expect(
            "EV013",
            lambda data, pkg: data.update(external_dependencies={
                "applicable": True,
                "services": [{
                    "name": "service",
                    "connect_timeout_seconds": 1,
                    "read_timeout_seconds": 1,
                    "max_retries": 0,
                    "wall_clock_budget_seconds": 1,
                    "terminal_states": None,
                    "failure_fixture_refs": ["assets/eval/test_profile.py"],
                }],
            }),
            "a null terminal state list",
        )
        expect(
            "EV013",
            lambda data, pkg: data.update(external_dependencies={"applicable": True, "services": [{
                "name": "service", "connect_timeout_seconds": 1, "read_timeout_seconds": 1,
                "max_retries": 0, "wall_clock_budget_seconds": 1,
                "terminal_states": ["partial"],
            }]}),
            "a service without failure_fixture_refs",
        )
        nonnumeric_budget = mutate(
            base, broken_root / "ev013-nonnumeric-budget",
            lambda data, pkg: data.update(external_dependencies={"applicable": True, "services": [{
                "name": "service", "connect_timeout_seconds": 1, "read_timeout_seconds": 1,
                "max_retries": 0, "wall_clock_budget_seconds": "one minute",
                "terminal_states": ["partial"],
                "failure_fixture_refs": ["assets/eval/test_profile.py"],
            }]}),
        )
        code, output = run_check(nonnumeric_budget)
        results.append((
            "EV013 reports a nonnumeric wall-clock budget without crashing",
            code == 1 and "EV013" in output and "Traceback" not in output,
            f"exit {code}, {'traceback' if 'Traceback' in output else 'clean finding'}",
        ))

        def parent_escape(data, pkg):
            (pkg.parent / "outside.py").write_text("# outside package\n", encoding="utf-8")
            data["clarification_branches"][0]["implementation_refs"] = ["../outside.py"]

        expect("EV008", parent_escape, "a parent-relative implementation reference outside the package")

        def absolute_escape(data, pkg):
            outside = pkg.parent / "absolute-outside.py"
            outside.write_text("# outside package\n", encoding="utf-8")
            data["clarification_branches"][0]["implementation_refs"] = [str(outside.resolve())]

        expect("EV008", absolute_escape, "an absolute implementation reference outside the package")
        expect(
            "EV008",
            lambda data, pkg: data["clarification_branches"][0].update(
                artifact_paths=["../outside-results.csv"]
            ),
            "a parent-relative result artifact outside the results root",
        )
        expect(
            "EV008",
            lambda data, pkg: data["clarification_branches"][0].update(
                artifact_paths=["/tmp/outside-results.csv"]
            ),
            "an absolute result artifact outside the results root",
        )
        malformed_capability = mutate(
            base, broken_root / "ev009-unhashable-capability",
            lambda data, pkg: data["capabilities"]["entries"][0].update(id=[]),
        )
        code, output = run_check(malformed_capability)
        results.append((
            "EV009 reports a non-string capability ID without crashing",
            code == 1 and "EV009" in output and "Traceback" not in output,
            f"exit {code}, {'traceback' if 'Traceback' in output else 'clean finding'}",
        ))
        expect(
            "EV011",
            lambda data, pkg: data["validation_matrix"].update(auto={"status": "failed"}),
            "a failed trial without evidence",
        )
        expect(
            "EV011",
            lambda data, pkg: data["validation_matrix"].update(guided={
                "status": "passed", "evidence_refs": ["assets/eval/test_profile.py"],
                "selected_branch_ids": ["input:not-an-offered-choice"],
            }),
            "a guided trial naming an unknown clarification branch",
        )
        expect(
            "EV011",
            lambda data, pkg: data["validation_matrix"].update(guided={
                "status": "passed", "evidence_refs": ["assets/eval/test_profile.py"],
                "selected_branch_ids": ["input:quantitative-example"],
            }),
            "a guided trial not grounded in a separate user-selected child run",
        )
        def forged_style_receipt(data, pkg):
            (pkg / "run_receipt.json").write_text(json.dumps({
                "evidence": {"report_style_verified": {
                    "style_source": {
                        "kind": "provider_profile",
                        "path": "/workspace/style_root/fake/assets/report_style.json",
                    },
                }},
            }), encoding="utf-8")
            data["validation_matrix"]["auto"] = {
                "status": "passed", "evidence_refs": ["run_receipt.json"],
            }
        expect(
            "EV011", forged_style_receipt,
            "a validation receipt backed by a workspace-created style source",
        )
        nonstring_branch = mutate(
            base, broken_root / "ev011-nonstring-branch",
            lambda data, pkg: data["validation_matrix"].update(guided={
                "status": "passed", "evidence_refs": ["assets/eval/test_profile.py"],
                "selected_branch_ids": [{}],
            }),
        )
        code, output = run_check(nonstring_branch)
        results.append((
            "EV011 reports a non-string selected branch ID without crashing",
            code == 1 and "EV011" in output and "Traceback" not in output,
            f"exit {code}, {'traceback' if 'Traceback' in output else 'clean finding'}",
        ))

        vague = mutate(base, broken_root / "ev003", lambda data, pkg: data["starting_task"].update(subject_input="my data"))
        # Keep the derived prompt synchronized so this isolates the warning rather than EV002.
        contract = json.loads((vague / "skill_contract.json").read_text(encoding="utf-8"))
        md = vague / "SKILL.md"
        text = md.read_text(encoding="utf-8")
        old = next(line for line in text.splitlines() if line.startswith("starting-prompt:"))
        task = contract["starting_task"]
        prompt = task["user_prompt"]
        quoted = json.dumps(prompt)
        md.write_text(text.replace(old, f"starting-prompt: {quoted}"), encoding="utf-8")
        code, output = run_check(vague)
        results.append(("EV003 warns on heuristic vagueness without blessing it",
                        code == 2 and "EV003" in output, f"exit {code}"))

        # Runtime gates: agreement, trace linkage, and text extraction cannot impersonate visual QA.
        runtime_helper = root / "runtime_report_qc.py"
        shutil.copy2(ROOT / "templates" / "report_qc.py", runtime_helper)
        shutil.copy2(ROOT / "templates" / "report_style.py", root / "report_style.py")
        spec = importlib.util.spec_from_file_location("evidence_report_qc", runtime_helper)
        report_qc = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(root))
        try:
            spec.loader.exec_module(report_qc)
        finally:
            sys.path.pop(0)
        checker_spec = importlib.util.spec_from_file_location("evidence_checker", CHECK)
        checker = importlib.util.module_from_spec(checker_spec)
        checker_spec.loader.exec_module(checker)
        evidence_spec = importlib.util.spec_from_file_location(
            "evidence_schema", ROOT / "scripts" / "evidence_contract.py"
        )
        evidence_contract = importlib.util.module_from_spec(evidence_spec)
        evidence_spec.loader.exec_module(evidence_contract)
        results.extend([
            ("receipt-v2 schema agrees across checker and runtime",
             checker.RECEIPT_SCHEMA_V2 == report_qc.RECEIPT_SCHEMA_V2,
             checker.RECEIPT_SCHEMA_V2),
            ("receipt-v3 style schema agrees across checker and runtime",
             checker.RECEIPT_SCHEMA_V3 == report_qc.RECEIPT_SCHEMA_V3,
             checker.RECEIPT_SCHEMA_V3),
            ("QC execution-log schema agrees across checker and runtime",
             checker.QC_RUN_LOG_SCHEMA == report_qc.QC_RUN_LOG_SCHEMA,
             checker.QC_RUN_LOG_SCHEMA),
            ("evidence schema agrees across checker and contract validator",
             checker.EVIDENCE_SCHEMA == evidence_contract.SCHEMA,
             checker.EVIDENCE_SCHEMA),
            ("figure-embedding states agree across checker and runtime",
             checker.EMBED_STATES == report_qc.EMBED_STATES,
             str(checker.EMBED_STATES)),
        ])
        runtime = root / "runtime"
        runtime.mkdir()
        report_qc.RESULTS = runtime
        report_qc.TRANSCRIPT = runtime / "execution_trace" / "transcript.jsonl"

        branch_contract = {
            "clarification_questions": [{
                "id": "input", "selection_mode": "single",
                "choices": [{"id": "example"}, {"id": "provided"}],
            }],
            "clarification_branches": [
                {"question_id": "input", "choice_id": "example",
                 "artifact_paths": ["example.csv"]},
                {"question_id": "input", "choice_id": "provided",
                 "artifact_paths": ["provided.csv"]},
            ],
        }
        try:
            selected_outputs = report_qc.outputs_for_selected_branches(
                ["input:provided"], branch_contract
            )
        except AttributeError:
            selected_outputs = []
        results.append((
            "selected clarification branches resolve only their own output paths",
            selected_outputs == ["provided.csv"],
            repr(selected_outputs),
        ))
        try:
            report_qc.outputs_for_selected_branches(
                ["input:provided"], {
                    **branch_contract,
                    "clarification_branches": [
                        *branch_contract["clarification_branches"][:1],
                        {"question_id": "input", "choice_id": "provided",
                         "artifact_paths": ["../outside.csv"]},
                    ],
                },
            )
        except report_qc.GateFailure:
            results.append(("runtime branch routing rejects result-root escapes", True, "raised"))
        else:
            results.append(("runtime branch routing rejects result-root escapes", False, "accepted"))

        facts_contract = {
            "facts": {
                "requirement": "required",
                "headline_definitions": [
                    {"field": "completion.completed", "operational_definition": "verified outcomes"},
                ],
                "partition_groups": [
                    {"name": "completion", "denominator_field": "completion.eligible",
                     "member_fields": ["completion.completed", "completion.not_computable"],
                     "identity": "sum_members_equals_denominator"},
                ],
            },
        }
        report_qc.assert_semantic_facts(
            {"completion": {"eligible": 10, "completed": 8, "not_computable": 2}}, facts_contract,
        )
        try:
            report_qc.assert_semantic_facts(
                {"completion": {"eligible": 10, "completed": 8, "not_computable": 1}}, facts_contract,
            )
        except report_qc.GateFailure:
            results.append(("semantic facts reject an unaccounted denominator", True, "raised"))
        else:
            results.append(("semantic facts reject an unaccounted denominator", False, "accepted 9 of 10"))

        write_text_pdf(runtime / "report.pdf", [
            "Task Context", "Methods & Sources", "Results",
            "Conclusions & Interpretation", "Limitations",
        ])
        (runtime / "report.txt").write_text("extractable text", encoding="utf-8")
        (runtime / "page-1.png").write_bytes(b"PNG" + b"x" * 2_000)
        report_sha256 = report_qc._sha256(runtime / "report.pdf")
        text_event = {"type": "pdf_text_extraction", "trace_event_id": "text",
                      "report": "report.pdf", "report_sha256": report_sha256,
                      "artifact": "report.txt",
                      "artifact_sha256": report_qc._sha256(runtime / "report.txt")}
        try:
            report_qc._pdf_review_evidence([text_event], "report.pdf")
        except report_qc.GateFailure:
            results.append(("text extraction alone cannot claim PDF visual QA", True, "raised"))
        else:
            results.append(("text extraction alone cannot claim PDF visual QA", False, "accepted"))

        render_event = {"type": "pdf_render", "trace_event_id": "render", "report": "report.pdf",
                        "report_sha256": report_sha256,
                        "pages": [{"page": 1, "image": "page-1.png",
                                   "image_sha256": report_qc._sha256(runtime / "page-1.png")}]}
        review_event = {"type": "pdf_visual_review", "trace_event_id": "review",
                        "report": "report.pdf", "report_sha256": report_sha256,
                        "pages": [], "review_evidence": "media:event"}
        try:
            report_qc._pdf_review_evidence([text_event, render_event, review_event], "report.pdf")
        except report_qc.GateFailure:
            results.append(("visual QA must cover every rendered PDF page", True, "raised"))
        else:
            results.append(("visual QA must cover every rendered PDF page", False, "accepted"))

        (runtime / "witness.json").write_text('{"value": 8}\n', encoding="utf-8")
        try:
            report_qc.assert_source_witnesses({"source_assertions": [{
                "id": "mismatch", "asserted_value": 7,
                "runtime_witness": {"artifact": "witness.json", "json_path": "value",
                                    "expected_value": 8},
            }]})
        except report_qc.GateFailure:
            results.append(("runtime source witness must equal the asserted source value", True, "raised"))
        else:
            results.append(("runtime source witness must equal the asserted source value", False, "accepted"))
        (runtime / "boolean-witness.json").write_text('{"value": true}\n', encoding="utf-8")
        try:
            report_qc.assert_source_witnesses({"source_assertions": [{
                "id": "boolean-numeric", "asserted_value": 1,
                "runtime_witness": {"artifact": "boolean-witness.json", "json_path": "value",
                                    "expected_value": True},
            }]})
        except report_qc.GateFailure:
            results.append(("runtime source witnesses preserve JSON Boolean/number types", True, "raised"))
        else:
            results.append(("runtime source witnesses preserve JSON Boolean/number types", False, "accepted"))
        try:
            report_qc.assert_source_witnesses({"source_assertions": [{
                "id": "escape", "asserted_value": 8,
                "runtime_witness": {"artifact": "../outside.json", "json_path": "value",
                                    "expected_value": 8},
            }]})
        except report_qc.GateFailure:
            results.append(("runtime source witness must stay beneath the results root", True, "raised"))
        else:
            results.append(("runtime source witness must stay beneath the results root", False, "accepted"))
        outside_witness = root / "outside-witness.json"
        outside_witness.write_text('{"value": 8}\n', encoding="utf-8")
        (runtime / "linked-witness.json").symlink_to(outside_witness)
        try:
            report_qc.assert_source_witnesses({"source_assertions": [{
                "id": "symlink-escape", "asserted_value": 8,
                "runtime_witness": {"artifact": "linked-witness.json", "json_path": "value",
                                    "expected_value": 8},
            }]})
        except report_qc.GateFailure:
            results.append(("runtime source witnesses reject symlink escapes", True, "raised"))
        else:
            results.append(("runtime source witnesses reject symlink escapes", False, "accepted"))

        runtime.joinpath("bad_ledger.json").write_text(json.dumps({
            "schema": "phylo-execution-ledger/1",
            "events": [{"trace_event_id": "invented"}],
        }), encoding="utf-8")
        try:
            report_qc._load_qc_run_log("bad_ledger.json")
        except report_qc.GateFailure:
            results.append(("receipts reject author-composed execution ledgers", True, "raised"))
        else:
            results.append(("receipts reject author-composed execution ledgers", False, "accepted"))

        report_qc.SKILL_ROOT = ROOT
        report_qc.run_bundled(
            [sys.executable, str(ROOT / "scripts" / "evidence_contract.py"), "--help"],
            "scripts/evidence_contract.py", [], log_path="qc_run_log.json",
        )
        qc_log = json.loads((runtime / "qc_run_log.json").read_text(encoding="utf-8"))
        results.append(("run_bundled writes measured command evidence without transcript IDs",
                        qc_log.get("generated_by") == "report_qc"
                        and qc_log.get("events", [{}])[0].get("exit_status") == 0
                        and "trace_event_id" not in qc_log.get("events", [{}])[0],
                        qc_log.get("schema", "missing")))

        stale_output = runtime / "stale.csv"
        stale_output.write_text("created by an earlier run\n", encoding="utf-8")
        no_op = root / "no_op.py"
        no_op.write_text("# successful command that does not touch the expected output\n", encoding="utf-8")
        report_qc.run_bundled(
            [sys.executable, str(no_op)], str(no_op), [str(stale_output)],
            log_path="stale_output_log.json",
        )
        stale_log = json.loads((runtime / "stale_output_log.json").read_text(encoding="utf-8"))
        stale_produced = stale_log["events"][0].get("produced_artifacts")
        results.append((
            "run_bundled does not credit an unchanged output left by an earlier run",
            stale_produced == [],
            repr(stale_produced),
        ))

        changed_output = runtime / "changed.csv"
        changed_output.write_text("old content\n", encoding="utf-8")
        writer = root / "write_output.py"
        writer.write_text(
            "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('current run content\\n')\n",
            encoding="utf-8",
        )
        report_qc.run_bundled(
            [sys.executable, str(writer), str(changed_output)], str(writer), [str(changed_output)],
            log_path="changed_output_log.json",
        )
        changed_log = json.loads((runtime / "changed_output_log.json").read_text(encoding="utf-8"))
        changed_produced = changed_log["events"][0].get("produced_artifacts", [])
        results.append((
            "run_bundled credits an output whose content changed during the command",
            len(changed_produced) == 1 and changed_produced[0].get("path") == str(changed_output),
            repr(changed_produced),
        ))

        bundled = root / "retry_command.py"
        bundled.write_text("# command identity fixture\n", encoding="utf-8")
        stale = runtime / "retry.csv"
        stale.write_text("old success\n", encoding="utf-8")
        retry_log = {
            "schema": report_qc.QC_RUN_LOG_SCHEMA, "generated_by": "report_qc", "events": [
                {"type": "command", "bundled_file": str(bundled),
                 "bundled_sha256": report_qc._sha256(bundled), "exit_status": 0,
                 "produced_artifacts": [{"path": "retry.csv",
                                         "sha256": report_qc._sha256(stale)}]},
                {"type": "command", "bundled_file": str(bundled),
                 "bundled_sha256": report_qc._sha256(bundled), "exit_status": 1,
                 "produced_artifacts": []},
            ],
        }
        (runtime / "latest_attempt_log.json").write_text(json.dumps(retry_log), encoding="utf-8")
        report_qc.write_receipt(
            report_name="missing.pdf", figures=[], bundled_files=[str(bundled)],
            outputs=["retry.csv"], infographics=[], qc_run_log="latest_attempt_log.json",
            figure_not_applicable_reason="no figure", contract={
            "facts": {"requirement": "not_applicable", "not_applicable_reason": "fixture has no facts"},
            "source_assertions": [],
            "source_assertions_not_applicable_reason": "none"}, strict=False,
            path="latest_attempt_receipt.json",
        )
        latest_receipt = json.loads(
            (runtime / "latest_attempt_receipt.json").read_text(encoding="utf-8")
        )
        results.append((
            "receipt ignores an older success after the latest command attempt fails",
            latest_receipt.get("execution_contract_satisfied") is False
            and latest_receipt.get("outputs_appeared") is False,
            str((latest_receipt.get("execution_contract_satisfied"),
                 latest_receipt.get("outputs_appeared"))),
        ))

        branch_a = runtime / "branch-a.csv"
        branch_b = runtime / "branch-b.csv"
        branch_a.write_text("a\n", encoding="utf-8")
        branch_b.write_text("b\n", encoding="utf-8")
        multi_log = {
            "schema": report_qc.QC_RUN_LOG_SCHEMA, "generated_by": "report_qc", "events": [
                {"type": "command", "bundled_file": str(bundled), "invocation_id": "branch-a",
                 "bundled_sha256": report_qc._sha256(bundled), "exit_status": 0,
                 "produced_artifacts": [{"path": "branch-a.csv",
                                         "sha256": report_qc._sha256(branch_a)}]},
                {"type": "command", "bundled_file": str(bundled), "invocation_id": "branch-b",
                 "bundled_sha256": report_qc._sha256(bundled), "exit_status": 0,
                 "produced_artifacts": [{"path": "branch-b.csv",
                                         "sha256": report_qc._sha256(branch_b)}]},
            ],
        }
        (runtime / "multi_invocation_log.json").write_text(json.dumps(multi_log), encoding="utf-8")
        report_qc.write_receipt(
            report_name="missing.pdf", figures=[], bundled_files=[str(bundled)],
            outputs=["branch-a.csv", "branch-b.csv"], infographics=[],
            qc_run_log="multi_invocation_log.json", figure_not_applicable_reason="no figure",
            contract={"facts": {"requirement": "not_applicable", "not_applicable_reason": "fixture has no facts"},
                      "source_assertions": [], "source_assertions_not_applicable_reason": "none"},
            strict=False, path="multi_invocation_receipt.json",
        )
        multi_receipt = json.loads(
            (runtime / "multi_invocation_receipt.json").read_text(encoding="utf-8")
        )
        executed = multi_receipt.get("evidence", {}).get(
            "execution_contract_satisfied", {}).get("executed", [{}])
        results.append((
            "receipt preserves distinct invocations of the same bundled command",
            multi_receipt.get("execution_contract_satisfied") is True
            and multi_receipt.get("outputs_appeared") is True
            and len(executed[0].get("qc_log_events", [])) == 2,
            str((multi_receipt.get("execution_contract_satisfied"),
                 multi_receipt.get("outputs_appeared"))),
        ))

        tool_output = runtime / "tool-output.csv"
        tool_output.write_text("created by a non-bundled tool\n", encoding="utf-8")
        report_qc.write_receipt(
            report_name="missing.pdf", figures=[], bundled_files=[], outputs=["tool-output.csv"],
            infographics=[], qc_run_log="qc_run_log.json",
            figure_not_applicable_reason="no figure",
            contract={
                "facts": {"requirement": "not_applicable", "not_applicable_reason": "fixture has no facts"},
                "execution": {"bundled_commands_applicable": False,
                              "not_applicable_reason": "output is produced by a platform tool"},
                "source_assertions": [], "source_assertions_not_applicable_reason": "none",
            }, strict=False, path="tool-output-receipt.json",
        )
        tool_receipt = json.loads(
            (runtime / "tool-output-receipt.json").read_text(encoding="utf-8")
        )
        results.append((
            "receipt accepts filesystem evidence when bundled commands do not apply",
            tool_receipt.get("execution_contract_satisfied") is True
            and tool_receipt.get("outputs_appeared") is True
            and tool_receipt.get("evidence", {}).get("outputs_appeared", {}).get("method")
            == "filesystem with resolved results-root containment",
            str((tool_receipt.get("execution_contract_satisfied"),
                 tool_receipt.get("outputs_appeared"))),
        ))

        command_output = runtime / "command-output.csv"
        command_output.write_text("command\n", encoding="utf-8")
        mixed_log = {
            "schema": report_qc.QC_RUN_LOG_SCHEMA, "generated_by": "report_qc", "events": [{
                "type": "command", "bundled_file": str(bundled), "invocation_id": "mixed",
                "bundled_sha256": report_qc._sha256(bundled), "exit_status": 0,
                "produced_artifacts": [{"path": "command-output.csv",
                                         "sha256": report_qc._sha256(command_output)}],
            }],
        }
        (runtime / "mixed-output-log.json").write_text(json.dumps(mixed_log), encoding="utf-8")
        report_qc.write_receipt(
            report_name="missing.pdf", figures=[], bundled_files=[str(bundled)],
            outputs=["command-output.csv", "tool-output.csv"], infographics=[],
            qc_run_log="mixed-output-log.json", figure_not_applicable_reason="no figure",
            contract={
                "facts": {"requirement": "not_applicable", "not_applicable_reason": "fixture has no facts"},
                "execution": {"bundled_commands_applicable": True,
                              "command_output_paths": ["command-output.csv"]},
                "source_assertions": [], "source_assertions_not_applicable_reason": "none",
            }, strict=False, path="mixed-output-receipt.json",
        )
        mixed_receipt = json.loads(
            (runtime / "mixed-output-receipt.json").read_text(encoding="utf-8")
        )
        appeared = mixed_receipt.get("evidence", {}).get("outputs_appeared", {}).get("appeared", [])
        results.append((
            "receipt requires command provenance only for declared command-produced outputs",
            mixed_receipt.get("outputs_appeared") is True
            and {item.get("provenance") for item in appeared} == {"command", "filesystem"},
            str(mixed_receipt.get("outputs_appeared")),
        ))

        outside_output = root / "outside-output.csv"
        outside_output.write_text("outside\n", encoding="utf-8")
        (runtime / "linked-output.csv").symlink_to(outside_output)
        report_qc.write_receipt(
            report_name="missing.pdf", figures=[], bundled_files=[], outputs=["linked-output.csv"],
            infographics=[], qc_run_log="qc_run_log.json",
            figure_not_applicable_reason="no figure",
            contract={
                "facts": {"requirement": "not_applicable", "not_applicable_reason": "fixture has no facts"},
                "execution": {"bundled_commands_applicable": False,
                              "not_applicable_reason": "output is produced by a platform tool"},
                "source_assertions": [], "source_assertions_not_applicable_reason": "none",
            }, strict=False, path="linked-output-receipt.json",
        )
        linked_receipt = json.loads(
            (runtime / "linked-output-receipt.json").read_text(encoding="utf-8")
        )
        results.append((
            "receipt rejects a declared output whose symlink target escapes results",
            linked_receipt.get("outputs_appeared") is False
            and "does not resolve beneath the results root"
            in linked_receipt.get("outputs_appeared_reason", ""),
            linked_receipt.get("outputs_appeared_reason", "missing reason"),
        ))

        (runtime / "helper.md").write_text("# Validated helper output\n", encoding="utf-8")
        report_qc.write_receipt(
            report_name=None, figures=[], bundled_files=[], outputs=["helper.md"],
            infographics=[], qc_run_log="missing-helper-log.json",
            figure_not_applicable_reason="the helper has no result figure",
            contract={
                "facts": {"requirement": "not_applicable", "not_applicable_reason": "formatter makes no evidence-bearing claims"},
                "deliverable_policy": {
                    "report": {"required": False, "not_applicable_reason": "composable helper"},
                    "infographic": {"required": False,
                                    "not_applicable_reason": "no explanatory visual is needed"},
                },
                "execution": {"bundled_commands_applicable": False,
                              "not_applicable_reason": "output is produced by a platform tool"},
                "source_assertions": [],
                "source_assertions_not_applicable_reason": "the helper uses only user input",
            }, path="helper-receipt.json",
        )
        helper_receipt = json.loads(
            (runtime / "helper-receipt.json").read_text(encoding="utf-8")
        )
        report_only = {
            "report_at_results_root", "report_branded", "text_extracted", "pages_rendered",
            "visual_review_attested", "report_sections_present", "infographic_lineage_verified",
            "facts_artifact_verified",
        }
        results.append((
            "filesystem-only helpers need no preexisting log and record typed not-applicable outcomes",
            all(helper_receipt.get(key) == "not_applicable" for key in report_only)
            and helper_receipt.get("execution_contract_satisfied") is True
            and helper_receipt.get("outputs_appeared") is True
            and helper_receipt.get("source_assertions_verified") is True,
            repr({key: helper_receipt.get(key) for key in sorted(report_only)}),
        ))

        original_copy2 = report_qc.shutil.copy2
        original_copyfile = report_qc.shutil.copyfile

        def reject_copy2(*args, **kwargs):
            raise PermissionError("object-backed results mount rejects copystat")

        def reject_in_place_copy(src, dst, *args, **kwargs):
            if pathlib.Path(dst).exists() or pathlib.Path(dst).is_symlink():
                raise PermissionError("object-backed results mount rejects in-place truncation")
            return original_copyfile(src, dst, *args, **kwargs)

        report_qc.shutil.copy2 = reject_copy2
        report_qc.shutil.copyfile = reject_in_place_copy
        try:
            report_qc.record_pdf_review(
                "report.pdf", "report.txt", ["page-1.png"], [1], "first review",
                "pass", [],
                log_path="pdf_retry_log.json",
            )
        except PermissionError as exc:
            results.append((
                "record_pdf_review publishes retries without unsupported metadata or truncation",
                False,
                str(exc),
            ))
        else:
            results.append((
                "record_pdf_review publishes retries without unsupported metadata or truncation",
                True,
                "destinations were unlinked before byte publication",
            ))
        finally:
            report_qc.shutil.copy2 = original_copy2
            report_qc.shutil.copyfile = original_copyfile
        report_qc.record_pdf_review(
            "report.pdf", "report.txt", ["page-1.png"], [1],
            "blue divider and indistinguishable series", "fail",
            ["section divider uses the wrong provider accent"],
            log_path="pdf_retry_log.json",
        )
        failed_review_events = json.loads(
            (runtime / "pdf_retry_log.json").read_text(encoding="utf-8")
        )["events"]
        original_page_count = report_qc._pdf_page_count
        report_qc._pdf_page_count = lambda report: 1
        try:
            report_qc._pdf_review_evidence(failed_review_events, "report.pdf")
        except report_qc.GateFailure as exc:
            blocked_failed_review = "did not pass" in str(exc)
            failed_review_detail = str(exc)
        else:
            blocked_failed_review = False
            failed_review_detail = "failed review accepted"
        finally:
            report_qc._pdf_page_count = original_page_count
        results.append((
            "visual-review issues block a receipt instead of becoming limitations",
            blocked_failed_review,
            failed_review_detail,
        ))
        first_text_sha = report_qc._sha256(runtime / "report.txt")
        first_page_sha = report_qc._sha256(runtime / "page-1.png")
        write_text_pdf(runtime / "report.pdf", [
            "Task Context", "Methods & Sources", "Results", "changed report content",
            "Conclusions & Interpretation", "Limitations",
        ])
        report_qc.record_pdf_review(
            "report.pdf", "report.txt", ["page-1.png"], [1], "replacement review",
            "pass", [],
            log_path="pdf_retry_log.json",
        )
        retry_log = json.loads((runtime / "pdf_retry_log.json").read_text(encoding="utf-8"))
        retry_events = retry_log.get("events", [])
        review_types = ("pdf_text_extraction", "pdf_render", "pdf_visual_review")
        retry_counts = {kind: sum(event.get("type") == kind for event in retry_events)
                        for kind in review_types}
        results.append((
            "record_pdf_review retries replace the prior coherent event set",
            retry_counts == {kind: 1 for kind in review_types},
            repr(retry_counts),
        ))
        results.append((
            "record_pdf_review regenerates extraction and renders from rebuilt PDF bytes",
            report_qc._sha256(runtime / "report.txt") != first_text_sha
            and report_qc._sha256(runtime / "page-1.png") != first_page_sha,
            "both artifacts changed" if (
                report_qc._sha256(runtime / "report.txt") != first_text_sha
                and report_qc._sha256(runtime / "page-1.png") != first_page_sha
            ) else "stale artifact reused",
        ))
        reviewed_text = (runtime / "report.txt").read_bytes()
        (runtime / "report.txt").write_bytes(reviewed_text + b"tampered")
        try:
            report_qc._report_sections_evidence(retry_events, "report.pdf")
        except report_qc.GateFailure:
            results.append(("post-review text-artifact replacement is rejected", True, "raised"))
        else:
            results.append(("post-review text-artifact replacement is rejected", False, "accepted"))
        (runtime / "report.txt").write_bytes(reviewed_text)
        reviewed_page = (runtime / "page-1.png").read_bytes()
        (runtime / "page-1.png").write_bytes(reviewed_page + b"tampered")
        try:
            report_qc._pdf_review_evidence(retry_events, "report.pdf")
        except report_qc.GateFailure:
            results.append(("post-review page-render replacement is rejected", True, "raised"))
        else:
            results.append(("post-review page-render replacement is rejected", False, "accepted"))
        (runtime / "page-1.png").write_bytes(reviewed_page)
        (runtime / "report.pdf").write_bytes(
            (runtime / "report.pdf").read_bytes() + b"changed after review"
        )
        for gate_name, gate in (
            ("PDF review evidence", report_qc._pdf_review_evidence),
            ("report-section evidence", report_qc._report_sections_evidence),
        ):
            try:
                gate(retry_events, "report.pdf")
            except report_qc.GateFailure:
                results.append((f"{gate_name} is bound to the exact reviewed PDF bytes", True, "raised"))
            else:
                results.append((f"{gate_name} is bound to the exact reviewed PDF bytes", False, "accepted"))

        report_qc.write_receipt(
            report_name="missing.pdf", figures=[], bundled_files=[], outputs=[],
            qc_run_log="qc_run_log.json", figure_not_applicable_reason="no result figure applies",
            contract={
                "facts": {"requirement": "not_applicable", "not_applicable_reason": "formatter has no facts"},
                "execution": {"bundled_commands_applicable": False,
                              "not_applicable_reason": "artifact-only formatter"},
                "source_assertions": [],
                "source_assertions_not_applicable_reason": "no external facts",
            }, strict=False,
        )
        receipt = json.loads((runtime / "run_receipt.json").read_text(encoding="utf-8"))
        results.append(("figure non-applicability is explicit rather than reported as an embedded pass",
                        receipt.get("figure_contract_satisfied") is True
                        and receipt.get("figures_embedded") == "not_applicable",
                        str(receipt.get("figures_embedded"))))

    width = max(len(name) for name, _, _ in results)
    for name, passed, detail in results:
        print(f"{name:<{width}}  {'PASS' if passed else 'FAIL'}  {detail}")
    failed = [name for name, passed, _ in results if not passed]
    print(f"\nRESULT: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
