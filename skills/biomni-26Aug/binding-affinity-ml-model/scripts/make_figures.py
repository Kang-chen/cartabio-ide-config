#!/usr/bin/env python3
"""
Generate the three data figures for the report from the CV outputs:
  fig1_pred_vs_actual  -- selected (RF) model, scaffold-split out-of-fold
  fig2_model_comparison -- Spearman & R2, scaffold vs random, all models
  fig3_dataset_landscape -- pAffinity by assay group + scaffold frequency

Conceptual/schematic figures are NOT drawn here (use an image tool for those).
These are data plots only. Saves .png + .svg with editable SVG text.

Usage: python make_figures.py --outdir /mnt/results/pcsk9
"""
import argparse, os, sys
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from scipy.stats import spearmanr

matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']
matplotlib.rcParams['svg.fonttype'] = 'none'
matplotlib.rcParams['figure.dpi'] = 120

FUNCTIONAL, BINDING = '#0279EE', '#FF9400'
MODEL_C = {'DeepPurpose': '#FD9BED', 'GNN': '#0279EE',
           'RandomForest': '#D4A04A', 'GBM': '#75A025'}
TIER_C = {'high': '#75A025', 'borderline': '#FF9400',
          'out_of_domain': '#B0AAA0'}


def fig_pred_vs_actual(outdir):
    p = os.path.join(outdir, 'data', 'rf_scaffold_oof.csv')
    if not os.path.exists(p):
        print("(skip fig1: no rf_scaffold_oof.csv)")
        return
    d = pd.read_csv(p)
    yt, yp, ag = d.y_true.values, d.rf_pred.values, d.assay_group.values
    fig, ax = plt.subplots(figsize=(4.6, 4.4))
    for grp, col in [('functional(IC50/EC50)', FUNCTIONAL),
                     ('binding(Kd/Ki)', BINDING)]:
        m = ag == grp
        if m.sum():
            ax.scatter(yt[m], yp[m], c=col, s=26, alpha=0.7,
                       edgecolors='white', linewidths=0.4, label=grp)
    lo = min(yt.min(), yp.min()) - 0.3
    hi = max(yt.max(), yp.max()) + 0.3
    ax.plot([lo, hi], [lo, hi], '--', c='#888', lw=1)
    ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
    ax.set_xlabel('Experimental pAffinity (-log10 M)')
    ax.set_ylabel('RF predicted pAffinity')
    rho = spearmanr(yt, yp)[0]
    ax.set_title(f'Random Forest (selected)  |  scaffold-split OOF\n'
                 f'Spearman rho = {rho:+.2f}', fontsize=10)
    ax.legend(fontsize=8, loc='upper left')
    ax.grid(alpha=0.25)
    _save(fig, outdir, 'fig1_pred_vs_actual')
    # print within-group spearman for the report
    for grp in ('binding(Kd/Ki)', 'functional(IC50/EC50)'):
        m = ag == grp
        if m.sum() > 2:
            print(f"  within {grp}: Spearman={spearmanr(yt[m], yp[m])[0]:+.3f}")


def fig_model_comparison(outdir):
    s = pd.read_csv(os.path.join(outdir, 'data', 'cv_summary.csv'))
    cv = {(r.split, r.model): r for _, r in s.iterrows()}
    models = [m for m in ['RandomForest', 'GBM', 'GNN', 'DeepPurpose']
              if ('scaffold', m) in cv]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.0))
    for ax, (metric, lab) in zip(axes, [('Spearman', 'Spearman rho'),
                                        ('R2', 'R2')]):
        x = np.arange(len(models)); w = 0.38
        sc = [cv[('scaffold', m)] for m in models]
        rd = [cv[('random', m)] for m in models]
        ax.bar(x - w / 2, [d[metric] for d in sc], w,
               yerr=[d[metric + '_sd'] for d in sc], capsize=3,
               color=[MODEL_C[m] for m in models], edgecolor='#222',
               linewidth=0.6, error_kw=dict(lw=1))
        ax.bar(x + w / 2, [d[metric] for d in rd], w,
               yerr=[d[metric + '_sd'] for d in rd], capsize=3,
               color=[MODEL_C[m] for m in models], edgecolor='#222',
               linewidth=0.6, hatch='///', alpha=0.55, error_kw=dict(lw=1))
        ax.axhline(0, color='#555', lw=0.8)
        ax.set_xticks(x)
        _lab = {'RandomForest': 'RF', 'DeepPurpose': 'DeepP.'}
        ax.set_xticklabels([_lab.get(m, m) for m in models])
        ax.set_title(lab, fontsize=12, fontweight='bold')
        ax.grid(axis='y', alpha=0.25)
        if metric == 'R2':
            lo = min(-1.0, min(d['R2'] - d['R2_sd'] for d in sc) - 0.2)
            ax.set_ylim(lo, 0.8)
    axes[0].set_ylabel('Cross-validated score (mean +/- SD)')
    leg = [Patch(fc='#bbb', ec='#222', label='scaffold split'),
           Patch(fc='#bbb', ec='#222', hatch='///', alpha=0.55,
                 label='random split')]
    fig.legend(handles=leg, loc='upper center', ncol=2, frameon=False,
               fontsize=9, bbox_to_anchor=(0.5, 1.02))
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    _save(fig, outdir, 'fig2_model_comparison')


def fig_dataset_landscape(outdir):
    comp = pd.read_csv(os.path.join(outdir, 'data', 'curated_dataset.csv'))
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.8))
    ax = axes[0]
    bins = np.linspace(comp.pAffinity.min(), comp.pAffinity.max(), 18)
    for grp, col, fill in [('functional(IC50/EC50)', FUNCTIONAL, True),
                           ('binding(Kd/Ki)', BINDING, False)]:
        sub = comp[comp.assay_group == grp].pAffinity
        if len(sub):
            ax.hist(sub, bins=bins, color=col, alpha=0.55 if fill else 1.0,
                    histtype='stepfilled' if fill else 'step', lw=1.6,
                    label=f'{grp} (n={len(sub)})')
    ax.set_xlabel('pAffinity (-log10 M)'); ax.set_ylabel('Compounds')
    ax.set_title('Affinity distribution by assay type', fontsize=10)
    ax.legend(fontsize=7.5); ax.grid(alpha=0.25)
    ax = axes[1]
    sc = comp.scaffold.value_counts().values
    ax.hist(sc, bins=np.arange(0.5, sc.max() + 1.5, 1), color='#75A025',
            edgecolor='#222', linewidth=0.5)
    ax.set_xlabel('Compounds per scaffold')
    ax.set_ylabel('Number of scaffolds')
    ax.set_title(f'Scaffold frequency ({(sc == 1).sum()} singletons / '
                 f'{len(sc)})', fontsize=10)
    ax.grid(alpha=0.25)
    plt.tight_layout()
    _save(fig, outdir, 'fig3_dataset_landscape')


def fig_ad_tiers(outdir):
    """Bar chart of applicability-domain tier counts among novel-scaffold
    library compounds (high / borderline / out-of-domain). Communicates that
    only the HIGH tier is a confident shortlist."""
    import json
    p = os.path.join(outdir, 'data', 'ad_tier_summary.json')
    if not os.path.exists(p):
        print("(skip fig5: no ad_tier_summary.json)")
        return
    with open(p) as fh:
        d = json.load(fh)
    tc = d.get('tier_counts', {})
    order = ['high', 'borderline', 'out_of_domain']
    labels = {'high': 'High-confidence\n(in-domain)',
              'borderline': 'Borderline\n(low-confidence)',
              'out_of_domain': 'Out-of-domain\n(unreliable)'}
    vals = [int(tc.get(k, 0)) for k in order]
    fig, ax = plt.subplots(figsize=(5.4, 4.0))
    bars = ax.bar([labels[k] for k in order], vals,
                  color=[TIER_C[k] for k in order], edgecolor='#222',
                  linewidth=0.7)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, f'{v:,}', ha='center',
                va='bottom', fontsize=9)
    ax.set_ylabel('Novel-scaffold library compounds')
    hi = d.get('ad_high_tanimoto_min'); hx = d.get('ad_high_tanimoto_max')
    bl = d.get('ad_borderline_tanimoto_min')
    ax.set_title('Applicability-domain confidence tiers\n'
                 f'(high: NN-Tanimoto in [{hi},{hx}] + in-range + low '
                 f'disagreement; borderline: [{bl},{hi}))', fontsize=9.5)
    ax.grid(axis='y', alpha=0.25)
    plt.tight_layout()
    _save(fig, outdir, 'fig5_ad_tiers')


def _save(fig, outdir, name):
    figdir = os.path.join(outdir, 'figures')
    fig.savefig(os.path.join(figdir, name + '.png'), dpi=120,
                bbox_inches='tight')
    fig.savefig(os.path.join(figdir, name + '.svg'), bbox_inches='tight')
    plt.close(fig)
    print(f"Saved {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--outdir', required=True)
    a = ap.parse_args()
    fig_pred_vs_actual(a.outdir)
    fig_model_comparison(a.outdir)
    fig_dataset_landscape(a.outdir)
    fig_ad_tiers(a.outdir)


if __name__ == '__main__':
    main()
