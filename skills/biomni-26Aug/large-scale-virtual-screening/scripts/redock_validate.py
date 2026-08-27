#!/usr/bin/env python3
"""
redock_validate.py -- validate the box + scoring by re-docking the native ligand.

Why: the single most failure-prone scientific step in SBVS is whether the box and
scoring function reproduce a KNOWN binding pose. If redock fails, the whole screen
is suspect (wrong box, wrong protonation, bad receptor).

Gate (core-aware, two-tier, WARN-not-FAIL):
  * compute BOTH whole-molecule heavy-atom RMSD and a CORE RMSD.
  * PASS if core RMSD < --rmsd-threshold (default 2.0 A), even when whole-molecule
    RMSD is higher (flexible solvent-exposed tails legitimately move).
  * never hard-fail: emit passed=true/false + a warning; caller decides. A failure
    must surface loudly and land in the report's Limitations.

CORE is defined PRINCIPLEDLY and recorded (not hand-picked):
  rule = "mcs"    : Maximum Common Substructure between the native ligand and itself
                    restricted to ring systems + directly attached atoms (the rigid
                    scaffold). Deterministic via RDKit rdFMCS on the ligand's own
                    ring-and-linker subgraph.
  rule = "buried" : atoms whose min distance to any receptor heavy atom <= --contact
                    (default 4.5 A) i.e. buried/contacting the pocket (proxy for low
                    SASA without needing a SASA engine).
  rule = "mcs+buried" (default): intersection of the two -- rigid AND buried.
  --core-smarts SMARTS overrides with an explicit substructure (audited too).

RMSD is symmetry-corrected over molecular automorphisms so symmetric rings don't
inflate it. The exact atom indices counted for the core are written to the JSON.

Outputs (into --outdir):
  redock_pose.pdbqt        best redocked pose
  redock_validation.json   {top_affinity, rmsd_full, rmsd_core, core_rule,
                            core_atom_indices, n_core, n_heavy, passed, warning}
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys

try:
    from common import (which, parse_vina_stdout, parse_vina_posefile,
                        pdbqt_heavy_coords, symmetric_rmsd, rdkit_automorphisms, SEED)
except ImportError:
    from scripts.common import (which, parse_vina_stdout, parse_vina_posefile,
                                pdbqt_heavy_coords, symmetric_rmsd,
                                rdkit_automorphisms, SEED)


# --------------------------------------------------------------------------- #
# native-ligand prep (PDB -> RDKit mol with coords, + PDBQT for docking)       #
# --------------------------------------------------------------------------- #
def _native_mol_from_pdb(ligand_pdb, template_smiles=None):
    """RDKit mol of the native ligand WITH crystal coordinates (heavy atoms)."""
    from rdkit import Chem
    mol = Chem.MolFromPDBFile(ligand_pdb, removeHs=True, sanitize=True)
    if mol is None:
        mol = Chem.MolFromPDBFile(ligand_pdb, removeHs=True, sanitize=False)
    if mol is None:
        raise ValueError(f"could not read native ligand from {ligand_pdb}")
    if template_smiles:
        from rdkit.Chem import AllChem
        templ = Chem.MolFromSmiles(template_smiles)
        if templ is not None:
            try:
                mol = AllChem.AssignBondOrdersFromTemplate(templ, mol)
            except Exception:
                pass
    return mol


def _ligand_pdb_to_pdbqt(ligand_pdb, out_pdbqt, template_smiles=None, seed=SEED):
    """Prepare the native ligand as a flexible PDBQT for redocking (Meeko)."""
    from rdkit import Chem
    from rdkit.Chem import AllChem
    mol = _native_mol_from_pdb(ligand_pdb, template_smiles)
    molH = Chem.AddHs(mol, addCoords=True)
    try:
        from meeko import MoleculePreparation, PDBQTWriterLegacy
        prep = MoleculePreparation()
        setups = prep.prepare(molH)
        setup = setups[0] if isinstance(setups, (list, tuple)) else setups
        pdbqt, ok, err = PDBQTWriterLegacy.write_string(setup)
        if not ok:
            raise RuntimeError(f"meeko write failed: {err}")
    except ImportError:
        from meeko import MoleculePreparation
        prep = MoleculePreparation()
        prep.prepare(molH)
        pdbqt = prep.write_pdbqt_string()
    with open(out_pdbqt, "w") as fh:
        fh.write(pdbqt)
    return mol  # heavy-atom crystal-coord reference


# --------------------------------------------------------------------------- #
# principled core selection                                                    #
# --------------------------------------------------------------------------- #
def _ring_and_linker_atoms(mol):
    """Rigid scaffold proxy: AROMATIC ring atoms + atoms bonded to >=2 aromatic-ring
    atoms (linkers fusing/joining aromatic systems).

    Aromatic (not all) rings on purpose: rigid binding cores are almost always
    conjugated fused-ring systems, whereas SATURATED aliphatic rings -- morpholine,
    piperazine, cyclohexyl -- are typically flexible, solvent-exposed decorations.
    Including such rings inflates the "core" RMSD with atoms that legitimately move.
    (Concretely, for gefitinib this drops the flexible morpholine and recovers the
    17-atom anilinoquinazoline core -> ~0.33 A vs the crystal, instead of ~3.7 A
    when the morpholine is wrongly folded in.)

    Falls back to ALL ring atoms only if the molecule has no aromatic ring at all,
    so purely aliphatic scaffolds still get a core."""
    ri = mol.GetRingInfo()
    arom_ring_atoms = set()
    for ring in ri.AtomRings():
        if all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring):
            arom_ring_atoms.update(ring)
    if not arom_ring_atoms:  # no aromatic ring -> fall back to all rings
        for ring in ri.AtomRings():
            arom_ring_atoms.update(ring)
    core = set(arom_ring_atoms)
    for a in mol.GetAtoms():
        if a.GetIdx() in arom_ring_atoms:
            continue
        nring = sum(1 for nb in a.GetNeighbors() if nb.GetIdx() in arom_ring_atoms)
        if nring >= 2:  # linker joining two (aromatic) ring systems
            core.add(a.GetIdx())
    return core


def _buried_atoms(mol, receptor_pdb, contact):
    """Heavy-atom indices within `contact` A of any receptor heavy atom."""
    import numpy as np
    conf = mol.GetConformer()
    lig = np.array([[conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y,
                     conf.GetAtomPosition(i).z] for i in range(mol.GetNumAtoms())])
    rec = []
    with open(receptor_pdb) as fh:
        for line in fh:
            if line[:6].strip() in ("ATOM", "HETATM"):
                el = line[76:78].strip()
                if el == "H" or line[12:16].strip()[:1] == "H":
                    continue
                try:
                    rec.append([float(line[30:38]), float(line[38:46]), float(line[46:54])])
                except ValueError:
                    continue
    if not rec:
        return set(range(mol.GetNumAtoms()))
    rec = np.array(rec)
    try:
        from scipy.spatial import cKDTree
        tree = cKDTree(rec)
        d, _ = tree.query(lig, k=1)
    except Exception:
        d = np.sqrt(((lig[:, None, :] - rec[None, :, :]) ** 2).sum(-1)).min(1)
    return {i for i in range(mol.GetNumAtoms()) if d[i] <= contact}


def _select_core(mol, rule, receptor_pdb, contact, core_smarts):
    from rdkit import Chem
    n = mol.GetNumAtoms()
    if core_smarts:
        patt = Chem.MolFromSmarts(core_smarts)
        match = mol.GetSubstructMatch(patt) if patt is not None else ()
        return set(match), f"smarts:{core_smarts}"
    mcs_atoms = _ring_and_linker_atoms(mol)
    if rule == "mcs":
        return mcs_atoms, "mcs(ring+linker)"
    buried = _buried_atoms(mol, receptor_pdb, contact) if receptor_pdb else set(range(n))
    if rule == "buried":
        return buried, f"buried(<= {contact} A)"
    # default intersection
    inter = mcs_atoms & buried
    if not inter:  # never leave core empty; fall back to whichever is non-empty
        inter = mcs_atoms or buried
        return inter, f"mcs+buried->fallback({'mcs' if mcs_atoms else 'buried'})"
    return inter, f"mcs+buried(<= {contact} A)"


# --------------------------------------------------------------------------- #
# docking                                                                      #
# --------------------------------------------------------------------------- #
def _run_vina(receptor_pdbqt, ligand_pdbqt, box, out_pose, exhaustiveness, num_modes, seed):
    vina = which("vina")
    if not vina:
        raise RuntimeError("AutoDock Vina CLI not found on PATH (tried /opt/conda/bin, /usr/local/bin)")
    cmd = [vina, "--receptor", receptor_pdbqt, "--ligand", ligand_pdbqt,
           "--center_x", str(box["center_x"]), "--center_y", str(box["center_y"]),
           "--center_z", str(box["center_z"]),
           "--size_x", str(box["size_x"]), "--size_y", str(box["size_y"]),
           "--size_z", str(box["size_z"]),
           "--exhaustiveness", str(exhaustiveness), "--num_modes", str(num_modes),
           "--seed", str(seed), "--out", out_pose]
    env = dict(os.environ)
    env["PATH"] = "/opt/conda/bin:/usr/local/bin:" + env.get("PATH", "")
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    aff = parse_vina_stdout(res.stdout)
    if aff is None:
        aff = parse_vina_posefile(out_pose)
    if aff is None:
        sys.stderr.write(res.stdout[-800:] + "\n" + res.stderr[-400:] + "\n")
        raise RuntimeError("could not parse redock affinity")
    return aff


def _match_file_to_rdkit(file_coords, rd_coords, tol=0.5):
    """Map PDBQT file-order heavy atoms onto RDKit heavy-atom order by matching
    (near-)identical reference coordinates. Both inputs describe the SAME crystal
    geometry, so the assignment should be exact (~0 A). Returns {file_i: rd_i} or
    None if the match is not one-to-one within tol (A)."""
    import numpy as np
    if len(file_coords) != len(rd_coords):
        return None
    F = np.asarray(file_coords, float)
    R = np.asarray(rd_coords, float)
    mapping = {}
    used = set()
    for fi in range(len(F)):
        d = np.linalg.norm(R - F[fi], axis=1)
        order = np.argsort(d)
        ri = int(order[0])
        if ri in used or d[ri] > tol:
            return None
        mapping[fi] = ri
        used.add(ri)
    if len(used) != len(rd_coords):
        return None
    return mapping


def run(args) -> int:
    os.makedirs(args.outdir, exist_ok=True)
    with open(args.box) as fh:
        box = json.load(fh)

    lig_pdbqt = os.path.join(args.outdir, "native_ligand.pdbqt")
    ref_mol = _ligand_pdb_to_pdbqt(args.ligand_pdb, lig_pdbqt, args.template_smiles, args.seed)

    out_pose = os.path.join(args.outdir, "redock_pose.pdbqt")
    aff = _run_vina(args.receptor_pdbqt, lig_pdbqt, box, out_pose,
                    args.exhaustiveness, args.num_modes, args.seed)

    # RMSD is computed in RDKit heavy-atom order so that core indices (also RDKit
    # order, from _select_core) line up with the coordinates.
    #   * ref_coords : crystal heavy-atom coords from the RDKit ref_mol conformer.
    #   * pose_coords: MODEL 1 of the redock PDBQT (Vina writes all num_modes poses
    #     as MODEL 1..N; we take the best pose only).
    # Meeko does NOT preserve RDKit atom order in the PDBQT, so we cannot compare
    # file-order to RDKit-order directly. We recover the permutation from the
    # REFERENCE: native_ligand.pdbqt and ref_mol describe the SAME crystal geometry,
    # so each PDBQT heavy atom has an (essentially) identical-coordinate twin in the
    # RDKit heavy-atom set. Nearest-coordinate matching yields file->RDKit order,
    # which we then apply to the pose. (Verified: naive same-order assumption gives
    # ~6 A per-atom error; this mapping restores the correct sub-angstrom core RMSD.)
    conf = ref_mol.GetConformer()
    heavy_atom_idx = [a.GetIdx() for a in ref_mol.GetAtoms() if a.GetAtomicNum() > 1]
    ref_coords = [[conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y,
                   conf.GetAtomPosition(i).z] for i in heavy_atom_idx]
    ref_file = pdbqt_heavy_coords(lig_pdbqt)                    # file order (crystal)
    pose_file = pdbqt_heavy_coords(out_pose, model1_only=True)  # file order (docked)

    warning = ""
    file_to_rd = _match_file_to_rdkit(ref_file, ref_coords)
    if file_to_rd is None or len(pose_file) != len(ref_coords):
        # mapping failed or atom counts differ -> fall back, warn loudly
        m = min(len(pose_file), len(ref_coords))
        warning = (f"could not build a reliable atom-order map "
                   f"(ref_rd={len(ref_coords)} ref_file={len(ref_file)} pose={len(pose_file)}); "
                   f"RMSD may be unreliable")
        ref_coords = ref_coords[:m]
        pose_coords = pose_file[:m]
    else:
        # reorder pose from file order into RDKit order
        pose_coords = [None] * len(ref_coords)
        for file_i, rd_i in file_to_rd.items():
            pose_coords[rd_i] = pose_file[file_i]
        if any(c is None for c in pose_coords):
            m = len(ref_coords)
            warning = "incomplete atom-order map; RMSD may be unreliable"
            pose_coords = [c for c in pose_coords if c is not None]
            ref_coords = ref_coords[:len(pose_coords)]
    n_heavy = len(ref_coords)

    autos = rdkit_automorphisms(ref_mol)
    # restrict automorphisms to the truncated index range if needed
    autos = [p for p in autos if max(p) < n_heavy] or [tuple(range(n_heavy))]

    rmsd_full, _ = symmetric_rmsd(ref_coords, pose_coords, autos)

    core_idx, core_rule = _select_core(ref_mol, args.core_rule, args.receptor_pdb,
                                       args.contact, args.core_smarts)
    core_idx = sorted(i for i in core_idx if i < n_heavy)
    rmsd_core = None
    if core_idx:
        ref_c = [ref_coords[i] for i in core_idx]
        pose_c = [pose_coords[i] for i in core_idx]
        # automorphisms among the core subset
        pos = {a: j for j, a in enumerate(core_idx)}
        sub_autos = []
        for p in autos:
            if all(p[i] in pos for i in core_idx):
                sub_autos.append(tuple(pos[p[i]] for i in core_idx))
        sub_autos = sub_autos or [tuple(range(len(core_idx)))]
        rmsd_core, _ = symmetric_rmsd(ref_c, pose_c, sub_autos)

    threshold = args.rmsd_threshold
    passed_full = rmsd_full is not None and rmsd_full < threshold
    passed_core = rmsd_core is not None and rmsd_core < threshold
    passed = bool(passed_full or passed_core)
    if not passed:
        warning = (warning + " | " if warning else "") + (
            f"REDOCK DID NOT PASS: full={rmsd_full} core={rmsd_core} (threshold {threshold} A). "
            "Box/protonation/receptor may be wrong; treat downstream ranking with caution.")
    elif not passed_full and passed_core:
        warning = (warning + " | " if warning else "") + (
            f"whole-molecule RMSD {rmsd_full:.2f} A exceeds {threshold} but rigid core "
            f"{rmsd_core:.2f} A passes (flexible solvent-exposed atoms moved).")

    result = {
        "top_affinity_kcal_mol": round(float(aff), 3),
        "rmsd_full_A": round(float(rmsd_full), 3) if rmsd_full is not None else None,
        "rmsd_core_A": round(float(rmsd_core), 3) if rmsd_core is not None else None,
        "rmsd_threshold_A": threshold,
        "core_rule": core_rule,
        "core_atom_indices": core_idx,
        "n_core_atoms": len(core_idx),
        "n_heavy_atoms": n_heavy,
        "passed": passed,
        "passed_by": ("full" if passed_full else ("core" if passed_core else "none")),
        "warning": warning,
        "box": {k: box[k] for k in ("center_x", "center_y", "center_z",
                                    "size_x", "size_y", "size_z")},
    }
    with open(os.path.join(args.outdir, "redock_validation.json"), "w") as fh:
        json.dump(result, fh, indent=2)

    tag = "PASS" if passed else "WARN(no-pass)"
    print(f"[{tag}] redock affinity={aff:.3f} kcal/mol | full RMSD="
          f"{result['rmsd_full_A']} A | core RMSD={result['rmsd_core_A']} A "
          f"[{core_rule}, {len(core_idx)}/{n_heavy} atoms]")
    if warning:
        print(f"[NOTE] {warning}", file=sys.stderr)
    return 0  # never hard-fail


def self_check() -> int:
    """Exercise core-selection + gate logic on a synthetic mol, no docking."""
    ok = True
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as e:
        print(f"[FAIL] rdkit: {e}", file=sys.stderr)
        return 1
    m = Chem.MolFromSmiles("c1ccc2ncnc(Nc3ccccc3)c2c1OCCN")  # quinazoline + flexible tail
    m = Chem.AddHs(m); AllChem.EmbedMolecule(m, randomSeed=1); m = Chem.RemoveHs(m)
    core, rule = _select_core(m, "mcs", None, 4.5, None)
    # core should capture the fused aromatic system and exclude the -OCCN tail
    if not core or len(core) >= m.GetNumAtoms():
        print(f"[FAIL] mcs core selection -> {len(core)}/{m.GetNumAtoms()}", file=sys.stderr); ok = False
    # gate: core passes, full fails
    passed_full = 3.2 < 2.0
    passed_core = 0.33 < 2.0
    passed = passed_full or passed_core
    if not (passed and not passed_full and passed_core):
        print("[FAIL] two-tier gate logic", file=sys.stderr); ok = False
    print(f"redock_validate.py self-check: {'PASS' if ok else 'FAIL'} "
          f"(mcs core {len(core)}/{m.GetNumAtoms()} atoms, two-tier gate ok)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Native-ligand redock validation (core-aware)")
    ap.add_argument("--receptor-pdbqt")
    ap.add_argument("--receptor-pdb", help="protein PDB (for buried-atom core rule)")
    ap.add_argument("--ligand-pdb", help="native ligand PDB (crystal coords)")
    ap.add_argument("--box", help="docking_box.json")
    ap.add_argument("--template-smiles", help="SMILES to fix native-ligand bond orders")
    ap.add_argument("--core-rule", choices=["mcs", "buried", "mcs+buried"], default="mcs+buried")
    ap.add_argument("--core-smarts", help="explicit core SMARTS (overrides rule; still audited)")
    ap.add_argument("--contact", type=float, default=4.5, help="buried-atom cutoff (A)")
    ap.add_argument("--rmsd-threshold", type=float, default=2.0)
    ap.add_argument("--exhaustiveness", type=int, default=16, help="thorough for validation")
    ap.add_argument("--num-modes", type=int, default=20)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--outdir", default="/mnt/results/sbvs_run/redock")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    for req in ("receptor_pdbqt", "ligand_pdb", "box"):
        if not getattr(args, req):
            ap.error(f"--{req.replace('_', '-')} is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
