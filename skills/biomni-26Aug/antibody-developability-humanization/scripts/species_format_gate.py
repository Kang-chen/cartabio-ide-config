"""
species_format_gate.py - decide the processing branch BEFORE humanization (§6).

Branches:
  paired_nonhuman -> humanize + assess   (murine/rat/rabbit paired VH/VL: default)
  paired_human    -> assess only         (already human/humanized; never re-graft)
  single_domain   -> assess only + warn  (VHH/nanobody or heavy/light-only)
  invalid         -> cannot proceed

This gate MUST run before humanize_backmutate.py. The agent uses `branch` to
decide whether to build grafts at all, and surfaces `notes` to the user.
"""
from __future__ import annotations
import json
import argparse

from ab_core import classify_format, DEFAULT_SCHEME

HUMANIZE_SPECIES = {"mouse", "rat", "rabbit", "murine"}


def gate(vh=None, vl=None, scheme=DEFAULT_SCHEME):
    branch, detail = classify_format(vh, vl, scheme=scheme)
    do_humanize = (branch == "paired_nonhuman")
    do_assess = (branch != "invalid")
    return {"branch": branch, "do_humanize": do_humanize,
            "do_assess": do_assess, "detail": detail,
            "notes": detail.get("notes", [])}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vh")
    ap.add_argument("--vl")
    ap.add_argument("--scheme", default=DEFAULT_SCHEME)
    ap.add_argument("--out", default="gate.json")
    a = ap.parse_args()
    res = gate(a.vh, a.vl, a.scheme)
    print(json.dumps(res, indent=2, default=str))
    json.dump(res, open(a.out, "w"), indent=2, default=str)
    for n in res["notes"]:
        print("  NOTE:", n)
