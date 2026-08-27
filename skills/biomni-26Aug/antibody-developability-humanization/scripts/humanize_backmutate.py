#!/usr/bin/env python3
"""
humanize_backmutate.py  --  CDR-graft humanization + rule-based back-mutation.

Generalized from the validated muMAb 4D5 -> trastuzumab workflow. Works on ANY
non-human paired VH/VL (mouse / rat / rabbit / etc.), not just 4D5.

Design philosophy (all BLIND to any clinical reference):
  * Acceptor selection: by default we build TWO acceptor philosophies per chain
      1. "consensus"       - the most common / most developable human germline
                             (VH3 IGHV3-23, Vk1 IGKV1-39, Vl1 IGLV1-40) -- what
                             industry actually grafts onto.
      2. "nearest"         - the human germline with the highest framework
                             identity to the query (ANARCI nearest-human call),
                             which maximizes raw sequence identity.
    Framework choice matters MORE than raw germline identity, so we report the
    frontier of both and let the caller compare.
  * Grafting: human FR1-FR3 (germline V) + human FR4 (germline J) + donor CDRs.
    CDR definition follows the numbering scheme (default Kabat, matching
    Carter 1992 humanization practice).
  * Back-mutation: revert human->donor ONLY at framework positions in the
    selected rule sets (Vernier / Interface / Canonical), never in CDRs, and
    only where graft != donor. Each reversion carries a per-position rationale
    (which rule(s) fired). An `aggressiveness` knob controls how many rule sets
    are active (see BACKMUT_LEVELS).

Public API:
  choose_acceptors(vh, vl, scheme, extra_vh_candidates=..., extra_vl_candidates=...)
  graft_chain(donor_seq, v_gene, j_gene, chain_type, scheme)
  propose_backmutations(graft_seq, donor_seq, chain_type, scheme, level, custom_positions)
  apply_backmutations(graft_seq, bm_df, scheme)
  humanize(vh, vl, scheme=, acceptors=, level=, ...)  -> dict of constructs + tables

CLI:
  python humanize_backmutate.py --vh <seq|-> --vl <seq> [--scheme kabat]
      [--level conservative|moderate|aggressive|maximal] [--json out.json]
"""
from __future__ import annotations
import argparse, json, re, sys
import pandas as pd

from ab_core import (make_chain, region_seq, DEFAULT_SCHEME,
                     _load_germlines)

# ---------------------------------------------------------------------------
# Rule sets (Kabat numbering) -- validated set from the 4D5 workflow.
#   Vernier zone (Foote & Winter 1992 J Mol Biol): FR residues underpinning
#     CDR conformation.
#   VH/VL interface (Chothia / Padlan): residues at the domain interface.
#   Canonical-class determinants (Chothia): CDR-supporting FR residues.
# ---------------------------------------------------------------------------
VERNIER_VH   = {2, 27, 28, 29, 30, 48, 49, 67, 69, 71, 73, 78, 80, 93, 94}
VERNIER_VL   = {2, 4, 35, 36, 46, 47, 48, 49, 64, 66, 68, 69, 71}
INTERFACE_VH = {37, 39, 45, 47, 91, 93, 103}
INTERFACE_VL = {36, 38, 43, 44, 46, 49, 87, 98}
CANONICAL_VH = {24, 71, 94}
CANONICAL_VL = {2, 25, 33, 71}

# Aggressiveness knob: which rule sets contribute back-mutations.
#   Fewer sets  -> fewer reversions -> MORE human, higher immunogenicity risk,
#                  higher risk of affinity loss (aggressive humanization).
#   More sets   -> more reversions  -> safer for affinity, less human
#                  (conservative humanization).
# NOTE naming follows humanization aggressiveness (how hard you push toward
# human), so "aggressive" = fewest back-mutations.
BACKMUT_LEVELS = {
    "aggressive":   ["Canonical"],                          # minimal reversions
    "moderate":     ["Vernier"],
    "conservative": ["Vernier", "Interface", "Canonical"],  # default (validated)
    "maximal":      ["Vernier", "Interface", "Canonical"],  # == conservative here
}
DEFAULT_LEVEL = "conservative"

# Field-standard "consensus"/developable acceptor germlines and their J genes.
CONSENSUS_ACCEPTORS = {
    "H": {"V": "IGHV3-23*01", "J": "IGHJ4*01"},   # human VH3 most common / developable
    "K": {"V": "IGKV1-39*01", "J": "IGKJ4*01"},   # human Vkappa1 most common
    "L": {"V": "IGLV1-40*01", "J": "IGLJ2*01"},   # human Vlambda1 (fallback)
}


def _rules_for(num: int, chain_type: str):
    """Return the list of rule names that flag this Kabat position."""
    H = chain_type == "H"
    out = []
    if num in (VERNIER_VH if H else VERNIER_VL):
        out.append("Vernier")
    if num in (INTERFACE_VH if H else INTERFACE_VL):
        out.append("Interface")
    if num in (CANONICAL_VH if H else CANONICAL_VL):
        out.append("Canonical")
    return out


def _active_positions(chain_type: str, level: str):
    """Union of Kabat positions active at this aggressiveness level."""
    sets = BACKMUT_LEVELS.get(level, BACKMUT_LEVELS[DEFAULT_LEVEL])
    H = chain_type == "H"
    pos = set()
    if "Vernier" in sets:
        pos |= (VERNIER_VH if H else VERNIER_VL)
    if "Interface" in sets:
        pos |= (INTERFACE_VH if H else INTERFACE_VL)
    if "Canonical" in sets:
        pos |= (CANONICAL_VH if H else CANONICAL_VL)
    return pos


# ---------------------------------------------------------------------------
# Framework-identity scoring for nearest-germline acceptor selection
# ---------------------------------------------------------------------------
def _imgt_intpos(seq: str, scheme: str):
    """{imgt_int_pos -> aa} for positions without insertion letters."""
    c = make_chain(seq, scheme="imgt")
    d = {}
    for p, a in c:
        if not getattr(p, "letter", ""):
            d[p.number] = a
    return d


def _germ_intpos(gapped: str):
    """IMGT-gapped germline string -> {1-based pos -> aa} skipping gaps."""
    return {i: ch for i, ch in enumerate(gapped, start=1) if ch not in ".-"}


def _fr_identity(query_seq: str, germ_gapped: str, scheme: str):
    """Framework-only % identity of query to an IMGT-gapped germline string.
    IMGT FR positions: FR1 1-26, FR2 39-55, FR3 66-104."""
    md = _imgt_intpos(query_seq, scheme)
    gd = _germ_intpos(germ_gapped)
    fr_pos = [p for p in gd if (1 <= p <= 26 or 39 <= p <= 55 or 66 <= p <= 104)]
    common = [p for p in fr_pos if p in md]
    if not common:
        return 0.0, 0
    ident = sum(1 for p in common if md[p] == gd[p])
    return round(100 * ident / len(common), 1), len(common)


def choose_acceptors(vh: str | None, vl: str | None,
                     scheme: str = DEFAULT_SCHEME,
                     extra_vh_families=("IGHV1", "IGHV3"),
                     extra_vl_families=("IGKV1",)):
    """Derive both acceptor philosophies (consensus + nearest) per chain.

    Returns a dict:
      {"VH": {"consensus": {...}, "nearest": {...}, "scores": [...]},
       "VL": {...}}
    Each acceptor entry: {"V": gene, "J": gene, "chain_type": "H"/"K"/"L",
                          "fr_identity": float}
    """
    Vg = _load_germlines()["V"]
    out = {}

    for dom, seq in (("VH", vh), ("VL", vl)):
        if not seq:
            out[dom] = None
            continue
        # figure out chain type + nearest human germline via ANARCI
        c = make_chain(seq, scheme="imgt", assign_germline=True,
                       allowed_species=["human"])
        ctype = c.chain_type              # 'H','K','L'
        nearest_v = c.v_gene
        nearest_j = c.j_gene
        hum_dict = Vg.get(ctype, {}).get("human", {})

        # score candidate germlines by framework identity
        fams = extra_vh_families if ctype == "H" else extra_vl_families
        cands = [g for g in hum_dict
                 if any(g.startswith(f + "-") for f in fams) and "*01" in g]
        # always include the nearest call + the consensus gene
        cons_v = CONSENSUS_ACCEPTORS.get(ctype, {}).get("V")
        cons_j = CONSENSUS_ACCEPTORS.get(ctype, {}).get("J", nearest_j)
        for g in (nearest_v, cons_v):
            if g and g in hum_dict and g not in cands:
                cands.append(g)
        scores = []
        for g in cands:
            fi, n = _fr_identity(seq, hum_dict[g], scheme)
            scores.append({"gene": g, "fr_identity": fi, "n": n})
        scores.sort(key=lambda d: -d["fr_identity"])

        nearest_fi = next((s["fr_identity"] for s in scores
                           if s["gene"] == nearest_v), None)
        cons_fi = next((s["fr_identity"] for s in scores
                        if s["gene"] == cons_v), None)

        out[dom] = {
            "chain_type": ctype,
            "consensus": ({"V": cons_v, "J": cons_j, "chain_type": ctype,
                           "fr_identity": cons_fi} if cons_v in hum_dict else None),
            "nearest": {"V": nearest_v, "J": nearest_j, "chain_type": ctype,
                        "fr_identity": nearest_fi},
            "scores": scores[:10],
        }
    return out


# ---------------------------------------------------------------------------
# Grafting engine (region-based; validated cell 43)
# ---------------------------------------------------------------------------
def _human_fr(v_gene: str, j_gene: str, chain_type: str, scheme: str):
    """FR1..FR4 dict: FR1-3 from human germline V, FR4 from human germline J."""
    _g = _load_germlines()
    Vg, Jg = _g["V"], _g["J"]
    germ_v = Vg[chain_type]["human"][v_gene].replace(".", "").replace("-", "")
    gc = make_chain(germ_v, scheme=scheme)
    fr = {r: region_seq(gc, r) for r in ("FR1", "FR2", "FR3")}
    jseq = Jg[chain_type]["human"][j_gene].replace(".", "").replace("-", "")
    if chain_type == "H":
        m = re.search(r"WG.G", jseq)
    else:
        m = re.search(r"FG.G", jseq)
    fr["FR4"] = jseq[m.start():] if m else jseq
    return fr


def graft_chain(donor_seq: str, v_gene: str, j_gene: str,
                chain_type: str, scheme: str = DEFAULT_SCHEME):
    """Human FRs (germline V + J) + donor CDRs -> grafted variable domain."""
    dc = make_chain(donor_seq, scheme=scheme)
    cdr = {r: region_seq(dc, r) for r in ("CDR1", "CDR2", "CDR3")}
    fr = _human_fr(v_gene, j_gene, chain_type, scheme)
    return (fr["FR1"] + cdr["CDR1"] + fr["FR2"] + cdr["CDR2"]
            + fr["FR3"] + cdr["CDR3"] + fr["FR4"])


# ---------------------------------------------------------------------------
# Back-mutation proposal + application (validated cells 45,47)
# ---------------------------------------------------------------------------
def _kabat_map(seq: str, scheme: str):
    """{kabat_label -> (number, aa, region)} + ordered [(label, aa)]."""
    c = make_chain(seq, scheme=scheme)
    d, ordered = {}, []
    for p, a in c:
        d[str(p)] = (p.number, a, p.get_region())
        ordered.append((str(p), a))
    return d, ordered


def propose_backmutations(graft_seq: str, donor_seq: str, chain_type: str,
                          scheme: str = DEFAULT_SCHEME,
                          level: str = DEFAULT_LEVEL,
                          custom_positions=None):
    """Compare graft vs donor at framework positions in the active rule sets;
    propose human->donor reversions with per-position rationale.

    custom_positions: optional iterable of int Kabat numbers to ALSO consider
    (union with the level's positions), for user-directed back-mutation.
    """
    positions = set(_active_positions(chain_type, level))
    if custom_positions:
        positions |= set(int(p) for p in custom_positions)

    gmap, _ = _kabat_map(graft_seq, scheme)
    dmap, _ = _kabat_map(donor_seq, scheme)
    rows = []
    for lbl, (num, gaa, greg) in gmap.items():
        if greg.startswith("CDR"):
            continue                       # CDRs already donor
        if num in positions and lbl in dmap:
            daa = dmap[lbl][1]
            if gaa != daa:
                rules = _rules_for(num, chain_type)
                if custom_positions and num in set(int(p) for p in custom_positions):
                    rules = rules or ["User-specified"]
                rows.append({"chain_type": chain_type, "kabat": lbl,
                             "kabat_num": num, "human_graft_aa": gaa,
                             "donor_aa": daa, "region": greg,
                             "rules": ";".join(rules) if rules else "Position"})
    df = pd.DataFrame(rows)
    return df.sort_values("kabat_num").reset_index(drop=True) if len(df) else df


def apply_backmutations(graft_seq: str, bm_df: pd.DataFrame,
                        scheme: str = DEFAULT_SCHEME):
    """Apply proposed reversions (by kabat label) -> (new_seq, [applied_str])."""
    _, ordered = _kabat_map(graft_seq, scheme)
    by_label = ({} if bm_df is None or len(bm_df) == 0
                else {r["kabat"]: r["donor_aa"] for _, r in bm_df.iterrows()})
    new, applied = [], []
    for lbl, a in ordered:
        if lbl in by_label:
            new.append(by_label[lbl])
            applied.append(f"{a}{lbl}{by_label[lbl]}")
        else:
            new.append(a)
    return "".join(new), applied


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def humanize(vh: str | None, vl: str | None,
             scheme: str = DEFAULT_SCHEME,
             acceptors: dict | None = None,
             level: str = DEFAULT_LEVEL,
             compare_both: bool = True,
             donor_name: str = "donor",
             custom_positions: dict | None = None):
    """Full humanization: graft + back-mutate on one or both acceptor
    philosophies. Returns dict with `constructs`, `backmutations`, `acceptors`.

    acceptors: optional override, e.g.
        {"consensus": {"VH": {"V":..,"J":..}, "VL": {...}},
         "nearest":   {"VH": {...},           "VL": {...}}}
      If None, derived via choose_acceptors().
    compare_both: if False, only the "consensus" philosophy is built.
    custom_positions: optional {"VH":[..ints], "VL":[..ints]} extra back-mut sites.
    """
    if not (vh and vl):
        raise ValueError("humanize() requires paired VH and VL sequences.")

    derived = choose_acceptors(vh, vl, scheme=scheme)
    vh_type = derived["VH"]["chain_type"]
    vl_type = derived["VL"]["chain_type"]

    philosophies = ["consensus", "nearest"] if compare_both else ["consensus"]
    if acceptors is None:
        acceptors = {}
        for phil in philosophies:
            acceptors[phil] = {
                "VH": (derived["VH"][phil] if derived["VH"].get(phil)
                       else derived["VH"]["nearest"]),
                "VL": (derived["VL"][phil] if derived["VL"].get(phil)
                       else derived["VL"]["nearest"]),
            }

    constructs = {donor_name: {"VH": vh, "VL": vl, "label": "Non-human parent",
                              "kind": "parent"}}
    bm_tables = []
    cp = custom_positions or {}

    for phil in philosophies:
        acc = acceptors[phil]
        # graft each chain
        g_vh = graft_chain(vh, acc["VH"]["V"], acc["VH"]["J"], vh_type, scheme)
        g_vl = graft_chain(vl, acc["VL"]["V"], acc["VL"]["J"], vl_type, scheme)
        tag = phil
        constructs[f"hu_{tag}_graft"] = {
            "VH": g_vh, "VL": g_vl, "kind": "graft", "philosophy": phil,
            "label": f"{phil} naive graft "
                     f"(VH {acc['VH']['V']} / VL {acc['VL']['V']})"}

        # back-mutate
        bm_vh = propose_backmutations(g_vh, vh, vh_type, scheme, level,
                                      cp.get("VH"))
        bm_vl = propose_backmutations(g_vl, vl, vl_type, scheme, level,
                                      cp.get("VL"))
        bmut_vh, ap_vh = apply_backmutations(g_vh, bm_vh, scheme)
        bmut_vl, ap_vl = apply_backmutations(g_vl, bm_vl, scheme)
        constructs[f"hu_{tag}_bmut"] = {
            "VH": bmut_vh, "VL": bmut_vl, "kind": "backmut", "philosophy": phil,
            "applied_VH": ap_vh, "applied_VL": ap_vl,
            "label": f"{phil} + back-mut (level={level})"}
        if len(bm_vh):
            bm_tables.append(bm_vh.assign(philosophy=phil, domain="VH"))
        if len(bm_vl):
            bm_tables.append(bm_vl.assign(philosophy=phil, domain="VL"))

    bm_all = (pd.concat(bm_tables, ignore_index=True)
              if bm_tables else pd.DataFrame())
    return {"constructs": constructs, "backmutations": bm_all,
            "acceptors": acceptors, "derived_acceptors": derived,
            "scheme": scheme, "level": level}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _read_arg_seq(val):
    if val == "-":
        return sys.stdin.read().strip()
    return val.strip() if val else None


def main():
    ap = argparse.ArgumentParser(description="CDR-graft humanization + back-mutation")
    ap.add_argument("--vh", required=True, help="donor VH sequence (or - for stdin)")
    ap.add_argument("--vl", required=True, help="donor VL sequence")
    ap.add_argument("--scheme", default=DEFAULT_SCHEME,
                    choices=["kabat", "imgt", "chothia", "martin"])
    ap.add_argument("--level", default=DEFAULT_LEVEL,
                    choices=list(BACKMUT_LEVELS))
    ap.add_argument("--no-compare", action="store_true",
                    help="only build the consensus acceptor (skip nearest)")
    ap.add_argument("--name", default="donor")
    ap.add_argument("--json", help="write full result JSON here")
    args = ap.parse_args()

    res = humanize(_read_arg_seq(args.vh), _read_arg_seq(args.vl),
                   scheme=args.scheme, level=args.level,
                   compare_both=not args.no_compare, donor_name=args.name)

    print(f"# Humanization ({args.scheme}, level={args.level})")
    for phil, acc in res["acceptors"].items():
        print(f"\n## {phil} acceptors: "
              f"VH {acc['VH']['V']}/{acc['VH']['J']}  "
              f"VL {acc['VL']['V']}/{acc['VL']['J']}")
    print("\n## Constructs")
    for k, v in res["constructs"].items():
        print(f"  {k:22s} VH({len(v['VH'])}) VL({len(v['VL'])}) - {v['label']}")
    if len(res["backmutations"]):
        print("\n## Back-mutations")
        cols = ["philosophy", "domain", "kabat", "human_graft_aa",
                "donor_aa", "rules"]
        print(res["backmutations"][cols].to_string(index=False))

    if args.json:
        out = {"acceptors": res["acceptors"], "scheme": res["scheme"],
               "level": res["level"],
               "constructs": {k: {kk: vv for kk, vv in v.items()}
                              for k, v in res["constructs"].items()},
               "backmutations": (res["backmutations"].to_dict("records")
                                 if len(res["backmutations"]) else [])}
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
