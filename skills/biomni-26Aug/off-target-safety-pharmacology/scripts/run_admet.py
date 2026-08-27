#!/usr/bin/env python3
"""
run_admet.py — ADMET / physicochemical profiling with a documented engine hierarchy.

Engine order (STAMPED in output so downstream reporting cannot conflate them):
  1) ADMET-AI (Swanson 2024)     -> ~98 endpoints + ChEMBL-reference PERCENTILES (CC BY-SA) + hERG.
                                    Preferred: percentiles let us flag e.g. hERG in the
                                    99th+ percentile of approved drugs. SELF-PROVISIONING:
                                    if admet_ai is not installed, this script installs it
                                    once with a PINNED recipe that preserves the baked torch
                                    stack, then retries. If PyPI egress is blocked or the
                                    install/import/predict fails, we fall back (see 2).
  2) Biomni predict_admet_properties (MPNN) -> ADMET endpoints WITHOUT DrugBank percentiles.
                                    Same TDC/Chemprop lineage; different, smaller endpoint set
                                    and NO percentile context. Load-bearing fallback.

Self-provisioning detail (try_admet_ai): pinned install is
  torch==2.7.1 torchvision==0.22.1 "numpy<2" admet_ai
(-> admet-ai==1.3.1 + chemprop==1.6.1, torch untouched), preferring `uv pip install`
with a `python -m pip install` fallback. A torch.load(weights_only=False) shim is applied
before ADMETModel() because torch>=2.6 defaults it to True and breaks chemprop 1.6.1
checkpoints. All best-effort: any failure yields the MPNN fallback with correct stamping.

Whichever runs, we write `engine` + `has_percentiles` into admet_meta.json. The report
MUST read that and describe endpoints/percentiles only if the engine actually produced them.

Usage:
  python run_admet.py --smiles "<smi>" --outdir <dir> [--engine auto|admet_ai|biomni]

Outputs:
  <outdir>/data/admet_all_properties.csv   (long: property,value[,percentile])
  <outdir>/data/admet_meta.json            (engine, n_endpoints, has_percentiles, key flags)
"""
import argparse, os, sys, json, importlib, subprocess
import pandas as pd

# Pinned install that KEEPS the baked torch stack (torch==2.7.1 / torchvision==0.22.1).
# A naive `pip install admet_ai` pulls torch 2.13 and breaks the import; these pins
# resolve to admet-ai==1.3.1 + chemprop==1.6.1 with torch untouched. numpy must stay
# <2.0 (numpy 2.x breaks pandas + chemprop 1.6.1). No URLs beyond PyPI, no secrets.
_ADMET_AI_PINS = ["torch==2.7.1", "torchvision==0.22.1", "numpy<2", "admet_ai"]
# Substrings that signal a blocked-egress / network failure (vs. a resolver error).
_EGRESS_MARKERS = ("403", "err_access_denied", "access denied", "tunnel", "failed to fetch",
                   "temporary failure in name resolution", "connection", "timed out",
                   "could not resolve", "network is unreachable", "proxy")


def _admet_ai_importable():
    try:
        importlib.import_module("admet_ai")
        return True
    except Exception:
        return False


def _admet_ai_distribution_present():
    """True if the admet_ai distribution is installed on disk, WITHOUT importing it.

    Uses importlib.metadata so we don't trigger numpy/pandas imports (which can be in a
    transient broken state right after an in-process numpy downgrade)."""
    try:
        import importlib.metadata as _md
    except Exception:  # pragma: no cover
        import importlib_metadata as _md  # type: ignore
    for dist in ("admet_ai", "admet-ai"):
        try:
            _md.version(dist)
            return True
        except Exception:
            continue
    return False


def _pip_install_admet_ai():
    """Idempotently install ADMET-AI with pinned deps into the RUNNING interpreter's env.

    Prefers `uv pip install` (fast), falls back to `python -m pip install`. Returns True
    on success (admet_ai importable afterwards), False otherwise. Never raises."""
    if _admet_ai_importable():
        print("[admet] ADMET-AI already importable; skipping install.", file=sys.stderr)
        return True

    # Command variants, in order of preference. `--python sys.executable` makes uv target
    # THIS interpreter's environment rather than a default/discovered venv.
    attempts = [
        ["uv", "pip", "install", "--python", sys.executable, *_ADMET_AI_PINS],
        [sys.executable, "-m", "pip", "install", *_ADMET_AI_PINS],
    ]
    for cmd in attempts:
        tool = "uv" if cmd[0] == "uv" else "pip"
        print(f"[admet] ADMET-AI not installed; installing via {tool}: "
              f"{' '.join(_ADMET_AI_PINS)}", file=sys.stderr)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except FileNotFoundError:
            print(f"[admet] {tool} not found; trying next installer.", file=sys.stderr)
            continue
        except Exception as e:
            print(f"[admet] {tool} install raised ({e}); trying next installer.",
                  file=sys.stderr)
            continue
        if r.returncode == 0:
            # NOTE: we deliberately do NOT test `import admet_ai` here. The pinned
            # install downgrades numpy to <2, but numpy 2.x is already imported in THIS
            # process, so an in-process import fails ("No module named numpy.rec") even
            # though the install is fine on disk. Verify via installed-distribution
            # metadata (no import needed); the caller then re-exec's so a clean child
            # process does the real import.
            importlib.invalidate_caches()
            if _admet_ai_distribution_present():
                print(f"[admet] ADMET-AI installed successfully via {tool} "
                      "(on-disk); will re-exec to load it cleanly.", file=sys.stderr)
                return True
            print(f"[admet] {tool} exited 0 but admet_ai distribution not found; "
                  "trying next installer.", file=sys.stderr)
            continue
        tail = ((r.stderr or "") + (r.stdout or ""))[-600:]
        low = tail.lower()
        if any(m in low for m in _EGRESS_MARKERS):
            print(f"[admet] {tool} install failed \u2014 looks like blocked PyPI egress; "
                  f"will fall back to MPNN.\n{tail}", file=sys.stderr)
        else:
            print(f"[admet] {tool} install failed (exit {r.returncode}); "
                  f"trying next installer.\n{tail}", file=sys.stderr)
    # Last resort: maybe it is importable/present despite the above.
    return _admet_ai_distribution_present()


def _apply_torch_load_shim():
    """torch>=2.6 defaults torch.load(weights_only=True), which fails on chemprop 1.6.1
    checkpoints. Default it back to False (idempotently) before building ADMETModel()."""
    try:
        import torch
    except Exception:
        return
    if getattr(torch.load, "_admet_wo_shim", False):
        return
    _orig_load = torch.load

    def _patched_load(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return _orig_load(*args, **kwargs)

    _patched_load._admet_wo_shim = True
    torch.load = _patched_load
    print("[admet] Applied torch.load(weights_only=False) shim for chemprop checkpoints.",
          file=sys.stderr)


def _reexec_once():
    """Re-exec this interpreter a single time (guarded by an env flag).

    The pinned install downgrades numpy to <2, but numpy 2.x is already imported in this
    process (via pandas at module load), so admet_ai then fails with 'No module named
    numpy.rec'. numpy cannot be swapped in-process, so after a FRESH install we re-exec
    with the same argv; the child starts clean on the newly-installed numpy 1.26."""
    if os.environ.get("ADMET_SELFPROVISION_REEXECED") == "1":
        return  # already re-exec'd once; do not loop
    os.environ["ADMET_SELFPROVISION_REEXECED"] = "1"
    print("[admet] Re-exec'ing interpreter once to load freshly-installed deps "
          "(numpy<2).", file=sys.stderr)
    sys.stdout.flush(); sys.stderr.flush()
    os.execv(sys.executable, [sys.executable] + sys.argv)


def try_admet_ai(smiles):
    """Return (df_long, meta) or (None, None) if ADMET-AI unavailable/failing.

    Self-provisioning: if admet_ai is not importable, attempt the pinned install once.
    Because the install downgrades numpy under an already-imported numpy, a successful
    fresh install triggers a one-time process re-exec so the retry runs on a clean
    interpreter. Any failure (import, install, blocked egress, runtime) returns
    (None, None) so the caller's Biomni MPNN fallback runs."""
    if not _admet_ai_importable():
        installed = _pip_install_admet_ai()
        # If we just installed it in THIS process, numpy was downgraded underneath us;
        # re-exec once so the import below runs against the new numpy. If the re-exec
        # guard is already set (we are the child) fall through and import directly.
        if installed and os.environ.get("ADMET_SELFPROVISION_REEXECED") != "1":
            _reexec_once()  # does not return (replaces the process)
    try:
        from admet_ai import ADMETModel
    except Exception as e:
        print(f"[admet] ADMET-AI import failed after install attempt ({e}); "
              "falling back to MPNN.", file=sys.stderr)
        return None, None
    try:
        _apply_torch_load_shim()
        # Commercially-permissive percentile reference (ChEMBL approved drugs, CC BY-SA)
        # replaces ADMET-AI's bundled DrugBank reference (CC BY-NC, no commercial use).
        # If the reference asset is missing, run WITHOUT percentiles (drugbank_path=None);
        # we never silently fall back to the bundled DrugBank set.
        from pathlib import Path
        _skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ref_csv = Path(_skill_dir) / "assets" / "chembl_approved_reference.csv"
        if ref_csv.exists():
            model = ADMETModel(drugbank_path=ref_csv)
            has_pct = True
            ref_note = f"ChEMBL approved-drug reference ({ref_csv.name}, CC BY-SA)"
        else:
            print(f"[admet] percentile reference not found at {ref_csv}; running "
                  "without percentiles (bundled DrugBank never used).", file=sys.stderr)
            model = ADMETModel(drugbank_path=None)
            has_pct = False
            ref_note = "none (percentile columns omitted)"
        preds = model.predict(smiles=smiles)  # dict or 1-row df depending on version
        if isinstance(preds, dict):
            wide = pd.DataFrame([preds])
        else:
            wide = preds if isinstance(preds, pd.DataFrame) else pd.DataFrame(preds)
        # ADMET-AI hardcodes the "_drugbank_approved_percentile" suffix even with a custom
        # reference; rename to reflect the true (ChEMBL) source.
        _ren = {c: c.replace("_drugbank_approved_percentile", "_chembl_approved_percentile")
                for c in wide.columns if c.endswith("_drugbank_approved_percentile")}
        if _ren:
            wide = wide.rename(columns=_ren)
        rows = []
        for col in wide.columns:
            if col.endswith("_chembl_approved_percentile"):
                continue
            val = wide.iloc[0][col]
            pctcol = f"{col}_chembl_approved_percentile"
            pct = wide.iloc[0][pctcol] if (has_pct and pctcol in wide.columns) else None
            rows.append({"property": col, "value": val, "percentile": pct})
        df_long = pd.DataFrame(rows)
        meta = {"engine": "ADMET-AI", "has_percentiles": has_pct,
                "percentile_reference": ref_note,
                "percentile_suffix": "chembl_approved_percentile",
                "n_endpoints": int(df_long["property"].nunique())}
        return df_long, meta
    except Exception as e:
        print(f"[admet] ADMET-AI runtime failed ({e}); will fall back.", file=sys.stderr)
        return None, None


def try_biomni(smiles):
    """Fallback: Biomni predict_admet_properties (MPNN). No DrugBank percentiles."""
    try:
        from biomni.tool.pharmacology import predict_admet_properties
    except Exception:
        try:
            mod = importlib.import_module("biomni.tool.pharmacology")
            predict_admet_properties = getattr(mod, "predict_admet_properties")
        except Exception as e:
            print(f"[admet] Biomni fallback import failed ({e}).", file=sys.stderr)
            return None, None
    try:
        out = predict_admet_properties(smiles_list=[smiles], ADMET_model_type="MPNN")
        # Biomni returns a formatted text "Research Log" string, NOT a frame/dict.
        # Parse lines of the form:  "- <Property (detail)>: <value> <unit>"
        import re
        rows = []
        if isinstance(out, str):
            for line in out.splitlines():
                line = line.strip()
                if not line.startswith("- ") or ":" not in line:
                    continue
                prop, rest = line[2:].split(":", 1)
                rest = rest.strip()
                m = re.match(r"^(-?\d+\.?\d*)\s*(.*)$", rest)
                if m:
                    val = float(m.group(1))
                    unit = m.group(2).strip()
                    prop_full = f"{prop.strip()} ({unit})" if unit else prop.strip()
                    rows.append({"property": prop_full, "value": val,
                                 "percentile": None, "raw_property": prop.strip()})
                else:
                    rows.append({"property": prop.strip(), "value": rest,
                                 "percentile": None, "raw_property": prop.strip()})
        else:
            # defensive: if a future version returns structured data
            wide = out if isinstance(out, pd.DataFrame) else pd.DataFrame(
                [out] if isinstance(out, dict) else out)
            for c in wide.columns:
                if c.lower() in ("smiles", "compound", "drug", "input", "index"):
                    continue
                rows.append({"property": c, "value": wide.iloc[0][c],
                             "percentile": None, "raw_property": c})
        if not rows:
            print("[admet] Biomni output parsed to 0 endpoints.", file=sys.stderr)
            return None, None
        df_long = pd.DataFrame(rows)
        meta = {"engine": "Biomni predict_admet_properties (MPNN)",
                "has_percentiles": False,
                "n_endpoints": int(df_long["property"].nunique())}
        return df_long, meta
    except Exception as e:
        print(f"[admet] Biomni fallback runtime failed ({e}).", file=sys.stderr)
        return None, None


def extract_flags(df_long, has_pct):
    """Pull a few decision-relevant endpoints if present (name-tolerant)."""
    flags = {}
    d = {str(k).lower(): (v, p) for k, v, p in
         df_long[["property", "value", "percentile"]].itertuples(index=False)}
    def find(substr):
        for k, (v, p) in d.items():
            if substr in k:
                return v, p
        return None, None
    for key, sub in [("hERG", "herg"), ("CYP3A4", "cyp3a4"),
                     ("CYP2D6", "cyp2d6"), ("ClinTox", "clintox"),
                     ("BBB", "bbb"), ("Bioavailability", "bioavailab")]:
        v, p = find(sub)
        if v is not None:
            flags[key] = {"value": v, "percentile": (p if has_pct else None)}
    return flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smiles", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--engine", default="auto",
                    choices=["auto", "admet_ai", "biomni"])
    args = ap.parse_args()
    os.makedirs(f"{args.outdir}/data", exist_ok=True)

    df_long, meta = None, None
    if args.engine in ("auto", "admet_ai"):
        df_long, meta = try_admet_ai(args.smiles)
    if df_long is None and args.engine in ("auto", "biomni"):
        df_long, meta = try_biomni(args.smiles)

    if df_long is None:
        meta = {"engine": "NONE", "has_percentiles": False, "n_endpoints": 0,
                "error": "Both ADMET-AI and Biomni fallback unavailable."}
        with open(f"{args.outdir}/data/admet_meta.json", "w") as fh:
            json.dump(meta, fh, indent=2, default=str)
        print(json.dumps(meta, default=str))
        sys.exit(2)

    meta["flags"] = extract_flags(df_long, meta["has_percentiles"])
    df_long.to_csv(f"{args.outdir}/data/admet_all_properties.csv", index=False)
    with open(f"{args.outdir}/data/admet_meta.json", "w") as fh:
        json.dump(meta, fh, indent=2, default=str)
    print(json.dumps(meta, default=str))


if __name__ == "__main__":
    main()
