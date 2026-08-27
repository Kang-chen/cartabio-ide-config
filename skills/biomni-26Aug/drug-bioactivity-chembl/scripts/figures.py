#!/usr/bin/env python3
"""
figures.py  --  Compound-agnostic, data-driven figures for the
`drug-bioactivity-chembl` skill. All five builders take the curated
DataFrames / aggregation tables from chembl_potency.py and save PNG+SVG.

Each builder returns the PNG path so the caller can run the mandatory
`Read(mode="media_output_check")` on it and regenerate if blank/clipped.

Style: colorblind-safe Okabe-Ito, Liberation Sans, editable SVG text.
Nothing here references a specific compound or target family.
"""
from __future__ import annotations
import os
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["axes.spines.top"] = False
matplotlib.rcParams["axes.spines.right"] = False

# Okabe-Ito palette, mapped to roles.
C = {
    "primary": "#0072B2", "offtarget": "#D55E00", "family2": "#CC79A7",
    "other": "#999999", "pos": "#009E73", "neg": "#E69F00", "grey": "#666666",
}


def _save(fig, path_png: str) -> str:
    os.makedirs(os.path.dirname(path_png), exist_ok=True)
    fig.savefig(path_png, dpi=200, bbox_inches="tight")
    fig.savefig(path_png.replace(".png", ".svg"), bbox_inches="tight")
    plt.close(fig)
    return path_png


def fig_potency_landscape(clean: pd.DataFrame, out: str,
                          use_type: str = "IC50",
                          label_col: str = "target_pref_name",
                          compound: str = "compound") -> str:
    """Strip plot of every biochemical measurement per target, ordered by median,
    primary targets highlighted. The 'where does it hit hardest' overview."""
    d = clean[clean["sd_type"] == use_type].copy()
    if d.empty:
        return _blank(out, f"No {use_type} data")
    order = (d.groupby(label_col)["standard_value"].median().sort_values().index.tolist())
    d[label_col] = pd.Categorical(d[label_col], categories=order, ordered=True)
    d = d.sort_values(label_col)
    ypos = {lab: i for i, lab in enumerate(order)}
    fig, ax = plt.subplots(figsize=(7.2, max(3.0, 0.42 * len(order) + 1.2)))
    for lab in order:
        sub = d[d[label_col] == lab]
        y = ypos[lab] + np.random.RandomState(0).uniform(-0.13, 0.13, len(sub))
        color = C["primary"] if sub["is_primary_target"].any() else C["offtarget"]
        ax.scatter(sub["standard_value"], y, s=22, alpha=0.6, color=color,
                   edgecolor="white", linewidth=0.3, zorder=3)
        med = sub["standard_value"].median()
        ax.plot([med, med], [ypos[lab] - 0.28, ypos[lab] + 0.28], color="black",
                lw=2, zorder=4)
    ax.set_xscale("log")
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(order, fontsize=8)
    ax.set_xlabel(f"{use_type} (nM, log scale)")
    ax.set_title(f"Potency landscape of {compound} across targets", fontsize=11)
    from matplotlib.lines import Line2D
    ax.legend(handles=[Line2D([0], [0], marker="o", color="w", label="primary target",
                              markerfacecolor=C["primary"], markersize=7),
                       Line2D([0], [0], marker="o", color="w", label="off-target",
                              markerfacecolor=C["offtarget"], markersize=7),
                       Line2D([0], [0], color="black", lw=2, label="median")],
              loc="lower right", fontsize=7, frameon=False)
    ax.grid(axis="x", alpha=0.25)
    return _save(fig, out)


def fig_median_range_forest(agg: pd.DataFrame, out: str, use_type: str = "IC50",
                            label_col: str = "target_pref_name",
                            primary_labels: Optional[list] = None,
                            compound: str = "compound",
                            top_n: Optional[int] = 14) -> str:
    """Forest/dot plot: median (diamond) + IQR (thick bar) + min-max (thin line)
    per target. The core 'median + range' visual the user asked for.

    For a legible report figure, at most `top_n` most-potent targets are shown
    (primary targets are always kept). Set top_n=None to show every target."""
    d = agg[agg["sd_type"] == use_type].copy().sort_values("median", ascending=False)
    if d.empty:
        return _blank(out, f"No {use_type} data")
    if top_n and len(d) > top_n:
        keep = d.sort_values("median").head(top_n)  # most potent
        if primary_labels:                          # always include primaries
            prim = d[d[label_col].isin(primary_labels)]
            keep = pd.concat([keep, prim]).drop_duplicates(subset=[label_col])
        d = keep.sort_values("median", ascending=False)
    y = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(8.6, max(3.2, 0.5 * len(d) + 1.6)))
    for i, (_, r) in enumerate(d.iterrows()):
        is_prim = primary_labels and r[label_col] in primary_labels
        col = C["primary"] if is_prim else C["offtarget"]
        if r["vmax"] > r["vmin"]:
            ax.plot([r["vmin"], r["vmax"]], [y[i], y[i]], color=col, lw=1, alpha=0.5, zorder=2)
        ax.plot([r["q25"], r["q75"]], [y[i], y[i]], color=col, lw=5, alpha=0.85, zorder=3)
        ax.scatter([r["median"]], [y[i]], marker="D", s=42, color=col,
                   edgecolor="black", linewidth=0.5, zorder=4)
    ax.set_xscale("log")
    ax.set_yticks(y)
    ax.set_yticklabels([_wrap_label(f"{r[label_col]} (n={int(r['n'])})", width=42)
                        for _, r in d.iterrows()], fontsize=9.5)
    ax.tick_params(axis="x", labelsize=9.5)
    ax.set_xlabel(f"{use_type} (nM, log scale)", fontsize=10.5)
    ax.set_title(f"{compound}: median {use_type} with IQR and range per target", fontsize=12)
    ax.grid(axis="x", alpha=0.25)
    # Legend: only show the entries that are actually present in the plot.
    from matplotlib.lines import Line2D
    handles = []
    has_primary = bool(primary_labels) and d[label_col].isin(primary_labels).any()
    if has_primary:
        handles.append(Line2D([0], [0], marker="D", color="none", label="Primary target",
                              markerfacecolor=C["primary"], markeredgecolor="black", markersize=8))
    handles.append(Line2D([0], [0], marker="D", color="none", label="Off-target",
                          markerfacecolor=C["offtarget"], markeredgecolor="black", markersize=8))
    handles.append(Line2D([0], [0], color=C["grey"], lw=5, alpha=0.85, label="IQR (Q25-Q75)"))
    handles.append(Line2D([0], [0], color=C["grey"], lw=1, alpha=0.5, label="min-max range"))
    # Place legend below the axes (outside the data area) so it never overlaps points.
    ax.legend(handles=handles, fontsize=8.5, loc="upper center",
              bbox_to_anchor=(0.5, -0.11), ncol=len(handles), framealpha=0.9,
              handletextpad=0.5, columnspacing=1.4)
    fig.subplots_adjust(bottom=0.18)
    return _save(fig, out)


def fig_selectivity(sel: pd.DataFrame, out: str, fold_col: Optional[str] = None,
                    label_col: str = "target_pref_name",
                    compound: str = "compound", ref_line: float = 30.0,
                    min_n: int = 2, top_n: Optional[int] = 20,
                    max_label_len: int = 48, drop_uninformative: bool = True) -> str:
    """
    Horizontal bar chart of fold-selectivity vs the primary target (log).
    For a clean HEADLINE figure, off-targets with n < `min_n` (default 2) are
    excluded (single points give unstable, sometimes <1x folds), and at most
    `top_n` off-targets are shown. Set min_n=1 / top_n=None to show everything.
    """
    if sel.empty:
        return _blank(out, "No selectivity data")
    if fold_col is None:
        fold_col = [c for c in sel.columns if c.startswith("fold_vs_")][0]
    d = sel[~sel["is_reference"]].copy()
    if drop_uninformative:
        # ChEMBL contains non-descriptive target_pref_name values (e.g. "Unchecked",
        # "Molecular identity unknown", "Unnamed protein"). These add noise to a
        # headline selectivity figure; they remain in the underlying data tables.
        _bad = r"^\s*(?:unchecked|unnamed|no relevant target|molecular identity unknown|unknown)\s*$"
        d = d[~d[label_col].astype(str).str.contains(_bad, case=False, regex=True)]
    if min_n and "n" in d.columns:
        d = d[d["n"] >= min_n]
    d = d[d[fold_col] >= 1].sort_values(fold_col)  # true off-targets (weaker than primary)
    if top_n:
        d = d.tail(top_n)
    if d.empty:
        return _blank(out, "No off-targets meet the display filter")
    disp = d[label_col].astype(str).apply(lambda s: _wrap_label(s, width=max_label_len))
    y = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(9.2, max(3.0, 0.46 * len(d) + 1.3)))
    ax.barh(y, d[fold_col], color=C["offtarget"], alpha=0.85, zorder=3)
    for i, (_, r) in enumerate(d.iterrows()):
        fv = r[fold_col]
        lbl = f"{fv:.1f}\u00d7" if fv < 10 else f"{fv:.0f}\u00d7"
        ax.text(fv * 1.05, y[i], lbl, va="center", fontsize=8.5)
    ax.axvline(ref_line, ls=":", color=C["grey"], lw=1)
    ax.set_xscale("log")
    ax.set_yticks(y)
    ax.set_yticklabels(disp, fontsize=8.5)
    ax.set_xlabel(f"Fold-selectivity vs {fold_col.replace('fold_vs_', '')} (log)", fontsize=9.5)
    ax.set_title(f"{compound}: off-target selectivity window (n\u2265{min_n})", fontsize=11)
    fig.tight_layout()
    return _save(fig, out)


def fig_data_composition(df: pd.DataFrame, clean: pd.DataFrame, out: str,
                         compound: str = "compound") -> str:
    """2x2 panel: records by measurement type, by assay_type, by tier, by year."""
    fig, axes = plt.subplots(2, 2, figsize=(8.4, 6.4))
    # (a) biochemical records by measurement type
    ax = axes[0, 0]
    vc = clean["sd_type"].value_counts()
    ax.bar(vc.index, vc.values, color=C["primary"])
    ax.set_title("(a) Aggregated records by type", fontsize=9.5)
    ax.set_ylabel("records")
    # (b) all records by ChEMBL assay_type
    ax = axes[0, 1]
    vc = df["assay_type"].fillna("NA").value_counts()
    ax.bar(vc.index, vc.values, color=C["offtarget"])
    ax.set_title("(b) All records by assay_type", fontsize=9.5)
    # (c) protein records by tier
    ax = axes[1, 0]
    vc = df["tier"].fillna("NA").value_counts()
    ax.bar(vc.index, vc.values, color=C["family2"])
    ax.set_title("(c) Records by target tier", fontsize=9.5)
    ax.set_ylabel("records")
    for lab in ax.get_xticklabels():
        lab.set_rotation(20)
    # (d) measurements by publication year
    ax = axes[1, 1]
    yrs = clean["document_year"].dropna().astype(int)
    if len(yrs):
        ax.hist(yrs, bins=range(int(yrs.min()), int(yrs.max()) + 2), color=C["pos"])
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.set_title("(d) Aggregated measurements by year", fontsize=9.5)
    fig.suptitle(f"ChEMBL data composition for {compound}", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    return _save(fig, out)


def fig_cellular(cell_df: pd.DataFrame, out: str, group_col: str = "hr_status",
                 value_col: str = "value_uM", compound: str = "compound") -> str:
    """Cellular antiproliferation panel (secondary). group_col is any categorical
    grouping the caller builds (e.g. BRCA/HR status). Skips gracefully if empty."""
    if cell_df is None or cell_df.empty or value_col not in cell_df.columns:
        return _blank(out, "No cellular activity data")
    groups = [g for g in cell_df[group_col].dropna().unique()]
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    data = [cell_df[cell_df[group_col] == g][value_col].dropna().values for g in groups]
    bp = ax.boxplot(data, vert=False, labels=groups, patch_artist=True, widths=0.6)
    for patch, col in zip(bp["boxes"], [C["pos"], C["neg"], C["grey"]] * 5):
        patch.set_facecolor(col)
        patch.set_alpha(0.7)
    ax.set_xscale("log")
    ax.set_xlabel(f"Antiproliferation IC50 (\u00b5M, log)")
    ax.set_title(f"{compound}: cellular activity by group (secondary)", fontsize=11)
    fig.tight_layout()
    return _save(fig, out)


def _wrap_label(s: str, width: int = 26, max_lines: int = 2) -> str:
    """Wrap a long target name onto <=max_lines lines instead of truncating,
    so full names stay visible. Only the final line is ellipsised if it still
    overflows (rare for ChEMBL target names)."""
    import textwrap
    s = str(s)
    lines = textwrap.wrap(s, width=width, break_long_words=False,
                          break_on_hyphens=False)
    if not lines:
        return s
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if len(lines[-1]) > width - 1:
            lines[-1] = lines[-1][: width - 1] + "\u2026"
        else:
            lines[-1] = lines[-1] + "\u2026"
    return "\n".join(lines)


def _blank(out: str, msg: str) -> str:
    fig, ax = plt.subplots(figsize=(6, 2))
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=11, color=C["grey"])
    ax.axis("off")
    return _save(fig, out)


import matplotlib.ticker  # noqa: E402  (used in fig_data_composition)
