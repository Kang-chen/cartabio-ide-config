# Percentile Reference Set — Attribution & License

The ADMET percentile columns produced by this skill
(`<endpoint>_chembl_approved_percentile`) rank each query compound against the
reference distribution in **`chembl_approved_reference.csv`**. This file documents
the source, license, and provenance of that reference set.

## Why this reference exists

ADMET-AI ships its percentile reference as **DrugBank** approved-drug data, which
is licensed **CC BY-NC 4.0** (non-commercial use only). To keep this skill's
workflow free of commercial-use restrictions, the DrugBank reference is replaced
with an approved small-molecule drug set derived from **ChEMBL**, which is
licensed **CC BY-SA 3.0** and permits commercial use with attribution and
share-alike.

Swapping the reference affects **only** the percentile *context* columns. Every
absolute ADMET prediction, structural-alert flag, and drug-likeness call is
identical to stock ADMET-AI.

## Source data — ChEMBL

- **Source:** ChEMBL database, European Bioinformatics Institute (EMBL-EBI).
- **Website:** https://www.ebi.ac.uk/chembl/
- **Query:** approved drugs (`max_phase = 4`), `molecule_type = "Small molecule"`,
  retrieved via the ChEMBL REST API.
- **License:** **CC BY-SA 3.0** — https://creativecommons.org/licenses/by-sa/3.0/
  - **BY** — attribution required (see below).
  - **SA** — derivative/redistributed versions of the ChEMBL-derived data must be
    shared under the same CC BY-SA 3.0 license.
  - **Commercial use permitted.**
- **Citation:** Zdrazil B., Felix E., Hunter F., et al. (2024) "The ChEMBL
  Database in 2023: a drug discovery platform spanning multiple bioactivity data
  types and time periods." *Nucleic Acids Research* 52(D1):D1180–D1192.
  doi:10.1093/nar/gkad1004.

**Required attribution statement:**
> Data derived from the ChEMBL database, European Bioinformatics Institute
> (EMBL-EBI), used under CC BY-SA 3.0.

## What the reference file contains

- **Structures:** ChEMBL approved small molecules, standardized with the same
  pipeline used for analysis inputs (desalt → parent fragment → neutralize →
  canonicalize), then de-duplicated by canonical SMILES and filtered to
  molecular weight in [100, 1500] Da.
- **Values:** the 52 ADMET-AI property/prediction columns. These reference values
  are **ADMET-AI model predictions** on the ChEMBL SMILES set — the same way
  ADMET-AI's original DrugBank reference distribution was produced — **not**
  experimental measurements.
- **Provenance:** machine-readable details (molecule count, versions, build date)
  are in `chembl_approved_reference.meta.json`.

## Reference values were generated with ADMET-AI

- **ADMET-AI** — Swanson et al. (2024) *Bioinformatics* 40(7):btae416. License: MIT.
- **RDKit** (standardization) — https://www.rdkit.org/. License: BSD-3-Clause.

## Regenerating this reference

Run `scripts/build_reference_set.py` to rebuild the reference against a newer
ChEMBL release (requires the `requests` package for the ChEMBL REST API). The
shipped file is sufficient for normal use; regeneration is optional.

## Share-alike note

Because ChEMBL is CC BY-SA 3.0, if you redistribute `chembl_approved_reference.csv`
(or a modified reference derived from it), redistribute it under CC BY-SA 3.0 with
the attribution statement above. This requirement applies to the reference data
file, not to your own query molecules or analysis outputs.
