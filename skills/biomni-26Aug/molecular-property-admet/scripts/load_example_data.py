"""
Load example FDA-approved drug panel or user-provided molecules.

Provides a curated set of ~30 well-known drugs with diverse ADMET profiles
for demonstration, plus utilities for loading user SMILES data.
"""

import pandas as pd

try:
    from rdkit import Chem
    HAS_RDKIT = True
except ImportError:
    HAS_RDKIT = False


# =============================================================================
# FDA-Approved Drug Panel — curated for diverse ADMET profiles
# =============================================================================
EXAMPLE_DRUGS = [
    # NSAIDs / Analgesics — familiar, good oral bioavailability
    {"name": "Aspirin", "smiles": "CC(=O)Oc1ccccc1C(=O)O", "therapeutic_class": "NSAID"},
    {"name": "Ibuprofen", "smiles": "CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)O", "therapeutic_class": "NSAID"},
    {"name": "Acetaminophen", "smiles": "CC(=O)Nc1ccc(O)cc1", "therapeutic_class": "Analgesic"},
    {"name": "Naproxen", "smiles": "COc1ccc2cc([C@@H](C)C(=O)O)ccc2c1", "therapeutic_class": "NSAID"},

    # CNS-penetrant — BBB+ examples
    {"name": "Caffeine", "smiles": "Cn1c(=O)c2c(ncn2C)n(C)c1=O", "therapeutic_class": "Stimulant"},
    {"name": "Fluoxetine", "smiles": "CNCCC(Oc1ccc(cc1)C(F)(F)F)c1ccccc1", "therapeutic_class": "SSRI"},
    {"name": "Diazepam", "smiles": "CN1C(=O)CN=C(c2ccccc2)c2cc(Cl)ccc21", "therapeutic_class": "Benzodiazepine"},

    # Statins — high protein binding, CYP substrates
    {"name": "Atorvastatin", "smiles": "CC(C)c1n(CC[C@@H](O)C[C@@H](O)CC(=O)O)c(c2ccc(F)cc2)c(c1c1ccccc1)C(=O)Nc1ccccc1", "therapeutic_class": "Statin"},
    {"name": "Rosuvastatin", "smiles": "CC(C)c1nc(N(C)S(C)(=O)=O)nc(c1/C=C/[C@@H](O)C[C@@H](O)CC(=O)O)c1ccc(F)cc1", "therapeutic_class": "Statin"},

    # CYP inhibitors — drug-drug interaction risk
    {"name": "Ketoconazole", "smiles": "CC(=O)N1CCN(CC1)c1ccc(OC[C@H]2CO[C@@](Cn3ccnc3)(c3ccc(Cl)cc3Cl)O2)cc1", "therapeutic_class": "Antifungal"},
    {"name": "Ritonavir", "smiles": "CC(C)[C@@H](NC(=O)N(C)Cc1csc(n1)C(C)C)C(=O)N[C@H](C[C@@H](O)[C@H](Cc1ccccc1)NC(=O)OCc1cncs1)Cc1ccccc1", "therapeutic_class": "Antiretroviral"},

    # CYP2D6 substrates — pharmacogenomics relevance
    {"name": "Metoprolol", "smiles": "COCCc1ccc(OCC(O)CNC(C)C)cc1", "therapeutic_class": "Beta-blocker"},
    {"name": "Codeine", "smiles": "COc1ccc2C[C@H]3N(C)CC[C@@]45[C@@H](Oc1c24)[C@@H](O)C=C[C@@H]35", "therapeutic_class": "Opioid"},

    # hERG risk — withdrawn drugs (negative examples for cardiac toxicity)
    {"name": "Cisapride", "smiles": "COc1cc(N)c(Cl)cc1C(=O)N[C@@H]1CC(OC)CCN1CCCC(=O)c1ccc(F)cc1", "therapeutic_class": "Prokinetic (withdrawn)"},
    {"name": "Terfenadine", "smiles": "CC(C)(C)c1ccc(cc1)C(O)CCCN1CCC(CC1)C(O)(c1ccccc1)c1ccccc1", "therapeutic_class": "Antihistamine (withdrawn)"},

    # Hepatotoxicity risk
    {"name": "Isoniazid", "smiles": "NNC(=O)c1ccncc1", "therapeutic_class": "Anti-tuberculosis"},

    # Minimal hepatic metabolism — renal clearance
    {"name": "Lisinopril", "smiles": "NCCCC[C@@H](N[C@@H](CCc1ccccc1)C(=O)O)C(=O)N1CCCC1C(=O)O", "therapeutic_class": "ACE inhibitor"},
    {"name": "Metformin", "smiles": "CN(C)C(=N)NC(=N)N", "therapeutic_class": "Antidiabetic"},

    # Kinase inhibitors — oncology (J&J-relevant)
    {"name": "Imatinib", "smiles": "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1", "therapeutic_class": "Kinase inhibitor"},
    {"name": "Erlotinib", "smiles": "C#Cc1cccc(Nc2ncnc3cc(OCCOC)c(OCCOC)cc23)c1", "therapeutic_class": "Kinase inhibitor"},

    # Anticoagulants — narrow therapeutic index
    {"name": "Warfarin", "smiles": "CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O", "therapeutic_class": "Anticoagulant"},
    {"name": "Rivaroxaban", "smiles": "O=C1CN(c2ccc(N3CC(=O)N(c4ccc(Cl)cc4)C3=O)cc2)c2cc(c(Cl)cc2O1)", "therapeutic_class": "Anticoagulant"},

    # Antibiotics — diverse absorption profiles
    {"name": "Ciprofloxacin", "smiles": "O=C(O)c1cn(C2CC2)c2cc(N3CCNCC3)c(F)cc2c1=O", "therapeutic_class": "Fluoroquinolone"},
    {"name": "Amoxicillin", "smiles": "CC1(C)S[C@@H]2[C@H](NC(=O)[C@@H](N)c3ccc(O)cc3)C(=O)N2[C@@H]1C(=O)O", "therapeutic_class": "Beta-lactam"},

    # Antihistamines
    {"name": "Loratadine", "smiles": "CCOC(=O)N1CCC(=C2c3ccc(Cl)cc3CCc3ncccc32)CC1", "therapeutic_class": "Antihistamine"},
    {"name": "Cetirizine", "smiles": "OC(=O)COCCN1CCN(CC1)C(c1ccccc1)c1ccc(Cl)cc1", "therapeutic_class": "Antihistamine"},

    # Proton pump inhibitor
    {"name": "Omeprazole", "smiles": "COc1ccc2[nH]c(S(=O)Cc3ncc(C)c(OC)c3C)nc2c1", "therapeutic_class": "PPI"},

    # Antihypertensive
    {"name": "Amlodipine", "smiles": "CCOC(=O)C1=C(COCCN)NC(C)=C(C1c1ccccc1Cl)C(=O)OC", "therapeutic_class": "Calcium channel blocker"},

    # Anti-diabetic (GLP-1 era context)
    {"name": "Sitagliptin", "smiles": "N[C@H](CC(=O)N1CCn2c(nnc2C(F)(F)F)C1)Cc1cc(F)c(F)cc1F", "therapeutic_class": "DPP-4 inhibitor"},

    # Immunosuppressant
    {"name": "Dexamethasone", "smiles": "C[C@@H]1C[C@H]2[C@@H]3CCC4=CC(=O)C=C[C@]4(C)[C@@]3(F)[C@@H](O)C[C@]2(C)[C@@]1(O)C(=O)CO", "therapeutic_class": "Corticosteroid"},
]


# =============================================================================
# Example screening hit-list — a deliberately "messy" set for the robustness demo
# =============================================================================
# Mimics a real Genedata Screener / ChEMBL actives export: assay columns
# (IC50, Hill slope, QC flag), salt forms, a duplicate structure under two names,
# a PAINS frequent-hitter, hERG-withdrawn drugs, and a non-small-molecule. Running
# the skill on this set exercises every robustness feature — standardization
# (desalting), de-duplication, sanity-flagging — plus QED ranking, PAINS, and ADMET.
EXAMPLE_HITLIST = [
    # Known active + its old code name = SAME structure -> de-duplication demo
    {"name": "Imatinib", "smiles": "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1", "IC50_nM": 12, "hill_slope": 1.0, "qc_flag": "pass"},
    {"name": "STI-571", "smiles": "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1", "IC50_nM": 15, "hill_slope": 1.1, "qc_flag": "pass"},
    # Salt forms -> desalting demo (MW must reflect the parent, not the counterion)
    {"name": "Fluoxetine HCl", "smiles": "CNCCC(Oc1ccc(cc1)C(F)(F)F)c1ccccc1.Cl", "IC50_nM": 120, "hill_slope": 0.9, "qc_flag": "pass"},
    {"name": "Diclofenac sodium", "smiles": "[Na+].[O-]C(=O)Cc1ccccc1Nc1c(Cl)cccc1Cl", "IC50_nM": 240, "hill_slope": 1.0, "qc_flag": "pass"},
    # hERG-withdrawn drugs -> ADMET safety heatmap should light up red on hERG
    {"name": "Cisapride", "smiles": "COc1cc(N)c(Cl)cc1C(=O)N[C@@H]1CC(OC)CCN1CCCC(=O)c1ccc(F)cc1", "IC50_nM": 35, "hill_slope": 1.0, "qc_flag": "pass"},
    {"name": "Terfenadine", "smiles": "CC(C)(C)c1ccc(cc1)C(O)CCCN1CCC(CC1)C(O)(c1ccccc1)c1ccccc1", "IC50_nM": 60, "hill_slope": 1.2, "qc_flag": "pass"},
    # Pan-CYP inhibitor (DDI) + very low QED / high MW
    {"name": "Ritonavir", "smiles": "CC(C)[C@@H](NC(=O)N(C)Cc1csc(n1)C(C)C)C(=O)N[C@H](C[C@@H](O)[C@H](Cc1ccccc1)NC(=O)OCc1cncs1)Cc1ccccc1", "IC50_nM": 18, "hill_slope": 0.95, "qc_flag": "pass"},
    # Lipinski-borderline drug-likeness contrast
    {"name": "Atorvastatin", "smiles": "CC(C)c1n(CC[C@@H](O)C[C@@H](O)CC(=O)O)c(c2ccc(F)cc2)c(c1c1ccccc1)C(=O)Nc1ccccc1", "IC50_nM": 200, "hill_slope": 0.8, "qc_flag": "pass"},
    # Clean, small, high-QED anchor (the "good" compound)
    {"name": "Caffeine", "smiles": "Cn1c(=O)c2c(ncn2C)n(C)c1=O", "IC50_nM": 5000, "hill_slope": 1.0, "qc_flag": "flag-weak"},
    # Famous PAINS frequent-hitter (steep Hill is a classic artifact tell) -> PAINS hard flag
    {"name": "Quercetin", "smiles": "O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12", "IC50_nM": 300, "hill_slope": 1.8, "qc_flag": "pass"},
    # Non-small-molecule that slipped into the plate -> sanity_flag (no carbon / inorganic)
    {"name": "Cisplatin", "smiles": "N.N.Cl[Pt]Cl", "IC50_nM": 800, "hill_slope": 1.0, "qc_flag": "pass"},
]


def load_example_drugs():
    """
    Load curated FDA-approved drug panel for ADMET demonstration.

    Returns a DataFrame with ~30 well-known drugs spanning diverse therapeutic
    classes and ADMET profiles. Includes known positive and negative examples
    for key endpoints (hERG, BBB, CYP inhibition, hepatotoxicity).

    Returns
    -------
    pd.DataFrame
        Columns: name, smiles, therapeutic_class
    """
    df = pd.DataFrame(EXAMPLE_DRUGS)

    # Validate SMILES if RDKit available
    if HAS_RDKIT:
        valid = []
        for _, row in df.iterrows():
            mol = Chem.MolFromSmiles(row["smiles"])
            if mol is not None:
                valid.append(True)
            else:
                print(f"   WARNING: Invalid SMILES for {row['name']}, skipping")
                valid.append(False)
        df = df[valid].reset_index(drop=True)

    print(f"✓ Data loaded successfully! {len(df)} molecules ready for analysis.")
    print(f"  Therapeutic classes: {', '.join(df['therapeutic_class'].unique())}")
    return df


def load_example_hitlist():
    """
    Load the example screening hit-list — a deliberately messy, real-world-flavored set.

    Eleven "confirmed actives" with assay columns (IC50_nM, hill_slope, qc_flag) that
    mimic a Genedata Screener / ChEMBL export: includes two salt forms, a duplicate
    structure (Imatinib ≡ STI-571), a PAINS frequent-hitter (quercetin), two
    hERG-withdrawn drugs (cisapride, terfenadine), and a non-small-molecule
    (cisplatin). Use it to demo standardization, de-duplication, sanity-flagging,
    QED ranking, PAINS, and ADMET in one run.

    Returns
    -------
    pd.DataFrame
        Columns: name, smiles, IC50_nM, hill_slope, qc_flag
    """
    df = pd.DataFrame(EXAMPLE_HITLIST)

    # Keep all rows that RDKit can parse (the messy bits — salts, the inorganic — are
    # handled downstream by standardize_molecules / the sanity gate, not dropped here).
    if HAS_RDKIT:
        df = validate_smiles(df)

    print(f"✓ Data loaded successfully! {len(df)} molecules ready for analysis.")
    print("  (Messy demo set: includes salts, a duplicate structure, a PAINS hit, "
          "hERG liabilities, and one non-small-molecule.)")
    return df


def load_user_molecules(file_path, smiles_col="smiles", name_col=None):
    """
    Load user-provided molecules from CSV, TSV, or text file.

    Parameters
    ----------
    file_path : str
        Path to input file. Supported formats:
        - CSV/TSV with a SMILES column
        - Text file with one SMILES per line
    smiles_col : str
        Column name containing SMILES (default: 'smiles')
    name_col : str, optional
        Column name for molecule names. If None, auto-generated.

    Returns
    -------
    pd.DataFrame
        Columns: name, smiles, (+ any additional columns from input)
    """
    import os

    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".csv", ".tsv"):
        sep = "\t" if ext == ".tsv" else ","
        df = pd.read_csv(file_path, sep=sep)
        # Try common column name variants
        smiles_variants = [smiles_col, "SMILES", "Smiles", "canonical_smiles",
                           "smiles_string", "smi", "structure"]
        found_col = None
        for variant in smiles_variants:
            if variant in df.columns:
                found_col = variant
                break
        if found_col is None:
            raise ValueError(
                f"No SMILES column found. Tried: {smiles_variants}. "
                f"Available columns: {list(df.columns)}"
            )
        df = df.rename(columns={found_col: "smiles"})
    elif ext in (".txt", ".smi"):
        with open(file_path) as f:
            smiles_list = [line.strip() for line in f if line.strip()]
        df = pd.DataFrame({"smiles": smiles_list})
    else:
        raise ValueError(f"Unsupported file format: {ext}. Use .csv, .tsv, .txt, or .smi")

    # Add names if missing
    if name_col and name_col in df.columns:
        df = df.rename(columns={name_col: "name"})
    elif "name" not in df.columns:
        df["name"] = [f"Mol_{i+1}" for i in range(len(df))]

    # Add therapeutic_class if missing
    if "therapeutic_class" not in df.columns:
        df["therapeutic_class"] = "User-provided"

    # Validate SMILES
    df = validate_smiles(df)

    print(f"✓ Data loaded successfully! {len(df)} molecules ready for analysis.")
    return df


def validate_smiles(df):
    """
    Validate SMILES strings using RDKit and remove invalid entries.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a 'smiles' column.

    Returns
    -------
    pd.DataFrame
        Filtered DataFrame with only valid SMILES.
    """
    if not HAS_RDKIT:
        print("   WARNING: RDKit not available, skipping SMILES validation")
        return df

    valid_mask = []
    invalid_count = 0
    for _, row in df.iterrows():
        mol = Chem.MolFromSmiles(row["smiles"])
        if mol is not None:
            valid_mask.append(True)
        else:
            valid_mask.append(False)
            invalid_count += 1
            print(f"   WARNING: Invalid SMILES for '{row.get('name', 'unknown')}': {row['smiles']}")

    if invalid_count > 0:
        print(f"   Removed {invalid_count} molecule(s) with invalid SMILES")

    return df[valid_mask].reset_index(drop=True)


if __name__ == "__main__":
    df = load_example_drugs()
    print(f"\nLoaded {len(df)} drugs:")
    print(df[["name", "therapeutic_class"]].to_string(index=False))
