# Worked example: Idiopathic Pulmonary Fibrosis (IPF)

This is the reference end-to-end run used to validate the engine. A future agent can use
these exact numbers as a **reproducibility smoke test**: running the packaged pipeline on
the built-in IPF disease signature with the 271-drug LINCS library must reproduce them (to
4 decimals for scores; ±1 for the FDR-significant and approved counts due to permutation
discretization and time-variant deduplication — see "Reproduction tolerances" below).

## Inputs
- Disease signature: built-in `idiopathic pulmonary fibrosis` (matched at score 1.00 by
  `resolve_inputs.match_disease`) — **349 up, 245 down** genes.
- Library: `single_drug_perturbations-v1.0.gmt` — **271 drug** signatures.
- Ortholog map: MGI (20,181 mouse symbols).

## Harmonization
- 271 perturbations retained; **160 human, 111 mouse** by organism detection.
- Mouse->human map rate mean 0.863 / median 0.865 per mouse signature.
- Common background **N = 15,229** genes.

## Scoring (exact, deterministic)
| drug | S_reversal | note |
|---|---|---|
| Fluticasone | **30.7059** | top glucocorticoid reverser (z_rev component 47.723) |
| Mesalazine | 29.5010 | COX/LOX inhibitor |
| Hydrocortisone | 28.1999 | glucocorticoid |
| Deferasirox | 18.7271 | iron chelator (mechanistically credible anti-fibrotic) |
| Carbidopa | 17.2968 | top by *consensus* rank |
| Carboplatin | 20.0413 | antiproliferative |
| **Bleomycin** | **−14.3195** | **negative control: canonical fibrosis inducer, correctly a top disease-mimic** |
| Estradiol | −24.1268 | pro-fibrotic, correctly negative |
| Nickel | −28.9677 | strongest mimic |

- FDR<0.05 reversers (all drugs): **81–82** (permutation floor gives ±1).
- Enrichment cross-check Spearman **ρ = 0.870** (connectivity vs KS enrichment).
- Fluticasone enrichment detail: es_up = −0.2112, es_dn = 0.1673, reversal_enrich = 0.3786.

## Annotation (Broad Hub)
- Matched to Hub (any phase): **~140**; approved (Launched): **~107**.
- Approved reversers (S>0): **~57**; approved & significant (FDR<0.05): **~33**.
- Salt-aware match works (Fluticasone -> `fluticasone-propionate`; mepenzolate bromide ->
  `mepenzolate`). False-positive guard works (morphine -> none, NOT apomorphine).

## Controls (the key validity check)
`check_controls(expected_reversers=[fluticasone, hydrocortisone, captopril, deferasirox],
expected_mimics=[bleomycin, estradiol])` -> every present control matched its expected
direction with significance; **bleomycin correctly flagged as a disease-mimic** (S=−14.32).
The output now carries `significant` (bool), `fdr_mimic`, and a three-valued
`matches_expectation` ('yes' requires significance, not just sign). The IPF controls
verdict is **'pass'** (all present controls are 'yes'), so
`controls_failure_acknowledgement` is NOT required in this worked example.

## Honest caveats surfaced in the IPF report
- Top-ranked **corticosteroids are ineffective/harmful in IPF** (a fibrotic, not
  inflammatory, disease) — high score ≠ efficacy.
- **Imatinib** is mechanistically attractive (shares PDGFR inhibition with nintedanib) but
  **failed a randomized IPF trial**.
- The two approved IPF drugs (pirfenidone, nintedanib) have **no LINCS perturbation
  signature** and could not be scored — a data limitation, not a method failure.
- Strongest convergent-evidence candidate: **deferasirox** (iron chelation aligns with
  ferroptosis/iron-accumulation evidence in IPF).

## `compound_flags` for the IPF report (single source of truth)
The honest caveats above are encoded as structured `report_config['compound_flags']` so the
same classification drives the body flag table AND the page-1 caption (a flagged compound can
never appear unflagged on page 1):

```python
report_config["compound_flags"] = [
    {"name": "Fluticasone",    "classification": "caution",  "note": "top glucocorticoid reverser, but corticosteroids are ineffective/harmful in fibrotic (non-inflammatory) IPF"},
    {"name": "Hydrocortisone", "classification": "caution",  "note": "glucocorticoid; high score reflects steroid signature, not anti-fibrotic efficacy"},
    {"name": "Imatinib",       "classification": "caution",  "note": "mechanistically attractive (PDGFR) but failed a randomized IPF trial"},
    {"name": "Deferasirox",    "classification": "credible", "note": "iron chelation aligns with ferroptosis/iron-accumulation evidence in IPF"},
]
```

Because the IPF controls verdict is **'pass'**, the page-1 headline reads "Control validation:
passed" and the derived caption names the top compounds with the `caution` ones annotated
(e.g. "Fluticasone [caution: …]"); no failure banner is shown. If instead a run's verdict were
'fail', `build()` would render the "Method validation did not pass" banner first and the
front-matter consistency gate would require `executive_summary`/`key_finding` to state the
failure before any candidate is named.

## Reproduction tolerances
- **Scores: identical to 4 decimals** (deterministic; seed=42, nperm=10,000).
- **FDR-significant count: ±1** (permutation p-value floor 1/(nperm+1)).
- **Approved count: ±1** — the current `clean_drug` deduplicates L1000 time-course variants
  (e.g. `carboplatin (24 h/30 h/36 h)` -> one `carboplatin`) and the salt-aware matcher
  recovers `mepenzolate bromide` -> mepenzolate. Both are *improvements* over the original
  hard-coded run; they do not change top candidates or controls.
