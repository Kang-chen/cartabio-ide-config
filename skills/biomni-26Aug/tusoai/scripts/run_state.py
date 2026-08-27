#!/usr/bin/env python3
"""Atomic run-state and append-only event helper for TusoAI Biomni tasks."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "repo"))

from tusoai.fs_utils import replace_file_portable  # noqa: E402
from tusoai.optimization import _dm_history_file_lock  # noqa: E402


def _parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = [part for part in dotted.split(".") if part]
    if not parts:
        raise ValueError("empty state key")
    cursor: dict[str, Any] = data
    for part in parts[:-1]:
        existing = cursor.get(part)
        if existing is None:
            existing = {}
            cursor[part] = existing
        if not isinstance(existing, dict):
            raise TypeError(f"cannot descend into non-object key: {part}")
        cursor = existing
    cursor[parts[-1]] = value


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    replace_file_portable(tmp, path)


def _append_event(events: Path, event_type: str, message: str, data: Any = None) -> None:
    events.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.time(),
        "type": event_type,
        "message": message,
        "data": data,
    }
    lock_target = events.with_suffix(events.suffix + ".state")
    with _dm_history_file_lock(lock_target):
        with events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _apply_sets(data: dict[str, Any], values: list[str]) -> None:
    for assignment in values:
        if "=" not in assignment:
            raise ValueError(f"--set requires dotted.key=value, got: {assignment}")
        key, raw = assignment.split("=", 1)
        _set_dotted(data, key, _parse_value(raw))


def cmd_init(args: argparse.Namespace) -> int:
    state = Path(args.state).expanduser().resolve()
    events = Path(args.events).expanduser().resolve()
    if state.exists() and not args.force:
        raise FileExistsError(f"state already exists: {state}")
    template = json.loads(Path(args.template).expanduser().resolve().read_text(encoding="utf-8"))
    now = time.time()
    template["created_at"] = template.get("created_at") or now
    template["updated_at"] = now
    _apply_sets(template, args.set)
    with _dm_history_file_lock(state):
        _atomic_json(state, template)
    _append_event(events, "state_initialized", "Initialized TusoAI run state", {"state": str(state)})
    print(json.dumps(template, indent=2, sort_keys=True))
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    state = Path(args.state).expanduser().resolve()
    events = Path(args.events).expanduser().resolve() if args.events else None
    with _dm_history_file_lock(state):
        data = json.loads(state.read_text(encoding="utf-8"))
        _apply_sets(data, args.set)
        data["updated_at"] = time.time()
        _atomic_json(state, data)
    if args.event_type and events:
        event_data = _parse_value(args.event_data) if args.event_data else None
        _append_event(events, args.event_type, args.message or "State updated", event_data)
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def cmd_event(args: argparse.Namespace) -> int:
    data = _parse_value(args.data) if args.data else None
    _append_event(Path(args.events).expanduser().resolve(), args.type, args.message, data)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.state).expanduser().resolve().read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--template", required=True)
    init.add_argument("--state", required=True)
    init.add_argument("--events", required=True)
    init.add_argument("--set", action="append", default=[])
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    patch = sub.add_parser("patch")
    patch.add_argument("--state", required=True)
    patch.add_argument("--set", action="append", default=[])
    patch.add_argument("--events")
    patch.add_argument("--event-type")
    patch.add_argument("--message")
    patch.add_argument("--event-data")
    patch.set_defaults(func=cmd_patch)

    event = sub.add_parser("event")
    event.add_argument("--events", required=True)
    event.add_argument("--type", required=True)
    event.add_argument("--message", required=True)
    event.add_argument("--data")
    event.set_defaults(func=cmd_event)

    show = sub.add_parser("show")
    show.add_argument("--state", required=True)
    show.set_defaults(func=cmd_show)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
