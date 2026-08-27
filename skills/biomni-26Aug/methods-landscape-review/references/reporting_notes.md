# Reporting Notes

How this skill turns verified artifacts into the final PDF, and how the one-page infographic
is generated. Final rendering is delegated to the **`pdf-report-generation`** skill — do not
re-implement PDF chrome (Phylo palette, header/footer, table styles) here; load that skill and
reuse its building blocks.

---

## Report section order (both modes)

1. **Title page** — title, subtitle (the task / methods compared), attribution, date.
2. **Infographic** — one full-width conceptual summary image (see below).
3. **Executive summary** — 2–3 paragraphs: what was compared/asked, the headline finding, the
   practical implication. State up front that there is no unconditional winner (comparison
   mode) and that recommendations are conditional.
4. **Introduction** — why the task matters and what distinguishes the contenders / frames the
   topic.
5. **Methods** — retrieval strategy (LiteratureSearch-first, multi-query), screening, full-text
   depth, and the **citation-verification protocol** with the actual `doi_layer_status`.
6. **Results** —
   - *Comparison mode*: landscape figure(s), the **comparison matrix** (Table), the
     **benchmark catalog** (Table), the **performance-claims** table (evidence-thickness
     tagged), and any quantitative head-to-head figure.
   - *Topic mode*: narrative synthesis with inline citations + the **evidence table**.
7. **Discussion / Decision guidance** —
   - *Comparison mode*: **regime-conditional** recommendations (by sample size, data quality,
     design). Present genuine disagreements as disagreements, with both sides cited.
   - *Topic mode*: what is established, what is contested, what is open.
8. **Limitations** — literature-synthesis caveats; benchmark caveats (self-referential gold
   standards, simulation/permutation assumptions, preprocessing sensitivity); scorecard is
   qualitative.
9. **Next steps** — concrete follow-ups, including *which Biomni skill/tool actually runs the
   recommended method* (from Step 7 inventory).
10. **Relevant Biomni resources** — the mapping from Step 7 (databases, data-lake datasets,
    packages, HPC tools, sibling skills). This section must **surface commercial-use licensing**
    per the policy in `references/biomni_resources_catalog.md` (§0): **omit non-commercial
    `[NC]` resources** and state in one line that they were excluded for commercial-licensing
    reasons; attach an explicit **ShareAlike/attribution caveat to any `[SA]` resource** that is
    recommended. If no restricted resource applies, add a brief line noting the resources were
    screened for commercial-use licensing.
11. **References** — verbatim, verified. Inline `[N]` citations in the body map to this list.

## How `build_report.py` consumes your narrative (`synthesis.json`)

`scripts/build_report.py` is a **layout engine**, not a writer. It reads two agent-authored
files from the run dir and lays them out with the `pdf-report-generation` building blocks. You
author these only **after** the Step 5 citation gate returns `clean` — every sentence must be
source-bound and verified.

`synthesis.json` (all fields optional except `title`; rich text uses ReportLab XML tags
`<b> <i> <super> <sub>` — never markdown or Unicode sub/superscripts):

```json
{
  "title": "ToolA vs ToolB for <task>",
  "subtitle": "Literature & benchmark synthesis",
  "mode": "comparison",            // or "topic" — controls which tables render
  "header_short": "short running header",
  "infographic": "infographic.png",         // optional; or pass --infographic
  "infographic_caption": "…",
  "executive_summary": ["para 1", "para 2"],
  "methods": ["para 1", "…"],
  "results_intro": ["para"],
  "results_sections": [
    {"heading": "…", "paragraphs": ["…"],
     "figure": "fig_performance_scorecard.png",   // embedded if present in run dir
     "figure_caption": "…",                        // else taken from fig_manifest.csv
     "callout": {"title": "…", "body": "…", "accent": "gold"}}   // accent: gold|orange
  ],
  "discussion": ["para", "…"],
  "limitations": ["para"],
  "next_steps": ["bullet", "…"],
  "callouts": [ {"where": "executive_summary"|"discussion",
                 "title": "…", "body": "…", "accent": "orange"} ]
}
```

`references.json`: an ordered list, either `[{"n": 1, "text": "…verbatim citation…"}, …]` or a
plain `["…", …]`. Text is copied verbatim from verified records; inline `[N]` markers in the
narrative map to these numbers.

The builder auto-appends: the comparison-mode tables (`comparison_matrix.csv`,
`benchmark_catalog.json`, `performance_claims.json`) or the topic-mode `theme_table.csv`; any
figures in `fig_manifest.csv` not already placed inline; and a provenance line from
`citation_verification.json`. It embeds **only files that exist**, so the same call works in
both modes. After building, it runs a `pypdf` structural check; you still must run a `Read`
`media_output_check` on the PDF.

## Mapping to `pdf-report-generation` building blocks

- Use its `SimpleDocTemplate` + canvas header/footer callback, Phylo palette constants, and
  the styled-paragraph / table / callout / divider helpers.
- Tables: set explicit `colWidths`, `hAlign="CENTER"`, `repeatRows=1` for long tables.
- Figures: embed with `Image(...)`, `hAlign="CENTER"`, and bind each figure to its caption
  with `KeepTogether([...])` to avoid orphaned captions.
- Use `<sub>`/`<super>` tags, never Unicode sub/superscripts.
- Write the PDF directly to `/mnt/results/report_<slug>.pdf` (ReportLab writes sequentially).
- **Validate**: `pypdf` page-count + extractable-text check, then a `Read`
  `mode="media_output_check"` pass over the final PDF; fix and re-check on failure.

---

## Infographic (built with the `GenerateImage` tool, NOT plotting code)

The infographic is a **conceptual/schematic** graphic — the exact case where `GenerateImage`
is required and hand-drawn matplotlib/ggplot is the wrong tool. Generate a single landscape
image summarizing the whole analysis, then embed it as the second page of the PDF.

Prompt template (fill the braces from the verified artifacts):

> "A clean, professional scientific infographic titled '{task} — method selection at a
> glance'. Flat modern vector style, generous whitespace, colorblind-safe palette (blues,
> teal, warm gray, one gold accent), sans-serif labels. Show {N} labeled option cards for
> {method_1}, {method_2}, {method_3}, each with 2–3 short bullet tradeoffs. Include a compact
> 'decision guide' band at the bottom that maps conditions (e.g. {condition_axis}) to the
> recommended option. Include small iconography for {domain} (e.g. DNA/RNA, bars, a
> chec/cross). No dense paragraphs, no fake numbers, labels only. High resolution, 16:9."

Rules for the infographic:
- **Labels and structure only — no fabricated numeric values.** Any number shown must be a
  verified value from `performance_claims` / `evidence_table`.
- Keep it legible: ≤ ~6 cards/sections, short phrases.
- After generation, run a `Read` `media_output_check` on the image; regenerate if the layout
  is crowded, text is garbled, or it invents numbers.
- For **topic mode**, adapt: replace option cards with 3–5 theme tiles and a "what's
  established vs. contested vs. open" band.

If `GenerateImage` is unavailable in the session, fall back to a composed multi-panel data
figure as the summary and note the substitution — but the default is `GenerateImage`.
