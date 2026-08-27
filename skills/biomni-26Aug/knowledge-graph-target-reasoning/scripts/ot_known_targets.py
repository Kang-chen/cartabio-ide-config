#!/usr/bin/env python3
"""
ot_known_targets.py
===================
Build a COMMERCIALLY-USABLE "known drug target" label for the face-validity
self-check, replacing the DrugBank-derived `known_drug_target` column that is
dropped in commercial mode.

Open Targets Platform data is released under **CC0 1.0** (public domain, no
attribution required, commercial use permitted) and the GraphQL API is free with
no key. This script:

  1. Resolves the disease name -> an Open Targets disease id (EFO/MONDO) via the
     `search` query (or uses --efo-id directly).
  2. Pulls the disease's `associatedTargets` (paginated) with per-datatype scores.
  3. Marks a target as a known drug target when it has **clinical**-datatype
     association evidence (score > --clinical-min; this is Open Targets' drug /
     clinical-precedence datatype) OR, optionally, when its overall association
     score >= --assoc-min.
  4. Joins a boolean `known_target_ot` column (by gene symbol) onto
     ranked_targets.csv, in place (or to --out).

If Open Targets is unreachable, pass --skip-on-error to leave the label absent so
the caller can fall back to a self-contained seed-recovery check.

Usage
-----
    python ot_known_targets.py \
        --ranked /mnt/results/SLE_target_ranking_commercial/ranked_targets.csv \
        --disease-name "systemic lupus erythematosus"
    # or with a known id:
    python ot_known_targets.py --ranked ... --efo-id MONDO_0007915

License: Open Targets data CC0 1.0; API free, no key.
"""
import argparse, json, os, sys, time
import pandas as pd

OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"


def ot_query(query, variables=None, retries=3, timeout=60):
    import requests
    last = None
    for i in range(retries):
        try:
            r = requests.post(OT_URL, json={"query": query, "variables": variables or {}},
                              timeout=timeout)
            r.raise_for_status()
            p = r.json()
            if "errors" in p:
                raise RuntimeError(p["errors"])
            return p["data"]
        except Exception as e:  # noqa
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def resolve_disease(name):
    q = """query S($q:String!){search(queryString:$q,entityNames:["disease"]){hits{id name entity}}}"""
    hits = ot_query(q, {"q": name})["search"]["hits"]
    hits = [h for h in hits if h["entity"] == "disease"]
    if not hits:
        raise SystemExit(f"No Open Targets disease matched '{name}'.")
    # prefer an exact (case-insensitive) name match, else the first hit
    for h in hits:
        if h["name"].lower() == name.lower():
            return h["id"], h["name"]
    return hits[0]["id"], hits[0]["name"]


def fetch_known_targets(efo_id, clinical_min=0.0, assoc_min=None, page_size=500, max_pages=20):
    """Return (known_symbols:set, assoc_score:dict, data_version, n_assoc_total)."""
    q = """
    query D($id:String!,$idx:Int!,$sz:Int!){
      disease(efoId:$id){ id name
        associatedTargets(page:{index:$idx,size:$sz}){
          count rows{ target{approvedSymbol} score datatypeScores{id score} } } } }
    """
    known, assoc = set(), {}
    total = None
    for idx in range(max_pages):
        d = ot_query(q, {"id": efo_id, "idx": idx, "sz": page_size})["disease"]
        if d is None:
            raise SystemExit(f"Open Targets returned no disease for id {efo_id}")
        at = d["associatedTargets"]
        total = at["count"]
        rows = at["rows"]
        if not rows:
            break
        for r in rows:
            sym = r["target"]["approvedSymbol"]
            assoc[sym] = r["score"]
            clin = any(dt["id"] == "clinical" and dt["score"] > clinical_min
                       for dt in r["datatypeScores"])
            if clin or (assoc_min is not None and r["score"] >= assoc_min):
                known.add(sym)
        if (idx + 1) * page_size >= total:
            break
    dv = ot_query("{meta{dataVersion{year month}}}")["meta"]["dataVersion"]
    return known, assoc, dv, total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ranked", required=True, help="ranked_targets.csv to annotate")
    ap.add_argument("--disease-name", default=None, help="disease name to resolve via OT search")
    ap.add_argument("--efo-id", default=None, help="OT disease id (EFO/MONDO), skips name resolution")
    ap.add_argument("--clinical-min", type=float, default=0.0,
                    help="min OT 'clinical' datatype score to count as known (default: >0)")
    ap.add_argument("--assoc-min", type=float, default=None,
                    help="optionally also mark targets with overall assoc score >= this")
    ap.add_argument("--col", default="known_target_ot", help="output column name")
    ap.add_argument("--out", default=None, help="output CSV (default: overwrite --ranked)")
    ap.add_argument("--meta-out", default=None, help="optional JSON to record OT provenance")
    ap.add_argument("--skip-on-error", action="store_true",
                    help="on any OT error, exit 0 without writing the label (enables caller fallback)")
    args = ap.parse_args()

    if not args.disease_name and not args.efo_id:
        raise SystemExit("Provide --disease-name or --efo-id.")

    try:
        if args.efo_id:
            efo_id, ot_name = args.efo_id, args.efo_id
            if args.disease_name:
                ot_name = args.disease_name
        else:
            efo_id, ot_name = resolve_disease(args.disease_name)
        print(f"[OT] disease: {ot_name}  id={efo_id}")
        known, assoc, dv, n_assoc = fetch_known_targets(
            efo_id, clinical_min=args.clinical_min, assoc_min=args.assoc_min)
        print(f"[OT] data version {dv['year']}-{dv['month']}; {n_assoc} associated targets; "
              f"{len(known)} known/clinical targets")
    except Exception as e:  # noqa
        msg = f"[OT] ERROR: {e}"
        if args.skip_on_error:
            print(msg + "  -> --skip-on-error set; leaving label absent for fallback.", file=sys.stderr)
            return 0
        raise

    df = pd.read_csv(args.ranked)
    df[args.col] = df["gene"].isin(known)
    n_hit = int(df[args.col].sum())
    # also attach OT overall association score (informational, CC0)
    df["ot_assoc_score"] = df["gene"].map(assoc).fillna(0.0)
    out = args.out or args.ranked
    df.to_csv(out, index=False)
    print(f"[OT] wrote {out}: {n_hit} rows flagged {args.col} "
          f"({n_hit} of {len(df)} ranked genes)")

    if args.meta_out:
        with open(args.meta_out, "w") as f:
            json.dump(dict(disease_name=ot_name, efo_id=efo_id,
                           ot_data_version=f"{dv['year']}-{dv['month']}",
                           n_associated_targets=n_assoc, n_known_targets_total=len(known),
                           n_known_in_ranking=n_hit, clinical_min=args.clinical_min,
                           assoc_min=args.assoc_min, source="Open Targets Platform (CC0 1.0)",
                           label_column=args.col), f, indent=2)
        print(f"[OT] wrote provenance {args.meta_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
