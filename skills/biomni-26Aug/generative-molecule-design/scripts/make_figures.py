"""
Publication-quality figures for the generative-design pipeline.

All figures use a colorblind-friendly palette, Liberation Sans, editable-text SVG
+ PNG, and single-line legends (multi-line legends break RDKit's grid renderer).
Molecule grids use rdCoordGen for clean 2D layouts and MolDraw2DCairo/SVG.

Every figure returned here should be passed through the media_output_check QC in
the SKILL.md workflow before being embedded in the report; regenerate any figure
that comes back blank/clipped/tangled.
"""
from __future__ import annotations
import os
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import Draw, rdCoordGen
from rdkit.Chem.Draw import rdMolDraw2D

matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"

# Colorblind-friendly palette (Phylo-aligned).
CB = {"blue": "#0279EE", "orange": "#FF9400", "green": "#75A025",
      "pink": "#FD9BED", "dark": "#222222", "grey": "#9AA0A6", "gold": "#D4A04A"}


def _save(fig, path_noext: str, formats=("png", "svg")):
    for fmt in formats:
        fig.savefig(f"{path_noext}.{fmt}", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return f"{path_noext}.png"


# ---------------------------------------------------------------- convergence
def fig_convergence(history: dict, out_noext: str, activity_label: str = "objective"):
    """GA best/mean fitness + cumulative unique molecules per generation."""
    g = history["gen"]
    fig, ax1 = plt.subplots(figsize=(7, 4.3))
    ax1.plot(g, history["best"], "-o", color=CB["blue"], lw=2, ms=4, label="Best fitness")
    ax1.plot(g, history["mean"], "-s", color=CB["orange"], lw=2, ms=4, label="Mean fitness")
    ax1.set_xlabel("Generation")
    ax1.set_ylabel("Fitness (composite desirability)")
    ax1.set_ylim(0, 1.02)
    ax1.grid(alpha=0.25)
    ax2 = ax1.twinx()
    ax2.plot(g, history["n_unique"], "--^", color=CB["green"], lw=1.6, ms=4,
             label="Cumulative unique molecules")
    ax2.set_ylabel("Cumulative unique molecules", color=CB["green"])
    ax2.tick_params(axis="y", labelcolor=CB["green"])
    lines1, lab1 = ax1.get_legend_handles_labels()
    lines2, lab2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, lab1 + lab2, loc="lower right", fontsize=8, framealpha=0.9)
    ax1.set_title("Genetic-algorithm convergence", fontsize=12, fontweight="bold")
    return _save(fig, out_noext)


# ------------------------------------------------------- activity vs QED scatter
def fig_activity_qed(df: pd.DataFrame, top: pd.DataFrame, out_noext: str,
                     activity_label: str = "Activity score"):
    """Scatter of the whole library in activity-QED space; top designs highlighted."""
    fig, ax = plt.subplots(figsize=(6.4, 5))
    sc = ax.scatter(df["activity"], df["QED"], c=df["combined"], cmap="viridis",
                    s=14, alpha=0.6, edgecolors="none")
    ax.scatter(top["activity"], top["QED"], s=90, facecolors="none",
               edgecolors=CB["orange"], linewidths=1.8, label="Selected top designs")
    cb = fig.colorbar(sc, ax=ax)
    cb.set_label("Composite fitness")
    ax.set_xlabel(activity_label)
    ax.set_ylabel("QED (drug-likeness)")
    ax.set_title("Generated library in activity-QED space", fontsize=12, fontweight="bold")
    ax.legend(loc="lower left", fontsize=8)
    ax.grid(alpha=0.25)
    return _save(fig, out_noext)


# --------------------------------------------------------- property distributions
def fig_property_distributions(df: pd.DataFrame, out_noext: str,
                               props=("MW", "LogP", "TPSA", "SA_Score", "QED", "activity"),
                               guides: Optional[Dict[str, float]] = None):
    """Grid of histograms for key physchem/scoring properties."""
    guides = guides or {"MW": 500, "LogP": 5, "TPSA": 140, "SA_Score": 4.5}
    n = len(props)
    ncol = 3
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 3.0 * nrow))
    axes = np.atleast_1d(axes).ravel()
    for k, p in enumerate(props):
        ax = axes[k]
        ax.hist(df[p].dropna(), bins=30, color=CB["blue"], alpha=0.8)
        if p in guides:
            ax.axvline(guides[p], color=CB["orange"], ls="--", lw=1.5,
                       label=f"guide {guides[p]:g}")
            ax.legend(fontsize=7)
        ax.set_title(p, fontsize=10, fontweight="bold")
        ax.grid(alpha=0.2)
    for k in range(n, len(axes)):
        axes[k].axis("off")
    fig.suptitle("Property distributions across generated library",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return _save(fig, out_noext)


# ------------------------------------------------------------------- novelty bar
def fig_novelty(top: pd.DataFrame, out_noext: str, novelty_max: float = 0.4,
                id_col: str = "design_id"):
    """Nearest-known Tanimoto per top design vs the novelty threshold."""
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    x = np.arange(len(top))
    vals = top["nn_known_tanimoto"].values
    ax.bar(x, vals, color=CB["green"], alpha=0.85)
    ax.axhline(novelty_max, color=CB["orange"], ls="--", lw=1.6,
               label=f"novelty threshold ({novelty_max})")
    ax.set_xticks(x)
    ax.set_xticklabels(top[id_col].values, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Max Tanimoto to any known active")
    ax.set_ylim(0, max(0.5, float(vals.max()) * 1.25))
    for xi, v in zip(x, vals):
        ax.text(xi, v + 0.01, f"{v:.2f}", ha="center", va="bottom", fontsize=7)
    ax.set_title("Novelty of selected designs (lower = more novel)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.2, axis="y")
    return _save(fig, out_noext)


# ------------------------------------------------------------ molecule grid image
def fig_molecule_grid(df: pd.DataFrame, out_png: str, id_col: str = "design_id",
                      legend_fields=("design_id", "activity", "QED", "SA_Score"),
                      mols_per_row: int = 4, sub=(340, 300)):
    """Structure grid with single-line legends. Uses rdCoordGen for clean layout.

    Writes PNG (out_png) and a sibling SVG. Returns the PNG path."""
    mols, legends = [], []
    for _, r in df.iterrows():
        m = Chem.MolFromSmiles(r["smiles"])
        if m is None:
            continue
        rdCoordGen.AddCoords(m)
        parts = []
        for f in legend_fields:
            v = r.get(f, "")
            if isinstance(v, float):
                parts.append(f"{f.split('_')[0]} {v:.2f}")
            else:
                parts.append(str(v))
        legends.append("  ".join(parts))  # SINGLE line only
        mols.append(m)

    n = len(mols)
    nrow = int(np.ceil(n / mols_per_row))
    w, h = sub[0] * mols_per_row, sub[1] * nrow
    # PNG
    d2d = rdMolDraw2D.MolDraw2DCairo(w, h, sub[0], sub[1])
    opts = d2d.drawOptions()
    opts.legendFontSize = 16
    opts.padding = 0.13
    opts.bondLineWidth = 2
    d2d.DrawMolecules(mols, legends=legends)
    d2d.FinishDrawing()
    with open(out_png, "wb") as f:
        f.write(d2d.GetDrawingText())
    # SVG (editable text)
    svg_path = os.path.splitext(out_png)[0] + ".svg"
    s2d = rdMolDraw2D.MolDraw2DSVG(w, h, sub[0], sub[1])
    so = s2d.drawOptions()
    so.legendFontSize = 16
    so.padding = 0.13
    so.bondLineWidth = 2
    s2d.DrawMolecules(mols, legends=legends)
    s2d.FinishDrawing()
    with open(svg_path, "w") as f:
        f.write(s2d.GetDrawingText())
    return out_png


# ------------------------------------------------------------------- route image
def fig_route(route_json_path: str, out_png: str) -> bool:
    """Render an AiZynthFinder best-route tree (delegates to run_retro.render_route)."""
    from run_retro import render_route
    return render_route(route_json_path, out_png)
