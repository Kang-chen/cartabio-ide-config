# Comparison and Caveats

Cross-method comparability rules, statistical assumptions, and known pitfalls for the
multi-method DE workflow. Read this before comparing outputs across DESeq2, edgeR, and
limma-voom.

---

## Significance: always use adjusted p-values

Thousands of genes are tested simultaneously, so significance is called on the
**Benjamini–Hochberg adjusted p-value (`padj` / FDR)**, **never** the raw p-value. The
default headline cutoff in this skill is **`padj < 0.05`**; a `|log2FC| ≥ 1` subset is also
reported. "p < 0.05" in a DEG context means `padj < 0.05` unless explicitly stated as a raw
p-value. Use inclusive inequalities unless the user specifies strict ones.

---

## The standardized schema and what is / isn't comparable

Every engine emits: `gene_id, baseMean_equiv, log2FoldChange, pvalue, padj, method`.

### `log2FoldChange` — comparable only on UNSHRUNK terms
DESeq2 can **shrink** log2 fold changes (apeglm) for ranking and visualization; edgeR and
limma do not shrink. Shrunk and unshrunk LFCs are not on the same footing. Therefore:

> The standardized `log2FoldChange` reported by every engine — and the LFC used anywhere in
> concordance/consensus output — is the **unshrunk** estimate, so cross-method comparisons
> are made on compatible terms. DESeq2's apeglm-shrunk LFC is computed separately and used
> **only** for DESeq2's own MA/volcano plots and gene ranking.

### `baseMean_equiv` — NOT comparable across methods

> **baseMean_equiv reports each engine's native expression-strength metric (DESeq2:
> linear-scale mean normalized count; edgeR/limma: log2-scale average). Values are not
> comparable across methods and should only be read within a single engine's output.**

Concretely, a gene might show `baseMean_equiv = 1200` under DESeq2 (mean normalized count,
linear scale) and `baseMean_equiv = 6.2` under edgeR/limma (logCPM / AveExpr, log2 scale) —
the same gene, differing by orders of magnitude purely because of the scale, not biology.
The per-engine scale is recorded in `run_log.txt` as `baseMean_equiv_scale: linear|log2`.
`baseMean_equiv` is useful for an engine's own MA plot and within-method ranking, but it is
**never** used in any cross-method computation (concordance, consensus, overlap).

### `stat` — deliberately absent from the standardized schema
Wald z (DESeq2), QL F (edgeR), and moderated t (limma) are different statistics on different
scales and are **not comparable**. Each engine keeps its native statistic only in its own
fitted object (labeled by `stat_type`); it is never merged into the shared table.

---

## Raw counts vs normalized input

- **DESeq2, edgeR, and limma-voom all require raw integer counts.** They model count-level
  mean–variance structure (negative binomial, or voom precision weights derived from
  logCPM). Feeding TPM/FPKM or pre-logged values violates these assumptions and produces
  invalid significance.
- **limma-trend is the only fallback for normalized-only data, and it is a last resort.**
  `log2(TPM)` carries **transcript-length normalization**, which breaks the logCPM
  mean–variance relationship that `voom`/`limma-trend` assume. Results are **approximate**
  and must be flagged as such. Prefer obtaining the raw count matrix whenever possible.
- The loader (`load_data.R`) and `inspect_and_recommend.R` both check integer-ness and warn
  loudly if the input does not look like raw counts.

---

## Design, confounding, and contrasts

- **Full rank / confounding:** `inspect_and_recommend.R` compares the model-matrix rank to
  its number of columns and warns when a covariate is aliased with the condition (e.g. batch
  perfectly nested within condition). Such effects are **inestimable by any engine** — fix
  the design before running.
- **Reference level & direction:** set the reference (control) level explicitly. A positive
  `log2FoldChange` means higher in the non-reference (test) level. The contrast builders use
  treatment coding, where the reference level is the intercept, and are correct whether the
  numerator or denominator is the reference.
- **Multi-group:** specify the exact comparison as a contrast `c(factor, numerator,
  denominator)` or a coefficient name. If you let an engine default to the last design
  coefficient, it announces which coefficient it tested — check it matches your intent.

---

## Concordance: a robustness check, not a significance booster

- Run all engines on the **same shared-filtered** gene universe (`filter_counts.R`) so the
  overlap measures method differences, not filtering differences.
- Concordance decides significance using **only `padj`** (optionally with a `|log2FC|`
  filter) and reports **unshrunk** LFCs; it never uses `baseMean_equiv`.
- Do **not** redefine "significant" as "called by any one method" — that inflates false
  positives. Genes significant in ≥2 methods (the consensus list) are a reasonable
  high-confidence set; genes unique to one method warrant scrutiny, not automatic trust.

---

## Practical expectations

- On well-replicated data the three engines typically agree on most DEGs; disagreements
  concentrate among low-count genes and borderline-padj genes.
- edgeR-QL and limma-voom are usually faster than DESeq2 at large n.
- Report the engine, version, design formula, contrast, filter, and thresholds (all captured
  in `run_log.txt`) for reproducibility.
