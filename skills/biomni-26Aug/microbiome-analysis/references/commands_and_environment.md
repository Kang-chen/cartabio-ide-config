# Environment setup, commands, and machine sizing

Tested recipes from a real 16S run (7,534 ASVs × 611 samples). Treat versions and
thread counts as adaptable defaults, but keep the **OOM-safe PICRUSt2 invocation**
and the **machine sizing** guidance — both were learned the hard way.

## First: use what Biomni already installs

Before installing anything, check `references/biomni_resources.md` and verify
packages directly with imports or `requireNamespace()`. Biomni already ships many
of the packages this skill uses, so **do not reinstall them**. (Note: this skill
uses **EC** predictions, not KEGG/KO, for license reasons — see `DATA_SOURCES.md`.)

- **Python (preinstalled):** `scikit-bio`, `biom-format`, `gseapy`, `pandas`,
  `numpy`, `scipy`, `statsmodels`, `matplotlib`, `seaborn`, `reportlab`, `pypdf`,
  `pillow`, `scikit-learn`.
- **R (preinstalled):** `ggplot2`, `ggprism`, `ggrepel`, `ComplexHeatmap`,
  `clusterProfiler`, `dplyr`, `tidyr`, `tibble`, `readr`, `RColorBrewer`.

Only two things genuinely need installing: **PICRUSt2** (its own env) and the
**Bioconductor microbiome-stats stack** (ALDEx2, phyloseq, microbiome, MaAsLin2,
ANCOMBC), which are not part of the default R set. Recipes below.

## Install recipes (micromamba)

```bash
# PICRUSt2 (its own env — pins are important; 2.5.2 is tested)
micromamba create -y -n picrust2 -c bioconda -c conda-forge picrust2=2.5.2

# R statistics stack (into base or a dedicated env)
micromamba install -y -n base -c bioconda -c conda-forge \
  bioconductor-aldex2 bioconductor-phyloseq bioconductor-microbiome \
  bioconductor-maaslin2 bioconductor-ancombc
# ANCOM-BC2 lives in the `ANCOMBC` Bioconductor package (function ancombc2()).
# Base R usually already has: ggplot2, dplyr, tidyr, svglite, scales, data.table, stringr, lme4.

# Python (metabolite modules + PDF): these are usually PREINSTALLED in Biomni.
# Only run this if a direct import shows one is missing. Use uv, not plain pip.
uv pip install pandas numpy scipy statsmodels reportlab pypdf pillow scikit-bio biom-format
```

## Verifying metabolite modules against free enzyme databases (EC)

The metabolite module enzyme sets (`references/metabolite_modules_ec.csv`) are keyed on
**EC numbers** (IUBMB Enzyme Commission), a free, open nomenclature. **KEGG is not used**
here because it is not licensed for commercial use (see `DATA_SOURCES.md`). To verify or
extend a module, confirm each EC's reaction/biology against a **license-clean** source —
ExplorEnz (the official IUBMB Enzyme List), the IUBMB Enzyme Nomenclature site, or Rhea:

```bash
# EC -> accepted name / reaction (ExplorEnz, official IUBMB Enzyme List)
curl -s "https://www.enzyme-database.org/query.php?ec=2.8.3.8"     # butyryl-CoA:acetate CoA-transferase
# EC -> curated reactions (Rhea; EBI, CC BY 4.0)
curl -s "https://www.rhea-db.org/rhea?query=ec:4.1.99.1&format=tsv" # tryptophanase -> indole
```

Never substitute EC numbers from memory — confirm against a free enzyme database, and
record the source/date so the modules stay reproducible. Note: the promiscuous
**EC:1.3.1.114** stays EXCLUDED from the bai module regardless of source (see
`metabolite_modules.md`). Do **not** ship or query MetaCyc/BioCyc content: MetaCyc became
subscription-only (2024) and is not commercially clean.

## OOM-safe PICRUSt2 command (DO NOT add --stratified)

```bash
micromamba run -n picrust2 picrust2_pipeline.py \
  -s asv_seqs.fasta \
  -i feature_table.tsv \
  -o picrust2_out \
  -p 4 \
  --max_nsti 2.0 \
  --verbose
```

- **Never pass `--stratified` on a memory-constrained machine.** In this project,
  `--stratified` OOM-killed a 32 GB machine. The unstratified run is the OOM-safe
  path. Derive per-taxon metabolite attribution instead by cross-referencing the
  taxonomy table with the differential-abundance results (see `metabolite_modules.py`
  and the taxon-link step), not from stratified PICRUSt2 output.
- Input `feature_table.tsv` must be a tab-delimited table whose first column header
  is `#OTU ID` (BIOM classic TSV), samples as remaining columns. Feature IDs must
  match the FASTA sequence headers.
- Key outputs:
  - `KO_metagenome_out/pred_metagenome_unstrat.tsv.gz` (KO × samples)
  - `EC_metagenome_out/pred_metagenome_unstrat.tsv.gz` (EC × samples)
  - `pathways_out/path_abun_unstrat.tsv.gz`
  - `marker_predicted_and_nsti.tsv.gz` (per-ASV NSTI)

## NSTI QC

After the run, compute the **mean weighted NSTI** (weight each ASV's NSTI by its
total abundance). <0.15 = reliable predictions. This run achieved **0.073**.

## Machine sizing (evidence-based)

- The phylogenetic-placement stage (EPA-ng) is the memory peak. On ~7.5k ASVs ×
  611 samples it used **~22% of a 64 GB machine (~14 GB) resident**. Runtime for the
  full pipeline was on the order of tens of minutes to ~1 h.
- **Recommend ≥32 GB for typical datasets; use 64 GB for large ASV counts** or if a
  32 GB attempt OOMs. Memory scales primarily with the number of ASVs (placement),
  not sample count.
- Provision with `ManageMachine` before the PICRUSt2 stage. Run foreground if you
  expect <60 min; otherwise background it and estimate remaining time from the log
  after placement completes.
- Back up PICRUSt2 outputs to durable storage (`/mnt/shared-workspace/...`) right
  after the run — placement is the expensive step you don't want to repeat.

## R + FUSE gotcha (writing deliverables)

`file.copy()` in R produces **0-byte files** on the S3-backed `/mnt/results` mount.
Either write compatible formats (CSV/TSV/PNG/SVG/PDF) directly to `/mnt/results`, or
write to `/workspace` first and move with a shell `cp`. PDFs built with reportlab
(Python) write fine directly to `/mnt/results`.
