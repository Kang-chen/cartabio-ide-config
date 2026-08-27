#!/usr/bin/env python3
"""Artifact I/O and input normalization for the evidence pipeline."""
from __future__ import annotations

import csv
import json
import pathlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def doi_url(value: str | None) -> str | None:
    """Return a resolvable DOI URL for a DOI-like string."""
    if not value:
        return None
    normalized = str(value).strip()
    if normalized.lower().startswith(("http://", "https://")):
        return normalized
    if normalized.lower().startswith("doi:"):
        normalized = normalized[4:].strip()
    if normalized.startswith("10.") and "/" in normalized:
        return "https://doi.org/" + normalized
    return None


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    temp.replace(path)


def write_jsonl(path: pathlib.Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temp.replace(path)


def read_jsonl(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def load_claims(path: pathlib.Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif suffix in {".json", ".jsonl"}:
        value = json.loads(path.read_text()) if suffix == ".json" else read_jsonl(path)
        rows = value if isinstance(value, list) else value.get("claims", [])
    else:
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip().lstrip("-* ").strip()
            if not text or text.startswith("#"):
                continue
            match = re.match(r"([A-Za-z][A-Za-z0-9_-]{0,30})\s*[:|]\s*(.+)", text)
            rows.append({
                "claim_id": match.group(1) if match else f"C-{len(rows)+1:03d}",
                "claim_text": match.group(2) if match else text,
            })
    claims: list[dict] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        claim_id = str(row.get("claim_id") or f"C-{index:03d}").strip()
        text = str(row.get("claim_text") or row.get("claim") or "").strip()
        if not text or claim_id in seen:
            continue
        seen.add(claim_id)
        claims.append({
            "claim_id": claim_id,
            "claim_text": text,
            "cluster": str(row.get("cluster") or "").strip(),
            "query_terms": str(row.get("query_terms") or "").strip(),
            "scope": str(row.get("scope") or row.get("claim_scope") or "").strip(),
            "parent_claim_id": str(row.get("parent_claim_id") or "").strip(),
            "revision_reason": str(row.get("revision_reason") or "").strip(),
        })
    if not claims:
        raise ValueError(f"no claims found in {path}")
    return claims


def load_records(path: pathlib.Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif suffix == ".jsonl":
        rows = read_jsonl(path)
    else:
        value = json.loads(path.read_text())
        rows = value if isinstance(value, list) else value.get("records", [])
    normalized: list[dict] = []
    for index, value in enumerate(rows, 1):
        row = dict(value)
        paper_id = row.get("paper_id") or row.get("pmcid") or row.get("pmid") or row.get("doi")
        row["paper_id"] = str(paper_id or f"P-{index:04d}").strip()
        for key in ("doi", "pmid", "pmcid", "title", "journal", "authors", "year"):
            if row.get(key) == "":
                row[key] = None
        normalized.append(row)
    return dedupe_records(normalized)


def dedupe_records(records: list[dict]) -> list[dict]:
    seen = {key: set() for key in ("doi", "pmid", "pmcid", "title", "paper_id")}
    output: list[dict] = []
    for row in records:
        identifiers = {
            "doi": str(row.get("doi") or "").strip().lower(),
            "pmid": str(row.get("pmid") or "").strip().lower(),
            "pmcid": str(row.get("pmcid") or "").strip().lower(),
            "title": re.sub(r"[^a-z0-9]+", " ", str(row.get("title") or "").lower()).strip(),
            "paper_id": str(row.get("paper_id") or "").strip().lower(),
        }
        if any(value and value in seen[kind] for kind, value in identifiers.items()):
            continue
        for kind, value in identifiers.items():
            if value:
                seen[kind].add(value)
        output.append(row)
    return output


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:120]


def access_label(record: dict, result: dict) -> str:
    """Preserve explicit acquisition provenance; absent classification is unknown."""
    if result.get("user_supplied"):
        return "user_supplied"
    return {
        "oa_licensed": "oa_licensed",
        "free_to_read": "free_to_read",
        "licensed_copy": "licensed_copy",
    }.get(str(record.get("access_state") or ""), "unknown")


def write_csv(
    path: pathlib.Path, rows: list[dict], columns: list[str] | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = columns or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            cooked = {
                key: json.dumps(row.get(key, ""), ensure_ascii=False)
                if isinstance(row.get(key, ""), (list, dict))
                else row.get(key, "")
                for key in fieldnames
            }
            writer.writerow(cooked)
