"""
ab_core.py - shared primitives for the antibody humanization/liability skill.

Holds the validated, generalized building blocks used by every step script:
  - chain numbering (configurable scheme: kabat/imgt/chothia/martin)
  - region/CDR delineation
  - source-species + germline detection
  - developability liability motif scanning (CDR-weighted)
  - biophysical descriptors (pI, charge, GRAVY, aromaticity)
  - framework % identity to nearest human germline (humanness)

These are extracted verbatim (then generalized) from the validated
muMAb 4D5 -> trastuzumab worked example. Numbers reproduce that run when
scheme='kabat' and cdr_definition='kabat'.
"""
from __future__ import annotations
import re
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from abnumber import Chain
from pyteomics import electrochem
from Bio.SeqUtils.ProtParam import ProteinAnalysis

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AA20 = set("ACDEFGHIKLMNPQRSTVWY")

# Kyte-Doolittle hydropathy (GRAVY)
KD = {'A':1.8,'R':-4.5,'N':-3.5,'D':-3.5,'C':2.5,'Q':-3.5,'E':-3.5,'G':-0.4,
      'H':-3.2,'I':4.5,'L':3.8,'K':-3.9,'M':1.9,'F':2.8,'P':-1.6,'S':-0.8,
      'T':-0.7,'W':-0.9,'Y':-1.3,'V':4.2}

# ---------------------------------------------------------------------------
# AGGRESCAN aggregation-propensity scale (a3v / aaAV)
# ---------------------------------------------------------------------------
# Per-residue intrinsic aggregation-propensity values derived from an in vivo
# Abeta42 GFP-fusion mutational assay (Sanchez de Groot et al., FEBS J 2006;
# 273:658-668) and used by the AGGRESCAN predictor (Conchillo-Sole et al.,
# BMC Bioinformatics 2007; 8:65). More positive = more aggregation-prone.
# This is a *named, published sequence-based* predictor and REPLACES the
# GRAVY/charge surrogate as the skill's aggregation metric. Values verified
# against three independent public implementations (pymol-psico, omegamp,
# and reproduce the paper's stated ranking: I,F,V,L highest -> D,E,N,R lowest).
A3V = {'I': 1.822, 'F': 1.754, 'V': 1.594, 'L': 1.380, 'Y': 1.159,
       'W': 1.037, 'M': 0.910, 'C': 0.604, 'A': -0.036, 'T': -0.159,
       'S': -0.294, 'P': -0.334, 'G': -0.535, 'K': -0.931, 'H': -1.033,
       'Q': -1.231, 'R': -1.240, 'N': -1.302, 'E': -1.412, 'D': -1.836}

# "Hot-spot threshold" (HST): AGGRESCAN defines it as the average a3v over the
# 20 natural amino acids. An aggregation-prone region (APR / "hot spot") is a
# stretch of >= MIN_APR_LEN consecutive residues whose windowed profile (a4v)
# exceeds the HST.
A3V_HST = round(sum(A3V.values()) / len(A3V), 4)   # ~ -0.02
MIN_APR_LEN = 5                                      # AGGRESCAN minimum hot-spot length

# Default numbering / CDR definition scheme. Configurable per §8 of the plan.
# Kabat is the grafting default (matches Carter 1992).
DEFAULT_SCHEME = "kabat"

# 7-allele IEDB reference HLA-DR panel (~99% pop coverage). Configurable.
DR_PANEL_7 = ["HLA-DRB1*01:01", "HLA-DRB1*03:01", "HLA-DRB1*04:01",
              "HLA-DRB1*07:01", "HLA-DRB1*08:01", "HLA-DRB1*11:01",
              "HLA-DRB1*15:01"]


# ---------------------------------------------------------------------------
# Germline database (shared, cached). Wraps anarci.germlines.all_germlines so
# both this module and humanize_backmutate.py load it once.
#   structure: _germlines["V"|"J"][chain_type("H"/"K"/"L")][species][gene] = seq
# ---------------------------------------------------------------------------
_germlines = {}


def _load_germlines():
    """Populate the module-level `_germlines` cache (idempotent)."""
    global _germlines
    if not _germlines:
        from anarci.germlines import all_germlines
        _germlines = all_germlines
    return _germlines


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------
def make_chain(seq: str, scheme: str = DEFAULT_SCHEME,
               assign_germline: bool = False,
               allowed_species=None) -> Chain:
    """Build an abnumber Chain. cdr_definition follows scheme unless scheme is
    a pure-numbering scheme (imgt) where Kabat CDRs are still common; we set
    cdr_definition=scheme for internal consistency and reproducibility."""
    kwargs = dict(scheme=scheme, cdr_definition=scheme)
    if assign_germline:
        kwargs["assign_germline"] = True
    if allowed_species is not None:
        kwargs["allowed_species"] = allowed_species
    # imgt/chothia/martin accept cdr_definition; kabat too. Guard anyway.
    try:
        return Chain(seq, **kwargs)
    except TypeError:
        kwargs.pop("cdr_definition", None)
        return Chain(seq, **kwargs)


def region_map(chain: Chain):
    """[(position_label:str, aa:str, region:str)] in sequence order."""
    return [(str(p), a, p.get_region()) for p, a in chain]


def region_seq(chain: Chain, region: str) -> str:
    return "".join(a for p, a in chain if p.get_region() == region)


def cdr_position_index_set(chain: Chain):
    """0-based sequence indices that fall in any CDR (for epitope overlap)."""
    return set(i for i, (_, _, r) in enumerate(region_map(chain)) if "CDR" in r)


def validate_sequence(seq: str, name: str = "sequence"):
    """Validate an amino-acid V-domain string. Returns (clean_seq, warnings)."""
    warns = []
    s = re.sub(r"\s+", "", seq).upper()
    if not s:
        raise ValueError(f"{name}: empty sequence")
    bad = sorted(set(s) - AA20)
    if bad:
        raise ValueError(f"{name}: non-standard residues {bad} "
                         f"(only 20 AA allowed; check for DNA/gaps/*)")
    if not (95 <= len(s) <= 140):
        warns.append(f"{name}: length {len(s)} is outside typical V-domain "
                     f"range (95-140); is this a full V region?")
    if s.count("C") < 2:
        warns.append(f"{name}: <2 cysteines - missing the conserved "
                     f"intradomain disulfide? sequence may be truncated")
    return s, warns


# ---------------------------------------------------------------------------
# Source species / germline detection  (§6 branching)
# ---------------------------------------------------------------------------
def detect_species_and_germline(seq: str, scheme: str = DEFAULT_SCHEME):
    """Assign V germline across common species and pick the best-scoring one.
    Returns dict: {chain_type, species, v_gene, j_gene, germline_identity}.
    species in {'human','mouse','rat','rabbit',...} as reported by ANARCI."""
    result = {"chain_type": None, "species": None, "v_gene": None,
              "j_gene": None, "germline_identity": None, "error": None}
    try:
        c = make_chain(seq, scheme=scheme, assign_germline=True)
        result["chain_type"] = c.chain_type          # 'H','K','L'
        result["species"] = getattr(c, "species", None)
        result["v_gene"] = getattr(c, "v_gene", None)
        result["j_gene"] = getattr(c, "j_gene", None)
    except Exception as e:  # noqa
        result["error"] = str(e)
    return result


def classify_format(vh_seq: str | None, vl_seq: str | None,
                    scheme: str = DEFAULT_SCHEME):
    """Decide the processing branch (§6).
    Returns (branch, detail) where branch in:
      'paired_nonhuman'  -> humanize + assess (supported default)
      'paired_human'     -> assess only (already human/humanized; do NOT graft)
      'single_domain'    -> VHH/nanobody: assess only, warn
      'invalid'          -> cannot proceed
    """
    detail = {"VH": None, "VL": None, "notes": []}
    if vh_seq:
        detail["VH"] = detect_species_and_germline(vh_seq, scheme)
    if vl_seq:
        detail["VL"] = detect_species_and_germline(vl_seq, scheme)

    has_h = bool(vh_seq)
    has_l = bool(vl_seq)

    # Single-domain (VHH / heavy-only) or missing a chain
    if has_h and not has_l:
        detail["notes"].append(
            "Only a heavy chain was provided. Treating as single-domain "
            "(VHH/nanobody or heavy-only). Conventional VH/VL grafting is "
            "not applicable; running assessment-only.")
        return "single_domain", detail
    if has_l and not has_h:
        detail["notes"].append(
            "Only a light chain was provided; conventional humanization "
            "requires a paired VH/VL. Running assessment-only.")
        return "single_domain", detail
    if not has_h and not has_l:
        return "invalid", detail

    # Paired: is it already human?
    #
    # ANARCI's single closest-species label is NOT reliable on its own: human
    # and camelid VH3 germlines are very close, so genuinely human antibodies
    # (e.g. adalimumab, a phage-display fully human mAb) can get a spurious
    # 'alpaca'/'llama' VH call. Humanizing an already-human antibody is exactly
    # the error we must avoid, so we gate on FRAMEWORK IDENTITY to the nearest
    # HUMAN germline (what actually matters), not the raw species string.
    HUMAN_FR_CUTOFF = 85.0  # both chains >= this % human FR identity => already human
    fr_ids = {}
    for dom, seq in (("VH", vh_seq), ("VL", vl_seq)):
        try:
            _, fi = framework_identity_to_human(seq, scheme=scheme)
        except Exception:
            fi = None
        fr_ids[dom] = fi
    detail["human_fr_identity"] = fr_ids

    sp = []
    for d in (detail["VH"], detail["VL"]):
        if d and d.get("species"):
            sp.append(d["species"].lower())
    all_species_human = bool(sp) and all(s == "human" for s in sp)
    both_fr_human = (fr_ids.get("VH") is not None and fr_ids.get("VL") is not None
                     and fr_ids["VH"] >= HUMAN_FR_CUTOFF
                     and fr_ids["VL"] >= HUMAN_FR_CUTOFF)

    if all_species_human or both_fr_human:
        why = ("both chains assign to human germlines"
               if all_species_human else
               f"both chains are highly human by framework identity "
               f"(VH {fr_ids['VH']}%, VL {fr_ids['VL']}% to nearest human germline)")
        detail["notes"].append(
            f"This antibody is already human/humanized ({why}); re-humanizing "
            f"it would be incorrect. Running assessment-only (no graft).")
        return "paired_human", detail

    detail["notes"].append(
        f"Detected non-human source (species VH="
        f"{detail['VH'].get('species') if detail['VH'] else '?'}, VL="
        f"{detail['VL'].get('species') if detail['VL'] else '?'}; human FR "
        f"identity VH={fr_ids.get('VH')}%, VL={fr_ids.get('VL')}%). "
        f"Proceeding with humanization + assessment.")
    return "paired_nonhuman", detail


# ---------------------------------------------------------------------------
# Developability liability scan  (validated rules; CDR-weighted)
# ---------------------------------------------------------------------------
def scan_liabilities(chain: Chain, chain_name: str,
                     cdr_weight: float = 1.6):
    """Position-annotated chemical-degradation liability motifs.
    Returns (DataFrame, n_cys). Rules & severities are the validated set."""
    km = region_map(chain)
    seq = "".join(a for _, a, _ in km)
    labels = [l for l, _, _ in km]
    regions = [r for _, _, r in km]
    is_cdr = ["CDR" in r for r in regions]
    hits = []

    def add(idx, span, mtype, sev):
        loc = "CDR" if any(is_cdr[idx:idx + span]) else "FR"
        crit = sev * (cdr_weight if loc == "CDR" else 1.0)
        hits.append({"chain": chain_name, "motif_type": mtype,
                     "residues": seq[idx:idx + span],
                     "position": "-".join(labels[idx:idx + span]),
                     "region": regions[idx], "location": loc,
                     "base_severity": sev,
                     "weighted_severity": round(crit, 2)})

    for i in range(len(seq)):
        tri = seq[i:i + 3]
        di = seq[i:i + 2]
        if len(tri) == 3 and tri[0] == "N" and tri[1] != "P" and tri[2] in "ST":
            add(i, 3, "N-glycosylation (NxS/T)", 3)
        if len(di) == 2 and di[0] == "N":
            if di[1] == "G":
                add(i, 2, "Deamidation NG (high)", 3)
            elif di[1] in "SNTH":
                add(i, 2, f"Deamidation N{di[1]} (moderate)", 2)
        if len(di) == 2 and di[0] == "D":
            if di[1] == "G":
                add(i, 2, "Isomerization DG (high)", 3)
            elif di[1] in "STDH":
                add(i, 2, f"Isomerization D{di[1]} (moderate)", 2)
        if seq[i] == "M":
            add(i, 1, "Oxidation Met", 2)
        if seq[i] == "W":
            add(i, 1, "Oxidation Trp", 1)

    cys_idx = [i for i, a in enumerate(seq) if a == "C"]
    n_cys = len(cys_idx)
    if n_cys != 2:
        unpaired = (n_cys % 2 == 1)
        add(cys_idx[0] if cys_idx else 0, 1,
            f"Cys count={n_cys} (expected 2; "
            f"{'UNPAIRED' if unpaired else 'extra pair'})", 3)
    return pd.DataFrame(hits), n_cys


def biophysical(seq: str, name: str):
    """Descriptive biophysical *context* (NOT the aggregation metric).

    Theoretical pI, net charge @ pH 6.0/7.4, GRAVY hydrophobicity, aromaticity,
    residue counts. These are retained as follow-up flags that help explain an
    aggregation call (e.g. a hydrophobic, high-GRAVY CDR-H3), but aggregation
    risk itself is scored by the named AGGRESCAN a3v predictor
    (`aggregation_scan`), not by GRAVY/charge surrogates."""
    pa = ProteinAnalysis(seq)
    return {"construct": name, "length": len(seq),
            "pI": round(electrochem.pI(seq), 2),
            "net_charge_pH6.0": round(electrochem.charge(seq, 6.0), 2),
            "net_charge_pH7.4": round(electrochem.charge(seq, 7.4), 2),
            "GRAVY": round(float(np.mean([KD[a] for a in seq])), 3),
            "aromaticity": round(pa.aromaticity(), 3),
            "n_Met": seq.count("M"), "n_Trp": seq.count("W"),
            "n_Cys": seq.count("C")}


# ---------------------------------------------------------------------------
# Aggregation propensity  (AGGRESCAN a3v; named sequence-based predictor)
# ---------------------------------------------------------------------------
def _aggrescan_window_size(n: int) -> int:
    """AGGRESCAN length-adaptive sliding-window size (Conchillo-Sole 2007):
    5 for <=75 residues, 7 for <=175, 9 for <=300, 11 for >300. A variable
    domain (~110-125 aa) therefore uses window 7."""
    if n <= 75:
        return 5
    if n <= 175:
        return 7
    if n <= 300:
        return 9
    return 11


def aggregation_profile(seq: str):
    """AGGRESCAN a4v aggregation profile for a sequence.

    Returns (a4v list aligned to `seq`, window_size). a4v[i] is the average of
    the a3v values in a window of `window_size` centred on residue i, with the
    profile clamped at the termini (edge residues take the nearest full-window
    value), matching AGGRESCAN's handling of positions that cannot centre a
    full window. Virtual charged terminal residues are added per the method
    (N-term = mean a3v of K,R; C-term = mean a3v of D,E) so terminal windows
    reflect the charged-terminus environment."""
    n = len(seq)
    if n == 0:
        return [], _aggrescan_window_size(0)
    w = _aggrescan_window_size(n)
    half = w // 2
    a3v = [A3V.get(a, 0.0) for a in seq]
    # virtual terminal residues (charge effect at NH3+/COO- termini)
    n_term = (A3V["K"] + A3V["R"]) / 2.0
    c_term = (A3V["D"] + A3V["E"]) / 2.0
    padded = [n_term] * half + a3v + [c_term] * half
    a4v = []
    for i in range(n):
        window = padded[i:i + w]
        a4v.append(sum(window) / len(window))
    return a4v, w


def aggregation_scan(chain: Chain, chain_name: str,
                     cdr_weight: float = 1.6):
    """Named sequence-based aggregation assessment for one variable domain.

    Uses the AGGRESCAN a3v scale (Sanchez de Groot 2006 / Conchillo-Sole 2007):
    computes the windowed a4v profile, calls Aggregation-Prone Regions (APRs)
    as runs of >= MIN_APR_LEN consecutive residues with a4v > HST and no proline
    (proline is an AGGRESCAN aggregation breaker that splits a run), and rolls up
    per-domain aggregation numbers. CDR-resident APRs are up-weighted `cdr_weight`
    (default 1.6x, consistent with the liability scan) because an aggregation
    hot-spot in a paratope loop is both harder to remove and more likely to
    couple to function.

    Returns (apr_df, rollup_dict):
      apr_df columns: chain, apr_index, residues, position (Kabat span),
                      region, location(CDR/FR), length, peak_a4v, area, weighted_area
      rollup keys:  chain, agg_score (mean a4v over domain; the headline
                    intrinsic-propensity value), n_APR, APR_in_CDR, APR_in_FR,
                    APR_residues, agg_weighted (CDR-weighted APR area burden),
                    max_a4v, window
    """
    km = region_map(chain)
    seq = "".join(a for _, a, _ in km)
    labels = [l for l, _, _ in km]
    regions = [r for _, _, r in km]
    is_cdr = ["CDR" in r for r in regions]
    a4v, w = aggregation_profile(seq)

    # call APRs: contiguous runs above HST, length >= MIN_APR_LEN
    aprs = []
    # A residue extends a hot-spot run only if its a4v exceeds the HST AND it is
    # not proline: AGGRESCAN treats Pro as an aggregation "breaker", so a proline
    # inside an otherwise-hot stretch splits it into separate candidate segments,
    # each of which must independently satisfy the >= MIN_APR_LEN rule.
    def _hot(idx):
        return a4v[idx] > A3V_HST and seq[idx] != "P"

    i = 0
    n = len(seq)
    while i < n:
        if _hot(i):
            j = i
            while j < n and _hot(j):
                j += 1
            if (j - i) >= MIN_APR_LEN:
                aprs.append((i, j))     # half-open [i, j)
            i = j
        else:
            i += 1

    rows = []
    n_cdr = n_fr = 0
    apr_res_total = 0
    weighted_area_total = 0.0
    for k, (s, e) in enumerate(aprs, start=1):
        seg_cdr = any(is_cdr[s:e])
        loc = "CDR" if seg_cdr else "FR"
        # area = sum of (a4v - HST) over the APR (positive excess propensity)
        area = float(sum(max(a4v[p] - A3V_HST, 0.0) for p in range(s, e)))
        weighted_area = area * (cdr_weight if loc == "CDR" else 1.0)
        weighted_area_total += weighted_area
        length = e - s
        apr_res_total += length
        if loc == "CDR":
            n_cdr += 1
        else:
            n_fr += 1
        rows.append({"chain": chain_name, "apr_index": k,
                     "residues": seq[s:e],
                     "position": f"{labels[s]}-{labels[e - 1]}",
                     "region": regions[s], "location": loc,
                     "length": length,
                     "peak_a4v": round(max(a4v[s:e]), 3),
                     "area": round(area, 2),
                     "weighted_area": round(weighted_area, 2)})

    rollup = {"chain": chain_name,
              "agg_score": round(float(np.mean(a4v)) if a4v else 0.0, 3),
              "n_APR": len(aprs), "APR_in_CDR": n_cdr, "APR_in_FR": n_fr,
              "APR_residues": apr_res_total,
              "agg_weighted": round(weighted_area_total, 2),
              "max_a4v": round(max(a4v), 3) if a4v else 0.0,
              "window": w}
    return pd.DataFrame(rows), rollup


# ---------------------------------------------------------------------------
# Humanness: framework % identity to nearest human germline
# ---------------------------------------------------------------------------
def framework_identity_to_human(seq: str, scheme: str = DEFAULT_SCHEME):
    """(v_gene, framework % identity vs assigned human V germline).
    Forcing human species yields a humanness *distance*, not a true call for
    non-human inputs - documented as such in references/."""
    all_germlines = _load_germlines()
    try:
        c = Chain(seq, scheme=scheme, cdr_definition=scheme,
                  assign_germline=True, allowed_species=["human"])
    except Exception:
        return None, None
    vgene = c.v_gene
    if vgene is None:
        return None, None
    kt = c.chain_type  # 'H','K','L'
    germ = all_germlines["V"].get(kt, {}).get("human", {}).get(vgene)
    if germ is None:
        return vgene, None
    gc = Chain(germ.replace(".", "").replace("-", ""),
               scheme=scheme, cdr_definition=scheme)
    gmap = {str(p): a for p, a in gc}
    fr_match = fr_tot = 0
    for p, a in c:
        if p.get_region().startswith("FR") and str(p) in gmap:
            fr_tot += 1
            if a == gmap[str(p)]:
                fr_match += 1
    return vgene, (round(100 * fr_match / fr_tot, 1) if fr_tot else None)
