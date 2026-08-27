#!/usr/bin/env python3
"""The single source of truth for support-state policy.

Every builder, verifier, statistic, and chart imports from here. This module
deliberately has no local imports so nothing can create a cycle by depending
on it.

Why it exists: the definition of "grounded" was written out by hand in six
places and three of them disagreed. A shipped report announced

    Grounded claims: 17/18

and then, three paragraphs later, explained that the eighteenth claim was
"a legitimately refuted finding grounded by its contradicting quotes" — i.e.
grounded. Both statements came from the same run. The gates had been corrected
to treat only C_INSUFFICIENT as ungrounded while the *counters* still excluded
C_REFUTED, so the headline number and the prose were computed under different
policies.

The rule, stated once:

    A claim is GROUNDED iff it carries at least one qualifying supporting OR
    contradicting quote. Only C_INSUFFICIENT — no qualifying quote of either
    kind — is ungrounded.

C_REFUTED is grounded. Its anchors are the contradicting quotes, and surfacing
them is the entire point of a contradiction hunt.
"""
from __future__ import annotations

# --- the states ------------------------------------------------------------

C2_CONVERGENT = "C2_CONVERGENT"
C1_SINGLE_DIRECT = "C1_SINGLE_DIRECT"
C1_INDIRECT = "C1_INDIRECT"
C_CONFLICTED = "C_CONFLICTED"
C_REFUTED = "C_REFUTED"
C_INSUFFICIENT = "C_INSUFFICIENT"

ALL_STATES = frozenset({
    C2_CONVERGENT, C1_SINGLE_DIRECT, C1_INDIRECT,
    C_CONFLICTED, C_REFUTED, C_INSUFFICIENT,
})

# The one definition. Do not inline a set literal anywhere else.
UNGROUNDED_STATES = frozenset({C_INSUFFICIENT})
GROUNDED_STATES = ALL_STATES - UNGROUNDED_STATES

# States whose wording must stay hedged — no categorical or causal language.
# C_CONFLICTED belongs here: a claim with live contradicting evidence is the
# one most in need of hedging, and it was previously omitted.
WEAK_STATES = frozenset({
    C_INSUFFICIENT, C_REFUTED, C_CONFLICTED, C1_INDIRECT,
})

# Ordered by STRENGTH OF SUPPORT, weakest first. C_REFUTED sits near the bottom
# because it is not support: "strongest support" cells are selected with this
# ordering, and ranking it above C1_SINGLE_DIRECT let an axis advertise
# "Refuted by evidence" as its strongest support.
SUPPORT_ORDER = (
    C_INSUFFICIENT,
    C_REFUTED,
    C1_INDIRECT,
    C_CONFLICTED,
    C1_SINGLE_DIRECT,
    C2_CONVERGENT,
)

SUPPORT_LABEL = {
    C2_CONVERGENT: "Convergent (≥2 independent primary studies)",
    C1_SINGLE_DIRECT: "One primary study",
    C1_INDIRECT: "Indirect / background support only",
    C_CONFLICTED: "Conflicted (support and contradiction)",
    C_REFUTED: "Refuted by evidence",
    C_INSUFFICIENT: "Insufficient evidence",
}

SUPPORT_COLOR = {
    C2_CONVERGENT: "#4C8C2B",
    C1_SINGLE_DIRECT: "#D9A03C",
    C1_INDIRECT: "#1F77D0",
    C_CONFLICTED: "#8C6BB1",
    C_REFUTED: "#E377C2",
    C_INSUFFICIENT: "#9E9E9E",
}


# --- the predicates every consumer must use --------------------------------

def is_grounded(state: str | None) -> bool:
    """True iff the claim carries a qualifying supporting or contradicting quote."""
    return str(state or C_INSUFFICIENT) not in UNGROUNDED_STATES


def count_grounded(states) -> int:
    """Number of grounded claims in an iterable of support states."""
    return sum(1 for s in states if is_grounded(s))


def strongest(states) -> str:
    """The strongest SUPPORT tier present, by SUPPORT_ORDER.

    Not "the most notable state" — a refutation is deliberately ranked low.
    """
    known = [s for s in states if s in SUPPORT_ORDER]
    if not known:
        return C_INSUFFICIENT
    return max(known, key=SUPPORT_ORDER.index)


def label(state: str | None) -> str:
    return SUPPORT_LABEL.get(str(state or ""), str(state or C_INSUFFICIENT))
