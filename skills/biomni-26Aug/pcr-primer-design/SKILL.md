---
id: "skill_62f7fe9a2d3e475983cadecafd0547e3"
name: "pcr-primer-design"
description: "Use to design and validate primers or probes for PCR, qPCR, TaqMan assays, genotyping, cloning, or sequencing applications."
category: "molecular_design"
visibility: "public"
starting-prompt: "Design qPCR primers for human GAPDH with MIQE compliance checking. Generate a PDF report with an intro, methods, results, conclusions and figures from all of the analyses you perform."
---

# PCR Primer Design

Comprehensive PCR and qPCR primer design following MIQE 2.0 guidelines with automated validation.

## When to Use This Skill

Use this skill when you need:
- ✅ **qPCR primers** with MIQE 2.0 compliance for publication
- ✅ **Standard PCR primers** for cloning, genotyping, or amplification (100-1000 bp)
- ✅ **TaqMan probes** for probe-based qPCR assays
- ✅ **Rigorous validation** (specificity, dimers, secondary structures)
- ✅ **Analysis-ready documentation** with comprehensive reports

**Choose application based on:**
- qPCR: 70-140 bp amplicons, strict Tm matching (±2°C), MIQE compliance
- Standard PCR: 100-1000 bp amplicons, general amplification
- TaqMan: Probe-based detection, fluorescent assays
- Multiplex: Multiple targets, compatible Tm requirements
- Sequencing: Single-direction primers, Sanger sequencing

**Don't use for:**
- ❌ In-situ hybridization probes → use specialized oligo design tools
- ❌ NGS library prep primers → use adapter design workflows
- ❌ CRISPR guide RNAs → use CRISPR-specific design tools

## Quick Start (Example)

Test this skill with a sample qPCR design in ~2 minutes:

```python
# Example: Design qPCR primers for a 700bp target sequence
from scripts.design_qpcr_primers import design_qpcr_primers

# Sample GAPDH sequence (700 bp, exon 3-4 region)
sequence = "ATGGGGAAGGTGAAGGTCGGAGTCAACGGATTTGGTCGTATTGGGCGCCTGGTCACCAGGGCTGCTTTTAACTCTGGTAAAGTGGATATTGTTGCCATCAATGACCCCTTCATTGACCTCAACTACATGGTTTACATGTTCCAATATGATTCCACCCATGGCAAATTCCATGGCACCGTCAAGGCTGAGAACGGGAAGCTTGTCATCAATGGAAATCCCATCACCATCTTCCAGGAGCGAGATCCCTCCAAAATCAAGTGGGGCGATGCTGGCGCTGAGTACGTCGTGGAGTCCACTGGCGTCTTCACCACCATGGAGAAGGCTGGGGCTCATTTGCAGGGGGGAGCCAAAAGGGTCATCATCTCTGCCCCCTCTGCTGATGCCCCCATGTTCGTCATGGGTGTGAACCATGAGAAGTATGACAACAGCCTCAAGATCATCAGCAATGCCTCCTGCACCACCAACTGCTTAGCACCCCTGGCCAAGGTCATCCATGACAACTTTGGTATCGTGGAAGGACTCATGACCACAGTCCATGCCATCACTGCCACCCAGAAGACTGTGGATGGCCCCTCCGGGAAACTGTGGCGTGATGGCCGCGGGGCTCTCCAGAACATCATCCCTGCCTCTACTGGCGCTGCCAAGGCTGTGGGCAAGGTCATCCCTGAGCTGAACGGGAAGCTCACTGGCATGGCCTTCCGTGTCCCCACTGCCAACGTGTCAGTGGTGGACCTGACCTGCCGTCTAGAAAAACCTGCCAAATATGATGACATCAAGAAGGTGGTGAAGCAGGCGTCGGAGGGCCCCCTCAAGGGCATCCTGGGCTACACTGAGCACCAGGTGGTCTCCTCTGACTTCAACAGCGACACCCACTCCTCCACCTTTGACGCTGGGGCTGGCATTGCCCTCAACGACCACTTTGTCAAGCTCATTTCCTGGTATGACAACGAATTTGGCTACAGCAACAGGGTGGTGGACCTCATGGCCCACATGGCCTCCAAGGAGTAAGACCCCTGGACCACCAGCCCCAGCAAGAGCACAAGAGGAAGAGAGAGACCCTCACTGCTGGGGAGTCCCTGCCACACTCAGTCCCCCACCACACTGAATCTCCCCTCCTCACAGTTGCCATGTAGACCCCTTGAAGAGGGGAGGGCTCTCTCTTCCTCTTGTGCTCTTGCTGGGGCTGGCATTGCCCTCAACGACCACTTTGTCAAGCTCATTTCCTGGTATGACAACG"

primers = design_qpcr_primers(
    sequence=sequence,
    amplicon_size_range=(80, 120),
    num_return=5
)

print(f"Found {len(primers['primers'])} primer pairs")
print(f"MIQE-compliant: {sum(p.get('miqe_compliant', False) for p in primers['primers'])}")
```

**What you get:** 3-5 MIQE-compliant primer pairs, amplicons 80-120 bp, Tm matched within 2°C

**For your own data:** Follow Clarification Questions below to provide your sequence.

## Installation

**Core packages:**
```bash
pip install primer3-py biopython matplotlib pandas requests openpyxl
```

**Recommended:** Use virtual environment:
```bash
python -m venv pcr_env
source pcr_env/bin/activate  # Windows: pcr_env\Scripts\activate
pip install -r requirements.txt
```

### Software Requirements

| Software | Version | License | Commercial Use | Installation |
|----------|---------|---------|----------------|--------------|
| primer3-py | ≥2.0.0 | GPL v2 | ✅ Permitted* | `pip install primer3-py` |
| Biopython | ≥1.80 | BSD | ✅ Permitted | `pip install biopython` |
| matplotlib | ≥3.5.0 | PSF (BSD-compatible) | ✅ Permitted | `pip install matplotlib` |
| pandas | ≥1.5.0 | BSD | ✅ Permitted | `pip install pandas` |

*GPL v2 permits use in AI agent applications (execution, not distribution).

**⚠️ plotnine-prism — `needs_commercial_review`.** An earlier version of this
skill listed `plotnine-prism` as an MIT-licensed dependency. The PyPI license
classifier for `plotnine-prism` is contradictory ("Other/Proprietary License
(GNU General Public License v2.0)"), and explicit commercial-use clearance was
not found. The visualization scripts have been rewritten to use **matplotlib**
directly, removing the `plotnine-prism` dependency. Do **not** reintroduce
`plotnine-prism` without first resolving its license status.

**NCBI API:** Primer-BLAST access is free. Rate limit: 3 requests/second (no API key) or 10/second (with free API key). See [references/primer_design_best_practices.md#ncbi-api-setup](references/primer_design_best_practices.md) for API key setup.

## Inputs

**Required:**
- **Target DNA sequence** in one of these formats:
  - FASTA file (local or uploaded)
  - GenBank/RefSeq accession (e.g., NM_002046)
  - Raw sequence (paste directly)
  - Gene name + organism (fetches from NCBI)

**Sequence requirements:**
- Minimum length: 150 bp (qPCR), 300 bp (standard PCR)
- Format: ATCG nucleotides (U converted to T)
- Quality: Avoid ambiguous bases (N) in primer regions

**Optional:**
- Regions to avoid (SNPs, repeats, splice sites)
- Custom parameter ranges (Tm, GC%, amplicon size)
- Organism/genome for specificity checking

**See [references/primer_design_best_practices.md#input-preparation](references/primer_design_best_practices.md) for sequence preparation guidelines.**

## Outputs

**Primary results:**
- **Primer sequences** with properties (Tm, GC%, length, position)
- **Validation report** (dimers, secondary structures, specificity)
- **Quality scores** and QC flags
- **`specificity_status`** — an explicit record of what specificity check ran
  (`in_silico_on_target_only`, `flagged_high_risk_unverified`, `local_blast_passed`,
  `local_blast_failed`, `primer_blast_passed`, or `not_run`). Carried in the CSV
  (`Specificity_Status`/`Pseudogene_Risk` columns), JSON (`metadata.specificity_status`),
  and MIQE checklist. See [Specificity: what was actually checked](#specificity-what-was-actually-checked).

**Export formats** (user-selectable):
- `primers.csv` - Spreadsheet-compatible table
- `primers.xlsx` - Excel with multiple sheets (design, validation, parameters)
- `primers.json` - Structured data for programmatic use
- `idt_order.txt` - IDT ordering format (copy-paste ready)
- `miqe_checklist.xlsx` - MIQE 2.0 compliance documentation (qPCR only)

**Visualizations** (optional):
- Primer binding site alignment (SVG, standard resolution)
- Tm distribution plots
- Secondary structure diagrams

**See [references/code_examples.md#export-examples](references/code_examples.md#export-examples) for format details.**

- `analysis_report.pdf` — PDF report created with `pdf-report-generation` from the generated primer-design artifacts

> **PDF generation:** This package does not ship a dedicated PDF-generation
> script. To produce `analysis_report.pdf`, use the **`pdf-report-generation`**
> skill (Biomni-native). If that skill is unavailable, `reportlab`
> (BSD-licensed, listed in `requirements.txt`) may be used directly as a fallback
> while clearly disclosing the fallback.

## Report Packaging

When the user requests a PDF report, run the analysis first, then load and use
the Biomni `pdf-report-generation` skill for the final PDF deliverable. Build
the PDF from this skill's markdown summary, result tables, and generated figures,
and save it using the PDF filename listed in Outputs, or a stable descriptive filename when Outputs does not define one.

Keep this skill focused on scientific workflow and artifact content. Do not add
custom figure appearance or report layout instructions here; those are handled
by the platform prompt and dedicated reporting skills. If `pdf-report-generation`
is unavailable, use the packaged markdown/HTML/script fallback when present and
clearly disclose the fallback.

## Clarification Questions

### 1. Input Sequence (ASK THIS FIRST)

Do you have a specific DNA sequence to design primers for?

- **Option A:** Upload FASTA file or provide file path
- **Option B:** Provide GenBank/RefSeq accession (e.g., NM_001256799)
- **Option C:** Provide gene name + organism (will fetch from NCBI)
- **Option D:** Paste sequence directly

**If uploaded:** Is this the complete target sequence or a specific region?

**Expected:** 150+ bp for qPCR, 300+ bp for standard PCR

### 2. PCR Application

What is your intended use for these primers?

- **qPCR (Quantitative PCR)** - Gene expression, 70-140 bp amplicons, MIQE-compliant
- **Standard PCR** - General amplification, cloning, genotyping, 100-1000 bp amplicons
- **TaqMan Assay** - Probe-based qPCR with fluorescent detection
- **Multiplex PCR** - Multiple targets simultaneously, compatible Tm required
- **Sequencing** - Sanger sequencing, single-direction primer
- **SNP Genotyping** - Allele-specific amplification

**Default:** qPCR (most common for gene expression studies)

### 3. Design Parameters

Do you want to use application-specific default parameters or customize?

- **Standard parameters** (recommended) - Optimized for selected application
- **Custom Tm range** - Specify melting temperature range (default: 58-62°C for qPCR)
- **Custom amplicon size** - Specify product size range
- **Custom GC range** - Adjust GC% (default: 40-60%)
- **Avoid regions** - Exclude specific sequences (SNPs, repeats, etc.)

**For qPCR:** Target exon-exon junction or ensure intron >1kb (MIQE guideline)?

**To understand design parameters:** See [references/parameter_ranges.md](references/parameter_ranges.md)

### 4. Validation Level

How thoroughly should primers be validated?

- **Basic** - Tm, GC%, dimer check (~1 min, sufficient for most uses)
- **Standard** - Basic + in-silico PCR on the supplied transcript (~2-3 min, recommended). **On-target only** — does not check the genome.
- **Complete** - Standard + genome-wide check via local BLAST (requires `blastn` + a database) or NCBI Primer-BLAST. Analysis-ready.
- **MIQE-compliant** - Complete + full documentation (qPCR only, ~10 min)

**Note:** In-silico PCR is fast and offline but checks only the one transcript you
provide. For genome-wide specificity (and to clear a `flagged_high_risk_unverified`
status on genes like ACTB/GAPDH) you must run local BLAST or NCBI Primer-BLAST.
NCBI Primer-BLAST requires internet and respects rate limits; the bundled
`check_primer_specificity()` returns `not_run` (it does not auto-submit to NCBI).

### 5. Output Requirements

What outputs do you need?

- **Export format:** CSV (default), Excel, JSON, IDT order format, or MIQE checklist
- **Report format:** Markdown (default), HTML, or text
- **Visualizations:** Generate primer alignment plots? (yes/no)
- **Number of primers:** How many primer pairs to return? (default: 5)

**For qPCR:** MIQE checklist is automatically generated.

## Standard Workflow

🚨 **EXECUTE EXACTLY AS SHOWN - Do not modify these commands.**

**CRITICAL: Use relative paths (scripts/, references/). DO NOT construct absolute paths.**

### Step 1: Load Target Sequence

**Option A: Load from FASTA file**

```python
from Bio import SeqIO
record = SeqIO.read("your_sequence.fasta", "fasta")
sequence = str(record.seq)
```

**Option B: Load from GenBank accession**

See [references/code_examples.md#loading-sequences](references/code_examples.md#loading-sequences) for NCBI fetching code.

**Option C: Paste sequence directly**

```python
# For quick testing, paste your target sequence
sequence = "ATGGGGAAGGTGAAGGTCGGAGTCAACGGATTTGGTCGTATTGGG..."  # Your sequence here
```

### Step 2: Design Primers

**Choose based on application (from Clarification Question #2):**

**For qPCR (most common):**

```python
from scripts.design_qpcr_primers import design_qpcr_primers

primers = design_qpcr_primers(
    sequence=sequence,
    amplicon_size_range=(70, 140),  # MIQE guideline
    tm_match_threshold=2.0,
    num_return=5
)
```

**For Standard PCR:**

```python
from scripts.design_standard_primers import design_pcr_primers

primers = design_pcr_primers(
    sequence=sequence,
    amplicon_size_range=(100, 1000),
    tm_range=(55, 65),
    num_return=5
)
```

**For TaqMan Assay:**

```python
from scripts.design_taqman_probes import design_taqman_assay

assay = design_taqman_assay(
    sequence=sequence,
    probe_tm_offset=8.0,  # Probe Tm = primer Tm + 8°C
    num_return=5
)
```

**For custom parameters:** Read [references/parameter_ranges.md](references/parameter_ranges.md) and adapt ranges to your requirements.

### Step 3: Validate Primers

**Basic validation (recommended for all):**

```python
from scripts.check_dimers import analyze_dimers
from scripts.check_secondary_structures import analyze_secondary_structures

top_primer = primers['primers'][0]

# Check dimers
dimer_result = analyze_dimers(
    [top_primer['forward_seq'], top_primer['reverse_seq']],
    temperature=60.0
)

# Check secondary structures
fwd_structure = analyze_secondary_structures(top_primer['forward_seq'], 60.0)
rev_structure = analyze_secondary_structures(top_primer['reverse_seq'], 60.0)
```

**Complete validation (for publication):**

```python
from scripts.validate_specificity import in_silico_pcr_report

# In-silico PCR (fast, OFFLINE) — checks ONLY the supplied transcript.
# Pass gene_symbol so pseudogene/paralog-prone targets (ACTB, GAPDH, ...) are flagged.
spec = in_silico_pcr_report(
    forward_primer=top_primer['forward_seq'],
    reverse_primer=top_primer['reverse_seq'],
    sequence=sequence,
    gene_symbol="ACTB"          # used for the pseudogene/paralog risk flag
)
print(spec['specificity_status'])   # e.g. 'in_silico_on_target_only' or 'flagged_high_risk_unverified'
if spec.get('warning'):
    print(spec['warning'])          # prominent caveat for high-risk genes
```

⚠️ **In-silico PCR only checks the ONE sequence you pass in.** It cannot see
pseudogenes, paralogs, or other transcripts elsewhere in the genome. A clean
result means "specific to this transcript", NOT "genome-wide specific". See
**[Specificity: what was actually checked](#specificity-what-was-actually-checked)** below.

**Genome-wide check (recommended for publication, especially for high-risk genes):**

```python
from scripts.validate_specificity import check_specificity_local_blast

# Requires BLAST+ (`blastn`) and a local genomic/transcriptomic database.
# If blastn or the db is unavailable, returns specificity_status == 'not_run'
# (an honest "not checked"), NOT a misleading pass.
spec = check_specificity_local_blast(
    forward_primer=top_primer['forward_seq'],
    reverse_primer=top_primer['reverse_seq'],
    blast_db_path="/path/to/blastdb/human_genome",
    max_mismatches=3,
    gene_symbol="ACTB"
)
print(spec['specificity_status'])   # 'local_blast_passed' | 'local_blast_failed' | 'not_run'

# NCBI Primer-BLAST: check_primer_specificity() builds a submit URL and returns
# 'not_run' (this offline build does NOT submit jobs to NCBI). See
# [references/code_examples.md#validation-examples](references/code_examples.md#validation-examples).
```

#### Specificity: what was actually checked

Every specificity function returns a **`specificity_status`** field so reports and
exports state exactly what was — and was not — verified. **The honest default is
in-silico-on-target only** (the genome is *not* checked unless you run local BLAST
or Primer-BLAST).

| `specificity_status` | Meaning |
|----------------------|---------|
| `in_silico_on_target_only` | In-silico PCR matched only the single supplied transcript. Genome-wide off-targets (pseudogenes, paralogs, other transcripts) **were NOT checked**. |
| `flagged_high_risk_unverified` | Target is a known pseudogene/paralog-prone gene (e.g. **ACTB, GAPDH**) **and** only in-silico-on-target ran. Off-targets are plausible but unverified — `is_specific` is `None` and a prominent `warning` is emitted. Run a genome-wide check. |
| `local_blast_passed` | Local `blastn` ran genome-wide and found no off-target hits above threshold. |
| `local_blast_failed` | Local `blastn` ran and found off-target hits — redesign. |
| `primer_blast_passed` | NCBI Primer-BLAST ran genome-wide and judged the primers specific. |
| `not_run` | No specificity check was actually performed (e.g. `blastn`/db unavailable, or Primer-BLAST not submitted). **Not** a pass. |

🚩 **Pseudogene / paralog caveat (ACTB, GAPDH, and friends).** Common reference
genes have processed pseudogenes (often retained in genomic DNA) and large paralog
families. In-silico PCR against a single transcript **cannot** detect these
off-targets. `flag_pseudogene_risk(gene_symbol)` flags such genes (curated set
includes ACTB, GAPDH, B2M, HPRT1, YWHAZ, RPL13A, PPIA, plus ribosomal `RPL*`/`RPS*`,
tubulin `TUBB*`/`TUBA*`, and actin `ACTG*` families). When a high-risk gene is only
checked in-silico-on-target, the status is escalated to `flagged_high_risk_unverified`
with a WARNING, and the MIQE checklist / CSV / JSON all carry the qualified status.
**Always pass `gene_symbol`** to the specificity functions so this flag fires.

**`in_silico_pcr()` mismatch tolerance:** the default is **exact match
(`max_mismatches=0`)**. Set `max_mismatches=N` to allow up to N substitutions
(Hamming, no indels) at each binding site to model tolerant priming.

⚠️ **CRITICAL - DO NOT:**
- ❌ Use absolute paths like `/mnt/knowhow/` → use relative paths `scripts/`
- ❌ Write inline primer design code → use provided scripts
- ❌ Skip dimer/structure checks → these catch common failures

### Step 4: Generate Reports and Export

```python
from scripts.generate_reports import generate_primer_report
from scripts.export_results import export_primers

# Generate report
report = generate_primer_report(
    primers=primers,
    validation_results={
        'dimers': dimer_result,
        'secondary_structures': {'forward': fwd_structure, 'reverse': rev_structure}
    },
    output_format="markdown",
    include_miqe_checklist=(application == 'qpcr')  # From Clarification Question #2
)

# Export in requested format(s). Pass validation_results so the
# specificity_status (and pseudogene flag) appear in the CSV/JSON/MIQE outputs.
export_primers(primers, format="csv", output_file="primers.csv",
               validation_results=validation_results)
export_primers(primers, format="excel", output_file="primers.xlsx",
               validation_results=validation_results)

# For qPCR: MIQE checklist (now carries the honest specificity_status)
if application == 'qpcr':
    export_primers(primers, format="miqe_checklist", output_file="miqe_checklist.xlsx",
                   validation_results=validation_results)
```

**Excel export is verified.** Every `.xlsx` write is wrapped in error handling and
checked for a non-trivial file size (>1000 bytes) afterward. If the write fails or
produces a suspiciously small/0-byte file, `export_primers` prints a warning and
**falls back to CSV** (same base filename, extra sheets as `<base>__<sheet>.csv`) —
it never silently leaves a corrupt `.xlsx`. The returned path tells you what was
actually written (`.xlsx` on success, `.csv` on fallback).

**For visualization plots:** See [references/code_examples.md#visualization](references/code_examples.md#visualization)

**That's it! The scripts handle all design and validation automatically.**

## Common Issues

| Issue | Likely Cause | Solution |
|-------|--------------|----------|
| **No primers found** | Target too short or constraints too strict | Relax Tm range (±5°C), widen GC range (35-65%), provide longer sequence (≥300 bp) |
| **High primer-dimer formation** | Complementary sequences, low Tm | Increase Tm to 60-62°C, redesign avoiding complementary regions |
| **Multiple off-target amplicons** | Low specificity, repetitive sequences | Move to unique region, increase primer length (22-25 nt), check specificity with BLAST |
| **Tm mismatch between primers** | Different GC content | Adjust primer lengths to balance Tm, use Primer3 penalty weights (see [references/parameter_ranges.md](references/parameter_ranges.md)) |
| **Poor qPCR efficiency** | Dimers, secondary structures, amplicon >150 bp | Redesign with shorter amplicon (70-120 bp), check for hairpins, verify no dimers |
| **NCBI API rate limit errors** | Too many requests too quickly | Wait 0.33 sec between requests, get free API key (10 req/sec), or use in-silico PCR first |
| **ImportError for scripts** | Missing `__init__.py` in scripts/ | Create empty `scripts/__init__.py` file to make it a Python package |

**For detailed troubleshooting:** See [references/troubleshooting_guide.md](references/troubleshooting_guide.md)

## Suggested Next Steps

After successful primer design:

1. **Order primers** - Use IDT order format export for direct ordering
2. **Optimize PCR conditions** - Test annealing temperature gradient (Tm ± 3°C)
3. **Validate experimentally**:
   - Test specificity (gel electrophoresis, melt curve for qPCR)
   - Optimize primer concentration (50-900 nM range)
   - Verify amplicon size (gel or bioanalyzer)
4. **For qPCR** - Perform standard curve, efficiency calculation, melt curve analysis
5. **Document** - Save MIQE checklist and validation reports for publication

**Related protocols:** See [references/primer_design_best_practices.md#experimental-validation](references/primer_design_best_practices.md#experimental-validation)

## Related Skills

- **qPCR Data Analysis** - Analyze qPCR Cq values after experimental validation
- **Sanger Sequencing Analysis** - Analyze results from sequencing primers
- **Gene Expression Normalization** - Choose reference genes for qPCR

## References

### Documentation
- [Primer Design Best Practices](references/primer_design_best_practices.md) - Comprehensive design guidelines
- [MIQE 2.0 Guidelines](references/miqe_guidelines.md) - qPCR standards and compliance
- [Parameter Ranges](references/parameter_ranges.md) - Recommended parameter values
- [Troubleshooting Guide](references/troubleshooting_guide.md) - Common issues and solutions
- [Code Examples](references/code_examples.md) - Complete code examples for all applications

### Key Publications
- **MIQE 2.0**: Bustin SA, et al. (2025) Clinical Chemistry 71(6):634-660
- **Primer3**: Untergasser A, et al. (2012) Nucleic Acids Research 40(15):e115
- **Primer-BLAST**: Ye J, et al. (2012) BMC Bioinformatics 13:134

### Online Resources
- NCBI Primer-BLAST: https://www.ncbi.nlm.nih.gov/tools/primer-blast/
- Primer3 Web: https://primer3.ut.ee/
- MIQE Guidelines: https://rdml.org/miqe.html
