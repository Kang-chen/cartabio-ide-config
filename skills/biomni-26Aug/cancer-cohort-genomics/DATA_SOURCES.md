# Data Sources, Attribution & Notices

This skill retrieves tumor genomic data exclusively from **cBioPortal's public
open-access REST API** (`https://www.cbioportal.org/api`). No controlled-access or
dbGaP-protected data is accessed.

## Required acknowledgment (include in every report)

> Tumor genomic data from The Cancer Genome Atlas (TCGA) open-access tier, obtained
> via cBioPortal (https://www.cbioportal.org); used per NIH GDC data-use policy.

## Required citations

- **Cerami E, Gao J, Dogrusoz U, et al.** The cBio Cancer Genomics Portal: An Open
  Platform for Exploring Multidimensional Cancer Genomics Data. *Cancer Discovery.*
  2012;2(5):401–404. doi:10.1158/2159-8290.CD-12-0095
- **Gao J, Aksoy BA, Dogrusoz U, et al.** Integrative Analysis of Complex Cancer
  Genomics and Clinical Profiles Using the cBioPortal. *Science Signaling.*
  2013;6(269):pl1. doi:10.1126/scisignal.2004088

When a specific study is analyzed, also cite that study's primary publication where
available (e.g. the MSK-IMPACT cohort: Zehir et al., *Nat Med* 2017).

## Access tier & compliance

- **Open-access only.** The skill queries aggregate, summary-level somatic mutation
  and copy-number calls plus non-identifying clinical attributes (e.g. `CANCER_TYPE`)
  that cBioPortal serves publicly. It does **not** access raw sequence reads, germline
  variants, or any dbGaP/controlled-access resources.
- **Endpoints used (all public):** `/genes`, `/studies`, `/molecular-profiles`,
  `/sample-lists`, `/molecular-profiles/{id}/mutations/fetch`,
  `/molecular-profiles/{id}/discrete-copy-number/fetch`,
  `/studies/{id}/clinical-data/fetch` (SAMPLE-level `CANCER_TYPE`).
- **TCGA open-access tier** is usable commercially with acknowledgment; the
  acknowledgment text above satisfies this. Follow the NIH GDC data-use policy
  (https://gdc.cancer.gov/access-data/data-access-policies).
- If a future change introduces any controlled-access, dbGaP, or non-cBioPortal
  endpoint, **flag it for data-use review** before use — it is out of scope for this
  open-access skill.
