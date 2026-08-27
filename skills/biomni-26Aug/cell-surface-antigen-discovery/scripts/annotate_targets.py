#!/usr/bin/env python3
"""Annotate candidate surface targets via the Open Targets Platform GraphQL API.

Adds, per gene:
- antibody tractability bucket + 0-1 score
- subcellular locations + plasma-membrane flag (cross-checks the topology filter)
- existing-antibody clinical maturity (from the AB tractability bucket) -> known-drug flag
- DepMap essentiality (mean gene effect) -> ANNOTATION ONLY, never a selection gate
- safety-liability count

NORMAL-TISSUE baseline is NOT fetched here. Open Targets v4 removed the `expressions`
field, so the baseline is built separately from the Human Protein Atlas by
scripts/hpa_baseline.py (consensus RNA + IHC protein), which writes
target_baseline_expression_long.csv for normal_tissue_safety.compute_therapeutic_index().

Network is accessed inside functions; the module imports cleanly offline. Failures
degrade to NaN/None (reported), never to fabricated values.
"""

import json
import os
import time

import pandas as pd

OT_GQL = "https://api.platform.opentargets.org/api/v4/graphql"

# Open Targets antibody tractability buckets -> 0-1 score (best evidence first).
_AB_BUCKET_SCORE = {
    "approved drug": 1.0,
    "advanced clinical": 0.9,
    "phase 1 clinical": 0.8,
    "uniprot loc high conf": 0.7,
    "go cc high conf": 0.65,
    "uniprot loc med conf": 0.6,
    "go cc med conf": 0.55,
    "uniprot sigp or tmhmm": 0.5,
    "human protein atlas loc": 0.5,
    "predicted tractable high confidence": 0.45,
    "predicted tractable med low confidence": 0.4,
}
_PM_TERMS = ("plasma membrane", "cell surface", "cell membrane", "apical", "basolateral")
# Antibody tractability bucket -> approximate clinical maturity of an existing antibody
# (Open Targets removed Target.knownDrugs; the AB tractability bucket already encodes this).
_AB_PHASE = {"approved drug": 4.0, "advanced clinical": 3.0, "phase 1 clinical": 1.0}


def _post(query, variables, retries=3, throttle=0.5):
    import requests
    last = None
    for i in range(retries):
        try:
            r = requests.post(OT_GQL, json={"query": query, "variables": variables}, timeout=45)
            if r.status_code == 200:
                time.sleep(throttle)
                return r.json().get("data")
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(throttle * (2 ** i))
    print(f"  ! Open Targets query failed after {retries} tries ({last})")
    return None


def resolve_ensembl_ids(symbols, chunk=25):
    """Map gene symbols -> Ensembl IDs via Open Targets mapIds (batched)."""
    q = ("query Map($qs:[String!]!){ mapIds(queryTerms:$qs entityNames:[\"target\"])"
         "{ mappings{ term hits{ id entity } } } }")
    out = {}
    syms = list(dict.fromkeys(symbols))
    for i in range(0, len(syms), chunk):
        part = syms[i:i + chunk]
        data = _post(q, {"qs": part})
        if not data:
            continue
        for m in data.get("mapIds", {}).get("mappings", []):
            term = m.get("term")
            ensg = next((h["id"] for h in m.get("hits", [])
                         if h.get("entity") == "target" and str(h.get("id", "")).startswith("ENSG")), None)
            if ensg:
                out[term] = ensg
    missing = [s for s in syms if s not in out]
    if missing:
        print(f"  Missing Ensembl mapping for {len(missing)} symbol(s): "
              f"{', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}")
    return out


_CORE_Q = """
query Ann($id:String!){
  target(ensemblId:$id){
    approvedSymbol
    tractability{ modality label value }
    subcellularLocations{ location }
    safetyLiabilities{ event }
  }
}"""
# NOTE: Open Targets v4 REMOVED the `expressions{ tissue rna protein }` field from the
# Target type (querying it returns HTTP 400 and fails every gene). Normal-tissue baseline
# is now built separately from the Human Protein Atlas by scripts/hpa_baseline.py, which
# writes target_baseline_expression_long.csv in the exact schema compute_therapeutic_index()
# consumes. Do NOT re-add `expressions` here.

_DEPMAP_Q = """
query Dep($id:String!){
  target(ensemblId:$id){ depMapEssentiality{ screens{ geneEffect } } }
}"""


def _antibody_score(tractability):
    best = 0.0
    label = None
    for t in tractability or []:
        if str(t.get("modality", "")).upper() != "AB":
            continue
        if not t.get("value"):
            continue
        lbl = str(t.get("label", "")).strip().lower()
        score = _AB_BUCKET_SCORE.get(lbl, 0.4)
        if score > best:
            best, label = score, t.get("label")
    return best, label


def annotate(genes, output_dir="results", include_depmap=True):
    """Annotate `genes` with Open Targets evidence. Writes target_annotations.csv and
    target_baseline_expression_long.csv; returns the annotation DataFrame."""
    os.makedirs(output_dir, exist_ok=True)
    sym2ensg = resolve_ensembl_ids(genes)

    ann_rows = []
    for sym in dict.fromkeys(genes):
        ensg = sym2ensg.get(sym)
        row = {"gene_symbol": sym, "ensembl_id": ensg,
               "antibody_tractability_score": float("nan"), "antibody_tractability_bucket": None,
               "subcellular_locations": None, "is_plasma_membrane": None,
               "known_drugs_count": float("nan"), "max_clinical_phase": float("nan"),
               "has_known_drug": None, "safety_liabilities_count": float("nan"),
               "depmap_mean_gene_effect": float("nan")}
        if ensg:
            data = _post(_CORE_Q, {"id": ensg})
            t = (data or {}).get("target") or {}
            if t:
                score, bucket = _antibody_score(t.get("tractability"))
                row["antibody_tractability_score"] = score
                row["antibody_tractability_bucket"] = bucket
                locs = [str(l.get("location")) for l in (t.get("subcellularLocations") or []) if l.get("location")]
                row["subcellular_locations"] = "; ".join(sorted(set(locs))) or None
                row["is_plasma_membrane"] = any(any(p in l.lower() for p in _PM_TERMS) for l in locs)
                # Existing-antibody competition/maturity derived from the AB tractability
                # bucket (OT removed Target.knownDrugs).
                phase = _AB_PHASE.get(str(bucket).lower(), 0.0) if bucket else 0.0
                row["max_clinical_phase"] = phase
                row["has_known_drug"] = phase > 0
                row["safety_liabilities_count"] = float(len(t.get("safetyLiabilities") or []))
            if include_depmap:
                dd = _post(_DEPMAP_Q, {"id": ensg})
                screens = []
                for blk in (((dd or {}).get("target") or {}).get("depMapEssentiality") or []):
                    screens += [s.get("geneEffect") for s in (blk.get("screens") or [])
                                if s.get("geneEffect") is not None]
                if screens:
                    row["depmap_mean_gene_effect"] = round(sum(screens) / len(screens), 4)
        ann_rows.append(row)

    ann_df = pd.DataFrame(ann_rows)
    ann_df.to_csv(os.path.join(output_dir, "target_annotations.csv"), index=False)
    n_ok = int(ann_df["antibody_tractability_score"].notna().sum())
    print(f"✓ Annotated {len(ann_df)} gene(s); {n_ok} with Open Targets tractability. "
          f"(DepMap essentiality stored as ANNOTATION only. Normal-tissue baseline is built "
          f"separately by scripts/hpa_baseline.py -> target_baseline_expression_long.csv.)")
    return ann_df


if __name__ == "__main__":
    df = annotate(["TACSTD2", "MET", "ERBB2"])
    print(df[["gene_symbol", "antibody_tractability_bucket", "is_plasma_membrane",
              "has_known_drug", "depmap_mean_gene_effect"]])
