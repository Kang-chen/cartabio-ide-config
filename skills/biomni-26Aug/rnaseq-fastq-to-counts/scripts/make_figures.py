#!/usr/bin/env python3
"""
make_figures.py

Generate the four standard QC/quantification figure groups for a bulk RNA-seq
FASTQ->counts run, from the pipeline's own output files. Colorblind-friendly
palette, editable SVG + PNG, Liberation Sans (Arial-metric) fonts.

Figure groups:
  fig1_qc          Read-QC summary from FastQC (per-base quality + %GC + read length)
  fig2_alignment   STAR alignment outcome (mapping categories + splice-junction breakdown)
  fig3_counts      Count distribution (log10 count histogram + top-N expressed genes + biotype)
  fig4_assignment  Read-assignment breakdown (assigned/no_feature/ambiguous/multimapping/unmapped)

Inputs (paths; any missing input -> that figure is skipped with a warning):
  --log-final     STAR Log.final.out
  --reads-per-gene STAR ReadsPerGene.out.tab (uses the selected strandedness column)
  --strandedness  strandedness.json (to pick the column; default unstranded)
  --assignment    assignment_summary.csv
  --gene-metadata gene_metadata.csv (for gene names + biotypes on fig3)
  --fastqc-r1 / --fastqc-r2   FastQC *_fastqc/fastqc_data.txt OR *_fastqc.zip (optional)
  --sample-name   label used in titles
  --outdir        directory for the figures (a figures/ subdir is created)

Usage:
  python make_figures.py --log-final star/S_Log.final.out \
    --reads-per-gene star/S_ReadsPerGene.out.tab --strandedness strandedness.json \
    --assignment assignment_summary.csv --gene-metadata gene_metadata.csv \
    --sample-name S --outdir /mnt/results/<run>
"""
import argparse, json, os, re, sys, io, zipfile
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams.update({"font.size": 11, "axes.titlesize": 12, "axes.labelsize": 11,
                            "xtick.labelsize": 10, "ytick.labelsize": 10, "legend.fontsize": 10})
import matplotlib.pyplot as plt
import numpy as np

# Colorblind-friendly (Okabe-Ito) + Phylo-ish accents
CB = {"blue": "#0072B2", "orange": "#E69F00", "green": "#009E73", "vermilion": "#D55E00",
      "purple": "#CC79A7", "sky": "#56B4E9", "yellow": "#F0E442", "gray": "#999999", "gold": "#D4A04A"}

SPECIAL_ROWS = {"N_unmapped", "N_multimapping", "N_noFeature", "N_ambiguous"}


def savefig(fig, outdir, stem):
    figdir = os.path.join(outdir, "figures")
    os.makedirs(figdir, exist_ok=True)
    for ext in ("png", "svg"):
        fig.savefig(os.path.join(figdir, f"{stem}.{ext}"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {stem}", flush=True)


def parse_log_final(path):
    d = {}
    with open(path) as fh:
        for line in fh:
            if "|" in line:
                k, v = line.split("|", 1)
                d[k.strip()] = v.strip()
    def num(key):
        v = d.get(key, "0").replace("%", "").replace(",", "")
        try: return float(v)
        except ValueError: return 0.0
    return d, num


def selected_col_idx(strandedness_path):
    if strandedness_path and os.path.exists(strandedness_path):
        with open(strandedness_path) as fh:
            j = json.load(fh)
        if isinstance(j, dict) and "selected_column" not in j:  # multi-sample
            j = list(j.values())[0]
        sel = j.get("selected_column", "unstranded")
        return {"unstranded": 0, "forward": 1, "reverse": 2}.get(sel, 0), sel
    return 0, "unstranded"


def read_fastqc(path):
    """Accept a fastqc_data.txt path or a *_fastqc.zip; return dict of modules."""
    if path is None:
        return None
    text = None
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.endswith("fastqc_data.txt")]
            if name:
                text = z.read(name[0]).decode("utf-8", "replace")
    elif path.endswith("fastqc_data.txt"):
        with open(path) as fh:
            text = fh.read()
    elif os.path.isdir(path):
        p = os.path.join(path, "fastqc_data.txt")
        if os.path.exists(p):
            text = open(p).read()
    if text is None:
        return None
    modules = {}
    cur = None; rows = []
    for line in text.splitlines():
        if line.startswith(">>") and not line.startswith(">>END"):
            cur = line[2:].rsplit("\t", 1)[0]; rows = []
        elif line.startswith(">>END_MODULE"):
            if cur: modules[cur] = rows
            cur = None
        elif cur is not None and not line.startswith("#"):
            rows.append(line.split("\t"))
    return modules


# ---------------- fig1: QC ----------------
def fig_qc(fq1, fq2, sample, outdir):
    m1 = read_fastqc(fq1); m2 = read_fastqc(fq2)
    if not m1 and not m2:
        print("WARN: no FastQC data — skipping fig1_qc", flush=True)
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    # per-base quality (mean) for R1/R2. FastQC bins later positions into ranges
    # like "10-14"; use the (mid)point of the base column as the true x position.
    def base_pos(tok):
        tok = str(tok)
        if "-" in tok:
            a, b = tok.split("-", 1)
            try:
                return (float(a) + float(b)) / 2.0
            except ValueError:
                return None
        try:
            return float(tok)
        except ValueError:
            return None
    ax = axes[0]
    for m, lab, col, ls in [(m1, "R1", CB["blue"], "-"), (m2, "R2", CB["orange"], "--")]:
        if m and "Per base sequence quality" in m:
            rows = m["Per base sequence quality"]
            xy = [(base_pos(r[0]), float(r[1])) for r in rows if base_pos(r[0]) is not None]
            if xy:
                x = [p for p, _ in xy]; y = [q for _, q in xy]
                ax.plot(x, y, label=lab, color=col, lw=2, linestyle=ls, alpha=0.9)
    ax.axhspan(28, 40, color=CB["green"], alpha=0.08)
    ax.axhspan(20, 28, color=CB["yellow"], alpha=0.08)
    ax.set_xlabel("Position in read (bp)"); ax.set_ylabel("Mean Phred quality")
    ax.set_title("Per-base sequence quality"); ax.set_ylim(0, 41); ax.legend()
    # read length + %GC text panel
    ax = axes[1]; ax.axis("off")
    def stat(m, key, field=0):
        if m and "Basic Statistics" in m:
            for r in m["Basic Statistics"]:
                if r[0] == key:
                    return r[1]
        return "NA"
    lines = [f"Sample: {sample}", ""]
    for m, lab in [(m1, "R1"), (m2, "R2")]:
        if m:
            lines.append(f"{lab}: length={stat(m,'Sequence length')} bp, "
                         f"%GC={stat(m,'%GC')}, seqs={stat(m,'Total Sequences')}")
    ax.text(0.02, 0.95, "\n".join(lines), va="top", ha="left", fontsize=11, family="monospace")
    ax.set_title("Read summary (FastQC)")
    fig.suptitle(f"Read QC — {sample}", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    savefig(fig, outdir, "fig1_qc")


# ---------------- fig2: alignment ----------------
def fig_alignment(log_final, sample, outdir):
    if not (log_final and os.path.exists(log_final)):
        print("WARN: no Log.final.out — skipping fig2_alignment", flush=True)
        return
    d, num = parse_log_final(log_final)
    total = num("Number of input reads")
    uniq = num("Uniquely mapped reads number")
    multi = num("Number of reads mapped to multiple loci")
    many = num("Number of reads mapped to too many loci")
    short = num("Number of reads unmapped: too short")
    other = num("Number of reads unmapped: other") + num("Number of reads unmapped: too many mismatches")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    # mapping categories
    ax = axes[0]
    cats = ["Unique", "Multi", "Too many\nloci", "Unmapped\ntoo short", "Unmapped\nother"]
    vals = [uniq, multi, many, short, other]
    cols = [CB["green"], CB["sky"], CB["yellow"], CB["vermilion"], CB["gray"]]
    bars = ax.bar(cats, vals, color=cols)
    ax.set_ylabel("Reads"); ax.set_title(f"Alignment categories ({total/1e6:.1f}M input)")
    for b, v in zip(bars, vals):
        pct = 100 * v / total if total else 0
        ax.text(b.get_x() + b.get_width()/2, v, f"{pct:.1f}%", ha="center", va="bottom", fontsize=9)
    ax.margins(y=0.15)
    # splice junctions
    ax = axes[1]
    sj = [("Annotated", num("Number of splices: Annotated (sjdb)"), CB["green"]),
          ("GT/AG", num("Number of splices: GT/AG"), CB["blue"]),
          ("GC/AG", num("Number of splices: GC/AG"), CB["orange"]),
          ("AT/AC", num("Number of splices: AT/AC"), CB["purple"]),
          ("Non-canon.", num("Number of splices: Non-canonical"), CB["vermilion"])]
    ax.barh([s[0] for s in sj][::-1], [s[1] for s in sj][::-1],
            color=[s[2] for s in sj][::-1])
    ax.set_xlabel("Splice junctions")
    ax.set_title(f"Splice junctions (total {int(num('Number of splices: Total')):,})")
    fig.suptitle(f"STAR alignment — {sample}", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    savefig(fig, outdir, "fig2_alignment")


# ---------------- fig3: counts ----------------
def load_counts_col(rpg_path, col_idx):
    counts = {}
    with open(rpg_path) as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 4 or p[0] in SPECIAL_ROWS:
                continue
            try:
                counts[p[0]] = int(p[1 + col_idx])
            except (ValueError, IndexError):
                continue
    return counts


def fig_counts(rpg_path, col_idx, gene_meta_path, sample, outdir, topn=20):
    if not (rpg_path and os.path.exists(rpg_path)):
        print("WARN: no ReadsPerGene.out.tab — skipping fig3_counts", flush=True)
        return
    counts = load_counts_col(rpg_path, col_idx)
    names, biotypes = {}, {}
    if gene_meta_path and os.path.exists(gene_meta_path):
        import csv
        with open(gene_meta_path) as fh:
            for row in csv.DictReader(fh):
                names[row["gene_id"]] = row.get("gene_name") or row["gene_id"]
                biotypes[row["gene_id"]] = row.get("gene_biotype") or "unknown"
    detected = {g: c for g, c in counts.items() if c > 0}
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.8))
    fig.subplots_adjust(wspace=0.35)
    # log10 histogram
    ax = axes[0]
    vals = np.log10(np.array(list(detected.values())) + 1)
    ax.hist(vals, bins=40, color=CB["blue"], edgecolor="white")
    ax.set_xlabel("log10(count + 1)"); ax.set_ylabel("Genes")
    ax.tick_params(axis="both", labelsize=10)
    ax.set_title(f"Count distribution ({len(detected)} detected)")
    # top-N genes
    ax = axes[1]
    top = sorted(detected.items(), key=lambda kv: -kv[1])[:topn]
    labels = [names.get(g, g) for g, _ in top]
    # shorten long unnamed Ensembl IDs
    labels = [(l if len(l) <= 15 else "…" + l[-6:]) for l in labels]
    ax.barh(labels[::-1], [c for _, c in top][::-1], color=CB["gold"])
    ax.set_xlabel("Counts"); ax.set_title(f"Top {topn} expressed genes")
    ax.tick_params(axis="y", labelsize=9)
    # biotype of detected genes
    ax = axes[2]
    if biotypes:
        from collections import Counter
        bt = Counter(biotypes.get(g, "unknown") for g in detected)
        top_bt = bt.most_common(6)
        ax.barh([b for b, _ in top_bt][::-1], [c for _, c in top_bt][::-1], color=CB["green"])
        ax.set_xlabel("Detected genes"); ax.set_title("Gene biotype (detected)")
        ax.tick_params(axis="y", labelsize=8)
    else:
        ax.axis("off"); ax.text(0.5, 0.5, "no gene_metadata", ha="center")
    fig.suptitle(f"Expression counts — {sample}", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    savefig(fig, outdir, "fig3_counts")


# ---------------- fig4: assignment ----------------
def fig_assignment(assign_path, sample, outdir):
    if not (assign_path and os.path.exists(assign_path)):
        print("WARN: no assignment_summary.csv — skipping fig4_assignment", flush=True)
        return
    import csv
    cats, vals = [], []
    with open(assign_path) as fh:
        rows = list(csv.reader(fh))
    # single-sample format: header ",reads" then category,value rows
    header = rows[0]
    if len(header) == 2 and header[1].strip() == "reads":
        for r in rows[1:]:
            if len(r) == 2:
                cats.append(r[0]); vals.append(float(r[1]))
    else:
        # multi-sample: take first sample row
        hdr = header
        first = rows[1]
        for k, v in zip(hdr[1:], first[1:]):
            cats.append(k); vals.append(float(v))
    label_map = {"assigned_to_genes": "Assigned", "no_feature": "No feature",
                 "ambiguous": "Ambiguous", "multimapping": "Multimapping", "unmapped": "Unmapped"}
    pretty = [label_map.get(c, c) for c in cats]
    colmap = {"Assigned": CB["green"], "No feature": CB["orange"], "Ambiguous": CB["yellow"],
              "Multimapping": CB["sky"], "Unmapped": CB["vermilion"]}
    cols = [colmap.get(p, CB["gray"]) for p in pretty]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    total = sum(vals)
    # bar
    ax = axes[0]
    bars = ax.bar(pretty, vals, color=cols)
    ax.set_ylabel("Reads"); ax.set_title("Read assignment"); ax.margins(y=0.15)
    ax.tick_params(axis="x", labelrotation=20)
    for b, v in zip(bars, vals):
        ax.text(b.get_x()+b.get_width()/2, v, f"{100*v/total:.1f}%", ha="center", va="bottom", fontsize=8)
    # donut (exclude unmapped to show feature-assignment among mapped)
    ax = axes[1]
    mapped = [(p, v) for p, v in zip(pretty, vals) if p != "Unmapped"]
    if mapped:
        wedges, _ = ax.pie([v for _, v in mapped], colors=[colmap.get(p, CB["gray"]) for p, _ in mapped],
                           startangle=90, wedgeprops=dict(width=0.4))
        ax.legend(wedges, [f"{p} ({v/1e3:.0f}k)" for p, v in mapped],
                  loc="center", fontsize=8, frameon=False)
        ax.set_title("Among mapped reads")
    fig.suptitle(f"Read assignment — {sample}", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    savefig(fig, outdir, "fig4_assignment")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-final")
    ap.add_argument("--reads-per-gene")
    ap.add_argument("--strandedness")
    ap.add_argument("--assignment")
    ap.add_argument("--gene-metadata")
    ap.add_argument("--fastqc-r1")
    ap.add_argument("--fastqc-r2")
    ap.add_argument("--sample-name", default="sample")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    col_idx, sel = selected_col_idx(args.strandedness)
    print(f"strandedness column: {sel} (idx {col_idx})", flush=True)

    fig_qc(args.fastqc_r1, args.fastqc_r2, args.sample_name, args.outdir)
    fig_alignment(args.log_final, args.sample_name, args.outdir)
    fig_counts(args.reads_per_gene, col_idx, args.gene_metadata, args.sample_name, args.outdir)
    fig_assignment(args.assignment, args.sample_name, args.outdir)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
