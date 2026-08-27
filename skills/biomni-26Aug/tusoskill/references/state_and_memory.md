# State and feedback memory

Durable memory lets Biomni avoid repeating mistakes and build on successes.

## Core files

- `state/state.json`: run metadata, budget, current iteration, best candidate, benchmark version.
- `state/strategy_weights.json`: adaptive priorities for intervention types and components.
- `state/diversity_portfolio.json`: active method families and their best evidence.
- `state/backlog.jsonl`: queued hypotheses.
- `experiments/results.jsonl`: evaluated candidates.
- `feedback/feedback.jsonl`: lessons, failed patterns, promising mechanisms, risks.
- `data_sources/data_sources.jsonl`: auxiliary resources and provenance.
- `derivations/derivations.jsonl`: derivation summaries and assumptions.

## Iteration counter and termination

- `state.json.iteration` is the number of **substantive** iterations completed. It is derived from `experiments/results.jsonl` on every `add-experiment`, so it self-heals and cannot drift down. A record is substantive iff `decision ∈ {accept, reject, archive}` and it is not flagged `non_substantive`. Records with `decision == infrastructure` or `--non-substantive` are kept for the audit trail but do **not** advance the counter (1 substantive candidate = exactly +1).
- `state.json.max_iterations` is the floor (default 50). Finalizing before `iteration >= max_iterations` is only allowed when a hard blocker is recorded.
- `state.json.blocker` (optional) records an authorized early stop: `{kind, detail, recorded_at, iteration_at_block}` where `kind ∈ {infra, benchmark, user, data}`. Written by `record-blocker`, removed by `clear-blocker`.

## Termination gate contract

`biomni_method_state.py stop-check --root <run_dir>`:

- Exit `0` (may finalize) iff `iteration >= max_iterations` **or** a valid `blocker` is present.
- Exit `3` (must keep iterating) otherwise; prints `remaining_to_floor` and a directive.
- Exit `2` if state is uninitialized.
- Reports `counter_matches_ledger`; if false it emits a drift warning so the counter can be reconciled against the substantive ledger records before the gate is trusted.
- Always prints `self_audit_line`: `STOP-GATE: iteration=<N>/<max>; hard_blocker=<none|infra|benchmark|user|data>; may_finalize=<yes|no>`.

## Feedback item fields

- Type: success, failure, risk, data insight, diagnostic insight, ablation insight, speed insight, theory insight.
- Component affected.
- Evidence and metric context.
- Recommendation.
- Whether similar changes should be tried.
- Whether the idea should be avoided.

## Probability updates

Increase priority for mechanisms that produce reproducible gains or actionable diagnostics. Reduce priority for directions that fail for scientific reasons. Do not penalize infrastructure failures as scientific failures. Maintain exploration floors for plausible under-tested directions.
