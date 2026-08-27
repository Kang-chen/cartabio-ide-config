# Data integration and search

New data, embeddings, and curated knowledge can transform a method, especially with small or noisy datasets. Search for auxiliary information when the task suggests biological structure beyond the provided files.

## Integration workflow

1. Define the missing signal: labels, priors, features, covariates, graph structure, negative controls, embeddings, or calibration data.
2. Search available Biomni resources, public biomedical databases, package datasets, and literature-derived resources.
3. Record provenance, version, license, download path, and preprocessing steps.
4. Verify join keys and coverage.
5. Check leakage: the resource must not encode hidden benchmark labels or post-split information.
6. Add the resource behind a clean feature or prior interface.
7. Run ablation against the same pipeline without the resource.
8. Cache compact processed artifacts with manifests rather than many small files.

## Safe integration patterns

- Train-only fit transforms, then apply to validation/test.
- Predefined public priors independent of benchmark labels.
- Frozen pretrained representations whose training corpus is acceptable for the task.
- Weak labels or pseudo-labels kept separate from validation labels.
- Resource coverage indicators so missing values are explicit.

## Risk patterns

- Preprocessing all samples jointly before splitting.
- External datasets that include benchmark labels or target outcomes.
- Joins by unstable names that accidentally encode labels.
- High-dimensional features added without regularization or ablation.
- Large downloads that do not fit the runtime budget.
