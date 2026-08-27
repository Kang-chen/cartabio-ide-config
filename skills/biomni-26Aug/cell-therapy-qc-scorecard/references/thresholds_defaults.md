# Default Thresholds & Provenance

These are the default GREEN / AMBER / RED cut points applied by
`build_scorecard.py`. **They are defaults, not regulatory standards.** Lot-release
acceptance criteria for a cell-therapy product are product-specific and must be set
with the sponsor. The exact thresholds used in any run are written to
`06_thresholds_reference.csv` and printed in the PDF so they are auditable, and any
of them can be overridden via `cfg['thresholds']`.

## Default cut points

| Module / metric | GREEN | AMBER | RED | Direction |
|-----------------|-------|-------|-----|-----------|
| **A. Target purity** | ≥ 90% | 75–90% | < 75% | higher better |
| **B. Residual pluripotency** | < 0.01% | 0.01–0.1% | > 0.1% | lower better |
| **C. Off-target lineage** | < 2% | 2–10% | > 10% | lower better |
| **D. Target maturity** | ≥ 60% | 40–60% | < 40% | higher better |
| **E. Cell retention** | ≥ 80% | 60–80% | < 60% | higher better |
| **E. Cross-species contamination** | ≤ 1% | 1–5% | > 5% | lower better |
| **E. Median mitochondrial %** | ≤ 10% | 10–20% | > 20% | lower better |
| Contamination (true, target-negative) | ≤ 1% | 1–5% | > 5% | lower better |

**Overall call per unit = worst active module.** One RED fails the lot.

Module E's single call is the worst of its available sub-metrics (retention,
cross-species contamination, mito).

## Provenance / rationale

- **Purity (90/75):** typical expectation that a releasable engineered product is
  predominantly the intended cell type; 75% is a common "needs investigation" floor.
- **Residual pluripotency (0.01/0.1):** anchored to the *analytical* sensitivity of
  orthogonal residual-iPSC assays (ddPCR / highly-sensitive qPCR reach ~0.001–0.01%).
  Because scRNA-seq cannot reach that LOD, GREEN here means "below scRNA-seq
  detection at this depth/cell number," to be confirmed by an orthogonal assay — it
  is not a certificate of absence. There is no single universal numeric standard;
  criteria are set per product with the sponsor.
- **Off-target (2/10):** small percentages of unwanted lineages are common; >10% is
  a substantial mis-differentiation signal.
- **Maturity (60/40):** a majority-mature product is a reasonable default potency
  expectation; product-specific potency assays should refine this.
- **Technical QC:** retention ≥80% follows the scanpy-core "aim for >70% retention"
  guidance with a stricter release bar; mito ≤10% is a standard viability proxy
  (raise for mito-rich tissues); cross-species ≤1% reflects that a well-sorted human
  product should carry negligible mouse reads.

## Overriding thresholds

Pass a partial `thresholds` dict to `build_config` (or edit `cfg['thresholds']`
before `build_scorecard`). Each entry is
`{"green": <edge>, "red": <edge>, "direction": "high_good"|"low_good"}`:

```python
cfg = build_config(..., thresholds={
    "purity_pct":      {"green": 95.0, "red": 85.0, "direction": "high_good"},
    "offtarget_pct":   {"green": 1.0,  "red": 5.0,  "direction": "low_good"},
})
```

Always state in the report (the template does this automatically) that thresholds
are defaults requiring sponsor sign-off, and recommend orthogonal confirmatory
assays for any AMBER/RED module.
