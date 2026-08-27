#!/usr/bin/env python3
"""fetch_gwas_catalog.py -- fetch OPEN harmonised GWAS summary statistics from the GWAS Catalog by
accession, restricted to a locus, with an honest gated-source fallback.

Core behavior (the honesty gate):
  * Resolve the study record via the GWAS Catalog REST API.
  * If `fullPvalueSet` is True  -> the study has downloadable harmonised (GRCh38) summary statistics.
    Construct the FTP harmonised path, download, subset to --region, record md5 + provenance.
  * If `fullPvalueSet` is False -> the study is metadata-only / access-gated (e.g. DIAMANTE).
    DO NOT fabricate or substitute a file. Print the data-access route and exit non-zero so the
    caller must either upload the file or approve an OPEN alternative accession.

This script only performs discovery + download from EBI (a trusted scientific source). It never
invents data.

Usage:
  python fetch_gwas_catalog.py --accession GCST006867 --region 1:213400000-214600000 \
      --out gwas_region.tsv --report gwas_fetch.json

  # discover accessions for a trait first:
  python fetch_gwas_catalog.py --find-trait "type 2 diabetes"
"""
import argparse, gzip, hashlib, io, json, os, re, sys, urllib.parse

import requests

REST = "https://www.ebi.ac.uk/gwas/rest/api"
FTP_BASE = "https://ftp.ebi.ac.uk/pub/databases/gwas/summary_statistics"
UA = {"User-Agent": "phylo-fine-mapping-susie/1.0 (+https://ftp.ebi.ac.uk)"}


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 120), **kw)
    r.raise_for_status()
    return r


def range_dir(accession: str) -> str:
    """GWAS Catalog buckets harmonised files into GCST(lo)-GCST(hi) dirs of 1000 studies.
    e.g. GCST006867 -> GCST006001-GCST007000 ; GCST90132184 -> GCST90132001-GCST90133000."""
    m = re.match(r"GCST(\d+)", accession)
    if not m:
        raise ValueError(f"bad accession: {accession}")
    n = int(m.group(1))
    width = len(m.group(1))
    lo = ((n - 1) // 1000) * 1000 + 1
    hi = lo + 999
    return f"GCST{lo:0{width}d}-GCST{hi:0{width}d}"


def find_trait(trait: str):
    """List candidate studies for a free-text/EFO trait so the user can choose an accession."""
    url = f"{REST}/efoTraits/search/findByEfoTrait?trait={urllib.parse.quote(trait)}"
    try:
        d = _get(url).json()
    except Exception as e:
        print(f"trait search failed: {e}", file=sys.stderr)
        return
    traits = d.get("_embedded", {}).get("efoTraits", [])
    if not traits:
        print(f"No EFO trait matched '{trait}'. Try the EFO label or an accession directly.")
        return
    print(f"EFO traits matching '{trait}':")
    for t in traits[:10]:
        print(f"  - {t.get('trait')}  (EFO: {t.get('shortForm')})")
    print("\nBrowse studies + accessions at: "
          "https://www.ebi.ac.uk/gwas/  (filter by 'Full summary statistics available').")


def parse_ancestry(study: dict):
    """Best-effort ancestry summary from the study record. Returns (superpop_guess, detail)."""
    detail = study.get("initialSampleSize") or ""
    anc_list = []
    for a in study.get("ancestries", []) or []:
        for grp in a.get("ancestralGroups", []) or []:
            g = grp.get("ancestralGroup")
            if g:
                anc_list.append(g)
    text = (detail + " " + " ".join(anc_list)).lower()
    # crude mapping to 1000G superpopulations; the caller/user must confirm.
    mapping = [
        ("EUR", ["european", "white", "ceu", "finnish"]),
        ("EAS", ["east asian", "chinese", "japanese", "korean", "han"]),
        ("AFR", ["african", "african american", "yoruba", "afro"]),
        ("SAS", ["south asian", "indian", "pakistani", "bangladeshi", "punjabi"]),
        ("AMR", ["hispanic", "latino", "admixed american", "amerindian"]),
    ]
    hits = [sp for sp, kws in mapping if any(k in text for k in kws)]
    guess = hits[0] if len(hits) == 1 else (";".join(hits) if hits else None)
    return guess, detail.strip(), sorted(set(anc_list))


def list_harmonised_files(accession: str):
    """Return candidate .h.tsv.gz filenames under the study's harmonised/ dir (directory listing)."""
    rdir = range_dir(accession)
    hdir = f"{FTP_BASE}/{rdir}/{accession}/harmonised/"
    try:
        html = _get(hdir).text
    except Exception as e:
        return hdir, [], f"could not list {hdir}: {e}"
    files = re.findall(r'href="([^"]+\.h\.tsv\.gz)"', html)
    # de-dup + ignore the .tbi
    files = sorted({f.split("/")[-1] for f in files if f.endswith(".h.tsv.gz")})
    return hdir, files, None


def subset_region(url: str, region: str, out_path: str):
    """Stream the (gzipped) harmonised TSV and keep only rows within chr:start-end (GRCh38).
    Harmonised files use hm_chrom / hm_pos columns. md5 is computed on the downloaded bytes."""
    chrom, rng = region.split(":")
    chrom = chrom.replace("chr", "")
    start, end = [int(x) for x in rng.split("-")]

    md5 = hashlib.md5()
    with requests.get(url, headers=UA, stream=True, timeout=600) as r:
        r.raise_for_status()
        raw = io.BytesIO()
        for chunk in r.iter_content(chunk_size=1 << 20):
            md5.update(chunk)
            raw.update(chunk) if False else raw.write(chunk)
    raw.seek(0)

    n_in = n_out = 0
    ci = pi = None
    with gzip.open(raw, "rt") as fh, open(out_path, "w") as out:
        header = fh.readline().rstrip("\n")
        cols = header.split("\t")
        # locate chrom/pos columns (prefer harmonised hm_* columns)
        def find(cands):
            for c in cands:
                if c in cols:
                    return cols.index(c)
            return None
        ci = find(["hm_chrom", "chromosome", "chr", "chrom"])
        pi = find(["hm_pos", "base_pair_location", "position", "pos", "bp"])
        if ci is None or pi is None:
            sys.exit(f"ERROR: could not find chrom/pos columns in {cols[:12]}...")
        out.write(header + "\n")
        for line in fh:
            n_in += 1
            f = line.rstrip("\n").split("\t")
            try:
                c = f[ci].replace("chr", "")
                p = int(float(f[pi]))
            except (ValueError, IndexError):
                continue
            if c == chrom and start <= p <= end:
                out.write(line)
                n_out += 1
    return md5.hexdigest(), n_in, n_out, cols


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accession", help="GWAS Catalog study accession, e.g. GCST006867")
    ap.add_argument("--region", help="GRCh38 window chr:start-end (no chr prefix needed)")
    ap.add_argument("--out", help="output subset TSV")
    ap.add_argument("--report", help="output provenance JSON")
    ap.add_argument("--find-trait", help="discover accessions for a free-text/EFO trait, then exit")
    ap.add_argument("--file", help="explicit harmonised filename to use (else auto-pick the .h.tsv.gz)")
    args = ap.parse_args()

    if args.find_trait:
        find_trait(args.find_trait)
        return

    if not args.accession:
        sys.exit("ERROR: --accession is required (or use --find-trait).")

    # 1) study record
    try:
        study = _get(f"{REST}/studies/{args.accession}").json()
    except Exception as e:
        sys.exit(f"ERROR: could not fetch study {args.accession}: {e}")

    full = bool(study.get("fullPvalueSet"))
    sp_guess, anc_detail, anc_groups = parse_ancestry(study)
    trait = study.get("diseaseTrait", {}).get("trait")

    report = {
        "accession": args.accession,
        "diseaseTrait": trait,
        "fullPvalueSet": full,
        "ancestry_superpop_guess": sp_guess,
        "ancestry_detail": anc_detail,
        "ancestral_groups": anc_groups,
        "region": args.region,
    }

    # 2) THE HONESTY GATE
    if not full:
        report["status"] = "GATED_OR_METADATA_ONLY"
        access = (f"https://www.ebi.ac.uk/gwas/studies/{args.accession}")
        msg = (
            f"\n================ GATED / METADATA-ONLY SOURCE ================\n"
            f"Study {args.accession} ({trait}) has fullPvalueSet = FALSE.\n"
            f"The GWAS Catalog holds only the study RECORD, not downloadable summary statistics.\n"
            f"This is common for access-gated meta-analyses (e.g. DIAMANTE, behind a data-use\n"
            f"agreement). This script will NOT fabricate or silently substitute a dataset.\n\n"
            f"To proceed, either:\n"
            f"  (a) obtain the summary statistics from the study's data-access route and pass the\n"
            f"      file directly to ingest_sumstats.py, or\n"
            f"  (b) choose an OPEN alternative accession (fullPvalueSet = True) for the same trait\n"
            f"      -- discover with:  python fetch_gwas_catalog.py --find-trait \"{trait}\"\n\n"
            f"Study page: {access}\n"
            f"=============================================================\n"
        )
        print(msg, file=sys.stderr)
        if args.report:
            json.dump(report, open(args.report, "w"), indent=2)
        sys.exit(3)  # distinct non-zero code for "gated source"

    # 3) open study -> need region + out to actually download
    if not (args.region and args.out and args.report):
        sys.exit("ERROR: --region, --out, and --report are required to download an open study.")

    hdir, files, err = list_harmonised_files(args.accession)
    report["harmonised_dir"] = hdir
    if err or not files:
        sys.exit(f"ERROR: no harmonised .h.tsv.gz found for {args.accession}. {err or ''}\n"
                 f"Checked: {hdir}")
    fname = args.file if args.file else files[0]
    if len(files) > 1 and not args.file:
        print(f"NOTE: multiple harmonised files; using {fname}. Others: {files}", file=sys.stderr)
    url = hdir + fname
    report["harmonised_file"] = fname
    report["harmonised_url"] = url

    print(f"[fetch] downloading + subsetting {fname} to {args.region} ...", file=sys.stderr)
    md5, n_in, n_out, cols = subset_region(url, args.region, args.out)
    report.update({"md5": md5, "n_rows_scanned": n_in, "n_rows_in_region": n_out,
                   "columns": cols, "status": "OK"})
    json.dump(report, open(args.report, "w"), indent=2)

    if n_out == 0:
        print(f"WARNING: 0 variants in {args.region}. Check the region/build.", file=sys.stderr)
    print(f"\u2713 GWAS Catalog fetch complete | accession={args.accession} "
          f"fullPvalueSet=True ancestry~{sp_guess} md5={md5[:12]} "
          f"variants_in_region={n_out}")
    print(f"  effect-allele + column mapping is resolved next by ingest_sumstats.py.")


if __name__ == "__main__":
    main()
