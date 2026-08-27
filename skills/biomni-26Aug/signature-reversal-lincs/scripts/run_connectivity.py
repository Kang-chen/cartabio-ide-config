#!/usr/bin/env python3
"""
run_connectivity.py — LINCS L1000 signature-reversal drug repurposing (disease-agnostic).

Given an up/down query signature, query SigCom LINCS for reversing compounds, aggregate to
per-compound tiers, compute a reproducibility-weighted composite score, and emit a robustness
summary. Falls back to local GMT enrichment if the SigCom API is unreachable.

This is a REFERENCE implementation for the `signature-reversal-lincs` skill. Read it, then adapt
inline in the analysis (thresholds, tissue lines, weights) to the specific disease — do not treat it
as a frozen black box. All numeric outputs are computed from data, never hardcoded.

Usage
-----
  python run_connectivity.py --up up_genes.txt --down down_genes.txt --out OUTDIR \
      [--database l1000_cp] [--limit 2000] [--min-sigs 2] [--tissue-lines HT29,HCT116,...]

Inputs
------
  --up / --down : text files, one HGNC symbol per line (or comma/whitespace separated).

Outputs (in OUTDIR)
-------------------
  reversers_raw.csv                  full ranked reverser rows returned by the API
  tier1_ranking.csv                  reproducible compounds (>= --min-sigs), composite-scored
  tier2_single_signature.csv         single-signature compounds
  robustness_summary.json            counts, coverage, strongest z-sum, tissue view, provenance
"""
import argparse, json, os, re, sys, time
import numpy as np
import pandas as pd
import requests

METADATA_API = "https://maayanlab.cloud/sigcom-lincs/metadata-api"
DATA_API     = "https://maayanlab.cloud/sigcom-lincs/data-api/api/v1"

# --- local data lake paths (fallback + BRD annotation) ---
GMT_DRUG = ("/mnt/datalake/LINCS1000/RNAseq_transcriptomics_genesets/"
            "single_drug_perturbations-v1.0.gmt")
HUB_MOL  = ("/mnt/datalake/broad_drug_repurposing_hub/"
            "broad_repurposing_hub_molecule_with_smiles.parquet")
HUB_INFO = ("/mnt/datalake/broad_drug_repurposing_hub/"
            "broad_repurposing_hub_phase_moa_target_info.parquet")

# Tissue-relevant cell lines for gut/colorectal disease (override with --tissue-lines).
DEFAULT_TISSUE_LINES = ["HT29","HCT116","SW480","SW620","LOVO","CACO2","HCT15","COLO205",
                        "LS180","DLD1","NCIH508","T84","RKO","GP2D"]


# ---------------------------------------------------------------- IO helpers
def read_genes(path):
    txt = open(path).read()
    toks = re.split(r"[\s,;]+", txt.strip())
    return sorted({t.strip().upper() for t in toks if t.strip()})


# ---------------------------------------------------------------- SigCom API
def api_reachable(timeout=15):
    """Probe with the real gene-resolution endpoint the skill depends on.
    Do NOT probe /listdata — it returns 404 on the live server and would trigger a false fallback."""
    try:
        r = requests.post(f"{METADATA_API}/entities/find",
                          json={"filter": {"where": {"meta.symbol": {"inq": ["TNF"]}}}},
                          timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False

def resolve_genes(genes, timeout=60):
    r = requests.post(f"{METADATA_API}/entities/find",
                      json={"filter": {"where": {"meta.symbol": {"inq": list(genes)}}}},
                      timeout=timeout)
    r.raise_for_status()
    return {e["meta"]["symbol"]: e["id"] for e in r.json()}

def connectivity_query(up_ids, dn_ids, database="l1000_cp", limit=2000, timeout=300):
    body = {"up_entities": list(up_ids), "down_entities": list(dn_ids),
            "limit": limit, "database": database}
    r = requests.post(f"{DATA_API}/enrich/ranktwosided", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()

def signature_meta(uuids, batch=100, timeout=120):
    out = {}
    for i in range(0, len(uuids), batch):
        chunk = uuids[i:i+batch]
        r = requests.post(f"{METADATA_API}/signatures/find",
                          json={"filter": {"where": {"id": {"inq": chunk}}}}, timeout=timeout)
        r.raise_for_status()
        for s in r.json():
            m = s.get("meta", {})
            out[s["id"]] = {k: m.get(k) for k in
                            ("pert_name","cell_line","pert_dose","pert_time",
                             "pubchem_id","cmap_id","moa")}
    return out


# ---------------------------------------------------------------- BRD name mapping
def base_brd(bid):
    m = re.match(r"(BRD-[A-Z0-9]+)", str(bid))
    return m.group(1) if m else None

def load_brd_map():
    if not os.path.exists(HUB_MOL):
        return {}, None
    hub = pd.read_parquet(HUB_MOL)
    brd_col = next((c for c in hub.columns if re.search(r"broad|brd|deprecated_broad_id|pert_id", c, re.I)), None)
    name_col = next((c for c in hub.columns if re.search(r"pert_iname|name", c, re.I)), None)
    m = {}
    if brd_col and name_col:
        for b, n in zip(hub[brd_col], hub[name_col]):
            bb = base_brd(b)
            if bb and isinstance(n, str) and n:
                m.setdefault(bb, n)
    return m, hub

def annotate_name(pert_name, brd_map):
    if isinstance(pert_name, str) and pert_name and not pert_name.startswith("BRD-"):
        return pert_name
    bb = base_brd(pert_name)
    return brd_map.get(bb, pert_name)


# ---------------------------------------------------------------- aggregation + scoring
def zscore(s):
    s = np.asarray(s, float)
    return (s - np.nanmean(s)) / (np.nanstd(s) + 1e-9)

def build_reverser_table(results, meta, brd_map):
    df = pd.DataFrame(results)
    for col in ("z-sum", "fdr-down"):
        if col not in df.columns:
            raise SystemExit(f"Unexpected API response: missing '{col}'. Got {list(df.columns)}")
    df["uuid"] = df.get("uuid", df.get("id"))
    df["compound_raw"] = df["uuid"].map(lambda u: (meta.get(u, {}) or {}).get("pert_name"))
    df["cell_line"]    = df["uuid"].map(lambda u: (meta.get(u, {}) or {}).get("cell_line"))
    df["moa"]          = df["uuid"].map(lambda u: (meta.get(u, {}) or {}).get("moa"))
    df["compound"]     = df["compound_raw"].map(lambda x: annotate_name(x, brd_map))
    return df

def aggregate(df, min_sigs=2):
    rev = df[df["type"] == "reversers"].copy()
    mim = df[df["type"] == "mimickers"].copy()
    n_mim = mim.groupby("compound").size().rename("n_mimicking_sigs")
    g = rev.groupby("compound")
    agg = pd.DataFrame({
        "n_reversing_sigs": g.size(),
        "n_cell_lines":     g["cell_line"].nunique(),
        "median_z_sum":     g["z-sum"].median(),
        "best_z_sum":       g["z-sum"].min(),
        "best_fdr_down":    g["fdr-down"].min(),
        "moa":              g["moa"].agg(lambda s: next((x for x in s if isinstance(x, str) and x), None)),
    }).join(n_mim).fillna({"n_mimicking_sigs": 0})
    agg["reverser_specificity"] = agg["n_reversing_sigs"] / (agg["n_reversing_sigs"] + agg["n_mimicking_sigs"])
    agg = agg.reset_index()

    tier1 = agg[agg["n_reversing_sigs"] >= min_sigs].copy()
    tier2 = agg[agg["n_reversing_sigs"] < min_sigs].copy().sort_values("best_z_sum")

    strength = -tier1["median_z_sum"]
    repro    = np.log1p(tier1["n_reversing_sigs"]) + np.log1p(tier1["n_cell_lines"])
    signif   = -np.log10(tier1["best_fdr_down"].clip(lower=1e-320))
    tier1["reverser_score"] = (0.45*zscore(repro) + 0.30*zscore(strength) + 0.25*zscore(signif)) \
                              * tier1["reverser_specificity"]
    tier1 = tier1.sort_values("reverser_score", ascending=False).reset_index(drop=True)
    tier1.insert(0, "rank", tier1.index + 1)
    return tier1, tier2, rev, mim


# ---------------------------------------------------------------- local GMT fallback
def parse_gmt(path):
    sets = {}
    with open(path) as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                sets[parts[0]] = {g.split(",")[0].strip().upper() for g in parts[2:] if g.strip()}
    return sets

def fallback_local(up, dn, out, min_sigs=2, universe=12000):
    """Coarse reversal scoring by hypergeometric overlap of query-up vs compound-down and
    query-down vs compound-up. Indicative ranks only."""
    from scipy.stats import hypergeom
    if not os.path.exists(GMT_DRUG):
        raise SystemExit(f"API unreachable and local GMT not found at {GMT_DRUG}")
    sets = parse_gmt(GMT_DRUG)
    up, dn = set(up), set(dn)
    rows = []
    for name, genes in sets.items():
        low = name.lower()
        # CREEDS-style names carry direction; pair up-query with drug-down and vice versa
        is_up  = ("-up" in low) or low.endswith(" up") or ("_up" in low)
        is_dn  = ("-dn" in low) or ("down" in low) or ("_dn" in low)
        if not (is_up or is_dn):
            continue
        q = dn if is_up else up          # reversal: drug-up opposes query-down; drug-dn opposes query-up
        k = len(genes & q)
        if k == 0:
            continue
        p = hypergeom.sf(k-1, universe, len(q), len(genes))
        rows.append({"signature": name, "compound": re.split(r"[-_ ]", name)[0],
                     "overlap": k, "p": p, "direction": "up" if is_up else "dn"})
    fb = pd.DataFrame(rows)
    if fb.empty:
        raise SystemExit("Local fallback produced no overlaps; check gene symbols.")
    fb["z-sum"] = np.log10(fb["p"].clip(lower=1e-320))   # negative pseudo-z for compatibility
    g = fb.groupby("compound")
    agg = pd.DataFrame({"n_reversing_sigs": g.size(),
                        "median_z_sum": g["z-sum"].median(),
                        "best_z_sum": g["z-sum"].min(),
                        "best_p": g["p"].min()}).reset_index()
    agg = agg.sort_values("best_z_sum").reset_index(drop=True)
    agg.insert(0, "rank", agg.index + 1)
    agg["method"] = "local_gmt_fallback"
    fb.to_csv(os.path.join(out, "reversers_raw.csv"), index=False)
    agg.to_csv(os.path.join(out, "tier1_ranking.csv"), index=False)
    summ = {"engine": "local_gmt_fallback", "note": "API unreachable; indicative ranks only",
            "signature_up_genes": len(up), "signature_dn_genes": len(dn),
            "unique_reverser_compounds": int(agg.shape[0])}
    json.dump(summ, open(os.path.join(out, "robustness_summary.json"), "w"), indent=2)
    print("[fallback] wrote local GMT reversal ranking.")
    return summ


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--up", required=True); ap.add_argument("--down", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--database", default="l1000_cp")
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--min-sigs", type=int, default=2)
    ap.add_argument("--tissue-lines", default=",".join(DEFAULT_TISSUE_LINES))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    up, dn = read_genes(a.up), read_genes(a.down)
    print(f"[query] up={len(up)} down={len(dn)} genes")

    if not api_reachable():
        print("[warn] SigCom API unreachable -> local GMT fallback")
        fallback_local(up, dn, a.out, min_sigs=a.min_sigs)
        return

    sym2id = resolve_genes(up + dn)
    up_ids = [sym2id[g] for g in up if g in sym2id]
    dn_ids = [sym2id[g] for g in dn if g in sym2id]
    cov_up = 100.0*len(up_ids)/max(len(up),1); cov_dn = 100.0*len(dn_ids)/max(len(dn),1)
    print(f"[resolve] L1000 coverage up={cov_up:.1f}% dn={cov_dn:.1f}%")

    resp = connectivity_query(up_ids, dn_ids, database=a.database, limit=a.limit)
    n_rev, n_mim = resp.get("reversers"), resp.get("mimickers")   # counts, not lists
    print(f"[query] returned {len(resp['results'])} rows | reversers={n_rev} mimickers={n_mim}")

    uuids = [r.get("uuid", r.get("id")) for r in resp["results"]]
    meta  = signature_meta(uuids)
    brd_map, _ = load_brd_map()
    df = build_reverser_table(resp["results"], meta, brd_map)
    tier1, tier2, rev, mim = aggregate(df, min_sigs=a.min_sigs)

    # tissue-context view
    tissue = {t.strip().upper() for t in a.tissue_lines.split(",") if t.strip()}
    rev["cell_up"] = rev["cell_line"].astype(str).str.upper()
    tissue_hits = sorted(set(rev.loc[rev["cell_up"].isin(tissue), "compound"].dropna())
                         & set(tier1["compound"]))

    df.to_csv(os.path.join(a.out, "reversers_raw.csv"), index=False)
    tier1.to_csv(os.path.join(a.out, "tier1_ranking.csv"), index=False)
    tier2.to_csv(os.path.join(a.out, "tier2_single_signature.csv"), index=False)

    summ = {
        "engine": "sigcom_lincs", "database": a.database,
        "signature_up_genes": len(up), "signature_dn_genes": len(dn),
        "l1000_coverage_up_pct": round(cov_up,1), "l1000_coverage_dn_pct": round(cov_dn,1),
        "db_reversers_count": int(n_rev) if n_rev is not None else None,
        "db_mimickers_count": int(n_mim) if n_mim is not None else None,
        "rows_retrieved": int(len(resp["results"])),
        "unique_reverser_compounds": int(rev["compound"].nunique()),
        "tier1_reproducible_compounds": int(tier1.shape[0]),
        "tier2_single_signature": int(tier2.shape[0]),
        "strongest_reverser_zsum": float(tier1["median_z_sum"].min()) if not tier1.empty else None,
        "tissue_lines_used": sorted(tissue),
        "tier1_tissue_supported": tissue_hits,
        "tier1_tissue_supported_n": len(tissue_hits),
    }
    json.dump(summ, open(os.path.join(a.out, "robustness_summary.json"), "w"), indent=2)
    print(f"[done] Tier-1={tier1.shape[0]} Tier-2={tier2.shape[0]} "
          f"tissue-supported={len(tissue_hits)} -> {a.out}")


if __name__ == "__main__":
    main()
