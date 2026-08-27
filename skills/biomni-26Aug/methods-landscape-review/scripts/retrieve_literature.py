#!/usr/bin/env python3
"""
retrieve_literature.py -- consolidate LiteratureSearch output into a deduplicated corpus.

LiteratureSearch is a Biomni *tool* invoked by the agent (not a Python API). Each call
appends structured records to /mnt/results/execution_trace/references.jsonl. This script:
  1. reads that references.jsonl (+ optional curated records JSON),
  2. maps records to the corpus schema (see references/schemas.md),
  3. dedups by normalized DOI, then normalized title,
  4. writes corpus.csv.

So the agent's flow is: run LiteratureSearch for each planned query -> run this script.

Usage:
  python retrieve_literature.py \
      --refs /mnt/results/execution_trace/references.jsonl \
      --curated curated_records.json \
      --out corpus.csv
"""
import argparse, json, re, os, csv, sys

CORPUS_COLS = [
    "source", "pmid", "doi", "doi_source", "title", "authors", "journal",
    "publication_date", "publication_type", "keywords", "abstract",
    "is_open_access", "is_preprint", "url", "citation_count",
]
PREPRINT_MARKERS = ("arxiv", "biorxiv", "medrxiv", "10.48550", "10.1101")


def norm_doi(doi):
    if not doi:
        return ""
    d = str(doi).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d.strip()


def norm_title(t):
    if not t:
        return ""
    return re.sub(r"[^a-z0-9 ]", "", str(t).lower()).strip()


def _authors_to_str(a):
    if isinstance(a, list):
        return "; ".join(str(x) for x in a)
    return str(a or "")


def _is_preprint(doi, ptype=""):
    d = norm_doi(doi)
    blob = (d + " " + str(ptype or "")).lower()
    return any(m in blob for m in PREPRINT_MARKERS)


def record_from_litsearch(r):
    """Map one references.jsonl record to the corpus schema."""
    doi = norm_doi(r.get("doi"))
    return {
        "source": f"literature_search:{r.get('provider','')}".rstrip(":"),
        "pmid": r.get("pmid", "") or "",
        "doi": doi,
        "doi_source": "record" if doi else "",
        "title": (r.get("title") or "").strip(),
        "authors": _authors_to_str(r.get("authors")),
        "journal": r.get("journal", "") or "",
        "publication_date": str(r.get("year", "") or ""),
        "publication_type": r.get("study_type", "") or "",
        "keywords": "",
        "abstract": (r.get("abstract") or "").strip(),
        "is_open_access": bool(r.get("is_open_access", False)),
        "is_preprint": _is_preprint(doi, r.get("study_type", "")),
        "url": r.get("url", "") or (f"https://doi.org/{doi}" if doi else ""),
        "citation_count": r.get("citation_count", "") or "",
    }


def record_from_curated(r):
    """Curated records: accept either corpus-schema dicts or litsearch-like dicts."""
    if "source" in r and "publication_date" in r:  # already corpus schema
        out = {c: r.get(c, "") for c in CORPUS_COLS}
        out["doi"] = norm_doi(out.get("doi"))
        out["source"] = out.get("source") or "literature_curated"
        return out
    out = record_from_litsearch(r)
    out["source"] = "literature_curated"
    return out


def consolidate(refs_path=None, curated_path=None):
    rows = []
    if refs_path and os.path.exists(refs_path):
        with open(refs_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(record_from_litsearch(json.loads(line)))
                except json.JSONDecodeError:
                    continue
    if curated_path and os.path.exists(curated_path):
        with open(curated_path) as f:
            cur = json.load(f)
        for r in (cur if isinstance(cur, list) else [cur]):
            rows.append(record_from_curated(r))

    # dedup: curated first (so they win ties), then by DOI, then by title
    rows.sort(key=lambda x: 0 if x["source"].startswith("literature_curated") else 1)
    seen_doi, seen_title, out = set(), set(), []
    for r in rows:
        d, t = r["doi"], norm_title(r["title"])
        if d and d in seen_doi:
            continue
        if not d and t and t in seen_title:
            continue
        if d:
            seen_doi.add(d)
        if t:
            seen_title.add(t)
        out.append(r)
    return out


def write_corpus(rows, out_path):
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CORPUS_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in CORPUS_COLS})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refs", default="/mnt/results/execution_trace/references.jsonl")
    ap.add_argument("--curated", default="", help="optional curated records JSON")
    ap.add_argument("--out", default="corpus.csv")
    args = ap.parse_args(argv)

    rows = consolidate(args.refs, args.curated or None)
    write_corpus(rows, args.out)
    n_pre = sum(1 for r in rows if r["is_preprint"])
    n_abs = sum(1 for r in rows if r["abstract"])
    print(f"Consolidated {len(rows)} unique records -> {args.out}")
    print(f"  with abstract: {n_abs} | preprints: {n_pre}")
    return rows


if __name__ == "__main__":
    main()
