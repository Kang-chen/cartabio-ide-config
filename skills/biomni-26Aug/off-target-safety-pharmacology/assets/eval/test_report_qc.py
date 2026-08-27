#!/usr/bin/env python3
"""
Offline unit tests for the report-QC layer (scripts/report_qc.py).

These guard the fixes that stop the agent from having to hand-patch outputs at runtime:
  * correct English ordinals (the "72th" -> "72nd" bug),
  * probabilities never printed at raw float precision,
  * the 4-state engine-agreement mapping,
  * ortholog collapse (two HMGCR rows -> one point),
  * the report_data gate (adaptive-split / agreement / primary handling must be present),
  * the PDF gate (raw-float leaks, near-blank pages),
  * the figure gate (blank / degenerate figures).

No network. Run with `pytest assets/eval/` or `python assets/eval/test_report_qc.py`.
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", "..", "scripts"))
sys.path.insert(0, _SCRIPTS)

import report_qc as q  # noqa: E402


# --------------------------------------------------------------------------- ordinals
def test_english_ordinal_table():
    expected = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 11: "11th", 12: "12th",
                13: "13th", 21: "21st", 22: "22nd", 23: "23rd", 72: "72nd",
                101: "101st", 111: "111th"}
    for n, want in expected.items():
        got = q.english_ordinal(n)
        assert got == want, f"english_ordinal({n}) == {got!r}, want {want!r}"
    # the specific regression: 72 must not be "72th"
    assert q.english_ordinal(72) != "72th"
    # floats round first (99.75 -> 100th)
    assert q.english_ordinal(99.75) == "100th"


# --------------------------------------------------------------------------- formatting
def test_fmt_prob_no_raw_precision():
    assert q.fmt_prob(0.9953125, 3) == "0.995"
    assert q.fmt_prob(0.724, 2) == "0.72"
    assert q.fmt_prob(None) == "n/a"
    assert q.fmt_prob(float("nan")) == "n/a"
    assert q.fmt_prob("n/a") == "n/a"
    # never leaks >3 decimals at the default
    assert len(q.fmt_prob(0.123456789).split(".")[1]) <= 3


# --------------------------------------------------------------------------- agreement
def test_agreement_state():
    assert q.agreement_state(True, True) == "Both"
    assert q.agreement_state(True, False) == "Similarity only"
    assert q.agreement_state(False, True) == "DTI only"
    assert q.agreement_state(False, False) == "Neither"
    assert q.AGREEMENT_ORDER["Both"] < q.AGREEMENT_ORDER["Similarity only"]
    assert q.AGREEMENT_ORDER["Similarity only"] < q.AGREEMENT_ORDER["Neither"]


# --------------------------------------------------------------------------- ortholog collapse
def test_collapse_orthologs():
    import pandas as pd
    df = pd.DataFrame({
        "label": ["HMGCR human", "HMGCR rat", "H1"],
        "pref": ["HMG-CoA reductase", "hmg-coa  reductase", "Histamine H1 receptor"],
        "val": [7.1, 8.4, 8.0]})
    out = q.collapse_orthologs(df, "pref", by="val")
    assert len(out) == 2, "two HMGCR orthologs must collapse to one row"
    hmgcr = out[out["pref"].str.lower().str.contains("hmg-coa")]
    assert int(hmgcr["n_orthologs"].iloc[0]) == 2
    # representative kept is the max-`by` row (rat, 8.4)
    assert abs(float(hmgcr["val"].iloc[0]) - 8.4) < 1e-9


def test_normalize_prefname():
    assert q.normalize_prefname("  HMG-CoA   Reductase ") == "hmg-coa reductase"
    assert q.normalize_prefname(None) == ""


# --------------------------------------------------------------------------- report_data gate
def _good_rd():
    return {
        "panel": {"n_core": 32, "n_adaptive": 3, "n_primary": 1},
        "benchmark": {"tier": "A", "n_sim_hits_core": 5, "n_sim_hits_adaptive": 3,
                      "primary_resolved": True, "agreement_counts": {"Both": 4},
                      "on_target": {"resolved": True}},
        "top_predictions": [{"label": "H1", "source": "core", "P_sim": 0.9,
                             "agreement": "Both"}],
    }


def test_validate_report_data_accepts_good():
    assert q.validate_report_data(_good_rd()) is True


def test_validate_report_data_rejects_missing_split():
    rd = _good_rd()
    del rd["benchmark"]["n_sim_hits_core"]           # simulate the item-1 defect
    try:
        q.validate_report_data(rd)
    except q.ReportQCError as e:
        assert "core/adaptive" in str(e)
        return
    raise AssertionError("gate should fail when the similarity-hit split is missing")


def test_validate_report_data_rejects_missing_agreement():
    rd = _good_rd()
    del rd["top_predictions"][0]["agreement"]
    try:
        q.validate_report_data(rd)
    except q.ReportQCError:
        return
    raise AssertionError("gate should fail when a top prediction lacks an agreement state")


# --------------------------------------------------------------------------- pdf gate
def _write_pdf(path, pages):
    """pages: list of text blocks (one per page)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=letter)
    for block in pages:
        y = 750
        for line in block.split("\n"):
            c.drawString(60, y, line)
            y -= 14
        c.showPage()
    c.save()


def _long_block(tag):
    body = " ".join([f"{tag} line {i} with enough words to exceed the content threshold."
                     for i in range(8)])
    return body


def test_validate_pdf_accepts_good(tmp_path=None):
    # min_bytes lowered only because a minimal synthetic PDF is legitimately tiny; the
    # DOI/version tokens and 2-3 decimal numbers below must NOT trip the raw-float gate.
    d = tmp_path or tempfile.mkdtemp()
    p = os.path.join(str(d), "good.pdf")
    _write_pdf(p, [_long_block("A") + "\nROC-AUC 0.799 and pChEMBL 6.019 here.",
                   _long_block("B") + "\nDOI 10.1093/nar/gky1075 must not trip the gate."])
    res = q.validate_pdf(p, min_bytes=500)
    assert res["status"] == "ok" and res["pages"] == 2


def test_validate_pdf_rejects_raw_float(tmp_path=None):
    d = tmp_path or tempfile.mkdtemp()
    p = os.path.join(str(d), "rawfloat.pdf")
    _write_pdf(p, [_long_block("A") + "\nhERG probability 0.9953125 leaked.",
                   _long_block("B")])
    try:
        q.validate_pdf(p, min_bytes=500)
    except q.ReportQCError as e:
        assert "raw float" in str(e) and "too small" not in str(e)
        return
    raise AssertionError("gate should fail on a >=4-decimal raw float")


def test_validate_pdf_rejects_blank_page(tmp_path=None):
    d = tmp_path or tempfile.mkdtemp()
    p = os.path.join(str(d), "blank.pdf")
    _write_pdf(p, [_long_block("A"), "x"])   # page 2 nearly empty, no image
    try:
        q.validate_pdf(p, min_bytes=500)
    except q.ReportQCError as e:
        assert "near-blank" in str(e) and "too small" not in str(e)
        return
    raise AssertionError("gate should fail on a near-blank page")


def test_validate_pdf_rejects_too_small(tmp_path=None):
    d = tmp_path or tempfile.mkdtemp()
    p = os.path.join(str(d), "small.pdf")
    _write_pdf(p, [_long_block("A"), _long_block("B")])
    try:
        q.validate_pdf(p, min_bytes=10_000_000)
    except q.ReportQCError as e:
        assert "too small" in str(e)
        return
    raise AssertionError("gate should fail when the file is below the size floor")


# --------------------------------------------------------------------------- figure gate
def test_assert_figure_ok(tmp_path=None):
    import numpy as np
    from PIL import Image
    d = tmp_path or tempfile.mkdtemp()
    # blank white image -> must fail
    blank = os.path.join(str(d), "blank.png")
    Image.fromarray(np.full((300, 400), 255, dtype="uint8")).save(blank)
    try:
        q.assert_figure_ok(blank)
    except q.ReportQCError:
        pass
    else:
        raise AssertionError("assert_figure_ok should reject a blank figure")
    # noisy image -> must pass
    good = os.path.join(str(d), "good.png")
    Image.fromarray(np.random.randint(0, 255, (300, 400), dtype="uint8")).save(good)
    assert q.assert_figure_ok(good)["status"] == "ok"


# --------------------------------------------------------------------------- plain runner
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001 - test runner surfaces all failures
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} tests passed, {failed} failed")
    sys.exit(1 if failed else 0)
