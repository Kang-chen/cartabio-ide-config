"""
Open-licensed, locally-runnable sgRNA scoring (replaces the CRISPick / Broad-GPP-portal tier).

This module scores guides you ALREADY HAVE (from Addgene Tier 1, the literature, or de-novo
Tier 2) with two openly-licensed methods that run entirely on this machine -- NO calls to the
CRISPick web tool or any GPP portal:

  * On-target efficiency -> Rule Set 3 (`rs3`, Broad GPP-RND lab; BSD-licensed).
      Rule Set 3 (DeWeirdt et al., Nat Biotechnol 2022) is the lab's own successor to
      Rule Set 2 / Azimuth. The original Azimuth/Rule-Set-2 package is Python-2-only and is
      uninstallable on modern Python -- even Bioconductor `crisprScore` dropped Azimuth for this
      reason and points users to Rule Set 3. We therefore use Rule Set 3 as the open, maintained
      stand-in and label it as such (rather than silently substituting).

  * Off-target specificity -> CFD (Cutting Frequency Determination; Doench et al.,
      Nat Biotechnol 2016; PMID 26780180). The CFD algorithm + its two published weight tables
      (`references/resource/cfd/*.pkl`) are bundled so this runs offline.

SCOPE (deliberate, per the skill's "score-a-guide" design):
  CFD scores a guide *against a specific candidate off-target sequence*. A genome-wide
  specificity number requires first ENUMERATING off-targets across a genome (a Cas-OFFinder /
  bowtie-style alignment against a genome FASTA), which is out of scope here. So:
    - If you pass explicit candidate off-target sequences, we compute per-site CFD and an
      aggregate CRISPOR-style specificity score.
    - If you do NOT, off-target output honestly reports that no genome-wide search was performed
      (we do not fabricate a specificity number).

Attribution to preserve in user methods:
  - On-target: Rule Set 3 -- DeWeirdt PC, et al. Nat Biotechnol. 2022;40(1):94-104
    (`rs3`, https://github.com/gpp-rnd/rs3, BSD). Successor to Rule Set 2/Azimuth
    (Doench JG, et al. Nat Biotechnol. 2016;34(2):184-191).
  - Off-target: CFD -- Doench JG, et al. Nat Biotechnol. 2016;34(2):184-191. PMID 26780180.
"""

from __future__ import annotations

import os
import pickle
import warnings

_HERE = os.path.dirname(os.path.abspath(__file__))
_CFD_DIR = os.path.normpath(os.path.join(_HERE, "..", "references", "resource", "cfd"))

# Rule Set 3 native z-scores in practice fall roughly in [-3, +3]; used to map to a 0-100
# percentile for readability. This is a display convenience, not a recalibration of the model.
_RS3_Z_LO, _RS3_Z_HI = -3.0, 3.0


# --------------------------------------------------------------------------------------------
# On-target: Rule Set 3
# --------------------------------------------------------------------------------------------
def _z_to_percentile(z: float) -> float:
    """Map an RS3 z-score to a 0-100 display scale by clamping to [_RS3_Z_LO, _RS3_Z_HI]."""
    frac = (z - _RS3_Z_LO) / (_RS3_Z_HI - _RS3_Z_LO)
    return round(100.0 * min(1.0, max(0.0, frac)), 1)


def _pad_context(seq: str) -> tuple[str, bool]:
    """
    Rule Set 3's sequence model expects a 30-mer: 4 nt 5' context + 20 nt protospacer +
    3 nt PAM + 3 nt 3' context. If the caller only has a bare 20-mer protospacer (or a 23-mer
    protospacer+PAM), pad the missing flanks with a neutral 'A' and flag the score as
    approximate. Returns (context_30mer, context_incomplete_flag).
    """
    s = seq.upper().strip()
    if len(s) == 30:
        return s, False
    # 23-mer = 20 nt protospacer + 3 nt PAM -> pad 4 nt (5') + 3 nt (3').
    if len(s) == 23:
        return ("AAAA" + s + "AAA"), True
    # 20-mer protospacer only -> assume canonical NGG PAM is unknown; pad 4 + (GG-less) + 3.
    if len(s) == 20:
        # Insert a placeholder 'AGG' PAM so the model sees a plausible NGG; flagged approximate.
        return ("AAAA" + s + "AGG" + "AAA"), True
    # Any other length: left as-is if 30, else pad/truncate to 30 and flag.
    if len(s) < 30:
        return (s + "A" * (30 - len(s))), True
    return s[:30], True


def score_on_target(
    contexts: list[str],
    tracr: str = "Hsu2013",
) -> list[dict]:
    """
    Rule Set 3 on-target efficiency for a list of guides.

    Parameters
    ----------
    contexts : list[str]
        Ideally 30-mer contexts (4 nt + 20 nt protospacer + 3 nt PAM + 3 nt). Bare 20-mer
        protospacers or 23-mers (protospacer+PAM) are accepted with neutral-flank padding and
        flagged `context_incomplete=True` (score is then approximate).
    tracr : {"Hsu2013", "Chen2013"}
        tracrRNA variant the RS3 sequence model was conditioned on. Hsu2013 is the common default.

    Returns
    -------
    list[dict], one per input, each with:
        rs3_z (float), on_target_percentile (0-100), context_incomplete (bool),
        method ("Rule Set 3"), tracr.
    """
    from rs3.seq import predict_seq  # imported lazily so the rest of the skill works without rs3

    padded, flags = [], []
    for c in contexts:
        p, f = _pad_context(c)
        padded.append(p)
        flags.append(f)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        zs = predict_seq(padded, sequence_tracr=tracr)

    out = []
    for z, f in zip(zs, flags):
        z = float(z)
        out.append({
            "rs3_z": round(z, 3),
            "on_target_percentile": _z_to_percentile(z),
            "context_incomplete": f,
            "method": "Rule Set 3",
            "tracr": tracr,
        })
    return out


# --------------------------------------------------------------------------------------------
# Off-target: CFD (Doench 2016)
# --------------------------------------------------------------------------------------------
_MM_SCORES = None
_PAM_SCORES = None


def _load_cfd():
    global _MM_SCORES, _PAM_SCORES
    if _MM_SCORES is None or _PAM_SCORES is None:
        with open(os.path.join(_CFD_DIR, "mismatch_score.pkl"), "rb") as fh:
            _MM_SCORES = pickle.load(fh)
        with open(os.path.join(_CFD_DIR, "pam_scores.pkl"), "rb") as fh:
            _PAM_SCORES = pickle.load(fh)
    return _MM_SCORES, _PAM_SCORES


def _revcom(s: str) -> str:
    comp = {"A": "T", "C": "G", "G": "C", "T": "A", "U": "A"}
    return "".join(comp[b] for b in reversed(s.upper()))


def calc_cfd(wt: str, sg: str, pam: str) -> float:
    """
    CFD score of an off-target. BSD implementation (source provided by J. Doench via CRISPOR).

    wt  : 20 nt on-target (intended) protospacer, 5'->3'
    sg  : 20 nt off-target protospacer, 5'->3'
    pam : 2 nt (the NGG PAM's 'GG' dinucleotide, i.e. positions 2-3 of the PAM)

    Returns a score in (0, 1]; 1.0 = identical (max cutting), lower = less likely off-target cut.

    >>> round(calc_cfd("GGGGGGGGGGGGGGGGGGGG", "GGGGGGGGGGGGGGGGGGGG", "GG"), 3)
    1.0
    """
    mm_scores, pam_scores = _load_cfd()
    score = 1.0
    sg = sg.replace("T", "U")
    wt = wt.replace("T", "U")
    for i, (w, s) in enumerate(zip(wt, sg)):
        if w == s:
            continue
        key = "r" + w + ":d" + _revcom(s) + "," + str(i + 1)
        score *= mm_scores[key]
    score *= pam_scores[pam.upper()]
    return score


def score_off_target(
    spacer: str,
    off_targets: list[dict] | None = None,
) -> dict:
    """
    Off-target specificity for one guide using CFD.

    Parameters
    ----------
    spacer : str
        The intended 20 nt protospacer (5'->3').
    off_targets : list[dict] | None
        Candidate off-targets to score. Each dict: {"protospacer": <20nt>, "pam": <2nt GG>}.
        These must come from YOU / an external genome search (Cas-OFFinder, CRISPOR, bowtie) --
        this module does not enumerate genome-wide off-targets.

    Returns
    -------
    dict:
        If off_targets is None/empty:
            {"specificity": None, "n_offtargets": 0, "genome_search": False, "note": ...}
        Else:
            {"specificity": <0-100 CRISPOR-style aggregate>, "n_offtargets": int,
             "genome_search": True, "cfd_scores": [...], "note": ...}
    """
    if not off_targets:
        return {
            "specificity": None,
            "n_offtargets": 0,
            "genome_search": False,
            "cfd_scores": [],
            "note": ("No genome-wide off-target search performed (out of scope: needs a genome "
                     "FASTA + aligner such as Cas-OFFinder/CRISPOR). Provide candidate "
                     "off-target sequences to compute CFD specificity."),
        }

    cfds = []
    for ot in off_targets:
        cfds.append(calc_cfd(spacer, ot["protospacer"], ot.get("pam", "GG")))
    # CRISPOR-style aggregate specificity: 100 / (1 + sum of off-target CFDs).
    specificity = round(100.0 / (1.0 + sum(cfds)), 1)
    return {
        "specificity": specificity,
        "n_offtargets": len(cfds),
        "genome_search": True,
        "cfd_scores": [round(c, 4) for c in cfds],
        "note": ("CFD specificity over supplied candidate off-targets "
                 "(higher = more specific; 100 = no scored off-targets)."),
    }


# --------------------------------------------------------------------------------------------
# Convenience: score a batch of guide records (any tier) for export.
# --------------------------------------------------------------------------------------------
def score_guides(
    guides: list[dict],
    tracr: str = "Hsu2013",
) -> list[dict]:
    """
    Annotate a list of guide records with on-target (and optional off-target) scores.

    Each input guide dict may contain:
        "sequence"    : 20-mer protospacer (required)
        "context"     : 30-mer context (optional; strongly preferred for accurate RS3)
        "off_targets" : optional list of {"protospacer","pam"} for CFD (see score_off_target)
        plus any other keys (passed through untouched, e.g. gene/source/citation).

    Returns the same list with added keys:
        on_target_percentile, rs3_z, context_incomplete, off_target_specificity,
        n_offtargets, genome_search, score_note.
    """
    contexts = [g.get("context") or g["sequence"] for g in guides]
    on = score_on_target(contexts, tracr=tracr)

    out = []
    for g, o in zip(guides, on):
        ot = score_off_target(g["sequence"], g.get("off_targets"))
        rec = dict(g)
        rec.update({
            "on_target_percentile": o["on_target_percentile"],
            "rs3_z": o["rs3_z"],
            "context_incomplete": o["context_incomplete"],
            "off_target_specificity": ot["specificity"],
            "n_offtargets": ot["n_offtargets"],
            "genome_search": ot["genome_search"],
            "score_note": ot["note"],
        })
        out.append(rec)
    return out


if __name__ == "__main__":
    # Minimal self-test (no network, no external files beyond the bundled CFD pickles).
    print("CFD identity:", round(calc_cfd("GGGGGGGGGGGGGGGGGGGG", "GGGGGGGGGGGGGGGGGGGG", "GG"), 3))
    print("CFD 1-mismatch:", round(calc_cfd("GAGTCCGAGCAGAAGAAGAA", "GAGTCCGAGCAGAAGAAGAT", "GG"), 3))
    demo = [{"gene": "DEMO", "sequence": "GAGTCCGAGCAGAAGAAGAA",
             "context": "GGGAGAGTCCGAGCAGAAGAAGAAGGGGGA"[:30]}]
    for r in score_guides(demo):
        print("scored:", {k: r[k] for k in
                          ("gene", "on_target_percentile", "rs3_z", "context_incomplete",
                           "off_target_specificity", "genome_search")})
