# Relevant Biomni Resources (seed / fallback inventory)

Use this list as a seed/fallback and a map of *which resource feeds which axis* and
*which sibling skill actually runs it*. Verify packages with imports, HPC tools
with `hpc_search_tools`, and sibling skills with `Skill` before quoting them in a
report.

## Structured evidence sources per axis

| Axis | Primary Biomni resource(s) | How accessed |
|---|---|---|
| Drug MoA / safety / mouse pheno / genetics-assoc | **Open Targets Platform** (queryable DB) | GraphQL API — see `open-targets` skill + `opentargets_queries.md` |
| Functional / CRISPR | **DepMap** | direct CSV read (target rows/cols only) — see `gene-essentiality` skill |
| Human genetics | **GeneBass** (pLoF/missense burden), **GWAS Catalog**, **gnomAD** (constraint) | data-lake / queryable DBs — see `gwas-to-function-twas`, `genetic-variant-annotation` |
| Mouse KO | Open Targets `mousePhenotypes`; **MouseMine** gene sets | GraphQL / data-lake |
| Drug depth (optional) | **ChEMBL**, **OpenFDA** | queryable DBs |
| Directional evidence (all axes) | **`LiteratureSearch`** Biomni tool | writes to `/mnt/results/execution_trace/references.jsonl` |

## Sibling skills to cross-reference in the report ("how to run the follow-ups")

- **`open-targets`** — run genetics/association/drug-MoA queries interactively.
- **`gene-essentiality`** — correct interpretation of DepMap essentiality (score sign!).
- **`gwas-to-function-twas`** — GWAS → gene direction of effect, TWAS.
- **`genetic-variant-annotation`** — annotate specific variants (consequence, frequency).
- **`mendelian-randomization-twosamplemr`** — causal direction from human genetics (a strong
  complement to the genetics axis when instruments exist).
- **`clinicaltrials-landscape`** — clinical pipeline depth for the drug-MoA axis.
- **`literature-preclinical`** / **`literature-review`** — deeper preclinical/literature synthesis.
- **`pdf-report-generation`** — the brand system this skill's report is built on.
- **`methods-landscape-review`** — sibling synthesis skill (compare *methods*, not direction).

## Packages available (no install needed)

Python: `requests`, `pandas`, `numpy`, `reportlab`, `pypdf`, `gseapy`, `lifelines`.
R: `ggplot2`, `ggprism`, `dplyr`, `tidyr`, `RColorBrewer`, `ComplexHeatmap`, `org.Hs.eg.db`.

## Datalake datasets worth knowing for direction context

- **DepMap** (19 files) — CRISPR effect/dependency, expression, mutations.
- **GTEx** — tissue expression / eQTL (supports an optional expression/eQTL axis).
- **GeneBass** — exome pLoF/missense/synonymous burden.
- **MSigDB / MouseMine** — gene-set context.
- **PrimeKG** — precision-medicine knowledge graph (target–disease–drug relationships).
- **Broad Drug Repurposing Hub** — molecule → MoA/target.
