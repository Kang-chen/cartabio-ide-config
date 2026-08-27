"""OPTIONAL MODE: ClinicalTrials.gov cross-check for top candidates.

For each top candidate, query the ClinicalTrials.gov v2 REST API for trials that mention
BOTH the drug and the target disease. This distinguishes:
  - candidates ALREADY in trials for the indication (external validation, but lower
    novelty), vs
  - candidates with NO trials for the indication (novel repurposing hypotheses).

Uses only the public v2 API (https://clinicaltrials.gov/api/v2/studies). No key needed.
Network-guarded: returns rows with n_trials_matched=NaN and note='query failed' on any
error, so the pipeline never hard-fails if offline.

Intervention verification: the loose full-text query total (n_trials_query_total) counts
any study whose text mentions the drug and disease strings. n_trials_matched is the
verified count — studies where the drug actually appears in the intervention names. The
report must quote n_trials_matched, never n_trials_query_total. query_condition records the
exact disease string sent to the API so the CSV states which disease was queried.

Public API:
  check_trials(drugs, disease, max_studies=20, pause=0.34) -> DataFrame
  trials_summary_for(drug, disease, ...) -> dict
"""
import re
import time
import pandas as pd

API = "https://clinicaltrials.gov/api/v2/studies"

# Common salt/form suffixes to strip when matching drug tokens to intervention names
_SALT_SUFFIXES = re.compile(
    r"\s+(hydrochloride|hcl|fumarate|fumarate|propionate|bromide|chloride|sulfate|sulphate|"
    r"citrate|tartrate|mesylate|maleate|acetate|phosphate|sodium|potassium|"
    r"besylate|tosylate|triflate|iodide|nitrate|carbonate|stearate|lactate|"
    r"gluconate|benzoate|salicylate|camphorsulfonate|esylate|napsylate|"
    r"hydrobromide|dihydrochloride|monohydrochloride|sesquihydrate|monohydrate|"
    r"dihydrate|trihydrate|hemihydrate|anhydrous|base|free\s+base|"
    r"cream|ointment|gel|lotion|solution|suspension|tablet|capsule|injection|"
    r"infusion|spray|drops|inhaler|aerosol|patch|suppository|syrup|elixir|"
    r"granules|powder|micronized|delayed|release|extended|immediate|sustained)\b",
    re.IGNORECASE)


def _strip_salt(token):
    """Strip a trailing salt/form suffix from a single drug token."""
    return _SALT_SUFFIXES.sub("", token).strip()


def trials_summary_for(drug, disease, max_studies=20, timeout=20):
    """Return dict with verified trial info for a drug-disease pair.

    Keys: drug, disease, query_condition, n_trials_query_total, n_trials_matched,
    verified_nct_ids, phases, statuses, nct_ids, note, truncated.
    """
    import requests
    drug_str = str(drug)
    disease_str = str(disease)
    params = {
        "query.intr": drug_str,
        "query.cond": disease_str,
        "pageSize": max_studies,
        "fields": "NCTId,BriefTitle,Phase,OverallStatus,InterventionName,Condition",
        "countTotal": "true",
    }
    try:
        r = requests.get(API, params=params, timeout=timeout)
        r.raise_for_status()
        js = r.json()
        total = js.get("totalCount", None)
        studies = js.get("studies", [])
        ncts, phases, statuses = [], set(), set()
        verified_ncts = []

        # Drug token for intervention verification: first whitespace-delimited token, salt-stripped
        drug_first_token = _strip_salt(drug_str.split()[0]) if drug_str.split() else drug_str
        drug_token_lower = drug_first_token.lower()

        for s in studies:
            proto = s.get("protocolSection", {})
            idm = proto.get("identificationModule", {})
            dm = proto.get("designModule", {})
            sm = proto.get("statusModule", {})
            im = proto.get("armsInterventionsModule", {})
            nct = idm.get("nctId")
            if nct:
                ncts.append(nct)
            for ph in dm.get("phases", []) or []:
                phases.add(ph)
            st = sm.get("overallStatus")
            if st:
                statuses.add(st)

            # Verify the drug actually appears in this study's intervention names
            interventions = im.get("interventions", []) or []
            intervention_names = []
            for iv in interventions:
                iname = iv.get("interventionName") or iv.get("name") or ""
                if iname:
                    intervention_names.append(str(iname).lower())
            # case-insensitive substring of the salt-stripped first token
            if drug_token_lower and any(drug_token_lower in iname for iname in intervention_names):
                if nct:
                    verified_ncts.append(nct)

        n_query_total = total if total is not None else len(studies)
        n_matched = len(verified_ncts)
        truncated = len(studies) >= max_studies and (total is None or total > max_studies)

        return dict(
            drug=drug_str, disease=disease_str, query_condition=disease_str,
            n_trials_query_total=n_query_total, n_trials_matched=n_matched,
            verified_nct_ids=",".join(verified_ncts[:10]),
            phases="|".join(sorted(phases)) if phases else "",
            statuses="|".join(sorted(statuses)) if statuses else "",
            nct_ids=",".join(ncts[:10]),
            note="already in trials for indication" if n_matched > 0 else "no verified trials for indication",
            truncated=truncated,
        )
    except Exception as e:  # noqa: BLE001
        return dict(
            drug=drug_str, disease=disease_str, query_condition=disease_str,
            n_trials_query_total=float("nan"), n_trials_matched=float("nan"),
            verified_nct_ids="", phases="", statuses="", nct_ids="",
            note=f"query failed: {type(e).__name__}", truncated=False,
        )


def check_trials(drugs, disease, max_studies=20, pause=0.34):
    """Query ClinicalTrials.gov for each drug against the disease. Returns a DataFrame."""
    rows = []
    for d in drugs:
        rows.append(trials_summary_for(d, disease, max_studies=max_studies))
        time.sleep(pause)  # be polite to the API
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys
    dis = sys.argv[1] if len(sys.argv) > 1 else "idiopathic pulmonary fibrosis"
    print(check_trials(["pirfenidone", "nintedanib", "carbidopa"], dis).to_string(index=False))
