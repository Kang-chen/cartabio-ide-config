---
id: "skill_b6b5b3b7da495f7833229bedd699bc7a"
name: "rnaseq-fastq-to-counts"
description: "Use to turn raw bulk RNA-seq FASTQ or SRA/ENA/GEO accessions into a DE-ready gene-count matrix with STAR or Salmon/tximport. Covers read QC, reference indexing, strandedness, per-sample and merged counts, and local or HPC execution."
category: "transcriptomics"
visibility: "public"
starting-prompt: "Align this bulk RNA-seq FASTQ sample and give me a DE-ready gene count matrix with a PDF report."
---

# Bulk RNA-seq: FASTQ → DE-ready count matrix

## Scope

Take raw bulk RNA-seq reads (uploaded FASTQ or a public accession) all the way to a
**DE-ready integer gene × sample count matrix**, with QC, strandedness auto-detection,
gene metadata, a DESeq2 load check, and a Phylo PDF report (infographic + intro/methods/
results/conclusions/figures/references/next-steps). It does **NOT** run the differential-
expression contrast itself — that is the downstream `bulk-rnaseq-counts-to-de-deseq2` /
`bulk-rnaseq-differential-expression` skills. Not for single-cell (use STARsolo/CellBender),
de novo transcriptome assembly (Trinity), or fusion calling (STAR-Fusion).

This skill is the **upstream bridge** in the pipeline:
`omics-dataset-retrieval` (find data) → **[this skill: FASTQ → counts]** → DESeq2 DE skills → enrichment.

## When to use

Trigger on any of: uploaded `.fastq`/`.fastq.gz`; a run accession (`SRR…`, `ERR…`, `DRR…`)
or GEO id (`GSM…`, `GSE…`); phrases like "align/map/quantify RNA-seq", "build a count
matrix", "ReadsPerGene", "gene counts", "STAR", "salmon", "detect strandedness", or
"counts ready for DESeq2/edgeR". If the user only has a count matrix already, skip to the
DESeq2 skill instead.

## Inputs

| Input | Notes |
|---|---|
| Reads | Paired-end (`_R1/_R2` or `_1/_2`) or single-end `.fastq.gz`; **or** a public accession (resolve to FASTQ via ENA; a GEO `GSM/GSE` → SRR runs). |
| Reference (STAR) | Genome FASTA + matching GTF. **Confirm organism + build + release with the user.** Default: Ensembl **GRCh38** (latest release) — but ask before building. |
| Transcriptome (salmon) | Transcript FASTA (+ genome as decoy, recommended) and a `tx2gene` map from the same GTF. |
| Fast/CI mode (optional) | Target chromosome (e.g. `22`/`chr22`) + read-subset size (e.g. 4M pairs) to keep runtime/compute small for validation/demos. |

## Outputs (saved under `/mnt/results/<run>/`)

- `counts_matrix.tsv` — **integer gene × sample matrix** (DESeq2/edgeR input; STAR special rows excluded).
- `gene_metadata.csv` — gene_id → name, biotype, chrom/start/end/strand.
- `strandedness.json` — empirical strandedness call + forward/reverse evidence.
- `assignment_summary.csv` — assigned / no_feature / ambiguous / multimapping / unmapped.
- `star/` — `Log.final.out`, `ReadsPerGene.out.tab`, `SJ.out.tab` (STAR path); or salmon `quant.sf` + `lib_format_counts.json`.
- `qc/` — FastQC HTML.
- `figures/` — 4 figure groups (PNG + editable SVG).
- `report_<run>.pdf` — Phylo-branded report with infographic.
- `README.md` — downstream DESeq2 load snippet + scope note.

## Environment & resource discovery (do this first)

1. Check local tool availability with Bash (`command -v STAR salmon samtools fastqc seqkit`)
   and verify required R packages with `Rscript`. Use `hpc_search_tools("STAR")` /
   `hpc_search_tools("salmon")` to confirm the HPC signatures.
2. **Pick an execution path:**
   - **Local (default, self-contained):** conda/mamba STAR + salmon on a chromosome-subset
     reference. Best for validation, demos, and single small samples. Size a worker ~8 CPU / 32 GB.
     Full-genome STAR indexing needs ~30+ GB RAM — only do full-genome locally on a big worker.
   - **HPC (production):** full-genome STAR via `hpc_run_tool("star", …)` with the prebuilt index
     `--genomeDir /mnt/fsx/dbs/star/GRCh38_v2.7.11b`; salmon via `hpc_run_tool("salmon", …)`.
     Use for real analyses, full genomes, and many samples.
3. Install local tools if needed: `conda install -c bioconda star salmon seqkit samtools fastqc`
   (or confirm they are already present).

## Workflow

### 1. Resolve & validate inputs
- If given an accession, fetch FASTQ from ENA (fast, no `prefetch`):
  `https://ftp.sra.ebi.ac.uk/vol1/fastq/<SRR[0:6]>/00<last digit>/<SRR>/<SRR>_{1,2}.fastq.gz`
  (query `https://www.ebi.ac.uk/ena/portal/api/filereport?accession=<SRR>&result=read_run&fields=fastq_ftp,library_layout,read_count`
  to get exact URLs, layout, and read count). A GEO `GSM/GSE` → map to SRR runs first.
- Verify gzip integrity (`gzip -t`), detect layout (paired vs single), and read length
  (`zcat R1 | head -2 | tail -1 | wc -c`, minus 1). Read length sets `--sjdbOverhang`.
- **Confirm organism, genome build, and annotation release with the user** before downloading a reference.

### 2. Acquire reference & enforce naming consistency
- Download FASTA + GTF for the confirmed build (Ensembl or GENCODE).
- **CRITICAL:** FASTA seqnames and GTF seqnames must match exactly (Ensembl `22` vs UCSC `chr22`)
  and be the same build (GRCh38 ≠ GRCh37). A mismatch silently yields **zero counts**. Verify:
  compare `grep '^>' fasta | head` against `cut -f1 gtf | sort -u | head`.
- For subset mode, subset the GTF to the target chromosome:
  `zcat ann.gtf.gz | awk -F'\t' '$1=="22" || /^#/' > chr22.gtf` (match the FASTA's seqname).

### 3. Read QC (FastQC)
- `fastqc reads_R1.fastq.gz reads_R2.fastq.gz -o qc/`. Read length drives `--sjdbOverhang = len − 1`.
  Note adapter content / overrepresentation; trim (Trimmomatic) only if clearly needed.

### 4. (Fast/CI mode) subset reads and/or reference
- Subset reads with seqkit **sequentially** (not with shell `&`, which corrupts timing/pairing):
  `seqkit head -n <N> R1.fastq.gz -o sub_R1.fastq.gz` then the same for R2 (same N).
- Compute the small-genome SA index size:
  `genomeSAindexNbases = min(14, floor(log2(genomeLength)/2 − 1))` (chr22 → 11; full genome → 14).

### 5a. STAR path — index, align, quantify
- Build index (local subset):
  ```
  STAR --runMode genomeGenerate --genomeDir star_index \
    --genomeFastaFiles chr.fa --sjdbGTFfile chr.gtf \
    --sjdbOverhang <readlen-1> --genomeSAindexNbases <n> --runThreadN 8
  ```
- Align + count:
  ```
  STAR --genomeDir star_index --readFilesIn sub_R1.fastq.gz sub_R2.fastq.gz \
    --readFilesCommand zcat --runThreadN 8 --quantMode GeneCounts \
    --outSAMtype BAM SortedByCoordinate --outFileNamePrefix star/<sample>_
  ```
  (HPC full genome: swap `--genomeDir /mnt/fsx/dbs/star/GRCh38_v2.7.11b` and run via `hpc_run_tool`.)
- Validate BAM: `samtools quickcheck …_Aligned.sortedByCoord.out.bam && samtools flagstat …`.
- **Runtime note:** STAR is slow when most reads are off-target (subset mode). Aligning a fixed
  read head-subset (e.g. 4M pairs) to a chr index runs in ~30 min on 8 CPU; run long alignments as a
  background job.

### 5b. salmon path (optional, fast) — index, quant, tximport
- Build a decoy-aware index (recommended): make `gentrome.fa` (transcripts + genome) and `decoys.txt`,
  then `salmon index -t gentrome.fa -i salmon_idx --decoys decoys.txt -k 31 -p 8`.
- Quantify with auto library-type detection:
  `salmon quant -i salmon_idx -l A -1 R1.fq.gz -2 R2.fq.gz --validateMappings -p 8 -o salmon/<sample>`
  (add `--gcBias` on real data). `-l A` records the inferred library type in `lib_format_counts.json`.
- Aggregate to gene level: build `tx2gene` from the GTF, then
  `Rscript scripts/tximport_salmon_to_counts.R --quant-dirs <name>=salmon/<sample>/quant.sf --tx2gene tx2gene.tsv --outdir /mnt/results/<run>`.
- **Do not mix STAR and salmon counts in one matrix** (union-exon vs transcript-EM).

### 6. Strandedness auto-detection & matrix
- Run the packaged builder (STAR path):
  ```
  python scripts/detect_strandedness_build_matrix.py \
    --reads-per-gene star/<sample>_ReadsPerGene.out.tab \
    --gtf chr.gtf --sample-name <sample> --outdir /mnt/results/<run>
  ```
  It picks the strand column (frac_fwd ≥0.8 → forward, ≤0.2 → reverse, else unstranded), writes
  `counts_matrix.tsv`, `strandedness.json`, `assignment_summary.csv`, `gene_metadata.csv`.
  For multiple samples pass repeated `--reads-per-gene` (or `--samples S1=…,S2=…`) → one merged matrix.
- For salmon, trust the `-l A` inferred library type (report it) — no column selection needed.

### 7. DE-readiness check
- `Rscript scripts/deseq_smoketest.R --counts /mnt/results/<run>/counts_matrix.tsv`.
  Asserts the matrix loads into `DESeqDataSetFromMatrix`, is integer, non-negative, no NA/dupes.
  (A single sample can't be DE-tested — this only proves the matrix is a valid input.)

### 8. Figures + PDF report
- Figures: `python scripts/make_figures.py --log-final … --reads-per-gene … --strandedness … --assignment … --gene-metadata … --fastqc-r1 qc/<sample>_R1_fastqc.zip --sample-name <sample> --outdir /mnt/results/<run>`
  → `fig1_qc`, `fig2_alignment`, `fig3_counts`, `fig4_assignment` (PNG + SVG).
- **Infographic:** use `GenerateImage` (load via ToolSearch) to draw a clean schematic of the workflow
  (FASTQ → QC → align → quantify → matrix → DE) annotated with this run's headline metrics.
  Do NOT hand-draw this with matplotlib.
- **References:** use `LiteratureSearch` to pull canonical method papers (STAR = Dobin 2013;
  salmon = Patro 2017; tximport = Soneson 2015; DESeq2 = Love 2014; dataset paper if applicable).
  Verify each citation from the returned record before putting it in the report.
- **Report:** follow the `pdf-report-generation` skill for branding. Write the run's numbers into a
  `metrics.json` and call:
  `python scripts/make_report.py --metrics metrics.json --fig-qc … --fig-align … --fig-counts … --fig-assign … --infographic … --title "…" --out /mnt/results/report_<run>.pdf`.
- **Mandatory:** run a `Read(mode="media_output_check")` on every figure PNG and the final PDF;
  regenerate if blank/clipped/low-quality.

### 9. Hand-off README
- Write `README.md` with the exact DESeq2 load snippet (below) and the scope note, so the user can
  move straight to the DE skill.

## Downstream hand-off (put in README.md)
```r
library(DESeq2)
counts  <- as.matrix(read.delim("counts_matrix.tsv", row.names = 1, check.names = FALSE))
colData <- data.frame(row.names = colnames(counts), condition = factor(c(...)))  # >=2 reps/group
dds <- DESeqDataSetFromMatrix(countData = counts, colData = colData, design = ~ condition)
dds <- DESeq(dds); res <- results(dds)
```

## Scientific caveats

- **Subset mode is for validation, not biology.** Aligning to one chromosome makes ~95% of reads
  correctly go unmapped-too-short (e.g. chr22 ≈ 94.6%). Never interpret subset counts as expression.
- **Genome-build & seqname mismatch is the #1 silent failure** — always verify FASTA vs GTF seqnames
  and build before indexing (Ensembl `22` vs UCSC `chr22`; GRCh37 vs GRCh38).
- **Strandedness needs enough reads** — with few gene-assigned reads the call is unreliable; STAR and
  featureCounts both mis-count if the wrong strand column/setting is used.
- **STAR vs salmon counts differ** (union-exon vs transcript-level EM); never combine them.
- **Real DE needs replicates** — a single sample gives a valid matrix but no differential test; defer
  the contrast, dispersion, and FDR to the DESeq2 skill (use adjusted p-values there).
- **Licensing:** Ensembl, GENCODE, and ENA are open — no access constraints for the default references.

## Test prompts (representative)

1. *Simple:* "Align this airway RNA-seq sample SRR1039508 to chr22 and give me a DESeq2-ready count matrix with a PDF report." → local STAR subset path, full deliverables.
2. *Edge / multi-sample:* "I have 4 paired-end FASTQ samples (2 control, 2 treated). Quantify them with salmon and build one gene count matrix ready for DESeq2." → salmon + tximport, merged matrix, library-type reported.
3. *Could-go-wrong scientifically:* "Build a STAR count matrix for this mouse RNA-seq using GENCODE — my last run gave all-zero counts." → confirm GRCm39 build, enforce FASTA/GTF seqname consistency, catch the build/naming mismatch.

## Canonical method references (verified DOIs — use in the report)
Pull these with `LiteratureSearch` and cite the ones actually used in the run; do not invent
DOIs. Verified:
- **STAR** — Dobin A, et al. STAR: ultrafast universal RNA-seq aligner. *Bioinformatics* 2013;29(1):15–21. doi:10.1093/bioinformatics/bts635
- **Salmon** — Patro R, et al. Salmon provides fast and bias-aware quantification of transcript expression. *Nat Methods* 2017;14(4):417–419. doi:10.1038/nmeth.4197
- **tximport** — Soneson C, Love MI, Robinson MD. Differential analyses for RNA-seq: transcript-level estimates improve gene-level inferences. *F1000Research* 2015;4:1521. doi:10.12688/f1000research.7563.2
- **DESeq2** — Love MI, Huber W, Anders S. Moderated estimation of fold change and dispersion for RNA-seq data with DESeq2. *Genome Biol* 2014;15(12):550. doi:10.1186/s13059-014-0550-8
- **Worked-example dataset (SRP033351/GSE52778)** — Himes BE, et al. RNA-Seq transcriptome profiling identifies CRISPLD2 as a glucocorticoid responsive gene… *PLoS ONE* 2014;9(6):e99625. doi:10.1371/journal.pone.0099625

## Packaged scripts
- `scripts/detect_strandedness_build_matrix.py` — STAR ReadsPerGene → strandedness call + matrix + metadata.
- `scripts/tximport_salmon_to_counts.R` — salmon quant → gene-level counts via tximport.
- `scripts/make_figures.py` — the 4 figure groups.
- `scripts/make_report.py` — Phylo-branded PDF (data-driven via metrics.json).
- `scripts/deseq_smoketest.R` — DE-readiness assertion.
- `references/parameters.md` — parameter rules (sjdbOverhang, genomeSAindexNbases, library type, decoys).
- `references/worked_example.md` — grounded SRR1039508/chr22 oracle with expected numbers.
