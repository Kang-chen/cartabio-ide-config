# Worked example: DRD2 de novo design

The skill was distilled from a complete, validated DRD2 run. Use these numbers as
a sanity reference when re-running the pipeline (expect similar magnitudes, not
identical values — the GA is stochastic across environments).

## Setup
- **Target:** DRD2 (dopamine receptor D2).
- **Activity backend:** TDC `DRD2` oracle (an SVM on ECFP; the Olivecrona 2017
  surrogate). Validation points: haloperidol = 1.000, risperidone = 0.983,
  aripiprazole = 1.000, ethanol = 0.0052, water = 0.0076.
- **Objective for the shipped run:** the geometric mean of activity and QED
  (`drd2_benchmark` preset, = `sqrt(activity × QED)`), with synthesizability then
  applied as an explicit filter. The `production` preset folds SA directly into
  the objective and is the recommended default for new targets.
- **Seeds:** 10 known DRD2 ligands (haloperidol, risperidone, aripiprazole,
  clozapine, quetiapine, olanzapine, sulpiride, chlorpromazine, ziprasidone,
  raclopride). Note olanzapine scores low on the oracle (0.124) — a documented
  surrogate quirk worth reporting, not a bug.
- **GA:** pop_size = 100, n_generations = 20, seed = 42.

## Results
- **Generation:** 1,271 unique valid molecules. Best composite fitness moved from
  0.9535 (gen 1) to 0.9563 (gen 20); mean fitness 0.4706 → 0.7705.
- **Filtering cascade:** 1,271 → 673 (novelty, Tanimoto < 0.4) → 327
  (activity > 0.5 AND QED > 0.6) → 325 (PAINS-clean) → 143 (ring-sane) → 132
  (SA_Score ≤ 4.5) → top 10.
- **Top 10:** activity 0.947–0.997, QED 0.801–0.879, SA_Score 2.21–4.30,
  nearest-known Tanimoto 0.311–0.397 (all genuinely novel).
- **Retrosynthesis (AiZynthFinder, USPTO + ZINC):** 2/10 fully solved —
  **DN-01** (3 steps, route score 0.9866) and **DN-06** (1 step, 0.9976). The
  other 8 returned partial routes (scores 0.72–0.83).

## Key scientific lesson (report this every time)
`SA_Score` inversely tracks solvability: the two solved designs had the lowest SA
(2.21, 2.98) while unsolved ones ran higher (up to 4.30). **A high oracle score
and high QED do not guarantee synthesizability** — this is exactly why the
production objective gates on makeability and why the ring-sanity + SA filters
exist. Without them, the GA happily produces strained bridged polycyclic
artifacts that score well but cannot be made.

## Spot-check you can run
`scoring.preset_drd2_benchmark(tdc_DRD2)(["O=C(CCCN1CCC(O)(c2ccc(Cl)cc2)CC1)c1ccc(F)cc1"])`
should return ≈ **0.871** for haloperidol (= sqrt(1.000 × 0.759)).
