#!/usr/bin/env python3
"""Seed a review with full text already retrieved by a supplied prior run."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil

from corpus_ledger import identity_tokens, paper_id, read_rows


OVERRIDES_PATH = pathlib.Path("corpus/prior_fulltext_overrides.jsonl")
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_FILE_FIELDS = {
    "local_pdf": ".pdf",
    "local_xml": ".xml",
    "figures_pdf": ".figures.pdf",
}
_METADATA_FIELDS = (
    "access", "access_state", "license", "oa_source", "oa_full_url",
    "figure_embedding_allowed", "reuse_rights",
)


def _safe(value: str) -> str:
    return _SAFE_RE.sub("_", value).strip("._") or "paper"


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _enrich(rows: list[dict], references: dict[str, dict]) -> list[dict]:
    return [{**references.get(paper_id(row), {}), **row} for row in rows]


def _source_path(value: object, prior_run: pathlib.Path) -> pathlib.Path | None:
    if not str(value or "").strip():
        return None
    path = pathlib.Path(str(value))
    return path if path.is_absolute() else prior_run / path


def seed(current_run: pathlib.Path, prior_run: pathlib.Path,
         selected_path: pathlib.Path) -> list[dict]:
    """Copy identity-matched positive full text and return record overrides."""
    current_run = current_run.resolve()
    prior_run = prior_run.resolve()
    current_refs = {
        paper_id(row): row
        for row in read_rows(current_run / "corpus" / "references.jsonl")
    }
    prior_refs = {
        paper_id(row): row
        for row in read_rows(prior_run / "corpus" / "references.jsonl")
    }
    selected = _enrich(read_rows(selected_path), current_refs)
    prior_papers = _enrich(
        read_rows(prior_run / "fulltext" / "papers.jsonl"), prior_refs
    )
    prior_by_identity: dict[str, list[dict]] = {}
    for row in prior_papers:
        sources = [_source_path(row.get(field), prior_run)
                   for field in _FILE_FIELDS]
        if not any(source is not None and source.is_file()
                   for source in sources):
            continue
        for token in identity_tokens(row):
            prior_by_identity.setdefault(token, []).append(row)

    destination = current_run / "fulltext" / "prior_reuse"
    destination.mkdir(parents=True, exist_ok=True)
    overrides: list[dict] = []
    for current in selected:
        matches: dict[str, dict] = {}
        for token in identity_tokens(current):
            for prior in prior_by_identity.get(token, []):
                matches[paper_id(prior)] = prior
        if not matches:
            continue
        prior = matches[sorted(matches)[0]]
        pid = paper_id(current)
        override = {
            "paper_id": pid,
            "reused_from_run": str(prior_run),
            "reused_from_paper_id": paper_id(prior),
        }
        for field, suffix in _FILE_FIELDS.items():
            source = _source_path(prior.get(field), prior_run)
            if source is None or not source.is_file():
                continue
            target = destination / f"{_safe(pid)}{suffix}"
            shutil.copy2(source, target)
            override[field] = str(target)
        for field in _METADATA_FIELDS:
            if field in prior:
                override[field] = prior[field]
        overrides.append(override)

    _write_jsonl(current_run / OVERRIDES_PATH, overrides)
    return overrides


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=pathlib.Path)
    parser.add_argument("--prior-run", required=True, type=pathlib.Path)
    parser.add_argument("--selected", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    overrides = seed(args.run_root, args.prior_run, args.selected)
    print(json.dumps({"reused_fulltexts": len(overrides)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
