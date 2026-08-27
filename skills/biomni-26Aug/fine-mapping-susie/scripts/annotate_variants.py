#!/usr/bin/env python3
"""
annotate_variants.py -- annotate fine-mapped credible-set variants with three OPTIONAL, independently
skippable layers. None of them is required for a valid fine-mapping result; each adds interpretive
context and each fails soft (logs a note, writes nothing, moves on) so a flaky API never breaks a run.

  Layer 1  GTEx v8 cis-eQTL         -- is the variant an eQTL for nearby genes? direction (NES sign)?
  Layer 2  eQTL Catalogue           -- same question in tissue/cell types GTEx lacks (e.g. pancreatic
                                        islets: van de Bunt QTD000554, PISA QTD000574). Uses the
                                        in-skill scripts/fetch_qtl.py plumbing when --fetch-qtl is set
                                        (defaults to the fetch_qtl.py next to this script).
  Layer 3  ENCODE SCREEN cCRE       -- does the variant fall in a candidate cis-regulatory element
                                        (promoter/enhancer)? returns element class + z-scores.

Honesty first: if a gene is NOT a significant eGene in a tissue, or a small QTL cohort is underpowered,
that is REPORTED as such -- never silently dropped and never spun as evidence. Direction of effect
(risk-allele -> up/down on the gene) is always resolved against the credible-set effect allele.

Design note on the PROX1 exemplar: PROX1 itself is not a detectable eGene in bulk pancreas / small
islet cohorts; the regulatory signal runs through the antisense PROX1-AS1. This script will show that
pattern truthfully (eGene: no for PROX1; cCRE overlap: yes) rather than forcing a cis-eQTL story.

Usage:
  python annotate_variants.py \
      --credible-set credible_set.csv \
      --out-prefix annotation \
      [--gtex ENSG00000117707,ENSG00000230461] [--gtex-tissues Pancreas] \
      [--fetch-qtl] [--qtl-datasets QTD000554,QTD000574] [--qtl-genes ENSG00000117707] \
      [--fetch-qtl-script /path/to/fetch_qtl.py]   # defaults to fetch_qtl.py beside this script \
      [--encode] [--flank 2000] \
      [--genome-build 38]

Inputs come straight from run_susie_finemap.R's credible_set.csv (cols: snp, varid, pos,
effect_allele, other_allele, beta, ... ). varid must be chr:pos:ref:alt.
"""
import argparse, json, os, subprocess, sys, time
import urllib.request, urllib.error
import pandas as pd

GTEX_BASE   = "https://gtexportal.org/api/v2"
ENCODE_CRE  = "https://screen-beta-api.wenglab.org/dataws/cre_table"
UA = {"User-Agent": "fine-mapping-susie/1.0", "Accept": "application/json"}


def log(msg): print(f"[annotate] {msg}", flush=True)


def http_get_json(url, timeout=60, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa
            last = e; time.sleep(1.5 * (k + 1))
    log(f"  GET failed ({last}) -> {url[:90]}")
    return None


def http_post_json(url, body, timeout=60, tries=3):
    data = json.dumps(body).encode()
    hdr = dict(UA); hdr["Content-Type"] = "application/json"
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdr, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa
            last = e; time.sleep(1.5 * (k + 1))
    log(f"  POST failed ({last}) -> {url[:90]}")
    return None


def parse_varid(varid):
    """chr:pos:ref:alt (or chr_pos_ref_alt). Returns (chrom_no_prefix, pos, ref, alt) or None."""
    if not isinstance(varid, str):
        return None
    sep = ":" if ":" in varid else ("_" if "_" in varid else None)
    if sep is None:
        return None
    p = varid.split(sep)
    if len(p) < 4:
        return None
    chrom = p[0].replace("chr", "")
    try:
        pos = int(p[1])
    except ValueError:
        return None
    return chrom, pos, p[2], p[3]


# ---------------------------------------------------------------- GTEx v8
def resolve_gencode(ensg):
    """Return the versioned gencodeId (ENSG........N) GTEx requires."""
    if "." in ensg:
        return ensg
    j = http_get_json(f"{GTEX_BASE}/reference/gene?geneId={ensg}")
    try:
        for rec in j.get("data", []):
            gid = rec.get("gencodeId", "")
            if gid.split(".")[0] == ensg:
                return gid
    except Exception:  # noqa
        pass
    return ensg  # fall back; API may still accept unversioned


def gtex_eqtl(gencode_versioned, variant_id_b38, tissue):
    """singleTissueEqtl filtered by gene+variant+tissue returns SIGNIFICANT eQTLs only."""
    url = (f"{GTEX_BASE}/association/singleTissueEqtl"
           f"?gencodeId={gencode_versioned}&variantId={variant_id_b38}"
           f"&tissueSiteDetailId={tissue}&datasetId=gtex_v8")
    j = http_get_json(url)
    rows = []
    if j:
        for rec in j.get("data", []):
            rows.append({
                "nes": rec.get("nes", rec.get("NES")),
                "pval": rec.get("pValue", rec.get("pval")),
                "tissue": rec.get("tissueSiteDetailId", tissue),
                "gencodeId": rec.get("gencodeId", gencode_versioned),
            })
    return rows


def run_gtex(cs, genes, tissues, build):
    if build not in ("38", "hg38", "GRCh38", 38):
        log("GTEx layer skipped: GTEx v8 is GRCh38 only and --genome-build != 38.")
        return pd.DataFrame()
    gmap = {g: resolve_gencode(g) for g in genes}
    out = []
    for _, v in cs.iterrows():
        pv = parse_varid(v.get("varid", ""))
        if not pv:
            continue
        chrom, pos, ref, alt = pv
        vid = f"chr{chrom}_{pos}_{ref}_{alt}_b38"
        ea = str(v.get("effect_allele", "")).upper()
        for g, gver in gmap.items():
            for t in tissues:
                for rec in gtex_eqtl(gver, vid, t):
                    nes = rec["nes"]
                    # GTEx NES is per-ALT allele. Re-orient to the credible-set effect allele.
                    dir_ea = None
                    if nes is not None and ea in (ref.upper(), alt.upper()):
                        signed = nes if ea == alt.upper() else -nes
                        dir_ea = "up" if signed > 0 else ("down" if signed < 0 else "none")
                    out.append({
                        "snp": v.get("snp"), "varid": v.get("varid"),
                        "gene": g, "gencodeId": rec["gencodeId"], "tissue": rec["tissue"],
                        "nes_per_alt": nes, "pval": rec["pval"],
                        "effect_allele": ea,
                        "direction_on_gene_per_effect_allele": dir_ea,
                        "is_significant_egene": True,   # endpoint returns sig only
                        "source": "GTEx_v8",
                    })
    df = pd.DataFrame(out)
    if df.empty:
        log("GTEx: no SIGNIFICANT cis-eQTL for the requested gene(s)/tissue(s) at these variants "
            "(this is a real negative -- the variant is not a detectable eGene here).")
    else:
        log(f"GTEx: {len(df)} significant eQTL record(s).")
    return df


# ---------------------------------------------------------------- eQTL Catalogue (via fetch_qtl.py)
def run_eqtl_catalogue(cs, datasets, genes, fetch_script, workdir):
    if not datasets or not genes:
        log("eQTL Catalogue layer skipped: need --qtl-datasets and --qtl-genes.")
        return pd.DataFrame()
    if not fetch_script or not os.path.exists(fetch_script):
        log(f"eQTL Catalogue layer skipped: fetch_qtl.py not found at {fetch_script}. "
            "Pass --fetch-qtl-script, or keep fetch_qtl.py alongside this script (the default).")
        return pd.DataFrame()
    cs_ids = set(cs["snp"].astype(str)) if "snp" in cs.columns else set()
    cs_pos = set(int(p) for p in cs["pos"]) if "pos" in cs.columns else set()
    # region spanning the credible set (small pad)
    if "pos" in cs.columns and len(cs):
        pv = parse_varid(cs.iloc[0].get("varid", ""))
        chrom = pv[0] if pv else None
        lo, hi = int(cs["pos"].min()) - 5000, int(cs["pos"].max()) + 5000
        region = f"{chrom}:{lo}-{hi}" if chrom else None
    else:
        region = None
    rows = []
    for ds in datasets:
        for g in genes:
            out_tsv = os.path.join(workdir, f"qtl_{ds}_{g}.tsv")
            rpt = os.path.join(workdir, f"qtl_{ds}_{g}.report.json")
            cmd = [sys.executable, fetch_script, "--source", "eqtl_catalogue",
                   "--gene-id", g, "--dataset-id", ds, "--out", out_tsv, "--report", rpt]
            if region:
                cmd += ["--region", region]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
            except Exception as e:  # noqa
                log(f"  eQTL Catalogue {ds}/{g}: fetch failed ({e}); skipping.")
                continue
            if not os.path.exists(out_tsv):
                continue
            try:
                q = pd.read_csv(out_tsv, sep="\t")
            except Exception:  # noqa
                continue
            # match by rsid if present, else by position
            idc = next((c for c in ("rsid", "snp", "variant") if c in q.columns), None)
            posc = next((c for c in ("position", "pos", "base_pair_location") if c in q.columns), None)
            betac = next((c for c in ("beta", "es", "effect") if c in q.columns), None)
            pc = next((c for c in ("pvalue", "pval", "p", "nlog10p") if c in q.columns), None)
            eac = next((c for c in ("alt", "effect_allele", "ALT") if c in q.columns), None)
            sub = q
            if idc and cs_ids:
                sub = q[q[idc].astype(str).isin(cs_ids)]
            elif posc and cs_pos:
                sub = q[q[posc].isin(cs_pos)]
            for _, r in sub.iterrows():
                rows.append({
                    "dataset": ds, "gene": g,
                    "snp": (str(r[idc]) if idc else None),
                    "pos": (int(r[posc]) if posc and pd.notna(r[posc]) else None),
                    "beta": (r[betac] if betac else None),
                    "pval": (r[pc] if pc else None),
                    "qtl_effect_allele": (r[eac] if eac else None),
                    "source": "eQTL_Catalogue",
                })
    df = pd.DataFrame(rows)
    if df.empty:
        log("eQTL Catalogue: no matching QTL rows for the credible-set variant(s) "
            "(often means underpowered small cohort or variant absent -- report as inconclusive).")
    else:
        log(f"eQTL Catalogue: {len(df)} QTL record(s) across {len(datasets)} dataset(s).")
    return df


# ---------------------------------------------------------------- ENCODE SCREEN cCRE
def run_encode(cs, flank, build):
    if build not in ("38", "hg38", "GRCh38", 38):
        log("ENCODE layer skipped: SCREEN cCREs used here are GRCh38; --genome-build != 38.")
        return pd.DataFrame()
    pvs = [parse_varid(v) for v in cs.get("varid", [])]
    pvs = [p for p in pvs if p]
    if not pvs:
        log("ENCODE layer skipped: no parseable varid in credible set.")
        return pd.DataFrame()
    chrom = pvs[0][0]
    lo = min(p[1] for p in pvs) - flank
    hi = max(p[1] for p in pvs) + flank
    body = {"assembly": "GRCh38", "coord_chrom": f"chr{chrom}",
            "coord_start": int(lo), "coord_end": int(hi),
            "gene_all_start": 0, "gene_all_end": 5000000,
            "element_type": "", "limit": 100}
    j = http_post_json(ENCODE_CRE, body)
    if not j:
        log("ENCODE: SCREEN beta API did not respond (it is frequently unstable). "
            "Skipping cCRE layer -- rerun later or use the UCSC ENCODE cCRE track manually.")
        return pd.DataFrame()
    recs = j.get("cres", j.get("data", j if isinstance(j, list) else []))
    rows = []
    cs_pos = [(p[1]) for p in pvs]
    for rec in (recs or []):
        try:
            start = int(rec.get("start", rec.get("coord_start")))
            length = int(rec.get("len", rec.get("length", 0)))
            end = start + length if length else int(rec.get("end", rec.get("coord_end", start)))
        except Exception:  # noqa
            continue
        overlaps = [sp for sp in cs_pos if start <= sp <= end]
        zs = rec.get("zScores", rec.get("ctspecific", {})) or {}
        rows.append({
            "accession": rec.get("accession", rec.get("info", {}).get("accession")),
            "chrom": f"chr{chrom}", "start": start, "end": end, "length": end - start,
            "element_class": rec.get("pct", rec.get("class", rec.get("element_type"))),
            "dnase_z": rec.get("dnase_zscore", zs.get("dnase")),
            "h3k4me3_z": rec.get("promoter_zscore", zs.get("h3k4me3")),
            "h3k27ac_z": rec.get("enhancer_zscore", zs.get("h3k27ac")),
            "ctcf_z": rec.get("ctcf_zscore", zs.get("ctcf")),
            "overlaps_credible_variant": bool(overlaps),
            "overlapping_positions": ";".join(map(str, overlaps)) if overlaps else "",
            "source": "ENCODE_SCREEN",
        })
    df = pd.DataFrame(rows)
    hit = df[df["overlaps_credible_variant"]] if not df.empty else df
    if df.empty:
        log("ENCODE: no cCREs returned for the window.")
    else:
        log(f"ENCODE: {len(df)} cCRE(s) in window; {len(hit)} directly overlap a credible-set variant.")
    return df


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Annotate fine-mapped variants (GTEx / eQTL Catalogue / ENCODE).")
    ap.add_argument("--credible-set", required=True)
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--gtex", default="", help="comma-sep ENSG ids for GTEx eQTL layer")
    ap.add_argument("--gtex-tissues", default="Pancreas", help="comma-sep GTEx tissueSiteDetailId")
    ap.add_argument("--fetch-qtl", action="store_true", help="enable eQTL Catalogue layer")
    ap.add_argument("--qtl-datasets", default="", help="comma-sep QTD ids")
    ap.add_argument("--qtl-genes", default="", help="comma-sep ENSG ids for eQTL Catalogue")
    ap.add_argument("--fetch-qtl-script", default="",
                    help="path to fetch_qtl.py (default: the fetch_qtl.py alongside this script)")
    ap.add_argument("--encode", action="store_true", help="enable ENCODE SCREEN cCRE layer")
    ap.add_argument("--flank", type=int, default=2000, help="bp flank for ENCODE window")
    ap.add_argument("--genome-build", default="38")
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()

    cs = pd.read_csv(args.credible_set)
    if cs.empty:
        log("Credible set is EMPTY -- nothing to annotate. Writing empty outputs.")
    workdir = args.workdir or os.path.dirname(os.path.abspath(args.out_prefix)) or "."
    os.makedirs(workdir, exist_ok=True)

    summary = {"n_credible_variants": int(len(cs)), "layers_run": [], "layers_skipped": []}

    # Layer 1 GTEx
    if args.gtex.strip():
        genes = [g.strip() for g in args.gtex.split(",") if g.strip()]
        tissues = [t.strip() for t in args.gtex_tissues.split(",") if t.strip()]
        g_df = run_gtex(cs, genes, tissues, args.genome_build)
        g_df.to_csv(f"{args.out_prefix}_gtex_eqtl.csv", index=False)
        summary["layers_run"].append("GTEx_v8")
        summary["gtex_n_records"] = int(len(g_df))
        summary["gtex_significant_egene"] = bool(len(g_df))
    else:
        summary["layers_skipped"].append("GTEx_v8 (no --gtex)")

    # Layer 2 eQTL Catalogue
    if args.fetch_qtl:
        ds = [d.strip() for d in args.qtl_datasets.split(",") if d.strip()]
        qg = [g.strip() for g in args.qtl_genes.split(",") if g.strip()]
        # default to the fetch_qtl.py vendored beside this script
        fetch_script = args.fetch_qtl_script or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fetch_qtl.py")
        q_df = run_eqtl_catalogue(cs, ds, qg, fetch_script, workdir)
        q_df.to_csv(f"{args.out_prefix}_eqtl_catalogue.csv", index=False)
        summary["layers_run"].append("eQTL_Catalogue")
        summary["eqtl_catalogue_n_records"] = int(len(q_df))
    else:
        summary["layers_skipped"].append("eQTL_Catalogue (no --fetch-qtl)")

    # Layer 3 ENCODE
    if args.encode:
        e_df = run_encode(cs, args.flank, args.genome_build)
        e_df.to_csv(f"{args.out_prefix}_encode_ccre.csv", index=False)
        summary["layers_run"].append("ENCODE_SCREEN")
        summary["encode_n_ccre"] = int(len(e_df))
        summary["encode_n_overlapping"] = int(e_df["overlaps_credible_variant"].sum()) if not e_df.empty else 0
    else:
        summary["layers_skipped"].append("ENCODE_SCREEN (no --encode)")

    with open(f"{args.out_prefix}_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    log("Done. Summary:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
