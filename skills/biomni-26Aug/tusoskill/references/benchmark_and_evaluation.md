# Benchmark and evaluation protocol

A benchmark is a contract. Freeze it before repeated optimization.

## Required benchmark card fields

- Task name and biological objective.
- Input files and output format.
- Primary metric and whether higher or lower is better.
- Tie-breakers and guardrails.
- Split strategy and random seeds.
- Data access boundaries and protected files.
- Baseline command and candidate command.
- Time and memory limits.
- Allowed auxiliary data policy.
- Evaluator version and hash of protected files.

## Sanity checks

Before serious optimization, verify:

- Constant predictions perform poorly or as expected.
- Shuffled labels break signal.
- Train-only preprocessing is enforced.
- Duplicate or near-duplicate samples are handled within split policy.
- The evaluator fails safely on malformed outputs.
- Runtime and memory measurements are captured.

## Repeated evaluation

If stochasticity or small data make metrics noisy, repeat runs over seeds or folds. Do not promote a candidate on a one-off lucky run. Report mean, variance, and worst-case guardrails when relevant.

## Proxy benchmarks

Use cheap proxy benchmarks to explore ideas quickly, but periodically re-evaluate leaders on the full frozen benchmark. A proxy is valid only if it correlates with the target objective or provides a specific diagnostic.
