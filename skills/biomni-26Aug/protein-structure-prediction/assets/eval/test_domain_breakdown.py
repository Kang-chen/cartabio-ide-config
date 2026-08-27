#!/usr/bin/env python3
"""Offline tests for confidence_breakdown.domain_breakdown() against the REAL
case that failed: PCSK9 / UniProt Q8NBP7. UniProt annotates BOTH domain-tier and
sequence-level features:
  Signal          1-30    (sequence tier)
  Propeptide     31-152   (sequence tier)
  Inhibitor I9   77-149   (Domain)
  Chain         153-692   (sequence tier)
  Peptidase S8  155-461   (Domain)
  C-terminal    450-692   (Region)
Residues 450-461 are covered by BOTH Peptidase S8 and the C-terminal region.
Residues 1-76 and 150-154 have NO DOMAIN feature — but they ARE annotated
(signal peptide / propeptide / chain), so they must be reported as
`no_domain_feature` with their covering sequence-level features, NOT as a blank,
and they are NOT `uncovered`. This locks in the widened-fetch + honest-label fix.
No network, no HPC."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "scripts"))
import numpy as np  # noqa: E402
import confidence_breakdown as cb  # noqa: E402

SEQ_FEATURES = [
    {"name": "Signal", "type": "Signal", "start": 1, "end": 30},
    {"name": "Propeptide", "type": "Propeptide", "start": 31, "end": 152},
    {"name": "Mature chain", "type": "Chain", "start": 153, "end": 692},
]
DOMAIN_FEATURES = [
    {"name": "Inhibitor I9", "type": "Domain", "start": 77, "end": 149},
    {"name": "Peptidase S8", "type": "Domain", "start": 155, "end": 461},
    {"name": "C-terminal", "type": "Region", "start": 450, "end": 692},
]
ALL_FEATURES = SEQ_FEATURES + DOMAIN_FEATURES  # order-independent; split by category
N = 692
PLDDT = np.linspace(40.0, 95.0, N)  # deterministic, length 692


def _db(features=ALL_FEATURES, plddt=PLDDT):
    return cb.domain_breakdown(plddt, features, protein_name="PCSK9")


def test_category_inference():
    assert cb.category_for_type("Signal") == "sequence"
    assert cb.category_for_type("Propeptide") == "sequence"
    assert cb.category_for_type("Transit peptide") == "sequence"
    assert cb.category_for_type("Chain") == "sequence"
    assert cb.category_for_type("Domain") == "domain"
    assert cb.category_for_type("Region") == "domain"
    # sequence-level types are part of the default fetch now
    for t in ("Signal", "Transit peptide", "Propeptide", "Chain", "Peptide"):
        assert t in cb.DEFAULT_FEATURE_TYPES


def test_seam_450_461_is_overlap_of_both():
    db = _db()
    seam = [s for s in db["segments"]
            if s["status"] == "overlap" and s["start"] <= 450 <= s["end"]]
    assert len(seam) == 1, db["segments"]
    s = seam[0]
    assert (s["start"], s["end"]) == (450, 461), s
    assert set(s["features"]) == {"Peptidase S8", "C-terminal"}, s
    # domain overlap is NOT polluted by the chain-spanning sequence feature
    assert db["overlap"]["ranges"] == [(450, 461)], db["overlap"]
    assert db["overlap"]["n_res"] == 12


def test_prodomain_1_76_no_domain_feature_but_annotated():
    db = _db()
    assert (1, 76) in db["no_domain_feature"]["ranges"], db["no_domain_feature"]
    seg = [s for s in db["segments"] if s["start"] == 1][0]
    assert seg["status"] == "no_domain_feature" and seg["end"] == 76, seg
    assert seg["features"] == []                       # no DOMAIN feature
    assert set(seg["sequence_features"]) == {"Signal", "Propeptide"}, seg
    # and it is NOT reported as a true blank
    assert (1, 76) not in db["uncovered"]["ranges"], db["uncovered"]


def test_gap_150_154_no_domain_feature_covered_by_sequence():
    db = _db()
    assert (150, 154) in db["no_domain_feature"]["ranges"], db["no_domain_feature"]
    seg = [s for s in db["segments"] if s["start"] == 150][0]
    assert seg["status"] == "no_domain_feature" and seg["end"] == 154, seg
    assert set(seg["sequence_features"]) == {"Propeptide", "Mature chain"}, seg


def test_sequence_features_reported_verbatim():
    db = _db()
    rows = {r["name"]: r for r in db["sequence_features"]}
    assert (rows["Signal"]["start"], rows["Signal"]["end"]) == (1, 30)
    assert (rows["Propeptide"]["start"], rows["Propeptide"]["end"]) == (31, 152)
    assert (rows["Mature chain"]["start"], rows["Mature chain"]["end"]) == (153, 692)
    # domain rows do NOT contain the sequence-level features
    assert "Signal" not in {r["name"] for r in db["features"]}


def test_uncovered_empty_for_pcsk9():
    # Signal(1-30)+Propeptide(31-152)+Chain(153-692) tile 1..692 -> nothing bare
    db = _db()
    assert db["uncovered"]["ranges"] == [], db["uncovered"]
    assert db["uncovered"]["n_res"] == 0


def test_per_feature_ranges_verbatim():
    db = _db()
    rows = {r["name"]: r for r in db["features"]}
    assert (rows["Inhibitor I9"]["start"], rows["Inhibitor I9"]["end"]) == (77, 149)
    assert (rows["Peptidase S8"]["start"], rows["Peptidase S8"]["end"]) == (155, 461)
    assert (rows["C-terminal"]["start"], rows["C-terminal"]["end"]) == (450, 692)
    # overlap residues counted toward BOTH features (never silently one)
    assert rows["Peptidase S8"]["n_res"] == 461 - 155 + 1      # 307, includes 450-461
    assert rows["C-terminal"]["n_res"] == 692 - 450 + 1        # 243, includes 450-461


def test_full_segment_partition():
    db = _db()
    segs = db["segments"]
    assert segs[0]["start"] == 1 and segs[-1]["end"] == N
    for a, b in zip(segs, segs[1:]):
        assert b["start"] == a["end"] + 1, (a, b)
    assert sum(s["n_res"] for s in segs) == N
    got = [(s["start"], s["end"], s["status"]) for s in segs]
    assert got == [
        (1, 76, "no_domain_feature"),
        (77, 149, "single"),
        (150, 154, "no_domain_feature"),
        (155, 449, "single"),
        (450, 461, "overlap"),
        (462, 692, "single"),
    ], got


def test_domain_only_still_works():
    # With ONLY domain features (no sequence tier), the gaps are BOTH
    # no_domain_feature AND uncovered (nothing else covers them).
    db = _db(features=DOMAIN_FEATURES)
    assert db["sequence_features"] == []
    assert db["no_domain_feature"]["ranges"] == [(1, 76), (150, 154)]
    assert db["uncovered"]["ranges"] == [(1, 76), (150, 154)]
    seg = [s for s in db["segments"] if s["start"] == 1][0]
    assert seg["status"] == "no_domain_feature" and seg["sequence_features"] == []


def test_omitted_with_reason_when_no_features():
    db = cb.domain_breakdown(PLDDT, [], feature_meta={"reason": "no accession given"})
    assert db["available"] is False
    assert "no accession given" in db["reason"]
    assert db["features"] == [] and db["segments"] == []
    assert db["sequence_features"] == []


def test_plddt_none_infers_length():
    db = cb.domain_breakdown(None, ALL_FEATURES)
    assert db["n_res"] == N
    seam = [s for s in db["segments"] if s["status"] == "overlap"][0]
    assert (seam["start"], seam["end"]) == (450, 461)
    assert seam["mean_plddt"] is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("ALL DOMAIN TESTS PASS")
