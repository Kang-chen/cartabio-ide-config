"""
benchmark_tesla — evaluate the TESLA-guided prioritizer against a TESLA-style truth set.

Grounding
---------
Wells DK, van Buuren MM, Dang KK, et al., Cell 2020 (DOI: 10.1016/j.cell.2020.09.015).
The TESLA consortium assessed submitted peptides for T-cell recognition and built a model of
tumor-epitope immunogenicity that **filtered out 98% of non-immunogenic peptides with a
precision above 0.70**, validated in an independent 310-epitope cohort. Pipelines that
prioritized strong binding affinity and filtered low-expression / low-VAF epitopes ranked
neoantigens better.

This harness reproduces that evaluation logic on a labelled peptide set:
  * a **truth set** of peptides each tagged immunogenic (T-cell recognized) or not, with the
    real per-peptide features (MHC %rank, expression, VAF, mutation position, …);
  * the prioritizer's TESLA composite + tier assignment applied to each peptide;
  * TESLA-style **filtering metrics** (fraction of non-immunogenic peptides removed, precision
    of the retained/'called' set) and **ranking metrics** (AUROC, average precision, top-K
    recall, enrichment) that quantify how well the composite orders immunogenic peptides first.

REAL-DATA-ONLY: the truth set must carry real labels and real features. The harness computes
metrics only; it never invents labels or features. If a peptide lacks a feature, that feature
simply does not contribute (as in scoring).
"""

from __future__ import annotations

import json
import math
import os
from typing import Optional

from tesla_features import score_candidates, binding_class


# =============================================================================
# Metrics
# =============================================================================
def _roc_auc(labels: list[int], scores: list[float]) -> Optional[float]:
    """AUROC via the rank-sum (Mann-Whitney U) identity; ties handled with average ranks."""
    pairs = [(s, y) for s, y in zip(scores, labels) if s is not None]
    n_pos = sum(1 for _, y in pairs if y == 1)
    n_neg = sum(1 for _, y in pairs if y == 0)
    if n_pos == 0 or n_neg == 0:
        return None
    # average ranks
    order = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and pairs[order[j + 1]][0] == pairs[order[i]][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    sum_pos = sum(ranks[i] for i in range(len(pairs)) if pairs[i][1] == 1)
    auc = (sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return round(auc, 4)


def _average_precision(labels: list[int], scores: list[float]) -> Optional[float]:
    """Area under the precision-recall curve (AP), scores ranked descending."""
    pairs = sorted([(s, y) for s, y in zip(scores, labels) if s is not None],
                   key=lambda t: -t[0])
    n_pos = sum(1 for _, y in pairs if y == 1)
    if n_pos == 0:
        return None
    tp = 0
    ap = 0.0
    for k, (_, y) in enumerate(pairs, start=1):
        if y == 1:
            tp += 1
            ap += tp / k       # precision at this recall step
    return round(ap / n_pos, 4)


def _topk_recall(labels: list[int], scores: list[float], k: int) -> Optional[float]:
    pairs = sorted([(s, y) for s, y in zip(scores, labels) if s is not None],
                   key=lambda t: -t[0])
    n_pos = sum(1 for _, y in pairs if y == 1)
    if n_pos == 0:
        return None
    hit = sum(1 for _, y in pairs[:k] if y == 1)
    return round(hit / n_pos, 4)


def _enrichment_at_k(labels: list[int], scores: list[float], k: int) -> Optional[float]:
    """Fold-enrichment of immunogenic peptides in the top-k vs the base rate."""
    pairs = sorted([(s, y) for s, y in zip(scores, labels) if s is not None],
                   key=lambda t: -t[0])
    if not pairs:
        return None
    base = sum(y for _, y in pairs) / len(pairs)
    if base == 0:
        return None
    topk = pairs[:k]
    prec_k = sum(y for _, y in topk) / len(topk)
    return round(prec_k / base, 3)


# =============================================================================
# TESLA-style filtering evaluation
# =============================================================================
def tesla_filtering_metrics(candidates: list[dict]) -> dict:
    """Reproduce the TESLA filtering statistic.

    'Called' peptides = those NOT excluded by the prioritizer (tier in Tier1/2/3, i.e. binders
    that pass the abundance filter). Reports:
      * n_immunogenic / n_nonimmunogenic (from truth labels)
      * frac_nonimmunogenic_filtered  = removed non-immunogenic / total non-immunogenic
      * precision_called              = immunogenic in called / called   (TESLA precision)
      * recall_called                 = immunogenic in called / total immunogenic (sensitivity)
    """
    called, excluded = [], []
    for c in candidates:
        (called if str(c.get("tier", "")).startswith("Tier") else excluded).append(c)

    pos = [c for c in candidates if c.get("label") == 1]
    neg = [c for c in candidates if c.get("label") == 0]
    n_pos, n_neg = len(pos), len(neg)

    neg_excluded = sum(1 for c in excluded if c.get("label") == 0)
    pos_called = sum(1 for c in called if c.get("label") == 1)

    frac_neg_filtered = (neg_excluded / n_neg) if n_neg else None
    precision_called = (pos_called / len(called)) if called else None
    recall_called = (pos_called / n_pos) if n_pos else None

    return {
        "n_total": len(candidates),
        "n_immunogenic": n_pos,
        "n_nonimmunogenic": n_neg,
        "n_called": len(called),
        "n_excluded": len(excluded),
        "frac_nonimmunogenic_filtered": (round(frac_neg_filtered, 4)
                                         if frac_neg_filtered is not None else None),
        "precision_called": round(precision_called, 4) if precision_called is not None else None,
        "recall_called": round(recall_called, 4) if recall_called is not None else None,
        "tesla_reference": ("Wells et al., Cell 2020: model filtered ~98% of non-immunogenic "
                            "peptides at precision >0.70"),
    }


def _presentation_score(c: dict) -> Optional[float]:
    """A composite restricted to the PRESENTATION features the public TESLA table can exercise.

    The 714-peptide benchmark table carries peptide + allele + label but NO expression/VAF and NO
    wild-type counterpart, so the recognition features (agretopicity, foreignness) cannot be
    computed or validated on it. This sub-score therefore uses only binding affinity + binding
    stability + hydrophobicity + mutation position — the presentation/processing signal the table
    can actually test — renormalised over whatever is available. It is the fair, apples-to-apples
    comparator on this benchmark; the full composite (with recognition) is workflow-only here.
    """
    t = c.get("tesla", {})
    subs = {
        "binding_affinity": t.get("binding_affinity_score"),
        "binding_stability": (t.get("binding_stability") or {}).get("score"),
        "fraction_hydrophobic": t.get("fraction_hydrophobic"),
        "mutation_position": (t.get("mutation_position") or {}).get("score"),
    }
    w = {"binding_affinity": 0.55, "binding_stability": 0.30,
         "fraction_hydrophobic": 0.075, "mutation_position": 0.075}
    used = {k: v for k, v in subs.items() if v is not None}
    if not used:
        return None
    wsum = sum(w[k] for k in used)
    return round(sum((w[k] / wsum) * used[k] for k in used) * 100.0, 4)


def ranking_metrics(candidates: list[dict]) -> dict:
    """Ranking metrics vs truth labels, for BOTH scores that matter on this benchmark:

    - ``auroc``/``average_precision`` etc.: the FULL composite priority (includes recognition
      features that, on this public table, are mostly uninformative because agretopicity is
      unavailable and foreignness cannot be validated — see notes).
    - ``auroc_presentation``/``average_precision_presentation``: the PRESENTATION sub-score
      (binding + stability + hydrophobicity + position) — the fair comparator the table can test.
    """
    labels = [c.get("label") for c in candidates]
    scores = [c.get("priority_score") for c in candidates]
    pscores = [_presentation_score(c) for c in candidates]
    # keep only labelled rows
    lab, sco, psco = [], [], []
    for y, s, ps in zip(labels, scores, pscores):
        if y in (0, 1):
            lab.append(y)
            sco.append(s if s is not None else 0.0)
            psco.append(ps if ps is not None else 0.0)
    out = {
        "auroc": _roc_auc(lab, sco),
        "average_precision": _average_precision(lab, sco),
        "auroc_presentation": _roc_auc(lab, psco),
        "average_precision_presentation": _average_precision(lab, psco),
        "top10_recall": _topk_recall(lab, sco, 10),
        "top20_recall": _topk_recall(lab, sco, 20),
        "top50_recall": _topk_recall(lab, sco, 50),
        "enrichment_top10": _enrichment_at_k(lab, sco, 10),
        "enrichment_top20": _enrichment_at_k(lab, sco, 20),
        "n_labelled": len(lab),
        "base_rate": round(sum(lab) / len(lab), 4) if lab else None,
        "note": ("auroc = full composite (recognition features workflow-only here: agretopicity "
                 "unavailable without WT, foreignness not validatable on this table); "
                 "auroc_presentation = binding+stability+hydrophobicity+position, the fair "
                 "comparator on this public table."),
    }
    return out


def per_feature_separation(candidates: list[dict]) -> dict:
    """Mean of each TESLA sub-feature in immunogenic vs non-immunogenic peptides.

    Recreates the TESLA observation that recognized peptides have stronger binding affinity and
    higher expression. Positive 'delta' means the feature is higher in immunogenic peptides.
    """
    def _mean(vals):
        vv = [v for v in vals if v is not None]
        return round(sum(vv) / len(vv), 4) if vv else None

    pos = [c for c in candidates if c.get("label") == 1]
    neg = [c for c in candidates if c.get("label") == 0]

    def _feat(c, path):
        t = c.get("tesla", {})
        if path == "binding_affinity":
            return t.get("binding_affinity_score")
        if path == "tumor_abundance":
            return (t.get("tumor_abundance") or {}).get("score")
        if path == "binding_stability":
            return (t.get("binding_stability") or {}).get("score")
        if path == "fraction_hydrophobic":
            return t.get("fraction_hydrophobic")
        if path == "mutation_position":
            return (t.get("mutation_position") or {}).get("score")
        if path == "mut_rank":
            return c.get("mut_rank")
        if path == "expr_tpm":
            return c.get("expr_tpm")
        return None

    out = {}
    for feat in ["binding_affinity", "tumor_abundance", "binding_stability",
                 "fraction_hydrophobic", "mutation_position", "mut_rank", "expr_tpm"]:
        mp = _mean([_feat(c, feat) for c in pos])
        mn = _mean([_feat(c, feat) for c in neg])
        delta = (round(mp - mn, 4) if (mp is not None and mn is not None) else None)
        out[feat] = {"immunogenic_mean": mp, "nonimmunogenic_mean": mn, "delta": delta}
    return out


# =============================================================================
# Driver
# =============================================================================
def run_benchmark(truth_candidates: list[dict], *, stability_map: Optional[dict] = None) -> dict:
    """Score a labelled truth set and compute all benchmark metrics.

    ``truth_candidates`` is a list of dicts each with the scoring inputs (peptide, mut_rank,
    expr_tpm, vaf, mut_pos_in_pep, wt_rank, presentation_score) **and** an integer ``label``
    (1 = immunogenic / T-cell recognized, 0 = non-immunogenic). Returns a metrics dict.
    """
    scored = score_candidates([dict(c) for c in truth_candidates], stability_map=stability_map)
    # carry labels through (score_candidates preserves unknown keys)
    metrics = {
        "filtering": tesla_filtering_metrics(scored),
        "ranking": ranking_metrics(scored),
        "feature_separation": per_feature_separation(scored),
        "scored_candidates": scored,
    }
    return metrics


def load_truth_set(path: str) -> list[dict]:
    """Load a truth set from JSON (list of records) or CSV/TSV with a 'label' column."""
    if path.lower().endswith(".json"):
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("peptides", [])
    import pandas as pd
    sep = "\t" if path.lower().endswith((".tsv", ".txt")) else ","
    df = pd.read_csv(path, sep=sep)
    return df.to_dict(orient="records")


# =============================================================================
# Real TESLA neoepitope benchmark (Wells et al., Cell 2020) — MHCflurry required
# =============================================================================
def _normalize_allele(a: str) -> str:
    """'HLA-A02:01' -> 'HLA-A*02:01' (MHCflurry form); leave already-starred forms as is."""
    a = str(a).strip()
    if a.startswith("HLA-") and "*" not in a and len(a) >= 6:
        return a[:5] + "*" + a[5:]
    return a


def benchmark_real_tesla(table_path: str, *, require_mhcflurry: bool = True) -> dict:
    """Run the benchmark on the REAL TESLA neoepitope table (peptide, target_value, allele).

    Source: Wells et al., Cell 2020, redistributed as an independent evaluation set
    (Mendeley Data doi:10.17632/6x87nx8jtc.1, CC BY 4.0). Each peptide carries its real
    restricting HLA-I allele and a real T-cell-recognition label (target_value 1/0).

    This computes the REAL MHCflurry %rank / affinity for each peptide against ITS OWN allele,
    then the TESLA presentation+recognition features (binding affinity, stability proxy,
    fraction hydrophobic, mutation position). Tumor abundance is unavailable in this table
    (no per-peptide expression/VAF), so that feature is correctly left ``None`` (never
    fabricated) and simply does not contribute — the benchmark thus evaluates the
    presentation+recognition portion of the model on real labels.

    Mutation position is unknown for these peptides (the table gives no in-peptide mutation
    index), so that feature is also left out per peptide; the composite renormalises over the
    features actually available.
    """
    from binding_core import HAS_MHCFLURRY, predict_binding, EngineUnavailable
    import pandas as pd

    if require_mhcflurry and not HAS_MHCFLURRY:
        raise EngineUnavailable(
            "MHCflurry is required to compute real binding features for the TESLA benchmark. "
            "Install: pip install mhcflurry && mhcflurry-downloads fetch. No synthetic fallback.")

    if table_path.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(table_path)
    else:
        df = pd.read_csv(table_path)
    # column autodetect
    pep_col = next((c for c in df.columns if str(c).lower() in ("peptide", "epitope", "sequence")), None)
    lab_col = next((c for c in df.columns if str(c).lower() in
                    ("target_value", "label", "immunogenic", "immunogenicity")), None)
    all_col = next((c for c in df.columns if str(c).lower() in ("allele", "hla", "mhc")), None)
    if not (pep_col and lab_col and all_col):
        raise ValueError(f"TESLA table must have peptide/label/allele columns; got {list(df.columns)}")

    df = df[[pep_col, lab_col, all_col]].dropna()
    df["allele_norm"] = df[all_col].map(_normalize_allele)
    alleles = sorted(df["allele_norm"].unique())

    # Build one pep_index per allele group so predict_binding scores each peptide on its own HLA.
    all_rank, all_aff = {}, {}
    engine = None
    for allele in alleles:
        peps = sorted(set(df.loc[df["allele_norm"] == allele, pep_col].astype(str)))
        pep_index = {"_g": {"mut_peptides": [(p, 0) for p in peps], "wt_peptides": []}}
        rc, ac, engine = predict_binding(pep_index, [allele])
        all_rank.update(rc)
        all_aff.update(ac)

    # assemble labelled candidates with the real best (=own-allele) %rank + affinity
    candidates = []
    for _, row in df.iterrows():
        pep = str(row[pep_col])
        allele = row["allele_norm"]
        r = all_rank.get((pep, allele))
        if r is None:
            continue  # allele unsupported by MHCflurry models -> drop (never fabricate a rank)
        candidates.append({
            "peptide": pep, "label": int(row[lab_col]), "hla_best": allele,
            "mut_rank": r, "affinity_nm": all_aff.get((pep, allele)),
            "presentation_score": (max(0.0, min(1.0, 1.0 - r / 100.0))),
            "expr_tpm": None, "vaf": None, "mut_pos_in_pep": None, "wt_rank": None,
        })
    n_input = len(df)
    n_dropped = n_input - len(candidates)
    print(f"[benchmark] real TESLA set: {len(candidates)}/{n_input} peptides scored on their own "
          f"HLA-I ({len(alleles)} alleles; engine {engine}; "
          f"{n_dropped} dropped as unsupported-allele or NaN score, never imputed)")
    metrics = run_benchmark(candidates)
    metrics["n_input_rows"] = n_input
    metrics["n_scored"] = len(candidates)
    metrics["n_dropped"] = n_dropped
    return metrics


def export_benchmark(metrics: dict, output_dir: str) -> dict:
    """Write benchmark_metrics.json + benchmark_scored.csv."""
    import pandas as pd
    os.makedirs(output_dir, exist_ok=True)
    mj = os.path.join(output_dir, "benchmark_metrics.json")
    with open(mj, "w") as f:
        json.dump({k: v for k, v in metrics.items() if k != "scored_candidates"},
                  f, indent=2, default=str)
    rows = []
    for c in metrics["scored_candidates"]:
        t = c.get("tesla", {})
        rows.append({
            "peptide": c.get("peptide"), "label": c.get("label"),
            "tier": c.get("tier"), "priority_score": c.get("priority_score"),
            "mut_rank": c.get("mut_rank"), "binding_class": t.get("binding_class"),
            "expr_tpm": c.get("expr_tpm"), "vaf": c.get("vaf"),
            "fraction_hydrophobic": t.get("fraction_hydrophobic"),
            "mut_pos_in_pep": (t.get("mutation_position") or {}).get("position"),
        })
    sc = os.path.join(output_dir, "benchmark_scored.csv")
    pd.DataFrame(rows).to_csv(sc, index=False)
    print(f"[benchmark] wrote {mj} and {sc}")
    return {"metrics_json": mj, "scored_csv": sc}


def summarize(metrics: dict) -> str:
    """One-paragraph human summary of the benchmark, framed against the TESLA result."""
    f = metrics["filtering"]
    r = metrics["ranking"]
    lines = [
        f"TESLA-style benchmark on {f['n_total']} labelled peptides "
        f"({f['n_immunogenic']} immunogenic / {f['n_nonimmunogenic']} non-immunogenic):",
        f"  Filtering: removed {f['frac_nonimmunogenic_filtered']} of non-immunogenic peptides; "
        f"precision of the called set = {f['precision_called']} "
        f"(recall {f['recall_called']}).",
        f"  Ranking: AUROC {r['auroc']}, average precision {r['average_precision']}, "
        f"top10 recall {r['top10_recall']}, enrichment@10 {r['enrichment_top10']}x.",
        "  Reference: Wells et al., Cell 2020 reported filtering ~98% of non-immunogenic "
        "peptides at precision >0.70.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Benchmark the TESLA prioritizer on a truth set")
    ap.add_argument("--truth", required=True, help="JSON/CSV truth set with a 'label' column")
    ap.add_argument("--out", default="benchmark_results")
    args = ap.parse_args()
    truth = load_truth_set(args.truth)
    metrics = run_benchmark(truth)
    export_benchmark(metrics, args.out)
    print(summarize(metrics))
