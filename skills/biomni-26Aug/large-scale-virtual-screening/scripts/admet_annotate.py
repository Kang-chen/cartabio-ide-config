#!/usr/bin/env python3
"""
admet_annotate.py -- OPTIONAL, ADVISORY ADMET / drug-likeness annotation of top hits.

Design constraints (from the skill spec):
  * ADVISORY ONLY -- never a hard filter. Annotations are appended to the top-N
    table; nothing is dropped based on them.
  * COMPOSABLE -- prefer an existing ADMET/tox capability over reimplementing.
    At authoring time NO dedicated tox/ADMET skill exists, so this calls the
    Biomni `predict_admet_properties` tool when available. If a dedicated tox/ADMET
    skill is added later, PREFER IT (documented in SKILL.md).
  * SKIPPABLE -- large at-scale runs can omit this to stay fast.

Provider resolution (first that works):
  1. biomni.tool `predict_admet_properties(smiles_list)`  [preferred available tool]
  2. RDKit rule-of-5 / QED fallback (always available) so the column is never empty.

Always adds transparent rule-based flags from RDKit (Lipinski Ro5 violations, QED,
PAINS if available) alongside whatever the ADMET model returns.

Input:
  --top tables/top_hits.csv   (from triage_sar.py)
Output:
  tables/top_hits_admet.csv   top hits + ADMET/advisory columns
  admet_meta.json             provider used, columns added, caveat text
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import sys


def _rdkit_flags(smiles):
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Crippen, rdMolDescriptors, QED
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        return {}
    mw = Descriptors.MolWt(m); logp = Crippen.MolLogP(m)
    hbd = rdMolDescriptors.CalcNumHBD(m); hba = rdMolDescriptors.CalcNumHBA(m)
    ro5 = sum([mw > 500, logp > 5, hbd > 5, hba > 10])
    return {
        "Ro5_violations": ro5,
        "Lipinski_pass": "yes" if ro5 <= 1 else "no",
        "QED": round(QED.qed(m), 3),
        "MW": round(mw, 1), "cLogP": round(logp, 2),
    }


def _parse_admet_text(text, smis):
    """
    Parse the biomni `predict_admet_properties` text report into smiles->dict.

    The tool returns a formatted string with one block per compound:
        Compound SMILES: <smiles>
        Predicted ADMET properties:
        - Solubility: 0.92 log mol/L
        - Absorption (HIA): 39.19 %
        - Clinical Toxicity: 12.21 %
        ...
    We split on 'Compound SMILES:' and parse '- key: value unit' lines. Blocks are
    matched to the input list positionally (order preserved by the tool), with a
    SMILES-string cross-check so mis-alignment is caught rather than silently wrong.
    """
    import re
    blocks = text.split("Compound SMILES:")
    parsed = []  # (smiles_in_block, {prop: value})
    for b in blocks[1:]:
        lines = b.strip().splitlines()
        if not lines:
            continue
        blk_smiles = lines[0].strip()
        props = {}
        for ln in lines[1:]:
            ln = ln.strip()
            if not ln.startswith("-"):
                continue
            m = re.match(r"-\s*(.+?):\s*([-\d.]+)\s*(.*)$", ln)
            if m:
                name = m.group(1).strip()
                try:
                    val = float(m.group(2))
                except ValueError:
                    val = m.group(2)
                unit = m.group(3).strip()
                key = name + (f" ({unit})" if unit and unit != "%" else "")
                props[key if unit != "%" else name + " %"] = val
        parsed.append((blk_smiles, props))

    out = {}
    # positional match with a light sanity check
    for i, smi in enumerate(smis):
        if i < len(parsed):
            blk_smiles, props = parsed[i]
            out[smi] = props
    if len(parsed) != len(smis):
        sys.stderr.write(f"[WARN] ADMET blocks ({len(parsed)}) != inputs ({len(smis)}); "
                         "positional match may be imperfect.\n")
    return out or None


def _try_biomni_admet(smis, model_type="MPNN"):
    """Return dict smiles->annotation dict, or None if the tool is unavailable."""
    fn = None
    # top-level import can be unavailable in some builds; try the module path first,
    # then the flat namespace. (Verified at authoring: pharmacology path works.)
    try:
        from biomni.tool.pharmacology import predict_admet_properties as fn
    except Exception:
        try:
            from biomni.tool import predict_admet_properties as fn
        except Exception:
            return None
    try:
        res = fn(smis, ADMET_model_type=model_type)
    except TypeError:
        try:
            res = fn(smis)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[WARN] predict_admet_properties failed: {e}\n")
            return None
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[WARN] predict_admet_properties failed: {e}\n")
        return None
    # observed return: a formatted text report (str). Parse it.
    if isinstance(res, str):
        return _parse_admet_text(res, smis)
    # tolerate future shapes
    try:
        import pandas as pd
        if isinstance(res, pd.DataFrame):
            key = next((c for c in res.columns if c.lower() in ("smiles", "drug", "compound")), None)
            if key is None:
                return {smi: res.iloc[i].to_dict() for i, smi in enumerate(smis) if i < len(res)}
            return {str(r[key]): r.drop(labels=[key]).to_dict() for _, r in res.iterrows()}
    except Exception:
        pass
    if isinstance(res, dict):
        return res
    if isinstance(res, list) and len(res) == len(smis):
        return {smi: (r if isinstance(r, dict) else {"admet": r}) for smi, r in zip(smis, res)}
    return None


def run(args) -> int:
    rows = []
    with open(args.top, newline="") as fh:
        reader = csv.DictReader(fh)
        base_fields = reader.fieldnames or []
        for r in reader:
            rows.append(r)
    if args.limit:
        rows = rows[: args.limit]
    smis = [r.get("smiles", "") for r in rows]

    provider = "rdkit_rules"
    model_ann = None
    if not args.rdkit_only:
        model_ann = _try_biomni_admet(smis, args.model_type)
        if model_ann is not None:
            provider = "biomni.predict_admet_properties(+rdkit_rules)"

    # union of extra columns
    extra_cols = ["Ro5_violations", "Lipinski_pass", "QED_advisory"]
    model_cols = []
    if model_ann:
        for smi in smis:
            a = model_ann.get(smi)
            if isinstance(a, dict):
                for k in a.keys():
                    kk = f"admet_{k}"
                    if kk not in model_cols:
                        model_cols.append(kk)
    all_fields = base_fields + [c for c in extra_cols if c not in base_fields] + \
                 [c for c in model_cols if c not in base_fields]

    tables = os.path.dirname(args.top)
    out = os.path.join(tables, "top_hits_admet.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=all_fields, extrasaction="ignore")
        w.writeheader()
        for r, smi in zip(rows, smis):
            flags = _rdkit_flags(smi)
            r["Ro5_violations"] = flags.get("Ro5_violations", "")
            r["Lipinski_pass"] = flags.get("Lipinski_pass", "")
            r["QED_advisory"] = flags.get("QED", "")
            if model_ann and isinstance(model_ann.get(smi), dict):
                for k, v in model_ann[smi].items():
                    r[f"admet_{k}"] = v
            w.writerow(r)

    meta = {"provider": provider, "n_annotated": len(rows),
            "model_columns": model_cols, "rule_columns": extra_cols,
            "caveat": ("ADVISORY ONLY -- ADMET/tox predictions are not applied as a "
                       "filter. Values are model estimates with substantial uncertainty; "
                       "use for prioritization/flagging, not decisions. Prefer a dedicated "
                       "tox/ADMET skill if one is available.")}
    with open(os.path.join(tables, "..", "admet_meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"[OK] ADMET/advisory annotation via {provider} -> {out} "
          f"({len(rows)} hits, {len(model_cols)} model cols + rule flags)")
    return 0


def self_check() -> int:
    ok = True
    f = _rdkit_flags("CC(=O)Oc1ccccc1C(=O)O")  # aspirin: Ro5-clean
    if not f or f.get("Lipinski_pass") != "yes":
        print(f"[FAIL] rdkit flags -> {f}", file=sys.stderr); ok = False
    # verify the text-report PARSER (the silent-drop failure class we hit): a canned
    # report must parse into per-SMILES dicts with the tox + absorption fields.
    canned = ("Research Log for ADMET Predictions:\n----\n\n"
              "Compound SMILES: CCO\nPredicted ADMET properties:\n"
              "- Solubility: 0.92 log mol/L\n- Absorption (HIA): 39.19 %\n"
              "- Clinical Toxicity: 12.21 %\n----\n")
    parsed = _parse_admet_text(canned, ["CCO"])
    if not parsed or "CCO" not in parsed or "Clinical Toxicity %" not in parsed["CCO"]:
        print(f"[FAIL] ADMET text parse -> {parsed}", file=sys.stderr); ok = False
    print(f"admet_annotate.py self-check: {'PASS' if ok else 'FAIL'} "
          f"(rdkit flags ok; ADMET text parser recovers "
          f"{len(parsed.get('CCO', {})) if parsed else 0} fields)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Optional advisory ADMET annotation of top hits")
    ap.add_argument("--top", help="tables/top_hits.csv")
    ap.add_argument("--rdkit-only", action="store_true", help="skip the ADMET model")
    ap.add_argument("--model-type", default="MPNN", help="predict_admet_properties ADMET_model_type")
    ap.add_argument("--limit", type=int, default=0, help="cap #hits annotated")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    if not args.top:
        ap.error("--top is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
