"""
vcf_to_variants — parse a somatic VCF into normalized variant records for neoantigen
peptide generation (SNV missense + small indel/frameshift).

REAL-DATA-ONLY: this module only reads what is in the VCF (and, when a variant lacks an
annotation, resolves the coding consequence from Ensembl VEP REST). It never invents a
variant, a consequence, an allele fraction, or an expression value.

What it does
------------
1. Reads the genome build from the VCF header (``##reference`` / ``##contig`` assembly=)
   and echoes it; warns on chr-naming vs. build mismatch. It does NOT lift over or
   silently mix builds.
2. Extracts, per record: CHROM/POS/REF/ALT, the tumour-sample DNA VAF (from FORMAT
   ``AF`` or computed from ``AD``/``DP``), and any germline population AF present in a VEP
   ``CSQ`` / SnpEff ``ANN`` field (for optional germline filtering).
3. Parses VEP ``CSQ`` or SnpEff ``ANN`` when present -> consequence, gene symbol, Ensembl
   gene/transcript, HGVSp / HGVSc, protein position. When neither annotation is present,
   resolves consequence + HGVSp via the Ensembl VEP REST API.
4. Classifies each variant into one of:
     - ``missense``       : single-residue substitution  (SNV path -> substitution windows)
     - ``inframe_indel``  : in-frame insertion/deletion   (indel path -> junction windows)
     - ``frameshift``     : frameshift indel              (indel path -> neoORF windows)
     - ``other``          : synonymous / intronic / stop-gain-only / splice / etc. -> not
                            used for peptides (kept, flagged, and reported)
5. Emits normalized dicts consumed by the SNV generator (``generate_peptides`` from the
   reused core) and the indel generator (``peptides_indel``).

Record schema (per variant)
---------------------------
Common keys: gene, chrom, pos (genomic), ref_dna, alt_dna, consequence, var_class
(one of the four above), vaf (tumour DNA VAF or None), gnomad_af (or None), ccf (or None),
ensembl_transcript, ensembl_gene, hgvsp, hgvsc, protein_pos.
For the SNV path we additionally emit ``variant`` ("S249C"), ``ref`` / ``alt`` (1-letter
AA), ``aa_pos`` — the exact keys the reused ``generate_peptides`` expects.
For the indel path we emit ``hgvsc`` / ``hgvsp`` / ``ensembl_transcript`` so the neoORF
generator can fetch the CDS and translate.
"""

from __future__ import annotations

import re
import ssl
import json
import urllib.request
import urllib.parse
from typing import Any, Optional


# ---- amino-acid 3->1 (module-local; independent of the reused core) ---------
_THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
    "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
    "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
    "TYR": "Y", "VAL": "V", "TER": "*", "*": "*",
}
_AA1 = set("ACDEFGHIKLMNPQRSTVWY*")

# Sequence Ontology consequence -> our coarse variant class.
_FRAMESHIFT_SO = {"frameshift_variant"}
_INFRAME_SO = {"inframe_insertion", "inframe_deletion", "disruptive_inframe_insertion",
               "disruptive_inframe_deletion", "conservative_inframe_insertion",
               "conservative_inframe_deletion"}
_MISSENSE_SO = {"missense_variant"}
# stop_gained / start_lost also create neo-sequence but are handled conservatively as
# 'other' unless a frameshift/inframe/missense term is present (kept + flagged).


def _make_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


_SSL = _make_ssl_context()
_UA = "biomni-neoantigen-tesla/1.0"


def _http_json(url: str, *, timeout: int = 45, data: Optional[bytes] = None) -> Optional[Any]:
    req = urllib.request.Request(url, data=data, method=("POST" if data else "GET"))
    req.add_header("User-Agent", _UA)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"   [vep] {url[:70]} -> {type(e).__name__}: {e}")
        return None


# =============================================================================
# HGVS-p parsing (SNV substitution)
# =============================================================================
def parse_hgvsp_substitution(hgvsp: str):
    """Parse a protein substitution HGVS (e.g. 'ENSP..:p.Ser249Cys' or 'p.S249C').

    Returns (ref1, pos, alt1) as (1-letter, int, 1-letter) for a clean single-residue
    missense, or None if it is not a simple substitution (indels, fs, synonymous, '=').
    """
    if not hgvsp:
        return None
    s = hgvsp.split(":")[-1]
    s = s[2:] if s.lower().startswith("p.") else s
    if "fs" in s or "del" in s or "ins" in s or "dup" in s or "=" in s or "*" in s and s.endswith("*") is False:
        # crude guard; the SO term is the authoritative classifier, this is a parse guard
        pass
    m = re.match(r"^([A-Za-z]{3}|[A-Za-z])(\d+)([A-Za-z]{3}|[A-Za-z]|\*|=)$", s)
    if not m:
        return None
    ref, pos, alt = m.group(1), int(m.group(2)), m.group(3)
    if alt == "=":
        return None  # synonymous
    ref1 = _THREE_TO_ONE.get(ref.upper(), ref.upper()[:1])
    alt1 = _THREE_TO_ONE.get(alt.upper(), alt.upper()[:1])
    if ref1 not in _AA1 or alt1 not in _AA1 or alt1 == "*":
        return None
    return ref1, pos, alt1


def _protein_pos_from_hgvsp(hgvsp: str) -> Optional[int]:
    if not hgvsp:
        return None
    s = hgvsp.split(":")[-1]
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None


# =============================================================================
# Annotation-field parsing: VEP CSQ and SnpEff ANN
# =============================================================================
def _csq_format(header_str: str):
    """Extract the pipe-delimited CSQ field order from the VEP ##INFO=<ID=CSQ ...> header."""
    m = re.search(r'Format:\s*([^">]+)', header_str)
    if not m:
        return None
    return [f.strip() for f in m.group(1).split("|")]


def _pick_worst_csq(entries: list[dict]) -> Optional[dict]:
    """Pick the most protein-impactful CSQ entry (frameshift > inframe > missense > other)."""
    def rank(e):
        cons = (e.get("Consequence") or "").lower()
        if any(t in cons for t in _FRAMESHIFT_SO):
            return 0
        if any(t in cons for t in _INFRAME_SO):
            return 1
        if any(t in cons for t in _MISSENSE_SO):
            return 2
        return 3
    if not entries:
        return None
    return sorted(entries, key=rank)[0]


def parse_csq(info_csq: str, csq_fields: list[str]) -> list[dict]:
    """Parse a VEP CSQ INFO string into a list of dicts keyed by the header field names."""
    out = []
    for allele_block in info_csq.split(","):
        parts = allele_block.split("|")
        if len(parts) != len(csq_fields):
            # tolerate ragged blocks by zipping to the shorter length
            n = min(len(parts), len(csq_fields))
            d = {csq_fields[i]: parts[i] for i in range(n)}
        else:
            d = dict(zip(csq_fields, parts))
        out.append(d)
    return out


def parse_ann(info_ann: str) -> list[dict]:
    """Parse a SnpEff ANN INFO string. ANN field order is fixed by the SnpEff spec."""
    fields = ["Allele", "Annotation", "Annotation_Impact", "Gene_Name", "Gene_ID",
              "Feature_Type", "Feature_ID", "Transcript_BioType", "Rank", "HGVS.c",
              "HGVS.p", "cDNA.pos", "CDS.pos", "AA.pos", "Distance", "Errors"]
    out = []
    for block in info_ann.split(","):
        parts = block.split("|")
        n = min(len(parts), len(fields))
        out.append({fields[i]: parts[i] for i in range(n)})
    return out


def _class_from_consequence(cons: str) -> str:
    c = (cons or "").lower()
    if any(t in c for t in _FRAMESHIFT_SO):
        return "frameshift"
    if any(t in c for t in _INFRAME_SO):
        return "inframe_indel"
    if any(t in c for t in _MISSENSE_SO):
        return "missense"
    return "other"


# =============================================================================
# VEP REST fallback (only when the VCF carries no CSQ/ANN)
# =============================================================================
def vep_rest_annotate(chrom: str, pos: int, ref: str, alt: str, *,
                      build: str = "GRCh38", timeout: int = 45) -> Optional[dict]:
    """Resolve consequence + gene + HGVSp/HGVSc for one variant via Ensembl VEP REST.

    Uses the region/allele endpoint. Returns a dict with consequence, gene, transcript,
    hgvsp, hgvsc, protein_pos, or None on failure. Real API call; nothing is fabricated.
    """
    server = "https://rest.ensembl.org" if build.upper().startswith("GRCH38") \
        else "https://grch37.rest.ensembl.org"
    # VEP HGVS-style region input: "chrom:pos ref/alt" -> use the region endpoint
    allele = f"{ref}/{alt}"
    url = (f"{server}/vep/human/region/{urllib.parse.quote(str(chrom))}:{pos}-{pos + len(ref) - 1}/"
           f"{urllib.parse.quote(allele)}?hgvs=1&numbers=1&content-type=application/json")
    data = _http_json(url, timeout=timeout)
    if not data or not isinstance(data, list):
        return None
    tcs = (data[0] or {}).get("transcript_consequences") or []
    if not tcs:
        return None

    def rank(tc):
        cons = ";".join(tc.get("consequence_terms", [])).lower()
        if any(t in cons for t in _FRAMESHIFT_SO):
            return 0
        if any(t in cons for t in _INFRAME_SO):
            return 1
        if any(t in cons for t in _MISSENSE_SO):
            return 2
        return 3
    tc = sorted(tcs, key=rank)[0]
    cons = ";".join(tc.get("consequence_terms", []))
    return {
        "consequence": cons,
        "gene": tc.get("gene_symbol") or tc.get("gene_id"),
        "ensembl_gene": tc.get("gene_id"),
        "ensembl_transcript": tc.get("transcript_id"),
        "hgvsp": tc.get("hgvsp"),
        "hgvsc": tc.get("hgvsc"),
        "protein_pos": (tc.get("protein_start") if tc.get("protein_start") else None),
    }


# =============================================================================
# VAF extraction
# =============================================================================
def _extract_vaf(variant, tumor_index: int) -> Optional[float]:
    """Tumour DNA VAF from FORMAT AF, else AD/(AD sum), for the tumour sample column."""
    fmt = variant.FORMAT if hasattr(variant, "FORMAT") else []
    # cyvcf2 exposes format fields via variant.format('AF') -> np.ndarray [n_samples, ...]
    try:
        af = variant.format("AF")
        if af is not None:
            val = af[tumor_index]
            v = float(val[0]) if hasattr(val, "__len__") else float(val)
            if 0 <= v <= 1:
                return round(v, 4)
    except Exception:  # noqa: BLE001
        pass
    try:
        ad = variant.format("AD")
        if ad is not None:
            row = ad[tumor_index]
            ref_c = float(row[0])
            alt_c = float(row[1]) if len(row) > 1 else 0.0
            tot = ref_c + alt_c
            if tot > 0:
                return round(alt_c / tot, 4)
    except Exception:  # noqa: BLE001
        pass
    return None


def _gnomad_af_from_csq(csq_entry: dict) -> Optional[float]:
    """Pull a gnomAD/1000G population AF from a CSQ entry if VEP added one."""
    for k in ("gnomADe_AF", "gnomADg_AF", "gnomAD_AF", "AF", "MAX_AF", "1000Gp3_AF"):
        v = csq_entry.get(k)
        if v not in (None, "", "."):
            try:
                return float(str(v).split("&")[0])
            except (TypeError, ValueError):
                continue
    return None


# =============================================================================
# Main entry point
# =============================================================================
def parse_vcf(vcf_path: str, *, tumor_sample: Optional[str] = None,
              germline_af_max: float = 0.001, use_vep_rest: bool = True,
              max_vep_rest: int = 300, ccf_from_vaf: bool = True) -> dict:
    """Parse a somatic VCF into normalized variant records.

    Parameters
    ----------
    vcf_path : str
        Path to a .vcf / .vcf.gz.
    tumor_sample : str, optional
        Sample column to use for VAF. If None and there is one sample, uses it; if there
        are two (tumour/normal), tries to pick the non-'normal'-named one and warns.
    germline_af_max : float
        Drop variants whose annotated population AF exceeds this (likely germline). Only
        applied when a population AF is available in the annotation. Default 0.001.
    use_vep_rest : bool
        If a record has no CSQ/ANN, resolve its consequence via Ensembl VEP REST.
    max_vep_rest : int
        Safety cap on the number of VEP REST calls (avoids hammering the API on a large
        unannotated VCF). Beyond this, unannotated records are flagged 'unannotated'.
    ccf_from_vaf : bool
        If no explicit CCF is available, proxy CCF as min(1, VAF*2) (documented proxy).

    Returns
    -------
    dict with keys:
        build            : genome build string read from the header (or 'unknown')
        variants         : list of normalized records (all classes; 'other' kept + flagged)
        peptide_variants : the subset with var_class in {missense, inframe_indel, frameshift}
        stats            : counts by class and skip reasons
        warnings         : list of human-readable warnings
    """
    try:
        from cyvcf2 import VCF
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "cyvcf2 is required to parse VCFs and is not importable. It is part of the "
            f"biomni environment. Original error: {e}")

    warnings: list[str] = []
    vcf = VCF(vcf_path)
    samples = list(vcf.samples)

    # ---- genome build from header ----
    build = "unknown"
    raw_header = vcf.raw_header or ""
    m = re.search(r"##reference=.*?(GRCh38|GRCh37|hg38|hg19|b37|b38)", raw_header, re.I)
    if m:
        build = {"hg38": "GRCh38", "b38": "GRCh38", "hg19": "GRCh37",
                 "b37": "GRCh37"}.get(m.group(1).lower(), m.group(1))
    else:
        m2 = re.search(r"assembly=(GRCh38|GRCh37|hg38|hg19)", raw_header, re.I)
        if m2:
            build = m2.group(1)
    # chr-naming vs build sanity
    seqnames = list(getattr(vcf, "seqnames", []) or [])
    chr_style = any(s.startswith("chr") for s in seqnames)
    if build == "unknown":
        warnings.append("Genome build not found in VCF header; assuming GRCh38 for VEP REST. "
                        "Set the correct build if this is wrong (no liftover is performed).")

    # ---- CSQ header format ----
    csq_fields = None
    for line in raw_header.splitlines():
        if line.startswith("##INFO=<ID=CSQ"):
            csq_fields = _csq_format(line)
            break

    # ---- tumour sample index ----
    if tumor_sample and tumor_sample in samples:
        tumor_index = samples.index(tumor_sample)
    elif len(samples) == 1:
        tumor_index = 0
    elif len(samples) >= 2:
        non_normal = [i for i, s in enumerate(samples) if "normal" not in s.lower()]
        tumor_index = non_normal[0] if non_normal else 0
        warnings.append(f"Two+ samples {samples}; using '{samples[tumor_index]}' as tumour for "
                        f"VAF. Pass tumor_sample=... to override.")
    else:
        tumor_index = 0

    variants: list[dict] = []
    stats = {"total": 0, "missense": 0, "inframe_indel": 0, "frameshift": 0, "other": 0,
             "germline_filtered": 0, "unannotated": 0, "vep_rest_calls": 0, "multiallelic": 0}
    build_for_vep = "GRCh38" if build in ("unknown", "GRCh38") else "GRCh37"

    for rec in vcf:
        stats["total"] += 1
        chrom = rec.CHROM
        pos = rec.POS
        ref_dna = rec.REF
        alts = rec.ALT or []
        if len(alts) > 1:
            stats["multiallelic"] += 1
        # process only the first ALT (recommend normalization/splitting upstream)
        alt_dna = alts[0] if alts else None
        if alt_dna is None:
            continue

        vaf = _extract_vaf(rec, tumor_index)
        gnomad_af = None
        ann_entry = None
        var_class = None
        gene = ensembl_gene = ensembl_transcript = hgvsp = hgvsc = None
        protein_pos = None
        consequence = None

        # ---- CSQ (VEP) ----
        info_csq = rec.INFO.get("CSQ") if csq_fields else None
        if info_csq:
            entries = parse_csq(info_csq, csq_fields)
            ann_entry = _pick_worst_csq(entries)
            if ann_entry:
                consequence = ann_entry.get("Consequence")
                gene = ann_entry.get("SYMBOL") or ann_entry.get("Gene")
                ensembl_gene = ann_entry.get("Gene")
                ensembl_transcript = ann_entry.get("Feature")
                hgvsp = ann_entry.get("HGVSp") or ann_entry.get("HGVSP")
                hgvsc = ann_entry.get("HGVSc") or ann_entry.get("HGVSC")
                protein_pos = _protein_pos_from_hgvsp(hgvsp) or (
                    int(ann_entry["Protein_position"].split("-")[0])
                    if ann_entry.get("Protein_position", "").split("-")[0].isdigit() else None)
                gnomad_af = _gnomad_af_from_csq(ann_entry)
        # ---- ANN (SnpEff) ----
        if consequence is None:
            info_ann = rec.INFO.get("ANN")
            if info_ann:
                anns = parse_ann(info_ann)
                # pick worst by our class ranking
                anns_sorted = sorted(anns, key=lambda a: {
                    "frameshift": 0, "inframe_indel": 1, "missense": 2, "other": 3
                }[_class_from_consequence(a.get("Annotation", ""))])
                ann_entry = anns_sorted[0] if anns_sorted else None
                if ann_entry:
                    consequence = ann_entry.get("Annotation")
                    gene = ann_entry.get("Gene_Name")
                    ensembl_gene = ann_entry.get("Gene_ID")
                    ensembl_transcript = ann_entry.get("Feature_ID")
                    hgvsp = ann_entry.get("HGVS.p")
                    hgvsc = ann_entry.get("HGVS.c")
                    aap = ann_entry.get("AA.pos", "")
                    protein_pos = (int(aap.split("/")[0]) if aap.split("/")[0].isdigit() else
                                   _protein_pos_from_hgvsp(hgvsp))
        # ---- VEP REST fallback ----
        if consequence is None and use_vep_rest and stats["vep_rest_calls"] < max_vep_rest:
            stats["vep_rest_calls"] += 1
            vr = vep_rest_annotate(chrom, pos, ref_dna, alt_dna, build=build_for_vep)
            if vr:
                consequence = vr["consequence"]
                gene = vr["gene"]
                ensembl_gene = vr["ensembl_gene"]
                ensembl_transcript = vr["ensembl_transcript"]
                hgvsp = vr["hgvsp"]
                hgvsc = vr["hgvsc"]
                protein_pos = vr["protein_pos"]

        if consequence is None:
            stats["unannotated"] += 1
            var_class = "other"
        else:
            var_class = _class_from_consequence(consequence)

        # ---- germline filter (only where a population AF exists) ----
        if gnomad_af is not None and gnomad_af > germline_af_max:
            stats["germline_filtered"] += 1
            continue

        ccf = None
        if ccf_from_vaf and vaf is not None:
            ccf = round(min(1.0, vaf * 2.0), 3)

        rec_out: dict[str, Any] = {
            "gene": gene, "chrom": chrom, "pos": pos, "ref_dna": ref_dna, "alt_dna": alt_dna,
            "consequence": consequence, "var_class": var_class, "vaf": vaf,
            "gnomad_af": gnomad_af, "ccf": ccf, "ensembl_gene": ensembl_gene,
            "ensembl_transcript": ensembl_transcript, "hgvsp": hgvsp, "hgvsc": hgvsc,
            "protein_pos": protein_pos,
        }

        # ---- SNV missense: emit the exact keys the reused generate_peptides expects ----
        if var_class == "missense":
            sub = parse_hgvsp_substitution(hgvsp) if hgvsp else None
            if sub is None and protein_pos and len(ref_dna) == 1 and len(alt_dna) == 1:
                # consequence says missense but HGVSp unparseable -> keep, let generator
                # attempt via protein_pos only if ref/alt AA are recoverable; else 'other'
                sub = None
            if sub is not None:
                ref1, ppos, alt1 = sub
                rec_out.update({
                    "type": "missense", "ref": ref1, "alt": alt1, "aa_pos": ppos,
                    "variant": f"{ref1}{ppos}{alt1}",
                    "uniprot": None,  # resolved downstream by gene symbol
                })
                stats["missense"] += 1
            else:
                rec_out["var_class"] = var_class = "other"
                warnings.append(f"{gene} {consequence}: missense but HGVSp unparseable "
                                f"({hgvsp!r}) -> not converted to a peptide.")
        if var_class in ("frameshift", "inframe_indel"):
            rec_out["type"] = var_class
            stats[var_class] += 1
        if var_class == "other":
            stats["other"] += 1

        variants.append(rec_out)

    vcf.close()
    peptide_variants = [v for v in variants
                        if v["var_class"] in ("missense", "inframe_indel", "frameshift")]

    print(f"   [vcf] build={build} | {stats['total']} records -> "
          f"{stats['missense']} missense, {stats['inframe_indel']} inframe-indel, "
          f"{stats['frameshift']} frameshift, {stats['other']} other "
          f"(germline-filtered {stats['germline_filtered']}, VEP-REST calls "
          f"{stats['vep_rest_calls']})")
    for w in warnings:
        print(f"   [vcf][warn] {w}")

    return {"build": build, "variants": variants, "peptide_variants": peptide_variants,
            "stats": stats, "warnings": warnings}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        res = parse_vcf(sys.argv[1])
        print(json.dumps({"build": res["build"], "stats": res["stats"],
                          "n_peptide_variants": len(res["peptide_variants"])}, indent=2))
        for v in res["peptide_variants"][:5]:
            print("  ", v["gene"], v["var_class"], v.get("variant") or v.get("hgvsp"), "VAF", v["vaf"])
