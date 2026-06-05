"""Unit tests for analysis.drift.metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analysis.drift.metrics import (
    BootstrapLatencyResult,
    bootstrap_latency_ci,
    detection_latency,
    fpr_per_detector_stream,
)


# ---------------------------------------------------------------------------
# detection_latency
# ---------------------------------------------------------------------------

def _gtd(rows):
    return pd.DataFrame(rows)


def test_detection_latency_picks_earliest_in_window():
    gtd = _gtd([{
        "drift_id": "D-1", "start_time_s": 100.0, "end_time_s": 160.0,
        "affected_kpis": "hosr",
        "phase_id_start": 1, "phase_id_end": 1, "expected_direction": "shape", "note": "",
    }])
    detections = pd.DataFrame([
        {"detector": "ADWIN", "stream_name": "hosr_rolling", "time_s": 105.0},
        {"detector": "ADWIN", "stream_name": "hosr_rolling", "time_s": 120.0},
    ])
    out = detection_latency(detections, gtd)
    row = out[(out["detector"] == "ADWIN") & (out["stream_name"] == "hosr_rolling")].iloc[0]
    assert row["detection_time_s"] == 105.0
    assert row["latency_s"] == 5.0
    assert row["missed"] == 0


def test_detection_latency_marks_missed_when_no_firing_in_window():
    gtd = _gtd([{
        "drift_id": "D-1", "start_time_s": 100.0, "end_time_s": 160.0,
        "affected_kpis": "hosr",
        "phase_id_start": 1, "phase_id_end": 1, "expected_direction": "shape", "note": "",
    }])
    detections = pd.DataFrame([
        {"detector": "ADWIN", "stream_name": "hosr_rolling", "time_s": 50.0},  # before
        {"detector": "ADWIN", "stream_name": "hosr_rolling", "time_s": 300.0},  # too late
    ])
    out = detection_latency(detections, gtd, max_latency_s=30.0)
    row = out[(out["detector"] == "ADWIN") & (out["stream_name"] == "hosr_rolling")].iloc[0]
    assert row["missed"] == 1
    assert np.isnan(row["latency_s"])


def test_detection_latency_deduplicates_drift_instances():
    # Two D-2 firings -> two instance keys (#1, #2)
    gtd = _gtd([
        {"drift_id": "D-2", "start_time_s": 100.0, "end_time_s": 120.0,
         "affected_kpis": "rsrp_serving_dbm",
         "phase_id_start": 1, "phase_id_end": 1, "expected_direction": "shape", "note": ""},
        {"drift_id": "D-2", "start_time_s": 300.0, "end_time_s": 320.0,
         "affected_kpis": "rsrp_serving_dbm",
         "phase_id_start": 1, "phase_id_end": 1, "expected_direction": "shape", "note": ""},
    ])
    detections = pd.DataFrame([
        {"detector": "ADWIN", "stream_name": "rsrp_serving_mean", "time_s": 105.0},
        {"detector": "ADWIN", "stream_name": "rsrp_serving_mean", "time_s": 310.0},
    ])
    out = detection_latency(detections, gtd)
    instances = set(out["drift_instance"].unique())
    assert instances == {"D-2#1", "D-2#2"}


def test_detection_latency_handles_empty_detections():
    gtd = _gtd([{
        "drift_id": "D-1", "start_time_s": 0.0, "end_time_s": 10.0,
        "affected_kpis": "hosr",
        "phase_id_start": 1, "phase_id_end": 1, "expected_direction": "shape", "note": "",
    }])
    out = detection_latency(pd.DataFrame(), gtd)
    # Empty detections -> nothing to enumerate; the GT exists but no (det, stream) appears
    assert out.empty


# ---------------------------------------------------------------------------
# fpr_per_detector_stream
# ---------------------------------------------------------------------------

def test_fpr_per_detector_stream_basic():
    # 10 baseline seconds + 5 drift seconds in one stream
    streams = pd.DataFrame({
        "time_s": list(range(15)),
        "stream_name": ["rsrp_serving_mean"] * 15,
        "scenario_name": ["baseline"] * 10 + ["drift_x"] * 5,
        "value": [0.0] * 15,
        "phase_id": [1] * 15,
    })
    detections = pd.DataFrame([
        {"detector": "ADWIN", "stream_name": "rsrp_serving_mean", "time_s": 3.0},  # baseline
        {"detector": "ADWIN", "stream_name": "rsrp_serving_mean", "time_s": 7.0},  # baseline
        {"detector": "ADWIN", "stream_name": "rsrp_serving_mean", "time_s": 12.0},  # drift -> not FP
    ])
    out = fpr_per_detector_stream(detections, streams)
    row = out[(out["detector"] == "ADWIN") & (out["stream_name"] == "rsrp_serving_mean")].iloc[0]
    assert row["n_baseline_s"] == 10
    assert row["n_false_alarms"] == 2
    assert abs(row["fpr_per_s"] - 0.2) < 1e-9


def test_fpr_zero_when_no_detections_in_baseline():
    streams = pd.DataFrame({
        "time_s": list(range(20)),
        "stream_name": ["hosr_rolling"] * 20,
        "scenario_name": ["baseline"] * 20,
        "value": [0.0] * 20,
        "phase_id": [1] * 20,
    })
    detections = pd.DataFrame()
    out = fpr_per_detector_stream(detections, streams)
    row = out[out["stream_name"] == "hosr_rolling"].iloc[0]
    assert row["n_false_alarms"] == 0
    assert row["fpr_per_s"] == 0.0


# ---------------------------------------------------------------------------
# bootstrap_latency_ci
# ---------------------------------------------------------------------------

def test_bootstrap_latency_ci_returns_finite_when_input_present():
    rng = np.random.default_rng(0)
    latencies = rng.uniform(1.0, 10.0, size=20)
    res = bootstrap_latency_ci(latencies, n_resamples=500)
    assert isinstance(res, BootstrapLatencyResult)
    assert np.isfinite(res.median)
    assert res.ci_low <= res.median <= res.ci_high
    assert res.n_obs == 20


def test_bootstrap_latency_ci_drops_nans():
    arr = np.array([1.0, np.nan, 2.0, 3.0, np.nan, np.nan])
    res = bootstrap_latency_ci(arr, n_resamples=300)
    assert res.n_obs == 3
    # Median of {1,2,3} = 2 -> bootstrap median should also be 2 most of the time
    assert abs(res.median - 2.0) < 1e-9


def test_bootstrap_latency_ci_all_nan_returns_nan():
    res = bootstrap_latency_ci(np.array([np.nan, np.nan]), n_resamples=100)
    assert np.isnan(res.median)
    assert np.isnan(res.ci_low)
    assert res.n_obs == 0


def test_bootstrap_latency_result_str_renders():
    r = BootstrapLatencyResult(median=12.3, ci_low=10.0, ci_high=15.0,
                                n_obs=10, n_resamples=1000, alpha=0.05)
    assert r.as_str(decimals=1) == "12.3 [10.0, 15.0]"
    nan_r = BootstrapLatencyResult(median=float("nan"), ci_low=float("nan"),
                                    ci_high=float("nan"), n_obs=0,
                                    n_resamples=1000, alpha=0.05)
    assert nan_r.as_str() == "nan"
