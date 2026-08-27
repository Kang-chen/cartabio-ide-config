"""Harmonize a disease signature and a perturbation library into a common gene space.

Disease-agnostic. The disease signature can come from:
  - a built-in LINCS disease signature (resolved by resolve_inputs.py), or
  - a user-supplied up/down gene list / GMT / DE table (resolve_inputs.py).

The perturbation library defaults to the LINCS single-drug perturbation gene sets,
but any GMT with '<name>-up'/'<name>-dn' entries works (e.g. single-gene perturbations).

Outputs a pickle: {disease_up, disease_dn, pert_sigs, BG, meta}
  disease_up/dn : set[str] of UPPER human symbols
  pert_sigs     : dict {pert_name: {'up': set, 'dn': set, 'organism': 'human'|'mouse'}}
  BG            : set[str] background universe (union of all library genes + disease genes)
  meta          : dict of harmonization stats

Public API:
  parse_gmt(path) -> dict {name: (desc, [genes])}
  is_mouse_style(sym) -> bool
  classify_organism(genes) -> 'human'|'mouse'
  to_human(genes, organism, m2h) -> set[str]
  harmonize(disease_up, disease_dn, disease_org, pert_gmt_path, m2h, ...) -> dict
"""
import os
import pickle
import re
from collections import defaultdict


# ----------------------------- GMT parsing -----------------------------
def parse_gmt(path):
    """Parse a GMT: return {name: (description, [genes])}. Splits on tab, requires >=3 fields."""
    out = {}
    with open(path) as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            name = parts[0].strip()
            desc = parts[1].strip()
            genes = [g.strip() for g in parts[2:] if g.strip()]
            if name:
                out[name] = (desc, genes)
    return out


def split_updn_library(gmt):
    """Group a GMT keyed by '<base>-up'/'<base>-dn' into {base: {'up':[...], 'dn':[...]}}.

    Suffix match is case-insensitive; accepts -up/-dn/-down. Bases with only one
    direction are kept (the missing direction becomes an empty set downstream).
    """
    lib = defaultdict(dict)
    for name, (_desc, genes) in gmt.items():
        m = re.search(r"[-_](up|dn|down)$", name, flags=re.IGNORECASE)
        if not m:
            continue
        direction = "up" if m.group(1).lower() == "up" else "dn"
        base = name[: m.start()]
        lib[base][direction] = genes
    return lib


# ----------------------------- organism handling -----------------------------
def is_mouse_style(sym):
    """Heuristic: mouse symbols are Title-case (e.g. 'Spp1'), human are UPPER ('SPP1')."""
    if len(sym) < 2:
        return False
    return sym[0].isupper() and sym[1:].islower() is False and any(c.islower() for c in sym[1:]) and sym[0].isupper()


def _mouse_style_strict(sym):
    # First char upper, rest has at least one lower and no other upper -> classic mouse gene symbol
    if len(sym) < 2:
        return False
    if not sym[0].isupper():
        return False
    rest = sym[1:]
    return any(c.islower() for c in rest) and not any(c.isupper() for c in rest)


def classify_organism(genes):
    """Classify a gene list as 'mouse' or 'human' by symbol casing majority."""
    mouse = sum(_mouse_style_strict(g) for g in genes)
    upper = sum(g.isupper() for g in genes)
    return "mouse" if mouse > upper else "human"


def to_human(genes, organism, m2h):
    """Map a gene list to UPPER human symbols. Mouse genes via ortholog map (fallback: uppercase)."""
    out = set()
    if organism == "human":
        for g in genes:
            out.add(g.upper())
    else:
        for g in genes:
            gu = g.upper()
            if gu in m2h:
                out.update(m2h[gu])
            else:
                out.add(gu)  # conserved-symbol fallback
    return out


# ----------------------------- harmonization -----------------------------
def harmonize(disease_up, disease_dn, disease_org, pert_gmt_path, m2h,
              out_pickle="/workspace/dri_run/data/harmonized.pkl",
              min_set_genes=5):
    """Build the common-gene-space signature bundle. Returns the bundle dict and writes a pickle."""
    # Disease signature -> human
    d_up = to_human(disease_up, disease_org, m2h)
    d_dn = to_human(disease_dn, disease_org, m2h)
    # Remove genes ambiguous between up and dn
    amb = d_up & d_dn
    d_up -= amb
    d_dn -= amb

    # Perturbation library
    gmt = parse_gmt(pert_gmt_path)
    lib = split_updn_library(gmt)

    pert_sigs = {}
    all_genes = set(d_up) | set(d_dn)
    map_rates = []
    for base, dirs in lib.items():
        up_raw = dirs.get("up", [])
        dn_raw = dirs.get("dn", [])
        org = classify_organism(up_raw + dn_raw)
        up_h = to_human(up_raw, org, m2h)
        dn_h = to_human(dn_raw, org, m2h)
        # drop ambiguous within a drug
        a = up_h & dn_h
        up_h -= a
        dn_h -= a
        if len(up_h) + len(dn_h) < min_set_genes:
            continue
        pert_sigs[base] = {"up": up_h, "dn": dn_h, "organism": org}
        all_genes |= up_h | dn_h
        if org == "mouse":
            n_raw = len({g.upper() for g in up_raw + dn_raw})
            n_map = len({g.upper() for g in up_raw + dn_raw if g.upper() in m2h})
            if n_raw:
                map_rates.append(n_map / n_raw)

    BG = all_genes
    import numpy as np
    meta = {
        "n_pert": len(pert_sigs),
        "n_human": sum(v["organism"] == "human" for v in pert_sigs.values()),
        "n_mouse": sum(v["organism"] == "mouse" for v in pert_sigs.values()),
        "disease_up_n": len(d_up),
        "disease_dn_n": len(d_dn),
        "bg_n": len(BG),
        "mouse_map_rate_mean": float(np.mean(map_rates)) if map_rates else None,
        "mouse_map_rate_median": float(np.median(map_rates)) if map_rates else None,
    }
    bundle = {"disease_up": d_up, "disease_dn": d_dn, "pert_sigs": pert_sigs, "BG": BG, "meta": meta}
    os.makedirs(os.path.dirname(out_pickle), exist_ok=True)
    with open(out_pickle, "wb") as fh:
        pickle.dump(bundle, fh)
    print(f"[harmonize] {meta}")
    print(f"[harmonize] wrote {out_pickle}")
    return bundle
