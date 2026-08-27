# Troubleshooting

Common errors and gotchas across the DESeq2 / edgeR / limma-voom engines and the shared
backbone scripts. Grouped by stage.

---

## Loading & validation (`load_data.R`)

**"Sample IDs do not match between counts and metadata"**
- Count-matrix column names must equal metadata rownames (set order is reconciled, but the
  *sets* must match). Check for trailing whitespace, `.` vs `-` in sample names, or an extra
  index column read in as data.
- Reading a CSV with `read.csv(...)` without `row.names=1` leaves gene IDs as a column —
  the loader expects gene IDs as rownames. Pass the gene-ID column or fix the file.

**"Matrix does not appear to contain raw integer counts"**
- The values look non-integer (TPM/FPKM/logged). DESeq2/edgeR/voom require **raw counts**.
  Obtain the count matrix, or accept the documented **limma-trend** fallback (approximate;
  see comparison-and-caveats.md). Do not round TPM to fake counts.

**tximport / SummarizedExperiment path fails**
- Ensure the `tximport` object's `$counts` (or `assay(se)`) is gene-level and that a
  `tx2gene` mapping was applied upstream for transcript quantifiers (Salmon/Kallisto).

---

## Inspection & design (`inspect_and_recommend.R`)

**Full-rank check warns: design is not full rank / confounded**
- A covariate is aliased with `condition` (e.g. every batch-1 sample is control, every
  batch-2 sample is treated). The effect is **inestimable by any engine**. Remedies: drop
  the confounded covariate, redesign, or (if paired) verify the pairing variable is correct.
- This is a real statistical block, not a warning to ignore — no method can rescue a
  rank-deficient design.

**Recommendation seems off for my data**
- The recommendation is **advisory**. If you know your design (e.g. you want limma-voom for
  a 40-sample multi-factor model), override it. The thresholds (≤2/group → edgeR; =3/group →
  DESeq2 with edgeR noted; ≥20/group or ≥4 coef → limma-voom) are soft starting points.

**PCA column-collision / "duplicate column" error**
- Guarded in the script: any metadata column literally named `sample`/`PC1`/`PC2` is
  dropped before the PCA coordinates are bound on, so the merge can't produce duplicate
  columns. If you patched the script, preserve that guard.

---

## Pre-filtering (`filter_counts.R`)

**Almost all genes filtered out**
- `filterByExpr()` uses the design/group to set the min-count threshold. If `group` or
  `design` is mis-specified (e.g. all samples in one group), filtering behaves oddly. Pass
  the correct group/design.

**Concordance denominators differ across methods**
- All engines **must** receive the same filtered matrix from `filter_counts.R`. If you filter
  separately per engine, concordance will measure filtering differences, not method
  differences. Filter once, reuse.

---

## DESeq2 (`run_deseq2.R`)

**"every gene contains at least one zero, cannot compute log geometric means"**
- Classic on sparse data. Pre-filtering usually fixes it; otherwise DESeq2 falls back to a
  poscounts/iterate size-factor estimator. Ensure `filter_counts.R` ran first.

**apeglm shrinkage error / wrong coefficient shrunk**
- `lfcShrink(type="apeglm")` shrinks a **coefficient**, not an arbitrary contrast. The engine
  resolves the coefficient name; if you pass a custom contrast that isn't a model coefficient,
  shrinkage is skipped and the **unshrunk** LFC is reported (which is what the standardized
  schema uses anyway). Shrunk LFC is for DESeq2's own viz only.

**Reference level / direction looks flipped**
- Set the factor reference explicitly (`relevel`). Positive `log2FoldChange` = up in the
  non-reference (test) level. The contrast builder uses treatment coding; confirm which level
  is the reference.

---

## edgeR (`run_edger.R`)

**`plotBCV` vs `plotQLDisp` confusion**
- With the **QL** pipeline (`glmQLFit`/`glmQLFTest`), the correct dispersion diagnostic is
  `plotQLDisp()` on the **fit**, not `plotBCV()`. The engine returns both the `glmQLFit`
  object and the `DGEList` (`dge`) so QC can call the right one. Don't swap them.

**"no residual degrees of freedom"**
- Too few samples relative to the model (e.g. 1 replicate per group with a multi-factor
  design). Reduce model complexity or add replicates.

---

## limma-voom (`run_limma_voom.R`)

**voom mean-variance plot looks like a flat cloud / weird trend**
- Usually means the input wasn't raw counts (voom expects counts → logCPM internally) or the
  data is over-filtered/under-filtered. Verify raw counts and the shared filter.

**Using limma-trend by mistake on raw counts**
- `limma-trend` (`eBayes(trend=TRUE)` on logCPM) is the **fallback for normalized-only
  input**, not the default for raw counts. On raw counts use the **voom** path. The engine
  only takes the trend path when explicitly in fallback mode; results are flagged approximate.

---

## Concordance (`concordance.R`)

**Empty consensus list / ordering error on empty result**
- Guarded: when no gene is significant in ≥2 methods, the function returns an empty,
  correctly-typed table instead of erroring on ordering. A truly empty consensus can be real
  (e.g. very few DEGs) — check per-method DEG counts first.

**Tempted to compare `baseMean_equiv` across methods**
- Don't. It is on different scales per engine (linear vs log2) and is excluded from all
  cross-method logic by design. Concordance uses only `padj` (and optionally unshrunk
  `|log2FC|`).

---

## Export (`export_results.R`)

**0-byte `.rds` file on `/mnt/results/`**
- S3-backed mounts don't support random-access writes. The engine uses a safe-save helper
  that writes the `.rds` to local scratch then copies it over. If you see a 0-byte file, the
  helper was bypassed — write to `/workspace/` first, then `cp` to `/mnt/results/`.

**CSV written but not visible to the user**
- Only files under `/mnt/results/` surface to the user. Writing to the working directory
  alone hides the output.

---

## General

- Record the engine, version, design, contrast, filter, and thresholds (`run_log.txt`) for
  every run — most "why is this different" questions are answered there.
- Significance is always on `padj` (BH-FDR), never raw p-values.
