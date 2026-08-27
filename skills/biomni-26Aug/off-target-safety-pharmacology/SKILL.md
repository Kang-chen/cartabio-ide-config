---
id: "skill_8c4bbb8b8155ca559167852f77ba44d1"
name: "off-target-safety-pharmacology"
description: "Use to predict and benchmark off-target or secondary-pharmacology liabilities for a small molecule supplied by name, SMILES, InChIKey, or ChEMBL ID. Builds a Bowes-style safety panel, combines orthogonal predictors with ADMET, separates the intended target from off-targets, and gates validation claims against measured ChEMBL data sufficiency."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Profile the off-target and secondary-pharmacology liability of atorvastatin (ChEMBL CHEMBL1487): resolve the compound, build the Bowes-style core safety panel with adaptive kNN expansion, and run two orthogonal off-target predictors (ECFP4 ligand similarity and a DeepPurpose DTI model) plus ADMET. Benchmark the predictions against atorvastatin's own measured ChEMBL bioactivity using the data-sufficiency tier gate, separating the intended primary target (HMG-CoA reductase) from genuine off-targets, and assemble the final PDF liability report."
---

# Off-Target & Secondary-Pharmacology Liability Profiling

Take a single small molecule and produce a defensible off-target / secondary-pharmacology
liability profile: what else is this molecule likely to hit, how bad is the cardiac (hERG)
and ADMET picture, and — critically — **how much can we trust the prediction given the
measured data that actually exist for this compound**.

The design principle is **honest benchmarking**. It is easy to make an off-target predictor
look good by scoring a compound against targets it is already known to hit. This skill refuses
to do that: it uses leave-query-out, keeps the compound's own measured data strictly separate
from the prediction panel, **separates the intended primary target from genuine off-targets**,
and gates the headline "validation" claim behind a data-sufficiency tier so a de novo prediction
is never dressed up as validated.

## Scope

**Does:** resolve any small-molecule identifier; resolve its intended primary target from ChEMBL
mechanism annotations; build a curated core safety panel (Bowes-style) with optional nearest-neighbor
(kNN) expansion; run two orthogonal off-target predictors (ECFP4 ligand similarity + a DeepPurpose
DTI model) plus ADMET; benchmark predictions against the compound's own measured ChEMBL bioactivity
with a data-sufficiency tier gate; and assemble a final PDF liability report. The final PDF is a
**required terminal step**, not optional.

**Does NOT:** predict primary-target potency for a known target (this is about *off*-targets);
provide experimentally measured affinities (every prediction here is a hypothesis); or certify
toxicological safety (a good ROC-AUC recovers *known* actives among panel targets — it does not
prove the absence of untested liabilities).

**Use it when:** "is this compound selective / what are its off-targets / does it have a hERG
problem?"; secondary-pharmacology / safety-panel triage of a hit or lead; comparing analog liability
profiles. It works both for compounds **with rich measured data** (validated benchmark) and for
**novel compounds** with little/no data (de novo discovery mode) — the tier system adapts.

## Inputs

- **Compound identifier (required):** name, SMILES, InChIKey, or ChEMBL ID.
- **`--adaptive` (optional, recommended for novel chemotypes):** enable kNN panel expansion.
- **`--admet-engine auto|admet_ai|biomni` (optional):** ADMET backend; `auto` prefers ADMET-AI and
  falls back to the Biomni MPNN.
- **Bundled reference assets:** `references/core_panel.csv` (32 curated safety targets) and
  `assets/chembl_approved_reference.csv` (ChEMBL approved-drug percentile reference for ADMET-AI).

## Outputs

- `<outdir>/data/*.csv|*.json` — intermediates (compound + primary targets, panel, per-engine
  predictions, ground truth, consensus with per-engine scores + 4-state agreement, benchmark summary).
- `<outdir>/figures/*.png|*.svg` — three data figures (per-engine off-target ranking, ADMET flags,
  tier-appropriate benchmark) plus `figure_qc.json`, and an optional `infographic.png`.
- `<outdir>/<compound>_liability_report.pdf` — Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

## Pipeline overview

```
resolve_compound.py  ->  build_panel.py  ->  [run_admet.py]
   (+primary target)     (+primary rows,      predict_similarity.py
                          -primary from        run_deeppurpose.py
                          adaptive)
fetch_ground_truth.py ->  benchmark.py   ->  consolidate.py -> make_figures.py -> report
   (flags primary)        (off vs on-target,   (report_data.json)                  (REQUIRED PDF via
                           splits, agreement)                                        pdf-report-generation)
```

All scripts live under `scripts/`. They pass data through a single working `--outdir`
(`<outdir>/data/*`, `<outdir>/figures/*`) and each prints a one-line JSON status. `compound.json`
(from step 1) is threaded into `build_panel.py`, `fetch_ground_truth.py` and `benchmark.py` so the
intended primary target is handled consistently.

**One-shot driver:** `scripts/run_pipeline.py --query "<compound>" --outdir <dir>
[--adaptive] [--admet-engine auto|admet_ai|biomni]` chains every analysis stage in order
(resolve → panel → ADMET → predictors → ground truth → benchmark → consolidate → figures), leaving
`<dir>/data/report_data.json` and `<dir>/figures/*` ready for the report. The **report is the
mandatory terminal step** and is produced through the `pdf-report-generation` skill (the infographic
comes from the GenerateImage tool — generate it and drop it at `<dir>/figures/infographic.png`; the
report degrades gracefully without it). Run stages individually (below) when you need to inspect or
tune a step.

### 0. Environment
Preinstalled: `rdkit`, `DeepPurpose`, `scikit-learn`, `pandas`, `requests`, `reportlab`, `pypdf`,
`pdfplumber`, `Pillow`. ADMET-AI (`admet_ai`) is **often not installed** and its install can fail
behind restricted egress — the ADMET step has a load-bearing fallback. No GPU needed.

## Workflow (each step, and why it matters)

### 1. Resolve the compound — `resolve_compound.py`
```
python scripts/resolve_compound.py --query "<name|SMILES|InChIKey|CHEMBLxxxx>" --outdir <dir>
```
Resolves any of four input types via ChEMBL, standardizes with RDKit, and — **new** — queries the
ChEMBL *mechanism* endpoint to record the compound's **intended primary target(s)** (UniProt +
pref_name + action type). **Why it matters:** you cannot separate on-target from off-target unless
you know the intended target; recovering the primary target is a sanity check, not off-target
performance. Capture stdout to `<dir>/compound.json` (the driver does this).

### 2. Build the prediction panel — `build_panel.py`
```
python scripts/build_panel.py --smiles "<canonical>" --inchikey14 <first-14> \
       --core references/core_panel.csv --outdir <dir> --compound-json <dir>/compound.json \
       [--adaptive --knn 25 --max-added 25]
```
- **Core panel (always):** 32 curated safety targets (Bowes-style) — ion channels (incl.
  hERG/KCNH2), aminergic GPCRs, transporters, enzymes. **Why:** these are the targets most linked to
  adverse events, so they are the right denominator for a safety triage.
- **Adaptive expansion (`--adaptive`):** adds novel single-protein targets that the query's ChEMBL
  neighbors are active against. **Why:** it extends coverage to a novel chemotype using zero measured
  data for the query. **Caveat (see below):** it is *circular* with the similarity engine, so adaptive
  additions are tagged `source="adaptive"` and counted separately from core.
- **Primary target handling (new):** the intended primary target and its cross-species orthologs are
  **excluded from the off-target panel** (never added by adaptive) and added back as an explicit
  `source="primary"` on-target row so they can be scored for the sanity check but kept out of every
  off-target count/metric.

### 3. ADMET — `run_admet.py` (engine-stamped)
```
python scripts/run_admet.py --smiles "<smi>" --outdir <dir> [--engine auto|admet_ai|biomni]
```
Engine recorded in `admet_meta.json` as `engine` + `has_percentiles`: **ADMET-AI** (preferred; ~100+
endpoints with ChEMBL approved-drug percentiles and hERG) or the **Biomni MPNN fallback** (~16
endpoints, **no hERG, no percentiles**). **Why it matters:** cardiac (hERG) and DMPK liabilities are
first-order safety questions. **The reporting layer branches on `has_percentiles`** — never describe
percentiles or a hERG value the engine did not produce.

### 4. Off-target predictors (two orthogonal engines)
**(a) Ligand similarity — `predict_similarity.py` (PRIMARY).** Max ECFP4 (r=2, 2048-bit) Tanimoto of
the query vs each target's ChEMBL actives (pChEMBL ≥ 6), mapped to probability by a logistic
(P = 1/(1+exp(−k(Tc−t0))), k=12, t0=0.35). **Leave-query-out** removes the query from every active set
by canonical SMILES AND InChIKey-14. **Why:** a molecule tends to hit targets whose known ligands it
resembles; leave-query-out is what makes a benchmark on a well-studied compound honest.
```
python scripts/predict_similarity.py --smiles "<canonical>" --inchikey14 <14> \
       --panel <dir>/data/prediction_panel.csv --outdir <dir> [--min-actives 5]
```
**(b) Deep-learning DTI — `run_deeppurpose.py` (SECONDARY).** Pretrained `morgan_cnn_bindingdb`,
CPU-only, sequence-based so it cannot leak the query. **Why:** an orthogonal vote; agreement between
the two engines is more trustworthy than either alone. Its **absolute nM values are not calibrated** —
use for ranking/voting only.
```
python scripts/run_deeppurpose.py --smiles "<smi>" --panel-seqs <dir>/tmp/panel_with_seqs.csv \
       --outdir <dir>
```

### 5. Ground truth — `fetch_ground_truth.py`
```
python scripts/fetch_ground_truth.py --chembl-id <CHEMBLxxxx> --outdir <dir> \
       --compound-json <dir>/compound.json
```
Pulls the query's OWN measured activities (potency assays with pChEMBL; per-target median; active ≤ 1
µM, potent ≤ 100 nM) and flags rows that are the primary target/orthologs. **Why:** the compound's own
measured data are the only real validation signal — and are kept STRICTLY separate from panel
construction (feeding them back would be circular and impossible for a novel compound).

### 6. Benchmark, agreement & tier gate — `benchmark.py` (THE HONESTY GATE)
```
python scripts/benchmark.py --sim <dir>/data/offtarget_similarity_predictions.csv \
       --dp <dir>/data/offtarget_deeppurpose_predictions.csv \
       --truth <dir>/data/known_targets_collapsed.csv --outdir <dir> \
       --compound-json <dir>/compound.json
```
- **Consensus & agreement:** consensus = mean(P_sim, min-max-normalized DeepPurpose). Because those
  two are **not on a comparable scale**, the consensus is kept only for continuity; each target also
  gets a 4-state `agreement` — **Both / Similarity only / DTI only / Neither** — which is what the
  report ranks and groups on.
- **Similarity-hit split:** off-target similarity hits are reported as **core-panel** (the
  independent-evidence count) vs **adaptive-panel** (high by construction).
- **Primary excluded:** the tier gate and headline ROC-AUC/AP are computed on **off-target** targets
  only. The primary target's recovery is reported separately as an on-target control. If the primary
  is unresolved, a **labeled proxy sensitivity** (excluding the single most-potent measured target) is
  also reported, so the benchmark is shown both ways.
- **Tier gate (off-target measured pChEMBL):** **Tier A** ≥ 15 measured AND ≥ 5 positives → ROC-AUC +
  AP as validation; **Tier B** some overlap below the gate → descriptive, NOT validation; **Tier C**
  little/no overlap → discovery-only, unvalidated. **Why:** this is the mechanism that stops a de novo
  prediction from being dressed up as validated.

### 7. Consolidate results & render figures — `consolidate.py` → `make_figures.py` → infographic
```
python scripts/consolidate.py --outdir <dir> --compound-json <dir>/compound.json \
       --references references/references.json
python scripts/make_figures.py --outdir <dir> --compound "<NAME>" --topn 15
# ---> generate the infographic with the GenerateImage tool -> <dir>/figures/infographic.png
```
`consolidate.py` gathers every upstream output into one `<dir>/data/report_data.json` (compound
block, ADMET engine/flags, benchmark tier/metrics, ground-truth counts, top off-target predictions
with per-engine scores + agreement, and the reference list) — the single data feed the report is
built from. **Why it matters:** one validated data object keeps every number in the report tied to
the same run, so nothing is remembered or hand-typed.

`make_figures.py` produces three data plots (all engine/tier-aware): the off-target ranking shows
**per-engine scores + agreement** (never consensus alone) and marks adaptive targets; the ADMET flags
plot is percentile- or probability-based per the engine; the benchmark panel is a ROC (Tier A),
predicted-vs-measured scatter with **ortholog pairs collapsed to one point** (Tier B), or a de-novo
ranking (Tier C). It also writes `figure_qc.json` and fails on a blank/degenerate figure. **Why it
matters:** the figures are the primary evidence a reviewer reads; the QC gate stops a blank or
degenerate plot from reaching the report.

**Infographic:** the workflow schematic is a *conceptual* figure — generate it with the `GenerateImage`
tool (NOT matplotlib) and save it to `<dir>/figures/infographic.png`. A validated example prompt is in
`references/infographic_prompt.txt`; an example rendering is `references/example_infographic.png`.
**Why it matters:** a one-glance workflow schematic orients the reader before the data figures.

### 8. Generate the liability report — REQUIRED terminal step (the run is not complete until this is done)
Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

Build it from `<dir>/data/report_data.json` (the single consolidated data feed), the figures in
`<dir>/figures/`, and `references/references.json`, then write
`<dir>/<compound>_liability_report.pdf`. **Why it matters:** the report is the deliverable a safety
reviewer acts on. Run the `report_qc` gates so a defect is caught, not shipped —
`validate_report_data` before building (the similarity-hit count must carry its core/adaptive split;
engine agreement and primary-target handling must be present) and `validate_pdf` after (no raw-float
precision, no near-blank page, minimum page count) — then a visual media-output check, regenerating
if anything is tofu/clipped. Every honesty element to keep is in **Scientific caveats** below.

## Scientific caveats (keep these in the report)

- **Adaptive-expansion circularity (standing limitation).** kNN expansion adds a target *because*
  chemically similar molecules hit it; the ligand-similarity engine then scores that same target near
  P = 1.0 **by construction, not by independent evidence**. Never report a single headline "similarity
  hits" number: split it into **core-panel** (independent) and **adaptive-panel** counts, and treat
  adaptive hits as hypotheses, not corroboration. Adaptive targets are tagged everywhere they appear.
- **The two engines are not directly comparable (standing limitation).** The consensus averages a
  logistic-calibrated similarity *probability* with a *min-max-normalized* DeepPurpose affinity — two
  different scales — so the consensus can rank a target the engines disagree about beside one they
  agree on. Always show per-engine values and the 4-state agreement, and weight dual-engine ("Both")
  support above single-engine support.
- **The primary target is not an off-target.** The intended primary target (from ChEMBL mechanism)
  and its cross-species orthologs are excluded from the off-target panel and from the benchmark's
  positive set; recovering the primary target is reported separately as a sanity check. If the primary
  cannot be resolved confidently, the benchmark is reported **both ways** (as-is, and excluding the
  most-potent measured target as a labeled proxy) and the residual on-target-contamination risk is
  stated.
- **ChEMBL is a living database.** Leave-query-out similarity metrics drift modestly between runs as
  new actives are deposited. Report the value computed **from the same data as the figures**; do not
  hardcode a remembered number.
- **Engine matters.** ADMET-AI and the Biomni fallback return different endpoint sets; the fallback
  has no percentiles and no hERG. Read `admet_meta.json` and describe only what the engine that ran
  produced.
- **Non-human orthologs.** ChEMBL's neighbor/active pools include rat/other orthologs, producing
  near-duplicate targets (same pref_name, different UniProt). Distinct-protein counts are by UniProt
  accession; ortholog pairs are collapsed by pref_name in the benchmark scatter.
- **DeepPurpose affinities are uncalibrated** — ranking/consensus only, never quoted as nM.
- **A high ROC-AUC ≠ safety.** It measures recovery of *known* actives among panel targets; it does
  not certify absence of untested liabilities.

## Optional gated stage (off by default): structure-level plausibility
For the top 1–3 dual-engine off-targets you *may* co-fold the query with the target (Boltz-2 / Chai-1
via HPC). Frame it strictly as **pose-level plausibility, NOT a second affinity predictor** (hERG in
particular is hard for structure-based methods). Do not gate the liability call on it.

## Reference values (astemizole, CHEMBL296419 — self-check any change)
- Resolve: MW 458.58, LogP 5.35, TPSA 42.32, max_phase 4, withdrawn, InChIKey
  GXDALQBWZGODGZ-UHFFFAOYSA-N.
- Ground truth: 899 activity records, 297 with pChEMBL, **84 distinct proteins, 21 active ≤ 1 µM, 7
  potent ≤ 100 nM**. Top: H1 (~4–7 nM), hERG/KCNH2 (~9 nM), alpha-1A (~15 nM), 5-HT2B (~36 nM),
  5-HT2A (~55 nM), Sigma-2 (~95 nM).
- Similarity: hERG max Tc ~0.79 → P ~0.99; H1 P ~0.98; MAO-A correctly negative.
- Benchmark: Tier A; off-target similarity ROC-AUC ~0.79 (consensus lower). Dual-engine hits are the
  aminergic set (H1, SERT, 5-HT1D/2A, D2/D3, NET, mu-opioid). Astemizole has no annotated small-molecule
  primary target of its own class in this panel, so off-target and naive numbers are similar.
- ADMET-AI (if available): hERG prob ~0.995, ~99.75th percentile. Fallback: 16 endpoints, no
  hERG/percentiles.
- End-to-end also verified on terfenadine (Tier A), imatinib (Tier B — data-rich but no panel overlap,
  validation correctly refused), sotorasib (Tier C — de novo). See `references/e2e_validation.json`
  (those runs are core-panel only; the core/adaptive split applies when `--adaptive` is used).

## Data sources & licenses
- **ADMET-AI percentiles** use a bundled **ChEMBL approved-drug reference**
  (`assets/chembl_approved_reference.csv`, CC BY-SA 3.0), **not** ADMET-AI's default DrugBank reference
  (CC BY-NC). Columns are renamed `*_chembl_approved_percentile`; if the reference is missing,
  percentiles are disabled. Regenerate with `scripts/build_reference_set.py`.
- **ChEMBL** data are CC BY-SA 3.0 (attribution + share-alike).
