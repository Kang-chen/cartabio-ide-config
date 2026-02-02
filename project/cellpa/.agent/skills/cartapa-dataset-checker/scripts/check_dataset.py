#!/usr/bin/env python
"""
CartaPA Dataset Validation Script
==================================

Comprehensive quality checks for spatial proteomics datasets.

Usage:
    python check_dataset.py --input data.h5ad [--dataset-type auto]
    python check_dataset.py --input data.h5ad --report report.md --visualize
"""

import argparse
import sys
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import anndata as ad


# Dataset configurations
DATASET_CONFIGS = {
    'codex_hcc': {
        'expected_cells': (400_000, 600_000),
        'expected_slices': 24,
        'expected_proteins': 51,
        'required_obs': ['patient_id', 'celltype', 'state'],
        'celltype_col': 'celltype',
        'state_col': 'state',
        'state_values': ['Pre', 'Post'],
        'celltypes': [
            'Unknown', 'Endothelial cells', 'CD4 T cells', 'CD8 T cells',
            'Treg cells', 'B cells', 'NK cells', 'Dendritic cells',
            'Macrophages', 'Mast cells', 'Neutrophils', 'Fibroblasts',
            'Hepatocytes', 'Tumor cells'
        ]
    },
    'codex_tnbc': {
        'expected_cells': (1_500_000, 2_500_000),
        'expected_slices': 28,
        'expected_proteins': 56,
        'required_obs': ['patient_id'],
        'celltype_col': 'cell_type',  # Uses underscore format
        'celltype_alt_cols': ['celltype', 'cell_type_ori'],
        'state_col': None,
        'celltypes': None,  # Needs verification from paper
        'warnings': ['Pre/post treatment labels may be unclear in raw data']
    },
    'imc_tnbc': {
        'expected_cells': (800_000, 1_200_000),
        'expected_slices': 243,
        'expected_proteins': 41,
        'required_obs': ['patient_id'],
        'celltype_col': 'cell_type',  # Uses underscore format
        'celltype_alt_cols': ['celltype', 'cell_type_ori'],
        'state_col': None,
        'celltypes': None,  # WARNING: epi cells not labeled with "epi"
        'warnings': ['Epithelial cells may not contain "epi" in name - check paper for marker mapping']
    },
    'safe_hnscc': {
        'expected_cells': (1_800_000, 2_500_000),
        'expected_slices': 41,
        'expected_proteins': 27,
        'required_obs': ['slice_id', 'patient_id'],
        'celltype_col': 'cell_type',  # Uses underscore format
        'celltype_alt_cols': ['celltype', 'cell_type_ori'],
        'state_col': None,
        'celltypes': None,
        'warnings': ['Missing stromal cell category', 'Check for tile-level coordinates']
    }
}


@dataclass
class ValidationResult:
    """Container for validation results."""
    passed: bool
    category: str
    check_name: str
    message: str
    details: Optional[Dict] = None


@dataclass
class ValidationReport:
    """Full validation report."""
    dataset_path: str
    dataset_type: str
    results: List[ValidationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def n_passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def n_failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def add(self, result: ValidationResult):
        self.results.append(result)

    def to_markdown(self) -> str:
        """Generate markdown report."""
        lines = [
            f"# Dataset Validation Report",
            f"",
            f"**Dataset**: `{self.dataset_path}`",
            f"**Type**: {self.dataset_type}",
            f"**Status**: {'✅ PASSED' if self.passed else '❌ FAILED'}",
            f"**Results**: {self.n_passed} passed, {self.n_failed} failed",
            f"",
            "## Detailed Results",
            ""
        ]

        # Group by category
        categories = {}
        for r in self.results:
            if r.category not in categories:
                categories[r.category] = []
            categories[r.category].append(r)

        for category, results in categories.items():
            lines.append(f"### {category}")
            lines.append("")
            for r in results:
                icon = "✅" if r.passed else "❌"
                lines.append(f"- {icon} **{r.check_name}**: {r.message}")
                if r.details:
                    for k, v in r.details.items():
                        lines.append(f"  - {k}: {v}")
            lines.append("")

        return "\n".join(lines)


def detect_dataset_type(adata: ad.AnnData) -> str:
    """Auto-detect dataset type from h5ad metadata."""
    obs_cols = set(adata.obs.columns)
    n_cells = adata.n_obs
    n_vars = adata.n_vars

    # Check for HCC markers
    if 'state' in obs_cols and n_vars >= 50:
        if 'Pre' in adata.obs.get('state', pd.Series()).unique():
            return 'codex_hcc'

    # Check cell count ranges
    if n_cells > 1_800_000:
        if 'slice_id' in obs_cols:
            slice_ids = adata.obs['slice_id'].astype(str)
            if any('hnscc' in s.lower() or 'safe' in s.lower() or 'pio' in s.lower()
                   for s in slice_ids.unique()):
                return 'safe_hnscc'
        if n_vars >= 55:
            return 'codex_tnbc'

    if 800_000 <= n_cells <= 1_200_000 and n_vars <= 45:
        return 'imc_tnbc'

    return 'unknown'


def check_basic_structure(adata: ad.AnnData, config: Dict) -> List[ValidationResult]:
    """Check basic dataset structure."""
    results = []

    # Cell count
    n_cells = adata.n_obs
    min_cells, max_cells = config.get('expected_cells', (0, float('inf')))
    cell_ok = min_cells <= n_cells <= max_cells
    results.append(ValidationResult(
        passed=cell_ok,
        category="Basic Structure",
        check_name="Cell count",
        message=f"{n_cells:,} cells" + ("" if cell_ok else f" (expected {min_cells:,}-{max_cells:,})"),
        details={'n_cells': n_cells}
    ))

    # Required obs columns
    required_obs = config.get('required_obs', [])
    missing_obs = [col for col in required_obs if col not in adata.obs.columns]
    results.append(ValidationResult(
        passed=len(missing_obs) == 0,
        category="Basic Structure",
        check_name="Required columns",
        message="All present" if not missing_obs else f"Missing: {', '.join(missing_obs)}",
        details={'present': list(adata.obs.columns), 'missing': missing_obs}
    ))

    # Protein count
    n_vars = adata.n_vars
    expected_proteins = config.get('expected_proteins', 0)
    protein_ok = n_vars >= expected_proteins * 0.9  # Allow 10% tolerance
    results.append(ValidationResult(
        passed=protein_ok,
        category="Basic Structure",
        check_name="Protein markers",
        message=f"{n_vars} markers" + ("" if protein_ok else f" (expected ~{expected_proteins})"),
        details={'n_vars': n_vars}
    ))

    return results


def check_spatial_coords(adata: ad.AnnData) -> List[ValidationResult]:
    """Check spatial coordinate validity."""
    results = []

    # Check if spatial coords exist
    has_spatial = 'spatial' in adata.obsm
    if not has_spatial:
        # Try obs columns
        has_spatial = any(col in adata.obs.columns
                         for col in ['X_coord', 'Y_coord', 'centroid_x', 'centroid_y'])

    results.append(ValidationResult(
        passed=has_spatial,
        category="Spatial Coordinates",
        check_name="Coordinates present",
        message="Found" if has_spatial else "Not found"
    ))

    if not has_spatial:
        return results

    # Get coordinates
    if 'spatial' in adata.obsm:
        coords = adata.obsm['spatial']
    else:
        x_col = 'X_coord' if 'X_coord' in adata.obs.columns else 'centroid_x'
        y_col = 'Y_coord' if 'Y_coord' in adata.obs.columns else 'centroid_y'
        coords = np.column_stack([adata.obs[x_col].values, adata.obs[y_col].values])

    # Check for NaN
    nan_count = np.isnan(coords).sum()
    results.append(ValidationResult(
        passed=nan_count == 0,
        category="Spatial Coordinates",
        check_name="No NaN values",
        message="Clean" if nan_count == 0 else f"{nan_count} NaN values found"
    ))

    # Check range (detect tile-level issue)
    x_range = coords[:, 0].max() - coords[:, 0].min()
    y_range = coords[:, 1].max() - coords[:, 1].min()

    # Warning if range is suspiciously small (likely tile-level)
    small_range = x_range < 2000 and y_range < 2000
    results.append(ValidationResult(
        passed=not small_range,
        category="Spatial Coordinates",
        check_name="Coordinate range",
        message=f"X: [{coords[:, 0].min():.0f}, {coords[:, 0].max():.0f}], Y: [{coords[:, 1].min():.0f}, {coords[:, 1].max():.0f}]" +
                (" ⚠️ Small range - possible tile-level coords" if small_range else ""),
        details={'x_range': x_range, 'y_range': y_range, 'possible_tile_level': small_range}
    ))

    return results


def check_celltypes(adata: ad.AnnData, config: Dict) -> List[ValidationResult]:
    """Check celltype annotations."""
    results = []

    # Try main column, then alternatives
    celltype_col = config.get('celltype_col', 'celltype')
    alt_cols = config.get('celltype_alt_cols', ['cell_type', 'celltype', 'cell_type_ori'])

    if celltype_col not in adata.obs.columns:
        # Try alternatives
        for alt in alt_cols:
            if alt in adata.obs.columns:
                celltype_col = alt
                break

    if celltype_col not in adata.obs.columns:
        results.append(ValidationResult(
            passed=False,
            category="Celltype Annotations",
            check_name="Celltype column",
            message=f"Column '{celltype_col}' not found"
        ))
        return results

    celltypes = adata.obs[celltype_col].value_counts()
    n_types = len(celltypes)

    results.append(ValidationResult(
        passed=n_types >= 3,
        category="Celltype Annotations",
        check_name="Celltype diversity",
        message=f"{n_types} cell types found",
        details={'celltypes': dict(celltypes)}
    ))

    # Check for dominant type (>80%)
    max_pct = celltypes.iloc[0] / len(adata) * 100
    results.append(ValidationResult(
        passed=max_pct < 80,
        category="Celltype Annotations",
        check_name="Distribution balance",
        message=f"Largest type: {celltypes.index[0]} ({max_pct:.1f}%)" +
                (" ⚠️ Highly imbalanced" if max_pct >= 80 else "")
    ))

    # Expected celltypes
    expected = config.get('celltypes')
    if expected:
        found = set(celltypes.index)
        missing = set(expected) - found
        extra = found - set(expected)
        results.append(ValidationResult(
            passed=len(missing) <= 2,  # Allow some flexibility
            category="Celltype Annotations",
            check_name="Expected types",
            message=f"Missing: {missing or 'None'}, Extra: {extra or 'None'}"
        ))

    return results


def check_treatment_labels(adata: ad.AnnData, config: Dict) -> List[ValidationResult]:
    """Check treatment/response labels."""
    results = []

    # Check state column
    state_col = config.get('state_col')
    if state_col and state_col in adata.obs.columns:
        states = adata.obs[state_col].value_counts()
        expected_states = set(config.get('state_values', []))
        found_states = set(states.index)

        results.append(ValidationResult(
            passed=expected_states <= found_states,
            category="Treatment Labels",
            check_name="Treatment states",
            message=f"Found: {list(found_states)}",
            details={'state_counts': dict(states)}
        ))

    # Check response label
    response_cols = ['response', 'label', 'Response']
    for col in response_cols:
        if col in adata.obs.columns:
            responses = adata.obs[col].value_counts()
            results.append(ValidationResult(
                passed=True,
                category="Treatment Labels",
                check_name="Response labels",
                message=f"Column '{col}': {dict(responses)}"
            ))
            break

    return results


def check_embeddings(adata: ad.AnnData) -> List[ValidationResult]:
    """Check CartaPA embeddings if present."""
    results = []

    # Check for embeddings
    emb_keys = ['X_cartapa', 'X_cartapa_sparse']
    found_keys = [k for k in emb_keys if k in adata.obsm]

    if not found_keys:
        results.append(ValidationResult(
            passed=True,  # Not a failure, just info
            category="Embeddings",
            check_name="CartaPA embeddings",
            message="Not present (expected before extraction)"
        ))
        return results

    for key in found_keys:
        emb = adata.obsm[key]

        # Shape check
        expected_dim = 128
        shape_ok = emb.shape[1] == expected_dim
        results.append(ValidationResult(
            passed=shape_ok,
            category="Embeddings",
            check_name=f"{key} shape",
            message=f"{emb.shape}" + ("" if shape_ok else f" (expected dim={expected_dim})")
        ))

        # NaN check
        nan_count = np.isnan(emb).sum()
        results.append(ValidationResult(
            passed=nan_count == 0,
            category="Embeddings",
            check_name=f"{key} NaN values",
            message="Clean" if nan_count == 0 else f"{nan_count} NaN values"
        ))

    # Check response probability
    prob_cols = ['node_response_prob', 'response_prob']
    for col in prob_cols:
        if col in adata.obs.columns:
            probs = adata.obs[col].values
            valid_range = np.all((probs >= 0) & (probs <= 1))
            results.append(ValidationResult(
                passed=valid_range,
                category="Embeddings",
                check_name="Response probability range",
                message=f"[{probs.min():.3f}, {probs.max():.3f}]" +
                        ("" if valid_range else " ⚠️ Outside [0,1]")
            ))
            break

    return results


def validate_dataset(
    input_path: str,
    dataset_type: str = 'auto',
    visualize: bool = False,
    output_report: Optional[str] = None
) -> ValidationReport:
    """Run full validation on a dataset."""

    print(f"Loading {input_path}...")
    adata = ad.read_h5ad(input_path)
    print(f"  Shape: {adata.shape}")

    # Detect or use specified type
    if dataset_type == 'auto':
        dataset_type = detect_dataset_type(adata)
        print(f"  Detected type: {dataset_type}")

    config = DATASET_CONFIGS.get(dataset_type, {})

    # Print warnings if any
    for warning in config.get('warnings', []):
        print(f"  ⚠️ Warning: {warning}")

    report = ValidationReport(
        dataset_path=input_path,
        dataset_type=dataset_type
    )

    # Run all checks
    print("\nRunning validation checks...")

    for result in check_basic_structure(adata, config):
        report.add(result)

    for result in check_spatial_coords(adata):
        report.add(result)

    for result in check_celltypes(adata, config):
        report.add(result)

    for result in check_treatment_labels(adata, config):
        report.add(result)

    for result in check_embeddings(adata):
        report.add(result)

    # Print summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    for r in report.results:
        icon = "✅" if r.passed else "❌"
        print(f"{icon} [{r.category}] {r.check_name}: {r.message}")

    print("\n" + "=" * 60)
    status = "✅ ALL CHECKS PASSED" if report.passed else "❌ SOME CHECKS FAILED"
    print(f"Result: {status} ({report.n_passed}/{len(report.results)} passed)")
    print("=" * 60)

    # Save report if requested
    if output_report:
        Path(output_report).parent.mkdir(parents=True, exist_ok=True)
        with open(output_report, 'w') as f:
            f.write(report.to_markdown())
        print(f"\nReport saved to: {output_report}")

    # Visualize if requested
    if visualize:
        try:
            visualize_coordinates(adata, input_path)
        except Exception as e:
            print(f"Visualization failed: {e}")

    return report


def visualize_coordinates(adata: ad.AnnData, input_path: str):
    """Create coordinate distribution visualization."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    # Get coordinates
    if 'spatial' in adata.obsm:
        coords = adata.obsm['spatial']
    else:
        x_col = 'X_coord' if 'X_coord' in adata.obs.columns else 'centroid_x'
        y_col = 'Y_coord' if 'Y_coord' in adata.obs.columns else 'centroid_y'
        coords = np.column_stack([adata.obs[x_col].values, adata.obs[y_col].values])

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # Scatter plot (sample for performance)
    n_sample = min(50000, len(coords))
    idx = np.random.choice(len(coords), n_sample, replace=False)
    axes[0].scatter(coords[idx, 0], coords[idx, 1], s=0.1, alpha=0.3)
    axes[0].set_xlabel('X')
    axes[0].set_ylabel('Y')
    axes[0].set_title('Spatial Distribution (sampled)')

    # Histogram
    axes[1].hist(coords[:, 0], bins=100, alpha=0.5, label='X')
    axes[1].hist(coords[:, 1], bins=100, alpha=0.5, label='Y')
    axes[1].set_xlabel('Coordinate Value')
    axes[1].set_ylabel('Count')
    axes[1].set_title('Coordinate Histograms')
    axes[1].legend()

    plt.tight_layout()

    output_path = input_path.replace('.h5ad', '_coord_check.png')
    plt.savefig(output_path, dpi=150)
    print(f"Visualization saved to: {output_path}")
    plt.close()


def main():
    parser = argparse.ArgumentParser(description='Validate CartaPA datasets')
    parser.add_argument('--input', '-i', required=True, help='Input h5ad file')
    parser.add_argument('--dataset-type', '-t', default='auto',
                       choices=['auto', 'codex_hcc', 'codex_tnbc', 'imc_tnbc', 'safe_hnscc', 'unknown'],
                       help='Dataset type (default: auto-detect)')
    parser.add_argument('--report', '-r', help='Output markdown report path')
    parser.add_argument('--visualize', '-v', action='store_true',
                       help='Generate coordinate visualization')
    parser.add_argument('--quick', '-q', action='store_true',
                       help='Quick mode: basic checks only')
    parser.add_argument('--celltypes-only', action='store_true',
                       help='Only check celltype annotations')

    args = parser.parse_args()

    report = validate_dataset(
        input_path=args.input,
        dataset_type=args.dataset_type,
        visualize=args.visualize,
        output_report=args.report
    )

    # Exit with error if validation failed
    sys.exit(0 if report.passed else 1)


if __name__ == '__main__':
    main()
