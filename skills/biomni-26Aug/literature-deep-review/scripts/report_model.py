#!/usr/bin/env python3
"""Assemble the canonical report model that BOTH deliverables render.

``review.md`` and ``review.pdf`` used to be written independently, by hand, once
per run. That is why they drifted: the same run could produce a Markdown file
with five figures and a PDF with one, or a PDF with an evidence-axis table that
the Markdown never had. This module is the single assembly step; the renderers
are dumb.

Everything here is derived from canonical artifacts:

    corpus/claims.jsonl                          claim text, scope, cluster
    evidence/evidence.jsonl                      accepted evidence rows
    deliverables/grounded_quotes.json            the verbatim anchors
    deliverables/figures_cited/                  exported paper-figure crops
    corpus/references.jsonl                      reference metadata
    deliverables/claim_narratives.jsonl          per-claim scientific narrative
    deliverables/report_sections.json            the authored prose sections
    run_manifest.json / review_stats.json        run configuration and counts

Nothing is invented, and no count is passed in by hand: the synthesis table and
the synthesis panel are both recomputed from the evidence here, so the figure
and the prose cannot disagree with the claim list (a defect the older report
shipped — its synthesis chart showed a 2/3 split for an axis whose claims were
actually 1/4).

The last two artifacts are what makes the report read as a scientific document
rather than a quote catalogue. Making the builders deterministic fixed report
DRIFT and cost report QUALITY: the hand-authored report they replaced separated,
for every central claim, the observed result from the authors' interpretation
from the reviewer's own inference from the contradiction from the evidence gap,
and the first deterministic model carried none of those fields. Both properties
are required, so the narrative is data too — structured, evidence-linked, and
validated here rather than improvised per run.

The rule that makes it trustworthy, enforced by ``_statement``:

    every narrative statement must EITHER cite ``evidence_ids`` that exist in
    evidence.jsonl, OR set ``"inference": true`` and be rendered under an
    explicit reviewer-inference label.

A statement with neither is a contract violation and lands in
``narrative_errors``; both builders refuse to render while that list is
non-empty. The point is that a reader can always tell observation from
interpretation from inference. Both artifacts are OPTIONAL — a run without them
renders exactly as it did before they existed.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import Counter, defaultdict
from typing import Any

from evidence_taxonomy import enrich_anchor, support_metadata_by_claim
from report_sections_policy import INLINE_SECTIONS, SECTION_KEYS, SECTION_TITLE
from scientific_semantics import section_classification_errors, statement_semantic_errors

# Support-state policy lives in ONE module. These are re-exports so existing
# importers (synthesis_panel, the gates) keep working — they are not a second
# definition. Six hand-written copies of "which states count as grounded" is
# how a report came to print "17/18 grounded" and then explain that the
# eighteenth was grounded too.
from support_policy import (  # noqa: E402,F401
    ALL_STATES,
    C_CONFLICTED,
    C_REFUTED,
    GROUNDED_STATES,
    SUPPORT_COLOR,
    SUPPORT_LABEL,
    SUPPORT_ORDER,
    UNGROUNDED_STATES,
    WEAK_STATES,
    is_grounded,
    strongest as _strongest_state,
)

# The structural contract lives in ONE file, next to the thresholds the gates
# read. The renderers pull the narrative vocabulary from it (see
# ``narrative_inference_label``) so a label cannot be changed in the contract and
# left unchanged in the output.
DEFAULT_CONTRACT = (
    pathlib.Path(__file__).resolve().parent.parent / "templates" / "report_contract.json"
)

# The five facets of a central claim, in render order, with the heading each one
# is rendered under. Both renderers walk this tuple, so Markdown and PDF cannot
# separate a claim into different parts. ``report_contract.json`` declares the
# same key list and a mismatch is reported as a contract-configuration error —
# see ``_narrative_contract_errors``.
NARRATIVE_FACETS: tuple[tuple[str, str], ...] = (
    ("observed_result", "Observed result"),
    ("authors_interpretation", "Authors' interpretation"),
    ("reviewer_inference", "Reviewer inference"),
    ("contradiction", "Contradiction / alternative explanation"),
    ("evidence_gap", "Evidence gap"),
)
NARRATIVE_FACET_KEYS: tuple[str, ...] = tuple(k for k, _ in NARRATIVE_FACETS)
NARRATIVE_FACET_LABEL: dict[str, str] = dict(NARRATIVE_FACETS)

# Fallback only. The live label comes from the contract via
# ``narrative_inference_label`` so the two deliverables cannot label the same
# statement differently.
INFERENCE_LABEL = "reviewer inference"

# Evidence axes are authored as snake_case identifiers so they can key a dict,
# and the shipped reports printed those identifiers straight into the section
# headings, the synthesis table and the chart's x-axis — where two of them
# collided into "mechanism_biologybiomarker_engagement". The identifier is the
# key; this is what a reader sees. Unknown axes fall back to a de-snaked title,
# so a run inventing its own axis still reads as English.
AXIS_LABEL: dict[str, str] = {
    "genetics_causality": "Genetics & causality",
    "human_genetics": "Human genetics",
    "causal_direction": "Direction of causality",
    # A run is free to invent an axis id, and the de-snaked fallback below then
    # produces things like "Genetic Causal" and "Safety Counter" — grammatical
    # nonsense that reads as machine output in a section heading, a table row and
    # a chart label. These are the ids two shipped runs actually chose.
    "genetic_causal": "Genetic & causal evidence",
    "mechanism": "Mechanism",
    "therapeutic": "Therapeutic strategies",
    "therapeutics": "Therapeutics",
    "safety_counter": "Safety & counter-evidence",
    "biomarkers": "Biomarkers",
    "mechanism_biology": "Mechanism & biology",
    "mech_amyloid": "Mechanism: amyloid",
    "mech_tau_neuroinflammation": "Mechanism: tau & neuroinflammation",
    "mech_lipid_bbb": "Mechanism: lipids & blood-brain barrier",
    "biomarker_engagement": "Biomarkers & target engagement",
    "therapeutics": "Therapeutics",
    "safety_contradictions": "Safety & contradictions",
    "uncategorized": "Uncategorized",
}

# Small words that stay lowercase when de-snaking an unknown axis identifier.
_MINOR_WORDS = {"and", "or", "of", "in", "the", "to", "vs", "for", "by"}

# Axis ids are snake_case, so a run that invents `biomarker_nfl` or
# `modality_aav_preclinical` gets title-cased into "Biomarker Nfl" and "Modality
# Aav Preclinical" — which is how a shipped report came to name its own axes
# after things no biologist writes. The run cannot be stopped from inventing ids
# (that freedom is deliberate), so the de-snaker has to know the shapes these
# take. Casing is the field's, not a rule: NfL, mRNA and pTau are mixed case on
# purpose.
_ACRONYMS = {
    "aav": "AAV", "adc": "ADC", "ad": "AD", "als": "ALS", "apoe": "APOE",
    "aso": "ASO", "bbb": "BBB", "car": "CAR", "cns": "CNS", "crispr": "CRISPR",
    "csf": "CSF", "ctdna": "ctDNA", "dna": "DNA", "ftd": "FTD",
    "ftld": "FTLD", "gwas": "GWAS", "grn": "GRN", "hla": "HLA", "igg": "IgG",
    "il": "IL", "ko": "KO", "mri": "MRI", "mrna": "mRNA", "nfl": "NfL",
    "nhp": "NHP", "pet": "PET", "pgrn": "PGRN", "pd": "PD", "ptau": "pTau",
    "qtl": "QTL", "rna": "RNA", "sirna": "siRNA", "snp": "SNP",
    "sort1": "SORT1", "tau": "tau", "tdp43": "TDP-43", "tdp": "TDP-43",
    "tmem106b": "TMEM106B", "trem2": "TREM2", "wt": "WT",
}


def display_axis(axis: str, overrides: dict[str, str] | None = None) -> str:
    """The reader-facing name of an evidence axis.

    ``overrides`` is the run's own ``axis_labels`` map and wins over everything.
    A run invents its axis ids, and no rule recovers English from a snake_case
    id: "lowering_silencing" wants "Lowering & silencing", "protective_mimicry"
    wants "Protective mimicry", and "mechanism_lysosome" wants "Mechanism:
    lysosome" — coordinate nouns, adjective-noun, and category-specifier are
    indistinguishable from the id alone. De-snaking guesses one shape for all
    three, which is why a delivered report headed its sections "Lowering
    Silencing" and "Structure Lipidation". The run knows what it meant; this
    lets it say so.
    """
    key = str(axis or "").strip()
    supplied = (overrides or {}).get(key)
    if supplied and str(supplied).strip():
        return str(supplied).strip()
    if key in AXIS_LABEL:
        return AXIS_LABEL[key]
    words = [w for w in re.split(r"[_\s]+", key) if w]
    if not words:
        return "Uncategorized"
    out: list[str] = []
    for i, word in enumerate(words):
        lowered = word.lower()
        if lowered in _ACRONYMS:
            out.append(_ACRONYMS[lowered])
        elif i and lowered in _MINOR_WORDS:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def read_jsonl(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def read_json(path: pathlib.Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ANCHORED at the start of the caption on purpose. A caption may cross-reference
# another paper's figure ("...the region highlighted in Fig. 2 of the companion
# paper"), and an unanchored search would happily label the crop "Fig. 2" —
# attributing the wrong number to a real published figure. Only a label the
# caption OPENS with is the figure's own.
_FIG_LABEL = re.compile(
    r"^[\s\W]{0,4}(?:Fig(?:ure)?\.?\s*)(\d+[A-Za-z]?)", re.IGNORECASE)


def figure_label(caption: str, figure_id: str, page: Any = None) -> str:
    """The paper's REAL figure label, e.g. "Fig. 3".

    ``figure_id`` is a parser-internal handle (``fig2_p02`` = second detected
    image on page 2) and must never be shown as if it were the paper's own
    numbering. The published label lives at the START of the caption — see
    ``_FIG_LABEL``. When the caption carries no leading label we say so rather
    than emitting a bare "Fig", which is what produced the older report's
    "Nguyen et al. 2018, Fig —", or worse, borrowing a cross-referenced number.

    With no label, the page is the next best handle a reader can act on:
    "Yang et al. 2025, figure on p. 6" sends them somewhere, where
    "figure (unnumbered in caption)" — which shipped twice — only explains the
    parser's difficulty.
    """
    m = _FIG_LABEL.match(caption or "")
    if m:
        return f"Fig. {m.group(1)}"
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        return "unnumbered figure"
    return f"figure on p. {page_number}"


def _author_list(authors: Any) -> list[str]:
    """Split a messy author string into individual names.

    Records arrive in at least three shapes, sometimes within one corpus:
    ``"Cha Yang; Tuancheng Feng; Fenghua Hu"``, ``"Rojas JC; Wang P; Staffaroni
    AM"``, and ``"Elisa Ventura, Giacomo Ducci, Reyes Dominguez-Benot, et al."``.
    Semicolons are unambiguous; commas are not, because ``"Zhang, Jian"`` is one
    author written surname-first. Treat commas as separators only when there are
    at least two of them, which no single ``Surname, Given`` record produces.
    """
    if isinstance(authors, list):
        return [str(a).strip() for a in authors if str(a).strip()]
    raw = str(authors or "").strip()
    if not raw:
        return []
    if ";" in raw:
        parts = raw.split(";")
    elif raw.count(",") >= 2:
        parts = raw.split(",")
    else:
        parts = [raw]
    return [p.strip() for p in parts
            if p.strip() and p.strip().lower().rstrip(".") != "et al"]


def _citation(anchor_or_ref: dict) -> str:
    """Short author-year citation — the form a reader actually recognises.

    "Ward et al. 2024", not "10.1002/trc2.12452". The shipped reports printed
    the bare DOI under every quote (and then repeated it at the end of the same
    line), which is machine provenance standing in for a citation: correct,
    resolvable, and unreadable. The DOI still travels, as the hyperlink target.

    ``et al.`` is used only when it is true. A two-author paper reads
    "Finch and Baker 2009"; a single-author paper is just "Rademakers 2012".
    """
    names = _author_list(anchor_or_ref.get("authors"))
    surnames = [s for s in (_surname(n) for n in names[:2]) if s]
    year = str(anchor_or_ref.get("year") or "").strip()
    if not surnames:
        title = str(anchor_or_ref.get("title") or "")
        first_word = title.split()[0] if title.split() else ""
        return f"{first_word} {year}".strip() if first_word else year
    if len(names) == 1:
        stem = surnames[0]
    elif len(names) == 2 and len(surnames) == 2:
        stem = f"{surnames[0]} and {surnames[1]}"
    else:
        stem = f"{surnames[0]} et al."
    return f"{stem} {year}".strip()


def _surname(name: str) -> str:
    """Best-effort surname from a mixed-format author string.

    Handles "Zhang J" (surname first, then initials), "J. Zhang", and
    "Zhang, Jian". The older pipeline emitted "J et al. 2020" for
    "Zhang J; Velmeshev D; ..." because it took the last whitespace token.
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



def _derive_support_states(evidence: list[dict]) -> dict[str, str]:
    """Support state per claim, computed from the canonical evidence rows.

    Uses the same `support_state` the pipeline uses, so the report cannot
    disagree with the matrix. Imported lazily: `evidence_first` pulls in the
    vendor parsers, and the renderers must stay importable without them.
    """
    from collections import defaultdict as _dd

    from evidence_first import support_state

    by_claim: dict[str, list[dict]] = _dd(list)
    for row in evidence:
        by_claim[row.get("claim_id")].append(row)
    return {cid: support_state(rows) for cid, rows in by_claim.items() if cid}


def resolve_review_mode(root: pathlib.Path) -> str:
    """The run's review mode, checked in every place it has ever been written.

    This is the single source of truth; every consumer must call it rather than
    reaching into the manifest itself. `evidence_first` writes the mode to
    `run_manifest.json["mode"]` and to `review_stats.json["review_mode"]`, but
    NOT to `config.review_mode` — so a consumer that only checks `config`
    silently falls back to its default and holds a `quick` run to `broad`'s
    contract. Two gates disagreeing about the mode of the same run is exactly
    the class of bug the report contract exists to prevent.
    """
    manifest = read_json(root / "run_manifest.json", {}) or {}
    stats = read_json(root / "deliverables" / "review_stats.json", None)
    if stats is None:
        stats = read_json(root / "review_stats.json", {}) or {}
    for value in (
        (manifest.get("config") or {}).get("review_mode"),
        manifest.get("mode"),
        manifest.get("review_mode"),
        (manifest.get("metrics") or {}).get("review_mode"),
        stats.get("review_mode"),
    ):
        if value:
            return str(value).lower()
    return "broad"  # strictest default: never silently relax the contract


# --- the scientific narrative ----------------------------------------------

def load_contract(path: pathlib.Path | str | None = None) -> dict:
    """``templates/report_contract.json`` as a dict, or ``{}`` if unreadable.

    Unreadable is not fatal here: the contract tunes the narrative requirement,
    and a run must still be able to render its evidence when the file is
    missing. Every check that depends on the contract is written so that an
    absent contract cannot silently *weaken* an existing gate — the gates that
    read it (``verify_report_contract``) fail loudly on their own.
    """
    data = read_json(pathlib.Path(path) if path else DEFAULT_CONTRACT, None)
    return data if isinstance(data, dict) else {}


def narrative_inference_label(contract: dict | None) -> str:
    """The label an un-cited statement is rendered under, in BOTH deliverables."""
    spec = (contract or {}).get("narrative") or {}
    return str(spec.get("inference_label") or INFERENCE_LABEL)


# Fallback only; the live value comes from the contract so the renderers and the
# gates that COUNT these labels cannot disagree about the string.
FIGURE_CAPTION_PREFIX = "Report Figure"

# How much of a verbatim source caption is reproduced under an embedded figure,
# in characters, cut at a sentence boundary. Both renderers use this: one
# shipped figure carried a 300-word panel-by-panel legend that filled two thirds
# of the page beneath the crop it was meant to caption. The "source" link
# reaches the unabridged original.
FIGURE_CAPTION_MAX_CHARS = 420


def figure_caption_prefix(contract: dict | None) -> str:
    """What an embedded paper figure is called in its caption.

    **Do not shorten this to "Figure".** It reads better and it breaks the figure
    gate. ``verify_pdf_assets`` counts embedded paper figures by finding
    ``<prefix> N`` in the flattened PDF text, and a report figure's caption
    REPRODUCES the source figure's own label: "Report Figure 1. Tanaka et al.
    2014, Fig. 3 — grounds C-002. Source caption: 'Figure 3 Increased lysosomal
    biogenesis ...'". With the bare prefix that quoted "Figure 3" counts as a
    third report figure, and a two-figure report claims three. Tried, measured,
    reverted — the extra word earns its place.

    Both renderers and both figure gates read this one value.
    """
    spec = (contract or {}).get("paper_figures") or {}
    return str(spec.get("caption_prefix") or FIGURE_CAPTION_PREFIX)


def searched_through(model: dict) -> str:
    """The date the literature was searched, as an ISO day, or "".

    ``rl_config.invariant`` pins the PDF's /CreationDate to a fixed epoch so two
    builds of one run are byte-identical — necessary, but it left the shipped
    documents stamped 2021-12-31 with no other date anywhere in them. A review
    whose bottom line is "as of this review" has to say when that was, and the
    run's own start time is the honest answer.
    """
    manifest = model.get("manifest") or {}
    stats = model.get("stats") or {}
    # Widened after two shipped reports carried NO date at all: neither manifest
    # had `run_started_utc`, so the line rendered empty, and /CreationDate is
    # pinned to a fixed epoch for byte-determinism. A review whose bottom line is
    # time-sensitive has to say when it was done, so every field a run has ever
    # written the timestamp to is checked before giving up.
    candidates = (
        manifest.get("searched_through"),
        manifest.get("run_started_utc"),
        manifest.get("started_utc"),
        manifest.get("created_utc"),
        manifest.get("created_at"),
        manifest.get("timestamp"),
        (manifest.get("config") or {}).get("run_started_utc"),
        (manifest.get("metrics") or {}).get("run_started_utc"),
        stats.get("run_started_utc"),
        stats.get("generated_utc"),
        stats.get("timestamp"),
    )
    for value in candidates:
        text = str(value or "").strip()
        # An ISO-8601 day is the only thing worth printing; a bare integer epoch
        # or a free-text note would read as noise next to the other counters.
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            return text[:10]
    return ""


def section_placeholder(key: str) -> str:
    """What a prose section says when the run supplied no text for it.

    Never an empty section body: a heading with nothing under it reads as an
    oversight in the *evidence*, and the reader cannot tell it apart from a
    section the builder dropped.
    """
    return (f"Not supplied for this run — add {key!r} to "
            "deliverables/report_sections.json.")


def _read_jsonl_reporting(path: pathlib.Path) -> tuple[list[dict], list[str]]:
    """``read_jsonl`` that REPORTS a malformed line instead of skipping it.

    Swallowing a ``JSONDecodeError`` is right for a large machine-written corpus
    file and wrong for a small hand-authored one: one trailing comma would
    silently delete a claim's entire narrative, and the Results section would
    quietly go back to being the quote catalogue this file exists to replace.
    """
    rows: list[dict] = []
    errors: list[str] = []
    for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(
                f"{path.name} line {lineno} is not valid JSON ({exc.msg}) — a "
                "dropped line is a silently missing narrative, so the build "
                "stops instead")
            continue
        if not isinstance(row, dict):
            errors.append(
                f"{path.name} line {lineno} is a {type(row).__name__}, not an "
                "object with a claim_id")
            continue
        rows.append(row)
    return rows, errors


def _snippet(text: str, limit: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _statement(raw: Any, *, where: str, evidence_ids: set[str],
               errors: list[str], evidence_by_id: dict[str, dict] | None = None,
               valid_claim_ids: set[str] | None = None,
               require_claim_alignment: bool = False) -> dict | None:
    """Normalise one narrative statement and record its contract violations.

    Returns ``None`` for "not provided" (absent, or present with empty text) so
    a partially authored narrative renders the facets it does have rather than
    failing. What it does NOT tolerate is an unattributable statement: the whole
    value of separating observation from interpretation from inference is lost
    the moment one sentence can be any of the three.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        errors.append(
            f"{where}: expected an object of the form "
            "{\"text\": ..., \"evidence_ids\": [...]}, got "
            f"{type(raw).__name__}")
        return None
    text = " ".join(str(raw.get("text") or "").split())
    ids = [str(e).strip() for e in (raw.get("evidence_ids") or [])
           if str(e).strip()]
    statement_claim_ids = [
        str(claim_id).strip() for claim_id in (raw.get("claim_ids") or [])
        if str(claim_id).strip()
    ]
    inference = bool(raw.get("inference"))
    if not text:
        return None
    for eid in ids:
        if eid not in evidence_ids:
            errors.append(
                f"{where}: cites evidence_id {eid!r}, which is not in "
                "evidence/evidence.jsonl — the narrative points at a row the "
                "reader cannot look up")
    if not ids and not inference:
        errors.append(
            f"{where}: “{_snippet(text)}” cites no evidence_ids and is "
            "not flagged \"inference\": true — a reader cannot tell whether this "
            "is an observation, the authors' reading, or yours. Cite the "
            "evidence rows it rests on, or mark it as reviewer inference so it "
            "renders under that label.")
    if require_claim_alignment and not inference:
        if not statement_claim_ids:
            errors.append(
                f"{where}: conclusion supplies no claim_ids — conclusions must "
                "name the atomic claims they synthesize so a citation from one "
                "claim cannot silently support another proposition"
            )
        unknown_claims = sorted(
            set(statement_claim_ids) - set(valid_claim_ids or set())
        )
        if unknown_claims:
            errors.append(
                f"{where}: cites unknown claim_ids {', '.join(unknown_claims)}"
            )
        rows = [
            (evidence_by_id or {}).get(evidence_id, {}) for evidence_id in ids
            if evidence_id in (evidence_by_id or {})
        ]
        cited_claims = {
            str(row.get("claim_id") or "") for row in rows
            if str(row.get("claim_id") or "")
        }
        missing_claim_support = sorted(set(statement_claim_ids) - cited_claims)
        if missing_claim_support:
            errors.append(
                f"{where}: claim_ids {', '.join(missing_claim_support)} have no "
                "cited evidence row in this conclusion"
            )
        unrelated_evidence = sorted(cited_claims - set(statement_claim_ids))
        if unrelated_evidence:
            errors.append(
                f"{where}: evidence_ids belong to undeclared claim_ids "
                f"{', '.join(unrelated_evidence)}"
            )
        kinds = {str(row.get("evidence_kind") or "") for row in rows}
        if rows and "primary" not in kinds and raw.get("qualified") is not True:
            errors.append(
                f"{where}: conclusion relies only on secondary/indirect evidence; "
                "set qualified=true after wording it as indirect rather than a "
                "directly established result"
            )
    else:
        rows = [
            (evidence_by_id or {}).get(evidence_id, {}) for evidence_id in ids
            if evidence_id in (evidence_by_id or {})
        ]
    if rows:
        errors += statement_semantic_errors(text, rows, where=where)
    kinds = {str(row.get("evidence_kind") or "") for row in rows}
    if not rows:
        evidence_qualification = "inference" if inference else "uncited"
    elif kinds == {"primary"}:
        evidence_qualification = "direct primary"
    elif "primary" in kinds:
        evidence_qualification = "mixed direct and indirect"
    else:
        evidence_qualification = "secondary/indirect"
    return {
        "text": text,
        "evidence_ids": ids,
        "inference": inference,
        "claim_ids": statement_claim_ids,
        "evidence_qualification": evidence_qualification,
        "qualified": bool(raw.get("qualified")),
        "no_qualifying_anchor": bool(raw.get("no_qualifying_anchor")),
        # Preserved for section_classification_errors: an external finding is
        # ungrounded (no evidence_ids), so it must still name its source and a
        # resolvable locator. Normalisation used to drop these fields, leaving
        # the check nothing to enforce.
        "source": str(raw.get("source") or "").strip(),
        "locator": str(raw.get("locator") or "").strip(),
    }


def _load_narratives(root: pathlib.Path, evidence_ids: set[str],
                     evidence_by_id: dict[str, dict],
                     claim_ids: set[str]) -> tuple[dict[str, dict], list[str]]:
    """``deliverables/claim_narratives.jsonl`` -> {claim_id: {facet: statement}}.

    Absent file: no narratives, no errors, and the renderers fall back to the
    plain quote-under-claim layout.

    A record whose keys ALL start with ``_`` is a comment line — the templates
    ship their schema notes that way, because JSONL has nowhere else to put
    them. Any other record without a resolvable ``claim_id`` is an error, as is
    an unrecognised facet key: a typo'd ``reviewer_inferences`` that is silently
    dropped is indistinguishable, in the rendered report, from a reviewer who
    never wrote one.
    """
    path = root / "deliverables" / "claim_narratives.jsonl"
    if not path.exists():
        return {}, []
    records, errors = _read_jsonl_reporting(path)
    known = set(NARRATIVE_FACET_KEYS)
    out: dict[str, dict] = {}
    for i, record in enumerate(records, 1):
        if all(str(k).startswith("_") for k in record):
            continue
        cid = str(record.get("claim_id") or "").strip()
        if not cid:
            errors.append(
                f"claim_narratives.jsonl record {i} has no claim_id, so it "
                "cannot be attached to a claim")
            continue
        if cid not in claim_ids:
            errors.append(
                f"claim_narratives.jsonl: narrative for {cid!r}, which is not a "
                "claim in corpus/claims.jsonl — it would render nowhere")
            continue
        if cid in out:
            errors.append(
                f"claim_narratives.jsonl: {cid!r} has more than one record; "
                "merge them, one record per claim")
            continue
        for key in sorted(set(record) - known - {"claim_id"}):
            if str(key).startswith("_"):
                continue
            errors.append(
                f"claim_narratives.jsonl {cid}: unrecognised field {key!r} — "
                f"expected one of {', '.join(NARRATIVE_FACET_KEYS)}")
        facets: dict[str, dict] = {}
        for key in NARRATIVE_FACET_KEYS:
            statement = _statement(record.get(key),
                                   where=f"claim_narratives.jsonl {cid}.{key}",
                                   evidence_ids=evidence_ids, errors=errors,
                                   evidence_by_id=evidence_by_id)
            if statement:
                if (
                    key == "contradiction"
                    and not statement["evidence_ids"]
                    and not statement["no_qualifying_anchor"]
                ):
                    errors.append(
                        f"claim_narratives.jsonl {cid}.contradiction: an uncited "
                        "countervailing statement must set "
                        '"no_qualifying_anchor": true so a searched-but-empty '
                        "result cannot be mistaken for retained evidence"
                    )
                facets[key] = statement
        out[cid] = facets
    return out, errors


def _load_sections(root: pathlib.Path, evidence_ids: set[str],
                   evidence_by_id: dict[str, dict],
                   claim_ids: set[str]) -> tuple[dict[str, list[dict]],
                                                    list[str], bool]:
    """``deliverables/report_sections.json`` -> {section: [statement, ...]}.

    Returns ``(sections, errors, present)``; ``present`` says whether the
    artifact exists at all. The fallback to ``build_pdf``'s ``--*-file``
    arguments is decided PER SECTION, on whether this file supplies that one —
    so a file that carries three of the four still lets the fourth come from
    the old CLI, and a section this file does supply is never overridden.

    A section may be written either as ``{"statements": [...]}`` or as a bare
    list; both read the same and both are validated by the same rule as a claim
    narrative, because an un-cited sentence in the Conclusions is exactly as
    unattributable as an un-cited sentence under a claim.
    """
    path = root / "deliverables" / "report_sections.json"
    empty = {key: [] for key in SECTION_KEYS}
    if not path.exists():
        return empty, [], False
    data = read_json(path, None)
    if not isinstance(data, dict):
        return empty, [
            "report_sections.json is not a JSON object with the keys "
            f"{', '.join(SECTION_KEYS)}"
        ], True

    errors: list[str] = []
    for key in sorted(set(data) - set(SECTION_KEYS)):
        if str(key).startswith("_"):
            continue
        errors.append(
            f"report_sections.json: unrecognised section {key!r} — expected one "
            f"of {', '.join(SECTION_KEYS)}; it would render nowhere")

    sections: dict[str, list[dict]] = {}
    for key in SECTION_KEYS:
        raw = data.get(key)
        if isinstance(raw, dict):
            raw = raw.get("statements")
        if raw is None:
            sections[key] = []
            continue
        if isinstance(raw, (str, dict)):
            raw = [raw]
        if not isinstance(raw, list):
            errors.append(
                f"report_sections.json {key}: expected a list of statements, "
                f"got {type(raw).__name__}")
            sections[key] = []
            continue
        out: list[dict] = []
        for i, item in enumerate(raw, 1):
            statement = _statement(item, where=f"report_sections.json {key}[{i}]",
                                   evidence_ids=evidence_ids, errors=errors,
                                   evidence_by_id=evidence_by_id,
                                   valid_claim_ids=claim_ids,
                                   require_claim_alignment=(key == "conclusions"))
            if statement:
                errors += section_classification_errors(key, statement, i)
                out.append(statement)
        sections[key] = out
    return sections, errors, True


_PAYWALL_COUNT = re.compile(
    r"\b(?P<count>\d+)\s+(?:hard[-\s]*)?paywalled\b", re.IGNORECASE
)
_TRANSIENT_MISS_COUNT = re.compile(
    r"\b(?P<count>\d+)\s+(?:transient(?:ly)?[-\s]+(?:failed|unavailable)"
    r"|retrieval[-\s]+fail(?:ed|ure)s?)\b",
    re.IGNORECASE,
)


def corpus_accounting_errors(sections: dict, corpus_ledger: dict) -> list[str]:
    """Authored Methods may summarize counts but may not reclassify misses."""
    classification = corpus_ledger.get("retrieval_classification") or {}
    expected = {
        "paywalled": int(classification.get("paywalled") or 0),
        "retrieval_failed": int(classification.get("retrieval_failed") or 0),
    }
    patterns = (
        ("paywalled", _PAYWALL_COUNT),
        ("retrieval_failed", _TRANSIENT_MISS_COUNT),
    )
    errors: list[str] = []
    for index, statement in enumerate(sections.get("methods") or [], 1):
        text = str(statement.get("text") or "")
        for kind, pattern in patterns:
            for match in pattern.finditer(text):
                authored = int(match.group("count"))
                if authored == expected[kind]:
                    continue
                errors.append(
                    f"report_sections.json methods[{index}] says "
                    f"{match.group(0)!r}, but corpus/corpus_ledger.json "
                    f"classifies {expected[kind]} as {kind}. Corpus disposition "
                    "counts are machine-derived; rewrite the sentence from the "
                    "ledger instead of combining retrieval failures with paywalls."
                )
    return errors


def support_accounting_errors(claim_rows: list[dict]) -> list[str]:
    """Each claim's declared support must reconcile with the anchors behind it.

    The support state is machine-derived from evidence/evidence.jsonl; the
    supporting/contradicting anchors a reader actually sees come from
    deliverables/grounded_quotes.json. They are independent artifacts, and when
    they disagree the report advertises a support level its own displayed
    evidence does not carry — the same failure class as a figure attached to a
    claim it does not support. Modelled on ``corpus_accounting_errors``: a
    mismatch is an error, not a warning.
    """
    errors: list[str] = []
    for row in claim_rows:
        cid = row.get("display_id") or row.get("claim_id") or "<unknown>"
        state = str(row.get("support_state") or "")
        n_support = len(row.get("supporting") or [])
        n_contra = len(row.get("contradicting") or [])
        if not is_grounded(state):
            if n_support or n_contra:
                errors.append(
                    f"claim {cid}: support state {state} is ungrounded but the "
                    f"claim displays {n_support} supporting and {n_contra} "
                    "contradicting anchor(s). Support states are machine-derived "
                    "from evidence.jsonl; recompute the state, or drop the "
                    "anchors the claim does not rest on."
                )
            continue
        if state == C_REFUTED:
            if n_contra == 0:
                errors.append(
                    f"claim {cid}: support state C_REFUTED but no contradicting "
                    "anchor is shown. A refutation must display the contradicting "
                    "evidence it rests on."
                )
        elif state == C_CONFLICTED:
            if n_support == 0 or n_contra == 0:
                errors.append(
                    f"claim {cid}: support state C_CONFLICTED but the claim "
                    f"displays {n_support} supporting and {n_contra} "
                    "contradicting anchor(s); a conflicted claim must show both "
                    "the support and the contradiction it reconciles."
                )
        elif n_support == 0:
            errors.append(
                f"claim {cid}: support state {state} is a support tier but no "
                "supporting anchor is shown. The delivered support is not backed "
                "by any evidence the reader can see."
            )
    return errors


# Wording that overstates a weak/hedged support tier. Mirrors
# verify_review.STRONG_WORDING; kept local because support_policy — the single
# source of truth for the STATES — must not grow a report-layer prose regex.
_PROHIBITED_STRONGER_WORDING = re.compile(
    r"\b(?:proves?|cures?|always|never|in humans|abolishes?)\b", re.IGNORECASE
)


def prohibited_wording_errors(claim_rows: list[dict]) -> list[str]:
    """Enforce the ``prohibited_stronger_wording`` column evidence_first computes.

    ``evidence_first.build_matrix`` sets that column non-empty exactly when the
    support state is a WEAK_STATE (indirect, conflicted, refuted, or
    insufficient), but nothing ever consumed it — so a weak-tier claim could
    still be delivered in categorical or causal wording. Here the delivered
    wording (the claim text plus any authored narrative facet prose) must not use
    stronger-than-supported language when the tier is weak; a violation fails the
    build gate rather than shipping.
    """
    errors: list[str] = []
    for row in claim_rows:
        if str(row.get("support_state") or "") not in WEAK_STATES:
            continue
        cid = row.get("display_id") or row.get("claim_id") or "<unknown>"
        delivered = [str(row.get("claim_text") or "")]
        narrative = row.get("narrative")
        if isinstance(narrative, dict):
            for facet in narrative.values():
                if isinstance(facet, dict):
                    delivered.append(str(facet.get("text") or ""))
        for text in delivered:
            match = _PROHIBITED_STRONGER_WORDING.search(text)
            if match:
                errors.append(
                    f"claim {cid}: support state "
                    f"{row.get('support_state')} is weak/hedged, but the "
                    f"delivered wording uses the stronger term {match.group(0)!r}. "
                    "Word the claim to the support tier it actually holds."
                )
                break
    return errors


_SECTION_EXCLUSIVITY = re.compile(
    r"\b(?:works?|responds?|effective|benefits?)\s+only\b"
    r"|\bonly\s+in\s+the\b"
    r"|\bdefines?\s+the\s+(?:responsive|sensitive)\s+subset\b",
    re.IGNORECASE,
)


def section_scope_errors(sections: dict, evidence: list[dict]) -> list[str]:
    """Reject categorical population boundaries unsupported by cited rows."""
    quotes = {
        str(row.get("evidence_id") or ""): str(row.get("quote") or "")
        for row in evidence
    }
    errors: list[str] = []
    for section, statements in sections.items():
        for index, statement in enumerate(statements or [], 1):
            text = str(statement.get("text") or "")
            cited_text = " ".join(
                quotes.get(str(evidence_id), "")
                for evidence_id in statement.get("evidence_ids") or []
            ).casefold()
            for match in _SECTION_EXCLUSIVITY.finditer(text):
                wording = " ".join(match.group(0).split()).casefold()
                if wording and wording in " ".join(cited_text.split()):
                    continue
                errors.append(
                    f"report_sections.json {section}[{index}] uses exclusive "
                    f"wording {match.group(0)!r} that appears in none of its "
                    "cited anchors. Describe the observed selected/enriched "
                    "context and disclose untested populations instead."
                )
    return errors


def _narrative_contract_errors(contract: dict, mode: str,
                               claim_rows: list[dict],
                               narratives_present: bool) -> list[str]:
    """The contract's narrative requirements, checked against the model.

    Only fires when the run actually carries narratives: the artifacts are
    optional, and a run that predates them must still build. What the contract
    prevents is a HALF-authored narrative — a deep/broad review that separates
    the facets for some central claims and drops back to a quote catalogue for
    the rest, which is the shape the reader cannot navigate.
    """
    spec = (contract or {}).get("narrative") or {}
    if not spec:
        return []
    errors: list[str] = []

    declared = [str(f) for f in (spec.get("facets") or [])]
    if declared and declared != list(NARRATIVE_FACET_KEYS):
        errors.append(
            "report_contract.json narrative.facets "
            f"{declared} disagrees with the facets the renderers emit "
            f"{list(NARRATIVE_FACET_KEYS)} — a facet named in only one of the "
            "two is either never rendered or never validated")

    required_here = mode in (spec.get("required_modes") or [])
    # The artifact was OPTIONAL, and that is how two `broad` runs of the same
    # skill produced structurally different documents: one separated all 14 of
    # its claims into the five facets, the other authored no narrative at all and
    # rendered a bare quote catalogue — the exact regression this module's
    # docstring says the artifact exists to prevent. Silence is not evidence that
    # a narrative was not wanted; for the modes that require one it is a missing
    # deliverable.
    if required_here and spec.get("require_artifact", True) and not narratives_present:
        errors.append(
            f"mode={mode} requires deliverables/claim_narratives.jsonl and the "
            "run has none — without it Results is a quote catalogue, and two "
            "runs of the same mode ship structurally different documents. "
            "Author one record per central claim (see "
            "templates/claim_narratives.jsonl), or set "
            "narrative.require_artifact false in the contract to opt out "
            "deliberately")
        return errors
    if not narratives_present:
        return errors
    if not required_here:
        return errors
    if not spec.get("require_observed_result", True):
        return errors

    selector = str(spec.get("central_claims", "grounded")).lower()
    central = ALL_STATES if selector == "all" else GROUNDED_STATES
    for row in claim_rows:
        if row["support_state"] not in central:
            continue
        if (row.get("narrative") or {}).get("observed_result"):
            continue
        errors.append(
            f"{row['claim_id']}: mode={mode} requires an observed_result for "
            "every central claim, and claim_narratives.jsonl carries none — "
            "without it the claim is a quote with no stated finding, which is "
            "the regression the narrative artifact exists to prevent")
    return errors


# Axis identifiers that count as the review's countervailing arm. Matched as
# substrings so a run may name its axis `safety_contradictions`,
# `contradictions`, `risks_and_safety`, and so on.
_CONTRADICTION_AXIS_MARKERS = ("safety", "contradict", "risk", "null",
                               "limitation", "counter", "adverse")


def _coverage_errors(contract: dict, mode: str,
                     claim_rows: list[dict]) -> list[str]:
    """Axes the contract requires that this run does not have.

    The GRN review carried a `safety_contradictions` axis with a deliberate
    contradiction search; the APOE review, same skill and same mode, had six axes
    all of which were mechanism or efficacy and NONE that looked for harm or
    disconfirmation — on a target whose central development risk is exactly that,
    and whose own Next steps asked for a safety adjudication. A target-evidence
    review with no countervailing arm reads as advocacy, so the arm is required
    rather than left to whether the run thought of it.
    """
    spec = (contract or {}).get("coverage") or {}
    if not spec or mode not in (spec.get("required_modes") or []):
        return []
    if not spec.get("require_contradiction_axis", True):
        return []
    axes = {str(row["cluster"]).lower() for row in claim_rows}
    if any(marker in axis for axis in axes
           for marker in _CONTRADICTION_AXIS_MARKERS):
        return []
    # A contradicting quote anywhere also satisfies it: the requirement is that
    # the review looked, not that it named an axis a particular way.
    if any(row["contradicting"] for row in claim_rows):
        return []
    return [
        f"mode={mode} requires an axis covering safety, contradictions, null "
        "results or risk, and this run has none of "
        f"{sorted(axes)} — nor a single contradicting quote. A review that "
        "searched only for confirmation cannot report that it found none; run "
        "the contradiction and safety facets (SKILL.md step 2) before delivering"
    ]


_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


def markdown_headings(text: str) -> list[str]:
    """Every ATX heading in a Markdown document, in order."""
    out: list[str] = []
    for line in (text or "").splitlines():
        match = _MD_HEADING.match(line)
        if match and match.group(1).strip():
            out.append(match.group(1).strip())
    return out


def missing_markdown_sections(text: str, contract: dict) -> list[str]:
    """Required sections absent from ``review.md`` AS HEADINGS.

    The PDF has been held to ``required_sections`` since the contract existed;
    the Markdown was held to nothing, and shipped for months with no
    Introduction, Methods, Conclusions or Next steps at all. One model, one
    section list, both deliverables.
    """
    spec = (contract or {}).get("markdown") or {}
    required = list((contract or {}).get("required_sections") or [])
    if not spec.get("required_sections_same_as_pdf", True):
        required = list(spec.get("required_sections") or required)
    present = {h.lower() for h in markdown_headings(text)}
    return [
        f"required section missing from review.md: {section!r} — the PDF is "
        "held to this section list and the Markdown must carry the same one"
        for section in required if section.lower() not in present
    ]


def build_model(root: pathlib.Path,
                contract: dict | None = None) -> dict[str, Any]:
    """Assemble everything both renderers need, in render order."""
    claims = read_jsonl(root / "corpus" / "claims.jsonl")
    evidence = read_jsonl(root / "evidence" / "evidence.jsonl")
    # Support states are DERIVED here from evidence.jsonl, never read out of
    # grounded_quotes.json. That file is a derived artifact, and the PDF quote
    # gate checks the report against it — so trusting it here would let one
    # stale or truncated file define both the report and its own expected
    # answer, with nothing outside the loop to catch the drift.
    #
    # It is still loaded, but only to cross-check: a disagreement means the
    # derived file predates the current evidence and the run must be
    # re-finalized. Detecting that is the point.
    grounded = read_json(root / "deliverables" / "grounded_quotes.json", {}) or {}
    derived_states = _derive_support_states(evidence)
    stale = [
        f"{cid}: grounded_quotes.json says {entry.get('support_state')!r} but "
        f"evidence.jsonl derives {derived_states.get(cid, 'C_INSUFFICIENT')!r}"
        for cid, entry in sorted(grounded.items())
        if entry.get("support_state") != derived_states.get(cid, "C_INSUFFICIENT")
    ]
    refs = read_jsonl(root / "corpus" / "references.jsonl")
    manifest = read_json(root / "run_manifest.json", {}) or {}
    # The run's own names for its axes. Only the run knows whether
    # "lowering_silencing" meant "Lowering & silencing" or "Lowering: silencing".
    axis_overrides = {str(k): str(v) for k, v in
                      (manifest.get("axis_labels") or {}).items() if v}
    stats = read_json(root / "deliverables" / "review_stats.json", None)
    if stats is None:
        stats = read_json(root / "review_stats.json", {}) or {}
    corpus_ledger = read_json(root / "corpus" / "corpus_ledger.json", {}) or {}
    ledger_counts = corpus_ledger.get("counts") or {}
    if ledger_counts:
        # Corpus numbers shown to the reader come from one canonical join, not
        # from authored Methods prose or per-machine summaries.
        stats = {
            **stats,
            "papers_discovered": ledger_counts.get("discovered", 0),
            "papers_in_scope": ledger_counts.get("in_scope", 0),
            "papers_selected": ledger_counts.get("selected", 0),
            "papers_full_text": ledger_counts.get("retrieved", 0),
            "papers_cited": ledger_counts.get("cited", 0),
            "papers_with_figures": ledger_counts.get("figure_producing", 0),
        }
    fig_manifest = read_json(
        root / "deliverables" / "figures_cited" / "figures_manifest.json", {}) or {}

    mode = resolve_review_mode(root)

    refs_by_id = {_pid(r.get("paper_id")): r for r in refs}

    # --- figures, keyed by the claims they ground -------------------------
    #
    # A figure whose image file is NOT on disk is dropped HERE, once, before
    # ``report_number`` is assigned — so both renderers and both header lines
    # see one identical list. When the PDF builder skipped a missing image on
    # its own, review.md linked two figures and the PDF embedded one while both
    # headers still claimed "2 paper figures", and the gates (which count
    # labels) saw nothing wrong.
    figures_dir = root / "deliverables" / "figures_cited"
    figures: list[dict] = []
    figures_missing: list[dict] = []
    figures_by_claim: dict[str, list[dict]] = defaultdict(list)
    for fig in fig_manifest.get("figures", []) or []:
        if fig.get("status") != "exported":
            continue
        pid = _pid(fig.get("paper_id"))
        ref = refs_by_id.get(pid, {})
        caption = fig.get("caption") or ""
        # The client report gets the clean source crop. OCR-box overlays remain
        # in figures_cited as diagnostic audit artifacts; shipping the boxes made
        # otherwise relevant figures look like parser screenshots.
        image = fig.get("image") or fig.get("annotated_image")
        img_path = _resolve_under(figures_dir, image)
        if img_path is None or not img_path.exists():
            figures_missing.append({
                "paper_id": pid, "figure_id": fig.get("figure_id"),
                "image": image, "resolved": str(img_path) if img_path else "",
            })
            continue
        entry = {
            "paper_id": pid,
            "figure_id": fig.get("figure_id"),
            "label": figure_label(caption, str(fig.get("figure_id")),
                                  fig.get("page")),
            "caption": caption,
            # Why the figure is shown, from figure_provenance: the caption terms
            # that scored and the in-figure text that was boxed. Empty when the
            # run predates provenance.
            "provenance": fig.get("provenance") or {},
            "provenance_note": str(fig.get("provenance_note") or ""),
            "license": str(fig.get("license") or ""),
            "reuse_rights": str(fig.get("reuse_rights") or "none"),
            "figure_embedding_allowed": bool(
                fig.get("figure_embedding_allowed")
            ),
            "included_at_user_direction": bool(
                fig.get("included_at_user_direction")
            ),
            "rights_notice": str(fig.get("rights_notice") or ""),
            "role": str(fig.get("role") or "primary_data"),
            "image": image,
            "image_path": str(img_path),
            "plain_image": fig.get("image"),
            "claims": fig.get("claims", []),
            "citation": _citation({**ref, **fig}),
            "url": fig.get("url") or ref.get("url") or (
                f"https://doi.org/{ref.get('doi')}" if ref.get("doi") else ""),
        }
        figures.append(entry)
        for cid in entry["claims"]:
            figures_by_claim[cid].append(entry)
    # ``report_number`` is deliberately NOT assigned here. Numbering follows the
    # order a reader MEETS the figures, which is the rendered claim order, and
    # that order is not known until the claims below have been sorted. Numbering
    # the manifest's own order — which is sorted by paper id — is how a shipped
    # report presented its figures as 5, 6, 3, 4, 1, 2.

    # --- the authored narrative, validated against the evidence -----------
    #
    # Every evidence_id a narrative statement cites must be a row that exists,
    # and every statement must be either cited or flagged as inference. Both
    # builders abort on ``narrative_errors``.
    evidence_ids = {str(row.get("evidence_id") or "").strip() for row in evidence
                    if str(row.get("evidence_id") or "").strip()}
    evidence_by_id = {
        str(row.get("evidence_id") or "").strip(): row for row in evidence
        if str(row.get("evidence_id") or "").strip()
    }
    claim_ids = {str(c.get("claim_id") or "") for c in claims}
    narratives, narrative_errors = _load_narratives(root, evidence_ids,
                                                    evidence_by_id, claim_ids)
    sections, section_errors, sections_present = _load_sections(
        root, evidence_ids, evidence_by_id, claim_ids
    )
    narrative_errors += section_errors
    narrative_errors += corpus_accounting_errors(sections, corpus_ledger)
    narrative_errors += section_scope_errors(sections, evidence)

    # --- claims in presentation order (grouped by axis) -------------------
    support_metadata = support_metadata_by_claim(evidence)
    claim_rows: list[dict] = []
    for claim in claims:
        cid = claim.get("claim_id")
        entry = grounded.get(cid, {})
        state = derived_states.get(cid, "C_INSUFFICIENT")
        support = support_metadata.get(str(cid), {})
        claim_rows.append({
            "claim_id": cid,
            "claim_text": claim.get("claim_text", ""),
            "scope": claim.get("scope", ""),
            "cluster": claim.get("cluster") or "uncategorized",
            "cluster_label": display_axis(
                claim.get("cluster") or "uncategorized", axis_overrides),
            "support_state": state,
            "support_label": support.get("label", SUPPORT_LABEL.get(state, state)),
            "support_basis": support.get("basis", {}),
            "supporting": _enrich_anchors(
                entry.get("supporting_anchors", []), refs_by_id),
            "contradicting": _enrich_anchors(
                entry.get("contradicting_anchors", []), refs_by_id),
            "figures": figures_by_claim.get(cid, []),
            # {} when this run authored no narrative for the claim — the
            # renderers then produce exactly what they produced before the
            # narrative artifacts existed.
            "narrative": narratives.get(str(cid), {}),
        })

    axes = _ordered_axes(claim_rows)
    claim_rows.sort(key=lambda c: (axes.index(c["cluster"]), c["claim_id"]))

    # The report prints the canonical claim id. Renumbering after axis sorting
    # made a visually tidy PDF but severed its joins to evidence.jsonl, figure
    # manifests, and narrative task receipts. Gaps are disclosed; IDs are never
    # repurposed for a different claim.
    for row in claim_rows:
        row["display_id"] = row["claim_id"]
    display_by_claim = {r["claim_id"]: r["display_id"] for r in claim_rows}

    # Each claim's declared support must reconcile with the anchors shown behind
    # it, and a weak-tier claim may not be delivered in stronger-than-supported
    # wording. Both feed narrative_errors, which both builders refuse to render.
    narrative_errors += support_accounting_errors(claim_rows)
    narrative_errors += prohibited_wording_errors(claim_rows)

    # Authored prose cites canonical IDs. Validate them after filtering so a
    # reference to a removed claim is marked explicitly rather than silently
    # pointing at a different retained claim.
    unresolved = _retarget_claim_references(claim_rows, sections, display_by_claim)

    # Figures are numbered in order of first appearance, and ``figures`` is
    # re-ordered to match so the Figures list reads in the same order as the
    # Results section that contains them.
    ordered_figures: list[dict] = []
    for row in claim_rows:
        for fig in row["figures"]:
            if "report_number" not in fig:
                fig["report_number"] = len(ordered_figures) + 1
                ordered_figures.append(fig)
    # A figure whose every claim was dropped still exists in the manifest; it is
    # numbered last rather than silently renumbering the ones a reader saw.
    for fig in figures:
        if "report_number" not in fig:
            fig["report_number"] = len(ordered_figures) + 1
            ordered_figures.append(fig)
    figures = ordered_figures
    for fig in figures:
        fig["claim_display_ids"] = [display_by_claim[c] for c in fig["claims"]
                                    if c in display_by_claim]

    contract = contract if isinstance(contract, dict) else load_contract()
    narrative_errors += _narrative_contract_errors(
        contract, mode, claim_rows, bool(narratives))
    coverage_errors = _coverage_errors(contract, mode, claim_rows)

    # --- synthesis table + panel, both recomputed from the same rows ------
    synthesis_table = _synthesis_table(claim_rows, evidence, refs_by_id, axes,
                                       axis_overrides)
    coverage_matrix = read_json(root / "corpus" / "coverage_matrix.json", {}) or {}
    searched_empty_axes = []
    live_axes = {str(row["cluster"]) for row in claim_rows}
    for row in coverage_matrix.get("axes") or []:
        axis = str(row.get("axis") or "").strip()
        if (
            not axis
            or str(row.get("status") or "") != "searched_empty"
            or axis in live_axes
        ):
            continue
        empty = {
            "axis": axis,
            "axis_label": display_axis(axis, axis_overrides),
            "status": "searched_empty",
            "queries": list(row.get("queries") or []),
            "reason": str(row.get("reason") or "").strip()
            or "No qualifying full-text evidence was retained for this axis.",
        }
        searched_empty_axes.append(empty)
        synthesis_table.append({
            **empty,
            "bottom_line": empty["reason"],
            "support_state": "C_INSUFFICIENT",
            "support_label": SUPPORT_LABEL["C_INSUFFICIENT"],
            "sources": [],
            "sources_kind": "none",
            "n_sources_total": 0,
            "n_claims": 0,
        })
    panel_counts = _panel_counts(claim_rows, axes)
    panel_studies = _panel_studies(claim_rows, evidence, axes)
    references, reference_errors = _reference_list(
        refs, evidence, [r["claim_id"] for r in claim_rows])
    # Anchors carry the DOI; the reference INDEX is what an inline citation
    # needs so "Ward et al. 2024" can be followed to a numbered entry. Resolved
    # after the list is numbered, because the number depends on citation order.
    ref_index = {r["paper_id"]: r["index"] for r in references}
    for row in claim_rows:
        for anchor in row["supporting"] + row["contradicting"]:
            anchor["reference_index"] = ref_index.get(_pid(anchor.get("paper_id")))
    for table_row in synthesis_table:
        for source in table_row["sources"]:
            source["reference_index"] = ref_index.get(source["paper_id"])

    # A narrative statement cites the evidence ROWS it rests on, and the shipped
    # reports printed those row ids: "[E-648e8fe191f72194, E-e1f59bcaeec16378]"
    # in the Introduction and Conclusions, 37 of them in one report. The id is
    # the right thing to validate against and the wrong thing to show, so it
    # keeps its place in the artifacts and resolves to a citation on the page.
    evidence_citations = {
        eid: {
            "citation": _citation({**refs_by_id.get(_pid(row.get("paper_id")), {}),
                                   **row}),
            "reference_index": ref_index.get(_pid(row.get("paper_id"))),
            "url": row.get("url") or refs_by_id.get(
                _pid(row.get("paper_id")), {}).get("url") or "",
        }
        for row in evidence
        if (eid := str(row.get("evidence_id") or "").strip())
    }

    model: dict[str, Any] = {
        "mode": mode,
        "title": manifest.get("title") or "Grounded literature review",
        "question": manifest.get("question", ""),
        "stats": stats,
        "manifest": manifest,
        "corpus_ledger": corpus_ledger,
        "paper_accountability": [
            row for row in (corpus_ledger.get("records") or [])
            if row.get("selected")
        ],
        "corpus_flow": [
            {"state": state, "count": int(ledger_counts.get(state) or 0)}
            for state in (
                "discovered", "deduplicated", "in_scope", "selected",
                "attempted", "retrieved", "evidence_bearing", "cited",
                "figure_producing",
            )
        ] if ledger_counts else [],
        "axes": axes,
        "claims": claim_rows,
        "figures": figures,
        "figures_missing": figures_missing,
        "synthesis_table": synthesis_table,
        "searched_empty_axes": searched_empty_axes,
        "panel_counts": panel_counts,
        # Independent primary studies per axis — what the panel plots, because it
        # is not an artifact of how finely claims were split.
        "panel_studies": panel_studies,
        "axis_labels": {axis: display_axis(axis, axis_overrides) for axis in axes},
        "references": references,
        "reference_errors": reference_errors,
        # {evidence_id: {citation, reference_index, url}} — how a narrative
        # statement's cited rows are rendered as citations rather than hashes.
        "evidence_citations": evidence_citations,
        # The authored prose sections, keyed by SECTION_KEYS and always present
        # (empty lists when the run supplied none — the renderers then use
        # build_pdf's legacy --*-file text for that section, or say the section
        # was not supplied). ``sections_present`` says whether
        # report_sections.json exists at all.
        "sections": sections,
        "sections_present": sections_present,
        "contract": contract,
        "inference_label": narrative_inference_label(contract),
        # Non-empty means grounded_quotes.json is stale relative to the
        # canonical evidence. Builders refuse to render on this.
        "stale_derived": stale,
        # Non-empty means a narrative statement is unattributable, cites a row
        # that does not exist, or a central claim states no observed result.
        # Builders refuse to render on this.
        "narrative_errors": narrative_errors,
        # Non-empty means the review never looked for disconfirming or safety
        # evidence. Builders refuse to render on this.
        "coverage_errors": coverage_errors,
    }
    # Reads the assembled claim and figure lists, so it runs last.
    model["unresolved_claim_refs"] = unresolved
    model["coverage_notes"] = coverage_notes(root, model)
    if unresolved:
        model["coverage_notes"].append(
            f"{len(unresolved)} cross-reference(s) in the authored prose point "
            f"to claims that did not survive adjudication "
            f"({', '.join(unresolved)}); they are marked in place rather than "
            "renumbered to a different claim.")
    return model


def _resolve_under(base: pathlib.Path, value: Any) -> pathlib.Path | None:
    """Resolve ``value`` as a path, treating a relative one as under ``base``.

    A bare ``pathlib.Path(value).exists()`` resolves a relative path against the
    PROCESS CWD, so the same run answered "does this image exist?" differently
    depending on where the gate was invoked from.
    """
    if not value:
        return None
    path = pathlib.Path(str(value))
    return path if path.is_absolute() else base / path


def _ordered_axes(claim_rows: list[dict]) -> list[str]:
    """Axes in first-appearance order, so the report reads in a stable order."""
    seen: list[str] = []
    for row in claim_rows:
        if row["cluster"] not in seen:
            seen.append(row["cluster"])
    return seen


def _strongest(states: list[str]) -> str:
    """Delegates to the policy — see support_policy.strongest."""
    return _strongest_state(states)


def _synthesis_table(claim_rows: list[dict], evidence: list[dict],
                     refs_by_id: dict, axes: list[str],
                     axis_overrides: dict[str, str] | None = None) -> list[dict]:
    """One row per evidence axis: bottom line + strongest support + sources.

    The "strongest support" cell names the actual papers behind the strongest
    claim on that axis, so the reader can check the tier against real sources
    instead of trusting a label.
    """
    ev_by_claim: dict[str, list[dict]] = defaultdict(list)
    for row in evidence:
        ev_by_claim[row.get("claim_id")].append(row)

    table: list[dict] = []
    for axis in axes:
        rows = [c for c in claim_rows if c["cluster"] == axis]
        if not rows:
            continue
        states = [r["support_state"] for r in rows]
        best = _strongest(states)
        best_rows = [r for r in rows if r["support_state"] == best]
        # Papers giving primary support to the strongest claim(s) on this axis.
        # Each carries its URL: these cells are citations like every other
        # citation in the report, and Table 1 was the one place they rendered as
        # dead text while the same author-year under a quote was a link.
        by_kind: dict[str, list[dict]] = {"primary": [], "secondary": []}
        seen_cites: set[str] = set()
        for r in best_rows:
            for ev in ev_by_claim.get(r["claim_id"], []):
                if ev.get("stance") != "supports":
                    continue
                kind = "primary" if ev.get("evidence_kind") == "primary" else "secondary"
                ref = refs_by_id.get(_pid(ev.get("paper_id")), {})
                cite = _citation({**ref, **ev})
                if not cite or cite in seen_cites:
                    continue
                seen_cites.add(cite)
                doi = ev.get("doi") or ref.get("doi") or ""
                by_kind[kind].append({
                    "citation": cite,
                    "paper_id": _pid(ev.get("paper_id")),
                    "url": (ev.get("url") or ref.get("url")
                            or (f"https://doi.org/{doi}" if doi else "")),
                })
        # Fall back to the secondary sources when no primary one exists. This
        # cell used to list primaries only, so an axis at indirect/background
        # tier rendered as an em dash — and a reader of the shipped report saw
        # "GRN loss-of-function causes FTD" beside a blank Sources cell, which
        # reads as UNSOURCED when the claim in fact carries three verbatim
        # quotes. The tier already says the support is indirect; the cell should
        # still name who said it.
        papers = by_kind["primary"] or by_kind["secondary"]
        sources_kind = "primary" if by_kind["primary"] else "secondary"
        table.append({
            "axis": axis,
            "axis_label": display_axis(axis, axis_overrides),
            "bottom_line": best_rows[0]["claim_text"] if best_rows else "",
            "support_state": best,
            "support_label": (best_rows[0]["support_label"] if best_rows else
                              SUPPORT_LABEL.get(best, best)),
            "sources": papers[:3],
            "sources_kind": sources_kind,
            # The Sources cell names the papers behind the STRONGEST claim on
            # the axis, which is why a row could read "Single direct primary
            # study" beside two source names and look self-contradictory. Say
            # how many claims the tier was taken over so the cell is readable.
            "n_sources_total": len(papers),
            "n_claims": len(rows),
        })
    return table


def _panel_counts(claim_rows: list[dict], axes: list[str]) -> dict[str, Counter]:
    """Claim counts per axis per support tier — the synthesis panel's data.

    Derived from the same ``claim_rows`` the Results section renders, which is
    what makes the panel and the claim list impossible to disagree.
    """
    counts: dict[str, Counter] = {axis: Counter() for axis in axes}
    for row in claim_rows:
        counts[row["cluster"]][row["support_state"]] += 1
    return counts


def _study_key(row: dict) -> str:
    """The unit of independence: a study, not a paper.

    Two papers from one cohort are one replication, which is why convergence is
    counted over ``study_id``/``cohort_id`` rather than over ``paper_id``.
    """
    for key in ("study_id", "cohort_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return _pid(row.get("paper_id"))


def _panel_studies(claim_rows: list[dict],
                   evidence: list[dict], axes: list[str]) -> dict[str, int]:
    """Distinct independent primary studies behind each axis.

    The panel used to plot claim counts, and its own caption then had to tell the
    reader not to believe them: "bar heights are claim counts, which depend on
    how finely the reviewer split the claims — read the tiers, not the totals".
    A chart that needs that disclaimer is measuring the wrong thing. How many
    independent studies an axis rests on is not an artifact of how the reviewer
    chose to phrase claims, so it can be plotted and believed.
    """
    axis_by_claim = {row["claim_id"]: row["cluster"] for row in claim_rows}
    studies: dict[str, set[str]] = {axis: set() for axis in axes}
    for row in evidence:
        if row.get("stance") != "supports" or row.get("evidence_kind") != "primary":
            continue
        axis = axis_by_claim.get(row.get("claim_id"))
        if axis in studies:
            studies[axis].add(_study_key(row))
    return {axis: len(keys) for axis, keys in studies.items()}


_CLAIM_REF = re.compile(r"\bC-(\d{2,4})\b")

# The parser's positional figure handle: fig6_p12 is "the sixth figure region,
# page 12". It is not the paper's figure number and can contradict it — a
# delivered report printed "figure fig6_p12" beside a quote beginning "Fig 4.".
_POSITIONAL_FIGURE_ID = re.compile(r"^fig\w*?\d+_p\d+$", re.IGNORECASE)

# A handle derived from the paper's own label, needing only spacing:
# Figure2, Fig4, FIGURE1, ExtendedDataFigure2.
_LABEL_FIGURE_ID = re.compile(
    r"^(extendeddata|supplementary|supp|extended)?fig(?:ure)?(\d+[a-z]?)$",
    re.IGNORECASE)

_FIGURE_PREFIX = {
    "extendeddata": "Extended Data ", "extended": "Extended Data ",
    "supplementary": "Supplementary ", "supp": "Supplementary ",
}


def readable_figure_locator(part: str) -> str:
    """Turn a locator's "figure <handle>" into something a reader can use.

    Returns "" when the handle is positional — a machine id tells the reader
    nothing, and printing one that disagrees with the figure's real number is
    worse than printing no number at all.
    """
    match = re.fullmatch(r"figure\s+(\S+)", str(part or "").strip(), re.IGNORECASE)
    if not match:
        return part
    handle = match.group(1)
    if _POSITIONAL_FIGURE_ID.match(handle):
        return ""
    labelled = _LABEL_FIGURE_ID.match(handle)
    if labelled:
        prefix = _FIGURE_PREFIX.get((labelled.group(1) or "").lower(), "")
        return f"{prefix}Fig. {labelled.group(2).upper()}"
    return part


def _retarget_claim_references(claim_rows: list[dict], sections: dict,
                               display_by_claim: dict[str, str]) -> list[str]:
    """Validate C-NNN references against the canonical delivered claim IDs.

    A reference whose claim did not survive cannot be repaired by renumbering —
    the thing it points at is not in the document. Those are replaced with an
    explicit marker rather than a plausible-looking wrong number, because a
    reader who follows C-012 and finds an unrelated claim is worse off than one
    told the target is absent. Returns the ids that could not be resolved.
    """
    # Accept the authored id in either raw ("42") or padded ("C-042") form.
    lookup: dict[str, str] = {}
    for raw, display in display_by_claim.items():
        for key in {str(raw), str(raw).upper()}:
            lookup[key] = display
            match = _CLAIM_REF.search(key)
            if match:
                lookup[f"C-{int(match.group(1)):03d}"] = display
    live_displays = set(display_by_claim.values())

    unresolved: list[str] = []

    def rewrite(text: str) -> str:
        def one(match: re.Match) -> str:
            token = f"C-{int(match.group(1)):03d}"
            if token in lookup:
                return lookup[token]
            if token in live_displays:
                return token          # already a delivered id
            unresolved.append(token)
            # Marked in place, not replaced by a phrase: three of these in one
            # sentence ("claims C-020, C-021 and C-061") turn into unreadable
            # prose if each becomes a clause.
            return f"{token} (not retained)"
        return _CLAIM_REF.sub(one, text)

    for row in claim_rows:
        for facet in (row.get("narrative") or {}).values():
            if isinstance(facet, dict) and facet.get("text"):
                facet["text"] = rewrite(str(facet["text"]))
    for statements in (sections or {}).values():
        for statement in statements or []:
            if isinstance(statement, dict) and statement.get("text"):
                statement["text"] = rewrite(str(statement["text"]))
    return sorted(set(unresolved))


def coverage_notes(root: pathlib.Path, model: dict) -> list[str]:
    """What this run could not cover, from counters it already keeps.

    Every number here was already in the run's artifacts and appeared nowhere in
    the delivered report. One review acquired 17 of 25 selected papers and
    another 18 of 30 with 12 paywalled; both mentioned it only in a Methods
    sentence and a Next step. A reader deciding how much weight to put on a tier
    needs to know how much of the intended corpus is missing, and the figure
    selection's own rejection counts say how much of the visual evidence was
    passed over and why.
    """
    stats = model.get("stats") or {}
    notes: list[str] = []

    selected = stats.get("papers_selected") or stats.get("papers_for_acquisition")
    retrieved = stats.get("papers_full_text")
    if selected and retrieved and int(selected) > int(retrieved):
        missing = int(selected) - int(retrieved)
        miss_rows = read_jsonl(root / "fulltext" / "not_retrieved.jsonl")
        miss_kinds = Counter(
            str(row.get("_not_retrieved_kind") or "unclassified")
            for row in miss_rows
        )
        ledger = model.get("corpus_ledger") or {}
        if not miss_kinds:
            miss_kinds.update(ledger.get("retrieval_classification") or {})
        classification = ""
        if miss_kinds:
            labels = {
                "paywalled": "confirmed closed/paywalled",
                "retrieval_failed": "still unavailable after transient retries",
                "unclassified": "unclassified",
            }
            classification = " Classification: " + ", ".join(
                f"{count} {labels.get(kind, kind)}"
                for kind, count in sorted(miss_kinds.items())
            ) + "."
        notes.append(
            f"Full text was obtained for {retrieved} of {selected} selected "
            f"papers; {missing} could not be retrieved and are reported as "
            "evidence gaps rather than as absent findings. No paywall was "
            f"bypassed.{classification}")

        missing_records = [
            row for row in (ledger.get("records") or [])
            if row.get("selected") and row.get("attempted")
            and not row.get("retrieved")
        ]
        if missing_records:
            details = "; ".join(
                f"{row.get('title') or row.get('paper_id')} "
                f"[{row.get('retrieval_kind') or 'unclassified'}]"
                for row in missing_records
            )
            notes.append(f"Unretrieved selected records: {details}.")

    ledger = model.get("corpus_ledger") or {}
    unused_full_texts = [
        row for row in (ledger.get("records") or [])
        if row.get("selected") and row.get("retrieved")
        and not row.get("evidence_bearing")
    ]
    if unused_full_texts:
        notes.append(
            "Retrieved full texts that yielded no accepted claim-grounding "
            "anchor: "
            + "; ".join(
                str(row.get("title") or row.get("paper_id"))
                for row in unused_full_texts
            )
            + "."
        )

    claims_total = stats.get("claims_drafted") or stats.get("claims_total")
    if claims_total and int(claims_total) > len(model.get("claims") or []):
        dropped = int(claims_total) - len(model["claims"])
        notes.append(
            f"{dropped} drafted claim(s) were dropped for want of a qualifying "
            "verbatim anchor rather than grounded on a weaker proxy. Delivered "
            "their canonical IDs remain reserved and are not reassigned to a "
            "different claim.")

    numbered_ids = sorted({
        int(match.group(1))
        for row in model.get("claims") or []
        if (match := re.fullmatch(r"C-(\d+)", str(row.get("claim_id") or "")))
    })
    if numbered_ids:
        present = set(numbered_ids)
        gaps = [value for value in range(numbered_ids[0], numbered_ids[-1] + 1)
                if value not in present]
        if gaps:
            labels = ", ".join(f"C-{value:03d}" for value in gaps[:12])
            if len(gaps) > 12:
                labels += f", and {len(gaps) - 12} more"
            notes.append(
                "Claim IDs are canonical across the PDF and machine-readable "
                f"artifacts. Reserved IDs not present in this report are {labels}; "
                "they were not reassigned to different claims."
            )

    searched_empty = model.get("searched_empty_axes") or []
    if searched_empty:
        notes.append(
            f"{len(searched_empty)} evidence axis/axes were searched but yielded "
            "no qualifying retained full-text anchor: "
            + ", ".join(str(row.get("axis_label") or row["axis"])
                        for row in searched_empty)
            + ". These are known negative-search outcomes, not omitted work."
        )

    manifest = read_json(
        root / "deliverables" / "figures_cited" / "figures_manifest.json", {}) or {}
    rejected = manifest.get("selection_rejected") or []
    if rejected:
        selection_counts = manifest.get("selection_counts") or {}
        causes = Counter(str(r.get("cause") or "unknown") for r in rejected)
        readable = {
            "schematic_not_data": "drawn schematics rather than data",
            "review_article": "figures from review articles",
            "below_relevance_floor": "captions not specific enough to the claim",
            "too_few_shared_terms": "captions sharing too little with the claim",
            "over_claim_cap": "beyond the per-claim figure cap",
            "over_paper_cap": "beyond the per-paper figure cap",
            "no_caption": "figures with no caption to match against",
            "no_caption_or_ocr": "figures with neither caption nor usable OCR text",
            "image_unavailable": "figure crops missing or not decodable before selection",
            "missing_visual_entailment": (
                "pairs lacking a passing direction/model/outcome visual check"
            ),
            "missing_visual_or_crop_entailment": (
                "pairs lacking a passing visual and complete-crop check"
            ),
            "partial_embedded_fragment": (
                "small unlabeled embedded-image fragments rejected as incomplete"
            ),
            "context_reserved_for_axis_coverage": (
                "context/model figures reserved for uncovered evidence axes"
            ),
        }
        parts = ", ".join(f"{n} {readable.get(cause, cause)}"
                          for cause, n in sorted(causes.items()))
        unique_figures = int(selection_counts.get("unique_figures_considered") or 0)
        candidate_pairs = int(selection_counts.get("candidate_pairs_considered") or 0)
        if not unique_figures:
            unique_figures = len({
                (str(row.get("paper_id") or ""), str(row.get("figure_id") or ""))
                for row in rejected
            } | {
                (str(row.get("paper_id") or ""), str(row.get("figure_id") or ""))
                for row in (model.get("figures") or [])
            })
        if not candidate_pairs:
            candidate_pairs = len(rejected) + int(selection_counts.get("chosen") or 0)
        notes.append(
            f"{len(model.get('figures') or [])} figure crops are shown; selection "
            f"evaluated {unique_figures} unique crops across {candidate_pairs} "
            f"claim–figure pairs. Rejected pairs were classified as {parts}.")

    axis_coverage = manifest.get("axis_coverage") or []
    if axis_coverage:
        covered = sum(
            bool(int(row.get("exported_figures", row.get("selected_figures")) or 0))
            for row in axis_coverage
        )
        gaps = [
            str(row.get("axis") or "") for row in axis_coverage
            if not int(row.get("exported_figures", row.get("selected_figures")) or 0)
        ]
        note = (
            f"Material paper figures cover {covered} of {len(axis_coverage)} "
            "evidence axes with eligible visuals selected adaptively"
        )
        if gaps:
            note += "; uncovered axes were " + ", ".join(gaps)
        recovered = int(manifest.get("coverage_ocr_recovered_figures") or 0)
        if recovered:
            note += (
                f". A second targeted OCR pass recovered text for {recovered} "
                "captionless figure(s)"
            )
        notes.append(note + ".")

    image_figures = int(stats.get("figure_images") or 0)
    if image_figures:
        attempted = int(stats.get("figure_ocr_attempted") or 0)
        completed = int(stats.get("figure_ocr_completed") or 0)
        missing_captions = int(stats.get("figure_caption_missing") or 0)
        inherited = int(stats.get("figure_caption_inherited") or 0)
        notes.append(
            f"Figure extraction produced {image_figures} image-backed crops; OCR "
            f"was attempted for {attempted} and completed or returned an honest "
            f"empty read for {completed}. {inherited} crop(s) inherited a unique "
            f"same-page parent caption, while {missing_captions} remain captionless."
        )

    directed = [fig for fig in model.get("figures") or []
                if fig.get("included_at_user_direction")]
    if directed:
        notes.append(
            f"{len(directed)} paper figure(s) were included at the user's "
            "explicit direction even though their recorded licence did not "
            "establish figure-reuse permission; each carries a rights notice "
            "and source link. User direction is recorded provenance, not a "
            "claim that the figure is openly licensed.")

    drifted = _scope_overreaches(model)
    if drifted:
        notes.append(
            "The stated scope of "
            + ", ".join(f"{cid} (names {', '.join(missing)})"
                        for cid, missing in drifted)
            + " names a model or construct that appears in none of that claim's "
            "quoted evidence. Read those claims' scope as the reviewer's framing "
            "of the question, not as a description of what was retrieved.")

    if not searched_through(model):
        # Absence used to be silent: the meta line simply omitted the date, and
        # because /CreationDate is pinned for determinism the document then
        # carried no date anywhere. Saying so is the difference between a known
        # gap and an invisible one.
        notes.append(
            "The search date could not be recovered from this run's manifest, so "
            "the report is undated. Treat the evidence as current only as of "
            "whenever the run was performed, and record `searched_through` in "
            "run_manifest.json on future runs.")

    weak = [row["display_id"] for row in model.get("claims") or []
            if row["support_state"] in WEAK_STATES]
    if weak:
        notes.append(
            f"{len(weak)} claim(s) rest on indirect, conflicted or single-source "
            f"evidence ({', '.join(weak)}) and their wording is hedged "
            "accordingly. A support tier describes what this review read, not "
            "what is true: where the field's foundational primary reports were "
            "not retrievable, the tier is lower than the underlying science.")
    return notes


def _scope_overreaches(model: dict) -> list[tuple[str, list[str]]]:
    """Claims whose scope names a system none of their evidence mentions.

    Reported, not blocked. A scope is the reviewer's statement of what the claim
    is about, and it may legitimately be broader than any single quote — but when
    it names something as specific as "P301S" and no anchor mentions it, the
    field is describing evidence the review does not hold. That shipped: a claim
    scoped to "Mouse tauopathy models (P301S)" rested entirely on
    targeted-replacement mice because the P301S study was paywalled.
    """
    from anchor_policy import scope_overreach

    out: list[tuple[str, list[str]]] = []
    for row in model.get("claims") or []:
        scope = str(row.get("scope") or "")
        if not scope:
            continue
        texts = [str(a.get("quote") or "")
                 for a in row["supporting"] + row["contradicting"]]
        texts += [str((row.get("narrative") or {}).get(facet, {}).get("text", ""))
                  for facet in NARRATIVE_FACET_KEYS]
        missing = scope_overreach(scope, texts)
        if missing:
            out.append((row.get("display_id") or row["claim_id"],
                        sorted(missing)))
    return out


def _pid(value: Any) -> str:
    """Canonical paper_id. Both sides of every join must go through this."""
    return str(value if value is not None else "").strip()


def _enrich_anchors(anchors: list[dict], refs_by_id: dict) -> list[dict]:
    """Attach the author-year citation each anchor needs to be rendered.

    ``grounded_quotes.json`` carries ``doi``/``title``/``year`` per anchor but
    not ``authors``, so an anchor alone cannot produce "Ward et al. 2024" — and
    that is exactly why both renderers fell back to printing the raw DOI. The
    reference record supplies the missing field; the anchor's own values win on
    conflict, since they came from the row that was actually quoted.
    """
    out: list[dict] = []
    for anchor in anchors:
        ref = refs_by_id.get(_pid(anchor.get("paper_id")), {})
        merged = {**ref, **{k: v for k, v in anchor.items() if v not in (None, "")}}
        enriched = enrich_anchor(anchor, ref)
        enriched["citation"] = _citation(merged)
        enriched["journal"] = clean_journal(str(merged.get("journal") or ""))
        enriched["url"] = (anchor.get("url") or ref.get("url")
                           or (f"https://doi.org/{merged.get('doi')}"
                               if merged.get("doi") else ""))
        out.append(enriched)
    return out


# A Crossref-minted DOI embeds the registration year for most large publishers
# (10.1038/s41467-024-49028-z, 10.1002/alz.13703). When it does and it
# contradicts the record's ``year`` by more than a year, one of the two is
# wrong — a shipped report cited "Xia et al. 2021" for
# 10.1038/s41467-024-49028-z, a 2024 paper, and repeated the wrong year in its
# synthesis table.
# Springer Nature's suffix is ``s<journal>-<yy y>-<article>``, where the middle
# group is the LAST THREE digits of the year: s41467-024-49028-z is 2024,
# s00401-998-... is 1998. A handful of publishers use a full four-digit year.
_DOI_YEAR = re.compile(r"/s\d{4,5}-(\d{3,4})-\d", re.IGNORECASE)


def doi_year(doi: str) -> str:
    """The year embedded in a DOI suffix, or "" when the pattern is absent.

    Only Springer Nature's scheme is decoded. Other registrants embed no year
    at all (10.1093/brain/awn352, 10.1002/alz.13703) and guessing from them
    would manufacture false conflicts.
    """
    match = _DOI_YEAR.search(str(doi or ""))
    if not match:
        return ""
    digits = match.group(1)
    if len(digits) == 4:
        return digits if "1900" <= digits <= "2099" else ""
    # Three digits: 0xx-1xx are 2000s, 9xx are 1900s.
    century = "19" if digits[0] == "9" else "20"
    year = f"{century}{digits[1:]}"
    return year if "1900" <= year <= "2099" else ""


def _reference_list(refs: list[dict], evidence: list[dict],
                    claim_order: list[str] | None = None,
                    ) -> tuple[list[dict], list[str]]:
    """(cited references in first-citation order, errors).

    An entry that grounds nothing is dropped rather than listed: a reference
    list that disagrees with the body is a defect (the older report listed a
    bioRxiv paper it never cited).

    "First-citation order" means the order a READER meets the sources, which is
    the order the Results section renders its claims — not the row order of
    evidence.jsonl. Those are different, and the shipped report proved it: its
    reference 1 was a paper first cited by the second-to-last claim, while the
    paper grounding C-001 sat at number 10. ``claim_order`` is the rendered
    claim sequence; evidence rows are visited claim by claim within it, and any
    paper cited by no rendered claim falls back to file order at the end.

    Two rules the earlier version broke, both of which silently corrupted the
    list rather than reporting anything:

    * FILTER BEFORE NUMBERING. ``enumerate`` used to run over the citation
      order and the un-listed entries were skipped inside the loop, so a corpus
      missing one reference produced indices ``1, 3`` — visible numbering holes
      pointing at nothing.
    * A cited paper with no reference record is an ERROR, not a silent drop:
      the body cites a source the reader cannot look up. It is returned for the
      caller to surface, because only the caller knows whether to abort.
    """
    cited = [row for row in evidence
             if row.get("stance") in {"supports", "contradicts"}]
    by_claim: dict[str, list[dict]] = defaultdict(list)
    for row in cited:
        by_claim[str(row.get("claim_id") or "")].append(row)

    order: list[str] = []
    def _take(rows: list[dict]) -> None:
        for row in rows:
            pid = _pid(row.get("paper_id"))
            if pid and pid not in order:
                order.append(pid)

    for cid in (claim_order or []):
        _take(by_claim.get(cid, []))
    _take(cited)  # anything cited by no rendered claim, in file order

    by_id = {_pid(r.get("paper_id")): r for r in refs}

    errors: list[str] = []
    cited_with_refs: list[tuple[str, dict]] = []
    for pid in order:
        ref = by_id.get(pid)
        if not ref:
            errors.append(
                f"cited paper {pid!r} has no record in corpus/references.jsonl — "
                "the body cites a source the reference list cannot show; add it "
                "to references.jsonl or drop the evidence rows that cite it"
            )
            continue
        cited_with_refs.append((pid, ref))

    out = []
    for i, (pid, ref) in enumerate(cited_with_refs, 1):
        doi = str(ref.get("doi") or "")
        year = str(ref.get("year") or "").strip()
        embedded = doi_year(doi)
        if year and embedded and abs(int(embedded) - int(year)) > 1:
            errors.append(
                f"reference {pid!r} is dated {year} but its DOI encodes "
                f"{embedded} ({doi}) — one of the two is wrong, and the year "
                "propagates into every inline citation of this paper")
        out.append({
            "index": i,
            "paper_id": pid,
            "authors": format_authors(ref.get("authors")),
            "year": year,
            "title": clean_title(str(ref.get("title") or "")),
            "journal": clean_journal(str(ref.get("journal") or "")),
            "doi": doi,
            "url": ref.get("url") or (f"https://doi.org/{doi}" if doi else ""),
            "citation": _citation(ref),
        })
    return out, errors


_TITLE_ARTIFACTS = [
    r"\s*[-–]\s*PMC\s*$",
    r"\s*\|\s*Journal of [^|]+\s*\|\s*Springer Nature Link\s*$",
    r"\s*\|\s*Springer Nature Link\s*$",
    r"\s*[-–]\s*PubMed\s*$",
    r"\s*\|\s*Nature\s*$",
    r"\s*[-–]\s*ScienceDirect\s*$",
    # The general case the specific patterns above kept missing: a scraped
    # <title> ends with the site or journal name after a pipe. A shipped
    # reference read "...in an isoform-dependent manner | Communications
    # Chemistry", with the journal then repeated in the journal field.
    r"\s*\|\s*[^|]{1,60}\s*$",
]


def clean_title(title: str) -> str:
    """Strip scraped page-chrome and unescape entities in a reference title.

    Also normalises terminal punctuation: records harvested from PubMed carry a
    trailing period that, followed by the renderer's own, produced "...molecular
    mechanisms.." in the shipped list.
    """
    import html

    out = html.unescape(title).strip()
    for pattern in _TITLE_ARTIFACTS:
        out = re.sub(pattern, "", out, flags=re.IGNORECASE)
    # A single trailing period is PubMed's, and doubling it with the renderer's
    # own produced "...molecular mechanisms..". An ellipsis is the author's and
    # must survive, so the period is only stripped when no period precedes it.
    return re.sub(r"(?<![.\s])\.\s*$", "", out.strip()).strip()


# Journal strings arrive as whatever the metadata source called them, so one
# reference list carried "Nature aging", "Cell reports", "Npj Dementia" and
# "Brain : a journal of neurology" side by side. Casing is normalised to title
# case with the known stylings preserved, and NLM's " : subtitle" suffix — which
# is cataloguing metadata, not the journal's name — is dropped.
_JOURNAL_EXACT = {
    "npj dementia": "npj Dementia",
    "nature aging": "Nature Aging",
    "cell reports": "Cell Reports",
    "elife": "eLife",
    "brain": "Brain",
    "neuron": "Neuron",
    "nature": "Nature",
    "science": "Science",
    "nature medicine": "Nature Medicine",
    "nature neuroscience": "Nature Neuroscience",
    "nature communications": "Nature Communications",
    "molecular neurodegeneration": "Molecular Neurodegeneration",
    "jama neurology": "JAMA Neurology",
    "eneuro": "eNeuro",
}
_JOURNAL_MINOR = {"and", "of", "the", "in", "for", "on", "a", "an", "&"}


def clean_journal(journal: str) -> str:
    """A consistently styled journal name.

    The NLM cataloguing suffix uses a SPACE-padded colon ("Brain : a journal of
    neurology"); a journal whose real name contains a colon does not
    ("Alzheimer's & Dementia: TRCI"). Requiring the leading space keeps the
    second intact.
    """
    name = re.sub(r"\s+:\s.*$", "", str(journal or "").strip()).strip()
    if not name:
        return ""
    exact = _JOURNAL_EXACT.get(name.lower())
    if exact:
        return exact
    words = name.split()
    return " ".join(
        w if (i and w.lower() in _JOURNAL_MINOR)
        # An all-caps token is an acronym (PNAS, JAMA); leave it alone.
        else w if w.isupper()
        else w[:1].upper() + w[1:]
        for i, w in enumerate(words))


def format_authors(authors: Any, max_shown: int = 3) -> str:
    """A consistent author string: "Surname Initials, …, et al.".

    The shipped list mixed three conventions across thirteen entries — full
    given names separated by semicolons, full given names separated by commas
    with a trailing "et al.", and surname-plus-initials for eight authors with
    no "et al." at all. Reference lists are read by scanning; three formats in
    one list defeats scanning.
    """
    names = _author_list(authors)
    if not names:
        return ""
    # "A, B, C, et al." states that the list is already truncated. _author_list
    # drops the "et al." token, so without this the record's own truncation
    # marker is lost and three of forty authors read as the complete list.
    truncated_at_source = bool(
        re.search(r"\bet\s+al\.?\s*$", str(authors or ""), re.IGNORECASE))
    shown = [s for s in (_format_one_author(n) for n in names[:max_shown]) if s]
    if not shown:
        return ""
    more = truncated_at_source or len(names) > len(shown)
    return ", ".join(shown) + (", et al." if more else "")


def _format_one_author(name: str) -> str:
    """"Jiasheng Zhang" / "Zhang JC" / "Zhang, Jiasheng" -> "Zhang J" / "Zhang JC".

    An already-abbreviated token such as "JC" or "M.P." is a complete initials
    block and is kept whole; taking its first letter dropped the middle initial
    and turned "Rojas JC" into "Rojas J".
    """
    surname = _surname(name)
    if not surname:
        return ""
    initials: list[str] = []
    for part in name.replace(",", " ").split():
        bare = part.replace(".", "")
        if not bare or bare.lower() == surname.lower() or not bare[0].isalpha():
            continue
        initials.append(bare.upper() if bare.isupper() and len(bare) <= 3
                        else bare[0].upper())
    return f"{surname} {''.join(initials)}".strip()
