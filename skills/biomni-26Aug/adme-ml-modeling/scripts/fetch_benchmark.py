"""Fetch real, public ADME benchmarks (no credentials) for the default demonstration.

The headline demonstration of this skill trains on **real measured** assay data, not on the
synthetic labels that ``make_example_data.py`` produces (those are an offline smoke test only).
This module downloads real single-endpoint benchmarks from Harvard Dataverse -- the same files
Therapeutics Data Commons (TDC) distributes -- using only the Python standard library, so it adds
**no dependency** to the pinned runtime (``pyproject.toml``/``uv.lock`` are unchanged).

Licences (stated; re-verify before redistributing):

- ``lipophilicity_astrazeneca`` -- real experimental logD7.4 (octanol/water, pH 7.4) for 4,200
  compounds. Source: AstraZeneca (2016) publicly disclosed in-vitro DMPK data, popularised by
  MoleculeNet (Wu et al., Chem. Sci. 2018), obtained through TDC. TDC lists it as **"Not Specified.
  CC BY 4.0"** -- the upstream AstraZeneca release did not state a licence and CC BY 4.0 is the label
  TDC applies; attribute AstraZeneca (2016) and MoleculeNet.
- ``solubility_aqsoldb`` -- real aqueous solubility log(mol/L) for 9,982 compounds. Source:
  AqSolDB (Sorkun et al., Sci. Data 2019), obtained through TDC. Documented **CC BY 4.0**.

TDC aggregates many sources and applies CC BY-NC-SA 4.0 as a catch-all; the dataset-specific
documentation for both datasets above is CC BY 4.0. Attribute the original authors when using or
redistributing, and re-verify the licence if the upstream record changes.
"""
from __future__ import annotations

import argparse
import io
import urllib.request
from pathlib import Path
from typing import Any

import pandas as pd

_DATAVERSE = "https://dataverse.harvard.edu/api/access/datafile/{id}"

# Registry of real single-endpoint ADME benchmarks. Each carries the exact Harvard Dataverse
# file id TDC uses, the modelling framing, and its licence + citation so provenance travels with
# the data. logD/logS are already log-scale physical quantities that can be negative, so they are
# modelled with ``scale="linear"`` (NOT ``log10``, which would exponentiate them).
BENCHMARKS: dict[str, dict[str, Any]] = {
    "lipophilicity_astrazeneca": {
        "dataverse_id": 4259595,
        "endpoint": "logD",
        "unit": "logD (log-ratio, octanol/water, pH 7.4)",
        "task": "regression",
        "scale": "linear",
        "assay": "AstraZeneca_logD7.4",
        "license": "CC BY 4.0",
        "license_note": (
            "TDC lists this dataset as 'Not Specified. CC BY 4.0': the upstream AstraZeneca (2016) "
            "release did not state a licence, and CC BY 4.0 is the label applied by Therapeutics "
            "Data Commons. Attribute AstraZeneca (2016) and MoleculeNet (Wu et al., 2018)."
        ),
        "citation": (
            "AstraZeneca (2016) publicly disclosed in-vitro DMPK data; via MoleculeNet "
            "(Wu et al., Chem. Sci. 2018); obtained through Therapeutics Data Commons."
        ),
    },
    "solubility_aqsoldb": {
        "dataverse_id": 4259610,
        "endpoint": "logS",
        "unit": "log(mol/L) aqueous solubility",
        "task": "regression",
        "scale": "linear",
        "assay": "AqSolDB_solubility",
        "license": "CC BY 4.0",
        "license_note": (
            "The original AqSolDB publication (Sorkun et al., Sci. Data 2019) is CC BY 4.0 and TDC "
            "also lists CC BY 4.0. Attribute Sorkun et al. (2019)."
        ),
        "citation": "AqSolDB (Sorkun et al., Sci. Data 2019); obtained through Therapeutics Data Commons.",
    },
}


def _download_tab(dataverse_id: int, timeout: int = 120) -> pd.DataFrame:
    """Download a TDC single-pred ``.tab`` (Drug_ID, Drug, Y) via stdlib urllib.

    ``urlopen`` follows the Dataverse 303 redirect to storage automatically, so no third-party
    HTTP client is required.
    """
    url = _DATAVERSE.format(id=dataverse_id)
    request = urllib.request.Request(url, headers={"User-Agent": "adme-ml-modeling-skill/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - trusted host
        raw = response.read()
    frame = pd.read_csv(io.BytesIO(raw), sep="\t")
    return frame.rename(columns={"Drug_ID": "compound_id", "Drug": "smiles", "Y": "value"})[
        ["compound_id", "smiles", "value"]
    ]


def fetch_benchmark(
    name: str, out_path: str | Path | None = None, timeout: int = 120
) -> dict[str, Any]:
    """Fetch a real benchmark, print provenance/licence, and (optionally) write a skill CSV.

    Returns a metadata dict. When ``out_path`` is None the parsed frame is returned under
    ``"frame"`` so callers (e.g. the demo-set builder) can use it without touching disk.
    """
    key = name.strip().lower()
    if key not in BENCHMARKS:
        raise ValueError(f"unknown benchmark {name!r}; choices: {sorted(BENCHMARKS)}")
    meta = BENCHMARKS[key]
    frame = _download_tab(int(meta["dataverse_id"]), timeout=timeout)
    out = frame.rename(columns={"value": meta["endpoint"]}).copy()
    out["assay"] = meta["assay"]
    out["unit"] = meta["unit"]
    result: dict[str, Any] = {
        "name": key,
        "n": int(len(out)),
        **{
            k: meta[k]
            for k in ("endpoint", "unit", "task", "scale", "assay", "license", "license_note", "citation")
        },
    }
    print(
        f"fetch_benchmark: {key} n={result['n']} endpoint={meta['endpoint']} "
        f"task={meta['task']} scale={meta['scale']} license={meta['license']}"
    )
    print(f"  source: {meta['citation']}")
    print(f"  licence note: {meta['license_note']}")
    if out_path is not None:
        out_path = Path(out_path)
        out.to_csv(out_path, index=False)
        result["path"] = str(out_path)
        print(f"  wrote {len(out)} rows -> {out_path}")
    else:
        result["frame"] = out
    return result


def dataset_spec_for(name: str, data_path: str) -> dict[str, Any]:
    """Return a skill ``DatasetSpec`` dict for a fetched benchmark CSV (no date -> scaffold split)."""
    meta = BENCHMARKS[name.strip().lower()]
    return {
        "data_path": data_path,
        "smiles_column": "smiles",
        "assay_context_columns": ["assay"],
        "endpoint": {
            "label_column": meta["endpoint"],
            "task": meta["task"],
            "scale": meta["scale"],
            "unit": meta["unit"],
            "unit_column": "unit",
        },
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch a real public ADME benchmark.")
    ap.add_argument("--name", default="lipophilicity_astrazeneca", choices=sorted(BENCHMARKS))
    ap.add_argument("--out", default="lipophilicity_astrazeneca.csv")
    args = ap.parse_args()
    fetch_benchmark(args.name, args.out)
