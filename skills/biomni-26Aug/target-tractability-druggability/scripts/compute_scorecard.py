#!/usr/bin/env python3
"""
compute_scorecard.py — turn the Open Targets + fpocket evidence into a transparent modality
viability scorecard (0-3 per dimension), for ANY target. Rubric documented in
references/scorecard_rubric.md. This is a triage heuristic, NOT a success prediction.

Dimensions per modality: tractability, structural, clinical -> overall = round(mean).
When no structure is retrieved, structural = "NA" (not assessed) and is excluded
from the overall mean (computed over the remaining assessed dimensions only).

Usage:
    python compute_scorecard.py --ot OT.json --pockets pockets.json --out scores.json
"""
import argparse
import json
import os
import re

CLINICAL_BUCKET_HINTS = ("approved drug", "advanced clinical", "phase 1 clinical",
                         "phase 2", "phase 3", "clinical")
ACCESSIBILITY_HINTS = ("loc high", "loc med", "sigp", "tmhmm", "go cc", "protein atlas",
                       "localization", "accessib")


def _best_pocket_score(pockets):
    """Best (highest) druggability score across all analyzed structures."""
    best = None
    holo = None
    for k, p in (pockets or {}).items():
        if isinstance(p, dict) and p.get("drug_score") is not None:
            s = p["drug_score"]
            best = s if best is None else max(best, s)
            if "holo" in k or (p.get("distance_to_reference_ligand") or 99) <= 12:
                holo = s if holo is None else max(holo, s)
    return best, holo, (pockets or {}).get("apo_to_holo_fold")


def _drug_stage_by_type(ot):
    """Map modality-ish drug types to best clinical stage present. Returns {bucket: rank} where
    rank: 3=approved, 2=clinical(phase1-3), 1=preclinical/none-but-listed, 0=absent."""
    def stage_rank(s):
        s = (s or "").upper()
        if "APPROV" in s or s == "4":
            return 3
        if any(k in s for k in ("PHASE_3", "PHASE_2", "PHASE_1")) or s in ("1", "2", "3"):
            return 2
        return 1  # listed but earlier
    by = {"SM": 0, "AB": 0, "PR": 0, "OC": 0}
    for r in ot.get("drugs", {}).get("rows", []):
        dt = (r.get("drugType") or "").lower()
        rank = stage_rank(r.get("maxClinicalStage"))
        if "small molecule" in dt:
            by["SM"] = max(by["SM"], rank)
        elif "antibody" in dt:
            by["AB"] = max(by["AB"], rank)
        elif "protac" in dt or "degrader" in dt:
            by["PR"] = max(by["PR"], rank)
        else:
            by["OC"] = max(by["OC"], rank)
    return by


def score_tractability(mod, buckets):
    """0-3 from bucket pattern; AB intracellular-only deal-breaker -> 0."""
    true_l = [b.lower() for b in buckets.get("true", [])]
    n_true = buckets.get("n_true", 0)
    has_clinical = any(any(h in b for h in CLINICAL_BUCKET_HINTS) for b in true_l)
    if mod == "AB":
        has_access = any(any(h in b for h in ACCESSIBILITY_HINTS) for b in true_l)
        # antibody needs surface/secreted accessibility; localization buckets alone that do NOT
        # indicate accessibility are not enough. If clinical AB exists, it's tractable regardless.
        if has_clinical:
            return 3
        if not has_access:
            return 0
        return 2 if n_true >= 2 else 1
    if has_clinical:
        return 3
    if n_true >= 3:
        return 2
    return 1 if n_true >= 1 else 0


def score_structural(mod, best_pocket, holo_pocket, ot, has_structure=True):
    """SM/PR need a pocket; AB scored on accessibility (surface localization).

    When no structure was retrieved (has_structure=False), SM/PR/OC return "NA"
    (not assessed) rather than 0, so the dimension is excluded from the Overall
    mean instead of being counted as negative evidence. AB is unaffected because
    its structural score reflects accessibility, not a pocket.
    """
    if mod == "AB":
        true_l = [b.lower() for b in ot["tractability"].get("AB", {}).get("true", [])]
        has_access = any(any(h in b for h in ACCESSIBILITY_HINTS) for b in true_l)
        return 3 if has_access else 0
    # small-molecule / degrader: use pocket druggability
    if not has_structure:
        return "NA"          # no structure retrieved -> not assessed
    s = holo_pocket if holo_pocket is not None else best_pocket
    if s is None:
        return 0             # structure analyzed, but no pocket found
    if s > 0.5:
        return 3
    if s >= 0.2:
        return 2
    return 1


def score_clinical(mod, drug_stage):
    return drug_stage.get(mod, 0)


def compute(ot, pockets):
    best_pocket, holo_pocket, _fold = _best_pocket_score(pockets)
    has_structure = bool(pockets)   # empty pockets dict => no structure was retrieved
    drug_stage = _drug_stage_by_type(ot)
    tract = ot["tractability"]
    mods = [m for m in ["SM", "AB", "PR", "OC"] if m in tract]
    out = {"modalities": {}, "inputs": {
        "best_pocket_score": best_pocket, "holo_pocket_score": holo_pocket,
        "drug_stage_by_modality": drug_stage}}
    for m in mods:
        t = score_tractability(m, tract[m])
        s = score_structural(m, best_pocket, holo_pocket, ot, has_structure)
        c = score_clinical(m, drug_stage)
        assessed = [v for v in (t, s, c) if v != "NA"]
        overall = round(sum(assessed) / len(assessed)) if assessed else 0
        out["modalities"][m] = {"tractability": t, "structural": s, "clinical": c,
                                "overall": overall}
    # verdict: rank by overall, tie-break by clinical then tractability
    ranked = sorted(out["modalities"].items(),
                    key=lambda kv: (kv[1]["overall"], kv[1]["clinical"], kv[1]["tractability"]),
                    reverse=True)
    verdict_word = {0: "Not viable", 1: "Low", 2: "Medium / emerging", 3: "High / most viable"}
    out["ranked"] = [{"modality": m, **v, "verdict": verdict_word[v["overall"]]}
                     for m, v in ranked]
    out["most_viable"] = ranked[0][0] if ranked else None
    # frontier = an emerging modality driven by enabling (not-yet-approved) evidence.
    # Prefer a specific emerging modality (PROTAC/degrader, then antibody) over the OC catch-all,
    # among modalities with overall>=2 and no approved drug (clinical<3).
    emerging = [(m, v) for m, v in ranked if v["clinical"] < 3 and v["overall"] >= 2]
    frontier = None
    for pref in ("PR", "AB", "SM"):
        for m, v in emerging:
            if m == pref:
                frontier = m
                break
        if frontier:
            break
    if frontier is None and emerging:
        frontier = emerging[0][0]
    out["frontier"] = frontier
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ot", required=True)
    ap.add_argument("--pockets", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    ot = json.load(open(args.ot))
    pockets = json.load(open(args.pockets)) if args.pockets and os.path.exists(args.pockets) else {}
    res = compute(ot, pockets)
    print(json.dumps(res, indent=2))
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        json.dump(res, open(args.out, "w"), indent=2)
        print(f"[done] wrote {args.out}")
    return res


if __name__ == "__main__":
    main()
