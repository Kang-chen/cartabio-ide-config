# Metabolite modules from predicted enzyme (EC) abundances

This reference defines the small, curated **metabolite-relevant enzyme modules** used
in Stage 4 (functional interpretation) of the skill, and encodes the correctness rules
that keep the inference honest. Modules are keyed on **EC numbers** (IUBMB Enzyme
Commission nomenclature), which PICRUSt2 predicts **by default**
(`EC_metagenome_out/pred_metagenome_unstrat.tsv.gz`). See `metabolite_modules_ec.csv`
for the machine-readable table and `DATA_SOURCES.md` for licensing/attribution.

> **Why EC and not KEGG Orthology (KO).** KEGG is not licensed for commercial use, so
> KO identifiers and KEGG pathway/module data are intentionally **not** shipped in this
> skill. EC numbers are a free, open IUBMB standard that KEGG cross-references but does
> not own, and they are PICRUSt2's default output — so the pivot to EC is both
> license-clean and requires no extra prediction step. An optional, off-by-default
> academic KO mode exists (`USE_KO`) for users with their own KO table who have
> confirmed they are covered by KEGG's academic terms; it ships no KEGG data.

> **These are predicted genomic potential, not measured metabolites.** 16S + PICRUSt2
> infers gene/enzyme copy number from taxonomy. Treat every module result as
> **hypothesis-generating**: it says "the community encodes more/less capacity for this
> reaction", never "this metabolite is higher/lower". Confirm with metabolomics,
> metagenomics, or culture before making a mechanistic claim.

## Modules

| Module | EC numbers | Reads out | Notes |
|---|---|---|---|
| `Butyrate_but_route` | 2.8.3.8, 2.8.3.9 | Health-associated butyrate synthesis (CoA-transferase) | Report **separately** from the buk route |
| `Butyrate_buk_route` | 2.7.2.7, 2.3.1.19 | Dysbiosis-associated butyrate synthesis (kinase) | Report **separately** from the but route |
| `Butyrate_total` | 2.8.3.8, 2.8.3.9, 2.7.2.7, 2.3.1.19, 1.3.8.1 | Aggregate butyrate capacity | Aggregate can **mask** a but→buk route switch — always inspect the split too |
| `Propionate` | 2.8.3.18, 2.7.2.1 | Propionate synthesis capacity | 2.7.2.1 (acetate kinase) is **shared with acetate** and can dominate — audit domination |
| `Acetate` | 2.7.2.1, 2.3.1.8, 6.2.1.1 | Acetate synthesis capacity | 2.7.2.1 shared with propionate |
| `BSH` | 3.5.1.24 | Bile-salt hydrolase (primary bile-acid deconjugation) | Single-enzyme module by design |
| `Indole` | 4.1.99.1 | Tryptophanase → indole (AhR ligand) | Single-enzyme module by design |
| `bai_secondary_BA` | (1.3.1.116) | 7α-dehydroxylation / secondary bile acids | **GATED OFF by default** — see below |

## Correctness rules (do not silently drop these)

1. **Split butyrate into but vs buk routes.** The health-associated CoA-transferase
   route (`but`, EC:2.8.3.8/2.8.3.9) and the kinase route (`buk`, EC:2.7.2.7 +
   EC:2.3.1.19) move in opposite directions in dysbiosis. The `Butyrate_total`
   aggregate can look flat or even *up* while the route mix shifts toward buk. Always
   report the two routes separately; FDR-correct the two routes **within their own
   family**, and the other main modules **jointly**.

2. **Audit single-enzyme domination.** For every multi-enzyme module, print which EC
   carries the largest mean fraction of the module score. If one enzyme carries the
   vast majority (e.g. acetate kinase 2.7.2.1 in the propionate/acetate modules), the
   module result is really about that one shared enzyme — say so in the interpretation.

3. **The `bai` secondary-bile-acid module is GATED OFF by default.** Two independent
   reasons: (a) of the eight operon genes, only `baiH` has a clean EC (EC:1.3.1.116);
   the other seven (baiA/B/CD/E/F/G/I) have **no EC number**, so the operon cannot be
   EC-keyed; and (b) 16S/PICRUSt2 cannot reliably resolve this low-abundance operon.
   **Never report secondary bile-acid (DCA/LCA) depletion from 16S prediction.** If a
   user explicitly enables the module, flag every result as low-confidence.

4. **Exclude the promiscuous 3-dehydro-bile-acid reductase (EC:1.3.1.114).** This
   enzyme (formerly KO K07007) is widespread and ~360× more abundant than the next bai
   enzyme. Including it makes the bai module look strongly, significantly *depleted*
   (an artifact of a promiscuous enzyme's overall abundance, not of bile-acid
   metabolism). It is **excluded** from the bai module. The exclusion rule is retained
   defensively even though EC:1.3.1.114 is absent from the reference PICRUSt2 EC output.

5. **Repeated measures → subject-mean test.** With multiple samples per subject,
   collapse to a per-subject mean before the group comparison (Wilcoxon), then BH-correct.

## What each module can and cannot tell you

- **Can:** flag communities that encode more/less capacity for a defined reaction;
  distinguish health-associated vs dysbiosis-associated butyrate routes; screen for
  gross shifts in SCFA / primary-bile-acid / indole enzyme content.
- **Cannot:** quantify metabolite concentrations; resolve strain-level or operon-level
  pathways poorly represented in 16S (e.g. secondary bile acids); substitute for
  metagenomics or metabolomics. Report accordingly.
