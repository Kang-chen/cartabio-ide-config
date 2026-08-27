"""
self_test.py — validate the genetic-constraint-gating pipeline on a known gene set.

Runs the full resolve -> fetch -> flag pipeline on the 10 genes validated when the
skill was authored, and asserts the expected calls. Because it hits live APIs
(gnomAD, MyGene.info), run it once on first use to confirm the skill works end to end.

    python self_test.py            # analysis-only assertions (fast)
    python self_test.py --full     # also generate figures + PDF into /tmp

Expected (gnomAD v2.1.1, standard thresholds LOEUF<0.35 or pLI>=0.90):
  Flagged YES : SCN1A, ARID1B, SETD2, NF1
  Flagged NO  : PCSK9, CYP2D6, GSTM1  (and MECP2, TP53, PTEN under v2.1.1 standard rule)
  Version shift surfaced for MECP2 and/or TP53 (borderline that strengthens in v4.1)
  Every disease note is either ClinGen-grounded or explicitly 'no curated ... retrieved'
  Drug-target layer: every gene gets a valid KO-tolerance tier; LoF-intolerant genes
    map to a constrained tier (Very low / Low) with High systemic on-target risk, and
    LoF-tolerant genes map to lower risk / direct-inhibition-favourable guidance.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from constraint_analysis import analyze_genes

TEST_GENES = ["SCN1A", "MECP2", "NF1", "ARID1B", "SETD2",
              "TP53", "PTEN", "PCSK9", "CYP2D6", "GSTM1"]

EXPECT_YES = {"SCN1A", "ARID1B", "SETD2", "NF1"}
EXPECT_NO = {"MECP2", "TP53", "PTEN", "PCSK9", "CYP2D6", "GSTM1"}
EXPECT_SHIFT_ANY = {"MECP2", "TP53"}  # at least one of these should carry a version_shift note


def run(full=False):
    print("Running self-test on", len(TEST_GENES), "genes (live gnomAD + MyGene.info)...")
    df = analyze_genes(TEST_GENES)
    fails = []

    # 1) all genes resolved to a Yes/No call
    unresolved = df.loc[~df["LoF_intolerant"].isin(["Yes", "No"]), "gene"].tolist()
    if unresolved:
        fails.append(f"unresolved/failed genes: {unresolved}")

    got_yes = set(df.loc[df["LoF_intolerant"] == "Yes", "gene"])
    got_no = set(df.loc[df["LoF_intolerant"] == "No", "gene"])

    # 2) expected flags
    missing_yes = EXPECT_YES - got_yes
    wrong_yes = (got_yes & EXPECT_NO)
    if missing_yes:
        fails.append(f"expected YES but not flagged: {sorted(missing_yes)}")
    if wrong_yes:
        fails.append(f"expected NO but flagged YES: {sorted(wrong_yes)}")

    # 3) version-shift surfaced for at least one borderline gene
    shift_genes = set(df.loc[df["version_shift"].astype(bool), "gene"])
    if not (EXPECT_SHIFT_ANY & shift_genes):
        fails.append(f"no version_shift surfaced for any of {sorted(EXPECT_SHIFT_ANY)} "
                     f"(saw shifts on {sorted(shift_genes)})")

    # 4) disease notes are grounded or explicitly not-retrieved (never blank)
    blank = df.loc[df["disease_label"].isna() | (df["disease_label"].astype(str).str.strip() == ""), "gene"].tolist()
    if blank:
        fails.append(f"blank disease note (should be grounded or explicit not-retrieved): {blank}")

    # 5) drug-target interpretation layer present, valid, and internally consistent
    valid_tiers = {"Very low (near-essential)", "Low (LoF-intolerant)", "Intermediate", "Tolerant", "Not determined"}
    if "ko_tolerance_tier" not in df.columns:
        fails.append("druggability layer missing (no ko_tolerance_tier column)")
    else:
        bad_tier = df.loc[~df["ko_tolerance_tier"].isin(valid_tiers), "gene"].tolist()
        if bad_tier:
            fails.append(f"invalid ko_tolerance_tier for: {bad_tier}")
        # every LoF-intolerant gene should be a constrained tier with elevated systemic risk
        intol = df[df["LoF_intolerant"] == "Yes"]
        wrong = intol.loc[~intol["ko_tolerance_tier"].isin(
            {"Very low (near-essential)", "Low (LoF-intolerant)"}), "gene"].tolist()
        if wrong:
            fails.append(f"LoF-intolerant genes not in a constrained tier: {wrong}")
        risk_bad = intol.loc[intol["systemic_target_risk"] != "High", "gene"].tolist()
        if risk_bad:
            fails.append(f"LoF-intolerant genes without High systemic on-target risk: {risk_bad}")
        # tolerant genes (if any) should read as lower risk / direct-inhibition-favourable
        tol = df[(df["LoF_intolerant"] == "No") & (df["ko_tolerance_tier"] == "Tolerant")]
        tol_bad = tol.loc[tol["systemic_target_risk"] != "Lower", "gene"].tolist()
        if tol_bad:
            fails.append(f"tolerant genes not marked Lower systemic risk: {tol_bad}")

    cols = ["gene", "LOEUF_v2", "pLI_v2", "LOEUF_v4", "pLI_v4",
            "LoF_intolerant", "ko_tolerance_tier", "systemic_target_risk", "version_shift"]
    print(df[cols].to_string(index=False))
    print()

    if full:
        from constraint_figures import make_all_figures
        from constraint_report import build_report
        figs = make_all_figures(df, "/tmp/constraint_selftest")
        build_report(df, figs, "/tmp/constraint_selftest/report.pdf")
        print("Full artifacts written to /tmp/constraint_selftest/")

    if fails:
        print("SELF-TEST FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    print("SELF-TEST PASSED: flags, version-shift, and grounded disease notes all as expected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(full="--full" in sys.argv))
