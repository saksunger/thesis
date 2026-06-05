"""Baseline anomaly detectors for Phase 5 Iter A (smoke).

Wraps three off-the-shelf detectors behind a small uniform interface so
the smoke evaluation can iterate over them without case-analysis:

- :class:`IsoForestDetector`  — sklearn IsolationForest
- :class:`LOFDetector`        — sklearn LocalOutlierFactor (novelty=True)
- :class:`PCAReconErrDetector` — linear autoencoder via truncated SVD; the
  anomaly score is the per-row reconstruction error. Defensible as the
  "minimum-viable AE" baseline; Iter B will swap in a non-linear MLP-AE.

All detectors implement::

    fit(X_train)            -> self          # X_train assumed to be normal
    score_samples(X)        -> 1-D np.ndarray of higher-is-more-anomalous

Note on imputation / scaling: a single sklearn Pipeline (median imputer +
standard scaler) is applied inside every detector, so detector-specific
preprocessing is removed from the smoke script. The pipeline is fitted on
the training data only (no leakage from test data).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.neighbors import LocalOutlierFactor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


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


def all_detectors() -> list:
    """Return one instance of every smoke detector with default hparams."""
    return [
        IsoForestDetector(),
        LOFDetector(),
        PCAReconErrDetector(),
    ]
