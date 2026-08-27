# Worked example (smoke-test oracle)

A fully-grounded run used to (a) show the end-to-end workflow and (b) serve as the
numeric oracle for validating the skill's scripts. All numbers below were produced
by an actual STAR run and verified against the output files, not estimated.

## Input
- **Sample:** `SRR1039508` — study **SRP033351 / GSE52778**
  (Himes BE et al. *PLoS ONE* 2014, doi:10.1371/journal.pone.0099625),
  untreated human airway smooth muscle cells, donor N61311.
- **Layout:** paired-end, **63 bp** reads (22,935,521 pairs total on ENA).
- **Reference:** Ensembl **GRCh38 release-112**, chromosome **22** only.
  - FASTA seqname `>22` matches GTF seqname `22` (naming-consistency check passed).
  - chr22 length **50,818,468 bp**; **1,454** annotated genes.

## Configuration (subset mode for speed)
- Read subset: first **4,000,000** read pairs (`seqkit head`).
- Index: `--sjdbOverhang 62` (63 bp − 1), `--genomeSAindexNbases 11`
  (`min(14, floor(log2(50818468)/2 − 1))`).
- Align: `--quantMode GeneCounts --outSAMtype BAM SortedByCoordinate`.
- Tools: STAR 2.7.11b, samtools 1.22.1, FastQC 0.12.1, seqkit 2.13.0,
  R 4.4.2 + DESeq2 1.46.0.

## Expected outputs (the oracle)
STAR `Log.final.out`:
| Metric | Value |
|---|---|
| Input reads | 4,000,000 |
| Uniquely mapped | 196,199 (4.90%) |
| Multi-mapped | 19,804 (0.50%) |
| Too many loci | 994 (0.02%) |
| Unmapped: too short | 3,782,553 (94.56%) |
| Mismatch rate/base | 2.32% |
| Avg mapped length | 123.28 |
| Splices total | 65,463 (58,411 annotated; 63,364 GT/AG) |
| Chimeric | 0 |

Strandedness (`strandedness.json`):
- sum unstranded = 151,334; forward = 77,743; reverse = 78,401
- fraction_forward = **0.4979** → **unstranded**

Matrix (`counts_matrix.tsv`):
- 1,454 gene rows; **682 detected** (>0); sum of assigned counts = **151,334**
- Top expressed genes:

| gene_id | gene | count |
|---|---|---|
| ENSG00000077942 | FBLN1 | 28,714 |
| ENSG00000100234 | TIMP3 | 9,603 |
| ENSG00000100316 | RPL3 | 7,240 |
| ENSG00000100345 | MYH9 | 6,328 |

Assignment (`assignment_summary.csv`): assigned 151,334; no_feature 31,845;
ambiguous 13,020; multimapping 19,804; unmapped 3,783,997.

## Why ~95% unmapped is EXPECTED here
Reads come from the whole transcriptome but the index is chr22 only (~1.6% of the
genome), so ~95% of reads correctly fail to map ("unmapped: too short"). This is a
feature of subset mode, **not** a QC failure. On a full-genome index the same
sample maps at the usual ~90%+. Subset mode exists to validate the pipeline and
keep runtime/compute small — never to draw biological conclusions.

## Validating the scripts against this oracle
`detect_strandedness_build_matrix.py` run on this sample's
`SRR1039508_ReadsPerGene.out.tab` must reproduce: protocol=unstranded,
fraction_forward=0.4979, 682 detected genes, assigned sum=151,334, and top gene
ENSG00000077942 = 28,714.

## Generic usage (any accession + chromosome)
Swap the accession (`SRR/ERR/DRR` from ENA, or resolve a `GSM/GSE`), organism,
build, and target chromosome. For real analysis, use the full genome (HPC STAR
prebuilt index) across all samples and merge per-sample columns before DE.
