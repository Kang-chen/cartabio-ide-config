# Test Datasets

## pasilla

The `experimental-design-statistics` skill uses the **pasilla** dataset as example pilot data for the no-pilot-data workflow path.

- **Source:** Bioconductor package `pasilla` (data package)
- **Original publication:** Brooks et al. 2011, "Conservation of an RNA regulatory map between Drosophila and mammals", *Genome Research*
- **License:** Artistic-2.0
- **Description:** RNA-seq data from Drosophila melanogaster treated and untreated conditions, with 7 samples (4 untreated, 3 treated). Used by `load_example_data()` in `scripts/load_example_data.R` as a realistic pilot dataset for CV estimation and power calculation demonstration.

### Installation

```r
if (!requireNamespace("BiocManager", quietly = TRUE))
    install.packages("BiocManager")
BiocManager::install("pasilla")
```

### Usage in tests

The pasilla dataset is not required by the unit tests in this directory (they use synthetic fixtures). It is required by the example workflow in `SKILL.md` and by `load_example_data()`.

### Data catalog

| Dataset | Format | Samples | Conditions | License |
|---------|--------|---------|------------|---------|
| pasilla | BAM/count matrix | 7 | untreated (4), treated (3) | Artistic-2.0 |
