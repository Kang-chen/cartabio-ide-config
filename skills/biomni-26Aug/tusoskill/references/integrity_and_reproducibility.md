# Integrity and reproducibility

A method is not credible unless it can be reproduced and audited.

## Leakage checks

Check for leakage through labels, file paths, sample IDs, ordering, duplicate samples, preprocessing before splitting, auxiliary data joins, model selection on final test data, and accidental access to protected files.

## Reproducibility checks

- Set and record random seeds.
- Record package versions and system assumptions.
- Save benchmark and data manifests.
- Keep exact commands for baseline, candidates, and final method.
- Re-run finalists from a clean checkout or clean working directory.
- Verify protected file hashes when available.

## Reporting

Report improvements with enough context: metric direction, baseline, final score, uncertainty, guardrail metrics, runtime, memory, number of attempts, and any known limitations. Do not overstate results from noisy or incomplete evaluations.
