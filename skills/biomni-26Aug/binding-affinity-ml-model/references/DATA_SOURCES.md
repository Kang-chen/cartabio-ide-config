# Data Sources & Licenses — binding-affinity-ml-model

This skill uses **one external data source: ChEMBL** (bioactivity + compound
structures + clinical/approved compound library), accessed live through the
EBI ChEMBL REST API (`https://www.ebi.ac.uk/chembl/api/data`). No other external
databases are used (the skill does **not** use Human Protein Atlas, DrugBank,
PubChem, or any other source).

---

## ChEMBL

| Field | Value |
|---|---|
| **Source** | ChEMBL, EMBL-EBI |
| **Access** | ChEMBL REST API (`https://www.ebi.ac.uk/chembl/api/data`) |
| **What is used** | Activity records (`standard_type`, `standard_relation`, `standard_value`, `standard_units`, canonical SMILES), target records, and small-molecule clinical/approved library (`max_phase`) |
| **License** | **Creative Commons Attribution-ShareAlike 3.0 Unported (CC BY-SA 3.0)** — <https://creativecommons.org/licenses/by-sa/3.0/> |
| **Commercial use** | **Permitted** (the license allows use, redistribution, and adaptation, including commercial), **subject to the two obligations below.** |

### Obligations under CC BY-SA 3.0 (must be honored)
1. **Attribution (BY).** Cite the current ChEMBL reference and the specific
   **ChEMBL release number** used in any work built on the data. This skill
   records the release (e.g. `ChEMBL_37`) in every run and prints/records it in
   the report. Preserve **ChEMBL IDs** in derived outputs (the skill keeps
   `molecule_chembl_id` / target `CHEMBL####` throughout).
2. **ShareAlike (SA).** If you redistribute ChEMBL data or an **adaptation** of
   it (e.g. a curated dataset derived from ChEMBL), it must be distributed under
   the **same CC BY-SA 3.0 license**. This applies to the curated dataset CSVs
   this skill emits when they are shared onward.

### Primary citation (attribution)
Cite the current ChEMBL database paper (the release notes on the ChEMBL site give
the exact current citation), and state the release number, e.g.:
> "Bioactivity data were obtained from ChEMBL (release ChEMBL_37; EMBL-EBI),
> used under CC BY-SA 3.0."

### Commercial-use nuance to be aware of
ChEMBL also contains some **compound property calculations derived from
commercial software**; those specific calculated fields carry the upstream
vendor's terms and "should not be extracted in isolation with the aim to train
models intended to replicate the commercial process." **This skill does not use
those commercial-derived property fields** — it uses raw experimental bioactivity
values (IC50/Ki/Kd/EC50 with `standard_relation`/`standard_value`/`standard_units`)
and molecular structures, then computes its own descriptors (RDKit Morgan
fingerprints, Bemis-Murcko scaffolds) locally. CC BY-SA 3.0 therefore governs the
data this skill consumes.

---

## Not used (for clarity)
- **Human Protein Atlas (HPA)** — *not used by this skill.* If a future variant
  were to add HPA-derived expression/localization data, note that HPA is
  distributed under **CC BY-SA 4.0** and would impose the same attribution +
  share-alike obligations (with the version difference); it is mentioned here
  only so the distinction is explicit, not because the skill depends on it.

---

## Summary
- **Single source:** ChEMBL (CC BY-SA 3.0), commercial-use OK **with**
  attribution (cite release + preserve ChEMBL IDs) **and** share-alike on any
  redistributed derivative datasets.
- The skill records the ChEMBL release per run and preserves ChEMBL IDs, so the
  attribution obligation is met by design; downstream redistribution of the
  curated CSVs must carry CC BY-SA 3.0.
