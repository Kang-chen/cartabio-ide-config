#!/usr/bin/env python3
"""What a quote has to be before it can support a claim, and at what strength.

Two shipped defects, both of which let a claim advertise a support tier its
evidence did not earn.

**1. A hypothesis accepted as a result.** This was recorded as
``supports/primary``:

    "By contrast, if suppression of ApoE4 in astrocytes rescues the BBB defect,
     a gain-of-function mechanism would be supported."

It is a conditional describing what an experiment WOULD show. It asserts nothing,
so it cannot support anything, and no amount of relabelling makes it evidence.
Rejected outright.

**2. Background framing counted as an original finding.** A claim reached
"Convergent (>=2 independent primary studies)" on, among others:

    "Apolipoprotein E4 (APOE4) is the strongest known genetic risk factor for
     late-onset Alzheimer's disease (AD)."

That is the opening line of a paper about *neuronal APOE4 removal in tauopathy
mice*. It is the field's consensus restated as background — true, and not a
result of the paper being quoted. ``is_attributed_quote`` did not catch it
because extraction had dropped the superscript reference, leaving no citation
marker and no reporting verb.

The rule that does catch it: **a primary anchor must either present a result or
come from a section that reports results.** An introduction reports the field; a
Results section, a Discussion, or a figure legend reports the study. This
downgrades some genuinely solid claims to "indirect / background support only" —
and that is the honest state when the actual primary sources (Baker 2006, Cruts
2006 for GRN) were never retrieved. A tier is a claim about what was read, not
about what is true.
"""
from __future__ import annotations

import re
from typing import Iterable

# --- 1. hypotheticals -------------------------------------------------------
#
# Anchored so that a conditional CLAUSE inside a reported result ("levels rose,
# which would be expected if uptake were blocked") is not mistaken for a
# hypothetical finding: the pattern requires the conditional to govern the
# sentence's main assertion.
_HYPOTHETICAL = re.compile(
    r"^\s*(?:by contrast,?\s+|conversely,?\s+|alternatively,?\s+|however,?\s+)?"
    r"(?:if|whether|should|were)\b[^.]{0,200}?"
    r"\b(?:would|could|might|may|should)\s+(?:be|have|show|indicate|suggest|"
    r"support|imply|argue|result|lead|require|reflect)\b",
    re.IGNORECASE)

# A sentence whose main verb is speculative rather than reported.
_SPECULATIVE_MAIN = re.compile(
    r"\b(?:we\s+(?:hypothesi[sz]ed?|propose|speculate|predict|anticipate|expect)"
    r"|it\s+(?:is|remains)\s+(?:possible|conceivable|unclear|unknown)"
    r"|(?:this|these)\s+(?:would|could|might)\s+"
    r"|remains?\s+to\s+be\s+(?:determined|established|shown|tested)"
    r"|future\s+(?:studies|work|experiments)\s+(?:will|should|are))\b",
    re.IGNORECASE)


def is_hypothetical(quote: str) -> bool:
    """True when the sentence describes what would be found, not what was."""
    text = " ".join(str(quote or "").split())
    if not text:
        return False
    return bool(_HYPOTHETICAL.search(text) or _SPECULATIVE_MAIN.search(text))


# --- 2. primary requires a result -------------------------------------------
#
# Sections that report the study's own work. A quote from anywhere else needs an
# explicit result marker before it may be called primary.
_RESULT_SECTIONS = (
    "result", "finding", "discussion", "conclusion", "figure", "table",
    "supplementary", "extended data", "primary endpoint", "secondary endpoint",
    "efficacy", "safety", "outcome", "non-clinical", "nonclinical",
)

# Sections that report the FIELD rather than the study.
_BACKGROUND_SECTIONS = (
    "introduction", "background", "front matter", "objective", "rationale",
    "significance", "importance",
)

# First-person and result-presenting markers. Their presence means the sentence
# is offering the paper's own observation, wherever it sits in the document.
_RESULT_MARKER = re.compile(
    # "describe/present/document/characterise" belong here with "show" and
    # "found". An observational study states its own result with them — "We
    # describe in vivo follow-up PET imaging and postmortem findings from an
    # ADAD PSEN1 E280A carrier ... protected against Alzheimer's symptoms" is
    # the paper's central finding, not a summary of someone else's. Their
    # absence demoted a primary human case report to background support in a
    # delivered review that then called it, in its own words, the strongest
    # possible proof-of-concept.
    r"\b(?:we\s+(?:show|showed|find|found|observe|observed|report|reported|"
    r"demonstrate|demonstrated|identify|identified|detect|detected|measured|"
    r"quantified|note|noted|confirm|confirmed|conclude|concluded|failed|"
    r"describe|described|present|presented|document|documented|"
    r"characterize|characterized|characterise|characterised)"
    r"|our\s+(?:data|results|findings|analysis|study|experiments|observations)"
    r"|here\s+we\b"
    r"|(?:this|the\s+present)\s+study\s+(?:shows|showed|provides|provided|"
    r"found|identifies|identified|demonstrates|demonstrated)"
    r"|(?:these|the)\s+(?:data|results|findings)\s+(?:show|showed|demonstrate|"
    r"demonstrated|indicate|indicated|suggest|suggested|support|supported|"
    r"reveal|revealed|confirm|confirmed)"
    r"|in\s+(?:this|the\s+present)\s+study\b"
    r"|(?:was|were)\s+(?:significantly|markedly|modestly)\b"
    r"|\bp\s*[<>=]\s*0?\.\d"
    r"|\bn\s*=\s*\d)",
    re.IGNORECASE)

# A measured group plus a direction of effect. This is how an abstract states the
# paper's OWN result without saying "we": "All FTLD patients with GRN
# loss-of-function mutations showed significantly reduced levels of GRN in
# plasma". A reporting verb alone is not enough (an introduction says "mutations
# lead to reduced levels"); the verb has to be one used of an observed cohort or
# sample, and it has to carry a magnitude or direction.
_OBSERVED_EFFECT = re.compile(
    r"\b(?:showed|shows|show|exhibited|exhibits|exhibit|displayed|displays|"
    r"display|revealed|reveals|reveal|had|have|developed|develops|develop|"
    r"accumulated|accumulates|accumulate|carried|carry|reached|reach)\b"
    r"(?:\W+\w+){0,4}?\W+"
    r"(?:significantly|markedly|modestly|substantially|reduced|increased|"
    r"elevated|decreased|lower|higher|greater|fewer|more|less|no\s+change|"
    # A case report's outcome is qualitative. "Protected against symptoms for
    # almost three decades" is as much an observed effect as a fold-change, and
    # requiring a magnitude made n=1 human genetics unrepresentable.
    r"protected|resistant|spared|unaffected|free\s+of|normal|intact)\b",
    re.IGNORECASE)

# The passive voice a paper uses to report its own measurement: "Similar low
# levels of GRN were identified in asymptomatic carriers", "inclusions were
# detected in the pons". A background sentence does not describe an act of
# measurement, it states a fact.
_PASSIVE_OBSERVATION = re.compile(
    r"\b(?:was|were|is|are)\s+(?:also\s+|further\s+|not\s+|significantly\s+|"
    r"markedly\s+)?"
    r"(?:identified|observed|detected|found|measured|quantified|seen|noted|"
    r"confirmed|recorded|assessed|reduced|increased|elevated|decreased|"
    r"enriched|depleted|abolished|rescued|restored|attenuated|impaired)\b",
    re.IGNORECASE)


def _section_matches(section: str, needles: tuple[str, ...]) -> bool:
    lowered = str(section or "").lower()
    return any(needle in lowered for needle in needles)


def may_be_primary(quote: str, section: str, block_type: str = "sentence") -> bool:
    """Can this quote legitimately be labelled ``primary``?

    A figure caption or in-panel text always can: a legend states the paper's own
    result as plainly as a Results sentence ("Latozinemab decreases sortilin in
    WBCs and increases PGRN levels..."), which is why captions are not held to
    the marker rule.
    """
    if block_type in {"caption", "figure_ocr", "table"}:
        return True
    text = " ".join(str(quote or "").split())
    if (_RESULT_MARKER.search(text) or _OBSERVED_EFFECT.search(text)
            or _PASSIVE_OBSERVATION.search(text)):
        return True
    if _section_matches(section, _RESULT_SECTIONS):
        return True
    if _section_matches(section, _BACKGROUND_SECTIONS):
        return False
    # An abstract carries both: its opening sentences frame the field, its later
    # ones report the study. With no result marker and no way to tell which half
    # this is, the weaker reading is the honest one.
    if "abstract" in str(section or "").lower():
        return False
    # Any other section (Methods, or a free-text heading) reports no result on
    # its own either.
    return False


# Named models, constructs and variants. These are PRECISE identifiers — a
# scope that names one is asserting that specific system was studied, and unlike
# a general phrase ("Humans; mutation carriers") there is no paraphrase under
# which the evidence could describe it differently.
#
# The defect: a claim's scope read "Mouse tauopathy models (P301S), humanized
# APOE" while no P301S study had been retrieved — Shi 2017 was paywalled — and
# its anchors were all targeted-replacement mice. The narrative facets said so
# honestly, but the scope field described evidence the review did not have, and a
# reader skimming claim headings and tiers would not know.
_NAMED_MODEL = re.compile(
    r"\b("
    r"P301[SL]|PS19|rTg4510|Tg2576|5x?FAD|3xTg|APP/PS(?:EN)?1|APPswe|J20|"
    r"hTDP-?43|TDP-?43[A-Z]?\d{2,4}[A-Z]|Q331K|M337V|A315T|"
    r"R136S|E280A|Christchurch|"
    r"Grn-?(?:KO|/-)|Grn\s*-/-|PGRN-?KO|APOE[234]-?TR|"
    r"LX100[0-9]|PR00[0-9]|PBFT0[0-9]|AL00[0-9]|AAVrh\.?10|AAV[0-9]"
    r")\b", re.IGNORECASE)


def named_models(text: str) -> set[str]:
    """Named models, constructs or clinical candidates mentioned in ``text``."""
    return {m.group(1).upper().replace(" ", "")
            for m in _NAMED_MODEL.finditer(str(text or ""))}


def scope_overreach(scope: str, anchor_texts: Iterable[str]) -> set[str]:
    """Named systems the scope claims that no anchor mentions.

    Only NAMED identifiers are checked. A scope legitimately generalises its
    anchors — "Humans; heterozygous GRN mutation carriers" over a quote saying
    "FTLD patients with GRN loss-of-function mutations" — so comparing ordinary
    vocabulary would fire constantly. "P301S" admits no such paraphrase.
    """
    claimed = named_models(scope)
    if not claimed:
        return set()
    present: set[str] = set()
    for text in anchor_texts:
        present |= named_models(text)
    return claimed - present


def primary_downgrade_reason(quote: str, section: str,
                             block_type: str = "sentence") -> str:
    """Why ``may_be_primary`` said no, phrased for the run's audit trail."""
    if may_be_primary(quote, section, block_type):
        return ""
    where = str(section or "an unlabelled section")
    if _section_matches(section, _BACKGROUND_SECTIONS):
        return (f"quote is from {where}, which reports the field rather than "
                "this paper's own work, and it presents no result of its own")
    if "abstract" in where.lower():
        return (f"quote is from {where} and presents no result marker, so it "
                "cannot be distinguished from the abstract's background framing")
    return (f"quote is from {where} and presents no result of this paper "
            "(no first-person finding, no statistic, no results section)")
