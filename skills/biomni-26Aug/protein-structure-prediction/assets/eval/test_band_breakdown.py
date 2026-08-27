#!/usr/bin/env python3
"""Offline tests for confidence_breakdown.band_breakdown() — the single source of
pLDDT confidence-band counts. Verifies that the printed labels match the binning
(the exact defect being removed: a right-closed cut() whose labels lied at the
boundaries). No network, no HPC."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "scripts"))
import numpy as np  # noqa: E402
import confidence_breakdown as cb  # noqa: E402


def _counts(bb):
    return {b["band"]: b["count"] for b in bb["bands"]}


def test_exact_boundaries_50_70_90():
    # Convention: lower-inclusive, upper-exclusive.
    # 90 -> very_high, 70 -> confident, 50 -> low, 49.999 -> very_low.
    bb = cb.band_breakdown([90.0, 89.999, 70.0, 69.999, 50.0, 49.999, 100.0, 0.0])
    c = _counts(bb)
    assert c == {"very_high": 2, "confident": 2, "low": 2, "very_low": 2}, c
    # 90 and 100 are very_high; 50 is low (NOT very_low); 70 is confident (NOT low)


def test_counts_sum_to_n():
    rng = np.random.default_rng(0)
    arr = rng.uniform(0, 100, size=500)
    bb = cb.band_breakdown(arr)
    assert sum(b["count"] for b in bb["bands"]) == bb["n_res"] == 500


def test_labels_match_computation():
    labels = {b["band"]: b["label"] for b in cb.band_breakdown([80.0])["bands"]}
    assert labels["very_high"] == "pLDDT >= 90"
    assert labels["confident"] == "70 <= pLDDT < 90"
    assert labels["low"] == "50 <= pLDDT < 70"
    assert labels["very_low"] == "pLDDT < 50"
    # explicit inclusivity flags
    vh = [b for b in cb.band_breakdown([95.0])["bands"] if b["band"] == "very_high"][0]
    assert vh["lower_inclusive"] is True and vh["lower"] == 90.0
    vl = [b for b in cb.band_breakdown([10.0])["bands"] if b["band"] == "very_low"][0]
    assert vl["upper_inclusive"] is False and vl["upper"] == 50.0


def test_percent_and_mean():
    bb = cb.band_breakdown([95.0, 95.0, 30.0, 30.0])  # 2 very_high, 2 very_low
    by = {b["band"]: b for b in bb["bands"]}
    assert by["very_high"]["count"] == 2 and by["very_high"]["percent"] == 50.0
    assert by["very_high"]["mean_plddt"] == 95.0
    assert by["confident"]["count"] == 0 and by["confident"]["mean_plddt"] is None


def test_empty_input():
    bb = cb.band_breakdown([])
    assert bb["n_res"] == 0
    assert all(b["count"] == 0 for b in bb["bands"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("ALL BAND TESTS PASS")
