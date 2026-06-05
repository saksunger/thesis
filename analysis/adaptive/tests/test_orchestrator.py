"""Unit tests for analysis.adaptive.orchestrator.

Uses tiny synthetic windows + streams DataFrames + fake detectors so the
tests exercise the orchestrator's bookkeeping without spinning up
sklearn or river (which are tested elsewhere).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.adaptive.orchestrator import (
    AdaptiveOrchestrator,
    OrchestratorConfig,
    RunResult,
)
from analysis.adaptive.strategies import (
    DriftTriggeredStrategy,
    PeriodicStrategy,
    StaticStrategy,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeAnomalyDetector:
    """Returns the first feature column as the score. Logs every fit."""

    name = "fake-anom"

    def __init__(self, fit_log: list[dict] | None = None,
                 score_const: float | None = None,
                 raise_on_score: bool = False) -> None:
        self.fit_log = fit_log if fit_log is not None else []
        self.score_const = score_const
        self.raise_on_score = raise_on_score
        self._fitted = False
        self.last_train_size = 0

    def fit(self, X: pd.DataFrame):
        self._fitted = True
        self.last_train_size = len(X)
        self.fit_log.append({"size": len(X)})
        return self

    def score_samples(self, X: pd.DataFrame) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("not fitted")
        if self.raise_on_score:
            raise RuntimeError("score failure")
        if self.score_const is not None:
            return np.full(len(X), float(self.score_const), dtype=float)
        # Default: return first column as score
        col = X.iloc[:, 0].to_numpy(dtype=float)
        return col


class FakeDriftDetector:
    """Fires once when it sees a value >= ``fire_at`` for the first time."""

    name = "fake-drift"

    def __init__(self, fire_at: float = 1e9) -> None:
        self.fire_at = float(fire_at)
        self.values_seen: list[float] = []
        self._fired = False

    def update(self, value: float) -> bool:
        self.values_seen.append(float(value))
        if not self._fired and value >= self.fire_at:
            self._fired = True
            return True
        return False


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------

def _make_windows(n: int = 50, n_features: int = 3,
                  warmup_phase_max: int = 3) -> pd.DataFrame:
    """A tiny windows table with controllable phase boundaries."""
    rng = np.random.default_rng(0)
    t_end = np.arange(1, n + 1, dtype=float)
    t_mid = t_end - 0.5
    # Split: phase 1..warmup_phase_max are baseline (label=0),
    # subsequent phases alternate, with 20 % positive labels.
    phase_id = np.zeros(n, dtype=int)
    boundary = n // 2
    phase_id[:boundary] = (
        rng.integers(low=1, high=warmup_phase_max + 1, size=boundary)
    )
    phase_id[boundary:] = (
        rng.integers(low=warmup_phase_max + 1, high=warmup_phase_max + 3, size=n - boundary)
    )
    labels = np.zeros(n, dtype=int)
    # Sprinkle positives only in non-baseline phases
    non_baseline = np.flatnonzero(phase_id > warmup_phase_max)
    if len(non_baseline) > 0:
        n_pos = max(1, len(non_baseline) // 5)
        pos_idx = rng.choice(non_baseline, size=n_pos, replace=False)
        labels[pos_idx] = 1

    df = pd.DataFrame({
        "t_end_s": t_end,
        "t_mid_s": t_mid,
        "phase_id": phase_id,
        "label_anomaly": labels,
    })
    for k in range(n_features):
        df[f"f{k}"] = rng.normal(size=n)
    return df


def _make_streams_wide(t_end_max: float = 50.0,
                       streams: tuple[str, ...] = ("hosr_rolling",)) -> pd.DataFrame:
    """Streams indexed by time_s at 1Hz."""
    t = np.arange(0, t_end_max + 1, 1.0)
    data = {sn: np.linspace(0.0, 1.0, len(t)) for sn in streams}
    return pd.DataFrame(data, index=pd.Index(t, name="time_s"))


def _cfg(feat_cols: list[str], **overrides) -> OrchestratorConfig:
    base = dict(
        feature_cols=feat_cols,
        warmup_max_phase_id=3,
        retrain_window_s=10.0,
    )
    base.update(overrides)
    return OrchestratorConfig(**base)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_missing_required_column_raises(self):
        w = _make_windows(n=20)
        w = w.drop(columns=["t_end_s"])
        with pytest.raises(ValueError, match="missing required columns"):
            AdaptiveOrchestrator(
                windows=w,
                streams_wide=_make_streams_wide(),
                base_detector_factory=lambda: FakeAnomalyDetector(),
                drift_detector_factories={},
                cfg=_cfg(["f0", "f1", "f2"]),
            )

    def test_empty_feature_cols_raises(self):
        with pytest.raises(ValueError, match="feature_cols"):
            OrchestratorConfig(feature_cols=[])

    def test_zero_retrain_window_raises(self):
        with pytest.raises(ValueError, match="retrain_window_s"):
            OrchestratorConfig(feature_cols=["f0"], retrain_window_s=0.0)

    def test_invalid_filter_quantile_raises(self):
        with pytest.raises(ValueError, match="filter_quantile"):
            OrchestratorConfig(
                feature_cols=["f0"],
                retrain_score_filter_quantile=0.0,
            )
        with pytest.raises(ValueError, match="filter_quantile"):
            OrchestratorConfig(
                feature_cols=["f0"],
                retrain_score_filter_quantile=1.5,
            )

    def test_filter_quantile_none_is_default(self):
        c = OrchestratorConfig(feature_cols=["f0"])
        assert c.retrain_score_filter_quantile is None


# ---------------------------------------------------------------------------
# Warm-up
# ---------------------------------------------------------------------------

class TestWarmup:
    def test_warmup_log_recorded(self):
        windows = _make_windows(n=30)
        cfg = _cfg(["f0", "f1", "f2"])
        orch = AdaptiveOrchestrator(
            windows=windows,
            streams_wide=_make_streams_wide(),
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={},
            cfg=cfg,
        )
        result = orch.run(StaticStrategy())
        log = result.retrain_log
        assert len(log) == 1
        assert log.iloc[0]["reason"] == "warmup"
        assert log.iloc[0]["n_train_samples"] > 0

    def test_no_warmup_windows_yields_empty_per_window(self):
        # Make every window phase_id > warmup_max_phase_id so warmup pool empty
        windows = _make_windows(n=20)
        windows["phase_id"] = 99  # all post-warmup
        cfg = _cfg(["f0", "f1", "f2"], warmup_max_phase_id=3)
        orch = AdaptiveOrchestrator(
            windows=windows,
            streams_wide=_make_streams_wide(),
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={},
            cfg=cfg,
        )
        result = orch.run(StaticStrategy())
        assert result.per_window.empty
        assert result.retrain_log.iloc[0]["reason"] == "warmup_failed"


# ---------------------------------------------------------------------------
# Per-strategy semantics
# ---------------------------------------------------------------------------

class TestStaticStrategy:
    def test_no_retrain_after_warmup(self):
        windows = _make_windows(n=50)
        orch = AdaptiveOrchestrator(
            windows=windows,
            streams_wide=_make_streams_wide(t_end_max=50.0),
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={},
            cfg=_cfg(["f0", "f1", "f2"]),
        )
        result = orch.run(StaticStrategy())
        # Exactly 1 fit (warmup only)
        assert len(result.retrain_log) == 1

    def test_all_post_warmup_windows_scored(self):
        windows = _make_windows(n=50)
        cfg = _cfg(["f0", "f1", "f2"])
        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=_make_streams_wide(t_end_max=50.0),
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={}, cfg=cfg,
        )
        result = orch.run(StaticStrategy())
        n_post = int((windows["phase_id"] > cfg.warmup_max_phase_id).sum())
        assert len(result.per_window) == n_post
        assert result.per_window["score"].notna().all()


class TestPeriodicStrategy:
    def test_fires_at_least_once(self):
        windows = _make_windows(n=80)
        cfg = _cfg(["f0", "f1", "f2"])
        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=_make_streams_wide(t_end_max=80.0),
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={}, cfg=cfg,
        )
        result = orch.run(PeriodicStrategy(period_s=10.0))
        post_warmup = result.retrain_log[result.retrain_log["reason"] != "warmup"]
        assert len(post_warmup) >= 1
        assert all(r.startswith("periodic") for r in post_warmup["reason"])


class TestDriftTriggeredStrategy:
    def test_no_drift_no_refit(self):
        windows = _make_windows(n=40)
        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=_make_streams_wide(t_end_max=40.0),
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={
                "hosr_rolling": lambda: FakeDriftDetector(fire_at=1e9),
            },
            cfg=_cfg(["f0", "f1", "f2"]),
        )
        result = orch.run(DriftTriggeredStrategy(cooldown_s=0.0))
        # Only warmup fit
        assert len(result.retrain_log) == 1

    def test_drift_fires_then_refits(self):
        windows = _make_windows(n=50)
        # Streams ramp 0..1; FakeDriftDetector fires at value >= 0.7
        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=_make_streams_wide(t_end_max=50.0),
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={
                "hosr_rolling": lambda: FakeDriftDetector(fire_at=0.7),
            },
            cfg=_cfg(["f0", "f1", "f2"]),
        )
        result = orch.run(DriftTriggeredStrategy(cooldown_s=0.0))
        post = result.retrain_log[result.retrain_log["reason"] != "warmup"]
        assert len(post) >= 1
        assert all(r.startswith("drift:") for r in post["reason"])

    def test_causality_no_future_stream_feed(self):
        """Drift detector must never see values past current window's t_end_s."""
        windows = _make_windows(n=10)
        # Streams cover 0..20 even though windows only go up to 10
        streams = _make_streams_wide(t_end_max=20.0)
        det_log: list[FakeDriftDetector] = []

        def factory():
            d = FakeDriftDetector(fire_at=1e9)  # never fires
            det_log.append(d)
            return d

        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=streams,
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={"hosr_rolling": factory},
            cfg=_cfg(["f0", "f1", "f2"]),
        )
        orch.run(StaticStrategy())
        assert len(det_log) == 1
        max_t_seen_idx = len(det_log[0].values_seen) - 1
        # Stream is at 1Hz starting at t=0; last fed sample index <= max t_end
        # Max t_end across windows is n_windows = 10 -> at most t=10 fed
        # (warmup_max_phase_id default 3 might already consume some).
        # The strict invariant: never fed past 20 (the future).
        # Tighter: never fed past 10 + 1 (warmup end might be < 10).
        # We just check that we never fed all 21 samples.
        assert max_t_seen_idx + 1 <= 11, (
            f"saw {max_t_seen_idx + 1} samples but only 11 should be fed "
            f"before t=10"
        )


# ---------------------------------------------------------------------------
# Retrain pool semantics
# ---------------------------------------------------------------------------

class TestRetrainPool:
    def test_retrain_uses_last_K_seconds(self):
        windows = _make_windows(n=50)
        cfg = _cfg(["f0", "f1", "f2"], retrain_window_s=5.0)
        # Track fits
        fit_log: list[dict] = []

        def factory():
            return FakeAnomalyDetector(fit_log=fit_log)

        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=_make_streams_wide(t_end_max=50.0),
            base_detector_factory=factory,
            drift_detector_factories={
                "hosr_rolling": lambda: FakeDriftDetector(fire_at=0.5),
            },
            cfg=cfg,
        )
        orch.run(DriftTriggeredStrategy(cooldown_s=0.0))
        # First fit is warmup (large); subsequent fits should use small pool
        assert len(fit_log) >= 2
        for entry in fit_log[1:]:
            # retrain_window_s = 5 -> at most 5 windows in pool
            assert entry["size"] <= 5

    def test_filter_keeps_only_low_score_rows(self):
        """Filter at q=0.5 should drop the high-score half of the retrain pool."""
        windows = _make_windows(n=50)
        cfg = _cfg(
            ["f0", "f1", "f2"],
            retrain_window_s=10.0,
            retrain_score_filter_quantile=0.5,
        )
        fit_log: list[dict] = []

        # First detector: returns row index as score (deterministic). The
        # filter at q=0.5 should drop half the pool on every refit.
        idx_counter = {"n": 0}

        class IndexedScoreDetector(FakeAnomalyDetector):
            def score_samples(self, X):
                # Return 0, 1, 2, ... so the bottom-50 % filter is exact
                if not self._fitted:
                    raise RuntimeError("not fitted")
                return np.arange(len(X), dtype=float)

        def factory():
            d = IndexedScoreDetector(fit_log=fit_log)
            idx_counter["n"] += 1
            return d

        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=_make_streams_wide(t_end_max=50.0),
            base_detector_factory=factory,
            drift_detector_factories={
                "hosr_rolling": lambda: FakeDriftDetector(fire_at=0.5),
            },
            cfg=cfg,
        )
        result = orch.run(DriftTriggeredStrategy(cooldown_s=0.0))
        # Look at retrain log: post-warmup fits must have
        # n_train_samples <= n_train_samples_pre_filter // 2 + 1 (rounding)
        log = result.retrain_log
        post = log[log["reason"] != "warmup"]
        assert len(post) >= 1
        for _, row in post.iterrows():
            pre = int(row["n_train_samples_pre_filter"])
            post_n = int(row["n_train_samples"])
            assert post_n <= (pre // 2) + 1, (
                f"filter at q=0.5 should drop ~half the pool: "
                f"pre={pre}, post={post_n}"
            )

    def test_filter_disabled_keeps_full_pool(self):
        """Without filter, post == pre in the log."""
        windows = _make_windows(n=50)
        cfg = _cfg(["f0", "f1", "f2"], retrain_window_s=10.0)
        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=_make_streams_wide(t_end_max=50.0),
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={
                "hosr_rolling": lambda: FakeDriftDetector(fire_at=0.5),
            },
            cfg=cfg,
        )
        result = orch.run(DriftTriggeredStrategy(cooldown_s=0.0))
        log = result.retrain_log
        post = log[log["reason"] != "warmup"]
        for _, row in post.iterrows():
            assert int(row["n_train_samples"]) == int(row["n_train_samples_pre_filter"])

    def test_score_failure_yields_nan(self):
        windows = _make_windows(n=30)
        # Detector raises on score AFTER first fit
        warm = FakeAnomalyDetector(score_const=1.0)
        det_seq = [warm, FakeAnomalyDetector(raise_on_score=True)]

        def factory():
            if det_seq:
                return det_seq.pop(0)
            return FakeAnomalyDetector(raise_on_score=True)

        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=_make_streams_wide(t_end_max=30.0),
            base_detector_factory=factory,
            drift_detector_factories={
                "hosr_rolling": lambda: FakeDriftDetector(fire_at=0.5),
            },
            cfg=_cfg(["f0", "f1", "f2"]),
        )
        result = orch.run(DriftTriggeredStrategy(cooldown_s=0.0))
        # After the drift-triggered refit, the new detector raises on score
        # -> some scores become NaN
        assert result.per_window["score"].isna().any()


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

class TestRunResult:
    def test_contains_strategy_and_detector_name(self):
        windows = _make_windows(n=30)
        cfg = _cfg(["f0", "f1", "f2"])
        orch = AdaptiveOrchestrator(
            windows=windows, streams_wide=_make_streams_wide(t_end_max=30.0),
            base_detector_factory=lambda: FakeAnomalyDetector(),
            drift_detector_factories={}, cfg=cfg,
        )
        result = orch.run(StaticStrategy())
        assert isinstance(result, RunResult)
        assert result.strategy_name == "static"
        assert result.base_detector_name == "fake-anom"
        assert "strategy" in result.per_window.columns
        assert (result.per_window["strategy"] == "static").all()
