# cBioPortal REST API cheat-sheet

Base URL: `https://www.cbioportal.org/api`  (public instance; no auth for public studies)
Interactive docs / Swagger: `https://www.cbioportal.org/api/swagger-ui/index.html`

All endpoints below are **gene- and cohort-agnostic** — pass gene symbols/IDs and
study IDs as arguments. The `scripts/cbioportal_client.py` helper wraps every call
with retry/backoff.

## Core endpoints

| Purpose | Method + path | Notes |
|---|---|---|
| Resolve a gene | `GET /genes/{hugoSymbolOrEntrez}` | Returns `{entrezGeneId, hugoGeneSymbol, type}`. Works with symbol (`KRAS`) or Entrez (`3845`). |
| List all studies | `GET /studies?pageSize=10000&projection=SUMMARY` | Each has `studyId`, `name`, `cancerTypeId`, `allSampleCount`. |
| Molecular profiles | `GET /studies/{studyId}/molecular-profiles` | Filter by `molecularAlterationType`. |
| Sample lists | `GET /studies/{studyId}/sample-lists` | Denominators live here. |
| Sample IDs in a list | `GET /sample-lists/{sampleListId}` | Read the `sampleIds` array (default projection lacks counts). |
| Mutations for gene(s) | `POST /molecular-profiles/{profileId}/mutations/fetch?projection=DETAILED` | Body `{"entrezGeneIds":[...],"sampleListId":"{study}_sequenced"}`. |
| Discrete CNA for gene(s) | `POST /molecular-profiles/{profileId}/discrete-copy-number/fetch?discreteCopyNumberEventType=ALL` | Body `{"entrezGeneIds":[...],"sampleListId":"{study}_cna"}`. Records carry integer `alteration` in {-2,-1,0,1,2}. |
| Cancer type per sample | `POST /studies/{studyId}/clinical-data/fetch?clinicalDataType=SAMPLE` | Body `{"attributeIds":["CANCER_TYPE"]}`. Needed only for mixed-cancer studies (e.g. MSK). |
| Cancer type dictionary | `GET /cancer-types?pageSize=...` | Oncotree-like labels (optional, for harmonization). |

## Molecular profile types

- **Mutation**: `molecularAlterationType == "MUTATION_EXTENDED"` → profile id usually `{study}_mutations`.
- **Discrete CNA**: `molecularAlterationType == "COPY_NUMBER_ALTERATION"` and `datatype == "DISCRETE"`.
  - TCGA PanCancer Atlas → `{study}_gistic` (prefer this; GISTIC 2.0 discrete calls).
  - MSK-IMPACT → `{study}_cna`.
  - Resolution rule: prefer a profile whose id contains `gistic`, else the discrete `_cna` profile.

## Sample-list ID conventions (denominators)

- Mutation-profiled samples: `{studyId}_sequenced`
- CNA-profiled samples: `{studyId}_cna`
- All samples: `{studyId}_all`
Always confirm the list exists (`GET /studies/{studyId}/sample-lists`) before use;
some studies omit one assay.

## Cohort auto-selection defaults

- **TCGA** default = all studies ending in `_tcga_pan_can_atlas_2018` (one study per
  cancer type; each study IS the cancer type — no clinical split needed).
- **MSK-IMPACT** default = the largest pan-cancer MSK-IMPACT study with
  `cancerTypeId == "mixed"`; prefer `msk_impact_2017` (Zehir et al., Nat Med 2017,
  10,945 samples) as the most comparable to the TCGA 2018 freeze. Split by the
  `CANCER_TYPE` clinical attribute.
- User can override by naming studies or a keyword (`find_studies_by_keyword`).

## Practical notes / known quirks

- **Batch fetch, don't loop per sample.** The `mutations/fetch` and
  `discrete-copy-number/fetch` POST endpoints take a `sampleListId` and return all
  matching records in one call. For KRAS-scale (one gene) queries this is a few
  thousand rows total across ~33 studies.
- **Rate limits / transient 5xx.** Retry statuses {429,500,502,503,504} with linear
  backoff (client does this).
- **MSK vs TCGA columns differ.** MSK mutation records carry fewer columns than TCGA;
  rely only on `sampleId`, `mutationType`, `proteinChange`, `proteinPosStart`.
- **`projection=DETAILED`** on mutations returns protein change + classification needed
  for hotspot binning; the default projection is sparser.
- **Structural variants / fusions** use a separate endpoint
  (`/molecular-profiles/{id}/structural-variant/fetch`) and are OUT of scope for this
  skill (alteration = mutation + CNA only).
