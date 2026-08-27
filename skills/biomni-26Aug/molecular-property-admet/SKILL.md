---
id: "skill_c30c5d550c6b897400e363e8d1664a3c"
name: "molecular-property-admet"
description: "Use to predict and compare SMILES-based small-molecule physicochemical properties, drug-likeness, and ADMET. Covers Lipinski/Veber/QED, PAINS and toxicophore alerts, CYP inhibition, hERG, AMES, DILI, compound standardization, and hit triage."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Profile my small-molecule SMILES for drug-likeness and ADMET (CYP, hERG, AMES, DILI) and flag developability liabilities."
---

# Small Molecule ADMET Prediction

Predict physicochemical properties, drug-likeness, and ADMET (Absorption, Distribution, Metabolism, Excretion, Toxicity) endpoints for small molecules from SMILES input. Generates publication-quality visualizations and safety profiles.

## When to Use This Skill

Use this skill when you need to:
- ✅ **Profile small molecules** for drug-likeness and ADMET properties
- ✅ **Assess Lipinski/Veber rule compliance** and **QED developability** for compound libraries
- ✅ **Flag PAINS** (assay-interference) and **toxicophore** (Brenk/NIH) structural alerts
- ✅ **Predict CYP inhibition** risk (CYP1A2, 2C9, 2C19, 2D6, 3A4)
- ✅ **Flag safety liabilities** (hERG, AMES mutagenicity, DILI)
- ✅ **Triage screening hits** by developability before deeper bioactivity/structure work
- ✅ **Process real-world hit lists** — salts, charged forms, and duplicates are standardized automatically; scales from a few to thousands of compounds
- ✅ **Compare ADMET profiles** across a drug panel or compound series
- ✅ **Generate ADMET reports** for medicinal chemistry decision-making

**Don't use this skill for:**
- ❌ Biologics/peptides/antibodies → SMILES-based models don't apply
- ❌ Protein-ligand docking or binding affinity → Use molecular docking skills
- ❌ QSAR model training → This uses pre-trained ADMET-AI models
- ❌ Reaction prediction or retrosynthesis

## Installation

| Software | Version | License | Commercial Use | Installation |
|----------|---------|---------|----------------|--------------|
| RDKit | ≥2023.03 | BSD-3-Clause | ✅ Permitted | `pip install rdkit` |
| pandas | ≥1.3 | BSD-3-Clause | ✅ Permitted | `pip install pandas` |
| numpy | ≥1.21 | BSD-3-Clause | ✅ Permitted | `pip install numpy` |
| seaborn | ≥0.11 | BSD-3-Clause | ✅ Permitted | `pip install seaborn` |
| matplotlib | ≥3.4 | PSF | ✅ Permitted | `pip install matplotlib` |
| ADMET-AI | ≥2.0 | MIT | ✅ Permitted | `pip install admet-ai` |
| requests | ≥2.25 | Apache-2.0 | ✅ Permitted | `pip install requests` (only to *rebuild* the reference) |
| adjustText | ≥0.8 | MIT | ✅ Permitted | `pip install adjustText` (optional — de-collides scatter labels; graceful fallback if absent) |

**Quick install:**
```bash
pip install rdkit admet-ai pandas numpy seaborn matplotlib
```

**ADMET-AI is only needed for ADMET prediction** (the default). For physicochemical + drug-likeness + QED + structural alerts alone, run `run_full_analysis(df, include_admet=False)` — that path needs only RDKit + pandas/numpy/seaborn/matplotlib (no `admet-ai`/torch). The final PDF report is generated with the Biomni `pdf-report-generation` skill after analysis artifacts are written.

**Percentile reference data (commercially permissive):** ADMET percentiles are computed against a bundled ChEMBL approved-drug reference set (`assets/chembl_approved_reference.csv`, CC BY-SA 3.0), **not** ADMET-AI's default DrugBank reference (CC BY-NC, which prohibits commercial use). The reference file ships with the skill, so no download or network access is needed for normal analysis. `requests` is only needed to *regenerate* the reference with `scripts/build_reference_set.py` (see "Percentile reference set" below).

## Inputs

- **SMILES strings** — CSV/TSV with a `smiles` column, or text file with one SMILES per line
- **Molecule names** (optional) — `name` column in CSV, or auto-generated
- **Example data** — two built-in sets (no download): an FDA-approved drug panel (~30 clean drugs) for the ADMET showcase, and a messy screening hit-list (`load_example_hitlist()`, also at `assets/demo_hitlist.csv`) that mimics a real Genedata Screener / ChEMBL export — salts, a duplicate, a PAINS hit, hERG liabilities, a non-small-molecule — for the robustness showcase

**Supported formats:** `.csv`, `.tsv`, `.txt`, `.smi`

**Robust to messy input:** salts/counterions are stripped to the drug-like parent, charged forms are neutralized, structures are canonicalized and de-duplicated, and non-drug-like entries (inorganics, out-of-range MW, unparseable) are *flagged* (`sanity_flag`), never silently dropped. Duplicate or blank names are fine — a unique `mol_id` keys all outputs.

## Outputs

**CSV files:**
- `all_properties.csv` — Complete results (molecules × all properties)
- `druglikeness_summary.csv` — Lipinski/Veber/QED assessment with pass/fail + alert + sanity flags
- `structural_alerts.csv` — Per-compound PAINS + toxicophore (Brenk/NIH) substructures
- `admet_predictions.csv` — ADMET-AI predictions (~41 endpoints)
- `flagged_compounds.csv` — Molecules with triage concerns (input quality, PAINS, ≥2 Lipinski violations, hERG+, AMES+, DILI+)

**Visualizations (PNG + SVG) — 4 plots, all scale from a few to thousands of molecules:**
- `physicochemical_overview` — Distribution panel (MW, LogP, TPSA, HBD, HBA, RotatableBonds) with drug-like reference lines
- `lipinski_space` — MW vs LogP; labeled scatter at small N, density (hexbin) at large N
- `developability_qed` — Top-N QED ranking bar, colored by PAINS status
- `admet_heatmap` — Registry-driven ADMET endpoint heatmap (top-N rows by predicted risk; rendered only when ADMET-AI columns are present)

**Key result columns:** `mol_id` (unique key), `input_smiles` (original) + `smiles` (standardized parent) + `standardization_note`, `sanity_flag`, `names_aggregated`; `QED` (0–1 developability); `PAINS_Count` / `PAINS_Alerts`, `Toxicophore_Count` / `Toxicophore_Alerts`, `PAINS_Pass` — alongside the physicochemical, Lipinski/Veber, and ADMET columns.

**ADMET percentile columns:** each ADMET-AI endpoint has a companion `<endpoint>_chembl_approved_percentile` column (0–100) giving the compound's rank for that endpoint relative to the bundled **ChEMBL** approved-drug reference. (These are named `*_chembl_approved_percentile`, not ADMET-AI's default `*_drugbank_approved_percentile` — see "Percentile reference set" below.)

**Reports:**
- `analysis_report.md` — Markdown summary
- `admet_analysis_report.pdf` — PDF report created with `pdf-report-generation`. When ADMET percentiles are present, include a short **"Reference-percentile context"** section (a small table of panel percentile position for decision-relevant endpoints — e.g. hERG, DILI, AMES, CYP3A4, BBB, solubility) with the ChEMBL attribution line; `scripts/build_report.py` is the worked reference implementation for the example panel.

**Analysis objects:**
- `analysis_object.pkl` — Complete analysis for downstream use
  - Load with: `obj = pickle.load(open('analysis_object.pkl', 'rb'))`

## Percentile Reference Set (commercially permissive)

ADMET-AI reports each endpoint both as an absolute value and as a **percentile against a reference set of approved drugs**, which is what makes a raw score interpretable (a hERG probability is only concerning if it is high *relative to approved drugs*). ADMET-AI ships this reference as DrugBank data, which is licensed **CC BY-NC** and therefore cannot be used commercially.

This skill replaces that reference with a **ChEMBL approved small-molecule drug set** so the entire workflow is free of commercial-use restrictions:

- **What ships:** `assets/chembl_approved_reference.csv` — approved small molecules from ChEMBL (`max_phase = 4`), standardized identically to the analysis pipeline, with the same 52 ADMET-AI property/prediction columns as the reference. Provenance (source, license, ChEMBL/ADMET-AI versions, molecule count, build date) is recorded in `assets/chembl_approved_reference.meta.json`; license terms are in `assets/REFERENCE_LICENSE.md`.
- **License:** ChEMBL data is **CC BY-SA 3.0** (attribution + share-alike), which **permits commercial use** — unlike DrugBank's CC BY-NC.
- **How percentiles are computed:** `compute_properties.predict_admet()` instantiates `ADMETModel(drugbank_path=assets/chembl_approved_reference.csv)`, so `scipy`'s `percentileofscore` ranks each query compound against the ChEMBL reference. The reference values are **ADMET-AI model predictions** on the ChEMBL set (exactly how DrugBank's shipped reference was produced) — not experimental measurements.
- **Output naming:** percentile columns are renamed to **`<endpoint>_chembl_approved_percentile`** (ADMET-AI hardcodes a `drugbank_approved_percentile` suffix internally; the skill renames them post-prediction so column names reflect the true source). **Downstream code that referenced `*_drugbank_approved_percentile` must use `*_chembl_approved_percentile`.**
- **Predictions are unchanged.** Swapping the reference changes only the percentile *context* columns; every absolute ADMET value, flag, and drug-likeness call is identical to stock ADMET-AI.
- **Fallback:** if the reference file is missing, `predict_admet()` runs with `drugbank_path=None` (percentiles disabled) and warns — it never silently falls back to the bundled DrugBank data.

**Rebuilding the reference (optional):**
```bash
python scripts/build_reference_set.py            # full build (queries ChEMBL, runs ADMET-AI)
python scripts/build_reference_set.py --limit 200 --out-dir /tmp/ref_test   # quick pilot
```
This fetches approved drugs from the ChEMBL REST API (`requests`), standardizes + de-duplicates them, runs ADMET-AI to populate the property columns, and writes the CSV + `meta.json`. Only needed to refresh against a newer ChEMBL release; the shipped file is sufficient for normal use.

## Report Packaging

When a PDF report is requested or listed in Outputs, run the analysis first, then
load and use the Biomni `pdf-report-generation` skill for the final PDF
deliverable. Build the PDF from this skill's markdown summary, result tables,
and generated figures, and save it using the PDF filename listed in Outputs, or
a stable descriptive filename when Outputs does not define one.

Keep this skill focused on scientific workflow and artifact content. Do not add
custom figure appearance or report layout instructions here; those are handled
by the platform prompt and dedicated reporting skills. If `pdf-report-generation`
is unavailable, use the packaged markdown/HTML/script fallback when present and
clearly disclose the fallback.

## Clarification Questions

1. **Input Files** (ASK THIS FIRST):
   - Do you have a SMILES file (.csv/.tsv/.txt) to analyze?
   - If uploaded: Is this the molecule set you'd like to profile?
   - **Or use example data?** Two built-in sets: (a) `load_example_drugs()` — ~30 clean FDA drugs (ADMET showcase); (b) `load_example_hitlist()` — a messy 11-compound screening hit-list with salts/duplicate/PAINS/hERG-liabilities (robustness showcase)

2. **Analysis Scope:**
   - a) Full ADMET profiling — all ~41 endpoints + physicochemical + drug-likeness (recommended) → `run_full_analysis(df)`
   - b) Physicochemical + drug-likeness only — faster, no ADMET-AI/torch needed → `run_full_analysis(df, include_admet=False)`

3. **Focus Area:**
   - a) Comprehensive overview — all endpoints (recommended for first analysis)
   - b) Safety-focused — prioritize hERG, AMES, DILI, CYP inhibition
   - c) Absorption/distribution — Caco-2, BBB, HIA, plasma protein binding
   - d) Developability triage — rank by QED, flag PAINS/toxicophores (for hit-list prioritization)

## Standard Workflow

🚨 **MANDATORY: USE SCRIPTS EXACTLY AS SHOWN — DO NOT WRITE INLINE CODE** 🚨

**Step 1 — Load data:**
```python
from scripts.load_example_data import load_example_drugs
df = load_example_drugs()
```
**Messy demo hit-list** (showcases standardization/dedup/sanity-flags): `from scripts.load_example_data import load_example_hitlist; df = load_example_hitlist()`
**For user data:** `from scripts.load_example_data import load_user_molecules; df = load_user_molecules("path/to/file.csv")`

**✅ VERIFICATION:** `"✓ Data loaded successfully! {n} molecules ready for analysis."`

**Step 2 — Run analysis:**
```python
from scripts.compute_properties import run_full_analysis
results_df, summary = run_full_analysis(df)
```
This one call **standardizes** inputs (desalt/neutralize/canonicalize/de-duplicate, adds `mol_id`/`sanity_flag`), then computes physicochemical descriptors, Lipinski/Veber rules, **QED**, **PAINS + toxicophore alerts**, and ADMET-AI predictions.
**For a quick physicochemical/drug-likeness check** (or an environment without ADMET-AI/torch), skip the heavy ADMET step: `run_full_analysis(df, include_admet=False)`.
**DO NOT write inline RDKit, standardization, QED, FilterCatalog, or ADMET-AI code. Just use the script.**

**✅ VERIFICATION:** `"✓ Analysis completed successfully! {n} molecules × {m} properties computed."`

**Step 3 — Generate visualizations:**
```python
from scripts.generate_plots import generate_all_plots
generate_all_plots(results_df, output_dir="results")
```
🚨 **DO NOT write inline plotting code (seaborn, matplotlib, etc.). Just use the script.** 🚨

**✅ VERIFICATION:** `"✓ All plots generated successfully!"`

**Step 4 — Export results:**
```python
from scripts.export_results import export_all
export_all(results_df, summary, output_dir="results")
```
**DO NOT write custom export code. Use export_all().**

**✅ VERIFICATION:** `"=== Export Complete ==="`

⚠️ **CRITICAL — DO NOT:**
- ❌ **Write inline RDKit descriptor code** → **STOP: Use `run_full_analysis()`**
- ❌ **Write inline plotting code** → **STOP: Use `generate_all_plots()`**
- ❌ **Write custom export code** → **STOP: Use `export_all()`**
- ❌ **Try to install system dependencies manually**

**⚠️ IF SCRIPTS FAIL — Script Failure Hierarchy:**
1. **Fix and Retry (90%)** — Install missing package, re-run script
2. **Modify Script (5%)** — Edit the script file itself, document changes
3. **Use as Reference (4%)** — Read script, adapt approach, cite source
4. **Write from Scratch (1%)** — Only if genuinely impossible, explain why

**NEVER skip directly to writing inline code without trying the script first.**

## Common Issues

| Error | Cause | Solution |
|-------|-------|----------|
| `ModuleNotFoundError: rdkit` | RDKit not installed | `pip install rdkit` |
| `ModuleNotFoundError: admet_ai` | ADMET-AI not installed | `pip install admet-ai` |
| ADMET-AI model download on first run | Models cached locally (~200MB) | Wait for download to complete, subsequent runs are fast |
| `Invalid SMILES` warning | Malformed SMILES string | Standardization flags these (`smiles` = NaN, `sanity_flag = standardization_failed`); they are kept, not dropped, and excluded from ADMET prediction |
| Many compounds flagged `sanity_flag` | Inorganic/no-carbon, MW out of [100, 1500], or failed standardization | Advisory — kept for transparency. Filter on `sanity_flag == ""` for the clean drug-like set |
| Desalting `note` says "kept largest" but looks wrong | Large organic counterion (e.g. pamoate) can be bigger than the active | Check `standardization_note` MW delta; the largest-fragment rule is advisory for such salts |
| SVG export failed | Missing SVG support | Normal — PNG files still generated. `generate_all_plots()` handles fallback automatically |
| Most compounds have a toxicophore alert | Expected — Brenk/NIH alerts fire on many approved drugs | Toxicophores are **advisory**, not exclusion. Use `PAINS_Count` as the hard triage flag; treat `Toxicophore_Count` as a review prompt |
| ADMET heatmap not produced | ADMET-AI not installed, or no 0–1 classification endpoints present | Expected when running physicochemical-only; the other 3 plots still render |

## Suggested Next Steps

This skill is the **developability/ADMET front-end** of a hit-triage pipeline. After ADMET profiling, the QED + alert-filtered shortlist feeds:
- **Bioactivity & off-target profiling** — ChEMBL / BindingDB for known activity and selectivity
- **Target tractability** — Open Targets for disease association and druggability
- **Structure & docking** — PDB / AlphaFold DB structures, pocket detection, AutoDock Vina binding-mode plausibility
- **Composite triage score** — Combine QED/ADMET (this skill) with selectivity and structural evidence
- **Lead optimization** — Use ADMET flags + PAINS/toxicophores to guide structural modifications
- **Toxicity follow-up** — Prioritize flagged compounds for experimental hERG/AMES assays

## Related Skills

- `de-results-to-gene-lists` — If starting from gene expression data
- `functional-enrichment-from-degs` — Pathway analysis of drug targets

## References

- **ADMET-AI:** Swanson et al. (2024) "ADMET-AI: A machine learning ADMET modeling platform" *Bioinformatics* 40(7):btae416
- **ChEMBL (percentile reference set):** Zdrazil et al. (2024) "The ChEMBL Database in 2023: a drug discovery platform spanning multiple bioactivity data types and time periods" *Nucleic Acids Res* 52(D1):D1180–D1192. doi:10.1093/nar/gkad1004. Data © EMBL-EBI, licensed CC BY-SA 3.0.
- **RDKit:** Open-source cheminformatics toolkit. https://www.rdkit.org/
- **Therapeutics Data Commons:** Huang et al. (2021) "Therapeutics Data Commons" *NeurIPS Datasets and Benchmarks*
- **Lipinski Rule of Five:** Lipinski et al. (2001) *Adv Drug Deliv Rev* 46:3-26
- **Veber Rules:** Veber et al. (2002) *J Med Chem* 45:2615-23
- **QED:** Bickerton et al. (2012) "Quantifying the chemical beauty of drugs" *Nat Chem* 4:90-98
- **PAINS:** Baell & Holloway (2010) "New substructure filters for removal of pan assay interference compounds (PAINS)" *J Med Chem* 53:2719-40
- **Toxicophores:** Brenk et al. (2008) "Lessons learnt from assembling screening libraries for drug discovery for neglected diseases" *ChemMedChem* 3:435-44
- **ADMET endpoint reference:** See `references/admet-reference.md`
