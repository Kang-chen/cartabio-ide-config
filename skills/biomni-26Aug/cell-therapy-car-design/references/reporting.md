# Reporting: Phylo-branded PDF with a summary infographic

The **final, required deliverable** of this skill is a single Phylo-branded PDF
that presents both parts (CAR design + CRISPR screen) with a summary infographic.
Do not finish the task with only tables/figures scattered in folders.

## How to build it
1. **Load the `pdf-report-generation` skill** (`Skill` -> load) and follow its
   ReportLab/Platypus conventions. That skill is the source of truth for styling
   and building blocks; this file only specifies the *structure and content* for
   this particular report and how to embed the infographic.
2. Build the story with Platypus (`SimpleDocTemplate` + flowables), then validate.

## Phylo styling (must match the pdf-report-generation skill and the infographic)
- Colors: `PHYLO_GOLD=#D4A04A` (primary accent), `HEADING=#111111`,
  `BODY=#2C2A26`, `MUTED=#8A8378`, `TABLE_HEADER_BG=#D4A04A`,
  `TABLE_ALT_ROW=#F9F7F3`, `TABLE_BORDER=#D5CFC5`.
- Fonts: **Helvetica** family for text; **Courier** for gene names, sequences,
  accessions, and other monospace tokens.
- Subscripts/superscripts via ReportLab `<sub>`/`<super>` markup, **not** Unicode
  (e.g., CD3<sub>zeta</sub>, log<sub>2</sub>FC).
- Every `Image`, `Drawing`, and `Table` gets `hAlign="CENTER"`; wrap each figure
  with its caption in `KeepTogether` so they never split across pages.
- Give every `Table` explicit `colWidths`.

## Required report structure (in order)
1. **Title block** — title, subtitle, date, "Prepared by Phylo".
2. **Summary infographic** (page 1, right after the title) — see below. This is
   the visual TL;DR the user asked for.
3. **Introduction / Background** — the biological problem: CD19 CAR-T therapy, why
   an scFv-based CAR, and why a pooled CRISPR knockout screen is used to find genes
   that regulate T-cell proliferation/persistence. Cite background with
   `LiteratureSearch` results (inline `[N]`).
4. **Methods** — reproducible, tool-attributed:
   - CAR design: scFv/domain provenance (RCSB PDB, UniProt, Addgene via
     `search_plasmids`/`get_plasmid_with_sequences`), codon optimization, cassette
     assembly. Name the exact Biomni tools used.
   - Screen: dataset accession + retrieval (`GEOparse`/SRA), library reconstruction
     approach if reads were used, MAGeCK `count` + `test` parameters, QC thresholds.
   - Hit validation: DepMap cross-check (`CRISPRGeneEffect.csv`, Chronos, negative =
     essential) and any alternative the user chose.
5. **Results**
   - Part 1 (CAR design): architecture figure, domain boundary table(s), ORF/GC
     stats, GenBank deliverables listed.
   - Part 2 (screen): QC figure(s), normalization/reproducibility, gene-selection
     (volcano/rank), top-hits figure, positive- and negative-regulator hit tables
     with exact scores/FDR, and the **DepMap cross-check table**.
6. **Conclusions** — the 3-5 headline takeaways, phrased for a decision-maker
   (e.g., which brakes are the most credible KO candidates and why; which hits are
   context-specific vs broadly essential).
7. **Figures** — all referenced figures embedded at legible size (preserve aspect
   ratio; derive height from the source image dimensions).
8. **References** — inline `[N]` throughout; the full list is handled by the UI. Do
   **not** hand-write a bibliography section. Sources come from `LiteratureSearch`.
9. **Next steps** — concrete follow-ups: wet-lab validation path (arrayed KO of top
   hits; `compare_knockout_cas_systems`, `get_lentivirus_production_protocol`,
   `get_facs_sorting_protocol`), additional screens, orthogonal validation,
   combination KOs, etc.
10. **Limitations** — pilot-scale screen caveats, single-timepoint, FDR limits, and
    the mandatory **"DepMap = cancer lines, not primary T cells"** caveat.

## The summary infographic
- Use `scripts/build_infographic.py`, which renders a **data-driven** ReportLab
  `Drawing` (metric callout cards + two diverging hit-ranking bar charts). It only
  visualizes **numbers you already computed and verified** — it does not re-derive
  science.
- **This is a data plot, not a schematic.** Do NOT use `GenerateImage` for it.
  (Use `GenerateImage` only for a separate conceptual MOA/CAR-mechanism cartoon if
  you want one; that is optional and belongs in the intro/background, not here.)
- Feed it a small JSON of verified metrics:
  ```json
  {
    "title": "...",
    "kpis": [{"label":"sgRNAs mapped","value":"~76%"}, ...5 cards...],
    "pos_hits": [["CBLB",0.545], ...top brakes, LFC...],
    "neg_hits": [["CD3D",-0.597], ...top essential, LFC...],
    "footer": "Positive = KO enhances proliferation; Negative = KO impairs it (log2FC)."
  }
  ```
- Suggested KPI cards: sgRNA mapping %, Gini index, # genes tested, top brake
  (KO enhances proliferation), top essential (KO impairs proliferation). Pull every
  value from the MAGeCK `countsummary`/`gene_summary` — do not eyeball them.
- Embed by importing `make_infographic_drawing(metrics)` and adding the returned
  `Drawing` (`hAlign="CENTER"`) to the story, OR render to PNG/PDF and place as an
  `Image`. Standalone `.pdf` output is handy for QC.
- Note: `renderPM` PNG export needs the optional `rlPyCairo` backend; the helper
  falls back to PyMuPDF rasterization if it is missing. Embedding the `Drawing`
  directly never needs a raster backend. **Commercial-use caveat**: PyMuPDF is
  AGPL-3.0 (copyleft); the PNG fallback may require an Artifex commercial license
  for commercial redistribution. Prefer embedding the `Drawing` directly or using
  the BSD-licensed `rlPyCairo` backend when available. `needs_commercial_review`
  before using the PyMuPDF fallback in a commercial pipeline.

## NaN / null safety in ReportLab flowables
- DataFrame columns read from MAGeCK or CSV outputs may contain `NaN`, `None`, or
  empty strings. ReportLab `Paragraph()` and `String()` crash or render literal
  `nan` text when given these values directly.
- **Before passing any DataFrame-derived value to `Paragraph()` or `String()`**,
  coerce it to a safe string. Use a helper like:
  ```python
  def _safe_str(val):
      if val is None:
          return ""
      import math
      if isinstance(val, float) and math.isnan(val):
          return ""
      return str(val)
  ```
- Apply this to every cell value in table data, KPI card values/labels, and hit
  bar labels. Do not rely on pandas' default string conversion.

## Hit-table sorting (critical — do not get this wrong)
- MAGeCK `gene_summary.txt` provides **two independent rank columns**: `pos|rank`
  (positive selection / brakes) and `neg|rank` (negative selection / essential).
  **Rank 1 = best hit.** Ranks are ascending: 1, 2, 3, …, N.
- **Top brakes** (positive selection): sort by `pos|rank` **ascending** and take
  `head(10)`. Do NOT use `nlargest(10, "pos|score")` — that selects the
  *highest-scoring* genes, which for positive selection are the **worst** brakes
  (bottom-ranked, rank ~1200+). The correct call is:
  ```python
  top_pos = gs.sort_values("pos|rank").head(10)
  ```
- **Top essential** (negative selection): sort by `neg|rank` **ascending** and
  take `head(10)`:
  ```python
  top_neg = gs.sort_values("neg|rank").head(10)
  ```
- **Before finishing, verify that each hit table in the PDF matches the
  corresponding CSV file.** The top brakes table (Table 4) must show genes with
  positive LFC and `pos|rank` 1–10; the top essential table (Table 5) must show
  genes with negative LFC and `neg|rank` 1–10. If the PDF table shows genes with
  rank ~1200 or wrong-sign LFC, the sort direction is reversed — fix it before
  saving.

## Validation (do this before declaring done)
- Open the PDF with `pypdf`, assert page count and that text is extractable on
  each page (guards against blank pages).
- **Cross-check hit tables**: read `top_positive_hits.csv` and
  `top_negative_hits.csv` back from disk and confirm the first row has
  `pos|rank`/`neg|rank` == 1 (not ~1200). If the rank is wrong, the sort is
  reversed — re-sort ascending and regenerate.
- Run `Read` in `media_output_check` mode on the infographic page and each figure
  page. If anything is blank, clipped, overlapping, or low-contrast, fix and
  re-check. (The infographic's bar value labels are a known trouble spot — use the
  chart's built-in bar labels, not manual text placement.)
- Save the final PDF to `/mnt/results/report_<task>.pdf` and name it in the final
  message.

## Commercial-use disclosures (for the report's Limitations or Methods section)
- **DepMap** (`CRISPRGeneEffect.csv`): DepMap data is provided under Broad
  Institute research-use terms and **may restrict commercial use**. The skill
  accesses it via the Biomni data lake. `needs_commercial_review`: verify DepMap
  terms (https://depmap.org/portal/terms/) before commercial application.
- **Addgene** (plasmids, Brunello library): Addgene plasmids are distributed under
  Material Transfer Agreement (MTA) terms that **may restrict commercial use**.
  The guide-sequence table is publicly accessible, but plasmid acquisition and use
  require MTA compliance. `needs_commercial_review`.
- **PyMuPDF** (fitz): AGPL-3.0 copyleft — see the note above on the PNG fallback.
  The primary PDF path uses ReportLab (BSD) and has no such restriction.
