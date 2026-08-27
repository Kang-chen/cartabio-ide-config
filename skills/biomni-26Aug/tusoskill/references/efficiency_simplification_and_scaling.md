# Efficiency, simplification, and scaling

Efficiency is part of method quality.

## Profile first

Measure runtime and peak memory by stage: data loading, preprocessing, feature generation, training, inference, post-processing, serialization, and evaluation.

## Common improvements

- Vectorize Python loops.
- Use sparse arrays for sparse biology matrices.
- Cache immutable train-safe features.
- Avoid repeated parsing of large files.
- Batch inference and avoid excessive small file writes.
- Reduce feature dimensionality with safe filtering or aggregation.
- Replace heavyweight models with simpler equivalents when performance is similar.
- Use early stopping and validation-only model selection.
- Precompute public priors once with a manifest.
- Limit dependencies to packages that materially help.

## Simplification rules

After a method improves, try to remove parts that may be unnecessary. Favor automatic hyperparameter estimation, fewer flags, smaller code paths, and clearer interfaces. Keep comments that explain biological or mathematical rationale; remove debug scaffolding.
