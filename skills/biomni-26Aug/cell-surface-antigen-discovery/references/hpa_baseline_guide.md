# Normal-Tissue Baseline from the Human Protein Atlas (dual signal)

`scripts/hpa_baseline.py` builds the normal-tissue baseline that feeds the therapeutic-index
safety score. It replaces the Open Targets baseline that `annotate_targets.py` used to emit,
because **Open Targets Platform v4 removed `Target.expressions`**.

## Why this module exists

The safety axis needs, per gene, an estimate of expression in *vital normal tissues* at both
the RNA and protein level. HPA is the canonical public source for both:

- **RNA** — consensus normalized transcript abundance (nTPM) across ~50 tissues.
- **Protein** — immunohistochemistry (IHC) staining intensity (Not detected / Low / Medium /
  High) across ~60 tissues and their cell types.

`normal_tissue_safety.compute_therapeutic_index()` takes the **conservative min** of a
protein-derived and an RNA-derived safety per gene, so supplying both restores the intended
dual-signal therapeutic index.

## Data sources (verified endpoints)

| Signal | URL | Status | Approx size | Key columns |
|--------|-----|--------|-------------|-------------|
| RNA (consensus nTPM) | `https://www.proteinatlas.org/download/tsv/rna_tissue_consensus.tsv.zip` | HTTP 200 | ~5.3 MB | Gene, Gene name, Tissue, nTPM |
| Protein (IHC level) | `https://www.proteinatlas.org/download/tsv/normal_ihc_data.tsv.zip` | HTTP 200 | ~5.7 MB | Gene, Gene name, Tissue, Cell type, Level, Reliability |

> ⚠️ **Do not use `https://www.proteinatlas.org/download/tsv/normal_tissue.tsv.zip`** — this
> path now returns **HTTP 404** (a ~117 KB HTML error page, not a zip). `hpa_baseline.py`
> guards against this by checking that the download starts with the zip magic bytes `PK`.

**Per-gene JSON fallback** (only when a bulk file is unavailable, e.g. an offline mirror):
`https://www.proteinatlas.org/ENSGXXXXXXXXXXX.json` — the consensus tissue nTPM block is parsed
for RNA. Requires an `{gene_symbol: ENSG...}` map passed as `ensembl_ids=`.

For a complete, reproducible per-gene baseline over an arbitrary candidate list,
use the Human Protein Atlas bulk download in this module. Downloads are cached under
`<output_dir>/hpa_cache/` and reused.

## Output schema (exactly what the safety module consumes)

`target_baseline_expression_long.csv`:

| Column | Source | Used by |
|--------|--------|---------|
| `gene_symbol` | HPA "Gene name" | join key |
| `tissue` | HPA tissue name | `_is_vital` keyword match |
| `organs` | mirror of `tissue` (HPA tissue names already carry organ words) | `_is_vital` keyword match |
| `rna_value` | consensus nTPM | `_rna_safety` (<1 → 1.0, <10 → 0.7, <50 → 0.4, else 0.1) |
| `rna_level` | `NaN` | (consensus RNA has no discrete level) |
| `protein_level` | IHC Level string | `_level_to_ord` → `_protein_safety` ({Not detected:1.0, Low:0.7, Medium:0.4, High:0.1}) |

**IHC aggregation:** HPA IHC has one row per (gene, tissue, cell type). The builder takes the
**MAX** level across cell types within a tissue — conservative for on-target/off-tumor
toxicity, since one positive vital-tissue cell type is a liability. Levels are mapped
{not detected:0, low:1, medium:2, high:3}; ambiguous rows (N/A, Ascending/Descending, Not
representative) are dropped before relabeling.

## Usage

```python
from scripts.hpa_baseline import build_hpa_baseline_long

# bulk download + cache on first run, then restrict to your candidate universe
build_hpa_baseline_long(candidate_genes, output_dir="results")

# or point at pre-downloaded TSVs (skips download)
build_hpa_baseline_long(candidate_genes, output_dir="results",
                        hpa_rna_tsv="rna_tissue_consensus.tsv",
                        hpa_ihc_tsv="normal_ihc_data.tsv")
```

Then:
```python
from scripts.normal_tissue_safety import compute_therapeutic_index
ti = compute_therapeutic_index(ann, output_dir="results",
                               baseline_long_path="results/target_baseline_expression_long.csv")
```

## Provenance / no-fabrication rules

- Gene with **RNA only** → RNA-based safety (protein unassessed).
- Gene with **IHC only** → protein-based safety (RNA unassessed).
- Gene with **neither** → absent from the baseline → `safety_score = NaN` → scorer applies the
  **neutral 0.7** default and flags `safety_unassessed=True`.
- Never impute or fabricate a normal-tissue value. Report the neutral-default count honestly
  (see the Safety Honesty section of the SKILL and `scoring_methodology.md`).

## What is unchanged

This module changes only the **source** of the baseline. The vital-organ keyword list, nTPM
thresholds, IHC→ordinal mapping, and the `min(protein_safety, rna_safety)` logic all live in
`normal_tissue_safety.py` and are source-agnostic.
