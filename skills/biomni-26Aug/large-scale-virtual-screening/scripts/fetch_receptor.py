#!/usr/bin/env python3
"""
fetch_receptor.py -- obtain a receptor structure and isolate protein + native ligand.

Modes:
  --pdb-id 2ITY        fetch from RCSB (https://files.rcsb.org/download/<ID>.pdb)
  --pdb-file file.pdb  use a user-provided structure

Outputs (into --outdir):
  <stem>_protein.pdb   protein only, one chain (waters + HETATM stripped)
  <stem>_ligand.pdb    the chosen native/co-crystal ligand (if any) -- used later
                       for box definition and redock validation
  receptor_meta.json   chain kept, ligand resname/chain, atom counts, provenance

Chain/ligand selection:
  * default keeps the FIRST protein chain seen (override with --chain)
  * native ligand = the largest non-water HETATM residue in the kept chain, unless
    --ligand-resname is given. Common crystallization additives (buffers, ions,
    cryoprotectants) are skipped by an ignore-list so we don't pick e.g. SO4/GOL.

This step does NOT convert to PDBQT (that is prepare_receptor.py).
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request

# Residues that are almost never the ligand of interest (buffers/ions/cryo/sugars/etc.)
_IGNORE_HET = {
    "HOH", "WAT", "DOD",
    "SO4", "PO4", "CL", "NA", "K", "MG", "CA", "ZN", "MN", "FE", "CU", "NI", "CD",
    "GOL", "EDO", "PEG", "PGE", "PG4", "DMS", "MPD", "ACT", "FMT", "TRS", "EPE",
    "IOD", "BR", "BME", "MES", "IMD", "NO3", "SCN", "CO3", "NH4", "FLC",
    "GOL", "1PE", "P6G", "BOG", "OGA", "NAG", "BMA", "MAN", "FUC", "GAL",
}


def _download_pdb(pdb_id: str, dest: str) -> str:
    pdb_id = pdb_id.strip().upper()
    url = f"https://files.rcsb.org/download/{pdb_id}.pdb"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read().decode("utf-8", "replace")
    with open(dest, "w") as fh:
        fh.write(data)
    return dest


def _parse_structure(pdb_path: str):
    """Return (atom_lines_by_chain, het_residues) with light PDB parsing (no deps)."""
    protein_chains = {}          # chain -> list of ATOM lines
    het = {}                     # (chain, resname, resseq) -> list of HETATM lines
    with open(pdb_path) as fh:
        for line in fh:
            rec = line[:6].strip()
            if rec == "ATOM":
                ch = line[21]
                protein_chains.setdefault(ch, []).append(line)
            elif rec == "HETATM":
                ch = line[21]
                resname = line[17:20].strip()
                resseq = line[22:26].strip()
                het.setdefault((ch, resname, resseq), []).append(line)
    return protein_chains, het


def _choose_ligand(het, chain, forced_resname=None):
    """Pick the native ligand residue for `chain`. Returns (key, lines) or (None, None)."""
    cands = []
    for (ch, resname, resseq), lines in het.items():
        if ch != chain:
            continue
        if forced_resname:
            if resname == forced_resname.upper():
                cands.append(((ch, resname, resseq), lines))
        else:
            if resname in _IGNORE_HET:
                continue
            cands.append(((ch, resname, resseq), lines))
    if not cands:
        return None, None
    # largest by heavy-atom count
    cands.sort(key=lambda kv: sum(1 for l in kv[1] if l[76:78].strip() != "H"), reverse=True)
    return cands[0]


def run(args) -> int:
    os.makedirs(args.outdir, exist_ok=True)
    if args.pdb_id:
        raw = os.path.join(args.outdir, f"{args.pdb_id.upper()}_raw.pdb")
        _download_pdb(args.pdb_id, raw)
        stem = args.pdb_id.upper()
        provenance = f"RCSB:{args.pdb_id.upper()}"
    else:
        raw = args.pdb_file
        stem = os.path.splitext(os.path.basename(args.pdb_file))[0]
        provenance = f"file:{os.path.basename(args.pdb_file)}"

    protein_chains, het = _parse_structure(raw)
    if not protein_chains:
        print("[ERROR] no protein ATOM records found", file=sys.stderr)
        return 2

    chain = args.chain if args.chain else sorted(protein_chains)[0]
    if chain not in protein_chains:
        print(f"[ERROR] chain {chain} not in structure (have {sorted(protein_chains)})",
              file=sys.stderr)
        return 2

    prot_path = os.path.join(args.outdir, f"{stem}_protein.pdb")
    with open(prot_path, "w") as fh:
        for line in protein_chains[chain]:
            fh.write(line)
        fh.write("END\n")

    lig_key, lig_lines = _choose_ligand(het, chain, args.ligand_resname)
    lig_path = None
    lig_meta = None
    if lig_lines:
        lig_path = os.path.join(args.outdir, f"{stem}_ligand.pdb")
        with open(lig_path, "w") as fh:
            for line in lig_lines:
                fh.write(line)
            fh.write("END\n")
        lig_meta = {"chain": lig_key[0], "resname": lig_key[1], "resseq": lig_key[2],
                    "n_atoms": len(lig_lines)}

    meta = {
        "provenance": provenance,
        "chain_kept": chain,
        "n_protein_atoms": len(protein_chains[chain]),
        "protein_pdb": os.path.basename(prot_path),
        "native_ligand": lig_meta,
        "ligand_pdb": os.path.basename(lig_path) if lig_path else None,
        "all_chains": sorted(protein_chains),
    }
    with open(os.path.join(args.outdir, "receptor_meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)

    print(f"[OK] protein -> {prot_path} ({meta['n_protein_atoms']} atoms, chain {chain})")
    if lig_meta:
        print(f"[OK] native ligand {lig_meta['resname']} ({lig_meta['n_atoms']} atoms) -> {lig_path}")
    else:
        print("[WARN] no native ligand found -- box must be given manually or via a pocket detector")
    return 0


def self_check() -> int:
    # verify ligand chooser skips buffers and picks the biggest real ligand
    het = {
        ("A", "SO4", "500"): ["HETATM" + " " * 70 + "S\n"] * 5,
        ("A", "IRE", "701"): ["HETATM" + " " * 70 + "C\n"] * 31,
        ("A", "GOL", "800"): ["HETATM" + " " * 70 + "C\n"] * 6,
    }
    key, _ = _choose_ligand(het, "A")
    ok = key is not None and key[1] == "IRE"
    print("fetch_receptor.py self-check:", "PASS" if ok else f"FAIL (picked {key})")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Fetch receptor + isolate protein/native ligand")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--pdb-id", help="RCSB PDB id to download, e.g. 2ITY")
    src.add_argument("--pdb-file", help="path to a local PDB file")
    ap.add_argument("--chain", help="protein chain to keep (default: first)")
    ap.add_argument("--ligand-resname", help="force a specific ligand residue name")
    ap.add_argument("--outdir", default="/mnt/results/sbvs_run/receptor")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    if not (args.pdb_id or args.pdb_file):
        ap.error("one of --pdb-id or --pdb-file is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
