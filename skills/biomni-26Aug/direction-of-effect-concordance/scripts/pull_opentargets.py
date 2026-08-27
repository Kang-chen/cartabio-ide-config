#!/usr/bin/env python3
"""
pull_opentargets.py — resolve identifiers and pull structured target evidence from the
Open Targets Platform GraphQL API for direction-of-effect concordance.

Two modes:
  --resolve   : symbol -> Ensembl ID, indication -> EFO/MONDO ID (Step 1). Writes ids.json.
  (default)   : full target profile pull (Step 2): drug MoA/action type + clinical stage,
                mouse phenotype classes, target-disease genetic_association score,
                tractability, safety liabilities. Writes opentargets_raw.json.

Verified for Open Targets release 2026.06 (see references/opentargets_queries.md for the
schema-drift fixes). Always checks payload['errors'] (GraphQL returns errors with HTTP 200).

Usage:
  python pull_opentargets.py --resolve --targets "PCSK9,SOST,PNPLA3" \
      --indications "hypercholesterolemia;osteoporosis;MASLD" --out RUN/data
  python pull_opentargets.py --targets "PCSK9,SOST,PNPLA3" \
      --indications "hypercholesterolemia;osteoporosis;MASLD" --out RUN/data
"""
import argparse, json, os, sys, time
import requests

URL = "https://api.platform.opentargets.org/api/v4/graphql"

INHIBIT_ACTIONS = {"INHIBITOR", "ANTAGONIST", "BLOCKER", "NEGATIVE MODULATOR",
                   "NEGATIVE ALLOSTERIC MODULATOR", "DEGRADER", "RNAI",
                   "ANTISENSE INHIBITOR", "DISRUPTING AGENT", "PROTEOLYSIS TARGETING CHIMERA"}
ACTIVATE_ACTIONS = {"AGONIST", "PARTIAL AGONIST", "ACTIVATOR", "POSITIVE MODULATOR",
                    "POSITIVE ALLOSTERIC MODULATOR", "STABILISER", "OPENER"}


def ot_query(query, variables=None, retries=3):
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(URL, json={"query": query, "variables": variables or {}},
                              timeout=90)
            r.raise_for_status()
            payload = r.json()
            if "errors" in payload:
                raise RuntimeError(payload["errors"])
            return payload["data"]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Open Targets query failed after {retries} tries: {last}")


SEARCH_Q = """
query Search($q: String!, $ents: [String!]!) {
  search(queryString: $q, entityNames: $ents) { hits { id name entity } }
}
"""

META_Q = "query { meta { dataVersion { year month } } }"

TARGET_Q = """
query Target($ensemblId: String!) {
  target(ensemblId: $ensemblId) {
    id approvedSymbol approvedName biotype isEssential
    tractability { label modality value }
    safetyLiabilities { event eventId datasource literature }
    drugAndClinicalCandidates {
      count
      rows {
        maxClinicalStage
        drug {
          id name maximumClinicalStage drugType
          mechanismsOfAction { rows { mechanismOfAction actionType targetName } }
        }
      }
    }
    mousePhenotypes { modelPhenotypeLabel modelPhenotypeClasses { id label } }
    associatedDiseases(page: { index: 0, size: 15 }) {
      rows { disease { id name } score datatypeScores { id score } }
    }
  }
}
"""


def parse_pairs(targets, indications):
    tlist = [t.strip() for t in targets.split(",") if t.strip()]
    ilist = [i.strip() for i in indications.split(";")] if indications else [""]
    if len(ilist) == 1:
        ilist = ilist * len(tlist)
    if len(ilist) != len(tlist):
        sys.exit(f"ERROR: {len(tlist)} targets but {len(ilist)} indications.")
    return list(zip(tlist, ilist))


def resolve(pairs):
    ids = {"targets": {}, "indications": {}, "warnings": []}
    for g, ind in pairs:
        hits = ot_query(SEARCH_Q, {"q": g, "ents": ["target"]})["search"]["hits"]
        if not hits:
            ids["warnings"].append(f"No Open Targets target hit for '{g}' -- check symbol.")
            ids["targets"][g] = None
        else:
            top = hits[0]
            ids["targets"][g] = top["id"]
            if top["name"].upper() != g.upper():
                ids["warnings"].append(
                    f"'{g}' resolved to {top['id']} named '{top['name']}' -- confirm this is intended.")
        if ind:
            dh = ot_query(SEARCH_Q, {"q": ind, "ents": ["disease"]})["search"]["hits"]
            ids["indications"][ind] = dh[0]["id"] if dh else None
            if not dh:
                ids["warnings"].append(f"No EFO/MONDO hit for indication '{ind}'.")
    return ids


def summarize_target(node, indication_efo=None):
    """Collapse the raw target node into direction-relevant fields."""
    drugs = []
    inhib, activ = 0, 0
    for row in (node.get("drugAndClinicalCandidates") or {}).get("rows", []) or []:
        d = row.get("drug") or {}
        moas = [(m.get("mechanismOfAction"), (m.get("actionType") or "").upper())
                for m in ((d.get("mechanismsOfAction") or {}).get("rows") or [])]
        for _, at in moas:
            if at in INHIBIT_ACTIONS:
                inhib += 1
            elif at in ACTIVATE_ACTIONS:
                activ += 1
        drugs.append({"name": d.get("name"), "drug_type": d.get("drugType"),
                      "max_stage": d.get("maximumClinicalStage"),
                      "row_stage": row.get("maxClinicalStage"),
                      "moa": [{"mechanism": m, "action_type": at} for m, at in moas]})
    # genetic_association score for the indication (if provided/matched)
    gen_score = None
    assoc = []
    for row in (node.get("associatedDiseases") or {}).get("rows", []) or []:
        dts = {x["id"]: x["score"] for x in (row.get("datatypeScores") or [])}
        assoc.append({"efo": row["disease"]["id"], "name": row["disease"]["name"],
                      "score": row.get("score"), "datatype_scores": dts})
        if indication_efo and row["disease"]["id"] == indication_efo:
            gen_score = dts.get("genetic_association")
    pheno_classes = []
    seen = set()
    for mp in node.get("mousePhenotypes") or []:
        for c in mp.get("modelPhenotypeClasses") or []:
            if c["label"] not in seen:
                seen.add(c["label"])
                pheno_classes.append({"id": c["id"], "label": c["label"]})
    drug_direction = ("INHIBIT" if inhib and inhib >= activ else
                      "ACTIVATE" if activ else "not_informative")
    return {
        "ensembl_id": node["id"], "approved_symbol": node.get("approvedSymbol"),
        "is_essential": node.get("isEssential"),
        "tractability": node.get("tractability"),
        "drugs": drugs, "n_inhibitor_moa": inhib, "n_activator_moa": activ,
        "drug_moa_direction": drug_direction,
        "mouse_phenotype_classes": pheno_classes,
        "associated_diseases": assoc, "indication_genetic_association": gen_score,
        "safety_liabilities": [{"event": s.get("event"), "datasource": s.get("datasource")}
                               for s in (node.get("safetyLiabilities") or [])],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--indications", default="")
    ap.add_argument("--resolve", action="store_true", help="only resolve IDs (Step 1)")
    ap.add_argument("--out", required=True, help="output dir (RUN/data)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    pairs = parse_pairs(args.targets, args.indications)

    ids = resolve(pairs)
    with open(os.path.join(args.out, "ids.json"), "w") as fh:
        json.dump(ids, fh, indent=2)
    for w in ids["warnings"]:
        print("WARNING:", w)

    if args.resolve:
        print(f"Resolved {sum(v is not None for v in ids['targets'].values())}/"
              f"{len(pairs)} targets -> {args.out}/ids.json")
        return

    dv = ot_query(META_Q)["meta"]["dataVersion"]
    data_version = f"{dv['year']}.{int(dv['month']):02d}"
    out = {"data_version": data_version, "source": "Open Targets Platform", "targets": {}}
    for g, ind in pairs:
        ens = ids["targets"].get(g)
        if not ens:
            print(f"SKIP {g}: no Ensembl ID.")
            continue
        efo = ids["indications"].get(ind)
        node = ot_query(TARGET_Q, {"ensemblId": ens})["target"]
        out["targets"][g] = summarize_target(node, efo)
        out["targets"][g]["indication"] = ind
        out["targets"][g]["indication_efo"] = efo
        print(f"{g}: drugs={len(out['targets'][g]['drugs'])} "
              f"MoA->{out['targets'][g]['drug_moa_direction']} "
              f"mouse_classes={len(out['targets'][g]['mouse_phenotype_classes'])} "
              f"gen_assoc={out['targets'][g]['indication_genetic_association']}")

    with open(os.path.join(args.out, "opentargets_raw.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"Open Targets {data_version} -> {args.out}/opentargets_raw.json")


if __name__ == "__main__":
    main()
