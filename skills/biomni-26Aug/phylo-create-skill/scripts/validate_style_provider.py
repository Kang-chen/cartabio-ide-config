#!/usr/bin/env python3
"""Validate a report-style provider against the creator's generic runtime contract."""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

sys.dont_write_bytecode = True

_HERE = pathlib.Path(__file__).resolve().parent
_STYLE_MODULE = _HERE.parent / "templates" / "report_style.py"


def style_module():
    if not _STYLE_MODULE.is_file():
        raise SystemExit(f"missing provider validator module: {_STYLE_MODULE}")
    spec = importlib.util.spec_from_file_location("_provider_contract", _STYLE_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider_dir", type=pathlib.Path)
    parser.add_argument("--activation", choices=("default", "explicit_only"))
    args = parser.parse_args()
    directory = args.provider_dir.expanduser().resolve()
    if not directory.is_dir():
        parser.error(f"provider directory does not exist: {directory}")
    module = style_module()
    try:
        profile, source_path, source = module.validate_provider_directory(
            directory,
            activation_hint=args.activation,
        )
    except module.StyleProviderError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "provider": profile["provider"],
        "activation": profile["activation"],
        "aliases": profile.get("user_selection_aliases", []),
        "pdf_markers": profile["pdf_markers"],
        "source": {**source, "path": str(source_path)},
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
