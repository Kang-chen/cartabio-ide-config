# Parameter reference: `consensus-disease-signature`

The analysis engine (`scripts/run_meta_signature.R`) is driven entirely by one YAML
config. This document defines every field. See `example_config.yaml` for a complete
worked example (ulcerative colitis, 3 cohorts).

---

## Top-level fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `disease` | string | `"disease"` | Free-text condition name; used in figure/report titles and `summary.json`. |
| `output_dir` | path | `/mnt/results` | `tables/` and `figures/` are created here. |
| `contrast` | map | — | Global 2-group contrast (see below). |
| `fdr` | number | `0.05` | FDR threshold defining the **consensus** set. |
| `core_lfc` | number | `1.0` | Additional |pooled log2FC| threshold defining the **core** set. |
| `noninflammatory_control_types` | list | `[normal, trauma, healthy, control]` | Control-type keywords considered "non-inflammatory" for the heterogeneous-control **sensitivity meta-analysis**. Case-insensitive substring match against each cohort's `control_type`. |
| `cohorts` | list | — | One entry per cohort (>= 2 required for meta-analysis). |

### `contrast`
Default contrast applied to every cohort unless overridden per cohort.

| Field | Type | Meaning |
|---|---|---|
| `case` | list/string | Metadata value(s) defining the **Case** group. |
| `control` | list/string | Metadata value(s) defining the **Control** group. |
| `column` | string | Default metadata column holding the group label. |

---

## Per-cohort fields (`cohorts[]`)

**Common**

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes | Cohort identifier (GEO accession or your own tag). Used in tables/figures. |
| `source` | `"geo"` \| `"matrix"` | yes | Ingestion mode. |
| `platform` | string | yes* | Platform tag (GPL, or `SYMBOL`/`ENSEMBL`/`ENTREZID`). *Optional for matrix cohorts already in symbols. |
| `type` | `"microarray"` \| `"rnaseq"` | no (default microarray) | Selects limma (arrays) vs limma-voom (counts). Also drives the report's data-type-aware Methods text (via `summary.json$data_types`). |
| `control_type` | string | no (default `"unspecified"`) | Free-text label for this cohort's control group (e.g. `normal`, `osteoarthritis`, `trauma`, `healthy`). Recorded in `summary.json`; drives heterogeneous-control flagging + the non-inflammatory sensitivity meta-analysis. **Set this whenever controls differ across cohorts.** |
| `group_column` | string | yes† | Metadata column with the group label. †Falls back to `contrast.column`. |
| `case_values` | list | yes† | Value(s) marking Case. †Falls back to `contrast.case`. |
| `control_values` | list | yes† | Value(s) marking Control. †Falls back to `contrast.control`. |
| `filters` | map | no | `column -> allowed values`; restrict to a tissue/timepoint/etc. before grouping. |
| `log2_transform` | bool | no | Force log2(x+1). Auto-applied to GEO matrices whose max > 100 when unset. |

**`source: geo`**

| Field | Type | Meaning |
|---|---|---|
| `platform_index` | int (default 1) | Which ExpressionSet to take when a series spans multiple platforms. |

**`source: matrix`**

| Field | Type | Meaning |
|---|---|---|
| `matrix_path` | path | Expression matrix, genes/probes (rows) x samples (cols). CSV or TSV. |
| `metadata_path` | path | Sample metadata, one row per sample. CSV or TSV. |
| `sample_id_col` | string/int (default 1) | Column in metadata matching the matrix column names. |

> ArrayExpress / BioStudies (e.g. `E-MTAB-*`) cohorts flow through the **matrix** path:
> download the processed matrix + SDRF, then point `matrix_path`/`metadata_path` at them.
> The native ArrayExpress API parser is fragile (field-name drift, over-aggressive
> `E-GEOD-` filtering) and is intentionally **not** built in.

---

## Supported platforms (probe -> symbol)

`scripts/annotate_platforms.R` -> `PLATFORM_DB` maps these out of the box:

| GPL | Array | Annotation package | `_PM` strip |
|---|---|---|---|
| GPL6244 | Affy Human Gene 1.0 ST | hugene10sttranscriptcluster.db | no |
| GPL570 | Affy HG-U133 Plus 2.0 | hgu133plus2.db | no |
| GPL571 | Affy HG-U133A 2.0 | hgu133a2.db | no |
| GPL96 | Affy HG-U133A | hgu133a.db | no |
| GPL13158 | Affy HT HG-U133+ PM | hgu133plus2.db | **yes** |
| GPL16311 | HT HG-U133+ PM variant | hgu133plus2.db | **yes** |
| GPL10558 | Illumina HumanHT-12 v4 | illuminaHumanv4.db | no |
| GPL6947 | Illumina HumanHT-12 v3 | illuminaHumanv3.db | no |

Plus the pseudo-platforms `SYMBOL` (identity), `ENSEMBL`, `ENTREZID` (mapped via
`org.Hs.eg.db`). **To add a new array:** add one row to `PLATFORM_DB` mapping the GPL
to its Bioconductor `.db` package. If no package exists, pre-map features to gene
symbols and set `platform: SYMBOL`.

> **The `_PM` fix matters.** GPL13158/GPL16311 probe IDs carry an `_PM` infix
> (`1007_PM_s_at`) absent from `hgu133plus2.db` keys (`1007_s_at`). Stripping `_PM`
> took the reference UC cohort from 3 -> 21,358 mapped symbols. This is automatic
> for those platforms.

---

## Outputs

Written under `output_dir`:

**`tables/`**
- `summary.json` — headline numbers + top genes; consumed by `build_report.py`. Includes distinct
  `n_fdr_sig` vs `n_consensus`, per-cohort `control_type`, `data_types`, `heterogeneous_controls`,
  and a `sensitivity` block.
- `meta_analysis_full.csv` — every tested gene: pooled estimate, SE, z, p, FDR, I2, tau2, k, direction, consensus/core flags, per-cohort log2FC.
- `consensus_UP_genes.csv`, `consensus_DOWN_genes.csv`
- `sensitivity_noninflammatory_meta.csv` — meta-analysis over non-inflammatory-control cohorts only (when >= 2 such cohorts).
- `enrichment_GO_BP_UP.csv`, `enrichment_GO_BP_DOWN.csv`, `enrichment_Reactome_UP.csv`, `enrichment_Reactome_DOWN.csv`
  (KEGG removed — not commercially licensed; Reactome replaces it.)
- `GSEA_hallmark.csv`

**`figures/`** (PNG @ 150 dpi + SVG)
- `QC_sample_distributions`, `volcano_per_study`, `concordance_scatter`,
  `consensus_heatmap`, `forest_top_genes`, `enrichment_ORA_dotplots` (GO + Reactome), `GSEA_hallmark_barplot`,
  and `sensitivity_preservation` (only when a non-inflammatory sensitivity meta ran)

**PDF** (via `scripts/build_report.py`) — assembled from `summary.json` + figures +
the agent-generated infographic + literature references.

---

## Key statistical choices (fixed; not config-exposed)

- **Effect size** = per-cohort log2FC with SE `stdev.unscaled * sqrt(s2.post)` from limma.
- **Meta-analysis** = random-effects `metafor::rma(method="REML")`, DerSimonian-Laird fallback; genes need >= 2 cohorts.
- **Consensus** = `FDR < fdr AND same sign in all contributing cohorts` (a **subset** of the FDR-significant genes; `n_consensus <= n_fdr_sig`, asserted at runtime).
- **Core** = consensus `AND |pooled log2FC| >= core_lfc`.
- **Enrichment** = GO-BP + Reactome ORA (KEGG removed for commercial licensing); **universe** = all meta-tested genes (not the whole genome) — the correct background.
- **GSEA** ranks by meta z-score; Hallmark collection only (loading full MSigDB C2 can OOM).
- **Duplicate guard** = cohort pairs with mean-expression r > 0.999 are flagged (re-deposited series inflate replication).
- **Heterogeneous controls** = if >1 distinct `control_type`, flag `heterogeneous_controls` and (when >= 2 non-inflammatory-control cohorts) run a sensitivity meta over that subset, reporting consensus-preservation fraction. Random effects absorb control-type differences as between-study heterogeneity; the pooled contrast is not silently treated as a single case-vs-normal comparison.
