"""
cbioportal_client.py — Reusable cBioPortal REST API client for the
cancer-cohort-genomics skill.

Generalizes the KRAS/TCGA/MSK-IMPACT workflow to ANY gene(s) and ANY cohorts.
Pure standard-library HTTP is avoided in favor of `requests` (available in the
Biomni sandbox) with retry/backoff on transient errors.

Nothing here is gene- or study-specific: every function takes the gene symbol(s)
or study IDs as arguments.

Public API base: https://www.cbioportal.org/api
Endpoints used (all gene/cohort-agnostic):
  GET  /genes/{hugoSymbolOrEntrez}                       -> resolve gene
  GET  /studies?pageSize=...                             -> enumerate studies
  GET  /studies/{studyId}/molecular-profiles            -> profiles for a study
  GET  /studies/{studyId}/sample-lists                  -> sample lists (denominators)
  GET  /sample-lists/{sampleListId}                      -> sample IDs in a list
  POST /molecular-profiles/{id}/mutations/fetch          -> mutations for gene(s)
  POST /molecular-profiles/{id}/discrete-copy-number/fetch -> discrete CNA for gene(s)
  POST /studies/{studyId}/clinical-data/fetch            -> CANCER_TYPE per sample
"""
from __future__ import annotations

import time
from typing import Iterable

import requests

API_BASE = "https://www.cbioportal.org/api"

# Transient HTTP statuses worth retrying with backoff.
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _request(method: str, path: str, *, params=None, json_body=None,
             max_attempts: int = 5, timeout: int = 60):
    """Single HTTP call with linear backoff on transient failures.

    Backoff sleeps 2*(attempt+1) seconds, matching the proven KRAS run.
    Raises the last error if all attempts fail.
    """
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    last_exc = None
    for attempt in range(max_attempts):
        try:
            resp = requests.request(
                method, url, params=params, json=json_body, timeout=timeout,
                headers={"Accept": "application/json"},
            )
            if resp.status_code in _RETRY_STATUS:
                last_exc = RuntimeError(f"HTTP {resp.status_code} for {url}")
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.RequestException, RuntimeError) as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"cBioPortal request failed after {max_attempts} attempts: "
                       f"{method} {url} :: {last_exc}")


def get(path: str, **params):
    """GET helper. Extra kwargs become query params."""
    return _request("GET", path, params=params or None)


def post(path: str, json_body, **params):
    """POST helper. json_body is the request payload; kwargs become query params."""
    return _request("POST", path, params=params or None, json_body=json_body)


# ---------------------------------------------------------------------------
# Gene resolution
# ---------------------------------------------------------------------------
def resolve_gene(symbol_or_entrez: str) -> dict:
    """Resolve a HUGO symbol or Entrez ID to {entrezGeneId, hugoGeneSymbol, type}.

    Works for any gene, e.g. resolve_gene("KRAS") -> entrez 3845;
    resolve_gene("TP53") -> 7157. Raises if the gene is unknown.
    """
    g = get(f"/genes/{symbol_or_entrez}")
    if "entrezGeneId" not in g:
        raise ValueError(f"Could not resolve gene {symbol_or_entrez!r}: {g}")
    return g


def resolve_genes(symbols: Iterable[str]) -> list[dict]:
    """Resolve a list of gene symbols/IDs. Skips (with a printed warning) any
    that fail to resolve so a single bad symbol does not abort a gene set."""
    out = []
    for s in symbols:
        try:
            out.append(resolve_gene(s))
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] gene {s!r} did not resolve: {exc}")
    if not out:
        raise ValueError("No genes resolved.")
    return out


# ---------------------------------------------------------------------------
# Study / cohort discovery and auto-selection
# ---------------------------------------------------------------------------
def list_studies() -> list[dict]:
    """Return all cBioPortal studies (studyId, name, cancerTypeId, allSampleCount...)."""
    return get("/studies", pageSize=10000, projection="SUMMARY")


def tcga_pancan_studies(studies: list[dict] | None = None) -> list[dict]:
    """The TCGA PanCancer Atlas family: one study per cancer type.

    These share the suffix `_tcga_pan_can_atlas_2018` and are the default TCGA
    cohort (each study IS a cancer type, so no extra clinical split is needed).
    """
    studies = studies or list_studies()
    return sorted(
        [s for s in studies if s["studyId"].endswith("_tcga_pan_can_atlas_2018")],
        key=lambda s: s["studyId"],
    )


def largest_msk_impact_study(studies: list[dict] | None = None) -> dict | None:
    """Auto-select a large pan-cancer MSK-IMPACT cohort as the default targeted-panel
    comparator. Picks the MSK-IMPACT study with the most samples that also carries a
    per-sample CANCER_TYPE breakdown (mixed cancerTypeId). Returns None if absent.

    NOTE: `msk_impact_2017` (Zehir et al., Nat Med 2017; 10,945 samples) is the most
    comparable to the TCGA 2018 freeze (matched mutation + discrete CNA + clean
    CANCER_TYPE). Prefer it when present; otherwise fall back to the largest MSK-IMPACT
    pan-cancer study. The caller can always override.
    """
    studies = studies or list_studies()
    candidates = [
        s for s in studies
        if "impact" in s["studyId"].lower() and s["studyId"].startswith("msk")
        and s.get("cancerTypeId") == "mixed"
    ]
    if not candidates:
        return None
    # Prefer the canonical 2017 freeze if available (best apples-to-apples with TCGA).
    for s in candidates:
        if s["studyId"] == "msk_impact_2017":
            return s
    return max(candidates, key=lambda s: s.get("allSampleCount", 0))


def find_studies_by_keyword(keyword: str, studies: list[dict] | None = None) -> list[dict]:
    """User override: match studies whose id/name contains a keyword (case-insensitive)."""
    studies = studies or list_studies()
    k = keyword.lower()
    return [s for s in studies
            if k in s["studyId"].lower() or k in s.get("name", "").lower()]


def auto_select_cohorts() -> dict:
    """Default cohort set: all TCGA PanCancer Atlas studies + the best MSK-IMPACT cohort.

    Returns {"tcga": [study,...], "msk": study_or_None}. Callers may override either.
    """
    studies = list_studies()
    return {
        "tcga": tcga_pancan_studies(studies),
        "msk": largest_msk_impact_study(studies),
    }


# ---------------------------------------------------------------------------
# Molecular profiles (mutation + discrete CNA) per study
# ---------------------------------------------------------------------------
def get_profiles(study_id: str) -> list[dict]:
    return get(f"/studies/{study_id}/molecular-profiles")


def resolve_profiles(study_id: str, profiles: list[dict] | None = None) -> dict:
    """Return {"mutation": profileId|None, "cna": profileId|None} for a study.

    Mutation  = molecularAlterationType MUTATION_EXTENDED.
    CNA        = COPY_NUMBER_ALTERATION with DISCRETE datatype; prefer a profile whose
                 id contains 'gistic' (TCGA -> *_gistic), else the discrete '_cna'
                 profile (MSK -> *_cna). Shallow calls are handled downstream by the
                 GISTIC value, not the profile choice.
    """
    profiles = profiles or get_profiles(study_id)
    mut = next((p["molecularProfileId"] for p in profiles
                if p.get("molecularAlterationType") == "MUTATION_EXTENDED"), None)

    cna_candidates = [p for p in profiles
                      if p.get("molecularAlterationType") == "COPY_NUMBER_ALTERATION"
                      and p.get("datatype") == "DISCRETE"]
    cna = None
    if cna_candidates:
        gistic = next((p["molecularProfileId"] for p in cna_candidates
                       if "gistic" in p["molecularProfileId"].lower()), None)
        cna = gistic or cna_candidates[0]["molecularProfileId"]
    return {"mutation": mut, "cna": cna}


# ---------------------------------------------------------------------------
# Sample lists = denominators
# ---------------------------------------------------------------------------
def get_sample_list_ids(study_id: str) -> list[str]:
    return [sl["sampleListId"] for sl in get(f"/studies/{study_id}/sample-lists")]


def sample_ids_in_list(sample_list_id: str) -> set[str]:
    """Sample IDs belonging to a sample list (the profiled denominator).

    Use `{study}_sequenced` for the mutation denominator and `{study}_cna` for the
    CNA denominator. The default projection lacks counts, so we read the `sampleIds`
    field directly.
    """
    data = get(f"/sample-lists/{sample_list_id}")
    return set(data.get("sampleIds", []))


def profiled_samples(study_id: str) -> dict:
    """Return {"seq": set(mutation-profiled), "cna": set(cna-profiled)} for a study.

    Falls back gracefully if a study lacks one of the canonical lists.
    """
    available = set(get_sample_list_ids(study_id))
    seq_id = f"{study_id}_sequenced"
    cna_id = f"{study_id}_cna"
    seq = sample_ids_in_list(seq_id) if seq_id in available else set()
    cna = sample_ids_in_list(cna_id) if cna_id in available else set()
    return {"seq": seq, "cna": cna}


# ---------------------------------------------------------------------------
# Mutation + CNA fetch (batched per study, per gene set)
# ---------------------------------------------------------------------------
def fetch_mutations(profile_id: str, entrez_ids: list[int], sample_list_id: str,
                    projection: str = "DETAILED") -> list[dict]:
    """POST mutations/fetch for the given gene(s) restricted to a sample list.

    Returns raw mutation records (sampleId, proteinChange, mutationType,
    variantType, proteinPosStart, ...). Non-silent filtering is done downstream.
    """
    body = {"entrezGeneIds": list(entrez_ids), "sampleListId": sample_list_id}
    return post(f"/molecular-profiles/{profile_id}/mutations/fetch",
                body, projection=projection)


def fetch_cna(profile_id: str, entrez_ids: list[int], sample_list_id: str) -> list[dict]:
    """POST discrete-copy-number/fetch for the given gene(s) (event type ALL).

    Returns records with an integer `alteration` GISTIC value in {-2,-1,0,1,2}.
    """
    body = {"entrezGeneIds": list(entrez_ids), "sampleListId": sample_list_id}
    return post(f"/molecular-profiles/{profile_id}/discrete-copy-number/fetch",
                body, discreteCopyNumberEventType="ALL")


def fetch_cancer_type(study_id: str,
                      attribute_id: str = "CANCER_TYPE") -> dict:
    """Map sampleId -> cancer type for a mixed-cancer study (e.g. MSK-IMPACT).

    Uses the SAMPLE clinical-data endpoint. For TCGA PanCancer Atlas this is
    unnecessary because each study is already a single cancer type.
    """
    rows = post(f"/studies/{study_id}/clinical-data/fetch",
                {"attributeIds": [attribute_id]},
                clinicalDataType="SAMPLE")
    return {r["sampleId"]: r["value"] for r in rows}


if __name__ == "__main__":
    # Smoke test: resolve a gene and show default cohort auto-selection.
    import json
    g = resolve_gene("KRAS")
    print("gene:", json.dumps(g))
    coh = auto_select_cohorts()
    print(f"TCGA PanCancer studies: {len(coh['tcga'])}")
    print("MSK default:", coh["msk"]["studyId"] if coh["msk"] else None,
          f"(n={coh['msk']['allSampleCount']})" if coh["msk"] else "")
