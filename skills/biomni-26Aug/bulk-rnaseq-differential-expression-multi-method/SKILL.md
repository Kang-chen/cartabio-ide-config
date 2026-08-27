---
name: bulk-rnaseq-differential-expression-multi-method
description: >
  General-purpose bulk RNA-seq differential expression (DEG) analysis with automatic,
  data-driven method selection across DESeq2, edgeR (quasi-likelihood F-test), and
  limma-voom. Use this whenever someone has a bulk RNA-seq count matrix (genes x samples)
  and wants differentially expressed genes between conditions — treated vs control, KO vs
  WT, tumor vs normal, dose-response, time course — even if they don't name a method, and
  especially when they're unsure which of DESeq2/edgeR/limma-voom to use. The skill
  inspects the data (raw integer counts vs normalized, replicates per group, library-size
  spread, batch/paired structure, design rank), recommends the most appropriate method
  with plain-language reasoning, asks the user to confirm, then runs a unified workflow
  producing a standardized results table, QC plots (PCA, MA, volcano, mean-variance),
  significant-gene lists (BH-FDR padj), and an optional cross-method concordance check.
  Handles two-group and multi-group/contrast designs, batch and paired covariates, and
  tximport/featureCounts/SummarizedExperiment inputs.
license: "Engine licenses — DESeq2/limma are LGPL/Artistic, edgeR is GPL (>=2); all permit research use."
---

# Bulk RNA-seq Differential Expression (multi-method)

A **method-aware** differential expression skill for bulk RNA-seq **count** data. It does
not lock you into one engine. Instead it looks at your data, **recommends** DESeq2, edgeR,
or limma-voom with a plain-language rationale, lets you **confirm or override**, then runs
a single unified workflow and (optionally) cross-checks gene calls across methods.

Reach for this whenever someone has a bulk RNA-seq count matrix and wants differentially
expressed genes — especially when the question is "which method should I use?"

---

## When to Use This Skill

Use it when the user has **a bulk RNA-seq count matrix** and wants differentially
expressed genes, and any of the following is true:

- They don't specify a method, or explicitly aren't sure whether to use DESeq2, edgeR, or limma-voom.
- The experiment has small n, large n, or a non-trivial design (batch, paired, multi-group).
- They want a robustness/sensitivity check across methods (concordance).
- They uploaded `quant.sf` (Salmon), Kallisto, featureCounts, or a `SummarizedExperiment`.

**Not for:** single-cell RNA-seq (use a scRNA-seq skill), normalized-only data where raw
counts are unavailable (handled only as a flagged last-resort fallback — see Caveats), or
upstream alignment/quantification (counts are the entry point).

---

## Inputs

| Input | Format | Notes |
|---|---|---|
| **Count matrix** | genes x samples, **raw integer counts** | CSV/TSV/RDS, tximport (Salmon/Kallisto), featureCounts, or `SummarizedExperiment`. **Not** TPM/FPKM/log-normalized. |
| **Sample metadata** | data frame | Rownames (or a sample-id column) match count columns. Must contain a grouping column (default name `condition`). Optional: `batch`, `individual`/`subject`, other covariates. |
| **User options** (optional) | — | Method override, reference level, design formula, **which contrast/coefficient to test**, significance thresholds, whether to run concordance. |

The mechanics of loading, validating, and orienting the matrix live in
[`scripts/load_data.R`](scripts/load_data.R) — use it rather than reimplementing ingestion.

---

## Outputs (saved to the results directory)

- `de_results_<method>.csv` — full standardized results table.
- `de_significant_<method>.csv` — significant genes (default padj < 0.05) plus the |log2FC| ≥ 1 subset.
- QC plots (PNG **and** SVG): PCA, MA, volcano, and the method-appropriate mean-variance / dispersion plot.
- `normalized_counts.csv` and the fitted model object (`.rds`).
- On request: `concordance_table.csv` + `consensus_degs.csv`.
- `run_log.txt` — method chosen and why, design formula, contrast tested, filter summary, thresholds, full-rank check result, per-engine `baseMean_equiv_scale`, and package versions.

### Standardized results schema (identical across all three engines)

`gene_id, baseMean_equiv, log2FoldChange, pvalue, padj, method`

This common schema is what makes downstream tooling and concordance method-agnostic. Two
columns require care:

- **`baseMean_equiv` is a *method-native* expression-strength metric and is NOT comparable
  across methods.** DESeq2 reports it on the linear count scale (mean normalized count);
  edgeR (logCPM) and limma (AveExpr) report it on the log2 scale. The same gene's value can
  differ by orders of magnitude purely because of which engine ran. It is fine for an
  engine's own MA plot and within-method ranking, but **never** use it in cross-method
  logic. See [`references/comparison-and-caveats.md`](references/comparison-and-caveats.md).
- There is deliberately **no merged `stat` column.** Wald z (DESeq2), QL F (edgeR), and
  moderated t (limma) are not comparable, so each engine keeps its native statistic only in
  its own raw output (labeled with a `stat_type`); it is never cross-merged.

---

## Workflow

The hard mechanics are in the scripts; your job is to drive them in the right order and
explain the method choice. Run from the skill directory so relative `scripts/...` paths
resolve.

**Step 0 — Clarify inputs (ask first).** Before anything else, confirm: (a) the count
file(s) and that values are **raw integer counts** (not TPM/FPKM); (b) the grouping column
and the **reference/control level**; (c) any covariates (batch, paired/subject id); and
(d) for **>2 groups**, *which contrast or coefficient* the user wants tested. Don't guess
the comparison — it defines the whole analysis.

**Step 1 — Load and validate.**
```r
source("scripts/load_data.R")
loaded <- load_de_data(counts = "<path-or-matrix>", metadata = "<path-or-df>",
                       condition_col = "condition")
```
This checks sample-ID concordance, matrix orientation, and integer-ness, and returns a
clean `counts` matrix + `coldata` data frame.

**Step 2 — Inspect and recommend (advisory).**
```r
source("scripts/inspect_and_recommend.R")
rec <- inspect_and_recommend(loaded$counts, loaded$coldata,
                             condition_col = "condition",
                             design = ~ condition)   # or your covariate formula
print(rec)
```
This reports replicates per group, integer-ness, library-size spread, PCA clustering by
condition/batch, and a **programmatic full-rank/confounding check** (model-matrix rank vs
its number of columns). It returns a *recommended* method and a *proposed* design formula.

Translate `rec` into a short prose recommendation for the user — **prefer the recommended
method, explain why, name the alternatives, and ask them to confirm or override.** The
heuristics are advisory, not hard rules:

- **Default to DESeq2** for typical experiments — robust and widely validated.
- **Lean toward edgeR** when replicates per group are very small — its quasi-likelihood
  F-test handles tiny-n dispersion estimation well.
- **Lean toward limma-voom** when sample size is large or the design is complex/multi-factor
  — it is fast, well-calibrated, and flexible with model formulas.
- If **more than one method is reasonable**, recommend the default and offer the concordance
  check rather than forcing a single choice.

If `inspect_and_recommend` reports the design is **not full rank** (e.g. batch perfectly
aliased with condition), stop and resolve the confounding with the user before running —
do not silently proceed.

**Step 3 — Confirm.** Get the user's sign-off on method, design formula, contrast, and
significance thresholds (default padj < 0.05). Record the decision.

**Step 4 — Shared pre-filter (always, before any engine).**
```r
source("scripts/filter_counts.R")
filt <- filter_counts(loaded$counts, loaded$coldata, condition_col = "condition")
```
This applies one common `filterByExpr()` low-expression filter on the **raw counts**. The
same filtered gene set is reused by whichever engine(s) run — this is what makes a later
concordance comparison reflect *method* differences rather than *filtering* differences.

**Step 5 — Run the chosen engine** (all share the same call signature and emit the
standardized schema):
```r
source("scripts/run_deseq2.R")      # or run_edger.R / run_limma_voom.R
fit <- run_deseq2(filt$counts, filt$coldata,
                  design = ~ condition,
                  contrast = c("condition", "treated", "control"),  # or coef name
                  ref_level = "control")
res <- fit$results   # standardized data frame
```
For DESeq2, apeglm LFC shrinkage is applied for the engine's *own* ranking/visualization;
the standardized `log2FoldChange` reported for cross-method use is the **unshrunk** value.

**Step 6 — QC plots and export.**
```r
source("scripts/qc_plots.R");      run_all_qc(fit, output_dir = "results")
source("scripts/export_results.R"); export_de(fit, output_dir = "results")
```

**Step 7 — (On request) cross-method concordance.** If the user wants a robustness check,
run the other applicable engines **on the same filtered set** and compare:
```r
source("scripts/concordance.R")
concordance(list(deseq2 = res_deseq2, edger = res_edger, limma = res_limma),
            padj_cutoff = 0.05, output_dir = "results")
```
This produces an overlap-count table and a consensus DEG list (genes significant in ≥2
methods). It compares using **only** `padj` and **unshrunk** `log2FoldChange` — never
`baseMean_equiv`. Concordance is a **robustness check, not a significance booster**.

**Step 8 — Report.** Summarize: method used and rationale, contrast tested, number of DEGs
(up/down) at the chosen threshold, key caveats. Point the user to the saved files.

---

## Method Selection at a Glance (advisory)

| Situation | Lean toward | Why |
|---|---|---|
| Typical experiment, raw counts, simple design | **DESeq2** (default) | Robust, validated, good default shrinkage |
| Very small replicates per group | **edgeR** (QL F-test) | Quasi-likelihood handles tiny-n dispersion |
| Large n and/or complex multi-factor design | **limma-voom** | Fast, well-calibrated, flexible formulas |
| User wants a robustness check | run ≥2 + **concordance** | Reports gene-call overlap across methods |

These are starting points to explain and confirm with the user, **not** thresholds to apply
mechanically. Full narrative: [`references/method-selection-guide.md`](references/method-selection-guide.md).

---

## Scientific Caveats

- **Use adjusted p-values (BH-FDR `padj`), never raw p-values**, to call significance —
  thousands of genes are tested simultaneously.
- **Count-based methods (DESeq2, edgeR) and limma-voom all require raw integer counts**,
  never TPM/FPKM. If only normalized values exist, `log2(TPM)` → **limma-trend** is a
  documented *last-resort* fallback with strong caveats: TPM's transcript-length
  normalization violates the logCPM mean–variance assumption, so results are approximate
  and must be flagged as such.
- **The design must be full rank / not confounded.** This is checked programmatically in
  `inspect_and_recommend.R`; resolve any confounding before running.
- **`baseMean_equiv` is not comparable across methods** (see schema note above).
- **Cross-method LFCs are only compared on unshrunk terms** in concordance output.

---

## Self-Test / Eval

[`scripts/selftest.R`](scripts/selftest.R) runs all three engines on a bundled example
dataset (pasilla) and asserts: every engine emits the standardized schema; significance
uses `padj`; the shared filter yields one common gene universe; the recommender behaves
sensibly across n; the full-rank check flags a confounded design; concordance uses only
`padj`/LFC (never `baseMean_equiv`); and a non-integer matrix triggers the raw-counts
guardrail. Run it after installing the engine packages to validate the skill end-to-end.

---

## References

- [`references/method-selection-guide.md`](references/method-selection-guide.md) — when and why to choose each engine (narrative).
- [`references/comparison-and-caveats.md`](references/comparison-and-caveats.md) — assumptions, FDR, design/contrast handling, LFC- and baseMean-comparability.
- [`references/troubleshooting.md`](references/troubleshooting.md) — common errors across all three engines.
