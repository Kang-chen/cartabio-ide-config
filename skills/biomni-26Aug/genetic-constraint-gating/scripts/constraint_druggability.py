"""
constraint_druggability.py — turn gnomAD LoF-constraint metrics into a drug-target
interpretation for each gene.

The prompt this skill serves asks not just "is the gene LoF-intolerant?" but
"what does that intolerance mean for the gene's suitability / risk as a drug
target?". This module supplies that layer. Every field is derived deterministically
from the constraint metrics (LOEUF, pLI, gnomAD LOEUF percentile, LoF Z, obs/exp
LoF) plus the gene's ClinGen-curated mode of inheritance — so it generalises to any
gene list and never hardcodes gene-specific claims or invents drug names.

Core interpretive principle (stated in the report too):
  gnomAD constraint measures selection against GERMLINE HETEROZYGOUS loss-of-function
  — i.e. whether losing one copy in every cell of a developing human is tolerated.
  It is a statement about organism-level / developmental essentiality, NOT about
  whether inhibiting the gene product in a specific adult tissue or tumour is
  therapeutically viable. A strong LoF-intolerant flag is therefore a SAFETY /
  MODALITY CAUTION for systemic full inhibition or degradation, not a veto on the
  gene as a target. Constrained tumour-suppressor-like genes are typically drugged
  through a DEPENDENCY created by their loss (synthetic lethality / paralog /
  downstream node), not by inhibiting the gene itself.

Public API:
  annotate_druggability(row_dict) -> dict of interpretation fields
  add_druggability_columns(df)    -> df with the interpretation columns appended
  tier_color(tier)               -> hex colour for figures/report
"""

import math

# ---- KO-tolerance tiers (ordinal; 0 = most intolerant) --------------------- #
TIER_ORDER = [
    "Very low (near-essential)",
    "Low (LoF-intolerant)",
    "Intermediate",
    "Tolerant",
    "Not determined",
]

# colour per tier (Okabe-Ito-derived; red->blue severity ramp, colourblind-safe)
_TIER_COLOR = {
    "Very low (near-essential)": "#7A0177",   # deep magenta = most constrained
    "Low (LoF-intolerant)":      "#D55E00",   # vermillion
    "Intermediate":              "#E69F00",   # orange
    "Tolerant":                  "#009E73",   # green = knockout-tolerated
    "Not determined":            "#999999",
}


def tier_color(tier):
    return _TIER_COLOR.get(tier, "#999999")


def _num(x):
    try:
        if x is None:
            return None
        f = float(x)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def ko_tolerance_tier(loeuf, pli, loeuf_pct):
    """
    Bin a gene's tolerance to heterozygous LoF from its constraint metrics.

    loeuf     : LOEUF (oe_lof_upper) on the flagging basis (v2.1.1 preferred)
    pli       : pLI on the same basis
    loeuf_pct : gnomAD LOEUF percentile (oe_lof_percentile), 0..1 where lower = more
                constrained; gnomAD's own recommended way to bin constraint. Optional.

    Returns (tier_str, rationale_str).
    """
    loeuf = _num(loeuf); pli = _num(pli); loeuf_pct = _num(loeuf_pct)
    if loeuf is None and pli is None:
        return "Not determined", "no LOEUF or pLI available"

    # gnomAD's own decile is the most principled signal when present.
    decile = None
    if loeuf_pct is not None:
        decile = int(loeuf_pct * 10)  # 0 = most constrained decile

    reasons = []
    # Very low tolerance / near-essential: top constraint decile, or extreme LOEUF+pLI
    very_low = False
    if decile is not None and decile == 0:
        very_low = True; reasons.append("in gnomAD's most-constrained LOEUF decile")
    if (loeuf is not None and loeuf < 0.20) and (pli is not None and pli >= 0.90):
        very_low = True; reasons.append(f"LOEUF {loeuf:.3f} < 0.20 with pLI {pli:.2f}")
    if very_low:
        return "Very low (near-essential)", "; ".join(reasons)

    # Low tolerance: standard LoF-intolerant flag
    if (loeuf is not None and loeuf < 0.35) or (pli is not None and pli >= 0.90):
        drv = []
        if loeuf is not None and loeuf < 0.35:
            drv.append(f"LOEUF {loeuf:.3f} < 0.35")
        if pli is not None and pli >= 0.90:
            drv.append(f"pLI {pli:.2f} \u2265 0.90")
        return "Low (LoF-intolerant)", "; ".join(drv)

    # Intermediate: LOEUF between cutoff and 0.6 (or mid deciles)
    if loeuf is not None and loeuf < 0.60:
        return "Intermediate", f"LOEUF {loeuf:.3f} in 0.35\u20130.60 (moderate constraint)"
    if decile is not None and decile <= 3:
        return "Intermediate", f"LOEUF decile {decile} (moderate constraint)"

    # Otherwise tolerant
    if loeuf is not None:
        return "Tolerant", f"LOEUF {loeuf:.3f} \u2265 0.60 (LoF variation tolerated in population)"
    return "Tolerant", "no strong LoF depletion"


def annotate_druggability(row):
    """
    row : dict-like with keys LOEUF_v2, pLI_v2, LOEUF_pct_v2, LOEUF_v4, pLI_v4,
          LOEUF_pct_v4, lof_z_v2, obs_lof_v2, exp_lof_v2, inheritance,
          disease_source, LoF_intolerant, flag_basis.

    Returns a dict with the interpretation fields (all strings/values derived from
    the inputs; no fabricated gene-specific content).
    """
    basis = row.get("flag_basis", "v2.1.1")
    if basis == "v4.1":
        loeuf, pli, pct = row.get("LOEUF_v4"), row.get("pLI_v4"), row.get("LOEUF_pct_v4")
    else:
        loeuf, pli, pct = row.get("LOEUF_v2"), row.get("pLI_v2"), row.get("LOEUF_pct_v2")
    loeuf = _num(loeuf); pli = _num(pli)

    tier, rationale = ko_tolerance_tier(loeuf, pli, pct)
    intolerant = row.get("LoF_intolerant") == "Yes"
    moi = (row.get("inheritance") or "").lower()
    grounded = row.get("disease_source") not in (None, "none", "")
    dominant = ("dominant" in moi) or ("x-linked" in moi and "recessive" not in moi)

    # ---- systemic-modality risk (full inhibition / degradation, systemic route) ----
    if tier == "Very low (near-essential)":
        sys_risk = "High"
        sys_note = ("Population selection removes essentially all heterozygous LoF. Systemic, "
                    "complete inhibition or degradation risks on-target toxicity in normal "
                    "dosage-sensitive tissues.")
    elif tier == "Low (LoF-intolerant)":
        sys_risk = "High"
        sys_note = ("Significant depletion of heterozygous LoF. Systemic full knockdown carries "
                    "on-target safety risk; favour a therapeutic window (partial/tunable or "
                    "tissue/tumour-restricted) over blunt systemic ablation.")
    elif tier == "Intermediate":
        sys_risk = "Moderate"
        sys_note = ("Partial LoF depletion. Systemic inhibition may be tolerable but warrants "
                    "careful margin/dose monitoring for on-target effects.")
    elif tier == "Tolerant":
        sys_risk = "Lower"
        sys_note = ("Heterozygous LoF is tolerated in the population, consistent with a wider "
                    "on-target safety margin for systemic inhibition or knockdown.")
    else:
        sys_risk = "Undetermined"
        sys_note = "Constraint not determined; on-target safety margin cannot be inferred from gnomAD."

    # dominant / X-linked disease sharpens the on-target caution for intolerant genes
    if intolerant and dominant and grounded:
        sys_note += (" A curated dominant/X-linked LoF disease mechanism reinforces that "
                     "one-copy loss is already pathogenic in humans.")

    # ---- recommended target strategy (generalised, mechanism-agnostic) ----
    if tier in ("Very low (near-essential)", "Low (LoF-intolerant)"):
        strategy = ("Prefer indirect strategies that exploit a dependency created by the gene's "
                    "loss (synthetic lethality, paralog dependency, or a downstream node) rather "
                    "than systemic direct inhibition; if direct inhibition is required, engineer a "
                    "therapeutic window (tumour/tissue-restricted delivery, partial or tunable "
                    "degradation).")
        actionability = "Drug the dependency, not the gene"
    elif tier == "Intermediate":
        strategy = ("Direct inhibition may be feasible with a monitored safety margin; a "
                    "dependency/synthetic-lethal angle remains a useful fallback if on-target "
                    "toxicity emerges.")
        actionability = "Direct inhibition possible with margin"
    elif tier == "Tolerant":
        strategy = ("Direct inhibition/knockdown of the gene product is the natural modality; the "
                    "population tolerates LoF, so on-target dose-limiting toxicity is less likely "
                    "(efficacy and delivery become the main questions).")
        actionability = "Direct inhibition favourable"
    else:
        strategy = "Insufficient constraint information to recommend a modality."
        actionability = "Undetermined"

    # a compact one-liner combining the two axes
    verdict = f"{tier} KO-tolerance \u2192 systemic on-target risk: {sys_risk}"

    return {
        "ko_tolerance_tier": tier,
        "ko_tolerance_rationale": rationale,
        "systemic_target_risk": sys_risk,
        "systemic_target_note": sys_note,
        "target_strategy": strategy,
        "actionability": actionability,
        "druggability_verdict": verdict,
    }


def add_druggability_columns(df):
    """Append the druggability interpretation columns to an analyze_genes() DataFrame."""
    cols = ["ko_tolerance_tier", "ko_tolerance_rationale", "systemic_target_risk",
            "systemic_target_note", "target_strategy", "actionability", "druggability_verdict"]
    recs = []
    for _, r in df.iterrows():
        if r.get("LoF_intolerant") in ("Yes", "No"):
            recs.append(annotate_druggability(r.to_dict()))
        else:  # unresolved / no record -> leave interpretation blank but present
            recs.append({c: ("Not determined" if c in
                             ("ko_tolerance_tier", "systemic_target_risk", "actionability")
                             else "gene not resolved / no gnomAD record") for c in cols})
    for c in cols:
        df[c] = [rec[c] for rec in recs]
    return df
