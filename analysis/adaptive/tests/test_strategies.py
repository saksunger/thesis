"""Unit tests for analysis.adaptive.strategies."""

from __future__ import annotations

import pytest

from analysis.adaptive.strategies import (
    DriftTriggeredStrategy,
    PeriodicStrategy,
    RetrainStrategy,
    StaticStrategy,
    default_strategies,
)


class TestStaticStrategy:
    def test_never_retrains(self):
        s = StaticStrategy()
        for t in [0.0, 10.0, 100.0, 1e6]:
            refit, reason = s.should_retrain(t, {"hosr_rolling": True,
                                                  "rlf_rate_rolling": True})
            assert refit is False
            assert isinstance(reason, str) and reason


class TestPeriodicStrategy:
    def test_first_call_skips_and_starts_clock(self):
        s = PeriodicStrategy(period_s=60.0)
        refit, reason = s.should_retrain(100.0, {})
        assert refit is False
        assert "init" in reason

    def test_fires_after_period(self):
        s = PeriodicStrategy(period_s=60.0)
        s.should_retrain(0.0, {})            # clock starts at 0
        refit, _ = s.should_retrain(30.0, {})
        assert refit is False
        refit, reason = s.should_retrain(60.5, {})
        assert refit is True
        assert "periodic" in reason

    def test_clock_resets_after_fire(self):
        s = PeriodicStrategy(period_s=60.0)
        s.should_retrain(0.0, {})
        s.should_retrain(60.5, {})  # fires
        refit, _ = s.should_retrain(90.0, {})
        assert refit is False
        refit, _ = s.should_retrain(125.0, {})
        assert refit is True

    def test_reset_clears_clock(self):
        s = PeriodicStrategy(period_s=60.0)
        s.should_retrain(0.0, {})
        s.should_retrain(60.0, {})  # fires
        s.reset()
        refit, reason = s.should_retrain(500.0, {})
        # After reset the FIRST call must skip again (init)
        assert refit is False
        assert "init" in reason

    def test_invalid_period(self):
        with pytest.raises(ValueError):
            PeriodicStrategy(period_s=0.0)
        with pytest.raises(ValueError):
            PeriodicStrategy(period_s=-1.0)

    def test_ignores_drift_signals(self):
        s = PeriodicStrategy(period_s=60.0)
        s.should_retrain(0.0, {"hosr_rolling": True})
        refit, _ = s.should_retrain(30.0, {"hosr_rolling": True})
        assert refit is False  # period not elapsed despite drift


class TestDriftTriggeredStrategy:
    def test_no_signal_no_refit(self):
        s = DriftTriggeredStrategy(cooldown_s=0.0)
        refit, reason = s.should_retrain(100.0, {})
        assert refit is False
        assert reason == "no_signal"
        refit, _ = s.should_retrain(200.0, {"hosr_rolling": False})
        assert refit is False

    def test_signal_fires(self):
        s = DriftTriggeredStrategy(cooldown_s=0.0)
        refit, reason = s.should_retrain(100.0, {"hosr_rolling": True})
        assert refit is True
        assert "hosr_rolling" in reason

    def test_cooldown_suppresses(self):
        s = DriftTriggeredStrategy(cooldown_s=30.0)
        refit, _ = s.should_retrain(100.0, {"hosr_rolling": True})
        assert refit is True
        refit, reason = s.should_retrain(120.0, {"hosr_rolling": True})
        assert refit is False
        assert "cooldown" in reason
        refit, _ = s.should_retrain(135.0, {"hosr_rolling": True})
        assert refit is True

    def test_watch_streams_filter(self):
        s = DriftTriggeredStrategy(cooldown_s=0.0,
                                    watch_streams=["hosr_rolling"])
        refit, _ = s.should_retrain(100.0, {"rlf_rate_rolling": True})
        assert refit is False  # rlf is not watched
        refit, _ = s.should_retrain(200.0,
                                     {"hosr_rolling": True,
                                      "rlf_rate_rolling": True})
        assert refit is True

    def test_multiple_signals_all_in_reason(self):
        s = DriftTriggeredStrategy(cooldown_s=0.0)
        refit, reason = s.should_retrain(
            100.0, {"hosr_rolling": True, "rlf_rate_rolling": True}
        )
        assert refit is True
        assert "hosr_rolling" in reason
        assert "rlf_rate_rolling" in reason

    def test_reset_clears_cooldown(self):
        s = DriftTriggeredStrategy(cooldown_s=30.0)
        s.should_retrain(100.0, {"hosr_rolling": True})  # fires
        s.reset()
        refit, _ = s.should_retrain(105.0, {"hosr_rolling": True})
        assert refit is True

    def test_invalid_cooldown(self):
        with pytest.raises(ValueError):
            DriftTriggeredStrategy(cooldown_s=-1.0)


class TestDefaultStrategies:
    def test_returns_three_in_order(self):
        st = default_strategies()
        assert len(st) == 3
        names = [s.name for s in st]
        assert names == ["static", "periodic", "drift_triggered"]
        assert all(isinstance(s, RetrainStrategy) for s in st)

    def test_period_propagates(self):
        st = default_strategies(period_s=42.0)
        periodic = [s for s in st if isinstance(s, PeriodicStrategy)][0]
        assert periodic.period_s == 42.0

    def test_cooldown_and_watch_propagate(self):
        st = default_strategies(cooldown_s=5.0, watch_streams=["a", "b"])
        dt = [s for s in st if isinstance(s, DriftTriggeredStrategy)][0]
        assert dt.cooldown_s == 5.0
        assert dt.watch_streams == ["a", "b"]
