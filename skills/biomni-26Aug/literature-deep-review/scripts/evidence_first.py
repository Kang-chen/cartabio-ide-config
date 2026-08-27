#!/usr/bin/env python3
"""Claim-first, source-grounded literature evidence pipeline.

The expensive model stage sees only high-scoring source blocks, batched once per
paper. All accepted evidence is then checked deterministically against the
canonical block store. The runner is idempotent: parsed papers and model batches
are cached under the run root and progress is recorded in run_manifest.json.
"""
from __future__ import annotations

import support_policy  # noqa: E402
import parse_quality
import evidence_lineage
import runtime_metrics

import argparse
import concurrent.futures
import hashlib
import importlib
import json
import math
import os
import pathlib
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from typing import Any

from anchor_policy import (
    is_hypothetical,
    may_be_primary,
    primary_downgrade_reason,
)
from adjudication_batches import (
    DEFAULT_ADJUDICATION_JOBS,
    MAX_ADJUDICATION_JOBS,
    candidate_batches as _candidate_batches,
    emit_batches,
    run_provider_batches,
)
from quote_integrity import problems as quote_problems
from intake_policy import OCR_MODES, require_figure_intake
from pipeline_io import (
    access_label as _access_label,
    atomic_json,
    dedupe_records,
    doi_url as _doi_url,
    load_claims,
    load_records,
    read_jsonl,
    safe_id,
    utc_now,
    write_csv,
    write_jsonl,
)


HERE = pathlib.Path(__file__).resolve().parent
VENDOR = HERE / "vendor" / "keyword_evidence"

# RETRIEVAL BUDGET, not report length.
#
# These bound how many candidate blocks are sent to the adjudicator per claim —
# a cost control on LLM calls. They are NOT a limit on how much evidence the
# report may show: every accepted row is rendered, and the report is as long as
# the evidence requires.
#
# They were nonetheless acting as a length limit. `top_per_paper: 3` meant a
# paper with eight sentences bearing on a claim could offer at most three, so
# two shipped `broad` reports turned 17 and 18 retrieved full texts into 45 and
# 29 quotes across ~13 pages of body — most of the acquired evidence was never
# even shown to the adjudicator. The budgets below are raised roughly 3x, which
# costs more tokens per claim and is the right trade: acquiring a full text and
# then declining to read most of it is the expensive mistake.
#
# `figure_quota` is a RESERVED per-paper allowance for caption / figure_ocr
# blocks: that many figure blocks are added to a claim's candidate set IN
# ADDITION TO the `top_per_paper` sentence slots, so a figure never has to
# outcompete body prose for the same slot. Without it, captions and in-figure
# OCR text almost never survived ranking, no claim was ever grounded on a
# figure, and the report shipped with (almost) no embedded figures. It is 0 for
# `quick` because that mode runs with OCR off.
#
# `max_papers=None` means no mode-imposed cap. Broad review size is determined
# by the relevant records selected after search; users may still set an exact
# positive ceiling with --max-papers or run_manifest.config.max_papers.
MODE_DEFAULTS = {
    "quick": {
        "max_papers": 5,
        "top_per_paper": 4,
        "top_per_claim": 24,
        "claims_per_call": 8,
        "max_blocks_per_call": 32,
        "figure_quota": 0,
        "ocr": "off",
    },
    "deep": {
        "max_papers": 15,
        "top_per_paper": 8,
        "top_per_claim": 80,
        # One batch per paper, same reason as broad below; the block budget
        # scales with the claim count so blocks-per-claim is unchanged.
        "claims_per_call": 25,
        "max_blocks_per_call": 200,
        "figure_quota": 4,
        "ocr": "targeted",
    },
    "broad": {
        "max_papers": None,
        "top_per_paper": 8,
        "top_per_claim": 120,
        # One batch per paper, not seven. At 4 claims per call a 27-paper /
        # 25-claim review produced up to 189 adjudication units; done one
        # conversational turn at a time that is the whole runtime. The block
        # budget rises with the claim count so each claim still gets the same
        # number of candidate blocks to be judged against — this buys fewer,
        # larger prompts, not thinner evidence.
        "claims_per_call": 25,
        "max_blocks_per_call": 300,
        "figure_quota": 4,
        "ocr": "targeted",
    },
}

# Block types that carry figure evidence (legend text and in-panel OCR lines).
FIGURE_BLOCK_TYPES = ("caption", "figure_ocr")

# Shortest in-panel OCR string that may be quoted as evidence. Panel text is
# exempt from the full-sentence rule, which let a one-character quote ("5")
# through every gate and be accepted as `primary`.
MIN_FIGURE_OCR_QUOTE_CHARS = 3

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-5-mini",
    "ollama": "gemma3",
}

# Bumped whenever the run_manifest layout changes; a manifest written by any
# other version is regenerated rather than resumed (see `_load_manifest`).
MANIFEST_SCHEMA_VERSION = 2

STANCE_VALUES = {"supports", "contradicts", "mentions"}
EVIDENCE_KINDS = {"primary", "indirect", "control", "secondary", "correlative", "inferred"}
NON_PRIMARY = EVIDENCE_KINDS - {"primary"}

# ---------------------------------------------------------------------------
# Attribution detector: a quote that carries an inline citation marker or uses
# background/reporting phrasing is describing ANOTHER source's work, so it may
# not be counted as `primary` (an original result of the paper being quoted).
# This makes evidence_kind labeling consistent and prevents a background
# sentence that cites a reference (e.g. "... in a gene-dose dependent manner5")
# from silently propping up a claim's support state. Genuine primary sentences
# ("we show", "our data", "these findings", "providing proof of concept") have
# no such marker and are unaffected.
# ---------------------------------------------------------------------------
CITATION_MARKER_RE = re.compile(
    r"("
    r"\[\s*\d+(?:\s*[,;\u2013-]\s*\d+)*\s*\]"                   # [5]  [52, 53]  [1-3]
    r"|\(\s*\d{1,3}(?:\s*[,;\u2013-]\s*\d{1,3})+\s*\)"          # (3,4)  (12-15)
    r"|\(\s*\d{1,3}\s*\)\s*[.;,]?\s*$"                          # trailing "(4)."
    r"|(?<=[a-z\)])\d{1,3}(?:\s*,\s*\d{1,3})*(?=[\s.,;:]|$)"    # word5   41,42   production23
    # residual superscript refs at the very end ("... in the serum\u00b3\u2070."); anchored
    # so exponents/units mid-sentence ("10\u2076 cells/ml") are not misread as refs.
    r"|[\u00b9\u00b2\u00b3\u2070-\u2079]+\s*[.;,]?\s*$"
    r")"
)
# Author-year parentheticals: "(Martens et al., 2012)", "(Baker and Hutton, 2006;
# Smith 2019)". Requires BOTH a capitalized surname and a standalone 4-digit year
# inside the same parentheses, so "(the 2019 cohort)" (no surname) and
# "(NCT01966666)" (year-like digits inside a longer token) do not match.
AUTHOR_YEAR_RE = re.compile(
    r"\((?=[^)]*\b(?:19|20)\d{2}[a-z]?\b)[^)]*?[A-Z][A-Za-z\u00c0-\u024f'\u2019-]+[^)]*\)"
)
# Allele/isoform tokens (apoE4, APOE-e4, E4/E4, ε4, apolipoprotein E4, ...) carry
# digits that are NOT citation markers. Mask them before scanning so a genuine
# finding about "the APOE-e4 allele" or "apoE4 > apoE3 > apoE2" is not misread as
# citing references 4/3/2.
_ISOFORM_RE = re.compile(
    r"\b(?:apo\s?e\s?[234]"
    r"|apoe[-\s]?e?[234]"
    r"|e[234]\s*/\s*e[234]"
    r"|[\u03b5e][234]"
    r"|apolipoprotein\s+e\s?[234])\b",
    re.IGNORECASE,
)
_ALLELE_TAIL_RE = re.compile(r"(?<=[A-Za-z\-])[eE][234]\b")
# Gene/protein symbols commonly contain terminal digits.  The generic citation
# detector intentionally catches ``word5`` superscript-like references, but it
# must not turn SLC33A1, Slc33a1, KEAP1 or NRF2 into citations 1/1/1/2.
_GENE_SYMBOL_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9-]*\d[A-Za-z0-9-]*"
    r"|[A-Z][a-z]+\d[A-Za-z0-9-]*"
    r"|[a-z]+\d+[a-z]+\d*"
    r"|[a-z]{1,4}\d{1,3})\b"
)


def _mask_isoforms(text: str) -> str:
    text = _ISOFORM_RE.sub(lambda m: re.sub(r"[234]", "#", m.group()), text)
    text = _ALLELE_TAIL_RE.sub(
        lambda m: m.group().translate(str.maketrans("234", "###")), text
    )
    return text


def _mask_gene_symbols(text: str) -> str:
    return _GENE_SYMBOL_RE.sub(
        lambda match: re.sub(r"\d", "#", match.group()), text
    )


ATTRIBUTION_RE = re.compile(
    r"\b("
    r"has\s+been\s+(?:\w+ly\s+)?(?:shown|reported|demonstrated|suggested|found|proposed|described|observed)"
    r"|have\s+been\s+(?:\w+ly\s+)?(?:shown|reported|demonstrated|suggested|found|proposed|described|observed)"
    r"|(?:was|were)\s+(?:\w+ly\s+)?(?:shown|reported|demonstrated|found|observed)\s+to"
    r"|studies?\s+(?:have\s+)?(?:\w+ly\s+)?(?:shown|revealed|reported|demonstrated|suggested|found)"
    r"|it\s+(?:has\s+been|is|was)\s+(?:\w+ly\s+)?(?:shown|reported|suggested|thought|proposed|known)"
    r"|(?:previous|earlier|prior|other|several|many|numerous)(?:ly)?\s+"
    r"(?:studies|study|work|works|reports|findings|groups|investigators|authors)"
    r"|according\s+to"
    r"|reportedly"
    r"|(?:is|are)\s+thought\s+to"
    # "Previously, we have shown ..." / "We previously reported ..." — the
    # paper is pointing at its OWN earlier publication, which is still another
    # source, so the statement is secondary here.
    r"|previously,?\s+(?:we|our\s+group|the\s+authors|others)\s+"
    r"(?:have\s+|had\s+|has\s+)?(?:\w+ly\s+)?"
    r"(?:shown|showed|reported|demonstrated|described|found|observed|identified|established)"
    r"|(?:we|others)\s+(?:have\s+|had\s+)?previously\s+(?:\w+ly\s+)?"
    r"(?:shown|showed|reported|demonstrated|described|found|observed|identified|established)"
    r"|as\s+(?:previously|recently|originally|first)\s+"
    r"(?:shown|reported|described|demonstrated|noted|published|observed)"
    r"|as\s+reported\b(?!\s+here)"
    r"|(?:reviewed|summarized)\s+in\b"
    # "Martens et al. reported ..." / "Baker et al. (2006) showed ..."
    r"|et\s+al\.?\s*,?\s*(?:\(?\s*(?:19|20)\d{2}[a-z]?\s*\)?\s*)?"
    r"(?:have\s+|has\s+)?(?:\w+ly\s+)?"
    r"(?:report(?:ed|s)?|show(?:ed|n|s)?|demonstrated|found|observed|described"
    r"|noted|proposed|suggested|identified|established|concluded)"
    r"|et\s+al\.?\s*,?\s*\(?\s*(?:19|20)\d{2}"
    r"|in\s+(?:line|agreement|keeping)\s+with\s+(?:previous|earlier|prior|published)"
    r")\b",
    re.IGNORECASE,
)


def is_attributed_quote(text: str) -> bool:
    """True if the quote cites another source or uses reporting/background
    phrasing, i.e. it summarizes others' work rather than being an original
    result of the quoted paper. Such quotes cannot be labeled `primary`.

    Covers inline citation markers (``[12]``, ``(3,4)``, ``(Martens et al.,
    2012)``, trailing superscripts) and reporting phrasing ("previously, we have
    shown", "it has been shown", "studies have shown", "X et al. reported",
    "as reported", "reviewed in ...").

    Allele/isoform names (apoE4, E4/E4, ...) are masked first so their digits are
    not mistaken for citation markers."""
    if not text:
        return False
    masked = _mask_gene_symbols(_mask_isoforms(text))
    return bool(
        CITATION_MARKER_RE.search(masked)
        or AUTHOR_YEAR_RE.search(masked)
        or ATTRIBUTION_RE.search(masked)
    )

# ---------------------------------------------------------------------------
# Reference / bibliography sections are NOT quotable. A bibliography entry is
# the TITLE of some OTHER work sitting in this paper's back matter, not a
# statement this paper makes, so quoting it as evidence attributes another
# group's finding to the wrong paper (e.g. the title of Baker et al. 2006 quoted
# from a 2025 review's reference list). The section name is matched WHOLE — an
# anchored match, not a substring — so a legitimate Results/Methods section that
# merely mentions the word "reference" ("Reference genome alignment") is not
# excluded.
# ---------------------------------------------------------------------------
_REFERENCE_SECTION_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s*[.)]?\s*)?"                  # optional "6." / "6.1)"
    r"(?:(?:supplementary|supplemental|additional|online|uncited)\s+)?"
    r"(?:references?"
    r"|ref[-\s]?list"
    r"|bibliograph(?:y|ies)"
    r"|(?:works|literature|references?)\s+cited"
    r"|cited\s+(?:works|literature)"
    r"|reference\s+list"
    r"|references?\s+and\s+notes?"
    r"|notes?\s+and\s+references?"
    r")\s*[:.]?\s*$",
    re.IGNORECASE,
)


def is_reference_block(block: dict) -> bool:
    """True if the block belongs to a paper's reference/bibliography list.

    Such blocks are hard-excluded from the candidate set and rejected at
    adjudication: a reference-list entry is another work's title, never evidence
    for a claim."""
    return bool(_REFERENCE_SECTION_RE.match(str(block.get("section") or "").strip()))


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "can", "causes",
    "does", "for", "from", "has", "have", "in", "into", "is", "it", "its", "of",
    "on", "or", "that", "the", "their", "this", "to", "was", "were", "with",
}

TRAILING_FRAGMENT = re.compile(
    r"\b(the|a|an|and|or|of|to|by|with|in|for|from|via|than|that|which)\s*$", re.I
)


def block_id(paper_id: str, kind: str, value: Any) -> str:
    return f"{paper_id}:{kind}:{value}"


def build_blocks(parsed: dict) -> list[dict]:
    paper_id = str(parsed.get("paper_id") or "unknown")
    blocks = []
    for index, sent in enumerate(parsed.get("sentences", []) or []):
        text = str(sent.get("text") or "").strip()
        if not text:
            continue
        sid = sent.get("sentence_id", index)
        blocks.append({
            "block_id": block_id(paper_id, "S", sid),
            "paper_id": paper_id,
            "block_type": "sentence",
            "text": text,
            "page": sent.get("page"),
            "section": sent.get("section"),
            "bbox": sent.get("bbox"),
            "figure_id": None,
            "image_path": None,
            "ocr_conf": None,
        })
    for f_index, fig in enumerate(parsed.get("figures", []) or []):
        fid = str(fig.get("figure_id") or fig.get("label") or f"fig{f_index+1}")
        caption = str(fig.get("caption") or "").strip()
        if caption:
            blocks.append({
                "block_id": block_id(paper_id, "CAP", fid),
                "paper_id": paper_id,
                "block_type": "caption",
                "text": caption,
                "page": fig.get("page"),
                "section": "Figure caption",
                "bbox": fig.get("caption_bbox"),
                "figure_id": fid,
                "image_path": fig.get("image_path"),
                "ocr_conf": None,
            })
        for o_index, line in enumerate(fig.get("ocr", []) or []):
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            blocks.append({
                "block_id": block_id(paper_id, "OCR", f"{fid}:{o_index}"),
                "paper_id": paper_id,
                "block_type": "figure_ocr",
                "text": text,
                "page": fig.get("page"),
                "section": "Figure panel",
                "bbox": line.get("bbox"),
                "figure_id": fid,
                "image_path": fig.get("image_path"),
                "ocr_conf": line.get("conf"),
            })
    return blocks


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}|\d+(?:\.\d+)?")

DIRECTION_TERMS = {
    "activate", "activated", "cause", "causes", "caused", "decrease", "decreases",
    "decreased", "impair", "impairs", "impaired", "increase", "increases", "increased",
    "inhibit", "inhibits", "inhibited", "loss", "necessary", "protect", "protects",
    "protected", "reduce", "reduces", "reduced", "required", "sufficient",
}


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text) if t.lower() not in STOPWORDS]


def claim_terms(claim: dict) -> list[str]:
    terms = tokenize(claim["claim_text"])
    explicit = re.split(r"[;,|]", claim.get("query_terms") or "")
    for phrase in explicit:
        terms.extend(tokenize(phrase) * 2)
    return terms


def _reserved_figure_slots(
    ordered: list[tuple[float, dict]],
    selected: list[tuple[float, dict]],
    figure_quota: int,
) -> list[tuple[float, dict]]:
    """Return the reserved caption/figure_ocr candidates to append for one claim.

    ``ordered`` is every scoring block for the claim, already sorted by
    ``(-score, block_id)``; ``selected`` is what the general per-paper slots
    already took. Up to ``figure_quota`` of the highest-scoring figure blocks per
    paper are added, skipping blocks already selected, and only for papers that
    are already in the claim's candidate set. Output order follows ``ordered``,
    so the result is deterministic."""
    if figure_quota <= 0 or not selected:
        return []
    chosen_ids = {block["block_id"] for _, block in selected}
    in_play = {block["paper_id"] for _, block in selected}
    granted: Counter = Counter()
    reserved: list[tuple[float, dict]] = []
    for score, block in ordered:
        pid = block["paper_id"]
        if block["block_type"] not in FIGURE_BLOCK_TYPES or pid not in in_play:
            continue
        if block["block_id"] in chosen_ids or granted[pid] >= figure_quota:
            continue
        granted[pid] += 1
        chosen_ids.add(block["block_id"])
        reserved.append((score, block))
    return reserved


def rank_candidates(
    claims: list[dict],
    blocks: list[dict],
    *,
    top_per_paper: int,
    top_per_claim: int,
    figure_quota: int = 0,
) -> list[dict]:
    """Score every block against every claim and keep a small candidate set.

    Two independent allowances per paper and claim:
      * ``top_per_paper`` general slots, filled by score across all block types;
      * ``figure_quota`` RESERVED slots for ``caption``/``figure_ocr`` blocks,
        granted IN ADDITION to the general slots so figure evidence never has to
        outcompete body prose. Reserved figures are granted only to papers that
        already contributed a block to this claim, which keeps the addition
        bounded by the papers actually in play.

    Reference/bibliography blocks are dropped before scoring (see
    ``is_reference_block``). Selection is fully deterministic: every ordering is
    a stable sort on ``(-score, block_id)``.
    """
    blocks = [b for b in blocks if not is_reference_block(b)]
    if not blocks:
        return []
    tokenized = [tokenize(b["text"]) for b in blocks]
    doc_freq = Counter()
    for tokens in tokenized:
        doc_freq.update(set(tokens))
    avg_len = sum(len(t) for t in tokenized) / max(1, len(tokenized))
    n_docs = len(blocks)
    rows = []
    for claim in claims:
        query = claim_terms(claim)
        if not query:
            continue
        q_counts = Counter(query)
        claim_vocabulary = set(tokenize(claim["claim_text"]))
        claim_direction = claim_vocabulary & DIRECTION_TERMS
        scored = []
        explicit_phrases = [
            p.strip().lower()
            for p in re.split(r"[;,|]", claim.get("query_terms") or "")
            if p.strip()
        ]
        for block, tokens in zip(blocks, tokenized):
            counts = Counter(tokens)
            length = max(1, len(tokens))
            score = 0.0
            for term, q_weight in q_counts.items():
                tf = counts.get(term, 0)
                if not tf:
                    continue
                idf = math.log(1.0 + (n_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                denom = tf + 1.2 * (1.0 - 0.75 + 0.75 * length / max(1.0, avg_len))
                score += min(2, q_weight) * idf * (tf * 2.2 / denom)
            lowered = block["text"].lower()
            score += sum(2.5 for phrase in explicit_phrases if phrase in lowered)
            block_vocabulary = set(tokens)
            if claim_vocabulary:
                score += 5.0 * len(claim_vocabulary & block_vocabulary) / len(claim_vocabulary)
            if claim_direction:
                score += 3.0 * len(claim_direction & block_vocabulary) / len(claim_direction)
            section = str(block.get("section") or "").lower()
            if any(key in section for key in ("result", "conclusion", "finding")):
                score *= 1.25
            elif "method" in section:
                score *= 0.8
            elif "reference" in section:
                score *= 0.35
            if block["block_type"] == "caption":
                # A figure legend states the paper's headline result as densely
                # as a Results sentence; weight it the same, not lower.
                score *= 1.25
            elif block["block_type"] == "figure_ocr":
                # In-panel OCR text is short and label-like by nature, so it is
                # exempt from the truncation penalty below and carries no
                # penalty of its own.
                pass
            elif looks_truncated(block["text"]):
                score *= 0.45
            if score > 0:
                scored.append((score, block))

        ordered = sorted(scored, key=lambda item: (-item[0], item[1]["block_id"]))
        by_paper: dict[str, list[tuple[float, dict]]] = defaultdict(list)
        for score, block in ordered:
            if len(by_paper[block["paper_id"]]) < top_per_paper:
                by_paper[block["paper_id"]].append((score, block))
        selected = []
        paper_order = sorted(by_paper, key=lambda p: -by_paper[p][0][0])
        rank = 0
        while len(selected) < top_per_claim:
            added = False
            for paper_id in paper_order:
                if rank < len(by_paper[paper_id]) and len(selected) < top_per_claim:
                    selected.append(by_paper[paper_id][rank])
                    added = True
            if not added:
                break
            rank += 1
        selected.extend(_reserved_figure_slots(ordered, selected, figure_quota))
        for rank, (score, block) in enumerate(selected, 1):
            rows.append({
                "claim_id": claim["claim_id"],
                "paper_id": block["paper_id"],
                "block_id": block["block_id"],
                "retrieval_score": round(score, 6),
                "rank": rank,
            })
    return rows


def normalize_quote(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_match(quote: str, source: str) -> str | None:
    if quote and quote in source:
        return "exact"
    if quote and normalize_quote(quote) in normalize_quote(source):
        return "normalized"
    return None


def looks_truncated(text: str) -> bool:
    clean = normalize_quote(text)
    if len(clean) < 12:
        return True
    if TRAILING_FRAGMENT.search(clean):
        return True
    return clean.count("(") != clean.count(")") or clean.count("[") != clean.count("]")


# Trailing punctuation that legitimately ends a complete sentence, optionally
# followed by a closing bracket/paren/quote or a bracketed citation.
_SENTENCE_END = re.compile(r"[.!?][\"'\u201d\u2019)\]]*\s*(?:\[[\d,\s\u2013-]+\])?\s*$")
# A sentence-initial token: uppercase letter, digit, or an opening quote/paren.
_SENTENCE_START = re.compile(r"^[\"'\u201c\u2018(\[]?[A-Z0-9]")
# Biomedical style keeps some identifiers lowercase even sentence-initially
# (apoE4, mTOR, p53, p-tau, miR-132, shRNA, \u03b1-synuclein). Such a token mixes case,
# carries a digit, is Greek-lettered, or is a short hyphenated prefix \u2014 a plain
# lowercase English word ("sortilin", "resulting") matches none of those, so this
# does not re-open the mid-sentence-fragment hole.
_LOWERCASE_TECHNICAL_START = re.compile(
    r"^[\"'\u201c\u2018(\[]?"
    r"(?:[a-z]+[A-Z0-9]|[a-z]{1,4}[-\u2011][A-Za-z0-9]|[\u03b1-\u03c9])"
)
# Cross-column interleaving artifacts (a word broken by a column seam):
#   - "neurotransmit- The"  (lowercase + hyphen + space + word): a word split
#     across a line/column break that was NOT rejoined because the continuation
#     came from the other column. The char BEFORE the hyphen must be lowercase so
#     we do not flag legitimate spaced acronym notation such as "FTD- GRN" /
#     "FTD- TDP" (uppercase before the hyphen), which pdfplumber tokenizes with a
#     space but is a single real token.
_HYPHEN_SPACE = re.compile(r"[a-z]- [A-Za-z]")
# A lowercase word directly followed by a Capitalized word with no intervening
# punctuation is a strong interleaving seam ("groups symptomatic individuals, NfL
# was elevated even for patients" style splices produce "... pre- However ...").
# We look specifically for the "-<space>Uppercase" seam and the doubled-verb
# artifact ("has also has", "studies have also has reported").
_DOUBLED_VERB = re.compile(r"\b(has|have|was|were|is|are)\s+\w+\s+\1\b", re.I)


def _has_merged_words(text: str) -> bool:
    """Detect PDF word-spacing failures where inter-word spaces were dropped,
    e.g. "MicrogliosiswasassessedbyCD68immunoreactivity" or
    "improvesmicrogliosisin". Two independent signals:
      (1) a very long unbroken token (>= 22 letters/digits with a lowercase char);
      (2) a token with >= 2 internal lowercase->Uppercase transitions
          (camelCase-like run produced by merging Sentence-cased words), which
          catches merged runs even when each merged word is individually short.
    Short legitimate tokens (gene notation, accessions, hyphenated compounds) are
    below these thresholds and are not flagged.
    """
    for m in re.finditer(r"\S+", text or ""):
        core = m.group(0).strip(".,;:()[]{}\"'")
        alnum = [c for c in core if c.isalnum()]
        if len(alnum) >= 22 and any(c.islower() for c in core):
            return True
        if len(re.findall(r"[a-z][A-Z]", core)) >= 2:
            return True
    return False


# ---------------------------------------------------------------------------
# Heading / label detector. A whole parsed block that is not a complete sentence
# is only acceptable as a quote when it really is a heading or label ("Results",
# "2.1 Animals and Housing", "TABLE 1"). Length alone is NOT evidence of that: a
# 62-character mid-sentence fragment such as "Grn - / - mice develop severe
# lysosomal dysfunction, resulting" is short but plainly truncated.
# ---------------------------------------------------------------------------
_HEADING_MAX_CHARS = 80
_HEADING_MAX_WORDS = 12
# Dangling tails: a heading never ends on one of these, so a block that does is a
# truncated sentence no matter how short it is.
_FRAGMENT_TAIL_WORDS = frozenset({
    "a", "an", "and", "as", "at", "because", "before", "both", "but", "by", "during",
    "either", "for", "from", "however", "if", "in", "including", "into", "its", "it",
    "leading", "neither", "nor", "of", "on", "or", "over", "resulting", "showing",
    "since", "so", "such", "suggesting", "than", "that", "the", "their", "then",
    "these", "they", "this", "those", "through", "thus", "to", "under", "upon",
    "using", "versus", "via", "vs", "we", "were", "what", "when", "where", "whereas",
    "whether", "which", "while", "who", "whose", "with", "within", "without", "yielding",
})
# Finite / clearly verbal forms. A heading is a noun phrase; a span containing a
# conjugated verb is a sentence (or a piece of one), not a label.
_FINITE_VERBS = frozenset({
    "abolished", "are", "be", "been", "being", "can", "caused", "confirmed", "could",
    "demonstrated", "developed", "did", "do", "does", "exhibited", "found", "had",
    "has", "have", "impaired", "indicated", "induced", "is", "led", "may", "might",
    "must", "observed", "occurred", "required", "resulted", "revealed", "shall",
    "should", "showed", "shown", "suggested", "was", "were", "will", "would",
})
# Numbered / typographic labels that are headings even without title case.
_LABEL_PREFIX_RE = re.compile(
    r"^\(?(?:\d+(?:\.\d+)*|[ivxlc]+|[a-h])[).]\s|"
    r"^(?:table|fig(?:ure)?|panel|box|scheme|appendix|supplementary)\b",
    re.IGNORECASE,
)
_HEADING_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’/-]*")


def looks_like_heading(text: str) -> bool:
    """True if ``text`` reads as a section heading or label rather than prose.

    Used to decide whether a whole parsed block that fails the complete-sentence
    test may still be quoted. Requires ALL of: short, no dangling tail, no
    finite verb, no sentence-terminal punctuation; plus one positive signal —
    title case, all caps, or a numbered/typographic label prefix."""
    q = normalize_quote(text or "")
    if not q or len(q) > _HEADING_MAX_CHARS:
        return False
    if q[-1] in ",;–—-" or _SENTENCE_END.search(q):
        return False
    if TRAILING_FRAGMENT.search(q):
        return False
    words = _HEADING_WORD_RE.findall(q)
    if not words or len(words) > _HEADING_MAX_WORDS:
        return False
    if words[-1].lower() in _FRAGMENT_TAIL_WORDS:
        return False
    if any(word.lower() in _FINITE_VERBS for word in words):
        return False
    if _LABEL_PREFIX_RE.match(q):
        return True
    if q == q.upper() and any(c.isalpha() for c in q):
        return True
    capitalized = sum(1 for word in words if word[0].isupper())
    return capitalized / len(words) >= 0.6


def looks_column_garbled(text: str) -> bool:
    """Heuristic: does this text show extraction artifacts that make it not a
    clean, readable sentence?

    Catches (a) cross-column interleaving — a word broken by the column seam
    (``neurotransmit- The``) or a repeated verb (``studies have also has
    reported``); (b) word-spacing failures where the PDF text layer dropped
    inter-word spaces (``MicrogliosiswasassessedbyCD68immunoreactivity``); and
    (c) everything ``quote_integrity`` detects — a letter-shattered word, a
    fused pair, a corrupted comparison operator, a spliced trailing fragment.

    (c) exists because four distinct kinds of damage shipped past (a) and (b),
    inside text presented between quotation marks: ``n e u ro d e ge n e
    ration``, ``Asexpected``, ``(P 5 0.001)`` for ``(P < 0.001)``, and a
    sentence whose clauses had been interleaved from the neighbouring column.
    Each slipped between the hyphen-seam test, the doubled-verb test, the
    22-character token test and the camelCase test.

    Such text is never a legitimate quote, even as a whole parsed block.
    """
    t = text or ""
    return (bool(_HYPHEN_SPACE.search(t)) or bool(_DOUBLED_VERB.search(t))
            or _has_merged_words(t) or bool(quote_problems(t)))


def is_incomplete_sentence_quote(quote: str, block_type: str,
                                 source_text: str | None = None) -> bool:
    """True if a body-*sentence* quote is not one-or-more COMPLETE, CLEAN sentences.

    Enforces the "always quote full sentences" rule: a quote drawn from a
    ``sentence`` block must begin at a sentence boundary and end at sentence-
    terminal punctuation — unless it spans the entire block (some parsed blocks
    are headings/list items with no terminal period, which are acceptable whole).
    In addition, a quote showing cross-column interleaving artifacts (a word
    broken by a column seam, or a doubled verb) is rejected even if it is a whole
    block, because such text is garbled rather than a real sentence. Caption /
    figure_ocr / table blocks are exempt (figure legends and in-figure text are
    legitimately not full prose sentences).
    """
    if block_type not in {"sentence", None, ""}:
        return False  # captions / OCR / tables may be partial
    q = (quote or "").strip()
    if not q:
        return True
    # Garbled text (cross-column interleave or merged words) is never acceptable,
    # whole block or not.
    if looks_column_garbled(q):
        return True
    ok_start = bool(_SENTENCE_START.match(q))
    ok_end = bool(_SENTENCE_END.search(q))
    if ok_start and ok_end:
        return False
    whole_block = source_text is not None and q == source_text.strip()
    # A whole parsed block that ends at sentence-terminal punctuation but opens
    # with a lowercase TECHNICAL identifier ("apoE4 lowers age of onset ...",
    # "mTOR signalling is elevated.") is a complete sentence whose first token is
    # lowercase by house style, not a mid-sentence fragment.
    if whole_block and ok_end and _LOWERCASE_TECHNICAL_START.match(q):
        return False
    # Whole-block quote is exempt ONLY when it genuinely READS as a heading or
    # label (see `looks_like_heading`). Length alone is not a heading test: a
    # short whole block can still be a mid-sentence fragment, e.g. the 62-char
    # "Grn - / - mice develop severe lysosomal dysfunction, resulting". A whole
    # block that fails the start/end sentence checks and is not heading-like is a
    # broken-sentence fragment (the parser split mid-sentence) and must be
    # flagged, not waved through.
    if whole_block and looks_like_heading(q):
        return False
    return True


# A bare figure number that bled into the front of caption text because the
# label glyph and the legend body were merged without a separator, e.g.
# "3Latozinemab decreases sortilin levels ...". Requires NO space and an
# immediately following Capitalized word, so real numbering ("3 Latozinemab ...")
# and in-text numerals are left alone.
_LEADING_FIGURE_NUMBER_RE = re.compile(r"^\s*\d{1,2}(?=[A-Z][a-z])")


def strip_leading_figure_label(quote: str) -> str:
    """Drop a bled-in leading figure number from a caption quote.

    ``"3Latozinemab decreases sortilin levels."`` ->
    ``"Latozinemab decreases sortilin levels."``. Returns the quote unchanged
    when there is no such bleed."""
    return _LEADING_FIGURE_NUMBER_RE.sub("", quote or "").strip()


def make_locator(block: dict) -> str:
    parts = []
    if block.get("page") is not None:
        parts.append(f"page {block['page']}")
    if block.get("section"):
        parts.append(str(block["section"]))
    if block.get("figure_id"):
        parts.append(f"figure {block['figure_id']}")
    return " · ".join(parts) or "section unavailable"


def validate_adjudication(
    row: dict,
    *,
    claims_by_id: dict[str, dict],
    blocks_by_id: dict[str, dict],
    papers_by_id: dict[str, dict],
    backend: str,
    model: str,
    request_meta: dict | None = None,
) -> tuple[dict | None, str | None]:
    claim_id = str(row.get("claim_id") or "")
    bid = str(row.get("block_id") or "")
    quote = str(row.get("quote") or "").strip()
    stance = str(row.get("stance") or "")
    kind = str(row.get("evidence_kind") or "")
    if claim_id not in claims_by_id:
        return None, f"unknown claim_id {claim_id!r}"
    block = blocks_by_id.get(bid)
    if block is None:
        return None, f"unknown block_id {bid!r}"
    if stance not in STANCE_VALUES:
        return None, f"invalid stance {stance!r}"
    if kind not in EVIDENCE_KINDS:
        return None, f"invalid evidence_kind {kind!r}"
    # A reference-list entry is the TITLE of some OTHER work sitting in this
    # paper's back matter, not a statement this paper makes. Quoting one credits
    # the wrong paper with the finding, so reject outright. Reference blocks are
    # already excluded from the candidate set; this catches imported
    # adjudications and any block that slips through.
    if is_reference_block(block):
        return None, (
            "quote comes from the paper's reference/bibliography list — a "
            "bibliography entry is another work's title, not a statement this "
            "paper makes, so it is never evidence; cite the referenced work "
            "directly instead"
        )
    # A figure number can bleed into the front of caption text when the label
    # glyph and the legend body are merged ("3Latozinemab decreases sortilin
    # levels ..."). Strip it, then require the remainder to still read as clean
    # caption text.
    if block["block_type"] in {"caption", "figure_ocr"}:
        cleaned = strip_leading_figure_label(quote)
        if cleaned != quote:
            if len(cleaned) < 12 or not _SENTENCE_START.match(cleaned):
                return None, (
                    "caption quote begins with a bled-in figure number and what "
                    "remains after stripping it is not clean caption text — "
                    "re-quote the caption without the figure label"
                )
            quote = cleaned
    # In-panel OCR text is exempt from the full-sentence and truncation rules
    # because axis labels and legends are legitimately short — but "5" is not
    # evidence of anything. A figure_ocr quote must carry enough characters to
    # be re-findable in the panel and at least one letter, so a bare tick label
    # or stray digit cannot be accepted as `primary` support.
    if block["block_type"] == "figure_ocr":
        stripped = normalize_quote(quote)
        if len(stripped) < MIN_FIGURE_OCR_QUOTE_CHARS or not any(
            c.isalpha() for c in stripped
        ):
            return None, (
                "figure OCR quote is too short to be evidence — it must be at "
                f"least {MIN_FIGURE_OCR_QUOTE_CHARS} characters and contain at "
                "least one letter (a bare number or tick label is not a finding)"
            )
    # A conditional or speculative sentence asserts nothing, so it cannot support
    # or contradict anything and there is no weaker label that would make it
    # evidence. A shipped report recorded "By contrast, if suppression of ApoE4 in
    # astrocytes rescues the BBB defect, a gain-of-function mechanism would be
    # supported." as supports/primary for a gain-of-function claim.
    if stance in {"supports", "contradicts"} and is_hypothetical(quote):
        return None, (
            "quote is conditional or speculative — it describes what a result "
            "would show, not what was found, so it cannot support or contradict "
            "a claim; quote the sentence reporting the outcome instead"
        )
    # Consistency guard: a quote that cites another source or uses
    # reporting/background phrasing cannot be `primary` (it summarizes others'
    # work, not an original result of THIS paper). RELABEL it `secondary` rather
    # than dropping the row: the observation is still real evidence, only of a
    # weaker kind, and `support_state` recomputes from the corrected label.
    relabeled_from = None
    downgrade_reason = ""
    if kind == "primary" and is_attributed_quote(quote):
        relabeled_from, kind = kind, "secondary"
        downgrade_reason = "quote cites another source or uses reporting phrasing"
    # Second guard, on the same label: `primary` means an original result of THIS
    # paper, so the quote must either present a result or come from a section that
    # reports them. Without this, the opening line of a mouse-tauopathy paper
    # ("APOE4 is the strongest known genetic risk factor for late-onset AD") was
    # counted as primary human-genetics evidence and helped a claim reach
    # "Convergent (>=2 independent primary studies)". Extraction had dropped its
    # superscript reference, so the attribution guard above saw nothing.
    if kind == "primary" and not may_be_primary(
            quote, str(block.get("section") or ""), block["block_type"]):
        relabeled_from, kind = "primary", "secondary"
        downgrade_reason = primary_downgrade_reason(
            quote, str(block.get("section") or ""), block["block_type"])
    match = quote_match(quote, block["text"])
    if not match:
        return None, "quote is not a substring of the cited block"
    if block["block_type"] in {"sentence", "caption"} and looks_truncated(quote):
        return None, "quote appears truncated or structurally incomplete"
    if is_incomplete_sentence_quote(quote, block["block_type"], block.get("text")):
        return None, (
            "quote from a body-sentence block is not a complete sentence — it must "
            "begin at a sentence boundary and end at sentence-terminal punctuation "
            "(quote the full sentence(s), not a sub-span)"
        )
    # Root-cause guard for merged/garbled caption or in-figure text: some journals
    # encode caption text with no inter-word spaces (e.g. "improvesmicrogliosisin
    # Grnmice") or with cross-column seams. That text is unreadable as a verbatim
    # quote, so it must NOT be accepted as a caption/figure evidence anchor even
    # though captions are exempt from the full-sentence rule. The figure image
    # itself is still exported (via any clean caption / figure_ocr / sentence
    # anchor), so suppressing the garbled *text* loses no real evidence.
    if block["block_type"] in {"caption", "figure_ocr"} and looks_column_garbled(quote):
        return None, (
            "caption/figure quote text is merged or garbled (the PDF text layer "
            "dropped inter-word spaces or interleaved columns) — do not quote it "
            "verbatim; cite a clean sentence or clean caption for this figure "
            "instead (the figure image is still exported from the other anchor)"
        )
    if bool(row.get("needs_figure_review", False)) and block["block_type"] != "figure_ocr":
        return None, "panel-level claim requires evidence from a resolved figure region"
    paper = papers_by_id.get(block["paper_id"], {})
    # `evidence_kind` is part of the identity. Without it, two rows differing
    # only in kind collapsed on dedup to whichever came last, so a `secondary`
    # row could silently replace the `primary` row for the same quote and drop
    # the claim a whole support tier.
    digest = hashlib.sha1(
        "|".join((claim_id, block["paper_id"], bid, quote, stance, kind)).encode()
    ).hexdigest()[:16]
    evidence = {
        "evidence_id": f"E-{digest}",
        "claim_id": claim_id,
        "paper_id": block["paper_id"],
        # Independence keys carried through from the corpus record: convergence
        # counts distinct studies, not distinct papers, and will not count two
        # papers from one cohort as two replications. Empty when the corpus
        # predates these fields (then `study_key` falls back to paper_id).
        "study_id": paper.get("study_id") or "",
        "cohort_id": paper.get("cohort_id") or "",
        "publication_role": paper.get("publication_role") or "",
        "block_id": bid,
        "quote": quote,
        "quote_match": match,
        "stance": stance,
        "evidence_kind": kind,
        # Set when a guard downgraded the model's label (primary -> secondary);
        # empty otherwise. Auditable trail for the relabel: the tier a claim ends
        # up with depends on these, so the reason has to be recoverable.
        "evidence_kind_relabeled_from": relabeled_from or "",
        "evidence_kind_relabel_reason": downgrade_reason,
        "scope_note": str(row.get("scope_note") or "").strip(),
        "rationale": str(row.get("rationale") or "").strip(),
        "needs_figure_review": bool(row.get("needs_figure_review", False)),
        "block_type": block["block_type"],
        "page": block.get("page"),
        "section": block.get("section"),
        "figure_id": block.get("figure_id"),
        "bbox": block.get("bbox"),
        "image_path": block.get("image_path"),
        "ocr_conf": block.get("ocr_conf"),
        "source_text": block["text"],
        "source_locator": make_locator(block),
        "doi": paper.get("doi"),
        "pmid": paper.get("pmid"),
        "pmcid": paper.get("pmcid"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "url": paper.get("oa_full_url") or paper.get("landing_url"),
        "access": paper.get("access") or "unknown",
        "adjudication_backend": backend,
        "adjudication_model": model,
        "request_id": (request_meta or {}).get("request_id"),
        "verified": True,
        "verified_at": utc_now(),
    }
    if evidence["block_type"] == "figure_ocr" and (
        not evidence.get("figure_id") or not evidence.get("image_path") or not evidence.get("bbox")
    ):
        return None, "figure OCR evidence lacks a resolvable figure region"
    return evidence, None


# ---------------------------------------------------------------------------
# Independence: convergence counts STUDIES, not papers.
#
# Counting distinct `paper_id` treated as independent replication: a preprint
# and its retitled journal version, an interim and a final report of one trial,
# a conference abstract and its full paper, and any number of papers analysing
# one cohort or consortium. `references_to_corpus` stamps `study_id`,
# `cohort_id`, and `publication_role` on every corpus record; those travel onto
# the evidence rows so this module can count the underlying study.
# ---------------------------------------------------------------------------

def study_key(row: dict) -> str:
    """Independence key for one evidence row: the STUDY it reports.

    Falls back to `paper_id` when the corpus carries no `study_id`, so a run
    against a corpus that predates the field counts exactly as it did before."""
    return str(row.get("study_id") or row.get("paper_id") or "").strip()


def cohort_key(row: dict) -> str:
    """Cohort / consortium identifier for one evidence row, `""` when unknown.

    Two papers from the GENFI cohort are two analyses of one set of
    participants, not two independent replications."""
    return str(row.get("cohort_id") or "").strip()


def _convergence_basis(
    supports: list[dict],
    primary: list[dict],
    n_papers: int,
    n_studies: int,
    n_cohorts: int,
    shared_cohort: str,
) -> str:
    """Human-auditable one-liner naming what the convergence test counted."""
    if not supports:
        return "no qualifying supporting evidence"
    if not primary:
        n_supporting_papers = len({str(r.get("paper_id") or "") for r in supports})
        return (
            f"no primary support ({n_supporting_papers} supporting "
            f"paper{'' if n_supporting_papers == 1 else 's'}, non-primary only)"
        )
    parts = [
        f"{n_studies} primary stud{'y' if n_studies == 1 else 'ies'} "
        f"across {n_papers} paper{'' if n_papers == 1 else 's'}",
        f"{n_cohorts} distinct cohort id{'' if n_cohorts == 1 else 's'}"
        if n_cohorts else "no cohort ids recorded",
    ]
    if shared_cohort and n_studies >= 2:
        parts.append(f"all from cohort {shared_cohort} — not independent replication")
    return "; ".join(parts)


def support_basis(rows: list[dict]) -> dict:
    """Support tier for one claim, plus the counting basis behind it.

    Tiers:
      * ``C_CONFLICTED``   — qualifying support AND qualifying contradiction.
      * ``C_REFUTED``      — contradiction with no support, and at least one
        contradicting row is `primary`. Refutation is a strong verdict ("we know
        it is false"), so a lone secondary/indirect counter-statement is NOT
        enough: with no support at all, that is ``C_INSUFFICIENT`` ("we do not
        know"). A shipped report once scored a claim REFUTED off one secondary
        row while the paper that supported it — whose title WAS the claim — had
        simply never been retrieved.
      * ``C1_INDIRECT``    — support, but none of it primary.
      * ``C2_CONVERGENT``  — primary support from >= 2 distinct STUDIES that do
        not all come from one cohort. Distinct papers are not enough (see
        ``study_key``), and neither is one cohort reported twice.
      * ``C1_SINGLE_DIRECT`` — primary support that does not clear that bar.

    Returns the state alongside ``n_primary_papers`` / ``n_primary_studies`` /
    ``n_primary_cohorts`` / ``convergence_basis`` so a reader can audit which
    number moved the claim.
    """
    supports = [
        r for r in rows
        if r["stance"] == "supports" and r["evidence_kind"] != "inferred"
    ]
    contradicts = [
        r for r in rows
        if r["stance"] == "contradicts" and r["evidence_kind"] != "inferred"
    ]
    primary = [r for r in supports if r["evidence_kind"] == "primary"]
    primary_papers = {str(r.get("paper_id") or "") for r in primary}
    primary_studies = {study_key(r) for r in primary}
    cohorts = [cohort_key(r) for r in primary]
    primary_cohorts = {c for c in cohorts if c}
    # A shared cohort only defeats convergence when EVERY primary row names the
    # same cohort. A study with no cohort recorded is not assumed to belong to
    # anyone else's cohort.
    shared_cohort = (
        sorted(primary_cohorts)[0]
        if len(primary_cohorts) == 1 and all(cohorts) else ""
    )

    if supports and contradicts:
        state = support_policy.C_CONFLICTED
    elif contradicts:
        state = (
            support_policy.C_REFUTED
            if any(r["evidence_kind"] == "primary" for r in contradicts)
            else support_policy.C_INSUFFICIENT
        )
    elif not supports:
        state = support_policy.C_INSUFFICIENT
    elif not primary:
        state = support_policy.C1_INDIRECT
    elif len(primary_studies) >= 2 and not shared_cohort:
        state = support_policy.C2_CONVERGENT
    else:
        state = support_policy.C1_SINGLE_DIRECT

    return {
        "support_state": state,
        "n_primary_papers": len(primary_papers),
        "n_primary_studies": len(primary_studies),
        "n_primary_cohorts": len(primary_cohorts),
        "convergence_basis": _convergence_basis(
            supports, primary, len(primary_papers), len(primary_studies),
            len(primary_cohorts), shared_cohort,
        ),
    }


def support_state(rows: list[dict]) -> str:
    """The support tier only — see ``support_basis`` for the counting basis."""
    return support_basis(rows)["support_state"]


# ---------------------------------------------------------------------------
# Contradiction-search coverage. "No contradicting evidence" and "nobody ever
# looked for contradicting evidence" produce identical matrices, and the second
# is not a finding. These two fields make the difference legible per claim.
# ---------------------------------------------------------------------------

def candidate_seeks_contradiction(row: dict) -> bool:
    """True if a candidate row was retrieved/scored to find OPPOSING evidence.

    Plain relevance ranking (`rank_candidates`) scores a block for how well it
    matches the claim as stated — it is a support probe, so its rows answer
    False. A contradiction-seeking retrieval marks its rows (`scored_for`) and
    those count."""
    return (
        str(row.get("scored_for") or "").strip().lower() in {"contradiction", "both"}
        or bool(row.get("contradiction_probe"))
    )


def contradiction_coverage(
    claims: list[dict],
    candidates: list[dict] | None = None,
    adjudicated_blocks: dict[str, set[str]] | None = None,
) -> dict[str, dict]:
    """Per-claim record of whether opposing evidence was actually looked for.

    A candidate block counts as examined when either
      * it came from a contradiction-seeking retrieval
        (``candidate_seeks_contradiction``), or
      * it was submitted to a stance adjudication, which labels `contradicts`
        as well as `supports` (``adjudicated_blocks``, keyed by claim_id).

    A run that only ranked candidates (``--backend none``) therefore reports
    ``contradiction_searched: false`` for every claim, which is the honest
    answer: nothing examined those blocks for opposing evidence."""
    examined: dict[str, set[str]] = defaultdict(set)
    for row in candidates or []:
        if candidate_seeks_contradiction(row):
            examined[str(row.get("claim_id") or "")].add(str(row.get("block_id") or ""))
    for cid, block_ids in (adjudicated_blocks or {}).items():
        examined[str(cid)].update(block_ids)
    return {
        claim["claim_id"]: {
            "contradiction_searched": bool(examined.get(claim["claim_id"])),
            "contradiction_candidates_examined": len(examined.get(claim["claim_id"], ())),
        }
        for claim in claims
    }


def build_matrix(
    claims: list[dict],
    evidence: list[dict],
    coverage: dict[str, dict] | None = None,
) -> list[dict]:
    by_claim: dict[str, list[dict]] = defaultdict(list)
    for row in evidence:
        by_claim[row["claim_id"]].append(row)
    matrix = []
    for claim in claims:
        rows = by_claim.get(claim["claim_id"], [])
        supports = [
            r for r in rows if r["stance"] == "supports" and r["evidence_kind"] != "inferred"
        ]
        mentions = [
            r for r in rows if r["stance"] == "mentions" or r["evidence_kind"] == "inferred"
        ]
        contra = [
            r for r in rows if r["stance"] == "contradicts" and r["evidence_kind"] != "inferred"
        ]
        basis = support_basis(rows)
        state = basis["support_state"]
        searched = (coverage or {}).get(claim["claim_id"], {})
        cited = supports + contra
        matrix.append({
            "claim_id": claim["claim_id"],
            "claim_text": claim["claim_text"],
            "claim_scope": claim.get("scope", ""),
            "cluster": claim.get("cluster", ""),
            "support_state": state,
            "supporting_evidence_ids": ";".join(r["evidence_id"] for r in supports),
            "supporting_evidence_kinds": ";".join(r["evidence_kind"] for r in supports),
            "mentioning_evidence_ids": ";".join(r["evidence_id"] for r in mentions),
            "contradicting_evidence_ids": ";".join(r["evidence_id"] for r in contra),
            "supporting_study_ids": ";".join(dict.fromkeys(study_key(r) for r in supports)),
            "supporting_paper_ids": ";".join(dict.fromkeys(r["paper_id"] for r in supports)),
            # Convergence audit trail: which count moved (or did not move) this
            # claim to C2_CONVERGENT.
            "n_primary_papers": basis["n_primary_papers"],
            "n_primary_studies": basis["n_primary_studies"],
            "n_primary_cohorts": basis["n_primary_cohorts"],
            "convergence_basis": basis["convergence_basis"],
            # Did anything actually look for opposing evidence, and over how
            # many candidate blocks? `false` means "never looked", which is not
            # the same as "found none".
            "contradiction_searched": bool(searched.get("contradiction_searched", False)),
            "contradiction_candidates_examined": int(
                searched.get("contradiction_candidates_examined", 0)
            ),
            "source_locators": ";".join(
                f"{r['paper_id']} {r['source_locator']}" for r in cited
            ),
            "evidence_source": "full-text" if cited else "none",
            "strongest_alternative_explanation": "",
            "prohibited_stronger_wording": (
                "categorical or causal wording" if state in support_policy.WEAK_STATES else ""
            ),
            "intended_section": "Findings" if cited else "Evidence gaps",
            "citation_keys": ";".join(dict.fromkeys(str(r.get("doi") or r["paper_id"]) for r in cited)),
            "audit_status": "verified" if cited else "gap",
        })
    return matrix


def render_review(
    run_root: pathlib.Path,
    manifest: dict,
    claims: list[dict],
    matrix: list[dict],
    evidence: list[dict],
    papers: list[dict],
) -> None:
    by_claim = defaultdict(list)
    for row in evidence:
        by_claim[row["claim_id"]].append(row)
    supported = support_policy.count_grounded(
        row["support_state"] for row in matrix)
    title = manifest.get("title") or "Grounded literature review"
    paper_word = "paper" if len(papers) == 1 else "papers"
    lines = [
        f"# {title}", "",
        f"**Question:** {manifest.get('question') or 'Not specified'}", "",
        f"**Mode:** {manifest['mode']} · **Full text:** {len(papers)} {paper_word} · "
        f"**Grounded claims:** {supported} of {len(claims)}", "",
        "Every quoted anchor below was checked against the canonical parsed source block. "
        "Claims without qualifying full-text evidence remain explicit gaps.", "",
        "## Findings", "",
    ]
    matrix_by_id = {row["claim_id"]: row for row in matrix}
    for claim in claims:
        cid = claim["claim_id"]
        state = matrix_by_id[cid]["support_state"]
        lines.extend([f"### {cid}: {claim['claim_text']}", "", f"**Support state:** `{state}`", ""])
        rows = sorted(by_claim.get(cid, []), key=lambda r: (r["stance"], r["paper_id"], r["evidence_id"]))
        if not rows:
            lines.extend(["No qualifying full-text anchor was found.", ""])
            continue
        for row in rows:
            label = f"{row.get('title') or row['paper_id']} ({row.get('year') or 'n.d.'})"
            _row_url = row.get("url") or _doi_url(row.get("doi") or row.get("paper_id"))
            if _row_url:
                label = f"[{label}]({_row_url})"
            display_stance = "mentions" if row["evidence_kind"] == "inferred" else row["stance"]
            lines.extend([
                f"- **{display_stance} · {row['evidence_kind']}** — “{row['quote']}”",
                f"  — {label} · {row['source_locator']} · `{row['quote_match']}`",
            ])
            if row.get("scope_note"):
                lines.append(f"  Scope: {row['scope_note']}")
        lines.append("")
    # Only genuinely ungrounded claims are gaps. A refuted claim is not a
    # gap — it is a resolved negative, and it renders in Results with the
    # contradicting quotes that establish it.
    gaps = [row for row in matrix
            if not support_policy.is_grounded(row["support_state"])]
    lines.extend(["## Evidence gaps", ""])
    for row in gaps:
        lines.append(f"- **{row['claim_id']} · {row['support_state']}** — {row['claim_text']}")
    if not gaps:
        lines.append("- None under the contracted support rules.")
    lines.extend(["", "## Methods", "", (
        "The run searched or received a paper corpus, selected full-text papers, parsed body sentences "
        "and captions into stable blocks, retrieved a small candidate set per claim, adjudicated those "
        "passages semantically, and rejected any quote or locator that did not resolve deterministically. "
        f"Figure OCR was `{manifest['config']['ocr']}`."
    ), "", "## References", ""])
    for paper in sorted(papers, key=lambda p: (str(p.get("year") or ""), p.get("title") or p["paper_id"])):
        authors = paper.get("authors")
        if isinstance(authors, list):
            authors = ", ".join(str(author) for author in authors)
        prefix = f"{authors} " if authors else ""
        label = f"{prefix}({paper.get('year') or 'n.d.'}). {paper.get('title') or paper['paper_id']}"
        if paper.get("journal"):
            label += f". {paper['journal']}"
        if paper.get("doi"):
            label += f". doi:{paper['doi']}"
        url = (paper.get("oa_full_url") or paper.get("landing_url")
               or paper.get("url") or _doi_url(paper.get("doi") or paper.get("paper_id")))
        lines.append(f"- [{label}]({url})" if url else f"- {label}")
    out = run_root / "deliverables" / "review.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _vendor_modules():
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    return {
        name: importlib.import_module(name)
        for name in ("search", "acquire", "parse_pdf", "parse_jats")
    }


def _cpu_budget() -> int:
    """Usable CPU count, respecting a cgroup quota when present (containers often
    report many host cores via os.cpu_count() but are quota-limited)."""
    n = os.cpu_count() or 1
    # cgroup v2: /sys/fs/cgroup/cpu.max = "<quota> <period>" or "max <period>"
    try:
        raw = pathlib.Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if raw and raw[0] != "max":
            quota = math.ceil(float(raw[0]) / float(raw[1]))
            if quota >= 1:
                n = min(n, quota)
    except Exception:
        # cgroup v1 fallback
        try:
            q = int(pathlib.Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
            p = int(pathlib.Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
            if q > 0 and p > 0:
                n = min(n, max(1, math.ceil(q / p)))
        except Exception:
            pass
    return max(1, n)


def _available_ram_gb() -> float | None:
    """Best-effort available RAM in GB (MemAvailable), or None if unknown."""
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024.0 * 1024.0)  # kB -> GB
    except Exception:
        return None
    return None


def _resolve_parse_jobs(requested: int, n_records: int) -> int:
    """Resolve the effective number of parallel parse workers.

    requested: 0 = auto, 1 = force serial, N = requested ceiling.
    Bounded by: number of records, a leave-one-core CPU budget, and an
    available-RAM ceiling (each parse worker peaks ~0.6 GB incl. Python
    baseline). Auto default is min(4, cpu_budget) — conservative because the
    common minimal worker is small and parsing is CPU-bound (>cpu_budget wins
    nothing)."""
    if n_records <= 1 or requested == 1:
        return 1
    cpu = _cpu_budget()
    cpu_workers = max(1, cpu - 1)  # leave a core for the parent
    ram = _available_ram_gb()
    mem_cap = max(1, int(ram / 0.6)) if ram else 10 ** 9
    if requested and requested > 1:
        ceiling = requested
    else:  # auto
        ceiling = min(4, cpu_workers)
    return max(1, min(ceiling, cpu_workers, mem_cap, n_records))


def _acquire_and_parse_one(original: dict, dirs: dict, opts: dict) -> dict:
    """Acquire + parse ONE paper. Pure worker: re-imports vendor modules, does
    NOT touch the shared manifest, print, or write the parsed snapshot (the
    parent process owns all snapshot/manifest writes so writes stay single-writer
    and results stay deterministically ordered).

    Returns a plain, picklable dict describing the outcome; the parsed payload is
    JSON-serialisable (the same structure already written to snapshots)."""
    vendor = _vendor_modules()
    pdfs_dir = pathlib.Path(dirs["pdfs_dir"])
    figures_dir = pathlib.Path(dirs["figures_dir"])
    parsed_dir = pathlib.Path(dirs["parsed_dir"])
    cache_dir = pathlib.Path(dirs["cache_dir"])
    record = dict(original)
    pid = record["paper_id"]
    snapshot = parsed_dir / f"{safe_id(pid)}.json"
    try:
        _acq_t0 = time.time()
        record = vendor["acquire"].acquire_pdf(
            record, pdfs_dir,
            refresh=opts.get("refresh_acquisition", False),
            fast_fail_closed=opts.get("fast_fail_closed", False),
        )
        _acq_secs = round(time.time() - _acq_t0, 2)
        if not record.get("local_pdf") and not record.get("local_xml"):
            return {
                "paper_id": pid, "status": "not_retrieved", "record": record,
                "parsed": None, "acquire_secs": _acq_secs,
                "reason": record.get("_not_retrieved_reason"),
                "from_cache": record.get("_from_cache"),
            }
        recovery_attempts: list[dict] = []
        if snapshot.exists():
            parsed = json.loads(snapshot.read_text())
            recovery_attempts.append({"route": "cached_parse", "status": "loaded"})
        elif record.get("local_xml"):
            # figures_pdf is the supplementary crop-only PDF fetched when the
            # text came from JATS XML (which carries captions but no images).
            # Passing it here is what actually produces the crops; without it
            # the whole figures-PDF path is fetched, cached, and then ignored.
            parsed = vendor["parse_jats"].parse_jats_xml(
                record["local_xml"], pid,
                figures_pdf=record.get("figures_pdf"),
                figures_dir=figures_dir,
            )
            recovery_attempts.append({
                "route": "jats_xml", "status": "parsed",
                "sentence_count": len(parsed.get("sentences", [])),
            })
        else:
            parsed = vendor["parse_pdf"].parse_pdf(
                record["local_pdf"], pid,
                figures_dir=figures_dir, quality="default",
                cache_dir=cache_dir / "parsed",
            )
            recovery_attempts.append({
                "route": "pdf_default", "status": "parsed",
                "sentence_count": len(parsed.get("sentences", [])),
            })

        quality = parse_quality.assess(
            parsed, min_sentences=opts.get("min_sentences", 0),
            recovery_attempts=recovery_attempts,
        )
        recovery_pdf = record.get("local_pdf") or record.get("figures_pdf")
        if quality["state"] != parse_quality.USABLE and recovery_pdf:
            try:
                recovered = vendor["parse_pdf"].parse_pdf(
                    recovery_pdf, pid,
                    figures_dir=figures_dir, quality="default",
                    cache_dir=cache_dir / "parsed",
                )
                recovery_attempts.append({
                    "route": "pdf_text_recovery", "status": "parsed",
                    "sentence_count": len(recovered.get("sentences", [])),
                })
                parsed = parse_quality.prefer(parsed, recovered)
            except Exception as exc:  # recorded recovery failure; primary parse survives
                recovery_attempts.append({
                    "route": "pdf_text_recovery", "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
            quality = parse_quality.assess(
                parsed, min_sentences=opts.get("min_sentences", 0),
                recovery_attempts=recovery_attempts,
            )

        if (
            opts.get("marker_fallback")
            and quality["state"] != parse_quality.USABLE
            and recovery_pdf
        ):
            try:
                recovered = vendor["parse_pdf"].parse_pdf(
                    recovery_pdf, pid,
                    figures_dir=figures_dir, quality="high",
                    cache_dir=cache_dir / "parsed",
                )
                recovery_attempts.append({
                    "route": "pdf_high_quality", "status": "parsed",
                    "sentence_count": len(recovered.get("sentences", [])),
                })
                parsed = parse_quality.prefer(parsed, recovered)
            except (ImportError, ModuleNotFoundError) as exc:
                recovery_attempts.append({
                    "route": "pdf_high_quality", "status": "unavailable",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
            except Exception as exc:  # primary parse remains a valid fallback
                recovery_attempts.append({
                    "route": "pdf_high_quality", "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
            quality = parse_quality.assess(
                parsed, min_sentences=opts.get("min_sentences", 0),
                recovery_attempts=recovery_attempts,
            )
        parsed.pop("__from_cache", None)
        parsed["parse_quality"] = quality
        return {
            "paper_id": pid, "status": "parsed", "record": record,
            "parsed": parsed, "acquire_secs": _acq_secs,
            "parse_quality": quality,
            "from_cache": record.get("_from_cache"),
            "user_supplied": bool(original.get("local_pdf") or original.get("local_xml")),
        }
    except Exception as exc:  # preserve per-paper failure, mirror serial path
        record["_not_retrieved_reason"] = f"pipeline_error:{type(exc).__name__}:{exc}"
        return {
            "paper_id": pid, "status": "failed", "record": record,
            "parsed": None, "reason": str(exc),
        }


def _new_manifest(args, config: dict) -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": args.run_id or pathlib.Path(args.run_root).name,
        "title": args.title,
        "question": args.question,
        "mode": args.review_mode,
        "status": "running",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "config": config,
        "papers": {},
        "metrics": {"model_requests": 0, "cache_hits": 0},
        "errors": [],
    }


def _load_manifest(path: pathlib.Path, args, config: dict) -> dict:
    """Resume from an existing manifest, or start a fresh one.

    A manifest written by an older schema (or a truncated/corrupt file) is
    REJECTED and regenerated rather than resumed: a `schema_version: 1`
    manifest has no `papers` key, and the run used to die on
    `KeyError: 'papers'` several minutes in. The `setdefault`s cover a manifest
    of the right version whose optional sections were dropped."""
    if path.exists():
        try:
            manifest = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            manifest = None
        if isinstance(manifest, dict) and manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION:
            manifest.setdefault("config", {})
            manifest.setdefault("papers", {})
            manifest.setdefault("errors", [])
            metrics = manifest.setdefault("metrics", {})
            metrics.setdefault("model_requests", 0)
            metrics.setdefault("cache_hits", 0)
            manifest["updated_at"] = utc_now()
            return manifest
    return _new_manifest(args, config)


def _resolved_run_config(defaults: dict, args, manifest: dict) -> dict:
    """Merge execution settings without erasing the intake contract.

    The preflight manifest owns the user's figure count and OCR decision.  The
    runner used to replace ``config`` wholesale, which discarded those fields
    and let a later ``--ocr off`` silently contradict intake.
    """
    recorded = dict(manifest.get("config") or {})
    recorded_ocr = recorded.get("ocr")
    source = recorded.get("ocr_decision_source")
    resolved = dict(defaults)

    # A paper ceiling is a user decision, not a mode invariant. Preserve an
    # explicitly recorded value when the CLI does not replace it. Null means
    # "use the mode default" (which is uncapped for broad).
    if args.max_papers is None and recorded.get("max_papers") is not None:
        resolved["max_papers"] = recorded["max_papers"]

    if args.ocr is None and recorded_ocr in OCR_MODES:
        resolved["ocr"] = recorded_ocr
    elif (args.ocr is not None and source in {"explicit_user", "delegated_default"}
          and recorded_ocr in OCR_MODES and args.ocr != recorded_ocr):
        raise ValueError(
            f"--ocr {args.ocr} conflicts with the recorded {source} choice "
            f"ocr={recorded_ocr}; update intake instead of overriding it"
        )

    paper_limit = resolved.get("max_papers")
    if paper_limit is not None and (
        isinstance(paper_limit, bool)
        or not isinstance(paper_limit, int)
        or paper_limit < 1
    ):
        raise ValueError("max_papers must be null (no cap) or a positive integer")

    config = {
        **recorded,
        **resolved,
        "backend": args.backend,
        "model": args.model or DEFAULT_MODELS.get(args.backend, ""),
        "marker_fallback": args.marker_fallback,
        "adjudication_jobs": args.adjudication_jobs,
    }
    if "minimum_paper_figures" in recorded:
        require_figure_intake({**manifest, "config": config})
    return config


def _records_from_args(args, vendor: dict) -> list[dict]:
    supplied = sum(bool(x) for x in (args.records, args.ids, args.pdfs))
    if supplied != 1:
        raise ValueError("provide exactly one of --records, --ids, or --pdfs")
    if args.records:
        return load_records(pathlib.Path(args.records))
    if args.ids:
        return vendor["search"].dedupe(vendor["search"].lookup_europe_pmc(args.ids))
    return vendor["search"].from_local(args.pdfs)


def _apply_paper_limit(records: list[dict], max_papers: int | None) -> list[dict]:
    """Apply an explicit paper ceiling; ``None`` keeps the full selected set."""
    return records if max_papers is None else records[:max_papers]


def _apply_prior_fulltext_overrides(records: list[dict],
                                    run_root: pathlib.Path) -> list[dict]:
    """Attach prior-run files copied locally by reuse_prior_fulltext.py."""
    path = run_root / "corpus" / "prior_fulltext_overrides.jsonl"
    if not path.exists():
        return records
    overrides = {str(row.get("paper_id") or ""): row for row in read_jsonl(path)}
    out: list[dict] = []
    for record in records:
        pid = str(record.get("paper_id") or "")
        override = overrides.get(pid)
        if not override:
            out.append(record)
            continue
        for field in ("local_pdf", "local_xml", "figures_pdf"):
            value = str(override.get(field) or "")
            if value and not pathlib.Path(value).is_file():
                raise ValueError(
                    f"prior full-text override for {pid} points to missing {field}: "
                    f"{value}"
                )
        out.append({**record, **override})
    return out


def _process_papers(args, records: list[dict], manifest: dict, vendor: dict):
    run_root = pathlib.Path(args.run_root)
    parsed_dir = run_root / "fulltext" / "parsed"
    pdfs_dir = run_root / "fulltext" / "pdfs"
    figures_dir = run_root / "fulltext" / "figures"
    cache_dir = pathlib.Path(args.cache_dir or (run_root / "cache"))
    parsed_dir.mkdir(parents=True, exist_ok=True)
    papers, misses, parsed_by_paper, quality_rows = [], [], {}, []

    dirs = {
        "pdfs_dir": str(pdfs_dir), "figures_dir": str(figures_dir),
        "parsed_dir": str(parsed_dir), "cache_dir": str(cache_dir),
    }
    opts = {
        "refresh_acquisition": getattr(args, "refresh_acquisition", False),
        "fast_fail_closed": getattr(args, "fast_fail_closed", False),
        "marker_fallback": args.marker_fallback,
        "min_sentences": args.min_sentences,
    }

    n_jobs = _resolve_parse_jobs(getattr(args, "parse_jobs", 0) or 0, len(records))
    manifest.setdefault("config", {})["parse_jobs"] = n_jobs

    def _apply(index: int, res: dict) -> None:
        """Parent-only: persist one worker result deterministically (snapshot +
        manifest write + papers/misses append + progress line)."""
        pid = res["paper_id"]
        status = res["status"]
        record = res["record"]
        if status == "not_retrieved":
            misses.append(record)
            manifest["papers"][pid] = {
                "status": "not_retrieved",
                "reason": res.get("reason"),
                "acquire_secs": res.get("acquire_secs"),
                "from_cache": res.get("from_cache"),
            }
        elif status == "failed":
            misses.append(record)
            manifest["papers"][pid] = {"status": "failed", "reason": res.get("reason")}
        else:  # parsed
            parsed = res["parsed"]
            snapshot = parsed_dir / f"{safe_id(pid)}.json"
            atomic_json(snapshot, parsed)
            parsed_by_paper[pid] = parsed
            quality = res.get("parse_quality") or parse_quality.assess(
                parsed, min_sentences=args.min_sentences,
            )
            quality_rows.append(quality)
            record.update({
                "parser": parsed.get("parser"),
                "n_pages": parsed.get("n_pages"),
                "full_text_status": "retrieved",
                # Derive the coarse access label from the acquisition
                # classifier rather than defaulting everything retrieved to
                # "open_access". A `free_to_read` paper is an author manuscript
                # that PMC serves for free but that is NOT in the OA subset —
                # stamping it "open_access" made the report claim a licence the
                # paper does not have, which is the label half of the same
                # defect that let a paywalled PDF be fetched.
                "access": record.get("access") or _access_label(record, res),
                "url": record.get("oa_full_url") or record.get("landing_url"),
            })
            papers.append(record)
            manifest["papers"][pid] = {
                "status": "parsed",
                "parser": parsed.get("parser"),
                "sentences": len(parsed.get("sentences", [])),
                "figures": len(parsed.get("figures", [])),
                "parse_quality": quality["state"],
                "acquire_secs": res.get("acquire_secs"),
                "from_cache": res.get("from_cache"),
            }
        manifest["updated_at"] = utc_now()
        atomic_json(run_root / "run_manifest.json", manifest)
        print(f"[evidence-first] paper {index}/{len(records)} {pid}: {manifest['papers'][pid]['status']}")

    def _run(indexed_records: list[tuple[int, dict]], run_opts: dict) -> dict[int, dict]:
        """Run a paper subset and return results without mutating run state."""
        if n_jobs <= 1 or len(indexed_records) <= 1:
            return {
                index: _acquire_and_parse_one(original, dirs, run_opts)
                for index, original in indexed_records
            }
        try:
            results: dict[int, dict] = {}
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=min(n_jobs, len(indexed_records))
            ) as pool:
                fut_to_idx = {
                    pool.submit(_acquire_and_parse_one, original, dirs, run_opts): index
                    for index, original in indexed_records
                }
                for future in concurrent.futures.as_completed(fut_to_idx):
                    results[fut_to_idx[future]] = future.result()
            return results
        except Exception as exc:
            print(
                f"[evidence-first] parallel parse unavailable "
                f"({type(exc).__name__}: {exc}); falling back to serial"
            )
            return {
                index: _acquire_and_parse_one(original, dirs, run_opts)
                for index, original in indexed_records
            }

    indexed = list(enumerate(records, 1))
    results = _run(indexed, opts)

    # A transient miss must not survive a long run merely because the first
    # request timed out. Retry only retrieval failures, once, with the negative
    # cache bypassed. Confirmed paywalls and parser failures are not retried.
    retry_indexes = [
        index for index, result in results.items()
        if result.get("status") == "not_retrieved"
        and (result.get("record") or {}).get("_not_retrieved_kind") == "retrieval_failed"
    ]
    retry_enabled = not getattr(args, "no_retry_transient", False)
    recovered = 0
    if retry_enabled and retry_indexes:
        retry_opts = {**opts, "refresh_acquisition": True}
        originals_by_index = dict(indexed)
        retried = _run(
            [(index, originals_by_index[index]) for index in retry_indexes],
            retry_opts,
        )
        for index, result in retried.items():
            if result.get("status") == "parsed":
                recovered += 1
            results[index] = result
    metrics = manifest.setdefault("metrics", {})
    metrics["transient_retry_attempts"] = len(retry_indexes) if retry_enabled else 0
    metrics["transient_retry_recovered"] = recovered
    metrics["transient_retry_remaining"] = sum(
        1 for result in results.values()
        if result.get("status") == "not_retrieved"
        and (result.get("record") or {}).get("_not_retrieved_kind") == "retrieval_failed"
    )

    # Apply once, in input order, so retry does not leave duplicate or stale
    # manifest rows and parallelism cannot change canonical ordering.
    for index in range(1, len(records) + 1):
        _apply(index, results[index])
    write_jsonl(run_root / "fulltext" / "parse_quality.jsonl", quality_rows)
    write_jsonl(run_root / "fulltext" / "acquisition_routes.jsonl", [
        {
            "schema_version": 1,
            "paper_id": result["paper_id"],
            "outcome": result["status"],
            "access_state": str((result.get("record") or {}).get("access_state") or ""),
            "attempts": list((result.get("record") or {}).get("_attempts") or []),
            "final_reason": str(
                (result.get("record") or {}).get("_not_retrieved_reason")
                or result.get("reason") or ""
            ),
            "user_supplied": bool(result.get("user_supplied")),
        }
        for _index, result in sorted(results.items())
    ])
    return papers, misses, parsed_by_paper


def _normalize_figure_metadata(parsed_by_paper: dict[str, dict]) -> None:
    """Attach caption/OCR lineage to every parsed figure in place.

    Embedded panel crops can be emitted separately from their parent legend.
    When exactly one captioned figure shares the page, inherit that caption and
    retain the parent ID. Ambiguous pages stay captionless rather than receiving
    a plausible but wrong legend.
    """
    for parsed in parsed_by_paper.values():
        figures = parsed.get("figures", []) or []
        captioned_by_page: dict[object, list[dict]] = defaultdict(list)
        for figure in figures:
            if str(figure.get("caption") or "").strip() and figure.get("page") is not None:
                captioned_by_page[figure.get("page")].append(figure)
        for figure in figures:
            caption = str(figure.get("caption") or "").strip()
            if caption:
                figure.setdefault("caption_source", "parsed_caption")
            else:
                parents = [
                    row for row in captioned_by_page.get(figure.get("page"), [])
                    if row is not figure
                ]
                if len(parents) == 1:
                    figure["caption"] = str(parents[0].get("caption") or "")
                    figure["caption_source"] = "parent_figure_same_page"
                    figure["parent_figure_id"] = str(
                        parents[0].get("figure_id") or ""
                    )
                else:
                    figure.setdefault("caption_source", "none")

            if "ocr_status" in figure:
                figure.setdefault("ocr_attempted", False)
                figure.setdefault("ocr_error", "")
                continue
            if figure.get("_ocr_failed"):
                figure.update({
                    "ocr_attempted": True,
                    "ocr_status": "failed",
                    "ocr_error": "legacy OCR failure",
                })
            elif figure.get("_ocr_skipped"):
                figure.update({
                    "ocr_attempted": False,
                    "ocr_status": "skipped",
                    "ocr_error": str(figure.get("_ocr_skipped") or ""),
                })
            elif figure.get("ocr"):
                figure.update({
                    "ocr_attempted": True,
                    "ocr_status": "completed",
                    "ocr_error": "",
                })
            else:
                figure.update({
                    "ocr_attempted": False,
                    "ocr_status": "not_attempted",
                    "ocr_error": "",
                })


def _validate_ocr_contract(parsed_by_paper: dict[str, dict], mode: str) -> None:
    """Fail ``ocr=all`` if an image-backed figure was not actually read."""
    if mode != "all":
        return
    failures = []
    for pid, parsed in parsed_by_paper.items():
        for figure in parsed.get("figures", []) or []:
            if not figure.get("image_path"):
                continue
            status = str(figure.get("ocr_status") or "")
            if not figure.get("ocr_attempted") or status in {"not_attempted", "skipped"}:
                failures.append(
                    f"{pid}/{figure.get('figure_id')} status={status or 'missing'}"
                )
            elif status == "failed":
                failures.append(
                    f"{pid}/{figure.get('figure_id')} failed: "
                    f"{figure.get('ocr_error') or 'unknown error'}"
                )
    if failures:
        raise RuntimeError(
            "OCR all did not complete for every image-backed figure: "
            + "; ".join(failures[:12])
        )


def _targeted_ocr(parsed_by_paper: dict[str, dict], candidates: list[dict], vendor: dict, cache_dir: pathlib.Path, *, all_figures: bool = False) -> set[str]:
    """Run OCR for candidate captions and image-backed captionless figures.

    Returns the set of paper_ids whose figures were actually OCR'd (so the caller
    can rebuild blocks + re-rank ONLY for changed papers, or skip entirely when
    the set is empty)."""
    caption_figures: dict[str, set[str]] = defaultdict(set)
    candidate_papers: set[str] = set()
    for row in candidates:
        candidate_papers.add(str(row.get("paper_id") or ""))
        parts = row["block_id"].split(":CAP:", 1)
        if len(parts) == 2:
            caption_figures[row["paper_id"]].add(parts[1])
    # Captionless crops could never become caption candidates, so the prior
    # targeted pass made their recovery impossible. OCR them when their paper
    # has any candidate evidence; selection can then match the in-panel text.
    for pid in candidate_papers:
        for figure in (parsed_by_paper.get(pid) or {}).get("figures", []) or []:
            if figure.get("image_path") and not str(figure.get("caption") or "").strip():
                caption_figures[pid].add(str(figure.get("figure_id") or ""))
    if not caption_figures:
        return set()
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    ocr = importlib.import_module("ocr_figures")
    changed: set[str] = set()
    for pid, figure_ids in caption_figures.items():
        parsed = parsed_by_paper.get(pid)
        if not parsed:
            continue
        chosen = []
        for fig in parsed.get("figures", []) or []:
            fig["paper_id"] = pid
            needs_ocr = (
                str(fig.get("figure_id")) in figure_ids
                and not (fig.get("ocr") or [])
            )
            if needs_ocr:
                chosen.append(fig)
        if chosen:
            try:
                updated = ocr.ocr_figures(
                    chosen,
                    cache_dir=cache_dir / "ocr",
                    min_conf=0.5,
                    verbose=False,
                    min_size_px=1 if all_figures else 100,
                )
                by_id = {
                    str(row.get("figure_id") or ""): row for row in updated
                }
                parsed["figures"] = [
                    by_id.get(str(row.get("figure_id") or ""), row)
                    for row in parsed.get("figures", []) or []
                ]
            except ocr.OcrUnavailable as exc:
                # The intake contract asked for OCR. Falling back to captions
                # here would silently change that user-visible choice after the
                # long run had already begun.
                raise RuntimeError(
                    "figure OCR was requested but is unavailable; rerun "
                    f"scripts/install.sh and resume: {exc}"
                ) from exc
            changed.add(pid)
    return changed


def _emit_adjudication_batches(run_root, candidates, claims_by_id, blocks_by_id,
                               papers_by_id, claims_per_call, max_blocks) -> int:
    """Compatibility wrapper around the focused batching module."""
    batches = _candidate_batches(
        candidates, claims_by_id, blocks_by_id, claims_per_call, max_blocks
    )
    return emit_batches(run_root, batches, papers_by_id)


def _load_imported_adjudications(path: pathlib.Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        return read_jsonl(path)
    value = json.loads(path.read_text())
    if isinstance(value, list):
        return value
    return value.get("evidence", [])


def _native_adjudication_coverage(
    run_root: pathlib.Path,
    adjudications_path: pathlib.Path,
) -> tuple[dict[str, set[str]], dict]:
    """Recover examined blocks, including batches that accepted zero rows.

    A flat evidence JSONL can only name accepted anchors. Native batch assembly
    writes an immutable receipt for every completed task, so the pipeline can
    distinguish "examined and found no evidence" from "task disappeared after
    compaction" without trusting coordinator memory.
    """
    receipt_path = run_root / "state" / "assemblies" / "adjudications.json"
    if not receipt_path.exists():
        return {}, {}
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    destination = run_root / str(receipt.get("destination") or "")
    if destination.resolve() != adjudications_path.resolve():
        return {}, {}
    if receipt.get("complete") is not True:
        raise ValueError("native adjudication assembly receipt is incomplete")
    digest = hashlib.sha256(adjudications_path.read_bytes()).hexdigest()
    if digest != str(receipt.get("destination_sha256") or ""):
        raise ValueError("native adjudications changed after assembly")

    examined: dict[str, set[str]] = defaultdict(set)
    units = receipt.get("units") or []
    if int(receipt.get("schema_version") or 1) < 2:
        # Receipts from pre-ledger runs prove file completion but did not retain
        # negative-search coverage. Preserve their accepted-row fallback.
        return {}, receipt
    if len(units) != int(receipt.get("task_count") or 0):
        raise ValueError("native adjudication receipt omits completed task units")
    for unit in units:
        block_ids = {str(value) for value in (unit.get("block_ids") or []) if value}
        for claim_id in unit.get("claim_ids") or []:
            if claim_id:
                examined[str(claim_id)].update(block_ids)
    return examined, receipt


def _write_run_tables(run_root, papers, misses, claims, matrix, evidence):
    write_jsonl(run_root / "corpus" / "claims.jsonl", claims)
    write_jsonl(run_root / "fulltext" / "papers.jsonl", papers)
    paper_columns = [
        "paper_id", "title", "authors", "year", "journal", "doi", "pmid", "pmcid",
        "access", "oa_source", "oa_full_url", "landing_url", "url", "parser", "n_pages",
        "full_text_status",
    ]
    write_csv(run_root / "fulltext" / "papers.csv", papers, paper_columns)
    miss_rows = [{
        "paper_id": r.get("paper_id"), "doi": r.get("doi"), "pmid": r.get("pmid"),
        "pmcid": r.get("pmcid"), "title": r.get("title"),
        "reason": r.get("_not_retrieved_reason"), "attempts": r.get("_attempts", []),
    } for r in misses]
    write_csv(
        run_root / "fulltext" / "not_retrieved.csv", miss_rows,
        ["paper_id", "doi", "pmid", "pmcid", "title", "reason", "attempts"],
    )
    write_jsonl(run_root / "fulltext" / "not_retrieved.jsonl", misses)
    write_jsonl(run_root / "evidence" / "evidence.jsonl", evidence)
    if evidence:
        write_csv(run_root / "deliverables" / "evidence_table.csv", evidence)
    else:
        write_csv(
            run_root / "deliverables" / "evidence_table.csv", [],
            ["evidence_id", "claim_id", "paper_id", "quote", "stance", "source_locator", "verified"],
        )
    write_csv(run_root / "synthesis" / "claim_evidence_matrix.csv", matrix)
    write_csv(run_root / "deliverables" / "claim_evidence_matrix.csv", matrix)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claim-first grounded evidence pipeline")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--claims", required=True)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--records")
    sources.add_argument("--ids", nargs="+")
    sources.add_argument("--pdfs", nargs="+")
    parser.add_argument("--review-mode", choices=sorted(MODE_DEFAULTS), default="quick")
    parser.add_argument(
        "--backend",
        choices=["none"],
        default="none",
        help="Biomni-native adjudication (the only supported skill runtime)",
    )
    parser.add_argument("--model", help=argparse.SUPPRESS)
    parser.add_argument("--adjudications-file")
    parser.add_argument("--ocr", choices=["off", "targeted", "all"])
    parser.add_argument(
        "--max-papers", type=int,
        help="optional positive cap on selected full texts; broad mode is uncapped when omitted",
    )
    parser.add_argument("--top-per-paper", type=int)
    parser.add_argument("--top-per-claim", type=int)
    parser.add_argument("--claims-per-call", type=int)
    parser.add_argument("--max-blocks-per-call", type=int)
    parser.add_argument(
        "--figure-quota", type=int,
        help="reserved caption/figure_ocr candidate slots per paper per claim, "
             "granted IN ADDITION to --top-per-paper so figure evidence never "
             "has to outcompete body sentences (mode default: 0 quick, 2 deep/broad)",
    )
    parser.add_argument("--marker-fallback", action="store_true")
    parser.add_argument("--min-sentences", type=int, default=20)
    parser.add_argument("--cache-dir")
    parser.add_argument(
        "--parse-jobs", type=int, default=0,
        help="parallel PDF-parse workers: 0=auto (min(4, cpu-1)), 1=serial, N=ceiling",
    )
    parser.add_argument(
        "--adjudication-jobs",
        type=int,
        default=DEFAULT_ADJUDICATION_JOBS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--refresh-acquisition", action="store_true",
        help="Ignore the persistent acquisition cache's negative hits and "
             "re-walk the full retrieval waterfall.")
    parser.add_argument(
        "--no-retry-transient", action="store_true",
        help="Disable the default one-time, cache-bypassing retry of transient "
             "retrieval failures.")
    parser.add_argument(
        "--preprocessed-run", action="store_true",
        help="Reuse merged fulltext/papers.jsonl, not_retrieved.jsonl, and "
             "fulltext/parsed snapshots; skip acquisition and parsing.")
    parser.add_argument(
        "--preprocess-only", action="store_true",
        help="Stop after acquisition/parsing and write mergeable full-text artifacts.")
    parser.add_argument(
        "--fast-fail-closed", action="store_true",
        help="Opt into the Unpaywall closed-paper speed shortcut; by default "
             "all directly accessible internet-PDF routes are attempted.")
    parser.add_argument(
        "--no-fast-fail-closed", dest="fast_fail_closed", action="store_false",
        help=argparse.SUPPRESS)
    parser.set_defaults(fast_fail_closed=False)
    parser.add_argument("--run-id")
    parser.add_argument("--title", default="Grounded literature review")
    parser.add_argument("--question", default="")
    args = parser.parse_args(argv)
    if not 1 <= args.adjudication_jobs <= MAX_ADJUDICATION_JOBS:
        parser.error(
            "--adjudication-jobs must be between 1 and "
            f"{MAX_ADJUDICATION_JOBS}"
        )

    started = time.time()
    run_root = pathlib.Path(args.run_root).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    defaults = dict(MODE_DEFAULTS[args.review_mode])
    for key in ("max_papers", "top_per_paper", "top_per_claim", "claims_per_call",
                "max_blocks_per_call", "figure_quota", "ocr"):
        value = getattr(args, key)
        if value is not None:
            defaults[key] = value
    initial_config = {
        **defaults,
        "backend": args.backend,
        "model": args.model or DEFAULT_MODELS.get(args.backend, ""),
        "marker_fallback": args.marker_fallback,
        "adjudication_jobs": args.adjudication_jobs,
    }
    manifest_path = run_root / "run_manifest.json"
    manifest = _load_manifest(manifest_path, args, initial_config)
    try:
        config = _resolved_run_config(defaults, args, manifest)
    except ValueError as exc:
        parser.error(str(exc))
    defaults.update({key: config[key] for key in defaults})
    manifest["config"] = config
    manifest["status"] = "running"
    atomic_json(manifest_path, manifest)

    claims = load_claims(pathlib.Path(args.claims))
    claims_by_id = {c["claim_id"]: c for c in claims}
    vendor = _vendor_modules()
    records = _apply_prior_fulltext_overrides(
        _apply_paper_limit(
            _records_from_args(args, vendor), defaults["max_papers"]
        ),
        run_root,
    )
    write_jsonl(run_root / "corpus" / "records.jsonl", records)
    if (run_root / "corpus" / "references.jsonl").exists():
        from corpus_ledger import refresh as refresh_corpus_ledger
        _ledger, selection_errors = refresh_corpus_ledger(
            run_root, run_root / "corpus" / "records.jsonl"
        )
        if selection_errors:
            parser.error("corpus selection gate: " + "; ".join(selection_errors))
    acquire_started = time.monotonic()
    if args.preprocessed_run:
        papers_path = run_root / "fulltext" / "papers.jsonl"
        misses_path = run_root / "fulltext" / "not_retrieved.jsonl"
        if not papers_path.exists() or not misses_path.exists():
            parser.error(
                "--preprocessed-run requires fulltext/papers.jsonl and "
                "fulltext/not_retrieved.jsonl"
            )
        papers = read_jsonl(papers_path)
        misses = read_jsonl(misses_path)
        parsed_by_paper = {}
        for paper in papers:
            pid = str(paper.get("paper_id") or "")
            snapshot = run_root / "fulltext" / "parsed" / f"{safe_id(pid)}.json"
            if not snapshot.exists():
                parser.error(f"preprocessed snapshot missing for {pid}: {snapshot}")
            parsed_by_paper[pid] = json.loads(snapshot.read_text())
            manifest.setdefault("papers", {})[pid] = {
                "status": "parsed",
                "parser": parsed_by_paper[pid].get("parser"),
                "sentences": len(parsed_by_paper[pid].get("sentences", [])),
                "figures": len(parsed_by_paper[pid].get("figures", [])),
                "source": "managed_machine_merge",
            }
        for miss in misses:
            pid = str(miss.get("paper_id") or "")
            manifest.setdefault("papers", {})[pid] = {
                "status": "not_retrieved",
                "reason": miss.get("_not_retrieved_reason"),
                "source": "managed_machine_merge",
            }
        manifest.setdefault("config", {})["preprocessed_run"] = True
    else:
        papers, misses, parsed_by_paper = _process_papers(args, records, manifest, vendor)
        transient_remaining = sum(
            1 for row in misses
            if str(row.get("_not_retrieved_kind") or "") == "retrieval_failed"
        )
        atomic_json(run_root / "fulltext" / "global_transient_retry.json", {
            "schema_version": 1,
            "completed": transient_remaining == 0,
            "attempted": int(
                manifest.get("metrics", {}).get("transient_retry_attempts") or 0
            ),
            "recovered": int(
                manifest.get("metrics", {}).get("transient_retry_recovered") or 0
            ),
            "remaining": transient_remaining,
            "reason": (
                "per-run recovery completed"
                if transient_remaining == 0
                else "global post-merge retry still required"
            ),
        })
    manifest["metrics"].setdefault("stage_timings_seconds", {})[
        "acquire_parse"
    ] = round(time.monotonic() - acquire_started, 3)
    papers_by_id = {p["paper_id"]: p for p in papers}

    _normalize_figure_metadata(parsed_by_paper)
    quality_rows = []
    for parsed in parsed_by_paper.values():
        quality = parsed.get("parse_quality") or parse_quality.assess(
            parsed, min_sentences=args.min_sentences,
        )
        parsed["parse_quality"] = quality
        quality_rows.append(quality)
    write_jsonl(run_root / "fulltext" / "parse_quality.jsonl", quality_rows)

    # Build blocks once, keyed by paper so an OCR pass can rebuild ONLY changed
    # papers (avoids rebuilding + re-ranking the entire corpus a second time).
    blocks_by_paper = {pid: build_blocks(parsed) for pid, parsed in parsed_by_paper.items()}
    blocks = [b for pid in parsed_by_paper for b in blocks_by_paper[pid]]

    def _rank(bl):
        return rank_candidates(
            claims, bl,
            top_per_paper=defaults["top_per_paper"],
            top_per_claim=defaults["top_per_claim"],
            figure_quota=defaults["figure_quota"],
        )

    candidates = _rank(blocks)
    if defaults["ocr"] in {"targeted", "all"}:
        if defaults["ocr"] == "all":
            candidates_for_ocr = [
                {
                    "paper_id": pid,
                    "block_id": f"{pid}:CAP:{figure.get('figure_id')}",
                }
                for pid, parsed in parsed_by_paper.items()
                for figure in (parsed.get("figures", []) or [])
            ]
        else:
            candidates_for_ocr = candidates
        changed = _targeted_ocr(
            parsed_by_paper, candidates_for_ocr, vendor,
            pathlib.Path(args.cache_dir or (run_root / "cache")),
            all_figures=defaults["ocr"] == "all",
        )
        # Only if OCR actually added figure_ocr blocks do we rebuild (just the
        # changed papers) and re-rank once so the new blocks can be surfaced.
        if changed:
            for pid in changed:
                if pid in parsed_by_paper:
                    blocks_by_paper[pid] = build_blocks(parsed_by_paper[pid])
            blocks = [b for pid in parsed_by_paper for b in blocks_by_paper[pid]]
            candidates = _rank(blocks)
    _normalize_figure_metadata(parsed_by_paper)
    _validate_ocr_contract(parsed_by_paper, defaults["ocr"])
    for pid, parsed in parsed_by_paper.items():
        atomic_json(
            run_root / "fulltext" / "parsed" / f"{safe_id(pid)}.json",
            parsed,
        )
    write_jsonl(run_root / "fulltext" / "blocks.jsonl", blocks)
    write_jsonl(run_root / "evidence" / "candidates.jsonl", candidates)
    blocks_by_id = {b["block_id"]: b for b in blocks}

    if args.preprocess_only:
        _write_run_tables(run_root, papers, misses, claims, [], [])
        manifest["metrics"].update({
            "papers_considered": len(records),
            "papers_selected": len(records),
            "papers_full_text": len(papers),
            "papers_not_retrieved": len(misses),
            "blocks": len(blocks),
            "candidate_links": len(candidates),
            "ocr_mode": defaults["ocr"],
        })
        runtime_metrics.record_invocation(
            manifest["metrics"], started, "preprocess"
        )
        manifest["status"] = "preprocessed"
        manifest["updated_at"] = utc_now()
        atomic_json(manifest_path, manifest)
        print(
            f"[evidence-first] status=preprocessed "
            f"papers={len(papers)}/{len(records)}"
        )
        return 0

    # In-session adjudication got one flat candidates.jsonl and a sentence of
    # prose telling the operator to batch it "in bounded per-paper batches". The
    # batching rule already exists here and was used only on the API path, so an
    # agent adjudicating natively improvised its own chunks and did them one
    # conversational turn at a time — 81 to 189 serial turns on a broad run, and
    # nothing anywhere said the chunks are independent of each other.
    #
    # Emitting them as discrete files makes the work countable and lets the Prod
    # coordinator complete several bounded units per turn without losing task
    # boundaries. Each carries the SAME canonical prompt, so native execution
    # cannot drift into adjudicating by different rules.
    batches = _candidate_batches(
        candidates,
        claims_by_id,
        blocks_by_id,
        defaults["claims_per_call"],
        defaults["max_blocks_per_call"],
    )
    emit_batches(run_root, batches, papers_by_id)

    raw_rows: list[tuple[dict, str, str, dict]] = []
    # Blocks actually put in front of a stance adjudication (which labels
    # `contradicts` as well as `supports`), per claim. This is the evidence that
    # opposing evidence was looked for at all.
    adjudicated_blocks: dict[str, set[str]] = defaultdict(set)
    if args.adjudications_file:
        imported_path = pathlib.Path(args.adjudications_file)
        native_coverage, native_receipt = _native_adjudication_coverage(
            run_root, imported_path
        )
        for claim_id, block_ids in native_coverage.items():
            adjudicated_blocks[claim_id].update(block_ids)
        if native_receipt:
            manifest["metrics"]["native_adjudication_batches_completed"] = int(
                native_receipt.get("task_count") or 0
            )
            manifest["metrics"]["native_adjudication_batches_zero_accepts"] = sum(
                1
                for unit in (native_receipt.get("units") or [])
                if int(unit.get("accepted_row_count") or 0) == 0
            )
        for row in _load_imported_adjudications(imported_path):
            raw_rows.append((row, "import", "import", {}))
            # Non-native imports have no assembly receipt, so their returned
            # rows remain the only blocks known to have been examined.
            adjudicated_blocks[str(row.get("claim_id") or "")].add(
                str(row.get("block_id") or "")
            )
    elif args.backend != "none":
        adjudication_started = time.monotonic()
        results = run_provider_batches(
            batches,
            papers_by_id,
            backend=args.backend,
            model=model,
            cache_dir=run_root / "evidence" / "adjudication_cache",
            jobs=args.adjudication_jobs,
        )
        manifest["metrics"].setdefault("stage_timings_seconds", {})[
            "adjudication"
        ] = round(time.monotonic() - adjudication_started, 3)
        manifest["metrics"]["adjudication_jobs"] = min(
            args.adjudication_jobs, max(1, len(batches))
        )
        manifest["metrics"]["adjudication_batch_seconds"] = [
            result.elapsed_seconds for result in results
        ]
        for result in results:
            if result.error:
                manifest.setdefault("errors", []).append({
                    "paper_id": result.paper_id,
                    "error": result.error,
                    "at": utc_now(),
                })
                continue
            if result.cache_hit:
                manifest["metrics"]["cache_hits"] += 1
            else:
                manifest["metrics"]["model_requests"] += 1
            for claim_id in result.claim_ids:
                adjudicated_blocks[claim_id].update(result.block_ids)
            meta = result.meta or {}
            raw_rows.extend(
                (row, args.backend, model, meta) for row in result.rows
            )
        manifest["updated_at"] = utc_now()
        atomic_json(manifest_path, manifest)

    canonical_adjudications = run_root / "evidence" / "adjudications.jsonl"
    supplied_adjudications = (
        pathlib.Path(args.adjudications_file).resolve()
        if args.adjudications_file else None
    )
    # Native batch assembly already hashes its canonical JSONL. Rewriting that
    # file here could invalidate the assembly receipt despite identical rows.
    if supplied_adjudications != canonical_adjudications.resolve():
        write_jsonl(
            canonical_adjudications,
            [raw for raw, _backend, _model, _meta in raw_rows],
        )
    evidence, rejected, lineage = [], [], []
    first_by_evidence_id: dict[str, str] = {}
    for ordinal, (raw, backend, used_model, meta) in enumerate(raw_rows, 1):
        adjudication_id = evidence_lineage.adjudication_id(raw, ordinal)
        accepted, error = validate_adjudication(
            raw,
            claims_by_id=claims_by_id,
            blocks_by_id=blocks_by_id,
            papers_by_id=papers_by_id,
            backend=backend,
            model=used_model,
            request_meta=meta,
        )
        if accepted:
            evidence_id = accepted["evidence_id"]
            if evidence_id in first_by_evidence_id:
                lineage.append(evidence_lineage.duplicate(
                    adjudication_id, raw, evidence_id,
                    first_by_evidence_id[evidence_id],
                ))
            else:
                first_by_evidence_id[evidence_id] = adjudication_id
                evidence.append(accepted)
                lineage.append(evidence_lineage.accepted(
                    adjudication_id, raw, evidence_id,
                ))
        else:
            rejected.append({
                "adjudication_id": adjudication_id,
                "row": raw,
                "reason": error,
            })
            lineage.append(evidence_lineage.rejected(
                adjudication_id, raw, str(error or "validation failed"),
            ))
    evidence = sorted(evidence, key=lambda r: r["evidence_id"])
    matrix = build_matrix(
        claims, evidence,
        contradiction_coverage(claims, candidates, adjudicated_blocks),
    )
    _write_run_tables(run_root, papers, misses, claims, matrix, evidence)
    write_jsonl(run_root / "evidence" / "rejected_evidence.jsonl", rejected)
    write_jsonl(run_root / "evidence" / "evidence_lineage.jsonl", lineage)

    # Export the actual figure images that ground accepted claims, with the
    # in-figure OCR text/boxes drawn on top. No-op when no figure grounds a
    # claim (the common case for text/caption-only reviews). Best-effort:
    # never let figure export abort a completed run.
    figures_exported = 0
    cited_papers_with_figures = 0
    try:
        from export_figures import export_cited_figures
        _fig_summary = export_cited_figures(run_root)
        figures_exported = _fig_summary.get("figures_exported", 0)
        cited_papers_with_figures = _fig_summary.get("cited_papers_with_figures", 0)
    except Exception as exc:  # noqa: BLE001
        print(f"[evidence-first] figure export skipped: {type(exc).__name__}: {exc}")

    # Adjudication failures (bad key, rate limit, schema-invalid response) were
    # appended to manifest["errors"] and skipped. They were never counted and
    # never printed, so a run in which EVERY batch failed finished as
    # `completed`, exit 0, with a review declaring every claim an evidence gap —
    # indistinguishable from a corpus that genuinely contains no evidence.
    adjudication_errors = len(manifest.get("errors", []))
    stats = {
        "adjudication_errors": adjudication_errors,
        "papers_considered": len(records),
        "papers_selected": len(records),
        "papers_full_text": len(papers),
        "papers_not_retrieved": len(misses),
        "blocks": len(blocks),
        "candidate_links": len(candidates),
        "claims_total": len(claims),
        "claims_grounded": support_policy.count_grounded(
            r["support_state"] for r in matrix),
        "evidence_accepted": len(evidence),
        "evidence_rejected": len(rejected),
        "figures_exported": figures_exported,
        "cited_papers_with_figures": cited_papers_with_figures,
        "review_mode": args.review_mode,
        # The RESOLVED value, not the raw CLI arg. `--ocr` defaults to None
        # and the run branches on the mode default, so a deep run that did
        # run targeted OCR was recording "ocr_mode": null — which the figure
        # gates read to decide whether the run is figure-rich at all.
        "ocr_mode": defaults["ocr"],
        "model_requests": manifest["metrics"]["model_requests"],
        "model_cache_hits": manifest["metrics"]["cache_hits"],
    }
    atomic_json(run_root / "deliverables" / "review_stats.json", stats)
    manifest["metrics"].update(stats)
    runtime_metrics.record_invocation(manifest["metrics"], started, "finalize")
    status = (
        "completed" if args.backend != "none" or args.adjudications_file
        else "candidates_ready"
    )
    if adjudication_errors:
        status = "completed_with_errors"
    manifest["status"] = status
    manifest["updated_at"] = utc_now()
    atomic_json(manifest_path, manifest)
    if (run_root / "corpus" / "references.jsonl").exists():
        from corpus_ledger import refresh as refresh_corpus_ledger
        refresh_corpus_ledger(run_root, run_root / "corpus" / "records.jsonl")
    render_review(run_root, manifest, claims, matrix, evidence, papers)
    print(
        f"[evidence-first] status={manifest['status']} papers={len(papers)}/{len(records)} "
        f"candidates={len(candidates)} evidence={len(evidence)} rejected={len(rejected)} "
        f"adjudication_errors={adjudication_errors} "
        f"model_requests={manifest['metrics']['model_requests']}"
    )
    # A run that adjudicated nothing, or that lost batches to errors, must not
    # look like a clean run: its "evidence gaps" are unproven.
    if adjudication_errors:
        print(
            f"[evidence-first] {adjudication_errors} adjudication batch(es) failed — "
            "evidence is INCOMPLETE and any 'evidence gap' in this run is "
            f"unproven; see errors[] in {manifest_path}"
        )
    if not papers:
        print("[evidence-first] no paper full text was processed — nothing was adjudicated")
    return 1 if (adjudication_errors or not papers) else 0


if __name__ == "__main__":
    raise SystemExit(main())
