---
id: "skill_b1cf4aefacef741d340cf8a7d522ab0b"
name: "large-scale-virtual-screening"
description: "Use for structure-based docking or virtual screening of compound libraries against a protein structure with AutoDock Vina. Covers receptor/library preparation, native-ligand redocking, hit ranking, scaffold clustering, prospective screens, and actives-vs-decoys benchmarks such as ROC-AUC, EF, and BEDROC."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Dock this compound library against my target and prioritize hits..."
---

# Large-Scale Virtual Screening (structure-based)

Dock a compound library against a protein target with **AutoDock Vina**, validate the
pose, rank and triage hits, summarize preliminary SAR, and write a **Phylo-branded PDF**.
The workflow adapts to two situations automatically and scales from a few hundred to
tens of thousands of compounds.

## Scope

**Does:** receptor prep + box definition, native-ligand redock validation (pose sanity),
ligand 3D prep (RDKit + Meeko), parallel Vina docking, enrichment benchmarking when
activity labels exist (ROC-AUC, EF, BEDROC), hit triage, Bemis-Murcko / Butina scaffold
SAR, optional advisory ADMET, optional bounded literature context, and a PDF report.

**Does NOT:** free-energy perturbation / MM-PBSA absolute affinities, covalent docking,
metalloprotein-specific parameterization, ternary-complex / PROTAC modeling, or molecular
dynamics. **Docking prioritizes compounds for follow-up; it does not confirm binding or
potency.** Vina scores carry ~2-3 kcal/mol error and are unreliable as absolute affinities.

## Inputs

- **Target** — a PDB id (fetched from RCSB) or a local `.pdb`. A co-crystal ligand is
  strongly preferred: it defines the box and enables redock validation.
- **Library** — auto-detected (see Decision Guide). One of:
  - user **SMILES/CSV** (first-class, prospective)
  - **ChEMBL** target-pull of bioactives (first-class, prospective)
  - **DUD-E**-style actives/decoys (labeled benchmark branch)
  - **Enamine REAL / datalake** subset (heavier at-scale mode; degrades gracefully)
- **Optional** — activity labels (a `label`/`activity` column), a box override, an
  infographic, literature toggle, ADMET toggle.

## Outputs (under a run dir, e.g. `/mnt/results/<run>/`)

- `report_<name>.pdf` — Phylo-branded report (infographic + Executive Summary, Background &
  Context [walled-off literature], Methods, Results, Discussion, Limitations, Next Steps,
  References, Data Files).
- Data: `master_library.csv`, `all_scores_merged.csv`, `molecular_descriptors.csv`,
  `redock_validation.json`, `docking_box.json`, `tables/top_hits.csv`,
  `tables/scaffold_clusters.csv`, and — labeled runs only — `enrichment_metrics.json`,
  `roc_curve.csv`. Figures in `figures/`.

## Decision Guide

- **Labeled vs label-free (automatic).** `build_library.py` writes `library_meta.json`
  with `labeled`. If `labeled=true` (DUD-E, or a CSV/ChEMBL benchmark with decoys), run
  `enrichment.py` (ROC/EF/BEDROC) plus triage + SAR. If `labeled=false`, **skip enrichment**
  and the report states it was omitted because there is no ground truth; deliver triage +
  SAR + property/ADMET flags.
- **Fan-out vs local (automatic).** Small library or no `ManageMachine` access → dock on one
  machine (`run_docking.py --mode local`). Larger library → pilot, estimate, confirm, then
  fan out (see Compute & Scaling). Local is a graceful fallback, never a user choice.
- **ADMET / Literature (optional).** Run for top hits when the user wants drug-likeness or
  background; skip for pure at-scale compute to stay fast.

## Environment setup (once per run)

```bash
# Install the Python bindings INTO the interpreter the scripts actually run under.
# The base image ships a `/workspace/.venv` whose `python` can be a broken symlink,
# so a bare `uv pip install` may land packages where imports won't find them. Pin the
# target to the conda interpreter and invoke every script with that same interpreter.
uv pip install --python /opt/conda/bin/python vina meeko gemmi
# meeko + gemmi + the vina Python module are NOT in the base image.
# receptor prep also needs ADFR `prepare_receptor` on PATH (/opt/conda/bin:/usr/local/bin);
# it falls back to Meeko/OpenBabel if absent.
export PATH=/opt/conda/bin:/usr/local/bin:$PATH   # so vina / prepare_receptor / obabel resolve
```

Scripts live in `scripts/`. **Invoke them with `/opt/conda/bin/python <script>.py` (not a
bare `python`)** so the imports resolve to the environment you just installed into. Every
script has a `--self-check` dry-run (no heavy compute) — run them first to confirm the
environment before a real screen. (The `python ...` in the workflow steps below is shorthand
for `/opt/conda/bin/python ...`.)

## Workflow

Run from `scripts/`. Paths below assume a run dir `RUN=/mnt/results/<run>`.

1. **Fetch receptor & isolate protein/native ligand** — *why: a clean single-chain
   receptor and the co-crystal ligand anchor both the box and the pose check.*
   ```bash
   python fetch_receptor.py --pdb-id 2ITY --outdir $RUN/receptor
   # or --pdb-file my_receptor.pdb ; --chain / --ligand-resname to override picks
   ```

2. **Prepare receptor PDBQT + define box** — *why: Vina needs PDBQT; a pocket-scale box
   (~16-22 A cube around the native ligand) balances accuracy and speed.*
   ```bash
   python prepare_receptor.py --protein-pdb $RUN/receptor/2ITY_protein.pdb \
       --from-ligand $RUN/receptor/2ITY_ligand.pdb --outdir $RUN/receptor
   # or --center X Y Z --size S for an explicit box
   ```

3. **Validate the box by native-ligand redock** — *why: this is the single most
   failure-prone step; if the box/scoring can't reproduce a known pose, the whole screen is
   suspect.* Uses a **core-aware, two-tier, warn-not-fail** gate: passes if the rigid,
   buried core RMSD < 2 A even when a flexible tail moves; the exact core atoms are recorded.
   ```bash
   python redock_validate.py --receptor-pdbqt $RUN/receptor/2ITY.pdbqt \
       --receptor-pdb $RUN/receptor/2ITY_protein.pdb \
       --ligand-pdb $RUN/receptor/2ITY_ligand.pdb --box $RUN/receptor/docking_box.json \
       --outdir $RUN/redock
   ```
   **If it does not pass, do not silently proceed** — surface the warning to the user, put it
   in the report's Limitations, and consider re-checking protonation/box/chain.

4. **Build the library (+ labels)** — *why: auto-detection routes to the right branch and
   records provenance.*
   ```bash
   python build_library.py --smiles-csv my_compounds.csv --outdir $RUN/library
   # or --chembl-target CHEMBL203  |  --dude-dir dude/egfr  |  --datalake enamine_subset.smi
   ```

5. **Prepare ligands (3D + PDBQT)** — *why: Vina needs 3D PDBQT; a fixed seed makes it
   reproducible. Target ≥95% prep success; failures are logged and auditable.*
   ```bash
   python prepare_ligands.py --library $RUN/library/master_library.csv \
       --outdir $RUN/ligands --nproc 8
   ```

6. **Dock — pilot, plan, confirm, then execute** — *why: measure real throughput before
   committing a fleet.* See **Compute & Scaling**.
   ```bash
   # (a) measure + plan
   python run_docking.py --mode plan --library $RUN/library/master_library.csv \
       --pdbqt-dir $RUN/ligands/pdbqt --receptor-pdbqt $RUN/receptor/2ITY.pdbqt \
       --box $RUN/receptor/docking_box.json --outdir $RUN
   #   -> report ETA/worker-hours from fanout_plan.json to the user; get an OK if large.
   # (b1) small / no ManageMachine -> local fallback:
   python run_docking.py --mode local --library ... --pdbqt-dir ... --receptor-pdbqt ... \
       --box ... --scores-dir $RUN/scores
   # (b2) fan-out: provision N workers with ManageMachine, run the shard template from the
   #      plan on each (dock_worker.py --shard i --nshards N), then:
   python run_docking.py --mode collect --library $RUN/library/master_library.csv \
       --scores-dir $RUN/scores --outdir $RUN
   ```

7. **Enrichment (labeled runs only)** — *why: quantifies whether docking separates known
   actives from decoys.*
   ```bash
   python enrichment.py --scores $RUN/all_scores_merged.csv --outdir $RUN
   ```

8. **Triage + preliminary SAR** — *why: turns a score list into a prioritized, scaffold-aware
   hit set and flags scoring-function property bias.*
   ```bash
   python triage_sar.py --scores $RUN/all_scores_merged.csv --outdir $RUN --top-n 50
   ```

9. **ADMET advisory (optional)** — *why: drug-likeness/tox context for the shortlist —
   advisory only, never a filter.*
   ```bash
   python admet_annotate.py --top $RUN/tables/top_hits.csv
   ```

10. **Figures** — *why: reviewers read plots first.* Data plots only; make the workflow
    **infographic** separately with `GenerateImage` (conceptual diagram, not matplotlib).
    ```bash
    python make_figures.py --run $RUN --outdir $RUN/figures
    ```

11. **Literature context (optional, walled-off)** — *why: cited background without
    contaminating computed results.* Use the Biomni `LiteratureSearch` tool for a few
    targeted queries (target biology; known inhibitor chemotypes; docking/benchmark caveats),
    then write `$RUN/literature.json`:
    ```json
    {"summary": "<2-4 sentence cited background with [1],[2] markers>",
     "references": [{"n": 1, "text": "Author et al., Journal, Year."}]}
    ```
    Keep this **separate** from the numeric results; the report renders it under a callout
    that labels it external context. Skip entirely for pure-compute runs.

12. **Report** — *why: the standalone deliverable.*
    ```bash
    python build_report.py --run $RUN --title "EGFR virtual screen" \
        --subtitle "DUD-E benchmark vs PDB 2ITY" \
        --infographic $RUN/figures/workflow_infographic.png \
        --literature-json $RUN/literature.json \
        --out $RUN/report_virtual_screen.pdf
    ```
    The script validates the PDF (pypdf: ≥2 pages, extractable text). **Also run the
    `Read` media-output-check** on the PDF/figures; regenerate anything blank or clipped.

## Compute & Scaling (adaptive fan-out with a confirm beat)

- **Pilot first.** `run_docking.py --mode plan` docks ~20 ligands and measures real
  throughput on *this* receptor/library/box. Do not assume a constant; ~13 s/ligand/core is
  only a starting point and swings with ligand flexibility, box size, and exhaustiveness.
- **Report before you scale.** The plan writes `fanout_plan.json` with measured rate, a
  recommended worker count, estimated wall time, and worker-hours (a cost proxy). **Tell the
  user "~X h on N workers (~W worker-hours), proceed?" and get confirmation** before
  provisioning a fleet, especially when `exceeds_abort_threshold` is true.
- **Fan out.** On approval, provision N workers with `ManageMachine` (e.g. 8-core each, up to
  the session's 5-machine limit) and run `dock_worker.py --shard i --nshards N` on each,
  writing shards to a shared/`$RUN/scores` dir. Then `--mode collect`.
- **Graceful fallback.** Small library or no `ManageMachine` → `--mode local` (process pool
  on one machine). This is automatic, not a separate user-facing mode.
- **FUSE-safe I/O.** `/mnt/results` and `/mnt/shared-workspace` are S3-backed and reject
  append/streaming writes. Workers write scores to local `/workspace` first and copy once at
  the end (handled inside the scripts). Never point per-ligand streaming output at `/mnt`.

See `references/compute_and_scaling.md` for the full procedure.

## Scientific caveats (MUST appear in every report's Limitations)

- Single **rigid receptor** conformation — no induced fit / side-chain flexibility.
- Vina scoring error **~2-3 kcal/mol**; poor **absolute** affinity — use for ranking only.
- One **tautomer / protonation / conformer** per ligand; **no explicit waters**.
- Labeled runs: **decoys are presumed-inactive** (property-matched or DUD-E), not confirmed
  non-binders — enrichment is optimistic relative to a real prospective screen.
- **Redock warnings** (if the native pose was not reproduced) must be stated prominently.
- **Docking prioritizes, it does not confirm** — top hits need orthogonal rescoring and
  experimental validation.

## Reusing platform capabilities (don't reinvent)

- **ADMET:** this skill calls the Biomni `predict_admet_properties` tool (DeepPurpose-backed)
  as an **advisory** annotator. At authoring time there is **no dedicated tox/ADMET skill**;
  if one becomes available, **prefer it** and pass its output into the top-N table instead.
- **Literature:** use the Biomni `LiteratureSearch` tool; keep results in the walled-off
  section only.
- **PDF styling:** `build_report.py` mirrors the `pdf-report-generation` skill's Phylo visual
  language (gold `#D4A04A` primary accent, Helvetica, Letter, gold table headers). If you
  extend the report, load that skill and keep them visually aligned.

## Data Sources & Licenses

This skill retrieves data from public external sources at run time. Downstream/commercial use
must honor each source's license. **`DATA_SOURCES.md` holds the full details; this is the summary.**

| Source | Used by | License | Commercial use | Key obligation |
|---|---|---|---|---|
| **ChEMBL** (bioactivities) | `build_library.py --chembl-target` | **CC BY-SA 3.0 Unported** | **Yes** | **Attribution + Share-Alike** |
| **RCSB PDB** (structures) | `fetch_receptor.py --pdb-id` | Public domain (**CC0 1.0**) | Yes | None required (citation appreciated) |
| **DUD-E** (actives/decoys benchmark) | `build_library.py --dude-dir` | Free for research (dude.docking.org) | Verify before commercial use | Cite Mysinger et al. 2012 |
| **TDC / DeepPurpose ADMET models** | `admet_annotate.py` (advisory) | TDC data CC BY 4.0; DeepPurpose BSD‑3‑Clause | Yes | Attribution; advisory outputs only |
| **Enamine REAL / datalake** (optional; off by default) | `build_library.py --datalake/--enamine` | Enamine terms of use | Check Enamine terms | Per Enamine license |

- **ChEMBL is commercial-use-friendly but "copyleft."** CC BY-SA 3.0 **permits commercial use**, but
  **requires attribution AND share-alike**: any redistributed dataset **derived** from ChEMBL (e.g. a
  `master_library.csv` built from a ChEMBL target pull, or its docking-scored derivatives) must (a) credit
  ChEMBL with the resource URL and **release/version** (e.g. "ChEMBL data from https://www.ebi.ac.uk/chembl,
  ChEMBL_35"), (b) **preserve the ChEMBL IDs**, and (c) be licensed under the **same CC BY-SA 3.0** terms.
  Plan for this in any product that ships ChEMBL-derived compound sets.
- **Human Protein Atlas (HPA)** is **not used by this skill**. *If* an HPA-derived layer is ever added
  (HPA is **CC BY-SA 4.0**), the same **commercial-OK + attribution + share-alike** obligations apply and
  must be documented here.

## References

- `references/worked_example_egfr.md` — a fully-worked, honestly-reported EGFR/DUD-E run
  (including a deliberately weak enrichment result) as a template for interpretation.
- `references/method_reference.md` — docking parameters, box sizing, and exact enrichment
  metric definitions/conventions.
- `references/compute_and_scaling.md` — pilot → fan-out procedure, cost/ETA math, FUSE rules.
- `references/troubleshooting.md` — RMSD atom mismatches, FUSE errors, 1-CPU autoscaling,
  meeko/gemmi install, `prepare_receptor` PATH, ChEMBL/datalake degradation.
