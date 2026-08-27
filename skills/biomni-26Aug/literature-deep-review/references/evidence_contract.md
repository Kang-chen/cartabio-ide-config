# Evidence contract

## Contents

1. Canonical artifacts
2. Source blocks
3. Adjudication input and output
4. Evidence rows
5. Support states
6. Deterministic verification

## 1. Canonical artifacts

The source of truth is split by responsibility:

- `corpus/claims.jsonl`: stable claim identity and revision lineage.
- `fulltext/acquisition_routes.jsonl`: one terminal acquisition outcome for
  every selected paper, including retry/recovery route details.
- `fulltext/parse_quality.jsonl`: one explicit body-text quality state for
  every retrieved paper (`usable`, `low_quality`, `figure_only`, `unusable`).
- `fulltext/blocks.jsonl`: exact parsed text plus page/section/figure provenance.
- `evidence/adjudications.jsonl`: unfiltered native/provider decisions.
- `evidence/adjudication_audit.jsonl`: per-batch accepted/rejected block counts
  and rejection-reason totals, including zero-accept batches.
- `evidence/evidence_lineage.jsonl`: accepted/rejected/duplicate disposition
  for every raw adjudication, with stable adjudication IDs.
- `evidence/rejected_evidence.jsonl`: rejected decisions and exact reasons.
- `evidence/evidence.jsonl`: accepted claim-to-block relationships.
- `evidence/entailment.jsonl`: independent blinded verdicts for displayed anchors.
- `evidence/figure_entailment.jsonl`: Biomni visual verdicts for exact
  claim/figure/image triples, including scientific and crop-integrity dimensions.
- `synthesis/claim_evidence_matrix.csv`: deterministic claim-level aggregation.
- `run_manifest.json`: configuration, per-paper status, model usage, and resume state.
- `state/intake_snapshot.json`: immutable review brief used for search freshness;
  mutable runtime metrics never invalidate it.
- `state/skill_provenance.json`: exact Git commit and content hashes for the
  coordinator package and managed-worker bundle. It must match the manifest and
  final managed execution receipt.
- `state/skill_provenance_upgrades.jsonl`: optional append-only transitions to a
  newer committed coordinator, with frozen scientific-artifact hashes. Never
  rewrite origin provenance or use this ledger for an uncommitted hot patch.

Record artifacts (paper-level metadata, not evidence):

- `corpus/references.jsonl` / `fulltext/papers.jsonl`: per-paper records. Beyond
  the identifiers, these carry `access_state` (see §4), `access_evidence`,
  `_not_retrieved_kind`, `first_author_surname`, `is_preprint`, and — for papers
  whose text came from JATS XML — `figures_pdf`, `figures_pdf_status`, and
  `figures_pdf_source` for the supplementary crop-only PDF.

Derived artifacts reports may consume:

- `deliverables/grounded_quotes.json`: the per-claim verbatim anchors.
- `deliverables/review_stats.json`: counts and run mode.
- `deliverables/figures_cited/figures_manifest.json`: exported figure crops.

Reports may only consume the artifacts named above, and every one of them is
derived from the canonical set. Never make a report-only evidence row.

## 2. Source blocks

Block IDs are stable within a parsed paper:

- `<paper_id>:S:<sentence_id>` for body/abstract sentences;
- `<paper_id>:CAP:<figure_id>` for figure captions;
- `<paper_id>:OCR:<figure_id>:<line_index>` for in-figure OCR lines.

Each block carries `paper_id`, `block_type`, exact `text`, `page`, `section`,
`bbox`, `figure_id`, `image_path`, and `ocr_conf`. XML/JATS blocks have section
locators and may legitimately have no page geometry.

**Reference/bibliography blocks are not quotable.** A block whose section name
is a bibliography heading (`References`, `Bibliography`, `Works Cited`,
`ref-list`, `Supplementary References`, and the numbered forms) is written to
`blocks.jsonl` — so every stored `block_id` stays resolvable — but is excluded
from the candidate set before scoring and rejected outright at adjudication.
The match is anchored on the whole section name, so a Results section that
merely mentions a "reference genome" is unaffected.

The reason is not tidiness: a bibliography entry is the *title of a different
paper*, so quoting one attributes another author's claim to the citing paper.
A shipped report grounded a claim on a sentence that turned out to be the title
of Baker et al. 2006 sitting in a 2025 review's reference list.

Note the dependency this creates. The exclusion keys on `section`, which the PDF
parser assigns heuristically — if a "References" heading goes undetected, the
bibliography inherits the previous section name and becomes quotable again.
Section detection is therefore load-bearing for this rule, not cosmetic.

## 3. Adjudication input and output

Adjudication receives one paper, a bounded group of claims, and a small set of
retrieved blocks. It must return an object with an `evidence` array. Each item:

```json
{
  "claim_id": "C-001",
  "block_id": "PMC123:S:42",
  "quote": "An exact substring of the cited block.",
  "stance": "supports",
  "evidence_kind": "primary",
  "scope_note": "mouse dopaminergic neurons under rotenone stress",
  "rationale": "The paper's own result directly tests the scoped claim.",
  "needs_figure_review": false
}
```

The native JSONL form ends with exactly one `_decision_audit` record. Its block
counts must reconcile to the supplied batch and its `rejection_reasons` counts
must sum to `rejected_blocks`. This compact audit records that candidates were
actually reviewed without retaining thousands of unsupported pseudo-evidence
rows.

Allowed stances: `supports`, `contradicts`, `mentions`.

Allowed evidence kinds:

- `primary`: this paper's own direct result;
- `indirect`: relevant direction, not the specific claim;
- `control`: control arm or control measurement;
- `secondary`: statement attributed to another paper;
- `correlative`: association without intervention/causal demonstration;
- `inferred`: reviewer synthesis rather than a reported result.

No returned item disappears during import. Stable adjudication IDs bind the raw
row to exactly one lineage disposition: `accepted`, `rejected`, or `duplicate`.
Rejected items retain the validator reason, and duplicates name the first
accepted adjudication. Reconciliation requires raw count = lineage count and
reconstructs `evidence.jsonl` and `rejected_evidence.jsonl` from that lineage.

## 4. Evidence rows

`evidence_first.py` accepts an adjudication only when the quote resolves to the
cited block and the block resolves to the paper. It then adds:

- stable `evidence_id`;
- `quote_match`: `exact` or `normalized` whitespace match;
- block provenance and a rendered locator;
- paper identifiers, explicit access status (`oa_licensed`, `free_to_read`,
  `licensed_copy`, `user_supplied`, or `unknown`), and URL when one exists;
- `evidence_kind_relabeled_from`: the model's original kind when the attribution
  guard downgraded it (see §5), empty otherwise;
- adjudication backend/model/request ID;
- `verified=true` and timestamp.

**`access` vs `access_state`.** The evidence row preserves the actual provenance
class: `oa_licensed`, `free_to_read`, `licensed_copy`, `user_supplied`, or
`unknown`. The paper record's `access_state` additionally records acquisition
classification, including `not_retrievable`. Missing classification becomes
`unknown`, never `oa_licensed`; `unknown` is preserved for diagnosis but fails
the final access gate until resolved. Access still does not grant figure-reuse
rights; those are derived separately from the paper licence.

A validated internet PDF retrieved without credentials is resolved as
`free_to_read` and may be parsed and OCRed even when no open licence is recorded.
That access label does not make the paper open access and does not establish
figure-reuse permission.

Discovery-provider text is not full text. A direct Exa URL ending in `.pdf` is
an acquisition candidate and must pass PDF-byte validation. Consensus metadata,
Consensus snippets/chunks, and Exa-extracted page text remain discovery or
abstract context unless the acquisition waterfall obtains a PDF or substantive
JATS body through an unauthenticated source route.

A short OCR label may be evidence only with a resolvable figure region. A body or
caption quotation ending in a dangling function word, or with unmatched brackets,
is rejected as likely truncated.

A quotation from a `sentence` block must be one or more **complete sentences**: it
must begin at a sentence boundary (uppercase letter, digit, or opening
quote/paren) and end at sentence-terminal punctuation (`.`/`!`/`?`, optionally
followed by a closing bracket or a bracketed citation), unless it is the entire
block (e.g. a heading or list item). Sub-sentence snippets such as
`"demonstrated target engagement of S15JG, a murine"` are rejected — quote the
full sentence(s). `caption` and `figure_ocr` blocks are exempt from the
*complete-sentence* rule (figure legends and in-figure text are not full prose
sentences) **but are NOT exempt from the garbled-text rule**: a `caption`/
`figure_ocr` quote whose text is merged (inter-word spaces dropped by the PDF text
layer, e.g. `"improvesmicrogliosisin Grnmice.MicrogliosiswasassessedbyCD68…"`) or
column-interleaved is rejected. Ground the figure on a clean sentence or clean
caption instead; the figure image is still exported from the clean anchor.

## 5. Support states

Support state is a pure function of accepted evidence:

| State | Rule |
|---|---|
| `C1_SINGLE_DIRECT` | primary support from exactly one study, no contradiction |
| `C1_INDIRECT` | support exists but none is primary |
| `C2_CONVERGENT` | primary support from at least two independent studies, not all one cohort |
| `C_CONFLICTED` | qualifying support and contradiction both exist |
| `C_REFUTED` | primary contradiction exists and qualifying support does not |
| `C_INSUFFICIENT` | no qualifying support or contradiction |

Only **primary** support raises a claim above `C1_INDIRECT`, and convergence is
counted over independent `study_id` values and cannot come entirely from one
`cohort_id`. Secondary/review rows corroborate but never establish
`C2_CONVERGENT`. A single primary study plus one review is `C1_SINGLE_DIRECT`,
not convergent. `inferred` rows and `mentions` never raise support. Preprints,
journal versions, and secondary analyses of one study do not become independent
merely because they are separate publications.

The report exposes four orthogonal descriptors instead of overloading
“primary/secondary”:

- **publication type:** primary report vs review/commentary;
- **anchor depth:** results/figure, abstract-only, methods, introduction, or
  discussion;
- **claim relationship:** direct, indirect/citation, or reviewer inference;
- **independence:** study ID, cohort ID, and publication role.

Thus a primary research paper available only as an abstract remains a
`primary report · abstract-only anchor`; its shallow anchor is disclosed but it
is not mislabeled as a review. Support labels print study and paper counts so
three publications from one study cannot read as three replications.

**`primary` vs `secondary` is enforced, not just asserted.** A supporting quote
that carries an inline citation marker (e.g. `[5]`, `41,42`, a trailing reference
number) or uses reporting/background phrasing (e.g. "has been shown", "studies
have reported", "it is thought to", "according to") is summarizing another
source's work, not presenting an original result of the quoted paper. Such a
quote may not be labeled `primary`. `validate_adjudication` **relabels it to
`secondary` in place and accepts the row** — the observation is real evidence,
only of a weaker kind. The original label is preserved in
`evidence_kind_relabeled_from` and the support state recomputes from the
corrected kind. The row is **not** rejected and does **not** appear in
`rejected_evidence.jsonl`; do not go looking for it there. Author-year
parentheticals such as `(Martens et al., 2012)` count as citation markers too.
Allele/isoform names (apoE4, E4/E4, ε4) are masked
before this check so their digits are not mistaken for citation markers. Genuine
own-result phrasing ("we show", "our data", "these findings", "providing proof of
concept") with no citation marker is unaffected.

## 6. Deterministic verification

The final gate suite reloads all canonical artifacts and requires:

1. Every evidence and block ID is unique and resolves.
2. Every quote occurs in the cited block.
3. Stored quote-match, locator, page, section, figure, and bbox equal the block.
4. Figure OCR evidence has figure ID, image path, and bbox.
5. Evidence paper and access status resolve to a retrieved paper; local,
   licensed, free-to-read, and unknown copies are never relabeled as licensed
   open access. Figure reuse remains a separate licence decision (see §4).
6. Matrix evidence sets equal the canonical evidence sets.
7. Matrix support states reproduce from evidence.
8. Weak/refuted claims do not use prohibited categorical wording.
9. Review statistics and printed claim counts agree with canonical artifacts.
10. **Every delivered claim is grounded.** No claim may ship with
    `C_INSUFFICIENT` (no qualifying supporting or contradicting quote). A claim
    that cannot be tied to at least one exact quote must be dropped, split, or
    narrowed until it is grounded, or evidence must be acquired for it. This is
    enforced by default; the only escape is the explicit
    `--allow-ungrounded-claims` flag, reserved for intermediate/in-progress
    runs, never for a delivered review. Note that `C_REFUTED` and
    `C_CONFLICTED` claims ARE grounded (they carry contradicting quotes) and are
    legitimate findings — only the total absence of qualifying evidence is
    disallowed.
11. **Every displayed grounding anchor has an independent verdict.** The task
    payload contains only the scoped claim and quotation/provenance needed to
    judge it; it omits first-pass stance, evidence kind, rationale, and support
    state. Only `entailment=yes` with direction, population, intervention, and
    outcome all matching and no scope overreach passes. Missing, partial,
    scope-mismatched, internally inconsistent, or rejected anchors fail delivery
    for supported, contradicted, and conflicted claims alike.
12. Every selected paper has an acquisition-route outcome; every retrieved
    paper has one parse-quality receipt. A figure-only parse cannot masquerade
    as body text, and an unusable parse blocks delivery.
13. Every raw adjudication has exactly one reconciled lineage disposition; the
    accepted and rejected ledgers equal their canonical reconstructions.
14. Every new adjudication batch has a complete negative-decision audit whose
    accepted and rejected block counts reconcile to the supplied candidates.
15. Every exported claim/figure pair has a Biomni visual verdict for the exact
    image hash. Direction, model, outcome, subject, crop completeness, label
    legibility, and page contamination must all pass; axis coverage cannot use
    weaker lexical thresholds than ordinary selection.
16. Evidence-backed prose and structured infographic assertions are checked for
    direction reversal, model/outcome escalation, and unsupported population
    generalization. Each infographic panel carries at least one atomic assertion
    linked to exact claim and evidence IDs.

Verification failure requires repair, evidence rejection, or claim downgrade.
Never override the gate in prose.
