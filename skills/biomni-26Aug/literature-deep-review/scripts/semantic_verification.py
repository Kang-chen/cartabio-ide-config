#!/usr/bin/env python3
"""Semantic-entailment contract: does the quoted sentence ENTAIL the claim as worded?

WHY THIS MODULE EXISTS
----------------------
Every gate upstream of this one proves a quote EXISTS. ``validate_adjudication``
proves the quote is a substring of the cited block; ``verify_review`` re-derives
that against the canonical block store; ``verify_pdf_quotes`` proves the sentence
survived verbatim into the rendered PDF. Not one of them can tell whether the
sentence MEANS what the claim says.

The shipped defect. A delivered report grounded

    C-007  "Progranulin deficiency drives microglial dysfunction and
            neuroinflammation."

on

    "AAV-expressed progranulin was only detected in neurons, not in microglia,
     indicating that the microglial activation in progranulin deficiency can be
     improved by targeting neurons and thus may be driven at least in part by
     neuronal dysfunction."

Every deterministic check passed, because every deterministic check was true:
the sentence is verbatim, in the cited block, in the cited paper, in the PDF.
It is still the wrong anchor, in four separable ways:

* **outcome** — the sentence never mentions neuroinflammation;
* **intervention** — it reports a RESCUE experiment (AAV-delivered progranulin),
  not the deficiency the claim is about;
* **direction** — it concludes the microglial phenotype is DOWNSTREAM of neuronal
  dysfunction, which argues against "progranulin deficiency drives microglial
  dysfunction" as worded;
* **scope** — the claim is broader than a single AAV-rescue observation supports.

And the same sentence was reused to ground a second, different claim: one
overstretched anchor doing the work of two.

Those four axes are exactly the fields of the verdict record below. A substring
check cannot produce any of them; a reader has to. So this module defines the
per-anchor record a reviewer produces and the deterministic rules that turn a
set of those records into a claim-level status.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It never calls a model. Producing verdicts is the operator's or adjudicator's
job; defining what a verdict is, and checking a set of them, is this module's.
Nothing here reads the clock either — ``verified_at`` is supplied by the caller,
so every function is a pure function of its arguments and the same inputs always
produce the same ``verdict_id``.

THE SECOND PASS MUST BE BLIND
-----------------------------
``blinded_payload`` builds the reviewer's prompt payload from a whitelist: the
claim text, its scope, and the quote. It carries none of the first pass's output
— no ``stance``, no ``evidence_kind``, no ``support_state``, no adjudicator
``rationale`` or ``scope_note``. A reviewer shown "stance: supports, kind:
primary" is not re-deriving entailment, it is ratifying a label, and a ratifying
second pass would have waved C-007 straight through.
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import support_policy  # noqa: E402

# --- the vocabulary --------------------------------------------------------

ENTAILMENT_YES = "yes"
ENTAILMENT_PARTIAL = "partial"
ENTAILMENT_NO = "no"
ENTAILMENT_VALUES = frozenset({ENTAILMENT_YES, ENTAILMENT_PARTIAL, ENTAILMENT_NO})

# Is the quoted statement THIS paper's own result, a result it attributes to
# somebody else, or background framing? Recorded on every verdict because
# "the paper says X" and "the paper says someone found X" ground a claim very
# differently, and the distinction is invisible once the sentence is in quotes.
RESULT_ORIGINAL = "original"
RESULT_CITED = "cited"
RESULT_BACKGROUND = "background"
RESULT_TYPES = frozenset({RESULT_ORIGINAL, RESULT_CITED, RESULT_BACKGROUND})

# The four axes C-007 failed on, in the order a reviewer should work through
# them. All four must hold before a `yes` entailment may carry a claim.
MATCH_FLAGS = (
    "direction_match",      # quote's direction of effect == the claim's
    "population_match",     # species / model / population, as scoped
    "intervention_match",   # what was done (deficiency vs rescue vs observation)
    "outcome_match",        # what was measured (microgliosis != neuroinflammation)
)

# Every field of a verdict record, in a stable order.
VERDICT_FIELDS = (
    "verdict_id",
    "claim_id",
    "evidence_id",
    "entailment",
    *MATCH_FLAGS,
    "result_type",
    "scope_overreach",
    "reviewer",
    "rationale",
    "verified_at",
)

# --- claim-level status ----------------------------------------------------

STATUS_VERIFIED = "verified"
STATUS_DISPUTED = "disputed"
STATUS_UNVERIFIED = "unverified"
CLAIM_STATUSES = frozenset({STATUS_VERIFIED, STATUS_DISPUTED, STATUS_UNVERIFIED})

# --- which claims must carry verdicts --------------------------------------
#
# A CENTRAL claim is one whose support state asserts direct primary evidence:
# the claims a reader takes as findings. They are the ones an unentailed anchor
# damages most, so they are the ones the entailment gate is mandatory for.
# The state strings are imported, never retyped — six modules each hand-writing
# a support-state set is how the definitions drifted apart before (see
# support_policy's docstring).
CENTRAL_SUPPORT_STATES = frozenset({
    support_policy.C2_CONVERGENT,
    support_policy.C1_SINGLE_DIRECT,
})

# --- blinding --------------------------------------------------------------

# The ONLY keys `blinded_payload` may emit. A whitelist, not a blacklist: a new
# field added to the evidence row upstream cannot leak into the second pass by
# default.
BLINDED_PAYLOAD_KEYS = ("claim_id", "claim_text", "scope", "evidence_id", "quote")

# First-pass output. If any of these reaches the reviewer, the second pass is
# no longer independent — it is being told the answer. `scope_note` is on this
# list even though it looks like scope: it is the ADJUDICATOR's scoping
# sentence, written by the pass being audited. The claim's own `scope` (from
# claims.jsonl) is what the reviewer legitimately needs.
FIRST_PASS_FIELDS = frozenset({
    "stance",
    "evidence_kind",
    "evidence_kind_relabeled_from",
    "support_state",
    "rationale",
    "scope_note",
    "quote_match",
    "needs_figure_review",
    "audit_status",
    "adjudication_backend",
    "adjudication_model",
    "verified",
})


# --- record construction ---------------------------------------------------

def verdict_id(claim_id: str, evidence_id: str, reviewer: str) -> str:
    """Stable ID for one reviewer's verdict on one (claim, anchor) pair.

    Deterministic by construction — re-running the same review of the same
    anchor overwrites rather than accumulates, and two reviewers of the same
    anchor get distinct IDs so disagreement is representable.
    """
    digest = hashlib.sha1(
        "|".join((str(claim_id), str(evidence_id), str(reviewer))).encode()
    ).hexdigest()[:16]
    return f"V-{digest}"


def new_verdict(
    *,
    claim_id: str,
    evidence_id: str,
    entailment: str,
    reviewer: str,
    rationale: str = "",
    direction_match: bool = False,
    population_match: bool = False,
    intervention_match: bool = False,
    outcome_match: bool = False,
    result_type: str = RESULT_BACKGROUND,
    scope_overreach: bool = False,
    verified_at: str = "",
) -> dict:
    """Build a verdict record. Pure: no clock, no IDs from anywhere but the args.

    The match flags and ``result_type`` default to the CONSERVATIVE value — an
    unfilled verdict asserts nothing and cannot carry a claim.
    """
    return {
        "verdict_id": verdict_id(claim_id, evidence_id, reviewer),
        "claim_id": str(claim_id),
        "evidence_id": str(evidence_id),
        "entailment": str(entailment),
        "direction_match": bool(direction_match),
        "population_match": bool(population_match),
        "intervention_match": bool(intervention_match),
        "outcome_match": bool(outcome_match),
        "result_type": str(result_type),
        "scope_overreach": bool(scope_overreach),
        "reviewer": str(reviewer),
        "rationale": str(rationale),
        "verified_at": str(verified_at),
    }


def validate_verdict(verdict: dict) -> list[str]:
    """Structural + coherence errors in one verdict record; [] when it is sound.

    Booleans must be real JSON booleans. The string ``"false"`` is truthy in
    Python, so accepting strings here would silently turn a reviewer's "no" into
    a "yes" — the precise class of error this module exists to stop.

    Coherence: ``entailment: yes`` requires every match axis and forbids scope
    overreach. ``partial`` is retained as an audit outcome but never carries a
    displayed claim; if all axes match and scope does not overreach, the reviewer
    must choose ``yes`` rather than an internally unexplained ``partial``.
    """
    errors: list[str] = []
    if not isinstance(verdict, dict):
        return [f"verdict is not an object: {verdict!r}"]

    for field in ("claim_id", "evidence_id", "reviewer"):
        if not str(verdict.get(field) or "").strip():
            errors.append(f"{field} is empty")

    entailment = verdict.get("entailment")
    if entailment not in ENTAILMENT_VALUES:
        errors.append(
            f"entailment={entailment!r} is not one of {sorted(ENTAILMENT_VALUES)}"
        )

    result_type = verdict.get("result_type")
    if result_type not in RESULT_TYPES:
        errors.append(
            f"result_type={result_type!r} is not one of {sorted(RESULT_TYPES)}"
        )

    for flag in (*MATCH_FLAGS, "scope_overreach"):
        value = verdict.get(flag)
        if not isinstance(value, bool):
            errors.append(
                f"{flag}={value!r} must be a JSON boolean, not "
                f"{type(value).__name__} (a non-empty string is always truthy, "
                "so a stringly-typed 'false' would read as true)"
            )

    if entailment == ENTAILMENT_YES and verdict.get("scope_overreach") is True:
        errors.append(
            "incoherent verdict: entailment='yes' asserts the quote entails the "
            "claim while scope_overreach=true asserts the claim is broader than "
            "the quote supports — pick one (a claim stretched past its quote is "
            "'partial' at best)"
        )
    if entailment == ENTAILMENT_YES:
        for flag in MATCH_FLAGS:
            if verdict.get(flag) is False:
                errors.append(
                    f"incoherent verdict: entailment='yes' requires {flag}=true"
                )
    if (
        entailment == ENTAILMENT_PARTIAL
        and verdict.get("scope_overreach") is False
        and all(verdict.get(flag) is True for flag in MATCH_FLAGS)
    ):
        errors.append(
            "incoherent verdict: entailment='partial' has every match axis true "
            "and no scope overreach; choose 'yes' or record the mismatch"
        )
    return errors


# --- the deterministic rules -----------------------------------------------

def verdict_is_acceptable(verdict: dict) -> bool:
    """May this anchor support its claim?

    The rule, in full:

        entailment == 'yes'
        AND NOT scope_overreach
        AND all four match flags are true

    Anything else — a 'no', a 'partial' with any mismatch, an overreaching
    'partial', an unrecognised value — cannot carry the claim. The C-007 anchor
    is a 'no' with three of four flags false and scope_overreach true; it fails
    on every limb at once, which is what makes it a good permanent fixture.
    """
    if not isinstance(verdict, dict):
        return False
    if verdict.get("entailment") != ENTAILMENT_YES:
        return False
    if verdict.get("scope_overreach") is True:
        return False
    return all(verdict.get(flag) is True for flag in MATCH_FLAGS)


def unacceptable_reason(verdict: dict) -> str:
    """Short human explanation of why a verdict cannot carry its claim ('' if it can).

    The gate quotes this back to the operator: "no verdict was acceptable" is
    unactionable, "direction and outcome do not match, and the claim overreaches
    the quote" tells them whether to re-anchor or reword the claim.
    """
    if verdict_is_acceptable(verdict):
        return ""
    if not isinstance(verdict, dict):
        return "verdict is not an object"
    entailment = verdict.get("entailment")
    if entailment == ENTAILMENT_NO:
        base = "the quote does not entail the claim (entailment=no)"
    elif entailment == ENTAILMENT_PARTIAL:
        base = "partial entailment"
    elif entailment == ENTAILMENT_YES:
        base = "the yes verdict is internally inconsistent"
    else:
        return f"unrecognised entailment value {entailment!r}"
    missing = [flag for flag in MATCH_FLAGS if verdict.get(flag) is not True]
    parts = []
    if missing:
        parts.append("mismatched: " + ", ".join(f.replace("_match", "") for f in missing))
    if verdict.get("scope_overreach") is True:
        parts.append("claim is broader than the quote supports")
    return base + (" — " + "; ".join(parts) if parts else "")


def claim_status(verdicts) -> str:
    """Aggregate one claim's anchor verdicts into ``verified``/``disputed``/``unverified``.

        verified   — at least one acceptable verdict and no unacceptable one
        disputed   — acceptable and unacceptable verdicts both present
        unverified — no acceptable verdict at all (an empty set included)

    ``disputed`` is a distinct state on purpose. A claim resting on one sound
    anchor plus one C-007-style anchor is not simply "verified": the bad anchor
    is still printed under the claim, still cited, and still reused elsewhere.
    Collapsing it into ``verified`` would hide exactly the row an editor needs
    to delete.
    """
    items = [v for v in (verdicts or [])]
    if not items:
        return STATUS_UNVERIFIED
    acceptable = [v for v in items if verdict_is_acceptable(v)]
    if not acceptable:
        return STATUS_UNVERIFIED
    if len(acceptable) == len(items):
        return STATUS_VERIFIED
    return STATUS_DISPUTED


# --- the blinded reviewer payload ------------------------------------------

def blinded_payload(claim: dict, anchor: dict) -> dict:
    """The prompt payload for a second-pass entailment reviewer.

    Contains the claim as worded, the scope it is asserted under, and the quote
    — and nothing else. Built from ``BLINDED_PAYLOAD_KEYS`` by whitelist, so no
    field added to an evidence row upstream can leak the first pass's judgment
    into the audit of that judgment.

    ``claim_id`` and ``evidence_id`` travel with the payload only as routing
    keys, so the returned verdict can be attached to the right anchor. They
    carry no judgment: nothing in an ID says whether the adjudicator thought the
    quote supported the claim.
    """
    claim = claim or {}
    anchor = anchor or {}
    return {
        "claim_id": str(claim.get("claim_id") or anchor.get("claim_id") or ""),
        "claim_text": str(claim.get("claim_text") or ""),
        # claims.jsonl calls it `scope`; the synthesis matrix calls the same
        # thing `claim_scope`. Never `scope_note` — that is the adjudicator's.
        "scope": str(claim.get("scope") or claim.get("claim_scope") or ""),
        "evidence_id": str(anchor.get("evidence_id") or ""),
        "quote": str(anchor.get("quote") or ""),
    }
