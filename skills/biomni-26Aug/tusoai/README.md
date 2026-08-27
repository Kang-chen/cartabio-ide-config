# Biomni TusoAI v2 skill package

This archive contains a rewritten Biomni skill for persistent, cooperative
multi-machine TusoAI execution and a hardened copy of the uploaded TusoAI source.

Start with `SKILL.md`. The skill resolves its installed root dynamically and uses
one canonical set of operation-specific instructions under `skills/`.

Key package contents:

- `SKILL.md` — autonomous phase/state orchestration contract;
- `skills/` — evaluator, task construction, data, cluster, monitoring, selection,
  recovery, and troubleshooting procedures;
- `scripts/` — evaluator audit, task-spec validation, shared-filesystem probe,
  atomic state/event management, and history checkpointing;
- `templates/` — task/context, run-state, cluster, task factory, bundle builder,
  and per-node launch templates;
- `repo/` — bundled TusoAI source and tests with Biomni v2 hardening;
- `VERSION` — skill package version.

Validation performed before packaging:

```bash
cd repo
python -m pytest -q
# 18 passed
```

The shared-filesystem probe must still be run concurrently on the actual Biomni
machines and mount before a production multi-machine launch.
