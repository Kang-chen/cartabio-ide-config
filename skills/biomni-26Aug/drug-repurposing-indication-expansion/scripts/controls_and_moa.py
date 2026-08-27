"""Positive/negative control checks + MOA over-representation among top reversers.

Disease-agnostic. Two independent validations of the ranked list:

1. CONTROL CHECK -- given user-provided (or agent-curated) lists of drugs EXPECTED to
   reverse the disease (e.g. standard-of-care, mechanistically antifibrotic) and drugs
   EXPECTED to mimic/induce it (e.g. a known disease-inducing agent), report each
   control's score and whether its direction matches expectation. Report honestly when a
   control is ABSENT from the library (cannot be scored) or scores unexpectedly. This is
   the strongest internal validity check (worked example: bleomycin, the canonical
   fibrosis inducer, correctly scored as a top disease-mimic).

2. MOA OVER-REPRESENTATION -- Fisher's exact test for each mechanism-of-action term:
   is it enriched among the significant approved reversers vs the approved-with-signature
   background? BH-FDR corrected. Small numbers usually mean nominal-only enrichment;
   report honestly.

Public API:
  check_controls(annotated_df, expected_reversers, expected_mimics, fdr_thresh=0.05) -> DataFrame
  controls_verdict(controls_df, fdr_thresh=0.05) -> dict
  moa_enrichment(annotated_df, fdr_thresh=0.05) -> DataFrame
"""
import re
import numpy as np
import pandas as pd
from scipy.stats import fisher_exact


def _norm(s):
    if not isinstance(s, str):
        return ""
    s = s.lower().strip()
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def check_controls(annotated_df, expected_reversers=None, expected_mimics=None, fdr_thresh=0.05):
    """Report score/direction of each named control and whether it matches expectation.

    expected_reversers / expected_mimics: lists of drug names (matched leniently against
    the 'drug' column). Returns a tidy DataFrame with one row per control.

    The verdict is SIGNIFICANCE-AWARE and three-valued:
      'yes'                       -- direction matches expectation AND that direction is
                                     significant (the relevant FDR < fdr_thresh).
      'no (significant opposite)' -- the OPPOSITE direction is significant (e.g. a control
                                     expected as a reverser is a significant mimic).
      'inconclusive (n.s.)'       -- neither direction is significant; the sign alone is
                                     not enough to claim a match.

    'yes' is unreachable from a sign alone — a near-zero score at FDR ~1.0 becomes
    'inconclusive (n.s.)', not 'yes'. This kills the defect where a control was marked
    matching at fdr 0.99 purely on the sign of a near-zero score.

    Output columns: control, expected, present, S_reversal, p_reversal, p_mimic,
    fdr_reversal, fdr_mimic, significant, direction, matches_expectation.
    """
    expected_reversers = expected_reversers or []
    expected_mimics = expected_mimics or []
    df = annotated_df.copy()
    df["_n"] = df["drug"].map(_norm)
    rows = []

    def lookup(name, expected):
        nn = _norm(name)
        hit = df[df["_n"] == nn]
        if len(hit) == 0:
            # try contains match
            hit = df[df["_n"].str.contains(re.escape(nn), na=False)] if nn else df.iloc[0:0]
        if len(hit) == 0:
            return dict(control=name, expected=expected, present=False, S_reversal=np.nan,
                        p_reversal=np.nan, p_mimic=np.nan,
                        fdr_reversal=np.nan, fdr_mimic=np.nan,
                        significant=False, direction="absent",
                        matches_expectation="n/a (not in library)")
        r = hit.sort_values("S_reversal", key=lambda s: s.abs(), ascending=False).iloc[0]
        s_rev = float(r["S_reversal"])
        direction = "reverser" if s_rev > 0 else "mimic"

        # Carry through p-values and FDRs when present
        p_rev = float(r["p_reversal"]) if "p_reversal" in r.index and pd.notna(r.get("p_reversal")) else np.nan
        p_mim = float(r["p_mimic"]) if "p_mimic" in r.index and pd.notna(r.get("p_mimic")) else np.nan
        fdr_rev = float(r["fdr_reversal"]) if "fdr_reversal" in r.index and pd.notna(r.get("fdr_reversal")) else np.nan
        fdr_mim = float(r["fdr_mimic"]) if "fdr_mimic" in r.index and pd.notna(r.get("fdr_mimic")) else np.nan

        # Determine which directions are significant
        rev_sig = (not np.isnan(fdr_rev)) and fdr_rev < fdr_thresh
        mim_sig = (not np.isnan(fdr_mim)) and fdr_mim < fdr_thresh
        # significant = at least one direction is significant
        significant = bool(rev_sig or mim_sig)

        # Three-valued verdict
        if direction == expected:
            # matches direction — but is it significant?
            if expected == "reverser" and rev_sig:
                verdict = "yes"
            elif expected == "mimic" and mim_sig:
                verdict = "yes"
            elif (expected == "reverser" and mim_sig) or (expected == "mimic" and rev_sig):
                # opposite direction is significant
                verdict = "no (significant opposite)"
            else:
                verdict = "inconclusive (n.s.)"
        else:
            # direction does not match expectation
            if (expected == "reverser" and mim_sig) or (expected == "mimic" and rev_sig):
                verdict = "no (significant opposite)"
            else:
                verdict = "inconclusive (n.s.)"

        return dict(control=r["drug"], expected=expected, present=True,
                    S_reversal=s_rev, p_reversal=p_rev, p_mimic=p_mim,
                    fdr_reversal=fdr_rev, fdr_mimic=fdr_mim,
                    significant=significant, direction=direction,
                    matches_expectation=verdict)

    for d in expected_reversers:
        rows.append(lookup(d, "reverser"))
    for d in expected_mimics:
        rows.append(lookup(d, "mimic"))
    return pd.DataFrame(rows)


def controls_verdict(controls_df, fdr_thresh=0.05):
    """Summarise a controls DataFrame into a pass/weak/fail verdict.

    status:
      'fail' -- any control is 'no (significant opposite)', OR zero present controls
                are 'yes' (the panel provides no positive validation).
      'weak' -- any inconclusive AND fewer than half of the present controls are 'yes'.
      'pass' -- otherwise.

    failures: list of human-readable strings naming each offending control with its
    S_reversal and FDR, suitable for pasting straight into a report banner.

    Returns dict(status, n_yes, n_no, n_inconclusive, n_absent, failures, summary).
    """
    if controls_df is None or len(controls_df) == 0:
        return dict(status="pass", n_yes=0, n_no=0, n_inconclusive=0, n_absent=0,
                    failures=[], summary="No controls specified.")

    present = controls_df[controls_df["present"]].copy() if "present" in controls_df.columns else controls_df.copy()
    n_absent = int((~controls_df["present"]).sum()) if "present" in controls_df.columns else 0
    n_present = len(present)

    def _count(val):
        return int((present["matches_expectation"] == val).sum()) if "matches_expectation" in present.columns else 0

    n_yes = _count("yes")
    n_no = _count("no (significant opposite)")
    n_inconclusive = _count("inconclusive (n.s.)")

    failures = []
    for _, r in present.iterrows():
        me = str(r.get("matches_expectation", ""))
        if me == "no (significant opposite)":
            s_rev = r.get("S_reversal", np.nan)
            fdr_rev = r.get("fdr_reversal", np.nan)
            fdr_mim = r.get("fdr_mimic", np.nan)
            fdr_str = f"fdr_reversal={fdr_rev:.4g}" if not pd.isna(fdr_rev) else "fdr_reversal=n/a"
            if not pd.isna(fdr_mim):
                fdr_str += f", fdr_mimic={fdr_mim:.4g}"
            s_str = f"{s_rev:.1f}" if not pd.isna(s_rev) else "n/a"
            failures.append(
                f"{r.get('control','?')} (expected {r.get('expected','?')}, "
                f"S_reversal={s_str}, {fdr_str}): significant opposite direction")

    # Determine status
    if n_no > 0 or (n_present > 0 and n_yes == 0):
        status = "fail"
    elif n_inconclusive > 0 and n_yes < n_present / 2:
        status = "weak"
    else:
        status = "pass"

    summary = (f"Controls verdict: {status} ({n_yes} yes, {n_no} no-significant-opposite, "
               f"{n_inconclusive} inconclusive, {n_absent} absent).")
    return dict(status=status, n_yes=n_yes, n_no=n_no, n_inconclusive=n_inconclusive,
                n_absent=n_absent, failures=failures, summary=summary)


def moa_enrichment(annotated_df, fdr_thresh=0.05):
    """Fisher over-representation of MOA terms among significant approved reversers."""
    df = annotated_df[annotated_df["approved"] & annotated_df["moa"].notna()].copy()
    if len(df) == 0:
        return pd.DataFrame(columns=["moa", "k_top", "n_top", "k_bg", "n_bg", "odds_ratio", "p", "fdr"])
    top = df[(df["S_reversal"] > 0) & (df["fdr_reversal"] < fdr_thresh)]
    # explode pipe-delimited MOA terms
    def moa_terms(s):
        return [t.strip() for t in str(s).split("|") if t.strip()]
    df["moa_list"] = df["moa"].map(moa_terms)
    top_set = set(top.index)
    n_top = len(top)
    n_bg = len(df)
    # count term occurrences
    from collections import Counter
    top_counts = Counter()
    bg_counts = Counter()
    for idx, row in df.iterrows():
        for term in set(row["moa_list"]):
            bg_counts[term] += 1
            if idx in top_set:
                top_counts[term] += 1
    recs = []
    for term, kbg in bg_counts.items():
        ktop = top_counts.get(term, 0)
        # 2x2: [[in_top_with_term, in_top_without],[in_bg_with, in_bg_without]]
        a = ktop
        b = n_top - ktop
        c = kbg - ktop
        d = (n_bg - n_top) - (kbg - ktop)
        try:
            orr, p = fisher_exact([[a, b], [c, d]], alternative="greater")
        except Exception:
            orr, p = np.nan, 1.0
        recs.append(dict(moa=term, k_top=ktop, n_top=n_top, k_bg=kbg, n_bg=n_bg,
                         odds_ratio=orr, p=p))
    res = pd.DataFrame(recs).sort_values("p")
    if len(res):
        try:
            from statsmodels.stats.multitest import multipletests
            res["fdr"] = multipletests(res["p"], method="fdr_bh")[1]
        except Exception:
            res["fdr"] = (res["p"] * len(res)).clip(upper=1.0)
    return res.reset_index(drop=True)
