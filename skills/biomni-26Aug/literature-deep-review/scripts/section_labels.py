#!/usr/bin/env python3
"""Deciding which section a block belongs to — the auditable half of a locator.

A grounded quote is only checkable if its locator resolves, so a wrong section
label is a correctness defect, not a cosmetic one. Two shipped failures:

**Ten abstract sentences located at "page 1 · Front matter".** The parser called
a page-1 body run the abstract only when a SINGLE block reached 200 characters.
Nature-family PDFs break an abstract into several shorter blocks, so none
qualified and the whole abstract kept the placeholder label.

**A Results sentence located at "page 2 · Methods".** Neurology, BMJ and others
print a structured abstract as one paragraph with run-in labels — "Methods We
enrolled ... Results In both cohorts ... Conclusions Plasma NfL predicts ...".
pdfplumber merges that into one block and the parser peeled only the FIRST label,
so everything after it inherited "Methods". Three quotes from one paper shipped
that way, including one whose text begins "Results In both cohorts".

This module holds the two decisions as pure functions so they are testable
without the PDF stack; ``vendor/keyword_evidence/parse_pdf.py`` imports them.
"""
from __future__ import annotations

import re

# --- run-in (structured abstract) headings ----------------------------------

RUN_IN_HEADINGS = (
    "Background", "Objective", "Objectives", "Purpose", "Aim", "Aims",
    "Introduction", "Methods", "Materials and Methods", "Design",
    "Participants", "Results", "Findings", "Discussion", "Conclusion",
    "Conclusions", "Interpretation", "Significance", "Importance",
    "Classification of Evidence", "Trial Registration",
)

# A label counts as run-in only at the start of the block or directly after
# sentence-terminal punctuation, and only when followed by a capitalised word.
# "the Results section" (next word lowercase) and "in Results 1" (digit) do not
# match, which is what keeps the split from firing on ordinary prose.
_RUN_IN_RE = re.compile(
    r"(?:(?<=^)|(?<=[.!?])\s)\s*(" + "|".join(
        re.escape(h) for h in sorted(RUN_IN_HEADINGS, key=len, reverse=True))
    + r")\s+(?=[A-Z])")

# Fewest run-in labels a block must carry before it is split. One label is the
# ordinary "heading merged with its paragraph" case, which the parser's existing
# `_find_heading_prefix` already handles; two or more is a structured abstract.
MIN_RUN_IN_LABELS = 2


def split_run_in_headings(text: str) -> list[str]:
    """Split a structured-abstract block at each run-in heading.

    Returns segments each STARTING with their own heading, so the parser's
    existing heading-peel path assigns every segment the right section. A block
    with fewer than ``MIN_RUN_IN_LABELS`` labels is returned unchanged.
    """
    s = " ".join(str(text or "").split())
    if not s:
        return []
    starts = [m.start(1) for m in _RUN_IN_RE.finditer(s)]
    if len(starts) < MIN_RUN_IN_LABELS:
        return [s]
    if starts[0] > 0:
        starts.insert(0, 0)
    bounds = starts + [len(s)]
    return [seg for seg in (s[a:b].strip()
                            for a, b in zip(bounds, bounds[1:])) if seg]


# --- is this block abstract prose, or front-matter furniture? ---------------

_PROSE_VERB = re.compile(
    r"\b(is|are|was|were|be|been|has|have|had|do|does|did|show|shows|showed|"
    r"shown|suggest|suggests|indicate|indicates|reduce|reduces|reduced|"
    r"increase|increases|increased|cause|causes|caused|lead|leads|led|found|"
    r"find|observe|observed|report|reports|reported|demonstrate|demonstrates|"
    r"demonstrated|remain|remains|result|results|resulted|associate|associated|"
    r"correlate|correlated|identify|identified|develop|developed|protect|"
    r"protects|protected|carry|carries|carried|reveal|reveals|revealed)\b",
    re.IGNORECASE)

# Front-matter furniture that is long and ends in a period but is not prose.
# Length alone cannot separate an affiliation line from an abstract sentence.
_NOT_PROSE = re.compile(
    r"^\s*\d*\s*(?:Department|Departments|Division|Institute|Center|Centre|"
    r"School|Faculty|Laboratory|Hospital|University|College|Correspondence|"
    r"Corresponding author|Received|Accepted|Published|Keywords|Key words|"
    r"Funding|Conflict of interest|Competing interests|©|Copyright)\b",
    re.IGNORECASE)

# Fewest words a block needs before it can be read as abstract prose. Author
# lists and titles fall below it; a sentence of an abstract does not.
PROSE_MIN_WORDS = 8


def is_prose(text: str) -> bool:
    """Does this block read as body prose rather than front-matter furniture?"""
    t = " ".join(str(text or "").split())
    if len(t.split()) < PROSE_MIN_WORDS:
        return False
    if _NOT_PROSE.match(t):
        return False
    return bool(_PROSE_VERB.search(t))


# --- how far a section label may travel from its heading --------------------
#
# A detected heading owns every following block until the next heading. For a
# Results or Discussion section that is right. For a section that is physically
# short it is badly wrong, and two shipped reports carried 18 wrong locators
# between them:
#
#   "Bumber et al. 2025 ... Abstract, p. 18"      (an abstract is on page 1)
#   "Jackson et al. 2024 ... Competing Interests, p. 14"
#
# Nature Reviews prints a one-line competing-interests declaration in the
# first-page furniture; it was detected as a heading, and every body block for
# the next thirteen pages inherited it. The abstract case is the same shape.
#
# So a label from a section known to be SHORT expires. Past its span the honest
# answer is that we do not know the section, which is what `UNRESOLVED_SECTION`
# says — and because it matches neither the result-reporting nor the background
# list in `anchor_policy`, a quote landing there cannot claim `primary` on
# section grounds alone. That is the conservative reading, and the right one.
MAX_PAGE_SPAN: dict[str, int] = {
    # Front matter and abstracts live on the first page or two.
    "front matter": 1,
    "abstract": 2,
    # An introduction can legitimately run a few pages in a long review.
    "introduction": 3,
    "background": 3,
    # Boilerplate declarations are one paragraph. They never own body text.
    "competing interests": 0,
    "conflict of interest": 0,
    "funding": 0,
    "acknowledgments": 0,
    "acknowledgements": 0,
    "author contributions": 0,
    "data availability": 0,
    "ethics": 0,
}

# What a block's section is called when the last heading's label has expired.
# Not "Unknown": templates/report_contract.json forbids that, and rightly — but
# it forbids it as a stand-in for a section we DID resolve. Here we genuinely did
# not, and saying so beats inheriting a label that is definitely wrong.
UNRESOLVED_SECTION = "Body"


def section_for_page(section: str, heading_page: int, block_page: int) -> str:
    """The label a block may carry, given where its heading was found.

    ``heading_page`` and ``block_page`` are 0-based. Returns ``section``
    unchanged when it is allowed to reach this page, and ``UNRESOLVED_SECTION``
    when the label has travelled further than that section physically can.
    """
    name = str(section or "").strip()
    span = MAX_PAGE_SPAN.get(name.lower())
    if span is None:
        return name  # Results, Discussion, a free-text heading: may run on
    return name if (block_page - heading_page) <= span else UNRESOLVED_SECTION
