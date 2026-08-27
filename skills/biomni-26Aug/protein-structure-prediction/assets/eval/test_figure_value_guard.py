#!/usr/bin/env python3
"""Offline tests for figure_value_guard — the gate that stops a figure/infographic
from stating a number the exported table does not support. Reproduces the exact
audited defect: an infographic reading "Confident 70-90: 30%" when the band table
says 29.48%. No network, no HPC."""
import os
import sys
import csv
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "scripts"))
import numpy as np  # noqa: E402
import confidence_breakdown as cb  # noqa: E402
import figure_value_guard as g  # noqa: E402

# n=10000 with 2948 residues in [70,90) -> confident == 29.48% exactly.
PLDDT = np.array([95.0] * 1000 + [80.0] * 2948 + [60.0] * 3000 + [30.0] * 3052)
assert PLDDT.size == 10000
BB = cb.band_breakdown(PLDDT)
PCT = {b["band"]: b["percent"] for b in BB["bands"]}
assert PCT["confident"] == 29.48, PCT


def test_derive_exact_percent_not_rounded():
    dv = g.derive_infographic_values(BB)
    assert dv["bands"]["confident"] == "Confident (70-90): 29.48%", dv["bands"]
    assert dv["percents"]["confident"] == 29.48


def test_good_prompt_from_derived_strings_passes():
    dv = g.derive_infographic_values(BB)
    prompt = "Confidence summary. " + "  ".join(dv["bands"].values())
    res = g.check_infographic(prompt, BB)
    assert res["ok"] is True
    assert set(res["checked"]) == {"very_high", "confident", "low", "very_low"}
    assert res["mismatched"] == []


def test_hand_typed_rounded_value_fails_loudly():
    dv = g.derive_infographic_values(BB)
    good = "Bands: " + "  ".join(dv["bands"].values())
    bad = good.replace(dv["bands"]["confident"], "Confident 70-90: 30%")
    raised = False
    try:
        g.check_infographic(bad, BB)
    except AssertionError as e:
        raised = True
        assert "29.48%" in str(e) and "30%" in str(e), str(e)
    assert raised, "gate did not fail on a hand-typed rounded value"


def test_no_cross_attribution_low_vs_very_low():
    # only 'very low' is stated -> it must NOT be read as the 'low' band
    res = g.check_infographic("Very low (< 50): 30.52%.", BB)
    assert res["checked"] == ["very_low"], res
    assert "low" in res["unverified"], res


def test_reads_from_bands_csv():
    # extract_plddt.py writes <prefix>_bands.csv with these columns
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x_bands.csv")
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["band", "label", "count", "percent", "mean_plddt"])
            for b in BB["bands"]:
                w.writerow([b["band"], b["label"], b["count"],
                            b["percent"], b["mean_plddt"]])
        assert g.band_percents(p)["confident"] == 29.48
        dv = g.derive_infographic_values(p)
        assert dv["bands"]["confident"] == "Confident (70-90): 29.48%"


def test_reads_from_full_breakdown_json():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x_confidence_breakdown.json")
        with open(p, "w") as fh:
            json.dump({"band_breakdown": BB, "domain_breakdown": {}}, fh)
        assert g.band_percents(p)["confident"] == 29.48


def test_extra_expected_number_mismatch_fails():
    raised = False
    try:
        g.check_infographic("Mean pLDDT: 82.0.", BB,
                            extra_expected=[{"label": "Mean pLDDT", "value": 81.77}])
    except AssertionError:
        raised = True
    assert raised, "extra_expected mismatch was not caught"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("ALL FIGURE-VALUE-GUARD TESTS PASS")
