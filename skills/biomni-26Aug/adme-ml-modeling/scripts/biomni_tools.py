"""Biomni-callable ADME tools plus a small JSON CLI for local validation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

REQUIRED_IMPORTS = {
    "chembl_structure_pipeline": "chembl-structure-pipeline",
    "joblib": "joblib",
    "mapie": "mapie",
    "molfeat": "molfeat",
    "numpy": "numpy",
    "pandas": "pandas",
    "pydantic": "pydantic",
    "rdkit": "rdkit",
    "sklearn": "scikit-learn",
    "scipy": "scipy",
    "splito": "splito",
    "xgboost": "xgboost",
}


# Virtual-environment markers that reveal which interpreter a package manager would install into.
SESSION_ENV_MARKERS = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
# The interactive Biomni agent session runs from this per-machine workspace, so a virtual
# environment located here is the live session's own environment, not an isolated runtime.
SESSION_WORKSPACE_ROOTS = ("/workspace",)
# Explicit, auditable opt-out for the rare case where an isolated environment is deliberately
# built under the session workspace and the operator accepts the risk.
SESSION_OVERRIDE_ENV = "ADME_SKILL_ACK_SESSION_ENV"
_TRUTHY = {"1", "true", "yes", "on"}


def _session_environment() -> dict[str, Any]:
    """Detect whether the pinned stack would be installed into the live agent session.

    The skill forbids installing its pinned runtime into the running Biomni session because doing
    so mutates the live interpreter and can silently downgrade the session's own packages (exactly
    what happens when ``uv`` targets ``/workspace/.venv``). The environment is treated as the live
    session when a virtual-environment marker (``VIRTUAL_ENV`` or ``UV_PROJECT_ENVIRONMENT``)
    resolves inside the interactive session workspace. Detection reads only environment variables,
    so it is deterministic and unit-testable by setting or unsetting those markers, and it is
    overridable through ``ADME_SKILL_ACK_SESSION_ENV`` for a purpose-built isolated environment that
    happens to live under the session workspace.
    """
    acknowledged = os.environ.get(SESSION_OVERRIDE_ENV, "").strip().lower() in _TRUTHY
    markers: dict[str, str] = {}
    for name in SESSION_ENV_MARKERS:
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        resolved = os.path.abspath(os.path.expanduser(value))
        if any(
            resolved == root or resolved.startswith(root.rstrip("/") + "/")
            for root in SESSION_WORKSPACE_ROOTS
        ):
            markers[name] = value
    return {
        "is_live_session": bool(markers) and not acknowledged,
        "acknowledged": acknowledged,
        "markers": markers,
    }


def dependency_status() -> dict[str, Any]:
    missing = [package for module, package in REQUIRED_IMPORTS.items() if importlib.util.find_spec(module) is None]
    session = _session_environment()
    if session["is_live_session"]:
        marker_text = ", ".join(f"{name}={value}" for name, value in session["markers"].items())
        return {
            "status": "unsafe_environment",
            "missing": missing,
            "active_session_markers": session["markers"],
            "override_env": SESSION_OVERRIDE_ENV,
            "message": (
                "Refusing to proceed: a virtual-environment marker points inside the live agent "
                f"session workspace ({marker_text}). Installing or running this skill's pinned "
                "stack here mutates the running Biomni session and can silently downgrade its "
                "packages. Build the pinned runtime in an isolated environment from "
                "pyproject.toml/uv.lock (for example, 'uv sync' into a dedicated prefix outside the "
                "session, or a container) and run the skill there. Set "
                f"{SESSION_OVERRIDE_ENV}=1 only if this IS a purpose-built isolated environment and "
                "you accept the risk."
            ),
        }
    return {
        "status": "ready" if not missing else "missing_dependencies",
        "missing": missing,
        "message": (
            "Use the pinned Biomni runtime described by pyproject.toml; do not install packages "
            "into an active agent session."
            if missing
            else "All standard-profile dependencies are importable."
        ),
    }


def _runtime_imports() -> tuple[Any, Any, Any]:
    status = dependency_status()
    if status["missing"]:
        raise RuntimeError(f"Missing standard-profile dependencies: {status['missing']}")
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from adme_skill import inspect_dataset, predict_bundle, train_model

    return inspect_dataset, train_model, predict_bundle


def inspect_adme_dataset(dataset_spec: dict[str, Any], output_dir: str) -> dict[str, Any]:
    """Audit one labelled small-molecule ADME endpoint before any model is trained."""
    try:
        inspect_dataset, _, _ = _runtime_imports()
        return inspect_dataset(dataset_spec, output_dir)
    except Exception as exc:  # noqa: BLE001 - Biomni tool boundaries return structured failures
        return {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}


def train_adme_model(
    dataset_spec: dict[str, Any], run_config: dict[str, Any], output_dir: str
) -> dict[str, Any]:
    """Run nested model selection and a locked prospective-style outer assessment."""
    try:
        _, train_model, _ = _runtime_imports()
        return train_model(dataset_spec, run_config, output_dir)
    except Exception as exc:  # noqa: BLE001 - Biomni tool boundaries return structured failures
        return {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}


def predict_adme_model(
    model_bundle_path: str,
    data_path: str,
    smiles_column: str,
    output_dir: str,
) -> dict[str, Any]:
    """Score new molecules with a saved bundle, conformal output, and domain flags."""
    try:
        _, _, predict_bundle = _runtime_imports()
        return predict_bundle(model_bundle_path, data_path, smiles_column, output_dir)
    except Exception as exc:  # noqa: BLE001 - Biomni tool boundaries return structured failures
        return {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}


def tool_descriptions() -> list[dict[str, Any]]:
    """Descriptions suitable for registration through Biomni A1.add_tool."""
    return [
        {
            "name": "inspect_adme_dataset",
            "description": (
                "Audit a labelled, single-endpoint small-molecule ADME dataset. Validates structures, "
                "units, assay context, class mapping, censoring, replicates, and dates. Always call "
                "before train_adme_model and resolve status=blocked findings rather than guessing."
            ),
        },
        {
            "name": "train_adme_model",
            "description": (
                "Train and honestly assess one supervised in-vitro ADME endpoint. Uses a locked outer "
                "time/scaffold/cluster/deployment split, inner-only selection, maintained molecular "
                "features, censor-aware AFT fitting, MAPIE uncertainty, and applicability-domain output."
            ),
        },
        {
            "name": "predict_adme_model",
            "description": (
                "Apply a saved ADME model bundle to new structures and write point predictions, "
                "conformal intervals or sets when supported, nearest-neighbor similarity, and explicit "
                "in/out-of-domain flags."
            ),
        },
    ]


def _load_json(value: str) -> dict[str, Any]:
    candidate = Path(value)
    if candidate.exists():
        return json.loads(candidate.read_text())
    return json.loads(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--dataset-spec", required=True)
    inspect_parser.add_argument("--output-dir", required=True)
    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("--dataset-spec", required=True)
    train_parser.add_argument("--run-config", default="{}")
    train_parser.add_argument("--output-dir", required=True)
    predict_parser = subparsers.add_parser("predict")
    predict_parser.add_argument("--model-bundle", required=True)
    predict_parser.add_argument("--data", required=True)
    predict_parser.add_argument("--smiles-column", default="smiles")
    predict_parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/biomni-adme-mpl")
    args = build_parser().parse_args(argv)
    if args.command == "preflight":
        result = dependency_status()
    elif args.command == "inspect":
        result = inspect_adme_dataset(_load_json(args.dataset_spec), args.output_dir)
    elif args.command == "train":
        result = train_adme_model(
            _load_json(args.dataset_spec), _load_json(args.run_config), args.output_dir
        )
    else:
        result = predict_adme_model(
            args.model_bundle, args.data, args.smiles_column, args.output_dir
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {"ready", "completed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
