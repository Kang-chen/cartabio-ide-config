#!/usr/bin/env python3
"""Small state helper for the tusoskill skill.

This script is intentionally generic. It does not optimize methods by itself; it
creates durable ledgers that Biomni can use while developing a method.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_WEIGHTS = {
    "representation": 0.16,
    "model_form": 0.14,
    "objective_optimization": 0.13,
    "data_integration": 0.12,
    "diagnosis_error_analysis": 0.11,
    "ablation_simplification": 0.10,
    "robustness_calibration": 0.09,
    "efficiency_scaling": 0.08,
    "derivation_theory": 0.05,
    "packaging_reproducibility": 0.02,
}
DEFAULT_FLOORS = {
    "representation": 0.05,
    "model_form": 0.05,
    "objective_optimization": 0.04,
    "data_integration": 0.04,
    "diagnosis_error_analysis": 0.04,
    "ablation_simplification": 0.04,
    "robustness_calibration": 0.03,
    "efficiency_scaling": 0.03,
    "derivation_theory": 0.02,
    "packaging_reproducibility": 0.01,
}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def append_jsonl(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def normalize(weights: Dict[str, float], floors: Dict[str, float] | None = None) -> Dict[str, float]:
    floors = floors or {}
    keys = list(weights)
    values = {k: max(float(weights.get(k, 0.0)), float(floors.get(k, 0.0))) for k in keys}
    total = sum(values.values())
    if total <= 0:
        return {k: 1.0 / len(keys) for k in keys}
    return {k: v / total for k, v in values.items()}


def state_paths(root: Path) -> Dict[str, Path]:
    return {
        "state": root / "state" / "state.json",
        "weights": root / "state" / "strategy_weights.json",
        "portfolio": root / "state" / "diversity_portfolio.json",
        "backlog": root / "state" / "backlog.jsonl",
        "experiments": root / "experiments" / "results.jsonl",
        "feedback": root / "feedback" / "feedback.jsonl",
        "leaderboard": root / "experiments" / "leaderboard.csv",
        "data_sources": root / "data_sources" / "data_sources.jsonl",
        "derivations": root / "derivations" / "derivations.jsonl",
        "status": root / "reports" / "status.json",
    }


def cmd_init(args: argparse.Namespace) -> None:
    root = Path(args.root)
    for d in ["benchmark", "state", "candidates", "experiments", "feedback", "data_sources", "derivations", "diagnostics", "final_method", "reports"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    paths = state_paths(root)
    run_id = args.run_id or f"bm-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    state = read_json(paths["state"], {})
    if not state:
        state = {
            "run_id": run_id,
            "created_at": now(),
            "updated_at": now(),
            "task": args.task,
            "primary_metric": args.metric,
            "metric_direction": args.direction,
            "max_iterations": args.max_iterations,
            "max_wall_hours": args.max_wall_hours,
            "iteration": 0,
            "best_candidate_id": None,
            "best_score": None,
            "benchmark_version": args.benchmark_version,
        }
        write_json(paths["state"], state)
    weights = normalize(DEFAULT_WEIGHTS, DEFAULT_FLOORS)
    if not paths["weights"].exists():
        write_json(paths["weights"], {"updated_at": now(), "weights": weights, "floors": DEFAULT_FLOORS})
    if not paths["portfolio"].exists():
        write_json(paths["portfolio"], {"updated_at": now(), "families": []})
    write_status(root)
    print(json.dumps({"root": str(root), "run_id": state["run_id"], "state": str(paths["state"])}))


def score_is_better(score: float | None, best: float | None, direction: str) -> bool:
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return False
    if best is None:
        return True
    return score > best if direction == "higher" else score < best


def update_weights(root: Path, intervention: str, decision: str) -> None:
    paths = state_paths(root)
    payload = read_json(paths["weights"], {"weights": DEFAULT_WEIGHTS, "floors": DEFAULT_FLOORS})
    weights = dict(payload.get("weights", DEFAULT_WEIGHTS))
    floors = dict(payload.get("floors", DEFAULT_FLOORS))
    if intervention not in weights:
        weights[intervention] = 0.03
        floors[intervention] = 0.01
    if decision == "accept":
        weights[intervention] *= 1.25
    elif decision == "archive":
        weights[intervention] *= 1.05
    elif decision == "reject":
        weights[intervention] *= 0.85
    # infrastructure decisions do not penalize scientific strategy
    payload["weights"] = normalize(weights, floors)
    payload["floors"] = floors
    payload["updated_at"] = now()
    write_json(paths["weights"], payload)


def update_portfolio(root: Path, record: Dict[str, Any]) -> None:
    paths = state_paths(root)
    portfolio = read_json(paths["portfolio"], {"families": []})
    families = portfolio.setdefault("families", [])
    family_name = record.get("method_family") or "unspecified"
    direction = read_json(paths["state"], {}).get("metric_direction", "higher")
    entry = None
    for fam in families:
        if fam.get("name") == family_name:
            entry = fam
            break
    if entry is None:
        entry = {"name": family_name, "created_at": now(), "attempts": 0, "best_candidate_id": None, "best_score": None, "notes": []}
        families.append(entry)
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    score = record.get("score")
    if score_is_better(score, entry.get("best_score"), direction):
        entry["best_score"] = score
        entry["best_candidate_id"] = record.get("candidate_id")
    note = record.get("summary") or record.get("hypothesis") or ""
    if note:
        entry.setdefault("notes", []).append({"at": now(), "candidate_id": record.get("candidate_id"), "decision": record.get("decision"), "note": note[:240]})
        entry["notes"] = entry["notes"][-8:]
    portfolio["updated_at"] = now()
    write_json(paths["portfolio"], portfolio)


def write_leaderboard(root: Path) -> None:
    paths = state_paths(root)
    rows = read_jsonl(paths["experiments"])
    state = read_json(paths["state"], {})
    direction = state.get("metric_direction", "higher")
    scored = [r for r in rows if r.get("score") is not None and r.get("decision") in {"accept", "archive", "reject"}]
    scored.sort(key=lambda r: r.get("score"), reverse=(direction == "higher"))
    paths["leaderboard"].parent.mkdir(parents=True, exist_ok=True)
    with paths["leaderboard"].open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "candidate_id", "parent_id", "iteration", "method_family", "intervention", "decision", "score", "runtime_sec", "memory_mb", "summary"])
        writer.writeheader()
        for i, r in enumerate(scored, 1):
            writer.writerow({
                "rank": i,
                "candidate_id": r.get("candidate_id"),
                "parent_id": r.get("parent_id"),
                "iteration": r.get("iteration"),
                "method_family": r.get("method_family"),
                "intervention": r.get("intervention"),
                "decision": r.get("decision"),
                "score": r.get("score"),
                "runtime_sec": r.get("runtime_sec"),
                "memory_mb": r.get("memory_mb"),
                "summary": r.get("summary", "")[:300],
            })


def write_status(root: Path) -> None:
    paths = state_paths(root)
    state = read_json(paths["state"], {})
    experiments = read_jsonl(paths["experiments"])
    feedback = read_jsonl(paths["feedback"])
    status = {
        "updated_at": now(),
        "run_id": state.get("run_id"),
        "task": state.get("task"),
        "iteration": state.get("iteration", 0),
        "max_iterations": state.get("max_iterations"),
        "best_candidate_id": state.get("best_candidate_id"),
        "best_score": state.get("best_score"),
        "experiments": len(experiments),
        "feedback_items": len(feedback),
        "leaderboard": str(paths["leaderboard"]),
    }
    write_json(paths["status"], status)


# Decisions that represent a real, informative iteration (accept/reject/archive).
# Anything else (infrastructure retries) or an explicit --non-substantive flag does not
# advance the iteration counter.
SUBSTANTIVE_DECISIONS = {"accept", "reject", "archive"}


def is_substantive(record: Dict[str, Any]) -> bool:
    """A record counts toward the iteration floor iff it has a substantive decision and
    is not explicitly flagged non-substantive / failed-infra."""
    if record.get("non_substantive"):
        return False
    return str(record.get("decision")) in SUBSTANTIVE_DECISIONS


def count_substantive(experiments: List[Dict[str, Any]]) -> int:
    return sum(1 for r in experiments if is_substantive(r))


def cmd_add_experiment(args: argparse.Namespace) -> None:
    root = Path(args.root)
    paths = state_paths(root)
    state = read_json(paths["state"], {})
    non_substantive = bool(getattr(args, "non_substantive", False)) or args.decision not in SUBSTANTIVE_DECISIONS
    record = {
        "timestamp": now(),
        # `iteration` here is the ordinal this record will occupy once counted; it is
        # informational. --iteration is advisory only and never used to advance the counter
        # (a smaller manual value can no longer stall the floor, which was the 6/50 bug).
        "iteration": args.iteration,
        "candidate_id": args.candidate_id,
        "parent_id": args.parent_id,
        "method_family": args.method_family,
        "intervention": args.intervention,
        "component": args.component,
        "hypothesis": args.hypothesis,
        "edited_files": args.edited_files or [],
        "score": args.score,
        "runtime_sec": args.runtime_sec,
        "memory_mb": args.memory_mb,
        "decision": args.decision,
        "non_substantive": non_substantive,
        "summary": args.summary,
        "guardrails": args.guardrails,
        "leakage_audit": args.leakage_audit,
        "next_action": args.next_action,
        "benchmark_version": args.benchmark_version or state.get("benchmark_version"),
    }
    append_jsonl(paths["experiments"], record)
    # Counter := number of substantive records in the ledger (1 substantive candidate = +1).
    # This is derived from the append-only ledger, so it is self-healing and cannot be
    # stalled by a manual --iteration value.
    experiments = read_jsonl(paths["experiments"])
    counted = count_substantive(experiments)
    record["iteration"] = counted if is_substantive(record) else state.get("iteration", 0)
    state["iteration"] = counted
    if score_is_better(args.score, state.get("best_score"), state.get("metric_direction", "higher")) and args.decision in {"accept", "archive"}:
        state["best_score"] = args.score
        state["best_candidate_id"] = args.candidate_id
    state["updated_at"] = now()
    write_json(paths["state"], state)
    update_weights(root, args.intervention, args.decision)
    update_portfolio(root, record)
    if args.feedback:
        append_jsonl(paths["feedback"], {"timestamp": now(), "type": "experiment", "candidate_id": args.candidate_id, "component": args.component, "evidence": args.feedback, "recommendation": args.next_action})
    write_leaderboard(root)
    write_status(root)
    remaining = max(0, int(state.get("max_iterations", 0)) - counted)
    print(json.dumps({"recorded": args.candidate_id, "iteration": counted, "substantive": is_substantive(record), "remaining_to_floor": remaining, "decision": args.decision, "best_candidate_id": state.get("best_candidate_id"), "best_score": state.get("best_score")}))


def cmd_add_backlog(args: argparse.Namespace) -> None:
    root = Path(args.root)
    paths = state_paths(root)
    item = {
        "timestamp": now(),
        "priority": args.priority,
        "method_family": args.method_family,
        "intervention": args.intervention,
        "component": args.component,
        "hypothesis": args.hypothesis,
        "test": args.test,
        "source": args.source,
    }
    append_jsonl(paths["backlog"], item)
    print(json.dumps({"added": item}))


def cmd_add_feedback(args: argparse.Namespace) -> None:
    root = Path(args.root)
    paths = state_paths(root)
    item = {
        "timestamp": now(),
        "type": args.type,
        "component": args.component,
        "evidence": args.evidence,
        "recommendation": args.recommendation,
        "similar_ideas": args.similar_ideas,
        "avoid": args.avoid,
    }
    append_jsonl(paths["feedback"], item)
    write_status(root)
    print(json.dumps({"feedback_recorded": True}))


def cmd_add_data_source(args: argparse.Namespace) -> None:
    root = Path(args.root)
    paths = state_paths(root)
    item = {
        "timestamp": now(),
        "name": args.name,
        "purpose": args.purpose,
        "provenance": args.provenance,
        "version": args.version,
        "license": args.license,
        "path": args.path,
        "join_keys": args.join_keys,
        "coverage": args.coverage,
        "leakage_assessment": args.leakage_assessment,
        "ablation_result": args.ablation_result,
    }
    append_jsonl(paths["data_sources"], item)
    print(json.dumps({"data_source_recorded": args.name}))


def cmd_add_derivation(args: argparse.Namespace) -> None:
    root = Path(args.root)
    paths = state_paths(root)
    item = {
        "timestamp": now(),
        "title": args.title,
        "component": args.component,
        "assumptions": args.assumptions,
        "objective": args.objective,
        "properties": args.properties,
        "complexity": args.complexity,
        "test_plan": args.test_plan,
    }
    append_jsonl(paths["derivations"], item)
    print(json.dumps({"derivation_recorded": args.title}))


def cmd_suggest_next(args: argparse.Namespace) -> None:
    root = Path(args.root)
    paths = state_paths(root)
    weights = read_json(paths["weights"], {"weights": DEFAULT_WEIGHTS}).get("weights", DEFAULT_WEIGHTS)
    backlog = read_jsonl(paths["backlog"])
    portfolio = read_json(paths["portfolio"], {"families": []})
    experiments = read_jsonl(paths["experiments"])
    sorted_weights = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
    backlog_sorted = sorted(backlog, key=lambda r: float(r.get("priority", 0)), reverse=True)
    suggestion = {
        "top_interventions": sorted_weights[:5],
        "top_backlog": backlog_sorted[:5],
        "portfolio_families": portfolio.get("families", [])[:12],
        "recent_experiments": experiments[-5:],
        "advice": "Choose a high-priority intervention that either exploits a robust success mechanism or tests an underexplored plausible method family. State a hypothesis before editing code.",
    }
    print(json.dumps(suggestion, indent=2, sort_keys=True))


HARD_BLOCKER_KINDS = {"infra", "benchmark", "user", "data"}


def cmd_record_blocker(args: argparse.Namespace) -> None:
    """Record a hard blocker from the closed list, permitting an early stop.

    A blocker is the ONLY thing (besides iteration >= max_iterations) that lets the
    termination gate pass. It is written into state so the early stop is auditable.
    """
    root = Path(args.root)
    paths = state_paths(root)
    state = read_json(paths["state"], {})
    blocker = {
        "kind": args.kind,          # infra | benchmark | user | data
        "detail": args.detail,      # exact error / instruction
        "recorded_at": now(),
        "iteration_at_block": int(state.get("iteration", 0)),
    }
    state["blocker"] = blocker
    state["updated_at"] = now()
    write_json(paths["state"], state)
    print(json.dumps({"blocker_recorded": blocker}))


def cmd_clear_blocker(args: argparse.Namespace) -> None:
    """Remove a previously recorded blocker (e.g. infra was restored)."""
    root = Path(args.root)
    paths = state_paths(root)
    state = read_json(paths["state"], {})
    had = state.pop("blocker", None)
    state["updated_at"] = now()
    write_json(paths["state"], state)
    print(json.dumps({"blocker_cleared": bool(had)}))


def cmd_stop_check(args: argparse.Namespace) -> None:
    """Machine-checkable termination gate.

    Exit code 0  => you MAY stop / finalize (iteration >= max_iterations OR a hard
                    blocker is recorded).
    Exit code 3  => you MUST NOT stop; keep iterating (floor not reached, no blocker).
    Exit code 2  => state is missing/uninitialized (treated as must-not-stop).

    Also reports drift between the counter and the number of substantive ledger records.
    """
    import sys
    root = Path(args.root)
    paths = state_paths(root)
    state = read_json(paths["state"], {})
    if not state:
        print(json.dumps({"error": "no state.json found; run `init` first", "may_finalize": False}))
        sys.exit(2)

    experiments = read_jsonl(paths["experiments"])
    substantive = count_substantive(experiments)
    counter = int(state.get("iteration", 0))
    max_iter = int(state.get("max_iterations", 0))
    blocker = state.get("blocker")
    blocker_valid = bool(blocker) and str(blocker.get("kind")) in HARD_BLOCKER_KINDS

    floor_reached = counter >= max_iter and max_iter > 0
    may_finalize = floor_reached or blocker_valid

    drift = counter != substantive
    remaining = max(0, max_iter - counter)

    verdict = {
        "may_finalize": may_finalize,
        "iteration": counter,
        "max_iterations": max_iter,
        "remaining_to_floor": remaining,
        "floor_reached": floor_reached,
        "hard_blocker": (blocker.get("kind") if blocker_valid else "none"),
        "blocker_detail": (blocker.get("detail") if blocker_valid else None),
        "substantive_record_count": substantive,
        "counter_matches_ledger": not drift,
        "self_audit_line": f"STOP-GATE: iteration={counter}/{max_iter}; hard_blocker={(blocker.get('kind') if blocker_valid else 'none')}; may_finalize={'yes' if may_finalize else 'no'}",
    }
    if drift:
        verdict["warning"] = (
            f"counter ({counter}) != substantive ledger records ({substantive}); "
            "reconcile before trusting the gate."
        )
    if not may_finalize:
        verdict["directive"] = (
            f"DO NOT STOP. {remaining} substantive iteration(s) remain before the floor. "
            "Replenish the backlog (diagnosis / ablation / orthogonal-information candidate / "
            "complete refactor / literature or data search / simplification / derivation) and continue."
        )
    print(json.dumps(verdict, indent=2, sort_keys=True))
    sys.exit(0 if may_finalize else 3)


def cmd_summary(args: argparse.Namespace) -> None:
    root = Path(args.root)
    paths = state_paths(root)
    state = read_json(paths["state"], {})
    experiments = read_jsonl(paths["experiments"])
    feedback = read_jsonl(paths["feedback"])
    portfolio = read_json(paths["portfolio"], {"families": []})
    print(f"# tusoskill run summary\n")
    print(f"Run ID: {state.get('run_id')}")
    print(f"Task: {state.get('task')}")
    print(f"Iteration: {state.get('iteration', 0)} / {state.get('max_iterations')}")
    print(f"Best: {state.get('best_candidate_id')} score={state.get('best_score')} ({state.get('metric_direction')})")
    print(f"Experiments: {len(experiments)} | Feedback items: {len(feedback)}\n")
    print("## Method families")
    for fam in portfolio.get("families", []):
        print(f"- {fam.get('name')}: attempts={fam.get('attempts')} best={fam.get('best_candidate_id')} score={fam.get('best_score')}")
    print("\n## Recent experiments")
    for r in experiments[-10:]:
        print(f"- iter {r.get('iteration')} {r.get('candidate_id')} {r.get('intervention')} {r.get('decision')} score={r.get('score')} — {r.get('summary')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="State helper for tusoskill")
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("init")
    s.add_argument("--root", required=True)
    s.add_argument("--run-id")
    s.add_argument("--task", default="method development")
    s.add_argument("--metric", default="primary")
    s.add_argument("--direction", choices=["higher", "lower"], default="higher")
    s.add_argument("--max-iterations", type=int, default=50)
    s.add_argument("--max-wall-hours", type=float, default=24.0)
    s.add_argument("--benchmark-version", default="unversioned")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("add-experiment")
    s.add_argument("--root", required=True)
    s.add_argument("--candidate-id", required=True)
    s.add_argument("--parent-id")
    s.add_argument("--iteration", type=int)
    s.add_argument("--method-family", default="unspecified")
    s.add_argument("--intervention", required=True)
    s.add_argument("--component", default="")
    s.add_argument("--hypothesis", default="")
    s.add_argument("--edited-files", nargs="*", default=[])
    s.add_argument("--score", type=float)
    s.add_argument("--runtime-sec", type=float)
    s.add_argument("--memory-mb", type=float)
    s.add_argument("--decision", choices=["accept", "reject", "archive", "infrastructure"], required=True)
    s.add_argument("--non-substantive", action="store_true", help="Record but do NOT advance the iteration counter (e.g. pure infra retry).")
    s.add_argument("--summary", default="")
    s.add_argument("--guardrails", default="")
    s.add_argument("--leakage-audit", default="")
    s.add_argument("--next-action", default="")
    s.add_argument("--benchmark-version", default="")
    s.add_argument("--feedback", default="")
    s.set_defaults(func=cmd_add_experiment)

    s = sub.add_parser("add-backlog")
    s.add_argument("--root", required=True)
    s.add_argument("--priority", type=float, default=1.0)
    s.add_argument("--method-family", default="exploratory")
    s.add_argument("--intervention", default="representation")
    s.add_argument("--component", default="")
    s.add_argument("--hypothesis", required=True)
    s.add_argument("--test", default="")
    s.add_argument("--source", default="manual")
    s.set_defaults(func=cmd_add_backlog)

    s = sub.add_parser("add-feedback")
    s.add_argument("--root", required=True)
    s.add_argument("--type", default="insight")
    s.add_argument("--component", default="")
    s.add_argument("--evidence", required=True)
    s.add_argument("--recommendation", default="")
    s.add_argument("--similar-ideas", default="")
    s.add_argument("--avoid", default="")
    s.set_defaults(func=cmd_add_feedback)

    s = sub.add_parser("add-data-source")
    s.add_argument("--root", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--purpose", default="")
    s.add_argument("--provenance", default="")
    s.add_argument("--version", default="")
    s.add_argument("--license", default="")
    s.add_argument("--path", default="")
    s.add_argument("--join-keys", default="")
    s.add_argument("--coverage", default="")
    s.add_argument("--leakage-assessment", default="")
    s.add_argument("--ablation-result", default="")
    s.set_defaults(func=cmd_add_data_source)

    s = sub.add_parser("add-derivation")
    s.add_argument("--root", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--component", default="")
    s.add_argument("--assumptions", default="")
    s.add_argument("--objective", default="")
    s.add_argument("--properties", default="")
    s.add_argument("--complexity", default="")
    s.add_argument("--test-plan", default="")
    s.set_defaults(func=cmd_add_derivation)

    s = sub.add_parser("suggest-next")
    s.add_argument("--root", required=True)
    s.set_defaults(func=cmd_suggest_next)

    s = sub.add_parser("stop-check", help="Termination gate: exit 0 only if iteration>=max OR a hard blocker is recorded; exit 3 => keep iterating.")
    s.add_argument("--root", required=True)
    s.set_defaults(func=cmd_stop_check)

    s = sub.add_parser("record-blocker", help="Record a hard blocker (closed list) that permits an early stop.")
    s.add_argument("--root", required=True)
    s.add_argument("--kind", choices=sorted(HARD_BLOCKER_KINDS), required=True, help="infra | benchmark | user | data")
    s.add_argument("--detail", required=True, help="Exact error message or user instruction justifying the stop.")
    s.set_defaults(func=cmd_record_blocker)

    s = sub.add_parser("clear-blocker", help="Remove a previously recorded blocker (e.g. infra restored).")
    s.add_argument("--root", required=True)
    s.set_defaults(func=cmd_clear_blocker)

    s = sub.add_parser("summary")
    s.add_argument("--root", required=True)
    s.set_defaults(func=cmd_summary)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
