# Biomni runtime and integration

## Contents

- Runtime contract
- Tool registration
- Dependency deployment
- Artifact handling
- Failure behavior

## Runtime contract

The skill separates concise know-how (`SKILL.md`) from deterministic Python tools
(`scripts/biomni_tools.py`). Biomni should call three functions:

- `inspect_adme_dataset`
- `train_adme_model`
- `predict_adme_model`

Each function accepts JSON-compatible arguments and returns a small JSON-compatible status
object containing summaries and artifact paths. Detailed tables remain files rather than being
placed in the agent context.

## Tool registration

Make `scripts/` importable in the Biomni tool environment, import the functions, and register
them through `A1.add_tool`. Use `tool_descriptions()` to populate Biomni's parallel tool-
description registry when contributing the functions to `biomni/tool/`.

Keep output directories inside the current Biomni workspace. Do not allow arbitrary shell
fragments in tool arguments.

## Dependency deployment

Use Python 3.11 or 3.12 and resolve `pyproject.toml` into a pinned image or isolated environment.
The standard runtime uses ChEMBL Structure Pipeline, RDKit, Molfeat, Splito, scikit-learn,
XGBoost, and MAPIE. Chemprop is an optional future/thorough profile and is not invoked by the
standard tools or included in their runtime dependencies.

This package deliberately ships `pyproject.toml` **and `uv.lock`** — an exception to the usual
rule that skills carry no dependency manifests. The lockfile pins every transitive dependency to
an exact, audited version so the runtime is reproducible, and it is the manifest from which the
isolated environment is built. Do not delete `uv.lock`; without it the pinned environment cannot
be reconstructed.

Call `dependency_status()` (CLI `preflight`) before a run. It enforces two rules:

1. **Isolation guard (refuse to install into the live session).** If a virtual-environment marker
   (`VIRTUAL_ENV` or `UV_PROJECT_ENVIRONMENT`) resolves inside the interactive session workspace
   (`/workspace`), preflight returns `status: unsafe_environment` with a non-zero CLI exit instead
   of proceeding. Installing or running the pinned stack in the live agent session mutates the
   running interpreter and can silently downgrade the session's own packages (e.g. `uv` targeting
   `/workspace/.venv`). Build the runtime in an isolated environment from `pyproject.toml`/`uv.lock`
   — a dedicated prefix outside the session, or a container — and run the skill there. Detection
   reads only environment variables (so it is deterministic and testable) and is overridable with
   `ADME_SKILL_ACK_SESSION_ENV=1` for a purpose-built isolated environment that intentionally lives
   under the session workspace.
2. **Presence check.** In an isolated environment preflight returns `status: missing_dependencies`
   (listing the absent packages) when any standard-profile package is not importable, and
   `status: ready` when all are present. Route execution to the pinned environment rather than
   installing packages ad hoc.

Never run `pip install` from an active scientific task.

## Artifact handling

Expose `model_card.md`, `evaluation.json`, prediction CSVs, and manifests through Biomni's
Results surface. Keep `model_bundle.joblib` as an opaque versioned artifact; only load bundles
created by this trusted runtime because pickle/joblib is not safe for untrusted files.

Input datasets are hashed but not copied into the bundle by default. This avoids silently
duplicating confidential assay data. A run manifest stores resolved paths, hashes, dependency
versions, configuration, and artifact hashes.

## Failure behavior

Return structured errors from the Biomni wrappers. Scientific audit blockers are not runtime
errors: return `status: blocked`, the audit path, and actionable blocker codes. Runtime errors
return `status: error`, exception type, and a concise message. Do not retry by weakening the
assay schema, changing the split to random, or replacing censored labels with their limits.
