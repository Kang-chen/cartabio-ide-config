---
id: "skill_5b530f17e1aaedd0139286faf1950c3c"
name: binding-affinity-ml-model
description: "Use to train and honestly benchmark target-specific small-molecule affinity or potency models from ChEMBL IC50, Ki, or Kd data, including QSAR, fingerprint, GNN, or DeepPurpose models, then screen or repurpose external compounds for novel target-active scaffolds with scaffold-split and applicability-domain checks."
category: "molecular_design"
visibility: "public"
starting-prompt: "Train and benchmark a QSAR model on ChEMBL bioactivity for my target, then screen a library for novel scaffolds: ..."
---

# ChEMBL QSAR / Novel-Scaffold Discovery

Turn a target name into a curated ChEMBL dataset, an honestly-benchmarked
affinity model, and a ranked list of novel-scaffold candidates — with the
correctness guardrails that separate a real result from a misleading one.

## Scope
- **Does:** pull ChEMBL bioactivity for a target, curate a drug-like
  small-molecule regression dataset (pooled pAffinity), benchmark models under a
  **scaffold-split** CV (Morgan-fingerprint Random Forest / Gradient Boosting,
  an optional message-passing GNN, and — when explicitly requested — a
  **DeepPurpose** deep model as a first-class competitor), select the best
  model, screen an external library (ChEMBL clinical/approved compounds or a
  user CSV), assign every novel-scaffold compound to a **three-tier
  applicability-domain confidence** (high / borderline / out-of-domain), and
  build a Phylo-styled PDF report.
- **Does NOT:** handle biologics/peptides/macrocycles as the modeling set
  (they are filtered out — see caveats), classification tasks, docking, ADMET,
  de-novo generation, or multi-target selectivity. This is single-endpoint
  small-molecule **affinity regression**.

## Requested modeling frameworks — NO SILENT SUBSTITUTION
If the user explicitly names a modeling framework (e.g. **DeepPurpose**), the
skill **must either use it or state prominently that it was unavailable and what
was used instead** — it must never quietly swap in a different method and present
it as if it were the requested one.
- **DeepPurpose is supported directly.** Pass `--framework deeppurpose` to
  `benchmark_models.py` (or `model_framework: "deeppurpose"` in the config). A
  DeepPurpose compound-property model (`deeppurpose_drug_encoding`, default
  `CNN`) is then trained and benchmarked as a **first-class model on the same
  scaffold/random CV folds** as the fingerprint baselines.
- **If the framework cannot run** (import failure, runtime error), the run does
  NOT silently substitute: it prints a prominent notice, records the reason in
  `data/framework_provenance.json`, falls back to the native models, and the PDF
  report states plainly that the requested framework was requested-but-unavailable
  and which models were used instead.
- **Any other named framework** that the skill does not implement must be handled
  the same way: attempt it, or refuse-and-disclose. Do not present native-model
  results as the requested framework's results.
- Selection of the production (screening) model is still by best **scaffold-split
  Spearman**; DeepPurpose competes on equal footing but the fingerprint Random
  Forest is preferred for *screening* when it wins, because it yields a native
  uncertainty estimate (per-tree spread) used by the applicability domain and
  cannot extrapolate beyond the training range.

## Inputs
- A target **gene/protein symbol** (e.g. `PCSK9`, `EGFR`) to auto-resolve, OR
  explicit **ChEMBL target IDs** (e.g. `CHEMBL2929`). Symbols are resolved to
  candidate targets with activity counts — confirm the right record(s) before
  committing (PPI targets, mutants, and orthologs all appear).
- Optional config JSON overriding any default (endpoints, MW cutoff, CV
  repeats, **applicability-domain tier thresholds**, `model_framework`,
  `deeppurpose_*` settings, library definition). See
  `scripts/common.py::DEFAULTS`.
- Optional external library as a CSV with a `smiles` column (otherwise ChEMBL
  clinical/approved small molecules are fetched).

## Outputs (under `<outdir>`, default `/mnt/results/qsar_run`)
- `data/curated_dataset.csv` — curated compounds + scaffolds + assay group
- `data/cv_summary.csv`, `data/cv_fold_metrics.csv` — benchmark metrics
  (includes a `DeepPurpose` row when the framework is requested and runs)
- `data/rf_scaffold_oof.csv` — selected-model out-of-fold predictions
- `data/framework_provenance.json` — what framework was requested, whether it
  ran, and (if not) why — so the report can disclose any fallback
- `data/all_scored_candidates.csv` — **every** novel-scaffold compound with its
  `ad_tier` (high / borderline / out_of_domain) and `ad_flags`
- `data/high_confidence_candidates.csv` — the high-confidence (in-domain)
  shortlist only; `data/top25_high_confidence.csv` — best per scaffold
- `data/ad_tier_summary.json` — tier counts + thresholds used
- (Back-compat: `data/novel_scaffold_candidates.csv` and
  `data/top25_novel_candidates.csv` still exist but now contain **only**
  high-confidence rows — never borderline/out-of-domain.)
- `figures/fig1..fig5` (`.png` + `.svg`) — data plots (fig5 = AD-tier bar)
- `report_<TARGET>_qsar.pdf` — the deliverable

## Environment
Python 3.10+, with `rdkit`, `torch`, `torch_geometric`, `scikit-learn`,
`scipy`, `pandas`, `numpy`, `matplotlib`, `reportlab`, `pypdf`, `Pillow`, and
(for `--framework deeppurpose`) `DeepPurpose`.
The GNN/DeepPurpose CV is CPU-friendly for a few hundred compounds
(~20-30 s/fold); for large datasets or many folds prefer an 8-core machine, use
a **single 5-fold CV** (`cv_repeats=1`) and a bounded `deeppurpose_train_epoch`,
or benchmark the deep model on a representative subsample (stated transparently
in the report). Install PyG with `uv pip install torch_geometric` and DeepPurpose
with `uv pip install DeepPurpose` if absent.

## Data sources & license (see `references/DATA_SOURCES.md`)
This skill's **only external data source is ChEMBL** (bioactivity, structures,
clinical/approved library) via the EBI ChEMBL REST API. ChEMBL is licensed
**CC BY-SA 3.0**: commercial use is permitted **but requires attribution**
(cite the ChEMBL release used — the skill records it per run — and preserve
ChEMBL IDs) **and share-alike** (any redistributed derivative dataset, e.g. the
curated CSVs, must carry CC BY-SA 3.0). The skill does not use ChEMBL's
commercial-software-derived property calculations. Human Protein Atlas is **not**
used by this skill.

---

## Workflow

Run the scripts in order. Each writes into `<outdir>` and the next reads from
it, so `--outdir` must be the same throughout. All scripts accept
`--config myconfig.json` and CLI overrides.

### Step 1-2 — Curate the dataset AND pass the data-reality gate
```bash
python scripts/curate_dataset.py --symbol PCSK9 --outdir /mnt/results/pcsk9
# or explicit targets:
python scripts/curate_dataset.py --target_ids CHEMBL2929,CHEMBL4523996 --outdir /mnt/results/pcsk9
```
This fetches `=`-relation nM affinities (IC50/Ki/Kd/EC50), standardizes
structures (largest fragment, neutralize, canonicalize), filters to drug-like
space (MW ≤ 650, organic), aggregates replicates by median, computes
Bemis-Murcko scaffolds, and labels each compound's assay group.

**Why the gate is the most important step.** ChEMBL activity counts are
misleading: a target can show thousands of activities but only tens of
*drug-like small molecules*, because its chemical matter is peptides/macrocycles
(common for protein-protein-interaction targets) or the obvious endpoint is
sparse. The script prints a per-target / per-endpoint breakdown and **exits
non-zero if the final set is below `min_compounds` (default 100)**. If it fails,
re-scope before modeling:
- pool more endpoints (already pools IC50/Ki/Kd/EC50),
- add the single-protein target alongside a PPI target,
- reconsider whether the target is tractable for small molecules at all.

**Decision rule:** N < 100 → do not run the GNN; either re-scope or drop to
fingerprint-only. N < ~200 → proceed but expect modest scaffold-split
performance; fingerprint baselines often win (that is a valid result).

### Step 3 — Benchmark models honestly (scaffold + random CV)
```bash
python scripts/benchmark_models.py --outdir /mnt/results/pcsk9
# fingerprint-only (faster, or when N is small):
python scripts/benchmark_models.py --outdir /mnt/results/pcsk9 --no_gnn
# honor an explicitly requested framework (first-class, no silent substitution):
python scripts/benchmark_models.py --outdir /mnt/results/pcsk9 --framework deeppurpose
```
Runs CV (default repeats × folds from config; use `cv_repeats=1` for a single
5-fold CV on large data) for the available models on **identical folds**, under
a **scaffold split** (headline) and a **random split** (optimistic reference
only). With `--framework deeppurpose`, a DeepPurpose model competes on the same
folds. Reports mean ± SD (SD is across folds; state "single 5-fold CV" in the
report when `cv_repeats=1` — do NOT call it "repeated CV") and recommends the
best scaffold-split model. Writes `data/framework_provenance.json`.

**Critical correctness property:** every deep model's early stopping uses an
inner validation split carved from the **training indices only**; the test fold
is used solely for the final prediction, and target scaling is fit on training
data only. This avoids the subtle leakage that makes scaffold-split metrics
optimistic and the model comparison unfair.

**No silent substitution:** if `--framework deeppurpose` is requested but cannot
run, the script prints a prominent notice, records the reason in
`framework_provenance.json`, and continues with native models — the report then
discloses this. It never presents native-model results as DeepPurpose's.

**Decision rule — model selection:** pick the model with the best
**scaffold-split Spearman**, not R². Do NOT assume a deep model (GNN /
DeepPurpose) wins; at small-to-moderate N the fingerprint models usually
generalize as well or better. If a deep model loses, select the fingerprint
model and say so plainly.

### Step 4 — Train final model + screen the library
```bash
python scripts/screen_library.py --outdir /mnt/results/pcsk9            # RF (default)
python scripts/screen_library.py --outdir /mnt/results/pcsk9 --model gnn  # only if GNN won
python scripts/screen_library.py --outdir /mnt/results/pcsk9 --library_csv mylib.csv
```
Trains the selected model on all curated compounds, scores the library, and
assigns every novel-scaffold compound to an **applicability-domain confidence
tier**. Random Forest is the default because tree ensembles do not extrapolate
beyond the training range (predictions stay physically sensible) and give a
native per-tree uncertainty used by the tiers.

**Three-tier applicability domain (replaces the old single "confident" band).**
Each novel-scaffold compound's nearest-neighbour Tanimoto to the training set,
prediction range, and per-tree disagreement determine its tier:
- **high (in-domain):** NN-Tanimoto ∈ `[ad_high_tanimoto_min,
  ad_high_tanimoto_max]` (default **[0.40, 0.70]**) **AND** prediction ≤ training
  max **AND** per-tree std ≤ the `ad_std_quantile` cutoff. Only this tier is a
  shortlist.
- **borderline (low-confidence):** NN-Tanimoto ∈ `[ad_borderline_tanimoto_min,
  ad_high_tanimoto_min)` (default **[0.30, 0.40)**) — weakly similar; reported
  for transparency but **never** called a confident hit.
- **out_of_domain (unreliable):** NN-Tanimoto **< 0.30** (extrapolation into
  unseen chemistry) **or > 0.70** (trivial near-analog, not novel) **or**
  prediction out of range **or** high disagreement.

**Why this matters:** the previous single band `[0.25, 0.55]` labelled
weakly-similar compounds (NN-Tanimoto ~0.26–0.43, i.e. barely above the
extrapolation floor) as "confident," dramatically over-claiming reliability. A
low nearest-neighbour Tanimoto means the model is extrapolating and the compound
must be flagged low-confidence / out-of-domain — not nominated as confident.
Tier counts and thresholds are written to `data/ad_tier_summary.json`; all
compounds with tiers to `data/all_scored_candidates.csv`; the high-confidence
subset to `data/high_confidence_candidates.csv`.

### Step 5 — Figures + report
```bash
python scripts/make_figures.py --outdir /mnt/results/pcsk9
python scripts/build_report.py --outdir /mnt/results/pcsk9 --target PCSK9 --chembl_version ChEMBL_37
```
`make_figures.py` draws the **data** plots (pred-vs-actual, model comparison
incl. DeepPurpose when present, dataset landscape, and the **AD-tier bar**,
fig5). `build_report.py` auto-detects the selected model from `cv_summary.csv`,
reads `framework_provenance.json` and `ad_tier_summary.json`, and assembles the
PDF with honest, discovery-grade framing — reporting the three AD tiers,
disclosing any requested-framework fallback, and describing the CV as a single
5-fold CV (SD across folds) rather than "repeated." (Conceptual/mechanism
figures, if wanted, should be made with an image-generation tool, not
matplotlib.)

After building, validate the PDF (`pypdf` page count) and visually check the
figures rendered (not blank/clipped) before delivering.

---

## Scientific caveats (carry these into any report)
- **Data-scale sets the ceiling.** A few hundred compounds is small for affinity
  modeling (scaffold Spearman often ~0.3-0.4, high fold variance → strictly
  discovery-grade). Well-populated targets (thousands of drug-like compounds,
  e.g. major kinases) can reach much higher scaffold-split performance, but that
  is still bounded to the populated chemical space — state the regime honestly
  rather than assuming either extreme.
- **Report CV honestly.** With `cv_repeats=1` the reported SD is across the five
  folds of a **single** 5-fold CV — describe it that way, NOT as "repeated CV."
- **No silent framework substitution.** If a specific framework was requested
  (e.g. DeepPurpose) but not used, the report MUST say so and name what was used
  instead. Never present native-model results as the requested framework's.
- **Applicability domain is three-tier, and most library compounds fall
  outside it.** Only the **high** tier (reliable NN-Tanimoto window + in-range +
  low disagreement) is a shortlist. Weakly-similar compounds (low NN-Tanimoto)
  are **borderline** or **out-of-domain** — extrapolations, not confident hits.
  Never label a low-Tanimoto compound "confident."
- **Pooled heterogeneous label.** IC50/Ki/Kd/EC50 are combined into one
  pAffinity. The curation step checks that assay-group means match (no batch
  offset), but pooling still mixes functional inhibition with direct binding.
  Track `assay_group` and report within-group signal.
- **Drug-like filter excludes real chemistry.** MW ≤ 650 removes peptides and
  macrocycles. For PPI targets this can remove most of the actives — that is the
  point of the data-reality gate, not a bug. If the target's therapeutics are
  biologics (antibodies, siRNA), a small-molecule affinity model is inherently
  limited; state this.
- **Predictions are unvalidated hypotheses.** Applicability-domain tiering
  reduces but does not eliminate unreliability. Predicted potencies are a
  **ranking**, not calibrated affinities. Even high-confidence compounds need
  experimental confirmation before any activity claim.
- **ChEMBL version + license.** Record the ChEMBL release used (counts/candidates
  change between versions) AND honor CC BY-SA 3.0 (attribution + share-alike;
  see `references/DATA_SOURCES.md`).
- **Confirm target resolution.** Auto-resolution returns candidates by activity
  count; always confirm the intended single-protein vs PPI vs mutant record.

## Files
- `scripts/common.py` — config (incl. tiered-AD + framework knobs), ChEMBL
  access, RDKit helpers, and the `ad_tier()` classifier (one source of truth for
  the drug-like/scaffold/fingerprint/AD-tier definitions)
- `scripts/curate_dataset.py` — Step 1-2 (fetch, curate, data-reality gate)
- `scripts/models.py` — featurization, MPNN, scaffold folds, leakage-free trainer
- `scripts/models_deeppurpose.py` — guarded DeepPurpose wrapper (raises
  `FrameworkUnavailable` so callers disclose + fall back, never substitute)
- `scripts/benchmark_models.py` — Step 3 (CV; `--framework deeppurpose`, writes
  framework provenance)
- `scripts/screen_library.py` — Step 4 (final model + three-tier applicability
  domain)
- `scripts/make_figures.py`, `scripts/build_report.py` — Step 5
- `references/METHODS.md` — reusable methods text, thresholds, and rationale
- `references/DATA_SOURCES.md` — external data sources and licenses (ChEMBL
  CC BY-SA 3.0)
