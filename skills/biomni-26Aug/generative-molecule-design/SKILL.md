---
id: "skill_0008d582238d9a1f65a2322b3bf71728"
name: generative-molecule-design
description: "Use for goal-directed de novo small-molecule generation, lead generation, or scaffold hopping against a target. Runs graph-based generation with configurable activity, drug-likeness, synthesizability, novelty, and makeability objectives, then proposes retrosynthetic routes; activity may use an oracle, QSAR surrogate, or docking."
category: "molecular_design"
visibility: "public"
starting-prompt: "Design de novo small molecules against my target with a multi-objective genetic algorithm and propose retrosynthetic routes."
---

# Generative Small-Molecule Design

## What this skill does

Given a target and an objective, it:
1. assembles seed actives and a novelty reference set,
2. **generates** novel molecules with a goal-directed graph genetic algorithm,
3. **scores** them with a configurable multi-objective function,
4. **filters + selects** the top designs (novelty, drug-likeness, synthesizability, ring sanity),
5. **proposes retrosynthetic routes** for the top designs (with graceful degradation),
6. **reports** everything as a Phylo-branded PDF with an infographic summary.

It is **target-agnostic**. The only target-specific piece is how the *activity*
score is produced; everything downstream is identical.

## When to use / not use

**Use it** for: de novo ideation against a target, scaffold hopping from known
actives, generating novel chemotypes for a benchmark oracle, or producing a
shareable design report.

**Do not use it** for: exhaustive virtual screening of an existing library (this
*generates* molecules), lead *optimization* of one series with tight SAR (a GA is
too diffuse), or any claim of experimental activity — outputs are computational
hypotheses, unsynthesized and untested.

## Environment setup

RDKit is preinstalled. Install the generative-chemistry packages once:
```bash
uv pip install PyTDC aizynthfinder==4.4.1 crem
```
**Provision the retrosynthesis models at setup, not lazily** (see below and
`references/environment.md`). All scripts live in `scripts/`; run from there so
the intra-package imports resolve (`import scoring`, `import graph_ga`, ...).

## Step 0 — Choose the activity backend (the only target-specific decision)

```
Does a TDC oracle exist for the target? (DRD2, GSK3B, JNK3, ...)
├── YES → backend: tdc_oracle   (offline, pretrained; the convenience case)
└── NO  → Do you have labelled actives/inactives (e.g. a ChEMBL export)?
          ├── YES → backend: qsar   (train a quick RandomForest surrogate on ECFP)
          └── NO  → Do you have a target structure + pocket?
                    ├── YES → backend: vina   (docking; slow, most general)
                    └── NO  → gather data first; you cannot score activity yet.
```
A TDC oracle's roster is small — **do not let it define the skill's scope**. The
QSAR and docking backends make any target reachable. See `scoring.py`
(`make_tdc_oracle`, `train_qsar_backend`, `make_vina_backend`) and
`config/schema.md`.

## Step 1 — Seeds and literature grounding

- Collect known actives (SMILES) for the target: seed the GA population **and**
  define the novelty reference set. Sources: ChEMBL (a queryable Biomni database),
  the Broad Drug Repurposing Hub datalake, or user-provided SMILES.
- Use the **`LiteratureSearch` tool** to ground the report: search the target
  biology and the design rationale (e.g. "<target> small molecule inhibitors",
  "goal-directed molecular generation genetic algorithm", "synthetic accessibility
  score"). Keep the returned records — you will pass them into the report's
  References section (see Step 6). Cite them inline in the report narrative with
  `[N]` matching the record indices.

## Step 2 — Build the scoring function

Use `scoring.build_scoring_from_config(cfg, activity_fn)` or a preset:
- **`production` (recommended default):** `geom_mean(activity, QED, low-SA)`. It
  **gates on makeability** so the GA cannot run away toward high-scoring
  unsynthesizable molecules — the classic failure mode.
- **`drd2_benchmark`:** bare `sqrt(activity × QED)` for benchmark comparability
  (no synthesizability term).

**Design rule (important):** every component is transformed to [0,1] first
(`identity`/`sigmoid`/`reverse_sigmoid`/`range`), then aggregated — default
**geometric mean**. Never build a weighted *arithmetic* sum over raw property
values: MW ≈ 350 numerically swamps QED ≈ 0.7. The geometric mean makes each
objective a soft gate (one near-zero component kills the score). This mirrors
REINVENT 4's transform + aggregation design.

## Step 3 — Generate (graph GA)

```python
import scoring as S, graph_ga as GA
activity_fn = S.make_tdc_oracle("DRD2")          # or train_qsar_backend(...)
sf = S.preset_production(activity_fn)
history, all_scored, gen_first = GA.run_ga(
    seed_smiles, sf, pop_size=100, n_generations=20, seed=42)
```
The GA is RDKit-native (BRICS fragment crossover + atom/bond/ring mutations, no
external fragment DB). Guardrails keep it drug-like (heavy atoms 8–50; disconnected
structures rejected). Invalid SMILES get fitness 0. Standard effort (100 × 20) runs
comfortably on the default sandbox; scale up for harder targets.

## Step 4 — Score, filter, select

```python
import select_and_filter as SF
df = SF.score_library(all_scored, gen_first, sf, known_smiles=seed_smiles)
top, counts, work = SF.select_top(df, cfg_select)   # cfg_select from config
```
Cascade: unique/valid → novelty (Tanimoto < 0.4) → activity+QED gates →
PAINS-clean → **ring-sanity** → **SA_Score ≤ 4.5** → top-N. A QED fallback keeps
the pipeline productive if the gate is too strict. `ring_sanity` rejects the
strained bridged/oversized/over-fused polycyclic artifacts a GA can produce
(largest ring > 7, > 2 bridgeheads, or > 1 spiro).

**Two-tier synthesizability:** Tier-1 = SA_Score (cheap, offline, always
computed; used in the objective and as a filter). Tier-2 = full retrosynthesis
(Step 5, heavy, on top-N only).

## Step 5 — Retrosynthesis (Tier-2, heavy, graceful)

**Provision models at setup** (once), into a persistent cache:
```python
from run_retro import provision_models
provision_models("/mnt/shared-workspace/aizynth_models")   # ~750 MB, one time
```
Then run on the top designs:
```python
from run_retro import run_retrosynthesis
retro = run_retrosynthesis(top, out_data_dir, cache_dir="/mnt/shared-workspace/aizynth_models")
```
**Graceful degradation is guaranteed.** If the models are missing or egress is
blocked, `run_retrosynthesis` returns a clean "skipped" summary (never raises),
the pipeline falls back to the Tier-1 SA_Score proxy, and the report says
retrosynthesis was unavailable. Do **not** re-enable a lazy in-request download —
that is the exact failure mode this design avoids. Merge `retro` back onto `top`
by `design_id` before reporting; assign stable IDs like `TARGET-DN-01`.

## Step 6 — Figures + PDF report

Generate QC'd figures with `make_figures.py` (convergence, activity-QED scatter,
property distributions, novelty bars, top-designs structure grid, seed grid, and
route trees). **Run the media_output_check QC (`Read` with
`mode="media_output_check"`) on every figure** and regenerate any that comes back
blank/clipped/tangled — molecule grids are the usual offenders (use the provided
rdCoordGen + single-line-legend renderer).

Build the report with `build_report.build_report(bundle, out_pdf)`. It follows the
**`pdf-report-generation` system skill** (`/mnt/skills/system/pdf-report-generation`)
— read that skill for the brand/layout conventions. The report includes, per spec:
an **infographic** (pipeline funnel + headline metric cards), **Introduction,
Methods, Results, Conclusions, Limitations, Next Steps, and References**. Populate
`bundle["references"]` from the Step-1 `LiteratureSearch` records
(`[{"index": N, "text": "Authors (year). Title. Journal."}]`) and cite them inline
in the narrative. If retro was skipped, set `bundle["retro_ran"]=False` and provide
`retro_skip_reason` — the report renders the fallback note automatically.

Validate the PDF: `pypdf` (page count ≥ 2, size > 5 KB, first-page text
extractable) then `media_output_check` on the whole PDF.

## Honest-reporting caveats (include these in every report)

- The activity score is a **surrogate** (a pretrained oracle, a QSAR model, or a
  docking score) and is **exploitable by a GA** — high scores can be adversarial
  artifacts, not real affinity.
- QED and SA_Score are **heuristics**, not experimental measures.
- Retrosynthetic routes are **USPTO-template proposals**, not validated syntheses.
- All compounds are **computational designs — unsynthesized and untested**.
- Note any target-specific oracle quirks (e.g. for DRD2, olanzapine scores an
  anomalously low 0.124 on the TDC oracle).

## Data sources & licenses

The skill can draw on several external data sources; which ones you touch depends
on the activity backend and seed source. **See `references/DATA_SOURCES.md` for the
full table** (access method, license, commercial-use flag, attribution/share-alike
obligations, and citations). Summary:

| Source | Role | License | Commercial | Obligation |
|---|---|---|---|---|
| **ChEMBL** | bioactivity → QSAR labels + seeds | **CC BY-SA 3.0** | **Yes** | **attribution + share-alike**; cite release version; keep ChEMBL IDs |
| **Broad Drug Repurposing Hub** | decoys for QSAR negatives | **CC BY 4.0** | **Yes\*** | attribution; \*Broad portal restricts commercial *redistribution* of annotations |
| **AiZynthFinder USPTO + ZINC** | Tier-2 retrosynthesis models/stock | MIT (sw); USPTO public-domain; ZINC research-use | **Yes** | cite AiZynthFinder + USPTO/ZINC provenance |
| **Human Protein Atlas** *(optional, not used here)* | tissue/target-specificity context | **CC BY-SA 4.0** | **Yes** | **attribution + share-alike** |
| **PyTDC oracles** *(optional; no BRAF oracle)* | `tdc_oracle` backend | MIT (sw); per-dataset | check dataset | cite TDC + dataset |

**Commercial-use note:** ChEMBL (CC BY-SA 3.0) and the Human Protein Atlas
(CC BY-SA 4.0) permit commercial use but require **attribution *and* share-alike**
(derivatives keep the same license). Record the ChEMBL release version (or datalake
snapshot date) in every report's Methods for reproducibility.

## Files

```
scripts/scoring.py            # transforms, aggregation, backends, property panel, presets
scripts/graph_ga.py           # RDKit-native goal-directed graph GA (caller supplies fitness)
scripts/select_and_filter.py  # property table + filtering cascade + ring_sanity + top-N
scripts/run_retro.py          # AiZynthFinder provision-or-skip retrosynthesis (graceful)
scripts/make_figures.py       # QC'd figures (colorblind, SVG+PNG, rdCoordGen grids)
scripts/build_report.py       # Phylo PDF: infographic + intro/methods/results/refs/next-steps
config/example_drd2.yml       # ready-to-run DRD2 config
config/schema.md              # full config reference
references/worked_example_drd2.md  # the validated DRD2 run + expected numbers
references/environment.md      # versions, install, model provisioning, graceful-degradation rationale
references/DATA_SOURCES.md     # external data sources, licenses, commercial-use & attribution terms
```
