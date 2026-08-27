#!/usr/bin/env python3
"""Detect — and where possible repair — extraction damage in a verbatim quote.

Everything this skill promises rests on one property: a quoted sentence is the
sentence the paper contains. The shipped reports broke that property in five
distinct ways, all inside text presented between quotation marks:

    "partially protected against APOE4-driven n e u ro d e ge n e ration and
     n eu ro in fl am mation but not Tau pathology."          letter-spacing
    "the impact of APOE2 and APOE4 gene dose was signi fi cantly greater"
                                                              ligature split
    "Asexpected, when we quanti fi ed the amount of MMP9 protein"
                                                              lost word space
    "reduced levels of GRN in plasma ... (P 5 0.001)"          symbol corruption
    "Our data show that homozygous signatures that are eliminated or even
     reversed with the homozygous R136S mutation fully protects against
     APOE4-driven Tau pathology, APOE4-R136S mutation."       column splice

``evidence_first.looks_column_garbled`` caught none of them: it tests for a
hyphen-space seam, a doubled verb, a >=22-character token, and >=2 camelCase
transitions, and every example above slips between those.

**Repair before rejection.** ``references/figures_and_quotes.md`` records that
merged caption text was "rejected as garbled for two releases while the real fix
was one function away", and that a rejection is not a diagnosis. Two of these
five are losslessly recoverable — a split ligature and a letter-spaced run have
exactly one reading — so they are repaired and the repair is recorded. The other
three are not recoverable and are rejected with the reason named, so the operator
can see whether the fix belongs in the parser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- 1. split presentation ligatures ----------------------------------------
#
# pdfminer emits U+FB01 as the three characters " fi " when the font's ToUnicode
# map decomposes it, so "significantly" arrives as "signi fi cantly". The fix is
# unambiguous: a standalone fi/fl/ff/ffi/ffl token wedged between two lowercase
# word fragments belongs to the word.
_LIGATURE_SPLIT = re.compile(r"(?<=[a-z])\s+(ffi|ffl|fi|fl|ff)\s+(?=[a-z])")

# --- 1b. Greek letters orphaned by spaces -----------------------------------
#
# A Greek glyph usually comes from a different font than the surrounding text, and
# pdfminer often emits a space on each side of the font switch:
#
#   "Cy3-A β 42 uptake by pericytes"     should be "Cy3-Aβ42"
#   "Scale bar, 20 µ m."                 should be "20 µm"
#
# Both shipped reports carried these in figure captions. The join is
# meaning-preserving and unambiguous ONLY in these two shapes — a letter or digit,
# then a lone Greek letter, then a digit; or a number, then mu, then a unit
# letter. A bare "α β γ subunits" is left alone, because there the spaces are
# real.
_GREEK = "αβγδεζηθικλμνξπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩµ"
# "Cy3-A β 42" -> "Cy3-Aβ42": alphanumeric, lone Greek letter, digit.
_ORPHAN_GREEK = re.compile(rf"(?<=[A-Za-z0-9])\s+([{_GREEK}])\s+(?=\d)")
# "A β accumulation" -> "Aβ accumulation": a SINGLE capital binds to the Greek
# letter (Aβ, Aα, Aγ are the biomedical compounds this shape occurs in), and the
# following space is preserved because it is a real word boundary.
_ORPHAN_GREEK_WORD = re.compile(rf"\b([A-Z])\s+([{_GREEK}])\s+(?=[A-Za-z])")
# "20 µ m" -> "20 µm": the unit prefix binds to its unit, and the space between
# the number and the unit stays.
_ORPHAN_MICRON = re.compile(r"(?<=\d)(\s+)([µμ])\s+(?=[mgLl]\b)")

# --- 2. letter-spaced runs ---------------------------------------------------
#
# "n e u ro d e ge n e ration" — one word exploded into fragments. This is NOT
# repaired, though an earlier attempt did: closing the run requires knowing where
# the word ends, and the fragments here are not uniformly short
# ("...ge n e ration"), so a regex join yields "neurodegene ration" — still not a
# word, and now silently presented as verbatim. Deciding the boundary means
# knowing the word, so there is no single reading and the quote is rejected.
#
# The run is only damage when its pieces are not themselves words. "it is up to
# us to do so" is eight short lowercase tokens in a row and perfectly clean, so
# the test counts fragments that are NOT ordinary short words.
_SHORT_RUN = re.compile(r"(?:\b[a-z]{1,3}\b\s+){3,}\b[a-z]{1,3}\b")

# Ordinary English and biomedical words of three letters or fewer. A run made of
# these is prose; a run made of anything else is a shattered word.
_REAL_SHORT_WORDS = {
    "a", "an", "as", "at", "be", "by", "do", "for", "he", "if", "in", "is", "it",
    "its", "no", "not", "of", "on", "or", "so", "the", "to", "up", "us", "we",
    "was", "all", "and", "any", "are", "but", "can", "did", "due", "few", "had",
    "has", "her", "him", "his", "how", "may", "new", "non", "nor", "now", "off",
    "one", "our", "out", "own", "per", "pre", "put", "saw", "see", "set", "she",
    "six", "ten", "too", "top", "two", "use", "via", "who", "why", "yet", "add",
    "age", "ago", "aim", "air", "arm", "big", "bit", "box", "cut", "day", "end",
    "eye", "far", "fit", "fix", "gap", "get", "got", "gut", "hit", "ice", "key",
    "lab", "law", "led", "leg", "let", "log", "lot", "low", "map", "max", "men",
    "mid", "min", "mix", "net", "old", "pit", "rat", "raw", "ray", "row", "run",
    "sub", "sum", "tag", "tip", "ton", "try", "way", "wet", "win", "mm", "cm",
    "ml", "mg", "kg", "nm", "um", "pm", "hr", "wt", "ko", "ns", "sd", "se",
    "vs", "ie", "eg", "et", "al", "de", "ex", "vi", "iv", "ip", "sc", "po",
}

# --- 3. lost word spaces ----------------------------------------------------
#
# "Asexpected", "isoformand" — two words fused with no case transition to betray
# them, which is why the camelCase test missed them.
#
# A curated list of OBSERVED fusions, matched as whole tokens. A rule-based
# version was tried first and had to be abandoned: `\bAs(?=[a-z]{4,})` flags the
# "As" in "Astrocyte", and `(?<=[a-z]{4})and\b` flags "ligand", "strand" and
# "understand". A false positive here REJECTS GOOD EVIDENCE, so precision wins
# over coverage and this list grows from real parser output rather than from
# guesses about English. The general case — a very long token, or repeated
# camelCase transitions — is still handled by
# ``evidence_first._has_merged_words``.
_FUSED_TOKENS = {
    "asexpected", "asshown", "asdescribed", "asnoted", "asreported",
    "asdiscussed", "asindicated", "isoformand", "inaddition", "forexample",
    "wefound", "weobserved", "weshow", "thatthe", "withthe", "fromthe",
    "inthe", "ofthe", "andthe", "tothe", "onthe", "atthe", "bythe", "isthe",
    "wasthe", "arethe", "werethe", "thesame", "thisstudy", "thesedata",
}
_TOKEN = re.compile(r"[A-Za-z][A-Za-z\-]*")

# --- 4. corrupted comparison operators --------------------------------------
#
# "(P 5 0.001)" is "(P < 0.001)" with the glyph for "<" mapped to "5"; the same
# font bug produced "P 5 0.05". "= = r 0.15" is a doubled operator. These change
# what the sentence asserts — a p-value of 5 is not a p-value — and there is no
# safe repair, because "<" and ">" are equally plausible readings.
_BAD_OPERATOR = re.compile(
    r"\b[Pp]\s*[45]\s*0?\.\d"          # "P 5 0.001"
    r"|=\s*=\s*"                        # "= = r 0.15"
    r"|\b[Pp]\s+0\.\d+\s*[,)]"          # "p 0.0780)" — operator vanished
    r"|\b[nN]\s*=\s*=\s*\d")


@dataclass
class QuoteVerdict:
    """The outcome of inspecting one quote."""
    text: str                                    # repaired text, or the original
    repairs: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return not self.problems


# A manuscript line-number gutter interleaved into the text. Submitted
# manuscripts and preprints number every line in the margin, and the extractor
# reads those integers as words: "3 associated with a four to five-fold
# decreased AD risk", "95% CI; 5 0.08-0.49", "12 non-stratified analyses were
# concordant". They are silent falsifications — the quote gains a number the
# sentence never had, sitting next to real statistics where it reads as data.
# Split by how much each position tells us. A gutter number landing MID-CLAUSE
# ("95% CI; 5 0.08-0.49", "risk: 13 V236E") cannot be anything else — no author
# writes that. One landing after a full stop is ambiguous, because a sentence may
# legitimately open with a numeral ("15 mice were analysed per group"), so those
# only count when several appear and the ascending gutter becomes obvious.
_GUTTER_MIDCLAUSE = re.compile(
    r"(?<=[;,:)]\s)(\d{1,3})\s+(?=[a-z]{3,}|[A-Z][a-z]{2,})")
_GUTTER_SENTENCE_START = re.compile(
    r"(?:(?<=^)|(?<=\.\s))(\d{1,3})\s+(?=[a-z]{3,}|[A-Z][a-z]{2,})")

# Editorial furniture from a submission template, not content: "(350-word
# limit)", "Word count: 3,412", "[Insert Table 1 here]".
_MANUSCRIPT_FURNITURE = re.compile(
    r"\(\s*\d+\s*-\s*word\s+limit\s*\)|\bword\s+count\s*[:=]|"
    r"\[\s*insert\s+(?:table|figure)[^\]]*\]", re.IGNORECASE)

# Journal production history is useful metadata, but not scientific evidence.
# It can be interleaved into the first body sentence by PDF column extraction.
_PUBLICATION_TIMELINE = re.compile(
    r"\b(?:received|accepted|published(?:\s+online)?)\s+"
    r"\d{1,2}\s+[A-Z][a-z]{2,8}\s+\d{4}\b",
    re.IGNORECASE,
)

# Exact corruption signatures observed in the SLC33A1 replay. Broad split-word
# heuristics reject legitimate typography, so keep this list evidence-driven.
_KNOWN_CORRUPT_TOKENS = re.compile(r"\b(?:pheand|recnotypes)\b", re.IGNORECASE)
_KNOWN_SPLIT_WORDS = re.compile(r"\bm\s+embrane\b", re.IGNORECASE)

# A single manuscript line number at the very start is otherwise missed by the
# multi-number gutter detector. Restrict this to a one-digit orphan followed by
# a word so real sample-size statements such as "15 mice" remain valid.
_LEADING_LINE_NUMBER = re.compile(r"^\s*[1-9]\s+[A-Za-z][A-Za-z0-9-]{3,}\b")

# A scientific exponent split by the extractor: "P = 1.4x10 -3", "5.8x10 -6".
# The minus sign has drifted from the exponent, so the quote reads as a
# subtraction of a small integer from a round number.
_SPLIT_EXPONENT = re.compile(r"\b(\d(?:\.\d+)?)\s*[x×]\s*10\s+(-\s?\d+)")

# Content lost mid-sentence: "interaction between age 2 and . Error bars".
# A conjunction or preposition immediately before a full stop means the
# extractor dropped what followed, and no repair can recover it.
_TRUNCATED_CLAUSE = re.compile(
    r"\b(and|or|but|with|between|versus|vs\.?|than|from|of|for|to|in)\s*[.;]",
    re.IGNORECASE)


def repair(text: str) -> tuple[str, list[str]]:
    """Undo losslessly recoverable extraction damage. Returns (text, repairs)."""
    original = text or ""
    out = original
    repairs: list[str] = []

    fixed = _LIGATURE_SPLIT.sub(r"\1", out)
    if fixed != out:
        repairs.append("rejoined split fi/fl ligature")
        out = fixed

    fixed = _ORPHAN_GREEK_WORD.sub(r"\1\2 ", _ORPHAN_GREEK.sub(r"\1", out))
    if fixed != out:
        repairs.append("rejoined Greek letter orphaned by font-switch spaces")
        out = fixed

    fixed = _ORPHAN_MICRON.sub(r"\1\2", out)
    if fixed != out:
        repairs.append("rejoined unit prefix orphaned by font-switch spaces")
        out = fixed

    out = re.sub(r"\s{2,}", " ", out).strip()
    return out, repairs


def shattered_runs(text: str) -> list[str]:
    """Letter-spaced runs in ``text`` — one word broken into fragments."""
    out: list[str] = []
    for match in _SHORT_RUN.finditer(text or ""):
        run = match.group(0)
        pieces = run.split()
        non_words = [p for p in pieces if p not in _REAL_SHORT_WORDS]
        if len(non_words) >= 3:
            out.append(" ".join(pieces))
    return out


def problems(text: str) -> list[str]:
    """Unrecoverable damage in ``text``, one message per kind."""
    found: list[str] = []
    t = text or ""

    if _BAD_OPERATOR.search(t):
        found.append(
            "a comparison operator is corrupt or missing (e.g. \"(P 5 0.001)\" "
            "for \"(P < 0.001)\", or \"= = r 0.15\") — the quote states "
            "something the paper does not, and < and > are equally plausible "
            "readings so there is no safe repair")
    runs = shattered_runs(t)
    if runs:
        found.append(
            f"a word is broken into letter-spaced fragments ({runs[0]!r}) — "
            "closing the gaps needs the word, so there is no single reading; "
            "dump page.chars across the span to see whether the parser's space "
            "tolerance is the cause")
    fused = sorted({m.group(0) for m in _TOKEN.finditer(t)
                    if m.group(0).lower() in _FUSED_TOKENS})
    if fused:
        found.append(
            f"words are fused with no space: {', '.join(fused[:4])} "
            "— the PDF text layer dropped an inter-word space; check the parser's "
            "space tolerance before treating the block as unusable")
    if _looks_spliced(t):
        found.append(
            "the sentence ends in a dangling phrase that repeats earlier "
            "content, which is the signature of two text columns interleaved — "
            "the result is not a sentence the paper contains")
    truncated = _TRUNCATED_CLAUSE.search(t)
    if truncated:
        found.append(
            f"the sentence breaks off at {truncated.group(0)!r} — the extractor "
            "dropped what followed, so the quote is not what the paper says and "
            "the missing span cannot be recovered from the text layer")
    mid = [m.group(1) for m in _GUTTER_MIDCLAUSE.finditer(t)]
    starts = [m.group(1) for m in _GUTTER_SENTENCE_START.finditer(t)]
    spliced_numbers = mid + starts if (mid or len(starts) >= 2) else []
    if spliced_numbers:
        found.append(
            f"stray integers are interleaved with the text "
            f"({', '.join(spliced_numbers[:4])}) — the signature of a manuscript "
            "line-number gutter read as content; the quote gains numbers the "
            "sentence never had, next to real statistics")
    if _MANUSCRIPT_FURNITURE.search(t):
        found.append(
            "the text contains submission-template furniture (e.g. a word-count "
            "limit), which is part of the manuscript file and not part of what "
            "the paper reports")
    if _PUBLICATION_TIMELINE.search(t):
        found.append(
            "the text contains journal publication-history furniture (received, "
            "accepted, or published date), not a scientific result")
    if _LEADING_LINE_NUMBER.search(t):
        found.append(
            "a manuscript line number is attached to the start of the quote")
    if _KNOWN_SPLIT_WORDS.search(t):
        found.append(
            "a known word is split by extraction damage (for example, "
            "'m embrane'); re-anchor from a clean text layer or OCR")
    corrupt = sorted(set(_KNOWN_CORRUPT_TOKENS.findall(t)))
    if corrupt:
        found.append(
            "known corrupted extraction token(s): " + ", ".join(corrupt))
    if _SPLIT_EXPONENT.search(t):
        found.append(
            "a scientific exponent is split from its base (e.g. \"1.4x10 -3\") — "
            "as written it reads as a subtraction, and the intended exponent "
            "cannot be distinguished from a genuine minus without the source")
    return found


_TRAILING_FRAGMENT = re.compile(r",\s*([^,.;:]{4,60})\.?\s*$")
_VERBISH = re.compile(
    r"\b(is|are|was|were|be|been|being|has|have|had|do|does|did|show|shows|"
    r"showed|shown|suggest|suggests|indicate|indicates|reduce|reduces|reduced|"
    r"increase|increases|increased|protect|protects|protected|cause|causes|"
    r"caused|lead|leads|led|found|find|observe|observed|report|reports|"
    r"reported|demonstrate|demonstrates|demonstrated|remain|remains|result|"
    r"results|resulted|associate|associated|correlate|correlated)\b", re.I)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def _looks_spliced(text: str) -> bool:
    """A trailing comma-fragment with no verb that echoes earlier content.

    Deliberately narrow. The shipped example is::

        "Our data show that homozygous signatures that are eliminated or even
         reversed with the homozygous R136S mutation fully protects against
         APOE4-driven Tau pathology, APOE4-R136S mutation."

    where "APOE4-R136S mutation" is a fragment from the neighbouring column,
    verbless, and duplicating tokens already in the sentence. A legitimate
    trailing appositive ("...in the cortex, the region most affected") introduces
    NEW content, which is what distinguishes the two. Broader splice detection
    needs a parser, not a regex, so this only claims the case it can prove.
    """
    match = _TRAILING_FRAGMENT.search(text or "")
    if not match:
        return False
    fragment = match.group(1).strip()
    if _VERBISH.search(fragment):
        return False
    fragment_words = {w.lower() for w in _WORD.findall(fragment)}
    if len(fragment_words) < 2:
        return False
    head = (text or "")[: match.start()]
    head_words = {w.lower() for w in _WORD.findall(head)}
    # Hyphenated compounds also count through their parts, so "APOE4-R136S"
    # echoes an earlier bare "APOE4".
    for word in list(fragment_words):
        fragment_words.update(part.lower() for part in word.split("-") if part)
    for word in list(head_words):
        head_words.update(part.lower() for part in word.split("-") if part)
    overlap = fragment_words & head_words
    return len(overlap) >= 2


def inspect(text: str) -> QuoteVerdict:
    """Repair what can be repaired, then report what remains wrong."""
    repaired, repairs = repair(text)
    return QuoteVerdict(text=repaired, repairs=repairs,
                        problems=problems(repaired))
