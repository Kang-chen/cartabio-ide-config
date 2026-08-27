#!/usr/bin/env python3
"""
Step 3: Honestly benchmark a GNN against fingerprint baselines (Random Forest,
Gradient Boosting) under repeated scaffold-split AND random-split CV.

Key honesty rules encoded here:
  * SCAFFOLD split is the headline metric (generalization to new chemistry);
    RANDOM split is reported ONLY as an optimistic reference.
  * The GNN uses leakage-free early stopping (see models.train_gnn_fixed).
  * ALL models see identical folds. The best model is chosen by scaffold-split
    performance -- do NOT assume the GNN wins. At small N the fingerprint
    baselines usually generalize better, and that is a valid, expected result.

Outputs:
  <outdir>/data/cv_fold_metrics.csv   (per-fold metrics, all models/splits)
  <outdir>/data/cv_summary.csv        (mean +/- SD)
  <outdir>/data/rf_scaffold_oof.csv   (RF out-of-fold preds for the chosen model)

Usage:
  python benchmark_models.py --outdir /mnt/results/pcsk9
  python benchmark_models.py --config myconfig.json --no_gnn   # fingerprint-only
"""
import argparse, json, os, sys, time
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import models as M


def metrics(yt, yp):
    return dict(RMSE=np.sqrt(mean_squared_error(yt, yp)),
                MAE=mean_absolute_error(yt, yp),
                R2=r2_score(yt, yp),
                Spearman=spearmanr(yt, yp)[0])


def run_cv(split, comp, y_all, X_fp, scaf, assay, cfg, dp_state=None):
    """dp_state: mutable dict tracking DeepPurpose usage across folds, e.g.
    {'requested': bool, 'used': bool, 'failed_reason': str|None}. When the
    framework is requested but fails on the first fold, we record the reason,
    stop attempting it, and continue with native models (the caller discloses
    the fallback -- never a silent substitution)."""
    rows, oT, oP, oA = [], [], [], []
    n_rep, n_spl = cfg['cv_repeats'], cfg['cv_folds']
    want_dp = (cfg.get('model_framework') == 'deeppurpose')
    for rep in range(n_rep):
        if split == 'scaffold':
            fset = M.scaffold_folds(scaf, n_splits=n_spl, seed=rep)
        else:
            fset = M.random_folds(len(y_all), n_splits=n_spl, seed=rep)
        for k in range(n_spl):
            te = np.array(fset[k])
            tr = np.array([i for j in range(n_spl) if j != k for i in fset[j]])
            yt = y_all[te]
            preds = []
            # ---- Requested framework: DeepPurpose (first-class, no silent swap) ----
            if want_dp and dp_state is not None and dp_state.get('failed_reason') is None:
                try:
                    import models_deeppurpose as DP
                    pdp = DP.train_deeppurpose_fixed(
                        tr, te, comp.std_smiles.values, y_all,
                        drug_encoding=cfg['deeppurpose_drug_encoding'],
                        train_epoch=cfg['deeppurpose_train_epoch'],
                        lr=cfg['deeppurpose_LR'],
                        batch_size=cfg['deeppurpose_batch_size'], seed=rep)
                    preds.append(('DeepPurpose', pdp))
                    dp_state['used'] = True
                except Exception as e:                           # noqa: BLE001
                    dp_state['failed_reason'] = f"{type(e).__name__}: {e}"
                    print(f"\n{'!'*78}\nDeepPurpose was REQUESTED but could not run "
                          f"on fold {split}/{k}:\n  {dp_state['failed_reason']}\n"
                          f"Falling back to native models (this is DISCLOSED, not a "
                          f"silent substitution).\n{'!'*78}\n", flush=True)
            if cfg['run_gnn']:
                pg = M.train_gnn_fixed(tr, te, comp.std_smiles.values, y_all,
                                       seed=rep)
                preds.append(('GNN', pg))
            rf = RandomForestRegressor(n_estimators=300, n_jobs=-1,
                                       random_state=rep).fit(X_fp[tr], y_all[tr])
            gb = GradientBoostingRegressor(n_estimators=300, max_depth=3,
                                           learning_rate=0.05,
                                           random_state=rep).fit(X_fp[tr],
                                                                 y_all[tr])
            preds += [('RandomForest', rf.predict(X_fp[te])),
                      ('GBM', gb.predict(X_fp[te]))]
            for name, pred in preds:
                m = metrics(yt, pred)
                m.update(model=name, repeat=rep, fold=k, split=split)
                rows.append(m)
            # RF OOF for the pred-vs-actual figure of the chosen model
            if split == 'scaffold' and rep == 0:
                oT += list(yt)
                oP += list(rf.predict(X_fp[te]))
                oA += list(assay[te])
    return pd.DataFrame(rows), (np.array(oT), np.array(oP), np.array(oA))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config')
    ap.add_argument('--outdir', default=None)
    ap.add_argument('--no_gnn', action='store_true')
    ap.add_argument('--framework', choices=['auto', 'deeppurpose'], default=None,
                    help="Requested modeling framework. 'deeppurpose' adds a "
                         "DeepPurpose model as a first-class competitor on the "
                         "SAME folds; if it cannot run, the failure is DISCLOSED "
                         "and native models are used (never a silent swap).")
    a = ap.parse_args()
    cfg = C.load_config(a.config, outdir=a.outdir, model_framework=a.framework)
    if a.no_gnn:
        cfg['run_gnn'] = False

    path = os.path.join(cfg['outdir'], 'data', 'curated_dataset.csv')
    if not os.path.exists(path):
        sys.exit(f"ERROR: {path} not found. Run curate_dataset.py first.")
    comp = pd.read_csv(path)
    y_all = comp.pAffinity.values.astype(float)
    scaf = comp.scaffold.values
    assay = comp.assay_group.values
    X_fp = C.morgan_matrix(comp.std_smiles, cfg['fp_radius'], cfg['fp_bits'])
    assert set(np.unique(X_fp)) <= {0, 1}, "fingerprint not binary"

    # ---- Framework provenance (no silent substitution) ----
    want_dp = (cfg.get('model_framework') == 'deeppurpose')
    dp_state = {'requested': want_dp, 'used': False, 'failed_reason': None,
                'drug_encoding': cfg['deeppurpose_drug_encoding'],
                'train_epoch': cfg['deeppurpose_train_epoch']}
    if want_dp:
        try:
            import models_deeppurpose as DP
            avail = DP.deeppurpose_available()
            dp_state['version'] = DP._version()
        except Exception as e:                                   # noqa: BLE001
            avail = False
            dp_state['version'] = 'import-error'
        if not avail:
            dp_state['failed_reason'] = ('DeepPurpose not importable at startup')
            print(f"\n{'!'*78}\nDeepPurpose was REQUESTED (--framework deeppurpose) "
                  f"but is NOT AVAILABLE in this environment.\nThe run will "
                  f"continue with native models and this will be DISCLOSED in the "
                  f"report. This is NOT a silent substitution.\n{'!'*78}\n")
    print(f"Dataset: {len(comp)} compounds, {comp.scaffold.nunique()} scaffolds. "
          f"GNN={'ON' if cfg['run_gnn'] else 'OFF'} | "
          f"framework={cfg.get('model_framework')} "
          f"(DeepPurpose requested={want_dp})")

    all_rows = []
    for split in ['scaffold', 'random']:
        t0 = time.time()
        print(f"\n{split.upper()} CV ...")
        res, oof = run_cv(split, comp, y_all, X_fp, scaf, assay, cfg,
                          dp_state=dp_state)
        all_rows.append(res)
        print(f"  {time.time() - t0:.0f}s")
        if split == 'scaffold':
            pd.DataFrame(dict(y_true=oof[0], rf_pred=oof[1],
                              assay_group=oof[2])).to_csv(
                os.path.join(cfg['outdir'], 'data', 'rf_scaffold_oof.csv'),
                index=False)

    allres = pd.concat(all_rows, ignore_index=True)
    allres.to_csv(os.path.join(cfg['outdir'], 'data', 'cv_fold_metrics.csv'),
                  index=False)
    summary = allres.groupby(['split', 'model']).agg(
        RMSE=('RMSE', 'mean'), RMSE_sd=('RMSE', 'std'), MAE=('MAE', 'mean'),
        R2=('R2', 'mean'), R2_sd=('R2', 'std'),
        Spearman=('Spearman', 'mean'),
        Spearman_sd=('Spearman', 'std')).round(3).reset_index()
    order = {'DeepPurpose': 0, 'GNN': 1, 'RandomForest': 2, 'GBM': 3}
    summary['o'] = summary.model.map(order).fillna(9)
    summary = summary.sort_values(['split', 'o']).drop(columns='o')
    summary.to_csv(os.path.join(cfg['outdir'], 'data', 'cv_summary.csv'),
                   index=False)

    # Persist framework provenance for the report to read + disclose.
    dp_state['available_and_used'] = bool(dp_state['used'])
    with open(os.path.join(cfg['outdir'], 'data', 'framework_provenance.json'),
              'w') as fh:
        json.dump(dp_state, fh, indent=2)

    print("\n" + "=" * 90)
    print("CROSS-VALIDATED PERFORMANCE (mean over folds; leakage-free)")
    print("=" * 90)
    for sp in ['scaffold', 'random']:
        print(f"\n--- {sp.upper()} ---")
        for _, r in summary[summary.split == sp].iterrows():
            print(f"  {r.model:14s} RMSE={r.RMSE:.3f}+/-{r.RMSE_sd:.3f}  "
                  f"MAE={r.MAE:.3f}  R2={r.R2:+.3f}+/-{r.R2_sd:.3f}  "
                  f"Spearman={r.Spearman:+.3f}+/-{r.Spearman_sd:.3f}")

    # Framework provenance summary (explicit, so no substitution is silent)
    if want_dp:
        if dp_state['used'] and dp_state['failed_reason'] is None:
            print(f"\n[framework] DeepPurpose ({dp_state.get('version')}, "
                  f"{dp_state['drug_encoding']}) was requested and USED as a "
                  f"first-class model.")
        else:
            print(f"\n[framework] DeepPurpose was REQUESTED but NOT used "
                  f"(reason: {dp_state['failed_reason']}). Native models were "
                  f"used instead -- this is disclosed, not silent.")

    # Recommend the model with best scaffold-split Spearman
    sc = summary[summary.split == 'scaffold'].sort_values('Spearman',
                                                          ascending=False)
    best = sc.iloc[0]
    print(f"\n>>> Best scaffold-split model: {best.model} "
          f"(Spearman={best.Spearman:+.3f}). Select this for screening.")
    if best.model not in ('GNN', 'DeepPurpose') and (cfg['run_gnn'] or dp_state['used']):
        print("    A deep model did NOT win the scaffold split -- a common, "
              "expected outcome at this data size. Use the fingerprint model as "
              "the production model and say so plainly in the report.")


if __name__ == '__main__':
    main()
