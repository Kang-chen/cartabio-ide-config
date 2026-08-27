#!/usr/bin/env python3
"""make_figures.py -- Python/matplotlib FALLBACK for make_figures.R.

Use this only when R + ggprism are unavailable. It is functionally parallel:
reads the SAME curated artifacts, auto-detects comparison vs topic mode, and
writes the SAME fig_*.png/.svg + fig_manifest.csv the report builder expects.

NOTHING is hardcoded -- every plotted value comes from the artifacts, so every
value traces to a source paper.

Usage:
    python3 make_figures.py --run <dir> [--out <dir>] [--title-prefix "..."]

Comparison mode artifacts: comparison_matrix.csv, performance_claims.json,
                           benchmark_catalog.json
Topic mode artifact:       theme_table.csv
"""
import argparse
import json
import os
import re
import textwrap

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"  # keep SVG text editable
import matplotlib.pyplot as plt
import pandas as pd

OKABE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00",
         "#56B4E9", "#F0E442", "#999999", "#000000"]
SCORE_FILL = {1: "#F4C7C3", 2: "#FCE8B2", 3: "#C6E5C3"}


def pal(n):
    return [OKABE[i % len(OKABE)] for i in range(n)]


def wrap(s, width=22):
    return "\n".join(textwrap.wrap(str(s), width=width)) or ""


MANIFEST = []


def _header(ax, title, subtitle=None):
    """Title + subtitle with guaranteed clearance (avoids overlap in matplotlib)."""
    # Title sits well above the axes; subtitle just below the title, both left-aligned.
    ax.set_title(title, fontsize=12, fontweight="bold", loc="left",
                 pad=26 if subtitle else 12)
    if subtitle:
        ax.text(0.0, 1.012, subtitle, transform=ax.transAxes, fontsize=8.3,
                color="#4d4d4d", va="bottom", ha="left")


def save(fig, out_dir, stem, mode, caption):
    png = os.path.join(out_dir, stem + ".png")
    svg = os.path.join(out_dir, stem + ".svg")
    fig.savefig(png, dpi=300, bbox_inches="tight")
    try:
        fig.savefig(svg, bbox_inches="tight")
    except Exception:
        pass
    plt.close(fig)
    MANIFEST.append({"file": os.path.basename(png), "mode": mode, "caption": caption})
    print(f"  saved {os.path.basename(png)}")


def score_finding(txt):
    t = str(txt).lower()
    pos = ["superior", "best", "recommended", "robust", "well", "good", "higher tpr",
           "controls fdr", "accurate", "reproducible", "favorable", "strong",
           "outperform", "top", "most sensitive", "preferred"]
    neg = ["inflat", "liberal", "fail", "spurious", "poor", "worst", "unfavorable",
           "false positive", "not accurate", "biased", "overly", "weak", "struggle",
           "loses control", "anti-conservative"]
    hp = any(k in t for k in pos)
    hn = any(k in t for k in neg)
    if hn and not hp:
        return 1
    if hp and not hn:
        return 3
    return 2


def text_grid(df_long, xs, ys, textcol, fills, title, subtitle, caption,
              out_dir, stem, mode, cap_short, xbold=True):
    """Generic tile grid with in-cell text. df_long has columns x, y, text[, score]."""
    nx, ny = len(xs), len(ys)
    fig_w = max(6.5, 2.0 + 1.9 * nx)
    fig_h = max(4.5, 0.72 * ny + 1.8)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    xidx = {v: i for i, v in enumerate(xs)}
    yidx = {v: i for i, v in enumerate(ys)}
    for _, r in df_long.iterrows():
        xi, yi = xidx[r["x"]], yidx[r["y"]]
        fc = fills(r)
        ax.add_patch(plt.Rectangle((xi - 0.5, yi - 0.5), 1, 1,
                                   facecolor=fc, edgecolor="white", linewidth=1.4))
        ax.text(xi, yi, wrap(r["text"], 20), ha="center", va="center",
                fontsize=6.3, color="#1f1f1f", linespacing=0.9)
    ax.set_xlim(-0.5, nx - 0.5)
    ax.set_ylim(-0.5, ny - 0.5)
    ax.set_xticks(range(nx))
    ax.set_xticklabels(xs, fontweight="bold" if xbold else "normal", fontsize=9.5)
    ax.set_yticks(range(ny))
    ax.set_yticklabels([wrap(y, 26) for y in ys], fontsize=8)
    ax.invert_yaxis()
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)
    _header(ax, title, subtitle)
    if caption:
        fig.text(0.01, 0.005, caption, fontsize=6.5, color="grey", ha="left", wrap=True)
    save(fig, out_dir, stem, mode, cap_short)


def run(run_dir, out_dir, tprefix):
    os.makedirs(out_dir, exist_ok=True)
    rd = lambda f: os.path.join(run_dir, f)
    ex = lambda f: os.path.exists(rd(f))

    # ---------------- comparison: comparison_matrix.csv ----------------
    if ex("comparison_matrix.csv"):
        cm = pd.read_csv(rd("comparison_matrix.csv"))
        dim_col = cm.columns[0]
        methods = list(cm.columns[1:])
        dims = list(cm[dim_col])
        rows = []
        for _, r in cm.iterrows():
            for m in methods:
                rows.append({"x": m, "y": r[dim_col], "text": r[m]})
        dfl = pd.DataFrame(rows)
        fillmap = {m: c for m, c in zip(methods, pal(len(methods)))}

        def fills(r):
            base = fillmap[r["x"]]
            return base + "2b"  # ~17% alpha in hex8
        text_grid(dfl, methods, dims, "text", fills,
                  f"{tprefix}Method characteristics matrix",
                  "Structural / algorithmic comparison across dimensions",
                  "Descriptors transcribed from method papers and benchmark syntheses (see references).",
                  out_dir, "fig_comparison_matrix", "comparison",
                  "Structural comparison of methods across evaluation dimensions.")

    # ---------------- comparison: performance_claims.json ----------------
    pc = None
    if ex("performance_claims.json"):
        with open(rd("performance_claims.json")) as fh:
            pc = pd.DataFrame(json.load(fh))
        if len(pc) and {"method", "dimension", "finding"}.issubset(pc.columns):
            pc["score"] = pc["finding"].map(score_finding)
            methods = list(dict.fromkeys(pc["method"]))
            dims = list(dict.fromkeys(pc["dimension"]))
            dfl = pc.rename(columns={"method": "x", "dimension": "y", "finding": "text"})[
                ["x", "y", "text", "score"]]
            text_grid(dfl, methods, dims, "text",
                      lambda r: SCORE_FILL.get(int(r["score"]), "#EEEEEE"),
                      f"{tprefix}Benchmark-derived performance scorecard",
                      "Direction inferred from finding text; cell shows verbatim finding",
                      ("Ordinal colors are a transparent keyword summary of published qualitative "
                       "findings, NOT a re-run metric. Each cell traces to source + DOI in the claims table."),
                      out_dir, "fig_performance_scorecard", "comparison",
                      "Qualitative performance scorecard (direction inferred; verbatim findings shown).")

            # evidence thickness
            if "evidence_thickness" in pc.columns:
                order = ["head_to_head", "multiple_benchmarks", "single_benchmark",
                         "single_study", "anecdotal"]
                pc2 = pc.copy()
                pc2["evidence_thickness"] = pc2["evidence_thickness"].fillna("unspecified").replace("", "unspecified")
                ct = pc2.groupby(["method", "evidence_thickness"]).size().unstack(fill_value=0)
                cats = [c for c in order if c in ct.columns] + \
                       [c for c in ct.columns if c not in order]
                ct = ct[cats]
                fig, ax = plt.subplots(figsize=(max(6.5, 2 + 1.4 * len(ct)), 4.8))
                bottom = [0] * len(ct)
                colors = pal(len(cats))
                for c, col in zip(cats, colors):
                    ax.bar(ct.index, ct[c], bottom=bottom, label=c, color=col,
                           edgecolor="white", width=0.66)
                    bottom = [b + v for b, v in zip(bottom, ct[c])]
                ax.set_ylabel("Number of claims")
                ax.set_title(f"{tprefix}Evidence backing each method", fontsize=12,
                             fontweight="bold", loc="left")
                for lbl in ax.get_xticklabels():
                    lbl.set_fontweight("bold")
                ax.legend(title="Evidence thickness", fontsize=7, title_fontsize=8)
                for s in ("top", "right"):
                    ax.spines[s].set_visible(False)
                fig.text(0.01, 0.005, "Evidence thickness assigned during extraction (head_to_head strongest).",
                         fontsize=6.5, color="grey")
                save(fig, out_dir, "fig_evidence_thickness", "comparison",
                     "Count of extracted claims per method, colored by strength of supporting evidence.")

    # ---------------- comparison: benchmark_catalog.json ----------------
    if ex("benchmark_catalog.json"):
        with open(rd("benchmark_catalog.json")) as fh:
            bc = pd.DataFrame(json.load(fh))
        if len(bc) and "benchmark_name" in bc.columns:
            has_type = "benchmark_type" in bc.columns
            types = list(dict.fromkeys(bc["benchmark_type"])) if has_type else ["benchmark"]
            tcol = {t: c for t, c in zip(types, pal(len(types)))}
            fig, ax = plt.subplots(figsize=(9, max(3.5, 0.55 * len(bc) + 1.8)))
            yy = range(len(bc))
            for i, (_, r) in enumerate(bc.iloc[::-1].iterrows()):
                c = tcol[r["benchmark_type"]] if has_type else OKABE[0]
                ax.barh(i, 1, color=c, edgecolor="white", height=0.85)
                org = f"  [{r['organism']}]" if "organism" in bc.columns else ""
                ax.text(0.02, i, f"{r['benchmark_name']}{org}", ha="left", va="center",
                        fontsize=8, color="#111111")
            ax.set_xlim(0, 1)
            ax.set_yticks([])
            ax.set_xticks([])
            for s in ax.spines.values():
                s.set_visible(False)
            ax.set_title(f"{tprefix}Benchmark evidence landscape", fontsize=12,
                         fontweight="bold", loc="left")
            if has_type:
                handles = [plt.Rectangle((0, 0), 1, 1, color=tcol[t]) for t in types]
                ax.legend(handles, types, title="Benchmark type", fontsize=7,
                          title_fontsize=8, loc="lower right")
            fig.text(0.01, 0.005, "Each benchmark is a distinct source of ground truth (see catalog table).",
                     fontsize=6.5, color="grey")
            save(fig, out_dir, "fig_benchmark_catalog", "comparison",
                 "Landscape of independent benchmarks / reference datasets underpinning the comparison.")

    # ---------------- topic: theme_table.csv ----------------
    if ex("theme_table.csv"):
        tt = pd.read_csv(rd("theme_table.csv"))
        if "theme" in tt.columns and len(tt):
            if "n_papers" not in tt.columns:
                tt["n_papers"] = 1
            tt = tt.sort_values("n_papers")
            fig, ax = plt.subplots(figsize=(8.5, max(3.5, 0.5 * len(tt) + 1.8)))
            if "consensus_level" in tt.columns:
                cons_pal = {"strong": "#009E73", "moderate": "#56B4E9",
                            "weak": "#E69F00", "contested": "#D55E00"}
                colors = [cons_pal.get(c, "#999999") for c in tt["consensus_level"]]
                ax.barh(range(len(tt)), tt["n_papers"], color=colors,
                        edgecolor="white", height=0.72)
                seen = [c for c in ["strong", "moderate", "weak", "contested"]
                        if c in set(tt["consensus_level"])]
                handles = [plt.Rectangle((0, 0), 1, 1, color=cons_pal[c]) for c in seen]
                ax.legend(handles, seen, title="Consensus", fontsize=7, title_fontsize=8)
            else:
                ax.barh(range(len(tt)), tt["n_papers"], color=OKABE[0],
                        edgecolor="white", height=0.72)
            ax.set_yticks(range(len(tt)))
            ax.set_yticklabels([wrap(t, 34) for t in tt["theme"]], fontsize=8)
            ax.set_xlabel("Number of papers")
            ax.set_title(f"{tprefix}Evidence map by theme", fontsize=12,
                         fontweight="bold", loc="left")
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            save(fig, out_dir, "fig_theme_map", "topic",
                 "Evidence map: number of papers per theme (colored by consensus where available).")

            if "evidence_quality" in tt.columns:
                eq_pal = {"high": "#009E73", "moderate": "#E69F00", "low": "#D55E00"}
                order = [q for q in ["high", "moderate", "low"] if q in set(tt["evidence_quality"])]
                agg = tt.groupby("evidence_quality")["n_papers"].sum()
                fig, ax = plt.subplots(figsize=(6.5, 4.6))
                ax.bar(order, [agg.get(q, 0) for q in order],
                       color=[eq_pal[q] for q in order], edgecolor="white", width=0.6)
                ax.set_ylabel("Number of papers")
                ax.set_title(f"{tprefix}Evidence quality distribution", fontsize=12,
                             fontweight="bold", loc="left")
                for s in ("top", "right"):
                    ax.spines[s].set_visible(False)
                save(fig, out_dir, "fig_evidence_quality", "topic",
                     "Distribution of papers by assessed evidence quality.")

    if MANIFEST:
        pd.DataFrame(MANIFEST).to_csv(os.path.join(out_dir, "fig_manifest.csv"), index=False)
        print(f"\nWrote {len(MANIFEST)} figure(s) + fig_manifest.csv to {out_dir}")
    else:
        print("\nNo artifacts found to plot. Expected comparison_matrix.csv / "
              "performance_claims.json / benchmark_catalog.json (comparison) or "
              "theme_table.csv (topic).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=".")
    ap.add_argument("--out", default=None)
    ap.add_argument("--title-prefix", default="")
    a = ap.parse_args()
    run(a.run, a.out or a.run, a.title_prefix)
