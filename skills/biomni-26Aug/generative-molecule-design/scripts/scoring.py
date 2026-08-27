"""
Pluggable multi-objective scoring for goal-directed molecule generation.

Design (mirrors REINVENT 4): a ScoringFunction is a list of *components*. Each
component maps a raw molecular property to a desirability score in [0, 1] via an
explicit *transform* (sigmoid / reverse_sigmoid / range / identity). Components
are combined by an *aggregation* mode. The default aggregation is the geometric
mean: one bad objective drives the whole score toward zero, which forces every
objective to be simultaneously decent. A naive weighted *arithmetic* sum over
raw property values is broken (e.g. MW ~= 350 numerically swamps QED ~= 0.7),
which is why every component is transformed to [0, 1] first.

Activity is supplied by a pluggable *backend*:
  (a) tdc_oracle  -- pretrained TDC oracle (offline). The convenience case.
  (b) qsar        -- a quick QSAR surrogate trained on user actives/inactives.
  (c) vina        -- AutoDock Vina docking score (optional; slow, most general).

Everything downstream of the activity score is backend-agnostic.

All property calculations are RDKit-native and offline. Synthetic accessibility
(SA_Score) uses the RDKit contrib `sascorer` -- no download, no network.
"""
from __future__ import annotations
import math
import os
import sys
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
from rdkit import Chem
from rdkit import RDLogger
from rdkit.Chem import QED, Crippen, Descriptors, rdMolDescriptors
from rdkit.Chem import DataStructs
from rdkit.Chem import AllChem
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams

RDLogger.DisableLog("rdApp.*")

# --- SA_Score (RDKit contrib). Always-available Tier-1 synthesizability proxy. ---
try:
    from rdkit.Chem import RDConfig
    sys.path.append(os.path.join(RDConfig.RDContribDir, "SA_Score"))
    import sascorer  # type: ignore
    _HAVE_SASCORER = True
except Exception:  # pragma: no cover
    _HAVE_SASCORER = False


# ======================================================================
# 1. Transforms: raw property value -> desirability in [0, 1]
# ======================================================================
def t_identity(x: float, **_) -> float:
    """Pass-through, clamped to [0,1]. For oracles already in [0,1]."""
    return float(min(1.0, max(0.0, x)))


def t_sigmoid(x: float, low: float = 0.0, high: float = 1.0, k: float = 10.0, **_) -> float:
    """Increasing sigmoid: ~0 below `low`, ~1 above `high`. Higher = better."""
    mid = 0.5 * (low + high)
    span = max(1e-9, (high - low))
    z = k * (x - mid) / span
    return float(1.0 / (1.0 + math.exp(-z)))


def t_reverse_sigmoid(x: float, low: float = 0.0, high: float = 1.0, k: float = 10.0, **_) -> float:
    """Decreasing sigmoid: ~1 below `low`, ~0 above `high`. Lower = better.
    Use for SA_Score, MW ceilings, LogP ceilings, etc."""
    return 1.0 - t_sigmoid(x, low=low, high=high, k=k)


def t_range(x: float, low: float, high: float, soft: float = 0.0, **_) -> float:
    """Hard/soft window: 1 inside [low, high], falling to 0 outside.
    `soft` (>0) adds a linear ramp of that width on each side."""
    if low <= x <= high:
        return 1.0
    if soft <= 0:
        return 0.0
    if x < low:
        return float(max(0.0, 1.0 - (low - x) / soft))
    return float(max(0.0, 1.0 - (x - high) / soft))


TRANSFORMS: Dict[str, Callable[..., float]] = {
    "identity": t_identity,
    "sigmoid": t_sigmoid,
    "reverse_sigmoid": t_reverse_sigmoid,
    "range": t_range,
}


# ======================================================================
# 2. Aggregation: list of component desirabilities -> single fitness
# ======================================================================
def agg_geometric_mean(vals: Sequence[float], weights: Optional[Sequence[float]] = None) -> float:
    """Weighted geometric mean. Any zero component -> 0 (hard gate on every
    objective). This is the recommended production aggregation."""
    vals = [max(0.0, float(v)) for v in vals]
    if not vals:
        return 0.0
    if any(v <= 0.0 for v in vals):
        return 0.0
    if weights is None:
        weights = [1.0] * len(vals)
    wsum = float(sum(weights))
    if wsum <= 0:
        return 0.0
    logsum = sum(w * math.log(v) for w, v in zip(weights, vals))
    return float(math.exp(logsum / wsum))


def agg_weighted_sum(vals: Sequence[float], weights: Optional[Sequence[float]] = None) -> float:
    """Weighted arithmetic mean of *transformed* [0,1] components. Softer than
    geometric mean -- a weak objective can be compensated by strong ones."""
    vals = [max(0.0, float(v)) for v in vals]
    if not vals:
        return 0.0
    if weights is None:
        weights = [1.0] * len(vals)
    wsum = float(sum(weights))
    if wsum <= 0:
        return 0.0
    return float(sum(w * v for w, v in zip(weights, vals)) / wsum)


def agg_product(vals: Sequence[float], weights: Optional[Sequence[float]] = None) -> float:
    """Plain product of components (unweighted). Harshest gate."""
    p = 1.0
    for v in vals:
        p *= max(0.0, float(v))
    return float(p)


AGGREGATIONS: Dict[str, Callable[..., float]] = {
    "geometric_mean": agg_geometric_mean,
    "weighted_sum": agg_weighted_sum,
    "product": agg_product,
}


# ======================================================================
# 3. Property calculators (raw values, backend-agnostic)
# ======================================================================
def calc_qed(mol) -> float:
    return float(QED.qed(mol))


def calc_sa(mol) -> float:
    if not _HAVE_SASCORER:
        raise RuntimeError("sascorer unavailable; cannot compute SA_Score")
    return float(sascorer.calculateScore(mol))


def calc_mw(mol) -> float:
    return float(Descriptors.MolWt(mol))


def calc_logp(mol) -> float:
    return float(Crippen.MolLogP(mol))


def calc_tpsa(mol) -> float:
    return float(rdMolDescriptors.CalcTPSA(mol))


def calc_hbd(mol) -> int:
    return int(rdMolDescriptors.CalcNumHBD(mol))


def calc_hba(mol) -> int:
    return int(rdMolDescriptors.CalcNumHBA(mol))


def calc_rotb(mol) -> int:
    return int(rdMolDescriptors.CalcNumRotatableBonds(mol))


PROPERTY_CALCS: Dict[str, Callable] = {
    "QED": calc_qed,
    "SA_Score": calc_sa,
    "MW": calc_mw,
    "LogP": calc_logp,
    "TPSA": calc_tpsa,
    "HBD": calc_hbd,
    "HBA": calc_hba,
    "RotB": calc_rotb,
}


# ---- PAINS / Brenk structural alerts (FilterCatalog; offline) ----
def build_alert_catalog(catalogs=("PAINS", "BRENK")) -> FilterCatalog:
    params = FilterCatalogParams()
    name_map = {
        "PAINS": FilterCatalogParams.FilterCatalogs.PAINS,
        "BRENK": FilterCatalogParams.FilterCatalogs.BRENK,
        "NIH": FilterCatalogParams.FilterCatalogs.NIH,
        "ZINC": FilterCatalogParams.FilterCatalogs.ZINC,
    }
    for c in catalogs:
        params.AddCatalog(name_map[c.upper()])
    return FilterCatalog(params)


def has_alert(mol, catalog: FilterCatalog) -> bool:
    return bool(catalog.HasMatch(mol))


# ---- Novelty: max ECFP4 Tanimoto to a reference set (lower = more novel) ----
def ecfp(mol, radius: int = 2, nbits: int = 2048):
    return AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=nbits)


def nearest_tanimoto(mol, ref_fps, radius: int = 2, nbits: int = 2048):
    """Return (max_similarity, argmax_index) vs a list of reference fingerprints."""
    if not ref_fps:
        return 0.0, -1
    fp = ecfp(mol, radius, nbits)
    sims = DataStructs.BulkTanimotoSimilarity(fp, ref_fps)
    j = int(np.argmax(sims))
    return float(sims[j]), j


# ======================================================================
# 4. Activity backends
# ======================================================================
def make_tdc_oracle(name: str):
    """Backend (a): pretrained TDC oracle. Returns callable(list[str])->list[float].
    Scores are already in [0,1] for the classifier oracles (DRD2, GSK3B, JNK3...)."""
    from tdc import Oracle
    oracle = Oracle(name=name)

    def _score(smiles: List[str]) -> List[float]:
        out = oracle(list(smiles))
        if isinstance(out, (int, float)):
            out = [out]
        return [float(x) for x in out]

    return _score


def train_qsar_backend(actives_smiles: List[str], inactives_smiles: List[str],
                       radius: int = 2, nbits: int = 2048, seed: int = 42):
    """Backend (b): quick QSAR surrogate (RandomForest on ECFP). Returns a
    callable(list[str])->list[float] giving P(active) in [0,1].

    Use when no TDC oracle exists for the target. Feed actives/inactives from a
    ChEMBL export (e.g. pChEMBL >= 6 as active) or any labelled CSV. This is a
    convenience surrogate, NOT a validated model -- report it as such."""
    from sklearn.ensemble import RandomForestClassifier

    def _fp_matrix(smis):
        X, keep = [], []
        for s in smis:
            m = Chem.MolFromSmiles(s)
            if m is None:
                continue
            arr = np.zeros((nbits,), dtype=np.int8)
            DataStructs.ConvertToNumpyArray(ecfp(m, radius, nbits), arr)
            X.append(arr)
            keep.append(s)
        return np.array(X), keep

    Xa, _ = _fp_matrix(actives_smiles)
    Xi, _ = _fp_matrix(inactives_smiles)
    X = np.vstack([Xa, Xi])
    y = np.r_[np.ones(len(Xa)), np.zeros(len(Xi))]
    clf = RandomForestClassifier(n_estimators=300, random_state=seed, n_jobs=-1)
    clf.fit(X, y)

    def _score(smiles: List[str]) -> List[float]:
        out = []
        for s in smiles:
            m = Chem.MolFromSmiles(s)
            if m is None:
                out.append(0.0)
                continue
            arr = np.zeros((nbits,), dtype=np.int8)
            DataStructs.ConvertToNumpyArray(ecfp(m, radius, nbits), arr)
            out.append(float(clf.predict_proba(arr.reshape(1, -1))[0, 1]))
        return out

    return _score


def make_vina_backend(*args, **kwargs):
    """Backend (c): AutoDock Vina docking score (optional; slow, most general).

    Not implemented inline -- see SKILL.md 'Docking backend' for the recipe using
    the installed `vina` + `autosite` CLIs. Docking returns kcal/mol (more
    negative = better); wrap with a `reverse_sigmoid` transform (e.g. low=-6,
    high=-11) to map to [0,1]. Provided as a documented extension point."""
    raise NotImplementedError(
        "Vina docking backend is a documented extension point; see SKILL.md."
    )


# ======================================================================
# 5. ScoringFunction: assemble components into a single fitness callable
# ======================================================================
class ScoringComponent:
    """One objective: a property (or the activity backend), transformed to [0,1]."""

    def __init__(self, name: str, transform: str = "identity",
                 params: Optional[dict] = None, weight: float = 1.0):
        if transform not in TRANSFORMS:
            raise ValueError(f"unknown transform '{transform}'")
        self.name = name
        self.transform = transform
        self.params = params or {}
        self.weight = float(weight)

    def desirability(self, raw: float) -> float:
        return float(TRANSFORMS[self.transform](raw, **self.params))


class ScoringFunction:
    """Combines an activity backend + property components into batch fitness.

    activity_fn : callable(list[str]) -> list[float]  (from a backend above)
    components  : list[ScoringComponent]. The component named 'activity' uses the
                  backend output; all others are computed from PROPERTY_CALCS.
    aggregation : key into AGGREGATIONS (default 'geometric_mean').
    """

    def __init__(self, activity_fn: Callable[[List[str]], List[float]],
                 components: List[ScoringComponent],
                 aggregation: str = "geometric_mean"):
        if aggregation not in AGGREGATIONS:
            raise ValueError(f"unknown aggregation '{aggregation}'")
        self.activity_fn = activity_fn
        self.components = components
        self.aggregation = aggregation

    def raw_properties(self, smiles: List[str]) -> Dict[str, List[float]]:
        """Compute every raw property referenced by the components (+ activity)."""
        mols = [Chem.MolFromSmiles(s) for s in smiles]
        cols: Dict[str, List[float]] = {}
        needs_activity = any(c.name == "activity" for c in self.components)
        if needs_activity:
            cols["activity"] = list(self.activity_fn(smiles))
        for c in self.components:
            if c.name == "activity":
                continue
            calc = PROPERTY_CALCS[c.name]
            vals = []
            for m in mols:
                try:
                    vals.append(float(calc(m)) if m is not None else float("nan"))
                except Exception:
                    vals.append(float("nan"))
            cols[c.name] = vals
        return cols

    def __call__(self, smiles: List[str]) -> List[float]:
        smiles = list(smiles)
        cols = self.raw_properties(smiles)
        agg = AGGREGATIONS[self.aggregation]
        weights = [c.weight for c in self.components]
        out = []
        for i, s in enumerate(smiles):
            if Chem.MolFromSmiles(s) is None:
                out.append(0.0)
                continue
            desir = []
            ok = True
            for c in self.components:
                raw = cols[c.name][i]
                if raw is None or (isinstance(raw, float) and math.isnan(raw)):
                    ok = False
                    break
                desir.append(c.desirability(raw))
            out.append(0.0 if not ok else float(agg(desir, weights)))
        return out


# ======================================================================
# 6. Presets
# ======================================================================
def preset_production(activity_fn) -> ScoringFunction:
    """RECOMMENDED default: activity x QED x (low SA). Gates on makeability so
    the GA cannot run away toward high-scoring unsynthesizable molecules.
    SA_Score in ~[1,10]; reverse_sigmoid(low=2.5, high=6) favors SA <~ 3-4."""
    comps = [
        ScoringComponent("activity", "identity", weight=1.0),
        ScoringComponent("QED", "identity", weight=1.0),
        ScoringComponent("SA_Score", "reverse_sigmoid",
                         {"low": 2.5, "high": 6.0, "k": 10.0}, weight=1.0),
    ]
    return ScoringFunction(activity_fn, comps, aggregation="geometric_mean")


def preset_drd2_benchmark(activity_fn) -> ScoringFunction:
    """Reproducibility preset: bare sqrt(activity x QED) -- the exact objective
    used in the completed DRD2 run and the Olivecrona/GuacaMol benchmark
    convention. NO synthesizability term (benchmark comparability only)."""
    comps = [
        ScoringComponent("activity", "identity", weight=1.0),
        ScoringComponent("QED", "identity", weight=1.0),
    ]
    return ScoringFunction(activity_fn, comps, aggregation="geometric_mean")


PRESETS = {"production": preset_production, "drd2_benchmark": preset_drd2_benchmark}


def build_scoring_from_config(cfg: dict, activity_fn) -> ScoringFunction:
    """Build a ScoringFunction from a config dict.

    cfg['objective'] may be a preset name ('production' | 'drd2_benchmark') OR a
    dict {aggregation: str, components: [{property, transform, params, weight}]}.
    """
    obj = cfg.get("objective", "production")
    if isinstance(obj, str):
        if obj not in PRESETS:
            raise ValueError(f"unknown objective preset '{obj}'")
        return PRESETS[obj](activity_fn)
    comps = []
    for c in obj["components"]:
        name = c["property"]
        comps.append(ScoringComponent(
            name=("activity" if name.lower() == "activity" else name),
            transform=c.get("transform", "identity"),
            params=c.get("params", {}),
            weight=float(c.get("weight", 1.0)),
        ))
    return ScoringFunction(activity_fn, comps,
                           aggregation=obj.get("aggregation", "geometric_mean"))
