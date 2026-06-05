"""Smoke unit tests for baseline anomaly detectors.

These tests verify (a) the API surface (fit + score_samples works on
plain DataFrames / ndarrays) and (b) that on a trivially separable
synthetic dataset the detectors actually score injected outliers higher
than normal points. They do NOT test detector quality — that's the
smoke_eval pipeline's job on real timeline data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.anomaly.detectors import (
    IsoForestDetector,
    LOFDetector,
    PCAReconErrDetector,
    all_detectors,
)


def _mk_blobs(n_normal: int = 200, n_anom: int = 20, n_features: int = 5, seed: int = 0):
    rng = np.random.default_rng(seed)
    normal = rng.normal(0, 1, size=(n_normal, n_features))
    anom = rng.normal(10, 0.5, size=(n_anom, n_features))  # far from origin
    X = np.vstack([normal, anom])
    y = np.array([0] * n_normal + [1] * n_anom)
    return X, y, normal


@pytest.mark.parametrize("Detector", [IsoForestDetector, LOFDetector, PCAReconErrDetector])
def test_detector_fit_and_score_returns_1d_array(Detector):
    X, _, normal = _mk_blobs()
    det = Detector()
    det.fit(normal)
    scores = det.score_samples(X)
    assert scores.shape == (len(X),)
    assert np.isfinite(scores).all()


@pytest.mark.parametrize("Detector", [IsoForestDetector, LOFDetector, PCAReconErrDetector])
def test_detector_scores_outliers_higher_on_separable_blob(Detector):
    X, y, normal = _mk_blobs(n_normal=500, n_anom=50)
    det = Detector().fit(normal)
    scores = det.score_samples(X)
    # Mean score on anomalies should beat mean on normals by > 1 std
    anom_mean = scores[y == 1].mean()
    norm_mean = scores[y == 0].mean()
    norm_std = scores[y == 0].std()
    assert anom_mean > norm_mean + norm_std, (
        f"{Detector.__name__}: anom_mean={anom_mean:.3f} not > "
        f"norm_mean={norm_mean:.3f} + std={norm_std:.3f}"
    )


def test_score_samples_before_fit_raises():
    X, _, _ = _mk_blobs()
    for det in all_detectors():
        with pytest.raises(RuntimeError):
            det.score_samples(X)


def test_accepts_dataframe_input():
    X, _, normal = _mk_blobs()
    df = pd.DataFrame(normal, columns=[f"f{i}" for i in range(normal.shape[1])])
    df_test = pd.DataFrame(X, columns=df.columns)
    det = IsoForestDetector().fit(df)
    scores = det.score_samples(df_test)
    assert scores.shape == (len(df_test),)


def test_handles_nan_via_imputer():
    X, _, normal = _mk_blobs()
    normal_with_nan = normal.copy()
    normal_with_nan[0, 0] = np.nan
    det = IsoForestDetector().fit(normal_with_nan)
    scores = det.score_samples(normal_with_nan)
    assert np.isfinite(scores).all(), "NaN must be imputed, not propagated"


def test_all_detectors_have_distinct_names():
    names = [d.name for d in all_detectors()]
    assert len(set(names)) == len(names), f"detector names collide: {names}"
