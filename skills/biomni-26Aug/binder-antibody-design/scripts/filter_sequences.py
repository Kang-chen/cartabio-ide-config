#!/usr/bin/env python3
"""
filter_sequences.py -- Parse ProteinMPNN FASTA outputs, score sequence quality,
apply pathology filters, and select a diverse top-N set of candidates for
structure-based validation (Boltz-2 / AF-multimer).

Stage 2 of the de novo binder design workflow (RFdiffusion -> ProteinMPNN -> Boltz-2).

WHAT IT DOES
  1. Reads every ProteinMPNN *.fa file in --seq-dir (one per backbone).
     The FIRST header/sequence pair in each file is the original RFdiffusion
     backbone sequence (poly-Gly / native), which is SKIPPED.
  2. Extracts the ProteinMPNN score (lower = better), sampling temperature, and
     sample index from each header (regex).
  3. Computes per-sequence complexity metrics:
       - Shannon entropy (bits) over amino-acid composition (higher = more diverse)
       - top1_frac: fraction of the single most frequent residue (lower = better)
       - n_cys: cysteine count (disulfide / oxidation liability if high)
  4. Optionally merges per-backbone interface-contact counts (from RFdiffusion
     backbone analysis JSON) so a minimum interface size can be required.
  5. Applies quality filters (defaults reproduce the validated PCSK9 preset):
       entropy >= 2.0, top1_frac <= 0.42, n_cys <= 2, interface_contacts >= 8
  6. Selects the best sequence per backbone, then the top-N backbones by score
     (topological diversity -- avoids N near-clones from one backbone).
  7. Writes a ranked all-sequences CSV, a selected-candidates CSV, and a FASTA
     of the selected candidates ready for structure prediction.

INPUT
  --seq-dir     Directory of ProteinMPNN FASTA files (outputs/seqs/*.fa).
  --backbone-json (optional) JSON mapping backbone-id -> {"contacts": int, ...}
                from the RFdiffusion backbone analysis. If given, interface_contacts
                is taken from here (keyed by design id); otherwise the filter is
                skipped unless --interface-json provides per-sequence contacts.

OUTPUT
  --out-csv         ranked table of ALL parsed sequences with metrics + pass flag
  --out-selected    CSV of the N selected candidates (default <out-csv stem>_selected.csv)
  --out-fasta       FASTA of selected candidates (default <out-csv stem>_selected.fasta)

EXAMPLE (PCSK9 worked example -- reproduces 120 parsed, 84 pass, 6 selected)
  python filter_sequences.py \
      --seq-dir /path/hpc_<mpnn_job>/outputs/seqs \
      --backbone-json /path/rfdiff/backbone_summary.json \
      --out-csv analysis/all_sequences.csv \
      --top-n 6

EXIT CODES
  0 success; 2 usage / input error (no files, no sequences, all filtered out).
"""
import argparse
import glob
import math
import os
import re
import sys
from collections import Counter

STD_AA = set("ACDEFGHIKLMNPQRSTVWY")

# Header regexes -- ProteinMPNN writes e.g.
#   >T=0.1, sample=3, score=0.8949, global_score=1.2345, seq_recovery=0.1234
RE_SCORE = re.compile(r"score=([\d.]+)")
RE_GLOBAL = re.compile(r"global_score=([\d.]+)")
RE_SAMPLE = re.compile(r"sample=(\d+)")
RE_TEMP = re.compile(r"T=([\d.]+)")


def die(msg, code=2):
    sys.stderr.write(f"[filter_sequences] ERROR: {msg}\n")
    sys.exit(code)


def shannon_entropy(seq):
    """Shannon entropy in bits over amino-acid composition."""
    L = len(seq)
    if L == 0:
        return 0.0
    counts = Counter(seq)
    return -sum((n / L) * math.log2(n / L) for n in counts.values())


def top1_fraction(seq):
    """Fraction of the single most frequent residue."""
    L = len(seq)
    if L == 0:
        return 1.0
    return Counter(seq).most_common(1)[0][1] / L


def parse_design_id(path):
    """Extract trailing integer backbone id from '..._<id>.fa'."""
    stem = os.path.basename(path).rsplit(".", 1)[0]
    m = re.search(r"(\d+)$", stem)
    return int(m.group(1)) if m else stem


def parse_fasta(path):
    """Return list of (header, seq) pairs from a ProteinMPNN FASTA."""
    entries = []
    header, chunks = None, []
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    entries.append((header, "".join(chunks)))
                header, chunks = line[1:], []
            else:
                chunks.append(line.strip())
    if header is not None:
        entries.append((header, "".join(chunks)))
    return entries


def main():
    ap = argparse.ArgumentParser(
        description="Filter + select ProteinMPNN designs for structure validation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--seq-dir", required=True,
                    help="Directory of ProteinMPNN FASTA outputs (*.fa).")
    ap.add_argument("--backbone-json", default=None,
                    help="RFdiffusion backbone analysis JSON: {design_id: {'contacts': int}}. "
                         "Provides interface_contacts per backbone if given.")
    ap.add_argument("--out-csv", required=True,
                    help="Output CSV of ALL parsed sequences with metrics.")
    ap.add_argument("--out-selected", default=None,
                    help="Output CSV of selected candidates (default: <out-csv stem>_selected.csv).")
    ap.add_argument("--out-fasta", default=None,
                    help="Output FASTA of selected candidates (default: <out-csv stem>_selected.fasta).")
    # Filter thresholds (defaults = validated PCSK9 preset)
    ap.add_argument("--min-entropy", type=float, default=2.0,
                    help="Minimum Shannon entropy (bits); rejects low-complexity/repetitive designs.")
    ap.add_argument("--max-top1-frac", type=float, default=0.42,
                    help="Maximum fraction of the single most frequent residue.")
    ap.add_argument("--max-cys", type=int, default=2,
                    help="Maximum cysteine count.")
    ap.add_argument("--min-contacts", type=int, default=8,
                    help="Minimum interface contacts (requires --backbone-json; skipped if unavailable).")
    ap.add_argument("--top-n", type=int, default=6,
                    help="Number of diverse candidates to select (best-per-backbone, top-N backbones).")
    ap.add_argument("--candidate-prefix", default="cand",
                    help="Prefix for selected candidate ids (cand1, cand2, ...).")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.seq_dir, "*.fa")) +
                   glob.glob(os.path.join(args.seq_dir, "*.fasta")))
    if not files:
        die(f"no FASTA files (*.fa/*.fasta) found in {args.seq_dir}")

    # Optional per-backbone interface contacts.
    contacts_by_design = {}
    if args.backbone_json:
        import json
        if not os.path.isfile(args.backbone_json):
            die(f"--backbone-json not found: {args.backbone_json}")
        bj = json.load(open(args.backbone_json))
        for k, v in bj.items():
            try:
                did = int(k)
            except (ValueError, TypeError):
                did = k
            if isinstance(v, dict):
                contacts_by_design[did] = v.get("contacts", v.get("interface_contacts"))
            else:
                contacts_by_design[did] = v

    rows = []
    for fa in files:
        design = parse_design_id(fa)
        entries = parse_fasta(fa)
        if len(entries) < 2:
            # only the original backbone entry -> no designs
            continue
        # SKIP first entry (original RFdiffusion backbone sequence)
        for header, seq in entries[1:]:
            seq = seq.upper()
            if not seq:
                continue
            m_s = RE_SCORE.search(header)
            m_g = RE_GLOBAL.search(header)
            m_sm = RE_SAMPLE.search(header)
            m_t = RE_TEMP.search(header)
            rows.append({
                "design": design,
                "temp": float(m_t.group(1)) if m_t else None,
                "sample": int(m_sm.group(1)) if m_sm else None,
                "length": len(seq),
                "mpnn_score": float(m_s.group(1)) if m_s else None,
                "global_score": float(m_g.group(1)) if m_g else None,
                "entropy": round(shannon_entropy(seq), 3),
                "top1_frac": round(top1_fraction(seq), 3),
                "n_cys": seq.count("C"),
                "interface_contacts": contacts_by_design.get(design),
                "seq": seq,
            })

    if not rows:
        die("no designed sequences parsed (all files had <2 entries?).")

    # Lazy import pandas so --help works without it.
    try:
        import pandas as pd
    except ImportError:
        die("pandas is required (pip install pandas).")

    df = pd.DataFrame(rows)

    # Apply filters. Interface filter only if contacts are available.
    have_contacts = df["interface_contacts"].notna().any()
    passf = (
        (df["entropy"] >= args.min_entropy) &
        (df["top1_frac"] <= args.max_top1_frac) &
        (df["n_cys"] <= args.max_cys)
    )
    if have_contacts:
        passf = passf & (df["interface_contacts"].fillna(-1) >= args.min_contacts)
    else:
        sys.stderr.write("[filter_sequences] NOTE: no interface_contacts available; "
                         "skipping --min-contacts filter.\n")
    df["pass"] = passf

    n_total, n_pass = len(df), int(passf.sum())
    df = df.sort_values("mpnn_score", na_position="last").reset_index(drop=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out_csv)) or ".", exist_ok=True)
    df.to_csv(args.out_csv, index=False)

    if n_pass == 0:
        die(f"0 of {n_total} sequences passed filters; loosen thresholds "
            f"(--min-entropy/--max-top1-frac/--max-cys/--min-contacts).")

    # Selection: best sequence per backbone, then top-N backbones by score.
    df_pass = df[df["pass"]].copy()
    best_per_backbone = (df_pass.sort_values("mpnn_score")
                                 .groupby("design", as_index=False)
                                 .first())
    selected = best_per_backbone.sort_values("mpnn_score").head(args.top_n).reset_index(drop=True)
    selected.insert(0, "candidate", [f"{args.candidate_prefix}{i+1}" for i in range(len(selected))])

    out_selected = args.out_selected or (os.path.splitext(args.out_csv)[0] + "_selected.csv")
    out_fasta = args.out_fasta or (os.path.splitext(args.out_csv)[0] + "_selected.fasta")
    selected.to_csv(out_selected, index=False)
    with open(out_fasta, "w") as fh:
        for _, r in selected.iterrows():
            fh.write(f">{r['candidate']} design={r['design']} mpnn_score={r['mpnn_score']} len={r['length']}\n")
            fh.write(r["seq"] + "\n")

    # Report.
    print(f"[filter_sequences] parsed {n_total} designed sequences from {len(files)} backbone file(s).")
    print(f"[filter_sequences] {n_pass}/{n_total} passed filters "
          f"(entropy>={args.min_entropy}, top1_frac<={args.max_top1_frac}, "
          f"n_cys<={args.max_cys}"
          + (f", contacts>={args.min_contacts}" if have_contacts else "") + ").")
    print(f"[filter_sequences] selected {len(selected)} diverse candidates "
          f"(best-per-backbone, top-{args.top_n} backbones):")
    for _, r in selected.iterrows():
        print(f"    {r['candidate']}: design {r['design']}, len {r['length']}, "
              f"MPNN {r['mpnn_score']:.4f}, entropy {r['entropy']}, "
              f"contacts {r['interface_contacts']}")
    print(f"[filter_sequences] wrote:\n    {args.out_csv}\n    {out_selected}\n    {out_fasta}")


if __name__ == "__main__":
    main()
