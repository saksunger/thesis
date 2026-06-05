"""Anomaly detectors for Phase 5 (smoke + benchmark).

Wraps off-the-shelf detectors behind a small uniform interface so the
evaluators can iterate over them without case-analysis. All detectors
implement::

    fit(X_train)            -> self          # X_train assumed to be normal
    score_samples(X)        -> 1-D np.ndarray of higher-is-more-anomalous

The detector roster (Iter B):

- :class:`IsoForestDetector`     — sklearn IsolationForest
- :class:`LOFDetector`           — sklearn LocalOutlierFactor (novelty=True)
- :class:`PCAReconErrDetector`   — linear autoencoder via truncated SVD;
  per-row MSE is the anomaly score. The "minimum-viable AE" baseline.
- :class:`OneClassSVMDetector`   — sklearn OneClassSVM with RBF kernel.
  Distance-from-hyperplane is the anomaly score. Classical baseline,
  cited extensively in the anomaly-detection literature.
- :class:`MLPReconErrDetector`   — non-linear autoencoder built from a
  bottleneck `MLPRegressor` (encoder + decoder stacked into a single
  net via `MLPRegressor` symmetric architecture). Per-row reconstruction
  MSE is the anomaly score. The non-linear counterpart to PCA-AE; closes
  out the "5+ detectors" bar required by `docs/plan.md` Phase 5.

Note on imputation / scaling: a single sklearn Pipeline (median imputer +
standard scaler) is applied inside every detector, so detector-specific
preprocessing is removed from the eval script. The pipeline is fitted on
the training data only (no leakage from test data).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.neighbors import LocalOutlierFactor
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM


@dataclass
class _PreprocPipeline:
    """Lazy holder for the (impute + standardize) preprocessing pipeline."""

    pipe: Pipeline | None = None

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        self.pipe = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
            ]
        )
        return self.pipe.fit_transform(X)

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.pipe is None:
            raise RuntimeError("Preprocessing pipeline has not been fitted.")
        return self.pipe.transform(X)


def _as_matrix(X: pd.DataFrame | np.ndarray) -> np.ndarray:
    if isinstance(X, pd.DataFrame):
        return X.to_numpy(dtype=float, copy=True)
    arr = np.asarray(X, dtype=float)
    return arr


# -------------------------------------------------------------------------
# IsolationForest
# -------------------------------------------------------------------------

class IsoForestDetector:
    """Isolation Forest wrapper (sklearn 1.4+ API)."""

    name = "IsoForest"

    def __init__(
        self,
        n_estimators: int = 200,
        contamination: float | str = "auto",
        random_state: int = 42,
    ) -> None:
        self.n_estimators = n_estimators
        self.contamination = contamination
        self.random_state = random_state
        self._pre = _PreprocPipeline()
        self._model: IsolationForest | None = None

    def fit(self, X: pd.DataFrame | np.ndarray) -> "IsoForestDetector":
        Xn = self._pre.fit_transform(_as_matrix(X))
        self._model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state,
            n_jobs=-1,
        )
        self._model.fit(Xn)
        return self

    def score_samples(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError(f"{self.name} not fitted")
        Xn = self._pre.transform(_as_matrix(X))
        # sklearn convention: higher = more normal. Flip sign for
        # "higher = more anomalous".
        return -self._model.score_samples(Xn)


# -------------------------------------------------------------------------
# Local Outlier Factor
# -------------------------------------------------------------------------

class LOFDetector:
    """Local Outlier Factor with `novelty=True` so we can score test sets."""

    name = "LOF"

    def __init__(
        self,
        n_neighbors: int = 20,
        contamination: float | str = "auto",
    ) -> None:
        self.n_neighbors = n_neighbors
        self.contamination = contamination
        self._pre = _PreprocPipeline()
        self._model: LocalOutlierFactor | None = None

    def fit(self, X: pd.DataFrame | np.ndarray) -> "LOFDetector":
        Xn = self._pre.fit_transform(_as_matrix(X))
        # n_neighbors must be <= n_samples - 1 (sklearn restriction)
        k = max(2, min(self.n_neighbors, len(Xn) - 1))
        self._model = LocalOutlierFactor(
            n_neighbors=k,
            contamination=self.contamination,
            novelty=True,
            n_jobs=-1,
        )
        self._model.fit(Xn)
        return self

    def score_samples(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError(f"{self.name} not fitted")
        Xn = self._pre.transform(_as_matrix(X))
        return -self._model.score_samples(Xn)


# -------------------------------------------------------------------------
# Linear autoencoder via TruncatedSVD reconstruction error
# -------------------------------------------------------------------------

class PCAReconErrDetector:
    """Minimum-viable autoencoder: linear PCA via TruncatedSVD.

    Anomaly score = per-row reconstruction MSE after projecting onto the
    top-`n_components` principal components. Justification: a linear AE
    *is* PCA mathematically (in the squared-error limit), so this is the
    cleanest single-hyperparameter AE baseline. Iter B will swap in a
    non-linear MLP-AE for stronger benchmarks.
    """

    name = "PCA-AE"

    def __init__(self, n_components: int = 5, random_state: int = 42) -> None:
        self.n_components = n_components
        self.random_state = random_state
        self._pre = _PreprocPipeline()
        self._model: TruncatedSVD | None = None

    def fit(self, X: pd.DataFrame | np.ndarray) -> "PCAReconErrDetector":
        Xn = self._pre.fit_transform(_as_matrix(X))
        k = max(1, min(self.n_components, min(Xn.shape) - 1))
        self._model = TruncatedSVD(n_components=k, random_state=self.random_state)
        self._model.fit(Xn)
        return self

    def score_samples(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError(f"{self.name} not fitted")
        Xn = self._pre.transform(_as_matrix(X))
        Xn_proj = self._model.transform(Xn)
        Xn_recon = self._model.inverse_transform(Xn_proj)
        # Per-row MSE
        return np.mean((Xn - Xn_recon) ** 2, axis=1)


# -------------------------------------------------------------------------
# OneClassSVM
# -------------------------------------------------------------------------

class OneClassSVMDetector:
    """One-Class SVM with RBF kernel, fitted on normal data only.

    Score = -decision_function (sklearn convention is "higher = normal";
    we flip sign for the project-wide "higher = more anomalous" rule).

    Notes:
        - `nu` is the upper bound on the fraction of training errors and
          a lower bound on the support-vector fraction. Default 0.05 is
          a sane "expected contamination" prior, consistent with the
          `flag_frac=0.05` reporting convention.
        - `gamma="scale"` (sklearn ≥ 0.22 default) is robust without
          per-dataset tuning.
        - One-Class SVM is O(n²) at fit time; we cap training samples at
          `max_train` (default 5,000) by uniform random subsample when
          the input exceeds the cap, to keep wall-clock bounded on the
          medium timeline (~1M windows).
    """

    name = "OneClassSVM"

    def __init__(
        self,
        nu: float = 0.05,
        gamma: str | float = "scale",
        kernel: str = "rbf",
        max_train: int = 5000,
        random_state: int = 42,
    ) -> None:
        self.nu = nu
        self.gamma = gamma
        self.kernel = kernel
        self.max_train = max_train
        self.random_state = random_state
        self._pre = _PreprocPipeline()
        self._model: OneClassSVM | None = None

    def fit(self, X: pd.DataFrame | np.ndarray) -> "OneClassSVMDetector":
        Xn = self._pre.fit_transform(_as_matrix(X))
        if len(Xn) > self.max_train:
            rng = np.random.default_rng(self.random_state)
            idx = rng.choice(len(Xn), size=self.max_train, replace=False)
            Xn = Xn[idx]
        self._model = OneClassSVM(
            nu=self.nu,
            gamma=self.gamma,
            kernel=self.kernel,
        )
        self._model.fit(Xn)
        return self

    def score_samples(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError(f"{self.name} not fitted")
        Xn = self._pre.transform(_as_matrix(X))
        return -self._model.decision_function(Xn)


# -------------------------------------------------------------------------
# Non-linear autoencoder via MLPRegressor
# -------------------------------------------------------------------------

class MLPReconErrDetector:
    """Non-linear autoencoder built from a bottleneck MLPRegressor.

    The MLP is trained to map X -> X with a narrow hidden layer (the
    bottleneck). Per-row reconstruction MSE is the anomaly score: rows
    that the encoder cannot compress + decoder cannot reconstruct
    produce a high score.

    Architecture:
        input (d) -> hidden_layer_sizes (default (16, 8, 16)) -> output (d)

    Defensible choices:
        - `(16, 8, 16)` is the canonical "AE" topology (symmetric
          encoder + decoder around a 8-unit bottleneck). For our ~25-d
          feature space this is a ~3:1 compression ratio.
        - `solver="adam"` for deterministic optimisation given
          `random_state`.
        - `early_stopping=True` to avoid overfitting on the normal
          training set when the train pool is small.
    """

    name = "MLP-AE"

    def __init__(
        self,
        hidden_layer_sizes: tuple[int, ...] = (16, 8, 16),
        max_iter: int = 200,
        random_state: int = 42,
        early_stopping: bool = True,
        max_train: int = 20000,
    ) -> None:
        self.hidden_layer_sizes = hidden_layer_sizes
        self.max_iter = max_iter
        self.random_state = random_state
        self.early_stopping = early_stopping
        self.max_train = max_train
        self._pre = _PreprocPipeline()
        self._model: MLPRegressor | None = None

    def fit(self, X: pd.DataFrame | np.ndarray) -> "MLPReconErrDetector":
        Xn = self._pre.fit_transform(_as_matrix(X))
        if len(Xn) > self.max_train:
            rng = np.random.default_rng(self.random_state)
            idx = rng.choice(len(Xn), size=self.max_train, replace=False)
            Xn = Xn[idx]
        # MLPRegressor as autoencoder: target = input
        self._model = MLPRegressor(
            hidden_layer_sizes=self.hidden_layer_sizes,
            activation="relu",
            solver="adam",
            max_iter=self.max_iter,
            random_state=self.random_state,
            early_stopping=self.early_stopping,
            validation_fraction=0.1 if self.early_stopping else 0.0,
            n_iter_no_change=10,
        )
        # Suppress non-convergence warnings; we cap iters intentionally.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._model.fit(Xn, Xn)
        return self

    def score_samples(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError(f"{self.name} not fitted")
        Xn = self._pre.transform(_as_matrix(X))
        Xn_recon = self._model.predict(Xn)
        return np.mean((Xn - Xn_recon) ** 2, axis=1)


# -------------------------------------------------------------------------
# Registry
# -------------------------------------------------------------------------

def all_detectors() -> list:
    """Return the Iter A smoke detector roster (3 classical detectors).

    Kept for back-compat with `smoke_eval.py`. The full Iter B roster is
    in :func:`benchmark_detectors`.
    """
    return [
        IsoForestDetector(),
        LOFDetector(),
        PCAReconErrDetector(),
    ]


def benchmark_detectors() -> list:
    """Return the Phase 5 Iter B detector roster (5 detectors).

    Hits the `docs/plan.md` "5+ anomaly detectors" bar:
    classical (IsoForest, LOF, OneClassSVM) + AE family (PCA-AE, MLP-AE).
    Each is constructed with thesis-defensible default hyperparameters.
    """
    return [
        IsoForestDetector(),
        LOFDetector(),
        OneClassSVMDetector(),
        PCAReconErrDetector(),
        MLPReconErrDetector(),
    ]
