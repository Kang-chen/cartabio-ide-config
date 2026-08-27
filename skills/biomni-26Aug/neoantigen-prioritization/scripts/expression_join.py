"""
expression_join — attach RNA-seq expression (TPM/FPKM) to variant records so the TESLA
'tumor abundance' feature can be computed.

REAL-DATA-ONLY: expression comes only from a user-supplied quantification table (or,
optionally, allele-level read counts pulled from a real RNA-seq BAM). If a gene has no
matching row, its expression is left as ``None`` (not provided) — never back-filled with a
representative or default value.

Primary path (recommended, default): a gene- or transcript-level TPM/FPKM matrix, e.g. the
output of Salmon / kallisto / STAR + featureCounts. The table is joined to each variant by
gene symbol or Ensembl gene/transcript id (auto-detected).

Optional refinement: ``rna_vaf_from_bam`` estimates the mutant-allele expression (RNA VAF)
at each variant's genomic locus from an RNA-seq BAM via a pysam pileup, so 'tumor abundance'
can reflect mutant-allele read support rather than gene-level TPM alone.
"""

from __future__ import annotations

import os
import re
from typing import Optional


def _read_table(path: str):
    """Read a CSV/TSV expression table into a pandas DataFrame (delimiter auto-detected)."""
    import pandas as pd
    sep = "\t" if path.lower().endswith((".tsv", ".txt", ".tsv.gz", ".txt.gz")) else None
    # sep=None with engine='python' sniffs the delimiter
    df = pd.read_csv(path, sep=sep, engine="python")
    return df


def _detect_columns(df):
    """Guess (id_col, value_col, id_kind) from an expression table.

    id_kind is 'ensembl_gene' | 'ensembl_transcript' | 'symbol'. The value column is the
    first column whose name matches TPM/FPKM/expression/abundance (case-insensitive), else
    the first numeric column.
    """
    import pandas as pd  # noqa: F401
    cols = list(df.columns)
    lc = {c: str(c).lower() for c in cols}

    # value column
    value_col = None
    for c in cols:
        if re.search(r"\b(tpm|fpkm|expression|abundance|counts?)\b", lc[c]):
            value_col = c
            break
    if value_col is None:
        num = df.select_dtypes("number").columns.tolist()
        value_col = num[0] if num else cols[-1]

    # id column + kind
    id_col, id_kind = None, None
    # sample values to sniff Ensembl ids
    def sniff_kind(series):
        s = series.astype(str).head(50)
        if s.str.match(r"ENS[A-Z]*G\d+").any():
            return "ensembl_gene"
        if s.str.match(r"ENS[A-Z]*T\d+").any():
            return "ensembl_transcript"
        return None
    # prefer explicitly named id columns
    for c in cols:
        if lc[c] in ("gene_id", "geneid", "ensembl_gene_id", "gene"):
            k = sniff_kind(df[c]) or ("symbol" if lc[c] in ("gene",) else "symbol")
            id_col, id_kind = c, (sniff_kind(df[c]) or "symbol")
            break
        if lc[c] in ("transcript_id", "transcriptid", "ensembl_transcript_id", "target_id", "name"):
            id_col, id_kind = c, (sniff_kind(df[c]) or "ensembl_transcript")
            break
        if lc[c] in ("gene_name", "symbol", "genesymbol", "hgnc_symbol"):
            id_col, id_kind = c, "symbol"
            break
    if id_col is None:
        # fall back to the first non-numeric column, sniff its kind
        obj = df.select_dtypes(exclude="number").columns.tolist()
        id_col = obj[0] if obj else cols[0]
        id_kind = sniff_kind(df[id_col]) or "symbol"
    return id_col, value_col, id_kind


def build_expression_index(expr_path: str) -> dict:
    """Build a lookup dict {key(upper) -> value} plus metadata from an expression table.

    Indexes EVERY identifier-like column present (gene symbol, Ensembl gene id, Ensembl
    transcript id / target_id), each keying the same value column, so a variant can be matched
    by whichever id it carries. Ensembl version suffixes are stripped. Duplicates keep the max.
    """
    import pandas as pd  # noqa: F401
    df = _read_table(expr_path)
    id_col, value_col, id_kind = _detect_columns(df)

    # collect all identifier-like columns (not just the primary one)
    lc = {c: str(c).lower() for c in df.columns}
    id_cols = set()
    for c in df.columns:
        name = lc[c]
        if name in ("gene_id", "geneid", "ensembl_gene_id", "gene", "gene_name", "symbol",
                    "genesymbol", "hgnc_symbol", "transcript_id", "transcriptid",
                    "ensembl_transcript_id", "target_id", "name"):
            id_cols.add(c)
        else:
            # sniff Ensembl-looking string columns
            s = df[c].astype(str).head(50)
            if s.str.match(r"ENS[A-Z]*[GT]\d+").any():
                id_cols.add(c)
    id_cols.add(id_col)  # ensure the detected primary is included

    idx = {}
    for _, row in df.iterrows():
        try:
            v = float(row[value_col])
        except (TypeError, ValueError):
            continue
        for c in id_cols:
            raw = row.get(c)
            if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                continue
            k = str(raw).split(".")[0].upper()  # strip Ensembl version
            if not k or k == "NAN":
                continue
            if k not in idx or v > idx[k]:  # keep max on duplicate ids
                idx[k] = v
    print(f"   [expr] table {os.path.basename(expr_path)}: id_cols={sorted(id_cols)} "
          f"(primary {id_kind}), value_col={value_col!r} -> {len(idx)} keys")
    return {"index": idx, "id_kind": id_kind, "value_col": value_col}


def join_expression(variants: list[dict], expr_path: Optional[str]) -> list[dict]:
    """Attach ``expr_tpm`` to each variant record from the expression table.

    Matching order per variant: Ensembl transcript -> Ensembl gene -> gene symbol
    (whichever the table is keyed on and present on the variant). Unmatched -> None.
    Returns the same list (mutated in place) for convenience.
    """
    if not expr_path:
        for v in variants:
            v.setdefault("expr_tpm", None)
        print("   [expr] no expression table provided -> tumor-abundance feature will use "
              "VAF only where available; TPM left as 'not provided' (never fabricated)")
        return variants

    meta = build_expression_index(expr_path)
    idx = meta["index"]
    n_hit = 0
    for v in variants:
        keys = []
        if v.get("ensembl_transcript"):
            keys.append(str(v["ensembl_transcript"]).split(".")[0].upper())
        if v.get("ensembl_gene"):
            keys.append(str(v["ensembl_gene"]).split(".")[0].upper())
        if v.get("gene"):
            keys.append(str(v["gene"]).upper())
        val = None
        for k in keys:
            if k in idx:
                val = idx[k]
                break
        v["expr_tpm"] = val
        if val is not None:
            n_hit += 1
    print(f"   [expr] joined expression to {n_hit}/{len(variants)} variants")
    return variants


# =============================================================================
# Optional: allele-level RNA expression (RNA VAF) from an RNA-seq BAM
# =============================================================================
def rna_vaf_from_bam(variants: list[dict], bam_path: str, *, min_base_quality: int = 20) -> list[dict]:
    """Estimate mutant-allele RNA support (RNA VAF + alt read depth) at each variant locus.

    Uses a pysam pileup restricted to each variant's genomic position — it does NOT load
    the whole BAM. Adds ``rna_alt_count``, ``rna_depth``, ``rna_vaf`` to each record
    (None where the site has no coverage). Real read counts only.

    Note: requires the BAM's contig naming to match the variant CHROM (e.g. both 'chr7' or
    both '7'); a mismatch yields no coverage and is reported.
    """
    try:
        import pysam
    except Exception as e:  # noqa: BLE001
        print(f"   [rna-bam] pysam unavailable ({e}); skipping RNA-VAF refinement")
        return variants
    if not os.path.isfile(bam_path):
        print(f"   [rna-bam] BAM not found: {bam_path}; skipping")
        return variants

    bam = pysam.AlignmentFile(bam_path, "rb")
    bam_contigs = set(bam.references)
    n_cov = 0
    for v in variants:
        chrom = str(v.get("chrom", ""))
        pos = v.get("pos")
        alt = (v.get("alt_dna") or "")
        if not chrom or pos is None:
            continue
        # try both chr and non-chr naming
        use_chrom = chrom if chrom in bam_contigs else (
            chrom[3:] if chrom.startswith("chr") and chrom[3:] in bam_contigs else (
                "chr" + chrom if ("chr" + chrom) in bam_contigs else None))
        if use_chrom is None:
            v["rna_vaf"] = None
            continue
        alt_count = depth = 0
        # SNV: count the alt base at the site; indel: approximate with depth only
        for col in bam.pileup(use_chrom, pos - 1, pos, truncate=True,
                              min_base_quality=min_base_quality, stepper="samtools"):
            for read in col.pileups:
                if read.is_del or read.is_refskip or read.query_position is None:
                    depth += 1 if not read.is_refskip else 0
                    continue
                depth += 1
                base = read.alignment.query_sequence[read.query_position]
                if len(alt) == 1 and base == alt:
                    alt_count += 1
        v["rna_alt_count"] = alt_count
        v["rna_depth"] = depth
        v["rna_vaf"] = round(alt_count / depth, 4) if depth > 0 else None
        if depth > 0:
            n_cov += 1
    bam.close()
    print(f"   [rna-bam] RNA coverage at {n_cov}/{len(variants)} variant loci")
    return variants


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        meta = build_expression_index(sys.argv[1])
        print("id_kind:", meta["id_kind"], "| n:", len(meta["index"]))
        for k, val in list(meta["index"].items())[:5]:
            print("  ", k, val)
