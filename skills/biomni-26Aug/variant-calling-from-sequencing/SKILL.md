---
id: "skill_21c4aae0e24ee315b4095239a58ceb6c"
name: "variant-calling-from-sequencing"
description: "Use to call and benchmark germline SNVs/indels from FASTQ, BAM/CRAM, or caller VCFs for WES/WGS and short- or long-read data. Covers GATK/DeepVariant, VCF normalization, caller concordance, GIAB/hap.py accuracy, coverage, Ti/Tv, and dbSNP QC."
category: "genomics_genetics"
visibility: "public"
starting-prompt: "Call germline variants from my sequencing data with GATK and DeepVariant, compare the callers, and give me a PDF report."
---

# Variant Calling from Sequencing

Take germline sequencing data from **reads (FASTQ) or alignments (BAM/CRAM) or existing per-caller VCFs** all the way to a benchmarked, QC'd, reported variant call set. The distinctive value of this skill is the **dual-caller comparison** (GATK HaplotypeCaller + DeepVariant) with rigorous, **target-aware** quality control and an explicit, non-negotiable separation between *caller concordance* (agreement) and *truth-set accuracy* (correctness).

This skill was generalized from a fully executed, verified HG002/NA24385 exome benchmark; the command recipes below are the exact patterns that worked in production, including the workarounds for known failure modes.

---

## Scope

**Does:** align short/long reads to a reference; call germline SNVs + indels with GATK HaplotypeCaller and DeepVariant; normalize and compute caller-vs-caller concordance; optionally benchmark accuracy against a GIAB truth set with hap.py; compute target-aware coverage/quality QC (on-target vs whole-region for exomes; whole-genome for WGS); generate figures and a Phylo-branded PDF report.

**Does NOT:** deep clinical annotation, pathogenicity/ACMG classification, ClinVar/gnomAD lookup, or variant prioritization → hand off to the **`genetic-variant-annotation`** skill (it starts from a VCF and owns that downstream space). Also out of scope: **somatic / tumor–normal** calling (use Strelka2/Mutect2), **copy-number and structural variants** (use dedicated SV/CNV tools; for long-read SV use `sniffles`), and joint-genotyping of large cohorts.

---

## Inputs

| Input | Formats | Notes |
|---|---|---|
| Reads | `.fastq.gz` (paired or single) | Entry point when starting from raw sequencing. |
| Alignment | `.bam` / `.cram` (+ index) | Entry point when reads are already aligned; must know the reference build used. |
| Existing calls | 1–N `.vcf.gz` (+ `.tbi`) | **Benchmark-only** entry point: skip alignment/calling, go straight to concordance/QC/report. Two VCFs from different callers is the canonical case. |
| Reference | FASTA (+ `.fai`, `.dict`) | Build **must** match the reads/BAM (GRCh38 vs GRCh37). Use a documented local reference or retrieve it from UCSC/Ensembl if not supplied. |
| Capture target BED | `.bed` (optional but strongly preferred for WES) | The exome kit's **bait/capture** intervals. If absent for an exome, fall back to a GENCODE-exon target **and warn** (see caveats). |
| Truth set | truth `.vcf.gz` + confident-region `.bed` (optional) | Only for GIAB-characterized samples (HG001–HG007). Enables hap.py accuracy benchmarking. |
| Config | sample name, assay (`WES`/`WGS`), read type (`short`/`long`), region/contig subset | Region subsetting (e.g. a single chromosome) is supported and recommended for pilots. |

---

## Outputs

Saved to `/mnt/results/` (deliverables) with intermediates on `/workspace` or `/mnt/shared-workspace`:

- **Per-caller VCFs** — normalized, PASS-filtered, bgzipped + `.tbi` (e.g. `<sample>_<region>_gatk.vcf.gz`, `..._deepvariant.vcf.gz`).
- **Concordance tables** — `concordance_counts.txt` (shared / caller-only, split by SNV/indel), `concordance_metrics.json` (Jaccard overall + by type, % of each caller's calls that are shared).
- **QC tables** — `per_caller_qc.csv` (total/SNV/indel, Ti/Tv, het/hom, dbSNP%), `on_target_qc.csv` (WES: on- vs off-target Ti/Tv, on-target depth/breadth, on-target dbSNP%).
- **Accuracy tables (optional)** — hap.py precision/recall/F1 per caller (SNV + indel), restricted to target ∩ confident regions.
- **Figures** — PNG + SVG, 300 DPI: coverage curve, depth contrast, concordance Venn + by-type, Ti/Tv & dbSNP panels, impact/consequence (if annotated), gene burden.
- **PDF report** — `report_<sample>_variant_calling.pdf` via the `pdf-report-generation` skill (infographic + intro + methods + results + conclusions + figures + references + next steps).

---

## Required Biomni integrations (use these tools explicitly)

1. **Direct resource checks** — validate reference/dbSNP/known-indel files supplied
   for the run, check installed tools (bwa, gatk, bcftools, mosdepth, bedtools,
   samtools, snpeff) with `command -v`, and check HPC callers
   (`pepper-deepvariant`, `clair3`, `strelka2`, `freebayes`) with
   `hpc_search_tools`.
2. **`LiteratureSearch`** — pull citations for the methods used (GATK HaplotypeCaller, DeepVariant, GA4GH/hap.py benchmarking, GIAB truth sets) and for any highlighted genes, so the report's References section is real (never fabricate PMIDs/DOIs).
3. **Installed-resource catalog** — this environment ships bwa, gatk, bcftools, samtools, bedtools, mosdepth, snpeff as CLI/conda tools, plus HPC callers. Prefer these over ad-hoc installs. hap.py + rtg-tools are conda-installable (no Docker).
4. **`pdf-report-generation` skill** — load it and follow its ReportLab/Platypus brand patterns to build the final PDF. Do not reinvent the report styling.

---

## Workflow

> Follow the steps in order, but **route by entry point** at Step 2. Use `TodoWrite` to track. Prefer the exact commands in the "Command reference" section — they encode fixes for real failure modes.

### Step 1 — Discover resources
Require a reference FASTA for reads/alignment entry points and verify every supplied
reference, dbSNP, and known-indel file directly. Confirm that all supplied resources
use the same build as the input (ask the user if ambiguous—a GRCh37/GRCh38 mismatch
silently produces wrong annotations and off-target everything). dbSNP and known-indel
resources are optional: when either is absent, skip BQSR and record that limitation
instead of guessing a path. Check required CLI/HPC tools as described above.

### Step 2 — Route by entry point
- **FASTQ present** → Step 3 (align), then 4/5.
- **BAM/CRAM present** → skip to Step 4/5 (calling). Confirm it is coordinate-sorted, indexed, and duplicate-marked (mark duplicates if not).
- **≥1 VCF present, no reads/BAM** → **benchmark-only**: skip to Step 6 (normalize + concordance) and Step 8 (QC on the VCFs). Coverage-based QC that needs a BAM is skipped with a note.

### Step 3 — Align (FASTQ entry)
BWA-MEM (short reads) or minimap2 (long reads) → sort → mark duplicates → BQSR
when build-matched dbSNP and known-indel resources were supplied. If either known-sites
resource is absent, use the duplicate-marked BAM for calling and state that BQSR was
skipped. Emit an analysis-ready BAM. **Do heavy BAM writes on local `/workspace`**,
not on S3-FUSE mounts (random-access writes fail there); copy the finished BAM to
shared storage.

### Step 4 — Call with GATK HaplotypeCaller (always local)
HaplotypeCaller (`-ERC GVCF`) → GenotypeGVCFs → split SNP/INDEL → hard-filter each with the standard tranches → merge. GATK runs via the installed CLI regardless of read type.

### Step 5 — Call with the deep-learning caller (branch by read type)
- **Short-read Illumina (WES/WGS default) → DeepVariant, local container.** DeepVariant is the correct short-read DL caller; the HPC cluster's DL tools are long-read pipelines and must NOT be used on Illumina. Run `google/deepvariant` via udocker with the exact env/volume workarounds below. Set `--model_type=WES` or `WGS` to match the assay.
- **Long-read (ONT/PacBio) → prefer Biomni HPC.** Use `hpc_run_tool` with `pepper-deepvariant` (or `clair3`) via `hpc_search_tools` first to get the usage string. Fall back to a local install only if HPC is unavailable.
- **Clair3 is an option, not a co-equal default** — it is long-read-first; do not reach for it on short-read exomes.

### Step 6 — Normalize + concordance (agreement, NOT accuracy)
For each caller: keep PASS, `bcftools norm -m -any -f REF` (split multiallelics + left-align — **mandatory** before comparison or two callers' representations won't match). Then `bcftools isec` → shared / GATK-only / DV-only. Compute **Jaccard = shared / union** overall and split by SNV/indel, plus % of each caller's calls that are shared. **State clearly in every output that this measures how much the callers agree, not which caller is correct.**

### Step 7 — [Optional, OFF by default] Truth-set accuracy with hap.py
Only when a truth VCF + confident-region BED are supplied (GIAB HG001–HG007). Run `hap.py` for each caller **restricted to the target BED ∩ confident regions**; report precision/recall/F1 (SNV + indel). This is the only step that measures *accuracy*. GIAB HG002 truth: AshkenazimTrio NISTv4.2.1. Do not present concordance numbers as accuracy in its place.

### Step 8 — Target-aware QC (the core scientific correction)
- **WES:** compute **on-target** depth and breadth **separately** from whole-region, using mosdepth region mode with the capture BED (`--by target.bed`). Report on-target mean depth and breadth at ≥1/10/20/30/50/100×. Split variants into on-/off-target (`bcftools view -R` / `-T ^`) and report **on-target Ti/Tv** (coding, expect ~2.8–3.2) vs off-target. **Never report a single whole-region depth average for an exome** — it conflates deeply-baited coding bases with off-bait bases and is misleading.
- **WGS:** report whole-genome mean depth + breadth; on-target steps are skipped.
- Per-caller: total/SNV/indel, Ti/Tv, het/hom ratio, and dbSNP known-rate when a
  build-matched dbSNP resource was supplied. **dbSNP rate gotcha:** callers do NOT
  populate rsIDs in the VCF ID field, so known% will read 0 unless you first
  annotate the calls against a dbSNP slice (`bcftools annotate`). Omit the metric
  rather than reporting 0 when dbSNP is unavailable.

### Step 9 — Figures
Generate the standard panels (coverage curve with bimodality annotation if present; whole-region-vs-on-target depth bars; concordance Venn + by-type; Ti/Tv on-vs-off + dbSNP; consequence/impact + gene burden if annotation is available). Okabe-Ito palette, `matplotlib.use("Agg")`, Liberation Sans, `svg.fonttype='none'`, save PNG **and** SVG at 300 DPI. Run a media-output-check on each key figure.

### Step 10 — Citations
Use `LiteratureSearch` to fetch real references for the callers, benchmarking methodology, and any highlighted genes. Verify every citation against the returned record; never invent DOIs/PMIDs.

### Step 11 — PDF report
Load the **`pdf-report-generation`** skill and build `report_<sample>_variant_calling.pdf` with: title + one infographic, Executive Summary, Methods (data, tools, key parameters), Results (concordance + QC tables and figures), Discussion/Interpretation, Conclusions, **explicit accuracy-vs-concordance caveat**, Recommendations/Next steps, References. Validate (pypdf ≥2 pages, >5 kB, extractable text) and run a media-output-check.

### Step 12 — Handoff
Point the user to the **`genetic-variant-annotation`** skill for pathogenicity/ClinVar/gnomAD annotation of the VCFs produced here.

---

## Command reference (verified working patterns)

Tool locations vary; resolve them with `command -v` rather than relying on the
reference-run paths. That run used GATK `/opt/gatk/gatk` (4.4.0.0);
bcftools/mosdepth/snpeff/java in a conda env; `/opt/conda/bin/samtools`,
`/opt/conda/bin/bedtools`, `/opt/conda/bin/python` (with pandas/matplotlib/reportlab).

### Align (short read) + BQSR
```bash
RG='@RG\tID:<sample>_<run>\tSM:<sample>\tLB:<lib>\tPL:ILLUMINA\tPU:<run>'
bwa mem -t $T -R "$RG" $REF $FQ1 $FQ2 | samtools sort -@ $T -o $W/sorted.bam -
samtools index $W/sorted.bam
# (optional) subset a region for a pilot: samtools view -b $W/sorted.bam chr20 -o $W/region.bam
gatk MarkDuplicates -I $W/region.bam -O $W/md.bam -M $W/markdup.txt --CREATE_INDEX true
# Run the following BQSR block only when build-matched $DBSNP and $MILLS inputs
# were supplied. Otherwise use $W/md.bam for calling and record that BQSR was skipped.
gatk BaseRecalibrator -I $W/md.bam -R $REF --known-sites $DBSNP --known-sites $MILLS -O $W/recal.table
gatk ApplyBQSR -I $W/md.bam -R $REF --bqsr-recal-file $W/recal.table -O $W/analysis_ready.bam
samtools index $W/analysis_ready.bam
```

### GATK HaplotypeCaller + hard filters
```bash
gatk HaplotypeCaller -R $REF -I $BAM -ERC GVCF -O $W/g.vcf.gz          # add -L <region> to subset
gatk GenotypeGVCFs   -R $REF -V $W/g.vcf.gz -O $W/raw.vcf.gz
gatk SelectVariants  -R $REF -V $W/raw.vcf.gz --select-type-to-include SNP   -O $W/snps.vcf.gz
gatk VariantFiltration -R $REF -V $W/snps.vcf.gz \
  --filter-expression "QD < 2.0" --filter-name QD2 \
  --filter-expression "FS > 60.0" --filter-name FS60 \
  --filter-expression "MQ < 40.0" --filter-name MQ40 \
  --filter-expression "MQRankSum < -12.5" --filter-name MQRankSum-12.5 \
  --filter-expression "ReadPosRankSum < -8.0" --filter-name ReadPosRankSum-8 \
  --filter-expression "SOR > 3.0" --filter-name SOR3 -O $W/snps.filt.vcf.gz
gatk SelectVariants  -R $REF -V $W/raw.vcf.gz --select-type-to-include INDEL -O $W/indels.vcf.gz
gatk VariantFiltration -R $REF -V $W/indels.vcf.gz \
  --filter-expression "QD < 2.0" --filter-name QD2 \
  --filter-expression "FS > 200.0" --filter-name FS200 \
  --filter-expression "ReadPosRankSum < -20.0" --filter-name ReadPosRankSum-20 \
  --filter-expression "SOR > 10.0" --filter-name SOR10 -O $W/indels.filt.vcf.gz
gatk MergeVcfs -I $W/snps.filt.vcf.gz -I $W/indels.filt.vcf.gz -O $OUT/<sample>.gatk.filtered.vcf.gz
```
> For WGS at scale, VQSR / CNN filtering is preferable to hard filters; hard filters are the robust default for small/targeted regions and single samples.

### DeepVariant (short read, local via udocker) — with the fixes that made it work
```bash
export UDOCKER_ALLOW_ROOT=1 PROOT_NO_SECCOMP=1
# ALL IO must be on local /workspace — udocker/PRoot cannot WRITE to S3-FUSE (/mnt/...). Stage ref+BAM to $W first.
udocker --allow-root run \
  --env=LD_LIBRARY_PATH=/usr/local/lib/python3.10/dist-packages/pysam.libs \
  --volume=${W}:/dvwork \
  dv run_deepvariant \
    --model_type=WES \            # or WGS
    --ref=/dvwork/refs/<ref>.fasta \
    --reads=/dvwork/bam/analysis_ready.bam \
    --regions=chr20 \             # omit for whole genome
    --output_vcf=/dvwork/out/<sample>.deepvariant.vcf.gz \
    --output_gvcf=/dvwork/out/<sample>.deepvariant.g.vcf.gz \
    --intermediate_results_dir=/dvwork/intermediate --num_shards=$T
cp $W/out/<sample>.deepvariant.vcf.gz* $OUT/     # sequential copy back to shared: OK
```
Two failure modes this encodes: (1) `pysam` `libunistring`/`libcrypto` ImportError under PRoot → fixed by the `LD_LIBRARY_PATH` env; (2) container cannot write to `/mnt` → all inputs/outputs on `/workspace`.

### DeepVariant-class caller (long read, Biomni HPC)
```python
from biomni.tool import hpc_search_tools, hpc_run_tool, hpc_get_job_results
print(hpc_search_tools("pepper deepvariant long read variant calling"))  # READ the usage field
# construct command per the returned usage, then:
job = hpc_run_tool("pepper-deepvariant", "<command per usage>", {"reads.bam": "<local path>", "ref.fasta": "<local path>"})
# when the completion callback arrives: hpc_get_job_results(job_id)
```

### Normalize + concordance
```bash
for c in gatk dv; do
  bcftools view -f PASS $VCF_$c | bcftools norm -m -any -f $REF -Oz -o $A/$c.norm.pass.vcf.gz
  bcftools index -t $A/$c.norm.pass.vcf.gz
done
bcftools isec -p $A/isec -Oz $A/gatk.norm.pass.vcf.gz $A/dv.norm.pass.vcf.gz
# isec: 0000=GATK-only, 0001=DV-only, 0002=shared(from GATK), 0003=shared(from DV)
# Jaccard = shared / (shared + gatk_only + dv_only)
```

### Target-aware coverage (WES)
```bash
# Build a GENCODE-exon target BED only if the user gives no capture BED (and WARN):
curl "https://api.genome.ucsc.edu/getData/track?genome=hg38;track=wgEncodeGencodeBasicV44;chrom=chr20" \
  | <parse exonStarts/exonEnds to BED> | bedtools sort | bedtools merge > target.bed
# On-target depth (region mode — global.dist is ALWAYS whole-reference, so you MUST use --by):
mosdepth -t 8 -n --by target.bed -x on_target $BAM       # read total_region line for on-target mean
# Per-base curve: samtools depth -a -b target.bed $BAM | awk '{print $3}'  -> numpy (d>=x).mean()
# On/off-target variant split:
bcftools view -R target.bed  $VCF   # on-target
bcftools view -T ^target.bed $VCF   # off-target
```

### dbSNP known-rate (avoid the 0% trap)
```bash
bcftools annotate -a $DBSNP_SLICE -c ID $A/gatk.norm.pass.vcf.gz -Oz -o $A/gatk.norm.pass.dbsnp.vcf.gz
# then known% = fraction of records with a non-'.' ID
```

### Truth-set accuracy (optional)
```bash
# conda install -c bioconda hap.py rtg-tools   (no Docker needed)
hap.py $TRUTH_VCF $A/gatk.norm.pass.vcf.gz -f $CONFIDENT_BED -R $target.bed -r $REF -o happy_gatk
# report SNV/indel precision, recall, F1 from happy_gatk.summary.csv
```

---

## Scientific caveats (read before interpreting results)

- **Concordance is agreement, not accuracy.** High Jaccard means the callers make the same calls — including making the same *mistakes*. Only hap.py-vs-truth (Step 7) measures correctness. Never let a report imply "caller X is better" from concordance alone.
- **On-target vs whole-region for exomes is non-negotiable.** In the reference exome run, whole-chromosome mean depth was 5.1× while on-target (exonic) mean depth was 77.3× — a >15× difference. Reporting the whole-region average would badly understate the actual sequencing depth of the captured coding regions.
- **A GENCODE-exon fallback target is broader than a kit's baited core.** If you must fall back (no capture BED supplied), warn the user: GENCODE exons include UTRs/non-coding exons that a capture kit does not bait, producing a **bimodal** coverage profile (e.g. median 3× but mean 77×, with the captured core >100×). A user who reads the median without this context will wrongly conclude the exome failed. Prefer the kit's actual bait BED whenever available.
- **Genome build + chromosome naming must be consistent.** Reads/BAM, reference, dbSNP, target BED, truth set, and annotation DB must all be the same build (GRCh37 vs GRCh38) and the same contig naming (`chr20` vs `20`). Convert with `bcftools annotate --rename-chrs` (SnpEff's Ensembl DB uses `20`; Broad/UCSC references use `chr20`).
- **Normalize before comparing.** Two callers can represent the same indel differently; `bcftools norm -m -any -f REF` (split multiallelics + left-align) is required before `isec`, or concordance will be spuriously low.
- **Match the DeepVariant model to the assay/read type** (`WES`/`WGS`; short vs long read). The wrong model degrades calls silently.
- **Expected QC ranges:** Ti/Tv ≈ 2.0–2.1 genome-wide, ≈ 2.8–3.2 for coding/exonic SNVs; het/hom and dbSNP known-rate depend on ancestry and depth. Values far outside these ranges signal a problem (contamination, wrong build, bad filtering).
- **Truth-set benchmarking is only valid for GIAB-characterized samples** (HG001–HG007) and only within the confident regions; F1 outside confident regions is meaningless.
- **FUSE storage constraints:** write BAMs/HDF5/random-access files to local `/workspace`, then copy finished files to `/mnt/results` or `/mnt/shared-workspace`. Appends (`>>`) and `touch` fail on FUSE; build scripts locally and copy. ReportLab PDFs and VCF/CSV writes go directly to `/mnt/results`.

---

## Test prompts

1. **Full pipeline (short-read WES):** "I have paired-end WES FASTQs for HG002 (Agilent SureSelect, GRCh38). Call variants with GATK and DeepVariant, compare the callers, and give me a PDF." — exercises align → dual-call → concordance → on-target QC → report.
2. **Benchmark-only (VCF entry):** "Here are two VCFs for the same sample from GATK and DeepVariant — how much do they agree and which looks better?" — exercises the VCF entry point, normalization, concordance, and the concordance≠accuracy caveat.
3. **Truth-set accuracy (WGS):** "Benchmark DeepVariant accuracy on my HG002 WGS BAM against the GIAB truth set." — exercises the hap.py module, whole-genome QC, and the accuracy-vs-concordance distinction.

## Related skills
- **Downstream:** `genetic-variant-annotation` (pathogenicity, ClinVar/gnomAD, ACMG), `pathway-enrichment` / `functional-enrichment-from-degs` (from gene lists).
- **Reporting:** `pdf-report-generation` (used for the PDF here).
- **Adjacent:** long-read SV calling (`sniffles`), somatic calling (Strelka2/Mutect2) — out of this skill's scope.
