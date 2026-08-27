---
id: "skill_66117a119805611838ba0a0cdde6edb2"
name: knowledge-graph-target-reasoning
description: "Use to discover and rank therapeutic targets for a disease through multi-hop biomedical knowledge-graph reasoning. Applies PrimeKG network propagation/RWR with optional TxGNN drug-target evidence and returns ranked genes/proteins with interpretable evidence paths; triggers on disease target nomination or network-based prioritization."
category: drug_discovery
visibility: public
starting-prompt: "Prioritize therapeutic targets for Parkinson's disease using the knowledge graph, explain the top hits, and generate a PDF report."
---

# Knowledge-Graph Target Reasoning

Rank therapeutic **targets** (genes/proteins) for a disease by propagating disease
seed genes across **PrimeKG** with Random-Walk-with-Restart, folding in an optional
**TxGNN** drug-target evidence layer, explaining every top hit with explicit
multi-hop evidence paths, validating the ranking against known drug targets,
attaching literature support, and rendering a Phylo-branded PDF report.

This skill is **disease-agnostic** — the disease is specified only by its PrimeKG
disease node id(s), which the skill resolves from a plain name.

## Scope

- **Does:** produce a *prioritized, auditable ranking* of candidate targets for a
  disease, with evidence paths, a face-validity check, per-target literature
  support, figures, and a PDF report.
- **Does NOT:** perform statistical inference or causal testing (no p-values/FDR);
  it is discovery/hypothesis-generation. Human targets only. The RWR **network**
  is PrimeKG (+ TxGNN drug-target layer in academic mode); it does not merge STRING
  or other networks into the propagation (those are recommended *follow-up*
  cross-checks). **Commercial mode does use Open Targets** for two license-clean
  layers — genetic-association **seeds** (replacing DisGeNET) and the **clinical
  known-target label** — but not as additional network edges.

## When to use

Target/gene prioritization or discovery for a disease; "what to drug", "which
genes drive <disease>", network/KG-based target ranking, repurposing-informed
target hypotheses.

**When NOT to use / hand off:**
- Association-database lookup for a target–disease pair → **`open-targets`** skill.
- Variant/GWAS-to-gene mapping → genetics/variant-annotation skills.
- Pure literature synthesis → **`literature-review`** skill.
- Compound/chemistry work → **ChEMBL** / pharmacology tools.

## Inputs

| Input | Source | Notes |
|---|---|---|
| PrimeKG CSV | PrimeKG | The knowledge graph. |
| TxGNN prediction pkl | TxGNN | **Academic mode only**; drug-target layer, dict keyed by disease **name**. Ignored in commercial mode (DrugBank-derived). |
| TxGNN name-mapping pkl | TxGNN | Academic mode only; find the TxGNN key for a disease. |
| Open Targets API | `https://api.platform.opentargets.org` (no key) | **Commercial mode**; supplies the CC0 **genetic-association seeds** via `ot_disease_seeds.py` (replacing DisGeNET seeds) **and** the CC0 **clinical known-target label** via `ot_known_targets.py`. |
| Disease seeds | PrimeKG `disease_protein` (academic; **DisGeNET-derived**) *or* Open Targets genetics (commercial; CC0) | Academic uses PrimeKG's DisGeNET seeds; commercial **replaces** them with OT genetics (Step 0b). |
| Disease name(s) | user | One or more; include subtypes/umbrella terms when relevant (e.g. a disease + its subtypes). |

See `references/resources.md` for schemas/compute footprint (runs in ~seconds on the
default machine — **no GPU/HPC needed**) and `references/DATA_SOURCES.md` for
source-by-source licensing and the `--edge-license` modes.

## Outputs (saved to `/mnt/results/<disease>_target_ranking/`)

- `ranked_targets.csv` — all genes ranked, with scores + known/novel/ADME columns
  (commercial runs also add `known_target_ot` + `ot_assoc_score` from Open Targets).
- `meta.json` — run provenance (anchors, seed counts, params, RWR iterations,
  runtime, **`edge_license`, `restricted_sources`, and a `provenance` block of
  kept/dropped edges per relation and source**); consumed by `make_figures.py
  --meta` and the report.
- `ot_seeds.json` — *(commercial)* Open Targets **genetic-association** seeds (CC0)
  used to replace the DisGeNET-derived PrimeKG seeds (disease id, data version,
  seed list).
- `ot_known_targets.json` — *(commercial)* Open Targets **clinical** label
  provenance (disease id, data version, #known targets).
- `evidence_paths.json` — enumerated multi-hop paths for the top targets.
- `enrichment_check.json` — face-validity self-check numbers (records `label_kind`:
  `known_drug_target`, `seed_recovery`, or the OT label column).
- `figures/` — core figures as PNG + editable SVG.
- `report_<disease>_target_prioritization[_commercial].pdf` — the report.

## Clarification questions (ask only what's missing)

1. **Disease + subtypes** — which disease? Any subtypes/related terms to include as
   separate anchors (umbrella terms often appear as multiple PrimeKG nodes)?
2. **Edge license** — **commercial** (default; RWR over commercially-usable sources,
   Open-Targets validation) or **academic** (adds DrugBank + TxGNN drug-target
   layer, `needs_commercial_review`)? Only ask if commercial-use intent is unclear.
3. **How many top hits to explain** with evidence paths + literature (default 10–12).
4. **Report audience** — internal triage vs. shareable deliverable (both use the
   same structure; affects tone/length).

If the user already gave a disease and a clear goal, proceed with sensible defaults
and record assumptions.

## Workflow

All scripts are in `scripts/`. Run them in order; each writes into the output dir.

### Step 0 — Resolve disease name → PrimeKG anchor id(s)
The one manual step, automated. Discover candidate disease nodes **and their seed
counts** (a node with ~0 seeds cannot drive a useful ranking):
```bash
python scripts/find_disease_anchors.py \
    --primekg "${PRIMEKG_CSV}" \
    --name "Parkinson"        # repeat --name for subtypes/related terms
```
Review the printed candidates; pick the id(s) with meaningful seed counts. Merge
several PrimeKG nodes for one clinical entity by underscore-joining ids
(e.g. `"5011_5535"`). **Do not blindly take the first row** — confirm the disease
name matches intent. For the TxGNN layer, find the matching disease-name key in the
TxGNN prediction dict (use `txgnn_name_mapping.pkl`).

> **Anchor-id caveat (underscore collision).** PrimeKG disease node ids can
> themselves contain underscores — they are composite UMLS CUI groupings (e.g. the
> "Parkinson disease" node id is the single string
> `11764_11658_..._13167`). This conflicts with the underscore-join convention for
> merging multiple nodes. The ranker and evidence-path scripts resolve this by
> checking the full raw anchor-id string against the parsed PrimeKG graph first:
> if the full string is itself a known disease node, it is kept verbatim; it is
> only split on `_` when the full string is not a known single node. **Use the
> `find_disease_anchors.py` output verbatim as the `--anchor-id`** — do not
> manually split or reformat ids.

> **Licensing — read before running.** This skill defaults to **commercial** mode,
> which is safe **only when both** of two independent steps are done: (1) it drops
> DrugBank-sourced PrimeKG edges via the `x_source`/`y_source` node-source columns,
> and (2) it **replaces the disease seeds** with **Open Targets genetics (CC0)**
> via `--seeds-file`, because PrimeKG's `disease_protein` seeds are
> **DisGeNET-derived** (non-commercial) and DisGeNET is *invisible* to the
> node-source filter (those columns record node vocabularies, not edge-evidence
> sources — see the critical note below). Commercial mode also disables the TxGNN
> layer (it needs DrugBank edges) and validates with **Open Targets** (CC0). Use
> `--edge-license academic` only for internal/non-commercial work — it keeps the
> DisGeNET seeds + DrugBank + TxGNN and is flagged `needs_commercial_review`. See
> the **Licensing & edge-license modes** section and `references/DATA_SOURCES.md`.
>
> **CRITICAL — node vocabularies ≠ edge-evidence sources.** PrimeKG's
> `x_source`/`y_source` record *the ontology each endpoint node came from*
> (gene=NCBI, disease=MONDO, drug=DrugBank, pathway=Reactome), **not** the database
> that supplied the relationship's evidence. So the edge filter catches **DrugBank**
> (drug nodes) but **cannot** catch **DisGeNET** or **KEGG**, which are
> edge-evidence sources — DisGeNET is PrimeKG's source for the `disease_protein`
> seed associations yet those edges read `NCBI`/`MONDO`. **Filtering alone therefore
> cannot make the seeds commercial-safe; the seeds must be replaced** (Step 0b/1).

### Step 0b — Commercial-safe seeds (Open Targets genetics; commercial mode only)
Because PrimeKG's `disease_protein` seeds are **DisGeNET-derived** (non-commercial)
and cannot be cleared by the edge filter, commercial mode **replaces** them with
**Open Targets genetic-association** targets (CC0 1.0):
```bash
python scripts/ot_disease_seeds.py \
    --disease-name "Parkinson disease" \
    --top-n 100 \
    --out /mnt/results/PD_target_ranking/ot_seeds.json
```
Writes a `--seeds-file` (JSON `seeds: [[symbol, ensembl], ...]`) from OT's
`genetic_association` datatype (top-N by score, or `--min-genetic <score>`). This is
an **independent** evidence type from the OT *clinical* label used in Step 1b/2, so
the downstream enrichment check stays non-circular. (Skip in academic mode — it uses
the PrimeKG DisGeNET seeds.)

### Step 1 — Rank targets
**Commercial (default) — RWR over commercially-usable sources, OT-replaced seeds:**
```bash
python scripts/rank_kg_targets.py \
    --primekg "${PRIMEKG_CSV}" \
    --anchor-id "<id>" --anchor-name PD \
    --edge-license commercial \
    --seeds-file /mnt/results/PD_target_ranking/ot_seeds.json \
    --seeds-source-name "Open Targets genetic association (CC0 1.0)" \
    --out /mnt/results/PD_target_ranking
```
**Academic (internal only) — DisGeNET seeds + DrugBank/TxGNN drug-target layer:**
```bash
python scripts/rank_kg_targets.py \
    --primekg "${PRIMEKG_CSV}" \
    --txgnn-pred "${TXGNN_PREDICTIONS}" \
    --anchor-id "<id>" --anchor-name PD --anchor-tx-key "parkinson disease" \
    --edge-license academic \
    --out /mnt/results/PD_target_ranking
```
`--edge-license` defaults to `commercial`; `--restricted-sources` defaults to
`DrugBank,DisGeNET,KEGG` (note: only DrugBank is catchable via node-source columns;
DisGeNET/KEGG entries document intent but are handled by seed replacement, not
filtering). **In commercial mode always pass `--seeds-file`** — running commercial
mode without it falls back to the DisGeNET-derived PrimeKG seeds and the ranker
emits a WARNING (the run is then *not* commercial-safe). In commercial mode the
TxGNN layer is force-disabled even if `--txgnn-pred` is supplied. Other defaults:
restart 0.30, weights 0.70/0.30, top-50 drugs — all exposed as flags. The script
keeps the **tie-aware `rank_norm`** (average ranks); this is deliberate — see
`references/methods.md` §6. The ranker records `seeds_replaced`/`seeds_provenance`,
a `provenance_note` (node-vocab caveat), and a `provenance` block (kept/dropped
edges per relation and source) in `meta.json`.

### Step 1b — Commercial known-target label (Open Targets; commercial mode only)
In commercial mode the DrugBank `known_drug_target` label is dropped, so build a
commercially-usable label from **Open Targets** (CC0 1.0) for the face-validity
check and figures:
```bash
python scripts/ot_known_targets.py \
    --ranked /mnt/results/PD_target_ranking/ranked_targets.csv \
    --disease-name "Parkinson disease" \
    --meta-out /mnt/results/PD_target_ranking/ot_known_targets.json \
    --skip-on-error
```
Adds a `known_target_ot` column (targets with Open-Targets **clinical**-datatype
evidence) + an `ot_assoc_score` column. `--skip-on-error` leaves the label absent
if OT is unreachable, so Step 2 can fall back to a self-contained seed-recovery
check. (Skip this step in academic mode — use the DrugBank label.)

### Step 2 — Face-validity self-check (REQUIRED)
**Commercial (Open-Targets label, with offline fallback):**
```bash
python scripts/check_enrichment.py --ranked /mnt/results/PD_target_ranking/ranked_targets.csv \
    --label-col known_target_ot --fallback-seed-recovery \
    --out /mnt/results/PD_target_ranking/enrichment_check.json
```
**Academic (DrugBank label):** omit `--label-col`/`--fallback-seed-recovery`
(defaults to `known_drug_target`). Known targets should be **strongly enriched in
the top bin** and enrichment should **decrease** down the list. If the script
prints `FACE-VALIDITY CHECK FAILED`, **stop and re-examine the anchors** (wrong
disease, too few seeds, poor PrimeKG coverage) before reporting. If OT is
unreachable and `--fallback-seed-recovery` is set, the check runs on `is_seed`
(source-free seed recovery) and records `label_kind: seed_recovery`. Report the
numbers either way.

### Step 3 — Enumerate evidence paths for top hits
```bash
python scripts/enumerate_evidence_paths.py \
    --primekg "${PRIMEKG_CSV}" \
    --ranked /mnt/results/PD_target_ranking/ranked_targets.csv \
    --anchor-id "<id>" --anchor-name PD --anchor-disease-name "Parkinson disease" \
    --edge-license commercial \
    --top 12 --out /mnt/results/PD_target_ranking/evidence_paths.json
```
Pass the **same `--edge-license`** as Step 1. In commercial mode the DrugBank
`drug_target` path template is not emitted (only direct_seed / ppi_bridge /
shared_concept).

### Step 4 — Literature validation of top hits (REQUIRED)
For each of the top-N targets, use the Biomni **`LiteratureSearch`** tool to find
support for the target in that disease, e.g. query `"<GENE> <disease>
therapeutic target"` (and mechanism-specific variants). For each target, record
**one grounded supporting sentence + citation(s)**. This feeds the report's
"Literature support" section. **Do not fabricate citations** — only cite returned
sources; if nothing solid is found for a target, say so (a genuinely novel
candidate may have little literature, which is itself informative).

### Step 5 — Figures
```bash
python scripts/make_figures.py \
    --ranked /mnt/results/PD_target_ranking/ranked_targets.csv \
    --evidence /mnt/results/PD_target_ranking/evidence_paths.json \
    --enrichment /mnt/results/PD_target_ranking/enrichment_check.json \
    --meta /mnt/results/PD_target_ranking/meta.json \
    --disease "Parkinson disease" \
    --out /mnt/results/PD_target_ranking/figures \
    --seed-venn --txgnn-fig --hero
```
Produces the **core set** (infographic, top-20 bar, score-distribution +
enrichment-by-bin, evidence-path grid). The conditional flags add a seed-Venn
(auto-skips with <2 anchors), a TxGNN-support bar (auto-skips with no TxGNN), and a
#1-hit "convergence" hero figure. **In commercial mode omit `--txgnn-fig`** (there
is no TxGNN support); the `known`/`novel` coloring uses the `known_target_ot`
column automatically when present. `--meta` is optional: the ranker (Step 1) writes
`meta.json` automatically, so just point `--meta` at it to show real provenance
(graph scale, seed counts, params) on the infographic. If absent, all keys fall
back to values computed from the ranked CSV / enrichment JSON.
**MEDIA-CHECK every figure** (`Read(..., mode="media_output_check")`) and
regenerate any that come back blank, clipped, or unreadable. Legends are placed
below the plot area by design.

### Step 6 — PDF report
Assemble the report and render it with the **`pdf-report-generation` skill**
(load it, follow its Phylo branding, tables, `hAlign="CENTER"`, `KeepTogether`,
`<sub>`/`<super>` not Unicode, and its validation steps). This skill defines the
**structure** (below); the PDF skill owns the **rendering**.

## Report specification (structure — render via `pdf-report-generation`)

Produce `report_<disease>_target_prioritization.pdf` with these sections **in
order**:

1. **Title + summary infographic** (fig1) — one-glance overview.
2. **Introduction** — the disease, why target prioritization matters, and the
   knowledge-graph reasoning approach in 2–3 sentences.
3. **Methods** — data sources (PrimeKG; TxGNN in academic mode; Open Targets
   genetic-association **seeds** + clinical **label** in commercial mode), the
   **`edge_license` mode used** and (commercial) **both** the provenance-based edge
   filter with kept/dropped counts **and** the seed replacement (state that PrimeKG
   `disease_protein` seeds are DisGeNET-derived and were replaced with OT genetics,
   with the node-vocab-vs-edge-evidence rationale), seed definition, RWR (restart),
   the score, and annotation. Pull from `references/methods.md`; state the actual
   parameters used.
4. **Results**
   - Ranked table (top ~25 of N) with combined/RWR/TxGNN, anchors, degree, status.
   - Top-20 ranking figure (fig2).
   - Score-distribution + enrichment-by-bin figure (fig3) with the self-check verdict.
   - Per-target **evidence paths** for the top hits (fig4 + prose "why X ranks high").
   - Conditional figures if generated (seed-Venn, TxGNN-support, hero).
5. **Literature support** — the per-target grounded sentences + citations from Step 4.
6. **Discussion / interpretation** — what the ranking recovers (known biology) and
   the most credible novel candidates; where RWR and TxGNN agree/disagree.
7. **Conclusions** — the headline: a credible, auditable target shortlist.
8. **Limitations** — from `references/methods.md` §10 (discovery not inference; hub
   bias; TxGNN is repurposing; tunable defaults; graph-derived known labels; KG
   coverage).
9. **References** — the literature citations (inline, handled by the platform).
10. **Next steps** — orthogonal validation of top hits (Open Targets, human
    genetics/GWAS, DepMap dependency, `predict_admet_properties` for druggability),
    and experimental follow-up.
11. **Data sources & licensing** *(required for commercial deliverables)* — a table
    of every source that fed the ranking + its license/commercial status, the
    excluded restricted sources, and the residual restriction (see
    `references/DATA_SOURCES.md`).

Always run the PDF skill's validation (pypdf page/text check + visual
media-check) and fix layout issues before delivering.

## Scientific caveats (surface these; never hide them)

- **Discovery, not inference** — a ranking, not a test; no p-values/FDR. Scores are
  rank-normalized and cluster near 1.0 at the top by construction, not by effect size.
- **Degree/hub bias** — hub genes can rank up topologically; mitigated (concept
  down-weighting + size cap + ADME flag) but not eliminated. Watch the `degree`
  column on very high-ranked hits.
- **TxGNN is repurposing, not target inference** *(academic mode only)* — target
  support is indirect (drug→target); some predicted drugs are early-stage/tool
  compounds; it is weighted below RWR. **Disabled in commercial mode** (needs
  DrugBank edges), which is then RWR-only.
- **Tunable defaults** — restart 0.30 and 0.70/0.30 weighting are defaults, not
  optimized; report them and note sensitivity.
- **Seed provenance depends on mode** — **academic** seeds are PrimeKG's
  `disease_protein` edges, whose disease→gene associations are **DisGeNET-derived**
  (non-commercial); **commercial** replaces them with **Open Targets genetic
  associations** (CC0). The node-source columns cannot distinguish these (both read
  NCBI/MONDO), which is exactly why commercial mode replaces rather than filters the
  seeds.
- **"Known target" label depends on mode** — in **academic** mode it is
  graph-derived from PrimeKG (DrugBank) indication/off-label edges, so the
  enrichment check is a *self-consistency* check (same source defines ranking edges
  and label). In **commercial** mode the label comes from **Open Targets clinical**
  evidence (CC0), which is **independent** of both the network backbone and the OT
  *genetic* seeds, making the check a genuine external validation. Either way,
  "known" is a moving annotation that may lag clinical practice.
- **KG coverage** — results inherit PrimeKG's gaps; absence of an edge ≠ absence of
  biology.

## Licensing & edge-license modes

PrimeKG integrates ~20 upstream resources, some **not licensed for commercial
use**. Rather than shipping restricted-source edges silently, this skill is
**provenance-aware** and exposes `--edge-license`. Full source-by-source licensing
is in **`references/DATA_SOURCES.md`** — read it before any commercial use.

**What actually feeds the ranking** (with the important node-vocab-vs-evidence
distinction): the RWR **network backbone** uses **NCBI** (PPI) and **GO** +
**Reactome** (concept edges) — all commercial-usable. Two things are **not** clean
as-shipped: (1) the **disease seeds** — PrimeKG's `disease_protein` edges read
`NCBI`/`MONDO` in the node-source columns but their disease→gene *associations* are
curated from **DisGeNET** (CC BY-NC-SA, **non-commercial**), which is therefore
invisible to the edge filter and must be **replaced** with Open Targets genetics
(CC0), not filtered; and (2) **DrugBank**, which supplies the drug nodes and *is*
caught by the edge filter (`drug_protein` / `indication` / `off-label use` — the
TxGNN layer + the DrugBank "known target" label). **KEGG** is not a node-source
string in this build (pathways are Reactome) and, like DisGeNET, could not be caught
by column filtering anyway; both remain in the default restricted list to document
intent. **Net: commercial safety requires seed replacement (Step 0b) AND the
DrugBank edge drop — filtering alone is insufficient.**

| Mode | Seeds | Edges | TxGNN | Known-target label | Commercial |
|---|---|---|---|---|---|
| **`commercial`** (default) | **Open Targets genetics (CC0)** — DisGeNET seeds replaced | NCBI PPI + GO/Reactome concepts; **DrugBank dropped** | disabled | **Open Targets** clinical (CC0) | **Safe** — attribute PrimeKG/GO/MONDO (CC BY 4.0) |
| **`academic`** | PrimeKG `disease_protein` (**DisGeNET-derived**) | all of the above **+ DrugBank** | enabled | DrugBank (indication/off-label) | **Restricted — `needs_commercial_review`** |

- **`commercial` (default)** is safe for commercial deployment **only when the
  seeds are replaced (Step 0b, `--seeds-file`)** *and* the DrugBank edges are
  dropped, and provided the **attribution** terms of **PrimeKG (CC BY 4.0)**, **GO
  (CC BY 4.0)**, and **MONDO (CC BY 4.0)** are honored (Reactome, NCBI, Open Targets
  are CC0 / public domain and need no attribution). To treat a run as
  commercial-safe, assert **both** (a) no restricted source in
  `meta.json → provenance.edges_kept_by_source` (DrugBank dropped), **and**
  (b) `meta.json → seeds_replaced == true` with a commercial `seeds_provenance`.
  Passing only (a) is **not** sufficient — the DisGeNET-derived seeds are invisible
  to the edge filter.
- **`academic`** keeps the DisGeNET-derived seeds **and** DrugBank-derived edges
  (and, when supplied, the TxGNN layer) and is therefore **`needs_commercial_review`**
  — use it only for internal / non-commercial work. Do **not** use academic-mode
  outputs in a commercial deliverable.
- **PrimeKG dataset license** — the published Nature Scientific Data dataset
  (Chandak, Huang, Zitnik 2023) is **CC BY 4.0**; the bioRxiv *preprint* carries
  CC BY-NC 4.0, but that is the preprint license, not the dataset license — do
  **not** label the dataset non-commercial based on the preprint.
- **TxGNN** — code is MIT-licensed; the precomputed prediction pickle is derived
  from PrimeKG (incl. DrugBank) and is only used in academic mode.
- **Residual restriction** — the **edge** filter is only as accurate as PrimeKG's
  `x_source`/`y_source` node-source labels, and by design it does **not** clear
  edge-evidence sources (DisGeNET/KEGG) — that is what **seed replacement** handles;
  re-verify both the node-source set *and* the disease→gene evidence source on a
  different PrimeKG build. CTD (present in PrimeKG, commercially restricted) is **not
  consumed** by the ranker, so it does not affect commercial runs.

## Related Biomni resources

- **Resources:** PrimeKG is required and TxGNN is optional (see
  `references/resources.md`).
- **Tools:** `LiteratureSearch` (top-hit validation),
  `predict_admet_properties` (druggability triage; verify by import before use).
- **Skills:** `pdf-report-generation` (required — report rendering); `open-targets`
  (recommended orthogonal validation); `literature-review` (deeper synthesis).
