#!/usr/bin/env python3
"""
tissue_expression.py  —  Reusable engine for the `tissue-expression-specificity` skill.

Profiles WHERE a human protein-coding gene is expressed across human tissues using
two independent atlases (GTEx bulk RNA-seq + Human Protein Atlas consensus tissue),
computes a tau (tau) tissue-specificity score, flags high-baseline tissues, tests
cross-atlas concordance, and builds an on-target safety organ matrix.

Generalized (gene-parameterized) from a validated GCGR run. NOTHING here is
GCGR-specific — pass any human gene symbol / Ensembl gene ID / UniProt accession.

Design notes
------------
* GTEx source = DATALAKE-FIRST, API-FALLBACK. Try a mounted curated GTEx median-TPM
  file (v11); if not present, fall back to the GTEx Portal v8 API. Records which was used.
* HPA source = stream-parse the single target <entry> from proteinatlas.xml.gz
  (constant memory; the 730 MB gzip is NOT loaded into RAM).
* Units: HPA nTPM and GTEx TPM are NOT unit-identical — concordance is assessed on
  ranks (Spearman, primary) and log2 (Pearson), i.e. patterns not magnitudes.
* tau is computed on log2(x+1) per-atlas at native resolution (no collapsing).

All functions return plain pandas / dicts so the calling agent can assemble a report.
"""

from __future__ import annotations
import os, re, gzip, glob, html, json, time
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants (recovered verbatim from the validated run; documented in references/)
# ---------------------------------------------------------------------------
HPA_XML   = "/mnt/datalake/human_protein_atlas/proteinatlas.xml.gz"
HPA_TSV   = "/mnt/datalake/human_protein_atlas/proteinatlas_subset.tsv"
# GTEx datalake glob(s) — checked in order; environments vary, so we glob broadly.
# The curated v11 file lives under GTEx/bulk_tissue_expression/ and may be .gct.gz,
# .gct (uncompressed), or .parquet. The extension-agnostic patterns ensure the
# datalake-first path matches the actual v11 filename regardless of compression.
GTEX_DATALAKE_GLOBS = [
    "/mnt/datalake/GTEx/bulk_tissue_expression/*gene_median_tpm*",
    "/mnt/datalake/GTEx/**/*gene_median_tpm*",
    "/mnt/datalake/GTEx/**/*median_tpm*.gct.gz",
    "/mnt/datalake/GTEx/**/*median_tpm*.gct",
    "/mnt/datalake/GTEx/**/*median_tpm*.parquet",
]
GTEX_API = "https://gtexportal.org/api/v2/expression/geneExpression"

# Dual-threshold high-baseline flagging (absolute OR relative)
ABS_MODERATE = 10.0   # "clearly expressed" floor
ABS_HIGH     = 25.0   # strongly-expressing tier
REL_PCTL     = 90     # top decile

# Vital / high-consequence organ core — ALWAYS shown in the safety matrix
# regardless of the target's expression (a low-but-nonzero vital organ still matters).
VITAL_CORE = ["Heart muscle", "Brain", "Liver", "Kidney", "Lung"]

# GTEx fine-grained tissueSiteDetailId -> harmonized organ name (matches HPA labels).
# "__drop__" = no clean HPA consensus-tissue counterpart (cell lines, whole blood, nerve).
GTEX_TO_ORGAN = {
    "Adipose_Subcutaneous": "Adipose tissue", "Adipose_Visceral_Omentum": "Adipose tissue",
    "Adrenal_Gland": "Adrenal gland",
    "Artery_Aorta": "Artery", "Artery_Coronary": "Artery", "Artery_Tibial": "Artery",
    "Bladder": "Urinary bladder",
    "Brain_Amygdala": "Brain", "Brain_Anterior_cingulate_cortex_BA24": "Brain",
    "Brain_Caudate_basal_ganglia": "Brain", "Brain_Cerebellar_Hemisphere": "Brain",
    "Brain_Cerebellum": "Brain", "Brain_Cortex": "Brain", "Brain_Frontal_Cortex_BA9": "Brain",
    "Brain_Hippocampus": "Brain", "Brain_Hypothalamus": "Brain",
    "Brain_Nucleus_accumbens_basal_ganglia": "Brain", "Brain_Putamen_basal_ganglia": "Brain",
    "Brain_Spinal_cord_cervical_c-1": "Brain", "Brain_Substantia_nigra": "Brain",
    "Breast_Mammary_Tissue": "Breast",
    "Cells_Cultured_fibroblasts": "__drop__", "Cells_EBV-transformed_lymphocytes": "__drop__",
    "Cervix_Ectocervix": "Cervix, uterine", "Cervix_Endocervix": "Cervix, uterine",
    "Colon_Sigmoid": "Colon", "Colon_Transverse": "Colon",
    "Esophagus_Gastroesophageal_Junction": "Esophagus", "Esophagus_Mucosa": "Esophagus",
    "Esophagus_Muscularis": "Esophagus",
    "Fallopian_Tube": "Fallopian tube",
    "Heart_Atrial_Appendage": "Heart muscle", "Heart_Left_Ventricle": "Heart muscle",
    "Kidney_Cortex": "Kidney", "Kidney_Medulla": "Kidney",
    "Liver": "Liver", "Lung": "Lung",
    "Minor_Salivary_Gland": "Salivary gland", "Minor_Salivary_gland": "Salivary gland",
    "Muscle_Skeletal": "Skeletal muscle",
    "Nerve_Tibial": "__drop__",
    "Ovary": "Ovary", "Pancreas": "Pancreas", "Pituitary": "Pituitary gland",
    "Prostate": "Prostate",
    "Skin_Not_Sun_Exposed_Suprapubic": "Skin", "Skin_Sun_Exposed_Lower_leg": "Skin",
    "Small_Intestine_Terminal_Ileum": "Small intestine",
    "Spleen": "Spleen", "Stomach": "Stomach", "Testis": "Testis",
    "Thyroid": "Thyroid gland", "Uterus": "Endometrium", "Vagina": "Vagina",
    "Whole_Blood": "__drop__",
}

# HPA tissue label -> organ (collapse brain subregions / numbered duplicates).
HPA_NAME_MAP = {
    "Heart muscle": "Heart muscle", "Skin 1": "Skin", "Skin 2": "Skin",
    "Endometrium 1": "Endometrium", "Endometrium 2": "Endometrium",
    "Stomach 1": "Stomach", "Stomach 2": "Stomach",
    "Colon": "Colon", "Small intestine": "Small intestine", "Duodenum": "Small intestine",
    "Cerebral cortex": "Brain", "Cerebellum": "Brain", "Hippocampus": "Brain",
    "Amygdala": "Brain", "Basal ganglia": "Brain", "Hypothalamus": "Brain",
    "Midbrain": "Brain", "Pons and medulla": "Brain", "Thalamus": "Brain",
    "Spinal cord": "Brain", "White matter": "Brain", "Corpus callosum": "Brain",
}

# Organ -> physiological system (for safety interpretation). Vital organs flagged.
SYSTEM_MAP = {
    "Liver": "Metabolic/hepatic", "Pancreas": "Metabolic/endocrine",
    "Kidney": "Excretory/renal", "Stomach": "GI", "Small intestine": "GI", "Colon": "GI",
    "Heart muscle": "Cardiovascular (vital)", "Adipose tissue": "Metabolic",
    "Brain": "CNS (vital)", "Adrenal gland": "Endocrine",
    "Skeletal muscle": "Musculoskeletal", "Lung": "Respiratory (vital)",
    "Thyroid gland": "Endocrine", "Pituitary gland": "Endocrine",
    "Spleen": "Immune", "Testis": "Reproductive", "Ovary": "Reproductive",
    "Prostate": "Reproductive", "Breast": "Reproductive", "Endometrium": "Reproductive",
    "Salivary gland": "GI", "Esophagus": "GI", "Urinary bladder": "Excretory/renal",
    "Artery": "Cardiovascular (vital)", "Skin": "Integumentary",
}


# ===========================================================================
# 0b. SAFE STRING CONVERSION  (ReportLab Paragraph / CSV NaN crash guard)
# ===========================================================================
def safe_cell(value) -> str:
    """Coerce a nullable DataFrame value to a safe string for ReportLab Paragraph()
    or CSV output. NaN/None/inf become 'n/a' instead of crashing Paragraph() with
    a float-NaN-to-string error. Repeated fragile conversions funnel through here."""
    if value is None:
        return "n/a"
    try:
        f = float(value)
        if np.isnan(f) or np.isinf(f):
            return "n/a"
    except (TypeError, ValueError):
        pass
    return str(value)


# ===========================================================================
# 1. GENE RESOLUTION  (symbol / Ensembl / UniProt  ->  Ensembl gene id)
# ===========================================================================
def resolve_gene(query: str, timeout: int = 30) -> dict:
    """Resolve a gene identifier to a canonical record.

    Accepts: HGNC symbol (e.g. 'GCGR'), Ensembl gene id ('ENSG00000215644'
    with or without version), or UniProt accession ('P47871').

    Resolution order (all self-contained / robust):
      1. If it already looks like an Ensembl gene id -> use directly, enrich from HPA TSV.
      2. Look up in the HPA subset TSV (has Gene, Ensembl, Uniprot columns) — HPA is a
         required data source here anyway, so this needs no external API.
      3. Fall back to the `gget` package (Ensembl) if installed and not yet resolved.

    Returns dict: {query, symbol, ensembl, ensembl_versioned(None until GTEx step),
                   uniprot, source}.  Raises ValueError if unresolvable/ambiguous.
    """
    q = str(query).strip()
    rec = {"query": q, "symbol": None, "ensembl": None, "uniprot": None, "source": None}

    # --- 1. already an Ensembl gene id? ---
    m = re.fullmatch(r"(ENSG\d{11})(\.\d+)?", q, re.I)
    if m:
        rec["ensembl"] = m.group(1).upper()
        rec["source"] = "ensembl_id (direct)"

    # --- 2. HPA subset TSV lookup (symbol / uniprot / ensembl) ---
    if os.path.exists(HPA_TSV):
        try:
            tsv = pd.read_csv(HPA_TSV, sep="\t", low_memory=False)
            cols = {c.lower(): c for c in tsv.columns}
            gcol = cols.get("gene"); ecol = cols.get("ensembl"); ucol = cols.get("uniprot")
            hit = None
            if rec["ensembl"] and ecol:
                sub = tsv[tsv[ecol].astype(str).str.upper() == rec["ensembl"]]
                if len(sub): hit = sub.iloc[0]
            if hit is None and gcol:
                sub = tsv[tsv[gcol].astype(str).str.upper() == q.upper()]
                if len(sub): hit = sub.iloc[0]; rec["source"] = "HPA TSV (symbol)"
            if hit is None and ucol:
                # Uniprot column can be comma/semicolon separated
                mask = tsv[ucol].astype(str).str.upper().str.split(r"[,; ]").apply(
                    lambda xs: q.upper() in [x.strip() for x in xs])
                sub = tsv[mask]
                if len(sub): hit = sub.iloc[0]; rec["source"] = "HPA TSV (uniprot)"
            if hit is not None:
                if gcol: rec["symbol"] = str(hit[gcol])
                if ecol and not rec["ensembl"]: rec["ensembl"] = str(hit[ecol]).upper()
                if ucol:
                    up = str(hit[ucol]).replace(";", ",").split(",")[0].strip()
                    rec["uniprot"] = up or None
                if rec["source"] is None: rec["source"] = "HPA TSV"
        except Exception as e:
            print(f"[resolve_gene] HPA TSV lookup skipped: {e}")

    # --- 3. gget fallback (Ensembl) ---
    if not rec["ensembl"]:
        try:
            import gget
            info = gget.search(q, species="homo_sapiens", limit=1)
            if info is not None and len(info):
                rec["ensembl"] = str(info.iloc[0]["ensembl_id"]).upper()
                rec["symbol"] = rec["symbol"] or str(info.iloc[0].get("gene_name", q))
                rec["source"] = "gget/Ensembl"
        except Exception as e:
            print(f"[resolve_gene] gget fallback unavailable: {e}")

    if not rec["ensembl"]:
        raise ValueError(
            f"Could not resolve '{query}' to an Ensembl gene id. "
            "Pass a valid human gene symbol, Ensembl gene id, or UniProt accession.")
    rec["symbol"] = rec["symbol"] or q
    return rec


# ===========================================================================
# 2. GTEx  (datalake-first, API fallback)  ->  per-tissue median TPM
# ===========================================================================
def _find_gtex_datalake_file() -> str | None:
    for pat in GTEX_DATALAKE_GLOBS:
        hits = glob.glob(pat, recursive=True)
        if hits:
            return sorted(hits)[0]
    return None


def get_gtex(ensembl: str, ensembl_versioned: str | None = None,
             timeout: int = 90) -> tuple[pd.DataFrame, str]:
    """Return (per-tissue GTEx DataFrame, source_string).

    DataFrame columns: tissue_gtex, n_samples, median_tpm, mean_tpm, gtex_version.
    Tries the mounted curated median-TPM file first; falls back to the GTEx v8 API.
    """
    dl = _find_gtex_datalake_file()
    if dl:
        try:
            return _gtex_from_datalake(dl, ensembl), f"GTEx datalake ({os.path.basename(dl)})"
        except Exception as e:
            print(f"[get_gtex] datalake file found but parse failed ({e}); using API.")

    # ---- API fallback (v8). The v2 API needs a VERSIONED gencodeId. ----
    import requests
    gid = ensembl_versioned or _guess_gencode_v8(ensembl)
    r = requests.get(GTEX_API, params={"gencodeId": gid, "datasetId": "gtex_v8"},
                     headers={"Accept": "application/json"}, timeout=timeout)
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        # retry with a couple of plausible versions if the guess missed
        for v in range(6, 16):
            cand = f"{ensembl}.{v}"
            rr = requests.get(GTEX_API, params={"gencodeId": cand, "datasetId": "gtex_v8"},
                              headers={"Accept": "application/json"}, timeout=timeout)
            if rr.ok and rr.json().get("data"):
                data = rr.json()["data"]; gid = cand; break
            time.sleep(0.5)   # bounded backoff between version retries
    if not data:
        raise RuntimeError(
            f"GTEx API returned no data for {ensembl}. Provide a versioned gencodeId "
            "(e.g. ENSG...\u200b.9) or check the gene id.")
    rows = []
    for rec in data:
        vals = np.array(rec["data"], dtype=float)
        rows.append({"tissue_gtex": rec["tissueSiteDetailId"], "n_samples": len(vals),
                     "median_tpm": float(np.median(vals)), "mean_tpm": float(np.mean(vals))})
    df = pd.DataFrame(rows).sort_values("median_tpm", ascending=False).reset_index(drop=True)
    df["gtex_version"] = "v8 (API, gtex_v8)"
    return df, f"GTEx v8 (portal API, dataset gtex_v8; gencodeId={gid})"


def _guess_gencode_v8(ensembl: str) -> str:
    """GTEx v8 uses GENCODE v26 versioned ids. If we don't know the version, the
    caller will retry; return the bare id with a common '.1' seed."""
    return f"{ensembl}.1"


def _gtex_from_datalake(path: str, ensembl: str) -> pd.DataFrame:
    """Extract one gene's per-tissue median TPM from a curated GTEx GCT/parquet.

    GCT format: 2 header lines, then columns Name, Description, <tissue cols...>.
    'Name' is a versioned Ensembl id; we match on the unversioned prefix.
    """
    base = ensembl.split(".")[0]
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
        name_col = next((c for c in df.columns if c.lower() in ("name", "gene_id", "ensembl")), df.columns[0])
        df["_base"] = df[name_col].astype(str).str.split(".").str[0]
        row = df[df["_base"] == base]
        if not len(row):
            raise KeyError(f"{ensembl} not in {path}")
        meta_cols = [c for c in df.columns if c.lower() in ("name", "description", "gene_id", "ensembl", "_base")]
        tissue_cols = [c for c in df.columns if c not in meta_cols]
        s = row.iloc[0][tissue_cols]
    else:  # GCT (gzipped or plain text)
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            f.readline(); f.readline()             # skip 2 header lines
            header = f.readline().rstrip("\n").split("\t")
            tissue_cols = header[2:]
            s = None
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if parts[0].split(".")[0] == base:
                    s = pd.Series(dict(zip(tissue_cols, [float(x) for x in parts[2:]])))
                    break
        if s is None:
            raise KeyError(f"{ensembl} not in {path}")
    out = pd.DataFrame({"tissue_gtex": s.index, "median_tpm": s.values.astype(float)})
    out["n_samples"] = np.nan   # curated medians: per-sample n not available in this file
    out["mean_tpm"] = np.nan
    out["gtex_version"] = f"v11 datalake ({os.path.basename(path)})"
    return out.sort_values("median_tpm", ascending=False).reset_index(drop=True)


# ===========================================================================
# 3. HPA  (stream-parse single entry from proteinatlas.xml.gz)
# ===========================================================================
def get_hpa(ensembl: str, symbol: str | None = None, xml_path: str = HPA_XML) -> dict:
    """Return dict with keys:
       df (per-tissue: tissue_hpa, organ_group_hpa, ntpm, ptpm, tpm),
       spec_category, spec_description, distribution, native_spec_score.
    Streams the gzip line-by-line and stops at the target <entry> (constant memory).
    """
    if not os.path.exists(xml_path):
        raise FileNotFoundError(f"HPA XML not found at {xml_path}")
    base = ensembl.split(".")[0]
    inside, entry, gene_txt = False, [], None
    with gzip.open(xml_path, "rt", encoding="utf-8") as f:
        for line in f:
            if "<entry" in line:
                inside, entry = True, []
            if inside:
                entry.append(line)
            if "</entry>" in line and inside:
                txt = "".join(entry)
                if base in txt:
                    gene_txt = txt; break
                inside = False
    if gene_txt is None:
        raise ValueError(f"{ensembl} ({symbol}) not found in HPA XML.")

    bm = re.search(r'<rnaExpression source="HPA"[^>]*assayType="consensusTissue">(.*?)</rnaExpression>',
                   gene_txt, re.S)
    if not bm:
        raise ValueError(f"No consensusTissue rnaExpression block for {ensembl} in HPA.")
    block = bm.group(1)

    spec = re.search(r'<rnaSpecificity description="([^"]*)" specificity="([^"]*)"', block)
    dist = re.search(r'<rnaDistribution[^>]*>([^<]+)</rnaDistribution>', block)
    spec_desc = spec.group(1) if spec else None
    spec_cat  = spec.group(2) if spec else None
    distribution = dist.group(1) if dist else None

    rows = []
    for db in re.findall(r'<data>(.*?)</data>', block, re.S):
        t = re.search(r'<tissue[^>]*organ="([^"]*)"[^>]*>([^<]+)</tissue>', db)
        if not t:
            continue
        ntpm = re.search(r'type="normalizedRNAExpression"\s+unitRNA="nTPM"\s+expRNA="([0-9.]+)"', db)
        ptpm = re.search(r'type="proteinCodingRNAExpression"\s+unitRNA="pTPM"\s+expRNA="([0-9.]+)"', db)
        tpm  = re.search(r'type="RNAExpression"\s+unitRNA="TPM"\s+expRNA="([0-9.]+)"', db)
        rows.append({"tissue_hpa": t.group(2), "organ_group_hpa": html.unescape(t.group(1)),
                     "ntpm": float(ntpm.group(1)) if ntpm else np.nan,
                     "ptpm": float(ptpm.group(1)) if ptpm else np.nan,
                     "tpm":  float(tpm.group(1))  if tpm  else np.nan})
    df = pd.DataFrame(rows).sort_values("ntpm", ascending=False).reset_index(drop=True)

    native_score = np.nan
    if os.path.exists(HPA_TSV) and symbol:
        try:
            tsv = pd.read_csv(HPA_TSV, sep="\t", low_memory=False)
            scol = next((c for c in tsv.columns if c.lower() == "rna tissue specificity score"), None)
            gcol = next((c for c in tsv.columns if c.lower() == "gene"), None)
            if scol and gcol:
                sub = tsv[tsv[gcol].astype(str).str.upper() == symbol.upper()]
                if len(sub):
                    native_score = float(sub.iloc[0][scol])
        except Exception:
            pass

    return {"df": df, "spec_category": spec_cat, "spec_description": spec_desc,
            "distribution": distribution, "native_spec_score": native_score}


# ===========================================================================
# 4. TAU tissue-specificity  (Yanai et al. 2005, on log2(x+1))
# ===========================================================================
def compute_tau(expr_values) -> float:
    """tau in [0,1]: 0 = ubiquitous, 1 = single-tissue-specific.
    Computed on log2(x+1)-transformed, max-normalized per-tissue values."""
    x = np.asarray(expr_values, dtype=float)
    x = x[~np.isnan(x)]
    x = np.clip(x, 0, None)
    xl = np.log2(x + 1.0)
    xmax = xl.max()
    if xmax <= 0:
        return float("nan")
    xhat = xl / xmax
    n = len(xl)
    return float(np.sum(1.0 - xhat) / (n - 1))


def compute_tau_linear(expr_values) -> float:
    """tau on raw (linear) values — reported alongside log2 tau for transparency."""
    x = np.asarray(expr_values, dtype=float)
    x = x[~np.isnan(x)]
    x = np.clip(x, 0, None)
    if x.max() <= 0 or len(x) < 2:
        return float("nan")
    return float(np.sum(1 - x / x.max()) / (len(x) - 1))


# ===========================================================================
# 5. HIGH-BASELINE FLAGGING  (dual threshold, per atlas)
# ===========================================================================
def flag_high_baseline(df: pd.DataFrame, val_col: str) -> tuple[pd.DataFrame, float]:
    """Flag tissues as high-baseline if (value >= ABS_MODERATE) OR (value >= 90th pctl).
    Adds columns: meets_abs10, meets_abs25, meets_top10pct, high_baseline, tier, flag_rule.
    Returns (annotated df, p90 cutoff)."""
    d = df.copy()
    p90 = float(np.percentile(d[val_col].dropna(), REL_PCTL))
    d["meets_abs10"]    = d[val_col] >= ABS_MODERATE
    d["meets_abs25"]    = d[val_col] >= ABS_HIGH
    d["meets_top10pct"] = d[val_col] >= p90
    d["high_baseline"]  = d["meets_abs10"] | d["meets_top10pct"]

    def tier(v):
        if v >= ABS_HIGH: return "high (>=25)"
        if v >= ABS_MODERATE: return "moderate (>=10)"
        return "low (<10)"
    d["tier"] = d[val_col].apply(tier)

    def rules(row):
        r = []
        if row["meets_abs25"]: r.append("abs>=25")
        elif row["meets_abs10"]: r.append("abs>=10")
        if row["meets_top10pct"]: r.append(f"top{100 - REL_PCTL}%")
        return "+".join(r) if r else "-"
    d["flag_rule"] = d.apply(rules, axis=1)
    return d, p90


# ===========================================================================
# 6. ORGAN COLLAPSE + CROSS-ATLAS CONCORDANCE
# ===========================================================================
def collapse_gtex_to_organ(gtex: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """Collapse GTEx fine sites -> organ (mean of member-site median TPM).
    Returns (organ df [organ, gtex_tpm], list of unmapped tissue ids)."""
    g = gtex.copy()
    g["organ"] = g["tissue_gtex"].map(GTEX_TO_ORGAN)
    unmapped = g[g["organ"].isna()]["tissue_gtex"].tolist()
    g = g[g["organ"].notna() & (g["organ"] != "__drop__")]
    organ = g.groupby("organ", as_index=False)["median_tpm"].mean().rename(
        columns={"median_tpm": "gtex_tpm"})
    return organ, unmapped


def collapse_hpa_to_organ(hpa: pd.DataFrame) -> pd.DataFrame:
    h = hpa.copy()
    h["organ"] = h["tissue_hpa"].apply(lambda t: HPA_NAME_MAP.get(t, t))
    return h.groupby("organ", as_index=False)["ntpm"].mean().rename(columns={"ntpm": "hpa_ntpm"})


def organ_concordance(gtex_organ: pd.DataFrame, hpa_organ: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Merge on shared organs; Spearman (primary, raw) + Pearson (log2).
    Returns (merged df, stats dict). If <3 shared organs, correlations are NaN."""
    from scipy.stats import spearmanr, pearsonr
    merged = gtex_organ.merge(hpa_organ, on="organ", how="inner").copy()
    merged["log2_gtex"] = np.log2(merged["gtex_tpm"] + 1)
    merged["log2_hpa"]  = np.log2(merged["hpa_ntpm"] + 1)
    if len(merged) >= 3:
        rho, p_rho = spearmanr(merged["gtex_tpm"], merged["hpa_ntpm"])
        r_pear, p_pear = pearsonr(merged["log2_gtex"], merged["log2_hpa"])
    else:
        rho = p_rho = r_pear = p_pear = float("nan")
    stats = {"n_organs": int(len(merged)),
             "spearman_rho": None if np.isnan(rho) else round(float(rho), 3),
             "spearman_p": None if np.isnan(p_rho) else float(p_rho),
             "pearson_r_log2": None if np.isnan(r_pear) else round(float(r_pear), 3),
             "pearson_p_log2": None if np.isnan(p_pear) else float(p_pear)}
    return merged.sort_values("gtex_tpm", ascending=False), stats


# ===========================================================================
# 7. SAFETY ORGAN MATRIX  (vital core + data-driven high-baseline)
# ===========================================================================
def build_safety_matrix(gtex_organ: pd.DataFrame, hpa_organ: pd.DataFrame,
                        gtex_flags: pd.DataFrame | None = None,
                        hpa_flags: pd.DataFrame | None = None) -> pd.DataFrame:
    """Assemble the on-target safety matrix.

    Panel = VITAL_CORE (always) UNION any organ flagged high-baseline in either atlas.
    Columns: organ, system, gtex_tpm, hpa_ntpm, on_target_flag.
    """
    gt_lu = dict(zip(gtex_organ["organ"], gtex_organ["gtex_tpm"]))
    hp_lu = dict(zip(hpa_organ["organ"], hpa_organ["hpa_ntpm"]))

    panel = list(VITAL_CORE)
    # add data-driven high-baseline organs (mapped to organ names)
    def _add_flagged(flags, name_col, mapper):
        if flags is None or not len(flags):
            return
        for t in flags[flags["high_baseline"]][name_col].tolist():
            org = mapper(t)
            if org and org != "__drop__" and org not in panel:
                panel.append(org)
    _add_flagged(gtex_flags, "tissue_gtex", lambda t: GTEX_TO_ORGAN.get(t))
    _add_flagged(hpa_flags, "tissue_hpa", lambda t: HPA_NAME_MAP.get(t, t))

    rows = []
    for org in panel:
        g = gt_lu.get(org, np.nan); h = hp_lu.get(org, np.nan)
        hi  = (pd.notna(g) and g >= ABS_HIGH) or (pd.notna(h) and h >= ABS_HIGH)
        mod = (pd.notna(g) and g >= ABS_MODERATE) or (pd.notna(h) and h >= ABS_MODERATE)
        flag = "High on-target expression" if hi else ("Moderate" if mod else "Low / negligible")
        rows.append({"organ": org, "system": SYSTEM_MAP.get(org, "Other"),
                     "gtex_tpm": round(g, 2) if pd.notna(g) else np.nan,
                     "hpa_ntpm": round(h, 2) if pd.notna(h) else np.nan,
                     "on_target_flag": flag})
    sf = pd.DataFrame(rows)
    # order: high -> moderate -> low, then by max expression
    order = {"High on-target expression": 0, "Moderate": 1, "Low / negligible": 2}
    sf["_o"] = sf["on_target_flag"].map(order)
    sf["_m"] = sf[["gtex_tpm", "hpa_ntpm"]].max(axis=1)
    sf = sf.sort_values(["_o", "_m"], ascending=[True, False]).drop(columns=["_o", "_m"]).reset_index(drop=True)
    return sf


# ===========================================================================
# Orchestrator — run the full analysis for one gene.
# ===========================================================================
def run_analysis(gene: str, outdir: str) -> dict:
    """End-to-end: resolve -> ingest GTEx+HPA -> tau -> flags -> concordance -> safety.
    Writes CSV tables to <outdir>/tables/ and returns a results dict for reporting.
    GTEx or HPA ingestion may fail independently; the analysis degrades gracefully.
    """
    tbl = os.path.join(outdir, "tables"); os.makedirs(tbl, exist_ok=True)
    rec = resolve_gene(gene)
    sym = rec["symbol"]; ens = rec["ensembl"]
    # Preserve the original gene-symbol case in output filenames (e.g. 'GCGR_'
    # not 'gcgr_') to match the expected-output naming convention.
    slug = re.sub(r"[^A-Za-z0-9]+", "_", sym).strip("_")
    res = {"gene": sym, "ensembl": ens, "uniprot": rec.get("uniprot"),
           "id_source": rec.get("source"), "slug": slug, "outdir": outdir,
           "gtex": None, "hpa": None, "warnings": []}

    # ---- GTEx ----
    try:
        gtex, gsrc = get_gtex(ens, rec.get("ensembl_versioned"))
        gtex.to_csv(f"{tbl}/{slug}_gtex_per_tissue.csv", index=False)
        gtex_f, gp90 = flag_high_baseline(gtex, "median_tpm")
        res["gtex"] = {"df": gtex, "flags": gtex_f, "p90": gp90, "source": gsrc,
                       "tau_log2": compute_tau(gtex["median_tpm"].values),
                       "tau_linear": compute_tau_linear(gtex["median_tpm"].values),
                       "n_tissues": int(len(gtex)),
                       "top_tissue": gtex.iloc[0]["tissue_gtex"],
                       "top_value": float(gtex.iloc[0]["median_tpm"])}
    except Exception as e:
        res["warnings"].append(f"GTEx ingestion failed: {e}")
        print(f"[run_analysis] GTEx failed: {e}")

    # ---- HPA ----
    try:
        hpa_d = get_hpa(ens, sym)
        hpa = hpa_d["df"]
        hpa.to_csv(f"{tbl}/{slug}_hpa_per_tissue.csv", index=False)
        hpa_f, hp90 = flag_high_baseline(hpa, "ntpm")
        res["hpa"] = {"df": hpa, "flags": hpa_f, "p90": hp90,
                      "tau_log2": compute_tau(hpa["ntpm"].values),
                      "tau_linear": compute_tau_linear(hpa["ntpm"].values),
                      "n_tissues": int(len(hpa)),
                      "top_tissue": hpa.iloc[0]["tissue_hpa"],
                      "top_value": float(hpa.iloc[0]["ntpm"]),
                      "spec_category": hpa_d["spec_category"],
                      "spec_description": hpa_d["spec_description"],
                      "distribution": hpa_d["distribution"],
                      "native_spec_score": hpa_d["native_spec_score"]}
    except Exception as e:
        res["warnings"].append(f"HPA ingestion failed: {e}")
        print(f"[run_analysis] HPA failed: {e}")

    # ---- tau table ----
    tau_rows = []
    if res["gtex"]:
        tau_rows.append({"atlas": "GTEx", "n_tissues": res["gtex"]["n_tissues"],
                         "tau_log2": round(res["gtex"]["tau_log2"], 4),
                         "tau_linear": round(res["gtex"]["tau_linear"], 4),
                         "top_tissue": res["gtex"]["top_tissue"]})
    if res["hpa"]:
        tau_rows.append({"atlas": "HPA consensus", "n_tissues": res["hpa"]["n_tissues"],
                         "tau_log2": round(res["hpa"]["tau_log2"], 4),
                         "tau_linear": round(res["hpa"]["tau_linear"], 4),
                         "top_tissue": res["hpa"]["top_tissue"]})
    if tau_rows:
        pd.DataFrame(tau_rows).to_csv(f"{tbl}/{slug}_tau_scores.csv", index=False)
    res["tau_table"] = tau_rows

    # ---- high-baseline flags table ----
    flag_frames = []
    if res["gtex"]:
        gf = res["gtex"]["flags"]
        gf = gf[gf["high_baseline"]][["tissue_gtex", "median_tpm", "tier", "flag_rule"]].rename(
            columns={"tissue_gtex": "tissue", "median_tpm": "value"})
        gf["atlas"] = "GTEx"; gf["unit"] = "median TPM"; flag_frames.append(gf)
    if res["hpa"]:
        hf = res["hpa"]["flags"]
        hf = hf[hf["high_baseline"]][["tissue_hpa", "ntpm", "tier", "flag_rule"]].rename(
            columns={"tissue_hpa": "tissue", "ntpm": "value"})
        hf["atlas"] = "HPA"; hf["unit"] = "nTPM"; flag_frames.append(hf)
    if flag_frames:
        pd.concat(flag_frames, ignore_index=True)[
            ["atlas", "tissue", "value", "unit", "tier", "flag_rule"]
        ].to_csv(f"{tbl}/{slug}_high_baseline_flags.csv", index=False)

    # ---- concordance (needs both atlases) ----
    if res["gtex"] and res["hpa"]:
        gorg, unmapped = collapse_gtex_to_organ(res["gtex"]["df"])
        horg = collapse_hpa_to_organ(res["hpa"]["df"])
        merged, cstats = organ_concordance(gorg, horg)
        merged.to_csv(f"{tbl}/{slug}_organ_concordance.csv", index=False)
        res["concordance"] = {"merged": merged, "stats": cstats, "unmapped_gtex": unmapped}
        res["gtex_organ"], res["hpa_organ"] = gorg, horg
        if unmapped:
            res["warnings"].append(f"Unmapped GTEx tissues (excluded from concordance): {unmapped}")
    else:
        res["concordance"] = None
        res["gtex_organ"] = (collapse_gtex_to_organ(res["gtex"]["df"])[0] if res["gtex"] else pd.DataFrame(columns=["organ", "gtex_tpm"]))
        res["hpa_organ"]  = (collapse_hpa_to_organ(res["hpa"]["df"]) if res["hpa"] else pd.DataFrame(columns=["organ", "hpa_ntpm"]))

    # ---- safety matrix ----
    safety = build_safety_matrix(
        res["gtex_organ"], res["hpa_organ"],
        gtex_flags=res["gtex"]["flags"] if res["gtex"] else None,
        hpa_flags=res["hpa"]["flags"] if res["hpa"] else None)
    safety.to_csv(f"{tbl}/{slug}_safety_organ_matrix.csv", index=False)
    res["safety"] = safety
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Target tissue-expression specificity & on-target safety.")
    ap.add_argument("gene", help="Gene symbol, Ensembl id, or UniProt accession")
    ap.add_argument("-o", "--outdir", default="/mnt/results", help="Output directory")
    a = ap.parse_args()
    r = run_analysis(a.gene, a.outdir)
    print(json.dumps({k: v for k, v in r.items()
                      if k in ("gene", "ensembl", "uniprot", "id_source", "tau_table", "warnings")},
                     indent=2, default=str))
