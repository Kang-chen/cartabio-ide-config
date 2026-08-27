---
id: "skill_b12e61063fd8475896b26186626f2426"
name: "omics-dataset-retrieval"
description: "Use to find, retrieve, catalog, and relevance-audit public omics datasets across GEO, SRA, ArrayExpress, CELLxGENE, and other repositories."
category: "data_discovery"
visibility: "public"
starting-prompt: "Find all publicly available omics datasets for my disease of interest. Generate a PDF report with an intro, methods, results, conclusions and figures from all of the analyses you perform."
---

# Omics Dataset Retrieval Skill

## Scope

Systematically retrieve, deduplicate, classify, and relevance-audit publicly available omics
datasets for a user-specified disease, phenotype, gene, or biological process. Covers all major
omics types (transcriptomics, proteomics, metabolomics, epigenomics, genomics, single-cell,
spatial transcriptomics, lipidomics, multi-omics) and the broadest possible set of public
repositories. Does **not** download raw data files or perform downstream analysis.

---

## Inputs

| Parameter | Type | Description |
|---|---|---|
| `disease_or_topic` | string | Disease name, phenotype, gene, or biological process (e.g., "sickle cell disease", "Alzheimer's disease", "BCL11A") |
| `synonyms` | list[str] | Alternative names, abbreviations, gene symbols (e.g., ["SCD", "SCA", "HbSS", "sickle cell anemia"]) |
| `omics_types` | list[str] or "all" | Restrict to specific omics types, or "all" (default) |
| `organism` | string | "all" (default) — includes human, mouse, and all other organisms; restrict to "human" or "mouse" only if explicitly requested |
| `year_min` | int | Earliest publication year to include (default: no limit) |
| `output_dir` | path | Where to save outputs (default: `/mnt/results/`) |

---

## Outputs

| File | Description |
|---|---|
| `<disease>_omics_datasets_MASTER.csv` | Full catalog with all metadata and relevance labels |
| `<disease>_omics_datasets_VALIDATED.csv` | Filtered to CORE + ADJACENT only |
| `<disease>_omics_summary.md` | Markdown summary: counts by omics type, repository, top datasets, limitations |
| `<disease>_omics_landscape.png` | Overview figure: donut chart + repository bar + timeline (if requested) |
| `report_<disease>_omics_landscape.pdf` | PDF report (when the user requests a PDF report) |

**PDF reports:** When the user requests a PDF report (e.g. "Generate a PDF report with an
intro, methods, results, conclusions and figures"), load the `pdf-report-generation` skill
and use it to produce a PDF with intro, methods, results, conclusions, and at least
one figure (the landscape PNG). Before passing any DataFrame-derived text to ReportLab `Paragraph()`, coerce
nullable/NaN values to safe strings (e.g. `str(v) if pd.notna(v) else ""`) to avoid
`TypeError` crashes on `float('nan')`.

---

## Repository Coverage Map

Work through repositories in priority order. Tier 1 repositories have programmatic APIs and
are the highest-yield sources. Tier 2 have APIs but are more specialized. Tier 3 require
web search or manual curation. Always attempt all tiers — rare diseases often have data
only in Tier 2/3 repositories.

### Tier 1 — High-yield, programmatic APIs (always query)

| Repository | Omics Focus | API Base URL | Notes |
|---|---|---|---|
| **GEO (NCBI)** | All transcriptomics, epigenomics | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/` | Largest source; run 20–40 targeted queries |
| **SRA (NCBI)** | Raw sequencing reads | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/` (db=sra) | Complements GEO; finds studies not deposited as GEO series |
| **ArrayExpress / BioStudies (EBI)** | Transcriptomics, functional genomics | `https://www.ebi.ac.uk/biostudies/api/v1/search` | European mirror of GEO; many unique studies |
| **PRIDE / ProteomeXchange** | Proteomics (MS) | `https://www.ebi.ac.uk/pride/ws/archive/v3/projects` | Primary proteomics repository; v2 endpoint deprecated (404) |
| **OmicsDI (aggregator)** | Multi-omics aggregator | `https://www.omicsdi.org/ws/dataset/search` | Covers MetaboLights, MassIVE, GNPS, Metabolomics Workbench, ArrayExpress, GEO, PRIDE — use to catch metabolomics and avoid re-querying individual repos |
| **CZ CELLxGENE** | scRNA-seq, spatial | `https://api.cellxgene.cziscience.com/curation/v1/collections` | 33M+ cells; query by disease using `cellxgene_census` Python package |
| **GDC / TCGA (NCI)** | Cancer multi-omics | `https://api.gdc.cancer.gov/` | Best for cancer diseases; covers TCGA, TARGET, CGCI, CPTAC-GDC |

### Tier 2 — Specialized APIs (query when relevant to disease/omics type)

| Repository | Omics Focus | API / Access | When to use |
|---|---|---|---|
| **ENCODE** | Epigenomics (ChIP-seq, ATAC-seq, RNA-seq) | `https://www.encodeproject.org/search/?format=json` | Regulatory genomics; TF binding; chromatin accessibility |
| **Expression Atlas (EBI)** | Bulk + single-cell RNA-seq | `https://www.ebi.ac.uk/gxa/json/experiments` | Curated, baseline + differential expression experiments |
| **Human Cell Atlas (HCA)** | scRNA-seq, spatial | `https://service.azul.data.humancellatlas.org/index/projects` | Reference cell type atlases; healthy tissue baselines |
| **Metabolomics Workbench (NIH)** | Metabolomics, lipidomics | `https://www.metabolomicsworkbench.org/rest/study/study_id/ST/named_json` | NIH-funded metabolomics; REST API available |
| **MetaboLights (EBI)** | Metabolomics | `https://www.ebi.ac.uk/metabolights/ws/studies/` | European metabolomics repository |
| **MassIVE / GNPS** | Metabolomics, lipidomics | `https://massive.ucsd.edu/ProteoSAFe/datasets.jsp` | MS-based metabolomics; use OmicsDI to search |
| **jPOST** | Proteomics (MS) | `https://repository.jpostdb.org/search` | Japanese proteomics repository; ProteomeXchange member |
| **iProX** | Proteomics (MS) | `https://www.iprox.cn/page/project.html` | Chinese proteomics repository; ProteomeXchange member |
| **cBioPortal** | Cancer multi-omics | `https://www.cbioportal.org/api/` | Cancer genomics; mutation, CNA, expression, methylation |
| **EpiRR / IHEC** | Epigenomics reference | `https://www.ebi.ac.uk/epirr/api/` | IHEC reference epigenomes; healthy tissue baselines |
| **Human Protein Atlas (HPA)** | Proteomics, RNA-seq | `https://www.proteinatlas.org/api/` | Tissue/cell-type protein and RNA expression |
| **ENA (EBI)** | Raw sequencing | `https://www.ebi.ac.uk/ena/portal/api/search` | European mirror of SRA; raw reads |

### Tier 3 — Web search + manual curation (always attempt via WebSearch tool)

| Repository | Omics Focus | Search Strategy |
|---|---|---|
| **Zenodo** | All types | `WebSearch: "{disease}" omics dataset site:zenodo.org` |
| **Figshare** | All types | `WebSearch: "{disease}" RNA-seq proteomics site:figshare.com` |
| **Dryad** | All types | `WebSearch: "{disease}" omics data site:datadryad.org` |
| **OSF (Open Science Framework)** | All types | `WebSearch: "{disease}" omics dataset site:osf.io` |
| **Harvard Dataverse** | All types | `WebSearch: "{disease}" omics dataset site:dataverse.harvard.edu` |
| **Synapse (Sage Bionetworks)** | Neuroscience, cancer | `WebSearch: "{disease}" omics dataset site:synapse.org` |
| **ICGC Data Portal** | Cancer genomics | `WebSearch: "{disease}" ICGC site:dcc.icgc.org` |
| **CPTAC (NCI)** | Cancer proteomics | `WebSearch: "{disease}" CPTAC proteomics site:proteomics.cancer.gov` |
| **AWS Open Data Registry** | All types | `WebSearch: "{disease}" omics site:registry.opendata.aws` |
| **dbGaP (NIH, controlled)** | Genomics, WGS | `WebSearch: "{disease}" WGS genomics site:ncbi.nlm.nih.gov/gap` |
| **EGA (EBI, controlled)** | Genomics, WGS | `WebSearch: "{disease}" genome sequencing site:ega-archive.org` |
| **JGA (Japan, controlled)** | Genomics | `WebSearch: "{disease}" genomics site:ddbj.nig.ac.jp/jga` |
| **UK Biobank** | Population genomics | `WebSearch: "{disease}" UK Biobank omics` |
| **FinnGen** | Population genomics | `WebSearch: "{disease}" FinnGen GWAS` |

---

## Workflow

### Step 1 — Ask upfront clarification questions (MANDATORY before any search)

**This step is not optional.** Before running any queries, use `AskUserQuestion` to collect
the information below. Do not assume defaults — the answers materially affect which repositories
are queried, how many results are returned, and how the relevance audit is tuned.

Ask ALL of the following in a single `AskUserQuestion` call (skip any already answered in the
user's initial message, but always ask the rest):

**Q1 — Disease / topic** *(if not already provided)*
- What disease, phenotype, gene, or biological process should we search for?
- Any synonyms, abbreviations, or alternative names to include?
  (e.g., "SCD", "SCA", "HbSS" for sickle cell disease — more synonyms = better recall)
- Any key genes or pathways central to this topic?
  (used to build additional targeted GEO queries beyond the disease name alone)

**Q2 — Omics types**
- Which omics types should be included?
  - **All types (default)** — transcriptomics, proteomics, metabolomics, epigenomics, genomics, single-cell, spatial, multi-omics
  - **Transcriptomics only** — bulk RNA-seq, microarray, scRNA-seq, spatial
  - **Epigenomics only** — ChIP-seq, ATAC-seq, methylation, CUT&RUN, Hi-C
  - **Proteomics / metabolomics only** — MS proteomics, metabolomics, lipidomics
  - **Custom** — user specifies which types to include

**Q3 — Organism**
- Which organisms should be included?
  - **All organisms (default)** — human, mouse, and any other species
  - **Human only** — Homo sapiens studies only
  - **Specific species** — user specifies (e.g., mouse only, zebrafish only)

**Q4 — Year range**
- Any restriction on publication/deposit year?
  - **No restriction (default)** — all years
  - **Recent only** — e.g., 2018 onwards, 2020 onwards
  - **Custom range** — user specifies start and/or end year

**Q5 — Controlled-access repositories**
- Should we include controlled-access repositories (dbGaP, EGA, JGA)?
  These require institutional data access agreements to download data,
  but we can still catalog their existence and metadata.
  - **Yes, include and flag them (default)** — catalog with "Controlled" label
  - **No, open-access only** — skip dbGaP, EGA, JGA entirely

**Q6 — Goal / output format**
- What is the primary goal?
  - **Browse and select (default)** — CSV catalog + Markdown summary for review
  - **Landscape overview** — also generate a visual overview figure (donut + timeline + repository bar)
  - **Download and analyze** — also include direct download links and file formats where available
  - **All of the above**

**Q7 — Tissue or cell type focus** *(optional but significantly improves recall)*
- Are there specific tissues, cell types, or sample sources to prioritize?
  (e.g., "whole blood", "brain cortex", "CD34+ HSCs", "plasma", "tumor")
  These are added as targeted search terms to GEO and other repositories.
  If none specified, broad disease-name queries are used.

Only proceed to Step 2 once all answers are collected. If the user provides partial information
upfront (e.g., just the disease name), ask only the remaining unanswered questions.

### Step 2 — Build search term matrix

Generate a comprehensive set of search queries by combining:
- Primary disease name + synonyms
- Omics-type-specific terms (RNA-seq, microarray, ChIP-seq, ATAC-seq, scRNA-seq, proteomics, metabolomics, methylation, WGS, SNP array, CUT&RUN, Hi-C, spatial transcriptomics, lipidomics)
- Tissue/cell-type terms relevant to the disease
- Clinical context terms (treatment, crisis, biomarker, pediatric, longitudinal)
- Key gene/pathway terms central to the disease

**GEO query syntax tips:**
- Use `[Title]` field tag for high-precision hits: `"sickle cell"[Title] AND "RNA-seq"`
- Use `[DataSet Type]` for omics filtering: `"expression profiling by array"[DataSet Type]`
- Use `retmax=100` per query; run 20–40 queries to maximize recall
- Deduplicate by GEO UID across all queries

### Step 3 — Query GEO (NCBI E-utilities) — PRIMARY SOURCE

```python
import requests, time, pandas as pd

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

# Build 20-40 queries combining disease + omics + tissue + gene terms
# Example for any disease:
search_queries = {
    "title_rnaseq":    f'"{disease}"[Title] AND "RNA-seq"',
    "title_scrna":     f'"{disease}"[Title] AND "single cell"',
    "title_array":     f'"{disease}"[Title] AND "expression profiling by array"[DataSet Type]',
    "title_chip":      f'"{disease}" AND "ChIP-seq"',
    "title_atac":      f'"{disease}" AND "ATAC-seq"',
    "title_methyl":    f'"{disease}" AND "methylation"',
    "title_wgs":       f'"{disease}" AND "whole genome sequencing"',
    "title_proteom":   f'"{disease}" AND "proteomics"',
    "title_metabolom": f'"{disease}" AND "metabolomics"',
    # Add synonym-based queries:
    # f'"{synonym}"[Title] AND "RNA-seq"' for each synonym
    # Add tissue-specific queries:
    # f'"{disease}" AND "{tissue_term}"' for key tissues
    # Add gene-specific queries:
    # f'"{disease}" AND "{key_gene}"' for key disease genes
}

all_ids = set()
for label, query in search_queries.items():
    r = requests.get(f"{BASE}esearch.fcgi",
                     params={"db": "gds", "term": query, "retmax": 100, "retmode": "json"})
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    all_ids.update(ids)
    time.sleep(0.34)  # Stay under NCBI rate limit (3 req/sec without API key)

# Fetch summaries in batches of 20
records = []
for i in range(0, len(list(all_ids)), 20):
    batch = list(all_ids)[i:i+20]
    r = requests.get(f"{BASE}esummary.fcgi",
                     params={"db": "gds", "id": ",".join(batch), "retmode": "json"})
    result = r.json().get("result", {})
    for uid in result.get("uids", []):
        item = result[uid]
        records.append({
            "Accession": item.get("accession", ""),
            "Title": item.get("title", ""),
            "GEO_Type": item.get("gdstype", ""),
            "Organism": item.get("taxon", ""),
            "N_Samples": item.get("n_samples", ""),
            "Date": item.get("pdat", ""),
            "Summary": item.get("summary", "")[:500],
            "Repository": "GEO",
        })
    time.sleep(0.34)

df_geo = pd.DataFrame(records)
# Filter: GSE/GDS accessions only (exclude GPL platform records)
# By default, keep ALL organisms. Only filter by organism if user explicitly requested a specific species.
# Example organism filter (apply only if requested):
#   df_geo = df_geo[df_geo["Organism"].str.contains("Homo sapiens", na=False)]
df_geo = df_geo[df_geo["Accession"].str.startswith(("GSE", "GDS"))]
```

**NCBI API key**: Set `api_key` param to increase rate limit to 10 req/sec (register free at NCBI).

### Step 4 — Query SRA (NCBI) for raw sequencing studies not in GEO

```python
import xml.etree.ElementTree as ET

# SRA catches studies deposited as raw reads without a GEO series.
#
# IMPORTANT — SRA esummary response shape (verified against the live API):
# Unlike GEO esummary (which returns nested dicts), the SRA esummary returns
# XML *strings* in the "expxml" and "runs" fields. The top-level item has only
# {uid, expxml, runs, extlinks, createdate, updatedate} — there are NO
# "experiment"/"study"/"sample"/"organism"/"title"/"summary" dict fields.
# Parsing expxml/runs as nested dicts (the old code) therefore yields 0 records.
# Parse the XML strings with xml.etree.ElementTree instead.
sra_records = []
for query in [disease] + synonyms[:3]:
    # Default: search all organisms. Add AND "Homo sapiens"[Organism] only if user requested human-only.
    r = requests.get(f"{BASE}esearch.fcgi",
                     params={"db": "sra", "term": f'"{query}"',
                             "retmax": 100, "retmode": "json"})
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    if ids:
        # Fetch in batches of 50 to stay within esummary limits
        for bi in range(0, len(ids), 50):
            batch = ids[bi:bi+50]
            r2 = requests.get(f"{BASE}esummary.fcgi",
                              params={"db": "sra", "id": ",".join(batch), "retmode": "json"})
            result = r2.json().get("result", {})
            for uid in result.get("uids", []):
                item = result[uid]
                expxml = item.get("expxml", "") or ""
                runs_xml = item.get("runs", "") or ""

                # Parse the expxml XML string for metadata.
                title = ""
                exp_acc = ""
                study_acc = ""
                sample_acc = ""
                organism_name = "unknown"
                exptype = ""
                try:
                    root = ET.fromstring(f"<root>{expxml}</root>")
                    summary_el = root.find(".//Summary")
                    if summary_el is not None:
                        title_el = summary_el.find("Title")
                        if title_el is not None:
                            title = title_el.text or ""
                    exp_el = root.find(".//Experiment")
                    if exp_el is not None:
                        exp_acc = exp_el.get("acc", "")
                    study_el = root.find(".//Study")
                    if study_el is not None:
                        study_acc = study_el.get("acc", "")
                    sample_el = root.find(".//Sample")
                    if sample_el is not None:
                        sample_acc = sample_el.get("acc", "")
                    org_el = root.find(".//Organism")
                    if org_el is not None:
                        organism_name = org_el.get("ScientificName", "unknown")
                    lib_el = root.find(".//LIBRARY_STRATEGY")
                    if lib_el is not None and lib_el.text:
                        exptype = lib_el.text
                except ET.ParseError:
                    pass

                # Parse the runs XML string for the run accession + spot count.
                run_acc = ""
                n_spots = ""
                try:
                    run_root = ET.fromstring(f"<root>{runs_xml}</root>")
                    run_el = run_root.find(".//Run")
                    if run_el is not None:
                        run_acc = run_el.get("acc", "")
                        n_spots = run_el.get("total_spots", "")
                except ET.ParseError:
                    pass

                # Prefer the most specific real accession; fall back through the chain
                accession = run_acc or exp_acc or study_acc or sample_acc or ""
                # Drop rows where ALL metadata fields are empty — these are just
                # numeric internal IDs with no usable information.
                if not any([accession, title, organism_name != "unknown"]):
                    continue
                sra_records.append({
                    "Accession": accession,
                    "Title": title,
                    "GEO_Type": exptype,
                    "Organism": organism_name,
                    "N_Samples": n_spots,
                    "Date": item.get("createdate", ""),
                    "Summary": "",
                    "Repository": "SRA",
                })
        time.sleep(0.34)
```

### Step 5 — Query ArrayExpress / BioStudies (EBI)

```python
import re

# BioStudies API — covers ArrayExpress functional genomics collection
# Source: https://www.ebi.ac.uk/biostudies/api/v1/search
#
# IMPORTANT — search-hit response shape (verified against the live API):
# each hit exposes: accession, title, type (always "study"), release_date, files,
# links, and `content` — a free-text blob containing the assay type
# (e.g. "transcription profiling by array"), EFO ontology terms, organism, and the
# study description. There is NO releaseDate / description / numberOfSamples /
# organism field on the hit — those live only at /studies/{accession}, which is too
# expensive to fetch per row. So we read release_date + mine `content` instead.
biostudies_url = "https://www.ebi.ac.uk/biostudies/api/v1/search"
biostudies_records = []

# Best-effort organism surfacing from the free-text `content` blob. The search snippet is
# truncated and query-centered, so it often omits the species (~64% coverage). Step 15
# backfills the authoritative organism from /studies/{accession} for the high-relevance
# rows, bringing those to ~100%.
ORGANISM_PATTERNS = ["Homo sapiens", "Mus musculus", "Rattus norvegicus", "Danio rerio",
                     "Drosophila melanogaster", "Saccharomyces cerevisiae",
                     "Caenorhabditis elegans", "Gallus gallus", "Sus scrofa",
                     "Macaca mulatta", "Pan troglodytes", "Arabidopsis thaliana"]

for keyword in [disease] + synonyms[:2]:
    r = requests.get(biostudies_url,
                     params={"query": keyword, "collection": "arrayexpress",
                             "pageSize": 100, "page": 1},
                     headers={"Accept": "application/json"}, timeout=60)
    if r.status_code == 200:
        data = r.json()
        for hit in data.get("hits", []):
            content = hit.get("content", "") or ""
            organism = "; ".join([o for o in ORGANISM_PATTERNS if o in content])
            biostudies_records.append({
                "Accession": hit.get("accession", ""),
                "Title": hit.get("title", ""),
                # `type` is always "study"; the real assay type is inside `content`
                # (e.g. "transcription profiling by array") and is recovered by
                # classify_omics in Step 13, which reads Title + Summary.
                "GEO_Type": "",
                "Organism": organism,
                "N_Samples": "",  # not in the search response; only files/links counts
                "Date": hit.get("release_date", ""),
                "Summary": content[:500],
                "Repository": "ArrayExpress/BioStudies",
            })
    time.sleep(1)

df_biostudies = pd.DataFrame(biostudies_records).drop_duplicates(subset="Accession")
# Do NOT drop E-GEOD- accessions here. ~60% of ArrayExpress hits are E-GEOD-* (GEO
# studies mirrored into ArrayExpress), but many of those are studies the GEO step
# missed (different search terms / indexing), and dropping them here is the original
# bug that made this source effectively disappear from the catalog. Instead, we let
# them through and dedupe against native GEO records at assembly (Step 15) via
# accession normalization: E-GEOD-41575  <->  GSE41575.
# Keep all organisms by default. Apply organism filter only if user explicitly requested a specific species.
# Example: df_biostudies = df_biostudies[df_biostudies["Organism"].str.contains("Homo sapiens|human", case=False, na=False)]
```

### Step 6 — Query PRIDE / ProteomeXchange (proteomics)

```python
import re

# PRIDE REST API v3
# Source: https://www.ebi.ac.uk/pride/ws/archive/v3/projects
# NOTE: the v2 endpoint (https://www.ebi.ac.uk/pride/ws/archive/v2/projects/search)
# returns HTTP 404 — it has been deprecated/moved. Use v3 instead.
# The v3 keyword filter does not reliably filter server-side, so we fetch projects
# and apply client-side word-boundary filtering by disease terms in title/description.
pride_url = "https://www.ebi.ac.uk/pride/ws/archive/v3/projects"
pride_records = []
disease_terms = [disease.lower()] + [s.lower() for s in synonyms]

for keyword in [disease] + synonyms:
    r = requests.get(pride_url,
                     params={"keyword": keyword, "pageSize": 100, "page": 0},
                     headers={"Accept": "application/json"}, timeout=60)
    if r.status_code == 200:
        data = r.json()
        # Handle both list and dict response formats
        projects = data if isinstance(data, list) else data.get("_embedded", {}).get("projects", [])
        for p in projects:
            title = p.get("title", "") or ""
            desc = p.get("projectDescription", "") or ""
            # Client-side word-boundary filter: the v3 keyword param returns generic
            # projects regardless of keyword, so re-check title+description ourselves.
            ptext = (title + " " + desc).lower()
            if not any(re.search(r'\b' + re.escape(t) + r'\b', ptext) for t in disease_terms):
                continue
            # organisms may be a list of strings or list of dicts with a "species" field
            orgs = p.get("organisms", [])
            if orgs and isinstance(orgs[0], dict):
                orgs = [o.get("species", "") for o in orgs]
            pride_records.append({
                "Accession": p.get("accession", ""),
                "Title": title,
                "GEO_Type": "Proteomics (MS)",
                "Organism": "; ".join(orgs) if isinstance(orgs, list) else str(orgs),
                "N_Samples": p.get("numberOfSamples", "") or p.get("sampleCount", ""),
                "Date": p.get("submissionDate", "") or p.get("projectDate", ""),
                "Summary": desc[:500],
                "Repository": "PRIDE",
            })
    time.sleep(1)

df_pride = pd.DataFrame(pride_records).drop_duplicates(subset="Accession")
# Keep all organisms by default. Apply organism filter only if user explicitly requested a specific species.
# Example: df_pride = df_pride[df_pride["Organism"].str.contains("Homo sapiens|human", case=False, na=False)]
```

**Also check jPOST and iProX** (ProteomeXchange members not always in PRIDE search):
```python
# jPOST REST API
# Source: https://repository.jpostdb.org/search
jpost_records = []
for keyword in [disease] + synonyms[:2]:
    r = requests.get("https://repository.jpostdb.org/search",
                     params={"keyword": keyword, "format": "json"}, timeout=30)
    if r.status_code == 200:
        for p in r.json().get("projects", []):
            jpost_records.append({
                "Accession": p.get("projectId", ""),
                "Title": p.get("title", ""),
                "GEO_Type": "Proteomics (MS)",
                "Organism": p.get("organism", ""),
                "N_Samples": "",
                "Date": p.get("releaseDate", ""),
                "Summary": p.get("description", "")[:500],
                "Repository": "jPOST",
            })
    time.sleep(1)
```

### Step 7 — Query OmicsDI (metabolomics aggregator)

OmicsDI is the single best entry point for metabolomics — it aggregates MetaboLights, MassIVE,
GNPS, Metabolomics Workbench, and Metabolon. Filter by source to avoid GEO duplicates.

```python
# OmicsDI aggregator
# Source: https://www.omicsdi.org/ws/dataset/search
omicsdi_url = "https://www.omicsdi.org/ws/dataset/search"
omicsdi_records = []
METABOLOMICS_REPOS = {"MetaboLights", "MassIVE", "GNPS", "Metabolon",
                      "Metabolomics Workbench", "HMDB", "Lipidmaps"}

for keyword in [disease] + synonyms[:2]:
    r = requests.get(omicsdi_url,
                     params={"query": keyword, "size": 100, "start": 0},
                     headers={"Accept": "application/json"}, timeout=60)
    if r.status_code == 200:
        for ds in r.json().get("datasets", []):
            repo = ds.get("source", "")
            if repo in METABOLOMICS_REPOS:
                # organisms may be a dict with "name" list, a list, or a string;
                # guard against all shapes to avoid str.join iterating a string char-by-char.
                orgs = ds.get("organisms", {})
                if isinstance(orgs, dict):
                    org_names = orgs.get("name", [])
                elif isinstance(orgs, list):
                    org_names = orgs
                else:
                    org_names = [str(orgs)] if orgs else []
                # OmicsDI often returns an empty "name" field (especially for
                # MetaboLights, jPOST, and some ArrayExpress records). Fall back
                # to the description (stripped of HTML tags) so the Title column
                # is never blank, which would make relevance auditing impossible.
                title = ds.get("name", "") or ""
                if not title.strip():
                    desc = ds.get("description", "") or ""
                    # Strip simple HTML tags for a cleaner title
                    import re as _re
                    title = _re.sub(r"<[^>]+>", "", desc).strip()[:200]
                if not title.strip():
                    title = f"Untitled — see {ds.get('id', '')}"
                omicsdi_records.append({
                    "Accession": ds.get("id", ""),
                    "Title": title,
                    "GEO_Type": "Metabolomics",
                    "Organism": "; ".join(org_names) if isinstance(org_names, list) else str(org_names),
                    "N_Samples": "",
                    "Date": ds.get("publicationDate", ""),
                    "Summary": ds.get("description", "")[:500],
                    "Repository": repo,
                })
    time.sleep(1)

df_metabolomics = pd.DataFrame(omicsdi_records).drop_duplicates(subset="Accession")
```

**Also query Metabolomics Workbench REST API directly** for NIH-funded studies:
```python
# Metabolomics Workbench REST API
# Source: https://www.metabolomicsworkbench.org/tools/mw_rest.php
mw_url = "https://www.metabolomicsworkbench.org/rest/study/study_title"
for keyword in [disease] + synonyms[:2]:
    r = requests.get(f"{mw_url}/{requests.utils.quote(keyword)}/summary/json", timeout=30)
    if r.status_code == 200 and r.text.strip():
        # Response is a dict of study_id -> study metadata
        for sid, meta in r.json().items():
            omicsdi_records.append({
                "Accession": sid,
                "Title": meta.get("study_title", ""),
                "GEO_Type": "Metabolomics",
                "Organism": meta.get("subject_species", ""),
                "N_Samples": meta.get("subject_count", ""),
                "Date": meta.get("submit_date", ""),
                "Summary": meta.get("study_summary", "")[:500],
                "Repository": "Metabolomics Workbench",
            })
```

### Step 8 — Query CZ CELLxGENE (single-cell)

```python
import re

# CZ CELLxGENE Collections API
# Source: https://api.cellxgene.cziscience.com/curation/v1/collections
cxg_url = "https://api.cellxgene.cziscience.com/curation/v1/collections"
r = requests.get(cxg_url, headers={"Accept": "application/json"}, timeout=60)
cxg_records = []
if r.status_code == 200:
    for col in r.json():
        title = col.get("name", "")
        desc = col.get("description", "")
        # Filter by disease keywords using word-boundary matching to avoid
        # short-synonym false positives (e.g. bare "ALS" inside "animals").
        text = (title + " " + desc).lower()
        if any(re.search(r'\b' + re.escape(kw.lower()) + r'\b', text) for kw in [disease] + synonyms):
            # d.get("organism") can return a list (not a string), which makes the
            # set comprehension raise "unhashable type: 'list'". Guard with isinstance.
            organisms = sorted({d.get("organism","") for d in col.get("datasets",[])
                                if isinstance(d.get("organism"), str) and d.get("organism")})
            cxg_records.append({
                "Accession": col.get("collection_id", ""),
                "Title": title,
                "GEO_Type": "scRNA-seq",
                "Organism": "; ".join(organisms) or "unknown",
                "N_Samples": col.get("cell_count", ""),
                "Date": col.get("published_at", ""),
                "Summary": desc[:500],
                "Repository": "CZ CELLxGENE",
            })

# Also use cellxgene_census Python package for cell-level metadata queries:
# import cellxgene_census
# census = cellxgene_census.open_soma()
# obs = census["census_data"]["homo_sapiens"].obs.read(
#     value_filter=f'disease == "{disease_ontology_term}"'
# ).concat().to_pandas()
```

### Step 9 — Query GDC / TCGA (cancer diseases)

Only relevant for cancer diseases. Skip for non-cancer diseases.

```python
# GDC REST API
# Source: https://api.gdc.cancer.gov/
gdc_url = "https://api.gdc.cancer.gov/projects"
r = requests.get(gdc_url,
                 params={"filters": json.dumps({"op": "in", "content": {
                             "field": "disease_type", "value": [disease] + synonyms}}),
                         "fields": "project_id,name,disease_type,primary_site,summary",
                         "format": "json", "size": 100},
                 timeout=60)
# Parse and add to records with Repository = "GDC/TCGA"
```

### Step 10 — Query ENCODE (epigenomics)

Only relevant when the disease has known epigenomic/regulatory components.

```python
# ENCODE REST API — rate limit: 10 req/sec
# Source: https://www.encodeproject.org/help/rest-api
encode_url = "https://www.encodeproject.org/search/"
r = requests.get(encode_url,
                 params={"searchTerm": disease, "type": "Experiment",
                         "status": "released", "format": "json", "limit": 100},
                 headers={"Accept": "application/json"}, timeout=60)
if r.status_code == 200:
    for exp in r.json().get("@graph", []):
        encode_records.append({
            "Accession": exp.get("accession", ""),
            "Title": exp.get("description", ""),
            "GEO_Type": exp.get("assay_title", ""),
            "Organism": exp.get("organism", {}).get("scientific_name", ""),
            "N_Samples": len(exp.get("replicates", [])),
            "Date": exp.get("date_released", ""),
            "Summary": exp.get("biosample_summary", "")[:500],
            "Repository": "ENCODE",
        })
```

### Step 11 — Query Expression Atlas (EBI)

```python
import re

# Expression Atlas REST API
# Source: https://www.ebi.ac.uk/gxa/json/experiments
# Default: no species filter — returns all organisms. Add species param only if user requested a specific organism.
atlas_url = "https://www.ebi.ac.uk/gxa/json/experiments"
r = requests.get(atlas_url, timeout=60)  # omit species= to get all organisms
atlas_records = []
if r.status_code == 200:
    for exp in r.json().get("experiments", []):
        text = (exp.get("experimentDescription","") + " " +
                " ".join(exp.get("factors",[])) + " " +
                " ".join(exp.get("experimentalFactors",[]))).lower()
        # Use word-boundary matching for disease keywords to avoid short-synonym
        # false positives (e.g. bare "ALS" matching inside "animals", "reveals").
        if any(re.search(r'\b' + re.escape(kw.lower()) + r'\b', text) for kw in [disease] + synonyms):
            # species may be a list OR a string; str.join iterates a string
            # character-by-character, corrupting "Homo sapiens" -> "H; o; m; o; ...".
            sp = exp.get("species", [])
            if isinstance(sp, list):
                organism = "; ".join(sp) if sp else "unknown"
            elif sp:
                organism = str(sp)
            else:
                organism = "unknown"
            atlas_records.append({
                "Accession": exp.get("experimentAccession", ""),
                "Title": exp.get("experimentDescription", ""),
                "GEO_Type": exp.get("experimentType", ""),
                "Organism": organism,
                "N_Samples": exp.get("numberOfAssays", ""),
                "Date": exp.get("lastUpdate", ""),
                "Summary": "",
                "Repository": "Expression Atlas",
            })
```

### Step 12 — Web search for Tier 3 repositories

Use the WebSearch tool for repositories without disease-specific APIs. Run all of these:

```
# Open repositories
WebSearch: "{disease}" omics dataset site:zenodo.org
WebSearch: "{disease}" RNA-seq proteomics site:figshare.com
WebSearch: "{disease}" omics data site:datadryad.org
WebSearch: "{disease}" omics dataset site:osf.io
WebSearch: "{disease}" omics dataset site:dataverse.harvard.edu
WebSearch: "{disease}" omics dataset site:synapse.org

# Controlled-access repositories
WebSearch: "{disease}" WGS genomics site:ncbi.nlm.nih.gov/gap
WebSearch: "{disease}" genome sequencing site:ega-archive.org
WebSearch: "{disease}" genomics site:ddbj.nig.ac.jp/jga

# Consortium/specialized
WebSearch: "{disease}" ICGC genomics dataset
WebSearch: "{disease}" CPTAC proteomics dataset
WebSearch: "{disease}" UK Biobank omics
WebSearch: "{disease}" AWS open data omics
```

Extract accession IDs, titles, and descriptions from search results. Add as rows with the
appropriate `Repository` label. Flag dbGaP/EGA/JGA as `Access = "Controlled"`.

### Step 13 — Classify omics type

Apply rule-based classification to each record's `GEO_Type`, `Title`, and `Summary`.
**The GEO `gdstype` field is unreliable — always use title + summary as primary signal.**

```python
import re

def classify_omics(row):
    combined = (str(row.get("GEO_Type","")) + " " +
                str(row.get("Title","")) + " " +
                str(row.get("Summary",""))).lower()

    # Single-cell (check before bulk RNA-seq)
    if any(x in combined for x in ["single cell", "scrna", "10x chromium", "dropseq",
                                    "smart-seq", "single-nucleus", "snrna"]):
        return "scRNA-seq"
    # Spatial transcriptomics
    elif any(x in combined for x in ["spatial transcriptom", "visium", "slide-seq",
                                      "merfish", "seqfish", "stereo-seq"]):
        return "Spatial Transcriptomics"
    # Chromatin accessibility
    elif "atac" in combined:
        return "ATAC-seq"
    # CUT&RUN / CUT&TAG
    elif any(x in combined for x in ["cut&run", "cut and run", "cutana", "cut&tag"]):
        return "CUT&RUN/CUT&TAG"
    # ChIP-seq
    elif any(x in combined for x in ["chip-seq", "chip seq", "binding/occupancy",
                                      "chromatin immunoprecipitation"]):
        return "ChIP-seq"
    # 3D genome — use word-boundary regex for short ambiguous substrings.
    # Bare "hic" matches inside "sclerosis" (sclero-hic), causing massive false positives.
    elif any(re.search(r'\b' + re.escape(x) + r'\b', combined) for x in ["hi-c", "hic", "3d genome", "chromatin conformation",
                                      "capture-c", "4c-seq", "5c"]):
        return "Hi-C / 3D Genome"
    # DNA methylation
    elif any(x in combined for x in ["methylat", "bisulfite", "wgbs", "rrbs",
                                      "epic array", "850k", "450k", "dnmt"]):
        return "DNA Methylation"
    # miRNA / ncRNA
    elif any(x in combined for x in ["mirna", "microrna", "ncrna", "lncrna",
                                      "small rna", "pirna", "circrna"]):
        return "miRNA/ncRNA"
    # Ribo-seq / RIP-seq
    elif any(x in combined for x in ["ribo-seq", "ribosome profiling", "rip-seq",
                                      "clip-seq", "iclip"]):
        return "Other (Ribo/RIP/CLIP-seq)"
    # Bulk RNA-seq
    elif any(x in combined for x in ["high throughput sequencing", "rna-seq", "rnaseq",
                                      "mrna-seq", "total rna", "poly-a"]):
        return "Bulk RNA-seq"
    # Microarray (note: ArrayExpress labels these "transcription profiling by array")
    elif any(x in combined for x in ["expression profiling by array",
                                      "transcription profiling by array", "microarray",
                                      "affymetrix", "illumina beadchip", "agilent"]):
        return "Microarray"
    # Genomics (ArrayExpress uses "comparative genomic hybridization" for CNV/CGH arrays)
    elif any(x in combined for x in ["wgs", "whole genome sequencing", "whole-genome",
                                      "snp array", "genotyping", "gwas", "exome",
                                      "comparative genomic hybridization"]):
        return "Genomics (WGS/SNP/Exome)"
    # Proteomics
    elif any(x in combined for x in ["proteom", "mass spectrometry", "lc-ms", "tmtpro",
                                      "label-free", "dia-ms", "dda-ms", "phosphoproteom"]):
        return "Proteomics (MS)"
    # Metabolomics / lipidomics
    elif any(x in combined for x in ["metabolom", "metabolite", "nmr", "gcms", "lcms",
                                      "lipidom", "lipidome", "untargeted ms"]):
        return "Metabolomics/Lipidomics"
    # Multi-omics
    elif any(x in combined for x in ["multi-omics", "multiomics", "multi omics",
                                      "integrat", "joint profil"]):
        return "Multi-omics"
    else:
        return "Other/Mixed"
```

### Step 14 — Relevance audit (4-tier classification)

Classify each dataset into one of four relevance tiers based on keyword analysis of title + summary.
**Tune keyword lists for each disease — the examples below are for SCD.**

```python
import re

def _word_match(keyword, text):
    """Word-boundary match: prevents short synonyms like 'ALS', 'AD', 'SCD'
    from matching inside common English words ('animals', 'reveals', 'muscles').
    Multi-word phrases use the first token as the boundary anchor."""
    return bool(re.search(r'\b' + re.escape(keyword.lower()) + r'\b', text))

def audit_relevance(row, disease_keywords, weak_keywords, exclude_keywords):
    """
    disease_keywords: strings confirming direct disease relevance (patient data or validated model)
    weak_keywords:    strings indicating adjacent/tangential relevance
    exclude_keywords: strings indicating the dataset is NOT about the disease (false positives)

    All keyword matching uses word boundaries to avoid short-synonym false positives
    (e.g. bare 'als' matching inside 'animals', 'reveals', 'individuals').
    """
    text = (str(row.get("Title","")) + " " + str(row.get("Summary",""))).lower()

    # Hard exclusion: only exclude if NO disease keyword matches AND an exclude
    # keyword is present. This prevents excluding genuine disease studies that
    # also mention a comparator disease.
    has_disease = any(_word_match(kw, text) for kw in disease_keywords)
    if not has_disease and any(_word_match(kw, text) for kw in exclude_keywords):
        return "REMOVE"

    strong_hits = sum(1 for kw in disease_keywords if _word_match(kw, text))
    weak_hits   = sum(1 for kw in weak_keywords    if _word_match(kw, text))

    if strong_hits >= 2:
        return "CORE/DIRECT"
    elif strong_hits == 1 and weak_hits >= 1:
        return "CORE/DIRECT"
    elif strong_hits == 1 and weak_hits == 0:
        return "ADJACENT"
    elif weak_hits >= 1 and strong_hits == 0:
        return "WEAK"
    else:
        return "WEAK"
```

**Relevance tier definitions:**

| Tier | Meaning | Action |
|---|---|---|
| `CORE/DIRECT` | Actual disease patient samples or validated disease model (e.g., HbSS mice) | Include in primary catalog |
| `ADJACENT` | Mechanistically relevant (e.g., HbF reactivation, globin switching) but no patient data | Include with flag; useful for mechanism studies |
| `WEAK` | Epidemiologically linked condition (e.g., sickle cell trait, RMC) or only tangentially related | Include with flag; user decides |
| `REMOVE` | False positive — no disease connection | Exclude; always manually review before deleting |

**Example keyword lists for SCD:**
```python
disease_keywords = ["sickle cell", "sickle-cell", "HbSS", "hemoglobin S",
                    "vaso-occlus", "sickling", "SCA patient", "SCD patient",
                    "sickle cell anemia", "sickle cell disease"]
weak_keywords    = ["sickle cell trait", "HbAS", "renal medullary carcinoma",
                    "RMC", "beta-thalassemia", "thalassemia only"]
exclude_keywords = ["lung cancer", "gastric cancer", "colon cancer",
                    "BACH1 lung", "unrelated disease"]  # tune per run
```

**Short-synonym warning:** Disease abbreviations like `ALS`, `AD`, `SCD`, `MND`, `FTD` are
short enough to match inside common English words when used as bare substrings (`ALS` →
`animals`, `reveals`, `individuals`; `AD` → `loaded`, `reading`; `SCD` → `muscle-derived`).
The `audit_relevance` function above uses word-boundary regex (`\b`) for all keyword
matching to prevent this. When tuning keyword lists for a new disease, prefer full disease
names over abbreviations wherever possible, and always test short synonyms against a sample
of known false-positive titles.

**Always manually review REMOVE candidates** before deleting — keyword matching produces false negatives.

### Step 15 — Assemble master catalog

```python
import pandas as pd, re

# Combine all sources. Order matters for dedup: list df_geo BEFORE df_biostudies so
# that when a study exists in both (e.g. GSE41575 and its E-GEOD-41575 mirror), the
# richer native GEO record is the one kept.
all_dfs = [df for df in [df_geo, df_sra, df_biostudies, df_pride, df_jpost,
                          df_metabolomics, df_cxg, df_encode, df_atlas,
                          df_zenodo, df_figshare, df_controlled]
           if df is not None and len(df) > 0]
df_all = pd.concat(all_dfs, ignore_index=True)

# Deduplicate across repositories. The same study can appear under different accession
# formats — most commonly a GEO series (GSE41575) and its ArrayExpress mirror
# (E-GEOD-41575). Normalize to a common key so these collapse, while genuinely unique
# ArrayExpress studies (E-MTAB-, E-MEXP-, E-TABM-, …) are retained.
def dedup_key(acc):
    acc = str(acc)
    m = re.match(r"^E-GEOD-(\d+)$", acc)
    return f"GSE{m.group(1)}" if m else acc
df_all["_dedup"] = df_all["Accession"].apply(dedup_key)
df_all = df_all.drop_duplicates(subset="_dedup").drop(columns=["_dedup"])

# Apply omics classification
df_all["Omics_Type"] = df_all.apply(classify_omics, axis=1)

# Apply relevance audit
df_all["Relevance"] = df_all.apply(
    lambda row: audit_relevance(row, disease_keywords, weak_keywords, exclude_keywords), axis=1
)

# --- Organism accuracy pass for ArrayExpress/BioStudies ---
# ArrayExpress search hits don't expose organism reliably — Step 5 mines a truncated,
# query-centered text snippet that often omits the species (≈64% coverage). For the rows
# that matter most (CORE/DIRECT + ADJACENT), fetch the authoritative organism from the
# per-study endpoint. Scoped to the validated subset so this stays cheap (a handful of
# calls) instead of one call per catalog row, which would be too slow at catalog scale.
def fetch_biostudies_organism(accession):
    """Return '; '-joined organism(s) from the BioStudies per-study record, walking
    nested subsections (sample-level organism attributes). '' on any failure."""
    try:
        r = requests.get(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{accession}",
                         headers={"Accept": "application/json"}, timeout=30)
        if r.status_code != 200:
            return ""
        organisms, stack = [], [r.json().get("section", {})]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for a in node.get("attributes", []) or []:
                    if str(a.get("name", "")).strip().lower() == "organism" and a.get("value"):
                        organisms.append(a["value"].strip())
                if node.get("subsections"):
                    stack.append(node["subsections"])
            elif isinstance(node, list):
                stack.extend(node)
        seen, out = set(), []
        for o in organisms:
            if o.lower() not in seen:
                seen.add(o.lower()); out.append(o)
        return "; ".join(out)
    except Exception:
        return ""

needs_organism = df_all[
    (df_all["Repository"] == "ArrayExpress/BioStudies")
    & (df_all["Relevance"].isin(["CORE/DIRECT", "ADJACENT"]))
    & (df_all["Organism"].fillna("").str.strip() == "")
]
for idx, row in needs_organism.iterrows():
    org = fetch_biostudies_organism(row["Accession"])
    if org:
        df_all.at[idx, "Organism"] = org
    time.sleep(0.2)  # be gentle on the EBI API

# Add access level column
CONTROLLED_REPOS = {"dbGaP", "EGA", "JGA", "UK Biobank", "FinnGen"}
df_all["Access"] = df_all["Repository"].apply(
    lambda r: "Controlled" if any(c in r for c in CONTROLLED_REPOS) else "Open"
)

# --- Date normalization ---
# Different repositories return dates in different formats: GEO uses YYYY/MM/DD,
# ArrayExpress uses YYYY-MM-DD, SRA/OmicsDI use compact YYYYMMDD, and some rows
# are year-only floats or ISO datetimes. Normalize everything to YYYY-MM-DD so
# the Date column is consistent and sorts correctly. Empty/invalid dates stay "".
def normalize_date(v):
    s = str(v).strip() if pd.notna(v) else ""
    if not s or s.lower() in ("nan", "none", "nat"):
        return ""
    # YYYY/MM/DD -> YYYY-MM-DD
    m = re.match(r"^(\d{4})/(\d{2})/(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # compact YYYYMMDD -> YYYY-MM-DD
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # ISO datetime YYYY-MM-DDTHH:... -> YYYY-MM-DD
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    # year-only float like 2024.0 -> "" (not a full date)
    if re.match(r"^\d{4}\.0$", s):
        return ""
    # already YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return s  # leave unrecognized values as-is rather than dropping data

df_all["Date"] = df_all["Date"].apply(normalize_date)

# Sort: relevance tier first, then year descending
tier_order = {"CORE/DIRECT": 0, "ADJACENT": 1, "WEAK": 2, "REMOVE": 3}
df_all["_tier"] = df_all["Relevance"].map(tier_order)
df_all = df_all.sort_values(["_tier", "Date"], ascending=[True, False]).drop(columns=["_tier"])

# Save outputs
df_all.to_csv(f"{output_dir}/{disease_slug}_omics_datasets_MASTER.csv", index=False)
df_validated = df_all[df_all["Relevance"].isin(["CORE/DIRECT", "ADJACENT"])]
df_validated.to_csv(f"{output_dir}/{disease_slug}_omics_datasets_VALIDATED.csv", index=False)

print(f"Total datasets: {len(df_all)}")
print(f"CORE/DIRECT: {(df_all.Relevance=='CORE/DIRECT').sum()}")
print(f"ADJACENT: {(df_all.Relevance=='ADJACENT').sum()}")
print(f"WEAK: {(df_all.Relevance=='WEAK').sum()}")
print(f"REMOVE: {(df_all.Relevance=='REMOVE').sum()}")
```

### Step 16 — Generate Markdown summary report

Write `<disease>_omics_summary.md` with these sections:

1. **Overview**: total datasets found, repositories searched, date of search
2. **By repository**: table of dataset counts per repository
3. **By omics type**: table of dataset counts per omics type (CORE/DIRECT only)
4. **Relevance breakdown**: counts per tier with brief explanation
5. **Top datasets**: largest N, most recent, most cited (if available)
6. **Controlled-access datasets**: list with access instructions
7. **Coverage gaps and limitations**: which repositories failed, which APIs require keys,
   which omics types are data-sparse for this disease

When writing DataFrame-derived values into the Markdown or PDF report, coerce nullable
values to safe strings first: `str(v) if pd.notna(v) else ""` (or `"N/A"`). ReportLab
`Paragraph()` raises `TypeError` on `float('nan')`, and CSV/Markdown cells render as
literal `nan` if not coerced.

### Step 17 — Generate overview figure (if requested)

Create a 3-panel figure saved as `<disease>_omics_landscape.png`:

```python
import matplotlib.pyplot as plt, matplotlib
matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']
matplotlib.rcParams['svg.fonttype'] = 'none'

fig, axes = plt.subplots(1, 3, figsize=(18, 6))

# Panel A: Donut chart — CORE/DIRECT datasets by omics type
# Panel B: Horizontal bar chart — all datasets by repository
# Panel C: Stacked bar timeline — datasets per year, colored by omics type

fig.savefig(f"{output_dir}/{disease_slug}_omics_landscape.png", dpi=150, bbox_inches="tight")
```

After saving, run `Read(file_path=..., mode="media_output_check")` to verify the figure renders correctly.

---

## Known API Limitations and Workarounds

| Repository | Issue | Workaround |
|---|---|---|
| **GEO** | `retmax=100` per query cap | Run 20–40 targeted queries; use `[Title]` field tags |
| **PRIDE** | v2 endpoint returns HTTP 404 (deprecated/moved); v3 keyword filter does not filter server-side | Use v3 endpoint (`https://www.ebi.ac.uk/pride/ws/archive/v3/projects`); apply client-side word-boundary filtering by disease terms in title/description |
| **MetaboLights** | Direct search API returns 404 | Use OmicsDI aggregator instead |
| **Metabolomics Workbench** | REST API requires exact study title match | Use keyword search via OmicsDI; use REST for known study IDs |
| **ArrayExpress/BioStudies** | Search hits do NOT expose `releaseDate`/`description`/`organism`/`numberOfSamples` — only `release_date` + a free-text `content` blob | Read `release_date`; mine `content` for summary/organism/assay type (Step 5). Organism mining is ~64% (truncated snippet); Step 15 backfills organism from `/studies/{accession}` for CORE/ADJACENT rows → ~100% where it matters, without a per-row cost on the full catalog |
| **ArrayExpress/BioStudies** | ~60% of hits are `E-GEOD-*` GEO mirrors | Do NOT drop them at query time; dedupe against GEO at assembly via `E-GEOD-NNN` → `GSENNN` normalization (Step 15) so unique studies survive |
| **DISGENET** | API v7 requires paid API key; returns HTML without key | Skip; use GEO + PubMed searches instead |
| **GWAS Catalog** | `findByDiseaseTrait` returns 0 for many disease names (trait name mismatch) | Search by EFO ontology ID; browse manually at https://www.ebi.ac.uk/gwas/ |
| **CellxGene** | No disease-specific collection tagging for rare diseases | Filter by `disease` field in Census cell metadata |
| **ENCODE** | Rate limit 10 req/sec | Add `time.sleep(0.1)` between requests |
| **dbGaP / EGA / JGA** | No open programmatic search API | Web search + manual curation; flag as Controlled access |
| **Zenodo / Figshare / Dryad** | No disease-specific API | WebSearch with `site:` operator |
| **Synapse** | Requires login for some datasets | Web search for public projects; note login requirement |
| **GDC/TCGA** | Only relevant for cancer diseases | Skip for non-cancer diseases |
| **jPOST / iProX** | API may be unstable | Fall back to ProteomeCentral search at http://central.proteomexchange.org |

---

## Scientific Caveats

1. **GEO `gdstype` field is unreliable**: Often says "Other" or "Expression profiling by high
   throughput sequencing" for ChIP-seq, ATAC-seq, and Ribo-seq studies. Always re-classify
   using title + summary text. Many studies are misclassified as "Hi-C" when they are RNA-seq.

2. **Cross-repository duplicates are common**: The same study may appear in GEO, ArrayExpress,
   SRA, and OmicsDI. GEO accessions (GSE) and ArrayExpress accessions (E-GEOD-) frequently refer
   to the same study under different accession formats. Step 15 normalizes `E-GEOD-NNN` → `GSENNN`
   before deduplicating so these collapse to one record — while native ArrayExpress studies
   (E-MTAB-, E-MEXP-, E-TABM-) are kept. Do NOT pre-filter E-GEOD- accessions at query time:
   doing so silently drops studies the GEO step may have missed.

3. **Controlled-access datasets**: dbGaP, EGA, and JGA studies require institutional data access
   agreements. Include them in the catalog but clearly label as "Controlled" access.

4. **Relevance audit is keyword-based**: Automated scoring produces false positives (unrelated
   studies mentioning disease keywords) and false negatives (relevant studies with unusual
   terminology). Always manually review REMOVE candidates before excluding.

5. **Short-synonym false positives**: Disease abbreviations like `ALS`, `AD`, `SCD`, `MND`,
   `FTD` are short enough to match inside common English words when used as bare substrings
   (`ALS` → `animals`, `reveals`, `individuals`; `AD` → `loaded`, `reading`; `SCD` →
   `muscle-derived`). All keyword matching in the query steps (3, 5, 8, 11) and the relevance
   audit (Step 14) uses word-boundary regex (`\b`) to prevent this. When tuning keyword lists
   for a new disease, prefer full disease names over abbreviations, and always test short
   synonyms against a sample of known false-positive titles before trusting the catalog.

6. **GEO retmax cap**: Each individual query is capped at 100 results. For common diseases
   (e.g., Alzheimer's, cancer), run many targeted queries to maximize recall.

7. **PRIDE sample counts**: Not always reported in the API response. Report as "N/A" when missing.

8. **CellxGene disease ontology**: CellxGene uses MONDO ontology terms for disease annotation.
   Look up the correct MONDO ID for your disease before filtering cell metadata.

9. **Metabolomics is data-sparse**: For most rare diseases, metabolomics datasets are few.
   Check Metabolomics Workbench and HMDB disease pages manually if OmicsDI returns nothing.

10. **Spatial transcriptomics is emerging**: Most spatial data (Visium, MERFISH) is in GEO or
   CellxGene. The field is growing rapidly — search with recent year filters.

---

## Example Usage

**User prompt**: "Find all publicly available omics datasets for Alzheimer's disease"

**Skill execution**:
1. Synonyms: `["AD", "Alzheimer", "LOAD", "EOAD", "dementia", "amyloid", "tau"]`
2. Tissue terms: brain, cortex, hippocampus, CSF, blood, iPSC neurons, microglia, astrocytes
3. Gene terms: APOE, APP, PSEN1, PSEN2, TREM2, BIN1, CLU, ABCA7
4. Run 30+ GEO queries, ArrayExpress, PRIDE, OmicsDI, CellxGene, Expression Atlas, Zenodo, Figshare
5. Classify omics types, audit relevance
6. Output: `alzheimers_omics_datasets_MASTER.csv`, `alzheimers_omics_datasets_VALIDATED.csv`, `alzheimers_omics_summary.md`

**User prompt**: "What RNA-seq data is available for BCL11A in erythroid cells?"

**Skill execution**:
1. Topic: BCL11A erythroid
2. Synonyms: `["BCL11A", "CTIP1", "fetal hemoglobin", "HbF", "globin switching"]`
3. Tissue terms: erythroid, HUDEP, CD34, erythroblast, reticulocyte
4. Run targeted GEO + ArrayExpress queries; skip PRIDE/metabolomics (RNA-seq focus)
5. Output: `bcl11a_erythroid_omics_datasets_MASTER.csv`

**User prompt**: "Survey all cancer proteomics datasets for pancreatic ductal adenocarcinoma"

**Skill execution**:
1. Synonyms: `["PDAC", "pancreatic cancer", "pancreatic adenocarcinoma"]`
2. Query PRIDE, jPOST, iProX, GDC/TCGA (PAAD project), CPTAC, cBioPortal
3. Skip metabolomics-only repos; focus on MS proteomics
4. Output: `pdac_omics_datasets_MASTER.csv`
