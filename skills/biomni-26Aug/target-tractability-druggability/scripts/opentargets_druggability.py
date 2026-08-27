#!/usr/bin/env python3
"""
opentargets_druggability.py — pull the Open Targets evidence for a target-druggability
assessment, for ANY human protein-coding target.

Collects (target-centric core):
  - ID resolution (symbol -> Ensembl gene ID; UniProt accession)
  - tractability buckets across ALL modalities (SM / AB / PR / OC and anything else returned)
  - drugAndClinicalCandidates (known drugs / clinical candidates)
  - depMapEssentiality + isEssential  (DepMap: NEGATIVE geneEffect = essential)
  - safetyLiabilities
  - meta.dataVersion  (cite the release)
Optional (when --disease is given):
  - target<->disease association score

Design note: the Open Targets GraphQL schema changes between releases. This script is written
DEFENSIVELY — it probes field availability and degrades gracefully rather than 400-ing, because
`knownDrugs`->`drugAndClinicalCandidates` and Drug.`isApproved`/`maximumClinicalTrialPhase`->
`maximumClinicalStage` have already changed once. Always inspect payload["errors"].

Usage:
    python opentargets_druggability.py --target KRAS \
        [--disease "lung carcinoma" | --efo EFO_0001071] \
        [--out /mnt/results/data/KRAS_opentargets.json]

Requires: requests  (pip install requests). pandas optional.
"""
import argparse
import json
import os
import sys
import time

import requests

URL = "https://api.platform.opentargets.org/api/v4/graphql"


def ot_query(query, variables=None, retries=3, timeout=60):
    """POST a GraphQL query. Raises on GraphQL errors (they arrive in the body with HTTP 200)."""
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(URL, json={"query": query, "variables": variables or {}},
                              timeout=timeout)
            r.raise_for_status()
            payload = r.json()
            if "errors" in payload and payload["errors"]:
                raise RuntimeError(payload["errors"])
            return payload["data"]
        except (requests.RequestException, RuntimeError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Open Targets query failed after {retries} attempts: {last}")


# ----------------------------------------------------------------------------- resolution
def resolve_target(name_or_id):
    """Return (ensembl_id, approvedSymbol, uniprot_accession). Accepts a symbol or an Ensembl ID."""
    if name_or_id.upper().startswith("ENSG"):
        ensembl = name_or_id
    else:
        q = """
        query S($q: String!) {
          search(queryString: $q, entityNames: ["target"]) {
            hits { id name entity object { ... on Target { approvedSymbol } } }
          }
        }"""
        hits = ot_query(q, {"q": name_or_id}).get("search", {}).get("hits", [])
        # prefer an exact (case-insensitive) approvedSymbol match, else the first target hit
        ensembl = None
        for h in hits:
            sym = (h.get("object") or {}).get("approvedSymbol")
            if sym and sym.upper() == name_or_id.upper():
                ensembl = h["id"]
                break
        if ensembl is None and hits:
            ensembl = hits[0]["id"]
        if ensembl is None:
            raise SystemExit(f"Could not resolve target '{name_or_id}' via Open Targets search.")

    # fetch symbol, biotype, uniprot from the target record
    q2 = """
    query T($id: String!) {
      target(ensemblId: $id) {
        id approvedSymbol approvedName biotype
        proteinIds { id source }
      }
    }"""
    t = ot_query(q2, {"id": ensembl})["target"]
    if t is None:
        raise SystemExit(f"No Open Targets target record for {ensembl}.")
    uniprot = None
    for p in (t.get("proteinIds") or []):
        if p.get("source") in ("uniprot_swissprot", "uniprot", "uniprot_trembl"):
            uniprot = p["id"]
            if p["source"] == "uniprot_swissprot":
                break
    return t, uniprot


# ----------------------------------------------------------------------------- core annotations
def get_core(ensembl):
    """tractability (all modalities) + essentiality + safety + data version."""
    q = """
    query C($id: String!) {
      target(ensemblId: $id) {
        id approvedSymbol biotype isEssential
        tractability { label modality value }
        safetyLiabilities { event eventId datasource literature }
        depMapEssentiality {
          tissueName
          screens { depmapId cellLineName diseaseFromSource geneEffect expression mutation }
        }
      }
      meta { dataVersion { year month iteration } }
    }"""
    d = ot_query(q, {"id": ensembl})
    return d["target"], d.get("meta", {}).get("dataVersion", {})


def summarize_tractability(rows):
    """Group boolean buckets by modality. Returns {modality: {"true":[...], "false":[...], "n_true":k}}."""
    out = {}
    for r in rows or []:
        mod = r.get("modality", "??")
        out.setdefault(mod, {"true": [], "false": []})
        (out[mod]["true"] if r.get("value") else out[mod]["false"]).append(r.get("label"))
    for mod in out:
        out[mod]["n_true"] = len(out[mod]["true"])
        out[mod]["n_total"] = len(out[mod]["true"]) + len(out[mod]["false"])
    return out


def summarize_depmap(target):
    """DepMap essentiality summary. REMEMBER: negative geneEffect = essential (dependency)."""
    effects = []
    tissues = set()
    for t in (target.get("depMapEssentiality") or []):
        tissues.add(t.get("tissueName"))
        for s in (t.get("screens") or []):
            ge = s.get("geneEffect")
            if ge is not None:
                effects.append(ge)
    if not effects:
        return {"n_screens": 0, "note": "no DepMap geneEffect data"}
    import statistics as st
    n = len(effects)
    return {
        "n_screens": n,
        "n_tissues": len(tissues),
        "mean_gene_effect": round(st.mean(effects), 4),
        "median_gene_effect": round(st.median(effects), 4),
        "min_gene_effect": round(min(effects), 4),
        "max_gene_effect": round(max(effects), 4),
        # dependency fractions (more negative = more essential)
        "frac_dependent_lt_-0.5": round(sum(1 for e in effects if e < -0.5) / n, 3),
        "frac_strong_lt_-1.0": round(sum(1 for e in effects if e < -1.0) / n, 3),
        "convention": "NEGATIVE geneEffect = essential (knockout reduces viability)",
    }


# ----------------------------------------------------------------------------- drugs (defensive)
def get_drugs(ensembl):
    """
    Known drugs / clinical candidates. Tries the current field name first
    (drugAndClinicalCandidates), then falls back to legacy knownDrugs, and within the drug
    object tries maximumClinicalStage then the older isApproved/maximumClinicalTrialPhase.
    Returns a normalized list of dicts.
    """
    # candidate query templates, tried in order until one doesn't error
    templates = [
        # current (v25/26+)
        """
        query D($id: String!) {
          target(ensemblId: $id) {
            drugAndClinicalCandidates {
              count
              rows {
                maxClinicalStage
                drug { id name drugType maximumClinicalStage
                       mechanismsOfAction { rows { mechanismOfAction actionType } } }
                diseases { disease { name } }
              }
            }
          }
        }""",
        # legacy Target.knownDrugs
        """
        query D($id: String!) {
          target(ensemblId: $id) {
            knownDrugs {
              count uniqueDrugs
              rows {
                phase status
                drug { id name drugType isApproved maximumClinicalTrialPhase
                       mechanismsOfAction { rows { mechanismOfAction actionType } } }
                disease { name }
              }
            }
          }
        }""",
    ]
    data = None
    for tpl in templates:
        try:
            data = ot_query(tpl, {"id": ensembl})
            break
        except RuntimeError:
            continue
    if data is None:
        return {"count": 0, "rows": [], "note": "no compatible drugs field found"}

    tgt = data["target"] or {}
    container = tgt.get("drugAndClinicalCandidates") or tgt.get("knownDrugs") or {}
    rows_in = container.get("rows") or []
    seen = {}
    for r in rows_in:
        dr = r.get("drug") or {}
        did = dr.get("id")
        stage = (dr.get("maximumClinicalStage")
                 or r.get("maxClinicalStage")
                 or dr.get("maximumClinicalTrialPhase")
                 or r.get("phase"))
        moa = []
        for m in ((dr.get("mechanismsOfAction") or {}).get("rows") or []):
            moa.append(m.get("mechanismOfAction"))
        # disease name may be row-level (legacy) or nested (current)
        dis = None
        if r.get("diseases"):
            dis = "; ".join(sorted({(x.get("disease") or {}).get("name")
                                    for x in r["diseases"] if x.get("disease")}))
        elif r.get("disease"):
            dis = (r["disease"] or {}).get("name")
        rec = seen.setdefault(did or dr.get("name"), {
            "id": did, "name": dr.get("name"), "drugType": dr.get("drugType"),
            "maxClinicalStage": stage, "isApproved": dr.get("isApproved"),
            "mechanismOfAction": "; ".join(sorted({m for m in moa if m})) or None,
            "indications": set(),
        })
        if dis:
            rec["indications"].add(dis)
    rows = []
    for v in seen.values():
        v["indications"] = "; ".join(sorted(v["indications"])) if v["indications"] else None
        rows.append(v)
    # sort: approved / late stage first
    def stage_rank(s):
        s = (s or "").upper()
        if "APPROV" in s or s == "4":
            return 0
        for k, rnk in (("PHASE_3", 1), ("3", 1), ("PHASE_2", 2), ("2", 2),
                       ("PHASE_1", 3), ("1", 3)):
            if k in s:
                return rnk
        return 4
    rows.sort(key=lambda r: stage_rank(r["maxClinicalStage"]))
    return {"count": container.get("count", len(rows)), "rows": rows}


# ----------------------------------------------------------------------------- optional disease
def get_target_disease_association(ensembl, efo):
    q = """
    query A($id: String!, $efo: String!) {
      disease(efoId: $efo) {
        id name
        associatedTargets(enableIndirect: true,
          Bs: { index: 0, size: 1 } ) { count rows { target { id } score datatypeScores { id score } } }
      }
    }""".replace("Bs", "page")  # guard against accidental keyword mangling
    try:
        d = ot_query(q, {"id": ensembl, "efo": efo})
    except RuntimeError:
        return None
    dis = d.get("disease")
    if not dis:
        return None
    # find our target in the (broad) association list; if not on page 1, do a targeted evidence count
    score = None
    for row in (dis.get("associatedTargets") or {}).get("rows", []):
        if (row.get("target") or {}).get("id") == ensembl:
            score = row.get("score")
    return {"efoId": dis["id"], "diseaseName": dis["name"], "association_score": score}


def resolve_efo(disease_name):
    q = """
    query S($q: String!) {
      search(queryString: $q, entityNames: ["disease"]) { hits { id name } }
    }"""
    hits = ot_query(q, {"q": disease_name}).get("search", {}).get("hits", [])
    return hits[0]["id"] if hits else None


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="gene symbol or Ensembl gene ID")
    ap.add_argument("--disease", default=None, help="disease name (resolved to EFO)")
    ap.add_argument("--efo", default=None, help="EFO/MONDO ID (skips name resolution)")
    ap.add_argument("--out", default=None, help="output JSON path")
    args = ap.parse_args()

    target_rec, uniprot = resolve_target(args.target)
    ensembl = target_rec["id"]
    symbol = target_rec["approvedSymbol"]
    biotype = target_rec.get("biotype")
    print(f"[resolve] {args.target} -> {symbol} ({ensembl}); UniProt={uniprot}; biotype={biotype}")

    if biotype and biotype != "protein_coding":
        print(f"[WARN] {symbol} biotype is '{biotype}', not protein_coding. This skill targets "
              f"human protein-coding targets; structural/pocket steps may not apply.",
              file=sys.stderr)

    core, dataver = get_core(ensembl)
    tract = summarize_tractability(core.get("tractability"))
    depmap = summarize_depmap(core)
    drugs = get_drugs(ensembl)

    print(f"[tractability] modalities: " +
          ", ".join(f"{m}={v['n_true']}/{v['n_total']}" for m, v in tract.items()))
    print(f"[essentiality] isEssential={core.get('isEssential')}; "
          f"screens={depmap.get('n_screens')}; mean_gene_effect={depmap.get('mean_gene_effect')}")
    print(f"[safety] {len(core.get('safetyLiabilities') or [])} curated liabilities")
    print(f"[drugs] {drugs['count']} known drugs/candidates; "
          f"top: {', '.join(r['name'] for r in drugs['rows'][:5])}")

    assoc = None
    if args.disease or args.efo:
        efo = args.efo or resolve_efo(args.disease)
        if efo:
            assoc = get_target_disease_association(ensembl, efo)
            print(f"[disease] {args.disease or efo} -> {efo}; "
                  f"assoc_score={assoc.get('association_score') if assoc else None}")
        else:
            print(f"[disease] could not resolve '{args.disease}' to an EFO ID")

    result = {
        "target": {"symbol": symbol, "ensembl": ensembl, "uniprot": uniprot,
                   "approvedName": target_rec.get("approvedName"), "biotype": biotype},
        "data_version": dataver,
        "isEssential": core.get("isEssential"),
        "tractability": tract,
        "depmap_essentiality": depmap,
        "safety_liabilities": core.get("safetyLiabilities") or [],
        "drugs": drugs,
        "disease_association": assoc,
    }
    out = args.out or f"/mnt/results/data/{symbol}_opentargets.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2)
    print(f"[done] wrote {out}")
    return result


if __name__ == "__main__":
    main()
