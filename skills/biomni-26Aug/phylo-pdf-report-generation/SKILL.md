---
id: "skill_a6cb8b3518492d2d622830fb552dcde9"
name: "phylo-pdf-report-generation"
description: "Renders a multi-page Phylo-branded PDF from a report_content.json file using ReportLab Platypus Validates generated PDF for page count, text extractability, page size, figure embedding, and blank pages Use this skill to render a Phylo-branded PDF report from structured scientific content (sections, figures, tables, metadata). Usable standalone or callable by other skills."
category: "reporting"
visibility: "internal"
starting-prompt: "How do I render my scientific analysis results as a branded report?"
---

<!-- archetype: format-utility -->
<!-- contract: evidence-v1 -->

# Phylo PDF Report Generation

## When to Use This Skill

Use this skill to render a Phylo-branded PDF report from structured scientific content. It accepts a JSON content document (`report_content.json`) containing metadata, ordered sections, figures, tables, and references, and produces a professionally styled multi-page PDF with Phylo visual identity.

**Use when:**
- You have completed a scientific analysis and need a formal, shareable PDF deliverable.
- Another skill has produced structured results (tables, figures, narrative) that need to be assembled into a branded report.
- You need a reproducible PDF with consistent Phylo branding for collaboration, stakeholder review, or regulatory submission.

**Do NOT use when:**
- You need a quick chat answer (no structure, methods, or evidence needed).
- You need a PowerPoint presentation (use `pptx-generation` instead).
- You need a Word document (use `docx-generation` instead).
- You need to generate scientific content or analysis — this skill renders provided content, it does not create findings.

## Why ReportLab Platypus, Not Canvas API (READ FIRST)

A competent practitioner who has never built a PDF renderer reaches for ReportLab's canvas API and manually places every text string and image at fixed coordinates. This produces a report that looks right on page 1 but breaks on page 2 when content overflows — text runs off the page, figures overlap, tables split across pages without repeating headers. The fix is to use Platypus (flowables), which handles automatic page breaks, text wrapping, and table splitting. This skill uses Platypus exclusively.

A second wrong default: using Unicode superscript characters (², ³) which render as black boxes in many PDF fonts. ReportLab's `<super>` markup must be used instead. This skill's paragraph renderer passes text through ReportLab's paragraph markup, which supports `<super>`, `<sub>`, `<b>`, `<i>`, and `<font>` tags.

A third: relying on ReportLab's default page size (A4) instead of explicitly setting US Letter (612 x 792 pt). This skill sets `pagesize=letter` explicitly in the document template.

## Inputs

### `report_content.json`

A JSON document with this structure:

```json
{
  "metadata": {
    "title": "Differential Expression Analysis: Treated vs Control",
    "subtitle": "RNA-seq DESeq2 results",
    "author": "Biomni Analysis",
    "date": "2026-09-02",
    "project": "Project Name (optional)"
  },
  "sections": [
    {
      "id": "task_context",
      "heading": "Task Context",
      "content": [
        {"type": "paragraph", "text": "The question, inputs, scope..."},
        {"type": "paragraph", "text": "Additional context..."}
      ]
    },
    {
      "id": "methods",
      "heading": "Methods & Sources",
      "content": [
        {"type": "paragraph", "text": "Analysis method description..."},
        {"type": "table", "title": "Software versions", "headers": ["Tool", "Version"], "rows": [["DESeq2", "1.42.0"]]},
        {"type": "paragraph", "text": "More methods..."}
      ]
    },
    {
      "id": "results",
      "heading": "Results",
      "content": [
        {"type": "figure", "path": "figures/volcano.png", "caption": "Volcano plot of DE genes. 3 of 412 genes pass at padj < 0.05."},
        {"type": "table", "title": "Top 10 DE genes", "headers": ["gene", "log2FC", "padj"], "rows": [["TP53", 3.2, 0.001]]},
        {"type": "paragraph", "text": "Summary statistics..."}
      ]
    },
    {
      "id": "conclusions",
      "heading": "Conclusions & Interpretation",
      "content": [
        {"type": "paragraph", "text": "Supported takeaways..."}
      ]
    },
    {
      "id": "limitations",
      "heading": "Limitations",
      "content": [
        {"type": "paragraph", "text": "Run-specific uncertainty..."}
      ]
    }
  ],
  "references": [
    {"id": 1, "citation": "Love MI et al. (2014) Genome Biology 15:550."},
    {"id": 2, "citation": "..."}
  ],
  "output_files_table": [
    {"filename": "data/de_results.csv", "description": "Full DE results table"},
    {"filename": "figures/volcano.png", "description": "Volcano plot"}
  ]
}
```

### Content block types

| Type | Required fields | Description |
|---|---|---|
| `paragraph` | `text` | A body text paragraph. Supports ReportLab markup: `<b>`, `<i>`, `<super>`, `<sub>`, `<font>`. |
| `subheading` | `text` | A sub-section heading within a section. |
| `figure` | `path`, `caption` (optional) | An image file (PNG/SVG/JPEG). Path is relative to the JSON file location or absolute. Auto-scaled to fit page width. |
| `table` | `headers`, `rows`, `title` (optional) | A data table. `headers` is an array of strings; `rows` is an array of arrays. Numeric and gene ID cells are rendered in monospace. |
| `bullet_list` | `items` | An array of strings, rendered as bullet points. |
| `numbered_list` | `items` | An array of strings, rendered as a numbered list. |

### What a stranger would misread

- Figure paths are relative to the JSON file's directory, not the current working directory. An absolute path always works.
- Table cells can be strings or numbers — numbers are converted to strings and rendered in monospace.
- The five sections (Task Context, Methods & Sources, Results, Conclusions & Interpretation, Limitations) should appear in this order. The renderer numbers them sequentially.
- References and output_files_table are optional but recommended for reproducibility.

## Outputs

- `report_phylo_pdf_report_generation.pdf` — Generate the PDF report with `pdf-report-generation` by default. When the user explicitly selects a compatible report-styling skill, use that provider instead for presentation only; keep every report, evidence, artifact, infographic, and review requirement unchanged. Include a Biomni GenerateImage infographic when required, task context, methods or sources, results, conclusions, figures where applicable, references, and next steps
- `report_content.json` — the structured content schema consumed by the renderer; a row is one content block (paragraph, figure, table, etc.) within a section
- Facts artifact — `not_applicable`; the machine-readable reason is in `skill_contract.json`


**Write the report to the results root under the name above.** Data tables, figures and intermediates go in `data/`, `figures/`, `tables/`. Note that `GenerateImage` strips directory components, so schematics always land at the root regardless of the path you pass it.

## Clarification Questions

1. **<question_id:q1_content>** What scientific content do you want in the report?
   - `<choice_id:json_file>` I have a `report_content.json` file ready — use it directly
   - `<choice_id:interactive>` I'll provide content elements (text, figures, tables) interactively — assemble the JSON for me
   - `<choice_id:from_analysis>` I have analysis results from another skill — help me structure them into the content schema

2. **<question_id:q2_figures>** Do you have specific figures or tables to include?
   - `<choice_id:yes_paths>` Yes, I have figure file paths and table data ready
   - `<choice_id:no_figures>` No figures — text-only report
   - `<choice_id:generate_figs>` I need figures generated from my data first (use a plotting skill)

3. **<question_id:q3_metadata>** What metadata should appear in the page-1 title block?
   - `<choice_id:standard>` Title, subtitle, author, date (recommended)
   - `<choice_id:minimal>` Title only
   - `<choice_id:full>` Title, subtitle, author, date, project name

## Standard Workflow

### Step 1 — Collect and validate content

Assemble or receive `report_content.json` with all sections, figures, tables, and metadata. Validate that:
- All five required sections are present (Task Context, Methods & Sources, Results, Conclusions & Interpretation, Limitations).
- All figure paths resolve to existing files (relative to the JSON file or absolute).
- Table headers and rows are well-formed (headers is a list of strings; rows is a list of lists).
- Metadata contains at least a title.

**Success looks like:** a valid `report_content.json` with no missing figure paths and all sections present.

### Step 2 — Render the PDF

Run the rendering script to produce the branded PDF:

```bash
python3 scripts/generate_report.py <report_content.json> <workspace_report.pdf>
```

The script uses ReportLab Platypus to build the Phylo house style (gold `#D4A04A` is the primary accent):
- No cover page: page 1 opens with a title block — 26 pt bold near-black title, 11 pt gold subtitle, and an italic muted attribution line (author, date, project).
- Every page carries a header (muted report title with a thin gold underline) and a footer (warm-gray rule with a centered "Page N").
- Section headings in near-black with a gold underline, kept with their first block via `KeepTogether` so a heading is never stranded at a page bottom; body text in justified warm-dark paragraphs.
- Figures auto-scaled to content width, centered, with italic muted captions bound to the image.
- Tables centered with a gold header row (white bold text), alternating warm off-white body rows, warm-gray grid, and the header row repeated across page breaks.
- References section with numbered, hanging-indent entries.
- Output files table for reproducibility.

**Success looks like:** a PDF file exists at the workspace path, is larger than 5 KB, and has more than one page.

### Step 3 — Validate the PDF

Run the validation script to check integrity:

```bash
python3 scripts/validate_pdf.py <workspace_report.pdf> [expected_figure_count]
```

Checks: file exists and is non-empty, >1 page, text is extractable (not image-only), page size is US Letter (612 x 792 pt), all declared figures are embedded, no blank pages.

**Success looks like:** the validator exits 0 with "PASS: All PDF validation checks passed".

### Step 4 — Copy to results root

Copy the validated PDF from the workspace to the results root:

```python
from report_qc import staged_copy
staged_copy(workspace_report_file, "report_phylo_pdf_report_generation.pdf")
```

### Step 5 — Final report (MANDATORY TERMINAL STEP)
**The run is not complete until this step has produced `report_phylo_pdf_report_generation.pdf` at the results root.**
Generate the PDF report with `pdf-report-generation` by default. When the user explicitly selects a compatible report-styling skill, use that provider instead for presentation only; keep every report, evidence, artifact, infographic, and review requirement unchanged. Include a Biomni GenerateImage infographic when required, task context, methods or sources, results, conclusions, figures where applicable, references, and next steps

Produce one combined PDF with a short narrative. rendered page images are inspection evidence only, never substitutes for the PDF. End the task with a concise conclusion and links to the PDF and supporting artifacts, not a bare file listing.

The PDF must use these visible top-level sections in this order, adapting their contents to this skill rather than adding generic filler:

1. `Task Context` — the research or practitioner question, supplied inputs, scope, and decision the output informs.
2. `Methods & Sources` — Methods & Sources covers the input contract, transformation, and validation method; Results presents the transformed artifact and integrity checks.
3. `Results` — the run's actual outputs and evidence, never a description of what the skill could do.
4. `Conclusions & Interpretation` — supported takeaways, their practical meaning, and appropriate next steps.
5. `Limitations` — run-specific uncertainty, missing coverage, failed or unavailable checks, and claims the evidence does not support.

Include references and a compact output-artifact table where applicable. Empty boilerplate does not satisfy a section.

An infographic is not applicable: Format utility renders provided content; does not generate qualitative infographics. Do not call `GenerateImage` for decorative compliance.

Render the PDF to a fresh workspace file named by `workspace_report_file`; do not open or truncate an existing PDF on the object-backed results mount. The `staged_copy` call below publishes the completed file under its declared results-root name.

Then verify the run and write its receipt with a single call. `write_receipt` runs every gate — the report exists at the results root and is big enough, each declared figure is present and non-blank, the exact infographic came from a same-ID `GenerateImage` call and result and is the first embedded image on page 1, and the finished PDF carries the markers declared by the resolved report-style provider — then records what each one returned. Load and follow the selected provider skill's complete report instructions and assets. QC prefers a provider-owned `report_style.json` under that provider's assets; when an existing installed provider has none, it derives only its declared aliases and PDF marker colors from that provider's immutable `SKILL.md`. Neither source is a theme recipe. Never stage, synthesize, or copy provider evidence into a workspace or results directory. A missing or ambiguous installed provider source is a blocker, not permission to reconstruct one. Run bundled commands through `run_bundled`, which writes the QC-owned `qc_run_log.json` from the subprocess result and output hashes. Do not author execution events or copy transcript identifiers. Record PDF visual review as an explicit attestation; it is not described as independently verified. Produce every source-witness artifact declared in `skill_contract.json`. The receipt writer rejects unmatched hashes, partial page coverage, a visual-review verdict other than pass, any unresolved visual-review issue, or source-value disagreement. Fix and rerender every failed page or figure; listing a visual defect under Limitations does not make it pass. It raises if any gate failed, **after** writing the receipt, so a failed run leaves the diagnostic behind.

Record each answer from `## Clarification Questions` in `selected_branch_ids` using the displayed `<question_id>:<choice_id>` value. The receipt derives its required outputs only from those selected branches; do not union mutually exclusive branch artifacts. The receipt derives any explicit report-style provider from immutable user messages and otherwise resolves the default from `skill_contract.json`; a caller variable cannot authorize an override. Never infer styling from the enterprise, account, project, or customer context. The absence of an affirmative styling directive is not ambiguity: use the contract default without asking a styling clarification. Ask only when user messages contain conflicting affirmative selections or request a provider that cannot be resolved safely. The fenced call below is the stable public API; execute it before reading `report_qc.py`, and inspect helper internals only if a `GateFailure` is not specific enough to act on.

```python
from report_qc import (outputs_for_selected_branches, record_pdf_review, staged_copy,
                       write_receipt)
selected_outputs = outputs_for_selected_branches(selected_branch_ids)
staged_copy(workspace_report_file, "report_phylo_pdf_report_generation.pdf")
record_pdf_review(
    report_name="report_phylo_pdf_report_generation.pdf", text_artifact=extracted_text_file,
    rendered_page_files=rendered_page_files,
    reviewed_page_numbers=reviewed_page_numbers,
    review_attestation=visual_review_notes,
    review_verdict=visual_review_verdict,
    review_issues=visual_review_issues,
)
write_receipt(
    report_name="report_phylo_pdf_report_generation.pdf",
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

Nothing existed before this skill. Both `scripts/generate_report.py` (the ReportLab Platypus renderer) and `scripts/validate_pdf.py` (the post-generation integrity checker) were authored fresh. The `report_qc.py` and `report_style.py` modules were scaffolded by `phylo-create-skill` and provide the receipt and style-verification infrastructure.

## Scientific caveats

This skill is a pure format utility — it renders provided content into a PDF. It does not generate evidence-bearing claims, compute statistics, or assert findings. The responsibility for scientific accuracy, evidence tier language, and data integrity lies with the calling skill or user that produced the content, not with this formatter.

| Caveat | What makes it concrete | Artifact field |
|---|---|---|
| Figure path not found | The renderer prints a placeholder paragraph instead of silently skipping | `validate_pdf.py` exit code |
| Text not extractable | The validator checks pypdf text extraction length > 50 chars | `validate_pdf.py` exit code |
| Wrong page size | The validator checks each page mediabox against 612 x 792 pt | `validate_pdf.py` exit code |

## Evidence Tier

Contract maturity: `generated`. Do not claim a higher tier than the validation matrix supports.

This skill does not compute an evidence tier. It is a format utility that renders provided content. The evidence tier of the rendered report is determined by the calling skill that produced the content.

## Data Sources & Licenses

- Commercial use: not applicable because no external data source or package dependency is used; see the machine-readable applicability decisions in `skill_contract.json`.
- ReportLab: BSD license, commercially usable, standard Python package.
- pypdf: BSD license, used for PDF validation.
- Liberation Sans / Liberation Mono fonts: SIL Open Font License, metric-equivalent to Arial and Courier.

## Resource Identity

Not applicable: this skill does not emit citations, accessions, or external identifiers. It renders provided content.

## Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| PDF has only 1 page | Content too short to spill past the title block | Ensure sections have enough content; a multi-section report should exceed one page |
| Figure shows as "[Figure not found]" | Image path is wrong or file does not exist | Check that figure paths are relative to the JSON file directory or use absolute paths |
| Text appears as black boxes | Unicode superscript/subscript characters used in text | Use ReportLab `<super>` and `<sub>` markup instead of Unicode characters |
| Table columns too narrow | Too many columns for the page width | Reduce the number of columns or shorten cell content; the renderer divides content width equally |
| PDF page size is A4 not Letter | ReportLab default page size used | The renderer explicitly sets `pagesize=letter`; if this occurs, check that the template was not modified |

## Suggested Next Steps

- Use `scientific-report-writing-standards` to learn the writing standards that produce high-quality content for this renderer.
- Use analysis skills (e.g., `bulk-rnaseq-counts-to-de-deseq2`, `survival-analysis-clinical`) to produce the results that this renderer assembles into a report.

## Related Skills

- `scientific-report-writing-standards` — provides the writing standards that govern report content
- `pdf-report-generation` — the platform's default report styling provider
- `docx-generation` — for Word document output instead of PDF
- `pptx-generation` — for PowerPoint presentation output
