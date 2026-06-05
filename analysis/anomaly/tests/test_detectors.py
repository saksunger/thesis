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
    MLPReconErrDetector,
    OneClassSVMDetector,
    PCAReconErrDetector,
    all_detectors,
    benchmark_detectors,
)


def _mk_blobs(n_normal: int = 200, n_anom: int = 20, n_features: int = 5, seed: int = 0):
    rng = np.random.default_rng(seed)
    normal = rng.normal(0, 1, size=(n_normal, n_features))
    anom = rng.normal(10, 0.5, size=(n_anom, n_features))  # far from origin
    X = np.vstack([normal, anom])
    y = np.array([0] * n_normal + [1] * n_anom)
    return X, y, normal


_ALL_DETECTOR_CLASSES = [
    IsoForestDetector,
    LOFDetector,
    PCAReconErrDetector,
    OneClassSVMDetector,
    MLPReconErrDetector,
]


@pytest.mark.parametrize("Detector", _ALL_DETECTOR_CLASSES)
def test_detector_fit_and_score_returns_1d_array(Detector):
    X, _, normal = _mk_blobs()
    det = Detector()
    det.fit(normal)
    scores = det.score_samples(X)
    assert scores.shape == (len(X),)
    assert np.isfinite(scores).all()


@pytest.mark.parametrize("Detector", _ALL_DETECTOR_CLASSES)
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
    for det in benchmark_detectors():
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


def test_benchmark_detectors_includes_smoke_roster():
    smoke_names = {d.name for d in all_detectors()}
    bench_names = {d.name for d in benchmark_detectors()}
    assert smoke_names.issubset(bench_names), (
        f"benchmark roster {bench_names} must include smoke roster {smoke_names}"
    )


def test_benchmark_detectors_has_at_least_five():
    # plan.md acceptance bar: "5+ anomaly detectors"
    names = [d.name for d in benchmark_detectors()]
    assert len(set(names)) >= 5, f"need 5+ detectors, got {names}"


def test_one_class_svm_caps_train_set_for_speed():
    # Train pool > max_train must not OOM / time-out; downsample applied
    rng = np.random.default_rng(0)
    big_normal = rng.normal(0, 1, size=(8000, 5))  # > default max_train=5000
    det = OneClassSVMDetector(max_train=2000)
    det.fit(big_normal)
    # n_support must be <= max_train (no use of full pool)
    assert det._model is not None
    assert sum(det._model.n_support_) <= 2000


def test_mlp_ae_reconstruction_is_better_on_normal_than_anomaly():
    # Spot-check the AE story: low recon error on normal, high on outlier.
    _, _, normal = _mk_blobs(n_normal=400, n_features=8, seed=7)
    rng = np.random.default_rng(7)
    anom = rng.normal(8, 0.3, size=(60, 8))
    det = MLPReconErrDetector(max_iter=300).fit(normal)
    err_normal = det.score_samples(normal).mean()
    err_anom = det.score_samples(anom).mean()
    assert err_anom > err_normal, (
        f"MLP-AE recon error not higher on anomaly "
        f"(normal={err_normal:.3f} >= anom={err_anom:.3f})"
    )
