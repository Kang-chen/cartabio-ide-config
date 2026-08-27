# Data Sources & Schema Reference

Maintainer notes for the `clinical-variant-allelic-series` skill. Everything here was
verified empirically against live endpoints (EGFR, BRAF, KIT, STK11).

---

## 1. ClinVar (NCBI E-utilities)

Base: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils`

### esearch
```
GET /esearch.fcgi?db=clinvar&term=<GENE>[gene]&retmax=100000&retmode=json
```
Returns `esearchresult.idlist` (variation UIDs) and `esearchresult.count`.
Observed counts: EGFR 4093, BRAF 1624, KIT 3463, STK11 3109, BRCA1 15986.

### esummary (batched)
```
POST /esummary.fcgi   (db=clinvar, retmode=xml, version=2.0, id=<comma-joined batch>)
```
- Batch size **300**, 3 retries with backoff, sleep **0.34 s** (no key) / **0.11 s** (with key).
- Parse `<DocumentSummary uid="...">` elements.

### ClinVar XML fields (v2.0 schema) — verified positions
| Field | Location | Notes |
|---|---|---|
| Variation ID | `DocumentSummary/@uid` | **This is the join key.** Equals numeric part of `<accession>` (`VCV000666267` → `666267`). **Not** `measure_id`. |
| accession | `<accession>` | `VCV…` |
| variant type | `<obj_type>` | e.g. "single nucleotide variant", "Deletion" |
| protein change | top-level `<protein_change>` | **comma-separated across transcripts** — take first token |
| molecular consequence | `<molecular_consequence_list><string>` | DocumentSummary level, **not** inside `variation_set` |
| germline classification | `germline_classification/description` | new schema |
| oncogenicity | `oncogenicity_classification/description` | new schema |
| clinical impact | `clinical_impact_classification/description` | new schema |
| review status | `<review_status>` | star rating source |
| cdna change | `variation_set/variation/cdna_change` | |
| variation name | `variation_set/variation/variation_name` | contains `(p.XXX)` term |
| canonical SPDI | `variation_set/variation/canonical_spdi` | |
| conditions | `trait_name` elements | |

If top-level `<protein_change>` is empty, fall back to parsing `(p.XXX)` from the title.

---

## 2. CIViC (nightly TSV exports)

Base: `https://civicdb.org/downloads/nightly/`

| File | Approx size | Purpose |
|---|---|---|
| `nightly-VariantSummaries.tsv` | ~571 KB | variants (gene in column `gene`, plus `variant_id`, `clinvar_ids`, `single_variant_molecular_profile_id`) |
| `nightly-MolecularProfileSummaries.tsv` | ~653 KB | molecular profiles (single- and multi-variant) |
| `nightly-ClinicalEvidenceSummaries.tsv` | ~3993 KB | one row per evidence item |

- Read with `pd.read_csv(sep='\t', dtype=str).fillna('')`.
- **Gene symbol is in the `gene` column** (do not assume a fixed column index; VariantSummaries
  has ~41 columns and the gene is not near the front).
- Join: Variant → MolecularProfile → Evidence. Keep single-variant profiles as primary.
- Evidence columns of interest: `evidence_level` (A–E), `evidence_type`
  (Predictive/Prognostic/Diagnostic/Predisposing/Oncogenic), `evidence_direction`,
  `significance` (Sensitivity/Response, Resistance, Poor Outcome, …), `therapies`, `disease`.

### The ClinVar ↔ CIViC bridge
CIViC's `clinvar_ids` field contains **ClinVar Variation IDs**, i.e. the same integers as the
ClinVar `uid`. Example: BRAF V600E `clinvar_ids = "13961,376069"`; these match ClinVar UIDs.
This is the highest-confidence join key in `build_allelic_series.py`.

Observed CIViC coverage (variants): EGFR many, BRAF 42, KIT 49, STK11 5, and many tumor
suppressors are 0–10 (CDH1 1, APC 1, NF1 3, MLH1 28, TP53 113, VHL 314).

---

## 3. UniProt (REST)

```
GET /uniprotkb/search?query=gene_exact:<GENE>+AND+organism_id:9606+AND+reviewed:true&fields=accession,length&format=json&size=1
GET /uniprotkb/<accession>.json
```
- Human-only, reviewed (Swiss-Prot) entry.
- From the full entry: `sequence.length` and `features[]`.
- **Domain feature priority:** collect `type in {Domain, Transmembrane}` first (structural);
  only fall back to `{Region, Topological domain}` if no structural domains exist. Keep
  features with `(end-start) >= 15`, sorted by start, first 10. This avoids the broad
  "Extracellular"/"Cytoplasmic" spans overlapping the specific Ig-like/kinase domains.
- Verified accessions: EGFR P00533 (1210 aa), BRAF P15056 (766 aa), KIT P10721 (976 aa),
  STK11 Q15831 (433 aa).

---

## 4. Join-method precedence (build_allelic_series.py)

1. `clinvar_id` (CIViC `clinvar_ids` ↔ ClinVar `uid`) — highest confidence
2. `clinvar_id+protein_change`
3. `protein_change` (normalized HGVS p.)
4. `categorical` (exon19del, exon20ins, amplification, expression, gene-level)
5. source-only (`clinvar_only`, `civic_only`)

Every joined allele must pass the residue-position consistency check (0 discrepancies).
