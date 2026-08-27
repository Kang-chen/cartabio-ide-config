"""
Build a commercially-permissive ADMET percentile reference set (ChEMBL approved drugs).

WHY THIS EXISTS
---------------
ADMET-AI ships a DrugBank-derived reference distribution
(``admet_ai/resources/data/drugbank_approved.csv``) that it uses to compute the
``*_drugbank_approved_percentile`` columns. The full DrugBank dataset is licensed
**CC BY-NC** (no commercial use). This script regenerates an equivalent reference
distribution from a **commercially-permissive source** — ChEMBL approved drugs
(``max_phase = 4``), licensed **CC BY-SA** — so the ADMET workflow is usable in a
commercial setting.

HOW IT WORKS (mirrors how the original DrugBank reference was produced)
----------------------------------------------------------------------
The reference "values" are **ADMET-AI model predictions**, not experimental data.
So we:
  1. Fetch approved drugs from the ChEMBL REST API (``max_phase = 4``): ChEMBL ID,
     preferred name, canonical SMILES, molecule type, ATC classifications.
  2. Filter to reference-appropriate small molecules and standardize each to its
     drug-like parent using this skill's own standardization (for consistency with
     how query molecules are processed), then de-duplicate by canonical SMILES.
  3. Run ADMET-AI ``predict()`` (with ``drugbank_path=None`` so no percentile
     columns are produced) to generate the 52 property/prediction columns.
  4. Assemble a CSV in the **exact DrugBank reference schema** so it is a drop-in
     replacement passable via ``ADMETModel(drugbank_path=...)``:
        metadata: smiles, name, id, atc, atc_name_1..4
        + 52 predicted property columns.
  5. Write ``assets/chembl_approved_reference.csv`` plus a provenance sidecar
     ``assets/chembl_approved_reference.meta.json``.

This is intended to be run **once** to bake the reference into the skill, so end
users need no network access or multi-minute rebuild at analysis time. It is kept
and documented so the reference is reproducible and updatable.

USAGE
-----
    python scripts/build_reference_set.py                # full build
    python scripts/build_reference_set.py --limit 200    # quick pilot (dev only)

Dependencies: ``requests`` (fetch) + ``rdkit`` + ``admet-ai`` (already required by
the skill for ADMET prediction). No new *runtime* dependency for end users — the
reference is pre-baked; ``requests`` is only needed to *rebuild* it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date

import pandas as pd
import requests

# --- Make the skill's own modules importable regardless of CWD ------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_THIS_DIR)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"
MImax = 1500  # upper MW bound for a reference small molecule
MImin = 100   # lower MW bound

# The 8 metadata columns that precede the 52 predicted property columns, in the
# exact order/naming used by the ADMET-AI DrugBank reference file.
META_COLUMNS = ["smiles", "name", "id", "atc", "atc_name_1", "atc_name_2", "atc_name_3", "atc_name_4"]


# ------------------------------------------------------------------------------
# 1. Fetch approved drugs from ChEMBL
# ------------------------------------------------------------------------------
def fetch_chembl_approved(limit: int | None = None, page_size: int = 200) -> pd.DataFrame:
    """Fetch ChEMBL approved drugs (max_phase=4) with SMILES + ATC classifications.

    Parameters
    ----------
    limit : int or None
        If set, stop after roughly this many *records fetched* (for a quick pilot).
        None (default) fetches the full approved set.
    page_size : int
        API page size.

    Returns
    -------
    DataFrame with columns: chembl_id, pref_name, canonical_smiles, molecule_type,
    atc_classifications (list).
    """
    print(f"Fetching ChEMBL approved drugs (max_phase=4) ...", flush=True)
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    rows: list[dict] = []
    url = f"{CHEMBL_BASE}/molecule"
    params = {
        "max_phase": 4,
        "limit": page_size,
        "offset": 0,
        "format": "json",
    }
    total = None
    while True:
        for attempt in range(4):
            try:
                r = session.get(url, params=params, timeout=90)
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                wait = 2 ** attempt
                print(f"   request failed ({e}); retrying in {wait}s", flush=True)
                time.sleep(wait)
        d = r.json()
        if total is None:
            total = d["page_meta"]["total_count"]
            print(f"   total approved records reported: {total}", flush=True)
        for m in d["molecules"]:
            ms = m.get("molecule_structures") or {}
            rows.append({
                "chembl_id": m.get("molecule_chembl_id"),
                "pref_name": m.get("pref_name"),
                "canonical_smiles": ms.get("canonical_smiles"),
                "molecule_type": m.get("molecule_type"),
                "atc_classifications": m.get("atc_classifications") or [],
            })
        got = len(rows)
        print(f"   fetched {got}/{total}", flush=True)
        if limit is not None and got >= limit:
            break
        nxt = d["page_meta"].get("next")
        if not nxt:
            break
        # next is a relative path incl. querystring; reset params so requests uses it verbatim
        url = "https://www.ebi.ac.uk" + nxt
        params = None

    df = pd.DataFrame(rows)
    print(f"   -> {len(df)} raw records", flush=True)
    return df


def fetch_atc_vocabulary() -> dict[str, tuple]:
    """Fetch the full ATC vocabulary once; return level5_code -> (l1,l2,l3,l4 descriptions)."""
    print("Fetching ATC vocabulary (level descriptions) ...", flush=True)
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    lookup: dict[str, tuple] = {}
    url = f"{CHEMBL_BASE}/atc_class"
    params = {"limit": 1000, "offset": 0, "format": "json"}
    total = None
    while True:
        r = session.get(url, params=params, timeout=90)
        r.raise_for_status()
        d = r.json()
        if total is None:
            total = d["page_meta"]["total_count"]
        for a in d["atc"]:
            lvl5 = a.get("level5")
            if lvl5:
                lookup[lvl5] = (
                    a.get("level1_description"),
                    a.get("level2_description"),
                    a.get("level3_description"),
                    a.get("level4_description"),
                )
        nxt = d["page_meta"].get("next")
        if not nxt:
            break
        url = "https://www.ebi.ac.uk" + nxt
        params = None
    print(f"   -> {len(lookup)} ATC level5 entries", flush=True)
    return lookup


# ------------------------------------------------------------------------------
# 2. Filter + standardize small molecules
# ------------------------------------------------------------------------------
def filter_and_standardize(df: pd.DataFrame) -> pd.DataFrame:
    """Keep small molecules with a valid SMILES; standardize to parent; de-duplicate.

    Reuses the skill's own standardization so reference molecules are processed
    identically to query molecules.
    """
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    # Reuse the skill's own standardization (desalt/neutralize/canonicalize) so
    # reference molecules are processed identically to query molecules.
    # _standardize_one(raw) -> (parent_smiles_or_None, note)
    from compute_properties import _standardize_one  # type: ignore

    print("Filtering + standardizing reference molecules ...", flush=True)
    before = len(df)
    df = df[df["molecule_type"] == "Small molecule"].copy()
    df = df[df["canonical_smiles"].notna()].copy()
    print(f"   small molecules with SMILES: {len(df)}/{before}", flush=True)

    std_smiles = []
    keep = []
    for smi in df["canonical_smiles"]:
        try:
            parent, _note = _standardize_one(smi)
        except Exception:
            parent = None
        if not parent:
            keep.append(False); std_smiles.append(None); continue
        mol = Chem.MolFromSmiles(parent)
        if mol is None:
            keep.append(False); std_smiles.append(None); continue
        mw = Descriptors.MolWt(mol)
        if mw < MImin or mw > MImax:
            keep.append(False); std_smiles.append(None); continue
        keep.append(True); std_smiles.append(Chem.MolToSmiles(mol))

    df["std_smiles"] = std_smiles
    df = df[pd.Series(keep, index=df.index)].copy()
    print(f"   after standardization + MW[{MImin},{MImax}] filter: {len(df)}", flush=True)

    # De-duplicate by standardized canonical SMILES (keep first / lowest ChEMBL id)
    df = df.sort_values("chembl_id").drop_duplicates(subset="std_smiles", keep="first")
    print(f"   after de-duplication by canonical SMILES: {len(df)}", flush=True)
    return df


# ------------------------------------------------------------------------------
# 3. Build ATC metadata columns
# ------------------------------------------------------------------------------
def build_atc_columns(atc_lists: list[list[str]], atc_vocab: dict[str, tuple]) -> pd.DataFrame:
    """Produce atc, atc_name_1..4 columns matching the DrugBank reference format.

    - atc: ';'-joined unique 5-char ATC subgroup codes (level4 code, e.g. 'C02CA')
    - atc_name_1..4: ';'-joined unique level descriptions across the drug's ATC codes
    """
    rows = []
    for codes in atc_lists:
        codes = [c for c in (codes or []) if c]
        if not codes:
            rows.append({c: None for c in ["atc", "atc_name_1", "atc_name_2", "atc_name_3", "atc_name_4"]})
            continue
        sub5 = []   # 5-char subgroup codes
        l1, l2, l3, l4 = [], [], [], []
        for full in codes:
            sub5.append(full[:5])
            names = atc_vocab.get(full)
            if names:
                if names[0]: l1.append(names[0])
                if names[1]: l2.append(names[1])
                if names[2]: l3.append(names[2])
                if names[3]: l4.append(names[3])
        def _join(xs):
            seen = list(dict.fromkeys(xs))  # unique, order-preserving
            return ";".join(seen) if seen else None
        rows.append({
            "atc": _join(sub5),
            "atc_name_1": _join(l1),
            "atc_name_2": _join(l2),
            "atc_name_3": _join(l3),
            "atc_name_4": _join(l4),
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------
# 4. Run ADMET-AI predictions to generate the 52 property columns
# ------------------------------------------------------------------------------
def predict_reference_properties(smiles: list[str]) -> pd.DataFrame:
    """Run ADMET-AI predict() (no percentile columns) -> 52 property columns."""
    from admet_ai import ADMETModel
    print(f"Running ADMET-AI predictions for {len(smiles)} reference molecules ...", flush=True)
    model = ADMETModel(drugbank_path=None)  # None => predict() returns only the 52 property columns
    preds = model.predict(smiles=list(smiles))
    preds = preds.reset_index(drop=True)
    print(f"   -> {preds.shape[1]} property columns", flush=True)
    return preds


# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Build ChEMBL approved-drug ADMET percentile reference.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Fetch only ~N ChEMBL records (quick pilot; dev only).")
    ap.add_argument("--out-dir", default=os.path.join(_SKILL_DIR, "assets"),
                    help="Output directory for the reference CSV + meta.")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    out_csv = os.path.join(args.out_dir, "chembl_approved_reference.csv")
    out_meta = os.path.join(args.out_dir, "chembl_approved_reference.meta.json")

    raw = fetch_chembl_approved(limit=args.limit)
    atc_vocab = fetch_atc_vocabulary()
    filt = filter_and_standardize(raw)

    # Predict properties on the standardized parents
    preds = predict_reference_properties(filt["std_smiles"].tolist())

    # Assemble metadata (align to the predicted rows, in order)
    atc_cols = build_atc_columns(filt["atc_classifications"].tolist(), atc_vocab).reset_index(drop=True)
    meta_df = pd.DataFrame({
        "smiles": filt["std_smiles"].tolist(),
        "name": filt["pref_name"].tolist(),
        "id": filt["chembl_id"].tolist(),
    })
    meta_df = pd.concat([meta_df, atc_cols], axis=1)
    meta_df = meta_df[["smiles", "name", "id", "atc", "atc_name_1", "atc_name_2", "atc_name_3", "atc_name_4"]]

    # Final reference: metadata + 52 property columns (in the DrugBank schema order)
    reference = pd.concat([meta_df, preds], axis=1)

    # Persist. CSV is safe to write directly to /mnt/results.
    reference.to_csv(out_csv, index=False)

    # ADMET-AI version for provenance
    try:
        import admet_ai
        admet_ai_version = getattr(admet_ai, "__version__", "unknown")
    except Exception:
        admet_ai_version = "unknown"

    meta = {
        "source": "ChEMBL",
        "source_url": "https://www.ebi.ac.uk/chembl/",
        "query": "molecule endpoint, max_phase=4 (approved), molecule_type='Small molecule'",
        "license": "CC BY-SA 3.0 (ChEMBL data)",
        "attribution": "ChEMBL database, European Bioinformatics Institute (EMBL-EBI).",
        "reference_values": "ADMET-AI model predictions on the ChEMBL approved-drug SMILES set "
                            "(the reference distribution used for percentile scoring).",
        "admet_ai_version": admet_ai_version,
        "n_molecules": int(len(reference)),
        "n_property_columns": int(preds.shape[1]),
        "date_built": date.today().isoformat(),
        "replaces": "admet_ai/resources/data/drugbank_approved.csv (CC BY-NC)",
        "percentile_column_suffix": "chembl_approved_percentile",
    }
    with open(out_meta, "w") as fh:
        json.dump(meta, fh, indent=2)

    print("\n=== Reference build complete ===")
    print(f"   rows: {len(reference)} | columns: {reference.shape[1]} "
          f"(8 metadata + {preds.shape[1]} property)")
    print(f"   CSV : {out_csv}")
    print(f"   META: {out_meta}")


if __name__ == "__main__":
    main()
