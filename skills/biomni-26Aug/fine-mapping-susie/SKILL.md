---
id: "skill_f55142b5be96e79ec64484a53f8087a4"
name: "fine-mapping-susie"
description: "Use to fine-map one GWAS locus with SuSiE, identify likely causal variants, and compute PIPs and 95% credible sets. Requires ancestry-matched LD, runs LD-mismatch diagnostics, accepts GWAS summary statistics or accessions, and annotates variants with GTEx/eQTL Catalogue eQTLs and ENCODE cCREs."
category: "genomics_genetics"
visibility: "public"
starting-prompt: "Fine-map the T2D GWAS locus at PROX1 using SuSiE and annotate the credible set."
---

# Ancestry-Aware GWAS Fine-Mapping (SuSiE) + Annotation

## When to Use This Skill

- You have **one GWAS locus** and want to know **which variant(s) are causal**: the 95% credible set +
  per-variant PIP (posterior inclusion probability).
- You want to **prioritize variants** at a lead signal, or reduce a locus to a short list of candidates.
- You then want to **annotate** the credible-set variants: cis-eQTLs (which gene/tissue) and regulatory
  elements (enhancer/promoter) to nominate a mechanism.
- Your GWAS is a **user upload** or an **open GWAS Catalog study** (harmonised summary statistics).

**This is the single-signal, upstream question.** For the two-dataset question — *do these two signals
share a causal variant?* (GWAS × eQTL/pQTL, PP.H4, `coloc.susie`) — fine-map each signal here first, then
run a colocalization method (`coloc` / `coloc.susie`) on the fine-mapped signals. This skill deliberately
stops at fine-mapping + annotation and produces the per-signal inputs (credible sets, aligned LD) that a
colocalization step consumes.

**Not suitable for:** genome-wide scans / automatic locus discovery (fine-map one region per run — supply
a window or lead variant); trans-ancestry fine-mapping in a single run (fine-map each ancestry with its
own LD, or use in-sample LD); any analysis with **no LD reference and no in-sample LD** (SuSiE on summary
statistics requires LD — do not substitute r² or an unsigned matrix).

## What's in this skill (self-contained)

Everything needed to go from a GWAS region to an annotated credible set ships in `scripts/`. There are
no external-skill dependencies.

```
scripts/
    fetch_gwas_catalog.py      # GWAS Catalog harmonised fetch + gated-source fallback (refuses to fabricate)
    ingest_sumstats.py         # summary-stat ingestion + auto column mapping (prefers hm_* harmonised cols)
    detect_build_liftover.py   # genome-build detection (anchor SNPs) + hg19->GRCh38 liftover (pyliftover)
    build_ld.sh                # subset NYGC 30x 1000G panel -> PLINK2 pgen (ancestry-matched, signed r)
    ld_utils.py                # signed-r LD matrix oriented to the effect allele (NOT r²)
    run_susie_finemap.R        # single-dataset SuSiE fine-mapping + LD-mismatch diagnostics
    fetch_qtl.py               # eQTL Catalogue / GTEx / eQTLGen QTL fetch (for annotation)
    annotate_variants.py       # GTEx / eQTL Catalogue / ENCODE SCREEN annotation (fails soft per layer)
    make_finemap_figures.py    # regional multi-panel + PIP plots
    generate_finemap_report.py # Phylo-branded PDF report (data-driven from the JSON/CSV outputs)
```

## Installation

```r
# R (>= 4.4)
install.packages(c("susieR", "data.table", "jsonlite", "ggplot2", "Rfast"))
```
```bash
# Python (>= 3.9)
uv pip install pandas numpy scipy requests reportlab pillow matplotlib pyliftover
# CLI: plink2 ; tabix + bgzip (htslib)
```

| Software | Version | License | Commercial Use |
|----------|---------|---------|----------------|
| susieR | ≥0.12 (tested 0.14.2) | BSD-3 | ✅ Permitted |
| data.table | ≥1.14 | MPL-2 | ✅ Permitted |
| Rfast | ≥2.1 | GPL-2+ | ✅ Permitted |
| ggplot2 / matplotlib | ≥3.4 / ≥3.7 | MIT / PSF-BSD | ✅ Permitted |
| PLINK2 | ≥2.0 | GPL-3 | ✅ Permitted |
| htslib (tabix/bgzip) | ≥1.10 | MIT | ✅ Permitted |
| pyliftover | ≥0.4 | GPL-3 | ✅ Permitted |
| reportlab | ≥4.0 | BSD | ✅ Permitted |

## Inputs

| Input | Specification |
|---|---|
| Region | Window `chr:start-end`, or a lead variant (rsID / `chr:pos`) + flank (default ±500 kb). One region per run. |
| GWAS | (a) uploaded CSV/TSV/.gz; or (b) a GWAS Catalog accession `GCST######` (open harmonised sumstats). Per-variant: variant ID, chr, pos, effect/other allele, beta **or** OR, SE, p, EAF/MAF, N (+ N_cases/N_controls for case-control). |
| **Ancestry** | **REQUIRED.** GWAS ancestry → 1000G superpopulation (EUR/AFR/EAS/SAS/AMR). Inferred from GWAS Catalog metadata when available, else asked. No silent default. In-sample LD accepted instead. |
| Trait type | `quant` or `cc`. For `cc`, sample size N (and case/control counts if available) is used to form z from beta/se. |
| Annotation targets | Tissue(s) for GTEx / eQTL Catalogue, and target gene(s) (Ensembl ENSG id). Optional per layer. |

## Outputs

Saved under `finemap_results/` (surfaced to the results panel):

- `credible_set.csv` — one row per credible-set variant: `cs, cs_size, within_cs_min_abs_corr, snp,
  varid, pos, effect_allele, other_allele, beta, se, pval, eaf, z, pip`.
- `all_variants_pip.csv` — every analyzed variant with z + PIP, sorted by PIP.
- `susie_report.json` — run summary: `n_snps_analyzed, n_dropped_na_z, N, L, coverage, susie_converged,
  **estimated_s**, kriging_outliers, ld_asymmetry_fixed, n_credible_sets, top_pip, warnings`. (This is the
  provenance/QC record — combine with `gwas_fetch.json`, `build.json`, `ld.json` for full lineage.)
- `susie_fit.rds` — the SuSiE object for reload / custom plots.
- `annot_gtex_eqtl.csv` — GTEx v8 eGene / tissue / NES / p + **direction of effect** (effect allele →
  gene up/down). `annot_eqtl_catalogue.csv` — eQTL Catalogue QTL rows (incl. islet/immune datasets).
  `annot_encode_ccre.csv` — ENCODE SCREEN cCREs (class + DNase/H3K27ac/H3K4me3/CTCF z; overlap flag).
  `annot_summary.json` — which layers ran + record counts. (Only for enabled layers.)
- `finemap_regional.png/.svg` — multi-panel regional plot (A: −log10 p colored by r² with lead; B: SuSiE
  PIP; C: gene track [optional]; D: cCRE track [optional]).
- `finemap_report.pdf` — Phylo-branded report (data-driven from the JSON/CSV outputs + `study_config.json`).

## Clarification Questions

1. **Region** (ASK FIRST): explicit window (`chr:start-end`) or a lead variant + flank (±500 kb)?
2. **GWAS source**: an uploaded file, or a GWAS Catalog accession (`GCST######`)? (If uploaded, confirm
   the auto-detected column mapping.)
3. **Ancestry** (REQUIRED — no default): GWAS ancestry / 1000G superpopulation for the LD panel
   (EUR/AFR/EAS/SAS/AMR), **or** an in-sample LD reference if you have one.
4. **Trait type**: `quant` or `cc`? (Provide N; for `cc`, case/control counts if available.)
5. **Annotation**: which tissue(s) and target gene(s) (Ensembl ENSG) for eQTL lookups? Include ENCODE
   regulatory annotation? (All annotation layers are optional.)
6. **Parameters** (defaults usually fine): SuSiE `L=10` (max signals), `coverage=0.95`; flank ±500 kb.

## Standard Workflow

🚨 **MANDATORY: USE THE SCRIPTS. DO NOT WRITE INLINE SuSiE / PLINK / LD CODE.** 🚨
Every script prints a `✓` line on success and writes a JSON sidecar. Run in order. All scripts live in
this skill's `scripts/` dir — below, `THIS=` this skill's `scripts` dir (e.g.
`THIS=/mnt/skills/user/fine-mapping-susie/scripts`). Adjust `--out*` to your run folder.

**Step 0 — Get the GWAS.**
- *Uploaded file:* skip to Step 1 with the user's path.
- *GWAS Catalog accession:*
```bash
python $THIS/fetch_gwas_catalog.py --accession GCST006867 \
    --region 1:213400000-214600000 --out gwas_region.tsv --report gwas_fetch.json
```
✅ **VERIFICATION:** `✓` line with **fullPvalueSet=True**, harmonised file md5, ancestry, and N.
❌ If **fullPvalueSet=False** (metadata-only / access-gated, e.g. DIAMANTE): the script **does NOT
fabricate a file** — it prints the data-access route and stops. Relay this to the user and ask them to
upload the file or approve an **open** alternative accession. **Never silently substitute a dataset.**

**Step 1 — Ingest (auto column mapping + explicit confirmation):**
```bash
python $THIS/ingest_sumstats.py --input gwas_region.tsv --out tidy.tsv --report tidy.json \
    --type cc     # or quant
```
✅ **VERIFICATION:** `✓ Ingestion complete` + a printed column mapping you confirm.

**Step 2 — Detect build & liftover to GRCh38:**
```bash
python $THIS/detect_build_liftover.py --input tidy.tsv --out tidy_b38.tsv --report build.json
```
✅ **VERIFICATION:** `✓` line stating detected build and (if applied) liftover hg19→GRCh38.
(GWAS Catalog harmonised files are already GRCh38; this is a no-op check for them.)

**Step 3 — Ancestry-matched signed-r LD reference (THE ANCESTRY GATE):**
```bash
# (1) subset NYGC 30x 1000G to region + superpopulation. --region has NO chr prefix.
bash $THIS/build_ld.sh --region 1:213400000-214600000 --superpop EUR \
    --snps-only --maf 0.005 --out-prefix /workspace/ld_region
# (2) signed-r matrix oriented to the GWAS effect allele.
python $THIS/ld_utils.py --plink-prefix /workspace/ld_region \
    --harmonized tidy_b38.tsv --id-col snp \
    --out-matrix ld.tsv --out-snps ld_snps.txt --report ld.json
```
The harmonized table fed to `ld_utils.py` **must** have `snp, varid (chr:pos:ref:alt), effect_allele,
other_allele`; r is signed w.r.t. `effect_allele`.
✅ **VERIFICATION:** `✓` line; LD matrix is square, symmetric, with **negative r present** (signed r,
NOT r²). **`--superpop` MUST match the GWAS ancestry** — this is the ancestry gate.
> If the user has **in-sample LD**, skip Step 3 and pass their signed-r matrix (aligned to the effect
> allele, same `snp` ids) directly to Step 4.

**Step 4 — SuSiE fine-mapping + LD-mismatch diagnostics:**
```bash
Rscript $THIS/run_susie_finemap.R --sumstats tidy_b38.tsv --ld ld.tsv --ld-snps ld_snps.txt \
    --type cc --L 10 --coverage 0.95 --out-dir finemap_results
```
✅ **VERIFICATION:** `✓ SuSiE fine-mapping complete` printing: #credible sets, **estimated_s**
(LD-mismatch λ; ≈0 = z consistent with LD, large = mismatch), convergence, and the top-PIP variant.
⚠️ If **estimated_s is high** (heuristic: >~0.1) or SuSiE won't converge → **STOP and interpret with
caution**: almost always an LD/ancestry mismatch or too-small a window. Do not report a credible set as
trustworthy under a high `s`. See `references/ancestry-ld-guide.md`.

**Step 5 — Annotate credible-set variants (optional layers; each independently skippable + fails soft):**
```bash
python $THIS/annotate_variants.py --credible-set finemap_results/credible_set.csv \
    --out-prefix finemap_results/annot \
    --gtex ENSG00000117707,ENSG00000230461 --gtex-tissues Pancreas,Liver \
    --fetch-qtl --qtl-datasets QTD000554,QTD000296 --qtl-genes ENSG00000117707,ENSG00000230461 \
    --encode --genome-build 38
```
Layers: **GTEx v8** (`--gtex` genes + `--gtex-tissues`), **eQTL Catalogue** (`--fetch-qtl` +
`--qtl-datasets` QTD ids + `--qtl-genes`; the fetch defaults to the in-skill `fetch_qtl.py`, override with
`--fetch-qtl-script` only if needed), **ENCODE SCREEN** (`--encode`). Omit any flag to skip that layer.
✅ **VERIFICATION:** `✓ annotation complete`; writes `annot_gtex_eqtl.csv`, `annot_eqtl_catalogue.csv`,
`annot_encode_ccre.csv`, and `annot_summary.json` (only for layers you enabled). Non-eGenes / underpowered
lookups are reported **as such** (not dropped, not forced) — direction of effect is re-oriented to the
credible-set effect allele.

**Step 6 — Figures + PDF report.** First write a small `study_config.json` (drives the report prose):
```json
{
  "trait": "Type 2 diabetes", "genes": ["PROX1","PROX1-AS1"],
  "gene_symbols": {"ENSG00000117707":"PROX1","ENSG00000230461":"PROX1-AS1"},
  "ancestry": "EUR", "ld_source": "1000 Genomes Phase 3",
  "region": "chr1:213,400,000-214,600,000",
  "gwas_source": "GWAS Catalog GCST006867 (Xue et al. 2018, harmonised GRCh38)", "build": "GRCh38"
}
```
An optional gene track needs a `genes.tsv` (`gene<TAB>start<TAB>end<TAB>strand`, GRCh38 coords).
```bash
# regional multi-panel figure (panels: A -log10p by r2, B PIP, C genes [optional], D cCRE [optional])
python $THIS/make_finemap_figures.py \
    --pip finemap_results/all_variants_pip.csv --ld ld.tsv --ld-snps ld_snps.txt \
    --credible-set finemap_results/credible_set.csv \
    --ccre finemap_results/annot_encode_ccre.csv --genes genes.tsv \
    --title "PROX1 T2D locus" --out-prefix finemap_results/finemap_regional
# Phylo-branded PDF report (all annotation inputs optional)
python $THIS/generate_finemap_report.py \
    --susie-report finemap_results/susie_report.json --credible-set finemap_results/credible_set.csv \
    --config study_config.json --annot-summary finemap_results/annot_summary.json \
    --gtex finemap_results/annot_gtex_eqtl.csv --ccre finemap_results/annot_encode_ccre.csv \
    --figure finemap_results/finemap_regional.png --out finemap_results/finemap_report.pdf
```
✅ **VERIFICATION:** `[figures] wrote ...` and `[report] wrote ...`.
**Then run the MANDATORY media-output check (`Read` mode=media_output_check) on every figure AND the PDF
before reporting results.** Regenerate anything blank/clipped/entity-leaking and re-check.

❌ **IF YOU DON'T SEE THE `✓` MESSAGES:** you likely wrote inline code or skipped a step. Stop, use the
scripts.

## ⚠️ CRITICAL — DO NOT

- ❌ Fine-map without an **ancestry-matched** (or in-sample) LD reference → **STOP**: the credible set is
  meaningless under LD mismatch. Ancestry is a required input.
- ❌ Ignore **estimated_s** → always report it; a high `s` invalidates the result.
- ❌ Use **LDLink/LDmatrix** or any **r² / unsigned** LD → breaks SuSiE (needs signed r). Use
  `build_ld.sh` + `ld_utils.py`.
- ❌ **Fabricate or silently substitute** a GWAS when a source is access-gated → surface it, get user
  approval for an open alternative (the `fetch_gwas_catalog.py` gated-source path enforces this).
- ❌ Mix genome builds → everything GRCh38 (Step 2 enforces).
- ❌ Overstate mechanism → report **direction of effect** and eGene power honestly; fine-mapping is
  discovery/in-silico, not experimental proof.

⚠️ **IF A SCRIPT FAILS — Failure Hierarchy:** (1) fix & retry (install a package, correct an argument);
(2) edit the script and document the change; (3) read it, adapt, cite; (4) write from scratch only if
genuinely impossible, and say why.

## Common Issues

| Issue | Cause | Solution |
|---|---|---|
| **estimated_s high (>~0.1)** | LD/ancestry mismatch, or reference ≠ GWAS population | Re-check `--superpop` matches GWAS ancestry; prefer in-sample LD; widen window; interpret with caution. |
| **SuSiE won't converge / 0 credible sets** | Weak signal, too few SNPs, LD mismatch | Widen the region; verify LD ancestry; a diffuse signal may simply not fine-map. |
| **All SNPs dropped** | id mismatch (rsID vs `chr:pos:ref:alt`) or build mismatch across GWAS/LD | Use a consistent `--id-col`; confirm Step 2 lifted to GRCh38; LD `snp` ids must match. |
| **LD matrix has no negatives** | LD was computed as r² | Rebuild with `ld_utils.py` (signed r); never use r². |
| **Region in MHC (chr6:25–34 Mb)** | Long-range LD breaks fine-mapping | Warn; interpret cautiously or avoid. |
| **NA-z / null-beta variants** | Missing beta/se in sumstats | `run_susie_finemap.R` drops them (reported); expected for a few variants. |
| **ENCODE SCREEN API error/empty** | SCREEN "beta" API is intermittent | `annotate_variants.py` skips ENCODE gracefully with a warning; rerun later or omit. |
| **GTEx eQTL empty** | unversioned gencodeId or wrong build id | Use versioned `ENSG…​.N` and `chr{c}_{pos}_{ref}_{alt}_b38` variant ids (handled by the script). |
| **fullPvalueSet=False** | study record has no downloadable sumstats | Not a bug — dataset is metadata-only/gated; upload the file or pick an open accession. |

## Interpreting Results

- **PIP** = posterior probability a variant is causal for a signal. A **95% credible set** is the
  smallest variant set whose PIPs sum to ≥0.95 for one signal; a small set = well-resolved.
- **`within_cs_min_abs_corr`** near 1 in a size-1 set means a single variant is confidently prioritized
  (PROX1: rs340874, PIP≈0.96, size-1).
- **estimated_s** (from `estimate_s_rss`) quantifies GWAS↔LD consistency: ≈0 is ideal; large values mean
  the LD reference doesn't match the GWAS (usually ancestry) and the credible set is untrustworthy.
- **Multiple credible sets** = multiple independent signals at the locus (SuSiE with `L>1`).
- **Annotation** nominates a mechanism (variant → cCRE / eGene → tissue) but does **not** prove
  causation; check **direction of effect** and whether the target is actually an eGene (power).

See `references/finemapping-method-reference.md` and `references/ancestry-ld-guide.md`.

## Suggested Next Steps

- **Formal colocalization** of a credible-set signal with an eQTL/pQTL (PP.H4 via `coloc` /
  `coloc.susie`). This skill's outputs — the per-signal credible sets and effect-allele-aligned LD — are
  exactly the inputs a colocalization step needs; fine-map both the GWAS and the QTL signal here first.
- Repeat fine-mapping in a **second ancestry** (its own LD panel) to test transferability.
- Directionality (does expression cause the trait?) → `mendelian-randomization-twosamplemr` /
  `gwas-to-function-twas`.
- Deep variant annotation → `genetic-variant-annotation`.

## Related Skills

- `gwas-to-function-twas` — gene-level association / druggability.
- `mendelian-randomization-twosamplemr` — causal direction.
- `pdf-report-generation` — report conventions reused here.

## References

See `references/`: `ancestry-ld-guide.md` (ancestry→superpop mapping, in-sample vs 1000G, estimate_s /
kriging interpretation, multi-ancestry caveats), `finemapping-method-reference.md` (SuSiE susie_rss math,
L/coverage, credible sets, PIP), `gwas-source-guide.md` (GWAS Catalog harmonised layout, fullPvalueSet,
gated-source handling, upload format), `annotation-guide.md` (GTEx / eQTL Catalogue / ENCODE SCREEN
endpoints, ids, direction of effect, eGene power), `troubleshooting.md`.
