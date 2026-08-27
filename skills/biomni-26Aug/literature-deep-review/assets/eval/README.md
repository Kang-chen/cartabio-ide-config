# Tests

```bash
cd biomni_skills/skills/literature-deep-review
python -m pytest tests/ -q
```

Needs the runtime dependencies from `scripts/install.sh`, including
`pdfplumber`, `pypdfium2`, and `pySBD`. Tests that read the
finished PDF's text or font table shell out to poppler (`pdftotext`,
`pdffonts`) and skip themselves if it is absent. Parser crop tests import the
real PDF parser, so install the declared parsing stack before running the full
suite.

## What is being tested

Every case here is a defect that actually shipped in a delivered report. The
strings in the parametrized lists are verbatim from those PDFs, so a regression
shows up as the same wrong output rather than as an abstract assertion failure.

| File | Guards |
|---|---|
| `test_citations.py` | author-year inline citations instead of DOIs, reference ordering, author/journal/title normalization, DOI-vs-year conflicts, axis display labels |
| `test_rendering.py` | Unicode font coverage (the black-box and dropped-Greek defects), canonical claim IDs, and what lands in the finished PDF |
| `test_figure_selection.py` | claim-driven figure choice: starvation, clustering, review schematics, the calibrated relevance thresholds (`SHIPPED_PAIRS`) |
| `test_scientific_semantics.py` | direction reversal, model/outcome escalation, and population-scope overreach in prose and infographic assertions |
| `test_evidence_taxonomy.py` | publication type, anchor depth, claim relationship, and independent-study labels remain orthogonal |
| `test_quote_quality.py` | five kinds of extraction damage, hypothetical anchors, `primary` requiring a stated result, section-label recovery |
| `test_structure.py` | required narratives, required contradiction axis, measured limitations, Contents, deliverable parity, the figure-prefix collision |
| `test_parallel_workflow.py` | bounded 8/16-worker provider pools, deterministic ordering/cache behavior, shared Biomni worker exchange, complete blinded entailment, direct grounded narrative assembly |
| `test_corpus_ledger.py` | uncapped broad selection completeness, explicit exclusions, prior-run reconciliation, required search axes, and global transient recovery |
| `test_managed_machine_shards.py` | adaptive multi-machine planning, object-store publication/materialization, exact per-task paper accounting, merge integrity, and retry-wave planning |
| `test_object_exchange.py` | write-once bundle transport without rename, completion-marker enforcement, checksum rejection, and local extraction |
| `test_delivery.py` | required artifact copy, source/destination digest revalidation, ordered one-command finalization, stale delivery detection, and final attestation |
| `test_reconciliation.py` | canonical count refresh using shared support policy, idempotent writes, cross-artifact row equality, managed-run receipts, and selected-figure dispositions |
| `test_slc33a1_replay.py` | uploaded-run replay for count drift, mixed panels, unresolved transient retries, and selected/exported figure loss |
| `test_pdf_structure.py` | strict acceptance of valid PDFs and rejection of truncated PDFs before delivery |
| `test_parse_quality.py` | zero-body, figure-only, and recovery-candidate parse classification |
| `test_evidence_lineage.py` | one stable accepted/rejected/duplicate disposition per raw adjudication |
| `test_runtime_metrics.py` | additive active runtime across resumed invocations rather than last-call overwrite |
| `test_skill_provenance.py` | deployment-stamped Git/package identity, immutable origin capture, audited committed upgrades, and post-capture drift rejection |
| `test_infographic.py` | real GenerateImage provenance, structured visual QA, and Fab-variable-region rather than Fc antibody binding |

## The fixture

`fixture_run.py` builds a synthetic run root carrying each defect at once — a
non-WinAnsi codepoint, an evidence file whose row order is not the rendered claim
order, a claim-id sequence with a hole, a figure whose caption has nothing to do
with its claim, and more. It is a *real* run layout (`corpus/`, `evidence/`,
`fulltext/parsed/`, `deliverables/`), so the builders and the production gates
run against it unmodified.

Two flags turn defects on that would otherwise break unrelated tests:
`front_matter_locator=True` reproduces the forbidden locator, and
`with_narratives=False` reproduces the quote-catalogue run.

Note `_png()` builds its PNGs rather than hard-coding bytes: the builders run
`PIL.Image.verify` and skip anything that fails, so a byte-literal with a bad CRC
makes every figure silently vanish and the figure tests pass by testing nothing.
That happened during development.
