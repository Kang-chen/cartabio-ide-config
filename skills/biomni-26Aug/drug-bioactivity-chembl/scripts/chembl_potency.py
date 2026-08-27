#!/usr/bin/env python3
"""
chembl_potency.py  --  Compound-agnostic ChEMBL bioactivity mining, curation,
potency aggregation, and selectivity analysis.

Part of the `drug-bioactivity-chembl` Biomni skill. NOTHING here is specific to
any one compound or target family: the compound is resolved by name/ID, targets
are discovered from the data, and target tiering is driven by the data itself.

Typical use (compound-centric, the primary mode):

    from chembl_potency import (resolve_compound, fetch_activities, build_frame,
                                classify_assays, tier_targets, standard_filter,
                                aggregate, selectivity)

    mol = resolve_compound("imatinib")                 # -> dict w/ molecule_chembl_id
    acts = fetch_activities(mol["molecule_chembl_id"]) # paginated REST pull
    df   = build_frame(acts)                           # tidy DataFrame
    df   = classify_assays(df)                          # biochemical / cellular / antiprolif
    df   = tier_targets(df)                             # primary / offtarget / cellular (data-driven)
    clean, prov = standard_filter(df)                   # "Standard" QC + provenance
    agg  = aggregate(clean)                             # median + IQR + range + geomean per target x type
    sel  = selectivity(agg)                             # fold vs primary target(s)

Target-centric variant (1 target -> many compounds): call fetch_activities with
target_chembl_id=... instead of molecule id; build_frame/standard_filter/aggregate
work unchanged (group by molecule instead of target -- see aggregate(group_col=...)).

Requires only the standard library + pandas + numpy (all preinstalled). The
`chembl_webresource_client` package is NOT installed in this environment, so we
use the public ChEMBL REST API directly (validated).
"""
from __future__ import annotations
import json
import re
import time
import urllib.parse
import urllib.request
from typing import Optional

import numpy as np
import pandas as pd

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"
_UA = {"User-Agent": "Mozilla/5.0 (Biomni drug-bioactivity-chembl skill)"}

# Affinity/potency measurement types treated as "target potency" by default.
# EC50 / Potency are functional and only added when explicitly requested.
DEFAULT_ACTIVITY_TYPES = ["IC50", "Ki", "Kd", "Kd(app)"]

# Assay-context columns we keep for every record (assay provenance).
CONTEXT_COLS = [
    "activity_id", "assay_chembl_id", "assay_description", "assay_type",
    "bao_label", "target_chembl_id", "target_pref_name", "target_organism",
    "document_year", "document_journal", "document_chembl_id",
    "standard_type", "standard_relation", "standard_value", "standard_units",
    "pchembl_value", "data_validity_comment",
]


# --------------------------------------------------------------------------- #
# Low-level REST helpers
# --------------------------------------------------------------------------- #
def _get(url: str, retries: int = 4, backoff: float = 2.0) -> dict:
    """GET a ChEMBL REST URL, returning parsed JSON, with simple retries."""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - network robustness
            last = e
            time.sleep(backoff * (i + 1))
    raise RuntimeError(f"ChEMBL request failed after {retries} tries: {url}\n{last}")


def _paginate(url: str, key: str) -> list:
    """Follow ChEMBL `page_meta.next` links, concatenating `key` records."""
    out: list = []
    while url:
        d = _get(url)
        out.extend(d.get(key, []))
        nxt = d.get("page_meta", {}).get("next")
        url = ("https://www.ebi.ac.uk" + nxt) if nxt else None
    return out


# --------------------------------------------------------------------------- #
# 1. Resolve the compound  (name / synonym / ChEMBL ID / SMILES)
# --------------------------------------------------------------------------- #
def resolve_compound(query: str, smiles: Optional[str] = None,
                     prefer_max_phase: bool = True) -> dict:
    """
    Resolve a compound to a single ChEMBL molecule record.

    `query` may be a ChEMBL ID (CHEMBLxxxx), a preferred name, or a synonym.
    If `smiles` is given, a structure (flexmatch) search is used as a fallback.

    Returns a dict with molecule_chembl_id, pref_name, max_phase, and a list of
    ALL candidate hits under 'candidates' so the caller can disambiguate and
    EXCLUDE close analogues (e.g. do not confuse a drug with its back-up analogue).

    Raises if nothing resolves.
    """
    hits: list[dict] = []

    # (a) direct ChEMBL ID
    if re.fullmatch(r"CHEMBL\d+", query.strip(), re.I):
        cid = query.strip().upper()
        d = _get(f"{CHEMBL_BASE}/molecule/{cid}.json")
        if d and d.get("molecule_chembl_id"):
            hits = [d]

    # (b) name / synonym search
    if not hits:
        q = urllib.parse.quote(query.strip())
        url = f"{CHEMBL_BASE}/molecule/search?q={q}&format=json&limit=25"
        try:
            hits = _paginate(url, "molecules")
        except Exception:
            hits = []
        # synonym exact filter as a secondary pass
        if not hits:
            url2 = (f"{CHEMBL_BASE}/molecule.json?molecule_synonyms__molecule_synonym__iexact="
                    f"{q}&limit=25")
            hits = _paginate(url2, "molecules")

    # (c) structure fallback
    if not hits and smiles:
        s = urllib.parse.quote(smiles.strip())
        url = f"{CHEMBL_BASE}/molecule.json?molecule_structures__canonical_smiles__flexmatch={s}&limit=25"
        hits = _paginate(url, "molecules")

    if not hits:
        raise ValueError(f"Compound '{query}' not found in ChEMBL. "
                         f"Try a ChEMBL ID or provide a SMILES.")

    def _phase(h):
        v = h.get("max_phase")
        return v if isinstance(v, (int, float)) else -1

    cands = [{
        "molecule_chembl_id": h.get("molecule_chembl_id"),
        "pref_name": h.get("pref_name"),
        "max_phase": h.get("max_phase"),
        "canonical_smiles": (h.get("molecule_structures") or {}).get("canonical_smiles"),
    } for h in hits if h.get("molecule_chembl_id")]

    # Prefer an exact name match; otherwise the most clinically advanced molecule.
    ql = query.strip().lower()
    exact = [c for c in cands if (c["pref_name"] or "").lower() == ql]
    chosen = (exact or (sorted(cands, key=lambda c: _phase(hits[cands.index(c)]),
                               reverse=True) if prefer_max_phase else cands))[0]
    chosen = dict(chosen)
    chosen["candidates"] = cands
    return chosen


# --------------------------------------------------------------------------- #
# 2. Pull bioactivity records
# --------------------------------------------------------------------------- #
def fetch_activities(molecule_chembl_id: Optional[str] = None,
                     target_chembl_id: Optional[str] = None,
                     activity_types: Optional[list[str]] = None,
                     page_size: int = 1000) -> list[dict]:
    """
    Pull ChEMBL activity records (paginated).

    Compound-centric: pass molecule_chembl_id.
    Target-centric:   pass target_chembl_id (rank many compounds on one target).

    `activity_types` filters standard_type (default IC50/Ki/Kd/Kd(app)).
    """
    if not (molecule_chembl_id or target_chembl_id):
        raise ValueError("Provide molecule_chembl_id or target_chembl_id.")
    types = activity_types or DEFAULT_ACTIVITY_TYPES
    params = {
        "format": "json",
        "limit": str(page_size),
        "standard_type__in": ",".join(types),
    }
    if molecule_chembl_id:
        params["molecule_chembl_id"] = molecule_chembl_id
    if target_chembl_id:
        params["target_chembl_id"] = target_chembl_id
    url = f"{CHEMBL_BASE}/activity.json?" + urllib.parse.urlencode(params)
    return _paginate(url, "activities")


# --------------------------------------------------------------------------- #
# 3. Tidy DataFrame
# --------------------------------------------------------------------------- #
def build_frame(activities: list[dict]) -> pd.DataFrame:
    """Turn raw activity dicts into a tidy DataFrame with the context columns."""
    if not activities:
        return pd.DataFrame(columns=CONTEXT_COLS)
    df = pd.DataFrame(activities)
    for c in CONTEXT_COLS:
        if c not in df.columns:
            df[c] = np.nan
    df = df[CONTEXT_COLS].copy()
    df["standard_value"] = pd.to_numeric(df["standard_value"], errors="coerce")
    df["document_year"] = pd.to_numeric(df["document_year"], errors="coerce")
    # Fold Kd(app) into Kd for reporting but keep an "apparent" flag.
    df["kd_apparent"] = df["standard_type"] == "Kd(app)"
    df["sd_type"] = df["standard_type"].replace({"Kd(app)": "Kd", "Ki(app)": "Ki"})
    return df


# --------------------------------------------------------------------------- #
# 4. Assay classification  (biochemical / cellular target-engagement / antiprolif)
# --------------------------------------------------------------------------- #
_ANTIPROLIF = re.compile(
    r"inhibition of .*cell.*(growth|prolifer|viabil)|antiprolif|cytotox|"
    r"\bgi50\b|\bcc50\b|\btgi\b|cell viability|cell growth|cell survival",
    re.I)
_CELL_ENGAGE = re.compile(r"\bin .*cells\b|ex vivo|whole[- ]cell|intact cell|"
                          r"immunofluoresc|in cellulo|cellular par", re.I)
_RECOMBINANT = re.compile(r"expressed in .*(sf9|insect|escherichia|e\.? ?coli|"
                          r"baculovirus|hek|cho)|recombinant|purified enzyme", re.I)


def classify_assays(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add an `assay_class` column: 'antiproliferation', 'cellular_target_engagement',
    or 'biochemical'. Uses assay_description text + bao_label. The recombinant guard
    keeps "enzyme expressed in Sf9/E.coli" assays as *biochemical* even though the
    text mentions cells.
    """
    df = df.copy()
    desc = df["assay_description"].fillna("").astype(str)
    bao = df["bao_label"].fillna("").astype(str)

    def _cls(row_desc, row_bao):
        if _ANTIPROLIF.search(row_desc):
            return "antiproliferation"
        cell_bao = row_bao in ("cell-based format", "organism-based format",
                               "subcellular format", "tissue-based format")
        if (_CELL_ENGAGE.search(row_desc) or cell_bao) and not _RECOMBINANT.search(row_desc):
            # Distinguish engagement vs growth: growth handled above already
            return "cellular_target_engagement"
        return "biochemical"

    df["assay_class"] = [_cls(d, b) for d, b in zip(desc, bao)]
    return df


# --------------------------------------------------------------------------- #
# 5. Target tiering  (DATA-DRIVEN, no hard-coded IDs)
# --------------------------------------------------------------------------- #
def _target_meta(target_ids: list[str]) -> pd.DataFrame:
    """Fetch target_type / organism / pref_name for a set of target ChEMBL IDs."""
    rows = []
    for tid in [t for t in target_ids if isinstance(t, str) and t.startswith("CHEMBL")]:
        try:
            d = _get(f"{CHEMBL_BASE}/target/{tid}.json")
            comps = d.get("target_components") or []
            rows.append({
                "target_chembl_id": tid,
                "target_type": d.get("target_type"),
                "organism": d.get("organism"),
                "pref_name_full": d.get("pref_name"),
                "n_components": len(comps),
            })
        except Exception:
            rows.append({"target_chembl_id": tid})
    return pd.DataFrame(rows)


def primary_candidate_table(protein: pd.DataFrame, min_n: int = 3) -> pd.DataFrame:
    """
    Rank single-protein targets as primary-target candidates. Returns a table the
    agent should PRINT for human review before trusting auto-detection.

    Score balances potency and support so a single ultra-potent outlier (n=1)
    cannot outrank a well-measured true primary:
        score = median_nM / sqrt(n)      (lower = stronger candidate)
    Requires n >= min_n exact-nM measurements. n=1/2 targets are shown but demoted.
    """
    cand = protein[(protein["standard_relation"] == "=") &
                   (protein["standard_units"] == "nM") &
                   (protein["standard_value"] > 0)].copy()
    if cand.empty:
        return pd.DataFrame()
    g = cand.groupby(["target_chembl_id", "target_pref_name"])["standard_value"]
    tab = g.agg(n="count", median="median", geomean=_geomean).reset_index()
    tab["score"] = tab["median"] / np.sqrt(tab["n"])
    tab["meets_min_n"] = tab["n"] >= min_n
    # well-supported candidates first (by score), then the rest
    tab = pd.concat([
        tab[tab["meets_min_n"]].sort_values("score"),
        tab[~tab["meets_min_n"]].sort_values("median"),
    ], ignore_index=True)
    return tab


def detect_primary_targets(protein: pd.DataFrame, min_n: int = 3,
                           top_k: int = 2) -> list[str]:
    """
    Auto-pick up to `top_k` primary target IDs using primary_candidate_table.
    Falls back to the most-measured target when nothing meets min_n.
    NOTE: heuristic only -- the SKILL.md requires the agent to review the
    candidate table and override when domain knowledge disagrees.
    """
    tab = primary_candidate_table(protein, min_n=min_n)
    if tab.empty:
        return protein["target_chembl_id"].value_counts().head(1).index.tolist()
    supported = tab[tab["meets_min_n"]]
    if len(supported):
        return supported["target_chembl_id"].head(top_k).tolist()
    return tab["target_chembl_id"].head(1).tolist()


def tier_targets(df: pd.DataFrame, primary_target_ids: Optional[list[str]] = None,
                 restrict_target_ids: Optional[list[str]] = None) -> pd.DataFrame:
    """
    Assign each record a `tier`:
        'cellular'          -> cell/organism/tissue assays (kept separate)
        'primary'           -> the auto-detected (or user-supplied) main target(s)
        'offtarget'         -> other single-protein targets
    Detection is data-driven: the primary target(s) are the single-protein
    target(s) with the most potent, best-supported biochemical IC50/Ki/Kd
    (lowest median with adequate n). Pass `primary_target_ids` to override, or
    `restrict_target_ids` to keep only a user-named target set as protein tiers.
    """
    df = df.copy()
    if "assay_class" not in df.columns:
        df = classify_assays(df)

    # Always start from a clean slate so re-tiering / overrides take full effect
    # (otherwise a prior auto-detected 'primary' would persist). Object dtype so
    # string tier labels assign cleanly.
    df["tier"] = pd.Series([np.nan] * len(df), index=df.index, dtype="object")

    # Cellular tier = non-biochemical readouts (antiproliferation or cell engagement)
    is_cell = df["assay_class"].isin(["antiproliferation", "cellular_target_engagement"])
    df.loc[is_cell, "tier"] = "cellular"

    protein = df[~is_cell].copy()
    if restrict_target_ids:
        protein = protein[protein["target_chembl_id"].isin(restrict_target_ids)]

    if primary_target_ids is None:
        primary_target_ids = detect_primary_targets(protein)

    df.loc[df["target_chembl_id"].isin(primary_target_ids) & ~is_cell, "tier"] = "primary"
    df.loc[df["tier"].isna() & ~is_cell, "tier"] = "offtarget"
    df["is_primary_target"] = df["target_chembl_id"].isin(primary_target_ids)
    df.attrs["primary_target_ids"] = primary_target_ids
    return df


# --------------------------------------------------------------------------- #
# 6. "Standard" quality filter + provenance
# --------------------------------------------------------------------------- #
def standard_filter(df: pd.DataFrame, tiers=("primary", "offtarget")) -> tuple[pd.DataFrame, dict]:
    """
    Apply the documented "Standard" QC policy to protein-target records and
    return (clean_exact_nM_df, provenance_dict).

    Policy:
      * keep only the requested protein tiers (default primary + offtarget)
      * drop records flagged 'Potential transcription error'
      * keep exact relation '=' and standard_units == 'nM' for aggregation
      * RETAIN 'Outside typical range' (flagged) -- common for very potent drugs
      * censored (>, <, >=, <=) values are SET ASIDE (reported as bounds), not aggregated
    Provenance reconciles: raw = clean + txn_error + non_nM + censored.
    """
    prov = {}
    prot = df[df["tier"].isin(tiers)].copy()
    prov["0_raw_pulled"] = int(len(df))
    prov["1_protein_target_records"] = int(len(prot))

    is_txn = prot["data_validity_comment"] == "Potential transcription error"
    prov["2_dropped_transcription_error"] = int(is_txn.sum())
    prot = prot[~is_txn]

    is_nM = prot["standard_units"] == "nM"
    prov["3_dropped_non_nM_units"] = int((~is_nM).sum())

    exact = prot["standard_relation"] == "="
    censored = prot[is_nM & ~exact].copy()
    prov["4_censored_reported_separately"] = int(len(censored))

    clean = prot[is_nM & exact].copy()
    clean["outside_typical_range"] = clean["data_validity_comment"] == "Outside typical range"
    prov["5_clean_exact_nM_for_aggregation"] = int(len(clean))
    prov["_censored_records"] = censored  # attached for the bounds table

    # log-molar transform for plotting (guard non-positive)
    v = clean["standard_value"].astype(float)
    clean["neg_log_M"] = np.where(v > 0, -np.log10(v * 1e-9), np.nan)
    return clean, prov


# --------------------------------------------------------------------------- #
# 7. Aggregate per target (or per compound) x measurement type
# --------------------------------------------------------------------------- #
def _geomean(s: pd.Series) -> float:
    v = s[s > 0].astype(float)
    return float(np.exp(np.log(v).mean())) if len(v) else np.nan


def aggregate(clean: pd.DataFrame, group_col: str = "target_chembl_id",
              label_col: str = "target_pref_name") -> pd.DataFrame:
    """
    Per (group x sd_type): n, median, IQR (q25,q75), min, max, geomean, #studies.
    group_col = 'target_chembl_id' for compound-centric; switch to
    'molecule_chembl_id' for target-centric ranking (also pass label_col).
    """
    if clean.empty:
        return pd.DataFrame()
    g = clean.groupby([group_col, label_col, "sd_type"])
    agg = g["standard_value"].agg(
        n="count", median="median",
        q25=lambda s: s.quantile(0.25), q75=lambda s: s.quantile(0.75),
        vmin="min", vmax="max", geomean=_geomean).reset_index()
    studies = g["document_chembl_id"].nunique().reset_index(name="n_studies")
    agg = agg.merge(studies, on=[group_col, label_col, "sd_type"])
    return agg.sort_values(["sd_type", "median"])


# --------------------------------------------------------------------------- #
# 8. Selectivity
# --------------------------------------------------------------------------- #
def selectivity(agg: pd.DataFrame, primary_labels: Optional[list[str]] = None,
                use_type: str = "IC50", label_col: str = "target_pref_name") -> pd.DataFrame:
    """
    Fold-selectivity of each off-target relative to the primary target(s), using
    median of `use_type`. If primary_labels is None, the most potent target
    (lowest median) is used as the reference. Returns a table with fold ratios.
    """
    sub = agg[agg["sd_type"] == use_type].copy()
    if sub.empty:
        return pd.DataFrame()
    sub = sub.sort_values("median")
    if primary_labels is None:
        primary_labels = [sub.iloc[0][label_col]]
    refs = {lab: float(sub[sub[label_col] == lab]["median"].iloc[0])
            for lab in primary_labels if (sub[label_col] == lab).any()}
    for lab, ref in refs.items():
        sub[f"fold_vs_{lab}"] = sub["median"] / ref
    sub["is_reference"] = sub[label_col].isin(refs)
    sub["provisional_n1"] = sub["n"] == 1
    return sub


# --------------------------------------------------------------------------- #
# 9. Literature sanity check helper
# --------------------------------------------------------------------------- #
def sanity_flag(primary_median_nM: float, lo: float = 0.1, hi: float = 1000.0) -> str:
    """
    Crude sanity band for a small-molecule primary-target median (nM).
    Returns 'ok' or a warning string. Tune lo/hi from literature per compound.
    """
    if primary_median_nM is None or np.isnan(primary_median_nM):
        return "no primary median"
    if primary_median_nM < lo or primary_median_nM > hi:
        return (f"WARNING: primary median {primary_median_nM:g} nM outside expected "
                f"{lo:g}-{hi:g} nM band -- verify target assignment / units")
    return "ok"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ChEMBL compound potency & selectivity")
    ap.add_argument("--compound", help="name / synonym / ChEMBL ID")
    ap.add_argument("--target", help="target ChEMBL ID (target-centric mode)")
    ap.add_argument("--smiles", default=None)
    ap.add_argument("--types", default=",".join(DEFAULT_ACTIVITY_TYPES))
    ap.add_argument("--out-prefix", default="/mnt/results/chembl_potency")
    args = ap.parse_args()

    types = args.types.split(",")
    if args.compound:
        mol = resolve_compound(args.compound, smiles=args.smiles)
        print("Resolved:", mol["molecule_chembl_id"], "|", mol["pref_name"],
              "| candidates:", len(mol["candidates"]))
        acts = fetch_activities(mol["molecule_chembl_id"], activity_types=types)
    else:
        acts = fetch_activities(target_chembl_id=args.target, activity_types=types)
    print("Pulled", len(acts), "activity records")

    df = tier_targets(classify_assays(build_frame(acts)))
    clean, prov = standard_filter(df)
    print("Provenance:", {k: v for k, v in prov.items() if not k.startswith("_")})
    agg = aggregate(clean)
    agg.to_csv(f"{args.out_prefix}_aggregated.csv", index=False)
    df.to_csv(f"{args.out_prefix}_records.csv", index=False)
    print("Wrote", f"{args.out_prefix}_aggregated.csv")
