#!/usr/bin/env python3
"""
detect_strandedness_build_matrix.py

Turn one or more STAR `--quantMode GeneCounts` ReadsPerGene.out.tab files into a
DE-ready integer gene x sample count matrix, auto-detecting library strandedness
from the STAR count columns and attaching gene biotype/coordinate metadata parsed
from the GTF.

STAR ReadsPerGene.out.tab columns (tab-separated):
  col1 = gene_id
  col2 = counts, UNSTRANDED protocol
  col3 = counts, stranded FORWARD (read1 same strand as gene; e.g. 'yes'/fr-secondstrand)
  col4 = counts, stranded REVERSE (read1 opposite strand; e.g. 'reverse'/fr-firststrand, dUTP)
The first 4 lines are special summary rows:
  N_unmapped, N_multimapping, N_noFeature, N_ambiguous  -> excluded from the matrix.

Strandedness rule (per sample, on gene-assigned reads only):
  frac_fwd = sum(col3) / (sum(col3) + sum(col4))
  frac_fwd >= 0.80            -> 'forward'  (use col3)
  frac_fwd <= 0.20            -> 'reverse'  (use col4)
  otherwise                   -> 'unstranded' (use col2)
The 0.80/0.20 cut is the standard heuristic; genuinely stranded libraries land
near 0.95+/0.05-, unstranded near 0.50. With too few assigned reads the call is
unreliable (see --min-assigned).

Usage:
  # single sample
  python detect_strandedness_build_matrix.py \
      --reads-per-gene SAMPLE_ReadsPerGene.out.tab \
      --gtf annotation.gtf \
      --outdir /mnt/results/<run> \
      [--sample-name SAMPLE] [--strandedness auto|unstranded|forward|reverse]

  # multiple samples -> one merged matrix (pass repeated --reads-per-gene, or a
  # comma list of name=path pairs via --samples)
  python detect_strandedness_build_matrix.py \
      --samples S1=S1_ReadsPerGene.out.tab,S2=S2_ReadsPerGene.out.tab \
      --gtf annotation.gtf --outdir /mnt/results/<run>

Outputs (in --outdir):
  counts_matrix.tsv       integer gene x sample matrix (gene_id + one col per sample); DESeq2/edgeR input
  strandedness.json       per-sample strandedness call + evidence
  assignment_summary.csv  per-sample assigned/no_feature/ambiguous/multimapping/unmapped
  gene_metadata.csv       gene_id -> gene_name, gene_biotype, chrom, start, end, strand (if --gtf)
"""
import argparse, json, os, re, sys
from collections import OrderedDict

SPECIAL_ROWS = {"N_unmapped", "N_multimapping", "N_noFeature", "N_ambiguous"}


def parse_reads_per_gene(path):
    """Return (gene_order, {gene_id: (c2,c3,c4)}, {special_row: (c2,c3,c4)})."""
    genes = OrderedDict()
    special = {}
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            gid = parts[0]
            try:
                c2, c3, c4 = int(parts[1]), int(parts[2]), int(parts[3])
            except ValueError:
                continue
            if gid in SPECIAL_ROWS:
                special[gid] = (c2, c3, c4)
            else:
                genes[gid] = (c2, c3, c4)
    if not genes:
        sys.exit(f"ERROR: no gene rows parsed from {path} — is this a STAR ReadsPerGene.out.tab?")
    return genes, special


def call_strandedness(genes, forced="auto"):
    sum2 = sum(v[0] for v in genes.values())
    sum3 = sum(v[1] for v in genes.values())
    sum4 = sum(v[2] for v in genes.values())
    denom = sum3 + sum4
    frac_fwd = (sum3 / denom) if denom else 0.0
    frac_rev = (sum4 / denom) if denom else 0.0
    if forced != "auto":
        protocol = forced
    elif frac_fwd >= 0.80:
        protocol = "forward"
    elif frac_fwd <= 0.20:
        protocol = "reverse"
    else:
        protocol = "unstranded"
    col_idx = {"unstranded": 0, "forward": 1, "reverse": 2}[protocol]
    return {
        "sum_unstranded_col2": sum2,
        "sum_forward_col3": sum3,
        "sum_reverse_col4": sum4,
        "fraction_forward": round(frac_fwd, 4),
        "fraction_reverse": round(frac_rev, 4),
        "detected_protocol": protocol,
        "selected_column": protocol,
        "_col_idx": col_idx,
    }


def parse_gtf_gene_metadata(gtf_path):
    """gene_id -> dict(gene_name, gene_biotype, chrom, start, end, strand) from `gene` lines."""
    gid_re = re.compile(r'gene_id "([^"]+)"')
    name_re = re.compile(r'gene_name "([^"]+)"')
    # Ensembl uses gene_biotype; GENCODE uses gene_type — accept both.
    biotype_re = re.compile(r'gene_(?:biotype|type) "([^"]+)"')
    meta = {}
    opener = open
    if gtf_path.endswith(".gz"):
        import gzip
        opener = gzip.open
    with opener(gtf_path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 9 or f[2] != "gene":
                continue
            attrs = f[8]
            m = gid_re.search(attrs)
            if not m:
                continue
            gid = m.group(1)
            nm = name_re.search(attrs)
            bt = biotype_re.search(attrs)
            meta[gid] = {
                "gene_name": nm.group(1) if nm else "",
                "gene_biotype": bt.group(1) if bt else "",
                "chrom": f[0], "start": f[3], "end": f[4], "strand": f[6],
            }
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reads-per-gene", action="append", default=[],
                    help="Path to a STAR ReadsPerGene.out.tab (repeatable for multiple samples).")
    ap.add_argument("--samples", default=None,
                    help="Comma-separated name=path pairs, e.g. S1=a.tab,S2=b.tab (alternative to --reads-per-gene).")
    ap.add_argument("--sample-name", action="append", default=[],
                    help="Sample name for each --reads-per-gene (order matches). Defaults to filename stem.")
    ap.add_argument("--gtf", default=None, help="GTF used for alignment (for gene metadata). Optional but recommended.")
    ap.add_argument("--strandedness", default="auto", choices=["auto", "unstranded", "forward", "reverse"],
                    help="Force a protocol or auto-detect per sample (default: auto).")
    ap.add_argument("--min-assigned", type=int, default=10000,
                    help="Warn if a sample has fewer assigned reads than this (strandedness call unreliable).")
    ap.add_argument("--outdir", required=True, help="Output directory (e.g. /mnt/results/<run>).")
    args = ap.parse_args()

    # Assemble the sample -> path map.
    sample_map = OrderedDict()
    if args.samples:
        for pair in args.samples.split(","):
            if "=" not in pair:
                sys.exit(f"ERROR: --samples entry '{pair}' must be name=path")
            name, path = pair.split("=", 1)
            sample_map[name.strip()] = path.strip()
    for i, path in enumerate(args.reads_per_gene):
        if i < len(args.sample_name):
            name = args.sample_name[i]
        else:
            name = os.path.basename(path).replace("_ReadsPerGene.out.tab", "").replace(".tab", "")
        sample_map[name] = path
    if not sample_map:
        sys.exit("ERROR: provide at least one --reads-per-gene or --samples entry.")

    os.makedirs(args.outdir, exist_ok=True)

    per_sample = OrderedDict()   # name -> (genes, special, strand_info)
    for name, path in sample_map.items():
        if not os.path.exists(path):
            sys.exit(f"ERROR: file not found for sample {name}: {path}")
        genes, special = parse_reads_per_gene(path)
        strand = call_strandedness(genes, forced=args.strandedness)
        per_sample[name] = (genes, special, strand)
        assigned = strand["sum_" + {0: "unstranded_col2", 1: "forward_col3", 2: "reverse_col4"}[strand["_col_idx"]]]
        flag = "  [WARN: few assigned reads; strandedness call may be unreliable]" if assigned < args.min_assigned else ""
        print(f"[{name}] protocol={strand['detected_protocol']} "
              f"frac_fwd={strand['fraction_forward']} assigned={assigned}{flag}", flush=True)

    # Union of gene ids, preserving first-seen order (STAR emits identical gene order per index).
    gene_order = []
    seen = set()
    for name, (genes, _s, _st) in per_sample.items():
        for gid in genes:
            if gid not in seen:
                seen.add(gid)
                gene_order.append(gid)

    # Write count matrix using each sample's selected column.
    matrix_path = os.path.join(args.outdir, "counts_matrix.tsv")
    sample_names = list(per_sample.keys())
    with open(matrix_path, "w") as out:
        out.write("gene_id\t" + "\t".join(sample_names) + "\n")
        for gid in gene_order:
            row = [gid]
            for name in sample_names:
                genes, _s, st = per_sample[name]
                v = genes.get(gid, (0, 0, 0))
                row.append(str(v[st["_col_idx"]]))
            out.write("\t".join(row) + "\n")

    # strandedness.json (drop private _col_idx)
    strand_out = {}
    for name, (_g, _s, st) in per_sample.items():
        clean = {k: v for k, v in st.items() if not k.startswith("_")}
        clean = {"sample": name, **clean}
        strand_out[name] = clean
    strand_json = list(strand_out.values())[0] if len(strand_out) == 1 else strand_out
    with open(os.path.join(args.outdir, "strandedness.json"), "w") as fh:
        json.dump(strand_json, fh, indent=2)

    # assignment_summary.csv (per sample). "assigned" uses the selected column's gene sum.
    with open(os.path.join(args.outdir, "assignment_summary.csv"), "w") as fh:
        if len(per_sample) == 1:
            fh.write(",reads\n")
            name = sample_names[0]
            genes, special, st = per_sample[name]
            assigned = sum(v[st["_col_idx"]] for v in genes.values())
            no_feature = special.get("N_noFeature", (0, 0, 0))[st["_col_idx"]]
            ambiguous = special.get("N_ambiguous", (0, 0, 0))[st["_col_idx"]]
            multimap = special.get("N_multimapping", (0, 0, 0))[st["_col_idx"]]
            unmapped = special.get("N_unmapped", (0, 0, 0))[st["_col_idx"]]
            fh.write(f"assigned_to_genes,{assigned}\n")
            fh.write(f"no_feature,{no_feature}\n")
            fh.write(f"ambiguous,{ambiguous}\n")
            fh.write(f"multimapping,{multimap}\n")
            fh.write(f"unmapped,{unmapped}\n")
        else:
            fh.write("sample,assigned_to_genes,no_feature,ambiguous,multimapping,unmapped\n")
            for name in sample_names:
                genes, special, st = per_sample[name]
                ci = st["_col_idx"]
                assigned = sum(v[ci] for v in genes.values())
                fh.write(f"{name},{assigned},"
                         f"{special.get('N_noFeature',(0,0,0))[ci]},"
                         f"{special.get('N_ambiguous',(0,0,0))[ci]},"
                         f"{special.get('N_multimapping',(0,0,0))[ci]},"
                         f"{special.get('N_unmapped',(0,0,0))[ci]}\n")

    # gene_metadata.csv
    n_detected = None
    if args.gtf:
        meta = parse_gtf_gene_metadata(args.gtf)
        meta_path = os.path.join(args.outdir, "gene_metadata.csv")
        with open(meta_path, "w") as fh:
            fh.write("gene_id,gene_name,gene_biotype,chrom,start,end,strand\n")
            for gid in gene_order:
                m = meta.get(gid, {})
                def esc(x):
                    x = str(x)
                    return '"' + x + '"' if ("," in x or '"' in x) else x
                fh.write(",".join([gid, esc(m.get("gene_name", "")), esc(m.get("gene_biotype", "")),
                                   m.get("chrom", ""), m.get("start", ""), m.get("end", ""),
                                   m.get("strand", "")]) + "\n")

    # Detection summary: genes with >0 in any sample.
    n_detected = sum(1 for gid in gene_order
                     if any(per_sample[n][0].get(gid, (0, 0, 0))[per_sample[n][2]["_col_idx"]] > 0
                            for n in sample_names))
    print(f"\nWrote {matrix_path}", flush=True)
    print(f"  genes (rows): {len(gene_order)}  |  detected (>0 in any sample): {n_detected}  |  samples: {len(sample_names)}", flush=True)


if __name__ == "__main__":
    main()
