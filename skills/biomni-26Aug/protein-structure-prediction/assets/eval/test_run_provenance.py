#!/usr/bin/env python3
"""Offline tests for run_provenance.py — the predictor/fallback disclosure and the
loud report gate. Models the audited PCSK9 run (AlphaFold2 requested, stalled in
MSA search at the poll bound with 0 files, cancelled; Boltz-2 produced the
numbers). No network, no HPC."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "scripts"))
import run_provenance as rp  # noqa: E402

FALLBACK_MANIFEST = {
    "name": "PCSK9", "sequence_length": 692,
    "poll_timeout_s": 2700, "poll_interval_s": 30,
    "requested_methods": ["alphafold"],
    "predictor_order": ["alphafold", "boltz", "esmfold"],
    "attempts": [
        {"method": "alphafold", "job_id": "j1", "status": "running",
         "outcome": "timeout", "seconds": 2700.0, "n_files": 0, "cancelled": True},
        {"method": "boltz", "job_id": "j2", "status": "completed",
         "outcome": "completed", "seconds": 220.0, "n_files": 6},
    ],
    "fallback_trail": [{"from": "alphafold", "reason": "timeout"}],
    "chosen_predictor": "boltz", "status": "success_fallback",
    "mean_plddt": 81.77, "ptm": 0.779,
}

NO_FALLBACK_MANIFEST = {
    "name": "B2M", "sequence_length": 99, "poll_timeout_s": 900,
    "requested_methods": None, "predictor_order": ["esmfold", "boltz"],
    "attempts": [{"method": "esmfold", "outcome": "completed",
                  "n_files": 3, "seconds": 40}],
    "fallback_trail": [], "chosen_predictor": "esmfold",
    "status": "success_primary",
}


def test_needs_disclosure():
    assert rp.needs_disclosure(FALLBACK_MANIFEST) is True
    assert rp.needs_disclosure(NO_FALLBACK_MANIFEST) is False


def test_render_contains_required_facts():
    txt = rp.render_run_provenance(FALLBACK_MANIFEST).lower()
    assert "alphafold" in txt                 # requested/primary named
    assert "boltz" in txt                     # producer named
    assert "2700" in txt                      # poll bound actually used (not 900)
    assert "0 output file" in txt             # still running, no output
    assert "requested" in txt                 # states it was requested
    assert "longer" in txt                    # longer-bound caveat
    assert ("fallback" in txt or "cancel" in txt or "timeout" in txt)


def test_gate_fails_when_disclosure_missing():
    bad = "PCSK9 folded. Mean pLDDT 81.77, pTM 0.779. Structure attached."
    raised = False
    try:
        rp.check_report(FALLBACK_MANIFEST, bad)
    except AssertionError as e:
        raised = True
        msg = str(e)
        assert "AlphaFold v2" in msg and "Boltz-2" in msg  # names the swap
    assert raised, "gate must fail loudly on a report missing the disclosure"


def test_gate_passes_with_canonical_disclosure():
    good = ("PCSK9 folded. Mean pLDDT 81.77.\n\n"
            + rp.render_run_provenance(FALLBACK_MANIFEST))
    res = rp.check_report(FALLBACK_MANIFEST, good)
    assert res["ok"] is True and res["required"] is True and res["missing"] == []


def test_gate_noop_without_fallback():
    res = rp.check_report(NO_FALLBACK_MANIFEST,
                          "Folded B2M with ESMCFold2. Mean pLDDT 86.5.")
    assert res["required"] is False and res["ok"] is True
    # render still produces a clean no-fallback provenance line
    assert "no fallback occurred" in rp.render_run_provenance(NO_FALLBACK_MANIFEST).lower()


def test_reported_bound_is_manifest_value_not_default():
    # the audited run overrode the 900 default to 2700 — the render must use 2700
    txt = rp.render_run_provenance(FALLBACK_MANIFEST)
    assert "2700 s" in txt and "900 s" not in txt


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("ALL PROVENANCE TESTS PASS")
