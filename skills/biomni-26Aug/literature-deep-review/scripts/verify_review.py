#!/usr/bin/env python3
"""Deterministic finalization gate for claim-first literature reviews."""
from __future__ import annotations

import support_policy  # noqa: E402

import argparse
import csv
import json
import math
import pathlib
import re
import sys
from collections import defaultdict

SCRIPTS = pathlib.Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evidence_first import (  # noqa: E402
    EVIDENCE_KINDS,
    STANCE_VALUES,
    build_matrix,
    is_incomplete_sentence_quote,
    looks_truncated,
    make_locator,
    quote_match,
    read_jsonl,
    support_state,
)
from report_model import resolve_review_mode  # noqa: E402
from verify_report_contract import (  # noqa: E402
    croppable_supply,
    requested_figure_floor,
    supply_failure,
)

DEFAULT_CONTRACT = SCRIPTS.parent / "templates" / "report_contract.json"


REQUIRED_MATRIX_COLUMNS = {
    "claim_id", "claim_text", "support_state", "supporting_evidence_ids",
    "mentioning_evidence_ids", "contradicting_evidence_ids", "source_locators",
}

from support_policy import WEAK_STATES, UNGROUNDED_STATES  # noqa: E402
# A claim is "grounded" iff at least one qualifying supporting OR contradicting
# quote resolves to it. Only C_INSUFFICIENT (no qualifying evidence at all) is
# ungrounded — C_REFUTED / C_CONFLICTED carry contradicting quotes and are
# legitimate findings (e.g. contradiction hunts).

STRONG_WORDING = re.compile(r"\b(proves?|cures?|always|never|in humans|abolishes?)\b", re.I)
ALLOWED_ACCESS = {
    "oa_licensed",
    "free_to_read",
    "user_supplied",
    "licensed_copy",
}


def _load_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def csv_rows(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def split_ids(value: str | None) -> list[str]:
    return [token.strip() for token in re.split(r"[;,]", value or "") if token.strip()]


def derived_stats(claims, blocks, papers, misses, evidence, matrix) -> dict:
    return {
        "papers_considered": len(papers) + len(misses),
        "papers_full_text": len(papers),
        "papers_not_retrieved": len(misses),
        "blocks": len(blocks),
        "claims_total": len(claims),
        "claims_grounded": sum(
            1 for row in matrix
            if support_policy.is_grounded(row.get("support_state"))
        ),
        "evidence_accepted": len(evidence),
    }


def _claims_missing_from_report(root, claims_by_id: dict, report: str) -> list[str]:
    """Claims the rendered report does not contain, by the id it prints.

    The report model exposes ``display_id`` for backwards compatibility, but it
    must equal the canonical ``claim_id``. Resolving through the model keeps the
    gate compatible with older runs while still catching a genuinely omitted
    claim.
    """
    display_by_claim: dict[str, str] = {}
    try:
        from report_model import build_model, load_contract
        for row in build_model(root, load_contract())["claims"]:
            if row.get("display_id"):
                display_by_claim[row["claim_id"]] = row["display_id"]
    except Exception:  # noqa: BLE001 - the model is checked by its own gate
        pass

    missing: list[str] = []
    for cid in claims_by_id:
        shown = display_by_claim.get(cid)
        if cid in report or (shown and shown in report):
            continue
        missing.append(f"{cid} (rendered as {shown})" if shown else str(cid))
    return missing


def verify(
    root: pathlib.Path,
    strict_counts: bool = False,
    require_all_grounded: bool = True,
    require_figures: bool = False,
    contract: dict | None = None,
) -> tuple[list[str], list[str], dict]:
    failures: list[str] = []
    notes: list[str] = []

    required = {
        "claims": root / "corpus" / "claims.jsonl",
        "blocks": root / "fulltext" / "blocks.jsonl",
        "papers": root / "fulltext" / "papers.jsonl",
        "evidence": root / "evidence" / "evidence.jsonl",
        "matrix": root / "synthesis" / "claim_evidence_matrix.csv",
    }
    for name, path in required.items():
        if not path.exists():
            failures.append(f"missing required {name} artifact: {path.relative_to(root)}")
    if failures:
        return failures, notes, {}

    claims = read_jsonl(required["claims"])
    blocks = read_jsonl(required["blocks"])
    papers = read_jsonl(required["papers"])
    evidence = read_jsonl(required["evidence"])
    matrix = csv_rows(required["matrix"])
    misses_path = root / "fulltext" / "not_retrieved.csv"
    misses = csv_rows(misses_path) if misses_path.exists() else []

    claims_by_id = {row.get("claim_id"): row for row in claims}
    blocks_by_id = {row.get("block_id"): row for row in blocks}
    papers_by_id = {row.get("paper_id"): row for row in papers}
    evidence_by_id: dict[str, dict] = {}
    if len(claims_by_id) != len(claims):
        failures.append("claim IDs are missing or duplicated")
    if len(blocks_by_id) != len(blocks):
        failures.append("block IDs are missing or duplicated")

    for row in evidence:
        eid = row.get("evidence_id")
        cid = row.get("claim_id")
        bid = row.get("block_id")
        pid = row.get("paper_id")
        prefix = str(eid or "<missing-evidence-id>")
        if not eid or eid in evidence_by_id:
            failures.append(f"{prefix}: missing or duplicate evidence_id")
            continue
        evidence_by_id[eid] = row
        if cid not in claims_by_id:
            failures.append(f"{prefix}: claim_id {cid!r} is absent from claims.jsonl")
        block = blocks_by_id.get(bid)
        if block is None:
            failures.append(f"{prefix}: block_id {bid!r} is absent from blocks.jsonl")
            continue
        if pid != block.get("paper_id"):
            failures.append(f"{prefix}: paper_id does not match the cited block")
        paper = papers_by_id.get(pid)
        if paper is None:
            failures.append(f"{prefix}: paper_id {pid!r} is absent from papers.jsonl")
        if row.get("stance") not in STANCE_VALUES:
            failures.append(f"{prefix}: invalid stance {row.get('stance')!r}")
        if row.get("evidence_kind") not in EVIDENCE_KINDS:
            failures.append(f"{prefix}: invalid evidence_kind {row.get('evidence_kind')!r}")
        match = quote_match(str(row.get("quote") or ""), str(block.get("text") or ""))
        if not match:
            failures.append(f"{prefix}: quote does not occur in the cited canonical block")
        elif row.get("quote_match") != match:
            failures.append(
                f"{prefix}: stored quote_match={row.get('quote_match')!r}, recomputed={match!r}"
            )
        if block.get("block_type") in {"sentence", "caption"} and looks_truncated(str(row.get("quote") or "")):
            failures.append(f"{prefix}: quotation appears truncated or structurally incomplete")
        if is_incomplete_sentence_quote(str(row.get("quote") or ""),
                                        block.get("block_type"),
                                        str(block.get("text") or "")):
            failures.append(
                f"{prefix}: body-sentence quote is not a complete sentence "
                "(must begin at a sentence boundary and end at terminal punctuation; "
                "quote the full sentence(s), not a sub-span)"
            )
        expected_locator = make_locator(block)
        if row.get("source_locator") != expected_locator:
            failures.append(
                f"{prefix}: locator does not match block provenance "
                f"({row.get('source_locator')!r} != {expected_locator!r})"
            )
        for field in ("page", "section", "figure_id", "bbox"):
            if row.get(field) != block.get(field):
                failures.append(f"{prefix}: {field} does not match the cited block")
        if block.get("block_type") == "figure_ocr" and (
            not row.get("figure_id") or not row.get("image_path") or not row.get("bbox")
        ):
            failures.append(f"{prefix}: figure OCR evidence has no resolvable region")
        if row.get("verified") is not True:
            failures.append(f"{prefix}: evidence is not marked verified")
        if row.get("access") not in ALLOWED_ACCESS:
            failures.append(f"{prefix}: invalid full-text access label {row.get('access')!r}")
        if paper and row.get("access") != paper.get("access"):
            failures.append(f"{prefix}: evidence access label does not match its paper")
        if paper and paper.get("full_text_status") != "retrieved":
            failures.append(f"{prefix}: paper is not labeled as retrieved full text")
        if row.get("stance") == "supports" and row.get("evidence_kind") == "inferred":
            notes.append(f"{prefix}: inferred row retained for audit but cannot raise claim support")

    if not matrix:
        failures.append("claim_evidence_matrix.csv has no claim rows")
    elif REQUIRED_MATRIX_COLUMNS - set(matrix[0]):
        failures.append(
            "claim_evidence_matrix.csv missing columns: "
            f"{sorted(REQUIRED_MATRIX_COLUMNS - set(matrix[0]))}"
        )

    matrix_by_id = {row.get("claim_id"): row for row in matrix}
    if set(matrix_by_id) != set(claims_by_id):
        failures.append("claim matrix IDs do not exactly match claims.jsonl")
    evidence_by_claim: dict[str, list[dict]] = defaultdict(list)
    for row in evidence:
        evidence_by_claim[row.get("claim_id")].append(row)

    for cid, claim in claims_by_id.items():
        row = matrix_by_id.get(cid)
        if row is None:
            continue
        rows = evidence_by_claim.get(cid, [])
        expected_state = support_state(rows)
        if row.get("support_state") != expected_state:
            failures.append(
                f"{cid}: support_state={row.get('support_state')!r}, expected={expected_state!r}"
            )
        # Every claim in the delivered review must be grounded in at least one
        # qualifying quote (supporting or contradicting). Ungrounded claims must
        # be dropped, split, or narrowed before delivery — never shipped.
        if require_all_grounded and expected_state in UNGROUNDED_STATES:
            failures.append(
                f"{cid}: ungrounded claim (support_state={expected_state!r}) — every "
                "claim must have >=1 qualifying supporting or contradicting quote; "
                "drop, split, or narrow this claim, or acquire evidence before delivery"
            )
        expected = {
            "supporting_evidence_ids": {
                r["evidence_id"] for r in rows
                if r["stance"] == "supports" and r["evidence_kind"] != "inferred"
            },
            "mentioning_evidence_ids": {
                r["evidence_id"] for r in rows
                if r["stance"] == "mentions" or r["evidence_kind"] == "inferred"
            },
            "contradicting_evidence_ids": {
                r["evidence_id"] for r in rows
                if r["stance"] == "contradicts" and r["evidence_kind"] != "inferred"
            },
        }
        for column, ids in expected.items():
            actual = set(split_ids(row.get(column)))
            if actual != ids:
                failures.append(f"{cid}: {column} does not match evidence.jsonl")
            for eid in actual:
                if eid not in evidence_by_id:
                    failures.append(f"{cid}: matrix references unknown evidence_id {eid}")
                elif evidence_by_id[eid].get("claim_id") != cid:
                    failures.append(f"{cid}: matrix references evidence belonging to another claim")
        cited = [
            r for r in rows
            if r["stance"] in {"supports", "contradicts"} and r["evidence_kind"] != "inferred"
        ]
        expected_locators = ";".join(f"{r['paper_id']} {r['source_locator']}" for r in cited)
        if row.get("source_locators", "") != expected_locators:
            failures.append(f"{cid}: matrix locators do not match its evidence rows")
        if expected_state in WEAK_STATES and STRONG_WORDING.search(claim.get("claim_text", "")):
            failures.append(f"{cid}: weak/refuted state uses prohibited strong wording")

    generated = build_matrix(claims, evidence)
    generated_by_id = {row["claim_id"]: row for row in generated}
    for cid, row in matrix_by_id.items():
        if cid in generated_by_id and row.get("support_state") != generated_by_id[cid]["support_state"]:
            failures.append(f"{cid}: matrix is not reproducible from canonical evidence")

    stats = derived_stats(claims, blocks, papers, misses, evidence, matrix)
    stats_path = root / "deliverables" / "review_stats.json"
    if stats_path.exists():
        stored = json.loads(stats_path.read_text())
        for key, value in stats.items():
            if stored.get(key) != value:
                failures.append(f"review_stats.json {key}={stored.get(key)!r}, expected={value!r}")
    elif strict_counts:
        failures.append("deliverables/review_stats.json is missing")

    report_path = root / "deliverables" / "review.md"
    if report_path.exists():
        report = report_path.read_text(encoding="utf-8", errors="replace")
        ratio = re.search(r"Grounded claims:\*\*\s*(\d+)\s+of\s+(\d+)", report)
        if ratio and (int(ratio.group(1)), int(ratio.group(2))) != (
            stats["claims_grounded"], stats["claims_total"]
        ):
            failures.append("review.md grounded-claim count disagrees with canonical artifacts")
        # Look for the canonical IDs the report prints. Older artifacts may
        # still carry a distinct display_id, so the compatibility resolver above
        # accepts either while current reports preserve claim_id end to end.
        missing = _claims_missing_from_report(root, claims_by_id, report)
        for label in missing:
            failures.append(f"review.md omits claim {label}")
    elif strict_counts:
        failures.append("deliverables/review.md is missing")

    # --- Figure-provenance gate -------------------------------------------
    # A figure-rich review (deep/broad, OCR on) must export the paper figures a
    # figure-level review promises. The bar is NOT "more than zero" — that let a
    # report that shipped one figure out of five croppable cited papers pass
    # clean. It is the contract floor for the mode and the fraction of the
    # croppable supply the contract demands, whichever is larger. This is a
    # WARNING by default (surfaced in notes) and can be escalated to a hard
    # failure with --require-figures. It never fires for quick / OCR-off /
    # text-only runs.
    figs_manifest = root / "deliverables" / "figures_cited" / "figures_manifest.json"
    figures_exported = 0
    exported_figure_keys: set[tuple[str, str]] = set()
    manifest_data = _load_json(figs_manifest) if figs_manifest.exists() else None
    if isinstance(manifest_data, dict):
        try:
            figures_exported = int(manifest_data.get("figures_exported", 0) or 0)
        except (TypeError, ValueError):
            figures_exported = 0
        for fig in manifest_data.get("figures", []) or []:
            if isinstance(fig, dict) and fig.get("status") == "exported":
                exported_figure_keys.add(
                    (str(fig.get("paper_id") or ""), str(fig.get("figure_id") or ""))
                )
    # The mode comes from the ONE canonical resolver, the same one both PDF
    # gates use. Three consumers each reading the manifest their own way is how
    # they came to hold the same run to different contracts.
    review_mode = resolve_review_mode(root)
    ocr_mode = None
    # Prefer run_manifest.json (authoritative; never overwritten by --emit-stats,
    # which rewrites review_stats.json with a canonical subset that drops the
    # mode/ocr/figure fields). Fall back to review_stats.json.
    manifest_path = root / "run_manifest.json"
    if manifest_path.exists():
        try:
            _mani = json.loads(manifest_path.read_text())
            ocr_mode = (_mani.get("config") or {}).get("ocr") or (_mani.get("metrics") or {}).get("ocr_mode")
        except Exception:
            pass
    if stats_path.exists():
        try:
            _stored = json.loads(stats_path.read_text())
            ocr_mode = ocr_mode or _stored.get("ocr_mode")
            if figures_exported == 0:
                figures_exported = int(_stored.get("figures_exported", 0) or 0)
        except Exception:
            pass
    # Figure-groundable claims: those carrying an accepted caption / figure_ocr
    # evidence row. A claim "landed" only if one of those rows names a figure the
    # run actually exported - that is what the operator has to chase down.
    fig_groundable = 0
    groundable_claims: set[str] = set()
    landed_claims: set[str] = set()
    for row in evidence:
        bid = str(row.get("block_id") or "")
        if ":CAP:" in bid or ":OCR:" in bid or row.get("evidence_kind") == "figure_ocr":
            if row.get("stance") in {"supports", "contradicts"}:
                fig_groundable += 1
                cid = str(row.get("claim_id") or "<unknown-claim>")
                groundable_claims.add(cid)
                key = (str(row.get("paper_id") or ""), str(row.get("figure_id") or ""))
                if key in exported_figure_keys:
                    landed_claims.add(cid)
    stranded_claims = sorted(groundable_claims - landed_claims)

    # Requirement from the contract + the croppable supply, never from what this
    # run exported.
    if contract is None:
        contract = _load_json(DEFAULT_CONTRACT)
    fig_spec = contract.get("paper_figures", {}) if isinstance(contract, dict) else {}
    try:
        selected_floor, floor_source = requested_figure_floor(
            root, contract, str(review_mode or "").strip().lower()
        )
    except (TypeError, ValueError) as exc:
        failures.append(f"invalid figure minimum: {exc}")
        selected_floor, floor_source = 0, "invalid run configuration"
    try:
        frac = float(fig_spec.get("min_fraction_of_croppable", 0) or 0)
    except (TypeError, ValueError):
        frac = 0.0
    supply = croppable_supply(root)
    croppable_papers = supply["croppable"]
    croppable_figures = supply["croppable_figures"]
    paper_required = math.ceil(frac * len(croppable_papers)) if croppable_papers else 0
    figure_required = selected_floor
    exported_papers = {
        paper_id for paper_id, _figure_id in exported_figure_keys
    } & croppable_papers

    is_figure_rich = (review_mode in {"deep", "broad"}) and (ocr_mode in {"targeted", "all"})
    if is_figure_rich:
        # An unreadable contract used to yield fig_spec={} -> both floors 0 ->
        # required = max(1, 0, 0) = 1: silently back to the tautological
        # "more than zero" bar this gate was rewritten to remove, with nothing
        # printed. verify_pdf_assets hard-fails on exactly this condition, and
        # so must this gate — a gate that quietly relaxes itself is worse than
        # no gate.
        if not fig_spec:
            failures.append(
                "could not read paper_figures thresholds from the report "
                f"contract ({DEFAULT_CONTRACT}); the figure requirement cannot "
                "be established for this figure-rich review "
                f"(mode={review_mode}, ocr={ocr_mode}). Restore the contract or "
                "pass an explicit --contract."
            )
        not_measurable = supply_failure(supply)
        if not_measurable:
            failures.append(not_measurable)
        # >=1 is the historical floor for a figure-rich review; the contract and
        # the croppable supply can only raise it.
        required_figures = max(1, figure_required, paper_required)
        if figures_exported < required_figures:
            msg = (
                "figure-provenance: a figure-rich review "
                f"(mode={review_mode}, ocr={ocr_mode}) exported {figures_exported} "
                f"paper figure(s) but >={required_figures} are required "
                f"(figure floor={figure_required} from {floor_source}; "
                f"{len(croppable_figures)} legal crops available; paper coverage floor="
                f"{paper_required}, {frac:.0%} of {len(croppable_papers)} "
                "croppable cited papers)"
                + (f"; {len(stranded_claims)} figure-groundable claim(s) ended with no "
                   f"exported figure: {', '.join(stranded_claims[:10])}"
                   f"{'...' if len(stranded_claims) > 10 else ''}"
                   if stranded_claims else
                   (f"; all {fig_groundable} figure-groundable evidence row(s) already "
                    "produced a figure, so the shortfall is unused supply"
                    if fig_groundable else
                    " and grounded no claim on a caption/figure block"))
                + ". Figure selection no longer requires a caption anchor (see scripts/figure_selection): a figure is chosen when its caption scores against the claim text. A shortfall now means the crops were not produced, the captions were not specific enough to any claim, or the selection caps are too tight — read selection_rejected in figures_manifest.json, which records the cause for every figure passed over."
            )
            if require_figures:
                failures.append(msg)
            else:
                notes.append("WARNING: " + msg)
        if len(exported_papers) < paper_required:
            unused = sorted(croppable_papers - exported_papers)
            msg = (
                "figure-provenance: too few cited papers contributed figures: "
                f"{len(exported_papers)} < {paper_required}; multiple crops from "
                "one paper cannot satisfy the paper-coverage floor. Available "
                f"papers with no export: {', '.join(unused[:8])}"
                f"{'...' if len(unused) > 8 else ''}."
            )
            if require_figures:
                failures.append(msg)
            else:
                notes.append("WARNING: " + msg)

    return failures, notes, stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify a claim-first literature review")
    parser.add_argument("--root", default=".")
    parser.add_argument("--strict-counts", action="store_true")
    parser.add_argument(
        "--allow-ungrounded-claims",
        action="store_true",
        help="Permit claims with no qualifying supporting or contradicting quote "
        "(C_INSUFFICIENT). OFF by default: every delivered claim must be grounded.",
    )
    parser.add_argument(
        "--emit-stats", nargs="?", const="deliverables/review_stats.json", default=None
    )
    parser.add_argument(
        "--require-figures",
        action="store_true",
        help="Escalate the figure-provenance check to a hard failure: a figure-rich "
        "review (deep/broad + OCR on) that exports fewer paper figures than the "
        "report contract requires fails instead of only warning. OFF by default "
        "(warning only).",
    )
    parser.add_argument(
        "--contract",
        default=None,
        help="Report contract holding the paper_figures thresholds the "
        "figure-provenance check measures against "
        f"(default: {DEFAULT_CONTRACT}).",
    )
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()

    # An EXPLICIT --contract that cannot be read is a hard error. Falling back
    # to the default contract would measure the run against thresholds the
    # operator did not ask for and never be told about it.
    contract = None
    if args.contract:
        contract = _load_json(pathlib.Path(args.contract))
        if not isinstance(contract, dict):
            print(f"FAIL: could not read the contract passed via --contract: "
                  f"{args.contract}")
            print("VERIFY-REVIEW: failures=1 warnings=0 result=fail")
            return 1

    failures, notes, stats = verify(
        root,
        strict_counts=args.strict_counts,
        require_all_grounded=not args.allow_ungrounded_claims,
        require_figures=args.require_figures,
        contract=contract,
    )
    if args.emit_stats and stats:
        target = pathlib.Path(args.emit_stats)
        if not target.is_absolute():
            target = root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
        notes.append(f"emitted canonical stats -> {target}")
    warn_count = 0
    for note in notes:
        if note.startswith("WARNING: "):
            print(f"WARN: {note[len('WARNING: '):]}")
            warn_count += 1
        else:
            print(f"NOTE: {note}")
    for failure in failures:
        print(f"FAIL: {failure}")
    print(
        f"VERIFY-REVIEW: failures={len(failures)} warnings={warn_count} "
        f"result={'pass' if not failures else 'fail'}"
    )
    return min(255, len(failures))


if __name__ == "__main__":
    sys.exit(main())
