from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from mapie.classification import CrossConformalClassifier, SplitConformalClassifier
from mapie.regression import CrossConformalRegressor, SplitConformalRegressor
from molfeat.trans import MoleculeTransformer
from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator
from scipy.stats import spearmanr
from sklearn.base import BaseEstimator, ClassifierMixin, RegressorMixin, clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    matthews_corrcoef,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
    root_mean_squared_error,
)
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .schema import RunConfig
from .validation import calibration_indices, inner_split_indices, scaffold_groups


def make_feature_transformer(name: str) -> Any:
    if name == "ecfp":
        return MoleculeTransformer("ecfp", n_jobs=1, dtype=np.float32)
    if name == "desc2d":
        return MoleculeTransformer("desc2d", n_jobs=1, dtype=np.float32)
    if name == "combined":
        return [make_feature_transformer("ecfp"), make_feature_transformer("desc2d")]
    raise ValueError(f"Unsupported feature set {name!r}")


def feature_state(transformer: Any) -> dict[str, Any]:
    if isinstance(transformer, list):
        return {"kind": "combined", "parts": [item.to_state_dict() for item in transformer]}
    return {"kind": "single", "state": transformer.to_state_dict()}


def transformer_from_state(state: dict[str, Any]) -> Any:
    if state["kind"] == "combined":
        return [MoleculeTransformer.from_state_dict(item) for item in state["parts"]]
    return MoleculeTransformer.from_state_dict(state["state"])


def transform_smiles(transformer: Any, smiles: list[str]) -> np.ndarray:
    if isinstance(transformer, list):
        arrays = [transform_smiles(item, smiles) for item in transformer]
        return np.hstack(arrays).astype(np.float32, copy=False)
    values = np.asarray(transformer(smiles), dtype=np.float32)
    return np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)


class Tanimoto1NNRegressor(RegressorMixin, BaseEstimator):
    def fit(self, X: np.ndarray, y: np.ndarray) -> Tanimoto1NNRegressor:
        self.X_ = np.asarray(X > 0, dtype=np.uint8)
        self.y_ = np.asarray(y, dtype=float)
        self.n_features_in_ = self.X_.shape[1]
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.y_[_nearest_binary_index(np.asarray(X > 0, dtype=np.uint8), self.X_)]


class Tanimoto1NNClassifier(ClassifierMixin, BaseEstimator):
    def fit(self, X: np.ndarray, y: np.ndarray) -> Tanimoto1NNClassifier:
        self.X_ = np.asarray(X > 0, dtype=np.uint8)
        self.y_ = np.asarray(y, dtype=int)
        self.classes_ = np.asarray([0, 1])
        self.n_features_in_ = self.X_.shape[1]
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.y_[_nearest_binary_index(np.asarray(X > 0, dtype=np.uint8), self.X_)]

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        prediction = self.predict(X).astype(float)
        return np.column_stack([1.0 - prediction, prediction])


def _nearest_binary_index(query: np.ndarray, reference: np.ndarray) -> np.ndarray:
    intersection = query.astype(np.float32) @ reference.astype(np.float32).T
    union = query.sum(axis=1)[:, None] + reference.sum(axis=1)[None, :] - intersection
    similarity = np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)
    return np.argmax(similarity, axis=1)


class XGBAFTRegressor(BaseEstimator, RegressorMixin):
    """XGBoost AFT wrapper that keeps censoring bounds as first-class labels."""

    def __init__(
        self,
        constant: bool = False,
        num_boost_round: int = 160,
        max_depth: int = 4,
        learning_rate: float = 0.05,
        seed: int = 0,
    ):
        self.constant = constant
        self.num_boost_round = num_boost_round
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.seed = seed

    def _features(self, X: np.ndarray) -> np.ndarray:
        values = np.asarray(X, dtype=np.float32)
        return np.ones((len(values), 1), dtype=np.float32) if self.constant else values

    def fit_interval(
        self, X: np.ndarray, lower: np.ndarray, upper: np.ndarray
    ) -> XGBAFTRegressor:
        matrix = xgb.DMatrix(self._features(X))
        matrix.set_float_info("label_lower_bound", np.asarray(lower, dtype=np.float32))
        matrix.set_float_info("label_upper_bound", np.asarray(upper, dtype=np.float32))
        params = {
            "objective": "survival:aft",
            "eval_metric": "aft-nloglik",
            "aft_loss_distribution": "normal",
            "aft_loss_distribution_scale": 1.0,
            "tree_method": "hist",
            "max_depth": 1 if self.constant else self.max_depth,
            "eta": self.learning_rate,
            "subsample": 0.85,
            "colsample_bytree": 1.0 if self.constant else 0.85,
            "seed": self.seed,
            "nthread": 1,
            "verbosity": 0,
        }
        self.booster_ = xgb.train(params, matrix, num_boost_round=self.num_boost_round)
        self.n_features_in_ = np.asarray(X).shape[1]
        return self

    def fit(self, X: np.ndarray, y: np.ndarray) -> XGBAFTRegressor:
        values = np.asarray(y, dtype=float)
        return self.fit_interval(X, values, values)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.booster_.predict(xgb.DMatrix(self._features(X)))


@dataclass(frozen=True)
class Candidate:
    model: str
    features: str

    @property
    def key(self) -> str:
        return f"{self.model}::{self.features}"


def default_candidates(task: str, censored: bool, feature_sets: list[str]) -> list[Candidate]:
    if censored:
        candidates = [Candidate("aft_constant", "ecfp")]
        candidates.extend(Candidate("aft_xgb", name) for name in feature_sets)
        return candidates
    candidates = [Candidate("dummy", "ecfp"), Candidate("knn", "ecfp")]
    linear = "ridge" if task == "regression" else "logistic"
    candidates.extend(Candidate(linear, name) for name in feature_sets)
    candidates.extend(Candidate("xgb", name) for name in feature_sets)
    return candidates


def build_model(name: str, task: str, seed: int) -> Any:
    if name == "dummy":
        return DummyRegressor(strategy="median") if task == "regression" else DummyClassifier(strategy="prior")
    if name == "knn":
        return Tanimoto1NNRegressor() if task == "regression" else Tanimoto1NNClassifier()
    if name == "ridge":
        return make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    if name == "logistic":
        return make_pipeline(
            StandardScaler(),
            LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000, random_state=seed),
        )
    if name == "xgb" and task == "regression":
        return xgb.XGBRegressor(
            n_estimators=160,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.85,
            colsample_bytree=0.85,
            objective="reg:squarederror",
            n_jobs=1,
            random_state=seed,
            verbosity=0,
        )
    if name == "xgb" and task == "classification":
        return xgb.XGBClassifier(
            n_estimators=160,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.85,
            colsample_bytree=0.85,
            objective="binary:logistic",
            eval_metric="logloss",
            n_jobs=1,
            random_state=seed,
            verbosity=0,
        )
    if name == "aft_constant":
        return XGBAFTRegressor(constant=True, seed=seed)
    if name == "aft_xgb":
        return XGBAFTRegressor(seed=seed)
    raise ValueError(f"Unsupported model {name!r} for {task}")


def _aft_bounds(lower: np.ndarray, upper: np.ndarray, scale: str) -> tuple[np.ndarray, np.ndarray]:
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    if scale == "log10":
        with np.errstate(over="ignore"):
            return np.power(10.0, lower), np.power(10.0, upper)
    converted_lower = np.where(np.isneginf(lower), 0.0, lower)
    if np.any(converted_lower < 0) or np.any(upper[np.isfinite(upper)] <= 0):
        raise ValueError(
            "censored AFT regression needs a positive physical endpoint; declare scale='log10' "
            "for log10-transformed positive quantities"
        )
    return converted_lower, upper


def _from_aft_prediction(prediction: np.ndarray, scale: str) -> np.ndarray:
    prediction = np.asarray(prediction, dtype=float)
    if scale == "log10":
        return np.log10(np.clip(prediction, np.finfo(float).tiny, None))
    return prediction


def interval_concordance(lower: np.ndarray, upper: np.ndarray, prediction: np.ndarray) -> float:
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    concordant = 0.0
    comparable = 0
    for left in range(len(prediction)):
        for right in range(left + 1, len(prediction)):
            if upper[left] < lower[right]:
                comparable += 1
                concordant += float(prediction[left] < prediction[right]) + 0.5 * float(
                    prediction[left] == prediction[right]
                )
            elif upper[right] < lower[left]:
                comparable += 1
                concordant += float(prediction[right] < prediction[left]) + 0.5 * float(
                    prediction[right] == prediction[left]
                )
    return float(concordant / comparable) if comparable else math.nan


def regression_metrics(y: np.ndarray, prediction: np.ndarray, log10_scale: bool) -> dict[str, float]:
    y = np.asarray(y, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    result = {
        "mae": float(mean_absolute_error(y, prediction)),
        "rmse": float(root_mean_squared_error(y, prediction)),
        "r2": float(r2_score(y, prediction)),
        "spearman": float(spearmanr(y, prediction).statistic),
    }
    if log10_scale:
        result["fraction_within_2fold"] = float(np.mean(np.abs(y - prediction) <= math.log10(2)))
    return result


def classification_metrics(
    y: np.ndarray, probability: np.ndarray, threshold: float
) -> dict[str, float]:
    y = np.asarray(y, dtype=int)
    probability = np.asarray(probability, dtype=float)
    label = (probability >= threshold).astype(int)
    return {
        "roc_auc": float(roc_auc_score(y, probability)),
        "average_precision": float(average_precision_score(y, probability)),
        "balanced_accuracy": float(balanced_accuracy_score(y, label)),
        "mcc": float(matthews_corrcoef(y, label)),
        "brier": float(brier_score_loss(y, probability)),
    }


def censored_metrics(frame: pd.DataFrame, prediction: np.ndarray) -> dict[str, float]:
    exact = frame["qualifier"] == "exact"
    result = {
        "interval_c_index": interval_concordance(
            frame["lower_bound"].to_numpy(float),
            frame["upper_bound"].to_numpy(float),
            prediction,
        ),
        "n_exact": int(exact.sum()),
        "n_censored": int((~exact).sum()),
    }
    if exact.any():
        result["mae_exact"] = float(mean_absolute_error(frame.loc[exact, "label"], prediction[exact]))
        result["rmse_exact"] = float(root_mean_squared_error(frame.loc[exact, "label"], prediction[exact]))
    return result


def _candidate_score(
    candidate: Candidate,
    task: str,
    censored: bool,
    scale: str,
    X: np.ndarray,
    frame: pd.DataFrame,
    fit: np.ndarray,
    valid: np.ndarray,
    seed: int,
) -> tuple[float, np.ndarray]:
    model = build_model(candidate.model, task, seed)
    if censored:
        lower, upper = _aft_bounds(
            frame.iloc[fit]["lower_bound"].to_numpy(float),
            frame.iloc[fit]["upper_bound"].to_numpy(float),
            scale,
        )
        model.fit_interval(X[fit], lower, upper)
        prediction = _from_aft_prediction(model.predict(X[valid]), scale)
        score = interval_concordance(
            frame.iloc[valid]["lower_bound"], frame.iloc[valid]["upper_bound"], prediction
        )
    else:
        y_fit = frame.iloc[fit]["label"].to_numpy()
        y_valid = frame.iloc[valid]["label"].to_numpy()
        model.fit(X[fit], y_fit)
        if task == "classification":
            prediction = model.predict_proba(X[valid])[:, 1]
            score = float(average_precision_score(y_valid, prediction))
        else:
            prediction = model.predict(X[valid])
            score = float(mean_absolute_error(y_valid, prediction))
    return score, np.asarray(prediction, dtype=float)


def select_candidate(
    train: pd.DataFrame,
    task: str,
    scale: str,
    strategy: str,
    config: RunConfig,
    feature_cache: dict[str, np.ndarray],
) -> tuple[Candidate, list[dict[str, Any]], np.ndarray]:
    censored = bool((train["qualifier"] != "exact").any())
    candidates = default_candidates(task, censored, config.feature_sets)
    if config.models:
        requested = set(config.models)
        candidates = [candidate for candidate in candidates if candidate.model in requested]
        unknown = requested - {candidate.model for candidate in default_candidates(task, censored, config.feature_sets)}
        if unknown:
            raise ValueError(f"Unsupported requested models for this task: {sorted(unknown)}")
    if not candidates:
        raise ValueError("No model candidates remain after applying the model filter")
    splits = inner_split_indices(train, task, strategy, config.inner_splits, config.seed)
    rows: list[dict[str, Any]] = []
    candidate_predictions: dict[str, np.ndarray] = {}
    for candidate in candidates:
        X = feature_cache[candidate.features]
        fold_scores: list[float] = []
        oof = np.full(len(train), np.nan)
        for fold_number, (fit, valid) in enumerate(splits):
            score, prediction = _candidate_score(
                candidate, task, censored, scale, X, train, fit, valid, config.seed + fold_number
            )
            if math.isfinite(score):
                fold_scores.append(score)
            oof[valid] = prediction
        if not fold_scores:
            continue
        rows.append(
            {
                "candidate": candidate.key,
                "model": candidate.model,
                "features": candidate.features,
                "metric": "interval_c_index" if censored else ("average_precision" if task == "classification" else "mae"),
                "mean": float(np.mean(fold_scores)),
                "std": float(np.std(fold_scores, ddof=1)) if len(fold_scores) > 1 else 0.0,
                "n_folds": len(fold_scores),
            }
        )
        candidate_predictions[candidate.key] = oof
    if not rows:
        raise ValueError("all inner-validation candidates failed")
    maximize = censored or task == "classification"
    best_row = (max if maximize else min)(rows, key=lambda row: row["mean"])
    standard_error = best_row["std"] / math.sqrt(max(1, best_row["n_folds"]))
    if maximize:
        eligible = [row for row in rows if row["mean"] >= best_row["mean"] - standard_error]
    else:
        eligible = [row for row in rows if row["mean"] <= best_row["mean"] + standard_error]
    complexity = {"dummy": 0, "aft_constant": 0, "knn": 1, "ridge": 2, "logistic": 2, "xgb": 3, "aft_xgb": 3}
    chosen_row = min(eligible, key=lambda row: (complexity[row["model"]], row["candidate"]))
    chosen = Candidate(chosen_row["model"], chosen_row["features"])
    for row in rows:
        row["selected"] = row["candidate"] == chosen.key
        row["selection_rule"] = "one-standard-error; prefer simpler model"
    return chosen, rows, candidate_predictions[chosen.key]


def fit_outer_assessment(
    train: pd.DataFrame,
    test: pd.DataFrame,
    candidate: Candidate,
    task: str,
    scale: str,
    strategy: str,
    config: RunConfig,
    X_train: np.ndarray,
    X_test: np.ndarray,
) -> dict[str, Any]:
    censored = bool((train["qualifier"] != "exact").any())
    if censored:
        model = build_model(candidate.model, task, config.seed)
        lower, upper = _aft_bounds(train["lower_bound"], train["upper_bound"], scale)
        model.fit_interval(X_train, lower, upper)
        prediction = _from_aft_prediction(model.predict(X_test), scale)
        return {
            "model": model,
            "prediction": prediction,
            "metrics": censored_metrics(test, prediction),
            "uncertainty": None,
            "warning": "Conformal intervals are not reported for censored AFT models.",
        }

    fit_idx, cal_idx = calibration_indices(
        train,
        task,
        strategy,
        config.calibration_fraction,
        config.seed,
        min_calibration=math.ceil(
            max(1 / config.confidence_level, 1 / (1 - config.confidence_level))
        )
        + 1,
    )
    base = build_model(candidate.model, task, config.seed)
    if task == "classification":
        fitted = CalibratedClassifierCV(base, method="sigmoid", cv=3).fit(
            X_train[fit_idx], train.iloc[fit_idx]["label"].astype(int)
        )
        conformal = SplitConformalClassifier(
            fitted,
            confidence_level=config.confidence_level,
            conformity_score="lac",
            prefit=True,
            random_state=config.seed,
        ).conformalize(X_train[cal_idx], train.iloc[cal_idx]["label"].astype(int))
        _, prediction_sets = conformal.predict_set(X_test)
        probability = fitted.predict_proba(X_test)[:, 1]
        set_values = prediction_sets[:, :, 0]
        y_test = test["label"].astype(int).to_numpy()
        coverage = float(np.mean(set_values[np.arange(len(test)), y_test]))
        return {
            "model": fitted,
            "conformal": conformal,
            "prediction": probability,
            "prediction_sets": set_values,
            "metrics": classification_metrics(y_test, probability, config.probability_threshold),
            "uncertainty": {
                "method": "MAPIE split conformal LAC",
                "confidence_level": config.confidence_level,
                "empirical_coverage": coverage,
                "mean_set_size": float(np.mean(set_values.sum(axis=1))),
            },
        }

    fitted = base.fit(X_train[fit_idx], train.iloc[fit_idx]["label"].to_numpy(float))
    conformal = SplitConformalRegressor(
        fitted,
        confidence_level=config.confidence_level,
        conformity_score="absolute",
        prefit=True,
    ).conformalize(X_train[cal_idx], train.iloc[cal_idx]["label"].to_numpy(float))
    point, intervals = conformal.predict_interval(X_test)
    lower, upper = intervals[:, 0, 0], intervals[:, 1, 0]
    y_test = test["label"].to_numpy(float)
    width = upper - lower
    asym = (upper - point) - (point - lower)
    rounded_width = np.round(width, 9)
    n_distinct = int(np.unique(rounded_width).size)
    is_constant = bool(np.ptp(width) <= 1e-9)
    max_asymmetry = float(np.max(np.abs(asym)))
    is_symmetric = bool(max_asymmetry <= 1e-9)
    return {
        "model": fitted,
        "conformal": conformal,
        "prediction": point,
        "prediction_lower": lower,
        "prediction_upper": upper,
        "metrics": regression_metrics(y_test, point, scale == "log10"),
        "uncertainty": {
            "method": "MAPIE split conformal absolute residual",
            "confidence_level": config.confidence_level,
            "empirical_coverage": float(np.mean((y_test >= lower) & (y_test <= upper))),
            "mean_interval_width": float(np.mean(width)),
            "interval_width_min": float(np.min(width)),
            "interval_width_max": float(np.max(width)),
            "interval_width_n_distinct": n_distinct,
            "interval_width_is_constant": is_constant,
            "max_interval_asymmetry": max_asymmetry,
            "interval_is_symmetric": is_symmetric,
            "scope": "locked outer test set",
        },
    }


def fit_deployment_models(
    frame: pd.DataFrame,
    candidate: Candidate,
    task: str,
    scale: str,
    config: RunConfig,
    X: np.ndarray,
) -> tuple[Any, Any | None, dict[str, Any] | None]:
    censored = bool((frame["qualifier"] != "exact").any())
    if censored:
        model = build_model(candidate.model, task, config.seed)
        lower, upper = _aft_bounds(frame["lower_bound"], frame["upper_bound"], scale)
        return model.fit_interval(X, lower, upper), None, None

    base = build_model(candidate.model, task, config.seed)
    y = frame["label"].to_numpy()
    n_splits = min(5, max(2, len(frame) // 15))
    groups = scaffold_groups(frame["smiles"].tolist(), n_splits, config.seed)
    cv = GroupKFold(n_splits=min(n_splits, len(set(groups))))
    if task == "classification":
        probability_model = CalibratedClassifierCV(base, method="sigmoid", cv=3).fit(X, y.astype(int))
        try:
            conformal = CrossConformalClassifier(
                estimator=clone(base),
                confidence_level=config.confidence_level,
                conformity_score="lac",
                cv=cv,
                random_state=config.seed,
            ).fit_conformalize(X, y.astype(int), groups=groups)
            deployment_uncertainty = {
                "estimator_class": type(conformal).__name__,
                "conformity_score": "lac",
                "aggregation": "cross-conformal (CV+)",
                "n_folds": int(cv.get_n_splits()),
                "point_source": "calibrated probability model, not conformal set membership",
                "set_geometry": "per-molecule set size; not a single global threshold",
                "branch": "GroupKFold cross-conformal",
            }
        except ValueError:
            conformal = CrossConformalClassifier(
                estimator=clone(base),
                confidence_level=config.confidence_level,
                conformity_score="lac",
                cv=3,
                random_state=config.seed,
            ).fit_conformalize(X, y.astype(int))
            deployment_uncertainty = {
                "estimator_class": type(conformal).__name__,
                "conformity_score": "lac",
                "aggregation": "cross-conformal (CV+)",
                "n_folds": 3,
                "point_source": "calibrated probability model, not conformal set membership",
                "set_geometry": "per-molecule set size; not a single global threshold",
                "branch": "cv=3 fallback (GroupKFold raised ValueError)",
            }
        return probability_model, conformal, deployment_uncertainty
    point_model = base.fit(X, y.astype(float))
    conformal = CrossConformalRegressor(
        estimator=clone(base),
        confidence_level=config.confidence_level,
        conformity_score="absolute",
        method="plus",
        cv=cv,
        random_state=config.seed,
    ).fit_conformalize(X, y.astype(float), groups=groups)
    deployment_uncertainty = {
        "estimator_class": type(conformal).__name__,
        "conformity_score": "absolute",
        "aggregation": "plus (CV+)",
        "n_folds": int(cv.get_n_splits()),
        "point_source": "CV+ fold-ensemble aggregate, not model_bundle.point_model",
        "interval_geometry": "per-molecule width; not symmetric about the point estimate",
        "branch": "GroupKFold cross-conformal CV+",
    }
    return point_model, conformal, deployment_uncertainty


def _fingerprints(smiles: list[str]) -> list[Any]:
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    return [generator.GetFingerprint(Chem.MolFromSmiles(value)) for value in smiles]


def nearest_similarity(reference_smiles: list[str], query_smiles: list[str]) -> np.ndarray:
    reference = _fingerprints(reference_smiles)
    query = _fingerprints(query_smiles)
    return np.asarray([max(DataStructs.BulkTanimotoSimilarity(fp, reference)) for fp in query])


def leave_one_out_similarity(smiles: list[str]) -> np.ndarray:
    fps = _fingerprints(smiles)
    values = []
    for index, fp in enumerate(fps):
        other = fps[:index] + fps[index + 1 :]
        values.append(max(DataStructs.BulkTanimotoSimilarity(fp, other)) if other else 0.0)
    return np.asarray(values, dtype=float)


_REFERENCE_SET_LABELS = {
    "outer_training": "outer-training molecules only",
    "all_audited": "all audited molecules (train+test)",
}


def reference_set_descriptor(reference_smiles: list[str], scope: str) -> dict[str, Any]:
    """Describe an applicability-domain reference set from the array actually used.

    ``n`` and the count embedded in ``label`` are taken from ``len(reference_smiles)`` -- the
    array passed to the similarity call -- so the description can never drift from the reference
    set it names. ``scope`` selects a fixed human-readable phrase.
    """
    n = int(len(reference_smiles))
    phrase = _REFERENCE_SET_LABELS.get(scope, scope)
    return {"scope": scope, "n": n, "label": f"{phrase}, n={n}"}


def _error_monotonicity(strata_mean_by_ascending_similarity: np.ndarray) -> dict[str, Any]:
    """Judge, from the computed strata, whether error tracks nearest-neighbour similarity.

    The applicability-domain flag is only a trust signal if out-of-fold error tends to *fall* as
    similarity to the training set *rises*. This computes the Spearman rank correlation between the
    ascending similarity stratum and its mean out-of-fold error and returns a derived verdict, so a
    report can state whether the flag is evidenced on this dataset instead of assuming it.
    """
    means = np.asarray(strata_mean_by_ascending_similarity, dtype=float)
    means = means[np.isfinite(means)]
    n_strata = int(means.size)
    base: dict[str, Any] = {
        "n_strata": n_strata,
        "spearman_rho": None,
        "metric": "Spearman rho of per-stratum mean out-of-fold error vs ascending similarity stratum",
    }
    if n_strata < 3:
        base["verdict"] = "insufficient_data"
        base["explanation"] = (
            "Fewer than three similarity strata with finite out-of-fold error; whether the "
            "applicability-domain flag tracks error on this dataset cannot be assessed."
        )
        return base
    rho = float(spearmanr(np.arange(n_strata), means).statistic)
    base["spearman_rho"] = rho
    base["strata_mean_error"] = [float(value) for value in means]
    if rho <= -0.5:
        base["verdict"] = "supported"
        base["explanation"] = (
            f"Per-stratum out-of-fold error decreases as nearest-neighbour similarity increases "
            f"(Spearman rho={rho:.2f} across {n_strata} strata); the applicability-domain flag "
            f"tracks error on this dataset."
        )
    elif rho >= 0.5:
        base["verdict"] = "inverted"
        base["explanation"] = (
            f"Per-stratum out-of-fold error increases with nearest-neighbour similarity "
            f"(Spearman rho={rho:.2f} across {n_strata} strata); the applicability-domain flag does "
            f"NOT track error on this dataset and must not be presented as a validated trust signal."
        )
    else:
        base["verdict"] = "not_evidenced"
        base["explanation"] = (
            f"No monotonic relationship between similarity stratum and out-of-fold error "
            f"(Spearman rho={rho:.2f} across {n_strata} strata); the applicability-domain flag is "
            f"not demonstrably a trust signal on this dataset."
        )
    return base


def domain_summary(
    train: pd.DataFrame,
    query: pd.DataFrame,
    oof_prediction: np.ndarray | None = None,
    query_label: str = "query",
    reference_scope: str = "outer_training",
) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    reference_smiles = train["smiles"].tolist()
    training_similarity = leave_one_out_similarity(reference_smiles)
    if len(training_similarity) >= 30:
        threshold = float(np.quantile(training_similarity, 0.05))
        threshold_kind = "training 5th-percentile leave-one-out similarity"
    else:
        threshold = 0.3
        threshold_kind = "heuristic; fewer than 30 training molecules"
    query_similarity = nearest_similarity(reference_smiles, query["smiles"].tolist())
    in_domain = query_similarity >= threshold
    result: dict[str, Any] = {
        "metric": "Morgan radius-2 Tanimoto similarity",
        "threshold": threshold,
        "threshold_kind": threshold_kind,
        "fraction_in_domain": float(np.mean(in_domain)),
        "n_query": int(len(query)),
        "evaluated_on": query_label,
        "reference_set": reference_set_descriptor(reference_smiles, reference_scope),
        "training_similarity_quantiles": {
            "q05": float(np.quantile(training_similarity, 0.05)),
            "median": float(np.median(training_similarity)),
            "q95": float(np.quantile(training_similarity, 0.95)),
        },
    }
    result["error_monotonicity"] = {
        "n_strata": 0,
        "spearman_rho": None,
        "metric": "Spearman rho of per-stratum mean out-of-fold error vs ascending similarity stratum",
        "verdict": "insufficient_data",
        "explanation": (
            "No out-of-fold predictions were available to relate nearest-neighbour similarity to "
            "error, so whether the applicability-domain flag tracks error is not assessed here."
        ),
    }
    if oof_prediction is not None:
        mask = np.isfinite(oof_prediction)
        if mask.sum() >= 12:
            if (train["qualifier"] != "exact").any():
                error = np.full(len(train), np.nan)
                exact = mask & (train["qualifier"].to_numpy() == "exact")
                error[exact] = np.abs(train.loc[exact, "label"] - oof_prediction[exact])
                mask = np.isfinite(error)
            elif set(train["label"].unique()) <= {0.0, 1.0}:
                error = (oof_prediction - train["label"].to_numpy()) ** 2
            else:
                error = np.abs(oof_prediction - train["label"].to_numpy())
            bins = pd.qcut(training_similarity[mask], q=min(4, mask.sum()), duplicates="drop")
            grouped = (
                pd.DataFrame({"similarity_bin": bins, "error": error[mask]})
                .groupby("similarity_bin", observed=True)["error"]
                .agg(["count", "mean", "median"])
                .sort_index()
            )
            result["oof_error_by_similarity"] = (
                grouped.reset_index()
                .assign(similarity_bin=lambda frame: frame["similarity_bin"].astype(str))
                .to_dict(orient="records")
            )
            result["error_monotonicity"] = _error_monotonicity(grouped["mean"].to_numpy())
    return result, query_similarity, in_domain


def grouped_bootstrap_ci(
    frame: pd.DataFrame,
    prediction: np.ndarray,
    task: str,
    scale: str,
    threshold: float,
    n_bootstrap: int,
    seed: int,
) -> dict[str, list[float]]:
    if n_bootstrap <= 0:
        return {}
    groups = scaffold_groups(frame["smiles"].tolist(), 5, seed)
    unique = np.unique(groups)
    if len(unique) < 2:
        return {}
    rng = np.random.default_rng(seed)
    samples: dict[str, list[float]] = {}
    for _ in range(n_bootstrap):
        selected = rng.choice(unique, size=len(unique), replace=True)
        indices = np.concatenate([np.flatnonzero(groups == group) for group in selected])
        subset = frame.iloc[indices]
        pred = np.asarray(prediction)[indices]
        try:
            if (frame["qualifier"] != "exact").any():
                metrics = censored_metrics(subset, pred)
            elif task == "classification":
                if subset["label"].nunique() < 2:
                    continue
                metrics = classification_metrics(subset["label"], pred, threshold)
            else:
                metrics = regression_metrics(subset["label"], pred, scale == "log10")
        except ValueError:
            continue
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                samples.setdefault(key, []).append(float(value))
    return {
        key: [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]
        for key, values in samples.items()
        if len(values) >= 20
    }
