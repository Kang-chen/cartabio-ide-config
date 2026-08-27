---
id: "skill_70fa3fe61a8d4640c4a520641092cfcd"
name: "genetic-constraint-gating"
description: "Use to triage gene lists by gnomAD loss-of-function constraint and interpret target safety or knockout tolerance. Retrieves LOEUF, pLI, observed/expected, LoF Z, and missense constraint; resolves aliases; flags LoF-intolerant, haploinsufficient, or dosage-sensitive genes and suggests modality implications."
category: "genomics_genetics"
visibility: "public"
starting-prompt: "Flag which of these genes are loss-of-function intolerant using gnomAD LOEUF and pLI: ..."
---

# Genetic Constraint Gating

Triage a set of candidate genes by their tolerance to loss-of-function (LoF) variation, using gnomAD gene-level constraint, **and interpret each gene's LoF-intolerance as a drug-target signal**. Given genes, the skill flags which are **LoF-intolerant** (strong candidates for dominant / haploinsufficiency mechanisms), translates each gene's constraint into a **knockout-tolerance tier + systemic on-target risk + recommended target strategy**, and delivers a report-ready CSV, figures, and PDF.

## Scope

- **Does:** resolve gene identifiers → fetch gnomAD LoF constraint (LOEUF, pLI, o/e, LoF Z, LOEUF percentile, missense o/e) for **v2.1.1 (primary)** and **v4.1 (comparison)** → flag LoF-intolerance under standard thresholds → annotate v2.1.1↔v4.1 version shifts → **interpret knockout tolerance as a drug target** (KO-tolerance tier, systemic on-target safety risk, modality/strategy guidance) → attach ClinGen-grounded disease/inheritance notes → output CSV + 4 figures + Phylo PDF.
- **Drug-target interpretation (core deliverable).** The flag is not the end point: each gene is placed on a **knockout-tolerance tier** (Very low / Low / Intermediate / Tolerant) derived from LOEUF, pLI and gnomAD's LOEUF percentile, mapped to a **systemic on-target risk** (High → Lower) and a **recommended strategy**. The guiding principle: gnomAD constraint measures selection against *germline heterozygous* LoF (organism-level essentiality), **not** whether inhibiting the protein in a specific adult tissue/tumour is viable — so a strong LoF-intolerant flag is a **safety/modality caution for systemic full inhibition or degradation, not a veto**. Constrained tumour-suppressor-like genes are typically drugged through a **dependency created by their loss** (synthetic lethality / paralog / downstream node); LoF-**tolerant** genes (e.g. PCSK9) are the ones where direct systemic inhibition is most naturally safe. The strategy text is mechanism-agnostic guidance — it never invents specific drug names.
- **Does NOT:** annotate individual variants in a VCF (use `genetic-variant-annotation`), assign clinical pathogenicity, make clinical diagnoses, or recommend specific drugs/compounds. Population constraint reflects germline heterozygous LoF selection only; the druggability layer is a triage/interpretation aid, not a target-validation package (for deeper tractability use `target-tractability-druggability`).

## Inputs

- A list of **HGNC gene symbols** (typed in the prompt) — primary path.
- An uploaded **CSV/TXT** with a gene column (auto-detects `gene`/`symbol`/`gene_symbol`; else first column; TXT = one token per line or comma/space separated).
- **Ensembl gene IDs** (ENSG…) and/or **deprecated symbols/aliases** — resolved to the current symbol via MyGene.info before querying gnomAD (the remap is recorded in `alias_note`).
- Any number of genes (dozens are fine; each is a few lightweight API calls).

## Outputs (written to the output dir, default `/mnt/results`)

- `gnomad_constraint_flags.csv` — one row per input gene. Constraint columns: `gene, input_as, gene_id, alias_note, obs_lof_v2, exp_lof_v2, oe_lof_v2, LOEUF_v2, LOEUF_lower_v2, LOEUF_pct_v2, pLI_v2, lof_z_v2, oe_mis_v2, mis_z_v2, LOEUF_v4, LOEUF_pct_v4, pLI_v4, flag_basis, LoF_intolerant, flag_driver, version_shift`; disease columns: `gene_name, disease_label, inheritance, mondo_id, gene_mim, disease_source`; **drug-target interpretation columns: `ko_tolerance_tier, ko_tolerance_rationale, systemic_target_risk, systemic_target_note, target_strategy, actionability, druggability_verdict`**.
- `fig1_ranked_loeuf.(png|svg)` — genes ranked by v2.1.1 LOEUF with the cutoff line.
- `fig2_pli_vs_loeuf.(png|svg)` — pLI vs LOEUF scatter with threshold quadrants.
- `fig3_loeuf_v2_vs_v4.(png|svg)` — v2.1.1→v4.1 LOEUF shift.
- `fig4_druggability_tier.(png|svg)` — **drug-target reading: LOEUF vs knockout-tolerance tier, with the high-on-target-risk zone shaded**.
- `report_gnomad_LoF_constraint.pdf` — Phylo-branded report (Intro, Methods, Results with flag table + figs 1–3 + version-shift note, per-gene disease table, **§3.3 Drug-target interpretation: KO-tolerance tier table + fig 4 + per-gene reading**, Conclusions).

Lighter modes: `--no-pdf` (CSV + figures) and `--csv-only` (CSV only).

## How to run

All logic lives in `scripts/`. Run the orchestrator (imports resolve when run from the skill dir):

```bash
python scripts/run_constraint_gating.py --genes SCN1A MECP2 NF1 ARID1B SETD2 TP53 PTEN PCSK9 CYP2D6 GSTM1 --outdir /mnt/results
python scripts/run_constraint_gating.py --file /mnt/user-uploads/candidates.csv --outdir /mnt/results
python scripts/run_constraint_gating.py --genes TP53 PTEN --no-pdf        # table + figures only
python scripts/run_constraint_gating.py --genes TP53 PTEN --loeuf-cut 0.35 --pli-cut 0.9
```

Or import the pieces to compose into a larger analysis: `analyze_genes` → DataFrame (already carries the drug-target interpretation columns); `constraint_druggability.annotate_druggability(row)` / `add_druggability_columns(df)` to (re)compute the KO-tolerance tier + risk + strategy standalone; `make_all_figures` (returns figs 1–4 incl. `druggability`); `build_report`.

**After generating any figure or the PDF, run a media-output-check** (`Read` with `mode="media_output_check"`) on each PNG and the PDF; regenerate if blank/clipped/unreadable. This is required by the platform figure guidelines and is not automated inside the scripts.

## Workflow (what the code does, and why)

1. **Resolve identifiers** (`constraint_fetch.resolve_gene`). gnomAD keys on current gene symbols; deprecated symbols/aliases/ENSG IDs must be normalized first or they silently return no record. MyGene.info maps `symbol:X OR alias:X` (or `ensembl.gene:ENSG…`) → current symbol + ENSG + Entrez, preferring an exact symbol hit and avoiding antisense (`-AS1`) partials.
2. **Fetch constraint** (`fetch_constraint`) for **v2.1.1** (`reference_genome: GRCh37`) and **v4.1** (`GRCh38`). Fields: `oe_lof/oe_lof_lower/oe_lof_upper`, `oe_lof_percentile` (gnomAD's LOEUF percentile; populated in v4.1, often null in v2.1.1), `pLI`, `lof_z`, `obs_lof/exp_lof`, and missense `oe_mis/mis_z`. **LOEUF = `oe_lof_upper`** (upper bound of the o/e 90% CI). gnomAD GraphQL throws transient errors *and* intermittently returns a 200 with `data.gene == null` for a gene that does have a record → the client retries both with backoff (see `fetch_constraint`'s `null_retries`). **When triaging a small, important gene set, verify each gene resolved on its intended basis (`flag_basis == 'v2.1.1'` with non-null `LOEUF_v2`/`pLI_v2`); if a silent null forced a v4.1 fallback, re-run so no call rests on a fallback.**
3. **Flag LoF-intolerance** on v2.1.1: `LOEUF < 0.35 OR pLI >= 0.90` (standard gnomAD convention). Both metrics are reported and `flag_driver` records which crossed, so the call is transparent and re-cuttable. v2.1.1 is the basis because both pLI and LOEUF are defined for essentially all genes; v4.1 de-emphasizes pLI.
4. **Version-shift annotation.** If the intolerance call flips between v2.1.1 and v4.1, or LOEUF moves ≥ 0.15, the gene is flagged in `version_shift`. This surfaces small-gene / low-expected-LoF borderline genes (e.g. MECP2, TP53) that a single-version flag would miss — larger v4.1 cohorts usually tighten these toward constraint.
5. **Ground the disease note** (`fetch_disease`) from ClinGen gene-disease validity curation via MyGene.info (`clingen.clinical_validity`): disease label, classification (definitive/strong/…), mode of inheritance, MONDO ID. Always includes gene name + gene MIM. **If no curated association exists, the note is `"no curated disease association retrieved"` and `disease_source='none'` — never model-generated.** (Optional, only on explicit user request: a clearly-tagged narrative note may be added, with no invented accession numbers.)
6. **Interpret knockout tolerance as a drug target** (`constraint_druggability.add_druggability_columns`). Each gene's constraint is translated — *deterministically from the metrics, never hardcoded per gene* — into: a **KO-tolerance tier** (`Very low (near-essential)` if in gnomAD's most-constrained LOEUF decile or LOEUF < 0.20 with pLI ≥ 0.90; `Low (LoF-intolerant)` if LOEUF < 0.35 or pLI ≥ 0.90; `Intermediate` if LOEUF < 0.60; else `Tolerant`), a **systemic on-target risk** (High → Lower) for complete systemic inhibition/degradation, and a **recommended strategy** (drug-the-dependency / synthetic-lethality framing for constrained genes; direct-inhibition for tolerant genes). A curated dominant/X-linked LoF disease mechanism sharpens the caution. The layer states the germline-heterozygous-vs-tissue-specific principle explicitly and adds no drug names.
7. **Assemble & report:** tidy CSV, four colorblind-safe figures (Okabe-Ito, Liberation Sans, editable SVG; fig 4 = the drug-target reading), and the Phylo PDF (Results §3.3 carries the KO-tolerance tier table, fig 4, and a per-gene reading).

## Database reference

| Source | Endpoint | Used for | Notes |
|---|---|---|---|
| gnomAD | `POST https://gnomad.broadinstitute.org/api` (GraphQL) | LoF constraint (LOEUF, pLI, o/e, obs/exp, LoF Z, LOEUF percentile, missense o/e & Z) | v2.1.1 = GRCh37 (primary); v4.1 = GRCh38. `gene(gene_symbol, reference_genome){ gnomad_constraint{...} }`. Transient errors *and* silent `gene:null` → retry/backoff. Public, no key. |
| MyGene.info | `GET /v3/query`, `GET /v3/gene/<entrez>` | symbol/alias/ENSG resolution; ClinGen disease + inheritance + MONDO; gene name + MIM | Public, no key. `clingen.clinical_validity` provides grounded disease/MOI. |

## Scientific caveats

- **Constraint = germline heterozygous LoF selection.** It under-calls recessive genes, somatic-only tumor suppressors, gain-of-function genes, and copy-number/structural-variant-driven dosage effects. A "No" flag ≠ biologically unimportant (e.g. **PTEN**, **TP53** are bona fide disease genes with only intermediate population LoF constraint).
- **Small genes** with few expected LoF variants are under-powered in v2.1.1 and can be false-negatives (e.g. **MECP2** just misses in v2.1.1 but is clearly intolerant in v4.1) — always check the `version_shift` column.
- **Segmental-duplication / CNV-prone loci** (e.g. **CYP2D6**, **GSTM1**) have unreliable SNV-based constraint; their common-null status is a copy-number phenomenon not captured by LOEUF/pLI.
- **Version choice:** v2.1.1 gives both pLI and LOEUF for all genes and matches most published constraint discussion; v4.1 is larger and LOEUF-led. This skill flags on v2.1.1 and reports v4.1 for context by design.
- **Druggability interpretation is a constraint-based *safety/modality* signal, not target validation.** The KO-tolerance tier reflects organism-level essentiality from germline heterozygous LoF selection; it does **not** measure whether a gene is druggable in a given tissue/tumour, nor structural tractability, expression, or existing chemistry. A "High systemic on-target risk" call flags that *complete systemic* inhibition/degradation is risky — many such genes are excellent targets via synthetic lethality, tumour-restricted delivery, or a downstream node. Conversely a "Tolerant / Lower risk" call (e.g. PCSK9) means on-target LoF is well tolerated, not that efficacy or delivery are solved. Pair with `target-tractability-druggability`, DepMap essentiality, and mechanism for target validation.
- **Not a clinical tool** — this is a research candidate-gene triage aid.

## Error handling

- **Unresolved symbol** → row kept with `LoF_intolerant='N/A'`, `note` explains it wasn't resolved (never dropped, never guessed).
- **No gnomAD record** → `LoF_intolerant='N/A'`, reported as not-available with reason.
- **gnomAD transient failure** → automatic retry with backoff; if still failing after retries the gene is reported as not-available rather than crashing the run.
- **No ClinGen entry** → explicit "no curated disease association retrieved".

## Self-test

`scripts/self_test.py` runs the full pipeline on the 10-gene set used to author the skill and asserts: the expected flags (SCN1A/ARID1B/SETD2/NF1 = Yes; others = No under the v2.1.1 standard rule), that a version shift is surfaced for a borderline gene (MECP2/TP53), that every disease note is grounded or explicitly not-retrieved, **and that the drug-target layer is present and internally consistent** (valid KO-tolerance tier for every gene; LoF-intolerant genes fall in a constrained tier with High systemic on-target risk; LoF-tolerant genes read as Lower risk / direct-inhibition-favourable). Run `python scripts/self_test.py` once on first use to confirm the live APIs and logic work end to end (add `--full` to also render figures + PDF to `/tmp`).
