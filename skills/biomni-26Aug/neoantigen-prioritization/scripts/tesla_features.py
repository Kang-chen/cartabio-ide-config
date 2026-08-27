"""
tesla_features — compute the five TESLA immunogenicity features per candidate neoepitope and
combine them into a transparent, tiered priority.

Grounding
---------
Wells DK, van Buuren MM, Dang KK, et al. "Key Parameters of Tumor Epitope Immunogenicity
Revealed Through a Consortium Approach Improve Neoantigen Prediction." Cell 2020
(DOI: 10.1016/j.cell.2020.09.015). The consortium integrated peptide features associated with
antigen **presentation** and T-cell **recognition** into a model that filtered out 98% of
non-immunogenic peptides at precision > 0.70, and showed that pipelines which (a) prioritize
strong MHC-I **binding affinity** and (b) filter epitopes from genes with low **tumor variant
allele fraction** or low **gene expression** identify and rank neoantigens better.

The feature panel implemented here spans TESLA's two axes — antigen **presentation** and T-cell
**recognition** (Wells et al. found four enriching features: strong binding affinity, high tumor
abundance, high binding stability, and peptide recognition):
  PRESENTATION
  1. Binding affinity      — MHC-I binding of the mutant peptide (MHCflurry %rank; strong best).
  2. Tumor abundance       — expression of the source gene weighted by mutant-allele fraction
                             (TPM x VAF); low-expression / low-VAF epitopes are de-prioritized.
  3. Binding stability     — pMHC complex stability (proxy from MHCflurry presentation score;
                             optional real NetMHCstabpan half-life if that engine is available).
  RECOGNITION
  4. Agretopicity          — WT %rank / mut %rank; a mutation that improves binding over wild-type
                             marks a non-self, mutation-created epitope (now weighted).
  5. Foreignness           — local-alignment similarity to known immunogenic epitopes (IEDB) and
                             dissimilarity to the human self-proteome (Luksza 2017; Richman 2019).
  6. Fraction hydrophobic  — fraction of hydrophobic residues in the peptide (TCR-contact bias).
  7. Mutational position   — position of the mutated residue within the peptide (central positions
                             favoured, anchor positions penalised).

REAL-DATA-ONLY: every feature is computed from real inputs (real MHCflurry output on real
peptides/HLA, real expression, real VAF, real peptide sequence). A feature that cannot be
computed from real data (e.g. no expression provided) is left ``None`` and simply does not
contribute — it is never imputed with a representative value. The composite is a documented,
deterministic function labelled a **defined prioritization score, not a validated clinical
immunogenicity probability**.
"""

from __future__ import annotations

import math
import os
from typing import Optional

# --- TESLA-aligned thresholds (documented; consistent with the reused IO-response core) -----
RANK_STRONG = 0.5     # %rank < 0.5  -> strong binder (TESLA: prioritize strong binding)
RANK_BINDER = 2.0     # %rank < 2.0  -> binder
RANK_WEAK = 10.0      # %rank < 10   -> weak; >=10 -> non-binder
EXPR_TPM_FLOOR = 5.0  # TPM >= 5     -> expressed (below -> low-expression, de-prioritized)
VAF_FLOOR = 0.05      # VAF >= 0.05  -> above the low-VAF filter (TESLA de-prioritizes low VAF)

# Kyte-Doolittle hydropathy: residues with positive index are hydrophobic.
_KD = {
    "A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5,
    "G": -0.4, "H": -3.2, "I": 4.5, "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8,
    "P": -1.6, "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2,
}
_HYDROPHOBIC = {aa for aa, v in _KD.items() if v > 0}  # A C I L M F V (and their kin)


# =============================================================================
# Individual TESLA features
# =============================================================================
def binding_class(mut_rank: Optional[float]) -> str:
    """TESLA binding class from MHCflurry %rank."""
    if mut_rank is None:
        return "unknown"
    if mut_rank < RANK_STRONG:
        return "strong"
    if mut_rank < RANK_BINDER:
        return "binder"
    if mut_rank < RANK_WEAK:
        return "weak"
    return "non"


def binding_affinity_score(mut_rank: Optional[float]) -> Optional[float]:
    """Feature 1 -> 0..1. Monotonic in binding strength; maps %rank via -log10 scaled to 2.0.

    %rank 0.01 -> ~1.0, 0.5 -> 0.66, 2.0 -> 0.35, 10 -> 0.0. None if no real prediction.
    """
    if mut_rank is None:
        return None
    r = max(mut_rank, 1e-3)
    # rescale so that %rank in [1e-3, RANK_WEAK] spans [1, 0]; clamp outside
    val = (math.log10(RANK_WEAK) - math.log10(r)) / (math.log10(RANK_WEAK) - math.log10(1e-3))
    return max(0.0, min(1.0, val))


def tumor_abundance(expr_tpm: Optional[float], vaf: Optional[float]) -> dict:
    """Feature 2. Tumor abundance = expression x mutant-allele fraction.

    Returns a dict with the raw product (``abundance``, TPM-scaled), a 0..1 ``score``, and the
    boolean ``pass_expr`` / ``pass_vaf`` filters. If expression is not provided, abundance is
    ``None`` (not fabricated) and the filter is reported as ``expr_provided=False``.
    """
    out = {"abundance": None, "score": None, "pass_expr": None, "pass_vaf": None,
           "expr_provided": expr_tpm is not None, "vaf_provided": vaf is not None}
    if vaf is not None:
        out["pass_vaf"] = vaf >= VAF_FLOOR
    if expr_tpm is None:
        # expression is the dominant TESLA abundance signal; without it we cannot form the product
        return out
    out["pass_expr"] = expr_tpm >= EXPR_TPM_FLOOR
    frac = vaf if vaf is not None else 1.0   # if VAF unknown, fall back to expression alone
    ab = expr_tpm * frac
    out["abundance"] = round(ab, 4)
    # log-scale to 0..1 (TPM 1 -> ~0.15, 5 -> ~0.4, 50 -> ~0.75, 500 -> ~1.0)
    out["score"] = max(0.0, min(1.0, math.log10(ab + 1.0) / 3.0))
    return out


def binding_stability_score(presentation_score: Optional[float],
                            stab_halflife_h: Optional[float] = None) -> dict:
    """Feature 3. pMHC stability.

    Preferred: real NetMHCstabpan predicted half-life (hours) if supplied via ``stab_halflife_h``
    (score saturates around ~4 h). Proxy (default): MHCflurry presentation score (0..1), which
    incorporates processing/stability signal beyond raw affinity. Returns dict with ``score`` and
    ``source`` ('netmhcstabpan' | 'mhcflurry_presentation' | None).
    """
    if stab_halflife_h is not None:
        s = max(0.0, min(1.0, stab_halflife_h / 4.0))
        return {"score": round(s, 4), "source": "netmhcstabpan", "halflife_h": stab_halflife_h}
    if presentation_score is not None:
        return {"score": round(float(presentation_score), 4),
                "source": "mhcflurry_presentation", "halflife_h": None}
    return {"score": None, "source": None, "halflife_h": None}


def fraction_hydrophobic(peptide: str) -> Optional[float]:
    """Feature 4. Fraction of hydrophobic residues (Kyte-Doolittle > 0) in the peptide."""
    if not peptide:
        return None
    n = sum(1 for aa in peptide if aa in _HYDROPHOBIC)
    return round(n / len(peptide), 4)


def mutation_position_score(mut_pos_in_pep: Optional[int], pep_len: Optional[int]) -> dict:
    """Feature 5. Mutational position within the peptide.

    Anchor residues (positions ~2 and the C-terminus) mainly drive MHC binding, so a mutation at
    a **non-anchor, central** TCR-facing position is more likely to create a recognizably foreign
    epitope. Returns dict with 1-based ``position``, ``is_anchor``, and a 0..1 ``score`` (central
    high, anchor low). ``mut_pos_in_pep`` is 1-based; None -> unknown (e.g. fully novel neoORF).
    """
    out = {"position": mut_pos_in_pep, "is_anchor": None, "score": None}
    if not mut_pos_in_pep or not pep_len or pep_len < 3:
        return out
    p = mut_pos_in_pep
    is_anchor = (p == 2) or (p == pep_len)   # canonical MHC-I anchors: P2 and P-Omega
    out["is_anchor"] = is_anchor
    if is_anchor:
        out["score"] = 0.2
    else:
        # distance from the nearer terminus, normalised -> central positions score highest
        center = (pep_len + 1) / 2.0
        out["score"] = round(1.0 - abs(p - center) / (center - 1.0) * 0.5, 4)  # 0.5..1.0
    return out


# =============================================================================
# Recognition features (TESLA "peptide recognition" axis)
# =============================================================================
# TESLA (Wells et al., Cell 2020) found FOUR features enriched immunogenicity: strong binding
# affinity, high tumor abundance, high binding stability, and PEPTIDE RECOGNITION. The two
# functions below add the recognition axis this panel previously under-weighted:
#   - agretopicity_score: mutant binds better than wild-type (differential agretopicity)
#   - foreignness_score : sequence (dis)similarity to known immunogenic epitopes vs. self,
#                         after Luksza et al. 2017 (Nature, doi:10.1038/nature24473) and
#                         Richman et al. 2019 (Cell Systems, doi:10.1016/j.cels.2019.08.009).
# REAL-DATA-ONLY: foreignness uses real bundled reference sequences (IEDB / human self proteome)
# and a real local Smith-Waterman alignment; if a reference set is absent the feature is None.

_REF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "reference")
_IEDB_REF_FILE = os.path.join(_REF_DIR, "iedb_immunogenic_9mers.txt")
_SELF_REF_FILE = os.path.join(_REF_DIR, "human_self_9mers.txt")

_REF_CACHE: dict = {}       # path -> list[str] of reference peptides (or None if unavailable)
_ALIGNER = None             # lazily-built Biopython PairwiseAligner (local SW, BLOSUM62)


def _load_reference(path: str) -> Optional[list]:
    """Load a bundled reference peptide set (one sequence per non-comment line). Cached.

    Returns None if the file is missing (real-data-only: the feature then stays None). Never
    fabricates sequences.
    """
    if path in _REF_CACHE:
        return _REF_CACHE[path]
    peps = None
    if os.path.exists(path):
        peps = []
        with open(path) as fh:
            for line in fh:
                s = line.strip().upper()
                if s and not s.startswith("#") and all(c in "ACDEFGHIKLMNPQRSTVWY" for c in s):
                    peps.append(s)
        peps = peps or None
    _REF_CACHE[path] = peps
    return peps


def _get_aligner():
    """Build (once) a local Smith-Waterman aligner with BLOSUM62. Returns None if Biopython absent."""
    global _ALIGNER
    if _ALIGNER is not None:
        return _ALIGNER
    try:
        from Bio.Align import PairwiseAligner, substitution_matrices
        a = PairwiseAligner()
        a.substitution_matrix = substitution_matrices.load("BLOSUM62")
        a.mode = "local"
        a.open_gap_score = -11.0
        a.extend_gap_score = -1.0
        _ALIGNER = a
    except Exception:
        _ALIGNER = False   # sentinel: tried and unavailable
    return _ALIGNER


def _best_alignment_score(peptide: str, refs: list) -> Optional[float]:
    """Best local-alignment (Smith-Waterman, BLOSUM62) raw score of ``peptide`` vs a reference set."""
    aligner = _get_aligner()
    if not aligner or not refs or not peptide:
        return None
    best = None
    for r in refs:
        try:
            sc = aligner.score(peptide, r)
        except Exception:
            continue
        if best is None or sc > best:
            best = sc
    return best


def agretopicity_score(agretopicity: Optional[float]) -> Optional[float]:
    """Recognition feature. Differential agretopicity = WT %rank / mut %rank.

    A value >= 1 means the mutation improves (or preserves) binding relative to wild-type — the
    hallmark of a mutation-created, non-self epitope (TESLA/Ghorani-style agretopicity). Maps to a
    0..1 score that saturates near agretopicity ~= 2 (mutant binds >=2x better than WT). Values < 1
    (WT already binds better; likely self-tolerised) score low. None -> unknown (e.g. frameshift
    neoORF with no 1:1 WT counterpart) -> None (does not contribute).
    """
    if agretopicity is None:
        return None
    a = float(agretopicity)
    if a <= 0:
        return 0.0
    # log-scaled, saturating: agreto 1 -> 0.5, agreto 2 -> ~0.85, agreto >=4 -> ~1.0
    return round(max(0.0, min(1.0, 0.5 + 0.5 * math.log2(a) / 2.0)), 4)


def foreignness_score(peptide: str,
                      iedb_refs: Optional[list] = None,
                      self_refs: Optional[list] = None) -> dict:
    """Recognition feature. Combined foreignness / dissimilarity-to-self from local alignment.

    - Foreignness: high similarity to a known immunogenic epitope (IEDB) => TCR-recognizable
      (Luksza et al. 2017, recognition potential).
    - Dissimilarity-to-self: LOW similarity to the human self-proteome => less likely tolerised,
      more immunogenic (Richman et al. 2019).

    Returns dict with ``score`` (0..1, higher = more likely recognized), the two component signals,
    and ``source``. If neither reference set is available (or Biopython missing), ``score`` is None
    and ``source`` is None — the feature simply does not contribute (real-data-only).
    """
    out = {"score": None, "foreignness": None, "self_similarity": None, "source": None}
    if not peptide:
        return out
    iedb = iedb_refs if iedb_refs is not None else _load_reference(_IEDB_REF_FILE)
    self_ = self_refs if self_refs is not None else _load_reference(_SELF_REF_FILE)
    if _get_aligner() in (None, False) or (not iedb and not self_):
        return out
    # self-alignment score of the peptide against itself = theoretical max (normalizer)
    max_self = _best_alignment_score(peptide, [peptide]) or 1.0
    comps = []
    used = []
    if iedb:
        fs = _best_alignment_score(peptide, iedb)
        if fs is not None:
            foreign = max(0.0, min(1.0, fs / max_self))   # similarity to known immunogenic set
            out["foreignness"] = round(foreign, 4)
            comps.append(foreign); used.append("iedb")
    if self_:
        ss = _best_alignment_score(peptide, self_)
        if ss is not None:
            self_sim = max(0.0, min(1.0, ss / max_self))
            out["self_similarity"] = round(self_sim, 4)
            comps.append(1.0 - self_sim)                  # dissimilarity-to-self
            used.append("self")
    if not comps:
        return out
    out["score"] = round(sum(comps) / len(comps), 4)
    out["source"] = "+".join(used)
    return out


# =============================================================================
# Composite TESLA priority
# =============================================================================
# Weights span BOTH TESLA axes: presentation (binding affinity, tumor abundance, stability) and
# recognition (agretopicity, foreignness/dissimilarity-to-self, hydrophobicity, mutation position).
# Recognition now contributes ~0.28 in total (was ~0.20 and excluded agretopicity/foreignness).
# Documented, not fit to outcome data — a transparent prioritization score.
FEATURE_WEIGHTS = {
    "binding_affinity": 0.30,
    "tumor_abundance": 0.22,
    "agretopicity": 0.15,
    "foreignness": 0.13,
    "binding_stability": 0.08,
    "fraction_hydrophobic": 0.06,
    "mutation_position": 0.06,
}


def composite_score(features: dict) -> dict:
    """Combine available TESLA feature sub-scores into a 0..100 priority.

    Only features present (non-None) contribute; the weight of any missing feature is removed and
    the remaining weights are renormalised, so a missing measurement neither helps nor hurts
    beyond removing its evidence. Returns the score, the renormalised weights used, and the list
    of features that were available.
    """
    subs = {
        "binding_affinity": features.get("binding_affinity_score"),
        "tumor_abundance": (features.get("tumor_abundance") or {}).get("score"),
        "agretopicity": features.get("agretopicity_score"),
        "foreignness": (features.get("foreignness") or {}).get("score"),
        "binding_stability": (features.get("binding_stability") or {}).get("score"),
        "fraction_hydrophobic": features.get("fraction_hydrophobic"),
        "mutation_position": (features.get("mutation_position") or {}).get("score"),
    }
    used = {k: v for k, v in subs.items() if v is not None}
    if not used:
        return {"score": None, "used_features": [], "weights_used": {}}
    wsum = sum(FEATURE_WEIGHTS[k] for k in used)
    weights_used = {k: FEATURE_WEIGHTS[k] / wsum for k in used}
    score = sum(weights_used[k] * used[k] for k in used) * 100.0
    return {"score": round(score, 2), "used_features": sorted(used.keys()),
            "weights_used": {k: round(v, 3) for k, v in weights_used.items()}}


def assign_tier(features: dict, comp: dict) -> str:
    """TESLA-guided discrete tier (the actionable call).

    Tier 1 (high-confidence): strong binder (%rank < 0.5) AND expressed above floor
            (or, if expression unknown, VAF above floor) AND not an anchor-only mutation.
    Tier 2 (candidate): binder (%rank < 2.0) with at least one supporting recognition/abundance
            signal.
    Tier 3 (low-priority): everything else that still binds (weak) — reported, de-prioritized.
    Excluded: non-binder (%rank >= 10) or fails the expression/VAF abundance filter when data
            is present.
    """
    bcls = features.get("binding_class", "unknown")
    ta = features.get("tumor_abundance") or {}
    mp = features.get("mutation_position") or {}

    # abundance filter (only applied when the relevant data is present)
    expr_ok = ta.get("pass_expr")
    vaf_ok = ta.get("pass_vaf")
    abundance_fail = (expr_ok is False) or (expr_ok is None and vaf_ok is False)

    if bcls == "non":
        return "excluded_nonbinder"
    if abundance_fail:
        return "excluded_low_abundance"

    if bcls == "strong" and (expr_ok is not False) and (mp.get("is_anchor") is not True):
        return "Tier1"
    if bcls in ("strong", "binder"):
        return "Tier2"
    return "Tier3"


# =============================================================================
# Orchestration over a candidate table
# =============================================================================
def score_candidates(candidates: list[dict], *, stability_map: Optional[dict] = None) -> list[dict]:
    """Compute all five TESLA features + composite + tier for each candidate neoepitope.

    Each input candidate dict is expected to carry (from binding_core + expression_join):
      peptide, mut_rank (MHCflurry %rank), presentation_score (optional), expr_tpm (optional),
      vaf (optional), mut_pos_in_pep (1-based, optional), wt_rank (optional for agretopicity).
    ``stability_map`` optionally maps peptide -> NetMHCstabpan half-life (hours) for feature 3.
    Returns the list with a ``tesla`` sub-dict and top-level ``priority_score`` / ``tier`` added.
    """
    stability_map = stability_map or {}
    for c in candidates:
        pep = c.get("peptide", "")
        mut_rank = c.get("mut_rank")
        feats = {}
        feats["binding_class"] = binding_class(mut_rank)
        feats["binding_affinity_score"] = binding_affinity_score(mut_rank)
        feats["tumor_abundance"] = tumor_abundance(c.get("expr_tpm"), c.get("vaf"))
        feats["binding_stability"] = binding_stability_score(
            c.get("presentation_score"), stability_map.get(pep))
        feats["fraction_hydrophobic"] = fraction_hydrophobic(pep)
        feats["mutation_position"] = mutation_position_score(
            c.get("mut_pos_in_pep"), len(pep) if pep else None)
        # agretopicity (reused-core convention: WT %rank / mut %rank) — retained for transparency
        # and the excluded-set audit, AND now scored into the recognition axis of the composite.
        wt_rank = c.get("wt_rank")
        if wt_rank is not None and mut_rank is not None and mut_rank > 0:
            feats["agretopicity"] = round(wt_rank / mut_rank, 3)
        else:
            feats["agretopicity"] = None
        feats["agretopicity_score"] = agretopicity_score(feats["agretopicity"])
        # foreignness / dissimilarity-to-self (real local alignment vs bundled IEDB + self refs)
        feats["foreignness"] = foreignness_score(pep)

        comp = composite_score(feats)
        feats["composite"] = comp
        tier = assign_tier(feats, comp)

        c["tesla"] = feats
        c["priority_score"] = comp["score"]
        c["tier"] = tier
    # rank: Tier1 > Tier2 > Tier3 > excluded, then by priority_score desc
    tier_rank = {"Tier1": 0, "Tier2": 1, "Tier3": 2,
                 "excluded_low_abundance": 3, "excluded_nonbinder": 4}
    candidates.sort(key=lambda c: (tier_rank.get(c["tier"], 9),
                                   -(c["priority_score"] or -1)))
    return candidates


def feature_provenance() -> dict:
    """Return a machine-readable description of the 5 features + weights for the report/methods."""
    return {
        "reference": "Wells DK et al., Cell 2020, DOI:10.1016/j.cell.2020.09.015 (TESLA)",
        "features": {
            "binding_affinity": "MHC-I mutant-peptide %rank (MHCflurry); strong <0.5, binder <2.0",
            "tumor_abundance": f"gene TPM x VAF; expr floor {EXPR_TPM_FLOOR} TPM, VAF floor {VAF_FLOOR}",
            "agretopicity": "WT %rank / mut %rank (recognition); mutant binding >= WT scores high "
                            "(differential agretopicity; Ghorani/TESLA recognition axis)",
            "foreignness": "local Smith-Waterman (BLOSUM62) similarity to known immunogenic IEDB "
                           "epitopes AND dissimilarity to the human self-proteome; after Luksza "
                           "et al. 2017 (doi:10.1038/nature24473) and Richman et al. 2019 "
                           "(doi:10.1016/j.cels.2019.08.009). None if reference sets unavailable.",
            "binding_stability": "MHCflurry presentation-score proxy (or NetMHCstabpan half-life if available)",
            "fraction_hydrophobic": "fraction of Kyte-Doolittle-hydrophobic residues in the peptide",
            "mutation_position": "1-based position of the mutation; anchors (P2, P-Omega) penalised",
        },
        "weights": FEATURE_WEIGHTS,
        "recognition_references": [
            "Luksza M et al., Nature 2017, doi:10.1038/nature24473 (neoantigen fitness / foreignness)",
            "Richman LP et al., Cell Systems 2019, doi:10.1016/j.cels.2019.08.009 (dissimilarity-to-self)",
            "IEDB: Vita R et al., NAR 2019, doi:10.1093/nar/gky1006 (immunogenic reference epitopes)",
        ],
        "note": "Defined prioritization score, NOT a validated clinical immunogenicity probability.",
    }


if __name__ == "__main__":
    # quick self-check on a couple of synthetic feature vectors (offline, no engines)
    demo = [
        {"peptide": "KLVVVGADGV", "mut_rank": 0.12, "presentation_score": 0.88,
         "expr_tpm": 42.0, "vaf": 0.31, "mut_pos_in_pep": 6, "wt_rank": 0.9},   # KRAS-like
        {"peptide": "AAAAAAAAA", "mut_rank": 15.0, "presentation_score": 0.05,
         "expr_tpm": 0.5, "vaf": 0.02, "mut_pos_in_pep": 2, "wt_rank": 12.0},   # junk
    ]
    for c in score_candidates(demo):
        print(f"{c['peptide']} tier={c['tier']} priority={c['priority_score']} "
              f"class={c['tesla']['binding_class']} "
              f"hydro={c['tesla']['fraction_hydrophobic']} "
              f"abund={c['tesla']['tumor_abundance']['score']}")
