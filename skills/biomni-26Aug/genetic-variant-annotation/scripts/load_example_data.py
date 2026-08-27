"""
Load Example VCF Data for Testing

Provides small example VCF files for testing the variant annotation workflow.
These are for evaluation/testing purposes.

For user data validation, see validate_vcf.py
"""

import gzip
from pathlib import Path


VERIFIED_BRCA_VARIANTS = [
    # CHROM, POS, ID, REF, ALT, DP, AF, SYMBOL, dbSNP GRCh38 HGVS
    ("chr17", 43057063, "rs80357906", "GGG", "GGGG", 50, 0.5, "BRCA1", "NC_000017.11:g.43057063_43057065dup"),
    ("chr17", 43063903, "rs28897696", "G", "A", 45, 0.5, "BRCA1", "NC_000017.11:g.43063903G>A"),
    ("chr17", 43093449, "rs1799949", "G", "A", 55, 0.5, "BRCA1", "NC_000017.11:g.43093449G>A"),
    ("chr17", 43106457, "rs80357382", "T", "C", 52, 0.5, "BRCA1", "NC_000017.11:g.43106457T>C"),
    ("chr17", 43092919, "rs799917", "G", "A", 48, 0.5, "BRCA1", "NC_000017.11:g.43092919G>A"),
    ("chr13", 32356456, "rs80358969", "A", "C", 47, 0.5, "BRCA2", "NC_000013.11:g.32356456A>C"),
    ("chr13", 32346896, "rs28897743", "G", "A", 51, 0.5, "BRCA2", "NC_000013.11:g.32346896G>A"),
    ("chr13", 32340455, "rs1799954", "C", "T", 49, 0.5, "BRCA2", "NC_000013.11:g.32340455C>T"),
    ("chr13", 32332592, "rs144848", "A", "C", 50, 0.5, "BRCA2", "NC_000013.11:g.32332592A>C"),
    ("chr13", 32398489, "rs11571833", "A", "T", 46, 0.5, "BRCA2", "NC_000013.11:g.32398489A>T"),
]


def load_clinvar_pathogenic_sample():
    """
    Create a small BRCA1/BRCA2 variant fixture for workflow testing

    Creates a minimal VCF with 10 dbSNP-verified BRCA1/BRCA2 variants for
    testing the annotation workflow. Treat this as a smoke-test fixture, not a
    clinical truth set.

    Dataset Details:
    - Variants: 10 dbSNP BRCA1/BRCA2 variants
    - Verification: GRCh38 positions/alleles from NCBI dbSNP primary placements;
      REF alleles checked against UCSC hg38 sequence
    - Genes: BRCA1 (chr17) and BRCA2 (chr13)
    - Genome: GRCh38
    - Size: ~2 KB (minimal)
    - Download time: None (generated programmatically)

    Returns
    -------
    dict
        {
            'vcf_path': str, Path to created VCF file
            'genome': str, Genome build ('GRCh38')
            'description': str, Dataset description
            'expected_results': dict, Expected annotation metrics
        }

    Examples
    --------
    >>> data = load_clinvar_pathogenic_sample()
    >>> print(f"VCF created at: {data['vcf_path']}")
    >>> # Use with annotation workflow:
    >>> from scripts.validate_vcf import validate_vcf
    >>> results = validate_vcf(data['vcf_path'])
    """
    # Setup data directory
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    vcf_file = data_dir / "clinvar_brca_pathogenic.vcf.gz"

    if vcf_file.exists():
        print(f"✓ Example data already exists: {vcf_file}")
    else:
        print("Creating minimal test VCF with verified BRCA variants...")
        create_minimal_test_vcf(vcf_file)
        print(f"✓ Created test VCF: {vcf_file}")

    return {
        'vcf_path': str(vcf_file),
        'genome': 'GRCh38',
        'description': 'GRCh38-verified BRCA1/BRCA2 variant fixture for annotation workflow testing',
        'expected_results': {
            'total_variants': 10,
            'verified_grch38_ref': True,
            'clinical_truth_set': False,
            'source': 'NCBI dbSNP primary GRCh38 placements; UCSC hg38 REF check',
            'runtime_vep': '2-5 minutes',
            'runtime_snpeff': '1-2 minutes'
        }
    }


def create_minimal_test_vcf(output_path):
    """
    Create minimal VCF with 10 BRCA1/BRCA2 variants for quick testing

    Includes a mix of SNVs and one small BRCA1 insertion. Consequence counts
    are database/transcript-set dependent; do not treat this example as a
    clinical validation truth set.

    Parameters
    ----------
    output_path : str or Path
        Output path for bgzipped VCF file
    """
    header = """##fileformat=VCFv4.2
##reference=GRCh38
##contig=<ID=chr13,length=114364328>
##contig=<ID=chr17,length=83257441>
##INFO=<ID=DP,Number=1,Type=Integer,Description="Total Depth">
##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">
##INFO=<ID=GENE,Number=1,Type=String,Description="Gene symbol for the fixture variant">
##INFO=<ID=DBSNP_HGVS,Number=1,Type=String,Description="dbSNP primary GRCh38 HGVS expression used to verify the fixture allele">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read Depth">
#CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO	FORMAT	SAMPLE1
"""
    rows = [
        (
            f"{chrom}\t{pos}\t{variant_id}\t{ref}\t{alt}\t100\tPASS\t"
            f"DP={dp};AF={af};GENE={symbol};DBSNP_HGVS={hgvs}\tGT:DP\t0/1:{dp}"
        )
        for chrom, pos, variant_id, ref, alt, dp, af, symbol, hgvs in VERIFIED_BRCA_VARIANTS
    ]
    vcf_content = header + "\n".join(rows) + "\n"

    with gzip.open(output_path, 'wt') as f:
        f.write(vcf_content)


def validate_input_data(vcf_path):
    """
    Validate user-provided VCF file before annotation

    Wrapper around validate_vcf.py for consistent interface.

    Parameters
    ----------
    vcf_path : str or Path
        Path to VCF file to validate

    Returns
    -------
    dict
        Validation results with 'is_valid', 'errors', 'warnings' keys

    See Also
    --------
    scripts.validate_vcf.validate_vcf : Full validation with detailed metrics
    """
    from validate_vcf import validate_vcf
    return validate_vcf(vcf_path)


if __name__ == '__main__':
    # Demo usage
    print("="*70)
    print("GENETIC VARIANT ANNOTATION - Example Data Loader")
    print("="*70)
    print()

    print("Loading example data...")
    data = load_clinvar_pathogenic_sample()

    print()
    print("Dataset Information:")
    print(f"  VCF file: {data['vcf_path']}")
    print(f"  Genome: {data['genome']}")
    print(f"  Description: {data['description']}")
    print()
    print("Expected Results:")
    for key, value in data['expected_results'].items():
        print(f"  {key}: {value}")
    print()
    print("✓ Ready for annotation!")
    print()
    print("Next steps:")
    print("  1. Validate: python -c 'from scripts.validate_vcf import validate_vcf; validate_vcf(\"data/clinvar_brca_pathogenic.vcf.gz\")'")
    print("  2. Annotate: See SKILL.md for full workflow")
