# Clustering & choosing resolution (without an oracle)

## Algorithm: FlowSOM + consensus meta-clustering

The community-standard, reproducible choice (CATALYST wraps it):

1. **FlowSOM** trains a self-organizing map over **type/lineage markers only** (not state/functional
   markers). Grid `xdim=10, ydim=10` → **100 SOM nodes** is a good default (fine enough to capture
   rare populations, coarse enough to be stable).
2. **ConsensusClusterPlus** meta-clusters the 100 nodes into `k` metaclusters, up to `maxK=20`.
3. Fix the seed (e.g. `seed=1234`) so results are reproducible.

```r
sce <- CATALYST::cluster(sce, features = "type", xdim = 10, ydim = 10,
                         maxK = 20, seed = 1234, verbose = FALSE)
```

## The hard part: choosing resolution WITHOUT knowing the true number of populations

The reference worked example leaned on "we know there are 14 populations." **In the wild you will not
have that oracle.** Two principled defaults:

### 1. Delta-area elbow
The consensus delta-area curve plots the relative gain in consensus CDF area as `k` increases.
The gain plateaus once you stop finding real structure — the elbow is a good `k`.

```r
pick_k_from_delta <- function(sce, min_gain_frac = 0.10, fallback = "meta12") {
  tryCatch({
    da <- CATALYST::delta_area(sce)
    y  <- ggplot2::ggplot_build(da)$data[[1]]$y      # reconstruct the delta-area values
    gain <- diff(y)
    # elbow = largest k whose incremental gain is still >= 10% of the max gain
    k <- max(which(gain >= min_gain_frac * max(gain))) + 1
    paste0("meta", k)
  }, error = function(e) fallback)
}
```

Always guard with `tryCatch` and a sensible fallback (e.g. `meta12`).

### 2. Two-tier: annotate coarse, then split flagged lineages
Rather than chasing one perfect global `k`:
- Pick a **coarse** `k` that cleanly separates major lineages (T / B / myeloid / NK / progenitors).
- For any lineage that is heterogeneous or was flagged as *merged* in benchmarking, **re-cluster just
  those cells** at higher resolution. This recovers CD4 vs CD8, naive vs memory, etc., without
  over-fragmenting well-defined populations globally.

### Expose the knob
Whatever the default, the resolution (`--k`/`--resolution`) is a first-class, overridable parameter.
Report a **resolution-sensitivity sweep** (see benchmarking_metrics.md) so the choice is auditable.

## Which markers to cluster on
Use lineage/type markers (surface phenotype). Exclude state/functional markers (cytokines,
phospho-, activation) from clustering — they define *states within* a population and belong in the
differential-**state** analysis, not in defining the population itself.

## References
- Van Gassen et al., FlowSOM. Cytometry A 2015.
- Wilkerson & Hayes, ConsensusClusterPlus. Bioinformatics 2010.
- Nowicka et al., CyTOF workflow. F1000Research 2019;6:748 (delta-area heuristic; type vs state markers).
