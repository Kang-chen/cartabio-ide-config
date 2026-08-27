#!/usr/bin/env python3
"""
build_panel.py — Build the off-target prediction panel:
  (1) fixed core safety panel (Bowes-style; references/core_panel.csv), ALWAYS included
  (2) OPTIONAL adaptive expansion by nearest-neighbor target annotation (structure-only, de novo):
        - kNN of the query in ECFP4 space against a pool of ChEMBL actives (QUERY EXCLUDED),
        - union of targets those neighbors hit,
        - add only NOVEL single-protein human targets to the core.
      This is genuinely de novo (works with zero measured data for the query) and NOT circular
      (it does not use the query's own measured targets to build the panel).

Also fetches UniProt sequences for every panel target (needed by DeepPurpose DTI).

IMPORTANT: The query compound's OWN measured off-targets are handled separately by
fetch_ground_truth.py — they are ground truth, never part of the prediction panel.

Usage:
  python build_panel.py --smiles "<canonical>" --inchikey14 <14char> \
        --core references/core_panel.csv --outdir <dir> \
        [--adaptive] [--knn 25] [--max-added 25]

Outputs:
  <outdir>/data/prediction_panel.csv           (uniprot,label,target_class,chembl_target_id,source)
  <outdir>/tmp/panel_with_seqs.csv             (+ sequence column for DeepPurpose)
"""
import argparse, os, sys, time, json
import requests
import pandas as pd
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs
from rdkit import RDLogger
RDLogger.DisableLog("rdApp.*")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_qc import normalize_prefname

CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"
UNIPROT = "https://rest.uniprot.org/uniprotkb"


def _get(url, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=45,
                             headers={"Accept": "application/json"})
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def fp(smiles):
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        return None
    return AllChem.GetMorganFingerprintAsBitVect(m, radius=2, nBits=2048)


def uniprot_fasta_seq(acc):
    try:
        r = requests.get(f"{UNIPROT}/{acc}.fasta", timeout=30)
        if r.status_code == 200 and r.text.startswith(">"):
            return "".join(r.text.splitlines()[1:])
    except Exception:
        pass
    return None


def resolve_target_meta(chembl_target_id):
    """Return (uniprot, pref_name) for a ChEMBL target id (single-protein only)."""
    j = _get(f"{CHEMBL}/target/{chembl_target_id}.json")
    if not j or j.get("target_type") != "SINGLE PROTEIN":
        return None, None
    for comp in j.get("target_components", []):
        acc = comp.get("accession")
        if acc:
            return acc, j.get("pref_name")
    return None, j.get("pref_name")


def adaptive_expand(query_smiles, query_ik14, knn, max_added, existing_uniprots,
                    primary_uniprots=None, primary_prefs=None):
    """
    Nearest-neighbor target annotation. We approximate a global kNN by sampling ChEMBL
    similarity via the ChEMBL similarity endpoint (Tanimoto), then map neighbor molecules
    to their targets. Query is excluded by InChIKey-14 and canonical SMILES.

    The compound's intended primary target and its orthologs (matched by UniProt or by
    normalized ChEMBL pref_name) are NEVER added as off-targets — otherwise the ligand-
    similarity engine would score them ~1.0 by construction (they were added *because* similar
    molecules hit them), manufacturing false "independent" evidence.
    """
    primary_uniprots = set(primary_uniprots or [])
    primary_prefs = set(primary_prefs or [])
    qfp = fp(query_smiles)
    if qfp is None:
        return []
    # ChEMBL similarity search: molecules similar to the query SMILES (>=70% Tanimoto)
    # endpoint: /similarity/{smiles}/{threshold}
    from urllib.parse import quote
    sim_url = f"{CHEMBL}/similarity/{quote(query_smiles)}/70.json"
    j = _get(sim_url, params={"limit": knn})
    neighbors = (j or {}).get("molecules", []) if j else []
    neigh_ids = []
    for m in neighbors:
        cid = m.get("molecule_chembl_id")
        ik = None
        try:
            ik = m.get("molecule_structures", {}).get("standard_inchi_key", "")
        except Exception:
            ik = ""
        if cid and (not ik or ik[:14] != query_ik14):
            neigh_ids.append(cid)
    # map neighbor molecules -> targets they are active against (<=1uM)
    target_counts = {}
    for cid in neigh_ids[:knn]:
        act = _get(f"{CHEMBL}/activity.json",
                   params={"molecule_chembl_id": cid, "pchembl_value__gte": 6,
                           "limit": 200})
        for a in (act or {}).get("activities", []):
            tid = a.get("target_chembl_id")
            if tid:
                target_counts[tid] = target_counts.get(tid, 0) + 1
    # rank candidate targets by neighbor support, resolve to single-protein human
    added = []
    for tid, _cnt in sorted(target_counts.items(), key=lambda x: x[1], reverse=True):
        if len(added) >= max_added:
            break
        acc, pref = resolve_target_meta(tid)
        if not acc or acc in existing_uniprots:
            continue
        # never add the intended primary target or its orthologs as an off-target
        if acc in primary_uniprots or (pref and normalize_prefname(pref) in primary_prefs):
            continue
        existing_uniprots.add(acc)
        added.append({"uniprot": acc, "label": (pref or tid)[:40],
                      "chembl_pref_name": pref, "target_class": "Adaptive (kNN)",
                      "chembl_target_id": tid, "source": "adaptive"})
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smiles", required=True, help="query canonical SMILES")
    ap.add_argument("--inchikey14", required=True, help="query InChIKey first-14 block")
    ap.add_argument("--core", required=True, help="path to core_panel.csv")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--adaptive", action="store_true", help="enable kNN panel expansion")
    ap.add_argument("--knn", type=int, default=25)
    ap.add_argument("--max-added", type=int, default=25)
    ap.add_argument("--compound-json", default=None,
                    help="resolve_compound output; supplies the intended primary target(s) so "
                         "they (and orthologs) are kept out of the off-target panel and are "
                         "scored separately for an on-target sanity check")
    args = ap.parse_args()

    os.makedirs(f"{args.outdir}/data", exist_ok=True)
    os.makedirs(f"{args.outdir}/tmp", exist_ok=True)

    # intended primary target(s) from resolve_compound (optional)
    primary_targets, primary_uniprots, primary_prefs = [], set(), set()
    if args.compound_json and os.path.exists(args.compound_json):
        comp = json.load(open(args.compound_json))
        primary_targets = comp.get("primary_targets", []) or []
        primary_uniprots = set(comp.get("primary_uniprots", []) or [])
        primary_prefs = {normalize_prefname(p) for p in comp.get("primary_pref_names", []) or []}

    core = pd.read_csv(args.core)
    core["source"] = "core"
    panel = core.copy()
    existing = set(core["uniprot"].dropna().tolist())

    added = []
    if args.adaptive:
        try:
            added = adaptive_expand(args.smiles, args.inchikey14, args.knn,
                                    args.max_added, existing,
                                    primary_uniprots=primary_uniprots,
                                    primary_prefs=primary_prefs)
        except Exception as e:
            print(f"[warn] adaptive expansion failed: {e}", file=sys.stderr)
    if added:
        panel = pd.concat([panel, pd.DataFrame(added)], ignore_index=True)

    # Add the intended PRIMARY target(s) as explicitly-tagged rows (source="primary"). They are
    # scored like any panel target so we can report on-target recovery as a sanity check, but
    # they are excluded from every off-target count/metric downstream.
    prim_rows = []
    for t in primary_targets:
        tid = t.get("chembl_target_id")
        pref = t.get("pref_name")
        for acc in (t.get("uniprots") or []):
            if acc in existing:
                continue
            existing.add(acc)
            prim_rows.append({"uniprot": acc, "label": (pref or tid or acc)[:40],
                              "chembl_pref_name": pref,
                              "target_class": "Primary target (on-target)",
                              "chembl_target_id": tid, "source": "primary"})
    if prim_rows:
        panel = pd.concat([panel, pd.DataFrame(prim_rows)], ignore_index=True)

    # is_primary flag: primary rows, plus any core/adaptive row that IS the primary target or an
    # ortholog of it (UniProt or normalized pref_name match).
    def _is_primary(row):
        if row.get("source") == "primary":
            return True
        if row.get("uniprot") in primary_uniprots:
            return True
        key = normalize_prefname(row.get("chembl_pref_name") or row.get("label"))
        return bool(key) and key in primary_prefs
    panel["is_primary"] = panel.apply(_is_primary, axis=1)

    # fetch UniProt sequences for DeepPurpose
    seqs = []
    for acc in panel["uniprot"]:
        s = uniprot_fasta_seq(acc) if isinstance(acc, str) else None
        seqs.append(s)
    panel_seq = panel.copy()
    panel_seq["sequence"] = seqs
    n_noseq = panel_seq["sequence"].isna().sum()

    panel.to_csv(f"{args.outdir}/data/prediction_panel.csv", index=False)
    # DeepPurpose needs a sequence; drop rows without one for that file only
    panel_seq.dropna(subset=["sequence"]).to_csv(
        f"{args.outdir}/tmp/panel_with_seqs.csv", index=False)

    print(json.dumps({
        "status": "ok",
        "n_core": int((panel["source"] == "core").sum()),
        "n_adaptive": int((panel["source"] == "adaptive").sum()),
        "n_primary": int((panel["source"] == "primary").sum()),
        "n_total": int(len(panel)),
        "n_missing_seq": int(n_noseq),
        "primary_targets": [t.get("pref_name") for t in primary_targets],
        "classes": panel["target_class"].value_counts().to_dict(),
    }, default=str))


if __name__ == "__main__":
    main()
