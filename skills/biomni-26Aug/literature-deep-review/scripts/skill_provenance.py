#!/usr/bin/env python3
"""Bind a review run to the exact literature-review skill package it used."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from typing import Iterable

from object_exchange import create_bundle


SCHEMA_VERSION = 1
UPGRADE_SCHEMA_VERSION = 1
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PACKAGE_INPUTS = ("SKILL.md", "assets", "references", "scripts", "templates")
IGNORED_NAMES = frozenset({".DS_Store", ".pytest_cache", "__pycache__"})
IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})
COMMIT_ENVIRONMENT_VARIABLE = "LITERATURE_REVIEW_SKILL_GIT_COMMIT"
ENTRYPOINT_VERSION_ENVIRONMENT_VARIABLE = "BIOMNI_SKILL_ENTRYPOINT_VERSION_ID"
DEPLOYMENT_METADATA_NAME = "skill_deployment.json"
RECEIPT = pathlib.Path("state/skill_provenance.json")
UPGRADE_LEDGER = pathlib.Path("state/skill_provenance_upgrades.jsonl")
UPGRADE_RESUME_STAGES = frozenset({"reconcile", "build", "verify", "deliver"})
UPGRADE_REQUIRED_ARTIFACTS = (
    "corpus/claims.jsonl",
    "evidence/evidence.jsonl",
    "evidence/entailment.jsonl",
)
UPGRADE_PROTECTED_ARTIFACTS = (
    "state/intake_snapshot.json",
    "corpus/references_snapshot.jsonl",
    "corpus/claims.jsonl",
    "corpus/corpus_ledger.json",
    "fulltext/papers.jsonl",
    "fulltext/parsed",
    "evidence/adjudications.jsonl",
    "evidence/adjudication_audit.jsonl",
    "evidence/evidence.jsonl",
    "evidence/entailment.jsonl",
    "evidence/figure_entailment.jsonl",
    "state/assemblies",
    "state/managed_launches",
)
IDENTITY_KEYS = (
    "git_commit",
    "skill_directory_sha256",
    "skill_bundle_sha256",
    "file_count",
    "entrypoint_version_id",
)


def _package_files(skill_root: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for name in PACKAGE_INPUTS:
        source = skill_root / name
        if not source.exists():
            continue
        candidates: Iterable[pathlib.Path]
        candidates = (source,) if source.is_file() else source.rglob("*")
        for path in candidates:
            if not path.is_file():
                continue
            relative = path.relative_to(skill_root)
            if IGNORED_NAMES.intersection(relative.parts):
                continue
            if path.suffix in IGNORED_SUFFIXES:
                continue
            if path.is_symlink():
                raise ValueError(f"skill package input may not be a symlink: {path}")
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(skill_root).as_posix())


def directory_sha256(skill_root: pathlib.Path) -> tuple[str, int]:
    """Hash the runtime package as sorted relative paths and file bytes."""
    root = pathlib.Path(skill_root).resolve()
    digest = hashlib.sha256()
    files = _package_files(root)
    if not files:
        raise ValueError(f"skill package has no runtime files: {root}")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        value = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest(), len(files)


def bundle_sha256(skill_root: pathlib.Path) -> str:
    root = pathlib.Path(skill_root).resolve()
    include_names = tuple(name for name in PACKAGE_INPUTS if (root / name).exists())
    with tempfile.TemporaryDirectory(prefix="ldr-provenance-") as directory:
        bundle = pathlib.Path(directory) / "skill.tar"
        return create_bundle(root, bundle, include_names)


def _deployment_metadata(skill_root: pathlib.Path) -> dict:
    path = pathlib.Path(skill_root) / DEPLOYMENT_METADATA_NAME
    if not path.exists():
        return {}
    metadata = _read_json(path)
    deployed = str(metadata.get("skill_commit") or "")
    expected_hash = str(metadata.get("skill_directory_sha256") or "")
    expected_count = metadata.get("file_count")
    if (
        metadata.get("schema_version") != 2
        or metadata.get("skill") != "literature-deep-review"
        or not COMMIT_PATTERN.fullmatch(deployed)
        or not HASH_PATTERN.fullmatch(expected_hash)
        or isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count < 1
    ):
        raise ValueError(f"invalid deployment metadata: {path}")
    actual_hash, actual_count = directory_sha256(skill_root)
    if actual_hash != expected_hash or actual_count != expected_count:
        raise ValueError(
            "installed skill files differ from the Git-managed deployment metadata"
        )
    return metadata


def _git_blob_sha256_compat(value: bytes) -> str:
    header = f"blob {len(value)}\0".encode("ascii")
    return hashlib.sha1(header + value).hexdigest()  # noqa: S324 - Git identity


def _local_blob_inventory(skill_root: pathlib.Path) -> dict[str, str]:
    root = pathlib.Path(skill_root).resolve()
    return {
        path.relative_to(root).as_posix(): _git_blob_sha256_compat(path.read_bytes())
        for path in _package_files(root)
    }


def _run_git(arguments: list[str], cwd: pathlib.Path) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _local_git_commit(skill_root: pathlib.Path) -> str:
    root = pathlib.Path(skill_root).resolve()
    repository = _run_git(["rev-parse", "--show-toplevel"], root)
    if not repository:
        return ""
    repository_root = pathlib.Path(repository)
    try:
        relative_root = root.relative_to(repository_root).as_posix()
    except ValueError:
        return ""
    commit = _run_git(
        ["log", "-1", "--format=%H", "--", relative_root], repository_root
    )
    if not COMMIT_PATTERN.fullmatch(commit):
        return ""
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "-z", commit, "--", relative_root],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if listing.returncode != 0:
        return ""
    expected: dict[str, str] = {}
    prefix = relative_root.rstrip("/") + "/"
    for entry in listing.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        object_sha = metadata.split()[2].decode("ascii")
        repository_path = raw_path.decode("utf-8")
        relative = repository_path.removeprefix(prefix)
        if relative == "SKILL.md" or relative.split("/", 1)[0] in PACKAGE_INPUTS[1:]:
            if not IGNORED_NAMES.intersection(pathlib.PurePosixPath(relative).parts):
                if pathlib.PurePosixPath(relative).suffix not in IGNORED_SUFFIXES:
                    expected[relative] = object_sha
    return commit if expected == _local_blob_inventory(root) else ""


def _resolve_commit(skill_root: pathlib.Path, explicit: str = "") -> tuple[str, str]:
    metadata = _deployment_metadata(skill_root)
    declared = explicit.strip() or os.environ.get(COMMIT_ENVIRONMENT_VARIABLE, "").strip()
    if declared:
        if not COMMIT_PATTERN.fullmatch(declared):
            raise ValueError("skill Git commit must be a full lowercase 40-character SHA")
        if metadata and declared != metadata["skill_commit"]:
            raise ValueError(
                "explicit skill Git commit differs from deployment metadata"
            )
        source = "argument" if explicit.strip() else "environment"
        return declared, source
    if metadata:
        return str(metadata["skill_commit"]), "deployment_metadata"
    local = _local_git_commit(skill_root)
    if local:
        return local, "local_git_content_match"
    raise ValueError(
        "cannot prove which Git commit produced this skill package; deploy the "
        "Git-managed package with deployment metadata or set "
        "LITERATURE_REVIEW_SKILL_GIT_COMMIT to its recorded deployment commit"
    )


def _package_identity(
    skill_root: pathlib.Path,
    *,
    git_commit: str = "",
    known_bundle_sha256: str = "",
) -> dict:
    skill = pathlib.Path(skill_root).resolve()
    commit, commit_source = _resolve_commit(skill, git_commit)
    directory_hash, file_count = directory_sha256(skill)
    bundle_hash = known_bundle_sha256 or bundle_sha256(skill)
    if not HASH_PATTERN.fullmatch(bundle_hash):
        raise ValueError("skill bundle SHA-256 must be a lowercase 64-character hash")
    return {
        "git_commit": commit,
        "git_commit_source": commit_source,
        "skill_directory_sha256": directory_hash,
        "skill_bundle_sha256": bundle_hash,
        "package_inputs": list(PACKAGE_INPUTS),
        "file_count": file_count,
        "entrypoint_version_id": os.environ.get(
            ENTRYPOINT_VERSION_ENVIRONMENT_VARIABLE, ""
        ).strip(),
    }


def _identity(record: dict) -> dict:
    return {key: record.get(key) for key in IDENTITY_KEYS}


def capture(
    run_root: pathlib.Path,
    skill_root: pathlib.Path,
    *,
    git_commit: str = "",
    known_bundle_sha256: str = "",
) -> dict:
    """Write the receipt and mirror its identity into ``run_manifest.json``."""
    run = pathlib.Path(run_root).resolve()
    skill = pathlib.Path(skill_root).resolve()
    identity = _package_identity(
        skill,
        git_commit=git_commit,
        known_bundle_sha256=known_bundle_sha256,
    )
    receipt = run / RECEIPT
    existing = _read_json(receipt)
    if existing:
        if _identity(existing) == _identity(identity):
            return existing
        raise ValueError(
            "skill provenance is immutable after capture; deploy a committed "
            "fix and record an audited upgrade instead of refreshing the receipt"
        )
    record = {
        "schema_version": SCHEMA_VERSION,
        **identity,
        "captured_at": datetime.now(UTC).isoformat(),
    }
    _atomic_json(receipt, record)
    manifest_path = run / "run_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["skill_provenance"] = record
    _atomic_json(manifest_path, manifest)
    return record


def _run_artifact_sha256(path: pathlib.Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        digest = hashlib.sha256()
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            relative = child.relative_to(path).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(hashlib.sha256(child.read_bytes()).digest())
        return digest.hexdigest()
    return ""


def _read_upgrades(run_root: pathlib.Path) -> list[dict]:
    path = pathlib.Path(run_root) / UPGRADE_LEDGER
    if not path.exists():
        return []
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path} line {number} is not a JSON object")
        rows.append(value)
    return rows


def _upgrade_chain_problems(
    run: pathlib.Path, origin: dict, upgrades: list[dict]
) -> list[str]:
    failures: list[str] = []
    previous = _identity(origin)
    for expected_seq, upgrade in enumerate(upgrades, 1):
        if (
            upgrade.get("schema_version") != UPGRADE_SCHEMA_VERSION
            or upgrade.get("seq") != expected_seq
        ):
            failures.append(
                f"skill provenance upgrade {expected_seq} has invalid identity"
            )
            continue
        if upgrade.get("from_identity") != previous:
            failures.append(
                f"skill provenance upgrade {expected_seq} breaks the identity chain"
            )
        target = upgrade.get("to_identity") or {}
        if not COMMIT_PATTERN.fullmatch(str(target.get("git_commit") or "")):
            failures.append(
                f"skill provenance upgrade {expected_seq} lacks a Git commit"
            )
        for key in ("skill_directory_sha256", "skill_bundle_sha256"):
            if not HASH_PATTERN.fullmatch(str(target.get(key) or "")):
                failures.append(
                    f"skill provenance upgrade {expected_seq} has invalid {key}"
                )
        if (
            isinstance(target.get("file_count"), bool)
            or not isinstance(target.get("file_count"), int)
            or target.get("file_count", 0) < 1
        ):
            failures.append(
                f"skill provenance upgrade {expected_seq} has invalid file_count"
            )
        if not str(upgrade.get("reason") or "").strip():
            failures.append(f"skill provenance upgrade {expected_seq} lacks a reason")
        if upgrade.get("resume_from_stage") not in UPGRADE_RESUME_STAGES:
            failures.append(
                f"skill provenance upgrade {expected_seq} has invalid resume stage"
            )
        for relative, expected in (
            upgrade.get("protected_artifact_sha256") or {}
        ).items():
            if _run_artifact_sha256(run / relative) != expected:
                failures.append(
                    f"upgrade-protected artifact changed after upgrade {expected_seq}: "
                    f"{relative}"
                )
        previous = target
    return failures


def record_upgrade(
    run_root: pathlib.Path,
    skill_root: pathlib.Path,
    *,
    git_commit: str = "",
    reason: str,
    resume_from_stage: str,
) -> dict:
    """Append a committed coordinator upgrade without rewriting run origin."""
    run = pathlib.Path(run_root).resolve()
    origin = _read_json(run / RECEIPT)
    if not origin:
        raise ValueError("capture origin provenance before recording an upgrade")
    reason = reason.strip()
    if not reason:
        raise ValueError("upgrade reason must be non-empty")
    if resume_from_stage not in UPGRADE_RESUME_STAGES:
        raise ValueError(
            "resume-from stage must be one of: "
            + ", ".join(sorted(UPGRADE_RESUME_STAGES))
        )
    missing = [
        relative
        for relative in UPGRADE_REQUIRED_ARTIFACTS
        if not (run / relative).exists()
    ]
    if missing:
        raise ValueError(
            "cannot upgrade before scientific artifacts are frozen: "
            + ", ".join(missing)
        )
    upgrades = _read_upgrades(run)
    chain_failures = _upgrade_chain_problems(run, origin, upgrades)
    if chain_failures:
        raise ValueError("invalid existing upgrade chain: " + "; ".join(chain_failures))
    previous = upgrades[-1]["to_identity"] if upgrades else _identity(origin)
    current = _package_identity(skill_root, git_commit=git_commit)
    target = _identity(current)
    if target["git_commit"] == previous["git_commit"]:
        raise ValueError(
            "upgrade requires a newer committed skill; uncommitted self-patching "
            "cannot change run provenance"
        )
    protected = {
        relative: _run_artifact_sha256(run / relative)
        for relative in UPGRADE_PROTECTED_ARTIFACTS
        if (run / relative).exists()
    }
    entry = {
        "schema_version": UPGRADE_SCHEMA_VERSION,
        "seq": len(upgrades) + 1,
        "from_identity": previous,
        "to_identity": target,
        "reason": reason,
        "resume_from_stage": resume_from_stage,
        "protected_artifact_sha256": protected,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    path = run / UPGRADE_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def problems(run_root: pathlib.Path, skill_root: pathlib.Path) -> list[str]:
    run = pathlib.Path(run_root).resolve()
    receipt = _read_json(run / RECEIPT)
    manifest = _read_json(run / "run_manifest.json")
    embedded = manifest.get("skill_provenance") or {}
    failures: list[str] = []
    if not receipt:
        return ["state/skill_provenance.json is missing"]
    if receipt.get("schema_version") != SCHEMA_VERSION:
        failures.append("skill provenance schema version is invalid")
    if not COMMIT_PATTERN.fullmatch(str(receipt.get("git_commit") or "")):
        failures.append("skill provenance lacks a full Git commit")
    for key in ("skill_directory_sha256", "skill_bundle_sha256"):
        if not HASH_PATTERN.fullmatch(str(receipt.get(key) or "")):
            failures.append(f"skill provenance {key} is invalid")
        if embedded.get(key) != receipt.get(key):
            failures.append(f"run_manifest skill provenance {key} differs from receipt")
    if embedded.get("git_commit") != receipt.get("git_commit"):
        failures.append("run_manifest skill provenance Git commit differs from receipt")
    try:
        upgrades = _read_upgrades(run)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        upgrades = []
        failures.append(f"skill provenance upgrade ledger is invalid: {exc}")
    failures.extend(_upgrade_chain_problems(run, receipt, upgrades))
    effective = upgrades[-1]["to_identity"] if upgrades else _identity(receipt)
    try:
        deployment = _deployment_metadata(skill_root)
    except ValueError as exc:
        deployment = {}
        failures.append(str(exc))
    if deployment:
        if deployment.get("skill_commit") != effective.get("git_commit"):
            failures.append(
                "effective skill Git commit differs from deployment metadata"
            )
        if deployment.get("skill_directory_sha256") != effective.get(
            "skill_directory_sha256"
        ):
            failures.append(
                "effective skill package hash differs from deployment metadata"
            )
    actual_hash, actual_count = directory_sha256(skill_root)
    if actual_hash != effective.get("skill_directory_sha256"):
        failures.append("installed skill files changed after provenance capture")
    if actual_count != effective.get("file_count"):
        failures.append("installed skill file inventory changed after provenance capture")
    return failures


def _read_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _atomic_json(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=pathlib.Path)
    parser.add_argument(
        "--skill-root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parent.parent,
    )
    parser.add_argument("--git-commit", default="")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--record-upgrade", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument(
        "--resume-from-stage",
        choices=sorted(UPGRADE_RESUME_STAGES),
        default="",
    )
    args = parser.parse_args()
    if args.verify and args.record_upgrade:
        parser.error("--verify and --record-upgrade are mutually exclusive")
    if args.verify:
        failures = problems(args.run_root, args.skill_root)
        for failure in failures:
            print(f"FAIL: {failure}")
        print(f"SKILL-PROVENANCE: failures={len(failures)}")
        return 1 if failures else 0
    if args.record_upgrade:
        if not args.reason or not args.resume_from_stage:
            parser.error("--record-upgrade requires --reason and --resume-from-stage")
        entry = record_upgrade(
            args.run_root,
            args.skill_root,
            git_commit=args.git_commit,
            reason=args.reason,
            resume_from_stage=args.resume_from_stage,
        )
        print(
            "SKILL-UPGRADE: "
            f"seq={entry['seq']} "
            f"commit={entry['to_identity']['git_commit']} "
            f"resume_from={entry['resume_from_stage']}"
        )
        return 0
    record = capture(
        args.run_root,
        args.skill_root,
        git_commit=args.git_commit,
    )
    print(
        "SKILL-PROVENANCE: "
        f"commit={record['git_commit']} "
        f"directory_sha256={record['skill_directory_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
