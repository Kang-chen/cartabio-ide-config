# SigCom LINCS API — connectivity mapping reference

SigCom LINCS (Maayan Lab, *Nucleic Acids Research* 2022) is the primary connectivity engine for this
skill. It exposes the full LINCS L1000 chemical-perturbation library and returns two-sided
connectivity scores for a query up/down gene signature. This file captures the exact endpoints and
payloads verified to work end-to-end.

## Base URLs
```python
METADATA_API = "https://maayanlab.cloud/sigcom-lincs/metadata-api"
DATA_API     = "https://maayanlab.cloud/sigcom-lincs/data-api/api/v1"
```
Reachability is not guaranteed from every sandbox. Probe first with the **real** gene-resolution
endpoint (`POST {METADATA_API}/entities/find` with a tiny gene list) and check for HTTP 200 — do
NOT probe `/listdata`, which returns 404 on the live server and would trigger a false fallback. If
the probe is non-200 or times out, use the local-GMT fallback (see SKILL.md Stage 3).

## Repository / database strings (the `database` parameter)
The `database` field in the enrich call is a **repository string**, NOT a library UUID:
- `"l1000_cp"` — full L1000 chemical perturbations (~693,215 signatures). **Primary.**
- `"l1000_mean_cp"` — collapsed/consensus chemical perturbations (~33,608). Corroboration check.
- (Other repos exist, e.g. `l1000_xpr` for CRISPR KO; not used for small-molecule repurposing.)

## Step 1 — Resolve gene symbols to L1000 entities
```python
import requests
def resolve_genes(genes):
    r = requests.post(f"{METADATA_API}/entities/find",
                      json={"filter": {"where": {"meta.symbol": {"inq": list(genes)}}}})
    r.raise_for_status()
    items = r.json()
    sym2id = {e["meta"]["symbol"]: e["id"] for e in items}   # symbol -> entity UUID
    return sym2id
```
Report coverage = resolved / requested per direction. L1000 measures ~12k genes (landmark +
inferred), so well-formed human signatures typically resolve >90%.

## Step 2 — Two-sided reversal query
```python
def connectivity_query(up_ids, dn_ids, database="l1000_cp", limit=2000):
    body = {"up_entities": list(up_ids), "down_entities": list(dn_ids),
            "limit": limit, "database": database}
    r = requests.post(f"{DATA_API}/enrich/ranktwosided", json=body)
    r.raise_for_status()
    return r.json()
```
**Response shape (top-level):**
- `results` — **list** of scored signature rows (this is the ranked output).
- `reversers`, `mimickers` — **integer counts** (NOT lists; a common bug is calling `len()` on them).
- `signatures`, `maxRank`, `queryTimeSec`.

**Each row in `results`:**
`direction-up`, `direction-down`, `z-up`, `z-down`, `z-sum`, `p-up`, `p-down`, `fdr-up`,
`fdr-down`, `logp-avg`, `logp-fisher`, `type` (`"reversers"` | `"mimickers"`), `uuid`, `rank`.

**Identify reversers (therapeutic direction):**
```python
import pandas as pd
df = pd.DataFrame(resp["results"])
reversers = df[df["type"] == "reversers"]        # negative z-sum; direction-up=-1, direction-down=1
mimickers = df[df["type"] == "mimickers"]        # positive z-sum
# strongest reverser = most negative z-sum
reversers = reversers.sort_values("z-sum")
```
Reversal logic: for a reverser, the compound pushes the query-**up** genes **down** and the
query-**down** genes **up** — i.e. it opposes the disease state (candidate therapeutic).

## Step 3 — Resolve signature UUIDs to compound metadata
```python
def signature_meta(uuids, batch=100):
    out = {}
    for i in range(0, len(uuids), batch):
        chunk = uuids[i:i+batch]
        r = requests.post(f"{METADATA_API}/signatures/find",
                          json={"filter": {"where": {"id": {"inq": chunk}}}})
        r.raise_for_status()
        for s in r.json():
            m = s.get("meta", {})
            out[s["id"]] = {k: m.get(k) for k in
                            ("pert_name","cell_line","pert_dose","pert_time",
                             "pubchem_id","cmap_id","moa")}
    return out
```
Note: in `l1000_cp` roughly three-quarters of signatures carry a resolved `pert_name`; the rest are
raw `BRD-...` screening IDs. In `l1000_mean_cp` most are raw BRD IDs (annotation-coverage limitation).

## Step 4 — Map BRD screening IDs to compound names (Broad Drug Repurposing Hub)
```python
import re, pandas as pd
def base_brd(bid):
    m = re.match(r"(BRD-[A-Z0-9]+)", str(bid))
    return m.group(1) if m else None

hub = pd.read_parquet("/mnt/datalake/broad_drug_repurposing_hub/"
                      "broad_repurposing_hub_molecule_with_smiles.parquet")
# build base_brd -> pert_iname map from the hub's BRD column, then apply to unresolved names
```
MoA/target/phase come from `broad_repurposing_hub_phase_moa_target_info.parquet`.

## Common pitfalls (learned)
- `reversers`/`mimickers` are **counts**, not lists — read the ranked list from `results`.
- `database` is a **repo string** (`"l1000_cp"`), not a UUID.
- Early 500s / JSONDecodeErrors came from wrong endpoints; use `metadata-api/entities/find` for gene
  resolution and `data-api/api/v1/enrich/ranktwosided` for the query.
- PubChem name resolution of BRD IDs usually returns nothing (BRD screening compounds lack public
  CIDs) — rely on the Broad hub instead.

## Offline fallback (local GMT enrichment)
When the API is unreachable, approximate connectivity locally: for each compound perturbation set in
`/mnt/datalake/LINCS1000/RNAseq_transcriptomics_genesets/single_drug_perturbations-v1.0.gmt` (which
provides compound up/dn gene sets), score reversal by the overlap of query-up with compound-down and
query-down with compound-up (e.g. Fisher/GSEA-style). This is coarser than L1000 z-sum — label
results as the fallback path and treat ranks as indicative only.
