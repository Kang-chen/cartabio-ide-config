"""
Assign ligand heavy atoms to chemical fragments (rings + functional groups).

This generalizes the hand-built imatinib moiety map. Two strategies:

  1. RDKit (preferred): build an RDKit molecule from the ligand's 3D coordinates
     (via a PDB block), perceive bonds, then group atoms by ring systems and by a
     small set of functional-group SMARTS. Every atom gets a human-readable
     fragment label.

  2. Distance-graph fallback (no RDKit / perception fails): connect heavy atoms
     within a bonding distance (<=1.75 A), find connected ring systems by cycle
     detection, and label the rest by element/connectivity.

The point is a per-atom -> fragment mapping so the contact map can be summarized
by chemically meaningful pieces (e.g. "aminopyrimidine", "piperazine").
See references/pdb_ligand_handling.md.

IMPORTANT — aromaticity: a ligand parsed from PDB coordinates has NO bond-order
information, so `MolFromPDBBlock(..., proximityBonding=True)` assigns single bonds
only and RDKit perceives 0 aromatic atoms. Aromatic rings would then be mislabeled
"aliphatic". To fix this, pass the ligand's canonical `smiles` (e.g. from RCSB
chem-comp metadata, `fetch_structure.get_ligand_metadata`). We build a template
from the SMILES and transfer its bond orders onto the coordinate molecule with
`AllChem.AssignBondOrdersFromTemplate`, which correctly recovers aromatic rings,
double bonds, and functional groups. Without a template we fall back to the
geometry-only perception (rings still grouped, but aromatic/aliphatic naming may
be approximate).
"""

import numpy as np

BOND_CUTOFF = 1.75  # A; covalent heavy-atom bond upper bound

# Minimal functional-group SMARTS -> label. Order matters (first match wins per atom).
FG_SMARTS = [
    ("carboxylate", "[CX3](=O)[OX1-,OX2H0]"),
    ("carboxylic_acid", "[CX3](=O)[OX2H1]"),
    ("amide", "[CX3](=[OX1])[NX3]"),
    ("sulfonamide", "[SX4](=O)(=O)[NX3]"),
    ("sulfone", "[SX4](=O)(=O)"),
    ("ester", "[CX3](=O)[OX2][#6]"),
    ("nitro", "[NX3+](=O)[O-]"),
    ("nitrile", "[NX1]#[CX2]"),
    ("guanidinium", "[NX3][CX3](=[NX2,NX3+])[NX3]"),
    ("phosphate", "[PX4](=O)([O])([O])[O]"),
    ("halogen", "[F,Cl,Br,I]"),
    ("hydroxyl", "[OX2H]"),
    ("ether", "[OX2]([#6])[#6]"),
    ("primary_amine", "[NX3;H2][#6]"),
    ("secondary_amine", "[NX3;H1]([#6])[#6]"),
    ("tertiary_amine", "[NX3;H0]([#6])([#6])[#6]"),
]


def _to_pdb_block(ligand_residue):
    """Serialize a Biopython ligand residue to a minimal PDB HETATM block."""
    lines = []
    for i, atom in enumerate(ligand_residue.get_atoms(), start=1):
        name = atom.get_name()
        elem = atom.element
        x, y, z = atom.coord
        lines.append(
            f"HETATM{i:>5} {name:<4} LIG A   1    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {elem:>2}"
        )
    lines.append("END")
    return "\n".join(lines)


def _apply_smiles_template(mol, smiles):
    """
    Transfer bond orders from a SMILES template onto a coordinate-derived mol so
    that aromaticity/double bonds are perceived correctly. Returns a new mol on
    success, or None if the template does not match (atom-count mismatch,
    substructure failure, etc.). Never raises.
    """
    if not smiles:
        return None
    from rdkit import Chem  # noqa: PLC0415
    from rdkit.Chem import AllChem  # noqa: PLC0415

    try:
        # SMILES may contain multiple period-separated components (e.g. salts /
        # counter-ions). Keep the component whose heavy-atom count matches.
        cand = [s for s in smiles.split(".") if s]
        target_n = mol.GetNumAtoms()
        template = None
        for smi in sorted(cand, key=len, reverse=True):
            t = Chem.MolFromSmiles(smi)
            if t is None:
                continue
            if t.GetNumAtoms() == target_n:
                template = t
                break
            if template is None:
                template = t  # best-effort default (largest parseable)
        if template is None:
            return None
        fixed = AllChem.AssignBondOrdersFromTemplate(template, mol)
        Chem.SanitizeMol(fixed)
        return fixed
    except Exception:  # noqa: BLE001
        return None


def fragments_rdkit(ligand_residue, smiles=None):
    """
    Try to assign fragments using RDKit. Returns dict {atom_name: fragment_label}
    or raises if RDKit is unavailable / perception fails.

    If `smiles` (canonical ligand SMILES, e.g. from RCSB chem-comp metadata) is
    provided, bond orders from that template are transferred onto the coordinate
    molecule so aromatic rings and functional groups are perceived correctly.
    Falls back to geometry-only perception when no usable template is available.
    """
    from rdkit import Chem  # noqa: PLC0415

    atom_names = [a.get_name() for a in ligand_residue.get_atoms()]
    block = _to_pdb_block(ligand_residue)
    mol = Chem.MolFromPDBBlock(block, sanitize=False, removeHs=True, proximityBonding=True)
    if mol is None:
        raise RuntimeError("RDKit could not build molecule from ligand coordinates")

    # Preferred: transfer bond orders from the SMILES template (recovers aromaticity).
    templated = _apply_smiles_template(mol, smiles)
    if templated is not None:
        mol = templated
    else:
        if smiles:
            print("[info] SMILES template did not match ligand coordinates; "
                  "using geometry-only perception (aromatic naming approximate)")
        try:
            Chem.SanitizeMol(mol)
        except Exception:  # noqa: BLE001
            # partial sanitize: at least perceive rings
            mol.UpdatePropertyCache(strict=False)
            Chem.GetSymmSSSR(mol)

    n = mol.GetNumAtoms()
    labels = ["scaffold"] * n

    # 1) ring systems -> label aromatic vs aliphatic rings, grouped by fused system
    ri = mol.GetRingInfo()
    atom_rings = ri.AtomRings()
    # union-find to fuse rings sharing atoms
    parent = list(range(len(atom_rings)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for i in range(len(atom_rings)):
        for j in range(i + 1, len(atom_rings)):
            if set(atom_rings[i]) & set(atom_rings[j]):
                union(i, j)
    system_atoms = {}
    for idx, ring in enumerate(atom_rings):
        root = find(idx)
        system_atoms.setdefault(root, set()).update(ring)
    ring_system_id = {}
    for sysnum, (root, atoms) in enumerate(system_atoms.items(), start=1):
        for a in atoms:
            ring_system_id[a] = sysnum

    def ring_label(atom_idx, atoms):
        heteros = sorted({mol.GetAtomWithIdx(a).GetSymbol() for a in atoms
                          if mol.GetAtomWithIdx(a).GetSymbol() not in ("C", "H")})
        aromatic = any(mol.GetAtomWithIdx(a).GetIsAromatic() for a in atoms)
        size = len(atoms)
        base = "aromatic_ring" if aromatic else "aliphatic_ring"
        n_n = sum(1 for a in atoms if mol.GetAtomWithIdx(a).GetSymbol() == "N")
        n_o = sum(1 for a in atoms if mol.GetAtomWithIdx(a).GetSymbol() == "O")
        n_s = sum(1 for a in atoms if mol.GetAtomWithIdx(a).GetSymbol() == "S")
        # nice names for common heterocycles.
        # IMPORTANT: saturated mixed-heteroatom rings (morpholine 1N+1O,
        # thiomorpholine 1N+1S) MUST be tested before the single-N "piperidine"
        # and single-O "pyran" branches, otherwise a 1N+1O ring is mislabeled
        # "piperidine" (the n_n==1 branch fires first). The single-heteroatom
        # branches are therefore constrained to exactly one heteroatom.
        if aromatic and size == 6 and n_n == 1 and n_o == 0 and n_s == 0:
            return "pyridine"
        if aromatic and size == 6 and n_n == 2:
            return "pyrimidine"
        if aromatic and size == 6 and n_n == 0 and not heteros:
            return "phenyl"
        if aromatic and size == 5 and n_n == 1 and n_o == 0:
            return "pyrrole"
        if aromatic and size == 5 and n_o == 1 and n_n == 0:
            return "furan"
        if aromatic and size == 5 and n_s == 1 and n_n == 0:
            return "thiophene"
        if not aromatic and size == 6 and n_n == 2 and n_o == 0 and n_s == 0:
            return "piperazine"
        if not aromatic and size == 6 and n_n == 1 and n_o == 1 and n_s == 0:
            return "morpholine"
        if not aromatic and size == 6 and n_n == 1 and n_s == 1 and n_o == 0:
            return "thiomorpholine"
        if not aromatic and size == 6 and n_n == 1 and n_o == 0 and n_s == 0:
            return "piperidine"
        if not aromatic and size == 6 and n_o == 1 and n_n == 0 and n_s == 0:
            return "pyran"
        if not aromatic and size == 6 and n_o == 2 and n_n == 0:
            return "dioxane"
        if heteros:
            return f"{base}({''.join(heteros)})"
        return base

    for sysnum, (root, atoms) in enumerate(system_atoms.items(), start=1):
        lbl = ring_label(None, atoms)
        for a in atoms:
            labels[a] = lbl

    # 2) functional groups on non-ring atoms (do not overwrite ring labels)
    for name, sma in FG_SMARTS:
        patt = Chem.MolFromSmarts(sma)
        if patt is None:
            continue
        for match in mol.GetSubstructMatches(patt):
            for a in match:
                if a < n and not mol.GetAtomWithIdx(a).IsInRing():
                    if labels[a] == "scaffold":
                        labels[a] = name

    # 3) map RDKit atom index -> original atom name (order is preserved by parser)
    mapping = {}
    for i in range(n):
        pdb_name = None
        info = mol.GetAtomWithIdx(i).GetPDBResidueInfo()
        if info is not None:
            pdb_name = info.GetName().strip()
        if not pdb_name and i < len(atom_names):
            pdb_name = atom_names[i]
        mapping[pdb_name] = labels[i]
    # any atoms RDKit dropped -> scaffold
    for nm in atom_names:
        mapping.setdefault(nm, "scaffold")
    return mapping


def fragments_graph_fallback(ligand_residue):
    """
    Coordinate-graph fallback. Detect rings via cycle basis and label the rest by
    element. Returns {atom_name: fragment_label}.
    """
    atoms = [a for a in ligand_residue.get_atoms() if a.element != "H"]
    names = [a.get_name() for a in atoms]
    xyz = np.array([a.coord for a in atoms])
    elems = [a.element for a in atoms]
    n = len(atoms)
    # adjacency
    d = np.linalg.norm(xyz[:, None, :] - xyz[None, :, :], axis=2)
    adj = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if 0.4 < d[i, j] <= BOND_CUTOFF:
                adj[i].append(j)
                adj[j].append(i)

    # find rings: simple approach — atoms in any cycle up to size 7
    in_ring = [False] * n

    def dfs(start):
        stack = [(start, -1, [start])]
        while stack:
            node, parent_node, path = stack.pop()
            for nb in adj[node]:
                if nb == parent_node:
                    continue
                if nb in path:
                    cyc = path[path.index(nb):]
                    if 3 <= len(cyc) <= 7:
                        for a in cyc:
                            in_ring[a] = True
                    continue
                if len(path) < 7:
                    stack.append((nb, node, path + [nb]))

    for i in range(n):
        if not in_ring[i]:
            dfs(i)

    # group ring atoms into connected ring systems
    labels = {}
    visited = [False] * n
    ring_sys = 0
    for i in range(n):
        if in_ring[i] and not visited[i]:
            ring_sys += 1
            comp = []
            stack = [i]
            while stack:
                x = stack.pop()
                if visited[x] or not in_ring[x]:
                    continue
                visited[x] = True
                comp.append(x)
                for nb in adj[x]:
                    if in_ring[nb] and not visited[nb]:
                        stack.append(nb)
            n_n = sum(1 for a in comp if elems[a] == "N")
            n_o = sum(1 for a in comp if elems[a] == "O")
            size = len(comp)
            if size == 6 and n_n == 2:
                lbl = "6-ring(2N)"
            elif size == 6 and n_n == 1:
                lbl = "6-ring(1N)"
            elif size == 6 and n_n == 0 and n_o == 0:
                lbl = "6-ring(C)"
            elif n_n or n_o:
                lbl = f"ring({'N' * n_n}{'O' * n_o})"
            else:
                lbl = f"ring{size}"
            for a in comp:
                labels[names[a]] = lbl

    # non-ring atoms: label by element + heteroatom flag
    for i in range(n):
        if names[i] in labels:
            continue
        if elems[i] == "O":
            labels[names[i]] = "O-group"
        elif elems[i] == "N":
            labels[names[i]] = "N-group"
        elif elems[i] in ("F", "CL", "BR", "I"):
            labels[names[i]] = "halogen"
        elif elems[i] == "S":
            labels[names[i]] = "S-group"
        else:
            labels[names[i]] = "linker/C"
    return labels


def assign_fragments(ligand_residue, smiles=None):
    """
    Best-effort fragment assignment. Returns
      (mapping {atom_name: fragment_label}, method_str)

    Pass `smiles` (canonical ligand SMILES from RCSB chem-comp metadata) so RDKit
    can recover aromatic ring naming; without it the RDKit path still runs but
    aromatic/aliphatic labels may be approximate.
    """
    try:
        m = fragments_rdkit(ligand_residue, smiles=smiles)
        if m and any(v not in ("scaffold",) for v in m.values()):
            return m, "rdkit"
        # if everything is scaffold, fall through to graph
    except Exception as e:  # noqa: BLE001
        print(f"[info] RDKit fragmentation unavailable ({e}); using graph fallback")
    return fragments_graph_fallback(ligand_residue), "graph"


def fragment_order(mapping):
    """Return fragment labels in a stable, first-appearance order."""
    seen = []
    for v in mapping.values():
        if v not in seen:
            seen.append(v)
    return seen


if __name__ == "__main__":
    import sys

    from find_ligands import load_structure, select_target_ligand

    s = load_structure(sys.argv[1])
    lg = select_target_ligand(s, prefer=sys.argv[2] if len(sys.argv) > 2 else None)
    res, ch = None, None
    for chain in list(s)[0]:
        for r in chain:
            if r.id[0] != " " and r.resname.strip() == lg["resname"]:
                res = r
                break
        if res:
            break
    smi = None
    try:
        from fetch_structure import get_ligand_metadata
        smi = get_ligand_metadata(lg["resname"]).get("smiles")
    except Exception:  # noqa: BLE001
        pass
    mp, method = assign_fragments(res, smiles=smi)
    print(f"method={method} (smiles={'yes' if smi else 'no'})")
    for name, frag in mp.items():
        print(f"  {name:>4} -> {frag}")
