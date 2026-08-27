#!/usr/bin/env python3
"""
make_figures.py
===============
Generate the CORE figure set for a knowledge-graph target-prioritization run, in
both PNG (dpi 200) and editable SVG. Disease-agnostic: all labels are derived from
the ranking outputs and a small metadata JSON.

CORE figures (always produced)
------------------------------
  fig1_summary_infographic  KPIs (graph scale, #seeds, top-bin enrichment) +
                            top-10 bar + 4-stage workflow strip.
  fig2_top20_ranking        Top-20 by combined score, colored known vs novel,
                            ADME hatched, rank labels in the left margin.
  fig3_score_enrichment     2-panel: combined-score distribution across all genes;
                            known-target fold-enrichment per rank bin.
  fig4_evidence_paths       Small-multiple graphs of the multi-hop evidence paths
                            for the top hits (from evidence_paths.json).

CONDITIONAL extras (opt in with flags; auto-skip when not applicable)
---------------------------------------------------------------------
  --seed-venn      seed-set intersections across anchors (needs >= 2 anchors)
  --txgnn-fig      genes with highest TxGNN drug-target support (needs TxGNN layer)
  --hero           single-target convergence figure for the #1 hit

Inputs
------
  --ranked           ranked_targets.csv           (required)
  --evidence         evidence_paths.json          (required for fig4)
  --enrichment       enrichment_check.json        (required for fig3 panel B)
  --meta             meta.json                    (optional; see keys below)
  --disease          disease display name         (for titles)
  --out              output figures/ dir

meta.json optional keys (all used only for display; computed values fall back
sensibly if absent): n_edges, n_genes, n_nnz, n_seeds_total, anchors (list of
{name, n_seeds}), n_iter, enrich_top50, n_known_top50.

Usage
-----
    python make_figures.py --ranked out/ranked_targets.csv \
        --evidence out/evidence_paths.json --enrichment out/enrichment_check.json \
        --meta out/meta.json --disease "Parkinson disease" --out out/figures \
        --seed-venn --txgnn-fig --hero

Style: matplotlib Agg, Liberation Sans, svg.fonttype='none', colorblind palette,
legends BELOW the plot area (avoids bar overlap). MEDIA-CHECK every figure after
generation and regenerate any that come back blank/clipped/unreadable.
"""
import argparse, json, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']
matplotlib.rcParams['svg.fonttype'] = 'none'
matplotlib.rcParams['pdf.fonttype'] = 42
matplotlib.rcParams['axes.spines.top'] = False
matplotlib.rcParams['axes.spines.right'] = False

# colorblind-friendly palette
CB = dict(blue='#0173B2', orange='#DE8F05', green='#029E73', red='#D55E00',
          purple='#CC78BC', yellow='#ECE133', teal='#56B4E9', grey='#949494')
ACC1, ACC2, ACC3 = '#0279EE', '#75A025', '#FF9400'  # infographic accents
KNOWN_C, NOVEL_C, ADME_C = CB['orange'], CB['blue'], CB['purple']


def _status_legend(df_slice, note_adme_hatch=False):
    """Legend handles for only the statuses actually present in df_slice."""
    from matplotlib.patches import Patch
    present = set()
    for _, r in df_slice.iterrows():
        present.add(status_of(dict(r))[0])
    order = [('Known', KNOWN_C, 'Known drug target'),
             ('Novel', NOVEL_C, 'Novel candidate'),
             ('ADME/PK', ADME_C, 'ADME/PK' + (' (hatched)' if note_adme_hatch else ''))]
    return [Patch(facecolor=c, label=lab) for name, c, lab in order if name in present]


def savefig(fig, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(out_dir, name + '.png')
    svg = os.path.join(out_dir, name + '.svg')
    fig.savefig(png, dpi=200, bbox_inches='tight')
    fig.savefig(svg, bbox_inches='tight')
    plt.close(fig)
    print('  wrote', png)


def status_of(row):
    # 'Known' can come from the academic DrugBank label (known_drug_target) or the
    # commercial Open Targets label (known_target_ot); either flags a known target.
    if row.get('known_drug_target', False) or row.get('known_target_ot', False):
        return 'Known', KNOWN_C
    if row.get('likely_ADME_PK', False):
        return 'ADME/PK', ADME_C
    return 'Novel', NOVEL_C


# ---------------------------------------------------------------- fig1 infographic
def fig_infographic(df, meta, enrich, disease, out_dir):
    fig = plt.figure(figsize=(11, 8.2))
    gs = fig.add_gridspec(3, 3, height_ratios=[0.9, 2.0, 0.9], hspace=0.55, wspace=0.3)
    fig.suptitle(f"Target prioritization for {disease}", fontsize=17, fontweight='bold', y=0.99)

    # KPI row
    n_genes = meta.get('n_genes', len(df))
    n_seeds = meta.get('n_seeds_total', int(df.get('is_seed', pd.Series([], dtype=bool)).sum()))
    enrich_top = meta.get('enrich_top50')
    if enrich_top is None and enrich and enrich.get('bins'):
        enrich_top = enrich['bins'][0]['fold_enrichment']
    kpis = [(f"{n_genes:,}", "genes ranked", ACC1),
            (f"{n_seeds:,}", "disease seed genes", ACC2),
            (f"{enrich_top:.1f}x" if enrich_top else "n/a", "known-target enrichment (top bin)", ACC3)]
    for i, (val, lab, col) in enumerate(kpis):
        ax = fig.add_subplot(gs[0, i]); ax.axis('off')
        ax.add_patch(FancyBboxPatch((0.04, 0.12), 0.92, 0.76, boxstyle="round,pad=0.02,rounding_size=0.04",
                                    facecolor=col, edgecolor='none', alpha=0.13, transform=ax.transAxes))
        ax.text(0.5, 0.62, val, ha='center', va='center', fontsize=25, fontweight='bold', color=col, transform=ax.transAxes)
        ax.text(0.5, 0.24, lab, ha='center', va='center', fontsize=10, color='#333', transform=ax.transAxes)

    # top-10 bar (spans all columns)
    ax = fig.add_subplot(gs[1, :])
    top = df.head(10).iloc[::-1]
    colors = [status_of(r._asdict() if hasattr(r, '_asdict') else dict(r))[1]
              for _, r in top.iterrows()]
    ax.barh(range(len(top)), top['combined_score'], color=colors, edgecolor='white')
    ax.set_yticks(range(len(top))); ax.set_yticklabels(top['gene'], fontsize=11)
    ax.set_xlabel('Combined score', fontsize=11)
    ax.set_title('Top 10 prioritized targets', fontsize=12, fontweight='bold', loc='left')
    ax.set_xlim(0, 1.02)  # honest axis from 0 (scores cluster near 1.0 by rank-norm design)
    handles = _status_legend(top)
    ax.legend(handles=handles, loc='upper center', bbox_to_anchor=(0.5, -0.14),
              ncol=len(handles), frameon=False, fontsize=9)

    # workflow strip
    ax = fig.add_subplot(gs[2, :]); ax.axis('off')
    steps = ["Seed genes\n(disease_protein)", "Network propagation\n(RWR over PrimeKG)",
             "Drug-target layer\n(TxGNN, optional)", "Ranked targets\n+ evidence paths"]
    n = len(steps); w = 0.20; gap = (1 - n * w) / (n + 1)
    for i, s in enumerate(steps):
        x = gap + i * (w + gap)
        ax.add_patch(FancyBboxPatch((x, 0.25), w, 0.5, boxstyle="round,pad=0.01,rounding_size=0.03",
                                    facecolor=ACC1, alpha=0.12, edgecolor=ACC1, lw=1, transform=ax.transAxes))
        ax.text(x + w / 2, 0.5, s, ha='center', va='center', fontsize=8.5, transform=ax.transAxes)
        if i < n - 1:
            ax.annotate('', xy=(x + w + gap * 0.85, 0.5), xytext=(x + w + gap * 0.15, 0.5),
                        arrowprops=dict(arrowstyle='-|>', color='#666', lw=1.4), transform=ax.transAxes)
    savefig(fig, out_dir, 'fig1_summary_infographic')


# ---------------------------------------------------------------- fig2 top-20 bar
def fig_top20(df, disease, out_dir, n=20):
    top = df.head(n).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8.5, 8))
    colors, hatches = [], []
    for _, r in top.iterrows():
        _, c = status_of(dict(r)); colors.append(c)
        hatches.append('///' if r.get('likely_ADME_PK', False) else '')
    bars = ax.barh(range(len(top)), top['combined_score'], color=colors, edgecolor='white')
    for b, h in zip(bars, hatches):
        if h: b.set_hatch(h)
    ax.set_yticks(range(len(top))); ax.set_yticklabels(top['gene'], fontsize=10)
    ranks = top['rank'].tolist()
    xmin = 0  # honest axis from 0 (scores cluster near 1.0 by rank-norm design)
    ax.set_xlim(xmin, 1.02)
    for i, rk in enumerate(ranks):
        ax.text(0.004, i, f"#{rk}", va='center', ha='left',
                fontsize=8, color='#222')
    ax.set_xlabel('Combined score (0.7\u00b7RWR + 0.3\u00b7TxGNN, rank-normalized)  \u2014  scores cluster near 1.0 by design; rank order (left) is the signal',
                  fontsize=8.5)
    ax.set_title(f'Top {n} prioritized targets \u2014 {disease}', fontsize=13, fontweight='bold', loc='left', pad=10)
    handles = _status_legend(top, note_adme_hatch=True)
    # If any bar is hatched (likely ADME/PK), always add an explicit hatch legend
    # entry so the pattern is never unexplained. ADME genes that are ALSO known
    # drug targets are colored as 'Known' by status_of, so the plain status legend
    # can omit the ADME color while hatched bars remain on screen.
    if any(hatches):
        from matplotlib.patches import Patch
        if not any(getattr(h, 'get_label', lambda: '')().startswith('ADME')
                   for h in handles):
            handles.append(Patch(facecolor='white', edgecolor='#555', hatch='///',
                                 label='ADME/PK (hatched)'))
    ax.legend(handles=handles, loc='upper center', bbox_to_anchor=(0.5, -0.07),
              ncol=len(handles), frameon=False, fontsize=9)
    savefig(fig, out_dir, 'fig2_top20_ranking')


# ---------------------------------------------------------------- fig3 dist + enrichment
def fig_score_enrichment(df, enrich, disease, out_dir):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))
    # A: score distribution
    ax1.hist(df['combined_score'], bins=60, color=CB['blue'], alpha=0.85)
    ax1.set_yscale('log')
    ax1.set_xlabel('Combined score'); ax1.set_ylabel('Genes (log)')
    ax1.set_title('A. Combined-score distribution', fontsize=11, fontweight='bold', loc='left')
    # B: enrichment by bin
    if enrich and enrich.get('bins'):
        bins = enrich['bins']
        labels = [f"{b['rank_lo']}\u2013{b['rank_hi']}" for b in bins]
        folds = [b['fold_enrichment'] for b in bins]
        bars = ax2.bar(range(len(bins)), folds, color=CB['orange'], edgecolor='white')
        # headroom, then label the dashed background line via a LEGEND entry (no
        # leader line, no data-anchored text) so it can never collide with bars or
        # value labels regardless of the (possibly non-monotonic) bar heights.
        ymax = max(folds) * 1.18
        ax2.set_ylim(0, ymax)
        ax2.axhline(1.0, ls='--', lw=1, color=CB['grey'],
                    label='1\u00d7 = genome-wide background')
        ax2.legend(loc='upper right', frameon=False, fontsize=7.5,
                   handlelength=1.6, borderaxespad=0.3)
        ax2.set_xticks(range(len(bins))); ax2.set_xticklabels(labels, rotation=30, ha='right', fontsize=8)
        ax2.set_ylabel('Fold-enrichment vs background')
        ax2.set_xlabel('Rank bin')
        for b, f in zip(bars, folds):
            ax2.text(b.get_x() + b.get_width() / 2, f + ymax * 0.01, f"{f:.1f}x",
                     ha='center', va='bottom', fontsize=8)
    ax2.set_title('B. Known-target enrichment by rank bin', fontsize=11, fontweight='bold', loc='left')
    fig.suptitle(f'Ranking quality \u2014 {disease}', fontsize=13, fontweight='bold', y=1.02, x=0.02, ha='left')
    fig.tight_layout()
    savefig(fig, out_dir, 'fig3_score_enrichment')


# ---------------------------------------------------------------- fig4 evidence paths
def fig_evidence_paths(evidence, disease, out_dir, n=6, paths_per_target=4):
    import textwrap
    # preferred colors for the standard kinds; any other kind gets a palette color
    PREF = {'direct_seed': CB['red'], 'ppi_bridge': CB['green'],
            'shared_concept': CB['teal'], 'pathway_bridge': CB['teal'],
            'drug_target': CB['orange']}
    EXTRA = [CB['blue'], CB['purple'], CB['yellow'], CB['grey']]
    targets = list(evidence.items())[:n]
    if not targets:
        print('  (no evidence paths to plot)'); return

    # assign a stable color to EVERY kind that actually appears (no gray fallbacks
    # without a legend entry) -> the figure is robust to arbitrary path kinds
    present_kinds = []
    for _, paths in targets:
        for p in paths[:paths_per_target]:
            if p['kind'] not in present_kinds:
                present_kinds.append(p['kind'])
    kind_c, ei = {}, 0
    for k in present_kinds:
        if k in PREF:
            kind_c[k] = PREF[k]
        else:
            kind_c[k] = EXTRA[ei % len(EXTRA)]; ei += 1

    ncol = 2
    nrow = int(np.ceil(len(targets) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(13, 3.9 * nrow))
    axes = np.atleast_1d(axes).ravel()
    for ax in axes:
        ax.axis('off')
    for ax, (tsym, paths) in zip(axes, targets):
        ax.set_title(tsym, fontsize=12, fontweight='bold', loc='left')
        shown = paths[:paths_per_target]
        for j, p in enumerate(shown):
            y = 1 - (j + 0.5) / max(len(shown), 1)
            ax.plot([0.01, 0.045], [y, y], color=kind_c.get(p['kind'], CB['grey']),
                    lw=4, transform=ax.transAxes, solid_capstyle='round')
            # wrap to <=3 lines; only truncate if still longer
            lines = textwrap.wrap(p['text'], width=52)
            if len(lines) > 3:
                lines = lines[:3]; lines[-1] = lines[-1][:49] + '\u2026'
            ax.text(0.065, y, '\n'.join(lines), va='center', ha='left', fontsize=7.6,
                    transform=ax.transAxes, linespacing=1.12)
    from matplotlib.lines import Line2D
    handles = [Line2D([0], [0], color=kind_c[k], lw=4, label=k.replace('_', ' '))
               for k in present_kinds]
    fig.legend(handles=handles, loc='lower center', ncol=min(len(handles), 5),
               frameon=False, fontsize=9, bbox_to_anchor=(0.5, -0.01))
    fig.suptitle(f'Multi-hop evidence paths for top targets \u2014 {disease}',
                 fontsize=13, fontweight='bold', y=1.0)
    fig.tight_layout(rect=[0, 0.04, 1, 0.97])
    savefig(fig, out_dir, 'fig4_evidence_paths')


# ---------------------------------------------------------------- conditional: seed venn
def fig_seed_venn(df, out_dir):
    seed_cols = [c for c in df.columns if c.startswith('seed_')]
    if len(seed_cols) < 2:
        print('  (seed-venn skipped: < 2 anchors)'); return
    try:
        from matplotlib_venn import venn2, venn3
    except Exception:
        print('  (seed-venn skipped: matplotlib_venn not installed; uv pip install matplotlib-venn)')
        return
    sets = {c.replace('seed_', ''): set(df.index[df[c].astype(bool)]) for c in seed_cols}
    fig, ax = plt.subplots(figsize=(6, 6))
    names = list(sets)
    if len(sets) == 2:
        venn2([sets[names[0]], sets[names[1]]], set_labels=names, ax=ax)
    elif len(sets) == 3:
        venn3([sets[names[0]], sets[names[1]], sets[names[2]]], set_labels=names, ax=ax)
    else:
        print('  (seed-venn supports 2-3 anchors; use an UpSet plot for more)'); plt.close(fig); return
    ax.set_title('Seed-gene set intersections', fontsize=12, fontweight='bold')
    savefig(fig, out_dir, 'fig5_seed_intersections')


# ---------------------------------------------------------------- conditional: txgnn bar
def fig_txgnn(df, out_dir, n=15):
    if 'txgnn_support' not in df.columns or df['txgnn_support'].sum() == 0:
        print('  (txgnn-fig skipped: no TxGNN support)'); return
    t = df[df['txgnn_support'] > 0].nlargest(n, 'txgnn_support').iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = [status_of(dict(r))[1] for _, r in t.iterrows()]
    ax.barh(range(len(t)), t['txgnn_support'], color=colors, edgecolor='white')
    ax.set_yticks(range(len(t))); ax.set_yticklabels(t['gene'], fontsize=10)
    ax.set_xlabel('TxGNN drug-target support (summed drug scores)')
    ax.set_title('Highest TxGNN drug-target support', fontsize=12, fontweight='bold', loc='left')
    savefig(fig, out_dir, 'fig6_txgnn_support')


# ---------------------------------------------------------------- conditional: hero
def fig_hero(df, evidence, out_dir):
    if df.empty:
        return
    top_sym = df.iloc[0]['gene']
    paths = evidence.get(top_sym, [])
    if not paths:
        print(f'  (hero skipped: no evidence paths for #1 hit {top_sym})'); return
    groups = {'direct_seed': [], 'ppi_bridge': [], 'drug_target': [], 'shared_concept': []}
    for p in paths:
        groups.get(p['kind'], groups['shared_concept']).append(p)
    fig, ax = plt.subplots(figsize=(8.5, 6)); ax.axis('off')
    ax.text(0.5, 0.97, f"Convergent evidence for {top_sym} (rank #1)", ha='center',
            fontsize=15, fontweight='bold', transform=ax.transAxes)
    ax.add_patch(FancyBboxPatch((0.60, 0.44), 0.18, 0.12, boxstyle="round,pad=0.02,rounding_size=0.03",
                                facecolor=ACC1, alpha=0.85, edgecolor='none', transform=ax.transAxes))
    ax.text(0.69, 0.5, top_sym, ha='center', va='center', color='white', fontsize=14,
            fontweight='bold', transform=ax.transAxes)

    def _hero_label(p):
        """Clean, self-contained bullet label per path kind (never a raw truncated
        path string, which can cut mid-token and look like a placeholder)."""
        k = p.get('kind')
        if k == 'direct_seed':
            return 'direct disease\u2013gene edge (seed)'
        if k == 'ppi_bridge' and p.get('bridge'):
            return f"via {p['bridge']} (PPI)"
        if k == 'drug_target' and p.get('drug'):
            return p['drug']
        if k == 'shared_concept' and p.get('bridge'):
            return f"via {p['bridge']} (shared pathway)"
        b = p.get('bridge') or p.get('drug')
        if b:
            return str(b)
        # last resort: wrap, do not hard-cut mid-token
        import textwrap as _tw
        return _tw.shorten(p.get('text', ''), width=44, placeholder='\u2026')

    modes = [('Direct disease links', groups['direct_seed'], 0.85, CB['red']),
             ('PPI-bridged from seeds', groups['ppi_bridge'], 0.5, CB['green']),
             ('Approved / predicted drugs', groups['drug_target'], 0.15, CB['orange'])]
    for lab, items, y, col in modes:
        ax.text(0.04, y + 0.07, lab, fontsize=10, fontweight='bold', color=col, transform=ax.transAxes)
        if not items:
            ax.text(0.05, y, '\u2022 (none)', fontsize=8.5, color='#888', transform=ax.transAxes)
        for j, p in enumerate(items[:4]):
            ax.text(0.05, y - j * 0.05, '\u2022 ' + _hero_label(p),
                    fontsize=8.5, transform=ax.transAxes)
        ax.annotate('', xy=(0.60, 0.5), xytext=(0.42, y),
                    arrowprops=dict(arrowstyle='-|>', color=col, lw=1.6), transform=ax.transAxes)
    savefig(fig, out_dir, 'fig7_hero')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ranked', required=True)
    ap.add_argument('--evidence', default=None)
    ap.add_argument('--enrichment', default=None)
    ap.add_argument('--meta', default=None)
    ap.add_argument('--disease', default='the disease')
    ap.add_argument('--out', default='./figures')
    ap.add_argument('--seed-venn', action='store_true')
    ap.add_argument('--txgnn-fig', action='store_true')
    ap.add_argument('--hero', action='store_true')
    args = ap.parse_args()

    df = pd.read_csv(args.ranked)
    meta = json.load(open(args.meta)) if args.meta and os.path.exists(args.meta) else {}
    enrich = json.load(open(args.enrichment)) if args.enrichment and os.path.exists(args.enrichment) else {}
    evidence = json.load(open(args.evidence)) if args.evidence and os.path.exists(args.evidence) else {}

    print('CORE figures:')
    fig_infographic(df, meta, enrich, args.disease, args.out)
    fig_top20(df, args.disease, args.out)
    fig_score_enrichment(df, enrich, args.disease, args.out)
    fig_evidence_paths(evidence, args.disease, args.out)

    if args.seed_venn:
        print('conditional: seed venn'); fig_seed_venn(df, args.out)
    if args.txgnn_fig:
        print('conditional: txgnn'); fig_txgnn(df, args.out)
    if args.hero:
        print('conditional: hero'); fig_hero(df, evidence, args.out)
    print('done. MEDIA-CHECK every PNG before using in the report.')


if __name__ == '__main__':
    main()
