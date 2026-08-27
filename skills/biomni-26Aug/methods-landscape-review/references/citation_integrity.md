# Citation Integrity Protocol (mandatory)

This skill's credibility rests on **never fabricating a number or a citation**. The
verification gate (Step 5) is blocking: nothing enters figures or report text until it passes.

---

## The two failure modes this prevents

1. **Fabricated / paraphrased citation fields.** After long sessions or context compaction,
   a *correct DOI* can get paired with an *invented title*, or an author/year/journal can
   drift. Real example: a real paper with DOI `10.1186/s13059-024-03231-9` was cited with a
   plausible-but-wrong title. The DOI, authors, journal, and paraphrased argument were all
   correct — only the title slot was invented. **Titles must be copied verbatim from the
   retrieved record.**
2. **Numbers from memory.** Summaries drop or alter specifics (sample sizes, FDR, effect
   sizes, dates). Any quantitative value in the report must trace to a retrieved record or a
   full-text read that is itself logged.

## What the gate checks

For **every** citation used in the report:
- `title`, `authors`, `year`, `journal`, `doi`, and any accession/NCT/URL are present in the
  retrieved records (`corpus.csv` / `references.jsonl`).
- Title is byte-for-byte from the record (allow only whitespace/HTML-entity normalization).

For **every** quantitative value used in a figure caption or report sentence:
- The number (and its context) appears in an abstract/record OR in a logged full-text read.

## Where to verify

1. **Primary**: the retrieved records — `corpus.csv` and
   `/mnt/results/execution_trace/references.jsonl` (structured records + full abstracts).
2. **After compaction**: also re-check the verbatim transcript at
   `/mnt/results/execution_trace/transcript.jsonl` — one JSON message per line, holding the
   original tool outputs behind any summary. Search it directly:
   ```bash
   rg -i "<keyword>" /mnt/results/execution_trace/transcript.jsonl
   ```
   Use this for both numbers and citation fields whenever the working state came through a
   summary rather than fresh tool output.

## Outcomes

- `clean` — all citations and all figure-critical numbers verified.
- `partial` — some items could not be verified; they were **dropped or explicitly flagged**
  in the report, and the report says so.
- `failed` — core claims could not be verified; do not present the report as authoritative;
  surface the problem to the user.

Record the true status in `citation_verification.json` and state it in the report's Methods
section. **Prefer dropping a claim over guessing.**

---

## Retrieval integrity (Step 1–2): avoid starving the corpus

A recency-biased search is its own integrity risk — it can silently omit the foundational
papers a fair comparison requires.

- **Do NOT over-restrict `year_min`.** Foundational method/tool papers are frequently
  2009–2014; a `year_min` set to "recent" will bury them. Retrieve foundational papers
  explicitly (query by tool name + "method"/"algorithm").
- Use a **multi-query** strategy (foundational, benchmark/comparison, recent advances) and
  dedup, rather than one broad query that gets truncated by a result cap.
- When a provider returns newest-first and truncates, the classics vanish — issue targeted
  name-anchored queries to recover them.
- `WebSearch`/`WebFetch` are for reading the full text of an **already-identified** paper,
  not for primary discovery. This keeps every citation anchored to a retrieved record.

---

## Benchmark-specific caveats to carry into the report

- **Self-referential gold standards**: some benchmarks use each tool's own full-data output
  as truth — note it.
- **Simulation assumptions / permutation nulls**: results depend on the generative model;
  normalization choices can manufacture false positives — present debated recommendations as
  debated, with both sides cited.
- **Preprocessing sensitivity**: pipeline/normalization can matter more than the headline
  method choice.
- **Ordinal scorecards are qualitative** syntheses of published findings, not re-measured
  metrics — label them as such in the figure and caption.
