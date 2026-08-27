"""
binding_core — real pMHC-I binding + protein-sequence retrieval for the TESLA
neoantigen-prioritization skill.

DESIGN: this skill is a companion/extension of the existing, well-audited
``neoantigen-io-response`` skill. It REUSES that skill's proven core (verified-TLS
sequence fetch, WT-residue validation, MHCflurry binding, and the EngineUnavailable
hard-fail contract) by importing it when that skill is staged. When it is not staged,
a self-contained VENDORED fallback with identical real-data-only semantics is used, so
this skill never silently degrades and never fabricates a sequence or a binding number.

Exposed names (stable API for the rest of this skill):
    EngineUnavailable        — hard-fail exception (mandatory-engine / missing-real-input)
    AA, AA_SET               — amino-acid alphabet
    DEFAULT_LENGTHS          — (8, 9, 10, 11)
    HAS_MHCFLURRY            — bool
    fetch_protein_sequence   — real UniProt/Ensembl protein sequence for a variant dict
    peptides_spanning        — k-mers spanning a centre residue (SNV path)
    generate_peptides        — validated matched mut/WT windows from a case dict (SNV path)
    classify                 — %rank -> strong/binder/weak/non
    predict_binding          — MHCflurry %rank + affinity for a pep_index (mut + matched WT)

REAL-DATA-ONLY: no synthetic/illustrative/anchor-motif path. If MHCflurry is missing or
no supplied HLA allele is supported, binding raises EngineUnavailable and NO numbers are
emitted.
"""

from __future__ import annotations

import os
import ssl
import sys
import json
import math
import urllib.request
import urllib.parse
from typing import Any, Optional


# =============================================================================
# Prefer the audited core from the installed neoantigen-io-response skill.
# =============================================================================
_REUSED_FROM: Optional[str] = None


def _try_import_reused_core():
    """Import the audited core from a staged neoantigen-io-response skill, if present.

    Returns a module-like object exposing the reused names, or None. We search the known
    staged locations plus an env override so the reuse works regardless of where the
    companion skill is mounted.
    """
    candidates = [
        os.environ.get("NEOANTIGEN_IO_SKILL_DIR"),
        "/mnt/skills/user/neoantigen-io-response",
        "/mnt/skills/system/neoantigen-io-response",
    ]
    for base in candidates:
        if not base:
            continue
        scripts_dir = os.path.join(base, "scripts")
        mod_path = os.path.join(scripts_dir, "neoantigen_io.py")
        if not os.path.isfile(mod_path):
            continue
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_neoantigen_io_reused", mod_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            # sanity: the names we depend on must exist
            for name in ("fetch_protein_sequence", "peptides_spanning", "generate_peptides",
                         "predict_binding", "EngineUnavailable", "classify"):
                if not hasattr(mod, name):
                    raise ImportError(f"reused core missing {name}")
            return mod, base
        except Exception:  # noqa: BLE001 — fall through to the vendored copy
            continue
    return None, None


_reused, _REUSED_FROM = _try_import_reused_core()


# =============================================================================
# Shared constants
# =============================================================================
AA = "ACDEFGHIKLMNPQRSTVWY"
AA_SET = set(AA)
DEFAULT_LENGTHS = (8, 9, 10, 11)


# =============================================================================
# Vendored fallback (only defined/used when the reused core is unavailable).
# Semantics are intentionally identical to neoantigen-io-response.
# =============================================================================
class _VendoredEngineUnavailable(RuntimeError):
    """Raised when MHCflurry (or a required real input) is absent. No fallback numbers."""
    pass


try:
    from mhcflurry import Class1PresentationPredictor  # noqa: F401
    HAS_MHCFLURRY = True
except Exception:  # noqa: BLE001
    HAS_MHCFLURRY = False


_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/123 Safari/537.36")


def _make_ssl_context() -> ssl.SSLContext:
    """Verified TLS (cert + hostname on). The 'real sequence' guarantee depends on the
    fetched bytes being authentic, so verification is never disabled."""
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
    req.add_header("User-Agent", _UA)
    req.add_header("Accept", "application/json, text/plain, */*")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        print(f"   [http] {url[:80]} -> {type(e).__name__}: {e}")
        return None


def _http_json(url: str, **kw) -> Optional[Any]:
    b = _http_get(url, **kw)
    if b is None:
        return None
    try:
        return json.loads(b)
    except Exception:
        return None


def _parse_fasta(text: str) -> str:
    seq = []
    for line in text.splitlines():
        if line.startswith(">") or not line.strip():
            continue
        seq.append(line.strip())
    return "".join(seq).upper()


def _vendored_fetch_protein_sequence(var: dict, *, timeout: int = 45) -> Optional[str]:
    """Fetch the REAL canonical protein sequence for a variant's gene (UniProt/Ensembl).
    Resolution order: explicit protein_seq -> uniprot -> ensembl_protein/transcript ->
    gene symbol -> reviewed human UniProt. Never returns a fabricated sequence."""
    seq = var.get("protein_seq")
    if seq and isinstance(seq, str) and set(seq.upper()) <= AA_SET:
        return seq.upper()

    acc = var.get("uniprot")
    if acc:
        raw = _http_get(f"https://rest.uniprot.org/uniprotkb/{urllib.parse.quote(str(acc))}.fasta",
                        timeout=timeout)
        if raw is not None:
            s = _parse_fasta(raw.decode("utf-8", "ignore"))
            if s and set(s) <= AA_SET:
                return s

    ens = var.get("ensembl_protein") or var.get("ensembl_transcript")
    if ens:
        raw = _http_get(f"https://rest.ensembl.org/sequence/id/{urllib.parse.quote(str(ens))}"
                        f"?type=protein;content-type=text/x-fasta", timeout=timeout)
        if raw is not None:
            s = _parse_fasta(raw.decode("utf-8", "ignore"))
            if s and set(s) <= AA_SET:
                return s

    gene = var.get("gene")
    if gene:
        q = f'(gene_exact:{gene}) AND (organism_id:9606) AND (reviewed:true)'
        meta = _http_json(
            "https://rest.uniprot.org/uniprotkb/search?"
            + urllib.parse.urlencode({"query": q, "fields": "accession",
                                      "format": "json", "size": "1"}),
            timeout=timeout,
        )
        results = (meta or {}).get("results") if isinstance(meta, dict) else None
        if results:
            acc = results[0].get("primaryAccession")
            if acc:
                raw = _http_get(f"https://rest.uniprot.org/uniprotkb/{acc}.fasta", timeout=timeout)
                if raw is not None:
                    s = _parse_fasta(raw.decode("utf-8", "ignore"))
                    if s and set(s) <= AA_SET:
                        return s
    return None


def _vendored_peptides_spanning(seq: str, center: int, lengths=DEFAULT_LENGTHS):
    """All k-mers (per length) that include the mutated centre residue.
    Returns (peptide, mutated_position_within_peptide) with 0-based mi."""
    out = []
    for L in lengths:
        for start in range(max(0, center - L + 1), min(center, len(seq) - L) + 1):
            pep = seq[start:start + L]
            if len(pep) == L and set(pep) <= AA_SET:
                out.append((pep, center - start))
    return out


def _aa3to1(x: str) -> Optional[str]:
    _M = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
          "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
          "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
          "TYR": "Y", "VAL": "V"}
    if not x:
        return None
    x = x.strip()
    if len(x) == 1 and x.upper() in AA_SET:
        return x.upper()
    return _M.get(x.upper())


def _parse_aa_change(variant: str):
    """Parse 'V600E' / 'p.Val600Glu' -> (ref1, pos, alt1). Returns (None,None,None) if not a sub."""
    import re
    if not variant:
        return None, None, None
    v = variant.strip()
    if v.startswith("p."):
        v = v[2:]
    m = re.match(r"^([A-Za-z]{3}|[A-Za-z])(\d+)([A-Za-z]{3}|[A-Za-z]|\*|=)$", v)
    if not m:
        return None, None, None
    ref, pos, alt = _aa3to1(m.group(1)), int(m.group(2)), m.group(3)
    if alt == "=":
        return None, None, None  # synonymous
    alt = "*" if alt == "*" else _aa3to1(alt)
    return ref, pos, alt


def _vendored_generate_peptides(case: dict, lengths=DEFAULT_LENGTHS) -> dict:
    """Vendored twin of the reused generate_peptides: validated matched mut/WT windows.

    For each missense variant: fetch the real protein, confirm the stated WT residue at the
    stated 1-based position, apply the substitution, extract 8-11mers spanning the site plus
    matched WT k-mers. WT-mismatch / no-sequence variants are skipped with a reason. Never
    fabricates a residue.
    """
    out: dict = {}
    n_pep = n_nonmis = n_noseq = n_mm = 0
    for i, var in enumerate(case.get("variants", [])):
        if (var.get("type") or "missense").lower() not in ("missense", "snv", "nonsynonymous"):
            n_nonmis += 1
            continue
        ref, pos, alt = _parse_aa_change(var.get("variant", ""))
        if ref is None or alt is None or alt == "*" or pos is None:
            n_nonmis += 1
            continue
        seq = _vendored_fetch_protein_sequence(var)
        if not seq:
            n_noseq += 1
            print(f"   [peptides] {var.get('gene','?')} {var.get('variant','?')}: no real sequence -> skipped")
            continue
        idx = pos - 1
        if idx < 0 or idx >= len(seq) or seq[idx] != ref:
            n_mm += 1
            real = seq[idx] if 0 <= idx < len(seq) else "?"
            print(f"   [peptides] {var.get('gene','?')} {var.get('variant','?')}: real residue "
                  f"{real} != stated WT {ref} at {pos} -> skipped (no substitution applied)")
            continue
        half = max(lengths) - 1
        lo, hi = max(0, idx - half), min(len(seq), idx + half + 1)
        wt_window = seq[lo:hi]
        ci = idx - lo
        mut_window = wt_window[:ci] + alt + wt_window[ci + 1:]
        mut_peps = _vendored_peptides_spanning(mut_window, ci, lengths)
        if not mut_peps:
            continue
        wt_peps = [(wt_window[(ci - mi):(ci - mi) + len(pep)], mi) for pep, mi in mut_peps]
        expr = var.get("expr_tpm")
        try:
            expr = float(expr) if expr is not None else None
        except (TypeError, ValueError):
            expr = None
        vid = f'{var.get("gene","?")}:{var.get("variant","?")}:{i}'
        out[vid] = {
            "gene": var.get("gene", "?"), "variant": var.get("variant", f"{ref}{pos}{alt}"),
            "ref": ref, "alt": alt, "pos": pos,
            "ccf": (float(var["ccf"]) if var.get("ccf") is not None else None),
            "expr_tpm": expr, "driver": bool(var.get("driver", False)),
            "source_seq": var.get("uniprot") or var.get("ensembl_transcript") or var.get("gene"),
            "mut_peptides": mut_peps, "wt_peptides": wt_peps,
        }
        n_pep += len(mut_peps)
    print(f"   [peptides] {len(out)} validated missense variants -> {n_pep} real mutant "
          f"{min(lengths)}-{max(lengths)}mers (skipped: {n_nonmis} non-missense, "
          f"{n_noseq} no-sequence, {n_mm} WT-mismatch)")
    return out


def _vendored_classify(rank: float) -> str:
    return "strong" if rank < 0.5 else "binder" if rank < 2.0 else "weak" if rank < 10 else "non"


def _predict_drop_nan(predictor, peptides: list, alleles: list):
    """Run MHCflurry presentation prediction, dropping peptide/allele pairs that yield NaN.

    Some MHCflurry backends (e.g. PyTorch where ``NNPACK`` is unsupported on the host CPU) emit NaN
    affinities for certain peptide/allele combinations; MHCflurry's presentation LogisticRegression
    then raises ``ValueError: Input X contains NaN``. Rather than impute (which would violate the
    real-data-only contract) we isolate and DROP the offending pairs: try the fast batch call first,
    and only if it raises fall back to per-allele then per-peptide scoring, skipping any peptide that
    errors or produces a NaN score. Returns ``(concatenated_df, n_dropped)``.
    """
    import pandas as pd
    allele_map = {a: [a] for a in alleles}
    try:
        df = predictor.predict(peptides=peptides, alleles=allele_map,
                               include_affinity_percentile=True)
        return df, 0
    except ValueError as e:
        if "NaN" not in str(e):
            raise
    # Fallback: score each allele independently; within a failing allele, score each peptide alone.
    frames, dropped = [], 0
    for a in alleles:
        try:
            frames.append(predictor.predict(peptides=peptides, alleles={a: [a]},
                                            include_affinity_percentile=True))
            continue
        except ValueError as e:
            if "NaN" not in str(e):
                raise
        for p in peptides:
            try:
                sub = predictor.predict(peptides=[p], alleles={a: [a]},
                                        include_affinity_percentile=True)
                frames.append(sub)
            except ValueError as e:
                if "NaN" not in str(e):
                    raise
                dropped += 1   # this peptide/allele pair is NaN on this backend -> drop it
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df, dropped


def _vendored_predict_binding(pep_index: dict, hla: list) -> tuple:
    """MHCflurry %rank + affinity for a pep_index (mut + matched WT), mandatory engine.

    Same contract as the reused predict_binding:
    returns (rank_cache, affinity_cache, engine) with caches keyed by (peptide, allele).
    Raises _VendoredEngineUnavailable if MHCflurry is absent, HLA empty, no peptides, or no
    supported allele.
    """
    if not HAS_MHCFLURRY:
        raise _VendoredEngineUnavailable(
            "MHCflurry is required and not installed. Install it "
            "(`pip install mhcflurry && mhcflurry-downloads fetch`). This skill produces "
            "binding numbers only from real MHCflurry runs — no synthetic fallback.")
    if not hla:
        raise _VendoredEngineUnavailable(
            "No real HLA-I genotype provided. A neoepitope is only meaningful against the "
            "patient's own HLA-I. Pass a real genotype (e.g. ['HLA-A*02:01', ...]).")
    all_peps: set[str] = set()
    for v in pep_index.values():
        all_peps.update(p for p, _ in v.get("mut_peptides", []))
        all_peps.update(p for p, _ in v.get("wt_peptides", []))
    uniq = sorted({p for p in all_peps if p and set(p) <= AA_SET and 8 <= len(p) <= 15})
    if not uniq:
        raise _VendoredEngineUnavailable(
            "No real peptides to score. This skill never fabricates peptides.")
    predictor = Class1PresentationPredictor.load()
    supported = set(getattr(predictor, "supported_alleles", []) or [])
    use_alleles = [a for a in hla if (not supported or a in supported)]
    if not use_alleles:
        raise _VendoredEngineUnavailable(
            f"None of the provided HLA-I alleles are supported by the installed MHCflurry "
            f"models ({sorted(hla)}). Provide 4-digit supported alleles or re-fetch models.")
    df, n_dropped_nan = _predict_drop_nan(predictor, uniq, use_alleles)
    rank_col = ("presentation_percentile" if "presentation_percentile" in df.columns
                else "affinity_percentile")
    aff_col = "affinity" if "affinity" in df.columns else None
    rank_cache, affinity_cache = {}, {}
    for _, row in df.iterrows():
        val = row[rank_col]
        if val is None or (isinstance(val, float) and math.isnan(val)):
            continue  # real-data-only: a NaN score is not a score -> drop, never impute
        key = (row["peptide"], row["sample_name"])
        rank_cache[key] = float(val)
        if aff_col is not None:
            try:
                a = float(row[aff_col])
                if not math.isnan(a):
                    affinity_cache[key] = a
            except (TypeError, ValueError):
                pass
    extra = f"; dropped {n_dropped_nan} NaN peptide-allele score(s)" if n_dropped_nan else ""
    print(f"   [binding] MHCflurry — {len(uniq)} real peptides x {len(use_alleles)} HLA-I "
          f"alleles (commercial-clean; no fallback){extra}")
    return rank_cache, affinity_cache, "MHCflurry (Apache-2.0)"


# =============================================================================
# Public API — wired to the reused core when available, else the vendored copy.
# =============================================================================
if _reused is not None:
    EngineUnavailable = _reused.EngineUnavailable
    fetch_protein_sequence = _reused.fetch_protein_sequence
    peptides_spanning = _reused.peptides_spanning
    generate_peptides = _reused.generate_peptides
    predict_binding = _reused.predict_binding
    classify = _reused.classify
    HAS_MHCFLURRY = getattr(_reused, "HAS_MHCFLURRY", HAS_MHCFLURRY)
else:
    EngineUnavailable = _VendoredEngineUnavailable
    fetch_protein_sequence = _vendored_fetch_protein_sequence
    peptides_spanning = _vendored_peptides_spanning
    generate_peptides = _vendored_generate_peptides
    predict_binding = _vendored_predict_binding
    classify = _vendored_classify


def core_provenance() -> str:
    """Human-readable note on whether the audited core or the vendored copy is in use."""
    if _REUSED_FROM:
        return f"reused audited core from {_REUSED_FROM}"
    return "vendored core (neoantigen-io-response not staged; identical real-data-only semantics)"


if __name__ == "__main__":
    print("binding_core provenance:", core_provenance())
    print("HAS_MHCFLURRY:", HAS_MHCFLURRY)
    print("DEFAULT_LENGTHS:", DEFAULT_LENGTHS)
    # quick offline check of the pure helpers
    s = "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQ"
    peps = peptides_spanning(s, 10, (9,))
    print("peptides_spanning sample:", peps[:3], "…", len(peps), "total")
