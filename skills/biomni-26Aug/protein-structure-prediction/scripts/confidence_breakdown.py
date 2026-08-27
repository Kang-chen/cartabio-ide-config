#!/usr/bin/env python3
"""
confidence_breakdown.py — canonical, single-source confidence breakdowns for the
protein-structure-prediction skill.

Two breakdowns live here, so the agent never hand-rolls them:

  1. band_breakdown(plddt)            -> pLDDT confidence-band counts
                                         (AlphaFold-calibrated) with EXPLICIT,
                                         unambiguous interval conventions.
  2. domain_breakdown(plddt, features)-> per-residue pLDDT summarized over
                                         UniProt-annotated features, taking every
                                         range VERBATIM from the fetched feature
                                         table (never a partition the agent
                                         composes). Domain-tier features drive the
                                         overlap / no-domain-feature map; signal /
                                         propeptide / transit-peptide / chain
                                         features are reported in their OWN
                                         category; only residues covered by NO
                                         feature at all are "uncovered".

Plus fetch_uniprot_features(accession) to pull the feature table (domain +
sequence-level tiers) so ranges are never invented.

WHY THIS FILE EXISTS
--------------------
In an audited run (PCSK9, UniProt Q8NBP7, 692 aa) the agent — given no packaged
function — hand-partitioned the protein into contiguous "domain" bins that
CONTRADICTED the UniProt features the same run had already fetched: residues
450-461 were attributed to the wrong domain and the "Inhibitor I9" label was
stretched across the whole prodomain. It also binned pLDDT with a right-closed
cut() whose boundaries did not match the labels it printed ("very high >=90"
actually meant >90; "<50" actually included 50). These functions make both steps
derive from produced data with a stated convention, so the label always matches
the computation and domain ranges always match UniProt.

All per-residue pLDDT is expected on the 0-100 scale that extract_plddt.py emits.
Residue numbering is 1..N of the SUBMITTED sequence (as in every other output).
"""
import os
import json
import argparse
import numpy as np


# ============================================================================
# 1. CONFIDENCE-BAND BREAKDOWN  (item 2 — one function, unambiguous boundaries)
# ============================================================================
# Convention (STATED): lower-bound INCLUSIVE, upper-bound EXCLUSIVE. Every
# residue in [0, 100] falls in exactly ONE band; no overlaps, no gaps. The label
# spells out the exact inequality so it always matches the computation.
#   very_high : pLDDT >= 90            [90, +inf)
#   confident : 70 <= pLDDT < 90       [70, 90)
#   low       : 50 <= pLDDT < 70       [50, 70)
#   very_low  : pLDDT < 50             (-inf, 50)
_NINF, _PINF = float("-inf"), float("inf")
_BANDS = [
    ("very_high", 90.0, _PINF, "pLDDT >= 90"),
    ("confident", 70.0, 90.0,  "70 <= pLDDT < 90"),
    ("low",       50.0, 70.0,  "50 <= pLDDT < 70"),
    ("very_low",  _NINF, 50.0, "pLDDT < 50"),
]
BAND_CONVENTION = ("lower-bound inclusive, upper-bound exclusive; "
                   "very_high=[90,inf), confident=[70,90), low=[50,70), "
                   "very_low=(-inf,50)")


def _as_plddt(plddt):
    arr = np.asarray(plddt, dtype=float).ravel()
    return arr[~np.isnan(arr)]


def band_breakdown(plddt):
    """Confidence-band counts for a per-residue pLDDT vector (0-100 scale).

    THIS IS THE SINGLE SOURCE for band counts in this skill. Do not re-implement
    binning anywhere else (an ad-hoc right/left-closed cut() is exactly the bug
    this replaces).

    Returns:
        {
          "convention": <str, states inclusivity>,
          "n_res": int,                       # residues counted (NaN dropped)
          "bands": [                          # order: very_high..very_low
            {"band","label","lower","upper","lower_inclusive","upper_inclusive",
             "count","percent","mean_plddt"}, ...
          ]
        }
    Guarantees sum(count) == n_res (asserts, so a binning gap/overlap fails loud).
    """
    arr = _as_plddt(plddt)
    n = int(arr.size)
    out = {"convention": BAND_CONVENTION, "n_res": n, "bands": []}
    counted = 0
    for key, lo, hi, label in _BANDS:
        mask = (arr >= lo) & (arr < hi)
        c = int(mask.sum())
        counted += c
        lower = None if lo == _NINF else lo
        upper = None if hi == _PINF else hi
        out["bands"].append({
            "band": key,
            "label": label,
            "lower": lower,
            "upper": upper,
            "lower_inclusive": (True if lower is not None else None),
            "upper_inclusive": (False if upper is not None else None),
            "count": c,
            "percent": (round(100.0 * c / n, 2) if n else 0.0),
            "mean_plddt": (round(float(arr[mask].mean()), 2) if c else None),
        })
    assert counted == n, (f"band counts sum to {counted} but n_res={n} — binning "
                          "gap/overlap bug (this must never happen)")
    return out


def _band_mix(arr):
    """Compact {band: count} for a slice, using the SAME binning as
    band_breakdown() (so segment/feature band mixes cannot drift from the top-
    level breakdown)."""
    return {b["band"]: b["count"] for b in band_breakdown(arr)["bands"]}


# ============================================================================
# 2. UNIPROT FEATURE FETCH  (ranges are pulled, never invented)
# ============================================================================
# Two tiers of feature types are fetched and kept in SEPARATE categories:
#
#   * DOMAIN_FEATURE_TYPES  — structural/functional segments (the domain-resolved
#     tier). The domain coverage / overlap / gap map is built from these ONLY.
#   * SEQUENCE_FEATURE_TYPES — processing / chain-level features (signal peptide,
#     propeptide, transit peptide, chain, peptide). These are REAL UniProt
#     annotations that a domain-only fetch silently drops, so residues in a signal
#     peptide or propeptide would wrongly appear to have "no annotation" (the exact
#     defect fixed here: PCSK9 1-30 signal + 31-152 propeptide came back as
#     "unannotated"). They are fetched and reported in their OWN category and are
#     deliberately NOT folded into the domain coverage map — the chain-spanning
#     "Chain" feature would otherwise bury the real domain overlaps.
#
# A residue is only "uncovered" when NO feature of EITHER tier covers it. Type
# sets are configurable via `types`.
DOMAIN_FEATURE_TYPES = (
    "Domain", "Region", "Repeat", "Motif", "Zinc finger",
    "DNA binding", "Calcium binding", "Coiled coil", "Transmembrane",
    "Intramembrane", "Topological domain",
)
# Exact UniProt JSON `type` strings (verified against Q8NBP7): "Signal" (not
# "Signal peptide"), "Propeptide", "Transit peptide", "Chain", "Peptide".
SEQUENCE_FEATURE_TYPES = (
    "Signal", "Transit peptide", "Propeptide", "Chain", "Peptide",
)
# Default fetch = BOTH tiers. Name kept as DEFAULT_FEATURE_TYPES for back-compat.
DEFAULT_FEATURE_TYPES = DOMAIN_FEATURE_TYPES + SEQUENCE_FEATURE_TYPES

_SEQ_TYPES_LC = {t.lower() for t in SEQUENCE_FEATURE_TYPES}


def category_for_type(ftype):
    """Category for a UniProt feature type: 'sequence' for processing / chain-level
    features (signal, transit peptide, propeptide, chain, peptide), else 'domain'.
    Keeps the domain-resolved map sharp while signal/propeptide/chain are reported
    separately rather than mislabelled as unannotated."""
    return "sequence" if str(ftype).lower() in _SEQ_TYPES_LC else "domain"


def fetch_uniprot_features(accession, types=DEFAULT_FEATURE_TYPES, timeout=30):
    """Fetch domain- and sequence-level features from UniProt REST for `accession`.

    Returns (features, meta):
      features: list of {"name","type","start","end","category"} with ranges taken
                VERBATIM from UniProt (1-based, inclusive), sorted by (start, end).
                `category` is 'domain' or 'sequence' (see category_for_type()).
      meta: {"accession","available","reason","n_features","protein_length",
             "requested_types"}.

    On ANY error, or when no features of the requested types exist, returns
    ([], meta) with meta["available"] False and a stated reason. It NEVER
    fabricates a boundary — a missing feature table means the domain breakdown is
    omitted with a reason, not approximated.
    """
    import urllib.request
    url = f"https://rest.uniprot.org/uniprotkb/{accession}.json"
    meta = {"accession": accession, "available": False, "reason": None,
            "n_features": 0, "protein_length": None,
            "requested_types": (list(types) if types else None)}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # network, 404, parse — all -> omit-with-reason
        meta["reason"] = f"UniProt fetch failed for {accession}: {e!r}"
        return [], meta
    try:
        meta["protein_length"] = int(data["sequence"]["length"])
    except Exception:
        pass
    wanted = {t.lower() for t in types} if types else None
    feats = []
    for f in data.get("features", []):
        ftype = str(f.get("type", ""))
        if wanted is not None and ftype.lower() not in wanted:
            continue
        loc = f.get("location", {})
        try:
            start = int(loc["start"]["value"])
            end = int(loc["end"]["value"])
        except (KeyError, TypeError, ValueError):
            continue  # ranges with unknown/fuzzy endpoints are skipped, not guessed
        name = f.get("description") or ftype
        feats.append({"name": name, "type": ftype, "start": start, "end": end,
                      "category": category_for_type(ftype)})
    feats.sort(key=lambda d: (d["start"], d["end"]))
    meta["n_features"] = len(feats)
    if not feats:
        meta["reason"] = (f"no features of types {sorted(types) if types else 'ANY'} "
                          f"annotated for UniProt {accession}")
        return [], meta
    meta["available"] = True
    return feats, meta


# ============================================================================
# 3. DOMAIN-RESOLVED BREAKDOWN
#    domain tier: overlap / no-domain-feature aware; sequence-level features
#    (signal/propeptide/transit/chain) reported in their OWN category; only
#    residues covered by NO feature at all are "uncovered".
# ============================================================================
def _ranges_from_positions(positions):
    """Compress a sorted iterable of ints into contiguous (start, end) ranges."""
    ranges = []
    for p in positions:
        if ranges and p == ranges[-1][1] + 1:
            ranges[-1][1] = p
        else:
            ranges.append([p, p])
    return [(a, b) for a, b in ranges]


def domain_breakdown(plddt, features, protein_name=None, feature_meta=None):
    """Per-residue pLDDT summarized over UniProt features, taking ranges VERBATIM.

    Features are split into two categories (see category_for_type()):
      * 'domain'   — structural/functional segments; the coverage / overlap / gap
                     map is built from these ONLY, so the domain-resolved view
                     stays sharp (a chain-spanning "Chain" feature cannot swamp it).
      * 'sequence' — processing / chain-level features (signal peptide, propeptide,
                     transit peptide, chain, peptide), reported in their OWN
                     category so a low-pLDDT signal peptide shows up as what it is
                     instead of a mysterious blank.

    Args:
        plddt: 1D per-residue pLDDT (0-100), length N = submitted-sequence length.
               May be None to compute the coverage map only (means become None);
               then N is inferred from the largest feature end.
        features: list of {"name","type","start","end"[, "category"]} (1-based,
               inclusive), as returned by fetch_uniprot_features(). `category` is
               inferred from `type` when absent. If empty/None the breakdown is
               OMITTED with a reason (never approximated).
        protein_name: optional label for the output.
        feature_meta: optional meta dict from fetch_uniprot_features() (its
               "reason" is surfaced when features is empty).

    Returns a dict. When features are present:
        {
          "available": True, "protein_name", "n_res",
          "requested_feature_types": {"domain":[...], "sequence":[...]},
          "features":          [ per-DOMAIN-feature rows: name,type,start,end
                                 (VERBATIM), n_res, mean_plddt, bands ],
          "sequence_features": [ per-SEQUENCE-feature rows, same shape ],
          "segments":   [ contiguous segments partitioned by the EXACT set of
                          covering DOMAIN features:
                          {start,end,n_res,status,features,sequence_features,
                           mean_plddt,bands};
                          status in {"no_domain_feature","single","overlap"};
                          "sequence_features" lists any signal/propeptide/chain/…
                          covering that segment ],
          "overlap":           {"n_res","ranges"},  # residues in >=2 DOMAIN feats
          "no_domain_feature": {"n_res","ranges"},  # residues in NO domain feat
                                                     # (may still be signal/chain)
          "uncovered":         {"n_res","ranges"},  # residues in NO feature of
                                                     # EITHER category (true gap)
          "notes": [ ... ]                           # e.g. feature outside 1..N
        }
    Overlapping residues appear in BOTH the relevant per-feature rows AND in an
    "overlap" segment listing every covering feature — never silently one. A
    residue with no DOMAIN feature is "no_domain_feature" (the honest label: it was
    tested against the domain tier), and its segment lists any covering
    sequence-level feature; only residues covered by NO feature at all are
    "uncovered". No boundary is ever invented.
    """
    if not features:
        reason = (feature_meta or {}).get("reason")
        return {"available": False,
                "protein_name": protein_name,
                "reason": (reason or "no UniProt feature table available; "
                           "domain-resolved breakdown omitted (not approximated)"),
                "features": [], "sequence_features": [], "segments": []}

    plddt_arr = None if plddt is None else np.asarray(plddt, dtype=float).ravel()
    if plddt_arr is not None:
        n = int(plddt_arr.size)
    else:
        n = max(int(f["end"]) for f in features)

    def _cat(f):
        return f.get("category") or category_for_type(f.get("type"))
    domain_feats = [f for f in features if _cat(f) == "domain"]
    seq_feats = [f for f in features if _cat(f) == "sequence"]

    def _stats(a, b):
        d = {}
        if plddt_arr is not None:
            sub = plddt_arr[a - 1:b]
            d["mean_plddt"] = (round(float(np.nanmean(sub)), 2)
                               if sub.size and not np.all(np.isnan(sub)) else None)
            d["bands"] = _band_mix(sub) if sub.size else None
        else:
            d["mean_plddt"] = None
            d["bands"] = None
        return d

    def _normalize(feat_list, tag):
        """Clip features to 1..n, collect notes; return (norm_rows, cover_map,
        notes). cover_map maps residue -> [row index in norm_rows, ...]."""
        norm, local_notes = [], []
        cover = {p: [] for p in range(1, n + 1)}
        for i, f in enumerate(feat_list):
            start, end = int(f["start"]), int(f["end"])
            name = f.get("name") or f.get("type") or f"{tag}_{i + 1}"
            ftype = f.get("type")
            cs, ce = max(1, start), min(n, end)
            norm.append({"index": i, "name": name, "type": ftype,
                         "start": start, "end": end, "cs": cs, "ce": ce})
            if ce < cs:
                local_notes.append(f"{tag} feature {name!r} ({start}-{end}) lies "
                                   f"outside residues 1-{n}; excluded")
                continue
            if start < 1 or end > n:
                local_notes.append(f"{tag} feature {name!r} annotated {start}-{end} "
                                   f"but submitted sequence is 1-{n}; counted over "
                                   f"{cs}-{ce}")
            for p in range(cs, ce + 1):
                cover[p].append(i)
        return norm, cover, local_notes

    dnorm, dcover, notes = _normalize(domain_feats, "domain")
    snorm, scover, snotes = _normalize(seq_feats, "sequence")
    notes = notes + snotes

    def _feature_rows(norm):
        rows = []
        for nf in norm:
            row = {"name": nf["name"], "type": nf["type"],
                   "start": nf["start"], "end": nf["end"]}
            cs, ce = nf["cs"], nf["ce"]
            if ce < cs:
                row.update({"n_res": 0, "mean_plddt": None, "bands": None,
                            "note": "outside submitted sequence"})
            else:
                row["n_res"] = ce - cs + 1
                row.update(_stats(cs, ce))
            rows.append(row)
        return rows

    feat_rows = _feature_rows(dnorm)   # ranges reported VERBATIM from UniProt
    seq_rows = _feature_rows(snorm)

    # coverage segments over DOMAIN features (group consecutive residues sharing
    # the exact covering set); attach covering sequence-level features per segment.
    segments = []
    p = 1
    while p <= n:
        key = tuple(sorted(dcover[p]))
        q = p
        while q + 1 <= n and tuple(sorted(dcover[q + 1])) == key:
            q += 1
        seg = {"start": p, "end": q, "n_res": q - p + 1}
        if not key:
            seg["status"], seg["features"] = "no_domain_feature", []
        elif len(key) == 1:
            seg["status"], seg["features"] = "single", [dnorm[key[0]]["name"]]
        else:
            seg["status"] = "overlap"
            seg["features"] = [dnorm[i]["name"] for i in key]
        seg["sequence_features"] = sorted(
            {snorm[i]["name"] for r in range(p, q + 1) for i in scover[r]})
        seg.update(_stats(p, q))
        segments.append(seg)
        p = q + 1

    overlap_pos = [p for p in range(1, n + 1) if len(dcover[p]) >= 2]
    no_domain_pos = [p for p in range(1, n + 1) if len(dcover[p]) == 0]
    uncovered_pos = [p for p in range(1, n + 1)
                     if len(dcover[p]) == 0 and len(scover[p]) == 0]
    return {
        "available": True,
        "protein_name": protein_name,
        "n_res": n,
        "requested_feature_types": {"domain": list(DOMAIN_FEATURE_TYPES),
                                    "sequence": list(SEQUENCE_FEATURE_TYPES)},
        "features": feat_rows,
        "sequence_features": seq_rows,
        "segments": segments,
        "overlap": {"n_res": len(overlap_pos),
                    "ranges": _ranges_from_positions(overlap_pos)},
        "no_domain_feature": {"n_res": len(no_domain_pos),
                              "ranges": _ranges_from_positions(no_domain_pos)},
        "uncovered": {"n_res": len(uncovered_pos),
                      "ranges": _ranges_from_positions(uncovered_pos)},
        "notes": notes,
    }


# ============================================================================
# CLI — build both breakdowns from the run's produced pLDDT CSV
# ============================================================================
def _read_plddt_csv(path):
    import csv
    vals = []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            vals.append(float(row["plddt_0_100"]))
    return np.asarray(vals, dtype=float)


def build_breakdowns(plddt, accession=None, features=None, feature_types=None,
                     protein_name=None):
    """Convenience: assemble both breakdowns. Feature source precedence:
    explicit `features` list > `accession` fetch > none (domain omitted)."""
    meta = None
    if features is None:
        if accession:
            types = tuple(feature_types) if feature_types else DEFAULT_FEATURE_TYPES
            features, meta = fetch_uniprot_features(accession, types=types)
        else:
            features, meta = [], {"reason": "no --accession or --features-json given"}
    return {
        "protein_name": protein_name,
        "accession": accession,
        "band_breakdown": band_breakdown(plddt),
        "domain_breakdown": domain_breakdown(plddt, features,
                                             protein_name=protein_name,
                                             feature_meta=meta),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Canonical band + domain-resolved pLDDT confidence breakdowns.")
    ap.add_argument("--plddt-csv", required=True,
                    help="per-residue CSV from extract_plddt.py (col plddt_0_100)")
    ap.add_argument("--name", default=None, help="protein label for the output")
    ap.add_argument("--accession", default=None,
                    help="UniProt accession to fetch the feature table from")
    ap.add_argument("--features-json", default=None,
                    help="JSON list of {name,type,start,end} (overrides --accession)")
    ap.add_argument("--feature-types", nargs="*", default=None,
                    help="restrict UniProt feature types (default: domain-oriented set)")
    ap.add_argument("--out", default=None, help="output JSON path (default: stdout)")
    a = ap.parse_args()

    plddt = _read_plddt_csv(a.plddt_csv)
    features = None
    if a.features_json:
        with open(a.features_json) as fh:
            features = json.load(fh)
    res = build_breakdowns(plddt, accession=a.accession, features=features,
                           feature_types=a.feature_types, protein_name=a.name)
    text = json.dumps(res, indent=2)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as fh:
            fh.write(text)
        print(f"wrote {a.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
