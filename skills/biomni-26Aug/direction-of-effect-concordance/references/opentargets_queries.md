# Open Targets GraphQL — verified queries for direction-of-effect

Endpoint: `https://api.platform.opentargets.org/api/v4/graphql` (no key, CC0 data).
Always check `payload["errors"]` — GraphQL returns errors in the body with HTTP 200.
Cite the release: `meta { dataVersion { year month } }`.

> **Schema-drift warning (learned the hard way on release 2026.06).** Field names change
> between releases. The fixes below were verified by introspection during the original run; if
> a query 400s with `Cannot query field "X" on type "Y"`, open the playground
> (`.../graphql/browser`) or run an introspection query and adjust. Do **not** guess field
> names.

## Helper

```python
import requests
URL = "https://api.platform.opentargets.org/api/v4/graphql"
def ot_query(query, variables=None):
    r = requests.post(URL, json={"query": query, "variables": variables or {}}, timeout=90)
    r.raise_for_status()
    payload = r.json()
    if "errors" in payload:
        raise RuntimeError(payload["errors"])
    return payload["data"]
```

## 1. Resolve names → IDs (targets and diseases)

```graphql
query Search($q: String!) {
  search(queryString: $q, entityNames: ["target","disease"]) {
    hits { id name entity }
  }
}
```
Take the top `target` hit's `id` (Ensembl) and the intended `disease` hit's `id` (EFO/MONDO).
Flag if the top hit's `name` does not match the requested symbol (ambiguous/deprecated).

## 2. Target profile — drug MoA, mouse pheno, genetics assoc, safety (one traversal)

**Verified field names for 2026.06** (these are the ones that work):

```graphql
query Target($ensemblId: String!) {
  target(ensemblId: $ensemblId) {
    id approvedSymbol approvedName biotype isEssential
    tractability { label modality value }
    safetyLiabilities { event eventId datasource literature }
    drugAndClinicalCandidates {
      count
      rows {
        maxClinicalStage
        drug {
          id name maximumClinicalStage drugType
          mechanismsOfAction { rows { mechanismOfAction actionType targetName } }
        }
      }
    }
    mousePhenotypes {
      modelPhenotypeLabel
      modelPhenotypeClasses { id label }
    }
    associatedDiseases(page: { index: 0, size: 12 }) {
      rows {
        disease { id name }
        score
        datatypeScores { id score }
      }
    }
  }
}
```

### Schema-drift fixes (what changed vs. older docs / older releases)

| Older field (may 400) | Use instead (2026.06) |
|---|---|
| `target.knownDrugs { ... }` | `target.drugAndClinicalCandidates { rows { maxClinicalStage drug{...} } }` (row type `ClinicalTargetFromTarget`) |
| `drug.isApproved` | `drug.maximumClinicalStage` (values like `"APPROVAL"`, `"PHASE_3"`, ...) |
| `drug.maximumClinicalTrialPhase` | `drug.maximumClinicalStage` |
| MoA at top level | `drug.mechanismsOfAction { rows { mechanismOfAction actionType targetName } }` |

**Action-type → direction:** `actionType` values such as `INHIBITOR`, `ANTAGONIST`,
`BLOCKER`, `NEGATIVE MODULATOR`, `DEGRADER`, `RNAI`, `ANTISENSE INHIBITOR` → **INHIBIT**;
`AGONIST`, `ACTIVATOR`, `POSITIVE MODULATOR`, `PARTIAL AGONIST` → **ACTIVATE**. Weight
approved (`maximumClinicalStage == "APPROVAL"`) above pipeline.

## 3. Disease → association score for a specific target (genetics datatype)

Use the target's `associatedDiseases` rows (above) filtered to the indication's EFO ID, and
read the `datatypeScores` entry with `id == "genetic_association"` for the human-genetics
association strength. For deeper variant-level direction, use the `variant` / `credibleSet` /
L2G queries documented in the `open-targets` skill and hand off to `gwas-to-function-twas`.

## 4. Release/version (cite it)

```graphql
query { meta { dataVersion { year month } } }
```

## Notes

- Do **not** loop the API over thousands of entities; this skill handles a handful of targets,
  which is fine. For bulk, use OT FTP/BigQuery.
- `mousePhenotypes` gives phenotype *classes/labels*, not the KO direction per se — combine
  with literature (Step 3) to assign the mouse-KO vote.
- `safetyLiabilities` populates the on-target **safety flags** (kept separate from direction).
