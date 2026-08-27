# Citation Integrity (the blocking gate)

Fabricated or drifted citations are the #1 failure mode of evidence-synthesis skills,
especially **after a session is compacted** (the classic symptom: a correct DOI paired with an
invented or paraphrased title). This gate is **mandatory and blocking** — the report is not
built until it returns `clean` (or `partial` with every remaining flag consciously resolved).

## What must be verified

For **every** claim that enters `synthesis.json` / `references.json`:
1. **Every quantitative value** (odds ratio, %, effect size, p-value, fracture-reduction,
   sample size, trial phase, date) traces to a specific retrieved record.
2. **Every citation field** — title, authors, year, journal, DOI, and any NCT/accession —
   matches the retrieved record. **Titles are copied verbatim**, never paraphrased.
3. Each `[n]` marker used anywhere resolves to a record in `references.jsonl` **and** has a
   corresponding entry in `references.json` (the report's References section). An index that
   exists in `references.jsonl` but is missing from `references.json` renders an unresolvable
   `[n]` in the PDF body and must be flagged as `failed` by the gate.

## Two-layer check

**Layer A — against `references.jsonl`** (the LiteratureSearch structured records for this
run). Extract every `[n]` index from `evidence_matrix.csv` (`cites`), `consensus_calls.csv`
(`key_flag`), and `synthesis.json`; confirm each maps to a stored record and that the
`references.json` text matches that record's title/authors/year/journal/DOI. **Also confirm
every used `[n]` index has a corresponding entry in `references.json`** — an index that
resolves in `references.jsonl` but is absent from `references.json` produces an orphan `[n]`
marker in the PDF body that no reader can resolve; the gate flags these as `failed`.

The DOI check has **two layers**:
- **Blob check** — the DOI printed in `references.json[n]` must appear somewhere in the
  retrieved records (or `transcript.jsonl`). This catches fully invented DOIs.
- **Per-record correspondence check** — when the retrieved record `references.jsonl[n]`
  carries its own `doi` field, the DOI printed in `references.json[n]` must match *that
  record's* DOI, not merely exist somewhere in the blob. This catches the classic
  post-compaction / duplicate-reference failure where a correct title is paired with a DOI
  that belongs to a *different* paper (e.g. an Abifadel 2003 Nature Genetics title paired with
  a Circulation: Cardiovascular Genetics DOI). A mismatch here is flagged as `failed` even
  though the DOI is real.

**Layer B — against `transcript.jsonl`** (the verbatim tool-output history; essential
post-compaction). Re-check any specific number, drug name, trial result, or citation string
with a targeted search before it goes in the report:
```bash
rg -i "romosozumab|vertebral|FRAME" /mnt/results/execution_trace/transcript.jsonl
rg -i "I148M|ABHD5|gain.of.function" /mnt/results/execution_trace/transcript.jsonl
```
If `transcript.jsonl` is **missing**, state that verbatim recovery is unavailable and do not
claim original wording/numbers from the compacted summary alone.

## `doi_layer_status`

- `clean` — every citation field and value verified; proceed.
- `partial` — some records legitimately lack a DOI (books, GeneReviews chapters, conference
  abstracts) but are otherwise verified; proceed only after consciously confirming each.
- `failed` — at least one value/title could not be verified → **fix or drop** before building.

The gate script **exits non-zero** unless status is `clean`/empty, so it acts as a real
blocker in the pipeline.

**`synthesis.json` must mirror the gate.** Set `synthesis.json` `doi_layer_status` to the
exact value written by `verify_citations.py` into `data/citation_verification.json`. The PDF
Methods section reads the gate file directly as ground truth, so a self-reported `clean` in
`synthesis.json` when the gate says `partial`/`failed` will appear as an internal
contradiction in the report.

## Rules

- **Prefer dropping a claim over guessing.** An unverifiable number or title is removed or
  explicitly flagged, never invented.
- **Verbatim titles only.** Do not "clean up" a title from memory.
- Legitimate short-form labels in a `defining_paper`/`source` field (e.g. "Cohen et al. 2006")
  are fine and should not be false-flagged.
- Normalize non-ASCII punctuation in stored titles for the PDF (see `reporting_notes.md`) —
  but normalize the *rendering*, never the *facts*.
