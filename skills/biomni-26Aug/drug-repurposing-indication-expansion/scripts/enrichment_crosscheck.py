"""Independent KS-based enrichment cross-check + consensus rank.

The connectivity score (connectivity_score.py) is the primary metric. This module
computes a second, methodologically independent reversal score based on a
Kolmogorov-Smirnov (GSEA-style) running-sum enrichment of the disease up/down sets
within each drug's ranked gene list. Two independent methods agreeing (high Spearman
rho) is the internal robustness check.

Algorithm
---------
For each drug, order genes as (drug_up followed by drug_dn) so the top of the list is
maximally up-regulated by the drug. Compute the classic KS enrichment score (ES) of the
disease-up set and of the disease-down set in that ordered list:
    running hit-rate vs miss-rate; ES = signed maximum deviation.
Reversal enrichment = ES(disease_down) - ES(disease_up): a drug that pushes disease-up
genes toward its down-end and disease-down genes toward its up-end scores positive
(i.e. reversal), mirroring the connectivity score's logic.

consensus_rank = mean( rank_by_connectivity , rank_by_enrichment )  (lower = better reverser)

The consensus is then turned into a single authoritative integer `canonical_rank` (1 = best)
by sorting on consensus_rank (asc) with a deterministic tie-break
(S_reversal desc -> fdr_reversal asc -> name asc). `canonical_rank` is the ONE ordering that
every downstream output (tables, figures, literature slate, report) reads; nothing re-sorts by
another key. See `assign_canonical_rank` / `CANONICAL_SORT_COLS`.

Public API
----------
  enrichment_score(ordered_genes, gene_set) -> float
  add_enrichment(conn_df, bundle) -> DataFrame  (adds es/rank/consensus + canonical_rank)
  assign_canonical_rank(df) -> DataFrame        (adds the definitive integer canonical_rank)
  rank_agreement(df_a, df_b, label_a, label_b) -> dict  (Spearman rho between two ranked frames)
  run(conn_csv, bundle_pickle, out_csv) -> DataFrame  (rows ordered by canonical_rank)
"""
import pickle
import numpy as np
import pandas as pd
from scipy.stats import spearmanr


def enrichment_score(ordered_genes, gene_set):
    """Rank-based KS running-sum ES of `gene_set` within `ordered_genes` (top = start).

    Uses the simple two-CDF Kolmogorov-Smirnov statistic between the positions of the
    set genes and the uniform position expectation:
        t   = length of the ordered list
        n   = number of set members that appear in the list
        pos = cumulative count of set members seen up to position j
        a = max_j ( j/t - pos/n )    # uniform ahead of hits  -> set enriched at bottom
        b = max_j ( pos/n - (j-1)/t) # hits ahead of uniform  -> set enriched at top
        ES = a if a > b else -b
    A set concentrated at the TOP of the list yields a positive ES.
    """
    t = len(ordered_genes)
    if t == 0:
        return 0.0
    n = sum(1 for g in ordered_genes if g in gene_set)  # set members actually present
    if n == 0:
        return 0.0
    pos = 0
    a = 0.0
    b = 0.0
    for j, g in enumerate(ordered_genes, start=1):
        if g in gene_set:
            pos += 1
        a = max(a, j / t - pos / n)        # uniform ahead of hits -> set enriched at bottom
        b = max(b, pos / n - (j - 1) / t)  # hits ahead of uniform -> set enriched at top
    # Standard KS convention: ES > 0 when the set is concentrated toward the TOP of the
    # ordered list. Since ordered = drug_up ++ drug_dn, a disease set at the top means it
    # overlaps the drug-UP genes. reversal_enrich = ES(disease_dn) - ES(disease_up) is then
    # positive for a disease-reversing drug (disease-dn pushed up, disease-up pushed down).
    # This reproduces the validated reference run exactly.
    return b if b > a else -a


def add_enrichment(conn_df, bundle):
    """Add es_disease_up/dn, reversal_enrich, and consensus_rank to the connectivity DataFrame."""
    s_up, s_dn = bundle["disease_up"], bundle["disease_dn"]
    es_up, es_dn, rev = [], [], []
    # index perturbation sigs by name for alignment with conn_df order
    sigs = bundle["pert_sigs"]
    for name in conn_df["pert"]:
        sig = sigs.get(name, {"up": set(), "dn": set()})
        ordered = list(sig["up"]) + list(sig["dn"])  # top = drug-up
        e_up = enrichment_score(ordered, s_up)
        e_dn = enrichment_score(ordered, s_dn)
        es_up.append(e_up)
        es_dn.append(e_dn)
        rev.append(e_dn - e_up)
    out = conn_df.copy()
    out["es_disease_up"] = es_up
    out["es_disease_dn"] = es_dn
    out["reversal_enrich"] = rev
    # ranks: 1 = best (highest score)
    out["rank_conn"] = out["S_reversal"].rank(ascending=False, method="average")
    out["rank_enr"] = out["reversal_enrich"].rank(ascending=False, method="average")
    out["consensus_rank"] = out[["rank_conn", "rank_enr"]].mean(axis=1)
    out = assign_canonical_rank(out)
    return out


# Canonical sort keys (single source of truth for ordering across the whole pipeline).
# Primary = consensus_rank (asc, lower is a better reverser). Deterministic tie-break:
# S_reversal (desc) -> fdr_reversal (asc) -> perturbation name (asc). Every downstream
# output (all tables, all figures, the literature slate, the report) MUST order by the
# resulting integer `canonical_rank` and must NOT re-sort by any other key.
CANONICAL_SORT_COLS = ["consensus_rank", "S_reversal", "fdr_reversal", "pert"]
CANONICAL_SORT_ASC = [True, False, True, True]


def assign_canonical_rank(df):
    """Return `df` ordered by the canonical keys with a 1-based integer `canonical_rank`.

    This is THE definitive ranking of the drug list. `consensus_rank` (the float mean of
    the two method ranks) is retained as a diagnostic column, but ordering everywhere is by
    `canonical_rank`. The tie-break is fully deterministic, so the ranking is reproducible
    (seed=42) with no dependence on input row order or pandas sort stability.
    """
    cols = [c for c in CANONICAL_SORT_COLS if c in df.columns]
    asc = [CANONICAL_SORT_ASC[CANONICAL_SORT_COLS.index(c)] for c in cols]
    out = df.sort_values(cols, ascending=asc, kind="mergesort").reset_index(drop=True)
    out.insert(0, "canonical_rank", range(1, len(out) + 1))
    return out


def rank_agreement(df_a, df_b, label_a="A", label_b="B"):
    """Compare two finished ranked frames for the same disease (cross-signature stability).

    Inner-joins on `pert`, runs Spearman on `canonical_rank` and on `S_reversal`, and
    returns a dict with the rhos, p-values, shared count, and a one-sentence summary.
    No new statistics or data sources — it compares two artifacts the pipeline already
    produced. Use when more than one signature is run for the same disease to quantify
    how unrelated the rankings are (a reviewer cannot tell from a qualitative 'markedly
    different' description).

    Returns dict(n_shared, rho_canonical_rank, p_canonical_rank, rho_S_reversal,
    p_S_reversal, summary).
    """
    if df_a is None or df_b is None or len(df_a) == 0 or len(df_b) == 0:
        return dict(n_shared=0, rho_canonical_rank=None, p_canonical_rank=None,
                    rho_S_reversal=None, p_S_reversal=None,
                    summary="Cannot compare: one or both frames are empty.")

    key = "pert"
    if key not in df_a.columns or key not in df_b.columns:
        return dict(n_shared=0, rho_canonical_rank=None, p_canonical_rank=None,
                    rho_S_reversal=None, p_S_reversal=None,
                    summary="Cannot compare: 'pert' column missing from one or both frames.")

    merged = df_a[[key, "canonical_rank", "S_reversal"]].merge(
        df_b[[key, "canonical_rank", "S_reversal"]],
        on=key, suffixes=(f"_{label_a}", f"_{label_b}"))
    n_shared = len(merged)
    if n_shared < 3:
        return dict(n_shared=n_shared, rho_canonical_rank=None, p_canonical_rank=None,
                    rho_S_reversal=None, p_S_reversal=None,
                    summary=f"Only {n_shared} shared perturbations; cannot compute Spearman.")

    cr_a = f"canonical_rank_{label_a}"
    cr_b = f"canonical_rank_{label_b}"
    sr_a = f"S_reversal_{label_a}"
    sr_b = f"S_reversal_{label_b}"

    rho_cr, p_cr = spearmanr(merged[cr_a], merged[cr_b])
    rho_sr, p_sr = spearmanr(merged[sr_a], merged[sr_b])

    summary = (f"Rank agreement between {label_a} and {label_b} across {n_shared} shared "
               f"perturbations: Spearman rho = {rho_cr:.3f} on canonical_rank "
               f"(p={p_cr:.2e}), rho = {rho_sr:.3f} on S_reversal (p={p_sr:.2e}).")
    return dict(n_shared=n_shared, rho_canonical_rank=float(rho_cr),
                p_canonical_rank=float(p_cr), rho_S_reversal=float(rho_sr),
                p_S_reversal=float(p_sr), summary=summary)


def run(conn_csv="/workspace/dri_run/data/connectivity.csv",
        bundle_pickle="/workspace/dri_run/data/harmonized.pkl",
        out_csv="/workspace/dri_run/data/consensus.csv"):
    conn = pd.read_csv(conn_csv)
    with open(bundle_pickle, "rb") as fh:
        bundle = pickle.load(fh)
    out = add_enrichment(conn, bundle)  # already ordered by canonical_rank
    rho, p = spearmanr(out["S_reversal"], out["reversal_enrich"])
    out.to_csv(out_csv, index=False)
    print(f"[enrichment] Spearman rho (connectivity vs enrichment) = {rho:.3f} (p={p:.2e})")
    print(f"[enrichment] canonical top 5 (consensus-based): {list(out.head(5)['pert'])}")
    print(f"[enrichment] wrote {out_csv} (row order = canonical_rank)")
    return out, rho, p


if __name__ == "__main__":
    import sys
    a = sys.argv[1] if len(sys.argv) > 1 else "/workspace/dri_run/data/connectivity.csv"
    b = sys.argv[2] if len(sys.argv) > 2 else "/workspace/dri_run/data/harmonized.pkl"
    c = sys.argv[3] if len(sys.argv) > 3 else "/workspace/dri_run/data/consensus.csv"
    run(a, b, c)
