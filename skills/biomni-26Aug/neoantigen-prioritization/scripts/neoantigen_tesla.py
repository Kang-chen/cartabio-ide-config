"""
neoantigen_tesla — end-to-end TESLA-guided neoantigen prioritization orchestrator.

Pipeline (REAL-DATA-ONLY):
  somatic VCF ──parse_vcf──▶ variants (missense / inframe-indel / frameshift, germline-filtered)
      │                                    │
      │ missense                           │ indel / frameshift
      ▼                                    ▼
  generate_peptides (reused audited      generate_indel_peptides (neoORF 8-11mers from real
  core: real protein from UniProt/        Ensembl CDS, mutant translated to first stop)
  Ensembl, WT residue validated,
  matched WT peptides)                      │
      │  pep_index                          │  pep_index (same shape)
      └───────────────┬─────────────────────┘
                      ▼  merged pep_index
        predict_binding  ── MHCflurry peptide-MHC-I on the patient's real HLA-I (mut + WT)
                      ▼  rank_cache / affinity_cache keyed by (peptide, allele)
        flatten -> per (variant, mut peptide) best-allele candidate (mut_rank, wt_rank, affinity)
                      ▼
        join_expression           ── real TPM table (gene/transcript) -> tumor abundance
                      ▼
        score_candidates          ── TESLA 5 features + composite priority + Tier 1/2/3
                      ▼
        ranked neoantigens.csv / prioritized.csv / summary.csv  (+ plots + PDF downstream)

MHCflurry is MANDATORY. If it is not installed (or no real HLA / no real peptides could be
generated), the run raises ``EngineUnavailable`` and emits NO binding/priority numbers. Missing
expression or VAF stays ``None`` and never becomes a fabricated value.
"""

from __future__ import annotations

import json
import os
from typing import Optional

# local modules (this dir is on sys.path when run as a skill)
from binding_core import (
    EngineUnavailable, DEFAULT_LENGTHS, HAS_MHCFLURRY,
    generate_peptides, predict_binding, classify, core_provenance,
)
from vcf_to_variants import parse_vcf
from peptides_indel import generate_indel_peptides
from expression_join import join_expression, rna_vaf_from_bam
from tesla_features import score_candidates, feature_provenance


def _normalize_hla(hla) -> list[str]:
    """Accept a list or a comma/space-separated string; normalise to 'HLA-A*02:01' forms."""
    if isinstance(hla, str):
        parts = [p.strip() for p in hla.replace(",", " ").split()]
    else:
        parts = [str(p).strip() for p in (hla or [])]
    out, seen = [], set()
    for p in parts:
        if not p:
            continue
        q = p if p.upper().startswith("HLA-") else "HLA-" + p
        if q not in seen:
            seen.add(q)
            out.append(q)
    if not out:
        raise EngineUnavailable(
            "No real HLA-I genotype supplied. Provide the patient's HLA-I alleles "
            "(e.g. ['HLA-A*02:01','HLA-B*07:02']) or type them upstream (OptiType/arcasHLA). "
            "HLA is never invented.")
    return out


def _variants_to_case(pvs: list[dict]) -> dict:
    """Convert parsed missense peptide-variants into a `case` dict for reused generate_peptides.

    generate_peptides validates the WT residue against the real protein, so we pass through the
    gene, the HGVSp-derived 'V600E' short form, and any protein handles (uniprot/ensembl). Real
    per-variant CCF and expression are attached where present (never fabricated).
    """
    variants = []
    for pv in pvs:
        if pv.get("var_class") != "missense":
            continue
        aa = pv.get("variant") or pv.get("aa_change")   # 1-letter 'V600E' (parse_vcf key: 'variant')
        if not aa:
            continue
        variants.append({
            "gene": pv.get("gene"),
            "variant": aa,
            "type": "missense",
            "uniprot": pv.get("uniprot"),
            "ensembl_protein": pv.get("ensembl_protein"),
            "ensembl_transcript": pv.get("ensembl_transcript"),
            "ccf": pv.get("ccf"),
            "expr_tpm": pv.get("expr_tpm"),   # usually None here; expression joined later by gene
            # carry originals so we can re-attach genomic context after peptide generation
            "_chrom": pv.get("chrom"), "_pos": pv.get("pos"), "_alt_dna": pv.get("alt"),
            "_ensembl_gene": pv.get("ensembl_gene"), "_vaf": pv.get("vaf"),
            "_gnomad_af": pv.get("gnomad_af"),
        })
    return {"variants": variants}


def _best_allele(pep: str, hla_list, rank_cache: dict, affinity_cache: dict):
    """Return (best_allele, best_rank, affinity_at_best) — the strongest (min %rank) allele."""
    best = (None, None, None)
    for a in hla_list:
        r = rank_cache.get((pep, a))
        if r is None:
            continue
        if best[1] is None or r < best[1]:
            best = (a, r, affinity_cache.get((pep, a)))
    return best


def _flatten_pep_index(pep_index: dict, hla_list, rank_cache, affinity_cache,
                       *, var_meta: Optional[dict] = None) -> list[dict]:
    """Turn a scored pep_index into one candidate per (variant, mutant peptide).

    Each candidate gets the best-allele mut %rank + affinity and the matched WT peptide's
    best %rank (for agretopicity). ``var_meta`` optionally maps variant-id -> extra genomic
    fields (chrom/pos/vaf/…) to re-attach.
    """
    var_meta = var_meta or {}
    cands = []
    for vid, v in pep_index.items():
        wt_by_mi = {mi: pep for pep, mi in v.get("wt_peptides", [])}
        meta = var_meta.get(vid, {})
        for pep, mi in v.get("mut_peptides", []):
            a, r, aff = _best_allele(pep, hla_list, rank_cache, affinity_cache)
            if r is None:
                continue  # peptide unscored (e.g. length unsupported) -> drop, never fabricate
            wt_pep = wt_by_mi.get(mi)
            wt_rank = None
            if wt_pep:
                _, wr, _ = _best_allele(wt_pep, hla_list, rank_cache, affinity_cache)
                wt_rank = wr
            cands.append({
                "gene": v.get("gene"), "variant": v.get("variant"),
                "var_class": v.get("var_class", meta.get("var_class", "missense")),
                "peptide": pep, "wt_peptide": wt_pep,
                "mut_pos_in_pep": (mi + 1) if (mi is not None and mi >= 0) else None,  # ->1-based
                "length": len(pep),
                "hla_best": a, "mut_rank": r, "wt_rank": wt_rank, "affinity_nm": aff,
                "presentation_score": None,  # populated below from %rank if desired
                "ccf": v.get("ccf"),
                "expr_tpm": v.get("expr_tpm"),
                "vaf": meta.get("vaf") if meta.get("vaf") is not None else v.get("vaf"),
                "gnomad_af": meta.get("gnomad_af"),
                "chrom": meta.get("chrom"), "pos": meta.get("pos"),
                "alt_dna": meta.get("alt_dna"),
                "ensembl_gene": meta.get("ensembl_gene"),
                "ensembl_transcript": meta.get("ensembl_transcript") or v.get("source_seq"),
                "is_neoorf": v.get("is_neoorf", False),
            })
    return cands


def _presentation_from_rank(mut_rank: Optional[float]) -> Optional[float]:
    """MHCflurry presentation-score proxy for the stability feature: 1 - rank/100, clamped."""
    if mut_rank is None:
        return None
    return max(0.0, min(1.0, 1.0 - mut_rank / 100.0))


def run_neoantigen_tesla(
    vcf_path: str,
    hla,
    *,
    expression_table: Optional[str] = None,
    rna_bam: Optional[str] = None,
    tumor_sample: Optional[str] = None,
    lengths=DEFAULT_LENGTHS,
    germline_af_max: float = 0.001,
    use_vep_rest: bool = True,
    include_indels: bool = True,
    stability_map: Optional[dict] = None,
    require_mhcflurry: bool = True,
) -> dict:
    """Run the full TESLA-guided neoantigen prioritization and return an analysis dict."""
    if require_mhcflurry and not HAS_MHCFLURRY:
        raise EngineUnavailable(
            "MHCflurry is required and not installed. Install with:\n"
            "    pip install mhcflurry && mhcflurry-downloads fetch\n"
            "There is no synthetic binding fallback — no neoantigen numbers are emitted without it.")

    hla_list = _normalize_hla(hla)
    print(f"[tesla] HLA-I: {hla_list}")

    # 1) variants
    vres = parse_vcf(vcf_path, tumor_sample=tumor_sample, germline_af_max=germline_af_max,
                     use_vep_rest=use_vep_rest)
    pvs = vres["peptide_variants"]
    print(f"[tesla] {len(pvs)} peptide-generating variants after filtering")

    # 2a) missense peptides via reused generate_peptides (real protein + WT validation)
    case = _variants_to_case(pvs)
    missense_index = generate_peptides(case, lengths=lengths) if case["variants"] else {}
    # re-attach genomic metadata to each missense variant id
    var_meta = {}
    # generate_peptides keys are f'{gene}:{variant}:{i}' following the case variant order
    case_vars = case["variants"]
    for vid in missense_index:
        # recover the index suffix
        try:
            i = int(vid.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            i = None
        m = case_vars[i] if (i is not None and i < len(case_vars)) else {}
        var_meta[vid] = {
            "var_class": "missense", "chrom": m.get("_chrom"), "pos": m.get("_pos"),
            "alt_dna": m.get("_alt_dna"), "ensembl_gene": m.get("_ensembl_gene"),
            "ensembl_transcript": m.get("ensembl_transcript"),
            "vaf": m.get("_vaf"), "gnomad_af": m.get("_gnomad_af"),
        }

    # 2b) indel neoORF peptides (same pep_index shape); carry class + genomic fields inline
    indel_index = {}
    if include_indels:
        indel_pvs = [pv for pv in pvs if pv.get("var_class") in ("frameshift", "inframe_indel")]
        if indel_pvs:
            raw = generate_indel_peptides(indel_pvs, lengths=lengths)
            for vid, v in raw.items():
                v["is_neoorf"] = True
                v.setdefault("var_class", "indel")
                indel_index[vid] = v

    merged = {**missense_index, **indel_index}
    if not merged:
        raise EngineUnavailable(
            "No real mutant peptides could be generated from the validated variants "
            "(no missense produced windows and no indel neoORFs). No numbers are emitted. "
            "Check the VCF annotation (gene/HGVSp/HGVSc) and network access to UniProt/Ensembl.")
    n_mut = sum(len(v.get("mut_peptides", [])) for v in merged.values())
    print(f"[tesla] {len(merged)} variants -> {n_mut} candidate peptides "
          f"({len(missense_index)} missense + {len(indel_index)} indel neoORF variants)")

    # 3) MHCflurry binding on the patient's real HLA-I (mutant + matched WT)
    rank_cache, affinity_cache, engine = predict_binding(merged, hla_list)

    # 4) flatten to candidates
    candidates = _flatten_pep_index(merged, hla_list, rank_cache, affinity_cache, var_meta=var_meta)
    for c in candidates:
        c["presentation_score"] = _presentation_from_rank(c.get("mut_rank"))
    if not candidates:
        raise EngineUnavailable(
            "No candidate peptide scored on the provided HLA-I. Check that the alleles are "
            "MHCflurry-supported (4-digit) and that peptides are 8-11mers.")
    print(f"[tesla] {len(candidates)} scored candidate peptides (engine: {engine})")

    # 5) expression -> tumor abundance
    candidates = join_expression(candidates, expression_table)
    if rna_bam:
        candidates = rna_vaf_from_bam(candidates, rna_bam)
        for c in candidates:
            if c.get("rna_vaf") is not None:
                c["vaf_dna"] = c.get("vaf")
                c["vaf"] = c["rna_vaf"]

    # 6) TESLA 5-feature scoring + tiering
    candidates = score_candidates(candidates, stability_map=stability_map)

    tiers = {}
    for c in candidates:
        tiers[c["tier"]] = tiers.get(c["tier"], 0) + 1
    print(f"[tesla] tiers: {tiers}")

    analysis = {
        "hla": hla_list,
        "engine": engine,
        "vcf_build": vres.get("build"),
        "n_variants_total": vres.get("n_records"),
        "n_peptide_variants": len(pvs),
        "n_missense_variants_scored": len(missense_index),
        "n_indel_variants_scored": len(indel_index),
        "n_candidates": len(candidates),
        "candidates": candidates,
        "tier_counts": tiers,
        "expression_table": expression_table,
        "rna_bam": rna_bam,
        "provenance": {
            "binding_core": core_provenance(),
            "tesla_features": feature_provenance(),
            "vcf": {"build": vres.get("build"), "germline_af_max": germline_af_max,
                    "use_vep_rest": use_vep_rest},
        },
    }
    return analysis


# =============================================================================
# Exports
# =============================================================================
_CSV_COLS = [
    "tier", "priority_score", "gene", "variant", "var_class", "peptide", "length",
    "hla_best", "mut_rank", "binding_class", "wt_rank", "agretopicity", "agretopicity_score",
    "foreignness", "dissim_to_self", "affinity_nm",
    "expr_tpm", "vaf", "ccf", "abundance",
    "fraction_hydrophobic", "mut_pos_in_pep", "is_anchor",
    "stability_score", "stability_source",
    "chrom", "pos", "ensembl_transcript",
]


def _flatten(c: dict) -> dict:
    t = c.get("tesla", {})
    ta = t.get("tumor_abundance") or {}
    mp = t.get("mutation_position") or {}
    st = t.get("binding_stability") or {}
    fr = t.get("foreignness") or {}
    return {
        "tier": c.get("tier"), "priority_score": c.get("priority_score"),
        "gene": c.get("gene"), "variant": c.get("variant"), "var_class": c.get("var_class"),
        "peptide": c.get("peptide"), "length": c.get("length"),
        "hla_best": c.get("hla_best"),
        "mut_rank": c.get("mut_rank"), "binding_class": t.get("binding_class"),
        "wt_rank": c.get("wt_rank"), "agretopicity": t.get("agretopicity"),
        "agretopicity_score": t.get("agretopicity_score"),
        "foreignness": fr.get("score"), "dissim_to_self": (
            None if fr.get("self_similarity") is None else round(1.0 - fr.get("self_similarity"), 4)),
        "affinity_nm": c.get("affinity_nm"),
        "expr_tpm": c.get("expr_tpm"), "vaf": c.get("vaf"), "ccf": c.get("ccf"),
        "abundance": ta.get("abundance"),
        "fraction_hydrophobic": t.get("fraction_hydrophobic"),
        "mut_pos_in_pep": mp.get("position"), "is_anchor": mp.get("is_anchor"),
        "stability_score": st.get("score"), "stability_source": st.get("source"),
        "chrom": c.get("chrom"), "pos": c.get("pos"),
        "ensembl_transcript": c.get("ensembl_transcript"),
    }


def export_results(analysis: dict, output_dir: str) -> dict:
    """Write neoantigens.csv (all), prioritized.csv (Tier1/2), summary.csv, analysis.json."""
    import pandas as pd
    os.makedirs(output_dir, exist_ok=True)
    rows = [_flatten(c) for c in analysis["candidates"]]
    df = pd.DataFrame(rows, columns=_CSV_COLS)

    all_csv = os.path.join(output_dir, "neoantigens.csv")
    df.to_csv(all_csv, index=False)

    prio = df[df["tier"].isin(["Tier1", "Tier2"])].copy()
    prio_csv = os.path.join(output_dir, "prioritized_neoantigens.csv")
    prio.to_csv(prio_csv, index=False)

    summ = (df.groupby("tier").size().rename("n").reset_index().sort_values("tier"))
    summ_csv = os.path.join(output_dir, "summary.csv")
    summ.to_csv(summ_csv, index=False)

    json_path = os.path.join(output_dir, "analysis.json")
    with open(json_path, "w") as f:
        json.dump({k: v for k, v in analysis.items() if k != "candidates"} |
                  {"candidates": rows}, f, indent=2, default=str)

    out = {"neoantigens_csv": all_csv, "prioritized_csv": prio_csv,
           "summary_csv": summ_csv, "analysis_json": json_path}
    print(f"[tesla] wrote: {list(out.values())}")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="TESLA-guided neoantigen prioritization")
    ap.add_argument("--vcf", required=True)
    ap.add_argument("--hla", required=True, help="comma/space separated HLA-I alleles")
    ap.add_argument("--expression", default=None)
    ap.add_argument("--rna-bam", default=None)
    ap.add_argument("--tumor-sample", default=None)
    ap.add_argument("--out", default="results")
    ap.add_argument("--no-indels", action="store_true")
    ap.add_argument("--no-vep-rest", action="store_true")
    args = ap.parse_args()

    analysis = run_neoantigen_tesla(
        args.vcf, args.hla, expression_table=args.expression, rna_bam=args.rna_bam,
        tumor_sample=args.tumor_sample, include_indels=not args.no_indels,
        use_vep_rest=not args.no_vep_rest)
    export_results(analysis, args.out)
