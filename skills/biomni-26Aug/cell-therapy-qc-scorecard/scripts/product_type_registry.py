"""
============================================================================
PRODUCT TYPE REGISTRY  —  cell-therapy scRNA-seq QC release scorecard
============================================================================

Curated marker panels for common cell-therapy target cell types, plus the
shared pluripotency, off-target, and proliferation panels. This is the primary
(fast, validated) source used by derive_marker_panels.resolve_panels(); gaps
fall back to CellMarker2 and LiteratureSearch.

Each TARGET_REGISTRY entry:
  identity_anchor    : small set of RAW-expression identity markers (Module A anchor)
  fidelity_lineages  : off-lineage programs used to grade target-cell fidelity (Module A)
  offtarget_exclude  : global off-target lineages to DROP for this target (they ARE the target)
  maturity           : {mature:[...], immature:[...]} axis (Module D); {} if none

Human gene symbols. For mouse, CellMarker2 mouse lookup / title-case is used at
runtime; the anchor logic is symbol-agnostic.

Provenance for the NK panels and the pluripotency/off-target logic is the
validated iPSC-NK reference run; see references/qc_release_methodology.md and
references/marker_panel_sources.md.
"""

# ---------------------------------------------------------------------------
# Shared pluripotency panels (Module B)  — specificity matters (caveat #2)
# ---------------------------------------------------------------------------
PLURIPOTENCY_PANELS = {
    # broad core set (some members, e.g. POU5F1/DNMT3B, are non-specific alone)
    "core": ["POU5F1", "NANOG", "LIN28A", "TDGF1", "DPPA4", "SALL4",
             "ZSCAN10", "DNMT3B", "PRDM14", "UTF1"],
    # SPECIFIC TFs: co-expression of these is strong residual-iPSC evidence
    "specific": ["NANOG", "LIN28A", "TDGF1", "PRDM14", "UTF1", "ZSCAN10", "SALL4"],
    # canonical triad — count of cells co-expressing all three is the strongest signal
    "triad": ["POU5F1", "NANOG", "LIN28A"],
}

# ---------------------------------------------------------------------------
# Shared off-target lineage panels (Module C)
# ---------------------------------------------------------------------------
OFFTARGET_PANELS = {
    "fibroblast": ["COL1A1", "COL1A2", "COL3A1", "DCN", "LUM"],
    "epithelial": ["EPCAM", "KRT8", "KRT18", "KRT19"],
    "endothelial": ["PECAM1", "CDH5", "VWF", "KDR"],
    "hepatic": ["ALB", "AFP", "TTR"],
    "neural": ["MAP2", "RBFOX3", "TUBB3", "STMN2"],
    "cardiac": ["TNNT2", "MYH6", "ACTC1"],
    "myeloid": ["LYZ", "CD14", "FCN1", "CST3", "CSF1R"],
    "erythroid": ["HBB", "HBA1", "HBA2", "GYPA"],
    "Tcell": ["CD3D", "CD3E", "CD3G", "TRAC", "CD8A"],
    "Bcell": ["CD19", "MS4A1", "CD79A", "CD79B", "IGHM"],
}

# ---------------------------------------------------------------------------
# Proliferation (Module D helper / pluripotency confound)
# ---------------------------------------------------------------------------
PROLIFERATION_MARKERS = ["MKI67", "TOP2A", "CENPF", "CCNB1", "UBE2C"]


# ---------------------------------------------------------------------------
# Target-cell registry
# ---------------------------------------------------------------------------
TARGET_REGISTRY = {
    "nk": {
        "aliases": ["nk", "nk cell", "natural killer", "ink", "inK", "car-nk", "car nk"],
        # pure cytotoxic anchor (NO score gate); validated on iPSC-NK reference
        "identity_anchor": ["GNLY", "NKG7", "KLRD1", "NCR1", "KLRF1", "PRF1", "GZMB"],
        "fidelity_lineages": {
            "Tcell": ["CD3D", "CD3E", "CD3G", "TRAC", "TRBC1", "TRBC2", "CD8A", "CD8B"],
            "Bcell": ["CD19", "MS4A1", "CD79A", "CD79B", "IGHM"],
            "myeloid": ["LYZ", "CD14", "FCN1", "CST3", "CSF1R"],
            "erythroid": ["HBB", "HBA1", "HBA2", "GYPA"],
        },
        "offtarget_exclude": [],  # NK's own lineage not in OFFTARGET_PANELS as such
        "maturity": {
            "mature": ["KLRD1", "NKG7", "GNLY", "PRF1", "GZMB", "GZMH", "FCGR3A",
                       "KLRF1", "CX3CR1", "ZEB2", "FGFBP2", "S1PR5"],
            "immature": ["SELL", "IL7R", "XCL1", "XCL2", "GZMK", "KIT", "TCF7",
                         "CD44", "GPR183"],
        },
    },
    "tcell": {
        "aliases": ["t cell", "tcell", "cd8 t", "cd4 t", "car-t", "car t", "cart",
                    "tcr-t", "it cell", "cytotoxic t"],
        "identity_anchor": ["CD3D", "CD3E", "CD3G", "TRAC", "CD8A", "CD8B", "CD4"],
        "fidelity_lineages": {
            "NK": ["GNLY", "NKG7", "KLRF1", "NCR1"],
            "Bcell": ["CD19", "MS4A1", "CD79A", "IGHM"],
            "myeloid": ["LYZ", "CD14", "FCN1", "CST3"],
        },
        "offtarget_exclude": ["Tcell"],
        "maturity": {
            "mature": ["CCL5", "GZMB", "GZMH", "PRF1", "GNLY", "FGFBP2", "FCGR3A",
                       "KLRG1", "CX3CR1", "TBX21"],
            "immature": ["CCR7", "SELL", "TCF7", "LEF1", "IL7R", "CD27", "CD28"],
        },
    },
    "macrophage": {
        "aliases": ["macrophage", "imac", "imacrophage", "car-m", "car macrophage",
                    "monocyte", "myeloid"],
        "identity_anchor": ["CD68", "CD14", "LYZ", "CSF1R", "FCGR3A", "MRC1", "ITGAM"],
        "fidelity_lineages": {
            "Tcell": ["CD3D", "CD3E", "TRAC"],
            "Bcell": ["CD19", "MS4A1", "CD79A"],
            "NK": ["GNLY", "NKG7", "KLRF1"],
        },
        "offtarget_exclude": ["myeloid"],
        "maturity": {
            "mature": ["MRC1", "CD163", "MERTK", "C1QA", "C1QB", "APOE", "MARCO"],
            "immature": ["S100A8", "S100A9", "FCN1", "VCAN", "PLAC8"],
        },
    },
    "cardiomyocyte": {
        "aliases": ["cardiomyocyte", "cardiac", "icardiomyocyte", "cm", "heart muscle"],
        "identity_anchor": ["TNNT2", "MYH6", "MYH7", "ACTC1", "TNNI3", "NPPA", "MYL7"],
        "fidelity_lineages": {
            "fibroblast": ["COL1A1", "COL1A2", "DCN", "LUM"],
            "endothelial": ["PECAM1", "CDH5", "VWF"],
            "epithelial": ["EPCAM", "KRT8", "KRT18"],
        },
        "offtarget_exclude": ["cardiac"],
        "maturity": {
            "mature": ["MYH7", "TNNI3", "MYL2", "PLN", "MYOM2", "CKM"],
            "immature": ["MYH6", "TNNI1", "MYL7", "NPPA", "ACTC1", "MKI67"],
        },
    },
    "hepatocyte": {
        "aliases": ["hepatocyte", "liver", "ihepatocyte", "hlc", "hepatic"],
        "identity_anchor": ["ALB", "TTR", "APOA1", "APOB", "SERPINA1", "TF", "CYP3A4"],
        "fidelity_lineages": {
            "fibroblast": ["COL1A1", "DCN", "LUM"],
            "endothelial": ["PECAM1", "CDH5"],
            "epithelial": ["KRT19", "EPCAM"],
        },
        "offtarget_exclude": ["hepatic"],
        "maturity": {
            "mature": ["CYP3A4", "CYP2E1", "ALB", "ASGR1", "G6PC", "APOB"],
            "immature": ["AFP", "KRT19", "DLK1", "SOX9", "MKI67"],
        },
    },
    "beta_cell": {
        "aliases": ["beta cell", "beta-cell", "islet", "ibeta", "pancreatic beta",
                    "sc-beta", "insulin-producing"],
        "identity_anchor": ["INS", "IAPP", "PDX1", "NKX6-1", "MAFA", "CHGA", "CHGB"],
        "fidelity_lineages": {
            "alpha": ["GCG", "ARX"], "delta": ["SST"], "acinar": ["PRSS1", "CPA1"],
            "ductal": ["KRT19", "SPP1"],
        },
        "offtarget_exclude": [],
        "maturity": {
            "mature": ["MAFA", "UCN3", "SIX2", "INS", "G6PC2"],
            "immature": ["MAFB", "NEUROG3", "LDHA", "MKI67", "SOX9"],
        },
    },
    "neuron": {
        "aliases": ["neuron", "ineuron", "cortical neuron", "dopaminergic",
                    "motor neuron", "neural"],
        "identity_anchor": ["MAP2", "RBFOX3", "TUBB3", "SYN1", "SNAP25", "STMN2", "NEFL"],
        "fidelity_lineages": {
            "astrocyte": ["GFAP", "AQP4", "S100B"],
            "oligodendrocyte": ["OLIG1", "OLIG2", "MBP"],
            "fibroblast": ["COL1A1", "DCN"],
        },
        "offtarget_exclude": ["neural"],
        "maturity": {
            "mature": ["RBFOX3", "SYN1", "SNAP25", "GRIA1", "DLG4", "MAP2"],
            "immature": ["NES", "SOX2", "DCX", "PAX6", "MKI67", "VIM"],
        },
    },
    "msc": {
        "aliases": ["msc", "mesenchymal", "stromal", "imsc"],
        "identity_anchor": ["THY1", "NT5E", "ENG", "PDGFRB", "COL1A1", "DCN", "LUM"],
        "fidelity_lineages": {
            "endothelial": ["PECAM1", "CDH5"], "epithelial": ["EPCAM", "KRT8"],
            "hematopoietic": ["PTPRC", "CD34"],
        },
        "offtarget_exclude": ["fibroblast"],
        "maturity": {},   # no standard immature/mature axis -> Module D off
    },
    "rpe": {
        "aliases": ["rpe", "retinal pigment", "irpe", "retinal pigment epithelium"],
        "identity_anchor": ["RPE65", "BEST1", "MITF", "TYR", "PMEL", "TTR", "RLBP1"],
        "fidelity_lineages": {"fibroblast": ["COL1A1", "DCN"],
                              "endothelial": ["PECAM1", "CDH5"]},
        "offtarget_exclude": ["epithelial"],
        "maturity": {},
    },
}


STOPWORDS = {"cell", "cells", "the", "a", "an", "derived", "ipsc", "esc", "car",
             "primary", "human", "mouse", "engineered", "product", "lot", "sample"}


def _norm_tokens(s: str):
    """Lowercase, split on non-alphanumerics, drop generic stopwords."""
    import re
    toks = re.split(r"[^a-z0-9]+", s.lower())
    return [w for w in toks if w and w not in STOPWORDS]


def resolve_target_key(target_cell: str) -> str:
    """Map a free-text target-cell name to a registry key (or '' if unknown).

    Uses WORD-level matching (not raw substring) so short aliases like 'nk' do not
    spuriously match inside unrelated words (e.g. 'u-nk-nown'). Unknown targets
    return '' so the CellMarker2 / literature fallback runs.
    """
    t = (target_cell or "").strip().lower()
    if not t:
        return ""
    q_tokens = _norm_tokens(t)
    q_set = set(q_tokens)
    q_join = " ".join(q_tokens)

    # Stage 1: exact key or exact-alias (word-normalized) match
    for key, entry in TARGET_REGISTRY.items():
        if t == key:
            return key
        for a in entry.get("aliases", []):
            a_tokens = _norm_tokens(a)
            if not a_tokens:
                continue
            a_join = " ".join(a_tokens)
            # exact normalized alias, or alias phrase appears as a token-subsequence
            if a_join == q_join or a_join in q_join.split("  "):
                return key
            # all alias tokens present as whole words in the query (e.g. "car t" ⊆ "car t")
            if set(a_tokens) <= q_set:
                return key
    # Stage 2: single-token alias appears as a whole word in the query
    for key, entry in TARGET_REGISTRY.items():
        for a in entry.get("aliases", []):
            a_tokens = _norm_tokens(a)
            if len(a_tokens) == 1 and a_tokens[0] in q_set:
                return key
    return ""


if __name__ == "__main__":
    cases = {
        "NK cell": "nk", "iPSC-NK": "nk", "iPSC-derived NK cell": "nk",
        "CAR-NK": "nk", "natural killer cell": "nk",
        "CAR-T": "tcell", "CD8 T cell": "tcell", "TCR-T product": "tcell",
        "cardiomyocyte": "cardiomyocyte", "iPSC cardiomyocyte": "cardiomyocyte",
        "hepatocyte": "hepatocyte", "sc-beta cell": "beta_cell",
        "pancreatic beta cell": "beta_cell", "cortical neuron": "neuron",
        "iMSC": "msc", "mesenchymal stromal cell": "msc", "RPE": "rpe",
        "iPSC-macrophage": "macrophage", "CAR-M": "macrophage",
        # negatives — must be '' so CellMarker2/literature fallback runs
        "unknown widget cell": "", "chondrocyte": "", "keratinocyte": "",
        "": "",
    }
    ok = True
    for q, exp in cases.items():
        got = resolve_target_key(q)
        flag = "✓" if got == exp else "✗"
        if got != exp:
            ok = False
        print(f"  {flag} {q!r:30s} -> {got!r:14s} (expected {exp!r})")
    assert ok, "one or more resolver cases failed"
    print("✓ product_type_registry smoke test passed")
