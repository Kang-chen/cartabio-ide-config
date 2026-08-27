#!/usr/bin/env python3
"""
ot_disease_seeds.py
===================
Build a COMMERCIAL-SAFE disease seed-gene list from Open Targets genetic-association
evidence (CC0 1.0), to REPLACE PrimeKG's DisGeNET-derived `disease_protein` seeds.

Why this is necessary
---------------------
PrimeKG's `disease_protein` (gene-disease) edges are curated from **DisGeNET**
(CC BY-NC-SA 4.0, non-commercial). PrimeKG's `x_source`/`y_source` columns record
NODE vocabularies (gene=NCBI, disease=MONDO), **not** the edge-evidence source, so a
provenance filter on those columns CANNOT remove DisGeNET-derived seeds. The seeds
are the entire foundation of the RWR ranking, so a genuinely commercial-safe run
must replace the seed source. Open Targets data is CC0 (public domain), so its
genetic-association targets are a commercial-usable substitute.

Output
------
JSON {efo_id, disease_name, ot_data_version, threshold/top_n, n, seeds:[[symbol,ensembl],...]}
suitable for `rank_kg_targets.py --seeds-file`.

Usage
-----
    python ot_disease_seeds.py --disease-name "systemic lupus erythematosus" \
        --min-genetic 0.3 --out /mnt/results/SLE_target_ranking_commercial/ot_seeds.json
    # or a fixed count:
    python ot_disease_seeds.py --disease-name "..." --top-n 100 --out ...

License: Open Targets data CC0 1.0; API free, no key.
"""
import argparse, json, sys, time

URL = "https://api.platform.opentargets.org/api/v4/graphql"


def q(query, variables=None, retries=3, timeout=60):
    import requests
    last = None
    for i in range(retries):
        try:
            r = requests.post(URL, json={"query": query, "variables": variables or {}}, timeout=timeout)
            r.raise_for_status()
            p = r.json()
            if "errors" in p:
                raise RuntimeError(p["errors"])
            return p["data"]
        except Exception as e:  # noqa
            last = e; time.sleep(1.5 * (i + 1))
    raise last


def resolve(name):
    d = q("""query S($q:String!){search(queryString:$q,entityNames:["disease"]){hits{id name entity}}}""", {"q": name})
    hits = [h for h in d["search"]["hits"] if h["entity"] == "disease"]
    if not hits:
        raise SystemExit(f"No Open Targets disease matched '{name}'.")
    for h in hits:
        if h["name"].lower() == name.lower():
            return h["id"], h["name"]
    return hits[0]["id"], hits[0]["name"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disease-name", default=None)
    ap.add_argument("--efo-id", default=None)
    ap.add_argument("--min-genetic", type=float, default=0.3,
                    help="min Open Targets genetic_association datatype score for a seed (default 0.3)")
    ap.add_argument("--top-n", type=int, default=None,
                    help="instead of a threshold, take the top-N targets by genetic_association score")
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not args.disease_name and not args.efo_id:
        raise SystemExit("Provide --disease-name or --efo-id.")

    if args.efo_id:
        efo, name = args.efo_id, (args.disease_name or args.efo_id)
    else:
        efo, name = resolve(args.disease_name)
    print(f"[OT-seeds] disease: {name}  id={efo}")

    data = q("""query D($id:String!,$sz:Int!){disease(efoId:$id){
      associatedTargets(page:{index:0,size:$sz}){count rows{
        target{approvedSymbol id} datatypeScores{id score}}}}}""",
             {"id": efo, "sz": args.page_size})["disease"]
    rows = data["associatedTargets"]["rows"]

    def gscore(r):
        for x in r["datatypeScores"]:
            if x["id"] == "genetic_association":
                return x["score"]
        return 0.0

    scored = [(r["target"]["approvedSymbol"], r["target"]["id"], gscore(r)) for r in rows]
    scored = [s for s in scored if s[2] > 0]
    scored.sort(key=lambda t: -t[2])
    if args.top_n:
        chosen = scored[:args.top_n]; crit = {"top_n": args.top_n}
    else:
        chosen = [s for s in scored if s[2] >= args.min_genetic]; crit = {"min_genetic": args.min_genetic}
    dv = q("{meta{dataVersion{year month}}}")["meta"]["dataVersion"]

    out = dict(efo_id=efo, disease_name=name, ot_data_version=f"{dv['year']}-{dv['month']}",
               source="Open Targets genetic_association (CC0 1.0)",
               n=len(chosen), seeds=[[s[0], s[1]] for s in chosen], **crit)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"[OT-seeds] {len(chosen)} seeds ({crit}); OT v{out['ot_data_version']}; wrote {args.out}")
    print("[OT-seeds] top 15:", [s[0] for s in chosen[:15]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
