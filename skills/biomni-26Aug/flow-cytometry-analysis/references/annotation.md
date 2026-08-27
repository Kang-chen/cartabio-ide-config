# Cluster annotation (marker-driven, expert-in-the-loop)

Clusters are numbers; annotation assigns them biological identities. This is **semi-automated**: the
pipeline proposes labels from marker signatures, then a human confirms. Never ship auto-labels as
final without review.

## Step 1 — per-cluster marker signature
Compute the **median** expression of each clustering (type) marker per cluster, then z-score across
clusters so high/low is relative:

```r
med <- sapply(clusters, function(cc)
         matrixStats::rowMedians(ex[type_markers, cl == cc, drop = FALSE]))
z <- t(scale(t(med)))                    # markers x clusters, z-scored across clusters
```

Median (not mean) resists outliers; z-scoring makes the heatmap readable and comparable across
markers on different scales.

## Step 2 — propose labels with a curated marker reference (CellMarker2)
Score each candidate cell type by the mean z-score of its marker set; the top-scoring type is the
proposal. CellMarker 2.0 (in the Biomni data lake) provides manually curated human/mouse markers.

```r
score[celltype] <- mean(z[markers_of(celltype), cluster])   # highest score -> proposed label
```

Restrict the reference to the tissue/context when known (e.g. bone marrow, PBMC) to avoid
implausible calls. If CellMarker2 is unavailable, fall back to a small built-in canonical panel and
say so.

## Step 3 — human review via an editable template
Write `annotation_template.csv` with columns: `cluster, proposed_population, top_markers, population`.
The `population` column is pre-filled with the proposal but **meant to be edited**. Re-running with a
completed template applies the expert labels via `CATALYST::mergeClusters(sce, k = chosen_k,
table = merge_tbl, id = "annotation", overwrite = TRUE)`.

## Step 4 — verify against the heatmap
The marker heatmap (populations × markers, z-scored) is the annotation audit trail: every label
should be defensible from the shown signature (e.g. CD3+CD4+ → CD4 T; CD19+CD20+ → B; CD14+ →
monocyte). If a label is not visible in the heatmap, fix the label.

## Two-tier annotation
Prefer **coarse-then-split**: annotate major lineages confidently, then re-cluster and sub-annotate
lineages that are internally heterogeneous or were flagged *merged* in benchmarking. This yields
robust top-level labels without over-interpreting noise.

## References
- Hu et al., CellMarker 2.0. Nucleic Acids Research 2023. doi:10.1093/nar/gkac947.
- Crowell et al., CATALYST (`mergeClusters`, `plotExprHeatmap`).
