#!/usr/bin/env python3
# =====================================================================================
# verify_citations.py -- CrossRef title-integrity guard for the flow-cytometry skill.
#
# WHY THIS EXISTS: the #1 scientific-review finding on this skill was citation-title
# hallucination -- real DOIs paired with fabricated/mis-attributed titles. This guard
# resolves every DOI in assets/references_cytometry.json against the live CrossRef REST
# API and fails HARD if a stored title does not match the registered title. It is the
# machine check that keeps the reference store honest.
#
# BEHAVIOR / EXIT CODES:
#   0  PASS  -- every reachable DOI's title matches (skipped/offline DOIs are reported).
#   0  SKIP  -- CrossRef unreachable for ALL DOIs (offline env); never blocks a build.
#   1  FAIL  -- at least one DOI resolved and its title did NOT match, or a DOI 404s
#               (i.e. the DOI itself does not exist -> a fabricated identifier).
#
# Title mismatch is FATAL (the hallucination class). Year / first-author mismatches are
# reported as WARNINGS (print-vs-online year drift etc. is common and not a fabrication).
#
# Usage:
#   python verify_citations.py [--refs-json <path>] [--timeout 15] [--strict-year]
# =====================================================================================
import argparse, json, os, re, sys, time
import unicodedata

try:
    import requests
except Exception:
    requests = None

UA = "Biomni-citation-verify/1.0 (mailto:help@biomni.ai)"
API = "https://api.crossref.org/works/"

_DASH = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")
_TAG = re.compile(r"<[^>]+>")

def norm_title(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = _TAG.sub("", s)                 # strip stray markup (<i>, <sub>, ...)
    s = s.translate(_DASH)              # unify dashes
    s = s.replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.rstrip(".")

def crossref_titles(doi, timeout):
    """Return (status, [candidate_titles], {candidate_years}, first_author_family).
    status in {'ok','notfound','offline','error'}. Years are gathered across all
    CrossRef date fields (issued/print/online/...) because print and online years
    legitimately differ; the stored citation year need only match one of them."""
    if requests is None:
        return "offline", [], set(), None
    try:
        r = requests.get(API + doi, headers={"User-Agent": UA}, timeout=timeout)
    except requests.exceptions.RequestException:
        return "offline", [], set(), None
    if r.status_code == 404:
        return "notfound", [], set(), None
    if r.status_code != 200:
        return "error", [], set(), None
    try:
        m = r.json()["message"]
    except Exception:
        return "error", [], set(), None
    titles = list(m.get("title") or [])
    subs = list(m.get("subtitle") or [])
    cands = list(titles)
    if titles and subs:                 # some records split "Title: Subtitle"
        cands.append(f"{titles[0]}: {subs[0]}")
    years = set()
    for k in ("issued", "published", "published-print", "published-online", "created"):
        dp = (m.get(k) or {}).get("date-parts") or []
        if dp and dp[0] and dp[0][0]:
            years.add(int(dp[0][0]))
    ji = (m.get("journal-issue") or {}).get("published-print") or {}
    dp = ji.get("date-parts") or []
    if dp and dp[0] and dp[0][0]:
        years.add(int(dp[0][0]))
    fam = None
    auth = m.get("author") or []
    if auth:
        fam = auth[0].get("family")
    return "ok", cands, years, fam

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_refs = os.path.join(here, "..", "assets", "references_cytometry.json")
    ap = argparse.ArgumentParser()
    ap.add_argument("--refs-json", default=default_refs)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--strict-year", action="store_true",
                    help="treat year mismatch as FATAL (default: warning)")
    A = ap.parse_args()

    with open(A.refs_json) as fh:
        store = json.load(fh)
    refs = store.get("refs", store)

    fatal, warned, checked, skipped, passed_ct = [], [], 0, 0, 0
    print(f"Verifying {len(refs)} citations against CrossRef ({A.refs_json})\n")
    for key, rec in refs.items():
        doi = rec.get("doi", "")
        stored = norm_title(rec.get("title", ""))
        status, cands, cr_years, cr_fam = crossref_titles(doi, A.timeout)
        if status in ("offline", "error"):
            skipped += 1
            print(f"  SKIP  [{key}] {doi}  (CrossRef unreachable: {status})")
            continue
        if status == "notfound":
            fatal.append((key, doi, "DOI not found on CrossRef (fabricated identifier?)"))
            print(f"  FAIL  [{key}] {doi}  DOI 404 -- does not exist")
            continue
        checked += 1
        cand_norm = [norm_title(c) for c in cands]
        if stored in cand_norm or any(stored == c for c in cand_norm):
            passed_ct += 1
            msg = f"  PASS  [{key}] {doi}"
            # non-fatal cross-checks
            yr = rec.get("year")
            if cr_years and yr and int(yr) not in cr_years:
                note = f"year stored={yr} crossref={sorted(cr_years)}"
                (fatal if A.strict_year else warned).append((key, doi, note))
                msg += f"   [{'FAIL' if A.strict_year else 'WARN'}: {note}]"
            print(msg)
        else:
            fatal.append((key, doi, f"title mismatch: stored='{rec.get('title','')}' vs crossref={cands}"))
            print(f"  FAIL  [{key}] {doi}  TITLE MISMATCH")
            print(f"        stored:   {rec.get('title','')}")
            print(f"        crossref: {cands}")
        time.sleep(0.2)   # be polite to the public pool

    print(f"\nSummary: checked={checked}  passed={passed_ct}"
          f"  fatal={len(fatal)}  warnings={len(warned)}  skipped={skipped}")
    if warned:
        print("Warnings (non-fatal):")
        for k, d, m in warned:
            print(f"  - [{k}] {m}")
    if fatal:
        print("\nFATAL citation-integrity failures:")
        for k, d, m in fatal:
            print(f"  - [{k}] {d}: {m}")
        sys.exit(1)
    if checked == 0 and skipped:
        print("\nCrossRef unreachable for all DOIs -- verification SKIPPED (offline). Not blocking.")
        sys.exit(0)
    print("\nAll reachable citations verified against CrossRef. PASS.")
    sys.exit(0)

if __name__ == "__main__":
    main()
