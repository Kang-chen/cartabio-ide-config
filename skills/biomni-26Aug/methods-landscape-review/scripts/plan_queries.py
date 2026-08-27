#!/usr/bin/env python3
"""
plan_queries.py -- expand a task (and optional method set) into a multi-query plan
for LiteratureSearch, deliberately covering FOUNDATIONAL papers (anti-recency-bias),
benchmark/comparison papers, and recent advances.

The output is a JSON list of query objects; the agent runs each with the Biomni
LiteratureSearch tool. This script does NOT call any network API -- it only builds the
query plan, so it is trivially testable offline.

Usage:
  python plan_queries.py --task "differential expression for bulk RNA-seq" \
      --methods "DESeq2,edgeR,limma-voom" --mode comparison --out queries.json
"""
import argparse, json, sys


def _clean_list(s):
    if not s:
        return []
    return [x.strip() for x in s.replace(";", ",").split(",") if x.strip()]


def build_query_plan(task, methods=None, mode="comparison", extra_terms=None):
    task = (task or "").strip()
    methods = methods or []
    extra_terms = extra_terms or []
    queries = []

    def add(q, intent, year_min=None, note=""):
        q = " ".join(q.split())
        if not q:
            return
        # dedup on normalized query text
        key = q.lower()
        if any(x["_key"] == key for x in queries):
            return
        queries.append({
            "query": q, "intent": intent, "year_min": year_min,
            "note": note, "_key": key,
        })

    # --- Foundational / tool papers: one per named method, NO year_min ---
    # (Foundational papers are often 2009-2014; never filter them out.)
    for m in methods:
        add(f"{m} method algorithm {task}", "foundational", year_min=None,
            note=f"foundational/tool paper for {m}")
        add(f"{m} original paper", "foundational", year_min=None,
            note=f"original description of {m}")

    # --- Benchmark / comparison papers ---
    if methods:
        joined = " vs ".join(methods[:4])
        add(f"benchmark comparison {joined} {task}", "benchmark", year_min=None,
            note="head-to-head benchmark of the named methods")
    add(f"{task} benchmark comparison methods evaluation", "benchmark", year_min=None,
        note="general benchmark studies for the task")
    add(f"{task} false discovery rate accuracy sensitivity comparison", "benchmark",
        year_min=None, note="performance/accuracy comparisons")

    # --- Recent advances (a light recency query is fine as ONE of many) ---
    add(f"{task} new method state of the art", "recent", year_min=None,
        note="recent advances / newer entrants")

    # --- Topic-mode extra coverage ---
    if mode == "topic":
        add(f"{task} review", "review", year_min=None, note="reviews / syntheses")
        add(f"{task} evidence findings", "topic", year_min=None,
            note="primary findings on the topic")

    # --- User-supplied extra terms ---
    for t in extra_terms:
        add(f"{task} {t}", "extra", year_min=None, note=f"extra term: {t}")

    for q in queries:
        q.pop("_key", None)
    return {"task": task, "mode": mode, "methods": methods, "queries": queries}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", required=True)
    ap.add_argument("--methods", default="", help="comma-separated method/tool names")
    ap.add_argument("--mode", default="comparison", choices=["comparison", "topic"])
    ap.add_argument("--extra", default="", help="comma-separated extra query terms")
    ap.add_argument("--out", default="queries.json")
    args = ap.parse_args(argv)

    plan = build_query_plan(
        args.task, _clean_list(args.methods), args.mode, _clean_list(args.extra)
    )
    with open(args.out, "w") as f:
        json.dump(plan, f, indent=2)
    print(f"Wrote {len(plan['queries'])} queries -> {args.out}")
    for q in plan["queries"]:
        print(f"  [{q['intent']:11s}] {q['query']}")
    return plan


if __name__ == "__main__":
    main()
