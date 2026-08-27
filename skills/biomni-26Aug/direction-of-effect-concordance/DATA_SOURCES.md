# External Data Sources & Licenses

This skill is **evidence synthesis over external, third-party data**. It pulls no proprietary
data of its own; every structured readout and citation comes from a public scientific
resource. This file records **which source feeds which evidence axis**, the **license** each
source is distributed under, and the **reuse obligations** (attribution / share-alike) and
**commercial-use** status that a user must honor when they run this skill or redistribute its
outputs.

> **Scope of this document.** This is *documentation only*. It does not change what the skill
> pulls, how it maps evidence, or what the report says. It exists so that users (and any
> downstream/commercial user) can see the licensing footprint of the sources at a glance and
> comply with each source's attribution and share-alike terms.

> **Always verify at run time.** Licenses change between releases (e.g., a source can move from
> CC BY-SA 3.0 to CC BY 4.0 across versions). Treat the table below as a maintained summary,
> not a legal guarantee, and re-check the source's own license page and the specific release
> you actually used. Cite the release/version you queried.

---

## Two source terms you must honor explicitly (called out per request)

- **ChEMBL — Creative Commons Attribution-ShareAlike 3.0 Unported (CC BY-SA 3.0).**
  **Commercial use is permitted**, but the license is **copyleft**: you must (a) **give
  attribution** — cite the ChEMBL resource URL *and* the release version (e.g. "ChEMBL data
  from https://www.ebi.ac.uk/chembl - ChEMBL_35") and the current ChEMBL paper, and preserve
  ChEMBL IDs — and (b) **share-alike** — any *adaptation* that incorporates ChEMBL data must be
  redistributed under the same (or a CC-compatible) license. This is only relevant here if the
  optional ChEMBL/OpenFDA drug-depth layer is used.

- **Human Protein Atlas (HPA) — Creative Commons Attribution-ShareAlike (CC BY-SA), with a
  version nuance.** HPA explicitly **permits commercial use** but requires **proper citation**
  (a primary HPA publication **and** a link to proteinatlas.org, displayed alongside any HPA
  content). Historically (portal versions ~v19-v22) HPA was released under **CC BY-SA 3.0**
  (share-alike); the **current** license page states **CC BY 4.0** for copyrightable parts.
  Document/attribute according to the **specific HPA version** you pull, and apply the
  **share-alike** obligation if you use a CC BY-SA-licensed HPA version. HPA is an **optional /
  contextual** source for this skill (surfaceome / tissue-expression context); it is not
  required for the default four-axis run.

**Copyleft in practice.** For any **CC BY-SA** source (ChEMBL, CC BY-SA versions of HPA, and
KEGG/BioCarta-derived MSigDB gene sets), if you build a *derivative dataset* that embeds that
source's data and then redistribute it, the derivative must carry a compatible share-alike
license. Simply *citing* a numeric value in a report is normal scholarly attribution and does
not "infect" your whole report; redistributing an *adapted copy of the data* is what triggers
share-alike. When in doubt, attribute the source, keep its IDs/release, and license any
redistributed adaptation under the same terms.

---

## Source-by-source summary

| Source | Evidence axis / role in this skill | Access | License | Commercial use | Attribution / share-alike obligations |
|---|---|---|---|---|---|
| **Open Targets Platform** | Drug MoA/action type, mouse phenotypes, target-disease genetic-association, tractability, safety liabilities (Steps 1-2) | GraphQL API (`pull_opentargets.py`) | **CC0 1.0** (public domain) | Yes, unrestricted | No legal requirement; **cite the release** (`meta { dataVersion { year month } }`) as good practice |
| **DepMap (Broad)** — CRISPRGeneEffect / CRISPRGeneDependency | Functional/CRISPR essentiality axis (Step 2) | Data-lake CSV (`pull_depmap.py`) | **CC BY 4.0** | Yes | **Attribution required** (Broad DepMap + release, e.g. 24Q…); some bundled datasets ask for specific paper citations |
| **GeneBass** | Human-genetics axis — exome pLoF / missense burden direction | Data-lake (Parquet/pickle) | Open for research; derived from **UK Biobank** exomes (tooling Apache-2.0) | Check UKB terms for large-scale/commercial | Attribute GeneBass + the UK Biobank resource; heed UK Biobank access terms |
| **GWAS Catalog (NHGRI-EBI)** | Human-genetics axis — common-variant direction of effect | Web/API | **Summary statistics: CC0**; other catalog data under **EMBL-EBI Terms of Use**; code Apache-2.0; diagrams CC BY 4.0 | Yes | CC0 stats need no attribution but **cite the original study**; per-study "Usage License" can differ — check it |
| **gnomAD** | Human-genetics axis — LoF-intolerance / constraint context (pLI, LOEUF) | Web/API/data-lake | **CC0** (public domain) | Yes, unrestricted | Attribution *requested* (not required); do not attempt participant re-identification |
| **GTEx** | Optional expression / eQTL axis (tissue expression direction) | Data-lake | **Open-access** summary data (portal terms); individual-level genotypes are **controlled-access (dbGaP)** | Open-access layer: yes | Cite the GTEx Consortium; controlled-access data require dbGaP authorization |
| **MouseMine / MGI** | Mouse-KO axis — phenotype gene sets (context beyond Open Targets `mousePhenotypes`) | Web/API | **CC BY 4.0** (MGI) | Yes | Attribution required (MGI / Mouse Genome Informatics) |
| **ChEMBL (EMBL-EBI)** | *Optional* drug-MoA / pharmacovigilance depth | Web/API | **CC BY-SA 3.0 Unported** | **Yes** | **Attribution + share-alike** — cite resource URL + release, preserve ChEMBL IDs, license adaptations alike (see call-out above) |
| **OpenFDA (US FDA)** | *Optional* drug-safety / pharmacovigilance depth | API | **U.S. Government public domain** (CC0-like); no warranty | Yes | No attribution required; do not imply FDA endorsement |
| **Human Protein Atlas** | *Optional* surfaceome / tissue-expression context | Web/API/downloads | **CC BY-SA 3.0** (historical) / **CC BY 4.0** (current) | **Yes** | **Attribution required** (primary HPA paper + proteinatlas.org link); **share-alike** for CC BY-SA versions (see call-out above) |
| **MSigDB (Broad/UCSD/MIT)** | Optional pathway / gene-set context | Web/download | **CC BY 4.0** core; **some gene sets restricted** (KEGG-derived = **CC BY-SA 4.0**; BioCarta/KEGG legacy carry extra terms) | Core: yes | Attribution required; for KEGG/BioCarta-derived sets honor the stricter per-set terms (incl. share-alike for KEGG_MEDICUS) |
| **PrimeKG** | Optional target-disease-drug knowledge-graph context | Data-lake | Open research release (aggregates many sources under their own licenses) | Depends on constituent sources | Attribute PrimeKG **and** respect the licenses of its constituent primary sources |
| **Broad Drug Repurposing Hub** | Optional molecule -> MoA/target context | Web/download | Open for research (Broad terms) | Check Broad terms | Attribute the Repurposing Hub + associated publication |
| **LiteratureSearch (Biomni tool: Consensus + Exa)** | Directional evidence for **all** axes (Step 3) | Biomni tool -> `references.jsonl` | Tool aggregates **third-party publication metadata**; cite each **primary paper** by its own terms | N/A (metadata) | Cite the underlying papers (DOIs), not the aggregator; publisher terms govern full text |

---

## Default vs. optional sources

- **Required for the default four-axis run:** Open Targets (CC0), DepMap (CC BY 4.0),
  human-genetics resources (GeneBass; GWAS Catalog summary stats CC0; gnomAD CC0), Open Targets
  `mousePhenotypes` for the mouse-KO axis, and the Biomni `LiteratureSearch` tool.
- **Optional / only if the user extends the skill:** ChEMBL (**CC BY-SA 3.0**), OpenFDA
  (public domain), Human Protein Atlas (**CC BY-SA / CC BY**), GTEx (optional eQTL axis),
  MouseMine/MGI, MSigDB, PrimeKG, Broad Drug Repurposing Hub.

Because the **default** axes lean on **CC0 / CC BY** sources, a default run has a light
attribution footprint (cite releases; attribute DepMap/MGI). The **share-alike** obligation
only enters if a user turns on a **CC BY-SA** source (ChEMBL, a CC BY-SA HPA version, or
KEGG-derived MSigDB sets) **and** redistributes an adapted copy of that data.

---

## Attribution snippets (ready to paste)

- **Open Targets:** "Target-evidence data from the Open Targets Platform (release <YYYY.MM>),
  CC0 1.0."
- **DepMap:** "CRISPR gene-effect/dependency data from DepMap (Broad Institute), release
  <24Q…>, CC BY 4.0."
- **gnomAD:** "Constraint metrics from gnomAD (<version>), CC0 1.0."
- **GWAS Catalog:** "GWAS summary statistics from the NHGRI-EBI GWAS Catalog (CC0); primary
  study: <citation>."
- **ChEMBL (if used):** "ChEMBL data from https://www.ebi.ac.uk/chembl - <ChEMBL_release>,
  CC BY-SA 3.0; adaptations redistributed under CC BY-SA."
- **Human Protein Atlas (if used):** "Data from the Human Protein Atlas (proteinatlas.org),
  <version>, CC BY-SA 3.0 / CC BY 4.0; see <primary HPA publication>."
