#!/usr/bin/env python3
"""Whether a paper's figures may be REPRODUCED, which is not the same question
as whether the paper may be READ.

The report may embed cropped figures from cited papers. This module classifies
the recorded licence separately from the user's report-level inclusion choice;
it never treats accessibility or user direction as proof of open licensing. Two
ways those concepts can otherwise be conflated:

* A `free_to_read` author manuscript is served free by PMC under a publisher
  deposit agreement. Readable, definitely not licensed for figure reuse.
* CC BY-ND permits redistribution but forbids derivative works, and a crop of a
  multi-panel figure is plausibly a derivative.

Retrieval rights and recorded reuse rights are independent. This module derives
the second from the licence string Europe PMC reports and defaults to "no" when
it does not recognise one. ``export_figures.py`` then applies the separately
recorded policy: reuse-cleared only, or explicit user-directed inclusion with a
visible rights notice.

Nothing here is legal advice. ``figure_embedding_allowed`` means the metadata
itself clears reuse; false does not prohibit a user-directed inclusion, but that
path must remain labeled and auditable.
"""
from __future__ import annotations

import re

# Reuse verdicts.
REUSE_FULL = "full"                  # attribution only (CC0/CC BY/CC BY-SA)
REUSE_NONCOMMERCIAL = "noncommercial"  # NC variants
REUSE_NO_DERIVATIVES = "no_derivatives"  # ND variants — a crop is a derivative
REUSE_NONE = "none"                  # no licence evidence; assume all rights reserved

# Ordered most-specific first: "cc by-nc-nd" must not match the "cc by" rule.
_LICENSE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"cc[\s._-]*by[\s._-]*nc[\s._-]*nd", re.I), REUSE_NO_DERIVATIVES),
    (re.compile(r"cc[\s._-]*by[\s._-]*nd", re.I), REUSE_NO_DERIVATIVES),
    (re.compile(r"cc[\s._-]*by[\s._-]*nc[\s._-]*sa", re.I), REUSE_NONCOMMERCIAL),
    (re.compile(r"cc[\s._-]*by[\s._-]*nc", re.I), REUSE_NONCOMMERCIAL),
    (re.compile(r"cc[\s._-]*by[\s._-]*sa", re.I), REUSE_FULL),
    (re.compile(r"cc[\s._-]*by\b", re.I), REUSE_FULL),
    (re.compile(r"\bcc[\s._-]*0\b|public[\s._-]*domain", re.I), REUSE_FULL),
)

# Verdicts under which a crop may be embedded. NC is excluded deliberately: the
# report is produced for commercial drug-discovery decisions, so the
# non-commercial condition is not satisfied. Flip this only with legal input.
_EMBEDDABLE = frozenset({REUSE_FULL})


def classify_license(license_text: str | None) -> str:
    """Map a licence string to a reuse verdict. Unrecognised -> REUSE_NONE."""
    text = (license_text or "").strip()
    if not text:
        return REUSE_NONE
    for pattern, verdict in _LICENSE_RULES:
        if pattern.search(text):
            return verdict
    return REUSE_NONE


def figure_embedding_allowed(license_text: str | None,
                             access_state: str | None = None) -> bool:
    """May a crop of this paper's figures be embedded in the report?

    Requires an affirmative reuse licence. ``access_state`` alone never grants
    it — `free_to_read` means the text was retrieved without authentication,
    not that the publisher licensed redistribution of the figures.
    """
    return classify_license(license_text) in _EMBEDDABLE


def rights_record(license_text: str | None,
                  access_state: str | None = None) -> dict[str, object]:
    """The three fields stamped on every paper record, plus the reason.

    The reason is stored so a report can say *why* a figure was omitted rather
    than silently showing fewer figures — which is indistinguishable from the
    crop-extraction failures this skill already has to explain.
    """
    verdict = classify_license(license_text)
    allowed = verdict in _EMBEDDABLE
    if allowed:
        reason = f"licence {license_text!r} permits reuse with attribution"
    elif verdict == REUSE_NONE:
        reason = (
            "no reuse licence recorded"
            + (f" (access_state={access_state})" if access_state else "")
            + " — free to read does not imply licensed to reproduce figures"
        )
    elif verdict == REUSE_NONCOMMERCIAL:
        reason = (f"licence {license_text!r} is non-commercial only; this report "
                  "is produced for commercial decision-making")
    else:
        reason = (f"licence {license_text!r} forbids derivative works and a crop "
                  "of a published figure is a derivative")
    return {
        "license": license_text or "",
        "reuse_rights": verdict,
        "figure_embedding_allowed": allowed,
        "figure_embedding_reason": reason,
    }
