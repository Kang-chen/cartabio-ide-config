#!/usr/bin/env python3
"""
prepare_ligands.py -- 3D-embed and PDBQT-prepare library ligands for Vina.

Per ligand:
  MolFromSmiles -> AddHs -> ETKDGv3 embed (seed) -> [retry useRandomCoords] ->
  MMFFOptimizeMolecule(maxIters=500) -> Meeko MoleculePreparation().prepare() ->
  PDBQTWriterLegacy.write_string()

Reads master_library.csv (mol_id, smiles, ...). Writes:
  <outdir>/pdbqt/<mol_id>.pdbqt          one file per successful ligand
  <outdir>/ligand_prep_log.csv           mol_id,status,note   (status ok|fail)

Parallelised with a process pool (--nproc). Deterministic embedding via --seed.
Meeko/gemmi are installed at runtime (`uv pip install meeko gemmi`) -- not in the
base image; SKILL.md documents this.

Acceptance target: >=95% of parseable SMILES prepared. The log makes failures
auditable (bad valence, embed failure, etc.).
"""
from __future__ import annotations
import argparse
import csv
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

try:
    from common import SEED
except ImportError:
    from scripts.common import SEED  # pragma: no cover


def _prep_one(mol_id, smiles, out_dir, seed):
    from rdkit import Chem
    from rdkit.Chem import AllChem
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return mol_id, "fail", "unparseable SMILES"
        mol = Chem.AddHs(mol)
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        if AllChem.EmbedMolecule(mol, params) != 0:
            params.useRandomCoords = True
            if AllChem.EmbedMolecule(mol, params) != 0:
                return mol_id, "fail", "embed failed"
        try:
            AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
        except Exception as e:  # MMFF can fail on exotic atoms; keep embedded geom
            pass
        from meeko import MoleculePreparation
        try:
            from meeko import PDBQTWriterLegacy
            prep = MoleculePreparation()
            setups = prep.prepare(mol)
            setup = setups[0] if isinstance(setups, (list, tuple)) else setups
            pdbqt, ok, err = PDBQTWriterLegacy.write_string(setup)
            if not ok:
                return mol_id, "fail", f"meeko write: {err}"
        except ImportError:
            # older meeko API
            prep = MoleculePreparation()
            prep.prepare(mol)
            pdbqt = prep.write_pdbqt_string()
        out = os.path.join(out_dir, f"{mol_id}.pdbqt")
        with open(out, "w") as fh:
            fh.write(pdbqt)
        return mol_id, "ok", ""
    except Exception as e:  # noqa: BLE001
        return mol_id, "fail", f"{type(e).__name__}: {e}"[:200]


def run(args) -> int:
    pdbqt_dir = os.path.join(args.outdir, "pdbqt")
    os.makedirs(pdbqt_dir, exist_ok=True)
    rows = []
    with open(args.library, newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("smiles"):
                rows.append((r["mol_id"], r["smiles"]))
    if args.limit:
        rows = rows[: args.limit]
    print(f"[info] preparing {len(rows)} ligands with nproc={args.nproc}")

    results = []
    if args.nproc > 1:
        with ProcessPoolExecutor(max_workers=args.nproc) as ex:
            futs = {ex.submit(_prep_one, mid, smi, pdbqt_dir, args.seed): mid
                    for mid, smi in rows}
            done = 0
            for fut in as_completed(futs):
                results.append(fut.result())
                done += 1
                if done % max(1, len(rows) // 10) == 0:
                    print(f"  ...{done}/{len(rows)}")
    else:
        for mid, smi in rows:
            results.append(_prep_one(mid, smi, pdbqt_dir, args.seed))

    log = os.path.join(args.outdir, "ligand_prep_log.csv")
    with open(log, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mol_id", "status", "note"])
        w.writerows(results)
    n_ok = sum(1 for _, s, _ in results if s == "ok")
    rate = 100.0 * n_ok / max(1, len(results))
    print(f"[OK] prepared {n_ok}/{len(results)} ({rate:.1f}%) -> {pdbqt_dir}")
    print(f"[OK] prep log -> {log}")
    if rate < 95.0:
        print(f"[WARN] prep success {rate:.1f}% < 95% target; inspect ligand_prep_log.csv",
              file=sys.stderr)
    return 0


def self_check() -> int:
    # Import-only check for RDKit; meeko may be installed at runtime.
    ok = True
    try:
        from rdkit import Chem  # noqa: F401
        from rdkit.Chem import AllChem  # noqa: F401
    except ImportError as e:
        print(f"[FAIL] rdkit import: {e}", file=sys.stderr); ok = False
    try:
        import meeko  # noqa: F401
        meeko_ok = True
    except ImportError:
        meeko_ok = False
    print(f"prepare_ligands.py self-check: {'PASS' if ok else 'FAIL'} "
          f"(rdkit ok, meeko {'present' if meeko_ok else 'install at runtime'})")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Embed + PDBQT-prepare ligands (RDKit+Meeko)")
    ap.add_argument("--library", help="master_library.csv")
    ap.add_argument("--outdir", default="/mnt/results/sbvs_run/ligands")
    ap.add_argument("--nproc", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--limit", type=int, default=0, help="cap #ligands (smoke tests)")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    if not args.library:
        ap.error("--library is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
