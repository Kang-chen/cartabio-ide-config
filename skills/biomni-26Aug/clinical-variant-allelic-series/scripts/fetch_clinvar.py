#!/usr/bin/env python3
"""
fetch_clinvar.py — Fetch and parse the ClinVar variant catalog for one human gene.

Uses NCBI E-utilities (esearch + esummary). ClinVar's current summary schema
splits classifications into three axes:
  - germline_classification        (Pathogenic / Likely pathogenic / VUS / ...)
  - clinical_impact_classification (drug response, etc. — somatic/therapeutic)
  - oncogenicity_classification    (Oncogenic / Likely oncogenic / ...)

Output: a tidy CSV, one row per ClinVar variation record for the gene.

Usage:
    python fetch_clinvar.py --gene EGFR --outdir /mnt/results/EGFR_allelic_series
    # optional: --email you@lab.org --api-key <NCBI_KEY>  (raises rate limit)

Verified against a live EGFR run: term "EGFR[gene]" -> 4093 variation UIDs.
Endpoints and parameters confirmed real (see references/data_sources.md).
"""
import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# One-letter <-> three-letter amino-acid maps (for protein-change normalization)
AA3TO1 = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q",
    "Glu": "E", "Gly": "G", "His": "H", "Ile": "I", "Leu": "L", "Lys": "K",
    "Met": "M", "Phe": "F", "Pro": "P", "Ser": "S", "Thr": "T", "Trp": "W",
    "Tyr": "Y", "Val": "V", "Ter": "*", "Sec": "U",
}


def _params(extra, email=None, api_key=None):
    p = dict(extra)
    if email:
        p["email"] = email
    if api_key:
        p["api_key"] = api_key
    return p


def _sleep(api_key):
    # NCBI allows 3 req/s without a key, 10 req/s with a key.
    time.sleep(0.11 if api_key else 0.34)


def esearch_uids(gene, email=None, api_key=None, retmax=100000):
    """Return all ClinVar variation UIDs for <gene>[gene]."""
    r = requests.get(
        f"{EUTILS}/esearch.fcgi",
        params=_params(
            {"db": "clinvar", "term": f"{gene}[gene]", "retmax": retmax, "retmode": "json"},
            email, api_key,
        ),
        timeout=60,
    )
    r.raise_for_status()
    res = r.json()["esearchresult"]
    uids = res.get("idlist", [])
    print(f"[clinvar] esearch '{gene}[gene]' -> {res.get('count')} records, "
          f"{len(uids)} UIDs fetched")
    return uids


def _protein_change_from_name(name):
    """
    Extract a compact protein change (e.g. 'T790M', 'L858R', 'E746_A750del')
    from a ClinVar preferred name that contains a p. HGVS term.
    Returns '' if none found.
    """
    if not name:
        return ""
    m = re.search(r"\(p\.([^)]+)\)", name)
    if not m:
        return ""
    p = m.group(1).strip()
    # Convert 3-letter to 1-letter codes where present.
    def repl(mm):
        return AA3TO1.get(mm.group(0), mm.group(0))
    p1 = re.sub(r"[A-Z][a-z]{2}", repl, p)
    return p1


def _first_codon(protein_change):
    """First integer following a letter = start codon (point subs AND range indels)."""
    if not protein_change:
        return None
    m = re.search(r"[A-Za-z](\d+)", protein_change.strip())
    return int(m.group(1)) if m else None


def _text(el, path, default=""):
    node = el.find(path)
    return node.text if node is not None and node.text is not None else default


def _parse_docsum_xml(xml_text):
    """
    Parse esummary XML (version 2.0) for db=clinvar into rows.
    XML is more robust than JSON here because nested classification blocks and
    variation-set fields are cleaner to walk.
    """
    rows = []
    root = ET.fromstring(xml_text)
    for ds in root.iter("DocumentSummary"):
        uid = ds.get("uid", "")
        title = _text(ds, "title")
        obj_type = _text(ds, "obj_type")  # e.g. 'single nucleotide variant', 'Indel'

        # Classifications (current schema)
        germ = _text(ds, "germline_classification/description")
        onco = _text(ds, "oncogenicity_classification/description")
        clin_impact = _text(ds, "clinical_impact_classification/description")

        # Review status / stars proxy (from germline block when present)
        review = _text(ds, "germline_classification/review_status")

        # Conditions / traits
        conds = []
        for trait in ds.iter("trait_name"):
            if trait.text:
                conds.append(trait.text)
        conditions = "; ".join(sorted(set(conds)))

        # Molecular consequence(s): live in <molecular_consequence_list><string>
        # at the DocumentSummary level (NOT inside variation_set).
        mcs = []
        mcl = ds.find("molecular_consequence_list")
        if mcl is not None:
            mcs = [s.text for s in mcl.findall("string") if s.text]
        molcons = "; ".join(sorted(set(mcs)))

        # Variation set: coordinates, HGVS, cdna
        cdna = ""
        canonical_spdi = ""
        vs = ds.find("variation_set/variation")
        if vs is not None:
            cdna = _text(vs, "cdna_change")
            canonical_spdi = _text(vs, "canonical_spdi")

        # ClinVar Variation ID == uid == numeric part of accession (VCV000666267).
        # This is the ID that CIViC's clinvar_ids column maps to. Do NOT use
        # measure_id (an internal measure identifier, a different number).
        var_id = uid

        # Protein change: prefer the explicit top-level <protein_change>. This
        # field may list the change across MULTIPLE transcripts, comma-separated
        # (e.g. "V600E, V512E, V578E, ..."); the FIRST token is the canonical/
        # preferred-transcript change, so keep only that. Fall back to parsing
        # the p. term from the title (which uses the preferred transcript).
        raw_pc = _text(ds, "protein_change").strip()
        first_pc = raw_pc.split(",")[0].strip() if raw_pc else ""
        if first_pc and re.match(r"^[A-Za-z*]\d+", first_pc):
            prot = first_pc                     # already compact, e.g. V600E
        elif "p." in first_pc:
            prot = _protein_change_from_name(first_pc)
        else:
            prot = _protein_change_from_name(title)
        codon = _first_codon(prot)

        rows.append({
            "uid": uid,
            "variation_id": var_id,
            "title": title,
            "variant_type": obj_type,
            "protein_change": prot,
            "residue_position": codon,
            "molecular_consequence": molcons,
            "cdna_change": cdna,
            "canonical_spdi": canonical_spdi,
            "germline_classification": germ,
            "oncogenicity_classification": onco,
            "clinical_impact_classification": clin_impact,
            "review_status": review,
            "conditions": conditions,
        })
    return rows


def esummary_records(uids, email=None, api_key=None, batch=300, retries=3):
    """Batched esummary (POST) -> parsed rows. Rate-limited and retried."""
    rows = []
    n = len(uids)
    for i in range(0, n, batch):
        chunk = uids[i:i + batch]
        ok = False
        for attempt in range(retries):
            try:
                r = requests.post(
                    f"{EUTILS}/esummary.fcgi",
                    data=_params(
                        {"db": "clinvar", "id": ",".join(chunk),
                         "retmode": "xml", "version": "2.0"},
                        email, api_key,
                    ),
                    timeout=120,
                )
                r.raise_for_status()
                rows.extend(_parse_docsum_xml(r.text))
                ok = True
                break
            except Exception as e:  # noqa: BLE001
                print(f"[clinvar] batch {i//batch} attempt {attempt+1} failed: {e}")
                time.sleep(1.5 * (attempt + 1))
        if not ok:
            print(f"[clinvar] WARNING: batch starting at {i} failed after {retries} tries")
        _sleep(api_key)
        print(f"[clinvar] esummary progress: {min(i+batch, n)}/{n}")
    return rows


def fetch(gene, outdir, email=None, api_key=None):
    os.makedirs(outdir, exist_ok=True)
    uids = esearch_uids(gene, email, api_key)
    if not uids:
        print(f"[clinvar] No ClinVar records for {gene}. Writing empty catalog.")
        df = pd.DataFrame()
    else:
        rows = esummary_records(uids, email, api_key)
        df = pd.DataFrame(rows)
    out_csv = os.path.join(outdir, f"{gene}_clinvar_full_catalog.csv")
    df.to_csv(out_csv, index=False)
    print(f"[clinvar] wrote {len(df)} records -> {out_csv}")

    # Quick provenance/summary sidecar
    summ = {
        "gene": gene,
        "clinvar_total": int(len(df)),
    }
    if len(df):
        germ = df["germline_classification"].fillna("")
        summ["clinvar_plp"] = int(germ.str.contains("athogenic", case=False).sum())
        summ["clinvar_missense"] = int(
            df["molecular_consequence"].fillna("").str.contains("missense", case=False).sum()
        )
        summ["clinvar_drugresponse"] = int(
            df["clinical_impact_classification"].fillna("").str.contains("drug", case=False).sum()
        )
        summ["clinvar_oncogenic"] = int(
            df["oncogenicity_classification"].fillna("").str.contains("ncogenic", case=False).sum()
        )
    with open(os.path.join(outdir, f"{gene}_clinvar_summary.json"), "w") as fh:
        json.dump(summ, fh, indent=2)
    return df


def main():
    ap = argparse.ArgumentParser(description="Fetch ClinVar catalog for a gene.")
    ap.add_argument("--gene", required=True, help="HGNC gene symbol, e.g. EGFR")
    ap.add_argument("--outdir", required=True, help="Output directory")
    ap.add_argument("--email", default=os.environ.get("NCBI_EMAIL"))
    ap.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY"))
    args = ap.parse_args()
    fetch(args.gene.upper(), args.outdir, args.email, args.api_key)


if __name__ == "__main__":
    sys.exit(main())
