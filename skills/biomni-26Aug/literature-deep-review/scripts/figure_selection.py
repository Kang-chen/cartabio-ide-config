#!/usr/bin/env python3
"""Choose which paper figures a claim should show, from the claim's own sources.

**The defect this replaces.** A figure used to be exported only when an accepted
evidence row's ``block_type`` was ``caption`` or ``figure_ocr`` — that is, only
when the reviewer happened to quote the figure's legend. Figure selection was a
side effect of quote-type selection, and three consequences followed:

1. **Starvation.** A claim grounded on three excellent Results *sentences* got
   no figure at all, even when the paper contained a figure showing exactly that
   result. Two shipped reports turned 45 and 29 verbatim quotes into 6 figures.
2. **Clustering.** One paper whose captions were quoted three times contributed
   three figures to a single claim, while four other evidence axes got none.
3. **Irrelevance.** A caption quoted for its wording dragged its figure along
   regardless of what the figure showed. One report embedded a review article's
   "candidate therapeutic strategies" schematic under a claim about lipid and
   cholesterol metabolism — a figure that supports nothing, from a paper that
   measured nothing.

**What replaces it.** For each claim, look at the figures of the papers that
claim ALREADY cites, score each caption against the claim text, and take the
best few that clear a relevance floor. A quoted caption is considered first but
must pass the same semantic, subject, role, and crowding gates as every other
candidate. Quoting a caption is no longer the only route in or a relevance
bypass.

Selection is deterministic: every ordering is a stable sort on
``(-score, paper_id, figure_id)``, and no random tie-break exists.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

# Selection policy. Overridable from ``report_contract.json``; the defaults are
# here so the module is usable and testable without one.
# KEEP IN SYNC with paper_figures.selection in templates/report_contract.json.
# The contract wins in production (``policy_from_contract`` merges over these),
# so a drift here is invisible in a real run and changes behaviour under any test
# that passes DEFAULT_POLICY directly — which is how a per-paper-cap rejection
# came to be reported as a per-claim-cap rejection.
DEFAULT_POLICY = {
    # Most figures shown under any one claim.
    "max_per_claim": 4,
    # Most figures one paper may contribute to one claim. The SLC33A1 report
    # showed two figures from each of two papers under one indirect claim while
    # most claims had none; the first figure from a paper carries its result,
    # and a second needs an explicit custom policy.
    "max_per_paper_per_claim": 1,
    # A caption must reach this similarity to the claim before it counts as
    # illustrating it. Calibrated on figure/claim pairs from the two shipped
    # reports: the lowest true positive (Jackson 2021 Fig. 4 under the
    # blood-brain-barrier claim) scores 0.17, and every true negative scores 0.
    "min_relevance": 0.15,
    # ...AND it must share at least this many distinct terms with the claim.
    # The score alone cannot separate "Figure 2 Brain sections." from a real
    # match: one common word shared with a claim that mentions the brain scores
    # 0.27, above three genuine matches. Counting distinct shared terms does
    # separate them — the vague caption shares exactly one, every true positive
    # shares three.
    "min_shared_terms": 2,
    # Figures from review articles are excluded by default: a review's figures
    # are drawn, not measured, so they cannot be primary evidence for a claim.
    "allow_review_figures": False,
    # Source models and review diagrams may illustrate an otherwise uncovered
    # decision axis, but their role is explicit and they never affect the
    # evidence-derived support tier.
    "allow_context_figures": False,
    # Coverage may redistribute an eligible figure; it may never lower the
    # semantic floor merely to fill an empty axis.
    "coverage_min_relevance": 0.15,
    "coverage_min_shared_terms": 2,
    # Runtime contracts enable this. Direct unit users may opt in while legacy
    # callers migrate to evidence/figure_entailment.jsonl.
    "require_pair_verification": True,
}

# Caption language that marks a drawn schematic rather than a result. These
# figures are legitimate in a review and worthless as evidence for a claim: they
# report no measurement, so nothing in them can support or contradict anything.
_SCHEMATIC_RE = re.compile(
    r"\b(?:schematic|schema|overview|summary\s+(?:of|diagram)|graphical\s+abstract"
    r"|proposed\s+(?:model|mechanism)|working\s+model|conceptual\s+model"
    r"|therapeutic\s+strategies|treatment\s+strategies|study\s+design"
    r"|flow\s?chart|flow\s+diagram|consort|timeline\s+of\s+the\s+study"
    # A figure that COMPARES published systems is surveying the literature, not
    # reporting a measurement. A review schematic captioned "Comparison of
    # patients carrying GRN mutations with rodent and human-derived models"
    # reached page 6 of a shipped report as though it were primary data.
    r"|comparison\s+of\s+\w+(?:\s+\w+){0,4}\s+(?:with|and|versus|vs\.?)"
    r"|current\s+(?:understanding|knowledge|model)|landscape\s+of"
    r"|(?:key|main)\s+(?:features|characteristics)\s+of"
    r"|(?:mechanistic|pathophysiological|disease)\s+model"
    r"|(?:^|[.:]\s*)model(?:\s+(?:of|for)\b|\s*$))\b",
    re.IGNORECASE)

# A caption that cites several other papers is describing collected literature.
# Primary figures cite at most a method source; they do not carry "[6, 37, 88,
# 118, 119]". This catches the review figure whose paper never recorded a
# study_type, which is the usual reason is_review_paper() misses one.
_CAPTION_CITATIONS = re.compile(r"\[(\d{1,3}(?:\s*[,;–-]\s*\d{1,3}){2,})\]")

# A multi-panel figure may begin with a workflow/schema panel and then present
# the paper's actual measurements. Treating one occurrence of "schematic" as a
# whole-figure veto dropped SLC33A1 western blots, tumor-response curves, and
# quantification panels. These are strong measurement markers, not merely words
# such as "result" or "analysis" that also occur in conceptual diagrams.
_MEASUREMENT_RE = re.compile(
    r"\b(?:western\s+blot|immunoblot|densitometric|quantif(?:ication|ied|y)"
    r"|tumou?r\s+volume|survival(?:\s+curve)?|kaplan[- ]meier|dose[- ]response"
    r"|flow\s+cytometry|microscopy|micrograph|cryo-?em\s+map|heat\s*map"
    r"|rna\s+sequencing|single[- ]cell|volcano\s+plot|box\s*plot|bar\s*(?:plot|graph)"
    r"|mean\s*(?:\+/-|±)\s*(?:sem|sd)|p\s*[<=>]\s*0?\.\d+"
    r"|n\s*=\s*\d+|independent\s+experiments?)\b",
    re.IGNORECASE,
)

# Study-type values that mark a paper whose figures are not primary data.
_REVIEW_TYPES = {"review", "systematic review", "meta-analysis", "narrative review",
                 "perspective", "commentary", "editorial"}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}")

# Words that appear in nearly every biomedical claim and so distinguish nothing.
_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "has", "have", "had", "not", "but", "its", "their", "which", "than", "then",
    "into", "onto", "via", "per", "also", "more", "most", "less", "least",
    "causes", "cause", "caused", "causing", "shows", "show", "shown",
    "levels", "level", "study", "studies", "patients", "human", "humans",
    "mice", "mouse", "model", "models", "data", "results", "figure", "fig",
    "using", "used", "both", "either", "rather", "such", "including",
}


# Suffixes stripped before matching, longest first. Biomedical captions and
# claims describe the same thing in different parts of speech constantly —
# "progranulin deficiency" in the claim, "progranulin-deficient" in the caption
# — and exact string matching scored that pair at zero.
_SUFFIXES = ("iveness", "ization", "isation", "ational", "ically", "ation",
             "ently", "ency", "ance", "ancy", "ible", "able", "ness", "ment",
             "ing", "ies", "ent", "ant", "ive", "ial", "al", "ic", "ed", "es",
             "s", "y")

# Negating/intensifying prefixes. "dysfunction" and "function" are the same
# concept for relevance purposes; so are "hypomethylation" and "methylation".
_PREFIXES = ("dys", "hyper", "hypo", "non", "un", "over", "under", "anti")

_MIN_STEM = 4


def _stem(word: str) -> str:
    """A crude stem good enough to relate a noun to its adjective."""
    stem = word.lower()
    for prefix in _PREFIXES:
        if stem.startswith(prefix) and len(stem) - len(prefix) >= _MIN_STEM:
            stem = stem[len(prefix):]
            break
    for suffix in _SUFFIXES:
        if stem.endswith(suffix) and len(stem) - len(suffix) >= _MIN_STEM:
            return stem[: -len(suffix)]
    return stem


def _terms(text: str) -> set[str]:
    """Distinctive stems in a string.

    Hyphenated compounds contribute their PARTS as well as the whole:
    ``progranulin-deficient`` has to match a claim that says ``progranulin``,
    and treating it as one opaque token is why a caption naming the claim's
    own subject scored as unrelated.
    """
    out: set[str] = set()
    for match in _WORD_RE.findall(str(text or "")):
        pieces = [match] + (match.split("-") if "-" in match else [])
        for piece in pieces:
            lowered = piece.lower()
            if len(piece) > 2 and lowered not in _STOP:
                out.add(_stem(lowered))
    return {t for t in out if len(t) >= 3}


def caption_relevance(caption: str, claim_text: str,
                      scope: str = "", corpus_df: Counter | None = None,
                      n_docs: int = 0) -> float:
    """How strongly a caption and a claim are about the same thing. 0..1.

    The geometric mean of two coverages: how much of the claim the caption
    accounts for, and how much of the caption the claim accounts for. Claim
    coverage alone punishes length — "Astrocyte-specific knockout of APOE4
    rescues BBB impairments" is exactly the right figure for a claim about
    astrocytic APOE4 and the blood-brain barrier, but the claim is a 25-word
    sentence and the caption can only ever cover a fraction of it. Caption
    coverage alone is worse: a two-word caption naming one common term would
    score 1.0. Requiring both keeps a short on-point caption while rejecting a
    short vague one.

    Terms are weighted by inverse document frequency when a corpus profile is
    supplied, so matching on "progranulin" counts for more than matching on
    "brain" — without which every caption from a paper about the right organ
    looked equally relevant.
    """
    claim_terms = _terms(claim_text) | _terms(scope)
    caption_terms = _terms(caption)
    if not claim_terms or not caption_terms:
        return 0.0

    def weight(term: str) -> float:
        if not corpus_df or not n_docs:
            return 1.0
        # Smoothed IDF, floored so a common term still counts for something.
        return max(0.25, math.log((n_docs + 1) / (corpus_df.get(term, 0) + 1)))

    shared = claim_terms & caption_terms
    if not shared:
        return 0.0
    matched = sum(weight(t) for t in shared)
    claim_total = sum(weight(t) for t in claim_terms)
    caption_total = sum(weight(t) for t in caption_terms)
    if claim_total <= 0 or caption_total <= 0:
        return 0.0
    return math.sqrt((matched / claim_total) * (matched / caption_total))


def shared_terms(caption: str, claim_text: str, scope: str = "") -> set[str]:
    """The distinct stems a caption and a claim have in common."""
    return _terms(caption) & (_terms(claim_text) | _terms(scope))


def surface_form(stem: str, *texts: str) -> str:
    """A readable word for a stem, taken from the text the stem came from.

    Stems are for matching, not for reading. Printing them put
    "frontotempor, heterozygou, lysosom, defici" into a figure caption where the
    reader needed "frontotemporal, heterozygous, lysosomal, deficient". The
    shortest matching surface form is chosen, since the longer ones are usually
    inflected or hyphenated variants of the same word.
    """
    candidates: set[str] = set()
    for text in texts:
        for match in _WORD_RE.findall(str(text or "")):
            for piece in ([match] + match.split("-") if "-" in match else [match]):
                if len(piece) > 2 and _stem(piece.lower()) == stem:
                    candidates.add(piece.lower())
    if not candidates:
        return stem
    return min(sorted(candidates), key=len)


def shared_term_words(caption: str, claim_text: str, scope: str = "") -> list[str]:
    """The shared vocabulary as words a reader recognises, alphabetically."""
    stems = shared_terms(caption, claim_text, scope)
    return sorted({surface_form(s, caption, claim_text, scope) for s in stems})


def is_schematic(caption: str) -> bool:
    """True when the caption describes a drawing rather than a measurement."""
    text = str(caption or "")
    return bool(_SCHEMATIC_RE.search(text)) or bool(_CAPTION_CITATIONS.search(text))


def is_review_paper(ref: dict, caption: str = "") -> bool:
    """True when this figure's paper is a review rather than a primary report.

    ``study_type`` is the reliable signal when the corpus recorded one, and it
    frequently does not — a preprint or a journal whose metadata omits it lands
    here as an empty string. The caption is the fallback: a figure legend citing
    a cluster of other papers belongs to a survey of the literature whatever the
    metadata says.
    """
    study_type = str((ref or {}).get("study_type") or "").strip().lower()
    if any(marker in study_type for marker in _REVIEW_TYPES):
        return True
    return bool(_CAPTION_CITATIONS.search(str(caption or "")))


def figure_role(ref: dict, caption: str = "") -> str:
    """Reader-facing role; only ``primary_data`` can depict original results."""
    if is_review_paper(ref, caption):
        return "review_context"
    if is_schematic(caption):
        if _MEASUREMENT_RE.search(str(caption or "")):
            return "primary_data"
        return "source_model"
    return "primary_data"


@dataclass
class FigureChoice:
    """One figure selected for one claim, with why it was selected."""
    paper_id: str
    figure_id: str
    claim_id: str
    reason: str            # quoted_caption | claim_match | figure_ocr_match | coverage_axis_match
    relevance: float = 0.0
    role: str = "primary_data"
    relationship: str = "direct"  # direct | illustrative
    pair_verification: str = ""  # accepted_figure_anchor | visual_entailment


@dataclass
class SelectionReport:
    """What was chosen, and — just as importantly — what was passed over.

    A selection step that silently drops candidates reads, downstream, exactly
    like a corpus that never had them. Rejections are claim–figure events, so
    unique crops and pair counts are reported separately.
    """
    chosen: list[FigureChoice] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    axis_coverage: list[dict] = field(default_factory=list)

    def by_figure(self) -> dict[tuple[str, str], list[FigureChoice]]:
        out: dict[tuple[str, str], list[FigureChoice]] = defaultdict(list)
        for choice in self.chosen:
            out[(choice.paper_id, choice.figure_id)].append(choice)
        return out

    def counts(self) -> dict[str, int]:
        causes = Counter(r["cause"] for r in self.rejected)
        unique_by_cause = {
            cause: len({
                (str(row.get("paper_id") or ""), str(row.get("figure_id") or ""))
                for row in self.rejected if row.get("cause") == cause
            })
            for cause in causes
        }
        events = [
            (choice.claim_id, choice.paper_id, choice.figure_id)
            for choice in self.chosen
        ] + [
            (str(row.get("claim_id") or ""), str(row.get("paper_id") or ""),
             str(row.get("figure_id") or ""))
            for row in self.rejected
        ]
        return {"chosen": len(self.chosen),
                "claims_with_figures": len({c.claim_id for c in self.chosen}),
                "candidate_pairs_considered": len(set(events)),
                "unique_figures_considered": len({(pid, fid) for _, pid, fid in events}),
                "source_papers_considered": len({pid for _, pid, _ in events}),
                **{f"rejected_{k}": v for k, v in sorted(causes.items())},
                **{f"rejected_pair_{k}": v for k, v in sorted(causes.items())},
                **{f"rejected_unique_{k}": v
                   for k, v in sorted(unique_by_cause.items())}}


def _visual_entailment(record: dict, claim_id: str) -> dict | None:
    """A blinded/native visual check attached to this exact claim/figure pair.

    The verdict booleans are NOT one undifferentiated set. The SCIENTIFIC
    verdicts — whether the figure actually supports the claim — are fatal: any
    failure disqualifies the pair, exactly as a claim/figure mismatch should.
    The COSMETIC verdicts (legibility and label quality) are advisory: a crop
    that is merely hard to read is a quality note, not grounds to silently drop
    a scientifically valid figure. Treating the two identically is what let a
    legibility failure and a support failure be handled the same way.
    """
    fatal = (
        "entails", "direction_match", "model_match", "outcome_match",
        "subject_match",
    )
    cosmetic = ("crop_complete", "labels_legible", "no_page_contamination")
    metadata = ("reviewer", "rationale")
    required = fatal + cosmetic + metadata
    for row in record.get("claim_entailments") or []:
        if str(row.get("claim_id") or "") != claim_id:
            continue
        # Every verdict field must be present, or the pair cannot be judged.
        if not all(key in row for key in required):
            continue
        # Scientific support is fatal: a failure here means the figure does not
        # establish the claim, so this pair is disqualified.
        if not all(row.get(key) is True for key in fatal):
            continue
        # Cosmetic checks are advisory: surfaced, never a reason to drop a
        # scientifically valid figure.
        advisories = [key for key in cosmetic if row.get(key) is not True]
        if advisories:
            row = {**row, "cosmetic_advisories": advisories}
        return row
    return None


def _selection_text(record: dict) -> tuple[str, str]:
    """Best available figure text and the provenance of that text."""
    caption = str(record.get("caption") or "").strip()
    ocr_text = " ".join(
        str(row.get("text") or "").strip()
        for row in (record.get("ocr") or [])
        if isinstance(row, dict) and str(row.get("text") or "").strip()
    )
    if caption and ocr_text:
        return f"{caption} {ocr_text}", "caption+ocr"
    if caption:
        return caption, "caption"
    return ocr_text, "ocr"


_ALIAS_SPLIT_RE = re.compile(r"\s*(?:/|;|\||\bor\b|\balso known as\b)\s*",
                             re.IGNORECASE)
_ALIAS_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def subject_aliases_from_manifest(manifest: dict) -> tuple[str, ...]:
    """Resolve the review subject names that a direct figure must identify.

    A claim can share generic outcome words (cell death, survival, response)
    with an unrelated panel from the same paper.  The run-level subject is the
    stable discriminator.  Explicit aliases win, with the short and long
    subject names retained as compatible fallbacks for existing manifests.
    """
    raw: list[str] = []
    configured = manifest.get("subject_aliases") or []
    if isinstance(configured, str):
        configured = [configured]
    raw.extend(str(value) for value in configured if str(value).strip())
    for key in ("subject", "subject_long"):
        value = str(manifest.get(key) or "").strip()
        if value:
            raw.extend(_ALIAS_SPLIT_RE.split(value))
    aliases: list[str] = []
    for value in raw:
        alias = " ".join(value.split()).strip(" ,.-")
        normalized = _ALIAS_NORMALIZE_RE.sub("", alias.casefold())
        if len(normalized) >= 3 and alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def _all_figure_text(record: dict) -> str:
    """Caption plus OCR, because either can carry the target identity."""
    caption = str(record.get("caption") or "").strip()
    ocr = " ".join(
        str(row.get("text") or "").strip()
        for row in (record.get("ocr") or [])
        if isinstance(row, dict) and str(row.get("text") or "").strip()
    )
    return " ".join(value for value in (caption, ocr) if value)


def names_subject(record: dict, aliases: Iterable[str]) -> bool:
    """Whether the visible figure text identifies the review subject."""
    text = _all_figure_text(record).casefold()
    if not text:
        return False
    for alias in aliases:
        parts = re.findall(r"[a-z0-9]+", str(alias).casefold())
        if len("".join(parts)) < 3:
            continue
        body = r"[\s_-]*".join(re.escape(part) for part in parts)
        if re.search(rf"(?<![a-z0-9]){body}(?![a-z0-9])", text):
            return True
    return False


def _figure_priority(row: dict) -> bool:
    """Parse the CSV/JSON boolean without treating the string 'false' as true."""
    value = row.get("figure_priority")
    if isinstance(value, bool):
        return value
    if value is None or str(value).strip() == "":
        return False
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(
        f"claim {row.get('claim_id') or '<unknown>'} has invalid "
        f"figure_priority={value!r}; expected true or false"
    )


def select(claims: list[dict], evidence: list[dict],
           figures: dict[tuple[str, str], dict],
           refs_by_id: dict[str, dict] | None = None,
           policy: dict | None = None,
           quoted: Iterable[tuple[str, str, str]] = (),
           subject_aliases: Iterable[str] = (),
           ) -> SelectionReport:
    """Pick the figures each claim should show.

    ``figures`` maps ``(paper_id, figure_id)`` to a parsed record with a caption
    or OCR text. ``quoted`` lists ``(paper_id, figure_id, claim_id)``
    triples whose caption or in-figure text was itself an accepted anchor. They
    are considered first but must still pass semantic scoring, the subject gate,
    role policy, and crowding caps.
    """
    settings = {**DEFAULT_POLICY, **(policy or {})}
    refs_by_id = refs_by_id or {}
    report = SelectionReport()
    subject_aliases = tuple(subject_aliases)
    # A missing input must never relax a check. Pair verification defaults ON
    # (see DEFAULT_POLICY); when it is on, the subject aliases it depends on
    # must be present. An empty alias set is a fail-loud error, not a silent
    # skip that turns the requirement off.
    require_pair_verification = bool(settings.get("require_pair_verification"))
    if require_pair_verification and not subject_aliases:
        raise ValueError(
            "require_pair_verification is enabled but no subject aliases were "
            "resolved; a missing alias set must not silently disable figure "
            "pair verification. Provide subject_aliases (or a manifest subject "
            "from which they can be derived), or explicitly set "
            "require_pair_verification=False in the selection policy."
        )

    # Which papers does each claim actually cite? A figure may only be shown
    # under a claim whose evidence already rests on that paper; anything else
    # would introduce a source the claim does not otherwise use.
    papers_by_claim: dict[str, set[str]] = defaultdict(set)
    for row in evidence:
        if row.get("stance") not in {"supports", "contradicts"}:
            continue
        cid, pid = str(row.get("claim_id") or ""), str(row.get("paper_id") or "")
        if cid and pid:
            papers_by_claim[cid].add(pid)

    # Corpus term profile for IDF, over the captions actually in play.
    doc_terms = [_terms(_selection_text(rec)[0]) for rec in figures.values()]
    corpus_df: Counter = Counter()
    for terms in doc_terms:
        corpus_df.update(terms)
    n_docs = len(doc_terms)

    quoted_by_claim: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for pid, fid, cid in quoted:
        quoted_by_claim[str(cid)].add((str(pid), str(fid)))

    for claim in claims:
        cid = str(claim.get("claim_id") or "")
        if not cid:
            continue
        chosen: list[FigureChoice] = []
        per_paper: Counter = Counter()
        # Match the figure to the atomic proposition itself.  Accepted quotes
        # often contain downstream outcomes shared by unrelated panels in the
        # same paper (cell death, survival, response); adding them here is how
        # an ATG9A panel and a KEAP1/NRF2 CAR-T panel were selected for SLC33A1.
        context_text = " ".join(
            str(value or "")
            for value in (claim.get("claim_text"), claim.get("scope"))
        )

        # 1. Quoted captions first, but with no semantic privilege. A caption
        #    can be valid textual evidence for one proposition while the image
        #    depicts a different proposition. The SLC33A1 structure image was
        #    attached to a historical-modulator claim through exactly that
        #    mismatch, so quote acceptance can never substitute for a
        #    claim-to-figure relevance check.
        for pid, fid in sorted(quoted_by_claim.get(cid, ())):
            record = figures.get((pid, fid))
            if record is None:
                continue
            figure_text, _text_source = _selection_text(record)
            if not figure_text:
                report.rejected.append({
                    "claim_id": cid, "paper_id": pid, "figure_id": fid,
                    "cause": "no_caption_or_ocr", "quoted": True,
                })
                continue
            if subject_aliases and not names_subject(record, subject_aliases):
                report.rejected.append({
                    "claim_id": cid, "paper_id": pid, "figure_id": fid,
                    "cause": "missing_subject_anchor", "quoted": True,
                })
                continue
            score = caption_relevance(
                figure_text, context_text, "", corpus_df, n_docs
            )
            shared = shared_terms(figure_text, context_text, "")
            if len(shared) < settings["min_shared_terms"]:
                report.rejected.append({
                    "claim_id": cid, "paper_id": pid, "figure_id": fid,
                    "cause": "too_few_shared_terms", "quoted": True,
                    "shared": sorted(shared),
                })
                continue
            if score < settings["min_relevance"]:
                report.rejected.append({
                    "claim_id": cid, "paper_id": pid, "figure_id": fid,
                    "cause": "below_relevance_floor", "quoted": True,
                    "relevance": round(score, 4),
                })
                continue
            if require_pair_verification and _visual_entailment(record, cid) is None:
                report.rejected.append({
                    "claim_id": cid,
                    "paper_id": pid,
                    "figure_id": fid,
                    "cause": "missing_visual_or_crop_entailment",
                    "quoted": True,
                    "relevance": round(score, 4),
                })
                continue
            # Quoted captions are subject to the crowding caps too. They were
            # exempt, and that is how clustering came back: four figures from Ma
            # 2018 under one APOE claim, three from Liraz under another, three
            # from Kurnellas in the GRN review — while most claims had none. A
            # quoted caption earns the figure a place in the queue, not an
            # unbounded number of places.
            # Per-PAPER first: it is the more specific diagnosis, and when both
            # caps would fire the operator needs to know one source is crowding
            # the claim rather than that the claim is simply full.
            if per_paper[pid] >= settings["max_per_paper_per_claim"]:
                report.rejected.append({
                    "claim_id": cid, "paper_id": pid, "figure_id": fid,
                    "cause": "over_paper_cap", "quoted": True})
                continue
            if len(chosen) >= settings["max_per_claim"]:
                report.rejected.append({
                    "claim_id": cid, "paper_id": pid, "figure_id": fid,
                    "cause": "over_claim_cap", "quoted": True})
                continue
            caption = record.get("caption") or ""
            role = figure_role(refs_by_id.get(pid, {}), caption)
            context_allowed = (
                settings["allow_context_figures"]
                or (role == "review_context" and settings["allow_review_figures"])
            )
            if role != "primary_data" and not context_allowed:
                report.rejected.append({
                    "claim_id": cid, "paper_id": pid, "figure_id": fid,
                    "cause": "illustrative_context_only", "quoted": True,
                    "role": role})
                continue
            chosen.append(FigureChoice(
                pid, fid, cid, "quoted_caption", score, role,
                "direct" if role == "primary_data" else "illustrative",
                "accepted_figure_anchor+visual_crop_check"
                if require_pair_verification else "accepted_figure_anchor",
            ))
            per_paper[pid] += 1

        # 2. Then the best-matching unquoted figures from the claim's own papers.
        scored: list[tuple[float, str, str, str, str]] = []
        for pid in papers_by_claim.get(cid, set()):
            for (fig_pid, fid), record in figures.items():
                if fig_pid != pid or (pid, fid) in quoted_by_claim.get(cid, ()):
                    continue
                figure_text, text_source = _selection_text(record)
                if not figure_text:
                    report.rejected.append({
                        "claim_id": cid, "paper_id": pid, "figure_id": fid,
                        "cause": "no_caption_or_ocr"})
                    continue
                if subject_aliases and not names_subject(record, subject_aliases):
                    report.rejected.append({
                        "claim_id": cid, "paper_id": pid, "figure_id": fid,
                        "cause": "missing_subject_anchor",
                    })
                    continue
                role = figure_role(refs_by_id.get(pid, {}), figure_text)
                if role == "review_context" and not (
                    settings["allow_review_figures"]
                    or settings["allow_context_figures"]
                ):
                    report.rejected.append({
                        "claim_id": cid, "paper_id": pid, "figure_id": fid,
                        "cause": "review_article"})
                    continue
                score = caption_relevance(figure_text, context_text, "",
                                          corpus_df, n_docs)
                shared = shared_terms(figure_text, context_text, "")
                if len(shared) < settings["min_shared_terms"]:
                    report.rejected.append({
                        "claim_id": cid, "paper_id": pid, "figure_id": fid,
                        "cause": "too_few_shared_terms",
                        "shared": sorted(shared)})
                    continue
                if score < settings["min_relevance"]:
                    report.rejected.append({
                        "claim_id": cid, "paper_id": pid, "figure_id": fid,
                        "cause": "below_relevance_floor",
                        "relevance": round(score, 4)})
                    continue
                if (
                    require_pair_verification
                    and _visual_entailment(record, cid) is None
                ):
                    report.rejected.append({
                        "claim_id": cid,
                        "paper_id": pid,
                        "figure_id": fid,
                        "cause": "missing_visual_entailment",
                        "relevance": round(score, 4),
                    })
                    continue
                if role != "primary_data":
                    report.rejected.append({
                        "claim_id": cid,
                        "paper_id": pid,
                        "figure_id": fid,
                        "cause": "context_reserved_for_axis_coverage",
                        "role": role,
                        "relevance": round(score, 4),
                        "shared": sorted(shared),
                    })
                    continue
                scored.append((score, pid, fid, text_source, role))

        for score, pid, fid, text_source, role in sorted(
            scored, key=lambda s: (-s[0], s[1], s[2])
        ):
            if per_paper[pid] >= settings["max_per_paper_per_claim"]:
                report.rejected.append({
                    "claim_id": cid, "paper_id": pid, "figure_id": fid,
                    "cause": "over_paper_cap", "relevance": round(score, 4)})
                continue
            if len(chosen) >= settings["max_per_claim"]:
                report.rejected.append({
                    "claim_id": cid, "paper_id": pid, "figure_id": fid,
                    "cause": "over_claim_cap", "relevance": round(score, 4)})
                continue
            reason = "figure_ocr_match" if text_source == "ocr" else "claim_match"
            chosen.append(FigureChoice(
                pid, fid, cid, reason, score, role, "direct",
                "visual_entailment" if require_pair_verification
                else "caption_semantic_match",
            ))
            per_paper[pid] += 1

        report.chosen.extend(chosen)

    # Adaptive coverage pass.  The user-selected figure minimum remains only a
    # floor; this pass adds one materially relevant visual to an otherwise
    # uncovered evidence axis when eligible supply exists.  It does not force a
    # global count or fraction and never changes claim support.
    claims_by_id = {str(row.get("claim_id") or ""): row for row in claims}
    chosen_axes = {
        str(claims_by_id.get(choice.claim_id, {}).get("cluster") or "")
        for choice in report.chosen
    }
    has_figure_priorities = any("figure_priority" in row for row in claims)
    axes = [
        axis for axis in dict.fromkeys(
            str(row.get("cluster") or "") for row in claims
            if not has_figure_priorities or _figure_priority(row)
        ) if axis
    ]
    for axis in axes:
        axis_claims = [
            row for row in claims if str(row.get("cluster") or "") == axis
            and (not has_figure_priorities or _figure_priority(row))
        ]
        eligible: list[tuple[float, int, str, str, str, str]] = []
        for claim in axis_claims:
            cid = str(claim.get("claim_id") or "")
            context_text = " ".join([
                str(claim.get("claim_text") or ""),
                str(claim.get("scope") or ""),
            ])
            for pid in papers_by_claim.get(cid, set()):
                for (fig_pid, fid), record in figures.items():
                    if fig_pid != pid:
                        continue
                    text, source = _selection_text(record)
                    if not text:
                        continue
                    if subject_aliases and not names_subject(record, subject_aliases):
                        continue
                    role = figure_role(refs_by_id.get(pid, {}), text)
                    context_allowed = (
                        settings["allow_context_figures"]
                        or (role == "review_context"
                            and settings["allow_review_figures"])
                    )
                    if role != "primary_data" and not context_allowed:
                        continue
                    score = caption_relevance(text, context_text, "", corpus_df, n_docs)
                    shared = shared_terms(text, context_text, "")
                    visual_verified = _visual_entailment(record, cid) is not None
                    if (
                        score >= max(
                            settings["min_relevance"],
                            settings["coverage_min_relevance"],
                        )
                        and len(shared) >= max(
                            settings["min_shared_terms"],
                            settings["coverage_min_shared_terms"],
                        )
                        and (
                            not require_pair_verification
                            or visual_verified
                        )
                    ):
                        role_rank = {"primary_data": 0, "source_model": 1,
                                     "review_context": 2}[role]
                        eligible.append((score, -role_rank, cid, pid, fid, role))
        selected_for_axis = [
            choice for choice in report.chosen
            if str(claims_by_id.get(choice.claim_id, {}).get("cluster") or "") == axis
        ]
        if axis not in chosen_axes and eligible:
            score, _rank, cid, pid, fid, role = sorted(
                eligible, key=lambda row: (-row[0], -row[1], row[2], row[3], row[4])
            )[0]
            choice = FigureChoice(
                pid, fid, cid, "coverage_axis_match", score, role,
                "direct" if role == "primary_data" else "illustrative",
                "visual_entailment" if require_pair_verification
                else "caption_semantic_match",
            )
            report.chosen.append(choice)
            selected_for_axis = [choice]
            report.rejected = [
                row for row in report.rejected
                if not (
                    str(row.get("claim_id") or "") == cid
                    and str(row.get("paper_id") or "") == pid
                    and str(row.get("figure_id") or "") == fid
                )
            ]
        report.axis_coverage.append({
            "axis": axis,
            "claim_count": len(axis_claims),
            "eligible_candidate_pairs": len(eligible),
            "selected_figures": len({
                (choice.paper_id, choice.figure_id) for choice in selected_for_axis
            }),
            "selected_roles": sorted({choice.role for choice in selected_for_axis}),
            "selected_figure_ids": [
                {"paper_id": pid, "figure_id": fid}
                for pid, fid in sorted({
                    (choice.paper_id, choice.figure_id)
                    for choice in selected_for_axis
                })
            ],
            "gap_reason": (
                "" if selected_for_axis else
                "no semantically eligible figure in the axis's cited full texts"
            ),
        })
    return report


def policy_from_contract(contract: dict | None) -> dict:
    """The selection policy declared in ``report_contract.json``."""
    spec = ((contract or {}).get("paper_figures") or {}).get("selection") or {}
    return {**DEFAULT_POLICY, **{k: v for k, v in spec.items()
                                 if k in DEFAULT_POLICY}}
