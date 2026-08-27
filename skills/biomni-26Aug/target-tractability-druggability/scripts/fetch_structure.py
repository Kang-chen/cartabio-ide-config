#!/usr/bin/env python3
"""
fetch_structure.py — obtain a 3D structure for pocket detection, for ANY human protein target.

Priority (auto mode):
  1. Best EXPERIMENTAL structure from RCSB PDB, mapped to the target's human UniProt accession.
     Candidates are ranked by:  bound drug-like ligand (holo) > resolution > chain completeness.
     Optionally returns BOTH a representative APO and a HOLO structure so the caller can contrast
     them (cryptic pockets appear as an apo->holo druggability jump).
  2. AlphaFold predicted model (fallback) when no usable experimental structure exists.
  3. Nothing (caller skips the pocket step with a documented note).

Also cleans a chosen PDB to a single chain (drop waters; optionally keep one reference ligand)
ready for fpocket.

Usage:
    # discover + download best structure(s) for a UniProt accession
    python fetch_structure.py --uniprot P00533 --outdir /workspace/egfr_struct

    # force a specific PDB id
    python fetch_structure.py --pdb 6OIM --outdir /workspace/kras_struct

    # clean a downloaded file for fpocket (chain A, drop waters, keep ligand MOV)
    python fetch_structure.py --clean /workspace/kras_struct/6oim.pdb \
        --chain A --keep-ligand MOV --clean-out /workspace/kras_struct/KRAS_holo.pdb

Requires: requests.  (No heavy structure libraries required — PDB is parsed with fixed columns.)
"""
import argparse
import json
import os
import sys

import requests

RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_FILE = "https://files.rcsb.org/download/{pdb}.pdb"
RCSB_ENTRY = "https://data.rcsb.org/rest/v1/core/entry/{pdb}"
UNIPROT_JSON = "https://rest.uniprot.org/uniprotkb/{acc}.json"
# AlphaFold model-file versions change over time (v4 -> v6 -> ...). Prefer the API,
# which reports the current pdbUrl; fall back to trying known versioned URLs.
ALPHAFOLD_API = "https://alphafold.ebi.ac.uk/api/prediction/{acc}"
ALPHAFOLD_FILE = "https://alphafold.ebi.ac.uk/files/AF-{acc}-F1-model_v{ver}.pdb"
ALPHAFOLD_VERSIONS = (6, 5, 4)  # newest first, used only if the API is unreachable

# Heteroatoms that are NOT drug-like: buffers, ions, cryo agents, glycans/sugars. Used to decide
# whether a structure is genuinely "holo (drug-like)". NOTE: we deliberately do NOT list common
# cofactors (GDP/GTP/ATP/NAD/FAD...) here as "non-drug" for the CLASSIFICATION of the reference
# ligand centroid, but the primary holo test is a HEAVY-ATOM-COUNT threshold on the actual
# coordinates (see `pdb_ligands`), which is far more robust than any code list.
NON_DRUG_HET = {
    "HOH", "DOD", "SO4", "PO4", "GOL", "EDO", "PEG", "PGE", "PG4", "P6G", "1PE", "MPD",
    "ACT", "FMT", "CL", "NA", "K", "MG", "CA", "ZN", "MN", "FE", "NI", "CU", "CD", "PT",
    "HG", "AU", "AG", "BR", "IOD", "Iod", "TRS", "DMS", "BME", "IMD", "EPE", "MES", "ACY",
    "CIT", "FLC", "TLA", "NO3", "NH4", "CO3", "SCN", "AZI", "OXY",
    # glycans / sugars (glycosylation, not ligands)
    "NAG", "NDG", "BMA", "MAN", "FUC", "GAL", "GLC", "BGC", "FUL", "SIA", "XYS", "A2G", "NGA",
}
# minimum heavy-atom count for a HETATM group to be considered a drug-like ligand
DRUGLIKE_MIN_HEAVY = 12


def uniprot_to_pdbs(acc):
    """Map UniProt -> list of PDB candidates using the UniProt cross-references (robust, no schema churn)."""
    r = requests.get(UNIPROT_JSON.format(acc=acc), timeout=60)
    r.raise_for_status()
    j = r.json()
    cands = []
    for x in j.get("uniProtKBCrossReferences", []):
        if x.get("database") != "PDB":
            continue
        props = {p["key"]: p["value"] for p in x.get("properties", [])}
        res = props.get("Resolution", "")
        try:
            resolution = float(res.split()[0]) if res and res[0].isdigit() else None
        except Exception:
            resolution = None
        cands.append({
            "pdb": x["id"],
            "method": props.get("Method"),
            "resolution": resolution,
            "chains": props.get("Chains"),
        })
    return cands


def pdb_ligands(pdb_text):
    """
    Classify HET ligands directly from PDB coordinates (robust, no REST churn).
    Returns dict {resname: max_heavy_atom_count} for non-water HETATM groups, plus a
    `has_drug_like` flag and the list of drug-like ligand codes (heavy-atom >= threshold and
    not a known buffer/ion/sugar).
    """
    from collections import defaultdict
    counts = defaultdict(int)
    for line in pdb_text.splitlines():
        if not line.startswith("HETATM"):
            continue
        resn = line[17:20].strip().upper()
        if resn in ("HOH", "DOD"):
            continue
        el = line[76:78].strip()
        # skip hydrogens (explicit element H, or a name beginning with H when element col empty)
        if el == "H" or (el == "" and line[12:14].strip()[:1] == "H"):
            continue
        key = (resn, line[21], line[22:26].strip())
        counts[key] += 1
    heavy = {}
    for (resn, _ch, _rn), n in counts.items():
        heavy[resn] = max(heavy.get(resn, 0), n)
    drug_like = [r for r, n in heavy.items()
                 if n >= DRUGLIKE_MIN_HEAVY and r not in NON_DRUG_HET]
    return {"ligand_heavy_atoms": heavy, "drug_like_ligands": sorted(drug_like),
            "has_drug_like": len(drug_like) > 0}


def entry_ligands(pdb):
    """Download the PDB and classify ligands from coordinates. Returns (has_drug_like, codes)."""
    try:
        r = requests.get(RCSB_FILE.format(pdb=pdb.upper()), timeout=120)
        r.raise_for_status()
    except Exception:
        return False, [], None
    info = pdb_ligands(r.text)
    return info["has_drug_like"], info["drug_like_ligands"], r.text


def rank_candidates(cands, outdir):
    """
    Download each candidate, classify ligands from coordinates, and sort:
    drug-like ligand first, then best resolution. Caches the downloaded text to disk so the
    chosen structure is not re-fetched.
    """
    os.makedirs(outdir, exist_ok=True)
    for c in cands:
        has_drug, ligs, text = entry_ligands(c["pdb"])
        c["has_drug_like_ligand"] = has_drug
        c["ligands"] = ligs
        if text:
            p = os.path.join(outdir, f"{c['pdb'].lower()}.pdb")
            with open(p, "w") as fh:
                fh.write(text)
            c["path"] = p
    cands.sort(key=lambda c: (
        0 if c["has_drug_like_ligand"] else 1,
        c["resolution"] if c["resolution"] is not None else 99.0,
    ))
    return cands


def download_pdb(pdb, outdir):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{pdb.lower()}.pdb")
    r = requests.get(RCSB_FILE.format(pdb=pdb.upper()), timeout=120)
    r.raise_for_status()
    with open(path, "w") as fh:
        fh.write(r.text)
    return path


def download_alphafold(acc, outdir):
    """Download the AlphaFold model for a UniProt accession.

    Resolves the current PDB file URL via the AlphaFold API (version-agnostic, so
    it survives model_v4 -> v6 -> ... bumps). If the API is unreachable, tries a
    short list of known versioned file URLs newest-first.
    """
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"AF-{acc}-F1.pdb")

    # 1) Preferred: ask the API for the current pdbUrl.
    pdb_url = None
    try:
        r = requests.get(ALPHAFOLD_API.format(acc=acc), timeout=60)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                pdb_url = data[0].get("pdbUrl")
    except Exception:
        pdb_url = None

    if pdb_url:
        try:
            g = requests.get(pdb_url, timeout=120)
            if g.status_code == 200 and g.text.startswith(("HEADER", "ATOM", "MODEL")):
                with open(path, "w") as fh:
                    fh.write(g.text)
                return path
        except Exception:
            pass

    # 2) Fallback: try known versioned file URLs, newest first.
    for ver in ALPHAFOLD_VERSIONS:
        try:
            g = requests.get(ALPHAFOLD_FILE.format(acc=acc, ver=ver), timeout=120)
            if g.status_code == 200 and g.text.startswith(("HEADER", "ATOM", "MODEL")):
                with open(path, "w") as fh:
                    fh.write(g.text)
                return path
        except Exception:
            continue
    return None


# ------------------------------------------------------------------------------- cleaning
def chain_residue_counts(pdb_path):
    counts = {}
    with open(pdb_path) as fh:
        seen = set()
        for line in fh:
            if line.startswith("ATOM"):
                ch = line[21]
                key = (ch, line[22:27])
                if key not in seen:
                    seen.add(key)
                    counts[ch] = counts.get(ch, 0) + 1
    return counts


def clean_pdb(pdb_path, out_path, keep_chain=None, keep_ligands=None, drop_water=True):
    """
    Write a cleaned PDB for fpocket:
      - keep only `keep_chain` (default: chain with most residues)
      - always drop waters (HOH/DOD) unless drop_water=False
      - keep only HETATM whose resname is in keep_ligands (if given)
    Returns dict with atom counts + the ligand centroid (if a ligand kept).
    """
    keep_ligands = set(x.upper() for x in (keep_ligands or []))
    if keep_chain is None:
        counts = chain_residue_counts(pdb_path)
        keep_chain = max(counts, key=counts.get) if counts else "A"

    prot_atoms = 0
    het_atoms = 0
    lig_xyz = []
    out_lines = []
    with open(pdb_path) as fh:
        for line in fh:
            rec = line[0:6].strip()
            if rec == "ATOM":
                if line[21] == keep_chain:
                    out_lines.append(line)
                    prot_atoms += 1
            elif rec == "HETATM":
                resname = line[17:20].strip().upper()
                if drop_water and resname in ("HOH", "DOD"):
                    continue
                if line[21] != keep_chain:
                    continue
                if keep_ligands and resname not in keep_ligands:
                    continue
                if keep_ligands:  # only keep explicitly requested ligands
                    out_lines.append(line)
                    het_atoms += 1
                    try:
                        lig_xyz.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
                    except ValueError:
                        pass
    out_lines.append("TER\n")
    out_lines.append("END\n")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        fh.writelines(out_lines)

    centroid = None
    if lig_xyz:
        n = len(lig_xyz)
        centroid = [round(sum(c[i] for c in lig_xyz) / n, 3) for i in range(3)]
    return {"out": out_path, "chain": keep_chain, "protein_atoms": prot_atoms,
            "hetero_atoms": het_atoms, "ligand_centroid": centroid, "n_ligand_atoms": len(lig_xyz)}


# ------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uniprot", help="UniProt accession to search RCSB for")
    ap.add_argument("--pdb", help="force a specific PDB id (skip search)")
    ap.add_argument("--outdir", default="/workspace/struct")
    ap.add_argument("--top", type=int, default=8, help="max RCSB candidates to rank")
    ap.add_argument("--alphafold-fallback", action="store_true", default=True)
    # cleaning mode
    ap.add_argument("--clean", help="path to a PDB to clean for fpocket")
    ap.add_argument("--chain", default=None)
    ap.add_argument("--keep-ligand", action="append", default=None)
    ap.add_argument("--clean-out", default=None)
    ap.add_argument("--out", default=None, help="JSON summary output path")
    args = ap.parse_args()

    # ---- cleaning-only mode
    if args.clean:
        info = clean_pdb(args.clean, args.clean_out or args.clean.replace(".pdb", "_clean.pdb"),
                         keep_chain=args.chain, keep_ligands=args.keep_ligand)
        print(json.dumps(info, indent=2))
        return info

    result = {"uniprot": args.uniprot, "source": None, "chosen": None, "candidates": [],
              "alphafold": None}

    if args.pdb:
        has_drug, ligs, text = entry_ligands(args.pdb)
        path = os.path.join(args.outdir, f"{args.pdb.lower()}.pdb")
        os.makedirs(args.outdir, exist_ok=True)
        if text:
            with open(path, "w") as fh:
                fh.write(text)
        else:
            path = download_pdb(args.pdb, args.outdir)
        result.update(source="experimental_forced",
                      chosen={"pdb": args.pdb, "path": path,
                              "has_drug_like_ligand": has_drug, "ligands": ligs})
        print(f"[structure] forced PDB {args.pdb} -> {path} (drug-like ligand: {has_drug}; {ligs})")
    elif args.uniprot:
        cands = []
        try:
            cands = rank_candidates(uniprot_to_pdbs(args.uniprot)[:max(args.top, 20)],
                                    args.outdir)[:args.top]
        except Exception as e:
            print(f"[structure] RCSB/UniProt lookup failed: {e}", file=sys.stderr)
        result["candidates"] = cands
        if cands:
            best = cands[0]
            path = best.get("path") or download_pdb(best["pdb"], args.outdir)
            best["path"] = path
            result.update(source="experimental", chosen=best)
            # also grab a representative apo (no drug-like ligand) for contrast, if available
            apo = next((c for c in cands if not c["has_drug_like_ligand"]), None)
            if apo and apo["pdb"] != best["pdb"]:
                apo["path"] = apo.get("path") or download_pdb(apo["pdb"], args.outdir)
                result["apo_contrast"] = apo
            print(f"[structure] best experimental: {best['pdb']} "
                  f"(res={best['resolution']}, drug-like ligand={best['has_drug_like_ligand']}, "
                  f"ligands={best['ligands']}) -> {path}")
        elif args.alphafold_fallback:
            af = download_alphafold(args.uniprot, args.outdir)
            if af:
                result.update(source="alphafold", chosen={"path": af, "model": "AlphaFold"})
                print(f"[structure] no experimental structure; AlphaFold model -> {af} "
                      f"(NOTE: predicted model, pocket scores less reliable)")
            else:
                result.update(source=None)
                print("[structure] no experimental structure and no AlphaFold model found; "
                      "pocket step will be skipped.")
    else:
        ap.error("provide --uniprot or --pdb (or --clean for cleaning mode)")

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"[done] wrote {args.out}")
    return result


if __name__ == "__main__":
    main()
