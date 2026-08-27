#!/usr/bin/env python3
"""
common.py -- shared helpers for the large-scale-virtual-screening skill.

Encapsulates the failure-prone pieces that must NOT be re-derived per run:
  * FUSE-safe copy to /mnt/results (S3-backed: no random-access/append writes)
  * AutoDock Vina score parsing (stdout table + pose-file REMARK)
  * symmetry-corrected heavy-atom RMSD over molecular automorphisms
  * PDBQT heavy-atom coordinate extraction (file order, H skipped)
  * deterministic seeding

Import from sibling scripts:  from common import <fn>
All functions are import-safe (no heavy work at import time).
"""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
from typing import Iterable, Sequence

SEED = 42


# --------------------------------------------------------------------------- #
# FUSE-safe filesystem                                                         #
# --------------------------------------------------------------------------- #
def fuse_safe_copy(src: str, dst: str) -> str:
    """
    Copy a finished file to an S3-backed FUSE path (/mnt/results, /mnt/shared-workspace).

    Those mounts do NOT support append / random-access / streaming writes, and
    Python's shutil can intermittently fail on them for some formats. The robust
    pattern used throughout this skill: build the file on local /workspace, then
    hand off to a single shell `cp` (which does one sequential write).

    NOTE: R's file.copy() produces 0-byte files on these mounts -- always use a
    shell cp, never a library copy, for the /mnt hand-off.
    """
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    # Prefer a shell cp for the /mnt hand-off; fall back to shutil for local->local.
    if dst.startswith("/mnt/"):
        subprocess.run(["cp", src, dst], check=True)
    else:
        shutil.copy(src, dst)
    return dst


def ensure_local_scratch(subdir: str = "") -> str:
    """Return a writable local scratch dir under /workspace (POSIX, fast)."""
    base = os.path.join("/workspace", "sbvs_scratch", subdir)
    os.makedirs(base, exist_ok=True)
    return base


# --------------------------------------------------------------------------- #
# AutoDock Vina score parsing                                                  #
# --------------------------------------------------------------------------- #
def parse_vina_stdout(stdout: str):
    """
    Best-affinity (mode 1) from Vina stdout result table.

    The results table rows look like:  '   1       -8.964          0          0'
    i.e. a 4-field line whose first field == '1'. Returns float kcal/mol or None.
    """
    for line in stdout.splitlines():
        s = line.split()
        if len(s) == 4 and s[0] == "1":
            try:
                return float(s[1])
            except ValueError:
                continue
    return None


def parse_vina_posefile(path: str):
    """
    Best-affinity from a Vina output PDBQT: first 'REMARK VINA RESULT' line,
    4th whitespace token. Returns float kcal/mol or None.
    """
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("REMARK VINA RESULT"):
                    return float(line.split()[3])
    except (OSError, ValueError, IndexError):
        return None
    return None


# --------------------------------------------------------------------------- #
# PDBQT heavy-atom coordinates (file order)                                    #
# --------------------------------------------------------------------------- #
def pdbqt_heavy_coords(path: str, model1_only: bool = False):
    """
    Extract heavy-atom (x,y,z) from a PDBQT in FILE ORDER, skipping hydrogens.

    Skips AutoDock atom types 'H'/'HD' (cols 78-79) and PDB atom names that begin
    with 'H'. File order matters: it lets us compare two PDBQTs written from the
    SAME meeko-prepared molecule (identical atom order) without a fragile RDKit
    round trip.

    model1_only=True stops at the first ENDMDL, so a multi-mode Vina output
    (MODEL 1..N) yields only the top pose's atoms -- essential for RMSD, since a
    20-mode redock file otherwise returns ~20x the atoms.

    Returns list[(x, y, z)] as floats.
    """
    coords = []
    with open(path) as fh:
        for line in fh:
            if model1_only and line.startswith("ENDMDL"):
                break
            if not (line.startswith("ATOM") or line.startswith("HETATM")):
                continue
            atom_name = line[12:16].strip()
            atype = line[77:79].strip() if len(line) >= 79 else ""
            if atype in ("H", "HD"):
                continue
            if atom_name[:1] == "H":
                continue
            try:
                x = float(line[30:38]); y = float(line[38:46]); z = float(line[46:54])
            except ValueError:
                continue
            coords.append((x, y, z))
    return coords


# --------------------------------------------------------------------------- #
# Symmetry-corrected RMSD over automorphisms                                   #
# --------------------------------------------------------------------------- #
def symmetric_rmsd(ref_coords: Sequence[Sequence[float]],
                   probe_coords: Sequence[Sequence[float]],
                   automorphisms: Iterable[Sequence[int]] | None = None):
    """
    Minimum heavy-atom RMSD between two equal-length coordinate sets, minimizing
    over a set of atom-index permutations (molecular automorphisms) so that
    symmetric substructures (e.g. a symmetric ring) do not inflate RMSD.

    ref_coords, probe_coords : aligned N x 3 arrays in the SAME atom indexing.
    automorphisms            : iterable of length-N index tuples. If None, only
                               the identity permutation is used.

    Returns (best_rmsd, best_perm). Requires numpy.
    """
    import numpy as np
    ref = np.asarray(ref_coords, dtype=float)
    prb = np.asarray(probe_coords, dtype=float)
    if ref.shape != prb.shape or ref.ndim != 2 or ref.shape[1] != 3:
        raise ValueError(f"coord shape mismatch: ref{ref.shape} probe{prb.shape}")
    n = ref.shape[0]
    if automorphisms is None:
        automorphisms = [tuple(range(n))]
    best_rmsd = None
    best_perm = None
    for perm in automorphisms:
        perm = list(perm)
        if len(perm) != n:
            continue
        d2 = np.sum((ref[perm] - prb) ** 2, axis=1)
        rmsd = float(np.sqrt(d2.mean()))
        if best_rmsd is None or rmsd < best_rmsd:
            best_rmsd = rmsd
            best_perm = perm
    return best_rmsd, best_perm


def rdkit_automorphisms(mol, max_matches: int = 10000):
    """
    Return self-match permutations (automorphisms) of an RDKit mol's heavy-atom
    graph, for use with symmetric_rmsd. Uses GetSubstructMatches(mol, mol,
    uniquify=False) which enumerates symmetry-equivalent atom mappings.
    """
    try:
        matches = mol.GetSubstructMatches(mol, uniquify=False, maxMatches=max_matches)
    except TypeError:
        matches = mol.GetSubstructMatches(mol, uniquify=False)
    return [tuple(m) for m in matches] if matches else [tuple(range(mol.GetNumAtoms()))]


# --------------------------------------------------------------------------- #
# Misc                                                                         #
# --------------------------------------------------------------------------- #
def seed_everything(seed: int = SEED):
    """Deterministic seeding for random + numpy (RDKit embed takes its own seed)."""
    import random
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:
        pass


def which(cmd: str):
    """shutil.which with the AutoDock/conda bin dirs added to PATH."""
    extra = ["/opt/conda/bin", "/usr/local/bin"]
    path = os.environ.get("PATH", "")
    for p in extra:
        if p not in path:
            path = p + os.pathsep + path
    return shutil.which(cmd, path=path)


def label_from_row(r):
    """Resolve a 0/1 activity label from a merged-scores row (dict-like).

    Primary key: numeric ``activity_label`` (1/0). Fallback: a textual
    ``source`` column ({active/actives -> 1, decoy/decoys/inactive -> 0}).
    The fallback matters because merged-score files are frequently labeled ONLY
    by a `source` string (this is how the reference EGFR run was written); without
    it, such a file would be treated as unlabeled and enrichment silently skipped.
    Returns 1, 0, or None (unlabeled / unparseable). Shared so enrichment, triage,
    and figures resolve labels identically."""
    lab = r.get("activity_label", "")
    if lab not in ("", None):
        try:
            return int(float(lab))
        except (ValueError, TypeError):
            pass
    src = (r.get("source") or "").strip().lower()
    if src in ("active", "actives"):
        return 1
    if src in ("decoy", "decoys", "inactive", "inactives"):
        return 0
    return None


def self_check() -> int:
    """Validate imports + basic behaviour without heavy compute. Returns exit code."""
    ok = True
    # Vina stdout parse
    demo = "mode |   affinity\n-----+-----------\n   1       -8.964          0          0\n   2       -7.10          1.2        2.1"
    v = parse_vina_stdout(demo)
    if v != -8.964:
        print(f"[FAIL] parse_vina_stdout -> {v}", file=sys.stderr); ok = False
    # label resolution: activity_label primary, source fallback, None otherwise
    if (label_from_row({"activity_label": "1"}) != 1
            or label_from_row({"activity_label": "0"}) != 0
            or label_from_row({"source": "active"}) != 1
            or label_from_row({"source": "decoy"}) != 0
            or label_from_row({"source": "user"}) is not None
            or label_from_row({}) is not None):
        print("[FAIL] label_from_row resolution", file=sys.stderr); ok = False
    # symmetric RMSD identity == 0
    r, _ = symmetric_rmsd([[0, 0, 0], [1, 0, 0]], [[0, 0, 0], [1, 0, 0]])
    if r is None or r > 1e-9:
        print(f"[FAIL] symmetric_rmsd identity -> {r}", file=sys.stderr); ok = False
    # permutation improves a swapped pair
    r2, perm = symmetric_rmsd([[0, 0, 0], [1, 0, 0]], [[1, 0, 0], [0, 0, 0]],
                              automorphisms=[(0, 1), (1, 0)])
    if r2 is None or r2 > 1e-9:
        print(f"[FAIL] symmetric_rmsd swap -> {r2} perm={perm}", file=sys.stderr); ok = False
    print("common.py self-check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="shared SBVS helpers")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    ap.print_help()
