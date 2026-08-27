#!/usr/bin/env python3
"""Deterministic batching and bounded provider concurrency for adjudication."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import pathlib
import threading
import time
from dataclasses import dataclass
from typing import Callable

from llm_adjudicator import AdjudicationError, adjudicate, build_prompt


DEFAULT_ADJUDICATION_JOBS = 8
MAX_ADJUDICATION_JOBS = 16

Batch = tuple[str, list[dict], list[dict]]
Adjudicate = Callable[[str, str, str], tuple[list[dict], dict]]


@dataclass(frozen=True)
class BatchResult:
    """One provider result, kept in input order after concurrent execution."""

    index: int
    paper_id: str
    claim_ids: tuple[str, ...]
    block_ids: tuple[str, ...]
    rows: tuple[dict, ...] = ()
    meta: dict | None = None
    cache_hit: bool = False
    elapsed_seconds: float = 0.0
    error: str = ""


def atomic_json(path: pathlib.Path, value: object) -> None:
    """Write JSON through a thread-unique temporary file on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temp.replace(path)


def candidate_batches(
    candidates: list[dict],
    claims_by_id: dict[str, dict],
    blocks_by_id: dict[str, dict],
    claims_per_call: int,
    max_blocks: int,
) -> list[Batch]:
    """Group candidates into deterministic, independent per-paper batches."""
    from collections import defaultdict

    grouped: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in candidates:
        grouped[row["paper_id"]][row["claim_id"]].append(row)

    batches: list[Batch] = []
    for paper_id in sorted(grouped):
        claim_ids = sorted(grouped[paper_id])
        for offset in range(0, len(claim_ids), claims_per_call):
            chunk = claim_ids[offset : offset + claims_per_call]
            selected_ids: set[str] = set()
            selected: list[dict] = []
            for claim_id in chunk:
                best = sorted(
                    grouped[paper_id][claim_id],
                    key=lambda row: (-row["retrieval_score"], row["block_id"]),
                )[0]
                if best["block_id"] not in selected_ids:
                    selected_ids.add(best["block_id"])
                    selected.append(best)
            remainder = sorted(
                (
                    row
                    for claim_id in chunk
                    for row in grouped[paper_id][claim_id]
                    if row["block_id"] not in selected_ids
                ),
                key=lambda row: (-row["retrieval_score"], row["block_id"]),
            )
            for row in remainder:
                if len(selected) >= max_blocks:
                    break
                if row["block_id"] not in selected_ids:
                    selected_ids.add(row["block_id"])
                    selected.append(row)
            batches.append(
                (
                    paper_id,
                    [claims_by_id[claim_id] for claim_id in chunk],
                    [blocks_by_id[row["block_id"]] for row in selected],
                )
            )
    return batches


def emit_batches(
    run_root: pathlib.Path,
    batches: list[Batch],
    papers_by_id: dict[str, dict],
) -> int:
    """Emit self-contained tasks for native Biomni workers."""
    out_dir = run_root / "evidence" / "adjudication_batches"
    if out_dir.exists():
        for stale in out_dir.glob("batch_*.json"):
            stale.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = run_root / "evidence" / "adjudications"
    outputs.mkdir(parents=True, exist_ok=True)

    for index, (paper_id, claims, blocks) in enumerate(batches, 1):
        batch_id = f"batch_{index:04d}"
        atomic_json(
            out_dir / f"{batch_id}.json",
            {
                "batch_id": batch_id,
                "paper_id": paper_id,
                "claim_ids": [claim["claim_id"] for claim in claims],
                "block_ids": [block["block_id"] for block in blocks],
                "n_blocks": len(blocks),
                "audit_required": True,
                "output_path": f"evidence/adjudications/{batch_id}.jsonl",
                "instructions": (
                    "Follow the adjudication prompt. Write each item from the "
                    "resulting evidence array as one JSON object per line to "
                    "output_path. Then write one final JSONL object with only "
                    "the key _decision_audit. Its value must contain "
                    "candidate_blocks_reviewed, accepted_blocks, rejected_blocks, "
                    "and rejection_reasons (a count mapping). Counts must cover "
                    "all supplied blocks; do not emit unsupported evidence rows. "
                    "Do not write the evidence wrapper, markdown, or commentary."
                ),
                "required_output": (
                    "JSONL evidence rows matching the prompt schema followed by "
                    "one _decision_audit object"
                ),
                "prompt": build_prompt(
                    papers_by_id.get(paper_id, {"paper_id": paper_id}),
                    claims,
                    blocks,
                ),
            },
        )
    print(
        f"ADJUDICATION-BATCHES: {len(batches)} independent unit(s) -> {out_dir} "
        "(stage with batch_tasks.py to emit bounded native packs; every task "
        "keeps its own output_path)"
    )
    return len(batches)


def _cache_path(
    cache_dir: pathlib.Path, backend: str, model: str, prompt: str
) -> pathlib.Path:
    digest = hashlib.sha256(
        (backend + "\0" + model + "\0" + prompt).encode()
    ).hexdigest()
    return cache_dir / f"{digest}.json"


def _run_one(
    index: int,
    batch: Batch,
    papers_by_id: dict[str, dict],
    backend: str,
    model: str,
    cache_dir: pathlib.Path,
    adjudicate_fn: Adjudicate,
) -> BatchResult:
    paper_id, claims, blocks = batch
    prompt = build_prompt(
        papers_by_id.get(paper_id, {"paper_id": paper_id}), claims, blocks
    )
    cache_path = _cache_path(cache_dir, backend, model, prompt)
    started = time.monotonic()
    claim_ids = tuple(str(claim["claim_id"]) for claim in claims)
    block_ids = tuple(str(block["block_id"]) for block in blocks)
    try:
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            rows = cached["evidence"]
            meta = cached.get("meta", {})
            cache_hit = True
        else:
            rows, meta = adjudicate_fn(backend, model, prompt)
            atomic_json(cache_path, {"evidence": rows, "meta": meta})
            cache_hit = False
        if not isinstance(rows, list):
            raise AdjudicationError("adjudication cache/result has no evidence list")
        return BatchResult(
            index=index,
            paper_id=paper_id,
            claim_ids=claim_ids,
            block_ids=block_ids,
            rows=tuple(rows),
            meta=meta if isinstance(meta, dict) else {},
            cache_hit=cache_hit,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
    except (AdjudicationError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return BatchResult(
            index=index,
            paper_id=paper_id,
            claim_ids=claim_ids,
            block_ids=block_ids,
            elapsed_seconds=round(time.monotonic() - started, 3),
            error=f"{type(exc).__name__}: {exc}",
        )


def run_provider_batches(
    batches: list[Batch],
    papers_by_id: dict[str, dict],
    *,
    backend: str,
    model: str,
    cache_dir: pathlib.Path,
    jobs: int = DEFAULT_ADJUDICATION_JOBS,
    adjudicate_fn: Adjudicate = adjudicate,
) -> list[BatchResult]:
    """Run independent provider calls concurrently and return input-ordered results."""
    if jobs < 1 or jobs > MAX_ADJUDICATION_JOBS:
        raise ValueError(
            f"jobs must be between 1 and {MAX_ADJUDICATION_JOBS}, got {jobs}"
        )
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not batches:
        return []
    worker_count = min(jobs, len(batches))
    if worker_count == 1:
        return [
            _run_one(
                index,
                batch,
                papers_by_id,
                backend,
                model,
                cache_dir,
                adjudicate_fn,
            )
            for index, batch in enumerate(batches)
        ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [
            pool.submit(
                _run_one,
                index,
                batch,
                papers_by_id,
                backend,
                model,
                cache_dir,
                adjudicate_fn,
            )
            for index, batch in enumerate(batches)
        ]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]
    return sorted(results, key=lambda result: result.index)
