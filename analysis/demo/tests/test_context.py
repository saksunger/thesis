"""Unit tests for analysis.demo.context."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.config_perf.data import SCENARIO_COLS
from analysis.demo.context import (
    DeploymentContext,
    build_context,
    current_config,
    per_phase_config_table,
)


def _synthetic_samples(n_ue: int = 4, duration_s: float = 120.0,
                       rng_seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(rng_seed)
    n_ticks = int(duration_s)
    rows = []
    for ue in range(n_ue):
        for t in range(n_ticks):
            rows.append({
                "time_s": float(t),
                "ue_id": ue,
                "rsrp_serving_dbm": float(-110 + rng.normal(0, 3)),
                "sinr_serving_db":  float(5 + rng.normal(0, 2)),
                "rsrq_serving_db":  float(-13 + rng.normal(0, 1)),
            })
    return pd.DataFrame(rows)


def _synthetic_events(n_phases: int = 5, phase_s: float = 60.0) -> pd.DataFrame:
    rows = []
    eid = 0
    for p in range(n_phases):
        ttt = 256.0 + 100 * p
        hyst = float(p % 4)
        a3 = float((p % 3) * 3.0)
        # 2 events per phase
        for i, t in enumerate([10.0, 40.0]):
            eid += 1
            rows.append({
                "event_id": eid,
                "event_time_s": p * phase_s + t,
                "phase_id": p,
                "cfg_ttt_ms": ttt,
                "cfg_hyst_db": hyst,
                "cfg_a3_off_db": a3,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# DeploymentContext
# ---------------------------------------------------------------------------

class TestDeploymentContext:
    def test_to_surrogate_dict_covers_scenario_cols(self):
        ctx = DeploymentContext(
            rsrp_p10_dbm=-120, rsrp_p50_dbm=-100, rsrp_p90_dbm=-80,
            sinr_p10_db=-5,    sinr_p50_db=2,    sinr_p90_db=20,
            rsrq_p10_db=-17,   rsrq_p50_db=-13,  rsrq_p90_db=-10,
        )
        d = ctx.to_surrogate_dict()
        assert set(d) == set(SCENARIO_COLS)

    def test_is_finite_true_for_normal(self):
        ctx = DeploymentContext(
            rsrp_p10_dbm=-120, rsrp_p50_dbm=-100, rsrp_p90_dbm=-80,
            sinr_p10_db=-5,    sinr_p50_db=2,    sinr_p90_db=20,
            rsrq_p10_db=-17,   rsrq_p50_db=-13,  rsrq_p90_db=-10,
        )
        assert ctx.is_finite() is True

    def test_is_finite_false_for_nan(self):
        ctx = DeploymentContext(
            rsrp_p10_dbm=float("nan"), rsrp_p50_dbm=-100, rsrp_p90_dbm=-80,
            sinr_p10_db=-5,            sinr_p50_db=2,    sinr_p90_db=20,
            rsrq_p10_db=-17,           rsrq_p50_db=-13,  rsrq_p90_db=-10,
        )
        assert ctx.is_finite() is False


# ---------------------------------------------------------------------------
# build_context
# ---------------------------------------------------------------------------

class TestBuildContext:
    def test_returns_finite_context_with_data(self):
        samples = _synthetic_samples()
        ctx = build_context(samples, t_now_s=60.0, context_window_s=30.0)
        assert ctx.is_finite()
        assert ctx.n_samples > 0
        # Percentiles must be ordered (lo <= mid <= hi)
        assert ctx.rsrp_p10_dbm <= ctx.rsrp_p50_dbm <= ctx.rsrp_p90_dbm
        assert ctx.sinr_p10_db <= ctx.sinr_p50_db <= ctx.sinr_p90_db
        assert ctx.rsrq_p10_db <= ctx.rsrq_p50_db <= ctx.rsrq_p90_db

    def test_window_extents_set(self):
        samples = _synthetic_samples()
        ctx = build_context(samples, t_now_s=80.0, context_window_s=30.0)
        assert ctx.t_window_end_s == pytest.approx(80.0)
        assert ctx.t_window_start_s == pytest.approx(50.0)

    def test_empty_window_returns_nan(self):
        samples = _synthetic_samples()
        # Query well before any samples
        ctx = build_context(samples, t_now_s=-100.0, context_window_s=30.0)
        assert ctx.n_samples == 0
        assert not ctx.is_finite()

    def test_invalid_window_raises(self):
        samples = _synthetic_samples()
        with pytest.raises(ValueError, match="context_window_s"):
            build_context(samples, t_now_s=10.0, context_window_s=0.0)
        with pytest.raises(ValueError, match="context_window_s"):
            build_context(samples, t_now_s=10.0, context_window_s=-5.0)

    def test_missing_time_col_raises(self):
        samples = pd.DataFrame({
            "rsrp_serving_dbm": [-110.0], "sinr_serving_db": [5.0],
            "rsrq_serving_db": [-13.0],
        })
        with pytest.raises(ValueError, match="time"):
            build_context(samples, t_now_s=10.0)

    def test_missing_radio_col_raises(self):
        samples = pd.DataFrame({
            "time_s": [0.0, 1.0], "rsrp_serving_dbm": [-110, -111],
        })
        with pytest.raises(ValueError, match="radio columns"):
            build_context(samples, t_now_s=1.0)


# ---------------------------------------------------------------------------
# current_config
# ---------------------------------------------------------------------------

class TestCurrentConfig:
    def test_basic_lookup(self):
        events = _synthetic_events(n_phases=3)
        # Query within phase 1 (t=60..120)
        cfg = current_config(events, t_now_s=90.0)
        assert cfg is not None
        # phase 1 has ttt=356 (256 + 100*1)
        assert cfg["ttt_ms"] == 356.0

    def test_returns_none_before_first_event(self):
        events = _synthetic_events()
        cfg = current_config(events, t_now_s=-10.0)
        assert cfg is None

    def test_returns_latest_event(self):
        events = _synthetic_events(n_phases=4)
        cfg = current_config(events, t_now_s=300.0)
        assert cfg is not None
        # n_phases=4 means phases 0..3. Phase 3 events fire at
        # t = 3*60+10 = 190 and 3*60+40 = 220, both <= 300. Latest is 220
        # (phase 3, ttt=256+100*3=556).
        assert cfg["ttt_ms"] == 556.0

    def test_empty_events_returns_none(self):
        events = pd.DataFrame(columns=[
            "event_time_s", "cfg_ttt_ms", "cfg_hyst_db", "cfg_a3_off_db",
        ])
        cfg = current_config(events, t_now_s=10.0)
        assert cfg is None

    def test_missing_cols_raises(self):
        events = pd.DataFrame({
            "event_time_s": [10.0], "cfg_ttt_ms": [256.0],
        })
        with pytest.raises(ValueError, match="missing columns"):
            current_config(events, t_now_s=20.0)


# ---------------------------------------------------------------------------
# per_phase_config_table
# ---------------------------------------------------------------------------

class TestPerPhaseConfigTable:
    def test_one_row_per_phase(self):
        events = _synthetic_events(n_phases=4)
        tbl = per_phase_config_table(events)
        assert len(tbl) == 4
        assert set(tbl["phase_id"]) == {0, 1, 2, 3}

    def test_columns(self):
        events = _synthetic_events()
        tbl = per_phase_config_table(events)
        for c in [
            "phase_id", "ttt_ms", "hyst_db", "a3_off_db",
            "t_min_s", "t_max_s", "n_events",
        ]:
            assert c in tbl.columns

    def test_empty_input(self):
        events = pd.DataFrame(columns=[
            "event_time_s", "phase_id", "cfg_ttt_ms", "cfg_hyst_db", "cfg_a3_off_db",
        ])
        tbl = per_phase_config_table(events)
        assert tbl.empty
        assert "phase_id" in tbl.columns
