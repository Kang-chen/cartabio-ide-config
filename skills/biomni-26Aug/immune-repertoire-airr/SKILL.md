---
id: "skill_d1c7d4c4273d38f002903e8199b4085c"
name: "immune-repertoire-airr"
description: "Use to analyze TCR-seq or BCR-seq immune repertoires from 10x V(D)J, immunoSEQ, MiXCR, or AIRR-C data. Covers clonotypes, clonal expansion, diversity, CDR3 sharing/public clones, V/J usage, rarefaction, and repertoire overlap for single-cell or bulk AIRR data."
category: "transcriptomics"
visibility: "public"
starting-prompt: "Analyze my TCR/BCR repertoire data for clonality, diversity, and overlap"
---

# TCR/BCR Immune Repertoire Analysis (AIRR)

Descriptive repertoire profiling of one or more adaptive-immune-receptor samples with
the R package **immunarch**, plus a Phylo-branded PDF report. Covers the four canonical
axes — **clonality**, **diversity**, **V/J gene usage**, and **repertoire overlap** —
with interpretation that adapts to data **modality** (single-cell vs. bulk) and
**receptor** (TCR vs. BCR).

The skill ships two tested, parameterized scripts. **Run them as the default path**;
do not regenerate the analysis logic ad hoc (the immunarch return structures and the
modality/Chao1 handling are subtle and already verified — see
`references/immunarch_api_notes.md`).

---

## Scope

**Does:** load AIRR/repertoire files (auto-detecting format), compute per-sample
clonality, diversity (richness + evenness + rarefaction), V/J gene-segment usage with
cross-sample similarity, and pairwise clonotype overlap; run optional group comparisons;
and produce a PDF report (infographic + intro + methods + results + conclusions +
figures + references + next steps).

**Does NOT:** reconstruct B-cell somatic-hypermutation lineages (Change-O / Immcantation
territory), integrate paired single-cell gene expression / phenotype, assemble raw
FASTQ into clonotypes (assumes clonotypes are already called by Cell Ranger / MiXCR /
immunoSEQ), or perform disease-outcome inference on underpowered cohorts. It is
descriptive/exploratory unless the cohort has a real biological contrast with adequate n.

---

## Inputs

Any format `immunarch::repLoad()` auto-detects, one file (or Cell Ranger output folder)
per sample in a single directory:

| Source | Typical file | Modality |
|---|---|---|
| 10x Genomics Cell Ranger V(D)J | `filtered_contig_annotations.csv` | single-cell |
| Adaptive Biotechnologies immunoSEQ | `*.tsv` | bulk |
| MiXCR | `*.clones.txt` / `*.clns` export | bulk |
| AIRR-C Rearrangement | `*_airr.tsv` (`_rearrangement.tsv`) | either |
| immunarch native | `.txt`/`.tsv` | either |

- **Optional metadata:** `metadata.txt` (tab-separated, first column `Sample`) in the
  same directory, giving group columns (e.g. `Group`, `Timepoint`, `Response`) for
  comparisons. If absent, the skill runs descriptively and can build simple grouping
  from sample-name tokens.
- **Receptor/chain:** auto-detected from V-gene prefixes; override if needed.
- **Expected size:** designed for up to a few dozen samples; each sample thousands to
  ~10⁵ clonotypes. Larger cohorts still run but keep figures legible.

If the user has **no data**, offer a bounded public fallback (e.g. 10x Genomics demo
V(D)J samples such as `sc5p_v2_hs_PBMC_1k`, `sc5p_v2_hs_PBMC_10k`) purely to demonstrate
the workflow — state clearly it is a demo, not the user's biology.

---

## Outputs

Written under `REP_OUT_DIR` (default `/mnt/results/repertoire/`):

- `tables/` — `sample_summary.csv`, `diversity_metrics.csv`, `clonality_top.csv`,
  `clonality_homeo.csv`, `geneusage_V.csv`, `geneusage_J.csv`,
  `overlap_public.csv`, `overlap_morisita.csv`, `overlap_flagged_pairs.csv`,
  and `wilcoxon_tests.csv` (if groups defined).
- `figures/` — clonality (homeostasis, top-clones), diversity (all-metrics,
  rarefaction), gene usage (V, J, JS heatmap, correlation heatmap), overlap (shared-
  clonotype heatmap, Morisita-Horn heatmap). Both **PNG** and editable **SVG**.
- `analysis_metrics.json` — machine-readable receptor/chain/modality flags + key ranges
  (consumed by the report generator).
- `report_repertoire_analysis.pdf` — the final report.

---

## Environment / installation (READ THIS FIRST)

immunarch is **not preinstalled**. Install version **0.9.1** (the classic in-memory
loader) — NOT current CRAN, which pulls `immundata`→`duckdb` and compiles pathologically
slowly (>40 min) for zero benefit here.

1. Provision a multi-core machine with `ManageMachine` (8 CPU). worker-0 (1 CPU) is too
   slow for the build.
2. Install:
   ```r
   .libPaths("/workspace/.Rlib")
   options(repos = "https://cloud.r-project.org", Ncpus = 8)
   Sys.setenv(MAKEFLAGS = "-j8")
   install.packages("ggraph")                          # dep that fails if skipped
   remotes::install_version("immunarch", version = "0.9.1",
                            dependencies = NA, upgrade = "never")
   ```
   `dependencies = NA` (not FALSE) installs required deps only. Full detail and the "why"
   are in `references/immunarch_api_notes.md`.
3. ggplot2, dplyr, tidyr, jsonlite are preinstalled. reportlab, pypdf, pandas, PIL are
   preinstalled for the PDF step.

---

## Workflow

Use McPAS-TCR or VDJdb for optional antigen annotation and `hpc_search_tools` for
HPC capabilities such as ImmuneBuilder.

1. **Stage inputs.** Put one file/folder per sample in `REP_DATA_DIR`
   (default `/mnt/shared-workspace/shared/rep_data/`). Add `metadata.txt` if group
   comparisons are wanted. Confirm the source format with the user.

2. **Install immunarch 0.9.1** on an 8-core machine (see Environment above). Skip if
   already present in `/workspace/.Rlib`.

3. **Run the analysis script.** `scripts/repertoire_analysis.R` is fully parameterized
   via environment variables (all optional; sensible defaults):
   ```bash
   REP_DATA_DIR=/mnt/shared-workspace/shared/rep_data \
   REP_OUT_DIR=/mnt/results/repertoire \
   REP_RECEPTOR=auto REP_CHAIN=auto REP_MODALITY=auto \
   REP_GROUP_COLS=Group REP_SPECIES=hs \
   Rscript scripts/repertoire_analysis.R
   ```
   The script auto-detects **receptor** (TR* → TCR, IG* → BCR), **chain** (default TRB
   for TCR, IGH for BCR), and **modality** (singleton fraction ≥ 0.85 → single-cell),
   then writes all tables, figures, and `analysis_metrics.json`. Override any auto value
   if you know better.

4. **Modality branch — the scientific crux.** How results are interpreted depends on
   modality (the script records it; the report enforces it):
   - **Single-cell (e.g. 10x):** abundance = cells. Nearly every clonotype is a
     singleton, so **Chao1 richness is inflated and unreliable** (verified: up to
     ~3×10⁵ vs. hundreds–thousands of observed clonotypes). Rank samples by **evenness**
     (Shannon, inverse Simpson, Gini-Simpson, D50) and **rarefaction**. Frame
     "clonality" as clonal-space homeostasis / top-clone occupancy, NOT PCR expansion.
   - **Bulk (e.g. immunoSEQ, MiXCR):** abundance = templates/reads. Chao1 and richness
     estimators are trustworthy; **clonal expansion is real biology** (report top-clone
     fractions and the hyperexpanded bin). Depth-normalize before comparing richness.

5. **Overlap interpretation.** The script flags sample pairs whose shared-clonotype count
   is far above the cross-sample background (`overlap_flagged_pairs.csv`). Because an
   individual's repertoire is essentially private, a flagged pair almost always means a
   **shared biological source** (same donor / replicate / contamination) — a built-in
   consistency check, not a biological discovery. (Verified example: two chemistries of
   one donor shared 202 clonotypes vs. ≤14 for unrelated pairs.)

6. **Group comparison (optional).** If a 2-level group column exists, the script runs
   two-sided Wilcoxon rank-sum tests on scalar metrics (`exact=FALSE`) and labels them
   **exploratory** when the smaller group has n < 4 (small n cannot yield small p-values).

7. **Gather citations.** Use `LiteratureSearch` for methods/interpretation references.
   `references/citations_seed.json` provides a verified starting set (immunarch, diversity
   metrics, evenness robustness, BCR clonal definition). Save the citations you use as
   `<REP_OUT_DIR>/citations.json` (a list of `{"n":int,"text":str}`).

8. **Build the infographic (schematic).** Generate the one-page visual summary with the
   **GenerateImage** tool (it is a schematic — workflow + headline finding — NOT a data
   plot). Save it as `<REP_OUT_DIR>/infographic.png`. If GenerateImage is unavailable,
   skip it; the report degrades gracefully.

9. **Generate the PDF report.** `scripts/make_report.py` reads `analysis_metrics.json`,
   the tables, the figures, `citations.json`, and the optional infographic, and writes a
   modality/receptor-aware report:
   ```bash
   python3 scripts/make_report.py --out-dir /mnt/results/repertoire \
     --pdf /mnt/results/report_repertoire_analysis.pdf \
     --title "Immune Repertoire Analysis"
   ```
   It follows the **pdf-report-generation** skill (load that skill for branding/layout
   conventions): Phylo palette, infographic summary, Introduction, Methods, Results,
   Conclusions (with modality/BCR limitations), References, and Next steps, and it runs a
   validation gate (pages ≥ 5, size > 20 kB, extractable text).

10. **QC every figure and the PDF.** Run the `Read` media-output-check on each key figure
    PNG and on rendered PDF pages; regenerate anything blank, clipped, or unreadable
    before delivering.

---

## Scientific caveats

- **Single-cell Chao1 is an artifact.** Never report inflated single-cell Chao1 as
  richness. Evenness indices (Gini-Simpson, Pielou/Shannon, inverse Simpson) are the most
  robust across data types [4]; prefer them plus rarefaction for cross-sample ranking.
- **BCR ≠ TCR.** For BCR, somatic hypermutation means an **exact-CDR3 clonotype is not a
  clonal lineage** [5]; isotype/class-switch structure is not modeled. Never silently call
  exact-CDR3 matches "clones/lineages" for BCR. Full SHM lineage reconstruction is out of
  scope (use Immcantation/Change-O separately).
- **Depth confounds diversity.** Deeper samples look more diverse. Use rarefaction (and,
  for bulk, depth-matched subsampling) before comparing.
- **Overlap ≠ biology by default.** High overlap almost always flags shared source, not a
  shared antigen response — verify sample provenance first.
- **Small n = exploratory.** Group tests on a few samples per arm are descriptive; do not
  over-interpret p-values. Move to inference only with adequate, balanced n.
- **`repDiversity(.method="entropy")` is invalid in 0.9.1** — Shannon is computed manually
  from clonotype proportions (handled by the script).
- **10x rows are paired chains** (`TRA;TRB`); gene usage uses `.ambig="exc"` so only the
  requested chain contributes.
- **FUSE writes:** figures are written to `/workspace` then copied to `/mnt/results`
  (R's `file.copy` to S3 FUSE yields 0-byte files); the script handles this.

---

## Optional enrichment (graceful-skip)

- **Antigen specificity:** annotate public/expanded CDR3β against **McPAS-TCR** (Biomni
  datalake) or VDJdb to suggest antigen associations. If the database is not mounted /
  resolvable, skip and say so — do not fabricate antigen calls.
- **Receptor structure:** for a few clones of interest, predict 3D structure with
  **ImmuneBuilder** (Biomni HPC: TCRBuilder2 / antibody models). Optional; not part of
  the default path.

---

## Bundled files

- `scripts/repertoire_analysis.R` — parameterized, modality-aware immunarch analysis.
- `scripts/make_report.py` — parameterized ReportLab PDF generator.
- `references/immunarch_api_notes.md` — verified 0.9.1 API return structures, install
  recipe, modality/Chao1 rationale, FUSE gotchas. **Read before editing the R script.**
- `references/citations_seed.json` — verified starting citations.

---

## Test prompts

1. *"I have 10x Cell Ranger TCR `filtered_contig_annotations.csv` files for 6 PBMC
   samples — analyze the repertoires and make a report."* (single-cell TCR; expect Chao1
   flagged as artifact, evenness emphasized.)
2. *"Run clonality and diversity on my Adaptive immunoSEQ TCRβ bulk samples, responders
   vs non-responders."* (bulk TCR + group test; expect Chao1 trusted, clonal expansion
   reported, Wilcoxon.)
3. *"Profile my 10x BCR (IGH) repertoires."* (BCR edge case; expect IGH auto-detection and
   explicit SHM / exact-CDR3-≠-lineage caveats.)
4. *"Annotate the expanded clonotypes with antigen specificity"* when no antigen DB is
   mounted. (Expect graceful skip with a clear message, not fabricated antigens.)
