#!/usr/bin/env python3
"""
analyze_interface.py -- Parse Boltz-2 (or AF-multimer-style) co-folding predictions
for a two-chain binder:target complex, compute interface metrics + confidence, map
the epitope onto NATIVE target numbering, and rank candidates.

Stage 3 of the de novo binder design workflow (RFdiffusion -> ProteinMPNN -> Boltz-2).

FOR EACH CANDIDATE it reads a Boltz results directory:
    <boltz_out>/predictions/<cid>/
        <cid>_model_0.cif              (predicted complex; chain A=binder, B=target)
        confidence_<cid>_model_0.json  (iptm, ptm, complex_plddt, ...)
        pae_<cid>_model_0.npz          (PAE matrix, (L,L))
        plddt_<cid>_model_0.npz        (per-residue pLDDT, (L,))
and computes:
    - iptm, ptm, complex_plddt            (from confidence JSON)
    - heavy-atom interface contacts        (element != H, distance <= --contact-cut, default 4.5 A)
    - binder interface residues            (chain-A residues with >=1 contact; 1-based positions)
    - target interface residues            (chain-B residues with >=1 contact; NATIVE numbering)
    - interface PAE                        (mean PAE over binder-res x target-res block, both directions)
    - binder interface pLDDT               (mean pLDDT over binder interface residues)
    - epitope overlap flags                (vs --functional-site and --catalytic-triad residue sets)

NATIVE NUMBERING
    Boltz renumbers the target chain 1..N. To recover native residue numbers, pass
    --domain-start S: native = S + (chain-B 0-based index). For the PCSK9 catalytic
    domain worked example S=155 (chain-B index 0 -> residue 155).

INPUT
    --candidates   One or more specs "cid=<path_to_boltz_out>" OR a CSV via --candidates-csv.
    --candidates-csv  CSV with columns: candidate, boltz_out[, design, mpnn_score, seq].
                      (mpnn_score/design/seq are carried through to the output if present.)

OUTPUT
    --out-json   per-candidate metrics (list of dicts) -- full detail incl. residue lists
    --out-csv    ranked summary table (sorted by --rank-by, default iptm desc)

EXAMPLE (PCSK9 cand2 -- reproduces iptm 0.9466, iface_pae 1.76, 170 contacts)
    python analyze_interface.py \
        --candidates cand2=/path/hpc_<job>/outputs/boltz_results_cand2 \
        --domain-start 155 \
        --catalytic-triad 186,226,386 \
        --out-json metrics.json --out-csv ranking.csv

EXIT CODES
    0 success; 2 usage/input error (missing files, no candidates parsed).
"""
import argparse
import glob
import json
import os
import sys

CONTACT_CUT_DEFAULT = 4.5


def die(msg, code=2):
    sys.stderr.write(f"[analyze_interface] ERROR: {msg}\n")
    sys.exit(code)


def find_pred_dir(boltz_out, cid):
    """Locate the Boltz predictions/<cid> directory under a results dir."""
    cands = [
        os.path.join(boltz_out, "predictions", cid),
        os.path.join(boltz_out, f"boltz_results_{cid}", "predictions", cid),
    ]
    # also glob one level down for boltz_results_* wrappers
    cands += glob.glob(os.path.join(boltz_out, "**", "predictions", cid), recursive=True)
    for d in cands:
        if os.path.isdir(d):
            return d
    return None


def load_npz_array(path):
    """Load the first array stored in an .npz file."""
    import numpy as np
    with np.load(path) as z:
        return z[z.files[0]]


def analyze_one(cid, boltz_out, args):
    import numpy as np
    from Bio.PDB import MMCIFParser
    from scipy.spatial.distance import cdist

    pred = find_pred_dir(boltz_out, cid)
    if pred is None:
        die(f"[{cid}] could not find predictions/{cid} under {boltz_out}")

    cif = os.path.join(pred, f"{cid}_model_0.cif")
    conf_json = os.path.join(pred, f"confidence_{cid}_model_0.json")
    pae_npz = os.path.join(pred, f"pae_{cid}_model_0.npz")
    plddt_npz = os.path.join(pred, f"plddt_{cid}_model_0.npz")
    for f in (cif, conf_json):
        if not os.path.isfile(f):
            die(f"[{cid}] missing required file: {f}")

    conf = json.load(open(conf_json))

    # --- structure: chain A = binder, chain B = target ---
    structure = MMCIFParser(QUIET=True).get_structure(cid, cif)
    model = structure[0]
    chain_ids = [c.id for c in model]
    bch = args.binder_chain if args.binder_chain in chain_ids else chain_ids[0]
    tch = args.target_chain if args.target_chain in chain_ids else chain_ids[1]

    binder_res = [r for r in model[bch].get_residues() if r.id[0] == " "]
    target_res = [r for r in model[tch].get_residues() if r.id[0] == " "]
    binder_len = len(binder_res)
    target_len = len(target_res)

    def heavy_atoms(reslist):
        coords, owner = [], []
        for idx, r in enumerate(reslist):
            for a in r:
                if a.element != "H":
                    coords.append(a.coord)
                    owner.append(idx)
        return np.asarray(coords), np.asarray(owner)

    bc, bo = heavy_atoms(binder_res)
    tc, to = heavy_atoms(target_res)
    if len(bc) == 0 or len(tc) == 0:
        die(f"[{cid}] no heavy atoms parsed for one of the chains")

    D = cdist(bc, tc)
    contact_mask = D <= args.contact_cut
    n_contacts = int(contact_mask.sum())

    b_hit = sorted(set(bo[np.where(contact_mask.any(axis=1))[0]].tolist()))
    t_hit = sorted(set(to[np.where(contact_mask.any(axis=0))[0]].tolist()))

    # binder positions 1-based; target residues in NATIVE numbering
    binder_iface_positions = [i + 1 for i in b_hit]
    target_contact_residues = [args.domain_start + i for i in t_hit]

    # --- PAE / pLDDT (optional but expected) ---
    iface_pae = None
    binder_iface_plddt = None
    if os.path.isfile(pae_npz) and b_hit and t_hit:
        pae = load_npz_array(pae_npz).astype(float)
        # Concatenated order: binder rows/cols 0..binder_len-1, then target binder_len..
        # Interface PAE = mean over the binder-rows x target-cols block, i.e. the expected
        # positional error of TARGET interface residues in the BINDER's reference frame.
        # (This is the standard directional convention; do NOT symmetrize -- the two
        # directions differ and symmetrizing inflates the value.)
        bi = np.array(b_hit)
        ti = np.array([binder_len + i for i in t_hit])
        block_bt = pae[np.ix_(bi, ti)]     # binder rows x target cols
        iface_pae = round(float(block_bt.mean()), 2)
    if os.path.isfile(plddt_npz) and b_hit:
        plddt = load_npz_array(plddt_npz).astype(float)
        binder_iface_plddt = round(float(np.mean([plddt[i] for i in b_hit])), 2)

    fset = set(args.functional_site)
    tset = set(args.catalytic_triad)
    epitope = set(target_contact_residues)

    # --- hotspot recovery ---
    # hotspots_declared is the sorted input set echoed verbatim; when no --hotspots
    # was given, epitope_status is NOT_ASSESSED -- it must never fall back to ON_TARGET.
    hotspots_declared = sorted(args.hotspots)
    if hotspots_declared:
        hset = set(hotspots_declared)
        hotspots_recovered = sorted(epitope & hset)
        n_hotspots_recovered = len(hotspots_recovered)
        n_declared = len(hotspots_declared)
        hotspot_recovery = f"{n_hotspots_recovered}/{n_declared}"
        if n_hotspots_recovered >= args.min_hotspot_recovery:
            epitope_status = "ON_TARGET"
        elif n_hotspots_recovered > 0:
            epitope_status = "PARTIAL"
        else:
            epitope_status = "OFF_TARGET"
    else:
        hotspots_recovered = []
        n_hotspots_recovered = 0
        hotspot_recovery = "0/0"
        epitope_status = "NOT_ASSESSED"

    # --- construct frame on every record ---
    domain_start = args.domain_start
    target_range = f"{domain_start}-{domain_start + target_len - 1}"

    rec = {
        "candidate": cid,
        "binder_len": binder_len,
        "target_len": target_len,
        "domain_start": domain_start,
        "target_range": target_range,
        "iptm": round(float(conf.get("iptm")), 4) if conf.get("iptm") is not None else None,
        "ptm": round(float(conf.get("ptm")), 4) if conf.get("ptm") is not None else None,
        "complex_plddt": round(float(conf.get("complex_plddt")), 4) if conf.get("complex_plddt") is not None else None,
        "complex_iplddt": round(float(conf.get("complex_iplddt")), 4) if conf.get("complex_iplddt") is not None else None,
        "binder_iface_plddt": binder_iface_plddt,
        "iface_pae": iface_pae,
        "n_interface_atom_contacts": n_contacts,
        "n_binder_iface_res": len(binder_iface_positions),
        "n_target_iface_res": len(target_contact_residues),
        "target_contact_residues": target_contact_residues,
        "binder_iface_positions": binder_iface_positions,
        "hotspots_declared": hotspots_declared,
        "hotspots_recovered": hotspots_recovered,
        "n_hotspots_recovered": n_hotspots_recovered,
        "hotspot_recovery": hotspot_recovery,
        "epitope_status": epitope_status,
    }
    # --- conditional site-overlap keys ---
    # Emit only when the corresponding input was supplied, and always alongside the
    # declared set so an empty overlap list is readable against a non-empty declared set.
    if fset:
        rec["functional_site_residues"] = sorted(fset)
        rec["functional_site_overlap"] = sorted(epitope & fset)
    if tset:
        rec["catalytic_triad_residues"] = sorted(tset)
        rec["triad_overlap"] = sorted(epitope & tset)
    return rec


def confidence_tier(iptm, iface_pae):
    """Human-readable interface-confidence tier."""
    if iptm is None:
        return "UNKNOWN"
    if iptm > 0.8:
        tier = "HIGH"
    elif iptm >= 0.6:
        tier = "MODERATE"
    else:
        tier = "LOW"
    if iface_pae is not None:
        if iface_pae < 5:
            pae_note = "confident"
        elif iface_pae <= 10:
            pae_note = "uncertain"
        else:
            pae_note = "unreliable"
        return f"{tier} ({pae_note} interface PAE)"
    return tier


def main():
    ap = argparse.ArgumentParser(
        description="Analyze Boltz-2 binder:target complexes; rank by interface confidence.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidates", nargs="+", metavar="cid=path",
                   help="Space-separated specs cid=<boltz_out_dir>.")
    g.add_argument("--candidates-csv",
                   help="CSV with columns candidate,boltz_out[,design,mpnn_score,seq].")
    ap.add_argument("--binder-chain", default="A", help="Binder chain id in the CIF.")
    ap.add_argument("--target-chain", default="B", help="Target chain id in the CIF.")
    ap.add_argument("--domain-start", type=int, default=1,
                    help="Native residue number of the target chain's first residue "
                         "(native = domain_start + chainB 0-based index).")
    ap.add_argument("--contact-cut", type=float, default=CONTACT_CUT_DEFAULT,
                    help="Heavy-atom contact distance cutoff (Angstrom).")
    ap.add_argument("--functional-site", default="",
                    help="Comma-separated native residue numbers of a functional/blocking site "
                         "(e.g. a receptor-binding epitope) to check overlap against.")
    ap.add_argument("--catalytic-triad", default="",
                    help="Comma-separated native residue numbers of catalytic residues.")
    ap.add_argument("--hotspots", default="",
                    help="Comma-separated native residue numbers of declared epitope hotspots "
                         "(the same set passed to prepare_target.py --hotspots and to "
                         "ppi.hotspot_res). When supplied, each candidate is scored for "
                         "hotspot recovery and assigned an epitope_status.")
    ap.add_argument("--min-hotspot-recovery", type=int, default=1,
                    help="Minimum number of declared hotspots recovered for ON_TARGET status. "
                         "A candidate recovering >0 but fewer than this is PARTIAL; 0 is OFF_TARGET.")
    ap.add_argument("--rank-by", default="iptm",
                    choices=["iptm", "ptm", "iface_pae", "complex_plddt"],
                    help="Metric to rank candidates by.")
    ap.add_argument("--out-json", required=True, help="Output per-candidate metrics JSON.")
    ap.add_argument("--out-csv", required=True, help="Output ranked summary CSV.")
    args = ap.parse_args()

    args.functional_site = [int(x) for x in args.functional_site.split(",") if x.strip()]
    args.catalytic_triad = [int(x) for x in args.catalytic_triad.split(",") if x.strip()]
    args.hotspots = [int(x) for x in args.hotspots.split(",") if x.strip()]

    # Build candidate list.
    carry = {}   # cid -> extra columns to merge (design, mpnn_score, seq)
    pairs = []
    if args.candidates_csv:
        import pandas as pd
        if not os.path.isfile(args.candidates_csv):
            die(f"--candidates-csv not found: {args.candidates_csv}")
        cdf = pd.read_csv(args.candidates_csv)
        need = {"candidate", "boltz_out"}
        if not need.issubset(cdf.columns):
            die(f"--candidates-csv must have columns {need}; found {list(cdf.columns)}")
        for _, r in cdf.iterrows():
            cid = str(r["candidate"])
            pairs.append((cid, str(r["boltz_out"])))
            carry[cid] = {k: r[k] for k in ("design", "mpnn_score", "seq") if k in cdf.columns}
    else:
        for spec in args.candidates:
            if "=" not in spec:
                die(f"bad --candidates spec (need cid=path): {spec}")
            cid, path = spec.split("=", 1)
            pairs.append((cid.strip(), path.strip()))

    if not pairs:
        die("no candidates to analyze.")

    records = []
    for cid, path in pairs:
        rec = analyze_one(cid, path, args)
        rec.update({k: v for k, v in carry.get(cid, {}).items()})
        rec["confidence_tier"] = confidence_tier(rec["iptm"], rec["iface_pae"])
        records.append(rec)
        print(f"[analyze_interface] {cid}: ipTM={rec['iptm']} iface_PAE={rec['iface_pae']} "
              f"contacts={rec['n_interface_atom_contacts']} "
              f"target_contact_res={rec['n_target_iface_res']} "
              f"hotspots={rec['hotspot_recovery']} ({rec['epitope_status']}) "
              f"-> {rec['confidence_tier']}")

    # Rank.
    ascending = args.rank_by in ("iface_pae",)
    records_sorted = sorted(
        records,
        key=lambda r: (r[args.rank_by] is None, r[args.rank_by] if r[args.rank_by] is not None else 0),
        reverse=not ascending,
    )
    for i, r in enumerate(records_sorted, 1):
        r["rank"] = i

    os.makedirs(os.path.dirname(os.path.abspath(args.out_json)) or ".", exist_ok=True)
    json.dump({r["candidate"]: r for r in records_sorted}, open(args.out_json, "w"), indent=1)

    # Summary CSV (scalar columns only).
    import pandas as pd
    scalar_cols = ["rank", "candidate", "design", "binder_len", "mpnn_score", "iptm", "ptm",
                   "complex_plddt", "binder_iface_plddt", "iface_pae",
                   "n_interface_atom_contacts", "n_binder_iface_res", "n_target_iface_res",
                   "n_hotspots_recovered", "hotspot_recovery", "epitope_status",
                   "confidence_tier"]
    rowdicts = [{k: r.get(k) for k in scalar_cols} for r in records_sorted]
    pd.DataFrame(rowdicts).to_csv(args.out_csv, index=False)

    print(f"[analyze_interface] ranked {len(records)} candidate(s) by {args.rank_by}; "
          f"top = {records_sorted[0]['candidate']} ({records_sorted[0][args.rank_by]}).")
    print(f"[analyze_interface] wrote:\n    {args.out_json}\n    {args.out_csv}")


if __name__ == "__main__":
    main()
