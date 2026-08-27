# Data sources & licensing — `drug-bioactivity-chembl`

This skill retrieves external data. All sources, their licenses, and the required
attribution are documented here. **Any report or dataset derived from this skill
must attribute the sources below and honor their obligations.**

---

## Summary table

| Source | Role in skill | License | Commercial use | Attribution | Share-alike |
|--------|---------------|---------|:--------------:|:-----------:|:-----------:|
| **ChEMBL** (EMBL-EBI) | Primary and only external **data** source: all IC50 / Ki / Kd (± EC50 / Potency) bioactivity records, molecule resolution, assay & target metadata (via public REST API) | **CC BY-SA 3.0** (Creative Commons Attribution-Share Alike 3.0 Unported) | ✅ Permitted | ✅ Required | ✅ Required |
| **Human Protein Atlas (HPA)** | **Not used by this skill** (listed for cross-skill consistency only) | **CC BY-SA** | ✅ Permitted | ✅ Required | ✅ Required |
| `predict_admet_properties` (`biomni.tool.pharmacology`) | Optional *predicted* ADMET context from SMILES — a **model**, not a data source | n/a (model output) | n/a | Label as *predicted* | n/a |

---

## 1. ChEMBL (the data source this skill uses)

- **Provider:** EMBL's European Bioinformatics Institute (EMBL-EBI).
- **Access:** public REST API — `https://www.ebi.ac.uk/chembl/api/data`. No API
  key, registration, or license acceptance step is required for public REST access.
- **What is pulled:** bioactivity records (`standard_type`, `standard_value`,
  `standard_units`, `standard_relation`, `pchembl_value`, `data_validity_comment`),
  molecule records (`molecule_chembl_id`, `pref_name`, `max_phase`,
  `canonical_smiles`), and assay/target metadata (`assay_description`,
  `assay_type`, `bao_label`, `target_chembl_id`, `target_pref_name`,
  `target_organism`, document IDs/years/journals).
- **License:** **Creative Commons Attribution-Share Alike 3.0 Unported
  (CC BY-SA 3.0).** Verbatim from EMBL-EBI's ChEMBL documentation:
  > "Access to the web interface of ChEMBL is made under the EBI's Terms of Use.
  > The ChEMBL data is made available on a Creative Commons Attribution-Share
  > Alike 3.0 Unported License."
- **What CC BY-SA 3.0 means here:**
  - **Commercial use is permitted.** The license places no non-commercial
    restriction; ChEMBL data may be used in commercial settings.
  - **Attribution (BY) is required.** You must credit ChEMBL as the data source
    (and, where practical, the ChEMBL release and the underlying primary-literature
    documents that ChEMBL abstracts).
  - **Share-alike (SA) is required.** If you distribute a modified or adapted
    version of the data (the skill's curated/aggregated CSVs are an *adaptation*
    of ChEMBL), you must license that redistributed dataset under the **same
    CC BY-SA 3.0** terms.
- **License text:** https://creativecommons.org/licenses/by-sa/3.0/
- **How to cite / attribute (use in reports):**
  - Short data-source line (place in every report's data/attribution note):
    > "Bioactivity data from ChEMBL (EMBL-EBI), licensed under CC BY-SA 3.0.
    > Derived datasets are redistributed under the same CC BY-SA 3.0 terms."
  - Formal citation (see the ChEMBL "Cite Us" FAQ and per-release DOIs on the
    ChEMBL Downloads page):
    > Zdrazil B, Felix E, Hunter F, et al. The ChEMBL Database in 2023: a drug
    > discovery platform spanning multiple bioactivity data types and time
    > periods. *Nucleic Acids Research*. 2024;52(D1):D1180-D1192.
    > doi:10.1093/nar/gkad1004
  - Reference a specific release with its DOI where the analysis depends on a
    particular ChEMBL version (DOIs listed on the ChEMBL Downloads page).

## 2. Human Protein Atlas (HPA) — NOT used by this skill

- HPA is **not queried** by the current workflow. It is documented here only so
  that this skill's data-source record is consistent with other Biomni skills
  that *do* use HPA.
- **If** a future variant of this skill pulls HPA data (e.g. normal-tissue
  expression or subcellular-localization context for a target), note that HPA is
  released under **CC BY-SA** — **commercial use permitted**, with **attribution
  and share-alike** required (same obligation shape as ChEMBL).
- Do **not** claim HPA provenance in any report unless HPA is actually queried in
  that run.

## 3. `predict_admet_properties` (model, not a data source)

- Provided by `biomni.tool.pharmacology`; produces **predicted** ADMET / physchem
  properties from a compound's SMILES.
- These are **model outputs, not measured ChEMBL data**, and carry **no
  data-license obligation** under this skill. Always label them as *predicted*
  and never merge them with measured bioactivity.

---

## Obligations checklist (apply on every run)

- [ ] Report includes a data-source line attributing **ChEMBL (CC BY-SA 3.0)**.
- [ ] Any redistributed CSVs derived from ChEMBL are noted as **adaptations under
      CC BY-SA 3.0** (share-alike inherited).
- [ ] Predicted ADMET (if shown) is explicitly labeled **predicted**, not measured.
- [ ] HPA provenance is claimed **only** if HPA was actually queried (it is not,
      by default).
