#!/usr/bin/env python3
"""
prepare_target.py — Prepare a folded protein target for de novo binder design.

Crops a PDB/CIF structure to a chosen chain and (optional) residue range, removes
prodomain segments, all heteroatoms, and water, verifies that user-specified key
residues (e.g. a catalytic triad or functional-site residues) survive the crop, and
emits an RFdiffusion contig string that honors internal unmodeled-loop gaps.

Why this matters: RFdiffusion conditions on the exact residues present in the input
PDB. If unmodeled loops are not split into separate contig segments, or if the target
numbering is silently wrong, the generated interface is meaningless. This script fails
loudly rather than producing a subtly broken target.

Example (PCSK9 catalytic domain, the worked example):
    python prepare_target.py \
        --in 2p4e.pdb --chain A --range 155-461 --margin 10 \
        --key-residues 186,226,386 \
        --binder-len 60-90 \
        --out target_pcsk9_cat.pdb --contig-out contig.txt
"""
import argparse, sys, gzip, os
from collections import OrderedDict

def eprint(*a):
    print(*a, file=sys.stderr)

def die(msg, code=2):
    eprint(f"[prepare_target] ERROR: {msg}")
    sys.exit(code)

def parse_args():
    p = argparse.ArgumentParser(description="Prepare a protein target for RFdiffusion binder design.")
    p.add_argument("--in", dest="inp", required=True, help="Input .pdb/.cif (optionally .gz).")
    p.add_argument("--chain", required=True, help="Target chain ID to keep (e.g. A).")
    p.add_argument("--range", default=None,
                   help="Core residue range to keep as start-end (e.g. 155-461). Omit to keep the whole chain.")
    p.add_argument("--margin", type=float, default=0,
                   help="Margin added around --range. Interpreted per --margin-mode. Default 0.")
    p.add_argument("--margin-mode", choices=["spatial", "sequence"], default="spatial",
                   help="'spatial' (recommended): also keep any chain residue with a heavy atom within "
                        "--margin Angstroms of the core range (preserves pocket-shaping residues, may be "
                        "non-contiguous). 'sequence': extend the core range by +/- int(margin) residue "
                        "numbers. Default spatial.")
    p.add_argument("--key-residues", default="",
                   help="Comma-separated residue numbers that MUST be present after cropping "
                        "(e.g. catalytic triad 186,226,386). Script exits non-zero if any are missing.")
    p.add_argument("--hotspots", default="",
                   help="Comma-separated residue numbers of declared epitope hotspots (the same set "
                        "passed to analyze_interface.py --hotspots and to ppi.hotspot_res). Recorded "
                        "in the construct_scope sidecar so downstream tools can cross-check. Distinct "
                        "from --key-residues (hotspots = epitope-bias set; key-residues = must-survive-crop set).")
    p.add_argument("--binder-len", default="60-90",
                   help="Binder length range lo-hi appended to the contig (default 60-90).")
    p.add_argument("--min-segment", type=int, default=4,
                   help="Drop contiguous kept-residue runs shorter than this many residues "
                        "(spatial mode can pull in scattered singletons that make a poor contig). "
                        "Runs containing a --key-residue are ALWAYS kept. Set 1 to keep everything. Default 4.")
    p.add_argument("--out", required=True, help="Output cropped PDB path.")
    p.add_argument("--contig-out", default=None, help="Optional path to write the RFdiffusion contig string.")
    p.add_argument("--scope-out", default=None,
                   help="Optional path to write a construct_scope JSON sidecar (domain_start, "
                        "target_range, core_range, margin, margin_mode, min_segment, is_truncated, "
                        "hotspots, hotspots_present_after_crop, hotspots_dropped_by_crop). It is the "
                        "single source of the construct span AND of the crop parameters that produced "
                        "it, so build_report.py can derive the range and explain nominal-vs-realised "
                        "instead of restating a hand-typed value.")
    return p.parse_args()

def load_structure(path):
    """Parse with Biopython; supports .pdb/.cif and .gz. Returns (structure, is_cif)."""
    from Bio.PDB import PDBParser, MMCIFParser
    opener = gzip.open if path.endswith(".gz") else open
    base = path[:-3] if path.endswith(".gz") else path
    is_cif = base.lower().endswith((".cif", ".mmcif"))
    parser = MMCIFParser(QUIET=True) if is_cif else PDBParser(QUIET=True)
    mode = "rt"
    with opener(path, mode) as fh:
        structure = parser.get_structure("target", fh)
    return structure, is_cif

def is_water(resname):
    return resname.strip() in ("HOH", "WAT", "DOD")

def main():
    args = parse_args()
    if not os.path.exists(args.inp):
        die(f"input file not found: {args.inp}")

    core_lo = core_hi = None
    lo = hi = None
    if args.range:
        try:
            core_lo, core_hi = (int(x) for x in args.range.split("-"))
        except Exception:
            die(f"--range must look like 155-461, got {args.range!r}")
        lo, hi = core_lo, core_hi
        if args.margin_mode == "sequence":
            lo -= int(args.margin)
            hi += int(args.margin)

    key_res = set()
    if args.key_residues.strip():
        try:
            key_res = {int(x) for x in args.key_residues.split(",") if x.strip() != ""}
        except Exception:
            die(f"--key-residues must be comma-separated integers, got {args.key_residues!r}")

    hotspot_res = set()
    if args.hotspots.strip():
        try:
            hotspot_res = {int(x) for x in args.hotspots.split(",") if x.strip() != ""}
        except Exception:
            die(f"--hotspots must be comma-separated integers, got {args.hotspots!r}")

    structure, _ = load_structure(args.inp)
    model = next(iter(structure))  # first model

    if args.chain not in [c.id for c in model]:
        die(f"chain {args.chain!r} not in structure; chains present: {[c.id for c in model]}")
    chain = model[args.chain]

    # Standard amino-acid residues of the chain, skipping hetero/water/hydrogens.
    from Bio.PDB.Polypeptide import is_aa
    aa_residues = []
    for res in chain:
        hetflag, resseq, icode = res.id
        if hetflag.strip() != "":      # HETATM (ligand, ion) — drop
            continue
        if is_water(res.resname):      # water — drop
            continue
        if not is_aa(res, standard=False):
            continue
        aa_residues.append(res)

    if not aa_residues:
        die("no standard amino-acid residues found in the chosen chain.")

    # Record the full chain residue numbers before any cropping (for truncation detection).
    full_chain_nums = sorted(r.id[1] for r in aa_residues)

    def in_core(res):
        return core_lo is None or (core_lo <= res.id[1] <= core_hi)

    if lo is None:
        # whole chain
        kept_residues = list(aa_residues)
    elif args.margin_mode == "sequence":
        kept_residues = [r for r in aa_residues if lo <= r.id[1] <= hi]
    else:  # spatial: core residues + residues with any heavy atom within `margin` A of core
        import numpy as np
        core_atoms = [a.coord for r in aa_residues if in_core(r) for a in r if a.element != "H"]
        if not core_atoms:
            die(f"no atoms found in core range {core_lo}-{core_hi}; check --range/--chain.")
        core_atoms = np.asarray(core_atoms)
        try:
            from scipy.spatial import cKDTree
            tree = cKDTree(core_atoms)
            kept_residues = []
            for r in aa_residues:
                if in_core(r):
                    kept_residues.append(r); continue
                if args.margin > 0:
                    coords = np.asarray([a.coord for a in r if a.element != "H"])
                    if len(coords) and tree.query(coords, distance_upper_bound=args.margin)[0].min() <= args.margin:
                        kept_residues.append(r)
        except ImportError:
            # Fallback: O(n^2) max-distance loop so a missing scipy never blocks Step 0.
            print("[prepare_target] NOTE: scipy not available; using O(n^2) distance fallback.")
            kept_residues = []
            for r in aa_residues:
                if in_core(r):
                    kept_residues.append(r); continue
                if args.margin > 0:
                    coords = np.asarray([a.coord for a in r if a.element != "H"])
                    if len(coords):
                        # min distance from any atom in this residue to any core atom
                        dmin = float("inf")
                        for c in coords:
                            diffs = core_atoms - c
                            d = np.sqrt((diffs * diffs).sum(axis=1)).min()
                            if d < dmin:
                                dmin = d
                        if dmin <= args.margin:
                            kept_residues.append(r)

    kept_residues.sort(key=lambda r: r.id[1])
    kept_resnums = [r.id[1] for r in kept_residues]

    if not kept_residues:
        die("no residues kept after cropping — check --chain and --range.")

    # Verify key residues present
    missing = sorted(r for r in key_res if r not in set(kept_resnums))
    if missing:
        die(f"required key residues missing after crop: {missing}. "
            f"Kept span {min(kept_resnums)}-{max(kept_resnums)}.")

    # Split kept_resnums into contiguous runs (gaps = unmodeled loops or spatial-margin breaks).
    def to_runs(nums):
        runs = []
        start = prev = nums[0]
        for n in nums[1:]:
            if n == prev + 1:
                prev = n
            else:
                runs.append((start, prev)); start = prev = n
        runs.append((start, prev))
        return runs

    runs = to_runs(kept_resnums)

    # Prune short isolated runs (bad for RFdiffusion) unless they carry a key residue.
    if args.min_segment > 1:
        pruned = [(a, b) for (a, b) in runs
                  if (b - a + 1) >= args.min_segment or any(a <= k <= b for k in key_res)]
        dropped = [(a, b) for (a, b) in runs if (a, b) not in pruned]
        if dropped:
            print(f"[prepare_target] dropped {len(dropped)} short run(s) < {args.min_segment} aa: {dropped}")
        if not pruned:
            die("all kept runs were shorter than --min-segment; lower --min-segment or widen --range.")
        runs = pruned
        keep_nums = {n for (a, b) in runs for n in range(a, b + 1)}
        kept_residues = [r for r in kept_residues if r.id[1] in keep_nums]
        kept_resnums = [r.id[1] for r in kept_residues]

    # Re-verify key residues survived pruning.
    missing = sorted(r for r in key_res if r not in set(kept_resnums))
    if missing:
        die(f"key residues dropped during segment pruning: {missing} (should not happen).")

    # Write cropped structure (PDB): surviving residues of the chosen chain, no H.
    from Bio.PDB import PDBIO, Select
    keep_ids = {res.id for res in kept_residues}
    target_chain_id = args.chain
    class Keep(Select):
        def accept_chain(self, c):
            return c.id == target_chain_id
        def accept_residue(self, r):
            return r.id in keep_ids
        def accept_atom(self, a):
            return a.element != "H"
    io = PDBIO()
    io.set_structure(structure)
    io.save(args.out, select=Keep())

    seg = "/".join(f"{args.chain}{a}-{b}" for a, b in runs)
    contig = f"[{seg}/0 {args.binder_len}]"

    n_gaps = len(runs) - 1
    print(f"[prepare_target] chain {args.chain}: kept {len(kept_resnums)} residues, "
          f"span {min(kept_resnums)}-{max(kept_resnums)}, {n_gaps} internal gap(s).")
    if key_res:
        print(f"[prepare_target] key residues present: {sorted(key_res)}")
    print(f"[prepare_target] contiguous segments: {runs}")
    print(f"[prepare_target] RFdiffusion contig: contigmap.contigs={contig}")
    print(f"[prepare_target] wrote target PDB: {args.out} ({os.path.getsize(args.out)} bytes)")

    if args.contig_out:
        with open(args.contig_out, "w") as fh:
            fh.write(contig + "\n")
        print(f"[prepare_target] wrote contig to {args.contig_out}")

    # --- construct scope sidecar ---
    # Truncation is detected from the residues actually kept, however the crop was
    # specified (spatial margin or --min-segment pruning can drop residues with no
    # --range given). This closes the loop for build_report.py's consistency-gate check.
    if args.scope_out:
        import json
        kept_set = set(kept_resnums)
        full_set = set(full_chain_nums)
        is_truncated = full_chain_nums != kept_resnums
        domain_start = min(kept_resnums) if kept_resnums else None
        target_range = f"{min(kept_resnums)}-{max(kept_resnums)}" if kept_resnums else None
        hs_sorted = sorted(hotspot_res)
        hs_present = sorted(hotspot_res & kept_set)
        hs_dropped = sorted(hotspot_res - kept_set)
        scope = {
            "domain_start": domain_start,
            "target_range": target_range,
            "core_range": args.range if args.range else None,
            "margin": args.margin,
            "margin_mode": args.margin_mode,
            "min_segment": args.min_segment,
            "is_truncated": is_truncated,
            "hotspots": hs_sorted,
            "hotspots_present_after_crop": hs_present,
            "hotspots_dropped_by_crop": hs_dropped,
            "kept_residues": kept_resnums,
            "full_chain_residues": full_chain_nums,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.scope_out)) or ".", exist_ok=True)
        with open(args.scope_out, "w") as fh:
            json.dump(scope, fh, indent=1)
        print(f"[prepare_target] wrote construct scope to {args.scope_out}")
        if is_truncated:
            print(f"[prepare_target] truncated-construct disclosure: {len(full_chain_nums)} chain residues "
                  f"-> {len(kept_resnums)} kept (dropped {len(full_set - kept_set)}).")
        if hs_dropped:
            print(f"[prepare_target] WARNING: {len(hs_dropped)} hotspot(s) dropped by crop: {hs_dropped}")

if __name__ == "__main__":
    main()
