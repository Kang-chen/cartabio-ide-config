#!/usr/bin/env python3
"""Emit and assemble Biomni-native claim/figure visual verification tasks."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re

from evidence_first import read_jsonl
from export_figures import image_candidate_disposition
from figure_selection import select, subject_aliases_from_manifest
from report_model import load_contract

REQUIRED_VERDICTS = (
    "entails", "direction_match", "model_match", "outcome_match",
    "subject_match", "crop_complete", "labels_legible",
    "no_page_contamination",
)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:160]


def _jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def _figures(root: pathlib.Path) -> dict[tuple[str, str], dict]:
    figures: dict[tuple[str, str], dict] = {}
    for path in sorted((root / "fulltext" / "parsed").glob("*.json")):
        try:
            paper = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        paper_id = str(paper.get("paper_id") or "")
        for row in paper.get("figures") or []:
            figure_id = str(row.get("figure_id") or "")
            if paper_id and figure_id and not image_candidate_disposition(row):
                figures[(paper_id, figure_id)] = dict(row)
    return figures


def emit(root: pathlib.Path) -> int:
    claims = read_jsonl(root / "corpus" / "claims.jsonl")
    evidence = read_jsonl(root / "evidence" / "evidence.jsonl")
    references = {
        str(row.get("paper_id") or ""): row
        for row in read_jsonl(root / "corpus" / "references.jsonl")
    }
    manifest = json.loads((root / "run_manifest.json").read_text())
    figures = _figures(root)
    quoted = [
        (str(row.get("paper_id") or ""), str(row.get("figure_id") or ""),
         str(row.get("claim_id") or ""))
        for row in evidence
        if row.get("block_type") in {"caption", "figure_ocr"}
        and row.get("figure_id")
    ]
    policy = dict((load_contract().get("paper_figures") or {}).get("selection") or {})
    policy.update({
        "require_pair_verification": False,
        "max_per_claim": 1000,
        "max_per_paper_per_claim": 1000,
    })
    proposed = select(
        claims, evidence, figures, references, policy,
        quoted=quoted,
        subject_aliases=subject_aliases_from_manifest(manifest),
    )
    claims_by_id = {str(row.get("claim_id") or ""): row for row in claims}
    evidence_by_pair: dict[tuple[str, str], list[dict]] = {}
    for row in evidence:
        key = (str(row.get("claim_id") or ""), str(row.get("paper_id") or ""))
        evidence_by_pair.setdefault(key, []).append(row)
    task_dir = root / "evidence" / "figure_entailment_tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    for stale in task_dir.glob("*.json"):
        stale.unlink()
    output_dir = root / "evidence" / "figure_entailment_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("*.json"):
        stale.unlink()
    seen: set[tuple[str, str, str]] = set()
    for choice in proposed.chosen:
        key = (choice.paper_id, choice.figure_id, choice.claim_id)
        if key in seen:
            continue
        seen.add(key)
        figure = figures[(choice.paper_id, choice.figure_id)]
        image = pathlib.Path(str(figure.get("image_path") or ""))
        name = _safe("__".join(key)) + ".json"
        task = {
            "task_id": f"figure-entailment:{'|'.join(key)}",
            "paper_id": choice.paper_id,
            "figure_id": choice.figure_id,
            "claim_id": choice.claim_id,
            "claim": claims_by_id[choice.claim_id],
            "caption": str(figure.get("caption") or ""),
            "ocr": figure.get("ocr") or [],
            "image_path": str(image),
            "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "anchors": [
                {field: row.get(field) for field in
                 ("evidence_id", "quote", "stance", "source_locator")}
                for row in evidence_by_pair.get((choice.claim_id, choice.paper_id), [])
            ],
            "output_path": str(output_dir / name),
            "instructions": (
                "Use Biomni Read in media_output_check mode on image_path. Judge "
                "only this claim/figure pair and its supplied anchors. Return one "
                "JSON object with every required boolean, reviewer, and rationale. "
                "entails means the visible panels—not merely the caption—depict the "
                "atomic claim. Fail direction/model/outcome mismatches. crop_complete "
                "requires all panels and edge labels; labels_legible requires readable "
                "axes/legends; no_page_contamination excludes headers/body prose."
            ),
            "required_output": {
                **{key: "boolean" for key in REQUIRED_VERDICTS},
                "reviewer": "stable Biomni reviewer identifier",
                "rationale": "specific visual justification or rejection reason",
            },
        }
        (task_dir / name).write_text(json.dumps(task, indent=2) + "\n")
    print(f"FIGURE-ENTAILMENT-TASKS: {len(seen)} -> {task_dir}")
    return len(seen)


def assemble(root: pathlib.Path) -> int:
    tasks = sorted((root / "evidence" / "figure_entailment_tasks").glob("*.json"))
    if not tasks:
        raise ValueError("no figure entailment tasks; run --emit first")
    rows: list[dict] = []
    missing: list[str] = []
    for task_path in tasks:
        task = json.loads(task_path.read_text())
        output = pathlib.Path(str(task.get("output_path") or ""))
        if not output.exists():
            missing.append(str(output))
            continue
        verdict = json.loads(output.read_text())
        absent = [key for key in (*REQUIRED_VERDICTS, "reviewer", "rationale")
                  if key not in verdict]
        if absent:
            raise ValueError(f"{output}: missing {', '.join(absent)}")
        if any(not isinstance(verdict[key], bool) for key in REQUIRED_VERDICTS):
            raise ValueError(f"{output}: every structured verdict must be boolean")
        image = pathlib.Path(str(task["image_path"]))
        if hashlib.sha256(image.read_bytes()).hexdigest() != task["image_sha256"]:
            raise ValueError(f"{task_path}: image changed after visual task emission")
        rows.append({
            "paper_id": task["paper_id"],
            "figure_id": task["figure_id"],
            "claim_id": task["claim_id"],
            **{key: verdict[key] for key in REQUIRED_VERDICTS},
            "reviewer": str(verdict["reviewer"]),
            "rationale": str(verdict["rationale"]),
            "image_sha256": task["image_sha256"],
        })
    if missing:
        raise ValueError(f"{len(missing)} figure-entailment output(s) missing")
    _jsonl(root / "evidence" / "figure_entailment.jsonl", rows)
    print(f"FIGURE-ENTAILMENT: {len(rows)} verified pair(s)")
    return len(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--emit", action="store_true")
    group.add_argument("--assemble", action="store_true")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    emit(root) if args.emit else assemble(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
