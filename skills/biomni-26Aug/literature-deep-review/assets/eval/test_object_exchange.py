from __future__ import annotations

import pathlib

import pytest

from object_exchange import (
    COMPLETION_NAME,
    BUNDLE_NAME,
    ObjectExchangeError,
    materialize_directory,
    publish_bytes,
    publish_directory,
    read_publication,
)


def _source(tmp_path: pathlib.Path) -> pathlib.Path:
    source = tmp_path / "source"
    (source / "fulltext" / "parsed").mkdir(parents=True)
    (source / "fulltext" / "parsed" / "p1.json").write_text(
        '{"paper_id":"p1"}\n', encoding="utf-8"
    )
    (source / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    return source


def test_publication_never_requires_atomic_replace(tmp_path, monkeypatch):
    source = _source(tmp_path)
    publication = tmp_path / "shared" / "attempt-1"

    def reject_replace(*_args, **_kwargs):
        raise OSError("object mount does not support rename")

    monkeypatch.setattr(pathlib.Path, "replace", reject_replace)
    manifest = publish_directory(
        source,
        publication,
        tmp_path / "local" / "result.tar",
        ("fulltext", "run_manifest.json"),
        {"task_id": "task-1"},
    )

    assert (publication / COMPLETION_NAME).is_file()
    assert read_publication(publication) == manifest


def test_materialize_requires_done_marker(tmp_path):
    source = _source(tmp_path)
    publication = tmp_path / "shared" / "attempt-1"
    publish_directory(
        source,
        publication,
        tmp_path / "local" / "result.tar",
        ("fulltext", "run_manifest.json"),
        {"task_id": "task-1"},
    )
    (publication / COMPLETION_NAME).unlink()

    with pytest.raises(ObjectExchangeError, match="publication is incomplete"):
        materialize_directory(publication, tmp_path / "materialized")


def test_materialize_rejects_corrupt_bundle(tmp_path):
    source = _source(tmp_path)
    publication = tmp_path / "shared" / "attempt-1"
    publish_directory(
        source,
        publication,
        tmp_path / "local" / "result.tar",
        ("fulltext", "run_manifest.json"),
        {"task_id": "task-1"},
    )
    (publication / BUNDLE_NAME).write_bytes(b"corrupt")

    with pytest.raises(ObjectExchangeError, match="bundle size mismatch"):
        materialize_directory(publication, tmp_path / "materialized")


def test_materialize_verifies_and_extracts_locally(tmp_path):
    source = _source(tmp_path)
    publication = tmp_path / "shared" / "attempt-1"
    publish_directory(
        source,
        publication,
        tmp_path / "local" / "result.tar",
        ("fulltext", "run_manifest.json"),
        {"task_id": "task-1"},
    )

    manifest = materialize_directory(publication, tmp_path / "materialized")

    assert manifest["metadata"] == {"task_id": "task-1"}
    assert (
        tmp_path / "materialized" / "fulltext" / "parsed" / "p1.json"
    ).is_file()
    assert (tmp_path / "materialized" / "run_manifest.json").is_file()


def test_write_once_is_idempotent_but_rejects_conflicts(tmp_path):
    destination = tmp_path / "shared" / "READY.json"
    publish_bytes(destination, b"same")
    publish_bytes(destination, b"same")

    with pytest.raises(ObjectExchangeError, match="different content"):
        publish_bytes(destination, b"different")
