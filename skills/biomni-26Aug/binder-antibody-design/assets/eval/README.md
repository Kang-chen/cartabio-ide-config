# Eval

Regression tests for the two provenance safeguards in this skill:

1. **Structure facts are fetched, not typed.** `fetch_structure_metadata.py` reads
   resolution / experimental method / deposition date from the RCSB entry and stamps
   provenance; the report renders those or "unavailable" and never a hand-typed value.
2. **The construct span is derived, not restated.** `build_report.py` takes the reported
   residue range from `construct_scope.json` and explains nominal-vs-realised; a
   consistency gate rejects hand-typed structure facts / spans and scope-vs-metrics
   disagreement.

## Run

```
python assets/eval/test_fixes.py          # standalone runner (prints PASS/FAIL)
pytest assets/eval/test_fixes.py -q       # or under pytest
```

Offline tests use `fixtures/5o45_rcsb.json` (a trimmed real RCSB payload). The single
live test (`test_live_fetch_5o45_returns_099`) hits the RCSB Data REST API and skips
cleanly when there is no network.
