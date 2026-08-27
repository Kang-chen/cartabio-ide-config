"""Publication-quality figures for a pharmacovigilance signal-detection run.

All figures are drug-agnostic and driven by the annotated results DataFrame
produced by :mod:`compute_disproportionality` + :mod:`annotate_signals`.

Figures produced (each saved as both PNG and SVG, editable text):

  1. ``fig1_top_signals_bar``   - ranked horizontal bar chart of the strongest
     genuine-ADR signals for the primary subject (pooled / class / drug), with
     labeled-vs-unlabeled colour coding.
  2. ``fig2_volcano``           - volcano plot (log2 ROR vs -log10 p), with the
     top signals marked by NUMBERED DIAMONDS and a side key (no leader lines,
     which was the design that resolved label-overlap in validation).
  3. ``fig3_forest``            - forest plot of top signals with 95% CIs.
  4. ``fig4_soc_heatmap``       - cross-drug heatmap of log-ROR by event x drug,
     diverging RdBu_r around ROR=1, grey NaN cells, asterisks on ROR<1
     (under-reported) cells.
  5. ``fig5_summary_panel``     - compact multi-panel data summary for the
     report's infographic (signal counts by SOC + labeled/unlabeled split).

Design constants (validated in the worked example) are module-level so a caller
can override them. Uses the Agg backend and keeps SVG text as text.
"""

from __future__ import annotations

import textwrap
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

# ---- global style -------------------------------------------------------- #
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"          # keep SVG text editable
matplotlib.rcParams["figure.dpi"] = 120
matplotlib.rcParams["axes.spines.top"] = False
matplotlib.rcParams["axes.spines.right"] = False

# Okabe-Ito colourblind-safe palette
CB = {
    "orange": "#E69F00", "skyblue": "#56B4E9", "green": "#009E73",
    "blue": "#0072B2", "vermillion": "#D55E00", "purple": "#CC79A7",
    "yellow": "#F0E442", "grey": "#999999",
}
LABELED_COLOR = CB["blue"]        # events already in the label
UNLABELED_COLOR = CB["vermillion"]  # potentially novel
BOXED_COLOR = "#7A0177"           # boxed-warning events
NAN_COLOR = "#DDDAD3"             # missing heatmap cells


def _save(fig, out_prefix: str) -> Dict[str, str]:
    """Save a figure to ``<out_prefix>.png`` and ``.svg``; return the paths."""
    paths = {}
    for ext in ("png", "svg"):
        p = f"{out_prefix}.{ext}"
        fig.savefig(p, bbox_inches="tight", facecolor="white")
        paths[ext] = p
    plt.close(fig)
    return paths


def _status_color(status: str) -> str:
    return {"boxed": BOXED_COLOR, "labeled": LABELED_COLOR,
            "unlabeled": UNLABELED_COLOR}.get(status, CB["grey"])


def _display(term: str) -> str:
    """Clean a MedDRA term for display.

    FAERS apostrophe handling can store possessives as ``^`` or ``^S`` (the
    apostrophe-variant workaround in query_faers). Restore readable apostrophes
    before title-casing, e.g. ``STILL^S DISEASE`` -> ``Still's Disease``.
    """
    s = str(term)
    s = s.replace("^S", "'S").replace("^s", "'s").replace("^", "'")
    s = s.replace("\u2019", "'")
    return s.title().replace("'S", "'s")


def _clean_events(res: pd.DataFrame, subject: str, drug_col: str,
                  drop_noise: bool = True) -> pd.DataFrame:
    sub = res[res[drug_col] == subject].copy()
    if drop_noise and "is_noise" in sub.columns:
        sub = sub[~sub["is_noise"]]
    return sub


# --------------------------------------------------------------------------- #
# 1. ranked bar chart of top signals
# --------------------------------------------------------------------------- #
def fig_top_signals_bar(res: pd.DataFrame, subject: str, out_prefix: str,
                        drug_col: str = "drug", event_col: str = "event",
                        top_n: int = 20, drop_noise: bool = True) -> Dict[str, str]:
    sub = _clean_events(res, subject, drug_col, drop_noise)
    sub = sub[sub.get("signal", True)].sort_values("ror", ascending=False).head(top_n)
    sub = sub.iloc[::-1]  # largest at top
    if sub.empty:
        return {}
    colors = [_status_color(s) for s in sub.get("label_status", ["unknown"] * len(sub))]
    low_conf = (sub["low_confidence"].fillna(False).astype(bool).values
                if "low_confidence" in sub.columns else np.zeros(len(sub), bool))
    fig, ax = plt.subplots(figsize=(9, max(4, 0.42 * len(sub) + 1)))
    y = np.arange(len(sub))
    bars = ax.barh(y, sub["ror"], color=colors, edgecolor="white", height=0.72)
    # mark low-confidence bars with a hatch + dark edge so they read as flagged
    for bar, lc in zip(bars, low_conf):
        if lc:
            bar.set_hatch("///")
            bar.set_edgecolor("#222222")
            bar.set_linewidth(0.8)
    if "ror_lower" in sub and "ror_upper" in sub:
        ax.errorbar(sub["ror"], y,
                    xerr=[sub["ror"] - sub["ror_lower"], sub["ror_upper"] - sub["ror"]],
                    fmt="none", ecolor="#444444", elinewidth=0.8, capsize=2)
    ax.set_yticks(y)
    ax.set_yticklabels([textwrap.fill(_display(t), 34) for t in sub[event_col]],
                       fontsize=8)
    ax.axvline(1.0, color="#888888", ls="--", lw=0.8)
    ax.set_xlabel("Reporting Odds Ratio (95% CI)", fontsize=10)
    ax.set_title(f"Top {len(sub)} disproportionality signals: {subject}",
                 fontsize=12, weight="bold")
    handles = [plt.Rectangle((0, 0), 1, 1, color=BOXED_COLOR),
               plt.Rectangle((0, 0), 1, 1, color=LABELED_COLOR),
               plt.Rectangle((0, 0), 1, 1, color=UNLABELED_COLOR)]
    labels = ["Boxed warning", "Labeled", "Unlabeled"]
    if low_conf.any():
        handles.append(plt.Rectangle((0, 0), 1, 1, facecolor="white",
                                     edgecolor="#222222", hatch="///"))
        labels.append("Low-confidence")
    ax.legend(handles, labels, fontsize=8, loc="lower right", frameon=False)
    return _save(fig, out_prefix)


# --------------------------------------------------------------------------- #
# 2. volcano plot with numbered diamonds + side key
# --------------------------------------------------------------------------- #
def fig_volcano(res: pd.DataFrame, subject: str, out_prefix: str,
                drug_col: str = "drug", event_col: str = "event",
                top_n_label: int = 12, y_cap: float = 60.0,
                drop_noise: bool = True) -> Dict[str, str]:
    sub = _clean_events(res, subject, drug_col, drop_noise)
    # y-axis = BH-FDR q-value (the multiplicity-corrected significance used for
    # the signal rule); fall back to the raw chi-square p-value only if an FDR
    # column is unavailable (use_fdr=False / statsmodels missing).
    ycol = "fdr" if ("fdr" in sub.columns and sub["fdr"].notna().any()) else "p_value"
    sub = sub[(sub["ror"] > 0) & sub[ycol].notna()].copy()
    if sub.empty:
        return {}
    sub["log2ror"] = np.log2(sub["ror"])
    # -log10 q (or p) with a floor for zeros (huge FAERS counts drive q/p to 0)
    ymin = sub.loc[sub[ycol] > 0, ycol].min() if (sub[ycol] > 0).any() else 1e-300
    sub["nlp"] = -np.log10(sub[ycol].clip(lower=ymin * 1e-3))
    sub["nlp_disp"] = sub["nlp"].clip(upper=y_cap)
    _ylabel = ("-log10 FDR q-value (capped)" if ycol == "fdr"
               else "-log10 p-value (capped)")

    sig = sub.get("signal", pd.Series(False, index=sub.index)).fillna(False)
    low_conf = (sub["low_confidence"].fillna(False).astype(bool)
                if "low_confidence" in sub.columns else pd.Series(False, index=sub.index))
    fig, ax = plt.subplots(figsize=(9, 6.5))
    ax.scatter(sub.loc[~sig, "log2ror"], sub.loc[~sig, "nlp_disp"], s=14,
               c="#BBBBBB", alpha=0.6, edgecolors="none", label="Not a signal")
    ax.scatter(sub.loc[sig, "log2ror"], sub.loc[sig, "nlp_disp"], s=26,
               c=CB["vermillion"], alpha=0.8, edgecolors="none", label="Signal")
    # ring low-confidence signals (e.g. extreme-ROR outliers) so they stand out
    lc_sig = sig & low_conf
    if lc_sig.any():
        ax.scatter(sub.loc[lc_sig, "log2ror"], sub.loc[lc_sig, "nlp_disp"],
                   s=120, facecolors="none", edgecolors="#222222",
                   linewidths=1.4, label="Low-confidence")
    ax.axvline(0, color="#888888", ls="--", lw=0.8)
    ax.axvline(1, color="#CCCCCC", ls=":", lw=0.8)  # ROR=2

    # number the strongest signals and list them in a side key (no leaders)
    # Number the strongest signals and list them in a side key (no leader
    # lines). Because huge FAERS counts push many top signals to the same
    # capped -log10 p, their true positions collide; we place the numbered
    # diamonds on an evenly spaced vertical LADDER at the right of the plot
    # (ordered by ROR) so every number stays legible, with a faint connector
    # to each point's true position.
    top = sub[sig].sort_values("ror", ascending=False).head(top_n_label).reset_index()
    n_top = len(top)
    x_ladder = None
    if n_top:
        x_ladder = float(sub["log2ror"].max()) * 1.02 + 0.4
        y_hi, y_lo = y_cap * 0.98, y_cap * 0.28
        ladder_y = np.linspace(y_hi, y_lo, n_top) if n_top > 1 else [y_hi]
        for i, row in top.iterrows():
            ly = ladder_y[i]
            ax.plot([row["log2ror"], x_ladder], [row["nlp_disp"], ly],
                    color="#CCCCCC", lw=0.5, zorder=3)
            ax.scatter(row["log2ror"], row["nlp_disp"], marker="o", s=18,
                       color=CB["vermillion"], edgecolors="black",
                       linewidths=0.4, zorder=4)
            ax.scatter(x_ladder, ly, marker="D", s=95, facecolor="white",
                       edgecolor="black", linewidths=1.1, zorder=5)
            ax.annotate(str(i + 1), (x_ladder, ly), fontsize=7,
                        ha="center", va="center", zorder=6)
    key_lines = [f"{i+1}. {_display(r[event_col])[:36]} (ROR {r['ror']:.1f})"
                 for i, r in top.iterrows()]
    ax.text(1.03, 1.0, "\n".join(key_lines), transform=ax.transAxes,
            fontsize=7.5, va="top", ha="left",
            bbox=dict(boxstyle="round", fc="#FAF9F3", ec="#D5CFC5"))

    ax.set_xlabel("log2 Reporting Odds Ratio", fontsize=10)
    ax.set_ylabel(_ylabel, fontsize=10)
    if x_ladder is not None:
        ax.set_xlim(right=x_ladder + 0.7)
    ax.set_ylim(0, y_cap + 6)
    ax.set_title(f"Disproportionality volcano: {subject}", fontsize=12, weight="bold")
    ax.legend(fontsize=8, loc="lower left", frameon=False)
    return _save(fig, out_prefix)


# --------------------------------------------------------------------------- #
# 3. forest plot
# --------------------------------------------------------------------------- #
def fig_forest(res: pd.DataFrame, subject: str, out_prefix: str,
               drug_col: str = "drug", event_col: str = "event",
               top_n: int = 15, drop_noise: bool = True) -> Dict[str, str]:
    sub = _clean_events(res, subject, drug_col, drop_noise)
    sub = sub[sub.get("signal", True) & sub["ror_lower"].notna()]
    sub = sub.sort_values("ror", ascending=False).head(top_n).iloc[::-1]
    if sub.empty:
        return {}
    low_conf = (sub["low_confidence"].fillna(False).astype(bool).values
                if "low_confidence" in sub.columns else np.zeros(len(sub), bool))
    fig, ax = plt.subplots(figsize=(8, max(4, 0.45 * len(sub) + 1)))
    y = np.arange(len(sub))
    # robust points in blue; low-confidence points in vermillion with a ring
    for grp_mask, col, mk, lbl in (
            (~low_conf, CB["blue"], "o", "Robust signal"),
            (low_conf, CB["vermillion"], "D", "Low-confidence")):
        if grp_mask.any():
            yy = y[grp_mask]
            ss = sub.iloc[np.where(grp_mask)[0]]
            ror = ss["ror"].to_numpy(dtype=float)
            xerr = np.vstack([(ss["ror"] - ss["ror_lower"]).to_numpy(dtype=float),
                              (ss["ror_upper"] - ss["ror"]).to_numpy(dtype=float)])
            ax.errorbar(ror, yy, xerr=xerr,
                        fmt=mk, color=col, ecolor="#444444", elinewidth=1,
                        capsize=3, markersize=6 if mk == "D" else 5,
                        markeredgecolor="#222222" if mk == "D" else col,
                        markeredgewidth=0.8, label=lbl)
    ax.axvline(1.0, color="#888888", ls="--", lw=0.8)
    ax.set_xscale("log")
    ax.set_yticks(y)
    ylabels = [textwrap.fill(_display(t), 34) + ("  \u26a0" if lc else "")
               for t, lc in zip(sub[event_col], low_conf)]
    ax.set_yticklabels(ylabels, fontsize=8)
    ax.set_xlabel("Reporting Odds Ratio (log scale, 95% CI)", fontsize=10)
    ax.set_title(f"Forest plot of top signals: {subject}", fontsize=12, weight="bold")
    if low_conf.any():
        ax.legend(fontsize=8, loc="lower right", frameon=False)
    return _save(fig, out_prefix)


# --------------------------------------------------------------------------- #
# 4. cross-drug SOC heatmap
# --------------------------------------------------------------------------- #
def fig_soc_heatmap(res: pd.DataFrame, out_prefix: str,
                    events: Optional[Sequence[str]] = None,
                    drugs: Optional[Sequence[str]] = None,
                    drug_col: str = "drug", event_col: str = "event",
                    top_n_events: int = 20, drop_noise: bool = True,
                    sort_by_soc: bool = True) -> Dict[str, str]:
    """Heatmap of log10(ROR) for event x drug, diverging around ROR=1."""
    df = res.copy()
    if drop_noise and "is_noise" in df.columns:
        df = df[~df["is_noise"]]
    if drugs is None:
        drugs = list(pd.unique(df[drug_col]))
    if events is None:
        # Choose events that are (a) a signal in at least one drug and (b) have
        # good cross-drug coverage, so the heatmap is informative rather than
        # mostly empty. Rank by coverage (# drugs with a value), breaking ties
        # by max ROR.
        drugset = set(drugs)
        sig_events = set(df[df.get("signal", True)][event_col])
        cand = df[df[event_col].isin(sig_events) & df[drug_col].isin(drugset)]
        cov = (cand[cand["ror"].notna()]
               .groupby(event_col)[drug_col].nunique().rename("coverage"))
        maxror = cand.groupby(event_col)["ror"].max().rename("maxror")
        ranking = pd.concat([cov, maxror], axis=1).fillna(0)
        ranking = ranking.sort_values(["coverage", "maxror"],
                                      ascending=[False, False])
        events = list(ranking.head(top_n_events).index)
    if sort_by_soc and "soc" in df.columns:
        soc_of = df.drop_duplicates(event_col).set_index(event_col)["soc"].to_dict()
        events = sorted(events, key=lambda e: (soc_of.get(e, "zzz"), e))

    drugs = list(drugs)
    mat = np.full((len(events), len(drugs)), np.nan)
    for i, ev in enumerate(events):
        for j, dr in enumerate(drugs):
            row = df[(df[event_col] == ev) & (df[drug_col] == dr)]
            if len(row) and pd.notna(row["ror"].iloc[0]) and row["ror"].iloc[0] > 0:
                mat[i, j] = np.log10(row["ror"].iloc[0])

    # drop drug columns / event rows that are entirely missing (no data)
    keep_cols = [j for j in range(len(drugs)) if np.isfinite(mat[:, j]).any()]
    if keep_cols and len(keep_cols) < len(drugs):
        mat = mat[:, keep_cols]
        drugs = [drugs[j] for j in keep_cols]
    keep_rows = [i for i in range(len(events)) if np.isfinite(mat[i, :]).any()]
    if keep_rows and len(keep_rows) < len(events):
        mat = mat[keep_rows, :]
        events = [events[i] for i in keep_rows]

    has_under = bool((mat < 0).any())
    vmax = np.nanmax(np.abs(mat)) if np.isfinite(mat).any() else 1.0
    vmax = max(vmax, 0.1)
    norm = mcolors.TwoSlopeNorm(vmin=-vmax, vcenter=0.0, vmax=vmax)
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad(NAN_COLOR)

    fig, ax = plt.subplots(figsize=(max(6, 1.1 * len(drugs) + 3),
                                    max(5, 0.42 * len(events) + 1.5)))
    im = ax.imshow(np.ma.masked_invalid(mat), aspect="auto", cmap=cmap, norm=norm)
    ax.set_xticks(np.arange(len(drugs)))
    ax.set_xticklabels([str(d) for d in drugs], rotation=40, ha="right", fontsize=8)
    ax.set_yticks(np.arange(len(events)))
    ax.set_yticklabels([textwrap.fill(_display(e), 26) for e in events], fontsize=7.5)
    # annotate cells: value + asterisk for ROR<1 (under-reported)
    for i in range(len(events)):
        for j in range(len(drugs)):
            v = mat[i, j]
            if np.isnan(v):
                continue
            txt = f"{10 ** v:.1f}"
            if v < 0:
                txt += "*"
            tc = "white" if abs(v) / vmax > 0.55 else "#222222"
            ax.text(j, i, txt, ha="center", va="center", fontsize=6.5, color=tc)
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("log10 ROR  (0 = ROR 1)", fontsize=8)
    title = "Cross-drug reporting pattern (values = ROR"
    title += "; * = under-reported, ROR<1)" if has_under else ")"
    ax.set_title(title, fontsize=11, weight="bold")
    return _save(fig, out_prefix)


# --------------------------------------------------------------------------- #
# 5. compact summary panel (for the infographic data half)
# --------------------------------------------------------------------------- #
def fig_summary_panel(res: pd.DataFrame, subject: str, out_prefix: str,
                      drug_col: str = "drug", event_col: str = "event",
                      drop_noise: bool = True) -> Dict[str, str]:
    sub = _clean_events(res, subject, drug_col, drop_noise)
    sig = sub[sub.get("signal", True)]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))

    # panel A: signals by SOC
    if "soc" in sig.columns and len(sig):
        by_soc = sig["soc"].value_counts().head(8).iloc[::-1]
        axes[0].barh(range(len(by_soc)), by_soc.values, color=CB["blue"])
        axes[0].set_yticks(range(len(by_soc)))
        axes[0].set_yticklabels([textwrap.fill(s, 28) for s in by_soc.index], fontsize=8)
        axes[0].set_xlabel("# signals", fontsize=9)
        axes[0].set_title("Signals by System Organ Class", fontsize=10, weight="bold")
    else:
        axes[0].axis("off")

    # panel B: labeled vs unlabeled split
    if "label_status" in sig.columns and len(sig):
        counts = sig["label_status"].value_counts()
        order = [s for s in ["boxed", "labeled", "unlabeled", "unknown"] if s in counts]
        vals = [counts[s] for s in order]
        cols = [_status_color(s) for s in order]
        axes[1].pie(vals, labels=[s.title() for s in order], colors=cols,
                    autopct="%1.0f%%", textprops={"fontsize": 8})
        axes[1].set_title("Signals by label status", fontsize=10, weight="bold")
    else:
        axes[1].axis("off")

    fig.suptitle(f"Signal summary: {subject}", fontsize=12, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return _save(fig, out_prefix)


# --------------------------------------------------------------------------- #
# convenience: generate the full standard set
# --------------------------------------------------------------------------- #
def generate_all_figures(res: pd.DataFrame, subject: str, out_dir: str,
                         drugs: Optional[Sequence[str]] = None,
                         drug_col: str = "drug", event_col: str = "event"
                         ) -> Dict[str, Dict[str, str]]:
    """Generate the standard figure set; returns {figure_name: {ext: path}}."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    figs: Dict[str, Dict[str, str]] = {}
    figs["bar"] = fig_top_signals_bar(res, subject, f"{out_dir}/fig1_top_signals_bar",
                                      drug_col, event_col)
    figs["volcano"] = fig_volcano(res, subject, f"{out_dir}/fig2_volcano",
                                  drug_col, event_col)
    figs["forest"] = fig_forest(res, subject, f"{out_dir}/fig3_forest",
                                drug_col, event_col)
    figs["heatmap"] = fig_soc_heatmap(res, f"{out_dir}/fig4_soc_heatmap",
                                      drugs=drugs, drug_col=drug_col,
                                      event_col=event_col)
    figs["summary"] = fig_summary_panel(res, subject, f"{out_dir}/fig5_summary_panel",
                                        drug_col, event_col)
    return {k: v for k, v in figs.items() if v}
