# Biomni Resources Used by This Skill

This skill is built on resources available in the Biomni environment and scoped
from the platform resource catalog. Verify required packages and functions
directly at runtime.

## Queryable databases

- **RCSB PDB** (one of Biomni's 17 queryable databases) — source of all
  co-crystal coordinates and ligand/entry metadata. Accessed via the public REST
  and search endpoints in `fetch_structure.py`:
  - coordinates: `files.rcsb.org/download/{PDB}.pdb`
  - entry metadata: `data.rcsb.org/rest/v1/core/entry/{PDB}`
  - ligand chem-comp: `data.rcsb.org/rest/v1/core/chemcomp/{CODE}`
  - text/chem search: `search.rcsb.org/rcsbsearch/v2/query`

## Agent tools

- **LiteratureSearch** (platform tool) — used in Step 3 to ground the pocket in
  real, cited literature. Structured records are written by the platform to
  `/mnt/results/execution_trace/references.jsonl`. The skill passes the returned
  references into the report and **never fabricates citations**.
- **predict_admet_properties** (Biomni pharmacology tool) — suggested as a next
  step for the ligand (from its SMILES, which RCSB provides).

## Preinstalled packages

Python (all present in the Biomni environment):
- **biopython** — structure parsing + heavy-atom geometry (contacts, H-bonds).
- **numpy** — vectorized distance matrices.
- **rdkit** — ligand bond perception and fragment/functional-group assignment.
- **matplotlib** — the 2D data figures (interaction diagram, distance chart, heatmap)
  and the 3D fallback.
- **reportlab** — the Phylo-branded PDF (via the pdf-report-generation skill).
- **pypdf** — PDF validation.
- **pillow** (PIL) — image sizing for figure embedding.

CLI / other:
- **PyMOL** (pymol-open-source, conda) — publication-quality 3D pocket render;
  optional, with a matplotlib fallback.
- **AutoDock Vina** (`vina`) — suggested next step for docking analogs.

## Related skills

- **pdf-report-generation** — the report builder follows this skill's Phylo brand
  palette, typography, layout, centering, and validation rules.
- **phylo-create-skill** — used to author, test, and package this skill.

## Deliberately NOT used

- No HPC/GPU tools are required (structure prediction, docking, MD are *next
  steps*, not part of contact mapping).
- No data-lake datasets are needed — the analysis is driven entirely by the
  supplied structure and RCSB metadata.

## How to re-check availability

From a Biomni session, verify the documented module and package directly:
```python
from importlib.util import find_spec
from biomni.tool.pharmacology import predict_admet_properties

assert find_spec("rdkit")
```
If a resource is missing in a given environment, install per the SKILL.md
Installation table (`uv pip install ...`, or conda for PyMOL).
