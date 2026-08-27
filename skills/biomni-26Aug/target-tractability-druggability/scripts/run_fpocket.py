#!/usr/bin/env python3
"""
run_fpocket.py — run fpocket on a cleaned PDB and summarize the top druggable pocket, for ANY
protein target. Generalizes the KRAS workflow: pocket location is annotated RELATIVE to a
reference ligand / cofactor centroid (computable for any structure), NOT hardcoded residues.

CRITICAL: fpocket pocket files are 1-INDEXED (pocket1_atm.pdb ...). Do NOT add a +1 offset.

Outputs a JSON summary with, for the top pocket (by Druggability Score):
  drug_score, volume, n_residues, hydrophobicity, sasa, residues,
  centroid, distance_to_reference_ligand, location_label, druggable_class.

Usage:
    python run_fpocket.py --pdb /workspace/egfr_struct/EGFR_holo.pdb \
        --ref-ligand-centroid 17.18 33.93 38.43 \
        --label "EGFR holo (1XKK, lapatinib)" \
        --out /workspace/egfr_pocket.json

    # apo vs holo contrast in one shot
    python run_fpocket.py --pdb apo.pdb --pdb-holo holo.pdb \
        --ref-ligand-centroid X Y Z --out pockets.json

Requires: fpocket on PATH (conda install -n base -c conda-forge -c bioconda fpocket).
"""
import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys


def ensure_fpocket():
    if shutil.which("fpocket") is None:
        sys.exit("fpocket not found on PATH. Install with:\n"
                 "  conda install -n base -y -c conda-forge -c bioconda fpocket")


def run_fpocket(pdb_path):
    """Run fpocket -f <pdb>. Returns the *_out directory path."""
    pdb_path = os.path.abspath(pdb_path)
    subprocess.run(["fpocket", "-f", pdb_path], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out_dir = pdb_path.rsplit(".", 1)[0] + "_out"
    if not os.path.isdir(out_dir):
        raise RuntimeError(f"fpocket did not produce {out_dir}")
    return out_dir


def parse_info(info_path):
    """
    Parse <name>_info.txt -> {pocket_id(int): {metric_lower: float}}. Pockets are 1-indexed.
    Keys are normalized to lowercase with collapsed whitespace so lookups are robust across
    fpocket versions (e.g. 'Hydrophobicity Score' vs 'Hydrophobicity score').
    """
    pockets = {}
    cur = None
    with open(info_path) as fh:
        for line in fh:
            m = re.match(r"\s*Pocket\s+(\d+)\s*:", line)
            if m:
                cur = int(m.group(1))
                pockets[cur] = {}
                continue
            if cur is not None and ":" in line:
                k, _, v = line.partition(":")
                key = re.sub(r"\s+", " ", k.strip().lower())
                try:
                    pockets[cur][key] = float(v.strip())
                except ValueError:
                    pass
    return pockets


def _get(metrics, *names):
    """Case-insensitive metric lookup by any of the given human-readable names."""
    for n in names:
        key = re.sub(r"\s+", " ", n.strip().lower())
        if key in metrics:
            return metrics[key]
    return None


def pocket_atoms(out_dir):
    """
    Return {pocket_id(int): {"residues": set[(resname,resnum)], "centroid": [x,y,z], "n_atoms": k}}.
    Pocket files are 1-INDEXED: pocket1_atm.pdb, pocket2_atm.pdb, ... (NO pocket0). Parse the id
    straight from the filename with NO offset.
    """
    result = {}
    for pf in sorted(glob.glob(os.path.join(out_dir, "pockets", "pocket*_atm.pdb"))):
        pid = int(re.search(r"pocket(\d+)_atm", os.path.basename(pf)).group(1))  # NO +1 offset
        residues = set()
        xs = ys = zs = 0.0
        natoms = 0
        with open(pf) as fh:
            for line in fh:
                if line.startswith(("ATOM", "HETATM")):
                    resn = line[17:20].strip()
                    try:
                        rnum = int(line[22:26])
                        x = float(line[30:38]); y = float(line[38:46]); z = float(line[46:54])
                    except ValueError:
                        continue
                    residues.add((resn, rnum))
                    xs += x; ys += y; zs += z; natoms += 1
        centroid = [round(xs / natoms, 3), round(ys / natoms, 3), round(zs / natoms, 3)] \
            if natoms else None
        result[pid] = {"residues": residues, "centroid": centroid, "n_atoms": natoms}
    return result


def dist(a, b):
    if not a or not b:
        return None
    return round(math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3))), 2)


def druggable_class(score):
    if score is None:
        return "unknown"
    if score > 0.5:
        return "druggable"
    if score >= 0.2:
        return "borderline"
    return "poorly_druggable"


def location_label(d_ligand):
    """Descriptive, target-agnostic label from distance to the reference ligand centroid."""
    if d_ligand is None:
        return "unannotated (no reference ligand)"
    if d_ligand <= 12:
        return f"drug/ligand-engaged (~{d_ligand} A from ligand)"
    if d_ligand <= 20:
        return f"peri-ligand (~{d_ligand} A from ligand)"
    return f"distinct / allosteric-type (~{d_ligand} A from ligand)"


def analyze(pdb_path, ref_centroid=None, label=None, drug_score_key="Druggability Score"):
    out_dir = run_fpocket(pdb_path)
    base = os.path.basename(pdb_path).rsplit(".", 1)[0]
    info = parse_info(os.path.join(out_dir, f"{base}_info.txt"))
    atoms = pocket_atoms(out_dir)
    if not info:
        return {"label": label or base, "error": "no pockets detected", "out_dir": out_dir}

    # pick top pocket by druggability score (case-insensitive; robust across fpocket versions)
    def score_of(pid):
        s = _get(info[pid], "Druggability Score", "Drug Score")
        return s if s is not None else -1.0
    top = max(info, key=score_of)
    a = atoms.get(top, {})
    residues = sorted(a.get("residues", []), key=lambda r: r[1])
    d_lig = dist(a.get("centroid"), ref_centroid)
    score = score_of(top)
    score = None if score == -1.0 else score
    return {
        "label": label or base,
        "structure": os.path.basename(pdb_path),
        "top_pocket_id": top,
        "drug_score": round(score, 3) if score is not None else None,
        "druggable_class": druggable_class(score),
        "volume": _get(info[top], "Volume"),
        "n_pocket_residues": len(residues),
        "hydrophobicity_score": _get(info[top], "Hydrophobicity Score", "Hydrophobicity"),
        "total_sasa": _get(info[top], "Total SASA"),
        "centroid": a.get("centroid"),
        "distance_to_reference_ligand": d_lig,
        "location_label": location_label(d_lig),
        "residues": [f"{rn}{num}" for rn, num in residues],
        "out_dir": out_dir,
        "n_pockets_total": len(info),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdb", required=True, help="cleaned PDB (single chain, waters dropped)")
    ap.add_argument("--pdb-holo", default=None, help="optional holo/drug-bound PDB for contrast")
    ap.add_argument("--ref-ligand-centroid", nargs=3, type=float, default=None,
                    metavar=("X", "Y", "Z"),
                    help="reference ligand centroid for pocket annotation (from fetch_structure)")
    ap.add_argument("--label", default=None)
    ap.add_argument("--label-holo", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ensure_fpocket()
    ref = args.ref_ligand_centroid
    res = {"apo_or_primary": analyze(args.pdb, ref, args.label)}
    print(f"[fpocket] {res['apo_or_primary']['label']}: "
          f"top pocket #{res['apo_or_primary'].get('top_pocket_id')} "
          f"drug_score={res['apo_or_primary'].get('drug_score')} "
          f"({res['apo_or_primary'].get('druggable_class')}), "
          f"{res['apo_or_primary'].get('location_label')}")
    if args.pdb_holo:
        res["holo"] = analyze(args.pdb_holo, ref, args.label_holo)
        print(f"[fpocket] {res['holo']['label']}: top pocket #{res['holo'].get('top_pocket_id')} "
              f"drug_score={res['holo'].get('drug_score')} ({res['holo'].get('druggable_class')})")
        a, h = res["apo_or_primary"].get("drug_score"), res["holo"].get("drug_score")
        if a and h and a > 0:
            res["apo_to_holo_fold"] = round(h / a, 2)
            print(f"[fpocket] apo->holo druggability fold-change: {res['apo_to_holo_fold']}x "
                  f"(large jump suggests a cryptic pocket)")

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(res, fh, indent=2)
        print(f"[done] wrote {args.out}")
    return res


if __name__ == "__main__":
    main()
