"""
============================================================================
LOAD UNITS  —  cell-therapy scRNA-seq QC release scorecard
============================================================================

Load each "unit" (lot / batch / sample) into an AnnData object, from either
local files (10x dir / .h5 / .h5ad) or a GEO accession. Standardizes gene
symbols to var_names and records raw cell counts.

Functions
  - load_units(cfg)             : dispatch to local or GEO loading -> dict[name]=AnnData
  - load_local_units(paths)     : load a list of local files/dirs
  - load_geo_units(accession)   : download a GEO series and load each sample matrix

GEO note
  The reliable per-file download route is the acc-based URL:
    https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSMxxxx&format=file&file=<url-encoded>
  (the FTP route is flaky). See references/marker_panel_sources.md.

Usage
  from load_units import load_units
  units = load_units(cfg)     # {unit_name: AnnData(raw counts)}
"""

import os
import re
import glob
import gzip
import shutil
import urllib.parse
import urllib.request
from typing import Dict, List

import numpy as np
import scanpy as sc
import anndata as ad


def _standardize(a) -> "ad.AnnData":
    """Make var_names unique gene symbols, ensure obs_names unique, drop empty."""
    a.var_names_make_unique()
    a.obs_names_make_unique()
    # ensure counts are in .X and integer-ish; keep a raw copy of counts layer
    if "counts" not in a.layers:
        a.layers["counts"] = a.X.copy()
    a.uns["n_cells_raw"] = int(a.n_obs)
    return a


def load_local_units(paths: List[str]) -> Dict[str, "ad.AnnData"]:
    """Load local units. Each path may be a 10x dir, a .h5, or a .h5ad."""
    units = {}
    for p in paths:
        name = re.sub(r"\.(h5ad|h5)$", "", os.path.basename(p.rstrip("/")))
        if os.path.isdir(p):
            a = sc.read_10x_mtx(p, var_names="gene_symbols", cache=False)
        elif p.endswith(".h5ad"):
            a = sc.read_h5ad(p)
        elif p.endswith(".h5"):
            a = sc.read_10x_h5(p)
        else:
            raise ValueError(f"Unsupported input: {p} (expect 10x dir, .h5, or .h5ad)")
        a = _standardize(a)
        a.obs["unit"] = name
        units[name] = a
        print(f"  ✓ loaded unit '{name}': {a.n_obs} cells × {a.n_vars} genes  ({p})")
    return units


# ---------------------------------------------------------------------------
# GEO loading
# ---------------------------------------------------------------------------
def _geo_supp_file_list(accession: str) -> List[str]:
    """List supplementary file names for a GSE via GEOparse if available."""
    try:
        import GEOparse
    except Exception as e:
        raise RuntimeError(
            "GEOparse is required to enumerate GEO supplementary files. "
            "Install with `uv pip install GEOparse`."
        ) from e
    workdir = f"/workspace/_geo_{accession}"
    os.makedirs(workdir, exist_ok=True)
    gse = GEOparse.get_GEO(geo=accession, destdir=workdir, silent=True, how="brief")
    files = {}  # gsm -> list of (filename)
    for gsm_name, gsm in gse.gsms.items():
        supp = gsm.metadata.get("supplementary_file_1", []) + \
               gsm.metadata.get("supplementary_file", [])
        files[gsm_name] = [os.path.basename(urllib.parse.unquote(u)) for u in supp]
    return gse, files, workdir


def _download_geo_file(gsm: str, filename: str, dest_dir: str) -> str:
    """Download one supplementary file via the reliable acc-based URL."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    url = ("https://www.ncbi.nlm.nih.gov/geo/download/?acc=%s&format=file&file=%s"
           % (gsm, urllib.parse.quote(filename)))
    print(f"    downloading {gsm}:{filename}")
    urllib.request.urlretrieve(url, dest)
    return dest


def load_geo_units(accession: str) -> Dict[str, "ad.AnnData"]:
    """Download a GEO series and load each sample as a unit.

    Handles the two common supplementary layouts:
      (a) a per-sample .h5 (CellRanger) file
      (b) a per-sample matrix.mtx(.gz) + barcodes + features trio
    For anything more exotic, download manually and use load_local_units().
    """
    gse, files, workdir = _geo_supp_file_list(accession)
    units = {}
    for gsm, flist in files.items():
        title = gse.gsms[gsm].metadata.get("title", [gsm])[0]
        safe = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_") or gsm
        dest_dir = os.path.join(workdir, gsm)
        h5 = [f for f in flist if f.endswith(".h5")]
        mtx = [f for f in flist if "matrix" in f.lower() and f.endswith((".mtx", ".mtx.gz"))]
        try:
            if h5:
                fp = _download_geo_file(gsm, h5[0], dest_dir)
                a = sc.read_10x_h5(fp)
            elif mtx:
                # download the trio into a 10x-style folder
                for f in flist:
                    _download_geo_file(gsm, f, dest_dir)
                a = _read_mtx_folder(dest_dir)
            else:
                print(f"    ⚠ {gsm} ({title}): no h5/mtx supp file recognized; skipping")
                continue
        except Exception as e:
            print(f"    ⚠ failed to load {gsm} ({title}): {e}")
            continue
        a = _standardize(a)
        a.obs["unit"] = safe
        a.obs["gsm"] = gsm
        units[safe] = a
        print(f"  ✓ loaded unit '{safe}' [{gsm}]: {a.n_obs} cells × {a.n_vars} genes")
    if not units:
        raise RuntimeError(
            f"No units loaded from {accession}. Inspect supplementary files in {workdir} "
            "and load manually with load_local_units()."
        )
    return units


def _read_mtx_folder(folder: str) -> "ad.AnnData":
    """Read a folder holding matrix.mtx(.gz)+barcodes+features with GEO prefixes."""
    # normalize filenames to the trio scanpy expects
    def _find(patterns):
        for pat in patterns:
            hits = glob.glob(os.path.join(folder, pat))
            if hits:
                return hits[0]
        return None
    mtx = _find(["*matrix.mtx.gz", "*matrix.mtx"])
    bcs = _find(["*barcodes.tsv.gz", "*barcodes.tsv"])
    fts = _find(["*features.tsv.gz", "*features.tsv", "*genes.tsv.gz", "*genes.tsv"])
    if not (mtx and bcs and fts):
        raise FileNotFoundError(f"Incomplete 10x trio in {folder}")
    std = os.path.join(folder, "_std")
    os.makedirs(std, exist_ok=True)
    for src, dst in [(mtx, "matrix.mtx.gz"), (bcs, "barcodes.tsv.gz"), (fts, "features.tsv.gz")]:
        out = os.path.join(std, dst)
        if src.endswith(".gz"):
            shutil.copy(src, out)
        else:  # gzip it
            with open(src, "rb") as fi, gzip.open(out, "wb") as fo:
                shutil.copyfileobj(fi, fo)
    return sc.read_10x_mtx(std, var_names="gene_symbols", cache=False)


def load_units(cfg: Dict) -> Dict[str, "ad.AnnData"]:
    """Top-level dispatch based on cfg['is_geo']."""
    if cfg.get("is_geo"):
        acc = cfg["inputs"][0]
        print(f"Loading units from GEO accession {acc} ...")
        units = load_geo_units(acc)
    else:
        print(f"Loading {len(cfg['inputs'])} local unit(s) ...")
        units = load_local_units(cfg["inputs"])
    print(f"✓ loaded {len(units)} unit(s): {list(units)}")
    return units


if __name__ == "__main__":
    print("load_units.py — import and call load_units(cfg). No standalone test (requires data).")
