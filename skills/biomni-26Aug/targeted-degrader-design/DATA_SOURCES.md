# External Data Sources & Licenses — targeted-degrader-design

This document is the canonical record of the external **data sources** the
`targeted-degrader-design` skill retrieves, and the license terms that govern their
use and redistribution. It complements the **software-dependency** licenses (RDKit,
AutoDock Vina, OpenBabel, ADMET-AI/Chemprop, ReportLab, ADFR Suite), which are
documented in `SKILL.md` and are a separate legal matter.

> **Key point.** Some sources are public-domain/CC0 (no obligations). Others are
> **CC BY** (attribution only) or **CC BY-SA** (attribution **plus** share-alike).
> **CC BY-SA is copyleft**: commercial use is allowed, but any dataset you derive or
> redistribute that embeds CC BY-SA data must itself be released under the same
> CC BY-SA license, with attribution. Verify the license of the exact release you
> download — at least one source (Human Protein Atlas) has changed license version
> over time.

---

## Summary table

| Data source | Used in skill for | License | Commercial use | Attribution | Share-alike |
|---|---|---|---|---|---|
| **ChEMBL** (EMBL-EBI) | Target confirmation (ChEMBL IDs), bioactivity, SMILES | **CC BY-SA 3.0 Unported** | Yes | **Required** | **Required** |
| **Human Protein Atlas** (HPA) | Normal-tissue expression / target-selectivity context | **Current: CC BY 4.0** · **Archived/versioned releases: CC BY-SA 3.0** | Yes | **Required** | **Required only for CC BY-SA-era (archived) data** |
| **PubChem** (NCBI) | Warhead / E3-ligand / reference-degrader SMILES | Public domain (NCBI / U.S. Gov) | Yes | Appreciated (not required) | No |
| **RCSB PDB** | Target 3D structures for docking | Public domain / **CC0 1.0** | Yes | Cite PDB ID + depositors (good practice) | No |
| **UniProt** | Target identity / accession cross-reference | **CC BY 4.0** | Yes | **Required** | No |
| **Literature metadata** (LiteratureSearch, DOIs) | Precedent degraders (E3, warhead, exit vector, DC50/Dmax) | Per-publisher | Varies | Cite by DOI | N/A |

---

## Copyleft (CC BY-SA) sources — attribution + share-alike

### ChEMBL — CC BY-SA 3.0 Unported
- **License:** Creative Commons Attribution-ShareAlike 3.0 Unported
  (https://creativecommons.org/licenses/by-sa/3.0/).
- **Commercial use:** **Permitted.** ChEMBL explicitly allows "use, redistribution
  and adaption ... as long as appropriate attribution is given ... and ... any
  adaptations are redistributed under the same license."
- **Attribution:** Cite the current ChEMBL publication and credit ChEMBL / EMBL-EBI.
- **Share-alike:** Any derived or redistributed dataset that incorporates ChEMBL
  data must be licensed under CC BY-SA 3.0 (or a later/compatible CC BY-SA version).
- **Extra caveat:** ChEMBL includes some compound-property **calculations derived
  from commercial software** that carry additional restrictions (e.g., they should
  not be extracted in isolation to replicate the commercial process). This skill
  uses ChEMBL only for **target IDs, SMILES, and bioactivity**, not those restricted
  calculated fields.

### Human Protein Atlas — CC BY 4.0 (current) / CC BY-SA 3.0 (archived)
- **License:** The **current** HPA release is licensed **CC BY 4.0**
  (attribution only). **Older / versioned (archived) releases** (e.g. the
  `vXX.proteinatlas.org` snapshots) were licensed **CC BY-SA 3.0**.
- **Commercial use:** **Permitted** ("for your research and commercial purposes")
  under both licenses.
- **Attribution:** Always required — cite a primary HPA publication **and** link to
  proteinatlas.org (and to the specific gene/image/data URL when a specific datum is
  used).
- **Share-alike:** Applies **only** to CC BY-SA-era (archived/versioned) data. If you
  pull from the current CC BY 4.0 release, there is no share-alike obligation.
- **Action:** Verify the license of the exact HPA release/version you download before
  redistributing, because the license version changed.

---

## Permissive / public-domain sources — no share-alike

- **PubChem (NCBI):** Public domain (works produced by NCBI are not subject to U.S.
  copyright). Free for any use including commercial; attribution is good practice.
- **RCSB PDB:** Structure coordinates are in the public domain / CC0 1.0. Free for
  any use; cite the specific PDB entry and its depositors/primary citation as good
  practice.
- **UniProt:** CC BY 4.0 — attribution required, **no** share-alike.

---

## Literature metadata (LiteratureSearch / DOIs)
Bibliographic metadata (titles, authors, DOIs, abstracts) retrieved during the
precedent-dossier step is used to attribute each precedent degrader by DOI. Full-text
articles remain under their respective publisher licenses — cite by DOI and do not
redistribute full text beyond fair use.

---

## Practical guidance for skill outputs
- If a table you export (e.g. a reference-degrader table) embeds **ChEMBL** values
  or **CC BY-SA-era HPA** values, attach the **CC BY-SA** license and source
  attribution to that output.
- If an output only embeds PubChem / RCSB / current-HPA (CC BY 4.0) / UniProt data,
  provide attribution where required (CC BY 4.0 / UniProt) but **no** share-alike is
  needed.
- Keep **data-source** licensing distinct from **software** licensing: the ADFR
  Suite (`prepare_receptor`/`prepare_ligand`) is **non-commercial only** and should
  be replaced with OpenBabel / Meeko / RDKit-based prep for any commercial use — that
  is a software restriction, unrelated to the data licenses above.
