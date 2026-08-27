---
id: "skill_3a9f742f9ccb071b1720a6c41dfdbb38"
name: signature-reversal-lincs
description: "Use when an up/down gene signature, DE result, or GEO/CREEDS signature should be queried against LINCS L1000/Connectivity Map (CMap) to find small molecules that reverse or mimic it. Covers connectivity scoring, reproducibility, positive controls, and MOA/target/clinical-phase annotation; accepts user-defined signatures and includes non-approved compounds."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Given my up/down disease signature, find LINCS L1000 compounds that reverse it as drug-repurposing candidates."
---

# Signature-Reversal Connectivity Mapping (LINCS L1000) for Drug Repurposing

## Scope
This skill runs **connectivity mapping**: it compares a query up/down gene signature against the
LINCS L1000 compendium of chemical-perturbation transcriptomic signatures to nominate compounds
that **reverse** (therapeutic direction) or **mimic** the query state, then ranks, validates,
annotates, and reports them. It is **disease- and phenotype-agnostic** — it works for any
condition with a transcriptomic signature (autoimmune, neurodegenerative, oncology, infection,
aging, etc.).

**It does NOT:** run wet-lab validation, guarantee clinical efficacy, perform docking/structure-based
screening (use structure-prediction skills for that), or replace formal target-based drug discovery.
Outputs are **in-silico repurposing hypotheses** that require experimental follow-up.

## Inputs
Accepts **either** of two starting points (auto-detect; prefer user-provided):
1. **User-provided signature** — up- and down-regulated gene lists (HGNC symbols) as text, CSV, or
   a differential-expression table (the skill will threshold it into up/down sets). Typical size
   50–250 genes per direction.
2. **Disease/phenotype name only** — the skill auto-builds a **consensus signature** from local
   data-lake gene-set collections (see `references/signature_construction.md`).

Optional inputs: organism (default human; mouse supported), signature size, cell-context of
interest (e.g., specific tissue/cell lines), list of known drugs to use as positive controls.

## Outputs (saved to `/mnt/results/`)
- **`UC-style ranked reverser table`** (`*_reverser_ranking.csv`) — primary Tier-1 (reproducible)
  ranking with composite score, reproducibility, strength, specificity, MoA/target.
- Supplementary tables: Tier-2 single-signature hits, cell-context view, known-drug validation.
- `*_signature.json` / gene-scoring CSV — the query signature + provenance.
- `robustness_summary.json` — machine-readable robustness statistics.
- `figures/` — signature overview, top reversers, connectivity landscape, MoA enrichment,
  positive-control validation (PNG + editable SVG).
- **`report_<disease>_repurposing.pdf`** — branded report with an **infographic summary** plus
  Intro, Methods, Results, Conclusions, Figures, References, and Next Steps.

## Engine & data (READ THIS FIRST — two distinct assets)
- **Primary connectivity engine: SigCom LINCS API** (external, `https://maayanlab.cloud/sigcom-lincs`).
  This queries the full L1000 chemical-perturbation library (~693k signatures) and returns
  two-sided connectivity scores. Full endpoint/payload spec: `references/sigcom_lincs_api.md`.
- **LINCS1000** provides **Enrichr/CREEDS-style GMT gene-set collections**
  (`single_drug_perturbations-v1.0.gmt`, `single_gene_perturbations-v1.0.gmt`,
  `disease_signatures-v1.0.gmt`, `human_GEO.gmt`, `mouse_GEO.gmt`) — **NOT** the L1000 level-5
  z-score matrix. Use these for (a) auto-building the query consensus signature and (b) the
  **offline fallback** connectivity path when the SigCom API is unreachable.
- Use the SigCom API and LINCS resources for connectivity analysis.

## Workflow (7 stages)

### Stage 1 — Establish the query signature
- If the user supplied up/down gene lists or a DE table, use them (threshold DE tables at a stated
  cutoff, e.g. |log2FC|>1 & padj<0.05, or top-N by effect). Normalize to current HGNC symbols.
- Else auto-build the signature for the named disease from data-lake GMTs. Two methods (pick to
  match the source): **(1) Direct** — `disease_signatures-v1.0.gmt` is CREEDS-style with one up + one
  dn set per disease (~330 diseases); take that pair after technical filtering (fast default).
  **(2) Consensus** — `human_GEO.gmt` has many studies per disease; majority-vote by direction
  (recurrence ≥4 studies, consistency ≥0.6; lower the floor to ≥2 if a disease has few studies).
  **Verify the signature is non-empty and plausible** before querying — a too-high recurrence floor
  on a sparse disease silently returns nothing. See `references/signature_construction.md`.
- **Clean technical genes** (ribosomal `^RP[LS]\d`, translation factors, GAPDH/ACTB/B2M, etc.) but
  **RETAIN metallothioneins (MT1*/MT2A)** and other genuine biology. Record the removed set.
- Save the signature + provenance to JSON. Print top recurrent genes as a sanity check.

### Stage 2 — Resolve genes to L1000 space + QC
- Resolve symbols to L1000 gene entities via SigCom `POST metadata-api/entities/find`.
- Report **coverage %** per direction. Warn if coverage < ~85% (signature may be noisy or use
  non-canonical symbols). List unresolved genes.

### Stage 3 — Connectivity query (find reversers)
- **Primary:** SigCom `POST data-api/api/v1/enrich/ranktwosided` with the resolved up/down entity
  IDs and `database="l1000_cp"`. **Reversers = negative z-sum** (compound up-genes are down in the
  query and vice versa). Optionally repeat with `database="l1000_mean_cp"` (consensus library) as a
  corroboration check. Retrieve top ~2000 reversers.
- **Fallback (API down):** enrich the query up/down sets against local
  `single_drug_perturbations-v1.0.gmt` (compound up/dn perturbation sets); a compound reverses the
  query when the query-up overlaps the compound-down set and vice versa. Clearly label results as
  the fallback path.
- Retrieve mimickers too (positive z-sum) if the user wants signature-mimicking compounds.

### Stage 4 — Aggregate signatures to compounds
- Resolve returned signature IDs to compound names + metadata via `POST metadata-api/signatures/find`
  (batch ≤100). Map screening IDs `BRD-xxxx` → `pert_iname` using the **Broad Drug Repurposing Hub**
  (`broad_repurposing_hub_*` parquet in the data lake).
- Aggregate per-well signatures (compound × cell line × dose × time) to **per-compound** statistics:
  n reversing signatures, n cell lines, median/best z-sum, best FDR, n mimicking signatures.

### Stage 5 — Rank + robustness
- **Tier the compounds:** Tier-1 = reproducible (≥2 independent reversing signatures); Tier-2 =
  single-signature. Rank Tier-1 by a **composite score** combining reproducibility, reversal
  strength, and significance, multiplied by a **reverser-specificity** factor that penalizes
  promiscuous compounds (strong as both reverser and mimicker). Formula: `references/ranking_and_robustness.md`.
- **MANDATORY checks:** (a) reproducibility tiering; (b) **positive-control recovery** — do known
  drugs for this disease appear as reversers? Classify each known drug as Tier-1 / Tier-2 / absent
  **by actual table membership** (never infer "absent" without checking both tiers); (c)
  promiscuity/specificity control.
- **DEFAULT-ON (document, allow disabling):** sensitivity of the ranking to signature-construction
  choices; cell-line-context (tissue-relevant) view. The sensitivity check can be satisfied by
  either (a) re-querying with an alternative signature (e.g. pre-housekeeping-filter or a different
  size) and computing Spearman correlation between the two rankings, or (b) reporting cell-context
  stability (how many Tier-1 compounds reverse in disease-relevant cell lines) as a proxy when
  re-query is not feasible. State which alternative was used.
- **OPTIONAL:** consensus-library corroboration; pathway/target enrichment (MSigDB-Hallmark and
  Reactome only — **KEGG is excluded for commercial-use safety**, and MSigDB use is restricted to
  the Hallmark collection); ADMET (`predict_admet_properties`) on top hits' SMILES.
- Save all numbers to `robustness_summary.json`.

### Stage 6 — Annotate hits (mechanism + evidence)
- Add MoA / target / clinical phase from the **Broad Drug Repurposing Hub**, **ChEMBL**, and
  **TxGNN** (drug-repurposing predictions & name mapping).
- Run **`LiteratureSearch`** for the top candidates in the disease context ("<compound> <disease>")
  to attach published evidence and generate report references. Verify every cited claim against the
  returned records — never fabricate PMIDs/DOIs.
- **CRITICAL — references must come from live tool output, not hardcoded lists.** The references
  list MUST be built by parsing `LiteratureSearch` tool output records (saved to
  `references.jsonl`), not by hardcoding reference strings in source code. The `LiteratureSearch`
  tool call must appear in the execution trace notebook. Build the report references from that file.
  Hardcoding references — even with real DOIs — bypasses the grounding workflow and makes citation
  provenance unverifiable.
- Compute MoA-class enrichment among the top reversers.

### Stage 7 — Figures + branded PDF report
- Generate data figures (seaborn/matplotlib; Okabe-Ito palette; editable SVG + PNG). Run the
  `Read` media-output check on every figure and regenerate if blank/clipped/overlapping.
  **Text-only model fallback:** if the visual media-output check is unavailable (text-only model),
  verify figures are non-blank programmatically: file size > 10 KB, pixel dimensions > 200×200,
  and non-white pixel ratio > 0.05 (compute with PIL/numpy). Record which check was used.
  Recommended figures: (1) signature overview + coverage, (2) top-N reversers bar chart,
  (3) connectivity landscape (reverser/mimicker distribution + reproducibility scatter),
  (4) MoA-class enrichment, (5) positive-control validation (where known drugs rank).
- Compile the report using the **`pdf-report-generation`** skill (load it with `Skill`). The report
  MUST contain an **infographic summary panel** (headline result, # candidates, top hits, key
  validation) plus **Intro, Methods, Results, Conclusions, Figures, References (from
  LiteratureSearch), and Next Steps**. Validate the PDF (page count, extractable text) and run the
  media-output check on the pages.

## Mandatory vs optional robustness (quick reference)
| Check | Status | Why it matters |
|---|---|---|
| Reproducibility tiering (≥2 sigs) | **Mandatory** | One-off signatures dominate raw scores; tiering controls false positives |
| Positive-control recovery of known drugs | **Mandatory** | The single best evidence the map is biologically meaningful |
| Promiscuity / reverser-specificity | **Mandatory** | Prevents pan-assay-active cytotoxins from topping the list |
| Sensitivity to signature choices | Default-on | Confirms hits aren't artifacts of gene filtering |
| Cell-context (tissue-relevant) view | Default-on | Adds tissue relevance beyond cancer-cell average |
| Consensus-library corroboration | Optional | Cross-checks against collapsed L1000 library |
| Pathway/target enrichment (MSigDB-Hallmark + Reactome only; no KEGG), ADMET | Optional | Mechanistic explanation, early developability |

## Database reference table
| Resource | Access | Use | IDs |
|---|---|---|---|
| SigCom LINCS API | External HTTPS | Connectivity scoring (l1000_cp / l1000_mean_cp) | gene symbols → entity UUIDs; signature UUIDs |
| LINCS1000 GMTs | Biomni resource | Consensus signature build; offline fallback | gene symbols |
| Broad Drug Repurposing Hub | Biomni resource | BRD→name, MoA, target, phase, SMILES | BRD IDs, pert_iname |
| ChEMBL | Queryable DB | Target/MoA annotation | ChEMBL IDs |
| TxGNN | Biomni resource | Repurposing predictions & name mapping | drug names |
| `LiteratureSearch` (Biomni) | Tool | Published evidence + report references | PMIDs/DOIs |
| `predict_admet_properties` (Biomni) | Tool | Optional ADMET from SMILES | SMILES |
| MSigDB **Hallmark** (H) | Gene-set file | Optional GSEA/ORA on the signature (**Hallmark collection only**) | gene symbols |
| Reactome | Queryable / gene-set | Optional pathway ORA on signature/hit targets | gene symbols |
| ~~KEGG~~ (excluded) | — | **NOT used** — non-commercial license; removed for commercial-use safety | — |

## Scientific caveats (state these in every report)
- **In-silico hypotheses**, not therapeutic recommendations; require experimental validation.
- **Cell-line context:** L1000 is dominated by cancer cell lines; compounds acting via gut-luminal,
  microbiome-, or host-metabolism-dependent mechanisms (e.g., topical agents, prodrugs) are
  systematically under-represented — expected false negatives, not true inactivity. Classify known
  drugs by actual table membership before claiming a drug is/ isn't recovered.
- **Scoring metric:** SigCom two-sided z-sum is related to but not identical to the Broad CMap tau;
  treat ranks as ordinal, not absolute.
- **Signature provenance:** an auto-built consensus inherits its source studies' platforms and case
  definitions; always state the source and study count.
- **Gene-symbol hygiene:** use current HGNC symbols; report unresolved genes and coverage.
- **Toxicity:** flag cytotoxic top hits (e.g., proteasome inhibitors) as mechanistic pointers, not
  direct systemic candidates.

## Commercial-use caveats (needs_commercial_review)
- **Broad Drug Repurposing Hub:** compound metadata is released under CC-BY 4.0 (permits commercial
  use with attribution), but the Broad's terms add a "research purposes only" clause that restricts
  use for clinical treatment or commercial marketing — it does **not** restrict research-stage drug
  repurposing R&D. Flag as `needs_commercial_review` for any production or clinical application
  beyond research R&D.
- **SigCom LINCS API / LINCS L1000 data:** NIH-funded and released under the NIH LINCS Data Release
  Policy (unrestricted re-use with citation). The historic lincscloud.org academic-use restriction
  is deprecated. No explicit commercial-use prohibition found in current terms, but no explicit
  commercial clearance either — treat as `needs_commercial_review` for commercial deployment.
- **ChEMBL:** CC BY-SA 3.0 (permits commercial use with attribution + share-alike).
- **Optional pathway-enrichment sources (KEGG excluded; MSigDB Hallmark-only):** **KEGG has been
  removed from this skill.** KEGG's data are free for academic use but require a paid commercial
  license for commercial/for-profit use and redistribution, so the skill never invokes it. For the
  optional pathway/target-enrichment step use **MSigDB — Hallmark collection only** (the Hallmark
  gene sets are released under a permissive CC-attribution license; other MSigDB collections may
  carry source-specific restrictions and are out of scope here) and/or **Reactome** (CC0/CC-BY,
  commercial use permitted with attribution). See `DATA_SOURCES.md` for the full source/license
  table.
- Do not assume any dataset, API, or tool is commercially cleared for all use cases without
  verifying the current license terms. These caveats preserve existing restrictions; they do not
  grant commercial rights.

## Error handling
- **SigCom API unreachable / non-200 / JSON error:** switch to the local-GMT fallback (Stage 3) and
  label results accordingly; do not silently fail.
- **Transient null / empty `data.gene` responses (gnomAD or similar gene-metadata lookups):** retry
  null `data.gene` results with bounded backoff (e.g. up to 3 retries with 2/4/8-second waits)
  before returning missing data. A single null response is often transient; do not treat it as a
  permanent "gene not found" without retrying.
- **Low gene coverage (<85%):** warn, list unresolved genes, suggest symbol updating; proceed but
  caveat.
- **Compound names unresolved (BRD only):** keep the BRD ID, note it; most unnamed hits are
  single-signature and filtered out of Tier-1 anyway.
- **No known drugs to validate against:** ask the user or pull disease-standard drugs via
  `LiteratureSearch`; never skip positive-control recovery silently.
- **ReportLab / CSV NaN crashes:** nullable DataFrame values (NaN, None, inf) must be coerced to
  safe strings before being passed to `Paragraph()` or written to table cells — use the `_safe_str()`
  helper in `scripts/build_report.py`. Never pass raw `float('nan')` to ReportLab.
- **Never hardcode results:** all numbers/citations in the report must come from live tool output at
  run time.

## Reference files
- `references/sigcom_lincs_api.md` — exact SigCom endpoints, payloads, response schema, reverser
  logic, BRD→name mapping.
- `references/signature_construction.md` — consensus-signature build, thresholds, technical-gene filter.
- `references/ranking_and_robustness.md` — composite scoring, specificity, robustness suite.
- `scripts/run_connectivity.py` — parameterized reference implementation (signature → ranked reversers).
- `scripts/build_report.py` — report scaffold (infographic + all sections) using pdf-report-generation.
