"""
============================================================================
DERIVE MARKER PANELS  —  cell-therapy scRNA-seq QC release scorecard
============================================================================

Resolve the target cell type (and off-target lineages, pluripotency, maturity)
to marker-gene panels. Resolution order for each panel:

  1) Built-in curated registry (product_type_registry) — fast, validated,
     covers common cell-therapy targets. This is the source of truth for the
     iPSC-NK reference logic.
  2) CellMarker2 datalake (/mnt/datalake/cellmarker2/) — fills gaps for target
     cell types not in the registry, using canonical human/mouse markers.
  3) LiteratureSearch — the AGENT runs this to add/confirm product-specific
     identity anchors, maturity axes, and pluripotency panels, then passes the
     genes in via `literature_markers=`. (This script does not call tools.)

The function returns a `panels` dict consumed by score_modules.py and records
the exact panels used to tables/marker_panels_used.csv for auditability.

Functions
  - resolve_panels(cfg, target_cell, source, literature_markers=None,
                   cellmarker2_dir=..., species=...) -> panels dict
  - cellmarker2_lookup(cell_type, species, dir) -> list[str]   (best-effort)

Usage
  from derive_marker_panels import resolve_panels
  panels = resolve_panels(cfg, cfg["target_cell"], cfg["source"])
"""

import os
import glob
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Import the curated registry (same package dir)
try:
    from product_type_registry import (
        TARGET_REGISTRY, PLURIPOTENCY_PANELS, OFFTARGET_PANELS,
        PROLIFERATION_MARKERS, resolve_target_key,
    )
except Exception:  # allow running the file standalone from another cwd
    from importlib import import_module
    _m = import_module("product_type_registry")
    TARGET_REGISTRY = _m.TARGET_REGISTRY
    PLURIPOTENCY_PANELS = _m.PLURIPOTENCY_PANELS
    OFFTARGET_PANELS = _m.OFFTARGET_PANELS
    PROLIFERATION_MARKERS = _m.PROLIFERATION_MARKERS
    resolve_target_key = _m.resolve_target_key


CELLMARKER2_DIR = "/mnt/datalake/cellmarker2"


def cellmarker2_lookup(cell_type: str, species: str = "human",
                       cellmarker2_dir: str = CELLMARKER2_DIR,
                       top_n: int = 15) -> List[str]:
    """Best-effort marker lookup from CellMarker2 Excel tables.

    Returns the most frequently reported marker genes for the closest matching
    cell-name. Returns [] if the datalake or a match is unavailable — callers
    should fall back to the registry / literature.
    """
    fname = "Cell_marker_Human.xlsx" if species == "human" else "Cell_marker_Mouse.xlsx"
    path = os.path.join(cellmarker2_dir, fname)
    if not os.path.exists(path):
        # try any xlsx in the dir
        hits = glob.glob(os.path.join(cellmarker2_dir, "*.xlsx"))
        if not hits:
            return []
        path = hits[0]
    try:
        df = pd.read_excel(path)
    except Exception:
        return []
    # column names in CellMarker2: 'cell_name', 'marker' (Symbol), 'Symbol'
    cols = {c.lower(): c for c in df.columns}
    cell_col = cols.get("cell_name") or cols.get("cellname") or cols.get("cell_type")
    gene_col = cols.get("symbol") or cols.get("marker") or cols.get("genesymbol")
    if not (cell_col and gene_col):
        return []
    q = cell_type.lower().replace(" cell", "").strip()
    sub = df[df[cell_col].astype(str).str.lower().str.contains(q, na=False)]
    if sub.empty:
        return []
    genes = (sub[gene_col].astype(str)
             .str.upper().str.split(r"[,\s;]+").explode())
    genes = genes[genes.str.match(r"^[A-Z0-9\-]+$", na=False)]
    top = genes.value_counts().head(top_n).index.tolist()
    return top


def resolve_panels(
    cfg: Dict,
    target_cell: str,
    source: str,
    literature_markers: Optional[Dict[str, List[str]]] = None,
    species: Optional[str] = None,
    cellmarker2_dir: str = CELLMARKER2_DIR,
) -> Dict:
    """Build the panels dict for all active modules.

    literature_markers (optional): dict the AGENT fills after LiteratureSearch,
      any of: {'identity_anchor':[...], 'maturity_mature':[...],
               'maturity_immature':[...], 'pluripotency_specific':[...],
               'pluripotency_core':[...], 'offtarget_extra':{lineage:[...]}}
      These are merged on top of registry/CellMarker2 panels.
    """
    species = species or cfg.get("species", "human")
    lit = literature_markers or {}
    key = resolve_target_key(target_cell)
    reg = TARGET_REGISTRY.get(key, {})

    # ---- Module A: identity anchor + fidelity lineages ----
    identity_anchor = list(reg.get("identity_anchor", []))
    if not identity_anchor:
        identity_anchor = cellmarker2_lookup(target_cell, species, cellmarker2_dir)[:8]
    identity_anchor = _merge(identity_anchor, lit.get("identity_anchor"))

    # "aberrant lineage" sets used to grade fidelity of target cells (Module A)
    fidelity_lineages = dict(reg.get("fidelity_lineages", {}))

    # ---- Module B: pluripotency (iPSC/ESC only) ----
    pluri_core = _merge(PLURIPOTENCY_PANELS["core"], lit.get("pluripotency_core"))
    pluri_specific = _merge(PLURIPOTENCY_PANELS["specific"], lit.get("pluripotency_specific"))
    pluri_triad = PLURIPOTENCY_PANELS["triad"]

    # ---- Module C: off-target lineages ----
    # start from the global off-target panels, then drop the target's own lineage(s)
    offtarget = {k: list(v) for k, v in OFFTARGET_PANELS.items()}
    for excl in reg.get("offtarget_exclude", []):
        offtarget.pop(excl, None)
    if lit.get("offtarget_extra"):
        for lin, genes in lit["offtarget_extra"].items():
            offtarget[lin] = _merge(offtarget.get(lin, []), genes)

    # ---- Module D: maturity axis (only if defined) ----
    mat = reg.get("maturity", {})
    maturity_mature = _merge(mat.get("mature", []), lit.get("maturity_mature"))
    maturity_immature = _merge(mat.get("immature", []), lit.get("maturity_immature"))
    has_maturity_axis = bool(maturity_mature and maturity_immature)

    # turn Module D off in cfg if no axis exists (and user didn't force it)
    if not has_maturity_axis and cfg["modules"].get("D") and "D" not in (lit.get("_forced") or []):
        cfg["modules"]["D"] = False
        print("  ⚠ no maturity axis defined for target — Module D turned OFF "
              "(provide literature_markers['maturity_mature'/'maturity_immature'] to enable)")

    panels = {
        "target_key": key,
        "identity_anchor": identity_anchor,
        "fidelity_lineages": fidelity_lineages,
        "pluripotency": {"core": pluri_core, "specific": pluri_specific, "triad": pluri_triad},
        "offtarget": offtarget,
        "maturity": {"mature": maturity_mature, "immature": maturity_immature,
                     "has_axis": has_maturity_axis},
        "proliferation": PROLIFERATION_MARKERS,
        "engineering_features": _engineering_features(cfg.get("engineering")),
    }

    _audit(cfg, panels, source)
    return panels


def _merge(base, extra):
    base = list(base or [])
    for g in (extra or []):
        if g not in base:
            base.append(g)
    return base


def _engineering_features(engineering: Optional[str]) -> List[str]:
    """Pull likely transgene feature tokens from an engineering string."""
    if not engineering:
        return []
    toks = []
    for t in str(engineering).replace("/", " ").replace("-", " ").split():
        t = t.strip().upper()
        if t and t not in ("CAR", "THE", "AND"):
            toks.append(t)
    # common reporter/transgene symbols
    toks += ["EGFP", "GFP"]
    return list(dict.fromkeys(toks))


def _audit(cfg: Dict, panels: Dict, source: str) -> None:
    rows = []
    rows.append(("identity_anchor", ",".join(panels["identity_anchor"])))
    for lin, gl in panels["fidelity_lineages"].items():
        rows.append((f"fidelity:{lin}", ",".join(gl)))
    if source in ("ipsc", "esc"):
        rows.append(("pluripotency_core", ",".join(panels["pluripotency"]["core"])))
        rows.append(("pluripotency_specific", ",".join(panels["pluripotency"]["specific"])))
        rows.append(("pluripotency_triad", ",".join(panels["pluripotency"]["triad"])))
    for lin, gl in panels["offtarget"].items():
        rows.append((f"offtarget:{lin}", ",".join(gl)))
    if panels["maturity"]["has_axis"]:
        rows.append(("maturity_mature", ",".join(panels["maturity"]["mature"])))
        rows.append(("maturity_immature", ",".join(panels["maturity"]["immature"])))
    rows.append(("proliferation", ",".join(panels["proliferation"])))
    if panels["engineering_features"]:
        rows.append(("engineering_features", ",".join(panels["engineering_features"])))
    df = pd.DataFrame(rows, columns=["panel", "genes"])
    out = os.path.join(cfg["dirs"]["tables"], "marker_panels_used.csv")
    df.to_csv(out, index=False)
    print(f"  ✓ target resolved to '{panels['target_key']}'; "
          f"identity anchor = {panels['identity_anchor']}")
    print(f"  ✓ off-target lineages: {list(panels['offtarget'])}")
    print(f"  ✓ marker panels -> {out}")


if __name__ == "__main__":
    # smoke test with a minimal fake cfg (no data)
    cfg = {"species": "human", "engineering": "MSLN-CAR",
           "modules": {"A": True, "B": True, "C": True, "D": True, "E": True},
           "dirs": {"tables": "/tmp"}}
    p = resolve_panels(cfg, "NK cell", "ipsc")
    assert p["identity_anchor"], "identity anchor should be non-empty for NK"
    assert p["maturity"]["has_axis"], "NK should have a maturity axis"
    print("✓ derive_marker_panels smoke test passed")
