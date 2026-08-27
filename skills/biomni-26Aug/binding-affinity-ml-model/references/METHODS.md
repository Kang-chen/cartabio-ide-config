# Methods & Rationale — ChEMBL QSAR / Novel-Scaffold Discovery

Reusable, defensible methods text and the reasoning behind each threshold. Adapt
the bracketed values to the actual run; keep the honest framing.

---

## 1. Data source and endpoint pooling
Bioactivity records were retrieved from [ChEMBL_XX] for [target / ChEMBL IDs].
Only measurements with an exact relation (`standard_relation == '='`), numeric
`standard_value`, `standard_units == 'nM'`, and an associated canonical SMILES
were retained. Affinity endpoints **IC50, Ki, Kd, EC50** were pooled into a
single potency label:

    pAffinity = -log10(value[M]) = -log10(value[nM] * 1e-9)

**Why pool.** A single clean endpoint is frequently too sparse to model
(especially for protein-protein-interaction targets). Pooling multiplies usable
data. The risk is mixing direct-binding constants (Kd/Ki) with functional
readouts (IC50/EC50). We mitigate this by (a) tracking each compound's dominant
assay type as a covariate (`assay_group` ∈ {binding(Kd/Ki), functional
(IC50/EC50)}), (b) checking that the two groups have similar mean pAffinity
before pooling (a large offset would signal a batch effect), and (c) reporting
**within-group** rank correlation to confirm the model captures structure-
activity signal rather than an assay-type offset.

## 2. Structure standardization and drug-like filtering
Each molecule was standardized with RDKit: largest organic fragment selected,
neutralized (`Uncharger`), sanitized, and canonicalized. Compounds were
deduplicated by InChIKey. The **drug-like small-molecule** filter kept
molecules with **MW ≤ 650 Da**, at least one carbon, and no metal atoms.

**Why MW ≤ 650.** This is a pragmatic boundary separating conventional
small molecules from peptides and macrocycles. It is deliberately generous
(beyond the classic Lipinski 500) to retain larger drug-like chemotypes while
still excluding peptidic matter. For PPI targets this filter can remove the
majority of actives — this is expected and is exactly what the data-reality gate
surfaces.

## 3. Data-reality gate (go / no-go before modeling)
After curation, the number of drug-like small molecules is checked against a
minimum (`min_compounds`, default 100). Below this, modeling is halted and the
target is re-scoped (pool more endpoints, add the single-protein target, or
acknowledge the target is not small-molecule-tractable).

**Why.** ChEMBL activity counts overstate modelable data. A target with
thousands of activities can yield only tens of drug-like molecules. Discovering
this *after* building a model wastes effort and produces meaningless metrics
(e.g. a GNN "trained" on 30 compounds). The gate forces the check up front.

## 4. Replicate aggregation and quality control
Multiple measurements for the same compound were aggregated by **median**
pAffinity. Compounds whose replicate spread exceeded **2 log units** were
dropped (unreliable); spread > 1 log unit was flagged. PAINS substructures were
flagged (using RDKit's PAINS catalog) but not removed, so reviewers can judge
them per candidate.

## 5. Molecular representations
Two complementary representations were used:
- **Molecular graphs** for the GNN: atoms as nodes with a 39-dim feature vector
  (one-hot element from {C,N,O,S,F,Cl,Br,I,P,B,Si,Se,H,other}, degree, formal
  charge, H count, hybridization, aromaticity, ring membership); bonds as
  undirected edges with a 7-dim feature vector (bond type, conjugation, ring
  membership).
- **Morgan / ECFP fingerprints** for the baselines: radius 2, 2048 bits.

The fingerprint encoding is defined once and reused for curation, CV, and
screening so train/test featurization is always identical.

## 6. Models
- **GNN:** an edge-conditioned message-passing network (`NNConv`), 3 layers,
  hidden width 64, ReLU, batch-norm, dropout 0.2, global sum-pooling, MLP head;
  Adam (lr 1e-3, weight decay 1e-4), batch 32, up to 200 epochs with early
  stopping (patience 25). ~0.43M parameters.
- **Baselines:** Random Forest (300 trees in CV, 500 for the final model) and
  Gradient Boosting (300 trees, depth 3, lr 0.05) on Morgan fingerprints.

## 7. Cross-validation and the leakage-free protocol
Performance was estimated by **repeated K-fold CV** ([3]×[5] folds) under two
splits:
- **Scaffold split (headline):** Bemis-Murcko scaffold groups are assigned
  wholly to folds (largest groups first, each to the currently-smallest fold),
  guaranteeing zero scaffold overlap between train and test. This measures
  generalization to genuinely new chemistry — the realistic discovery setting.
- **Random split (reference only):** reported to quantify how much analog
  leakage inflates naive metrics; never used for model selection.

**Leakage-free early stopping (critical).** The GNN's early-stopping validation
set is carved from the **training indices only** (an inner 85/15 split); the
test fold is used solely for the final prediction, and target standardization
(mean/SD) is fit on the inner-training data only. All models see identical
folds. This prevents the subtle but serious error of selecting the GNN's stopping
epoch on the test fold, which would bias scaffold-split metrics upward and make
the GNN-vs-baseline comparison unfair.

**Model selection.** The production model is the one with the best
**scaffold-split Spearman** (rank correlation is the robust criterion for a
noisy, small-N regression). The GNN is not assumed to win; at a few hundred
compounds, fingerprint baselines commonly generalize better, and reporting that
honestly is part of the method.

## 8. External library screening and three-tier applicability domain
The selected model (Random Forest by default) was retrained on all curated
compounds and used to score an external library ([ChEMBL clinical/approved small
molecules at max_phase 4/3/2] or a user-supplied CSV). Library compounds were
standardized and drug-like-filtered identically, deduplicated, and any compound
already in the training set (by InChIKey) removed. Every remaining **novel-
scaffold** compound (Bemis-Murcko scaffold absent from training) was assigned an
**applicability-domain confidence tier** from three signals: nearest-neighbour
(NN) Tanimoto to the training set, whether the prediction lies within the
observed training range, and per-tree prediction disagreement.

- **High-confidence (in-domain):** NN-Tanimoto ∈ **[0.40, 0.70]** AND prediction
  ≤ training max AND per-tree std ≤ the 75th-percentile cutoff. Only this tier is
  treated as a nominated shortlist.
- **Borderline (low-confidence):** NN-Tanimoto ∈ **[0.30, 0.40)** — the compound
  is only weakly similar to any training molecule. Reported for transparency but
  **not** a confident hit.
- **Out-of-domain (unreliable):** NN-Tanimoto **< 0.30** (extrapolation into
  unseen chemistry) OR **> 0.70** (trivial near-analog, not a novel chemotype) OR
  prediction out of range OR high disagreement.

**Why the tiers replaced the old single band.** A previous version nominated any
compound with NN-Tanimoto in a single **[0.25, 0.55]** band as "confident." In
practice the bulk of such hits sit near the bottom of that band (NN-Tanimoto
~0.26–0.43), i.e. barely above the extrapolation floor — the model is effectively
extrapolating for them and cannot score them reliably. Calling those "confident"
over-claims reliability. The tiered scheme raises the confidence floor to 0.40
and explicitly labels 0.30–0.40 as borderline and <0.30 as out-of-domain, so a
low-similarity compound is never presented as a confident hit. NN-Tanimoto to the
training set is a standard, transparent QSAR applicability-domain criterion.

**Why Random Forest for screening.** Tree ensembles cannot predict outside the
training target range, so their predictions stay physically sensible, and they
provide a native per-tree uncertainty used by the tiers; deep models (GNN /
DeepPurpose) can extrapolate to implausible values and lack an intrinsic spread.
Combined with usually matching or beating the deep models on the scaffold-split
benchmark at small-to-moderate N, this makes RF the safer production choice.

## 9. Modeling frameworks and the no-silent-substitution principle
Model families compared on identical folds: Morgan-fingerprint Random Forest and
Gradient Boosting (baselines), an optional edge-conditioned message-passing GNN,
and — **when the user explicitly requests it** — a **DeepPurpose** compound-
property model (default `CNN` drug encoding), trained with the same leakage-free
protocol (inner-train early stopping; test fold untouched). If a requested
framework cannot be imported or fails at runtime, the pipeline does **not**
silently substitute a different method: it records the reason in
`framework_provenance.json`, falls back to the native models, and the report
states plainly that the framework was requested-but-unavailable and what was used
instead. This prevents mislabeling native-model results as the requested
framework's — a reporting-integrity requirement, not merely a convenience.

## 10. Honest interpretation
Results are **discovery-grade**: a ranking of hypotheses for experimental triage,
not calibrated affinity predictions. Under a hard scaffold split even the best
model explains only a bounded fraction of variance, and most external library
compounds fall **outside the applicability domain** (only the high tier is
reliable). When cross-validation uses a single repeat, report the fold spread as
the SD of a **single 5-fold CV**, not "repeated CV." Nominated high-confidence
compounds — including any approved drugs, which represent a repurposing angle —
require confirmation in a target-specific binding or functional assay before any
activity claim.

## 11. Data source and license
Bioactivity, structures, and the screening library come solely from **ChEMBL**
(CC BY-SA 3.0): commercial use is permitted with **attribution** (record the
ChEMBL release; preserve ChEMBL IDs) and **share-alike** (redistributed derivative
datasets carry CC BY-SA 3.0). See `references/DATA_SOURCES.md`.

## 12. Suggested extensions
- Conformal prediction for calibrated per-compound confidence intervals to
  complement the tiered applicability domain.
- Multi-task or larger-data training (which may make the GNN / DeepPurpose models
  competitive with fingerprints).
- A separate model for the peptide/macrocycle chemical space that the drug-like
  filter excludes.
- Selectivity / off-target and basic ADMET triage on the high-confidence
  shortlist.
