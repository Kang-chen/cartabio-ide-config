# Data sources & identifier resolution reference

How `scripts/tissue_expression.py` obtains data. All sources are **human** atlases.
The workflow uses GTEx and Human Protein Atlas resources.

---

## Gene identifier resolution

Accepts a **gene symbol** (`GCGR`), **Ensembl gene id** (`ENSG00000215644`, versioned or
not), or **UniProt accession** (`P47871`). Resolution order (`resolve_gene`):

1. **Ensembl-id shortcut** — if the input matches `ENSG\d{11}(\.\d+)?`, use it directly.
2. **HPA subset TSV** (`proteinatlas_subset.tsv`) — has `Gene`, `Ensembl`, `Uniprot`
   columns. Self-contained (HPA is a required source anyway), so no external API needed for
   the common symbol→Ensembl case.
3. **`gget` fallback** — `gget.search(query, species="homo_sapiens")` against Ensembl if
   still unresolved (the `gget` package is preinstalled).

Additional queryable databases available if deeper resolution is ever needed: **Ensembl**,
**UniProt**, **NCBI** (all in the Biomni queryable-DB set). Errors clearly if the id is
ambiguous or maps to no human protein-coding gene.

---

## GTEx (bulk RNA-seq) — DATALAKE-FIRST, API-FALLBACK

**Primary — curated datalake median-TPM file (freshest, v11):**
- The datalake advertises a GTEx collection incl. a gene-median-TPM GCT/parquet under
  `GTEx/bulk_tissue_expression/` (e.g.
  `GTEx_Analysis_2025-08-22_v11_RNASeQCv2.4.3_gene_median_tpm.gct.gz`). The script globs
  several patterns — including extension-agnostic globs that match `.gct.gz`, `.gct`
  (uncompressed), and `.parquet` — so the datalake-first path works regardless of
  compression or exact filename.
- GCT format: 2 header lines, then `Name` (versioned Ensembl), `Description`, then one
  column per tissue. Match the gene on the **unversioned** Ensembl prefix.
- **Fallback:** use the GTEx Portal v8 API.

**Fallback — GTEx Portal v8 API:**
```python
import requests
r = requests.get("https://gtexportal.org/api/v2/expression/geneExpression",
                 params={"gencodeId": "ENSG00000215644.9", "datasetId": "gtex_v8"},
                 headers={"Accept": "application/json"}, timeout=90)
data = r.json()["data"]   # list; each has tissueSiteDetailId + per-sample TPM array 'data'
```
- The v2 API needs a **versioned** `gencodeId` (GENCODE v26 for v8). If the version is
  unknown, the script retries `.1`…`.15` until one returns data.
- Aggregate each tissue's per-sample TPM array to a **median** (and mean).
- Returns **~54 tissues**. Record the exact source string (endpoint + gencodeId used).
- Note: the `medianGeneExpression` endpoint returned empty in testing — use
  `geneExpression` (per-sample) and compute medians yourself.

Always record which source + GTEx version was actually used, and put it in the report Methods.

---

## Human Protein Atlas (HPA) — consensus tissue, stream-parsed

- Resource: Human Protein Atlas XML (~730 MB gzip).
- **Stream-parse line-by-line**, accumulate each `<entry>…</entry>`, stop at the entry
  containing the target Ensembl id. **Constant memory — never load the whole file.**
- Extract the consensus-tissue block:
  ```
  <rnaExpression source="HPA" ... assayType="consensusTissue"> ... </rnaExpression>
  ```
- Within it, each `<data>…</data>` holds one `<tissue organ="...">name</tissue>` plus
  expression levels; pull:
  - `type="normalizedRNAExpression" unitRNA="nTPM"` → **nTPM** (primary HPA unit)
  - `type="proteinCodingRNAExpression" unitRNA="pTPM"` → pTPM (QC)
  - `type="RNAExpression" unitRNA="TPM"` → TPM (QC)
- Also capture HPA's native call: `<rnaSpecificity description="..." specificity="...">`
  and `<rnaDistribution>`. Apply `html.unescape()` to organ-group strings (fixes `&amp;`).
- Returns **~51 consensus tissues**.
- **Native specificity score:** column `RNA tissue specificity score` in
  `proteinatlas_subset.tsv` (keyed by `Gene` symbol).

**Note:** `proteinatlas_subset.tsv` only carries summary columns + the enriched tissues —
the **XML is the source** for the full per-tissue profile. Do not rely on the TSV for the
per-tissue vector.

---

## Environment resources actually used (from the Biomni catalog)

| Purpose | Resource | Type |
|---|---|---|
| Bulk tissue expression | **GTEx** (datalake, 25 datasets) + GTEx Portal API | datalake / API |
| Protein/RNA tissue atlas | **Human Protein Atlas** (datalake) | datalake |
| Gene id resolution | Ensembl / UniProt / NCBI; `gget` | queryable DB / package |
| Literature context | **`LiteratureSearch`** (Biomni tool) | agent tool |
| Summary infographic | **`GenerateImage`** (deferred; load via `ToolSearch`) | agent tool |
| Compute / plotting | `pandas`, `numpy`, `scipy`, `matplotlib`, `seaborn` | preinstalled |
| PDF build & validation | `reportlab`, `pypdf` + `pdf-report-generation` skill | preinstalled / skill |

## Data source licenses & attribution

This skill relies on external data sources. Their licenses and attribution/redistribution
obligations are summarized below. **Check the upstream license page before any commercial
or redistribution use** — terms can change between data-source versions.

| Data source | Used for | License | Commercial use | Obligations |
|---|---|---|---|---|
| **Human Protein Atlas (HPA)** | Per-tissue consensus RNA expression (nTPM), native specificity call | **CC BY-SA 3.0** for all HPA-copyrightable content, specifically the XML fields marked `source="HPA"` that this skill parses (the current main site also states CC BY 4.0 for the database as a whole — see note) | **Yes** | **Attribution + ShareAlike.** Cite an HPA primary publication AND link to proteinatlas.org; derivative/redistributed HPA-derived content must be shared under the same CC BY-SA license. |
| **GTEx** (Portal open-access summary data: gene median-TPM / gene-TPM; and the v8 Portal API used as fallback) | Per-tissue bulk RNA-seq median TPM | **Open-access** under the **NIH Genomic Data Sharing (GDS) policy** — since v5, *no restrictions on use or publication after release*. Not a CC BY-SA license. | **Yes** (open access, unrestricted) | **Attribution.** Cite the GTEx Portal (date accessed) and dbGaP accession `phs000424`. Note: only *summary/open-access* GTEx data is used here — raw sequence and full donor metadata are **protected access** (dbGaP/AnVIL) and are NOT used by this skill. |
| **Ensembl / UniProt / NCBI**, `gget` | Gene-identifier resolution only (no bulk data redistributed) | Ensembl: no restrictions (EMBL-EBI terms); UniProt: **CC BY 4.0**; NCBI: US-Gov public domain / per-resource | Yes | Attribution per each resource's terms when identifiers/annotations are reused. |
| **Biomni `LiteratureSearch`** records | Cited safety/biology context in the report | Underlying papers retain their own copyright; only bibliographic metadata + short highlights are surfaced | Cite normally | Cite each paper; do not redistribute full text. |

### Explicit commercial-use + share-alike notes (as required for this skill's sources)

- **Human Protein Atlas — CC BY-SA (Attribution-ShareAlike).** HPA data (the `source="HPA"`
  XML this skill stream-parses) is released under **CC BY-SA 3.0**, which **permits commercial
  use** but **requires (a) attribution** — cite an HPA primary publication and link to
  `http://www.proteinatlas.org` — **and (b) share-alike**: any redistributed or adapted
  HPA-derived data/content must be licensed under the same CC BY-SA terms. (The current
  `www.proteinatlas.org/about/licence` page states **CC BY 4.0** for the database as a whole;
  the CC BY-SA 3.0 statement is the one historically applied and still attached to the
  downloadable `source="HPA"` XML fields. When in doubt, honor the stricter ShareAlike terms
  and contact `contact@proteinatlas.org` for restricted uses.)
- **ChEMBL — CC BY-SA 3.0 (documented for completeness; NOT a data source for this skill).**
  ChEMBL is **not** used anywhere in this skill (this is a tissue-expression skill built on
  GTEx + HPA). For reference, ChEMBL is released under **CC BY-SA 3.0**, which likewise
  **permits commercial use** but **requires attribution + share-alike**. If a future variant
  of this skill were to incorporate ChEMBL bioactivity/target data, those CC BY-SA 3.0
  attribution + share-alike obligations would apply to the ChEMBL-derived content.

### Practical attribution snippet (for reports built with this skill)

> Tissue-expression data: **GTEx** (GTEx Portal, accessed <date>; dbGaP phs000424) and the
> **Human Protein Atlas** (proteinatlas.org; cite HPA primary publication). HPA content is
> used under **CC BY-SA 3.0** (attribution + share-alike); GTEx open-access summary data is
> used under the NIH Genomic Data Sharing policy.

---

## Complementary skills (for extensions, not duplicated here)

- **`open-targets`** — target–disease association / tractability.
- **`gene-essentiality`** — DepMap dependency (note: essentiality scores are inverted).
- **`literature-preclinical`** / **`literature-review`** — deep evidence synthesis.
- **`omics-dataset-retrieval`** — finding additional expression datasets.
