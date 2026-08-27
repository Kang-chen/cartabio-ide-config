#!/usr/bin/env python3
"""
resolve_compound.py — Resolve any small-molecule identifier to a standardized SMILES
and compute RDKit physicochemical descriptors.

Accepts: compound name, SMILES, InChIKey, or ChEMBL ID.
Strategy:
  1. If input looks like a ChEMBL ID -> ChEMBL molecule/{id}.
  2. If input parses as SMILES in RDKit -> use directly (also try ChEMBL match for metadata).
  3. Else treat as name/InChIKey -> ChEMBL molecule search.
Outputs a one-row compound_summary.csv and prints a JSON blob to stdout.

Usage:
  python resolve_compound.py --query "astemizole" --outdir /mnt/results/astemizole_offtarget
  python resolve_compound.py --query "COc1ccc(...)" --name astemizole --outdir <dir>

No secrets, no network beyond ChEMBL REST. Fails loudly if the compound cannot be resolved.
"""
import argparse, json, os, sys, time
import urllib.parse
import requests
import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors, Crippen, Lipinski, QED, AllChem, Draw
from rdkit import RDLogger
RDLogger.DisableLog("rdApp.*")

CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"


def _get(url, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30,
                             headers={"Accept": "application/json"})
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def looks_like_chembl_id(q):
    return q.upper().startswith("CHEMBL") and q[6:].isdigit()


def looks_like_inchikey(q):
    parts = q.strip().split("-")
    return len(parts) == 3 and len(parts[0]) == 14 and q.replace("-", "").isalnum()


def resolve_via_chembl_id(cid):
    j = _get(f"{CHEMBL}/molecule/{cid.upper()}.json")
    if not j:
        return None
    return j


def resolve_via_search(query):
    """ChEMBL molecule search by name / synonym / InChIKey."""
    # exact InChIKey lookup first
    if looks_like_inchikey(query):
        j = _get(f"{CHEMBL}/molecule.json",
                 params={"molecule_structures__standard_inchi_key": query.strip(), "limit": 1})
        if j and j.get("molecules"):
            return j["molecules"][0]
    # name / synonym search
    j = _get(f"{CHEMBL}/molecule/search.json", params={"q": query, "limit": 5})
    if j and j.get("molecules"):
        # prefer highest max_phase (approved drugs) then first
        def _phase(m):
            v = m.get("max_phase")
            try:
                return float(v)
            except (TypeError, ValueError):
                return -1.0
        mols = sorted(j["molecules"], key=_phase, reverse=True)
        return mols[0]
    return None


def smiles_from_mol_record(rec):
    try:
        return rec.get("molecule_structures", {}).get("canonical_smiles")
    except Exception:
        return None


def target_components(tid):
    """Return (uniprot_accessions, pref_name, target_type) for a ChEMBL target id."""
    j = _get(f"{CHEMBL}/target/{tid}.json")
    accs, pref, ttype = [], None, None
    if j:
        ttype = j.get("target_type")
        pref = j.get("pref_name")
        for comp in j.get("target_components", []):
            a = comp.get("accession")
            if a:
                accs.append(a)
    return accs, pref, ttype


def fetch_primary_targets(chembl_id):
    """Resolve the compound's INTENDED primary target(s) from ChEMBL *mechanism* annotations.

    This is what lets the pipeline separate on-target recovery from off-target recovery: the
    primary target (and its orthologs, matched downstream by pref_name) must not be scored as
    an off-target. Returns (targets, resolved_bool). `targets` is a list of dicts with
    chembl_target_id, uniprots, pref_name, target_type, action_type, moa.
    """
    out = []
    if not chembl_id:
        return out, False
    j = _get(f"{CHEMBL}/mechanism.json",
             params={"molecule_chembl_id": chembl_id, "limit": 100})
    mechs = (j or {}).get("mechanisms", []) if j else []
    seen = set()
    for mech in mechs:
        tid = mech.get("target_chembl_id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        accs, pref, ttype = target_components(tid)
        out.append({"chembl_target_id": tid, "uniprots": accs, "pref_name": pref,
                    "target_type": ttype, "action_type": mech.get("action_type"),
                    "moa": mech.get("mechanism_of_action")})
    return out, len(out) > 0


def compute_descriptors(smiles):
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"RDKit could not parse SMILES: {smiles}")
    # standardize: canonical SMILES (largest fragment / desalt is light here)
    frags = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True)
    if len(frags) > 1:
        mol = max(frags, key=lambda m: m.GetNumHeavyAtoms())
    can = Chem.MolToSmiles(mol)
    _ik = Chem.MolToInchiKey(mol)
    d = {
        "canonical_smiles": can,
        "smiles": can,               # alias for convenience/robustness
        "inchikey": _ik,
        "inchikey14": _ik[:14],      # first-14 block used for leave-query-out
        "MW": round(Descriptors.MolWt(mol), 2),
        "LogP": round(Crippen.MolLogP(mol), 2),
        "TPSA": round(Descriptors.TPSA(mol), 2),
        "HBD": Lipinski.NumHDonors(mol),
        "HBA": Lipinski.NumHAcceptors(mol),
        "RotB": Descriptors.NumRotatableBonds(mol),
        "AromaticRings": Descriptors.NumAromaticRings(mol),
        "FractionCSP3": round(Descriptors.FractionCSP3(mol), 3),
        "QED": round(QED.qed(mol), 3),
    }
    # Lipinski violations
    viol = 0
    if d["MW"] > 500: viol += 1
    if d["LogP"] > 5: viol += 1
    if d["HBD"] > 5: viol += 1
    if d["HBA"] > 10: viol += 1
    d["Lipinski_Violations"] = viol
    return mol, d


def draw_2d(mol, path):
    try:
        img = Draw.MolToImage(mol, size=(500, 400))
        img.save(path)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True, help="name | SMILES | InChIKey | ChEMBL ID")
    ap.add_argument("--name", default=None, help="display name (optional)")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    os.makedirs(f"{args.outdir}/data", exist_ok=True)
    os.makedirs(f"{args.outdir}/figures", exist_ok=True)

    q = args.query.strip()
    rec = None
    chembl_id = None
    display_name = args.name

    # 1) direct SMILES?
    smiles = None
    maybe_mol = Chem.MolFromSmiles(q)
    if maybe_mol is not None and (not looks_like_chembl_id(q)):
        smiles = q
        # try to get ChEMBL metadata by InChIKey for name/phase
        ik = Chem.MolToInchiKey(maybe_mol)
        j = _get(f"{CHEMBL}/molecule.json",
                 params={"molecule_structures__standard_inchi_key": ik, "limit": 1})
        if j and j.get("molecules"):
            rec = j["molecules"][0]
            chembl_id = rec.get("molecule_chembl_id")
    # 2) ChEMBL ID?
    if smiles is None and looks_like_chembl_id(q):
        rec = resolve_via_chembl_id(q)
        if rec:
            chembl_id = rec.get("molecule_chembl_id")
            smiles = smiles_from_mol_record(rec)
    # 3) name / InChIKey search
    if smiles is None:
        rec = resolve_via_search(q)
        if rec:
            chembl_id = rec.get("molecule_chembl_id")
            smiles = smiles_from_mol_record(rec)

    if smiles is None:
        print(json.dumps({"status": "error",
                          "message": f"Could not resolve compound from query: {q!r}"}))
        sys.exit(2)

    if display_name is None and rec is not None:
        display_name = rec.get("pref_name") or chembl_id or q
    if display_name is None:
        display_name = chembl_id or q

    mol, desc = compute_descriptors(smiles)

    max_phase = rec.get("max_phase") if rec else None
    withdrawn = rec.get("withdrawn_flag") if rec else None

    out = {
        "status": "ok",
        "name": display_name,
        "chembl_id": chembl_id,
        "max_phase": max_phase,
        "withdrawn": withdrawn,
        **desc,
    }

    # Resolve the INTENDED primary target(s) so downstream stages can keep on-target
    # recovery separate from off-target recovery (item: primary != off-target).
    prim, prim_ok = fetch_primary_targets(chembl_id)
    out["primary_targets"] = prim
    out["primary_uniprots"] = sorted({a for t in prim for a in t.get("uniprots", [])})
    out["primary_pref_names"] = sorted({t["pref_name"] for t in prim if t.get("pref_name")})
    out["primary_target_resolved"] = bool(prim_ok)
    # save summary + 2D
    row = {k: out[k] for k in
           ["name", "chembl_id", "canonical_smiles", "inchikey", "MW", "LogP", "TPSA",
            "HBD", "HBA", "RotB", "AromaticRings", "FractionCSP3", "QED",
            "Lipinski_Violations", "max_phase", "withdrawn"]}
    pd.DataFrame([row]).to_csv(f"{args.outdir}/data/compound_summary.csv", index=False)
    safe = "".join(c if c.isalnum() else "_" for c in str(display_name))[:40] or "compound"
    fig2d = f"{args.outdir}/figures/{safe}_2D.png"
    out["fig2d"] = fig2d if draw_2d(mol, fig2d) else None

    print(json.dumps(out, default=str))


if __name__ == "__main__":
    main()
