---
id: "skill_1770b7f9d13954ae16b07b3ec91515bf"
name: "tissue-expression-specificity"
description: "Use to determine where a human target is expressed and assess expression-based on-target safety. Integrates GTEx and Human Protein Atlas tissue profiles, ranks high-baseline organs, computes tissue-specificity/tau and cross-atlas concordance, and flags vital-organ liabilities; accepts gene symbols, Ensembl IDs, or UniProt accessions."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Assess the tissue-expression specificity and on-target safety risk for GENE across GTEx and the Human Protein Atlas, and generate a PDF report."
---

# Target Tissue-Expression Specificity & On-Target Safety Assessment

## Scope

**Does:** For one human protein-coding gene, profile where it is expressed across human
tissues using two independent atlases (**GTEx** bulk RNA-seq + **Human Protein Atlas**
consensus tissue), compute a **tau (τ) tissue-specificity score** per atlas, **flag
high-baseline tissues**, test **cross-atlas concordance**, and synthesize **on-target
safety risk** over a vital-organ + data-driven organ panel with **grounded literature
context**. Deliverable = a **Phylo-branded PDF report** (infographic + intro + methods +
results + figures + references + next steps) plus supporting CSV tables.

**Does NOT:** cell-type / single-cell resolution, protein-level (antibody) profiling,
genetic constraint / essentiality, multi-target batch comparison, or non-human organisms
(GTEx and HPA are human). These are noted as extensions — see "Next steps".

This is the on-target-*expression* liability lens. For complementary target views, see
`open-targets` (associations/tractability), `gene-essentiality` (DepMap), and
`literature-preclinical` (deep evidence synthesis).

## Inputs

- **One target identifier** — gene symbol (`GCGR`), Ensembl gene ID (`ENSG00000215644`,
  versioned or not), or UniProt accession (`P47871`). Resolved automatically.
- Human only. One target per run.

## Outputs (under `/mnt/results/`)

- `report_<GENE>_tissue_safety.pdf` — main deliverable.
- `figures/` — infographic + 4 data figures (PNG + SVG).
- `tables/` — `<gene>_gtex_per_tissue.csv`, `<gene>_hpa_per_tissue.csv`,
  `<gene>_tau_scores.csv`, `<gene>_high_baseline_flags.csv`,
  `<gene>_organ_concordance.csv`, `<gene>_safety_organ_matrix.csv`.

## Bundled files

- `scripts/tissue_expression.py` — analysis engine: `resolve_gene`, `get_gtex`
  (datalake-first, API fallback), `get_hpa`, `compute_tau`, `flag_high_baseline`,
  `organ_concordance`, `build_safety_matrix`, and `run_analysis(gene, outdir)` orchestrator.
- `scripts/make_figures.py` — the 4 data figures (Phylo palette); `make_all_figures(res, figdir)`.
- `references/methods.md` — exact τ / threshold / concordance / safety / literature recipes + assumptions.
- `references/data_sources.md` — GTEx & HPA access, identifier resolution, resource table,
  and full **data-source license & attribution** details.

**Read `references/methods.md` and `references/data_sources.md` before running** — they hold
the scientific rationale and the parsing/threshold details you must state in the report.

## Data sources & licenses (attribution required)

This skill uses two external human atlases plus identifier-resolution lookups. Their
licenses **permit commercial use but carry attribution (and, for CC BY-SA, share-alike)
obligations** — honor them in any report or redistribution. Full details, the resource
table, and an attribution snippet are in `references/data_sources.md`.

| Source | Used for | License | Commercial? | Key obligation |
|---|---|---|---|---|
| **Human Protein Atlas** | Per-tissue nTPM + native specificity call | **CC BY-SA 3.0** (the `source="HPA"` XML this skill parses; current site also lists CC BY 4.0 for the DB as a whole) | **Yes** | **Attribution + ShareAlike** — cite an HPA primary publication + link proteinatlas.org; redistributed HPA-derived content stays under CC BY-SA |
| **GTEx** (open-access summary median-TPM / v8 Portal API) | Per-tissue bulk median TPM | **Open-access, NIH Genomic Data Sharing policy** (no use/publication restrictions post-release; not CC BY-SA) | **Yes** | **Attribution** — cite GTEx Portal (date) + dbGaP `phs000424`; only *open-access* summary data is used (protected raw data is not) |
| **Ensembl / UniProt / NCBI**, `gget` | Gene-ID resolution only | Ensembl (EMBL-EBI terms); UniProt **CC BY 4.0**; NCBI public-domain | Yes | Attribution per resource |

- **Human Protein Atlas (CC BY-SA):** commercial use is allowed **with attribution + share-alike**.
- **ChEMBL (CC BY-SA 3.0):** likewise permits commercial use **with attribution + share-alike** —
  documented here for completeness, but **ChEMBL is NOT a data source for this skill** (GTEx + HPA only).

---

## Workflow

### 0. Ground the environment (do this first)
Run `scripts/tissue_expression.py`; it uses GTEx and Human Protein Atlas resources
and reports which source it selected.

### 1. Resolve the target
`resolve_gene(query)` → canonical symbol + Ensembl gene ID (+ UniProt). Confirm it maps to
exactly one human protein-coding gene; stop with a clear message if ambiguous/unmapped.
*Why:* both atlases key on Ensembl; GTEx's v8 API needs a versioned GENCODE id.

### 2. Ingest GTEx (datalake-first, API fallback)
`get_gtex(ensembl)` → per-tissue **median TPM** (~54 tissues). Tries the curated datalake
median-TPM file first (freshest, v11); falls back to the GTEx Portal v8 API (aggregating
per-sample TPM to medians). **Record which source + GTEx version was used** for the report.

### 3. Ingest HPA (stream-parse one entry)
`get_hpa(ensembl, symbol)` → per-tissue **nTPM** (~51 tissues) + HPA's native specificity
call/score. The 730 MB XML is stream-parsed for the single target entry (constant memory).
*Why two atlases:* independent platforms/normalizations — agreement raises confidence in
the expression pattern.

### 4. Tau specificity + high-baseline flags
`compute_tau` (Yanai 2005, on log2(x+1)) **per atlas at native resolution**; report log2-τ
(primary) + linear-τ + HPA native call. `flag_high_baseline` applies the **dual threshold**
(absolute ≥10 / ≥25 **OR** top-decile) and tiers each tissue. *Why:* τ quantifies overall
selectivity; flags pinpoint the specific tissues carrying expression.

### 5. Cross-atlas concordance (organ-collapsed)
Collapse GTEx fine sites → organ, harmonize to HPA labels, then `organ_concordance` →
**Spearman ρ (primary, rank-based)** + **Pearson r (log2)** over shared organs, with n.
*Why ranks:* HPA nTPM ≠ GTEx TPM — concordance tests agreement on the *pattern*, not
magnitudes. Skipped gracefully if only one atlas is available.

### 6. On-target safety synthesis + literature
`build_safety_matrix` = **vital-organ core (heart, brain/CNS, liver, kidney, lung)** UNION
any high-baseline organ, each with GTEx/HPA values + on-target flag. Then run **targeted
`LiteratureSearch`** queries for the **top on-target organ(s)** (e.g. `"<GENE> hepatic
safety"`, `"<GENE> renal expression"`) to add cited context. **Cite every external claim
inline `[N]`; never fabricate references.** *Why:* high on-target expression in a vital,
non-intended organ is the key safety signal to surface.

### 7. Figures (infographic + 4 data plots)
- **Infographic** — a conceptual summary (target + top tissues + τ + key safety takeaway).
  This is schematic, so generate it with **`GenerateImage`** (deferred — load via
  `ToolSearch` with `select:GenerateImage`), NOT plotting code.
- **4 data figures** via `make_all_figures(res, figdir)`: ranked bars, concordance scatter,
  τ summary, safety heatmap. Matplotlib/seaborn, Phylo palette, SVG + PNG.
- **Run a `media_output_check` (Read tool) on every figure PNG** before including it;
  regenerate anything blank/clipped/unreadable.

### 8. PDF report (use the `pdf-report-generation` skill)
Load and follow the **`pdf-report-generation`** skill for Phylo branding, ReportLab
building blocks, and validation. Structure:
1. **Title** + attribution
2. **Executive summary** — τ verdict, top tissues, dominant on-target organ, safety takeaway
3. **Introduction** — target biology (class/function/therapeutic rationale) + why tissue
   expression matters for on-target safety
4. **Methods** — data sources & versions actually used, τ definition, thresholds, concordance,
   safety-panel logic (pull from `references/methods.md`)
5. **Results** — the infographic + 4 figures + key tables, with captions
6. **On-target safety interpretation** — organ-by-organ read; expected pharmacology vs
   vital-organ liabilities; literature context (inline-cited)
7. **Limitations** — nTPM≠TPM; bulk masks cell type; τ on medians; expression ≠ exposure;
   GTEx vintage
8. **References** — contiguous, all in-text `[N]` resolving (LiteratureSearch records only).
   **Every reference in the References section must be cited at least once in-text with
   `[N]` notation, and every in-text `[N]` must have a matching reference entry.** Do not
   list a reference that is never cited in the body — either weave it into the safety
   interpretation text or remove it from the list.
9. **Next steps** — extensions (below)
Then **validate**: `pypdf` page count / size / extractable text + a visual
`media_output_check`; confirm citations are contiguous and figures render.

**Save the PDF as `report_<GENE>_tissue_safety.pdf` in `/mnt/results/`.**

### Quick start
```python
import sys; sys.path.insert(0, "scripts")
from tissue_expression import run_analysis
from make_figures import make_all_figures

res = run_analysis("GCGR", "/mnt/results")     # any symbol / Ensembl / UniProt id
figs = make_all_figures(res, "/mnt/results/figures")
# then: GenerateImage infographic  ->  build PDF via pdf-report-generation skill
print(res["tau_table"], res["warnings"])
```

---

## Scientific caveats (state the relevant ones in every report)

1. **nTPM (HPA) ≠ TPM (GTEx).** Compare on ranks/log-scale — the analysis reports *where*
   the gene is expressed (pattern), not absolute magnitude equivalence.
2. **Bulk expression masks cell type.** A tissue value is the cell-mixture average; a rare
   high-expressing cell type can be diluted. Single-cell resolution is out of scope.
3. **τ is computed on median profiles** and depends on the tissue set / normalization.
   Report the log2-τ as primary and always alongside the atlas and n tissues.
4. **Expression ≠ drug exposure.** The target being present in an organ does not prove a
   drug reaches or perturbs it — but high on-target expression in a vital organ is the
   relevant on-target *risk flag*.
5. **GTEx vintage varies** (curated datalake v11 vs v8 API). Always state which was used.
6. **Human only.** Do not apply to mouse/other species with these atlases.
7. **Graceful degradation.** If one atlas is unavailable, still report τ + ranked bars +
   safety synthesis for the available atlas and clearly note the missing cross-atlas step.

## Error handling

- **Gene unresolved/ambiguous** → stop and ask the user for a precise identifier.
- **GTEx datalake absent** → automatic API fallback (expected; note the source).
- **GTEx API empty** → retry with versioned gencodeId `.1`…`.15` with bounded backoff
  between attempts (script does this); if still empty, report the gene id may be wrong /
  not in v8. Transient null responses (empty `data` from a temporary API hiccup) are
  retried before returning missing data.
- **Gene not in HPA** → report HPA coverage gap; continue with GTEx-only outputs.
- **`LiteratureSearch` returns nothing relevant** → say so; do not invent context.

## Next steps / extensions (offer in the report)

- **Cell-type resolution** — HPA single-cell / GTEx snRNA-seq to see which cell types drive
  a tissue's signal.
- **Protein-level evidence** — HPA immunohistochemistry (validated antibodies) to confirm
  protein follows mRNA.
- **Genetic constraint & essentiality** — gnomAD/GeneBass LoF tolerance + DepMap dependency
  (`gene-essentiality` skill) for a fuller on-target liability picture.
- **Selectivity benchmarking** — compare τ / profile against related targets (e.g. a
  receptor family) for co-agonist / off-target context.
- **Mouse cross-species** — mouse expression atlases to assess model-organism relevance.
