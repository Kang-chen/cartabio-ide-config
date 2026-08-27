#!/usr/bin/env python3
"""Regression tests for transcript-bound report-style selection and receipt compatibility."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))
import check_skill  # type: ignore  # noqa: E402
from test_checks_support import complete, rg, run_check, write_pdf  # type: ignore  # noqa: E402


def load_report_qc():
    spec = importlib.util.spec_from_file_location("style_selection_report_qc", ROOT / "templates" / "report_qc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_profile(
    root: pathlib.Path, provider: str, activation: str, colors: tuple[str, str], aliases: list[str] | None = None
) -> None:
    path = root / provider / "assets" / "report_style.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "schema": "biomni-report-style/1",
        "provider": provider,
        "activation": activation,
        "pdf_markers": {"required_any": [colors[0]], "supporting_any": [colors[1]], "minimum_distinct_markers": 2},
    }
    if aliases is not None:
        profile["user_selection_aliases"] = aliases
    path.write_text(json.dumps(profile), encoding="utf-8")
    skill_path = root / provider / "SKILL.md"
    if not skill_path.exists():
        write_skill(root, provider, colors)


def write_skill(
    root: pathlib.Path,
    provider: str,
    colors: tuple[str, str],
    *,
    primary_label: str = "primary accent / logo",
) -> None:
    path = root / provider / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f'name: "{provider}"\n'
        'description: "Fixture installed report styling skill."\n'
        "---\n\n"
        "# Fixture report styling\n\n"
        "## Brand at a glance\n\n"
        f'- Primary `{colors[0]}` ({primary_label}).\n'
        f'- Supporting `{colors[1]}` (headings and rules).\n\n'
        "## Workflow\n\nUse the installed assets.\n",
        encoding="utf-8",
    )


def write_transcript(results: pathlib.Path, records: list[dict] | dict, *, pretty: bool = False) -> None:
    path = results / "execution_trace" / "transcript.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        path.write_text(json.dumps(records), encoding="utf-8")
    else:
        rows = records if isinstance(records, list) else [records]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def style_contract() -> dict:
    return {
        "deliverable_policy": {
            "report": {
                "required": True,
                "not_applicable_reason": "",
                "default_style_provider": "pdf-report-generation",
                "explicit_style_override_allowed": True,
            },
            "infographic": {"required": False, "not_applicable_reason": "fixture omits it"},
        },
        "facts": {"requirement": "not_applicable", "not_applicable_reason": "fixture has no facts"},
        "execution": {"bundled_commands_applicable": False, "not_applicable_reason": "fixture has no command"},
        "source_assertions": [],
        "source_assertions_not_applicable_reason": "fixture has no source assertions",
    }


def style_receipt(module, results: pathlib.Path, report: str, *, asserted: str | None = None) -> dict:
    module.write_receipt(
        report_name=report,
        figures=[],
        figure_not_applicable_reason="fixture has no figures",
        outputs=["result.csv"],
        infographics=[],
        style_provider=asserted,
        qc_run_log="missing-qc-log.json",
        contract=style_contract(),
        strict=False,
        path="style_receipt.json",
    )
    return json.loads((results / "style_receipt.json").read_text(encoding="utf-8"))


def write_v3_receipt(package: pathlib.Path, style: dict) -> None:
    evidence = {key: {"path": f"/mnt/results/{key}", "bytes": 31_337} for key in check_skill.EVIDENCE_RECEIPT_KEYS}
    evidence["report_style_verified"] = style
    receipt = dict.fromkeys(check_skill.EVIDENCE_RECEIPT_KEYS, True)
    receipt.update(
        {
            "schema": check_skill.RECEIPT_SCHEMA_V3,
            "generated_by": "report_qc.write_receipt",
            "figures_embedded": "pass",
            "evidence": evidence,
        }
    )
    (package / "run_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")


def main() -> int:
    rows: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        results = base / "results"
        results.mkdir()
        system = base / "system"
        user = base / "user"
        personal = base / "personal"
        write_skill(system, "pdf-report-generation", ("#D4A04A", "#111111"))
        write_skill(user, "fixture-enterprise-styling", ("#123456", "#654321"))
        write_skill(user, "second-enterprise-styling", ("#246810", "#135791"))
        write_profile(
            user,
            "broken-enterprise-styling",
            "explicit_only",
            ("#369ABC", "#CBA963"),
            ["Broken enterprise styling"],
        )
        broken_profile = user / "broken-enterprise-styling" / "assets" / "report_style.json"
        broken_payload = json.loads(broken_profile.read_text(encoding="utf-8"))
        broken_payload["pdf_markers"]["minimum_distinct_markers"] = 1
        broken_profile.write_text(json.dumps(broken_payload), encoding="utf-8")
        write_profile(
            personal,
            "future-style-provider",
            "explicit_only",
            ("#147ABC", "#765432"),
            ["Future provider house style"],
        )
        write_skill(personal, "future-style-provider", ("#AAAAAA", "#BBBBBB"))
        validator = ROOT / "scripts" / "validate_style_provider.py"
        validated = []
        for provider_dir, activation in (
            (system / "pdf-report-generation", "default"),
            (user / "fixture-enterprise-styling", "explicit_only"),
            (personal / "future-style-provider", "explicit_only"),
        ):
            run = subprocess.run(
                [sys.executable, str(validator), str(provider_dir), "--activation", activation],
                capture_output=True,
                text=True,
            )
            validated.append(run.returncode == 0)
        rows.append((
            "the provider authoring validator accepts default, legacy, and future contracts",
            all(validated),
            f"{sum(validated)}/{len(validated)} accepted",
        ))
        mismatch = subprocess.run(
            [
                sys.executable,
                str(validator),
                str(personal / "future-style-provider"),
                "--activation",
                "default",
            ],
            capture_output=True,
            text=True,
        )
        rows.append((
            "the provider authoring validator rejects an activation mismatch",
            mismatch.returncode == 1 and "expected 'default'" in mismatch.stderr,
            f"exit {mismatch.returncode}",
        ))
        module = load_report_qc()
        module.RESULTS = results
        module._SYSTEM_STYLE_ROOT = system
        module._USER_STYLE_ROOT = user
        module._PERSONAL_STYLE_ROOT = personal
        (results / "result.csv").write_text("value\n1\n", encoding="utf-8")
        padding = b"\n% " + b"padding " * 3_000
        write_pdf(results / "default.pdf", rg("#D4A04A", "#111111") + padding)
        write_pdf(results / "explicit.pdf", rg("#123456", "#654321") + padding)
        write_pdf(results / "future.pdf", rg("#147ABC", "#765432") + padding)

        def selection_case(name: str, records, expected: str | None, *, pretty: bool = False) -> None:
            write_transcript(results, records, pretty=pretty)
            try:
                provider, evidence = module._selected_style_from_transcript()
                ok = provider == expected and ((evidence is None) == (expected is None))
                detail = provider or "contract default"
                if evidence:
                    ok = (
                        ok
                        and evidence["source"] == "user_message"
                        and not any(key in evidence for key in ("content", "message", "text"))
                    )
            except module.GateFailure as exc:
                ok, detail = expected == "raises", str(exc)
            rows.append((name, ok, detail))

        selection_case(
            "customer context alone keeps the default",
            [{"type": "user", "i": 1, "content": "Prepare this for the Fixture enterprise audience."}],
            None,
        )
        selection_case(
            "assistant text or tool activity cannot authorize enterprise styling",
            [
                {"type": "user", "i": 1, "content": "Prepare the report."},
                {"type": "assistant", "i": 2, "content": "Use Fixture enterprise styling."},
                {"type": "tool_result", "content": "Use Fixture enterprise styling."},
            ],
            None,
        )
        selection_case(
            "an affirmative user message selects the enterprise provider",
            [{"type": "user", "i": 3, "content": "Please use Fixture enterprise styling."}],
            "fixture-enterprise-styling",
        )
        selection_case(
            "a future profile provider is discovered without a creator registry",
            [{"type": "user", "i": 3, "content": "Please apply the Future provider house style."}],
            "future-style-provider",
        )
        installed_skills_root = ROOT.parent
        installed_legacy = sorted(installed_skills_root.glob("*-styling/SKILL.md"))
        derived_legacy = []
        for skill_path in installed_legacy:
            try:
                profile, source_path, source = module._report_style.resolve_provider(
                    skill_path.parent.name,
                    (installed_skills_root,),
                    activation_hint="explicit_only",
                )
            except module._report_style.StyleProviderError:
                derived_legacy.append(False)
            else:
                derived_legacy.append(
                    profile.get("activation") == "explicit_only"
                    and source_path == skill_path
                    and source.get("kind") == "installed_skill_markdown"
                )
        rows.append((
            "all unchanged installed legacy styling skills derive a usable provider contract",
            bool(installed_legacy) and all(derived_legacy),
            f"{sum(derived_legacy)}/{len(derived_legacy)} derived",
        ))
        try:
            installed_default, _, installed_default_source = module._report_style.resolve_provider(
                "pdf-report-generation",
                (installed_skills_root,),
                activation_hint="default",
            )
        except module._report_style.StyleProviderError as exc:
            default_derived, default_detail = False, str(exc)
        else:
            default_derived = (
                installed_default.get("activation") == "default"
                and installed_default_source.get("kind") == "installed_skill_markdown"
            )
            default_detail = str(installed_default.get("pdf_markers"))
        rows.append((
            "the unchanged installed default report skill derives a usable provider contract",
            default_derived,
            default_detail,
        ))
        selection_case(
            "chunked user messages are reassembled",
            [
                {"type": "user", "i": 4, "content": "Please use Fixture "},
                {"type": "user", "i": 4, "content": "enterprise styling."},
            ],
            "fixture-enterprise-styling",
        )
        selection_case(
            "API envelopes retain immutable user message ids",
            {
                "data": [
                    {
                        "role": "user",
                        "id": "msg-4",
                        "content": [{"type": "text", "text": "Apply the Fixture enterprise house style."}],
                    }
                ]
            },
            "fixture-enterprise-styling",
            pretty=True,
        )
        selection_case(
            "API envelopes can use their immutable record index",
            {
                "data": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Use Fixture enterprise styling."}],
                    }
                ]
            },
            "fixture-enterprise-styling",
            pretty=True,
        )
        selection_case(
            "a later revocation clears an earlier selection",
            [
                {"type": "user", "i": 5, "content": "Use Fixture enterprise styling."},
                {"type": "user", "i": 6, "content": "Do not use Fixture enterprise styling."},
            ],
            None,
        )
        selection_case(
            "revoking language after the alias cannot authorize it",
            [{"type": "user", "i": 6, "content": "Fixture enterprise styling should not be used."}],
            None,
        )
        selection_case(
            "an unambiguous re-selection after revocation is accepted",
            [
                {"type": "user", "i": 5, "content": "Use Fixture enterprise styling."},
                {"type": "user", "i": 6, "content": "Do not use Fixture enterprise styling."},
                {"type": "user", "i": 7, "content": "Apply the Fixture enterprise house style."},
            ],
            "fixture-enterprise-styling",
        )
        selection_case(
            "conflicting provider selections fail closed",
            [{"type": "user", "i": 8, "content": "Use Fixture enterprise styling and use Second enterprise styling."}],
            "raises",
        )
        selection_case(
            "an instead-of directive selects only its affirmative provider",
            [
                {
                    "type": "user",
                    "i": 8,
                    "content": "Use Fixture enterprise styling instead of Second enterprise styling.",
                }
            ],
            "fixture-enterprise-styling",
        )
        selection_case(
            "a later competing selection without revocation fails closed",
            [
                {"type": "user", "i": 8, "content": "Use Fixture enterprise styling."},
                {"type": "user", "i": 9, "content": "Use Second enterprise styling."},
            ],
            "raises",
        )
        selection_case(
            "a user directive without an immutable locator fails closed",
            [{"type": "user", "content": "Use Fixture enterprise styling."}],
            "raises",
        )
        write_transcript(
            results,
            [
                {"type": "user", "i": 10, "content": "Use Fixture "},
                {"type": "user", "i": 10, "content": "enterprise styling."},
            ],
        )
        _, chunked_evidence = module._selected_style_from_transcript()
        write_transcript(
            results,
            {
                "data": [
                    {
                        "role": "user",
                        "id": "message-10",
                        "content": "Use Fixture enterprise styling.",
                    }
                ]
            },
            pretty=True,
        )
        _, envelope_evidence = module._selected_style_from_transcript()
        rows.append(
            (
                "chunked and API messages produce stable content hashes",
                chunked_evidence["message_sha256"] == envelope_evidence["message_sha256"],
                chunked_evidence["message_sha256"][:12],
            )
        )
        transcript_path = results / "execution_trace" / "transcript.jsonl"
        transcript_path.write_text('{"type":"user","i":10,"content":\n', encoding="utf-8")
        try:
            module._selected_style_from_transcript()
            malformed_failed = False
        except module.GateFailure:
            malformed_failed = True
        rows.append(
            (
                "a malformed transcript fails closed",
                malformed_failed,
                "raised" if malformed_failed else "did not raise",
            )
        )

        write_transcript(results, [{"type": "user", "i": 8, "content": "Prepare the report."}])
        receipt = style_receipt(module, results, "explicit.pdf", asserted="fixture-enterprise-styling")
        rows.append(
            (
                "a caller-only enterprise override is rejected",
                receipt.get("report_style_verified") is False
                and "immutable user selection" in receipt.get("report_style_verified_reason", ""),
                receipt.get("report_style_verified_reason", "missing reason"),
            )
        )
        try:
            module.write_receipt(
                report_name="explicit.pdf",
                figures=[],
                figure_not_applicable_reason="fixture has no figures",
                outputs=["result.csv"],
                infographics=[],
                style_provider="fixture-enterprise-styling",
                qc_run_log="missing-qc-log.json",
                contract=style_contract(),
                strict=True,
                path="unsafe_style_receipt.json",
            )
            raised = False
        except module.GateFailure:
            raised = True
        unsafe_receipt = json.loads((results / "unsafe_style_receipt.json").read_text(encoding="utf-8"))
        rows.append(
            (
                "strict mode raises after writing caller-only diagnostics",
                raised and unsafe_receipt.get("report_style_verified") is False,
                "raised with diagnostic" if raised else "did not raise",
            )
        )
        write_transcript(results, [{"type": "user", "i": 8, "content": "Please use Fixture enterprise styling."}])
        receipt = style_receipt(module, results, "explicit.pdf", asserted="second-enterprise-styling")
        rows.append(
            (
                "a caller assertion cannot replace the user-selected provider",
                receipt.get("report_style_verified") is False
                and "immutable user selection" in receipt.get("report_style_verified_reason", ""),
                receipt.get("report_style_verified_reason", "missing reason"),
            )
        )
        write_transcript(
            results,
            [{"type": "user", "i": 8, "content": "Please use Broken enterprise styling."}],
        )
        receipt = style_receipt(module, results, "default.pdf")
        rows.append(
            (
                "a selected malformed provider blocks instead of falling back",
                receipt.get("report_style_verified") is False
                and "minimum_distinct_markers" in receipt.get("report_style_verified_reason", ""),
                receipt.get("report_style_verified_reason", "missing reason"),
            )
        )
        write_transcript(results, [{"type": "user", "i": 8, "content": "Prepare the report."}])
        receipt = style_receipt(module, results, "default.pdf", asserted="pdf-report-generation")
        style = receipt.get("evidence", {}).get("report_style_verified", {})
        rows.append(
            (
                "an asserted default remains the contract default",
                receipt.get("report_style_verified") is True
                and style.get("selection") == "contract_default"
                and style.get("style_source", {}).get("kind") == "installed_skill_markdown"
                and "selection_evidence" not in style,
                str(style.get("selection")),
            )
        )
        write_transcript(results, [{"type": "user", "i": 9, "content": "Please use Fixture enterprise styling."}])
        receipt = style_receipt(module, results, "explicit.pdf")
        style = receipt.get("evidence", {}).get("report_style_verified", {})
        selection = style.get("selection_evidence", {})
        rows.append(
            (
                "an authorized override records immutable selection evidence",
                receipt.get("report_style_verified") is True
                and style.get("selection") == "explicit_override"
                and style.get("style_source", {}).get("kind") == "installed_skill_markdown"
                and selection.get("message_locator") == {"kind": "index", "value": "9"}
                and len(selection.get("message_sha256", "")) == 64
                and len(selection.get("transcript_sha256", "")) == 64,
                str(style.get("selection")),
            )
        )
        write_transcript(results, [{"type": "user", "i": 10, "content": "Apply the Future provider house style."}])
        receipt = style_receipt(module, results, "future.pdf")
        future_style = receipt.get("evidence", {}).get("report_style_verified", {})
        rows.append((
            "a future structured provider records profile-first provenance",
            receipt.get("report_style_verified") is True
            and future_style.get("provider") == "future-style-provider"
            and future_style.get("style_source", {}).get("kind") == "provider_profile",
            str(future_style.get("style_source", {}).get("kind")),
        ))

        checked = complete(base / "style-receipts", "style-receipts")
        default_style = {
            "provider": "pdf-report-generation",
            "activation": "default",
            "style_source": {
                "kind": "installed_skill_markdown",
                "path": "/mnt/skills/system/pdf-report-generation/SKILL.md",
                "bytes": 512,
                "sha256": "a" * 64,
                "derivation_schema": "biomni-report-style-derivation/1",
                "marker_set_sha256": "c" * 64,
            },
            "selection": "contract_default",
            "contract_default_provider": "pdf-report-generation",
        }
        write_v3_receipt(checked, default_style)
        code, output = run_check(checked, ["--require-run-receipt"])
        rows.append((
            "the receipt checker accepts the exact contract default",
            code == 0,
            f"exit {code}: {output[:600]}",
        ))
        default_without_source_hash = {
            **default_style,
            "style_source": {
                **default_style["style_source"],
                "sha256": None,
            },
        }
        write_v3_receipt(checked, default_without_source_hash)
        code, output = run_check(checked, ["--require-run-receipt"])
        rows.append(
            (
                "the receipt checker requires the provider source hash",
                code == 1 and "source hash" in output,
                f"exit {code}",
            )
        )
        explicit_without_user_evidence = {
            **default_style,
            "provider": "fixture-enterprise-styling",
            "activation": "explicit_only",
            "style_source": {
                "kind": "installed_skill_markdown",
                "path": "/mnt/skills/user/fixture-enterprise-styling/SKILL.md",
                "bytes": 512,
                "sha256": "b" * 64,
                "derivation_schema": "biomni-report-style-derivation/1",
                "marker_set_sha256": "d" * 64,
            },
            "selection": "explicit_override",
        }
        write_v3_receipt(checked, explicit_without_user_evidence)
        code, output = run_check(checked, ["--require-run-receipt"])
        rows.append(
            (
                "the receipt checker rejects an override without user evidence",
                code == 1 and "immutable user-selection" in output,
                f"exit {code}",
            )
        )
        mismatched_default = {
            **default_style,
            "provider": "fixture-enterprise-styling",
            "style_source": {
                "kind": "installed_skill_markdown",
                "path": "/mnt/skills/system/fixture-enterprise-styling/SKILL.md",
                "bytes": 512,
                "sha256": "b" * 64,
                "derivation_schema": "biomni-report-style-derivation/1",
                "marker_set_sha256": "d" * 64,
            },
        }
        write_v3_receipt(checked, mismatched_default)
        code, output = run_check(checked, ["--require-run-receipt"])
        rows.append(
            (
                "the receipt checker rejects a false contract-default provider",
                code == 1 and "does not use the contract default" in output,
                f"exit {code}",
            )
        )

        package = complete(base / "legacy", "legacy-style-contract")
        contract_path = package / "skill_contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        report_policy = contract["deliverable_policy"]["report"]
        report_policy.pop("default_style_provider")
        report_policy.pop("explicit_style_override_allowed")
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        evidence = {key: {"path": f"/mnt/results/{key}"} for key in check_skill.EVIDENCE_RECEIPT_KEYS_V2}
        evidence["report_branded"] = {
            "provider": "pdf-report-generation",
            "profile": {"path": "/mnt/skills/system/pdf-report-generation/assets/report_style.json"},
        }
        legacy_receipt = dict.fromkeys(check_skill.EVIDENCE_RECEIPT_KEYS_V2, True)
        legacy_receipt.update(
            {
                "schema": check_skill.RECEIPT_SCHEMA_V2,
                "generated_by": "report_qc.write_receipt",
                "figures_embedded": "pass",
                "evidence": evidence,
            }
        )
        (package / "run_receipt.json").write_text(json.dumps(legacy_receipt), encoding="utf-8")
        code, output = run_check(package, ["--require-run-receipt"])
        rows.append((
            "a complete pre-style evidence-v1 package accepts receipt v2",
            code == 0,
            f"exit {code}: {output[:600]}",
        ))

    width = max(len(name) for name, _, _ in rows)
    for name, passed, detail in rows:
        print(f"{name:<{width}}  {'PASS' if passed else 'FAIL'}  {detail[:100]}")
    failed = [name for name, passed, _ in rows if not passed]
    print(f"\nRESULT: {len(rows) - len(failed)}/{len(rows)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
