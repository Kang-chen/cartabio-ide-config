#!/usr/bin/env python3
"""
find_disease_anchors.py
=======================
Resolve a free-text disease NAME to PrimeKG disease anchor node id(s) so the rest
of the pipeline (rank_kg_targets.py) can run. This is the one step that turns a
plain question ("targets for Parkinson's disease") into the graph coordinates the
ranker needs.

What it does
------------
- Streams PrimeKG once, collects every distinct `disease` node (id + name).
- Case-insensitive substring match against one or more --name queries (optional
  fuzzy match with --fuzzy for near-spellings).
- For each candidate disease node, reports the number of `disease_protein` seed
  genes it has -- the single most important number for picking an anchor, because
  a disease node with 0-1 seeds cannot drive a useful RWR.
- Prints a ready-to-paste --anchor-id / --anchor-name / --anchor-tx-key block.

It does NOT auto-select among ambiguous matches. Umbrella terms (e.g. a disease
and its subtypes) frequently appear as several nodes; review the candidates and
decide which id(s) to use, joining multiple ids for one clinical entity with
underscores (e.g. "5011_5535").

Usage
-----
    python find_disease_anchors.py \
        --primekg "${PRIMEKG_CSV}" \
        --name "Crohn" --name "ulcerative colitis" --name "inflammatory bowel"

    # then copy the printed --anchor-* block into rank_kg_targets.py

TxGNN keys
----------
The printed block leaves --anchor-tx-key blank. To enable the TxGNN layer, look up
the matching disease *name* key in the TxGNN prediction dict and use the TxGNN
name mapping to bridge names.
"""
import argparse, time
from collections import defaultdict
import pandas as pd


def collect_disease_nodes_and_seeds(path, queries, fuzzy=False, cutoff=0.72):
    """Single streaming pass: gather disease-node names and their seed-gene counts."""
    queries_lc = [q.lower() for q in queries]
    disease_names = {}          # id -> name
    seed_counts = defaultdict(set)  # disease id -> set(gene ids)
    usecols = ['relation', 'x_type', 'x_id', 'x_name', 'y_type', 'y_id', 'y_name']
    for ch in pd.read_csv(path, usecols=usecols, chunksize=1_000_000, low_memory=False):
        ch['x_id'] = ch['x_id'].astype(str); ch['y_id'] = ch['y_id'].astype(str)
        # disease node names (from any relation touching a disease)
        for side in ('x', 'y'):
            m = ch[ch[f'{side}_type'] == 'disease']
            for did, name in zip(m[f'{side}_id'], m[f'{side}_name']):
                disease_names[did] = name
        # disease_protein seed links
        dp = ch[ch['relation'] == 'disease_protein']
        for r in dp.itertuples(index=False):
            if r.x_type == 'disease' and r.y_type == 'gene/protein':
                seed_counts[r.x_id].add(r.y_id)
            elif r.y_type == 'disease' and r.x_type == 'gene/protein':
                seed_counts[r.y_id].add(r.x_id)

    # match
    def matches(name):
        nl = str(name).lower()
        if any(q in nl for q in queries_lc):
            return True
        if fuzzy:
            import difflib
            for q in queries_lc:
                if difflib.SequenceMatcher(None, q, nl).ratio() >= cutoff:
                    return True
        return False

    hits = [(did, name, len(seed_counts.get(did, set())))
            for did, name in disease_names.items() if matches(name)]
    hits.sort(key=lambda t: -t[2])  # most seeds first
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--primekg', required=True)
    ap.add_argument('--name', action='append', required=True, help='Disease name query (repeatable)')
    ap.add_argument('--fuzzy', action='store_true', help='Also allow near-spelling fuzzy matches')
    ap.add_argument('--min-seeds', type=int, default=1, help='Hide candidates with fewer seed genes than this')
    ap.add_argument('--max-show', type=int, default=40)
    args = ap.parse_args()

    t0 = time.time()
    hits = collect_disease_nodes_and_seeds(args.primekg, args.name, fuzzy=args.fuzzy)
    hits = [h for h in hits if h[2] >= args.min_seeds]
    print(f"[{time.time()-t0:.0f}s] {len(hits)} candidate disease node(s) "
          f"(>= {args.min_seeds} seed gene(s)) matching {args.name}\n")
    if not hits:
        print("No disease nodes matched. Try a shorter/alternative name, add --fuzzy, "
              "or lower --min-seeds. Absence here means PrimeKG lacks a matching node "
              "or seed genes for that term.")
        return

    print(f"{'node_id':<16}{'seeds':>6}  name")
    print('-' * 72)
    for did, name, n in hits[:args.max_show]:
        print(f"{did:<16}{n:>6}  {name}")

    # ready-to-paste block from the single best-seeded candidate
    top = hits[0]
    label = ''.join(w[:1].upper() for w in str(top[1]).split()[:3]) or 'DIS'
    print("\n# --- ready-to-paste starting point (edit ids/labels/tx-keys as needed) ---")
    print(f'  --anchor-id "{top[0]}" --anchor-name {label} --anchor-tx-key ""')
    print("# To merge several nodes into one clinical entity, underscore-join their ids:")
    joined = '_'.join(h[0] for h in hits[:min(3, len(hits))])
    print(f'#   --anchor-id "{joined}" --anchor-name {label} --anchor-tx-key ""')
    print("# Fill --anchor-tx-key with the disease's TxGNN dict key to enable the TxGNN layer.")


if __name__ == '__main__':
    main()
