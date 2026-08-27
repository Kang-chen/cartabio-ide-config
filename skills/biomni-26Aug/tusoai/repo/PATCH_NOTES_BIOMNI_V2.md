# Biomni v2 source hardening

This bundled source starts from the user-supplied TusoAI revision and adds the
following reliability fixes for Biomni execution:

- data-only portable file copies replace `shutil.copy` in evaluator workspaces;
  chmod/utime failures on object-store FUSE mounts no longer abort a run;
- shared-history writes use complete-file replacement and a hybrid directory +
  advisory lock, with stale-lock recovery;
- single-machine history retains its original schema, while multi-machine
  records receive `run_id` and `history_seq` deduplication fields;
- the dynamic-repository import guard is loaded explicitly through an evaluator
  bootstrap, preventing hard-coded original-repository imports from silently
  bypassing candidate edits;
- evaluator output must contain exactly one finite standalone
  `tuso_evaluate: <number>` line;
- the shadowed legacy optimizer is retained under an explicit internal name so
  source-inspection tools see only one public `optimize()` definition;
- concurrent shared-history and portable-copy regressions are covered by tests.

Run `python -m pytest -q` from this repository root to validate the bundle.
