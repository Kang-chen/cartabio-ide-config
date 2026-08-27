#!/usr/bin/env python3
"""Bridge Biomni LiteratureSearch output into the claim-first pipeline corpus.

LiteratureSearch is a Biomni *tool* invoked by the agent (not a Python API). Each
call appends structured records to /mnt/results/execution_trace/references.jsonl.
This helper reads the slice of that file this run appended (plus any curated
records), maps each record to the normalized corpus schema that evidence_first.py
ingests via --records, merges duplicates, and writes corpus/references.jsonl,
corpus/corpus.csv, corpus/references_snapshot.jsonl and corpus/ingestion.json.

Agent flow:
    0. OFF=$(python references_to_corpus.py --refs <refs.jsonl> --print-offset)
    1. run LiteratureSearch for each planned facet (appends to references.jsonl)
    2. python references_to_corpus.py --refs <refs.jsonl> --since-offset "$OFF"
       --run-root <RUN>
    3. draft claims -> select pivotal_papers.csv -> evidence_first.py --records ...

Step 0 is not optional bookkeeping. references.jsonl is a GLOBAL, APPEND-ONLY
trace: every LiteratureSearch call in the session appends to the same file,
including calls made by earlier, unrelated tasks. Reading it whole therefore
imports another task's hits into this run's corpus, silently -- nothing in the
output says it happened. ``--print-offset`` captures the file's size *before*
this run searches; ``--since-offset`` consumes only the bytes appended after
it. The bytes actually consumed are copied verbatim to
corpus/references_snapshot.jsonl and described in corpus/ingestion.json (source,
start/end offset, record count, timestamp), so the corpus can be rebuilt from
the run's own artifacts. Run without an offset and ingestion.json carries a
``WARNING`` naming the contamination risk, because a corpus you cannot scope is
a corpus you cannot trust.

The normalized corpus schema (one row per publication):
    paper_id, id_type, doi, pmid, pmcid, title, year, journal, authors,
    first_author_surname, pdf_url, is_open_access, is_preprint, url,
    abstract, search_query, search_facet, provider, study_type,
    citation_count, sample_size, is_retracted, retraction_notice,
    study_id, cohort_id, publication_role, merged_from, raw

paper_id/id_type are derived from the strongest available identifier
(pmcid > pmid > doi > synthetic P-<n>) so the record is stable across reruns.

Nothing that arrives is thrown away. ``search_query``/``search_facet`` are the
provenance of why a paper is in the corpus at all; ``is_retracted`` used to be
dropped on the floor, which is how a retracted paper reaches a report; and
every source field the mapping does not consume is kept verbatim under ``raw``.

A paper is not a study. Convergence counted over paper_id treats a preprint and
its retitled journal version, an interim and a final report, or a conference
abstract and its full paper as independent replication. ``study_id`` is the
identity of the underlying study (shared trial registration > first-author +
year + fuzzy title match > paper_id), ``cohort_id`` names the cohort a record
draws on (GENFI, ADNI, UK Biobank, ...) and ``publication_role`` says which
kind of publication this is. Count convergence over study_id, not paper_id.

Metadata arrives dirty: LiteratureSearch records that were scraped from a
publisher page carry the page's chrome in the title (" - PMC", "| Springer
Nature Link"), HTML entities in every text field ("Alzheimer's &amp; Dementia"),
and author strings in half a dozen shapes. This module is the single place that
cleans them, so no downstream renderer has to guess -- and so none of them guess
differently. It also labels preprints (``is_preprint``) and gives them a server
name for a journal, because a preprint with an empty journal renders as an
orphan period and reads as if it were a peer-reviewed source.
"""
from __future__ import annotations
import argparse
import csv
import datetime
import difflib
import html
import json
import pathlib
import re
import sys

CORPUS_COLS = [
    "paper_id", "id_type", "doi", "pmid", "pmcid", "title", "year", "journal",
    "authors", "first_author_surname", "pdf_url", "is_open_access",
    "is_preprint", "url",
    # Preserved from LiteratureSearch instead of dropped (see module docstring).
    "abstract", "search_query", "search_facet", "provider", "study_type",
    "citation_count", "sample_size", "is_retracted", "retraction_notice",
    # Study identity, so convergence is not counted over publications.
    "study_id", "cohort_id", "publication_role",
    # Merge bookkeeping and the untouched remainder of the source record.
    "merged_from", "raw",
]
# Columns whose value is a list or a dict; JSON-encoded on the way into the CSV
# so corpus.csv stays a flat table without the record losing its structure.
CONTAINER_COLS = ("search_query", "search_facet", "merged_from", "raw")

# --- title chrome -----------------------------------------------------------
# Scraped page-chrome that trails a title when a record came from a web page
# instead of a metadata API. Copied from ``_TITLE_ARTIFACTS`` in
# scripts/report_model.py (its ``clean_title``) so the bridge and the renderer
# strip identically. Deliberately NOT imported: report_model.py belongs to the
# renderer workstream and importing it would couple the two modules.
# Every pattern is anchored with ``$``, so only a trailing suffix is ever
# removed and a real title containing a pipe or a dash survives intact.
TITLE_CHROME_PATTERNS = [
    r"\s*[-–—]\s*PMC\s*$",
    r"\s*[-–—]\s*PubMed(?:\s+Central)?\s*$",
    r"\s*[-–—]\s*ScienceDirect\s*$",
    r"\s*\|\s*Journal of [^|]+\s*\|\s*Springer Nature Link\s*$",
    # Generalisation of the line above, for the shipped
    # "... | Nature Communications | Springer Nature Link" form: exactly one
    # journal segment (no pipes of its own) in front of the Springer chrome.
    r"\s*\|\s*[^|]{1,80}\s*\|\s*Springer Nature Link\s*$",
    r"\s*\|\s*Springer Nature Link\s*$",
    r"\s*\|\s*Nature\s*$",
]

# --- preprint detection -----------------------------------------------------
# DOI prefixes whose whole namespace is a preprint server. 10.1101 is NOT here:
# Cold Spring Harbor Laboratory Press shares it between bioRxiv/medRxiv and its
# journals (Genome Research, Genes & Development, ...), so it needs the suffix
# test below.
PREPRINT_DOI_PREFIXES = {
    "10.21203": "Research Square",
    "10.48550": "arXiv",
    "10.20944": "Preprints.org",
    "10.31219": "OSF Preprints",
}
# A 10.1101 preprint suffix is a posting date ("2020.05.06.093286") or a legacy
# serial ("093286"), optionally versioned; a CSHL journal suffix always carries
# a journal token ("gr.245621.118", "gad.123456", "cshperspect.a033118").
CSHL_PREPRINT_SUFFIX = re.compile(r"^(?:\d{4}\.\d{2}\.\d{2}\.)?\d+(?:v\d+)?$")
# Substring -> display name, longest/most specific first ("psyarxiv" contains
# "arxiv", so it has to be tested before it).
PREPRINT_HOST_NAMES = (
    ("medrxiv", "medRxiv"),
    ("biorxiv", "bioRxiv"),
    ("chemrxiv", "ChemRxiv"),
    ("psyarxiv", "PsyArXiv"),
    ("arxiv", "arXiv"),
    ("research square", "Research Square"),
    ("researchsquare", "Research Square"),
    ("preprints.org", "Preprints.org"),
    ("authorea", "Authorea"),
    ("ssrn", "SSRN"),
)
PREPRINT_MARKERS = tuple(marker for marker, _ in PREPRINT_HOST_NAMES)
PREPRINT_SUFFIX = " (preprint)"

# --- author strings ---------------------------------------------------------
# "TiffanyW . Todd" (shipped): a scrape put a space before the initial's period
# and dropped the one in front of the initial itself.
SPACE_BEFORE_DOT = re.compile(r"\s+\.")
# A token that is a name with a single capital glued to its end -- "TiffanyW",
# "ZhangJ". Requiring the capital to be the token's last letter keeps real names
# safe: "McDonald", "DeVries" and "MacLeod" do not match.
GLUED_INITIAL = re.compile(r"^(\w*[a-z])([A-Z])\.?$")

# --- source field aliases ---------------------------------------------------
# LiteratureSearch, Consensus and Exa each name the same field differently, and
# a curated record is hand-written. Every alias the mapping consumes is listed
# here so ``raw_extras`` can compute the exact remainder: a field that is not
# read by name is kept verbatim rather than silently dropped.
ABSTRACT_KEYS = ("abstract", "abstract_text", "summary")
SEARCH_QUERY_KEYS = ("search_query", "query", "search_queries")
SEARCH_FACET_KEYS = ("search_facet", "facet", "search_facets")
PROVIDER_KEYS = ("provider", "search_provider", "source")
STUDY_TYPE_KEYS = ("study_type", "study_design", "publication_type")
CITATION_KEYS = ("citation_count", "citations", "cited_by_count", "n_citations")
SAMPLE_SIZE_KEYS = ("sample_size", "n_participants", "sample_n", "cohort_size")
RETRACTED_KEYS = ("is_retracted", "retracted")
RETRACTION_NOTICE_KEYS = ("retraction_notice", "retraction_url", "retraction")
TRIAL_ID_KEYS = ("trial_id", "nct_id", "nct", "trial_registration",
                 "registration", "clinical_trial_id", "registry_id")
COHORT_KEYS = ("cohort", "cohort_id", "cohort_name", "consortium")
MAPPED_SOURCE_KEYS = frozenset(
    ("doi", "pmid", "pmcid", "title", "journal", "authors", "year", "url",
     "pdf_url", "is_open_access", "open_access", "arxiv_id", "arxiv",
     "paper_id", "id_type", "first_author_surname", "is_preprint",
     "study_id", "publication_role", "merged_from", "raw")
    + ABSTRACT_KEYS + SEARCH_QUERY_KEYS + SEARCH_FACET_KEYS + PROVIDER_KEYS
    + STUDY_TYPE_KEYS + CITATION_KEYS + SAMPLE_SIZE_KEYS + RETRACTED_KEYS
    + RETRACTION_NOTICE_KEYS + TRIAL_ID_KEYS + COHORT_KEYS
)

# --- retraction -------------------------------------------------------------
# Publishers announce a retraction in the title itself ("RETRACTED ARTICLE: ...",
# "Retraction Note: ..."), which is often the only signal a scraped record
# carries. A retracted paper that reaches a report as ordinary evidence is the
# worst failure this bridge can produce, so the title is screened too -- but
# only in the publishers' marker forms, so that a bibliometric paper about
# retracted articles is not itself flagged as retracted.
RETRACTION_TITLE_CUE = re.compile(
    r"^\s*(?:\[\s*(?:retracted|withdrawn)[^\]]*\]"
    r"|(?:retracted(?:\s+article)?|retraction(?:\s+note)?|withdrawn)"
    r"\s*[:.\-–—])",
    re.IGNORECASE)
TRUE_WORDS = {"true", "yes", "y", "1", "retracted"}

# --- study identity ---------------------------------------------------------
# A registration id is the strongest statement that two papers report ONE study.
# The EudraCT number has no distinctive shape ("2014-001234-12" is also a date
# range), so it only counts when the registry names itself next to it.
TRIAL_ID_PATTERNS = (
    re.compile(r"\bNCT\d{8}\b", re.IGNORECASE),
    re.compile(r"\bISRCTN\d{8}\b", re.IGNORECASE),
    re.compile(r"\bEudraCT[\s:#-]*(\d{4}-\d{6}-\d{2})\b", re.IGNORECASE),
    re.compile(r"\b(?:ChiCTR|ACTRN|UMIN|JPRN|DRKS)[A-Z-]*\d{6,}\b", re.IGNORECASE),
)
# Two titles count as the same title at or above this token-set ratio. 0.9 is
# tight enough that "GRN in FTD" and "GRN in ALS" stay apart.
TITLE_MATCH_RATIO = 0.9
# ...and even then a fuzzy title alone never groups two records: see same_study.
STUDY_YEAR_WINDOW = 1

# Cohort / consortium / biobank names. Acronyms are matched case-sensitively
# because their lowercase forms are ordinary words ("dian", "nacc"); spelled-out
# names are matched case-insensitively. Order is longest/most specific first.
COHORT_PATTERNS = tuple(
    (display, re.compile(pattern, flags)) for display, pattern, flags in (
        ("GENFI", r"\bGENFI\b", 0),
        ("GENFI", r"\bgenetic frontotemporal dementia initiative\b", re.IGNORECASE),
        ("ALLFTD", r"\bALLFTD\b", 0),
        ("LEFFTDS", r"\bLEFFTDS\b", 0),
        ("ARTFL", r"\bARTFL\b", 0),
        ("4RTNI", r"\b4RTNI\b", 0),
        ("ADNI", r"\bADNI\b", 0),
        ("ADNI", r"\balzheimer'?s disease neuroimaging initiative\b", re.IGNORECASE),
        ("DIAN", r"\bDIAN\b", 0),
        ("DIAN", r"\bdominantly inherited alzheimer network\b", re.IGNORECASE),
        ("NACC", r"\bNACC\b", 0),
        ("UK Biobank", r"\bUK ?Biobank\b", re.IGNORECASE),
        ("FinnGen", r"\bFinnGen\b", re.IGNORECASE),
        ("All of Us", r"\ball of us research program\b", re.IGNORECASE),
        ("PPMI", r"\bPPMI\b", 0),
        ("PPMI", r"\bparkinson'?s progression markers initiative\b", re.IGNORECASE),
        ("Framingham", r"\bframingham (?:heart study|study|cohort)\b", re.IGNORECASE),
        ("Rotterdam Study", r"\brotterdam study\b", re.IGNORECASE),
        ("Whitehall II", r"\bwhitehall II\b", re.IGNORECASE),
        ("ENIGMA", r"\bENIGMA\b", 0),
        ("TRACK-HD", r"\bTRACK-HD\b", re.IGNORECASE),
        ("Enroll-HD", r"\bEnroll-HD\b", re.IGNORECASE),
        ("Million Veteran Program", r"\bmillion veteran program\b", re.IGNORECASE),
        ("MRC CFAS", r"\bMRC ?CFAS\b", 0),
    )
)

# --- publication role -------------------------------------------------------
# Ranked strongest-first: when two records of one publication merge, the merged
# record takes the most complete role ("primary" beats the preprint it absorbed).
PUBLICATION_ROLES = ("primary", "secondary_analysis", "interim",
                     "conference_abstract", "preprint", "unknown")
ROLE_RANK = {role: rank for rank, role in enumerate(PUBLICATION_ROLES)}
# Venue cues beat title cues: a paper printed in a supplement of meeting
# abstracts is a conference abstract whatever its title says.
CONFERENCE_VENUE_CUES = ("meeting abstract", "conference abstract",
                         "abstracts of the", "abstract supplement",
                         "conference proceedings", "proceedings of the")
CONFERENCE_TITLE_CUES = ("[abstract]", "(abstract)", "abstract only",
                         "poster abstract", "conference abstract")
INTERIM_CUES = ("interim analysis", "interim results", "interim report",
                "interim findings", "interim data", "preliminary results of",
                "preliminary report")
SECONDARY_CUES = ("secondary analysis", "post hoc analysis", "post-hoc analysis",
                  "exploratory analysis of", "subgroup analysis of",
                  "ancillary study", "substudy", "sub-study",
                  "further analysis of", "secondary outcomes of",
                  "long-term follow-up of", "extension study of")


def unescape_text(value: str | None) -> str:
    """HTML-unescape and collapse whitespace in a scraped metadata field.

    Unescaping repeats until it is stable so double-escaped feeds ("&amp;amp;")
    resolve too; three rounds is far more than any real feed needs.
    """
    if not value:
        return ""
    out = str(value)
    for _ in range(3):
        nxt = html.unescape(out)
        if nxt == out:
            break
        out = nxt
    return re.sub(r"\s+", " ", out).strip()


def clean_title(title: str | None) -> str:
    """Strip trailing scraped page-chrome and unescape entities in a title."""
    out = unescape_text(title)
    # Scrapers chain chrome ("... - PMC - PubMed"), so re-run until stable.
    for _ in range(3):
        before = out
        for pattern in TITLE_CHROME_PATTERNS:
            out = re.sub(pattern, "", out, flags=re.IGNORECASE)
        out = out.strip()
        if out == before:
            break
    return out


def norm_doi(doi: str | None) -> str:
    if not doi:
        return ""
    d = str(doi).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d.strip()


def norm_title(t: str | None) -> str:
    if not t:
        return ""
    return re.sub(r"[^a-z0-9 ]", " ", str(t).lower()).strip()


def title_similarity(a: str | None, b: str | None) -> float:
    """Order-insensitive similarity of two titles, in [0, 1].

    A token-set ratio: both titles are normalized, reduced to their unique
    tokens and sorted before comparison, so a retitled version that reorders or
    repeats words ("Progranulin in microglia" / "Microglial progranulin") still
    scores as the same title. difflib is stdlib, so no dependency is added.
    """
    ta = " ".join(sorted(set(norm_title(a).split())))
    tb = " ".join(sorted(set(norm_title(b).split())))
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb).ratio()


def _first(record: dict, *keys: str) -> str:
    """First non-empty value among ``keys``, cleaned to a string."""
    for key in keys:
        value = record.get(key)
        if isinstance(value, (list, tuple)):
            value = "; ".join(str(v) for v in value if v)
        if value is None or isinstance(value, bool):
            continue
        text = unescape_text(str(value))
        if text:
            return text
    return ""


def _as_int(value) -> int | None:
    """Integer from a count that may arrive as "1,234", "n=57" or 57.0."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    m = re.search(r"-?\d+", str(value).replace(",", ""))
    return int(m.group(0)) if m else None


def _as_bool(value) -> bool:
    """Truthiness of a flag that may arrive as a bool, 0/1 or "true"."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in TRUE_WORDS


def _as_list(value) -> list[str]:
    """Zero or more strings from a scalar-or-list field, order preserved."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in items:
        text = unescape_text(str(item)) if not isinstance(item, bool) else ""
        if text and text not in out:
            out.append(text)
    return out


def _first_list(record: dict, *keys: str) -> list[str]:
    """Items of the first non-empty list-or-scalar field among ``keys``."""
    for key in keys:
        items = _as_list(record.get(key))
        if items:
            return items
    return []


def _year_int(value) -> int | None:
    """Publication year as an int; "2020-05-06" and 2020 both give 2020."""
    m = re.search(r"(1[6-9]|20)\d{2}", str(value or ""))
    return int(m.group(0)) if m else None


def repair_author_spacing(name: str) -> str:
    """Undo the scrape damage in "TiffanyW . Todd" -> "Tiffany W. Todd"."""
    out = SPACE_BEFORE_DOT.sub(".", name)
    parts: list[str] = []
    for token in out.split():
        m = GLUED_INITIAL.match(token)
        if m:
            parts.append(m.group(1))
            parts.append(m.group(2) + ("." if token.endswith(".") else ""))
        else:
            parts.append(token)
    return " ".join(parts)


def authors_to_str(a) -> str:
    """One "; "-joined author string, entity-free and de-mangled.

    Unescaping happens BEFORE the split: a numeric character reference ends in
    a semicolon, so splitting first turns "G&#246;tzl J" into two authors.
    """
    if isinstance(a, list):
        items = [unescape_text(x) for x in a]
    else:
        items = unescape_text(a).split(";")
    cleaned = [repair_author_spacing(x.strip()) for x in items]
    return "; ".join(c for c in cleaned if c)


def surname(name: str) -> str:
    """Best-effort surname from one mixed-format author name.

    Copied from ``_surname`` in scripts/report_model.py (not imported: that
    module is another workstream's). Handles "Zhang J" (surname then initials),
    "J. Zhang", "Zhang, Jian" and "Wu Y H". Taking the last whitespace token
    instead is what shipped "J et al. 2020" for "Zhang J; Velmeshev D; ...".
    """
    name = name.strip().strip(".")
    if not name:
        return ""
    if "," in name:
        return name.split(",")[0].strip()
    parts = [p for p in name.split() if p]
    if not parts:
        return ""
    # Drop trailing initials ("Zhang J", "Wu Y H") -> "Zhang".
    while len(parts) > 1 and len(parts[-1].strip(".")) <= 2 and parts[-1].strip(".").isupper():
        parts.pop()
    # Drop leading initials ("J. Zhang") -> "Zhang".
    while len(parts) > 1 and len(parts[0].strip(".")) <= 2:
        parts.pop(0)
    return parts[-1]


def first_author_surname(authors: str | None) -> str:
    """Surname of the first author in a full author string.

    Normalized once, here, so the renderers stop each deriving it their own way.
    Authors are separated by ";" or "," depending on the source, and the first
    field of a comma form may itself be "Surname, Given".
    """
    first = str(authors or "").split(";")[0].split(",")[0]
    return surname(first)


def _server_from_text(text: str) -> str:
    lowered = (text or "").lower()
    for marker, display in PREPRINT_HOST_NAMES:
        if marker in lowered:
            return display
    return ""


def _server_from_doi(doi: str) -> str:
    prefix, _, suffix = doi.partition("/")
    if prefix in PREPRINT_DOI_PREFIXES:
        return PREPRINT_DOI_PREFIXES[prefix]
    if prefix == "10.1101" and CSHL_PREPRINT_SUFFIX.match(suffix):
        # Both servers share the prefix and the suffix shape; the record's own
        # text is the only hint, and bioRxiv is the larger of the two.
        return "bioRxiv"
    return ""


def detect_preprint(doi: str, journal: str, url: str,
                    arxiv_id: str = "") -> tuple[bool, str]:
    """Return ``(is_preprint, journal_display)``.

    ``journal_display`` is the value to show in place of the journal, e.g.
    "Research Square (preprint)"; it is "" when the record is not a preprint.
    A preprint host in the URL only counts when the journal is missing, so a
    published paper whose record links its preprint is not relabelled.
    """
    server = _server_from_doi(doi)
    if not server and str(arxiv_id).strip():
        server = "arXiv"
    if not server:
        server = _server_from_text(f"{journal} {doi}")
    if not server and not journal.strip():
        server = _server_from_text(url)
    if not server:
        return False, ""
    if server == "bioRxiv":
        # medRxiv shares bioRxiv's DOI prefix and suffix shape.
        server = _server_from_text(f"{journal} {url}") or server
    return True, f"{server}{PREPRINT_SUFFIX}"


def _record_text(record: dict) -> str:
    """Every string a record carries, joined -- for cue detection only."""
    # study_id is included so a registration resolved once, at mapping time,
    # is still visible to assign_study_ids after the source keys are gone.
    parts = [str(record.get(k) or "") for k in
             ("title", "abstract", "journal", "study_id") + TRIAL_ID_KEYS + COHORT_KEYS]
    raw = record.get("raw")
    if isinstance(raw, dict):
        parts += [str(v) for v in raw.values() if isinstance(v, (str, int))]
    return " ".join(p for p in parts if p)


def trial_registration(record: dict) -> str:
    """The record's trial-registration id (NCT/ISRCTN/EudraCT/...), or "".

    Two papers that name the same registration are two reports of one trial --
    the only identity signal strong enough to be trusted on its own.
    """
    text = _record_text(record)
    for pattern in TRIAL_ID_PATTERNS:
        m = pattern.search(text)
        if m:
            # A capturing group means the registry name was matched as context
            # (EudraCT); the id itself is the group.
            return (m.group(1) if m.groups() else m.group(0)).upper()
    return ""


def detect_cohort(*texts: str) -> str:
    """Canonical name of the cohort/consortium/biobank a record draws on.

    Returns "" when none is detectable. Two papers on the same cohort are not
    independent samples, so the name has to survive into the corpus for a
    reader (or a convergence count) to notice.
    """
    haystack = " ".join(str(t) for t in texts if t)
    if not haystack:
        return ""
    for display, pattern in COHORT_PATTERNS:
        if pattern.search(haystack):
            return display
    return ""


def detect_publication_role(title: str, journal: str, doi: str = "",
                            is_preprint: bool = False,
                            study_type: str = "") -> str:
    """Which kind of publication this record is: see PUBLICATION_ROLES.

    Content cues are tested before the preprint test, because "interim" and
    "conference_abstract" say more about how much weight the record deserves
    than the server it was posted on -- an interim analysis on medRxiv is an
    interim analysis. A record with neither cues nor a journal is "unknown"
    rather than "primary": absent evidence is not evidence of a full paper.
    """
    title_l = (title or "").lower()
    venue_l = f"{journal or ''} {study_type or ''}".lower()
    if (any(cue in venue_l for cue in CONFERENCE_VENUE_CUES)
            or any(cue in title_l for cue in CONFERENCE_TITLE_CUES)):
        return "conference_abstract"
    if any(cue in title_l for cue in INTERIM_CUES):
        return "interim"
    if any(cue in title_l for cue in SECONDARY_CUES):
        return "secondary_analysis"
    if is_preprint or _server_from_doi(norm_doi(doi)):
        return "preprint"
    return "primary" if (journal or "").strip() else "unknown"


def detect_retraction(record: dict, title: str = "") -> tuple[bool, str]:
    """Return ``(is_retracted, retraction_notice)`` for a source record.

    The flag was dropped entirely before this existed, so a retracted paper
    entered the corpus indistinguishable from a live one. A notice implies the
    flag, and so does a publisher's "RETRACTED ARTICLE:" title prefix.
    """
    notice = _first(record, *RETRACTION_NOTICE_KEYS)
    flagged = any(_as_bool(record.get(k)) for k in RETRACTED_KEYS)
    if notice.strip().lower() in TRUE_WORDS:  # "retraction": true, not a notice
        flagged, notice = True, ""
    if bool(RETRACTION_TITLE_CUE.match(title or "")):
        flagged = True
    return bool(flagged or notice), notice


def raw_extras(record: dict) -> dict:
    """Every source field the mapping did not consume, kept verbatim.

    Lossless by construction: the mapping reads a known alias list
    (MAPPED_SOURCE_KEYS), and whatever is left over lands here instead of being
    dropped, so a field a future provider adds survives into the corpus even
    though this module has never heard of it.
    """
    inherited = record.get("raw")
    extras = dict(inherited) if isinstance(inherited, dict) else {}
    for key, value in record.items():
        if key not in MAPPED_SOURCE_KEYS:
            extras[key] = value
    return extras


def pick_id(doi: str, pmid: str, pmcid: str, index: int) -> tuple[str, str]:
    """Return (paper_id, id_type) from the strongest available identifier."""
    if pmcid:
        return pmcid, "pmcid"
    if pmid:
        return str(pmid), "pmid"
    if doi:
        return doi, "doi"
    return f"P-{index:04d}", "local"


def record_from_litsearch(r: dict, index: int) -> dict:
    doi = norm_doi(r.get("doi"))
    pmid = str(r.get("pmid") or "").strip()
    pmcid = str(r.get("pmcid") or "").strip().upper()
    if pmcid and not pmcid.startswith("PMC"):
        pmcid = "PMC" + pmcid
    paper_id, id_type = pick_id(doi, pmid, pmcid, index)
    url = str(r.get("url") or "").strip() or (f"https://doi.org/{doi}" if doi else "")
    supplied_pdf = str(_first(r, "pdf_url") or "").strip()
    if not supplied_pdf and re.search(r"\.pdf(?:[?#]|$)", url, re.IGNORECASE):
        # Exa may return a publisher/repository PDF URL directly. Preserve that
        # candidate; it still has to pass acquire.py's PDF-byte validation.
        supplied_pdf = url
    title = clean_title(r.get("title"))
    journal = unescape_text(r.get("journal"))
    authors = authors_to_str(r.get("authors"))
    is_preprint, preprint_journal = detect_preprint(
        doi, journal, url, str(r.get("arxiv_id") or r.get("arxiv") or ""))
    if is_preprint:
        # A preprint with no journal renders as an orphan period ("... FTD. .")
        # and hides that the source was never peer reviewed. Name the server.
        journal = preprint_journal
    abstract = _first(r, *ABSTRACT_KEYS)
    study_type = _first(r, *STUDY_TYPE_KEYS)
    is_retracted, retraction_notice = detect_retraction(r, title)
    raw = raw_extras(r)
    cohort = (_first(r, *COHORT_KEYS)
              or detect_cohort(title, abstract, journal))
    record = {
        "paper_id": paper_id,
        "id_type": id_type,
        "doi": doi or None,
        "pmid": pmid or None,
        "pmcid": pmcid or None,
        "title": title or None,
        "year": r.get("year") or None,
        "journal": journal or None,
        "authors": authors or None,
        "first_author_surname": first_author_surname(authors) or None,
        # Only provider-supplied direct candidates belong here. A PMCID is
        # enough for acquire.py to use the official JATS/OA routes; synthesizing
        # Europe PMC's website-render URL duplicated traffic and made bulk
        # retrieval look like interactive page use.
        "pdf_url": supplied_pdf or None,
        "is_open_access": bool(r.get("is_open_access", r.get("open_access", False))),
        "is_preprint": is_preprint,
        "url": url or None,
        "abstract": abstract or None,
        # Why this paper is in the corpus at all. Lists because one paper is
        # routinely surfaced by several facets, and each one is evidence.
        "search_query": _first_list(r, *SEARCH_QUERY_KEYS),
        "search_facet": _first_list(r, *SEARCH_FACET_KEYS),
        "provider": _first(r, *PROVIDER_KEYS) or None,
        "study_type": study_type or None,
        "citation_count": _as_int(_first(r, *CITATION_KEYS)),
        "sample_size": _as_int(_first(r, *SAMPLE_SIZE_KEYS)),
        "is_retracted": is_retracted,
        "retraction_notice": retraction_notice or None,
        "cohort_id": cohort or None,
        "publication_role": detect_publication_role(
            title, journal, doi, is_preprint, study_type),
        "merged_from": [],
        "raw": raw,
    }
    # A registration shared with another record is resolved in assign_study_ids;
    # on its own a record is its own study.
    record["study_id"] = trial_registration({**r, **record}) or paper_id
    return record


# --- grouping ---------------------------------------------------------------
# A tiny union-find, so a record that matches one row by DOI and another by
# title pulls all three into one group instead of picking a winner arbitrarily.
def _find(parent: list[int], i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent: list[int], a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[max(ra, rb)] = min(ra, rb)


def _groups(parent: list[int]) -> list[list[int]]:
    """Members of each group, in input order, groups in first-seen order."""
    grouped: dict[int, list[int]] = {}
    for i in range(len(parent)):
        grouped.setdefault(_find(parent, i), []).append(i)
    return [grouped[root] for root in sorted(grouped)]


def dedupe_keys(r: dict) -> dict:
    """The exact-match identifiers two records must share to be one record."""
    return {
        "doi": norm_doi(r.get("doi")),
        "pmid": str(r.get("pmid") or "").strip().lower(),
        "pmcid": str(r.get("pmcid") or "").strip().lower(),
        "title": norm_title(r.get("title")),
    }


def _richer_title(current: str, incoming: str) -> str:
    """The fuller of two titles -- but only when they are the same title.

    Length alone must not decide: "Third" and "Third again" are two papers that
    collided on an identifier, and taking the longer one would rewrite the kept
    record's title to the other paper's. So the longer title wins only when the
    two are near-identical, i.e. one is a fuller rendering of the other.
    """
    if not current:
        return incoming
    if not incoming:
        return current
    fuller = len(norm_title(incoming)) > len(norm_title(current))
    if fuller and title_similarity(current, incoming) >= TITLE_MATCH_RATIO:
        return incoming
    return current


def _merge_venue(kept: dict, other: dict) -> tuple[str, bool]:
    """``(journal, is_preprint)`` for the union of two records.

    ORing ``is_preprint`` would relabel a published paper as a preprint the
    moment its bioRxiv posting merged into it -- exactly the mislabelling
    detect_preprint exists to prevent. The merged record is a preprint only if
    every version of it is, and it takes the journal of a version that agrees
    with that verdict (never a "(preprint)" label on a published paper).
    """
    versions = [(str(kept.get("journal") or ""), bool(kept.get("is_preprint"))),
                (str(other.get("journal") or ""), bool(other.get("is_preprint")))]
    preprint = all(pre for _, pre in versions)
    named = [j for j, pre in versions if j and pre == preprint]
    return (named[0] if named else ""), preprint


def _union_list(a, b) -> list:
    """Concatenation of two list fields, order preserved, without repeats."""
    out: list = []
    for item in list(a or []) + list(b or []):
        if item not in out:
            out.append(item)
    return out


def _merge_provider(kept: dict, other: dict) -> str | None:
    """Providers that surfaced the record, "; "-joined when they differ."""
    names = _union_list(_as_list(kept.get("provider")), _as_list(other.get("provider")))
    return "; ".join(names) or None


# Scalar fields whose discarded value is worth keeping: a merge that silently
# forgets the other record's DOI is the bug this whole function exists to fix.
STASHED_FIELDS = ("doi", "pmid", "pmcid", "url", "pdf_url", "title", "journal",
                  "abstract", "authors", "year")
ID_STRENGTH = {"pmcid": 3, "pmid": 2, "doi": 1, "local": 0}


def merge_records(kept: dict, other: dict) -> dict:
    """Fold ``other`` into ``kept``; return their union.

    Dedup used to ``continue`` past a duplicate, keeping whichever record
    happened to arrive first and dropping the other whole. When the survivor
    had no pmcid and the loser did, acquisition lost the paper's only
    open-access route and the report called it unretrievable -- for a paper it
    could have downloaded. So nothing is dropped: identifiers fill each other's
    blanks, retraction and access flags are ORed (a retraction known to either
    record is known), search queries accumulate (a paper surfaced by three
    facets is more strongly indicated than one surfaced by one), and a value
    that genuinely conflicts is parked under ``raw['merged_<field>']``.
    """
    # Exact-title deduplication often sees a preprint before its version of
    # record. Treat the published row as one atomic citation identity. Merging
    # the venue independently from the DOI produced a real hybrid citation:
    # Nature Cell Biology beside a bioRxiv DOI, then prose that called the
    # already-published result a preprint. The preprint PDF may still be the
    # best readable full text, so ``pdf_url`` remains a fillable access route;
    # DOI/PMID/year/journal/URL/paper_id must all come from one version.
    versions = (kept, other)
    published = [row for row in versions if row.get("is_preprint") is False]
    preprints = [row for row in versions if row.get("is_preprint") is True]
    preferred_version = published[0] if published and preprints else None

    merged = dict(kept)
    for field in ("doi", "pmid", "pmcid", "url", "pdf_url", "year", "abstract",
                  "study_type", "sample_size", "cohort_id", "retraction_notice",
                  "study_id", "first_author_surname"):
        if not merged.get(field) and other.get(field):
            merged[field] = other[field]
    merged["title"] = _richer_title(str(kept.get("title") or ""),
                                    str(other.get("title") or "")) or None
    # The longer author list is the more complete one; the surname follows it.
    if len(str(other.get("authors") or "")) > len(str(kept.get("authors") or "")):
        merged["authors"] = other["authors"]
        merged["first_author_surname"] = (
            first_author_surname(merged["authors"]) or merged.get("first_author_surname"))
    # The fullest abstract, not the first one seen.
    if len(str(other.get("abstract") or "")) > len(str(merged.get("abstract") or "")):
        merged["abstract"] = other["abstract"]
    journal, is_preprint = _merge_venue(kept, other)
    merged["journal"] = journal or None
    merged["is_preprint"] = is_preprint
    for flag in ("is_open_access", "is_retracted"):
        merged[flag] = bool(kept.get(flag)) or bool(other.get(flag))
    counts = [c for c in (_as_int(kept.get("citation_count")),
                          _as_int(other.get("citation_count"))) if c is not None]
    if counts:
        merged["citation_count"] = max(counts)
    roles = [r for r in (kept.get("publication_role"), other.get("publication_role"))
             if r in ROLE_RANK]
    if roles:
        merged["publication_role"] = min(roles, key=lambda r: ROLE_RANK[r])
    if preferred_version is not None:
        for field in (
            "paper_id", "id_type", "doi", "pmid", "pmcid", "url", "year",
            "journal", "publication_role",
        ):
            if preferred_version.get(field):
                merged[field] = preferred_version[field]
        merged["is_preprint"] = False
    for field in ("search_query", "search_facet"):
        if kept.get(field) or other.get(field):
            merged[field] = _union_list(kept.get(field), other.get(field))
    if kept.get("provider") or other.get("provider"):
        merged["provider"] = _merge_provider(kept, other)
    raw = dict(other.get("raw") or {})
    raw.update(dict(kept.get("raw") or {}))  # the kept record wins a clash
    for field in STASHED_FIELDS:
        dropped = [v for v in (kept.get(field), other.get(field))
                   if v and v != merged.get(field)]
        if dropped:
            raw[f"merged_{field}"] = _union_list(raw.get(f"merged_{field}"), dropped)
    if raw or "raw" in merged:
        merged["raw"] = raw
    merged["merged_from"] = _union_list(
        _union_list(kept.get("merged_from"), other.get("merged_from")),
        [
            row["paper_id"] for row in versions
            if row.get("paper_id") and row.get("paper_id") != merged.get("paper_id")
        ],
    )
    if kept.get("id_type") in ID_STRENGTH:
        # A rescued identifier can be stronger than the one paper_id was built
        # from; pick_id's contract is "the strongest available".
        new_id, new_type = pick_id(str(merged.get("doi") or ""),
                                   str(merged.get("pmid") or ""),
                                   str(merged.get("pmcid") or ""), 0)
        if ID_STRENGTH[new_type] > ID_STRENGTH[kept["id_type"]]:
            previous_id = merged.get("paper_id")
            merged["paper_id"], merged["id_type"] = new_id, new_type
            # The id this record used to answer to, so it stays traceable.
            merged["merged_from"] = _union_list(
                merged["merged_from"],
                [previous_id] if previous_id else [])
    return merged


def dedupe(rows: list[dict]) -> list[dict]:
    """One record per publication, merged rather than first-wins.

    Records are grouped by any shared exact identifier (DOI, PMID, PMCID) or
    exactly-normalized title -- transitively, so a chain of partial overlaps
    collapses into one record -- and each group is folded with merge_records.
    Retitled versions of one *study* are NOT collapsed here: they are distinct
    publications and both stay citable; assign_study_ids gives them a shared
    study_id instead.
    """
    parent = list(range(len(rows)))
    first_seen: dict[tuple[str, str], int] = {}
    for i, r in enumerate(rows):
        for kind, value in dedupe_keys(r).items():
            if value:
                _union(parent, i, first_seen.setdefault((kind, value), i))
    out = []
    for group in _groups(parent):
        record = rows[group[0]]
        for member in group[1:]:
            record = merge_records(record, rows[member])
        out.append(record)
    return out


def _study_year(record: dict) -> int | None:
    return _year_int(record.get("year"))


def _study_author(record: dict) -> str:
    name = str(record.get("first_author_surname")
               or first_author_surname(record.get("authors")) or "")
    return name.strip().lower()


def same_study(a: dict, b: dict) -> bool:
    """True when two records are two publications of one study.

    Deliberately hard to satisfy. A false merge deletes a genuine independent
    replication from the convergence count -- it makes the evidence look
    thinner or, worse, makes two unrelated papers look like one confirmed
    result -- while a missed merge only leaves the old, visible situation. So a
    fuzzy title is never enough on its own: the first author must match and the
    years must be adjacent (a preprint and its journal version are rarely more
    than a year apart). Records missing an author or a year are left alone.
    """
    author_a, author_b = _study_author(a), _study_author(b)
    if not author_a or author_a != author_b:
        return False
    year_a, year_b = _study_year(a), _study_year(b)
    if year_a is None or year_b is None:
        return False
    if abs(year_a - year_b) > STUDY_YEAR_WINDOW:
        return False
    return title_similarity(a.get("title"), b.get("title")) >= TITLE_MATCH_RATIO


def assign_study_ids(rows: list[dict]) -> list[dict]:
    """Stamp a shared ``study_id`` on records reporting the same study.

    Two signals, in order of strength: a shared trial registration, then the
    conservative first-author + adjacent-year + fuzzy-title match of
    same_study. The group's id is its registration when it has one, else the
    paper_id of its most complete publication (a journal article over the
    preprint it grew out of), so the id does not change when a rerun happens to
    see the preprint first.
    """
    out = [dict(r) for r in rows]
    parent = list(range(len(out)))
    trials = [trial_registration(r) for r in out]
    first_trial: dict[str, int] = {}
    for i, trial in enumerate(trials):
        if trial:
            _union(parent, i, first_trial.setdefault(trial, i))
    # Bucketed by first author so the fuzzy comparison stays near-linear; two
    # records can only be one study if the author key matches anyway.
    buckets: dict[str, list[int]] = {}
    for i, r in enumerate(out):
        author = _study_author(r)
        if author:
            buckets.setdefault(author, []).append(i)
    for members in buckets.values():
        for pos, i in enumerate(members):
            for j in members[pos + 1:]:
                if same_study(out[i], out[j]):
                    _union(parent, i, j)
    for group in _groups(parent):
        trial = next((trials[i] for i in group if trials[i]), "")
        best = min(group, key=lambda i: (ROLE_RANK.get(
            out[i].get("publication_role"), len(ROLE_RANK)), i))
        study_id = trial or str(out[best].get("paper_id") or f"S-{best + 1:04d}")
        for i in group:
            out[i]["study_id"] = study_id
    return out


def parse_jsonl_text(text: str) -> list[dict]:
    """Every JSON object in a JSONL blob; unparseable lines are skipped.

    A slice that starts mid-line (an offset captured from a different file, or
    a trace rewritten under us) loses only that first partial line.
    """
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            v = json.loads(line)
            if isinstance(v, dict):
                rows.append(v)
        except json.JSONDecodeError:
            continue
    return rows


def read_jsonl(path: pathlib.Path) -> list[dict]:
    return parse_jsonl_text(path.read_text(encoding="utf-8", errors="replace"))


# --- run-scoped ingestion ---------------------------------------------------
NO_OFFSET_WARNING = (
    "no --since-offset was supplied, so the ENTIRE global references.jsonl was "
    "consumed. references.jsonl is append-only and shared by every task in the "
    "session, so this corpus may contain LiteratureSearch records from earlier, "
    "unrelated tasks. Capture the offset before searching "
    "(references_to_corpus.py --refs <file> --print-offset) and pass it back as "
    "--since-offset to scope the corpus to this run."
)


def current_offset(path) -> int:
    """Byte offset a later run should resume from: the file's size right now.

    Call this BEFORE the run's first LiteratureSearch and hand the number back
    as --since-offset afterwards. A missing file is offset 0, so the very first
    task of a session needs no special case.
    """
    try:
        return pathlib.Path(path).stat().st_size
    except OSError:
        return 0


def read_slice(path, start: int = 0) -> tuple[str, int, int]:
    """Return ``(text, start_offset, end_offset)`` for the bytes after ``start``.

    The slice is returned verbatim so it can be snapshotted byte-for-byte: the
    run's corpus must be rebuildable from the run's own artifacts, not from a
    global file that will have grown by then. A ``start`` past EOF (the trace
    was rotated or truncated between the capture and the read) is clamped to
    the file size, which yields an empty slice instead of an exception.
    """
    p = pathlib.Path(path)
    if not p.exists():
        return "", 0, 0
    size = p.stat().st_size
    begin = max(0, min(int(start or 0), size))
    with p.open("rb") as handle:
        handle.seek(begin)
        data = handle.read()
    return data.decode("utf-8", errors="replace"), begin, begin + len(data)


def ingestion_record(source, start: int, end: int, count: int,
                     scoped: bool, **extra) -> dict:
    """The provenance of one ingestion, written to corpus/ingestion.json.

    ``WARNING`` is the load-bearing field: it is a non-empty string exactly
    when the run consumed the whole global trace and therefore cannot promise
    the corpus belongs to this task alone.
    """
    return {
        "source": str(source),
        "start_offset": int(start),
        "end_offset": int(end),
        "bytes_consumed": int(end) - int(start),
        "record_count": int(count),
        "run_scoped": bool(scoped),
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
                             .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "WARNING": None if scoped else NO_OFFSET_WARNING,
        **extra,
    }


def csv_row(record: dict) -> dict:
    """A corpus record flattened for the CSV: containers become JSON strings."""
    row = dict(record)
    for col in CONTAINER_COLS:
        value = row.get(col)
        if isinstance(value, (list, dict, tuple)):
            row[col] = (json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if value else "")
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Consolidate LiteratureSearch -> corpus")
    ap.add_argument(
        "--refs",
        default="/mnt/results/execution_trace/references.jsonl",
        help="LiteratureSearch references.jsonl (Biomni writes this).",
    )
    ap.add_argument("--curated", help="Optional curated records JSON (list of dicts).")
    ap.add_argument("--run-root", help="Run root; writes corpus/ under it.")
    ap.add_argument(
        "--since-offset", type=int, default=None, metavar="N",
        help="Byte offset in --refs to start reading at, as reported by "
             "--print-offset before this run's first search. Without it the "
             "whole global (append-only, session-wide) file is consumed and "
             "earlier tasks' records can enter this corpus.",
    )
    ap.add_argument(
        "--snapshot-out",
        help="Where to copy the consumed slice (default: "
             "<run-root>/corpus/references_snapshot.jsonl).",
    )
    ap.add_argument(
        "--print-offset", action="store_true",
        help="Print the current byte offset of --refs and exit. Run this "
             "BEFORE searching, then pass the number back as --since-offset.",
    )
    args = ap.parse_args(argv)

    refs_path = pathlib.Path(args.refs)
    if args.print_offset:
        print(current_offset(refs_path))
        return 0
    if not args.run_root:
        ap.error("--run-root is required (except with --print-offset)")

    scoped = args.since_offset is not None
    if not refs_path.exists():
        print(f"[bridge] WARNING: {refs_path} not found; no LiteratureSearch records.",
              file=sys.stderr)
    slice_text, start, end = read_slice(refs_path, args.since_offset or 0)
    raw: list[dict] = parse_jsonl_text(slice_text)
    refs_count = len(raw)
    if not scoped:
        print(f"[bridge] WARNING: {NO_OFFSET_WARNING}", file=sys.stderr)

    curated: list[dict] = []
    if args.curated:
        cpath = pathlib.Path(args.curated)
        if cpath.exists():
            val = json.loads(cpath.read_text())
            for row in (val if isinstance(val, list) else val.get("records", [])):
                if isinstance(row, dict):
                    # Provenance: a hand-picked record is not a search hit.
                    curated.append({"provider": "curated", **row})
    raw.extend(curated)

    mapped = [record_from_litsearch(r, i) for i, r in enumerate(raw, 1)]
    deduped = assign_study_ids(dedupe(mapped))

    run_root = pathlib.Path(args.run_root)
    corpus = run_root / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    snapshot = (pathlib.Path(args.snapshot_out) if args.snapshot_out
                else corpus / "references_snapshot.jsonl")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(slice_text, encoding="utf-8")

    with (corpus / "references.jsonl").open("w", encoding="utf-8") as h:
        for r in deduped:
            h.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    with (corpus / "corpus.csv").open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=CORPUS_COLS, extrasaction="ignore")
        w.writeheader()
        for r in deduped:
            w.writerow(csv_row(r))

    ingestion = ingestion_record(
        refs_path, start, end, refs_count, scoped,
        source_exists=refs_path.exists(),
        snapshot=str(snapshot),
        curated_path=str(args.curated) if args.curated else None,
        curated_count=len(curated),
        mapped_count=len(mapped),
        record_count_unique=len(deduped),
        merged_count=len(mapped) - len(deduped),
        study_count=len({r.get("study_id") for r in deduped}),
    )
    (corpus / "ingestion.json").write_text(
        json.dumps(ingestion, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    # Seed the durable end-to-end inventory immediately.  At this point the
    # selected set is intentionally empty; the managed-machine preparation step
    # refreshes and validates it after scope decisions are recorded.
    from corpus_ledger import refresh as refresh_corpus_ledger
    refresh_corpus_ledger(run_root)

    print(f"[bridge] {len(deduped)} unique records from {len(mapped)} mapped "
          f"({refs_count} raw LiteratureSearch rows, {len(curated)} curated) "
          f"covering {ingestion['study_count']} studies")
    print(f"[bridge] consumed bytes {start}-{end} of {refs_path}"
          f"{'' if scoped else ' (WHOLE FILE -- see corpus/ingestion.json)'}")
    print(f"[bridge] wrote {corpus/'references.jsonl'}, {corpus/'corpus.csv'}, "
          f"{snapshot}, {corpus/'ingestion.json'} and "
          f"{corpus/'corpus_ledger.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
