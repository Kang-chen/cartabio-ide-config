# Interaction-Type Classification

Each pocket residue contact is annotated with one or more interaction types, **plus
a confidence tier and the engine that made the call**.

## Engines: PLIP (primary) + hardened geometry (fallback)

Interaction typing is performed first with the **Protein-Ligand Interaction Profiler
(PLIP)** (`scripts/plip_backend.py`). PLIP protonates the complex with OpenBabel and
applies published geometric criteria that **include donor/acceptor angles** — the
directional information the skill's original distance-only rules lacked. When PLIP is
unavailable, the skill falls back to a **hardened in-house geometry engine**
(`scripts/classify_interactions.py`) that reproduces the same guarantees:

- ligand ionic centres come from **RDKit formal charges** (transferred from the
  ligand SMILES template), not the old "every N is +, every O is −" proxy;
- **halogen bonds require σ-hole directionality** (C–X···acceptor angle check);
- **π-cation requires the cation to sit over the ring face** (offset check).

PLIP calls REPLACE geometry calls for any residue PLIP typed; residues PLIP did not
type keep their geometry calls. Water bridges PLIP finds are reported separately and
are **not** counted as residue contact types.

## Confidence tiers (honesty layer)

Every interaction call carries `interaction_confidence`:

| Tier | Meaning |
|------|---------|
| **high** | Confirmed by PLIP, or meets the strict geometry (correct angles for H/halogen bonds; a genuine formal charge for salt bridge / π-cation). |
| **tentative** | Distance-only, near a geometric threshold, or dependent on an assumed ligand protonation/charge state (e.g. a morpholine N, pKa ~5–6, that may be neutral at physiological pH). |

Tentative calls are italicised and daggered (†) in the report table, drawn as hollow/
hatched marks in the figures, and carried in the `interaction_confidence` CSV column.
The `interaction_source` column records `PLIP` or `geometry` per contact.

The skill has two tiers of interaction TYPES, controlled by `extended_interactions`:

## Core tier (default — well validated)

| Type | Geometric rule | Notes |
|------|----------------|-------|
| **Hydrogen bond** | protein N/O ··· ligand N/O ≤ 3.5 Å | Distance-only "candidate" (see contact_definitions.md). |
| **Salt bridge** | charged protein group within 4.0 Å of an opposite-charge ligand atom | Protein: Asp/Glu carboxylate O (−); Lys NZ, Arg NH/NE, His ND1/NE2 (+). Ligand charge is approximated from N (potential +) and O (potential −) atoms — coarse without formal charges. |
| **Hydrophobic / vdW** | apolar (C/S) protein atom within 4.0 Å of an apolar ligand atom, with no polar/ionic assignment | The default "shape-complementarity" contact when nothing more specific applies. |

These three types are what the imatinib-ABL1 benchmark validated and are the
safest to report without caveats.

## Extended tier (opt-in — heuristic)

Enable only when the user asks. These add geometry code and require more caution.

| Type | Geometric rule | Confidence logic |
|------|----------------|------------------|
| **π-stacking** | aromatic ring centroid (protein Phe/Tyr/Trp/His) ··· ligand aromatic ring centroid ≤ 5.5 Å | Plane angle classifies parallel (<30°) vs T-shaped (60–120°) vs tilted. `high` when centroid ≤ 5.0 Å and the angle is parallel/T-shaped; else `tentative`. |
| **π-cation** | a **formally charged** cation within 6.0 Å of an aromatic ring centroid | Both directions (protein cation–ligand ring, ligand cation–protein ring). `high` only when centroid distance ≤ 5.0 Å **and** the cation lies within ~2.0 Å of the ring-normal axis (over the face) **and** the ligand charge is a real formal charge; otherwise `tentative`. A neutral ligand yields no ligand-cation π-cation at all. |
| **Halogen bond** | ligand halogen (Cl/Br/I) ··· protein O/N ≤ 3.5 Å **with** the C–X···acceptor angle | F is excluded. `high` only when the C–X···A angle is ~140–180° (σ-hole directionality satisfied); a distance hit with a missing/off-axis angle is kept but `tentative`. |
| **Salt bridge** | protein Asp/Glu carboxylate or Lys/Arg/His cation within 4.0 Å of an **oppositely, formally charged** ligand atom | Ligand charge = RDKit formal charge (from the SMILES template). `high` when both sides carry a real formal charge; if only the coarse element proxy is available (RDKit failed) the call is forced `tentative`. A ligand with no formal charge yields no salt bridge. |

## How a residue gets multiple tags

A residue can satisfy several rules (e.g. a Thr that H-bonds *and* packs against
the ligand → "H-bond + hydrophobic"). The report shows all tags joined by "+".
For coloring in figures, a single representative type is chosen by priority:
salt bridge > H-bond > π-cation > π-stacking > halogen > hydrophobic > vdW.

## Reading the results honestly

- Prefer **PLIP** calls (source = `PLIP`); they enforce angles and protonation.
- Trust **high** over **tentative**. A tentative call is not wrong, but it is
  unconfirmed — open the 3D structure before it supports a conclusion.
- **Salt bridge / π-cation on a neutral ligand** will not appear: the engines
  require a genuine formal charge. This is deliberate (it prevents the classic
  false positive of calling every amine/carboxylate contact ionic).
- Absence of an interaction type means *the rule (incl. angle/charge) was not met*,
  not that the interaction is chemically impossible.

## Implementation

Primary engine: `scripts/plip_backend.py` (PLIP). Fallback + confidence logic:
`scripts/classify_interactions.py`. Geometry cutoffs are module constants
(`CUT_HB`, `CUT_SALT`, `CUT_HYDRO`, `CUT_PISTACK`, `CUT_PICATION`, `CUT_HALOGEN`) and
the confidence-tier thresholds are `HALOGEN_HIGH_MIN_ANGLE`, `PICATION_HIGH_MAX_A`,
`PICATION_HIGH_MAX_OFFSET`, `PISTACK_HIGH_MAX_A` (geometry) and
`SALT_BRIDGE_HIGH_MAX_A`, `HALOGEN_HIGH_MIN_DON_ANGLE`, `HALOGEN_HIGH_MIN_ACC_ANGLE`
(PLIP backend). Document any change in the report Methods.
