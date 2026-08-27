# Worked example: Levine_32dim (the reference run this skill generalizes)

This skill was distilled from a full analysis of **Levine_32dim** (Bioconductor `HDCytoData`). The
numbers below are the actual results of that run — use them as a sanity check when validating the
pipeline, and as a concrete illustration of the honesty rules.

## Dataset (provenance FROM metadata — do not infer)
- **32-marker mass cytometry (CyTOF)** of **healthy adult human bone-marrow mononuclear cells
  (BMMCs)** from **two healthy donors** (H1, H2; ages ~19–28). This is `HDCytoData`'s "Benchmark
  Data Set 2."
- **Critical provenance note:** these donors are **healthy**. The source paper (Levine et al., Cell
  2015) is *about* AML, and a *different* dataset (Levine_13dim) is the AML cohort — but Levine_32dim
  itself is healthy BMMC. An earlier draft of this analysis wrongly called them AML patients; the fix
  was to read cohort from package metadata, never from the paper's topic. **This is exactly why
  `04`/`07` pull provenance from metadata and assert no cohort when metadata is silent.**
- H1: **191,351** cells; H2: **74,276** cells. Manually gated fractions: H1 = 37.9%, H2 = 42.7%
  labeled into 14 reference populations (the rest "unassigned").

## Preprocessing
- Modality auto-detected as CyTOF → **arcsinh, cofactor 5**. No compensation (CyTOF).
- **QC on pre-cleaned benchmark data.** For known-pre-cleaned data like HDCytoData, run with
  `--qc off` → QC removes 0 cells (the correct usage; see the re-run command below). With the default
  `--qc on`, the data-driven engine instead *proposes* a DNA-intact gate: because pre-cleaned DNA is
  **unimodal**, the honesty guard (dip test) refuses to invent a valley, flags **REVIEW_unimodal**, and
  falls back to the conservative legacy 5th/99.5th-percentile cutoff (~5.5% removed — H1 191,351→180,873;
  H2 74,276→70,208, identical to the prior fixed-percentile behavior), writing every cutoff to
  `gating_thresholds_template.csv` with a per-gate figure for you to confirm or set `apply=N`. On *real*
  FCS the valley detection does the real work. Either way the behavior is transparent and reviewable
  (see qc_gating.md and threshold_selection.md).

## Clustering
- FlowSOM `xdim=10, ydim=10` (**100 nodes**), ConsensusClusterPlus `maxK=20`, `seed=1234`.
- Chosen resolution **meta16** (delta-area/known-structure); also evaluated the full SOM (~95–100
  clusters, "som100").

## Benchmark vs manual gates (the honesty illustration)
| Resolution | accuracy | weighted-F1 | recovered |
|---|---|---|---|
| meta16 | **0.760** | **0.675** (ARI 0.672, NMI 0.780) | 9 / 14 |
| som100 (~95 clusters) | **0.966** | **0.962** | 12 / 14 |

- Well-separated lineages score high at meta16: Monocytes **F1 ≈ 0.998**, Mature_B **0.987**,
  Pre_B **0.948**, Basophils ≈ 0.906, pDCs ≈ 0.878.
- **Five populations scored F1 = 0 at meta16** — `CD8_T`, `CD16+_NK`, `CD34+CD38lo_HSCs`, `Pro_B`
  (n=513), `CD34+CD38+CD123+_HSPCs` (n=304) — but these were **merged, not missed**: at som100 they
  recovered to F1 ≈ **0.98, 0.86, 0.68, 0.00, 0.00** respectively (CD8_T and CD16+_NK clearly
  resolution artifacts; the two rare HSPC subsets are closer to genuinely hard).
- The tell-tale merge signature: at meta16 `CD4_T` had precision ≈ **0.567**, recall ≈ **1.00** —
  CD8 events were being absorbed into the CD4 cluster. That is one merge seen from both sides.
- **Lesson:** never report "CD8 F1 = 0" as "CD8 undetectable." The sweep proves it re-separates.
  This is why `05` labels `merged_with:` explicitly and runs the resolution sweep.

## Abundances (per-sample composition)
- CD4 T ≈ **42%**, Monocytes ≈ **20%**, Mature B ≈ **14%** of cells (dominant populations).
- Pre-B differed between donors: H1 = **4.44%** vs H2 = **8.42%** (~2×) — the kind of per-sample
  difference the abundance tables/heatmap surface.

## Reproducibility knobs used
- Seeds: 1234 (prep/cluster), 1601 (dimensionality reduction), 1 (ggrepel label layout).
- UMAP/t-SNE on a balanced **10,000 cells/sample** subsample (visualization only; capped at the smallest sample).

## How to re-run the equivalent
```bash
Rscript scripts/01_load_and_qc.R  --input HDCytoData:Levine_32dim --qc off --outdir <o>
Rscript scripts/02_cluster.R      --outdir <o> --xdim 10 --ydim 10 --maxK 20 --seed 1234
Rscript scripts/03_annotate.R     --outdir <o>
Rscript scripts/04_quantify_dr.R  --outdir <o> --cells_per_sample 10000 --seed 1601
Rscript scripts/05_benchmark.R    --outdir <o> --sweep meta10,meta14,meta20,som100
python  scripts/07_build_report.py --outdir <o>
```
(`06_diff_abundance.R` is skipped here — 2 samples, no group contrast, and below the ≥3/group gate.)
