# Biomni environment resources used by this skill

This skill runs inside Biomni/Phylo, which provides a large catalog of installed
packages, queryable databases, curated datasets, and HPC tools. **Use them
instead of re-installing or reinventing.** This file maps the resources this skill
relies on to each stage, and gives the exact discovery calls to run.

## How to discover what's actually available

Environments drift, so **confirm availability at runtime** rather than assuming.
Verify Python packages with `importlib.util.find_spec`, R packages with
`requireNamespace()`, and upstream HPC tools with `hpc_search_tools`.

For external knowledge, use the dedicated tools rather than model memory:
- `LiteratureSearch(...)` — papers for the report's Introduction, mechanistic
  interpretation, and References (writes structured records to
  `/mnt/results/execution_trace/references.jsonl`).
- **Free enzyme databases** (ExplorEnz / IUBMB Enzyme Nomenclature, Rhea) — verify the
  **EC numbers** behind the metabolite modules. KEGG is intentionally **not** used
  (not licensed for commercial use — see `DATA_SOURCES.md`).
- `WebSearch` / `WebFetch` — current, non-paper context (e.g. a DB/version note).

## Preinstalled packages this skill uses

Verify each with a Python import or `requireNamespace()` in R.

### Python
| Package | Role in this skill |
|---|---|
| `scikit-bio` | Alpha/beta diversity, ordination (PCoA), `permanova` — a Python alternative to the R phyloseq/vegan path in Stage 1. |
| `biom-format` | Read/write BIOM feature tables (interop with QIIME2/PICRUSt2 I/O). |
| `scipy`, `statsmodels` | Wilcoxon/Mann–Whitney tests and BH-FDR in the metabolite-module script (Stage 4). |
| `gseapy` | Optional gene-set/pathway enrichment on ranked EC tables (Stage 3), descriptive only. |
| `pandas`, `numpy` | Table wrangling throughout. |
| `reportlab`, `pypdf`, `pillow` | PDF report + validation + aspect-safe image embedding (Stage 5). |
| `matplotlib`, `seaborn` | Data plots (Stages 1–4). |

### R
| Package | Role in this skill |
|---|---|
| `phyloseq` | Import feature table + taxonomy + tree; diversity + ordination backbone (Stages 1–2). Installed via Bioconductor (see `commands_and_environment.md`). |
| `ComplexHeatmap` | Taxa × sample abundance heatmaps (Stage 2 visualization). Preinstalled. |
| `clusterProfiler` | Optional enrichment/annotation if extending function analysis. Preinstalled. |
| `ggplot2`, `ggprism`, `ggrepel`, `RColorBrewer` | Publication figures (LFC forests, diversity boxplots). Preinstalled. |
| `dplyr`, `tidyr`, `tibble`, `readr` | Data wrangling. Preinstalled. |
| ALDEx2 / phyloseq / microbiome / MaAsLin2 / ANCOMBC | Stats stack — **installed per `commands_and_environment.md`** (Bioconductor), not preinstalled by default. |

> Note: PICRUSt2 is **not** a preinstalled package — install it in its own
> micromamba env (`picrust2=2.5.2`) per `commands_and_environment.md`. The Biomni
> HPC catalog focuses on read alignment/assembly/variant calling, not amplicon
> functional prediction, so PICRUSt2 is run locally on a right-sized machine.

## Queryable databases this skill uses

Biomni exposes bundled query schemas for several external databases. Relevant here:

| Database | Use |
|---|---|
| Reactome | Optional pathway context for host-side interpretation (e.g. SCFA receptor signaling). Free (CC0). |
| UniProt / NCBI | Look up specific enzymes/genes or taxa when annotating a hit. Free. |

> **KEGG is intentionally NOT used by this skill** (KO IDs and KEGG pathway/module data
> are not licensed for commercial use). The metabolite modules are keyed on **EC numbers**
> (free IUBMB nomenclature). Verify/extend them against free enzyme databases — ExplorEnz
> (official IUBMB Enzyme List), the IUBMB Enzyme Nomenclature site, or Rhea (EBI, CC BY) —
> and refresh `references/metabolite_modules_ec.csv` the same way. Do not query or ship
> MetaCyc/BioCyc (subscription-only since 2024). See `DATA_SOURCES.md`.

## Curated datalake datasets (optional extensions)

Only if the analysis grows beyond the core 16S workflow:

| Dataset | Possible use |
|---|---|
| MSigDB (human gene sets) | Host-side gene-set enrichment if the study pairs microbiome with host transcriptomics. |
| Human Protein Atlas | Tissue/immune context for host receptors (GPR43/GPR109A, AhR). |

These are **not** part of the default 16S pipeline — mention them only if the
user's question spans host multi-omics.

## HPC / upstream tools (out of the default scope, but available)

If the user actually needs raw-read processing (this skill assumes a **processed**
feature table), point them to the Biomni HPC catalog and QIIME2-style upstream
steps. Relevant HPC tools: `multiqc` (aggregate read QC), `bowtie2`/`bwa` and
`minimap2` (alignment), and the assemblers for shotgun work. Amplicon
denoising (DADA2/Deblur) and taxonomy assignment remain the user's upstream
responsibility before this skill starts.

## One-line summary of the resource routing

- **Discovery** → direct package imports and `hpc_search_tools`.
- **Literature / citations / mechanism** → `LiteratureSearch` (+ `WebSearch`/`WebFetch` for non-paper context).
- **Enzyme (EC) verification** → ExplorEnz / IUBMB / Rhea (free). **Not KEGG** (unlicensed for commercial use).
- **Diversity/ordination** → R `phyloseq`/vegan **or** Python `scikit-bio`.
- **BIOM I/O** → `biom-format`.
- **Stats/FDR** → `scipy`/`statsmodels` (Python), Bioconductor stack (R).
- **Report + infographic** → `pdf-report-generation` skill + `build_report.py` + `GenerateImage`.
