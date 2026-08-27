"""OPTIONAL MODE: physicochemical / drug-likeness properties for top candidates.

Uses RDKit on the SMILES already attached from the Broad Repurposing Hub (annotate_hub
adds a 'smiles' column). Computes the standard developability descriptors and Lipinski /
Veber flags so the report can comment on oral-drug-likeness of the top hits. This is a
DESCRIPTIVE add-on, not a filter -- repurposing candidates are usually approved drugs
that already passed ADMET, so this mostly contextualises novelty/route.

Public API:
  compute_admet(annotated_df, top_n=15) -> DataFrame  (adds descriptor columns)

Requires: rdkit (import guarded; returns input unchanged with a warning if unavailable).
"""
import pandas as pd


def compute_admet(annotated_df, top_n=15):
    """Compute RDKit descriptors for the top_n reversers that have SMILES."""
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors, Crippen, Lipinski, rdMolDescriptors
    except Exception as e:  # noqa: BLE001
        print(f"[admet] RDKit unavailable ({e}); skipping ADMET mode.")
        return annotated_df

    df = annotated_df[annotated_df["S_reversal"] > 0].copy()
    if "smiles" in df.columns:
        df = df[df["smiles"].notna() & (df["smiles"].astype(str).str.len() > 0)]
    else:
        print("[admet] no 'smiles' column; run annotate_hub first.")
        return annotated_df
    df = df.sort_values("consensus_rank").head(top_n)

    recs = []
    for _, r in df.iterrows():
        smi = str(r["smiles"])
        m = Chem.MolFromSmiles(smi)
        if m is None:
            recs.append(dict(drug=r.get("drug", r.get("pert")), smiles=smi, mw=None))
            continue
        mw = Descriptors.MolWt(m)
        logp = Crippen.MolLogP(m)
        hbd = Lipinski.NumHDonors(m)
        hba = Lipinski.NumHAcceptors(m)
        tpsa = rdMolDescriptors.CalcTPSA(m)
        rotb = Lipinski.NumRotatableBonds(m)
        lipinski_viol = sum([mw > 500, logp > 5, hbd > 5, hba > 10])
        veber_ok = (rotb <= 10) and (tpsa <= 140)
        recs.append(dict(
            drug=r.get("drug", r.get("pert")),
            smiles=smi, mw=round(mw, 1), clogp=round(logp, 2),
            hbd=hbd, hba=hba, tpsa=round(tpsa, 1), rot_bonds=rotb,
            lipinski_violations=lipinski_viol,
            lipinski_pass=lipinski_viol <= 1,
            veber_pass=bool(veber_ok),
        ))
    return pd.DataFrame(recs)
