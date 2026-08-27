# Modes and intake

## Contents

1. Intake sequence
2. Mode defaults
3. Full-text allocation
4. Post-search steering
5. Conditional questions

## 1. Intake sequence

Ask only for information that materially changes search, evidence, governance, or
the deliverable.

Required:

1. "Which outcome do you want: a quick grounded answer, a deep focused review, or
   a broad landscape?"
2. "What ballpark number of full texts should I plan to review: all relevant
   papers available, approximately N (give a number or range), or decide after I
   report search availability?"
3. "What decision should this review inform?"
4. "What scope should I enforce: population/species/model, intervention or
   perturbation, outcomes, date range, and included study designs?"

If the user already supplied an answer, do not ask again. If the user delegates,
choose `quick` and state the defaults once.

The paper-count answer is an intake preference, not necessarily the final
runtime cap. Record `all relevant` as an uncapped preference. Preserve an
approximate number or range in the review brief until the search reports actual
availability. Leave `decide after search` unresolved until the post-search
checkpoint. Only write `config.max_papers` as a positive integer after an exact
ceiling is confirmed; use `null` when the confirmed choice is all relevant
papers.

## 2. Mode defaults

| Setting | quick | deep | broad |
|---|---:|---:|---:|
| Abstract records | 10-15 starting range | 30-50 starting range | Query-dependent; all relevant returned results |
| Full-text selection | Up to 5 by default | Up to 15 by default | Query-dependent; no built-in cap |
| Candidate claims | 5-8 | 10-20 | 3-5 per cluster |
| Retrieved blocks per paper/claim | 2 | 3 | 3 |
| Reserved figure slots per paper/claim | 0 | 2 | 2 |
| Total blocks per claim | 12 | 30 | 40 |
| Claims per model batch | 8 | 8 | 6 |
| Maximum blocks per model batch | 24 | 36 | 40 |
| OCR | off | targeted | targeted |
| Fallback minimum paper figures | 0 | 4 | 6 |
| Delegated figure policy | fixed/no figures | adaptive | adaptive |
| PDF | off | ask | ask |

The source of truth is `MODE_DEFAULTS` in `scripts/evidence_first.py` for the
retrieval settings, `templates/report_contract.json` for fallback figure
minimums, and `ADAPTIVE_BASE_MINIMUM` in `scripts/intake_policy.py` for adaptive
baselines. This table mirrors them and must be updated with them.

Paper counts are not fixed mode ranges. In broad mode, `max_papers=null` means
process every record in the selected set. After each query, report the available
unique-paper count and proposed selection; let the user set any positive
`max_papers` ceiling or accept the full set. Never infer a fixed broad-mode
maximum. Quick/deep defaults are starting caps and may also be overridden with
`--max-papers N`.

The reserved figure slots are additive — they do not compete with the
sentence slots, so raising them cannot starve text evidence. Note the aggregate
consequence: the candidate set per claim is bounded by
`total blocks + (figure slots x papers in play)`, not by `total blocks` alone.

Quick/deep retrieval counts are caps, not quotas; broad has no default cap. Ask
the user for figure density and record `config.figure_count_policy` as `fixed`
or `adaptive`. Fixed choices record `config.minimum_paper_figures` immediately.
Adaptive choices leave it null until the full-text and figure-candidate
inventory is available, then resolve it with
`scripts/intake_policy.py --resolve-adaptive`. Beyond the minimum, include only material, nonredundant
figures. Never lower the evidence rule to hit a count.

## 3. Full-text allocation

Select papers by expected information value, not citation count alone. Cover:

- the paper most directly answering each central claim;
- independent replication when available;
- at least one credible contradiction or null result for central claims;
- distinct populations, model systems, or assay classes relevant to scope;
- foundational work when a recent paper depends on it.

For broad mode, cluster the abstract corpus first and allocate full text
proportionally, with a minimum of one paper per material cluster. Reserve roughly
20% of the full-text budget for outliers, contradictions, and underrepresented
clusters.

## 4. Post-search steering

Run this checkpoint before acquiring full text in `deep` or `broad`. Show a short
decision brief containing:

- cluster names and approximate paper counts;
- the total relevant unique papers available after deduplication and filtering;
- candidate claims by cluster;
- abstract-level disagreements and surprising outliers;
- the proposed full-text set;
- questions the set will not cover.

Relate the available count and proposed set to the intake ballpark, then ask:
"Should I use all relevant selected papers, set an exact maximum, or prioritize
particular clusters, disagreements, or outliers?" In broad mode, continue with
all selected papers if the user does not answer.

Before acquisition, write explicit scope exclusions with reasons and validate
the canonical corpus ledger. An uncapped broad run must select every in-scope
deduplicated record. Also complete `corpus/coverage_matrix.json` for
dependency/causality, direction, mechanism/competing models,
pharmacology/target engagement, biomarker/patient context,
safety/essentiality, translational/clinical evidence, contradictions/nulls,
and combinations. Each axis records actual queries or a reason it is not
applicable. Final statuses are `searched_with_evidence`, `searched_empty`, or
`not_applicable`; a searched-empty axis remains a visible report finding rather
than disappearing because it produced no retained claim.

Record the decision in `run_manifest.json` under `steering` with timestamp,
decision, and rationale. A steering choice may narrow depth without silently
changing the original question or eligibility criteria.

## 5. Conditional questions

The default evidence-access policy is:

- Use any internet PDF the pipeline can retrieve without credentials or
  circumventing an authentication challenge or technical control.
- Also use user-supplied PDFs.

Do not ask an access question unless the user raises a restriction. If they
explicitly require open-access-only sources, filter the paper set before
acquisition. A successfully validated internet PDF is otherwise readable and
OCR-eligible even when licence metadata is absent; this does not grant
permission to reproduce its figures.

Whenever the requested minimum is greater than zero, always ask the separate
figure-text question:

- Captions only, with no OCR (must be an explicit user choice).
- OCR only figures implicated by retrieved captions (recommended for deep/broad).
- OCR all figures (exceptional; slow and noisy).

Always ask figure amount for a PDF report. Offer none/text-only; concise
(usually 1-3, floor 1); standard (usually 4-8, floor 4); comprehensive (usually
8-15+, floor 8); adaptive to corpus/evidence-axis coverage; or a custom exact
minimum. Explicitly say the ranges are expected bands, not caps. Adaptive is
recommended for deep/broad reviews and must not stop at four figures merely
because four was the old fallback.

For adaptive selection, record `figure_count_policy=adaptive` and leave
`minimum_paper_figures=null` during acquisition. After inventory, count
retrieved full texts, populated evidence axes, and distinct materially eligible
figures, then run:

```bash
python scripts/intake_policy.py --manifest RUN/run_manifest.json \
  --resolve-adaptive --full-text-papers N --populated-axes N \
  --eligible-figures N
```

The resolved floor is limited by eligible supply and otherwise takes the
largest of the mode baseline (`quick=1`, `deep=4`, `broad=6`), the populated-axis
count, and one figure per five retrieved full texts. It is not a maximum;
continue adding material, nonredundant figures. The resolver writes
`config.adaptive_figure_resolution` with all inputs, the unlimited desired
minimum, whether supply limited it, and the resolved floor. Report those values
in the completion trace; never summarize an adaptive run only as “4/4.” Record the separate OCR choice
and `ocr_decision_source` in `run_manifest.config`, then run
`scripts/intake_policy.py --manifest ...` before dependency installation. An
unresolved adaptive choice uses targeted OCR. A missing OCR answer is not
permission to choose caption-only processing. If the fixed minimum is zero,
record `ocr=off` and `ocr_decision_source=no_figures`.

For an adaptive policy or a positive fixed minimum, also ask the reuse policy:

- Reuse-cleared figures only (recommended for externally distributed reports).
- Include accessible figures at the user's direction, even when recorded reuse
  rights are unknown or restricted.

Record `figure_reuse_policy` and `figure_reuse_decision_source`. The delegated
default is `reuse_cleared_only`; `user_directed` requires an explicit user
choice. User direction permits inclusion in this workflow but does not turn an
unknown/restricted licence into an open licence, so affected captions retain a
rights notice and source link.

The standard package contains Markdown, evidence CSV/JSONL, and the canonical
verified PDF built by `build_pdf.py`. `deep`/`broad` require the opening
visual abstract; it is optional for `quick`. `GenerateImage` is limited to that
asset. Write the machine-readable request with
`infographic_spec.py --write-tool-request`, load the deferred tool with
`ToolSearch(query="select:GenerateImage")`, wait, and make a real Biomni
`GenerateImage` tool call using the request arguments. Install its exact
`/mnt/results/...` path with `infographic_spec.py --install-image`, which applies
the deterministic Signifier/DieGrotesk header and evidence strip. Media-check
that final installed image, record the pass with `--record-media-check pass`,
then verify; both report formats embed those exact checked bytes.
Real paper figures remain real crops. All report content is drawn only from
verified evidence rows.

Keep every reasoning stage inside Biomni. Prod does not expose general-purpose
subagents, so complete staged adjudication, entailment, and narrative tasks in
the bounded `native_packs` emitted by `batch_tasks.py`. A pack reduces handoff
turns while preserving a separate output and evidence boundary for every task;
it is not a claim of concurrent reasoning. Use managed machines only for
deterministic acquisition, parsing, and OCR. Never route general review work to
a database-query agent.
