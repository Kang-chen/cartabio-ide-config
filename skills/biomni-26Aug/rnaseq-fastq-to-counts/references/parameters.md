# Parameter reference — bulk RNA-seq FASTQ → counts

Rules of thumb and rationale for the parameters that most often get set wrong.
All values here are grounded in the STAR/salmon documentation and the skill's
worked example (see `worked_example.md`).

## STAR index parameters

### `--sjdbOverhang`
- **Rule:** `read_length − 1`.
- **Why:** it sizes the splice-junction database sequence flanks; the ideal is
  `max(readlen) − 1`. STAR's default (100) works for 101 bp reads but is
  suboptimal for shorter reads.
- **Worked example:** 63 bp reads → `--sjdbOverhang 62`.
- Get read length from FastQC ("Sequence length") or
  `zcat reads_R1.fastq.gz | head -2 | tail -1 | wc -c` (subtract 1 for newline).

### `--genomeSAindexNbases`
- **Rule (small genomes / single chromosome):** `min(14, floor(log2(genomeLength)/2 − 1))`.
- **Why:** the default (14) allocates a suffix-array index sized for a full
  ~3 Gb human genome. For a single chromosome this over-allocates and STAR
  explicitly warns; too-large values waste memory and can degrade mapping.
- **Worked example:** chr22 length 50,818,468 bp → `min(14, floor(log2(50818468)/2 − 1))` = **11**.
- Full genome: leave at default 14.
- One-liner: `python3 -c "import math;L=<len>;print(min(14,int(math.log2(L)/2-1)))"`

### `--sjdbGTFfile`
- Always pass the GTF at index time for annotation-aware junctions and
  `--quantMode GeneCounts`. **The GTF and FASTA must use identical seqnames**
  (Ensembl `22` vs UCSC `chr22`) and the **same build** (GRCh38 ≠ GRCh37). This
  is the #1 silent failure — a mismatch yields zero counts with no error.

## STAR alignment parameters

- `--quantMode GeneCounts` → emits `ReadsPerGene.out.tab` (the per-gene counts).
- `--outSAMtype BAM SortedByCoordinate` → indexed BAM for QC/IGV.
- `--readFilesCommand zcat` → for gzipped FASTQ.
- `--runThreadN` → match worker CPUs (≈8).
- `--twopassMode Basic` (optional) → better novel-junction sensitivity; ~2× runtime.
- Validate output: `samtools quickcheck out.bam && samtools flagstat out.bam`.

### HPC (full genome, production)
Prebuilt index: `--genomeDir /mnt/fsx/dbs/star/GRCh38_v2.7.11b` (no local build).
Run via `hpc_run_tool("star", command, input_files={...})`.

## Strandedness (STAR ReadsPerGene.out.tab)

Columns: `gene_id | unstranded(col2) | forward(col3) | reverse(col4)`.
Exclude the 4 special rows (`N_unmapped, N_multimapping, N_noFeature, N_ambiguous`).

| frac_fwd = Σcol3 / (Σcol3+Σcol4) | Call | Column used |
|---|---|---|
| ≥ 0.80 | forward (fr-secondstrand) | col3 |
| ≤ 0.20 | reverse (fr-firststrand, dUTP) | col4 |
| otherwise | unstranded | col2 |

Most modern Illumina kits (dUTP: TruSeq Stranded, NEBNext Ultra Directional) are
**reverse**. Older/unstranded → ~0.50. Worked example: frac_fwd 0.4979 → unstranded.

## salmon parameters

- `salmon index -t transcriptome.fa -i idx -k 31` — k=31 good for ≥75 bp reads;
  drop k for shorter reads.
- **Decoy-aware index (recommended):** concatenate genome as decoy
  (`gentrome.fa` + `decoys.txt`) so genomic multimappers aren't miscounted.
- `salmon quant -l A --validateMappings` — `-l A` **auto-detects library type**
  (ISR = reverse-stranded, ISF = forward, IU/U = unstranded), recorded in
  `lib_format_counts.json` (`expected_format`).
- Add `--gcBias` (and `--seqBias`) for bias correction on real data.
- Aggregate to gene level with **tximport** + a `tx2gene` map built from the same
  GTF; import with `countsFromAbundance="lengthScaledTPM"` for a DESeq2-ready matrix.

## Aligner choice

| Aligner | Output | Use when |
|---|---|---|
| STAR | spliced BAM + gene counts | need a genome BAM (QC/IGV/variant/fusion), splice-junction detail, ENCODE-style counts |
| salmon | transcript quant (→ gene via tximport) | speed, transcript-level estimates, no BAM needed; large sample counts |
| HISAT2 | spliced BAM (low memory) | memory-constrained genome alignment (pair with featureCounts) |

STAR and salmon counts are **not interchangeable** (union-exon vs transcript-EM);
never mix them in one matrix.
