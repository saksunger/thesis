"""Unit tests for analysis.adaptive.metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.adaptive.metrics import (
    SlidingConfig,
    cost_summary,
    drift_phase_pr_auc,
    overall_pr_auc,
    sliding_pr_auc,
)


# ---------------------------------------------------------------------------
# Sliding PR-AUC
# ---------------------------------------------------------------------------

def _make_per_window(n: int = 100, n_pos: int = 20,
                     seed: int = 0) -> pd.DataFrame:
    """Construct a per-window DataFrame with a controllable signal."""
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    labels = np.zeros(n, dtype=int)
    pos_idx = rng.choice(n, size=n_pos, replace=False)
    labels[pos_idx] = 1
    # Score = high for positives + noise, low for negatives + noise
    scores = rng.normal(loc=0.0, scale=0.3, size=n)
    scores[labels == 1] += 2.0
    return pd.DataFrame({
        "t_mid_s": t,
        "score": scores,
        "label_anomaly": labels,
        "phase_id": np.zeros(n, dtype=int),
    })


class TestSlidingPrAuc:
    def test_basic_shape(self):
        pw = _make_per_window(n=100, n_pos=20)
        cfg = SlidingConfig(eval_window_s=20.0, stride_s=10.0,
                            min_pos=1, min_neg=1)
        out = sliding_pr_auc(pw, cfg)
        assert {"t_center_s", "pr_auc", "n_pos", "n_neg"} <= set(out.columns)
        assert len(out) >= 1
        # Should be sorted by time
        assert (np.diff(out["t_center_s"].to_numpy()) >= 0).all()

    def test_empty_input(self):
        out = sliding_pr_auc(pd.DataFrame(), SlidingConfig())
        assert out.empty
        assert set(out.columns) == {"t_center_s", "pr_auc", "n_pos", "n_neg"}

    def test_all_nan_scores(self):
        pw = _make_per_window(n=50)
        pw["score"] = np.nan
        out = sliding_pr_auc(pw, SlidingConfig())
        assert out.empty

    def test_pr_auc_positive_for_separable_signal(self):
        pw = _make_per_window(n=200, n_pos=40, seed=1)
        cfg = SlidingConfig(eval_window_s=200.0, stride_s=100.0,
                            min_pos=1, min_neg=1)
        out = sliding_pr_auc(pw, cfg)
        valid = out["pr_auc"].dropna()
        assert (valid > 0.3).all(), (
            f"separable signal should yield PR-AUC > 0.3, got {valid.tolist()}"
        )

    def test_degenerate_window_yields_nan(self):
        # All-negative window
        n = 50
        pw = pd.DataFrame({
            "t_mid_s": np.arange(n, dtype=float),
            "score": np.random.default_rng(0).normal(size=n),
            "label_anomaly": np.zeros(n, dtype=int),
            "phase_id": np.zeros(n, dtype=int),
        })
        cfg = SlidingConfig(eval_window_s=50.0, stride_s=10.0,
                            min_pos=1, min_neg=1)
        out = sliding_pr_auc(pw, cfg)
        # Every window has 0 positives -> all pr_auc must be NaN
        assert out["pr_auc"].isna().all()
        # And n_pos must be reported as 0
        assert (out["n_pos"] == 0).all()

    def test_min_pos_threshold(self):
        pw = _make_per_window(n=100, n_pos=5)
        cfg = SlidingConfig(eval_window_s=10.0, stride_s=10.0,
                            min_pos=10, min_neg=0)  # threshold > n_pos
        out = sliding_pr_auc(pw, cfg)
        # min_pos=10 is higher than the total positives in any 10s window
        assert out["pr_auc"].isna().all()

    def test_invalid_config(self):
        with pytest.raises(ValueError):
            SlidingConfig(eval_window_s=0.0)
        with pytest.raises(ValueError):
            SlidingConfig(stride_s=-1.0)


# ---------------------------------------------------------------------------
# Overall + drift-phase PR-AUC
# ---------------------------------------------------------------------------

class TestOverallPrAuc:
    def test_perfectly_separable(self):
        pw = pd.DataFrame({
            "score": [0.0, 0.1, 0.2, 0.9, 0.95, 1.0],
            "label_anomaly": [0, 0, 0, 1, 1, 1],
        })
        assert overall_pr_auc(pw) == pytest.approx(1.0)

    def test_all_negative_returns_nan(self):
        pw = pd.DataFrame({
            "score": [0.0, 0.5, 1.0],
            "label_anomaly": [0, 0, 0],
        })
        assert np.isnan(overall_pr_auc(pw))

    def test_drops_nan_scores(self):
        pw = pd.DataFrame({
            "score": [np.nan, 0.0, 1.0, np.nan],
            "label_anomaly": [1, 0, 1, 0],
        })
        # After dropping NaN scores: scores=[0,1], labels=[0,1] -> PR-AUC=1
        assert overall_pr_auc(pw) == pytest.approx(1.0)


class TestDriftPhasePrAuc:
    def test_filters_by_phase(self):
        pw = pd.DataFrame({
            "score": [0.0, 1.0, 0.0, 1.0],
            "label_anomaly": [0, 1, 0, 1],
            "phase_id": [1, 1, 5, 5],
        })
        # Both phases are perfectly separable -> PR-AUC=1
        assert drift_phase_pr_auc(pw, [5]) == pytest.approx(1.0)
        # Empty phase list -> NaN
        assert np.isnan(drift_phase_pr_auc(pw, []))


# ---------------------------------------------------------------------------
# Cost summary
# ---------------------------------------------------------------------------

class TestCostSummary:
    def test_empty(self):
        out = cost_summary(pd.DataFrame())
        assert out["n_refits"] == 0
        assert out["fit_cpu_s_sum"] == 0.0
        assert out["first_refit_t_s"] is None

    def test_static_like(self):
        log = pd.DataFrame([
            {"t_s": 0.0, "reason": "warmup", "n_train_samples": 100,
             "fit_cpu_s": 0.05},
        ])
        out = cost_summary(log)
        assert out["n_refits"] == 1
        assert out["n_train_samples_sum"] == 100
        assert out["fit_cpu_s_sum"] == pytest.approx(0.05)
        assert out["first_refit_t_s"] is None  # only warmup

    def test_with_post_warmup_refits(self):
        log = pd.DataFrame([
            {"t_s": 0.0, "reason": "warmup", "n_train_samples": 100,
             "fit_cpu_s": 0.05},
            {"t_s": 60.0, "reason": "periodic:60s", "n_train_samples": 50,
             "fit_cpu_s": 0.02},
            {"t_s": 120.0, "reason": "periodic:60s", "n_train_samples": 50,
             "fit_cpu_s": 0.02},
        ])
        out = cost_summary(log)
        assert out["n_refits"] == 3
        assert out["n_train_samples_sum"] == 200
        assert out["fit_cpu_s_sum"] == pytest.approx(0.09)
        assert out["fit_cpu_s_mean"] == pytest.approx(0.03)
        assert out["first_refit_t_s"] == pytest.approx(60.0)
