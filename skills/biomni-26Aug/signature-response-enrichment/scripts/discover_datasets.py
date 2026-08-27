#!/usr/bin/env python3
"""
discover_datasets.py — Stage 1 of signature-response-enrichment.

Query public transcriptomic repositories for candidate cohorts to test whether a
gene signature's residual activity marks non-response to a drug in a disease.

Searches BOTH:
  - NCBI GEO via eutils (esearch/esummary on the `gds` database)
  - ArrayExpress / BioStudies via its REST API

and writes a candidate catalog CSV (all candidates, pre-curation). The AGENT then
applies inclusion rules (drug arm + longitudinal + per-patient response) and fills
the Decision/Reason/Role columns (Stage 2). This script does NOT auto-select.

Usage:
  python discover_datasets.py --drug adalimumab --disease psoriasis \
      --out /mnt/results/<run>/data/psoriasis_adalimumab_catalog.csv \
      [--email you@example.org] [--retmax 200] [--tissue "lesional skin"]

Notes:
  - Set NCBI_API_KEY in the environment to raise the eutils rate limit (optional).
  - This is a STARTING POINT. Real metadata is messy; the agent is expected to read
    each promising series' full record and refine tissue/timepoint/PASI fields by hand.
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BIOSTUDIES = "https://www.ebi.ac.uk/biostudies/api/v1/search"

CATALOG_COLUMNS = [
    "accession", "source", "title", "organism", "platform",
    "n_samples", "treatments", "tissue", "timepoints",
    "response_metric_in_metadata", "pmid",
    "decision", "role", "reason",  # <- filled by the agent in Stage 2
]


def _get(url, tries=4, pause=0.34):
    """GET with simple retry/backoff; returns decoded text or raises."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "biomni-skill/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - report and back off
            last = e
            time.sleep(pause * (i + 1))
    raise RuntimeError(f"GET failed after {tries} tries: {url}\n{last}")


# ----------------------------- GEO (eutils) -----------------------------------
def geo_search(drug, disease, retmax, email=None):
    """Return GEO series (GSE) summaries matching disease+drug."""
    api_key = os.environ.get("NCBI_API_KEY")
    term = f'("{disease}"[All Fields] AND "{drug}"[All Fields]) AND "gse"[Filter]'
    params = {"db": "gds", "term": term, "retmax": str(retmax), "retmode": "json"}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    url = f"{EUTILS}/esearch.fcgi?" + urllib.parse.urlencode(params)
    ids = json.loads(_get(url)).get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    rows = []
    # esummary in batches of 100
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        p = {"db": "gds", "id": ",".join(batch), "retmode": "json"}
        if api_key:
            p["api_key"] = api_key
        u = f"{EUTILS}/esummary.fcgi?" + urllib.parse.urlencode(p)
        res = json.loads(_get(u)).get("result", {})
        for uid in res.get("uids", []):
            d = res.get(uid, {})
            acc = d.get("accession", "")
            if not acc.startswith("GSE"):
                continue  # keep series only, skip GDS/GPL rows
            rows.append({
                "accession": acc,
                "source": "GEO",
                "title": d.get("title", ""),
                "organism": d.get("taxon", ""),
                "platform": d.get("gpl", ""),
                "n_samples": d.get("n_samples", ""),
                "treatments": "",  # not in esummary; agent fills from full record
                "tissue": "",
                "timepoints": "",
                "response_metric_in_metadata": "",
                "pmid": ";".join(str(x) for x in d.get("pubmedids", []) or []),
                "decision": "", "role": "", "reason": "",
            })
    return rows


# ------------------------- ArrayExpress / BioStudies --------------------------
def biostudies_search(drug, disease, retmax):
    """Return ArrayExpress/BioStudies studies matching disease+drug."""
    params = {"query": f"{disease} {drug}", "pageSize": str(min(retmax, 100)),
              "type": "study"}
    url = f"{BIOSTUDIES}?" + urllib.parse.urlencode(params)
    try:
        hits = json.loads(_get(url)).get("hits", [])
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[warn] BioStudies query failed: {e}\n")
        return []
    rows = []
    for h in hits:
        acc = h.get("accession", "")
        rows.append({
            "accession": acc,
            "source": "ArrayExpress/BioStudies",
            "title": h.get("title", ""),
            "organism": h.get("organism", ""),
            "platform": h.get("technology", h.get("assay_technology", "")),
            "n_samples": h.get("n_samples", ""),
            "treatments": "",
            "tissue": "",
            "timepoints": "",
            "response_metric_in_metadata": "",
            "pmid": "",
            "decision": "", "role": "", "reason": "",
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drug", required=True)
    ap.add_argument("--disease", required=True)
    ap.add_argument("--out", required=True, help="output catalog CSV path")
    ap.add_argument("--email", default=None, help="contact email for NCBI eutils")
    ap.add_argument("--retmax", type=int, default=200)
    ap.add_argument("--tissue", default=None, help="target tissue (recorded only)")
    args = ap.parse_args()

    sys.stderr.write(f"[discover] GEO: '{args.disease}' + '{args.drug}' ...\n")
    geo = geo_search(args.drug, args.disease, args.retmax, args.email)
    sys.stderr.write(f"[discover]   GEO candidates: {len(geo)}\n")

    sys.stderr.write("[discover] ArrayExpress/BioStudies ...\n")
    bs = biostudies_search(args.drug, args.disease, args.retmax)
    sys.stderr.write(f"[discover]   BioStudies candidates: {len(bs)}\n")

    # de-duplicate by accession, GEO taking precedence
    seen, rows = set(), []
    for r in geo + bs:
        a = r["accession"]
        if a and a not in seen:
            seen.add(a)
            rows.append(r)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CATALOG_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CATALOG_COLUMNS})

    sys.stderr.write(
        f"[discover] wrote {len(rows)} candidates -> {args.out}\n"
        "[discover] NEXT (agent, Stage 2): read each promising series' full record, "
        "fill treatments/tissue/timepoints/response_metric_in_metadata, then set "
        "decision (Included/Excluded), role (primary/validation/pharmacodynamic), "
        "and a one-line reason. ArrayExpress/BioStudies rows may include cohorts "
        "absent from GEO (e.g. PSORT E-MTAB-14509) - do not drop them.\n")


if __name__ == "__main__":
    main()
