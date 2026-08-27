#!/usr/bin/env python3
"""Build and validate the canonical paper-completeness ledger for one review.

The ledger is derived from durable run artifacts.  It is the join between the
search pool, explicit scope decisions, acquisition outcomes, accepted evidence,
citations, and reproduced figures.  A coordinator may be compacted or replaced;
the final report is still reconstructible because every paper has one explicit
row and every transition is counted here.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
from collections import Counter
from typing import Iterable


LEDGER_PATH = pathlib.Path("corpus/corpus_ledger.json")
SCOPE_PATH = pathlib.Path("corpus/scope_decisions.jsonl")
PRIOR_PATH = pathlib.Path("corpus/prior_references.jsonl")
PRIOR_LEDGER_PATH = pathlib.Path("corpus/prior_corpus_ledger.json")
RECONCILIATION_PATH = pathlib.Path("corpus/prior_run_reconciliation.jsonl")
GLOBAL_RETRY_PATH = pathlib.Path("fulltext/global_transient_retry.json")
COVERAGE_MATRIX_PATH = pathlib.Path("corpus/coverage_matrix.json")
REQUIRED_BROAD_AXES = (
    "dependency_causality",
    "direction_of_effect",
    "mechanism_competing_models",
    "pharmacology_target_engagement",
    "biomarker_patient_context",
    "safety_essentiality",
    "translational_clinical",
    "contradictions_nulls",
    "combinations",
)
STATES = (
    "discovered",
    "deduplicated",
    "in_scope",
    "selected",
    "attempted",
    "retrieved",
    "evidence_bearing",
    "cited",
    "figure_producing",
)
PRIOR_STATUSES = frozenset({"retained", "superseded", "excluded"})
GROUNDING_STANCES = frozenset({"supports", "contradicts"})


def read_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def read_rows(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    rows: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path} line {line_number} is not a JSON object")
        rows.append(value)
    return rows


def atomic_json(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def paper_id(row: dict) -> str:
    return str(
        row.get("paper_id")
        or row.get("doi")
        or row.get("pmid")
        or row.get("pmcid")
        or ""
    ).strip()


def identity_tokens(row: dict) -> set[str]:
    """Stable paper/version-family identities for cross-run reconciliation."""
    tokens: set[str] = set()
    for key in ("paper_id", "doi", "pmid", "pmcid", "study_id"):
        value = str(row.get(key) or "").strip().casefold()
        if value:
            tokens.add(f"{key}:{value}")
    merged = row.get("merged_from") or []
    if isinstance(merged, str):
        merged = [merged]
    tokens.update(
        f"merged:{str(value).strip().casefold()}"
        for value in merged if str(value).strip()
    )
    # A prior canonical paper_id may reappear only as merged_from (or vice
    # versa) after preprint/journal-version normalization.
    pid = str(row.get("paper_id") or "").strip().casefold()
    if pid:
        tokens.add(f"merged:{pid}")
    return tokens


def _indexed(rows: Iterable[dict], label: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for row in rows:
        pid = paper_id(row)
        if not pid:
            raise ValueError(f"{label} contains a record without paper_id")
        if pid in indexed:
            raise ValueError(f"{label} contains duplicate paper_id {pid!r}")
        indexed[pid] = row
    return indexed


def _selected_path(root: pathlib.Path) -> pathlib.Path | None:
    for relative in (
        "corpus/records.jsonl",
        "corpus/pivotal_papers.csv",
        "corpus/pivotal_papers.jsonl",
    ):
        path = root / relative
        if path.exists():
            return path
    return None


def _miss_kind(row: dict) -> str:
    kind = str(row.get("_not_retrieved_kind") or "").strip()
    if kind:
        return kind
    reason = str(row.get("_not_retrieved_reason") or row.get("reason") or "").lower()
    if any(term in reason for term in ("paywall", "closed", "unauthorized", "forbidden")):
        return "paywalled"
    if any(term in reason for term in ("timeout", "network", "fetch", "connection", "retrieval")):
        return "retrieval_failed"
    return "unclassified"


def build(root: pathlib.Path, selected_path: pathlib.Path | None = None) -> dict:
    root = root.resolve()
    references = _indexed(read_rows(root / "corpus" / "references.jsonl"), "references")
    if not references:
        raise ValueError("corpus/references.jsonl is empty or missing")

    scope = _indexed(read_rows(root / SCOPE_PATH), "scope decisions")
    unknown_scope = sorted(set(scope) - set(references))
    if unknown_scope:
        raise ValueError(
            "scope decisions reference papers absent from references.jsonl: "
            + ", ".join(unknown_scope[:8])
        )
    invalid_scope = [
        pid for pid, row in scope.items()
        if not isinstance(row.get("in_scope"), bool)
    ]
    if invalid_scope:
        raise ValueError(
            "scope decisions require a boolean in_scope field: "
            + ", ".join(invalid_scope[:8])
        )

    chosen_path = selected_path or _selected_path(root)
    selected_rows = read_rows(chosen_path) if chosen_path else []
    selected = _indexed(selected_rows, "selected records") if selected_rows else {}
    papers = _indexed(read_rows(root / "fulltext" / "papers.jsonl"), "retrieved papers")
    misses = _indexed(
        read_rows(root / "fulltext" / "not_retrieved.jsonl"),
        "not-retrieved papers",
    )
    evidence = read_rows(root / "evidence" / "evidence.jsonl")
    parse_quality = _indexed(
        read_rows(root / "fulltext" / "parse_quality.jsonl"),
        "parse-quality receipts",
    ) if (root / "fulltext" / "parse_quality.jsonl").exists() else {}
    lineage = read_rows(root / "evidence" / "evidence_lineage.jsonl")
    figure_manifest = read_json(
        root / "deliverables" / "figures_cited" / "figures_manifest.json"
    )

    evidence_bearing = {
        paper_id(row) for row in evidence if paper_id(row)
    }
    cited = {
        paper_id(row)
        for row in evidence
        if paper_id(row)
        and row.get("stance") in GROUNDING_STANCES
        and row.get("evidence_kind") != "inferred"
    }
    figure_producing = {
        paper_id(row)
        for row in (figure_manifest.get("figures") or [])
        if row.get("status") == "exported" and paper_id(row)
    }
    evidence_counts = Counter(paper_id(row) for row in evidence if paper_id(row))
    rejected_counts = Counter(
        paper_id(row) for row in lineage
        if row.get("disposition") == "rejected" and paper_id(row)
    )
    figure_counts = Counter(
        paper_id(row)
        for row in (figure_manifest.get("figures") or [])
        if row.get("status") == "exported" and paper_id(row)
    )
    unknown_selected = sorted(set(selected) - set(references))
    unknown_outcomes = sorted((set(papers) | set(misses)) - set(references))
    unknown_evidence = sorted((evidence_bearing | cited) - set(references))
    unknown_figures = sorted(figure_producing - set(references))
    for label, values in (
        ("selected records", unknown_selected),
        ("acquisition outcomes", unknown_outcomes),
        ("evidence", unknown_evidence),
        ("figure manifest", unknown_figures),
    ):
        if values:
            raise ValueError(
                f"{label} reference papers absent from references.jsonl: "
                + ", ".join(values[:8])
            )

    entries: list[dict] = []
    for pid, reference in references.items():
        decision = scope.get(pid)
        in_scope = True if decision is None else decision.get("in_scope") is True
        scope_reason = (
            "not explicitly excluded"
            if decision is None
            else str(decision.get("reason") or "").strip()
        )
        miss = misses.get(pid, {})
        entries.append({
            "paper_id": pid,
            "title": str(reference.get("title") or ""),
            "doi": str(reference.get("doi") or ""),
            "year": str(reference.get("year") or ""),
            "journal": str(reference.get("journal") or ""),
            "is_preprint": bool(reference.get("is_preprint")),
            "publication_role": str(reference.get("publication_role") or ""),
            "study_id": str(reference.get("study_id") or ""),
            "merged_from": (
                list(reference.get("merged_from") or [])
                if not isinstance(reference.get("merged_from"), str)
                else [str(reference.get("merged_from"))]
            ),
            "discovered": True,
            "deduplicated": True,
            "in_scope": in_scope,
            "scope_reason": scope_reason,
            "selected": pid in selected,
            "selection_reason": (
                "selected for acquisition"
                if pid in selected
                else ("out of scope" if not in_scope else "in-scope paper not selected")
            ),
            "attempted": pid in papers or pid in misses,
            "retrieved": pid in papers,
            "retrieval_kind": _miss_kind(miss) if miss else "retrieved" if pid in papers else "not_attempted",
            "retrieval_reason": str(
                miss.get("_not_retrieved_reason") or miss.get("reason") or ""
            ),
            "parse_quality": str(parse_quality.get(pid, {}).get("state") or ""),
            "parse_quality_reason": str(
                parse_quality.get(pid, {}).get("reason") or ""
            ),
            "accepted_evidence_count": int(evidence_counts.get(pid, 0)),
            "rejected_adjudication_count": int(rejected_counts.get(pid, 0)),
            "evidence_bearing": pid in evidence_bearing,
            "cited": pid in cited,
            "figure_producing": pid in figure_producing,
            "exported_figure_count": int(figure_counts.get(pid, 0)),
        })

    prior = _indexed(read_rows(root / PRIOR_PATH), "prior references")
    reconciliations = _indexed(
        read_rows(root / RECONCILIATION_PATH), "prior-run reconciliation"
    )
    unknown_reconciliations = sorted(set(reconciliations) - set(prior))
    if unknown_reconciliations:
        raise ValueError(
            "prior-run reconciliation references unknown prior papers: "
            + ", ".join(unknown_reconciliations[:8])
        )
    prior_rows: list[dict] = []
    for pid, prior_ref in prior.items():
        if pid in selected:
            status, reason = "retained", "present in current selected corpus"
        else:
            decision = reconciliations.get(pid, {})
            status = str(decision.get("status") or "")
            reason = str(decision.get("reason") or "").strip()
        prior_rows.append({
            "paper_id": pid,
            "title": str(prior_ref.get("title") or ""),
            "status": status,
            "reason": reason,
            "current_selected": pid in selected,
            "replacement_paper_ids": list(
                reconciliations.get(pid, {}).get("replacement_paper_ids") or []
            ),
        })

    counts = {state: sum(bool(row[state]) for row in entries) for state in STATES}
    ingestion = read_json(root / "corpus" / "ingestion.json")
    counts["discovered"] = max(
        len(entries),
        int(
            ingestion.get("mapped_count")
            or ingestion.get("record_count")
            or len(entries)
        ),
    )
    miss_counts = Counter(
        row["retrieval_kind"] for row in entries if row["attempted"] and not row["retrieved"]
    )
    manifest = read_json(root / "run_manifest.json")
    prior_ledger = read_json(root / PRIOR_LEDGER_PATH)
    prior_retrieved = [
        row for row in (prior_ledger.get("records") or [])
        if isinstance(row, dict) and row.get("retrieved")
    ]
    prior_by_identity: dict[str, list[dict]] = {}
    for row in prior_retrieved:
        for token in identity_tokens(row):
            prior_by_identity.setdefault(token, []).append(row)
    retrieval_regressions: list[dict] = []
    for row in entries:
        if not row.get("selected") or row.get("retrieved"):
            continue
        matches: dict[str, dict] = {}
        for token in identity_tokens(row):
            for prior_row in prior_by_identity.get(token, []):
                matches[paper_id(prior_row)] = prior_row
        if matches:
            retrieval_regressions.append({
                "paper_id": row["paper_id"],
                "prior_paper_ids": sorted(matches),
                "current_retrieval_kind": row["retrieval_kind"],
                "current_retrieval_reason": row["retrieval_reason"],
            })
    return {
        "schema_version": 1,
        "review_mode": str(manifest.get("mode") or ""),
        "explicit_paper_cap": (manifest.get("config") or {}).get("max_papers"),
        "scope_policy": (
            "Every deduplicated search record is in scope unless "
            "corpus/scope_decisions.jsonl explicitly excludes it with a reason."
        ),
        "selected_source": (
            str(chosen_path.relative_to(root))
            if chosen_path and chosen_path.is_relative_to(root)
            else str(chosen_path or "")
        ),
        "counts": counts,
        "retrieval_classification": dict(sorted(miss_counts.items())),
        "records": entries,
        "prior_run_reconciliation": prior_rows,
        "retrieval_regressions": retrieval_regressions,
        "global_transient_retry": read_json(root / GLOBAL_RETRY_PATH),
        "coverage_matrix": read_json(root / COVERAGE_MATRIX_PATH),
    }


def validate(ledger: dict, *, final: bool = False) -> list[str]:
    errors: list[str] = []
    records = ledger.get("records") or []
    mode = str(ledger.get("review_mode") or "")
    cap = ledger.get("explicit_paper_cap")

    for row in records:
        pid = str(row.get("paper_id") or "")
        if not row.get("in_scope") and not str(row.get("scope_reason") or "").strip():
            errors.append(f"{pid}: out-of-scope decision has no reason")
        if row.get("selected") and not row.get("in_scope"):
            errors.append(f"{pid}: selected even though scope decision excludes it")
        if row.get("attempted") and not row.get("selected"):
            errors.append(f"{pid}: acquisition outcome exists for an unselected paper")
        if final and row.get("selected") and not row.get("attempted"):
            errors.append(f"{pid}: selected paper was never attempted")
        if row.get("retrieved") and not row.get("attempted"):
            errors.append(f"{pid}: retrieved without an acquisition attempt")
        if final and row.get("retrieved") and not str(
            row.get("parse_quality") or ""
        ).strip():
            errors.append(f"{pid}: retrieved paper lacks parse-quality state")
        if row.get("evidence_bearing") and not row.get("retrieved"):
            errors.append(f"{pid}: evidence-bearing without retrieved full text")
        if row.get("cited") and not row.get("evidence_bearing"):
            errors.append(f"{pid}: cited without an evidence-bearing record")
        if row.get("figure_producing") and not row.get("cited"):
            errors.append(f"{pid}: produced a report figure but is not cited")

    if mode == "broad" and cap is None:
        omitted = [
            str(row.get("paper_id") or "")
            for row in records
            if row.get("in_scope") and not row.get("selected")
        ]
        if omitted:
            errors.append(
                "uncapped broad review omitted in-scope papers: "
                + ", ".join(omitted[:8])
            )

    if mode == "broad":
        matrix = ledger.get("coverage_matrix") or {}
        rows = {
            str(row.get("axis") or ""): row
            for row in (matrix.get("axes") or [])
            if isinstance(row, dict)
        }
        for axis in REQUIRED_BROAD_AXES:
            row = rows.get(axis)
            if row is None:
                errors.append(f"broad coverage matrix is missing axis {axis}")
                continue
            status = str(row.get("status") or "")
            if status not in {
                "searched_with_evidence", "searched_empty", "not_applicable"
            }:
                errors.append(
                    f"broad coverage axis {axis} must be searched_with_evidence, "
                    "searched_empty, or not_applicable"
                )
            elif status.startswith("searched_") and not (row.get("queries") or []):
                errors.append(f"broad coverage axis {axis} records no search query")
            elif status in {"searched_empty", "not_applicable"} and not str(
                row.get("reason") or ""
            ).strip():
                errors.append(
                    f"broad coverage axis {axis} status={status} has no reason"
                )

    for row in ledger.get("prior_run_reconciliation") or []:
        status = str(row.get("status") or "")
        reason = str(row.get("reason") or "").strip()
        pid = str(row.get("paper_id") or "")
        if status not in PRIOR_STATUSES:
            errors.append(f"{pid}: prior source is not retained, superseded, or excluded")
        elif status == "retained" and not row.get("current_selected"):
            errors.append(f"{pid}: prior source marked retained but is not selected")
        elif status != "retained" and not reason:
            errors.append(f"{pid}: prior-source {status} decision has no reason")

    transient = [
        row for row in records
        if row.get("attempted") and not row.get("retrieved")
        and row.get("retrieval_kind") == "retrieval_failed"
    ]
    if final and transient:
        recovery = ledger.get("global_transient_retry") or {}
        if recovery.get("completed") is not True:
            errors.append(
                f"{len(transient)} transient retrieval failure(s) remain without "
                "a completed global post-merge retry"
            )
        elif int(recovery.get("attempted") or 0) < len(transient):
            errors.append(
                "global post-merge retry claims completion but attempted fewer "
                "papers than the remaining transient set"
            )
    if final:
        for row in ledger.get("retrieval_regressions") or []:
            errors.append(
                f"{row.get('paper_id')}: current run failed to retrieve a paper "
                "that a prior run retrieved "
                f"({', '.join(row.get('prior_paper_ids') or [])}); reuse the "
                "prior cached full text or exhaust the recorded version-family "
                "routes before assembly"
            )
    return errors


def refresh(
    root: pathlib.Path,
    selected_path: pathlib.Path | None = None,
    *,
    final: bool = False,
) -> tuple[dict, list[str]]:
    ledger = build(root, selected_path)
    errors = validate(ledger, final=final)
    ledger["validation"] = {"final": final, "ok": not errors, "errors": errors}
    atomic_json(root / LEDGER_PATH, ledger)
    return ledger, errors


def summary(ledger: dict) -> dict:
    return {
        "counts": dict(ledger.get("counts") or {}),
        "retrieval_classification": dict(ledger.get("retrieval_classification") or {}),
        "prior_run_reconciliation": Counter(
            str(row.get("status") or "unresolved")
            for row in (ledger.get("prior_run_reconciliation") or [])
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=pathlib.Path)
    parser.add_argument("--selected", type=pathlib.Path)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args(argv)
    ledger, errors = refresh(args.root, args.selected, final=args.final)
    print(json.dumps({**summary(ledger), "errors": errors}, indent=2, default=dict))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
