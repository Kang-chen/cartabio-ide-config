#!/usr/bin/env python3
"""
plan_queries.py — build an anti-recency-bias LiteratureSearch query set for
direction-of-effect concordance: one query group per (axis, target).

The agent then runs each query with the Biomni `LiteratureSearch` tool (which writes
structured records to /mnt/results/execution_trace/references.jsonl). This script only
PLANS the queries; it does not call any search API.

Usage:
  python plan_queries.py --targets "PCSK9,SOST,PNPLA3" \
      --indications "hypercholesterolemia;osteoporosis;MASLD" \
      --axes "Human genetics,Functional/CRISPR,Drug MoA,Mouse KO" \
      --out RUN/queries.json
"""
import argparse, json, sys

# Per-axis query templates. {g}=gene symbol, {ind}=indication.
# Deliberately mix foundational + mechanism + recent phrasing; DO NOT set year_min high.
AXIS_TEMPLATES = {
    "Human genetics": [
        "{g} loss-of-function variants {ind} direction of effect protective or risk",
        "{g} rare coding variants gain-of-function versus loss-of-function {ind}",
        "{g} GWAS common variant {ind} risk allele effect direction",
    ],
    "Functional/CRISPR": [
        "{g} CRISPR knockout knockdown functional consequence {ind}",
        "{g} gene silencing siRNA antisense mechanism {ind}",
        "{g} cell line dependency essentiality {ind}",
    ],
    "Drug MoA": [
        "{g} inhibitor antagonist agonist activator {ind} clinical",
        "{g} approved drug mechanism of action {ind}",
        "{g} antibody small molecule oligonucleotide therapeutic {ind}",
    ],
    "Mouse KO": [
        "{g} knockout mouse phenotype {ind}",
        "{g} mouse model gene deletion {ind} liver bone cardiovascular metabolic",
        "{g} conditional knockout transgenic {ind} phenotype",
    ],
    # Optional axes the user may add:
    "Expression/eQTL": [
        "{g} expression eQTL {ind} tissue direction",
        "{g} mRNA protein level disease {ind} up or down regulated",
    ],
    "Protein network": [
        "{g} protein-protein interaction pathway {ind} mechanism",
    ],
}


def parse_pairs(targets, indications):
    tlist = [t.strip() for t in targets.split(",") if t.strip()]
    # indications may be ';'-separated (one per target) or a single shared value
    ilist = [i.strip() for i in indications.split(";")] if indications else [""]
    if len(ilist) == 1:
        ilist = ilist * len(tlist)
    if len(ilist) != len(tlist):
        sys.exit(f"ERROR: {len(tlist)} targets but {len(ilist)} indications. "
                 f"Give one shared indication or one per target (';'-separated).")
    return list(zip(tlist, ilist))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True, help="comma-separated gene symbols")
    ap.add_argument("--indications", required=True,
                    help="';'-separated (one per target) or a single shared value")
    ap.add_argument("--axes", default="Human genetics,Functional/CRISPR,Drug MoA,Mouse KO")
    ap.add_argument("--max-papers", type=int, default=8)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pairs = parse_pairs(args.targets, args.indications)
    axes = [a.strip() for a in args.axes.split(",") if a.strip()]

    plan = {"targets": [t for t, _ in pairs], "axes": axes, "queries": []}
    for g, ind in pairs:
        for axis in axes:
            tmpls = AXIS_TEMPLATES.get(axis)
            if not tmpls:
                # Unknown/custom axis: build one generic directional query.
                tmpls = ["{g} " + axis + " {ind} direction of effect"]
            for tmpl in tmpls:
                plan["queries"].append({
                    "target": g, "indication": ind, "axis": axis,
                    "query": tmpl.format(g=g, ind=ind or "disease"),
                    "max_papers": args.max_papers,
                    # No year_min on purpose (avoid burying foundational LoF/KO papers).
                })

    with open(args.out, "w") as fh:
        json.dump(plan, fh, indent=2)

    print(f"Planned {len(plan['queries'])} LiteratureSearch queries "
          f"({len(pairs)} targets x {len(axes)} axes) -> {args.out}")
    print("Run each 'query' with the Biomni LiteratureSearch tool. "
          "Do NOT set a high year_min (foundational papers are often 2005-2015).")


if __name__ == "__main__":
    main()
