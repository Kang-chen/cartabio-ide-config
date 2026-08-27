# Benchmarking against manual gating (honestly)

Runs **only** when per-cell manual-gate labels exist. The goal is an honest comparison, and honesty
here has two specific failure modes that this skill hard-wires against.

> **Not the same as step 4b validation.** This file (`05`) is *cluster-vs-per-cell-labels*
> benchmarking (precision/recall/F1/ARI/NMI), which needs a per-cell ground-truth label column —
> rare for real customer data. `scripts/08_validate_vs_manual.R` (`references/validation_vs_manual.md`)
> is the *per-sample statistics* reconciliation against a FlowJo-style aggregate export (population
> counts / %), which real data usually *does* have. When only aggregate stats exist, use `08`, not `05`.

## The standard mapping: maximum overlap
Map each automated cluster to the manual population it overlaps most (Weber & Robinson 2016), then
score precision/recall/F1 per manual population, plus overall accuracy, ARI, and NMI.

## CORRECTNESS TRAP #1 — name the mapping vector
`apply(ct, 1, which.max)` returns an **unnamed** vector. Indexing it by cluster id then returns all
`NA`, silently zeroing every metric. Always name it:

```r
ct  <- table(cluster = cl, truth = truth)
c2p <- setNames(colnames(ct)[apply(ct, 1, which.max)], rownames(ct))  # cluster -> gold, NAMED
pred <- factor(unname(c2p[as.character(cl)]), levels = levels(truth))
```

## CORRECTNESS TRAP #2 — F1 = 0 usually means "merged", not "missed"
With standard max-overlap, when **two** manual populations map to the **same** cluster, only one can
"win"; the other scores **F1 = 0**. That is a clustering **resolution** artifact, not evidence the
population is undetectable.

Concrete example from the reference run: at `meta16`, `CD8_T`, `CD16+_NK`, `CD34+CD38lo_HSCs`,
`Pro_B`, and `CD34+CD38+CD123+_HSPCs` scored F1 = 0 — yet at full SOM resolution (~95–100 clusters)
CD8_T recovered to **F1 ≈ 0.98** and CD16+_NK to ≈ 0.86. Meanwhile `CD4_T` showed precision ≈ 0.57 /
recall ≈ 1.00 because CD8 events were being absorbed into the CD4 cluster — the two sides of the same
merge. Reporting "CD8 F1 = 0" without this context is misleading.

**So: detect many-to-one collapses and label status explicitly.**

```r
# for each gold population, find the cluster it maps to, and check who that cluster is assigned to
status[p] <- if (partner != p) paste0("merged_with:", partner) else if (F1 == 0) "missed" else "recovered"
```

Statuses: `recovered` (own cluster), `merged_with:<other>` (resolution artifact — re-separates at
higher resolution), `missed` (absent even at full resolution). Figures color merged populations
distinctly from missed ones.

## Resolution-sensitivity sweep — separate merges from misses
Re-score the benchmark across several resolutions (e.g. `meta10, meta14, meta20, som100`). A
population whose F1 climbs as resolution increases was **merged**; one that stays at 0 even at full
SOM resolution was genuinely **missed**. This is the auditable way to defend a resolution choice
without an oracle population count.

## Metrics reported
- Per population: precision, recall, F1, `n_truth`, status.
- Overall: accuracy, weighted-F1 (weight by `n_truth`), ARI, NMI (`aricode`).
- Counts: #recovered / #merged / #missed at the chosen resolution and across the sweep.

## References
- Weber & Robinson. Comparison of clustering methods for high-dimensional cytometry. Cytometry A 2016.
- Hubert & Arabie (ARI); Strehl & Ghosh (NMI); `aricode` R package.
