#!/usr/bin/env python3
"""Validate the tusoskill skill package structure."""
from __future__ import annotations

import argparse
import json
import py_compile
from pathlib import Path

REQUIRED_FILES = [
    "SKILL.md",
    "README.md",
    "VERSION",
    "references/method_development_loop.md",
    "references/benchmark_and_evaluation.md",
    "references/biological_reasoning_and_priors.md",
    "references/data_integration_and_search.md",
    "references/diagnostics_ablations_and_error_analysis.md",
    "references/derivation_and_model_formulation.md",
    "references/foundation_model_optimization.md",
    "references/efficiency_simplification_and_scaling.md",
    "references/integrity_and_reproducibility.md",
    "references/state_and_memory.md",
    "references/final_delivery_contract.md",
    "scripts/biomni_method_state.py",
    "scripts/create_benchmark_scaffold.py",
    "scripts/benchmark_runner.py",
    "scripts/protected_hashes.py",
    "scripts/create_manifest.py",
    "scripts/safe_zip.py",
    "templates/benchmark_card.md",
    "templates/method_blueprint.md",
    "templates/final_report.md",
]

REQUIRED_PHRASES = [
    "Biomni owns the full method-development loop",
    "Freeze the evaluator before optimization",
    "50 substantive method iterations per round, with no wall-clock limit",
    "keep iterating",
    "smaller validation subset",
    "diversity portfolio",
    "Optimize for real generalization",
    "Biological data and knowledge integration",
    "Final selection and delivery",
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--skill-root", required=True)
    p.add_argument("--compile", action="store_true")
    args = p.parse_args()
    root = Path(args.skill_root)
    errors = []
    for rel in REQUIRED_FILES:
        if not (root / rel).exists():
            errors.append(f"missing {rel}")
    skill = (root / "SKILL.md").read_text(encoding="utf-8") if (root / "SKILL.md").exists() else ""
    for phrase in REQUIRED_PHRASES:
        if phrase not in skill:
            errors.append(f"SKILL.md missing required phrase: {phrase}")
    if len([p for p in root.rglob("*") if p.is_file()]) > 10000:
        errors.append("package exceeds 10000 files")
    for json_path in root.rglob("*.json"):
        try:
            json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"invalid JSON {json_path.relative_to(root)}: {e}")
    legacy_tokens = ["diagnostic" + "_2", "diagnostic" + "_v2", "ablation" + "_2", "ablation" + "_v2"]
    for candidate in root.rglob("*"):
        if candidate.is_file() and candidate.suffix.lower() in {".md", ".py", ".json", ".txt", ".sh", ".yaml", ".yml"}:
            body = candidate.read_text(encoding="utf-8", errors="replace").lower()
            for token in legacy_tokens:
                if token in body:
                    errors.append(f"legacy or over-specific token in {candidate.relative_to(root)}")
    if args.compile:
        for py in root.rglob("*.py"):
            try:
                py_compile.compile(str(py), doraise=True)
            except Exception as e:
                errors.append(f"python compile failed {py.relative_to(root)}: {e}")
    if errors:
        print("tusoskill skill validation failed:")
        for e in errors:
            print(f"- {e}")
        raise SystemExit(1)
    print(f"tusoskill skill validation passed: {root}")


if __name__ == "__main__":
    main()
