#!/usr/bin/env python3
"""Create a benchmark scaffold for tusoskill runs."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

BENCHMARK_TEMPLATE = """# Benchmark card

## Task
- Name: {task}
- Biological objective: {objective}
- Inputs: {inputs}
- Outputs: {outputs}
- Candidate command: {candidate_command}
- Baseline command: {baseline_command}

## Metrics
- Primary metric: {metric}
- Direction: {direction}
- Tie-breakers: {tie_breakers}
- Guardrails: {guardrails}

## Data protocol
- Train data: {train_data}
- Validation/development data: {validation_data}
- Final/test data access policy: {test_policy}
- Split method and seeds: {splits}
- Protected files: {protected_files}
- Allowed auxiliary data: {auxiliary_policy}

## Constraints
- Runtime limit: {runtime_limit}
- Memory limit: {memory_limit}
- File-count limit: {file_count_limit}
- Package policy: {package_policy}

## Sanity checks
- Constant output: pending
- Shuffled-label check: pending
- No-signal baseline: pending
- Protected hash verification: pending

## Versioning
- Evaluator version/hash: {evaluator_version}
- Created: {created_at}
- Last updated: {created_at}
"""

SMOKE_EVALUATOR = """#!/usr/bin/env python3
from __future__ import annotations

import json

# Smoke evaluator: replace with the task-specific protected evaluator before real optimization.
# It only confirms that the benchmark command path works.

if __name__ == "__main__":
    print(json.dumps({"smoke_metric": 0.0, "smoke_ok": 1.0}))
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--objective", default="")
    p.add_argument("--metric", default="primary")
    p.add_argument("--direction", choices=["higher", "lower"], default="higher")
    p.add_argument("--candidate-command", default="")
    p.add_argument("--baseline-command", default="")
    p.add_argument("--inputs", default="")
    p.add_argument("--outputs", default="")
    p.add_argument("--train-data", default="")
    p.add_argument("--validation-data", default="")
    p.add_argument("--test-policy", default="hidden or protected if present")
    p.add_argument("--splits", default="development split to be documented")
    p.add_argument("--guardrails", default="runtime, memory, leakage, reproducibility")
    p.add_argument("--tie-breakers", default="simplicity, runtime, memory")
    p.add_argument("--protected-files", default="")
    p.add_argument("--auxiliary-policy", default="allowed if provenance and leakage checks are recorded")
    p.add_argument("--runtime-limit", default="task dependent")
    p.add_argument("--memory-limit", default="task dependent")
    p.add_argument("--file-count-limit", default="keep /mnt/results under 10000 files")
    p.add_argument("--package-policy", default="minimize dependencies and record versions")
    p.add_argument("--evaluator-version", default="unversioned")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    values = vars(args)
    values["created_at"] = created
    card = BENCHMARK_TEMPLATE.format(**values)
    (out / "benchmark_card.md").write_text(card, encoding="utf-8")
    manifest = {
        "task": args.task,
        "objective": args.objective,
        "metric": args.metric,
        "direction": args.direction,
        "candidate_command": args.candidate_command,
        "baseline_command": args.baseline_command,
        "created_at": created,
        "evaluator_version": args.evaluator_version,
        "guardrails": args.guardrails,
    }
    (out / "benchmark_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    smoke = out / "evaluator_smoke.py"
    smoke.write_text(SMOKE_EVALUATOR, encoding="utf-8")
    smoke.chmod(0o755)
    print(json.dumps({"benchmark_card": str(out / "benchmark_card.md"), "manifest": str(out / "benchmark_manifest.json"), "smoke_evaluator": str(smoke)}))


if __name__ == "__main__":
    main()
