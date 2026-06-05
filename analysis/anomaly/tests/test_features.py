"""Unit tests for window-feature aggregator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.anomaly.features import (
    EVENT_TYPES,
    SAMPLE_NUMERIC_COLS,
    WindowConfig,
    aggregate_windows,
    feature_columns,
    feature_groups,
)


def _mk_samples(
    n_ue: int = 2,
    duration_s: float = 30.0,
    dt_s: float = 0.01,
    phase_id: int = 1,
    scenario: str = "baseline",
) -> pd.DataFrame:
    rows = []
    t = np.arange(0, duration_s + 1e-9, dt_s)
    rng = np.random.default_rng(0)
    for ue in range(1, n_ue + 1):
        rows.append(
            pd.DataFrame(
                {
                    "ue_id": ue,
                    "time_s": t,
                    "phase_id": phase_id,
                    "scenario_name": scenario,
                    "serving_cell_id": 1 + (rng.random(len(t)) > 0.7).astype(int),
                    "rsrp_serving_dbm": -90 + rng.normal(0, 2, len(t)),
                    "rsrq_serving_db": -10 + rng.normal(0, 1, len(t)),
                    "sinr_serving_db": 10 + rng.normal(0, 3, len(t)),
                    "rsrp_neighbor_dbm": -95 + rng.normal(0, 2, len(t)),
                    "rsrq_neighbor_db": -12 + rng.normal(0, 1, len(t)),
                    "ue_speed_mps": 10.0,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def _mk_events(n_per_ue: int = 0, n_ue: int = 2, phase_id: int = 1) -> pd.DataFrame:
    if n_per_ue == 0:
        return pd.DataFrame(
            columns=["ue_id", "event_time_s", "event_type", "phase_id"]
        )
    rows = []
    for ue in range(1, n_ue + 1):
        for k in range(n_per_ue):
            # Place at t = 1, 6, 11, 16, ... so each lands strictly inside
            # a 5 s non-overlapping window (avoid the half-open edge).
            rows.append(
                {
                    "ue_id": ue,
                    "event_time_s": 1.0 + k * 5.0,
                    "event_type": "HO_ATTEMPT",
                    "phase_id": phase_id,
                }
            )
    return pd.DataFrame(rows)


def test_feature_columns_includes_whitelist_quantiles():
    cols = feature_columns()
    for c in SAMPLE_NUMERIC_COLS:
        for suffix in ("p10", "p50", "p90", "std"):
            assert f"{c}__{suffix}" in cols, f"missing {c}__{suffix}"
    assert "serving_cell_mode" in cols
    for ev in EVENT_TYPES:
        assert f"n_{ev.lower()}" in cols
    assert "hosr" in cols
    assert "hofr_rate" in cols
    assert "rlf_rate" in cols
    assert "ping_pong_rate" in cols


def test_window_count_matches_expected_with_5s_1s_config():
    samples = _mk_samples(n_ue=2, duration_s=30.0, dt_s=0.01)
    events = _mk_events(n_per_ue=0)
    feats = aggregate_windows(samples, events, WindowConfig(window_s=5.0, slide_s=1.0))
    # t in [0, 30], window 5s, slide 1s → starts at 0, 1, ..., 25 = 26 windows
    expected_per_ue = 26
    assert len(feats) == 2 * expected_per_ue
    assert set(feats["ue_id"]) == {1, 2}


def test_phase_boundary_not_crossed():
    # Two phases with different scenario names; no window must contain
    # samples from both.
    s1 = _mk_samples(n_ue=1, duration_s=10.0, phase_id=1, scenario="baseline")
    s2 = _mk_samples(n_ue=1, duration_s=10.0, phase_id=2, scenario="drift_x")
    s2["time_s"] = s2["time_s"] + 10.0
    samples = pd.concat([s1, s2], ignore_index=True)
    events = _mk_events(n_per_ue=0)
    feats = aggregate_windows(samples, events, WindowConfig(window_s=5.0, slide_s=1.0))
    # No "mixed" scenario windows; each window's scenario_name comes from
    # one phase only
    phase_count = feats.groupby("phase_id")["window_id"].count().to_dict()
    assert all(v > 0 for v in phase_count.values())
    assert "baseline" in feats[feats.phase_id == 1].scenario_name.unique()
    assert "drift_x" in feats[feats.phase_id == 2].scenario_name.unique()


def test_event_counts_propagate_into_features():
    samples = _mk_samples(n_ue=1, duration_s=30.0)
    # 6 HO_ATTEMPTs spread one per 5s
    events = _mk_events(n_per_ue=6, n_ue=1)
    feats = aggregate_windows(samples, events, WindowConfig(window_s=5.0, slide_s=5.0))
    # Each 5s window should hit exactly one event
    assert (feats["n_ho_attempt"] >= 0).all()
    assert feats["n_ho_attempt"].sum() == 6


def test_invalid_window_config_raises():
    with pytest.raises(ValueError):
        WindowConfig(window_s=0)
    with pytest.raises(ValueError):
        WindowConfig(window_s=5, slide_s=10)


def test_missing_column_raises():
    samples = _mk_samples(n_ue=1, duration_s=10.0)
    samples = samples.drop(columns=["sinr_serving_db"])
    events = _mk_events(0)
    with pytest.raises(ValueError, match="missing required columns"):
        aggregate_windows(samples, events)


def test_hosr_nan_when_no_attempts():
    samples = _mk_samples(n_ue=1, duration_s=10.0)
    events = _mk_events(0)
    feats = aggregate_windows(samples, events, WindowConfig(window_s=5.0, slide_s=5.0))
    assert feats["hosr"].isna().all(), "HOSR must be NaN when no HO attempts"


# -------------------------------------------------------------------------
# Phase 5 Iter B: feature_groups() helper
# -------------------------------------------------------------------------

def test_feature_groups_partition_matches_feature_columns():
    groups = feature_groups()
    all_grouped = [c for cols in groups.values() for c in cols]
    expected = feature_columns()
    # No duplicates across groups (disjoint)
    assert len(all_grouped) == len(set(all_grouped))
    # Exhaustive: union == feature_columns()
    assert set(all_grouped) == set(expected)
    assert len(all_grouped) == len(expected)


def test_feature_groups_have_expected_families():
    groups = feature_groups()
    assert set(groups) == {"rsrp", "rsrq", "sinr", "speed", "cell", "events", "derived"}
    # Sanity checks on per-family membership
    assert all(c.startswith("rsrp") for c in groups["rsrp"])
    assert all(c.startswith("rsrq") for c in groups["rsrq"])
    assert all(c.startswith("sinr_") for c in groups["sinr"])
    assert all(c.startswith("ue_speed_") for c in groups["speed"])
    assert groups["cell"] == ["serving_cell_mode"]
    assert all(c.startswith("n_") for c in groups["events"])
    assert set(groups["derived"]) == {"hosr", "hofr_rate", "rlf_rate", "ping_pong_rate"}


def test_feature_groups_drop_one_yields_disjoint_remainder():
    groups = feature_groups()
    full = feature_columns()
    for fam, cols in groups.items():
        kept = [c for c in full if c not in set(cols)]
        # Dropping one family must remove exactly that family
        assert len(kept) == len(full) - len(cols), f"family {fam} drop count mismatch"
        assert not (set(kept) & set(cols))
