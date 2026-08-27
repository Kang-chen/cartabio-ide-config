#!/usr/bin/env python3
"""Immutable bundle exchange for object-store-backed filesystem mounts.

The shared mount is a courier, not a working filesystem: writers create each
object once and publish a completion marker last.  Compute, extraction, and
atomic replacement remain on machine-local POSIX storage.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import tarfile
from collections.abc import Iterable, Mapping


BUNDLE_NAME = "result.tar"
MANIFEST_NAME = "manifest.json"
COMPLETION_NAME = "DONE.json"
COPY_CHUNK_BYTES = 1024 * 1024
SCHEMA_VERSION = 1
IGNORED_BUNDLE_NAMES = {".DS_Store", ".pytest_cache", "__pycache__"}
IGNORED_BUNDLE_SUFFIXES = {".pyc", ".pyo"}


class ObjectExchangeError(ValueError):
    """An immutable publication is missing, conflicting, or corrupt."""


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def publish_bytes(path: pathlib.Path, value: bytes) -> str:
    """Create one shared object without rename or overwrite."""
    expected = sha256_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(value)
    except FileExistsError:
        actual = sha256_file(path)
        if actual != expected:
            raise ObjectExchangeError(
                f"immutable object already exists with different content: {path}"
            )
        return expected
    actual = sha256_file(path)
    if actual != expected:
        raise ObjectExchangeError(
            f"object-store write verification failed for {path}: "
            f"expected {expected}, got {actual}"
        )
    return expected


def publish_json(path: pathlib.Path, value: object) -> str:
    return publish_bytes(path, json_bytes(value))


def publish_file(source: pathlib.Path, destination: pathlib.Path) -> str:
    """Stream one local file to an immutable shared object and verify it."""
    expected = sha256_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=COPY_CHUNK_BYTES)
    except FileExistsError:
        actual = sha256_file(destination)
        if actual != expected:
            raise ObjectExchangeError(
                "immutable object already exists with different content: "
                f"{destination}"
            )
        return expected
    actual = sha256_file(destination)
    if actual != expected:
        raise ObjectExchangeError(
            f"object-store copy verification failed for {destination}: "
            f"expected {expected}, got {actual}"
        )
    return expected


def _normalized_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def _bundle_paths(
    source_root: pathlib.Path,
    include_names: Iterable[str],
) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for name in include_names:
        source = source_root / name
        if not source.exists():
            raise ObjectExchangeError(f"bundle input is missing: {source}")
        paths.append(source)
        if source.is_dir():
            paths.extend(sorted(source.rglob("*")))
    included = {
        path for path in paths
        if not IGNORED_BUNDLE_NAMES.intersection(path.parts)
        and path.suffix not in IGNORED_BUNDLE_SUFFIXES
    }
    return sorted(
        included,
        key=lambda path: path.relative_to(source_root).as_posix(),
    )


def create_bundle(
    source_root: pathlib.Path,
    bundle_path: pathlib.Path,
    include_names: Iterable[str],
) -> str:
    """Create a deterministic uncompressed tar on local POSIX storage."""
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for source in _bundle_paths(source_root, include_names):
            if source.is_symlink():
                raise ObjectExchangeError(f"bundle input may not be a symlink: {source}")
            relative = source.relative_to(source_root).as_posix()
            info = _normalized_tar_info(archive.gettarinfo(str(source), relative))
            if info.isfile():
                with source.open("rb") as handle:
                    archive.addfile(info, handle)
            elif info.isdir():
                archive.addfile(info)
            else:
                raise ObjectExchangeError(
                    f"bundle input must be a regular file or directory: {source}"
                )
    return sha256_file(bundle_path)


def publish_directory(
    source_root: pathlib.Path,
    publication_root: pathlib.Path,
    local_bundle_path: pathlib.Path,
    include_names: Iterable[str],
    metadata: Mapping[str, object],
) -> dict:
    """Publish bundle, manifest, then DONE marker to a unique prefix."""
    bundle_sha256 = create_bundle(source_root, local_bundle_path, include_names)
    bundle_size = local_bundle_path.stat().st_size
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle": BUNDLE_NAME,
        "bundle_sha256": bundle_sha256,
        "bundle_size": bundle_size,
        "metadata": dict(metadata),
    }
    manifest_value = json_bytes(manifest)
    publish_file(local_bundle_path, publication_root / BUNDLE_NAME)
    publish_bytes(publication_root / MANIFEST_NAME, manifest_value)
    publish_json(
        publication_root / COMPLETION_NAME,
        {
            "schema_version": SCHEMA_VERSION,
            "manifest_sha256": sha256_bytes(manifest_value),
        },
    )
    return manifest


def read_publication(publication_root: pathlib.Path) -> dict:
    """Validate a completed publication without extracting it."""
    completion_path = publication_root / COMPLETION_NAME
    if not completion_path.exists():
        raise ObjectExchangeError(f"publication is incomplete: {completion_path}")
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    manifest_path = publication_root / MANIFEST_NAME
    if not manifest_path.exists():
        raise ObjectExchangeError(f"publication manifest is missing: {manifest_path}")
    manifest_value = manifest_path.read_bytes()
    if sha256_bytes(manifest_value) != completion.get("manifest_sha256"):
        raise ObjectExchangeError(f"publication manifest is corrupt: {manifest_path}")
    manifest = json.loads(manifest_value)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("bundle") != BUNDLE_NAME
    ):
        raise ObjectExchangeError(
            f"publication manifest has an invalid contract: {manifest_path}"
        )
    bundle_path = publication_root / BUNDLE_NAME
    if not bundle_path.is_file():
        raise ObjectExchangeError(f"publication bundle is missing: {bundle_path}")
    if bundle_path.stat().st_size != int(manifest.get("bundle_size") or -1):
        raise ObjectExchangeError(f"publication bundle size mismatch: {bundle_path}")
    if sha256_file(bundle_path) != manifest.get("bundle_sha256"):
        raise ObjectExchangeError(f"publication bundle checksum mismatch: {bundle_path}")
    return manifest


def _safe_extract(bundle_path: pathlib.Path, destination: pathlib.Path) -> None:
    with tarfile.open(bundle_path, mode="r") as archive:
        for member in archive.getmembers():
            relative = pathlib.PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ObjectExchangeError(
                    f"bundle contains an unsafe path: {member.name!r}"
                )
            if not (member.isfile() or member.isdir()):
                raise ObjectExchangeError(
                    f"bundle contains an unsupported entry: {member.name!r}"
                )
        archive.extractall(destination)


def materialize_directory(
    publication_root: pathlib.Path,
    destination: pathlib.Path,
) -> dict:
    """Verify a shared publication, then extract it to local POSIX storage."""
    manifest = read_publication(publication_root)
    receipt_path = destination / ".materialized.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("bundle_sha256") == manifest["bundle_sha256"]:
            return manifest
        raise ObjectExchangeError(
            f"local materialization conflicts with publication: {destination}"
        )
    if destination.exists() and any(destination.iterdir()):
        raise ObjectExchangeError(
            f"local materialization is incomplete or unverified: {destination}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    local_bundle = destination.parent / (
        f".{destination.name}.{manifest['bundle_sha256'][:12]}.tar"
    )
    shutil.copyfile(publication_root / BUNDLE_NAME, local_bundle)
    if sha256_file(local_bundle) != manifest["bundle_sha256"]:
        raise ObjectExchangeError(
            f"local bundle copy checksum mismatch: {publication_root / BUNDLE_NAME}"
        )
    _safe_extract(local_bundle, destination)
    temporary = receipt_path.with_name(f".{receipt_path.name}.tmp")
    temporary.write_bytes(json_bytes({
        "schema_version": SCHEMA_VERSION,
        "bundle_sha256": manifest["bundle_sha256"],
    }))
    temporary.replace(receipt_path)
    return manifest
