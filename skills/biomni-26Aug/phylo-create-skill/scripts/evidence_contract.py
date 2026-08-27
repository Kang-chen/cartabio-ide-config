#!/usr/bin/env python3
"""Build and validate the evidence contract emitted with every new skill scaffold.

The contract is deliberately domain-neutral. Controls declare when they apply; a formatter does not
pretend to have experimental units, while a quantitative workflow cannot silently omit them.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from datetime import date
from collections.abc import Iterable


SCHEMA = "phylo-skill-evidence/1"
MATURITY_STATES = (
    "generated",
    "structurally_valid",
    "evidence_validated",
    "user_validated",
    "installable",
)
FACTS_REQUIREMENTS = ("required", "not_applicable")
SELECTION_MODES = ("single", "multiple")
STYLE_PROVIDER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STYLE_CONTRACT_KEYS = frozenset({
    "default_style_provider",
    "explicit_style_override_allowed",
})
TASK_ID_RE = re.compile(r"^tsk_[A-Za-z0-9]+$")
CAPABILITY_STATES = ("tested", "conditional", "unsupported")
VALIDATION_STATES = ("passed", "failed", "not_run")
FINAL_STATES = ("complete", "partial", "not_computable")
AUDIENCE_STATES = ("user_facing", "composable_helper")
COMMERCIAL_STATES = ("allowed", "no_prohibition_found", "prohibited", "not_checked")
PARTITION_IDENTITIES = ("sum_members_equals_denominator",)
MAX_EXTERNAL_RETRIES = 5
MAX_EXTERNAL_WALL_CLOCK_SECONDS = 3_600
UNRESOLVED = re.compile(r"(?:<[^>]+>|\.{3})")
VAGUE = re.compile(
    r"(?:\bmy (?:data|dataset|disease|question|experiment)\b|"
    r"\bgene of interest\b|\bas appropriate\b)",
    re.I,
)
MAX_USER_PROMPT_CHARS = 240
INSTALLED_STYLE_SOURCE_PREFIXES = (
    "/mnt/skills/system/",
    "/mnt/skills/user/",
    "/mnt/skills/personal/",
)
DELIVERABLE_PROMPT = re.compile(
    r"\b(?:pdf|infographic|generateimage|deliverables?|references?|next steps?)\b|"
    r"\b(?:generate|produce|create|write|include|provide)\b.{0,48}"
    r"\b(?:reports?|figures?|methods?|conclusions?)\b",
    re.I,
)


def starting_prompt(task: dict[str, str]) -> str:
    """Render the catalog prompt from the bounded user-facing research question."""
    return " ".join(task["user_prompt"].split())


def catalog_description(capabilities: dict) -> str:
    """Render catalog claims only from capabilities marked tested."""
    entries = capabilities.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    by_id = {entry["id"]: entry for entry in entries
             if isinstance(entry, dict) and isinstance(entry.get("id"), str)
             and entry["id"].strip()}
    claims = []
    catalog_claim_ids = capabilities.get("catalog_claim_ids", [])
    if not isinstance(catalog_claim_ids, list):
        catalog_claim_ids = []
    for capability_id in catalog_claim_ids:
        if not isinstance(capability_id, str) or not capability_id.strip():
            continue
        entry = by_id.get(capability_id, {})
        if entry.get("status") == "tested" and entry.get("claim"):
            claims.append(str(entry["claim"]).strip())
    trigger = str(capabilities.get("trigger", "")).strip()
    return " ".join([*claims, trigger]).strip()


def build_contract(
    *, slug: str, archetype: str, record: dict, facts_requirement: str,
    facts_not_applicable_reason: str = "",
) -> dict:
    """Create an explicit contract. Missing author decisions stay visibly unresolved."""
    task = record.get("starting_task") if isinstance(record.get("starting_task"), dict) else {}
    capabilities = record.get("capabilities") if isinstance(record.get("capabilities"), dict) else {}
    facts = record.get("facts") if isinstance(record.get("facts"), dict) else {}
    facts = {
        "requirement": facts_requirement,
        "not_applicable_reason": facts_not_applicable_reason,
        "schema": facts.get("schema", "report_facts.json"),
        "runtime_payload_artifact": facts.get("runtime_payload_artifact", ""),
        "headline_definitions": facts.get("headline_definitions", []),
        "partition_groups": facts.get("partition_groups", []),
        "partition_not_applicable_reason": facts.get("partition_not_applicable_reason", ""),
        "known_answer_eval_refs": facts.get("known_answer_eval_refs", []),
    }
    deliverable_policy = record.get(
        "deliverable_policy",
        {
            "audience": "user_facing",
            "report": {"required": True, "not_applicable_reason": ""},
            "infographic": {"required": True, "not_applicable_reason": ""},
        },
    )
    if isinstance(deliverable_policy, dict):
        deliverable_policy = dict(deliverable_policy)
        report_policy = deliverable_policy.get("report")
        if isinstance(report_policy, dict):
            report_policy = dict(report_policy)
            # Generated skills are portable: enterprise style is a per-run explicit override, never
            # baked into a package merely because authoring occurred inside that enterprise.
            report_policy["default_style_provider"] = "pdf-report-generation"
            report_policy["explicit_style_override_allowed"] = True
            deliverable_policy["report"] = report_policy
    report_required = (
        isinstance(deliverable_policy, dict)
        and isinstance(deliverable_policy.get("report"), dict)
        and deliverable_policy["report"].get("required") is True
    )
    default_pdf_review = (
        {
            "text_extraction_required": True,
            "render_all_pages_required": True,
            "visual_review_required": True,
        }
        if report_required
        else {
            "applicable": False,
            "not_applicable_reason": "The skill does not produce a PDF report.",
        }
    )
    return {
        "schema": SCHEMA,
        "skill": {"slug": slug, "archetype": archetype},
        "maturity": record.get("maturity", "generated"),
        "starting_task": task,
        "deliverable_policy": deliverable_policy,
        "facts": facts,
        "source_assertions": record.get("source_assertions", []),
        "source_assertions_not_applicable_reason": record.get(
            "source_assertions_not_applicable_reason", ""
        ),
        "resource_identity": record.get(
            "resource_identity",
            {"applicable": False, "not_applicable_reason": "unresolved"},
        ),
        "clarification_branches": record.get("clarification_branches", []),
        "clarification_questions": record.get("clarification_questions", []),
        "capabilities": capabilities,
        "runtime_instructions": record.get("runtime_instructions", {}),
        "validation_matrix": record.get("validation_matrix", {}),
        "inference_readiness": record.get(
            "inference_readiness",
            {"applicable": False, "not_applicable_reason": "unresolved"},
        ),
        "external_dependencies": record.get(
            "external_dependencies",
            {"applicable": False, "not_applicable_reason": "unresolved", "services": []},
        ),
        "figures": record.get(
            "figures", {"applicable": False, "not_applicable_reason": "unresolved"}
        ),
        "execution": record.get(
            "execution", {"bundled_commands_applicable": False,
                          "not_applicable_reason": "unresolved"}
        ),
        "pdf_review": record.get("pdf_review", default_pdf_review),
        "installation": record.get(
            "installation",
            {
                "offer_private_preview_after_generation": True,
                "registration_requires_explicit_user_confirmation": True,
                "registration_managed_outside_package": True,
            },
        ),
        "authoring": {
            "generator": "phylo-create-skill",
            "derived_files": ["SKILL.md", "eval.yaml", "DATA_SOURCES.md"],
            "repair_policy": "regenerate derived files from skill_contract.json and rerun the full gate",
        },
    }


def canonical_json(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _same_json_value(left: object, right: object) -> bool:
    """Compare values with JSON type semantics (where true is not the number 1)."""
    try:
        return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == \
            json.dumps(right, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return False


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _missing(record: dict, fields: Iterable[str]) -> list[str]:
    return [
        field for field in fields
        if record.get(field) is None or not str(record.get(field, "")).strip()
    ]


def _references_existing(pkg: pathlib.Path, refs: object) -> list[str]:
    if not isinstance(refs, list):
        return ["reference list is not an array"]
    package_root = pkg.resolve()
    missing = []
    for ref in refs:
        value = str(ref).split("#", 1)[0].strip()
        candidate = pathlib.Path(value)
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (package_root / candidate).resolve()
        )
        try:
            resolved.relative_to(package_root)
        except ValueError:
            missing.append(str(ref))
            continue
        if not value or not resolved.is_file():
            missing.append(str(ref))
    return missing


def _invalid_receipt_style_sources(pkg: pathlib.Path, refs: object) -> list[str]:
    """Reject validation receipts that evidence style with a caller-created source."""
    if not isinstance(refs, list):
        return []
    invalid = []
    package_root = pkg.resolve()
    for ref in refs:
        relative = str(ref).split("#", 1)[0].strip()
        if "receipt" not in pathlib.PurePosixPath(relative).name or not relative.endswith(".json"):
            continue
        path = (package_root / relative).resolve()
        try:
            path.relative_to(package_root)
        except ValueError:
            continue
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        evidence = receipt.get("evidence") if isinstance(receipt, dict) else None
        style = (
            evidence.get("report_style_verified") or evidence.get("report_branded")
            if isinstance(evidence, dict) else None
        )
        source_path = None
        if isinstance(style, dict):
            source = style.get("style_source") or style.get("profile")
            if isinstance(source, dict):
                source_path = source.get("path")
        if not isinstance(source_path, str) or not source_path.startswith(
            INSTALLED_STYLE_SOURCE_PREFIXES
        ):
            invalid.append(relative)
    return invalid


def _invalid_result_paths(paths: object) -> list[str]:
    """Return output paths that are not portable relative paths beneath the results root."""
    if not isinstance(paths, list):
        return ["artifact path list is not an array"]
    invalid = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            invalid.append(repr(raw))
            continue
        path = pathlib.PurePosixPath(raw.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            invalid.append(raw)
    return invalid


def _same_portable_result_path(left: str, right: str) -> bool:
    """Compare two validated results-root paths without letting spelling hide an alias."""
    return pathlib.PurePosixPath(left.replace("\\", "/")) == pathlib.PurePosixPath(
        right.replace("\\", "/")
    )


def validate_contract(pkg: pathlib.Path, data: object, frontmatter: dict[str, str]) -> list[tuple[str, str, str]]:
    """Return ``(rule, severity, message)`` findings for a generated package contract."""
    out: list[tuple[str, str, str]] = []

    def fail(rule: str, message: str) -> None:
        out.append((rule, "FAIL", message))

    def warn(rule: str, message: str) -> None:
        out.append((rule, "WARN", message))

    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        fail("EV001", f"skill_contract.json must be a {SCHEMA!r} object")
        return out

    task = data.get("starting_task")
    required_task = (
        "user_prompt", "subject_input", "objective", "decision_context", "deliverables"
    )
    if not isinstance(task, dict):
        fail("EV002", "starting_task must define a user prompt and four internal execution fields")
    else:
        invalid_types = [field for field in required_task
                         if field in task and not isinstance(task.get(field), str)]
        missing = _missing(task, required_task)
        if invalid_types:
            fail("EV002", f"starting_task fields must be strings: {', '.join(invalid_types)}")
        elif missing:
            fail("EV002", f"starting_task missing: {', '.join(missing)}")
        else:
            rendered = starting_prompt(task)
            actual = frontmatter.get("starting-prompt", "")
            if actual != rendered:
                fail("EV002", "starting-prompt was not derived from skill_contract.json:starting_task")
            if len(rendered) > MAX_USER_PROMPT_CHARS:
                fail("EV002", f"starting-prompt exceeds {MAX_USER_PROMPT_CHARS} characters")
            if not rendered.endswith("?"):
                fail("EV002", "starting-prompt must be a short natural research question ending in '?'")
            if DELIVERABLE_PROMPT.search(rendered):
                fail("EV002", "starting-prompt contains deliverable instructions that belong inside the skill")
            policy = data.get("deliverable_policy")
            report_required = (
                isinstance(policy, dict)
                and isinstance(policy.get("report"), dict)
                and policy["report"].get("required") is True
            )
            if report_required and ".pdf" not in task["deliverables"].lower():
                fail("EV002", "starting_task.deliverables must name the required PDF")
            if not report_required and ".pdf" in task["deliverables"].lower():
                fail("EV002", "starting_task.deliverables names a PDF although reports are not applicable")
            internal = " ".join(str(task[field]) for field in required_task)
            if UNRESOLVED.search(internal):
                fail("EV002", "starting task contains an unresolved placeholder")
            if VAGUE.search(internal):
                warn("EV003", "starting prompt contains a placeholder or vague stand-in; name a concrete subject")

    facts = data.get("facts")
    facts_requirement = facts.get("requirement") if isinstance(facts, dict) else None
    if not isinstance(facts, dict) or facts.get("requirement") not in FACTS_REQUIREMENTS:
        fail("EV004", f"facts.requirement must be one of {', '.join(FACTS_REQUIREMENTS)}")
    elif facts["requirement"] == "required":
        if facts.get("schema") != "report_facts.json":
            fail("EV004", "required facts schema must be the results-root report_facts.json artifact")
        runtime_payload = facts.get("runtime_payload_artifact")
        if not isinstance(runtime_payload, str) or not runtime_payload.strip():
            fail("EV005", "facts are required but no runtime payload artifact is named")
        else:
            invalid = _invalid_result_paths([runtime_payload])
            if invalid:
                fail("EV005", f"facts runtime payload must stay beneath the results root: {invalid}")
            elif _same_portable_result_path(runtime_payload, facts["schema"]):
                fail(
                    "EV005",
                    "facts runtime payload must be distinct from report_facts.json; "
                    "the report facts artifact cannot evidence itself",
                )
        definitions = facts.get("headline_definitions")
        if not isinstance(definitions, list) or not definitions:
            fail("EV005", "facts are required but no operational headline definitions are declared")
        groups = facts.get("partition_groups")
        reason = str(facts.get("partition_not_applicable_reason", "")).strip()
        if not isinstance(groups, list) or (not groups and not reason):
            fail("EV005", "declare denominator/completion partitions or why no partition applies")
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict) or _missing(
                group, ("name", "denominator_field", "identity")
            ) or not isinstance(group.get("member_fields"), list) or not group.get("member_fields"):
                fail("EV005", "each partition group needs name, denominator, members, and an identity")
            elif group["identity"] not in PARTITION_IDENTITIES:
                fail("EV005", f"partition {group['name']!r} uses an unsupported accounting identity")
        known_answer_refs = facts.get("known_answer_eval_refs")
        if not isinstance(known_answer_refs, list) or not known_answer_refs:
            fail("EV005", "evidence-bearing facts need at least one known-answer semantic eval")
        else:
            missing = _references_existing(pkg, known_answer_refs)
            if missing:
                fail("EV005", f"known-answer semantic evals are missing: {missing}")
    elif not str(facts.get("not_applicable_reason", "")).strip():
        fail("EV004", "facts marked not_applicable need a machine-readable reason")

    policy = data.get("deliverable_policy")
    if not isinstance(policy, dict) or policy.get("audience") not in AUDIENCE_STATES:
        fail("EV017", f"deliverable_policy.audience must be one of {', '.join(AUDIENCE_STATES)}")
        policy = {}
    for kind in ("report", "infographic"):
        choice = policy.get(kind) if isinstance(policy, dict) else None
        if not isinstance(choice, dict) or not isinstance(choice.get("required"), bool):
            fail("EV017", f"deliverable_policy.{kind}.required must be explicit")
        elif choice["required"] is False and (
            not str(choice.get("not_applicable_reason", "")).strip()
            or choice.get("not_applicable_reason") == "unresolved"
        ):
            fail("EV017", f"deliverable_policy.{kind} marked not applicable needs a resolved reason")
    report_choice = policy.get("report") if isinstance(policy, dict) else None
    if isinstance(report_choice, dict):
        if report_choice.get("required") is not True:
            fail("EV017", "every generated skill must require a PDF report")
        present_style_keys = STYLE_CONTRACT_KEYS.intersection(report_choice)
        if present_style_keys and present_style_keys != STYLE_CONTRACT_KEYS:
            missing_style_keys = sorted(STYLE_CONTRACT_KEYS.difference(report_choice))
            fail(
                "EV017",
                "deliverable_policy.report has a partial style contract; missing "
                + ", ".join(missing_style_keys),
            )
        elif present_style_keys == STYLE_CONTRACT_KEYS:
            default_provider = report_choice.get("default_style_provider")
            if (
                not isinstance(default_provider, str)
                or not STYLE_PROVIDER_RE.fullmatch(default_provider)
            ):
                fail(
                    "EV017",
                    "deliverable_policy.report.default_style_provider must be a lowercase skill slug",
                )
            if report_choice.get("explicit_style_override_allowed") is not True:
                fail(
                    "EV017",
                    "generated reports must allow an explicitly selected compatible style provider",
                )
    assertions = data.get("source_assertions")
    if not isinstance(assertions, list):
        fail("EV006", "source_assertions must be an array")
    elif (facts_requirement == "required" and not assertions
          and not str(data.get("source_assertions_not_applicable_reason", "")).strip()):
        fail("EV006", "evidence-bearing skill needs verified source assertions or an applicability reason")
    for assertion in assertions if isinstance(assertions, list) else []:
        required = ("id", "field", "asserted_value", "primary_source_uri", "retrieved_at", "verification_method")
        if not isinstance(assertion, dict) or _missing(assertion, required):
            fail("EV006", "each computation-critical source assertion needs identity, source, date, and verification")
            continue
        if not re.match(r"^https?://", str(assertion["primary_source_uri"])):
            fail("EV006", f"source assertion {assertion.get('id')!r} does not name an HTTP(S) primary source")
        try:
            date.fromisoformat(str(assertion["retrieved_at"]))
        except ValueError:
            fail("EV006", f"source assertion {assertion.get('id')!r} has no ISO retrieval date")
        witness = assertion.get("runtime_witness")
        if not isinstance(witness, dict) or _missing(witness, ("artifact", "json_path", "expected_value")):
            fail("EV007", f"source assertion {assertion.get('id')!r} has no runtime witness")
        else:
            if not _same_json_value(
                witness.get("expected_value"), assertion.get("asserted_value")
            ):
                fail("EV007", f"source assertion {assertion.get('id')!r} disagrees with its runtime witness")
            invalid = _invalid_result_paths([witness.get("artifact")])
            if invalid:
                fail("EV007", f"source assertion witness must stay beneath the results root: {invalid}")

    identity = data.get("resource_identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("applicable"), bool):
        fail("EV006", "resource_identity.applicable must be explicit")
    elif identity["applicable"]:
        required_identity = (
            "artifact", "authoritative_source_uri", "verification_artifact",
            "violation_json_path", "failure_policy",
        )
        if _missing(identity, required_identity):
            fail("EV006", "resource identity needs artifacts, an authoritative source, and a failure policy")
        elif not re.match(r"^https?://", str(identity["authoritative_source_uri"])):
            fail("EV006", "resource identity authoritative_source_uri must be HTTP(S)")
        if identity.get("failure_policy") != "exclude_or_not_computable":
            fail("EV006", "resource identity mismatches must be excluded or make the run not_computable")
        if not _same_json_value(identity.get("expected_violations"), 0):
            fail("EV006", "resource identity expected_violations must be the numeric value 0")
        identifiers = identity.get("identifier_fields")
        fields = identity.get("identity_fields")
        identifiers_valid = (
            isinstance(identifiers, list)
            and bool(identifiers)
            and all(isinstance(field, str) and field.strip() for field in identifiers)
        )
        fields_valid = (
            isinstance(fields, list)
            and bool(fields)
            and all(isinstance(field, str) and field.strip() for field in fields)
        )
        if not identifiers_valid:
            fail("EV006", "resource identity needs at least one identifier field")
        if not fields_valid:
            fail("EV006", "resource identity needs non-identifier fields such as title/year or name/version")
        elif identifiers_valid and not set(fields).difference(identifiers):
            fail("EV006", "resource identity cannot be verified by identifier presence alone")
        mismatch_refs = identity.get("mismatch_fixture_refs")
        if not isinstance(mismatch_refs, list) or not mismatch_refs:
            fail("EV006", "resource identity needs a swapped-valid-identifier mismatch fixture")
        elif (missing := _references_existing(pkg, mismatch_refs)):
            fail("EV006", f"resource identity mismatch fixtures are missing: {missing}")
        linked_witnesses = [
            assertion.get("runtime_witness", {})
            for assertion in assertions if isinstance(assertion, dict)
            if isinstance(assertion.get("runtime_witness"), dict)
        ] if isinstance(assertions, list) else []
        if not any(
            witness.get("artifact") == identity.get("verification_artifact")
            and witness.get("json_path") == identity.get("violation_json_path")
            and _same_json_value(witness.get("expected_value"), 0)
            for witness in linked_witnesses
        ):
            fail("EV006", "resource identity violations must be a zero-valued source-assertion witness")
    elif (
        not str(identity.get("not_applicable_reason", "")).strip()
        or identity.get("not_applicable_reason") == "unresolved"
    ):
        fail("EV006", "resource identity marked not applicable without a resolved reason")

    questions = data.get("clarification_questions")
    question_choices: dict[str, set[str]] = {}
    if not isinstance(questions, list) or not questions:
        fail("EV008", "clarification_questions must declare prompts, selection mode, and choices")
    for question in questions if isinstance(questions, list) else []:
        if not isinstance(question, dict) or _missing(question, ("id", "prompt", "selection_mode")):
            fail("EV008", "each clarification question needs id, prompt, and selection_mode")
            continue
        if question["selection_mode"] not in SELECTION_MODES:
            fail("EV008", f"question {question['id']!r} has invalid selection_mode")
        if ":" in str(question["id"]):
            fail("EV008", f"question {question['id']!r} contains reserved branch-ID separator ':'")
        choices = question.get("choices")
        if not isinstance(choices, list) or len(choices) < 2:
            fail("EV008", f"question {question['id']!r} needs at least two explicit choices")
            continue
        ids = [choice.get("id") for choice in choices if isinstance(choice, dict)]
        if len(ids) != len(choices) or any(
            not isinstance(choice_id, str) or not choice_id.strip() for choice_id in ids
        ):
            fail("EV008", f"question {question['id']!r} has a choice without an id")
            continue
        if len(set(ids)) != len(ids):
            fail("EV008", f"question {question['id']!r} has duplicate choice ids")
        if any(":" in str(choice_id) for choice_id in ids):
            fail("EV008", f"question {question['id']!r} has a choice containing reserved ':'")
        question_choices[str(question["id"])] = set(ids)

    branches = data.get("clarification_branches")
    if not isinstance(branches, list) or not branches:
        fail("EV008", "clarification_branches must map every offered choice to implementation and evals")
    for branch in branches if isinstance(branches, list) else []:
        if not isinstance(branch, dict) or _missing(branch, ("question_id", "choice_id", "fallback_status")):
            fail("EV008", "each clarification branch needs question_id, choice_id, and fallback_status")
            continue
        if branch.get("fallback_status") not in FINAL_STATES:
            fail("EV008", f"branch {branch.get('choice_id')!r} has an invalid fallback status")
        if branch.get("choice_id") not in question_choices.get(str(branch.get("question_id")), set()):
            fail("EV008", f"branch {branch.get('choice_id')!r} is not an offered question choice")
        for field in ("implementation_refs", "artifact_paths", "eval_refs"):
            refs = branch.get(field)
            if not isinstance(refs, list) or not refs:
                fail("EV008", f"branch {branch.get('choice_id')!r} has no {field}")
            elif field == "artifact_paths":
                invalid = _invalid_result_paths(refs)
                if invalid:
                    fail("EV008", f"branch {branch.get('choice_id')!r} has output paths outside results: {invalid}")
            else:
                missing = _references_existing(pkg, refs)
                if missing:
                    fail("EV008", f"branch {branch.get('choice_id')!r} references missing {field}: {missing}")
    covered = {(str(branch.get("question_id")), str(branch.get("choice_id")))
               for branch in branches if isinstance(branch, dict)} if isinstance(branches, list) else set()
    for question_id, choices in question_choices.items():
        missing_choices = sorted(choice for choice in choices if (question_id, choice) not in covered)
        if missing_choices:
            fail("EV008", f"question {question_id!r} has unmapped choices: {missing_choices}")

    runtime = data.get("runtime_instructions")
    if not isinstance(runtime, dict):
        fail("EV009", "runtime_instructions must be a structured object, separate from interview prose")
    else:
        inputs = runtime.get("inputs")
        workflow = runtime.get("workflow")
        caveats = runtime.get("caveats")
        sources = runtime.get("data_sources")
        materials = runtime.get("existing_materials")
        if not isinstance(inputs, list) or not inputs or any(not str(item).strip() for item in inputs):
            fail("EV009", "runtime_instructions.inputs must contain concrete input requirements")
        if not isinstance(workflow, list) or not workflow:
            fail("EV009", "runtime_instructions.workflow must contain executable steps")
        for step in workflow if isinstance(workflow, list) else []:
            if not isinstance(step, dict) or _missing(step, ("title", "instruction")):
                fail("EV009", "each runtime workflow step needs title and instruction")
            elif UNRESOLVED.search(f"{step['title']} {step['instruction']}"):
                fail("EV009", "runtime workflow contains an unresolved placeholder")
        if not isinstance(caveats, list):
            fail("EV009", "runtime_instructions.caveats must be an array")
        for caveat in caveats if isinstance(caveats, list) else []:
            if not isinstance(caveat, dict) or _missing(caveat, ("statement", "evidence_ref")):
                fail("EV009", "each runtime caveat needs a statement and artifact-bound evidence_ref")
        if not isinstance(sources, list):
            fail("EV009", "runtime_instructions.data_sources must be an array")
        for source in sources if isinstance(sources, list) else []:
            if not isinstance(source, dict) or _missing(
                source, (
                    "name", "type", "uri", "version", "license", "commercial_status",
                    "commercial_evidence", "verification_ref", "notes", "included",
                )
            ):
                fail("EV009", "each runtime data source needs identity, version, license, commercial-use evidence, inclusion, and verification_ref")
                continue
            if source.get("commercial_status") not in COMMERCIAL_STATES:
                fail("EV009", f"data source {source.get('name')!r} has an invalid commercial_status")
            if not isinstance(source.get("included"), bool):
                fail("EV009", f"data source {source.get('name')!r} included must be boolean")
            elif source["included"] and source.get("commercial_status") in ("prohibited", "not_checked"):
                fail("EV009", f"data source {source.get('name')!r} cannot be included with commercial_status {source.get('commercial_status')!r}")
        if not isinstance(materials, list):
            fail("EV009", "runtime_instructions.existing_materials must be an array")

    capabilities = data.get("capabilities")
    if not isinstance(capabilities, dict):
        fail("EV009", "capabilities must be a ledger object")
    else:
        entries = capabilities.get("entries")
        if not isinstance(entries, list) or not entries:
            fail("EV009", "capability ledger has no entries")
            entries = []
        by_id: dict[str, dict] = {}
        for entry in entries:
            if not isinstance(entry, dict) or _missing(entry, ("id", "claim", "status")):
                fail("EV009", "each capability needs id, claim, and status")
                continue
            capability_id = entry.get("id")
            if not isinstance(capability_id, str) or not capability_id.strip():
                fail("EV009", "each capability id must be a non-empty string")
                continue
            if capability_id in by_id:
                fail("EV009", f"duplicate capability id {capability_id!r}")
                continue
            by_id[capability_id] = entry
            if entry["status"] not in CAPABILITY_STATES:
                fail("EV009", f"capability {entry['id']!r} has invalid status {entry['status']!r}")
            if entry["status"] == "tested":
                for field in ("implementation_refs", "eval_refs"):
                    refs = entry.get(field)
                    missing = _references_existing(pkg, refs)
                    if not isinstance(refs, list) or not refs:
                        fail("EV009", f"tested capability {entry['id']!r} has no {field}")
                    elif missing:
                        fail("EV009", f"tested capability {entry['id']!r} has missing {field}: {missing}")
        catalog_claim_ids = capabilities.get("catalog_claim_ids")
        if not isinstance(catalog_claim_ids, list):
            fail("EV010", "capabilities.catalog_claim_ids must be an array")
            catalog_claim_ids = []
        for capability_id in catalog_claim_ids:
            if not isinstance(capability_id, str) or not capability_id.strip():
                fail("EV010", "catalog capability IDs must be non-empty strings")
                continue
            if by_id.get(capability_id, {}).get("status") != "tested":
                fail("EV010", f"catalog capability {capability_id!r} is not tested")
        if frontmatter.get("description", "") != catalog_description(capabilities):
            fail("EV010", "frontmatter description was not derived from tested capability claims")

    matrix = data.get("validation_matrix")
    if not isinstance(matrix, dict):
        fail("EV011", "validation_matrix must declare separate auto and guided trials")
    else:
        for mode in ("auto", "guided"):
            trial = matrix.get(mode)
            if not isinstance(trial, dict) or trial.get("status") not in VALIDATION_STATES:
                fail("EV011", f"validation_matrix.{mode} needs a valid status")
                continue
            if trial["status"] in ("passed", "failed") and not trial.get("evidence_refs"):
                fail("EV011", f"validation_matrix.{mode} {trial['status']} without evidence_refs")
            elif trial["status"] in ("passed", "failed"):
                refs = trial["evidence_refs"]
                missing = _references_existing(pkg, refs)
                if not isinstance(refs, list):
                    fail("EV011", f"validation_matrix.{mode}.evidence_refs must be an array")
                if missing:
                    fail("EV011", f"validation_matrix.{mode} has missing evidence_refs: {missing}")
                invalid_sources = _invalid_receipt_style_sources(pkg, refs)
                if invalid_sources:
                    fail(
                        "EV011",
                        f"validation_matrix.{mode} uses non-installed style evidence: "
                        f"{invalid_sources}",
                    )
                if mode == "guided":
                    selected = trial.get("selected_branch_ids")
                    if not isinstance(selected, list) or not selected:
                        fail("EV011", "guided validation passed without selected_branch_ids")
                    elif not all(isinstance(branch_id, str) and branch_id.strip()
                                 for branch_id in selected):
                        fail("EV011", "guided validation selected_branch_ids must be strings")
                    else:
                        known = {f"{question_id}:{choice_id}" for question_id, choice_id in covered}
                        unknown = [branch_id for branch_id in selected if branch_id not in known]
                        if unknown:
                            fail("EV011", f"guided validation names unknown branch IDs: {unknown}")
                    if trial.get("selection_source") != "user_message":
                        fail(
                            "EV011",
                            "guided validation must come from a user's clarification selection",
                        )
                    external_task_id = trial.get("external_task_id")
                    if not isinstance(external_task_id, str) or not TASK_ID_RE.fullmatch(
                        external_task_id
                    ):
                        fail(
                            "EV011",
                            "guided validation must name the separate child-run task ID",
                        )
            if trial["status"] == "not_run" and not str(trial.get("reason", "")).strip():
                fail("EV011", f"validation_matrix.{mode} not_run without a reason")

    inference = data.get("inference_readiness")
    if not isinstance(inference, dict) or not isinstance(inference.get("applicable"), bool):
        fail("EV012", "inference_readiness.applicable must be explicit")
    elif inference["applicable"]:
        fields = (
            "experimental_unit", "replicate_type", "minimum_independent_units",
            "design_identifiability_check", "permutation_support_check", "runtime_preflight_ref",
        )
        if _missing(inference, fields):
            fail("EV012", "inferential workflow lacks experimental-unit, replication, design, or permutation gates")
        elif (not isinstance(inference["minimum_independent_units"], int)
              or isinstance(inference["minimum_independent_units"], bool)
              or inference["minimum_independent_units"] < 1):
            fail("EV012", "minimum_independent_units must be a positive integer")
        elif _references_existing(pkg, [inference["runtime_preflight_ref"]]):
            fail("EV012", "inference runtime preflight does not exist")
    elif not str(inference.get("not_applicable_reason", "")).strip() or inference.get("not_applicable_reason") == "unresolved":
        fail("EV012", "inference marked not applicable without a resolved reason")

    external = data.get("external_dependencies")
    if not isinstance(external, dict) or not isinstance(external.get("applicable"), bool):
        fail("EV013", "external_dependencies.applicable must be explicit")
    elif external["applicable"]:
        services = external.get("services")
        if not isinstance(services, list) or not services:
            fail("EV013", "external dependencies apply but no service policy is declared")
        for service in services if isinstance(services, list) else []:
            fields = ("name", "connect_timeout_seconds", "read_timeout_seconds", "max_retries", "wall_clock_budget_seconds")
            if not isinstance(service, dict) or _missing(service, fields):
                fail("EV013", "each external service needs explicit timeouts, retry count, and wall-clock budget")
                continue
            else:
                positive = (service["connect_timeout_seconds"], service["read_timeout_seconds"],
                            service["wall_clock_budget_seconds"])
                if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
                           for value in positive):
                    fail("EV013", f"service {service.get('name')!r} has a non-positive timeout/budget")
                retries = service["max_retries"]
                if (not isinstance(retries, int) or isinstance(retries, bool)
                        or not 0 <= retries <= MAX_EXTERNAL_RETRIES):
                    fail("EV013", f"service {service.get('name')!r} max_retries must be 0–{MAX_EXTERNAL_RETRIES}")
                wall_clock = service["wall_clock_budget_seconds"]
                if (isinstance(wall_clock, (int, float)) and not isinstance(wall_clock, bool)
                        and wall_clock > MAX_EXTERNAL_WALL_CLOCK_SECONDS):
                    fail("EV013", f"service {service.get('name')!r} wall-clock budget exceeds one hour")
                terminal_states = service.get("terminal_states")
                if not isinstance(terminal_states, list):
                    fail("EV013", f"service {service.get('name')!r} terminal_states must be an array")
                    terminal_states = []
                if not {"partial", "not_computable"}.intersection(terminal_states):
                    fail("EV013", f"service {service.get('name')!r} has no partial/not_computable finalization")
            failure_refs = service.get("failure_fixture_refs")
            if not isinstance(failure_refs, list) or not failure_refs:
                fail("EV013", f"service {service.get('name')!r} needs failure_fixture_refs")
            elif (missing := _references_existing(pkg, failure_refs)):
                fail("EV013", f"service {service.get('name')!r} has missing failure fixtures: {missing}")
    elif not str(external.get("not_applicable_reason", "")).strip() or external.get("not_applicable_reason") == "unresolved":
        fail("EV013", "external dependencies marked not applicable without a resolved reason")

    review = data.get("pdf_review")
    required_review = ("text_extraction_required", "render_all_pages_required", "visual_review_required")
    report_required = (
        isinstance(policy, dict)
        and isinstance(policy.get("report"), dict)
        and policy["report"].get("required") is True
    )
    if report_required:
        if not isinstance(review, dict) or any(review.get(field) is not True for field in required_review):
            fail("EV014", "report-producing skills must require text extraction, all-page rendering, and visual review")
    elif not isinstance(review, dict) or review.get("applicable") is not False or not str(
        review.get("not_applicable_reason", "")
    ).strip():
        fail("EV014", "skills without a report need an explicit pdf_review not-applicable reason")

    figures = data.get("figures")
    if not isinstance(figures, dict) or not isinstance(figures.get("applicable"), bool):
        fail("EV014", "figures.applicable must be explicit")
    elif not figures["applicable"] and (
        not str(figures.get("not_applicable_reason", "")).strip()
        or figures.get("not_applicable_reason") == "unresolved"
    ):
        fail("EV014", "figures marked not applicable need a resolved reason")

    execution = data.get("execution")
    if not isinstance(execution, dict) or not isinstance(execution.get("bundled_commands_applicable"), bool):
        fail("EV014", "execution.bundled_commands_applicable must be explicit")
    elif execution["bundled_commands_applicable"]:
        refs = execution.get("bundled_file_refs")
        if not isinstance(refs, list) or not refs:
            fail("EV014", "bundled command execution applies but no bundled_file_refs are declared")
        elif (missing := _references_existing(pkg, refs)):
            fail("EV014", f"execution references missing bundled files: {missing}")
        command_outputs = execution.get("command_output_paths")
        if not isinstance(command_outputs, list) or not all(
            isinstance(path, str) and path.strip() for path in command_outputs
        ):
            fail("EV014", "execution.command_output_paths must be an explicit array of paths")
        else:
            invalid = _invalid_result_paths(command_outputs)
            if invalid:
                fail("EV014", f"command-produced outputs escape the results root: {invalid}")
            branch_outputs = {
                path for branch in branches if isinstance(branch, dict)
                for path in (
                    branch.get("artifact_paths", [])
                    if isinstance(branch.get("artifact_paths", []), list)
                    else []
                )
                if isinstance(path, str)
            } if isinstance(branches, list) else set()
            undeclared = sorted(set(command_outputs).difference(branch_outputs))
            if undeclared:
                fail("EV014", f"command-produced outputs are not declared by a branch: {undeclared}")
    elif not str(execution.get("not_applicable_reason", "")).strip() or execution.get("not_applicable_reason") == "unresolved":
        fail("EV014", "bundled command execution marked not applicable needs a resolved reason")

    maturity = data.get("maturity")
    if maturity not in MATURITY_STATES:
        fail("EV015", f"maturity must be one of {', '.join(MATURITY_STATES)}")
    elif maturity in ("evidence_validated", "user_validated", "installable"):
        auto = matrix.get("auto", {}) if isinstance(matrix, dict) else {}
        if auto.get("status") != "passed":
            fail("EV015", f"maturity {maturity!r} requires a passed auto validation")
    if maturity in ("user_validated", "installable"):
        guided = matrix.get("guided", {}) if isinstance(matrix, dict) else {}
        if guided.get("status") != "passed":
            fail("EV015", f"maturity {maturity!r} requires a passed guided validation")
    skill_text = (pkg / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    stated_maturity = re.findall(r"Contract maturity:\s*`([^`]+)`", skill_text)
    if stated_maturity != [maturity]:
        fail("EV015", "SKILL.md Evidence Tier must state the same single maturity as skill_contract.json")
    installation = data.get("installation")
    required_installation = (
        "offer_private_preview_after_generation",
        "registration_requires_explicit_user_confirmation",
        "registration_managed_outside_package",
    )
    if not isinstance(installation, dict) or any(
        installation.get(field) is not True for field in required_installation
    ):
        fail("EV016", "generation must offer a private preview, while registration remains an external explicit-confirmation gate")
    elif "approved" in installation:
        fail("EV016", "mutable user approval must not be stored inside the immutable reviewed package")

    authoring = data.get("authoring")
    if not isinstance(authoring, dict) or authoring.get("generator") != "phylo-create-skill":
        fail("EV017", "authoring metadata must identify phylo-create-skill")
    elif authoring.get("derived_files") != ["SKILL.md", "eval.yaml", "DATA_SOURCES.md"]:
        fail("EV017", "authoring.derived_files must name the three contract projections")
    elif "regenerate" not in str(authoring.get("repair_policy", "")) or "full gate" not in str(
        authoring.get("repair_policy", "")
    ):
        fail("EV017", "derived-file repairs must regenerate from the contract and rerun the full gate")

    eval_path = pkg / "eval.yaml"
    sources_path = pkg / "DATA_SOURCES.md"
    if not eval_path.is_file() or not sources_path.is_file():
        fail("EV017", "generated packages need root eval.yaml and DATA_SOURCES.md projections")
    else:
        eval_text = eval_path.read_text(encoding="utf-8", errors="replace")
        prompt = starting_prompt(task) if isinstance(task, dict) and task.get("user_prompt") else ""
        prompt_lines = [line.strip() for line in eval_text.splitlines()
                        if line.strip().startswith("prompt:")]
        if len(prompt_lines) != 1:
            fail("EV017", "eval.yaml must contain exactly one sample prompt")
        elif prompt:
            match = re.fullmatch(r'prompt:\s*("(?:[^"\\]|\\.)*")', prompt_lines[0])
            try:
                eval_prompt = json.loads(match.group(1)) if match else None
            except ValueError:
                eval_prompt = None
            if eval_prompt != prompt:
                fail("EV017", "eval.yaml sample prompt drifted from starting_task.user_prompt")
        for required_line in (
            "version: 1", "eval_source: skill_author", "verification_status: unreviewed",
            "expected_outputs:", "invariants:", "judge_criteria:",
        ):
            if required_line not in eval_text:
                fail("EV017", f"eval.yaml is missing {required_line!r}")
        source_text = sources_path.read_text(encoding="utf-8", errors="replace")
        for source in runtime.get("data_sources", []) if isinstance(runtime, dict) else []:
            if isinstance(source, dict) and any(
                str(source.get(field, "")) not in source_text
                for field in ("name", "uri", "commercial_status", "commercial_evidence")
            ):
                fail("EV017", f"DATA_SOURCES.md drifted from source {source.get('name')!r}")

    return out
