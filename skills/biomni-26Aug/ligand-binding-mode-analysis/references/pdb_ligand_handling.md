# PDB & Ligand Handling

How the skill reads structures, chooses the ligand, and resolves chemistry.

## Structure input

`fetch_structure.py` supports three modes:
- **PDB ID** → `https://files.rcsb.org/download/{PDB}.pdb`; metadata from
  `https://data.rcsb.org/rest/v1/core/entry/{PDB}`.
- **Local file** → `.pdb`/`.cif`/`.ent` (+`.gz`), parsed with the matching
  Biopython parser.
- **Target + ligand** → RCSB search API (`https://search.rcsb.org/rcsbsearch/v2/query`)
  ranks co-crystals by resolution (ascending) and ligand presence.

Metadata endpoints are best-effort; if a metadata call fails, the pipeline still
runs and simply omits resolution/title in the report.

## Ligand selection (`find_ligands.py`)

The goal is to analyse the **biologically relevant small molecule**, not a
crystallization additive. Selection logic:

1. Enumerate all non-water HETATM groups.
2. Drop entries on the **ignore-list** (ions, buffers, cryoprotectants, common
   glycans/solvents — see below).
3. Among the rest, require ≥ `min_heavy` (default 6) heavy atoms and pick the one
   with the **most heavy atoms** (drug-like molecules are larger than ions).
4. If nothing qualifies, fall back to notable cofactors (ATP/ADP/NAD/FAD/HEM/…);
   if still nothing, raise a clear "apo / no ligand" error.

Pass `ligand_code="XXX"` to force a specific ligand (recommended when several
plausible ligands or a cofactor are present).

### Ignore-list (crystallographic additives)

Water: `HOH DOD WAT`
Ions: `NA K MG CA ZN MN FE FE2 CO NI CU CU1 CD HG CS RB SR BA LI AL CL BR IOD F
SO4 PO4 NO3 CO3 ACT FMT OXL OH`
Buffers / cryoprotectants / precipitants: `GOL EDO PEG PG4 PGE PE4 1PE 2PE P6G MPD
DMS DMSO TRS EPE MES BME DTT TCE IPA MOH ACY ACN CCN BEN PHENOL GLC BOG LDA SUC
FLC CIT TLA MLA MLI SIN AKG BCT`
Glycans (usually modifications, not the drug): `NAG MAN BMA FUC GAL SIA XYS`
Cofactor pyridoxal: `PLP PMP`

**Cofactors that MIGHT be the ligand of interest** are *not* ignored but flagged:
`ATP ADP AMP GTP GDP GNP ANP NAD NAP NDP FAD FMN SAM SAH COA HEM`. If the user is
studying one of these, pass it as `ligand_code`.

> The ignore-list is pragmatic, not exhaustive. If an odd additive is picked as
> the ligand, force the correct one with `ligand_code`, or add the code to
> `IGNORE_HET`.

## Multiple copies / chains

Crystals often contain several protein chains, each with its own ligand copy. The
pipeline computes contacts to the chosen ligand copy, then keeps the **dominant
contacting protomer** (the chain contributing the most close contacts) so contacts
from different protomers are not mixed. Additional protomers/structures are handled
via the cross-structure concordance path instead.

## Alternate conformations & occupancy

Biopython yields atoms in occupancy order; altloc handling is minimal. If a key
pocket residue has alternate conformers or partial occupancy, verify manually.

## Ligand fragment perception (`ligand_fragments.py`)

To summarize contacts by chemical piece, each ligand heavy atom is labelled with a
fragment:
- **RDKit path (preferred):** build a molecule from the ligand coordinates
  (`MolFromPDBBlock(..., proximityBonding=True)`), perceive rings, fuse shared-atom
  rings into ring systems, and name common heterocycles (pyridine, pyrimidine,
  phenyl, piperazine, piperidine, pyran, pyrrole, furan). Non-ring atoms are
  labelled by functional-group SMARTS (amide, carboxylate, sulfonamide, amine,
  hydroxyl, halogen, …).
- **Graph fallback:** if RDKit is unavailable or can't sanitize the molecule,
  connect heavy atoms within 1.75 Å, detect rings by cycle search, and label
  generically by ring size / heteroatom content and element.

The fragment map drives the F3 heatmap and the "nearest fragment" column, giving a
direct structure-activity handle.
