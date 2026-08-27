# The interview

**Load when** you are about to interview a skill author, or a first answer came back thin and you need
the probe for it.
**Skip if** the authoring record already answers every question with source-backed, concrete examples.
**What this will not tell you** whether the author's answer is scientifically right. You are eliciting
their judgement, not auditing it. If you think an answer is wrong, say so plainly and let them decide.

---

## Two interviews, and the first produces the second

| | Authoring interview | Runtime clarification |
|---|---|---|
| Between | you and the skill's author | the finished skill and whoever runs it |
| Happens | once, now | every run, forever |
| Written into | the whole package | `## Clarification Questions` |

Q1's answer *is* runtime question #1. If you never ask what the input looks like, the generated skill
cannot ask its user for the right thing either, and the defect is permanent.

## How to run it

Ask all seven in **one batched turn**, each with the default you propose to use. An author who can
accept six and correct one has given you more than an author who abandons a seven-turn interrogation.

Record each answer with where the decision came from:

```json
{"q3": {"answer": "...", "decision_source": "author-confirmed"},
 "q5": {"answer": "...", "decision_source": "agent-default"}}
```

`decision_source` is what makes an auto-progressed skill honest later. Never upgrade an
`agent-default` to `author-confirmed` because it sounds plausible.

---

## Q1 — Name one concrete subject or input

*Give the real file/schema or named dataset; the exact literature question; the material, scale and
endpoint for a protocol; or the concrete object a utility transforms. Is there a verified demo?*

**No default. Ask first, and wait.** A guessed subject produces a skill for data, evidence, or material
that does not exist.

Probes, in order:
1. "What exact object can a stranger retrieve, upload, or reproduce?" For files, paste the header and
   two rows; for literature, state one answerable question and evidence scope; for protocols, name the
   material, scale and endpoint.
2. "What would a stranger misread?" — units, coordinate build, evidence type, material state, or what
   missing means.
3. "How large or broad does this get?" — rows, studies, samples, reactions, pages, or requests.
4. "What verified public or bundled example has the same shape?" Without one, stop instead of inventing.

> **Good:** "A DESeq2 results CSV: `gene_id, baseMean, log2FoldChange, lfcSE, stat, pvalue, padj`.
> `gene_id` is Ensembl with version suffixes, which trips joins. Usually 20–60k rows. `padj` is `NA`
> for independently-filtered genes, and treating those as non-significant is wrong — they were never
> tested."
>
> **Thin:** "A gene expression file." — This tells you nothing you did not already assume. Probe 1.

## Q2 — What would a competent practitioner get wrong?

*A competent practitioner who has never worked on this problem sits down to do it. What do they do
that you would reject in review?*

**No default, and it cannot be auto-filled.** This is the one fact that cannot be inferred from the
task description, read off the data, or synthesised by any amount of reasoning. It exists only in
someone who has reviewed this analysis and turned a version of it down.

Probes:
1. "What did the last person who did this get wrong?"
2. "If you saw this result in a paper, what would make you not believe it?"
3. "Is there a default in the standard tool that is wrong for this problem?"

> **Good:** "They rank by raw p-value and hand over the top 50. That puts underpowered
> low-expression genes with huge fold changes in the same list as real hits, so the top of the list is
> dominated by near-zero-denominator noise. You have to filter on `baseMean` before ranking, and you
> have to say what you filtered."
>
> **Thin:** "They might not do proper QC." — A generic caveat wearing the right heading. Probe 3:
> *which* QC step, and what does skipping it do to the output?

**The stop rule.** If after two probes the author cannot answer this, say so:

> "I don't have a wrong-default to warn about for this workflow. That is a legitimate answer — it means
> I should write a shorter, more cautious skill rather than invent a caveat. Is that right?"

An invented Q2 is worse than an empty one: it ships as domain knowledge and nobody can tell from
outside that it is fiction.

## Q3 — What plausible output is wrong or misleading?

*What makes you distrust a number, source claim, protocol parameter, or transformed artifact? What
control, cross-check, or completion partition should expose the problem?*

Default (marked generic if used): results driven by batch structure, by a single outlier sample, or by
an identifier mapping that silently dropped features.

Fold the correctness interrogation into the probes here — this is where generic caveats become real:
genome build (GRCh37 vs GRCh38), strand, symbol → Ensembl → UniProt mapping, deprecated symbols,
multiple-testing correction, batch structure, and whether a cited accession or PMID was ever verified.

Probes:
1. "You said batch effects — which batch variable, in which dataset, and how would I see it in the
   output?" *(the general form: name the variable, the data, and the visible symptom)*
2. "What is the negative control, and what does it look like when it fails?"
3. "Should the skill check its own output before returning it? On what?"

> **Good:** "A gene with `|log2FC| > 5` and `baseMean < 10` looks like the strongest hit and is almost
> always a near-zero-denominator artifact. The check is: no called hit may have `baseMean` below the
> 10th percentile of the tested set, and if one does the run should say so rather than rank it first."
>
> **Thin:** "Watch out for false positives." — Nothing to bind a gate to. Probe 1.

Each answer must end up as: the claim + the number that makes it concrete + the artifact field that
records whether it fired. A caveat with no field behind it is prose.

## Q4 — Where is the line between validated and a hypothesis?

*And what number decides?*

Default: cap the skill at hypothesis-generating; claim no validated tier at all.

Probes:
1. "What would have to be true for you to put this in a deck for someone senior?"
2. "Is there a threshold below which you would not report the result at all?"
3. "Who would object to the strongest sentence this skill could write, and why?"

> **Good:** "Validated needs an independent cohort replicating direction at `padj < 0.05`. With one
> cohort it is hypothesis-generating no matter how strong the p-value, and the skill must not use the
> word validated in that case."

This becomes a tier gate: the skill computes which tier it reached, and the report's language is
constrained by the computed tier rather than by the agent's enthusiasm.

## Q5 — Who reads it, and which figure do they need?

*Who reads the output and what do they do next? Which single number or table do they need — and for
each analysis step, which figure would they need to believe its result?*

Default: a bench scientist who needs the ranked results table, the report, and one figure per analysis
step.

Probes:
1. "What decision does this output change?"
2. "If they could keep one figure, which one?"
3. "For each analysis step — what would you have to see to believe it worked?"

Probe 3 produces the `## Figures` manifest. A step whose result has no figure is a number the reader
takes on trust; if a step genuinely has nothing to plot, record that reason rather than inventing a
figure for it.

## Q6 — Sources and terms

*Every data source and package: where from, what terms?*

Default: permissive-licensed sources only; anything with unclear terms becomes a stated blocker rather
than a quiet dependency.

Probes:
1. "Is any of this behind a licence or a registration?"
2. "Does any source require attribution or share-alike in the output itself?"
3. "Is the input the user's own data, or something we redistribute?"

An honest blocker is a deliverable. "This needs a database we cannot use commercially" is a useful
answer that saves the work.

## Q7 — What already exists?

*What have you already written for this?*

Default: nothing written; scripts are authored fresh. (This is often the true answer — it is complete,
not thin.)

Probes:
1. "Is there a script that already does part of this, even a rough one?"
2. "Which part of this is fragile and must not be rewritten from memory?"

The answer sets freedom levels: fragile and consistency-critical code goes into `scripts/` with the
exact invocation; things to adapt go into `references/`; genuinely open choices stay as prose.

---

## The yield rule

Q2, Q3 and Q4 *are* the interview. Q1 and Q5 shape the interface; Q6 and Q7 are logistics. If you get
one substantive sentence out of Q2, Q3 and Q4 each, you have enough to write a real skill. If you get
three generic sentences, you have a template with good headings, and no reviewer will be able to tell.

Probe once, specifically, and take the second answer. Probing a third time reads as an interrogation
and the answers get worse, not better.
