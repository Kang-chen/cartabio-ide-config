#!/usr/bin/env python3
"""
Read-driven pooled-CRISPR-screen library reconstruction.

Recovers the guide-to-gene table and non-targeting controls (NTCs) directly from
raw FASTQ + a public parent library (e.g., Brunello), for screens whose guide
table is paywalled/missing. See references/read_driven_library_reconstruction.md.

Outputs a MAGeCK library CSV (sgRNA,sequence,gene; NO header), an ntc_guides.txt,
and RC-corrected FASTQ containing bare 20-nt spacers in reference orientation.

Usage:
    python reconstruct_library.py \
        --brunello broadgpp-brunello-library.txt \
        --fastq D1_Div.fastq.gz D1_NonDiv.fastq.gz D2_Div.fastq.gz D2_NonDiv.fastq.gz \
        --labels D1_Div D1_NonDiv D2_Div D2_NonDiv \
        --outdir /workspace/screen/mageck \
        --procdir /workspace/screen/fastq_proc \
        --anchor GCTCTTAAAC --n-ntc 48 --min-count 10 --min-guides 3
"""
import os, gzip, argparse, collections

def rc(s):
    return s.translate(str.maketrans("ACGTN", "TGCAN"))[::-1]

def read_fastq_seqs(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as f:
        for i, line in enumerate(f):
            if i % 4 == 1:
                yield line.strip()

def load_brunello(path):
    """Return dict spacer->gene. Auto-detects the two needed columns."""
    import csv
    spacer2gene = {}
    with open(path) as f:
        rdr = csv.reader(f, delimiter="\t")
        header = next(rdr)
        # find columns by name (robust to column order)
        sp_col = next(i for i, h in enumerate(header) if "sequence" in h.lower() and "target" in h.lower())
        # Prefer "Target Gene Symbol" over "Target Gene ID": both contain
        # "gene", so match "symbol" first and fall back to a non-ID gene column.
        gn_col = next(
            (i for i, h in enumerate(header) if "symbol" in h.lower()),
            next(i for i, h in enumerate(header) if "gene" in h.lower() and "id" not in h.lower()),
        )
        for row in rdr:
            if len(row) > max(sp_col, gn_col):
                spacer2gene[row[sp_col].upper().strip()] = row[gn_col].strip()
    return spacer2gene

def extract(seq, anchor):
    i = seq.find(anchor)
    if i < 0:
        return None
    sp = seq[i+len(anchor): i+len(anchor)+20]
    return sp if len(sp) == 20 else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brunello", required=True)
    ap.add_argument("--fastq", nargs="+", required=True)
    ap.add_argument("--labels", nargs="+", required=True)
    ap.add_argument("--outdir", default="/workspace/screen/mageck")
    ap.add_argument("--procdir", default="/workspace/screen/fastq_proc")
    ap.add_argument("--anchor", default="GCTCTTAAAC")
    ap.add_argument("--n-ntc", type=int, default=48)
    ap.add_argument("--min-count", type=int, default=10)
    ap.add_argument("--min-guides", type=int, default=3)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True); os.makedirs(args.procdir, exist_ok=True)

    spacer2gene = load_brunello(args.brunello)
    print(f"Brunello: {len(spacer2gene)} spacers, {len(set(spacer2gene.values()))} genes")

    # ---- Orientation check on a sample of the first file
    sample = []
    for k, sp in enumerate(read_fastq_seqs(args.fastq[0])):
        e = extract(sp, args.anchor)
        if e:
            sample.append(e)
        if len(sample) >= 50000:
            break
    fwd = sum(s in spacer2gene for s in sample)
    rev = sum(rc(s) in spacer2gene for s in sample)
    use_rc = rev > fwd
    print(f"Orientation: fwd={fwd} rev={rev} -> use_rc={use_rc}")

    def to_ref(sp):
        return rc(sp) if use_rc else sp

    # ---- Tally pooled counts + write RC-corrected FASTQ
    pooled = collections.Counter()
    for path, lab in zip(args.fastq, args.labels):
        outp = os.path.join(args.procdir, f"{lab}.fastq.gz")
        n_in = n_out = 0
        with gzip.open(outp, "wt") as out:
            op = gzip.open if path.endswith(".gz") else open
            with op(path, "rt") as f:
                while True:
                    h = f.readline()
                    if not h:
                        break
                    s = f.readline().strip(); f.readline(); q = f.readline().strip()
                    n_in += 1
                    e = extract(s, args.anchor)
                    if not e:
                        continue
                    ref = to_ref(e)
                    pooled[ref] += 1
                    out.write(f"{h.strip()}\n{ref}\n+\n{'I'*20}\n")
                    n_out += 1
        print(f"{lab}: kept {n_out}/{n_in} ({100*n_out/n_in:.1f}%) -> {outp}")

    # ---- Determine pilot genes: >= min-guides observed at count >= min-count
    gene_guides = collections.defaultdict(set)
    for sp, ct in pooled.items():
        if sp in spacer2gene and ct >= args.min_count:
            gene_guides[spacer2gene[sp]].add(sp)
    pilot_genes = {g for g, gs in gene_guides.items() if len(gs) >= args.min_guides}
    print(f"Pilot genes: {len(pilot_genes)}")

    # take ALL parent-library guides for pilot genes (non-circular)
    gene_all_guides = collections.defaultdict(list)
    for sp, g in spacer2gene.items():
        if g in pilot_genes:
            gene_all_guides[g].append(sp)

    # ---- Recover NTCs: top non-parent spacers by pooled count (U6 G-start block)
    non_lib = [(sp, ct) for sp, ct in pooled.items() if sp not in spacer2gene]
    non_lib.sort(key=lambda x: -x[1])
    ntc = non_lib[:args.n_ntc]
    g_frac = sum(1 for sp, _ in ntc if sp.startswith("G")) / max(len(ntc), 1)
    print(f"NTCs: {len(ntc)}, G-start fraction={g_frac:.2f} (expect ~1.0)")

    # ---- Write MAGeCK library + NTC list
    lib_path = os.path.join(args.outdir, "pilot_library.csv")
    ntc_path = os.path.join(args.outdir, "ntc_guides.txt")
    with open(lib_path, "w") as f, open(ntc_path, "w") as nf:
        for g in sorted(gene_all_guides):
            for n, sp in enumerate(sorted(gene_all_guides[g]), 1):
                f.write(f"{g}_{n},{sp},{g}\n")
        for n, (sp, _) in enumerate(ntc, 1):
            gid = f"Non_Targeting_Control_{n:02d}"
            f.write(f"{gid},{sp},Non_Targeting_Control\n")
            nf.write(gid + "\n")
    n_targ = sum(len(v) for v in gene_all_guides.values())
    print(f"Library: {n_targ} targeting + {len(ntc)} NTC = {n_targ+len(ntc)} guides -> {lib_path}")
    print(f"NTC list -> {ntc_path}")

if __name__ == "__main__":
    main()
