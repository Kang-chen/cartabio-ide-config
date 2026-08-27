"""
DeepPurpose model wrapper for single-molecule pAffinity regression.

WHY THIS FILE EXISTS (no silent substitution):
When the user explicitly requests a modeling framework (e.g. DeepPurpose), the
skill must either USE it or FAIL LOUDLY and tell the caller what it used instead
-- it must never quietly swap in a different method. This wrapper isolates the
DeepPurpose dependency behind a guarded import: if DeepPurpose cannot be
imported or a run fails, it raises `FrameworkUnavailable`, and the calling
script is responsible for printing a prominent notice and falling back to the
native models (recording the fallback so the report can disclose it).

Leakage-free protocol (matches models.train_gnn_fixed):
  * The inner validation set for early stopping is carved from the TRAINING
    indices only; the test fold is used solely for the final prediction.
  * DeepPurpose's own train/val/test split is bypassed (`split_method='no_split'`)
    so the caller controls the exact fold membership (identical folds across all
    models -> fair comparison).

Compound-property dataframe format (DeepPurpose): columns
  ['SMILES', 'Label', 'drug_encoding'].

Tested with DeepPurpose 0.1.5.
"""
import os
import numpy as np
from sklearn.model_selection import train_test_split as _tts


class FrameworkUnavailable(RuntimeError):
    """Raised when a requested modeling framework cannot be imported or run."""


def deeppurpose_available():
    """True iff DeepPurpose imports cleanly in this environment."""
    try:
        import DeepPurpose.CompoundPred  # noqa: F401
        import DeepPurpose.utils  # noqa: F401
        return True
    except Exception:                                            # noqa: BLE001
        return False


def _version():
    try:
        import DeepPurpose
        return getattr(DeepPurpose, '__version__', 'unknown')
    except Exception:                                            # noqa: BLE001
        return 'unknown'


def _make_df(smiles, y, drug_encoding):
    """Build a DeepPurpose property-prediction dataframe with a fixed encoding,
    bypassing DeepPurpose's internal split (we control folds externally)."""
    from DeepPurpose import utils
    return utils.data_process(
        X_drug=np.asarray(smiles), y=np.asarray(y, dtype=float),
        drug_encoding=drug_encoding, split_method='no_split',
        frac=[1, 0, 0], random_seed=0, mode='DTI')


def train_deeppurpose_fixed(train_idx, test_idx, X_smiles, y,
                            drug_encoding='CNN', train_epoch=40, lr=1e-3,
                            batch_size=128, seed=0, result_folder=None):
    """Train a DeepPurpose compound-property model on `train_idx` and predict on
    `test_idx`. Early-stopping validation is carved from the TRAIN indices only.

    Returns predictions (np.ndarray) aligned to `test_idx`.
    Raises FrameworkUnavailable if DeepPurpose is missing or the run fails.
    """
    if not deeppurpose_available():
        raise FrameworkUnavailable(
            "DeepPurpose is not importable in this environment. Install it "
            "(`uv pip install DeepPurpose`) or select a native model. "
            "Refusing to silently substitute a different method.")
    try:
        from DeepPurpose import CompoundPred, utils

        train_idx = np.asarray(train_idx)
        test_idx = np.asarray(test_idx)
        # inner train/val split on TRAINING indices only (leakage-free)
        tr_in, va_in = _tts(train_idx, test_size=0.15, random_state=seed,
                            shuffle=True)
        train_df = _make_df(X_smiles[tr_in], y[tr_in], drug_encoding)
        val_df = _make_df(X_smiles[va_in], y[va_in], drug_encoding)
        # test df carries dummy labels ONLY to satisfy the dataframe schema; it is
        # used solely for .predict(). We do NOT pass it to .train() -- DeepPurpose's
        # internal test-metric step computes a correlation and divides by zero on
        # constant dummy labels ("No admissable pairs"). Predictions are unaffected.
        test_df = _make_df(X_smiles[test_idx],
                           np.zeros(len(test_idx)), drug_encoding)

        rf = result_folder or os.path.join('/tmp', f'dp_{os.getpid()}_{seed}')
        os.makedirs(rf, exist_ok=True)
        cfg = utils.generate_config(
            drug_encoding=drug_encoding, result_folder=rf,
            train_epoch=int(train_epoch), LR=float(lr),
            batch_size=int(batch_size))
        model = CompoundPred.model_initialize(**cfg)
        model.train(train_df, val_df, verbose=False)   # early stopping on val only
        preds = np.asarray(model.predict(test_df)).ravel().astype(float)
        if preds.shape[0] != test_idx.shape[0]:
            raise FrameworkUnavailable(
                f"DeepPurpose returned {preds.shape[0]} predictions for "
                f"{test_idx.shape[0]} test compounds.")
        return preds
    except FrameworkUnavailable:
        raise
    except Exception as e:                                       # noqa: BLE001
        raise FrameworkUnavailable(
            f"DeepPurpose run failed ({type(e).__name__}: {e}). The caller "
            f"should disclose this and fall back to a native model rather than "
            f"substitute silently.") from e


def train_deeppurpose_final(train_idx, score_smiles, X_smiles, y,
                            drug_encoding='CNN', train_epoch=40, lr=1e-3,
                            batch_size=128, seed=0, result_folder=None):
    """Train on all `train_idx` and score an external `score_smiles` list.
    Returns predictions aligned to `score_smiles`. Raises FrameworkUnavailable
    on any failure so the caller can disclose + fall back."""
    if not deeppurpose_available():
        raise FrameworkUnavailable("DeepPurpose is not importable.")
    try:
        from DeepPurpose import CompoundPred, utils
        train_idx = np.asarray(train_idx)
        tr_in, va_in = _tts(train_idx, test_size=0.15, random_state=seed,
                            shuffle=True)
        train_df = _make_df(X_smiles[tr_in], y[tr_in], drug_encoding)
        val_df = _make_df(X_smiles[va_in], y[va_in], drug_encoding)
        score_df = _make_df(np.asarray(score_smiles),
                            np.zeros(len(score_smiles)), drug_encoding)
        rf = result_folder or os.path.join('/tmp', f'dp_final_{os.getpid()}')
        os.makedirs(rf, exist_ok=True)
        cfg = utils.generate_config(
            drug_encoding=drug_encoding, result_folder=rf,
            train_epoch=int(train_epoch), LR=float(lr),
            batch_size=int(batch_size))
        model = CompoundPred.model_initialize(**cfg)
        model.train(train_df, val_df, verbose=False)
        preds = np.asarray(model.predict(score_df)).ravel().astype(float)
        return preds
    except Exception as e:                                       # noqa: BLE001
        raise FrameworkUnavailable(
            f"DeepPurpose final-model run failed "
            f"({type(e).__name__}: {e}).") from e
