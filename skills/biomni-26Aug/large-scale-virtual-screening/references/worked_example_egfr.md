# Worked example: EGFR / DUD-E benchmark (honest reporting template)

This is the reference run the skill was built from. It is deliberately included with a
**weak enrichment result**, because the point of the example is to show how to report a
screen *honestly* — a benchmark that separates actives from decoys only slightly is a real
and common outcome, and the report should say so plainly.

All numbers below are from that run's saved outputs.

## Setup

- **Target:** EGFR kinase domain, **PDB 2ITY**, co-crystal ligand **IRE = gefitinib**
  (4-anilinoquinazoline chemotype; C22H24ClFN4O3).
- **Library:** DUD-E EGFR set — **542 actives + 2500 decoys** (decoys seeded-random
  subsampled from ~35k with `random.seed(42)`), **3042 total**.
- **Box:** 16 A cube centered at **(-50.5, -0.73, -21.56)**, from the native ligand.
- **Docking:** AutoDock Vina, exhaustiveness 8, 9 modes, seed 42, 1 CPU/ligand, across
  4 fan-out workers (8 cores each). 4 ligand timeouts out of 3042 (>99.8% completion).

## Pose validation (redock)

- Native-ligand redock top pose: **-8.964 kcal/mol**.
- **Whole-molecule RMSD 3.221 A** but **rigid-core RMSD 0.325 A** (anilinoquinazoline core,
  17 of 31 heavy atoms). The flexible morpholino-propoxy tail is solvent-exposed and moves,
  which inflates the whole-molecule number. Under the core-aware two-tier gate this **PASSES**
  — and this is exactly the case that motivated the core-aware design.
- The `redock_validation.json` records the exact core atom indices so the criterion is
  auditable rather than hand-waved.

## Enrichment (labeled branch)

| Metric | Value |
|---|---|
| ROC-AUC | **0.589** |
| BEDROC (alpha=20) | **0.193** |
| EF 1% | **1.09** |
| EF 5% | 0.887 |
| EF 10% | 1.016 |
| Actives median affinity | -8.08 kcal/mol |
| Decoys median affinity | -7.87 kcal/mol |
| Mann-Whitney p | 4.6e-11 |
| Rank-biserial effect | 0.18 |

**Honest reading:** the separation is **statistically significant but tiny** (huge n makes
p small; the effect size 0.18 and ROC-AUC 0.59 are near-random). Early enrichment is weak
(EF1% ~1.1, i.e. barely better than random at the top). This is a *near-random* screen by
recognition quality — the report says so, and does not oversell "significant separation."

## Triage + SAR

- **Precision@N:** P@10 = **40%** (4/10), P@20 = 25%, P@50 = 24%, P@100 = 19%, vs a baseline
  active rate of **17.8%**. The very top is modestly enriched even though global AUC is poor.
- **Scaffolds:** 542 actives -> **215 Butina clusters** (Morgan r=2, 2048-bit, Tanimoto
  cutoff 0.4), **136 singletons**, largest clusters 30/18/17/14/13 — a chemically diverse
  active set.
- **Property-affinity Spearman (a scoring-bias check):** AromaticRings **rho=-0.388**,
  HeavyAtoms -0.341, MW -0.302, cLogP -0.297 (all: bigger/more-lipophilic -> more-negative
  score), while QED **rho=+0.231** (drug-likeness -> worse score). This is the classic
  **Vina size/lipophilicity bias**: the function rewards large, greasy molecules, which
  partly explains why decoy-vs-active separation is poor and why the top of the list can be
  dominated by high-MW compounds. The report flags this so top hits aren't taken at face
  value.

## What this example teaches

1. Whole-molecule redock RMSD can fail for a *good* pose when a tail is flexible — use the
   auditable rigid-core criterion.
2. Statistical significance != useful enrichment; report effect size and early EF, not just p.
3. Always check property-affinity trends; a strong MW/cLogP correlation means the ranking is
   partly a molecular-weight sort, not binding discrimination.
4. A weak benchmark is still a legitimate, reportable result — say so, and recommend
   orthogonal rescoring before committing to hits.
