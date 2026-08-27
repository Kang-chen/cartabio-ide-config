"""Literature search for the keyword evidence skill.

Three input modes, all converging to the same normalized record:

  {
    "paper_id": str,         # canonical id used by the rest of the pipeline
    "id_type": "doi" | "pmid" | "pmcid" | "arxiv" | "local",
    "doi": str | None,
    "pmid": str | None,
    "pmcid": str | None,
    "arxiv_id": str | None,
    "title": str | None,
    "year": int | None,
    "journal": str | None,
    "authors": list[str] | None,
    "pdf_url": str | None,        # best guess; acquire.py may override
    "local_pdf": str | None,      # set in "local" mode
  }

For search mode we hit Europe PMC's REST API (no key needed); for ID mode we
auto-detect the type and look up metadata via the same endpoint. arXiv ids fall
back to the arXiv API.
"""
from __future__ import annotations
import json
import os
import random
import re
import time
import urllib.parse
from typing import Any, Iterable

import requests

from http_policy import polite_get


_CONTACT_EMAIL = os.environ.get(
    "PHYLO_LITERATURE_CONTACT_EMAIL", "research@phylo.ai")
_UA = os.environ.get(
    "LITERATURE_HTTP_USER_AGENT",
    f"phylo-biomni-literature-keyword-evidence/0.2 "
    f"(mailto:{_CONTACT_EMAIL})",
)
_EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_EPMC_ARTICLE = "https://www.ebi.ac.uk/europepmc/webservices/rest/article"

# Free-full-text filter for `open_access_only`. NOT `OPEN_ACCESS:Y`: that is the
# OA-subset flag alone, and it excludes every author manuscript that is in
# PMC/Europe PMC (`isOpenAccess: N`, `inEPMC: Y`) — precisely the `free_to_read`
# population the three-state access design exists to retrieve. Filtering them
# out of the corpus meant the acquisition classifier never got to rescue them.
_EPMC_FREE_FULLTEXT = "(OPEN_ACCESS:Y OR IN_EPMC:Y)"


def _epmc_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept": "application/json"})
    return s


def _epmc_get_json(
    s: requests.Session,
    params: dict[str, Any],
    *,
    max_attempts: int = 3,
    base_sleep_s: float = 1.0,
    timeout: int = 30,
) -> tuple[dict[str, Any] | None, str | None]:
    """GET the Europe PMC search endpoint with bounded exponential backoff.

    Shared by the corpus search and the single-id lookup so both survive the
    same failures: a single 503 must not abort a whole run before any paper is
    acquired. Returns (payload, error). On success: (dict, None). On failure:
    (None, "<reason>"), `transient_`-prefixed when retries were exhausted on a
    retryable condition (429, 5xx, timeout, connection reset, DNS).
    """
    last_err = "unknown"
    for attempt in range(max_attempts):
        try:
            r = polite_get(s, _EPMC_SEARCH, params=params, timeout=timeout)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last_err = f"transient_http_{r.status_code}"
                if attempt + 1 < max_attempts:
                    retry_after = str(r.headers.get("Retry-After") or "").strip()
                    try:
                        provider_delay = max(0.0, float(retry_after))
                    except ValueError:
                        provider_delay = 0.0
                    time.sleep(max(provider_delay, base_sleep_s * (2**attempt))
                               + random.uniform(0.0, base_sleep_s))
                    continue
                return None, last_err
            if r.status_code != 200:
                return None, f"http_{r.status_code}"
            return r.json(), None
        except requests.RequestException as e:
            last_err = f"transient_net_err:{type(e).__name__}"
            if attempt + 1 < max_attempts:
                time.sleep(base_sleep_s * (2**attempt)
                           + random.uniform(0.0, base_sleep_s))
                continue
            return None, last_err
        except ValueError as e:  # malformed JSON body
            return None, f"bad_json:{type(e).__name__}"
    return None, last_err


def detect_id_type(raw_id: str) -> str:
    """Identify what kind of identifier a string represents."""
    s = raw_id.strip()
    # arXiv: e.g. "2305.10403" or "arXiv:2305.10403" or "cs.LG/0001001"
    if s.lower().startswith("arxiv:"):
        return "arxiv"
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", s):
        return "arxiv"
    if re.fullmatch(r"[a-z\-]+/\d{7}", s):
        return "arxiv"
    # PMCID: PMC followed by digits
    if re.fullmatch(r"PMC\d+", s, re.IGNORECASE):
        return "pmcid"
    # DOI: starts with 10. and contains a slash
    if re.fullmatch(r"10\.\d{4,9}/[^\s]+", s):
        return "doi"
    # PMID: pure digits (heuristic)
    if re.fullmatch(r"\d{4,9}", s):
        return "pmid"
    raise ValueError(f"cannot identify id type for {raw_id!r}")


def _normalize_arxiv(s: str) -> str:
    s = s.strip()
    if s.lower().startswith("arxiv:"):
        s = s[6:]
    return s


def _epmc_to_record(r: dict[str, Any]) -> dict[str, Any]:
    """Convert one Europe PMC result row into our normalized record."""
    doi = r.get("doi")
    pmid = r.get("pmid")
    pmcid = r.get("pmcid")
    title = r.get("title")
    year = None
    try:
        year = int(r.get("pubYear")) if r.get("pubYear") else None
    except Exception:
        year = None
    journal = r.get("journalTitle")
    authors_field = r.get("authorString", "")
    authors = [a.strip() for a in authors_field.split(",")] if authors_field else None
    pdf_url = None
    # fullTextUrlList only present in core result type
    ftl = r.get("fullTextUrlList") or {}
    for entry in ftl.get("fullTextUrl", []) or []:
        if entry.get("documentStyle") == "pdf":
            pdf_url = entry.get("url")
            break
    if pmcid:
        paper_id = pmcid
        id_type = "pmcid"
    elif doi:
        paper_id = doi.replace("/", "_")
        id_type = "doi"
    elif pmid:
        paper_id = f"PMID{pmid}"
        id_type = "pmid"
    else:
        paper_id = title[:60] if title else f"unknown_{time.time_ns()}"
        id_type = "unknown"
    return {
        "paper_id": paper_id,
        "id_type": id_type,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "arxiv_id": None,
        "title": title,
        "year": year,
        "journal": journal,
        "authors": authors,
        "pdf_url": pdf_url,
        "local_pdf": None,
    }


def search_europe_pmc(
    query: str,
    max_papers: int = 20,
    year_min: int | None = None,
    year_max: int | None = None,
    open_access_only: bool = True,
) -> list[dict[str, Any]]:
    """Search Europe PMC and return normalized records.

    `open_access_only` keeps its meaning — restrict the corpus to papers whose
    full text is free — but is expressed as `(OPEN_ACCESS:Y OR IN_EPMC:Y)` so
    free-to-read author manuscripts are included rather than filtered out
    before acquisition ever sees them.

    Transient failures (429, 5xx, timeout) are retried with exponential backoff
    via `_epmc_get_json`; only an exhausted or non-retryable failure raises.
    """
    s = _epmc_session()
    q_parts = [query]
    if year_min:
        q_parts.append(f"PUB_YEAR:[{year_min} TO {year_max or 3000}]")
    if open_access_only:
        q_parts.append(_EPMC_FREE_FULLTEXT)
    q = " AND ".join(q_parts)
    params = {
        "query": q,
        "format": "json",
        "resultType": "core",
        "pageSize": min(max_papers, 100),
    }
    data, err = _epmc_get_json(s, params)
    if data is None:
        raise requests.HTTPError(f"Europe PMC search failed: {err}")
    rows = (data.get("resultList") or {}).get("result", []) or []
    out = []
    for r in rows[:max_papers]:
        out.append(_epmc_to_record(r))
    return out


def _epmc_lookup_one(
    s: requests.Session,
    raw: str,
    id_type: str,
    *,
    max_attempts: int = 3,
    base_sleep_s: float = 1.0,
) -> tuple[dict[str, Any] | None, str | None]:
    """Single-id Europe PMC lookup with retry on transient failures.

    Returns (record, error_string). On success: (record, None). On transient
    failure exhausted: (None, "transient_<details>"). On clean "no such id":
    (None, None) — caller fills in an empty stub record.

    Transport-level retries (RequestException, HTTP 5xx incl. 503, HTTP 429)
    live in the shared `_epmc_get_json` helper. On top of that this retries an
    empty result list once: Europe PMC occasionally returns 200 + empty under
    load even for valid IDs, and we cannot tell that from a real "not in index"
    without asking again.
    """
    if id_type == "doi":
        q = f"DOI:{raw}"
    elif id_type == "pmid":
        q = f"EXT_ID:{raw} AND SRC:MED"
    elif id_type == "pmcid":
        q = f"PMCID:{raw}"
    else:
        q = raw
    params = {"query": q, "format": "json", "resultType": "core", "pageSize": 1}
    last_err = None
    for attempt in range(max_attempts):
        data, err = _epmc_get_json(
            s, params, max_attempts=1, base_sleep_s=base_sleep_s)
        if err is not None:
            last_err = err
            if attempt + 1 < max_attempts and err.startswith("transient_"):
                time.sleep(base_sleep_s * (2**attempt)
                           + random.uniform(0.0, base_sleep_s))
                continue
            return None, last_err
        try:
            rows = (data.get("resultList") or {}).get("result", []) or []
            if rows:
                return _epmc_to_record(rows[0]), None
        except Exception as e:
            return None, f"unexpected:{type(e).__name__}:{e}"
        # Empty result list — could be transient or genuine miss. Retry once
        # to disambiguate; if it stays empty, treat as genuine miss.
        if attempt + 1 < max_attempts:
            last_err = "empty_result"
            time.sleep(base_sleep_s * (2**attempt)
                       + random.uniform(0.0, base_sleep_s))
            continue
        return None, None  # genuine miss
    return None, last_err or "exhausted"


def lookup_europe_pmc(ids: Iterable[str]) -> list[dict[str, Any]]:
    """Look up metadata for a list of DOIs/PMIDs/PMCIDs via Europe PMC.

    Transient HTTP failures (429, 5xx, empty-result-then-success) are retried
    automatically with exponential backoff. See `_epmc_lookup_one`.
    """
    s = _epmc_session()
    out = []
    for raw in ids:
        try:
            id_type = detect_id_type(raw)
        except ValueError:
            out.append({
                "paper_id": raw,
                "id_type": "unknown",
                "doi": None, "pmid": None, "pmcid": None, "arxiv_id": None,
                "title": None, "year": None, "journal": None, "authors": None,
                "pdf_url": None, "local_pdf": None,
            })
            continue
        if id_type == "arxiv":
            out.append(lookup_arxiv(_normalize_arxiv(raw)))
            continue
        record, err = _epmc_lookup_one(s, raw, id_type)
        if record is not None:
            out.append(record)
        else:
            stub = {
                "paper_id": raw,
                "id_type": id_type,
                "doi": raw if id_type == "doi" else None,
                "pmid": raw if id_type == "pmid" else None,
                "pmcid": raw if id_type == "pmcid" else None,
                "arxiv_id": None,
                "title": None, "year": None, "journal": None, "authors": None,
                "pdf_url": None, "local_pdf": None,
            }
            if err is not None:
                stub["_lookup_error"] = err
            out.append(stub)
        time.sleep(0.2)  # gentle to Europe PMC between ids
    return out


def lookup_arxiv(arxiv_id: str) -> dict[str, Any]:
    """Look up arXiv metadata. arXiv's API returns Atom XML."""
    try:
        import arxiv as arxiv_pkg
    except ImportError:
        arxiv_pkg = None
    if arxiv_pkg is not None:
        try:
            client = arxiv_pkg.Client()
            search = arxiv_pkg.Search(id_list=[arxiv_id])
            res = next(client.results(search), None)
            if res is not None:
                return {
                    "paper_id": f"arXiv_{arxiv_id}",
                    "id_type": "arxiv",
                    "doi": res.doi,
                    "pmid": None,
                    "pmcid": None,
                    "arxiv_id": arxiv_id,
                    "title": res.title,
                    "year": res.published.year if res.published else None,
                    "journal": "arXiv",
                    "authors": [a.name for a in (res.authors or [])],
                    "pdf_url": res.pdf_url,
                    "local_pdf": None,
                }
        except Exception:
            pass
    # Fallback: synthesize record with the canonical pdf URL
    return {
        "paper_id": f"arXiv_{arxiv_id}",
        "id_type": "arxiv",
        "doi": None, "pmid": None, "pmcid": None,
        "arxiv_id": arxiv_id,
        "title": None, "year": None, "journal": "arXiv", "authors": None,
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        "local_pdf": None,
    }


def from_local(paths: Iterable[str]) -> list[dict[str, Any]]:
    """Build records for local PDF paths (no network)."""
    import pathlib, hashlib
    out = []
    for p in paths:
        path = pathlib.Path(p)
        if not path.exists():
            continue
        # paper_id = stem + short content hash
        try:
            h = hashlib.sha1(path.read_bytes()[:8192]).hexdigest()[:8]
        except Exception:
            h = "00000000"
        paper_id = f"{path.stem}_{h}"
        out.append({
            "paper_id": paper_id,
            "id_type": "local",
            "doi": None, "pmid": None, "pmcid": None, "arxiv_id": None,
            "title": path.stem,
            "year": None, "journal": None, "authors": None,
            "pdf_url": None,
            "local_pdf": str(path.resolve()),
            "access": "user_supplied",
        })
    return out


def dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by normalized DOI then PMID then paper_id."""
    seen_doi = set()
    seen_pmid = set()
    seen_id = set()
    out = []
    for r in records:
        key_doi = (r.get("doi") or "").lower().strip()
        key_pmid = (r.get("pmid") or "").strip()
        key_id = r.get("paper_id") or ""
        if key_doi and key_doi in seen_doi:
            continue
        if key_pmid and key_pmid in seen_pmid:
            continue
        if key_id and key_id in seen_id:
            continue
        if key_doi:
            seen_doi.add(key_doi)
        if key_pmid:
            seen_pmid.add(key_pmid)
        if key_id:
            seen_id.add(key_id)
        out.append(r)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("search")
    sp.add_argument("--query", required=True)
    sp.add_argument("--max-papers", type=int, default=20)
    sp.add_argument("--year-min", type=int)
    sp.add_argument("--year-max", type=int)
    sp.add_argument("--include-paywalled", action="store_true")
    si = sub.add_parser("ids")
    si.add_argument("ids", nargs="+")
    sl = sub.add_parser("local")
    sl.add_argument("paths", nargs="+")
    args = ap.parse_args()
    if args.cmd == "search":
        recs = search_europe_pmc(
            args.query, args.max_papers,
            year_min=args.year_min, year_max=args.year_max,
            open_access_only=not args.include_paywalled,
        )
    elif args.cmd == "ids":
        recs = lookup_europe_pmc(args.ids)
    else:
        recs = from_local(args.paths)
    recs = dedupe(recs)
    print(json.dumps(recs, indent=2))
