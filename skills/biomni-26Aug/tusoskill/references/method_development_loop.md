# Method development loop

This protocol turns an open-ended method-building request into a controlled scientific search.

## Entry checklist

1. Restate the biological or biomedical goal.
2. Identify the candidate method interface: inputs, outputs, allowed files, command-line entry point, and expected artifacts.
3. Define primary metric, metric direction, guardrails, and time/memory limits.
4. Run the baseline and a trivial sanity candidate.
5. Create a method blueprint and initial diversity portfolio.
6. Start an append-only experiment ledger.

## Adaptive loop

Each iteration should have one clearly stated hypothesis. The hypothesis may target the model, representation, objective, data, inference, robustness, speed, or evaluation setup. Implement the intervention, evaluate it, diagnose surprising behavior, and update future priorities.

Useful accept/reject logic:

- Accept when the primary metric improves beyond expected noise and guardrails do not regress materially.
- Archive as promising when it improves a subgroup, insight, speed, or robustness but is not yet globally superior.
- Reject when it fails the primary metric, violates integrity, increases complexity without benefit, or is not reproducible.
- Mark as infrastructure when the run failed due to evaluator, environment, or data-loading problems rather than scientific content.

## Diversity portfolio

Keep several method families alive. Families can differ by representation, model class, data source, objective, inference rule, or biological prior. Mutating only the current best risks local optima. Allocate some attempts to underexplored plausible families until evidence rules them out.

## Search when stuck

When progress stalls, rotate through:

- Deep error analysis.
- Data quality and leakage checks.
- New biological priors or public resources.
- New model formulation or derivation.
- Simplification of bloated components.
- Runtime profiling and acceleration.
- Reframing the objective or validation proxy.
- Larger codebase restructuring if the current design blocks good ideas.


## Budget discipline

The default active budget is 50 substantive iterations or 24 wall-clock hours. Continue until a budget bound is reached or a hard blocker occurs. Do not stop merely because a candidate improved, several candidates failed, the benchmark is noisy, the backlog is thin, or the next ideas seem incremental. Convert those conditions into the next iteration: diagnose, ablate, profile, search for data or priors, simplify, derive, or try a different algorithmic family.

### Counting substantive iterations

One substantive candidate advances the counter by **exactly +1**. A record counts iff its decision is `accept`, `reject`, or `archive`. Records marked `infrastructure` (evaluator/environment/data-loading failures) or passed with `--non-substantive` (pure retries) do **not** advance the counter. The counter is derived from the append-only ledger by `biomni_method_state.py`, so it is self-healing: a manual `--iteration` value is advisory only and can never stall the floor. (The historical 6/50 stop was caused by a `max(iteration, current)` stall on a manual lower value; that path no longer exists.)

### Hard blockers (closed list — the ONLY early-stop reasons)

An early stop before the iteration floor is permitted **only** for one of these four conditions:

1. **infra** — infrastructure/compute is exhausted or unavailable and cannot be restored (e.g. GPU quota gone for the session, sandbox cannot be provisioned).
2. **benchmark** — the benchmark/evaluator is broken and unrepairable within scope (scoring code errors that you cannot fix, missing scoring dependencies).
3. **user** — the user explicitly instructed you to stop.
4. **data** — required data is genuinely unavailable (dataset cannot be obtained, access denied) and no substitute exists.

The following are **NOT** hard blockers and must be converted into the next iteration, never used to stop: "converged", "diminishing returns", "plateau", "no credible path", "near-duplicate variants", "would burn compute", "backlog thin", "benchmark noisy", "already beats baseline/SOTA", "good enough".

### Machine-checkable termination gate

Before finalizing, you MUST pass the gate:

```bash
python scripts/biomni_method_state.py stop-check --root <run_dir>
```

- Exit `0` => you MAY finalize (`iteration >= max_iterations`, OR a hard blocker is recorded).
- Exit `3` => you MUST keep iterating; the printed `remaining_to_floor` tells you how many substantive iterations are left.
- The command also emits a `self_audit_line` of the form `STOP-GATE: iteration=<N>/<max>; hard_blocker=<none|infra|benchmark|user|data>; may_finalize=<yes|no>` — paste it before any finalize step.

To stop early on a genuine hard blocker, first record it (this makes the early stop auditable and lets the gate pass):

```bash
python scripts/biomni_method_state.py record-blocker --root <run_dir> --kind <infra|benchmark|user|data> --detail "<exact error or instruction>"
```

If a blocker is later resolved (e.g. infra restored), clear it with `clear-blocker` and resume iterating.
