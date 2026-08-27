#!/usr/bin/env python3
"""Entailment gate: every delivered grounding anchor must mean what it claims.

The sibling gates prove existence. ``verify_review`` proves each quote is in the
block it cites; ``verify_pdf_quotes`` proves it reached the PDF verbatim;
``verify_report_contract`` proves the report is shaped like a review. All three
passed the run that shipped

    C-007  "Progranulin deficiency drives microglial dysfunction and
            neuroinflammation."

grounded on a sentence about an AAV RESCUE experiment that never mentions
neuroinflammation and concludes the microglial phenotype is downstream of
neurons — and reused that same sentence to ground a second claim. Existence was
never the problem. See ``semantic_verification`` for the full anatomy of the
defect and the verdict record that decomposes it.

This gate reads the verdicts a (blinded) reviewer produced into
``evidence/entailment.jsonl`` and holds the run to them.

Hard failures:

1. a verdict is structurally invalid or self-contradictory (see
   ``validate_verdict``) — an unreadable verdict is not a passing verdict;
2. a verdict references a ``claim_id`` or ``evidence_id`` that is not in
   ``evidence/evidence.jsonl``, or an anchor belonging to a different claim;
3. any displayed supporting or contradicting anchor has an unacceptable
   verdict — known-bad evidence may not remain under a delivered claim;
4. one anchor grounds two different claims and any verdict on it reports
   ``scope_overreach`` — one overstretched sentence doing the work of two.

Warning by default, hard failure under ``--require-entailment``:

5. a displayed supporting or contradicting anchor has no verdict. Mid-gathering
   runs may warn; delivery passes ``--require-entailment`` and fails.

Usage:
    python verify_entailment.py --root <run-root> [--require-entailment]

Exit code is the failure count, so it drops straight into a shell gate.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict

SCRIPTS = pathlib.Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from semantic_verification import (  # noqa: E402
    RESULT_ORIGINAL,
    unacceptable_reason,
    validate_verdict,
    verdict_is_acceptable,
)

VERDICTS_RELPATH = pathlib.PurePosixPath("evidence") / "entailment.jsonl"
EVIDENCE_RELPATH = pathlib.PurePosixPath("evidence") / "evidence.jsonl"

# These are the anchors the report displays as grounding evidence. Mentions and
# reviewer inference remain useful context but do not carry a claim.
_GROUNDING_STANCES = frozenset({"supports", "contradicts"})
_NON_QUALIFYING_KIND = "inferred"


def _read_jsonl(path: pathlib.Path) -> tuple[list[dict], list[str]]:
    """Rows plus per-line parse errors.

    Errors are RETURNED, never swallowed. A verdict file with a broken line is
    a file whose verdicts are partly unknown, and silently skipping the line
    would turn "unreadable" into "absent" — which under this gate's default is
    a warning rather than a failure.
    """
    rows: list[dict] = []
    errors: list[str] = []
    if not path.exists():
        return rows, errors
    for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception as exc:
            errors.append(f"{path.name} line {lineno}: not valid JSON ({exc})")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name} line {lineno}: not a JSON object")
            continue
        rows.append(value)
    return rows, errors


def _grounding_anchors(rows: list[dict]) -> list[dict]:
    return [r for r in rows
            if r.get("stance") in _GROUNDING_STANCES
            and r.get("evidence_kind") != _NON_QUALIFYING_KIND]


def _anchor_key(row: dict) -> tuple[str, str]:
    """Identity of the SENTENCE an anchor quotes, independent of which claim
    cites it.

    ``evidence_id`` is a hash over the claim as well as the block, so the same
    sentence cited under two claims yields two different evidence IDs — which
    is exactly how the shipped reuse went unnoticed. Keying on (block, quote)
    is what makes reuse visible.
    """
    return (str(row.get("block_id") or ""), str(row.get("quote") or ""))


def verify(root: pathlib.Path,
           require_entailment: bool = False) -> tuple[list[str], list[str], dict]:
    failures: list[str] = []
    notes: list[str] = []

    evidence_path = root / EVIDENCE_RELPATH
    verdicts_path = root / VERDICTS_RELPATH

    if not evidence_path.exists():
        failures.append(
            f"missing required evidence artifact: {EVIDENCE_RELPATH} — the "
            "entailment gate checks verdicts against the canonical evidence "
            "rows and cannot run without them"
        )
        return failures, notes, {}

    evidence, evidence_errors = _read_jsonl(evidence_path)
    verdicts, verdict_errors = _read_jsonl(verdicts_path)
    failures.extend(evidence_errors)
    failures.extend(verdict_errors)

    evidence_by_id: dict[str, dict] = {}
    by_claim: dict[str, list[dict]] = defaultdict(list)
    for row in evidence:
        eid = str(row.get("evidence_id") or "")
        if eid:
            evidence_by_id[eid] = row
        by_claim[str(row.get("claim_id") or "")].append(row)

    # --- 1/2: every verdict must be sound and must resolve ------------------
    verdicts_by_claim: dict[str, list[dict]] = defaultdict(list)
    resolved: list[tuple[dict, dict]] = []  # (verdict, evidence row)
    for index, verdict in enumerate(verdicts, 1):
        label = str(verdict.get("verdict_id") or f"<verdict line {index}>")
        for error in validate_verdict(verdict):
            failures.append(f"{label}: {error}")
        cid = str(verdict.get("claim_id") or "")
        eid = str(verdict.get("evidence_id") or "")
        if cid not in by_claim:
            failures.append(
                f"{label}: claim_id {cid!r} does not appear in {EVIDENCE_RELPATH} "
                "— the verdict grades a claim this run has no evidence for"
            )
            continue
        row = evidence_by_id.get(eid)
        if row is None:
            failures.append(
                f"{label}: evidence_id {eid!r} does not appear in "
                f"{EVIDENCE_RELPATH} — the verdict grades an anchor that is not "
                "in the canonical evidence store"
            )
            continue
        if str(row.get("claim_id") or "") != cid:
            failures.append(
                f"{label}: evidence {eid} belongs to claim "
                f"{row.get('claim_id')!r}, not {cid!r} — a verdict cannot be "
                "moved between claims; re-review the anchor under the claim it "
                "is actually cited for"
            )
            continue
        verdicts_by_claim[cid].append(verdict)
        resolved.append((verdict, row))

    # --- 3/5: every displayed grounding anchor must be covered + acceptable --
    claims_requiring_verdicts: list[str] = []
    fully_covered: list[str] = []
    missing_anchors: list[str] = []
    rejected_anchors: list[str] = []
    for cid in sorted(by_claim):
        rows = by_claim[cid]
        anchors = _grounding_anchors(rows)
        if not anchors:
            continue
        claims_requiring_verdicts.append(cid)
        anchor_ids = {str(r.get("evidence_id") or "") for r in anchors}
        claim_verdicts = [v for v in verdicts_by_claim.get(cid, [])
                          if str(v.get("evidence_id") or "") in anchor_ids]
        graded = {str(v.get("evidence_id") or "") for v in claim_verdicts}
        missing = sorted(anchor_ids - graded)
        if missing:
            missing_anchors.extend(missing)
            message = (
                f"{cid}: {len(missing)} of {len(anchor_ids)} displayed grounding "
                f"anchor(s) have no blinded entailment verdict: {', '.join(missing[:8])}"
            )
            (failures if require_entailment else notes).append(message)

        bad = [verdict for verdict in claim_verdicts
               if not verdict_is_acceptable(verdict)]
        if bad:
            rejected_anchors.extend(
                str(verdict.get("evidence_id") or "") for verdict in bad
            )
            reasons = "; ".join(
                f"{verdict.get('evidence_id')}: {unacceptable_reason(verdict)}"
                for verdict in bad
            )
            failures.append(
                f"{cid}: {len(bad)} displayed grounding anchor(s) were rejected "
                f"by semantic review [{reasons}]. Remove or re-anchor them, then "
                "rebuild the evidence-derived artifacts; known-bad anchors may "
                "not ship beside an otherwise acceptable claim."
            )
        if not missing and not bad:
            fully_covered.append(cid)

        if claim_verdicts and all(
            verdict.get("result_type") != RESULT_ORIGINAL
            for verdict in claim_verdicts
            if verdict_is_acceptable(verdict)
        ):
            notes.append(
                f"{cid} is entailed only by non-original statements "
                "(result_type cited/background); keep the evidence tier indirect."
            )

    # --- 4: one anchor, two claims, and an overreach ------------------------
    # Keyed on the SENTENCE, over every evidence row — not just the graded ones
    # — because reuse is a property of the store, and the second citation of a
    # sentence is often the one nobody reviewed. A duplicated `evidence_id`
    # lands on the same key (same block, same quote), so both shapes of reuse
    # are caught here.
    claims_by_anchor: dict[tuple[str, str], set[str]] = defaultdict(set)
    ids_by_anchor: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in evidence:
        key = _anchor_key(row)
        claims_by_anchor[key].add(str(row.get("claim_id") or ""))
        ids_by_anchor[key].add(str(row.get("evidence_id") or ""))
    verdicts_by_anchor: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for verdict, row in resolved:
        verdicts_by_anchor[_anchor_key(row)].append(verdict)

    for key, cids in sorted(claims_by_anchor.items()):
        if len(cids) < 2:
            continue
        overreaching = [v for v in verdicts_by_anchor.get(key, [])
                        if v.get("scope_overreach") is True]
        if not overreaching:
            continue
        block_id, quote = key
        failures.append(
            f"anchor {block_id} (evidence {', '.join(sorted(ids_by_anchor[key]))}) "
            f"is reused to ground {len(cids)} different claims "
            f"({', '.join(sorted(cids))}) and {len(overreaching)} verdict(s) on "
            f"it report scope_overreach: "
            f"“{quote[:120]}{'...' if len(quote) > 120 else ''}”. One sentence "
            "stretched across two claims supports neither as worded — give each "
            "claim its own anchor or narrow the claims."
        )

    stats = {
        "verdicts": len(verdicts),
        "claims_requiring_verdicts": len(claims_requiring_verdicts),
        "claims_fully_covered": len(fully_covered),
        "anchors_missing": len(set(missing_anchors)),
        "anchors_rejected": len(set(rejected_anchors)),
    }
    if not claims_requiring_verdicts:
        notes.append(
            "no displayed supporting or contradicting anchors in this run — "
            "the entailment gate has nothing to enforce"
        )
    return failures, notes, stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify that quoted anchors entail the claims they ground")
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--require-entailment",
        action="store_true",
        help="Escalate every missing grounding-anchor verdict to a hard failure. "
        "OFF for mid-gathering runs; ON for delivery.",
    )
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()

    failures, notes, stats = verify(root, require_entailment=args.require_entailment)
    for note in notes:
        print(f"NOTE: {note}")
    if stats:
        print(
            f"ENTAILMENT: verdicts={stats['verdicts']} "
            f"claims={stats['claims_requiring_verdicts']} "
            f"covered={stats['claims_fully_covered']} "
            f"missing={stats['anchors_missing']} "
            f"rejected={stats['anchors_rejected']}"
        )
    for failure in failures:
        print(f"FAIL: {failure}")
    print(f"VERIFY-ENTAILMENT: failures={len(failures)} "
          f"result={'pass' if not failures else 'fail'}")
    return min(255, len(failures))


if __name__ == "__main__":
    sys.exit(main())
