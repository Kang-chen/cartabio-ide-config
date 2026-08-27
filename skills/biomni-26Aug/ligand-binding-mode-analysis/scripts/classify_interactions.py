"""
Classify the physicochemical nature of each protein-ligand residue contact.

This module is the HARDENED, self-contained GEOMETRY engine. It is used (a) as the
automatic fallback when PLIP is unavailable, and (b) to fill in any contact that
PLIP did not type. Detection is geometric (distance + angle from crystal
coordinates); X-ray models usually lack hydrogens, so H-bonds remain distance-based
"candidates" and every call carries an explicit CONFIDENCE tier.

Improvements over the original distance-only heuristic (all motivated by the
gefitinib-EGFR review):
  * Ligand ionic centers are taken from RDKit FORMAL CHARGES (transferred from the
    ligand SMILES template) instead of the naive "every N is +, every O is -"
    proxy. A salt bridge/pi-cation is only asserted against a genuinely charged
    ligand atom. When formal charges are unavailable we fall back to the element
    proxy but force confidence="tentative".
  * Halogen bonds now require sigma-hole directionality: the C-X...acceptor angle
    must be ~[140,180] deg for "high"; a distance-only hit (angle unknown/outside)
    is kept but downgraded to "tentative".
  * pi-cation requires the cation to sit reasonably over the ring face (offset from
    the ring-normal projection) for "high"; otherwise "tentative".
  * Every contact gets `confidence` ("high" | "tentative") and `source`
    ("geometry"). See references/interaction_types.md for the exact criteria.

Two tiers, controlled by `extended`:
  CORE (default): hydrogen bond, salt bridge, hydrophobic/vdW.
  EXTENDED (opt-in): also pi-stacking, pi-cation, halogen bond.
"""

import numpy as np
from Bio.PDB.Polypeptide import is_aa

CUT_HB = 3.5
CUT_SALT = 4.0
CUT_HYDRO = 4.0
CUT_PISTACK = 5.5
CUT_PICATION = 6.0
CUT_HALOGEN = 3.5

# confidence-tier thresholds
HALOGEN_HIGH_MIN_ANGLE = 140.0   # C-X...A angle for a "high" halogen bond
HALOGEN_HIGH_MAX_ANGLE = 180.0
PICATION_HIGH_MAX_OFFSET = 2.0   # A; lateral offset of cation from ring-normal axis
PICATION_HIGH_MAX_A = 5.0        # A; centroid-cation distance for "high" pi-cation
PISTACK_HIGH_MAX_A = 5.0         # centroid distance for "high" pi-stacking

# protein charged-group atoms
ACIDIC = {"ASP": ["OD1", "OD2"], "GLU": ["OE1", "OE2"]}
BASIC = {"LYS": ["NZ"], "ARG": ["NH1", "NH2", "NE"], "HIS": ["ND1", "NE2"]}
# aromatic residue ring atoms (for pi geometry)
AROMATIC_RES = {
    "PHE": ["CG", "CD1", "CD2", "CE1", "CE2", "CZ"],
    "TYR": ["CG", "CD1", "CD2", "CE1", "CE2", "CZ"],
    "TRP": ["CD2", "CE2", "CE3", "CZ2", "CZ3", "CH2"],
    "HIS": ["CG", "ND1", "CD2", "CE1", "NE2"],
}
HALOGENS = {"CL", "BR", "I"}  # F excluded: essentially never a halogen-bond donor


def _atoms(res):
    return [a for a in res.get_atoms() if a.element != "H"]


def _coord(res, names):
    out = []
    for a in res.get_atoms():
        if a.get_name() in names:
            out.append(a.coord)
    return np.array(out) if out else None


def _ring_centroid_normal(coords):
    c = coords.mean(axis=0)
    u, s, vh = np.linalg.svd(coords - c)
    normal = vh[2]
    return c, normal / np.linalg.norm(normal)


def _ligand_aromatic_rings(ligand_residue, fragment_map):
    """Return list of (centroid, normal, atom_names) for aromatic rings, using the
    fragment map to seed which atoms are aromatic, then clustering geometrically."""
    aromatic_labels = {"pyridine", "pyrimidine", "phenyl", "pyrrole", "furan",
                       "thiophene", "aromatic_ring"}
    arom_atoms = [a for a in ligand_residue.get_atoms()
                  if a.element != "H" and (fragment_map.get(a.get_name(), "").split("(")[0]
                                           in aromatic_labels)]
    rings = []
    if not arom_atoms:
        return rings
    coords = np.array([a.coord for a in arom_atoms])
    names = [a.get_name() for a in arom_atoms]
    n = len(arom_atoms)
    adj = [[] for _ in range(n)]
    d = np.linalg.norm(coords[:, None] - coords[None], axis=2)
    for i in range(n):
        for j in range(i + 1, n):
            if 0.4 < d[i, j] <= 1.8:
                adj[i].append(j)
                adj[j].append(i)
    seen = [False] * n
    for i in range(n):
        if seen[i]:
            continue
        comp = []
        stack = [i]
        while stack:
            x = stack.pop()
            if seen[x]:
                continue
            seen[x] = True
            comp.append(x)
            for nb in adj[x]:
                if not seen[nb]:
                    stack.append(nb)
        if len(comp) >= 5:
            cc = coords[comp]
            centroid, normal = _ring_centroid_normal(cc)
            rings.append((centroid, normal, [names[k] for k in comp]))
    return rings


# ---------------------------------------------------------------------------
# Formal-charge-aware ligand ionic centers (replaces the naive element proxy).
# ---------------------------------------------------------------------------
def ligand_charged_atoms(ligand_residue, smiles=None):
    """
    Return (pos_atoms, neg_atoms, method) where each list holds Biopython atoms that
    carry a genuine formal charge.

    method="rdkit_formal"  -> charges taken from an RDKit molecule whose bond orders
                              were transferred from the ligand SMILES template; only
                              atoms with nonzero GetFormalCharge() are ionic.
    method="element_proxy" -> RDKit unavailable/failed; coarse fallback where amine N
                              is treated as (+) and carboxylate-like O as (-). Callers
                              must mark such assignments confidence="tentative".
    """
    # --- preferred: RDKit formal charges via SMILES template ---
    try:
        from rdkit import Chem  # noqa: PLC0415
        from ligand_fragments import _to_pdb_block, _apply_smiles_template  # noqa: PLC0415

        atom_names = [a.get_name() for a in ligand_residue.get_atoms() if a.element != "H"]
        block = _to_pdb_block(ligand_residue)
        mol = Chem.MolFromPDBBlock(block, sanitize=False, removeHs=True, proximityBonding=True)
        if mol is not None:
            templated = _apply_smiles_template(mol, smiles)
            if templated is not None:
                mol = templated
                # map rdkit idx -> name (parser preserves order)
                name_by_idx = {}
                for i in range(mol.GetNumAtoms()):
                    info = mol.GetAtomWithIdx(i).GetPDBResidueInfo()
                    nm = info.GetName().strip() if info is not None else (
                        atom_names[i] if i < len(atom_names) else None)
                    name_by_idx[i] = nm
                by_name = {a.get_name(): a for a in ligand_residue.get_atoms()}
                pos, neg = [], []
                for i in range(mol.GetNumAtoms()):
                    fc = mol.GetAtomWithIdx(i).GetFormalCharge()
                    nm = name_by_idx.get(i)
                    at = by_name.get(nm)
                    if at is None:
                        continue
                    if fc > 0:
                        pos.append(at)
                    elif fc < 0:
                        neg.append(at)
                return pos, neg, "rdkit_formal"
    except Exception:  # noqa: BLE001
        pass

    # --- fallback: element proxy (coarse; caller downgrades confidence) ---
    pos, neg = [], []
    for a in ligand_residue.get_atoms():
        if a.element == "N":
            pos.append(a)
        elif a.element == "O":
            neg.append(a)
    return pos, neg, "element_proxy"


def _halogen_bond_angle(lig_residue, halogen_atom, acceptor_coord):
    """C-X...A angle (deg) for the given halogen. Returns None if no bonded C found."""
    hx = halogen_atom.coord
    # nearest heavy atom to the halogen within a covalent bond distance = its carbon
    best_c, best_d = None, 1e9
    for a in lig_residue.get_atoms():
        if a is halogen_atom or a.element == "H":
            continue
        d = float(np.linalg.norm(a.coord - hx))
        if d < best_d and 0.5 < d <= 2.1:
            best_d, best_c = d, a
    if best_c is None:
        return None
    v1 = best_c.coord - hx          # X->C
    v2 = acceptor_coord - hx        # X->A
    cosang = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))
    cosang = max(-1.0, min(1.0, cosang))
    return float(np.degrees(np.arccos(cosang)))


def classify(structure, ligand_residue, contacts, fragment_map, model_index=0,
             extended=False, smiles=None):
    """
    Annotate each contact with interaction typing from hardened geometry.

    Adds to each contact: interaction_type (str), interaction_tags (list of
    {type, confidence, source}), and convenience keys interaction_confidence
    (worst-case tier among tags) and interaction_source ("geometry").

    Returns (annotated_contacts, extra) where extra has pi_stacking / pi_cation /
    halogen_bonds lists and 'charge_method'.
    """
    model = list(structure)[model_index]
    lig_atoms = _atoms(ligand_residue)
    lig_xyz = np.array([a.coord for a in lig_atoms])

    res_lookup = {}
    for chain in model:
        for res in chain:
            if res.id[0] == " " and is_aa(res, standard=False):
                res_lookup[(chain.id, res.id[1], res.id[2].strip())] = res

    extra = {"pi_stacking": [], "pi_cation": [], "halogen_bonds": []}
    lig_rings = _ligand_aromatic_rings(ligand_residue, fragment_map) if extended else []
    lig_pos, lig_neg, charge_method = ligand_charged_atoms(ligand_residue, smiles=smiles)
    extra["charge_method"] = charge_method
    ionic_tentative = (charge_method != "rdkit_formal")  # coarse charges -> downgrade

    for c in contacts:
        res = res_lookup.get((c["chain"], c["resseq"], c["icode"]))
        tags = []  # list of dicts {type, confidence, source}
        if res is None:
            c["interaction_tags"] = [{"type": "vdW", "confidence": "high", "source": "geometry"}]
            c["interaction_type"] = "vdW"
            c["interaction_confidence"] = "high"
            c["interaction_source"] = "geometry"
            continue
        rn = c["resname"].upper()

        # hydrogen bonds already computed in compute_contacts (distance candidate)
        if c["hbonds"]:
            tags.append({"type": "H-bond", "confidence": "high", "source": "geometry"})

        # salt bridge: acidic/basic protein group close to OPPOSITE FORMAL charge
        if rn in ACIDIC and lig_pos:
            acc = _coord(res, ACIDIC[rn])
            if acc is not None:
                pos_xyz = np.array([a.coord for a in lig_pos])
                if np.linalg.norm(acc[:, None] - pos_xyz[None], axis=2).min() <= CUT_SALT:
                    tags.append({"type": "salt bridge",
                                 "confidence": "tentative" if ionic_tentative else "high",
                                 "source": "geometry"})
        if rn in BASIC and lig_neg:
            don = _coord(res, BASIC[rn])
            if don is not None:
                neg_xyz = np.array([a.coord for a in lig_neg])
                if np.linalg.norm(don[:, None] - neg_xyz[None], axis=2).min() <= CUT_SALT:
                    tags.append({"type": "salt bridge",
                                 "confidence": "tentative" if ionic_tentative else "high",
                                 "source": "geometry"})

        # hydrophobic/vdW: apolar close approach, only if no polar/ionic tag yet
        if not tags:
            p_apolar = [a for a in _atoms(res) if a.element in ("C", "S")]
            lig_apolar_idx = [i for i, a in enumerate(lig_atoms) if a.element in ("C", "S")]
            if p_apolar and lig_apolar_idx:
                pa = np.array([a.coord for a in p_apolar])
                la = lig_xyz[lig_apolar_idx]
                if np.linalg.norm(pa[:, None] - la[None], axis=2).min() <= CUT_HYDRO:
                    tags.append({"type": "hydrophobic", "confidence": "high", "source": "geometry"})

        # EXTENDED: pi-stacking & pi-cation (protein aromatic vs ligand ring/cation)
        if extended and rn in AROMATIC_RES:
            rc = _coord(res, AROMATIC_RES[rn])
            if rc is not None and len(rc) >= 5:
                res_centroid, res_normal = _ring_centroid_normal(rc)
                for (lc, ln, lnames) in lig_rings:
                    dist = float(np.linalg.norm(res_centroid - lc))
                    if dist <= CUT_PISTACK:
                        ang = float(np.degrees(np.arccos(
                            min(1.0, abs(float(np.dot(res_normal, ln)))))))
                        geom = "parallel" if ang < 30 else ("T-shaped" if 60 <= ang <= 120 else "tilted")
                        conf = "high" if (dist <= PISTACK_HIGH_MAX_A and (ang < 30 or 60 <= ang <= 120)) else "tentative"
                        tags.append({"type": "pi-stacking", "confidence": conf, "source": "geometry"})
                        extra["pi_stacking"].append(
                            {"residue": f"{rn}{c['resseq']}", "chain": c["chain"],
                             "centroid_dist": round(dist, 2), "plane_angle_deg": round(ang, 1),
                             "geometry": geom, "confidence": conf})
                        break
                # pi-cation: cationic LIGAND atom over the protein ring face
                if lig_pos:
                    pos_xyz = np.array([a.coord for a in lig_pos])
                    dcat = np.linalg.norm(res_centroid - pos_xyz, axis=1)
                    if dcat.min() <= CUT_PICATION:
                        j = int(np.argmin(dcat))
                        offset = _point_axis_offset(pos_xyz[j], res_centroid, res_normal)
                        conf = "high" if (dcat.min() <= PICATION_HIGH_MAX_A and offset <= PICATION_HIGH_MAX_OFFSET and not ionic_tentative) else "tentative"
                        tags.append({"type": "pi-cation", "confidence": conf, "source": "geometry"})
                        extra["pi_cation"].append(
                            {"residue": f"{rn}{c['resseq']}", "chain": c["chain"],
                             "type": "ligand_cation-protein_pi", "dist": round(float(dcat.min()), 2),
                             "offset": round(float(offset), 2), "confidence": conf})

        # EXTENDED: pi-cation where protein cation meets ligand aromatic ring
        if extended and rn in BASIC and lig_rings:
            cat = _coord(res, BASIC[rn])
            if cat is not None:
                for (lc, ln, lnames) in lig_rings:
                    dvec = np.linalg.norm(cat - lc, axis=1)
                    if dvec.min() <= CUT_PICATION:
                        k = int(np.argmin(dvec))
                        offset = _point_axis_offset(cat[k], lc, ln)
                        conf = "high" if (dvec.min() <= PICATION_HIGH_MAX_A and offset <= PICATION_HIGH_MAX_OFFSET) else "tentative"
                        if not any(t["type"] == "pi-cation" for t in tags):
                            tags.append({"type": "pi-cation", "confidence": conf, "source": "geometry"})
                            extra["pi_cation"].append(
                                {"residue": f"{rn}{c['resseq']}", "chain": c["chain"],
                                 "type": "protein_cation-ligand_pi", "dist": round(float(dvec.min()), 2),
                                 "offset": round(float(offset), 2), "confidence": conf})
                        break

        # EXTENDED: halogen bond -- ligand halogen ... protein O/N WITH angle check
        if extended:
            lig_hal = [a for a in lig_atoms if a.element in HALOGENS]
            p_acc = [a for a in _atoms(res) if a.element in ("O", "N")]
            if lig_hal and p_acc:
                hx = np.array([a.coord for a in lig_hal])
                px = np.array([a.coord for a in p_acc])
                dmat = np.linalg.norm(hx[:, None] - px[None], axis=2)
                if dmat.min() <= CUT_HALOGEN:
                    i, j = np.unravel_index(np.argmin(dmat), dmat.shape)
                    ang = _halogen_bond_angle(ligand_residue, lig_hal[i], p_acc[j].coord)
                    if ang is not None and HALOGEN_HIGH_MIN_ANGLE <= ang <= HALOGEN_HIGH_MAX_ANGLE:
                        conf = "high"
                    else:
                        conf = "tentative"
                    tags.append({"type": "halogen bond", "confidence": conf, "source": "geometry"})
                    extra["halogen_bonds"].append(
                        {"residue": f"{rn}{c['resseq']}", "chain": c["chain"],
                         "lig_halogen": lig_hal[i].get_name(), "prot_atom": p_acc[j].get_name(),
                         "dist": round(float(dmat.min()), 2),
                         "angle": None if ang is None else round(ang, 0), "confidence": conf})

        if not tags:
            tags = [{"type": "vdW", "confidence": "high", "source": "geometry"}]

        # de-duplicate by type, keep strongest confidence per type
        merged = _merge_tags(tags)
        c["interaction_tags"] = merged
        c["interaction_type"] = " + ".join(t["type"] for t in merged)
        c["interaction_confidence"] = _worst_confidence(merged)
        c["interaction_source"] = "geometry"

    return contacts, extra


def _point_axis_offset(point, axis_origin, axis_dir):
    """Perpendicular distance (A) from `point` to the line through axis_origin along
    axis_dir (i.e. how far the cation sits off the ring-normal axis)."""
    v = np.asarray(point) - np.asarray(axis_origin)
    n = np.asarray(axis_dir) / (np.linalg.norm(axis_dir) + 1e-9)
    proj = np.dot(v, n) * n
    perp = v - proj
    return float(np.linalg.norm(perp))


def _merge_tags(tags):
    order = {"high": 0, "tentative": 1}
    best = {}
    seq = []
    for t in tags:
        ty = t["type"]
        if ty not in best:
            best[ty] = dict(t)
            seq.append(ty)
        else:
            if order[t["confidence"]] < order[best[ty]["confidence"]]:
                best[ty]["confidence"] = t["confidence"]
    return [best[ty] for ty in seq]


def _worst_confidence(tags):
    return "tentative" if any(t["confidence"] == "tentative" for t in tags) else "high"


def merge_plip_tags(contacts, plip_by_residue):
    """
    Overlay PLIP-derived typing (primary) onto the contact list. For each contact
    that PLIP typed, PLIP's tags REPLACE the geometry tags (PLIP is the validated
    engine); contacts PLIP did not type keep their geometry tags. Water bridges are
    not represented as residue contact types.

    `plip_by_residue`: {(chain, resseq): [ {type, confidence, source, detail}, ... ]}
    """
    for c in contacts:
        key = (c["chain"], int(c["resseq"]))
        items = plip_by_residue.get(key)
        if not items:
            # keep geometry tags but mark source explicitly if missing
            c.setdefault("interaction_source", "geometry")
            c.setdefault("interaction_confidence",
                         _worst_confidence(c.get("interaction_tags",
                                                 [{"type": c.get("interaction_type", "vdW"),
                                                   "confidence": "high"}])))
            continue
        # H-bond may be present in compute_contacts geometry too; prefer PLIP's call.
        merged = _merge_tags([{"type": it["type"], "confidence": it["confidence"],
                               "source": "PLIP"} for it in items])
        # if geometry found a candidate H-bond that PLIP did not report, keep it as tentative
        if c["hbonds"] and not any(t["type"] == "H-bond" for t in merged):
            merged.append({"type": "H-bond", "confidence": "tentative", "source": "geometry"})
            merged = _merge_tags(merged)
        c["interaction_tags"] = merged
        c["interaction_type"] = " + ".join(t["type"] for t in merged)
        c["interaction_confidence"] = _worst_confidence(merged)
        c["interaction_source"] = "PLIP"
    return contacts


def interaction_type_counts(contacts):
    """Count residues by interaction tag (per-residue tallies)."""
    counts = {}
    for c in contacts:
        for t in c.get("interaction_tags", [{"type": "vdW"}]):
            ty = t["type"] if isinstance(t, dict) else t
            counts[ty] = counts.get(ty, 0) + 1
    return counts


def confidence_counts(contacts):
    """Count residues by worst-case confidence tier."""
    counts = {"high": 0, "tentative": 0}
    for c in contacts:
        counts[c.get("interaction_confidence", "high")] = counts.get(
            c.get("interaction_confidence", "high"), 0) + 1
    return counts
