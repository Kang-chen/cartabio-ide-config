#!/usr/bin/env python3
"""
rank_kg_targets.py
==================
Rank candidate therapeutic *targets* (genes/proteins) for ANY disease by
multi-hop reasoning over the PrimeKG biomedical knowledge graph, with an optional
TxGNN drug-repurposing evidence layer. Disease-agnostic: the disease is specified
entirely by its PrimeKG anchor node id(s) (see find_disease_anchors.py).

Method
------
1. Build a gene-gene network from PrimeKG: protein_protein (PPI) backbone plus
   genes projected together when they share a pathway / biological_process /
   molecular_function concept (generic concepts > MAX_CONCEPT genes are dropped;
   concept edges are down-weighted by CONCEPT_SCALE / (n - 1)).
2. Seeds = genes with a direct `disease_protein` edge to each anchor disease node.
   A gene seeding several anchors gets proportionally higher initial mass.
3. Random-Walk-with-Restart (RWR) network propagation from the seeds -> per-gene
   propagation score (primary ranking signal).
4. TxGNN evidence layer (optional): take the top-N TxGNN-predicted drugs for each
   anchor disease, map them to protein targets via `drug_protein`, and aggregate
   their TxGNN scores per gene -> drug-target support score.
5. Combined score = W_RWR * rank_norm(RWR) + W_TX * rank_norm(TxGNN-support).
   Genes are annotated known-vs-novel (target of any drug indicated/off-label for
   the disease) and flagged as likely ADME/PK proteins.

This is a *prioritization*, not a statistical test: outputs are discovery-stage
target hypotheses. Rankings are topology-biased toward high-degree hub genes; the
`degree` column and the TxGNN / known-target annotations are provided as partial
orthogonal checks. Run check_enrichment.py afterwards for a face-validity check.

Usage
-----
    python rank_kg_targets.py \
        --primekg "${PRIMEKG_CSV}" \
        --txgnn-pred "${TXGNN_PREDICTIONS}" \
        --anchor-id "5011_5535" --anchor-name CD --anchor-tx-key "Crohn disease" \
        --anchor-id "5101"      --anchor-name UC --anchor-tx-key "ulcerative colitis (disease)" \
        --out ./out

`--anchor-id` is the PrimeKG `x_id`/`y_id` of a disease node; discover them with
find_disease_anchors.py (or grep the CSV for the disease name). Anchor ids may be
underscore-joined groups (e.g. "5011_5535") to merge several PrimeKG disease
nodes that represent the same clinical entity. `--anchor-tx-key` (optional,
repeatable, same order) maps an anchor to its TxGNN prediction dict key (a disease
*name*); if omitted or "" the TxGNN layer for that anchor is skipped and the
ranking is RWR-only.

This workflow uses PrimeKG and optional TxGNN predictions.

Author: Phylo Biomni. Tested on PrimeKG (8.1M edges) + TxGNN precomputed predictions.
"""
import argparse, os, pickle, time, json
from collections import defaultdict
from itertools import combinations
import numpy as np
import pandas as pd
import scipy.sparse as sp

# ----------------------------- parameters (documented defaults) -----------------------------
RESTART      = 0.30    # RWR restart probability
W_RWR, W_TX  = 0.70, 0.30
TOP_DRUGS    = 50      # top TxGNN drugs per anchor to map to targets
MAX_CONCEPT  = 200     # drop pathway/process/molfunc concepts larger than this (too generic)
CONCEPT_SCALE= 0.50    # weight of concept-projected gene-gene edges relative to PPI (=1.0)
CONV_TOL     = 1e-10

GENE_GENE = {'protein_protein'}
GENE_VIA  = {'pathway_protein', 'bioprocess_protein', 'molfunc_protein'}
DIS_GENE  = {'disease_protein'}
DRUG_GENE = {'drug_protein'}
DRUG_DIS  = {'indication', 'off-label use', 'contraindication'}
KEEP = GENE_GENE | GENE_VIA | DIS_GENE | DRUG_GENE | DRUG_DIS

ADME_PREFIX = ('CYP', 'UGT', 'SLCO', 'ABCB', 'ABCC', 'ABCG', 'GST')
ADME_EXACT  = {'ALB', 'SERPINA6', 'ORM1', 'ORM2', 'AHR'}


def is_adme(sym):
    return sym in ADME_EXACT or any(sym.startswith(p) for p in ADME_PREFIX)


def expand_anchor_ids(anchor_map):
    """Anchor ids may be underscore-joined groups of PrimeKG disease node ids.
    Returns {anchor_name: set(individual_ids)}.

    NOTE: PrimeKG disease node ids can themselves contain underscores (they are
    composite UMLS CUI groupings, e.g. the "Parkinson disease" node id is the
    single string "11764_11658_..._13167"). Blindly splitting on "_" fragments
    such single ids into non-existent ids and yields 0 seeds. Use
    resolve_anchor_ids() after parsing the graph instead — it checks the full raw
    string against the parsed dis_gene map and only splits when the full string is
    not itself a known disease node.
    """
    out = {}
    for name, raw in anchor_map.items():
        out[name] = set(str(raw).split('_'))
    return out


def resolve_anchor_ids(anchor_map, dis_gene):
    """Resolve anchor ids against the parsed disease->gene map.

    For each anchor, if the full raw id string is itself a known PrimeKG disease
    node (present in dis_gene), keep it verbatim — it is a single node whose id
    happens to contain underscores. Only split on "_" when the full string is NOT
    a known node, which preserves the multi-node merge convention for ids like
    "5011_5535" (two genuinely separate nodes).

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


def parse_primekg(path, anchor_id_sets, restricted_sources=None):
    """Stream PrimeKG once, collecting the components needed for target ranking.

    Provenance-aware licensing filter
    ---------------------------------
    PrimeKG integrates ~20 upstream resources; each edge carries `x_source` and
    `y_source` provenance columns. When `restricted_sources` is a non-empty set
    (commercial mode), any edge whose x_source OR y_source is in that set is
    DROPPED before it can enter the gene-gene graph, the seed/known-target maps,
    or the drug layer. This keeps license-restricted edges (e.g. DrugBank) out of
    the ranking entirely, rather than silently shipping them. The RWR network
    backbone (NCBI protein_protein), GO/Reactome concept edges, and MONDO
    disease_protein seeds are unaffected. Returns per-source kept/dropped tallies
    in `source_stats` for provenance logging. When `restricted_sources` is falsy
    (academic mode), behaviour is identical to the original (no filtering).
    """
    restricted_sources = set(restricted_sources or ())
    all_anchor_ids = set()
    for s in anchor_id_sets.values():
        all_anchor_ids |= s
    ppi_edges = []
    gene_concept = defaultdict(set)
    dis_gene = defaultdict(set)
    drug_gene = defaultdict(set)
    drug_dis = defaultdict(lambda: defaultdict(set))
    gene_name, drug_name = {}, {}
    # provenance accounting: relation -> {'kept': n, 'dropped': n}; source -> counts
    rel_kept = defaultdict(int); rel_dropped = defaultdict(int)
    src_kept = defaultdict(int); src_dropped = defaultdict(int)
    usecols = ['relation', 'x_type', 'x_id', 'x_name', 'y_type', 'y_id', 'y_name',
               'x_source', 'y_source']
    for ch in pd.read_csv(path, usecols=usecols, chunksize=1_000_000, low_memory=False):
        ch['x_id'] = ch['x_id'].astype(str); ch['y_id'] = ch['y_id'].astype(str)
        sub = ch[ch['relation'].isin(KEEP)]
        for r in sub.itertuples(index=False):
            rel = r.relation
            # --- provenance / licensing filter ---
            xs, ys = str(r.x_source), str(r.y_source)
            if restricted_sources and (xs in restricted_sources or ys in restricted_sources):
                rel_dropped[rel] += 1
                for s in {xs, ys} & restricted_sources:
                    src_dropped[s] += 1
                continue
            rel_kept[rel] += 1
            for s in {xs, ys}:
                src_kept[s] += 1
            if r.x_type == 'gene/protein': gene_name[r.x_id] = r.x_name
            if r.y_type == 'gene/protein': gene_name[r.y_id] = r.y_name
            if r.x_type == 'drug': drug_name[r.x_id] = r.x_name
            if r.y_type == 'drug': drug_name[r.y_id] = r.y_name
            if rel in GENE_GENE:
                ppi_edges.append((r.x_id, r.y_id))
            elif rel in GENE_VIA:
                if r.x_type == 'gene/protein' and r.y_type != 'gene/protein':
                    gene_concept[r.y_id].add(r.x_id)
                elif r.y_type == 'gene/protein' and r.x_type != 'gene/protein':
                    gene_concept[r.x_id].add(r.y_id)
            elif rel in DIS_GENE:
                if r.x_type == 'disease' and r.y_type == 'gene/protein': dis_gene[r.x_id].add(r.y_id)
                elif r.y_type == 'disease' and r.x_type == 'gene/protein': dis_gene[r.y_id].add(r.x_id)
            elif rel in DRUG_GENE:
                if r.x_type == 'drug' and r.y_type == 'gene/protein': drug_gene[r.x_id].add(r.y_id)
                elif r.y_type == 'drug' and r.x_type == 'gene/protein': drug_gene[r.y_id].add(r.x_id)
            elif rel in DRUG_DIS:
                if r.x_type == 'drug' and r.y_type == 'disease': drug_dis[rel][r.y_id].add(r.x_id)
                elif r.y_type == 'drug' and r.x_type == 'disease': drug_dis[rel][r.x_id].add(r.y_id)
    source_stats = dict(
        restricted_sources=sorted(restricted_sources),
        edges_kept_by_relation=dict(rel_kept),
        edges_dropped_by_relation=dict(rel_dropped),
        edges_kept_by_source=dict(src_kept),
        edges_dropped_by_source=dict(src_dropped),
    )
    return dict(ppi_edges=ppi_edges, gene_concept=gene_concept, dis_gene=dis_gene,
                drug_gene=drug_gene, drug_dis=drug_dis, gene_name=gene_name,
                drug_name=drug_name, source_stats=source_stats)


def build_adjacency(kg):
    universe = set()
    for a, b in kg['ppi_edges']: universe.add(a); universe.add(b)
    for gs in kg['gene_concept'].values(): universe |= gs
    for gs in kg['dis_gene'].values(): universe |= gs
    for gs in kg['drug_gene'].values(): universe |= gs
    genes = sorted(universe)
    gidx = {g: i for i, g in enumerate(genes)}
    N = len(genes)
    w = defaultdict(float)

    def add_edge(a, b, weight):
        if a == b or a not in gidx or b not in gidx: return
        i, j = gidx[a], gidx[b]
        if i > j: i, j = j, i
        w[(i, j)] += weight

    for a, b in kg['ppi_edges']:
        add_edge(a, b, 1.0)
    for gs in kg['gene_concept'].values():
        gs = [g for g in gs if g in gidx]
        n = len(gs)
        if n < 2 or n > MAX_CONCEPT: continue
        ww = CONCEPT_SCALE / (n - 1)
        for a, b in combinations(gs, 2):
            add_edge(a, b, ww)
    rows = np.fromiter((k[0] for k in w), dtype=np.int32, count=len(w))
    cols = np.fromiter((k[1] for k in w), dtype=np.int32, count=len(w))
    vals = np.fromiter((w[k] for k in w), dtype=np.float64, count=len(w))
    A = sp.coo_matrix((vals, (rows, cols)), shape=(N, N)).tocsr()
    A = A + A.T
    return genes, gidx, A


def run_rwr(A, p0, restart=RESTART, tol=CONV_TOL, max_iter=1000):
    deg = np.asarray(A.sum(axis=0)).ravel()
    deg[deg == 0] = 1.0
    W = A.dot(sp.diags(1.0 / deg))
    p = p0.copy()
    for it in range(max_iter):
        p_new = (1 - restart) * W.dot(p) + restart * p0
        if np.abs(p_new - p).sum() < tol:
            p = p_new; break
        p = p_new
    return p, it + 1


def rank_norm(x):
    """Tie-aware rank-normalization to [0, 1].

    Uses average ranks (scipy.stats.rankdata) rather than ordinal argsort so that
    equal values receive the SAME normalized score. This matters for the TxGNN
    term: the vast majority of genes have exactly-zero drug-target support, and
    ordinal ranking (argsort of argsort) would spread those tied zeros arbitrarily
    across the whole [0, 1] interval, injecting a meaningless tie-break into the
    combined score. With average ranks, all no-support genes collapse to one
    neutral value and their relative order is decided purely by the RWR term,
    while only genes with real drug-target support are lifted above that baseline.

    (This is the corrected implementation; do NOT revert to argsort-of-argsort.)
    """
    from scipy.stats import rankdata
    x = np.asarray(x, dtype=float)
    if len(x) <= 1:
        return np.zeros_like(x)
    return (rankdata(x, method='average') - 1) / (len(x) - 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--primekg', required=True)
    ap.add_argument('--txgnn-pred', default=None, help='TxGNN prediction pkl (optional)')
    ap.add_argument('--anchor-id', action='append', required=True, help='PrimeKG disease node id, underscore-joined for groups (repeatable)')
    ap.add_argument('--anchor-name', action='append', required=True, help='Short label per anchor (repeatable, same order)')
    ap.add_argument('--anchor-tx-key', action='append', default=[], help='TxGNN dict key (disease name) per anchor, same order; use "" to skip')
    ap.add_argument('--out', default='./kg_target_ranking')
    ap.add_argument('--restart', type=float, default=RESTART)
    ap.add_argument('--w-rwr', type=float, default=W_RWR)
    ap.add_argument('--w-tx', type=float, default=W_TX)
    ap.add_argument('--top-drugs', type=int, default=TOP_DRUGS)
    ap.add_argument('--edge-license', choices=['commercial', 'academic'], default='commercial',
                    help="'commercial' (default) drops edges from license-restricted sources "
                         "(--restricted-sources) using PrimeKG x_source/y_source provenance, and "
                         "force-disables the TxGNN layer (it needs DrugBank drug_protein edges). "
                         "'academic' keeps all edges + TxGNN (may ship restricted-source edges; "
                         "flag as needs_commercial_review).")
    ap.add_argument('--restricted-sources', default='DrugBank,DisGeNET,KEGG',
                    help="Comma-separated PrimeKG NODE-vocabulary source names dropped in "
                         "commercial mode via x_source/y_source (default: DrugBank,DisGeNET,KEGG). "
                         "IMPORTANT: these columns record NODE vocabularies, not the edge-EVIDENCE "
                         "source. This catches DrugBank (drug nodes carry DrugBank ids) but CANNOT "
                         "catch DisGeNET/KEGG, which are edge-evidence sources for disease_protein / "
                         "pathway edges whose node vocabularies are NCBI/MONDO/Reactome. To clear "
                         "DisGeNET-derived seeds for commercial use, replace the seeds with "
                         "--seeds-file (see below). See references/DATA_SOURCES.md.")
    ap.add_argument('--seeds-file', default=None,
                    help="Optional path to a commercial-safe seed list that REPLACES PrimeKG's "
                         "DisGeNET-derived disease_protein seeds. Accepts a plain text file (one "
                         "gene symbol per line) or a JSON with a 'seeds' list of [symbol, id] pairs "
                         "or symbols. REQUIRED for a genuinely commercial-safe ranking (e.g. Open "
                         "Targets genetic-association targets, CC0). Seeds are matched to PrimeKG "
                         "gene nodes by symbol; the network edges themselves stay commercial-safe "
                         "(NCBI/GO/Reactome).")
    ap.add_argument('--seeds-source-name', default=None,
                    help="Human-readable provenance for --seeds-file (e.g. 'Open Targets genetic "
                         "association (CC0)'); recorded in meta.json.")
    args = ap.parse_args()
    assert len(args.anchor_id) == len(args.anchor_name), "anchor-id and anchor-name must pair up"
    os.makedirs(args.out, exist_ok=True)
    anchors = dict(zip(args.anchor_name, args.anchor_id))
    tx_keys = dict(zip(args.anchor_name, args.anchor_tx_key)) if args.anchor_tx_key else {}

    restricted = set(s.strip() for s in args.restricted_sources.split(',') if s.strip()) \
        if args.edge_license == 'commercial' else set()
    if args.edge_license == 'commercial':
        print(f"[license] COMMERCIAL mode: dropping edges from restricted sources {sorted(restricted)}; "
              "TxGNN layer force-disabled (requires DrugBank).")
    else:
        print("[license] ACADEMIC mode: all edges + TxGNN retained (may include license-restricted "
              "sources; flag as needs_commercial_review).")

    t0 = time.time()
    kg = parse_primekg(args.primekg, {}, restricted_sources=restricted)
    genes, gidx, A = build_adjacency(kg)
    N = len(genes)
    print(f"[{time.time()-t0:.0f}s] graph: {N} genes, {A.nnz} nnz")

    # Resolve anchor ids against the parsed disease->gene map. PrimeKG disease
    # node ids can themselves contain underscores (composite UMLS CUI groupings),
    # so we check the full raw string first and only split when it is not a known
    # single node. This preserves multi-node merge for ids like "5011_5535" while
    # correctly handling single nodes like "11764_11658_..._13167".
    anchor_id_sets = resolve_anchor_ids(anchors, kg['dis_gene'])

    # seeds
    seed_count = np.zeros(N)
    seed_flags = {k: np.zeros(N, bool) for k in anchors}
    seeds_provenance = "PrimeKG disease_protein (DisGeNET-derived)"
    if args.seeds_file:
        # Commercial-safe path: REPLACE DisGeNET-derived seeds with an external
        # commercial-usable seed list (matched to PrimeKG gene nodes by symbol).
        # This is required because PrimeKG's disease_protein edges are curated from
        # DisGeNET (CC BY-NC-SA) and their node-vocabulary source columns (NCBI/
        # MONDO) do NOT expose that edge-evidence provenance, so the x_source/
        # y_source filter cannot remove them. Replacing the seed source at the
        # symbol level is what actually makes the ranking's foundation commercial.
        import json as _json
        syms = []
        txt = open(args.seeds_file).read().strip()
        if txt[:1] in '{[':
            obj = _json.loads(txt)
            raw = obj.get('seeds', obj) if isinstance(obj, dict) else obj
            for e in raw:
                syms.append((e[0] if isinstance(e, (list, tuple)) else e))
        else:
            syms = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        sym2id = {}
        for gid, s in kg['gene_name'].items():
            sym2id.setdefault(s, gid)
        anchor0 = list(anchors)[0]
        n_matched = 0
        for s in syms:
            gid = sym2id.get(s)
            if gid is not None and gid in gidx:
                seed_count[gidx[gid]] = 1; seed_flags[anchor0][gidx[gid]] = True
                n_matched += 1
        seeds_provenance = args.seeds_source_name or f"external seeds file ({args.seeds_file})"
        print(f"[seeds] REPLACED PrimeKG DisGeNET seeds with {n_matched}/{len(syms)} matched "
              f"external seeds from: {seeds_provenance}")
        if n_matched == 0:
            raise SystemExit("No external seeds matched PrimeKG gene symbols. Check --seeds-file.")
    else:
        for k, ids in anchor_id_sets.items():
            for did in ids:
                for g in kg['dis_gene'].get(did, set()):
                    if g in gidx:
                        seed_count[gidx[g]] += 1; seed_flags[k][gidx[g]] = True
        if args.edge_license == 'commercial':
            print("[license] WARNING: using PrimeKG disease_protein seeds, which are DisGeNET-derived "
                  "(CC BY-NC-SA, non-commercial). The x_source/y_source filter CANNOT remove this "
                  "edge-evidence provenance. For a genuinely commercial-safe ranking, supply "
                  "--seeds-file with commercial-usable seeds (e.g. Open Targets genetics, CC0).")
    n_seed_per_anchor = {k: int(seed_flags[k].sum()) for k in anchors}
    print("seeds per anchor:", n_seed_per_anchor)
    if seed_count.sum() == 0:
        raise SystemExit("No seed genes found. Check --anchor-id (or --seeds-file).")
    p0 = seed_count / seed_count.sum()
    rwr, n_iter = run_rwr(A, p0, restart=args.restart)
    print(f"[{time.time()-t0:.0f}s] RWR converged in {n_iter} iterations")

    # TxGNN layer. In commercial mode this is force-disabled: mapping TxGNN drugs
    # to their protein targets requires DrugBank-sourced drug_protein edges, which
    # are dropped by the licensing filter, so any TxGNN support would be empty
    # and/or reintroduce restricted provenance. RWR-only is the clean ranking.
    tx_support = np.zeros(N)
    n_tx_drugs = defaultdict(int)
    txgnn_enabled = bool(args.txgnn_pred and tx_keys and any(tx_keys.values())
                         and args.edge_license == 'academic')
    if args.edge_license == 'commercial' and args.txgnn_pred and tx_keys and any(tx_keys.values()):
        print("[license] TxGNN layer disabled in commercial mode (drug_protein edges are DrugBank).")
    if txgnn_enabled:
        with open(args.txgnn_pred, 'rb') as f:
            txpred = pickle.load(f)
        for k, ids in anchor_id_sets.items():
            key = tx_keys.get(k, '')
            if not key or key not in txpred: continue
            top = sorted(txpred[key].items(), key=lambda x: -x[1])[:args.top_drugs]
            for drug_id, score in top:
                for g in kg['drug_gene'].get(str(drug_id), set()):
                    if g in gidx:
                        tx_support[gidx[g]] += score; n_tx_drugs[g] += 1
        print(f"[{time.time()-t0:.0f}s] TxGNN layer: {int((tx_support>0).sum())} genes with drug-target support")
    else:
        print("TxGNN layer skipped (no --txgnn-pred / --anchor-tx-key): RWR-only ranking")

    # known target annotation
    known = np.zeros(N, bool)
    ind_drugs = set()
    for ids in anchor_id_sets.values():
        for did in ids:
            ind_drugs |= kg['drug_dis']['indication'].get(did, set())
            ind_drugs |= kg['drug_dis']['off-label use'].get(did, set())
    for d in ind_drugs:
        for g in kg['drug_gene'].get(d, set()):
            if g in gidx: known[gidx[g]] = True

    rwr_rn, tx_rn = rank_norm(rwr), rank_norm(tx_support)
    combined = args.w_rwr * rwr_rn + args.w_tx * tx_rn

    rows = []
    for i in range(N):
        g = genes[i]; sym = kg['gene_name'].get(g, g)
        rows.append(dict(gene_id=g, gene=sym, combined_score=combined[i],
                         rwr_score=rwr[i], rwr_rank_norm=rwr_rn[i],
                         txgnn_support=tx_support[i], txgnn_rank_norm=tx_rn[i],
                         is_seed=bool(seed_count[i] > 0), n_anchors=int(seed_count[i]),
                         **{f'seed_{k}': bool(seed_flags[k][i]) for k in anchors},
                         known_drug_target=bool(known[i]), likely_ADME_PK=is_adme(sym),
                         degree=int(A[i].nnz), n_txgnn_drugs=int(n_tx_drugs.get(g, 0))))
    df = pd.DataFrame(rows).sort_values('combined_score', ascending=False).reset_index(drop=True)
    df.insert(0, 'rank', np.arange(1, len(df) + 1))
    out_csv = os.path.join(args.out, 'ranked_targets.csv')
    df.to_csv(out_csv, index=False)
    print(f"[{time.time()-t0:.0f}s] wrote {out_csv} ({len(df)} genes)")

    # run provenance / metadata (consumed by make_figures.py --meta and the report)
    meta = dict(
        anchors=anchors,
        anchor_tx_keys={k: v for k, v in tx_keys.items() if v},
        n_genes=int(N),
        n_seeds_total=int((seed_count > 0).sum()),
        n_seeds_per_anchor=n_seed_per_anchor,
        n_known_targets=int(known.sum()),
        n_txgnn_supported=int((tx_support > 0).sum()),
        rwr_iterations=int(n_iter),
        params=dict(restart=args.restart, w_rwr=args.w_rwr, w_tx=args.w_tx,
                    top_drugs=args.top_drugs, max_concept=MAX_CONCEPT,
                    concept_scale=CONCEPT_SCALE),
        txgnn_used=bool(txgnn_enabled),
        edge_license=args.edge_license,
        restricted_sources=sorted(restricted),
        seeds_provenance=seeds_provenance,
        seeds_replaced=bool(args.seeds_file),
        provenance=kg.get('source_stats', {}),
        provenance_note=("x_source/y_source are NODE vocabularies, not edge-evidence sources; "
                         "they catch DrugBank (drug nodes) but NOT DisGeNET/KEGG edge provenance. "
                         "Commercial safety of the seed layer requires --seeds-file replacement."),
        runtime_sec=round(time.time() - t0, 1),
    )
    with open(os.path.join(args.out, 'meta.json'), 'w') as f:
        json.dump(meta, f, indent=2)
    print(f"[{time.time()-t0:.0f}s] wrote {os.path.join(args.out, 'meta.json')}")
    print(df.head(20)[['rank', 'gene', 'combined_score', 'rwr_score', 'txgnn_support',
                       'n_anchors', 'known_drug_target']].to_string(index=False))


if __name__ == '__main__':
    main()
