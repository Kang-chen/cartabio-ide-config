# Method Selection Guide (advisory)

This is narrative guidance for choosing among **DESeq2**, **edgeR (quasi-likelihood
F-test)**, and **limma-voom** for a bulk RNA-seq count experiment. It is meant to be
**explained to the user and confirmed**, not applied as a rigid decision tree. The
companion script `scripts/inspect_and_recommend.R` computes the data characteristics
referenced below and returns an advisory recommendation; translate that into prose,
name the alternatives, and let the user confirm or override.

---

## The three engines in one paragraph

All three model bulk RNA-seq **counts** and are well validated; on typical datasets with
adequate replication they agree on the large majority of genes. They differ mainly in how
they model dispersion and small-sample variance:

- **DESeq2** — negative-binomial GLM with empirical-Bayes dispersion shrinkage and a Wald
  test (or LRT). Robust, conservative-ish, strong default; provides apeglm LFC shrinkage
  for ranking/visualization.
- **edgeR (QL F-test)** — negative-binomial GLM with a **quasi-likelihood** F-test that
  accounts for the uncertainty in dispersion estimation. Particularly well-behaved when
  replicates are **few**, where dispersion is hard to estimate.
- **limma-voom** — transforms counts to logCPM, estimates **precision weights** from the
  mean–variance trend (`voom`), then uses limma's linear models + empirical-Bayes
  moderated t-tests. **Fast and flexible** with complex designs; scales gracefully to
  large sample sizes.

---

## When to lean which way

| Situation | Lean toward | Reasoning |
|---|---|---|
| Typical experiment, raw counts, simple/moderate design, ~3–20 reps/group | **DESeq2** (default) | Robust, widely validated, sensible defaults |
| Very few replicates per group | **edgeR** | QL F-test handles tiny-n dispersion uncertainty best |
| Large n and/or complex multi-factor design | **limma-voom** | Fast, well-calibrated FDR, flexible model formulas |
| Reviewer/robustness concern about the gene list | run ≥2 + **concordance** | Report cross-method overlap as a stability check |

These cutpoints (e.g. "few", "large") are **soft**. `inspect_and_recommend.R` uses
provisional thresholds (≤2/group → edgeR; **=3/group → DESeq2 with edgeR flagged as a
strong alternative**; ≥20/group or ≥4 coefficients → limma-voom; otherwise DESeq2) only to
produce a starting recommendation. n=3 is the most common — and borderline-small — design,
so it defaults to DESeq2 while surfacing edgeR as a genuine option. Always explain *why* and
let the data and the user's judgment decide.

---

## Input type drives validity, not just preference

- **Raw integer counts** are required by all three engines. This is the primary, supported
  path.
- **Normalized values (TPM/FPKM, or already-log values)** are **not** valid input for
  count-based DE. If raw counts cannot be obtained, the only fallback is **limma-trend** on
  `log2` values, and it carries strong caveats (see
  [comparison-and-caveats.md](comparison-and-caveats.md)). Treat any such result as
  approximate and flag it explicitly.

---

## Design and contrast considerations

- **Check the design before choosing a test.** `inspect_and_recommend.R` runs a
  programmatic full-rank check; if a covariate (e.g. batch) is confounded/aliased with the
  condition, the effect cannot be estimated by any engine — fix the design first.
- **Put the condition of interest last** in the formula (e.g. `~ batch + condition`) so the
  default coefficient is the one you care about, or pass an explicit contrast/coefficient.
- **Multi-group designs** (e.g. control / dose-low / dose-high): decide *which* comparison
  answers the question and pass it as a contrast `c("condition","dose_high","control")` or a
  coefficient name. All three engines accept an explicit contrast.

---

## Concordance as a robustness check

Running two or three engines on the **same shared-filtered** gene set and comparing the
significant-gene overlap is a good way to gauge how method-sensitive a result is. It is a
**robustness check, not a significance booster** — do not redefine "significant" as "called
by any method." The consensus list (`scripts/concordance.R`) reports genes significant in
≥2 methods and uses only `padj` and **unshrunk** `log2FoldChange` for comparison.

---

## References

- Love MI, Huber W, Anders S. *Moderated estimation of fold change and dispersion for
  RNA-seq data with DESeq2.* Genome Biology 2014.
- Robinson MD, McCarthy DJ, Smyth GK. *edgeR.* Bioinformatics 2010; Lun ATL, Chen Y, Smyth
  GK. *QL framework in edgeR.* 2016.
- Law CW, Chen Y, Shi W, Smyth GK. *voom: precision weights unlock linear model analysis
  tools for RNA-seq read counts.* Genome Biology 2014.
