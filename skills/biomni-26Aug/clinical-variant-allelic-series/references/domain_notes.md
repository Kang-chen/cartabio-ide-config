# Biological Interpretation Notes

How to read and correctly caveat a clinical allelic series produced by this skill.

---

## Actionability tiers (what they mean, what they don't)

The tier is a **summary of curated evidence at data-access time**, not a clinical
recommendation and not an AMP/ASCO/CAP tier.

| Tier | Rule (in `build_allelic_series.py`) | Interpretation |
|---|---|---|
| **Tier 1** | CIViC predictive evidence present **AND** a sensitivity or resistance therapy is linked | Therapeutically actionable — a drug association exists |
| **Tier 2** | any curated CIViC evidence **OR** ClinVar significance contains "pathogenic" (and not "conflicting") | Clinically meaningful but not (yet) drug-linked here |
| **Tier 3** | everything else | Uncertain / other — **absence of evidence, not evidence of benignity** |

`LEVEL_RANK = {A:5, B:4, C:3, D:2, E:1}` (CIViC evidence levels; A = strongest, e.g.
professional guidelines / validated in trials; E = preclinical/inferential).

Typical distributions observed:
- **BRAF** (oncogene, mature target): Tier1 38, Tier2 105, Tier3 843.
- **KIT** (oncogene): Tier1 46, Tier2 86, Tier3 2306.
- **STK11** (tumor suppressor): Tier1 4, Tier2 249, Tier3 1543 — few actionable alleles,
  many pathogenic LoF variants. This shape is expected and correct for a suppressor.

## Mechanism inference (order matters)

Applied first-match-wins in `infer_mechanism()`:
1. predictive evidence → **Actionable (predictive evidence)**
2. oncogenicity contains "oncogenic" → **Oncogenic (ClinVar)**
3. consequence is nonsense/frameshift/stop/splice → **Predicted loss-of-function**
4. in-frame insertion/deletion → **In-frame indel**
5. significance contains "pathogenic" → **Pathogenic (ClinVar)**
6. missense → **Missense (uncertain/other)**
7. else → **Other annotated**

This is a coarse annotation for the landscape figure, not a functional assay result.

## Functional classes are literature-derived (cite them)

Several genes have well-established functional-class frameworks that explain *why* alleles
at different positions respond to different drugs. These are **not computed by this skill** —
if you state them in a report, ground them with `LiteratureSearch`. Examples:

- **BRAF**: Class I (V600, RAS-independent active monomers → RAF/MEK-inhibitor sensitive),
  Class II (non-V600 RAS-independent dimers, e.g. G469A, K601E), Class III (kinase-impaired,
  RAS-dependent, e.g. D594G → can be resistant to anti-EGFR and depend on upstream signaling).
  This is why the BRAF therapy matrix shows divergent V600 vs. non-V600 profiles.
- **KIT** (GIST): exon 11 juxtamembrane mutations are imatinib-sensitive; exon 17 kinase
  activation-loop mutations (e.g. D816V) and gatekeeper T670I confer resistance. The lollipop
  captures both hotspot regions.
- **EGFR** (NSCLC): classical sensitizing (exon 19 del, L858R) vs. resistance (T790M gatekeeper,
  C797S) vs. exon 20 insertions (largely TKI-refractory except specific agents).

## Reading the figures

- **F1 lollipop** — x = residue position over the UniProt domain track; a faint rug shows the
  *full* ClinVar allele density; lollipop markers are the notable/evidenced alleles (stem
  height ∝ curated evidence count; color/shape = CIViC-actionable / ClinVar P-LP / other).
  When notable alleles cluster in a sub-window, a zoom panel is auto-added.
- **F2 landscape** — distribution of alleles across tiers and mechanism categories.
- **F3 evidence** — CIViC evidence by type and level (only for evidenced genes).
- **F4 therapy matrix** — allele × therapy; **blue = sensitivity, red = resistance**, cell
  value encodes the highest evidence level, `±` = both directions reported. This is the single
  most decision-relevant figure and the clearest place class-biology shows up.

## Mandatory caveats to include in any report

1. Tiers reflect curated evidence at access time — not guideline-based clinical interpretation.
2. Actionability is frequently **tumor-type/lineage dependent**; this series is tissue-agnostic.
3. CIViC coverage is deepest for recurrent hotspots — rare alleles are under-annotated.
4. ClinVar classifications can be **conflicting**; they are reported as submitted.
5. Functional-class statements are from the literature, not computed per allele.
6. Refresh against the latest ClinVar/CIViC releases before any clinical use.

## Sensible next-step analyses to offer

- Overlay tumor-type context (melanoma vs. CRC vs. NSCLC) since actionability is lineage-specific.
- Weight alleles by observed clinical recurrence using commercially-usable sources: cBioPortal / AACR GENIE cohort mutation frequencies (subject to their data-use terms) and the number of ClinVar submissions per variant as a license-clean recurrence proxy.
- Add computed functional-class / structural prediction for non-hotspot alleles.
- Integrate gnomAD population frequency to flag likely-benign common variants.
