#!/usr/bin/env python3
"""
Eval for the two fixes in this skill:

  1. Structure facts (resolution, method, deposition date) are FETCHED from the PDB and
     carried through with provenance -- never hand-typed. The report never falls back to a
     typed default, and a gate fails if a named PDB has no fetched metadata behind it.
  2. The construct residue span is DERIVED from construct_scope.json (one source), and the
     report states nominal-vs-realised when the crop pulls in extra residues. A gate fails
     if the config hand-types the span or if scope and metrics disagree.

Runs standalone (`python assets/eval/test_fixes.py`) or under pytest. Uses relative paths
only (no setwd); the live 5O45 check skips cleanly when there is no network.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "scripts"))
FIXTURES = os.path.join(HERE, "fixtures")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pandas as pd  # noqa: E402  (a declared skill dependency)

import fetch_structure_metadata as fsm  # noqa: E402
import build_report as br  # noqa: E402


def _load_fixture():
    with open(os.path.join(FIXTURES, "5o45_rcsb.json")) as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- Fix 1: fetch
def test_parse_5o45_fixture_offline():
    """The parser reads 0.99 A from a real RCSB payload -- not 2.10 or 1.98."""
    rec = fsm.parse_entry_json("5o45", _load_fixture(), "https://example/5O45",
                               fetched_at="2026-01-01T00:00:00Z")
    assert rec["available"] is True
    assert rec["resolution_A"] == 0.99, rec["resolution_A"]
    assert rec["experimental_method"] == "X-ray", rec["experimental_method"]
    assert rec["deposition_date"] == "2017-05-26", rec["deposition_date"]
    assert rec["release_date"] == "2017-09-20", rec["release_date"]
    assert rec["pdb_id"] == "5O45"
    assert rec["provenance"]["source_url"] and rec["provenance"]["fetched_at"]


def test_unavailable_record_is_provenance_honest():
    """A failed fetch yields nulls + an error, never a fabricated value."""
    rec = fsm.build_unavailable_record("5O45", "https://example/5O45", "boom")
    assert rec["available"] is False
    for field in fsm.FACT_FIELDS:
        assert rec[field] is None, field
    assert rec["provenance"]["error"] == "boom"


def test_resolution_and_structure_fact_render():
    good = {"available": True, "resolution_A": 0.99, "experimental_method": "X-ray"}
    assert br.resolution_str(good) == "0.99 \u00c5"
    assert br.structure_fact(good, "experimental_method") == "X-ray"
    bad = {"available": False, "resolution_A": None}
    assert br.resolution_str(bad) == "unavailable"
    assert br.structure_fact(bad, "resolution_A") is None
    assert br.resolution_str(None) == "unavailable"


# ------------------------------------------------------------------- Fix 2: derived span
def test_construct_span_derives_and_explains():
    cfg = {"domain_label": "IgV domain"}
    scope = {"core_range": "18-127", "target_range": "17-137", "margin": 10, "margin_mode": "spatial"}
    realised, expl = br.construct_span_bits(cfg, scope)
    assert realised == "17-137"
    assert expl and "17-137" in expl and "18-127" in expl and "spatial margin" in expl, expl
    # No divergence -> realised only, no explanation.
    realised2, expl2 = br.construct_span_bits(cfg, {"core_range": "18-127", "target_range": "18-127"})
    assert realised2 == "18-127" and expl2 is None


# ------------------------------------------------------------------------------- the gate
def _clean_inputs():
    cfg = {"target_pdb": "5O45", "domain_label": "IgV domain",
           "hotspots": [115, 116, 121, 122, 123], "figures": {}}
    metrics = {"cand1": {"iptm": 0.9, "iface_pae": 2.0, "target_range": "17-137",
                         "hotspots_declared": [115, 116, 121, 122, 123],
                         "epitope_status": "ON_TARGET"}}
    rank = pd.DataFrame([{"candidate": "cand1", "iptm": 0.9, "iface_pae": 2.0}])
    derived = {"n_validated": 1}
    struct_meta = {"available": True, "resolution_A": 0.99, "experimental_method": "X-ray",
                   "deposition_date": "2017-05-26", "pdb_id": "5O45",
                   "provenance": {"pdb_id": "5O45", "source_url": "https://x/5O45", "fetched_at": "t"}}
    scope = {"target_range": "17-137", "core_range": "18-127", "margin": 10,
             "margin_mode": "spatial", "domain_start": 17}
    return cfg, rank, metrics, derived, struct_meta, scope


def _warn(cfg, rank, metrics, derived, sm, scope):
    return br.verify_report_inputs(cfg, rank, metrics, derived, sm, scope, strict=False)


def test_clean_inputs_pass_gate():
    cfg, rank, metrics, derived, sm, scope = _clean_inputs()
    w = _warn(cfg, rank, metrics, derived, sm, scope)
    assert w == [], w


def test_handtyped_resolution_fails_gate():
    cfg, rank, metrics, derived, sm, scope = _clean_inputs()
    cfg["target_resolution"] = "2.10 \u00c5"
    assert any("target_resolution" in x for x in _warn(cfg, rank, metrics, derived, sm, scope))


def test_handtyped_domain_range_fails_gate():
    cfg, rank, metrics, derived, sm, scope = _clean_inputs()
    cfg["domain_range"] = "18-127"
    assert any("domain_range" in x for x in _warn(cfg, rank, metrics, derived, sm, scope))


def test_missing_structure_metadata_fails_gate():
    cfg, rank, metrics, derived, sm, scope = _clean_inputs()
    assert any("no fetched structure metadata" in x
               for x in _warn(cfg, rank, metrics, derived, None, scope))


def test_pdb_mismatch_fails_gate():
    cfg, rank, metrics, derived, sm, scope = _clean_inputs()
    sm["provenance"]["pdb_id"] = "1ABC"
    sm["pdb_id"] = "1ABC"
    assert any("1ABC" in x and "5O45" in x for x in _warn(cfg, rank, metrics, derived, sm, scope))


def test_scope_metrics_mismatch_fails_gate():
    cfg, rank, metrics, derived, sm, scope = _clean_inputs()
    scope["target_range"] = "18-130"
    assert any("disagrees with metrics target_range" in x
               for x in _warn(cfg, rank, metrics, derived, sm, scope))


def test_missing_scope_fails_gate():
    cfg, rank, metrics, derived, sm, scope = _clean_inputs()
    assert any("construct_scope" in x for x in _warn(cfg, rank, metrics, derived, sm, None))


def test_strict_mode_exits_2():
    cfg, rank, metrics, derived, sm, scope = _clean_inputs()
    cfg["target_resolution"] = "2.10 \u00c5"
    try:
        br.verify_report_inputs(cfg, rank, metrics, derived, sm, scope, strict=True)
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("strict gate did not exit on a hand-typed structure fact")


# --------------------------------------------------------------- live (network) smoke test
def test_live_fetch_5o45_returns_099():
    """Confirms the real RCSB entry for 5O45 reports 0.99 A. Skips if offline."""
    try:
        rec = fsm.fetch_structure_metadata("5O45", timeout=30)
    except Exception as e:  # noqa: BLE001
        msg = f"live RCSB fetch unavailable ({e!r})"
        try:
            import pytest
            pytest.skip(msg)
        except ImportError:
            print(f"[SKIP] {msg}")
            return
    assert rec["resolution_A"] == 0.99, rec["resolution_A"]
    assert rec["experimental_method"] == "X-ray"
    assert rec["available"] is True


def _run_standalone():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = skipped = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {e!r}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
