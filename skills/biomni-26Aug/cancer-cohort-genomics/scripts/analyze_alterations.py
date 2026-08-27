"""
analyze_alterations.py — Alteration-frequency analysis for the
cancer-cohort-genomics skill.

Given raw mutation + CNA records and profiled-sample sets (from cbioportal_client),
compute per-cancer-type alteration frequencies on a COMMON denominator, classify
GISTIC events, and bin mutation hotspots. Gene-agnostic.

KEY CONVENTIONS (proven in the KRAS run):
  * Non-silent mutation = any mutationType NOT in SILENT_TYPES.
  * Amplification = GISTIC discrete +2.  Deep deletion = GISTIC discrete -2.
    Shallow gain/loss (+1/-1) are NOT counted as events.
  * COMMON DENOMINATOR (default): samples profiled for BOTH mutation AND CNA.
      mut_freq = |mut ∩ both| / |both|
      amp_freq = |amp ∩ both| / |both|
      any_freq = |(mut ∩ both) ∪ (amp ∩ both)| / |both|
    This guarantees  any% >= max(mut%, amp%)  and makes cross-cohort comparison
    apples-to-apples. (The per-assay denominator is available via
    compute_row_perassay() but can make any% < mut% look wrong — see references.)
  * Stability flag: cancer types with common-denominator N < 20 are flagged
    low-confidence (kept, not dropped).
"""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

# Mutation classifications that are NOT protein-altering (excluded from counts).
SILENT_TYPES = {
    "Silent", "Synonymous", "3'UTR", "5'UTR", "3'Flank", "5'Flank",
    "Intron", "IGR", "RNA",
}

AMP_GISTIC = 2      # high-level amplification
DEEPDEL_GISTIC = -2  # deep / homozygous deletion
STABILITY_MIN_N = 20

# Hotspot codon -> canonical label. Extend as needed; unknown codons -> "Other".
# These KRAS/oncogene codons are common recurrent positions; the binning itself
# is gene-agnostic (it just reads the codon number from the protein change).
_HGVS_CODON_RE = re.compile(r"^([A-Za-z])(\d+)")


# ---------------------------------------------------------------------------
# Record -> sample-level sets
# ---------------------------------------------------------------------------
def nonsilent_mutated_samples(mut_records: list[dict]) -> set[str]:
    """Sample IDs with >=1 non-silent mutation in the queried gene(s)."""
    return {r["sampleId"] for r in mut_records
            if r.get("mutationType") not in SILENT_TYPES}


def gistic_event_samples(cna_records: list[dict]) -> dict:
    """Return {"amp": set(sampleId with +2), "deepdel": set(sampleId with -2)}."""
    amp, deepdel = set(), set()
    for r in cna_records:
        val = r.get("alteration")
        if val == AMP_GISTIC:
            amp.add(r["sampleId"])
        elif val == DEEPDEL_GISTIC:
            deepdel.add(r["sampleId"])
    return {"amp": amp, "deepdel": deepdel}


# ---------------------------------------------------------------------------
# Per-cancer-type frequency rows
# ---------------------------------------------------------------------------
def compute_row_common(cohort: str, cancer_type: str, study_code: str,
                       seq_samples: set[str], cna_samples: set[str],
                       mut_samples: set[str], amp_samples: set[str],
                       deepdel_samples: set[str]) -> dict:
    """One tidy row using the COMMON denominator (profiled for BOTH assays).

    All event sets are intersected with `both` so numerator and denominator are
    on the same sample universe.
    """
    both = seq_samples & cna_samples
    n = len(both)
    mut_in = mut_samples & both
    amp_in = amp_samples & both
    del_in = deepdel_samples & both
    any_in = mut_in | amp_in  # deep deletion of an oncogene is rare; "any" = mut OR amp
    pct = (lambda x: round(100.0 * len(x) / n, 4)) if n else (lambda x: None)
    return {
        "cohort": cohort,
        "cancer_type": cancer_type,
        "study_code": study_code,
        "n_profiled": n,
        "n_mut": len(mut_in), "mut_freq_pct": pct(mut_in),
        "n_amp": len(amp_in), "amp_freq_pct": pct(amp_in),
        "n_deepdel": len(del_in), "deepdel_freq_pct": pct(del_in),
        "n_any": len(any_in), "any_freq_pct": pct(any_in),
        "stable": "yes" if n >= STABILITY_MIN_N else "no",
    }


def compute_row_perassay(cohort: str, cancer_type: str, study_code: str,
                         seq_samples: set[str], cna_samples: set[str],
                         mut_samples: set[str], amp_samples: set[str],
                         deepdel_samples: set[str]) -> dict:
    """Alternative: cBioPortal-style per-assay denominators.

    mut_freq over mutation-profiled; amp/deepdel over CNA-profiled. `any` uses the
    union of profiled samples. Provided for parity with the cBioPortal website; note
    it can yield any% < mut% (see references/conventions.md). NOT the default.
    """
    n_seq, n_cna = len(seq_samples), len(cna_samples)
    mut_in = mut_samples & seq_samples
    amp_in = amp_samples & cna_samples
    del_in = deepdel_samples & cna_samples
    union_prof = seq_samples | cna_samples
    any_in = (mut_in | amp_in)
    return {
        "cohort": cohort, "cancer_type": cancer_type, "study_code": study_code,
        "n_profiled_mut": n_seq, "n_mut": len(mut_in),
        "mut_freq_pct": round(100.0 * len(mut_in) / n_seq, 4) if n_seq else None,
        "n_profiled_cna": n_cna, "n_amp": len(amp_in),
        "amp_freq_pct": round(100.0 * len(amp_in) / n_cna, 4) if n_cna else None,
        "n_deepdel": len(del_in),
        "deepdel_freq_pct": round(100.0 * len(del_in) / n_cna, 4) if n_cna else None,
        "n_profiled_any": len(union_prof), "n_any": len(any_in),
        "any_freq_pct": round(100.0 * len(any_in) / len(union_prof), 4) if union_prof else None,
        "stable": "yes" if min(n_seq, n_cna) >= STABILITY_MIN_N else "no",
    }


def validate_common_invariant(df: pd.DataFrame) -> pd.DataFrame:
    """Return rows that VIOLATE any% >= max(mut%, amp%). Should be empty for the
    common denominator. Use as a self-check before reporting."""
    bad = df[(df["any_freq_pct"] < df["mut_freq_pct"]) |
             (df["any_freq_pct"] < df["amp_freq_pct"])]
    return bad


# ---------------------------------------------------------------------------
# Hotspot / allele binning (conditional — meaningful for genes with recurrent codons)
# ---------------------------------------------------------------------------
def parse_codon(protein_change: str):
    """Return (refAA, codon:int) from an HGVSp_short like 'G12D' -> ('G', 12).
    Returns (None, None) if unparseable (e.g. splice, fusion)."""
    if not protein_change:
        return None, None
    m = _HGVS_CODON_RE.match(protein_change.lstrip("p."))
    if not m:
        return None, None
    return m.group(1).upper(), int(m.group(2))


def hotspot_bins(mut_records: list[dict], hotspot_codons: dict | None = None) -> pd.DataFrame:
    """Aggregate non-silent mutations into codon hotspot bins + specific alleles.

    hotspot_codons: optional {codon:int -> label} map (e.g. {12:"G12",13:"G13",
    61:"Q61"}). If None, the top recurrent codons are discovered from the data.
    Returns a long DataFrame with columns: bin, allele, count.
    """
    allele_counts = defaultdict(int)
    codon_counts = defaultdict(int)
    for r in mut_records:
        if r.get("mutationType") in SILENT_TYPES:
            continue
        pc = r.get("proteinChange") or ""
        _, codon = parse_codon(pc)
        if pc:
            allele_counts[pc] += 1
        if codon is not None:
            codon_counts[codon] += 1

    if hotspot_codons is None:
        # discover: codons mutated in >=2 samples become named bins
        hotspot_codons = {c: f"codon_{c}" for c, n in codon_counts.items() if n >= 2}

    rows = []
    for allele, ct in sorted(allele_counts.items(), key=lambda kv: -kv[1]):
        _, codon = parse_codon(allele)
        label = hotspot_codons.get(codon, "Other") if codon is not None else "Other"
        rows.append({"bin": label, "allele": allele, "count": ct})
    return pd.DataFrame(rows)


def has_recurrent_hotspots(mut_records: list[dict], min_frac: float = 0.20) -> bool:
    """Heuristic: True if the single most common codon accounts for >= min_frac of
    non-silent mutations (oncogene-like, e.g. KRAS G12). False for tumor suppressors
    with dispersed inactivating mutations -> caller should skip the hotspot figure.
    """
    codon_counts = defaultdict(int)
    total = 0
    for r in mut_records:
        if r.get("mutationType") in SILENT_TYPES:
            continue
        _, codon = parse_codon(r.get("proteinChange") or "")
        if codon is not None:
            codon_counts[codon] += 1
            total += 1
    if total == 0:
        return False
    return (max(codon_counts.values()) / total) >= min_frac


# ---------------------------------------------------------------------------
# Sanity-check reference (catch query bugs before reporting)
# ---------------------------------------------------------------------------
# Approximate published KRAS frequencies by cancer type (mutation %). Large
# deviations from these ballparks for KRAS should trigger a logic review.
# For other genes, use LiteratureSearch to establish the expected ballpark.
KRAS_SANITY = {
    "Pancreatic": (60, 95),      # very high (bulk/panel purity lowers vs ~90% canonical)
    "Colorectal": (30, 50),
    "Lung Adenocarcinoma": (20, 40),
    "Endometrial": (10, 30),
}


def sanity_check_kras(freq_df: pd.DataFrame) -> list[str]:
    """Return human-readable warnings if KRAS frequencies fall outside expected
    ballparks. Empty list = looks consistent with known biology."""
    warns = []
    for _, row in freq_df.iterrows():
        for key, (lo, hi) in KRAS_SANITY.items():
            if key.lower() in str(row["cancer_type"]).lower() and row.get("mut_freq_pct") is not None:
                if not (lo <= row["mut_freq_pct"] <= hi):
                    warns.append(
                        f"{row['cohort']} {row['cancer_type']}: KRAS mut "
                        f"{row['mut_freq_pct']}% outside expected {lo}-{hi}%")
    return warns
