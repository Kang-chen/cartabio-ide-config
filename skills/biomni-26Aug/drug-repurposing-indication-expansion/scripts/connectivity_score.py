"""CMap-style bidirectional connectivity reversal scoring + permutation null + BH-FDR.

Disease-agnostic. Given a harmonized bundle (from harmonize_signatures.harmonize),
score every perturbation for its ability to REVERSE the disease signature.

Score definition
----------------
For a drug with up-set (D_up) and down-set (D_dn), disease up-set (S_up)/down-set (S_dn),
background of size N:
    z_reversal = hyper_z(|S_up & D_dn|) + hyper_z(|S_dn & D_up|)   # opposite direction = reversal
    z_mimic    = hyper_z(|S_up & D_up|) + hyper_z(|S_dn & D_dn|)   # same direction    = mimicry
    S_reversal = z_reversal - z_mimic                              # positive => reverses disease

where hyper_z is the standardized (observed - expected)/sd of an overlap under the
hypergeometric model (size-corrected, so large drug sets are not automatically favored).

Significance
------------
A permutation null (default N=10,000) samples each of the four overlap counts directly
from its hypergeometric distribution (preserving set sizes), recomputes S_reversal, and
derives a one-sided p for reversal. p-values are BH-FDR corrected across all perturbations.

Public API
----------
  hyper_z(overlap, sizeA, sizeB, N) -> float
  score_all(bundle, nperm=10000, seed=42) -> pandas.DataFrame  (also written to CSV by run())
      Columns include S_reversal, z_reversal, z_mimic, p_reversal, p_mimic,
      fdr_reversal (BH-FDR on p_reversal), fdr_mimic (BH-FDR on p_mimic),
      S_reversal_z, n_drug_up, n_drug_dn, organism.
  run(bundle_pickle, out_csv, nperm, seed) -> DataFrame
"""
import math
import pickle
import numpy as np
import pandas as pd


def hyper_z(overlap, sizeA, sizeB, N):
    """Standardized overlap statistic under the hypergeometric model."""
    if N <= 1 or sizeA == 0 or sizeB == 0:
        return 0.0
    mean = sizeA * sizeB / N
    var = mean * (1 - sizeA / N) * (1 - sizeB / N) * (N / (N - 1))
    if var <= 0:
        return 0.0
    return (overlap - mean) / math.sqrt(var)


def _score_one(s_up, s_dn, d_up, d_dn, N):
    ov_up_in_dd = len(s_up & d_dn)   # disease-up genes among drug-down
    ov_dn_in_du = len(s_dn & d_up)   # disease-down genes among drug-up
    ov_up_in_du = len(s_up & d_up)
    ov_dn_in_dd = len(s_dn & d_dn)
    z_rev = hyper_z(ov_up_in_dd, len(s_up), len(d_dn), N) + hyper_z(ov_dn_in_du, len(s_dn), len(d_up), N)
    z_mim = hyper_z(ov_up_in_du, len(s_up), len(d_up), N) + hyper_z(ov_dn_in_dd, len(s_dn), len(d_dn), N)
    return dict(S_reversal=z_rev - z_mim, z_reversal=z_rev, z_mimic=z_mim,
                ov_up_in_dd=ov_up_in_dd, ov_dn_in_du=ov_dn_in_du,
                ov_up_in_du=ov_up_in_du, ov_dn_in_dd=ov_dn_in_dd)


def _perm_null(s_up_n, s_dn_n, d_up_n, d_dn_n, N, nperm, rng):
    """Sample S_reversal under the null by drawing each overlap from its hypergeometric law."""
    # rng.hypergeometric(ngood, nbad, nsample): ngood = size of one set (K), nbad = N-K,
    # nsample = size of the other set (n). Expectation K*n/N matches the analytic mean.
    def draw(K, n):
        if K == 0 or n == 0:
            return np.zeros(nperm)
        return rng.hypergeometric(K, N - K, n, size=nperm)
    o1 = draw(s_up_n, d_dn_n)  # reversal term 1
    o2 = draw(s_dn_n, d_up_n)  # reversal term 2
    o3 = draw(s_up_n, d_up_n)  # mimic term 1
    o4 = draw(s_dn_n, d_dn_n)  # mimic term 2

    def z(obs, K, n):
        if K == 0 or n == 0:
            return np.zeros(nperm)
        mean = K * n / N
        var = mean * (1 - K / N) * (1 - n / N) * (N / (N - 1))
        if var <= 0:
            return np.zeros(nperm)
        return (obs - mean) / math.sqrt(var)
    zrev = z(o1, s_up_n, d_dn_n) + z(o2, s_dn_n, d_up_n)
    zmim = z(o3, s_up_n, d_up_n) + z(o4, s_dn_n, d_dn_n)
    return zrev - zmim


def score_all(bundle, nperm=10000, seed=42):
    """Score every perturbation; return a DataFrame with scores, permutation p, and BH-FDR."""
    s_up, s_dn = bundle["disease_up"], bundle["disease_dn"]
    N = len(bundle["BG"])
    rng = np.random.default_rng(seed)
    recs = []
    for name, sig in bundle["pert_sigs"].items():
        d_up, d_dn = sig["up"], sig["dn"]
        sc = _score_one(s_up, s_dn, d_up, d_dn, N)
        null = _perm_null(len(s_up), len(s_dn), len(d_up), len(d_dn), N, nperm, rng)
        obs = sc["S_reversal"]
        p_rev = (np.sum(null >= obs) + 1) / (nperm + 1)
        p_mim = (np.sum(null <= obs) + 1) / (nperm + 1)
        nm, nsd = float(np.mean(null)), float(np.std(null))
        recs.append(dict(pert=name, **sc,
                         n_drug_up=len(d_up), n_drug_dn=len(d_dn),
                         organism=sig["organism"], p_reversal=p_rev, p_mimic=p_mim,
                         null_mean=nm, null_sd=nsd,
                         S_reversal_z=(obs - nm) / nsd if nsd > 0 else 0.0))
    df = pd.DataFrame(recs)
    # BH-FDR on reversal p-values
    try:
        from statsmodels.stats.multitest import multipletests
        df["fdr_reversal"] = multipletests(df["p_reversal"], method="fdr_bh")[1]
    except Exception:  # pragma: no cover - fallback BH
        m = len(df)
        order = df["p_reversal"].rank(method="first").astype(int)
        df["fdr_reversal"] = (df["p_reversal"] * m / order).clip(upper=1.0)
    # BH-FDR on mimic p-values — lets check_controls distinguish a significant mimic
    # (e.g. a disease-inducing control) from a null result. Without fdr_mimic, a drug
    # scored as a MIMIC shows fdr_reversal = 1.0 and there is no statistic that says
    # "this is a SIGNIFICANT mimic".
    try:
        from statsmodels.stats.multitest import multipletests
        df["fdr_mimic"] = multipletests(df["p_mimic"], method="fdr_bh")[1]
    except Exception:  # pragma: no cover - fallback BH
        m = len(df)
        order = df["p_mimic"].rank(method="first").astype(int)
        df["fdr_mimic"] = (df["p_mimic"] * m / order).clip(upper=1.0)
    df = df.sort_values("S_reversal", ascending=False).reset_index(drop=True)
    return df


def run(bundle_pickle="/workspace/dri_run/data/harmonized.pkl",
        out_csv="/workspace/dri_run/data/connectivity.csv", nperm=10000, seed=42):
    with open(bundle_pickle, "rb") as fh:
        bundle = pickle.load(fh)
    df = score_all(bundle, nperm=nperm, seed=seed)
    df.to_csv(out_csv, index=False)
    n_sig = int((df["fdr_reversal"] < 0.05).sum())
    print(f"[connectivity] scored {len(df)} perturbations; {n_sig} reverse at FDR<0.05")
    # NOTE: this is the top 5 by raw S_reversal (pre-consensus); it is NOT the final ranking.
    # The authoritative order (canonical_rank) is produced later in enrichment_crosscheck.
    print(f"[connectivity] top 5 by S_reversal (pre-consensus, not final): {list(df.head(5)['pert'])}")
    print(f"[connectivity] wrote {out_csv}")
    return df


if __name__ == "__main__":
    import sys
    bp = sys.argv[1] if len(sys.argv) > 1 else "/workspace/dri_run/data/harmonized.pkl"
    oc = sys.argv[2] if len(sys.argv) > 2 else "/workspace/dri_run/data/connectivity.csv"
    run(bp, oc)
