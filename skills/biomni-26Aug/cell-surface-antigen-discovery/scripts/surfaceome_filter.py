#!/usr/bin/env python3
"""Surfaceome membership + extracellular-topology / ectodomain-accessibility filter.

Antibody modalities (ADC / CAR-T / bispecific) can only engage an epitope that is
physically exposed on the OUTSIDE of an intact plasma membrane. This module gates
out proteins that are NOT antibody-accessible even if they intersect a surfaceome
list: cytoplasmic plaque proteins (e.g. cingulin / CGN), ER / organelle-membrane
proteins (e.g. ITPR3), and secreted / basement-membrane ECM proteins (e.g. laminins).

It loads the bundled surfaceome seed (curated, with UniProt-derived topology) and is
designed to be cross-checked / extended with Open Targets `subcellularLocations` and
the full in-silico surfaceome (SURFY) for genome-wide runs.

Per skill convention, the filter REPORTS what it removes ("Removed N", "Missing N")
rather than silently dropping rows.
"""

import math
import os
import re
import urllib.request

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEED_CSV = os.path.join(_HERE, "..", "references", "surfaceome_seed.csv")

# Ectodomain accessibility ordering (higher = more antibody-accessible).
ACCESSIBILITY_RANK = {"none": 0, "low": 1, "partial": 2, "high": 3}

# Localizations that are NOT a plasma-membrane ectodomain -> hard-gated out.
NON_SURFACE_LOCALIZATIONS = {"cytoplasmic", "intracellular_er", "secreted_ecm", "nuclear"}

# ---------------------------------------------------------------------------
# SURFY in-silico surfaceome (Bausch-Fluck et al. 2018, PNAS) — genome-scale.
# ---------------------------------------------------------------------------
# Table S3 is journal supplementary data (cite the paper; not redistributed with
# this skill). It is downloaded + cached at runtime, exactly like the HPA baseline.
# The wlab.ethz.ch host migrated to wollscheidlab.org and now serves a Git-LFS
# pointer for the .xlsx; the ETH-lab R package `steveneschrich/surfaceome` vendors a
# byte-identical copy (sha256 verified against the wollscheidlab LFS oid), used here
# as a fallback mirror. A local path may be passed to skip the network entirely.
SURFY_URLS = (
    "https://wollscheidlab.org/SURFY/table_S3_surfaceome.xlsx",  # official (may serve an LFS pointer)
    "https://wlab.ethz.ch/surfaceome/table_S3_surfaceome.xlsx",  # legacy official
    "https://raw.githubusercontent.com/steveneschrich/surfaceome/main/"
    "data-raw/surfy/table_S3_surfaceome.xlsx",                    # byte-identical mirror
)
SURFY_MASTER_SHEET = "SurfaceomeMasterTable"

# A genome-scale candidate set whose topology gate removes NOTHING is an inert gate
# (the bug this fix targets: every SURFY member blanket-assigned plasma_membrane).
# Above this many candidates, apply_topology_filter() requires >0 removals or raises.
GENOME_SCALE_MIN_CANDIDATES = 200

# SURFY "Surfaceome Label Source" values that reflect EXPERIMENTAL surface evidence
# (Cell Surface Protein Atlas positive-training set / UniProt GPI anchor) rather than
# a pure machine-learning prediction. Used to seed the confirmed/unconfirmed split.
SURFY_EXPERIMENTAL_SOURCES = {"pos. trainingset", "gpi (uniprot)"}
# Ectodomain shorter than this many non-cytoplasmic residues cannot host an antibody
# epitope -> accessibility "none" -> gated (e.g. ESYT3 = 5 aa, TRAT1 = 11 aa loops).
MIN_ECTODOMAIN_AA = 15
_ORGANELLE_TERMS = ("endoplasmic reticulum", "golgi", "mitochond", "nucle",
                    "endosome", "lysosome", "peroxisome")
_CELLMEM_TERMS = ("cell membrane", "plasma membrane", "apical", "basolateral", "cell surface")


def load_surfaceome(include_in_silico=True, seed_csv=_SEED_CSV):
    """Load the bundled surfaceome reference.

    Parameters
    ----------
    include_in_silico : bool
        If False, keep only MS-validated entries (higher confidence, fewer genes).
    seed_csv : str
        Path to the surfaceome seed CSV.

    Returns
    -------
    pandas.DataFrame with columns:
        gene_symbol, surfaceome_class, topology, ectodomain_accessibility,
        localization, notes
    """
    if not os.path.exists(seed_csv):
        raise FileNotFoundError(
            f"Surfaceome seed not found: {seed_csv}. "
            "For a genome-wide run, fetch the full in-silico surfaceome "
            "(see references/census_atlas_guide.md)."
        )
    df = pd.read_csv(seed_csv)
    df["gene_symbol"] = df["gene_symbol"].astype(str).str.strip()
    df["ectodomain_accessibility"] = (
        df["ectodomain_accessibility"].astype(str).str.strip().str.lower()
    )
    df["localization"] = df["localization"].astype(str).str.strip().str.lower()
    if not include_in_silico:
        before = len(df)
        df = df[df["surfaceome_class"].astype(str).str.lower() == "ms-validated"].copy()
        print(f"  load_surfaceome: kept MS-validated only ({len(df)}/{before})")
    df = df.drop_duplicates(subset="gene_symbol").reset_index(drop=True)
    print(f"✓ Surfaceome loaded: {len(df)} surface genes")
    return df


# ---------------------------------------------------------------------------
# SURFY (genome-scale in-silico surfaceome) loader — PER-GENE topology.
# ---------------------------------------------------------------------------
# CRITICAL: do NOT assign a blanket `localization = plasma_membrane` to SURFY
# members (that makes the topology gate inert). Every field below is derived
# per-gene from SURFY Table S3's own topology string, TM count, Almen class, and
# label source, so cytoplasmic-tail / contact-site / organelle-membrane proteins
# are demoted or gated exactly like the curated seed treats CGN / ITPR3 / laminins.

def _parse_noncyt_len(topology):
    """Sum of non-cytoplasmic (extracellular-facing) residues from a SURFY topology
    string like 'SP:1-24;NC:25-305;TM:306-330;CY:331-362'. Returns the total NC
    length (0 if no NC segment; NaN if the string is unparseable/empty).

    This is the antibody-addressable ectodomain size. A protein whose only
    non-cytoplasmic exposure is a handful of residues (ESYT3: NC 47-51 = 5 aa) has
    no epitope, even though it sits in the membrane.
    """
    if not isinstance(topology, str) or not topology.strip():
        return float("nan")
    total = 0
    for seg in topology.split(";"):
        mo = re.match(r"^\s*(SP|NC|TM|CY)\s*:\s*(\d+)\s*-\s*(\d+)\s*$", seg)
        if mo and mo.group(1) == "NC":
            total += int(mo.group(3)) - int(mo.group(2)) + 1
    return float(total)


def _surfy_accessibility(n_tm, noncyt_len, source, almen):
    """Map SURFY topology to the skill's {none, low, partial, high} ectodomain
    accessibility, from ectodomain size + membrane topology class (NOT a blanket)."""
    src = str(source or "").strip().lower()
    if "gpi" in src:
        return "high"  # GPI-anchored: the mature protein is entirely extracellular
    nl = 0.0 if (noncyt_len is None or (isinstance(noncyt_len, float) and math.isnan(noncyt_len))) else float(noncyt_len)
    if nl < MIN_ECTODOMAIN_AA:
        return "none"  # no antibody-addressable ectodomain -> gated upstream
    ntm = 0
    if n_tm is not None and not (isinstance(n_tm, float) and math.isnan(n_tm)):
        try:
            ntm = int(round(float(n_tm)))
        except (TypeError, ValueError):
            ntm = 0
    if ntm <= 1:  # single-pass (or GPI/peripheral with a real ectodomain)
        if nl >= 150:
            return "high"
        if nl >= 60:
            return "partial"
        return "low"
    if ntm >= 7:  # GPCR / large multipass: minimal accessible epitope
        return "low"
    return "partial" if nl >= 150 else "low"  # 2-6 TM transporters/channels/claudins


def _surfy_localization(surface_label, subcellular):
    """Assign localization per-gene. Surface-labeled proteins that UniProt places on
    an organelle membrane with NO plasma-/cell-membrane annotation are treated as
    intracellular (gated); genuine PM proteins that merely transit the ER/Golgi
    (e.g. EGFR, MSLN) keep `plasma_membrane`."""
    lab = str(surface_label or "").strip().lower()
    sub = str(subcellular or "").strip().lower()
    if lab == "nonsurface":
        if "secret" in sub or "extracellular matrix" in sub:
            return "secreted_ecm"
        if "nucle" in sub:
            return "nuclear"
        return "cytoplasmic"
    cellmem = any(k in sub for k in _CELLMEM_TERMS)
    organelle = any(k in sub for k in _ORGANELLE_TERMS)
    if organelle and not cellmem:
        return "intracellular_er"  # organelle-membrane resident, not the PM -> gated
    return "plasma_membrane"


def _download_surfy(source, cache_dir):
    """Return a local path to Table S3, downloading + caching if `source` is a URL
    (or None -> try SURFY_URLS in order). Validates the payload is a real .xlsx (zip
    'PK' magic), never an HTML error page or a Git-LFS pointer."""
    if source and os.path.exists(str(source)):
        return str(source)
    os.makedirs(cache_dir, exist_ok=True)
    cached = os.path.join(cache_dir, "table_S3_surfaceome.xlsx")
    if os.path.exists(cached) and os.path.getsize(cached) > 100000:
        return cached
    urls = [source] if source else list(SURFY_URLS)
    last = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Biomni surfaceome loader)"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if data[:2] != b"PK":  # not a zip/xlsx (HTML error page or LFS pointer)
                last = f"{url}: not an .xlsx (first bytes {data[:16]!r})"
                continue
            with open(cached, "wb") as fh:
                fh.write(data)
            print(f"  SURFY Table S3 downloaded from {url} ({len(data)} bytes) -> {cached}")
            return cached
        except Exception as exc:  # noqa: BLE001
            last = f"{url}: {exc}"
            continue
    raise RuntimeError(
        "Could not obtain SURFY Table S3 (table_S3_surfaceome.xlsx). Last error: "
        f"{last}. Pass a local path via load_surfy_surfaceome(source=...) "
        "(download from wollscheidlab.org/SURFY and cite Bausch-Fluck et al. 2018 PNAS).")


def load_surfy_surfaceome(source=None, cache_dir="results/surfy_cache",
                          include_nonsurface=False, sheet=SURFY_MASTER_SHEET):
    """Load the genome-scale SURFY in-silico surfaceome (~2,886 surface proteins)
    with PER-GENE topology/accessibility/localization, in the same schema as
    ``load_surfaceome`` plus provenance columns.

    Returns a DataFrame with the seed columns (gene_symbol, surfaceome_class,
    topology, ectodomain_accessibility, localization, notes) AND:
        surfy_label, surfy_source, almen_class, n_tm, noncyt_len, ensembl_gene,
        surface_confirmation_surfy  (confirmed_experimental | predicted)

    ``surface_confirmation_surfy`` seeds the confirmed/unconfirmed split completed in
    scoring with Open Targets plasma-membrane confirmation:
    CSPA positive-training-set / GPI-anchored -> confirmed_experimental; a pure
    machine-learning prediction -> predicted (unconfirmed unless OT confirms it).
    """
    path = _download_surfy(source, cache_dir)
    raw = pd.read_excel(path, sheet_name=sheet, header=1)
    raw = raw[raw["Surfaceome Label"].notna()].copy()
    if not include_nonsurface:
        raw = raw[raw["Surfaceome Label"].astype(str).str.strip().str.lower() == "surface"].copy()

    rows = []
    for _, r in raw.iterrows():
        gene = str(r.get("UniProt gene") or "").strip()
        if not gene or gene.lower() == "nan":
            continue
        src = str(r.get("Surfaceome Label Source") or "").strip()
        label = str(r.get("Surfaceome Label") or "").strip()
        n_tm = r.get("TM domains")
        topo = r.get("topology")
        almen = str(r.get("Membranome Almen main-class") or "").strip()
        subloc = r.get("UniProt subcellular")
        noncyt = _parse_noncyt_len(topo)
        acc = _surfy_accessibility(n_tm, noncyt, src, almen)
        loc = _surfy_localization(label, subloc)
        is_exp = src.lower() in SURFY_EXPERIMENTAL_SOURCES
        try:
            ntm_int = int(round(float(n_tm))) if pd.notna(n_tm) else None
        except (TypeError, ValueError):
            ntm_int = None
        rows.append({
            "gene_symbol": gene,
            "surfaceome_class": "MS-validated" if is_exp else "in-silico",
            "topology": (f"{ntm_int}TM" if ntm_int is not None else "unknown") if label == "surface" else "non-surface",
            "ectodomain_accessibility": acc,
            "localization": loc,
            "notes": f"SURFY {label}; source={src or 'NA'}; Almen={almen or 'NA'}; "
                     f"ectodomain~{int(noncyt) if pd.notna(noncyt) else 'NA'}aa",
            "surfy_label": label,
            "surfy_source": src,
            "almen_class": almen,
            "n_tm": ntm_int,
            "noncyt_len": None if (isinstance(noncyt, float) and math.isnan(noncyt)) else int(noncyt),
            "ensembl_gene": (str(r.get("Ensembl gene")).strip() if pd.notna(r.get("Ensembl gene")) else None),
            "surface_confirmation_surfy": "confirmed_experimental" if is_exp else "predicted",
        })
    df = pd.DataFrame(rows)
    # De-dupe by gene, preferring experimental source then the larger ectodomain.
    df["_exp"] = (df["surface_confirmation_surfy"] == "confirmed_experimental").astype(int)
    df["_nl"] = df["noncyt_len"].fillna(-1)
    df = (df.sort_values(["_exp", "_nl"], ascending=[False, False])
            .drop_duplicates(subset="gene_symbol").drop(columns=["_exp", "_nl"])
            .reset_index(drop=True))
    acc_counts = df["ectodomain_accessibility"].value_counts().to_dict()
    n_exp = int((df["surface_confirmation_surfy"] == "confirmed_experimental").sum())
    print(f"✓ SURFY surfaceome loaded: {len(df)} genes "
          f"({n_exp} experimental-source, {len(df) - n_exp} ML-predicted); "
          f"accessibility {acc_counts}")
    return df


def _is_surface(row):
    """A gene is antibody-accessible if it sits in the plasma membrane AND has a
    non-`none` ectodomain accessibility."""
    loc = str(row.get("localization", "")).lower()
    acc = str(row.get("ectodomain_accessibility", "none")).lower()
    if loc in NON_SURFACE_LOCALIZATIONS:
        return False
    if ACCESSIBILITY_RANK.get(acc, 0) <= 0:
        return False
    return True


def apply_topology_filter(spec_df, surfaceome_df, drop_inaccessible=True, genome_scale=None):
    """Intersect candidate expression rows with the surfaceome and gate out
    proteins that are not antibody-accessible.

    Parameters
    ----------
    spec_df : pandas.DataFrame
        Consensus compartment-expression table (must have `gene_symbol`).
        If None, the full surfaceome is used as the candidate set.
    surfaceome_df : pandas.DataFrame
        Output of `load_surfaceome()` or `load_surfy_surfaceome()`.
    drop_inaccessible : bool
        If True, remove rows whose localization is non-surface (cytoplasmic / ER /
        secreted-ECM) or whose accessibility is `none`. low/partial/high are kept
        (and penalized later in scoring).
    genome_scale : bool or None
        Enables the inert-gate check. If None, inferred True when the candidate set
        has >= GENOME_SCALE_MIN_CANDIDATES genes. When True, the filter RAISES if it
        removes nothing — a genome-scale surfaceome that passes every gene means the
        per-gene topology was not applied (the blanket-plasma_membrane bug), and a
        gate that never fires is not a gate.

    Returns
    -------
    pandas.DataFrame: surfaceome topology for the PASSING candidates
        (gene_symbol, surfaceome_class, topology, ectodomain_accessibility,
         localization, accessibility_rank, passed_topology)
    """
    surf = surfaceome_df.copy()
    surf["accessibility_rank"] = surf["ectodomain_accessibility"].map(
        lambda a: ACCESSIBILITY_RANK.get(str(a).lower(), 0)
    )
    surf["passed_topology"] = surf.apply(_is_surface, axis=1)

    if spec_df is not None and "gene_symbol" in getattr(spec_df, "columns", []):
        cand_genes = set(spec_df["gene_symbol"].astype(str))
        in_surf = surf[surf["gene_symbol"].isin(cand_genes)].copy()
        missing = sorted(cand_genes - set(surf["gene_symbol"]))
        if missing:
            print(
                f"  Missing {len(missing)} candidate gene(s) not in surfaceome "
                f"(no topology call): {', '.join(missing[:10])}"
                + (" ..." if len(missing) > 10 else "")
            )
    else:
        in_surf = surf.copy()

    removed = in_surf[~in_surf["passed_topology"]]
    n_candidates = len(in_surf)
    if len(removed):
        by_loc = removed["localization"].value_counts().to_dict()
        by_acc = removed["ectodomain_accessibility"].value_counts().to_dict()
        print(
            f"  Removed {len(removed)} non-accessible protein(s) "
            f"(not a plasma-membrane ectodomain): localization={by_loc}; accessibility={by_acc}"
        )
        for _, r in removed.head(15).iterrows():
            print(f"    - {r['gene_symbol']}: {r['localization']} / {r['ectodomain_accessibility']}")
        if len(removed) > 15:
            print(f"    ... and {len(removed) - 15} more")

    # Inert-gate check: a genome-scale candidate set that excludes NOTHING means the
    # per-gene topology was not really applied (the blanket-plasma_membrane bug).
    if genome_scale is None:
        genome_scale = n_candidates >= GENOME_SCALE_MIN_CANDIDATES
    if genome_scale and drop_inaccessible and len(removed) == 0:
        raise RuntimeError(
            f"Topology gate excluded 0 of {n_candidates} genome-scale candidates. "
            "A gate that never fires is not a gate: every candidate was passed as an "
            "accessible plasma-membrane ectodomain, which indicates a blanket "
            "localization/accessibility assignment rather than per-gene SURFY topology. "
            "Load the surfaceome with load_surfy_surfaceome() (per-gene topology) — do "
            "NOT assign every SURFY member localization='plasma_membrane'.")

    out = in_surf[in_surf["passed_topology"]].copy() if drop_inaccessible else in_surf.copy()
    out = out.reset_index(drop=True)
    n_removed = n_candidates - len(out) if drop_inaccessible else int((~in_surf["passed_topology"]).sum())
    print(f"✓ Topology filter: {len(out)} antibody-accessible surface candidate(s) retained "
          f"({n_removed} excluded of {n_candidates})")
    return out


if __name__ == "__main__":
    sf = load_surfaceome()
    passing = apply_topology_filter(None, sf)
    print(passing[["gene_symbol", "topology", "ectodomain_accessibility", "passed_topology"]].head(15))
