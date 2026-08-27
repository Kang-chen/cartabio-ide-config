#!/usr/bin/env python3
"""Reconcile final report counters against canonical run artifacts.

The manifest is an execution log, not a source of truth.  Long Biomni runs may
span managed machines and context compactions, so final counters are rebuilt
from the durable corpus, evidence, verification, and figure artifacts before
either renderer runs.  Verification then fails if any delivered table lost a
row or any selected figure disappeared without an explicit disposition.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
from collections import Counter
from datetime import datetime

import evidence_lineage
import support_policy
from intake_policy import figure_intake_errors
from managed_machine_shards import ABSOLUTE_MAX_MACHINES, expected_machine_count
from skill_provenance import problems as skill_provenance_problems


RECEIPT = pathlib.Path("state/final_reconciliation.json")
EXPLICIT_FIGURE_DISPOSITIONS = frozenset({
    "exported",
    "reuse_not_permitted",
    "image_unavailable",
    "image_missing",
    "export_failed",
})
MANAGED_EXECUTION_MIN_PAPERS = 12
VALID_OCR_MODES = frozenset({"off", "targeted", "all"})
CANONICAL_METRICS = (
    "papers_discovered",
    "papers_in_scope",
    "papers_selected",
    "papers_full_text",
    "papers_not_retrieved",
    "papers_cited",
    "claims_total",
    "claims_grounded",
    "evidence_accepted",
    "evidence_grounding",
    "evidence_mentions",
    "entailment_verified",
    "figures_exported",
    "cited_papers_with_figures",
    "figure_images",
    "figure_ocr_attempted",
    "figure_ocr_completed",
    "figure_caption_inherited",
    "figure_caption_missing",
)
ASSEMBLY_DESTINATIONS = {
    "adjudications": "evidence/adjudications.jsonl",
    "entailment": "evidence/entailment.jsonl",
    "narratives": "deliverables/claim_narratives.jsonl",
}


def _managed_execution_waiver(config: dict) -> str:
    waiver = config.get("managed_execution_waiver") or {}
    if not isinstance(waiver, dict) or waiver.get("approved_by_user") is not True:
        return ""
    return str(waiver.get("reason") or "").strip()


def _json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _jsonl(path: pathlib.Path) -> list[dict]:
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


def _csv(path: pathlib.Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _atomic_json(path: pathlib.Path, value: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if path.exists() and path.read_bytes() == payload:
        return False
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return True


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _ids(rows: list[dict], key: str) -> list[str]:
    return [str(row.get(key) or "").strip() for row in rows if row.get(key)]


def _elapsed_seconds(manifest: dict) -> float | None:
    starts = (
        manifest.get("run_started_utc"),
        manifest.get("created_at"),
        manifest.get("started_utc"),
    )
    ends = (manifest.get("updated_at"), manifest.get("completed_at"))
    start = next((str(value) for value in starts if value), "")
    end = next((str(value) for value in ends if value), "")
    if not start or not end:
        return None
    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        last = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round(max(0.0, (last - first).total_seconds()), 3)


def _claim_disposition(root: pathlib.Path) -> dict:
    retained = _ids(_jsonl(root / "corpus" / "claims.jsonl"), "claim_id")
    numbered = sorted({
        int(match.group(1))
        for claim_id in retained
        if (match := re.fullmatch(r"C-(\d+)", claim_id))
    })
    reserved = []
    if numbered:
        present = set(numbered)
        reserved = [
            f"C-{value:03d}"
            for value in range(numbered[0], numbered[-1] + 1)
            if value not in present
        ]
    return {
        "retained": retained,
        "reserved_not_reassigned": reserved,
        "policy": "canonical claim IDs are immutable across all artifacts",
    }


def _assembly_failures(root: pathlib.Path, *, require_native: bool,
                       mode: str) -> list[str]:
    """Validate native task receipts against the files being delivered."""
    failures: list[str] = []
    receipts_dir = root / "state" / "assemblies"
    required = {"entailment"} if require_native else set()
    if require_native and mode in {"deep", "broad"}:
        required.add("narratives")
    if require_native and any(
        (root / "evidence" / "adjudication_batches").glob("*.json")
    ):
        required.add("adjudications")
    for kind, expected_destination in ASSEMBLY_DESTINATIONS.items():
        path = receipts_dir / f"{kind}.json"
        if not path.exists():
            if kind in required:
                failures.append(f"native {kind} assembly receipt is missing")
            continue
        receipt = _json(path)
        if receipt.get("complete") is not True:
            failures.append(f"native {kind} assembly receipt is incomplete")
        task_count = int(receipt.get("task_count") or 0)
        output_count = int(receipt.get("output_count") or 0)
        if task_count != output_count:
            failures.append(
                f"native {kind} assembly has {task_count} tasks but "
                f"{output_count} outputs"
            )
        task_hashes = receipt.get("task_sha256") or {}
        output_hashes = receipt.get("output_sha256") or {}
        if len(task_hashes) != task_count:
            failures.append(f"native {kind} assembly task hash inventory is incomplete")
        if len(output_hashes) != output_count:
            failures.append(f"native {kind} assembly output hash inventory is incomplete")
        destination = str(receipt.get("destination") or "")
        if destination != expected_destination:
            failures.append(
                f"native {kind} assembly destination is {destination!r}, "
                f"expected {expected_destination!r}"
            )
            continue
        destination_path = root / destination
        if not destination_path.exists():
            failures.append(f"native {kind} assembly destination is missing")
        elif receipt.get("destination_sha256") != _sha256(destination_path):
            failures.append(
                f"native {kind} assembly destination hash is stale; the "
                "delivered file changed after deterministic assembly"
            )
    return failures


def _canonical_counts(root: pathlib.Path) -> dict[str, int]:
    ledger = _json(root / "corpus" / "corpus_ledger.json")
    ledger_counts = ledger.get("counts") or {}
    claims = _jsonl(root / "corpus" / "claims.jsonl")
    evidence = _jsonl(root / "evidence" / "evidence.jsonl")
    entailment = _jsonl(root / "evidence" / "entailment.jsonl")
    misses = _jsonl(root / "fulltext" / "not_retrieved.jsonl")
    matrix = _csv(root / "deliverables" / "claim_evidence_matrix.csv")
    figures = _json(
        root / "deliverables" / "figures_cited" / "figures_manifest.json"
    ).get("figures") or []
    exported = [row for row in figures if row.get("status") == "exported"]
    parsed_figures = []
    for path in sorted((root / "fulltext" / "parsed").glob("*.json")):
        parsed_figures.extend(_json(path).get("figures") or [])
    image_figures = [row for row in parsed_figures if row.get("image_path")]
    grounding = [
        row for row in evidence
        if row.get("stance") in {"supports", "contradicts"}
        and row.get("evidence_kind") != "inferred"
    ]
    return {
        "papers_discovered": int(ledger_counts.get("discovered") or 0),
        "papers_in_scope": int(ledger_counts.get("in_scope") or 0),
        "papers_selected": int(ledger_counts.get("selected") or 0),
        "papers_full_text": int(ledger_counts.get("retrieved") or 0),
        "papers_not_retrieved": len(misses),
        "papers_cited": int(ledger_counts.get("cited") or len({
            str(row.get("paper_id") or "") for row in grounding if row.get("paper_id")
        })),
        "claims_total": len(claims),
        "claims_grounded": support_policy.count_grounded(
            row.get("support_state") for row in matrix
        ),
        "evidence_accepted": len(evidence),
        "evidence_grounding": len(grounding),
        "evidence_mentions": sum(row.get("stance") == "mentions" for row in evidence),
        "entailment_verified": len(entailment),
        "figures_exported": len(exported),
        "cited_papers_with_figures": len({
            str(row.get("paper_id") or "") for row in exported if row.get("paper_id")
        }),
        "figure_images": len(image_figures),
        "figure_ocr_attempted": sum(
            bool(row.get("ocr_attempted") or row.get("ocr"))
            for row in image_figures
        ),
        "figure_ocr_completed": sum(
            str(row.get("ocr_status") or "") in {"completed", "empty"}
            or bool(row.get("ocr"))
            for row in image_figures
        ),
        "figure_caption_inherited": sum(
            str(row.get("caption_source") or "") == "parent_figure_same_page"
            for row in image_figures
        ),
        "figure_caption_missing": sum(
            not str(row.get("caption") or "").strip() for row in image_figures
        ),
    }


def _cross_artifact_failures(root: pathlib.Path, counts: dict[str, int]) -> list[str]:
    failures: list[str] = []
    parsed = {
        str(_json(path).get("paper_id") or path.stem): _json(path)
        for path in sorted((root / "fulltext" / "parsed").glob("*.json"))
    }
    parse_quality = {
        str(row.get("paper_id") or ""): row
        for row in _jsonl(root / "fulltext" / "parse_quality.jsonl")
    }
    if parsed and not parse_quality:
        failures.append("fulltext/parse_quality.jsonl is missing for parsed papers")
    missing_quality = sorted(set(parsed) - set(parse_quality))
    if missing_quality:
        failures.append(
            "parsed papers lack parse-quality receipts: "
            + ", ".join(missing_quality[:8])
        )
    for pid, receipt in parse_quality.items():
        state = str(receipt.get("state") or "")
        substantive = int(receipt.get("substantive_sentence_count") or 0)
        figures = int(receipt.get("nonempty_figure_count") or 0)
        if state == "usable" and substantive < 1:
            failures.append(f"{pid}: usable parse has zero substantive sentences")
        elif state == "figure_only" and (substantive or figures < 1):
            failures.append(f"{pid}: invalid figure_only parse-quality receipt")
        elif state == "unusable":
            failures.append(
                f"{pid}: retrieved source is unusable after parse recovery"
            )
    claims = _jsonl(root / "corpus" / "claims.jsonl")
    evidence = _jsonl(root / "evidence" / "evidence.jsonl")
    adjudications_path = root / "evidence" / "adjudications.jsonl"
    if not adjudications_path.exists():
        failures.append("evidence/adjudications.jsonl is missing")
    else:
        adjudications = _jsonl(adjudications_path)
        lineage_path = root / "evidence" / "evidence_lineage.jsonl"
        lineage = _jsonl(lineage_path)
        if adjudications and not lineage_path.exists():
            failures.append("evidence/evidence_lineage.jsonl is missing")
        failures.extend(evidence_lineage.problems(adjudications, evidence, lineage))
        rejected_rows = _jsonl(root / "evidence" / "rejected_evidence.jsonl")
        rejected_by_id = {
            str(row.get("adjudication_id") or ""): row for row in rejected_rows
        }
        expected_rejected = {
            str(row.get("adjudication_id") or ""): row for row in lineage
            if row.get("disposition") == "rejected"
        }
        if set(rejected_by_id) != set(expected_rejected):
            failures.append(
                "rejected_evidence.jsonl differs from rejected lineage dispositions"
            )
        raw_by_id = {
            evidence_lineage.adjudication_id(raw, ordinal): raw
            for ordinal, raw in enumerate(adjudications, 1)
        }
        for adjudication_id, disposition in expected_rejected.items():
            ledger = rejected_by_id.get(adjudication_id) or {}
            if ledger.get("row") != raw_by_id.get(adjudication_id):
                failures.append(
                    f"{adjudication_id}: rejected ledger does not preserve raw decision"
                )
            if str(ledger.get("reason") or "") != str(
                disposition.get("reason") or ""
            ):
                failures.append(
                    f"{adjudication_id}: rejected ledger reason differs from lineage"
                )
        audited_tasks = [
            _json(path) for path in sorted(
                (root / "evidence" / "adjudication_batches").glob("*.json")
            )
            if _json(path).get("audit_required")
        ]
        if audited_tasks:
            audit_rows = _jsonl(root / "evidence" / "adjudication_audit.jsonl")
            audited_ids = {str(row.get("batch_id") or "") for row in audit_rows}
            expected_ids = {
                str(row.get("batch_id") or row.get("task_id") or "")
                for row in audited_tasks
            }
            if audited_ids != expected_ids:
                failures.append(
                    "adjudication_audit.jsonl does not cover every audit-required batch"
                )
            for row in audit_rows:
                reviewed = int(row.get("candidate_blocks_reviewed") or 0)
                accepted = int(row.get("accepted_blocks") or 0)
                rejected = int(row.get("rejected_blocks") or 0)
                reasons = row.get("rejection_reasons") or {}
                if accepted + rejected != reviewed or sum(
                    int(value) for value in reasons.values()
                ) != rejected:
                    failures.append(
                        f"adjudication audit {row.get('batch_id')} does not reconcile"
                    )
    evidence_table = _csv(root / "deliverables" / "evidence_table.csv")
    matrix = _csv(root / "deliverables" / "claim_evidence_matrix.csv")
    entailment = _jsonl(root / "evidence" / "entailment.jsonl")
    claim_ids = set(_ids(claims, "claim_id"))
    evidence_ids = set(_ids(evidence, "evidence_id"))
    table_ids = set(_ids(evidence_table, "evidence_id"))
    matrix_ids = set(_ids(matrix, "claim_id"))
    if evidence_ids != table_ids:
        failures.append(
            "deliverables/evidence_table.csv evidence IDs differ from "
            f"evidence/evidence.jsonl ({len(table_ids)} != {len(evidence_ids)})"
        )
    if claim_ids != matrix_ids:
        failures.append(
            "deliverables/claim_evidence_matrix.csv claim IDs differ from "
            f"corpus/claims.jsonl ({len(matrix_ids)} != {len(claim_ids)})"
        )
    expected_verdicts = {
        str(row.get("evidence_id") or "") for row in evidence
        if row.get("stance") in {"supports", "contradicts"}
        and row.get("evidence_kind") != "inferred"
    }
    verdict_ids = set(_ids(entailment, "evidence_id"))
    if expected_verdicts != verdict_ids:
        failures.append(
            "evidence/entailment.jsonl does not cover every grounding evidence "
            f"row ({len(verdict_ids)} != {len(expected_verdicts)})"
        )

    papers = _jsonl(root / "fulltext" / "papers.jsonl")
    misses = _jsonl(root / "fulltext" / "not_retrieved.jsonl")
    routes_path = root / "fulltext" / "acquisition_routes.jsonl"
    routes = _jsonl(routes_path)
    expected_route_ids = set(_ids(papers + misses, "paper_id"))
    route_ids = set(_ids(routes, "paper_id"))
    if expected_route_ids and not routes_path.exists():
        failures.append("fulltext/acquisition_routes.jsonl is missing")
    elif expected_route_ids != route_ids:
        failures.append(
            "acquisition route receipts do not account for every acquisition "
            f"outcome ({len(route_ids)} != {len(expected_route_ids)})"
        )

    figure_manifest = _json(
        root / "deliverables" / "figures_cited" / "figures_manifest.json"
    )
    statuses = {
        (str(row.get("paper_id") or ""), str(row.get("figure_id") or "")):
        str(row.get("status") or "")
        for row in figure_manifest.get("figures") or []
    }
    figure_entailment = {
        (
            str(row.get("paper_id") or ""),
            str(row.get("figure_id") or ""),
            str(row.get("claim_id") or ""),
        ): row
        for row in _jsonl(root / "evidence" / "figure_entailment.jsonl")
    }
    enforce_visual_ledger = bool(figure_manifest.get("figure_entailment_artifact"))
    for selected in figure_manifest.get("selected_figure_ids") or []:
        key = (
            str(selected.get("paper_id") or ""),
            str(selected.get("figure_id") or ""),
        )
        status = statuses.get(key, "")
        if status not in EXPLICIT_FIGURE_DISPOSITIONS:
            failures.append(
                f"selected figure {key[0]}/{key[1]} has no exported or explicit "
                "failure disposition"
            )
    for figure in figure_manifest.get("figures") or []:
        if figure.get("status") != "exported":
            continue
        quality = figure.get("quality_check") or {}
        if quality.get("status") != "pass":
            failures.append(
                f"exported figure {figure.get('paper_id')}/{figure.get('figure_id')} "
                "lacks a passing crop-quality receipt"
            )
        for mapping in figure.get("selection") or []:
            if float(mapping.get("relevance") or 0.0) <= 0:
                failures.append(
                    f"exported figure {figure.get('paper_id')}/{figure.get('figure_id')} "
                    f"has an unscored claim mapping to {mapping.get('claim_id')}"
                )
            if enforce_visual_ledger:
                pair = (
                    str(figure.get("paper_id") or ""),
                    str(figure.get("figure_id") or ""),
                    str(mapping.get("claim_id") or ""),
                )
                verdict = figure_entailment.get(pair) or {}
                checks = (
                    "entails", "direction_match", "model_match", "outcome_match",
                    "subject_match", "crop_complete", "labels_legible",
                    "no_page_contamination",
                )
                if not verdict or not all(verdict.get(key) is True for key in checks):
                    failures.append(
                        f"exported figure {pair[0]}/{pair[1]} lacks a complete "
                        f"passing visual verdict for {pair[2]}"
                    )
    for axis in figure_manifest.get("axis_coverage") or []:
        if (
            int(axis.get("eligible_candidate_pairs") or 0)
            and not int(axis.get("selected_figures") or 0)
        ):
            failures.append(
                f"figure axis {axis.get('axis')} had eligible visuals but the "
                "selector retained none"
            )
        if int(axis.get("selected_figures") or 0) and not (
            int(axis.get("exported_figures") or 0)
            or str(axis.get("gap_reason") or "").strip()
        ):
            failures.append(
                f"figure axis {axis.get('axis')} selected visuals but exported "
                "none and records no gap reason"
            )

    manifest = _json(root / "run_manifest.json")
    config = manifest.get("config") or {}
    metrics = manifest.get("metrics") or {}
    mode = str(manifest.get("mode") or "").lower()
    waiver = _managed_execution_waiver(config)
    failures.extend(
        f"figure/OCR intake: {failure}" for failure in figure_intake_errors(manifest)
    )
    selected_papers = counts.get("papers_selected", 0)
    managed_required = mode == "broad" or (
        mode == "deep" and selected_papers >= MANAGED_EXECUTION_MIN_PAPERS
    )
    managed_receipt = metrics.get("managed_machines") or {}
    if (
        mode in {"deep", "broad"}
        and config.get("adaptive_managed_concurrency")
        and not managed_receipt
    ):
        failures.append(
            "config.adaptive_managed_concurrency=true but metrics.managed_machines "
            "is absent; managed work cannot be reconciled after compaction"
        )
    if (
        managed_required
        and not config.get("adaptive_managed_concurrency")
        and not waiver
    ):
        failures.append(
            "required deep/broad run disabled adaptive managed execution without "
            "a user-approved config.managed_execution_waiver reason"
        )
    if managed_receipt:
        ocr_mode = str(config.get("ocr") or "")
        max_machines = int(config.get("managed_machines") or 0)
        if ocr_mode not in VALID_OCR_MODES:
            failures.append("managed execution cannot verify an invalid OCR mode")
        elif not 1 <= max_machines <= ABSOLUTE_MAX_MACHINES:
            failures.append(
                "managed execution config.managed_machines must be between 1 and "
                f"{ABSOLUTE_MAX_MACHINES}"
            )
        else:
            expected_machines = expected_machine_count(
                selected_papers, max_machines, ocr_mode
            )
            actual_machines = int(managed_receipt.get("machine_count") or 0)
            if actual_machines != expected_machines and not waiver:
                failures.append(
                    "managed execution used "
                    f"{actual_machines} machine(s), expected {expected_machines} "
                    "from selected-paper count and OCR mode"
                )
            completions = managed_receipt.get("machines") or []
            if len(completions) != actual_machines:
                failures.append(
                    "managed execution completion inventory does not match its "
                    "machine count"
                )
            completion_ids = {
                str(row.get("machine_id") or "") for row in completions
            }
            launches = managed_receipt.get("background_launches") or []
            launch_ids = {str(row.get("machine_id") or "") for row in launches}
            if launch_ids != completion_ids:
                failures.append(
                    "managed execution lacks one tracked-background launch receipt "
                    "per completed machine"
                )
            for launch in launches:
                if launch.get("run_in_background") is not True or not str(
                    launch.get("background_name") or ""
                ).strip():
                    failures.append(
                        "managed execution contains an invalid background launch receipt"
                    )
        if managed_receipt.get("exchange_mode") != "object-store":
            failures.append("managed execution did not use the object-store courier")
        provenance = manifest.get("skill_provenance") or {}
        if managed_receipt.get("skill_bundle_sha256") != provenance.get(
            "skill_bundle_sha256"
        ):
            failures.append(
                "managed workers did not use the skill bundle recorded in provenance"
            )
        if managed_receipt.get("skill_git_commit") != provenance.get("git_commit"):
            failures.append(
                "managed workers did not use the Git commit recorded in provenance"
            )
    failures.extend(skill_provenance_problems(root, pathlib.Path(__file__).parent.parent))
    retry = _json(root / "fulltext" / "global_transient_retry.json")
    if config.get("require_global_transient_retry") and retry.get("completed") is not True:
        failures.append("global transient-retrieval recovery is incomplete")
    failures.extend(_assembly_failures(
        root,
        require_native=bool(config.get("native_task_packing")),
        mode=str(manifest.get("mode") or "").lower(),
    ))
    return failures


def _quality_summary(root: pathlib.Path, counts: dict, failures: list[str]) -> dict:
    parse_states = Counter(
        str(row.get("state") or "unknown")
        for row in _jsonl(root / "fulltext" / "parse_quality.jsonl")
    )
    dispositions = Counter(
        str(row.get("disposition") or "unknown")
        for row in _jsonl(root / "evidence" / "evidence_lineage.jsonl")
    )
    route_outcomes = Counter(
        str(row.get("outcome") or "unknown")
        for row in _jsonl(root / "fulltext" / "acquisition_routes.jsonl")
    )
    figure_manifest = _json(
        root / "deliverables" / "figures_cited" / "figures_manifest.json"
    )
    manifest = _json(root / "run_manifest.json")
    media = _json(root / "state" / "infographic_media_check.json")
    adjudication_audit = _jsonl(root / "evidence" / "adjudication_audit.jsonl")
    figure_verdicts = _jsonl(root / "evidence" / "figure_entailment.jsonl")
    visual_checks = (
        "entails", "direction_match", "model_match", "outcome_match",
        "subject_match", "crop_complete", "labels_legible",
        "no_page_contamination",
    )
    return {
        "schema_version": 1,
        "complete": not failures,
        "canonical_counts": counts,
        "parse_states": dict(sorted(parse_states.items())),
        "adjudication_dispositions": dict(sorted(dispositions.items())),
        "adjudication_audit": {
            "batches": len(adjudication_audit),
            "blocks_reviewed": sum(int(row.get("candidate_blocks_reviewed") or 0)
                                   for row in adjudication_audit),
            "blocks_rejected": sum(int(row.get("rejected_blocks") or 0)
                                   for row in adjudication_audit),
        },
        "acquisition_outcomes": dict(sorted(route_outcomes.items())),
        "figure_selection": figure_manifest.get("selection_counts") or {},
        "figure_axis_coverage": figure_manifest.get("axis_coverage") or [],
        "figure_visual_verification": {
            "pairs_reviewed": len(figure_verdicts),
            "pairs_passing": sum(
                all(row.get(key) is True for key in visual_checks)
                for row in figure_verdicts
            ),
        },
        "figure_policy": {
            "count_policy": (manifest.get("config") or {}).get("figure_count_policy"),
            "minimum_paper_figures": (manifest.get("config") or {}).get(
                "minimum_paper_figures"
            ),
            "adaptive_resolution": (manifest.get("config") or {}).get(
                "adaptive_figure_resolution"
            ),
        },
        "infographic_checks": media.get("checks") or {},
        "execution": {
            "invocations": (manifest.get("metrics") or {}).get("invocations") or [],
            "active_invocation_seconds": (
                manifest.get("metrics") or {}
            ).get("active_invocation_seconds"),
            "calendar_span_seconds": (
                manifest.get("metrics") or {}
            ).get("calendar_span_seconds"),
            "managed_machines": (
                manifest.get("metrics") or {}
            ).get("managed_machines"),
            "skill_provenance": manifest.get("skill_provenance") or {},
            "skill_provenance_upgrades": _jsonl(
                root / "state" / "skill_provenance_upgrades.jsonl"
            ),
        },
        "failures": failures,
    }


def refresh(root: pathlib.Path, *, write: bool) -> tuple[dict, list[str]]:
    """Return the canonical reconciliation receipt and validation failures."""
    root = pathlib.Path(root).resolve()
    counts = _canonical_counts(root)
    manifest_path = root / "run_manifest.json"
    stats_path = root / "deliverables" / "review_stats.json"
    manifest = _json(manifest_path)
    stats = _json(stats_path)
    if write:
        manifest_metrics = manifest.setdefault("metrics", {})
        manifest_metrics.update(counts)
        stats.update(counts)
        elapsed = _elapsed_seconds(manifest)
        if elapsed is not None:
            # created_at -> updated_at is a resumable run's calendar span, not
            # active compute. Calling it end-to-end runtime made a roughly
            # two-hour SLC33A1 execution appear to take 31.8 hours after an
            # overnight resume. Keep the receipt, name it honestly, and leave
            # active stage timing to the managed/native task receipts.
            manifest_metrics.pop("end_to_end_elapsed_seconds", None)
            stats.pop("end_to_end_elapsed_seconds", None)
            manifest_metrics["calendar_span_seconds"] = elapsed
            stats["calendar_span_seconds"] = elapsed
        _atomic_json(manifest_path, manifest)
        _atomic_json(stats_path, stats)

    failures = _cross_artifact_failures(root, counts)
    current_manifest = _json(manifest_path)
    current_stats = _json(stats_path)
    for key in CANONICAL_METRICS:
        expected = counts[key]
        if (current_manifest.get("metrics") or {}).get(key) != expected:
            failures.append(
                f"run_manifest.json metrics.{key} is not canonical "
                f"({(current_manifest.get('metrics') or {}).get(key)!r} != {expected})"
            )
        if current_stats.get(key) != expected:
            failures.append(
                f"deliverables/review_stats.json {key} is not canonical "
                f"({current_stats.get(key)!r} != {expected})"
            )

    artifacts = [
        "corpus/claims.jsonl",
        "corpus/corpus_ledger.json",
        "deliverables/claim_evidence_matrix.csv",
        "deliverables/evidence_table.csv",
        "deliverables/figures_cited/figures_manifest.json",
        "evidence/entailment.jsonl",
        "evidence/evidence.jsonl",
        "evidence/evidence_lineage.jsonl",
        "evidence/rejected_evidence.jsonl",
        "fulltext/global_transient_retry.json",
        "fulltext/acquisition_routes.jsonl",
        "fulltext/parse_quality.jsonl",
        "run_manifest.json",
        "state/intake_snapshot.json",
        "state/skill_provenance.json",
    ]
    if (root / "state" / "skill_provenance_upgrades.jsonl").exists():
        artifacts.append("state/skill_provenance_upgrades.jsonl")
    for optional_evidence in (
        "evidence/adjudication_audit.jsonl",
        "evidence/figure_entailment.jsonl",
    ):
        if (root / optional_evidence).exists():
            artifacts.append(optional_evidence)
    artifacts.extend(
        str(path.relative_to(root))
        for path in sorted((root / "state" / "assemblies").glob("*.json"))
    )
    artifacts.extend(
        str(path.relative_to(root))
        for path in sorted((root / "state" / "managed_launches").glob("**/*.json"))
    )
    receipt = {
        "schema_version": 1,
        "complete": not failures,
        "counts": counts,
        "claim_disposition": _claim_disposition(root),
        "artifact_sha256": {
            relative: _sha256(root / relative) for relative in artifacts
        },
        "failures": failures,
    }
    if write:
        _atomic_json(root / RECEIPT, receipt)
        _atomic_json(
            root / "state" / "quality_summary.json",
            _quality_summary(root, counts, failures),
        )
    return receipt, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=pathlib.Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    receipt, failures = refresh(args.root, write=args.write)
    for failure in failures:
        print(f"FAIL: {failure}")
    print(
        "RECONCILE: "
        f"evidence={receipt['counts']['evidence_accepted']} "
        f"figures={receipt['counts']['figures_exported']} "
        f"failures={len(failures)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
