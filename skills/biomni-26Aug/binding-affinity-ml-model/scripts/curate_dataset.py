#!/usr/bin/env python3
"""
Step 1-2: Fetch ChEMBL bioactivity for the target(s), curate a drug-like
small-molecule regression dataset, and RUN THE DATA-REALITY GATE.

WHY THE GATE MATTERS (the key lesson this skill encodes):
Many targets look data-rich in ChEMBL but the drug-like small-molecule subset
is tiny because the chemical matter is peptides/macrocycles (PPI targets), or
the "obvious" endpoint is sparse. In the PCSK9 case the PPI IC50 endpoint gave
only ~30 drug-like molecules -- far too few for an honest GNN. This script
prints per-target / per-endpoint counts and STOPS with a clear message if the
final set is below `min_compounds`, so you re-scope (pool endpoints, add the
single-protein target) BEFORE wasting effort on modeling.

Output: <outdir>/data/curated_dataset.csv and a printed curation summary.

Usage:
  python curate_dataset.py --symbol PCSK9
  python curate_dataset.py --target_ids CHEMBL2929,CHEMBL4523996 --outdir /mnt/results/pcsk9
  python curate_dataset.py --config myconfig.json
"""
import argparse, json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C


def build_config():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config')
    ap.add_argument('--symbol')
    ap.add_argument('--target_ids', help='comma-separated ChEMBL target IDs')
    ap.add_argument('--organism', default=None)
    ap.add_argument('--mw_max', type=float, default=None)
    ap.add_argument('--min_compounds', type=int, default=None)
    ap.add_argument('--outdir', default=None)
    a = ap.parse_args()
    tids = a.target_ids.split(',') if a.target_ids else None
    cfg = C.load_config(a.config, target_symbol=a.symbol, target_chembl_ids=tids,
                        organism=a.organism, mw_max=a.mw_max,
                        min_compounds=a.min_compounds, outdir=a.outdir)
    return cfg


def resolve_targets(cfg):
    if cfg['target_chembl_ids']:
        return cfg['target_chembl_ids']
    if not cfg['target_symbol']:
        sys.exit("ERROR: provide --symbol or --target_ids.")
    cand = C.resolve_target(cfg['target_symbol'], cfg['organism'])
    single = cand[cand.target_type == 'SINGLE PROTEIN']
    picks = list(single.chembl_id.head(1))
    ppi = cand[cand.target_type.isin(
        ['PROTEIN-PROTEIN INTERACTION', 'PROTEIN COMPLEX'])]
    picks += list(ppi.chembl_id.head(1))
    picks = picks or list(cand.chembl_id.head(1))
    print(f"\nAuto-selected target(s): {picks}")
    print("  (Confirm these are correct; pass --target_ids to override.)")
    cand.to_csv(os.path.join(cfg['outdir'], 'data',
                             'target_candidates.csv'), index=False)
    return picks


def curate(cfg, target_ids):
    aff = set(cfg['affinity_types'])
    pool = []
    print(f"\nFetching activities for {target_ids} ...")
    for tid in target_ids:
        for r in C.fetch_activities(tid, verbose=True):
            if (r['standard_type'] in aff and r.get('standard_relation') == '='
                    and r.get('standard_value') is not None
                    and r.get('standard_units') == 'nM'
                    and r.get('canonical_smiles')):
                pool.append(dict(tid=tid, type=r['standard_type'],
                                 mol=r['molecule_chembl_id'],
                                 smi=r['canonical_smiles'],
                                 val=float(r['standard_value'])))
    raw = pd.DataFrame(pool)
    if raw.empty:
        sys.exit("ERROR: no usable '=' nM affinity rows with SMILES. "
                 "Check target IDs / endpoints.")
    print(f"\nRaw affinity rows (= relation, nM, w/ SMILES): {len(raw)} "
          f"| unique mols: {raw.mol.nunique()}")

    # standardize
    res = [C.std_smiles_ik_mw(s) for s in raw.smi]
    raw['std'] = [x[0] for x in res]
    raw['ik'] = [x[1] for x in res]
    raw['mw'] = [x[2] for x in res]
    raw = raw.dropna(subset=['std']).copy()

    # drug-like filter (data-reality: how many peptides/macrocycles get dropped)
    raw['dl'] = [C.is_druglike(s, w, cfg['mw_max'], cfg['require_carbon'],
                               cfg['exclude_metals'])
                 for s, w in zip(raw['std'], raw['mw'])]
    dl = raw[raw.dl].copy()
    dl['pAff'] = C.pAffinity_from_nM(dl['val'])
    n_removed = raw.mol.nunique() - dl.mol.nunique()
    print(f"Drug-like rows: {len(dl)} | unique drug-like compounds: "
          f"{dl.ik.nunique()}")
    print(f"Non-drug-like unique compounds removed "
          f"(peptides/macrocycles/inorganic): ~{n_removed}")

    # per-target / per-endpoint breakdown (the data-reality table)
    print("\n--- Data-reality breakdown (drug-like unique compounds) ---")
    for tid in target_ids:
        sub = dl[dl.tid == tid]
        by = sub.groupby('type').ik.nunique().to_dict()
        print(f"  {tid}: {sub.ik.nunique()} compounds  by endpoint={by}")

    # aggregate replicates per compound (median), track assay composition
    def agg(g):
        types = g['type'].value_counts().to_dict()
        return pd.Series({
            'molecule_chembl_id': g['mol'].iloc[0],
            'std_smiles': g['std'].iloc[0],
            'mw': g['mw'].iloc[0],
            'n_measurements': int(len(g)),
            'assay_types': ','.join(f'{k}:{v}' for k, v in sorted(types.items())),
            'dominant_assay': g['type'].mode().iloc[0],
            'pAffinity': round(float(g['pAff'].median()), 3),
            'pAff_range': round(float(g['pAff'].max() - g['pAff'].min()), 3),
            'n_targets': int(g['tid'].nunique()),
        })
    comp = dl.groupby('ik', as_index=False).apply(agg, include_groups=False) \
             .rename(columns={'ik': 'inchikey'})

    flag = int((comp.pAff_range > cfg['replicate_flag_log']).sum())
    drop = int((comp.pAff_range > cfg['replicate_drop_log']).sum())
    comp = comp[comp.pAff_range <= cfg['replicate_drop_log']].copy()
    print(f"\nReplicate range >{cfg['replicate_flag_log']} log flagged: {flag}; "
          f">{cfg['replicate_drop_log']} log dropped: {drop}")

    # PAINS flag (kept, not removed) + Murcko scaffold + assay group covariate
    from rdkit import Chem
    from rdkit.Chem import FilterCatalog
    params = FilterCatalog.FilterCatalogParams()
    params.AddCatalog(FilterCatalog.FilterCatalogParams.FilterCatalogs.PAINS)
    cat = FilterCatalog.FilterCatalog(params)
    comp['PAINS'] = [cat.HasMatch(Chem.MolFromSmiles(s)) for s in comp.std_smiles]
    comp['scaffold'] = [C.murcko_scaffold(s) for s in comp.std_smiles]
    comp = comp[comp.scaffold.notna() & (comp.scaffold != '')] \
        .reset_index(drop=True)
    comp['assay_group'] = comp['dominant_assay'].map(C.assay_group)

    return comp


def summarize_and_gate(cfg, comp):
    n = len(comp)
    sc = comp.scaffold.value_counts()
    print("\n" + "=" * 78)
    print(f"FINAL curated dataset: {n} compounds | {comp.scaffold.nunique()} "
          f"unique scaffolds")
    print("=" * 78)
    print("Dominant assay:", dict(comp.dominant_assay.value_counts()))
    print("Assay group   :", dict(comp.assay_group.value_counts()))
    print(f"PAINS flagged (kept): {int(comp.PAINS.sum())}")
    print(f"MW range: {comp.mw.min():.0f}-{comp.mw.max():.0f} Da")
    print("pAffinity:", comp.pAffinity.describe().round(2).to_dict())
    print(f"Scaffold singletons: {(sc == 1).sum()} / {len(sc)}; "
          f"largest class: {sc.iloc[0]} compounds")

    # assay-group mean check (pooling introduces a batch offset only if means differ)
    if comp.assay_group.nunique() > 1:
        gm = comp.groupby('assay_group').pAffinity.mean().round(2).to_dict()
        print(f"Assay-group mean pAffinity (should be similar): {gm}")

    out = os.path.join(cfg['outdir'], 'data', 'curated_dataset.csv')
    comp.to_csv(out, index=False)
    print(f"\nSaved -> {out}")

    # ---- DATA-REALITY GATE ----
    if n < cfg['min_compounds']:
        print("\n" + "!" * 78)
        print(f"DATA-REALITY GATE FAILED: only {n} drug-like compounds "
              f"(< min_compounds={cfg['min_compounds']}).")
        print("Do NOT proceed to GNN modeling on this set. Options:")
        print("  * Pool more endpoints (add Ki/Kd/EC50) -- already pooling: "
              f"{cfg['affinity_types']}")
        print("  * Add the single-protein target as well as the PPI target.")
        print("  * Relax mw_max if some 'peptidic' matter is still drug-like.")
        print("  * Consider fingerprint-only models; a GNN needs more data.")
        print("!" * 78)
        return False
    if n < 200:
        print(f"\nNOTE: N={n} is small. Expect modest scaffold-split "
              f"performance and high fold variance. Fingerprint baselines "
              f"often beat a GNN at this size -- report honestly.")
    return True


def main():
    cfg = build_config()
    with open(os.path.join(cfg['outdir'], 'config_used.json'), 'w') as fh:
        json.dump(cfg, fh, indent=2)
    tids = resolve_targets(cfg)
    comp = curate(cfg, tids)
    ok = summarize_and_gate(cfg, comp)
    sys.exit(0 if ok else 2)


if __name__ == '__main__':
    main()
