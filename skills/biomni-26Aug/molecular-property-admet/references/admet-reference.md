# ADMET Endpoint Reference Guide

Interpretation guide for ADMET-AI predictions and physicochemical descriptors.

## Input Standardization & Sanity Flags

Every molecule is standardized before any property is computed, so descriptors reflect the
drug-like parent rather than a salt or charged form. Order (RDKit `MolStandardize`):
`Cleanup` (normalize/sanitize/disconnect metals) → `FragmentParent` (desalt) → `Uncharger`
(neutralize) → canonical **isomeric** SMILES.

| Column | Meaning |
|--------|---------|
| `input_smiles` | The original SMILES as provided |
| `smiles` | The standardized parent (NaN if standardization failed) |
| `standardization_note` | What changed: `ok`, `desalted (...)`, `neutralized`, `parse_failed`, `standardization_failed:*` |
| `sanity_flag` | `""` if drug-like; else `no_carbon/inorganic`, `MW<100`, `MW>1500`, or `standardization_failed` |
| `names_aggregated` | Names of all inputs that collapsed into this unique structure |
| `mol_id` | Stable unique key (e.g. `MOL_0007`) used for all plots/merges |

**Decision notes:**
- **Flagged, never dropped.** `sanity_flag` marks non-drug-like inputs but keeps them — filter on
  `sanity_flag == ""` for the clean set. This avoids silently discarding the user's data.
- **Stereoisomers stay distinct** (isomeric canonical SMILES), so (R)/(S) pairs are not merged.
- **Desalting picks the largest fragment.** For large organic counterions (e.g. pamoate) this can keep
  the wrong fragment — `standardization_note` records the removed mass so it can be reviewed; treat as advisory.

## Physicochemical Descriptors (RDKit)

| Property | Description | Drug-like Range | Method |
|----------|-------------|-----------------|--------|
| **MW** | Molecular weight (Da) | 150–500 | RDKit exact mass |
| **LogP** | Octanol-water partition coefficient | -0.4 to 5.6 | Wildman-Crippen |
| **HBD** | Hydrogen bond donors | 0–5 | Lipinski N-H, O-H count |
| **HBA** | Hydrogen bond acceptors | 0–10 | Lipinski N, O count |
| **TPSA** | Topological polar surface area (Å²) | 20–140 | Ertl method |
| **RotatableBonds** | Rotatable bonds count | 0–10 | RDKit default |
| **AromaticRings** | Number of aromatic rings | 0–4 | RDKit |
| **FractionCSP3** | Fraction of sp3-hybridized carbons | >0.25 | RDKit |

## Drug-Likeness Rules

### Lipinski Rule of Five (Ro5)
A molecule is drug-like if it has ≤1 violation of:
- MW ≤ 500 Da
- LogP ≤ 5
- HBD ≤ 5
- HBA ≤ 10

### Veber Rules
For good oral bioavailability:
- Rotatable bonds ≤ 10
- TPSA ≤ 140 Å²

### QED — Quantitative Estimate of Drug-likeness
A single composite desirability score in **[0, 1]** (Bickerton et al. 2012) integrating MW, ALOGP, HBA, HBD, PSA, rotatable bonds, aromatic rings, and structural-alert count. Higher = more drug-like. Useful as a one-number developability ranking.

| QED | Interpretation |
|-----|----------------|
| > 0.67 | Attractive — strongly drug-like |
| 0.5 – 0.67 | Acceptable — typical of marketed oral drugs |
| < 0.5 | Less drug-like — scrutinize physicochemical liabilities |

*Note:* QED is a relative ranking aid, not a hard cutoff. Many approved drugs (e.g. large kinase inhibitors) score below 0.5; weight it alongside the specific physicochemical and ADMET liabilities.

## Structural Alerts

Substructure pattern matches from the RDKit `FilterCatalog`. **Two tiers with different decision weight:**

| Catalog | Column | Meaning | How to act |
|---------|--------|---------|------------|
| **PAINS** (Baell & Holloway 2010) | `PAINS_Count`, `PAINS_Alerts` | Pan-assay interference substructures — frequent false positives in bioassays (e.g. catechols, rhodanines, quinones) | **Hard triage flag.** A PAINS hit warrants orthogonal-assay confirmation before believing the activity; default to de-prioritize. |
| **Brenk** (2008) + **NIH** | `Toxicophore_Count`, `Toxicophore_Alerts` | Unwanted/reactive functional groups — potential toxicity or unfavourable PK (e.g. Michael acceptors, nitro groups, thioesters) | **Advisory.** Many approved drugs carry at least one (aspirin → phenol ester). Use as a review prompt, not automatic exclusion. |

- `PAINS_Pass` = True when `PAINS_Count == 0` (invalid SMILES → False, so they cannot pass silently).
- A compound can carry toxicophore alerts but **zero** PAINS alerts — that is common and not disqualifying.
- Structural alerts are pattern-based heuristics: confirm against assay readouts and context before acting.

## ADMET Endpoints

### Absorption (A)

| Endpoint | Type | Threshold | Interpretation |
|----------|------|-----------|----------------|
| **Caco-2** | Regression | >−5.15 (high perm) | Cell line model of intestinal permeability. Higher = better absorption. |
| **HIA** | Classification | >0.5 = HIA+ | Human intestinal absorption. >90% absorbed orally = positive. |
| **Pgp Substrate** | Classification | >0.5 = substrate | P-glycoprotein efflux substrate. Positive = may have reduced oral absorption. |
| **Pgp Inhibitor** | Classification | >0.5 = inhibitor | P-glycoprotein inhibitor. May cause drug-drug interactions. |
| **Bioavailability** | Classification | >0.5 = F>20% | Oral bioavailability >20%. |

### Distribution (D)

| Endpoint | Type | Threshold | Interpretation |
|----------|------|-----------|----------------|
| **BBB** | Classification | >0.5 = BBB+ | Blood-brain barrier permeability. Important for CNS drugs (want high) or non-CNS drugs (want low). |
| **PPB** | Regression | <90% (moderate) | Plasma protein binding. High binding reduces free drug concentration. |
| **VDss** | Regression | 0.04–20 L/kg | Volume of distribution at steady state. Indicates tissue distribution extent. |

### Metabolism (M)

| Endpoint | Type | Threshold | Interpretation |
|----------|------|-----------|----------------|
| **CYP1A2 Inhibitor** | Classification | >0.5 = inhibitor | Inhibition = drug-drug interaction risk |
| **CYP2C9 Inhibitor** | Classification | >0.5 = inhibitor | Warfarin metabolism affected |
| **CYP2C19 Inhibitor** | Classification | >0.5 = inhibitor | Clopidogrel activation affected |
| **CYP2D6 Inhibitor** | Classification | >0.5 = inhibitor | Codeine/tamoxifen metabolism; pharmacogenomics-relevant |
| **CYP3A4 Inhibitor** | Classification | >0.5 = inhibitor | Most common DDI enzyme; statins, immunosuppressants |
| **CYP Substrates** | Classification | >0.5 = substrate | Metabolized by specific CYP enzyme |

### Excretion (E)

| Endpoint | Type | Threshold | Interpretation |
|----------|------|-----------|----------------|
| **Half-life** | Classification | >0.5 = long t½ | Long half-life (>3h) vs short. Determines dosing frequency. |
| **Clearance** | Regression | Various | Hepatic/renal clearance rate. Higher = faster elimination. |

### Toxicity (T)

| Endpoint | Type | Threshold | Clinical Concern |
|----------|------|-----------|------------------|
| **hERG** | Classification | >0.5 = blocker | **CRITICAL.** hERG K+ channel blockade → QT prolongation → fatal arrhythmia. Drugs withdrawn (cisapride, terfenadine). |
| **AMES** | Classification | >0.5 = mutagen | Ames test mutagenicity. Positive = potential carcinogen. |
| **DILI** | Classification | >0.5 = hepatotoxic | Drug-induced liver injury. Leading cause of drug withdrawal. |
| **Skin Sensitization** | Classification | >0.5 = sensitizer | Allergic contact dermatitis risk. |
| **LD50** | Regression | Lower = more toxic | Acute oral toxicity (rat). Lower LD50 = more dangerous. |
| **Clinical Toxicity** | Classification | >0.5 = toxic | Failed clinical trials due to toxicity. |

## Interpreting Results

### Traffic-Light System
- **Green (p < 0.3):** Low risk — favorable ADMET property
- **Yellow (0.3 ≤ p ≤ 0.7):** Moderate risk — needs further investigation
- **Red (p > 0.7):** High risk — safety concern, prioritize for experimental testing

### Key Decision Points
1. **hERG positive (>0.5):** Flag for patch-clamp assay before advancing
2. **AMES positive (>0.5):** Consider structural modification or mini-Ames confirmation
3. **DILI positive (>0.5):** Evaluate hepatocyte toxicity in vitro
4. **Multiple CYP inhibition:** High DDI risk — check clinical co-medications
5. **BBB+ for non-CNS drug:** Potential CNS side effects — flag for review

### Model Limitations
- Predictions are based on training data from TDC benchmarks
- Novel scaffolds far from training domain may have lower accuracy
- Binary thresholds (>0.5) are defaults — adjust based on project risk tolerance
- Always validate computationally flagged compounds experimentally
