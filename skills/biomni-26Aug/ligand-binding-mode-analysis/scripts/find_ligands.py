"""
Enumerate non-polymer ligands in a structure and select the target ligand.

Filters out crystallographic additives (ions, buffers, cryoprotectants,
detergents, common covalent modifications) using a curated ignore-list so the
"ligand" the skill analyses is the biologically relevant small molecule rather
than a sulfate ion or a glycerol.

See references/pdb_ligand_handling.md for the rationale and the full ignore-list.
"""

import os

from Bio.PDB import MMCIFParser, PDBParser
from Bio.PDB.Polypeptide import is_aa

# Common non-ligand HETATM groups: ions, buffers, cryoprotectants, sugars used
# as additives, detergents, and ubiquitous solvents. These are excluded from
# ligand selection but can be reported separately.
IGNORE_HET = {
    # water
    "HOH", "DOD", "WAT",
    # monatomic ions / small ions
    "NA", "K", "MG", "CA", "ZN", "MN", "FE", "FE2", "CO", "NI", "CU", "CU1",
    "CD", "HG", "CS", "RB", "SR", "BA", "LI", "AL", "CL", "BR", "IOD", "F",
    "SO4", "PO4", "NO3", "CO3", "ACT", "FMT", "OXL", "OH",
    # buffers / cryoprotectants / precipitants
    "GOL", "EDO", "PEG", "PG4", "PGE", "PE4", "1PE", "2PE", "P6G", "MPD",
    "DMS", "DMSO", "TRS", "EPE", "MES", "BME", "DTT", "TCE", "IPA", "MOH",
    "ACY", "ACN", "CCN", "BEN", "PHENOL", "GLC", "BOG", "LDA", "SUC",
    "FLC", "CIT", "TLA", "MLA", "MLI", "SIN", "AKG", "BCT",
    # frequent covalent / modification groups that are not the drug
    "NAG", "MAN", "BMA", "FUC", "GAL", "SIA", "XYS",  # glycans
    "PLP", "PMP",  # cofactor pyridoxal (report but not "the ligand" by default)
}

# Cofactors/nucleotides that ARE sometimes the ligand of interest. Not ignored;
# flagged so the user/agent can decide.
NOTABLE_COFACTORS = {"ATP", "ADP", "AMP", "GTP", "GDP", "GNP", "ANP", "NAD",
                     "NAP", "NDP", "FAD", "FMN", "SAM", "SAH", "COA", "HEM"}


def _parser_for(path):
    return MMCIFParser(QUIET=True) if path.lower().endswith((".cif", ".mmcif")) else PDBParser(QUIET=True)


def load_structure(path, structure_id=None):
    """Parse a structure file into a Biopython Structure object."""
    structure_id = structure_id or os.path.splitext(os.path.basename(path))[0]
    parser = _parser_for(path)
    return parser.get_structure(structure_id, path)


def enumerate_ligands(structure, model_index=0):
    """
    List all non-water HETATM groups in the structure.

    Returns a list of dicts:
      {resname, chain, resseq, icode, n_atoms, n_heavy, is_ignored, is_cofactor}
    grouped by unique (resname) with per-copy details.
    """
    model = list(structure)[model_index]
    groups = {}
    for chain in model:
        for res in chain:
            hetflag, resseq, icode = res.id
            if hetflag == " ":
                continue  # polymer residue
            resname = res.resname.strip()
            if resname in ("HOH", "DOD", "WAT"):
                continue
            atoms = list(res.get_atoms())
            n_heavy = sum(1 for a in atoms if a.element != "H")
            entry = groups.setdefault(
                resname,
                {
                    "resname": resname,
                    "copies": [],
                    "n_atoms": len(atoms),
                    "n_heavy": n_heavy,
                    "is_ignored": resname in IGNORE_HET,
                    "is_cofactor": resname in NOTABLE_COFACTORS,
                },
            )
            entry["copies"].append(
                {"chain": chain.id, "resseq": resseq, "icode": icode.strip(), "n_atoms": len(atoms)}
            )
    return list(groups.values())


def select_target_ligand(structure, prefer=None, model_index=0, min_heavy=6):
    """
    Pick the most likely drug-like ligand.

    Strategy:
      - If `prefer` (a chem-comp code) is given and present, use it.
      - Otherwise drop ignored additives, then choose the ligand with the most
        heavy atoms (drug-like molecules are larger than ions/buffers), requiring
        at least `min_heavy` heavy atoms.

    Returns
    -------
    dict: {resname, copies:[...], n_heavy, ...}  or raises if nothing suitable.
    """
    ligs = enumerate_ligands(structure, model_index=model_index)
    if prefer:
        prefer = prefer.strip().upper()
        for lg in ligs:
            if lg["resname"] == prefer:
                return lg
        raise ValueError(f"Requested ligand {prefer} not found. Present: {[l['resname'] for l in ligs]}")

    candidates = [l for l in ligs if not l["is_ignored"] and l["n_heavy"] >= min_heavy]
    if not candidates:
        # fall back to notable cofactors if that is all there is
        candidates = [l for l in ligs if l["is_cofactor"]]
    if not candidates:
        raise ValueError(
            "No drug-like ligand found (only ions/buffers/solvent present). "
            f"Groups seen: {[(l['resname'], l['n_heavy']) for l in ligs]}. "
            "This structure may be apo — see references/troubleshooting.md."
        )
    candidates.sort(key=lambda d: d["n_heavy"], reverse=True)
    best = candidates[0]
    if len(candidates) > 1:
        print(
            f"[info] multiple ligands present; picked {best['resname']} "
            f"({best['n_heavy']} heavy atoms). Others: "
            + ", ".join(f"{c['resname']}({c['n_heavy']})" for c in candidates[1:])
        )
    return best


def get_ligand_residue(structure, resname, chain_id=None, model_index=0):
    """Return the Biopython Residue object for a given ligand copy."""
    model = list(structure)[model_index]
    for chain in model:
        if chain_id and chain.id != chain_id:
            continue
        for res in chain:
            if res.id[0] != " " and res.resname.strip() == resname.strip().upper():
                return res, chain.id
    raise ValueError(f"Ligand {resname} (chain {chain_id}) not found")


def count_polymer_chains(structure, model_index=0):
    """Return dict {chain_id: n_amino_acid_residues} for protein chains."""
    model = list(structure)[model_index]
    out = {}
    for chain in model:
        n = sum(1 for r in chain if r.id[0] == " " and is_aa(r, standard=False))
        if n > 0:
            out[chain.id] = n
    return out


if __name__ == "__main__":
    import sys

    s = load_structure(sys.argv[1])
    for lg in enumerate_ligands(s):
        tag = "IGNORE" if lg["is_ignored"] else ("cofactor" if lg["is_cofactor"] else "LIGAND")
        print(f"{lg['resname']:>4}  heavy={lg['n_heavy']:>3}  copies={len(lg['copies'])}  [{tag}]")
