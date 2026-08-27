# ChEMBL REST API — recipes for compound bioactivity mining

This skill uses the **public ChEMBL REST API** directly. The
`chembl_webresource_client` Python package is **not installed** in the Biomni
environment, so do not rely on it — the direct REST calls below are validated and
are what `scripts/chembl_potency.py` implements.

Base URL: `https://www.ebi.ac.uk/chembl/api/data`
All endpoints accept `format=json`. Results are paginated; follow
`page_meta.next` (a relative path — prepend `https://www.ebi.ac.uk`).

ChEMBL is one of Biomni's 17 bundled queryable databases (see the supported-
resources catalog). No API key or license is required for public REST access.

---

## 1. Resolve a compound to one molecule

Order of attempts (see `resolve_compound`):

1. **ChEMBL ID** (`CHEMBLxxxx`): `GET /molecule/{id}.json`
2. **Name / synonym**: `GET /molecule/search?q={query}&format=json&limit=25`
   → records under `molecules`. Falls back to
   `molecule.json?molecule_synonyms__molecule_synonym__iexact={query}`.
3. **Structure** (if SMILES supplied):
   `molecule.json?molecule_structures__canonical_smiles__flexmatch={smiles}`

Always inspect ALL candidates (`molecule_chembl_id`, `pref_name`, `max_phase`)
and **exclude close analogues**. Worked example: olaparib resolves to
`CHEMBL521686` (max_phase 4); its back-up analogue **AZD2461** (`CHEMBL4098253`)
is a *different* molecule and must be excluded. Prefer an exact `pref_name`
match; otherwise the highest `max_phase`.

Useful molecule fields: `molecule_chembl_id`, `pref_name`, `max_phase`,
`molecule_structures.canonical_smiles` (feed to `predict_admet_properties`),
`molecule_synonyms`.

---

## 2. Pull bioactivity (activity endpoint)

Compound-centric (primary mode):
```
/activity.json?molecule_chembl_id={id}&standard_type__in=IC50,Ki,Kd&format=json&limit=1000
```
Target-centric (rank many compounds on one target):
```
/activity.json?target_chembl_id={id}&standard_type__in=IC50,Ki&format=json&limit=1000
```

`standard_type__in` is a comma list. Default affinity set:
`IC50,Ki,Kd,Kd(app)`. Add `EC50,Potency` only when functional potency is wanted
(and keep them in a separate bucket from binding/inhibition).

Pagination (validated pattern):
```python
def fetch_all(url):
    out = []
    while url:
        d = json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=180))
        out.extend(d['activities'])
        nxt = d['page_meta'].get('next')
        url = 'https://www.ebi.ac.uk' + nxt if nxt else None
    return out
```

### Activity fields to KEEP (assay provenance)
`activity_id`, `assay_chembl_id`, `assay_description`, `assay_type`
(B=binding, F=functional, A=ADMET, T=toxicity), `bao_label` (assay format —
`single protein format`, `cell-based format`, `assay format`, …),
`target_chembl_id`, `target_pref_name`, `target_organism`,
`document_chembl_id`, `document_year`, `document_journal`,
`standard_type`, `standard_relation` (`=`, `>`, `<`, `>=`, `<=`),
`standard_value`, `standard_units`, `pchembl_value`
(= −log10(molar) precomputed by ChEMBL), `data_validity_comment`.

### `data_validity_comment` values that matter
- `Potential transcription error` → **drop**.
- `Outside typical range` → **keep but flag** (very common for very potent
  drugs; excluding it biases medians upward).
- `None`/empty → normal.

---

## 3. Resolve targets

`GET /target/{target_chembl_id}.json` → `target_type`
(`SINGLE PROTEIN`, `PROTEIN COMPLEX`, `CELL-LINE`, `ORGANISM`, …),
`organism`, `pref_name`, `target_components` (UniProt accessions under
`target_component_synonyms` / `accession`).

Tiering is **data-driven** — never hard-code target IDs. Detect the primary
target(s) from the data (`detect_primary_targets`), group other single-protein
targets as off-targets, and route cell-line / organism assays to a separate
cellular tier by `bao_label` / `assay_class`.

---

## 4. Units and censored values

- Keep `standard_units == 'nM'` for the aggregation set. Other unambiguous
  concentration units (`uM`, `pM`, `M`) can be converted to nM; ambiguous units
  (`ug.mL-1`, `%`) are dropped from potency aggregation.
- Censored relations (`>`, `<`, `>=`, `<=`) are **set aside** from medians and
  reported as **bounds** (e.g. an off-target `IC50 > 10000 nM` becomes a
  "≥ fold" lower bound in selectivity). They are biologically meaningful
  (they demonstrate *lack* of activity) so never silently discard them.

---

## 5. Rate / robustness

Public REST is generous but not unlimited. `scripts/chembl_potency.py` retries
with backoff. A whole-compound pull is typically hundreds–few-thousand records
(seconds–~2 min). If you hit transient errors, retry; if ChEMBL is unreachable,
report that rather than fabricating data.

---

## 6. Optional structure-based enrichment (Biomni tools)

Once you have the compound's `canonical_smiles` from ChEMBL you may add a
**predicted** ADMET context box:
```python
from biomni.tool import pharmacology
admet = pharmacology.predict_admet_properties([smiles])   # MPNN by default
```
Clearly label these as *predicted* properties, not measured ChEMBL data. Use
the documented `biomni.tool.pharmacology` module and verify any additional helper
with a direct import before use.
