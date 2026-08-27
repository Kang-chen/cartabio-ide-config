# Diagnostics, ablations, and error analysis

Diagnostics explain what to do next. Ablations prove whether a component matters.

## Diagnostics

Run diagnostics when improvement stalls, a result is surprising, a subgroup fails, calibration regresses, runtime becomes excessive, or data assumptions are uncertain.

Useful diagnostic outputs:

- Shape, dtype, missingness, ranges, and distribution summaries.
- Label balance, outcome prevalence, censoring, and subgroup counts.
- Error tables by biologically meaningful strata.
- Top false positives and false negatives with safe metadata only.
- Learning curves and validation curves.
- Calibration bins and uncertainty-error relationships.
- Feature or pathway importance summaries.
- Runtime and memory profiles by pipeline stage.
- A concrete next action.

Keep diagnostics compact. Do not dump protected labels or large raw data into logs.

## Ablations

Ablate one meaningful factor at a time when feasible. Examples:

- Remove a feature family or replace it with zeros.
- Disable an auxiliary data source.
- Replace a learned component with a simple baseline.
- Remove a regularizer, calibration step, post-processing rule, or ensemble member.
- Reduce capacity, feature count, or training time.
- Swap a biological prior for an unstructured equivalent.

Interpretation:

- If ablation hurts robustly, the component is likely useful.
- If ablation improves or does not change performance, simplify.
- If ablation changes only runtime, decide whether speed or accuracy matters more.
- If ablation has high variance, repeat or diagnose data dependence.
