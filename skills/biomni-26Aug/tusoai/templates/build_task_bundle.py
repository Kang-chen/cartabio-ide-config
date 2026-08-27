#!/usr/bin/env python3
"""Build and persist one trusted task bundle for every TusoAI cluster node."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pickle
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_factory(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("tuso_task_factory", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load task factory: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    factory_path = Path(args.factory).expanduser().resolve()
    module = _load_factory(factory_path)
    ai = module.build_ai()
    bundle: dict[str, Any] = module.build_task_bundle(ai)
    required = {"method_tasks", "data_tasks", "reference_filename", "task_description", "global_hints", "optimize_kwargs"}
    missing = sorted(required - set(bundle))
    if missing:
        raise ValueError(f"task bundle missing fields: {missing}")
    function_names = [task.function_name for task in [*bundle["method_tasks"], *bundle["data_tasks"]]]
    if not function_names or len(function_names) != len(set(function_names)):
        raise ValueError("task bundle must contain at least one uniquely named target")

    payload = pickle.dumps(bundle, protocol=pickle.HIGHEST_PROTOCOL)
    output = Path(args.output).expanduser().resolve()
    _atomic_bytes(output, payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "schema_version": 1,
        "created_at": time.time(),
        "factory": str(factory_path),
        "bundle": str(output),
        "sha256": digest,
        "function_names": function_names,
        "reference_filename": str(bundle["reference_filename"]),
        "task_description": str(bundle["task_description"]),
        "construction_cost": float(bundle.get("construction_cost", 0.0) or 0.0),
    }
    manifest_path = Path(args.manifest).expanduser().resolve()
    _atomic_bytes(manifest_path, json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
