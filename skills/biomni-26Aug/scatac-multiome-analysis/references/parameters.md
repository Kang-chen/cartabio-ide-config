# Parameters & rationale

Defaults below were validated on the 10x 10k-PBMC (v2, GRCh38) run. They are
**sensible starting points, not universal constants** — revisit per tissue, assay
chemistry, and sequencing depth. All are exposed in `config.yaml`.

## 1. QC metrics and filter

| metric | default | meaning / why |
|--------|---------|---------------|
| `min_count` | 3,000 | Min fragments in peaks. Below this, too sparse to embed reliably. |
| `max_count` | 100,000 | Max fragments. Very high → possible multiplets / barcode artifacts. |
| `min_frip` | 25 (%) | Fraction of reads in peaks. Low FRiP → poor signal-to-noise / ambient. |
| `max_nucleosome` | 4 | Nucleosome banding ratio. High → over-digested / low-quality nuclei. |
| `min_tss` | 3 | TSS enrichment. Core ENCODE ATAC quality metric; low → weak open-chromatin signal. |
| `max_blacklist` | 0.05 | Fraction of fragments in ENCODE blacklist regions. High → artifact-driven. |

Derivations (from 10x per-barcode metrics when a `singlecell.csv` is provided):

```
pct_reads_in_peaks = peak_region_fragments / passed_filters * 100
blacklist_ratio    = blacklist_region_fragments / peak_region_fragments
```

Applied as a single AND filter:
```
nCount_peaks > min_count & nCount_peaks < max_count &
pct_reads_in_peaks > min_frip & nucleosome_signal < max_nucleosome &
TSS.enrichment > min_tss & blacklist_ratio < max_blacklist
```

**Tissue note:** blood/PBMC tolerates strict FRiP/TSS; solid-tissue or archival
nuclei often need `min_tss` ~2 and `min_frip` ~15–20. Inspect the pre-filter QC
violins (figure 01) before committing thresholds.

**Fragments-only note:** if no per-barcode metrics exist, FRiP/blacklist are
computed directly from the fragments against the (recalled) peak set and blacklist;
if no cell list exists, candidate cells come from a fragment-count knee first.

## 2. Dimensionality reduction & clustering

- `set.seed(1234)` before every stochastic step.
- **TF-IDF** normalization (`RunTFIDF`) → `FindTopFeatures(min.cutoff="q0")` (all
  peaks) → **LSI** via `RunSVD`.
- **Drop LSI component 1**: it captures sequencing depth, not biology. Verify with
  `DepthCor()` (figure 05) — component 1 typically shows |r| > 0.9 with depth.
  Downstream uses `dims = 2:30`.
- `RunUMAP` / `FindNeighbors` on `dims = 2:30`; `FindClusters(algorithm=3)` (SLM).
- `resolution_initial = 1.2` → clusters used **only** as groups for peak recalling
  (over-clustering here is fine and desirable — more cell-state-specific peaks).
- `resolution_final = 1.0` → clusters on the recalled matrix used for annotation.

## 3. Per-cluster MACS3 peak recalling (best practice)

Aggregate (whole-sample) peak calling under-represents peaks specific to rare
populations. Calling **per cluster** then merging recovers cell-state-specific
regulatory elements. In the PBMC run this yielded **180,156** merged peaks vs
**165,376** in the aggregate vendor set (standard-chrom filtered).

```r
peaks <- CallPeaks(obj, group.by = "seurat_clusters",
                   macs2.path = macs3_path,
                   outdir = macs_dir)          # outdir MUST pre-exist (dir.create first)
peaks <- keepStandardChromosomes(peaks, pruning.mode = "coarse")
peaks <- subsetByOverlaps(peaks, blacklist, invert = TRUE)
mat   <- FeatureMatrix(fragments = Fragments(obj), features = peaks,
                       cells = colnames(obj))
# rebuild ChromatinAssay on `mat`, then repeat TF-IDF/LSI/UMAP/clusters
```

Then recompute the full reduction/clustering on the recalled matrix.

## 4. Gene activity & annotation

- **Restrict `GeneActivity` to the marker panel.** Computing activity over all
  ~28k genes is slow and times out; the annotation only needs the marker genes.
  `NormalizeData(scale.factor = median(obj$nCount_ACTIVITY))`.
- **Annotation logic:** z-score each gene's mean activity across clusters
  (`z <- t(scale(t(avg)))`), average z per marker set per cluster, assign each
  cluster to `max.col(score_mat)`.
- **Confidence gating (required):** report per cluster the top marker-set z-score
  and the margin to the 2nd. Flag `low-confidence` when top z < `min_top_zscore`
  (default 1.0) and `ambiguous` when margin < `min_margin` (default 0.25).
  *Cautionary example from the demo:* cluster 19 → DC at z = 0.97 (below 1.0) —
  a genuinely weak call the gate surfaces instead of hiding.

### Two critical Seurat/Signac gotchas (they cause silent/loud failures)

1. **`AverageExpression` prefixes numeric group names with `g`** (`g0`, `g1`, …).
   Strip and realign or the cluster→type map keys won't match:
   ```r
   colnames(avg) <- sub("^g", "", colnames(avg))
   avg <- avg[, levels(obj$seurat_clusters), drop = FALSE]
   ```
2. **Metadata assignment needs cell-barcode names**, else
   `No cell overlap between new meta data and Seurat object`:
   ```r
   ct <- unname(map[as.character(obj$seurat_clusters)])
   names(ct) <- colnames(obj)
   obj <- AddMetaData(obj, factor(ct, levels = sort(unique(ct))), col.name = "cell_type")
   ```

### Label transfer (optional) & multiome (inverted)
- Optional: `FindTransferAnchors` (reference scRNA-seq → query gene-activity) +
  `TransferData`; compare against marker calls and flag disagreements.
- **Multiome:** annotate on the matched RNA modality, then transfer labels to ATAC
  cells (RNA-anchored is more reliable than accessibility-only).

## 5. Differential accessibility

```r
obj <- FindTopFeatures(obj, min.cutoff = "q25")   # restrict to variable peaks
vf  <- VariableFeatures(obj)
da  <- FindAllMarkers(obj, features = vf, only.pos = TRUE, min.pct = 0.15,
                      test.use = "wilcox", max.cells.per.ident = 200)
```

- **Do NOT use `test.use = "LR"` with `latent.vars`** — it hangs on ~180k peaks.
  Wilcoxon on variable peaks with per-ident subsampling is fast and defensible.
- `presto` (installed) accelerates the Wilcoxon test.
- **`ScaleData` feature-order quirk (ChromatinAssay):** reorder features to the
  assay's own order before scaling, else `validObject`/order errors:
  ```r
  feat_order <- rownames(obj[["recalled"]])
  toppeaks   <- feat_order[feat_order %in% toppeaks]
  obj <- ScaleData(obj, features = toppeaks)
  ```
- Report each DA peak's nearest gene + distance (`ClosestFeature`).

## 6. Determinism & provenance

UMAP, clustering, MACS3, and DA subsampling are stochastic. Results are
**directional, not bitwise-reproducible** across machines/versions. The pipeline
fixes `seed` everywhere and writes a `provenance_manifest.json` capturing
parameters, `sessionInfo()` + package versions, genome build, input paths/accession,
and seeds so a run is auditable and re-runnable.
