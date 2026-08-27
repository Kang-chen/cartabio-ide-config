# Resource reference

Contents and schemas of the Biomni resources this skill depends on; storage
locations are resolved by the runtime.

---

## LINCS L1000 gene-set signatures

| file | contents | used for |
|---|---|---|
| `disease_signatures-v1.0.gmt` | **~333 disease** up/dn signatures (`<disease>-up` / `<disease>-dn`) | built-in disease input (`resolve_inputs`) |
| `single_drug_perturbations-v1.0.gmt` | **271 drug** up/dn perturbation signatures | default screening library |
| `single_gene_perturbations-v1.0.gmt` | single-gene KD/OE up/dn signatures | optional target nomination |
| `human_GEO.gmt`, `mouse_GEO.gmt` | GEO-derived up/dn signatures | alternative signature source |
| `SARS-CoV-2_RNAseq_Datasets_11-09-20.gmt` | COVID-19 signatures | niche disease input |
| `GTEx_age_signatures.gmt` | age-associated signatures | niche |

The ~333 disease names span a broad range (multiple cancers, Alzheimer's disease,
Parkinson's disease, rheumatoid arthritis, ulcerative colitis, diabetic nephropathy,
idiopathic pulmonary fibrosis, and many more) — this is what makes the skill genuinely
disease-agnostic. Enumerate them with `resolve_inputs.list_disease_signatures()`.

### Continuous z-score matrices (NOT used by default)
- `human_geo_sigs.tsv` (~1.6 GB z-scores), `mouse_geo_sigs.tsv` (~1.49 GB).
These are the continuous form; the default engine uses the **gene-set** form above. Only
touch these if a future extension implements a z-score (cosine/XSum) score — they are large,
so budget memory accordingly.

---

## Broad Institute Drug Repurposing Hub

| file | rows | key columns |
|---|---|---|
| `broad_repurposing_hub_phase_moa_target_info.parquet` | ~6,798 | `pert_iname, clinical_phase, moa, target, disease_area, indication` |
| `broad_repurposing_hub_molecule_with_smiles.parquet` | ~20,283 | `pert_iname, InChIKey, smiles, pubchem_cid` |

`clinical_phase` value counts (approx): Launched 2427, Preclinical 2310, Phase 2 813,
Phase 1 566, Phase 3 458, Withdrawn 95, Phase 1/Phase 2 85, Phase 2/Phase 3 44.
**"Approved" = clinical_phase == "Launched".**

`PHASE_RANK` used for tie-breaking: Launched 6, Phase 3 5, Phase 2/Phase 3 4, Phase 2 3,
Phase 1/Phase 2 2, Phase 1 1, Preclinical 0, Withdrawn −1 (missing −2).

---

## MGI mouse–human orthology
- Runtime URL (verified HTTP 200):
  `https://www.informatics.jax.org/downloads/reports/HOM_MouseHumanSequence.rpt`
- Bundled offline fallback: `assets/HOM_MouseHumanSequence.rpt` (~15 MB, 46,523 rows).
- Map is grouped by `DB Class Key`; organism via `Common Organism Name` containing
  `human` / `mouse`; keyed by UPPER mouse `Symbol` -> set of UPPER human `Symbol`
  (~20,181 mouse symbols).

---

## Biomni tools & packages used
- **LiteratureSearch** (Biomni tool) — literature grounding for top candidates; writes
  structured records to `/mnt/results/execution_trace/references.jsonl`. Must be called by
  the agent, not shelled out.
- **Direct checks** — confirm packages with imports.
- **GenerateImage** (deferred tool; load via ToolSearch) — the opening infographic
  (conceptual figure).
- **pdf-report-generation skill** — canonical report brand-style definition (palette +
  typography); `assets/report_style.py` loads it at runtime.
- Python packages: `pandas`, `numpy`, `scipy` (fisher_exact, KS), `statsmodels`
  (multipletests), `matplotlib` (data figures), `reportlab` + `Pillow` (PDF), `pypdf`
  (report QC), `requests` (ClinicalTrials.gov), `rdkit` (optional ADMET mode).

---

## Optional resources
- RummaGEO and TxGNN can extend the workflow when relevant.
