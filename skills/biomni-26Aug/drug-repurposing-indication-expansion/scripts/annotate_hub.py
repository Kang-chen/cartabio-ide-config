"""Annotate scored perturbations with the Broad Drug Repurposing Hub.

Adds clinical phase, MOA, target, disease_area, indication, SMILES/InChIKey/pubchem_cid
to the connectivity+enrichment results, and marks approved drugs (clinical_phase == 'Launched').

Name matching (order):
  1. exact normalized match (lowercase, strip parentheticals & time annotations)
  2. salt-aware base-name match (strip a trailing salt token only if it is a known salt
     suffix) -- recovers e.g. 'fluticasone' -> 'fluticasone-propionate' without matching
     'morphine' -> 'apomorphine'.

Public API:
  norm(s), clean_drug(s), hub_base(s)
  annotate(consensus_df, hub_dir, out_csv) -> DataFrame
"""
import os
import re
import pandas as pd

HUB_DIR_DEFAULT = "/mnt/datalake/broad_drug_repurposing_hub"

# Known salt/ester/hydrate suffixes -- only these are stripped for base-name matching.
SALT_SUFFIXES = {
    "propionate", "acetate", "benzoate", "cypionate", "hemisuccinate", "butyrate",
    "hydrochloride", "sulfate", "phosphate", "sodium", "maleate", "citrate", "tartrate",
    "mesylate", "besylate", "succinate", "fumarate", "dihydrochloride", "valerate",
    "furoate", "xinafoate", "decanoate", "hemihydrate", "potassium", "calcium", "bromide",
    "chloride", "nitrate", "tosylate", "palmitate", "enanthate", "undecanoate",
    "dipropionate", "diacetate", "pivalate", "aceponate", "monohydrate", "hydrate",
    "hydrobromide", "sulphate",
}

PHASE_RANK = {
    "Launched": 6, "Phase 3": 5, "Phase 2/Phase 3": 4, "Phase 2": 3,
    "Phase 1/Phase 2": 2, "Phase 1": 1, "Preclinical": 0, "Withdrawn": -1,
}


def norm(s):
    """Lowercase, strip parentheticals, collapse to alphanumeric-space tokens."""
    if not isinstance(s, str):
        return ""
    s = s.lower().strip()
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)          # drop parentheticals
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()      # keep alnum
    return s


def clean_drug(s):
    """Clean a perturbation name for DISPLAY (Hub matching uses norm() independently).

    Removes bracketed qualifiers that are not part of the drug identity: dose/time/
    concentration annotations (e.g. '(30 h)', '(1.25 um)', HTML-entity-garbled units) and
    parenthetical brand names (e.g. 'imatinib (glivec)' -> 'imatinib'). Conservative: only
    strips parentheticals and collapses whitespace; never alters the base token. This means
    L1000 time-course / dose replicates of the same drug collapse to a single display name,
    which is why the approved count can differ by +/-1 from a run that kept them separate.
    """
    if not isinstance(s, str):
        return s
    # remove any parenthetical qualifier (time, dose/concentration, brand, garbled units)
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    # tidy stray HTML entities / non-standard chars left behind, collapse whitespace
    s = re.sub(r"&[a-z]+;|&#\d+;", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def hub_base(n):
    """Return the base name after stripping a single trailing salt token (if it's a known salt)."""
    toks = n.split()
    if len(toks) >= 2 and toks[-1] in SALT_SUFFIXES:
        return " ".join(toks[:-1])
    return n


def _build_hub(hub_dir):
    moa = pd.read_parquet(os.path.join(hub_dir, "broad_repurposing_hub_phase_moa_target_info.parquet"))
    mol = pd.read_parquet(os.path.join(hub_dir, "broad_repurposing_hub_molecule_with_smiles.parquet"))
    # one row per molecule for smiles (first non-null)
    mol_small = (mol.sort_values("purity", ascending=False)
                 .groupby("pert_iname", as_index=False)
                 .first()[["pert_iname", "smiles", "InChIKey", "pubchem_cid"]])
    hub = moa.merge(mol_small, on="pert_iname", how="left")
    hub["norm"] = hub["pert_iname"].map(norm)
    hub["base"] = hub["norm"].map(hub_base)
    hub["phase_rank"] = hub["clinical_phase"].map(PHASE_RANK).fillna(-2)
    return hub


def annotate(consensus_df, hub_dir=HUB_DIR_DEFAULT,
             out_csv="/workspace/dri_run/data/annotated.csv"):
    hub = _build_hub(hub_dir)
    # exact-normalized lookup: keep the highest-phase record per normalized name
    hub_exact = hub.sort_values("phase_rank", ascending=False).drop_duplicates("norm")
    exact_map = hub_exact.set_index("norm")
    hub_base_tbl = hub.sort_values("phase_rank", ascending=False).drop_duplicates("base")
    base_map = hub_base_tbl.set_index("base")

    cols = ["clinical_phase", "moa", "target", "disease_area", "indication",
            "smiles", "InChIKey", "pubchem_cid", "pert_iname"]
    recs = []
    for _, r in consensus_df.iterrows():
        cn = clean_drug(r["pert"])
        nn = norm(cn)
        match, how = None, "none"
        if nn in exact_map.index:
            match = exact_map.loc[nn]
            how = "exact"
        else:
            bn = hub_base(nn)
            if bn in base_map.index:
                match = base_map.loc[bn]
                how = "salt_base"
        rec = {"drug": cn, "match_type": how}
        if match is not None:
            for c in cols:
                rec[c] = match[c]
        else:
            for c in cols:
                rec[c] = None
        recs.append(rec)
    ann = pd.DataFrame(recs)
    # IMPORTANT: preserve the incoming canonical_rank order. Do NOT sort here -- annotation is
    # a left-join on the already-ranked consensus frame; every downstream consumer relies on
    # this frame staying in canonical_rank order.
    merged = pd.concat([consensus_df.reset_index(drop=True), ann.drop(columns=["drug"])], axis=1)
    merged["drug"] = ann["drug"]
    merged["approved"] = merged["clinical_phase"].eq("Launched")
    merged["phase_rank"] = merged["clinical_phase"].map(PHASE_RANK).fillna(-2)
    if "canonical_rank" in merged.columns:
        # guard: annotation must not have reordered rows
        assert list(merged["canonical_rank"]) == sorted(merged["canonical_rank"]), \
            "annotate_hub reordered rows; canonical_rank order was not preserved"
    merged.to_csv(out_csv, index=False)
    n_any = int(merged["clinical_phase"].notna().sum())
    n_appr = int(merged["approved"].sum())
    print(f"[annotate] matched {n_any} to Hub (any phase); {n_appr} approved (Launched)")
    print(f"[annotate] wrote {out_csv}")
    return merged


if __name__ == "__main__":
    import sys
    inp = sys.argv[1] if len(sys.argv) > 1 else "/workspace/dri_run/data/consensus.csv"
    out = sys.argv[2] if len(sys.argv) > 2 else "/workspace/dri_run/data/annotated.csv"
    annotate(pd.read_csv(inp), out_csv=out)
