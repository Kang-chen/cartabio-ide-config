"""OpenFDA / FAERS query layer.

Encapsulates the OpenFDA drug endpoints and the (non-obvious) API rules that make
FAERS disproportionality analysis work reliably:

  * Faceted term counts REQUIRE the `.exact` field suffix
    (``patient.reaction.reactionmeddrapt.exact``) and ``limit <= 500``.
  * Plain (non-count) queries return ``meta.results.total`` for 2x2 cell "a".
  * OpenFDA encodes apostrophes oddly; terms such as "ADDISON'S DISEASE" can
    400. We retry with apostrophe variants (``^`` -> ``'`` / right single quote,
    drop the possessive) across both the ``.exact`` and plain fields.
  * Rate limiting (HTTP 429) -> exponential-ish backoff. 404 -> None. 400 ->
    None after variant attempts.

Public functions
----------------
- ``get_total(search)``            -> int report count for a query
- ``get_term_counts(search, n)``   -> {reaction_term: count} facet
- ``count_single_term(search, term)`` -> int cases of one reaction under a query
- ``drug_event_search(names)``     -> OpenFDA search string for a drug name list
- ``background_search(comparator)``-> search string for the disproportionality background
- ``fetch_drug_label(name)``       -> raw /drug/label.json record (for grounding)

All functions accept an optional ``api_key`` (OpenFDA key raises rate limits).
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Dict, Iterable, List, Optional

import requests

EVENT_URL = "https://api.fda.gov/drug/event.json"
LABEL_URL = "https://api.fda.gov/drug/label.json"

REACTION_FIELD = "patient.reaction.reactionmeddrapt"
REACTION_FIELD_EXACT = REACTION_FIELD + ".exact"
MAX_COUNT_LIMIT = 500          # OpenFDA rejects count limit > 500
DEFAULT_TIMEOUT = 60


# --------------------------------------------------------------------------- #
# low-level request with retry/backoff
# --------------------------------------------------------------------------- #
def _request(url: str, params: dict, api_key: Optional[str] = None,
             max_retries: int = 5) -> Optional[dict]:
    """GET with retry. Returns parsed JSON, or None for 404/empty results.

    Raises RuntimeError only after exhausting retries on 429/5xx.
    """
    p = dict(params)
    if api_key:
        p["api_key"] = api_key
    last_exc = None
    for i in range(max_retries):
        try:
            r = requests.get(url, params=p, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as e:            # network hiccup
            last_exc = e
            time.sleep(2 + i * 2)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            # OpenFDA returns 404 when a query legitimately matches nothing
            return None
        if r.status_code == 429:                          # rate limited
            time.sleep(2 + i * 3)
            continue
        if r.status_code == 400:                          # bad query (e.g. apostrophe)
            return {"__http400__": True}
        if 500 <= r.status_code < 600:                    # transient server error
            time.sleep(2 + i * 2)
            continue
        # any other status: surface it
        raise RuntimeError(f"OpenFDA HTTP {r.status_code} for {url} params={params}")
    if last_exc:
        raise RuntimeError(f"OpenFDA request failed after {max_retries} retries: {last_exc}")
    raise RuntimeError(f"OpenFDA request failed after {max_retries} retries (rate limit / 5xx).")


# --------------------------------------------------------------------------- #
# totals & facet counts
# --------------------------------------------------------------------------- #
def get_total(search: str, api_key: Optional[str] = None) -> int:
    """Total number of FAERS reports matching ``search`` (2x2 cell math)."""
    js = _request(EVENT_URL, {"search": search, "limit": 1}, api_key)
    if not js or js.get("__http400__"):
        return 0
    return int(js.get("meta", {}).get("results", {}).get("total", 0))


def get_term_counts(search: str, n: int = MAX_COUNT_LIMIT,
                    api_key: Optional[str] = None) -> Dict[str, int]:
    """Faceted reaction-term counts for a query. Requires the .exact field.

    Returns ``{REACTION_TERM (upper-case): count}`` (top ``n``, n<=500).
    """
    n = min(int(n), MAX_COUNT_LIMIT)
    js = _request(EVENT_URL,
                  {"search": search, "count": REACTION_FIELD_EXACT, "limit": n},
                  api_key)
    if not js or js.get("__http400__"):
        return {}
    out = {}
    for row in js.get("results", []):
        out[str(row["term"]).upper()] = int(row["count"])
    return out


def _apostrophe_variants(term: str) -> List[str]:
    """Candidate spellings for a reaction term with an apostrophe/possessive.

    OpenFDA sometimes stores apostrophes as ``^``. Try the raw term plus
    common normalizations so possessive terms (Addison's, Kaposi's, ...) resolve.
    """
    variants = [term]
    if "^" in term:
        variants += [term.replace("^", "'"),
                     term.replace("^", "\u2019"),   # right single quote
                     term.replace("^S", "S").replace("^s", "s")]
    if "'" in term:
        variants += [term.replace("'", "^"),
                     term.replace("'", "\u2019"),
                     term.replace("'S", "S").replace("'s", "s")]
    if "\u2019" in term:
        variants += [term.replace("\u2019", "'"), term.replace("\u2019", "^")]
    # de-dup, preserve order
    seen, uniq = set(), []
    for v in variants:
        if v not in seen:
            seen.add(v); uniq.append(v)
    return uniq


def count_single_term(search: str, term: str,
                      api_key: Optional[str] = None) -> int:
    """Count reports matching ``search`` AND a single reaction ``term``.

    Robust to apostrophe encoding: tries variants across .exact and plain fields.
    Returns 0 if nothing resolves.
    """
    for variant in _apostrophe_variants(term):
        q = urllib.parse.quote(variant)
        for field in (REACTION_FIELD_EXACT, REACTION_FIELD):
            full = f'{search} AND {field}:"{q}"' if search else f'{field}:"{q}"'
            js = _request(EVENT_URL, {"search": full, "limit": 1}, api_key)
            if js and not js.get("__http400__"):
                tot = int(js.get("meta", {}).get("results", {}).get("total", 0))
                if tot > 0:
                    return tot
    return 0


# --------------------------------------------------------------------------- #
# search-string builders
# --------------------------------------------------------------------------- #
def _drug_clause(name: str) -> str:
    """Match a drug by generic OR brand name (quoted, phrase match)."""
    q = urllib.parse.quote(name)
    return (f'(patient.drug.openfda.generic_name:"{q}" '
            f'OR patient.drug.openfda.brand_name:"{q}")')


def drug_event_search(names: Iterable[str]) -> str:
    """OpenFDA search string matching ANY of the given drug names."""
    names = [n for n in names if n and str(n).strip()]
    if not names:
        raise ValueError("drug_event_search: empty name list")
    if len(names) == 1:
        return _drug_clause(names[0])
    return "(" + " OR ".join(_drug_clause(n) for n in names) + ")"


def background_search(comparator: Optional[Iterable[str]] = None) -> str:
    """Background (denominator) search for disproportionality.

    ``comparator=None`` -> full FAERS with a coded reaction (standard).
    A list -> restrict background to that comparator drug set (active comparator).
    """
    if comparator:
        return drug_event_search(comparator)
    return f"_exists_:{REACTION_FIELD}"


# --------------------------------------------------------------------------- #
# drug label endpoint (for automated ADR grounding)
# --------------------------------------------------------------------------- #
def fetch_drug_label(name: str, api_key: Optional[str] = None) -> Optional[dict]:
    """Return the first /drug/label.json record for a drug name, or None.

    Useful fields: ``adverse_reactions``, ``boxed_warning``, ``warnings_and_cautions``.
    """
    q = urllib.parse.quote(name)
    for field in ("openfda.generic_name", "openfda.brand_name",
                  "openfda.substance_name"):
        js = _request(LABEL_URL, {"search": f'{field}:"{q}"', "limit": 1}, api_key)
        if js and not js.get("__http400__") and js.get("results"):
            return js["results"][0]
    return None


if __name__ == "__main__":                                 # tiny smoke test
    import sys
    drug = sys.argv[1] if len(sys.argv) > 1 else "upadacitinib"
    s = drug_event_search([drug])
    print(f"search: {s}")
    print(f"total reports: {get_total(s):,}")
    tc = get_term_counts(s, 5)
    print("top 5 reactions:", list(tc.items())[:5])
    print("background (full):", f"{get_total(background_search()):,}")
