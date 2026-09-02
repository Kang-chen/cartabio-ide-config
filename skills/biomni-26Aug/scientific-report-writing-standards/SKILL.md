---
id: "skill_d76bd8e854ac8f694859a6f0de5ace68"
name: "scientific-report-writing-standards"
description: "Provides report writing standards for biomedical analyses covering structure, discipline, data integrity, evidence tiers, figures, citations, and bioinformatics norms Defines evidence tier language constraints bounding the strongest sentence a report may write Use when writing or reviewing a scientific report for a biomedical or bioinformatics analysis."
category: "reporting"
visibility: "internal"
starting-prompt: "What standards should I follow when writing a scientific report for my biomedical analysis?"
---

<!-- archetype: correctness-guidance -->
<!-- contract: evidence-v1 -->

# Scientific Report Writing Standards

## When to Use This Skill

Use this skill when you need to write, structure, or review a scientific report for a biomedical or bioinformatics analysis. It provides comprehensive guidance on report anatomy, writing discipline, data integrity rules, evidence tier language, figure and table standards, citation conventions, and bioinformatics-specific reporting norms.

**Use when:**
- You have completed an analysis and need to write a report that is rigorous enough to share externally, submit for review, or include in a publication.
- You are reviewing a draft report and need to check it against writing standards.
- You are producing structured content (`report_content.json`) for `phylo-pdf-report-generation` and need to know what belongs in each section.

**Do NOT use when:**
- You need to render a PDF from already-structured content (use `phylo-pdf-report-generation` directly).
- You need to design a laboratory protocol (use a protocol skill).
- You need a quick chat answer with no structure or evidence.

## Why Derive, Don't Restate (READ FIRST)

A competent practitioner who has never written a Biomni-style report writes the report from the SKILL.md instead of from the run — sentences about "the workflow performed DESeq2 analysis" rather than "3 of 412 genes were differentially expressed at padj < 0.05". They write generic limitations ("batch effects may confound results") instead of run-specific ones ("condition and batch were not orthogonal; batch was included as a covariate"). They hand-type numbers into prose that then disagree with the exported table. They use "validated" for a single-cohort result. They put conclusion sentences in figure titles.

The fix for all of these is the same principle: **derive, don't restate**. A report that prints its count by reading the table cannot disagree with the table. Numbers in prose must be derived from artifacts at render time, not hand-typed. The evidence tier must be computed from the run, not chosen by the agent. Figure captions must state what the figure shows, not what it is called. Limitations must be specific to this run, not generic boilerplate.

## Inputs

The input is a completed scientific analysis (typically biomedical/bioinformatics) that has produced results requiring documentation. This may include:

- Differential expression results (DESeq2/edgeR/limma-voom tables, volcano plots, MA plots)
- Survival analysis output (Kaplan-Meier curves, Cox model coefficients, log-rank tests)
- Single-cell analysis results (UMAP embeddings, marker gene tables, cell-type annotations)
- Variant calling results (VCF summaries, annotation tables, QC metrics)
- Pathway enrichment results (ORA/GSEA output, enrichment maps)
- Any other analysis producing quantitative results, figures, and tables

The skill does not process data files — it provides guidance on how to structure and write the report that documents the analysis.

## Outputs

- `report_scientific_report_writing_standards.pdf` — Generate the PDF report with `pdf-report-generation` by default. When the user explicitly selects a compatible report-styling skill, use that provider instead for presentation only; keep every report, evidence, artifact, infographic, and review requirement unchanged. Include a Biomni GenerateImage infographic when required, task context, methods or sources, results, conclusions, figures where applicable, references, and next steps
- `report_content.json` — the structured content schema ready for `phylo-pdf-report-generation` to render; a row is one content block (paragraph, figure, table) within a section
- Facts artifact — `not_applicable`; the machine-readable reason is in `skill_contract.json`


**Write the report to the results root under the name above.** Data tables, figures and intermediates go in `data/`, `figures/`, `tables/`. Note that `GenerateImage` strips directory components, so schematics always land at the root regardless of the path you pass it.

## Clarification Questions

1. **<question_id:q1_analysis>** What analysis results do you need to report?
   - `<choice_id:differential_expression>` Differential expression (RNA-seq bulk or single-cell)
   - `<choice_id:survival>` Survival / time-to-event analysis
   - `<choice_id:variant_calling>` Variant calling / genomics
   - `<choice_id:pathway>` Pathway or functional enrichment
   - `<choice_id:other>` Other analysis type — describe it

2. **<question_id:q2_audience>** Who is the audience for this report?
   - `<choice_id:bench_scientist>` Bench scientist — needs ranked results, clear conclusions, one figure per result (recommended default)
   - `<choice_id:pi>` Principal investigator — needs methods detail, effect sizes, and limitations
   - `<choice_id:regulatory>` Regulatory / clinical — needs full reproducibility, software versions, parameter files
   - `<choice_id:publication>` Publication-ready — needs rigorous statistical reporting, figure standards, citation formatting

3. **<question_id:q3_tier>** What evidence tier does your analysis support?
   - `<choice_id:hypothesis_generating>` Single cohort, no independent replication (most common — use cautious language)
   - `<choice_id:validated>` Independent cohort replication at padj < 0.05 or equivalent
   - `<choice_id:exploratory>` No formal statistical testing or post-hoc analysis
   - `<choice_id:unsure>` I'm not sure — help me determine the appropriate tier

## Standard Workflow

### Step 1 — Assess report need and determine evidence tier

Determine whether a report is warranted:
- **Write a report** when: the analysis has multiple steps with substantial results, the user explicitly requests a report, or the results warrant standalone documentation.
- **Do NOT write a report** when: the answer fits in a paragraph with no figures or tables (it's a chat answer), or it's a simple lookup or single computation.

Determine the evidence tier from the run:
- **Validated**: independent cohort replicating direction at padj < 0.05 (or equivalent). May use "validated", "confirmed", "demonstrates".
- **Hypothesis-generating**: single cohort, no replication. Must NOT use "validated", "confirmed", "establishes", "demonstrates". Use "suggests", "indicates", "is consistent with".
- **Exploratory**: no statistical testing or post-hoc analysis. Use "observed", "appears to", "may".

The tier is computed from the run, not chosen by the agent. If the analysis has no independent replication, the tier is hypothesis-generating regardless of p-value strength.

**Success looks like:** a clear decision on whether to write a report, and a determined evidence tier with its language constraints.

### Step 2 — Structure the report content

Apply the five mandatory sections in order. Each section has specific content requirements:

**1. Task Context** — The question, inputs, scope, and decision the output informs. State what was asked, what data was used, what comparison was made, and what decision this report supports.

**2. Methods & Sources** — The actual analysis method and data. For each step: the parameters actually used, software versions, the model formula or algorithm configuration. State data sources with their licenses. Parameters the skill pinned rather than chose at runtime should say so. Include before-and-after counts: "20,531 genes → 14,208 after independent filtering".

**3. Results** — Actual run outputs. Summary statistics, top-results tables, every declared figure embedded with its caption, and explicit warnings where a statistical concern fired. Numbers in prose must be derived from the exported artifacts, not hand-typed.

**4. Conclusions & Interpretation** — Three to five findings, each with a number. Practical meaning. Suggested next steps. Language constrained by the evidence tier.

**5. Limitations** — Run-specific uncertainty, missing coverage, unavailable checks, and unsupported claims. NOT generic boilerplate. Each limitation should reference something specific about this run.

**Success looks like:** a `report_content.json` with all five sections populated with run-specific content, not generic filler.

### Step 3 — Apply writing discipline and bioinformatics norms

While writing each section, apply these rules:

**Writing discipline:**
- The report describes the analysis, never the skill. No sentences about workflow steps or features.
- No flattery or filler. Start with substance.
- Titles are noun phrases: "Differential Expression: Treated vs Control" — not "Analysis Reveals Important Insights".
- Captions state what the figure shows: "3 of 412 genes pass at padj < 0.05, all in one direction" — not "Volcano plot".
- One figure per analysis step. A result with no figure is a number the reader takes on trust.
- Superscripts and subscripts use the renderer's markup (`<super>`, `<sub>`), not Unicode characters.

**Data integrity:**
- Never fabricate data. All findings must come from provided data or correctly retrieved external sources.
- Before-and-after counts for every filtering step.
- Gate expectations from outside the run: thresholds come from skill package literals or pre-run parameter files, not from the run's own output.
- Caveats are gates, not prose: each caveat carries the claim, the number that makes it concrete, and the artifact field recording whether it fired.

**Bioinformatics-specific norms:**
- Gene IDs: state whether Ensembl (with/without version suffix), Entrez, or gene symbols. Note that Ensembl version suffixes trip joins.
- Genome build: always state GRCh37 vs GRCh38 (or equivalent). Chromosome naming convention (UCSC `chr1` vs Ensembl `1`).
- Statistical reporting: name the test, the correction method, the threshold. Report effect sizes, not just p-values. Write "padj < 0.05" not "significant".
- Normalization: state which normalization (CPM, TPM, DESeq2 size factors, etc.) and whether data is log-transformed.
- Batch effects: state whether batch correction was applied, which variable, and whether condition and batch are orthogonal.
- Reproducibility: software versions, parameter files, random seeds. Output-files table (filename → what it is).

**Citation standards:**
- Inline numbered references: `[1]`, `[2]`, combined as `[1, 2, 3]`.
- Never invent citations. Verify from source records.
- No separate bibliography section. Inline citations only.
- Database record badges: format as `[[UniProt:P04637]]` with descriptive text before the badge.

**Figure and table standards:**
- Color: colorblind-friendly palettes. Phylo palette for HTML/interactive.
- Fonts: Liberation Sans. In Python: `matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']`.
- Export: save both `.svg` and `.png`. Keep SVG text editable: `matplotlib.rcParams['svg.fonttype'] = 'none'`.
- Text on plots: titles and axis labels are short noun phrases. No methods parentheticals, no conclusion restated in title. No explanatory notes, takeaway callouts, or summary boxes on the figure.
- Tables: header row clearly labeled. Numeric columns right-aligned. Gene/protein IDs in monospace. Significant figures consistent within a column.

**Success looks like:** all content blocks follow the writing discipline, data integrity rules, and bioinformatics norms.

### Step 4 — Final report (MANDATORY TERMINAL STEP)
**The run is not complete until this step has produced `report_scientific_report_writing_standards.pdf` at the results root.**
Generate the PDF report with `pdf-report-generation` by default. When the user explicitly selects a compatible report-styling skill, use that provider instead for presentation only; keep every report, evidence, artifact, infographic, and review requirement unchanged. Include a Biomni GenerateImage infographic when required, task context, methods or sources, results, conclusions, figures where applicable, references, and next steps

Produce one combined PDF with a short narrative. rendered page images are inspection evidence only, never substitutes for the PDF. End the task with a concise conclusion and links to the PDF and supporting artifacts, not a bare file listing.

The PDF must use these visible top-level sections in this order, adapting their contents to this skill rather than adding generic filler:

1. `Task Context` — the research or practitioner question, supplied inputs, scope, and decision the output informs.
2. `Methods & Sources` — Methods & Sources covers the rules, authorities, and evaluation approach; Results presents the guidance, decisions, counterexamples, and validation outcomes.
3. `Results` — the run's actual outputs and evidence, never a description of what the skill could do.
4. `Conclusions & Interpretation` — supported takeaways, their practical meaning, and appropriate next steps.
5. `Limitations` — run-specific uncertainty, missing coverage, failed or unavailable checks, and claims the evidence does not support.

Include references and a compact output-artifact table where applicable. Empty boilerplate does not satisfy a section.

An infographic is not applicable: Guidance skill teaches writing standards; does not generate qualitative infographics. Do not call `GenerateImage` for decorative compliance.

Render the PDF to a fresh workspace file named by `workspace_report_file`; do not open or truncate an existing PDF on the object-backed results mount. The `staged_copy` call below publishes the completed file under its declared results-root name.

Then verify the run and write its receipt with a single call. `write_receipt` runs every gate — the report exists at the results root and is big enough, each declared figure is present and non-blank, the exact infographic came from a same-ID `GenerateImage` call and result and is the first embedded image on page 1, and the finished PDF carries the markers declared by the resolved report-style provider — then records what each one returned. Load and follow the selected provider skill's complete report instructions and assets. QC prefers a provider-owned `report_style.json` under that provider's assets; when an existing installed provider has none, it derives only its declared aliases and PDF marker colors from that provider's immutable `SKILL.md`. Neither source is a theme recipe. Never stage, synthesize, or copy provider evidence into a workspace or results directory. A missing or ambiguous installed provider source is a blocker, not permission to reconstruct one. Run bundled commands through `run_bundled`, which writes the QC-owned `qc_run_log.json` from the subprocess result and output hashes. Do not author execution events or copy transcript identifiers. Record PDF visual review as an explicit attestation; it is not described as independently verified. Produce every source-witness artifact declared in `skill_contract.json`. The receipt writer rejects unmatched hashes, partial page coverage, a visual-review verdict other than pass, any unresolved visual-review issue, or source-value disagreement. Fix and rerender every failed page or figure; listing a visual defect under Limitations does not make it pass. It raises if any gate failed, **after** writing the receipt, so a failed run leaves the diagnostic behind.

Record each answer from `## Clarification Questions` in `selected_branch_ids` using the displayed `<question_id>:<choice_id>` value. The receipt derives its required outputs only from those selected branches; do not union mutually exclusive branch artifacts. The receipt derives any explicit report-style provider from immutable user messages and otherwise resolves the default from `skill_contract.json`; a caller variable cannot authorize an override. Never infer styling from the enterprise, account, project, or customer context. The absence of an affirmative styling directive is not ambiguity: use the contract default without asking a styling clarification. Ask only when user messages contain conflicting affirmative selections or request a provider that cannot be resolved safely. The fenced call below is the stable public API; execute it before reading `report_qc.py`, and inspect helper internals only if a `GateFailure` is not specific enough to act on.

```python
from report_qc import (outputs_for_selected_branches, record_pdf_review, staged_copy,
                       write_receipt)
selected_outputs = outputs_for_selected_branches(selected_branch_ids)
staged_copy(workspace_report_file, "report_scientific_report_writing_standards.pdf")
record_pdf_review(
    report_name="report_scientific_report_writing_standards.pdf", text_artifact=extracted_text_file,
    rendered_page_files=rendered_page_files,
    reviewed_page_numbers=reviewed_page_numbers,
    review_attestation=visual_review_notes,
    review_verdict=visual_review_verdict,
    review_issues=visual_review_issues,
)
write_receipt(
    report_name="report_scientific_report_writing_standards.pdf",
    figures=[],
    figure_not_applicable_reason='',
    bundled_files=[],
    outputs=selected_outputs,
    infographics=[],
    qc_run_log="qc_run_log.json",
)
```

The receipt is `run_receipt.json` at the **results root**, not beside this SKILL.md — once this skill is installed its own directory is read-only, so a per-run receipt written there cannot work. It records `execution_contract_satisfied`, `outputs_appeared`, `report_at_results_root`, `figure_contract_satisfied`, `facts_artifact_verified`, `report_style_verified`, `text_extracted`, `pages_rendered`, `visual_review_attested`, `report_sections_present`, `source_assertions_verified`, `infographic_lineage_verified`, each with the path, byte count, colours or transcript record the outcome was read from.

**Do not write this file by hand.** `check_skill.py --require-run-receipt` requires the schema marker and per-outcome evidence, so a hand-written block of `true`s fails. Whatever did not hold is recorded `false` with a `<key>_reason`; fix the run rather than the receipt.

## Existing materials

Nothing existed before this skill. The guidance content was authored fresh, drawing on established biomedical reporting conventions (DESeq2 reporting norms, genome build standards, statistical reporting guidelines). The writing standards are high-freedom prose guidance that the agent adapts to each analysis, so they belong in SKILL.md rather than in a script.

## Scientific caveats

This skill provides guidance — it does not perform analysis or generate evidence. The correctness of any report produced following these standards depends on the underlying analysis being sound.

| Caveat | What makes it concrete | Artifact field |
|---|---|---|
| Evidence tier must be computed from the run, not chosen | A single-cohort result labeled "validated" is always wrong | Evidence tier section in the report |
| Numbers in prose must derive from artifacts | Prose count disagreeing with table count is a defect | Report text vs. exported table |
| Limitations must be run-specific | Generic boilerplate that could apply to any experiment is not a limitation | Limitations section content |

## Evidence Tier

Contract maturity: `generated`. Do not claim a higher tier than the validation matrix supports.

This skill teaches evidence tier rules but does not compute a tier itself. The tier of any report produced following these standards is determined by the analysis skill that generated the results.

### Evidence tier language constraints

| Tier | Criterion | Permitted language | Forbidden language |
|---|---|---|---|
| Validated | Independent cohort replication at padj < 0.05 (or equivalent) | "validated", "confirmed", "demonstrates", "establishes" | — |
| Hypothesis-generating | Single cohort, no independent replication | "suggests", "indicates", "is consistent with", "supports the hypothesis" | "validated", "confirmed", "establishes", "demonstrates" |
| Exploratory | No formal statistical testing or post-hoc analysis | "observed", "appears to", "may", "could" | "validated", "confirmed", "establishes", "demonstrates", "significant" |

## Data Sources & Licenses

- Commercial use: not applicable because no external data source or package dependency is used; see the machine-readable applicability decisions in `skill_contract.json`.
- No external data sources required. This is a guidance skill that encodes writing standards.
- Referenced conventions (DESeq2 reporting, genome build standards, statistical reporting) are publicly available and unencumbered.

## Resource Identity

Not applicable: this skill does not emit citations, accessions, or external identifiers. It teaches citation standards that the analysis skill applies.

## Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| Report reads like a skill description | Written from SKILL.md instead of from the run | Rewrite each sentence to describe what was found, not what the workflow did |
| Prose numbers disagree with table | Numbers hand-typed instead of derived from artifacts | Read counts from the exported table at render time |
| Generic limitations section | Boilerplate copied without run-specific content | Replace each limitation with one that references something specific about this run |
| "Validated" used for single-cohort result | Evidence tier not computed from the run | Downgrade to "hypothesis-generating"; replace "validated/confirmed/demonstrates" with "suggests/indicates/is consistent with" |
| Figure caption says "Volcano Plot" | Caption describes the chart type, not the finding | State what the figure shows: "3 of 412 genes pass at padj < 0.05, all in one direction" |
| Gene ID type not stated | Ensembl/Entrez/symbol convention omitted | Add a sentence in Methods stating the ID type and version suffix convention |
| Genome build not stated | GRCh37 vs GRCh38 omitted | Add the build and chromosome naming convention to Methods |

## Suggested Next Steps

- Use `phylo-pdf-report-generation` to render the structured content into a branded PDF.
- Use analysis skills (e.g., `bulk-rnaseq-counts-to-de-deseq2`, `survival-analysis-clinical`) to produce the results that these standards govern.

## Related Skills

- `phylo-pdf-report-generation` — renders structured content into a Phylo-branded PDF using these standards
- `pdf-report-generation` — the platform's default report styling provider
- `data-analysis-best-practices` — general best-practice guidance for analyzing user-supplied data
