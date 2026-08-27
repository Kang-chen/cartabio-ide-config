"""Disproportionality statistics for spontaneous-report (FAERS) signal detection.

Given, for each (drug, adverse-event) pair, the four cells of the classic 2x2
contingency table, this module computes the standard disproportionality
measures used in pharmacovigilance:

    * ROR  - Reporting Odds Ratio (+ 95% CI)
    * PRR  - Proportional Reporting Ratio
    * chi2 - Pearson chi-square with Yates continuity correction
    * FDR  - Benjamini-Hochberg adjusted p-values (per drug)

and applies a configurable *signal* rule (default: the field-standard
EMA/van Puijenbroek-style criterion).

The 2x2 table for a given (drug D, event E) over a report universe of size N:

                     event = E        event != E        (row totals)
    drug = D            a                b               a + b   (all D reports)
    drug != D          c                d               c + d
    (col totals)     a + c            b + d             N

where:
    a = reports mentioning BOTH D and E
    b = reports mentioning D but NOT E              = n_drug_total - a
    c = reports mentioning E but NOT D              = n_event_total - a
    d = all other reports                            = N - a - b - c

IMPORTANT interpretive caveat (surface this in every report):
    Disproportionality is a measure of *differential reporting*, NOT of causal
    risk. A signal means an event is reported more often than expected for a
    drug relative to the rest of the database; it is hypothesis-generating and
    subject to reporting/notoriety/indication biases and confounding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

try:  # scipy / statsmodels are in the standard Biomni env
    from scipy.stats import chi2_contingency
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False

try:
    from statsmodels.stats.multitest import multipletests
    _HAVE_SM = True
except Exception:  # pragma: no cover
    _HAVE_SM = False


# --------------------------------------------------------------------------- #
# signal-criterion configuration (all thresholds overridable)
# --------------------------------------------------------------------------- #
@dataclass
class SignalCriteria:
    """Thresholds defining a disproportionality *signal*.

    Defaults follow the widely used combined criterion (van Puijenbroek 2002;
    EMA screening practice): a lower ROR CI bound above 1, PRR at least 2,
    chi-square at least 4 (~ p<0.05, 1 df), and a minimum case count so that
    tiny, unstable counts are not flagged.
    """
    ror_ci_lower_min: float = 1.0     # ROR 95% CI lower bound must exceed this
    prr_min: float = 2.0              # PRR threshold
    chi2_min: float = 4.0             # chi-square threshold (~p<0.05, 1 df)
    min_cases: int = 3                # minimum a (co-reported count) to be a signal
    use_fdr: bool = True              # additionally require FDR q < fdr_q
    fdr_q: float = 0.05               # BH-adjusted p-value threshold
    continuity_correction: float = 0.5  # added to all cells when any cell == 0

    # --- low-confidence flagging (does NOT remove signals; only marks them) --- #
    # A flagged signal is still a signal; ``low_confidence`` marks rows whose
    # disproportionality is fragile or likely inflated, so downstream tables /
    # figures / report can annotate (not hide) them. Two independent triggers:
    #   1. small case count  -> unstable estimate (matters mainly for rarely
    #      reported drugs; for high-volume drugs the OpenFDA top-500 reaction
    #      facet floors ``a`` well above this, so it is often a no-op).
    #   2. extreme ROR outlier -> implausibly large ROR relative to the drug's
    #      own signal distribution, a hallmark of notoriety / stimulated
    #      reporting or a mechanism/efficacy-adjacent term rather than a
    #      stable safety effect. Uses a Tukey "far-out" fence on ln(ROR)
    #      (Q3 + k*IQR) computed over the drug's genuine (non-noise) signals,
    #      AND an absolute floor so nothing is flagged when the whole
    #      distribution is legitimately high.
    min_cases_confident: int = 10     # a < this -> low_count (fragile) flag
    extreme_ror_enable: bool = True   # enable the extreme-ROR outlier flag
    extreme_ror_iqr_k: float = 3.0    # Tukey far-out multiplier on ln(ROR)
    extreme_ror_abs_floor: float = 25.0  # ROR must also exceed this to be flagged

    def describe(self) -> str:
        parts = [
            f"ROR 95% CI lower > {self.ror_ci_lower_min:g}",
            f"PRR >= {self.prr_min:g}",
            f"chi-square >= {self.chi2_min:g}",
            f"cases (a) >= {self.min_cases}",
        ]
        if self.use_fdr:
            parts.append(f"BH-FDR q < {self.fdr_q:g}")
        return "; ".join(parts)

    def describe_confidence(self) -> str:
        """Human-readable description of the low-confidence flagging rule."""
        parts = [f"case count a < {self.min_cases_confident}"]
        if self.extreme_ror_enable:
            parts.append(
                f"ROR outlier (> max({self.extreme_ror_abs_floor:g}, "
                f"Tukey Q3 + {self.extreme_ror_iqr_k:g}\u00d7IQR of ln ROR))")
        return "signals flagged low-confidence if: " + "; OR ".join(parts)


# --------------------------------------------------------------------------- #
# single 2x2 computation
# --------------------------------------------------------------------------- #
def _cc(a, b, c, d, cc: float):
    """Apply continuity correction to all cells if any cell is zero."""
    if min(a, b, c, d) == 0 and cc:
        return a + cc, b + cc, c + cc, d + cc
    return float(a), float(b), float(c), float(d)


def compute_2x2(a: float, b: float, c: float, d: float,
                cc: float = 0.5) -> Dict[str, float]:
    """Compute disproportionality measures for one 2x2 table.

    Parameters
    ----------
    a, b, c, d : cell counts (see module docstring).
    cc : continuity correction added to every cell when any cell is zero.

    Returns a dict with ror, ror_lower, ror_upper, prr, chi2, p_value plus the
    (possibly corrected) cells. Returns NaNs for degenerate tables.
    """
    out = {"a": a, "b": b, "c": c, "d": d,
           "ror": np.nan, "ror_lower": np.nan, "ror_upper": np.nan,
           "prr": np.nan, "chi2": np.nan, "p_value": np.nan}
    if any(x < 0 for x in (a, b, c, d)):
        return out  # impossible table (e.g. background smaller than drug count)

    ca, cb, cc_, cd = _cc(a, b, c, d, cc)

    # Reporting Odds Ratio and its log-normal 95% CI
    if cb > 0 and cc_ > 0 and cd > 0 and ca > 0:
        ror = (ca / cb) / (cc_ / cd)
        se = math.sqrt(1 / ca + 1 / cb + 1 / cc_ + 1 / cd)
        out["ror"] = ror
        out["ror_lower"] = math.exp(math.log(ror) - 1.96 * se)
        out["ror_upper"] = math.exp(math.log(ror) + 1.96 * se)

    # Proportional Reporting Ratio
    denom_drug = ca + cb
    denom_rest = cc_ + cd
    if denom_drug > 0 and denom_rest > 0:
        p_drug = ca / denom_drug
        p_rest = cc_ / denom_rest
        if p_rest > 0:
            out["prr"] = p_drug / p_rest

    # Pearson chi-square with Yates correction
    if _HAVE_SCIPY:
        try:
            table = np.array([[ca, cb], [cc_, cd]])
            chi2, p, _, _ = chi2_contingency(table, correction=True)
            out["chi2"] = float(chi2)
            out["p_value"] = float(p)
        except Exception:
            pass
    else:  # manual Yates-corrected chi-square fallback
        n = ca + cb + cc_ + cd
        row1, row2 = ca + cb, cc_ + cd
        col1, col2 = ca + cc_, cb + cd
        if min(row1, row2, col1, col2) > 0:
            exp = [row1 * col1 / n, row1 * col2 / n,
                   row2 * col1 / n, row2 * col2 / n]
            obs = [ca, cb, cc_, cd]
            chi2 = sum((abs(o - e) - 0.5) ** 2 / e for o, e in zip(obs, exp))
            out["chi2"] = chi2
    return out


# --------------------------------------------------------------------------- #
# table-level computation over many (drug, event) pairs
# --------------------------------------------------------------------------- #
def compute_disproportionality(
    counts: pd.DataFrame,
    n_total: int,
    drug_totals: Dict[str, int],
    event_totals: Optional[Dict[str, int]] = None,
    criteria: Optional[SignalCriteria] = None,
    drug_col: str = "drug",
    event_col: str = "event",
    a_col: str = "a",
) -> pd.DataFrame:
    """Compute disproportionality for every (drug, event) row of ``counts``.

    Parameters
    ----------
    counts : DataFrame with one row per (drug, event) and the co-report count
        ``a`` (drug AND event). Must contain ``drug_col``, ``event_col``,
        ``a_col``.
    n_total : total number of reports in the comparison universe (N). For a
        whole-FAERS comparator this is the full database size; for a custom
        comparator it is the size of that background set.
    drug_totals : {drug: total reports mentioning that drug} (a + b).
    event_totals : {event: total reports mentioning that event across the
        universe} (a + c). If None, it is derived by summing ``a`` across drugs
        for each event -- correct only when the drug set partitions the
        universe, so passing explicit whole-universe event totals is preferred.
    criteria : SignalCriteria; defaults applied when None.
    Returns the input augmented with a, b, c, d, ror, CI, prr, chi2, p_value,
    fdr (per-drug BH), and boolean ``signal``.
    """
    crit = criteria or SignalCriteria()
    df = counts.copy()

    if event_totals is None:
        event_totals = df.groupby(event_col)[a_col].sum().to_dict()

    rows: List[Dict[str, float]] = []
    for _, r in df.iterrows():
        drug = r[drug_col]
        event = r[event_col]
        a = float(r[a_col])
        n_drug = float(drug_totals.get(drug, np.nan))
        n_event = float(event_totals.get(event, np.nan))
        if math.isnan(n_drug) or math.isnan(n_event):
            stats = {"a": a, "b": np.nan, "c": np.nan, "d": np.nan,
                     "ror": np.nan, "ror_lower": np.nan, "ror_upper": np.nan,
                     "prr": np.nan, "chi2": np.nan, "p_value": np.nan}
        else:
            b = n_drug - a
            c = n_event - a
            d = n_total - a - b - c
            stats = compute_2x2(a, b, c, d, cc=crit.continuity_correction)
        merged = {**r.to_dict(), **stats}
        rows.append(merged)

    res = pd.DataFrame(rows)

    # per-drug Benjamini-Hochberg FDR on the raw chi-square p-values
    res["fdr"] = np.nan
    if crit.use_fdr and _HAVE_SM:
        for drug, idx in res.groupby(drug_col).groups.items():
            sub = res.loc[idx]
            mask = sub["p_value"].notna()
            if mask.sum() > 0:
                q = np.full(len(sub), np.nan)
                _, qvals, _, _ = multipletests(
                    sub.loc[mask, "p_value"].values, method="fdr_bh")
                q[mask.values] = qvals
                res.loc[idx, "fdr"] = q

    # apply the signal rule
    res["signal"] = _apply_signal_rule(res, crit)
    res.attrs["criteria"] = crit.describe()
    return res


def _apply_signal_rule(res: pd.DataFrame, crit: SignalCriteria) -> pd.Series:
    sig = (
        (res["ror_lower"] > crit.ror_ci_lower_min)
        & (res["prr"] >= crit.prr_min)
        & (res["chi2"] >= crit.chi2_min)
        & (res["a"] >= crit.min_cases)
    )
    if crit.use_fdr:
        sig = sig & (res["fdr"] < crit.fdr_q)
    return sig.fillna(False)


def flag_low_confidence(res: pd.DataFrame,
                        criteria: Optional[SignalCriteria] = None,
                        drug_col: str = "drug") -> pd.DataFrame:
    """Mark statistically-flagged signals whose estimate is low-confidence.

    Adds four columns (only *signal* rows can be flagged; non-signals get
    False / ""):

      * ``low_count``    - a < ``criteria.min_cases_confident`` (fragile count).
      * ``extreme_ror``  - ROR is an implausible high outlier vs. the drug's own
        genuine-signal ROR distribution (Tukey far-out fence on ln ROR:
        ROR > max(abs_floor, exp(Q3 + k*IQR))). Computed **per drug** over the
        drug's genuine (non-noise) signals so administrative/procedure noise
        does not distort the fence; requires >= 4 such signals, else only the
        absolute floor applies.
      * ``low_confidence`` - logical OR of the above.
      * ``low_confidence_reason`` - short human-readable reason(s).

    This function is idempotent and safe to call after annotation (it uses
    ``is_noise`` when present). It never changes ``signal``; downstream code
    should *annotate* low-confidence signals, not drop them.
    """
    crit = criteria or SignalCriteria()
    df = res.copy()
    n = len(df)
    sig = df["signal"] if "signal" in df.columns else pd.Series(False, index=df.index)
    sig = sig.fillna(False).astype(bool)
    is_noise = (df["is_noise"].fillna(False).astype(bool)
                if "is_noise" in df.columns else pd.Series(False, index=df.index))

    low_count = sig & (df["a"] < crit.min_cases_confident)

    extreme = pd.Series(False, index=df.index)
    thresholds: Dict[str, float] = {}
    if crit.extreme_ror_enable:
        for drug, idx in df.groupby(drug_col).groups.items():
            sub = df.loc[idx]
            genuine = sub[sig.loc[idx] & ~is_noise.loc[idx] & sub["ror"].notna()
                          & (sub["ror"] > 0)]
            thr = crit.extreme_ror_abs_floor
            if len(genuine) >= 4:
                lr = np.log(genuine["ror"].astype(float))
                q1, q3 = lr.quantile(0.25), lr.quantile(0.75)
                fence = float(np.exp(q3 + crit.extreme_ror_iqr_k * (q3 - q1)))
                thr = max(crit.extreme_ror_abs_floor, fence)
            thresholds[str(drug)] = thr
            extreme.loc[idx] = (sig.loc[idx] & (sub["ror"] >= thr)).fillna(False)

    low_conf = (low_count | extreme).fillna(False)

    def _reason(i) -> str:
        parts = []
        if bool(low_count.iloc[i]):
            parts.append(f"a<{crit.min_cases_confident}")
        if bool(extreme.iloc[i]):
            drug = str(df[drug_col].iloc[i])
            thr = thresholds.get(drug, crit.extreme_ror_abs_floor)
            parts.append(f"ROR outlier (>{thr:.1f})")
        return "; ".join(parts)

    df["low_count"] = low_count.values
    df["extreme_ror"] = extreme.values
    df["low_confidence"] = low_conf.values
    df["low_confidence_reason"] = [_reason(i) for i in range(n)]
    df.attrs["extreme_ror_thresholds"] = thresholds
    return df


def summarize_signals(res: pd.DataFrame, drug_col: str = "drug") -> pd.DataFrame:
    """One-row-per-drug summary: #events tested, #signals, top event by ROR."""
    out = []
    for drug, sub in res.groupby(drug_col):
        sig = sub[sub["signal"]]
        top = sig.sort_values("ror", ascending=False).head(1)
        out.append({
            drug_col: drug,
            "events_tested": len(sub),
            "signals": int(sub["signal"].sum()),
            "top_signal_event": (top["event"].iloc[0]
                                 if len(top) else None),
            "top_signal_ror": (float(top["ror"].iloc[0])
                               if len(top) else np.nan),
        })
    return pd.DataFrame(out).sort_values("signals", ascending=False)
