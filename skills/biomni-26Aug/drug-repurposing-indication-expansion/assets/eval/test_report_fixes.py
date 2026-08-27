"""Eval tests for the two report fixes (styling source + front-matter consistency gate).

Runnable with plain `python assets/eval/test_report_fixes.py` (exits non-zero on failure) or
with pytest. Uses relative paths only (no setwd); writes any PDF to a temp dir, never into the
package.

Covers:
  1. report_style sources the brand palette from pdf-report-generation (header rule #D4A04A,
     footer rule #D5CFC5) and declares no hardcoded hex in the report layer.
  2. build()'s front-matter consistency gate raises on a fail-verdict report whose summary
     omits the failure, and on a passing report that names a flagged artifact unflagged; and
     builds cleanly when the front matter is consistent.
  3. (best effort) the rendered header/footer rules in a built PDF are #D4A04A / #D5CFC5.
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(os.path.dirname(_HERE))            # package root (…/drug-repurposing-…)
for _p in (os.path.join(_PKG, "assets"), os.path.join(_PKG, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd
import report_style as rs
import build_report as br


def _hex(c):
    return "#" + "".join(f"{int(round(v * 255)):02X}" for v in (c.red, c.green, c.blue))


def _approved():
    return pd.DataFrame({
        "canonical_rank": [8, 11, 12, 13],
        "drug": ["Imatinib", "Methylprednisolone", "Cisplatin", "Tibolone"],
        "S_reversal": [22.1, 19.4, 18.7, 17.9],
        "fdr_reversal": [1e-3, 2e-3, 3e-3, 4e-3],
        "moa": ["BCR-ABL inhibitor", "glucocorticoid agonist", "DNA crosslinker", "ER agonist"],
    })


def _controls(kind):
    if kind == "fail":
        row = dict(control="Isotretinoin", expected="mimic", present=True, S_reversal=15.2,
                   p_reversal=1e-4, p_mimic=0.9, fdr_reversal=1e-3, fdr_mimic=0.95,
                   significant=True, direction="reverser",
                   matches_expectation="no (significant opposite)")
    else:
        row = dict(control="Fluticasone", expected="reverser", present=True, S_reversal=30.7,
                   p_reversal=1e-4, p_mimic=0.99, fdr_reversal=1e-3, fdr_mimic=0.99,
                   significant=True, direction="reverser", matches_expectation="yes")
    return pd.DataFrame([row])


_FLAGS = [
    {"name": "Methylprednisolone", "classification": "artifact", "note": "steroid signature, not lipid biology"},
    {"name": "Cisplatin", "classification": "artifact", "note": "cytotoxic signature drives score"},
]
_STATS = dict(n_up=120, n_dn=95, n_drugs=271, n_human=160, n_mouse=111, n_approved=107,
              n_appr_sig=33, bg=15229, mouse_map_median="86", rho="0.87")
_REF = ["Doe J et al. Example study. J Example. 2021. PMID: 12345678"]


def _cfg(**over):
    cfg = dict(
        title="Drug Repurposing for Hypercholesterolemia",
        disease_label="hypercholesterolemia",
        top_hit_rationale="Canonical #1 is a research chemical, likely a non-specific assay artifact.",
        executive_summary=["Exploratory analysis."],
        introduction=["bg"], results_intro=["d"], results_top=["t"], results_moa=["m"],
        results_controls=["c"], discussion=["d"], conclusions=["c"], references=_REF,
    )
    cfg.update(over)
    return cfg


def _build(cfg, kind, name):
    out = os.path.join(tempfile.mkdtemp(), f"{name}.pdf")
    br.build(cfg, _STATS, {"approved": _approved(), "controls": _controls(kind)}, {}, out)
    return out


# ---------------------------------------------------------------------------
# 1. Styling source
# ---------------------------------------------------------------------------
def test_palette_sourced_from_platform():
    assert _hex(rs.DIVIDER_COLOR) == "#D4A04A", "header rule must be the gold accent"
    assert _hex(rs.TABLE_BORDER) == "#D5CFC5", "footer rule must be warm grey"
    assert _hex(rs.TABLE_HEADER_BG) == "#D4A04A" and _hex(rs.TABLE_HEADER_FG) == "#FFFFFF"


def test_no_hardcoded_hex_in_report_layer():
    src = open(os.path.join(_PKG, "assets", "report_style.py")).read()
    assert 'HexColor("#' not in src, "report layer must not declare hex color literals"


# ---------------------------------------------------------------------------
# 2. Front-matter consistency gate
# ---------------------------------------------------------------------------
def test_gate_raises_when_failed_verdict_not_stated():
    cfg = _cfg(executive_summary=["Imatinib and tibolone are promising candidates."],
               controls_failure_acknowledgement="ack", compound_flags=_FLAGS)
    try:
        _build(cfg, "fail", "b")
    except ValueError:
        return
    raise AssertionError("gate should raise when a failed verdict is not stated in the front matter")


def test_gate_raises_when_flagged_compound_named_unflagged():
    cfg = _cfg(key_finding_title="Top",
               key_finding_body="Cisplatin is a strong approved candidate here.",
               compound_flags=_FLAGS)
    try:
        _build(cfg, "pass", "c")
    except ValueError:
        return
    raise AssertionError("gate should raise when a flagged compound is named unflagged up front")


def test_gate_allows_consistent_failed_report():
    cfg = _cfg(
        executive_summary=["Positive-control validation did not pass; results are exploratory only."],
        controls_failure_acknowledgement="Isotretinoin (expected mimic) scored as a significant reverser.",
        compound_flags=_FLAGS)
    assert os.path.exists(_build(cfg, "fail", "a"))


def test_gate_allows_legacy_passing_report():
    assert os.path.exists(_build(_cfg(), "pass", "d"))


# ---------------------------------------------------------------------------
# 3. Rendered rule colours (best effort — needs a PDF renderer)
# ---------------------------------------------------------------------------
def test_rendered_rule_colours():
    try:
        import fitz
    except Exception:
        return  # renderer unavailable; the constants test already covers the source
    pdf = _build(_cfg(), "pass", "render")
    page = fitz.open(pdf)[0]
    H = page.rect.height
    hdr = ftr = None
    for d in page.get_drawings():
        col = d.get("color")
        if not col:
            continue
        for it in d["items"]:
            if it[0] == "l" and abs(it[1].y - it[2].y) < 0.6 and abs(it[1].x - 60) < 4 and abs(it[2].x - 552) < 6:
                hx = "#" + "".join(f"{int(round(v * 255)):02X}" for v in col)
                if it[1].y < H / 2:
                    hdr = hx
                else:
                    ftr = hx
    assert hdr == "#D4A04A", f"rendered header rule {hdr} != #D4A04A"
    assert ftr == "#D5CFC5", f"rendered footer rule {ftr} != #D5CFC5"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(fns)} eval tests passed.")
