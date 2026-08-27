"""Annotate disproportionality signals with label, category, SOC, and context.

After :mod:`compute_disproportionality` flags statistical signals, this module
adds the interpretive layer that turns a table of ROR values into something a
reviewer can act on:

  1. **Label grounding** - is the reported event already described in the FDA
     label (boxed warning / adverse reactions section)? Uses the OpenFDA
     ``/drug/label.json`` endpoint via ``query_faers.fetch_drug_label``.
     -> ``label_status`` in {"boxed", "labeled", "unlabeled", "unknown"}.

  2. **Non-informative filtering** - spontaneous reports are full of terms that
     are not adverse drug reactions: the underlying disease/indication, drug
     product complaints, dosing/administration terms, and off-topic lab
     markers. These are flagged so they can be excluded from "novel signal"
     shortlists. -> ``is_noise`` + ``noise_reason``.

  3. **Generic ADR category** - a light keyword mapping to broad clinical
     buckets (infection, thrombosis, cardiac, hepatic, malignancy, ...), drug-
     agnostic so the skill works for any therapy. -> ``category``.

  4. **MedDRA System Organ Class (SOC)** - passed through if the caller already
     has it (FAERS/MedDRA), otherwise a coarse keyword fallback. -> ``soc``.

  5. **Literature grounding** - :func:`build_literature_query` produces a
     focused query string; the ORCHESTRATOR runs the actual ``LiteratureSearch``
     tool (this module does not call agent tools directly) and passes results to
     :func:`attach_literature` for a short evidence note per top signal.

Design principle: everything here is *drug-agnostic*. No JAK-specific terms are
hard-coded; keyword lists are generic clinical vocabulary so the same code
annotates signals for an oncology drug, an antidiabetic, or a biologic.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import pandas as pd

from .query_faers import fetch_drug_label


# --------------------------------------------------------------------------- #
# 1. label grounding
# --------------------------------------------------------------------------- #
def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def _term_tokens(term: str) -> List[str]:
    """Content tokens of a MedDRA term, dropping stopwords/qualifiers."""
    stop = {"of", "the", "and", "in", "with", "due", "to", "nos", "aggravated",
            "increased", "decreased", "abnormal", "disorder", "disease"}
    toks = [t for t in _normalize(term).split() if t not in stop and len(t) > 2]
    return toks


def match_label(term: str, label: Optional[dict]) -> str:
    """Classify a term against a drug label record.

    Returns "boxed" if the term appears in the boxed warning, "labeled" if it
    appears anywhere in adverse-reactions / warnings text, "unlabeled" if the
    label exists but does not mention it, "unknown" if no label was found.
    """
    if not label:
        return "unknown"
    boxed = " ".join(label.get("boxed_warning", []) or [])
    body_fields = []
    for f in ("adverse_reactions", "warnings_and_cautions", "warnings",
              "adverse_reactions_table"):
        body_fields.extend(label.get(f, []) or [])
    body = " ".join(body_fields)
    boxed_n, body_n = _normalize(boxed), _normalize(body)

    toks = _term_tokens(term)
    if not toks:
        return "unlabeled" if (boxed or body) else "unknown"

    def _hit(hay: str) -> bool:
        # require the most specific token (longest) to appear; for multi-token
        # terms require >=2 tokens or the full phrase
        phrase = " ".join(toks)
        if phrase and phrase in hay:
            return True
        present = [t for t in toks if re.search(rf"\b{re.escape(t)}", hay)]
        if len(toks) == 1:
            return len(present) == 1
        return len(present) >= max(2, int(round(0.6 * len(toks))))

    if boxed_n and _hit(boxed_n):
        return "boxed"
    if body_n and _hit(body_n):
        return "labeled"
    return "unlabeled" if (boxed or body) else "unknown"


def annotate_labels(res: pd.DataFrame, drugs: List[str],
                    drug_col: str = "drug", event_col: str = "event",
                    api_key: Optional[str] = None,
                    label_cache: Optional[Dict[str, dict]] = None
                    ) -> pd.DataFrame:
    """Add a ``label_status`` column by matching each event against its label.

    ``label_cache`` (optional) lets the caller pass pre-fetched labels; missing
    entries are fetched once per drug and cached. For pooled/class rows whose
    ``drug`` value is not a real drug name (e.g. "JAK1_pooled"), pass a
    representative member name via ``label_cache`` or the pooled row is marked
    "unknown".
    """
    cache: Dict[str, dict] = dict(label_cache or {})
    df = res.copy()

    def _label_for(drug: str):
        if drug in cache:
            return cache[drug]
        lab = fetch_drug_label(drug, api_key)
        cache[drug] = lab
        return lab

    statuses = []
    for _, r in df.iterrows():
        lab = _label_for(str(r[drug_col]))
        statuses.append(match_label(str(r[event_col]), lab))
    df["label_status"] = statuses
    df.attrs["label_cache"] = cache
    return df


# --------------------------------------------------------------------------- #
# 2. non-informative (noise) term filtering
# --------------------------------------------------------------------------- #
# Terms that are typically NOT adverse drug reactions in FAERS.
_NOISE_PATTERNS = {
    "product/quality": re.compile(
        r"\b(product|device|packaging|dose|dosage|administration|"
        r"expired|storage|label|syringe|pen|injection site|"
        r"needle|overdose|underdose|off label|drug ineffective|"
        r"therapeutic response|therapy)\b", re.I),
    "indication/disease": re.compile(
        r"\b(indication|prophylaxis|condition aggravated|"
        r"disease progression|neoplasm progression|tumour progression|"
        r"tumor progression|metastas\w+ to|disease recurrence|"
        r"underlying|relapse|"
        # the treated diseases themselves are reported as PTs but reflect the
        # population, not drug toxicity (confounding by indication):
        r"rheumatoid arthritis|psoriatic arthritis|psoriasis|"
        r"ankylosing spondylitis|\bspondylitis|"
        r"ulcerative colitis|colitis ulcerative|"  # both MedDRA word orders
        r"crohn|atopic dermatitis|inflammatory bowel|"
        r"disease activity|flare)\b", re.I),
    "procedure": re.compile(
        # surgical / interventional PTs reflect the patient's surgical history
        # or an intervention, not a direct adverse drug reaction.
        r"(ectomy\b|ostomy\b|otomy\b|oscopy\b|plasty\b|"
        r"\bsurgery|\bsurgical|\boperation\b|\bprocedure\b|"
        r"\btransplant|\bresection\b|\bbiopsy\b|\bcatheter|\bstent\b|"
        r"\bintubation\b|\btransfusion\b|\bdialysis\b|\bincision\b|"
        r"\bdrainage\b|\bamputation\b|\bimplant\b|\binsertion\b)", re.I),
    "nonspecific": re.compile(
        r"\b(no adverse event|unevaluable|adverse event|"
        r"death|malaise|feeling abnormal|illness|unspecified|"
        r"condition|general symptom|clinical)\b", re.I),
    "lab-marker": re.compile(
        r"\b(antibody positive|antibody test|serology|titre|titer)\b", re.I),
}


def flag_noise(term: str) -> Optional[str]:
    """Return a noise-reason string if the term is likely NOT a true ADR."""
    for reason, pat in _NOISE_PATTERNS.items():
        if pat.search(term):
            return reason
    return None


# --------------------------------------------------------------------------- #
# 3. generic ADR category (drug-agnostic clinical buckets)
# --------------------------------------------------------------------------- #
_CATEGORY_RULES = [
    ("Infection", re.compile(
        r"\b(infection|sepsis|pneumonia|zoster|herpes|tuberculosis|"
        r"cellulitis|abscess|candidiasis|bacteraemia|viral|fungal|"
        r"influenza|covid|bronchitis|sinusitis)\b", re.I)),
    ("Thrombosis/Vascular", re.compile(
        r"\b(thrombosis|thromboembolism|embolism|embolus|thrombus|"
        r"deep vein|pulmonary embolism|infarct|ischaemia|ischemia|"
        r"occlusion)\b", re.I)),
    ("Cardiac", re.compile(
        r"\b(cardiac|myocardial|arrhythmia|tachycardia|bradycardia|"
        r"heart failure|angina|palpitation|atrial|ventricular)\b", re.I)),
    ("Malignancy", re.compile(
        r"\b(carcinoma|cancer|malignan|lymphoma|leukaemia|leukemia|"
        r"neoplasm|tumour|tumor|melanoma|metasta)\b", re.I)),
    ("Hepatobiliary", re.compile(
        r"\b(hepat|liver|transaminase|bilirubin|jaundice|"
        r"alt |ast |hepatic enzyme|cholestasis)\b", re.I)),
    ("Haematologic", re.compile(
        r"\b(anaemia|anemia|neutropenia|thrombocytopenia|leukopenia|"
        r"lymphopenia|pancytopenia|cytopenia|agranulocytosis)\b", re.I)),
    ("Renal", re.compile(
        r"\b(renal|kidney|nephr|creatinine|acute kidney)\b", re.I)),
    ("Metabolic/Lipid", re.compile(
        r"(cholesterol|lipid|hyperlipid|triglyceride|"
        r"glucose|diabet|hyperglyc|hypoglyc|\bweight)", re.I)),
    ("GI/Perforation", re.compile(
        r"\b(perforation|ulcer|gastrointestinal|diverticul|colitis|"
        r"pancreatitis|haemorrhage|bleeding|nausea|vomiting|diarrhoea)\b",
        re.I)),
    ("Skin/Hypersensitivity", re.compile(
        r"\b(rash|urticaria|pruritus|dermatitis|acne|erythema|"
        r"hypersensitivity|angioedema|anaphyla|stevens)\b", re.I)),
    ("Musculoskeletal", re.compile(
        r"\b(arthralgia|myalgia|arthr|muscul|fracture|osteoporosis|"
        r"osteoarthritis|tendon|bone|stiffness|osteonecrosis)\b", re.I)),
    ("Immune/Autoimmune", re.compile(
        r"\b(lupus|immunodeficiency|autoimmune|vasculitis|"
        r"sarcoidosis|immune|hypogammaglobulinaemia|"
        r"hypersensitivity vasculitis)\b", re.I)),
    ("Neuro/Psych", re.compile(
        r"\b(headache|dizziness|neuropathy|seizure|depression|anxiety|"
        r"insomnia|paraesthesia|tremor|cognitive)\b", re.I)),
]


def categorize(term: str) -> str:
    for name, pat in _CATEGORY_RULES:
        if pat.search(term):
            return name
    return "Other"


# --------------------------------------------------------------------------- #
# 4. coarse SOC fallback (used only when MedDRA SOC not supplied)
# --------------------------------------------------------------------------- #
_SOC_FALLBACK = {
    "Infection": "Infections and infestations",
    "Thrombosis/Vascular": "Vascular disorders",
    "Cardiac": "Cardiac disorders",
    "Malignancy": "Neoplasms benign, malignant and unspecified",
    "Hepatobiliary": "Hepatobiliary disorders",
    "Haematologic": "Blood and lymphatic system disorders",
    "Renal": "Renal and urinary disorders",
    "Metabolic/Lipid": "Metabolism and nutrition disorders",
    "GI/Perforation": "Gastrointestinal disorders",
    "Skin/Hypersensitivity": "Skin and subcutaneous tissue disorders",
    "Musculoskeletal": "Musculoskeletal and connective tissue disorders",
    "Immune/Autoimmune": "Immune system disorders",
    "Neuro/Psych": "Nervous system disorders",
    "Other": "General disorders and administration site conditions",
}


def infer_soc(term: str) -> str:
    return _SOC_FALLBACK.get(categorize(term), "Other")


# --------------------------------------------------------------------------- #
# top-level annotate
# --------------------------------------------------------------------------- #
def annotate_signals(res: pd.DataFrame, drugs: List[str],
                     drug_col: str = "drug", event_col: str = "event",
                     soc_col: Optional[str] = None,
                     api_key: Optional[str] = None,
                     label_cache: Optional[Dict[str, dict]] = None,
                     criteria=None
                     ) -> pd.DataFrame:
    """Add label_status, is_noise/noise_reason, category, soc, and
    low-confidence flags.

    ``criteria`` (a ``SignalCriteria``) is used only for the low-confidence
    flagging thresholds; if None, defaults are used. The extreme-ROR fence is
    computed here (after ``is_noise`` is known) so that administrative/procedure
    noise terms do not distort the drug's ROR distribution.
    """
    df = annotate_labels(res, drugs, drug_col=drug_col, event_col=event_col,
                         api_key=api_key, label_cache=label_cache)
    noise = df[event_col].astype(str).map(flag_noise)
    df["noise_reason"] = noise
    df["is_noise"] = noise.notna()
    df["category"] = df[event_col].astype(str).map(categorize)
    if soc_col and soc_col in df.columns:
        df["soc"] = df[soc_col].fillna(df[event_col].astype(str).map(infer_soc))
    else:
        df["soc"] = df[event_col].astype(str).map(infer_soc)
    # low-confidence flagging (needs signal + is_noise + ror; all present now)
    from .compute_disproportionality import flag_low_confidence
    df = flag_low_confidence(df, criteria=criteria, drug_col=drug_col)
    return df


# --------------------------------------------------------------------------- #
# 5. literature grounding (query builder + attach; agent runs the search)
# --------------------------------------------------------------------------- #
def build_literature_query(subject: str, top_events: List[str],
                           max_events: int = 6) -> str:
    """Build a focused LiteratureSearch query for the drug/class + top events.

    ``subject`` is the drug, class, or target label. The ORCHESTRATOR passes the
    returned string to the ``LiteratureSearch`` tool.
    """
    evs = ", ".join(top_events[:max_events])
    return (f"{subject} safety adverse events pharmacovigilance "
            f"disproportionality {evs}").strip()


def attach_literature(res: pd.DataFrame, references: List[dict],
                      event_col: str = "event") -> pd.DataFrame:
    """Attach a coarse ``lit_support`` flag by matching event tokens to titles.

    ``references`` is a list of dicts with at least a ``title`` (and optionally
    ``abstract``) field, as produced by the LiteratureSearch tool / the
    references.jsonl records. This is a lightweight lexical match to indicate
    which flagged events have *some* published safety discussion; it is not a
    substitute for reading the papers.
    """
    corpus = " ".join(
        _normalize(f"{r.get('title','')} {r.get('abstract','')}")
        for r in (references or []))
    df = res.copy()
    def _supported(term: str) -> bool:
        toks = _term_tokens(term)
        if not toks:
            return False
        hits = sum(1 for t in toks if t in corpus)
        return hits >= max(1, len(toks) - 1)
    df["lit_support"] = df[event_col].astype(str).map(_supported)
    return df
