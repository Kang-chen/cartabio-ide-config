#!/usr/bin/env python3
"""
make_figures.py -- Generate the standard publication figures for a de novo binder
design campaign from the pipeline's output files.

Stage 4a of the workflow (RFdiffusion -> ProteinMPNN -> Boltz-2 -> figures + report).

FIGURES PRODUCED (PNG + SVG, colorblind-friendly palette):
  figN_mpnn_scores   : ProteinMPNN score distribution of all designs, selected
                       candidates highlighted; shows the quality-filter selection.
  figN_boltz_metrics : 4-panel Boltz-2 metric comparison across candidates
                       (ipTM, interface PAE, interface contacts, and MPNN-score-vs-ipTM
                       scatter with a QUALITY-ALIGNED Spearman correlation).
  figN_interfaces    : epitope contact-occupancy map -- how many candidates contact
                       each target residue -- plus per-candidate linear epitope tracks.

INPUT (all produced by earlier stages):
  --all-sequences  CSV from filter_sequences.py (all designs + 'pass' + selected flag)
  --metrics-json   JSON from analyze_interface.py (per-candidate interface metrics)
  --ranking-csv    CSV from analyze_interface.py (ranked summary)
  --outdir         directory to write figures into
  --prefix         filename prefix / figure-number stub (default 'fig')

CORRELATION CONVENTION (important -- read before interpreting panel D):
  ProteinMPNN score is better when LOWER; ipTM is better when HIGHER. The scatter
  plots mpnn_score (x) vs ipTM (y), so when the two metrics AGREE the points slope
  DOWNWARD and the raw Spearman(mpnn_score, ipTM) is NEGATIVE. To keep the printed
  sign consistent with the visible trend, panel D annotates the RAW correlation
  rho(MPNN, ipTM) and then states the interpretation in words: a negative raw rho
  means "metrics agree (both improve together)". It also checks whether the top-N /
  bottom-N candidate SETS agree even if the fine ordering within a set differs.
  (Do NOT report only +|rho| from Spearman(-mpnn_score, ipTM): it is technically the
  quality-aligned value but confuses readers looking at a downward-sloping plot.)

EXIT CODES
  0 success; 2 usage/input error.
"""
import argparse
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
import matplotlib.pyplot as plt
import numpy as np

# colorblind-friendly palette
BLUE = "#0279EE"; ORANGE = "#FF9400"; GREEN = "#75A025"; RED = "#C0392B"
GRAY = "#BDBDBD"; LIGHT_GREEN = "#EAF3DE"; LIGHT_RED = "#F7E9E9"


def die(msg, code=2):
    sys.stderr.write(f"[make_figures] ERROR: {msg}\n")
    sys.exit(code)


def save(fig, outdir, name):
    png = os.path.join(outdir, name + ".png")
    svg = os.path.join(outdir, name + ".svg")
    fig.savefig(png, dpi=155, bbox_inches="tight")
    fig.savefig(svg, bbox_inches="tight")
    plt.close(fig)
    print(f"[make_figures] wrote {png}")
    return png


def fig_mpnn_scores(df, selected_ids, outdir, prefix):
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    alls = df["mpnn_score"].dropna().values
    ax.hist(alls, bins=24, color=GRAY, alpha=0.75, edgecolor="white", label="all designs")
    sel = df[df["candidate"].isin(selected_ids)] if "candidate" in df.columns else df.head(0)
    if len(sel):
        for _, r in sel.iterrows():
            ax.axvline(r["mpnn_score"], color=GREEN, lw=1.6, alpha=0.9)
        ax.axvline(sel["mpnn_score"].iloc[0], color=GREEN, lw=1.6, alpha=0.9,
                   label=f"selected (n={len(sel)})")
    ax.set_xlabel("ProteinMPNN score  (lower = better)")
    ax.set_ylabel("number of designs")
    ax.set_title("Design score distribution and selected candidates")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    return save(fig, outdir, f"{prefix}_mpnn_scores")


def _spearman(x, y):
    try:
        from scipy.stats import spearmanr
        rho, p = spearmanr(x, y)
        return rho, p
    except Exception:
        return None, None


def fig_boltz_metrics(rank, outdir, prefix):
    r = rank.sort_values("iptm", ascending=False).reset_index(drop=True)
    cands = r["candidate"].tolist()
    x = np.arange(len(cands))
    colors = [GREEN if v > 0.8 else (ORANGE if v >= 0.6 else RED) for v in r["iptm"]]

    fig, axes = plt.subplots(2, 2, figsize=(10.5, 8.0))

    # A: ipTM
    ax = axes[0, 0]
    ax.bar(x, r["iptm"], color=colors, edgecolor="white")
    ax.axhline(0.8, ls="--", color=GRAY, lw=1); ax.axhline(0.6, ls=":", color=GRAY, lw=1)
    ax.set_xticks(x); ax.set_xticklabels(cands, rotation=45, ha="right")
    ax.set_ylabel("ipTM"); ax.set_title("A. Interface confidence (ipTM)")
    ax.set_ylim(0, 1.02); ax.spines[["top", "right"]].set_visible(False)

    # B: interface PAE
    ax = axes[0, 1]
    if "iface_pae" in r.columns:
        pcol = [GREEN if v < 5 else (ORANGE if v <= 10 else RED) for v in r["iface_pae"]]
        ax.bar(x, r["iface_pae"], color=pcol, edgecolor="white")
        ax.axhline(5, ls="--", color=GRAY, lw=1); ax.axhline(10, ls=":", color=GRAY, lw=1)
        ax.set_ylabel("interface PAE (\u00c5)")
    ax.set_xticks(x); ax.set_xticklabels(cands, rotation=45, ha="right")
    ax.set_title("B. Interface PAE (lower = better)")
    ax.spines[["top", "right"]].set_visible(False)

    # C: interface contacts
    ax = axes[1, 0]
    ccol = "n_interface_atom_contacts" if "n_interface_atom_contacts" in r.columns else None
    if ccol:
        ax.bar(x, r[ccol], color=BLUE, edgecolor="white")
        ax.set_ylabel("heavy-atom contacts")
    ax.set_xticks(x); ax.set_xticklabels(cands, rotation=45, ha="right")
    ax.set_title("C. Interface size (contacts)")
    ax.spines[["top", "right"]].set_visible(False)

    # D: MPNN score vs ipTM, quality-aligned correlation
    ax = axes[1, 1]
    if "mpnn_score" in r.columns and r["mpnn_score"].notna().all():
        ax.scatter(r["mpnn_score"], r["iptm"], c=colors, s=90, edgecolor="black", zorder=3)
        # split into top / bottom half by mpnn_score for set-agreement annotation
        n = len(r)
        k = max(1, n // 2)
        by_mpnn = r.sort_values("mpnn_score")
        top_mpnn = set(by_mpnn.head(k)["candidate"]); bot_mpnn = set(by_mpnn.tail(n - k)["candidate"])
        by_iptm = r.sort_values("iptm", ascending=False)
        top_iptm = set(by_iptm.head(k)["candidate"]); bot_iptm = set(by_iptm.tail(n - k)["candidate"])
        split = (by_mpnn["mpnn_score"].iloc[k - 1] + by_mpnn["mpnn_score"].iloc[k]) / 2 if n > 1 else by_mpnn["mpnn_score"].iloc[0]
        ax.axvspan(ax.get_xlim()[0], split, color=LIGHT_GREEN, alpha=0.6, zorder=0)
        ax.axvspan(split, ax.get_xlim()[1], color=LIGHT_RED, alpha=0.6, zorder=0)

        # --- per-point labels with fixed dark colour + white halo, zorder=6 ---
        LABEL_COLOR = "#222222"
        for _, row in r.iterrows():
            ax.annotate(row["candidate"], (row["mpnn_score"], row["iptm"]),
                        fontsize=8, color=LABEL_COLOR, zorder=6,
                        xytext=(4, 4), textcoords="offset points",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75))

        rho_raw, p = _spearman(r["mpnn_score"], r["iptm"])
        set_agree = (top_mpnn == top_iptm)
        if rho_raw is not None:
            # Report the RAW correlation on the plotted axes so the sign matches the
            # visible trend, then interpret it. Because lower MPNN = better but higher
            # ipTM = better, a NEGATIVE raw rho means the metrics AGREE (better designs
            # fold with higher confidence). Stating +|rho| alone confuses readers who
            # see a downward-sloping scatter, so we show both.
            agree_txt = ("metrics agree (both improve \u2192):\nsame top/bottom sets"
                         if set_agree and rho_raw < 0
                         else ("sets differ between metrics" if not set_agree
                               else "metrics disagree in direction"))
            txt = (f"\u03c1(MPNN, ipTM) = {rho_raw:+.2f}"
                   + (f"  (n={n}, p={p:.2f})" if p is not None else f"  (n={n})") + "\n"
                   + agree_txt + ";\norder differs within set")

            # --- automatic placement in the emptiest quadrant ---
            # Normalise plotted points to axes fraction, count per quadrant, place the
            # box in the emptiest one (tie-break lower-left). If the chosen quadrant
            # still contains a point within the box footprint, shrink font and re-place;
            # last resort: below the panel title outside the data area.
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            xs = (r["mpnn_score"].values - xlim[0]) / (xlim[1] - xlim[0])
            ys = (r["iptm"].values - ylim[0]) / (ylim[1] - ylim[0])
            quads = {
                "ll": sum((xs < 0.5) & (ys < 0.5)),
                "lr": sum((xs >= 0.5) & (ys < 0.5)),
                "ul": sum((xs < 0.5) & (ys >= 0.5)),
                "ur": sum((xs >= 0.5) & (ys >= 0.5)),
            }
            # tie-break order: ll, lr, ul, ur (lower-left first = empty good-MPNN/bad-ipTM corner)
            best_quad = min(quads, key=lambda q: (quads[q], ["ll", "lr", "ul", "ur"].index(q)))
            quad_pos = {"ll": (0.03, 0.03, "left", "bottom"),
                        "lr": (0.97, 0.03, "right", "bottom"),
                        "ul": (0.03, 0.97, "left", "top"),
                        "ur": (0.97, 0.97, "right", "top")}
            bx, by, ha, va = quad_pos[best_quad]
            ann_fontsize = 8.0

            # Check if any point falls within the box footprint (~0.28 x 0.22 axes frac)
            box_w, box_h = 0.30, 0.24
            def _in_footprint(qx, qy):
                if ha == "right":
                    x0, x1 = qx - box_w, qx
                else:
                    x0, x1 = qx, qx + box_w
                if va == "top":
                    y0, y1 = qy - box_h, qy
                else:
                    y0, y1 = qy, qy + box_h
                return any((x0 <= px <= x1) & (y0 <= py <= y1) for px, py in zip(xs, ys))

            if _in_footprint(bx, by):
                ann_fontsize = 7.0
                # try the second-emptiest quadrant
                ranked = sorted(quads, key=lambda q: (quads[q], ["ll", "lr", "ul", "ur"].index(q)))
                for alt_q in ranked[1:]:
                    bx2, by2, ha2, va2 = quad_pos[alt_q]
                    if not _in_footprint(bx2, by2):
                        bx, by, ha, va = bx2, by2, ha2, va2
                        ann_fontsize = 8.0
                        break
                else:
                    # all quadrants have a point in footprint -- place below title
                    bx, by, ha, va = 0.5, 1.08, "center", "bottom"
                    ann_fontsize = 7.0

            ax.annotate(txt, xy=(bx, by), xycoords="axes fraction",
                        ha=ha, va=va, fontsize=ann_fontsize, zorder=5,
                        bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=GRAY, alpha=0.92))
        ax.set_xlabel("ProteinMPNN score  (lower = better)")
        ax.set_ylabel("ipTM  (higher = better)")
    ax.set_title("D. Design score vs binding confidence")
    ax.spines[["top", "right"]].set_visible(False)

    fig.suptitle("Boltz-2 co-folding validation metrics", fontsize=13, fontweight="bold", y=1.0)
    fig.tight_layout()
    return save(fig, outdir, f"{prefix}_boltz_metrics")


def fig_interfaces(metrics, ranking, outdir, prefix):
    # Order candidates by rank if available.
    order = list(ranking["candidate"]) if ranking is not None else list(metrics.keys())
    order = [c for c in order if c in metrics]
    # Collect contact residues.
    per_cand = {c: sorted(metrics[c].get("target_contact_residues", [])) for c in order}
    all_res = sorted({res for residues in per_cand.values() for res in residues})
    if not all_res:
        die("no target_contact_residues found in metrics JSON.")
    lo, hi = min(all_res), max(all_res)
    span = list(range(lo, hi + 1))
    occupancy = {res: sum(res in per_cand[c] for c in order) for res in span}

    # --- construct span from metrics records ---
    construct_lo, construct_hi = lo, hi
    have_construct = False
    target_ranges = {str(md.get("target_range", "")) for md in metrics.values() if md.get("target_range")}
    if len(target_ranges) == 1:
        tr = target_ranges.pop()
        try:
            construct_lo, construct_hi = (int(x) for x in tr.split("-"))
            have_construct = True
        except Exception:
            pass

    # --- declared hotspot residues for vertical guide lines ---
    hotspots_declared = []
    for md in metrics.values():
        hd = md.get("hotspots_declared")
        if hd:
            hotspots_declared = sorted(hd)
            break

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.2),
                                   gridspec_kw={"height_ratios": [1.1, 1.4]})

    # --- hotspot vertical guide lines spanning both panels ---
    xlim_lo = construct_lo - 6 if have_construct else lo - 6
    xlim_hi = construct_hi + 2 if have_construct else hi + 2
    for hs in hotspots_declared:
        if xlim_lo <= hs <= xlim_hi:
            ax1.axvline(hs, color=RED, ls=":", lw=0.8, alpha=0.6, zorder=1)
            ax2.axvline(hs, color=RED, ls=":", lw=0.8, alpha=0.6, zorder=1)

    # Top: occupancy bar (how many candidates contact each residue).
    vals = [occupancy[r] for r in span]
    # --- build legend only from occupancy classes that actually occur ---
    present = set(vals) - {0}
    bars_colors = []
    for v in vals:
        if v == 0:
            bars_colors.append("#EEEEEE")
        elif v == len(order):
            bars_colors.append(GREEN)
        elif v >= 2:
            bars_colors.append(ORANGE)
        else:
            bars_colors.append(BLUE)
    ax1.bar(span, vals, color=bars_colors, width=1.0)
    ax1.set_ylabel("# candidates\ncontacting")
    # --- title uses construct span when available ---
    if have_construct:
        ax1.set_title(f"Target contact occupancy across the construct ({construct_lo}\u2013{construct_hi})")
    else:
        ax1.set_title(f"Target contact occupancy (residues contacted {lo}\u2013{hi})")
    ax1.set_ylim(0, len(order) + 0.5)
    ax1.set_xlim(xlim_lo, xlim_hi)
    ax1.spines[["top", "right"]].set_visible(False)
    # --- legend derived from values actually plotted ---
    from matplotlib.patches import Patch
    legend_items = []
    if max(vals) == len(order):
        legend_items.append(Patch(facecolor=GREEN, label=f"all {len(order)} candidates"))
    if any(v >= 2 and v < len(order) for v in vals):
        legend_items.append(Patch(facecolor=ORANGE, label="\u22652 candidates"))
    if 1 in present:
        legend_items.append(Patch(facecolor=BLUE, label="1 candidate"))
    if hotspots_declared:
        from matplotlib.lines import Line2D
        legend_items.append(Line2D([0], [0], color=RED, ls=":", lw=1, label="declared hotspots"))
    if legend_items:
        ax1.legend(handles=legend_items, frameon=False, fontsize=7.5, loc="upper left", ncol=len(legend_items))
    # --- guard "residues contacted by ALL" annotation ---
    conv = [r for r in span if occupancy[r] == len(order)]
    if conv and max(vals) == len(order):
        ax1.annotate(f"{len(conv)} residues contacted by ALL {len(order)}: "
                     f"{conv[0]}\u2013{conv[-1]} region",
                     xy=(0.99, 0.95), xycoords="axes fraction", ha="right", va="top",
                     fontsize=8.5, color=GREEN)

    # --- bottom track recoloured by epitope_status, not rank identity ---
    ONTARGET_COLOR = GREEN
    OFFTARGET_COLOR = RED
    NOTASSESSED_COLOR = GRAY
    for i, c in enumerate(order):
        y = len(order) - i
        hits = per_cand[c]
        md = metrics.get(c, {})
        status = md.get("epitope_status", "NOT_ASSESSED")
        if status in ("ON_TARGET", "PARTIAL"):
            pt_color = ONTARGET_COLOR
        elif status == "OFF_TARGET":
            pt_color = OFFTARGET_COLOR
        else:
            pt_color = NOTASSESSED_COLOR
        # emphasise rank 1 by marker size + edge, not hue
        if i == 0:
            ax2.scatter(hits, [y] * len(hits), marker="s", s=42,
                        color=pt_color, edgecolor="black", linewidth=0.8, zorder=4)
        else:
            ax2.scatter(hits, [y] * len(hits), marker="s", s=28,
                        color=pt_color, edgecolor="none", zorder=3)
        ax2.text(xlim_lo - 1, y, c, ha="right", va="center", fontsize=9)
    ax2.set_yticks([]); ax2.set_xlabel("target residue number (native)")
    ax2.set_xlim(xlim_lo, xlim_hi)
    ax2.set_title("Per-candidate contact footprint")
    ax2.spines[["top", "right", "left"]].set_visible(False)
    # --- attach legend to ax2 itself (never shared with ax1) ---
    track_legend = []
    track_legend.append(Patch(facecolor=ONTARGET_COLOR, label="on-target / partial"))
    track_legend.append(Patch(facecolor=OFFTARGET_COLOR, label="off-target"))
    track_legend.append(Patch(facecolor=NOTASSESSED_COLOR, label="not assessed"))
    from matplotlib.lines import Line2D
    track_legend.append(Line2D([0], [0], marker="s", color="w", markerfacecolor="black",
                               markeredgecolor="black", markersize=8, label="rank 1 (emphasised)"))
    ax2.legend(handles=track_legend, frameon=False, fontsize=7.5, loc="upper left", ncol=2)

    fig.tight_layout()
    return save(fig, outdir, f"{prefix}_interfaces")


def main():
    ap = argparse.ArgumentParser(
        description="Generate standard binder-design figures from pipeline outputs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--metrics-json", required=True, help="analyze_interface.py output JSON.")
    ap.add_argument("--ranking-csv", required=True, help="analyze_interface.py ranked CSV.")
    ap.add_argument("--all-sequences", default=None,
                    help="filter_sequences.py all-sequences CSV (for the MPNN-score figure).")
    ap.add_argument("--selected-csv", default=None,
                    help="filter_sequences.py selected CSV (candidate ids to highlight).")
    ap.add_argument("--outdir", required=True, help="Directory to write figures into.")
    ap.add_argument("--prefix", default="fig", help="Figure filename prefix.")
    args = ap.parse_args()

    import pandas as pd
    for f in (args.metrics_json, args.ranking_csv):
        if not os.path.isfile(f):
            die(f"missing input: {f}")
    os.makedirs(args.outdir, exist_ok=True)

    metrics = json.load(open(args.metrics_json))
    ranking = pd.read_csv(args.ranking_csv)

    written = []
    # MPNN score distribution (optional; needs all-sequences CSV).
    if args.all_sequences and os.path.isfile(args.all_sequences):
        df = pd.read_csv(args.all_sequences)
        selected_ids = []
        if args.selected_csv and os.path.isfile(args.selected_csv):
            selected_ids = list(pd.read_csv(args.selected_csv)["candidate"])
        elif "candidate" in ranking.columns:
            selected_ids = list(ranking["candidate"])
        # attach candidate ids to df rows by matching design+mpnn_score if possible
        if "candidate" not in df.columns and args.selected_csv and os.path.isfile(args.selected_csv):
            selc = pd.read_csv(args.selected_csv)[["candidate", "design", "mpnn_score"]]
            df = df.merge(selc, on=["design", "mpnn_score"], how="left")
        written.append(fig_mpnn_scores(df, selected_ids, args.outdir, args.prefix))

    written.append(fig_boltz_metrics(ranking, args.outdir, args.prefix))
    written.append(fig_interfaces(metrics, ranking, args.outdir, args.prefix))

    print(f"[make_figures] done -- {len(written)} figure(s) written to {args.outdir}")


if __name__ == "__main__":
    main()
