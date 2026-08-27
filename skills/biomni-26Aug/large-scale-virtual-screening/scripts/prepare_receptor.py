#!/usr/bin/env python3
"""
prepare_receptor.py -- convert a protein PDB to PDBQT and define the docking box.

Two jobs:
  1. Receptor PDBQT:
       primary : ADFR `prepare_receptor -r <pdb> -o <pdbqt> -A hydrogens`
       fallback: Meeko `mk_prepare_receptor.py` OR OpenBabel `obabel -xr`
     (PATH is extended with /opt/conda/bin and /usr/local/bin.)
  2. Docking box:
       --from-ligand <ligand.pdb>   center = ligand centroid; size = ligand extent
                                     + 2*--padding (default 4 A), clamped to a cube
                                     of at least --min-size (default 16 A).
       --center X Y Z --size S       explicit box (overrides ligand).

Outputs (into --outdir):
  <stem>.pdbqt         receptor for Vina
  docking_box.json     {center_x,center_y,center_z,size_x,size_y,size_z, source}

Rationale for defaults: a ~16-22 A cube around the co-crystal ligand is a standard
pocket-scale search space; too large a box degrades both accuracy and speed.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys

try:
    from common import which
except ImportError:
    from scripts.common import which  # pragma: no cover


def _receptor_to_pdbqt(pdb_path: str, out_pdbqt: str) -> str:
    """Try ADFR prepare_receptor, then Meeko, then OpenBabel."""
    env = dict(os.environ)
    env["PATH"] = "/opt/conda/bin:/usr/local/bin:" + env.get("PATH", "")

    pr = which("prepare_receptor")
    if pr:
        cmd = [pr, "-r", pdb_path, "-o", out_pdbqt, "-A", "hydrogens"]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(out_pdbqt) and os.path.getsize(out_pdbqt) > 0:
            return "prepare_receptor(ADFR)"
        sys.stderr.write(f"[WARN] prepare_receptor failed: {res.stderr[-400:]}\n")

    mk = which("mk_prepare_receptor.py") or which("mk_prepare_receptor")
    if mk:
        cmd = [mk, "--read_pdb", pdb_path, "-o", out_pdbqt.replace(".pdbqt", "")]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if os.path.exists(out_pdbqt) and os.path.getsize(out_pdbqt) > 0:
            return "meeko(mk_prepare_receptor)"
        sys.stderr.write(f"[WARN] meeko receptor prep failed: {res.stderr[-400:]}\n")

    ob = which("obabel")
    if ob:
        cmd = [ob, pdb_path, "-O", out_pdbqt, "-xr", "-p", "7.4"]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if os.path.exists(out_pdbqt) and os.path.getsize(out_pdbqt) > 0:
            return "openbabel(obabel -xr)"
        sys.stderr.write(f"[WARN] obabel receptor prep failed: {res.stderr[-400:]}\n")

    raise RuntimeError(
        "No receptor-prep tool succeeded. Install ADFR (prepare_receptor) or meeko, "
        "or ensure obabel is on PATH. Tried /opt/conda/bin and /usr/local/bin.")


def _ligand_extent(ligand_pdb: str):
    xs, ys, zs = [], [], []
    with open(ligand_pdb) as fh:
        for line in fh:
            if line[:6].strip() in ("ATOM", "HETATM"):
                try:
                    xs.append(float(line[30:38])); ys.append(float(line[38:46])); zs.append(float(line[46:54]))
                except ValueError:
                    continue
    if not xs:
        raise ValueError(f"no atoms parsed from {ligand_pdb}")
    cen = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    ext = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    return cen, ext


def _box_from_ligand(ligand_pdb: str, padding: float, min_size: float):
    cen, ext = _ligand_extent(ligand_pdb)
    size = max(min_size, max(ext) + 2 * padding)
    return {
        "center_x": round(cen[0], 3), "center_y": round(cen[1], 3), "center_z": round(cen[2], 3),
        "size_x": round(size, 3), "size_y": round(size, 3), "size_z": round(size, 3),
        "source": f"native_ligand:{os.path.basename(ligand_pdb)} (padding={padding}, min={min_size})",
    }


def run(args) -> int:
    os.makedirs(args.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.protein_pdb))[0].replace("_protein", "")
    out_pdbqt = os.path.join(args.outdir, f"{stem}.pdbqt")

    method = _receptor_to_pdbqt(args.protein_pdb, out_pdbqt)
    print(f"[OK] receptor PDBQT -> {out_pdbqt} via {method}")

    if args.center and args.size:
        box = {
            "center_x": args.center[0], "center_y": args.center[1], "center_z": args.center[2],
            "size_x": args.size, "size_y": args.size, "size_z": args.size,
            "source": "explicit --center/--size",
        }
    elif args.from_ligand:
        box = _box_from_ligand(args.from_ligand, args.padding, args.min_size)
    else:
        raise SystemExit("Provide --from-ligand LIGAND.pdb or --center X Y Z --size S")

    box["receptor_pdbqt"] = os.path.basename(out_pdbqt)
    box["prep_method"] = method
    with open(os.path.join(args.outdir, "docking_box.json"), "w") as fh:
        json.dump(box, fh, indent=2)
    print(f"[OK] box center=({box['center_x']},{box['center_y']},{box['center_z']}) "
          f"size={box['size_x']} A  [{box['source']}]")
    return 0


def self_check() -> int:
    # box math on a synthetic ligand pdb
    import tempfile
    lines = []
    for i, (x, y, z) in enumerate([(0, 0, 0), (4, 0, 0), (0, 6, 0), (0, 0, 2)]):
        lines.append(f"HETATM{i:5d}  C   LIG A 701    {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           C\n")
    with tempfile.NamedTemporaryFile("w", suffix=".pdb", delete=False) as tf:
        tf.writelines(lines); p = tf.name
    box = _box_from_ligand(p, padding=4.0, min_size=16.0)
    os.unlink(p)
    ok = (box["center_x"] == 2.0 and box["center_y"] == 3.0 and box["size_x"] == 16.0)
    print("prepare_receptor.py self-check:", "PASS" if ok else f"FAIL ({box})")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Receptor -> PDBQT + docking box")
    ap.add_argument("--protein-pdb", help="protein-only PDB (from fetch_receptor.py)")
    ap.add_argument("--from-ligand", help="native ligand PDB to define the box")
    ap.add_argument("--center", nargs=3, type=float, metavar=("X", "Y", "Z"))
    ap.add_argument("--size", type=float, help="cubic box edge (A)")
    ap.add_argument("--padding", type=float, default=4.0, help="A added around ligand extent")
    ap.add_argument("--min-size", type=float, default=16.0, help="minimum cubic edge (A)")
    ap.add_argument("--outdir", default="/mnt/results/sbvs_run/receptor")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    if not args.protein_pdb:
        ap.error("--protein-pdb is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
