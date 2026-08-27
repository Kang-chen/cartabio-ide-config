---
id: "skill_fd517e4fafbb82ec7030a849e0ea0c23"
name: microbiome-analysis
description: "Use for two-group 16S rRNA amplicon analysis from processed ASV/OTU feature tables. Covers alpha/beta diversity, consensus differential abundance with ANCOM-BC2/ALDEx2/MaAsLin2, PICRUSt2 function prediction, and inferred SCFA, bile-acid, or tryptophan-metabolite capacity."
category: "functional_analysis"
visibility: "public"
starting-prompt: "Compare two groups in my 16S feature table: alpha/beta diversity, differential-abundance testing, and predicted function/metabolite capacity."
---

# 16S Microbiome: Differential Abundance + Predicted Function + Metabolite Inference

A modular, **adaptive** workflow. The stages below are independent — **ask the
user which they want** and run only those. A user with two 16S groups who wants
"differentially abundant genera" needs only Stage 1–2; someone asking about SCFA
capacity needs Stage 3–4; a full study report needs all of it plus the report stage.

## Scope

- **Does**: two-group 16S comparison — community diversity, taxa differential
  abundance (3-method consensus), PICRUSt2 predicted function, curated microbial
  metabolite module inference, literature grounding, and a branded PDF report
  with an infographic.
- **Does NOT**: raw-read processing (DADA2/Deblur), public-data acquisition,
  shotgun metagenomics, >2-group / continuous-outcome designs (extendable, not
  default), or functional validation. Metabolite modules are **predicted genomic
  potential**, never measured metabolites.

## Inputs (entry point)

Assume the user already has **processed** data:

| Input | Format | Used by |
|---|---|---|
| Feature table | TSV, features × samples, integer counts; first-col header `#OTU ID` for PICRUSt2 | all stages |
| Representative sequences | FASTA, headers = feature IDs | PICRUSt2 (Stage 3) |
| Taxonomy | TSV, feature ID + ranks (Kingdom…Genus/Species) | Stage 2 |
| Sample metadata | TSV, first col = sample ID; must include a 2-level group column; ideally subject ID + covariates | all stages |
| Phylogenetic tree (optional) | Newick | Faith's PD + UniFrac |

If the user lacks processed data, tell them raw processing is out of scope and
point them to DADA2/Deblur first (or a public accession + QIIME2). If they only
have a raw-read accession, note that read QC/assembly-adjacent tooling exists in
the Biomni HPC cluster (FastQC/MultiQC, and QIIME2-style upstream steps are still
their responsibility) — see `references/biomni_resources.md`.

## Outputs

CSV tables + PNG/SVG figures under `results/` (or `/mnt/results/...` for
user-facing deliverables), and an optional PDF report with an infographic
summary page. Save durable deliverables to `/mnt/results`; keep heavy
intermediates (PICRUSt2 output) on `/mnt/shared-workspace`.

## Bundled assets

- `scripts/da_diversity.R` — Stage 1–2 (alpha LMM, beta PERMANOVA, 3-method DA consensus). **R-first.**
- `scripts/functional_da.R` — Stage 3 functional DA (ALDEx2 on the **EC** unstratified table by default).
- `scripts/metabolite_modules.py` — Stage 4 metabolite module scoring (EC-keyed; route-split, EC:1.3.1.114 excluded, bai gated off, domination audit; off-by-default academic KO mode).
- `scripts/build_report.py` — reusable Phylo-branded PDF module (implements the `pdf-report-generation` skill's patterns; adds an infographic page, a references block, and a next-steps block).
- `references/metabolite_modules.md` + `references/metabolite_modules_ec.csv` — **EC-keyed** metabolite module sets + the EC:1.3.1.114 exclusion rationale + bai gating + interpretation guardrails.
- `references/DATA_SOURCES.md` — **licensing & attribution**: why KEGG was removed (not licensed for commercial use), why EC/PICRUSt2 defaults are used, MetaCyc/eggNOG notes, and the academic KO opt-in.
- `references/commands_and_environment.md` — install recipes, OOM-safe PICRUSt2 command, machine sizing, FUSE gotchas, preinstalled-package notes, free EC verification recipe.
- `references/biomni_resources.md` — **which Biomni-installed tools, databases, and packages this skill uses at each stage**, and how to discover more.

Edit the CONFIG block at the top of each script (file paths, group column,
reference level, subject column, covariates, thresholds) — they are **templates**,
not hardcoded to any dataset.

## Use the Biomni environment, don't reinvent it

This skill runs inside Biomni/Phylo, which ships a large catalog of tools,
databases, and packages. **Prefer them over ad-hoc installs.** Two rules:

1. **Discover before assuming.** Start from `references/biomni_resources.md`.
   Verify Python packages with imports, R packages with `requireNamespace()`, and
   upstream HPC tools with `hpc_search_tools`.
2. **Route external knowledge through the right tool.** Use `LiteratureSearch`
   (not model memory) for any background/mechanistic/citation claim. Verify or
   refresh the module **EC numbers** against a free enzyme database (ExplorEnz /
   IUBMB Enzyme Nomenclature / Rhea) before trusting a module definition. **Do not
   use KEGG** — it is not licensed for commercial use (see `DATA_SOURCES.md`).

Key preinstalled resources this skill leans on (verify directly): Python
`scikit-bio` (diversity metrics, ordination),
`biom-format` (BIOM I/O), `gseapy` (optional EC/pathway gene-set enrichment),
`statsmodels`/`scipy` (tests, FDR), `reportlab`/`pypdf`/`pillow` (PDF); R
`phyloseq`, `ComplexHeatmap`, `clusterProfiler`, `ggplot2`/`ggprism`; free enzyme
databases (ExplorEnz/IUBMB/Rhea) for EC verification; and the HPC
read-alignment/assembly tools for any upstream work the user may need.
Bioconductor stats packages (ANCOM-BC2, ALDEx2, MaAsLin2) are installed per
`references/commands_and_environment.md`.

## Language

**R-first** for statistics (phyloseq, ANCOM-BC2, ALDEx2, MaAsLin2, lme4/vegan).
**Python** only where required: PICRUSt2 CLI orchestration, metabolite-module
scoring, `scikit-bio` diversity helpers, and the reportlab PDF. The
metabolite-module math is language-agnostic (a small R port is trivial if the
user insists on all-R).

---

## Workflow

### Step 0 — Clarify, discover resources, and inspect (always)

1. Confirm the **two groups** and which is the reference/control level.
2. Confirm **repeated measures**: do subjects contribute multiple samples? If yes,
   you MUST use subject random effects / subject-level permutation / subject-mean
   tests — never treat samples as independent. Identify the subject-ID column.
3. Confirm **covariates** to adjust for (sex, BMI, sequencing run, etc.).
4. Ask **which stages** the user wants (diversity / taxa DA / function /
   metabolites / literature grounding / report).
5. **Discover environment resources** with the direct checks in
   `references/biomni_resources.md` so you use installed packages/databases instead
   of assuming or re-installing.
6. Inspect the feature table (dims, integer counts?), metadata columns and group
   sizes, and whether a tree is available. **Report the number of independent
   subjects per group, not just sample counts** — small subject counts (e.g. few
   healthy controls) drive the main caveat.

### Step 0.5 — Literature grounding (optional but recommended; feeds intro + references)

Use the **`LiteratureSearch`** tool (never model memory) to build the evidence
base the report's Introduction, interpretation, and References sections draw on.
Do this once up front so later mechanistic claims are already grounded.

- Background/context queries, e.g. `"gut microbiome dysbiosis <disease>"`,
  `"16S microbiome <disease> case control"`. Prefer human studies for clinical
  framing; use `sjr_max`/`study_types` to bias toward strong evidence.
- Mechanistic queries to support Stage-4 interpretation, run **only** for the
  axes your data actually implicates: butyrate → `"butyrate regulatory T cells
  HDAC GPR43 intestinal"`; indole/AhR → `"indole aryl hydrocarbon receptor IL-22
  intestinal barrier"`; bile acids → `"secondary bile acids gut microbiome"`.
- Method citations if asked: ANCOM-BC2, ALDEx2, MaAsLin2, PICRUSt2, PERMANOVA.

`LiteratureSearch` writes structured records (authors, journal, DOI, year) to
`/mnt/results/execution_trace/references.jsonl`. **Cite with inline `[N]`** and
carry the same references into the PDF's References section. Do not fabricate
citations; if a claim isn't supported by a retrieved paper, mark it as
hypothesis or omit it. For current, non-paper context (e.g. a database version),
`WebSearch`/`WebFetch` are the right tools instead.

### Step 1 — Community diversity (optional)

Run `da_diversity.R` (alpha + beta portions). Defaults:
- **Alpha**: rarefy (default depth 10,000), compute Observed / Shannon /
  InvSimpson (+ Faith's PD if a tree is attached). Test with a **linear mixed
  model** (subject random effect) when repeated measures; add subject-mean
  Wilcoxon as a sensitivity check. BH-correct across metrics.
- **Beta**: Bray-Curtis (+ unweighted/weighted UniFrac if a tree is attached);
  **PERMANOVA** (`adonis2`) with **subject-blocked permutation**, plus a
  one-sample-per-subject baseline for robustness.
- The R path uses phyloseq/vegan. If you prefer Python for ordination/diversity,
  `scikit-bio` gives the same metrics (`skbio.diversity.alpha_diversity`,
  `beta_diversity`, `pcoa`) and `permanova`; results should agree — pick one and
  state it.
- Interpretation cue: if presence/absence distances (unweighted UniFrac, Observed
  richness) separate groups more than abundance-weighted ones, the signal is
  **loss of rare taxa** rather than dominance shifts.

### Step 2 — Differential abundance of taxa (optional)

Run the DA portion of `da_diversity.R`. **Three-method consensus**:
- **ANCOM-BC2** (primary; subject random effect; covariate-adjusted),
- **ALDEx2** (CLR; Welch + Wilcoxon),
- **MaAsLin2** (TSS/log linear model; covariates + subject).
- Prevalence filter (default ≥10%). A genus is a **consensus hit** when flagged
  significant by **≥2 methods with concordant direction**. Report LFC/effect/coef
  side by side. BH-FDR, q<0.05 default (offer 0.01 / 0.1).
- Visualize with ggplot2 (LFC forest/bar); for a taxa×sample abundance heatmap,
  R `ComplexHeatmap` is installed.

### Step 3 — PICRUSt2 predicted function (optional; compute-heavy)

See `references/commands_and_environment.md` for the exact recipe.
- Install PICRUSt2 in its own env (`picrust2=2.5.2`).
- **Run WITHOUT `--stratified`** (the stratified flag OOM-killed a 32 GB machine
  in prior use). Command:
  `micromamba run -n picrust2 picrust2_pipeline.py -s <seqs.fasta> -i <table.tsv> -o picrust2_out -p 4 --max_nsti 2.0 --verbose`
- **Provision ≥32 GB** (64 GB for large ASV counts) via `ManageMachine` before
  this step; placement peaked ~14 GB on ~7.5k ASVs. Memory scales with ASV count.
- **Report mean weighted NSTI** as a QC metric (<0.15 = reliable; a prior run hit 0.073).
- Back up PICRUSt2 outputs to `/mnt/shared-workspace` immediately.
- PICRUSt2 outputs **EC** numbers by default (`EC_metagenome_out/...`); that is the
  license-clean feature space this skill uses. (KO output is optional and only for
  academic users covered by KEGG's terms — see `DATA_SOURCES.md`; not the default.)
- Functional DA: run `functional_da.R` (ALDEx2) on the **EC** unstratified table.
  **Expect a very large number of significant gene families** — they reflect
  correlated, community-wide taxonomic shifts, not many independent changes.
  Emphasize curated gene-set **direction** (Step 4) over raw counts.
- Optional: EC/pathway gene-set enrichment on the ranked functional table with
  `gseapy` (preinstalled) — treat as descriptive, given the correlated-shift caveat.

### Step 4 — Inferred microbial metabolites (optional; the distinctive core)

Run `metabolite_modules.py`. Read `references/metabolite_modules.md` first. Modules
are keyed on **EC numbers** (PICRUSt2's default output), not KEGG/KO. **Before
trusting any EC→reaction mapping, verify it against a free enzyme database**
(ExplorEnz / IUBMB Enzyme Nomenclature / Rhea) — the bundled CSV was built this
way; refresh it the same way if you extend the modules. **Do not use KEGG** (not
licensed for commercial use; see `DATA_SOURCES.md`). Non-negotiable correctness
rules (all baked into the script):

1. **Split butyrate into two routes.** `but` (EC:2.8.3.8/2.8.3.9, health-associated
   CoA-transferase) vs `buk` (EC:2.7.2.7 + EC:2.3.1.19, dysbiosis-associated
   kinase). The aggregate can look flat while the routes move in **opposite**
   directions (a "route switch"). Report the routes separately; the aggregate only
   alongside.
2. **The bai (secondary bile acid) module is GATED OFF by default, and the
   promiscuous EC:1.3.1.114 is excluded.** Two reasons the operon can't be trusted
   from 16S: only one of its enzymes (baiH, EC:1.3.1.116) has a clean EC while the
   rest have none, and 16S/PICRUSt2 cannot resolve this low-abundance operon.
   Additionally, the promiscuous 3-dehydro-bile-acid reductase (EC:1.3.1.114,
   formerly KO K07007) is ~360× more abundant than the next bai enzyme and, if
   present, produces a **spurious** secondary-bile-acid "depletion" — so it is
   excluded from the module. **Do not report secondary bile-acid depletion from 16S
   prediction.** Enable the module (`INCLUDE_BAI=True`) only with explicit
   low-confidence caveats.
3. **Single-enzyme domination audit.** The script prints the top-EC fraction for
   each multi-EC module. If one EC dominates (e.g. acetate kinase EC:2.7.2.1, shared
   between the acetate and propionate modules), temper the claim and note it.
4. **Test method**: subject-mean Wilcoxon (repeated measures) with BH correction
   (routes corrected within their own family).
5. **Framing**: predicted potential, not measured metabolites → **hypothesis-
   generating**. For gut studies, connect reduced but-route butyrate to weaker
   Treg induction (HDAC inhibition; GPR43/GPR109A) and impaired barrier as
   mechanistic context — and **cite the supporting literature from Step 0.5**
   (`LiteratureSearch`), not memory.

**Taxon → metabolite attribution** (since we don't use stratified PICRUSt2):
cross-reference the DA results (Step 2) with known metabolite-producing genera
(e.g. butyrate producers: Faecalibacterium, Roseburia, Gemmiger, Coprococcus,
Agathobacter, Anaerobutyricum, Anaerostipes). Directional consistency between a
depleted functional route and coordinated depletion of its producer genera is a
strong triangulated signal even when individual genus tests miss significance.

### Step 5 — Literature-grounded PDF report with infographic (optional)

**Delegate branding and layout to the `pdf-report-generation` skill** — load it
and follow its Phylo palette, typography, header/footer, table, callout, and
validation conventions. `scripts/build_report.py` is a ready-made implementation
of exactly those patterns (same colors/fonts/margins), so you can either import
it or hand-build per that skill. Either way, produce the **full required
structure below**, in order:

1. **Infographic summary page** — a one-page visual synopsis generated with the
   `GenerateImage` tool (schematic/infographic, NOT a matplotlib plot). Prompt it
   with the concrete study result: the two groups and n (subjects, not just
   samples), the headline diversity/DA finding, the number of consensus DA taxa
   with direction, and the metabolite-route story (e.g. but-route down / buk-route
   up). Keep labels factual and derived from the actual results — never invent
   numbers. Save as PNG and embed as the report's opening figure (or a facing
   summary page). `build_report.py` has an `infographic()` helper that embeds a
   full-width image with a caption.
2. **Introduction / Background** — 1–2 short paragraphs framing the disease/
   question and why the microbiome matters, **grounded in `LiteratureSearch`
   results with inline `[N]` citations** (Step 0.5).
3. **Methods** — inputs, tools, and key parameters, **including a cohort table
   with subject counts per group** (not just samples) and the software/DB
   versions used.
4. **Results** — the analytical core: diversity, beta, DA (+ figure), composition
   (+ figures), function + metabolites (+ figure), taxon→metabolite link. Tables
   and captioned figures.
5. **Conclusions / Discussion** — what the results mean biologically (host-immune
   interpretation), with citations for mechanistic claims.
6. **Figures** — every figure captioned and co-located with its caption (see
   layout lessons). Data plots come from Stages 1–4; the infographic from
   `GenerateImage`.
7. **References** — the papers retrieved via `LiteratureSearch`, rendered as a
   numbered list matching the inline `[N]` markers. `build_report.py` has a
   `references_block()` helper.
8. **Next steps** — concrete follow-ups (e.g. shotgun metagenomics or targeted
   metabolomics to confirm predicted functions; validation of specific taxa;
   larger control arm). `build_report.py` has a `nextsteps_block()` helper.

Layout lessons baked into `build_report.py` (and consistent with
`pdf-report-generation`):
- `figel()` **preserves image aspect ratio** and wraps image+caption in
  `KeepTogether` so captions never orphan and figures never distort.
- **Build the story fresh each run** — reportlab consumes flowables during
  `build()`; calling it twice on the same list yields a 0-byte PDF.
- After building, run the `Read` media-output-check on the PDF (or key pages,
  especially the infographic page) to confirm figures render, captions align,
  and no page is broken — regenerate if not. This is the validation step the
  `pdf-report-generation` skill requires.

## Figure conventions

- Data plots: seaborn/matplotlib (Python) or ggplot2 (R). Fonts
  "Liberation Sans"/"Arimo"/"DejaVu Sans"; `svg.fonttype='none'`; export PNG+SVG.
- Colorblind-safe palette: two groups → control `#75A025` (green) vs case
  `#FF9400` (orange); up/down/n.s. → `#FF9400` / `#0279EE` / `#BBBBBB`.
- The **infographic** is the one figure made with `GenerateImage` (schematic),
  not a data plot — everything else is plotted from the data.
- Media-output-check every figure; regenerate if blank/clipped/unreadable.

## Scientific caveats (state these in outputs)

- **Predicted ≠ measured.** PICRUSt2 = genomic potential; rare functions (bai)
  predicted poorly. All function/metabolite results are hypothesis-generating.
- **Repeated measures.** Never treat multiple samples per subject as independent.
- **Small n.** Report independent subject counts per group; flag underpowered arms.
- **Effect sizes.** Stool case-control PERMANOVA R² is typically small (~1–3%);
  significant ≠ large.
- **Reference consistency.** Keep the taxonomy reference (e.g. SILVA v138.2), the
  PICRUSt2 version, and the enzyme-database source/date behind the EC modules
  consistent and stated. (Functional analysis uses free EC numbers, not KEGG.)
- **EC:1.3.1.114 / route-split.** The two most common ways to get a wrong
  metabolite conclusion (a promiscuous enzyme faking bile-acid depletion, and a
  masked butyrate route switch); both are guarded in the bundled script and reference.
- **Citations.** Ground background and mechanism in `LiteratureSearch` results
  with inline `[N]`; do not cite from memory.
