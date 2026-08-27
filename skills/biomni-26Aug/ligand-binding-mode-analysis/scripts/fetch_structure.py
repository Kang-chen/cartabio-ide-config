"""
Fetch or load a protein-ligand co-crystal structure.

Three input modes:
  1. Explicit PDB ID   -> download coordinates + metadata from RCSB PDB.
  2. Local file        -> load a user-supplied .pdb / .cif / .ent (optionally .gz).
  3. Target + ligand   -> query the RCSB PDB search API for co-crystals and rank
                          candidates by resolution + ligand presence (see rank_cocrystals()).

RCSB PDB is one of Biomni's 17 queryable databases. This module talks to the
public RCSB REST + search endpoints directly so the skill has no hidden
dependency on a specific Biomni wrapper.

Endpoints
---------
Coordinates : https://files.rcsb.org/download/{PDB}.pdb   (or .cif)
Entry meta  : https://data.rcsb.org/rest/v1/core/entry/{PDB}
Ligand meta : https://data.rcsb.org/rest/v1/core/chemcomp/{CODE}
Search      : https://search.rcsb.org/rcsbsearch/v2/query
"""

import gzip
import json
import os
import time
import urllib.parse
import urllib.request

RCSB_FILE = "https://files.rcsb.org/download/{pdb}.{ext}"
RCSB_ENTRY = "https://data.rcsb.org/rest/v1/core/entry/{pdb}"
RCSB_CHEMCOMP = "https://data.rcsb.org/rest/v1/core/chemcomp/{code}"
RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"

_HEADERS = {"User-Agent": "biomni-binding-pocket-skill/1.0"}


def _get(url, timeout=60, retries=3):
    """HTTP GET with small retry loop. Returns bytes."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url}\n{last}")


def _post_json(url, payload, timeout=60, retries=3):
    data = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=data, headers={**_HEADERS, "Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"POST failed after {retries} tries: {url}\n{last}")


def fetch_pdb(pdb_id, out_dir="pocket_analysis/structures", fmt="pdb"):
    """
    Download a structure file from RCSB.

    Parameters
    ----------
    pdb_id : str   4-character PDB accession (case-insensitive).
    out_dir : str  Directory to save the file.
    fmt : str      'pdb' (default) or 'cif'.

    Returns
    -------
    dict with keys: pdb_id, path, format
    """
    pdb_id = pdb_id.strip().upper()
    if len(pdb_id) != 4:
        raise ValueError(f"PDB ID must be 4 characters, got {pdb_id!r}")
    os.makedirs(out_dir, exist_ok=True)
    ext = "cif" if fmt.lower() == "cif" else "pdb"
    url = RCSB_FILE.format(pdb=pdb_id, ext=ext)
    raw = _get(url)
    path = os.path.join(out_dir, f"{pdb_id}.{ext}")
    with open(path, "wb") as fh:
        fh.write(raw)
    print(f"[OK] downloaded {pdb_id} -> {path} ({len(raw):,} bytes)")
    return {"pdb_id": pdb_id, "path": path, "format": ext}


def load_local_structure(path, out_dir="pocket_analysis/structures"):
    """
    Copy/normalize a local structure file into the working directory.

    Handles .pdb, .cif, .ent and their .gz variants. Returns the same dict
    shape as fetch_pdb() so downstream code is input-agnostic.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(path)
    is_gz = base.endswith(".gz")
    stem = base[:-3] if is_gz else base
    ext = "cif" if stem.lower().endswith((".cif", ".mmcif")) else "pdb"
    pdb_id = os.path.splitext(stem)[0].upper()[:8]
    out_path = os.path.join(out_dir, f"{pdb_id}.{ext}")
    raw = gzip.open(path, "rb").read() if is_gz else open(path, "rb").read()
    with open(out_path, "wb") as fh:
        fh.write(raw)
    print(f"[OK] loaded local structure {path} -> {out_path} ({len(raw):,} bytes)")
    return {"pdb_id": pdb_id, "path": out_path, "format": ext}


def get_entry_metadata(pdb_id):
    """Return a compact dict of entry-level metadata from RCSB (best-effort)."""
    pdb_id = pdb_id.strip().upper()
    try:
        j = json.loads(_get(RCSB_ENTRY.format(pdb=pdb_id)).decode())
    except Exception as e:  # noqa: BLE001
        print(f"[warn] could not fetch metadata for {pdb_id}: {e}")
        return {"pdb_id": pdb_id}
    info = j.get("struct", {}) or {}
    exptl = (j.get("exptl") or [{}])[0]
    res = None
    for key in ("rcsb_entry_info",):
        d = j.get(key, {}) or {}
        if d.get("resolution_combined"):
            res = d["resolution_combined"][0]
    org = None
    try:
        org = (j.get("rcsb_entry_container_identifiers") or {}).get("polymer_entity_ids")
    except Exception:  # noqa: BLE001
        pass
    return {
        "pdb_id": pdb_id,
        "title": info.get("title"),
        "method": exptl.get("method"),
        "resolution_A": res,
        "deposited": (j.get("rcsb_accession_info") or {}).get("deposit_date"),
        "polymer_entities": org,
    }


def get_ligand_metadata(code):
    """Return chem-comp metadata (name, formula, weight, SMILES) for a ligand code."""
    code = code.strip().upper()
    try:
        j = json.loads(_get(RCSB_CHEMCOMP.format(code=code)).decode())
    except Exception as e:  # noqa: BLE001
        print(f"[warn] could not fetch ligand metadata for {code}: {e}")
        return {"code": code}
    cc = j.get("chem_comp", {}) or {}
    smiles = None
    for d in j.get("rcsb_chem_comp_descriptor", {}) or {}:
        pass
    desc = j.get("rcsb_chem_comp_descriptor", {}) or {}
    smiles = desc.get("SMILES_stereo") or desc.get("SMILES")
    return {
        "code": code,
        "name": cc.get("name"),
        "formula": cc.get("formula"),
        "formula_weight": cc.get("formula_weight"),
        "type": cc.get("type"),
        "smiles": smiles,
    }


def rank_cocrystals(target_query, ligand_code=None, max_hits=25):
    """
    Search RCSB for co-crystal structures of a target (optionally with a specific
    ligand) and rank candidates. Returns a list of dicts sorted best-first.

    Ranking favours: has the requested ligand > better (lower) resolution > X-ray.

    Parameters
    ----------
    target_query : str
        Free-text target (e.g. "ABL1 kinase", "HIV-1 protease", "P00533").
    ligand_code : str or None
        Restrict to entries containing this ligand chem-comp id (e.g. "STI").
    max_hits : int
        Max candidates to return.
    """
    nodes = [
        {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": target_query},
        }
    ]
    if ligand_code:
        nodes.append(
            {
                "type": "terminal",
                "service": "text_chem",
                "parameters": {
                    "attribute": "rcsb_chem_comp_container_identifiers.comp_id",
                    "operator": "exact_match",
                    "value": ligand_code.strip().upper(),
                },
            }
        )
    query = {
        "query": {"type": "group", "logical_operator": "and", "nodes": nodes}
        if len(nodes) > 1
        else nodes[0],
        "return_type": "entry",
        "request_options": {
            "paginate": {"start": 0, "rows": max_hits},
            "results_content_type": ["experimental"],
            "sort": [{"sort_by": "rcsb_entry_info.resolution_combined", "direction": "asc"}],
        },
    }
    try:
        j = _post_json(RCSB_SEARCH, query)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] RCSB search failed: {e}")
        return []
    hits = []
    for row in j.get("result_set", []):
        pdb_id = row.get("identifier")
        if not pdb_id:
            continue
        meta = get_entry_metadata(pdb_id)
        hits.append(
            {
                "pdb_id": pdb_id,
                "resolution_A": meta.get("resolution_A"),
                "method": meta.get("method"),
                "title": meta.get("title"),
                "score": row.get("score"),
            }
        )
    hits.sort(key=lambda d: (d["resolution_A"] is None, d["resolution_A"] or 1e9))
    return hits


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(json.dumps(fetch_pdb(sys.argv[1]), indent=2))
