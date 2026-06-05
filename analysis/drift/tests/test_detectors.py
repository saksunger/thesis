"""Smoke + API tests for drift detectors."""

from __future__ import annotations

import numpy as np
import pytest

from analysis.drift.detectors import (
    ADWINDetector,
    DDMDetector,
    EDDMDetector,
    EnergyBatchDetector,
    KSWINDetector,
    MMDBatchDetector,
    PageHinkleyDetector,
    benchmark_detectors,
    detector_names,
)


def _shifted_stream(n_baseline=200, n_after=100, mu_shift=5.0, seed=0):
    rng = np.random.default_rng(seed)
    return np.concatenate([
        rng.normal(0, 1, n_baseline),
        rng.normal(mu_shift, 1, n_after),
    ])


_STREAM_DETECTORS = [ADWINDetector, KSWINDetector, PageHinkleyDetector]
_BIN_DETECTORS = [DDMDetector, EDDMDetector]
_BATCH_DETECTORS = [MMDBatchDetector, EnergyBatchDetector]


def test_benchmark_detector_roster_has_seven():
    names = detector_names()
    assert len(set(names)) == 7
    assert set(names) >= {"ADWIN", "KSWIN", "PageHinkley",
                          "DDM", "EDDM", "MMD-batch", "Energy-batch"}


@pytest.mark.parametrize("Det", _STREAM_DETECTORS)
def test_stream_detector_detects_sharp_shift(Det):
    stream = _shifted_stream()
    det = Det()
    detected_at = None
    for i, v in enumerate(stream):
        if det.update(v):
            detected_at = i
            break
    assert detected_at is not None, f"{Det.__name__} never detected"
    # True drift at index 200; allow generous post-detection budget
    assert 195 <= detected_at <= 260, (
        f"{Det.__name__}: detected at {detected_at}, expected near 200"
    )


@pytest.mark.parametrize("Det", _BIN_DETECTORS)
def test_binarising_detector_detects_with_zscore_direction(Det):
    stream = _shifted_stream()
    det = Det(direction="absdev_zscore", z=2.0, warmup_n=30)
    detected_at = None
    for i, v in enumerate(stream):
        if det.update(v):
            detected_at = i
            break
    assert detected_at is not None, f"{Det.__name__} never detected"
    assert 195 <= detected_at <= 290, (
        f"{Det.__name__}: detected at {detected_at}, expected after 200"
    )


@pytest.mark.parametrize("Det", _BATCH_DETECTORS)
def test_batch_detector_detects_sharp_shift(Det):
    stream = _shifted_stream(n_baseline=120, n_after=120, mu_shift=5.0)
    det = Det(ref_size=60, active_size=60, alpha=0.01, n_perm=100)
    detected_at = None
    for i, v in enumerate(stream):
        if det.update(v):
            detected_at = i
            break
    assert detected_at is not None, f"{Det.__name__} never detected"
    # Batch detectors need ref+active filled before they can fire
    assert detected_at >= 60, "fired before reference window full"
    # And ideally after the shift at index 120
    assert detected_at <= 240


def test_all_detectors_handle_nan_gracefully():
    # NaNs should be ignored; detector should not crash and not falsely fire
    stream = np.array([np.nan, np.nan, 0.0, 1.0, np.nan, 0.5, 0.4])
    for Det in [ADWINDetector, KSWINDetector, PageHinkleyDetector,
                DDMDetector, EDDMDetector, MMDBatchDetector, EnergyBatchDetector]:
        det = Det()
        # Just verify no exception is raised
        for v in stream:
            det.update(v)


def test_no_drift_in_pure_baseline_no_false_alarms_for_adwin():
    rng = np.random.default_rng(123)
    stream = rng.normal(0, 1, 500)
    det = ADWINDetector()
    fires = sum(int(det.update(v)) for v in stream)
    # ADWIN occasionally false-fires; allow up to 2 false alarms in 500 samples
    assert fires <= 2, f"ADWIN false-fired {fires} times on pure baseline"


def test_benchmark_detectors_returns_fresh_instances():
    a = benchmark_detectors()
    b = benchmark_detectors()
    assert a is not b
    assert all(x is not y for x, y in zip(a, b))


def test_binariser_unknown_direction_raises():
    det = DDMDetector(direction="bogus")
    with pytest.raises(ValueError, match="unknown direction"):
        det.update(1.0)
