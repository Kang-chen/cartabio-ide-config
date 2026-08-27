# Example data and the demonstration prediction set

## Contents

- Default demonstration: a real public benchmark
- Data sources and licences
- The demonstration prediction set
- Why this composition (design decision)
- Measure, never assume
- What is *not* tuned
- Reproducing the asset
- Synthetic generator (offline smoke test only)

## Default demonstration: a real public benchmark

The default demonstration and every headline number use **real measured** assay data, not
synthetic labels. `scripts/fetch_benchmark.py` downloads **Lipophilicity_AstraZeneca** — 4,200
real experimental logD₇.₄ (octanol/water, pH 7.4) values — from Harvard Dataverse using only the
Python standard library (no credentials, no added dependency). logD₇.₄ is a log-ratio that can be
negative, so it is modelled as `task=regression, scale=linear` (**not** `log10`, which would
wrongly exponentiate it); the benchmark carries no dates, so it uses a **scaffold** split.

A deterministic build at authoring time (full workflow: audit → train → predict) selected
**XGBoost on 2-D descriptors** with a locked-outer **R² ≈ 0.64, MAE ≈ 0.61 logD, Spearman ≈
0.79** — honest, modest real-assay performance, not the inflated numbers a synthetic label with a
known functional form would produce.

## Data sources and licences

Re-verify licences before redistributing; `fetch_benchmark.py` records them in every run.

- **Lipophilicity_AstraZeneca** — real logD₇.₄ for 4,200 compounds. Source: AstraZeneca (2016)
  publicly disclosed in-vitro DMPK data, popularised by MoleculeNet (Wu et al., *Chem. Sci.* 2018),
  obtained through Therapeutics Data Commons (TDC). TDC lists it as **"Not Specified. CC BY 4.0"** —
  the upstream AstraZeneca release did not state a licence and CC BY 4.0 is the label TDC applies.
  Attribute AstraZeneca (2016) and MoleculeNet.
- **AqSolDB** — real aqueous solubility (log mol/L) for 9,982 compounds. Source: Sorkun et al.,
  *Scientific Data* 6, 143 (2019), Harvard Dataverse (doi:10.7910/DVN/OVHAW8), obtained through TDC.
  The original publication is **CC BY 4.0** and TDC also lists CC BY 4.0. Attribute Sorkun et al. (2019).

TDC aggregates many datasets and applies CC BY-NC-SA 4.0 as a blanket catch-all; the two
dataset-specific records above are CC BY 4.0. This skill **fetches** the data at runtime rather than
bundling the full datasets; only a 100-molecule demonstration subset is shipped, with attribution.

## The demonstration prediction set

`assets/sample-test-set.csv` is the bundled set scored by `predict_adme_model` in the example
workflow, built by `scripts/build_sample_test_set.py` from **real molecules**. It has four columns:

- `smiles` — 100 real drug-sized SMILES;
- `design_source` — `lipophilicity_holdout` or `aqsoldb_external`;
- `design_regime` — the **intended** AD regime, `expected_in_domain` or `expected_out_of_domain`;
- `measured_logD` — the real held-out logD for the in-domain block; blank for the out-of-domain
  block (AqSolDB reports aqueous solubility, a different endpoint).

It is a deliberate **50/50 construction** spanning both AD regimes:

- **50 `expected_in_domain`** (`lipophilicity_holdout`) — real Lipophilicity molecules randomly
  held out of the training set and removed from the deployment reference. They share the training
  endpoint and chemical space and carry their real logD, but are not verbatim reference members.
- **50 `expected_out_of_domain`** (`aqsoldb_external`) — real molecules from a **distinct
  database** (AqSolDB), restricted to those whose molecular weight is **outside the training set's
  own drug-like MW window** (data-derived 5th–95th percentile, ≈ **[212, 539] Da** at the last
  build). MW is a chemical-property criterion; it does **not** use the AD fingerprint metric.

## Why this composition (design decision)

An earlier revision made this set 100% verbatim training molecules (nearest-neighbour similarity
1.000) — that only demonstrated trivial recall. The revision after that swung to ~97% out of
domain — that only demonstrated the extrapolation-warning path. **Neither extreme demonstrates the
skill,** and both were built on synthetic labels.

Composing against a **real** benchmark makes an honest in-domain block possible: molecules held out
of a real 4,150-molecule training set are genuinely near-domain (not toy analogs) yet not verbatim
members. Drawing the out-of-domain block from a **different database** and gating it on a
molecular-weight window — not the fingerprint metric the flag uses — gives an out-of-domain block
that is defined independently of the thing being measured. The mixed set exercises the **in-domain
prediction path** and the **extrapolation-warning path** in one run.

## Measure, never assume

`design_regime` records the *intended* regime only. The *achieved* nearest-neighbour similarity and
in-domain fraction are computed at runtime against the real deployment reference, never asserted:
`scripts/build_sample_test_set.py` prints them on regeneration, and a real `predict_adme_model` run
reports them in `prediction_manifest.json` (`applicability_domain` counts, `nn_similarity_*`, and a
`domain_warning` when most rows are OOD).

Measured at the last regeneration (deployment reference n=4,150; AD threshold 0.294 = outer-training
leave-one-out 5th-percentile similarity):

| block | n (scored) | NN Tanimoto (min / median / max) | achieved in-domain |
|---|---|---|---|
| `expected_in_domain` | 50 (50) | 0.250 / 0.713 / 1.000 | 48 / 50 = **0.96** |
| `expected_out_of_domain` | 50 (48) | 0.071 / 0.289 / 0.688 | 23 / 48 = **0.48** |
| overall | 100 (98) | — | 71 / 98 = **0.72** |

Two out-of-domain-designed molecules are inorganic AqSolDB salts (e.g. sodium titanate, copper
oxide) that the structure standardizer correctly rejects as `invalid_structure`; they are excluded
from the fractions above rather than silently counted. Roughly half the expected-OOD molecules
nonetheless score in-domain — real small-molecule spaces from different assays overlap, and the
measurement **says so** rather than hiding it. These numbers, and any drift on rebuild, are reported
by the measurement, not assumed.

The AD flag's honesty on this dataset is itself measured: the build reports the computed
`error_monotonicity` verdict (out-of-fold error vs similarity stratum). On this Lipophilicity build
the verdict is **`supported`** (Spearman ρ = −1.0 across 4 strata; per-stratum mean absolute error
0.69 → 0.59 → 0.54 → 0.54 as similarity rises). That is derived from the strata, not asserted, and a
different dataset can yield `not_evidenced` or `inverted` — in which case the flag must not be
presented as a validated trust signal.

## What is *not* tuned

The AD threshold (0.294), the Morgan radius-2 Tanimoto metric, and the molecule selection were
**not** adjusted to hit any target in-domain fraction. The 50/50 split is an intended *construction*
choice; the in-domain block is a plain random holdout and the out-of-domain block is gated only on
the data-derived MW window and cross-database novelty. The achieved fraction is whatever the
measurement yields.

## Reproducing the asset

Run, with the pinned runtime, from an isolated environment (paths resolve relative to the script;
no `setwd`/`chdir`):

```
python scripts/build_sample_test_set.py
```

It fetches Lipophilicity_AstraZeneca and AqSolDB, holds out 50 real Lipophilicity molecules for the
in-domain block, trains the reference model on the rest (scaffold split, ECFP + 2-D descriptors,
XGBoost in the ladder), derives the deployment reference and AD threshold, selects the out-of-domain
block by the MW-window/cross-database rule, writes `assets/sample-test-set.csv`, and measures the
achieved coverage via `predict_bundle`. The run is deterministic given the versioned Dataverse files.
Requires network access; there is no synthetic fallback — if the fetch fails the build stops rather
than substituting synthetic data.

## Synthetic generator (offline smoke test only)

`scripts/make_example_data.py::make_dataset` builds a small **synthetic** logD-like dataset by
decorating a compact `SEED_SMILES` fragment list. It exists **only** as a deterministic,
network-free smoke test for the pipeline; it is **not** real measurements and its metrics must never
be reported as real-assay performance. `make_heldout_prediction_set` similarly writes an
in-domain-only synthetic prediction set for that smoke test. For any demonstration or real result,
use the real benchmark above.
