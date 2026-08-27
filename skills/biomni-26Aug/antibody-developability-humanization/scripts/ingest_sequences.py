"""
ingest_sequences.py - resolve and validate the antibody input.

Input contract (§2):
  * PRIMARY: user-provided VH + VL amino-acid sequences (FASTA or paste).
    Exact sequence is load-bearing - a single wrong residue silently corrupts
    every downstream result, so sequences are validated hard.
  * FALLBACK (name-based): resolve against a STRUCTURED database
    (Thera-SAbDab / IMGT) - never free web search. Any *retrieved* sequence
    must be echoed back with its germline assignment and CONFIRMED by the user
    before the pipeline runs. Retrieval is never a silent input.

This module does not itself pause for confirmation (the agent does, using the
returned `needs_confirmation` flag + the echo block). It only fetches/validates.
"""
from __future__ import annotations
import io
import sys
import json
import argparse
import urllib.request

from ab_core import validate_sequence, detect_species_and_germline


# ---------------------------------------------------------------------------
# FASTA / paste parsing
# ---------------------------------------------------------------------------
def parse_fasta(text: str) -> dict:
    """Return {header: sequence}. Accepts FASTA or a bare single sequence."""
    text = text.strip()
    seqs, name, buf = {}, None, []
    if not text.startswith(">"):
        return {"seq": "".join(text.split())}
    for line in text.splitlines():
        if line.startswith(">"):
            if name is not None:
                seqs[name] = "".join(buf)
            name = line[1:].strip().split()[0] if len(line) > 1 else f"seq{len(seqs)}"
            buf = []
        else:
            buf.append(line.strip())
    if name is not None:
        seqs[name] = "".join(buf)
    return seqs


def guess_vh_vl(seqs: dict):
    """Assign parsed sequences to VH/VL by header hints then by chain typing."""
    vh = vl = None
    for h, s in seqs.items():
        hl = h.lower()
        if any(k in hl for k in ["vh", "heavy", "_h", "hc"]):
            vh = s
        elif any(k in hl for k in ["vl", "light", "_l", "lc", "kappa", "lambda"]):
            vl = s
    if vh and vl:
        return vh, vl
    # fall back to chain typing
    typed = {}
    for h, s in seqs.items():
        d = detect_species_and_germline(s)
        typed[h] = d.get("chain_type")
    for h, ct in typed.items():
        if ct == "H" and vh is None:
            vh = seqs[h]
        elif ct in ("K", "L") and vl is None:
            vl = seqs[h]
    return vh, vl


# ---------------------------------------------------------------------------
# Name-based fallback: Thera-SAbDab (structured DB, NOT web search)
# ---------------------------------------------------------------------------
# Verified endpoint (Thera-SAbDab all-therapeutics CSV: Therapeutic,
# HeavySequence, LightSequence + metadata). Path is under sabdab-sabpred/static.
THERA_CSV = ("https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/"
             "static/downloads/TheraSAbDab_SeqStruc_OnlineDownload.csv")


def fetch_therasabdab_table():
    """Download the Thera-SAbDab all-therapeutics table (VH/VL + metadata).
    Returns (rows, header). Requires network egress to opig.stats.ox.ac.uk.
    """
    req = urllib.request.Request(THERA_CSV,
                                 headers={"User-Agent": "Mozilla/5.0 biomni-ab-skill"})
    with urllib.request.urlopen(req, timeout=60) as fh:
        raw = fh.read().decode("utf-8-sig", "replace")  # strip BOM
    sep = "\t" if raw.count("\t") > raw.count(",") else ","
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    header = [h.strip().lstrip("\ufeff") for h in lines[0].split(sep)]
    rows = []
    for ln in lines[1:]:
        cells = ln.split(sep)
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells)))
    return rows, header


def resolve_by_name(name: str):
    """Look up a therapeutic by (fuzzy) name in Thera-SAbDab.
    Returns (record_or_None, note). VH/VL column names vary by release, so we
    search case-insensitively for heavy/light sequence-like columns."""
    try:
        rows, header = fetch_therasabdab_table()
    except Exception as e:  # egress blocked / DB down
        return None, (f"Could not reach Thera-SAbDab ({e}). Provide VH/VL "
                      f"sequences directly (paste or FASTA).")
    name_l = name.strip().lower()
    # locate a name column
    name_cols = [h for h in header if "name" in h.lower() or "therapeutic" in h.lower()]
    hcol = next((h for h in header if "heavy" in h.lower() and "seq" in h.lower()), None)
    lcol = next((h for h in header if "light" in h.lower() and "seq" in h.lower()), None)
    # fall back to VH/VL column labels
    hcol = hcol or next((h for h in header if h.lower() in ("vh", "heavy")), None)
    lcol = lcol or next((h for h in header if h.lower() in ("vl", "light")), None)
    match = None
    for r in rows:
        for nc in name_cols:
            if name_l == str(r.get(nc, "")).strip().lower():
                match = r
                break
        if match:
            break
    if match is None:  # relaxed contains-match
        for r in rows:
            for nc in name_cols:
                if name_l in str(r.get(nc, "")).strip().lower():
                    match = r
                    break
            if match:
                break
    if match is None:
        return None, f"'{name}' not found in Thera-SAbDab."
    vh = (match.get(hcol) or "").strip() if hcol else ""
    vl = (match.get(lcol) or "").strip() if lcol else ""
    if not (vh and vl):
        return None, (f"'{name}' found in Thera-SAbDab but VH/VL sequence "
                      f"columns are empty for this release; provide sequences "
                      f"directly.")
    return {"name": name, "VH": vh, "VL": vl, "raw": match}, "resolved from Thera-SAbDab"


# ---------------------------------------------------------------------------
# Confirmation echo block
# ---------------------------------------------------------------------------
def germline_echo(vh: str, vl: str):
    """Human-readable echo block used to get explicit user confirmation for a
    *retrieved* sequence before running the pipeline."""
    lines = ["Retrieved sequence - CONFIRM before running the pipeline:"]
    for dom, s in (("VH", vh), ("VL", vl)):
        if not s:
            continue
        d = detect_species_and_germline(s)
        lines.append(f"  {dom} ({len(s)} aa): {s}")
        lines.append(f"      -> species={d.get('species')}, "
                     f"V={d.get('v_gene')}, J={d.get('j_gene')}, "
                     f"chain_type={d.get('chain_type')}")
    lines.append("If this is the intended antibody, confirm to proceed.")
    return "\n".join(lines)


def ingest(vh=None, vl=None, fasta_text=None, name=None):
    """Main entry. Returns dict with VH/VL (validated), warnings, source, and
    needs_confirmation (True iff sequences were retrieved, not user-supplied)."""
    source = "user"
    needs_confirmation = False
    warns = []

    if fasta_text:
        seqs = parse_fasta(fasta_text)
        vh, vl = guess_vh_vl(seqs)
    elif name and not (vh and vl):
        rec, note = resolve_by_name(name)
        warns.append(note)
        if rec is None:
            return {"ok": False, "error": note, "source": "therasabdab"}
        vh, vl = rec["VH"], rec["VL"]
        source = "therasabdab"
        needs_confirmation = True

    out = {"ok": True, "source": source,
           "needs_confirmation": needs_confirmation, "warnings": warns}
    if vh:
        vh, w = validate_sequence(vh, "VH")
        warns.extend(w)
        out["VH"] = vh
    if vl:
        vl, w = validate_sequence(vl, "VL")
        warns.extend(w)
        out["VL"] = vl
    if needs_confirmation:
        out["confirmation_block"] = germline_echo(out.get("VH"), out.get("VL"))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vh")
    ap.add_argument("--vl")
    ap.add_argument("--fasta", help="path to FASTA file")
    ap.add_argument("--name", help="therapeutic name (Thera-SAbDab fallback)")
    ap.add_argument("--out", default="ingest.json")
    a = ap.parse_args()
    ft = open(a.fasta).read() if a.fasta else None
    res = ingest(vh=a.vh, vl=a.vl, fasta_text=ft, name=a.name)
    print(json.dumps(res, indent=2))
    if res.get("confirmation_block"):
        print("\n" + res["confirmation_block"], file=sys.stderr)
    json.dump(res, open(a.out, "w"), indent=2)
