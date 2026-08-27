from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from molfeat.trans import MoleculeTransformer
from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold
from sklearn.cluster import KMeans
from sklearn.model_selection import GroupKFold, GroupShuffleSplit, ShuffleSplit, TimeSeriesSplit
from splito import KMeansSplit, MaxDissimilaritySplit, MOODSplitter, ScaffoldSplit

from .data import read_table, standardize_structure
from .schema import DatasetSpec, RunConfig


def resolve_split(spec: DatasetSpec, config: RunConfig) -> str:
    if config.split != "auto":
        return config.split
    return "time" if spec.date_column else "scaffold"


def _ecfp(smiles: list[str]) -> np.ndarray:
    transformer = MoleculeTransformer("ecfp", n_jobs=1, dtype="float32")
    return np.asarray(transformer(smiles), dtype=np.float32)


def _scaffold(smiles: str) -> str:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return "<invalid>"
    scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol, includeChirality=True)
    return scaffold or "<acyclic>"


def scaffold_groups(smiles: list[str], n_fallback_groups: int = 5, seed: int = 0) -> np.ndarray:
    groups = np.asarray([_scaffold(value) for value in smiles], dtype=object)
    if len(set(groups)) > 1:
        return groups
    n_clusters = min(max(2, n_fallback_groups), max(2, len(smiles) // 3))
    if len(smiles) < 6:
        return np.arange(len(smiles)).astype(str)
    labels = KMeans(n_clusters=n_clusters, random_state=seed, n_init=10).fit_predict(_ecfp(smiles))
    return np.asarray([f"cluster-{value}" for value in labels], dtype=object)


def assign_outer_split(
    rows: pd.DataFrame, spec: DatasetSpec, config: RunConfig
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Assign raw observations before replicate aggregation.

    For temporal evaluation, future re-measurements of molecules already present before
    the cutoff are purged rather than leaked into training or counted as novel test cases.
    """
    strategy = resolve_split(spec, config)
    assigned = rows.copy()
    assigned["outer_split"] = "excluded"
    metadata: dict[str, Any] = {"strategy": strategy, "test_fraction_requested": config.test_fraction}

    if strategy == "time":
        if not spec.date_column or assigned["measurement_date"].isna().any():
            raise ValueError("time split requires a complete, valid date column")
        ordered_dates = assigned["measurement_date"].sort_values().to_numpy()
        cut_position = min(len(ordered_dates) - 1, max(1, int((1 - config.test_fraction) * len(ordered_dates))))
        cutoff = pd.Timestamp(ordered_dates[cut_position])
        pre_mask = assigned["measurement_date"] < cutoff
        post_mask = ~pre_mask
        seen = set(assigned.loc[pre_mask, "identity_key"])
        novel_post = post_mask & ~assigned["identity_key"].isin(seen)
        retests = post_mask & assigned["identity_key"].isin(seen)
        assigned.loc[pre_mask, "outer_split"] = "train"
        assigned.loc[novel_post, "outer_split"] = "test"
        assigned.loc[retests, "outer_split"] = "purged_retest"
        metadata.update(
            cutoff=cutoff.isoformat(),
            n_purged_retests=int(retests.sum()),
            rule="train before cutoff; test novel identities on/after cutoff; purge later retests",
        )
    else:
        unique = assigned.drop_duplicates("identity_key", keep="first").reset_index(drop=True)
        smiles = unique["smiles"].tolist()
        if strategy == "random":
            if spec.series_column:
                groups = unique[spec.series_column].fillna(unique["identity_key"]).astype(str)
                splitter = GroupShuffleSplit(
                    n_splits=1, test_size=config.test_fraction, random_state=config.seed
                )
                train_idx, test_idx = next(splitter.split(unique, groups=groups))
                metadata["grouping"] = spec.series_column
            else:
                splitter = ShuffleSplit(n_splits=1, test_size=config.test_fraction, random_state=config.seed)
                train_idx, test_idx = next(splitter.split(unique))
                metadata["diagnostic_only"] = True
        elif strategy == "scaffold":
            splitter = ScaffoldSplit(
                smiles,
                n_splits=1,
                test_size=config.test_fraction,
                random_state=config.seed,
            )
            train_idx, test_idx = next(splitter.split(smiles))
        elif strategy == "cluster":
            features = _ecfp(smiles)
            splitter = KMeansSplit(
                n_clusters=min(10, max(2, len(unique) // 10)),
                n_splits=1,
                test_size=config.test_fraction,
                random_state=config.seed,
            )
            train_idx, test_idx = next(splitter.split(features))
        elif strategy == "deployment":
            deployment = read_table(config.deployment_path or "")
            if config.deployment_smiles_column not in deployment.columns:
                raise ValueError(
                    f"deployment SMILES column {config.deployment_smiles_column!r} is missing"
                )
            deployment_smiles = []
            for value in deployment[config.deployment_smiles_column]:
                standardized, _, _ = standardize_structure(value)
                if standardized:
                    deployment_smiles.append(standardized)
            if len(deployment_smiles) < 3:
                raise ValueError("deployment split needs at least three valid deployment structures")
            features = _ecfp(smiles)
            deployment_features = _ecfp(deployment_smiles)
            candidates = {
                "random": ShuffleSplit(
                    n_splits=3, test_size=config.test_fraction, random_state=config.seed
                ),
                "cluster": KMeansSplit(
                    n_clusters=min(10, max(2, len(unique) // 10)),
                    n_splits=3,
                    test_size=config.test_fraction,
                    random_state=config.seed,
                ),
                "max_dissimilarity": MaxDissimilaritySplit(
                    n_clusters=min(10, max(2, len(unique) // 10)),
                    n_splits=3,
                    test_size=config.test_fraction,
                    random_state=config.seed,
                ),
            }
            mood = MOODSplitter(candidates, k=min(5, len(unique) - 1), n_jobs=1)
            ranking = mood.fit(features, X_deployment=deployment_features)
            train_idx, test_idx = next(mood.split(features))
            metadata["mood_ranking"] = ranking.to_dict(orient="records")
            metadata["deployment_size"] = len(deployment_smiles)
        else:
            raise ValueError(f"Unsupported split strategy {strategy!r}")

        train_keys = set(unique.iloc[np.asarray(train_idx)]["identity_key"])
        test_keys = set(unique.iloc[np.asarray(test_idx)]["identity_key"])
        assigned.loc[assigned["identity_key"].isin(train_keys), "outer_split"] = "train"
        assigned.loc[assigned["identity_key"].isin(test_keys), "outer_split"] = "test"

    train = assigned[assigned["outer_split"] == "train"]
    test = assigned[assigned["outer_split"] == "test"]
    overlap = set(train["identity_key"]) & set(test["identity_key"])
    if overlap:
        raise AssertionError("outer split leaked molecular identities")
    if len(train) < 15 or len(test) < 5:
        raise ValueError(
            f"{strategy} split is too small after grouping/purging: {len(train)} train, {len(test)} test"
        )
    if spec.endpoint.task == "classification":
        for name, frame in (("train", train), ("test", test)):
            if frame["label"].nunique() < 2:
                raise ValueError(f"{strategy} {name} partition contains only one class")
    metadata.update(
        n_train_rows=len(train),
        n_test_rows=len(test),
        n_excluded_rows=int((assigned["outer_split"] != "train").sum() - len(test)),
        identity_overlap=0,
    )
    return assigned, metadata


def inner_split_indices(
    train: pd.DataFrame, task: str, strategy: str, n_splits: int, seed: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    n_splits = min(n_splits, max(2, len(train) // 8))
    if strategy == "time":
        if train["measurement_date"].isna().any():
            raise ValueError("time-based inner validation requires complete dates")
        if not train["measurement_date"].is_monotonic_increasing:
            raise ValueError("time-based inner validation input must be sorted by date")
        splits = list(TimeSeriesSplit(n_splits=n_splits).split(train))
    else:
        groups = scaffold_groups(train["smiles"].tolist(), n_splits, seed)
        unique_groups = len(set(groups))
        n_splits = min(n_splits, unique_groups)
        if n_splits < 2:
            raise ValueError("fewer than two chemistry groups are available for inner validation")
        splits = list(GroupKFold(n_splits=n_splits).split(train, train["label"], groups))
    if task == "classification":
        usable = [
            (fit, valid)
            for fit, valid in splits
            if train.iloc[fit]["label"].nunique() == 2 and train.iloc[valid]["label"].nunique() == 2
        ]
        if len(usable) < 2:
            raise ValueError("classification needs at least two inner folds containing both classes")
        return usable
    return splits


def calibration_indices(
    train: pd.DataFrame,
    task: str,
    strategy: str,
    fraction: float,
    seed: int,
    min_calibration: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    if strategy == "time":
        calibration_size = max(min_calibration, math.ceil(fraction * len(train)))
        cut = len(train) - calibration_size
        fit_idx = np.arange(cut)
        cal_idx = np.arange(cut, len(train))
    else:
        groups = scaffold_groups(train["smiles"].tolist(), 5, seed)
        effective_fraction = max(fraction, min_calibration / len(train))
        if effective_fraction >= 0.5:
            raise ValueError("not enough training molecules for the requested conformal confidence level")
        splitter = GroupShuffleSplit(n_splits=20, test_size=effective_fraction, random_state=seed)
        fit_idx = cal_idx = None
        for candidate_fit, candidate_cal in splitter.split(train, train["label"], groups):
            if len(candidate_cal) < min_calibration:
                continue
            if task != "classification" or (
                train.iloc[candidate_fit]["label"].nunique() == 2
                and train.iloc[candidate_cal]["label"].nunique() == 2
            ):
                fit_idx, cal_idx = candidate_fit, candidate_cal
                break
        if fit_idx is None or cal_idx is None:
            raise ValueError("could not construct a group-disjoint calibration split")
    if len(fit_idx) < 10 or len(cal_idx) < min_calibration:
        raise ValueError("calibration split is too small")
    return np.asarray(fit_idx), np.asarray(cal_idx)
