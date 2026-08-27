#!/usr/bin/env python3
"""Cross-machine safety probe for TusoAI's shared history directory.

Run ``init`` once, run ``write`` concurrently on every proposed worker, then run
``verify``.  The probe is deliberately confined to ``.tusoai_shared_fs_probe``
under the supplied root.
"""
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

from tusoai.fs_utils import replace_file_portable, rmtree_portable  # noqa: E402
from tusoai.optimization import _DMLogSinks, _dm_read_history_entries  # noqa: E402

PROBE_DIRNAME = ".tusoai_shared_fs_probe"


def _probe_dir(root: str) -> Path:
    return Path(root).expanduser().resolve() / PROBE_DIRNAME


def _atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    replace_file_portable(tmp, path)


def cmd_init(args: argparse.Namespace) -> int:
    probe = _probe_dir(args.root)
    if probe.exists():
        rmtree_portable(probe, ignore_errors=True)
    probe.mkdir(parents=True, exist_ok=False)

    atomic_target = probe / "atomic_replace.json"
    atomic_tmp = probe / "atomic_replace.tmp"
    atomic_tmp.write_text('{"ok": true}', encoding="utf-8")
    os.replace(atomic_tmp, atomic_target)
    if json.loads(atomic_target.read_text(encoding="utf-8")) != {"ok": True}:
        raise RuntimeError("atomic replacement verification failed")

    (probe / "history.json").write_text("[]", encoding="utf-8")
    _atomic_json(
        probe / "manifest.json",
        {
            "schema_version": 1,
            "created_at": time.time(),
            "root": str(Path(args.root).expanduser().resolve()),
            "iterations": args.iterations,
            "nonce": uuid.uuid4().hex,
        },
    )
    print(json.dumps({"status": "initialized", "probe_dir": str(probe)}, indent=2))
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    probe = _probe_dir(args.root)
    history_path = probe / "history.json"
    manifest_path = probe / "manifest.json"
    if not history_path.exists() or not manifest_path.exists():
        raise FileNotFoundError("probe is not initialized; run the init command first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    iterations = int(args.iterations or manifest.get("iterations", 25))
    run_id = f"probe-{args.node_id}-{uuid.uuid4().hex[:10]}"
    sinks = _DMLogSinks(
        history_path=history_path,
        dev_path=probe / f"dev_{args.node_id}.json",
        prompt_io_path=probe / f"prompt_{args.node_id}.json",
        multi_machine=True,
        run_id=run_id,
    )
    for seq in range(iterations):
        sinks.log_history(
            {
                "stage": "shared_fs_probe",
                "node_id": args.node_id,
                "node_seq": seq,
                "writer_pid": os.getpid(),
                "written_at": time.time(),
                "accuracy": float(seq),
                "runtime": 0.0,
                "code": f"def probe_{args.node_id}_{seq}():\n    return {seq}\n",
            }
        )
    marker = probe / f"done_{args.node_id}.json"
    _atomic_json(marker, {"node_id": args.node_id, "iterations": iterations, "run_id": run_id})
    print(json.dumps({"status": "written", "node_id": args.node_id, "iterations": iterations}, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    probe = _probe_dir(args.root)
    manifest = json.loads((probe / "manifest.json").read_text(encoding="utf-8"))
    iterations = int(args.iterations or manifest.get("iterations", 25))
    expected_nodes = [str(node) for node in args.expected_node]
    if not expected_nodes:
        raise ValueError("provide at least one --expected-node")

    entries = _dm_read_history_entries(probe / "history.json")
    observed = {
        (str(entry.get("node_id")), int(entry.get("node_seq")))
        for entry in entries
        if isinstance(entry, dict) and entry.get("stage") == "shared_fs_probe"
    }
    expected = {(node, seq) for node in expected_nodes for seq in range(iterations)}
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    duplicate_keys = len(entries) - len({(e.get("run_id"), e.get("history_seq")) for e in entries if isinstance(e, dict)})
    lock_artifacts = sorted(str(path.name) for path in probe.glob("history.json.lock.d*"))
    report = {
        "status": "pass" if not missing and not extra and duplicate_keys == 0 and not lock_artifacts else "fail",
        "probe_dir": str(probe),
        "expected_entries": len(expected),
        "observed_entries": len(observed),
        "missing": missing[:20],
        "extra": extra[:20],
        "duplicate_history_keys": duplicate_keys,
        "leftover_lock_artifacts": lock_artifacts,
    }
    _atomic_json(probe / "verification.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 2


def cmd_clean(args: argparse.Namespace) -> int:
    probe = _probe_dir(args.root)
    if probe.exists():
        rmtree_portable(probe, ignore_errors=True)
    print(json.dumps({"status": "cleaned", "probe_dir": str(probe)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--root", required=True)
    init.add_argument("--iterations", type=int, default=25)
    init.set_defaults(func=cmd_init)

    write = sub.add_parser("write")
    write.add_argument("--root", required=True)
    write.add_argument("--node-id", required=True)
    write.add_argument("--iterations", type=int)
    write.set_defaults(func=cmd_write)

    verify = sub.add_parser("verify")
    verify.add_argument("--root", required=True)
    verify.add_argument("--expected-node", action="append", default=[])
    verify.add_argument("--iterations", type=int)
    verify.set_defaults(func=cmd_verify)

    clean = sub.add_parser("clean")
    clean.add_argument("--root", required=True)
    clean.set_defaults(func=cmd_clean)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
