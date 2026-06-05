"""Surrogate models for the config-performance map.

Uniform interface (mirrors the Phase 5 anomaly-detector convention)::

    fit(X, y)             -> self
    predict(X)            -> ndarray of shape (n_samples,)
    predict_interval(X)   -> tuple (lo, mid, hi), each ndarray of (n_samples,)
                             — :class:`QuantileGBSurrogate` and
                             :class:`ConformalQuantileGBSurrogate` implement
                             this.

Three classes are shipped in Iter A:

- :class:`HistGBSurrogate`
    sklearn :class:`HistGradientBoostingRegressor` (point estimate).
    The "default" model used for headline MAE / R^2 tables. Fast on
    the 360-row sweep (~50 ms / fit), supports missing values natively
    (defensive — the parquet has none today but future sweep extensions
    might).

- :class:`QuantileGBSurrogate`
    Three :class:`HistGradientBoostingRegressor` instances trained with
    `loss='quantile'` at q in {0.1, 0.5, 0.9}. Yields an 80 %
    prediction interval per sample at no additional inference cost.
    *Empirically miscalibrated on n=360* (intervals systematically too
    narrow — see Phase 8 Iter A acceptance C4 in ``benchmark_eval.py``).
    Kept as the "naive" baseline for the Chapter 8 narrative.

- :class:`ConformalQuantileGBSurrogate`
    :class:`QuantileGBSurrogate` wrapped with **split-conformal
    prediction** (Vovk 2005; Romano, Patterson & Candès 2019,
    "Conformalized Quantile Regression"). Holds out a calibration
    fraction at fit time, computes the empirical conformity score of
    the held-out residuals against the naive interval, and widens the
    interval by that score at predict time. The resulting interval has
    a finite-sample coverage guarantee at the requested miscoverage
    level (assuming exchangeability). Used for the Iter A acceptance
    C4 (reliability coverage in [0.75, 0.90]).

The wrappers do NOT do any feature scaling — gradient boosting is
scale-invariant, and the scenario percentiles have meaningful absolute
units (dBm / dB) that the trees benefit from preserving.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def _to_2d(X: pd.DataFrame | np.ndarray) -> np.ndarray:
    if isinstance(X, pd.DataFrame):
        return X.to_numpy(dtype=float, copy=True)
    arr = np.asarray(X, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    return arr


def _to_1d(y: pd.Series | pd.DataFrame | np.ndarray) -> np.ndarray:
    if isinstance(y, pd.DataFrame):
        if y.shape[1] != 1:
            raise ValueError(
                f"y must be 1-D or a single-column DataFrame; got shape {y.shape}"
            )
        return y.to_numpy(dtype=float).ravel()
    if isinstance(y, pd.Series):
        return y.to_numpy(dtype=float)
    arr = np.asarray(y, dtype=float)
    return arr.ravel()


# -------------------------------------------------------------------------
# Point estimate
# -------------------------------------------------------------------------

@dataclass
class HistGBSurrogate:
    """sklearn HistGradientBoostingRegressor wrapper (point estimate).

    Default hyper-parameters tuned for the 360-row Phase 4c sweep:
        - `max_iter=500` (vs sklearn default 100): the sweep is small so
          we afford more trees; early stopping kicks in long before this.
        - `learning_rate=0.05`: conservative — bias-variance favours
          slow learning on small data.
        - `max_depth=6`: deep enough for non-linear (TTT, hyst, A3)
          interactions without over-fitting on 360 rows.
        - `early_stopping=True`, `validation_fraction=0.1`: defensive
          on per-fold CV.
    """

    name: str = "HistGB"
    max_iter: int = 500
    learning_rate: float = 0.05
    max_depth: int = 6
    min_samples_leaf: int = 5
    random_state: int = 42

    def __post_init__(self) -> None:
        self._model: HistGradientBoostingRegressor | None = None

    def fit(self, X: pd.DataFrame | np.ndarray, y) -> "HistGBSurrogate":
        Xn = _to_2d(X)
        yn = _to_1d(y)
        self._model = HistGradientBoostingRegressor(
            max_iter=self.max_iter,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf,
            random_state=self.random_state,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=20,
        )
        self._model.fit(Xn, yn)
        return self

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError(f"{self.name} not fitted")
        return np.asarray(self._model.predict(_to_2d(X)), dtype=float)


# -------------------------------------------------------------------------
# Quantile estimate (3 models, 1 per quantile)
# -------------------------------------------------------------------------

@dataclass
class QuantileGBSurrogate:
    """Three HistGradientBoostingRegressor instances at q in {q_lo, 0.5, q_hi}.

    Yields a prediction interval of nominal width (q_hi - q_lo). For
    `q_lo=0.1, q_hi=0.9` (Iter A default), the interval is the 80 %
    central credible band — Iter A acceptance C4 checks empirical
    coverage on the held-out fold.

    Memory note: holding 3 trained GBMs triples the per-target footprint
    vs HistGBSurrogate. For 3 targets × 5 CV folds × 3 quantiles = 45
    GBMs total, ~1 MB each → trivial. The fit budget is < 5 s total on
    the 360-row sweep.
    """

    name: str = "QuantileGB"
    q_lo: float = 0.1
    q_hi: float = 0.9
    max_iter: int = 500
    learning_rate: float = 0.05
    max_depth: int = 6
    min_samples_leaf: int = 5
    random_state: int = 42

    def __post_init__(self) -> None:
        if not (0.0 < self.q_lo < 0.5):
            raise ValueError(f"q_lo must be in (0, 0.5) (got {self.q_lo})")
        if not (0.5 < self.q_hi < 1.0):
            raise ValueError(f"q_hi must be in (0.5, 1) (got {self.q_hi})")
        self._models: dict[float, HistGradientBoostingRegressor] | None = None

    @property
    def nominal_coverage(self) -> float:
        """The interval's nominal coverage = q_hi - q_lo (e.g. 0.8 for 0.1/0.9)."""
        return float(self.q_hi - self.q_lo)

    def fit(self, X: pd.DataFrame | np.ndarray, y) -> "QuantileGBSurrogate":
        Xn = _to_2d(X)
        yn = _to_1d(y)
        models: dict[float, HistGradientBoostingRegressor] = {}
        for q in (self.q_lo, 0.5, self.q_hi):
            m = HistGradientBoostingRegressor(
                loss="quantile",
                quantile=q,
                max_iter=self.max_iter,
                learning_rate=self.learning_rate,
                max_depth=self.max_depth,
                min_samples_leaf=self.min_samples_leaf,
                random_state=self.random_state,
                early_stopping=True,
                validation_fraction=0.1,
                n_iter_no_change=20,
            )
            m.fit(Xn, yn)
            models[q] = m
        self._models = models
        return self

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Median (q=0.5) prediction, taken from the monotonised triple.

        Delegating to :meth:`predict_interval` guarantees the invariant
        ``predict(X) == predict_interval(X)[1]`` so downstream code that
        mixes point and interval calls never sees the median straddle
        the lo/hi bounds (which the 3 independent quantile models can
        violate in pathological corners of feature space).
        """
        _, mid, _ = self.predict_interval(X)
        return mid

    def predict_interval(
        self, X: pd.DataFrame | np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (lo, mid, hi) prediction arrays. Each is a 1-D ndarray."""
        if self._models is None:
            raise RuntimeError(f"{self.name} not fitted")
        Xn = _to_2d(X)
        lo = np.asarray(self._models[self.q_lo].predict(Xn), dtype=float)
        mid = np.asarray(self._models[0.5].predict(Xn), dtype=float)
        hi = np.asarray(self._models[self.q_hi].predict(Xn), dtype=float)
        # Enforce monotonicity (the 3 quantile models are independent so
        # in pathological corners of feature space `lo` may exceed `mid`
        # or `mid` may exceed `hi`. Sort per-row to keep the interval
        # well-defined for downstream coverage / inverse-query code.)
        stacked = np.sort(np.stack([lo, mid, hi], axis=1), axis=1)
        return stacked[:, 0], stacked[:, 1], stacked[:, 2]


# -------------------------------------------------------------------------
# Conformal wrapper around QuantileGBSurrogate
# -------------------------------------------------------------------------

@dataclass
class ConformalQuantileGBSurrogate:
    """Split-conformal calibration on top of :class:`QuantileGBSurrogate`.

    Algorithm (Romano, Patterson & Candès 2019, "Conformalized Quantile
    Regression", aka CQR):

        1. Random split fit-set into a training half and a calibration
           half (default 75/25).
        2. Fit the underlying QuantileGB on the training half.
        3. Score the calibration half with naive ``predict_interval``,
           compute conformity scores
               E_i = max(lo_i - y_i, y_i - hi_i)
           (positive when y is outside the interval).
        4. Let q_hat = empirical (1 - alpha) * (1 + 1/n_cal) quantile
           of {E_i}. (The (1 + 1/n_cal) factor is the standard
           finite-sample correction.)
        5. At predict time, widen the naive interval symmetrically by
           q_hat:  [lo - q_hat, mid, hi + q_hat].

    Why this works:
        Under exchangeability (i.i.d. enough that the rank of a new
        sample's conformity score among the calibration scores is
        uniform on {1, ..., n_cal + 1}), the resulting interval has
        empirical coverage >= 1 - alpha in finite samples. No model
        assumption needed — this is a "wrap any quantile regressor"
        recipe. Phase 8 Iter A acceptance C4 directly tests the
        guarantee.

    Caveat:
        Calibration costs ~25 % of training rows. On the 360-row sweep
        this is ~90 calibration samples per fold, comfortably above the
        ~30-row floor where the q_hat estimator stabilises. For Iter B
        a larger sweep could reduce calibration_frac to 0.15.
    """

    name: str = "ConformalQuantileGB"
    q_lo: float = 0.1
    q_hi: float = 0.9
    calibration_frac: float = 0.25
    max_iter: int = 500
    learning_rate: float = 0.05
    max_depth: int = 6
    min_samples_leaf: int = 5
    random_state: int = 42

    def __post_init__(self) -> None:
        if not (0.0 < self.q_lo < 0.5):
            raise ValueError(f"q_lo must be in (0, 0.5) (got {self.q_lo})")
        if not (0.5 < self.q_hi < 1.0):
            raise ValueError(f"q_hi must be in (0.5, 1) (got {self.q_hi})")
        if not (0.05 <= self.calibration_frac <= 0.5):
            raise ValueError(
                f"calibration_frac must be in [0.05, 0.5] "
                f"(got {self.calibration_frac})"
            )
        self._inner: QuantileGBSurrogate | None = None
        self._q_hat: float | None = None
        self._n_calibration: int | None = None

    @property
    def nominal_coverage(self) -> float:
        return float(self.q_hi - self.q_lo)

    def fit(self, X, y) -> "ConformalQuantileGBSurrogate":
        Xn = _to_2d(X)
        yn = _to_1d(y)
        n = Xn.shape[0]
        if n < 20:
            raise ValueError(
                f"ConformalQuantileGB needs >= 20 fit samples to leave a "
                f"meaningful calibration set; got {n}"
            )
        rng = np.random.default_rng(self.random_state)
        idx = rng.permutation(n)
        n_cal = max(int(round(n * self.calibration_frac)), 5)
        cal_idx = idx[:n_cal]
        tr_idx = idx[n_cal:]
        X_tr, y_tr = Xn[tr_idx], yn[tr_idx]
        X_cal, y_cal = Xn[cal_idx], yn[cal_idx]

        self._inner = QuantileGBSurrogate(
            q_lo=self.q_lo, q_hi=self.q_hi,
            max_iter=self.max_iter, learning_rate=self.learning_rate,
            max_depth=self.max_depth, min_samples_leaf=self.min_samples_leaf,
            random_state=self.random_state,
        ).fit(X_tr, y_tr)

        lo_cal, _mid_cal, hi_cal = self._inner.predict_interval(X_cal)
        # CQR conformity score: max(lo - y, y - hi); positive iff outside the interval
        e = np.maximum(lo_cal - y_cal, y_cal - hi_cal)
        # Finite-sample-corrected (1 - alpha) quantile, alpha = miscoverage = 1 - nominal
        alpha = 1.0 - self.nominal_coverage
        level = float(np.ceil((n_cal + 1) * (1.0 - alpha))) / float(n_cal)
        level = float(np.clip(level, 0.0, 1.0))
        self._q_hat = float(np.quantile(e, level)) if n_cal > 0 else 0.0
        self._n_calibration = int(n_cal)
        return self

    def predict(self, X) -> np.ndarray:
        _, mid, _ = self.predict_interval(X)
        return mid

    def predict_interval(self, X) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if self._inner is None or self._q_hat is None:
            raise RuntimeError(f"{self.name} not fitted")
        lo, mid, hi = self._inner.predict_interval(X)
        return lo - self._q_hat, mid, hi + self._q_hat


# -------------------------------------------------------------------------
# Per-target factory
# -------------------------------------------------------------------------

def fit_per_target(
    surrogate_factory,
    X: pd.DataFrame,
    Y: pd.DataFrame,
    targets: tuple[str, ...] | None = None,
) -> dict[str, "HistGBSurrogate | QuantileGBSurrogate"]:
    """Fit one fresh surrogate instance per target column.

    Args:
        surrogate_factory : zero-arg callable returning a fresh surrogate
                            (e.g. `lambda: HistGBSurrogate()`).
        X : feature DataFrame, n_samples x n_features.
        Y : target DataFrame, n_samples x n_targets.
        targets : optional subset of Y.columns to fit. Default = all.

    Returns:
        dict mapping target name -> fitted surrogate.
    """
    out: dict[str, "HistGBSurrogate | QuantileGBSurrogate"] = {}
    cols = list(Y.columns) if targets is None else [t for t in targets if t in Y.columns]
    for t in cols:
        out[t] = surrogate_factory().fit(X, Y[t])
    return out


# -------------------------------------------------------------------------
# Registry
# -------------------------------------------------------------------------

def benchmark_surrogates() -> Mapping[str, "callable"]:
    """Return the Iter A surrogate registry as `name -> factory`.

    The factory takes no args and produces a freshly-constructed model.
    Used by the CV runner to instantiate one model per (target, fold).
    """
    return {
        "HistGB":              lambda: HistGBSurrogate(),
        "QuantileGB":          lambda: QuantileGBSurrogate(),
        "ConformalQuantileGB": lambda: ConformalQuantileGBSurrogate(),
    }
