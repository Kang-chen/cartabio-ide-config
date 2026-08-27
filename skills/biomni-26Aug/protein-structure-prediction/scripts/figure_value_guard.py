#!/usr/bin/env python3
"""
figure_value_guard.py — make every number on a figure/infographic DERIVE from the
exported table, and GATE it so a hand-typed value can never ship.

WHY THIS FILE EXISTS
--------------------
In an audited run the summary infographic labelled a confidence band
"Confident 70-90: 30%" while the exported band table gave **29.48%** — the value
had been typed into the GenerateImage prompt by hand and silently rounded. The
prompt text is free-form, so nothing forced its numbers to match the computed
breakdown.

This module closes that gap the same way run_provenance.py closes the predictor-
swap gap:
  * derive_infographic_values(source) -> the canonical label:value strings the
    agent should PASTE into the infographic prompt, formatted from the exported
    band table. No number is typed by hand.
  * check_infographic(prompt_text, source) -> the LOUD GATE. It parses the
    percentage stated next to each band label in the prompt and fails (raises /
    exits non-zero) if any stated value does not match the exported table.

`source` may be:
  * a path to `<name>_<method>_bands.csv` (cols band,label,count,percent,mean_plddt);
  * a path to `<name>_confidence_breakdown.json` (has "band_breakdown");
  * a band_breakdown dict, or a full breakdown dict with a "band_breakdown" key.

The exported percent (2 decimals, as band_breakdown() emits) is the single source
of truth; the infographic must not restate it at a different rounding.
"""
import os
import re
import sys
import csv
import json
import argparse


# band key -> friendly display name (the pLDDT interval is spelled so the label
# always matches the binning convention in confidence_breakdown.band_breakdown()).
_DISPLAY = {
    "very_high": "Very high (pLDDT >= 90)",
    "confident": "Confident (70-90)",
    "low": "Low (50-70)",
    "very_low": "Very low (< 50)",
}
# label synonyms used to locate a band's stated percentage in free-form prompt
# text. Ordered most-specific first so "very low"/"very high" are matched (and
# masked) before the shorter "low"/"high".
_SYNONYMS = [
    ("very_high", ("very high", "very-high", "veryhigh")),
    ("very_low", ("very low", "very-low", "verylow")),
    ("confident", ("confident",)),
    ("low", ("low",)),
]
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_WINDOW = 48  # chars after a band label to look for its percentage token


# ----------------------------------------------------------------------------
# load the exported band table (the single source of truth)
# ----------------------------------------------------------------------------
def _bands_from_dict(d):
    """Accept a band_breakdown dict, or a full breakdown dict that contains one."""
    if isinstance(d, dict) and "bands" in d:
        return d["bands"]
    if isinstance(d, dict) and "band_breakdown" in d:
        return d["band_breakdown"]["bands"]
    raise ValueError("dict is neither a band_breakdown nor has a 'band_breakdown' key")


def load_band_table(source):
    """Return {band: {"label","count","percent","mean_plddt"}} from any accepted
    source (bands CSV path, breakdown JSON path, or a dict)."""
    if isinstance(source, dict):
        bands = _bands_from_dict(source)
    elif isinstance(source, str) and source.lower().endswith(".csv"):
        bands = []
        with open(source) as fh:
            for row in csv.DictReader(fh):
                bands.append({
                    "band": row["band"], "label": row.get("label", ""),
                    "count": int(row["count"]),
                    "percent": float(row["percent"]),
                    "mean_plddt": (None if row.get("mean_plddt") in (None, "", "None")
                                   else float(row["mean_plddt"])),
                })
    elif isinstance(source, str):  # assume JSON
        with open(source) as fh:
            bands = _bands_from_dict(json.load(fh))
    else:
        raise TypeError(f"unsupported source type: {type(source)!r}")
    return {b["band"]: {"label": b.get("label", ""),
                        "count": b.get("count"),
                        "percent": float(b["percent"]),
                        "mean_plddt": b.get("mean_plddt")} for b in bands}


def band_percents(source):
    """{band: percent_float} from the exported table."""
    return {k: v["percent"] for k, v in load_band_table(source).items()}


# ----------------------------------------------------------------------------
# derive the infographic strings (so numbers are never typed)
# ----------------------------------------------------------------------------
def format_percent(p):
    """Exported precision (matches band_breakdown() rounding: 2 decimals)."""
    return f"{float(p):.2f}%"


def derive_infographic_values(source, extra=None):
    """Canonical label:value strings to paste verbatim into the infographic prompt.

    Returns {
      "bands":   {band: "Confident (70-90): 29.48%", ...},   # paste these
      "percents":{band: 29.48, ...},                          # raw, if needed
      "extra":   {name: "Mean pLDDT: 81.77", ...},            # from `extra` dict
    }
    `extra` is an optional {name: (value, unit_or_fmt)} for non-band numbers such
    as mean pLDDT or pTM; each is formatted from the passed value, never typed.
    """
    tbl = load_band_table(source)
    bands = {k: f"{_DISPLAY.get(k, k)}: {format_percent(v['percent'])}"
             for k, v in tbl.items()}
    percents = {k: v["percent"] for k, v in tbl.items()}
    extra_out = {}
    for name, val in (extra or {}).items():
        if isinstance(val, (list, tuple)) and len(val) == 2:
            v, suffix = val
            extra_out[name] = f"{name}: {v}{suffix}"
        else:
            extra_out[name] = f"{name}: {val}"
    return {"bands": bands, "percents": percents, "extra": extra_out}


# ----------------------------------------------------------------------------
# the loud gate
# ----------------------------------------------------------------------------
def _stated_percents(prompt_text):
    """Parse {band: [stated_percent, ...]} from free-form prompt text by finding
    the percentage token that follows each band label. 'very low'/'very high' are
    matched and masked before 'low'/'high' so they never cross-attribute."""
    text = (prompt_text or "").lower()
    found = {}
    for band, syns in _SYNONYMS:
        vals = []
        for syn in syns:
            start = 0
            while True:
                j = text.find(syn, start)
                if j < 0:
                    break
                window = text[j: j + len(syn) + _WINDOW]
                m = _PCT.search(window)
                if m:
                    vals.append(float(m.group(1)))
                # mask this label occurrence so a shorter label can't re-match it
                text = text[:j] + (" " * len(syn)) + text[j + len(syn):]
                start = j + len(syn)
        if vals:
            found[band] = vals
    return found


def check_infographic(prompt_text, source, tol=0.01, extra_expected=None):
    """Loud gate: every band percentage stated in `prompt_text` must match the
    exported table `source`. Raises AssertionError when a stated value disagrees
    (e.g. "30%" where the table says 29.48%).

    Returns {"ok":bool, "checked":[band,...], "mismatched":[...],
             "unverified":[band,...]}.
    `extra_expected` (optional): list of {"label","value","tol"?,"percent"?} to
    check non-band numbers (mean pLDDT, pTM) the same way.
    """
    exported = band_percents(source)
    stated = _stated_percents(prompt_text)
    result = {"ok": True, "checked": [], "mismatched": [], "unverified": []}

    for band, exp in exported.items():
        if band not in stated:
            result["unverified"].append(band)
            continue
        for got in stated[band]:
            result["checked"].append(band)
            if abs(got - exp) > tol:
                result["mismatched"].append(
                    f"{_DISPLAY.get(band, band)}: infographic states {got:g}% but "
                    f"exported table says {exp:.2f}%")

    # optional non-band numbers (mean pLDDT, pTM, …)
    low = (prompt_text or "").lower()
    for item in (extra_expected or []):
        label = str(item["label"]).lower()
        val = float(item["value"])
        itol = float(item.get("tol", tol))
        as_pct = bool(item.get("percent", False))
        j = low.find(label)
        if j < 0:
            result["unverified"].append(item["label"])
            continue
        window = low[j: j + len(label) + _WINDOW]
        if as_pct:
            m = _PCT.search(window)
            got = float(m.group(1)) if m else None
        else:
            m = re.search(r"(\d+(?:\.\d+)?)", window[len(label):])
            got = float(m.group(1)) if m else None
        if got is None:
            result["unverified"].append(item["label"])
            continue
        result["checked"].append(item["label"])
        if abs(got - val) > itol:
            result["mismatched"].append(
                f"{item['label']}: infographic states {got:g} but exported value "
                f"is {val:g}")

    result["ok"] = not result["mismatched"]
    if not result["ok"]:
        raise AssertionError(
            "Infographic/figure states value(s) that do not match the exported "
            "table:\n  - " + "\n  - ".join(result["mismatched"])
            + "\n\nBuild the labels from the exported table instead, e.g.:\n"
            + "\n".join(f"  {v}" for v in
                        derive_infographic_values(source)["bands"].values()))
    return result


def main():
    ap = argparse.ArgumentParser(
        description="Derive infographic band values from the exported table, or "
                    "GATE an infographic/figure prompt against it.")
    ap.add_argument("--breakdown", required=True,
                    help="bands CSV, confidence-breakdown JSON, or band_breakdown JSON")
    ap.add_argument("--infographic-text", default=None,
                    help="path to the infographic/figure prompt text to GATE; "
                         "omit to just --render")
    ap.add_argument("--render", action="store_true",
                    help="print the canonical label:value strings and exit")
    a = ap.parse_args()

    if a.render or not a.infographic_text:
        vals = derive_infographic_values(a.breakdown)
        for s in vals["bands"].values():
            print(s)
        return
    with open(a.infographic_text) as fh:
        text = fh.read()
    try:
        res = check_infographic(text, a.breakdown)
    except AssertionError as e:
        print("GATE FAILED:\n" + str(e), file=sys.stderr)
        sys.exit(1)
    print(f"GATE PASSED: {len(res['checked'])} value(s) match the exported table."
          + (f" Unverified (not stated): {res['unverified']}."
             if res["unverified"] else ""))


if __name__ == "__main__":
    main()
