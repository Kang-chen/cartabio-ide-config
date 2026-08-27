"""
Option 3 helper: sanity-check a candidate sgRNA against the de-novo design rules.

This is a lightweight rule checker (length, GC 40-60%, TTTT terminator, >4 homopolymer runs,
and PAM verification for a given enzyme). It is NOT a genome-wide off-target search — use
Cas-OFFinder / CRISPOR or CRISPick's precomputed off-target ranks for real specificity.
"""

from __future__ import annotations

import re

# Enzyme -> (expected protospacer length range, PAM regex, PAM side).
# PAM regex uses IUPAC: N=ACGT, V=ACG, R=AG.
_IUPAC = {"N": "[ACGT]", "V": "[ACG]", "R": "[AG]", "Y": "[CT]", "W": "[AT]",
          "S": "[GC]", "K": "[GT]", "M": "[AC]"}

ENZYMES = {
    "SpCas9":     {"len": (20, 20), "pam": "NGG",    "side": "3'"},
    "SaCas9":     {"len": (20, 21), "pam": "NNGRRT", "side": "3'"},
    "AsCas12a":   {"len": (23, 25), "pam": "TTTV",   "side": "5'"},
    "enAsCas12a": {"len": (23, 25), "pam": "TTTV",   "side": "5'"},
}


def _pam_to_regex(pam: str) -> str:
    return "".join(_IUPAC.get(b, b) for b in pam.upper())


def gc_content(seq: str) -> float:
    seq = seq.upper()
    if not seq:
        return 0.0
    return 100.0 * (seq.count("G") + seq.count("C")) / len(seq)


def check_design_rules(protospacer: str, enzyme: str = "SpCas9", pam: str | None = None) -> dict:
    """
    Check one candidate protospacer against the de-novo rules.

    Parameters
    ----------
    protospacer : str
        The guide/protospacer sequence (without the PAM), 5'->3'.
    enzyme : str
        One of SpCas9, SaCas9, AsCas12a, enAsCas12a.
    pam : str, optional
        The observed PAM in the genome flanking the protospacer. If given, it is checked
        against the enzyme's PAM pattern.

    Returns
    -------
    dict: {'passes': bool, 'checks': {name: (ok, detail)}, 'enzyme': ..., 'gc': float}
    """
    seq = protospacer.upper().strip()
    spec = ENZYMES.get(enzyme)
    checks: dict[str, tuple[bool, str]] = {}

    if spec is None:
        return {"passes": False, "enzyme": enzyme, "gc": None,
                "checks": {"enzyme_known": (False, f"Unknown enzyme '{enzyme}'. "
                                            f"Known: {list(ENZYMES)}")}}

    lo, hi = spec["len"]
    checks["length"] = (lo <= len(seq) <= hi,
                        f"{len(seq)} bp (expected {lo}-{hi} for {enzyme})")

    gc = gc_content(seq)
    checks["gc_content"] = (40.0 <= gc <= 60.0, f"{gc:.0f}% (target 40-60%)")

    checks["no_TTTT"] = ("TTTT" not in seq, "TTTT terminator present" if "TTTT" in seq
                         else "no TTTT run")

    homo = re.search(r"(A{5,}|C{5,}|G{5,}|T{5,})", seq)
    checks["no_long_homopolymer"] = (homo is None,
                                     f"{homo.group(0)} run" if homo else "no run >4 nt")

    checks["valid_bases"] = (re.fullmatch(r"[ACGT]+", seq) is not None,
                             "non-ACGT characters present" if not re.fullmatch(r"[ACGT]+", seq)
                             else "all ACGT")

    if pam is not None:
        rx = _pam_to_regex(spec["pam"])
        ok = re.fullmatch(rx, pam.upper()) is not None
        checks["pam"] = (ok, f"observed '{pam.upper()}' vs required {spec['pam']} "
                         f"({spec['side']} of target)")

    passes = all(ok for ok, _ in checks.values())
    return {"passes": passes, "enzyme": enzyme, "gc": gc, "checks": checks}


def format_report(result: dict) -> str:
    lines = [f"Enzyme: {result['enzyme']}   Overall: {'PASS' if result['passes'] else 'FAIL'}"]
    for name, (ok, detail) in result["checks"].items():
        lines.append(f"  [{'OK ' if ok else 'XX '}] {name}: {detail}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    seq = sys.argv[1] if len(sys.argv) > 1 else "GAGGTTGTGAGGCGCTGCCC"
    enz = sys.argv[2] if len(sys.argv) > 2 else "SpCas9"
    pam = sys.argv[3] if len(sys.argv) > 3 else None
    print(format_report(check_design_rules(seq, enz, pam)))
