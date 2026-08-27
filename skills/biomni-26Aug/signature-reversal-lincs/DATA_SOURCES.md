# Data sources & commercial-use status — `signature-reversal-lincs`

This file records the external data sources, tools, and gene-set collections the skill uses (or
deliberately excludes), together with their licensing / commercial-use status. It exists so the
skill never silently invokes a source with a non-commercial license.

**Guiding rule:** flags below *preserve existing restrictions*; they do **not** grant commercial
rights. Do not assume any dataset, API, or tool is commercially cleared without verifying its
current license terms.

## Summary of the commercial-use cleanup (this revision)
- **KEGG removed.** KEGG previously appeared only in the OPTIONAL pathway/target-enrichment step
  (`MSigDB / Reactome / KEGG`). KEGG data are free for academic use but require a **paid commercial
  license** for commercial / for-profit use and redistribution. To keep the skill safe for any
  research-R&D-and-beyond use, KEGG has been **removed** from the enrichment options in `SKILL.md`
  and `references/ranking_and_robustness.md`. The skill never calls KEGG.
- **MSigDB restricted to Hallmark.** The optional gene-set enrichment step now specifies the
  **MSigDB Hallmark collection only**. The Hallmark (H) gene sets are distributed under a permissive
  Creative-Commons attribution license. Other MSigDB collections (e.g. C2:CP, which itself *contains*
  KEGG-derived sets) may carry source-specific restrictions and are **out of scope** here.
- No code changed: pathway enrichment is an *optional, documented* step with no bundled
  implementation script, so this is a documentation-only cleanup. The skill's executed workflow uses
  Broad-hub **MoA-class enrichment** (Fisher exact), not pathway ORA.

## Source / license table

| Source | Role in skill | Access | License / commercial status | Used? |
|---|---|---|---|---|
| **SigCom LINCS API / LINCS L1000** | Primary connectivity engine (l1000_cp / l1000_mean_cp) | External HTTPS | NIH LINCS Data Release Policy — unrestricted re-use **with citation**; historic lincscloud.org academic-only clause is deprecated. No explicit commercial prohibition, but no explicit clearance → `needs_commercial_review` for commercial deployment. | **Yes** |
| **LINCS1000 GMTs** (`/mnt/datalake/LINCS1000/`) | Consensus signature build; offline fallback | Local datalake | Enrichr/GEO2Enrichr-derived gene sets from public GEO; treat per underlying GEO/Enrichr terms → `needs_commercial_review`. | **Yes** |
| **Broad Drug Repurposing Hub** (`/mnt/datalake/broad_drug_repurposing_hub/`) | BRD→name, MoA, target, phase, SMILES | Local parquet | Metadata CC-BY 4.0 (commercial use with attribution), but Broad terms add a "research purposes only" clause restricting clinical treatment / commercial marketing (does **not** restrict research-stage repurposing R&D) → `needs_commercial_review` beyond research R&D. | **Yes** |
| **ChEMBL** | Target / MoA annotation | Queryable DB | CC BY-SA 3.0 — commercial use permitted with attribution + share-alike. | Optional |
| **TxGNN** (`/mnt/datalake/`) | Repurposing predictions & name mapping | Local datalake | Per its published terms → `needs_commercial_review`. | Optional |
| **`LiteratureSearch`** (Biomni) | Published evidence + report references | Tool | Returns bibliographic records; cite sources normally. | **Yes** |
| **`predict_admet_properties`** (Biomni) | Optional ADMET from SMILES | Tool | Model/tool-dependent. | Optional |
| **MSigDB — Hallmark (H) collection** | Optional GSEA/ORA on the signature | Gene-set file | Hallmark sets under a permissive CC-attribution license (commercial use with attribution). **Hallmark only** — other collections out of scope. | Optional (Hallmark only) |
| **Reactome** | Optional pathway ORA on signature / hit targets | Queryable / gene-set | CC0 / CC-BY — commercial use permitted with attribution. | Optional |
| ~~**KEGG**~~ | ~~pathway enrichment~~ | — | **EXCLUDED** — free for academic use only; paid commercial license required for commercial use / redistribution. **The skill never uses KEGG.** | **No (excluded)** |

## Notes
- MSigDB C2:CP includes KEGG-derived gene sets; because the skill restricts MSigDB to the **Hallmark
  (H)** collection, no KEGG-derived content enters the analysis through MSigDB either.
- If a future analysis genuinely needs KEGG pathways for a commercial application, obtain a KEGG
  commercial license first; otherwise substitute Reactome / MSigDB-Hallmark, which are already the
  skill's defaults.
