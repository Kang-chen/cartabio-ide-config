"""Resolve a user request into a validated set of FAERS-queryable drugs.

Three input modes (auto-detected, or forced via ``mode=``):

  * ``explicit`` : the user already named a drug or list of generics/brands.
  * ``class``    : a pharmacologic class ("SGLT2 inhibitors", "anti-TNF");
                   members are resolved from OpenFDA pharm-class fields.
  * ``target``   : a molecular target symbol ("JAK1", "EGFR"); drugs are
                   resolved via the Open Targets Platform GraphQL API
                   (target -> knownDrugs). This deliberately REUSES the
                   ``open-targets`` skill's endpoint rather than reimplementing
                   target biology.

Every resolved name is validated against OpenFDA: a drug is kept only if it has
a non-zero normalized FAERS report count (i.e. OpenFDA can attribute reports to
it). Names that resolve to 0 normalized reports are dropped with a warning
(this is the "filgotinib" failure mode -- present as free text but not
normalized, so disproportionation on it is unreliable).

Returns a dict:
  {"drugs": [validated generic names],
   "counts": {name: n_reports},
   "dropped": [(name, reason)],
   "mode": "explicit"|"class"|"target",
   "log": [human-readable strings]}
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Dict, List, Optional

import requests

from .query_faers import (EVENT_URL, LABEL_URL, _request, drug_event_search,
                          get_total)

OPENTARGETS_GQL = "https://api.platform.opentargets.org/api/v4/graphql"


# --------------------------------------------------------------------------- #
# mode detection
# --------------------------------------------------------------------------- #
# Word stems that signal a *class* rather than a single drug. Matched as stems
# (no trailing ``\b``) so plurals like "statins"/"inhibitors" are caught.
_CLASS_HINTS = re.compile(
    r"(inhibitor|agonist|antagonist|blocker|anti-|antibod|"
    r"\bstatin|sartan|prazole|gliflozin|gliptin|"
    r"\bbiologic|\bclass\b)", re.I)


def detect_mode(query, explicit_is_list: bool) -> str:
    """Heuristic mode detection when the caller does not force ``mode``."""
    if explicit_is_list:
        return "explicit"
    text = str(query).strip()
    # a single gene-like token (all caps + digits, no spaces) -> target
    if re.fullmatch(r"[A-Z0-9]{2,10}", text) and not text.isdigit():
        return "target"
    if _CLASS_HINTS.search(text):
        return "class"
    return "explicit"


# --------------------------------------------------------------------------- #
# class -> members  (OpenFDA pharmacologic-class facets)
# --------------------------------------------------------------------------- #
# Informal class names -> canonical FDA established-pharmacologic-class (EPC)
# label text. OpenFDA's pharm_class fields use these formal strings, so a bare
# acronym ("SGLT2") or plain plural ("statins") will NOT match without mapping.
_CLASS_SYNONYMS = {
    "sglt2 inhibitor": "Sodium-Glucose Cotransporter 2 Inhibitor",
    "sglt-2 inhibitor": "Sodium-Glucose Cotransporter 2 Inhibitor",
    "statin": "HMG-CoA Reductase Inhibitor",
    "hmg-coa reductase inhibitor": "HMG-CoA Reductase Inhibitor",
    "jak inhibitor": "Janus Kinase Inhibitor",
    "janus kinase inhibitor": "Janus Kinase Inhibitor",
    "dpp-4 inhibitor": "Dipeptidyl Peptidase 4 Inhibitor",
    "dpp4 inhibitor": "Dipeptidyl Peptidase 4 Inhibitor",
    "glp-1 agonist": "GLP-1 Receptor Agonist",
    "glp1 agonist": "GLP-1 Receptor Agonist",
    "ace inhibitor": "Angiotensin Converting Enzyme Inhibitor",
    "arb": "Angiotensin 2 Receptor Blocker",
    "sartan": "Angiotensin 2 Receptor Blocker",
    "ppi": "Proton Pump Inhibitor",
    "proton pump inhibitor": "Proton Pump Inhibitor",
    "prazole": "Proton Pump Inhibitor",
    "ssri": "Serotonin Reuptake Inhibitor",
    "tnf inhibitor": "Tumor Necrosis Factor Blocker",
    "anti-tnf": "Tumor Necrosis Factor Blocker",
    "beta blocker": "beta-Adrenergic Blocker",
    "pd-1 inhibitor": "Programmed Death Receptor-1 Blocking Antibody",
    "pd-l1 inhibitor": "Programmed Death Ligand-1 Blocking Antibody",
    "checkpoint inhibitor": "Blocking Antibody",
}


def _canonical_class(class_name: str) -> str:
    """Map an informal class name to a canonical FDA EPC/MoA phrase.

    Strips a trailing plural 's' from the head noun and looks the phrase up in
    :data:`_CLASS_SYNONYMS`; if no mapping exists the (singularized) input is
    returned so it can still be tried as literal label text.
    """
    key = class_name.strip().lower()
    # normalize "SGLT2 inhibitors" -> "sglt2 inhibitor"
    key = re.sub(r"(inhibitor|agonist|antagonist|blocker|statin|sartan|"
                 r"gliflozin|gliptin|prazole)s\b", r"\1", key)
    return _CLASS_SYNONYMS.get(key, class_name.strip())


def _is_single_generic(name: str) -> bool:
    """True if ``name`` looks like one base generic (not a combo product)."""
    if " and " in name or "," in name or "/" in name or "+" in name:
        return False
    return True


def resolve_class_members(class_name: str, api_key: Optional[str] = None,
                          max_members: int = 60) -> List[str]:
    """Resolve a pharmacologic class to member generic names via OpenFDA labels.

    Uses the ``/drug/label.json`` endpoint (drug-labeled classes) rather than
    faceting event reports, which avoids co-reported-drug contamination. The
    informal class name is first mapped to a canonical EPC phrase, then queried
    against EPC -> MoA -> CS pharm-class fields. Combination products are
    dropped so only base generics are returned.
    """
    term = _canonical_class(class_name)
    members: List[str] = []
    for field in ("openfda.pharm_class_epc",
                  "openfda.pharm_class_moa",
                  "openfda.pharm_class_cs"):
        # NB: do NOT urllib-quote the term -- requests encodes params itself,
        # and manual quoting double-encodes spaces inside the quoted phrase.
        js = _request(LABEL_URL,
                      {"search": f'{field}:"{term}"',
                       "count": "openfda.generic_name.exact",
                       "limit": max_members}, api_key)
        if js and not js.get("__http400__"):
            for row in js.get("results", []):
                name = str(row["term"]).lower().strip()
                if not _is_single_generic(name):
                    continue
                base = _base_generic(name)  # strip descriptors + salt/hydrate
                if base and base not in members:
                    members.append(base)
        if members:
            break
    return members


# --------------------------------------------------------------------------- #
# target -> drugs  (Open Targets GraphQL; reuses the open-targets skill API)
# --------------------------------------------------------------------------- #
_OT_TARGET_QUERY = """
query resolveTargetDrugs($sym: String!) {
  search(queryString: $sym, entityNames: ["target"], page: {index: 0, size: 1}) {
    hits { id name entity }
  }
}
"""
# NOTE: current Open Targets schema exposes target -> drugs via
# ``drugAndClinicalCandidates`` (no pagination args), not the older ``knownDrugs``.
_OT_KNOWN_DRUGS = """
query targetDrugs($ensg: String!) {
  target(ensemblId: $ensg) {
    approvedSymbol
    drugAndClinicalCandidates { count rows { drug { name } } }
  }
}
"""

# Common salt / hydrate suffixes appended to drug names in Open Targets records.
# We strip these to recover the base generic (INN) that FAERS normalizes on.
_SALT_TOKENS = (
    "hydrochloride", "dihydrochloride", "hydrobromide", "phosphate", "citrate",
    "maleate", "mesylate", "besylate", "tartrate", "succinate", "sulfate",
    "sulphate", "sodium", "potassium", "calcium", "magnesium", "acetate",
    "fumarate", "malate", "hemihydrate", "monohydrate", "dihydrate",
    "trihydrate", "hydrate", "bitartrate", "tosylate", "pamoate", "gluconate",
    "lactate", "sesquihydrate", "hydrofumarate", "dimesylate", "nitrate",
)


_DESCRIPTOR_TOKENS = frozenset((
    "oral", "film", "coated", "extended", "release", "er", "xr", "ir",
    "tablet", "tablets", "capsule", "capsules", "injection", "solution",
))


def _base_generic(name: str) -> str:
    """Recover the base generic (INN) from a labeled name.

    Drops trailing formulation descriptors ("oral", "film coated", "ER") and
    salt/hydrate tokens ("hydrochloride", "magnesium"), iterating until the
    trailing token is a real drug-name token.
    """
    toks = name.lower().replace("-", " ").replace(",", " ").split()
    changed = True
    while toks and changed:
        changed = False
        while toks and toks[-1] in _DESCRIPTOR_TOKENS:
            toks.pop(); changed = True
        while toks and toks[-1] in _SALT_TOKENS:
            toks.pop(); changed = True
    return " ".join(toks).strip() or name.lower()


def resolve_target_drugs(target_symbol: str, timeout: int = 60) -> List[str]:
    """Resolve a target gene symbol to base generic drug names via Open Targets.

    Returns lowercase, salt-normalized, de-duplicated names (validated against
    OpenFDA later). Empty list if the target or its drugs cannot be resolved.
    """
    try:
        r = requests.post(OPENTARGETS_GQL,
                          json={"query": _OT_TARGET_QUERY,
                                "variables": {"sym": target_symbol}},
                          timeout=timeout)
        hits = r.json().get("data", {}).get("search", {}).get("hits", [])
        if not hits:
            return []
        ensg = hits[0]["id"]
        r2 = requests.post(OPENTARGETS_GQL,
                           json={"query": _OT_KNOWN_DRUGS,
                                 "variables": {"ensg": ensg}},
                           timeout=timeout)
        rows = ((r2.json().get("data", {}) or {}).get("target", {}) or {}
                ).get("drugAndClinicalCandidates", {}) or {}
        rows = rows.get("rows", [])
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return []
    names = []
    for row in rows:
        nm = (row.get("drug") or {}).get("name")
        if nm:
            base = _base_generic(nm)
            if base not in names:
                names.append(base)
    return names


# --------------------------------------------------------------------------- #
# validation against OpenFDA
# --------------------------------------------------------------------------- #
def validate_drugs(names: List[str], min_reports: int = 1,
                   api_key: Optional[str] = None):
    """Keep only drugs with >= ``min_reports`` normalized FAERS reports."""
    kept, counts, dropped = [], {}, []
    for name in names:
        n = get_total(drug_event_search([name]), api_key)
        if n >= min_reports:
            kept.append(name); counts[name] = n
        else:
            dropped.append((name, f"{n} normalized FAERS reports (< {min_reports})"))
    # sort kept by report volume (desc) for stable, informative ordering
    kept.sort(key=lambda x: counts[x], reverse=True)
    return kept, counts, dropped


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def resolve_drugs(query, mode: Optional[str] = None,
                  min_reports: int = 1, api_key: Optional[str] = None) -> Dict:
    """Resolve + validate a drug set. See module docstring for the return shape.

    ``query`` may be a string (single drug / class / target) or a list of
    explicit drug names.
    """
    log: List[str] = []
    explicit_is_list = isinstance(query, (list, tuple))
    mode = mode or detect_mode(query, explicit_is_list)
    log.append(f"mode = {mode}")

    if mode == "explicit":
        candidates = list(query) if explicit_is_list else [query]
    elif mode == "class":
        candidates = resolve_class_members(query, api_key)
        log.append(f"class '{query}' -> {len(candidates)} candidate members")
        if not candidates:
            log.append("WARNING: class resolution returned 0 members; "
                       "supply an explicit drug list instead.")
    elif mode == "target":
        candidates = resolve_target_drugs(query if not explicit_is_list else query[0])
        log.append(f"target '{query}' -> {len(candidates)} candidate drugs (Open Targets)")
        if not candidates:
            log.append("WARNING: target resolution returned 0 drugs; "
                       "supply an explicit drug list instead.")
    else:
        raise ValueError(f"unknown mode: {mode}")

    kept, counts, dropped = validate_drugs(candidates, min_reports, api_key)
    for name, reason in dropped:
        log.append(f"DROPPED {name}: {reason}")
    log.append(f"validated {len(kept)} drug(s): {', '.join(kept) if kept else '(none)'}")

    return {"drugs": kept, "counts": counts, "dropped": dropped,
            "mode": mode, "log": log}


if __name__ == "__main__":
    import json
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else "JAK1"
    forced = sys.argv[2] if len(sys.argv) > 2 else None
    res = resolve_drugs(arg, mode=forced)
    print(json.dumps({k: v for k, v in res.items() if k != "counts"}, indent=2))
    print("counts:", res["counts"])
