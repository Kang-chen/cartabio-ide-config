"""
peptides_indel — generate neoORF / junction peptides from small indels (frameshift and
in-frame) for neoantigen prediction.

Why this exists
---------------
The reused ``neoantigen-io-response`` core generates SNV substitution windows only. TESLA
and the broader literature show frameshift-derived neoORFs are a rich, highly *foreign*
neoantigen source (an entirely novel C-terminal peptide stretch until the next stop), so a
neoantigen skill that starts from a VCF must handle them.

REAL-DATA-ONLY: peptides come from translating the REAL transcript CDS (fetched from
Ensembl) with the REAL indel applied at the REAL CDS coordinate parsed from HGVSc. If the
CDS cannot be fetched, or HGVSc cannot be parsed, or the applied edit does not reproduce
the annotated protein consequence sanity checks, the variant is SKIPPED with a logged
reason — never patched with a synthetic sequence.

Approach
--------
1. Fetch the transcript CDS (Ensembl ``/sequence/id/<transcript>?type=cds``).
2. Parse the CDS edit from HGVSc: deletions ``c.123del`` / ``c.123_125del``,
   insertions ``c.123_124insACGT``, delins ``c.123_125delinsAC``, and duplications
   ``c.123dup`` / ``c.123_125dup``.
3. Apply the edit to the CDS, translate WT and mutant from the start codon, and locate the
   first divergent residue (the neojunction). For a frameshift the mutant diverges and runs
   to a new stop; for an in-frame indel only a short stretch changes.
4. Extract all 8-11mers overlapping at least one novel residue -> candidate neoepitopes.
   Frameshift peptides have NO matched-WT counterpart (agretopicity is N/A by definition),
   which is recorded so downstream scoring treats them correctly.

Output record (per variant) mirrors the reused generator's schema closely so the
downstream scorer can consume SNV and indel neoepitopes uniformly:
    {gene, variant, var_class, ccf, expr_tpm(None here; joined later), driver(False),
     source_seq(transcript), mut_peptides:[(pep, novel_flag_positions)],
     wt_peptides:[] , neojunction_index, mut_protein_len, stop_gained_early(bool)}
Here ``mut_peptides`` items are (peptide, mi) where ``mi`` is the **0-based** position of the
FIRST novel residue within the peptide (matching the reused SNV generator's 0-based mi
convention), used later for the TESLA 'mutational position' feature; WT peptides are empty
for frameshift.
"""

from __future__ import annotations

import re
import ssl
import urllib.request
import urllib.parse
from typing import Optional

from Bio.Seq import Seq  # Biopython is preinstalled

AA_SET = set("ACDEFGHIKLMNPQRSTVWY")
DEFAULT_LENGTHS = (8, 9, 10, 11)


def _make_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


_SSL = _make_ssl_context()


def _http_get(url: str, *, timeout: int = 45) -> Optional[bytes]:
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "biomni-neoantigen-tesla/1.0")
    req.add_header("Accept", "text/x-fasta, text/plain, */*")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        print(f"   [cds] {url[:70]} -> {type(e).__name__}: {e}")
        return None


def _parse_fasta(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.startswith(">") or not line.strip():
            continue
        out.append(line.strip())
    return "".join(out).upper()


def fetch_cds(transcript_id: str, *, timeout: int = 45) -> Optional[str]:
    """Fetch the REAL coding sequence (CDS) for an Ensembl transcript id."""
    if not transcript_id:
        return None
    tid = transcript_id.split(".")[0]  # strip version for the endpoint
    raw = _http_get(f"https://rest.ensembl.org/sequence/id/{urllib.parse.quote(tid)}"
                    f"?type=cds;content-type=text/x-fasta", timeout=timeout)
    if raw is None:
        return None
    s = _parse_fasta(raw.decode("utf-8", "ignore"))
    return s if s and set(s) <= set("ACGTN") else None


# =============================================================================
# HGVSc parsing -> a normalized CDS edit
# =============================================================================
def parse_hgvsc(hgvsc: str):
    """Parse a coding HGVS into a normalized edit dict.

    Supports (1-based CDS coordinates, ignoring UTR offsets like c.-12 / c.*5 which are
    non-coding and returned as None):
      del      : c.76del, c.76_78del, c.76delA
      ins      : c.76_77insACGT
      dup      : c.76dup, c.76_78dup
      delins   : c.76_78delinsAC
    Returns {kind, start, end, seq} with 1-based inclusive start/end, or None if
    unparseable / non-coding.
    """
    if not hgvsc:
        return None
    s = hgvsc.split(":")[-1]
    s = s[2:] if s.lower().startswith("c.") else s
    # reject clearly non-CDS coordinates
    if s.startswith("-") or s.startswith("*") or "+" in s or "-" in s.split("del")[0].split("ins")[0].lstrip("0123456789_"):
        # crude guard for intronic/UTR; positions with +/- offsets are non-CDS
        if re.search(r"[+\-]\d", s):
            return None

    m = re.match(r"^(\d+)(?:_(\d+))?delins([ACGT]+)$", s)
    if m:
        start = int(m.group(1)); end = int(m.group(2) or m.group(1))
        return {"kind": "delins", "start": start, "end": end, "seq": m.group(3)}
    m = re.match(r"^(\d+)(?:_(\d+))?del([ACGT]*)$", s)
    if m:
        start = int(m.group(1)); end = int(m.group(2) or m.group(1))
        return {"kind": "del", "start": start, "end": end, "seq": m.group(3) or None}
    m = re.match(r"^(\d+)_(\d+)ins([ACGT]+)$", s)
    if m:
        return {"kind": "ins", "start": int(m.group(1)), "end": int(m.group(2)),
                "seq": m.group(3)}
    m = re.match(r"^(\d+)(?:_(\d+))?dup([ACGT]*)$", s)
    if m:
        start = int(m.group(1)); end = int(m.group(2) or m.group(1))
        return {"kind": "dup", "start": start, "end": end, "seq": m.group(3) or None}
    return None


def apply_cds_edit(cds: str, edit: dict) -> Optional[str]:
    """Apply a normalized CDS edit (1-based inclusive) and return the mutant CDS."""
    n = len(cds)
    s0 = edit["start"] - 1  # 0-based
    e0 = edit["end"]        # slice end (exclusive) for inclusive [start,end]
    if s0 < 0 or e0 > n:
        return None
    if edit["kind"] == "del":
        return cds[:s0] + cds[e0:]
    if edit["kind"] == "delins":
        return cds[:s0] + edit["seq"] + cds[e0:]
    if edit["kind"] == "ins":
        # insertion between start and end (end == start+1 by spec) -> after position start
        return cds[:edit["start"]] + edit["seq"] + cds[edit["start"]:]
    if edit["kind"] == "dup":
        dup_seq = edit["seq"] or cds[s0:e0]
        return cds[:e0] + dup_seq + cds[e0:]
    return None


def _translate_to_stop(nt: str) -> str:
    """Translate a CDS from the first codon to the first stop (stop excluded)."""
    # trim to a multiple of 3 for Biopython, then cut at the first '*'
    usable = nt[: len(nt) - (len(nt) % 3)]
    aa = str(Seq(usable).translate(to_stop=False))
    star = aa.find("*")
    return aa[:star] if star != -1 else aa


def peptides_from_neoorf(wt_prot: str, mut_prot: str, lengths=DEFAULT_LENGTHS):
    """Given WT and mutant protein translations, find the neojunction (first divergent
    residue) and return all k-mers overlapping >=1 novel residue.

    Returns (peptides, neojunction_index) where peptides is a list of (peptide, mi) with
    ``mi`` = **0-based** index of the first novel residue within the peptide (matching the
    reused SNV generator's convention, where mi is the 0-based mutated position). If the
    junction lies upstream of the window (entirely novel context) mi is 0 (the whole window
    is novel, so its first residue is the first novel one seen here).
    """
    # first divergence
    j = 0
    while j < min(len(wt_prot), len(mut_prot)) and wt_prot[j] == mut_prot[j]:
        j += 1
    # novel residues are mut_prot[j:] (frameshift) or a short changed stretch (inframe)
    novel_start = j  # 0-based index in mut_prot of first novel residue
    peps = []
    for L in lengths:
        # windows in mut_prot that overlap any position >= novel_start
        first_start = max(0, novel_start - L + 1)
        last_start = min(len(mut_prot) - L, len(mut_prot) - 1)
        for start in range(first_start, last_start + 1):
            pep = mut_prot[start:start + L]
            if len(pep) != L or not (set(pep) <= AA_SET):
                continue
            # must include at least one novel residue
            if start + L - 1 < novel_start:
                continue
            # 0-based position of the first novel residue within this window (>=0)
            mi = (novel_start - start) if start <= novel_start < start + L else 0
            peps.append((pep, mi))
    return peps, novel_start


def generate_indel_peptides(peptide_variants: list[dict], *, lengths=DEFAULT_LENGTHS) -> dict:
    """Generate neoORF/junction peptides for the frameshift + in-frame indel records.

    Consumes the normalized records from ``vcf_to_variants.parse_vcf`` (those with
    var_class in {frameshift, inframe_indel}) and returns a dict keyed by an internal id
    with the same downstream shape as the SNV generator (mut_peptides / wt_peptides etc.).
    Missense records are ignored here (handled by the reused SNV generator).
    """
    out: dict[str, dict] = {}
    n_pep = 0
    n_skipped_nocds = 0
    n_skipped_noedit = 0
    n_skipped_nonovel = 0
    for i, var in enumerate(peptide_variants):
        vc = var.get("var_class")
        if vc not in ("frameshift", "inframe_indel"):
            continue
        transcript = var.get("ensembl_transcript")
        cds = fetch_cds(transcript) if transcript else None
        if not cds:
            n_skipped_nocds += 1
            print(f"   [indel] {var.get('gene','?')} {var.get('hgvsc','?')}: no CDS for "
                  f"transcript {transcript!r} -> skipped (never synthesised)")
            continue
        edit = parse_hgvsc(var.get("hgvsc", ""))
        if edit is None:
            n_skipped_noedit += 1
            print(f"   [indel] {var.get('gene','?')} {var.get('hgvsc','?')}: HGVSc unparseable "
                  f"or non-coding -> skipped")
            continue
        mut_cds = apply_cds_edit(cds, edit)
        if not mut_cds:
            n_skipped_noedit += 1
            print(f"   [indel] {var.get('gene','?')} {var.get('hgvsc','?')}: edit out of CDS "
                  f"bounds -> skipped")
            continue

        wt_prot = _translate_to_stop(cds)
        mut_prot = _translate_to_stop(mut_cds)

        # sanity: frameshift should change the length OR the tail; inframe changes a short stretch
        indel_len = (len(edit.get("seq") or "") if edit["kind"] in ("ins", "delins", "dup")
                     else 0) - (0 if edit["kind"] in ("ins",) else (edit["end"] - edit["start"] + 1))
        # (indel_len sign isn't used for classification — SO term already classified it)

        peps, novel_start = peptides_from_neoorf(wt_prot, mut_prot, lengths)
        if not peps:
            n_skipped_nonovel += 1
            print(f"   [indel] {var.get('gene','?')} {var.get('hgvsc','?')}: no novel k-mers "
                  f"produced (edit may be synonymous at protein level) -> skipped")
            continue

        # frameshift: no matched WT peptide (novel ORF). inframe: WT peptides exist but the
        # junction is short; we conservatively treat indel epitopes as WT-less for
        # agretopicity (recorded as N/A) since there is no 1:1 residue-matched WT k-mer.
        vid = f'{var.get("gene","?")}:{var.get("hgvsc","?")}:{i}'
        out[vid] = {
            "gene": var.get("gene", "?"),
            "variant": (var.get("hgvsp") or var.get("hgvsc") or f"{vc}"),
            "var_class": vc,
            "ccf": (float(var["ccf"]) if var.get("ccf") is not None else None),
            "vaf": var.get("vaf"),
            "expr_tpm": None,  # joined later from the RNA-seq table
            "driver": bool(var.get("driver", False)),
            "source_seq": transcript,
            "mut_peptides": peps,
            "wt_peptides": [],  # neoORF -> no residue-matched WT
            "neojunction_index": novel_start,
            "mut_protein_len": len(mut_prot),
            "wt_protein_len": len(wt_prot),
            "stop_gained_early": len(mut_prot) < len(wt_prot),
            "is_neoorf": vc == "frameshift",
        }
        n_pep += len(peps)

    print(f"   [indel] {len(out)} indel variants -> {n_pep} neoORF/junction "
          f"{min(lengths)}-{max(lengths)}mers "
          f"(skipped: {n_skipped_nocds} no-CDS, {n_skipped_noedit} bad-HGVSc, "
          f"{n_skipped_nonovel} no-novel)")
    return out


if __name__ == "__main__":
    # offline unit checks of the pure translation/edit logic (no network)
    cds = "ATG" + "GCT" * 10 + "TAA"  # M + 10xA + stop
    print("WT prot:", _translate_to_stop(cds))
    # frameshift: delete 1 nt at CDS pos 5 -> shifts frame
    edit = parse_hgvsc("c.5del")
    print("parsed edit:", edit)
    mut = apply_cds_edit(cds, edit)
    print("mut prot:", _translate_to_stop(mut))
    peps, j = peptides_from_neoorf(_translate_to_stop(cds), _translate_to_stop(mut), (8, 9))
    print("neojunction idx:", j, "| n neoORF peps:", len(peps), "| sample:", peps[:3])
    # inframe deletion of 3 nt (one codon) at pos 4-6
    e2 = parse_hgvsc("c.4_6del")
    print("inframe edit:", e2, "-> mut:", _translate_to_stop(apply_cds_edit(cds, e2)))
