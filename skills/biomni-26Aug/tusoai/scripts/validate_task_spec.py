#!/usr/bin/env python3
"""Validate the persisted TusoAI task/context contract before construction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_TOP_LEVEL = {
    "schema_version",
    "task_id",
    "objective",
    "metric",
    "evaluator",
    "editable_targets",
    "immutable_constraints",
    "constraint_coverage",
    "global_hints",
    "budgets",
}


def _find_placeholders(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            findings.extend(_find_placeholders(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_find_placeholders(child, f"{path}[{index}]"))
    elif isinstance(value, str) and ("replace-me" in value.lower() or "replace_me" in value.lower()):
        findings.append(path)
    return findings


def _constraint_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("constraint", "")).strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec")
    args = parser.parse_args()
    path = Path(args.spec).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []

    missing = sorted(REQUIRED_TOP_LEVEL - set(data))
    if missing:
        errors.append(f"missing top-level fields: {', '.join(missing)}")
    placeholders = _find_placeholders(data)
    if placeholders:
        errors.append(f"unresolved template placeholders at: {', '.join(placeholders)}")

    targets = data.get("editable_targets") or []
    names: list[str] = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            errors.append(f"editable_targets[{index}] is not an object")
            continue
        for field in ("function_name", "task_type", "source_path"):
            if not str(target.get(field, "")).strip():
                errors.append(f"editable_targets[{index}] missing {field}")
        names.append(str(target.get("function_name", "")))
        if target.get("task_type") not in {"method", "data"}:
            errors.append(f"editable_targets[{index}].task_type must be method or data")
        if not target.get("hints"):
            errors.append(f"editable_targets[{index}] has no target-specific hints")
    if len(names) != len(set(names)):
        errors.append("editable target function names must be unique")

    constraints = [_constraint_text(item) for item in data.get("immutable_constraints") or []]
    constraints = [item for item in constraints if item]
    if not constraints:
        errors.append("immutable_constraints must be non-empty")
    coverage: dict[str, list[str]] = {}
    for index, item in enumerate(data.get("constraint_coverage") or []):
        if not isinstance(item, dict):
            errors.append(f"constraint_coverage[{index}] is not an object")
            continue
        constraint = str(item.get("constraint", "")).strip()
        enforced_by = [str(value).strip() for value in item.get("enforced_by") or [] if str(value).strip()]
        if not constraint:
            errors.append(f"constraint_coverage[{index}] has no constraint")
        if not enforced_by:
            errors.append(f"constraint_coverage[{index}] has no enforcement route")
        coverage[constraint] = enforced_by
    for constraint in constraints:
        if constraint not in coverage:
            errors.append(f"constraint is not covered: {constraint}")

    evaluator = data.get("evaluator") or {}
    if not str(evaluator.get("path", "")).strip():
        errors.append("evaluator.path is required")
    if evaluator.get("direction") not in {"maximize", "minimize_via_negative"}:
        errors.append("evaluator.direction must be maximize or minimize_via_negative")

    report = {"valid": not errors, "path": str(path), "errors": errors}
    print(json.dumps(report, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
