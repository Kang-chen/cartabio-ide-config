#!/usr/bin/env python
"""
Assemble a references.json for the report from benchmark-context literature.

IMPORTANT — how citations are meant to flow in this skill:
  1. The AGENT runs the `LiteratureSearch` tool (NOT a Python API) for the model
     and dataset in play, e.g.:
        LiteratureSearch("scGPT perturbation prediction single cell")
        LiteratureSearch("GEARS multi-gene perturbation prediction")
        LiteratureSearch("<dataset> Perturb-seq CRISPR screen")
     Those calls append structured records to
        /mnt/results/execution_trace/references.jsonl
  2. This script reads that references.jsonl, de-duplicates by DOI/title, and
     writes a compact references.json the report driver embeds.
  3. If references.jsonl is absent (e.g. offline dry run), it falls back to the
     three ANCHOR references below. These three were verified via LiteratureSearch
     (real DOIs) and are the canonical citations for this benchmark; still prefer
     freshly-searched records when available so the reference list matches the
     specific model/dataset the user chose.

Only include a reference if it is genuinely relevant to the reported benchmark;
do not pad the list.
"""
import argparse, json, os
from pathlib import Path

# Verified via LiteratureSearch (see SKILL.md provenance note). Real DOIs.
ANCHORS = [
    {"key": "scgpt", "authors": "Cui H, Wang C, Maan H, et al.",
     "year": 2024, "title": "scGPT: toward building a foundation model for single-cell "
     "multi-omics using generative AI", "journal": "Nature Methods",
     "doi": "10.1038/s41592-024-02201-0"},
    {"key": "gears", "authors": "Roohani Y, Huang K, Leskovec J",
     "year": 2023, "title": "Predicting transcriptional outcomes of novel multigene "
     "perturbations with GEARS", "journal": "Nature Biotechnology",
     "doi": "10.1038/s41587-023-01905-6"},
    {"key": "norman", "authors": "Norman TM, Horlbeck MA, Replogle JM, et al.",
     "year": 2019, "title": "Exploring genetic interaction manifolds constructed from "
     "rich single-cell phenotypes", "journal": "Science",
     "doi": "10.1126/science.aax4438"},
]


def _norm(s):
    return "".join(c.lower() for c in (s or "") if c.isalnum())


def _fmt_authors(a):
    """Accept a string or a list of author names; return a clean 'A, B, et al.' string."""
    if isinstance(a, list):
        names = [str(x) for x in a if x]
        if len(names) > 3:
            names = names[:3] + ["et al."]
        return ", ".join(names)
    return str(a or "")


def read_jsonl(path):
    recs = []
    if not os.path.exists(path):
        return recs
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        # normalize to our record shape
        recs.append({
            "authors": _fmt_authors(r.get("authors") or r.get("author") or ""),
            "year": r.get("year"),
            "title": r.get("title") or r.get("source") or "",
            "journal": r.get("journal") or r.get("venue") or "",
            "doi": (r.get("doi") or "").replace("https://doi.org/", ""),
            "url": r.get("url") or (("https://doi.org/" + r["doi"]) if r.get("doi") else ""),
        })
    return recs


def dedupe(recs):
    seen, out = set(), []
    for r in recs:
        k = _norm(r.get("doi")) or _norm(r.get("title"))[:60]
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refs_jsonl", default="/mnt/results/execution_trace/references.jsonl",
                    help="Agent-produced LiteratureSearch records.")
    ap.add_argument("--out", default="/mnt/results/execution_trace/references.json")
    ap.add_argument("--max_refs", type=int, default=12)
    ap.add_argument("--anchors_only", action="store_true",
                    help="Ignore references.jsonl and emit only the verified anchors.")
    args = ap.parse_args()

    recs = [] if args.anchors_only else read_jsonl(args.refs_jsonl)
    if recs:
        # keep anchors first (by DOI match) then the rest, deduped
        anchor_dois = {_norm(a["doi"]) for a in ANCHORS}
        anchors_present = [r for r in recs if _norm(r.get("doi")) in anchor_dois]
        others = [r for r in recs if _norm(r.get("doi")) not in anchor_dois]
        merged = dedupe(ANCHORS_as_records(anchors_present) + others)[: args.max_refs]
        source = f"{args.refs_jsonl} (+anchors)"
    else:
        merged = ANCHORS
        source = "anchors fallback (no references.jsonl found)"

    json.dump({"references": merged, "source": source}, open(args.out, "w"), indent=2)
    print(f"[refs] {len(merged)} references -> {args.out}  [{source}]", flush=True)
    for i, r in enumerate(merged, 1):
        print(f"  [{i}] {r.get('authors','')} ({r.get('year','?')}) "
              f"{r.get('title','')[:70]} — {r.get('journal','')} {r.get('doi','')}", flush=True)


def ANCHORS_as_records(present):
    """Prefer freshly-searched anchor records; fall back to the hardcoded anchor text."""
    if present:
        return present
    return ANCHORS


if __name__ == "__main__":
    main()
