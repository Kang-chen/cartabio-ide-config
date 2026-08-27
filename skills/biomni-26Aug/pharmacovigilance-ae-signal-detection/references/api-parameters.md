# API Parameters & Query Rules

Deep reference for the two external APIs this skill uses. The scripts encode all
of this; read it only when debugging or extending.

## OpenFDA drug endpoints

| Purpose | Endpoint | Constant |
|---------|----------|----------|
| Adverse-event (FAERS) reports | `https://api.fda.gov/drug/event.json` | `query_faers.EVENT_URL` |
| Structured product labels (SPL) | `https://api.fda.gov/drug/label.json` | `query_faers.LABEL_URL` |

**Auth:** none required. Optional free API key raises the rate limit from 240
req/min & 1,000/day to 240 req/min & 120,000/day. Pass via `api_key=`; the client
adds it as the `api_key` query parameter.

### Key fields

| Field | Meaning |
|-------|---------|
| `patient.reaction.reactionmeddrapt` | MedDRA Preferred Term for the adverse event |
| `patient.reaction.reactionmeddrapt.exact` | `.exact` variant — **required for faceting/counting** exact terms |
| `patient.drug.openfda.generic_name` | generic drug name |
| `patient.drug.openfda.brand_name` | brand drug name |
| `patient.drug.openfda.substance_name` | active substance |
| `openfda.pharm_class_epc` | Established Pharmacologic Class (label endpoint) |
| `openfda.pharm_class_moa` | Mechanism of Action class |
| `openfda.pharm_class_cs` | Chemical Structure class |
| `openfda.generic_name.exact` | exact generic name (for counting class members) |

### Query construction rules (CRITICAL)

1. **Never manually `urllib.parse.quote` a term and then wrap it in quotes.**
   OpenFDA phrase search needs `field:"term with spaces"`. Manual quoting turns
   the space into `%20` *inside* the quotes and returns 0 hits. The scripts pass
   raw terms into a quoted phrase (letting `requests` encode the whole param).
   - `fetch_drug_label` / `count_single_term` do call `urllib.parse.quote` on a
     term and embed it — this works only because those are used for
     single-token or already-safe values; do **not** copy that pattern for
     multi-word phrase search of reaction terms.
2. **Counting exact terms** requires the `.exact` field and `count=` OR
   `search=... & limit=1` then read `meta.results.total`. The facet `limit`
   **cannot exceed 500** (`MAX_COUNT_LIMIT`); OpenFDA returns HTTP 400 otherwise.
3. **Total reports for a search:** `{"search": search, "limit": 1}` →
   `meta.results.total` (`get_total`).
4. **Drug clause** (match generic OR brand):
   `(patient.drug.openfda.generic_name:"X" OR patient.drug.openfda.brand_name:"X")`
   (`_drug_clause`). Multiple drugs → OR them (`drug_event_search`).
5. **Background / denominator:** `_exists_:patient.reaction.reactionmeddrapt`
   (all reports with a coded reaction) for a whole-FAERS comparator, or the drug
   clause of a custom comparator set (`background_search`).
6. **Co-occurrence (cell a):** `<drug clause> AND <reaction .exact>` count.

### Retry / error handling (`_request`)

| Condition | Action |
|-----------|--------|
| HTTP 429 (rate limit) | sleep `2 + i*3` s, retry |
| HTTP 404 | return `None` (no results — normal for unmatched drug/term) |
| HTTP 400 | return `{"__http400__": True}` (bad query — caller decides) |
| HTTP 5xx / network / timeout | sleep `2 + i*2` s, retry |

Default `DEFAULT_TIMEOUT = 60` s; a few retries per call.

### Apostrophe / special-character terms

MedDRA terms contain apostrophes ("Crohn's disease", "Still's disease") whose
encoding varies across FAERS records (`'`, `’` U+2019, or stripped). Both
`count_single_term` and the figure/label matchers try apostrophe **variants**:
for a `'` try also `’` and the stripped form; for `’` try `'` and stripped; the
`'S` possessive may appear as `S`. Never assume one canonical form.

## Open Targets GraphQL (target mode)

- Endpoint: `https://api.platform.opentargets.org/api/v4/graphql`
- **Step 1 — symbol → Ensembl gene ID:** `search` query, take `hits[0].id`.
- **Step 2 — drugs for the target:**
  ```graphql
  query targetDrugs($ensg: String!) {
    target(ensemblId: $ensg) {
      approvedSymbol
      drugAndClinicalCandidates { count rows { drug { name } } }
    }
  }
  ```
  Parse `data.target.drugAndClinicalCandidates.rows[].drug.name`.
- **Obsolete:** the old `knownDrugs` field / `KnownDrug` type no longer exists;
  `drugAndClinicalCandidates` takes **no arguments**.
- Returned names often carry **salt/ester suffixes** (RUXOLITINIB PHOSPHATE,
  TOFACITINIB CITRATE). `_base_generic` normalizes them to the base generic so
  FAERS matching works.

## Class-resolution specifics (`resolve_class_members`)

- Uses **`/drug/label.json`** (not event) to avoid co-report contamination.
- Searches `openfda.pharm_class_epc`, then `_moa`, then `_cs` with
  `field:"<canonical class>"`.
- Informal names are mapped to canonical EPC labels via `_CLASS_SYNONYMS`
  (e.g. "SGLT2 inhibitors" → "Sodium-Glucose Cotransporter 2 Inhibitor",
  "statins" → "HMG-CoA Reductase Inhibitor", "JAK inhibitors" → "Janus Kinase
  Inhibitor"). `_canonical_class` singularizes the plural stem first.
- Members counted via `openfda.generic_name.exact`, `limit=60`.
- Combination products are dropped (`_is_single_generic` removes names with
  " and ", "/", "+", ",").
- `_base_generic` + descriptor stripping normalize each member.
