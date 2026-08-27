"""
Variant Visualization Module

This module provides robust PNG/SVG plots for variant annotation outputs using
matplotlib. It intentionally avoids plotnine so artifact generation is not tied
to fragile plotnine/matplotlib compatibility constraints.
"""

import sys
from pathlib import Path

try:
    import pandas as pd
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as e:
    print(f"Error: Missing required package: {e}")
    print("Install with: pip install pandas numpy matplotlib")
    sys.exit(1)


IMPACT_COLORS = {
    "HIGH": "#D32F2F",
    "MODERATE": "#F57C00",
    "LOW": "#FBC02D",
    "MODIFIER": "#7CB342",
}


def _style_axis(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.8)
    ax.set_axisbelow(True)


def _base_output_paths(base_path):
    base = str(Path(base_path).with_suffix(""))
    return f"{base}.png", f"{base}.svg"


def _save_figure(fig, base_path, width=8, height=6, dpi=300):
    """
    Save a matplotlib figure in both PNG and SVG formats.
    """
    fig.set_size_inches(width, height)
    fig.tight_layout()
    saved_paths = []

    for output_path in _base_output_paths(base_path):
        try:
            fig.savefig(output_path, dpi=dpi if output_path.endswith(".png") else None)
            print(f"   Saved: {output_path}")
            saved_paths.append(output_path)
        except Exception as e:
            print(f"   Warning: {Path(output_path).suffix.upper()[1:]} save failed: {e}")

    plt.close(fig)

    if not saved_paths:
        raise RuntimeError(f"Failed to save plot outputs for {base_path}")

    return saved_paths


def _chrom_sort_key(chrom):
    label = str(chrom)
    normalized = label[3:] if label.lower().startswith("chr") else label
    special = {"X": 23, "Y": 24, "M": 25, "MT": 25}
    if normalized in special:
        return special[normalized], label
    try:
        return int(normalized), label
    except ValueError:
        return 999, label


def plot_consequence_distribution(df, output_file="consequence_distribution.svg", top_n=15):
    """
    Plot distribution of variant consequence types.
    """
    if "Consequence" not in df.columns:
        print("Error: Consequence column not found")
        return

    consequences = []
    for consequence in df["Consequence"].dropna():
        consequences.extend(str(consequence).split("&"))

    if not consequences:
        print("Error: No valid consequence values found")
        return

    counts = pd.Series(consequences).value_counts().head(top_n).sort_values()

    fig, ax = plt.subplots()
    ax.barh(counts.index, counts.values, color="#0073C2")
    ax.set_title("Variant Consequence Distribution")
    ax.set_xlabel("Number of Variants")
    ax.set_ylabel("Consequence Type")
    _style_axis(ax)

    print("Saving consequence distribution plot...")
    _save_figure(fig, output_file, width=8, height=6, dpi=300)


def plot_impact_by_chromosome(df, output_file="impact_by_chromosome.svg"):
    """
    Plot variant impact distribution across chromosomes.
    """
    if "CHROM" not in df.columns or "IMPACT" not in df.columns:
        print("Error: CHROM or IMPACT column not found")
        return

    chroms = sorted(df["CHROM"].dropna().unique(), key=_chrom_sort_key)
    impact_order = ["HIGH", "MODERATE", "LOW", "MODIFIER"]
    extra_impacts = [value for value in sorted(df["IMPACT"].dropna().unique()) if value not in impact_order]
    impact_order.extend(extra_impacts)

    counts = (
        df.groupby(["CHROM", "IMPACT"])
        .size()
        .unstack(fill_value=0)
        .reindex(index=chroms, columns=impact_order, fill_value=0)
    )

    fig, ax = plt.subplots()
    bottom = np.zeros(len(counts))
    x = np.arange(len(counts.index))

    for impact in impact_order:
        values = counts[impact].to_numpy()
        if values.sum() == 0:
            continue
        ax.bar(
            x,
            values,
            bottom=bottom,
            label=impact,
            color=IMPACT_COLORS.get(impact, "#6A6A6A"),
        )
        bottom += values

    ax.set_title("Variant Impact by Chromosome")
    ax.set_xlabel("Chromosome")
    ax.set_ylabel("Number of Variants")
    ax.set_xticks(x)
    ax.set_xticklabels(counts.index, rotation=45, ha="right")
    ax.legend(title="Impact", frameon=False)
    _style_axis(ax)

    print("Saving impact by chromosome plot...")
    _save_figure(fig, output_file, width=12, height=6, dpi=300)


def plot_pathogenicity_scores(df, scores=None, output_file="pathogenicity_scores.svg"):
    """
    Plot distribution of pathogenicity scores.
    """
    if scores is None:
        scores = ["CADD_PHRED", "REVEL"]

    plot_data = {
        score_name: pd.to_numeric(df[score_name], errors="coerce").dropna()
        for score_name in scores
        if score_name in df.columns
    }
    plot_data = {score_name: values for score_name, values in plot_data.items() if len(values) > 0}

    if not plot_data:
        print("Error: No valid score columns found")
        return

    fig, axes = plt.subplots(1, len(plot_data), squeeze=False)
    axes = axes[0]

    for ax, (score_name, values) in zip(axes, plot_data.items()):
        bins = min(30, max(5, len(values)))
        ax.hist(values, bins=bins, color="#0073C2", alpha=0.75, edgecolor="white")
        ax.set_title(score_name)
        ax.set_xlabel("Score Value")
        ax.set_ylabel("Count")
        _style_axis(ax)

    fig.suptitle("Pathogenicity Score Distributions", y=1.03)

    print("Saving pathogenicity scores plot...")
    _save_figure(fig, output_file, width=max(6, 5 * len(plot_data)), height=5, dpi=300)


def plot_allele_frequency(df, population="gnomAD_AF", output_file="allele_frequency.svg", log_scale=True):
    """
    Plot allele frequency distribution.
    """
    if population not in df.columns:
        print(f"Error: {population} column not found")
        return

    af = pd.to_numeric(df[population], errors="coerce").dropna()
    af = af[af > 0]

    if len(af) == 0:
        print("Error: No valid allele frequencies found")
        return

    fig, ax = plt.subplots()
    if log_scale and af.min() > 0 and af.max() > af.min():
        bins = np.logspace(np.log10(af.min()), np.log10(af.max()), min(50, max(5, len(af))))
        ax.set_xscale("log")
    else:
        bins = min(50, max(5, len(af)))

    ax.hist(af, bins=bins, color="#0073C2", alpha=0.75, edgecolor="white")
    ax.set_title("Allele Frequency Distribution")
    ax.set_xlabel("Allele Frequency")
    ax.set_ylabel("Number of Variants")
    _style_axis(ax)

    print("Saving allele frequency plot...")
    _save_figure(fig, output_file, width=8, height=6, dpi=300)


def plot_gene_burden(gene_df, top_n=20, output_file="gene_burden.svg"):
    """
    Plot variants per gene (gene burden).
    """
    if "N_Variants" not in gene_df.columns:
        print("Error: N_Variants column not found")
        return

    gene_col = "SYMBOL" if "SYMBOL" in gene_df.columns else "Gene"
    if gene_col not in gene_df.columns:
        print("Error: Gene column not found")
        return

    top_genes = gene_df.nlargest(top_n, "N_Variants").sort_values("N_Variants")

    fig, ax = plt.subplots()
    ax.barh(top_genes[gene_col], top_genes["N_Variants"], color="#0073C2")
    ax.set_title(f"Top {top_n} Genes by Variant Count")
    ax.set_xlabel("Number of Variants")
    ax.set_ylabel("Gene")
    _style_axis(ax)

    print("Saving gene burden plot...")
    _save_figure(fig, output_file, width=8, height=8, dpi=300)


def plot_variant_quality(df, output_file="variant_quality.svg"):
    """
    Plot variant quality score distribution.
    """
    if "QUAL" not in df.columns:
        print("Error: QUAL column not found")
        return

    qual = pd.to_numeric(df["QUAL"], errors="coerce").dropna()

    if len(qual) == 0:
        print("Error: No valid quality scores found")
        return

    fig, ax = plt.subplots()
    counts, bins, _ = ax.hist(
        qual,
        bins=min(50, max(5, len(qual))),
        color="#0073C2",
        alpha=0.75,
        edgecolor="white",
    )
    ax.axvline(30, linestyle="--", color="red", linewidth=1)
    ax.text(35, max(counts) * 0.9 if len(counts) else 1, "Q30 threshold", color="red", ha="left")
    ax.set_title("Variant Quality Score Distribution")
    ax.set_xlabel("Quality Score (QUAL)")
    ax.set_ylabel("Number of Variants")
    _style_axis(ax)

    print("Saving quality distribution plot...")
    _save_figure(fig, output_file, width=8, height=6, dpi=300)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Plot variant distributions")
    parser.add_argument("input_csv", help="Input CSV file with variant annotations")
    parser.add_argument("--consequence", help="Output file for consequence plot")
    parser.add_argument("--impact-chr", help="Output file for impact by chromosome plot")
    parser.add_argument("--scores", help="Output file for pathogenicity scores plot")
    parser.add_argument("--frequency", help="Output file for allele frequency plot")

    args = parser.parse_args()

    df = pd.read_csv(args.input_csv)
    print(f"Loaded {len(df)} variants")

    if args.consequence:
        plot_consequence_distribution(df, output_file=args.consequence)

    if args.impact_chr:
        plot_impact_by_chromosome(df, output_file=args.impact_chr)

    if args.scores:
        plot_pathogenicity_scores(df, output_file=args.scores)

    if args.frequency:
        plot_allele_frequency(df, output_file=args.frequency)
