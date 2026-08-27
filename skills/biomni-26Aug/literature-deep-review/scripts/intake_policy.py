#!/usr/bin/env python3
"""Validate the figure/OCR choices recorded during review intake.

Figure count and figure reading depth are separate decisions.  A request for
paper figures does not authorize the coordinator to silently turn OCR off to
save install time.  This module makes that distinction executable so a missing
question is caught before a long review proceeds.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
from typing import Any


OCR_MODES = frozenset({"off", "targeted", "all"})
OCR_DECISION_SOURCES = frozenset({
    "explicit_user",
    "delegated_default",
    "no_figures",
})
FIGURE_REUSE_POLICIES = frozenset({"reuse_cleared_only", "user_directed"})
FIGURE_REUSE_DECISION_SOURCES = frozenset({
    "explicit_user",
    "delegated_default",
    "no_figures",
})
FIGURE_COUNT_POLICIES = frozenset({"fixed", "adaptive"})
ADAPTIVE_BASE_MINIMUM = {"quick": 1, "deep": 4, "broad": 6}
INTAKE_SNAPSHOT = pathlib.Path("state/intake_snapshot.json")
MUTABLE_MANIFEST_FIELDS = frozenset(
    {
        "completed_at",
        "config",
        "metrics",
        "papers",
        "skill_provenance",
        "status",
        "updated_at",
    }
)


def adaptive_figure_resolution(
    mode: str,
    full_text_papers: int,
    populated_axes: int,
    eligible_figures: int,
) -> dict[str, int | bool]:
    """Return the disclosed inputs and result behind an adaptive figure floor."""
    counts = {
        "full_text_papers": full_text_papers,
        "populated_axes": populated_axes,
        "eligible_figures": eligible_figures,
    }
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in ADAPTIVE_BASE_MINIMUM:
        raise ValueError("mode must be one of: quick, deep, broad")
    baseline = ADAPTIVE_BASE_MINIMUM[normalized_mode]
    per_five = math.ceil(full_text_papers / 5)
    desired = max(baseline, per_five, populated_axes)
    resolved = min(desired, eligible_figures)
    return {
        "eligible_figures": eligible_figures,
        "full_text_papers": full_text_papers,
        "limited_by_eligible_supply": resolved < desired,
        "mode_baseline": baseline,
        "one_per_five_full_texts": per_five,
        "populated_axes": populated_axes,
        "resolved_minimum": resolved,
        "unlimited_desired_minimum": desired,
    }


def adaptive_figure_minimum(
    mode: str,
    full_text_papers: int,
    populated_axes: int,
    eligible_figures: int,
) -> int:
    """Resolve an adaptive floor from the corpus, without creating a cap.

    Use one figure per five retrieved full texts or one per populated evidence
    axis, whichever is larger, subject to the materially eligible figure supply.
    The selector may still include additional nonredundant figures.
    """
    return int(
        adaptive_figure_resolution(
            mode,
            full_text_papers,
            populated_axes,
            eligible_figures,
        )["resolved_minimum"]
    )


def intake_snapshot_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """Freeze the review brief without mutable execution and report settings."""
    frozen = {
        key: value
        for key, value in manifest.items()
        if key not in MUTABLE_MANIFEST_FIELDS
    }
    return {"schema_version": 1, "review_brief": frozen}


def intake_snapshot_errors(
    manifest: dict[str, Any], snapshot: dict[str, Any]
) -> list[str]:
    if not snapshot:
        return ["state/intake_snapshot.json is missing"]
    if snapshot.get("schema_version") != 1:
        return ["state/intake_snapshot.json has an invalid schema version"]
    if snapshot != intake_snapshot_payload(manifest):
        return [
            "the frozen review brief changed after intake; restore it or begin "
            "a newly scoped run"
        ]
    return []


def ensure_intake_snapshot(
    manifest_path: pathlib.Path, manifest: dict[str, Any]
) -> pathlib.Path:
    path = manifest_path.parent / INTAKE_SNAPSHOT
    payload = intake_snapshot_payload(manifest)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid intake snapshot: {exc}") from exc
        errors = intake_snapshot_errors(manifest, current)
        if errors:
            raise ValueError("; ".join(errors))
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def recommended_ocr_mode(minimum_paper_figures: int) -> str:
    """Return the conservative delegated default for the requested output."""
    return "targeted" if minimum_paper_figures > 0 else "off"


def figure_intake_errors(manifest: dict[str, Any]) -> list[str]:
    """Return all figure/OCR intake errors in a run manifest.

    The figure contract is required for skill-driven report runs.  Direct
    low-level uses of ``evidence_first.py`` may omit the figure fields entirely;
    its caller decides whether to require this full contract.
    """
    config = manifest.get("config")
    if not isinstance(config, dict):
        return ["run_manifest.config must be an object"]

    errors: list[str] = []

    # The downstream gates cannot run without the run-level identity fields the
    # rest of the pipeline reads: the report has no heading without a title, and
    # figure pair-verification (figure_selection.select) refuses to run without
    # the subject aliases it discriminates on. A run that carries none of these
    # cannot be checked, so it must not start — the same principle that stops the
    # pair-verification fail-open.
    from figure_selection import subject_aliases_from_manifest
    if not str(manifest.get("subject") or "").strip():
        errors.append(
            "run_manifest.subject is required: figure subject gating and alias "
            "resolution depend on it"
        )
    if not str(manifest.get("title") or "").strip():
        errors.append(
            "run_manifest.title is required: the report has no heading without it"
        )
    if not subject_aliases_from_manifest(manifest):
        errors.append(
            "run_manifest resolves no subject aliases: set subject_aliases, or a "
            "subject/subject_long from which they can be derived; figure pair "
            "verification cannot run without them"
        )

    floor = config.get("minimum_paper_figures")
    raw_policy = config.get("figure_count_policy")
    # Manifests created before figure_count_policy existed already carry an
    # integer floor. Treat those as fixed rather than invalidating old runs.
    policy = (
        "fixed" if raw_policy is None and isinstance(floor, int) else raw_policy
    )
    if policy not in FIGURE_COUNT_POLICIES:
        errors.append(
            "config.figure_count_policy must be one of: fixed, adaptive"
        )
        return errors
    unresolved_adaptive = policy == "adaptive" and floor is None
    if not unresolved_adaptive and (
        isinstance(floor, bool) or not isinstance(floor, int) or floor < 0
    ):
        errors.append(
            "config.minimum_paper_figures must be a non-negative integer, or "
            "null only while an adaptive minimum awaits the figure inventory"
        )
        return errors
    if policy == "adaptive" and not unresolved_adaptive:
        resolution = config.get("adaptive_figure_resolution")
        if not isinstance(resolution, dict):
            errors.append(
                "resolved adaptive figure policy requires "
                "config.adaptive_figure_resolution"
            )
        elif resolution.get("resolved_minimum") != floor:
            errors.append(
                "adaptive_figure_resolution.resolved_minimum differs from "
                "config.minimum_paper_figures"
            )

    ocr = config.get("ocr")
    source = config.get("ocr_decision_source")
    reuse_policy = config.get("figure_reuse_policy")
    reuse_source = config.get("figure_reuse_decision_source")
    if ocr not in OCR_MODES:
        errors.append("config.ocr must be one of: off, targeted, all")
    if source not in OCR_DECISION_SOURCES:
        errors.append(
            "config.ocr_decision_source must be one of: explicit_user, "
            "delegated_default, no_figures"
        )
    if reuse_policy not in FIGURE_REUSE_POLICIES:
        errors.append(
            "config.figure_reuse_policy must be one of: "
            "reuse_cleared_only, user_directed"
        )
    if reuse_source not in FIGURE_REUSE_DECISION_SOURCES:
        errors.append(
            "config.figure_reuse_decision_source must be one of: "
            "explicit_user, delegated_default, no_figures"
        )
    if errors:
        return errors

    figures_requested = policy == "adaptive" or floor > 0
    if figures_requested:
        if source == "no_figures":
            errors.append(
                "ocr_decision_source cannot be no_figures when paper figures are requested"
            )
        if ocr == "off" and source != "explicit_user":
            errors.append(
                "OCR may be off with requested paper figures only after an explicit user choice"
            )
        if source == "delegated_default" and ocr != "targeted":
            errors.append(
                "the delegated OCR default is targeted whenever paper figures are requested"
            )
        if reuse_source == "no_figures":
            errors.append(
                "figure_reuse_decision_source cannot be no_figures when paper "
                "figures are requested"
            )
        if reuse_policy == "user_directed" and reuse_source != "explicit_user":
            errors.append(
                "user-directed figure inclusion requires an explicit user choice"
            )
        if (reuse_source == "delegated_default"
                and reuse_policy != "reuse_cleared_only"):
            errors.append(
                "the delegated figure-reuse default is reuse_cleared_only"
            )
    else:
        if source == "no_figures" and ocr != "off":
            errors.append("a no-figures OCR decision must use ocr=off")
        if source == "delegated_default" and ocr != "off":
            errors.append("the delegated OCR default is off when no paper figures are requested")
        if reuse_source == "no_figures" and reuse_policy != "reuse_cleared_only":
            errors.append(
                "a no-figures reuse decision must use reuse_cleared_only"
            )
        if (reuse_source == "delegated_default"
                and reuse_policy != "reuse_cleared_only"):
            errors.append(
                "the delegated figure-reuse default is reuse_cleared_only"
            )

    if ocr == "all" and source != "explicit_user":
        errors.append("all-figure OCR requires an explicit user choice")
    return errors


def require_figure_intake(manifest: dict[str, Any]) -> None:
    """Raise a concise error when the recorded figure intake is incomplete."""
    errors = figure_intake_errors(manifest)
    if errors:
        raise ValueError("invalid figure/OCR intake: " + "; ".join(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the recorded paper-figure and OCR intake choices"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--resolve-adaptive", action="store_true")
    parser.add_argument("--full-text-papers", type=int)
    parser.add_argument("--populated-axes", type=int)
    parser.add_argument("--eligible-figures", type=int)
    args = parser.parse_args(argv)
    path = pathlib.Path(args.manifest)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if args.resolve_adaptive:
            config = manifest.get("config") or {}
            if config.get("figure_count_policy") != "adaptive":
                raise ValueError(
                    "--resolve-adaptive requires config.figure_count_policy=adaptive"
                )
            required = {
                "--full-text-papers": args.full_text_papers,
                "--populated-axes": args.populated_axes,
                "--eligible-figures": args.eligible_figures,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError(
                    "--resolve-adaptive also requires " + ", ".join(missing)
                )
            resolution = adaptive_figure_resolution(
                str(manifest.get("mode") or ""),
                args.full_text_papers,
                args.populated_axes,
                args.eligible_figures,
            )
            config["minimum_paper_figures"] = resolution["resolved_minimum"]
            config["adaptive_figure_resolution"] = resolution
            manifest["config"] = config
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        require_figure_intake(manifest)
        ensure_intake_snapshot(path, manifest)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    if args.resolve_adaptive:
        print(
            "adaptive figure minimum resolved: "
            f"{manifest['config']['minimum_paper_figures']}"
        )
    else:
        print("figure/OCR intake OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
