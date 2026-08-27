"""
Generate publication-quality visualizations for ADMET / developability analysis.

Produces 4 robust, N-aware plots (every one renders from a few molecules to
thousands):
1. Physicochemical property distributions (small-multiples)
2. Lipinski chemical space (scatter at small N, density at large N)
3. Developability ranking (top-N by QED, colored by PAINS)
4. ADMET endpoint heatmap (registry-driven, bounded row count)
"""

import os
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# Resolve the sibling registry import regardless of load mechanism / cwd (see note in
# compute_properties.py — Biomni does not guarantee the skill dir is on sys.path).
import sys as _sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
try:
    from admet_endpoints import resolve_endpoints
except ImportError:  # last resort: imported as part of the 'scripts' package
    from scripts.admet_endpoints import resolve_endpoints

# Project standard: seaborn ticks + Arial-metric-equivalent sans-serif.
# Liberation Sans / Arimo are metric-compatible with Arial and ship on most Linux
# images; fall back to DejaVu Sans. Keep SVG text editable (not outlined paths).
sns.set_style("ticks")
plt.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
plt.rcParams["font.sans-serif"] = ["Liberation Sans", "Arimo", "DejaVu Sans", "Helvetica"]
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["figure.dpi"] = 300

# Scaling thresholds (one place to tune N-aware behavior)
LABEL_THRESHOLD = 50    # above this many points, drop per-point text labels
MAX_HEATMAP_ROWS = 50   # cap heatmap rows so figure height stays bounded
TOP_N_BARS = 30         # bars shown in the developability ranking


def _save_plot(fig, base_path):
    """Save plot as PNG + SVG with graceful SVG fallback."""
    png_path = os.path.splitext(base_path)[0] + ".png"
    svg_path = os.path.splitext(base_path)[0] + ".svg"

    fig.savefig(png_path, dpi=300, bbox_inches="tight", facecolor="white")
    print(f"   Saved: {png_path}")

    try:
        fig.savefig(svg_path, format="svg", bbox_inches="tight", facecolor="white")
        print(f"   Saved: {svg_path}")
    except Exception:
        print(f"   (SVG export failed for {svg_path})")

    plt.close(fig)


def _row_labels(df):
    """Readable, unique per-row labels for plotting (display name, disambiguated by mol_id)."""
    if "name" in df.columns:
        labels = df["name"].astype(str)
    else:
        labels = pd.Series([""] * len(df), index=df.index)
    if "mol_id" in df.columns:
        labels = labels.where(labels.str.strip() != "", df["mol_id"].astype(str))
        dup = labels.duplicated(keep=False)
        if dup.any():
            labels = labels.mask(dup, labels + " [" + df["mol_id"].astype(str) + "]")
    return labels


# =============================================================================
# Plot 1: Physicochemical property distributions (small-multiples)
# =============================================================================

def plot_physicochemical_overview(df, output_dir):
    """2x3 grid of physicochemical distributions with drug-like reference lines."""
    print("   Generating physicochemical distributions...")

    specs = [
        ("MW", 500, "Molecular Weight (Da)"),
        ("LogP", 5, "LogP"),
        ("TPSA", 140, "TPSA (Å²)"),
        ("HBD", 5, "H-Bond Donors"),
        ("HBA", 10, "H-Bond Acceptors"),
        ("RotatableBonds", 10, "Rotatable Bonds"),
    ]
    avail = [(c, t, l) for c, t, l in specs if c in df.columns]
    if len(avail) < 4:
        print("   WARNING: not enough physicochemical columns, skipping")
        return

    fig, axes = plt.subplots(2, 3, figsize=(14, 8))
    flat = axes.flat
    for ax, (col, thr, label) in zip(flat, avail):
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(vals) == 0:
            ax.axis("off")
            continue
        sns.histplot(vals, ax=ax, color="#4c72b0", edgecolor="white",
                     kde=(len(vals) >= 10))
        ax.axvline(thr, color="#e74c3c", linestyle="--", linewidth=1.6,
                   label=f"Ro5/Veber \u2264 {thr}")
        ax.set_xlabel(label, fontsize=13)
        ax.set_ylabel("Count", fontsize=13)
        ax.tick_params(axis="both", labelsize=11)
        ax.margins(x=0.06)  # keep edge reference lines off the frame
        ax.legend(fontsize=10)
    for ax in list(flat)[len(avail):]:
        ax.axis("off")

    fig.suptitle(f"Physicochemical Property Distributions (n={len(df)})",
                 fontsize=16, fontweight="bold")
    fig.tight_layout()
    _save_plot(fig, os.path.join(output_dir, "physicochemical_overview.png"))


# =============================================================================
# Plot 2: Lipinski Chemical Space (N-aware)
# =============================================================================

def plot_lipinski_space(df, output_dir):
    """MW vs LogP; scatter+labels at small N, hexbin density at large N."""
    print("   Generating Lipinski chemical space plot...")

    if "MW" not in df.columns or "LogP" not in df.columns:
        print("   WARNING: MW/LogP not available, skipping")
        return

    d = df.copy()
    d["MW"] = pd.to_numeric(d["MW"], errors="coerce")
    d["LogP"] = pd.to_numeric(d["LogP"], errors="coerce")
    d = d.dropna(subset=["MW", "LogP"])
    if d.empty:
        print("   WARNING: no valid MW/LogP values, skipping")
        return

    fig, ax = plt.subplots(figsize=(10, 7))
    n = len(d)

    if n > LABEL_THRESHOLD:
        # Density view — readable for hundreds/thousands of compounds
        hb = ax.hexbin(d["MW"], d["LogP"], gridsize=40, cmap="Blues", mincnt=1, zorder=3)
        plt.colorbar(hb, ax=ax, label="Compound count", shrink=0.8)
    else:
        viol = "Lipinski_Violations" if "Lipinski_Violations" in d.columns else None
        if viol:
            sc = ax.scatter(d["MW"], d["LogP"], c=pd.to_numeric(d[viol], errors="coerce"),
                            cmap="cividis", s=80, edgecolors="black", linewidth=0.5,
                            vmin=0, vmax=3, zorder=5)
            plt.colorbar(sc, ax=ax, label="Lipinski Violations", shrink=0.8)
        else:
            ax.scatter(d["MW"], d["LogP"], c="#3498db", s=80,
                       edgecolors="black", linewidth=0.5, zorder=5)
        labels = _row_labels(d)
        # Declutter overlapping compound labels with adjustText when available.
        try:
            from adjustText import adjust_text
            texts = [ax.text(row["MW"], row["LogP"], lab, fontsize=7.5)
                     for (_, row), lab in zip(d.iterrows(), labels)]
            adjust_text(texts, ax=ax,
                        expand=(1.25, 1.6), force_text=(0.4, 0.7),
                        arrowprops=dict(arrowstyle="-", color="#999999", lw=0.5))
        except Exception:
            for (_, row), lab in zip(d.iterrows(), labels):
                ax.annotate(lab, (row["MW"], row["LogP"]), fontsize=7, alpha=0.75,
                            ha="center", va="bottom", xytext=(0, 5),
                            textcoords="offset points")

    # Lipinski boundaries + drug-like zone (data coordinates)
    ax.axvline(x=500, color="#444444", linestyle="--", linewidth=1.5, alpha=0.8, label="MW = 500")
    ax.axhline(y=5, color="#444444", linestyle="--", linewidth=1.5, alpha=0.8, label="LogP = 5")
    xlim, ylim = ax.get_xlim(), ax.get_ylim()
    ax.add_patch(plt.Rectangle((xlim[0], ylim[0]), 500 - xlim[0], 5 - ylim[0],
                               color="#0072B2", alpha=0.07, zorder=0))
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)

    # Add a little horizontal head-room so edge labels are not clipped by the colorbar.
    cur = ax.get_xlim()
    ax.set_xlim(cur[0], cur[1] + 0.06 * (cur[1] - cur[0]))
    ax.set_xlabel("Molecular Weight (Da)", fontsize=13)
    ax.set_ylabel("LogP", fontsize=13)
    ax.tick_params(axis="both", labelsize=11)
    ax.set_title(f"Lipinski Chemical Space (n={n})", fontsize=15, fontweight="bold")
    ax.legend(fontsize=9, loc="upper left")
    sns.despine()

    _save_plot(fig, os.path.join(output_dir, "lipinski_space.png"))


# =============================================================================
# Plot 3: Developability Ranking (top-N by QED, flagged for PAINS)
# =============================================================================

def plot_developability(df, output_dir):
    """Horizontal bar of QED for the top-N most developable, colored by PAINS."""
    print("   Generating developability (QED) ranking...")

    if "QED" not in df.columns:
        print("   WARNING: No QED column found, skipping")
        return

    d = df.copy()
    d["QED"] = pd.to_numeric(d["QED"], errors="coerce")
    d["__label"] = _row_labels(d)
    d["__pains"] = (pd.to_numeric(d["PAINS_Count"], errors="coerce").fillna(0) > 0) \
        if "PAINS_Count" in d.columns else False
    d = d.dropna(subset=["QED"]).sort_values("QED")
    if d.empty:
        print("   WARNING: No valid QED values, skipping")
        return

    truncated = len(d) > TOP_N_BARS
    if truncated:
        d = d.tail(TOP_N_BARS)  # most developable

    colors = ["#E69F00" if p else "#0072B2" for p in d["__pains"]]

    fig, ax = plt.subplots(figsize=(8, max(4, len(d) * 0.3)))
    ax.barh(d["__label"], d["QED"], color=colors, edgecolor="black", linewidth=0.5, zorder=3)
    ax.axvline(0.5, color="#7f8c8d", linestyle="--", linewidth=1.2,
               label="QED = 0.5 (typical oral drug)", zorder=2)
    ax.set_xlim(0, 1)
    ax.set_xlabel("QED (drug-likeness)", fontsize=12)
    title = f"Top {len(d)} by QED" if truncated else "Developability Ranking by QED"
    ax.set_title(title, fontsize=14, fontweight="bold")

    from matplotlib.patches import Patch
    handles = [
        Patch(facecolor="#0072B2", edgecolor="black", label="No PAINS alert"),
        Patch(facecolor="#E69F00", edgecolor="black", label="PAINS alert"),
    ]
    leg1 = ax.legend(handles=handles, loc="lower right", fontsize=9, title="Structural alert")
    ax.add_artist(leg1)
    ax.legend(loc="upper left", fontsize=8)
    sns.despine()

    _save_plot(fig, os.path.join(output_dir, "developability_qed.png"))


# =============================================================================
# Plot 4: ADMET Endpoint Heatmap (registry-driven, bounded rows)
# =============================================================================

def plot_admet_heatmap(df, output_dir):
    """Traffic-light heatmap of classification ADMET endpoints (registry-resolved)."""
    print("   Generating ADMET endpoint heatmap...")

    resolved = resolve_endpoints(df.columns)
    ordered = [(k, c) for k, c in resolved.items() if c]
    if len(ordered) < 3:
        print("   WARNING: fewer than 3 ADMET endpoints present, skipping heatmap")
        return

    # Build matrix keyed by readable label; keep only 0–1 classification endpoints
    # so the probability colormap and clustering are interpretable.
    mat = pd.DataFrame({k: pd.to_numeric(df[c], errors="coerce") for k, c in ordered})
    mat.index = _row_labels(df)
    keep = [k for k in mat.columns
            if mat[k].notna().any() and mat[k].dropna().between(0, 1).all()]
    mat = mat[keep].dropna(axis=0, how="any")
    if mat.shape[1] < 3 or mat.shape[0] < 2:
        print("   WARNING: not enough complete classification-endpoint data, skipping heatmap")
        return
    mat.index.name = None   # avoid a stray axis-title artifact ("name") on the plot/colorbar

    # Cap rows (highest mean predicted risk first) so figure height stays bounded
    if len(mat) > MAX_HEATMAP_ROWS:
        score = mat.mean(axis=1)
        mat = mat.loc[score.sort_values(ascending=False).head(MAX_HEATMAP_ROWS).index]
        print(f"   (showing top {MAX_HEATMAP_ROWS} of {len(df)} compounds by mean predicted risk)")

    # Colorblind-safe sequential map (viridis): low prob = dark purple, high prob = yellow.
    # High predicted risk is the bright, salient end while remaining deuteran/protan-safe.
    cmap = "viridis"
    annot = len(mat) <= 20

    # Taller/wider figure; enlarged labels for legibility when embedded in the PDF.
    g = sns.clustermap(
        mat, cmap=cmap, vmin=0, vmax=1,
        figsize=(max(11, len(mat.columns) * 0.80 + 5.0),
                 min(MAX_HEATMAP_ROWS * 0.42 + 3.0, max(8, len(mat) * 0.42 + 3.0))),
        linewidths=0.5, linecolor="white",
        cbar_kws={"label": "Predicted probability", "orientation": "horizontal"},
        annot=annot, fmt=".2f" if annot else "",
        annot_kws={"fontsize": 7} if annot else None,
        dendrogram_ratio=0.12, xticklabels=True, yticklabels=True,
    )
    # Reserve space on the right (long names) and at the bottom (colorbar).
    g.fig.subplots_adjust(right=0.80, bottom=0.16)
    # Manually park the colorbar as a horizontal bar centred along the BOTTOM,
    # clear of the dendrogram and heatmap (figure-relative coords: left,bottom,w,h).
    g.ax_cbar.set_position([0.34, 0.045, 0.32, 0.022])
    g.ax_cbar.tick_params(labelsize=10)
    g.ax_cbar.set_xlabel("Predicted probability", fontsize=11)
    g.fig.suptitle("ADMET Endpoint Heatmap", y=1.02, fontsize=16, fontweight="bold")
    plt.setp(g.ax_heatmap.get_xticklabels(), rotation=45, ha="right", fontsize=11)
    plt.setp(g.ax_heatmap.get_yticklabels(), fontsize=10, rotation=0)

    _save_plot(g.fig, os.path.join(output_dir, "admet_heatmap.png"))


# =============================================================================
# Generate all plots
# =============================================================================

def generate_all_plots(results_df, output_dir="results"):
    """
    Generate all 4 ADMET / developability visualizations.

    Each plot is isolated: one failing plot logs a warning and the rest still run.

    Parameters
    ----------
    results_df : pd.DataFrame
        Full results from run_full_analysis().
    output_dir : str
        Directory to save plots.
    """
    os.makedirs(output_dir, exist_ok=True)
    print("\nGenerating visualizations...")

    plots = [
        ("physicochemical_overview", plot_physicochemical_overview),
        ("lipinski_space", plot_lipinski_space),
        ("developability_qed", plot_developability),
        ("admet_heatmap", plot_admet_heatmap),
    ]
    for label, fn in plots:
        try:
            fn(results_df, output_dir)
        except Exception as e:
            print(f"   ⚠️  {label} failed: {e}")

    print("\n✓ All plots generated successfully!")


if __name__ == "__main__":
    print("Run via the main workflow — not standalone.")
