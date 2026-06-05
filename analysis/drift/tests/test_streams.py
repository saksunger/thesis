"""Unit tests for analysis.drift.streams."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.drift.streams import (
    STREAM_NAMES,
    StreamConfig,
    build_streams,
    pivot_streams,
)


def _mk_samples(duration_s: float = 30.0, dt_s: float = 0.1, n_ue: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    t = np.arange(0, duration_s + 1e-9, dt_s)
    rows = []
    for ue in range(1, n_ue + 1):
        rows.append(pd.DataFrame({
            "ue_id": ue,
            "time_s": t,
            "phase_id": 1,
            "scenario_name": "baseline",
            "serving_cell_id": 1,
            "rsrp_serving_dbm": -90 + rng.normal(0, 1, len(t)),
            "rsrq_serving_db":  -10 + rng.normal(0, 0.5, len(t)),
            "sinr_serving_db":  10 + rng.normal(0, 2, len(t)),
        }))
    return pd.concat(rows, ignore_index=True)


def _mk_events(times_by_type: dict[str, list[float]], phase_id: int = 1) -> pd.DataFrame:
    rows = []
    for et, ts in times_by_type.items():
        for t in ts:
            rows.append({"ue_id": 1, "event_time_s": float(t),
                         "event_type": et, "phase_id": phase_id})
    if not rows:
        return pd.DataFrame(columns=["ue_id", "event_time_s", "event_type", "phase_id"])
    return pd.DataFrame(rows)


def test_invalid_config_raises():
    with pytest.raises(ValueError):
        StreamConfig(cadence_s=0)
    with pytest.raises(ValueError):
        StreamConfig(cadence_s=1.0, roll_window_s=0.5)


def test_build_streams_returns_six_streams():
    samples = _mk_samples(duration_s=20.0)
    events = _mk_events({})
    out = build_streams(samples, events)
    assert set(out["stream_name"].unique()) == set(STREAM_NAMES)
    # Each stream has the same length
    counts = out.groupby("stream_name")["time_s"].count()
    assert counts.nunique() == 1


def test_per_second_cadence_grid_size():
    samples = _mk_samples(duration_s=20.0)
    out = build_streams(samples, _mk_events({}), StreamConfig(cadence_s=1.0))
    per_stream = int(out.groupby("stream_name")["time_s"].count().iloc[0])
    # grid from floor(0)=0 to ceil(20)=20 inclusive at 1s cadence = 21 points
    assert per_stream == 21


def test_rsrp_mean_matches_naive_per_second():
    samples = _mk_samples(duration_s=10.0, dt_s=0.1, n_ue=2)
    out = build_streams(samples, _mk_events({}), StreamConfig(cadence_s=1.0))
    rsrp = out[out["stream_name"] == "rsrp_serving_mean"].sort_values("time_s")
    # Compare against pandas groupby per-second mean
    samples["bucket"] = np.floor(samples["time_s"]).astype(int)
    expected = samples.groupby("bucket")["rsrp_serving_dbm"].mean()
    # rsrp grid includes 0..10, but t=10 bucket may have only 1 sample (t=10.0)
    for bucket, val in expected.items():
        row = rsrp[rsrp["time_s"] == float(bucket)].iloc[0]
        assert abs(row["value"] - val) < 1e-9, (
            f"rsrp at t={bucket}: got {row['value']}, expected {val}"
        )


def test_ho_rate_rolling_counts_events():
    samples = _mk_samples(duration_s=30.0)
    # 10 HO_ATTEMPTs spread across t=5..15
    events = _mk_events({"HO_ATTEMPT": list(np.linspace(5.0, 15.0, 10))})
    out = build_streams(samples, events,
                        StreamConfig(cadence_s=1.0, roll_window_s=10.0))
    ho = out[out["stream_name"] == "ho_rate_rolling"].sort_values("time_s")
    # At t=20 the window covers (10, 20] — should hit ~5 events at rate 0.5/s
    row = ho[ho["time_s"] == 20.0].iloc[0]
    assert row["value"] > 0.0
    # At t=0 the window covers (-10, 0] — should be 0
    row0 = ho[ho["time_s"] == 0.0].iloc[0]
    assert row0["value"] == 0.0


def test_hosr_nan_when_no_handovers():
    samples = _mk_samples(duration_s=10.0)
    out = build_streams(samples, _mk_events({}),
                        StreamConfig(cadence_s=1.0, roll_window_s=5.0))
    hosr = out[out["stream_name"] == "hosr_rolling"]
    assert hosr["value"].isna().all()


def test_hosr_computed_when_handovers_present():
    samples = _mk_samples(duration_s=20.0)
    events = _mk_events({
        "HO_SUCCESS": [3.0, 4.0, 5.0],
        "HO_FAIL":    [6.0],
    })
    out = build_streams(samples, events,
                        StreamConfig(cadence_s=1.0, roll_window_s=10.0))
    hosr = out[out["stream_name"] == "hosr_rolling"].sort_values("time_s")
    row = hosr[hosr["time_s"] == 10.0].iloc[0]
    # 3 success + 1 fail in (0, 10] -> HOSR = 0.75
    assert abs(row["value"] - 0.75) < 1e-9


def test_pivot_streams_matches_long():
    samples = _mk_samples(duration_s=10.0)
    out_long = build_streams(samples, _mk_events({}))
    wide = pivot_streams(out_long)
    assert list(wide.columns) == list(STREAM_NAMES)
    # Same number of rows as the per-stream count
    assert len(wide) == int(out_long.groupby("stream_name")["time_s"].count().iloc[0])


def test_missing_columns_raises():
    samples = _mk_samples(duration_s=5.0).drop(columns=["sinr_serving_db"])
    with pytest.raises(ValueError, match="missing required columns"):
        build_streams(samples, _mk_events({}))
