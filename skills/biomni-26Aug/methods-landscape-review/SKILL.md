---
id: "skill_685b09d4f2d509ab9198bf01121d7c7d"
name: methods-landscape-review
description: "Use to compare computational methods, tools, algorithms, or analytical approaches and recommend which to use from published benchmarks. Produces a comparison matrix, performance scorecard, benchmark catalog, and regime-specific guidance; also supports citation-verified narrative scientific evidence synthesis with an evidence table."
category: literature
visibility: public
starting-prompt: "Compare methods for <task> and recommend which to use, with a PDF report"
---

# Comparative Methods & Benchmark Landscape

Turn a fuzzy "which method should I use?" or "what does the literature say about X?" into a
**decision-complete, citation-honest PDF** grounded in the published record. This skill
generalizes a benchmark-synthesis workflow: retrieve → screen → extract → **verify every
claim** → visualize → recommend → report.

**This skill is search + synthesis + structured reporting**, not an analysis that runs the
methods on data. It tells the user *which* method to use and *why*, and points them to the
Biomni resources/skills that actually *run* the chosen method.

---

## When to Use This Skill

Trigger on requests like:
- **Comparison mode** (primary): "Compare DESeq2 vs edgeR vs limma-voom", "which aligner
  should I use for RNA-seq?", "benchmark batch-integration methods for scRNA-seq",
  "pros/cons of Harmony vs scVI", "best variant caller for long reads", "methods landscape
  for imputation".
- **Topic mode**: "synthesize the evidence on ambient-RNA removal in droplet scRNA-seq",
  "state of the art in spatial deconvolution", "what does the literature say about X".

If the request names competing methods/tools OR asks *which* to use for a task → **comparison
mode**. If it asks to survey/summarize a topic without a method-vs-method framing → **topic
mode**. If ambiguous, ask one short clarifying question.

Do **not** use this skill to actually execute a method on the user's data — hand off to the
relevant sibling skill for that (see Step 7).

---

## Scope

- **Does**: multi-query literature retrieval, PRISMA-style screening, method/benchmark/claim
  extraction, a blocking citation-verification gate, data-driven + conceptual figures, a
  Biomni-resource inventory, and a Phylo-branded PDF with decision guidance.
- **Does NOT**: run the compared methods on data, download datasets for analysis, fabricate
  any number or citation, or re-implement PDF chrome (that is delegated to
  `pdf-report-generation`).

## Inputs

- A **task / question** (free text). For comparison mode, optionally an explicit **set of
  candidate methods/tools**; if not given, propose a canonical set and confirm with the user.
- Optional **scope boundaries** (e.g., "bulk RNA-seq only; not single-cell"), and optional
  retrieval filters (year range, study type, journal quartile, human-only, min sample size).

## Outputs (all under `/mnt/results/<run_dir>/`)

- `references.jsonl` (structured retrieved records; written by LiteratureSearch to
  `/mnt/results/execution_trace/references.jsonl`) + a consolidated `corpus.csv`.
- `screening_log.csv`, and for comparison mode: `comparison_matrix.csv`,
  `benchmark_catalog.{json,csv}`, `performance_claims.{json,csv}`; for topic mode:
  `evidence_table.csv`.
- `citation_verification.json` (reports `doi_layer_status`: clean / partial / failed).
- Figures (`.png` + `.svg`) + `fig_manifest.csv`, and one **infographic** (`.png`).
- `synthesis.json` (agent-authored narrative) + `references.json` (verbatim verified
  references) — the inputs the report builder lays out.
- Final `report_<slug>.pdf`.

---

## Workflow

Run steps in order. Create a run directory first, e.g.
`RUN=/mnt/results/methods_landscape_<slug>` and `mkdir -p $RUN`.
All scripts live in this skill's `scripts/`; reference docs in `references/`.

### Step 0 — Align (brief)
Confirm mode, candidate methods (comparison mode), scope boundaries, and depth
(abstract-level vs. targeted full-text on pivotal benchmarks). Keep it to 1–2 questions; if
the user already gave enough, proceed.

### Step 1 — Plan queries (anti-recency-bias)
`python scripts/plan_queries.py --task "<task>" --methods "<m1,m2,...>" --mode <comparison|topic> --out $RUN/queries.json`
Produces a multi-query set: foundational/tool papers, benchmark/comparison papers, and
recent advances. **Do NOT over-restrict `year_min`** — foundational method papers are often
2009–2014 and must not be filtered out (a real failure mode: recency-biased search buries the
classics). See `references/citation_integrity.md`.

### Step 2 — Retrieve (LiteratureSearch-first)
Use the Biomni **`LiteratureSearch`** tool for each planned query (it writes structured
records + abstracts to `/mnt/results/execution_trace/references.jsonl`). Then consolidate &
dedup:
`python scripts/retrieve_literature.py --refs /mnt/results/execution_trace/references.jsonl --out $RUN/corpus.csv`
(optional: `--curated curated_records.json` to fold in any hand-added records).
- Retrieval is peer-reviewed-first. `WebSearch`/`WebFetch` are allowed **only** to read the
  full text of an *already-identified* paper (for exact numbers), never as the primary
  discovery channel — this keeps every citation grounded in a retrieved record.
- Expose optional filters (year, study type, `sjr_max`, `human`, `sample_size_min`).

### Step 3 — Screen & extract
`python scripts/screen_and_extract.py --corpus $RUN/corpus.csv --task "<task>" --methods "<...>" --mode <...> --out $RUN`
- PRISMA-style include/exclude with reasons → `screening_log.csv`.
- Comparison mode → per-method extraction, `benchmark_catalog`, and `performance_claims`
  tagged by **evidence thickness** (`head_to_head` > `multi_benchmark` > `single_benchmark`).
- Topic mode → `evidence_table.csv` (theme, finding, study type, effect, citation). For the
  topic-mode figures (Step 6), also derive `theme_table.csv` by aggregating the populated
  `evidence_table.csv` per theme (`theme, n_papers[, consensus_level, evidence_quality]`).
- Schemas: `references/schemas.md`.

### Step 4 — Targeted full-text (optional, pivotal only)
For the handful of pivotal benchmark/keystone papers, read full text via `WebFetch` to pull
**exact numbers** (sample sizes, FDR, concordance, effect sizes). Record each number with its
source DOI into the `performance_claims` / `evidence_table`. Do not scale this to the whole
corpus.

### Step 5 — CITATION-VERIFICATION GATE (mandatory, blocking)
`python scripts/verify_citations.py --run $RUN --refs /mnt/results/execution_trace/references.jsonl --transcript /mnt/results/execution_trace/transcript.jsonl`
- Confirms **every** quantitative value AND **every** citation field (title, authors, year,
  journal, DOI, accession/NCT) against the retrieved records, and re-checks against the
  working `transcript.jsonl` (essential after a session has been compacted). See
  `references/citation_integrity.md`.
- Emits `citation_verification.json` with `doi_layer_status` ∈ {clean, partial, failed} and a
  list of any dropped/flagged items. **Exits non-zero when status is not clean/empty, so it
  acts as a real blocking gate** — do not proceed to the report until it returns `clean` (or
  `partial` with every remaining flag consciously resolved).
- The gate flags a genuinely unverifiable DOI or a fabricated *full* title, but does **not**
  false-flag legitimate short-form citation labels (e.g. "Schurch et al. 2016") in the
  `defining_paper`/`source` fields.
- **Any claim that cannot be verified is dropped or explicitly flagged — never guessed.**
  Titles in particular must be copied verbatim from the record, never paraphrased (a known
  post-compaction failure is a correct DOI paired with an invented title).

### Step 6 — Figures (data-driven) + infographic (GenerateImage)
`Rscript scripts/make_figures.R --run $RUN` (Python fallback:
`python scripts/make_figures.py --run $RUN`). Mode is **auto-detected** from which artifacts
exist (comparison-mode vs `theme_table.csv`); an optional `--title-prefix "..."` prepends a
label to every figure title. Each script writes `.png`+`.svg` plus a `fig_manifest.csv`
(file, mode, caption) that the report builder consumes.
- Comparison mode: comparison-matrix heatmap, ordinal **performance scorecard** (label it
  explicitly as a *qualitative synthesis of published findings, not re-measured metrics*),
  benchmark-design catalog, evidence-thickness distribution, method/benchmark timeline, and
  any quantitative head-to-head panel the evidence supports.
- Topic mode: theme map, evidence-quality panel, timeline.
- **After each figure, run `Read` with `mode="media_output_check"`; regenerate on failure.**
- **Infographic**: build the one-page conceptual summary with the **`GenerateImage`** tool
  (task, contenders, headline tradeoffs, decision rule) — do NOT hand-draw it in
  matplotlib/ggplot. A prompt template is in `references/reporting_notes.md`.
- Style: Okabe-Ito / colorblind-safe palette, Liberation Sans, `theme_prism`; PNG + SVG.

### Step 7 — Inventory Biomni resources (deep)
Start from `references/biomni_resources_catalog.md`; verify packages with direct
imports and HPC tools with `hpc_search_tools`. Use `Skill` to find **sibling skills**
relevant to the task. Produce a short "Relevant Biomni resources & how to run
this" mapping, and **cross-reference the sibling skill(s) that actually execute
the recommended method** (e.g., DE-tool comparison →
`bulk-rnaseq-differential-expression`,
`bulk-rnaseq-counts-to-de-deseq2`, `functional-enrichment-from-degs`; enrichment topic →
`pathway-enrichment`; scRNA integration → `scrnaseq-scanpy-core-analysis`).

**Licensing / commercial-use policy (mandatory).** Apply the licensing policy in
`references/biomni_resources_catalog.md` (§0) to every resource you would recommend, including
anything found during direct runtime verification. **Exclude non-commercial
`[NC]` resources** (e.g. KEGG, DisGeNET, OMIM, DrugBank) from the recommendation and add
a one-line note that they were omitted for commercial-licensing reasons (do not curate a
substitute). **Recommend ShareAlike `[SA]` resources** (e.g. ChEMBL, Human Protein Atlas,
PharmGKB) **only with an explicit ShareAlike/attribution caveat.** Untagged resources are
permissive and may be recommended normally.

### Step 8 — Assemble the PDF (via `pdf-report-generation`)
Load the **`pdf-report-generation`** skill for its building blocks, then author
`$RUN/synthesis.json` (ALL narrative text — title, subtitle, mode, executive_summary,
methods, results_intro, results_sections [each may name a `figure`], discussion,
limitations, next_steps, callouts) and `$RUN/references.json` (ordered `[{n, text}]`,
verbatim verified references). **`build_report.py` is a layout engine, not a content
author** — every sentence in `synthesis.json` must already be source-bound and past the
Step 5 gate. Then build:
`python scripts/build_report.py --run $RUN --out /mnt/results/report_<slug>.pdf [--infographic $RUN/<infographic>.png]`
(title/mode come from `synthesis.json`; the builder embeds only artifacts/figures that
exist). See `references/reporting_notes.md` for the full `synthesis.json` schema.
Sections, in order: title page → **infographic** → executive summary → introduction →
methods (retrieval + verification protocol + `doi_layer_status`) → results (figures +
matrix/catalog/claims OR topic synthesis + evidence table) → discussion with
**regime-conditional decision guidance** and honest treatment of genuine disagreements →
limitations → **next steps** → relevant-Biomni-resources → references (verbatim, verified).
- Validate: `pypdf` structural check (page count, extractable text) **and** a `Read`
  `media_output_check` of the final PDF. Fix and re-check on any failure.

---

## Scientific Caveats & Integrity Rules

- **No unconditional "winner."** Method recommendations are **regime-conditional** (sample
  size, data quality, design). State the conditions.
- **Present genuine disagreements as disagreements**, not resolved facts (e.g., a debated
  benchmark recommendation should show both sides with citations).
- **Every figure value traces to a specific paper.** Ordinal scorecards are qualitative
  syntheses and must be labeled as such — never presented as re-measured metrics.
- **Citation honesty is non-negotiable** (Step 5). Report the real `doi_layer_status`. Copy
  titles verbatim from records. Prefer dropping a claim over guessing.
- **Benchmarks have caveats** (self-referential gold standards, simulation assumptions,
  permutation nulls, preprocessing sensitivity) — surface them.

## Failure Modes to Avoid

- Recency-biased retrieval that buries foundational papers (mitigate in Step 1).
- Hallucinated/paraphrased citation fields after compaction (mitigate in Step 5 +
  transcript re-check).
- Empty/placeholder figure panels or noisy auto-extracted tokens (mitigate with the media
  check in Step 6; prefer curated, source-bound values over raw abstract mining).
- Over-restricting `year_min` or study-type filters and starving the corpus.

## Environment Notes

- Python: `reportlab`, `pypdf`, `pandas`, `requests` (all preinstalled). R: `ggplot2`,
  `ggprism`, `dplyr`, `tidyr`, `patchwork`, `ggrepel`, `RColorBrewer` (preinstalled).
- Compute is trivial (API calls + light plotting + PDF). Runs on the default sandbox; no
  GPU/HPC needed.
- R `file.copy()` to `/mnt/results` yields 0-byte files — write figures directly to
  `/mnt/results/...` or stage in `/workspace` then shell `cp`.
