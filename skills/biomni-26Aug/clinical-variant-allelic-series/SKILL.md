---
id: "skill_71ac25ccb32e00a971a5e49bcae1bfe2"
name: clinical-variant-allelic-series
description: "Use to build a gene-level clinical allelic series or per-variant/per-residue actionability map. Integrates ClinVar and CIViC to answer which mutations are pathogenic, actionable, or targetable and produces mutation landscapes, lollipop plots, evidence tiers, and therapy matrices."
category: "genomics_genetics"
visibility: "public"
starting-prompt: "Build a clinical allelic series for my gene from ClinVar + CIViC: tier each variant by actionability with a lollipop plot and therapy matrix."
---

# Clinical Variant Allelic Series

Turn a single human gene symbol into an integrated, position-resolved catalogue of every
clinically observed variant, annotated with germline/somatic significance and curated
therapy evidence, tiered by actionability, and packaged as figures + a PDF report.

This skill was validated end-to-end on **BRAF** (rich actionability, single hotspot
cluster), **KIT** (two separated hotspots, multi-domain receptor), and **STK11**
(sparse tumor suppressor), plus a zero-CIViC degradation test.

---

## Scope

**Does:** For one human gene, fetch the full ClinVar record set and CIViC curated evidence,
reconcile them into one allele table keyed by residue position, assign an actionability
tier + mechanism to each allele, generate adaptive figures, and build a config-driven PDF.

**Does NOT:** call variants from sequencing data; interpret a patient's specific genotype;
replace guideline-based clinical interpretation (AMP/ASCO/CAP, ACMG); compute functional
class de novo; or cover non-human genes (identifier resolution is human-only, `organism_id:9606`).

---

## Inputs

- **Required:** one HGNC gene symbol (e.g. `EGFR`, `BRAF`, `KIT`). That's it.
- **Optional:** NCBI email + API key (raises E-utilities rate limit from 3 to 10 req/s;
  pass `--email`/`--api-key` or set `NCBI_EMAIL`/`NCBI_API_KEY`); a UniProt accession
  override for `make_figures.py` (`--uniprot`, useful when a symbol is ambiguous).
- **For the report:** a `report_config.json` that the agent assembles (see Step 5). The
  agent writes the narrative and grounds key claims with `LiteratureSearch`.

## Outputs (written to `--outdir`)

| File | Content |
|---|---|
| `<GENE>_clinvar_full_catalog.csv` | Every ClinVar record: variation ID, protein change, molecular consequence, classifications, conditions |
| `<GENE>_clinvar_summary.json` | Counts by classification/consequence |
| `<GENE>_civic_variants.csv` | CIViC variants for the gene (with `clinvar_ids` bridge) |
| `<GENE>_civic_evidence_long.csv` | One row per CIViC evidence item (single-variant flagged) |
| `<GENE>_allelic_series_master.csv` | **The core deliverable** — one row per allele, joined, positioned, tiered |
| `<GENE>_summary_stats.json` | Allele counts, tier counts, join-method breakdown, position-discrepancy check |
| `figures/F1_lollipop.{png,svg}` | Protein-domain lollipop (adaptive single- or two-panel) |
| `figures/F2_landscape.{png,svg}` | Actionability landscape (tiers × mechanism) |
| `figures/F3_evidence.{png,svg}` | CIViC evidence distribution — **skipped if no CIViC evidence** |
| `figures/F4_therapy_matrix.{png,svg}` | Allele × therapy matrix — **skipped if no predictive evidence** |
| `figures/figures_manifest.json` | Which figures were produced + UniProt metadata |
| `report_<GENE>_allelic_series.pdf` | Phylo-styled PDF (validated: >=3 pages, extractable text) |

---

## Quick start

Run the five scripts in order (all live in `scripts/`). Use a persistent `--outdir`
(prefer `/mnt/shared-workspace/<run>` for intermediates; copy final deliverables to
`/mnt/results/`). ClinVar fetch time scales with record count (~300 records/batch,
~0.3 s/batch without an API key): EGFR ~4k, BRAF ~1.6k, KIT/STK11 ~3k records.

```bash
GENE=BRAF
OUT=/mnt/shared-workspace/allelic_${GENE}
python scripts/fetch_clinvar.py       --gene $GENE --outdir $OUT
python scripts/fetch_civic.py         --gene $GENE --outdir $OUT
python scripts/build_allelic_series.py --gene $GENE --outdir $OUT
python scripts/make_figures.py        --gene $GENE --outdir $OUT
# assemble $OUT/report_config.json (see Step 5), then:
python scripts/build_report.py        --gene $GENE --outdir $OUT --config $OUT/report_config.json
```

Before starting, verify the documented variant-annotation helpers by importing them,
and use `LiteratureSearch` to ground the gene's biology (functional classes, key
alleles, targeted therapies) for the report narrative.

---

## Workflow steps (and why each matters)

1. **Fetch ClinVar (`fetch_clinvar.py`).** `esearch` on `db=clinvar` with `term=<GENE>[gene]`
   and `retmax=100000` to get all UIDs, then batched `esummary` (XML v2.0). Captures the
   **ClinVar Variation ID = the `uid`** (this is the join key to CIViC), protein change,
   molecular consequence, and the *new* ClinVar classification schema
   (`germline_classification`, `oncogenicity_classification`, `clinical_impact_classification`).
   *Why:* ClinVar is the comprehensive record of observed variants and their asserted
   significance; the new tripartite schema separates germline pathogenicity from somatic
   oncogenicity, which matters for correct mechanism labels.

2. **Fetch CIViC (`fetch_civic.py`).** Downloads the three nightly TSV exports (Variant,
   MolecularProfile, ClinicalEvidence; cached 24 h) and joins Variant→MolecularProfile→Evidence.
   Keeps **single-variant molecular profiles** as the primary evidence set; multi-variant
   (complex) profiles are tabulated separately. *Why:* CIViC is the expert-curated layer of
   *clinical* meaning (therapy response, prognosis, diagnosis) that ClinVar lacks; single-variant
   profiles are the ones that map cleanly onto individual alleles.

3. **Build the allelic series (`build_allelic_series.py`).** Reconciles ClinVar and CIViC by
   four keys (Variation ID; normalized protein change; both; categorical descriptors like
   "exon 19 deletion"), derives residue positions, assigns tiers + mechanism, and **asserts
   zero position discrepancies**. *Why:* this is where the two sources become one queryable
   table; the position-consistency assertion is the main correctness guardrail.

4. **Make figures (`make_figures.py`).** Resolves the gene to its reviewed human UniProt entry
   (accession, length, domains), then renders adaptive figures. The lollipop auto-detects
   whether notable alleles are concentrated (→ two-panel overview+zoom) or spread (→ single
   panel), and F3/F4 self-skip when the underlying evidence is absent. *Why:* the domain
   context and hotspot structure are the biological story; adaptivity keeps it readable for
   both hotspot-driven oncogenes and diffusely-mutated tumor suppressors.

5. **Assemble config + build report (`build_report.py`).** The agent writes
   `report_config.json` — title, executive summary, introduction, methods, `results` blocks
   (each optionally referencing a figure by filename), a `key_allele_table`, conclusions,
   `limitations`, `next_steps`, and `references`. Ground the narrative and the references with
   `LiteratureSearch` (do **not** invent citations). Optionally generate a front-page
   infographic with `GenerateImage`. `build_report.py` renders a Phylo-styled PDF and
   self-validates. *Why:* the report is the human-readable deliverable; grounding + validation
   keep it trustworthy. This step leverages the **pdf-report-generation** skill's patterns.

---

## Database reference

| Source | Endpoint | Key identifiers | Notes |
|---|---|---|---|
| ClinVar | NCBI E-utilities (`esearch`/`esummary`, `db=clinvar`) | Variation ID (`uid`), accession `VCV…` | `retmax=100000`; batch esummary 300; rate limit 3/s (10/s with API key) |
| CIViC | Nightly TSV exports (`civicdb.org/downloads/nightly/…`) | `variant_id`, `molecular_profile_id`, `clinvar_ids` | `clinvar_ids` = ClinVar Variation IDs = the bridge to ClinVar `uid` |
| UniProt | REST (`/uniprotkb/search`, `/uniprotkb/{acc}.json`) | primary accession | human-only (`organism_id:9606`, `reviewed:true`); domains for the lollipop track |

---

## Scientific caveats & hard-won lessons

These are baked into the scripts; do not "simplify" them away.

- **Residue position comes from the variant NAME, never a linked record.** `pos_from_name`
  = first residue number after a letter in the HGVS protein change (start codon for both
  substitutions and range indels). Taking position from a linked ClinVar record produces
  silent mismatches; the build asserts **0 discrepancies**.
- **ClinVar Variation ID = the `uid`** (numeric part of `VCV000666267` → `666267`), *not*
  the `measure_id`. Using `measure_id` breaks the CIViC join.
- **`molecular_consequence`** lives at the DocumentSummary level (`molecular_consequence_list/string`),
  not inside `variation_set`.
- **Top-level `<protein_change>` is comma-separated across transcripts** — take the first token.
- **CSV round-trip pitfalls (both are real bugs that silently corrupt tiers):**
  - Booleans become the *strings* `"True"`/`"False"` when read with `dtype=str`. Compare with
    the `_as_bool()` helper, never `== True`.
  - `NaN` becomes the *truthy string* `"nan"`. Use the `_s()` safe-string helper in any
    truthiness test (tiering, mechanism inference), or everything collapses into Tier 1.
- **Evidence absence ≠ benign.** CIViC coverage is deepest for recurrent hotspots; rare
  alleles are under-annotated. Tier 3 means "not evidenced here", not "safe".
- **Actionability is often lineage-dependent** (e.g. BRAF V600E responds differently in
  melanoma vs. colorectal cancer). The series is tumor-type-agnostic; say so in the report.
- **Functional classes are literature-annotated, not computed.** If you cite BRAF Class I/II/III
  or similar, ground it with `LiteratureSearch`.
- **Lollipop label density:** notable alleles are capped (~22) and labels are thinned by a
  minimum gap and placed with a deterministic **fan-out** (evenly spaced label slots + leader
  lines) so hotspot clusters stay readable. `adjustText` (if used elsewhere) is v1.3.0 —
  use `iter_lim`/`time_lim`, not the old `lim`, and suppress its verbose stdout.
- **UniProt domains:** prefer structural `Domain`/`Transmembrane` features over broad
  `Region`/`Topological domain` spans (the latter overlap and clutter the track); labels are
  collision-filtered.
- **PDF text must be ASCII.** `build_report.py` runs `ascii_clean()`; never emit unicode
  sub/superscripts or smart quotes into the PDF.

## Error handling / graceful degradation

- Gene absent from CIViC → empty CIViC files → F3/F4 skip, build + report still succeed
  (Tier 1 will be 0). Verified.
- UniProt resolution fails → figures fall back (no domain track / length inferred from data);
  pass `--uniprot` to override.
- E-utilities transient failures → esummary retries 3× with backoff.
- Always keep intermediates in a persistent path (`/mnt/shared-workspace/…`); sandbox
  `/workspace` is per-machine and is lost on machine restart.

## Related skills / tools

- **pdf-report-generation** — the PDF styling/validation patterns `build_report.py` follows.
- **LiteratureSearch** — ground the report narrative and populate real references.
- **Python imports** — verify documented in-environment variant-annotation helpers before use.
- **GenerateImage** — optional front-page infographic (verify text with a media check).
