# External data sources & licenses

This skill is target-agnostic; the specific external data it touches depends on
which activity backend and seed source you choose. This file documents **every**
external data source the skill can use, its license, whether commercial use is
permitted, and the attribution/share-alike obligations that come with it.

**Bottom line for commercial use:** the two Creative Commons *ShareAlike* sources
below — **ChEMBL (CC BY-SA 3.0)** and the **Human Protein Atlas (CC BY-SA 4.0)** —
**do permit commercial use, but require both attribution and share-alike**
(derivative/redistributed data must carry the same license). The **Broad Drug
Repurposing Hub (CC BY 4.0)** permits commercial *use* with attribution, but the
Broad portal additionally restricts commercial *repackaging/redistribution* of the
annotations without permission (see the caveat below). Always preserve source IDs
and cite the release/version you used.

---

## Sources used in the BRAF V600E worked example

| Source | Used for | Access | License | Commercial use | Obligations |
|---|---|---|---|---|---|
| **ChEMBL** | Target bioactivity data → QSAR actives/inactives + seed actives | ChEMBL REST API (`ebi.ac.uk/chembl`) or the Biomni ChEMBL database | **CC BY-SA 3.0 Unported** | **Yes** | **Attribution + ShareAlike.** Cite the ChEMBL URL and **release version**; preserve ChEMBL IDs; license derivative data under a compatible SA license. |
| **Broad Drug Repurposing Hub** | Property-matched assumed-inactive **decoys** for the QSAR negative set | Biomni datalake (`/mnt/datalake/broad_drug_repurposing_hub/*.parquet`) | **CC BY 4.0** (per Biomni datalake `license_info`) | **Yes (with caveat)** | **Attribution.** *Caveat:* the Broad Repurposing Hub portal states the annotations are "for research purposes only" and "may not be repackaged or redistributed for commercial purposes without permission." Commercial redistributors should seek permission from the Broad. |
| **AiZynthFinder public models — USPTO templates + ZINC stock** | Tier-2 retrosynthesis (expansion/filter/ringbreaker policies + in-stock building blocks) | `download_public_data` (figshare), provisioned to a persistent cache | Software **MIT**; **USPTO** reaction data is US Patent Office public-domain data ("provided for anyone to use"); **ZINC** stock is free for research/academic use | **Yes** (USPTO public domain); **ZINC:** free for research — verify ZINC terms for commercial redistribution | Cite AiZynthFinder (Genheden et al. 2020) and the USPTO/ZINC provenance. Routes are template proposals, not validated syntheses. |

### Attribution strings to include in derived outputs
- **ChEMBL:** "ChEMBL data from https://www.ebi.ac.uk/chembl (ChEMBL release <NN>)." Primary citation: Mendez D, et al. *ChEMBL: towards direct deposition of bioassay data.* Nucleic Acids Res. 2019;47(D1):D930-D940. doi:10.1093/nar/gky1075. Licensed CC BY-SA 3.0.
- **Broad Drug Repurposing Hub:** Corsello SM, et al. *The Drug Repurposing Hub: a next-generation drug library and information resource.* Nat Med. 2017;23:405-408. doi:10.1038/nm.4306. Licensed CC BY 4.0 (portal research-use / no-commercial-redistribution caveat applies).
- **AiZynthFinder / USPTO / ZINC:** Genheden S, et al. *AiZynthFinder: a fast, robust and flexible open-source software for retrosynthetic planning.* J Cheminform. 2020;12:70. doi:10.1186/s13321-020-00472-1. USPTO reaction data (US Patent Office, public domain); ZINC database (Irwin & Shoichet).

---

## Optional supported sources (NOT used in the BRAF V600E worked example)

The skill supports these but they were not exercised in the BRAF run. Documented
so you know the license terms before switching backend/seed source.

| Source | Would be used for | Access | License | Commercial use | Obligations |
|---|---|---|---|---|---|
| **Human Protein Atlas (HPA)** | Optional tissue/expression context or target-specificity filtering | Biomni datalake `human_protein_atlas` / hpa web | **CC BY-SA 4.0** | **Yes** | **Attribution + ShareAlike** (same family as ChEMBL: cite HPA + version; SA on derivatives). |
| **PyTDC (Therapeutics Data Commons) oracles** | `tdc_oracle` activity backend (DRD2, GSK3B, JNK3, ...) | `tdc.Oracle(name=...)` (pretrained, ~35 MB one-time) | Software **MIT**; wrapped datasets carry dataset-specific licenses | Software: Yes; **check each wrapped dataset** | Cite TDC (Huang et al. 2021) and the specific oracle/dataset. **Not applicable to BRAF** — no TDC oracle exists for BRAF, which is why the worked example uses the QSAR backend. |
| **User-provided SMILES / labelled CSV** | Seeds and/or QSAR training labels | User upload | User-owned | Per user | Whatever license the user's own data carries. |

---

## Software dependencies (not data, listed for completeness)
- **RDKit** — BSD-3-Clause (mols, QED, SA_Score contrib, FilterCatalog/PAINS-BRENK, BRICS, drawing). No external data.
- **scikit-learn** — BSD-3-Clause (QSAR RandomForest backend).
- **aizynthfinder**, **PyTDC**, **crem** — MIT (see each project's LICENSE).

## Reproducibility note
When you run the pipeline, record the **ChEMBL release version** returned by the
API (or the datalake snapshot date) in your report's Methods so the bioactivity
provenance is reproducible, and preserve source IDs (ChEMBL IDs, Broad IDs) in any
derived tables.
