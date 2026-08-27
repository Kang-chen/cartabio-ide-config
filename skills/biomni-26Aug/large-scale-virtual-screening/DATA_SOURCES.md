# Data Sources & Licenses

The `large-scale-virtual-screening` skill retrieves data from the public external sources below at
run time. It does **not** bundle these datasets; it fetches them on demand. Anyone using this skill's
outputs — **especially for commercial purposes** — must comply with each source's license, most notably
**ChEMBL's attribution + share-alike (copyleft) terms**.

This file is the authoritative, detailed record; `SKILL.md` carries a summary that points here.

---

## Summary table

| Source | Where the skill uses it | License | Commercial use | Obligations on your outputs |
|---|---|---|---|---|
| **ChEMBL** | `build_library.py --chembl-target` (via `chembl_webresource_client`); provides target bioactivities / actives | **CC BY-SA 3.0 Unported** | **Allowed** | **Attribution** (URL + release version, preserve ChEMBL IDs) **+ Share-Alike** (derivatives under CC BY-SA 3.0) |
| **RCSB PDB** | `fetch_receptor.py --pdb-id` (downloads `https://files.rcsb.org/download/<ID>.pdb`); provides the receptor structure (e.g. 6LU7) | Public domain — **CC0 1.0** | Allowed | None legally required; citing the PDB and the specific entry/depositors is good practice |
| **DUD-E** (Directory of Useful Decoys, Enhanced) | `build_library.py --dude-dir`; optional labeled actives/decoys benchmark | Freely available for research (dude.docking.org); **not an explicit commercial license** | **Verify before commercial use** | Cite Mysinger, Carchia, Irwin & Shoichet, *J. Med. Chem.* 2012 |
| **TDC / DeepPurpose ADMET models** | `admet_annotate.py` via the Biomni `predict_admet_properties` tool (advisory ADMET on top hits) | Therapeutics Data Commons datasets: **CC BY 4.0**; DeepPurpose code: **BSD-3-Clause** | Allowed | Attribution to TDC (Huang et al.) and DeepPurpose (Huang et al.); outputs are **advisory only**, never a filter |
| **Enamine REAL / datalake** | `build_library.py --datalake` / `--enamine` (optional heavy at-scale mode; **off by default, not used in the standard workflow**) | **Enamine terms of use** | **Check Enamine terms** | Per Enamine's license/agreement for the REAL library |

> **Human Protein Atlas (HPA)** — **not used by this skill.** Listed here only for completeness: HPA is
> released under **CC BY-SA 4.0** (commercial use allowed, but attribution + share-alike required). If a
> future revision adds an HPA-derived layer (e.g. tissue-expression context for a target), add it to the
> table above and carry the same CC BY-SA obligations into any redistributed derivative.

---

## ChEMBL — the copyleft source that matters most here (CC BY-SA 3.0)

The default, first-class way this skill builds a library is a **ChEMBL target pull** of bioactive
compounds (`build_library.py --chembl-target CHEMBLxxxx`). That makes ChEMBL's license the primary
compliance consideration for any product built on this skill.

- **License:** Creative Commons **Attribution-ShareAlike 3.0 Unported (CC BY-SA 3.0)**.
- **Commercial use:** **Permitted.** CC BY-SA is a permissive, commercial-friendly license.
- **But it is "copyleft" (share-alike).** Two obligations attach to *derivatives*:
  1. **Attribution (BY):** credit ChEMBL, including the **resource URL and the release/version number**,
     and **preserve the ChEMBL IDs** in the data you distribute.
  2. **Share-Alike (SA):** if you distribute an **adaptation** of ChEMBL data, you must license that
     adaptation under **CC BY-SA 3.0** (or a later/compatible CC BY-SA version).
- **What counts as a derivative in this skill:** `library/master_library.csv` built from a ChEMBL pull,
  and anything computed from it that still embeds the ChEMBL compounds/IDs — e.g. `all_scores_merged.csv`,
  `molecular_descriptors.csv`, `tables/top_hits.csv`. If you **redistribute** any of these, they inherit
  CC BY-SA 3.0 and the attribution requirements above. (Using them **internally** does not trigger the
  distribution obligations, but attribution remains good practice.)
- **Suggested attribution string** (put it on the entry portal / in the docs of any redistributed work):

  > "ChEMBL data from https://www.ebi.ac.uk/chembl — ChEMBL release <version> — licensed under CC BY-SA 3.0.
  > ChEMBL IDs preserved."

  Set `<version>` to the actual release used (e.g. `ChEMBL_35`).

**Authoritative references:**
- License text: https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/latest/LICENSE
- Attribution requirement: https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/latest/REQUIRED.ATTRIBUTION
- Overview: https://www.ebi.ac.uk/chembl/ and https://chembl.gitbook.io/chembl-interface-documentation/about

---

## RCSB PDB structures — public domain (CC0)

`fetch_receptor.py --pdb-id` downloads coordinate files from RCSB (`files.rcsb.org`). PDB structure data
are released into the **public domain under CC0 1.0** — free for any use, including commercial, with **no
legal attribution requirement**. It is nonetheless customary and encouraged to cite the PDB and the
specific entry (and the primary citation for the structure, e.g. 6LU7: Jin et al., *Nature* 2020).

---

## DUD-E benchmark — research use; verify for commercial

`build_library.py --dude-dir` can ingest DUD-E-style `actives_final.ism` / `decoys_final.ism` files for a
labeled benchmark. DUD-E is distributed freely for research from dude.docking.org but **does not carry an
explicit open commercial license**; confirm terms before any commercial redistribution. Cite:
Mysinger MM, Carchia M, Irwin JJ, Shoichet BK. *J. Med. Chem.* 2012, 55(14):6582-6594.

> **Note:** the standard/default workflow in this skill does **not** require DUD-E — it builds a labeled
> benchmark from ChEMBL actives + property-matched decoys. DUD-E is one optional input branch.

---

## TDC / DeepPurpose ADMET models — advisory only

`admet_annotate.py` (an **optional, advisory** step) calls the Biomni `predict_admet_properties` tool,
which is backed by **DeepPurpose** MPNN models trained on **Therapeutics Data Commons (TDC)** datasets
(solubility, Caco-2, HIA, bioavailability, BBB, PPBR, CYPs, half-life, clearance, clinical toxicity).

- **TDC datasets:** **CC BY 4.0** (commercial use allowed with attribution). Cite Huang et al.,
  *Nat. Chem. Biol.* 2022 / NeurIPS 2021.
- **DeepPurpose:** **BSD-3-Clause** (permissive; commercial use allowed with attribution). Cite Huang et al.,
  *Bioinformatics* 2020.
- These predictions are **advisory context only** in this skill — never used as a hit filter.

---

## Compliance checklist for downstream/commercial use

1. **Redistributing a ChEMBL-derived compound set** (library or scored tables)? Attach the ChEMBL
   attribution string (URL + release version), keep ChEMBL IDs, and license it **CC BY-SA 3.0**.
2. **Shipping PDB-derived receptor files?** No legal obligation (CC0); cite the entry as courtesy.
3. **Using DUD-E commercially?** Confirm DUD-E terms first; it is research-oriented.
4. **Surfacing ADMET predictions?** Attribute TDC + DeepPurpose; present as advisory, not decisions.
5. **Adding an HPA layer later?** Document it here and carry CC BY-SA 4.0 (attribution + share-alike).
