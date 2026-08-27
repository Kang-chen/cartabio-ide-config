"""
Visualize primer binding sites and properties.

This module creates publication-quality visualizations using matplotlib
following the repository standards.
"""

import os
import matplotlib
matplotlib.use('Agg')  # non-interactive backend for headless rendering
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from typing import Dict, List

# Publication-quality defaults (Liberation Sans = Arial-metric equivalent)
matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']
matplotlib.rcParams['svg.fonttype'] = 'none'  # keep SVG text editable

# Color palette (colorblind-friendly, matches original plotnine values)
_COLOR_FORWARD = '#E74C3C'   # red
_COLOR_REVERSE = '#3498DB'   # blue
_COLOR_AMPLICON = '#95A5A6'  # gray
_COLOR_PASS = '#27AE60'      # green
_COLOR_FAIL = '#E74C3C'      # red


def _save_figure(fig: plt.Figure, output_file: str, width: float, height: float):
    """Save a matplotlib figure to *output_file* at 300 DPI.

    A thin helper so every plot uses consistent export settings and the
    original ``print(...)`` confirmation message is emitted uniformly.
    """
    fig.set_size_inches(width, height)
    fig.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close(fig)
    print(f"Plot saved: {output_file}")


def _set_title_and_legend(
    fig: plt.Figure,
    title: str,
    handles: List[plt.Line2D],
    ncol: int,
):
    """Place the title and legend in reserved figure space.

    Keeping legends in figure-level space avoids title/legend overlap and keeps
    the plot area stable when the figure is embedded in PDF reports.
    """
    fig.suptitle(title, y=0.98)
    fig.legend(
        handles=handles,
        loc='upper center',
        bbox_to_anchor=(0.5, 0.91),
        ncol=ncol,
        frameon=False,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.80))


def plot_primer_alignment(
    sequence: str,
    primers: Dict,
    output_file: str,
    show_sequence: bool = False
):
    """
    Visualize primer positions on target sequence.

    Creates a publication-quality plot showing primer binding sites.

    Parameters
    ----------
    sequence : str
        Target DNA sequence
    primers : dict
        Primer pair dictionary with positions
    output_file : str
        Output file path (SVG recommended)
    show_sequence : bool
        Show sequence at primer positions. Default: False

    Example
    -------
    >>> plot_primer_alignment(
    ...     sequence="ATGC..." * 100,
    ...     primers=primer_pair,
    ...     output_file="primer_alignment.svg"
    ... )
    """

    # Prepare data
    seq_length = len(sequence)

    plot_data = []

    # Forward primer
    plot_data.append({
        'type': 'Forward Primer',
        'start': primers['forward_pos'],
        'end': primers['forward_pos'] + primers['forward_length'],
        'y': 1,
        'label': f"F: {primers['forward_tm']:.1f}°C",
        'sequence': primers['forward_seq'],
        'color': _COLOR_FORWARD,
    })

    # Reverse primer
    plot_data.append({
        'type': 'Reverse Primer',
        'start': primers['reverse_pos'] - primers['reverse_length'],
        'end': primers['reverse_pos'],
        'y': 1,
        'label': f"R: {primers['reverse_tm']:.1f}°C",
        'sequence': primers['reverse_seq'],
        'color': _COLOR_REVERSE,
    })

    # Amplicon
    plot_data.append({
        'type': 'Amplicon',
        'start': primers['forward_pos'],
        'end': primers['reverse_pos'],
        'y': 0.5,
        'label': f"{primers['amplicon_size']} bp",
        'sequence': '',
        'color': _COLOR_AMPLICON,
    })

    df = pd.DataFrame(plot_data)

    # Create plot
    fig, ax = plt.subplots()

    for _, row in df.iterrows():
        ax.plot([row['start'], row['end']], [row['y'], row['y']],
                color=row['color'], linewidth=6, solid_capstyle='butt')
        mid = (row['start'] + row['end']) / 2
        ax.text(mid, row['y'] + 0.12, row['label'],
                ha='center', va='bottom', fontsize=9)

    ax.set_xlabel('Position (bp)')
    ax.set_ylabel('')
    ax.set_ylim(-0.5, 2)
    ax.set_xlim(0, seq_length)
    ax.set_yticks([])
    _set_title_and_legend(
        fig,
        'Primer Binding Sites',
        [
            plt.Line2D([0], [0], color=_COLOR_FORWARD, linewidth=6, label='Forward Primer'),
            plt.Line2D([0], [0], color=_COLOR_REVERSE, linewidth=6, label='Reverse Primer'),
            plt.Line2D([0], [0], color=_COLOR_AMPLICON, linewidth=6, label='Amplicon'),
        ],
        ncol=3,
    )

    _save_figure(fig, output_file, width=10, height=4)
    print(f"Primer alignment plot saved: {output_file}")


def plot_tm_distribution(
    primer_set: List[Dict],
    output_file: str
):
    """
    Plot melting temperature distribution for primer set.

    Parameters
    ----------
    primer_set : list of dict
        List of primer pairs
    output_file : str
        Output file path (SVG recommended)

    Example
    -------
    >>> plot_tm_distribution(
    ...     primer_set=primers['primers'],
    ...     output_file="tm_distribution.svg"
    ... )
    """

    # Prepare data
    tm_data = []
    for i, pair in enumerate(primer_set, 1):
        tm_data.append({
            'Pair': f"Pair {i}",
            'Primer': 'Forward',
            'Tm': pair['forward_tm'],
        })
        tm_data.append({
            'Pair': f"Pair {i}",
            'Primer': 'Reverse',
            'Tm': pair['reverse_tm'],
        })

    df = pd.DataFrame(tm_data)

    # Create plot
    fig, ax = plt.subplots()

    pairs = sorted(df['Pair'].unique())
    x = np.arange(len(pairs))
    bar_width = 0.35

    fwd = df[df['Primer'] == 'Forward']['Tm'].values
    rev = df[df['Primer'] == 'Reverse']['Tm'].values

    ax.bar(x - bar_width / 2, fwd, bar_width, label='Forward', color=_COLOR_FORWARD)
    ax.bar(x + bar_width / 2, rev, bar_width, label='Reverse', color=_COLOR_REVERSE)

    ax.axhline(y=60, color='gray', linestyle='--', alpha=0.5)

    ax.set_xlabel('Primer Pair')
    ax.set_ylabel('Melting Temperature (°C)')
    ax.set_xticks(x)
    ax.set_xticklabels(pairs, rotation=45, ha='right')
    _set_title_and_legend(
        fig,
        'Melting Temperature Distribution',
        [
            plt.Line2D([0], [0], color=_COLOR_FORWARD, linewidth=6, label='Forward'),
            plt.Line2D([0], [0], color=_COLOR_REVERSE, linewidth=6, label='Reverse'),
        ],
        ncol=2,
    )

    _save_figure(fig, output_file, width=8, height=6)
    print(f"Tm distribution plot saved: {output_file}")


def plot_primer_properties(
    primer_set: List[Dict],
    output_file: str
):
    """
    Plot primer properties (Tm, GC%, length) for comparison.

    Parameters
    ----------
    primer_set : list of dict
        List of primer pairs
    output_file : str
        Output file path

    Example
    -------
    >>> plot_primer_properties(
    ...     primer_set=primers['primers'][:5],
    ...     output_file="primer_properties.svg"
    ... )
    """

    # Prepare data
    prop_data = []
    for i, pair in enumerate(primer_set, 1):
        for primer_type, prefix in [('Forward', 'forward'), ('Reverse', 'reverse')]:
            prop_data.append({
                'Pair': f"Pair {i}",
                'Type': primer_type,
                'Tm (°C)': pair[f'{prefix}_tm'],
                'GC (%)': pair[f'{prefix}_gc'],
                'Length (bp)': pair[f'{prefix}_length'],
            })

    df = pd.DataFrame(prop_data)

    pairs = sorted(df['Pair'].unique())
    x = np.arange(len(pairs))

    # Build sub-plot filenames in an extension-agnostic way so that both
    # .svg and .png (and any other extension) produce distinct _tm / _gc
    # files. The previous output_file.replace('.svg', '_tm.svg') only
    # matched .svg paths; for .png it left the name unchanged, so both
    # sub-plots were written to the same file and one was silently lost.
    base, ext = os.path.splitext(output_file)
    tm_file = f"{base}_tm{ext}"
    gc_file = f"{base}_gc{ext}"

    # Create Tm plot
    fig_tm, ax_tm = plt.subplots()
    for primer_type, color in [('Forward', _COLOR_FORWARD), ('Reverse', _COLOR_REVERSE)]:
        sub = df[df['Type'] == primer_type]
        ax_tm.scatter(x, sub['Tm (°C)'].values, color=color, label=primer_type, zorder=3)
    ax_tm.axhline(y=58, color='gray', linestyle='--', alpha=0.3)
    ax_tm.axhline(y=62, color='gray', linestyle='--', alpha=0.3)
    ax_tm.set_xlabel('')
    ax_tm.set_ylabel('Tm (°C)')
    ax_tm.set_xticks(x)
    ax_tm.set_xticklabels(pairs)
    _set_title_and_legend(
        fig_tm,
        'Primer Melting Temperatures',
        [
            plt.Line2D([0], [0], color=_COLOR_FORWARD, marker='o', linewidth=0, label='Forward'),
            plt.Line2D([0], [0], color=_COLOR_REVERSE, marker='o', linewidth=0, label='Reverse'),
        ],
        ncol=2,
    )

    _save_figure(fig_tm, tm_file, width=8, height=4)

    # Create GC% plot
    fig_gc, ax_gc = plt.subplots()
    for primer_type, color in [('Forward', _COLOR_FORWARD), ('Reverse', _COLOR_REVERSE)]:
        sub = df[df['Type'] == primer_type]
        ax_gc.scatter(x, sub['GC (%)'].values, color=color, label=primer_type, zorder=3)
    ax_gc.axhline(y=40, color='gray', linestyle='--', alpha=0.3)
    ax_gc.axhline(y=60, color='gray', linestyle='--', alpha=0.3)
    ax_gc.set_xlabel('')
    ax_gc.set_ylabel('GC (%)')
    ax_gc.set_xticks(x)
    ax_gc.set_xticklabels(pairs)
    _set_title_and_legend(
        fig_gc,
        'Primer GC Content',
        [
            plt.Line2D([0], [0], color=_COLOR_FORWARD, marker='o', linewidth=0, label='Forward'),
            plt.Line2D([0], [0], color=_COLOR_REVERSE, marker='o', linewidth=0, label='Reverse'),
        ],
        ncol=2,
    )

    _save_figure(fig_gc, gc_file, width=8, height=4)

    print(f"Primer property plots saved: {base}_*{ext}")


def plot_amplicon_sizes(
    primer_set: List[Dict],
    output_file: str,
    target_range: tuple = None
):
    """
    Plot amplicon size distribution.

    Parameters
    ----------
    primer_set : list of dict
        List of primer pairs
    output_file : str
        Output file path
    target_range : tuple, optional
        (min, max) target amplicon size for reference lines

    Example
    -------
    >>> plot_amplicon_sizes(
    ...     primer_set=primers['primers'],
    ...     output_file="amplicon_sizes.svg",
    ...     target_range=(70, 140)
    ... )
    """

    # Prepare data
    amp_data = []
    for i, pair in enumerate(primer_set, 1):
        amp_data.append({
            'Pair': f"Pair {i}",
            'Amplicon Size (bp)': pair['amplicon_size'],
            'In Range': target_range is None or (
                target_range[0] <= pair['amplicon_size'] <= target_range[1]
            )
        })

    df = pd.DataFrame(amp_data)

    # Create plot
    fig, ax = plt.subplots()

    pairs = sorted(df['Pair'].unique())
    x = np.arange(len(pairs))
    sizes = df['Amplicon Size (bp)'].values
    in_range = df['In Range'].values
    colors = [_COLOR_PASS if r else _COLOR_FAIL for r in in_range]

    ax.bar(x, sizes, color=colors)

    if target_range:
        ax.axhline(y=target_range[0], color='gray', linestyle='--', alpha=0.3)
        ax.axhline(y=target_range[1], color='gray', linestyle='--', alpha=0.3)

    ax.set_xlabel('Primer Pair')
    ax.set_ylabel('Amplicon Size (bp)')
    ax.set_xticks(x)
    ax.set_xticklabels(pairs, rotation=45, ha='right')
    _set_title_and_legend(
        fig,
        'Amplicon Sizes',
        [
            plt.Line2D([0], [0], color=_COLOR_PASS, linewidth=6, label='Within Target'),
            plt.Line2D([0], [0], color=_COLOR_FAIL, linewidth=6, label='Outside Target'),
        ],
        ncol=2,
    )

    _save_figure(fig, output_file, width=8, height=6)
    print(f"Amplicon size plot saved: {output_file}")


def plot_qc_summary(
    primer_set: List[Dict],
    validation_results: List[Dict],
    output_file: str
):
    """
    Create a QC summary visualization.

    Parameters
    ----------
    primer_set : list of dict
        List of primer pairs
    validation_results : list of dict
        Validation results for each pair
    output_file : str
        Output file path

    Example
    -------
    >>> plot_qc_summary(
    ...     primer_set=primers['primers'],
    ...     validation_results=[val1, val2, val3],
    ...     output_file="qc_summary.svg"
    ... )
    """

    # Prepare data
    qc_data = []
    for i, (pair, validation) in enumerate(zip(primer_set, validation_results), 1):
        qc_checks = {
            'Tm Match': pair.get('tm_diff', 0) <= 2.0,
            'GC Content': (40 <= pair.get('forward_gc', 0) <= 60 and
                          40 <= pair.get('reverse_gc', 0) <= 60),
            'No Dimers': not validation.get('dimers', {}).get('has_issues', True),
            'Specific': validation.get('specificity', {}).get('is_specific', False),
        }

        for check_name, passes in qc_checks.items():
            qc_data.append({
                'Pair': f"Pair {i}",
                'QC Check': check_name,
                'Status': 'Pass' if passes else 'Fail',
            })

    df = pd.DataFrame(qc_data)

    # Create plot (grid of Pass/Fail markers)
    fig, ax = plt.subplots()

    pairs = sorted(df['Pair'].unique())
    checks = list(dict.fromkeys(df['QC Check']))  # preserve order, unique

    for row_idx, pair in enumerate(pairs):
        for col_idx, check in enumerate(checks):
            sub = df[(df['Pair'] == pair) & (df['QC Check'] == check)]
            if not sub.empty:
                status = sub['Status'].values[0]
                color = _COLOR_PASS if status == 'Pass' else _COLOR_FAIL
                marker = 'o' if status == 'Pass' else 'X'
                ax.scatter(col_idx, row_idx, color=color, marker=marker, s=120, zorder=3)

    ax.set_xlabel('Quality Check')
    ax.set_ylabel('Primer Pair')
    ax.set_xticks(range(len(checks)))
    ax.set_xticklabels(checks, rotation=45, ha='right')
    ax.set_yticks(range(len(pairs)))
    ax.set_yticklabels(pairs)
    ax.set_xlim(-0.5, len(checks) - 0.5)
    ax.set_ylim(-0.5, len(pairs) - 0.5)
    ax.invert_yaxis()
    _set_title_and_legend(
        fig,
        'QC Summary',
        [
            plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=_COLOR_PASS,
                       markersize=10, label='Pass'),
            plt.Line2D([0], [0], marker='X', color='w', markerfacecolor=_COLOR_FAIL,
                       markersize=10, label='Fail'),
        ],
        ncol=2,
    )

    _save_figure(fig, output_file, width=8, height=6)
    print(f"QC summary plot saved: {output_file}")
