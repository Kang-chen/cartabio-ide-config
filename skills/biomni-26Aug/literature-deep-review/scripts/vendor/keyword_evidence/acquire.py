"""Directly retrievable full-text acquisition for the keyword/deep-dive engines.

Stop at first validated hit. Two kinds of full text are accepted:
  * a PDF  -> saved as <paper_id>.pdf   (record["local_pdf"])
  * JATS XML -> saved as <paper_id>.xml (record["local_xml"], parser="jats-xml")

Waterfall order (never supplies credentials or circumvents an access control):
  0. If the record has a DOI but no PMCID, resolve PMCID/PMID from the DOI via
     the Europe PMC search API so the PMCID-gated routes below can fire        [ID]
  1. record["pdf_url"] (including a direct PDF returned by Exa)              [PDF]
  2. Europe PMC fullTextXML (PMCID), the documented programmatic route        [XML]
  3. NCBI PMC OA service (oa.fcgi): advertised PDF, else .tar.gz package      [PDF]
  4. Europe PMC ?pdf=render only as a last PMCID fallback                     [PDF]
  5. Record/publisher landing URLs: accept a declared PDF link when the server
     actually returns validated PDF bytes without authentication              [PDF]
  6. Unpaywall (hardened): all oa_locations, publisher/published first, with
     a one-hop landing-page -> citation_pdf_url follow; if a location is a PMC
     article, also try its JATS full-text XML                                [PDF/XML]
  7. OpenAlex: best_oa_location + any location OpenAlex flags `is_oa` (its
     primary_location is routinely the publisher paywall, so the flag is
     mandatory), same one-hop landing follow; PMC->JATS XML too              [PDF/XML]
  8. bioRxiv/medRxiv API                                                     [PDF]
  9. arXiv direct                                                            [PDF]
 10. Otherwise log to not_retrieved with the full reason chain.

Step 0 is the key robustness fix: records from LiteratureSearch/Consensus/Exa
frequently carry only a DOI. Without a PMCID, routes 2-4 (including the JATS XML
route that recovers papers whose publisher PDF is 403) were silently skipped,
so a fully open PMC copy went unused. Resolving the PMCID first closes that gap.

Why the XML route matters: many papers whose *publisher* PDF returns HTTP 403
are fully open as JATS XML in the PMC archive (CC-BY / CC0 / NIH-open). Parsing
that XML is a legitimate OA route; it uses the copy the archive itself serves.

Supplementary figures PDF (after an XML win)
----------------------------------------
JATS XML carries figure *captions* but no page geometry, so `parse_jats` cannot
crop a figure image out of it (`image_path` is always None). A paper that wins
through XML therefore used to contribute captions and never a figure crop, which
starved the report of embedded paper figures. After an XML win we now make a
best-effort, time-bounded, non-fatal *supplementary* fetch of a PDF purely for
figure cropping (`record["figures_pdf"]`). It is deliberately separate from
`record["local_pdf"]`: the XML stays the TEXT source (it is cleaner) and
`parser_hint` is untouched. The supplementary fetch uses the same rule as the
main fetch: accept only validated PDF bytes served without authentication.

Access state (three-way, not two-way)
-------------------------------------
"Not in the PMC Open Access subset" is NOT the same as "paywalled". Author
manuscripts (`isOpenAccess: N` but `inPMC`/`inEPMC: Y`) are legally and freely
readable in the archive. Collapsing the two produced reports that claimed a
paywall for papers anyone can read. Every record therefore carries
`access_state`, classified from the Europe PMC search API:

  * `oa_licensed`     — `isOpenAccess: Y`; in the OA subset / CC-licensed.
  * `free_to_read`    — a validated full-text file was served without
                        authentication, but no reusable open licence was
                        established; evidence may be read/OCRed, not called OA.
  * `not_retrievable` — genuinely not obtainable (e.g. not in PMC at all).

`free_to_read` is an access fact, not a licence claim. A publisher PDF that a
fresh unauthenticated session serves directly may be read and OCRed; the
acquirer never submits credentials, defeats a challenge, or bypasses a
technical control. Figure reproduction remains a separate recorded policy.
Correspondingly, a miss is labelled `paywalled` vs `retrieval_failed`.

Polite UA + contact email. Europe PMC request starts share a process-safe local
pacer; transient retries honor Retry-After and add exponential backoff with
jitter. Validates a PDF by the first %PDF bytes; validates XML by parsing it
and requiring a JATS <article> root whose <body> holds real content — an
abstract-only record is not full text and is never accepted as one. The
landing-page hop follows only a PDF URL the page itself declares and accepts it
only when a fresh session receives actual PDF bytes. Also records a canonical
`landing_url` (doi.org/<doi>) and, on
success, `oa_full_url` for hyperlinking in reports — set for EVERY record,
retrieved or not, so downstream references are always clickable.
"""
from __future__ import annotations
import contextlib
import json
import os
import pathlib
import random
import re
import tempfile
import time
from typing import Any, Iterator
from xml.etree import ElementTree as ET

import requests

from http_policy import polite_get

try:  # POSIX advisory locking for the shared acquisition cache.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

_DEFAULT_CONTACT_EMAIL = "research@phylo.ai"
# A real, monitored contact mailbox — Unpaywall/OpenAlex throttle placeholder
# addresses. Override with UNPAYWALL_EMAIL / OPENALEX_EMAIL if desired.
_CONTACT_EMAIL = (
    os.environ.get("PHYLO_LITERATURE_CONTACT_EMAIL")
    or os.environ.get("UNPAYWALL_EMAIL")
    or os.environ.get("OPENALEX_EMAIL")
    or _DEFAULT_CONTACT_EMAIL
)
_UA = os.environ.get(
    "LITERATURE_HTTP_USER_AGENT",
    f"phylo-biomni-literature-deep-review/0.3 (mailto:{_CONTACT_EMAIL})",
)
_UNPAYWALL_EMAIL = _CONTACT_EMAIL
_EPMC_FULLTEXT_XML = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
_EPMC_RENDER_PDF = "https://europepmc.org/articles/{pmcid}?pdf=render"
_PMC_ARTICLE = "https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"

# ---------------------------------------------------------------------------
# Access states. Three-way, because "not in the PMC Open Access subset" is not
# the same thing as "paywalled" — see the module docstring.
# ---------------------------------------------------------------------------
ACCESS_OA_LICENSED = "oa_licensed"      # Europe PMC isOpenAccess=Y (CC / OA subset)
ACCESS_FREE_TO_READ = "free_to_read"    # retrieved without auth; no OA licence claim
ACCESS_NOT_RETRIEVABLE = "not_retrievable"  # genuinely could not be obtained
# Not one of the three review states: only for records that bypassed the
# waterfall entirely (a user-supplied local file), where we have no evidence.
ACCESS_UNKNOWN = "unknown"

# Miss kinds for `_not_retrieved_reason`. "paywalled" is now a CLAIM about the
# paper, made only when nothing indicates the full text is free; a paper the
# archive serves for free that we simply failed to fetch is `retrieval_failed`.
MISS_PAYWALLED = "paywalled"
MISS_RETRIEVAL_FAILED = "retrieval_failed"

# Set on the `_epmc_access` info dict when the classification CALL failed
# (outage, rate limit, schema change) rather than the DOI not being indexed.
# An outage is not evidence of a paywall — see `_classify_miss`.
ACCESS_LOOKUP_ERROR_KEY = "lookup_error"

# ---------------------------------------------------------------------------
# Supplementary figures-PDF fetch (after an XML win). Statuses recorded on the
# record so downstream can say WHY a paper has no figures instead of silently
# showing none.
# ---------------------------------------------------------------------------
FIGURES_PDF_OK = "ok"                    # a PDF was obtained for cropping
FIGURES_PDF_UNAVAILABLE = "unavailable"  # retrieval routes tried, none served a PDF
# No supplementary attempt was made: the text source is already a PDF (so
# `local_pdf` is what figures come from), or there was no PMCID / no time left.
FIGURES_PDF_SKIPPED = "skipped"
_FIGURES_PDF_CACHE_SUFFIX = "::figures_pdf"
try:
    # Wall-clock budget for the whole supplementary attempt. It is a nice-to-
    # have, so it must never dominate acquisition time.
    FIGURES_PDF_BUDGET_S = float(os.environ.get("FIGURES_PDF_BUDGET_S", "45"))
except (TypeError, ValueError):
    FIGURES_PDF_BUDGET_S = 45.0

# ---------------------------------------------------------------------------
# Persistent acquisition cache (positive + negative).
#
# The acquisition waterfall is network-bound and, for paywalled papers, walks
# the entire provider chain (EPMC id-resolve -> JATS XML -> PMC OA -> render ->
# PMC OA -> Unpaywall -> OpenAlex -> bioRxiv -> arXiv) before giving up. When a
# review is re-run (steering the corpus, adding papers, re-deriving support
# states), acquisition was previously re-attempted from scratch every time, so
# the *failed* papers re-paid the full walk on every cycle — the dominant time
# sink in practice. This memo records, per paper_id:
#   * ok           -> which OA source won + the local file, so we reuse it
#   * not_retrieved-> the reason + timestamp, so we skip the walk within a TTL
# Negative entries expire after a TTL that depends on WHY we came back empty
# (see `_neg_ttl_days`): a genuine paywall is a stable fact about the paper, a
# retrieval failure is a fact about us at one moment. Positive entries are only
# trusted while the referenced local file still exists on disk.
# `--refresh-acquisition` (see evidence_first.py) forces a full re-walk by
# ignoring negative cache hits.
#
# Acquisition runs in a ProcessPoolExecutor, so several workers read-modify-write
# this file concurrently; every write therefore re-reads and merges under an
# exclusive lock and lands via a uniquely-named temp file (see
# `_save_acquire_cache`). Without that, last-writer-wins discarded nearly every
# entry and two writers could interleave into one shared temp path, promoting
# malformed JSON.
# ---------------------------------------------------------------------------
_ACQUIRE_CACHE_NAME = "_acquire_cache.json"
_ACQUIRE_LOCK_SUFFIX = ".lock"
# Increment when a new default route makes an old negative result incomplete.
ACQUIRE_POLICY_VERSION = 3
try:
    ACQUIRE_NEG_TTL_DAYS = float(os.environ.get("ACQUIRE_NEG_TTL_DAYS", "14"))
except (TypeError, ValueError):
    ACQUIRE_NEG_TTL_DAYS = 14.0
try:
    # Short window for `retrieval_failed` misses (outage, rate limit, timeout).
    ACQUIRE_FAILED_TTL_HOURS = float(
        os.environ.get("ACQUIRE_FAILED_TTL_HOURS", "6"))
except (TypeError, ValueError):
    ACQUIRE_FAILED_TTL_HOURS = 6.0


def _cache_path(pdfs_dir: pathlib.Path) -> pathlib.Path:
    return pdfs_dir / _ACQUIRE_CACHE_NAME


def _load_acquire_cache(pdfs_dir: pathlib.Path) -> dict[str, Any]:
    p = _cache_path(pdfs_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}  # corrupt cache is non-fatal; treat as empty


@contextlib.contextmanager
def _cache_lock(pdfs_dir: pathlib.Path) -> Iterator[None]:
    """Exclusive advisory lock guarding a cache read-modify-write.

    The lock lives on a sidecar file rather than on the cache itself: the cache
    is replaced by `os.replace`, so its inode changes under any holder and the
    lock would stop being mutual. POSIX-only; where `fcntl` is unavailable we
    degrade to no locking (the atomic rename still guarantees readable JSON,
    only concurrent merges can be lost).
    """
    if fcntl is None:  # pragma: no cover - non-POSIX platforms
        yield
        return
    lock_path = _cache_path(pdfs_dir).with_name(
        _ACQUIRE_CACHE_NAME + _ACQUIRE_LOCK_SUFFIX)
    fh = None
    try:
        fh = open(lock_path, "a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:  # locking is best-effort; never fail the run over it
        if fh is not None:
            fh.close()
        fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()


def _merge_cache(disk: dict[str, Any], mine: dict[str, Any]) -> dict[str, Any]:
    """Merge our entries onto what another worker may have written meanwhile.

    Keys are per-paper, so conflicts are rare; when they happen the newer
    timestamp wins rather than "whoever called os.replace last".
    """
    out = dict(disk)
    for key, entry in mine.items():
        old = out.get(key)
        if not isinstance(old, dict) or not isinstance(entry, dict):
            out[key] = entry
            continue
        try:
            newer = float(entry.get("ts") or 0) >= float(old.get("ts") or 0)
        except (TypeError, ValueError):
            newer = True
        if newer:
            out[key] = entry
    return out


def _save_acquire_cache(pdfs_dir: pathlib.Path, cache: dict[str, Any]) -> None:
    """Merge `cache` into the on-disk memo and replace it atomically."""
    p = _cache_path(pdfs_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with _cache_lock(pdfs_dir):
            merged = _merge_cache(_load_acquire_cache(pdfs_dir), cache)
            # A unique temp name: a fixed one lets two writers interleave into
            # the same file and promote half-written JSON.
            fd, tmp_name = tempfile.mkstemp(
                dir=str(p.parent), prefix=_ACQUIRE_CACHE_NAME + ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as fh:
                    json.dump(merged, fh, indent=2, sort_keys=True)
                os.replace(tmp_name, p)  # same filesystem: atomic
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise
    except OSError:
        pass  # cache is a best-effort optimization; never fail the run over it


def _neg_ttl_days(entry: dict[str, Any]) -> float:
    """How long a cached miss stays trusted, by miss kind.

    A `paywalled` verdict is a stable fact about the paper, worth remembering
    for ACQUIRE_NEG_TTL_DAYS. A `retrieval_failed` verdict is a fact about one
    moment on our side (Europe PMC 503, rate limit, timeout); caching it for two
    weeks turns a minutes-long outage into a fortnight of fabricated gaps.
    """
    if entry.get("kind") == MISS_RETRIEVAL_FAILED:
        return min(ACQUIRE_FAILED_TTL_HOURS / 24.0, ACQUIRE_NEG_TTL_DAYS)
    return ACQUIRE_NEG_TTL_DAYS


def _neg_entry_fresh(entry: dict[str, Any]) -> bool:
    """A cached not_retrieved entry is trusted only within the TTL window."""
    if ACQUIRE_NEG_TTL_DAYS < 0:  # negative TTL disables expiry (always fresh)
        return True
    ts = entry.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    age_days = (time.time() - ts) / 86400.0
    return age_days <= _neg_ttl_days(entry)


def _retrieval_miss_fresh(entry: dict[str, Any]) -> bool:
    """Whether a cached main-document miss is valid under today's routes."""
    return (
        entry.get("policy_version") == ACQUIRE_POLICY_VERSION
        and _neg_entry_fresh(entry)
    )


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    return s


def _is_pdf(content: bytes) -> bool:
    return content[:5] == b"%PDF-"


def _retry_delay(response: requests.Response | None, attempt: int,
                 base_sleep_s: float) -> float:
    """Provider-directed or exponential delay, with desynchronising jitter."""
    retry_after = "" if response is None else str(
        response.headers.get("Retry-After") or ""
    ).strip()
    try:
        provider_delay = max(0.0, float(retry_after))
    except ValueError:
        provider_delay = 0.0
    exponential = base_sleep_s * (2**attempt)
    return max(provider_delay, exponential) + random.uniform(0.0, base_sleep_s)


def _download(
    s: requests.Session,
    url: str,
    dest: pathlib.Path,
    timeout: int = 60,
    *,
    max_attempts: int = 3,
    base_sleep_s: float = 1.5,
) -> tuple[bool, str]:
    """Try to download a URL, validate the bytes are a PDF, save to dest.

    Retries with exponential backoff on transient HTTP failures (429, 5xx) and
    on requests exceptions (timeout, connection reset, DNS). Non-transient
    errors (4xx other than 429, "not a PDF" payload) are returned immediately.

    Returns (ok, reason). On retry exhaustion the reason carries `transient_`
    prefix so callers can distinguish from genuine "no PDF here" responses.
    """
    last_reason = "unknown"
    for attempt in range(max_attempts):
        try:
            r = polite_get(s, url, allow_redirects=True, timeout=timeout, stream=True)
            if r.status_code == 200:
                ct = (r.headers.get("Content-Type") or "").lower()
                body = r.content
                if not _is_pdf(body):
                    # HTML landing page (e.g. paywall) — never transient.
                    return False, f"not_pdf_ct={ct[:60]}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
                return True, "ok"
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last_reason = f"transient_http_{r.status_code}"
                if attempt + 1 < max_attempts:
                    time.sleep(_retry_delay(r, attempt, base_sleep_s))
                    continue
                return False, last_reason
            # Non-transient: 4xx other than 429 (404, 403, 410, ...)
            return False, f"http_{r.status_code}"
        except requests.RequestException as e:
            last_reason = f"transient_net_err:{type(e).__name__}"
            if attempt + 1 < max_attempts:
                time.sleep(_retry_delay(None, attempt, base_sleep_s))
                continue
            return False, last_reason
    return False, last_reason


def _try_pmc_oa(s: requests.Session, pmcid: str) -> str | None:
    """Use NCBI's OA service to resolve PMCID -> tarball or PDF URL.

    Returns a PDF URL if one is advertised, else None.
    """
    url = f"https://pmc.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}"
    try:
        r = polite_get(s, url, timeout=30)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.text)
        # The XML lists <link format="pdf" href="ftp://..." />
        for link in root.iter("link"):
            if link.attrib.get("format") == "pdf":
                href = link.attrib.get("href")
                if href:
                    # ftp:// -> https:// (the same server speaks HTTPS too)
                    return href.replace("ftp://", "https://")
        return None
    except Exception:
        return None


def _try_unpaywall(s: requests.Session, doi: str) -> str | None:
    if not doi:
        return None
    url = f"https://api.unpaywall.org/v2/{doi}"
    try:
        r = polite_get(s, url, params={"email": _UNPAYWALL_EMAIL}, timeout=30)
        if r.status_code != 200:
            return None
        data = r.json()
        best = data.get("best_oa_location") or {}
        pdf = best.get("url_for_pdf")
        if pdf:
            return pdf
        # Fall back: any oa_location with a pdf URL
        for loc in data.get("oa_locations", []) or []:
            if loc.get("url_for_pdf"):
                return loc["url_for_pdf"]
        return None
    except Exception:
        return None


def _try_biorxiv(s: requests.Session, doi: str) -> str | None:
    if not doi:
        return None
    # bioRxiv/medRxiv expose /details/biorxiv/<doi> returning JSON with the version list
    for server in ("biorxiv", "medrxiv"):
        url = f"https://api.biorxiv.org/details/{server}/{doi}"
        try:
            r = polite_get(s, url, timeout=30)
            if r.status_code != 200:
                continue
            data = r.json()
            coll = data.get("collection") or []
            if not coll:
                continue
            # Latest version
            latest = coll[-1]
            ver = latest.get("version")
            if ver is None:
                continue
            return f"https://www.{server}.org/content/{doi}v{ver}.full.pdf"
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# New OA routes: full-text XML, hardened Unpaywall, OpenAlex, landing-page hop.
# ---------------------------------------------------------------------------

def _local_name(tag: Any) -> str:
    """Element tag without its XML namespace, lowercased."""
    return str(tag).rsplit("}", 1)[-1].strip().lower()


def _parse_jats(content: bytes) -> ET.Element | None:
    """Parse bytes as XML and return the root iff it is a JATS <article>.

    Defensive by design: anything that does not parse, or whose root is not
    `article` (an HTML page, a redirect body, a fragment), is NOT JATS. Named
    entity references are neutralised on a second pass because some archives
    serve `&alpha;` against a DTD we do not fetch; that is a serialisation
    detail, not a reason to discard a real full text.
    """
    if not content:
        return None
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        try:
            root = ET.fromstring(_neutralize_entities(content))
        except (ET.ParseError, ValueError):
            return None
    except (ValueError, TypeError):
        return None
    return root if _local_name(root.tag) == "article" else None


_NAMED_ENTITY_RE = re.compile(
    rb"&(?!(?:amp|lt|gt|quot|apos);|#)[A-Za-z][A-Za-z0-9._-]*;")
_DOCTYPE_RE = re.compile(rb"<!DOCTYPE[^>[]*(\[[^]]*\])?[^>]*>", re.IGNORECASE)


def _neutralize_entities(content: bytes) -> bytes:
    """Drop the DOCTYPE and replace undefined named entities with a space.

    Only ever used to decide whether the payload is a full text; the bytes
    saved to disk are always the originals.
    """
    return _NAMED_ENTITY_RE.sub(b" ", _DOCTYPE_RE.sub(b"", content))


def _jats_has_body_content(root: ET.Element | None) -> bool:
    """True when a JATS <body> carries real full text, not just an abstract.

    The old substring check (`b"<body" in content`) accepted
    `<article><front><abstract/></front><body/></article>`: the file was saved,
    stamped as full text, and its ABSTRACT became quotable sentences that read
    exactly like Results. An abstract is never full-text evidence, so a body
    with no <sec> and no non-empty <p> is rejected here and the waterfall keeps
    looking.
    """
    if root is None:
        return False
    # JATS puts <body> directly under <article>. Deliberately not a descendant
    # search: a <sub-article> body is a peer-review report, not this paper's
    # text, and it must not stand in for a missing body.
    for el in root:
        if _local_name(el.tag) != "body":
            continue
        for child in el.iter():
            name = _local_name(child.tag)
            if name == "sec":
                return True
            if name == "p" and "".join(child.itertext()).strip():
                return True
    return False


def _is_jats_xml(content: bytes) -> bool:
    """A JATS full text: an <article> root whose <body> has real content."""
    return _jats_has_body_content(_parse_jats(content))


def _xml_file_has_body(path: pathlib.Path) -> bool:
    """Re-check a saved XML on disk before declaring it a full-text win."""
    try:
        return _is_jats_xml(pathlib.Path(path).read_bytes())
    except OSError:
        return False


def _download_xml(
    s: requests.Session,
    url: str,
    dest: pathlib.Path,
    timeout: int = 60,
    *,
    max_attempts: int = 3,
    base_sleep_s: float = 1.5,
) -> tuple[bool, str]:
    """Download JATS XML, validate it has a substantive <body>, save to dest."""
    last_reason = "unknown"
    for attempt in range(max_attempts):
        try:
            r = polite_get(s, url, allow_redirects=True, timeout=timeout)
            if r.status_code == 200:
                body = r.content
                if not _is_jats_xml(body):
                    return False, "not_jats_xml"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
                return True, "ok"
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last_reason = f"transient_http_{r.status_code}"
                if attempt + 1 < max_attempts:
                    time.sleep(_retry_delay(r, attempt, base_sleep_s))
                    continue
                return False, last_reason
            return False, f"http_{r.status_code}"
        except requests.RequestException as e:
            last_reason = f"transient_net_err:{type(e).__name__}"
            if attempt + 1 < max_attempts:
                time.sleep(_retry_delay(None, attempt, base_sleep_s))
                continue
            return False, last_reason
    return False, last_reason


def _try_epmc_fulltext_xml(s: requests.Session, pmcid: str) -> str | None:
    """Return the official Europe PMC full-text XML URL without probing it.

    The caller downloads and validates once. The previous probe issued the same
    GET twice for every available paper and omitted unavailable XML routes from
    the attempt ledger.
    """
    if not pmcid:
        return None
    return _EPMC_FULLTEXT_XML.format(pmcid=pmcid)


_EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_PMCID_RE = re.compile(r"PMC\d+", re.IGNORECASE)


def _norm_doi(doi: str) -> str:
    """Strip common DOI prefixes/URLs so it can go into an EPMC query cleanly."""
    if not doi:
        return ""
    d = doi.strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.IGNORECASE)
    d = re.sub(r"^doi:\s*", "", d, flags=re.IGNORECASE)
    return d.strip()


def _pmcid_from_pmc_url(url: str) -> str | None:
    """Extract a PMCID from any PMC article URL that OA indexes hand back."""
    if not url:
        return None
    m = re.search(r"/(PMC\d+)", url, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # bare numeric PMC article path, e.g. .../pmc/articles/5948154/
    m = re.search(r"/pmc/articles/(\d+)", url, re.IGNORECASE)
    if m:
        return "PMC" + m.group(1)
    return None


def _resolve_ids_from_doi(
    s: requests.Session, doi: str
) -> tuple[str | None, str | None]:
    """Resolve (pmcid, pmid) for a DOI via the Europe PMC search API.

    Many records arrive from LiteratureSearch/Consensus/Exa with only a DOI and
    no PMCID, which silently skips the PMCID-gated OA routes (Europe PMC render,
    JATS full-text XML, NCBI PMC OA) — even when a fully open PMC copy exists.
    Backfilling the PMCID/PMID here lets those routes fire. Open-access only:
    this resolves *identifiers*, it never bypasses a paywall.

    Returns (pmcid, pmid); either may be None. Never raises.
    """
    doi = _norm_doi(doi)
    if not doi:
        return None, None
    try:
        r = polite_get(
            s,
            _EPMC_SEARCH,
            params={
                "query": f'DOI:"{doi}"',
                "format": "json",
                "resultType": "lite",
                "pageSize": 1,
            },
            timeout=30,
        )
        if r.status_code != 200:
            return None, None
        results = (r.json().get("resultList") or {}).get("result") or []
        if not results:
            return None, None
        hit = results[0]
        pmcid = hit.get("pmcid")
        pmid = hit.get("pmid")
        # Only trust a PMCID that looks like one; the XML/OA routes validate the
        # payload themselves, so we don't gate on isOpenAccess here.
        if pmcid and not _PMCID_RE.fullmatch(pmcid.strip()):
            pmcid = None
        return (pmcid.strip().upper() if pmcid else None,
                str(pmid).strip() if pmid else None)
    except Exception:
        return None, None


def _epmc_flag(value: Any) -> bool:
    """Europe PMC returns its booleans as the strings "Y"/"N"."""
    return str(value or "").strip().upper() == "Y"


def _access_lookup_error(reason: str) -> tuple[str, dict[str, Any]]:
    """The access-classification CALL failed — no verdict, and we say so.

    The reason is `transient_`-prefixed so it also reads correctly in the
    attempts chain: it names OUR failure, not a property of the paper.
    """
    return ACCESS_UNKNOWN, {ACCESS_LOOKUP_ERROR_KEY: reason, "decided_by": None}


def _access_lookup_failed(info: dict[str, Any] | None) -> bool:
    """True when Europe PMC could not be asked, as opposed to had no answer."""
    return bool((info or {}).get(ACCESS_LOOKUP_ERROR_KEY))


def _epmc_access(s: requests.Session, doi: str) -> tuple[str, dict[str, Any]]:
    """Classify a DOI's access state from the Europe PMC search API.

    Returns ``(state, info)``. ``state`` is ``oa_licensed`` / ``free_to_read``
    / ``ACCESS_UNKNOWN``; the terminal ``not_retrievable`` verdict is only
    reached once the waterfall has actually failed (see ``_final_access_state``),
    because an EPMC miss can also mean "not indexed" or "transient error".

    ``info`` carries the raw evidence (``isOpenAccess``/``inPMC``/``inEPMC``,
    plus the ``pmcid``/``pmid`` this call already resolved, so the caller does
    not need a second identifier round-trip). Recording the raw flags is the
    point: a report can then state WHY a paper was called free or paywalled.

    Two very different misses are kept apart, because collapsing them let an
    outage fabricate a paywall verdict (and cache it):

      * DOI simply not indexed -> ``(ACCESS_UNKNOWN, {})`` — an empty ``info``
        still means "Europe PMC had nothing to say".
      * the call itself failed (503, 429, timeout, schema change) ->
        ``(ACCESS_UNKNOWN, {ACCESS_LOOKUP_ERROR_KEY: "transient_..."})``, which
        ``_access_lookup_failed`` detects and ``_classify_miss`` refuses to read
        as evidence of a paywall.

    This is a *classification* call. `free_to_read` describes what the archive
    itself serves for free; it never authorises scraping a publisher site.
    Never raises.
    """
    doi = _norm_doi(doi)
    if not doi:
        return ACCESS_UNKNOWN, {}
    try:
        r = polite_get(
            s,
            _EPMC_SEARCH,
            params={
                "query": f'DOI:"{doi}"',
                "format": "json",
                "resultType": "core",
                "pageSize": 1,
            },
            timeout=30,
        )
        if r.status_code != 200:
            return _access_lookup_error(f"transient_http_{r.status_code}")
        results = (r.json().get("resultList") or {}).get("result") or []
        if not results:
            return ACCESS_UNKNOWN, {}  # indexed nowhere — a real answer
        hit = results[0]
    except requests.RequestException as exc:
        return _access_lookup_error(f"transient_net_err:{type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001 - malformed payload / schema change
        return _access_lookup_error(f"transient_error:{type(exc).__name__}")

    pmcid = hit.get("pmcid")
    if pmcid and not _PMCID_RE.fullmatch(str(pmcid).strip()):
        pmcid = None
    pmid = hit.get("pmid")
    info: dict[str, Any] = {
        "isOpenAccess": hit.get("isOpenAccess"),
        "inPMC": hit.get("inPMC"),
        "inEPMC": hit.get("inEPMC"),
        "license": hit.get("license"),
        "pmcid": str(pmcid).strip().upper() if pmcid else None,
        "pmid": str(pmid).strip() if pmid else None,
        "decided_by": "europe_pmc_search",
    }
    if _epmc_flag(hit.get("isOpenAccess")):
        return ACCESS_OA_LICENSED, info
    if _epmc_flag(hit.get("inPMC")) or _epmc_flag(hit.get("inEPMC")):
        # Author manuscript: outside the OA subset but free in the archive.
        return ACCESS_FREE_TO_READ, info
    return ACCESS_UNKNOWN, info


def _stamp_reuse_rights(record: dict) -> dict:
    """Add license / reuse_rights / figure_embedding_allowed to the record.

    Retrieval rights and reuse rights are different questions. A free_to_read
    author manuscript may be read but is not licensed for figure reproduction,
    and the report embeds crops. Defaults to "not allowed" when no licence is
    recorded — see scripts/reuse_rights.py.
    """
    import sys as _sys
    _scripts = str(pathlib.Path(__file__).resolve().parents[2])
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from reuse_rights import rights_record

    evidence = record.get("access_evidence") or {}
    record.update(rights_record(evidence.get("license"),
                                record.get("access_state")))
    return record


def _final_access_state(epmc_state: str, retrieved: bool) -> str:
    """Resolve the state recorded on the record once the waterfall is done.

    Europe PMC's verdict wins when it has one. Otherwise a paper we *did*
    retrieve is reported as ``free_to_read`` — we know it was served openly, but
    we have no licence evidence, and over-claiming ``oa_licensed`` (which gates
    quoting downstream) would be worse than under-claiming. A paper we neither
    classified nor retrieved is ``not_retrievable``.
    """
    if epmc_state in (ACCESS_OA_LICENSED, ACCESS_FREE_TO_READ):
        return epmc_state
    return ACCESS_FREE_TO_READ if retrieved else ACCESS_NOT_RETRIEVABLE


def _classify_miss(
    access_state: str,
    attempts: list[dict[str, Any]],
    *,
    classification_failed: bool = False,
) -> str:
    """"paywalled" vs "retrieval_failed" for a record we could not retrieve.

    Claiming a paywall is only honest when nothing says the text is free. If the
    archive serves it (oa_licensed / free_to_read), if the classification call
    itself never came back (`classification_failed`), or if every route died on
    a transient error, the paper may well be retrievable and WE failed — say so.
    A Europe PMC outage must never be recorded as a paywall.
    """
    if access_state in (ACCESS_OA_LICENSED, ACCESS_FREE_TO_READ):
        return MISS_RETRIEVAL_FAILED
    if classification_failed:
        return MISS_RETRIEVAL_FAILED
    if any("transient" in str(a.get("reason") or "") for a in attempts):
        return MISS_RETRIEVAL_FAILED
    return MISS_PAYWALLED


def _pdf_url_from_landing(
    s: requests.Session, url: str, *, timeout: int = 45,
) -> str | None:
    """One conservative hop: fetch an HTML landing page and extract the
    publisher-declared PDF link (`citation_pdf_url` meta), or an obvious .pdf
    anchor. Only follows links the page itself exposes; never guesses."""
    try:
        r = polite_get(s, url, allow_redirects=True, timeout=timeout)
        if r.status_code != 200:
            return None
        ct = (r.headers.get("Content-Type") or "").lower()
        if "html" not in ct and b"<html" not in r.content[:400].lower():
            return None
        html = r.content.decode("utf-8", "ignore")
    except Exception:
        return None
    # citation_pdf_url meta (either attribute order)
    for pat in (
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
    ):
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return _absolutize(m.group(1), r.url)
    # obvious .pdf link
    m = re.search(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', html, re.IGNORECASE)
    if m:
        return _absolutize(m.group(1), r.url)
    return None


def _absolutize(href: str, base: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, href)


def _unpaywall_urls(s: requests.Session, doi: str) -> list[str]:
    """All Unpaywall OA URLs for a DOI, best first (publisher/published PDFs,
    then any pdf urls, then landing urls for a hop). Empty if not OA."""
    if not doi:
        return []
    try:
        r = polite_get(s, f"https://api.unpaywall.org/v2/{doi}",
                       params={"email": _CONTACT_EMAIL}, timeout=30)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    locs = data.get("oa_locations") or []

    def rank(loc):
        return (
            0 if loc.get("version") == "publishedVersion" else 1,
            0 if loc.get("host_type") == "publisher" else 1,
        )

    ordered = sorted(locs, key=rank)
    out: list[str] = []
    for loc in ordered:
        u = loc.get("url_for_pdf")
        if u and u not in out:
            out.append(u)
    for loc in ordered:
        u = loc.get("url")  # landing page -> hop candidate
        if u and u not in out:
            out.append(u)
    return out


def _unpaywall_is_oa(s: requests.Session, doi: str) -> tuple[bool | None, str]:
    """Cheap Unpaywall OA status probe used to fail closed papers fast.

    Returns (is_oa, reason). `is_oa` is None when the status is unknown (network
    error / not indexed) — in that case we do NOT skip, to preserve recall. The
    one Unpaywall call here is not wasted: `_unpaywall_urls` is called later on
    the OA path anyway, and requests.Session keeps the connection warm.

    This is deliberately keyed on Unpaywall's `is_oa`, never on a publisher-name
    blocklist: many Nature/Cell/NEJM-family papers ARE open via PMC, so a
    blocklist would wrongly drop recoverable open copies.
    """
    if not doi:
        return None, "no_doi"
    try:
        r = polite_get(s, f"https://api.unpaywall.org/v2/{doi}",
                       params={"email": _CONTACT_EMAIL}, timeout=30)
        if r.status_code != 200:
            return None, f"unpaywall_http_{r.status_code}"
        data = r.json()
    except Exception as exc:  # noqa: BLE001 - unknown status must not skip
        return None, f"unpaywall_error:{type(exc).__name__}"
    is_oa = data.get("is_oa")
    if is_oa is True:
        return True, "unpaywall_is_oa=true"
    if is_oa is False:
        return False, "unpaywall_is_oa=false"
    return None, "unpaywall_is_oa=unknown"


def _openalex_location_is_oa(loc: Any) -> bool:
    """Whether OpenAlex affirmatively marks this location as open access.

    Only `best_oa_location` is OA by construction. `primary_location` and the
    `locations` list describe EVERY known copy, including the publisher's
    paywall page, and each carries its own `is_oa` flag. Reading that flag is
    the difference between an OA route and a paywall bypass: a closed paper's
    `primary_location.landing_page_url` is the paywall, and publishers emit
    `citation_pdf_url` on paywalled pages by design, so a landing-page hop from
    there would fetch and store the subscription PDF.
    """
    return isinstance(loc, dict) and loc.get("is_oa") is True


def _openalex_urls(s: requests.Session, doi: str) -> list[str]:
    """OA URLs from OpenAlex for a DOI (independent of Unpaywall).

    Every returned URL comes from a location an index affirmatively marked open
    access; non-OA locations are skipped entirely rather than fetched.
    """
    if not doi:
        return []
    try:
        r = polite_get(s, f"https://api.openalex.org/works/doi:{doi}",
                       params={"mailto": _CONTACT_EMAIL}, timeout=30)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    out: list[str] = []

    def _add(u: Any) -> None:
        if u and isinstance(u, str) and u not in out:
            out.append(u)

    best = data.get("best_oa_location") or {}
    if isinstance(best, dict):  # OA by construction — behaviour preserved
        _add(best.get("pdf_url"))
        _add(best.get("landing_page_url"))
    primary = data.get("primary_location") or {}
    if _openalex_location_is_oa(primary):
        _add(primary.get("pdf_url"))
        _add(primary.get("landing_page_url"))
    for loc in data.get("locations") or []:
        if _openalex_location_is_oa(loc):
            _add(loc.get("pdf_url"))
    oa = data.get("open_access") or {}
    if isinstance(oa, dict) and oa.get("is_oa") is not False:
        _add(oa.get("oa_url"))
    return out


def _try_url_as_pdf(
    s: requests.Session, url: str, dest: pathlib.Path,
    *, allow_landing_hop: bool = True,
) -> tuple[bool, str]:
    """Try a URL as a direct PDF. Returns (ok, reason).

    `allow_landing_hop` enables ONE hop from an HTML landing page to a PDF URL
    declared by that page. The hop uses the same fresh, unauthenticated session
    and succeeds only when the response validates as a PDF. It never supplies
    credentials, defeats an access challenge, or guesses a hidden endpoint.
    """
    ok, reason = _download(s, url, dest)
    if ok:
        return True, "ok"
    if reason.startswith("not_pdf"):
        if not allow_landing_hop:
            return False, f"{reason};hop=disabled"
        hop = _pdf_url_from_landing(s, url)
        if hop and hop != url:
            ok2, reason2 = _download(s, hop, dest)
            if ok2:
                return True, "ok_via_landing_hop"
            return False, f"{reason};hop={reason2}"
    return False, reason


def _already_attempted(attempts: list[dict[str, Any]], url: str) -> bool:
    """True if this exact URL already failed non-transiently in this walk.

    The supplementary pass must not re-walk a route already tried for the same
    PMCID. A
    transient failure is not counted: that one is worth one more shot.
    """
    return any(
        a.get("url") == url and "transient" not in str(a.get("reason") or "")
        for a in attempts
    )


def _try_figures_pdf(
    s: requests.Session,
    record: dict[str, Any],
    dest: pathlib.Path,
    attempts: list[dict[str, Any]],
    *,
    deadline: float,
) -> tuple[str, str | None, str | None]:
    """Best-effort *supplementary* PDF fetch, for figure cropping only.

    Called after an XML win. The XML remains the text source; this only gives
    `parse_jats` page geometry it can crop figures from. Strictly non-fatal and
    time-bounded — a failure here must never fail acquisition, never touch
    `parser_hint`/`local_pdf`/`oa_source`, and never blow the run's time budget.

    Routes run in yield order: NCBI PMC OA, Europe PMC ?pdf=render fallback, then any
    record/publisher URL that serves validated PDF bytes without authentication.
    A landing hop follows only a page-declared PDF URL. Single attempt per route
    (no backoff retries): this is a nice-to-have, not worth waiting on.

    Returns ``(status, local_path, source)``.
    """
    pmcid = str(record.get("pmcid") or "").strip()

    def _left() -> float:
        return deadline - time.monotonic()

    def _fetch(source: str, url: str, *, allow_landing_hop: bool = False) -> bool:
        ok, reason = _download(
            s, url, dest,
            timeout=max(1, int(min(FIGURES_PDF_BUDGET_S, _left()))),
            max_attempts=1,
        )
        if not ok and reason.startswith("not_pdf") and allow_landing_hop and _left() > 0:
            hop = _pdf_url_from_landing(
                s, url, timeout=max(1, int(min(FIGURES_PDF_BUDGET_S, _left()))))
            if hop and hop != url and _left() > 0:
                ok, hop_reason = _download(
                    s, hop, dest,
                    timeout=max(1, int(min(FIGURES_PDF_BUDGET_S, _left()))),
                    max_attempts=1,
                )
                reason = "ok_via_landing_hop" if ok else f"{reason};hop={hop_reason}"
        attempts.append({"source": f"figures_pdf:{source}", "url": url,
                         "reason": reason})
        return ok

    try:
        if pmcid:
            oa_url = _try_pmc_oa(s, pmcid)
            if oa_url and not _already_attempted(attempts, oa_url):
                if _left() <= 0:
                    return FIGURES_PDF_SKIPPED, None, None
                if _fetch("ncbi_pmc_oa", oa_url):
                    return FIGURES_PDF_OK, str(dest), "ncbi_pmc_oa"
            pmc_article = _PMC_ARTICLE.format(pmcid=pmcid)
            if not _already_attempted(attempts, pmc_article):
                if _left() <= 0:
                    return FIGURES_PDF_SKIPPED, None, None
                if _fetch(
                    "ncbi_pmc_author_manuscript",
                    pmc_article,
                    allow_landing_hop=True,
                ):
                    return (
                        FIGURES_PDF_OK,
                        str(dest),
                        "ncbi_pmc_author_manuscript",
                    )
            epmc_url = _EPMC_RENDER_PDF.format(pmcid=pmcid)
            if not _already_attempted(attempts, epmc_url):
                if _left() <= 0:
                    return FIGURES_PDF_SKIPPED, None, None
                if _fetch("europe_pmc_render", epmc_url):
                    return FIGURES_PDF_OK, str(dest), "europe_pmc_render"

        internet_urls: list[str] = []
        for field in ("pdf_url", "url", "landing_url"):
            candidate = record.get(field)
            if (
                isinstance(candidate, str)
                and candidate.startswith(("http://", "https://"))
                and candidate not in internet_urls
                and not _already_attempted(attempts, candidate)
            ):
                internet_urls.append(candidate)
        for internet_url in internet_urls:
            if _left() <= 0:
                return FIGURES_PDF_SKIPPED, None, None
            if _fetch("accessible_internet", internet_url, allow_landing_hop=True):
                return FIGURES_PDF_OK, str(dest), "accessible_internet"
    except Exception as exc:  # noqa: BLE001 - supplementary, never fatal
        attempts.append({"source": "figures_pdf", "url": pmcid,
                         "reason": f"error:{type(exc).__name__}"})
        return FIGURES_PDF_UNAVAILABLE, None, None
    return FIGURES_PDF_UNAVAILABLE, None, None


def acquire_pdf(
    record: dict[str, Any],
    pdfs_dir: str | pathlib.Path,
    refresh: bool = False,
    fast_fail_closed: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Try to download a PDF for one paper record. Returns the record with
    `local_pdf` and `oa_source` set on success, or `_not_retrieved` on failure.

    Parameters
    ----------
    refresh : bool
        Ignore negative-cache hits and re-walk the full retrieval waterfall.
    fast_fail_closed : bool
        Optional speed shortcut: skip the slow publisher/landing/index/preprint
        routes when Unpaywall reports the paper is not open access and no PMCID
        is available. False by default because actual PDF retrievability, not
        index licence metadata, governs whether text may be read and OCRed.
    use_cache : bool
        Read/write the persistent acquisition memo in ``pdfs_dir``. Positive
        hits are reused only while the local file still exists; negative hits
        are reused only within ``ACQUIRE_NEG_TTL_DAYS``.

    Record fields set here
    ----------------------
    ``access_state`` / ``access_evidence``
        Three-way access classification and the raw Europe PMC flags behind it
        (see the module docstring). Set on EVERY record.
    ``figures_pdf`` / ``figures_pdf_status`` / ``figures_pdf_source``
        A supplementary PDF fetched purely so figures can be cropped from a
        paper whose TEXT came from JATS XML. Distinct from ``local_pdf``.
    ``_not_retrieved_kind``
        ``paywalled`` or ``retrieval_failed`` for a miss; also prefixed onto
        ``_not_retrieved_reason``.
    """
    pdfs_dir = pathlib.Path(pdfs_dir)
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    paper_id = record["paper_id"]

    # Canonical landing URL for hyperlinking — set for EVERY record, always,
    # so references are clickable even when no full text is retrieved.
    if record.get("doi") and not record.get("landing_url"):
        record["landing_url"] = f"https://doi.org/{record['doi']}"
    elif record.get("pmcid") and not record.get("landing_url"):
        record["landing_url"] = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{record['pmcid']}/"
    elif record.get("pmid") and not record.get("landing_url"):
        record["landing_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{record['pmid']}/"

    # Present on EVERY record so downstream never has to guess whether the key
    # exists. Only the XML path overwrites these (see `_attach_figures_pdf`);
    # a PDF-sourced paper crops from `local_pdf` and needs no supplement.
    record.setdefault("figures_pdf", None)
    record.setdefault("figures_pdf_status", FIGURES_PDF_SKIPPED)
    record.setdefault("figures_pdf_source", None)

    if record.get("local_pdf") or record.get("local_xml"):
        record.setdefault("oa_full_url", record.get("landing_url"))
        # Bypassed the waterfall (user-supplied file): no access evidence.
        record.setdefault("access_state", ACCESS_UNKNOWN)
        record.setdefault("access_evidence", {"decided_by": "local_file_present"})
        _stamp_reuse_rights(record)
        return record  # already local

    dest = pdfs_dir / f"{paper_id}.pdf"
    xml_dest = pdfs_dir / f"{paper_id}.xml"
    # Deliberately NOT `<paper_id>.pdf`: the supplementary figures PDF must never
    # be mistaken for `local_pdf` (the text source stays the XML).
    figures_dest = pdfs_dir / f"{paper_id}.figures.pdf"

    s = _session()
    attempts: list[dict[str, Any]] = []

    # ---- Consult the persistent acquisition cache before any network I/O. ----
    cache = _load_acquire_cache(pdfs_dir) if use_cache else {}
    cached = cache.get(paper_id) if use_cache else None

    def _restore_access(entry: dict[str, Any]) -> None:
        """Reuse the memoised access classification instead of re-querying.

        The PMCID rides along because the supplementary figures pass is gated on
        it: without it, a cache-hit record whose figures entry has since expired
        would report `skipped` forever instead of retrying.
        """
        record["access_state"] = entry.get("access_state") or ACCESS_UNKNOWN
        record["access_evidence"] = entry.get("access_evidence") or {}
        if entry.get("pmcid") and not record.get("pmcid"):
            record["pmcid"] = entry["pmcid"]

    def _attach_figures_pdf() -> None:
        """Attach a supplementary figures PDF to an XML-sourced record.

        Memoised under its own cache key (``<paper_id>::figures_pdf``) so a miss
        is not re-walked every run; negative entries honour the same
        ``ACQUIRE_NEG_TTL_DAYS`` window and ``refresh`` escape hatch as the main
        memo. Never raises and never touches the text source.
        """
        key = f"{paper_id}{_FIGURES_PDF_CACHE_SUFFIX}"
        entry = cache.get(key) if use_cache else None
        if entry:
            if entry.get("status") == FIGURES_PDF_OK:
                fp = entry.get("figures_pdf")
                if fp and pathlib.Path(fp).exists():
                    record["figures_pdf"] = fp
                    record["figures_pdf_status"] = FIGURES_PDF_OK
                    record["figures_pdf_source"] = entry.get("source")
                    return
                # File vanished -> fall through and re-attempt.
            elif not refresh and _neg_entry_fresh(entry):
                record["figures_pdf"] = None
                record["figures_pdf_status"] = entry.get("status") or FIGURES_PDF_UNAVAILABLE
                record["figures_pdf_source"] = None
                return
        status, path, source = _try_figures_pdf(
            s, record, figures_dest, attempts,
            deadline=time.monotonic() + FIGURES_PDF_BUDGET_S,
        )
        record["figures_pdf"] = path
        record["figures_pdf_status"] = status
        record["figures_pdf_source"] = source
        # `skipped` means "no open route was tried" — no PMCID yet, or the time
        # budget ran out. That is a fact about this run, not about the paper, so
        # memoising it would freeze a budget blip into a permanent no-figures
        # verdict. Only a real outcome (ok / unavailable) is remembered.
        if use_cache and status != FIGURES_PDF_SKIPPED:
            cache[key] = {"status": status, "figures_pdf": path,
                          "source": source, "ts": time.time()}
            _save_acquire_cache(pdfs_dir, cache)

    if cached:
        if cached.get("status") == "ok":
            lp, lx = cached.get("local_pdf"), cached.get("local_xml")
            if lp and pathlib.Path(lp).exists():
                record["local_pdf"] = lp
                record["oa_source"] = cached.get("oa_source")
                record["oa_full_url"] = cached.get("oa_full_url") or record.get("landing_url")
                record["_from_cache"] = "ok"
                _restore_access(cached)
                return record
            if lx and pathlib.Path(lx).exists():
                record["local_xml"] = lx
                record["parser_hint"] = "jats-xml"
                record["oa_source"] = cached.get("oa_source")
                record["oa_full_url"] = cached.get("oa_full_url") or record.get("landing_url")
                record["_from_cache"] = "ok"
                _restore_access(cached)
                _attach_figures_pdf()
                return record
            # File vanished -> fall through and re-acquire (drop stale entry).
        elif (cached.get("status") == "not_retrieved" and not refresh
              and _retrieval_miss_fresh(cached)):
            record["_not_retrieved"] = True
            record["oa_full_url"] = None
            record["_not_retrieved_reason"] = cached.get("reason") or "cached_not_retrieved"
            record["_not_retrieved_kind"] = cached.get("kind")
            record["_from_cache"] = "not_retrieved"
            record["_attempts"] = cached.get("attempts", [])
            _restore_access(cached)
            return record

    def _remember(rec: dict[str, Any]) -> dict[str, Any]:
        """Persist the outcome for this paper into the acquisition memo."""
        if not use_cache:
            return rec
        if rec.get("local_pdf") or rec.get("local_xml"):
            cache[paper_id] = {
                "status": "ok",
                "oa_source": rec.get("oa_source"),
                "oa_full_url": rec.get("oa_full_url"),
                "local_pdf": rec.get("local_pdf"),
                "local_xml": rec.get("local_xml"),
                "access_state": rec.get("access_state"),
                "access_evidence": rec.get("access_evidence"),
                "pmcid": rec.get("pmcid"),
                "ts": time.time(),
            }
        elif rec.get("_not_retrieved"):
            cache[paper_id] = {
                "status": "not_retrieved",
                "policy_version": ACQUIRE_POLICY_VERSION,
                "reason": rec.get("_not_retrieved_reason"),
                "kind": rec.get("_not_retrieved_kind"),
                "attempts": rec.get("_attempts", []),
                "access_state": rec.get("access_state"),
                "access_evidence": rec.get("access_evidence"),
                "ts": time.time(),
            }
        _save_acquire_cache(pdfs_dir, cache)
        return rec

    # 0. One Europe PMC lookup: classify the access state AND backfill
    #    PMCID/PMID when missing, so the PMCID-gated OA routes (2-4, incl. the
    #    JATS XML route) can actually run. Identifier resolution and licence
    #    classification only — never a paywall bypass.
    access_state = ACCESS_UNKNOWN
    access_info: dict[str, Any] = {}
    if record.get("doi"):
        access_state, access_info = _epmc_access(s, record["doi"])
        # The error, when there is one, goes into the chain: a reason string
        # that hides "Europe PMC was down" is how an outage got reported as a
        # paywall. The `transient_` marker also keeps `_classify_miss` honest.
        _access_err = access_info.get(ACCESS_LOOKUP_ERROR_KEY)
        attempts.append({
            "source": "epmc_access",
            "url": f'DOI:"{_norm_doi(record["doi"])}"',
            "reason": (
                f"state={access_state}"
                f";isOpenAccess={access_info.get('isOpenAccess') or '-'}"
                f";inPMC={access_info.get('inPMC') or '-'}"
                f";inEPMC={access_info.get('inEPMC') or '-'}"
                + (f";error={_access_err}" if _access_err else "")
            ),
        })
        if access_info.get("pmcid") and not record.get("pmcid"):
            record["pmcid"] = access_info["pmcid"]
        if access_info.get("pmid") and not record.get("pmid"):
            record["pmid"] = access_info["pmid"]
    record["access_state"] = access_state
    record["access_evidence"] = access_info

    # Fallback identifier resolve: only when Europe PMC returned nothing at all
    # (not indexed, or a transient error), so it doubles as a cheap retry.
    if (record.get("doi") and not record.get("pmcid")
            and (not access_info or _access_lookup_failed(access_info))):
        res_pmcid, res_pmid = _resolve_ids_from_doi(s, record["doi"])
        attempts.append({
            "source": "epmc_id_resolve",
            "url": f'DOI:"{_norm_doi(record["doi"])}"',
            "reason": f"pmcid={res_pmcid or '-'};pmid={res_pmid or '-'}",
        })
        if res_pmcid:
            record["pmcid"] = res_pmcid
        if res_pmid and not record.get("pmid"):
            record["pmid"] = res_pmid

    def _win_pdf(source: str, url: str):
        record["local_pdf"] = str(dest)
        record["oa_source"] = source
        record["oa_full_url"] = url
        record["access_state"] = _final_access_state(access_state, True)
        _stamp_reuse_rights(record)
        record["_attempts"] = attempts
        return _remember(record)

    def _win_xml(source: str, url: str):
        """Declare an XML win — but only for XML that really carries a body.

        An abstract-only JATS record (empty `<body/>`, or a body whose sections
        are all skipped) is NOT full text: quoting it would present an abstract
        as Results-grade evidence. When that is what we downloaded we discard
        the file and return None so the waterfall keeps walking.
        """
        if not _xml_file_has_body(xml_dest):
            attempts.append({"source": source, "url": url,
                             "reason": "xml_no_body_content"})
            with contextlib.suppress(OSError):
                xml_dest.unlink()
            return None
        record["local_xml"] = str(xml_dest)
        record["parser_hint"] = "jats-xml"
        record["oa_source"] = source
        record["oa_full_url"] = url
        record["access_state"] = _final_access_state(access_state, True)
        _stamp_reuse_rights(record)
        # Supplementary only: figures for an XML-sourced paper. Must not change
        # the text source, `parser_hint`, or the success of this acquisition.
        _attach_figures_pdf()
        record["_attempts"] = attempts
        return _remember(record)

    # 1. URL already on the record. A declared landing hop is acceptable when a
    #    fresh session actually receives validated PDF bytes.
    if record.get("pdf_url"):
        ok, reason = _try_url_as_pdf(
            s, record["pdf_url"], dest, allow_landing_hop=True)
        attempts.append({"source": "record_pdf_url", "url": record["pdf_url"], "reason": reason})
        if ok:
            return _win_pdf("record_pdf_url", record["pdf_url"])

    # 2. Europe PMC full-text XML (PMCID). Prefer the documented REST route to
    #    the interactive website's ?pdf=render endpoint.
    if record.get("pmcid"):
        xml_url = _try_epmc_fulltext_xml(s, record["pmcid"])
        if xml_url:
            ok, reason = _download_xml(s, xml_url, xml_dest)
            attempts.append({"source": "europe_pmc_fulltext_xml", "url": xml_url, "reason": reason})
            if ok:
                win = _win_xml("europe_pmc_fulltext_xml", xml_url)
                if win is not None:
                    return win

    # 3. NCBI OA service (advertised PDF)
    if record.get("pmcid"):
        oa_url = _try_pmc_oa(s, record["pmcid"])
        if oa_url:
            ok, reason = _download(s, oa_url, dest)
            attempts.append({"source": "ncbi_pmc_oa", "url": oa_url, "reason": reason})
            if ok:
                return _win_pdf("ncbi_pmc_oa", oa_url)

    # 3b. Public PMC article page.  NIH author manuscripts may be freely
    # readable in PMC while absent from the OA-subset service.  Fetch the
    # official article page and follow only its declared citation_pdf_url; this
    # neither guesses a protected endpoint nor depends on an OA licence flag.
    if record.get("pmcid"):
        pmc_article = _PMC_ARTICLE.format(pmcid=record["pmcid"])
        ok, reason = _try_url_as_pdf(
            s, pmc_article, dest, allow_landing_hop=True
        )
        attempts.append({
            "source": "ncbi_pmc_author_manuscript",
            "url": pmc_article,
            "reason": reason,
        })
        if ok:
            return _win_pdf("ncbi_pmc_author_manuscript", pmc_article)

    # 4. Europe PMC's website renderer is a last PMCID fallback. It is never
    #    synthesized into the corpus and is attempted at most once per paper.
    if record.get("pmcid"):
        epmc_url = _EPMC_RENDER_PDF.format(pmcid=record["pmcid"])
        ok, reason = _download(s, epmc_url, dest)
        attempts.append({"source": "europe_pmc_render", "url": epmc_url,
                         "reason": reason})
        if ok:
            return _win_pdf("europe_pmc_render", epmc_url)

    def _try_pmc_xml_from_url(source: str, url: str) -> dict | None:
        """If an OA index hands back a PMC article URL, try its JATS full-text
        XML (the copy the archive itself serves). Returns a win record or None.
        """
        pmcid = _pmcid_from_pmc_url(url)
        if not pmcid or record.get("local_xml"):
            return None
        xml_url = _try_epmc_fulltext_xml(s, pmcid)
        if not xml_url:
            return None
        ok, reason = _download_xml(s, xml_url, xml_dest)
        attempts.append({"source": f"{source}_pmc_jats_xml", "url": xml_url,
                         "reason": reason})
        if ok:
            record["pmcid"] = record.get("pmcid") or pmcid
            return _win_xml(f"{source}_pmc_jats_xml", xml_url)
        return None  # `_win_xml` also returns None for an abstract-only body

    # ---- Optional fast-fail gate for slow publisher/index/preprint routes. ----
    # Routes 1-4 above are fast, PMCID-gated OA routes and always run. Routes
    # 5-9 involve multiple provider round-trips and landing-page hops that, for
    # a genuinely closed paper, all fail. If Unpaywall definitively reports the
    # paper is NOT open access AND we still have no PMCID (so no OA copy is
    # indexed in PMC), skip 5-9 and record the miss now. Only a definitive
    # is_oa=false triggers the skip; unknown/None keeps the full walk so recall
    # is preserved. A paper Europe PMC reports as oa_licensed or free_to_read is
    # never fast-failed: the archive says the full text is free, so we ATTEMPT it.
    run_slow_routes = True
    if (fast_fail_closed and record.get("doi") and not record.get("pmcid")
            and access_state not in (ACCESS_OA_LICENSED, ACCESS_FREE_TO_READ)):
        is_oa, oa_reason = _unpaywall_is_oa(s, record["doi"])
        attempts.append({"source": "unpaywall_is_oa", "url": record["doi"], "reason": oa_reason})
        if is_oa is False:
            run_slow_routes = False

    # 5. Publicly reachable record/publisher landing pages. Availability is
    #    established by the response itself, not by OA metadata. This route
    #    never authenticates or works around a technical access control.
    if run_slow_routes:
        internet_urls: list[str] = []
        for field in ("url", "landing_url"):
            candidate = record.get(field)
            if (
                isinstance(candidate, str)
                and candidate.startswith(("http://", "https://"))
                and candidate != record.get("pdf_url")
                and candidate not in internet_urls
            ):
                internet_urls.append(candidate)
        for internet_url in internet_urls:
            ok, reason = _try_url_as_pdf(
                s, internet_url, dest, allow_landing_hop=True)
            attempts.append({
                "source": "accessible_internet",
                "url": internet_url,
                "reason": reason,
            })
            if ok:
                return _win_pdf("accessible_internet", internet_url)

    # 6. Unpaywall (hardened: all locations, publisher first, one landing hop;
    #    PMC locations also get a JATS-XML attempt)
    if run_slow_routes and record.get("doi"):
        up_urls = _unpaywall_urls(s, record["doi"])
        for up_url in up_urls[:5]:
            # Unpaywall `oa_locations` are OA by construction, so the one-hop
            # landing follow is allowed here.
            ok, reason = _try_url_as_pdf(s, up_url, dest, allow_landing_hop=True)
            attempts.append({"source": "unpaywall", "url": up_url, "reason": reason})
            if ok:
                return _win_pdf("unpaywall", up_url)
            win = _try_pmc_xml_from_url("unpaywall", up_url)
            if win is not None:
                return win

    # 7. OpenAlex (independent index; PMC locations also get a JATS-XML attempt)
    if run_slow_routes and record.get("doi"):
        oa_urls = _openalex_urls(s, record["doi"])
        for oa_url in oa_urls[:5]:
            # `_openalex_urls` only yields locations OpenAlex marks `is_oa`, so
            # the one-hop landing follow cannot land on a paywall page.
            ok, reason = _try_url_as_pdf(s, oa_url, dest, allow_landing_hop=True)
            attempts.append({"source": "openalex", "url": oa_url, "reason": reason})
            if ok:
                return _win_pdf("openalex", oa_url)
            win = _try_pmc_xml_from_url("openalex", oa_url)
            if win is not None:
                return win

    # 8. bioRxiv / medRxiv
    if run_slow_routes and record.get("doi"):
        br_url = _try_biorxiv(s, record["doi"])
        if br_url:
            ok, reason = _download(s, br_url, dest)
            attempts.append({"source": "biorxiv_medrxiv", "url": br_url, "reason": reason})
            if ok:
                return _win_pdf("biorxiv_medrxiv", br_url)

    # 9. arXiv direct
    if record.get("arxiv_id"):
        ax_url = f"https://arxiv.org/pdf/{record['arxiv_id']}.pdf"
        ok, reason = _download(s, ax_url, dest)
        attempts.append({"source": "arxiv", "url": ax_url, "reason": reason})
        if ok:
            return _win_pdf("arxiv", ax_url)

    record["_not_retrieved"] = True
    record["oa_full_url"] = None
    record["_attempts"] = attempts
    record["access_state"] = _final_access_state(access_state, False)
    _stamp_reuse_rights(record)
    # Say WHY we have nothing: a paper the archive serves for free that we
    # failed to fetch is NOT a paywall, and reporting it as one is what turned a
    # retrievable CC-BY paper into a fabricated "paywall gap".
    kind = _classify_miss(
        record["access_state"], attempts,
        classification_failed=_access_lookup_failed(access_info),
    )
    record["_not_retrieved_kind"] = kind
    chain = "; ".join(f"{a['source']}={a['reason']}" for a in attempts) or "no_url_known"
    record["_not_retrieved_reason"] = f"{kind}: {chain}"
    return _remember(record)


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--records-json", required=True, help="Path to JSON file with list of records (from search.py)")
    ap.add_argument("--pdfs-dir", required=True)
    ap.add_argument("--refresh-acquisition", action="store_true",
                    help="Ignore negative-cache hits and re-walk the full retrieval waterfall.")
    ap.add_argument("--fast-fail-closed", action="store_true",
                    help="Opt into the Unpaywall closed-paper speed shortcut.")
    ap.add_argument("--no-fast-fail-closed", dest="fast_fail_closed",
                    action="store_false", help=argparse.SUPPRESS)
    ap.set_defaults(fast_fail_closed=False)
    ap.add_argument("--no-acquire-cache", action="store_true",
                    help="Disable the persistent acquisition cache.")
    args = ap.parse_args()
    with open(args.records_json) as f:
        records = json.load(f)
    if not isinstance(records, list):
        records = [records]
    out = [
        acquire_pdf(
            r, args.pdfs_dir,
            refresh=args.refresh_acquisition,
            fast_fail_closed=args.fast_fail_closed,
            use_cache=not args.no_acquire_cache,
        )
        for r in records
    ]
    n_ok = sum(1 for r in out if r.get("local_pdf") or r.get("local_xml"))
    n_cached = sum(1 for r in out if r.get("_from_cache"))
    print(json.dumps({
        "n_records": len(out),
        "n_acquired": n_ok,
        "n_not_retrieved": len(out) - n_ok,
        "n_from_cache": n_cached,
        # Honest breakdown: a paywall claim and a fetch failure are different
        # facts, and so are the three access states.
        "n_by_access_state": {
            state: sum(1 for r in out if r.get("access_state") == state)
            for state in (ACCESS_OA_LICENSED, ACCESS_FREE_TO_READ,
                          ACCESS_NOT_RETRIEVABLE, ACCESS_UNKNOWN)
        },
        "n_by_miss_kind": {
            kind: sum(1 for r in out if r.get("_not_retrieved_kind") == kind)
            for kind in (MISS_PAYWALLED, MISS_RETRIEVAL_FAILED)
        },
        "n_figures_pdf_ok": sum(
            1 for r in out if r.get("figures_pdf_status") == FIGURES_PDF_OK),
        "records": out,
    }, indent=2))
