# Reports and integrity

**Load when** deciding report/infographic applicability, designing an applicable PDF, or an applicable facts artifact.
**Skip if** only reviewing a package whose evidence-v1 receipt already passed.
**What this will not tell you** whether the conclusion is right. Artifact agreement is necessary but
not semantic correctness; primary-source witnesses and known-answer evals cover part of that residual,
while domain judgement and review remain necessary.

---

## Why "derive, don't restate" is the whole design

Correcting an individual wrong number does not stop the same defect returning. Changing the mechanism
does. In order of preference:

1. **Derive.** A report that prints its count by *reading the table* cannot disagree with the table.
   Most "the report says 34, the table has 33" defects die here and never come back.
2. **Gate.** When you cannot derive, add a check that fails loudly when a headline disagrees with an
   exported artifact.
3. **Reword.** Only when neither is possible. If a defect has already recurred once, rewording has been
   tried and failed — do not reach for it again.

A fix that says "change 100 to 50" is the kind that comes back. A fix that says "read the value from
the parameters file at render time" is the kind that does not.

---

## The facts artifact

When applicable, one JSON file holds every evidence-bearing headline the report may quote, its
operational definition, provenance, denominator/completion partitions, and pre-formatted sentences.
It is written after all gates pass. A non-evidence-bearing utility records `not_applicable` in
`skill_contract.json` rather than creating ceremonial facts.

```python
from report_qc import assert_figures, write_facts_from_artifact

# 1. gates first — a failing run must not produce quotable numbers
assert_thresholds_came_from_outside(params)   # yours to write; report_qc ships the generic ones
figures = assert_figures("figures/manifest.json")

# 2. the workflow has already written every domain fact to the contract-named runtime payload;
# validate that complete payload and attach the checked figure inventory
write_facts_from_artifact(
    "report_facts.json",
    source="facts_payload.json",
    figures=figures,
    contract="skill_contract.json",
)
```

The payload contains the counts, tier, headline, and caveat fields required by the contract. Why
pre-formatted sentences and not just numbers: the sentence is where the arithmetic and the wording
can diverge. If the renderer composes prose from raw numbers it can still say "most" when the number is
33 of 14,208. Write the sentence once, next to the number it describes.

**Ordering is load-bearing.** Gates run *before* the facts file is written. A run that fails a gate then
has no facts file at all, so there is nothing for a report to quote and the failure cannot be papered
over downstream.

---

## A gate's expectation must come from somewhere the run cannot write

This is the most common way a gate turns out to be decorative.

- **Bad:** the threshold is read from the run's own output. One report silently dropped from five
  figures to one and passed with zero failures, because the expected figure count was derived from the
  figures that happened to exist.
- **Bad:** a filter that excludes nothing. If your quality filter has never removed a row, it is not a
  filter; it is a comment.
- **Good:** the expectation is a literal in the skill package, a value in a parameters file written
  before the analysis ran, or a count declared by the author.

Test for it: could this gate pass if the analysis produced nothing? If yes, it is not a gate.

**A gate is wired only when a script calls it.** A `validate_*` or `assert_*` function that nothing
invokes is documentation. If the intent is for the agent to call it by hand, say so explicitly in the
step, and record the result.

---

## Tier gates: constrain the strongest sentence

The skill computes which evidence tier the run reached and the report's language is bounded by it. The
agent does not choose the tier and does not choose the adjective.

```python
tier = "validated" if replicated_in_independent_cohort else "hypothesis-generating"
if tier != "validated":
    forbid_words(report_text, ["validated", "confirmed", "establishes", "demonstrates"])
```

If the author never confirmed where that line sits, the tier is hypothesis-generating and the skill says
so. An unconfirmed threshold that gets reported as validation is the failure this prevents, and it
always errs in the direction that favours shipping.

---

## Caveats are gates, not prose

Each caveat carries three things: the claim, the number that makes it concrete, and the artifact field
recording whether it fired.

> **Bound:** "Batch structure may confound the comparison. `report_facts.json:caveats_fired
> .batch_confounded` is true when condition and batch are not orthogonal — in this run it is
> **true**, and the affected contrast is flagged in the results table."
>
> **Unbound:** "Batch effects may confound results." — reads careful, checks nothing, and is true of
> every experiment ever run.

A caveat that names no field cannot tell the reader whether it applies to *this* run, which is the only
thing they wanted to know.

---

## What the report must contain

This section applies when `deliverable_policy.report.required` is true. User-facing scientific output
normally requires it. A composable helper may declare it not applicable only with a resolved reason;
its receipt records report-only outcomes as typed `not_applicable` rather than false or fabricated.
An infographic has its own decision and may be omitted from a report only with a resolved reason.

**Form** — one combined PDF with readable narrative. The qualitative infographic generated by the
Biomni `GenerateImage` tool is the first substantive visual near the beginning of the report. Rendered
page images are inspection evidence, not deliverables and not a substitute for the PDF.

Use these visible top-level sections, in this order, for every archetype:

1. **Task Context** — the question, inputs, scope, and decision the output informs.
2. **Methods & Sources** — the actual analysis method and data for analysis workflows; search,
   selection, extraction, and evidence grading for syntheses; materials and authoritative procedures
   for protocols; rules and authorities for guidance; transformations and validation for utilities;
   generation/inspection contracts for meta-tooling.
3. **Results** — actual run outputs. For protocols this means the produced protocol, checkpoints,
   outputs, and acceptance criteria; for utilities, the transformed artifact and integrity checks.
4. **Conclusions & Interpretation** — supported takeaways, practical meaning, and next steps.
5. **Limitations** — run-specific uncertainty, missing coverage, unavailable checks, and unsupported
   claims. Empty boilerplate does not satisfy this section.

**Data** — source, size, what was filtered and why, before-and-after counts ("20,531 genes → 14,208
after independent filtering").

**Methods** — each step with the parameters actually used, software versions, the model formula or
algorithm configuration. Parameters the skill pinned rather than chose at runtime should say so.

**Results** — summary statistics, top-results tables, every declared figure embedded with its caption,
and explicit warnings where a statistical concern fired.

**Interpretation** — three to five findings, each with a number. Limitations specific to *this* run, not
a generic list. Suggested next steps.

**Reproducibility** — an output-files table (filename → what it is), data sources with their licence
obligations, and references.

**The report describes the analysis, never the skill.** A reader does not care that a skill exists; they
care what was found. Sentences about the workflow's own steps and features are a sign the report was
written from the SKILL.md rather than from the run.

---

## Assembly and acceptance

Hand the content and artifacts to the contract's default report provider unless an affirmative user
message explicitly selects a compatible report-styling skill. The receipt derives that selection
from the immutable execution transcript and provider-owned aliases. New providers declare those in
`assets/report_style.json`; existing `*-styling` providers use aliases conservatively derived from
their installed skill slug. A caller variable, customer identity, or project context cannot
authorize it. No styling request is the unambiguous default path and must not trigger a styling
clarification. The selected provider
owns layout, styling and typography;
it must not change the evidence, report sections, artifacts, infographic lineage, or review gates. Do
not restate styling rules in the skill — describe what each figure must *communicate*, not how it
should look.

Before declaring the report ready:

- It exists, at the results root, under the declared `report_<slug>.pdf` name.
- More than one page and a plausible file size — a one-page report of a multi-step analysis is a
  rendering failure, not a concise summary.
- Text is extractable, not an image of text.
- Every declared figure is embedded, and none is distorted or blank.
- The exact `GenerateImage` infographic is embedded as the first substantive visual near the start;
  require a same-ID call/result pair, returned filename match, decoded-pixel identity, and image 1 on
  page 1. A filename match or image count alone is not identity evidence.
- Run the platform's own visual check on the finished PDF and on every generated image, and regenerate
  on failure. A failed or unavailable mandatory visual check is blocking: source image dimensions do
  not prove correct placement, and `not_evaluable` is not a pass.
- Superscripts and subscripts use the renderer's markup, not Unicode characters — Unicode superscripts
  render as black boxes in many PDF fonts. Write `3.36e-06` or use `<super>` tags.
- Page size is set explicitly to US Letter. Do not rely on a library default.

If the report cannot be produced, that is a failed run, not a run with a missing file. Say which gate
stopped it.

Finish with a short narrative response that states the main conclusion and points to the combined PDF
and supporting artifacts. Do not return only filenames, page renders, or a directory listing.

## Bounded repair loop

Treat a clean sample run as an evaluation, not as an interactive drafting session. If it fails,
repair the package mechanism and start a fresh task; do not steer the same run. Stop after at most two
repair attempts and report what remains. Stop earlier when the next change would encode one demo's
locus, accession, row count, wording, or stochastic layout accident instead of a reusable invariant.
At that point the remaining defect is execution evidence, platform behavior, or nondeterminism — not
another justification for narrowing the skill.
