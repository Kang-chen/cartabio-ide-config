# Functional annotation of fine-mapped variants

Once fine-mapping has resolved a credible set, annotation asks *what the variant might do*. This is
**interpretive context, not proof of causality**, and every layer here is **optional and fails soft** —
a flaky API or an absent record never invalidates the fine-mapping result. `annotate_variants.py` runs
three independent layers; enable only the ones you need.

## Guiding principle: report negatives honestly

The single most important rule: **"not an eQTL here" and "underpowered / inconclusive" are real,
reportable outcomes — never dropped, never spun.** A variant that is genome-wide significant for a trait
but is *not* a detectable eQTL for the nearby gene in the tissue you checked is a genuine, informative
result (it may act through a different gene, a different tissue/cell type, an antisense transcript, or a
non-eQTL mechanism). The scripts and report template surface this explicitly.

**Worked anchor (PROX1):** rs340874 is genome-wide significant for T2D and sits in an active enhancer,
but **PROX1 itself is not a significant eGene** in bulk pancreas or the small islet cohorts. The
regulatory signal runs through the **antisense lncRNA PROX1-AS1** (GTEx pancreas: C allele → NES = +0.48,
P = 9.5e-11). The annotation output shows PROX1 as a negative and PROX1-AS1 as the positive — the honest
picture, not a forced "variant regulates PROX1" story.

## Layer 1 — GTEx v8 cis-eQTL

- **Endpoint:** `https://gtexportal.org/api/v2/association/singleTissueEqtl` filtered by
  `gencodeId` + `variantId` + `tissueSiteDetailId` + `datasetId=gtex_v8`.
- **Variant id format:** `chr{c}_{pos}_{ref}_{alt}_b38` (GRCh38 only — GTEx v8 is b38; the layer is
  skipped if `--genome-build != 38`).
- **Gene id:** GTEx needs the **versioned** gencodeId (`ENSG........N`); the script resolves an
  unversioned ENSG via `/reference/gene?geneId=...`.
- **Filtered query returns significant eQTLs only** — so an empty result means "not a significant eGene
  in this tissue," which the script reports as a true negative.
- **Direction of effect:** GTEx `nes` is per **ALT** allele. The script re-orients it to the
  **credible-set effect allele** and reports `direction_on_gene_per_effect_allele` = up/down. Always
  interpret direction relative to the *risk/effect* allele, not ALT.

## Layer 2 — eQTL Catalogue (tissues/cell types GTEx lacks)

- Uses the in-skill `fetch_qtl.py` (`scripts/fetch_qtl.py`, `--source eqtl_catalogue --dataset-id QTD...`).
  Enable with `--fetch-qtl --qtl-datasets ... --qtl-genes ...`; the fetch script defaults to the
  in-skill `fetch_qtl.py`, so `--fetch-qtl-script` is only needed to point at a different copy.
- **Why:** disease-relevant contexts absent from GTEx — e.g. **pancreatic islets** for T2D:
  - `QTD000554` — van de Bunt et al. islet eQTL (n ≈ 117)
  - `QTD000574` — PISA islet eQTL (n ≈ 127)
  - `QTD000296` — GTEx v8 pancreas (ge) as an eQTL Catalogue-harmonised comparator (n ≈ 305)
- **Small-cohort caveat:** islet datasets are small and **underpowered**; a null result is
  **inconclusive**, not evidence of "no effect." The script and report say so. Anchor: PROX1 in the
  islet cohorts was underpowered (coloc PP.H4 ≈ 0.04–0.11), consistent with — not contradicting — the
  PROX1-AS1 mechanism.
- Matches QTL rows to credible-set variants by rsID (preferred) or position; carries beta, p-value, and
  the QTL effect allele so direction can be checked.

## Layer 3 — ENCODE SCREEN candidate cis-regulatory elements (cCREs)

- **Endpoint:** `POST https://screen-beta-api.wenglab.org/dataws/cre_table` with body
  `{"assembly":"GRCh38","coord_chrom":"chr{c}","coord_start":..,"coord_end":..,"gene_all_start":0,
  "gene_all_end":5000000,"element_type":"","limit":100}`.
- Reports element class (promoter-like PLS, proximal enhancer-like pELS, distal dELS, CTCF-only, DNase-
  H3K4me3) and z-scores (DNase, H3K4me3, H3K27ac, CTCF), and whether each cCRE **overlaps** a
  credible-set variant.
- **Beta API is frequently unstable.** The layer **degrades gracefully** — if SCREEN doesn't respond it
  logs a note and writes nothing, so the run still succeeds; rerun later or check the UCSC ENCODE cCRE
  track manually. Do not treat an API outage as "no regulatory element."
- Anchor: rs340874 overlaps cCRE **EH38E2865330** (pELS, 209 bp; DNase z = 2.64, H3K27ac z = 3.83,
  H3K4me3 z = 4.21) — a strong active-enhancer signature consistent with a regulatory mechanism.

## Putting annotation together with direction of effect

A credible mechanistic narrative aligns the pieces **and states where they don't align**:
1. Which allele is the risk/effect allele (from the credible set).
2. Whether it's an eQTL and, if so, the **direction on the gene for that allele** (up/down).
3. Whether it sits in a regulatory element (cCRE class + marks).
4. Whether **orthogonal functional evidence** (reporter assays, EMSA, perturbation) agrees.

**Direction-of-effect caution (learned from PROX1):** eQTL direction can appear to conflict with prior
functional models, and eQTL direction in one tissue need not generalize to the causal cell type. For
PROX1, cancer-cell literature reports PROX1-AS1 *positively* regulating PROX1, whereas a β-cell model
(Lecompte et al. 2013, *Diabetes*, doi:10.2337/db12-0864 — the insulin-lowering/risk allele lowered
reporter activity and PROX1 knockdown reduced glucose-stimulated insulin secretion) points the other
way. The right move is to **present the tension explicitly** and let colocalization + cell-type-specific
data adjudicate — not to force a clean story. The report template always includes this caveat.

## Handing off to colocalization

Annotation flags *candidate* regulatory genes/elements. To formally test whether the **GWAS signal and
an eQTL signal share the same causal variant**, run a colocalization method (`coloc` / `coloc.susie`).
The variants, region, LD, and eQTL datasets identified here feed directly into it. The PROX1
anchor's decisive evidence was exactly this: T2D × PROX1-AS1 colocalization in GTEx pancreas gave
**PP.H4 = 0.92** with rs340874 as the top shared variant.

## Endpoints (summary)

- GTEx v8 API: `https://gtexportal.org/api/v2` (`/association/singleTissueEqtl`, `/reference/gene`)
- eQTL Catalogue: via `fetch_qtl.py` (`--source eqtl_catalogue`), dataset ids `QTD......`
- ENCODE SCREEN: `https://screen-beta-api.wenglab.org/dataws/cre_table` (beta; unstable — graceful skip)
- Gene ids used in the anchor: PROX1 = ENSG00000117707, PROX1-AS1 = ENSG00000230461
