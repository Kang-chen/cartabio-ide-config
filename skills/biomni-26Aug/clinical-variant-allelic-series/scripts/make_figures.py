#!/usr/bin/env python3
"""
make_figures.py — Adaptive figure set for a gene's allelic series.

Figures (drawn only when the underlying data exists):
  F1  Protein-domain lollipop of alleles (ALWAYS)
        - color + shape encode category (colorblind-robust)
        - domain track fetched live from UniProt for the gene's canonical protein
  F2  Variant landscape (ALWAYS): significance + molecular-consequence panels
  F3  CIViC evidence distribution (IF single-variant evidence exists)
  F4  Variant x therapy actionability matrix (IF >=1 therapy association)
        - colorblind-safe diverging palette; +level = sensitivity, -level = resistance

Every figure is saved as BOTH .png and .svg (editable text). The agent MUST run a
Read media-output-check on each PNG and regenerate on failure (see SKILL.md).

Usage:
    python make_figures.py --gene EGFR --outdir /mnt/results/EGFR_allelic_series \
        [--uniprot P00533]   # optional; auto-resolved from gene symbol if omitted
"""
import argparse
import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
import numpy as np
import pandas as pd
import requests

matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42

LEVEL_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}


def _as_bool(series):
    """Normalize a column that may be real bool or the strings 'True'/'False'."""
    return series.astype(str).str.strip().str.lower().isin(["true", "1", "yes"])

# Category color + marker (colorblind-robust via BOTH hue and shape)
CAT_COLORS = {"CIViC actionable": "#0279EE", "ClinVar P/LP": "#D55E00", "Other annotated": "#9B9B9B"}
CAT_MARKERS = {"CIViC actionable": "o", "ClinVar P/LP": "^", "Other annotated": "s"}

DOMAIN_FILL = ["#8FB3D9", "#C9DCEC", "#F4A742", "#75A025", "#D9D9D9", "#B7A3D9"]


# ----------------------------------------------------------------------------
# UniProt: resolve gene -> canonical accession, protein length, domain features
# ----------------------------------------------------------------------------
def resolve_uniprot(gene, accession=None):
    """Return (accession, length, [(name,start,end), ...]) from UniProt REST."""
    try:
        if not accession:
            q = (f"https://rest.uniprot.org/uniprotkb/search?query="
                 f"gene_exact:{gene}+AND+organism_id:9606+AND+reviewed:true"
                 f"&fields=accession,length&format=json&size=1")
            j = requests.get(q, timeout=60).json()
            results = j.get("results", [])
            if not results:
                return None, None, []
            accession = results[0]["primaryAccession"]
        # full entry for features
        e = requests.get(
            f"https://rest.uniprot.org/uniprotkb/{accession}.json", timeout=60
        ).json()
        length = e.get("sequence", {}).get("length")
        # Collect features by class. Structural domains (Domain, Transmembrane)
        # are preferred; broad Topological/Region spans overlap them on the track
        # and are only used as a fallback for proteins lacking fine annotation.
        structural, broad = [], []
        for f in e.get("features", []):
            ftype = f.get("type")
            loc = f.get("location", {})
            s = loc.get("start", {}).get("value")
            en = loc.get("end", {}).get("value")
            if not (s and en):
                continue
            desc = f.get("description", ftype)
            item = (desc, int(s), int(en))
            if ftype in ("Domain", "Transmembrane"):
                structural.append(item)
            elif ftype in ("Region", "Topological domain"):
                broad.append(item)
        structural = [d for d in structural if (d[2] - d[1]) >= 15]
        broad = [d for d in broad if (d[2] - d[1]) >= 15]
        # Use structural domains if we have any; else fall back to broad spans.
        domains = structural if structural else broad
        domains = sorted(domains, key=lambda d: d[1])[:10]
        return accession, length, domains
    except Exception as e:  # noqa: BLE001
        print(f"[fig] UniProt resolve failed for {gene}: {e}")
        return accession, None, []


def categorize(row):
    if pd.notna(row.get("civic_best_level")) and str(row.get("civic_best_level") or ""):
        return "CIViC actionable"
    sig = str(row.get("clinvar_significance", "") or "")
    if re.search(r"athogenic", sig, re.I) and "onflicting" not in sig.lower():
        return "ClinVar P/LP"
    return "Other annotated"


# ----------------------------------------------------------------------------
# F1 lollipop
# ----------------------------------------------------------------------------
def _thin_by_gap(notable, x0, x1, max_labels, min_gap_frac=0.030):
    """
    Keep labels that are at least min_gap apart (in residues). When two notable
    alleles are closer than the gap, keep the higher-weight one. Guarantees
    rotated vertical labels won't collide regardless of gene density.
    """
    span = max(x1 - x0, 1)
    min_gap = span * min_gap_frac
    kept = []
    for _, r in notable.sort_values("weight", ascending=False).iterrows():
        x = r["residue_position"]
        if all(abs(x - k) >= min_gap for k in kept):
            kept.append(x)
        if len(kept) >= max_labels:
            break
    return notable[notable["residue_position"].isin(kept)]


def _fanout_labels(ax, notable, x0, x1, base_top, fontsize=7.4):
    """
    Fan-out labels: retained labels are spread at EVENLY spaced x positions
    across the panel top, each connected by an angled leader line to its true
    marker x. This decouples label spacing from marker spacing, so tightly
    clustered hotspot alleles (e.g. BRAF 594-601) still get readable, non-
    overlapping labels. Gene-agnostic and deterministic.
    """
    nb = notable.sort_values("residue_position").reset_index(drop=True)
    n = len(nb)
    if n == 0:
        return base_top + 1.0
    span = max(x1 - x0, 1)
    # anchor row for the label text, above the tallest stem
    ytext = base_top + 1.15
    yknee = base_top + 0.55            # where the leader line bends
    # evenly spaced label x slots inside a margin
    left, right = x0 + span * 0.03, x1 - span * 0.03
    if n == 1:
        slots = [(left + right) / 2]
    else:
        slots = [left + (right - left) * i / (n - 1) for i in range(n)]
    for i, (_, r) in enumerate(nb.iterrows()):
        xm = r["residue_position"]      # true marker x
        xl = slots[i]                   # label x slot
        # leader: stem tip -> knee at marker x -> label slot
        ax.plot([xm, xm, xl], [r["stem"] + 0.02, yknee, ytext - 0.05],
                color="#B0B0B0", lw=0.5, zorder=3)
        ax.text(xl, ytext, r["variant"], fontsize=fontsize, ha="center",
                va="bottom", rotation=90, zorder=6)
    maxlen = nb["variant"].astype(str).str.len().max()
    return ytext + 0.16 * float(maxlen) + 0.3


def _draw_track(ax, length, domains, spec, track_y, track_h, x0, x1):
    """Domain track + faint allele rug, CLIPPED to the visible [x0,x1] window."""
    vx0, vx1 = max(1, x0), min(length, x1)
    ax.add_patch(plt.Rectangle((vx0, track_y), vx1 - vx0, track_h,
                               facecolor="#EEEEEE", edgecolor="#BBBBBB", lw=0.5, zorder=1))
    vspan = max(vx1 - vx0, 1)
    last_label_right = -1e9        # rightmost x of the previously drawn label
    for i, (name, s, e) in enumerate(domains or []):
        ds, de = max(s, vx0), min(e, vx1)
        if de <= ds:
            continue  # domain not in this window
        ax.add_patch(plt.Rectangle((ds, track_y), de - ds, track_h,
                                   facecolor=DOMAIN_FILL[i % len(DOMAIN_FILL)],
                                   edgecolor="#666", lw=0.4, zorder=2))
        # Label only if the box is wide enough AND the label won't collide with
        # the previous one (approx label half-width from char count).
        if (de - ds) > vspan * 0.05:
            label = name[:16]
            cx = (ds + de) / 2
            half_w = vspan * 0.006 * len(label)   # rough text half-width in data units
            if cx - half_w > last_label_right:
                ax.text(cx, track_y + track_h / 2, label, ha="center",
                        va="center", fontsize=6.2, zorder=3)
                last_label_right = cx + half_w
    rug = spec[(spec["residue_position"] >= vx0) & (spec["residue_position"] <= vx1)]
    ax.scatter(rug["residue_position"], np.full(len(rug), track_y + track_h + 0.05),
               marker="|", s=18, c="#C9C9C9", lw=0.5, zorder=2)


def fig_lollipop(gene, master, length, domains, figdir, max_labels=22):
    spec = master[(master["is_specific_allele"] == True) &  # noqa: E712
                  master["residue_position"].notna()].copy()
    if spec.empty:
        print("[fig] F1 skipped: no positioned specific alleles")
        return None
    spec["residue_position"] = spec["residue_position"].astype(float)
    spec["cat"] = spec.apply(categorize, axis=1)
    if not length:
        length = int(spec["residue_position"].max() + 30)

    # evidence weight -> stem height
    spec["weight"] = pd.to_numeric(spec.get("n_evidence", 1), errors="coerce").fillna(1)
    spec["stem"] = 1 + np.log1p(spec["weight"])

    # Notable set to lollipop + label: Tier-1-with-evidence first, then top by
    # weight. We do NOT lollipop all alleles (dense genes -> unreadable); the
    # full landscape is shown as a faint density rug on the domain track.
    labelable = spec[(spec["tier"] == 1) & (spec["weight"] > 0)]
    if len(labelable) < max_labels:
        labelable = pd.concat([labelable, spec.sort_values("weight", ascending=False)]) \
            .drop_duplicates("variant")
    notable = labelable.sort_values("weight", ascending=False).head(max_labels).copy()

    # Decide layout: if notable alleles are concentrated in a sub-window that is
    # small vs the whole protein, add a zoom panel on that window.
    lo, hi = notable["residue_position"].min(), notable["residue_position"].max()
    concentrated = (hi - lo) < 0.55 * length and length > 400 and len(notable) >= 6
    pad = max(0.03 * length, 20)
    zlo, zhi = max(1, lo - pad), min(length, hi + pad)

    if concentrated:
        fig, (axT, axZ) = plt.subplots(
            2, 1, figsize=(14, 9.0),
            gridspec_kw={"height_ratios": [0.9, 1.6]})
    else:
        fig, axT = plt.subplots(figsize=(14, 7.5))
        axZ = None

    track_y, track_h = -0.7, 0.5

    def draw_panel(ax, x0, x1, do_labels, title):
        _draw_track(ax, length, domains, spec, track_y, track_h, x0, x1)
        sub = spec[(spec["residue_position"] >= x0) & (spec["residue_position"] <= x1)]
        for cat, g in sub.groupby("cat"):
            ax.scatter(g["residue_position"], g["stem"], c=CAT_COLORS.get(cat, "#999"),
                       marker=CAT_MARKERS.get(cat, "o"), s=46, edgecolor="white",
                       lw=0.6, zorder=5, label=cat)
            for _, r in g.iterrows():
                ax.plot([r["residue_position"], r["residue_position"]], [0, r["stem"]],
                        color=CAT_COLORS.get(cat, "#999"), lw=0.8, alpha=0.7, zorder=4)
        top = sub["stem"].max() if len(sub) else 1.5
        ymax = top + 1.0
        if do_labels:
            nb = notable[(notable["residue_position"] >= x0) &
                         (notable["residue_position"] <= x1)]
            nb = _thin_by_gap(nb, x0, x1, max_labels, min_gap_frac=0.012)
            ymax = _fanout_labels(ax, nb, x0, x1, top)
        ax.set_xlim(x0 - (x1 - x0) * 0.02, x1 + (x1 - x0) * 0.02)
        ax.set_ylim(track_y - 0.25, ymax)
        ax.set_yticks([])
        ax.set_xlabel("Residue position (aa)")
        ax.set_title(title, fontsize=11.5, weight="bold")
        for sp in ("top", "left", "right"):
            ax.spines[sp].set_visible(False)

    if concentrated:
        # Top: full-length overview, markers only (no labels).
        draw_panel(axT, 1, length, False,
                   f"{gene} allelic series - full-length overview")
        axT.legend(loc="upper right", frameon=False, fontsize=8)
        # highlight the zoom window on the overview
        axT.axvspan(zlo, zhi, color="#FFF2D9", alpha=0.6, zorder=0)
        # Bottom: zoom with labels.
        draw_panel(axZ, zlo, zhi, True,
                   f"Zoom: residues {int(zlo)}-{int(zhi)} (labeled: notable alleles)")
        axZ.legend(loc="upper right", frameon=False, fontsize=8)
    else:
        draw_panel(axT, 1, length, True,
                   f"{gene} allelic series - protein-domain lollipop")
        axT.legend(loc="upper right", frameon=False, fontsize=8)

    fig.subplots_adjust(left=0.05, right=0.98, top=0.93, bottom=0.08, hspace=0.42)
    png = os.path.join(figdir, "F1_lollipop.png")
    fig.savefig(png, dpi=200)
    fig.savefig(os.path.join(figdir, "F1_lollipop.svg"))
    plt.close(fig)
    print(f"[fig] wrote {png} (concentrated={concentrated}, {len(notable)} labeled)")
    return png


# ----------------------------------------------------------------------------
# F2 landscape
# ----------------------------------------------------------------------------
def fig_landscape(gene, cv, figdir):
    if cv.empty:
        print("[fig] F2 skipped: empty ClinVar catalog")
        return None
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    # panel A: germline classification counts (top categories)
    sig = cv["germline_classification"].fillna("").replace("", "not provided")
    top = sig.value_counts().head(8)[::-1]
    axes[0].barh(top.index, top.values, color="#0279EE")
    axes[0].set_title("ClinVar classification", fontsize=11, weight="bold")
    axes[0].set_xlabel("variants")
    # panel B: molecular consequence
    mc = cv["molecular_consequence"].fillna("").str.split(";").explode().str.strip()
    mc = mc[mc != ""]
    topmc = mc.value_counts().head(8)[::-1]
    axes[1].barh(topmc.index, topmc.values, color="#75A025")
    axes[1].set_title("Molecular consequence", fontsize=11, weight="bold")
    axes[1].set_xlabel("variants")
    for ax in axes:
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(labelsize=8)
    fig.suptitle(f"{gene} ClinVar variant landscape", fontsize=12, weight="bold")
    fig.tight_layout()
    png = os.path.join(figdir, "F2_landscape.png")
    fig.savefig(png, dpi=200, bbox_inches="tight")
    fig.savefig(os.path.join(figdir, "F2_landscape.svg"), bbox_inches="tight")
    plt.close(fig)
    print(f"[fig] wrote {png}")
    return png


# ----------------------------------------------------------------------------
# F3 evidence distribution
# ----------------------------------------------------------------------------
def fig_evidence(gene, ev, figdir):
    if ev.empty:
        print("[fig] F3 skipped: no CIViC evidence")
        return None
    e = ev[_as_bool(ev["is_single_variant_evidence"])].copy() if "is_single_variant_evidence" in ev.columns else ev.copy()
    if e.empty:
        print("[fig] F3 skipped: no single-variant evidence")
        return None
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    lv = e.get("evidence_level", pd.Series(dtype=str)).fillna("NA")
    lvc = lv.value_counts().sort_index()
    axes[0].bar(lvc.index, lvc.values, color="#D4A04A")
    axes[0].set_title("Evidence level", fontsize=11, weight="bold")
    axes[0].set_ylabel("evidence items")
    et = e.get("evidence_type", pd.Series(dtype=str)).fillna("NA").value_counts()[::-1]
    axes[1].barh(et.index, et.values, color="#0279EE")
    axes[1].set_title("Evidence type", fontsize=11, weight="bold")
    for ax in axes:
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(labelsize=8)
    fig.suptitle(f"{gene} CIViC clinical evidence", fontsize=12, weight="bold")
    fig.tight_layout()
    png = os.path.join(figdir, "F3_evidence.png")
    fig.savefig(png, dpi=200, bbox_inches="tight")
    fig.savefig(os.path.join(figdir, "F3_evidence.svg"), bbox_inches="tight")
    plt.close(fig)
    print(f"[fig] wrote {png}")
    return png


# ----------------------------------------------------------------------------
# F4 therapy matrix
# ----------------------------------------------------------------------------
def fig_therapy_matrix(gene, ev, figdir, max_variants=14, max_ther=18):
    if ev.empty:
        print("[fig] F4 skipped: no CIViC evidence")
        return None
    e = ev[_as_bool(ev["is_single_variant_evidence"])].copy() if "is_single_variant_evidence" in ev.columns else ev.copy()
    e = e[e.get("evidence_type", "").astype(str).str.contains("Predictive", case=False, na=False)]
    if e.empty:
        print("[fig] F4 skipped: no predictive/therapy evidence")
        return None

    def col(name, default=""):
        return e[name].fillna(default) if name in e.columns else pd.Series([default] * len(e), index=e.index)

    e = e.assign(
        _lvl=col("evidence_level").astype(str).str.strip().str.upper().str[:1],
        _sig=col("significance", "").astype(str),
        _dir=col("evidence_direction", "").astype(str),
        _ther=col("therapies", "").astype(str),
        _var=col("variant").astype(str),
    )
    recs = []
    for _, r in e.iterrows():
        rank = LEVEL_RANK.get(r["_lvl"], 0)
        if rank == 0:
            continue
        thers = [t.strip() for t in re.split(r"[,;]", r["_ther"]) if t.strip()]
        # Combine significance with evidence_direction: a "Does Not Support"
        # item inverts the significance (Does Not Support Resistance -> the drug
        # works -> sensitivity; Does Not Support Sensitivity -> resistance).
        supports = not re.search(r"Does\s*Not\s*Support", r["_dir"], re.I)
        sig_sens = bool(re.search(r"Sensitivity|Response", r["_sig"], re.I))
        sig_res = bool(re.search(r"Resistance", r["_sig"], re.I))
        sens = (sig_sens and supports) or (sig_res and not supports)
        res = (sig_res and supports) or (sig_sens and not supports)
        for t in thers:
            val = 0
            if sens and not res:
                val = rank
            elif res and not sens:
                val = -rank
            elif sens and res:
                val = 0.0  # both -> shown as +/-
            recs.append({"variant": r["_var"], "therapy": t, "val": val,
                         "both": sens and res})
    if not recs:
        print("[fig] F4 skipped: no rankable therapy associations")
        return None
    d = pd.DataFrame(recs)
    # pick top variants/therapies by association count
    tv = d["variant"].value_counts().head(max_variants).index
    tt = d["therapy"].value_counts().head(max_ther).index
    d = d[d["variant"].isin(tv) & d["therapy"].isin(tt)]
    mat = d.pivot_table(index="variant", columns="therapy", values="val",
                        aggfunc=lambda s: max(s, key=abs))
    both = d.pivot_table(index="variant", columns="therapy", values="both", aggfunc="any")

    cmap = LinearSegmentedColormap.from_list(
        "sens_res", ["#B2182B", "#EF8A62", "#FDDBC7", "#F7F7F7",
                     "#D1E5F0", "#67A9CF", "#2166AC"])
    cmap.set_bad("#EDEDED")
    norm = TwoSlopeNorm(vmin=-5, vcenter=0, vmax=5)

    fig, ax = plt.subplots(figsize=(min(1.0 + 0.6 * mat.shape[1], 15),
                                    min(1.0 + 0.5 * mat.shape[0], 12)))
    im = ax.imshow(mat.values, cmap=cmap, norm=norm, aspect="auto")
    ax.set_xticks(range(mat.shape[1]))
    ax.set_xticklabels(mat.columns, rotation=45, ha="right", fontsize=7.5)
    ax.set_yticks(range(mat.shape[0]))
    ax.set_yticklabels(mat.index, fontsize=8)
    # annotate cells: letter for level, +/- for both
    inv = {v: k for k, v in LEVEL_RANK.items()}
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            if pd.isna(v):
                continue
            if both.values[i, j]:
                txt = "±"
            else:
                txt = inv.get(int(abs(v)), "")
            ax.text(j, i, txt, ha="center", va="center", fontsize=7,
                    color="#222" if abs(v) < 4 else "white")
    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02,
                        ticks=[-5, -3, 0, 3, 5])
    cbar.ax.set_yticklabels(["Resistance A", "C", "—", "C", "Sensitivity A"], fontsize=7)
    ax.set_title(f"{gene} variant × therapy actionability (CIViC)\n"
                 "Blue = sensitivity, Red = resistance, ± = both directions",
                 fontsize=11, weight="bold")
    fig.tight_layout()
    png = os.path.join(figdir, "F4_therapy_matrix.png")
    fig.savefig(png, dpi=200, bbox_inches="tight")
    fig.savefig(os.path.join(figdir, "F4_therapy_matrix.svg"), bbox_inches="tight")
    plt.close(fig)
    print(f"[fig] wrote {png}")
    return png


def make_all(gene, outdir, uniprot=None):
    figdir = os.path.join(outdir, "figures")
    os.makedirs(figdir, exist_ok=True)
    master = pd.read_csv(os.path.join(outdir, f"{gene}_allelic_series_master.csv"))
    cv_path = os.path.join(outdir, f"{gene}_clinvar_full_catalog.csv")
    ev_path = os.path.join(outdir, f"{gene}_civic_evidence_long.csv")
    cv = pd.read_csv(cv_path, dtype=str) if os.path.exists(cv_path) else pd.DataFrame()
    ev = pd.read_csv(ev_path, dtype=str) if os.path.exists(ev_path) else pd.DataFrame()

    acc, length, domains = resolve_uniprot(gene, uniprot)
    print(f"[fig] UniProt {acc}, length={length}, {len(domains)} domains")

    produced = {}
    produced["F1"] = fig_lollipop(gene, master, length, domains, figdir)
    produced["F2"] = fig_landscape(gene, cv, figdir)
    produced["F3"] = fig_evidence(gene, ev, figdir)
    produced["F4"] = fig_therapy_matrix(gene, ev, figdir)
    produced = {k: v for k, v in produced.items() if v}
    with open(os.path.join(figdir, "figures_manifest.json"), "w") as fh:
        json.dump({"gene": gene, "uniprot": acc, "protein_length": length,
                   "figures": produced}, fh, indent=2)
    print(f"[fig] produced {list(produced)}")
    return produced


def main():
    ap = argparse.ArgumentParser(description="Adaptive figures for an allelic series.")
    ap.add_argument("--gene", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--uniprot", default=None, help="Optional UniProt accession override")
    args = ap.parse_args()
    make_all(args.gene.upper(), args.outdir, args.uniprot)


if __name__ == "__main__":
    sys.exit(main())
