#!/usr/bin/env python3
"""
load_example_data.py  --  Two bundled, self-contained example antibodies for
the antibody-developability-humanization skill. No network required.

Examples
--------
1. muMAb 4D5  (reference-present demo)
   Murine anti-HER2 mAb (ATCC CRL 10463); the parent that was humanized into
   trastuzumab. Exercises the full pipeline INCLUDING the optional
   reference-present benchmark: design blind from the murine sequence, then
   reveal trastuzumab and score canonical back-mutation recovery.

2. adalimumab  (already-human / assess-only demo)
   Approved fully-human anti-TNF mAb (Humira). Exercises the "already human"
   branch: the species/format gate should classify it paired_human (framework
   identity to nearest human germline >= 85% on both chains, despite ANARCI
   sometimes labelling VH as a non-human species), so the workflow ASSESSES
   developability + immunogenicity and SKIPS humanization.

All sequences are variable-domain (Fv) only. Sequences were cross-checked
against public sources (Thera-SAbDab; Carter et al. 1992 for 4D5/trastuzumab).

Usage
-----
    from load_example_data import EXAMPLES, get_example
    ex = get_example("mumab4d5")        # -> dict
    print(ex["VH"], ex["VL"], ex["reference"])

    # CLI: write an example to a JSON the other scripts can consume
    python load_example_data.py --name mumab4d5 --out example.json
    python load_example_data.py --list
"""
from __future__ import annotations
import argparse, json

# --- muMAb 4D5 (murine anti-HER2 parent of trastuzumab) ---
MUMAB4D5_VH = ("QVQLQQSGPELVKPGASLKLSCTASGFNIKDTYIHWVKQRPEQGLEWIGRIYPTNGYT"
               "RYDPKFQDKATITADTSSNTAYLQMNSLRPEDTAVYYCSRWGGDGFYAMDYWGQGTLVTVSS")
MUMAB4D5_VL = ("DIVMTQSHKFMSTSVGDRVSITCRASQDVNTAVAWYQQKPGHSPKLLIYSASFLESGVP"
               "DRFTGNRSGTDFTFTISSVQAEDLAVYYCQQHYTTPPTFGGGTKVEIK")

# --- trastuzumab (humAb4D5-8) : the held-out reference for the 4D5 demo ---
TRASTUZUMAB_VH = ("EVQLVESGGGLVQPGGSLRLSCAASGFNIKDTYIHWVRQAPGKGLEWVARIYPTNGYT"
                  "RYADSVKGRFTISADTSKNTAYLQMNSLRAEDTAVYYCSRWGGDGFYAMDVWGQGTLVTVSS")
TRASTUZUMAB_VL = ("DIQMTQSPSSLSASVGDRVTITCRASQDVNTAVAWYQQKPGKAPKLLIYSASFLESGVP"
                  "SRFSGSRSGTDFTLTISSLQPEDFATYYCQQHYTTPPTFGQGTKVEIKRT")

# --- adalimumab (Humira; approved fully-human anti-TNF) ---
ADALIMUMAB_VH = ("EVQLVESGGGLVQPGRSLRLSCAASGFTFDDYAMHWVRQAPGKGLEWVSAITWNSGHID"
                 "YADSVEGRFTISRDNAKNSLYLQMNSLRAEDTAVYYCAKVSYLSTASSLDYWGQGTLVTVSS")
ADALIMUMAB_VL = ("DIQMTQSPSSLSASVGDRVTITCRASQGIRNYLAWYQQKPGKAPKLLIYAASTLQSGVP"
                 "SRFSGSGSGTDFTLTISSLQPEDVATYYCQRYNRAPYTFGQGTKVEIK")

EXAMPLES = {
    "mumab4d5": {
        "name": "muMAb 4D5",
        "description": "Murine anti-HER2 mAb (ATCC CRL 10463); parent of "
                       "trastuzumab. Reference-present benchmark demo.",
        "VH": MUMAB4D5_VH,
        "VL": MUMAB4D5_VL,
        "source_species": "murine",
        "expected_branch": "paired_nonhuman",   # -> humanize
        "reference": {                            # held-out clinical answer
            "name": "trastuzumab",
            "VH": TRASTUZUMAB_VH,
            "VL": TRASTUZUMAB_VL,
        },
        # canonical framework back-mutations found in trastuzumab (Kabat)
        "reference_canonical": ["H71", "H73", "H78", "H93", "L66"],
    },
    "adalimumab": {
        "name": "adalimumab",
        "description": "Approved fully-human anti-TNF mAb (Humira). "
                       "Already-human / assess-only demo.",
        "VH": ADALIMUMAB_VH,
        "VL": ADALIMUMAB_VL,
        "source_species": "human",
        "expected_branch": "paired_human",       # -> assess only, no humanize
        "reference": None,
    },
}


def get_example(name: str) -> dict:
    """Return a bundled example by key (case-insensitive; hyphens/spaces ok)."""
    key = name.lower().replace("-", "").replace("_", "").replace(" ", "")
    aliases = {"mumab4d5": "mumab4d5", "4d5": "mumab4d5",
               "trastuzumabparent": "mumab4d5",
               "adalimumab": "adalimumab", "humira": "adalimumab"}
    key = aliases.get(key, key)
    if key not in EXAMPLES:
        raise KeyError(f"unknown example '{name}'. Available: "
                       f"{list(EXAMPLES)}")
    return EXAMPLES[key]


def _cli():
    ap = argparse.ArgumentParser(description="Load a bundled example antibody")
    ap.add_argument("--name", help="example key: mumab4d5 | adalimumab")
    ap.add_argument("--out", help="write the example as JSON to this path")
    ap.add_argument("--list", action="store_true", help="list examples")
    a = ap.parse_args()
    if a.list or not a.name:
        for k, v in EXAMPLES.items():
            ref = v["reference"]["name"] if v.get("reference") else "-"
            print(f"{k:12s}  {v['name']:14s}  branch={v['expected_branch']:16s}"
                  f"  reference={ref}")
        return
    ex = get_example(a.name)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(ex, f, indent=2)
        print(f"wrote {a.out}")
    else:
        print(json.dumps(ex, indent=2))


if __name__ == "__main__":
    _cli()
