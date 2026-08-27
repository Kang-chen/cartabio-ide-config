from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from chembl_structure_pipeline import checker, standardizer
from rdkit import Chem

from .schema import DatasetSpec, EndpointSpec

_QUALIFIED_NUMBER = re.compile(
    r"^\s*(?P<op><=|>=|<|>|≤|≥|=)?\s*"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$"
)


def read_table(path: str) -> pd.DataFrame:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(source)
    if suffix in {".tsv", ".txt"}:
        return pd.read_csv(source, sep="\t")
    if suffix in {".sdf", ".sd"}:
        rows: list[dict[str, Any]] = []
        for mol in Chem.SDMolSupplier(str(source), removeHs=False):
            if mol is None:
                rows.append({"smiles": None})
                continue
            row = {name: mol.GetProp(name) for name in mol.GetPropNames()}
            row["smiles"] = Chem.MolToSmiles(mol, isomericSmiles=True)
            rows.append(row)
        return pd.DataFrame(rows)
    if suffix in {".smi", ".smiles"}:
        rows = []
        with source.open() as handle:
            for line in handle:
                parts = line.strip().split()
                if parts:
                    rows.append({"smiles": parts[0], "compound_id": parts[1] if len(parts) > 1 else None})
        return pd.DataFrame(rows)
    raise ValueError(f"Unsupported input format: {suffix}. Use CSV, TSV, SDF, or SMI.")


def parse_interval(value: Any, qualifier: Any = None) -> tuple[float, float, float, str]:
    """Parse an exact or qualified value on the reporting scale.

    Returns (display_value, lower_bound, upper_bound, qualifier_code).
    """
    if pd.isna(value):
        raise ValueError("missing label")
    raw = str(value).strip()
    match = _QUALIFIED_NUMBER.match(raw)
    if not match:
        raise ValueError(f"invalid numeric label {value!r}")
    embedded_op = match.group("op") or ""
    external_op = "" if qualifier is None or pd.isna(qualifier) else str(qualifier).strip()
    op = external_op or embedded_op or "="
    op = {"≤": "<=", "≥": ">="}.get(op, op)
    if external_op and embedded_op and op != {"≤": "<=", "≥": ">="}.get(embedded_op, embedded_op):
        raise ValueError("label and qualifier columns disagree")
    number = float(match.group("value"))
    if not math.isfinite(number):
        raise ValueError("label must be finite")
    if op in {"=", ""}:
        return number, number, number, "exact"
    if op in {">", ">="}:
        return number, number, math.inf, "right"
    if op in {"<", "<="}:
        return number, -math.inf, number, "left"
    raise ValueError(f"unsupported qualifier {op!r}")


def standardize_structure(smiles: Any) -> tuple[str | None, str | None, list[str]]:
    """Return ChEMBL parent SMILES, InChIKey, and structure-check messages."""
    if not isinstance(smiles, str) or not smiles.strip():
        return None, None, ["missing SMILES"]
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None, None, ["RDKit parse failed"]
    messages: list[str] = []
    try:
        for issue in checker.check_molblock(Chem.MolToMolBlock(mol)):
            messages.append(str(issue[-1]))
        normalized = standardizer.standardize_mol(mol)
        parent, excluded = standardizer.get_parent_mol(normalized)
        if excluded or parent is None or parent.GetNumHeavyAtoms() == 0:
            return None, None, messages + ["structure excluded by ChEMBL parent standardization"]
        Chem.SanitizeMol(parent)
        parent_smiles = Chem.MolToSmiles(parent, isomericSmiles=True)
        identity = Chem.MolToInchiKey(parent) or parent_smiles
        return parent_smiles, identity, messages
    except Exception as exc:  # noqa: BLE001 - isolate toolkit failures to the affected row
        return None, None, messages + [f"standardization failed: {type(exc).__name__}"]


def _issue(code: str, message: str, rows: list[int] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if rows:
        item["rows"] = rows[:50]
        item["n_rows"] = len(rows)
    return item


def _class_values(series: pd.Series, endpoint: EndpointSpec) -> tuple[pd.Series, list[int]]:
    bad: list[int] = []
    if endpoint.class_mapping is not None:
        mapping = {str(key): value for key, value in endpoint.class_mapping.items()}
        values = series.map(lambda value: mapping.get(str(value)))
    else:
        values = pd.to_numeric(series, errors="coerce")
    for index, value in values.items():
        if pd.isna(value) or int(value) not in {0, 1} or float(value) != int(value):
            bad.append(int(index))
    return values.astype("Float64"), bad


def prepare_dataset(spec: DatasetSpec, require_labels: bool = True) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = read_table(spec.data_path).copy()
    raw.insert(0, "source_row", np.arange(len(raw), dtype=int))
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    required = [spec.smiles_column]
    if require_labels:
        required.append(spec.endpoint.label_column)
    optional_declared = [
        spec.date_column,
        spec.series_column,
        spec.compound_id_column,
        spec.endpoint.unit_column,
        spec.endpoint.qualifier_column,
        *spec.assay_context_columns,
    ]
    missing = [column for column in required if column not in raw.columns]
    missing_declared = [column for column in optional_declared if column and column not in raw.columns]
    if missing:
        blockers.append(_issue("missing_required_columns", f"Missing required columns: {missing}"))
    if missing_declared:
        blockers.append(_issue("missing_declared_columns", f"Missing declared columns: {missing_declared}"))
    if missing or missing_declared:
        return raw, {
            "status": "blocked",
            "blockers": blockers,
            "warnings": warnings,
            "summary": {"n_input_rows": len(raw), "n_model_rows": 0},
        }

    work = raw.copy()
    work["raw_smiles"] = work[spec.smiles_column]
    standardized = [standardize_structure(value) for value in work[spec.smiles_column]]
    work["smiles"] = [item[0] for item in standardized]
    work["molecule_key"] = [item[1] for item in standardized]
    work["structure_messages"] = [" | ".join(item[2]) for item in standardized]
    invalid_structure = work.index[work["smiles"].isna()].astype(int).tolist()
    if invalid_structure:
        warnings.append(
            _issue(
                "invalid_structures_excluded",
                "Unparseable or excluded structures will not be modeled.",
                invalid_structure,
            )
        )

    context_columns = list(dict.fromkeys(spec.assay_context_columns))
    if spec.endpoint.unit_column and spec.endpoint.unit_column not in context_columns:
        context_columns.append(spec.endpoint.unit_column)
    if context_columns:
        context = work[context_columns].fillna("<missing>").astype(str)
        work["assay_signature"] = context.apply(
            lambda row: "|".join(f"{column}={row[column]}" for column in context_columns), axis=1
        )
        signatures = sorted(work["assay_signature"].unique().tolist())
        if len(signatures) > 1 and not spec.allow_mixed_assays:
            blockers.append(
                _issue(
                    "mixed_assay_context",
                    f"Found {len(signatures)} assay signatures. Harmonize or model them separately.",
                )
            )
    else:
        work["assay_signature"] = "<unspecified>"
        warnings.append(
            _issue(
                "assay_context_unspecified",
                "No assay context columns were declared; cross-assay compatibility cannot be verified.",
            )
        )

    unit_values: list[str] = []
    if spec.endpoint.unit_column:
        unit_values = sorted(
            work[spec.endpoint.unit_column].dropna().astype(str).str.strip().unique().tolist()
        )
        if len(unit_values) > 1:
            blockers.append(_issue("mixed_units", f"Found multiple endpoint units: {unit_values}"))
        if spec.endpoint.unit and unit_values and unit_values[0].casefold() != spec.endpoint.unit.casefold():
            blockers.append(
                _issue(
                    "unit_mismatch",
                    f"Declared unit {spec.endpoint.unit!r} does not match data unit {unit_values[0]!r}.",
                )
            )
    elif require_labels and not spec.endpoint.unit:
        warnings.append(_issue("unit_unspecified", "Endpoint units were not declared."))

    label_errors: list[int] = []
    if require_labels:
        if spec.endpoint.task == "classification":
            if spec.endpoint.qualifier_column:
                nonempty = work[spec.endpoint.qualifier_column].dropna().astype(str).str.strip()
                if (nonempty != "").any():
                    blockers.append(_issue("qualified_class_labels", "Classification labels cannot be censored."))
            values, label_errors = _class_values(work[spec.endpoint.label_column], spec.endpoint)
            work["label"] = values.astype(float)
            work["lower_bound"] = work["label"]
            work["upper_bound"] = work["label"]
            work["qualifier"] = "exact"
            observed = sorted(work.loc[~work.index.isin(label_errors), "label"].dropna().unique())
            if observed != [0.0, 1.0]:
                blockers.append(
                    _issue("classification_requires_two_classes", f"Observed mapped classes are {observed}.")
                )
        else:
            parsed = []
            for index, row in work.iterrows():
                qualifier = row[spec.endpoint.qualifier_column] if spec.endpoint.qualifier_column else None
                try:
                    parsed.append(parse_interval(row[spec.endpoint.label_column], qualifier))
                except ValueError:
                    parsed.append((math.nan, math.nan, math.nan, "invalid"))
                    label_errors.append(int(index))
            work["label"] = [item[0] for item in parsed]
            work["lower_bound"] = [item[1] for item in parsed]
            work["upper_bound"] = [item[2] for item in parsed]
            work["qualifier"] = [item[3] for item in parsed]
    else:
        work["label"] = np.nan
        work["lower_bound"] = np.nan
        work["upper_bound"] = np.nan
        work["qualifier"] = "unlabelled"

    if label_errors:
        blockers.append(_issue("invalid_labels", "Labels could not be parsed or mapped explicitly.", label_errors))

    if spec.date_column:
        work["measurement_date"] = pd.to_datetime(work[spec.date_column], errors="coerce", utc=True)
        bad_dates = work.index[work[spec.date_column].notna() & work["measurement_date"].isna()].tolist()
        if bad_dates:
            blockers.append(_issue("invalid_dates", "Declared dates could not be parsed.", bad_dates))
    else:
        work["measurement_date"] = pd.NaT

    work["identity_key"] = work["molecule_key"].astype(str) + "||" + work["assay_signature"]
    valid = work[work["smiles"].notna() & ~work.index.isin(label_errors)].copy()

    conflicts = find_replicate_conflicts(valid, spec.endpoint.task)
    if conflicts:
        blockers.append(
            _issue(
                "contradictory_replicates",
                "Some replicate labels cannot be reconciled within the same molecule and assay.",
                conflicts,
            )
        )

    if require_labels and len(valid) < 20:
        blockers.append(_issue("insufficient_data", f"Only {len(valid)} modelable rows; at least 20 are required."))
    elif require_labels and len(valid) < 50:
        warnings.append(_issue("small_dataset", f"Only {len(valid)} rows; prospective estimates will be unstable."))

    n_changed = int(
        sum(
            str(raw_value).strip() != str(std_value)
            for raw_value, std_value in zip(work["raw_smiles"], work["smiles"])
            if pd.notna(std_value)
        )
    )
    summary = {
        "n_input_rows": len(raw),
        "n_model_rows": len(valid),
        "n_invalid_structures": len(invalid_structure),
        "n_standardized_changed": n_changed,
        "n_unique_molecules": int(valid["molecule_key"].nunique()),
        "n_assay_signatures": int(valid["assay_signature"].nunique()),
        "n_replicate_groups": int((valid.groupby("identity_key").size() > 1).sum()) if len(valid) else 0,
        "n_censored": int((valid["qualifier"].isin(["left", "right"])).sum()),
        "unit_values": unit_values,
    }
    audit = {
        "status": "blocked" if blockers else "ready",
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary,
    }
    return valid.reset_index(drop=True), audit


def find_replicate_conflicts(df: pd.DataFrame, task: str) -> list[int]:
    conflicts: list[int] = []
    for _, group in df.groupby("identity_key", sort=False):
        if task == "classification":
            if group["label"].nunique(dropna=True) > 1:
                conflicts.extend(group["source_row"].astype(int).tolist())
            continue
        exact = group[group["qualifier"] == "exact"]["label"].to_numpy(float)
        right = group[group["qualifier"] == "right"]["lower_bound"].to_numpy(float)
        left = group[group["qualifier"] == "left"]["upper_bound"].to_numpy(float)
        if exact.size:
            if (right.size and np.any(exact[:, None] < right[None, :])) or (
                left.size and np.any(exact[:, None] > left[None, :])
            ):
                conflicts.extend(group["source_row"].astype(int).tolist())
        elif right.size and left.size and float(np.max(right)) > float(np.min(left)):
            conflicts.extend(group["source_row"].astype(int).tolist())
    return sorted(set(conflicts))


def aggregate_replicates(df: pd.DataFrame, task: str) -> tuple[pd.DataFrame, float | None]:
    """Aggregate only after split assignment, within molecule and assay identity."""
    rows: list[dict[str, Any]] = []
    replicate_stds: list[float] = []
    for identity_key, group in df.groupby("identity_key", sort=False):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "identity_key": identity_key,
            "molecule_key": first["molecule_key"],
            "smiles": first["smiles"],
            "assay_signature": first["assay_signature"],
            "source_rows": ",".join(str(value) for value in group["source_row"].astype(int)),
            "n_replicates": len(group),
            "measurement_date": group["measurement_date"].min(),
        }
        if task == "classification":
            labels = group["label"].dropna().astype(int).unique()
            if len(labels) != 1:
                raise ValueError(f"Conflicting class labels for {identity_key}")
            value = float(labels[0])
            row.update(label=value, lower_bound=value, upper_bound=value, qualifier="exact")
        else:
            exact = group[group["qualifier"] == "exact"]["label"].to_numpy(float)
            if exact.size:
                value = float(np.median(exact))
                if exact.size > 1:
                    replicate_stds.append(float(np.std(exact, ddof=1)))
                row.update(label=value, lower_bound=value, upper_bound=value, qualifier="exact")
            else:
                lower = float(group["lower_bound"].max())
                upper = float(group["upper_bound"].min())
                if lower > upper:
                    raise ValueError(f"Contradictory censoring intervals for {identity_key}")
                if math.isfinite(lower) and math.isfinite(upper):
                    value, qualifier = (lower + upper) / 2.0, "interval"
                elif math.isfinite(lower):
                    value, qualifier = lower, "right"
                else:
                    value, qualifier = upper, "left"
                row.update(label=value, lower_bound=lower, upper_bound=upper, qualifier=qualifier)
        if "outer_split" in group.columns:
            row["outer_split"] = first["outer_split"]
        rows.append(row)
    noise = float(np.median(replicate_stds)) if replicate_stds else None
    return pd.DataFrame(rows), noise
