#!/usr/bin/env python3
"""
Step 4: Train the FINAL selected model on all curated compounds, score an
external library, and nominate novel-scaffold candidates within the model's
applicability domain.

Defaults to Random Forest as the production model because tree ensembles do NOT
extrapolate beyond the training range (predictions stay physically sensible)
and they usually win the scaffold-split benchmark at small N. Pass
--model gnn to use the GNN instead (only sensible if it won the benchmark).

Applicability domain (all three must hold for a "confident" hit):
  * NOVELTY: Bemis-Murcko scaffold absent from the training set.
  * SIMILARITY WINDOW: nearest-neighbour Tanimoto to training in
    [ad_tanimoto_min, ad_tanimoto_max]. Too far = unreliable; too near = trivial
    analog, not novel chemistry.
  * PREDICTION IN RANGE: predicted pAffinity <= training max (no extrapolation).
  * LOW DISAGREEMENT: ensemble/tree std below the ad_std_quantile cutoff.

Outputs:
  <outdir>/data/novel_scaffold_candidates.csv   (all confident hits)
  <outdir>/data/top25_novel_candidates.csv      (best per scaffold, top 25)
  <outdir>/figures/fig4_top_candidates.png      (structure grid of top 9)

Usage:
  python screen_library.py --outdir /mnt/results/pcsk9
  python screen_library.py --outdir /mnt/results/pcsk9 --library_csv mylib.csv
"""
import argparse, os, sys, io
import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import DataStructs
from sklearn.ensemble import RandomForestRegressor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import models as M


def load_library(cfg, library_csv):
    """Return DataFrame with columns chembl_id, smi, max_phase, pref_name."""
    if library_csv:
        df = pd.read_csv(library_csv)
        col = next((c for c in df.columns if c.lower() in
                    ('smiles', 'smi', 'canonical_smiles')), None)
        if col is None:
            sys.exit("ERROR: library CSV needs a 'smiles' column.")
        df = df.rename(columns={col: 'smi'})
        if 'chembl_id' not in df:
            df['chembl_id'] = [f'LIB{i}' for i in range(len(df))]
        df['max_phase'] = df.get('max_phase', np.nan)
        df['pref_name'] = df.get('pref_name', df['chembl_id'])
        return df[['chembl_id', 'smi', 'max_phase', 'pref_name']]
    lib = []
    print("Fetching ChEMBL clinical/approved small molecules ...")
    for ph in cfg['library_max_phase']:
        lib += C.fetch_library_by_phase(ph, npages=cfg['library_pages'],
                                        verbose=True)
    return pd.DataFrame(lib).drop_duplicates('chembl_id')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config')
    ap.add_argument('--outdir', default=None)
    ap.add_argument('--model', choices=['rf', 'gnn'], default='rf')
    ap.add_argument('--library_csv', default=None)
    a = ap.parse_args()
    cfg = C.load_config(a.config, outdir=a.outdir,
                        library_smiles_csv=a.library_csv)

    comp = pd.read_csv(os.path.join(cfg['outdir'], 'data',
                                    'curated_dataset.csv'))
    y_all = comp.pAffinity.values.astype(float)
    X_fp = C.morgan_matrix(comp.std_smiles, cfg['fp_radius'], cfg['fp_bits'])
    train_scaffolds = set(comp.scaffold.values)
    train_iks = set(comp.inchikey)
    train_fps = C.morgan_bitvects(comp.std_smiles, cfg['fp_radius'],
                                  cfg['fp_bits'])

    # ---- final model ----
    if a.model == 'rf':
        final = RandomForestRegressor(n_estimators=500, n_jobs=-1,
                                      random_state=0).fit(X_fp, y_all)
        print("Final model: Random Forest (500 trees) on all "
              f"{len(comp)} compounds.")
    else:
        print("Final model: GNN. (Use only if it won the scaffold-split "
              "benchmark.) Predictions can extrapolate -- interpret with care.")
        final = None

    # ---- library ----
    libdf = load_library(cfg, cfg['library_smiles_csv'])
    print(f"Library raw: {len(libdf)}")
    pr = [C.std_smiles_ik_mw(s) for s in libdf.smi]
    libdf['std_smiles'] = [x[0] for x in pr]
    libdf['ik'] = [x[1] for x in pr]
    libdf['mw'] = [x[2] for x in pr]
    libdf['scaffold'] = [C.murcko_scaffold(x[0]) if x[0] else None for x in pr]
    libdf = libdf.dropna(subset=['std_smiles', 'scaffold']).copy()
    libdf = libdf[[C.is_druglike(s, w, cfg['mw_max'], cfg['require_carbon'],
                                 cfg['exclude_metals'])
                   for s, w in zip(libdf.std_smiles, libdf.mw)]]
    libdf = libdf[libdf.scaffold != ''].drop_duplicates('ik')
    libdf = libdf[~libdf.ik.isin(train_iks)].copy()
    libdf['novel_scaffold'] = ~libdf.scaffold.isin(train_scaffolds)
    print(f"Drug-like, deduped, minus training: {len(libdf)} "
          f"| novel scaffold: {int(libdf.novel_scaffold.sum())}")

    # ---- score ----
    Xlib = C.morgan_matrix(libdf.std_smiles, cfg['fp_radius'], cfg['fp_bits'])
    if a.model == 'rf':
        pred, pstd = M.rf_predict_with_std(final, Xlib)
    else:
        pred = M.train_gnn_fixed(np.arange(len(comp)), np.arange(len(libdf)),
                                 np.concatenate([comp.std_smiles.values,
                                                 libdf.std_smiles.values]),
                                 np.concatenate([y_all, np.zeros(len(libdf))]))
        pstd = np.full(len(libdf), np.nan)
    libdf['pred_pAffinity'] = pred
    libdf['pred_std'] = pstd
    print(f"Prediction range: {np.nanmin(pred):.2f}-{np.nanmax(pred):.2f} "
          f"(training max {y_all.max():.2f})")

    # ---- applicability domain: THREE-TIER confidence ----
    # Every novel-scaffold compound is assigned a tier. HIGH-confidence requires
    # the reliable similarity window AND in-range prediction AND low model
    # disagreement. Weakly-similar compounds (below the high floor) are BORDERLINE
    # (low-confidence, NOT confident). Compounds too far (extrapolation) or too
    # near (trivial analog) are OUT-OF-DOMAIN. This replaces the old single
    # [0.25,0.55] "confident" band that mislabeled ~0.26-0.43 compounds.
    novel = libdf[libdf.novel_scaffold].dropna(subset=['pred_pAffinity']).copy()

    def nn_tan(smi):
        fp = C.morgan_bitvects([smi], cfg['fp_radius'], cfg['fp_bits'])[0]
        return max(DataStructs.BulkTanimotoSimilarity(fp, train_fps))
    novel['nn_tanimoto'] = [nn_tan(s) for s in novel.std_smiles]
    novel['pred_in_range'] = novel.pred_pAffinity <= y_all.max()

    # per-compound std threshold (only meaningful for tree ensembles)
    if novel.pred_std.notna().any():
        sd_thresh = novel.pred_std.quantile(cfg['ad_std_quantile'])
        std_ok = (novel.pred_std <= sd_thresh).values
    else:
        sd_thresh = np.nan
        std_ok = np.ones(len(novel), dtype=bool)  # no ensemble std available

    tiers, flags = [], []
    for t, in_range, sok in zip(novel.nn_tanimoto.values,
                                novel.pred_in_range.values, std_ok):
        tier = C.ad_tier(t, bool(in_range), bool(sok), cfg)
        tiers.append(tier)
        fl = []
        if t < cfg['ad_borderline_tanimoto_min']:
            fl.append('too_dissimilar(extrapolation)')
        elif t < cfg['ad_high_tanimoto_min']:
            fl.append('weakly_similar')
        if t > cfg['ad_high_tanimoto_max']:
            fl.append('too_similar(trivial_analog)')
        if not in_range:
            fl.append('pred_out_of_range')
        if not sok:
            fl.append('high_model_disagreement')
        flags.append(';'.join(fl) if fl else 'in_domain')
    novel['ad_tier'] = tiers
    novel['ad_flags'] = flags

    tier_counts = novel.ad_tier.value_counts().to_dict()
    print(f"Applicability-domain tiers among {len(novel)} novel-scaffold "
          f"compounds: {tier_counts}")
    print(f"  (high-confidence window Tanimoto in "
          f"[{cfg['ad_high_tanimoto_min']},{cfg['ad_high_tanimoto_max']}], "
          f"borderline in [{cfg['ad_borderline_tanimoto_min']},"
          f"{cfg['ad_high_tanimoto_min']}); std<= "
          f"{sd_thresh if np.isnan(sd_thresh) else round(sd_thresh,3)})")

    phase_map = {4.0: 'Approved', 3.0: 'Phase 3', 2.0: 'Phase 2',
                 1.0: 'Phase 1', 0.0: 'Preclinical'}
    novel['clinical_status'] = pd.to_numeric(
        novel['max_phase'], errors='coerce').map(phase_map).fillna('Unknown')

    cols = ['chembl_id', 'pref_name', 'clinical_status', 'ad_tier', 'ad_flags',
            'pred_pAffinity', 'pred_std', 'nn_tanimoto', 'mw', 'std_smiles',
            'scaffold']

    # ALL scored novel compounds with their tier (full transparency)
    novel_out = novel.sort_values(
        ['ad_tier', 'pred_pAffinity'],
        ascending=[True, False])[cols].round(
        {'pred_pAffinity': 3, 'pred_std': 3, 'nn_tanimoto': 3, 'mw': 1})
    novel_out.to_csv(os.path.join(cfg['outdir'], 'data',
                                  'all_scored_candidates.csv'), index=False)

    # HIGH-confidence only (the genuine shortlist)
    high = novel[novel.ad_tier == 'high'].sort_values(
        'pred_pAffinity', ascending=False).copy()
    high_div = high.drop_duplicates('scaffold').copy()
    borderline = novel[novel.ad_tier == 'borderline'].copy()
    print(f"HIGH-confidence novel-scaffold hits: {len(high)} "
          f"({high.scaffold.nunique()} scaffolds); diverse shortlist "
          f"{len(high_div)}. Borderline: {len(borderline)}; "
          f"out-of-domain: {int((novel.ad_tier=='out_of_domain').sum())}.")

    high[cols].round({'pred_pAffinity': 3, 'pred_std': 3, 'nn_tanimoto': 3,
                      'mw': 1}).to_csv(
        os.path.join(cfg['outdir'], 'data',
                     'high_confidence_candidates.csv'), index=False)
    # Back-compat: keep the old filename, but it now contains ONLY high-confidence
    # rows (never borderline/out-of-domain).
    high[cols].round({'pred_pAffinity': 3, 'pred_std': 3, 'nn_tanimoto': 3,
                      'mw': 1}).to_csv(
        os.path.join(cfg['outdir'], 'data',
                     'novel_scaffold_candidates.csv'), index=False)

    top25 = high_div.sort_values('pred_pAffinity', ascending=False).head(25)[cols]
    top25 = top25.round({'pred_pAffinity': 2, 'pred_std': 2,
                         'nn_tanimoto': 2, 'mw': 0})
    top25.to_csv(os.path.join(cfg['outdir'], 'data',
                              'top25_high_confidence.csv'), index=False)
    # Back-compat alias
    top25.to_csv(os.path.join(cfg['outdir'], 'data',
                              'top25_novel_candidates.csv'), index=False)
    # persist tier counts for the report
    import json as _json
    with open(os.path.join(cfg['outdir'], 'data', 'ad_tier_summary.json'),
              'w') as fh:
        _json.dump({'tier_counts': tier_counts,
                    'n_novel': int(len(novel)),
                    'ad_high_tanimoto_min': cfg['ad_high_tanimoto_min'],
                    'ad_high_tanimoto_max': cfg['ad_high_tanimoto_max'],
                    'ad_borderline_tanimoto_min': cfg['ad_borderline_tanimoto_min'],
                    'std_threshold': None if np.isnan(sd_thresh) else float(sd_thresh),
                    'n_high': int(len(high)), 'n_borderline': int(len(borderline)),
                    'n_high_scaffolds': int(high.scaffold.nunique())}, fh, indent=2)
    print("Clinical status among HIGH-confidence hits:",
          high.clinical_status.value_counts().to_dict())

    # keep names used by the structure-grid block below
    conf_div = high_div

    # ---- structure grid (HIGH-confidence first; fill from borderline if <9) ----
    try:
        from rdkit.Chem import Draw
        from PIL import Image as PILImage
        gcols = ['chembl_id', 'pref_name', 'clinical_status', 'ad_tier',
                 'pred_pAffinity', 'nn_tanimoto', 'std_smiles']
        grid_df = high_div.sort_values('pred_pAffinity', ascending=False)[gcols]
        if len(grid_df) < 9:
            bfill = borderline.drop_duplicates('scaffold').sort_values(
                'pred_pAffinity', ascending=False)[gcols]
            grid_df = pd.concat([grid_df, bfill], ignore_index=True)
        top9 = grid_df.head(9)
        if len(top9) == 0:
            print("(structure grid skipped: no high-confidence or borderline "
                  "candidates to display)")
        else:
            legends = []
            for _, r in top9.iterrows():
                tag = 'HIGH' if r.ad_tier == 'high' else 'borderline'
                legends.append(
                    f"{(str(r.pref_name) or r.chembl_id)[:20]} [{tag}]\n"
                    f"{r.clinical_status} | pAff {r.pred_pAffinity:.1f} | "
                    f"Tan {r.nn_tanimoto:.2f}")
            img = Draw.MolsToGridImage(
                [Chem.MolFromSmiles(s) for s in top9.std_smiles], molsPerRow=3,
                subImgSize=(330, 260), legends=legends,
                useSVG=False, returnPNG=False)
            if not isinstance(img, PILImage.Image):
                img = PILImage.open(io.BytesIO(img.data))
            fig4 = os.path.join(cfg['outdir'], 'figures',
                                'fig4_top_candidates.png')
            img.save(fig4)
            n_hi = int((top9.ad_tier == 'high').sum())
            print(f"Saved structure grid -> {fig4} "
                  f"({n_hi} high-confidence, {len(top9)-n_hi} borderline shown)")
    except Exception as e:                                       # noqa: BLE001
        print(f"(structure grid skipped: {e})")


if __name__ == '__main__':
    main()
