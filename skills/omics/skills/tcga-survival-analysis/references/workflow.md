# TCGA Survival Analysis Workflow

Detailed workflow for TCGA data survival analysis.

## Step 1: Environment Check

**Check strategy (by priority):**
1. Check if the current environment (or user-specified environment) has required packages
2. Missing packages → Install with `uv pip install` first
3. If uv unavailable → Use `pip install`
4. If installation fails/conflicts → Only then create a new conda environment

```bash
# Check all dependencies
python -c "
import sys
missing = []
for pkg in ['pandas', 'numpy', 'matplotlib', 'requests', 'lifelines']:
    try:
        __import__(pkg)
    except ImportError:
        missing.append(pkg)
if missing:
    print(f'Missing: {missing}')
    sys.exit(1)
else:
    print('All dependencies OK')
"
```

To install missing packages:

```bash
# Prefer uv (faster)
uv pip install lifelines

# Or use pip
pip install lifelines
```

**Only if above methods fail**, create a new environment:

```bash
conda env create -f .agent/skills/tcga-survival-analysis/environment.yml
conda activate tcga-survival
```

## Step 2: Environment Setup

```python
import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import requests
from lifelines import KaplanMeierFitter
from lifelines.statistics import logrank_test

# Create output directories
os.makedirs('results', exist_ok=True)
os.makedirs('figures', exist_ok=True)
```

## Step 3: Download Data from cBioPortal

```python
CBIOPORTAL_API = "https://www.cbioportal.org/api"

# TCGA study ID (using LUAD as example)
study_id = "luad_tcga_pan_can_atlas_2018"

# Get sample list
samples_resp = requests.get(
    f"{CBIOPORTAL_API}/studies/{study_id}/samples",
    headers={"Accept": "application/json"}
)
sample_ids = [s['sampleId'] for s in samples_resp.json()]

# Get gene expression data (using EGFR as example, Entrez ID = 1956)
mrna_profile = f"{study_id}_rna_seq_v2_mrna"
gene_resp = requests.post(
    f"{CBIOPORTAL_API}/molecular-profiles/{mrna_profile}/molecular-data/fetch",
    json={"entrezGeneIds": [1956], "sampleIds": sample_ids},
    headers={"Accept": "application/json", "Content-Type": "application/json"}
)
gene_data = gene_resp.json()

# Get clinical data
clinical_resp = requests.get(
    f"{CBIOPORTAL_API}/studies/{study_id}/clinical-data",
    params={"clinicalDataType": "PATIENT"},
    headers={"Accept": "application/json"}
)
```

## Step 4: Data Processing

```python
# Create expression dataframe
expr_df = pd.DataFrame([{
    'sample_id': d['sampleId'],
    'patient_id': d['patientId'],
    'gene_expr': d['value']
} for d in gene_data])

# Process clinical data
clinical_records = {}
for item in clinical_resp.json():
    pid = item['patientId']
    if pid not in clinical_records:
        clinical_records[pid] = {}
    clinical_records[pid][item['clinicalAttributeId']] = item['value']

clinical_df = pd.DataFrame.from_dict(clinical_records, orient='index')
clinical_df = clinical_df.reset_index().rename(columns={'index': 'patient_id'})

# Merge data
merged_df = expr_df.merge(clinical_df, on='patient_id', how='inner')

# Process survival data
merged_df['OS_time'] = pd.to_numeric(merged_df['OS_MONTHS'], errors='coerce')
merged_df['OS_event'] = merged_df['OS_STATUS'].apply(
    lambda x: 1 if '1:DECEASED' in str(x).upper() or 'DECEASED' in str(x).upper() else 0
)
merged_df = merged_df.dropna(subset=['OS_time', 'OS_event'])
merged_df = merged_df[merged_df['OS_time'] > 0]
```

## Step 5: Survival Analysis

```python
# Group by median expression
median_expr = merged_df['gene_expr'].median()
merged_df['group'] = (merged_df['gene_expr'] >= median_expr).map({True: 'High', False: 'Low'})

high_group = merged_df[merged_df['group'] == 'High']
low_group = merged_df[merged_df['group'] == 'Low']

# Kaplan-Meier fitting
kmf_high = KaplanMeierFitter()
kmf_low = KaplanMeierFitter()

kmf_high.fit(high_group['OS_time'] / 12, high_group['OS_event'], label='High')
kmf_low.fit(low_group['OS_time'] / 12, low_group['OS_event'], label='Low')

# Log-rank test
results = logrank_test(
    high_group['OS_time'], low_group['OS_time'],
    high_group['OS_event'], low_group['OS_event']
)
print(f"Log-rank p-value: {results.p_value:.4e}")
```

## Step 6: Visualization

```python
fig, ax = plt.subplots(figsize=(10, 7))
kmf_high.plot_survival_function(ax=ax, ci_show=True, color='#E74C3C')
kmf_low.plot_survival_function(ax=ax, ci_show=True, color='#3498DB')

ax.set_xlabel('Time (Years)')
ax.set_ylabel('Overall Survival Probability')
ax.set_title('Gene Expression and Overall Survival')
ax.legend(loc='lower left')

plt.savefig('figures/survival_km.png', dpi=300, bbox_inches='tight')
```

## Common Entrez Gene IDs

| Gene | Entrez ID |
| ---- | --------- |
| EGFR | 1956      |
| TP53 | 7157      |
| KRAS | 3845      |
| BRAF | 673       |
| ALK  | 238       |
| MET  | 4233      |
| ROS1 | 6098      |


## Common Issues

1. **API timeout**: Increase requests timeout parameter
2. **No expression data**: Verify molecular profile ID is correct
3. **Missing survival data**: Check OS_MONTHS/OS_STATUS column names
