"""
Tier-2 synthesizability: full CASP retrosynthesis with AiZynthFinder, with
graceful degradation.

Design contract (important for locked-down / no-egress sandboxes):
  * The ~750 MB USPTO models + ZINC stock are PROVISIONED AT SETUP, not lazily in
    the user's first request path. Use `provision_models()` once (or the
    `download_public_data` CLI at install time) to populate a PERSISTENT cache
    (default /mnt/shared-workspace/aizynth_models/ so it survives machine
    hibernation/restart).
  * `run_retrosynthesis()` NEVER downloads and NEVER crashes the pipeline. If the
    models are missing OR egress is blocked (the classic GitHub/external 403),
    it returns a "skipped" result with a clear reason. The caller then falls back
    to the Tier-1 SA_Score proxy and the report states retrosynthesis was
    unavailable.

Tier-1 (SA_Score) is always computed upstream in scoring.py; this module is the
optional heavy stage that runs only on the top-N designs.
"""
from __future__ import annotations
import json
import os
import time
from typing import Dict, List, Optional

import pandas as pd

DEFAULT_CACHE = "/mnt/shared-workspace/aizynth_models"

# Files AiZynthFinder's download_public_data writes; used to check completeness.
_REQUIRED_FILES = [
    "config.yml",
    "uspto_model.onnx",
    "uspto_templates.csv.gz",
    "zinc_stock.hdf5",
]


def models_available(cache_dir: str = DEFAULT_CACHE) -> bool:
    """True only if a config + the core USPTO/ZINC files are present."""
    if not os.path.isdir(cache_dir):
        return False
    return all(os.path.exists(os.path.join(cache_dir, f)) for f in _REQUIRED_FILES)


def normalize_config_paths(cache_dir: str = DEFAULT_CACHE) -> None:
    """Rewrite config.yml so every model path points inside `cache_dir`.

    `download_public_data` bakes absolute paths into config.yml. If the cache is
    later copied/moved (e.g. from /workspace to a persistent share), those paths
    break. This makes the cache self-healing: call it after provisioning or a
    move so AiZynthFinder finds the files wherever they now live."""
    import re
    cfg = os.path.join(cache_dir, "config.yml")
    if not os.path.exists(cfg):
        return
    s = open(cfg).read()
    # Point any '.../<file>' reference at the current cache_dir by basename.
    for fname in ["uspto_model.onnx", "uspto_templates.csv.gz",
                  "uspto_ringbreaker_model.onnx", "uspto_ringbreaker_templates.csv.gz",
                  "uspto_filter_model.onnx", "zinc_stock.hdf5"]:
        s = re.sub(r"[^\s:]*" + re.escape(fname),
                   os.path.join(cache_dir, fname), s)
    open(cfg, "w").write(s)


def provision_models(cache_dir: str = DEFAULT_CACHE, timeout_s: int = 1800) -> Dict:
    """Download the public USPTO models + ZINC stock ONCE into a persistent cache.

    Call this at skill setup / install time -- NOT inside an interactive request.
    Returns {'ok': bool, 'reason': str, 'cache_dir': str}. Never raises on network
    failure; reports the failure so setup can surface it."""
    if models_available(cache_dir):
        return {"ok": True, "reason": "already_present", "cache_dir": cache_dir}
    os.makedirs(cache_dir, exist_ok=True)
    try:
        import subprocess
        # `download_public_data <dir>` fetches models + writes a config.yml.
        proc = subprocess.run(
            ["download_public_data", cache_dir],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if proc.returncode != 0:
            return {"ok": False,
                    "reason": f"download_public_data failed: {proc.stderr[-300:]}",
                    "cache_dir": cache_dir}
    except FileNotFoundError:
        return {"ok": False, "reason": "download_public_data CLI not found "
                "(is aizynthfinder installed?)", "cache_dir": cache_dir}
    except Exception as e:  # timeouts, blocked egress, etc.
        return {"ok": False, "reason": f"provisioning error: {str(e)[:200]}",
                "cache_dir": cache_dir}
    ok = models_available(cache_dir)
    if ok:
        normalize_config_paths(cache_dir)  # make paths portable
    return {"ok": ok,
            "reason": "provisioned" if ok else "download ran but files incomplete",
            "cache_dir": cache_dir}


def _skipped(designs: pd.DataFrame, reason: str, out_data_dir: str) -> pd.DataFrame:
    """Build a 'skipped' summary (one row per design) and persist it."""
    rows = []
    for _, r in designs.iterrows():
        rows.append({
            "design_id": r["design_id"], "smiles": r["smiles"],
            "retro_status": "skipped", "skip_reason": reason,
            "is_solved": False, "n_routes": 0, "top_score": 0.0,
            "n_steps_best": 0, "n_precursors_best": 0,
            "n_precursors_in_stock": 0, "search_time_s": 0.0,
        })
    df = pd.DataFrame(rows)
    if out_data_dir:
        os.makedirs(out_data_dir, exist_ok=True)
        df.to_csv(os.path.join(out_data_dir, "retrosynthesis_summary.csv"), index=False)
    return df


def run_retrosynthesis(designs: pd.DataFrame, out_data_dir: str,
                       cache_dir: str = DEFAULT_CACHE,
                       time_limit: int = 120, iteration_limit: int = 200,
                       id_col: str = "design_id", smiles_col: str = "smiles"
                       ) -> pd.DataFrame:
    """Run CASP retrosynthesis on `designs`, or skip cleanly if unavailable.

    Returns a summary DataFrame. Also writes route_<id>.json (best route) per
    solved/partial design and retrosynthesis_summary.csv. NEVER raises on missing
    models or a blocked network -- returns a 'skipped' summary instead."""
    # Graceful degradation gate #1: models present?
    if not models_available(cache_dir):
        reason = (f"AiZynthFinder models not found in {cache_dir}; "
                  "run provision_models() at setup. Falling back to SA_Score proxy.")
        print(f"[retro] SKIPPED: {reason}", flush=True)
        return _skipped(designs, reason, out_data_dir)

    # Graceful degradation gate #2: library import / config load failures.
    try:
        normalize_config_paths(cache_dir)  # self-heal paths if cache was moved
        from aizynthfinder.aizynthfinder import AiZynthFinder
        finder = AiZynthFinder(configfile=os.path.join(cache_dir, "config.yml"))
        finder.stock.select("zinc")
        finder.expansion_policy.select("uspto")
        finder.filter_policy.select("uspto")
        finder.config.search.time_limit = time_limit
        finder.config.search.iteration_limit = iteration_limit
    except Exception as e:
        reason = f"AiZynthFinder init failed ({str(e)[:150]}); using SA_Score proxy."
        print(f"[retro] SKIPPED: {reason}", flush=True)
        return _skipped(designs, reason, out_data_dir)

    os.makedirs(out_data_dir, exist_ok=True)
    results: List[Dict] = []
    for _, row in designs.iterrows():
        smi, did = row[smiles_col], row[id_col]
        t0 = time.time()
        try:
            finder.target_smiles = smi
            finder.tree_search()
            finder.build_routes()
            stats = finder.extract_statistics()
            route_dicts = finder.routes.dicts
            rec = {
                "design_id": did, "smiles": smi, "retro_status": "ran",
                "is_solved": bool(stats.get("is_solved", False)),
                "n_routes": int(len(finder.routes)),
                "top_score": round(float(stats.get("top_score", 0.0)), 4),
                "n_steps_best": int(stats.get("number_of_steps", 0)),
                "n_precursors_best": int(stats.get("number_of_precursors", 0)),
                "n_precursors_in_stock": int(stats.get("number_of_precursors_in_stock", 0)),
                "search_time_s": round(time.time() - t0, 1),
            }
            if route_dicts:
                with open(os.path.join(out_data_dir, f"route_{did}.json"), "w") as f:
                    json.dump(route_dicts[0], f)
            results.append(rec)
            print(f"  {did}: solved={rec['is_solved']} steps={rec['n_steps_best']} "
                  f"score={rec['top_score']} ({rec['search_time_s']}s)", flush=True)
        except Exception as e:
            # A single-molecule failure must not abort the batch.
            results.append({
                "design_id": did, "smiles": smi, "retro_status": "error",
                "is_solved": False, "n_routes": 0, "top_score": 0.0,
                "n_steps_best": 0, "n_precursors_best": 0,
                "n_precursors_in_stock": 0, "search_time_s": round(time.time() - t0, 1),
                "error": str(e)[:150],
            })
            print(f"  {did}: ERROR {str(e)[:80]}", flush=True)

    df = pd.DataFrame(results)
    df.to_csv(os.path.join(out_data_dir, "retrosynthesis_summary.csv"), index=False)
    n_solved = int(df["is_solved"].sum())
    print(f"[retro] done: {n_solved}/{len(df)} designs solved", flush=True)
    return df


def render_route(route_json_path: str, out_png: str) -> bool:
    """Render a saved best-route tree to PNG. Returns False on failure (non-fatal)."""
    try:
        from aizynthfinder.reactiontree import ReactionTree
        with open(route_json_path) as f:
            route = json.load(f)
        rt = ReactionTree.from_dict(route)
        img = rt.to_image(show_all=True)
        img.save(out_png)
        return True
    except Exception as e:
        print(f"[retro] route render failed for {route_json_path}: {str(e)[:100]}",
              flush=True)
        return False
