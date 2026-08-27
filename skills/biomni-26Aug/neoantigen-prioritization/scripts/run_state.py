#!/usr/bin/env python3
"""Durable run-state manager for long-horizon TESLA prioritization runs.

The scientific pipeline remains in ``neoantigen_tesla.py``. This helper makes the
surrounding Biomni workflow resumable by atomically recording phase transitions,
input fingerprints, artifact fingerprints, the next action, and a compact journal.
It uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1
SKILL_NAME = "neoantigen-prioritization"
PHASES = (
    "intake",
    "preflight",
    "prioritization",
    "validation",
    "visualization",
    "reporting",
    "handoff",
)
AUTO_HASH_LIMIT = 256 * 1024 * 1024


class StateError(RuntimeError):
    """Raised when a requested transition would make run state ambiguous."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(path_value: str, mode: str = "auto") -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise StateError(f"Expected a readable file: {path}")
    stat = path.stat()
    use_content = mode == "full" or (mode == "auto" and stat.st_size <= AUTO_HASH_LIMIT)
    record: dict[str, Any] = {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "fingerprint_kind": "sha256" if use_content else "metadata",
    }
    if use_content:
        record["sha256"] = _sha256_file(path)
    else:
        raw = f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}".encode()
        record["metadata_fingerprint"] = hashlib.sha256(raw).hexdigest()
    return record


def _parse_pairs(values: list[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise StateError(f"{label} must use NAME=PATH syntax: {value!r}")
        name, path = value.split("=", 1)
        name, path = name.strip(), path.strip()
        if not name or not path:
            raise StateError(f"{label} must use non-empty NAME=PATH syntax: {value!r}")
        if name in parsed:
            raise StateError(f"Duplicate {label} name: {name}")
        parsed[name] = path
    return parsed


def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _load(run_dir_value: str) -> tuple[Path, dict[str, Any]]:
    run_dir = Path(run_dir_value).expanduser().resolve()
    path = _state_path(run_dir)
    if not path.is_file():
        raise StateError(f"Run state does not exist: {path}")
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    if state.get("schema_version") != SCHEMA_VERSION:
        raise StateError(f"Unsupported state schema: {state.get('schema_version')}")
    return run_dir, state


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    last_error = state.get("last_error") or {}
    return {
        "schema_version": state["schema_version"],
        "skill": state["skill"],
        "run_id": state["run_id"],
        "case_id": state["case_id"],
        "status": state["status"],
        "current_phase": state.get("current_phase"),
        "next_action": state.get("next_action"),
        "updated_at": state["updated_at"],
        "last_error": ({"phase": last_error.get("phase"), "at": last_error.get("at")}
                       if last_error else None),
        "phases": {name: state["phases"][name]["status"] for name in PHASES},
    }


def _save(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    _atomic_json(_state_path(run_dir), state)
    results_dir = state.get("results_dir")
    if results_dir:
        _atomic_json(Path(results_dir) / "status.json", _summary(state))


def _journal(run_dir: Path, event: str, **details: Any) -> None:
    record = {"at": _now(), "event": event, **details}
    with (run_dir / "journal.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_plan(run_dir: Path, state: dict[str, Any]) -> None:
    phase_lines = "\n".join(f"- [ ] {i}. `{name}`" for i, name in enumerate(PHASES))
    inputs = "\n".join(
        f"- `{name}`: `{record['path']}` ({record['fingerprint_kind']})"
        for name, record in state["inputs"].items()
    )
    text = f"""# Managed TESLA run plan

- Run ID: `{state['run_id']}`
- De-identified case ID: `{state['case_id']}`
- State: `state.json`
- Journal: `journal.jsonl`
- User-visible results: `{state.get('results_dir') or 'not configured'}`

## Frozen inputs

{inputs or '- None recorded'}

## Phase checklist

{phase_lines}

## Non-negotiable scientific invariants

- Use real somatic variants and a real patient HLA-I genotype.
- Use MHCflurry for peptide-MHC-I values; never synthesize a fallback score.
- Keep missing expression, VAF, stability, and recognition measurements null.
- Do not interpret a missing measurement as a low measurement.
- Do not expose direct patient identifiers in paths, logs, reports, or progress messages.

## Resume protocol

Run `python3 scripts/run_state.py verify --run-dir <this-directory>`, read `state.json`,
then continue exactly from `next_action`. Never repeat a completed phase unless verification
shows that one of its recorded inputs or artifacts changed.
"""
    (run_dir / "plan.md").write_text(text, encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).expanduser().resolve()
    if _state_path(run_dir).exists():
        raise StateError(f"Refusing to overwrite existing run state: {_state_path(run_dir)}")
    run_dir.mkdir(parents=True, exist_ok=True)
    results_dir = str(Path(args.results_dir).expanduser().resolve()) if args.results_dir else None
    if results_dir:
        Path(results_dir).mkdir(parents=True, exist_ok=True)

    input_paths = _parse_pairs(args.input, "input")
    inputs = {name: _fingerprint(path, args.hash_mode) for name, path in input_paths.items()}
    config: dict[str, Any] = {}
    config_file = None
    if args.config:
        config_path = Path(args.config).expanduser().resolve()
        with config_path.open(encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            raise StateError("Configuration JSON must contain an object")
        config_file = _fingerprint(str(config_path), "full")
    canonical_config = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    created = _now()
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "skill": SKILL_NAME,
        "run_id": run_dir.name,
        "case_id": args.case_id,
        "status": "active",
        "current_phase": "intake",
        "next_action": "Begin phase intake",
        "created_at": created,
        "updated_at": created,
        "results_dir": results_dir,
        "inputs": inputs,
        "config": config,
        "config_file": config_file,
        "config_sha256": hashlib.sha256(canonical_config).hexdigest(),
        "phases": {
            name: {
                "status": "pending",
                "attempts": 0,
                "started_at": None,
                "completed_at": None,
                "last_message": None,
                "error": None,
                "artifacts": [],
            }
            for name in PHASES
        },
        "artifacts": {},
        "last_error": None,
    }
    _write_plan(run_dir, state)
    _save(run_dir, state)
    _journal(run_dir, "initialized", inputs=sorted(inputs), config_sha256=state["config_sha256"])
    print(json.dumps(_summary(state), indent=2, sort_keys=True))


def _phase_index(phase: str) -> int:
    try:
        return PHASES.index(phase)
    except ValueError as exc:
        raise StateError(f"Unknown phase {phase!r}; choose from {', '.join(PHASES)}") from exc


def cmd_begin(args: argparse.Namespace) -> None:
    run_dir, state = _load(args.run_dir)
    index = _phase_index(args.phase)
    incomplete = [name for name in PHASES[:index] if state["phases"][name]["status"] != "completed"]
    if incomplete:
        raise StateError(f"Cannot begin {args.phase}; earlier phases are incomplete: {incomplete}")
    phase = state["phases"][args.phase]
    if phase["status"] == "completed":
        raise StateError(f"Phase already completed: {args.phase}")
    if phase["status"] == "in_progress":
        raise StateError(f"Phase already in progress; resume from next_action: {state['next_action']}")
    phase.update({
        "status": "in_progress",
        "attempts": phase["attempts"] + 1,
        "started_at": _now(),
        "last_message": None,
        "error": None,
    })
    state.update({
        "status": "active",
        "current_phase": args.phase,
        "next_action": args.next_action or f"Execute the {args.phase} phase gate",
        "last_error": None,
    })
    _save(run_dir, state)
    _journal(run_dir, "phase_began", phase=args.phase, attempt=phase["attempts"],
             next_action=state["next_action"])
    print(json.dumps(_summary(state), indent=2, sort_keys=True))


def cmd_note(args: argparse.Namespace) -> None:
    run_dir, state = _load(args.run_dir)
    phase_name = state.get("current_phase")
    if not phase_name or state["phases"][phase_name]["status"] != "in_progress":
        raise StateError("A note requires an in-progress phase")
    state["phases"][phase_name]["last_message"] = args.message
    if args.next_action:
        state["next_action"] = args.next_action
    _save(run_dir, state)
    _journal(run_dir, "note", phase=phase_name, message=args.message,
             next_action=state["next_action"])
    print(json.dumps(_summary(state), indent=2, sort_keys=True))


def cmd_complete(args: argparse.Namespace) -> None:
    run_dir, state = _load(args.run_dir)
    index = _phase_index(args.phase)
    phase = state["phases"][args.phase]
    if phase["status"] != "in_progress":
        raise StateError(f"Can only complete an in-progress phase: {args.phase}")
    artifact_paths = _parse_pairs(args.artifact, "artifact")
    if not artifact_paths:
        raise StateError(f"Phase completion requires at least one gate artifact: {args.phase}")
    recorded = []
    for name, path in artifact_paths.items():
        if name in state["artifacts"]:
            raise StateError(f"Artifact name is already recorded: {name}")
        state["artifacts"][name] = _fingerprint(path, "full") | {"phase": args.phase}
        recorded.append(name)
    phase.update({
        "status": "completed",
        "completed_at": _now(),
        "last_message": args.message,
        "error": None,
        "artifacts": recorded,
    })
    if index == len(PHASES) - 1:
        state.update({
            "status": "complete",
            "current_phase": None,
            "next_action": None,
            "last_error": None,
        })
    else:
        next_phase = PHASES[index + 1]
        state.update({
            "status": "active",
            "current_phase": next_phase,
            "next_action": args.next_action or f"Begin phase {next_phase}",
            "last_error": None,
        })
    _save(run_dir, state)
    _journal(run_dir, "phase_completed", phase=args.phase, artifacts=recorded,
             message=args.message, next_action=state["next_action"])
    print(json.dumps(_summary(state), indent=2, sort_keys=True))


def cmd_fail(args: argparse.Namespace) -> None:
    run_dir, state = _load(args.run_dir)
    _phase_index(args.phase)
    phase = state["phases"][args.phase]
    if phase["status"] != "in_progress":
        raise StateError(f"Can only fail an in-progress phase: {args.phase}")
    status = "blocked" if args.blocked else "failed"
    phase.update({"status": status, "error": args.error, "last_message": args.error})
    state.update({
        "status": status,
        "current_phase": args.phase,
        "next_action": args.next_action or f"Resolve {args.phase} failure, then begin it again",
        "last_error": {"phase": args.phase, "message": args.error, "at": _now()},
    })
    _save(run_dir, state)
    _journal(run_dir, "phase_failed", phase=args.phase, status=status, error=args.error,
             next_action=state["next_action"])
    print(json.dumps(_summary(state), indent=2, sort_keys=True))


def _verify_record(record: dict[str, Any]) -> str | None:
    path = Path(record["path"])
    if not path.is_file():
        return "missing"
    stat = path.stat()
    if stat.st_size != record["size"]:
        return "size changed"
    if record["fingerprint_kind"] == "sha256":
        if _sha256_file(path) != record["sha256"]:
            return "sha256 changed"
    elif stat.st_mtime_ns != record["mtime_ns"]:
        return "mtime changed (metadata-only fingerprint)"
    return None


def cmd_verify(args: argparse.Namespace) -> None:
    _, state = _load(args.run_dir)
    problems = []
    config_file = state.get("config_file")
    if config_file:
        problem = _verify_record(config_file)
        if problem:
            problems.append({"kind": "config", "name": "config_file", "problem": problem,
                             "path": config_file["path"]})
    for kind in ("inputs", "artifacts"):
        for name, record in state[kind].items():
            problem = _verify_record(record)
            if problem:
                problems.append({"kind": kind[:-1], "name": name, "problem": problem,
                                 "path": record["path"]})
    result = {"ok": not problems, "run_id": state["run_id"], "status": state["status"],
              "current_phase": state.get("current_phase"), "next_action": state.get("next_action"),
              "problems": problems}
    print(json.dumps(result, indent=2, sort_keys=True))
    if problems:
        raise SystemExit(2)


def cmd_status(args: argparse.Namespace) -> None:
    _, state = _load(args.run_dir)
    print(json.dumps(state if args.full else _summary(state), indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage durable TESLA run state")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a new managed run")
    init.add_argument("--run-dir", required=True)
    init.add_argument("--results-dir")
    init.add_argument("--case-id", required=True, help="De-identified case identifier")
    init.add_argument("--input", action="append", default=[], metavar="NAME=PATH")
    init.add_argument("--config", help="Frozen run configuration as a JSON object")
    init.add_argument("--hash-mode", choices=("auto", "full", "metadata"), default="auto")
    init.set_defaults(func=cmd_init)

    begin = sub.add_parser("begin", help="Begin or retry the next legal phase")
    begin.add_argument("--run-dir", required=True)
    begin.add_argument("phase")
    begin.add_argument("--next-action")
    begin.set_defaults(func=cmd_begin)

    note = sub.add_parser("note", help="Checkpoint progress within the current phase")
    note.add_argument("--run-dir", required=True)
    note.add_argument("--message", required=True)
    note.add_argument("--next-action")
    note.set_defaults(func=cmd_note)

    complete = sub.add_parser("complete", help="Complete a phase and fingerprint its artifacts")
    complete.add_argument("--run-dir", required=True)
    complete.add_argument("phase")
    complete.add_argument("--artifact", action="append", default=[], metavar="NAME=PATH")
    complete.add_argument("--message")
    complete.add_argument("--next-action")
    complete.set_defaults(func=cmd_complete)

    fail = sub.add_parser("fail", help="Record a failed or externally blocked phase")
    fail.add_argument("--run-dir", required=True)
    fail.add_argument("phase")
    fail.add_argument("--error", required=True)
    fail.add_argument("--next-action")
    fail.add_argument("--blocked", action="store_true")
    fail.set_defaults(func=cmd_fail)

    status = sub.add_parser("status", help="Read the compact or full run state")
    status.add_argument("--run-dir", required=True)
    status.add_argument("--full", action="store_true")
    status.set_defaults(func=cmd_status)

    verify = sub.add_parser("verify", help="Verify recorded inputs and artifacts have not drifted")
    verify.add_argument("--run-dir", required=True)
    verify.set_defaults(func=cmd_verify)
    return parser


def main() -> int:
    try:
        args = _parser().parse_args()
        args.func(args)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, StateError) as exc:
        print(f"run_state: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
