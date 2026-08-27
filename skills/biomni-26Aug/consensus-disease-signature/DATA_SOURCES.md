# Data sources & licenses — `consensus-disease-signature`

This skill combines multiple public bulk-transcriptome cohorts into a consensus signature and
annotates it with pathway databases. This document records every external data source and
software dependency that carries the data, together with its license and commercial-use status.
It is the authoritative reference for the skill's `needs_commercial_review` posture.

> **Summary:** After removing KEGG, no runtime **data** dependency in this skill carries a known
> commercial-use *prohibition*. MSigDB Hallmark remains the one item to confirm per deployment
> (Hallmark itself appears CC-BY-4.0, but the broader MSigDB is mixed). "No prohibition found" is
> not the same as explicit clearance — verify each item for your own deployment context.

## Expression data (inputs)

| Source | What | License / terms | Commercial use |
|---|---|---|---|
| **NCBI GEO** (`GEOquery`, or supplementary count/intensity matrices) | The per-cohort expression data being meta-analyzed. | NCBI places no restriction on the redistribution or reuse of GEO data; individual submitters retain no additional license by default. | Permitted (verify any unusual per-series note). |
| **ArrayExpress / BioStudies** (`E-MTAB-*`, via the matrix path) | Optional alternative cohort source. | EMBL-EBI data are generally released under EBI's terms of use; most processed matrices are freely reusable. | Generally permitted; verify the specific study. |
| **User-supplied matrices** | Proprietary cohorts fed through the `matrix` path. | Whatever the user's own terms are. | User's responsibility. |

## Pathway / gene-set databases (annotation)

| Source | Used by | License | Commercial use |
|---|---|---|---|
| **Reactome** | `ReactomePA::enrichPathway` + `reactome.db` (ORA of core up/down genes). **Replaces KEGG.** | The Reactome knowledgebase is released to the **public domain (CC0)**; figures/derived images are CC-BY 4.0 for attribution. | **Permitted.** This is the KEGG substitute chosen specifically for commercial usability. |
| **Gene Ontology (GO)** | `clusterProfiler::enrichGO` + `org.Hs.eg.db` (GO-BP ORA). | GO annotations are released under **CC-BY 4.0**. | Permitted with attribution. |
| **MSigDB Hallmark (H)** | `msigdbr` + `fgsea` (Hallmark GSEA). | Individual Hallmark gene-set pages appear **CC-BY-4.0**, but the broader MSigDB has **mixed** licensing; the Broad notes a significant portion needs a commercial license. This skill loads **only** the Hallmark (H) collection. | **Review per deployment.** Confirm Hallmark specifically, or obtain an MSigDB commercial license. Do not assume full MSigDB is cleared. |
| **KEGG** | *(removed)* | KEGG API is academic-use only; non-academic use requires a commercial license (KEGG copyright page, updated 2024-10-01; license via Pathway Solutions). | **Not used.** KEGG ORA and its `enrichment_KEGG_*` outputs were removed from this skill; Reactome replaces it. No `rest.kegg.jp` call remains. |

## Software dependencies (carrying data or with notable licenses)

| Package | Role | License | Commercial use |
|---|---|---|---|
| `metafor` | Random-effects meta-analysis. | GPL (>= 2) | Permitted (copyleft; keep in mind for redistribution). |
| `limma`, `edgeR` | Per-cohort differential expression. | GPL (>= 2) | Permitted. |
| `clusterProfiler` | ORA (GO), enrichment plumbing. | GPL-2 / Artistic-2.0 family | Permitted. |
| `ReactomePA` | Reactome ORA. | Artistic-2.0 | Permitted. |
| `reactome.db` | Reactome annotation data package (~1 GB). | CC-BY / Artistic-2.0 (data: Reactome CC0) | Permitted. |
| `org.Hs.eg.db`, platform `.db` packages | ID mapping, probe->symbol. | Artistic-2.0 | Permitted. |
| `fgsea` | GSEA. | MIT | Permitted. |
| `GEOquery` | GEO ingestion. | Artistic-2.0 | Permitted. |
| `ggplot2`, `ComplexHeatmap`, `circlize`, `patchwork`, `svglite`, `reshape2` | Figures. | GPL / MIT family | Permitted. |
| `reportlab`, `pypdf`, `pillow` | PDF report. | BSD / permissive | Permitted. |

## Change log

- **KEGG removed** (was `clusterProfiler::enrichKEGG()` → `enrichment_KEGG_{UP,DOWN}.csv`) because
  the KEGG API is not licensed for commercial use. Replaced with **Reactome**
  (`ReactomePA::enrichPathway` → `enrichment_Reactome_{UP,DOWN}.csv`). GO-BP ORA and Hallmark GSEA
  are retained. See SKILL.md "Commercial-use restrictions" for details.
