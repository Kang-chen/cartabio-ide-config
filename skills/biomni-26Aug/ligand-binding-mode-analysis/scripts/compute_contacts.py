"""
Compute protein-ligand heavy-atom contacts and candidate hydrogen bonds.

Method (see references/contact_definitions.md for rationale):
  - Heavy-atom distance geometry between every polymer residue and the ligand.
  - CORE contact  : any heavy-atom pair <= 4.0 A  (the packing shell that
                    dominates van der Waals / shape complementarity).
  - WIDE contact  : any heavy-atom pair <= 4.5 A  (a permissive shell that
                    captures longer polar/electrostatic contacts).
  - H-bond candidate: N/O(protein) ... N/O(ligand) <= 3.5 A. This is a
                    DISTANCE-ONLY (geometric) criterion. Crystallographic models
                    usually lack hydrogens, so donor-H...acceptor angles are not
                    enforced; call these "candidate" H-bonds.

Waters and ions are excluded from the quantitative contact set (they are handled
by find_ligands / and can be reported separately as bridging waters).

The core geometry here is deliberately dependency-light: Biopython + numpy.
"""

import numpy as np
from Bio.PDB.Polypeptide import is_aa

CUT_CORE = 4.0
CUT_WIDE = 4.5
CUT_HB = 3.5

POLAR_ELEMENTS = {"N", "O"}
# Elements treated as apolar carbon-like for hydrophobic contacts
APOLAR_ELEMENTS = {"C", "S"}


def is_polar(atom):
    """True if the atom is a potential H-bond donor/acceptor (N or O)."""
    return atom.element in POLAR_ELEMENTS


def _residue_heavy_atoms(res):
    return [a for a in res.get_atoms() if a.element != "H"]


def compute_contacts(
    structure,
    ligand_residue,
    model_index=0,
    cut_core=CUT_CORE,
    cut_wide=CUT_WIDE,
    cut_hb=CUT_HB,
    chains=None,
):
    """
    Compute per-residue contacts between all polymer residues and a ligand.

    Parameters
    ----------
    structure : Bio.PDB.Structure
    ligand_residue : Bio.PDB.Residue  (the target ligand copy)
    chains : list[str] or None   Restrict to these polymer chain ids (default all).

    Returns
    -------
    list of dicts, one per contacting residue (min heavy-atom distance <= cut_wide),
    each with:
      chain, resseq, icode, resname, min_dist, n_core, n_wide, core_contact,
      nearest_lig_atom, nearest_prot_atom, hbonds:[{prot_atom, lig_atom, dist}]
    Sorted by min_dist ascending.
    """
    model = list(structure)[model_index]
    lig_atoms = _residue_heavy_atoms(ligand_residue)
    if not lig_atoms:
        raise ValueError("Ligand has no heavy atoms")
    lig_xyz = np.array([a.coord for a in lig_atoms], dtype=float)
    lig_names = [a.get_name() for a in lig_atoms]
    lig_polar = np.array([is_polar(a) for a in lig_atoms])

    results = []
    for chain in model:
        if chains and chain.id not in chains:
            continue
        for res in chain:
            if res.id[0] != " ":
                continue  # skip HETATM / water
            if not is_aa(res, standard=False):
                continue
            p_atoms = _residue_heavy_atoms(res)
            if not p_atoms:
                continue
            p_xyz = np.array([a.coord for a in p_atoms], dtype=float)
            d = np.linalg.norm(p_xyz[:, None, :] - lig_xyz[None, :, :], axis=2)
            dmin = float(d.min())
            if dmin > cut_wide:
                continue
            n_core = int((d <= cut_core).sum())
            n_wide = int((d <= cut_wide).sum())
            imin = np.unravel_index(np.argmin(d), d.shape)
            nearest_prot = p_atoms[imin[0]].get_name()
            nearest_lig = lig_names[imin[1]]

            # candidate H-bonds: polar-polar pairs within cut_hb
            p_polar = np.array([is_polar(a) for a in p_atoms])
            hb = []
            polar_mask = p_polar[:, None] & lig_polar[None, :]
            close = (d <= cut_hb) & polar_mask
            for pi, li in zip(*np.where(close)):
                hb.append(
                    {
                        "prot_atom": p_atoms[pi].get_name(),
                        "lig_atom": lig_names[li],
                        "dist": round(float(d[pi, li]), 2),
                    }
                )
            hb.sort(key=lambda x: x["dist"])
            results.append(
                {
                    "chain": chain.id,
                    "resseq": res.id[1],
                    "icode": res.id[2].strip(),
                    "resname": res.resname.strip(),
                    "min_dist": round(dmin, 2),
                    "n_core": n_core,
                    "n_wide": n_wide,
                    "core_contact": dmin <= cut_core,
                    "nearest_lig_atom": nearest_lig,
                    "nearest_prot_atom": nearest_prot,
                    "hbonds": hb,
                }
            )
    results.sort(key=lambda r: r["min_dist"])
    return results


def summarize_contacts(contacts):
    """Return a compact dict summary of a contact list."""
    n_res = len(contacts)
    n_core = sum(1 for c in contacts if c["core_contact"])
    n_hb_res = sum(1 for c in contacts if c["hbonds"])
    n_hb = sum(len(c["hbonds"]) for c in contacts)
    return {
        "n_contact_residues": n_res,
        "n_core_residues": n_core,
        "n_hbond_residues": n_hb_res,
        "n_hbonds": n_hb,
    }


def contacts_by_ligand_atom(structure, ligand_residue, model_index=0, cut=CUT_WIDE, chains=None):
    """
    Per-ligand-atom contact counts (how many protein heavy atoms each ligand atom
    touches within `cut`). Useful for the fragment heatmap.

    Returns dict {lig_atom_name: {n_contacts, residues:set()}}
    """
    model = list(structure)[model_index]
    lig_atoms = _residue_heavy_atoms(ligand_residue)
    lig_xyz = np.array([a.coord for a in lig_atoms], dtype=float)
    lig_names = [a.get_name() for a in lig_atoms]
    out = {name: {"n_contacts": 0, "residues": set()} for name in lig_names}
    for chain in model:
        if chains and chain.id not in chains:
            continue
        for res in chain:
            if res.id[0] != " " or not is_aa(res, standard=False):
                continue
            p_atoms = _residue_heavy_atoms(res)
            if not p_atoms:
                continue
            p_xyz = np.array([a.coord for a in p_atoms], dtype=float)
            d = np.linalg.norm(lig_xyz[:, None, :] - p_xyz[None, :, :], axis=2)
            rlabel = f"{res.resname.strip()}{res.id[1]}"
            for li in range(len(lig_names)):
                hits = int((d[li] <= cut).sum())
                if hits:
                    out[lig_names[li]]["n_contacts"] += hits
                    out[lig_names[li]]["residues"].add(rlabel)
    return out
