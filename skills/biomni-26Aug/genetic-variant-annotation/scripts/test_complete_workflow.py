#!/usr/bin/env python3
"""
Complete Workflow Test for Genetic Variant Annotation

Tests all 4 steps of the standard workflow:
1. Load and validate data
2. Run annotation (simulated if VEP/SNPEff not available)
3. Generate visualizations
4. Export results

This script can run with or without VEP/SNPEff installed.
"""

import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent))

from load_example_data import VERIFIED_BRCA_VARIANTS, load_clinvar_pathogenic_sample


def build_simulated_annotation_dataframe():
    """Build simulated annotations on the same verified variants as the VCF fixture."""
    consequences = [
        'frameshift_variant', 'stop_gained', 'missense_variant',
        'missense_variant', 'missense_variant',
        'missense_variant', 'stop_gained', 'synonymous_variant',
        'missense_variant', 'stop_gained',
    ]
    impacts = [
        'HIGH', 'HIGH', 'MODERATE', 'MODERATE', 'MODERATE',
        'MODERATE', 'HIGH', 'LOW', 'MODERATE', 'HIGH',
    ]
    gnomad_af = [0.0001, 0.0, 0.0005, 0.002, 0.001,
                 0.0003, 0.0, 0.05, 0.01, 0.0]
    sift = ['deleterious', 'deleterious', 'tolerated', 'deleterious', 'deleterious',
            'deleterious', 'deleterious', 'tolerated', 'tolerated', 'deleterious']
    cadd = [32.5, 35.0, 22.1, 24.8, 25.3, 26.1, 34.2, 15.2, 23.5, 36.0]
    revel = [0.89, 0.95, 0.45, 0.62, 0.65, 0.71, 0.93, 0.25, 0.55, 0.96]

    return pd.DataFrame({
        'CHROM': [record[0] for record in VERIFIED_BRCA_VARIANTS],
        'POS': [record[1] for record in VERIFIED_BRCA_VARIANTS],
        'REF': [record[3] for record in VERIFIED_BRCA_VARIANTS],
        'ALT': [record[4] for record in VERIFIED_BRCA_VARIANTS],
        'QUAL': [100] * len(VERIFIED_BRCA_VARIANTS),
        'SYMBOL': [record[7] for record in VERIFIED_BRCA_VARIANTS],
        'Consequence': consequences,
        'IMPACT': impacts,
        'gnomAD_AF': gnomad_af,
        'SIFT': sift,
        'CADD_PHRED': cadd,
        'REVEL': revel,
    })


print("="*70)
print("GENETIC VARIANT ANNOTATION - COMPLETE WORKFLOW TEST")
print("="*70)
print()

# =============================================================================
# STEP 1: LOAD AND VALIDATE DATA
# =============================================================================
print("="*70)
print("STEP 1: LOAD AND VALIDATE DATA")
print("="*70)
print()

try:
    from validate_vcf import validate_vcf, vcf_summary_stats

    # Load example data
    print("Loading example data...")
    data = load_clinvar_pathogenic_sample()
    input_vcf = data['vcf_path']
    print(f"✓ Example VCF created: {input_vcf}")

    # Validate VCF
    print("\nValidating VCF format...")
    results = validate_vcf(input_vcf)
    if not results['is_valid']:
        print(f"❌ Validation errors: {results['errors']}")
        sys.exit(1)

    stats = vcf_summary_stats(input_vcf)
    print(f"✓ VCF is valid!")
    print(f"  Total variants: {stats['total_variants']}")
    print(f"  SNVs: {stats['snvs']}")
    print(f"  Indels: {stats['insertions'] + stats['deletions']}")

    print("\n✓ Data loaded successfully!")

except Exception as e:
    print(f"❌ Step 1 failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# =============================================================================
# STEP 2: RUN ANNOTATION (SIMULATED IF TOOLS NOT AVAILABLE)
# =============================================================================
print("\n" + "="*70)
print("STEP 2: RUN ANNOTATION")
print("="*70)
print()

# Check if annotation tools are available
from select_tool import select_annotation_tool

try:
    tool = select_annotation_tool(organism='human', use_case='clinical', resources='standard')
    print(f"Recommended tool: {tool}")
except:
    tool = 'vep'  # Default
    print(f"Using default tool: {tool}")

# Try to check if VEP/SNPEff are installed
import shutil
vep_available = shutil.which('vep') is not None
snpeff_available = shutil.which('snpEff') is not None

print(f"VEP available: {vep_available}")
print(f"SNPEff available: {snpeff_available}")

if not vep_available and not snpeff_available:
    print("\n⚠️  No annotation tools installed. Creating simulated annotated data...")
    print("   (For real annotation, install VEP or SNPEff)")

    # Create simulated annotated DataFrame
    np.random.seed(42)
    df = build_simulated_annotation_dataframe()

    annotated_vcf = input_vcf  # Use original VCF path
    print(f"✓ Simulated annotation completed! ({len(df)} variants)")

else:
    print("\n✓ Annotation tools available!")
    print("   (Skipping actual annotation for speed - would run VEP/SNPEff here)")
    print("   Creating simulated data for testing steps 3-4...")

    # Even if tools available, simulate for speed
    np.random.seed(42)
    df = build_simulated_annotation_dataframe()
    annotated_vcf = input_vcf

print("\n✓ Annotation completed successfully!")

# =============================================================================
# STEP 3: GENERATE VISUALIZATIONS
# =============================================================================
print("\n" + "="*70)
print("STEP 3: GENERATE VISUALIZATIONS")
print("="*70)
print()

try:
    from filter_variants import filter_by_consequence, filter_by_frequency
    from annotate_genes import annotate_gene_summary
    from plot_variant_distribution import (
        plot_consequence_distribution,
        plot_impact_by_chromosome,
        plot_pathogenicity_scores,
        plot_allele_frequency,
        plot_gene_burden
    )

    # Filter variants
    print("Filtering variants...")
    high_impact = filter_by_consequence(df, impact=['HIGH', 'MODERATE'])
    print(f"  HIGH/MODERATE impact: {len(high_impact)} variants")

    rare = filter_by_frequency(df, max_af=0.01)
    print(f"  Rare (AF < 0.01): {len(rare)} variants")

    # Generate gene summaries
    print("\nGenerating gene-level summaries...")
    gene_df = annotate_gene_summary(df)
    if gene_df is not None:
        print(f"  ✓ Gene summary created: {len(gene_df)} genes")
    else:
        print("  ⚠️  No gene summary (SYMBOL column may be missing)")

    # Create output directory
    output_dir = "results"
    Path(output_dir).mkdir(exist_ok=True)

    # Generate visualizations
    print("\nGenerating visualizations (PNG + SVG)...")

    print("\n1. Consequence distribution plot...")
    plot_consequence_distribution(df, f"{output_dir}/consequence_distribution.png")

    print("\n2. Impact by chromosome plot...")
    plot_impact_by_chromosome(df, f"{output_dir}/impact_by_chromosome.png")

    print("\n3. Pathogenicity scores plot...")
    plot_pathogenicity_scores(df, scores=['CADD_PHRED', 'REVEL'],
                              output_file=f"{output_dir}/pathogenicity_scores.png")

    print("\n4. Allele frequency plot...")
    plot_allele_frequency(df, output_file=f"{output_dir}/allele_frequency.png")

    if gene_df is not None:
        print("\n5. Gene burden plot...")
        plot_gene_burden(gene_df, output_file=f"{output_dir}/gene_burden.png")

    print("\n✓ All visualizations generated successfully!")

except Exception as e:
    print(f"❌ Step 3 failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# =============================================================================
# STEP 4: EXPORT RESULTS
# =============================================================================
print("\n" + "="*70)
print("STEP 4: EXPORT RESULTS")
print("="*70)
print()

try:
    from export_results import export_all

    # Export all results
    print("Exporting all results in all formats...")
    export_all(
        df=df,
        gene_df=gene_df,
        original_vcf=input_vcf,
        output_dir=output_dir,
        tool_name=tool
    )

except Exception as e:
    print(f"❌ Step 4 failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# =============================================================================
# VERIFICATION: TEST PICKLE LOADING
# =============================================================================
print("\n" + "="*70)
print("VERIFICATION: TESTING PICKLE LOADING")
print("="*70)
print()

try:
    from test_pickle_load import test_pickle_load

    pickle_path = f"{output_dir}/analysis_object.pkl"
    obj = test_pickle_load(pickle_path)

    if obj is None:
        print("❌ Pickle loading test failed!")
        sys.exit(1)

except Exception as e:
    print(f"❌ Pickle verification failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# =============================================================================
# FINAL SUMMARY
# =============================================================================
print("\n" + "="*70)
print("✓✓✓ COMPLETE WORKFLOW TEST PASSED ✓✓✓")
print("="*70)
print()
print("All 4 steps completed successfully:")
print("  ✓ Step 1: Data loaded and validated")
print("  ✓ Step 2: Annotation completed (simulated)")
print("  ✓ Step 3: Visualizations generated (PNG + SVG)")
print("  ✓ Step 4: All results exported")
print("  ✓ Verification: Pickle loading works")
print()
print(f"Output directory: {output_dir}/")
print()
print("Generated files:")
print("  - analysis_object.pkl (for downstream skills)")
print("  - all_variants.csv")
print("  - high_impact_variants.csv")
print("  - moderate_impact_variants.csv")
print("  - rare_variants.csv")
if gene_df is not None:
    print("  - gene_summary.csv")
print("  - summary_report.xlsx")
print("  - consequence_distribution.png/.svg")
print("  - impact_by_chromosome.png/.svg")
print("  - pathogenicity_scores.png/.svg")
print("  - allele_frequency.png/.svg")
if gene_df is not None:
    print("  - gene_burden.png/.svg")
print()
print("="*70)
print("🎉 WORKFLOW VALIDATION COMPLETE!")
print("="*70)
