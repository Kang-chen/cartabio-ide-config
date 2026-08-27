"""
Bundled worked example: imatinib (STI-571) bound to ABL1 kinase.

This is the validated regression fixture for the skill. It reproduces the
imatinib-ABL1 binding-pocket analysis using:
  - primary structure  : 1IEP  (Abl kinase domain + STI-571, mouse, 2.1 A)
  - comparison         : 2HYY  (human ABL1 + imatinib)
  - ligand             : STI

Expected hallmarks (used as a sanity check, not hard-coded into the pipeline):
  - Thr315 (gatekeeper) makes a hydrogen bond to the aminopyrimidine N.
  - Met318 (hinge) hydrogen-bonds via backbone.
  - Asp381 of the DFG motif contacts the ligand.
  - ~20-25 residues within 4.5 A; the pocket is highly conserved mouse vs human.

Usage:
    from scripts.load_example import load_example, EXPECTED
    params = load_example()               # dict of run_analysis kwargs
    # then: run_analysis(**params)
"""

EXAMPLE_INFO = {
    "name": "imatinib_abl1",
    "description": "Imatinib (STI-571) bound to ABL1 kinase domain",
    "primary": "1IEP",
    "comparisons": ["2HYY"],
    "ligand_code": "STI",
    "target_name": "ABL1 tyrosine kinase (Abl kinase domain)",
}

# Sanity-check expectations for the regression test (approximate; distances in A).
EXPECTED = {
    "min_contact_residues": 20,
    "must_have_residues": [315, 318, 381],   # gatekeeper Thr, hinge Met, DFG Asp
    "must_have_hbond_residues": [315, 318, 381],
    "is_kinase": True,
    "min_hbonds": 5,
}


def load_example(extended_interactions=False, out_dir="pocket_analysis_imatinib"):
    """Return kwargs for run_analysis reproducing the imatinib-ABL1 benchmark."""
    return {
        "primary": EXAMPLE_INFO["primary"],
        "ligand_code": EXAMPLE_INFO["ligand_code"],
        "comparisons": EXAMPLE_INFO["comparisons"],
        "target_name": EXAMPLE_INFO["target_name"],
        "extended_interactions": extended_interactions,
        "out_dir": out_dir,
    }


def check_expectations(payload):
    """
    Compare a produced payload against EXPECTED. Returns (ok, messages).
    Used by the regression test; prints a pass/fail line per check.
    """
    msgs = []
    ok = True
    summ = payload["summary"]
    resnums = {c["resseq"] for c in payload["contacts"]}
    hb_resnums = {c["resseq"] for c in payload["contacts"] if c["hbonds"]}

    if summ["n_contact_residues"] >= EXPECTED["min_contact_residues"]:
        msgs.append(f"[PASS] {summ['n_contact_residues']} contact residues (>= {EXPECTED['min_contact_residues']})")
    else:
        ok = False
        msgs.append(f"[FAIL] only {summ['n_contact_residues']} contact residues")

    for r in EXPECTED["must_have_residues"]:
        if r in resnums:
            msgs.append(f"[PASS] residue {r} present in pocket")
        else:
            ok = False
            msgs.append(f"[FAIL] residue {r} missing from pocket")

    for r in EXPECTED["must_have_hbond_residues"]:
        if r in hb_resnums:
            msgs.append(f"[PASS] residue {r} forms a candidate H-bond")
        else:
            ok = False
            msgs.append(f"[FAIL] residue {r} has no H-bond")

    if payload["kinase"]["is_kinase"] == EXPECTED["is_kinase"]:
        msgs.append(f"[PASS] kinase detection = {payload['kinase']['is_kinase']}")
    else:
        ok = False
        msgs.append(f"[FAIL] kinase detection = {payload['kinase']['is_kinase']}")

    if summ["n_hbonds"] >= EXPECTED["min_hbonds"]:
        msgs.append(f"[PASS] {summ['n_hbonds']} candidate H-bonds (>= {EXPECTED['min_hbonds']})")
    else:
        ok = False
        msgs.append(f"[FAIL] only {summ['n_hbonds']} candidate H-bonds")

    return ok, msgs
