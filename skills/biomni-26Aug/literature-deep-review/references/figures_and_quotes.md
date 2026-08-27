# Figure and quote rules — rationale

`SKILL.md` states these rules; this file explains why each exists, mostly by
naming the shipped defect that motivated it. Read it when you are tempted to
relax one.

## How figures are chosen (superseded rule inside)

**Current rule.** For each claim, `scripts/figure_selection.py` scores caption
or OCR text from figures belonging to papers that claim *already cites*. The
matching context is the atomic claim plus scope—not all accepted quote text,
whose generic outcomes can pull in an unrelated panel from the same paper. The
caption or in-figure OCR must also name one of the review subject aliases. A
quoted caption is considered first but bypasses neither lexical scoring nor the
subject/role gates; being quoted is not proof that the panel depicts the claim.

Caption/OCR scoring now proposes candidates only. Before export, Biomni reads
the exact image for every proposed `(claim_id, paper_id, figure_id)` and records
whether the visible panels entail the claim with matching direction, model,
outcome, and subject. It also records crop completeness, label legibility, and
page contamination. The inspected image is hashed. An exact accepted
caption/figure anchor establishes the textual locator, but it does not waive the
visual/crop check. Missing checks fail export rather than silently shrinking the
report.

**What it replaced, and why.** A figure used to reach the report only when an
accepted evidence row's `block_type` was `caption` or `figure_ocr`, i.e. only
when the reviewer happened to quote that figure's legend. Figure choice was a
side effect of quote-type choice, and three things followed:

1. **Starvation.** A claim grounded on three Results *sentences* got no figure,
   even when its paper contained a figure showing exactly that result. Two
   shipped `broad` reports turned 45 and 29 verbatim quotes into 6 figures.
2. **Clustering.** One paper whose captions were quoted three times put three
   figures under a single claim while four other axes got none.
3. **Irrelevance.** Raulin et al. 2022 Fig. 2 — a *review's* drawing of
   "ApoE-targeted therapeutic strategies" — was embedded under a claim about
   APOE4 altering lipid metabolism, because that caption was the claim's anchor.
   It measures nothing, so it could support nothing.

The role taxonomy prevents a repeat of (3). A primary-data figure can depict an
original result. A source mechanism/model or review/context diagram is excluded
by default; a custom contract may include it only as illustrative context,
never as evidence, axis coverage, or a source-figure-floor contribution. A
generated infographic is synthesis only.

A schematic panel does not make an entire multi-panel result conceptual. If the
same caption identifies measured panels (for example western blots,
quantification, survival, imaging, sample sizes, or test statistics), the
figure remains `primary_data`. `source_model` is reserved for figures that are
wholly conceptual.

Thresholds (`min_relevance`, `min_shared_terms`) are calibrated together on real
figure/claim pairs from the shipped reports, kept as `SHIPPED_PAIRS` in
`tests/test_figure_selection.py`. Neither works alone: the score cannot reject
"Figure 2 Brain sections." against a claim mentioning the brain (it scores 0.27,
above three genuine matches), and the shared-term count cannot rank. Change one
and re-check the separation.

**Selection reports what it dropped.** `selection_rejected` in
`figures_manifest.json` records a cause per figure passed over. A step that
silently drops candidates reads downstream exactly like a corpus that never had
them, and "6 figures" then looks like the whole supply.
Rejection diagnostics report both claim–figure pair counts and unique-figure
counts. Do not describe 81 rejected pairs as 81 missing crops.
Missing or undecodable crops are rejected before scoring as
`image_unavailable`, allowing the next eligible figure to replace them. Every
selected `(paper_id, figure_id)` then appears in the manifest as `exported` or
with an explicit reuse/image/export failure disposition.

**Coverage is adaptive, not a high quota.** The user minimum remains a floor.
After ordinary per-claim selection, only claims explicitly marked
`figure_priority=true` are checked for coverage. An otherwise-uncovered
decision-critical axis receives its best eligible nonredundant primary-data
visual when one exists, with no fixed global count or coverage fraction. The
manifest records eligible pairs, selected roles, and an explicit gap reason per
priority axis.
The final coverage count is based on exported figures. A pre-export selection
does not satisfy an axis.
Coverage uses the same relevance and shared-term floors as ordinary selection;
it may redistribute a qualifying figure, never weaken the scientific threshold
to fill an empty axis.

**Crop QA is a gate, not a renderer preference.** An exported crop must decode,
retain its panel labels, and exclude adjacent page prose. OCR touching a crop
edge is treated as possible clipping; multiple long prose lines are treated as
body-text contamination. The exporter records the checks and pass status in
`quality_check`, and reconciliation refuses an exported figure without that
structured pass receipt.

## Why a figure carries boxes

`scripts/figure_provenance.py`. Two reports reproduced 27 figures between them
and not one showed why THAT figure, out of the paper's twelve, was under that
claim. The box-drawing code already existed — `_draw_annotations` plus
`report_model`'s preference for an annotated copy — and never fired: its input
came only from evidence rows whose `block_type` was `figure_ocr`, and there were
0 and 1 of those against 35 caption anchors.

The reason is structural. Selection scores **captions**, and a caption is text
*below* the image, so nothing in the picture was ever the recorded reason and
there was nothing to box. Provenance closes that loop instead of decorating it:
in-figure OCR text is matched against the claim's terms using the same stemmer
`caption_relevance` uses, so a box marks something that genuinely contributed.

Three honesty rules, each one a way this could have become theatre:

1. **A box only ever marks a term the claim contains.** Never "something
   interesting in the picture".
2. **The caption names both signals separately** — the caption terms that scored
   and the boxed in-figure text. They are different evidence and a reader
   auditing the choice needs to know which is which.
3. **No OCR means the caption says so.** The standard installer first reuses
   Biomni's EasyOCR/PyTorch runtime, installing only missing OCR dependencies,
   and ensures the English model is ready. A user may still explicitly choose
   caption-only processing; falling silent would read as "the picture was
   checked and nothing matched", so that choice remains stated.

Each parsed figure records `caption_source`, `ocr_attempted`, `ocr_status`, and
`ocr_error`. `ocr=all` requires every image-backed crop to have an attempted,
non-failed disposition. A separate embedded panel crop may inherit the one
unambiguous caption on its page; the manifest retains `parent_figure_id` so this
is auditable rather than guessed.

Labels show words, not stems. The first version printed `frontotempor`,
`heterozygou`, `lysosom`, `defici` — which describes the stemmer, not the
evidence; `surface_form` maps a stem back to the shortest word that produced it.

The plain crop is never drawn on. Annotation writes a separate file, so the
report always retains an unaltered reproduction of the published figure.

## Why figure grounding is a hard requirement

A figure-level review that ships no figures is not a stylistic miss — it means
either that no crops were produced or that no caption in the cited corpus was
specific enough to any claim, and both mean the reviewer never looked at what
the papers actually showed.

**The regression this prevents.** One run shipped a report with 5 embedded paper
figures; a later run of the same pipeline on the same question shipped 1 — and
both passed every gate with "0 failures, 0 warnings". Three causes compounded:

1. **Retrieval starved figure blocks.** `rank_candidates` kept only
   `top_per_paper` blocks per paper per claim. `broad` allowed 2 (fewer than
   `deep`'s 3), caption blocks got a 1.1× nudge against Results sentences at
   1.25×, and `figure_ocr` was *penalized* at 0.9×. Figure blocks essentially
   never won a slot. Fixed with a reserved `figure_quota` that does not compete
   with sentence slots — and, later, by decoupling figure choice from quote
   choice entirely (see above), so a starved caption no longer costs a figure.
2. **JATS-XML acquisition forfeits figures silently.** `parse_jats` sets
   `image_path: None` for every figure, so any paper recovered as XML (route 3,
   which fires whenever the publisher PDF 403s) can contribute captions but
   never a crop. Fixed with a supplementary figures-only PDF fetch.
3. **The gates were tautological.** `verify_pdf_assets` read the required figure
   count out of the run's own `figures_manifest.json`, so exporting one figure
   and embedding one figure passed. Fixed by taking the floor from
   `templates/report_contract.json` plus the *croppable supply*.

The lesson generalizes: **a gate whose expectation comes from the artifact it is
checking cannot detect a shortfall.** Thresholds belong in the contract.

## Crop availability is legitimately partial; selection is not minimal

The parser crops figures where an embedded raster image sits above a detected
caption. Vector-only figures (many *Nature*-family papers), JATS-XML sources,
and some single-column layouts may yield no crop. That is graceful degradation,
not permission to stop after reaching a small count.

The contract handles this honestly by taking the report's minimum from the
user's run configuration. Corpus size does not silently increase it. The
mode-specific 0/3/4 values are fallbacks only when the user delegates the
choice. Beyond the chosen floor, include a figure allowed by the recorded reuse
policy only when it adds material, nonredundant panel-level information. The
user may choose `user_directed` inclusion for accessible figures whose reuse
rights are unknown/restricted; keep the source link and disclose that status in
the caption rather than treating the figure as openly licensed. If the user explicitly
requests a distinct-paper coverage rule, multiple figures from one paper do not
satisfy it.

When an important axis remains uncovered, export runs a second targeted OCR pass
over image-backed captionless crops from that axis's cited papers, persists the
OCR into the parsed artifact, and reruns selection. This is narrower than OCRing
every crop in the retrieved corpus and directly addresses captionless figures
that could not enter lexical selection on the first pass.

## Why quotes must be complete sentences

**The defect.** A shipped report contained this as a grounding anchor:

> "Grn - / - mice develop severe lysosomal dysfunction, resulting"

`is_incomplete_sentence_quote` exempted any whole-block quote under 80
characters, intending to allow headings and list items — but with no actual
heading test, just a length check. At 62 characters this fragment passed a gate
the skill advertised as enforcing complete sentences. The exemption is now a
real heading/label test.

## Five kinds of extraction damage, and which are repairable

`scripts/quote_integrity.py`. The principle is the one this file already states
below — **a rejection is not a diagnosis** — so damage with exactly one reading
is repaired and the repair recorded; damage without one is rejected and the
reason named. All five shipped inside quotation marks:

| Damage | Example (verbatim from a shipped report) | Handling |
|---|---|---|
| split ligature | `gene dose was signi fi cantly greater` | repaired |
| letter-shattered word | `n e u ro d e ge n e ration` | rejected |
| fused words | `Asexpected, when we quantified` | rejected |
| corrupt operator | `(P 5 0.001)` for `(P < 0.001)` | rejected |
| column splice | `... Tau pathology, APOE4-R136S mutation.` | rejected |

Split ligatures are repaired **in the parser** (`_clean_text`), not at display
time, so the quote stays an exact substring of the block it came from and every
locator and quote gate still resolves. Repairing at display would leave the
stored evidence damaged and the rendered text different from it.

Letter-spaced runs are *not* repaired, though a first attempt did: closing the
gaps needs the word, and the fragments are not uniformly short
(`...ge n e ration`), so a regex join yields `neurodegene ration` — still not a
word, and now presented as verbatim. Two wrongs.

The fused-word detector is a **curated list of observed fusions, not a pattern**.
The pattern version flagged the `As` in `Astrocyte` and the `and` in `ligand`,
`strand` and `understand`. A false positive here rejects good evidence, so
precision beats coverage and the list grows from real parser output.

## Why merged and garbled caption text is rejected

Two different causes hide behind one symptom, and only one is irrecoverable.
Diagnose before assuming.

**Cause 1 — a parser bug (was live for two releases).** This shipped:

> "PR006 increases progranulin levels in CSF of FTD-GRN study
> participants.CSF samples were collected from study participants..."

The missing space came from `"".join(el.itertext())` in the JATS parser, which
concatenated XML text nodes with no separator: `<title>…participants.</title>`
followed by `<p>CSF samples…` produced exactly that string. It was never a
PDF-layer problem. The parser now walks the tree and inserts a separator only at
block-element boundaries, so inline `<italic>`, `<sup>`, and `<xref>` still join
without one and `PGRN<sup>-/-</sup> mice` survives intact.

The lesson generalizes past this bug: that text was **rejected as garbled for
two releases while the real fix was one function away**, and the rejection rule
made the corruption invisible instead of loud. A rejection is not a diagnosis.

**Cause 2 — a genuinely absent space glyph.** Some PDF text layers really do
drop inter-word spaces:

> "Figure4. AAV- Grn improvesmicrogliosisin Grnmice.Microgliosiswasassessed…"

Before calling this irrecoverable, dump `page.chars` across the merged span and
look: if a space char is present, the text did not come from the PDF layer at
all; if there is none and the inter-character gaps are 0.5–1.4 pt, it is a
tolerance setting in the parser and is fixable there. Only when the gaps are
≤ 0 does the font genuinely lack usable widths — and only then is choosing a
clean anchor the right response. The figure still exports from any clean
anchor, so suppressing the text costs no evidence in that case.

Related: a shipped quote began `"3Latozinemab decreases sortilin levels…"` — a
figure number bleeding into the caption text. Stripped at adjudication.

## Why reference-list blocks are not quotable

A shipped report grounded a claim on:

> "Mutations in progranulin cause tau-negative frontotemporal dementia linked to
> chromosome 17."

attributed to a 2025 review at "page 20 · References". The sentence appears
exactly once in that review — as the **title of Baker et al. 2006 in its
bibliography**. It is not a statement the review makes. A reference-list entry
is not evidence of anything, and the honest locator ("References") was the only
tell. Bibliography blocks are now excluded from the quotable index outright.

## Why `primary` requires a stated result

`scripts/anchor_policy.py`. `primary` means an original result of the paper being
quoted, so the quote must either present a result — a first-person finding, a
statistic, an observed effect, a measurement in the passive voice — or come from
a section that reports results (Results, Discussion, a figure legend).

**The defect.** A claim reached "Convergent (≥2 independent primary studies)"
partly on:

> "Apolipoprotein E4 (APOE4) is the strongest known genetic risk factor for
> late-onset Alzheimer's disease (AD)."

That is the opening line of a paper about *neuronal APOE4 removal in tauopathy
mice*. It restates the field's consensus as background — true, and not a result
of the paper quoted. The citation-marker rule below did not catch it because
extraction had dropped the superscript reference.

This downgrades some genuinely solid claims to "indirect / background support
only". **That is the correct outcome**, and the honest one: when the actual
primary sources (Baker 2006, Cruts 2006 for GRN) were never retrieved, a tier
that says "convergent primary" is a claim about reading that did not happen. A
support tier describes what this review read, not what is true. Every downgrade
records its reason in `evidence_kind_relabel_reason`.

A **conditional** is rejected outright rather than downgraded, because no label
makes it evidence:

> "By contrast, if suppression of ApoE4 in astrocytes rescues the BBB defect, a
> gain-of-function mechanism would be supported."

Recorded as `supports/primary` for a gain-of-function claim. It is a statement of
study design; it asserts nothing.

## Why citation markers force `secondary`

A shipped report labeled this `supports/primary`:

> "Previously, we have shown that the ablation of sortilin in mice leads to an
> increase of PGRN levels in the brain lysates and in the serum."

In the source JATS the sentence ends with a superscript `<xref>` to reference 30
— a different, earlier paper. Extraction dropped the superscript, and that is
precisely what made a summary of prior work look like an original result. It
fails the skill's own rule on both limbs (reporting phrasing *and* a citation
marker), and because support state is derived from `evidence_kind`, the
mislabel promoted the claim's tier.

Detect reporting phrasing and citation markers, and relabel to `secondary`.
Relabel rather than drop — the sentence is still real corroboration.

## Quote-to-claim entailment

Grounding is not just "the sentence exists in that paper". A shipped report used

> "AAV-expressed progranulin was only detected in neurons, not in microglia,
> indicating that the microglial activation in progranulin deficiency can be
> improved by targeting neurons and thus may be driven at least in part by
> neuronal dysfunction."

to ground *"Progranulin deficiency drives microglial dysfunction and
neuroinflammation."* The sentence never mentions neuroinflammation, describes a
rescue experiment, and concludes the microglial phenotype is downstream of
neurons — which argues against the claim as worded. The same sentence was also
reused to ground a second, different claim.

A verifier that only checks substring presence cannot catch this. Emit a
blinded task for every displayed grounding anchor and require an acceptable
verdict before delivery. The second pass must not see first-pass stance,
evidence kind, rationale, or support tier.

“Acceptable” is deliberately strict: `entailment=yes`, every direction,
population, intervention, and outcome flag true, and no scope overreach.
`partial` is recorded for audit and revision but never carries a displayed
claim. A `yes` with any mismatched axis is structurally invalid rather than a
passing reviewer opinion.

## Access states

Explicit states, not a coarse open/closed label. The distinction matters because
conflating them produced a report that called freely-readable papers "paywalled":

| State | Meaning | Quotable |
|---|---|---|
| `oa_licensed` | In the PMC OA subset / CC-licensed | Yes |
| `free_to_read` | Validated full text served without authentication, but no OA licence established | Yes, labeled |
| `licensed_copy` | Retrieved through an authorized licensed source | Yes, not automatically redistributable |
| `user_supplied` | User-provided local PDF | Yes, not automatically redistributable |
| `unknown` | Full text exists but access classification is absent | Yes, labeled; never call it open access |
| `not_retrievable` | Genuinely could not be obtained | No — a real gap |

Verified examples: Klein 2017 Neuron (PMC5558861) and Hu 2010 Neuron
(PMC2990962) are `free_to_read` — the older report called both "paywalled" and
listed acquiring them as a next step. The Aggarwal/Jones ASO paper
(PMC10755782) is **CC BY** and should simply have been retrieved; it was
reported as a paywalled gap. Baker 2006 and Cruts 2006 are genuinely
`not_retrievable`.

`free_to_read` includes a validated publisher PDF served to the pipeline's fresh
unauthenticated session. The pipeline never supplies credentials or circumvents
an authentication challenge or technical control. This permits reading and OCR,
not automatic figure reproduction.
