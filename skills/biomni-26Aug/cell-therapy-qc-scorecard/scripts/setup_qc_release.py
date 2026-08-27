"""
============================================================================
SETUP & CONFIG  —  cell-therapy scRNA-seq QC release scorecard
============================================================================

Builds the run configuration from a free-text product description, resolves
the adaptive module set, makes sibling-skill scripts importable, and creates
the output directory tree under /mnt/results/<run_name>/.

Functions
  - infer_source(product)      : guess ipsc / esc / primary from description
  - build_config(...)          : assemble & validate the run config dict
  - make_siblings_importable() : add scrnaseq-scanpy-core-analysis scripts to sys.path
  - ensure_output_tree(cfg)    : create /mnt/results/<run_name>/{tables,figures,h5ad}

Usage
  from setup_qc_release import build_config
  cfg = build_config(product="iPSC-derived NK cell (MSLN-CAR)",
                     target_cell="NK cell", source="ipsc",
                     inputs=["GSE291599"], run_name="ipsc_nk_qc")
"""

import os
import re
import sys
import glob
import json
from typing import Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# Locate sibling skills so their scripts can be imported / read.
# We search common skill roots rather than hard-coding a single path, because
# skills may be mounted read-only under /mnt/skills or staged under /mnt/results.
# ---------------------------------------------------------------------------
SKILL_ROOTS = [
    "/mnt/skills/system", "/mnt/skills/user", "/mnt/skills",
    "/mnt/results/skills",
]


def _find_skill_dir(skill_name: str) -> Optional[str]:
    for root in SKILL_ROOTS:
        cand = os.path.join(root, skill_name)
        if os.path.isdir(cand):
            return cand
    # fall back to a shallow glob
    for root in SKILL_ROOTS:
        hits = glob.glob(os.path.join(root, "*", skill_name))
        if hits:
            return hits[0]
    return None


def make_siblings_importable(verbose: bool = True) -> Dict[str, Optional[str]]:
    """Add the scanpy-core (and this) skill scripts dirs to sys.path.

    Returns a dict of {skill_name: scripts_dir or None}.
    """
    found = {}
    for skill in ["scrnaseq-scanpy-core-analysis", "cell-therapy-qc-scorecard"]:
        d = _find_skill_dir(skill)
        scripts = os.path.join(d, "scripts") if d else None
        if scripts and os.path.isdir(scripts) and scripts not in sys.path:
            sys.path.insert(0, scripts)
        found[skill] = scripts if (scripts and os.path.isdir(scripts)) else None
        if verbose:
            status = "✓" if found[skill] else "✗ (not found — reuse steps will fail)"
            print(f"  {status} {skill}: {found[skill]}")
    return found


def infer_source(product: str) -> str:
    """Guess the cell source from a free-text product description."""
    p = (product or "").lower()
    if re.search(r"\bipsc|\bips cell|induced pluripotent|reprogrammed|\bi[nptm][a-z]*-derived|\bi(nk|t|macrophage|cardiomyocyte|hepatocyte|neuron|beta)\b", p):
        return "ipsc"
    if re.search(r"\besc|embryonic stem|\bhesc\b", p):
        return "esc"
    return "primary"


# Default GREEN/AMBER/RED thresholds. See references/thresholds_defaults.md.
# Each entry: (green_edge, red_edge) with direction implied by the module.
DEFAULT_THRESHOLDS = {
    # purity: higher is better -> GREEN if >= green, RED if < red
    "purity_pct":        {"green": 90.0, "red": 75.0, "direction": "high_good"},
    # residual pluripotency: lower is better -> GREEN if < green, RED if > red
    "resid_pluri_pct":   {"green": 0.01, "red": 0.10, "direction": "low_good"},
    # off-target: lower is better
    "offtarget_pct":     {"green": 2.0,  "red": 10.0, "direction": "low_good"},
    # maturity: higher is better
    "maturity_pct":      {"green": 60.0, "red": 40.0, "direction": "high_good"},
    # technical QC composite pieces (all low_good except retention)
    "retention_pct":     {"green": 80.0, "red": 60.0, "direction": "high_good"},
    "species_contam_pct":{"green": 1.0,  "red": 5.0,  "direction": "low_good"},
    "mito_pct":          {"green": 10.0, "red": 20.0, "direction": "low_good"},
    # contamination (true non-target contaminants, target-negative)
    "contam_pct":        {"green": 1.0,  "red": 5.0,  "direction": "low_good"},
}


def build_config(
    product: str,
    target_cell: str,
    inputs: Union[str, List[str]],
    source: Optional[str] = None,
    species: str = "human",
    engineering: Optional[str] = None,
    multispecies: Optional[bool] = None,
    keep_species_frac: float = 0.9,
    unit_metadata: Optional[str] = None,
    modules: Optional[Dict[str, bool]] = None,
    thresholds: Optional[Dict[str, dict]] = None,
    run_name: str = "cell_therapy_qc_scorecard",
    results_root: str = "/mnt/results",
    verbose: bool = True,
) -> Dict:
    """Assemble and validate the run config. See module docstring for fields."""
    if source is None:
        source = infer_source(product)
    if source not in ("ipsc", "esc", "primary"):
        raise ValueError("source must be one of ipsc / esc / primary")
    if species not in ("human", "mouse"):
        raise ValueError("species must be 'human' or 'mouse'")

    if isinstance(inputs, str):
        inputs = [inputs]
    # detect GEO accession(s)
    is_geo = all(re.fullmatch(r"GSE\d+", str(x).strip()) for x in inputs) and len(inputs) >= 1

    # Adaptive module defaults:
    #   A identity, C off-target, E tech-QC  -> always
    #   B pluripotency -> iPSC/ESC only
    #   D maturity     -> resolved later against the product-type registry;
    #                     default True here, derive_marker_panels may set False.
    auto_modules = {
        "A": True,
        "B": source in ("ipsc", "esc"),
        "C": True,
        "D": True,   # may be turned off in Step 3 if no maturity axis is defined
        "E": True,
    }
    if modules:
        auto_modules.update({k: bool(v) for k, v in modules.items()})

    thr = json.loads(json.dumps(DEFAULT_THRESHOLDS))  # deep copy
    if thresholds:
        for k, v in thresholds.items():
            thr.setdefault(k, {}).update(v)

    if multispecies is None:
        multispecies = False  # user/Step-2 can flip; safe default

    outdir = os.path.join(results_root, run_name)
    cfg = {
        "product": product,
        "target_cell": target_cell,
        "source": source,
        "engineering": engineering,
        "species": species,
        "multispecies": bool(multispecies),
        "keep_species_frac": float(keep_species_frac),
        "inputs": inputs,
        "is_geo": bool(is_geo),
        "unit_metadata": unit_metadata,
        "modules": auto_modules,
        "thresholds": thr,
        "run_name": run_name,
        "outdir": outdir,
        "dirs": {
            "tables": os.path.join(outdir, "tables"),
            "figures": os.path.join(outdir, "figures"),
            "h5ad": os.path.join(outdir, "h5ad"),
        },
    }

    ensure_output_tree(cfg)
    if verbose:
        print("=== QC release config ===")
        print(f"  product      : {product}")
        print(f"  target_cell  : {target_cell}")
        print(f"  source       : {source}  (Module B pluripotency: {'ON' if auto_modules['B'] else 'OFF'})")
        print(f"  species      : {species}  (multispecies: {cfg['multispecies']})")
        print(f"  inputs       : {'GEO ' if is_geo else ''}{inputs}")
        print(f"  active mods  : {[m for m,on in auto_modules.items() if on]}")
        print(f"  outdir       : {outdir}")
        print("  (sibling skills)")
        make_siblings_importable(verbose=True)
        print("✓ config built")
    else:
        make_siblings_importable(verbose=False)
    return cfg


def ensure_output_tree(cfg: Dict) -> None:
    os.makedirs(cfg["outdir"], exist_ok=True)
    for d in cfg["dirs"].values():
        os.makedirs(d, exist_ok=True)


def save_config(cfg: Dict) -> str:
    """Persist the config as JSON for auditability."""
    path = os.path.join(cfg["outdir"], "run_config.json")
    with open(path, "w") as f:
        json.dump({k: v for k, v in cfg.items()}, f, indent=2, default=str)
    print(f"✓ config saved -> {path}")
    return path


if __name__ == "__main__":
    # smoke test (no data touched)
    cfg = build_config(
        product="iPSC-derived NK cell (MSLN-CAR)",
        target_cell="NK cell",
        inputs=["GSE291599"],
        run_name="_smoke_qc_release",
    )
    save_config(cfg)
    assert cfg["source"] == "ipsc"
    assert cfg["modules"]["B"] is True
    print("✓ setup_qc_release smoke test passed")
