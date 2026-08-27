"""Stable adjudication identifiers and end-to-end disposition checks."""
from __future__ import annotations

import hashlib
import json


FINAL_DISPOSITIONS = frozenset({"accepted", "rejected", "duplicate"})


def adjudication_id(row: dict, ordinal: int) -> str:
    """Identify one deterministic occurrence, including repeated raw rows."""
    payload = json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(f"{ordinal}\0{payload}".encode("utf-8")).hexdigest()[:20]
    return f"A-{digest}"


def accepted(
    adjudication_id_value: str,
    raw: dict,
    evidence_id: str,
) -> dict:
    return {
        "schema_version": 1,
        "adjudication_id": adjudication_id_value,
        "disposition": "accepted",
        "evidence_id": evidence_id,
        "claim_id": str(raw.get("claim_id") or ""),
        "paper_id": str(raw.get("paper_id") or ""),
        "block_id": str(raw.get("block_id") or ""),
    }


def rejected(adjudication_id_value: str, raw: dict, reason: str) -> dict:
    return {
        "schema_version": 1,
        "adjudication_id": adjudication_id_value,
        "disposition": "rejected",
        "claim_id": str(raw.get("claim_id") or ""),
        "paper_id": str(raw.get("paper_id") or ""),
        "block_id": str(raw.get("block_id") or ""),
        "reason": str(reason or "validation failed"),
    }


def duplicate(
    adjudication_id_value: str,
    raw: dict,
    evidence_id: str,
    first_adjudication_id: str,
) -> dict:
    return {
        "schema_version": 1,
        "adjudication_id": adjudication_id_value,
        "disposition": "duplicate",
        "evidence_id": evidence_id,
        "duplicate_of_adjudication_id": first_adjudication_id,
        "claim_id": str(raw.get("claim_id") or ""),
        "paper_id": str(raw.get("paper_id") or ""),
        "block_id": str(raw.get("block_id") or ""),
    }


def problems(
    adjudications: list[dict],
    evidence: list[dict],
    lineage: list[dict],
) -> list[str]:
    failures: list[str] = []
    if len(adjudications) != len(lineage):
        failures.append(
            "adjudication lineage row count differs from raw adjudications "
            f"({len(lineage)} != {len(adjudications)})"
        )
    lineage_ids = [str(row.get("adjudication_id") or "") for row in lineage]
    if not all(lineage_ids) or len(lineage_ids) != len(set(lineage_ids)):
        failures.append("adjudication lineage IDs are missing or duplicated")
    invalid = sorted({
        str(row.get("disposition") or "") for row in lineage
        if str(row.get("disposition") or "") not in FINAL_DISPOSITIONS
    })
    if invalid:
        failures.append("invalid adjudication dispositions: " + ", ".join(invalid))
    for ordinal, (raw, disposition) in enumerate(
        zip(adjudications, lineage), 1
    ):
        expected_id = adjudication_id(raw, ordinal)
        if disposition.get("adjudication_id") != expected_id:
            failures.append(
                f"adjudication lineage row {ordinal} does not match its raw decision"
            )
            continue
        for key in ("claim_id", "paper_id", "block_id"):
            if str(disposition.get(key) or "") != str(raw.get(key) or ""):
                failures.append(
                    f"{expected_id}: lineage {key} differs from raw adjudication"
                )
    accepted_ids = {
        str(row.get("evidence_id") or "") for row in lineage
        if row.get("disposition") == "accepted"
    }
    evidence_ids = {str(row.get("evidence_id") or "") for row in evidence}
    if accepted_ids != evidence_ids:
        failures.append(
            "accepted adjudication lineage differs from final evidence "
            f"({len(accepted_ids)} != {len(evidence_ids)})"
        )
    for row in lineage:
        if row.get("disposition") == "rejected" and not str(
            row.get("reason") or ""
        ).strip():
            failures.append(
                f"{row.get('adjudication_id')}: rejected without a typed reason"
            )
        if row.get("disposition") == "duplicate" and not row.get(
            "duplicate_of_adjudication_id"
        ):
            failures.append(
                f"{row.get('adjudication_id')}: duplicate lacks original lineage ID"
            )
    return failures
