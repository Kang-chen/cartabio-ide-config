#!/usr/bin/env python3
"""Extract per-claim grounded verbatim anchors from the canonical evidence store.

This is the single source of truth for the exact quoted sentences that MUST be
embedded, verbatim, under each claim in every deliverable (review.md and the PDF
Results section). It reads only the canonical artifacts and never invents text.

Output:
- ``deliverables/grounded_quotes.json`` : machine-readable, keyed by claim_id.
- ``deliverables/grounded_quotes.md``   : human-readable block for pasting/review.

Each claim entry lists its accepted anchors (exact ``quote`` + rendered
``locator`` + ``stance`` + ``evidence_kind`` + ``paper_id`` + ``block_id`` +
access ``url``), split into supporting/contradicting/mentioning, so the report
author can drop the real sentences straight in with provenance.

A claim that is grounded (any support_state except C_INSUFFICIENT) but carries
zero supporting/contradicting anchors is a hard error: the whole point of the
skill is that grounded claims are backed by exact sentences. Refuted claims
count — their anchors are the contradicting quotes.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict

from evidence_first import read_jsonl, make_locator, support_state

# Claims in these states do not require an anchor.
#
# C_REFUTED is deliberately NOT here. A refuted claim is grounded — by its
# contradicting quotes — and `build()` below collects those into
# `contradicting_anchors`, so the requirement is satisfiable. Exempting it meant
# a contradiction hunt, one of this skill's advertised uses, could ship every
# refuted finding with no verbatim quote behind it. Matches verify_pdf_quotes,
# verify_review, verify_report_contract, and SKILL.md §7.
from support_policy import UNGROUNDED_STATES as _UNGROUNDED
# An anchor with one of these stances/kinds counts as a real grounding sentence.
_ANCHOR_STANCES = {"supports", "contradicts"}


def _anchor(row: dict) -> dict:
    return {
        "evidence_id": row.get("evidence_id"),
        "paper_id": row.get("paper_id"),
        "block_id": row.get("block_id"),
        "quote": row.get("quote"),
        "quote_match": row.get("quote_match"),
        "stance": row.get("stance"),
        "evidence_kind": row.get("evidence_kind"),
        "source_locator": row.get("source_locator") or "",
        "page": row.get("page"),
        "section": row.get("section"),
        "figure_id": row.get("figure_id"),
        "url": row.get("url") or row.get("access_url") or "",
        "doi": row.get("doi") or "",
        "title": row.get("title") or "",
        "year": row.get("year"),
    }


def build(root: pathlib.Path) -> tuple[dict, list[str]]:
    failures: list[str] = []
    claims_path = root / "corpus" / "claims.jsonl"
    evidence_path = root / "evidence" / "evidence.jsonl"
    for name, path in (("claims", claims_path), ("evidence", evidence_path)):
        if not path.exists():
            failures.append(f"missing required {name} artifact: {path}")
    if failures:
        return {}, failures

    claims = read_jsonl(claims_path)
    evidence = read_jsonl(evidence_path)

    by_claim: dict[str, list[dict]] = defaultdict(list)
    for row in evidence:
        by_claim[row.get("claim_id")].append(row)

    out: dict[str, dict] = {}
    for claim in claims:
        cid = claim.get("claim_id")
        rows = by_claim.get(cid, [])
        state = support_state(rows) if rows else "C_INSUFFICIENT"
        supporting = [_anchor(r) for r in rows
                      if r.get("stance") == "supports" and r.get("evidence_kind") != "inferred"]
        contradicting = [_anchor(r) for r in rows
                         if r.get("stance") == "contradicts" and r.get("evidence_kind") != "inferred"]
        mentioning = [_anchor(r) for r in rows
                      if r.get("stance") == "mentions" or r.get("evidence_kind") == "inferred"]
        entry = {
            "claim_id": cid,
            "claim_text": claim.get("claim_text", ""),
            "cluster": claim.get("cluster", ""),
            "support_state": state,
            "supporting_anchors": supporting,
            "contradicting_anchors": contradicting,
            "mentioning_anchors": mentioning,
            "n_grounding_anchors": len(supporting) + len(contradicting),
        }
        out[cid] = entry
        # A grounded claim with no real grounding sentence is a contract violation.
        if state not in _UNGROUNDED and entry["n_grounding_anchors"] == 0:
            failures.append(
                f"{cid}: support_state={state} but has zero supporting/contradicting "
                "verbatim anchors (grounded claims require at least one exact sentence)"
            )

    return out, failures


def render_markdown(data: dict) -> str:
    lines = ["# Grounded quotes (verbatim anchors per claim)", ""]
    lines.append(
        "Every quote below is an exact substring of the cited canonical source block. "
        "Embed these verbatim (with their locators) under each claim in the report."
    )
    lines.append("")
    for cid in sorted(data):
        e = data[cid]
        lines.append(f"## {cid} — {e['claim_text']}  `[{e['support_state']}]`")
        lines.append("")
        buckets = [
            ("Supporting", e["supporting_anchors"]),
            ("Contradicting", e["contradicting_anchors"]),
            ("Mentioning / context", e["mentioning_anchors"]),
        ]
        any_anchor = False
        for label, anchors in buckets:
            if not anchors:
                continue
            any_anchor = True
            lines.append(f"**{label}:**")
            lines.append("")
            for a in anchors:
                loc = a["source_locator"] or make_locator(a)
                cite = a.get("doi") or a.get("paper_id") or ""
                lines.append(f"> \u201c{a['quote']}\u201d")
                lines.append("")
                lines.append(
                    f"> — `{a['block_id']}` · {loc} · {a['stance']}/{a['evidence_kind']}"
                    + (f" · {cite}" if cite else "")
                )
                lines.append("")
        if not any_anchor:
            lines.append("_No verbatim anchors (claim is not grounded)._")
            lines.append("")
    return "\n".join(lines) + "\n"


def emit_narrative_tasks(root: pathlib.Path, data: dict) -> int:
    """One narrative-authoring unit per claim. Returns the count.

    The adjudication batches are emitted as discrete files and the emitter
    prints, at the moment it hands the work over, that they are independent and
    should be run concurrently. The per-claim narratives had no such moment:
    they are pure agent work, so the only statement that they are parallel lived
    in a paragraph of SKILL.md. Instructions one indirection away from the point
    of use are the same failure that kept the antibody shape guide out of the
    renderer for three runs.

    Each file carries the claim, its scope, its computed support tier and its
    accepted quotes — everything the author needs and nothing to go fetch. No
    record reads another, so all of them can be written at once.
    """
    from report_model import NARRATIVE_FACET_LABEL
    from support_policy import SUPPORT_LABEL

    out_dir = root / "deliverables" / "narrative_tasks"
    if out_dir.exists():
        for stale in out_dir.glob("*.json"):
            stale.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir = root / "deliverables" / "narrative_outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for claim_id, entry in sorted(data.items()):
        if entry["support_state"] in _UNGROUNDED:
            continue
        safe = claim_id.replace("/", "_")
        atomic = out_dir / f"{safe}.json"
        state = entry["support_state"]
        atomic.write_text(json.dumps({
            "claim_id": claim_id,
            "claim_text": entry.get("claim_text", ""),
            "cluster": entry.get("cluster", ""),
            "support_state": state,
            "support_label": SUPPORT_LABEL.get(state, state),
            # The accepted anchors themselves, so the author writes from the
            # quotes rather than going back to the evidence store for them.
            "supporting_anchors": entry.get("supporting_anchors", []),
            "contradicting_anchors": entry.get("contradicting_anchors", []),
            "facets_required": list(NARRATIVE_FACET_LABEL),
            "output_path": f"deliverables/narrative_outputs/{safe}.json",
            "output": "one JSON object for this claim_id",
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        count += 1

    print(f"NARRATIVE-TASKS: {count} independent unit(s) -> {out_dir} "
          f"(native Biomni is default; stage-workers emits bounded native packs, "
          f"then use batch_tasks.py assemble-narratives)")
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract per-claim grounded verbatim anchors from canonical evidence"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--strict", action="store_true",
        help="exit non-zero if any grounded claim lacks a verbatim anchor",
    )
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()

    data, failures = build(root)
    if data:
        deliv = root / "deliverables"
        deliv.mkdir(parents=True, exist_ok=True)
        (deliv / "grounded_quotes.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (deliv / "grounded_quotes.md").write_text(
            render_markdown(data), encoding="utf-8"
        )
        total_anchors = sum(e["n_grounding_anchors"] for e in data.values())
        grounded = sum(
            1 for e in data.values() if e["support_state"] not in _UNGROUNDED
        )
        print(
            f"GROUNDED-QUOTES: claims={len(data)} grounded={grounded} "
            f"grounding_anchors={total_anchors} -> {deliv/'grounded_quotes.json'}"
        )
        # The task files are a convenience for parallelising the narratives;
        # the grounded quotes are the product. This runs AFTER the quotes are
        # written, so letting it raise would fail the grounding step with its
        # real output already on disk — the operator sees a non-zero exit and a
        # complete grounded_quotes.json and cannot tell which to believe.
        try:
            emit_narrative_tasks(root, data)
        except Exception as exc:  # noqa: BLE001 - never cost the grounding step
            print(f"NARRATIVE-TASKS: not emitted ({type(exc).__name__}: {exc}). "
                  "Grounding is unaffected; author the narratives from "
                  "deliverables/grounded_quotes.json instead.", file=sys.stderr)

    for f in failures:
        print(f"FAIL: {f}")
    print(
        f"GROUNDED-QUOTES: failures={len(failures)} "
        f"result={'pass' if not failures else 'fail'}"
    )
    if args.strict:
        return min(255, len(failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
