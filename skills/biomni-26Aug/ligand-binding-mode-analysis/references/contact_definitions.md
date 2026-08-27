# Contact and Hydrogen-Bond Definitions

The skill uses simple, transparent, distance-based geometry. This document
records the cutoffs and *why* they were chosen. They match the validated
imatinib-ABL1 (PDB 1IEP) benchmark.

## Distance shells

| Shell | Cutoff | Meaning |
|-------|--------|---------|
| **Core contact** | heavy-atom pair ≤ **4.0 Å** | The packing shell that dominates shape complementarity / van der Waals interactions. Two non-hydrogen atoms this close are in direct contact. |
| **Wide contact** | heavy-atom pair ≤ **4.5 Å** | A permissive shell that also captures slightly longer polar/electrostatic contacts and marginal van der Waals interactions. Used to define the full pocket-residue set. |
| **Candidate H-bond** | polar(N/O)···polar(N/O) ≤ **3.5 Å** | Distance-only hydrogen-bond criterion. |

Only **heavy atoms** (non-hydrogen) are used, because:
1. X-ray structures at typical resolution do not resolve hydrogen positions.
2. Heavy-atom distances are the standard, reproducible way to define contacts.

A residue is included in the pocket if **any** of its heavy atoms lies within the
wide (4.5 Å) shell of **any** ligand heavy atom. The reported `min_dist` is the
minimum over all heavy-atom pairs.

## Why 4.0 / 4.5 Å?

- Van der Waals contact distances between heavy atoms are ~3.4–4.0 Å (sum of vdW
  radii for C/N/O pairs). 4.0 Å therefore captures genuine packing contacts.
- 4.5 Å adds a small margin so that borderline polar contacts and water-mediated
  neighbours are not missed when defining the residue set. Contacts between 4.0 and
  4.5 Å are reported but flagged as non-core.
- These values are widely used in protein-ligand contact analyses and reproduced
  the expected imatinib-ABL1 pocket (~21 core / ~25 wide residues).

## Hydrogen bonds (candidates)

A candidate hydrogen bond is any pair of a protein N/O atom and a ligand N/O atom
within 3.5 Å. This is a **geometric, distance-only** definition:

- No donor–hydrogen–acceptor **angle** is enforced, because hydrogens are usually
  absent from the coordinates.
- Both "protein donor / ligand acceptor" and "protein acceptor / ligand donor"
  cases are captured, since donor/acceptor identity cannot be assigned without
  hydrogens.
- Borderline distances (3.3–3.5 Å) may be weak or geometrically unfavourable —
  confirm in the 3D structure before asserting a specific H-bond.

Because of these limitations we always call them **candidate** hydrogen bonds.

## What is excluded

- **Waters, ions, buffers, cryoprotectants** are excluded from the quantitative
  contact set (they are filtered during ligand selection). Bridging-water contacts
  can be biologically important but are not counted here.
- **Hydrogen atoms** (if present in a model) are ignored for distance computation.
- **Alternate conformations (altlocs):** Biopython returns the first/occupancy-
  ordered atom; if a pocket residue has alternate conformers, inspect manually.

## Tuning

`compute_contacts.compute_contacts(..., cut_core=, cut_wide=, cut_hb=)` lets you
change the cutoffs. If you loosen them, state the new values in the report Methods.
