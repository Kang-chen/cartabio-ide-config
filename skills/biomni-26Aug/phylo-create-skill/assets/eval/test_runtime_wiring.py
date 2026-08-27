#!/usr/bin/env python3
"""Behavioral regressions for generated facts/figure wiring and artifact containment."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

from test_checks_support import CHECK, PKG_ROOT, SCAFFOLD, png_bytes  # noqa: E402
from test_evidence_contract import record  # noqa: E402


ARCHETYPES = (
    "analysis-workflow",
    "evidence-synthesis",
    "protocol-workflow",
    "correctness-guidance",
    "format-utility",
    "meta-tooling",
)


def load_qc(results_root: pathlib.Path):
    os.environ["BIOMNI_RESULTS"] = str(results_root)
    source = PKG_ROOT / "templates" / "report_qc.py"
    spec = importlib.util.spec_from_file_location("runtime_wiring_qc", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RESULTS = results_root
    return module


def scaffold(root: pathlib.Path, archetype: str, *, figures_applicable: bool = True) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    _, facts_requirement, payload = record("literature")
    payload["facts"]["runtime_payload_artifact"] = "facts_payload.json"
    payload["figures"] = {
        "applicable": figures_applicable,
        "not_applicable_reason": "" if figures_applicable else "this result has no useful figure",
    }
    slug = f"runtime-{archetype}"
    record_path = root / f"{slug}.json"
    record_path.write_text(json.dumps(payload), encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--slug",
            slug,
            "--archetype",
            archetype,
            "--category",
            "general",
            "--record",
            str(record_path),
            "--dest",
            str(root / "skills"),
            "--facts-requirement",
            facts_requirement,
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode:
        raise RuntimeError(run.stdout + run.stderr)
    return root / "skills" / slug


def execute_generated_facts_phases(package: pathlib.Path, results_root: pathlib.Path) -> dict:
    """Execute the generated figure/facts fences, not a test-side reconstruction of them."""
    text = (package / "SKILL.md").read_text(encoding="utf-8")
    fences = re.findall(r"```python\n(.*?)```", text, re.S)
    phases = [fence for fence in fences if (
        "figures = assert_figures" in fence or "write_facts_from_artifact" in fence
    )]
    results_root.mkdir(parents=True, exist_ok=True)
    figures_dir = results_root / "figures"
    figures_dir.mkdir(exist_ok=True)
    (figures_dir / "result.png").write_bytes(png_bytes())
    (figures_dir / "manifest.json").write_text(json.dumps([{
        "step": 1,
        "file": "figures/result.png",
        "caption": "One of two eligible records completed and one was not computable.",
    }]), encoding="utf-8")
    (results_root / "facts_payload.json").write_text(json.dumps({
        "completion": {"eligible": 2, "completed": 1, "not_computable": 1},
    }), encoding="utf-8")
    env = {
        **os.environ,
        "BIOMNI_RESULTS": str(results_root),
        "PYTHONPATH": str(package / "scripts"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    run = subprocess.run(
        [sys.executable, "-c", "\n".join(phases)],
        cwd=package,
        env=env,
        capture_output=True,
        text=True,
    )
    if run.returncode:
        raise RuntimeError(run.stdout + run.stderr)
    return json.loads((results_root / "report_facts.json").read_text(encoding="utf-8"))


def check_package(package: pathlib.Path) -> str:
    run = subprocess.run(
        [sys.executable, str(CHECK), str(package), "--contract", "A"],
        capture_output=True,
        text=True,
    )
    return run.stdout + run.stderr


def receipt_contract(profile: str, *, figures_applicable: bool) -> dict:
    _, facts_requirement, payload = record(profile)
    payload["schema"] = "phylo-skill-evidence/1"
    payload["facts"]["requirement"] = facts_requirement
    if facts_requirement == "required":
        payload["facts"]["schema"] = "report_facts.json"
        payload["facts"]["not_applicable_reason"] = ""
    else:
        payload["facts"]["schema"] = None
        payload["facts"]["not_applicable_reason"] = (
            "formatter makes no evidence-bearing claims"
        )
    payload["deliverable_policy"] = {
        "audience": "composable_helper",
        "report": {
            "required": False,
            "not_applicable_reason": "receipt fixture has no report",
            "default_style_provider": "pdf-report-generation",
            "explicit_style_override_allowed": True,
        },
        "infographic": {"required": False, "not_applicable_reason": "receipt fixture has no infographic"},
    }
    payload["figures"] = {
        "applicable": figures_applicable,
        "not_applicable_reason": "receipt fixture has no figure" if not figures_applicable else "",
    }
    payload["execution"] = {
        "bundled_commands_applicable": False,
        "bundled_file_refs": [],
        "command_output_paths": [],
        "not_applicable_reason": "receipt fixture uses filesystem output",
    }
    return payload


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(label: str, condition: bool, detail: str) -> None:
        results.append((label, condition, detail))

    def rejects(label: str, fn, needle: str) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - public failure behavior is under test
            check(label, needle in str(exc), f"{type(exc).__name__}: {exc}"[:140])
        else:
            check(label, False, "did not raise")

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        for archetype in ARCHETYPES:
            package = scaffold(root / archetype, archetype)
            text = (package / "SKILL.md").read_text(encoding="utf-8")
            check(
                f"{archetype} ships both report QC runtime modules",
                (package / "scripts" / "report_qc.py").is_file()
                and (package / "scripts" / "report_style.py").is_file(),
                "both copied",
            )
            check(
                f"{archetype} treats no styling request as the unambiguous default",
                "The absence of an affirmative styling directive is not ambiguity" in text
                and "without asking a styling clarification" in text,
                "default needs no clarification",
            )
            facts_call = "write_facts_from_artifact(" in text
            figure_assignment = 'figures = assert_figures("figures/manifest.json")' in text
            order_ok = (
                facts_call
                and figure_assignment
                and text.index("figures = assert_figures") < text.index("write_facts_from_artifact")
                < text.index("write_receipt(")
            )
            check(
                f"{archetype} initializes figures and writes complete required facts before receipt",
                order_ok,
                "ordered runtime phases" if order_ok else "missing or misordered runtime phase",
            )
            facts = execute_generated_facts_phases(package, root / archetype / "results")
            check(
                f"{archetype} executes its generated figure/facts phases",
                facts.get("completion") == {"eligible": 2, "completed": 1, "not_computable": 1}
                and len(facts.get("figures", [])) == 1,
                "facts and validated figure attached",
            )

        package = scaffold(root / "no-figures", "evidence-synthesis", figures_applicable=False)
        text = (package / "SKILL.md").read_text(encoding="utf-8")
        no_figures_ok = (
            "figures = []" in text
            and "write_facts_from_artifact(" in text
            and 'assert_figures("figures/manifest.json")' not in text
        )
        check(
            "facts-required non-analysis path binds an empty figure list when figures do not apply",
            no_figures_ok,
            "bound" if no_figures_ok else "facts or figure binding missing",
        )
        no_figure_facts = execute_generated_facts_phases(package, root / "no-figures" / "results")
        check(
            "facts-required non-analysis path executes with an empty figure inventory",
            no_figure_facts.get("figures") == [],
            "empty inventory written",
        )

        missing_facts_call = scaffold(root / "missing-facts-call", "protocol-workflow")
        skill_md = missing_facts_call / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8").replace(
                "write_facts_from_artifact(", "removed_facts_writer(", 1
            ),
            encoding="utf-8",
        )
        check(
            "RC011 rejects a required-facts runtime whose writer was removed",
            "RC011" in check_package(missing_facts_call),
            "RC011 fired",
        )

        wrong_facts_source = scaffold(root / "wrong-facts-source", "protocol-workflow")
        skill_md = wrong_facts_source / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8").replace(
                "source='facts_payload.json'", "source='invented_payload.json'", 1
            ),
            encoding="utf-8",
        )
        check(
            "RC011 rejects a facts writer bound to a source other than the contract's",
            "RC011" in check_package(wrong_facts_source),
            "RC011 fired",
        )

        missing_empty_binding = scaffold(
            root / "missing-empty-binding", "evidence-synthesis", figures_applicable=False
        )
        skill_md = missing_empty_binding / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8").replace("figures = []\n", "", 1),
            encoding="utf-8",
        )
        check(
            "RC011 rejects an N/A-figures facts writer with an undefined inventory",
            "RC011" in check_package(missing_empty_binding),
            "RC011 fired",
        )

        missing_figure_init = scaffold(root / "missing-figure-init", "evidence-synthesis")
        skill_md = missing_figure_init / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8").replace(
                "figures = assert_figures(", "ignored_figures = assert_figures(", 1
            ),
            encoding="utf-8",
        )
        check(
            "RC012 rejects an applicable-figures runtime whose assignment was removed",
            "RC012" in check_package(missing_figure_init),
            "RC012 fired",
        )

        invalid_contract = scaffold(root / "invalid-payload", "protocol-workflow")
        contract_path = invalid_contract / "skill_contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["facts"]["runtime_payload_artifact"] = "../outside.json"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        check(
            "EV005 rejects a facts runtime payload outside the results root",
            "EV005" in check_package(invalid_contract),
            "EV005 fired",
        )

        _, alias_requirement, alias_record = record("literature")
        alias_record["facts"]["runtime_payload_artifact"] = "./report_facts.json"
        alias_record_path = root / "aliased-facts-record.json"
        alias_record_path.write_text(json.dumps(alias_record), encoding="utf-8")
        alias_scaffold = subprocess.run([
            sys.executable, str(SCAFFOLD), "--slug", "aliased-facts",
            "--archetype", "evidence-synthesis", "--category", "general",
            "--record", str(alias_record_path), "--dest", str(root / "aliased-facts"),
            "--facts-requirement", alias_requirement,
        ], capture_output=True, text=True)
        alias_scaffold_output = alias_scaffold.stdout + alias_scaffold.stderr
        check(
            "scaffolder rejects a runtime facts payload aliased to report_facts.json",
            alias_scaffold.returncode != 0 and "must be distinct" in alias_scaffold_output,
            f"exit {alias_scaffold.returncode}",
        )

        results_root = root / "results"
        results_root.mkdir()
        qc = load_qc(results_root)
        outside = root / "outside.png"
        outside.write_bytes(png_bytes())
        rejects(
            "assert_figures rejects an absolute figure outside the results root",
            lambda: qc.assert_figures([{"step": 1, "file": str(outside), "caption": "outside"}]),
            "results root",
        )

        figures = results_root / "figures"
        figures.mkdir()
        escaping = figures / "escaping.png"
        escaping.symlink_to(outside)
        rejects(
            "assert_figures rejects a results-root symlink targeting an external figure",
            lambda: qc.assert_figures(
                [{"step": 1, "file": "figures/escaping.png", "caption": "escape"}]
            ),
            "results root",
        )

        receipt_results = root / "receipt-results"
        receipt_results.mkdir()
        (receipt_results / "result.txt").write_text("result", encoding="utf-8")
        receipt_figures = receipt_results / "figures"
        receipt_figures.mkdir()
        (receipt_figures / "result.png").write_bytes(png_bytes())
        valid_figures = [{
            "step": 1,
            "file": "figures/result.png",
            "caption": "One of two eligible records completed.",
        }]
        facts_payload = {
            "completion": {"eligible": 2, "completed": 1, "not_computable": 1},
        }
        (receipt_results / "facts_payload.json").write_text(
            json.dumps(facts_payload), encoding="utf-8"
        )
        good_facts = {
            **facts_payload,
            "figures": valid_figures,
        }
        (receipt_results / "report_facts.json").write_text(
            json.dumps(good_facts), encoding="utf-8"
        )
        qc = load_qc(receipt_results)
        facts_contract = receipt_contract("literature", figures_applicable=True)
        rejects(
            "facts writer rejects report_facts.json as its own runtime payload",
            lambda: qc.write_facts_from_artifact(
                "report_facts.json",
                source="report_facts.json",
                figures=valid_figures,
                contract=facts_contract,
            ),
            "distinct files",
        )
        aliased_facts = receipt_results / "aliased-report-facts.json"
        aliased_facts.symlink_to(receipt_results / "facts_payload.json")
        rejects(
            "facts writer rejects a symlink to its runtime payload",
            lambda: qc.write_facts_from_artifact(
                "aliased-report-facts.json",
                source="facts_payload.json",
                figures=valid_figures,
                contract=facts_contract,
            ),
            "distinct files",
        )
        receipt_path = qc.write_receipt(
            report_name=None,
            figures=valid_figures,
            outputs=["result.txt"],
            qc_run_log="absent-qc-log.json",
            contract=facts_contract,
            strict=False,
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        check(
            "receipt verifies the canonical facts artifact against its semantic contract",
            receipt.get("facts_artifact_verified") is True
            and receipt.get("evidence", {}).get("facts_artifact_verified", {}).get("sha256"),
            "semantic fact evidence recorded",
        )

        aliased_contract = json.loads(json.dumps(facts_contract))
        aliased_contract["facts"]["runtime_payload_artifact"] = "report_facts.json"
        aliased_receipt_path = qc.write_receipt(
            report_name=None,
            figures=valid_figures,
            outputs=["result.txt"],
            qc_run_log="absent-qc-log.json",
            contract=aliased_contract,
            path="aliased-facts-receipt.json",
            strict=False,
        )
        aliased_receipt = json.loads(aliased_receipt_path.read_text(encoding="utf-8"))
        check(
            "receipt rejects report_facts.json as its own runtime evidence",
            aliased_receipt.get("facts_artifact_verified") is False
            and "distinct files" in aliased_receipt.get("facts_artifact_verified_reason", ""),
            aliased_receipt.get("facts_artifact_verified_reason", "missing reason")[:140],
        )

        symlink_source = receipt_results / "facts-source-alias.json"
        symlink_source.symlink_to(receipt_results / "report_facts.json")
        symlink_contract = json.loads(json.dumps(facts_contract))
        symlink_contract["facts"]["runtime_payload_artifact"] = "facts-source-alias.json"
        symlink_receipt_path = qc.write_receipt(
            report_name=None,
            figures=valid_figures,
            outputs=["result.txt"],
            qc_run_log="absent-qc-log.json",
            contract=symlink_contract,
            path="symlink-facts-receipt.json",
            strict=False,
        )
        symlink_receipt = json.loads(symlink_receipt_path.read_text(encoding="utf-8"))
        check(
            "receipt rejects a symlink that aliases report_facts.json to its runtime evidence",
            symlink_receipt.get("facts_artifact_verified") is False
            and "distinct files" in symlink_receipt.get("facts_artifact_verified_reason", ""),
            symlink_receipt.get("facts_artifact_verified_reason", "missing reason")[:140],
        )

        (receipt_results / "facts_payload.json").write_text(json.dumps({
            "completion": {"eligible": 3, "completed": 1, "not_computable": 2},
        }), encoding="utf-8")
        stale_receipt_path = qc.write_receipt(
            report_name=None,
            figures=valid_figures,
            outputs=["result.txt"],
            qc_run_log="absent-qc-log.json",
            contract=facts_contract,
            path="stale-facts-receipt.json",
            strict=False,
        )
        stale_receipt = json.loads(stale_receipt_path.read_text(encoding="utf-8"))
        check(
            "receipt rejects semantically valid facts left from an earlier runtime payload",
            stale_receipt.get("facts_artifact_verified") is False
            and "current runtime payload" in stale_receipt.get(
                "facts_artifact_verified_reason", ""
            ),
            stale_receipt.get("facts_artifact_verified_reason", "missing reason")[:140],
        )
        (receipt_results / "facts_payload.json").write_text(
            json.dumps(facts_payload), encoding="utf-8"
        )

        invalid_facts_payload = {
            "completion": {"eligible": 2, "completed": 2, "not_computable": 1},
        }
        (receipt_results / "facts_payload.json").write_text(
            json.dumps(invalid_facts_payload), encoding="utf-8"
        )
        (receipt_results / "report_facts.json").write_text(json.dumps({
            **invalid_facts_payload,
            "figures": valid_figures,
        }), encoding="utf-8")
        bad_receipt_path = qc.write_receipt(
            report_name=None,
            figures=valid_figures,
            outputs=["result.txt"],
            qc_run_log="absent-qc-log.json",
            contract=facts_contract,
            path="bad-facts-receipt.json",
            strict=False,
        )
        bad_receipt = json.loads(bad_receipt_path.read_text(encoding="utf-8"))
        check(
            "receipt rejects a semantically invalid canonical facts artifact",
            bad_receipt.get("facts_artifact_verified") is False,
            bad_receipt.get("facts_artifact_verified_reason", "missing reason")[:140],
        )

        outside_receipt_path = qc.write_receipt(
            report_name=None,
            figures=[{"step": 1, "file": str(outside), "caption": "outside"}],
            outputs=["result.txt"],
            qc_run_log="absent-qc-log.json",
            contract=facts_contract,
            path="outside-figure-receipt.json",
            strict=False,
        )
        outside_receipt = json.loads(outside_receipt_path.read_text(encoding="utf-8"))
        check(
            "receipt cannot credit a figure outside the results root",
            outside_receipt.get("figure_contract_satisfied") is False,
            outside_receipt.get("figure_contract_satisfied_reason", "missing reason")[:140],
        )

        na_results = root / "na-receipt-results"
        na_results.mkdir()
        (na_results / "data_dictionary.md").write_text("dictionary", encoding="utf-8")
        na_qc = load_qc(na_results)
        na_contract = receipt_contract("utility", figures_applicable=False)
        na_path = na_qc.write_receipt(
            report_name=None,
            figures=[],
            outputs=["data_dictionary.md"],
            qc_run_log="absent-qc-log.json",
            figure_not_applicable_reason="receipt fixture has no figure",
            contract=na_contract,
            strict=False,
        )
        na_receipt = json.loads(na_path.read_text(encoding="utf-8"))
        check(
            "receipt records facts as typed not_applicable when the contract does",
            na_receipt.get("facts_artifact_verified") == "not_applicable"
            and na_receipt.get("report_style_verified") == "not_applicable"
            and na_receipt.get("schema") == "phylo-run-receipt/3"
            and na_receipt.get("evidence", {}).get("facts_artifact_verified", {}).get("reason"),
            "typed non-applicability recorded",
        )

    width = max(len(label) for label, _, _ in results)
    for label, passed, detail in results:
        print(f"{label:{width}}  {'PASS' if passed else 'FAIL':4}  {detail}")
    failed = [row for row in results if not row[1]]
    print(f"RESULT: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
