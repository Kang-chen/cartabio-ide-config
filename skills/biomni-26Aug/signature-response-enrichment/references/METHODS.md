# METHODS reference — signature-response-enrichment

Verbatim parameter/formula reference for the skill. All values are the defaults baked into
the helper scripts and are configurable via the SKILL.md inputs. These match the worked
example (residual TYK2 signaling → adalimumab non-response in psoriasis) and were verified
against that run's execution record.

## GSVA scoring (`run_gsva.R`)
- **Version:** GSVA **1.50.5** (Bioconductor 3.18). Pinned deliberately — GSVA ≥ 1.54
  requires `SpatialExperiment` → `magick` → `libmagick` (system lib frequently
  unavailable). 1.50.5 keeps the classic `gsva()` / `gsvaParam()` API.
- **Call:** `gsvaParam(exprData, geneSets, kcdf="Gaussian", minSize=3, maxSize=500)` then
  `gsva(param, verbose=FALSE)`.
- **kcdf:** `Gaussian` for log-normalized / log-CPM input (default). Fractional tximport
  gene-level counts use the **log-CPM + Gaussian** route rather than a Poisson kernel.
  Use `Poisson` only for raw integer counts.
- **Coverage:** map signature symbols to each cohort's feature space; report `n_mapped /
  n_total`; drop sets with `< minSize` mapped genes and flag them.
- **Alias resolution (three layers, honest about limits):**
  1. **Manual family-token expansions** (always applied) — for ambiguous tokens that are
     not real single symbols: `IFNA → IFNA1 + IFNA2`, `IL18R → IL18R1`. This list is small
     and curated; **extend it per project** if a signature uses other family tokens.
  2. **`limma::alias2SymbolTable`** — applied *only when the package is installed* and
     *only to symbols not already present* in the cohort's feature space; a mapped symbol
     is adopted only if it is actually present (never silently replaces a matching symbol).
  3. **Case-insensitive direct matching** otherwise.
  If `limma::alias2SymbolTable` is unavailable, mapping falls back to layers 1+3 only, so
  signatures using non-current single-gene aliases **may under-map** — this is **not
  silent**: `run_gsva.R` prints whether the alias table was used and the coverage report
  lists the **unmapped genes per set** (`unmapped_genes` column). Review it and add manual
  expansions or install `limma` if coverage is low on a new dataset.

## Response definition (`build_response_table.py`)
- `pct_improve = (baseline − endpoint) / baseline` on the cohort's continuous severity
  metric.
- **Responder iff `pct_improve >= threshold − 1e-9`.** Default `threshold = 0.75` (i.e.
  the 75%-improvement convention, e.g. PASI75). The `− 1e-9` avoids floating-point
  boundary flips and materially changed one boundary patient in the worked example
  (15R/10NR → **16R/9NR** in the PSORT refinement cohort).
- **Fallback:** if no numeric severity metric exists, use the categorical responder label
  and flag the cohort `label_based=True`.
- Baseline = 0 is guarded (undefined % improvement → patient dropped).

## Change-from-baseline ΔGSVA + concordance (`delta_gsva_stats.R`)
- **ΔGSVA(t) = GSVA(t) − GSVA(baseline)** per patient, per gene set (within-patient paired
  difference — the *only* paired step).
- **Endpoint contrast:** NR vs R; **direction = mean(NR) − mean(R)** (positive ⇒ higher in
  non-responders).
- **Primary test — two-sample (UNPAIRED) Wilcoxon rank-sum** comparing the NR patients'
  ΔGSVA values against the R patients' ΔGSVA values: `wilcox.test(nr, r)` (two-sided p
  reported; one-sided `alternative="greater"` NR>R p used for Fisher). t-test alongside.
  NR and R are **different, usually unequal-size groups** (e.g. 9 NR vs 16 R), so this is
  **not** a paired test — do not label it "paired Wilcoxon" and do not pass `paired=TRUE`.
  (`paired=TRUE` is correct *only* in the pharmacodynamic pre/post module, where the same
  patients are measured twice.)
- **Cross-cohort concordance — Fisher's method** on the one-sided (NR>R) p-values:
  ```r
  X <- -2 * sum(log(pvals))
  p <- pchisq(X, df = 2 * length(pvals), lower.tail = FALSE)
  ```
  Single cohort ⇒ concordance is n/a.
  **Independence assumption (critical):** Fisher's method assumes the p-values are
  *independent*. **Sub-cohorts that are splits of the same parent study (e.g. a
  discovery/refinement split of one consortium dataset) are not independent** — combining
  them is pseudo-replication and inflates the apparent evidence. Pass `--cohort-parent` to
  `delta_gsva_stats.R`; it then reports **`fisher_one_sided_p_independent`** (one cohort per
  parent — the value to report) and **`fisher_one_sided_p_all_cohorts`** (every split
  combined — a **sensitivity check only**), and warns when parents are shared. Always show
  the **per-cohort effects and one-sided p-values** next to any combined value, and never
  let a borderline combined p (just under 0.05) carry the headline alone. In the worked
  example the two PSORT splits share parent `E-MTAB-14509`, so the all-splits Fisher p
  (≈0.048 JAK1) is optimistic; the independent view leans heavily on the single
  cross-platform cohort (GSE85034) and the consistent direction, not on a formal combined
  p-value.
- **Multiplicity:** Benjamini-Hochberg FDR across the gene-set × timepoint family.

## Competitive gene-set test (`camera_endpoint.R`)
- `limma::camera(E, ids2indices(sets, features), design, contrast=2)` where
  `design = model.matrix(~grp)`, `grp = factor(response_group, levels=c("R","NR"))` so the
  tested coefficient (coef 2 = `grpNR`) is **NR vs R**.
- Accounts for inter-gene correlation. **CAVEAT:** absolute on-treatment endpoint contrast
  is partly **confounded by residual disease activity** — report as supportive, never the
  sole headline.
- **Auto-skip** when < 2 samples/group at endpoint, or no set maps ≥ 2 genes.

## Per-gene differential expression (`gene_de.R`)
- **RNA-seq → limma-voom:** `DGEList` → `filterByExpr` → `calcNormFactors` → `voom(dge,
  design)` → `lmFit` → `eBayes`; test coef `grpNR`.
- **Microarray → limma:** `lmFit(E, design)` → `eBayes`; test coef `grpNR`.
- BH-FDR on the per-gene family. Summary row: `n_genes, min_FDR, n_FDR<0.05,
  n_nominal_p<0.05`. **Auto-skip** when < 2 samples/group.

## Significance marks & multiplicity (everywhere)
- `*` = nominal p < 0.05; `**` = FDR < 0.05. **Report nominal and FDR separately** — never
  conflate. BH is the default correction.

## Figures (`make_figures.R`)
- Colors (Okabe-Ito, colorblind-safe): **NR `#D55E00`**, **R `#0072B2`**, volcano
  signatures `#E69F00` / `#7E2F8E`, pharmacodynamic `#117733`, accent gold `#D4A04A`.
- Heatmap fill: `scale_fill_gradient2(low=COL_R, mid="white", high=COL_NR, midpoint=0,
  limits=c(-0.6,0.6), oob=squish)`.
- Font: **Liberation Sans** (metric-equivalent to Arial). Export PNG (300 dpi) **and** SVG
  with editable text.

## Report glyph safety (`build_report.py`, `assets/report_style.py`)
- **SAFE in Helvetica:** `&mdash; &ndash; &#916;(Δ) &#945;(α) &#946;(β) &#947;(γ)
  &#954;(κ) &#8226;(•) &minus; &#215;(×) &#177;(±) &#8776;(≈) &#8805;(≥) &#8242;(′)
  &rarr;(→) &lt; &gt; &amp; &lsquo; &rsquo;`.
- **UNSAFE — never emit** (render as `.notdef` black squares): **`&nbsp;`**,
  **`&#8209;` (non-breaking hyphen)**. Use plain ASCII space / hyphen.

## Verify-before-trust gate (`validate_report.py` + agent media-check)
1. Reconcile every headline number in the PDF against the source CSVs (fail on mismatch).
2. Table-numbering integrity: contiguous 1..N, no duplicates, every in-text `Table N`
   resolves.
3. Media-check every figure page (render via pymupdf, run the `Read` media-output-check);
   regenerate on any blank/clipped/glyph failure and re-check.
4. Confirm every external fact (PMID/DOI/accession) against the primary source or the run
   record — never reconstruct from memory.

## Report structure (fixed template)
Title → **Table 1 discovery catalog** → Summary + headline callout → Methods (datasets,
signatures+coverage, GSVA settings, "which test produced which result" mapping) → Results
(analysed-cohort table, ΔGSVA endpoint, CAMERA, per-gene DE, pharmacodynamic) →
Limitations → Next steps → References. Every figure/table/subheading names its method
(GSVA ΔGSVA / CAMERA / limma-voom / Fisher). Headline presents ΔGSVA concordance **and**
FDR-adjusted results side by side with honest sensitivity caveats.
