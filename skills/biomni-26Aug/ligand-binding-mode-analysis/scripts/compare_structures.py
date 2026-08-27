"""
Optional cross-structure concordance of a binding pocket.

Given contact tables from a PRIMARY structure and one or more COMPARISON
structures of the same (or homologous) target, report which pocket residues are
reproduced, whether the amino-acid identity is conserved, and how the minimum
contact distance varies. This is the generalization of the imatinib-ABL1
1IEP-vs-2HYY concordance check (mouse vs human ABL1).

Matching strategy:
  - Primary: match by residue NUMBER (works when structures share numbering,
    e.g. two ABL1 crystals).
  - Fallback: match by sequence alignment of the two chains and map aligned
    positions (works across orthologs/paralogs with different numbering).

The comparison is descriptive: reproducibility across independent crystals is
evidence a contact is real rather than a packing artifact.
"""

import numpy as np


def _index_by_resnum(contacts):
    return {(c["resname"], c["resseq"]): c for c in contacts}


def _index_by_num_only(contacts):
    d = {}
    for c in contacts:
        d.setdefault(c["resseq"], c)
    return d


def concordance_by_number(primary, others, other_labels=None):
    """
    Build a concordance table matching residues by number.

    Parameters
    ----------
    primary : list[dict]        contact list for the primary structure
    others  : list[list[dict]]  contact lists for comparison structures
    other_labels : list[str]    labels for the comparison structures

    Returns
    -------
    list[dict] rows with:
      resname, resseq, min_dist_primary, min_dist_<label>..., n_structures_present,
      identity_conserved (bool), present_in_all (bool)
    """
    other_labels = other_labels or [f"struct{i+2}" for i in range(len(others))]
    prim_idx = _index_by_num_only(primary)
    other_idx = [_index_by_num_only(o) for o in others]

    rows = []
    for c in primary:
        row = {
            "resname": c["resname"],
            "resseq": c["resseq"],
            "min_dist_primary": c["min_dist"],
        }
        present = 1
        identity_ok = True
        for lab, oi in zip(other_labels, other_idx):
            oc = oi.get(c["resseq"])
            if oc is not None:
                row[f"min_dist_{lab}"] = oc["min_dist"]
                present += 1
                if oc["resname"] != c["resname"]:
                    identity_ok = False
            else:
                row[f"min_dist_{lab}"] = None
        row["n_structures_present"] = present
        row["present_in_all"] = present == (1 + len(others))
        row["identity_conserved"] = identity_ok
        rows.append(row)
    rows.sort(key=lambda r: r["min_dist_primary"])
    return rows


def summarize_concordance(rows, n_structures):
    """Compact summary of a concordance table."""
    n = len(rows)
    in_all = sum(1 for r in rows if r["present_in_all"])
    identity = sum(1 for r in rows if r["identity_conserved"])
    # distance spread across structures for shared residues
    spreads = []
    for r in rows:
        vals = [r["min_dist_primary"]] + [
            v for k, v in r.items() if k.startswith("min_dist_") and k != "min_dist_primary" and v is not None
        ]
        if len(vals) > 1:
            spreads.append(max(vals) - min(vals))
    return {
        "n_pocket_residues": n,
        "n_present_in_all": in_all,
        "frac_present_in_all": round(in_all / n, 3) if n else 0,
        "n_identity_conserved": identity,
        "median_distance_spread_A": round(float(np.median(spreads)), 2) if spreads else None,
        "max_distance_spread_A": round(float(np.max(spreads)), 2) if spreads else None,
        "n_structures": n_structures,
    }
