---
name: tcga-survival-analysis
description: Perform survival analysis on TCGA cancer data using cBioPortal API and lifelines. Use when user wants to analyze gene expression and survival outcomes, create Kaplan-Meier curves, run Cox proportional hazards models, or study specific genes (like TP53, KRAS, EGFR) in cancer cohorts (LUAD, LUSC, BRCA, etc.). Triggers on keywords like "TCGA", "survival analysis", "Kaplan-Meier", "hazard ratio", or cancer type abbreviations.
---

# TCGA Survival Analysis

Perform survival analysis on TCGA data using cBioPortal API + lifelines.

## Step 1: Environment Check

Before starting the analysis, verify that required dependencies are available:

```bash
# Check required packages
python -c "import pandas, numpy, matplotlib, requests, lifelines; print('All dependencies OK')"
```

**Dependency check strategy (by priority):**

1. Check the currently activated environment (or user-specified environment)
2. Missing packages → Install with `uv pip install` first
3. If uv unavailable → Use `pip install`
4. If installation fails or conflicts → Only then create a new conda environment

**Required dependencies:**
- pandas
- numpy  
- matplotlib
- requests
- lifelines

To install missing packages:

```bash
# Prefer uv (faster)
uv pip install lifelines

# Or use pip
pip install lifelines
```

## Step 2: Quick Start

```python
import pandas as pd
import requests
from lifelines import KaplanMeierFitter
from lifelines.statistics import logrank_test

# cBioPortal API
CBIOPORTAL_API = "https://www.cbioportal.org/api"
study_id = "luad_tcga_pan_can_atlas_2018"  # LUAD Pan-Cancer Atlas

# Fetch EGFR expression data
# ... (see workflow.md for details)

# Perform Kaplan-Meier analysis
kmf = KaplanMeierFitter()
kmf.fit(time, event, label='Gene High')
kmf.plot_survival_function()
```

## Workflow

See [workflow.md](references/workflow.md) for detailed workflow, including:
1. **Environment check** - Verify dependencies, install as needed
2. **Data download** - Fetch expression and clinical data from cBioPortal
3. **Data processing** - Merge expression and survival data
4. **Survival analysis** - Kaplan-Meier analysis and Log-rank test
5. **Visualization** - Survival curves and expression distribution plots

## Key Functions

| Module      | Function                        |
| ----------- | ------------------------------- |
| `requests`  | cBioPortal API data retrieval   |
| `lifelines` | Kaplan-Meier and Cox regression |
| `pandas`    | Data processing and merging     |

## Supported Cancer Types

Common cBioPortal TCGA study IDs:
- `luad_tcga_pan_can_atlas_2018` - Lung Adenocarcinoma (LUAD)
- `lusc_tcga_pan_can_atlas_2018` - Lung Squamous Cell Carcinoma (LUSC)
- `brca_tcga_pan_can_atlas_2018` - Breast Cancer (BRCA)
- `coadread_tcga_pan_can_atlas_2018` - Colorectal Cancer

## Troubleshooting

- **Network issues**: cBioPortal API requires stable network connection
- **Gene names**: Use Entrez Gene ID (e.g., EGFR = 1956)
- **Survival data**: Check if OS_MONTHS and OS_STATUS columns exist
