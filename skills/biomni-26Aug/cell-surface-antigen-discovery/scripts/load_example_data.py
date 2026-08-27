#!/usr/bin/env python3
"""Demo inputs for the cell-surface target discovery skill.

The bundled demo runs on **lung adenocarcinoma (LUAD)**, which has ~0.8M whole-cell
single-cell cells across many atlases in the CZ CELLxGENE Census — enough to exercise the
multi-atlas consensus engine out of the box. Step 2 of the Standard Workflow pulls live
Census expression for the surfaceome genes; no expression values are bundled or simulated.

Census splits lung cancer by subtype — this example targets `lung adenocarcinoma`
specifically (the largest label, and the subtype where the validated ADC/bispecific
antigens concentrate), NOT the umbrella `non-small cell lung carcinoma`. Pass a list to
`disease_label` (e.g. ['lung adenocarcinoma', 'non-small cell lung carcinoma']) to union
related labels; keep squamous separate to preserve adenocarcinoma specificity.

The skill is tumor-agnostic: set `disease_label` to any verified Census tumor label (see
references/census_atlas_guide.md). For tumors whose Census coverage is single-nucleus-only
or thin, supply a curated whole-cell atlas via the own-`.h5ad` input instead of Census.
"""

import os

import pandas as pd

from surfaceome_filter import load_surfaceome

_HERE = os.path.dirname(os.path.abspath(__file__))
_KNOWN_CSV = os.path.join(_HERE, "..", "references", "known_surface_targets.csv")

DEMO_DISEASE_LABEL = "lung adenocarcinoma"   # Census-rich, whole-cell, multi-atlas
DEMO_CENSUS_VERSION = "2025-11-08"


def load_known_targets(path=_KNOWN_CSV):
    df = pd.read_csv(path)
    df["gene_symbol"] = df["gene_symbol"].astype(str).str.strip()
    return df


def load_demo_inputs(disease_label=DEMO_DISEASE_LABEL, include_in_silico=True):
    """Return the demo input bundle for the Standard Workflow Step 2."""
    surfaceome = load_surfaceome(include_in_silico=include_in_silico)
    known = load_known_targets()
    print(f"✓ Inputs loaded: {len(surfaceome)} surface genes, {len(known)} known targets")
    print(f"  Disease: {disease_label} | Census version: {DEMO_CENSUS_VERSION}")
    return {
        "disease_label": disease_label,
        "census_version": DEMO_CENSUS_VERSION,
        "surfaceome": surfaceome,
        "known_targets": known,
    }


if __name__ == "__main__":
    inp = load_demo_inputs()
    print(inp["surfaceome"].head())
    print(inp["known_targets"][["gene_symbol", "modality", "recall_core"]].head(10))
