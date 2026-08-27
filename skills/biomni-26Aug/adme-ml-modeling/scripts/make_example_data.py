"""
make_example_data.py -- Generate a small SYNTHETIC ADME-like dataset. This is an OFFLINE
SMOKE-TEST helper only: it is NOT real measurements and must not be used for headline results.
It builds a semi-realistic logD-like regression label (correlated with clogP/TPSA plus noise) so
the pipeline has signal to find on a deterministic, network-free input, plus a derived binary
"permeable" label and a fake registration date for time-splits.

For the DEFAULT demonstration and any real result, fetch a real public benchmark instead:
``scripts/fetch_benchmark.py`` (Lipophilicity_AstraZeneca and AqSolDB via Harvard Dataverse/TDC,
CC BY 4.0) and ``scripts/build_sample_test_set.py`` build the shipped demonstration set from real
measured logD. See references/example_data.md and references/assay_schema.md.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors

# A compact, diverse seed list of real drug-like SMILES (well-known molecules).
SEED_SMILES = [
    "CC(=O)Oc1ccccc1C(=O)O", "CN1C=NC2=C1C(=O)N(C(=O)N2C)C", "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
    "OC(=O)c1ccccc1O", "CC(=O)Nc1ccc(O)cc1", "Clc1ccccc1", "c1ccc2ccccc2c1",
    "CCN(CC)CC", "OCC(O)CO", "CC(N)Cc1ccccc1", "COc1ccc2cc(ccc2c1)C(C)C(=O)O",
    "CN1CCC[C@H]1c1cccnc1", "Oc1ccc(cc1)C(=O)C", "CCOC(=O)c1ccccc1",
    "c1ccc(cc1)S(=O)(=O)N", "NC(=O)c1ccncc1", "Cc1ccc(cc1)S(=O)(=O)N",
    "CC(C)(C)NCC(O)c1ccc(O)c(O)c1", "COc1ccccc1OC", "CCc1ccccc1",
    "O=C1CCCCC1", "c1ccncc1", "c1ccoc1", "c1cc[nH]c1", "CC(=O)Nc1ccccc1",
    "Oc1ccccc1", "Nc1ccccc1", "Cc1ccccc1C", "CCCCCCCC(=O)O", "CCCCCCO",
    "c1ccc(cc1)c1ccccc1", "O=C(O)c1ccc(cc1)N", "CN(C)c1ccccc1",
    "C1CCNCC1", "C1CCOC1", "CC(C)NCC(O)COc1ccccc1", "Clc1ccc(cc1)Cl",
    "Fc1ccc(cc1)C(F)(F)F", "Brc1ccccc1", "Ic1ccccc1", "N#Cc1ccccc1",
]


def _elaborate(smiles_list, n_target, rng):
    """Grow the seed set with simple, valid decorations to reach n_target molecules."""
    subs = ["C", "CC", "O", "N", "F", "Cl", "C(=O)O", "OC", "N(C)C", "S(=O)(=O)N"]
    out = list(dict.fromkeys(smiles_list))
    attempts = 0
    while len(out) < n_target and attempts < n_target * 40:
        attempts += 1
        base = rng.choice(smiles_list)
        mol = Chem.MolFromSmiles(base)
        if mol is None:
            continue
        # attach a substituent to an aromatic carbon by naive SMILES concat via RWMol
        sub = rng.choice(subs)
        cand = base + sub if rng.random() < 0.3 else _rdkit_decorate(mol, sub, rng)
        m = Chem.MolFromSmiles(cand) if cand else None
        if m is not None and 5 <= m.GetNumHeavyAtoms() <= 45:
            out.append(Chem.MolToSmiles(m))
    return list(dict.fromkeys(out))[:n_target]


def _rdkit_decorate(mol, sub, rng):
    try:
        rw = Chem.RWMol(mol)
        sub_mol = Chem.MolFromSmiles(sub)
        if sub_mol is None:
            return None
        combo = Chem.CombineMols(rw, sub_mol)
        rwc = Chem.RWMol(combo)
        # bond a random heavy atom of the base to the first atom of the sub
        base_atoms = [a.GetIdx() for a in mol.GetAtoms()
                      if a.GetTotalNumHs() > 0 and a.GetIdx() < mol.GetNumAtoms()]
        if not base_atoms:
            return None
        a1 = int(rng.choice(base_atoms))
        a2 = mol.GetNumAtoms()
        rwc.AddBond(a1, a2, Chem.BondType.SINGLE)
        m = rwc.GetMol()
        Chem.SanitizeMol(m)
        return Chem.MolToSmiles(m)
    except Exception:  # noqa: BLE001 - reject any invalid synthetic decoration
        return None


def make_dataset(n=300, seed=0, noise=0.4):
    rng = np.random.default_rng(seed)
    smiles = _elaborate(SEED_SMILES, n, rng)
    rows = []
    for smi in smiles:
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            continue
        clogp = Crippen.MolLogP(mol)
        tpsa = Descriptors.TPSA(mol)
        mw = Descriptors.MolWt(mol)
        # synthetic logD-like target: driven by lipophilicity & polarity + noise
        logd = 0.9 * clogp - 0.015 * tpsa - 0.002 * mw + rng.normal(scale=noise)
        # fake registration date (spread over ~3 years) to enable time-splits
        date = pd.Timestamp("2021-01-01") + pd.to_timedelta(int(rng.integers(0, 1000)), unit="D")
        rows.append({"smiles": smi, "logD": round(float(logd), 3),
                     "permeable": int(logd > 1.5), "date": date.date().isoformat()})
    return pd.DataFrame(rows)


def make_heldout_prediction_set(
    train_smiles: list[str], n: int = 25, seed: int = 99, pool_size: int = 200
) -> pd.DataFrame:
    """Build a genuinely held-out in-distribution prediction set.

    The previous implementation called ``make_dataset(n=25, seed=...)`` which, because
    ``_elaborate`` seeds itself from the same ``SEED_SMILES`` list, returned the first 25
    seed molecules verbatim -- exact training-set members with nearest-neighbour
    similarity 1.000.  Scoring that set exercises the code path but tests nothing about
    in-domain deployment.

    This function instead grows a *larger* decorated pool from the same seed list using an
    independent RNG, removes any SMILES already present in the training set, and returns
    the first ``n`` novel molecules.  Achieved nearest-neighbour Tanimoto similarity is
    measured (not assumed) and printed so the in-domain coverage of the returned set is
    visible rather than asserted.
    """
    rng = np.random.default_rng(seed)
    pool = _elaborate(SEED_SMILES, pool_size, rng)
    train_set = set(train_smiles)
    novel = [smi for smi in pool if smi not in train_set]
    if len(novel) < n:
        raise ValueError(
            f"only {len(novel)} novel molecules available; increase pool_size "
            f"(currently {pool_size})"
        )
    selected = novel[:n]
    similarity = _nearest_similarity(train_smiles, selected)
    print(
        f"make_heldout_prediction_set: NN Tanimoto similarity to training -- "
        f"min={float(np.min(similarity)):.3f} "
        f"median={float(np.median(similarity)):.3f} "
        f"max={float(np.max(similarity)):.3f} (n={len(selected)})"
    )
    return pd.DataFrame({"smiles": selected})



def _nearest_similarity(train_smiles: list[str], query_smiles: list[str]) -> np.ndarray:
    """Lazy import of the runtime similarity helper to avoid a hard import-time dependency."""
    import sys
    from pathlib import Path

    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from adme_skill.modeling import nearest_similarity

    return nearest_similarity(train_smiles, query_smiles)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="example_adme.csv")
    ap.add_argument("--predict-out", default="example_predict.csv",
                    help="also write a small unlabelled prediction set")
    a = ap.parse_args()
    df = make_dataset(n=a.n, seed=a.seed)
    df.to_csv(a.out, index=False)
    print(f"Wrote {len(df)} rows -> {a.out}")
    # a genuinely held-out in-distribution prediction set: novel decorated molecules
    # from the same chemical space, filtered to exclude training-set members.
    pred = make_heldout_prediction_set(df["smiles"].tolist(), n=25, seed=a.seed + 99)
    pred.to_csv(a.predict_out, index=False)
    print(f"Wrote {len(pred)} unlabelled rows -> {a.predict_out}")
