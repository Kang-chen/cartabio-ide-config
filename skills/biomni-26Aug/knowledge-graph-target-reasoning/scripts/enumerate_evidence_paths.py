#!/usr/bin/env python3
"""
enumerate_evidence_paths.py
===========================
Explain the top-ranked targets with human-readable, multi-hop evidence paths
through PrimeKG. Every top hit from rank_kg_targets.py should be auditable rather
than taken on faith; this script enumerates the graph paths that justify each one.

Path templates (kind, score)
----------------------------
  direct_seed    (3): disease --[disease_protein]--> target        (target is a seed)
  ppi_bridge     (2): disease --[disease_protein]--> seed --[PPI]--> target
  shared_concept (1): disease --> seed --[shared pathway/process/molfunc]--> target
  drug_target    (2 if the drug is indicated/off-label for an anchor, else 1):
                      drug --[drug_protein]--> target, drug linked to an anchor

Output
------
JSON: { target_symbol: [ {kind, text, anchors, score, [bridge|concept|drug]} ] }
matching the structure consumed by make_figures.py and the PDF report. Paths are
sorted by score (strongest first) and de-duplicated.

Usage
-----
    python enumerate_evidence_paths.py \
        --primekg /mnt/datalake/primekg/primekg.csv \
        --ranked  ./out/ranked_targets.csv \
        --anchor-id "5011_5535" --anchor-name CD --anchor-disease-name "Crohn disease" \
        --anchor-id "5101"      --anchor-name UC --anchor-disease-name "ulcerative colitis (disease)" \
        --top 12 --out ./out/evidence_paths.json

--anchor-id / --anchor-name must match what was passed to rank_kg_targets.py.
--anchor-disease-name (optional, same order) is the human-readable disease label
used in the path text; if omitted, the anchor-name is used.
"""
import argparse, json, os
from collections import defaultdict
from itertools import combinations
import pandas as pd

GENE_VIA = {'pathway_protein', 'bioprocess_protein', 'molfunc_protein'}
MAX_CONCEPT = 200  # keep consistent with rank_kg_targets.py


def expand_ids(raw):
    return set(str(raw).split('_'))


def resolve_ids(anchor_map, dis_gene):
    """Resolve anchor ids against the parsed disease->gene map.

    PrimeKG disease node ids can themselves contain underscores (composite UMLS
    CUI groupings, e.g. the "Parkinson disease" node id is the single string
    "11764_11658_..._13167"). If the full raw id is itself a known disease node,
    keep it verbatim; only split on "_" when the full string is not a known node,
    which preserves the multi-node merge convention for ids like "5011_5535".

    Returns {anchor_name: set(resolved_ids)}.
    """
    out = {}
    for name, raw in anchor_map.items():
        raw = str(raw)
        if raw in dis_gene:
            out[name] = {raw}
        else:
            out[name] = set(raw.split('_'))
    return out


def parse(path, anchor_id_sets, target_ids, restricted_sources=None):
    """Collect only the graph pieces needed to explain the chosen targets.

    When `restricted_sources` is non-empty (commercial mode), edges whose
    x_source/y_source is restricted (e.g. DrugBank) are dropped, so the
    `drug_target` evidence-path template (which relies on DrugBank drug_protein /
    indication / off-label edges) is not emitted. Keeps the explanations
    license-consistent with the ranking.
    """
    restricted_sources = set(restricted_sources or ())
    dis_gene = defaultdict(set)     # disease id -> seed gene ids
    ppi = defaultdict(set)          # gene id -> neighbor gene ids
    gene_concept = defaultdict(set) # concept id -> gene ids
    drug_gene = defaultdict(set)    # drug id -> target gene ids
    drug_dis = defaultdict(lambda: defaultdict(set))  # rel -> disease id -> drug ids
    gene_name, drug_name = {}, {}
    all_anchor_ids = set().union(*anchor_id_sets.values()) if anchor_id_sets else set()
    usecols = ['relation', 'x_type', 'x_id', 'x_name', 'y_type', 'y_id', 'y_name',
               'x_source', 'y_source']
    KEEP = GENE_VIA | {'protein_protein', 'disease_protein', 'drug_protein',
                       'indication', 'off-label use'}
    for ch in pd.read_csv(path, usecols=usecols, chunksize=1_000_000, low_memory=False):
        ch['x_id'] = ch['x_id'].astype(str); ch['y_id'] = ch['y_id'].astype(str)
        sub = ch[ch['relation'].isin(KEEP)]
        for r in sub.itertuples(index=False):
            rel = r.relation
            if restricted_sources and (str(r.x_source) in restricted_sources
                                       or str(r.y_source) in restricted_sources):
                continue
            if r.x_type == 'gene/protein': gene_name[r.x_id] = r.x_name
            if r.y_type == 'gene/protein': gene_name[r.y_id] = r.y_name
            if r.x_type == 'drug': drug_name[r.x_id] = r.x_name
            if r.y_type == 'drug': drug_name[r.y_id] = r.y_name
            if rel == 'disease_protein':
                if r.x_type == 'disease' and r.y_type == 'gene/protein': dis_gene[r.x_id].add(r.y_id)
                elif r.y_type == 'disease' and r.x_type == 'gene/protein': dis_gene[r.y_id].add(r.x_id)
            elif rel == 'protein_protein':
                ppi[r.x_id].add(r.y_id); ppi[r.y_id].add(r.x_id)
            elif rel in GENE_VIA:
                if r.x_type == 'gene/protein' and r.y_type != 'gene/protein':
                    gene_concept[r.y_id].add(r.x_id)
                elif r.y_type == 'gene/protein' and r.x_type != 'gene/protein':
                    gene_concept[r.x_id].add(r.y_id)
            elif rel == 'drug_protein':
                if r.x_type == 'drug' and r.y_type == 'gene/protein': drug_gene[r.x_id].add(r.y_id)
                elif r.y_type == 'drug' and r.x_type == 'gene/protein': drug_gene[r.y_id].add(r.x_id)
            elif rel in ('indication', 'off-label use'):
                if r.x_type == 'drug' and r.y_type == 'disease': drug_dis[rel][r.y_id].add(r.x_id)
                elif r.y_type == 'drug' and r.x_type == 'disease': drug_dis[rel][r.x_id].add(r.y_id)
    return dict(dis_gene=dis_gene, ppi=ppi, gene_concept=gene_concept,
                drug_gene=drug_gene, drug_dis=drug_dis,
                gene_name=gene_name, drug_name=drug_name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--primekg', required=True)
    ap.add_argument('--ranked', required=True, help='ranked_targets.csv from rank_kg_targets.py')
    ap.add_argument('--anchor-id', action='append', required=True)
    ap.add_argument('--anchor-name', action='append', required=True)
    ap.add_argument('--anchor-disease-name', action='append', default=[])
    ap.add_argument('--top', type=int, default=12)
    ap.add_argument('--out', default='./evidence_paths.json')
    ap.add_argument('--edge-license', choices=['commercial', 'academic'], default='commercial',
                    help="'commercial' (default) drops restricted-source edges so no DrugBank "
                         "drug_target paths are emitted; 'academic' keeps them.")
    ap.add_argument('--restricted-sources', default='DrugBank,DisGeNET,KEGG',
                    help='Comma-separated restricted PrimeKG NODE-vocabulary sources (commercial '
                         'mode only). Note: does not clear DisGeNET edge-evidence provenance; use '
                         '--seeds-file to replace DisGeNET-derived seeds.')
    ap.add_argument('--seeds-file', default=None,
                    help='Commercial-safe seed list (same file as rank_kg_targets.py --seeds-file) '
                         'that REPLACES DisGeNET disease_protein seeds when building direct_seed / '
                         'ppi_bridge / shared_concept path text, so paths match the ranking.')
    args = ap.parse_args()

    restricted = set(s.strip() for s in args.restricted_sources.split(',') if s.strip()) \
        if args.edge_license == 'commercial' else set()
    if restricted:
        print(f"[license] COMMERCIAL mode: excluding restricted sources {sorted(restricted)} "
              "(no drug_target evidence paths).")

    anchors = dict(zip(args.anchor_name, args.anchor_id))
    dnames = dict(zip(args.anchor_name, args.anchor_disease_name)) if args.anchor_disease_name else {}

    df = pd.read_csv(args.ranked)
    top = df.head(args.top)
    target_ids = {str(r.gene_id): r.gene for r in top.itertuples(index=False)}
    id2sym = {str(gid): sym for gid, sym in target_ids.items()}

    kg = parse(args.primekg, {}, set(target_ids), restricted_sources=restricted)
    # Resolve anchor ids against the parsed disease->gene map. PrimeKG disease
    # node ids can themselves contain underscores (composite UMLS CUI groupings),
    # so check the full raw string first and only split when it is not a known
    # single node. This preserves multi-node merge for ids like "5011_5535" while
    # correctly handling single nodes like "11764_11658_..._13167".
    anchor_id_sets = resolve_ids(anchors, kg['dis_gene'])
    # per-anchor seed sets + a display name per anchor
    if args.seeds_file:
        # Commercial-safe: build seeds from the external (e.g. Open Targets, CC0)
        # symbol list mapped to PrimeKG gene ids, replacing DisGeNET disease_protein
        # seeds so evidence paths match the commercial ranking.
        import json as _json
        txt = open(args.seeds_file).read().strip()
        if txt[:1] in '{[':
            obj = _json.loads(txt); raw = obj.get('seeds', obj) if isinstance(obj, dict) else obj
            syms = [(e[0] if isinstance(e, (list, tuple)) else e) for e in raw]
        else:
            syms = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        sym2id = {}
        for gid, s in kg['gene_name'].items():
            sym2id.setdefault(s, gid)
        seed_ids = {sym2id[s] for s in syms if s in sym2id}
        anchor_seeds = {k: set(seed_ids) for k in anchors}
        print(f"[seeds] evidence paths use {len(seed_ids)} external seeds "
              f"(replacing DisGeNET disease_protein seeds)")
    else:
        anchor_seeds = {k: set().union(*[kg['dis_gene'].get(d, set()) for d in ids])
                        for k, ids in anchor_id_sets.items()}
    anchor_disp = {k: (dnames.get(k) or k) for k in anchors}
    # invert concepts -> for a target, which concepts is it in
    gene_in_concept = defaultdict(set)
    for cid, gs in kg['gene_concept'].items():
        if len(gs) < 2 or len(gs) > MAX_CONCEPT:
            continue
        for g in gs:
            gene_in_concept[g].add(cid)
    # drugs linked to any anchor (indicated / off-label)
    anchor_drugs = set()
    for ids in anchor_id_sets.values():
        for d in ids:
            anchor_drugs |= kg['drug_dis']['indication'].get(d, set())
            anchor_drugs |= kg['drug_dis']['off-label use'].get(d, set())
    indicated_drugs = set()
    for ids in anchor_id_sets.values():
        for d in ids:
            indicated_drugs |= kg['drug_dis']['indication'].get(d, set())

    out = {}
    for tid, tsym in target_ids.items():
        paths = []
        seen = set()

        def add(kind, text, ancs, score, **extra):
            key = (kind, text)
            if key in seen:
                return
            seen.add(key)
            rec = dict(kind=kind, text=text, anchors=sorted(ancs), score=score)
            rec.update(extra)
            paths.append(rec)

        # seed-edge label: PrimeKG disease_protein (DisGeNET) by default, or the
        # commercial-safe genetic-association edge when seeds are externally supplied
        _sedge = 'genetic assoc' if args.seeds_file else 'disease_protein'
        # A: direct seed
        for k in anchors:
            if tid in anchor_seeds[k]:
                add('direct_seed', f"{anchor_disp[k]} \u2014[{_sedge}]\u2192 {tsym}", [k], 3)
        # B: PPI bridge (disease -> seed -> PPI -> target)
        tgt_ppi = kg['ppi'].get(tid, set())
        for k in anchors:
            for seed in anchor_seeds[k] & tgt_ppi:
                if seed == tid:
                    continue
                bsym = kg['gene_name'].get(seed, seed)
                add('ppi_bridge',
                    f"{anchor_disp[k]} \u2014[{_sedge}]\u2192 {bsym} \u2014[PPI]\u2192 {tsym}",
                    [k], 2, bridge=bsym)
        # C: shared concept (disease -> seed -> shared concept -> target)
        tgt_concepts = gene_in_concept.get(tid, set())
        for k in anchors:
            for seed in anchor_seeds[k]:
                if seed == tid:
                    continue
                shared = gene_in_concept.get(seed, set()) & tgt_concepts
                if shared:
                    ssym = kg['gene_name'].get(seed, seed)
                    add('shared_concept',
                        f"{anchor_disp[k]} \u2014[{_sedge}]\u2192 {ssym} \u2014[shared pathway/process]\u2192 {tsym}",
                        [k], 1, bridge=ssym)
        # D: drug -> target (drug linked to an anchor)
        for drug in anchor_drugs:
            if tid in kg['drug_gene'].get(drug, set()):
                dname = kg['drug_name'].get(drug, drug)
                sc = 2 if drug in indicated_drugs else 1
                add('drug_target', f"{dname} \u2014[drug_protein]\u2192 {tsym}",
                    sorted(anchors), sc, drug=dname)

        paths.sort(key=lambda p: -p['score'])
        out[tsym] = paths

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    n_paths = sum(len(v) for v in out.values())
    print(f"wrote {args.out}: {len(out)} targets, {n_paths} evidence paths")
    for sym, ps in out.items():
        kinds = defaultdict(int)
        for p in ps:
            kinds[p['kind']] += 1
        print(f"  {sym:<10} {dict(kinds)}")


if __name__ == '__main__':
    main()
