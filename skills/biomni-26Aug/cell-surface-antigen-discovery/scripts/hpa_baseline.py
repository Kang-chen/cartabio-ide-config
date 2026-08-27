#!/usr/bin/env python3
"""Build the normal-tissue baseline long table from the Human Protein Atlas (dual signal).

WHY THIS EXISTS
---------------
Open Targets Platform v4 removed ``Target.expressions`` (the normal-tissue baseline that
blended HPA/GTEx RNA + HPA protein levels). ``annotate_targets.py`` therefore no longer
emits ``target_baseline_expression_long.csv``. This module reconstructs that dual-signal
input directly from two canonical Human Protein Atlas bulk downloads so that the
(UNMODIFIED) ``normal_tissue_safety.compute_therapeutic_index()`` still receives a
protein + RNA baseline and can take its conservative min-safety.

DATA SOURCES (verified endpoints; both HTTP 200)
------------------------------------------------
  RNA  : https://www.proteinatlas.org/download/tsv/rna_tissue_consensus.tsv.zip  (~5.3 MB)
         -> columns: Gene, Gene name, Tissue, nTPM        (consensus nTPM, ~50 tissues)
  IHC  : https://www.proteinatlas.org/download/tsv/normal_ihc_data.tsv.zip       (~5.7 MB)
         -> columns: Gene, Gene name, Tissue, Cell type, Level, Reliability
                     (protein IHC: Not detected / Low / Medium / High, ~60 tissues)

  NOTE: the older ``normal_tissue.tsv.zip`` path now returns HTTP 404 (an HTML error page
  ~117 KB). Do NOT use it. Use ``normal_ihc_data.tsv.zip`` for protein.

  Per-gene JSON fallback (used only when a bulk file is unavailable, e.g. offline mirror):
         https://www.proteinatlas.org/ENSGXXXXXXXXXXX.json

OUTPUT (exact schema the safety module consumes)
------------------------------------------------
    target_baseline_expression_long.csv with columns:
        gene_symbol, tissue, organs, rna_value, rna_level, protein_level

  - rna_value     <- HPA consensus nTPM   (-> _rna_safety thresholds <1, <10, <50 nTPM)
  - protein_level <- HPA IHC Level string (-> _level_to_ord -> _protein_safety)
  - organs        <- tissue               (compute_therapeutic_index._is_vital matches
                                            VITAL_ORGAN_KEYWORDS on "tissue + organs";
                                            raw HPA tissue names already carry organ words)
  - rna_level     <- NaN                  (consensus RNA has no discrete level)

compute_therapeutic_index() takes the CONSERVATIVE MIN of the protein-derived and
RNA-derived safety per gene, so supplying both restores the intended dual-signal
therapeutic index. Provenance rules (no fabrication):
  - gene with RNA only    -> RNA-based safety (protein unassessed)
  - gene with IHC only    -> protein-based safety (RNA unassessed)
  - gene with neither     -> absent from baseline -> safety_score NaN (reweighted downstream)

This module ONLY changes the *source* of the baseline. Vital-organ keywords, nTPM
thresholds, IHC->ordinal mapping, and the min(protein, RNA) logic all live unchanged in
normal_tissue_safety.py.
"""
import io
import os
import time
import zipfile
import urllib.request

import numpy as np
import pandas as pd

HPA_RNA_URL = "https://www.proteinatlas.org/download/tsv/rna_tissue_consensus.tsv.zip"
HPA_IHC_URL = "https://www.proteinatlas.org/download/tsv/normal_ihc_data.tsv.zip"
HPA_JSON_TMPL = "https://www.proteinatlas.org/{ensembl_id}.json"

_UA = {"User-Agent": "Mozilla/5.0 (cell-surface-antigen-discovery/1.0)"}
IHC_ORD = {"not detected": 0, "low": 1, "medium": 2, "high": 3}
IHC_LAB = {0: "Not detected", 1: "Low", 2: "Medium", 3: "High"}


# --------------------------------------------------------------------------- #
# Download + cache
# --------------------------------------------------------------------------- #
def _download_zip_tsv(url, cache_path, timeout=180, retries=3):
    """Download a .tsv.zip from HPA to cache_path (the unzipped .tsv). Returns cache_path.

    Skips the download if a non-trivial cached .tsv already exists. Guards against the
    known HTML-error-page failure mode (a 404 returns ~117 KB of HTML, not a valid zip).
    """
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 100_000:
        print(f"    [cache] {os.path.basename(cache_path)} "
              f"({os.path.getsize(cache_path)/1e6:.1f} MB)")
        return cache_path
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            # A valid HPA download is a zip; a 404 yields a small HTML page.
            if not raw[:2] == b"PK":
                raise ValueError(
                    f"response is not a zip (got {len(raw)} bytes, "
                    f"starts {raw[:16]!r}); URL may be deprecated -> HTTP error page")
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                name = [n for n in zf.namelist() if n.endswith(".tsv")][0]
                data = zf.read(name)
            os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
            with open(cache_path, "wb") as fh:
                fh.write(data)
            print(f"    [downloaded] {url.split('/')[-1]} -> "
                  f"{os.path.basename(cache_path)} ({len(data)/1e6:.1f} MB)")
            return cache_path
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to download {url}: {last}")


def _resolve_source(url, explicit_path, cache_dir, cache_name):
    """Return a local .tsv path: prefer an explicitly supplied file, else download+cache."""
    if explicit_path and os.path.exists(explicit_path):
        print(f"    [supplied] {os.path.basename(explicit_path)}")
        return explicit_path
    return _download_zip_tsv(url, os.path.join(cache_dir, cache_name))


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #
def _load_rna(hpa_rna_tsv):
    rna = pd.read_csv(hpa_rna_tsv, sep="\t")
    rna.columns = [c.strip() for c in rna.columns]  # Gene, Gene name, Tissue, nTPM
    return pd.DataFrame({
        "gene_symbol": rna["Gene name"].astype(str),
        "tissue": rna["Tissue"].astype(str),
        "organs": rna["Tissue"].astype(str),
        "rna_value": pd.to_numeric(rna["nTPM"], errors="coerce"),
        "rna_level": np.nan,
        "protein_level": np.nan,
    })


def _load_protein(hpa_ihc_tsv):
    """Collapse cell-type-level IHC rows to the MAX protein level per gene x tissue.

    HPA IHC has one row per (gene, tissue, cell type). For a tissue-level safety call we
    take the highest expression seen in ANY cell type of that tissue (conservative for
    on-target/off-tumour toxicity: one positive vital-tissue cell type is a liability).
    """
    ihc = pd.read_csv(hpa_ihc_tsv, sep="\t")
    ihc.columns = [c.strip() for c in ihc.columns]
    ihc["_ord"] = ihc["Level"].astype(str).str.strip().str.lower().map(IHC_ORD)
    ihc = ihc.dropna(subset=["_ord"])  # drop N/A, Ascending/Descending, Not representative
    grp = ihc.groupby(["Gene name", "Tissue"], as_index=False)["_ord"].max()
    return pd.DataFrame({
        "gene_symbol": grp["Gene name"].astype(str),
        "tissue": grp["Tissue"].astype(str),
        "organs": grp["Tissue"].astype(str),
        "rna_value": np.nan,
        "rna_level": np.nan,
        "protein_level": grp["_ord"].map(lambda o: IHC_LAB.get(int(o))),
    })


# --------------------------------------------------------------------------- #
# Per-gene JSON fallback (only when a bulk file cannot be obtained)
# --------------------------------------------------------------------------- #
def _fallback_json(ensembl_ids, timeout=30, throttle=0.4):
    """Fetch RNA consensus nTPM per tissue from the per-gene HPA JSON API.

    ``ensembl_ids`` maps gene_symbol -> Ensembl gene id. Returns a long DataFrame in the
    output schema (RNA only). Used solely when the bulk RNA file is unavailable.
    """
    import json
    rows = []
    for sym, ens in ensembl_ids.items():
        if not isinstance(ens, str) or not ens.startswith("ENSG"):
            continue
        try:
            req = urllib.request.Request(HPA_JSON_TMPL.format(ensembl_id=ens), headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read())
        except Exception:  # noqa: BLE001
            time.sleep(throttle)
            continue
        # HPA JSON exposes consensus tissue nTPM under keys like
        # "RNA consensus tissue gene data" -> {tissue: nTPM}.
        block = None
        for k in j:
            if "consensus" in k.lower() and "tissue" in k.lower():
                block = j[k]
                break
        if isinstance(block, dict):
            for tissue, val in block.items():
                rows.append({
                    "gene_symbol": sym, "tissue": str(tissue), "organs": str(tissue),
                    "rna_value": pd.to_numeric(val, errors="coerce"),
                    "rna_level": np.nan, "protein_level": np.nan,
                })
        time.sleep(throttle)
    return pd.DataFrame(rows) if rows else None


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def build_hpa_baseline_long(genes, output_dir="results", hpa_rna_tsv=None,
                            hpa_ihc_tsv=None, cache_dir=None, ensembl_ids=None):
    """Build target_baseline_expression_long.csv for ``genes`` from HPA.

    Parameters
    ----------
    genes : iterable of gene symbols to restrict the baseline to (your candidate universe).
    output_dir : where target_baseline_expression_long.csv is written.
    hpa_rna_tsv, hpa_ihc_tsv : optional pre-downloaded .tsv paths; if omitted, the two
        canonical HPA bulk files are downloaded and cached.
    cache_dir : where to cache the bulk .tsv files (default: <output_dir>/hpa_cache).
    ensembl_ids : optional {gene_symbol: ENSG...} map enabling the per-gene JSON fallback
        for RNA if the bulk RNA download fails.

    Returns the long DataFrame (also written to disk).
    """
    os.makedirs(output_dir, exist_ok=True)
    cache_dir = cache_dir or os.path.join(output_dir, "hpa_cache")
    want = set(dict.fromkeys(str(g) for g in genes))

    # ---- RNA (required) ----
    try:
        rna_path = _resolve_source(HPA_RNA_URL, hpa_rna_tsv, cache_dir,
                                   "rna_tissue_consensus.tsv")
        rna_long = _load_rna(rna_path)
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] bulk RNA unavailable ({e}); trying per-gene JSON fallback")
        rna_long = _fallback_json(ensembl_ids or {}) if ensembl_ids else None
        if rna_long is None:
            raise RuntimeError(
                "Could not obtain HPA RNA baseline (bulk failed and no ensembl_ids for "
                "JSON fallback). Supply hpa_rna_tsv= or ensembl_ids=.")
    rna_long = rna_long[rna_long["gene_symbol"].isin(want)]
    parts = [rna_long]

    # ---- Protein IHC (optional but recommended for dual-signal safety) ----
    prot_long = None
    try:
        ihc_path = _resolve_source(HPA_IHC_URL, hpa_ihc_tsv, cache_dir,
                                   "normal_ihc_data.tsv")
        prot_long = _load_protein(ihc_path)
        prot_long = prot_long[prot_long["gene_symbol"].isin(want)]
        parts.append(prot_long)
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] IHC protein baseline unavailable ({e}); RNA-only safety")

    out = pd.concat(parts, ignore_index=True)[
        ["gene_symbol", "tissue", "organs", "rna_value", "rna_level", "protein_level"]]
    path = os.path.join(output_dir, "target_baseline_expression_long.csv")
    out.to_csv(path, index=False)

    n_rna = rna_long["gene_symbol"].nunique()
    n_prot = prot_long["gene_symbol"].nunique() if prot_long is not None else 0
    missing = sorted(want - set(out["gene_symbol"]))
    print(f"\u2713 HPA dual baseline: {len(out)} rows -> {path}")
    print(f"    RNA (consensus nTPM): {n_rna}/{len(want)} genes, "
          f"{rna_long['tissue'].nunique()} tissues")
    if prot_long is not None:
        print(f"    Protein (IHC level):  {n_prot}/{len(want)} genes, "
              f"{prot_long['tissue'].nunique()} tissues")
    else:
        print("    Protein (IHC level):  not available -> RNA-only safety")
    if missing:
        print(f"  {len(missing)} gene(s) absent from HPA (safety_score -> NaN): "
              f"{', '.join(missing[:12])}{' ...' if len(missing) > 12 else ''}")
    return out


if __name__ == "__main__":
    # Tiny live smoke test (downloads + caches the two bulk files on first run).
    build_hpa_baseline_long(
        ["TACSTD2", "ERBB2", "ATP1A1", "EGFR", "FOLR1"],
        output_dir="/tmp/hpa_demo",
    )
