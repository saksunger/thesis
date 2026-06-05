"""Unit tests for analysis.demo.orchestrator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.config_perf.data import SCENARIO_COLS, TARGET_COLS
from analysis.demo.context import DeploymentContext
from analysis.demo.orchestrator import (
    DemoConfig,
    DemoOrchestrator,
    DemoResult,
    Intervention,
    SurrogateBundle,
)


# ---------------------------------------------------------------------------
# Helpers — fake surrogate + drift detector + base detector
# ---------------------------------------------------------------------------

class _ConstantSurrogate:
    """Always returns a fixed prediction per call. Implements both
    `predict` and `predict_interval` so it can stand in for either
    a point or an interval surrogate."""

    def __init__(self, value: float, name: str = "const"):
        self.value = float(value)
        self.name = name

    def fit(self, X, y):
        return self

    def predict(self, X) -> np.ndarray:
        n = len(X) if hasattr(X, "__len__") else 1
        return np.full(n, self.value, dtype=float)

    def predict_interval(self, X):
        n = len(X) if hasattr(X, "__len__") else 1
        lo = np.full(n, self.value - 0.05, dtype=float)
        mid = np.full(n, self.value, dtype=float)
        hi = np.full(n, self.value + 0.05, dtype=float)
        return lo, mid, hi


def _make_surrogate_bundle(
    hosr: float = 0.95, rlf: float = 0.03, pp: float = 0.05,
) -> SurrogateBundle:
    return SurrogateBundle(
        point_models={
            "hosr": _ConstantSurrogate(hosr),
            "rlf_rate": _ConstantSurrogate(rlf),
            "ping_pong_rate": _ConstantSurrogate(pp),
        },
        interval_models={
            "hosr": _ConstantSurrogate(hosr),
            "rlf_rate": _ConstantSurrogate(rlf),
            "ping_pong_rate": _ConstantSurrogate(pp),
        },
    )


# ---------------------------------------------------------------------------
# Intervention
# ---------------------------------------------------------------------------

class TestIntervention:
    def _make(self, hosr_cur=0.9, hosr_rec=0.95, rlf_cur=0.05, rlf_rec=0.03,
              pp_cur=0.10, pp_rec=0.06):
        ctx = DeploymentContext(
            rsrp_p10_dbm=-120, rsrp_p50_dbm=-100, rsrp_p90_dbm=-80,
            sinr_p10_db=-5,    sinr_p50_db=2,    sinr_p90_db=20,
            rsrq_p10_db=-17,   rsrq_p50_db=-13,  rsrq_p90_db=-10,
        )
        return Intervention(
            t_trigger_s=300.0,
            drift_streams_fired=("hosr_rolling",),
            context=ctx,
            current_cfg={"ttt_ms": 480.0, "hyst_db": 3.0, "a3_off_db": 3.0},
            recommended_cfg={"ttt_ms": 256.0, "hyst_db": 0.0, "a3_off_db": 0.0},
            pred_current={"hosr": hosr_cur, "rlf_rate": rlf_cur, "ping_pong_rate": pp_cur},
            pred_recommended={"hosr": hosr_rec, "rlf_rate": rlf_rec, "ping_pong_rate": pp_rec},
            pred_recommended_lo={t: 0.0 for t in TARGET_COLS},
            pred_recommended_hi={t: 1.0 for t in TARGET_COLS},
            n_feasible=12, n_candidates=36, cfg_changed=True,
        )

    def test_uplift_raw(self):
        iv = self._make(hosr_cur=0.9, hosr_rec=0.95)
        assert iv.uplift("hosr") == pytest.approx(0.05)

    def test_signed_uplift_hosr(self):
        """HOSR higher = better, so signed_uplift == raw uplift."""
        iv = self._make(hosr_cur=0.9, hosr_rec=0.95)
        assert iv.signed_uplift("hosr") == pytest.approx(0.05)

    def test_signed_uplift_rlf_sign_flipped(self):
        """RLF lower = better, so signed_uplift flips sign."""
        iv = self._make(rlf_cur=0.05, rlf_rec=0.03)
        # Raw uplift = 0.03 - 0.05 = -0.02; signed = +0.02 (improvement)
        assert iv.signed_uplift("rlf_rate") == pytest.approx(0.02)

    def test_signed_uplift_pp_sign_flipped(self):
        iv = self._make(pp_cur=0.10, pp_rec=0.06)
        assert iv.signed_uplift("ping_pong_rate") == pytest.approx(0.04)

    def test_as_record_contains_all_targets(self):
        iv = self._make()
        rec = iv.as_record()
        for t in TARGET_COLS:
            assert f"pred_current_{t}" in rec
            assert f"pred_recommended_{t}" in rec
            assert f"signed_uplift_{t}" in rec
        for k in ("ttt_ms", "hyst_db", "a3_off_db"):
            assert f"current_{k}" in rec
            assert f"recommended_{k}" in rec
        for c in SCENARIO_COLS:
            assert c in rec


# ---------------------------------------------------------------------------
# SurrogateBundle
# ---------------------------------------------------------------------------

class TestSurrogateBundle:
    def test_validate_passes_with_all_targets(self):
        bundle = _make_surrogate_bundle()
        bundle.validate()

    def test_validate_raises_on_missing_target(self):
        bundle = SurrogateBundle(
            point_models={"hosr": _ConstantSurrogate(1.0)},
            interval_models={"hosr": _ConstantSurrogate(1.0)},
        )
        with pytest.raises(ValueError, match="missing point surrogate"):
            bundle.validate()


# ---------------------------------------------------------------------------
# DemoConfig
# ---------------------------------------------------------------------------

class TestDemoConfig:
    def test_empty_feature_cols_raises(self):
        with pytest.raises(ValueError, match="feature_cols"):
            DemoConfig(feature_cols=[])

    def test_invalid_context_window_raises(self):
        with pytest.raises(ValueError, match="context_window_s"):
            DemoConfig(feature_cols=["a"], context_window_s=0)

    def test_invalid_filter_quantile_raises(self):
        with pytest.raises(ValueError, match="filter_quantile"):
            DemoConfig(feature_cols=["a"], filter_quantile=0.0)
        with pytest.raises(ValueError, match="filter_quantile"):
            DemoConfig(feature_cols=["a"], filter_quantile=1.5)

    def test_empty_watch_streams_raises(self):
        with pytest.raises(ValueError, match="watch_streams"):
            DemoConfig(feature_cols=["a"], watch_streams=())


# ---------------------------------------------------------------------------
# _build_feature_row (static helper)
# ---------------------------------------------------------------------------

class TestBuildFeatureRow:
    def test_returns_single_row_dataframe_with_correct_columns(self):
        ctx = DeploymentContext(
            rsrp_p10_dbm=-120, rsrp_p50_dbm=-100, rsrp_p90_dbm=-80,
            sinr_p10_db=-5,    sinr_p50_db=2,    sinr_p90_db=20,
            rsrq_p10_db=-17,   rsrq_p50_db=-13,  rsrq_p90_db=-10,
        )
        cfg = {"ttt_ms": 256.0, "hyst_db": 3.0, "a3_off_db": 6.0}
        row = DemoOrchestrator._build_feature_row(cfg, ctx)
        assert isinstance(row, pd.DataFrame)
        assert row.shape == (1, 12)  # 3 controlled + 9 scenario
        # Column order: controlled first, then scenario
        assert list(row.columns)[:3] == ["ttt_ms", "hyst_db", "a3_off_db"]
        assert list(row.columns)[3:] == list(SCENARIO_COLS)
        assert row.iloc[0]["ttt_ms"] == 256.0
        assert row.iloc[0]["rsrp_p50_dbm"] == -100.0


# ---------------------------------------------------------------------------
# DemoResult.intervention_log_df
# ---------------------------------------------------------------------------

class TestIntervention_Log_Df:
    def test_empty_returns_dataframe_with_expected_columns(self):
        result = DemoResult(
            per_window=pd.DataFrame(),
            retrain_log=pd.DataFrame(),
            interventions=[],
            base_detector_name="PCA-AE",
        )
        df = result.intervention_log_df()
        assert df.empty
        # Must still expose the canonical schema columns so downstream
        # CSV-readers don't break
        assert "t_trigger_s" in df.columns
        for t in TARGET_COLS:
            assert f"pred_current_{t}" in df.columns

    def test_non_empty_round_trip(self):
        ctx = DeploymentContext(
            rsrp_p10_dbm=-120, rsrp_p50_dbm=-100, rsrp_p90_dbm=-80,
            sinr_p10_db=-5,    sinr_p50_db=2,    sinr_p90_db=20,
            rsrq_p10_db=-17,   rsrq_p50_db=-13,  rsrq_p90_db=-10,
        )
        iv = Intervention(
            t_trigger_s=100.0, drift_streams_fired=("s1",),
            context=ctx,
            current_cfg={"ttt_ms": 256.0, "hyst_db": 0.0, "a3_off_db": 0.0},
            recommended_cfg={"ttt_ms": 480.0, "hyst_db": 1.0, "a3_off_db": 3.0},
            pred_current={t: 0.5 for t in TARGET_COLS},
            pred_recommended={t: 0.6 for t in TARGET_COLS},
            pred_recommended_lo={t: 0.4 for t in TARGET_COLS},
            pred_recommended_hi={t: 0.8 for t in TARGET_COLS},
        )
        result = DemoResult(
            per_window=pd.DataFrame(),
            retrain_log=pd.DataFrame(),
            interventions=[iv],
            base_detector_name="PCA-AE",
        )
        df = result.intervention_log_df()
        assert len(df) == 1
        assert df.iloc[0]["t_trigger_s"] == 100.0
        assert df.iloc[0]["current_ttt_ms"] == 256.0
        assert df.iloc[0]["recommended_ttt_ms"] == 480.0


# ---------------------------------------------------------------------------
# DemoOrchestrator._build_interventions (post-pass on retrain log)
# ---------------------------------------------------------------------------

class TestBuildInterventionsFromRetrainLog:
    def _orch(self, samples, events):
        """Build a DemoOrchestrator with minimum machinery to exercise
        the post-pass (we don't run the walk-forward)."""
        return DemoOrchestrator(
            windows=pd.DataFrame(),
            streams_wide=pd.DataFrame(),
            samples=samples,
            events=events,
            base_detector_factory=lambda: None,
            drift_detector_factories={},
            surrogates=_make_surrogate_bundle(hosr=0.95, rlf=0.02, pp=0.04),
            cfg=DemoConfig(feature_cols=["x"]),
        )

    def _samples(self):
        rng = np.random.default_rng(0)
        rows = []
        for ue in range(3):
            for t in range(200):
                rows.append({
                    "time_s": float(t),
                    "ue_id": ue,
                    "rsrp_serving_dbm": float(-110 + rng.normal(0, 2)),
                    "sinr_serving_db":  float(5 + rng.normal(0, 1)),
                    "rsrq_serving_db":  float(-13 + rng.normal(0, 0.5)),
                })
        return pd.DataFrame(rows)

    def _events(self):
        rows = []
        for i, t in enumerate([10.0, 50.0, 110.0, 170.0]):
            rows.append({
                "event_id": i + 1,
                "event_time_s": t,
                "phase_id": i // 2,
                "cfg_ttt_ms": 480.0,
                "cfg_hyst_db": 3.0,
                "cfg_a3_off_db": 3.0,
            })
        return pd.DataFrame(rows)

    def test_skips_non_drift_rows(self):
        orch = self._orch(self._samples(), self._events())
        retrain_log = pd.DataFrame([
            {"t_s": 0.0, "reason": "warmup"},
            {"t_s": 60.0, "reason": "periodic"},
        ])
        ivs = orch._build_interventions(retrain_log)
        assert ivs == []

    def test_drift_row_yields_intervention(self):
        orch = self._orch(self._samples(), self._events())
        retrain_log = pd.DataFrame([
            {"t_s": 120.0, "reason": "drift:hosr_rolling"},
        ])
        ivs = orch._build_interventions(retrain_log)
        assert len(ivs) == 1
        iv = ivs[0]
        assert iv.t_trigger_s == 120.0
        assert iv.drift_streams_fired == ("hosr_rolling",)
        # Current config matches the synthetic events
        assert iv.current_cfg["ttt_ms"] == 480.0

    def test_multi_stream_drift_parsed(self):
        orch = self._orch(self._samples(), self._events())
        retrain_log = pd.DataFrame([
            {"t_s": 150.0, "reason": "drift:hosr_rolling,rlf_rate_rolling"},
        ])
        ivs = orch._build_interventions(retrain_log)
        assert len(ivs) == 1
        assert set(ivs[0].drift_streams_fired) == {
            "hosr_rolling", "rlf_rate_rolling",
        }

    def test_empty_log_returns_empty(self):
        orch = self._orch(self._samples(), self._events())
        ivs = orch._build_interventions(pd.DataFrame())
        assert ivs == []
