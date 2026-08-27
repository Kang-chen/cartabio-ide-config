# Data sources & licensing — knowledge-graph target reasoning

This skill ranks targets over **PrimeKG**, which integrates ~20 upstream resources.
Some of those resources are **not licensed for commercial use**. To avoid silently
shipping restricted-source content in a commercial deliverable, the pipeline is
**provenance-aware** on two independent layers:

1. **Edge layer** — it reads PrimeKG's per-edge `x_source` / `y_source` columns
   and, in the default **commercial** mode, drops every edge whose node source is a
   restricted resource (this catches **DrugBank**).
2. **Seed layer** — because the disease→gene seeds are the entire foundation of the
   random-walk ranking and their evidence source (**DisGeNET**) is *invisible* to
   the edge-source columns (see §1.1), commercial mode **replaces** the seeds with a
   genuinely commercial-usable source (**Open Targets genetics, CC0**) via
   `--seeds-file`.

Both layers are required. Filtering `x_source`/`y_source` alone is **not**
sufficient to make the seed layer commercial-safe.

This file records (a) every source actually present in the current PrimeKG build,
(b) which sources feed the ranking, (c) their licenses and commercial status, and
(d) exactly what each `--edge-license` mode includes.

## 1. Sources present in this PrimeKG build

Measured directly from the distinct `x_source` / `y_source` values in
`primekg.csv`, cross-checked against the PrimeKG paper (Chandak, Huang & Zitnik,
*Sci Data* 2023, doi:10.1038/s41597-023-01960-3).

| Source string in graph | What it labels | Feeds the **ranking**? | License (typical) | Commercial use |
|---|---|---|---|---|
| **NCBI** | gene/protein **nodes** | **yes** — `protein_protein` backbone + `disease_protein` gene end | US Government work / public domain | **Yes** |
| **Gene Ontology (GO)** | GO-term nodes | **yes** — `bioprocess_protein`, `molfunc_protein` | CC BY 4.0 | **Yes** (attribution) |
| **Reactome** | pathway nodes | **yes** — `pathway_protein` | CC0 1.0 | **Yes** |
| **MONDO** (+ `MONDO_grouped`) | disease **nodes** | **yes** — `disease_protein` disease end | CC BY 4.0 | **Yes** (attribution) |
| **UBERON** | anatomy nodes | no (not a ranker relation) | CC BY 3.0/4.0 | Yes (attribution) |
| **HPO** | phenotype nodes | no (not a ranker relation) | HPO license (free; attribution) | Generally yes (verify) |
| **CTD** | some edge annotations | no (not in ranker `KEEP` set) | CTD terms — **restricted for commercial redistribution** | **No** (but not consumed) |
| **DrugBank** | drug **nodes** | **yes (academic only)** — `drug_protein`, `indication`, `off-label use`, `contraindication` | DrugBank academic license / proprietary | **No** |

### 1.1 CRITICAL: `x_source`/`y_source` are NODE vocabularies, not edge-evidence sources

Per the PrimeKG paper, `node_source` is *"the ontology from which node_id and
node_name were extracted."* These columns therefore record the **vocabulary each
endpoint node was drawn from**, **not** the database that provided the *evidence*
for the relationship. Concretely, for a `disease_protein` edge:

```
disease_protein: 160,822 edges
  x_source = NCBI    y_source = MONDO     (gene node = NCBI, disease node = MONDO)
  x_source = MONDO   y_source = NCBI      (disease node = MONDO, gene node = NCBI)
```

The disease–gene *associations* in these edges are curated by PrimeKG primarily
from **DisGeNET** (CC BY-NC-SA 4.0 — **non-commercial**), yet the source columns
only ever read `NCBI`/`MONDO` (the node ontologies). **Consequences:**

- **DisGeNET is structurally invisible** to an `x_source`/`y_source` filter. No
  value of `--restricted-sources` can drop the DisGeNET-derived `disease_protein`
  seed edges by column matching, because the string "DisGeNET" never appears there.
- **KEGG** is likewise not a node-source string in this build (pathway nodes are
  Reactome), so it too cannot be caught by column filtering — and KEGG is not
  actually a PrimeKG pathway source here.
- The edge filter **does** work for **DrugBank** only because DrugBank supplies the
  *drug nodes*, so `drug_protein`/`indication`/`off-label use`/`contraindication`
  edges carry `x_source=DrugBank` (or `y_source=DrugBank`) and are dropped.

**Therefore the seed layer cannot be made commercial-safe by filtering.** The
DisGeNET-derived `disease_protein` seeds pass any column filter unchanged. The only
correct fix is to **replace the seeds** with a commercial-usable disease→gene
source. This skill uses **Open Targets genetic associations (CC0 1.0)** via
`ot_disease_seeds.py` → `--seeds-file` (see §2.1). The ranker records the
node-vocabulary caveat verbatim in `meta.json → provenance_note` and emits a
runtime **WARNING** if commercial mode is run on PrimeKG (DisGeNET) seeds without a
`--seeds-file`.

## 2. Which sources the ranking actually depends on

The ranker (`rank_kg_targets.py`) consumes only these relations:

| Relation | Node vocab (x/y_source) | **Evidence source** | Role | Commercial-safe by itself |
|---|---|---|---|---|
| `protein_protein` | NCBI | NCBI/curated PPI | gene–gene network backbone | ✅ |
| `bioprocess_protein` | GO | GO | shared-concept edges | ✅ |
| `molfunc_protein` | GO | GO | shared-concept edges | ✅ |
| `pathway_protein` | REACTOME | Reactome | shared-concept edges | ✅ |
| `disease_protein` | NCBI / MONDO | **DisGeNET (non-commercial)** | disease **seeds** | ❌ **evidence is DisGeNET** — must replace seeds |
| `drug_protein` | DrugBank | DrugBank | academic TxGNN drug-target layer | ❌ |
| `indication`, `off-label use` | DrugBank | Drug Central / DrugBank | known-target label + `drug_target` paths | ❌ |
| `contraindication` | DrugBank | Drug Central / DrugBank | in `KEEP` but unused in scoring | ❌ |

**Conclusion:** the RWR network *backbone* (NCBI PPI + GO + Reactome concept edges)
is commercial-safe as-is. **Two things are not:** (i) the **DisGeNET-derived
`disease_protein` seeds**, which are invisible to the edge filter and must be
**replaced** (not filtered) with Open Targets genetics; and (ii) the **DrugBank**
drug layer, which the edge filter *does* remove. Commercial mode does both.

*Measured impact (SLE run):* commercial mode kept ~1.99M node-source occurrences
across NCBI/MONDO/GO/Reactome and dropped **136,568 DrugBank edges**
(`drug_protein` 51,306; `contraindication` 61,350; `indication` 18,776;
`off-label use` 5,136). Independently, the **seeds were replaced**: 100 Open Targets
genetic-association SLE targets (CC0) were used instead of the DisGeNET-derived
PrimeKG seeds (`meta.json → seeds_replaced=true`,
`seeds_provenance="Open Targets genetic association (CC0 1.0)"`).

### 2.1 Commercial-safe seeds — Open Targets genetics

`ot_disease_seeds.py` queries the **Open Targets Platform** GraphQL API for the
disease's `associatedTargets` with **`genetic_association`** datatype evidence and
writes a `--seeds-file` (JSON: `seeds: [[symbol, ensembl], ...]`).

- **License:** Open Targets data is **CC0 1.0** (public domain) — commercial use
  permitted, no attribution required. API is free, no key.
- **Selection:** top-N by genetic-association score (default N=100) or a
  `--min-genetic` score threshold.
- **Matching:** seed symbols are matched to PrimeKG gene nodes by symbol; unmatched
  symbols are dropped (count reported in stderr and `meta.json`).
- *SLE run:* disease `MONDO_0007915`, OT data v2026-06, 100 seeds
  (e.g. TREX1, TYK2, ATRIP, WDFY4, NCF2, IRF5, TNFAIP3, DNASE1L3, BLK, TNIP1).

## 3. Replacement for the DrugBank "known target" label — Open Targets

Commercial mode also rebuilds the "known drug target" label (used only by the
face-validity self-check and figures — **not** by the ranking) from **Open
Targets** via `ot_known_targets.py`:

- **License:** CC0 1.0. **Label definition:** a target is "known" if it has
  **clinical**-datatype association evidence for the disease (drug/clinical
  precedence). This is an **independent** evidence type from the
  genetic-association seeds, so the enrichment check is a genuine (not circular)
  self-consistency test.
- *SLE run:* `MONDO_0007915`, OT data v2026-06, **183** known targets among the
  ranked genes; top-50 enrichment **11.6×** over the 0.86% background.

## 4. `--edge-license` modes

| | **commercial** (default) | **academic** |
|---|---|---|
| Disease→gene **seeds** | **REPLACED** with Open Targets genetics (CC0) via `--seeds-file` | PrimeKG `disease_protein` (**DisGeNET-derived**) |
| Restricted **edges** dropped | DrugBank (drug nodes) | none |
| Graph edges | NCBI PPI + GO/Reactome concepts (+ OT-seeded disease links) | all of the above **+ DrugBank** |
| TxGNN drug-target layer | **disabled** (needs DrugBank `drug_protein`) | enabled (if `--txgnn-pred` given) |
| Known-target label | **Open Targets** clinical (`known_target_ot`, CC0) | DrugBank (`known_drug_target`) |
| Evidence-path templates | direct_seed, ppi_bridge, shared_concept (seed edge = "genetic assoc") | + `drug_target` (seed edge = "disease_protein") |
| Face-validity check label | Open Targets clinical (independent source) | DrugBank (self-consistency) |
| Commercial use | **Safe** (honor CC BY attribution for PrimeKG/GO/MONDO) | **Restricted — `needs_commercial_review`** |

## 5. Residual restrictions & obligations

- **Academic mode** ships DisGeNET-derived seeds **and** DrugBank-derived edges →
  **`needs_commercial_review`**. It is not the default and must not be used for
  commercial deliverables.
- **Commercial mode**: the seed layer is clean *only when `--seeds-file` is
  supplied* with a commercial source. Running commercial mode **without**
  `--seeds-file` falls back to DisGeNET-derived PrimeKG seeds — the ranker emits a
  **WARNING** and this is **not** commercial-safe. Always pair
  `--edge-license commercial` with `ot_disease_seeds.py` output.
- Even when clean, retained sources carry **attribution** obligations:
  **PrimeKG (CC BY 4.0)**, **GO (CC BY 4.0)**, **MONDO (CC BY 4.0)**. Reactome
  (CC0), NCBI (public domain), and Open Targets (CC0) require no attribution.
- The **edge** filter is only as complete as PrimeKG's node-source labels, and by
  design it does **not** and **cannot** clear edge-evidence sources such as
  DisGeNET — that is what seed replacement is for. If you use a different PrimeKG
  build, re-verify the node-source set (`meta.json → provenance`) *and* re-confirm
  the disease→gene evidence source before treating a run as commercial-safe.
- **CTD** is present in PrimeKG and is commercially restricted, but it is **not
  consumed** by the ranker, so it does not affect the commercial ranking.

## 6. How to verify provenance for a given run

`rank_kg_targets.py` writes a `provenance` block and seed-layer fields into
`meta.json`:

```json
"seeds_replaced": true,
"seeds_provenance": "Open Targets genetic association (CC0 1.0)",
"provenance_note": "x_source/y_source are NODE vocabularies, not edge-evidence sources; they catch DrugBank (drug nodes) but NOT DisGeNET/KEGG edge provenance. Commercial safety of the seed layer requires --seeds-file replacement.",
"provenance": {
  "restricted_sources": ["DisGeNET", "DrugBank", "KEGG"],
  "edges_kept_by_source":  { "NCBI": ..., "MONDO": ..., "GO": ..., "REACTOME": ... },
  "edges_dropped_by_source": { "DrugBank": 136568 }
}
```

For a commercial run, assert **both**: (a) `edges_dropped_by_source` removed
DrugBank and `edges_kept_by_source` contains no restricted node source, **and**
(b) `seeds_replaced == true` with a commercial `seeds_provenance`. Passing only (a)
does **not** establish commercial safety of the seeds.
